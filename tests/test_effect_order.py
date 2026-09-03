"""
tests/test_effect_order.py — 01/09/2026, buff bar precisa mostrar a fila
na ordem REAL de ativação, mesmo quando os buffs vêm de mecanismos
diferentes (StatusEffects vs FireShieldEffect/IceBlockEffect/flags
simples) que não compartilham nenhum relógio entre si. Ponto único:
engine.effect_order.mark_activated()/get_seq() — um contador monotônico
guardado no componente EffectOrder da própria entidade. Pedido explícito
do usuário: nada de comparação hardcoded por fonte em ui/buff_bar.py —
um sistema central de verdade, chamado 1x no momento de ativação de cada
efeito (engine.core_systems.apply_effect() já chama sozinho pra todo
buff/debuff de StatusEffects — sem call site novo nenhum pros que já
existem).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.effect_order import mark_activated, get_seq
from engine.core_systems import apply_effect
from engine.components import FireShieldEffect, StatusEffects
from ui.buff_bar import _gather_extra_buffs


class TestEffectOrderBasics(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")

    def test_nunca_marcado_retorna_zero(self):
        self.assertEqual(get_seq(self.ws.world, self.eid, "algo_que_nunca_ligou"), 0)

    def test_marcacoes_sucessivas_sao_crescentes(self):
        mark_activated(self.ws.world, self.eid, "a")
        mark_activated(self.ws.world, self.eid, "b")
        mark_activated(self.ws.world, self.eid, "c")
        seq_a = get_seq(self.ws.world, self.eid, "a")
        seq_b = get_seq(self.ws.world, self.eid, "b")
        seq_c = get_seq(self.ws.world, self.eid, "c")
        self.assertLess(seq_a, seq_b)
        self.assertLess(seq_b, seq_c)

    def test_remarcar_avanca_a_ordem(self):
        mark_activated(self.ws.world, self.eid, "x")
        mark_activated(self.ws.world, self.eid, "y")
        seq_x_antes = get_seq(self.ws.world, self.eid, "x")
        mark_activated(self.ws.world, self.eid, "x")   # x reativa DEPOIS de y
        self.assertGreater(get_seq(self.ws.world, self.eid, "x"), seq_x_antes)
        self.assertGreater(get_seq(self.ws.world, self.eid, "x"),
                           get_seq(self.ws.world, self.eid, "y"))


class TestApplyEffectStampsOrderAutomatically(unittest.TestCase):
    """apply_effect() (engine/core_systems.py) já chama mark_activated
    sozinho — todo buff/debuff de StatusEffects (proc de item, buff de
    skill, cc_immune, etc.) ganha ordenação sem precisar de call site
    novo em cada skill/talento."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")

    def test_novo_efeito_ganha_seq_maior_que_zero(self):
        self.assertEqual(get_seq(self.ws.world, self.eid, "cc_immune"), 0)
        apply_effect(self.ws.world, self.eid, "cc_immune", 5.0)
        self.assertGreater(get_seq(self.ws.world, self.eid, "cc_immune"), 0)

    def test_refresh_do_mesmo_efeito_nao_reordena(self):
        apply_effect(self.ws.world, self.eid, "cc_immune", 5.0)
        apply_effect(self.ws.world, self.eid, "poison", 3.0, 1.0)
        seq_immune_antes = get_seq(self.ws.world, self.eid, "cc_immune")
        apply_effect(self.ws.world, self.eid, "cc_immune", 8.0)   # refresh, mesmo tipo já ativo
        self.assertEqual(get_seq(self.ws.world, self.eid, "cc_immune"), seq_immune_antes,
                         "refresh de um efeito já ativo não deveria mudar a posição na fila")


class TestBuffBarOrdersAcrossSources(unittest.TestCase):
    """O teste real que o usuário pediu: um buff de StatusEffects e um
    efeito de mecanismo PRÓPRIO (Escudo de Fogo) na fila JUNTOS, na ordem
    correta de quando cada um realmente ligou — não "todo StatusEffects
    primeiro, extra depois" (que era o bug antes desta correção)."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="mago")
        self.world = self.ws.world

    def _combined_sorted_types(self):
        from content.status_effects_data import EFFECT_DEFS
        sfx = self.world.get_component(self.eid, StatusEffects)
        buffs = [e for e in (sfx.effects.values() if sfx else [])
                if EFFECT_DEFS.get(e.effect_type) and EFFECT_DEFS[e.effect_type].is_buff]
        buffs.extend(_gather_extra_buffs(self.world, self.eid))
        buffs.sort(key=lambda e: get_seq(self.world, self.eid, e.effect_type))
        return [e.effect_type for e in buffs]

    def test_escudo_de_fogo_ativado_antes_de_um_buff_fica_a_esquerda(self):
        # Escudo de Fogo (mecanismo próprio) liga PRIMEIRO...
        self.world.add_component(self.eid, FireShieldEffect(duration=15.0))
        from engine.effect_order import mark_activated as _mark
        _mark(self.world, self.eid, "escudo_fogo")
        # ...DEPOIS um proc de item (StatusEffects) liga.
        apply_effect(self.world, self.eid, "proc_amulet_warrior", 16.0)

        types = self._combined_sorted_types()
        self.assertEqual(types, ["escudo_fogo", "proc_amulet_warrior"],
                         "escudo de fogo ligou primeiro, tem que ficar mais à esquerda")

    def test_buff_ativado_antes_do_escudo_de_fogo_fica_a_esquerda(self):
        # Inverte a ordem: proc de item liga primeiro...
        apply_effect(self.world, self.eid, "proc_amulet_warrior", 16.0)
        # ...DEPOIS Escudo de Fogo liga.
        self.world.add_component(self.eid, FireShieldEffect(duration=15.0))
        from engine.effect_order import mark_activated as _mark
        _mark(self.world, self.eid, "escudo_fogo")

        types = self._combined_sorted_types()
        self.assertEqual(types, ["proc_amulet_warrior", "escudo_fogo"],
                         "o proc ligou primeiro — antes desta correção, escudo de fogo "
                         "sempre apareceria depois dele mesmo tendo ligado antes")


if __name__ == "__main__":
    unittest.main()
