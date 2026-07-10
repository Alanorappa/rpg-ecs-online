"""
tests/test_map_filter.py
Testa as correções P1 (occupied tiles filtradas por mapa) e P2 (predicate fix)
nas classes TileValidationSystem, EnemyAISystem, EnemyAbilitySystem e SpawnZoneSystem.

Regras testadas:
- P2: entidade SEM MapLocation deve ser EXCLUÍDA quando map_filter está ativo
- P2: entidade com MapLocation ERRADO deve ser EXCLUÍDA
- P1: occupied tiles só incluem entidades do mesmo mapa
- Spawn: mob criado por SpawnZoneSystem recebe MapLocation do bundle
- Integração: timer de cooldown de ataque decrementa 1× por tick (não 3×)

Uso:
    py -3.10 -m pytest tests/test_map_filter.py -v
"""
import os, sys, asyncio, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from engine.world import World
from engine.components import TileMovement, MapLocation
from ui.systems import TileValidationSystem, EnemyAISystem
from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob


MAP_A = "maps/map_1.csv"
MAP_B = "maps/map_cave_west.csv"
DT    = 0.05   # dt padrão de um tick


def _world_with_entities():
    """Cria um World com 3 entidades:
       e_a  — TileMovement(5,5) + MapLocation(MAP_A)
       e_b  — TileMovement(6,6) + MapLocation(MAP_B)
       e_no — TileMovement(7,7) — sem MapLocation
    Retorna (world, e_a, e_b, e_no).
    """
    w = World()
    e_a = w.create_entity()
    w.add_component(e_a, TileMovement(current_tile_x=5, current_tile_y=5))
    w.add_component(e_a, MapLocation(MAP_A))

    e_b = w.create_entity()
    w.add_component(e_b, TileMovement(current_tile_x=6, current_tile_y=6))
    w.add_component(e_b, MapLocation(MAP_B))

    e_no = w.create_entity()
    w.add_component(e_no, TileMovement(current_tile_x=7, current_tile_y=7))

    return w, e_a, e_b, e_no


# ─────────────────────────────────────────────────────────────────────────────
# P2 — TileValidationSystem
# ─────────────────────────────────────────────────────────────────────────────

