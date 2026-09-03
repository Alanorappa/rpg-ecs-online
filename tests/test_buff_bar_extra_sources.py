"""
tests/test_buff_bar_extra_sources.py — 31/08/2026, buff bar fase 2.
Escudo de Fogo, Bloco de Gelo, Camuflagem e os procs de Chama Interna/
Na Mosca têm mecanismo PRÓPRIO (não StatusEffects) — a buff bar aprendeu
a LER cada um desses sem tocar em como funcionam por dentro
(ui/buff_bar.py::_gather_extra_buffs). Testes diretos no world (sem
WorldServer completo), matching engine.core_systems.grant_visible_buff's
teste unitário mais simples desta mesma sessão.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatStats, CharacterStats, FireShieldEffect, IceBlockEffect
from content.status_effects_data import EFFECT_DEFS
from ui.buff_bar import _gather_extra_buffs


class TestGatherExtraBuffs(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="mago")
        self.world = self.ws.world
        self.cs   = self.world.get_component(self.eid, CombatStats)
        self.char = self.world.get_component(self.eid, CharacterStats)

    def _types(self):
        return {e.effect_type for e in _gather_extra_buffs(self.world, self.eid)}

    def test_sem_nenhum_efeito_ativo_lista_vazia(self):
        self.assertEqual(_gather_extra_buffs(self.world, self.eid), [])

    def test_escudo_de_fogo_aparece_com_duracao_restante(self):
        self.world.add_component(self.eid, FireShieldEffect(duration=15.0, elapsed=6.0))
        result = _gather_extra_buffs(self.world, self.eid)
        eff = next(e for e in result if e.effect_type == "escudo_fogo")
        self.assertAlmostEqual(eff.duration, 9.0, delta=0.01)

    def test_bloco_de_gelo_aparece_com_duracao_restante(self):
        self.world.add_component(self.eid, IceBlockEffect(duration=5.0, elapsed=2.0))
        result = _gather_extra_buffs(self.world, self.eid)
        eff = next(e for e in result if e.effect_type == "bloco_de_gelo")
        self.assertAlmostEqual(eff.duration, 3.0, delta=0.01)

    def test_camuflagem_aparece_so_quando_timer_positivo(self):
        self.cs.camouflage_timer = 4.5
        self.assertIn("camuflagem", self._types())
        eff = next(e for e in _gather_extra_buffs(self.world, self.eid) if e.effect_type == "camuflagem")
        self.assertAlmostEqual(eff.duration, 4.5, delta=0.01)

        self.cs.camouflage_timer = 0.0
        self.assertNotIn("camuflagem", self._types())

    def test_chama_interna_aparece_sem_contador_quando_pronta(self):
        self.char.fire_instant_ready = True
        result = _gather_extra_buffs(self.world, self.eid)
        eff = next(e for e in result if e.effect_type == "chama_interna")
        self.assertEqual(eff.duration, 0.0)

        self.char.fire_instant_ready = False
        self.assertNotIn("chama_interna", self._types())

    def test_na_mosca_aparece_sem_contador_quando_pronta(self):
        self.cs.na_mosca_bonus_active = True
        result = _gather_extra_buffs(self.world, self.eid)
        eff = next(e for e in result if e.effect_type == "na_mosca")
        self.assertEqual(eff.duration, 0.0)

        self.cs.na_mosca_bonus_active = False
        self.assertNotIn("na_mosca", self._types())

    def test_todas_as_5_chaves_tem_entrada_em_effect_defs(self):
        for _etype in ("escudo_fogo", "bloco_de_gelo", "camuflagem", "chama_interna", "na_mosca"):
            self.assertIn(_etype, EFFECT_DEFS, f"{_etype} precisa de EffectDef pro ícone/cor de fallback")
            self.assertTrue(EFFECT_DEFS[_etype].is_buff)


if __name__ == "__main__":
    unittest.main()
