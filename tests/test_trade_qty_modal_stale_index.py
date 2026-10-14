"""
tests/test_trade_qty_modal_stale_index.py — 11/10/2026, bug real
relatado pelo usuário: abrir o modal de quantidade do trade (Shift+
clique num item empilhável), escolher a quantidade e confirmar fazia o
item ERRADO ir pro slot de oferta.

Causa raiz: o modal guardava a POSIÇÃO (`inv_index`) do item no momento
em que abriu. Se QUALQUER outra oferta do mesmo jogador fosse
confirmada pelo servidor (TRADE_STATE) enquanto o modal ainda estava
aberto esperando o jogador escolher a quantidade,
`client/network_handlers.py::_handle_msg_trade_state` removia o item
ofertado de `Inventory.items` por índice — deslocando todo mundo depois
dele. A posição antiga guardada no modal passava a apontar pra outro
item, mas o clique em OK/Enter confiava cegamente nela.

Fix: `_confirm_trade_qty_modal` (novo, client/trade_handlers.py)
reencontra a posição ATUAL do MESMO objeto `item` (guardado por
referência desde a abertura do modal, nunca trocado) em vez de confiar
na posição antiga — cancela em vez de ofertar algo errado se o item já
não existir mais na bolsa.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from client.network_handlers import NetworkHandlers
from client.trade_handlers import TradeHandlers
from engine.world import World
from engine.components import Inventory, Wallet, Item
from ui.ui_components import TradeUIState
from shared.messages import MsgType


def _item(name: str, item_id: str, stack: int = 1, max_stack: int = 1) -> Item:
    it = Item(name, "consumable" if max_stack > 1 else "weapon",
             slot=None if max_stack > 1 else "mainhand",
             max_stack=max_stack, item_id=item_id)
    it.stack = stack
    return it


class _FakeNet:
    def __init__(self):
        self.sent: list[tuple] = []
        self.connected = True

    def send(self, msg_type, payload) -> None:
        self.sent.append((msg_type, payload))


class _TradeQtyFixture(NetworkHandlers, TradeHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._net = _FakeNet()
        self._trade_qty_modal = None
        self._my_eid = 1

    def _get_trade_ui(self):
        return self.world.get_component(self.player_entity, TradeUIState)

    def _item_from_data(self, d: dict):
        if not d:
            return None
        it = Item(d.get("name", ""), d.get("item_type", ""), slot=d.get("slot", ""),
                  max_stack=d.get("max_stack", 1), item_id=d.get("item_id", ""))
        it.stack = d.get("stack", 1)
        return it

    def _send_save_state(self, include_fog: bool = True) -> None:
        pass


def _item_data(item_id, name, stack, max_stack=20):
    return {"item_id": item_id, "name": name, "item_type": "consumable",
           "rarity": "common", "value": 1, "slot": "", "stack": stack,
           "max_stack": max_stack}


def _make_fixture(bag_items):
    world = World()
    player = world.create_entity()
    inv = Inventory()
    inv.items = list(bag_items)
    world.add_component(player, inv)
    world.add_component(player, Wallet(gold=0))
    tui = TradeUIState()
    tui.trade_id = 1
    world.add_component(player, tui)
    fx = _TradeQtyFixture(world, player)
    return fx, inv, tui


class TestConfirmTradeQtyModalReresolveIndex(unittest.TestCase):

    def test_sem_mudanca_na_bag_oferta_o_item_certo(self):
        """Caso comum: nada muda entre abrir e confirmar — continua
        funcionando igual antes."""
        potion = _item("Poção de Vida", "hp_potion", stack=5, max_stack=20)
        fx, inv, tui = _make_fixture([potion])

        fx._open_trade_qty_modal(0, potion)
        fx._trade_qty_modal["qty"] = 3
        fx._confirm_trade_qty_modal()

        self.assertEqual(len(fx._net.sent), 1)
        msg_type, payload = fx._net.sent[0]
        self.assertEqual(msg_type, MsgType.TRADE_OFFER_ITEM)
        self.assertEqual(payload["inv_index"], 0)
        self.assertEqual(payload["quantity"], 3)
        self.assertIsNone(fx._trade_qty_modal, "modal deveria fechar após confirmar")

    def test_reproducao_exata_do_bug_indice_desatualizado_pela_confirmacao_de_outra_oferta(self):
        """Cenário EXATO relatado pelo usuário: item A é ofertado (índice
        0), ANTES da confirmação chegar o jogador abre o modal de
        quantidade pro item B (que estava no índice 1). A confirmação da
        oferta de A chega enquanto o modal ainda está aberto, removendo
        o índice 0 e deslocando B pro índice 0. Confirmar o modal tem
        que ofertar B (no índice NOVO, 0), nunca o que quer que esteja
        na posição antiga guardada (1, que já nem existe mais)."""
        item_a = _item("Espada de Ferro", "iron_sword", stack=1, max_stack=1)
        item_b = _item("Poção de Vida", "hp_potion", stack=5, max_stack=20)
        fx, inv, tui = _make_fixture([item_a, item_b])

        # Oferece item_a (índice 0) -- simula o clique direito simples.
        fx._offer_trade_item(0, quantity=1)
        fx._net.sent.clear()

        # Abre o modal de quantidade pro item_b, que está no índice 1
        # NESTE momento.
        fx._open_trade_qty_modal(1, item_b)
        fx._trade_qty_modal["qty"] = 2

        # A confirmação da oferta de item_a chega do servidor ENQUANTO o
        # modal ainda está aberto -- remove o índice 0, item_b desliza
        # pro índice 0.
        fx._handle_msg_trade_state({
            "trade_id": 1,
            "my_offer": [_item_data("iron_sword", "Espada de Ferro", 1, max_stack=1)],
            "my_gold": 0, "their_offer": [], "their_gold": 0,
        })
        self.assertEqual(inv.items[0], item_b, "item_b deveria ter deslizado pro índice 0")

        # Confirma o modal -- tem que ofertar item_b (índice NOVO, 0),
        # nunca a posição antiga (1).
        fx._confirm_trade_qty_modal()

        self.assertEqual(len(fx._net.sent), 1)
        msg_type, payload = fx._net.sent[0]
        self.assertEqual(msg_type, MsgType.TRADE_OFFER_ITEM)
        self.assertEqual(payload["inv_index"], 0,
            "deveria usar a posição ATUAL de item_b (0), não a antiga (1) -- bug relatado")
        self.assertEqual(payload["quantity"], 2)

    def test_item_do_modal_nao_existe_mais_cancela_sem_ofertar_nada(self):
        """Caso extremo: o item do modal foi totalmente consumido/
        removido da bolsa por outro caminho antes da confirmação --
        cancela em vez de ofertar o que quer que esteja na posição
        antiga (seria sempre errado)."""
        item_a = _item("Poção de Vida", "hp_potion", stack=5, max_stack=20)
        fx, inv, tui = _make_fixture([item_a])

        fx._open_trade_qty_modal(0, item_a)
        fx._trade_qty_modal["qty"] = 2

        inv.items.clear()  # item sumiu da bolsa por completo

        fx._confirm_trade_qty_modal()

        self.assertEqual(fx._net.sent, [], "não pode ofertar nada se o item do modal sumiu")
        self.assertIsNone(fx._trade_qty_modal)


if __name__ == "__main__":
    unittest.main()
