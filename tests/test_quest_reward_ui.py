"""
tests/test_quest_reward_ui.py — QuestDialogSystem: seleção de recompensa
de item no diálogo de entrega (23/07/2026, pedido do usuário) — mesmo
modal existente, ícones de item fixo + pool de escolha clicável.

Visual do atlas de UI (20/10/2026): botões/slots agem no SOLTAR do mouse
dentro de render() — tests.helpers.click_quest_dialog faz o clique
completo sobre `dlg._hit_rects[<id>]`.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import CharacterStats, QuestLog, PlayerControlled, QuestGiver, NPC
from tests.helpers import click_quest_dialog
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

    npc = world.create_entity()
    world.add_component(npc, NPC(name="Npc Teste"))
    world.add_component(npc, QuestGiver(turn_in_ids=(qid,)))
    dlg._dialog_npc_id       = npc
    dlg._dialog_selected_qid = qid
    dlg._dialog_state        = "turnin"
    dlg.render()   # popula _hit_rects
    return dlg


def _choice_ids(dlg) -> list:
    return [k for k in dlg._hit_rects if k.startswith("q:slot:choice:")]



class TestQuestRewardChoiceUI(unittest.TestCase):

    def tearDown(self):
        for qid in list(QUESTS.keys()):
            if qid.startswith("qrui_"):
                del QUESTS[qid]

    def test_render_popula_um_slot_por_item_de_escolha(self):
        dlg = _make_dialog("qrui_a", QuestReward(choice=("training_sword", "hp_potion")))
        self.assertEqual(set(_choice_ids(dlg)),
                         {"q:slot:choice:training_sword", "q:slot:choice:hp_potion"})

    def test_clicar_no_icone_seleciona_o_item(self):
        dlg = _make_dialog("qrui_b", QuestReward(choice=("training_sword", "hp_potion")))
        click_quest_dialog(dlg, "q:slot:choice:training_sword")
        self.assertEqual(dlg._turnin_chosen_item, "training_sword")

    def test_concluir_sem_selecao_nao_manda_quest_turn_in(self):
        dlg = _make_dialog("qrui_c", QuestReward(choice=("training_sword", "hp_potion")))
        self.assertIsNone(dlg._turnin_chosen_item)
        click_quest_dialog(dlg, "q:complete")
        self.assertEqual(dlg._qs._net.sent, [])
        # Modal continua aberto — clique em botão "desabilitado" não fecha nada
        self.assertEqual(dlg._dialog_state, "turnin")

    def test_concluir_com_selecao_manda_quest_turn_in_com_chosen_item(self):
        dlg = _make_dialog("qrui_d", QuestReward(choice=("training_sword", "hp_potion")))
        click_quest_dialog(dlg, "q:slot:choice:hp_potion")
        click_quest_dialog(dlg, "q:complete")
        self.assertEqual(len(dlg._qs._net.sent), 1)
        msg_type, payload = dlg._qs._net.sent[0]
        self.assertEqual(payload["quest_id"], "qrui_d")
        self.assertEqual(payload["chosen_item"], "hp_potion")

    def test_sem_pool_de_escolha_concluir_funciona_direto(self):
        """Quest só com itens fixos (sem choice) — Concluir manda na hora,
        sem exigir seleção nenhuma."""
        dlg = _make_dialog("qrui_e", QuestReward(items=("hp_potion",)))
        self.assertEqual(_choice_ids(dlg), [])
        self.assertIn("q:slot:item:hp_potion", dlg._hit_rects)
        click_quest_dialog(dlg, "q:complete")
        self.assertEqual(len(dlg._qs._net.sent), 1)
        self.assertEqual(dlg._qs._net.sent[0][1]["chosen_item"], "")

    def test_clicar_em_outro_item_troca_a_selecao(self):
        dlg = _make_dialog("qrui_f", QuestReward(choice=("training_sword", "hp_potion", "mana_potion")))
        click_quest_dialog(dlg, "q:slot:choice:training_sword")
        self.assertEqual(dlg._turnin_chosen_item, "training_sword")
        click_quest_dialog(dlg, "q:slot:choice:hp_potion")
        self.assertEqual(dlg._turnin_chosen_item, "hp_potion")

    def test_icones_ficam_logo_apos_texto_curto_nao_colados_no_botao(self):
        """Bug real reportado pelo usuário (print, 24/07/2026): pra uma
        quest com texto curto, a faixa de ícones ficava presa perto do
        botão Concluir — deve ficar logo abaixo do texto de verdade."""
        dlg = _make_dialog("qrui_g", QuestReward(xp=15, choice=("training_sword",)))
        rect = dlg._hit_rects["q:slot:choice:training_sword"]
        self.assertLess(rect.y, dlg._hit_rects["q:complete"].y - 100)

    def test_reward_skill_info_resolve_skill_valida_do_catalogo(self):
        dlg = _make_dialog("qrui_h", QuestReward(xp=5, skill="golpe_poderoso"))
        info = dlg._reward_skill_info("golpe_poderoso")
        self.assertEqual(info, ("golpe_poderoso", "Golpe Poderoso"))

    def test_reward_skill_info_ignora_chave_inexistente_no_catalogo(self):
        dlg = _make_dialog("qrui_i", QuestReward(xp=5, skill="isso_nao_existe"))
        self.assertIsNone(dlg._reward_skill_info("isso_nao_existe"))
        self.assertIsNone(dlg._reward_skill_info(""))

    def test_render_turnin_com_skill_nao_quebra_e_gera_tooltip_no_hover(self):
        """A skill de recompensa aparece num slot junto dos itens fixos, e
        o hover mostra tooltip (pedido do usuário, 25/07/2026)."""
        dlg = _make_dialog("qrui_j", QuestReward(xp=5, items=("hp_potion",), skill="golpe_poderoso"))
        pos = dlg._hit_rects["q:slot:skill:golpe_poderoso"].center
        orig = pygame.mouse.get_pos
        pygame.mouse.get_pos = lambda: pos
        try:
            dlg.render()
        finally:
            pygame.mouse.get_pos = orig
        self.assertIsNotNone(dlg.pending_tooltip)
        self.assertEqual(dlg.pending_tooltip[2], "Golpe Poderoso")

    def test_x_da_barra_de_titulo_fecha(self):
        dlg = _make_dialog("qrui_l", QuestReward(xp=5))
        click_quest_dialog(dlg, "q:close")
        self.assertFalse(dlg.is_open)
        self.assertEqual(dlg._qs._net.sent, [])

    def test_soltar_fora_do_botao_nao_dispara(self):
        """Botão do atlas age no SOLTAR, e só se soltar em cima dele."""
        dlg = _make_dialog("qrui_m", QuestReward(xp=5))
        pos = dlg._hit_rects["q:complete"].center
        orig_pos, orig_pr = pygame.mouse.get_pos, pygame.mouse.get_pressed
        try:
            pygame.mouse.get_pos = lambda: pos
            pygame.mouse.get_pressed = lambda num_buttons=3: (True, False, False)
            dlg.handle_events([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)])
            dlg.render()
            pygame.mouse.get_pos = lambda: (5, 5)
            pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
            dlg.handle_events([pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(5, 5))])
            dlg.render()
        finally:
            pygame.mouse.get_pos, pygame.mouse.get_pressed = orig_pos, orig_pr
        self.assertEqual(dlg._qs._net.sent, [])
        self.assertTrue(dlg.is_open)

    def test_texto_longo_rola_com_a_roda_e_fica_dentro_da_tela(self):
        dlg = _make_dialog("qrui_n", QuestReward(xp=5))
        QUESTS["qrui_n"] = QuestDef(title="Longa", description="palavra " * 900,
                                    objectives=(ObjectiveDef(type="kill", target="*", count=1),),
                                    reward=QuestReward(xp=5))
        dlg._dialog_state = "detail"
        dlg.render()
        sw, sh = dlg.hud_surf.get_size()
        for r in dlg._hit_rects.values():
            self.assertTrue(0 <= r.top and r.bottom <= sh, r)
        pos = dlg._hit_rects["q:accept"].center
        orig = pygame.mouse.get_pos
        pygame.mouse.get_pos = lambda: (pos[0], pos[1] - 100)
        try:
            dlg.handle_events([pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3)])
            dlg.render()
        finally:
            pygame.mouse.get_pos = orig
        self.assertGreater(dlg._scroll.offset, 0)

    def test_sem_hover_nao_seta_tooltip(self):
        dlg = _make_dialog("qrui_k", QuestReward(xp=5, skill="golpe_poderoso"))
        orig = pygame.mouse.get_pos
        pygame.mouse.get_pos = lambda: (9999, 9999)
        try:
            dlg.render()
        finally:
            pygame.mouse.get_pos = orig
        self.assertIsNone(dlg.pending_tooltip)


if __name__ == "__main__":
    unittest.main()
