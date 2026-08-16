"""
tests/test_icon_key_migration.py — ícone de item migrado de nome de
exibição pra item_id (11/08/2026, pedido do usuário, ver
PROBLEMAS_ARQUITETURA.md §25). `IconManager.item_key(item)` agora
prefere `item.item_id` (chave estável, nunca colide — trava de
integridade em `WorldServer._check_item_name_collisions` garante isso
pro conteúdo real); só cai pro nome de exibição quando item_id está
vazio (item legado/inerte, sem catálogo).

Arquivos em assets/icons/ renomeados de item_<nome_snake>.png pra
item_<item_id>.png (21 arquivos, incluindo 3 que já estavam órfãos
antes da migração por nome de exibição desatualizado/typo — corrigidos
junto, confirmados com o usuário).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))  # necessário pra Surface.convert_alpha()

from ui.icon_manager import ICONS, IconManager
from engine.components import Item


class TestItemKeyPreferItemId(unittest.TestCase):

    def test_item_com_item_id_usa_item_id(self):
        item = Item("Nome Qualquer", "weapon", slot="mainhand", item_id="iron_sword")
        self.assertEqual(IconManager.item_key(item), "item_iron_sword")

    def test_item_sem_item_id_cai_pro_nome(self):
        item = Item("Item Legado Sem Catalogo", "material", slot=None, item_id="")
        self.assertEqual(IconManager.item_key(item), "item_item_legado_sem_catalogo")

    def test_item_key_by_name_mesma_formatacao_do_fallback(self):
        self.assertEqual(IconManager.item_key_by_name("Poção de Vida"), "item_poção_de_vida")

    def test_nomes_iguais_item_ids_diferentes_geram_chaves_diferentes(self):
        """A razão de existir da migração: dois itens com nome IGUAL mas
        item_id diferente não podem colidir na mesma chave de ícone."""
        a = Item("Relíquia", "material", slot=None, item_id="reliquia_a")
        b = Item("Relíquia", "material", slot=None, item_id="reliquia_b")
        self.assertNotEqual(IconManager.item_key(a), IconManager.item_key(b))


class TestRealCatalogIconsResolve(unittest.TestCase):
    """Prova diferencial: cada item_id de catálogo que TINHA um ícone
    antes da migração continua resolvendo um arquivo real em disco
    depois do rename — falha se algum arquivo ficou pra trás/typo."""

    EXPECTED_ITEM_IDS = [
        "basic_quiver", "short_bow", "artefato_misterioso", "roasted_meat",
        "hearty_stew", "iron_sword", "training_sword", "arrow",
        "large_waterskin", "medium_waterskin", "small_waterskin",
        "mana_potion", "hp_potion", "large_mana_potion", "large_hp_potion",
        "small_mana_potion", "small_hp_potion", "presa_lobo", "pa",
        "simple_bread", "vomito_zumbi", "lampiao",
    ]

    def test_todo_item_id_esperado_resolve_um_arquivo_real(self):
        for item_id in self.EXPECTED_ITEM_IDS:
            item = Item("qualquer", "material", slot=None, item_id=item_id)
            key = IconManager.item_key(item)
            surf = ICONS.get(key, 32)
            self.assertIsNotNone(surf, f"ícone ausente pra item_id={item_id!r} (key={key!r})")

    def test_item_id_de_verdade_do_catalogo_resolve_icone(self):
        """Reconstrói via catálogo REAL (não string à mão) — prova que o
        item_id do jogo de verdade bate com o arquivo renomeado."""
        from content.item_table import ITEMS
        sword = ITEMS["iron_sword"]()
        surf = ICONS.get(IconManager.item_key(sword), 32)
        self.assertIsNotNone(surf)


if __name__ == "__main__":
    unittest.main()
