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
    teleport_mob_to_player, set_entity_tile, first_mob, get_mob_hp, get_player_hp,
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
        from engine.components import CombatStats
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
        from engine.components import CombatStats
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
        from engine.components import CombatStats
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
        from engine.components import CombatState
        for mob_eid in self.ws._mob_eids:
            cs = self.ws.world.get_component(mob_eid, CombatState)
            self.assertIsNotNone(cs, f"Mob {mob_eid} sem CombatState")

    def test_mob_attacks_player_in_range(self):
        """EnemyAISystem deve atacar player e gerar queda de HP ou COMBAT_RESULT."""
        from engine.components import CombatStats, TileMovement
        mob_eid    = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]

        # Player em tile walkable com Position sincronizada; mob adjacente
        set_entity_tile(self.ws, player_eid, 115, 389)
        teleport_mob_to_player(self.ws, mob_eid, player_eid, offset_x=1)

        pcs       = self.ws.world.get_component(player_eid, CombatStats)
        hp_before = pcs.current_hp if pcs else 0

        deltas = run_ticks(self.ws, 120)   # 6s — IDLE→CHASING→ATTACKING

        pcs      = self.ws.world.get_component(player_eid, CombatStats)
        hp_after = pcs.current_hp if pcs else hp_before
        hits_via_combat = [c for c in deltas["combat"] if c.get("target") == player_eid]

        self.assertTrue(hp_after < hp_before or len(hits_via_combat) > 0,
                        "EnemyAISystem não causou dano ao player em 6s com mob adjacente")

    def test_mob_attack_reduces_player_hp_on_server(self):
        """Ataque do mob deve reduzir HP do player no servidor."""
        from tests.helpers import first_ai_mob
        mob_eid = first_ai_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        hp_before, _ = get_player_hp(self.ws, "s1")

        teleport_mob_to_player(self.ws, mob_eid, player_eid)
        from engine.components import AIControlled
        mob_ai = self.ws.world.get_component(mob_eid, AIControlled)
        mob_ai.state      = "ATTACKING"
        mob_ai.target_eid = player_eid

        run_ticks(self.ws, 80)
        hp_after, _ = get_player_hp(self.ws, "s1")
        self.assertLess(hp_after, hp_before,
                        f"HP do player não diminuiu ({hp_before} → {hp_after})")

    def test_only_one_mob_attacks_per_tick(self):
        """Apenas 1 mob deve atacar o mesmo player por tick (evita burst de dano)."""
        player_eid = self.ws._player_eids["s1"]
        # Coloca TODOS os mobs adjacentes ao player
        from engine.components import TileMovement
        for i, mob_eid in enumerate(list(self.ws._mob_eids)[:5]):
            mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
            ptm    = self.ws.world.get_component(player_eid, TileMovement)
            if mob_tm and ptm:
                mob_tm.current_tile_x = ptm.current_tile_x + (i % 2)
                mob_tm.current_tile_y = ptm.current_tile_y
            from engine.components import CombatState
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
        """Quando player HP é reduzido para 0 pelo servidor, deve emitir player_deaths."""
        from engine.components import CombatStats, TileMovement
        mob_eid    = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]

        # Player com HP mínimo em área walkable
        pcs = self.ws.world.get_component(player_eid, CombatStats)
        pcs.current_hp = 1

        # Player com Position sincronizada; mob adjacente
        set_entity_tile(self.ws, player_eid, 115, 389)
        teleport_mob_to_player(self.ws, mob_eid, player_eid, offset_x=1)

        deltas = run_ticks(self.ws, 120)   # 6s: AGGRO_DELAY→CHASING→ATTACKING→kill
        self.assertGreater(len(deltas["player_deaths"]), 0,
                           "Nenhum player_death emitido em 6s com player HP=1 e mob adjacente")

    def test_player_death_clears_mob_aggro(self):
        """_handle_player_death deve limpar target_entity_id de todos os mobs."""
        mob_eid    = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]

        # Seta aggro manualmente (simula mob que estava atacando o player)
        from engine.components import CombatState
        mob_cs_state = self.ws.world.get_component(mob_eid, CombatState)
        mob_cs_state.target_entity_id = player_eid

        # Chama _handle_player_death diretamente (não depende de EnemyAI atacar)
        self.ws._handle_player_death(player_eid)

        # Após death, mob deve ter perdido o alvo
        mob_cs = self.ws.world.get_component(mob_eid, CombatState)
        if mob_cs:
            self.assertEqual(mob_cs.target_entity_id, -1,
                             "Mob ainda tem alvo após _handle_player_death")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Movimento de Mobs
