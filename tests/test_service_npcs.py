"""
tests/test_service_npcs.py — NPCs de serviço (mercador/ferreiro/
treinador/dador-de-missão) ganham combate genérico (Fase 1, 21/07/2026,
pedido do usuário: "todos os NPCs, tenham como default, o mesmo que foi
feito para o Guarda Real"). Mesma infra de `create_combat_npc`/Guarda
Real (`Combatant`+`CombatStats`+`AIControlled`+`Faction`) via
`_build_combat_entity`, mais o componente de capacidade de sempre
(`Merchant`/`Trainer`/`QuestGiver`/`Blacksmith`) — ver
tests/test_faction.py::TestCombatNpcArchetype pro precedente do Guarda
Real. NÃO inclui uso de skill real do jogador (Fase 2, a discutir depois).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile
from engine.components import (
    Combatant, CombatStats, AIControlled, Faction, NPC, Merchant, Blacksmith,
    Trainer, QuestGiver, TileMovement, CombatState,
)
from engine.entity_factory import (
    create_merchant, create_blacksmith, create_quest_giver, create_trainer,
    create_enemy, create_combat_npc,
)
from engine.world_systems import deal_damage


class TestServiceNpcCombateGenerico(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.peid = self.ws._player_eids["s1"]

    def test_merchant_tem_combatant_stats_ai_e_faccao_civis(self):
        eid = create_merchant(self.ws.world, 131, 374, name="Zeca", shop_id="general")
        self.assertIsNotNone(self.ws.world.get_component(eid, Combatant))
        self.assertIsNotNone(self.ws.world.get_component(eid, CombatStats))
        self.assertIsNotNone(self.ws.world.get_component(eid, AIControlled))
        fac = self.ws.world.get_component(eid, Faction)
        self.assertEqual(fac.faction_id, "civis")
        merch = self.ws.world.get_component(eid, Merchant)
        self.assertEqual(merch.shop_id, "general")
        npc = self.ws.world.get_component(eid, NPC)
        self.assertEqual(npc.name, "Zeca")

    def test_blacksmith_tem_combate_e_merchant_e_blacksmith(self):
        eid = create_blacksmith(self.ws.world, 132, 374, name="Grum", shop_id="blacksmith")
        self.assertIsNotNone(self.ws.world.get_component(eid, Combatant))
        self.assertIsNotNone(self.ws.world.get_component(eid, Merchant))
        self.assertIsNotNone(self.ws.world.get_component(eid, Blacksmith))

    def test_quest_giver_tem_combate_e_quest_giver(self):
        eid = create_quest_giver(self.ws.world, 133, 374, name="Missiveiro",
                                 quest_ids=("q1",), turn_in_ids=("q1",))
        self.assertIsNotNone(self.ws.world.get_component(eid, Combatant))
        qg = self.ws.world.get_component(eid, QuestGiver)
        self.assertEqual(qg.quest_ids, ("q1",))

    def test_trainer_guerreiro_luta_como_guerreiro(self):
        eid = create_trainer(self.ws.world, 134, 374, name="Instrutor", class_id="guerreiro")
        ai = self.ws.world.get_component(eid, AIControlled)
        self.assertEqual(ai.entity_class, "Warrior")
        self.assertFalse(ai.is_ranged)
        trainer = self.ws.world.get_component(eid, Trainer)
        self.assertEqual(trainer.class_id, "guerreiro")

    def test_trainer_mago_luta_como_mago(self):
        eid = create_trainer(self.ws.world, 135, 374, name="Arcanista", class_id="mago")
        ai = self.ws.world.get_component(eid, AIControlled)
        self.assertEqual(ai.entity_class, "Mage")
        self.assertTrue(ai.is_ranged)

    def test_trainer_arqueiro_luta_como_arqueiro(self):
        eid = create_trainer(self.ws.world, 136, 374, name="Caçador", class_id="arqueiro")
        ai = self.ws.world.get_component(eid, AIControlled)
        self.assertEqual(ai.entity_class, "Hunter")
        self.assertTrue(ai.is_ranged)

    def test_trainer_com_quest_acumula_quest_giver(self):
        eid = create_trainer(self.ws.world, 137, 374, name="Instrutor+Quest",
                             class_id="guerreiro", quest_ids=("q2",))
        self.assertIsNotNone(self.ws.world.get_component(eid, Trainer))
        self.assertIsNotNone(self.ws.world.get_component(eid, QuestGiver))

    def test_npc_de_servico_amigavel_nao_pode_ser_atacado_pelo_player(self):
        eid = create_merchant(self.ws.world, 138, 374, name="Zeca")
        self.ws.world.add_component(eid, CombatState())
        cs = self.ws.world.get_component(eid, CombatStats)
        hp_before = cs.current_hp

        deal_damage(self.peid, eid, "physical", pre_outcome="hit")

        self.assertEqual(cs.current_hp, hp_before,
                         "NPC de serviço é amigável — dano do player deveria ser bloqueado")

    def test_npc_de_servico_recebe_dano_de_mob_hostil(self):
        """Mesma checagem do Guarda Real (test_faction.py) — hostil
        continua conseguindo ferir o NPC de serviço normalmente."""
        eid = create_merchant(self.ws.world, 139, 374, name="Zeca")
        self.ws.world.add_component(eid, CombatState())
        cs = self.ws.world.get_component(eid, CombatStats)
        hp_before = cs.current_hp
        hostile = create_enemy(self.ws.world, 140, 374, faction="monstros_hostis")

        deal_damage(hostile, eid, "physical", pre_outcome="hit")

        self.assertLess(cs.current_hp, hp_before,
                        "mob hostil deveria conseguir ferir o NPC de serviço")

    def test_payload_de_spawn_inclui_campos_condicionais_de_capacidade(self):
        eid = create_merchant(self.ws.world, 141, 374, name="Zeca", shop_id="general")
        tm = self.ws.world.get_component(eid, TileMovement)
        payload = self.ws._build_mob_spawn_payload(eid, tm)
        self.assertEqual(payload["profession"], "Comerciante")
        self.assertEqual(payload["shop_id"], "general")
        self.assertNotIn("class_id", payload)
        self.assertNotIn("quest_ids", payload)

    def test_payload_de_trainer_inclui_class_id(self):
        eid = create_trainer(self.ws.world, 142, 374, name="Instrutor", class_id="mago")
        tm = self.ws.world.get_component(eid, TileMovement)
        payload = self.ws._build_mob_spawn_payload(eid, tm)
        self.assertEqual(payload["class_id"], "mago")
        self.assertNotIn("shop_id", payload)

    def test_payload_de_trainer_arqueiro_manda_race_e_is_ranged_corretos(self):
        """Bug real relatado pelo usuário 21/07/2026: treinador de arqueiro
        lutava certo (com HP/dano corretos) no servidor, mas no cliente
        virava melee/mudo — causa raiz era _build_mob_spawn_payload nunca
        propagar is_ranged=True (ficava preso no default False, já que só
        SpawnZoneOwner setava esse campo) nem a chave EXATA de MOB_TABLE
        (mandava só EntityIdentity.race, a categoria AMPLA "Humanoide", que
        nunca bate uma entrada de MOB_TABLE) — sem raça registrada, o
        cliente caía no fallback genérico (sem som, sempre melee)."""
        eid = create_trainer(self.ws.world, 146, 374, name="Andre", class_id="arqueiro")
        tm = self.ws.world.get_component(eid, TileMovement)
        payload = self.ws._build_mob_spawn_payload(eid, tm)
        self.assertEqual(payload["race"], "Arqueiro (NPC)")
        self.assertTrue(payload["is_ranged"])
        self.assertEqual(payload["entity_class"], "Hunter")

    def test_trainer_arqueiro_sons_espelham_o_player_e_resto_silencioso(self):
        """Decisão do usuário 21/07/2026: elementos de combate do NPC
        espelham o PLAYER da classe — disparo "arrow_release" e impacto
        "arrow_impact" (campo novo attack_impact em NpcSounds, renomeado
        de MobSounds na mesma leva); aggro/morte/emotes VAZIOS de
        propósito (silêncio, usuário preenche depois na tabela)."""
        from engine.components import NpcSounds
        eid = create_trainer(self.ws.world, 150, 374, name="Andre", class_id="arqueiro")
        snd = self.ws.world.get_component(eid, NpcSounds)
        self.assertEqual(snd.attack_ranged, "arrow_release")
        self.assertEqual(snd.attack_impact, "arrow_impact")
        self.assertFalse(snd.aggro)
        self.assertFalse(snd.death)
        self.assertFalse(snd.emote_attack)
        self.assertFalse(snd.emote_get_crit)

    def test_guerreiro_npc_sons_melee_generico_e_resto_silencioso(self):
        from engine.components import NpcSounds
        eid = create_merchant(self.ws.world, 151, 374, name="Zeca")
        snd = self.ws.world.get_component(eid, NpcSounds)
        self.assertEqual(snd.attack_melee, "hit_normal")
        self.assertFalse(snd.attack_impact)
        self.assertFalse(snd.aggro)
        self.assertFalse(snd.death)

    def test_payload_de_blacksmith_marca_is_blacksmith(self):
        eid = create_blacksmith(self.ws.world, 143, 374, name="Grum")
        tm = self.ws.world.get_component(eid, TileMovement)
        payload = self.ws._build_mob_spawn_payload(eid, tm)
        self.assertTrue(payload.get("is_blacksmith"))
        self.assertIn("shop_id", payload)

    def test_mob_normal_e_guarda_real_continuam_sem_campos_de_npc_servico(self):
        """Regressão — payload de um mob comum ou do Guarda Real (sem
        Merchant/Trainer/QuestGiver) não deve ganhar nenhum campo novo."""
        hostile = create_enemy(self.ws.world, 144, 374, faction="monstros_hostis")
        guard = create_combat_npc(self.ws.world, 145, 374, faction="guardas_vila",
                                  name="Guarda Real")
        for eid in (hostile, guard):
            tm = self.ws.world.get_component(eid, TileMovement)
            payload = self.ws._build_mob_spawn_payload(eid, tm)
            self.assertNotIn("shop_id", payload)
            self.assertNotIn("class_id", payload)
            self.assertNotIn("quest_ids", payload)
            self.assertNotIn("is_blacksmith", payload)
        # Guarda Real tem NPC (profession) mas nenhuma capacidade — profession
        # aparece (é condicionado só a NPC, não a Merchant/Trainer/etc.)
        guard_tm = self.ws.world.get_component(guard, TileMovement)
        guard_payload = self.ws._build_mob_spawn_payload(guard, guard_tm)
        self.assertIn("profession", guard_payload)


class TestServiceNpcVoltaProSpawnExato(unittest.TestCase):
    """NPC de serviço (tag NPC) volta pro TILE EXATO do spawn depois do
    combate (feedback do usuário 21/07/2026) — antes assentava a até
    proximity_threshold_tiles (1) do spawn, igual mob comum, e cada briga
    deslocava o posto. Mob comum mantém o threshold antigo (sem mudança).
    Ver EnemyAISystem._settle_threshold_tiles."""

    def setUp(self):
        from engine.components import MapLocation
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.npc = create_merchant(self.ws.world, 150, 380, name="Zeca", level=60)
        self.ws.world.add_component(self.npc, MapLocation(self.ws._map_file))

    def test_npc_deslocado_volta_pro_tile_exato(self):
        set_entity_tile(self.ws, self.npc, 152, 381)   # 3 tiles fora do posto
        run_ticks(self.ws, 200)                        # ~10s de jogo
        tm = self.ws.world.get_component(self.npc, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (150, 380),
                         "NPC de serviço deveria voltar pro tile EXATO do spawn")
        ai = self.ws.world.get_component(self.npc, AIControlled)
        self.assertEqual(ai.state, "IDLE")

    def test_mob_comum_mantem_threshold_antigo(self):
        """Regressão: mob comum a 1 tile do spawn continua considerado 'em
        casa' (threshold 1 inalterado) — a volta exata é só pra tag NPC."""
        from engine.components import MapLocation
        mob = create_enemy(self.ws.world, 160, 380, faction="monstros_hostis")
        self.ws.world.add_component(mob, MapLocation(self.ws._map_file))
        set_entity_tile(self.ws, mob, 161, 380)        # 1 tile fora
        run_ticks(self.ws, 100)
        tm = self.ws.world.get_component(mob, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (161, 380),
                         "mob comum a 1 tile do spawn não deveria se mover (comportamento antigo)")


if __name__ == "__main__":
    unittest.main()
