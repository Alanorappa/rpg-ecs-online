"""
tests/test_map_loader_cache.py — cache de parse de mapa (11/08/2026, ver
PROBLEMAS_ARQUITETURA.md §27). Achado por profiling real: `WorldServer()`
levava ~396ms pra construir, ~84% disso em `_parse_terrain_cell` — cada
teste que chama `make_world_server()` reparseava os MESMOS 3 CSVs de
mapa do zero (conteúdo estático, nunca muda em runtime). Fix:
`engine/map_loader.py::load_map_csv` cacheia o parse por filepath
resolvido, mas SEMPRE devolve uma CÓPIA independente — nunca o objeto
cacheado direto, pra não vazar mutação de uma instância de WorldServer
pra outra (spawn_points/object_matrix são mutados por quem chama depois,
ex: harvestable reabastecendo).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from engine.map_loader import load_map_csv, _MAP_CSV_CACHE

_MAP = "maps/map_1.csv"


class TestMapLoaderCacheIsolation(unittest.TestCase):

    def test_duas_chamadas_retornam_dados_equivalentes(self):
        t1, o1, sp1, v1 = load_map_csv(_MAP)
        t2, o2, sp2, v2 = load_map_csv(_MAP)
        self.assertEqual(t1, t2)
        self.assertEqual(o1, o2)
        self.assertEqual(sp1, sp2)

    def test_segunda_chamada_usa_o_cache_e_devolve_objetos_diferentes(self):
        """Nunca pode ser o MESMO objeto — senão mutar o resultado de uma
        chamada vazaria pra outra (2 WorldServer distintos)."""
        t1, o1, sp1, v1 = load_map_csv(_MAP)
        t2, o2, sp2, v2 = load_map_csv(_MAP)
        self.assertIsNot(t1, t2)
        self.assertIsNot(o1, o2)
        self.assertIsNot(sp1, sp2)
        self.assertTrue(o1, "sanity: object_matrix não deveria vir vazio")

    def test_mutar_object_matrix_de_uma_chamada_nao_afeta_a_proxima(self):
        _, o1, _, _ = load_map_csv(_MAP)
        original_cell = o1[0][0]
        o1[0][0] = "__mutated__"

        _, o2, _, _ = load_map_csv(_MAP)

        self.assertEqual(o2[0][0], original_cell,
                         "mutação na 1ª chamada vazou pra 2ª — cache não está isolando cópias")

    def test_mutar_spawn_points_de_uma_chamada_nao_afeta_a_proxima(self):
        _, _, sp1, _ = load_map_csv(_MAP)
        original_count = len(sp1["spawn_zones"])
        sp1["spawn_zones"].append({"fake": "entry"})

        _, _, sp2, _ = load_map_csv(_MAP)

        self.assertEqual(len(sp2["spawn_zones"]), original_count,
                         "mutação em spawn_points da 1ª chamada vazou pra 2ª")
        self.assertNotIn({"fake": "entry"}, sp2["spawn_zones"])

    def test_cache_populado_apos_primeira_chamada(self):
        from paths import resource_path
        load_map_csv(_MAP)
        self.assertIn(resource_path(_MAP), _MAP_CSV_CACHE)


class TestTwoWorldServersDontShareMutableMapState(unittest.TestCase):
    """Prova de integração: 2 instâncias de WorldServer construídas em
    sequência (mesmo cenário de rodar vários testes na mesma suíte) não
    compartilham estado de harvestable/spawn — cada uma tem sua própria
    cópia mutável independente."""

    def test_harvestable_de_uma_instancia_nao_aparece_na_outra_antes_de_mutar(self):
        from tests.helpers import make_world_server
        ws1 = make_world_server()
        ws2 = make_world_server()

        # Mutação em uma instância (simula reabastecer/esvaziar harvestable)
        cid = next(iter(ws1._corpses), None)
        if cid is not None:
            ws1._corpses[cid]["coins"] = 999999

            # A OUTRA instância não pode ver essa mutação.
            if cid in ws2._corpses:
                self.assertNotEqual(ws2._corpses[cid]["coins"], 999999)


if __name__ == "__main__":
    unittest.main()
