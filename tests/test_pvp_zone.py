"""
tests/test_pvp_zone.py — Zona PvP (Fase F do roadmap): "sozinho = todos
hostis dentro da zona; em grupo = só quem está fora do grupo".
Ver server/pvp_zone_processor.py e ARQUITETURA_ONLINE.md §34.26.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from engine.faction_system import can_engage

_ZONE_RECT = (100, 100, 120, 120)   # x1,y1,x2,y2 em tiles
_IN_ZONE   = (110, 110)             # dentro do rect acima
_OUT_ZONE  = (5, 5)                 # bem longe do rect


class TestPvpZone(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", *_IN_ZONE)
        self.b = spawn_player(self.ws, "s2", *_IN_ZONE)
        map_file = self.ws.get_entity_map(self.a)
        self.ws._pvp_zones_by_map[map_file] = [{"name": "Zona Teste", "rect": _ZONE_RECT}]

    def test_ambos_dentro_da_zona_sem_grupo_podem_se_engajar(self):
        self.assertTrue(can_engage(self.ws.world, self.a, self.b))
        self.assertTrue(can_engage(self.ws.world, self.b, self.a))

    def test_um_dentro_outro_fora_continua_amigavel(self):
        set_entity_tile(self.ws, self.b, *_OUT_ZONE)
        self.assertFalse(can_engage(self.ws.world, self.a, self.b))
        self.assertFalse(can_engage(self.ws.world, self.b, self.a))

    def test_mesmo_grupo_dentro_da_zona_continua_amigavel(self):
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)
        self.assertFalse(can_engage(self.ws.world, self.a, self.b))
        self.assertFalse(can_engage(self.ws.world, self.b, self.a))

    def test_grupos_diferentes_dentro_da_zona_sao_hostis(self):
        c = spawn_player(self.ws, "s3", *_IN_ZONE)
        d = spawn_player(self.ws, "s4", *_IN_ZONE)
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)
        self.assertIsNone(self.ws.request_party_invite(c, d))
        self.ws.respond_party_invite(d, accept=True)
        self.assertTrue(can_engage(self.ws.world, self.a, c))
        self.assertTrue(can_engage(self.ws.world, c, self.a))

    def test_fora_de_qualquer_zona_sem_duelo_ou_grupo_continua_amigavel(self):
        set_entity_tile(self.ws, self.a, *_OUT_ZONE)
        set_entity_tile(self.ws, self.b, *_OUT_ZONE)
        self.assertFalse(can_engage(self.ws.world, self.a, self.b))

    def test_mapa_sem_pvp_zones_nao_quebra(self):
        map_file = self.ws.get_entity_map(self.a)
        self.ws._pvp_zones_by_map[map_file] = []
        self.assertFalse(can_engage(self.ws.world, self.a, self.b))

    def test_duelo_continua_funcionando_junto_com_zona(self):
        """Regressão: zona PvP é um `or`-clause novo em _pvp_allowed_between,
        não deveria interferir no caminho de duelo (fora da zona)."""
        set_entity_tile(self.ws, self.a, *_OUT_ZONE)
        set_entity_tile(self.ws, self.b, *_OUT_ZONE)
        self.assertIsNone(self.ws.request_duel(self.a, self.b))
        self.ws.respond_duel_invite(self.b, accept=True)
        self.assertTrue(can_engage(self.ws.world, self.a, self.b))


if __name__ == "__main__":
    unittest.main()
