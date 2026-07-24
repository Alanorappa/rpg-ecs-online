"""
tests/test_quest_reward_ui.py — QuestDialogSystem: seleção de recompensa
de item no diálogo de entrega (23/07/2026, pedido do usuário) — mesmo
modal existente, ícones de item fixo + pool de escolha clicável.

QuestDialogSystem lê pygame.mouse.get_pos() diretamente no handler de
clique (não event.pos) — os testes monkeypatcham isso pra simular o
cursor sobre um rect específico, restaurando no fim.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import CharacterStats, QuestLog, PlayerControlled
from ui.quest_system import QuestSystem, QuestDialogSystem
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef


class _FakeNet:
    def __init__(self):
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


def _make_dialog(qid: str, reward: QuestReward):
    QUESTS[qid] = QuestDef(
        title="Quest de Teste", description="d",
        objectives=(ObjectiveDef(type="kill", target="*", count=1),),
        reward=reward,
    )
    world = World()
    player = world.create_entity()
    world.add_component(player, CharacterStats(name="Testchar", class_id="guerreiro"))
    world.add_component(player, QuestLog())
    world.add_component(player, PlayerControlled())
    screen = pygame.Surface((800, 600))

    qs = QuestSystem(world, player)
    qs.set_ui_scale(1.0)
    dlg = QuestDialogSystem(world, player, screen, qs)
    dlg.set_ui_scale(1.0)
    dlg._qs._net = _FakeNet()

    dlg._dialog_npc_id       = 999
    dlg._dialog_selected_qid = qid
    dlg._dialog_state        = "turnin"
    dlg._render_turnin(20, 20)   # popula _reward_choice_rects/_complete_rect
    return dlg


def _click_at(dlg, pos) -> None:
    orig = pygame.mouse.get_pos
    pygame.mouse.get_pos = lambda: pos
    try:
        dlg.handle_events([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)])
    finally:
        pygame.mouse.get_pos = orig


class TestQuestRewardChoiceUI(unittest.TestCase):

    def tearDown(self):
        for qid in list(QUESTS.keys()):
            if qid.startswith("qrui_"):
                del QUESTS[qid]

    def test_render_popula_um_rect_por_item_de_escolha(self):
        dlg = _make_dialog("qrui_a", QuestReward(choice=("training_sword", "hp_potion")))
        self.assertEqual(len(dlg._reward_choice_rects), 2)
        keys = {k for _, k in dlg._reward_choice_rects}
        self.assertEqual(keys, {"training_sword", "hp_potion"})

    def test_clicar_no_icone_seleciona_o_item(self):
        dlg = _make_dialog("qrui_b", QuestReward(choice=("training_sword", "hp_potion")))
        rect, item_key = dlg._reward_choice_rects[0]
        _click_at(dlg, rect.center)
        self.assertEqual(dlg._turnin_chosen_item, item_key)

    def test_concluir_sem_selecao_nao_manda_quest_turn_in(self):
        dlg = _make_dialog("qrui_c", QuestReward(choice=("training_sword", "hp_potion")))
        self.assertIsNone(dlg._turnin_chosen_item)
        _click_at(dlg, dlg._complete_rect.center)
        self.assertEqual(dlg._qs._net.sent, [])
        # Modal continua aberto — clique em botão "desabilitado" não fecha nada
        self.assertEqual(dlg._dialog_state, "turnin")

    def test_concluir_com_selecao_manda_quest_turn_in_com_chosen_item(self):
        dlg = _make_dialog("qrui_d", QuestReward(choice=("training_sword", "hp_potion")))
        rect, item_key = dlg._reward_choice_rects[1]
        _click_at(dlg, rect.center)
        _click_at(dlg, dlg._complete_rect.center)
        self.assertEqual(len(dlg._qs._net.sent), 1)
        msg_type, payload = dlg._qs._net.sent[0]
        self.assertEqual(payload["quest_id"], "qrui_d")
        self.assertEqual(payload["chosen_item"], item_key)

    def test_sem_pool_de_escolha_concluir_funciona_direto(self):
        """Quest só com itens fixos (sem choice) — Concluir manda na hora,
        sem exigir seleção nenhuma."""
        dlg = _make_dialog("qrui_e", QuestReward(items=("hp_potion",)))
        self.assertEqual(dlg._reward_choice_rects, [])
        _click_at(dlg, dlg._complete_rect.center)
        self.assertEqual(len(dlg._qs._net.sent), 1)
        self.assertEqual(dlg._qs._net.sent[0][1]["chosen_item"], "")

    def test_clicar_em_outro_item_troca_a_selecao(self):
        dlg = _make_dialog("qrui_f", QuestReward(choice=("training_sword", "hp_potion", "mana_potion")))
        r0, k0 = dlg._reward_choice_rects[0]
        r1, k1 = dlg._reward_choice_rects[1]
        _click_at(dlg, r0.center)
        self.assertEqual(dlg._turnin_chosen_item, k0)
        _click_at(dlg, r1.center)
        self.assertEqual(dlg._turnin_chosen_item, k1)


if __name__ == "__main__":
    unittest.main()
