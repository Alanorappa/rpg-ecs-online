"""
trade_handlers.py — Mixin com o sistema de trade (player↔player): popup de
Shift+clique num player remoto, modal de convite (aceitar/recusar) e a
janela de troca em si (5 slots de cada lado + gold + confirmar/cancelar).
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self.player_entity, self.screen, self._net, self.font_*,
self._u(), self._safe_panel_origin() e self._get_trade_ui() (já definido em
game.py, roteia pro componente ECS TradeUIState).

Autoridade é o servidor (server/trade_processor.py) — este arquivo só
reflete TRADE_STATE/TRADE_OPEN/TRADE_RESULT/TRADE_CANCELLED (ver
client/network_handlers.py) e envia os 8 tipos de mensagem C→S. Item
ofertado só sai da Inventory de verdade quando o servidor confirma via
TRADE_STATE — nunca otimista (mesmo racional do EQUIP_REJECTED).
"""
import pygame

from engine.components import Inventory, RemoteControlled, Wallet
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.sound_manager import SOUNDS
from ui.ui_helpers import item_tooltip_lines, draw_stack_count
from ui.ui_sizes import UI


_RARITY_COLORS = {
    "common":    (200, 200, 200),
    "uncommon":  ( 30, 200,  30),
    "rare":      ( 80, 140, 255),
    "epic":      (180,  50, 255),
    "legendary": (224, 135,  47),
    "mythic":    (221,  68,  68),
}


