"""
tests/test_map_services.py — guarda automática do _svc (item A3,
PROBLEMAS_ARQUITETURA.md §11): is_tile_walkable de módulo resolve o bundle
do mapa da PRÓPRIA entidade mesmo com _svc apontando pro mapa errado.

Reproduz a classe de bug real: cave_west é 80×60 — um tile de map_1 como
(131,374) fica FORA DOS LIMITES da caverna. Com _svc sabotado pra caverna
(simulando register_map_services_for esquecido), o resolver ainda valida
contra map_1.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from tests.helpers import make_world_server, spawn_player
import engine.world_systems as wsys


class TestServiceResolverGuard(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.p  = spawn_player(self.ws, "s1", 130, 374)   # player em map_1
        # Sabota _svc: aponta pro bundle da CAVERNA (80×60)
        cave = next(m for m in self.ws._map_bundles if "cave" in m)
        bundle = self.ws._map_bundles[cave]
        wsys.register_services(tile_validation=bundle.tile_validation,
                               pathfinding=bundle.pathfinding)

    def test_resolver_neutraliza_svc_no_mapa_errado(self):
        self.assertTrue(wsys.is_tile_walkable(self.p, 131, 374),
                        "resolver deveria validar contra map_1 (walkable)")

    def test_sem_resolver_reproduz_o_bug(self):
        saved = wsys._svc_resolver
        wsys.register_service_resolver(None)
        try:
            self.assertFalse(wsys.is_tile_walkable(self.p, 131, 374),
                             "sem resolver deveria falhar (fora dos limites da caverna)")
        finally:
            wsys.register_service_resolver(saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
