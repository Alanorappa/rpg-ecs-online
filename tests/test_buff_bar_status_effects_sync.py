"""
tests/test_buff_bar_status_effects_sync.py — 31/08/2026, buff bar
unificada (pedido do usuário, estilo WoW). Antes desta mudança, todo
buff temporário de verdade do jogo (proc de item, self-buff de skill,
buff de matar chefe de jungle) só mexia em CombatStats.timed_modifiers
— sem ícone, sem identidade estável, e principalmente nunca sincronizado
pro cliente (proc de item, especificamente, o cliente tentava "adivinhar"
localmente com RNG próprio, podendo divergir do servidor).

Fix: engine.core_systems.grant_visible_buff(world, eid, effect_type,
duration, mutate_fn) — chama apply_effect() (StatusEffects,
is_buff=True) E roda a mutação de stat real (mutate_fn), num único
ponto. Os 3 call sites reais (item proc, buff de chefe de jungle,
self-buff de skill) passam a rotear por aqui, reaproveitando o MESMO
canal de sincronização (AOI_UPDATE/deltas["effects"],
WorldServer._collect_player_effects) que debuff já usa.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from unittest.mock import patch

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatState, CombatStats, Equipment, Faction, InitialPosition
from content.status_effects_data import EFFECT_DEFS
from content.item_table import resolve_item_by_id
from engine.core_systems import grant_visible_buff


def _effects_for(ws, eid) -> dict:
    effects_list = ws._collect_player_effects().get(eid, [])
    return {e["type"]: e["duration"] for e in effects_list}


class TestItemProcGrantsVisibleBuff(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.cs  = self.ws.world.get_component(self.eid, CombatState)
        self.cst = self.ws.world.get_component(self.eid, CombatStats)
        self.equip = self.ws.world.get_component(self.eid, Equipment)
        item = resolve_item_by_id("amulet_warrior")
        self.assertIsNotNone(item, "amulet_warrior deveria existir no catálogo")
        self.equip.slots["neck"] = item

    @patch("random.random", return_value=0.0)   # força o proc (chance=0.25)
    def test_proc_de_item_grava_activeeffect_visivel(self, _mock_rand):
        self.cs._just_entered_combat = True
        self.ws._combat_state_sys._roll_procs(self.eid, self.cst)

        self.assertIn("proc_amulet_warrior", EFFECT_DEFS)
        self.assertTrue(EFFECT_DEFS["proc_amulet_warrior"].is_buff)

        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        self.assertIsNotNone(sfx)
        eff = sfx.get("proc_amulet_warrior")
        self.assertIsNotNone(eff, "proc deveria ter registrado um ActiveEffect visível")
        self.assertAlmostEqual(eff.duration, 16.0, delta=0.01)

        # guarda de regressão: a mutação de stat REAL continua acontecendo
        # (grant_visible_buff não pode ter quebrado o timed_modifiers real)
        self.assertTrue(any(e["label"] == "Fúria do Guerreiro"
                            for e in self.cst.timed_modifiers))

        # e aparece no canal de sincronização de rede pro cliente
        self.assertAlmostEqual(_effects_for(self.ws, self.eid).get("proc_amulet_warrior", -1),
                               16.0, delta=0.01)


class TestJungleBossBuffGrantsVisibleBuff(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.ws.world.add_component(self.eid, Faction(faction_id="jogadores"))
        self.cst = self.ws.world.get_component(self.eid, CombatStats)
        self.boss = self.ws.world.create_entity()
        self.ws.world.add_component(self.boss, InitialPosition(x=100, y=100))
        self.map_file = self.ws.get_player_map("s1")

    def test_buff_de_boss_grava_activeeffect_visivel(self):
        _etype = "jungle_boss_buff_jungle_boss_guardiao_de_pedra"
        self.assertIn(_etype, EFFECT_DEFS)
        self.assertTrue(EFFECT_DEFS[_etype].is_buff)

        # idx 0 do buff_cycle = só armor
        self.ws._grant_jungle_boss_buff(self.boss, "jogadores", self.map_file,
                                        "jungle_boss_guardiao_de_pedra")

        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        self.assertIsNotNone(sfx)
        eff = sfx.get(_etype)
        self.assertIsNotNone(eff)
        self.assertAlmostEqual(eff.duration, 90.0, delta=0.01)
        self.assertAlmostEqual(_effects_for(self.ws, self.eid).get(_etype, -1),
                               90.0, delta=0.01)

    def test_buff_combinado_aplica_os_2_atributos(self):
        """Bug real achado 31/08/2026 (não a pedido do usuário, mas
        corrigido junto por já estar mexendo nesta função): buff_cycle
        índice 2 do Guardião de Pedra combina armor+stamina numa SÓ
        concessão — antes, os 2 modifiers usavam o MESMO label, e o
        add_timed_modifier() do 2º só renovava o timer do 1º sem nunca
        aplicar o 2º atributo de verdade."""
        # mata o mesmo camp 3x (idx 0, 1, 2 -> "combinado")
        for _ in range(3):
            self.ws._grant_jungle_boss_buff(self.boss, "jogadores", self.map_file,
                                            "jungle_boss_guardiao_de_pedra")
        labels = [e["label"] for e in self.cst.timed_modifiers]
        _armor_labels   = [l for l in labels if l.endswith("_armor")]
        _stamina_labels = [l for l in labels if l.endswith("_stamina")]
        self.assertTrue(_armor_labels,   "modifier de armor deveria ter sido aplicado")
        self.assertTrue(_stamina_labels, "modifier de stamina deveria ter sido aplicado (bug: era descartado)")

    def test_matar_o_mesmo_camp_2x_renova_1_icone_so(self):
        """Limitação conhecida e aceita (avisada no plano): StatusEffects só
        guarda 1 ActiveEffect por chave — matar o mesmo camp 2x renova a
        duração de 1 ícone só, não empilha um 2º. O efeito de STATS
        (timed_modifiers) continua empilhando normalmente (não testado
        aqui, já coberto por tests/test_jungle_camps.py)."""
        _etype = "jungle_boss_buff_jungle_boss_guardiao_de_pedra"
        self.ws._grant_jungle_boss_buff(self.boss, "jogadores", self.map_file,
                                        "jungle_boss_guardiao_de_pedra")
        self.ws._grant_jungle_boss_buff(self.boss, "jogadores", self.map_file,
                                        "jungle_boss_guardiao_de_pedra")
        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        self.assertEqual(len([1 for k in sfx.effects if k == _etype]), 1)


class TestSkillSelfBuffGrantsVisibleBuff(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="arqueiro")
        self.cst = self.ws.world.get_component(self.eid, CombatStats)

    def test_cancao_inspiracao_grava_activeeffect_visivel(self):
        self.assertIn("cancao_inspiracao", EFFECT_DEFS)
        self.assertTrue(EFFECT_DEFS["cancao_inspiracao"].is_buff)
        self.ws._server_cancao_inspiracao(self.eid, -1, {})

        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        eff = sfx.get("cancao_inspiracao") if sfx else None
        self.assertIsNotNone(eff)
        self.assertAlmostEqual(eff.duration, 20.0, delta=0.01)
        self.assertTrue(any(e["label"] == "cancao_inspiracao" for e in self.cst.timed_modifiers))

    def test_so_um_gole_grava_activeeffect_visivel(self):
        self.assertIn("so_um_gole", EFFECT_DEFS)
        self.assertTrue(EFFECT_DEFS["so_um_gole"].is_buff)
        self.ws._server_so_um_gole(self.eid, -1, {})

        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        eff = sfx.get("so_um_gole") if sfx else None
        self.assertIsNotNone(eff)
        self.assertAlmostEqual(eff.duration, 10.0, delta=0.01)
        self.assertTrue(self.cst.concentration_free)


class TestLapsoElementalCritGrantsVisibleBuff(unittest.TestCase):
    """31/08/2026, fase 2 da buff bar: o bônus de crit_rating do Lapso
    Elemental (talento de mago, proc a cada 3 crits de fogo) só existia
    em timed_modifiers, sem NENHUM registro visível — achado ao investigar
    outros efeitos citados pelo usuário. A parte de queima (elemental_lapse,
    debuff no próprio caster) continua igual; só a parte de crit_rating
    passou a rotear por grant_visible_buff."""

    def setUp(self):
        from tests.helpers import run_ticks, first_mob
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="mago")
        run_ticks(self.ws, 50)
        self.cst = self.ws.world.get_component(self.eid, CombatStats)
        self.mob = first_mob(self.ws)
        self.assertIsNotNone(self.mob, "precisa de um mob real no mapa de teste")

    @patch("engine.damage_calculator.resolve_attack_outcome", return_value=("crit", 0))
    def test_lapso_elemental_crit_grava_activeeffect_visivel(self, _mock_outcome):
        self.assertIn("lapso_elemental_crit", EFFECT_DEFS)
        self.assertTrue(EFFECT_DEFS["lapso_elemental_crit"].is_buff)

        self.cst.elemental_lapse_crit_bonus = 0.15
        self.cst.fire_crit_counter = 2   # +1 crit fecha o 3º e dispara o proc
        self.ws._proj_spell_result = {"is_crit": False, "lapso_proc": None}
        self.ws._server_bola_de_fogo(self.eid, self.mob, {})

        from engine.components import StatusEffects
        sfx = self.ws.world.get_component(self.eid, StatusEffects)
        self.assertIsNotNone(sfx)
        eff = sfx.get("lapso_elemental_crit")
        self.assertIsNotNone(eff, "proc deveria ter registrado o buff visível de crit")
        self.assertAlmostEqual(eff.duration, 5.0, delta=0.01)

        self.assertTrue(any(e["label"] == "lapso_elemental" for e in self.cst.timed_modifiers),
                        "guarda de regressão: o modifier real de crit_rating continua sendo aplicado")
        self.assertAlmostEqual(_effects_for(self.ws, self.eid).get("lapso_elemental_crit", -1),
                               5.0, delta=0.01)

        # o debuff de queima (elemental_lapse, is_buff=False) continua existindo
        # separado — chave diferente, StatusEffects guarda os 2 ao mesmo tempo.
        self.assertIsNotNone(sfx.get("elemental_lapse"))
        self.assertFalse(EFFECT_DEFS["elemental_lapse"].is_buff)


class TestGrantVisibleBuffWrapper(unittest.TestCase):
    """Unitário, direto no helper — sem WorldServer."""

    def test_roda_apply_effect_e_mutate_fn(self):
        from tests.helpers import make_world_server as _mws
        ws = _mws()
        eid = spawn_player(ws, "s1", 130, 374, class_id="guerreiro")
        called = []
        grant_visible_buff(ws.world, eid, "cc_immune", 5.0, lambda: called.append(True))

        self.assertEqual(called, [True], "mutate_fn deveria ter rodado")
        from engine.components import StatusEffects
        sfx = ws.world.get_component(eid, StatusEffects)
        self.assertIsNotNone(sfx.get("cc_immune"), "apply_effect deveria ter rodado")


if __name__ == "__main__":
    unittest.main()
