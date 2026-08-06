"""
tests/test_towers.py
Sistema de Torres (29/07/2026, pedido do usuário) — torre estática com
facção, ataque à distância, alvo sticky com prioridade mob>player e
aggro-switch pra defender aliado, ramp de dano só contra player,
respawn exato (não via SpawnZone) e XP/ouro da própria definição
(nunca MOB_TABLE/tier). Ver arquitetura/ARQUITETURA_ONLINE.md.
"""
import unittest
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import make_world_server, spawn_player, run_ticks

from engine.world import World
from engine.entity_factory import create_tower, create_enemy, create_player, create_tilemap, create_minion
from engine.components import Tower, CombatStats, Faction, MapLocation, PendingDeath
from engine.world_systems import (TowerSystem, ProjectileSystem, register_services,
                        CombatSystem, PathfindingSystem, TileValidationSystem)
from content.faction_data import RELATIONSHIP


def _make_world_with_services():
    """World headless mínimo com serviços registrados (combat/pathfinding/
    tile_validation) — suficiente pra TowerSystem/ProjectileSystem
    resolverem dano/LOS sem precisar de um WorldServer inteiro."""
    w = World()
    terrain = ["." * 30 for _ in range(30)]
    objects = [["."] * 30 for _ in range(30)]
    tm_eid = create_tilemap(w, terrain, objects, None)
    tv = TileValidationSystem(w, tilemap_entity=tm_eid)
    pf = PathfindingSystem(w, tilemap_entity=tm_eid)
    combat = CombatSystem(w, is_server=True)
    register_services(combat=combat, pathfinding=pf, tile_validation=tv)
    return w