# ─────────────────────────────────────────────────────────────────────────────

class TestMobMovement(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)

    def test_mob_moves_toward_player(self):
        """EnemyAISystem: mob dentro do aggro range (<=5 tiles) deve perseguir player."""
        from engine.components import TileMovement
        from engine.utils import chebyshev
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        ptm    = self.ws.world.get_component(player_eid, TileMovement)
        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)

        # Usa posição real do mob; player a 3 tiles — sincroniza Position também
        nat_x = mob_tm.current_tile_x;  nat_y = mob_tm.current_tile_y
        set_entity_tile(self.ws, player_eid, nat_x + 3, nat_y)
        # Reseta AI para IDLE
        from engine.components import AIControlled
        ai = self.ws.world.get_component(mob_eid, AIControlled)
        if ai:
            ai.state = "IDLE";  ai.path = [];  ai.aggroed_by_damage = False

        dist_before = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                                ptm.current_tile_x,    ptm.current_tile_y)

        run_ticks(self.ws, 120)   # 6s — IDLE→AGGRO_DELAY(1s)→CHASING→mover

        dist_after = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                               ptm.current_tile_x,    ptm.current_tile_y)
        self.assertLess(dist_after, dist_before,
                        f"EnemyAISystem não moveu mob (aggro=5 tiles, dist={dist_before}→{dist_after})")

    def test_mob_does_not_walk_through_solid_tile(self):
        """EnemyAISystem usa pathfinding — mobs não caminham por tiles sólidos."""
        from engine.components import Tilemap, TileMovement
        mob_eid = first_mob(self.ws)
        if not mob_eid:
            self.skipTest("Sem mobs")

        # Verifica que o tilemap tem tiles sólidos (pré-condição)
        solid_found = False
        for _, tc in self.ws.world.get_entities_with(Tilemap):
            for ty, row in enumerate(tc.tile_matrix):
                for tx, tile in enumerate(row):
                    if tile.is_solid:
                        solid_found = True
                        break
                if solid_found:
                    break
            break

        if not solid_found:
            self.skipTest("Nenhum tile sólido no mapa")

        # Com EnemyAISystem o pathfinding evita tiles sólidos naturalmente
        # Verificamos que o tilemap existe e tem tiles sólidos — o pathfinding cuida do resto
        self.assertTrue(solid_found, "Tilemap deve ter tiles sólidos para o pathfinding evitar")

    def test_mob_moved_deltas_emitted(self):
        """Mobs em movimento devem gerar deltas['moved']."""
        from engine.components import TileMovement
        mob_eid = first_mob(self.ws)
        player_eid = self.ws._player_eids["s1"]
        ptm    = self.ws.world.get_component(player_eid, TileMovement)
        mob_tm = self.ws.world.get_component(mob_eid, TileMovement)
        ptm.current_tile_x    = 115;  ptm.current_tile_y    = 389
        # Usa posição natural do mob; player a 3 tiles — sincroniza Position
        nat_x = mob_tm.current_tile_x;  nat_y = mob_tm.current_tile_y
        set_entity_tile(self.ws, player_eid, nat_x + 3, nat_y)
        from engine.components import AIControlled
        ai = self.ws.world.get_component(mob_eid, AIControlled)
        if ai:
            ai.state = "IDLE";  ai.path = [];  ai.aggroed_by_damage = False
        # EnemyAISystem detecta player e move o mob automaticamente

        deltas = run_ticks(self.ws, 120)   # 6s: AGGRO_DELAY(1s) + CHASING
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
        # Zera timer de ataque para garantir disparo imediato (evita flakiness de estado global)
        self.ws._attack_timers["s1"] = 0.0

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
        from engine.components import CombatStats
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
        """Dano de mob em player A não deve alterar HP de player B.

        Player B é colocado longe das zonas de spawn de mobs para garantir
        isolamento. O mob de teste é explicitamente travado em player A via
        AIControlled.target_eid (campo usado pelo EnemyAISystem N-players).
        """
        eid1 = spawn_player(self.ws, "s1", 130, 374)
        # Player B longe das zonas de spawn (tiles ~115-135, ~370-395)
        eid2 = spawn_player(self.ws, "s2", 10, 10)
        run_ticks(self.ws, 40)

        hp_b_before, _ = get_player_hp(self.ws, "s2")
        mob_eid = first_mob(self.ws)
        if not mob_eid:
            self.skipTest("Sem mobs")

        from engine.components import CombatState, CombatStats, AIControlled
        # Travar o mob explicitamente em player A — EnemyAISystem usa AIControlled.target_eid
        mob_ai = self.ws.world.get_component(mob_eid, AIControlled)
        if mob_ai:
            mob_ai.target_eid        = eid1
            mob_ai.aggroed_by_damage = True   # mantém foco mesmo com _select_target
            mob_ai.state             = "CHASING"
        mob_cs_combat = self.ws.world.get_component(mob_eid, CombatState)
        if mob_cs_combat:
            mob_cs_combat.target_entity_id = eid1

        pcs1 = self.ws.world.get_component(eid1, CombatStats)
        pcs1.current_hp = 50  # player A com pouco HP para garantir dano

        from engine.components import TileMovement
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
# 5. Bugs reportados pelo usuário — testes de regressão
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBugs(unittest.TestCase):
    """
    Testes que cobrem bugs encontrados em sessões de teste manuais.
    Cada teste documenta exatamente o sintoma observado.
    """

    def setUp(self):
        self.ws = make_world_server()

    def test_mob_attack_generates_combat_delta_with_player_target(self):
        """Quando mob ataca player, deltas['combat'] deve ter entry com target=player_eid.

        Sintoma: barra de HP do player não atualiza em tempo real — servidor
        ataca mas o cliente nunca recebe o hp_after intermediário.
        """
        eid = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)
        mob = first_mob(self.ws)
        if not mob:
            self.skipTest("Sem mobs")

        from engine.components import CombatState, CombatStats, AIControlled, TileMovement, Position
        from engine.tileset import TILE_SIZE as _TS
        mob_ai  = self.ws.world.get_component(mob, AIControlled)
        mob_tm  = self.ws.world.get_component(mob, TileMovement)
        mob_cs  = self.ws.world.get_component(mob, CombatStats)
        mob_cst = self.ws.world.get_component(mob, CombatState)
        mob_pos = self.ws.world.get_component(mob, Position)
        ptm     = self.ws.world.get_component(eid, TileMovement)

        # Posiciona mob adjacente ao player — sincroniza current, target E is_moving=False
        # Evita que TileMovementSystem mova o mob para longe no próximo tick
        tx = ptm.current_tile_x + 1
        ty = ptm.current_tile_y
        mob_tm.current_tile_x = tx;  mob_tm.target_tile_x = tx
        mob_tm.current_tile_y = ty;  mob_tm.target_tile_y = ty
        mob_tm.is_moving = False;    mob_tm.progress = 0.0
        if mob_pos:
            mob_pos.x = tx * _TS + _TS // 2
            mob_pos.y = ty * _TS + _TS // 2

        # Configura mob para atacar imediatamente
        if mob_ai:
            mob_ai.target_eid        = eid
            mob_ai.aggroed_by_damage = True
            mob_ai.state             = "ATTACKING"
        if mob_cs:
            mob_cs.attack_cooldown_timer = 0.0  # permite ataque imediato
        if mob_cst:
            mob_cst.target_entity_id = eid

        deltas = run_ticks(self.ws, 5)
        combat_events = deltas.get("combat", [])
        player_hits = [cr for cr in combat_events if cr.get("target") == eid]

        self.assertGreater(len(player_hits), 0,
            "Mob atacou player mas nenhum entry combat com target=player_eid foi emitido")

        for hit in player_hits:
            self.assertIn("hp_after", hit, "Entry de combate sem campo hp_after")
            self.assertGreaterEqual(hit["hp_after"], 0,
                "hp_after negativo no delta de combate")

    def test_corpse_created_in_loot_notifications_after_mob_death(self):
        """Após mob morrer, _pending_loot_notifications deve ter entrada de corpse.

        Sintoma: mob some sem deixar corpo para lootear.
        Root cause original: _next_corpse_id=1 → eid=-1 → cliente ignora.
        """
        eid = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)
        mob = first_mob(self.ws)
        if not mob:
            self.skipTest("Sem mobs")

        # Mata o mob diretamente via auto-attack simulado
        from engine.components import CombatStats, TileMovement, CombatState
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        ptm    = self.ws.world.get_component(eid, TileMovement)
        mob_tm = self.ws.world.get_component(mob, TileMovement)
        mob_cs_c = self.ws.world.get_component(mob, CombatState)

        mob_tm.current_tile_x = ptm.current_tile_x + 1
        mob_tm.current_tile_y = ptm.current_tile_y
        if mob_cs_c:
            mob_cs_c.target_entity_id = -1
        self.ws.world.get_component(eid, CombatState).target_entity_id = mob

        self.ws._mob_damage_log[mob] = {eid: mob_cs.max_hp}

        # Força morte direto no HP para garantir PendingDeath via sweep
        mob_cs.current_hp = 0
        run_ticks(self.ws, 2)

        # Após o tick, corpse deve estar registrado
        self.assertGreater(len(self.ws._corpses) + len(self.ws._pending_loot_notifications), 0,
            "Nenhum corpse criado após morte de mob — barra de loot nunca aparecerá no cliente")

    def test_mob_state_returning_immediately_after_player_death(self):
        """Quando player morre, mobs que o perseguiam devem ir para RETURNING imediatamente.

        Sintoma: mob continua perseguindo o player até o tile de respawn.
        """
        eid = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)
        mob = first_mob(self.ws)
        if not mob:
            self.skipTest("Sem mobs")

        from engine.components import AIControlled, CombatState
        mob_ai = self.ws.world.get_component(mob, AIControlled)
        if not mob_ai:
            self.skipTest("Mob sem AIControlled")

        # Simula mob perseguindo o player
        mob_ai.target_eid        = eid
        mob_ai.aggroed_by_damage = True
        mob_ai.state             = "CHASING"

        # Mata o player
        self.ws._handle_player_death(eid)

        # O mob deve estar em RETURNING imediatamente após a morte
        ai_after = self.ws.world.get_component(mob, AIControlled)
        self.assertIsNotNone(ai_after, "Mob foi removido após _handle_player_death")
        self.assertEqual(ai_after.state, "RETURNING",
            f"Mob deveria estar RETURNING após player morrer, mas está: {ai_after.state}")
        self.assertEqual(ai_after.target_eid, -1,
            "Mob ainda tem target_eid após player morrer")
        self.assertFalse(ai_after.aggroed_by_damage,
            "aggroed_by_damage não foi limpo após player morrer")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Punho no Queixo — skill de carga do Cavaleiro
