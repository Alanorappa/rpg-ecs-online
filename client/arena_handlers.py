"""
client/arena_handlers.py — Mixin do GameEngine: lado cliente da Arena 2x2
(Fase G leva 1 — ver server/match_processor.py e ARQUITETURA_ONLINE.md).

Sem lógica de gameplay aqui: a troca de mapa pra dentro/fora da instância
já reusa 100% o fluxo existente de ZONE_CHANGE (client/network_handlers.py::
_handle_msg_zone_change → game.py::_do_transition, o mesmo usado por
transição de caverna) — este mixin só cuida do botão "Fila de Arena 2x2"
no frame de grupo e do feedback (log/aviso) de entrar na fila/começar/
terminar a partida.
"""
import pygame

from ui.sound_manager import SOUNDS


class ArenaHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _arena_in_queue(self) -> bool:
        return getattr(self, "_arena_in_queue_val", False)

    @property
    def _arena_in_match(self) -> bool:
        return getattr(self, "_arena_in_match_val", False)

    @property
    def _arena_opponents_server(self) -> set:
        """server_eids dos oponentes da partida ativa (vazio fora de
        partida). Guardado como SERVER eid, não local — na hora de
        ARENA_MATCH_START o oponente ainda pode nem ter sido spawnado
        localmente (a troca de mapa/instância acontece junto), então
        resolver pra local eid ali (como o duelo faz, via
        self._remote_players) daria -1 sempre. A resolução acontece na
        hora do check em game.py::_client_pvp_context, via
        _local_eid_to_server_eid — nesse ponto o alvo já existe local
        (você está tentando atacar ele)."""
        return getattr(self, "_arena_opponents_server_val", set())

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_arena_queue_state(self, payload: dict) -> None:
        in_queue = bool(payload.get("in_queue", False))
        self._arena_in_queue_val = in_queue
        reason = payload.get("reason")
        if not in_queue and reason:
            from ui.floating_text import WARN
            _msgs = {
                "no_party":       "Precisa estar em um grupo pra entrar na fila.",
                "not_leader":     "Só o líder do grupo pode entrar na fila.",
                "wrong_size":     "A fila de Arena 2x2 precisa de um grupo com exatamente 2 membros.",
                "already_queued": "O grupo já está na fila.",
                "in_match":       "Alguém do grupo já está em uma partida.",
            }
            WARN.add(_msgs.get(reason, "Não foi possível entrar na fila de Arena."))

    def _handle_msg_arena_match_start(self, payload: dict) -> None:
        from ui.floating_text import WARN
        self._arena_in_queue_val = False
        self._arena_in_match_val = True
        self._arena_opponents_server_val = set(payload.get("opponents", []))
        WARN.add("Partida de Arena 2x2 começou! Use /forfeit ou /ff pra desistir.")

    def _handle_msg_arena_match_end(self, payload: dict) -> None:
        from ui.floating_text import WARN
        won = bool(payload.get("won", False))
        self._arena_in_match_val = False
        self._arena_opponents_server_val = set()
        WARN.add("Vitória na Arena!" if won else "Derrota na Arena.")

    # ── Envio ao servidor ──────────────────────────────────────────────────

    def _send_arena_queue_join(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.ARENA_QUEUE_JOIN, {})

    def _send_arena_queue_leave(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.ARENA_QUEUE_LEAVE, {})

    def _send_arena_forfeit(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.ARENA_FORFEIT, {})

    # ── Comando de chat "/forfeit" ou "/ff" ──────────────────────────────────

    def _try_handle_arena_chat_command(self, text: str) -> bool:
        """Chamado por client/chat_handlers.py::_send_chat_message ANTES
        de mandar como chat normal (mesmo padrão de
        PartyHandlers._try_handle_party_chat_command). True = era o
        comando (consumido), False = texto normal."""
        if text.strip().lower() not in ("/forfeit", "/ff"):
            return False
        from ui.floating_text import WARN
        if not self._arena_in_match:
            WARN.add("Você não está em uma partida de Arena.")
            return True
        self._send_arena_forfeit()
        return True

    # ── Botão "Fila de Arena 2x2" (logo abaixo do frame de grupo) ────────────

    def _arena_queue_button_rect(self):
        """None se não deve aparecer — só líder de um grupo com EXATAMENTE
        2 membros vê o botão (mesma regra que o servidor valida). Também
        some enquanto uma partida está rolando (bug real relatado pelo
        usuário 20/07/2026: o botão "Entrar na fila 2x2" continuava
        aparecendo dentro da própria arena)."""
        if self._arena_in_match:
            return None
        rows = self._party_frame_rects()
        if not rows:
            return None
        if self._party_leader_eid != self._my_eid or len(self._party_members) != 2:
            return None
        last_row = rows[-1][1]
        w, h = self._u(self._FRAME_W), self._u(24)
        x0   = self._u(self._FRAME_X0)
        y0   = last_row.bottom + self._u(6)
        return pygame.Rect(x0, y0, w, h)

    def _draw_arena_queue_button(self) -> None:
        rect = self._arena_queue_button_rect()
        if rect is None:
            return
        in_queue = self._arena_in_queue
        label = "Sair da Fila" if in_queue else "Fila de Arena 2x2"
        bg    = (90, 50, 50) if in_queue else (40, 70, 40)
        pygame.draw.rect(self.screen, bg, rect, border_radius=4)
        pygame.draw.rect(self.screen, (140, 140, 140), rect, 1, border_radius=4)
        s = self.font_xs.render(label, False, (230, 230, 230))
        self.screen.blit(s, (rect.centerx - s.get_width() // 2,
                             rect.centery - s.get_height() // 2))

    def _handle_arena_click(self, event) -> bool:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        rect = self._arena_queue_button_rect()
        if rect is None or not rect.collidepoint(event.pos):
            return False
        if self._arena_in_queue:
            self._send_arena_queue_leave()
        else:
            self._send_arena_queue_join()
        SOUNDS.play_ui("button_click")
        return True
