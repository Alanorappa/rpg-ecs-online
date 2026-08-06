"""
tests/test_bg_queue_client_ui.py — Cliente da fila REAL de Battleground
(04/08/2026, ver client/bg_queue_handlers.py). Fixture leve mesmo padrão
de tests/test_arena_client_ui.py — a linha de Battleground vive no MESMO
modal unificado da Arena (client/arena_handlers.py::_draw_arena_queue_modal),
por isso o fixture combina os 2 mixins.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from client.arena_handlers import ArenaHandlers
from client.bg_queue_handlers import BgQueueHandlers
from engine.world import World


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class _FakeCharStatsUI:
    def get_data(self):
        return None


class _BgFixture(ArenaHandlers, BgQueueHandlers):
    def __init__(self, my_eid=1):
        self._my_eid              = my_eid
        self._party_leader_eid_val = -1
        self._party_members_val    = []
        self.screen                = pygame.Surface((1280, 720))
        self._ui_scale             = 1.0
        self._u_scale_override     = None
        from ui.fonts import make as _font
        self.font_xs = _font(14)
        self.font_sm = _font(18)
        self.font_md = _font(22)
        self._net = _FakeNet()
        self._char_stats_ui = _FakeCharStatsUI()
        self._char_stats_requested = False
        # _reset_local_arena_resources (chamado por _handle_msg_bg_match_
        # start) precisa de world/player_entity de verdade — mesmo padrão
        # de outras fixtures leves deste projeto (tests/test_client_ui.py).
        self.world = World()
        self.player_entity = self.world.create_entity()

    def _u(self, px: int) -> int:
        s = self._u_scale_override if self._u_scale_override is not None else self._ui_scale
        return max(1, round(px * s))

    def _send_char_stats_request(self):
        self._char_stats_requested = True

    def _close_all_modals(self):
        """Fixture leve — só o suficiente pro que este arquivo testa
        (abrir/fechar o modal unificado de fila), não o GameEngine
        inteiro."""
        self._arena_modal_open_val = False

    @property
    def _party_leader_eid(self):
        return self._party_leader_eid_val

    @property
    def _party_members(self):
        return self._party_members_val


# ── Handlers de rede — estado local ───────────────────────────────────────

def test_queue_state_marca_in_queue():
    fx = _BgFixture()
    fx._handle_msg_bg_queue_state({"in_queue": True})
    assert fx._bg_in_queue is True


def test_queue_state_recusado_mostra_aviso_e_nao_marca(monkeypatch):
    fx = _BgFixture()
    warned = []
    import ui.floating_text as ft
    monkeypatch.setattr(ft.WARN, "add", lambda msg: warned.append(msg))
    fx._handle_msg_bg_queue_state({"in_queue": False, "reason": "not_leader"})
    assert fx._bg_in_queue is False
    assert warned


def test_match_found_fecha_modal_zera_fila_guarda_pending():
    fx = _BgFixture()
    fx._arena_modal_open_val = True
    fx._bg_in_queue_val = True
    fx._handle_msg_bg_match_found({"team_size": 3, "teammates": [2, 3], "opponents": [9, 10, 11]})
    assert fx._bg_in_queue is False
    assert fx._arena_modal_open is False
    assert fx._bg_pending_match["team_size"] == 3
    assert fx._bg_pending_match["opponents"] == [9, 10, 11]


def test_match_start_limpa_pending_marca_in_match_guarda_oponentes():
    fx = _BgFixture()
    fx._bg_pending_match_val = {"team_size": 1, "teammates": [], "opponents": [5], "deadline": 0}
    fx._handle_msg_bg_match_start({"team_size": 1, "teammates": [], "opponents": [5]})
    assert fx._bg_pending_match is None
    assert fx._bg_in_match is True
    assert fx._bg_opponents_server == {5}


# ── Envio ao servidor ──────────────────────────────────────────────────────

def test_send_bg_queue_join_sem_payload_de_modo():
    fx = _BgFixture()
    fx._send_bg_queue_join()
    assert fx._net.sent == [("bg_queue_join", {})]


def test_send_bg_queue_leave():
    fx = _BgFixture()
    fx._send_bg_queue_leave()
    assert fx._net.sent == [("bg_queue_leave", {})]


def test_send_bg_match_accept():
    fx = _BgFixture()
    fx._send_bg_match_accept()
    assert fx._net.sent == [("bg_match_accept", {})]


# ── Linha de Battleground dentro do modal unificado ───────────────────────

def test_clique_entrar_na_linha_de_bg_manda_join_e_fecha_modal():
    fx = _BgFixture()
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=bg_btn_rect.center)
    consumed = fx._handle_arena_modal_click(event)
    assert consumed is True
    assert fx._arena_modal_open is False
    assert fx._net.sent == [("bg_queue_join", {})]


def test_clique_sair_na_linha_de_bg_manda_leave():
    fx = _BgFixture()
    fx._bg_in_queue_val = True
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=bg_btn_rect.center)
    fx._handle_arena_modal_click(event)
    assert fx._net.sent == [("bg_queue_leave", {})]


def test_linha_de_bg_nao_interfere_no_clique_das_linhas_de_arena():
    fx = _BgFixture(my_eid=1)
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    _, _, _, btn_rect = next(r for r in rows if r[0] == "1v1")
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=btn_rect.center)
    fx._handle_arena_modal_click(event)
    assert fx._net.sent == [("arena_queue_join", {"mode": "1v1"})]


# ── Modal de aceite ("Partida encontrada!") ───────────────────────────────

def test_aceite_clique_manda_accept_e_fecha_pending():
    fx = _BgFixture()
    import time
    fx._bg_pending_match_val = {"team_size": 2, "teammates": [2], "opponents": [7, 8],
                                "deadline": time.time() + 15.0}
    _, accept_rect = fx._bg_accept_modal_rects()
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=accept_rect.center)
    consumed = fx._handle_bg_accept_click(event)
    assert consumed is True
    assert fx._net.sent == [("bg_match_accept", {})]
    assert fx._bg_pending_match is None


def test_aceite_expirado_localmente_limpa_sozinho_ao_desenhar():
    fx = _BgFixture()
    import time
    fx._bg_pending_match_val = {"team_size": 1, "teammates": [], "opponents": [7],
                                "deadline": time.time() - 1.0}
    fx._draw_bg_accept_modal()
    assert fx._bg_pending_match is None


# ── Abertura via F1/"/bgqueue" (mesma ação, _open_bg_queue_modal) ─────────

def test_abrir_modal_toggla_quando_fechado():
    fx = _BgFixture()
    fx._char_stats_requested = False
    fx._open_bg_queue_modal()
    assert fx._arena_modal_open is True
    assert fx._char_stats_requested is True


def test_abrir_modal_fecha_quando_ja_aberto():
    fx = _BgFixture()
    fx._arena_modal_open_val = True
    fx._open_bg_queue_modal()
    assert fx._arena_modal_open is False


def test_nao_abre_modal_se_ja_em_partida_bg():
    fx = _BgFixture()
    fx._bg_in_match_val = True
    fx._open_bg_queue_modal()
    assert fx._arena_modal_open is False


def test_nao_abre_modal_se_pending_de_arena():
    fx = _BgFixture()
    fx._arena_pending_match_val = {"mode": "1v1", "teammates": [], "opponents": [], "deadline": 0}
    fx._open_bg_queue_modal()
    assert fx._arena_modal_open is False


def test_chat_command_bgqueue_abre_o_modal():
    fx = _BgFixture()
    consumed = fx._try_handle_bg_chat_command("/bgqueue")
    assert consumed is True
    assert fx._arena_modal_open is True


def test_chat_command_bgqueue_case_insensitive_e_com_espacos():
    fx = _BgFixture()
    consumed = fx._try_handle_bg_chat_command("  /BgQueue  ")
    assert consumed is True


def test_texto_normal_nao_e_consumido_como_comando():
    fx = _BgFixture()
    consumed = fx._try_handle_bg_chat_command("oi gente")
    assert consumed is False
    assert fx._arena_modal_open is False
