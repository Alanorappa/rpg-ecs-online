"""
tests/test_jungle_camps.py
Monstros de jungle estilo MOBA (13/08/2026, pedido do usuário) — normal
(hostil aos 2 times, XP/gold no mesmo padrão de Minion) e boss (XP/gold
restritos ao time de quem deu o golpe final, buff de time por abate,
ciclo de buff que muda a cada respawn e trava no último). Ver
PROBLEMAS_ARQUITETURA.md e server/bush_zone_processor.py-style plano em
server/world_server.py::_create_jungle_camps/_grant_jungle_boss_buff.
"""
import unittest
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile

from engine.components import (CombatStats, PendingDeath, Faction, MapLocation,
                              JungleMob, Minion, InitialPosition)
from engine.entity_factory import create_enemy
from content.jungle_definitions import JUNGLE_MOB_TABLE, JUNGLE_BOSS_TABLE
from content.faction_data import RELATIONSHIP
from server.instance_progression import enter_normalized_progression


class TestJungleMobHostility(unittest.TestCase):
    """Neutro aos 2 times da BG (16/08/2026 — antes era "hostil" via a
    facção compartilhada "monstros_hostis", aggrando por proximidade;
    bug real relatado pelo usuário: "só de chegar perto eles aggram").
    Facção PRÓPRIA "monstros_jungle", tier "neutro" — ainda pode ser
    atacado/engajado por qualquer time (can_engage), só não aggra
    sozinho por proximidade (is_hostile)."""

    def test_faction_monstros_jungle_e_neutra_aos_2_times(self):
        from content.faction_data import get_relationship
        self.assertEqual(get_relationship("arena_time_a", "monstros_jungle"), "neutro")
        self.assertEqual(get_relationship("arena_time_b", "monstros_jungle"), "neutro")

    def test_jungle_mob_nao_e_hostil_mas_pode_ser_engajado_pelos_2_times(self):
        from engine.faction_system import is_hostile, can_engage
        ws = make_world_server()
        ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_lobo_alfa"}],
            is_boss=False, map_file=ws._map_file)
        mob_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))

        a = spawn_player(ws, "sa", 20, 20)
        ws.world.add_component(a, Faction("arena_time_a"))
        b = spawn_player(ws, "sb", 20, 20)
        ws.world.add_component(b, Faction("arena_time_b"))

        self.assertFalse(is_hostile(ws.world, mob_eid, a),
                         "jungle não deveria aggrar por proximidade (is_hostile)")
        self.assertFalse(is_hostile(ws.world, mob_eid, b))
        self.assertTrue(can_engage(ws.world, a, mob_eid),
                        "jungle ainda deveria poder ser atacado pelos 2 times")
        self.assertTrue(can_engage(ws.world, b, mob_eid))

    def test_jungle_mob_nao_aggra_so_por_player_chegar_perto(self):
        """Fim-a-fim via EnemyAISystem.update() (não só is_hostile isolado)
        — bug real relatado pelo usuário."""
        ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(ws, 8, 8, 40, 40)
        ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_lobo_alfa"}],
            is_boss=False, map_file=ws._map_file)
        mob_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))

        p = spawn_player(ws, "sp", 21, 20)  # adjacente, dentro do raio antigo de aggro
        ws.world.add_component(p, Faction("arena_time_a"))
        run_ticks(ws, 60)

        from engine.components import AIControlled
        ai = ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "IDLE",
                         "jungle não deveria sair de IDLE só pelo player estar perto")

    def test_nome_exibido_e_o_display_name_nao_o_mob_key_cru(self):
        """16/08/2026, bug real relatado pelo usuário: "os monstros da
        jungle estão com o código no lugar do nome" — create_enemy()
        usava `race` (= mob_key, ex: "jungle_lobo_alfa") como fallback
        de EntityIdentity.name quando nenhum identity_name é passado."""
        from engine.components import EntityIdentity
        ws = make_world_server()
        ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_lobo_alfa"}],
            is_boss=False, map_file=ws._map_file)
        mob_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))
        ident = ws.world.get_component(mob_eid, EntityIdentity)
        self.assertEqual(ident.name, JUNGLE_MOB_TABLE["jungle_lobo_alfa"]["display_name"])
        self.assertNotEqual(ident.name, "jungle_lobo_alfa")


