# mail_system.py
"""
MailSystem — NPC "Caixa de Correio" (25/09/2026, pedido do usuário, ver
arquitetura/ARQUITETURA ONLINE.md). Clique direito abre o inbox; de lá o
player abre uma correspondência recebida (texto + anexos de item/gold,
clique direito reclama) ou compõe uma nova (até 6 slots de item + gold,
"Enviar Correspondência" manda tudo de uma vez).

Mesmo padrão de `ui/shop_system.py` (clique direito no NPC + walk_to_npc/
suppress_chase se não-adjacente) e de `client/trade_handlers.py` (campo de
texto via TEXTINPUT, slot de item clicável) — nenhum mecanismo novo, só
composição do que já existe. Estado em `ui/ui_components.py::MailUIState`
(componente ECS no player), nunca dict solto.

Staging da composição (6 slots + gold) é 100% LOCAL — diferente do trade
(que manda 1 mensagem por clique porque é negociação em tempo real com
outro player online), aqui não existe negociação: nada sai do Inventory
real até "Enviar" (1 mensagem só, MAIL_SEND). Entrega de anexo reclamado
chega por INVENTORY_UPDATE/STATS_UPDATE (canais já existentes) — este
System só lê MailUIState pra desenhar, nunca materializa item/gold ele
mesmo.
"""
from __future__ import annotations
import pygame

from ui.fonts import make as _font
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI

from engine.components import (Position, Renderable, TileMovement, PlayerAutoMove,
                               Inventory, Mailbox, NPC, NPCInteractionRequest)
from engine.world import World
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev, get_camera_offset
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines, fill_surf, wrap_text, draw_stack_count
from ui.npc_interaction import walk_to_npc, suppress_chase
from content.item_table import RARITY_COLORS as _SHARED_RARITY_COLORS
from engine.quest_events import fire as quest_fire

from engine.world_systems import System


