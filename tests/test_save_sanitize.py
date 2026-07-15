"""
tests/test_save_sanitize.py — sanitize_inventory_payload na borda de
persistência (item A4, PROBLEMAS_ARQUITETURA.md §11): item forjado
descartado, stats sempre do catálogo, stack/aljava clampados.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from tests.helpers import make_world_server


class TestSanitizeInventory(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ws = make_world_server()
        from content.loot_tables import _T as LT
        cls.loot_item = next(f() for f in LT.values() if callable(f))
        from content.merchant_data import SHOPS
        cls.shop_item = next(e["factory"]() for s in SHOPS.values()
                             for e in s.get("stock", []))
        from content.crafting_data import RECIPES
        cls.craft_item = next(r["result_factory"]() for r in RECIPES.values()
                              if r.get("result_factory"))

    def test_item_de_cada_catalogo_sobrevive(self):
        """Inclui o de FORJA — antes do fix de _build_item_caches, item
        craftado ficava fora do _item_value_cache e seria descartado."""
        payload = [
            {"name": self.loot_item.name,  "item_type": self.loot_item.item_type,
             "slot": self.loot_item.slot},
            {"name": self.shop_item.name,  "item_type": self.shop_item.item_type,
             "slot": self.shop_item.slot},
            {"name": self.craft_item.name, "item_type": self.craft_item.item_type,
             "slot": self.craft_item.slot},
        ]
        out = self.ws.sanitize_inventory_payload(payload)
        self.assertEqual([d["name"] for d in out],
                         [self.loot_item.name, self.shop_item.name, self.craft_item.name])

    def test_item_forjado_descartado_e_stats_do_catalogo(self):
        payload = [
            {"name": "Espada do Hacker Supremo", "item_type": "weapon", "slot": "mainhand",
             "modifiers": [{"attribute": "attack_power", "value": 99999, "type": "flat"}],
             "value": 999999},
            {"name": self.loot_item.name, "item_type": self.loot_item.item_type,
             "slot": self.loot_item.slot,
             "modifiers": [{"attribute": "attack_power", "value": 99999, "type": "flat"}],
             "value": 999999},
        ]
        out = self.ws.sanitize_inventory_payload(payload)
        names = [d["name"] for d in out]
        self.assertNotIn("Espada do Hacker Supremo", names, "item forjado passou")
        self.assertIn(self.loot_item.name, names)
        legit = next(d for d in out if d["name"] == self.loot_item.name)
        self.assertNotEqual(legit.get("value", 0), 999999, "value forjado sobreviveu")
        forged_mods = [m for m in legit.get("modifiers", [])
                       if m.get("value") == 99999]
        self.assertFalse(forged_mods, "modifier forjado sobreviveu")

    def test_stack_clampado_e_lixo_ignorado(self):
        payload = [
            {"name": self.loot_item.name, "item_type": self.loot_item.item_type,
             "slot": self.loot_item.slot, "stack": 999999},
            "lixo-nao-dict",
        ]
        out = self.ws.sanitize_inventory_payload(payload)
        self.assertEqual(len(out), 1)
        cap = max(1, getattr(self.loot_item, "max_stack", 1))
        self.assertLessEqual(out[0].get("stack", 1), cap, "stack não clampado")

    def test_payload_invalido(self):
        self.assertIsNone(self.ws.sanitize_inventory_payload(None))
        self.assertIsNone(self.ws.sanitize_inventory_payload("x"))
        self.assertEqual(self.ws.sanitize_inventory_payload([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