class TestJungleMobNormalReward(unittest.TestCase):
    """XP/gold do mob normal seguem EXATAMENTE o padrão de Minion (XP por
    proximidade sem filtro de time; gold só pro golpe final)."""

    def setUp(self):
        self.ws = make_world_server()
        self.ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_lobo_alfa"}],
            is_boss=False, map_file=self.ws._map_file)
        self.mob_eid = next(eid for eid, jm in self.ws.world.get_entities_with(JungleMob))

    def _kill(self, killer_eid=-1):
        cs = self.ws.world.get_component(self.mob_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(self.mob_eid, PendingDeath(killer_entity_id=killer_eid))

    def test_xp_por_proximidade_sem_precisar_bater(self):
        from engine.components import CharacterStats
        p = spawn_player(self.ws, "p1", 20, 20)
        enter_normalized_progression(self.ws, p)
        char = self.ws.world.get_component(p, CharacterStats)
        xp_antes = char.current_xp

        self._kill(killer_eid=-1)  # ninguém bateu
        run_ticks(self.ws, 1)

        self.assertGreater(char.current_xp, xp_antes,
                           "player perto do jungle mob deveria ganhar xp mesmo sem bater")

    def test_xp_dividido_entre_players_de_times_diferentes_perto(self):
        """Diferente do boss — normal NÃO filtra por time."""
        from engine.components import CharacterStats
        a = spawn_player(self.ws, "sa", 20, 20)
        self.ws.world.add_component(a, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, a)
        b = spawn_player(self.ws, "sb", 20, 20)
        self.ws.world.add_component(b, Faction("arena_time_b"))
        enter_normalized_progression(self.ws, b)
        char_a = self.ws.world.get_component(a, CharacterStats)
        char_b = self.ws.world.get_component(b, CharacterStats)
        xp_a_antes, xp_b_antes = char_a.current_xp, char_b.current_xp

        self._kill(killer_eid=-1)
        run_ticks(self.ws, 1)

        self.assertGreater(char_a.current_xp, xp_a_antes,
                           "jungle normal não filtra por time — os 2 times perto ganham XP")
        self.assertGreater(char_b.current_xp, xp_b_antes)

    def test_gold_so_pro_golpe_final(self):
        from engine.components import Wallet
        p = spawn_player(self.ws, "p1", 20, 20)
        enter_normalized_progression(self.ws, p)
        wallet = self.ws.world.get_component(p, Wallet)
        gold_antes = wallet.gold

        self._kill(killer_eid=p)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes)
        self.assertLessEqual(wallet.gold - gold_antes, JUNGLE_MOB_TABLE["jungle_lobo_alfa"]["gold_max"])

    def test_sem_drop_fisico_de_item_ou_coin_no_corpse(self):
        """16/08/2026, bug real relatado pelo usuário: mob de jungle
        dropava item/gold FÍSICO no corpse, além do gold de instância já
        creditado direto na carteira — jungle não deveria dropar nada,
        mesmo padrão de minion."""
        from engine.components import Wallet
        p = spawn_player(self.ws, "p1", 20, 20)
        enter_normalized_progression(self.ws, p)
        wallet = self.ws.world.get_component(p, Wallet)
        gold_antes = wallet.gold

        self._kill(killer_eid=p)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes,
                           "gold de instância ainda deveria ser creditado direto na carteira")
        corpses = [c for c in self.ws._corpses.values() if c.get("owner_eid") == p]
        self.assertEqual(len(corpses), 1)
        self.assertEqual(corpses[0]["items"], [], "jungle não deveria dropar item físico")
        self.assertEqual(corpses[0]["coins"], 0, "jungle não deveria dropar coin físico (já foi pra carteira)")


