"""
tests/test_map_width_ragged_rows.py — engine/entity_factory.py::create_tilemap

Bug real achado 02/09/2026 investigando avisos "[MAPA] largura
inesperada" no console (map_1, forma orgânica/irregular de propósito —
toda linha começa/termina com "m", montanha, borda natural; só a
extensão real do terreno varia por altura, não é CSV corrompido).
`map_width_tiles` usava só `len(terrain_matrix[0])` — se a linha 0
calhar de ser mais ESTREITA que outras linhas do mapa (comum num mapa
não-retangular), terreno real pintado além da largura da linha 0 virava
"fora do mapa" pra qualquer coisa que respeita `map_width_tiles`
(movimento, câmera, minimapa). Fix: usa a MAIOR linha.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.entity_factory import create_tilemap
from engine.components import Tilemap
from engine.map_loader import validate_map


class TestMapWidthUsesWidestRow(unittest.TestCase):

    def test_linha_0_mais_estreita_nao_trunca_largura_do_mapa(self):
        """Linha 0 (5 tiles) mais estreita que a linha 1 (10 tiles) —
        map_width_tiles precisa refletir os 10, não os 5."""
        terrain = ["GGGGG", "GGGGGGGGGG", "GGGGG"]
        objects = ["....."] * 3
        w = World()
        eid = create_tilemap(w, terrain, objects)
        tm = w.get_component(eid, Tilemap)
        self.assertEqual(tm.map_width_tiles, 10)

    def test_linha_curta_e_preenchida_com_floor_ate_a_largura_maxima(self):
        from engine.tileset import FLOOR_TILE
        terrain = ["GGGGG", "GGGGGGGGGG"]
        objects = ["....."] * 2
        w = World()
        eid = create_tilemap(w, terrain, objects)
        tm = w.get_component(eid, Tilemap)
        self.assertEqual(len(tm.tile_matrix[0]), 10, "linha curta deveria ser preenchida até a largura máxima")
        self.assertIs(tm.tile_matrix[0][7], FLOOR_TILE, "preenchimento além da linha real deveria ser FLOOR_TILE")

    def test_todas_as_linhas_do_tile_matrix_ficam_com_o_mesmo_tamanho(self):
        terrain = ["G" * 3, "G" * 12, "G" * 7, "G" * 5]
        objects = ["." * 3, "." * 12, "." * 7, "." * 5]
        w = World()
        eid = create_tilemap(w, terrain, objects)
        tm = w.get_component(eid, Tilemap)
        self.assertTrue(all(len(row) == tm.map_width_tiles for row in tm.tile_matrix))

    def test_mapa_retangular_uniforme_continua_igual_de_sempre(self):
        """Regressão: mapa retangular normal (todas as linhas iguais)
        continua se comportando exatamente como antes — a mudança só
        importa pra mapa com linhas de tamanhos diferentes."""
        terrain = ["G" * 8] * 4
        objects = ["." * 8] * 4
        w = World()
        eid = create_tilemap(w, terrain, objects)
        tm = w.get_component(eid, Tilemap)
        self.assertEqual(tm.map_width_tiles, 8)
        self.assertEqual(tm.map_height_tiles, 4)


class TestValidateMapNoLongerFlagsRaggedRows(unittest.TestCase):
    """`validate_map` (02/09/2026) parou de comparar toda linha contra a
    largura da linha 0 — falso positivo pra mapa de forma orgânica de
    propósito (mesma causa raiz do bug de `create_tilemap` acima)."""

    def test_linhas_de_tamanhos_diferentes_nao_gera_mais_aviso(self):
        tile_matrix = ["G" * 5, "G" * 20, "G" * 3]
        self.assertEqual(validate_map(tile_matrix), [])

    def test_mapa_vazio_ainda_avisa(self):
        self.assertEqual(validate_map([]), ["Mapa vazio."])

    def test_mapa_retangular_normal_continua_sem_aviso(self):
        self.assertEqual(validate_map(["G" * 8] * 4), [])


if __name__ == "__main__":
    unittest.main()
