"""
tests/test_quest_talk_completable_priority.py — bug real reportado pelo
usuário (04/09/2026): quest cujo ÚNICO objetivo pendente é "falar com
NPC X" não era detectada como completável em NENHUM dos lugares que
decidem o que mostrar ao clicar com o direito em X — nem no atalho que
pula o menu de ação (TrainerSystem._open_menu), nem na lista de opções
do menu (_compute_options), nem na lista de quests do diálogo
(QuestDialogSystem._content_list), nem no ícone acima da cabeça
(marker_for). Todos esses lugares chamavam `_get_completable_quests`
SEM `pending_npc_name` — só `QuestDialogSystem._open_dialog` (chamado
DEPOIS de já se decidir abrir o diálogo de quest) passava esse
parâmetro corretamente.

Sintoma relatado: clicar no NPC abria o menu "Quests/Treinamento" em vez
de ir direto pra entrega; ao entrar em "Quests", a quest pronta pra
entregar não aparecia na lista.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import (
    CharacterStats, QuestLog, PlayerControlled, QuestGiver, Trainer, NPC,
)
from ui.quest_system import QuestSystem, QuestDialogSystem
from ui.trainer_system import TrainerSystem
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef


QID = "qttp_fale_comigo"


class TestQuestTalkCompletablePriority(unittest.TestCase):

    def setUp(self):
        QUESTS[QID] = QuestDef(
            title="Quest de Teste",
            description="d",
            objectives=(ObjectiveDef(type="talk_to_npc", target="Instrutor", count=1),),
            reward=QuestReward(xp=10),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Testchar", class_id="guerreiro"))
        ql = QuestLog()
        ql.active[QID] = [0]   # objetivo talk_to_npc ainda não "confirmado" localmente
        self.world.add_component(self.player, ql)
        self.world.add_component(self.player, PlayerControlled())

        # NPC alvo do talk_to_npc — é TAMBÉM o treinador (mesmo NPC dá
        # treino de classe E aceita a entrega desta quest) — cenário exato
        # relatado pelo usuário.
        self.npc = self.world.create_entity()
        self.world.add_component(self.npc, NPC(name="Instrutor"))
        self.world.add_component(self.npc, QuestGiver(quest_ids=(), turn_in_ids=(QID,)))
        self.world.add_component(self.npc, Trainer(class_id="guerreiro"))

        screen = pygame.Surface((800, 600))
        self.qs  = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)
        self.trainer = TrainerSystem(self.world, self.player, screen)
        self.trainer.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(QID, None)

    def test_get_completable_quests_com_pending_npc_name_detecta_a_quest(self):
        self.assertEqual(self.dlg._get_completable_quests(self.npc), [],
                         "sem pending_npc_name, ainda não deveria contar como completável")
        self.assertEqual(self.dlg._get_completable_quests(self.npc, "Instrutor"), [QID])

    def test_marker_mostra_completavel_nao_em_progresso(self):
        marker = self.dlg.marker_for(self.npc)
        self.assertIsNotNone(marker)
        icon_name, color, symbol = marker
        self.assertEqual(icon_name, "map_quest_complete")

    def test_open_menu_vai_direto_pro_turnin_sem_mostrar_o_menu_de_acao(self):
        self.trainer._open_menu(self.npc)
        self.assertEqual(self.trainer._state, self.trainer.STATE_CLOSED,
                         "deveria ter pulado o menu de ação (STATE_MENU) direto pro diálogo de quest")
        # 15/09/2026 (item 4 da revisão de performance): TrainerSystem não
        # chama mais QuestDialogSystem._open_dialog() direto — escreve
        # NPCInteractionRequest no player, consumido por
        # QuestDialogSystem.update() no MESMO frame (aqui, explicitamente,
        # já que o teste não roda um game loop de verdade).
        self.dlg.update()
        self.assertEqual(self.dlg._dialog_state, "turnin")
        self.assertEqual(self.dlg._dialog_selected_qid, QID)

    def test_compute_options_inclui_quests_mesmo_so_com_a_pendente_de_talk(self):
        self.trainer._tr_name = "Instrutor"
        options = self.trainer._compute_options(self.npc)
        keys = {k for k, _ in options}
        self.assertIn("quest:0", keys)


if __name__ == "__main__":
    unittest.main()