class TestJungleBossReward(unittest.TestCase):
    """XP/gold do boss: por proximidade E restrito ao MESMO time do
    golpe final — diferente do mob normal."""

    def setUp(self):
        self.ws = make_world_server()
        self.ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_boss_ancestral"}],
            is_boss=True, map_file=self.ws._map_file)
        self.boss_eid = next(eid for eid, jm in self.ws.world.get_entities_with(JungleMob)
                             if jm.is_boss)

    def _kill(self, killer_eid):
        cs = self.ws.world.get_component(self.boss_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(self.boss_eid, PendingDeath(killer_entity_id=killer_eid))

    def test_xp_so_pro_time_do_killer_nao_pro_adversario_perto(self):
        from engine.components import CharacterStats
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)

        enemy_nearby = spawn_player(self.ws, "se", 20, 20)
        self.ws.world.add_component(enemy_nearby, Faction("arena_time_b"))
        enter_normalized_progression(self.ws, enemy_nearby)

        char_k = self.ws.world.get_component(killer, CharacterStats)
        char_e = self.ws.world.get_component(enemy_nearby, CharacterStats)
        xp_k_antes, xp_e_antes = char_k.current_xp, char_e.current_xp

        self._kill(killer_eid=killer)
        run_ticks(self.ws, 1)

        self.assertGreater(char_k.current_xp, xp_k_antes, "time do killer deveria ganhar XP do boss")
        self.assertEqual(char_e.current_xp, xp_e_antes,
                         "adversário perto NÃO deveria ganhar XP do boss do outro time")

    def test_gold_so_pro_time_do_killer(self):
        from engine.components import Wallet
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)

        enemy_nearby = spawn_player(self.ws, "se", 20, 20)
        self.ws.world.add_component(enemy_nearby, Faction("arena_time_b"))
        enter_normalized_progression(self.ws, enemy_nearby)

        wallet_k = self.ws.world.get_component(killer, Wallet)
        wallet_e = self.ws.world.get_component(enemy_nearby, Wallet)
        gold_k_antes, gold_e_antes = wallet_k.gold, wallet_e.gold

        self._kill(killer_eid=killer)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet_k.gold, gold_k_antes)
        self.assertEqual(wallet_e.gold, gold_e_antes)

    def test_gold_dividido_entre_membros_do_time_perto(self):
        from engine.components import Wallet
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)
        ally = spawn_player(self.ws, "sal", 20, 20)
        self.ws.world.add_component(ally, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, ally)

        wallet_k = self.ws.world.get_component(killer, Wallet)
        wallet_al = self.ws.world.get_component(ally, Wallet)
        gold_k_antes, gold_al_antes = wallet_k.gold, wallet_al.gold

        self._kill(killer_eid=killer)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet_k.gold, gold_k_antes)
        self.assertGreater(wallet_al.gold, gold_al_antes,
                           "aliado perto do MESMO time deveria receber parte do ouro")

    def test_sem_drop_fisico_de_item_ou_coin_no_corpse(self):
        from engine.components import Wallet
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)
        wallet_k = self.ws.world.get_component(killer, Wallet)
        gold_k_antes = wallet_k.gold

        self._kill(killer_eid=killer)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet_k.gold, gold_k_antes)
        corpses = [c for c in self.ws._corpses.values() if c.get("owner_eid") == killer]
        self.assertEqual(len(corpses), 1)
        self.assertEqual(corpses[0]["items"], [], "boss de jungle não deveria dropar item físico")
        self.assertEqual(corpses[0]["coins"], 0, "boss de jungle não deveria dropar coin físico")


