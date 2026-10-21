"""
trade_handlers.py — Mixin com o sistema de trade (player↔player): popup de
Shift+clique num player remoto, modal de convite (aceitar/recusar) e a
janela de troca em si (5 slots de cada lado + gold + confirmar/cancelar).
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self.player_entity, self.screen, self._net, self.font_*,
self._ui_events, self._trade_press (PressState do atlas), self._mm_safe_area()
e self._get_trade_ui() (já definido em game.py, roteia pro componente ECS
TradeUIState). Visual: atlas de UI (ui/atlas_window.py), ações no SOLTAR.

Autoridade é o servidor (server/trade_processor.py) — este arquivo só
reflete TRADE_STATE/TRADE_OPEN/TRADE_RESULT/TRADE_CANCELLED (ver
client/network_handlers.py) e envia os 8 tipos de mensagem C→S. Item
ofertado só sai da Inventory de verdade quando o servidor confirma via
TRADE_STATE — nunca otimista (mesmo racional do EQUIP_REJECTED).
"""
import math
import pygame

from engine.components import Inventory, RemoteControlled, Wallet
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.sound_manager import SOUNDS
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_helpers import item_tooltip_lines


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

    def _offer_trade_item(self, inv_index: int, quantity: int = 1) -> None:
        """`quantity` (11/08/2026, pedido do usuário — mesmo padrão de
        BUY_REQUEST): clique direito simples oferece 1 unidade; o modal
        de quantidade (Shift+clique direito num item empilhável, ver
        `_open_trade_qty_modal`) manda a quantidade escolhida."""
        if not self._net:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.TRADE_OFFER_ITEM,
                       {"inv_index": inv_index, "quantity": quantity})

    def _open_trade_qty_modal(self, inv_index: int, item) -> None:
        """Abre o modal de quantidade pra fatiar um item empilhável antes
        de ofertar — mesmo padrão visual de ShopSystem._qty_modal
        (pedido explícito do usuário: "o mesmo que já temos na compra de
        itens stackáveis"). `max_qty` é só o que o jogador TEM agora
        (diferente da loja, aqui não há limite de ouro)."""
        self._trade_qty_modal = {
            **aw.quantity_state(item.stack),
            # Só a posição NO MOMENTO da abertura — nunca usada pra
            # confirmar a oferta (ver _confirm_trade_qty_modal, que
            # reencontra a posição atual por referência de objeto
            # `item` em vez de confiar nisso, 11/10/2026).
            "inv_index": inv_index,
            "item":      item,
        }

    def _close_trade_qty_modal(self) -> None:
        self._trade_qty_modal = None

    def _confirm_trade_qty_modal(self) -> None:
        """Oferece o item do modal de quantidade — ponto único usado
        pelos 2 gatilhos de confirmação (Enter e botão OK, 11/10/2026,
        bug real relatado pelo usuário). `m["inv_index"]` (posição
        guardada no momento em que o modal ABRIU) pode ter ficado
        desatualizado: se QUALQUER outra oferta deste mesmo jogador for
        confirmada pelo servidor (TRADE_STATE, client/network_handlers.py::
        _handle_msg_trade_state) enquanto o modal ainda está aberto
        esperando o jogador escolher a quantidade, `Inventory.items` é
        mutado por índice (pop do item ofertado, deslocando todo mundo
        depois dele) — a posição antiga passa a apontar pra OUTRO item.
        Reencontra a posição ATUAL do MESMO objeto `item` (guardado por
        referência desde a abertura, nunca trocado) em vez de confiar no
        índice antigo — se o item já não existe mais na bolsa (foi
        totalmente consumido/ofertado por outro caminho nesse meio
        tempo), cancela em vez de ofertar algo errado."""
        m = self._trade_qty_modal
        if m is None:
            return
        from engine.components import Inventory as _InvQtyConfirm
        inv = self.world.get_component(self.player_entity, _InvQtyConfirm)
        fresh_index = None
        if inv is not None:
            for i, it in enumerate(inv.items):
                if it is m["item"]:
                    fresh_index = i
                    break
        self._close_trade_qty_modal()
        if fresh_index is None:
            return
        self._offer_trade_item(fresh_index, m["qty"])

    def _handle_trade_qty_modal_events(self, events: list) -> None:
        """Teclado do modal de quantidade (dígitos, ENTER, ESC) — o mouse é
        tratado no desenho (atlas_window.quantity_window, age no soltar)."""
        m = self._trade_qty_modal
        if m is None:
            return
        for event in events:
            res = aw.quantity_key(m, event)
            if res == "ok":
                self._confirm_trade_qty_modal()
                return
            if res == "cancel":
                self._close_trade_qty_modal()
                return

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
    # Atlas de UI (21/10/2026): as ações agem no SOLTAR, dentro do desenho
    # (_draw_trade_ui, Frame do atlas). Aqui só se decide se o clique é
    # "de trade" (pra não vazar pro mundo), pelos rects desenhados no frame
    # anterior (`_trade_rects`).

    def _handle_trade_click(self, event) -> bool:
        """Retorna True se o clique caiu numa UI de trade (consumido)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button not in (1, 3):
            return False
        if self._trade_qty_modal is not None:
            return True
        tui = self._get_trade_ui()
        if tui is None:
            return False
        if tui.pending_invite_from_eid != -1:
            return True
        rects = getattr(self, "_trade_rects", {})
        if tui.popup_target_eid != -1:
            pr = rects.get("popup")
            if pr is not None and pr.collidepoint(event.pos):
                return True
            tui.popup_target_eid = -1   # clique fora fecha o menu
            return False
        if tui.is_open:
            wr = rects.get("window")
            return wr is not None and wr.collidepoint(event.pos)
        return False

    # ── Render ─────────────────────────────────────────────────────────────

    def _draw_trade_ui(self) -> None:
        tui = self._get_trade_ui()
        self._trade_rects = {}
        if tui is None:
            return
        events = getattr(self, "_ui_events", [])
        if tui.pending_invite_from_eid != -1:
            self._draw_trade_invite(tui, events)
            return
        if tui.popup_target_eid != -1:
            self._draw_trade_popup(tui, events)
        if tui.is_open:
            modal = self._trade_qty_modal is not None
            self._draw_trade_window(tui, [] if modal else events, modal)
            if modal:
                self._draw_trade_qty_modal(events)

    # Menu de interação com player (clique direito em player amigável —
    # substituiu o shift+clique/"Trade", decisão do usuário 16/07/2026).
    _PLAYER_POPUP_BUTTONS = ("Negociar", "Duelar", "Seguir", "Convidar p/ Grupo")

    def _trade_hit(self, hid: str, f, x0, y0, x1, y1) -> None:
        self._trade_rects[hid] = pygame.Rect(f.px + round(x0 * aw.S), f.py + round(y0 * aw.S),
                                             round((x1 - x0) * aw.S), round((y1 - y0) * aw.S))

    def _draw_trade_popup(self, tui, events) -> None:
        """Painel escuro pequeno logo abaixo do player clicado: nome + 4
        botões empilhados."""
        T, S = aw.T, aw.S
        labels = self._PLAYER_POPUP_BUTTONS
        bw = aw.button_tiles(self.font_sm, list(labels), 4)
        tw = max(bw + 2, aw.tiles_for(self.font_sm, [tui.popup_target_name], 1) + 2)
        th = 2 + len(labels) + 1
        px, py = tui.popup_screen_pos
        area = pygame.Rect(int(px - tw * T * S / 2), int(py), tw * T * S, th * T * S)
        f = aw.Frame(self.screen, events, tw, th, area=area, press=self._trade_press,
                     font=self.font_sm, style="dark", overlay=False)
        f.text(tui.popup_target_name, tw * T / 2, T, (220, 210, 190))
        target_eid = tui.popup_target_eid
        col = (tw - bw) // 2
        ay0, ay1 = ui_atlas.button_art_y(False)
        fired = None
        for i, label in enumerate(labels):
            if f.button(f"popup:{i}", col, 2 + i, bw, label, tall=False):
                fired = i
            self._trade_hit(f"popup:{i}", f, col * T, (2 + i) * T + ay0, (col + bw) * T, (2 + i) * T + ay1)
        f.end()
        self._trade_rects["popup"] = pygame.Rect(f.px, f.py, f.pw, f.ph)
        if fired is None:
            return
        tui.popup_target_eid = -1
        SOUNDS.play_ui("button_click")
        if fired == 0:
            self._send_trade_request(target_eid)
        elif fired == 1:
            self._send_duel_request(target_eid)
        elif fired == 2:
            from engine.components import PlayerAutoMove as _PAMfl
            _pam = self.world.get_component(self.player_entity, _PAMfl)
            if _pam is not None:
                _pam.follow_eid    = target_eid   # eid LOCAL do proxy remoto
                _pam.ground_target = None
                _pam.path.clear()
                _pam.path_recalc_timer = 0.0
        elif fired == 3:
            self._send_party_invite_request(target_eid)

    def _draw_trade_invite(self, tui, events) -> None:
        fired = aw.prompt(self.screen, events, area=self._mm_safe_area(), press=self._trade_press,
                          font=self.font_md, message=f"{tui.pending_invite_from_name} quer negociar com você",
                          buttons=["Aceitar", "Recusar"], hits=self._trade_rects, hit_prefix="invite")
        if fired is None:
            return
        from shared.messages import MsgType
        if self._net:
            self._net.send(MsgType.TRADE_ACCEPT if fired == 0 else MsgType.TRADE_DECLINE, {})
        tui.clear_invite()
        SOUNDS.play_ui("button_click")

    # Janela de troca (atlas): mochila 5x4 à esquerda; à direita "sua
    # oferta" (rótulo + campo de ouro, 5 slots em linha) e embaixo "oferta
    # do outro" (só leitura); Negociar / Cancelar centralizados embaixo.
    # Slots de ícone 32x32 (aw.SLOT) — medidas em px RAW.
    _BAG_COLS = 5
    _BAG_ROWS = 4
    _OFFER_W = 4      # largura (tiles) do campo de ouro de cada oferta
    _N_OFFER = 5      # slots por oferta
    _BAG_X, _BAG_Y = 8, 32

    def _draw_item_tooltip(self, item, hint: str = "") -> None:
        lines = item_tooltip_lines(item)
        if hint:
            lines.append((hint, (140, 140, 140)))
        mx, my = pygame.mouse.get_pos()
        self._pending_tooltip = (mx, my, item.name, lines, _RARITY_COLORS.get(item.rarity, (255, 220, 100)))

    def _draw_trade_window(self, tui, events, modal_open: bool) -> None:
        T, S = aw.T, aw.S
        SL = aw.SLOT
        bag_w = aw.grid_px(self._BAG_COLS)
        div_x = self._BAG_X + bag_w + 8
        ox0 = div_x + 8                                   # coluna das ofertas (px RAW)
        offer_w = aw.grid_px(self._N_OFFER)
        bw = aw.button_tiles(self.font_md, ["Negociar", "Cancelar"], 3)
        title = f"Troca com {tui.other_name}"
        tw = max(math.ceil((ox0 + offer_w + 8) / T), 2 * bw + 3,
                 aw.tiles_for(self.font_md, [title], 1) + 3)
        th = 13
        # (rótulo y, campo de ouro ty, slots y) de cada oferta
        offers_y = ((40, 2, 50), (100, 5.75, 110))
        f = aw.Frame(self.screen, events, tw, th, area=self._mm_safe_area(),
                     press=None if modal_open else self._trade_press, font=self.font_md,
                     raw=ui_atlas.titled_dark_window_raw(tw, th), overlay=False)
        self._trade_rects["window"] = pygame.Rect(f.px, f.py, f.pw, f.ph)
        ip = aw.slot_icon_px()
        shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        f.text(title, (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("trade:close", tw - 1):
            self._close_trade()
        self._trade_hit("trade:close", f, (tw - 1) * T, 0, tw * T, T)

        hdr = (190, 170, 120)
        wal = self.world.get_component(self.player_entity, Wallet)
        f.text(f"Saldo: {wal.gold if wal else 0}g", self._BAG_X, 24, hdr, self.font_sm, anchor="midleft")
        f.text("Sua oferta", ox0, offers_y[0][0], hdr, self.font_sm, anchor="midleft")
        f.text(tui.other_name, ox0, offers_y[1][0], hdr, self.font_sm, anchor="midleft")
        f.divider_v(div_x, T, (th - 3) * T)
        f.divider_h((offers_y[0][2] + SL + offers_y[1][0] - 8) // 2, ox0, (tw - 1) * T)

        # ── Mochila (clique direito oferta; Shift+direito = quantidade) ──
        from engine.components import Inventory as _InvDraw
        inv = self.world.get_component(self.player_entity, _InvDraw)
        items = inv.items if inv else []
        cells = [None if it is None else (aw.item_icon(it, self.font_sm),
                                          it.stack if getattr(it, "max_stack", 1) > 1 else 0)
                 for it in items]
        hov_i, _ = f.grid("trade:bag", self._BAG_X, self._BAG_Y, self._BAG_COLS, self._BAG_ROWS, cells,
                          count_font=self.font_xs)
        for i in range(len(items)):
            c, r = i % self._BAG_COLS, i // self._BAG_COLS
            x, y = self._BAG_X + c * SL, self._BAG_Y + r * SL
            self._trade_hit(f"bag:{i}", f, x, y, x + SL, y + SL)
        item = items[hov_i] if 0 <= hov_i < len(items) else None
        if item is not None:
            stackable = getattr(item, "max_stack", 1) > 1 and item.stack > 1
            self._draw_item_tooltip(item, "Direito: 1  |  Shift+direito: escolher qtd" if stackable
                                    else "Clique direito p/ ofertar")
            if f.rreleased and not modal_open:
                if shift and getattr(item, "max_stack", 1) > 1:
                    self._open_trade_qty_modal(hov_i, item)
                else:
                    self._offer_trade_item(hov_i, quantity=1)

        # ── Ofertas: ouro + 5 slots por coluna ──
        focused = getattr(self, "_trade_gold_focus", False)
        gold_text = (self._trade_gold_text + "|") if focused else str(tui.my_gold)
        ay0, ay1 = ui_atlas.FIELD_ART_Y
        inset = ui_atlas.FIELD_ART_INSET_X
        gcol = (ox0 + offer_w) / T - self._OFFER_W            # campo de ouro à direita do rótulo
        my_gty, their_gty = offers_y[0][1], offers_y[1][1]
        over_gold = f.field_hover(gcol, my_gty, self._OFFER_W)
        if f.text_field("trade:gold", gcol, my_gty, self._OFFER_W, gold_text, active=focused):
            if not focused:
                self._trade_gold_focus = True
                self._trade_gold_text = str(tui.my_gold) if tui.my_gold else ""
        elif f.clicked and focused and not over_gold:
            # Clique em QUALQUER OUTRO lugar com o campo focado envia o valor
            # antes — sem isso, digitar e clicar direto em "Negociar"
            # descartava o texto (bug real: troca saía sem o ouro).
            self._confirm_trade_gold()
        self._trade_hit("trade:gold", f, gcol * T + inset, my_gty * T + ay0,
                        (gcol + self._OFFER_W) * T - inset, my_gty * T + ay1)
        ui_atlas.blit_text_field(f.raw, gcol, their_gty, self._OFFER_W)
        f.text(str(tui.their_gold), (gcol + self._OFFER_W / 2) * T, their_gty * T + (ay0 + ay1) / 2,
               (200, 190, 170))

        for (lab_y, _gty, sy), offer, mine in zip(offers_y, (tui.my_offer, tui.their_offer), (True, False)):
            for i in range(self._N_OFFER):
                it = offer[i] if i < len(offer) else None
                sx = ox0 + i * SL
                icon = aw.item_icon(it, self.font_sm)
                count = it.stack if it and getattr(it, "max_stack", 1) > 1 else 0
                hov, _ = f.slot(f"offer:{int(mine)}:{i}", sx, sy, icon, count, dark=True,
                                count_font=self.font_xs)
                if mine:
                    self._trade_hit(f"my:{i}", f, sx, sy, sx + SL, sy + SL)
                if hov and it is not None:
                    self._draw_item_tooltip(it, "Clique direito p/ retirar" if mine else "")
                    if mine and f.rreleased and not modal_open:
                        self._withdraw_trade_item(i)
            if (tui.my_confirmed if mine else tui.their_confirmed):
                ov = pygame.Surface(((offer_w + 4) * S, (sy + SL + 2 - (lab_y - 8)) * S), pygame.SRCALPHA)
                ov.fill((60, 220, 90, 70))
                f.overlays.append((ov, ox0 - 2, lab_y - 8))

        # ── Negociar / Cancelar (centralizados) ──
        bx = (tw - (2 * bw + 1)) / 2
        if f.button("trade:confirm", bx, th - 2, bw, "Negociar"):
            self._send_trade_confirm()
            SOUNDS.play_ui("button_click")
        if f.button("trade:cancel", bx + bw + 1, th - 2, bw, "Cancelar"):
            self._close_trade()
        ty0, ty1 = ui_atlas.button_art_y(True)
        self._trade_hit("trade:confirm", f, bx * T, (th - 2) * T + ty0, (bx + bw) * T, (th - 2) * T + ty1)
        self._trade_hit("trade:cancel", f, (bx + bw + 1) * T, (th - 2) * T + ty0, (bx + 2 * bw + 1) * T,
                        (th - 2) * T + ty1)
        f.end()

    def _draw_trade_qty_modal(self, events) -> None:
        """Modal de quantidade do kit (mesmo da loja — pedido do usuário),
        sem preço (troca não tem custo)."""
        m = self._trade_qty_modal
        item = m["item"]
        res = aw.quantity_window(self.screen, events, m, area=self._mm_safe_area(),
                                 press=self._trade_press, font=self.font_md, small_font=self.font_sm,
                                 title=item.name, info=f"Você tem {item.stack}",
                                 confirm_label=f"Ofertar {m['qty']}", hits=self._trade_rects)
        if res == "ok":
            self._confirm_trade_qty_modal()
        elif res == "cancel":
            self._close_trade_qty_modal()
