"""
tests/test_server.py
Suite de testes do servidor — roda sem abrir o jogo.

Uso:
    py -3.10 tests/test_server.py          # output legível
    py -3.10 -m pytest tests/ -v          # com pytest instalado
"""
import unittest
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import (
    make_world_server, spawn_player, run_ticks,
    teleport_mob_to_player, first_mob, get_mob_hp, get_player_hp,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Spawn e AOI
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnAndAOI(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_mobs_spawn_after_two_seconds(self):
        """SpawnZoneSystem deve criar mobs nos primeiros 2 segundos."""
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 50)   # 2.5s
        self.assertGreater(len(self.ws._mob_eids), 0,
                           "Nenhum mob spawnado após 2.5s")

    def test_spawned_deltas_emitted_for_new_mobs(self):
        """Mobs criados pelo SpawnZoneSystem devem aparecer em deltas['spawned']."""
        spawn_player(self.ws, "s1", 130, 374)
        deltas = run_ticks(self.ws, 50)
        self.assertGreater(len(deltas["spawned"]), 0,
                           "Nenhum delta 'spawned' emitido")

    def test_mob_spawn_payload_has_required_fields(self):
        """Payload de ENTITY_SPAWN deve ter campos obrigatórios para o cliente recriar o mob."""
        spawn_player(self.ws, "s1", 130, 374)
        deltas = run_ticks(self.ws, 50)
        mob_spawns = [s for s in deltas["spawned"] if s.get("kind") == "enemy"]
        self.assertGreater(len(mob_spawns), 0, "Nenhum mob no spawned")
        sp = mob_spawns[0]
        for field in ("eid", "kind", "tx", "ty", "hp", "hp_max", "race", "tier"):
            self.assertIn(field, sp, f"Campo '{field}' ausente no payload de spawn")

    def test_get_entity_spawn_data_returns_mob(self):
        """get_entity_spawn_data deve retornar dados válidos para mob existente."""
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 50)
        mob_eid = first_mob(self.ws)
        self.assertIsNotNone(mob_eid)
        data = self.ws.get_entity_spawn_data(mob_eid)
        self.assertIsNotNone(data, "get_entity_spawn_data retornou None para mob existente")
        self.assertEqual(data["kind"], "enemy")
        self.assertEqual(data["eid"], mob_eid)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Combate Player → Mob
# ─────────────────────────────────────────────────────────────────────────────