class TestJungleBossBuff(unittest.TestCase):
    """Time inteiro (jogadores + minions vivos) ganha o buff do ciclo ao
    abater o boss; ciclo muda por abate e trava no último."""

    def setUp(self):
        self.ws = make_world_server()
        # respawn_s curto (1.0s) pra poder matar o boss várias vezes em
        # sequência dentro de um teste (ciclo de buff) sem esperar o
        # default de produção (300s).
        self.ws._create_jungle_camps(
            [{"x": 20, "y": 20, "mob_key": "jungle_boss_ancestral", "respawn_s": 1.0}],
            is_boss=True, map_file=self.ws._map_file)
        self.boss_eid = next(eid for eid, jm in self.ws.world.get_entities_with(JungleMob)
                             if jm.is_boss)

    def _kill_current_boss(self, killer_eid):
        cs = self.ws.world.get_component(self.boss_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(self.boss_eid, PendingDeath(killer_entity_id=killer_eid))
        run_ticks(self.ws, 1)
        # Espera o respawn (respawn_s=1.0s -> 30 ticks de 0.05s = 1.5s)
        # pra poder matar de novo na próxima chamada.
        run_ticks(self.ws, 30)
        alive = [eid for eid, jm in self.ws.world.get_entities_with(JungleMob) if jm.is_boss]
        self.assertTrue(alive, "boss deveria ter respawnado antes do próximo abate")
        self.boss_eid = alive[0]

    def test_time_inteiro_recebe_buff_jogadores_e_minions_vivos(self):
        from engine.components import CombatStats as _CS
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)
        # Aliado LONGE (fora do raio de proximidade de XP/ouro) — ainda
        # assim deveria ganhar o BUFF (regra diferente: time inteiro).
        far_ally = spawn_player(self.ws, "sfar", 200, 200)
        self.ws.world.add_component(far_ally, Faction("arena_time_a"))

        from engine.entity_factory import create_minion
        minion = create_minion(self.ws.world, 5, 5, "minion_melee",
                               faction_id="arena_time_a", route=[(5, 5)])
        self.ws.world.add_component(minion, MapLocation(self.ws._map_file))
        # 1 tick pra registrar o minion em _mob_eids (sweep de _tick())
        # ANTES de matar o boss — sem isso, a varredura de
        # _grant_jungle_boss_buff nunca o encontra.
        run_ticks(self.ws, 1)

        self._kill_current_boss(killer_eid=killer)

        cs_far = self.ws.world.get_component(far_ally, _CS)
        cs_minion = self.ws.world.get_component(minion, _CS)
        self.assertTrue(any(m["label"].startswith("jungle_boss_") for m in cs_far.timed_modifiers),
                        "aliado longe deveria receber o buff (time inteiro, não só quem está perto)")
        self.assertTrue(any(m["label"].startswith("jungle_boss_") for m in cs_minion.timed_modifiers),
                        "minion vivo do time vencedor deveria receber o buff")

    def test_ciclo_de_buff_muda_a_cada_abate_e_trava_no_ultimo(self):
        """31/08/2026: reescrito pra checar o CONJUNTO de atributos da
        concessão mais recente, não só timed_modifiers[-1]/modifiers[0].
        Bug real corrigido junto (mexendo nesta função pro buff bar):
        buff_cycle[2] (combinado, attack_power+haste_rating) usava o
        MESMO label pros 2 modifiers — add_timed_modifier() do 2º só
        renovava o timer do 1º, o atributo haste_rating nunca aplicava
        de verdade. Este teste ANTES validava (sem querer) esse bug —
        `seen_attrs[2] == modifiers[0]["attribute"]` só passava porque só
        o 1º atributo realmente entrava na lista."""
        from engine.components import CombatStats as _CS
        killer = spawn_player(self.ws, "sk", 20, 20)
        self.ws.world.add_component(killer, Faction("arena_time_a"))
        enter_normalized_progression(self.ws, killer)
        cs_killer = self.ws.world.get_component(killer, _CS)

        buff_cycle = JUNGLE_BOSS_TABLE["jungle_boss_ancestral"]["buff_cycle"]
        seen_attr_sets = []
        for _ in range(len(buff_cycle) + 1):  # +1 pra provar que trava no último
            _before = len(cs_killer.timed_modifiers)
            self._kill_current_boss(killer_eid=killer)
            _new_entries = cs_killer.timed_modifiers[_before:]
            seen_attr_sets.append({e["modifier"].attribute for e in _new_entries})

        self.assertEqual(seen_attr_sets[0], {m["attribute"] for m in buff_cycle[0]["modifiers"]})
        self.assertEqual(seen_attr_sets[1], {m["attribute"] for m in buff_cycle[1]["modifiers"]})
        self.assertEqual(seen_attr_sets[2], {m["attribute"] for m in buff_cycle[2]["modifiers"]},
                         "buff combinado deveria aplicar os 2 atributos (bug corrigido 31/08/2026)")
        # 4º abate (índice 3, além da lista de 3 buffs) repete o ÚLTIMO
        # buff do ciclo (mesmos 2 atributos) — empilha como concessão NOVA
        # (comportamento já existente, não mudou: cada abate usa um label
        # distinto por incluir o índice do ciclo).
        self.assertEqual(seen_attr_sets[3], {m["attribute"] for m in buff_cycle[2]["modifiers"]})


class TestJungleCampRespawn(unittest.TestCase):

    def test_boss_respawna_na_mesma_posicao(self):
        ws = make_world_server()
        ws._create_jungle_camps(
            [{"x": 30, "y": 30, "mob_key": "jungle_boss_ancestral", "respawn_s": 1.0}],
            is_boss=True, map_file=ws._map_file)
        boss_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob) if jm.is_boss)

        killer = spawn_player(ws, "sk", 30, 30)
        ws.world.add_component(killer, Faction("arena_time_a"))

        cs = ws.world.get_component(boss_eid, CombatStats)
        cs.current_hp = 0
        ws.world.add_component(boss_eid, PendingDeath(killer_entity_id=killer))
        run_ticks(ws, 1)

        self.assertEqual(len([e for e, jm in ws.world.get_entities_with(JungleMob) if jm.is_boss]), 0,
                         "boss morto não deveria estar vivo antes do respawn_s passar")

        run_ticks(ws, 30)  # 30 * 0.05s = 1.5s > respawn_s=1.0

        from engine.components import TileMovement as _TM
        alive = [(e, ws.world.get_component(e, _TM))
                for e, jm in ws.world.get_entities_with(JungleMob) if jm.is_boss]
        self.assertEqual(len(alive), 1, "boss deveria ter respawnado")
        _, tm = alive[0]
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (30, 30))


