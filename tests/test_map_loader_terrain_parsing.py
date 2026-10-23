"""
tests/test_map_loader_terrain_parsing.py — `_parse_terrain_cell` nunca pode
devolver um token desconhecido de múltiplos caracteres como `terrain_char`
(10/09/2026, bug real relatado pelo usuário: "o mapa duplicou a largura,
mas a parte explorável não"). `terrain_matrix` é sempre 1 char por tile
("".join(terrain_row) em `_load_two_layer`/`_load_single_layer`) — um
token de N chars nessa posição infla a linha inteira, desalinhando todo
tile seguinte (colisão errada) e inflando `map_width_tiles` (minimapa/
câmera/limites, ver `ui/minimap.py::render_fullmap`).

Causa raiz real: battleground tinha 258 células "txg_0_0"/"txg_0_4" —
sheet nunca cadastrada em `SHEET_TILE_MAP` — inflando a largura
carregada de 100 pra até 208.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from engine.map_loader import _parse_terrain_cell, load_map_csv
from engine.tileset import SHEET_TILE_MAP


class TestColisaoPorCelulaNoTerreno(unittest.TestCase):
    """22/10/2026 — chão da caverna ("cvg"): célula com colisão (buraco/
    lava/água) vira "W" (sólido, não bloqueia visão) via `cell_underlying`;
    o resto da família continua no `underlying_terrain` dela."""

    def test_celula_solida_e_andavel_na_mesma_familia(self):
        from engine.tileset import SHEET_FAMILIES, TILE_MAPPING
        fam = next(f for f in SHEET_FAMILIES if f["prefix"] == "cvg")
        solid = next(iter(fam["cell_underlying"]))
        walk = next(c for c in fam["cells"] if c not in fam["cell_underlying"])
        tc, vis = _parse_terrain_cell("cvg_%d_%d" % solid)
        self.assertEqual(tc, "W")
        self.assertTrue(TILE_MAPPING[tc].is_solid)
        self.assertEqual(TILE_MAPPING[tc].vision_height, 0)
        self.assertEqual(_parse_terrain_cell("cvg_%d_%d" % walk)[0], fam["underlying_terrain"])

    def test_celula_fora_de_cells_nao_e_registrada(self):
        from engine.tileset import SHEET_FAMILIES
        fam = next(f for f in SHEET_FAMILIES if f["prefix"] == "cvg")
        self.assertEqual({k for k in SHEET_TILE_MAP if k.startswith("cvg_")},
                         {"cvg_%d_%d" % c for c in fam["cells"]})


class TestParseTerrainCellNuncaInflaLinha(unittest.TestCase):

    def test_token_desconhecido_de_varios_chars_cai_pro_G(self):
        tc, vis = _parse_terrain_cell("txg_0_4")
        self.assertEqual(tc, "G")
        self.assertEqual(vis, "",
            "token desconhecido não deveria carregar visual nenhum")

    def test_token_desconhecido_diferente_tambem_cai_pro_G(self):
        """Qualquer prefixo de sheet removida/nunca cadastrada tem que
        cair no mesmo fallback, não só o caso real "txg" — a proteção é
        genérica (`len(cell) > 1`), não uma lista de nomes conhecidos."""
        tc, vis = _parse_terrain_cell("xyz_9_9")
        self.assertEqual(tc, "G")
        self.assertEqual(vis, "")

    def test_char_simples_desconhecido_continua_passando_direto(self):
        """1 caractere nunca infla nada — continua indo direto pro
        terrain_char, mesmo comportamento de sempre (ex: 'm' de montanha,
        '#' de parede, que não estão em SHEET_TILE_MAP)."""
        tc, vis = _parse_terrain_cell("m")
        self.assertEqual(tc, "m")
        self.assertEqual(vis, "")

    def test_token_registrado_no_sheet_tile_map_funciona_normal(self):
        sample = next(iter(SHEET_TILE_MAP))
        tc, vis = _parse_terrain_cell(sample)
        self.assertEqual(len(tc), 1,
            "terrain_char de um sheet tile reconhecido precisa continuar sendo 1 char")
        self.assertEqual(vis, sample)


class TestBattlegroundMapCarregaQuadrado(unittest.TestCase):
    """Regressão de integração: o mapa real da BG (não um mock) precisa
    carregar como grid uniforme — nenhuma linha mais larga que outra.
    Prova de verdade do bug relatado (minimapa/mundo da BG "esticado")."""

    def test_moba_battleground_todas_as_linhas_tem_a_mesma_largura(self):
        terrain_matrix, object_matrix, _spawn_points, _visual = load_map_csv(
            "maps/moba_battleground.csv")
        larguras = {len(row) for row in terrain_matrix}
        self.assertEqual(larguras, {100},
            "terrain_matrix da BG deveria ser uniformemente 100 colunas "
            f"— larguras encontradas: {sorted(larguras)}")


class TestDayNightAffectedFlag(unittest.TestCase):
    """day_night_affected (23/09/2026) — mapa fechado (caverna/dungeon)
    marca False no <mapa>_entities.json pra ui/day_night_system.py e
    ui/day_night_clock_hud.py pularem o ciclo dia/noite inteiro nesse
    mapa (sem luz do sol, tingir/mostrar relógio não faz sentido). Ver
    PROBLEMAS_ARQUITETURA.md §206."""

    def test_mapa_sem_a_chave_no_json_defaults_pra_true(self):
        _, _, spawn_points, _ = load_map_csv("maps/map_1.csv")
        self.assertIs(spawn_points["day_night_affected"], True)

    def test_caverna_oeste_marcada_como_nao_afetada(self):
        _, _, spawn_points, _ = load_map_csv("maps/map_cave_west.csv")
        self.assertIs(spawn_points["day_night_affected"], False)

    def test_caverna_leste_marcada_como_nao_afetada(self):
        _, _, spawn_points, _ = load_map_csv("maps/map_cave_east.csv")
        self.assertIs(spawn_points["day_night_affected"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