class TestTowerTargeting(unittest.TestCase):
    """TowerSystem isolado (World headless, sem WorldServer) — foco na
    lógica de alvo/ataque, não no ciclo de vida de morte/respawn (isso
    é coberto em TestTowerDeathXpGoldRespawn, via WorldServer real)."""

    def setUp(self):
        self.world = _make_world_with_services()
        self.ts = TowerSystem(self.world)
        self.proj_sys = ProjectileSystem(self.world, None)

    def test_torre_ataca_mob_hostil_no_alcance(self):
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila")
        mob_eid = create_enemy(self.world, 12, 10, race="Lobo", faction="monstros_hostis")
        mob_cs = self.world.get_component(mob_eid, CombatStats)
        hp_before = mob_cs.current_hp

        for _ in range(200):
            self.ts.update(1 / 30)
            self.proj_sys.update(dt=1 / 30)

        self.assertLess(mob_cs.current_hp, hp_before, "torre deveria ter danificado o mob")

    def test_torre_nunca_ataca_player_com_mob_no_alcance(self):
        RELATIONSHIP[("arena_time_a", "monstros_hostis")] = "hostil"
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="arena_time_a")
        mob_eid = create_enemy(self.world, 12, 10, race="Lobo", faction="monstros_hostis")
        player_eid = create_player(self.world, 11, 10)
        self.world.add_component(player_eid, Faction("arena_time_b"))

        self.ts.update(1 / 30)
        tower = self.world.get_component(tower_eid, Tower)
        self.assertEqual(tower.current_target_eid, mob_eid,
                         "torre deveria priorizar o mob, nunca o player, enquanto ele estiver no alcance")

    def test_torre_ataca_minion_hostil_no_alcance(self):
        """Bug real relatado pelo usuário no playtest do battleground MOBA
        (01/08/2026): torre nunca atacava minion do time oposto. Raiz:
        `_acquire_target` classificava candidatos em 2 buckets (player /
        AIControlled-ou-NPC) — Minion não tem NENHUM dos dois componentes
        (MinionSystem próprio, sem AIControlled), então passava em todos
        os checks de hostilidade/alcance mas nunca entrava em bucket
        nenhum, nunca virando alvo. Corrigido: bucket "não-player" vira o
        default (qualquer hostil que não seja PlayerControlled)."""
        RELATIONSHIP[("arena_time_a", "arena_time_b")] = "hostil"
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="arena_time_a")
        minion_eid = create_minion(self.world, 12, 10, "minion_melee",
                                   faction_id="arena_time_b", route=[(12, 10)])
        minion_cs = self.world.get_component(minion_eid, CombatStats)
        hp_before = minion_cs.current_hp

        for _ in range(200):
            self.ts.update(1 / 30)
            self.proj_sys.update(dt=1 / 30)

        self.assertLess(minion_cs.current_hp, hp_before, "torre deveria ter danificado o minion")

    def test_alvo_sticky_nao_reavalia_a_cada_tick(self):
        """2 mobs no alcance — a torre deve manter o PRIMEIRO alvo mesmo se
        um segundo mob ficar mais próximo depois."""
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila")
        # x=13 (não 15): attack_range_tiles de torre_de_fogo foi ajustado
        # pelo usuário pra 4 (content/tower_definitions.py) — distância 5
        # ficava fora de alcance, mob nunca virava alvo (falha pré-existente
        # não relacionada às mudanças de 01/08/2026, achada ao investigar
        # outro bug — teste só precisava acompanhar o balanceamento).
        mob_far_eid = create_enemy(self.world, 13, 10, race="Lobo", faction="monstros_hostis")
        self.ts.update(1 / 30)
        tower = self.world.get_component(tower_eid, Tower)
        self.assertEqual(tower.current_target_eid, mob_far_eid)

        # Mob mais perto aparece DEPOIS — não deveria roubar o alvo.
        mob_near_eid = create_enemy(self.world, 11, 10, race="Lobo", faction="monstros_hostis")
        for _ in range(5):
            self.ts.update(1 / 30)
        self.assertEqual(tower.current_target_eid, mob_far_eid,
                         "alvo sticky não deveria trocar só por um candidato mais próximo aparecer")

    def test_aggro_switch_defende_aliado_atacado_no_alcance(self):
        RELATIONSHIP[("arena_time_a", "monstros_hostis")] = "hostil"
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="arena_time_a")
        mob_eid = create_enemy(self.world, 12, 10, race="Lobo", faction="monstros_hostis")
        enemy_player_eid = create_player(self.world, 13, 10)
        self.world.add_component(enemy_player_eid, Faction("arena_time_b"))
        ally_eid = create_player(self.world, 14, 10)
        self.world.add_component(ally_eid, Faction("arena_time_a"))

        self.ts.update(1 / 30)
        tower = self.world.get_component(tower_eid, Tower)
        self.assertEqual(tower.current_target_eid, mob_eid, "alvo inicial deveria ser o mob")

        combat_log = [{"attacker": enemy_player_eid, "target": ally_eid, "damage": 10,
                      "outcome": "hit", "hp_after": 90, "source": "auto"}]
        self.ts.update(1 / 30, combat_this_tick=combat_log)
        self.assertEqual(tower.current_target_eid, enemy_player_eid,
                         "torre deveria trocar IMEDIATAMENTE pro atacante do aliado")

    def test_ramp_de_dano_so_sobe_contra_player_nunca_contra_mob(self):
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="arena_time_a")
        tower = self.world.get_component(tower_eid, Tower)
        player_eid = create_player(self.world, 11, 10)

        tower.current_target_eid = player_eid
        tower.attack_cd = 0.0
        self.ts._attack(tower_eid, tower, self.world.get_component(tower_eid, CombatStats))
        self.assertEqual(tower.dmg_ramp_stacks, 1)

        tower.attack_cd = 0.0
        self.ts._attack(tower_eid, tower, self.world.get_component(tower_eid, CombatStats))
        self.assertEqual(tower.dmg_ramp_stacks, 2)

        # Contra mob nunca sobe.
        mob_eid = create_enemy(self.world, 12, 10, race="Lobo", faction="monstros_hostis")
        tower.current_target_eid = mob_eid
        tower.attack_cd = 0.0
        self.ts._attack(tower_eid, tower, self.world.get_component(tower_eid, CombatStats))
        self.assertEqual(tower.dmg_ramp_stacks, 2, "ramp não deveria subir/persistir efeito contra mob")

    def test_ramp_reseta_apos_3s_sem_bater_em_player(self):
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila")
        tower = self.world.get_component(tower_eid, Tower)
        tower.dmg_ramp_stacks = 2
        tower.dmg_ramp_timer = 0.0

        for _ in range(89):   # 89 * (1/30) ≈ 2.97s — ainda não passou de 3.0s
            self.ts.update(1 / 30)
        self.assertEqual(tower.dmg_ramp_stacks, 2)

        for _ in range(5):
            self.ts.update(1 / 30)
        self.assertEqual(tower.dmg_ramp_stacks, 0, "ramp deveria zerar após 3s sem bater em player")

    def test_regen_desligado_nao_regenera(self):
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila",
                                 regen_enabled=False)
        cs = self.world.get_component(tower_eid, CombatStats)
        cs.current_hp = 1
        for _ in range(300):   # 10s simulados
            self.ts.update(1 / 30)
        self.assertEqual(cs.current_hp, 1, "regen_enabled=False nunca deveria regenerar")

    def test_regen_ligado_regenera_fora_de_combate(self):
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila",
                                 regen_enabled=True)
        cs = self.world.get_component(tower_eid, CombatStats)
        cs.current_hp = 1
        for _ in range(180):   # 6s simulados (regen a cada 5s)
            self.ts.update(1 / 30)
        self.assertGreater(cs.current_hp, 1, "regen_enabled=True deveria regenerar fora de combate")

    def test_acquire_target_com_spatial_hash_nao_varre_todas_as_entidades(self):
        """04/08/2026, pedido do usuário (log de perf mostrou tower_system
        como um dos maiores consumidores de tick com a BG ativa): torre
        sem alvo varria TODAS as entidades do jogo só pra achar "tem
        hostil por perto?" — `spatial_hash` (`engine.utils.SpatialHash`,
        mesma técnica já usada pro AOI de sessão) limita aos candidatos
        realmente próximos. Aqui: 1 hostil DENTRO do alcance + 30 hostis
        FORA do alcance (bem longe) — confirma que a torre ainda acha o
        certo, e que `world.get_entities_with` (o sweep completo) NUNCA
        é chamado quando o índice espacial é fornecido."""
        from engine.components import Position, TileMovement
        from engine.utils import SpatialHash
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila")
        close_eid = create_enemy(self.world, 12, 10, race="Lobo", faction="monstros_hostis")
        for i in range(30):
            create_enemy(self.world, 10 + 50 + i, 10, race="Lobo", faction="monstros_hostis")

        spatial_hash = {"": SpatialHash(cell_size=9)}
        for eid, pos, cs, tm in self.world.get_entities_with(Position, CombatStats, TileMovement):
            if cs.current_hp > 0:
                spatial_hash[""].insert(eid, tm.current_tile_x, tm.current_tile_y)

        calls = []
        real_get_entities_with = self.world.get_entities_with

        def _spy_get_entities_with(*a, **k):
            calls.append(a)
            return real_get_entities_with(*a, **k)

        self.world.get_entities_with = _spy_get_entities_with
        self.ts.update(1 / 30, spatial_hash=spatial_hash)

        tower = self.world.get_component(tower_eid, Tower)
        self.assertEqual(tower.current_target_eid, close_eid,
                         "torre deveria ter achado o hostil dentro do alcance mesmo com o índice espacial")
        _sweep_calls = [c for c in calls if c and c[0] in (Position,)]
        self.assertEqual(len(_sweep_calls), 0,
                         "_acquire_target não deveria ter feito o sweep completo "
                         "(world.get_entities_with) com spatial_hash fornecido")


