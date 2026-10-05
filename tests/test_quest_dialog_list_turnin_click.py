"""
tests/test_quest_dialog_list_turnin_click.py — bug real relatado pelo
usuário (05/10/2026): pegou "bem_vindo_guerreiro" com Caterina Alisarf
(objetivo único: `talk_to_npc` com Avido Faseo) e foi entregar com Avido
Faseo — em vez de abrir o diálogo de ENTREGA, abria o diálogo de ACEITAR
de novo. Avido Faseo também oferece outra quest própria
("merchant_greeting") — se o player já tivesse essa outra quest ativa
antes, o bug desaparecia.

Causa raiz: `QuestDialogSystem._open_dialog` monta `total = comp + avail`
— com as 2 quests disponíveis (bem_vindo_guerreiro completável +
merchant_greeting disponível), `len(total) > 1`, então o diálogo abre no
estado "list" (lista pra escolher), SEM pré-selecionar nada. O handler de
clique NA LISTA (`handle_events`, bloco `_dialog_state == "list"`) decidia
"turnin" vs "detail" chamando `self._qs.can_turn_in(qid)` — versão CRUA,
que só olha o progresso bruto em `QuestLog.active`. Como o objetivo
`talk_to_npc` nunca é mutado de verdade no cliente ONLINE (
`_process_talk_to_npc` é no-op de propósito — só o SERVIDOR pode
confirmar isso, ver `can_turn_in_after_talk`), `can_turn_in` cru sempre
devolvia False pra essa quest especificamente, e o clique caía no
fallback "detail" (aceitar) em vez de "turnin" (entregar).

Com `merchant_greeting` JÁ ativo antes, `avail` fica vazio (filtrado por
já estar em `ql.active`), `len(total) == 1`, e o diálogo pula a lista —
vai direto pro branch de item único (linha ~1199), que já usava a
checagem CERTA (`_get_completable_quests`, com `pending_npc_name`) desde
sempre. Por isso o bug só aparecia com a quest extra disponível.

Fix: o clique na lista agora recalcula com `_get_completable_quests`
(mesma função, mesmo `pending_npc_name`) em vez de `can_turn_in` cru —
mesmo critério usado pra MONTAR a lista, agora também usado pra decidir
o que fazer com o clique.
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
    CharacterStats, QuestLog, PlayerControlled, QuestGiver, NPC,
)
from ui.quest_system import QuestSystem, QuestDialogSystem
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef

QID_TALK  = "qdlc_bem_vindo"     # dada por outro NPC, só talk_to_npc aqui
QID_AVAIL = "qdlc_merchant"      # oferecida pelo PRÓPRIO NPC de entrega


class TestListClickAbreTurninParaQuestDeTalk(unittest.TestCase):
    """Cenário exato relatado: NPC de entrega tem OUTRA quest disponível
    pra oferecer — a lista aparece, e clicar na completável-por-talk
    precisa abrir "turnin", não "detail"."""

    def setUp(self):
        QUESTS[QID_TALK] = QuestDef(
            title="Bem-vindo (teste)", description="d",
            objectives=(ObjectiveDef(type="talk_to_npc", target="Avido", count=1),),
            reward=QuestReward(xp=10),
        )
        QUESTS[QID_AVAIL] = QuestDef(
            title="Saudação do Mercador (teste)", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=5),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Testchar", class_id="guerreiro"))
        ql = QuestLog()
        ql.active[QID_TALK] = [0]   # talk_to_npc ainda não "confirmado" localmente (online)
        self.world.add_component(self.player, ql)
        self.world.add_component(self.player, PlayerControlled())

        self.avido = self.world.create_entity()
        self.world.add_component(self.avido, NPC(name="Avido"))
        self.world.add_component(self.avido, QuestGiver(
            quest_ids=(QID_AVAIL,), turn_in_ids=(QID_TALK,)))

        screen = pygame.Surface((800, 600))
        self.qs  = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(QID_TALK, None)
        QUESTS.pop(QID_AVAIL, None)

    def test_dialog_abre_em_list_com_as_2_quests(self):
        """Confirma o setup do bug: com a quest extra disponível, o
        diálogo abre em "list", não direto numa das duas."""
        self.dlg._open_dialog(self.avido)
        self.assertEqual(self.dlg._dialog_state, "list")

    def _simula_clique_na_lista(self, qid: str) -> None:
        rect = pygame.Rect(0, 0, 100, 20)
        self.dlg._list_rects = {qid: rect}
        event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(10, 10))
        self.dlg.handle_events([event])

    def test_clicar_na_quest_de_talk_abre_turnin_nao_detail(self):
        self.dlg._open_dialog(self.avido)
        self.assertEqual(self.dlg._dialog_state, "list")

        self._simula_clique_na_lista(QID_TALK)

        self.assertEqual(self.dlg._dialog_state, "turnin",
            "quest completável só por talk_to_npc deveria abrir ENTREGA, "
            "não voltar pro diálogo de aceitar")
        self.assertEqual(self.dlg._dialog_selected_qid, QID_TALK)

    def test_clicar_na_quest_disponivel_continua_abrindo_detail(self):
        """Regressão: a OUTRA quest (disponível, nunca aceita) continua
        abrindo "detail" (aceitar) normalmente."""
        self.dlg._open_dialog(self.avido)
        self._simula_clique_na_lista(QID_AVAIL)
        self.assertEqual(self.dlg._dialog_state, "detail")
        self.assertEqual(self.dlg._dialog_selected_qid, QID_AVAIL)


class TestSemQuestExtraPulaDiretoProTurnin(unittest.TestCase):
    """Controle: sem a quest extra disponível (ex: já aceita antes,
    como o usuário reportou como "workaround"), total tem só 1 item —
    pula a lista, vai direto pro turnin. Confirma que o bug realmente
    dependia da lista existir."""

    def setUp(self):
        QUESTS[QID_TALK] = QuestDef(
            title="Bem-vindo (teste)", description="d",
            objectives=(ObjectiveDef(type="talk_to_npc", target="Avido", count=1),),
            reward=QuestReward(xp=10),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Testchar", class_id="guerreiro"))
        ql = QuestLog()
        ql.active[QID_TALK] = [0]
        self.world.add_component(self.player, ql)
        self.world.add_component(self.player, PlayerControlled())

        self.avido = self.world.create_entity()
        self.world.add_component(self.avido, NPC(name="Avido"))
        self.world.add_component(self.avido, QuestGiver(quest_ids=(), turn_in_ids=(QID_TALK,)))

        screen = pygame.Surface((800, 600))
        self.qs  = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(QID_TALK, None)

    def test_abre_direto_em_turnin_sem_lista(self):
        self.dlg._open_dialog(self.avido)
        self.assertEqual(self.dlg._dialog_state, "turnin")
        self.assertEqual(self.dlg._dialog_selected_qid, QID_TALK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
