"""
client/battleground_handlers.py — Mixin do GameEngine: lado cliente do
battleground de teste (`/testbg`, server/debug_battleground.py, 02/08/2026).

Sem lógica de gameplay aqui: retorno ao mundo real reaproveita 100% o
ZONE_CHANGE existente (manual via "/testbg leave"/botão "Voltar", ou
timeout automático de 15s — ver server/debug_battleground.py::
DEBUG_BG_RESULT_AUTO_LEAVE_S) — este mixin só cuida do modal de fim de
partida (placar dos dois times: K/D/Farm/Gold/Dano, pedido do usuário —
mesmo espírito visual do modal de fim de partida da Arena,
client/arena_handlers.py::_draw_arena_result_modal, mas com colunas
extras e os dois times agrupados).
"""
import pygame

from ui.sound_manager import SOUNDS

_MODAL_W, _MODAL_H = 560, 420
_ROW_H = 24


class BattlegroundHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _bg_result(self):
        """None fora da tela de resultado; senão
        {"winner_faction": str, "players": [{eid,name,team,kills,deaths,
        farm,gold,damage,won}, ...]}."""
        return getattr(self, "_bg_result_val", None)

    # ── Handler de rede (dispatch em client/network_handlers.py) ─────────────

    def _handle_msg_bg_match_result(self, payload: dict) -> None:
        """Nexus derrubado — abre o modal de fim de partida (placar dos 2
        times + botão "Voltar"). NÃO teleporta sozinho — só quando o
        player sai de verdade ("/testbg leave", manual ou por timeout de
        15s), que chega como ZONE_CHANGE normal (client/network_handlers.py::
        _handle_msg_zone_change já limpa este modal, ver lá)."""
        self._bg_result_val = payload

    # ── Envio ao servidor ─────────────────────────────────────────────────

    def _send_bg_leave(self) -> None:
        """Botão "Voltar" do modal de resultado (BG_MATCH_RESULT — mesmo
        payload/modal pra debug E fila real, ver docstring do módulo).
        Precisa saber QUAL sistema me colocou na BG pra mandar a ação
        certa (04/08/2026, fila real — server/bg_queue_processor.py):
        se estou numa partida REAL da fila (`_bg_in_match`, client/
        bg_queue_handlers.py), manda BG_MATCH_LEAVE; senão, assume a
        instância de debug de sempre e manda o MESMO comando de chat que
        "/testbg leave" digitado manualmente já usa (zero protocolo
        novo, reaproveita server/debug_battleground.py::_leave, já
        testado)."""
        if not self._net:
            return
        from shared.messages import MsgType
        if self._bg_in_match:
            self._net.send(MsgType.BG_MATCH_LEAVE, {})
        else:
            self._net.send(MsgType.CHAT_SEND, {"text": "/testbg leave"})

    # ── Modal de fim de partida ───────────────────────────────────────────

    def _bg_result_modal_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(_MODAL_W), self._u(_MODAL_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        panel_rect = pygame.Rect(x0, y0, w, h)
        leave_rect = pygame.Rect(x0 + (w - self._u(180)) // 2, y0 + h - self._u(46),
                                 self._u(180), self._u(34))
        return panel_rect, leave_rect

    def _draw_bg_result_modal(self) -> None:
        result = self._bg_result
        if result is None:
            return
        panel_rect, leave_rect = self._bg_result_modal_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (24, 20, 30), panel_rect, border_radius=8)
        pygame.draw.rect(self.screen, (120, 100, 160), panel_rect, 2, border_radius=8)

        title_s = self.font_md.render("Fim de Partida — Battleground de Teste",
                                      False, (225, 210, 245))
        self.screen.blit(title_s, (panel_rect.centerx - title_s.get_width() // 2,
                                   panel_rect.y + self._u(14)))

        players = result.get("players", [])
        # Identifica "minha linha" pelo eid do servidor (self._my_eid), não
        # pelo nome — mesmo racional do modal de Arena (nomes não são
        # únicos entre contas).
        my_row = next((r for r in players if r.get("eid") == self._my_eid), None)
        if my_row is not None:
            banner = "Vitória!" if my_row.get("won") else "Derrota"
            banner_col = (120, 220, 120) if my_row.get("won") else (220, 110, 110)
            banner_s = self.font_md.render(banner, False, banner_col)
            self.screen.blit(banner_s, (panel_rect.centerx - banner_s.get_width() // 2,
                                        panel_rect.y + self._u(42)))

        col_x = {
            "name":   panel_rect.x + self._u(20),
            "kills":  panel_rect.x + self._u(230),
            "deaths": panel_rect.x + self._u(285),
            "farm":   panel_rect.x + self._u(345),
            "gold":   panel_rect.x + self._u(405),
            "damage": panel_rect.x + self._u(470),
        }
        header_y = panel_rect.y + self._u(78)
        hdr_col = (150, 140, 175)
        for key, label in (("name", "Jogador"), ("kills", "K"), ("deaths", "D"),
                           ("farm", "Farm"), ("gold", "Gold"), ("damage", "Dano")):
            self.screen.blit(self.font_xs.render(label, False, hdr_col), (col_x[key], header_y))

        # Times agrupados (confirmado com o usuário: tabela completa dos
        # dois times, não só o próprio player) — vencedor primeiro.
        team_a = sorted((r for r in players if r.get("team") == "a"),
                        key=lambda r: r.get("damage", 0), reverse=True)
        team_b = sorted((r for r in players if r.get("team") == "b"),
                        key=lambda r: r.get("damage", 0), reverse=True)
        winner_first = team_a if result.get("winner_faction") == "arena_time_a" else team_b
        loser_second = team_b if winner_first is team_a else team_a

        row_y = header_y + self._u(20)
        row_h = self._u(_ROW_H)
        for group in (winner_first, loser_second):
            for r in group:
                won = bool(r.get("won"))
                col = (170, 230, 170) if won else (230, 170, 170)
                self.screen.blit(self.font_sm.render(str(r.get("name", "?")), False, col),
                                 (col_x["name"], row_y))
                for key in ("kills", "deaths", "farm", "gold", "damage"):
                    self.screen.blit(
                        self.font_sm.render(str(r.get(key, 0)), False, col),
                        (col_x[key], row_y))
                row_y += row_h
            row_y += self._u(10)  # respiro entre os dois times

        hov = leave_rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (75, 55, 100) if hov else (55, 40, 75),
                         leave_rect, border_radius=5)
        pygame.draw.rect(self.screen, (160, 130, 200), leave_rect, 1, border_radius=5)
        btn_s = self.font_sm.render("Voltar", False, (230, 220, 245))
        self.screen.blit(btn_s, (leave_rect.centerx - btn_s.get_width() // 2,
                                 leave_rect.centery - btn_s.get_height() // 2))

    def _handle_bg_result_click(self, event) -> bool:
        """True sempre (modal bloqueante — consome qualquer clique
        enquanto a tela de resultado está aberta, mesmo padrão do modal
        de fim de partida da Arena)."""
        _, leave_rect = self._bg_result_modal_rects()
        if leave_rect.collidepoint(event.pos):
            self._send_bg_leave()
            SOUNDS.play_ui("button_click")
        return True

    def _handle_bg_click(self, event) -> bool:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self._bg_result is not None:
            return self._handle_bg_result_click(event)
        return False
