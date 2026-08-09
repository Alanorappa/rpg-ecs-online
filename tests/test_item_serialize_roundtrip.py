"""
tests/test_item_serialize_roundtrip.py — Fase 1 do roteiro de saneamento
(07/08/2026, achado 06 do benchmark arquitetural, ver
PROBLEMAS_ARQUITETURA.md §12/§13): `client/save_sync_handlers.py` tinha
duas listas hardcoded de nomes de campo de Item (serialize/deserialize),
e já tinham divergido de verdade — "damage_min"/"damage_max" existem em
`Item`, estavam no loop de deserialize mas FALTAVAM no de serialize,
perdendo silenciosamente o dano de arma de qualquer item reconstruído
pelo caminho de fallback `_item_from_data` (ex: arma comprada em
loja/forjada, que não bate com nenhum factory de `loot_tables._T`).
Unificado numa constante única `_ITEM_STAT_FIELDS`.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import unittest
from client.save_sync_handlers import SaveSyncHandlers
from engine.components import Item


class _SaveSyncFixture(SaveSyncHandlers):
    """Só o suficiente pra exercitar _serialize_item/_item_from_data
    isolados — mesmo padrão de tests/test_hotbar_bg_leak.py::
    _SaveConfigFixture (atribui os métodos reais da classe mixin numa
    fixture leve, sem precisar de um GameEngine completo)."""
    pass


class TestItemSerializeRoundtrip(unittest.TestCase):

    def setUp(self):
        self.fx = _SaveSyncFixture()

    def test_dano_de_arma_sobrevive_ao_roundtrip_via_fallback(self):
        """Reproduz o bug real: item que só existe via _item_from_data
        (fallback — arma de loja/forjada, não bate com loot_tables._T)
        precisa manter damage_min/damage_max depois de serializar e
        reconstruir, exatamente como já fazia pra attack_power/armor/etc."""
        arma = Item("Espada Forjada Especial", "weapon", "mainhand",
                    rarity="rare", value=50, damage_min=5, damage_max=9,
                    subtype="Sword", attack_speed=2.0)

        d = self.fx._serialize_item(arma)
        self.assertEqual(d.get("damage_min"), 5)
        self.assertEqual(d.get("damage_max"), 9)

        reconstruida = self.fx._item_from_data(d)
        self.assertEqual(reconstruida.damage_min, 5,
            "dano mínimo da arma deveria sobreviver ao save/load (fallback)")
        self.assertEqual(reconstruida.damage_max, 9,
            "dano máximo da arma deveria sobreviver ao save/load (fallback)")

    def test_icon_key_morto_nao_aparece_no_serializado(self):
        """icon_key nunca foi atributo real de Item (getattr sempre
        retornava None) — confirma que a limpeza não reintroduziu um
        campo fantasma no payload."""
        item = Item("Item Qualquer", "misc", "", rarity="common", value=1)
        d = self.fx._serialize_item(item)
        self.assertNotIn("icon_key", d)

    def test_campos_gerais_continuam_no_roundtrip(self):
        """Regressão: os campos que já funcionavam antes (attack_power,
        item_level, description, etc.) continuam sobrevivendo depois da
        unificação das duas listas numa só."""
        item = Item("Cajado Arcano", "weapon", "mainhand", rarity="epic",
                    value=80, subtype="Staff", item_level=15,
                    level_requirement=10, description="Um cajado antigo.")
        # attack_power/spell_power não são kwargs do construtor — são
        # atributos dinâmicos setados depois (mesmo padrão real de
        # factory de item com bônus mágico).
        item.attack_power = 12
        item.spell_power = 20
        d = self.fx._serialize_item(item)
        reconstruido = self.fx._item_from_data(d)
        self.assertEqual(reconstruido.attack_power, 12)
        self.assertEqual(reconstruido.spell_power, 20)
        self.assertEqual(reconstruido.item_level, 15)
        self.assertEqual(reconstruido.level_requirement, 10)
        self.assertEqual(reconstruido.description, "Um cajado antigo.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
