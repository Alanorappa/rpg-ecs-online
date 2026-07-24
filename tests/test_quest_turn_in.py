"""
tests/test_quest_turn_in.py — server/session.py::_handle_quest_turn_in,
recompensa de itens (23/07/2026, pedido do usuário): QuestReward.items
(sempre concedidos) e QuestReward.choice (jogador escolhe 1, validado
server-side antes de conceder). Nenhum teste cobria este handler antes
desta leva — só a lógica pura (engine/quest_logic.py) tinha cobertura.

Injeta quests de teste em content.quests_data.QUESTS (dict mutado in-
place, visível em todos os módulos que já deram `from ... import QUESTS`)
pra não depender de quests reais do jogo mudarem no futuro.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_session import make_session_manager, fake_login, get_msgs_of_type
from shared.messages import MsgType
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
from engine.components import QuestLog


def _make_ready_quest(qid: str, reward: QuestReward) -> None:
    QUESTS[qid] = QuestDef(
        title="Quest de Teste",
        description="d",
        objectives=(ObjectiveDef(type="kill", target="*", count=1),),
        reward=reward,
    )


class TestQuestTurnInRewardItems(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._added_qids: list = []

    def tearDown(self):
        for qid in self._added_qids:
            QUESTS.pop(qid, None)

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    def _add_quest(self, qid: str, reward: QuestReward) -> None:
        _make_ready_quest(qid, reward)
        self._added_qids.append(qid)

    async def _ready_session(self, qid: str, sid: str, username: str):
        session, fw = await fake_login(self.mgr, sid, username)
        ql = self.ws.world.get_component(session.entity_id, QuestLog)
        ql.active[qid] = [1]
        fw.sent.clear()
        return session, fw, ql

    async def test_item_fixo_e_concedido_via_inventory_update(self):
        self._add_quest("qti_fixed", QuestReward(xp=10, items=("training_sword",)))
        session, fw, ql = await self._ready_session("qti_fixed", "qti_a", "qtiusera")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_fixed"}, 0)

        inv_updates = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)
        self.assertEqual(len(inv_updates), 1)
        items = inv_updates[0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Espada de treinamento")
        self.assertEqual(items[0]["stack"], 1)
        self.assertNotIn("qti_fixed", ql.active)   # quest de fato completada

    async def test_multiplos_itens_fixos_com_stack(self):
        self._add_quest("qti_multi", QuestReward(items=("hp_potion", ("mana_potion", 3))))
        session, fw, _ = await self._ready_session("qti_multi", "qti_b", "qtiuserb")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_multi"}, 0)

        items = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)[0]["items"]
        by_name = {i["name"]: i for i in items}
        self.assertEqual(by_name["Poção de Vida"]["stack"], 1)
        self.assertEqual(by_name["Poção de Mana"]["stack"], 3)

    async def test_escolha_valida_concede_so_o_item_escolhido(self):
        self._add_quest("qti_choice", QuestReward(choice=("training_sword", "hp_potion")))
        session, fw, _ = await self._ready_session("qti_choice", "qti_c", "qtiuserc")

        await self.mgr._handle_quest_turn_in(session, {
            "quest_id": "qti_choice", "chosen_item": "hp_potion",
        }, 0)

        items = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)[0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Poção de Vida")

    async def test_fixos_e_escolha_juntos_concede_os_dois(self):
        self._add_quest("qti_both", QuestReward(
            items=("hp_potion",), choice=("training_sword", "mana_potion"),
        ))
        session, fw, _ = await self._ready_session("qti_both", "qti_d", "qtiuserd")

        await self.mgr._handle_quest_turn_in(session, {
            "quest_id": "qti_both", "chosen_item": "training_sword",
        }, 0)

        items = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)[0]["items"]
        names = {i["name"] for i in items}
        self.assertEqual(names, {"Poção de Vida", "Espada de treinamento"})

    async def test_escolha_invalida_recusa_a_entrega_inteira(self):
        """chosen_item fora do pool real (cliente adulterado/dessincronizado)
        — nem xp/gold/itens são concedidos, quest continua ativa."""
        self._add_quest("qti_bad_choice", QuestReward(
            xp=50, choice=("training_sword", "hp_potion"),
        ))
        session, fw, ql = await self._ready_session("qti_bad_choice", "qti_e", "qtiusere")

        await self.mgr._handle_quest_turn_in(session, {
            "quest_id": "qti_bad_choice", "chosen_item": "item_forjado",
        }, 0)

        self.assertEqual(get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE), [])
        self.assertEqual(get_msgs_of_type(fw, MsgType.QUEST_UPDATE), [])
        self.assertIn("qti_bad_choice", ql.active)   # nunca completada

    async def test_escolha_ausente_quando_obrigatoria_tambem_recusa(self):
        self._add_quest("qti_no_choice_sent", QuestReward(choice=("training_sword",)))
        session, fw, ql = await self._ready_session("qti_no_choice_sent", "qti_f", "qtiuserf")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_no_choice_sent"}, 0)

        self.assertEqual(get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE), [])
        self.assertIn("qti_no_choice_sent", ql.active)

    async def test_item_key_invalido_no_catalogo_e_ignorado_sem_derrubar_o_resto(self):
        """Typo num item_key (autor de conteúdo escreveu errado) não deveria
        derrubar a entrega inteira — só aquele item específico é ignorado
        (server loga um warning), xp/gold e os outros itens válidos seguem
        normalmente."""
        self._add_quest("qti_typo", QuestReward(
            xp=20, items=("hp_potion", "isso_nao_existe_de_verdade"),
        ))
        session, fw, ql = await self._ready_session("qti_typo", "qti_g", "qtiuserg")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_typo"}, 0)

        items = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)[0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Poção de Vida")
        self.assertNotIn("qti_typo", ql.active)   # completada normalmente

    async def test_sem_itens_de_recompensa_nao_manda_inventory_update(self):
        self._add_quest("qti_none", QuestReward(xp=5, gold=5))
        session, fw, _ = await self._ready_session("qti_none", "qti_h", "qtiuserh")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_none"}, 0)

        self.assertEqual(get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE), [])


if __name__ == "__main__":
    unittest.main()