class TestTowerDeathXpGoldRespawn(unittest.TestCase):
    """Ciclo de vida completo (morte → XP/ouro → respawn) via WorldServer
    real (make_world_server) — precisa do pipeline de sessão/death
    handler de verdade, não só do TowerSystem isolado."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10)

    def _kill_tower(self, tower_eid):
        from engine.components import CombatStats as _CS
        cs = self.ws.world.get_component(tower_eid, _CS)
        cs.current_hp = 0
        self.ws.world.add_component(tower_eid, PendingDeath(killer_entity_id=self.player_eid))
        self.ws._mob_damage_log[tower_eid] = {self.player_eid: 999}

    def test_xp_e_ouro_vem_da_propria_definicao_da_torre(self):
        tower_eid = create_tower(self.ws.world, 11, 10, "torre_de_fogo",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        self._kill_tower(tower_eid)
        run_ticks(self.ws, 1)

        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot, "corpse da torre deveria ter sido registrado")
        self.assertGreaterEqual(loot["coins"], 20)
        self.assertLessEqual(loot["coins"], 40)
        self.assertEqual(loot["items"], [], "torre não deveria dropar item, só ouro")

    def test_ouro_de_instancia_nao_duplica_com_coins_do_corpse(self):
        """Bug real relatado pelo usuário (02/08/2026): dentro da
        progressão normalizada, matar uma torre concedia ouro instantâneo
        (server/instance_progression.py::grant_instance_gold) E TAMBÉM
        colocava coins no corpse (loot normal, nunca suprimido antes) —
        o killer podia ganhar o MESMO ouro duas vezes. Corrigido: coins do
        corpse vira 0 quando o ouro de instância já foi concedido."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import Wallet
        enter_normalized_progression(self.ws, self.player_eid)
        wallet = self.ws.world.get_component(self.player_eid, Wallet)
        gold_antes = wallet.gold

        tower_eid = create_tower(self.ws.world, 15, 10, "torre_de_fogo",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        self._kill_tower(tower_eid)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes,
                           "gold de instância deveria ter subido pro killer")
        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot)
        self.assertEqual(loot["coins"], 0,
                         "corpse não deveria ter coins — já concedido instantaneamente")

    def test_ranged_kill_de_torre_nao_droppa_gold_no_corpse_dentro_da_instancia(self):
        """Cobertura nova (03/08/2026 — usuário relatou "torre dropou gold"
        com o Arqueiro "jungo", investigação NÃO reproduziu regressão real
        no auto-attack ranged: _server_apply_ranged_physical
        (spell_completion_processor.py) já seta PendingDeath com o killer
        certo no fim da própria função, então o gate `killer_eid != -1`
        (server_death_handler.py) e a supressão de coins já funcionavam.
        Esta classe só tinha cobertura de MELEE (guerreiro) até agora —
        este teste fecha o gap pra RANGED, confirmando que continua
        correto. Se o bug do usuário reaparecer, é OUTRO caminho de kill
        (skill/DoT/multi-hit) — ver ARQUITETURA_ONLINE.md."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import Wallet, Equipment, Item, CombatState
        # (50,50)/(53,50): corredor com linha de visão livre no map_1 real
        # (confirmado via EnemyAISystem._has_line_of_sight) — a área usada
        # pelo resto desta classe ((10,10) etc.) tem obstáculos que
        # bloqueiam LOS a distância >1, o que só importa pra ranged
        # (melee nunca checa LOS).
        arqueiro_eid = spawn_player(self.ws, "s_arq", 50, 50, class_id="arqueiro")
        enter_normalized_progression(self.ws, arqueiro_eid)
        wallet = self.ws.world.get_component(arqueiro_eid, Wallet)
        gold_antes = wallet.gold

        bow    = Item("Arco Teste", "weapon", "mainhand", subtype="Bow", cast_range=12)
        quiver = Item("Aljava Teste", "quiver", "offhand", arrow_count=50, max_arrows=50)
        equip = self.ws.world.get_component(arqueiro_eid, Equipment)
        equip.slots["mainhand"] = bow
        equip.slots["offhand"]  = quiver

        tower_eid = create_tower(self.ws.world, 53, 50, "torre_de_fogo",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation(self.ws.MAP_FILE))
        run_ticks(self.ws, 1)

        tower_cs = self.ws.world.get_component(tower_eid, CombatStats)
        tower_cs.current_hp   = 1
        tower_cs.dodge_rating = 0.0
        tower_cs.parry_rating = 0.0
        arq_cs = self.ws.world.get_component(arqueiro_eid, CombatStats)
        arq_cs.acerto = 100.0

        cs_state = self.ws.world.get_component(arqueiro_eid, CombatState)
        cs_state.target_entity_id = tower_eid
        cs_state.is_pursuing      = True
        sid = self.ws.get_session_id_for_player(arqueiro_eid)
        self.ws._attack_timers[sid] = 0.0
        # player_hp_snapshot é o HP do ATACANTE (detecta contra-ataque
        # mob→player no mesmo tick, ver docstring de _process_player_attacks)
        # — nunca o hp do ALVO.
        self.ws._process_player_attacks(0.05, {arqueiro_eid: arq_cs.current_hp})
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes,
                           "gold de instância deveria ter subido pro killer ranged")
        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot)
        self.assertEqual(loot["coins"], 0,
                         "corpse não deveria ter coins — ouro de instância já concedido")

    def test_torre_morta_por_golpe_final_de_minion_credita_ouro_ao_player(self):
        """Bug real relatado pelo usuário (03/08/2026, "jungo"): torre
        atacada por um player + um minion aliado, mas o GOLPE FINAL veio
        do minion (MinionSystem também chama deal_damage/PendingDeath,
        ver engine/world_systems.py:1806) — `killer_eid` virava o eid do
        MINION, não de um player, então o gate antigo (`killer_eid in
        _player_eids_now`) falhava por completo: nem creditava ouro
        automático (killer não é player) NEM suprimia os coins físicos do
        corpse (`_instance_gold_ja_concedido` nunca virava True) — a
        torre voltava a dropar ouro físico dentro da instância, exatamente
        como no print do usuário. Fix: fallback pro primeiro PLAYER que
        bateu (`damage_log` já filtrado só-players)."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import Wallet
        enter_normalized_progression(self.ws, self.player_eid)
        wallet = self.ws.world.get_component(self.player_eid, Wallet)
        gold_antes = wallet.gold

        tower_eid = create_tower(self.ws.world, 20, 10, "torre_de_fogo",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation(self.ws.MAP_FILE))
        minion_eid = create_minion(self.ws.world, 21, 10, "minion_melee",
                                   faction_id="jogadores", route=[(21, 10)])
        self.ws.world.add_component(minion_eid, MapLocation(self.ws.MAP_FILE))
        run_ticks(self.ws, 1)

        tower_cs = self.ws.world.get_component(tower_eid, CombatStats)
        tower_cs.current_hp = 0
        # Golpe final é do MINION, não do player — mas o player TAMBÉM
        # bateu antes (entra no damage_log, é quem deveria ser creditado).
        self.ws.world.add_component(tower_eid, PendingDeath(killer_entity_id=minion_eid))
        self.ws._mob_damage_log[tower_eid] = {self.player_eid: 500, minion_eid: 999}
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes,
                           "gold de instância deveria ter subido pro player que bateu, "
                           "mesmo o golpe final sendo de um minion aliado")
        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot)
        self.assertEqual(loot["coins"], 0,
                         "corpse não deveria ter coins — ouro de instância já concedido "
                         "ao player, mesmo com killer_eid sendo um minion")

    def test_corpse_de_minion_usa_timer_curto_nao_o_de_mob_normal(self):
        """Pedido do usuário (03/08/2026): corpo de minion (sem loot,
        sempre 0 moedas/0 itens) floodava o mapa usando o mesmo timer de
        120s de um mob normal — volume de mortes numa lane MOBA acumula
        rápido. Fix: timer curto dedicado (LootProcessorMixin.
        MINION_CORPSE_TIMER_S, 4s), só pra minion."""
        from engine.entity_factory import create_minion
        minion_eid = create_minion(self.ws.world, 20, 10, "minion_melee",
                                   faction_id="monstros_hostis", route=[(20, 10)])
        self.ws.world.add_component(minion_eid, MapLocation(self.ws.MAP_FILE))
        run_ticks(self.ws, 1)
        cs = self.ws.world.get_component(minion_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(minion_eid, PendingDeath(killer_entity_id=self.player_eid))
        self.ws._mob_damage_log[minion_eid] = {self.player_eid: 999}
        run_ticks(self.ws, 1)

        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot)
        self.assertAlmostEqual(loot["timer"], self.ws.MINION_CORPSE_TIMER_S, delta=0.5)
        self.assertLess(loot["timer"], 120.0)

    def test_torre_respawnavel_volta_no_mesmo_tile_com_hp_cheio(self):
        tower_eid = create_tower(self.ws.world, 12, 10, "torre_de_flechas",
                                 faction_id="monstros_hostis",
                                 respawnable=True, respawn_s=1.0)
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        self._kill_tower(tower_eid)
        run_ticks(self.ws, 1)

        self.assertIsNone(self.ws.world.get_component(tower_eid, Tower),
                         "torre morta deveria ter sido removida do world")
        key = ("maps/map_1.csv", 12, 10)
        self.assertIn(key, self.ws._tower_respawn_timers)

        run_ticks(self.ws, 25)   # 1.25s simulados (respawn_s=1.0)
        self.assertNotIn(key, self.ws._tower_respawn_timers, "timer deveria ter concluído")

        # Filtra por TILE (12,10) — map_1 real já tem 2 torres próprias
        # (map_1_entities.json::towers), não dá pra assumir "só existe 1
        # torre no world inteiro".
        from engine.components import TileMovement as _TM
        new_towers = [eid for eid, tm_ in self.ws.world.get_entities_with(_TM)
                     if self.ws.world.get_component(eid, Tower) is not None
                     and (tm_.current_tile_x, tm_.current_tile_y) == (12, 10)]
        self.assertEqual(len(new_towers), 1, "deveria existir exatamente 1 torre nova neste tile")
        new_cs = self.ws.world.get_component(new_towers[0], CombatStats)
        self.assertEqual(new_cs.current_hp, new_cs.max_hp, "torre deveria respawnar com HP cheio")

    def test_torre_nao_respawnavel_nunca_volta(self):
        tower_eid = create_tower(self.ws.world, 13, 10, "torre_de_fogo",
                                 faction_id="monstros_hostis", respawnable=False)
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        self._kill_tower(tower_eid)
        run_ticks(self.ws, 60)   # 2s simulados — tempo de sobra
        self.assertNotIn(("maps/map_1.csv", 13, 10), self.ws._tower_respawn_timers,
                         "torre não-respawnável não deveria agendar respawn")
        # Filtra por TILE (13,10) — map_1 real já tem 2 torres próprias.
        from engine.components import TileMovement as _TM
        remaining = [eid for eid, tm_ in self.ws.world.get_entities_with(_TM)
                    if self.ws.world.get_component(eid, Tower) is not None
                    and (tm_.current_tile_x, tm_.current_tile_y) == (13, 10)]
        self.assertEqual(remaining, [], "torre não-respawnável nunca deveria voltar neste tile")

    def test_torre_sincroniza_como_enemy_com_faction_correta(self):
        """Confirma sync pelo pipeline genérico de mob (zero protocolo
        novo) — ENTITY_SPAWN com kind='enemy' e a facção certa."""
        tower_eid = create_tower(self.ws.world, 14, 10, "torre_de_fogo",
                                 faction_id="guardas_vila")
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        self.assertIn(tower_eid, self.ws._mob_eids,
                     "torre deveria se auto-registrar em _mob_eids (gate Combatant)")
        data = self.ws.get_entity_spawn_data(tower_eid)
        self.assertIsNotNone(data)
        self.assertEqual(data["kind"], "enemy")
        self.assertEqual(data["faction"], "guardas_vila")

    def test_torre_reporta_is_ranged_true_no_payload_de_spawn(self):
        """Bug real (29/07/2026): torre não tem AIControlled, então
        `is_ranged` ficava travado no default False do payload de spawn
        (mesma classe de bug já documentada pra 'Arqueiro (NPC)' sem
        SpawnZone) — cliente escolhia som/alcance de melee pra uma torre
        que só ataca à distância."""
        tower_eid = create_tower(self.ws.world, 15, 10, "torre_de_flechas",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)
        data = self.ws.get_entity_spawn_data(tower_eid)
        self.assertTrue(data["is_ranged"], "torre deveria sempre reportar is_ranged=True")

    def test_torre_aparece_como_atacante_no_combat_log_do_player(self):
        """Bug real (29/07/2026): `_mob_attacker_of` (server/combat_
        processor.py) só reconhecia atacante com AIControlled — torre
        nunca aparecia, então o dano dela em um player resolvia
        attacker=-1 OU (pior) herdava do cache `_last_mob_attacker` o
        atacante ERRADO de um mob real diferente que tivesse batido no
        player antes. Isso quebrava a atribuição de som/log de combate
        (relatado pelo usuário: torre de flecha tocando som de
        attack_melee — na verdade estava tocando o som do ÚLTIMO mob
        real, não da torre)."""
        tower_eid = create_tower(self.ws.world, 16, 10, "torre_de_flechas",
                                 faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation("maps/map_1.csv"))
        run_ticks(self.ws, 1)   # garante registro em _mob_eids
        tower = self.ws.world.get_component(tower_eid, Tower)
        tower.current_target_eid = self.player_eid

        pcs = self.ws.world.get_component(self.player_eid, CombatStats)
        hp_before = pcs.current_hp
        pcs.current_hp = hp_before - 10   # simula o dano já aplicado pelo projétil

        self.ws._process_player_attacks(0.05, {self.player_eid: hp_before})
        attacks = [a for a in self.ws._pending_mob_attacks if a["target"] == self.player_eid]
        self.assertTrue(attacks, "deveria ter gerado uma entrada de combate pro player")
        self.assertTrue(any(a["attacker"] == tower_eid for a in attacks),
                        "torre deveria aparecer como atacante, nunca -1 ou outro mob")


