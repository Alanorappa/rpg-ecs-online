"""
tests/test_atlas_kit.py — peças do kit de janelas do atlas de UI
(ui/atlas_window.py: checkbox, abas, slot/grade, texto com quebra,
divisórias, X da barra de título, campo de senha; ui/ui_atlas.py:
titled_dark_window_raw). Clique sempre no SOLTAR do mouse.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from ui import atlas_window as aw
from ui import ui_atlas
from ui.fonts import make as _font

_W, _H = 1280, 720
T, S = aw.T, aw.S


@pytest.fixture(autouse=True)
def _display():
    prev = pygame.display.get_surface()
    prev_size = prev.get_size() if prev is not None else None
    pygame.display.set_mode((_W, _H))
    yield
    if prev_size is not None:
        pygame.display.set_mode(prev_size)


def _frame(events=(), press=None, tw=14, th=12):
    screen = pygame.Surface((_W, _H))
    f = aw.Frame(screen, list(events), tw, th, area=pygame.Rect(0, 0, _W, _H),
                 press=press or aw.PressState(), font=_font(24),
                 raw=ui_atlas.titled_dark_window_raw(tw, th), overlay=False)
    return f


def _origin():
    f = _frame()
    return f.px, f.py


def _click(build, raw_pos):
    """build(f) desenha as peças e devolve o resultado; clica (aperta e
    solta) no ponto RAW dado. Devolve (resultado_no_apertar, resultado_no_soltar)."""
    press = aw.PressState()
    ox, oy = _origin()
    pos = (ox + int(raw_pos[0] * S), oy + int(raw_pos[1] * S))
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: (True, False, False)
        f = _frame([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)], press)
        down = build(f); f.end()
        pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
        f = _frame([pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)], press)
        up = build(f); f.end()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig
    return down, up


def test_janela_com_titulo_tem_x_na_ultima_coluna():
    raw = ui_atlas.titled_dark_window_raw(14, 12)
    assert raw.get_size() == (14 * T, 12 * T)
    atlas = ui_atlas._load()
    x_tile = atlas.subsurface((5 * T, 0, T, T))
    for px in ((3, 3), (8, 8), (12, 4)):
        assert raw.get_at((13 * T + px[0], px[1])) == x_tile.get_at(px)


def test_checkbox_alterna_no_soltar_inclusive_pelo_rotulo():
    build = lambda f: f.checkbox("cb", 2, 3, False, "Lembrar login")
    assert _click(build, (2 * T + 8, 3 * T + 8)) == (False, True)
    assert _click(build, (3 * T + 20, 3 * T + 8))[1] is True   # rótulo
    assert _click(build, (1 * T + 8, 3 * T + 8))[1] is False


def test_abas_devolvem_indice_clicado_e_ignoram_a_ativa():
    labels = ["Entrada", "Escrever"]
    font = _font(24)
    w0 = aw.tiles_for(font, ["Entrada"], 3)
    build = lambda f: f.tabs("tb", 1, 1, labels, 0)
    assert _click(build, ((1 + w0) * T + 8, T + 8)) == (None, 1)
    assert _click(build, (1 * T + 8, T + 8))[1] is None   # já ativa


def test_grade_devolve_indice_sob_o_mouse_e_clicado():
    cells = [None] * 20
    build = lambda f: f.grid("g", 8 * T, 2 * T, 5, 4, cells)
    # célula (col 2, linha 1) = índice 7 — slots de aw.SLOT px RAW
    down, up = _click(build, (8 * T + 2 * aw.SLOT + 8, 2 * T + aw.SLOT + 8))
    assert down == (7, -1) and up == (7, 7)


def test_slot_coloca_icone_e_quantidade_nas_sobreposicoes():
    f = _frame()
    icon = pygame.Surface((aw.slot_icon_px(), aw.slot_icon_px()))
    f.slot("s", 32, 32, icon, count=5)
    assert len(f.overlays) == 2   # ícone + número
    f = _frame()
    f.slot("s", 32, 32, icon, count=1)
    assert len(f.overlays) == 1   # quantidade 1 não aparece


def test_text_block_quebra_e_devolve_y_final():
    f = _frame()
    y_end = f.text_block("palavra " * 40, 16, 16, 100)
    assert len(f.texts) > 3
    assert y_end > 16 + 3 * 8


def test_campo_de_senha_mostra_asteriscos():
    f = _frame()
    f.text_field("pw", 2, 4, 8, "segredo", mask=True)
    assert f.texts[-1][0] == "*******"


def test_x_da_barra_de_titulo_dispara_no_soltar():
    build = lambda f: f.close_button("x", 13)
    assert _click(build, (13 * T + 8, 8)) == (False, True)


def test_slot_tem_icone_32_com_borda():
    from ui import ui_atlas
    assert ui_atlas.slot_raw().get_size() == (aw.SLOT, aw.SLOT)
    assert aw.slot_icon_px() == 32 * aw.S


def test_modal_de_quantidade_centralizado():
    hits = {}
    st = aw.quantity_state(10)
    screen = pygame.Surface((1280, 720))
    area = pygame.Rect(0, 0, 1280, 720)
    aw.quantity_window(screen, [], st, area=area, press=aw.PressState(), font=_font(24),
                       small_font=_font(18), title="Poção", info="10g", confirm_label="Comprar 1",
                       hits=hits)
    cx = area.centerx
    assert abs((hits["qty:-"].left + hits["qty:+"].right) / 2 - cx) <= 2
    assert abs((hits["qty:cancel"].left + hits["qty:ok"].right) / 2 - cx) <= 2
