"""
tests/test_trade_state_stack_diff.py — bug real relatado pelo usuário
(11/08/2026, ver PROBLEMAS_ARQUITETURA.md §26): clicar direito numa
stack pra ofertar 1 unidade no trade fazia a STACK INTEIRA sumir da
bag, não só a unidade ofertada.

Causa raiz: `client/network_handlers.py::_handle_msg_trade_state`
diffava a oferta por CONTAGEM de entradas (multiset por nome) — 1
entrada nova na oferta = remove 1 slot INTEIRO da bag. Isso era certo
enquanto toda oferta sempre movia o item inteiro (antes do fatiar de
stack, §26-A), mas quebrou quando passou a aceitar quantidade parcial:
ofertar 1 de uma stack de 5 conta como "1 entrada nova", que ainda
populava o pop do slot inteiro, matando as outras 4 unidades que
deveriam ter ficado na bag.

Fix: diff por QUANTIDADE (soma de `stack` por item_id/nome) em vez de
contagem de entradas.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from client.network_handlers import NetworkHandlers
from engine.world import World
from engine.components import Inventory, Wallet, Item
from ui.ui_components import TradeUIState


def _potion(stack: int, item_id: str = "hp_potion") -> Item:
    it = Item("Poção de Vida", "consumable", slot=None, max_stack=20, item_id=item_id)
    it.stack = stack
    return it


class _TradeFixture(NetworkHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity

    def _get_trade_ui(self):
        return self.world.get_component(self.player_entity, TradeUIState)

    def _item_from_data(self, d: dict):
        if not d:
            return None
        it = Item(d.get("name", ""), d.get("item_type", ""), slot=d.get("slot", ""),
                  max_stack=d.get("max_stack", 1), item_id=d.get("item_id", ""))
        it.stack = d.get("stack", 1)
        return it

    def _send_save_state(self) -> None:
        pass


def _make_fixture(bag_items=None, my_gold=0):
    world = World()
    player = world.create_entity()
    inv = Inventory()
    inv.items = list(bag_items or [])
    world.add_component(player, inv)
    world.add_component(player, Wallet(gold=100))
    tui = TradeUIState()
    tui.trade_id = 1
    tui.my_gold = my_gold
    world.add_component(player, tui)
    fx = _TradeFixture(world, player)
    return fx, world, player, inv, tui


def _item_data(item_id, name, stack, max_stack=20):
    return {"item_id": item_id, "name": name, "item_type": "consumable",
           "rarity": "common", "value": 1, "slot": "", "stack": stack,
           "max_stack": max_stack}


class TestPartialStackOfferKeepsRestInBag(unittest.TestCase):

    def test_ofertar_1_de_uma_stack_de_5_remove_so_1_da_bag(self):
        """Cenário EXATO relatado pelo usuário: clique direito numa
        stack de poções oferta 1, mas a bag inteira sumia."""
        fx, world, player, inv, tui = _make_fixture([_potion(5)])

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("hp_potion", "Poção de Vida", 1)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(len(inv.items), 1, "as outras 4 unidades deveriam continuar na bag")
        self.assertEqual(inv.items[0].stack, 4)
        self.assertEqual(len(tui.my_offer), 1)
        self.assertEqual(tui.my_offer[0].stack, 1)

    def test_ofertar_quantidade_do_modal_remove_so_a_quantidade_escolhida(self):
        """Mesmo cenário via modal de quantidade (Shift+clique, escolheu 3)."""
        fx, world, player, inv, tui = _make_fixture([_potion(10)])

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("hp_potion", "Poção de Vida", 3)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(inv.items[0].stack, 7)
        self.assertEqual(tui.my_offer[0].stack, 3)

    def test_ofertar_stack_inteira_ainda_remove_o_slot_por_completo(self):
        """Regressão: comportamento original (item_id/quantity ausente —
        oferece tudo) continua removendo o slot inteiro quando a
        quantidade ofertada É a stack inteira."""
        fx, world, player, inv, tui = _make_fixture([_potion(5)])

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("hp_potion", "Poção de Vida", 5)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(inv.items, [])
        self.assertEqual(tui.my_offer[0].stack, 5)

    def test_ofertas_sucessivas_acumulam_decremento_corretamente(self):
        """2 sincronizações seguidas (oferta 1, depois oferta mais 2) —
        o diff é sempre contra o ESTADO anterior (tui.my_offer), não
        cumulativo em duplicidade."""
        fx, world, player, inv, tui = _make_fixture([_potion(10)])

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("hp_potion", "Poção de Vida", 1)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })
        self.assertEqual(inv.items[0].stack, 9)

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("hp_potion", "Poção de Vida", 3)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })
        self.assertEqual(inv.items[0].stack, 7, "só mais 2 deveriam sair (3 total - 1 já ofertado)")
        self.assertEqual(tui.my_offer[0].stack, 3)

    def test_item_nao_empilhavel_continua_funcionando_normal(self):
        sword = Item("Espada de Ferro", "weapon", slot="mainhand",
                     max_stack=1, item_id="iron_sword")
        sword.stack = 1
        fx, world, player, inv, tui = _make_fixture([sword])

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("iron_sword", "Espada de Ferro", 1, max_stack=1)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(inv.items, [])
        self.assertEqual(tui.my_offer[0].stack, 1)


class TestWithdrawMergesBackIntoExistingStack(unittest.TestCase):

    def test_retirar_oferta_fatiada_mescla_de_volta_no_stack_que_sobrou(self):
        fx, world, player, inv, tui = _make_fixture([_potion(7)])  # já tinha ofertado 3 antes
        tui.my_offer = [fx._item_from_data(_item_data("hp_potion", "Poção de Vida", 3))]

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [],   # retirou tudo da oferta
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(len(inv.items), 1, "deveria mesclar de volta no slot existente, não criar um 2º")
        self.assertEqual(inv.items[0].stack, 10)
        self.assertEqual(tui.my_offer, [])

    def test_retirar_oferta_sem_stack_existente_cria_slot_novo(self):
        fx, world, player, inv, tui = _make_fixture([])
        tui.my_offer = [fx._item_from_data(_item_data("hp_potion", "Poção de Vida", 3))]

        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })

        self.assertEqual(len(inv.items), 1)
        self.assertEqual(inv.items[0].stack, 3)


if __name__ == "__main__":
    unittest.main()
