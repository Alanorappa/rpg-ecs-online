"""
tests/test_quest_split_npc.py — quest dada por um NPC e entregue em OUTRO
(24/07/2026, pedido do usuário: "bem_vindo_guerreiro" dada por Caterina
Alisarf, entregue em Avido Faseo).

Bug real: QuestGiver.turn_in_ids vazio cai no fallback "= quest_ids"
(engine/components.py) — pensado pra quests simples de 1 NPC só. Sem
filtro, o NPC que só DÁ a quest (turn_in_ids=(), não listado em nenhum
turn_in_ids) também aparecia como aceitando a entrega, porque seu próprio
quest_ids virava o fallback. Fix: QuestDialogSystem._turn_in_ids_for()
exclui do fallback qualquer qid já reivindicado por OUTRO NPC em
turn_in_ids não-vazio.
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


QID = "qsplit_bem_vindo"


class TestQuestSplitNpc(unittest.TestCase):

    def setUp(self):
        QUESTS[QID] = QuestDef(
            title="Quest de Teste",
            description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=10),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Testchar", class_id="guerreiro"))
        self.world.add_component(self.player, QuestLog())
        self.world.add_component(self.player, PlayerControlled())

        self.giver_npc = self.world.create_entity()
        self.world.add_component(self.giver_npc, NPC(name="Caterina"))
        self.world.add_component(self.giver_npc, QuestGiver(quest_ids=(QID,), turn_in_ids=()))

        self.turnin_npc = self.world.create_entity()
        self.world.add_component(self.turnin_npc, NPC(name="Avido"))
        self.world.add_component(self.turnin_npc, QuestGiver(quest_ids=(), turn_in_ids=(QID,)))

        screen = pygame.Surface((800, 600))
        self.qs = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(QID, None)

    def _ql(self):
        return self.world.get_component(self.player, QuestLog)

    def test_quest_ativa_incompleta_so_aparece_em_progresso_no_npc_certo(self):
        ql = self._ql()
        ql.active[QID] = [0]
        self.assertEqual(self.dlg._get_inprogress_quests(self.giver_npc), [])
        self.assertEqual(self.dlg._get_inprogress_quests(self.turnin_npc), [QID])

    def test_quest_completavel_so_aparece_no_npc_de_entrega(self):
        ql = self._ql()
        ql.active[QID] = [1]   # objetivo (count=1) cumprido
        self.assertEqual(self.dlg._get_completable_quests(self.giver_npc), [])
        self.assertEqual(self.dlg._get_completable_quests(self.turnin_npc), [QID])

    def test_marker_do_npc_que_so_da_fica_none_apos_aceitar(self):
        ql = self._ql()
        ql.active[QID] = [1]
        self.assertIsNone(self.dlg.marker_for(self.giver_npc))
        self.assertIsNotNone(self.dlg.marker_for(self.turnin_npc))

    def test_fallback_continua_funcionando_pra_npc_unico_sem_split(self):
        """Regressão: quest simples (1 NPC só dá E recebe, turn_in_ids
        vazio, ninguém mais reivindica) continua usando o fallback."""
        solo_qid = "qsplit_solo"
        QUESTS[solo_qid] = QuestDef(
            title="Solo", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=5),
        )
        try:
            solo_npc = self.world.create_entity()
            self.world.add_component(solo_npc, NPC(name="Solo"))
            self.world.add_component(solo_npc, QuestGiver(quest_ids=(solo_qid,), turn_in_ids=()))
            ql = self._ql()
            ql.active[solo_qid] = [1]
            self.assertEqual(self.dlg._get_completable_quests(solo_npc), [solo_qid])
        finally:
            QUESTS.pop(solo_qid, None)


if __name__ == "__main__":
    unittest.main()