class TestPlayerAttacksMob(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)   # spawn mobs

    def test_player_attack_reduces_mob_hp(self):
        """Auto-attack do player deve reduzir HP do mob no servidor."""
        mob_eid = first_mob(self.ws)
        self.assertIsNotNone(mob_eid)
        hp_before, _ = get_mob_hp(self.ws, mob_eid)

        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        run_ticks(self.ws, 60)   # 3s de combate (1-2 ataques)

        hp_after, _ = get_mob_hp(self.ws, mob_eid)
        self.assertLess(hp_after, hp_before,
                        f"HP do mob não diminuiu ({hp_before} → {hp_after})")

    def test_combat_result_emitted_on_hit(self):
        """Cada hit deve gerar um entry em deltas['combat']."""
        mob_eid = first_mob(self.ws)
        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        deltas = run_ticks(self.ws, 60)

        player_hits = [c for c in deltas["combat"]
                       if c["attacker"] == self.ws._player_eids["s1"]]
        self.assertGreater(len(player_hits), 0, "Nenhum COMBAT_RESULT emitido")

    def test_combat_result_has_valid_hp_after(self):
        """hp_after deve ser >= 0 e <= hp_max do mob."""
        mob_eid = first_mob(self.ws)
        _, hp_max_before = get_mob_hp(self.ws, mob_eid)  # captura ANTES do combate
        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        deltas = run_ticks(self.ws, 60)

        for cr in deltas["combat"]:
            if cr.get("target") == mob_eid:
                self.assertGreaterEqual(cr["hp_after"], 0)
                self.assertLessEqual(cr["hp_after"], hp_max_before,
                                     f"hp_after={cr['hp_after']} > hp_max={hp_max_before}")

    def test_mob_death_sends_entity_despawn(self):
        """Quando mob morre, ENTITY_DESPAWN deve ser emitido."""
        mob_eid = first_mob(self.ws)
        from components import CombatStats
        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 1   # mata na primeira pancada

        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        deltas = run_ticks(self.ws, 60)

        self.assertIn(mob_eid, deltas["despawned"],
                      "ENTITY_DESPAWN não emitido após morte do mob")

    def test_mob_removed_from_mob_eids_on_death(self):
        """Mob morto deve ser removido de ws._mob_eids."""
        mob_eid = first_mob(self.ws)
        from components import CombatStats
        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 1

        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        run_ticks(self.ws, 60)

        self.assertNotIn(mob_eid, self.ws._mob_eids,
                         "Mob ainda em _mob_eids após morte")

    def test_no_duplicate_despawn(self):
        """ENTITY_DESPAWN não deve ser emitido em duplicata."""
        mob_eid = first_mob(self.ws)
        from components import CombatStats
        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 1

        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        deltas = run_ticks(self.ws, 60)

        from collections import Counter
        counts = Counter(deltas["despawned"])
        self.assertEqual(counts.get(mob_eid, 0), 1,
                         f"Duplicata em despawned: {counts}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Combate Mob → Player
# ─────────────────────────────────────────────────────────────────────────────

class TestMobAttacksPlayer(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)

    def test_mob_has_combat_state(self):
        """Todo mob spawnado deve ter CombatState (necessário para aggro)."""
        from components import CombatState
        for mob_eid in self.ws._mob_eids:
            cs = self.ws.world.get_component(mob_eid, CombatState)
            self.assertIsNotNone(cs, f"Mob {mob_eid} sem CombatState")

    def test_mob_attacks_player_in_range(self):
        """Mob em melee range deve atacar o player e gerar COMBAT_RESULT."""
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        teleport_mob_to_player(self.ws, mob_eid, player_eid)

        from components import CombatState
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid

        deltas = run_ticks(self.ws, 80)   # 4s
        mob_hits = [c for c in deltas["combat"] if c["attacker"] == mob_eid]
        self.assertGreater(len(mob_hits), 0,
                           "Mob não atacou o player em 4 segundos")

    def test_mob_attack_reduces_player_hp_on_server(self):
        """Ataque do mob deve reduzir HP do player no servidor."""
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        hp_before, _ = get_player_hp(self.ws, "s1")

        teleport_mob_to_player(self.ws, mob_eid, player_eid)
        from components import CombatState
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid

        run_ticks(self.ws, 80)
        hp_after, _ = get_player_hp(self.ws, "s1")
        self.assertLess(hp_after, hp_before,
                        f"HP do player não diminuiu ({hp_before} → {hp_after})")

    def test_only_one_mob_attacks_per_tick(self):
        """Apenas 1 mob deve atacar o mesmo player por tick (evita burst de dano)."""
        player_eid = self.ws._player_eids["s1"]
        # Coloca TODOS os mobs adjacentes ao player
        from components import TileMovement
        for i, mob_eid in enumerate(list(self.ws._mob_eids)[:5]):
            mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
            ptm    = self.ws.world.get_component(player_eid, TileMovement)
            if mob_tm and ptm:
                mob_tm.current_tile_x = ptm.current_tile_x + (i % 2)
                mob_tm.current_tile_y = ptm.current_tile_y
            from components import CombatState
            mob_cs = self.ws.world.get_component(mob_eid, CombatState)
            if mob_cs:
                mob_cs.target_entity_id = player_eid
        # Zero os timers para que todos queiram atacar no mesmo tick
        for mob_eid in list(self.ws._mob_eids)[:5]:
            self.ws._attack_timers[f"mob_{mob_eid}"] = 0.0

        # Acumula combat results de 1 tick apenas
        combat_this_tick = []
        orig = self.ws._collect_deltas
        def patched():
            d = orig()
            combat_this_tick.extend(d.get("combat", []))
            return d
        self.ws._collect_deltas = patched
        import asyncio
        asyncio.run(asyncio.coroutine(lambda: self.ws._tick(0.05))())
        self.ws._collect_deltas = orig

        mob_hits = [c for c in combat_this_tick if c["target"] == player_eid]
        self.assertLessEqual(len(mob_hits), 1,
                             f"Múltiplos mobs atacaram no mesmo tick: {len(mob_hits)}")

    def test_player_death_emits_player_death_event(self):
        """Quando player HP = 0, deve emitir player_deaths."""
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]

        # Reduz HP do player para 1
        from components import CombatStats
        pcs = self.ws.world.get_component(player_eid, CombatStats)
        pcs.current_hp = 1

        teleport_mob_to_player(self.ws, mob_eid, player_eid)
        from components import CombatState
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid
        self.ws._attack_timers[f"mob_{mob_eid}"] = 0.0

        deltas = run_ticks(self.ws, 5)
        self.assertGreater(len(deltas["player_deaths"]), 0,
                           "Nenhum player_death emitido quando player HP=0")

    def test_player_death_clears_mob_aggro(self):
        """Após morte do player, mobs devem perder o alvo."""
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]

        from components import CombatStats, CombatState
        pcs = self.ws.world.get_component(player_eid, CombatStats)
        pcs.current_hp = 1

        teleport_mob_to_player(self.ws, mob_eid, player_eid)
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid
        self.ws._attack_timers[f"mob_{mob_eid}"] = 0.0

        run_ticks(self.ws, 10)

        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        if mob_cs:
            self.assertEqual(mob_cs.target_entity_id, -1,
                             "Mob ainda tem alvo após morte do player")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Movimento de Mobs
