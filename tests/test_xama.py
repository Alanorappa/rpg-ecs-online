"""
tests/test_xama.py

Classe Xamã (build Raiz) — Fase 1 + Sopro da Coruja (24/10/2026).

Cobre:
  - `engine.core_systems.apply_heal` (ponto único NOVO de escrita de cura
    em HP — teste automatizado obrigatório pela regra de pontos únicos).
  - Recurso Espírito: máximo da classe, regen só fora de combate.
  - Toque Ancestral: cast real pela fila do servidor causa dano e devolve
    Espírito (+ Sussurros Constantes).
  - Sopro da Coruja: cura aliado mirado / o próprio Xamã sem aliado, cobra
    Espírito (com desconto de Alma Generosa), recusa sem Espírito.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, authorize_skill
from engine.components import CombatStats, CharacterStats, CombatState
from engine import core_systems


class TestApplyHeal(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "ah_p", 130, 374, class_id="xama")
        self.cs = self.ws.world.get_component(self.eid, CombatStats)

    def test_cura_limitada_ao_hp_maximo(self):
        self.cs.current_hp = self.cs.max_hp - 10
        healed = core_systems.apply_heal(self.ws.world, self.eid, self.eid, 500)
        self.assertEqual(healed, 10)
        self.assertEqual(self.cs.current_hp, self.cs.max_hp)

    def test_alvo_morto_nao_recebe_cura(self):
        self.cs.current_hp = 0
        self.assertEqual(core_systems.apply_heal(self.ws.world, self.eid, self.eid, 50), 0)
        self.assertEqual(self.cs.current_hp, 0)

    def test_valor_nao_positivo_e_entidade_sem_hp(self):
        self.cs.current_hp = 10
        self.assertEqual(core_systems.apply_heal(self.ws.world, self.eid, self.eid, 0), 0)
        other = self.ws.world.create_entity()
        self.assertEqual(core_systems.apply_heal(self.ws.world, self.eid, other, 50), 0)

    def test_cura_vira_evento_de_combate_regen(self):
        self.cs.current_hp = self.cs.max_hp - 40
        self.ws._combat_this_tick.clear()
        core_systems.apply_heal(self.ws.world, self.eid, self.eid, 25, sid="sopro_da_coruja")
        evs = [e for e in self.ws._combat_this_tick if e.get("source") == "heal"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["damage"], -25)
        self.assertEqual(evs[0]["outcome"], "regen")
        self.assertEqual(evs[0]["target"], self.eid)

    def test_cura_sem_efeito_nao_gera_evento(self):
        self.cs.current_hp = self.cs.max_hp
        self.ws._combat_this_tick.clear()
        core_systems.apply_heal(self.ws.world, self.eid, self.eid, 25)
        self.assertFalse([e for e in self.ws._combat_this_tick if e.get("source") == "heal"])


class TestEspirito(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "esp_p", 130, 374, class_id="xama")
        self.char = self.ws.world.get_component(self.eid, CharacterStats)

    def test_xama_nasce_com_espirito_cheio(self):
        self.assertEqual(self.char.max_espirito, 100)
        self.assertEqual(self.char.espirito, 100)

    def test_outras_classes_sem_espirito(self):
        g = spawn_player(self.ws, "esp_g", 132, 374, class_id="guerreiro")
        self.assertEqual(self.ws.world.get_component(g, CharacterStats).max_espirito, 0)

    def test_regen_fora_de_combate(self):
        self.char.espirito = 50
        for _ in range(10):               # 1s, em blocos curtos (ver debug do helper)
            run_ticks(self.ws, 2, dt=0.05)
        self.assertAlmostEqual(self.char.espirito, 55, delta=0.6)

    def test_sem_regen_em_combate(self):
        self.char.espirito = 50
        cst = self.ws.world.get_component(self.eid, CombatState)
        from engine.stat_fns import enter_combat
        enter_combat(cst)
        run_ticks(self.ws, 20, dt=0.05)
        self.assertAlmostEqual(self.char.espirito, 50, delta=0.01)


class _XamaDuelBase(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.xama = spawn_player(self.ws, "x_xama", 130, 374, class_id="xama", hp=300)
        self.char = self.ws.world.get_component(self.xama, CharacterStats)
        self.xcs  = self.ws.world.get_component(self.xama, CombatStats)

    def _cast(self, sid, tid, ticks):
        self.ws.queue_skill("x_xama", sid, tid=tid, dir_x=0.0, dir_y=0.0, ts=0)
        return run_ticks(self.ws, ticks, dt=0.05)


class TestToqueAncestral(_XamaDuelBase):
    def setUp(self):
        super().setUp()
        self.victim = spawn_player(self.ws, "x_victim", 133, 374, class_id="guerreiro", hp=500)
        self.assertIsNone(self.ws.request_duel(self.xama, self.victim))
        self.ws.respond_duel_invite(self.victim, accept=True)
        authorize_skill(self.ws, self.xama, "toque_ancestral")
        self.vcs = self.ws.world.get_component(self.victim, CombatStats)

    def _cast_until_hit(self):
        """Magia pode errar (rolagem de acerto) — tenta até 6 casts."""
        for _ in range(6):
            hp0, esp0 = self.vcs.current_hp, self.char.espirito
            self._cast("toque_ancestral", self.victim, 40)   # 1s cast + CD 1.5s de folga
            if self.vcs.current_hp < hp0:
                return hp0 - self.vcs.current_hp, self.char.espirito - esp0
        self.fail("Toque Ancestral nunca acertou em 6 casts")

    def test_causa_dano_e_gera_espirito(self):
        self.char.espirito = 20
        dmg, gain = self._cast_until_hit()
        self.assertGreater(dmg, 0)
        self.assertAlmostEqual(gain, 8, delta=0.5)    # +regen de ~1 tick antes de entrar em combate

    def test_sussurros_constantes_soma_espirito(self):
        self.char.espirito = 20
        self.xcs.toque_espirito_bonus = 3
        _dmg, gain = self._cast_until_hit()
        self.assertAlmostEqual(gain, 11, delta=0.5)

    def test_espirito_nao_passa_do_maximo(self):
        self.char.espirito = 98
        self._cast_until_hit()
        self.assertEqual(self.char.espirito, 100)


class TestSoproDaCoruja(_XamaDuelBase):
    def setUp(self):
        super().setUp()
        authorize_skill(self.ws, self.xama, "sopro_da_coruja")
        self.ally = spawn_player(self.ws, "x_ally", 133, 374, class_id="guerreiro", hp=500)
        self.acs = self.ws.world.get_component(self.ally, CombatStats)

    def _expected_heal(self):
        return 60 + int(self.xcs.spell_power * 1.20)

    def test_cura_aliado_mirado_e_cobra_espirito(self):
        self.acs.current_hp = 100
        self._cast("sopro_da_coruja", self.ally, 40)   # 1.5s cast
        self.assertEqual(self.acs.current_hp, min(self.acs.max_hp, 100 + self._expected_heal()))
        self.assertAlmostEqual(self.char.espirito, 70, delta=3)   # cura não põe em combate: regen volta

    def test_mirar_aliado_nao_vira_alvo_de_combate(self):
        self.acs.current_hp = 100
        self._cast("sopro_da_coruja", self.ally, 40)
        cst = self.ws.world.get_component(self.xama, CombatState)
        self.assertNotEqual(cst.target_entity_id, self.ally)

    def test_sem_aliado_cura_o_proprio_xama(self):
        self.xcs.current_hp = 50
        self._cast("sopro_da_coruja", -1, 40)
        self.assertGreaterEqual(self.xcs.current_hp, min(self.xcs.max_hp, 50 + self._expected_heal()))

    def test_aliado_fora_de_alcance_nao_cura(self):
        far = spawn_player(self.ws, "x_far", 150, 374, class_id="guerreiro", hp=500)
        fcs = self.ws.world.get_component(far, CombatStats)
        fcs.current_hp = 100
        self._cast("sopro_da_coruja", far, 40)
        self.assertLessEqual(fcs.current_hp, 101)   # só HP5 natural, nunca a cura
        self.assertAlmostEqual(self.char.espirito, 100, delta=0.01)

    def test_alma_generosa_reduz_custo(self):
        self.xcs.cura_custo_reducao = 10
        self.acs.current_hp = 100
        self._cast("sopro_da_coruja", self.ally, 40)
        self.assertAlmostEqual(self.char.espirito, 80, delta=3)

    def test_espirito_insuficiente_recusa(self):
        self.char.espirito = 10
        self.acs.current_hp = 100
        from engine.stat_fns import enter_combat
        enter_combat(self.ws.world.get_component(self.xama, CombatState))  # sem regen
        self._cast("sopro_da_coruja", self.ally, 40)
        self.assertLessEqual(self.acs.current_hp, 101)
        self.assertAlmostEqual(self.char.espirito, 10, delta=0.01)


if __name__ == "__main__":
    unittest.main()
