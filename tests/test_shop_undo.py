"""
tests/test_shop_undo.py — Desfazer da loja (21/10/2026, pedido do usuário):
dura enquanto a janela estiver aberta; servidor autoritativo
(WorldServer.process_shop_undo / clear_shop_undo). Desfaz a ÚLTIMA
transação (LIFO) devolvendo o valor exato dela.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player
from engine.components import Inventory, Wallet

NPC = 77


class TestShopUndo(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.inv.items.clear()
        self.wallet.gold = 100_000

    def _buy(self, item_id, qty=1):
        r = self.ws.process_shop_buy("s1", "general", "", qty, item_id=item_id, npc_eid=-1)
        self.assertTrue(r["success"], r)
        return r

    def test_desfazer_compra_devolve_ouro_e_tira_o_item(self):
        paid = self._buy("iron_sword")["price"]
        self.assertEqual(self.wallet.gold, 100_000 - paid)
        r = self.ws.process_shop_undo("s1", -1)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["kind"], "buy")
        self.assertEqual(self.wallet.gold, 100_000)
        self.assertEqual(self.inv.items, [])
        self.assertEqual(r["removed"], [{"item_id": "iron_sword", "stack": 1}])
        self.assertEqual(r["undo_left"], 0)

    def test_desfazer_venda_devolve_o_item_e_cobra_o_recebido(self):
        sword = self.ws._item_factory_by_id("iron_sword")
        self.inv.items.append(sword)
        s = self.ws.process_shop_sell("s1", sword.name, sword.value, 1, item_id="iron_sword", npc_eid=-1)
        self.assertTrue(s["success"])
        self.assertEqual(self.inv.items, [])
        r = self.ws.process_shop_undo("s1", -1)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["kind"], "sell")
        self.assertEqual(self.wallet.gold, 100_000)
        self.assertEqual([it.item_id for it in self.inv.items], ["iron_sword"])
        self.assertEqual(r["item"]["item_id"], "iron_sword")

    def test_lifo_e_compra_ja_consumida_sai_do_historico(self):
        self._buy("iron_sword")
        self._buy("arrow", 10)
        # flechas usadas antes de desfazer: não dá mais pra desfazer essa
        next(it for it in self.inv.items if it.item_id == "arrow").stack = 3
        r = self.ws.process_shop_undo("s1", -1)
        self.assertFalse(r["success"])
        self.assertTrue(r["discarded"])
        self.assertEqual(r["undo_left"], 1)
        r = self.ws.process_shop_undo("s1", -1)   # agora desfaz a espada
        self.assertTrue(r["success"])
        self.assertEqual(r["kind"], "buy")

    def test_venda_sem_ouro_pra_devolver_falha_e_mantem_historico(self):
        sword = self.ws._item_factory_by_id("iron_sword")
        self.inv.items.append(sword)
        self.ws.process_shop_sell("s1", sword.name, sword.value, 1, item_id="iron_sword", npc_eid=-1)
        self.wallet.gold = 0
        r = self.ws.process_shop_undo("s1", -1)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "insufficient_gold")
        self.assertEqual(r["undo_left"], 1)

    def test_fechar_a_janela_esquece_e_outro_mercador_nao_desfaz(self):
        paid = self._buy("iron_sword")["price"]
        r = self.ws.process_shop_undo("s1", NPC)   # outra janela
        self.assertFalse(r["success"])
        self.ws.clear_shop_undo("s1")
        r = self.ws.process_shop_undo("s1", -1)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "nothing_to_undo")
        self.assertEqual(self.wallet.gold, 100_000 - paid)


if __name__ == "__main__":
    unittest.main()
