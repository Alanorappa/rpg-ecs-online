"""
tests/test_hud_atlas.py — HUD no atlas de UI (21/10/2026, pedido do
usuário): moldura do minimapa (nome + fileira de ícones), retrato (rosto +
Vida/recurso/XP nas ranhuras, 3x) e molduras de slot da hotbar/consumíveis.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from ui import ui_atlas


@pytest.fixture(autouse=True)
def _display():
    prev = pygame.display.get_surface()
    prev_size = prev.get_size() if prev is not None else None
    pygame.display.set_mode((1280, 720))
    yield
    if prev_size is not None:
        pygame.display.set_mode(prev_size)


def test_stretch_mantem_bordas_e_repete_o_meio():
    rect = ui_atlas.HUD_SKILL_FRAME
    src = ui_atlas.piece_raw(rect)
    out = ui_atlas.stretch_raw(rect, 36, 36)
    assert out.get_size() == (36, 36)
    for (sx, sy), (dx, dy) in (((0, 0), (0, 0)), ((19, 19), (35, 35)), ((2, 0), (2, 0)), ((19, 3), (35, 3))):
        assert tuple(out.get_at((dx, dy))) == tuple(src.get_at((sx, sy)))
    # meio repetido: qualquer coluna do miolo da borda de cima = coluna do meio da peça
    assert tuple(out.get_at((20, 1))) == tuple(src.get_at((10, 1)))


def test_moldura_de_slot_cabe_icone_32():
    for kind in ("skill", "cons"):
        f = ui_atlas.hud_slot_frame(kind)
        assert f.get_size() == (ui_atlas.HUD_SLOT_PX, ui_atlas.HUD_SLOT_PX)
    assert ui_atlas.HUD_SLOT_PX - 2 * ui_atlas.HUD_SLOT_BORDER * ui_atlas.SCALE == 32 * ui_atlas.SCALE


def test_retrato_tem_3_ranhuras_medidas_do_atlas():
    g = ui_atlas.portrait_grooves()
    assert len(g) == 3
    assert all(h == 2 and w > 20 for _x, _y, w, h in g)
    assert g[0][1] < g[1][1] < g[2][1]


class _MO:
    _cols, _rows = 60, 60
    _base_surf = pygame.Surface((60, 60))


def test_minimapa_moldura_e_icones_na_borda_de_baixo():
    from ui.minimap import Minimap
    screen = pygame.Surface((1280, 720))
    mm = Minimap(screen, _MO())
    mm.title = "Vila"
    mm.render(30, 30, {(30, 30)}, {(30, 30)}, [])
    fr = mm.frame_rect()
    content = mm.get_rect()
    assert fr.contains(content)
    icons = mm.icon_rects()
    assert len(icons) == 5
    for r in icons:
        assert r.top < fr.bottom < r.bottom          # círculo encaixado na borda de baixo
    assert abs((icons[0].left + icons[-1].right) / 2 - fr.centerx) <= ui_atlas.SCALE
    mm.render_icons([True, False, False, False, False])   # não quebra com aceso/apagado


def test_icone_consumido_age_no_soltar():
    import client.hud_handlers as hh

    class _Fx(hh.HudHandlers):
        def __init__(self):
            from ui.minimap import Minimap
            self._minimap = Minimap(pygame.Surface((1280, 720)), _MO())
            self._map_overlay = type("M", (), {"is_open": False})()
            self._mm_icon_pressed = -1
            self.calls = []

        def _toggle_inventory(self):
            self.calls.append("inv")

    fx = _Fx()
    pos = fx._minimap.icon_rects()[0].center
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)
    assert fx._handle_minimap_icon_event(down) is True and fx.calls == []
    assert fx._handle_minimap_icon_event(up) is True and fx.calls == ["inv"]
    away = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(5, 5))
    assert fx._handle_minimap_icon_event(away) is False
