"""
client/arena_handlers.py — Mixin do GameEngine: lado cliente da Arena 2x2
(Fase G leva 1 — ver server/match_processor.py e ARQUITETURA_ONLINE.md).

Sem lógica de gameplay aqui: a troca de mapa pra dentro/fora da instância
já reusa 100% o fluxo existente de ZONE_CHANGE (client/network_handlers.py::
_handle_msg_zone_change → game.py::_do_transition, o mesmo usado por
transição de caverna) — este mixin só cuida do botão "Fila de Arena 2x2"
no frame de grupo, da janela de aceite "Partida encontrada!" + overlay de
contagem regressiva de preparo (pedido do usuário 21/07/2026 — ver
server/match_processor.py::_propose_match/request_arena_accept), do modal
de fim de partida (placar + "Sair da Arena", estilo WoW — pedido do
usuário 20/07/2026) e do feedback (log/aviso) de entrar na fila/começar/
terminar a partida. A contenção durante o preparo é FÍSICA (portão sólido
em ARENA_GATE_TILES, shared/constants.py — revisado 22/07/2026, pedido do
usuário: modelo WoW, sem freeze de ação/movimento) — este mixin só troca
a célula do portão localmente ao receber ARENA_GATE_OPEN
(_handle_msg_arena_gate_open), o overlay de contagem aqui é só visual.
"""
import pygame

from ui.ui_sizes import UI
from ui.sound_manager import SOUNDS


class ArenaHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _arena_in_queue(self) -> bool:
        return getattr(self, "_arena_in_queue_val", False)

    @property
    def _arena_result(self):
        """None fora da tela de resultado; senão lista de
        {"name","damage","won"} dos 4 players da partida decidida."""
        return getattr(self, "_arena_result_val", None)

    @property
    def _arena_in_match(self) -> bool:
        return getattr(self, "_arena_in_match_val", False)

    @property
    def _arena_pending_match(self):
        """None fora da janela de aceite; senão
        {"teammates","opponents","deadline"} — deadline em `time.time()`
        LOCAL (cliente fecha a janela sozinho ao vencer, sem precisar de
        mensagem do servidor, que expira o convite por conta própria via
        `accept_deadline`, ver server/match_processor.py::request_arena_accept)."""
        return getattr(self, "_arena_pending_match_val", None)

    @property
    def _arena_countdown_remaining(self) -> "float | None":
        """Segundos restantes de preparo (None = nenhuma contagem ativa).
        Puramente cosmético — quem contém de verdade é o portão físico
        (ARENA_GATE_TILES), aberto pelo servidor e espelhado aqui via
        ARENA_GATE_OPEN (_handle_msg_arena_gate_open)."""
        deadline = getattr(self, "_arena_countdown_deadline_val", None)
        if deadline is None:
            return None
        import time as _time_acr
        remaining = deadline - _time_acr.time()
        return remaining if remaining > 0 else None

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

    def _handle_msg_arena_match_found(self, payload: dict) -> None:
        """Fila pareou — janela "Partida encontrada!" com botão Aceitar,
        expira sozinha em ARENA_ACCEPT_WINDOW_S (shared/constants.py) sem
        precisar de resposta do servidor (que também expira por conta
        própria). A fila já terminou pro servidor neste momento (parou de
        contar como "na fila") — zera o flag local, senão o botão
        continuaria mostrando "Sair da Fila" durante a janela de aceite."""
        import time as _time_amf
        from shared.constants import ARENA_ACCEPT_WINDOW_S
        self._arena_in_queue_val = False
        self._arena_pending_match_val = {
            "teammates": payload.get("teammates", []),
            "opponents": payload.get("opponents", []),
            "deadline":  _time_amf.time() + ARENA_ACCEPT_WINDOW_S,
        }

    def _handle_msg_arena_countdown(self, payload: dict) -> None:
        import time as _time_acd
        self._arena_countdown_deadline_val = _time_acd.time() + payload.get("remaining", 0.0)

    def _handle_msg_arena_gate_open(self, payload: dict) -> None:
        """Portão físico da arena abriu (fim do preparo) — mesmo swap de
        tile aplicado no servidor (server/match_processor.py::
        _tick_arena_pending), pra este cliente parar de ver/colidir com o
        portão fechado. Mandado a cada um dos 4, inclusive quem aceitar
        DEPOIS do portão já ter aberto (ver request_arena_accept)."""
        from engine.components import Tilemap
        from engine.tileset import STONE_FLOOR
        from shared.constants import ARENA_GATE_TILES
        tilemap_comp = self.world.get_component(self.tilemap_entity, Tilemap)
        if tilemap_comp is None:
            return
        for gx, gy in ARENA_GATE_TILES:
            if 0 <= gy < len(tilemap_comp.tile_matrix) and 0 <= gx < len(tilemap_comp.tile_matrix[gy]):
                tilemap_comp.tile_matrix[gy][gx] = STONE_FLOOR

    def _handle_msg_arena_match_start(self, payload: dict) -> None:
        from ui.floating_text import WARN
        self._arena_in_queue_val = False
        self._arena_pending_match_val = None
        self._arena_in_match_val = True
        self._arena_opponents_server_val = set(payload.get("opponents", []))
        self._reset_local_arena_resources()
        WARN.add("Partida de Arena 2x2 começou! Use /forfeit ou /ff pra desistir.")

    def _handle_msg_arena_match_end(self, payload: dict) -> None:
        won = bool(payload.get("won", False))
        self._arena_in_match_val = False
        self._arena_opponents_server_val = set()
        self._arena_countdown_deadline_val = None
        # Fecha o modal de resultado (se estava aberto) — ARENA_MATCH_END
        # só chega depois que o servidor já restaurou de verdade este
        # player (clique em "Sair da Arena", /forfeit, ou timeout).
        self._arena_result_val = None
        self._reset_local_arena_resources()

    def _reset_local_arena_resources(self) -> None:
        """Espelha localmente (feedback instantâneo, sem esperar o
        próximo STATS_UPDATE) a restauração de HP/mana/concentração/
        cooldown que o servidor já fez de verdade — ao entrar E ao sair
        da arena (WorldServer._reset_combat_resources, mesmo gatilho,
        pedido do usuário 20/07/2026). Só mexe no player LOCAL — HP bar
        de players remotos já é 100% server-driven (ENTITY_SPAWN/
        STATS_UPDATE), não precisa de espelho aqui."""
        from engine.components import CombatStats as _CST_ar, CharacterStats as _Char_ar, PlayerSkills as _PS_ar
        cs = self.world.get_component(self.player_entity, _CST_ar)
        if cs:
            cs.current_hp = cs.max_hp
        char = self.world.get_component(self.player_entity, _Char_ar)
        if char:
            char.mana          = char.max_mana
            char.concentration = char.max_concentration
        ps = self.world.get_component(self.player_entity, _PS_ar)
        if ps:
            ps.gcd_timer = 0.0
            for sk in ps.skills:
                if sk is None:
                    continue
                sk.current_cooldown = 0.0
                if sk.max_charges > 0:
                    sk.charges      = sk.max_charges
                    sk.charge_timer = 0.0

    def _handle_msg_arena_match_result(self, payload: dict) -> None:
        """Partida decidida (time inteiro eliminado ou esvaziado) — abre
        o modal de fim de partida (placar + "Sair da Arena"). NÃO
        teleporta sozinho — isso só acontece quando o player sai de
        verdade (ARENA_MATCH_END, acima)."""
        self._arena_result_val = payload.get("results", [])

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

    def _send_arena_accept(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.ARENA_MATCH_ACCEPT, {})

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
        if self._arena_in_match or self._arena_pending_match is not None:
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
        if self._arena_pending_match is not None:
            return self._handle_arena_accept_click(event)
        if self._arena_result is not None:
            return self._handle_arena_result_click(event)
        rect = self._arena_queue_button_rect()
        if rect is None or not rect.collidepoint(event.pos):
            return False
        if self._arena_in_queue:
            self._send_arena_queue_leave()
        else:
            self._send_arena_queue_join()
        SOUNDS.play_ui("button_click")
        return True

    # ── Modal de fim de partida (placar + "Sair da Arena", estilo WoW) ───────

    def _arena_result_modal_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.ARENA_RESULT_W), self._u(UI.ARENA_RESULT_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        panel_rect = pygame.Rect(x0, y0, w, h)
        leave_rect = pygame.Rect(x0 + self._u(90), y0 + h - self._u(46),
                                 self._u(200), self._u(34))
        return panel_rect, leave_rect

    def _draw_arena_result_modal(self) -> None:
        results = self._arena_result
        if results is None:
            return
        panel_rect, leave_rect = self._arena_result_modal_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (24, 20, 30), panel_rect, border_radius=8)
        pygame.draw.rect(self.screen, (120, 100, 160), panel_rect, 2, border_radius=8)

        title_s = self.font_md.render("Fim de Partida — Arena 2x2", False, (225, 210, 245))
        self.screen.blit(title_s, (panel_rect.centerx - title_s.get_width() // 2,
                                   panel_rect.y + self._u(14)))

        # Identifica "minha linha" pelo eid do servidor (self._my_eid), não
        # pelo nome — nomes de personagem NÃO são únicos entre contas
        # (confirmado: duplicatas reais em data/game.db), então um match por
        # nome podia pegar a linha do adversário e mostrar Derrota pra quem
        # venceu (bug real reportado 21/07/2026).
        my_row = next((r for r in results if r.get("eid") == self._my_eid), None)
        if my_row is not None:
            banner = "Vitória!" if my_row.get("won") else "Derrota"
            banner_col = (120, 220, 120) if my_row.get("won") else (220, 110, 110)
            banner_s = self.font_md.render(banner, False, banner_col)
            self.screen.blit(banner_s, (panel_rect.centerx - banner_s.get_width() // 2,
                                        panel_rect.y + self._u(42)))

        header_y = panel_rect.y + self._u(78)
        hdr_col  = (150, 140, 175)
        self.screen.blit(self.font_xs.render("Jogador", False, hdr_col),
                         (panel_rect.x + self._u(20), header_y))
        self.screen.blit(self.font_xs.render("Dano", False, hdr_col),
                         (panel_rect.x + self._u(220), header_y))

        rows_sorted = sorted(results, key=lambda r: r.get("damage", 0), reverse=True)
        row_h = self._u(28)
        for i, r in enumerate(rows_sorted):
            ry = header_y + self._u(20) + i * row_h
            won = bool(r.get("won"))
            col = (170, 230, 170) if won else (230, 170, 170)
            name_s = self.font_sm.render(str(r.get("name", "?")), False, col)
            dmg_s  = self.font_sm.render(str(r.get("damage", 0)), False, col)
            self.screen.blit(name_s, (panel_rect.x + self._u(20), ry))
            self.screen.blit(dmg_s,  (panel_rect.x + self._u(220), ry))

        hov = leave_rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (75, 55, 100) if hov else (55, 40, 75),
                         leave_rect, border_radius=5)
        pygame.draw.rect(self.screen, (160, 130, 200), leave_rect, 1, border_radius=5)
        btn_s = self.font_sm.render("Sair da Arena", False, (230, 220, 245))
        self.screen.blit(btn_s, (leave_rect.centerx - btn_s.get_width() // 2,
                                 leave_rect.centery - btn_s.get_height() // 2))

    def _handle_arena_result_click(self, event) -> bool:
        """True sempre (modal bloqueante — consome qualquer clique
        enquanto a tela de resultado está aberta, mesmo padrão do
        convite de duelo)."""
        _, leave_rect = self._arena_result_modal_rects()
        if leave_rect.collidepoint(event.pos):
            self._send_arena_forfeit()
            SOUNDS.play_ui("button_click")
        return True

    # ── Modal "Partida encontrada!" (aceite, 21/07/2026) ─────────────────────

    def _arena_accept_modal_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.ARENA_ACCEPT_W), self._u(UI.ARENA_ACCEPT_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        panel_rect  = pygame.Rect(x0, y0, w, h)
        accept_rect = pygame.Rect(x0 + (w - self._u(160)) // 2, y0 + h - self._u(50),
                                  self._u(160), self._u(34))
        return panel_rect, accept_rect

    def _draw_arena_accept_modal(self) -> None:
        """Janela de aceite — fecha sozinha (`_arena_pending_match_val =
        None`) ao vencer o prazo local, sem precisar de nenhuma mensagem
        do servidor (que também expira o convite por conta própria)."""
        pending = self._arena_pending_match
        if pending is None:
            return
        import time as _time_dam
        remaining = pending["deadline"] - _time_dam.time()
        if remaining <= 0:
            self._arena_pending_match_val = None
            return

        panel_rect, accept_rect = self._arena_accept_modal_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (24, 20, 30), panel_rect, border_radius=8)
        pygame.draw.rect(self.screen, (120, 160, 100), panel_rect, 2, border_radius=8)

        title_s = self.font_md.render("Partida encontrada!", False, (210, 235, 200))
        self.screen.blit(title_s, (panel_rect.centerx - title_s.get_width() // 2,
                                   panel_rect.y + self._u(16)))

        sub_s = self.font_sm.render(f"Aceitar em {int(remaining) + 1}s",
                                    False, (200, 200, 200))
        self.screen.blit(sub_s, (panel_rect.centerx - sub_s.get_width() // 2,
                                 panel_rect.y + self._u(56)))

        hov = accept_rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (60, 100, 55) if hov else (45, 75, 42),
                         accept_rect, border_radius=5)
        pygame.draw.rect(self.screen, (130, 190, 120), accept_rect, 1, border_radius=5)
        btn_s = self.font_sm.render("Aceitar", False, (220, 245, 210))
        self.screen.blit(btn_s, (accept_rect.centerx - btn_s.get_width() // 2,
                                 accept_rect.centery - btn_s.get_height() // 2))

    def _handle_arena_accept_click(self, event) -> bool:
        """True sempre (modal bloqueante, mesmo padrão do resultado de
        fim de partida) — só o clique DENTRO do botão manda o aceite."""
        _, accept_rect = self._arena_accept_modal_rects()
        if accept_rect.collidepoint(event.pos):
            self._send_arena_accept()
            self._arena_pending_match_val = None
            SOUNDS.play_ui("button_click")
        return True

    # ── Overlay de contagem regressiva de preparo (21/07/2026) ───────────────

    def _draw_arena_countdown_overlay(self) -> None:
        """Número grande no meio da tela enquanto o preparo dura —
        puramente cosmético, o bloqueio de ação/movimento real já vem do
        servidor via CombatState.is_stunned (mesmo mecanismo do freeze de
        fim de partida)."""
        remaining = self._arena_countdown_remaining
        if remaining is None:
            return
        SW, SH = self.screen.get_size()
        num_s = self.font_lg.render(str(int(remaining) + 1), False, (255, 230, 140))
        self.screen.blit(num_s, (SW // 2 - num_s.get_width() // 2,
                                 SH // 3 - num_s.get_height() // 2))
        label_s = self.font_sm.render("Preparando...", False, (230, 220, 190))
        self.screen.blit(label_s, (SW // 2 - label_s.get_width() // 2,
                                   SH // 3 + num_s.get_height() // 2 + self._u(4)))
