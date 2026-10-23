"""
tests/test_ranged_over_low_ground.py — piso abaixo do personagem (água,
lava, buraco: `TileType.low_ground`) impede de andar, mas não barra
flecha/magia (23/10/2026, pedido do usuário). Parede e pilar de pedra
continuam barrando; validação de movimento (projectile=False) também.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.components import Tilemap
from engine.tileset import TILE_MAPPING, SHEET_FAMILIES
from engine.world_systems import EnemyAISystem

LOS = EnemyAISystem._has_line_of_sight


def _row_map(mid_char: str) -> Tilemap:
    """Linha de 5 tiles: chão, chão, <mid_char>, chão, chão."""
    row = [TILE_MAPPING["."]] * 5
    row[2] = TILE_MAPPING[mid_char]
    return Tilemap(tile_matrix=[row], terrain_matrix=["." * 5], object_matrix=[["."] * 5],
                   tile_size=32, map_width_tiles=5, map_height_tiles=1)


def test_flecha_passa_por_cima_da_agua_lava_buraco():
    tm = _row_map("W")
    assert LOS(tm, 0, 0, 4, 0, projectile=True)


def test_movimento_continua_barrado_pela_agua():
    assert not LOS(_row_map("W"), 0, 0, 4, 0)


def test_parede_e_pedra_continuam_barrando_flecha():
    for ch in ("c", "r", "#"):
        assert not LOS(_row_map(ch), 0, 0, 4, 0, projectile=True), ch


def test_chao_de_caverna_borda_e_baixa_e_pilar_e_pedra():
    fam = next(f for f in SHEET_FAMILIES if f["prefix"] == "cvg")
    under = fam["cell_underlying"]
    assert under[(0, 0)] == "W"                    # borda do buraco
    assert under[(6, 7)] == "W"                    # lava cheia
    assert under[(3, 1)] == "r" and under[(4, 8)] == "r"   # pilares
    assert TILE_MAPPING["W"].low_ground and TILE_MAPPING["W"].is_solid
    assert not TILE_MAPPING["r"].low_ground and TILE_MAPPING["r"].is_solid
