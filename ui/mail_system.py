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
composição do que já existe. Visual: atlas de UI (ui/atlas_window.py), 1
janela com abas Entrada/Escrever, cliques no SOLTAR. Estado em `ui/ui_components.py::MailUIState`
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
import copy
import pygame

from ui.fonts import make as _font
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_scale_mixin import UIScaleMixin

from engine.components import (Position, Renderable, TileMovement, PlayerAutoMove,
                               Inventory, Mailbox, NPC, NPCInteractionRequest)
from engine.world import World
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev, get_camera_offset
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines
from ui.npc_interaction import walk_to_npc, suppress_chase
from content.item_table import RARITY_COLORS as _SHARED_RARITY_COLORS
from engine.quest_events import fire as quest_fire

from engine.world_systems import System


class MailSystem(UIScaleMixin, System):
    """Sistema do NPC "Caixa de Correio" — 3 telas (inbox/compose/detail),
    ver MailUIState.screen."""

    _FONT_BASES = {"_font_xs": 15, "_font_sm": 20, "_font_md": 26, "_font_lg": 32}
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
        # Modal de quantidade (Shift+clique direito num item empilhável na
        # bolsa) — peça do kit, mesma da loja/troca.
        self._compose_qty_modal: "dict | None" = None
        # Atlas: botão afundado, rolagem da Entrada, eventos do frame,
        # área segura (injetada por game.py), rects clicáveis (testes),
        # cache do marcador de carta nova (por instância).
        self._press = aw.PressState()
        self._scroll = aw.ScrollState()
        self._frame_events: list = []
        self.safe_area_fn = None
        self._hit_rects: dict = {}
        self._new_mail_marker_cache = None

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
    def compose_field_focused(self) -> bool:
        """True enquanto um campo de texto da composição está com foco —
        usado por game.py (atalhos de menu) e PlayerInputSystem (WASD)
        pra parar de vazar digitação pro resto do jogo, mesmo padrão de
        `self._chat_active`/`self._trade_gold_focus`."""
        ui = self._ui()
        # `ui.screen == "compose"` é checado de propósito (não só
        # `compose_focus` truthy) — defesa extra contra o mesmo campo
        # ficar "preso" caso algum caminho futuro feche o modal sem
        # limpar o foco (ver `_close`/`_back_to_inbox`, que já limpam,
        # mas nunca custa a segunda trava pra um bug que já travou o
        # personagem uma vez).
        return ui is not None and ui.screen == "compose" and bool(ui.compose_focus)

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
        """Abre o inbox, OU — se travado por quest (06/10/2026,
        generalização pedida pelo usuário, mesmo padrão de `ui/
        shop_system.py::_try_open_merchant`/`ui/crafting_system.py::
        _open_menu`) — pede pro QuestDialogSystem abrir no lugar. Nenhum
        mailbox hoje configura `NPCUnlock`, mas a fiação já fica pronta
        pra quando um precisar."""
        ui = self._ui()
        if ui is None or not self._net:
            return
        from engine.components import QuestLog as _QL_mail
        import engine.quest_logic as _quest_logic_mail
        ql = self.world.get_component(self.player_entity, _QL_mail)
        if not _quest_logic_mail.is_npc_unlocked(self.world, mailbox_eid, ql):
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=mailbox_eid))
            self._open_cooldown = 0.3
            return
        ui.mailbox_eid = mailbox_eid
        ui.screen = "inbox"
        self._pending_mailbox_id = -1
        self._open_cooldown = 0.3
        self._scroll = aw.ScrollState()
        from shared.messages import MsgType
        self._net.send(MsgType.MAIL_OPEN_INBOX, {})
        _npc = self.world.get_component(mailbox_eid, NPC)
        quest_fire("talk_to_npc", npc_name=_npc.name if _npc else "Correio")

    def _close(self) -> None:
        ui = self._ui()
        if ui is not None:
            # Bug real relatado pelo usuário 25/09/2026: fechar o correio
            # com um campo de texto ainda focado (ex: "Para") deixava
            # `compose_focus` preso num valor truthy pra sempre —
            # `compose_field_focused` continuava True mesmo com o modal
            # fechado, bloqueando WASD indefinidamente (ver
            # ui/player_input_system.py). Limpa o foco em QUALQUER
            # fechamento, não só quando o usuário desfoca manualmente.
            if ui.compose_focus:
                pygame.key.stop_text_input()
            ui.compose_focus = ""
            ui.screen  = "closed"
            ui.detail  = None
        self._compose_qty_modal = None
        self._pending_mailbox_id = -1

    def _back_to_inbox(self) -> None:
        ui = self._ui()
        if ui is not None and ui.sub_screen_open:
            if ui.compose_focus:
                pygame.key.stop_text_input()
            ui.compose_focus = ""
            ui.screen = "inbox"
            ui.detail = None
            self._compose_qty_modal = None

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
    # Eventos de UI — teclado aqui; mouse no desenho (atlas, age no SOLTAR)
    # ------------------------------------------------------------------

    def handle_events(self, events: list) -> None:
        ui = self._ui()
        if ui is None or not ui.is_open:
            return
        for event in events:
            if self._compose_qty_modal is not None:
                res = aw.quantity_key(self._compose_qty_modal, event)
                if res == "ok":
                    self._confirm_compose_qty_modal(ui, self._compose_qty_modal)
                elif res == "cancel":
                    self._close_compose_qty_modal()
                continue
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
        self._frame_events = list(events)

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

    def _open_compose(self, ui) -> None:
        ui.compose_to      = ""
        ui.compose_subject = ""
        ui.compose_body    = ""
        ui.compose_items   = []
        ui.compose_gold_text = ""
        ui.compose_focus   = ""
        self._compose_qty_modal = None
        ui.screen = "compose"

    def _focus(self, ui, field: str) -> None:
        ui.compose_focus = field
        if field in ("to", "subject", "body"):
            pygame.key.start_text_input()

    def _unfocus(self, ui) -> None:
        if ui.compose_focus:
            ui.compose_focus = ""
            pygame.key.stop_text_input()

    # ── Modal de quantidade (Shift+clique direito num item empilhável) —
    # peça do kit (atlas_window.quantity_window), mesma da loja/troca;
    # aqui sem preço: "quantos dessa pilha vão pra este envio". ──────────

    def _open_compose_qty_modal(self, inv_index: int, item) -> None:
        self._compose_qty_modal = {**aw.quantity_state(item.stack),
                                   "inv_index": inv_index, "item_name": item.name}

    def _close_compose_qty_modal(self) -> None:
        self._compose_qty_modal = None

    def _confirm_compose_qty_modal(self, ui, m: dict) -> None:
        if len(ui.compose_items) < 6:
            ui.compose_items.append((m["inv_index"], m["qty"]))
        self._close_compose_qty_modal()

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
            "items":   [{"inv_index": idx, "quantity": q} for idx, q in ui.compose_items],
            "gold":    gold,
        })

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

    def _new_mail_marker_surf(self):
        """Círculo + glifo (mesmo padrão de fallback de
        ui/quest_system.py::_fallback_marker_surf) — sem asset dedicado.
        Cache POR INSTÂNCIA (nunca atributo de classe)."""
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
    # Render — janela do correio (atlas de UI, 21/10/2026)
    # ------------------------------------------------------------------
    # 1 janela "Correio" com barra de título e X e as abas [Entrada]
    # [Escrever] (dentro do painel, na borda de cima — decisão do usuário);
    # ler uma carta abre dentro da aba Entrada (Voltar retorna). Cliques
    # agem no SOLTAR (atlas_window); campos de texto do atlas.

    _TW, _TH = 22, 15
    _BAG_COLS, _BAG_ROWS = 4, 5
    _ROW0 = 3                # 1ª linha de conteúdo (título 0, abas 1, folga 2)
    _MAIL_ROW_H = 2          # tiles por carta na lista da Entrada

    def _area(self) -> pygame.Rect:
        if self.safe_area_fn is not None:
            return self.safe_area_fn()
        SW, SH = self.hud_surf.get_size()
        return aw.safe_area(SW, SH, SH)

    def _mail_hit(self, hid: str, f, x0, y0, x1, y1) -> None:
        self._hit_rects[hid] = pygame.Rect(f.px + round(x0 * aw.S), f.py + round(y0 * aw.S),
                                           round((x1 - x0) * aw.S), round((y1 - y0) * aw.S))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        events, self._frame_events = self._frame_events, []
        self.pending_tooltip = None
        self._hit_rects = {}
        ui = self._ui()
        if ui is None or not ui.is_open:
            return
        if ui.screen == "detail" and ui.detail is None:
            self._back_to_inbox()
        T = aw.T
        modal = self._compose_qty_modal is not None
        tw, th = self._TW, self._TH
        f = aw.Frame(self.hud_surf, [] if modal else events, tw, th, area=self._area(),
                     press=None if modal else self._press, font=self._font_md,
                     raw=ui_atlas.titled_dark_window_raw(tw, th))
        f.text("Correio", (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("mail:close", tw - 1):
            self._close()
            return
        self._mail_hit("mail:close", f, (tw - 1) * T, 0, tw * T, T)

        on_compose = ui.screen == "compose"
        tab = f.tabs("mail:tabs", 1, 1, [f"Entrada ({len(ui.inbox)})", "Escrever"], 1 if on_compose else 0,
                     font=self._font_sm)
        w0 = f.tiles_for([f"Entrada ({len(ui.inbox)})"], 3, self._font_sm)
        w1 = f.tiles_for(["Escrever"], 3, self._font_sm)
        self._mail_hit("mail:tab:0", f, T, T, (1 + w0) * T, 2 * T)
        self._mail_hit("mail:tab:1", f, (1 + w0) * T, T, (1 + w0 + w1) * T, 2 * T)

        if ui.screen == "inbox":
            self._draw_inbox(ui, f)
        elif ui.screen == "compose":
            self._draw_compose(ui, f)
        elif ui.screen == "detail":
            self._draw_detail(ui, f)
        content = getattr(f, "_mail_content", None)
        f.end()
        if content is not None:
            content()   # lista rolável desenhada por cima do painel ampliado
        self._panel_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)

        if tab == 0 and ui.screen != "inbox":
            self._back_to_inbox()
        elif tab == 1 and ui.screen != "compose":
            self._open_compose(ui)

        if modal:
            self.pending_tooltip = None   # modal suprime tooltip dos itens abaixo
            m = self._compose_qty_modal
            res = aw.quantity_window(self.hud_surf, events, m, area=self._area(), press=self._press,
                                     font=self._font_md, small_font=self._font_sm,
                                     title=m["item_name"], info=f"Você tem {m['max_qty']}",
                                     confirm_label=f"Anexar {m['qty']}", hits=self._hit_rects)
            if res == "ok":
                self._confirm_compose_qty_modal(ui, m)
            elif res == "cancel":
                self._close_compose_qty_modal()

    # ── Entrada ──────────────────────────────────────────────────────

    def _draw_inbox(self, ui, f) -> None:
        T, S = aw.T, aw.S
        tw, th = self._TW, self._TH
        list_cols = tw - 3
        view_y0, view_h = self._ROW0 * T, (th - self._ROW0 - 1) * T
        row_h = self._MAIL_ROW_H * T
        content_h = max(1, len(ui.inbox) * row_h)
        off = f.scrollbar(self._scroll, tw - 2, self._ROW0, th - 2, max(0, content_h - view_h))
        lw = list_cols * T
        in_view = view_y0 <= f.ry < view_y0 + view_h and T <= f.rx < T + lw
        fc = f.child(pygame.Surface((lw, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        fc.rx = f.rx - T
        opened = None
        if not ui.inbox:
            f.text("Nenhuma correspondência.", T + 4, view_y0 + T / 2, aw.DISABLED_COL, self._font_sm,
                   anchor="midleft")
        for i, mail in enumerate(ui.inbox):
            y = i * row_h
            if i:
                fc.divider_h(y, 0, lw)
            hov = fc.hover(0, y, lw, y + row_h)
            mark = "*" if mail.get("has_attachment") else "-"
            fc.text(mark, 6, y + T / 2, (255, 210, 90) if mail.get("has_attachment") else aw.DISABLED_COL,
                    self._font_sm, anchor="midleft")
            fc.text(mail.get("sender_name", ""), 18, y + T / 2,
                    aw.ACTIVE_COL if hov else aw.TEXT_COL, self._font_sm, anchor="midleft")
            fc.text(mail.get("subject", ""), 18, y + 1.4 * T, (150, 140, 120), self._font_xs, anchor="midleft")
            if fc.press(f"mail:open:{i}", 0, y, lw, y + row_h)[1]:
                opened = mail
            ry = view_y0 + y - off
            if view_y0 <= ry and ry + row_h <= view_y0 + view_h:
                self._mail_hit(f"mail:open:{i}", f, T, ry, T + lw, ry + row_h)

        def _draw():
            content = ui_atlas.scale_up(fc.raw)
            aw.draw_texts(content, fc.texts, self._font_sm)
            view = content.subsurface((0, off * S, content.get_width(),
                                       max(0, min(view_h, content_h - off)) * S))
            self.hud_surf.blit(view, (f.px + T * S, f.py + view_y0 * S))
        f._mail_content = _draw
        if opened is not None and self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.MAIL_OPEN_ITEM, {"mail_id": opened["mail_id"]})

    # ── Escrever ─────────────────────────────────────────────────────

    def _draw_compose(self, ui, f) -> None:
        T, S = aw.T, aw.S
        tw, th = self._TW, self._TH
        mx, my = pygame.mouse.get_pos()
        modal = self._compose_qty_modal is not None
        ip = aw.slot_icon_px()
        inv = self.world.get_component(self.player_entity, Inventory)
        items = inv.items if inv else []
        staged = {idx for idx, _q in ui.compose_items}
        shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)

        # Bolsa (clique direito anexa; Shift+direito = quantidade)
        f.text("Sua bolsa", T, (self._ROW0 - 0.5) * T, (190, 170, 120), self._font_sm, anchor="midleft")
        cells = []
        for i, it in enumerate(items):
            cells.append(None if it is None else
                         (ICONS.get(ICONS.item_key(it), ip), it.stack if getattr(it, "max_stack", 1) > 1 else 0))
        bag0 = self._ROW0
        hov_i, _ = f.grid("mail:bag", 1, bag0, self._BAG_COLS, self._BAG_ROWS, cells,
                          count_font=self._font_xs)
        for i in range(len(items)):
            c, r = i % self._BAG_COLS, i // self._BAG_COLS
            self._mail_hit(f"mail:bag:{i}", f, (1 + c) * T, (bag0 + r) * T, (2 + c) * T, (bag0 + r + 1) * T)
            if i in staged:   # já anexado: escurece
                dim = pygame.Surface((ip, ip), pygame.SRCALPHA)
                dim.fill((0, 0, 0, 160))
                f.overlays.append((dim, (1 + c) * T + 1, (bag0 + r) * T + 1))
        it = items[hov_i] if 0 <= hov_i < len(items) else None
        if it is not None and hov_i not in staged:
            rar = self._RARITY_COLORS.get(it.rarity, (200, 200, 200))
            lines = item_tooltip_lines(it, None)
            lines.append(("Clique dir. anexa tudo | Shift+dir. = qtd." if getattr(it, "max_stack", 1) > 1
                          and it.stack > 1 else "Clique direito p/ anexar", (150, 150, 150)))
            self.pending_tooltip = (mx, my, it.name, lines, rar, it, None)
            if f.rreleased and not modal and len(ui.compose_items) < 6:
                if shift and getattr(it, "max_stack", 1) > 1 and it.stack > 1:
                    self._open_compose_qty_modal(hov_i, it)
                else:
                    ui.compose_items.append((hov_i, it.stack))

        # Formulário
        fx0 = 1 + self._BAG_COLS + 1
        fw = tw - 1 - fx0 - 1
        cur = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else ""
        fields = (("to", self._ROW0, fw, ui.compose_to, "Para:"),
                  ("subject", self._ROW0 + 1, fw, ui.compose_subject,
                   self._subject_placeholder(ui) or "Assunto:"))
        over_any = False
        for key, row, w, value, ph in fields:
            focused = ui.compose_focus == key
            if f.text_field(f"mail:{key}", fx0, row, w, value + (cur if focused else ""), active=focused,
                            placeholder=ph, align="left", font=self._font_sm):
                self._focus(ui, key)
            over_any |= f.field_hover(fx0, row, w)
            self._mail_hit(f"mail:{key}", f, fx0 * T, row * T, (fx0 + w) * T, (row + 1) * T)

        # Corpo (multi-linha): moldura do atlas, texto com quebra
        by0, bh = self._ROW0 + 2, 4
        ui_atlas.blit_frame(f.raw, fx0, by0, fw, bh)
        body_focused = ui.compose_focus == "body"
        f.text_block((ui.compose_body + (cur if body_focused else "")) or " ",
                     fx0 * T + 8, by0 * T + 8, fw * T - 16, aw.TEXT_COL, self._font_sm)
        body_hov = f.hover(fx0 * T, by0 * T, (fx0 + fw) * T, (by0 + bh) * T)
        if f.press("mail:body", fx0 * T, by0 * T, (fx0 + fw) * T, (by0 + bh) * T)[1]:
            self._focus(ui, "body")
        over_any |= body_hov
        self._mail_hit("mail:body", f, fx0 * T, by0 * T, (fx0 + fw) * T, (by0 + bh) * T)

        # Anexos (6 slots — mostra a QUANTIDADE ESCOLHIDA, cópia rasa só
        # pra desenho, nunca muta o item da bolsa; clique direito remove)
        sy = (by0 + bh) * T + 4
        for i in range(6):
            item_obj = None
            if i < len(ui.compose_items):
                idx, qty = ui.compose_items[i]
                if 0 <= idx < len(items) and items[idx] is not None:
                    item_obj = copy.copy(items[idx])
                    item_obj.stack = qty
            sx = fx0 * T + i * (T + 2)
            icon = ICONS.get(ICONS.item_key(item_obj), ip) if item_obj else None
            count = item_obj.stack if item_obj and getattr(item_obj, "max_stack", 1) > 1 else 0
            hov, _ = f.slot(f"mail:att:{i}", sx, sy, icon, count, dark=True, count_font=self._font_xs)
            self._mail_hit(f"mail:att:{i}", f, sx, sy, sx + T, sy + T)
            if hov and item_obj is not None:
                lines = item_tooltip_lines(item_obj, None)
                lines.append(("Clique direito p/ remover", (150, 150, 150)))
                self.pending_tooltip = (mx, my, item_obj.name, lines,
                                        self._RARITY_COLORS.get(item_obj.rarity, (200, 200, 200)), item_obj, None)
                if f.rreleased and not modal:
                    ui.compose_items.pop(i)

        # Ouro
        grow = by0 + bh + 2
        gfocused = ui.compose_focus == "gold"
        if f.text_field("mail:gold", fx0, grow, 7, ui.compose_gold_text + (cur if gfocused else ""),
                        active=gfocused, placeholder="Moedas de ouro:", align="left", font=self._font_sm):
            self._focus(ui, "gold")
        over_any |= f.field_hover(fx0, grow, 7)
        self._mail_hit("mail:gold", f, fx0 * T, grow * T, (fx0 + 7) * T, (grow + 1) * T)

        # Clique fora de qualquer campo desfoca (mesma UX do ouro do trade)
        if f.clicked and ui.compose_focus and not over_any:
            self._unfocus(ui)

        bw = f.tiles_for(["Enviar"], 3)
        if f.button("mail:send", tw - 1 - bw, th - 2, bw, "Enviar"):
            self._send_compose(ui)
        ay0, ay1 = ui_atlas.button_art_y(True)
        self._mail_hit("mail:send", f, (tw - 1 - bw) * T, (th - 2) * T + ay0, (tw - 1) * T, (th - 2) * T + ay1)

    # ── Ler correspondência ──────────────────────────────────────────

    def _draw_detail(self, ui, f) -> None:
        T = aw.T
        tw, th = self._TW, self._TH
        mx, my = pygame.mouse.get_pos()
        d = ui.detail
        if d is None:
            return
        ip = aw.slot_icon_px()
        f.text(f"De: {d['sender_name']}", T, self._ROW0 * T + T / 2, aw.TEXT_COL, self._font_sm, anchor="midleft")
        f.text(f"Assunto: {d['subject']}", T, (self._ROW0 + 1) * T + T / 2, (190, 170, 120),
               self._font_sm, anchor="midleft")
        by0, bh = self._ROW0 + 2, 5
        ui_atlas.blit_frame(f.raw, 1, by0, tw - 2, bh)
        f.text_block(d["body"] or " ", T + 8, by0 * T + 8, (tw - 2) * T - 16, aw.TEXT_COL, self._font_sm)

        # Pacote: item reclamado SOME do slot (pedido do usuário 25/09/2026)
        prow = by0 + bh
        f.text("Pacote:", T, prow * T + T / 2, (190, 170, 120), self._font_sm, anchor="midleft")
        px0 = 5 * T
        claim = None
        for it in d["items"]:
            idx = it["item_index"]
            if not 0 <= idx < 6:
                continue
            sx = px0 + idx * (T + 2)
            item = None if it["claimed"] else it["item"]
            icon = ICONS.get(ICONS.item_key(item), ip) if item else None
            count = item.stack if item and getattr(item, "max_stack", 1) > 1 else 0
            hov, fired = f.slot(f"mail:claim:{idx}", sx, prow * T, icon, count, dark=True,
                                count_font=self._font_xs, enabled=item is not None)
            self._mail_hit(f"mail:claim:{idx}", f, sx, prow * T, sx + T, (prow + 1) * T)
            if hov and item is not None:
                lines = item_tooltip_lines(item, None)
                lines.append(("Clique p/ reclamar", (150, 150, 150)))
                self.pending_tooltip = (mx, my, item.name, lines,
                                        self._RARITY_COLORS.get(item.rarity, (200, 200, 200)), item, None)
                if fired or f.rreleased:   # qualquer um dos 2 botões reclama
                    claim = ("item", idx)

        if d["gold"] > 0:
            label = (f"{d['gold']}g (reclamado)" if d["gold_claimed"]
                     else f"Reclamar {d['gold']}g")
            gw = f.tiles_for([label], 3, self._font_sm)
            if f.button("mail:gold", 1, prow + 1, gw, label, tall=False, enabled=not d["gold_claimed"]):
                claim = ("gold",)
            if not d["gold_claimed"] and f.right_click(T, (prow + 1) * T, (1 + gw) * T, (prow + 2) * T):
                claim = ("gold",)
            by0b, by1b = ui_atlas.button_art_y(False)
            self._mail_hit("mail:gold", f, T, (prow + 1) * T + by0b, (1 + gw) * T, (prow + 1) * T + by1b)

        bw = f.tiles_for(["Deletar", "Voltar"], 3)
        delete = f.button("mail:delete", 1, th - 2, bw, "Deletar")
        back = f.button("mail:back", tw - 1 - bw, th - 2, bw, "Voltar")
        ay0, ay1 = ui_atlas.button_art_y(True)
        self._mail_hit("mail:delete", f, T, (th - 2) * T + ay0, (1 + bw) * T, (th - 2) * T + ay1)
        self._mail_hit("mail:back", f, (tw - 1 - bw) * T, (th - 2) * T + ay0, (tw - 1) * T, (th - 2) * T + ay1)

        from shared.messages import MsgType
        if claim and self._net:
            if claim[0] == "gold":
                self._net.send(MsgType.MAIL_CLAIM_GOLD, {"mail_id": d["mail_id"]})
            else:
                self._net.send(MsgType.MAIL_CLAIM_ITEM, {"mail_id": d["mail_id"], "item_index": claim[1]})
        if delete:
            if self._net:
                self._net.send(MsgType.MAIL_DELETE, {"mail_id": d["mail_id"]})
            ui.detail = None
            ui.screen = "inbox"
        elif back:
            self._back_to_inbox()