class TradeHandlers:

    # ── Envio de mensagens C→S ────────────────────────────────────────────

    def _send_trade_request(self, target_local_eid: int) -> None:
        if not self._net:
            return
        rc = self.world.get_component(target_local_eid, RemoteControlled)
        if rc is None:
            return
        from shared.messages import MsgType
        tui = self._get_trade_ui()
        if tui is not None:
            tui.awaiting_response_to_eid  = target_local_eid
            tui.awaiting_response_to_name = rc.name
        self._net.send(MsgType.TRADE_REQUEST, {"target_eid": rc.server_eid})

    def _send_duel_request(self, target_local_eid: int) -> None:
        """Botão "Duelar" do modal de interação — mesmo formato do trade
        (server resolve convite/aceite, ver server/duel_processor.py)."""
        if not self._net:
            return
        rc = self.world.get_component(target_local_eid, RemoteControlled)
        if rc is None:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.DUEL_REQUEST, {"target_eid": rc.server_eid})

    def _send_party_invite_request(self, target_local_eid: int) -> None:
        """Botão "Convidar p/ Grupo" do modal — mesmo formato do trade/
        duelo (server resolve convite/aceite, ver server/party_processor.py).
        Também usado pelo comando de chat "/convidar" (client/
        party_handlers.py) depois de resolver o nome pro eid local."""
        if not self._net:
            return
        rc = self.world.get_component(target_local_eid, RemoteControlled)
        if rc is None:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.PARTY_INVITE, {"target_eid": rc.server_eid})

    def _offer_trade_item(self, inv_index: int) -> None:
        if not self._net:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.TRADE_OFFER_ITEM, {"inv_index": inv_index})

    def _withdraw_trade_item(self, offer_slot: int) -> None:
        if not self._net:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.TRADE_WITHDRAW_ITEM, {"offer_slot": offer_slot})

    def _confirm_trade_gold(self) -> None:
        if not self._net:
            return
        try:
            amount = max(0, int(self._trade_gold_text or "0"))
        except ValueError:
            amount = 0
        from shared.messages import MsgType
        self._net.send(MsgType.TRADE_SET_GOLD, {"amount": amount})
        self._trade_gold_focus = False

    def _send_trade_confirm(self) -> None:
        if not self._net:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.TRADE_CONFIRM, {})

    def _close_trade(self) -> None:
        """Fecha o que estiver de trade aberto no momento — usado pelo ESC/
        registro de modais (ModalStackHandlers). Convite recebido é recusado;
        trade ativa é cancelada (servidor devolve item/gold dos 2 lados)."""
        tui = self._get_trade_ui()
        if tui is None:
            return
        from shared.messages import MsgType
        if tui.pending_invite_from_eid != -1 and self._net:
            self._net.send(MsgType.TRADE_DECLINE, {})
            tui.clear_invite()
        elif tui.is_open and self._net:
            self._net.send(MsgType.TRADE_CANCEL, {})
        else:
            tui.popup_target_eid = -1
            tui.clear_invite()
        self._trade_gold_focus = False

    @property
    def _trade_is_open(self) -> bool:
        tui = self._get_trade_ui()
        return tui is not None and tui.is_open

    # ── Input (teclado — campo de gold) ──────────────────────────────────

    def _handle_trade_gold_key(self, event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._confirm_trade_gold()
        elif event.key == pygame.K_ESCAPE:
            self._trade_gold_focus = False
        elif event.key == pygame.K_BACKSPACE:
            self._trade_gold_text = self._trade_gold_text[:-1]
        elif event.unicode.isdigit() and len(self._trade_gold_text) < 9:
            self._trade_gold_text += event.unicode

    # ── Input (mouse) ─────────────────────────────────────────────────────

    def _handle_trade_click(self, event) -> bool:
        """Retorna True se o clique foi consumido por alguma UI de trade."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button not in (1, 3):
            return False
        tui = self._get_trade_ui()
        if tui is None:
            return False

        if tui.pending_invite_from_eid != -1:
            self._click_trade_invite(event, tui)
            return True

        if tui.popup_target_eid != -1:
            if self._click_trade_popup(event, tui):
                return True
            tui.popup_target_eid = -1
            return False

        if tui.is_open:
            return self._click_trade_window(event, tui)

        return False

    def _click_trade_popup(self, event, tui) -> bool:
        mx, my = event.pos
        rect, btns = self._player_popup_button_rects(tui)
        if not rect.collidepoint(mx, my):
            return False
        if event.button != 1:
            return True
        target_eid = tui.popup_target_eid
        if btns[0].collidepoint(mx, my):      # Negociar
            tui.popup_target_eid = -1
            self._send_trade_request(target_eid)
            SOUNDS.play_ui("button_click")
        elif btns[1].collidepoint(mx, my):    # Duelar
            tui.popup_target_eid = -1
            self._send_duel_request(target_eid)
            SOUNDS.play_ui("button_click")
        elif btns[2].collidepoint(mx, my):    # Seguir
            tui.popup_target_eid = -1
            from engine.components import PlayerAutoMove as _PAMfl
            _pam = self.world.get_component(self.player_entity, _PAMfl)
            if _pam is not None:
                _pam.follow_eid    = target_eid   # eid LOCAL do proxy remoto
                _pam.ground_target = None
                _pam.path.clear()
                _pam.path_recalc_timer = 0.0
            SOUNDS.play_ui("button_click")
        elif btns[3].collidepoint(mx, my):    # Convidar p/ Grupo
            tui.popup_target_eid = -1
            self._send_party_invite_request(target_eid)
            SOUNDS.play_ui("button_click")
        return True

    def _click_trade_invite(self, event, tui) -> None:
        if event.button != 1:
            return
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.TRADE_INVITE_W), self._u(UI.TRADE_INVITE_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        mx, my = event.pos
        btn_w = (w - self._u(30)) // 2
        accept_r = pygame.Rect(x0 + self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        decline_r = pygame.Rect(x0 + w - btn_w - self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        from shared.messages import MsgType
        if accept_r.collidepoint(mx, my):
            if self._net:
                self._net.send(MsgType.TRADE_ACCEPT, {})
            tui.clear_invite()
            SOUNDS.play_ui("button_click")
        elif decline_r.collidepoint(mx, my):
            if self._net:
                self._net.send(MsgType.TRADE_DECLINE, {})
            tui.clear_invite()
            SOUNDS.play_ui("button_click")

    def _trade_window_origin(self):
        x0, y0 = self._safe_panel_origin(UI.TRADE_W, UI.TRADE_H)
        return x0 + UI.TRADE_OFFSET_X, y0 + UI.TRADE_OFFSET_Y

    # Geometria: bag (própria, 4×5=20 slots) à esquerda, coluna "minha oferta"
    # (1 retângulo de gold editável + 5 slots de item, alinhados às 5 linhas
    # da bag) no meio, coluna "oferta do outro" (read-only, mesmo formato) à
    # direita. Layout pedido pelo usuário — ver PROBLEMAS_ARQUITETURA.md.
    _BAG_COLS = 4
    _BAG_ROWS = 5
    _TITLE_H = 36
    _HEADER_H = 32
    _HEADER_GAP = 6
    _COL_GAP = 16
    _BOTTOM_H = 50

    def _trade_layout(self, x0: int, y0: int) -> dict:
        pad = self._u(UI.TRADE_PAD)
        slot = self._u(UI.TRADE_SLOT_SZ)
        gap = self._u(UI.TRADE_SLOT_GAP)
        title_h = self._u(self._TITLE_H)
        header_h = self._u(self._HEADER_H)
        header_gap = self._u(self._HEADER_GAP)
        col_gap = self._u(self._COL_GAP)

        bag_w = self._BAG_COLS * slot + (self._BAG_COLS - 1) * gap
        content_y0 = y0 + title_h + header_h + header_gap

        bag_x = x0 + pad
        my_col_x = bag_x + bag_w + col_gap
        their_col_x = my_col_x + slot + col_gap

        bag_rects = []
        for r in range(self._BAG_ROWS):
            for c in range(self._BAG_COLS):
                bag_rects.append(pygame.Rect(bag_x + c * (slot + gap),
                                             content_y0 + r * (slot + gap), slot, slot))

        def _item_col_rects(col_x: int) -> list:
            return [pygame.Rect(col_x, content_y0 + i * (slot + gap), slot, slot)
                    for i in range(self._BAG_ROWS)]

        # gold rect ocupa a própria faixa de cabeçalho (acima do content_y0)
        my_gold_rect = pygame.Rect(my_col_x, y0 + title_h, slot, header_h)
        their_gold_rect = pygame.Rect(their_col_x, y0 + title_h, slot, header_h)

        bag_bottom = content_y0 + self._BAG_ROWS * slot + (self._BAG_ROWS - 1) * gap
        btn_y = bag_bottom + self._u(10)
        btn_h = self._u(36)
        negociar_r = pygame.Rect(bag_x, btn_y, bag_w, btn_h)
        cancelar_w = slot * 2 + col_gap
        cancelar_r = pygame.Rect(my_col_x, btn_y, cancelar_w, btn_h)
        close_r = pygame.Rect(x0 + self._u(UI.TRADE_W) - self._u(32), y0 + self._u(6),
                              self._u(26), self._u(26))

        return {
            "bag": bag_rects, "my_items": _item_col_rects(my_col_x),
            "their_items": _item_col_rects(their_col_x),
            "my_gold": my_gold_rect, "their_gold": their_gold_rect,
            "bag_x": bag_x, "my_col_x": my_col_x, "their_col_x": their_col_x,
            "content_y0": content_y0, "negociar": negociar_r, "cancelar": cancelar_r,
            "close": close_r,
        }

    def _click_trade_window(self, event, tui) -> bool:
        x0, y0 = self._trade_window_origin()
        w, h = self._u(UI.TRADE_W), self._u(UI.TRADE_H)
        rect = pygame.Rect(x0, y0, w, h)
        mx, my = event.pos
        if not rect.collidepoint(mx, my):
            return False

        L = self._trade_layout(x0, y0)

        if event.button == 1:
            if L["close"].collidepoint(mx, my):
                self._close_trade()
                return True
            if L["my_gold"].collidepoint(mx, my):
                if not self._trade_gold_focus:
                    self._trade_gold_focus = True
                    self._trade_gold_text = str(tui.my_gold) if tui.my_gold else ""
                return True
            # Clique em QUALQUER OUTRO lugar com o campo de gold focado envia o
            # valor digitado antes de prosseguir — sem isso, digitar um valor e
            # clicar direto em "Negociar" descartava o texto sem nunca mandar
            # TRADE_SET_GOLD (bug real: gold "zerava" e a troca saía sem gold).
            if self._trade_gold_focus:
                self._confirm_trade_gold()
            if L["negociar"].collidepoint(mx, my):
                self._send_trade_confirm()
                SOUNDS.play_ui("button_click")
                return True
            if L["cancelar"].collidepoint(mx, my):
                self._close_trade()
                return True

        if event.button == 3:
            from engine.components import Inventory as _InvTC
            inv = self.world.get_component(self.player_entity, _InvTC)
            if inv is not None:
                for i, r in enumerate(L["bag"]):
                    if r.collidepoint(mx, my) and i < len(inv.items):
                        self._offer_trade_item(i)
                        return True
            for i, r in enumerate(L["my_items"]):
                if r.collidepoint(mx, my) and i < len(tui.my_offer):
                    self._withdraw_trade_item(i)
                    return True

        return True

    # ── Render ─────────────────────────────────────────────────────────────

    def _draw_trade_ui(self) -> None:
        tui = self._get_trade_ui()
        if tui is None:
            return
        if tui.pending_invite_from_eid != -1:
            self._draw_trade_invite(tui)
            return
        if tui.popup_target_eid != -1:
            self._draw_trade_popup(tui)
        if tui.is_open:
            self._draw_trade_window(tui)

    # Modal de interação com player (clique direito em player amigável —
    # substituiu o shift+clique/"Trade", decisão do usuário 16/07/2026).
    # (label, cor_fundo, cor_borda, cor_texto) na ordem vertical dos botões.
    _PLAYER_POPUP_BUTTONS = (
        ("Negociar", (60, 90, 50),  (110, 170, 90), (220, 240, 220)),
        ("Duelar",   (90, 50, 50),  (170, 100, 90), (240, 220, 220)),
        ("Seguir",   (50, 70, 95),  (100, 140, 180), (220, 230, 240)),
        ("Convidar p/ Grupo", (80, 75, 40), (150, 140, 80), (240, 235, 210)),
    )

    def _player_popup_button_rects(self, tui) -> "tuple[pygame.Rect, list[pygame.Rect]]":
        """(rect do popup, [rect de cada botão na ordem de _PLAYER_POPUP_BUTTONS])
        — geometria única compartilhada entre draw e hit-test (nunca duplicar)."""
        px, py = tui.popup_screen_pos
        w, h = self._u(UI.TRADE_POPUP_W), self._u(UI.TRADE_POPUP_H)
        rect = pygame.Rect(int(px - w / 2), int(py), w, h)
        btns = []
        for i in range(len(self._PLAYER_POPUP_BUTTONS)):
            btns.append(pygame.Rect(rect.x + self._u(10),
                                    rect.y + self._u(30) + i * self._u(34),
                                    w - self._u(20), self._u(28)))
        return rect, btns

    def _draw_trade_popup(self, tui) -> None:
        rect, btns = self._player_popup_button_rects(tui)
        pygame.draw.rect(self.screen, (30, 25, 18), rect, border_radius=6)
        pygame.draw.rect(self.screen, (140, 110, 60), rect, 1, border_radius=6)
        name_s = self.font_sm.render(tui.popup_target_name, False, (220, 210, 190))
        self.screen.blit(name_s, (rect.x + self._u(8), rect.y + self._u(4)))
        for (label, bg, border, txt), btn in zip(self._PLAYER_POPUP_BUTTONS, btns):
            pygame.draw.rect(self.screen, bg, btn, border_radius=4)
            pygame.draw.rect(self.screen, border, btn, 1, border_radius=4)
            lbl = self.font_sm.render(label, False, txt)
            self.screen.blit(lbl, (btn.centerx - lbl.get_width() // 2,
                                   btn.centery - lbl.get_height() // 2))

    def _draw_trade_invite(self, tui) -> None:
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.TRADE_INVITE_W), self._u(UI.TRADE_INVITE_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        rect = pygame.Rect(x0, y0, w, h)
        pygame.draw.rect(self.screen, (32, 26, 18), rect, border_radius=8)
        pygame.draw.rect(self.screen, (170, 140, 70), rect, 2, border_radius=8)
        msg = f"{tui.pending_invite_from_name} quer negociar com você"
        msg_s = self.font_md.render(msg, False, (230, 220, 200))
        self.screen.blit(msg_s, (x0 + (w - msg_s.get_width()) // 2, y0 + self._u(30)))
        btn_w = (w - self._u(30)) // 2
        accept_r = pygame.Rect(x0 + self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        decline_r = pygame.Rect(x0 + w - btn_w - self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        pygame.draw.rect(self.screen, (50, 90, 50), accept_r, border_radius=5)
        pygame.draw.rect(self.screen, (90, 50, 50), decline_r, border_radius=5)
        a_s = self.font_sm.render("Aceitar", False, (220, 240, 220))
        d_s = self.font_sm.render("Recusar", False, (240, 220, 220))
        self.screen.blit(a_s, (accept_r.centerx - a_s.get_width() // 2, accept_r.centery - a_s.get_height() // 2))
        self.screen.blit(d_s, (decline_r.centerx - d_s.get_width() // 2, decline_r.centery - d_s.get_height() // 2))

    def _draw_item_slot(self, r, item, hovered: bool, offer_hint: str = "") -> None:
        pygame.draw.rect(self.screen, (40, 32, 20), r, border_radius=3)
        pygame.draw.rect(self.screen, (150, 120, 60) if hovered and item else (70, 55, 32), r, 1, border_radius=3)
        if not item:
            return
        ic = r.width - self._u(6)
        icon_r = pygame.Rect(r.x + self._u(3), r.y + self._u(3), ic, ic)
        icon_surf = ICONS.get(ICONS.item_key(item), ic)
        if icon_surf:
            self.screen.blit(icon_surf, icon_r)
        else:
            pygame.draw.rect(self.screen, _RARITY_COLORS.get(item.rarity, (120, 120, 120)), icon_r, border_radius=2)
        draw_stack_count(self.screen, item, r, self.font_xs)
        if hovered:
            lines = item_tooltip_lines(item)
            if offer_hint:
                lines.append((offer_hint, (140, 140, 140)))
            name_col = _RARITY_COLORS.get(item.rarity, (255, 220, 100))
            self._pending_tooltip = (pygame.mouse.get_pos()[0], pygame.mouse.get_pos()[1],
                                     item.name, lines, name_col)

    def _draw_trade_window(self, tui) -> None:
        x0, y0 = self._trade_window_origin()
        w, h = self._u(UI.TRADE_W), self._u(UI.TRADE_H)
        pad = self._u(UI.TRADE_PAD)
        rect = pygame.Rect(x0, y0, w, h)
        pygame.draw.rect(self.screen, (26, 22, 16), rect, border_radius=8)
        pygame.draw.rect(self.screen, (150, 120, 60), rect, 2, border_radius=8)

        title = self.font_md.render(f"Trade — negociando com {tui.other_name}", False, (230, 210, 160))
        self.screen.blit(title, (x0 + pad, y0 + self._u(8)))

        L = self._trade_layout(x0, y0)

        pygame.draw.rect(self.screen, (70, 30, 30), L["close"], border_radius=4)
        xs = self.font_sm.render("X", False, (255, 255, 255))
        self.screen.blit(xs, (L["close"].centerx - xs.get_width() // 2, L["close"].centery - xs.get_height() // 2))

        title_h = self._u(self._TITLE_H)

        # ── Cabeçalho: nome do personagem + meu saldo de gold (read-only) ──
        wal = self.world.get_component(self.player_entity, Wallet)
        name_s = self.font_sm.render(getattr(self, "_logged_char_name", "") or "Você", False, (220, 210, 190))
        self.screen.blit(name_s, (L["bag_x"], y0 + title_h + self._u(4)))
        gold_bal_s = self.font_sm.render(f"Seu gold: {wal.gold if wal else 0}", False, (200, 190, 170))
        self.screen.blit(gold_bal_s, (L["bag_x"], y0 + title_h + self._u(18)))

        # ── Bag (própria) ──
        from engine.components import Inventory as _InvDraw
        inv = self.world.get_component(self.player_entity, _InvDraw)
        mx, my = pygame.mouse.get_pos()
        for i, r in enumerate(L["bag"]):
            item = inv.items[i] if inv and i < len(inv.items) else None
            self._draw_item_slot(r, item, r.collidepoint(mx, my),
                                 "Clique direito p/ ofertar" if item else "")

        # ── Coluna "minha oferta": gold editável + 5 slots ──
        focused = getattr(self, "_trade_gold_focus", False)
        pygame.draw.rect(self.screen, (45, 36, 20), L["my_gold"], border_radius=4)
        pygame.draw.rect(self.screen, (200, 170, 80) if focused else (90, 70, 40), L["my_gold"], 1, border_radius=4)
        gold_text = (self._trade_gold_text if focused else str(tui.my_gold)) + ("|" if focused else "")
        lbl = self.font_xs.render(gold_text, False, (230, 210, 160))
        self.screen.blit(lbl, (L["my_gold"].centerx - lbl.get_width() // 2, L["my_gold"].centery - lbl.get_height() // 2))
        for i, r in enumerate(L["my_items"]):
            item = tui.my_offer[i] if i < len(tui.my_offer) else None
            self._draw_item_slot(r, item, r.collidepoint(mx, my),
                                 "Clique direito p/ retirar" if item else "")
        if tui.my_confirmed:
            self._draw_confirmed_overlay(L["my_gold"], L["my_items"])

        # ── Coluna "oferta do outro": gold read-only + 5 slots ──
        pygame.draw.rect(self.screen, (45, 36, 20), L["their_gold"], border_radius=4)
        pygame.draw.rect(self.screen, (90, 70, 40), L["their_gold"], 1, border_radius=4)
        lbl2 = self.font_xs.render(str(tui.their_gold), False, (200, 190, 170))
        self.screen.blit(lbl2, (L["their_gold"].centerx - lbl2.get_width() // 2, L["their_gold"].centery - lbl2.get_height() // 2))
        for i, r in enumerate(L["their_items"]):
            item = tui.their_offer[i] if i < len(tui.their_offer) else None
            self._draw_item_slot(r, item, r.collidepoint(mx, my))
        if tui.their_confirmed:
            self._draw_confirmed_overlay(L["their_gold"], L["their_items"])

        # ── Negociar / Cancelar ──
        pygame.draw.rect(self.screen, (50, 90, 50), L["negociar"], border_radius=5)
        pygame.draw.rect(self.screen, (90, 50, 50), L["cancelar"], border_radius=5)
        n_s = self.font_sm.render("Negociar", False, (220, 240, 220))
        c_s = self.font_sm.render("Cancelar", False, (240, 220, 220))
        self.screen.blit(n_s, (L["negociar"].centerx - n_s.get_width() // 2, L["negociar"].centery - n_s.get_height() // 2))
        self.screen.blit(c_s, (L["cancelar"].centerx - c_s.get_width() // 2, L["cancelar"].centery - c_s.get_height() // 2))

    def _draw_confirmed_overlay(self, gold_rect, item_rects: list) -> None:
        """Layer verde semi-transparente sobre a coluna de oferta confirmada."""
        top = min([gold_rect.y] + [r.y for r in item_rects])
        bottom = max(r.bottom for r in item_rects)
        left = min([gold_rect.x] + [r.x for r in item_rects])
        right = max([gold_rect.right] + [r.right for r in item_rects])
        overlay = pygame.Surface((right - left, bottom - top), pygame.SRCALPHA)
        overlay.fill((60, 220, 90, 70))
        self.screen.blit(overlay, (left, top))