class TestNexusTowerEndsMatch(unittest.TestCase):
    """Tower.is_nexus (02/08/2026, pedido do usuário) — torre derrubada com
    is_nexus=True dispara notify_nexus_destroyed via server_death_handler.py
    (server/server_death_handler.py:130-135), que termina a partida de teste
    carregada em server/debug_battleground.py (idempotente/no-op se não
    houver partida de teste ativa — Arena de verdade nunca usa is_nexus)."""

    def setUp(self):
        from server import debug_battleground as bg
        self.bg = bg
        self.ws = make_world_server()
        self.p_a = spawn_player(self.ws, "nexus_a", 10, 10, class_id="guerreiro")
        self.p_b = spawn_player(self.ws, "nexus_b", 12, 10, class_id="guerreiro")
        self.ws.world.add_component(self.p_a, Faction("arena_time_a"))
        self.ws.world.add_component(self.p_b, Faction("arena_time_b"))
        bg._state["members"] = {self.p_a, self.p_b}
        bg._state["stat_snapshots"] = {
            self.p_a: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0},
            self.p_b: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0},
        }
        bg._state["match_decided"]         = False
        bg._state["result_deadline"]       = None
        bg._state["pending_match_result"]  = []

    def tearDown(self):
        self.bg._state["members"]            = set()
        self.bg._state["stat_snapshots"]     = {}
        self.bg._state["match_decided"]      = False
        self.bg._state["result_deadline"]    = None
        self.bg._state["pending_match_result"] = []

    def _kill_nexus(self, faction_id: str, killer_eid: int):
        tower_eid = create_tower(self.ws.world, 20, 10, "torre_de_fogo",
                                 faction_id=faction_id, is_nexus=True)
        # MapLocation precisa ser a instância REAL de debug (04/08/2026 —
        # notify_nexus_destroyed passou a checar `tower_map_file` pra não
        # confundir com partidas da fila real, ver server/bg_queue_
        # processor.py) — uma torre no mapa aberto nunca deveria disparar
        # o fim de uma partida de teste.
        self.ws.world.add_component(tower_eid, MapLocation(self.bg.DEBUG_BG_INSTANCE_KEY))
        run_ticks(self.ws, 1)
        cs = self.ws.world.get_component(tower_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(tower_eid, PendingDeath(killer_entity_id=killer_eid))
        self.ws._mob_damage_log[tower_eid] = {killer_eid: 999}
        run_ticks(self.ws, 1)
        return tower_eid

    def test_derrubar_nexus_do_time_b_declara_time_a_vencedor(self):
        self._kill_nexus("arena_time_b", self.p_a)
        self.assertTrue(self.bg._state["match_decided"])
        self.assertEqual(len(self.bg._state["pending_match_result"]), 2)
        _, payload = self.bg._state["pending_match_result"][0]
        self.assertEqual(payload["winner_faction"], "arena_time_a")
        by_eid = {p["eid"]: p for p in payload["players"]}
        self.assertTrue(by_eid[self.p_a]["won"])
        self.assertFalse(by_eid[self.p_b]["won"])

    def test_torre_normal_nao_termina_partida(self):
        """Prova de que só is_nexus=True dispara o fim de partida — torre
        comum (respawnável, sem is_nexus) derrubada não deve afetar
        match_decided."""
        tower_eid = create_tower(self.ws.world, 21, 10, "torre_de_flechas",
                                 faction_id="arena_time_b")
        self.ws.world.add_component(tower_eid, MapLocation(self.ws.MAP_FILE))
        run_ticks(self.ws, 1)
        cs = self.ws.world.get_component(tower_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(tower_eid, PendingDeath(killer_entity_id=self.p_a))
        self.ws._mob_damage_log[tower_eid] = {self.p_a: 999}
        run_ticks(self.ws, 1)
        self.assertFalse(self.bg._state["match_decided"],
                         "torre sem is_nexus nunca deveria terminar a partida")


if __name__ == "__main__":
    unittest.main()