class TestJungleCampLevelScaling(unittest.TestCase):
    """Level do camp = média de TODOS os players da instância (sem filtro
    de Faction — jungle é neutra, hostil aos 2 times por igual), recalculada
    a cada respawn (16/08/2026, pedido do usuário: "o level dos monstros
    precisa subir junto com o nível médio de todos os players, assim como
    acontece com os minions") — mesmo espírito de
    test_minions.py::test_level_do_minion_e_a_media_do_time_recalculada_a_cada_wave,
    só que SEM filtro de time (`_compute_match_avg_level`, não
    `_compute_team_avg_level`)."""

    def test_level_do_mob_e_a_media_de_todos_os_players_recalculada_no_respawn(self):
        from engine.components import CharacterStats, EntityIdentity
        ws = make_world_server()
        ws._create_jungle_camps(
            [{"x": 30, "y": 30, "mob_key": "jungle_lobo_alfa", "level": 1, "respawn_s": 1.0}],
            is_boss=False, map_file=ws._map_file)
        mob_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))

        # 2 players de TIMES DIFERENTES — jungle não filtra por Faction,
        # os 2 devem entrar na média (diferente do minion, que só olha o
        # PRÓPRIO time da lane).
        p1 = spawn_player(ws, "jl1", 30, 30)
        p2 = spawn_player(ws, "jl2", 31, 30)
        ws.world.add_component(p1, Faction("arena_time_a"))
        ws.world.add_component(p2, Faction("arena_time_b"))
        ws.world.get_component(p1, CharacterStats).level = 6
        ws.world.get_component(p2, CharacterStats).level = 12
        ws.world.add_component(p1, MapLocation(ws._map_file))
        ws.world.add_component(p2, MapLocation(ws._map_file))

        cs = ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 0
        ws.world.add_component(mob_eid, PendingDeath(killer_entity_id=p1))
        run_ticks(ws, 30)  # 30 * 0.05s = 1.5s > respawn_s=1.0

        respawned = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))
        ident = ws.world.get_component(respawned, EntityIdentity)
        self.assertEqual(ident.level, 9, "level deveria ser a média de TODOS os players (6+12)/2=9")

    def test_level_cai_no_fallback_estatico_sem_player_na_instancia(self):
        from engine.components import EntityIdentity
        ws = make_world_server()
        ws._create_jungle_camps(
            [{"x": 30, "y": 30, "mob_key": "jungle_lobo_alfa", "level": 4, "respawn_s": 1.0}],
            is_boss=False, map_file=ws._map_file)
        mob_eid = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))
        ident_antes = ws.world.get_component(mob_eid, EntityIdentity)
        self.assertEqual(ident_antes.level, 4, "sem player na instância, cai no fallback do JSON")

        cs = ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 0
        ws.world.add_component(mob_eid, PendingDeath(killer_entity_id=-1))
        run_ticks(ws, 30)

        respawned = next(eid for eid, jm in ws.world.get_entities_with(JungleMob))
        ident_depois = ws.world.get_component(respawned, EntityIdentity)
        self.assertEqual(ident_depois.level, 4, "sem player, respawn continua no fallback salvo")


class TestJungleFallbackReconstruction(unittest.TestCase):
    """`_build_combat_entity` (usado pelo CLIENTE pra reconstruir o
    espelho remoto de qualquer mob) resolve jungle mob/boss pela cadeia
    de fallback nova — mesmo padrão já usado por Torre/Minion."""

    def test_create_enemy_resolve_jungle_mob_pela_cadeia_de_fallback(self):
        from engine.world import World
        from engine.components import EntityIdentity, CombatStats as _CS
        world = World()
        eid = create_enemy(world, 10, 10, race="jungle_lobo_alfa", faction="monstros_hostis")
        ident = world.get_component(eid, EntityIdentity)
        cs = world.get_component(eid, _CS)
        self.assertEqual(ident.entity_class, JUNGLE_MOB_TABLE["jungle_lobo_alfa"]["entity_class"])
        self.assertEqual(cs.max_hp, JUNGLE_MOB_TABLE["jungle_lobo_alfa"]["attributes"]["health"])

    def test_create_enemy_resolve_jungle_boss_pela_cadeia_de_fallback(self):
        from engine.world import World
        from engine.components import EntityIdentity, CombatStats as _CS
        world = World()
        eid = create_enemy(world, 10, 10, race="jungle_boss_ancestral", faction="monstros_hostis")
        ident = world.get_component(eid, EntityIdentity)
        cs = world.get_component(eid, _CS)
        self.assertEqual(ident.entity_class, JUNGLE_BOSS_TABLE["jungle_boss_ancestral"]["entity_class"])
        self.assertEqual(cs.max_hp, JUNGLE_BOSS_TABLE["jungle_boss_ancestral"]["attributes"]["health"])


if __name__ == "__main__":
    unittest.main()