class MailSystem(UIScaleMixin, System):
    """Sistema do NPC "Caixa de Correio" — 3 telas (inbox/compose/detail),
    ver MailUIState.screen."""

    _FONT_BASES = {"_font_sm": 20, "_font_md": 26, "_font_lg": 32}
    _RARITY_COLORS = _SHARED_RARITY_COLORS

    # Tolerância de clique EXTRA além do tamanho visual da caixa
    # (25/09/2026, 2ª rodada do bug real relatado pelo usuário — a 1ª
    # rodada só igualou o tamanho de PLAYER_SIZE=24 pra TILE_SIZE=32,
    # insuficiente: diagnóstico ao vivo mediu cliques reais errando por
    # até 227px). Só afeta a DETECÇÃO de clique, nunca o visual
    # (Renderable continua TILE_SIZE) — teto de 16px por lado é o
    # MÁXIMO seguro sem alcançar o NPC vizinho mais próximo (mercador/
    # ferreiro/missiveiro ficam a 2 tiles = 64px de distância; 16px de
    # folga de cada lado = 32px de raio total, exatamente a metade
    # dessa distância, sem sobrepor).
    _CLICK_PAD_PX = 16

    def __init__(self, world: World, player_entity: int, screen):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        self._pending_mailbox_id: int = -1
        self._right_click_consumed: bool = False
        self._net = None
        self.pending_tooltip = None
        self._open_cooldown: float = 0.0
        self._inbox_scroll: int = 0
        self._bag_scroll:   int = 0

    # ------------------------------------------------------------------
    # Estado (via MailUIState — nunca dict solto, ver engine/components.py
    # e a disciplina de "proibido estado mutável ad-hoc fora do ECS")
    # ------------------------------------------------------------------

    def _ui(self):
        from ui.ui_components import MailUIState
        return self.world.get_component(self.player_entity, MailUIState)

    @property
    def is_open(self) -> bool:
        ui = self._ui()
        return ui is not None and ui.is_open

    @property
    def mailbox_eid(self) -> int:
        """Mesma convenção de `ShopSystem.open_merchant_id` — usado por
        `game.py::_npc_proximity_panels` pra fechar o painel quando o
        player se afasta demais da caixa de correio."""
        ui = self._ui()
        return ui.mailbox_eid if ui is not None else -1

    @property
    def sub_screen_open(self) -> bool:
        ui = self._ui()
        return ui is not None and ui.sub_screen_open

    def open_for(self, mailbox_eid: int) -> None:
        ui = self._ui()
        if ui is None or not self._net:
            return
        ui.mailbox_eid = mailbox_eid
        ui.screen = "inbox"
        self._pending_mailbox_id = -1
        self._open_cooldown = 0.3
        self._inbox_scroll = 0
        from shared.messages import MsgType
        self._net.send(MsgType.MAIL_OPEN_INBOX, {})
        _npc = self.world.get_component(mailbox_eid, NPC)
        quest_fire("talk_to_npc", npc_name=_npc.name if _npc else "Correio")

    def _close(self) -> None:
        ui = self._ui()
        if ui is not None:
            ui.screen  = "closed"
            ui.detail  = None
        self._pending_mailbox_id = -1

    def _back_to_inbox(self) -> None:
        ui = self._ui()
        if ui is not None and ui.sub_screen_open:
            ui.screen = "inbox"
            ui.detail = None

    # ------------------------------------------------------------------
    # Update — detecção de clique no mundo (mesmo padrão de ShopSystem)
    # ------------------------------------------------------------------

    def update(self, events=None, dt: float = 0) -> None:
        _npc_req = self.world.get_component(self.player_entity, NPCInteractionRequest)
        if _npc_req is not None and _npc_req.kind == "open_mail":
            self.open_for(_npc_req.target_eid)
            self.world.remove_component(self.player_entity, NPCInteractionRequest)

        self._right_click_consumed = False
        if self._open_cooldown > 0:
            self._open_cooldown = max(0.0, self._open_cooldown - dt)

        if self._pending_mailbox_id != -1:
            mt = self._mailbox_tile(self._pending_mailbox_id)
            pt = self._player_tile()
            if mt is None:
                self._pending_mailbox_id = -1
            elif pt and self._cheby(pt, mt) <= 1:
                auto = self.world.get_component(self.player_entity, PlayerAutoMove)
                if auto:
                    auto.active        = False
                    auto.path          = []
                    auto.ground_target = None
                self.open_for(self._pending_mailbox_id)

        if not events:
            return

        cam_x, cam_y = self._get_cam()
        for event in events:
            if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 3
                    and not self.is_open):
                mx, my = event.pos
                _sc = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                wx, wy = mx * _sc + cam_x, my * _sc + cam_y
                for eid, pos, rend, _ in self.world.get_entities_with(Position, Renderable, Mailbox):
                    hw = rend.width  / 2 + self._CLICK_PAD_PX
                    hh = rend.height / 2 + self._CLICK_PAD_PX
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        mt = self._mailbox_tile(eid)
                        if pt and mt and self._cheby(pt, mt) <= 1:
                            suppress_chase(self.world, self.player_entity)
                            self.open_for(eid)
                        else:
                            self._pending_mailbox_id = eid
                            walk_to_npc(self.world, self.player_entity, pt, mt)
                        self._right_click_consumed = True
                        break

    # ------------------------------------------------------------------
    # Helpers de posição/tile (idênticos a ShopSystem)
    # ------------------------------------------------------------------

    def _get_cam(self):
        SW = self.world_surf.get_width()
        SH = self.world_surf.get_height()
        return get_camera_offset(self.world, SW, SH)

    def _player_tile(self):
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _mailbox_tile(self, eid: int):
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TILE_SIZE), int(pos.y / TILE_SIZE)) if pos else None

    @staticmethod
    def _cheby(t1, t2) -> int:
        return chebyshev(t1[0], t1[1], t2[0], t2[1])

    # ------------------------------------------------------------------
    # Eventos de UI — despacha pra tela ativa
    # ------------------------------------------------------------------

    def handle_events(self, events: list) -> None:
        ui = self._ui()
        if ui is None or not ui.is_open:
            return
        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if ui.sub_screen_open:
                    self._back_to_inbox()
                else:
                    self._close()
                return
            if event.type == pygame.TEXTINPUT:
                self._handle_text_input(ui, event)
                continue
            if event.type == pygame.KEYDOWN:
                self._handle_key(ui, event)
                continue
            if event.type == pygame.MOUSEWHEEL and ui.screen == "inbox":
                self._inbox_scroll = max(0, self._inbox_scroll - event.y)
                continue
            if event.type == pygame.MOUSEWHEEL and ui.screen == "compose":
                self._bag_scroll = max(0, self._bag_scroll - event.y)
                continue
            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            if ui.screen == "inbox":
                self._handle_inbox_click(ui, event)
            elif ui.screen == "compose":
                self._handle_compose_click(ui, event)
            elif ui.screen == "detail":
                self._handle_detail_click(ui, event)

    # ── Digitação (Para/Assunto/Texto via TEXTINPUT — ver client/
    # chat_handlers.py pro racional de por que NÃO usar event.unicode do
    # KEYDOWN pra texto livre: quebra tecla morta ABNT2/US-Intl, "não"
    # virava "n~ão". Gold continua digit-only via KEYDOWN, mesmo padrão
    # de client/trade_handlers.py::_handle_trade_gold_key.) ────────────

    def _handle_text_input(self, ui, event) -> None:
        if ui.screen != "compose" or not ui.compose_focus:
            return
        if ui.compose_focus == "to" and len(ui.compose_to) < 24:
            ui.compose_to += event.text
        elif ui.compose_focus == "subject" and len(ui.compose_subject) < 40:
            ui.compose_subject += event.text
        elif ui.compose_focus == "body" and len(ui.compose_body) < 500:
            ui.compose_body += event.text

    def _handle_key(self, ui, event) -> None:
        if ui.screen != "compose" or not ui.compose_focus:
            return
        if event.key == pygame.K_BACKSPACE:
            if ui.compose_focus == "to":
                ui.compose_to = ui.compose_to[:-1]
            elif ui.compose_focus == "subject":
                ui.compose_subject = ui.compose_subject[:-1]
            elif ui.compose_focus == "body":
                ui.compose_body = ui.compose_body[:-1]
            elif ui.compose_focus == "gold":
                ui.compose_gold_text = ui.compose_gold_text[:-1]
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if ui.compose_focus == "body" and len(ui.compose_body) < 500:
                ui.compose_body += "\n"
            else:
                ui.compose_focus = ""
                pygame.key.stop_text_input()
        elif ui.compose_focus == "gold" and event.unicode.isdigit() and len(ui.compose_gold_text) < 9:
            ui.compose_gold_text += event.unicode

    def _subject_placeholder(self, ui) -> str:
        """Auto-preenchimento (pedido do usuário): nome do 1º item staged,
        ou o valor de gold se só gold, ou vazio. Recalculado sempre — nunca
        gravado em compose_subject (esse campo é só o que o usuário
        DIGITOU de verdade)."""
        if ui.compose_items:
            inv = self.world.get_component(self.player_entity, Inventory)
            idx, _qty = ui.compose_items[0]
            if inv and 0 <= idx < len(inv.items):
                return inv.items[idx].name
        try:
            gold = max(0, int(ui.compose_gold_text or "0"))
        except ValueError:
            gold = 0
        return str(gold) if gold > 0 else ""

    # ------------------------------------------------------------------
    # Layouts (compartilhados clique/desenho, mesmo princípio de
    # ShopSystem/_trade_layout — nunca duas contas de geometria separadas)
    # ------------------------------------------------------------------

    def _inbox_layout(self) -> dict:
        self._set_panel_scale(UI.MAIL_INBOX_W, UI.MAIL_INBOX_H)
        x0, y0 = self._safe_panel_origin(UI.MAIL_INBOX_W, UI.MAIL_INBOX_H)
        W, H   = self._u(UI.MAIL_INBOX_W), self._u(UI.MAIL_INBOX_H)
        pad    = self._u(UI.MAIL_PAD)
        row_h  = self._u(UI.MAIL_ROW_H)
        return {
            "panel": pygame.Rect(x0, y0, W, H),
            "close": pygame.Rect(x0 + W - self._u(40), y0 + self._u(6), self._u(34), self._u(34)),
            "body_y": y0 + self._u(50),
            "row_h": row_h, "pad": pad, "x0": x0, "y0": y0, "W": W, "H": H,
            "send_btn": pygame.Rect(x0 + pad, y0 + H - self._u(50), W - pad * 2, self._u(38)),
        }

    def _compose_layout(self) -> dict:
        self._set_panel_scale(UI.MAIL_COMPOSE_W, UI.MAIL_COMPOSE_H)
        x0, y0 = self._safe_panel_origin(UI.MAIL_COMPOSE_W, UI.MAIL_COMPOSE_H)
        W, H   = self._u(UI.MAIL_COMPOSE_W), self._u(UI.MAIL_COMPOSE_H)
        pad    = self._u(UI.MAIL_PAD)
        bag_w  = self._u(190)
        form_x = x0 + bag_w + pad * 2
        form_w = W - bag_w - pad * 3
        row_h  = self._u(30)
        body_r = pygame.Rect(form_x, y0 + self._u(124), form_w, self._u(UI.MAIL_BODY_H))
        slots_y = body_r.bottom + self._u(14)
        slot_sz = self._u(UI.MAIL_SLOT_SZ)
        gap     = self._u(UI.MAIL_SLOT_GAP)
        slots   = [pygame.Rect(form_x + i * (slot_sz + gap), slots_y, slot_sz, slot_sz) for i in range(6)]
        gold_r  = pygame.Rect(form_x, slots_y + slot_sz + self._u(14), self._u(160), self._u(UI.MAIL_FIELD_H))
        return {
            "panel": pygame.Rect(x0, y0, W, H),
            "close": pygame.Rect(x0 + W - self._u(40), y0 + self._u(6), self._u(34), self._u(34)),
            "bag":   pygame.Rect(x0 + pad, y0 + self._u(64), bag_w - pad, H - self._u(64) - pad - self._u(46)),
            "bag_row_h": row_h,
            "to":      pygame.Rect(form_x, y0 + self._u(44), form_w, self._u(UI.MAIL_FIELD_H)),
            "subject": pygame.Rect(form_x, y0 + self._u(84), form_w, self._u(UI.MAIL_FIELD_H)),
            "body":    body_r,
            "slots":   slots,
            "gold":    gold_r,
            "send":    pygame.Rect(form_x, y0 + H - self._u(48), form_w, self._u(38)),
            "x0": x0, "y0": y0, "W": W, "H": H, "pad": pad, "form_x": form_x, "form_w": form_w,
        }

    def _detail_layout(self) -> dict:
        self._set_panel_scale(UI.MAIL_DETAIL_W, UI.MAIL_DETAIL_H)
        x0, y0 = self._safe_panel_origin(UI.MAIL_DETAIL_W, UI.MAIL_DETAIL_H)
        W, H   = self._u(UI.MAIL_DETAIL_W), self._u(UI.MAIL_DETAIL_H)
        pad    = self._u(UI.MAIL_PAD)
        body_r = pygame.Rect(x0 + pad, y0 + self._u(90), W - pad * 2, self._u(220))
        slots_y = body_r.bottom + self._u(14)
        slot_sz = self._u(UI.MAIL_SLOT_SZ)
        gap     = self._u(UI.MAIL_SLOT_GAP)
        slots   = [pygame.Rect(x0 + pad + i * (slot_sz + gap), slots_y, slot_sz, slot_sz) for i in range(6)]
        return {
            "panel":  pygame.Rect(x0, y0, W, H),
            "body":   body_r,
            "slots":  slots,
            "gold":   pygame.Rect(x0 + pad, slots_y + slot_sz + self._u(10), self._u(160), self._u(32)),
            "delete": pygame.Rect(x0 + pad, y0 + H - self._u(46), self._u(150), self._u(34)),
            "close2": pygame.Rect(x0 + W - pad - self._u(150), y0 + H - self._u(46), self._u(150), self._u(34)),
            "x0": x0, "y0": y0, "W": W, "H": H, "pad": pad,
        }

    # ------------------------------------------------------------------
    # Clique — inbox
    # ------------------------------------------------------------------

    def _handle_inbox_click(self, ui, event) -> None:
        L = self._inbox_layout()
        mx, my = event.pos
        if event.button == 1 and L["close"].collidepoint(mx, my):
            self._close()
            return
        if event.button == 1 and L["send_btn"].collidepoint(mx, my):
            self._open_compose(ui)
            return
        if event.button == 1:
            for i, mail in enumerate(ui.inbox):
                vis_i = i - self._inbox_scroll
                if not (0 <= vis_i < UI.MAIL_MAX_ROWS):
                    continue
                r = pygame.Rect(L["x0"] + L["pad"], L["body_y"] + vis_i * L["row_h"],
                                L["W"] - L["pad"] * 2, L["row_h"] - self._u(4))
                if r.collidepoint(mx, my):
                    from shared.messages import MsgType
                    self._net.send(MsgType.MAIL_OPEN_ITEM, {"mail_id": mail["mail_id"]})
                    return

    def _open_compose(self, ui) -> None:
        ui.compose_to      = ""
        ui.compose_subject = ""
        ui.compose_body    = ""
        ui.compose_items   = []
        ui.compose_gold_text = ""
        ui.compose_focus   = ""
        self._bag_scroll   = 0
        ui.screen = "compose"

    # ------------------------------------------------------------------
    # Clique — compor
    # ------------------------------------------------------------------

    def _handle_compose_click(self, ui, event) -> None:
        L = self._compose_layout()
        mx, my = event.pos

        if event.button == 1 and L["close"].collidepoint(mx, my):
            self._close()
            return
        if event.button == 1 and L["to"].collidepoint(mx, my):
            ui.compose_focus = "to"
            pygame.key.start_text_input()
            return
        if event.button == 1 and L["subject"].collidepoint(mx, my):
            ui.compose_focus = "subject"
            pygame.key.start_text_input()
            return
        if event.button == 1 and L["body"].collidepoint(mx, my):
            ui.compose_focus = "body"
            pygame.key.start_text_input()
            return
        if event.button == 1 and L["gold"].collidepoint(mx, my):
            ui.compose_focus = "gold"
            return
        if event.button == 1 and L["send"].collidepoint(mx, my):
            self._send_compose(ui)
            return

        if event.button == 1 and ui.compose_focus:
            # Clique fora de qualquer campo de texto desfoca (mesma UX do
            # campo de gold do trade).
            ui.compose_focus = ""
            pygame.key.stop_text_input()

        if event.button == 1:
            for i, r in enumerate(L["slots"]):
                if r.collidepoint(mx, my) and i < len(ui.compose_items):
                    ui.compose_items.pop(i)
                    return

        if event.button == 3:
            inv = self.world.get_component(self.player_entity, Inventory)
            if inv is None:
                return
            staged_indices = {idx for idx, _q in ui.compose_items}
            row_h = L["bag_row_h"]
            for i, item in enumerate(inv.items):
                vis_i = i - self._bag_scroll
                r = pygame.Rect(L["bag"].x, L["bag"].y + vis_i * row_h, L["bag"].width, row_h - self._u(2))
                if r.collidepoint(mx, my):
                    if i in staged_indices or len(ui.compose_items) >= 6:
                        return
                    ui.compose_items.append((i, item.stack))
                    return

    def _send_compose(self, ui) -> None:
        if not self._net:
            return
        to_name = ui.compose_to.strip()
        if not to_name:
            LOG.add("Informe o destinatario!", (220, 80, 80))
            return
        try:
            gold = max(0, int(ui.compose_gold_text or "0"))
        except ValueError:
            gold = 0
        if not ui.compose_items and gold <= 0:
            LOG.add("Correspondencia vazia!", (220, 150, 50))
            return
        from shared.messages import MsgType
        subject = ui.compose_subject.strip() or self._subject_placeholder(ui)
        self._net.send(MsgType.MAIL_SEND, {
            "to_name": to_name,
            "subject": subject,
            "body":    ui.compose_body,
            "items":   [{"inv_index": idx} for idx, _q in ui.compose_items],
            "gold":    gold,
        })

    # ------------------------------------------------------------------
    # Clique — abrir correspondência
    # ------------------------------------------------------------------

    def _handle_detail_click(self, ui, event) -> None:
        if ui.detail is None:
            return
        L = self._detail_layout()
        mx, my = event.pos

        if event.button == 1 and L["delete"].collidepoint(mx, my):
            from shared.messages import MsgType
            self._net.send(MsgType.MAIL_DELETE, {"mail_id": ui.detail["mail_id"]})
            ui.detail = None
            ui.screen = "inbox"
            return
        if event.button == 1 and L["close2"].collidepoint(mx, my):
            self._back_to_inbox()
            return
        if (event.button == 1 and not ui.detail["gold_claimed"] and ui.detail["gold"] > 0
                and L["gold"].collidepoint(mx, my)):
            from shared.messages import MsgType
            self._net.send(MsgType.MAIL_CLAIM_GOLD, {"mail_id": ui.detail["mail_id"]})
            return
        if event.button == 3:
            for it in ui.detail["items"]:
                if it["claimed"] or it["item"] is None:
                    continue
                if not (0 <= it["item_index"] < len(L["slots"])):
                    continue
                r = L["slots"][it["item_index"]]
                if r.collidepoint(mx, my):
                    from shared.messages import MsgType
                    self._net.send(MsgType.MAIL_CLAIM_ITEM,
                                   {"mail_id": ui.detail["mail_id"], "item_index": it["item_index"]})
                    return

    # ------------------------------------------------------------------
    # Render — indicador no mundo (correspondência nova)
    # ------------------------------------------------------------------

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        ui = self._ui()
        if ui is None or ui.unread_count <= 0:
            return
        from ui.world_labels import WORLD_LABELS
        icon = self._new_mail_marker_surf()
        for eid, pos, rend, _ in self.world.get_entities_with(Position, Renderable, Mailbox):
            WORLD_LABELS.add_icon(pos.x, pos.y - rend.height / 2, icon, stack_key=eid)

    _new_mail_marker_cache = None

    def _new_mail_marker_surf(self):
        """Círculo + glifo (mesmo padrão de fallback de
        ui/quest_system.py::_fallback_marker_surf) — sem asset dedicado."""
        if self._new_mail_marker_cache is None:
            size = 18
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(surf, (20, 30, 50), (size // 2, size // 2), size // 2 - 1)
            pygame.draw.circle(surf, (120, 170, 255), (size // 2, size // 2), size // 2 - 1, 2)
            glyph = self._font_lg.render("!", False, (120, 170, 255))
            surf.blit(glyph, (size // 2 - glyph.get_width() // 2, size // 2 - glyph.get_height() // 2))
            self._new_mail_marker_cache = surf
        return self._new_mail_marker_cache

    # ------------------------------------------------------------------
    # Render — painéis
    # ------------------------------------------------------------------

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        self.pending_tooltip = None
        ui = self._ui()
        if ui is None or not ui.is_open:
            return
        if ui.screen == "inbox":
            self._render_inbox(ui)
        elif ui.screen == "compose":
            self._render_compose(ui)
        elif ui.screen == "detail":
            self._render_detail(ui)

    def _draw_panel_frame(self, rect: pygame.Rect, title: str) -> None:
        SW, SH = self.hud_surf.get_size()
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 160)), (0, 0))
        self.hud_surf.blit(fill_surf((rect.width, rect.height), (10, 14, 22, 235)), rect.topleft)
        pygame.draw.rect(self.hud_surf, (70, 100, 150), rect, 2, border_radius=4)
        title_s = self._font_lg.render(f"  {title}", False, (170, 200, 255))
        self.hud_surf.blit(title_s, (rect.x + self._u(8), rect.y + self._u(8)))

    def _draw_close_button(self, r: pygame.Rect) -> None:
        mx, my = pygame.mouse.get_pos()
        hov = r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (180, 60, 60) if hov else (100, 35, 35), r, border_radius=3)
        xs = self._font_md.render("X", False, (255, 255, 255))
        self.hud_surf.blit(xs, (r.centerx - xs.get_width() // 2, r.centery - xs.get_height() // 2))

    def _draw_text_field(self, r: pygame.Rect, text: str, focused: bool, placeholder: str = "") -> None:
        pygame.draw.rect(self.hud_surf, (20, 26, 38), r, border_radius=3)
        pygame.draw.rect(self.hud_surf, (140, 170, 220) if focused else (60, 75, 100), r, 1, border_radius=3)
        shown = text if (text or not placeholder) else placeholder
        color = (230, 235, 245) if (text or not placeholder) else (90, 100, 120)
        lbl = self._font_sm.render(shown + ("|" if focused and (pygame.time.get_ticks() // 500) % 2 == 0 else ""),
                                   False, color)
        self.hud_surf.blit(lbl, (r.x + self._u(6), r.centery - lbl.get_height() // 2))

    def _draw_item_slot(self, r: pygame.Rect, item, hovered: bool, hint: str = "") -> None:
        bg_c   = (30, 40, 55) if hovered else (18, 24, 34)
        bord_c = (140, 170, 220) if hovered else (55, 70, 95)
        pygame.draw.rect(self.hud_surf, bg_c, r, border_radius=3)
        pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)
        if item is None:
            return
        rar_col = self._RARITY_COLORS.get(getattr(item, "rarity", "common"), (100, 100, 100))
        icon_surf = ICONS.get(ICONS.item_key(item), r.width - self._u(4))
        if icon_surf:
            self.hud_surf.blit(icon_surf, (r.x + self._u(2), r.y + self._u(2)))
        else:
            pygame.draw.rect(self.hud_surf, rar_col, r.inflate(-self._u(6), -self._u(6)), 1, border_radius=2)
        draw_stack_count(self.hud_surf, item, r, self._font_sm)
        if hovered:
            lines = item_tooltip_lines(item, None)
            if hint:
                lines.append((hint, (150, 150, 150)))
            self.pending_tooltip = (pygame.mouse.get_pos()[0], pygame.mouse.get_pos()[1],
                                    item.name, lines, rar_col, item, None)

    # ── Inbox ────────────────────────────────────────────────────────

    def _render_inbox(self, ui) -> None:
        L = self._inbox_layout()
        self._draw_panel_frame(L["panel"], f"CAIXA DE ENTRADA  ({len(ui.inbox)})")
        self._draw_close_button(L["close"])
        mx, my = pygame.mouse.get_pos()

        max_scroll = max(0, len(ui.inbox) - UI.MAIL_MAX_ROWS)
        self._inbox_scroll = min(self._inbox_scroll, max_scroll)

        if not ui.inbox:
            empty_s = self._font_sm.render("Nenhuma correspondencia.", False, (120, 130, 150))
            self.hud_surf.blit(empty_s, (L["x0"] + L["pad"], L["body_y"]))

        for i, mail in enumerate(ui.inbox):
            vis_i = i - self._inbox_scroll
            if not (0 <= vis_i < UI.MAIL_MAX_ROWS):
                continue
            r = pygame.Rect(L["x0"] + L["pad"], L["body_y"] + vis_i * L["row_h"],
                            L["W"] - L["pad"] * 2, L["row_h"] - self._u(4))
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf, (24, 32, 46) if hov else (16, 21, 30), r, border_radius=3)
            pygame.draw.rect(self.hud_surf, (90, 115, 160) if hov else (45, 55, 75), r, 1, border_radius=3)
            icon_c = (255, 210, 90) if mail.get("has_attachment") else (140, 150, 170)
            self.hud_surf.blit(self._font_sm.render("*" if mail.get("has_attachment") else "-", False, icon_c),
                               (r.x + self._u(6), r.y + self._u(4)))
            name_s = self._font_sm.render(mail.get("sender_name", ""), False, (200, 215, 235))
            self.hud_surf.blit(name_s, (r.x + self._u(22), r.y + self._u(2)))
            subj_s = self._font_sm.render(mail.get("subject", ""), False, (130, 145, 165))
            self.hud_surf.blit(subj_s, (r.x + self._u(22), r.y + self._u(18)))

        # Botão "Enviar Correspondência"
        btn = L["send_btn"]
        hov = btn.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (35, 55, 85) if hov else (24, 38, 60), btn, border_radius=4)
        pygame.draw.rect(self.hud_surf, (120, 160, 220), btn, 1, border_radius=4)
        lbl = self._font_md.render("Enviar Correspondencia", False, (200, 220, 250))
        self.hud_surf.blit(lbl, (btn.centerx - lbl.get_width() // 2, btn.centery - lbl.get_height() // 2))

    # ── Compor ───────────────────────────────────────────────────────

    def _render_compose(self, ui) -> None:
        L = self._compose_layout()
        self._draw_panel_frame(L["panel"], "NOVA CORRESPONDENCIA")
        self._draw_close_button(L["close"])
        mx, my = pygame.mouse.get_pos()

        # Bolsa
        bag_lbl = self._font_md.render("Sua Bolsa", False, (170, 190, 220))
        self.hud_surf.blit(bag_lbl, (L["bag"].x, L["y0"] + self._u(44)))
        inv = self.world.get_component(self.player_entity, Inventory)
        bag_items = inv.items if inv else []
        staged_indices = {idx for idx, _q in ui.compose_items}
        row_h = L["bag_row_h"]
        max_bag_scroll = max(0, len(bag_items) - (L["bag"].height // row_h))
        self._bag_scroll = min(self._bag_scroll, max_bag_scroll)
        for i, item in enumerate(bag_items):
            vis_i = i - self._bag_scroll
            r = pygame.Rect(L["bag"].x, L["bag"].y + vis_i * row_h, L["bag"].width, row_h - self._u(2))
            if r.bottom < L["bag"].y or r.top > L["bag"].bottom:
                continue
            hov = r.collidepoint(mx, my)
            dim = i in staged_indices
            bg_c = (18, 24, 34) if dim else ((28, 38, 55) if hov else (18, 24, 34))
            pygame.draw.rect(self.hud_surf, bg_c, r, border_radius=3)
            rar_col = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
            name_col = (60, 65, 75) if dim else rar_col
            stack = getattr(item, "stack", 1)
            label = item.name if stack <= 1 else f"{item.name} x{stack}"
            self.hud_surf.blit(self._font_sm.render(label, False, name_col), (r.x + self._u(4), r.y + self._u(4)))
            if hov and not dim:
                lines = item_tooltip_lines(item, None)
                lines.append(("Clique direito p/ anexar", (150, 150, 150)))
                self.pending_tooltip = (mx, my, item.name, lines, rar_col, item, None)

        # Campos
        self._draw_text_field(L["to"], ui.compose_to, ui.compose_focus == "to", "Para:")
        subj_placeholder = self._subject_placeholder(ui) or "Assunto:"
        self._draw_text_field(L["subject"], ui.compose_subject, ui.compose_focus == "subject", subj_placeholder)
        # Corpo (multi-linha)
        pygame.draw.rect(self.hud_surf, (20, 26, 38), L["body"], border_radius=3)
        pygame.draw.rect(self.hud_surf, (140, 170, 220) if ui.compose_focus == "body" else (60, 75, 100),
                         L["body"], 1, border_radius=3)
        # Quebra simples por largura (texto livre não precisa de wrap
        # perfeito por parágrafo aqui — mesma tolerância de campo de chat).
        y = L["body"].y + self._u(6)
        for raw_line in ui.compose_body.split("\n"):
            for wrapped in (wrap_text(raw_line, self._font_sm, L["body"].width - self._u(12)) or [""]):
                if y > L["body"].bottom - self._u(18):
                    break
                self.hud_surf.blit(self._font_sm.render(wrapped, False, (220, 225, 235)),
                                   (L["body"].x + self._u(6), y))
                y += self._u(18)

        # Slots de item
        for i, r in enumerate(L["slots"]):
            item_obj = None
            if i < len(ui.compose_items):
                idx, _q = ui.compose_items[i]
                if inv and 0 <= idx < len(inv.items):
                    item_obj = inv.items[idx]
            self._draw_item_slot(r, item_obj, r.collidepoint(mx, my), "Clique p/ remover" if item_obj else "")

        # Gold
        self._draw_text_field(L["gold"], ui.compose_gold_text, ui.compose_focus == "gold", "Moedas de ouro:")

        # Enviar
        btn = L["send"]
        hov = btn.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (35, 55, 85) if hov else (24, 38, 60), btn, border_radius=4)
        pygame.draw.rect(self.hud_surf, (120, 160, 220), btn, 1, border_radius=4)
        lbl = self._font_md.render("Enviar", False, (200, 220, 250))
        self.hud_surf.blit(lbl, (btn.centerx - lbl.get_width() // 2, btn.centery - lbl.get_height() // 2))

    # ── Abrir correspondência ────────────────────────────────────────

    def _render_detail(self, ui) -> None:
        if ui.detail is None:
            self._back_to_inbox()
            return
        L = self._detail_layout()
        self._draw_panel_frame(L["panel"], "CORRESPONDENCIA")
        mx, my = pygame.mouse.get_pos()

        de_s = self._font_sm.render(f"De: {ui.detail['sender_name']}", False, (170, 190, 220))
        self.hud_surf.blit(de_s, (L["x0"] + L["pad"], L["y0"] + self._u(44)))
        as_s = self._font_sm.render(f"Assunto: {ui.detail['subject']}", False, (150, 165, 190))
        self.hud_surf.blit(as_s, (L["x0"] + L["pad"], L["y0"] + self._u(64)))

        pygame.draw.rect(self.hud_surf, (16, 21, 30), L["body"], border_radius=3)
        pygame.draw.rect(self.hud_surf, (55, 70, 95), L["body"], 1, border_radius=3)
        y = L["body"].y + self._u(6)
        for raw_line in ui.detail["body"].split("\n"):
            for wrapped in (wrap_text(raw_line, self._font_sm, L["body"].width - self._u(12)) or [""]):
                if y > L["body"].bottom - self._u(18):
                    break
                self.hud_surf.blit(self._font_sm.render(wrapped, False, (210, 215, 225)),
                                   (L["body"].x + self._u(6), y))
                y += self._u(18)

        pac_s = self._font_sm.render("Pacote:", False, (170, 190, 220))
        self.hud_surf.blit(pac_s, (L["x0"] + L["pad"], L["body"].bottom + self._u(2)))

        for it in ui.detail["items"]:
            idx = it["item_index"]
            if not (0 <= idx < len(L["slots"])):
                continue
            r = L["slots"][idx]
            self._draw_item_slot(r, it["item"], r.collidepoint(mx, my) and not it["claimed"],
                                 "Ja reclamado" if it["claimed"] else "Clique direito p/ reclamar")
            if it["claimed"]:
                overlay = fill_surf((r.width, r.height), (0, 0, 0, 150))
                self.hud_surf.blit(overlay, r.topleft)

        if ui.detail["gold"] > 0:
            r = L["gold"]
            claimed = ui.detail["gold_claimed"]
            hov = r.collidepoint(mx, my) and not claimed
            bg_c = (40, 34, 12) if hov else (26, 22, 10)
            pygame.draw.rect(self.hud_surf, bg_c, r, border_radius=3)
            pygame.draw.rect(self.hud_surf, (180, 150, 60), r, 1, border_radius=3)
            label = f"{ui.detail['gold']}g" + (" (reclamado)" if claimed else " - clique p/ reclamar")
            col = (110, 100, 70) if claimed else (255, 215, 0)
            lbl = self._font_sm.render(label, False, col)
            self.hud_surf.blit(lbl, (r.x + self._u(6), r.centery - lbl.get_height() // 2))

        for btn, label, col in ((L["delete"], "Deletar", (220, 100, 100)),
                                (L["close2"], "Fechar", (180, 190, 210))):
            hov = btn.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf, (40, 25, 25) if hov else (28, 18, 18), btn, border_radius=4) \
                if label == "Deletar" else \
                pygame.draw.rect(self.hud_surf, (30, 38, 50) if hov else (22, 28, 38), btn, border_radius=4)
            pygame.draw.rect(self.hud_surf, col, btn, 1, border_radius=4)
            lbl = self._font_sm.render(label, False, col)
            self.hud_surf.blit(lbl, (btn.centerx - lbl.get_width() // 2, btn.centery - lbl.get_height() // 2))
