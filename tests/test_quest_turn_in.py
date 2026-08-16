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


class TestQuestTurnInConsumesCollectItem(unittest.IsolatedAsyncioTestCase):
    """collect_item objective — entrega da quest remove o item da bag DO
    SERVIDOR (engine/quest_logic.py::complete_quest, já existia) E avisa
    o cliente pra tirar o mesmo item da bag LOCAL (campo "removed" de
    INVENTORY_UPDATE, 28/07/2026 — bug real relatado pelo usuário: item
    entregue continuava "fantasma" na bag do cliente, já que antes NADA
    avisava essa remoção — só itens CONCEDIDOS eram notificados)."""

    def setUp(self):
        self._added_qids: list = []

    def tearDown(self):
        for qid in self._added_qids:
            QUESTS.pop(qid, None)

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def test_entrega_remove_do_inventory_do_servidor_e_manda_removed(self):
        qid = "qti_collect"
        QUESTS[qid] = QuestDef(
            title="Quest de Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="*",
                                     loot_item="presa_lobo", count=2),),
            reward=QuestReward(xp=5),
        )
        self._added_qids.append(qid)
        session, fw = await fake_login(self.mgr, "qti_i", "qtiuseri")
        ql = self.ws.world.get_component(session.entity_id, QuestLog)
        ql.active[qid] = [2]   # objetivo já completo (2/2)

        from engine.components import Inventory, Item
        inv = self.ws.world.get_component(session.entity_id, Inventory)
        # Personagem vem de fake_login (conta real no banco, ver
        # PROBLEMAS_ARQUITETURA.md) — pode carregar itens residuais de
        # execuções anteriores da suíte contra o mesmo banco. Limpa antes
        # de montar o cenário pra este teste não depender de bag vazia.
        inv.items = []
        item = Item("Presa de Lobo", "material", slot=None, max_stack=99, item_id="presa_lobo")
        item.stack = 2
        inv.items.append(item)
        fw.sent.clear()

        await self.mgr._handle_quest_turn_in(session, {"quest_id": qid}, 0)

        self.assertNotIn(item, inv.items)   # removido do Inventory do servidor
        inv_updates = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)
        self.assertEqual(len(inv_updates), 1)
        self.assertEqual(inv_updates[0]["items"], [])
        self.assertEqual(inv_updates[0]["removed"],
                         [{"item_id": "presa_lobo", "stack": 2}])

    async def test_entrega_com_reward_item_junto_manda_os_dois_campos(self):
        qid = "qti_collect_and_reward"
        QUESTS[qid] = QuestDef(
            title="Quest de Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="*",
                                     loot_item="presa_lobo", count=1),),
            reward=QuestReward(items=("hp_potion",)),
        )
        self._added_qids.append(qid)
        session, fw = await fake_login(self.mgr, "qti_j", "qtiuserj")
        ql = self.ws.world.get_component(session.entity_id, QuestLog)
        ql.active[qid] = [1]

        from engine.components import Inventory, Item
        inv = self.ws.world.get_component(session.entity_id, Inventory)
        # Ver comentário equivalente no teste acima — conta real no banco,
        # pode carregar resíduo de execuções anteriores da suíte.
        inv.items = []
        item = Item("Presa de Lobo", "material", slot=None, max_stack=99, item_id="presa_lobo")
        item.stack = 1
        inv.items.append(item)
        fw.sent.clear()

        await self.mgr._handle_quest_turn_in(session, {"quest_id": qid}, 0)

        inv_updates = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)
        self.assertEqual(len(inv_updates), 1)
        self.assertEqual(inv_updates[0]["items"][0]["name"], "Poção de Vida")
        self.assertEqual(inv_updates[0]["removed"],
                         [{"item_id": "presa_lobo", "stack": 1}])


