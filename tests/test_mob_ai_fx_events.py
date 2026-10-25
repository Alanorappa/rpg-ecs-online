"""
tests/test_mob_ai_fx_events.py

Texto "Aggro"/"Evadiu" sobre o mob (24/10/2026, pedido do usuário): o
servidor emite um resultado de combate outcome "aggro" quando o mob sai de
IDLE pra briga e "evade" quando desiste e volta pra casa
(EnemyAISystem._emit_ai_state_fx → _svc["emit_ai_event"]). Volta pra casa
porque o alvo morreu NÃO é evade.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import TileMovement, AIControlled, Enemy, CombatStats
from engine.utils import snap_to_tile


class TestAggroEvadeFx(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.p = spawn_player(self.ws, "p_fx", 128, 392, class_id="xama", hp=50000)
        run_ticks(self.ws, 300)
        best = None
        for m in list(self.ws._mob_eids):
            if (self.ws.world.get_component(m, AIControlled) is None
                    or self.ws.world.get_component(m, Enemy) is None
                    or self.ws.get_entity_map(m) != "maps/map_1.csv"):
                continue
            t = self.ws.world.get_component(m, TileMovement)
            d = abs(t.current_tile_x - 128) + abs(t.current_tile_y - 392)
            if best is None or d < best[0]:
                best = (d, m)
        self.mob = best[1]
        self.mtm = self.ws.world.get_component(self.mob, TileMovement)
        for m in list(self.ws._mob_eids):          # só este mob na região
            if m == self.mob or self.ws.world.get_component(m, AIControlled) is None:
                continue
            t = self.ws.world.get_component(m, TileMovement)
            if t and max(abs(t.current_tile_x - self.mtm.current_tile_x),
                         abs(t.current_tile_y - self.mtm.current_tile_y)) <= 40:
                self.ws.world.remove_entity(m)
                self.ws._mob_eids.discard(m)
        # Afasta o jogador primeiro (mob em repouso)
        snap_to_tile(self.ws.world, self.p, self.mtm.current_tile_x + 30, self.mtm.current_tile_y)
        run_ticks(self.ws, 60)

    def _ticks(self, n, each=None):
        """Roda tick a tick (o helper em lote se comporta diferente) e
        devolve os outcomes aggro/evade deste mob."""
        out = []
        for _ in range(n):
            if each:
                each()
            d = run_ticks(self.ws, 1)
            out += [e["outcome"] for e in d["combat"]
                    if e.get("target") == self.mob and e.get("outcome") in ("aggro", "evade")]
        return out

    def _aggro(self):
        snap_to_tile(self.ws.world, self.p, self.mtm.current_tile_x + 3, self.mtm.current_tile_y)
        return self._ticks(60)

    def test_aggro_e_depois_evade_quando_alvo_some(self):
        self.assertEqual(self._aggro().count("aggro"), 1)
        # Alvo fica invisível (ex: Camuflagem) → mob desiste e volta pra casa
        # (mesma transição pra RETURNING de leash/inalcançável, sem depender
        # da velocidade do mob sorteado).
        from engine.components import CombatState
        self.ws.world.get_component(self.p, CombatState).is_visible = False
        self.assertIn("evade", self._ticks(30))

    def test_alvo_morreu_nao_e_evade(self):
        self.assertEqual(self._aggro().count("aggro"), 1)
        self.ws.world.get_component(self.p, CombatStats).current_hp = 0
        self.assertNotIn("evade", self._ticks(150))


if __name__ == "__main__":
    unittest.main()