class TestTileValidationMapFilter(unittest.TestCase):

    def test_excludes_entity_without_map_location(self):
        """Entidade sem MapLocation não entra no cache _occupied quando map_filter ativo."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = TileValidationSystem(w, map_filter=MAP_A)
        sys.update()
        self.assertIn((5, 5), sys._occupied,  "mapa correto deveria estar ocupado")
        self.assertNotIn((7, 7), sys._occupied, "sem MapLocation deve ser excluído")

    def test_excludes_entity_wrong_map(self):
        """Entidade com MapLocation errado não entra no cache _occupied."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = TileValidationSystem(w, map_filter=MAP_A)
        sys.update()
        self.assertNotIn((6, 6), sys._occupied, "mapa errado deve ser excluído")

    def test_no_filter_includes_all(self):
        """Sem map_filter, todas as entidades entram no cache (comportamento offline)."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = TileValidationSystem(w, map_filter="")
        sys.update()
        self.assertIn((5, 5), sys._occupied)
        self.assertIn((6, 6), sys._occupied)
        self.assertIn((7, 7), sys._occupied)


# ─────────────────────────────────────────────────────────────────────────────
# P1 — EnemyAISystem._get_occupied_tiles
# ─────────────────────────────────────────────────────────────────────────────

class TestEnemyAIOccupiedTiles(unittest.TestCase):

    def test_occupied_filtered_by_map(self):
        """_get_occupied_tiles só inclui tiles de entidades do mapa correto."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = EnemyAISystem(w, map_filter=MAP_A)
        occupied = sys._get_occupied_tiles()
        self.assertIn((5, 5), occupied,     "tile do mapa correto ausente")
        self.assertNotIn((6, 6), occupied,  "tile de mapa errado presente")
        self.assertNotIn((7, 7), occupied,  "tile sem MapLocation presente")

    def test_occupied_no_filter_includes_all(self):
        """Sem map_filter, _get_occupied_tiles inclui todas as entidades."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = EnemyAISystem(w, map_filter="")
        occupied = sys._get_occupied_tiles()
        self.assertIn((5, 5), occupied)
        self.assertIn((6, 6), occupied)
        self.assertIn((7, 7), occupied)

    def test_occupied_except_entity_id_respected(self):
        """except_entity_id deve excluir o próprio mob do conjunto (comportamento existente)."""
        w, e_a, e_b, e_no = _world_with_entities()
        sys = EnemyAISystem(w, map_filter=MAP_A)
        occupied = sys._get_occupied_tiles(except_entity_id=e_a)
        self.assertNotIn((5, 5), occupied, "próprio mob não deveria estar em occupied")

    def test_occupied_moving_entity_uses_target_tile(self):
        """Entidade em movimento contribui com target_tile, não current_tile."""
        w = World()
        e = w.create_entity()
        w.add_component(e, TileMovement(
            current_tile_x=5, current_tile_y=5,
            target_tile_x=8, target_tile_y=8,
            is_moving=True,
        ))
        w.add_component(e, MapLocation(MAP_A))
        sys = EnemyAISystem(w, map_filter=MAP_A)
        occupied = sys._get_occupied_tiles()
        self.assertNotIn((5, 5), occupied, "current_tile não deveria ser bloqueador durante movimento")
        self.assertIn((8, 8), occupied,    "target_tile deveria ser o bloqueador")


# ─────────────────────────────────────────────────────────────────────────────
# Integração — mobs spawnam com MapLocation e timer de ataque decrementa 1×
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnedMobHasMapLocation(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_spawned_mobs_have_map_location(self):
        """Todos os mobs criados por SpawnZoneSystem devem ter MapLocation."""
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 60)   # ~3s para spawns iniciais
        self.assertGreater(len(self.ws._mob_eids), 0, "nenhum mob spawnado")
        for eid in self.ws._mob_eids:
            ml = self.ws.world.get_component(eid, MapLocation)
            self.assertIsNotNone(ml, f"mob {eid} não tem MapLocation após spawn")

    def test_spawned_mob_map_location_is_a_loaded_map(self):
        """MapLocation de cada mob deve apontar para um mapa carregado no servidor."""
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 60)
        loaded_maps = set(self.ws._map_bundles.keys())
        for eid in self.ws._mob_eids:
            ml = self.ws.world.get_component(eid, MapLocation)
            if ml is None:
                continue
            self.assertIn(ml.map_file, loaded_maps,
                          f"mob {eid} tem MapLocation desconhecido: {ml.map_file}")


class TestAttackCooldownDecrementedOnce(unittest.TestCase):
    """Garante que o timer de ataque decrementa 1× por tick — não 3× (bug pré-fix).

    Antes da correção P2 + spawn fix, mobs sem MapLocation eram processados
    pelos 3 EnemyAISystems (um por bundle: map_1, cave_west, cave_east),
    cada um decrementando attack_cooldown_timer em dt.  O resultado era
    velocidade de ataque 3× maior que a configurada.
    """

    def setUp(self):
        self.ws = make_world_server()

    def _first_ai_mob_on_map1(self):
        from engine.components import AIControlled
        for eid in self.ws._mob_eids:
            ml  = self.ws.world.get_component(eid, MapLocation)
            ai  = self.ws.world.get_component(eid, AIControlled)
            if ai and ml and ml.map_file == MAP_A:
                return eid
        return None

    def test_cooldown_decrements_once_per_tick(self):
        """attack_cooldown_timer diminui em dt (não 3×dt) por tick."""
        from engine.components import CombatStats
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 60)   # spawna mobs

        mob_eid = self._first_ai_mob_on_map1()
        self.assertIsNotNone(mob_eid, "nenhum mob com AI no map_1")

        cs = self.ws.world.get_component(mob_eid, CombatStats)
        self.assertIsNotNone(cs)

        # Força cooldown alto para garantir que o mob não ataque neste tick
        FIXED_CD = 10.0
        cs.attack_cooldown_timer = FIXED_CD

        async def _one_tick():
            self.ws._tick(DT)

        asyncio.run(_one_tick())

        expected   = round(FIXED_CD - DT, 6)
        actual     = round(cs.attack_cooldown_timer, 6)
        triple_dec = round(FIXED_CD - 3 * DT, 6)

        self.assertAlmostEqual(actual, expected, places=4,
            msg=f"cooldown deveria ser {expected} (1×dt), mas foi {actual} "
                f"(seria {triple_dec} com o bug 3×dt)")
        self.assertNotAlmostEqual(actual, triple_dec, places=4,
            msg="cooldown parece estar decrementando 3× — bug P2 ainda ativo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