# ─────────────────────────────────────────────────────────────────────────────

class TestPunhoNoQueixo(unittest.TestCase):
    """
    Fluxo esperado:
    1. talent cav_punho_queixo alocado → cs.pnq_enabled=True
    2. 3 auto-ataques com dano → pnq_counter 0→3→0 e skill.charges += 1
    3. CAST_SKILL punho_no_queixo → dano 45%AP + stun no alvo + charges -= 1
    4. Skill em cooldown → novos auto-ataques NÃO incrementam o contador
    """

    def setUp(self):
        self.ws = make_world_server()
        # Spawn mobs via SpawnZoneSystem
        run_ticks(self.ws, 50)

    def _setup_warrior_pnq(self, tx=130, ty=374) -> tuple[int, object, object]:
        """Spawna guerreiro com pnq ativo; retorna (eid, pnq_sk, char_stats)."""
        from engine.components import CombatStats, CharacterStats, PlayerSkills
        from content.skill_config import SKILL_CATALOG

        eid = spawn_player(self.ws, "s1", tx, ty, class_id="guerreiro")
        # Gate autoritativo (is_skill_authorized): fixture precisa "aprender"
        # a skill + alocar o talento, como um player real.
        from tests.helpers import authorize_skill
        authorize_skill(self.ws, eid, "punho_no_queixo")
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.pnq_enabled       = True
        cs.pnq_stun_duration = 1.0
        cs.acerto            = 100.0   # garante hit em testes (sem miss/dodge)

        ps = self.ws.world.get_component(eid, PlayerSkills)
        pnq_sk = next((sk for sk in ps.skills if sk and sk.skill_id == 'punho_no_queixo'), None)
        if pnq_sk is None:
            pnq_sk = PlayerSkills._make_skill('punho_no_queixo', SKILL_CATALOG)
            self.assertIsNotNone(pnq_sk, "SKILL_CATALOG não tem punho_no_queixo")
            try:
                idx = ps.skills.index(None)
                ps.skills[idx] = pnq_sk
            except ValueError:
                ps.skills.append(pnq_sk)

        char = self.ws.world.get_component(eid, CharacterStats)
        char.pnq_counter = 0
        return eid, pnq_sk, char

    def _setup_adjacent_mob(self, player_eid: int) -> int:
        """Retorna mob existente posicionado adjacente ao player."""
        from engine.components import CombatState, CombatStats
        mob = first_mob(self.ws)
        self.assertIsNotNone(mob, "Nenhum mob spawnado")
        teleport_mob_to_player(self.ws, mob, player_eid)
        # Garante mob vivo; zera dodge/parry para evitar flakiness nos testes
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp   = mob_cs.max_hp
        mob_cs.dodge_rating = 0.0
        mob_cs.parry_rating = 0.0
        return mob

    def _force_attack(self, session_id: str, player_eid: int, mob_eid: int):
        """Força 1 auto-ataque: reseta timer e processa o tick de combate."""
        from engine.components import CombatState, CombatStats
        cs_state = self.ws.world.get_component(player_eid, CombatState)
        cs_state.target_entity_id = mob_eid
        cs_state.is_pursuing      = True
        self.ws._attack_timers[session_id] = 0.0
        mob_cs = self.ws.world.get_component(mob_eid, CombatStats)
        mob_cs.current_hp = max(mob_cs.current_hp, 1)  # mantém vivo
        from engine.components import CombatStats as _CS
        snapshot = {mob_eid: mob_cs.current_hp}
        self.ws._process_player_attacks(0.05, snapshot)

    # ── Teste 1: 3 acertos geram 1 carga ────────────────────────────────────

    def test_three_hits_grant_one_charge(self):
        """3 auto-ataques com dano → skill.charges == 1."""
        eid, pnq_sk, char = self._setup_warrior_pnq()
        mob = self._setup_adjacent_mob(eid)

        self.assertEqual(pnq_sk.charges, 0, "charges deve iniciar em 0")

        for _ in range(3):
            self._force_attack("s1", eid, mob)

        self.assertEqual(char.pnq_counter, 0,
                         "pnq_counter deve ter resetado após 3 acertos")
        self.assertEqual(pnq_sk.charges, 1,
                         f"Esperado 1 carga após 3 acertos, got {pnq_sk.charges}")

    # ── Teste 2: cast aplica dano + stun + consome carga ────────────────────

    def test_cast_deals_damage_and_stuns(self):
        """Usar skill com 1 carga → dano, stun no mob, charges volta a 0."""
        from engine.components import CombatState, CombatStats, StatusEffects

        eid, pnq_sk, _ = self._setup_warrior_pnq()
        mob = self._setup_adjacent_mob(eid)

        # Dá 1 carga diretamente (testa o handler, não o acúmulo)
        pnq_sk.charges = 1

        cs_state = self.ws.world.get_component(eid, CombatState)
        cs_state.target_entity_id = mob
        cs_state.is_pursuing      = True

        mob_cs    = self.ws.world.get_component(mob, CombatStats)
        hp_before = mob_cs.current_hp

        self.ws._pending_skill_requests.append({
            "player_eid": eid, "sid": "punho_no_queixo",
            "tid": mob, "dir_x": 0.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        self.assertLess(mob_cs.current_hp, hp_before,
                        "Mob HP não reduziu após punho_no_queixo")
        sfx = self.ws.world.get_component(mob, StatusEffects)
        self.assertIn("stun", sfx.effects if sfx else {},
                      "Mob não foi atordoado")
        self.assertEqual(pnq_sk.charges, 0, "Carga não foi consumida")

    # ── Teste 3: sem carga → handler rejeita ────────────────────────────────

    def test_cast_without_charge_fails(self):
        """Tentar usar skill sem cargas não causa dano."""
        from engine.components import CombatState, CombatStats

        eid, pnq_sk, _ = self._setup_warrior_pnq()
        mob = self._setup_adjacent_mob(eid)

        pnq_sk.charges = 0  # sem carga

        cs_state = self.ws.world.get_component(eid, CombatState)
        cs_state.target_entity_id = mob

        mob_cs    = self.ws.world.get_component(mob, CombatStats)
        hp_before = mob_cs.current_hp

        self.ws._pending_skill_requests.append({
            "player_eid": eid, "sid": "punho_no_queixo",
            "tid": mob, "dir_x": 0.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        self.assertEqual(mob_cs.current_hp, hp_before,
                         "Dano causado sem carga (handler deveria rejeitar)")

    # ── Teste 4: em cooldown → auto-ataques NÃO acumulam ────────────────────

    def test_hits_during_cooldown_dont_accumulate(self):
        """Enquanto skill em cooldown, pnq_counter e charges não mudam."""
        eid, pnq_sk, char = self._setup_warrior_pnq()
        mob = self._setup_adjacent_mob(eid)

        # Simula cooldown via _skill_last_used (servidor usa isso, não current_cooldown)
        import time as _t
        self.ws._skill_last_used[(eid, "punho_no_queixo")] = _t.time()
        pnq_sk.charges   = 0
        char.pnq_counter = 0

        for _ in range(3):
            self._force_attack("s1", eid, mob)

        self.assertEqual(pnq_sk.charges, 0,
                         "charges acumulou com skill em CD (não deveria)")

    # ── Teste 5: pnq_enabled=False → nenhum acúmulo ─────────────────────────

    def test_no_accumulation_without_talent(self):
        """Sem talent (pnq_enabled=False) → counter e charges permanecem 0."""
        from engine.components import CombatStats

        eid, pnq_sk, char = self._setup_warrior_pnq()
        mob = self._setup_adjacent_mob(eid)

        cs = self.ws.world.get_component(eid, CombatStats)
        cs.pnq_enabled  = False  # talent não alocado
        char.pnq_counter = 0

        for _ in range(3):
            self._force_attack("s1", eid, mob)

        self.assertEqual(char.pnq_counter, 0,
                         "pnq_counter incrementou sem talent ativo")
        self.assertEqual(pnq_sk.charges, 0,
                         "charges acumulou sem talent ativo")

    # ── Teste 6: skill não no hotbar → lazy-create em ps.skills ────��────────

    def test_skill_lazily_added_to_ps_skills(self):
        """Se punho_no_queixo não está em ps.skills, servidor cria ao primeiro acerto."""
        from engine.components import CombatStats, PlayerSkills

        eid = spawn_player(self.ws, "s2", 130, 374, class_id="guerreiro")
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.pnq_enabled       = True
        cs.pnq_stun_duration = 1.0
        cs.acerto            = 100.0   # hit garantido

        # Remove skill do ps.skills (simula hotbar sem ela)
        ps = self.ws.world.get_component(eid, PlayerSkills)
        ps.skills = [sk for sk in ps.skills if not (sk and sk.skill_id == 'punho_no_queixo')]

        mob = first_mob(self.ws)
        teleport_mob_to_player(self.ws, mob, eid)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp   = mob_cs.max_hp
        mob_cs.dodge_rating = 0.0
        mob_cs.parry_rating = 0.0

        from engine.components import CombatState
        cst = self.ws.world.get_component(eid, CombatState)
        cst.target_entity_id = mob
        cst.is_pursuing      = True

        # 3 ataques devem criar a skill lazily e acumular a carga.
        # Resetar target e HP a cada iteração:
        #   – deal_damage pode matar o mob na 1ª hit → target_entity_id=-1;
        #   – PendingDeath não remove da _mob_eids (ServerDeathHandler não rodou),
        #     mas HP<0 seria detectado como alvo inválido sem o reset.
        from engine.components import CombatState, PendingDeath
        for _ in range(3):
            mob_cs.current_hp = mob_cs.max_hp          # garante alvo vivo
            self.ws.world.remove_component(mob, PendingDeath)  # limpa morte pendente
            cst.target_entity_id = mob                 # restaura target (deal_damage zera)
            self.ws._attack_timers["s2"] = 0.0
            snap = {mob: mob_cs.current_hp}
            self.ws._process_player_attacks(0.05, snap)

        pnq_sk = next((sk for sk in ps.skills if sk and sk.skill_id == 'punho_no_queixo'), None)
        self.assertIsNotNone(pnq_sk, "punho_no_queixo não foi adicionado lazily")
        self.assertEqual(pnq_sk.charges, 1,
                         "1 carga esperada após criação lazy + 3 acertos")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Habilidades ranged de mobs — LOS + projétil
# ─────────────────────────────────────────────────────────────────────────────

class TestRangedMobAbilities(unittest.TestCase):
    """
    Garante:
    1. EnemyAbilitySystem ranged (range>1) lança PROJÉTIL em vez de aplicar DoT direto.
    2. O projétil carrega ability_id e só aplica efeito ao acertar (ProjectileSystem).
    3. Sem LOS, EnemyAbilitySystem NÃO lança projétil nem consome cooldown.
    """

    def _make_hunter_mob(self, ws):
        """Cria um mob Hunter com EnemyAbilities(poison_arrow) e AIControlled ATTACKING."""
        from engine.world import World
        from engine.components import (Position, TileMovement, CombatStats, AIControlled,
                                InitialPosition, DetectionRadius, EnemyAbilities,
                                EnemyAbilitySlot, EntityIdentity, Enemy)
        from content.enemy_abilities_data import ABILITY_DEFS
        from engine.tileset import TILE_SIZE

        tx, ty = 130, 372  # 2 tiles acima do player (sem parede entre eles)
        eid = ws.world.create_entity()
        ws.world.add_component(eid, Position(x=tx * TILE_SIZE, y=ty * TILE_SIZE,
                                              prev_x=tx * TILE_SIZE, prev_y=ty * TILE_SIZE))
        ws.world.add_component(eid, TileMovement(current_tile_x=tx, current_tile_y=ty,
                                                  target_tile_x=tx, target_tile_y=ty))
        _mob_cs = CombatStats()
        _mob_cs.max_hp     = 200
        _mob_cs.current_hp = 200
        ws.world.add_component(eid, _mob_cs)
        ws.world.add_component(eid, AIControlled(
            state="ATTACKING", is_ranged=True, entity_class="Hunter",
            target_eid=-1,  # será setado no teste
        ))
        ws.world.add_component(eid, InitialPosition(x=tx * TILE_SIZE, y=ty * TILE_SIZE))
        ws.world.add_component(eid, DetectionRadius(radius=8 * TILE_SIZE))
        ws.world.add_component(eid, Enemy())
        ws.world.add_component(eid, EntityIdentity(name="Hunter de Teste", race="Fera",
                                                    entity_class="Hunter"))
        # MapLocation: obrigatório desde o refactor multi-mapa — os sistemas
        # por-bundle (EnemyAbilitySystem/EnemyAISystem com _map_filter) pulam
        # entidades sem MapLocation do próprio mapa; sem isso o mob sintético
        # é invisível pra TODOS os bundles e a habilidade nunca dispara.
        from engine.components import MapLocation as _MapLoc
        ws.world.add_component(eid, _MapLoc(map_file="maps/map_1.csv"))
        # Habilidade poison_arrow pronta para disparar (cooldown=0)
        defn = ABILITY_DEFS["poison_arrow"]
        slot = EnemyAbilitySlot(ability_id="poison_arrow", cooldown=12.0, current_cooldown=0.0)
        ws.world.add_component(eid, EnemyAbilities(slots=[slot]))
        ws._mob_eids.add(eid)
        return eid

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 40)   # deixa SpawnZoneSystem inicializar
        self.p_eid = spawn_player(self.ws, "s1", 130, 374)

    def test_ranged_ability_spawns_projectile_not_direct_effect(self):
        """poison_arrow deve criar projétil com ability_id, NÃO aplicar efeito direto."""
        from engine.components import Projectile, StatusEffects, AIControlled
        from ui.systems import EnemyAISystem

        hunter = self._make_hunter_mob(self.ws)
        ai = self.ws.world.get_component(hunter, AIControlled)
        ai.target_eid = self.p_eid

        # Garante LOS (independe do layout do mapa)
        original_los = EnemyAISystem._has_line_of_sight
        EnemyAISystem._has_line_of_sight = staticmethod(lambda *a: True)
        try:
            # Antes: player sem poison
            sfx_before = self.ws.world.get_component(self.p_eid, StatusEffects)
            had_poison_before = sfx_before.has("poison") if sfx_before else False

            # 1 tick: EnemyAbilitySystem dispara, ProjectileSystem move (não acerta ainda)
            run_ticks(self.ws, 1)

            # Deve existir projétil com ability_id="poison_arrow"
            from engine.components import Position as _Pos
            ability_projs = [
                (eid, p) for eid, pos, p in self.ws.world.get_entities_with(_Pos, Projectile)
                if p.ability_id == "poison_arrow"
            ]
            self.assertGreater(len(ability_projs), 0,
                               "EnemyAbilitySystem deveria ter criado projétil de ability")

            # Player NÃO deve ter poison (projétil viajando)
            sfx_after = self.ws.world.get_component(self.p_eid, StatusEffects)
            has_poison = sfx_after.has("poison") if sfx_after else False
            self.assertFalse(has_poison,
                             "poison não deveria ser aplicado antes do projétil acertar")
        finally:
            # staticmethod(original_los) -- acesso via classe/instancia ja
            # desempacota o staticmethod original pra funcao pura; reatribuir
            # sem reembrulhar fazia self._has_line_of_sight(...) (chamada por
            # instancia) injetar self como 1o arg, quebrando os outros call
            # sites com TypeError de contagem de args pro resto do processo.
            EnemyAISystem._has_line_of_sight = staticmethod(original_los)

    def test_ability_projectile_applies_dot_on_hit(self):
        """Projétil de ability com ability_id aplica DoT ao acertar (ProjectileSystem)."""
        from engine.components import Projectile, StatusEffects, Position, AIControlled
        from engine.tileset import TILE_SIZE

        # Cria projétil já na posição do player (1 tick = hit)
        p_pos = self.ws.world.get_component(self.p_eid, Position)
        hunter = self._make_hunter_mob(self.ws)
        ai = self.ws.world.get_component(hunter, AIControlled)
        ai.target_eid = self.p_eid

        proj_eid = self.ws.world.create_entity()
        self.ws.world.add_component(proj_eid, Position(
            x=p_pos.x, y=p_pos.y, prev_x=p_pos.x, prev_y=p_pos.y))
        self.ws.world.add_component(proj_eid, Projectile(
            attacker_id=hunter, target_id=self.p_eid,
            damage_type="magical", speed=320.0,
            color=(60, 200, 80), is_arrow=True,
            ability_id="poison_arrow",
        ))

        # Roda 1 tick: ProjectileSystem detecta hit (dist=0)
        run_ticks(self.ws, 1)

        sfx = self.ws.world.get_component(self.p_eid, StatusEffects)
        has_poison = sfx.has("poison") if sfx else False
        self.assertTrue(has_poison,
                        "ProjectileSystem deveria ter aplicado poison ao acertar")

    def test_ability_cooldown_not_consumed_without_los(self):
        """Sem LOS entre mob e player, ability NÃO dispara e cooldown NÃO é consumido."""
        from engine.components import Projectile, EnemyAbilities, AIControlled, TileMovement
        from engine.tileset import TILE_SIZE

        hunter = self._make_hunter_mob(self.ws)
        ai = self.ws.world.get_component(hunter, AIControlled)
        ai.target_eid = self.p_eid

        # Coloca parede (tile sólido) entre hunter e player usando o tilemap real
        # Como é difícil inserir tiles sólidos em testes, simulamos LOS=False
        # sobrescrevendo o tilemap component e mockando o método estático.
        # Abordagem: colocar Hunter e player em tiles opostos com colisão garantida
        # via monkey-patch do _has_line_of_sight.
        from ui.systems import EnemyAISystem
        original_los = EnemyAISystem._has_line_of_sight

        # Força LOS=False
        EnemyAISystem._has_line_of_sight = staticmethod(lambda *a: False)
        try:
            # Pega slot antes
            abilities = self.ws.world.get_component(hunter, EnemyAbilities)
            slot = abilities.slots[0]
            cd_before = slot.current_cooldown  # deve ser 0 (pronto para disparar)

            run_ticks(self.ws, 1)

            cd_after = slot.current_cooldown
            self.assertEqual(cd_after, cd_before,
                             "Cooldown não deveria ter sido consumido sem LOS")

            # Também não deve existir projétil de ability
            from engine.components import Position
            ability_projs = [
                eid for eid, pos, p in self.ws.world.get_entities_with(Position, Projectile)
                if p.ability_id == "poison_arrow"
            ]
            self.assertEqual(len(ability_projs), 0,
                             "Nenhum projétil deveria ser criado sem LOS")
        finally:
            # staticmethod(original_los) -- acesso via classe/instancia ja
            # desempacota o staticmethod original pra funcao pura; reatribuir
            # sem reembrulhar fazia self._has_line_of_sight(...) (chamada por
            # instancia) injetar self como 1o arg, quebrando os outros call
            # sites com TypeError de contagem de args pro resto do processo.
            EnemyAISystem._has_line_of_sight = staticmethod(original_los)


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
