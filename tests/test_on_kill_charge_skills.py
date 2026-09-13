"""
tests/test_on_kill_charge_skills.py — skills com `on_kill=="charge"`
(Vitória Iminente) precisam conceder carga tanto matando MOB/minion/torre
quanto matando JOGADOR (PvP).

Bug real relatado pelo usuário (13/09/2026): "Vitória Iminente não está
ganhando carga quando o guerreiro mata um alvo" — só acontecia dentro da
Battleground. Investigação: a concessão de carga só existia dentro do
loop de morte de mob (`ServerDeathHandler.update()`,
`server/server_death_handler.py`) — morte de JOGADOR passa por um
caminho de código TOTALMENTE separado (`RespawnMixin._handle_player_death`,
`server/respawn_system.py`, crédito de kill estilo LoL) que nunca teve
essa lógica. Como a BG é o modo onde matar jogador é a atividade
principal, a carga nunca acendia lá — mesmo funcionando normalmente
contra mob/minion/torre (inclusive dentro da própria BG).

Fix: lógica extraída pra `grant_on_kill_charge_skills()`
(`server/server_death_handler.py`), chamada dos DOIS pontos de morte.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import unittest

from tests.helpers import make_world_server, spawn_player, authorize_skill, engage_target
from engine.components import CombatStats, Faction, PlayerSkills, CharacterStats
from engine.entity_factory import create_enemy
from engine.core_systems import apply_damage_core
from server.instance_progression import enter_normalized_progression

_FAC_A = "arena_time_a"
_FAC_B = "arena_time_b"


class TestOnKillChargeMobPath(unittest.TestCase):
    """Regressão do caminho original (mob/minion/torre) após a extração
    pra função compartilhada — precisa continuar funcionando igual."""

    def setUp(self):
        self.ws = make_world_server()

    def test_matar_mob_concede_carga_com_skill_so_aprendida_nao_na_hotbar(self):
        player = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, player, "vitoria_iminente")
        ps = self.ws.world.get_component(player, PlayerSkills)
        self.assertIsNone(ps.skill_by_id("vitoria_iminente"),
            "setup: skill não deveria estar na hotbar ainda (só aprendida)")

        mob = create_enemy(self.ws.world, 131, 374, faction="monstros_hostis")
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        apply_damage_core(self.ws.world, mob, mob_cs.current_hp + 999, killer_eid=player)
        self.ws._death_handler.update()

        sk = self.ws.world.get_component(player, PlayerSkills).skill_by_id("vitoria_iminente")
        self.assertIsNotNone(sk, "skill deveria ter sido lazy-created na hotbar")
        self.assertEqual(sk.charges, sk.max_charges)


class TestOnKillChargePlayerPath(unittest.TestCase):
    """O bug relatado: matar JOGADOR (PvP/BG) precisa conceder a mesma
    carga que matar mob já concedia."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s_a", 10, 10, class_id="guerreiro")
        self.v = spawn_player(self.ws, "s_v", 11, 10, class_id="guerreiro")
        self.ws.world.add_component(self.a, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(self.v, Faction(faction_id=_FAC_B))
        for eid in (self.a, self.v):
            enter_normalized_progression(self.ws, eid)
            self.ws.world.get_component(eid, CombatStats).acerto = 100.0
        authorize_skill(self.ws, self.a, "vitoria_iminente")

    def _kill_v_with_a(self):
        vic_cs = self.ws.world.get_component(self.v, CombatStats)
        vic_cs.dodge_rating = 0.0
        vic_cs.parry_rating = 0.0
        vic_cs.current_hp = 1  # 1 golpe mata
        snapshot = {self.v: vic_cs.current_hp}
        engage_target(self.ws, "s_a", self.v)
        self.ws._attack_timers["s_a"] = 0.0
        self.ws._process_player_attacks(0.05, snapshot)

    def test_matar_jogador_concede_carga_pro_matador(self):
        ps_a = self.ws.world.get_component(self.a, PlayerSkills)
        self.assertIsNone(ps_a.skill_by_id("vitoria_iminente"),
            "setup: skill não deveria estar na hotbar da instância ainda")

        self._kill_v_with_a()

        sk = self.ws.world.get_component(self.a, PlayerSkills).skill_by_id("vitoria_iminente")
        self.assertIsNotNone(sk,
            "matar um JOGADOR deveria conceder a mesma carga que matar mob concede")
        self.assertEqual(sk.charges, sk.max_charges)
        self.assertEqual(sk.charge_timer, sk.charge_timeout)

    def test_assistente_nao_ganha_carga_so_o_killer_creditado(self):
        """Um 3º player que só assistiu (não foi o último a bater dentro
        da janela) não deveria ganhar a carga — só quem leva o kill."""
        c = spawn_player(self.ws, "s_c", 12, 10, class_id="guerreiro")
        self.ws.world.add_component(c, Faction(faction_id=_FAC_A))
        enter_normalized_progression(self.ws, c)
        authorize_skill(self.ws, c, "vitoria_iminente")

        import time
        self.ws._player_damage_seq += 1
        self.ws._player_damage_log.setdefault(self.v, {})[c] = (
            time.time(), self.ws._player_damage_seq)

        self._kill_v_with_a()

        sk_c = self.ws.world.get_component(c, PlayerSkills).skill_by_id("vitoria_iminente")
        self.assertIsNone(sk_c, "assistente não deveria ganhar carga, só o killer creditado")


if __name__ == "__main__":
    unittest.main(verbosity=2)
