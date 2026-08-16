"""
tests/test_loot_authoritative.py — Débito A4 (11/08/2026, ver
PROBLEMAS_ARQUITETURA.md): redesign de saque, última peça client-
authoritative do item A4. Antes, `request_loot()` só tirava o item do
dict do corpse e devolvia pro cliente aplicar por conta própria na sua
cópia LOCAL do Inventory — o servidor nunca sabia se o item realmente
coube na bag, nunca empilhava de verdade, e "item" era casado por NOME
de exibição (`item_name`), que não distingue itens diferentes com o
mesmo nome.

Fix: `request_loot()` agora credita direto no Inventory AO VIVO do
servidor antes de responder (mesmo modelo já provado em `craft_item`);
`take="item"` casa por `item_id`; item que não cabe na bag continua no
corpse (decisão do usuário: isso não é comportamento de loot, é só "não
tira do corpse o que não coube" — sem lógica nova de expiração, o timer
normal do corpse já cobre); progresso de quest `collect_item` se move
pra dentro do próprio fluxo de saque (server/session.py::
_handle_loot_request), já que o cliente não manda mais INV_SYNC depois
de lootear. Guarda de integridade nova: dois item_ids diferentes com o
mesmo nome de exibição recusam o boot do servidor (decisão do usuário,
11/08/2026 — "trava para que isso não possa acontecer").
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import Inventory, Item


def _loot_dict(item_id: str, stack: int = 1) -> dict:
    from content.item_table import ITEMS
    from server.server_death_handler import _serialize_item
    d = _serialize_item(ITEMS[item_id]())
    d["stack"] = stack
    return d


def _make_corpse(ws, owner_eid: int, items: list, coins: int = 0) -> int:
    cid = ws._next_corpse_id
    ws._next_corpse_id += 1
    ws._corpses[cid] = {
        "tx": 130, "ty": 374, "owner_eid": owner_eid,
        "items": items, "coins": coins,
        "timer": 120.0, "map": ws._map_file, "quest_rolls": {},
    }
    return cid


class TestLootCreditaDiretoNoInventoryAoVivo(unittest.TestCase):
    """request_loot() agora muta o Inventory AO VIVO do jogador antes de
    responder — nunca mais um pedido pro cliente aplicar sozinho."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv = self.ws.world.get_component(self.eid, Inventory)

    def test_item_stackavel_empilha_em_stack_existente(self):
        existing = Item("Poção de Vida", "consumable", slot=None,
                        max_stack=20, item_id="hp_potion")
        existing.stack = 2
        self.inv.items.append(existing)
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("hp_potion", stack=3)])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(self.inv.items), 1, "não deveria ter criado slot novo")
        self.assertEqual(self.inv.items[0].stack, 5)

    def test_item_nao_stackavel_ocupa_slot_novo(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(self.inv.items), 1)
        self.assertEqual(self.inv.items[0].item_id, "iron_sword")

    def test_take_item_casa_por_item_id_nao_por_nome(self):
        cid = _make_corpse(self.ws, self.eid,
                           [_loot_dict("hp_potion"), _loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual([i["item_id"] for i in result["items"]], ["iron_sword"])
        remaining = [i["item_id"] for i in self.ws._corpses[cid]["items"]]
        self.assertEqual(remaining, ["hp_potion"])
        self.assertEqual(len(self.inv.items), 1)
        self.assertEqual(self.inv.items[0].item_id, "iron_sword")

    def test_dado_invalido_no_reconstruct_nao_desaparece_do_cadaver(self):
        """Dict sem item_id e sem name (não deveria acontecer em produção —
        corpse só é populado por lógica server-side) não pode simplesmente
        sumir do corpse em silêncio — fica lá, não concedido, mesma regra
        de 'sem espaço'."""
        bad = {"stack": 1}
        cid = _make_corpse(self.ws, self.eid, [bad])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(result["items"], [])
        self.assertEqual(self.ws._corpses[cid]["items"], [bad],
                         "dado inválido deveria permanecer no corpse, não sumir")


class TestLootItemSemEspacoFicaNoCadaver(unittest.TestCase):
    """Decisão do usuário (11/08/2026): bag cheia não é comportamento de
    loot — o item simplesmente continua no corpse, sem lógica nova de
    expiração (o timer normal do corpse já cobre)."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        # Enche a bag com slots não-empilháveis distintos.
        for i in range(self.inv.max_slots):
            filler = Item(f"Enchimento {i}", "material", slot=None,
                         max_stack=1, item_id=f"filler_{i}")
            self.inv.items.append(filler)

    def test_take_all_com_bag_cheia_nao_perde_o_item(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")], coins=5)

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(result["items"], [])
        self.assertTrue(result["no_space"])
        self.assertEqual(result["coins"], 5, "ouro não depende de espaço na bag")
        self.assertEqual(len(self.ws._corpses[cid]["items"]), 1,
                         "item que não coube deveria continuar no corpse")
        self.assertEqual(len(self.inv.items), self.inv.max_slots,
                         "bag não deveria ter sido alterada")

    def test_take_item_com_bag_cheia_retorna_no_space(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual(result["items"], [])
        self.assertTrue(result["no_space"])
        self.assertEqual(len(self.ws._corpses[cid]["items"]), 1)

    def test_take_item_ja_pego_por_outro_nao_e_no_space(self):
        """Corpse já vazio pro item pedido (outro membro do grupo já
        levou) é um caso DIFERENTE de 'não coube' — no_space deve ficar
        False (cliente mostra 'já foi saqueado', não 'mochila cheia')."""
        cid = _make_corpse(self.ws, self.eid, [])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual(result["items"], [])
        self.assertFalse(result["no_space"])

    def test_item_stackavel_ainda_empilha_mesmo_com_bag_cheia_de_outros_itens(self):
        """Bag "cheia" (max_slots atingido) ainda aceita empilhar num slot
        JÁ existente do mesmo item_id — só precisa de slot livre quando
        for criar um NOVO."""
        existing = Item("Poção de Vida", "consumable", slot=None,
                        max_stack=20, item_id="hp_potion")
        existing.stack = 1
        self.inv.items[0] = existing  # substitui 1 filler por uma poção já estocada
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("hp_potion", stack=2)])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(existing.stack, 3)
        self.assertFalse(result["no_space"])


class TestLootQuestProgressViaSessionFlow(unittest.IsolatedAsyncioTestCase):
    """Progresso de collect_item precisa disparar DIRETO no fluxo de
    saque agora — o cliente não manda mais INV_SYNC depois de lootear
    (débito A4, request_loot já é quem muta o Inventory ao vivo)."""

    QID = "qcl_loot_flow_teste"

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login, get_msgs_of_type
        self._fake_login = fake_login
        self._get_msgs = get_msgs_of_type
        self.ws_server, self.mgr = make_session_manager()
        from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
        QUESTS[self.QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="*",
                                     loot_item="arrow", count=2),),
            reward=QuestReward(xp=1),
        )

    async def asyncTearDown(self):
        from content.quests_data import QUESTS
        QUESTS.pop(self.QID, None)

    async def test_saquear_item_de_quest_atualiza_progresso_sem_inv_sync(self):
        from shared.messages import encode, MsgType
        from engine.components import QuestLog
        session, fw = await self._fake_login(self.mgr, "s1", "user_loot_qp_a", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = _make_corpse(self.ws_server, session.entity_id,
                           [_loot_dict("arrow", stack=2)])

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        updates = self._get_msgs(fw, MsgType.QUEST_UPDATE)
        self.assertEqual(len(updates), 1,
                         "saque de item de quest deveria disparar QUEST_UPDATE direto, sem INV_SYNC")
        self.assertEqual(updates[0]["active"][self.QID], [2])

    async def test_saque_sem_item_de_quest_nao_dispara_quest_update(self):
        from shared.messages import encode, MsgType
        from engine.components import QuestLog
        session, fw = await self._fake_login(self.mgr, "s1", "user_loot_qp_b", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = _make_corpse(self.ws_server, session.entity_id,
                           [_loot_dict("iron_sword")])   # não é o item da quest

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        updates = self._get_msgs(fw, MsgType.QUEST_UPDATE)
        self.assertEqual(updates, [], "item alheio à quest não deveria disparar QUEST_UPDATE")


class TestNameCollisionGuard(unittest.TestCase):
    """Trava de integridade (11/08/2026, pedido do usuário): dois
    item_ids diferentes com o mesmo nome de exibição recusam o boot do
    servidor — decisão explícita do usuário (hard-fail, não só log)."""

    def test_catalogo_real_atual_nao_tem_colisao(self):
        from server.world_server import WorldServer
        WorldServer._check_item_name_collisions()  # não deveria levantar

    def test_colisao_sintetica_levanta_runtime_error(self):
        from server.world_server import WorldServer
        from content.crafting_data import MATERIALS

        MATERIALS["__teste_colisao__"] = lambda: Item(
            "Poção de Vida", "material", slot=None, max_stack=1,
            item_id="__fake_colisao_id__")
        try:
            with self.assertRaises(RuntimeError):
                WorldServer._check_item_name_collisions()
        finally:
            MATERIALS.pop("__teste_colisao__", None)


if __name__ == "__main__":
    unittest.main()