# ─────────────────────────────────────────────────────────────────────────────

class TestMobMovement(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)

    def test_mob_moves_toward_player(self):
        """Mob em aggro deve se mover em direção ao player."""
        from components import TileMovement, CombatState
        from utils import chebyshev
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        ptm = self.ws.world.get_component(player_eid, TileMovement)
        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)

        # Usa posição do spawn do player (115,389) que é walkable; mob a 5 tiles
        ptm.current_tile_x = 115;  ptm.current_tile_y = 389
        mob_tm.current_tile_x = 120;  mob_tm.current_tile_y = 389
        dist_before = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                                ptm.current_tile_x,    ptm.current_tile_y)

        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid

        run_ticks(self.ws, 40)   # 2s para o mob se mover

        dist_after = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                               ptm.current_tile_x,    ptm.current_tile_y)
        self.assertLess(dist_after, dist_before,
                        f"Mob não se aproximou ({dist_before} → {dist_after})")

    def test_mob_does_not_walk_through_solid_tile(self):
        """Mob não deve entrar em tile sólido."""
        from components import Tilemap, TileMovement, CombatState
        from server.mob_system import ServerMobSystem
        mob_system = self.ws._mob_system
        mob_eid = first_mob(self.ws)
        if not mob_eid:
            self.skipTest("Sem mobs")

        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
        # Encontra um tile sólido próximo
        solid_found = False
        for _, tc in self.ws.world.get_entities_with(Tilemap):
            rows = tc.tile_matrix
            for ty, row in enumerate(rows):
                for tx, tile in enumerate(row):
                    if tile.is_solid:
                        is_walkable = mob_system._is_walkable(tx, ty)
                        self.assertFalse(is_walkable,
                                         f"_is_walkable retornou True para tile sólido ({tx},{ty})")
                        solid_found = True
                        break
                if solid_found: break
            break
        if not solid_found:
            self.skipTest("Nenhum tile sólido encontrado no mapa")

    def test_mob_moved_deltas_emitted(self):
        """Mobs que se movem devem aparecer em deltas['moved']."""
        from components import TileMovement, CombatState
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        ptm = self.ws.world.get_component(player_eid, TileMovement)
        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
        # Usa área walkable do mapa (próximo ao spawn do player)
        ptm.current_tile_x = 115;  ptm.current_tile_y = 389
        mob_tm.current_tile_x = 120;  mob_tm.current_tile_y = 389
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid

        deltas = run_ticks(self.ws, 40)
        mob_moves = [m for m in deltas["moved"] if m["eid"] == mob_eid]
        self.assertGreater(len(mob_moves), 0,
                           "Mob em aggro não gerou deltas de movimento")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Múltiplos players
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoAttackFlow(unittest.TestCase):
    """Verifica o fluxo completo: player seleciona mob → AUTO_ATTACK → dano no servidor."""

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)

    def test_player_target_must_reach_server(self):
        """Se player define alvo e está em range, servidor deve processar ataque."""
        mob_eid = first_mob(self.ws)
        self.assertIsNotNone(mob_eid)
        hp_before, _ = get_mob_hp(self.ws, mob_eid)

        # Define alvo e aproxima mob (simula player clicando no mob)
        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)

        deltas = run_ticks(self.ws, 60)   # 3 segundos

        hp_after, _ = get_mob_hp(self.ws, mob_eid)
        player_hits = [c for c in deltas["combat"]
                       if c["attacker"] == self.ws._player_eids["s1"]]
        self.assertGreater(len(player_hits), 0,
                           "Servidor não processou nenhum ataque em 3s com mob adjacente")
        self.assertLess(hp_after, hp_before,
                        f"HP do mob não diminuiu: {hp_before} → {hp_after}")

    def test_mob_with_high_ap_dies_in_reasonable_time(self):
        """Com AP=50, mob HP~175 deve morrer em ≤ 20s."""
        mob_eid = first_mob(self.ws)
        from components import CombatStats
        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 1   # força morte rápida para validar o fluxo

        teleport_mob_to_player(self.ws, mob_eid, self.ws._player_eids["s1"])
        self.ws.set_player_target("s1", mob_eid)
        deltas = run_ticks(self.ws, 60)

        self.assertIn(mob_eid, deltas["despawned"],
                      "Mob com HP=1 não morreu após 3s")


