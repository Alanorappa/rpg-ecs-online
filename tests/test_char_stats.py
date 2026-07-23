"""
tests/test_char_stats.py — CharStatsTracker (Fase E, 23/07/2026): modal de
estatísticas do personagem. Server-autoritativo, mesma regra de
SkillLevels/QuestLog — ver engine/components.py::CharStatsTracker e
ARQUITETURA_ONLINE.md.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, set_entity_tile
from engine.components import CharStatsTracker, PendingDeath, CombatStats
from engine.core_systems import apply_damage_core


class TestDamageTracking(unittest.TestCase):
    """_damage_tracker_composite (server/world_server.py) distingue PvE de
    PvP olhando se o ALVO é um player — mesma regra usada por
    _is_player_entity (engine/faction_system.py)."""

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)
        self.p1 = spawn_player(self.ws, "cst_p1", 130, 374, class_id="guerreiro")
        self.p2 = spawn_player(self.ws, "cst_p2", 132, 374, class_id="mago")
        self.mob = first_mob(self.ws)
        if not self.mob:
            self.skipTest("Sem mobs")

    def test_dano_em_mob_credita_pve_damage(self):
        cst = self.ws.world.get_component(self.p1, CharStatsTracker)
        apply_damage_core(self.ws.world, self.mob, 10, killer_eid=self.p1)
        self.assertEqual(cst.pve_damage, 10)
        self.assertEqual(cst.pvp_damage, 0)

    def test_dano_em_player_credita_pvp_damage_nao_pve(self):
        cst = self.ws.world.get_component(self.p1, CharStatsTracker)
        self.assertIsNone(self.ws.request_duel(self.p1, self.p2))
        self.ws.respond_duel_invite(self.p2, accept=True)
        apply_damage_core(self.ws.world, self.p2, 15, killer_eid=self.p1)
        self.assertEqual(cst.pvp_damage, 15)
        self.assertEqual(cst.pve_damage, 0)

    def test_dano_bloqueado_por_faccao_nao_incrementa_nada(self):
        """p1 ataca p2 sem nenhum contexto PvP (amigável por padrão) —
        apply_damage_core devolve blocked_friendly ANTES do tracker, então
        nenhum contador deve mudar."""
        cst = self.ws.world.get_component(self.p1, CharStatsTracker)
        outcome = apply_damage_core(self.ws.world, self.p2, 15, killer_eid=self.p1)
        self.assertEqual(outcome, "blocked_friendly")
        self.assertEqual(cst.pvp_damage, 0)


class TestMobKillTracking(unittest.TestCase):
    """mobs_killed credita o first-attacker (mesmo dono do loot/quest),
    incrementado por ServerDeathHandler.update() — server/server_death_handler.py."""

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)
        self.p1 = spawn_player(self.ws, "cst_mk1", 130, 374, class_id="guerreiro")
        self.mob = first_mob(self.ws)
        if not self.mob:
            self.skipTest("Sem mobs")

    def test_mob_morto_credita_mobs_killed_do_first_attacker(self):
        cst = self.ws.world.get_component(self.p1, CharStatsTracker)
        mob_cs = self.ws.world.get_component(self.mob, CombatStats)
        mob_cs.current_hp = 1
        apply_damage_core(self.ws.world, self.mob, 999, killer_eid=self.p1)
        self.ws.world.add_component(self.mob, PendingDeath(killer_entity_id=self.p1))
        self.ws._death_handler.update()
        self.assertEqual(cst.mobs_killed, 1)


class TestDuelTracking(unittest.TestCase):
    """duel_wins/duel_losses incrementados em DuelProcessorMixin.end_duel
    (server/duel_processor.py) só em vitória de verdade (reason='win')."""

    def setUp(self):
        self.ws = make_world_server()
        self.p1 = spawn_player(self.ws, "cst_d1", 130, 374, class_id="guerreiro")
        self.p2 = spawn_player(self.ws, "cst_d2", 132, 374, class_id="mago")
        self.assertIsNone(self.ws.request_duel(self.p1, self.p2))
        self.ws.respond_duel_invite(self.p2, accept=True)

    def test_golpe_letal_credita_vencedor_e_perdedor(self):
        cst1 = self.ws.world.get_component(self.p1, CharStatsTracker)
        cst2 = self.ws.world.get_component(self.p2, CharStatsTracker)
        p2_cs = self.ws.world.get_component(self.p2, CombatStats)
        p2_cs.current_hp = 5
        apply_damage_core(self.ws.world, self.p2, 999, killer_eid=self.p1)
        self.assertEqual(cst1.duel_wins, 1)
        self.assertEqual(cst2.duel_losses, 1)
        self.assertEqual(cst1.duel_losses, 0)
        self.assertEqual(cst2.duel_wins, 0)

    def test_encerramento_por_distancia_nao_credita_ninguem(self):
        cst1 = self.ws.world.get_component(self.p1, CharStatsTracker)
        cst2 = self.ws.world.get_component(self.p2, CharStatsTracker)
        pair = self.ws._duel_pair_of(self.p1)
        self.ws.end_duel(pair, winner_eid=-1, reason="distance")
        self.assertEqual(cst1.duel_wins, 0)
        self.assertEqual(cst1.duel_losses, 0)
        self.assertEqual(cst2.duel_wins, 0)
        self.assertEqual(cst2.duel_losses, 0)


class TestPlayersKilledTracking(unittest.TestCase):
    """players_killed (kill de PvP de verdade, fora de duelo/arena — ver
    server_death_handler.py) exige uma zona PvP (mesmo setup de
    tests/test_pvp_zone.py) já que duelo/arena interceptam o golpe letal
    antes de gerar PendingDeath de verdade."""

    def setUp(self):
        self.ws = make_world_server()
        self.p1 = spawn_player(self.ws, "cst_pk1", 110, 110, class_id="guerreiro")
        self.p2 = spawn_player(self.ws, "cst_pk2", 110, 110, class_id="mago")
        map_file = self.ws.get_entity_map(self.p1)
        self.ws._pvp_zones_by_map[map_file] = [{"name": "Zona Teste", "rect": (100, 100, 120, 120)}]

    def test_kill_de_verdade_em_zona_pvp_credita_players_killed(self):
        cst1 = self.ws.world.get_component(self.p1, CharStatsTracker)
        p2_cs = self.ws.world.get_component(self.p2, CombatStats)
        p2_cs.current_hp = 5
        apply_damage_core(self.ws.world, self.p2, 999, killer_eid=self.p1)
        self.assertLessEqual(p2_cs.current_hp, 0)
        self.ws._death_handler.update()
        self.assertEqual(cst1.players_killed, 1)


class TestArenaTracking(unittest.TestCase):
    """arena_wins/arena_losses (por modo — hoje só '2v2' existe) creditados
    em MatchProcessorMixin._finish_match (server/match_processor.py)."""

    def setUp(self):
        self.ws = make_world_server()
        self.a1 = spawn_player(self.ws, "cst_a1", 130, 374, class_id="guerreiro")
        self.a2 = spawn_player(self.ws, "cst_a2", 132, 374, class_id="guerreiro")
        self.b1 = spawn_player(self.ws, "cst_b1", 140, 374, class_id="mago")
        self.b2 = spawn_player(self.ws, "cst_b2", 142, 374, class_id="mago")

    def test_finish_match_credita_vitoria_e_derrota_por_modo(self):
        match_id = "arena2v2_test"
        self.ws._active_matches[match_id] = {
            "instance_key": None, "team_a": [self.a1, self.a2], "team_b": [self.b1, self.b2],
            "invited_a": [], "invited_b": [], "eliminated": set(), "return_pos": {},
            "damage_by_eid": {}, "arena_locked": set(), "decided": False, "decided_at": 0.0,
            "winner_members": set(),
        }
        self.ws._finish_match(match_id, "team_a")
        cst_a1 = self.ws.world.get_component(self.a1, CharStatsTracker)
        cst_b1 = self.ws.world.get_component(self.b1, CharStatsTracker)
        self.assertEqual(cst_a1.arena_wins["2v2"], 1)
        self.assertEqual(cst_a1.arena_losses["2v2"], 0)
        self.assertEqual(cst_b1.arena_losses["2v2"], 1)
        self.assertEqual(cst_b1.arena_wins["2v2"], 0)

    def test_finish_match_sem_vencedor_nao_credita_ninguem(self):
        match_id = "arena2v2_test2"
        self.ws._active_matches[match_id] = {
            "instance_key": None, "team_a": [self.a1, self.a2], "team_b": [self.b1, self.b2],
            "invited_a": [], "invited_b": [], "eliminated": set(), "return_pos": {},
            "damage_by_eid": {}, "arena_locked": set(), "decided": False, "decided_at": 0.0,
            "winner_members": set(),
        }
        self.ws._finish_match(match_id, None)
        cst_a1 = self.ws.world.get_component(self.a1, CharStatsTracker)
        self.assertEqual(cst_a1.arena_wins["2v2"], 0)
        self.assertEqual(cst_a1.arena_losses["2v2"], 0)


class TestSaveLoadRoundTrip(unittest.TestCase):
    """get_player_save_data → char_stats — mesmo contrato de quests/skill_levels."""

    def test_get_player_save_data_inclui_char_stats(self):
        ws = make_world_server()
        p1 = spawn_player(ws, "cst_sv1", 130, 374, class_id="guerreiro")
        cst = ws.world.get_component(p1, CharStatsTracker)
        cst.pve_damage  = 42
        cst.mobs_killed = 3
        cst.arena_wins["3v3"] = 2
        sid = ws._player_eid_to_sid.get(p1)
        data = ws.get_player_save_data(sid)
        self.assertIn("char_stats", data)
        self.assertEqual(data["char_stats"]["pve_damage"], 42)
        self.assertEqual(data["char_stats"]["mobs_killed"], 3)
        self.assertEqual(data["char_stats"]["arena_wins"]["3v3"], 2)


if __name__ == "__main__":
    unittest.main()
