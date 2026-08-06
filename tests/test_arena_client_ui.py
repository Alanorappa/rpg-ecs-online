"""
tests/test_arena_client_ui.py — Cliente da Arena 1x1/2x2/3x3 (Fase H,
23/07/2026): modal unificado de fila (lista, não 3 botões hardcoded),
elegibilidade por modo, e handlers de rede que alimentam o estado local.
Ver client/arena_handlers.py.

Fixture leve (mesmo padrão de _PvpCtxFixture em test_client_ui.py) — não
precisa de um GameEngine inteiro, só dos atributos que ArenaHandlers de
fato usa.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from client.arena_handlers import ArenaHandlers, ARENA_MODE_LIST


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class _FakeCharStatsUI:
    def __init__(self, data=None):
        self._data = data

    def get_data(self):
        return self._data


class _ArenaFixture(ArenaHandlers):
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

    def _u(self, px: int) -> int:
        s = self._u_scale_override if self._u_scale_override is not None else self._ui_scale
        return max(1, round(px * s))

    @property
    def _party_leader_eid(self):
        return self._party_leader_eid_val

    @property
    def _party_members(self):
        return self._party_members_val


# ── Elegibilidade por modo ────────────────────────────────────────────────

def test_1v1_sempre_elegivel_sem_grupo():
    fx = _ArenaFixture()
    ok, reason = fx._arena_mode_eligible("1v1")
    assert ok is True
    assert reason == ""


def test_2v2_sem_grupo_e_recusado_no_party():
    fx = _ArenaFixture()
    ok, reason = fx._arena_mode_eligible("2v2")
    assert ok is False
    assert "líder" in reason or "grupo" in reason


def test_2v2_grupo_de_2_como_lider_e_elegivel():
    fx = _ArenaFixture(my_eid=1)
    fx._party_leader_eid_val = 1
    fx._party_members_val = [{"eid": 1}, {"eid": 2}]
    ok, _ = fx._arena_mode_eligible("2v2")
    assert ok is True


def test_2v2_grupo_de_3_e_recusado_tamanho_errado():
    fx = _ArenaFixture(my_eid=1)
    fx._party_leader_eid_val = 1
    fx._party_members_val = [{"eid": 1}, {"eid": 2}, {"eid": 3}]
    ok, reason = fx._arena_mode_eligible("2v2")
    assert ok is False
    assert "2" in reason


def test_3v3_nao_lider_e_recusado():
    fx = _ArenaFixture(my_eid=2)
    fx._party_leader_eid_val = 1   # outro é o líder
    fx._party_members_val = [{"eid": 1}, {"eid": 2}, {"eid": 3}]
    ok, reason = fx._arena_mode_eligible("3v3")
    assert ok is False


def test_3v3_lider_com_trio_e_elegivel():
    fx = _ArenaFixture(my_eid=1)
    fx._party_leader_eid_val = 1
    fx._party_members_val = [{"eid": 1}, {"eid": 2}, {"eid": 3}]
    ok, _ = fx._arena_mode_eligible("3v3")
    assert ok is True


# ── ARENA_MODE_LIST — extensibilidade (Fase H) ───────────────────────────

def test_arena_mode_list_tem_os_3_modos_na_ordem_certa():
    ids = [mid for mid, _ in ARENA_MODE_LIST]
    assert ids == ["1v1", "2v2", "3v3"]


# ── Handlers de rede — estado local ───────────────────────────────────────

def test_queue_state_guarda_modo_quando_entra_na_fila():
    fx = _ArenaFixture()
    fx._handle_msg_arena_queue_state({"in_queue": True, "mode": "3v3"})
    assert fx._arena_in_queue_mode == "3v3"


def test_queue_state_limpa_modo_quando_sai_da_fila():
    fx = _ArenaFixture()
    fx._handle_msg_arena_queue_state({"in_queue": True, "mode": "1v1"})
    fx._handle_msg_arena_queue_state({"in_queue": False})
    assert fx._arena_in_queue_mode is None


def test_match_found_fecha_modal_e_zera_fila_guarda_modo_do_pending():
    fx = _ArenaFixture()
    fx._arena_modal_open_val = True
    fx._handle_msg_arena_queue_state({"in_queue": True, "mode": "1v1"})
    fx._handle_msg_arena_match_found({"mode": "1v1", "teammates": [], "opponents": [9]})
    assert fx._arena_in_queue_mode is None
    assert fx._arena_modal_open is False
    assert fx._arena_pending_match_val["mode"] == "1v1"


def test_match_result_guarda_mode_pro_titulo_do_modal():
    fx = _ArenaFixture()
    fx._handle_msg_arena_match_result({"mode": "3v3", "results": [{"eid": 1, "name": "A", "damage": 10, "won": True}]})
    assert fx._arena_result_mode == "3v3"
    assert fx._arena_result == [{"eid": 1, "name": "A", "damage": 10, "won": True}]


def test_match_result_sem_campo_mode_usa_default_2v2_compat():
    fx = _ArenaFixture()
    fx._handle_msg_arena_match_result({"results": []})
    assert fx._arena_result_mode == "2v2"


# ── Envio ao servidor ──────────────────────────────────────────────────────

def test_send_arena_queue_join_manda_o_modo_no_payload():
    fx = _ArenaFixture()
    fx._send_arena_queue_join("3v3")
    assert fx._net.sent == [("arena_queue_join", {"mode": "3v3"})] or \
           fx._net.sent[0][1] == {"mode": "3v3"}


# ── Clique no modal ──────────────────────────────────────────────────────

def test_clique_entrar_manda_join_e_fecha_o_modal():
    fx = _ArenaFixture(my_eid=1)
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    _, _, _, btn_rect = next(r for r in rows if r[0] == "1v1")
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=btn_rect.center)
    consumed = fx._handle_arena_modal_click(event)
    assert consumed is True
    assert fx._arena_modal_open is False
    assert any(p.get("mode") == "1v1" for _, p in fx._net.sent)


def test_clique_entrar_em_modo_inelegivel_nao_manda_nada():
    fx = _ArenaFixture(my_eid=1)   # sem grupo — 2v2 inelegível
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    _, _, _, btn_rect = next(r for r in rows if r[0] == "2v2")
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=btn_rect.center)
    fx._handle_arena_modal_click(event)
    assert fx._net.sent == []
    # modal continua aberto — clique em botão desabilitado não fecha nada
    assert fx._arena_modal_open is True


def test_clique_no_x_fecha_sem_mandar_nada():
    fx = _ArenaFixture()
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=close_rect.center)
    consumed = fx._handle_arena_modal_click(event)
    assert consumed is True
    assert fx._arena_modal_open is False
    assert fx._net.sent == []


def test_clique_sair_quando_ja_na_fila_manda_leave():
    fx = _ArenaFixture(my_eid=1)
    fx._arena_in_queue_mode_val = "1v1"
    fx._arena_modal_open_val = True
    panel, close_rect, rows, bg_row_rect, bg_btn_rect = fx._arena_modal_rects()
    _, _, _, btn_rect = next(r for r in rows if r[0] == "1v1")
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=btn_rect.center)
    fx._handle_arena_modal_click(event)
    assert fx._net.sent[0][0] == "arena_queue_leave"
