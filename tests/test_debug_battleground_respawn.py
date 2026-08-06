"""tests/test_debug_battleground_respawn.py — server/debug_battleground.py::
_process_respawns (02/08/2026, pedido do usuário: "poderia ter um respawn
dele na base, com uma contagem de 15 segundos"). Respawn automático estilo
MOBA — sem "Liberar espírito"/caminhada de fantasma, sem prompt "Reviver
agora?" (esses continuam existindo pro resto do jogo, só o battleground de
teste usa esse fluxo mais simples).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.components import Faction, TileMovement, CombatStats, GhostState
from server import debug_battleground as bg


class TestDebugBattlegroundRespawn(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10)
        bg._state["members"] = {self.eid}
        bg._state["respawn_timers"] = {}

    def tearDown(self):
        bg._state["members"] = set()
        bg._state["respawn_timers"] = {}

    def _kill(self, faction_id: str) -> None:
        self.ws.world.add_component(self.eid, Faction(faction_id=faction_id))
        cs = self.ws.world.get_component(self.eid, CombatStats)
        cs.current_hp = 0
        self.ws._handle_player_death(self.eid)

    def test_morte_inicia_timer_de_respawn(self):
        self._kill("arena_time_a")
        bg._process_respawns(self.ws)
        self.assertIn(self.eid, bg._state["respawn_timers"])
        self.assertAlmostEqual(bg._state["respawn_timers"][self.eid], bg.DEBUG_BG_RESPAWN_S)

    def test_respawna_na_base_do_time_a_apos_15_segundos(self):
        self._kill("arena_time_a")
        n_ticks = int(bg.DEBUG_BG_RESPAWN_S / bg.TICK_INTERVAL) + 2
        for _ in range(n_ticks):
            bg._process_respawns(self.ws)

        tm = self.ws.world.get_component(self.eid, TileMovement)
        expected = bg.DEBUG_BG_SPAWN["a"]
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), expected)

        gst = self.ws.world.get_component(self.eid, GhostState)
        self.assertFalse(gst.is_dead, "deveria ter revivido (is_dead=False)")
        cs = self.ws.world.get_component(self.eid, CombatStats)
        self.assertEqual(cs.current_hp, cs.max_hp, "deveria reviver com HP cheio")
        self.assertNotIn(self.eid, bg._state["respawn_timers"])

    def test_respawna_na_base_do_time_b(self):
        self._kill("arena_time_b")
        n_ticks = int(bg.DEBUG_BG_RESPAWN_S / bg.TICK_INTERVAL) + 2
        for _ in range(n_ticks):
            bg._process_respawns(self.ws)

        tm = self.ws.world.get_component(self.eid, TileMovement)
        expected = bg.DEBUG_BG_SPAWN["b"]
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), expected)

    def test_nao_respawna_antes_do_tempo(self):
        self._kill("arena_time_a")
        n_ticks = int(bg.DEBUG_BG_RESPAWN_S / bg.TICK_INTERVAL) - 5
        for _ in range(max(1, n_ticks)):
            bg._process_respawns(self.ws)

        gst = self.ws.world.get_component(self.eid, GhostState)
        self.assertTrue(gst.is_dead, "não deveria ter revivido ainda")
        self.assertIn(self.eid, bg._state["respawn_timers"])

    def test_sair_do_battleground_morto_limpa_timer_pendente(self):
        """Prova de regressão do edge case documentado no código: sair via
        /testbg leave enquanto o timer de respawn ainda está contando não
        pode deixar um timer travado tentando teleportar o jogador de volta
        pro battleground depois dele já estar em outro mapa."""
        self._kill("arena_time_a")
        bg._process_respawns(self.ws)
        self.assertIn(self.eid, bg._state["respawn_timers"])

        bg._leave(self.ws, self.eid)

        self.assertNotIn(self.eid, bg._state["respawn_timers"])
        self.assertNotIn(self.eid, bg._state["members"])


if __name__ == "__main__":
    unittest.main()
