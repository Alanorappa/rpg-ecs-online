"""
chat_handlers.py — Mixin com o chat de texto: janela com 3 abas (Local,
Mundial, Combate), scrollbar, quebra de linha, campo de digitação. Separado
de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self.player_entity, self.screen, self._net, self.font_sm,
self._u(), self._chat_active (property que roteia pro componente ECS
UIState — ver game.py) e self._chat_tab/_chat_local/_chat_world/
_chat_scroll/_chat_text (atributos simples, inicializados em
GameEngine.__init__).

Local e Mundial são canais de chat de verdade (servidor autoritativo —
server/session.py::_handle_chat); Combate não é chat, é o histórico de
combat_log.py (dano/cura/proc/loot já reportado por ~145 call-sites
espalhados pelo código, sem nenhuma mudança neles) — só leitura, sem campo
de digitação. Servidor sempre inclui o próprio remetente no broadcast (AOI
ou global), então o cliente nunca ecoa a própria mensagem antes da
confirmação.
"""
import pygame

from ui.ui_sizes import UI
from ui.ui_helpers import wrap_text
from ui.combat_log import LOG


_TABS = ("local", "world", "combat")
_TAB_LABELS = {"local": "Local", "world": "Mundial", "combat": "Combate"}


class ChatHandlers:

    _MAX_CHAT_LEN = 200  # mesmo teto do servidor (session.py::_handle_chat)
    _MARGIN_LEFT   = 6
    _MARGIN_BOTTOM = 86  # espaço livre acima da hotbar (subido +20px — encostava nela)

    # Hold-to-repeat do Backspace (delay inicial mais longo, depois repete rápido
    # — igual qualquer campo de texto de verdade). Não usa pygame.key.set_repeat()
    # global: isso geraria KEYDOWN repetido pra QUALQUER tecla segurada em
    # QUALQUER lugar do jogo, inclusive fora do chat (ex.: segurar "I" spammaria
    # abrir/fechar inventário), já que set_repeat afeta a fila de eventos inteira,
    # não só o campo focado. Polling isolado aqui, mesmo padrão de
    # pygame.key.get_pressed() que o WASD já usa (PlayerInputSystem).
    _BS_INITIAL_DELAY   = 0.40
    _BS_REPEAT_INTERVAL = 0.04

    # ── Envio / digitação ────────────────────────────────────────────────

    def _open_chat_input(self) -> None:
        if self._chat_tab == "combat":
            return  # aba de combate é só leitura, sem campo de digitação
        self._chat_active = True
        self._chat_text = ""
        pygame.key.start_text_input()

    def _close_chat_input(self) -> None:
        self._chat_active = False
        self._chat_text = ""
        pygame.key.stop_text_input()

    def _send_chat_message(self) -> None:
        text = self._chat_text.strip()
        # Comando de grupo ("/convidar Nome") — primeira vez que o chat
        # interpreta algo antes de mandar como texto normal (17/07/2026,
        # ver client/party_handlers.py::_try_handle_party_chat_command).
        # Resolve nome→eid local e despacha PARTY_INVITE; nunca vira
        # CHAT_SEND (comando consumido, achando o alvo ou não).
        if text and self._try_handle_party_chat_command(text):
            self._close_chat_input()
            return
        # "/forfeit" ou "/ff" — desiste da partida de Arena atual (20/07/2026,
        # ver client/arena_handlers.py::_try_handle_arena_chat_command).
        if text and self._try_handle_arena_chat_command(text):
            self._close_chat_input()
            return
        if text and self._net:
            from shared.messages import MsgType
            channel = "local" if self._chat_tab == "local" else "world"
            self._net.send(MsgType.CHAT_SEND, {"text": text, "channel": channel})
        self._close_chat_input()

    def _handle_chat_key(self, event) -> None:
        """Só teclas de CONTROLE (Enter/Esc/Backspace) — a digitação de texto
        em si vem de TEXTINPUT (_handle_chat_text_input), não daqui.

        Usar event.unicode do KEYDOWN pra digitar quebra teclado com tecla
        morta (ABNT2/US-Intl: "~" + "a" = "ã"): o KEYDOWN do "~" já chega com
        unicode="~" sozinho (SDL ainda não sabe que vem um "a" combinando
        depois), então o char errado era inserido ANTES do "ã" combinado
        chegar — bug real reportado pelo usuário, digitar "não" virava "n~ão".
        TEXTINPUT só dispara depois que o SDL já resolveu a composição
        completa (nunca manda a tecla morta sozinha), corrigindo isso de
        graça — é o mecanismo que o próprio SDL2/pygame recomenda pra
        entrada de texto internacional.
        """
        if event.type != pygame.KEYDOWN:
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._send_chat_message()
        elif event.key == pygame.K_ESCAPE:
            self._close_chat_input()
        elif event.key == pygame.K_BACKSPACE:
            self._chat_text = self._chat_text[:-1]
            self._chat_bs_hold_t    = 0.0
            self._chat_bs_repeating = False

    def _handle_chat_text_input(self, event) -> None:
        if event.type != pygame.TEXTINPUT or not self._chat_active:
            return
        if len(self._chat_text) < self._MAX_CHAT_LEN:
            self._chat_text += event.text

    def _update_chat_input(self, dt: float) -> None:
        """Hold-to-repeat do Backspace — chamado todo frame (game.py), não só
        em evento. Ver comentário de _BS_INITIAL_DELAY acima."""
        if not self._chat_active or not pygame.key.get_pressed()[pygame.K_BACKSPACE]:
            self._chat_bs_hold_t    = 0.0
            self._chat_bs_repeating = False
            return
        self._chat_bs_hold_t += dt
        threshold = self._BS_REPEAT_INTERVAL if self._chat_bs_repeating else self._BS_INITIAL_DELAY
        if self._chat_bs_hold_t >= threshold:
            self._chat_text        = self._chat_text[:-1]
            self._chat_bs_hold_t    = 0.0
            self._chat_bs_repeating = True

    # ── Geometria (compartilhada por desenho/clique/scroll) ─────────────

    def _chat_window_rect(self) -> pygame.Rect:
        SH = self.screen.get_height()
        w = self._u(UI.CHAT_W)
        h = self._u(UI.CHAT_H)
        x0 = self._u(self._MARGIN_LEFT) + UI.CHAT_OFFSET_X
        y0 = SH - h - self._u(self._MARGIN_BOTTOM) + UI.CHAT_OFFSET_Y
        return pygame.Rect(x0, y0, w, h)

    def _chat_layout(self) -> dict:
        win = self._chat_window_rect()
        pad = self._u(UI.CHAT_PAD)
        tab_h = self._u(UI.CHAT_TAB_H)
        input_h = self._u(UI.CHAT_INPUT_H)
        scroll_w = self._u(UI.CHAT_SCROLL_W)

        tab_w = win.width // len(_TABS)
        tabs = []
        for i, tab_id in enumerate(_TABS):
            tabs.append((pygame.Rect(win.x + i * tab_w, win.y, tab_w, tab_h), tab_id))

        has_input = self._chat_tab != "combat"
        msg_bottom = win.bottom - pad - (input_h + pad if has_input else 0)
        msg_area = pygame.Rect(win.x + pad, win.y + tab_h + pad,
                               win.width - pad * 2 - scroll_w - pad, msg_bottom - (win.y + tab_h + pad))

        scrollbar_track = pygame.Rect(msg_area.right + pad, msg_area.y, scroll_w, msg_area.height)

        input_box = None
        if has_input:
            input_box = pygame.Rect(win.x + pad, win.bottom - pad - input_h,
                                    win.width - pad * 2, input_h)

        return {"window": win, "tabs": tabs, "msg_area": msg_area,
                "scrollbar_track": scrollbar_track, "input_box": input_box}

    # ── Dados por aba ─────────────────────────────────────────────────

    def _chat_tab_raw_entries(self, tab: str) -> list:
        if tab == "local":
            return [(f"{s}: {t}", tuple(c)) for s, t, c in self._chat_local]
        elif tab == "world":
            return [(f"[Mundial] {s}: {t}", tuple(c)) for s, t, c in self._chat_world]
        else:
            return list(LOG.entries)

    def _chat_tab_lines(self, tab: str, max_px: int) -> list:
        """Linhas já quebradas (wrap) pra exibição — cacheado por aba,
        recalculado só quando o nº de entradas muda (novas mensagens),
        não a cada frame."""
        cache = getattr(self, "_chat_line_cache", None)
        if cache is None:
            cache = {}
            self._chat_line_cache = cache
        raw = self._chat_tab_raw_entries(tab)
        cached = cache.get(tab)
        if cached is not None and cached[0] == len(raw) and cached[2] == max_px:
            return cached[1]
        lines = []
        for text, color in raw:
            for line in wrap_text(text, self.font_sm, max_px):
                lines.append((line, color))
        cache[tab] = (len(raw), lines, max_px)
        return lines

    # ── Input (mouse) ────────────────────────────────────────────────────

    def _handle_chat_click(self, event) -> bool:
        """Retorna True se o clique foi consumido pela janela de chat."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        L = self._chat_layout()
        mx, my = event.pos
        if not L["window"].collidepoint(mx, my):
            return False

        for rect, tab_id in L["tabs"]:
            if rect.collidepoint(mx, my):
                self._chat_tab = tab_id
                if tab_id == "combat" and self._chat_active:
                    self._close_chat_input()
                return True

        if L["input_box"] is not None and L["input_box"].collidepoint(mx, my):
            self._open_chat_input()
            return True

        return True  # clique em qualquer outro ponto da janela não vaza pro mundo

    def _handle_chat_scroll(self, event) -> bool:
        """Retorna True se o mouse está sobre a janela (consome o scroll)."""
        if event.type != pygame.MOUSEWHEEL:
            return False
        L = self._chat_layout()
        mx, my = pygame.mouse.get_pos()
        if not L["window"].collidepoint(mx, my):
            return False
        tab = self._chat_tab
        line_h = self._u(UI.CHAT_LINE_H)
        visible = max(1, L["msg_area"].height // line_h)
        total = len(self._chat_tab_lines(tab, L["msg_area"].width))
        max_scroll = max(0, total - visible)
        # scroll=0 é o fundo (mensagens mais recentes); rodar a roda PRA CIMA
        # (event.y>0) deve revelar histórico mais ANTIGO — soma, não subtrai
        # (convenção oposta à de outras listas do projeto, ex. debug_handlers,
        # que são ancoradas no TOPO — aqui é ancorada no FUNDO, como qualquer chat).
        self._chat_scroll[tab] = max(0, min(max_scroll, self._chat_scroll[tab] + event.y))
        return True

    # ── Render ────────────────────────────────────────────────────────

    # Opacidade do FUNDO da janela (não do texto/borda/thumb — esses ficam
    # 100% opacos). Pedido do usuário: fundo opaco "ficava muito forte".
    _BG_ALPHA = round(255 * 0.25)

    def _draw_chat_log(self) -> None:
        surf = self.screen
        L = self._chat_layout()
        win = L["window"]

        # fill_surf: Surface SRCALPHA CACHEADA (ui_helpers.py) — evita
        # alocar+preencher uma Surface nova todo frame (lição de perf já
        # documentada no projeto: isso media 13ms+ de spike em outros painéis).
        from ui.ui_helpers import fill_surf
        surf.blit(fill_surf(win.size, (18, 18, 22, self._BG_ALPHA)), win.topleft)
        pygame.draw.rect(surf, (90, 80, 60), win, 1, border_radius=4)

        mx, my = pygame.mouse.get_pos()
        for rect, tab_id in L["tabs"]:
            active = tab_id == self._chat_tab
            hovered = rect.collidepoint(mx, my)
            bg = (45, 40, 30) if active else ((32, 30, 26) if hovered else (24, 22, 20))
            surf.blit(fill_surf(rect.size, (*bg, self._BG_ALPHA)), rect.topleft)
            fg = (230, 210, 160) if active else (160, 150, 140)
            label = self.font_sm.render(_TAB_LABELS[tab_id], False, fg)
            surf.blit(label, (rect.centerx - label.get_width() // 2,
                              rect.centery - label.get_height() // 2))
        pygame.draw.line(surf, (90, 80, 60), (win.x, win.y + L["tabs"][0][0].height),
                         (win.right, win.y + L["tabs"][0][0].height))

        msg_area = L["msg_area"]
        line_h = self._u(UI.CHAT_LINE_H)
        lines = self._chat_tab_lines(self._chat_tab, msg_area.width)
        visible = max(1, msg_area.height // line_h)
        total = len(lines)
        max_scroll = max(0, total - visible)
        scroll = min(self._chat_scroll[self._chat_tab], max_scroll)
        self._chat_scroll[self._chat_tab] = scroll

        # scroll=0 mostra as últimas `visible` linhas (segue mensagens novas
        # automaticamente); scroll>0 "congela" mais pra trás no histórico.
        end = total - scroll
        start = max(0, end - visible)
        shown = lines[start:end]
        for i, (text, color) in enumerate(shown):
            line_s = self.font_sm.render(text, False, color)
            surf.blit(line_s, (msg_area.x, msg_area.y + i * line_h))

        if total > visible:
            track = L["scrollbar_track"]
            surf.blit(fill_surf(track.size, (40, 36, 30, self._BG_ALPHA)), track.topleft)
            thumb_h = max(self._u(16), track.height * visible // total)
            thumb_y = track.y + (track.height - thumb_h) * (max_scroll - scroll) // max(1, max_scroll)
            # thumb continua 100% opaco (elemento de controle, não "fundo")
            pygame.draw.rect(surf, (130, 110, 70),
                             (track.x, thumb_y, track.width, thumb_h), border_radius=3)

        if L["input_box"] is not None:
            box = L["input_box"]
            focused = self._chat_active
            surf.blit(fill_surf(box.size, (24, 24, 28, self._BG_ALPHA)), box.topleft)
            pygame.draw.rect(surf, (200, 170, 80) if focused else (80, 70, 55), box, 1, border_radius=3)
            shown_text = self._chat_text + ("|" if focused else "")
            input_s = self.font_sm.render(shown_text, False, (225, 225, 215))
            pad_x = self._u(4)
            dest_y = box.centery - input_s.get_height() // 2
            avail_w = box.width - pad_x * 2
            # Texto maior que o campo: corta pela ESQUERDA (mostra sempre o
            # final, onde o cursor está) em vez de vazar pra fora da caixa —
            # mesmo comportamento de qualquer campo de texto padrão.
            if input_s.get_width() > avail_w:
                src = pygame.Rect(input_s.get_width() - avail_w, 0, avail_w, input_s.get_height())
                surf.blit(input_s, (box.x + pad_x, dest_y), src)
            else:
                surf.blit(input_s, (box.x + pad_x, dest_y))
