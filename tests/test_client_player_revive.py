"""tests/test_client_player_revive.py — client/network_handlers.py::
_handle_msg_player_revive.

Bug real relatado pelo usuário (02/08/2026): reviver dentro do
battleground de teste fazia o personagem "andar sozinho" de volta pro
tile onde morreu. Causa raiz: o handler resetava TileMovement/GhostState/
CombatState ao reviver, mas nunca `PlayerAutoMove` — se o player estava
perseguindo/seguindo/andando até um ground_target no momento da morte, o
path/ground_target ANTIGO (perto de onde morreu) sobrevivia ao teleporte
de revive e retomava assim que o movimento voltava a ser permitido.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import (
    CombatStats, CharacterStats, TileMovement, Position, GhostState,
    CombatState, PlayerAutoMove, PlayerControlled,
)
from client.network_handlers import NetworkHandlers


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class FakeClient(NetworkHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()
        self._remote_players = {}
        self._remote_mobs = {}


class TestPlayerReviveClearsAutoMove(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, CombatStats())
        self.world.add_component(self.eid, CharacterStats(name="p1", class_id="guerreiro"))
        self.world.add_component(self.eid, TileMovement(current_tile_x=50, current_tile_y=50))
        self.world.add_component(self.eid, Position(x=0, y=0))
        self.world.add_component(self.eid, GhostState(is_dead=True))
        self.world.add_component(self.eid, CombatState())
        # Simula o player perseguindo/andando até o ponto onde morreu.
        am = PlayerAutoMove()
        am.active = True
        am.path = [(48, 48), (49, 49), (50, 50)]
        am.ground_target = (50, 50)
        am.follow_eid = 99
        self.world.add_component(self.eid, am)
        self.client = FakeClient(self.world, self.eid)

    def test_revive_limpa_auto_move_pendente(self):
        self.client._handle_msg_player_revive({
            "tx": 3, "ty": 96, "hp": 100, "hp_max": 100, "mana": 0, "max_mana": 0,
        })
        am = self.world.get_component(self.eid, PlayerAutoMove)
        self.assertFalse(am.active, "auto-move não deveria continuar ativo após reviver")
        self.assertEqual(am.path, [], "path pendente deveria ter sido descartado")
        self.assertIsNone(am.ground_target)
        self.assertEqual(am.follow_eid, -1)

    def test_revive_ainda_teleporta_e_revive_normalmente(self):
        """Prova que a limpeza de PlayerAutoMove não quebrou o resto do
        handler (teleporte/HP/GhostState continuam funcionando)."""
        self.client._handle_msg_player_revive({
            "tx": 3, "ty": 96, "hp": 100, "hp_max": 100, "mana": 0, "max_mana": 0,
        })
        tm = self.world.get_component(self.eid, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (3, 96))
        gst = self.world.get_component(self.eid, GhostState)
        self.assertFalse(gst.is_dead)
        cs = self.world.get_component(self.eid, CombatStats)
        self.assertEqual(cs.current_hp, 100)


if __name__ == "__main__":
    unittest.main()
