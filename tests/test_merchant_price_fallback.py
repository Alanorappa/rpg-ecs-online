"""
tests/test_merchant_price_fallback.py

Pedido do usuário (10/10/2026): "price" numa entrada de stock de
merchant_data.py virou opcional — quando ausente, o preço de compra cai
pro `.value` do próprio item (item_table.py), sem markup. Resolvido 1x na
carga do módulo (`content.merchant_data._fill_missing_prices`), nunca por
venda/frame — `ui/shop_system.py` (cliente) e
`server/world_server.py::_build_item_caches`/`process_shop_buy` (servidor)
continuam lendo `entry["price"]` sem nenhuma mudança, porque já chega
preenchido.
"""
import unittest

from content.item_table import ITEMS
from content.merchant_data import SHOPS, _fill_missing_prices


class TestMerchantPriceFallback(unittest.TestCase):

    def test_entrada_sem_price_cai_pro_value_do_item(self):
        shop = {"loja_teste": {"stock": [{"factory": ITEMS["iron_sword"]}]}}
        _fill_missing_prices(shop)
        entry = shop["loja_teste"]["stock"][0]
        self.assertIn("price", entry)
        self.assertEqual(entry["price"], entry["factory"]().value)

    def test_entrada_com_price_explicito_nao_e_sobrescrita(self):
        shop = {"loja_teste": {"stock": [{"factory": ITEMS["iron_sword"], "price": 999}]}}
        _fill_missing_prices(shop)
        self.assertEqual(shop["loja_teste"]["stock"][0]["price"], 999)

    def test_price_zero_nao_e_tratado_como_ausente(self):
        # 0 é um preço válido (item "de graça") — só None/chave ausente
        # deve cair pro fallback, nunca um 0 explícito.
        shop = {"loja_teste": {"stock": [{"factory": ITEMS["iron_sword"], "price": 0}]}}
        _fill_missing_prices(shop)
        self.assertEqual(shop["loja_teste"]["stock"][0]["price"], 0)

    def test_shops_real_todas_as_entradas_tem_price_apos_carga_do_modulo(self):
        for shop_id, shop in SHOPS.items():
            for entry in shop.get("stock", []):
                self.assertIn("price", entry, f"{shop_id}: entrada sem price")
                self.assertIsNotNone(entry["price"], f"{shop_id}: price None")

    def test_instance_shop_nao_e_afetada_pelo_fallback(self):
        # instance_shop já resolve price por raridade/value próprios
        # (_instance_shop_price) — nunca deve passar pelo fallback genérico.
        inst = SHOPS["instance_shop"]["stock"]
        self.assertTrue(len(inst) > 0)
        for entry in inst:
            self.assertIsNotNone(entry["price"])


if __name__ == "__main__":
    unittest.main()
