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
from engine.components import CombatStats, CharacterStats, CombatState, TileMovement
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


def _sem_mobs(ws):
    """Determinismo: nenhum mob nascendo/vivo puxando combate no teste."""
    from engine.components import AIControlled, SpawnZone
    for _z, zone in ws.world.get_entities_with(SpawnZone):
        zone.max_count = 0
        zone.respawn_timers.clear()
    for m in list(ws._mob_eids):
        if ws.world.get_component(m, AIControlled) is not None:
            ws.world.remove_entity(m)
            ws._mob_eids.discard(m)


class TestEspirito(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        _sem_mobs(self.ws)
        self.eid = spawn_player(self.ws, "esp_p", 130, 374, class_id="xama")
        self.char = self.ws.world.get_component(self.eid, CharacterStats)

    def test_xama_nasce_com_espirito_vazio(self):
        self.assertEqual(self.char.max_espirito, 100)
        self.assertEqual(self.char.espirito, 0)

    def test_outras_classes_sem_espirito(self):
        g = spawn_player(self.ws, "esp_g", 132, 374, class_id="guerreiro")
        self.assertEqual(self.ws.world.get_component(g, CharacterStats).max_espirito, 0)

    def test_travado_fora_de_combate(self):
        self.char.espirito = 50
        for _ in range(10):               # 1s, em blocos curtos (ver debug do helper)
            run_ticks(self.ws, 2, dt=0.05)
        self.assertAlmostEqual(self.char.espirito, 50, delta=0.01)

    def test_regen_em_combate(self):
        self.char.espirito = 50
        cst = self.ws.world.get_component(self.eid, CombatState)
        from engine.stat_fns import enter_combat
        enter_combat(cst)
        for _ in range(10):
            run_ticks(self.ws, 2, dt=0.05)
        self.assertAlmostEqual(self.char.espirito, 55, delta=0.6)

    def test_reviver_zera(self):
        self.char.espirito = 80
        self.char.reset_volatile()
        self.assertEqual(self.char.espirito, 0)

    def test_morrer_zera(self):
        self.char.espirito = 80
        self.ws._handle_player_death(self.eid)
        self.assertEqual(self.char.espirito, 0)

    def test_morto_nao_regenera_mesmo_em_combate(self):
        """Bug do playtest: morto continuava 'em combate' e o Espírito subia."""
        from engine.stat_fns import enter_combat
        from engine.core_systems import BaseCombatStateSystem
        cst = self.ws.world.get_component(self.eid, CombatState)
        cs = self.ws.world.get_component(self.eid, CombatStats)
        enter_combat(cst)
        cs.current_hp = 0
        self.char.espirito = 0
        for _ in range(40):
            BaseCombatStateSystem._tick_espirito_regen(cst, self.char, 0.05, cs)
        self.assertEqual(self.char.espirito, 0)

    def test_regen_sobe_em_degraus_de_1s(self):
        from engine.stat_fns import enter_combat
        self.char.espirito = 50
        enter_combat(self.ws.world.get_component(self.eid, CombatState))
        valores = set()
        for _ in range(16):                 # ~0.8s em ticks de 0.05s
            run_ticks(self.ws, 1, dt=0.05)
            valores.add(self.char.espirito)
        self.assertEqual(valores, {50}, "não pode subir antes de 1s inteiro")
        for _ in range(6):
            run_ticks(self.ws, 1, dt=0.05)
        self.assertEqual(self.char.espirito, 55)


class TestTooltipTipoDeArma(unittest.TestCase):
    def _tipo(self, subtype, cls):
        from ui.ui_helpers import item_tooltip_lines
        from content.item_table import ITEMS as _IF
        i = _IF["mystic_staff"]()          # item real; só troca o tipo
        i.subtype, i.two_handed = subtype, False
        for line in item_tooltip_lines(i, cls):
            if isinstance(line[0], tuple):
                return line[1]
        return None

    def test_tipo_em_portugues_e_vermelho_quando_proibido(self):
        self.assertEqual(self._tipo("Sword", "xama"), ("Espada", (220, 80, 80)))
        self.assertEqual(self._tipo("Staff", "xama"), ("Cajado", (80, 220, 80)))
        self.assertEqual(self._tipo("Sword", "guerreiro"), ("Espada", (80, 220, 80)))


class _XamaDuelBase(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.xama = spawn_player(self.ws, "x_xama", 130, 374, class_id="xama", hp=300)
        self.char = self.ws.world.get_component(self.xama, CharacterStats)
        self.xcs  = self.ws.world.get_component(self.xama, CombatStats)
        self.char.espirito = 100   # começa vazio no jogo; aqui, tanque cheio pros testes
        # Determinismo: sem mobs da região puxando combate (regen em combate
        # mexeria nos valores exatos de custo).
        _sem_mobs(self.ws)

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
        """Magia pode errar (rolagem de acerto) — tenta até 6 casts (CD 8s
        zerado entre tentativas)."""
        for _ in range(6):
            hp0, esp0 = self.vcs.current_hp, self.char.espirito
            self._cast("toque_ancestral", self.victim, 40)   # 1s cast
            if self.vcs.current_hp < hp0:
                return hp0 - self.vcs.current_hp, esp0 - self.char.espirito
            self.ws._skill_last_used.clear()
            self.char.espirito = 100
        self.fail("Toque Ancestral nunca acertou em 6 casts")

    def test_causa_dano_e_custa_20(self):
        dmg, spent = self._cast_until_hit()
        self.assertGreater(dmg, 0)
        # custa 20; em combate regenera 5/s durante o cast (~1s)
        self.assertGreater(spent, 12)
        self.assertLessEqual(spent, 20)

    def test_corredor_da_hot_no_proprio_xama(self):
        from engine.components import StatusEffects
        self._cast_until_hit()
        eff = self.ws.world.get_component(self.xama, StatusEffects).get("corredor_ancestral")
        self.assertIsNotNone(eff, "Xamã sozinho no corredor recebe a cura inteira")
        self.assertAlmostEqual(eff.magnitude * 6, self._corredor_base(), delta=0.01)

    def _corredor_base(self):
        from content.skill_config import SKILL_CATALOG
        p = SKILL_CATALOG["toque_ancestral"]["params"]
        return p["corridor_heal"] + self.xcs.spell_power * p["corridor_sp_coeff"]

    def test_sussurros_constantes_aumenta_cura_do_corredor(self):
        from engine.components import StatusEffects
        self.xcs.corredor_cura_bonus = 0.30 * 5
        self._cast_until_hit()
        eff = self.ws.world.get_component(self.xama, StatusEffects).get("corredor_ancestral")
        self.assertAlmostEqual(eff.magnitude * 6, self._corredor_base() * 2.5, delta=0.01)

    def test_sem_espirito_recusa(self):
        self.char.espirito = 5
        self.ws.queue_skill("x_xama", "toque_ancestral", tid=self.victim, dir_x=0.0, dir_y=0.0, ts=0)
        run_ticks(self.ws, 1)
        self.assertFalse([e for e in self.ws._pending_spell_completions
                          if e["spell_id"] == "toque_ancestral"], "cast não deveria começar")


class TestDivisaoDoCorredor(unittest.TestCase):
    """core_systems.corridor_heal_shares — função pura."""

    def test_sozinho_recebe_tudo(self):
        s = core_systems.corridor_heal_shares((0, 0), (6, 0), {1: (6, 0)}, 1, 2, 60)
        self.assertEqual(s, {1: 60})

    def test_quem_esta_perto_do_alvo_recebe_mais(self):
        s = core_systems.corridor_heal_shares(
            (0, 0), (6, 0), {1: (6, 0), 2: (1, 0), 3: (4, 1)}, 1, 2, 100)
        self.assertGreater(s[2], s[3])
        self.assertGreater(s[3], s[1])
        self.assertAlmostEqual(sum(s.values()), 100)

    def test_fora_do_corredor_nao_entra(self):
        s = core_systems.corridor_heal_shares(
            (0, 0), (6, 0), {1: (6, 0), 2: (3, 3), 3: (-1, 0), 4: (8, 0)}, 1, 2, 100)
        self.assertEqual(set(s), {1})

    def test_largura_maior_com_rastros_de_alma(self):
        s = core_systems.corridor_heal_shares((0, 0), (6, 0), {1: (6, 0), 2: (3, 3)}, 1, 3, 100)
        self.assertIn(2, s)


class TestCorredorComAliado(_XamaDuelBase):
    def test_aliado_no_corredor_tambem_recebe(self):
        from engine.components import StatusEffects
        victim = spawn_player(self.ws, "x_v2", 134, 374, class_id="guerreiro", hp=500)
        self.assertIsNone(self.ws.request_duel(self.xama, victim))
        self.ws.respond_duel_invite(victim, accept=True)
        ally = spawn_player(self.ws, "x_al2", 133, 374, class_id="guerreiro", hp=500)
        authorize_skill(self.ws, self.xama, "toque_ancestral")
        vcs = self.ws.world.get_component(victim, CombatStats)
        for _ in range(6):
            hp0 = vcs.current_hp
            self._cast("toque_ancestral", victim, 40)
            if vcs.current_hp < hp0:
                break
            self.ws._skill_last_used.clear()
            self.char.espirito = 100
        a = self.ws.world.get_component(ally, StatusEffects).get("corredor_ancestral")
        x = self.ws.world.get_component(self.xama, StatusEffects).get("corredor_ancestral")
        self.assertIsNotNone(a)
        self.assertGreater(a.magnitude, x.magnitude)
        self.assertIsNone(self.ws.world.get_component(victim, StatusEffects).get("corredor_ancestral")
                          if self.ws.world.get_component(victim, StatusEffects) else None)


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
        self.assertAlmostEqual(self.char.espirito, 60, delta=0.01)   # 40; travado fora de combate

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
        self.assertAlmostEqual(self.char.espirito, 70, delta=0.01)   # 40 - 10

    def test_espirito_insuficiente_recusa(self):
        self.char.espirito = 10
        self.acs.current_hp = 100
        self._cast("sopro_da_coruja", self.ally, 40)
        self.assertLessEqual(self.acs.current_hp, 101)
        # não gastou (pode ter subido se algum mob da região puxou combate)
        self.assertGreaterEqual(self.char.espirito, 10)


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
        self.assertAlmostEqual(self.char.espirito, 80, delta=0.6)   # entra em combate: regen 5/s

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


# ── Redesenho 25/10/2026: armas, ataque básico, regra de talento ─────────────

class TestArmasDoXama(unittest.TestCase):
    def _item(self, item_type, subtype="", two_handed=False):
        class _I: pass
        i = _I(); i.item_type = item_type; i.subtype = subtype; i.two_handed = two_handed
        return i

    def test_permitidas(self):
        from engine.stats_system import is_weapon_allowed_for_class as ok
        for st in ("Staff", "Scepter", "Mace", "Club", "Hammer", "Axe"):
            self.assertTrue(ok(self._item("weapon", st), "xama"), st)
        self.assertTrue(ok(self._item("shield"), "xama"))

    def test_proibidas(self):
        from engine.stats_system import is_weapon_allowed_for_class as ok
        for st in ("Wand", "Sword", "Dagger", "Bow"):
            self.assertFalse(ok(self._item("weapon", st), "xama"), st)
        self.assertFalse(ok(self._item("quiver"), "xama"))


class TestEspiritoNoAtaqueBasico(_XamaDuelBase):
    def setUp(self):
        super().setUp()
        self.char.espirito = 0
        self.v = spawn_player(self.ws, "x_vab", 131, 374, class_id="guerreiro", hp=5000)
        self.assertIsNone(self.ws.request_duel(self.xama, self.v))
        self.ws.respond_duel_invite(self.v, accept=True)

    def test_ganha_25_so_quando_acerta(self):
        from engine.components import CombatState
        cst = self.ws.world.get_component(self.xama, CombatState)
        tcs = self.ws.world.get_component(self.v, CombatStats)
        tm  = self.ws.world.get_component(self.xama, TileMovement)
        ttm = self.ws.world.get_component(self.v, TileMovement)
        cst.target_entity_id = self.v
        cst.is_pursuing = True
        res = self.ws._resolve_auto_attack(self.xama, self.v, "x_xama", 0.05,
                                           self.xcs, tm, tcs, ttm)
        self.assertIsNotNone(res, "ataque básico não disparou")
        hit = res["outcome"] not in ("miss", "dodge", "parry")
        self.assertAlmostEqual(self.char.espirito, 25 if hit else 0, delta=0.01)


class TestFlechaEspiritual(_XamaDuelBase):
    """Bloco 2: ataque básico à distância do Xamã com cajado."""
    def setUp(self):
        super().setUp()
        from engine.components import Equipment
        from content.item_table import ITEMS
        self.char.espirito = 0
        self.eq = self.ws.world.get_component(self.xama, Equipment)
        self.eq.slots["mainhand"] = ITEMS["mystic_staff"]()
        self.eq.slots["offhand"] = None
        # 4 tiles (com linha de visão livre): fora do corpo a corpo, dentro
        # do alcance padrão (6)
        self.v = spawn_player(self.ws, "x_vfe", 130, 378, class_id="guerreiro", hp=5000)
        self.assertIsNone(self.ws.request_duel(self.xama, self.v))
        self.ws.respond_duel_invite(self.v, accept=True)

    def _atirar(self):
        cst = self.ws.world.get_component(self.xama, CombatState)
        cst.target_entity_id = self.v
        cst.is_pursuing = True
        return self.ws._resolve_auto_attack(
            self.xama, self.v, "x_xama", 1.0, self.xcs,   # passa o atraso do 1º tiro
            self.ws.world.get_component(self.xama, TileMovement),
            self.ws.world.get_component(self.v, CombatStats),
            self.ws.world.get_component(self.v, TileMovement))

    def test_tipo_por_arma(self):
        from engine.world_systems import ranged_auto_attack
        from content.item_table import ITEMS
        self.assertEqual(ranged_auto_attack(self.ws.world, self.xama)[0], "spirit")
        self.eq.slots["mainhand"] = ITEMS["lich_scepter"]()
        self.assertEqual(ranged_auto_attack(self.ws.world, self.xama)[0], "spirit")
        self.eq.slots["mainhand"] = None
        self.assertEqual(ranged_auto_attack(self.ws.world, self.xama), ("", 1))

    def test_arqueiro_com_arco_continua_flecha(self):
        from engine.components import Equipment
        from engine.world_systems import ranged_auto_attack
        from content.item_table import ITEMS
        arq = spawn_player(self.ws, "x_arq", 130, 380, class_id="arqueiro", hp=300)
        bow = next(k for k, f in ITEMS.items() if getattr(f(), "subtype", "") == "Bow")
        self.ws.world.get_component(arq, Equipment).slots["mainhand"] = ITEMS[bow]()
        self.assertEqual(ranged_auto_attack(self.ws.world, arq)[0], "arrow")

    def test_dispara_de_longe_sem_aljava_com_dano_de_sp(self):
        from engine.components import SkillLevels
        skl = self.ws.world.get_component(self.xama, SkillLevels)
        xp_antes = skl.xp.get("magic", 0) if skl and hasattr(skl, "xp") else None
        vcs = self.ws.world.get_component(self.v, CombatStats)
        hp0 = vcs.current_hp
        res = self._atirar()
        self.assertIsNotNone(res, "flecha espiritual não disparou")
        self.assertEqual(res["ranged_kind"], "spirit")
        self.assertTrue(res["is_ranged"])
        self.assertIn(res["outcome"], ("hit", "crit", "immune"))
        if res["outcome"] != "immune":
            self.assertGreater(res["damage"], 0)
            self.assertEqual(hp0 - vcs.current_hp, res["damage"])
            self.assertAlmostEqual(self.char.espirito, 25, delta=0.01)
        if xp_antes is not None:
            self.assertGreater(skl.xp.get("magic", 0), xp_antes)

    def test_sem_cajado_nao_alcanca_de_longe(self):
        self.eq.slots["mainhand"] = None
        self.assertIsNone(self._atirar())

    def test_mago_nao_e_mais_conjurador_de_auto_ataque_do_xama(self):
        from engine.stats_system import CASTER_CLASSES
        self.assertNotIn("xama", CASTER_CLASSES)


class TestFlechaEspiritualCliente(unittest.TestCase):
    """Projétil visual: linha azul enquanto não existe o sheet; luz azul."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_mode((1, 1))

    def _sistema(self):
        from engine.world import World
        from ui.spell_system import PlayerProjectileSystem
        surf = pygame.Surface((200, 200))
        return PlayerProjectileSystem(World(), surf, 0), surf

    def test_render_linha_e_luz(self):
        from ui.spell_system import SPIRIT_BOLT_ID, PlayerProjectileSystem as P
        pps, surf = self._sistema()
        pps._spirit_bolt_frames = []          # sem sheet → linha
        e = pps.spawn(100.0, 100.0, 1, 2, spell_id=SPIRIT_BOLT_ID,
                      target_last_x=180.0, target_last_y=100.0)
        e.spawn_x = 0.0                       # já "viajou": opacidade plena
        surf.fill((0, 0, 0))
        pps.render(0, 0)
        r, g, b, _ = surf.get_at((95, 100))
        self.assertGreater(b, r)
        self.assertGreater(b, 0)
        self.assertLess(b, P.SPIRIT_BOLT_COLOR[2])   # 50% de transparência
        luzes = pps.get_fireball_light_sources(0, 0)
        self.assertEqual(len(luzes), 1)
        self.assertEqual(luzes[0]["glow_color"], P.SPIRIT_BOLT_LIGHT_COLOR)


class TestTalentoExigeHabilidade(_XamaDuelBase):
    def test_sussurros_exige_toque_aprendido(self):
        from engine.components import PlayerSkills, TalentTree
        from engine.world_systems import talent_skill_requirement_met
        from content.talent_data import TALENTS
        ps = self.ws.world.get_component(self.xama, PlayerSkills)
        t = TALENTS["raiz_sussurros_constantes"]
        ps.learned_skill_ids.discard("toque_ancestral")
        self.assertFalse(talent_skill_requirement_met(self.ws.world, self.xama, t, {}))
        ps.learned_skill_ids.add("toque_ancestral")
        self.assertTrue(talent_skill_requirement_met(self.ws.world, self.xama, t, {}))

    def test_servidor_descarta_ponto_sem_habilidade(self):
        from engine.components import PlayerSkills, TalentTree
        ps = self.ws.world.get_component(self.xama, PlayerSkills)
        tt = self.ws.world.get_component(self.xama, TalentTree)
        tt.available_points = 3
        ps.learned_skill_ids.discard("toque_ancestral")
        out = self.ws.validate_talent_allocation(
            "x_xama", {"allocated": {"raiz_sussurros_constantes": 2, "raiz_alma_generosa": 1}})
        self.assertNotIn("raiz_sussurros_constantes", out["allocated"])
        self.assertEqual(out["allocated"].get("raiz_alma_generosa"), 1)
        self.assertEqual(out["available_points"], 2)

    def test_habilidade_vinda_de_talento_do_mesmo_payload_vale(self):
        from engine.world_systems import talent_skill_requirement_met
        fake = {"requires_skill": "sopro_da_coruja"}
        self.assertFalse(talent_skill_requirement_met(self.ws.world, self.xama, fake, {}))
        self.assertTrue(talent_skill_requirement_met(self.ws.world, self.xama, fake,
                                                     {"raiz_sopro_da_coruja": 1}))


if __name__ == "__main__":
    unittest.main()
