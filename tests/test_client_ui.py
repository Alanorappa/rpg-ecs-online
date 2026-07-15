"""
tests/test_client_ui.py — Testes headless de UI do CLIENTE (SDL dummy).

Primeiros testes permanentes de cliente do projeto (item D2 da auditoria,
PROBLEMAS_ARQUITETURA.md §11): os padrões de validação headless usados nas
correções de 11-15/07/2026 (marcadores de mapa, fonte tight, HUD composta)
viravam scripts descartáveis no scratchpad — promovidos aqui pra virarem
sinal de regressão.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
# Vários caminhos de UI exigem um display real (convert_alpha, mouse):
pygame.display.set_mode((320, 240))


# ── ui/map_markers.py ────────────────────────────────────────────────────────

def _make_marker_world():
    from engine.world import World
    from engine.components import (TileMovement, Trainer, Merchant, Visible)
    world = World()
    trainer = world.create_entity()   # SEM Visible (fora de FoW) — deve aparecer
    world.add_component(trainer, TileMovement(current_tile_x=5, current_tile_y=5))
    world.add_component(trainer, Trainer(class_id="mago"))
    merchant = world.create_entity()  # COM Visible — também aparece
    world.add_component(merchant, TileMovement(current_tile_x=8, current_tile_y=8))
    world.add_component(merchant, Merchant())
    world.add_component(merchant, Visible())
    return world


class _FakeQuestDialog:
    def marker_for(self, eid):
        return None


def test_collect_markers_nao_exige_visible():
    """Decisão 25 (ARQUITETURA_ONLINE.md): ícone de mapa/minimapa GUIA o
    player — NPC fora da linha de visão continua no mapa."""
    from ui.map_markers import collect_markers
    world = _make_marker_world()
    player = world.create_entity()
    markers = collect_markers(world, player, _FakeQuestDialog())
    kinds = sorted(m.icon_name for m in markers)
    assert kinds == ["map_merchant", "map_trainer_mago"], kinds


def test_deconflict_positions_sem_sobreposicao():
    from ui.map_markers import deconflict_positions
    pts = [(100.0, 100.0)] * 4           # 4 marcadores no MESMO ponto
    placed = deconflict_positions(pts, min_dist=8.0)
    assert len(placed) == 4
    for i, (ax, ay) in enumerate(placed):
        for bx, by in placed[i + 1:]:
            assert (ax - bx) ** 2 + (ay - by) ** 2 >= 8.0 ** 2 - 1e-6, \
                "dois marcadores continuam sobrepostos"


# ── ui/fonts.py::render_tight ────────────────────────────────────────────────

def test_render_tight_remove_bearing_gigante():
    """Bug real 11/07: 'i' da MEGAMAN10 tem advance=6 mas tinta começa em x=3
    ('Zumbi' virava 'Zumb i'). render_tight reempacota pela tinta real."""
    from ui.fonts import make_pixel, render_tight
    font = make_pixel(16)
    naive = font.render("Zumbi", False, (255, 255, 255))
    tight = render_tight(font, "Zumbi", (255, 255, 255))
    assert tight.get_width() < naive.get_width(), \
        f"tight ({tight.get_width()}px) deveria ser mais estreito que naive ({naive.get_width()}px)"
    # cache: mesma chamada devolve o MESMO objeto
    assert render_tight(font, "Zumbi", (255, 255, 255)) is tight


# ── ui/hud_bars.py ───────────────────────────────────────────────────────────

def test_build_hud_tamanhos_e_cache_de_nivel():
    import ui.hud_bars as hb
    from ui.fonts import make_pixel
    font = make_pixel(hb.LEVEL_FONT_SIZE)

    p = hb.build_player_hud(0.5, 0.3, 0.8, hb.RESOURCE_COLORS["arqueiro"], 12, font)
    assert p.get_size() == (hb.P_SIZE[0] * hb.SCALE, hb.P_SIZE[1] * hb.SCALE)

    m = hb.build_mob_hud(0.9, 7, font)
    assert m.get_size() == (hb.M_SIZE[0] * hb.SCALE, hb.M_SIZE[1] * hb.SCALE)

    # Cache do número de nível (fix de perf 14/07): 2ª chamada = cache hit
    hb._level_surf_cache.clear()
    hb._level_surf(33, font)
    n_after_first = len(hb._level_surf_cache)
    hb._level_surf(33, font)
    assert len(hb._level_surf_cache) == n_after_first == 1


def test_effects_row_offset_meia_largura():
    """Bug real 11/07: usava largura INTEIRA pra ir do centro à borda direita
    — sobrava um vão do tamanho da HUD entre ela e os ícones de efeito."""
    import ui.hud_bars as hb
    from ui.fonts import make_pixel
    surf = hb.build_mob_hud(1.0, 1, make_pixel(hb.LEVEL_FONT_SIZE))
    xo, yo = hb.effects_row_offset(surf)
    assert xo == surf.get_width() / 2 + hb.EFFECTS_GAP_PX
    assert yo == -hb.HUD_GAP_PX - surf.get_height() / 2
