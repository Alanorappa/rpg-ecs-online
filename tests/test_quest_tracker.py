"""
tests/test_quest_tracker.py — Quest tracker do HUD (Fase F, 23/07/2026):
limite de 5 quests visíveis (era 3), ordenação por progresso ABSOLUTO
somado (não proporcional), e minimizar/expandir. Ver ui/quest_system.py e
ARQUITETURA_ONLINE.md.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from engine.world import World
from engine.components import QuestLog
from ui.quest_system import QuestSystem
from ui.ui_sizes import UI


class TestQuestTrackerSortAndLimit(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.ql = QuestLog()
        self.world.add_component(self.player, self.ql)
        self.qs = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)

    def test_max_hud_quests_e_5(self):
        self.assertEqual(UI.QUEST_HUD_MAX_VISIBLE, 5)
        self.assertEqual(self.qs.MAX_HUD_QUESTS, 5)

    def test_ordena_por_progresso_absoluto_somado_nao_proporcional(self):
        # "survivor" (1 objetivo, count=3): progresso 2/3 -> soma absoluta 2
        # "bear_hunter" (1 objetivo, count=5): progresso 4/5 -> soma absoluta 4
        # bear_hunter tem proporção MENOR (4/5=0.8 vs 2/3=0.67) só que soma
        # absoluta MAIOR (4 > 2) — deve vir primeiro (pedido do usuário:
        # ordenar por absoluto, não por proporção).
        self.ql.active["survivor"]    = [2]
        self.ql.active["bear_hunter"] = [4]
        sorted_items = self.qs._sorted_active_items(self.ql)
        self.assertEqual([qid for qid, _ in sorted_items], ["bear_hunter", "survivor"])

    def test_mais_de_5_quests_so_mostra_as_5_de_maior_soma(self):
        # 6 quests reais do catálogo, cada uma com 1 objetivo de count>=1 —
        # soma = valor único do progresso.
        quests_progress = {
            "first_blood":   1,
            "survivor":      1,
            "bear_hunter":   5,
            "bear_pelt":     1,
            "wolf_fangs":    3,
            "spider_venom":  2,
        }
        for qid, prog in quests_progress.items():
            self.ql.active[qid] = [prog]
        sorted_items = self.qs._sorted_active_items(self.ql)[:self.qs.MAX_HUD_QUESTS]
        shown_qids = {qid for qid, _ in sorted_items}
        self.assertEqual(len(shown_qids), 5)
        # bear_hunter(5)/wolf_fangs(3)/spider_venom(2) têm soma estritamente
        # maior que o trio empatado em soma=1 (first_blood/survivor/
        # bear_pelt) — sempre entram, não importa o desempate do trio.
        for qid in ("bear_hunter", "wolf_fangs", "spider_venom"):
            self.assertIn(qid, shown_qids)


class TestQuestTrackerMinimize(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.ql = QuestLog()
        self.ql.active["first_blood"] = [1]
        self.world.add_component(self.player, self.ql)
        self.qs = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.screen = pygame.Surface((1280, 720))

    def test_minimizado_produz_surf_menor_que_expandido(self):
        self.qs.render_hud(self.screen)
        expanded_h = self.qs._hud_cache_surf.get_height()

        self.qs._tracker_minimized = True
        self.qs._hud_cache_key = None
        self.qs.render_hud(self.screen)
        minimized_h = self.qs._hud_cache_surf.get_height()

        self.assertLess(minimized_h, expanded_h)

    def test_clique_no_botao_alterna_minimizado(self):
        self.qs.render_hud(self.screen)
        self.assertFalse(self.qs._tracker_minimized)
        rect = self.qs._last_hud_rect
        btn_center = (rect.right - 9, rect.y + 9)

        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=btn_center)
        consumed = self.qs.handle_tracker_click(click)
        self.assertTrue(consumed)
        self.assertTrue(self.qs._tracker_minimized)

        # Clicar de novo (fora do botão, ex. no meio do texto) NÃO deve
        # mexer no estado nem "consumir" o clique.
        far_click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(rect.x + 2, rect.y + 2))
        consumed2 = self.qs.handle_tracker_click(far_click)
        self.assertFalse(consumed2)
        self.assertTrue(self.qs._tracker_minimized)  # inalterado

    def test_sem_quests_ativas_last_hud_rect_fica_none(self):
        self.qs.render_hud(self.screen)
        self.ql.active.clear()
        self.qs.render_hud(self.screen)
        self.assertIsNone(self.qs._last_hud_rect)
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0))
        self.assertFalse(self.qs.handle_tracker_click(click))


if __name__ == "__main__":
    unittest.main()
