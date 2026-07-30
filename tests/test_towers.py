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
from engine.entity_factory import create_tower, create_enemy, create_player, create_tilemap
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

    def test_alvo_sticky_nao_reavalia_a_cada_tick(self):
        """2 mobs no alcance — a torre deve manter o PRIMEIRO alvo mesmo se
        um segundo mob ficar mais próximo depois."""
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="guardas_vila")
        mob_far_eid = create_enemy(self.world, 15, 10, race="Lobo", faction="monstros_hostis")
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


if __name__ == "__main__":
    unittest.main()