class TestQuestTurnInRewardSkill(unittest.IsolatedAsyncioTestCase):
    """QuestReward.skill (25/07/2026, pedido do usuário) — diferente de
    item: servidor grava em PlayerSkills.learned_skill_ids NA HORA (gate
    de autorização server-side, is_skill_authorized, precisa ser real
    imediatamente)."""

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

    # skills usadas nos testes desta classe — sempre removidas do estado
    # inicial (ver _ready_session) pra garantir baseline determinístico.
    _TEST_SKILL_IDS = ("golpe_poderoso", "bola_de_fogo")

    async def _ready_session(self, qid: str, sid: str, username: str):
        session, fw = await fake_login(self.mgr, sid, username, class_id="guerreiro")
        ql = self.ws.world.get_component(session.entity_id, QuestLog)
        ql.active[qid] = [1]

        # data/game.db é um arquivo REAL, compartilhado entre execuções de
        # teste (não efêmero) — um username reusado entre rodadas pode
        # trazer skills já aprendidas/persistidas de uma corrida anterior.
        # Sem este reset, test_skill_valida_da_classe_certa_e_concedida
        # (que assume a skill AINDA não aprendida) fica dependente de
        # nunca ter rodado antes pra aquele username — limpa explicitamente
        # pra garantir baseline determinístico independente do histórico
        # do banco.
        from engine.components import PlayerSkills as _PS_reset
        ps = self.ws.world.get_component(session.entity_id, _PS_reset)
        if ps is not None:
            for _sid_reset in self._TEST_SKILL_IDS:
                ps.learned_skill_ids.discard(_sid_reset)
            for _i, _sk in enumerate(ps.skills):
                if _sk is not None and _sk.skill_id in self._TEST_SKILL_IDS:
                    ps.skills[_i] = None

        fw.sent.clear()
        return session, fw, ql

    async def test_skill_valida_da_classe_certa_e_concedida(self):
        self._add_quest("qts_ok", QuestReward(xp=10, skill="golpe_poderoso"))
        session, fw, ql = await self._ready_session("qts_ok", "qts_a", "qtsusera")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qts_ok"}, 0)

        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(session.entity_id, PlayerSkills)
        self.assertIn("golpe_poderoso", ps.learned_skill_ids)
        self.assertTrue(any(sk and sk.skill_id == "golpe_poderoso" for sk in ps.skills))

        granted = get_msgs_of_type(fw, MsgType.SKILL_GRANTED)
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["skill_id"], "golpe_poderoso")
        self.assertNotIn("qts_ok", ql.active)

    async def test_skill_de_outra_classe_e_ignorada_sem_derrubar_o_resto(self):
        """Quest de guerreiro com reward.skill de mago (classe errada) —
        ignorada com warning, resto da entrega (xp, conclusão da quest)
        segue normal."""
        self._add_quest("qts_wrong_class", QuestReward(xp=15, skill="bola_de_fogo"))
        session, fw, ql = await self._ready_session("qts_wrong_class", "qts_b", "qtsuserb")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qts_wrong_class"}, 0)

        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(session.entity_id, PlayerSkills)
        self.assertNotIn("bola_de_fogo", ps.learned_skill_ids)
        self.assertEqual(get_msgs_of_type(fw, MsgType.SKILL_GRANTED), [])
        self.assertNotIn("qts_wrong_class", ql.active)

    async def test_skill_inexistente_no_catalogo_e_ignorada(self):
        self._add_quest("qts_typo", QuestReward(xp=5, skill="isso_nao_existe"))
        session, fw, ql = await self._ready_session("qts_typo", "qts_c", "qtsuserc")

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qts_typo"}, 0)

        self.assertEqual(get_msgs_of_type(fw, MsgType.SKILL_GRANTED), [])
        self.assertNotIn("qts_typo", ql.active)

    async def test_skill_ja_aprendida_nao_reenvia_nem_duplica_slot(self):
        """Player que já tinha a skill (ex.: aprendida via treinador antes
        de entregar a quest) não deveria duplicar o slot na hotbar nem
        mandar SKILL_GRANTED de novo."""
        self._add_quest("qts_already", QuestReward(skill="golpe_poderoso"))
        session, fw, ql = await self._ready_session("qts_already", "qts_d", "qtsuserd")

        from engine.components import PlayerSkills
        from content.skill_config import SKILL_CATALOG
        ps = self.ws.world.get_component(session.entity_id, PlayerSkills)
        ps.learned_skill_ids.add("golpe_poderoso")
        idx = ps.skills.index(None)
        ps.skills[idx] = PlayerSkills._make_skill("golpe_poderoso", SKILL_CATALOG)

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qts_already"}, 0)

        self.assertEqual(get_msgs_of_type(fw, MsgType.SKILL_GRANTED), [])
        self.assertEqual(
            sum(1 for sk in ps.skills if sk and sk.skill_id == "golpe_poderoso"), 1)


class TestQuestTurnInInstanceXp(unittest.IsolatedAsyncioTestCase):
    """Gap real achado nesta sessão (02/08/2026): recompensa de XP de
    quest caía direto no `process_levelups` REAL, sem checar
    `is_in_normalized_progression` — mesmo desvio que `world_server.py::
    consume_xp()` já aplica pra XP de kill de mob/minion/torre, mas nunca
    tinha sido estendido pra recompensa de quest."""

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

    async def test_xp_de_quest_dentro_da_instancia_usa_curva_de_instancia(self):
        from server.instance_progression import enter_normalized_progression
        from engine.components import CharacterStats

        self._add_quest("qti_inst", QuestReward(xp=10_000_000))
        session, fw = await fake_login(self.mgr, "qti_inst_sid", "qtiinstuser")
        ql = self.ws.world.get_component(session.entity_id, QuestLog)
        ql.active["qti_inst"] = [1]
        enter_normalized_progression(self.ws, session.entity_id)
        char = self.ws.world.get_component(session.entity_id, CharacterStats)
        self.assertEqual(char.level, 1)  # pré-condição: acabou de entrar

        await self.mgr._handle_quest_turn_in(session, {"quest_id": "qti_inst"}, 0)

        from server.instance_progression import INSTANCE_LEVEL_CAP
        self.assertEqual(char.level, INSTANCE_LEVEL_CAP,
                         "XP de quest deveria ter usado a curva/cap de instância")


if __name__ == "__main__":
    unittest.main()
