"""
client/bg_queue_handlers.py — Mixin do GameEngine: lado cliente da fila
REAL de matchmaking da BG estilo MOBA (04/08/2026, pedido do usuário —
server/bg_queue_processor.py).

Revisado 10/08/2026 (achado real do usuário — fila sem escolha de
tamanho colapsava sempre pro menor par disponível): 3 filas de tamanho
FIXO (BG_MODE_LIST abaixo — 2v2/3v3/5v5), mesmo padrão de
client/arena_handlers.py::ARENA_MODE_LIST.

Sem lógica de gameplay aqui, mesmo espírito de client/arena_handlers.py:
troca de mapa reusa 100% ZONE_CHANGE existente; este mixin cuida só do
estado de fila (entrar/sair, agora COM escolha de modo/tamanho), da
janela de aceite "Partida encontrada!" (modal PRÓPRIO — formato de
payload diferente do de Arena: `team_size` vem do tamanho real do modo,
`mode` já bate 1 pra 1 com `team_size` desde a revisão) e do feedback de
início de partida. As linhas de Battleground dentro do modal unificado
de fila são desenhadas por client/arena_handlers.py
(_draw_arena_queue_modal, mesmo container e mesmo padrão de linha por
modo que ARENA_MODE_LIST já usa — só o ESTADO/ENVIO é deste mixin). O
modal de RESULTADO (Nexus derrubado) já existe pronto em
client/battleground_handlers.py (BG_MATCH_RESULT, reaproveitado tal
qual — não recriado aqui).
"""
import pygame

from ui.ui_sizes import UI
from ui.sound_manager import SOUNDS

# Modos de fila da BG (10/08/2026 — mesmo padrão de ARENA_MODE_LIST em
# client/arena_handlers.py e BG_MODES em server/bg_queue_processor.py).
# LISTA, não 3 botões hardcoded — 1 entrada nova aqui cobre um modo
# futuro sem redesenhar o modal.
BG_MODE_LIST = [
    ("2v2", "Battleground 2x2"),
    ("3v3", "Battleground 3x3"),
    ("5v5", "Battleground 5x5"),
]
BG_MODE_LABELS    = dict(BG_MODE_LIST)
BG_MODE_TEAM_SIZE = {"2v2": 2, "3v3": 3, "5v5": 5}


class BgQueueHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _bg_in_queue_mode(self) -> "str | None":
        """None fora de qualquer fila; senão o mode_id da fila atual (só
        se pode estar em UMA fila de modo por vez, ver server/
        bg_queue_processor.py::request_bg_queue_join)."""
        return getattr(self, "_bg_in_queue_mode_val", None)

    @property
    def _bg_pending_match(self):
        """None fora da janela de aceite; senão {"mode","team_size",
        "teammates","opponents","deadline"} — deadline em `time.time()`
        LOCAL (mesmo padrão de ArenaHandlers._arena_pending_match: fecha
        sozinho ao vencer, sem esperar mensagem do servidor, que também
        expira o convite por conta própria)."""
        return getattr(self, "_bg_pending_match_val", None)

    @property
    def _bg_in_match(self) -> bool:
        return getattr(self, "_bg_in_match_val", False)

    @property
    def _bg_opponents_server(self) -> set:
        """server_eids dos oponentes da partida REAL ativa — mesmo papel
        de ArenaHandlers._arena_opponents_server (game.py::
        _client_pvp_context lê os dois, compostos, pra liberar clique-
        direito/SPACE/skill contra o player inimigo: o cliente nunca
        recebe Faction de outro player via ENTITY_SPAWN, só mob manda
        "faction")."""
        return getattr(self, "_bg_opponents_server_val", set())

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_bg_queue_state(self, payload: dict) -> None:
        in_queue = bool(payload.get("in_queue", False))
        self._bg_in_queue_mode_val = payload.get("mode") if in_queue else None
        reason = payload.get("reason")
        if not in_queue and reason:
            from ui.floating_text import WARN
            _msgs = {
                "invalid_mode":   "Modo de fila inválido.",
                "no_party":       "Precisa estar em um grupo pra entrar com o grupo (ou saia dele pra entrar sozinho).",
                "not_leader":     "Só o líder do grupo pode enfileirar.",
                "wrong_size":     "Grupo grande demais pra esse tamanho de Battleground.",
                "already_queued": "Você já está em uma fila de Battleground.",
                "in_match":       "Alguém do grupo já está em uma partida.",
            }
            WARN.add(_msgs.get(reason, "Não foi possível entrar na fila da Battleground."))

    def _handle_msg_bg_match_found(self, payload: dict) -> None:
        import time as _time_bmf
        from shared.constants import ARENA_ACCEPT_WINDOW_S
        self._bg_in_queue_mode_val = None
        self._arena_modal_open_val = False
        self._bg_pending_match_val = {
            "mode":      payload.get("mode", "5v5"),
            "team_size": payload.get("team_size", 5),
            "teammates": payload.get("teammates", []),
            "opponents": payload.get("opponents", []),
            "deadline":  _time_bmf.time() + ARENA_ACCEPT_WINDOW_S,
        }

    def _handle_msg_bg_match_start(self, payload: dict) -> None:
        from ui.floating_text import WARN
        self._bg_pending_match_val   = None
        self._bg_in_match_val        = True
        self._bg_opponents_server_val = set(payload.get("opponents", []))
        self._reset_local_arena_resources()
        team_size = payload.get("team_size", 1)
        WARN.add(f"Partida {team_size}x{team_size} começou!")

    # ── Envio ao servidor ──────────────────────────────────────────────────

    def _send_bg_queue_join(self, mode_id: str) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.BG_QUEUE_JOIN, {"mode": mode_id})

    def _send_bg_queue_leave(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.BG_QUEUE_LEAVE, {})

    def _send_bg_match_accept(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.BG_MATCH_ACCEPT, {})

    # ── Comando de chat "/bgqueue" (abre o MESMO modal do F1) ────────────────

    def _try_handle_bg_chat_command(self, text: str) -> bool:
        """Chamado por client/chat_handlers.py::_send_chat_message ANTES
        de mandar como chat normal (mesmo padrão de
        PartyHandlers._try_handle_party_chat_command). True = era o
        comando (consumido), False = texto normal. Mesma ação do atalho
        F1 (game.py) — abre o modal unificado de fila, nunca entra
        direto: o player ainda escolhe o modo lá dentro."""
        if text.strip().lower() != "/bgqueue":
            return False
        self._open_bg_queue_modal()
        return True

    def _open_bg_queue_modal(self) -> None:
        """Mesma ação do clique no antigo botão "Fila de Arena" (agora
        removido) — abre o modal unificado (client/arena_handlers.py::
        _draw_arena_queue_modal), que já lista Arena 1v1/2v2/3v3 + as
        linhas de Battleground 2v2/3v3/5v5. Toggle: fecha se já estiver
        aberto."""
        if self._arena_pending_match is not None or self._arena_in_match \
                or self._bg_pending_match is not None or self._bg_in_match:
            return  # já ocupado — não faz sentido abrir a fila
        already_open = self._arena_modal_open
        self._close_all_modals()
        if not already_open:
            self._arena_modal_open_val = True
            self._send_char_stats_request()

    # ── Elegibilidade de modo (feedback visual — servidor SEMPRE revalida) ───

    def _bg_mode_eligible(self, mode_id: str) -> "tuple[bool, str]":
        """(elegível, motivo_se_não) — mesma validação que o servidor faz
        em request_bg_queue_join, checada aqui só pra feedback visual (o
        servidor SEMPRE revalida; nunca confiar só nisto). Diferente da
        Arena: grupo MENOR que o time do modo é elegível (preenchido por
        outros na fila), só maior que o time é recusado."""
        team_size = BG_MODE_TEAM_SIZE[mode_id]
        if self._party_leader_eid == -1:
            return True, ""  # sozinho, sem grupo — sempre elegível
        if self._party_leader_eid != self._my_eid:
            return False, "Só o líder do grupo pode enfileirar"
        if len(self._party_members) > team_size:
            return False, f"Grupo grande demais (máx. {team_size})"
        return True, ""

    # ── Modal "Partida encontrada!" (aceite) ──────────────────────────────

    def _bg_accept_modal_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.ARENA_ACCEPT_W), self._u(UI.ARENA_ACCEPT_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        panel_rect  = pygame.Rect(x0, y0, w, h)
        accept_rect = pygame.Rect(x0 + (w - self._u(160)) // 2, y0 + h - self._u(50),
                                  self._u(160), self._u(34))
        return panel_rect, accept_rect

    def _draw_bg_accept_modal(self) -> None:
        """Espelha ArenaHandlers._draw_arena_accept_modal — payload
        diferente (team_size/mode do bracket fixo escolhido pelo
        player), por isso um modal próprio em vez de reaproveitar o da
        Arena."""
        pending = self._bg_pending_match
        if pending is None:
            return
        import time as _time_dbm
        remaining = pending["deadline"] - _time_dbm.time()
        if remaining <= 0:
            self._bg_pending_match_val = None
            return

        panel_rect, accept_rect = self._bg_accept_modal_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (24, 20, 30), panel_rect, border_radius=8)
        pygame.draw.rect(self.screen, (120, 160, 100), panel_rect, 2, border_radius=8)

        ts = pending.get("team_size", 1)
        title_s = self.font_md.render(f"Partida encontrada! — Battleground {ts}x{ts}",
                                      False, (210, 235, 200))
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

    def _handle_bg_accept_click(self, event) -> bool:
        """True sempre (modal bloqueante, mesmo padrão do aceite de
        Arena) — só o clique DENTRO do botão manda o aceite."""
        _, accept_rect = self._bg_accept_modal_rects()
        if accept_rect.collidepoint(event.pos):
            self._send_bg_match_accept()
            self._bg_pending_match_val = None
            SOUNDS.play_ui("button_click")
        return True

    def _handle_bg_queue_click(self, event) -> bool:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self._bg_pending_match is not None:
            return self._handle_bg_accept_click(event)
        return False