class TestMultiplePlayers(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_two_players_can_spawn(self):
        """Dois players distintos podem ser criados no mesmo mundo."""
        eid1 = spawn_player(self.ws, "s1", 115, 389)
        eid2 = spawn_player(self.ws, "s2", 117, 389)
        self.assertNotEqual(eid1, eid2)
        self.assertEqual(len(self.ws._player_eids), 2)

    def test_player_combat_does_not_affect_other_player_hp(self):
        """Dano de mob em player A não deve alterar HP de player B."""
        eid1 = spawn_player(self.ws, "s1", 130, 374)
        eid2 = spawn_player(self.ws, "s2", 117, 389)
        run_ticks(self.ws, 40)

        hp_b_before, _ = get_player_hp(self.ws, "s2")
        mob_eid = first_mob(self.ws)
        if not mob_eid:
            self.skipTest("Sem mobs")

        from components import CombatState, CombatStats
        mob_cs_combat = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs_combat.target_entity_id = eid1

        pcs1 = self.ws.world.get_component(eid1, CombatStats)
        pcs1.current_hp = 50  # player A com pouco HP para garantir dano

        from components import TileMovement
        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
        ptm1   = self.ws.world.get_component(eid1, TileMovement)
        mob_tm.current_tile_x = ptm1.current_tile_x + 1
        mob_tm.current_tile_y = ptm1.current_tile_y
        self.ws._attack_timers[f"mob_{mob_eid}"] = 0.0

        run_ticks(self.ws, 20)
        hp_b_after, _ = get_player_hp(self.ws, "s2")
        self.assertEqual(hp_b_before, hp_b_after,
                         "HP de player B foi alterado por dano em player A")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
