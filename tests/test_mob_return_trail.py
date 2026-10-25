"""
tests/test_mob_return_trail.py

Trilha de volta + coleira por passos (24/10/2026, desenho do usuário,
HISTORICO_BUGS.md §103). Bug real: mob puxado pra longe do spawn dava
evade e ficava PARADO (e imune) pra sempre — a volta usava o A* limitado
(retângulo + folga, 300 nós), que falha em parte dos casos. Agora o mob
anota os tiles que pisou e volta por eles; e desiste depois de 12 passos
fora do raio livre de 8 tiles do spawn.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import (TileMovement, AIControlled, Enemy, InitialPosition,
                                Position, Tilemap)
from engine.world_systems import EnemyAISystem
from engine.utils import snap_to_tile, chebyshev


class _FakeTM:
    def __init__(self, x, y):
        self.current_tile_x, self.current_tile_y = x, y
        self.is_moving = False


class TestTrilhaUnidade(unittest.TestCase):
    def setUp(self):
        self.sys_ = EnemyAISystem.__new__(EnemyAISystem)
        self.ai = AIControlled()

    def _walk(self, tiles, spawn=(0, 0)):
        for x, y in tiles:
            self.sys_._record_trail(self.ai, _FakeTM(x, y), spawn)

    def test_passos_so_contam_fora_do_raio_livre(self):
        self._walk([(x, 0) for x in range(0, 13)])        # 0..12: fora do raio 8 = 9..12
        self.assertEqual(self.ai.steps_outside, 4)
        self.assertEqual(self.sys_._leash_reason(self.ai, 12), "")
        self._walk([(x, 0) for x in range(13, 21)])       # +8 passos fora = 12
        self.assertEqual(self.ai.steps_outside, 12)
        self.assertEqual(self.sys_._leash_reason(self.ai, 20), "passos")

    def test_cruzar_a_propria_trilha_corta_o_laco(self):
        self._walk([(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        self.assertEqual(self.ai.return_trail, [(0, 0), (1, 0)])

    def test_salto_de_empurrao_preenche_tiles(self):
        self._walk([(0, 0), (4, 0)])
        self.assertEqual(self.ai.return_trail, [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)])

    def test_coleira_de_distancia_continua(self):
        self.assertEqual(self.sys_._leash_reason(self.ai, 21), "distancia")
        self.ai.aggroed_by_damage = True
        self.assertEqual(self.sys_._leash_reason(self.ai, 21), "")


class TestVoltaPelaTrilha(unittest.TestCase):
    """Integração: mob arrastado junto com o alvo pra longe do spawn,
    desiste pelos passos e volta SÓ pela trilha (A* quebrado de propósito)."""

    def setUp(self):
        self.ws = make_world_server()
        self.p = spawn_player(self.ws, "p_tr", 128, 392, class_id="xama", hp=500000)
        run_ticks(self.ws, 300)
        self.bundle = self.ws._map_bundles["maps/map_1.csv"]
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
        self.mtm = self.ws.world.get_component(self.mob, TileMovement)
        ip = self.ws.world.get_component(self.mob, InitialPosition)
        self.spawn = (int(ip.x // 32), int(ip.y // 32))
        for m in list(self.ws._mob_eids):          # só este mob na região
            if m == self.mob or self.ws.world.get_component(m, AIControlled) is None:
                continue
            t = self.ws.world.get_component(m, TileMovement)
            if t and chebyshev(t.current_tile_x, t.current_tile_y, *self.spawn) <= 45:
                self.ws.world.remove_entity(m)
                self.ws._mob_eids.discard(m)
        # Rota andável real a partir do spawn até ~22 tiles (A* sem limites)
        tmap = self.ws.world.get_component(self.bundle.tilemap_entity, Tilemap)
        self.route = None
        for r in range(22, 30):
            for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r)):
                tx, ty = self.spawn[0] + dx, self.spawn[1] + dy
                if not (0 <= tx < tmap.map_width_tiles and 0 <= ty < tmap.map_height_tiles):
                    continue
                if tmap.tile_matrix[ty][tx].is_solid:
                    continue
                p = self.bundle.pathfinding.find_path(self.spawn, (tx, ty),
                                                      max_nodes=20000, manhattan_limit=None)
                if p and len(p) >= 20:
                    self.route = [self.spawn] + p
                    break
            if self.route:
                break
        self.assertIsNotNone(self.route, "sem rota de teste a partir do spawn")

    def test_desiste_por_passos_e_volta_pela_trilha_sem_astar(self):
        # Agro: alvo encostado no mob, no spawn
        snap_to_tile(self.ws.world, self.mob, *self.route[0])
        snap_to_tile(self.ws.world, self.p, *self.route[1])
        for _ in range(40):
            run_ticks(self.ws, 1)
        self.assertIn(self.ai.state, ("ATTACKING", "CHASING", "AGGRO_DELAY"))
        # Arrasta mob + alvo pela rota (mob 1 tile atrás do alvo)
        gave_up_at = None
        for i in range(1, len(self.route) - 1):
            snap_to_tile(self.ws.world, self.mob, *self.route[i])
            snap_to_tile(self.ws.world, self.p, *self.route[i + 1])
            run_ticks(self.ws, 1)
            if self.ai.state == "RETURNING":
                gave_up_at = i
                break
        self.assertIsNotNone(gave_up_at, "mob deveria desistir pela coleira de passos")
        far = chebyshev(self.mtm.current_tile_x, self.mtm.current_tile_y, *self.spawn)
        self.assertLessEqual(far, EnemyAISystem.FREE_CHASE_RADIUS_TILES
                             + EnemyAISystem.MAX_CHASE_STEPS_OUTSIDE + 1)
        # Alvo some daqui; A* quebrado → só a trilha pode levá-lo pra casa
        snap_to_tile(self.ws.world, self.p, 128, 392)
        self.bundle.pathfinding.find_path = lambda *a, **k: None
        for _ in range(30 * 40):
            run_ticks(self.ws, 1)
            if self.ai.state == "IDLE":
                break
        self.assertEqual(self.ai.state, "IDLE", "mob deveria ter voltado pra casa pela trilha")
        self.assertLessEqual(chebyshev(self.mtm.current_tile_x, self.mtm.current_tile_y,
                                       *self.spawn), 2)


if __name__ == "__main__":
    unittest.main()
