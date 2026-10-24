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


# ── Fase 2: mecânicas por dado no catálogo de efeitos ─────────────────────────

class TestEfeitosPorDado(unittest.TestCase):
    """apply_damage_core (ponto único) + sync_status_derived_state lendo
    EffectDef.damage_taken_mult / absorbs / speed_mult / blocks_stealth."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "fx_a", 130, 374, class_id="guerreiro", hp=500)
        self.cs = self.ws.world.get_component(self.a, CombatStats)
        self.cs.current_hp = 400

    def test_marca_amplifica_dano_recebido(self):
        core_systems.apply_effect(self.ws.world, self.a, "marca_do_gaviao", 12.0)
        core_systems.apply_damage_core(self.ws.world, self.a, 100)
        self.assertEqual(self.cs.current_hp, 400 - 105)

    def test_escudo_absorve_parcial_e_some_ao_zerar(self):
        from engine.components import StatusEffects
        core_systems.apply_effect(self.ws.world, self.a, "pele_do_urso", 8.0, magnitude=60.0)
        core_systems.apply_damage_core(self.ws.world, self.a, 40)
        self.assertEqual(self.cs.current_hp, 400)
        sfx = self.ws.world.get_component(self.a, StatusEffects)
        self.assertAlmostEqual(sfx.get("pele_do_urso").magnitude, 20.0)
        core_systems.apply_damage_core(self.ws.world, self.a, 50)
        self.assertEqual(self.cs.current_hp, 400 - 30)
        self.assertFalse(sfx.has("pele_do_urso"))

    def test_marca_e_escudo_juntos(self):
        core_systems.apply_effect(self.ws.world, self.a, "marca_do_gaviao", 12.0)
        core_systems.apply_effect(self.ws.world, self.a, "pele_do_urso", 8.0, magnitude=100.0)
        core_systems.apply_damage_core(self.ws.world, self.a, 200)   # 210 - 100
        self.assertEqual(self.cs.current_hp, 400 - 110)

    def test_escudo_segura_dot(self):
        """Bug relatado em playtest: sangramento do zumbi passava direto
        pelo escudo (DoT não passa por apply_damage_core)."""
        from engine.components import StatusEffects, ActiveEffect
        core_systems.apply_effect(self.ws.world, self.a, "pele_do_urso", 8.0, magnitude=50.0)
        sfx = self.ws.world.get_component(self.a, StatusEffects)
        sfx.effects["bleed"] = ActiveEffect("bleed", 10.0, magnitude=20.0, tick_interval=1.0)
        core_systems.StatusEffectSystem(self.ws.world)._apply_tick(self.a, sfx.effects["bleed"])
        self.assertEqual(self.cs.current_hp, 400)
        self.assertAlmostEqual(sfx.get("pele_do_urso").magnitude, 30.0)

    def test_absorcao_vira_evento_absorb(self):
        core_systems.apply_effect(self.ws.world, self.a, "pele_do_urso", 8.0, magnitude=30.0)
        self.ws._combat_this_tick.clear()
        core_systems.apply_damage_core(self.ws.world, self.a, 50)
        evs = [e for e in self.ws._combat_this_tick if e.get("outcome") == "absorb"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["absorbed"], 30)
        self.assertEqual(evs[0]["target"], self.a)
        self.assertEqual(self.cs.current_hp, 400 - 20)

    def test_sem_efeito_dano_inalterado(self):
        core_systems.apply_damage_core(self.ws.world, self.a, 77)
        self.assertEqual(self.cs.current_hp, 400 - 77)

    def test_lobo_acelera_e_combina_com_lentidao(self):
        from engine.components import StatusEffects, TileMovement
        tm = self.ws.world.get_component(self.a, TileMovement)
        core_systems.apply_effect(self.ws.world, self.a, "espirito_do_lobo", 8.0)
        sfx = self.ws.world.get_component(self.a, StatusEffects)
        core_systems.sync_status_derived_state(self.ws.world, self.a, sfx)
        self.assertAlmostEqual(tm.slow_mult, 1.2)
        core_systems.apply_effect(self.ws.world, self.a, "slow", 5.0, magnitude=0.5)
        core_systems.sync_status_derived_state(self.ws.world, self.a, sfx)
        self.assertAlmostEqual(tm.slow_mult, 0.6)
        sfx.remove("espirito_do_lobo"); sfx.remove("slow")
        core_systems.sync_status_derived_state(self.ws.world, self.a, sfx)
        self.assertAlmostEqual(tm.slow_mult, 1.0)

    def test_marcado_nao_camufla(self):
        arq = spawn_player(self.ws, "fx_arq", 132, 374, class_id="arqueiro", hp=300)
        authorize_skill(self.ws, arq, "camuflagem")
        self.ws.world.get_component(arq, CharacterStats).concentration = 100
        core_systems.apply_effect(self.ws.world, arq, "marca_do_gaviao", 12.0)
        self.ws.queue_skill("fx_arq", "camuflagem", tid=-1, dir_x=0.0, dir_y=0.0, ts=0)
        run_ticks(self.ws, 40, dt=0.05)
        self.assertLessEqual(self.ws.world.get_component(arq, CombatStats).camouflage_timer, 0)


class TestSkillsBaseFase2(_XamaDuelBase):
    def setUp(self):
        super().setUp()
        for sid in ("espirito_do_lobo", "pele_do_urso", "marca_do_gaviao", "investida_do_bisao"):
            authorize_skill(self.ws, self.xama, sid)
        self.ally = spawn_player(self.ws, "x_ally2", 132, 374, class_id="guerreiro", hp=500)

    def _sfx(self, eid):
        from engine.components import StatusEffects
        return self.ws.world.get_component(eid, StatusEffects)

    def test_lobo_em_aliado_cobra_15(self):
        self._cast("espirito_do_lobo", self.ally, 2)
        self.assertTrue(self._sfx(self.ally).has("espirito_do_lobo"))
        self.assertAlmostEqual(self.char.espirito, 85, delta=0.6)

    def test_lobo_sem_aliado_vai_em_si(self):
        self._cast("espirito_do_lobo", -1, 2)
        self.assertTrue(self._sfx(self.xama).has("espirito_do_lobo"))

    def test_urso_escudo_com_valor_do_sp(self):
        self._cast("pele_do_urso", self.ally, 2)
        eff = self._sfx(self.ally).get("pele_do_urso")
        self.assertIsNotNone(eff)
        self.assertEqual(int(eff.magnitude), 60 + int(self.xcs.spell_power * 0.80))
        self.assertAlmostEqual(self.char.espirito, 75, delta=0.6)

    def test_espirito_insuficiente_recusa_lobo(self):
        from engine.stat_fns import enter_combat
        enter_combat(self.ws.world.get_component(self.xama, CombatState))
        self.char.espirito = 5
        self._cast("espirito_do_lobo", self.ally, 2)
        sfx = self._sfx(self.ally)
        self.assertFalse(sfx is not None and sfx.has("espirito_do_lobo"))

    def _duel(self, x):
        v = spawn_player(self.ws, f"x_v{x}", x, 374, class_id="guerreiro", hp=500)
        self.assertIsNone(self.ws.request_duel(self.xama, v))
        self.ws.respond_duel_invite(v, accept=True)
        return v

    def test_gaviao_marca_inimigo(self):
        v = self._duel(134)
        self._cast("marca_do_gaviao", v, 2)
        self.assertTrue(self._sfx(v).has("marca_do_gaviao"))
        self.assertAlmostEqual(self.char.espirito, 80, delta=0.01)

    def test_bisao_avanca_causa_dano_e_empurra(self):
        from engine.components import TileMovement
        from engine.utils import snap_to_tile
        # Fileira sem obstáculo em map_1 (111..117, 359..361) — a arrancada
        # exige caminho livre.
        snap_to_tile(self.ws.world, self.xama, 111, 360)
        v = spawn_player(self.ws, "x_vb", 114, 360, class_id="guerreiro", hp=500)
        self.assertIsNone(self.ws.request_duel(self.xama, v))
        self.ws.respond_duel_invite(v, accept=True)
        vcs = self.ws.world.get_component(v, CombatStats)
        for _ in range(6):                  # magia pode errar
            hp0 = vcs.current_hp
            vtm = self.ws.world.get_component(v, TileMovement)
            vx0 = vtm.current_tile_x
            self._cast("investida_do_bisao", v, 15)
            if vcs.current_hp < hp0:
                break
            self.ws._skill_last_used.clear()
        else:
            self.fail("Investida do Bisão nunca acertou")
        xtm = self.ws.world.get_component(self.xama, TileMovement)
        self.assertEqual(xtm.target_tile_x, vx0 - 1)          # parou ao lado do alvo
        self.assertEqual(vtm.current_tile_x, vx0 + 1)         # alvo empurrado 1 tile


if __name__ == "__main__":
    unittest.main()
