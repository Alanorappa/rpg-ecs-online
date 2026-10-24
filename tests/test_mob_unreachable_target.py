"""
tests/test_mob_unreachable_target.py

Bug real em produção (24/10/2026, HISTORICO_BUGS.md §102): um par que se
agredia através de uma cerca (Custódio dentro do cercado, zumbi fora)
recalculava o caminho impossível TODO TICK — `should_recalculate_path`
disparava em "sem caminho" ignorando o timer — e cada recálculo testava
vários tiles (A* completo falhando). Isso esgotava o orçamento de A* do
mapa (MAX_PATHFINDS_PER_FRAME) e TODOS os outros mobs/NPCs paravam.

Simula "alvo inalcançável" forçando o pathfinding do mapa a sempre falhar.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import TileMovement, AIControlled, Enemy
from engine.utils import snap_to_tile


class TestAlvoInalcancavel(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.p = spawn_player(self.ws, "p_unr", 128, 392, class_id="xama", hp=50000)
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
        self.ai = self.ws.world.get_component(self.mob, AIControlled)
        mtm = self.ws.world.get_component(self.mob, TileMovement)
        # Jogador a 3 tiles do mob (dentro do raio de detecção) → mob agra
        snap_to_tile(self.ws.world, self.p, mtm.current_tile_x + 3, mtm.current_tile_y)
        self.bundle = self.ws._map_bundles["maps/map_1.csv"]
        self.calls = 0
        def _always_fail(*_a, **_k):
            self.calls += 1
            return None
        self.bundle.pathfinding.find_path = _always_fail

    def _chasers(self):
        out = []
        for m in list(self.ws._mob_eids):
            ai = self.ws.world.get_component(m, AIControlled)
            if ai is not None and ai.target_eid == self.p and ai.state == "CHASING":
                out.append(m)
        return out

    def test_nao_recalcula_todo_tick_e_desiste(self):
        run_ticks(self.ws, 45)                      # agro + delay de agro
        chasers = self._chasers()
        self.assertTrue(chasers, "nenhum mob perseguindo o jogador — cenário não montado")
        self.calls = 0
        run_ticks(self.ws, 30)                      # 1s
        # Antigo: ~5 A* por tick × 30 ticks por mob (medido: 278/s), estourando
        # o orçamento. Novo: 1 rodada por path_recalc_interval (~0.8s).
        self.assertLess(self.calls, 20 * len(chasers),
                        f"A* demais por segundo: {self.calls} ({len(chasers)} mob(s))")
        # Passa do UNREACHABLE_GIVE_UP_S: cada um desiste (RETURNING) pelo
        # menos uma vez — depois pode reagrar, o jogador continua perto.
        gave_up = set()
        for _ in range(30 * 5):
            run_ticks(self.ws, 1)
            for m in chasers:
                if self.ws.world.get_component(m, AIControlled).state == "RETURNING":
                    gave_up.add(m)
        self.assertEqual(gave_up, set(chasers),
                         "todo mob com alvo inalcançável deveria ter desistido")


if __name__ == "__main__":
    unittest.main()
