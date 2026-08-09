"""
tests/test_gm_commands.py — sistema de GM server-autoritativo (07/08/2026,
ver PROBLEMAS_ARQUITETURA.md/ARQUITETURA_ONLINE.md). GM_LEVELUP/
GM_ADD_GOLD/GM_ADD_ITEM substituem o menu de debug (F12) que antes só
mutava o ECS local do CLIENTE — o servidor nunca sabia do nível/talento
forçado, então qualquer skill autorizada por talento era recusada de
verdade (`is_skill_authorized`), tornando o F12 inútil pra testar
qualquer coisa online.

Cobre o par (nega sem `is_gm` / aplica com `is_gm`) pra cada uma das 3
mensagens — mesmo padrão de bypass negado usado em skill_processor.py
pra taunt/stun (sem is_gm, ignora silenciosamente, sem resposta)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_session import make_session_manager, fake_login, get_msgs_of_type
from shared.messages import MsgType
from server.auth import _register_account_sync, _hash
from server.grant_gm import set_gm
from engine.components import CharacterStats, CombatStats, TalentTree, Wallet, Inventory


async def _login(mgr, sid: str, username: str, *, gm: bool, class_id: str = "mago"):
    _register_account_sync(username, _hash("test123"))
    if gm:
        set_gm(username, True)
    return await fake_login(mgr, sid, username, class_id=class_id)


class TestGmLevelup(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def test_sem_gm_nao_faz_nada(self):
        session, fw = await _login(self.mgr, "gmlvl_a", "gmlvlusera", gm=False)
        char = self.ws.world.get_component(session.entity_id, CharacterStats)
        level_before = char.level
        fw.sent.clear()

        await self.mgr._handle_gm_levelup(session, {"levels": 5}, 0)
        await self.mgr._flush_stats_updates()

        self.assertEqual(char.level, level_before)
        self.assertEqual(get_msgs_of_type(fw, MsgType.STATS_UPDATE), [])

    async def test_com_gm_sobe_nivel_de_verdade_no_servidor(self):
        session, fw = await _login(self.mgr, "gmlvl_b", "gmlvluserb", gm=True)
        char = self.ws.world.get_component(session.entity_id, CharacterStats)
        tt   = self.ws.world.get_component(session.entity_id, TalentTree)
        level_before  = char.level
        points_before = tt.available_points if tt else 0
        fw.sent.clear()

        await self.mgr._handle_gm_levelup(session, {"levels": 5}, 0)
        await self.mgr._flush_stats_updates()

        self.assertGreater(char.level, level_before)
        self.assertGreater(tt.available_points, points_before)
        updates = get_msgs_of_type(fw, MsgType.STATS_UPDATE)
        self.assertTrue(any("level" in u for u in updates))


class TestGmAddGold(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def test_sem_gm_nao_muda_gold(self):
        session, fw = await _login(self.mgr, "gmgold_a", "gmgoldusera", gm=False)
        wallet = self.ws.world.get_component(session.entity_id, Wallet)
        gold_before = wallet.gold
        fw.sent.clear()

        await self.mgr._handle_gm_add_gold(session, {"amount": 500}, 0)
        await self.mgr._flush_stats_updates()

        self.assertEqual(wallet.gold, gold_before)
        self.assertEqual(get_msgs_of_type(fw, MsgType.STATS_UPDATE), [])

    async def test_com_gm_adiciona_gold_de_verdade(self):
        session, fw = await _login(self.mgr, "gmgold_b", "gmgolduserb", gm=True)
        wallet = self.ws.world.get_component(session.entity_id, Wallet)
        gold_before = wallet.gold
        fw.sent.clear()

        await self.mgr._handle_gm_add_gold(session, {"amount": 500}, 0)
        await self.mgr._flush_stats_updates()

        self.assertEqual(wallet.gold, gold_before + 500)
        updates = get_msgs_of_type(fw, MsgType.STATS_UPDATE)
        self.assertTrue(any(u.get("gold") == wallet.gold for u in updates))


class TestGmAddItem(unittest.IsolatedAsyncioTestCase):

    ITEM_NAME = "Espada de treinamento"

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def test_sem_gm_nao_adiciona_item(self):
        session, fw = await _login(self.mgr, "gmitem_a", "gmitemusera", gm=False)
        inv = self.ws.world.get_component(session.entity_id, Inventory)
        count_before = len(inv.items)
        fw.sent.clear()

        await self.mgr._handle_gm_add_item(session, {"item_name": self.ITEM_NAME}, 0)

        self.assertEqual(len(inv.items), count_before)
        self.assertEqual(get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE), [])

    async def test_com_gm_adiciona_item_de_verdade(self):
        session, fw = await _login(self.mgr, "gmitem_b", "gmitemuserb", gm=True)
        inv = self.ws.world.get_component(session.entity_id, Inventory)
        count_before = len(inv.items)
        fw.sent.clear()

        await self.mgr._handle_gm_add_item(session, {"item_name": self.ITEM_NAME}, 0)

        self.assertEqual(len(inv.items), count_before + 1)
        self.assertEqual(inv.items[-1].name, self.ITEM_NAME)
        updates = get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["items"][0]["name"], self.ITEM_NAME)

    async def test_item_desconhecido_nao_quebra_nem_adiciona(self):
        session, fw = await _login(self.mgr, "gmitem_c", "gmitemuserc", gm=True)
        inv = self.ws.world.get_component(session.entity_id, Inventory)
        count_before = len(inv.items)
        fw.sent.clear()

        await self.mgr._handle_gm_add_item(session, {"item_name": "Item Que Nao Existe"}, 0)

        self.assertEqual(len(inv.items), count_before)
        self.assertEqual(get_msgs_of_type(fw, MsgType.INVENTORY_UPDATE), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
