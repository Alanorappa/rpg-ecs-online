"""
tests/test_trade_quantity_slice.py — fatiar quantidade ao ofertar item
empilhável no trade (11/08/2026, pedido do usuário, ver
PROBLEMAS_ARQUITETURA.md). Antes, clique direito na bag sempre movia a
STACK INTEIRA pra oferta (`add_trade_item` só sabia popar por índice).
Agora `add_trade_item(player_eid, inv_index, quantity)` fatia: item de
origem perde só `quantity` do stack (o resto continua na mochila), e
uma CÓPIA nova (reconstruída via `_item_factory_by_id`, nunca por cópia
direta de objeto do cliente) entra na oferta com `stack=quantity`.

Decisão do usuário: clique direito simples agora oferece 1 unidade por
padrão (igual ao padrão de compra na loja, BUY_REQUEST); Shift+clique
abre o modal de quantidade — mas o SERVIDOR não sabe de Shift/UI, só
recebe `quantity` explícito; o comportamento "sem quantity = oferece
tudo" continua existindo como fallback de protocolo (ex: client antigo/
mensagem malformada).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import Inventory, Item


class TestAddTradeItemQuantitySlice(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a  = spawn_player(self.ws, "s1", 130, 374)
        self.b  = spawn_player(self.ws, "s2", 131, 374)
        self.assertIsNone(self.ws.request_trade(self.a, self.b))
        _requester, self.trade_id = self.ws.respond_trade_invite(self.b, accept=True)
        self.assertIsNotNone(self.trade_id)
        self.inv_a = self.ws.world.get_component(self.a, Inventory)

    def _give_potion(self, stack: int) -> Item:
        potion = self.ws._item_factory_by_id("hp_potion")
        potion.stack = stack
        self.inv_a.items.append(potion)
        return potion

    def test_quantity_none_oferece_stack_inteira_comportamento_original(self):
        potion = self._give_potion(5)
        result = self.ws.add_trade_item(self.a, 0, None)
        self.assertIsNone(result)
        self.assertNotIn(potion, self.inv_a.items)
        session = self.ws.get_trade_session(self.a)
        self.assertEqual(session.offer_of(self.a), [potion])
        self.assertEqual(session.offer_of(self.a)[0].stack, 5)

    def test_quantity_maior_ou_igual_ao_stack_oferece_tudo(self):
        potion = self._give_potion(3)
        result = self.ws.add_trade_item(self.a, 0, 10)
        self.assertIsNone(result)
        self.assertNotIn(potion, self.inv_a.items)
        session = self.ws.get_trade_session(self.a)
        self.assertEqual(session.offer_of(self.a)[0].stack, 3)

    def test_quantity_parcial_fatia_mantem_resto_na_mochila(self):
        self._give_potion(5)
        result = self.ws.add_trade_item(self.a, 0, 2)
        self.assertIsNone(result)
        # Resto (3) continua na mochila, no MESMO slot.
        self.assertEqual(len(self.inv_a.items), 1)
        self.assertEqual(self.inv_a.items[0].stack, 3)
        # Oferta recebeu uma cópia NOVA com stack=2.
        session = self.ws.get_trade_session(self.a)
        offered = session.offer_of(self.a)
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0].stack, 2)
        self.assertEqual(offered[0].item_id, "hp_potion")
        self.assertIsNot(offered[0], self.inv_a.items[0], "oferta e mochila não podem compartilhar o mesmo objeto")

    def test_quantity_1_default_do_clique_simples(self):
        self._give_potion(5)
        result = self.ws.add_trade_item(self.a, 0, 1)
        self.assertIsNone(result)
        self.assertEqual(self.inv_a.items[0].stack, 4)
        session = self.ws.get_trade_session(self.a)
        self.assertEqual(session.offer_of(self.a)[0].stack, 1)

    def test_item_nao_empilhavel_ignora_quantity_oferece_inteiro(self):
        sword = self.ws._item_factory_by_id("iron_sword")
        sword.stack = 1
        self.inv_a.items.append(sword)
        result = self.ws.add_trade_item(self.a, 0, 1)
        self.assertIsNone(result)
        self.assertNotIn(sword, self.inv_a.items)
        session = self.ws.get_trade_session(self.a)
        self.assertEqual(session.offer_of(self.a), [sword])

    def test_item_sem_item_id_nao_e_fatiado_com_seguranca(self):
        """Item legado (save antigo sem item_id) — clonar via catálogo é
        impossível, então mesmo com quantity parcial oferece tudo (não
        quebra, não inventa stats)."""
        fake = Item("Item Legado", "material", slot=None, max_stack=10)
        fake.stack = 5
        fake.item_id = ""
        self.inv_a.items.append(fake)
        result = self.ws.add_trade_item(self.a, 0, 2)
        self.assertIsNone(result)
        self.assertNotIn(fake, self.inv_a.items)
        session = self.ws.get_trade_session(self.a)
        self.assertEqual(session.offer_of(self.a)[0].stack, 5)

    def test_cancelar_trade_devolve_fatia_ofertada_pra_mochila(self):
        """Regressão do fluxo de cancelamento existente (_cancel_trade_session)
        — precisa continuar funcionando com a cópia fatiada (não é o
        mesmo objeto que ficou na mochila)."""
        self._give_potion(5)
        self.ws.add_trade_item(self.a, 0, 2)
        self.ws.cancel_trade(self.a)
        # 2 slots agora: o resto (3) que já estava, + a fatia devolvida (2).
        stacks = sorted(it.stack for it in self.inv_a.items)
        self.assertEqual(stacks, [2, 3])

    def test_withdraw_devolve_a_fatia_pra_mochila_como_slot_novo(self):
        self._give_potion(5)
        self.ws.add_trade_item(self.a, 0, 2)
        result = self.ws.withdraw_trade_item(self.a, 0)
        self.assertIsNone(result)
        stacks = sorted(it.stack for it in self.inv_a.items)
        self.assertEqual(stacks, [2, 3])


class TestTradeOfferItemProtocol(unittest.IsolatedAsyncioTestCase):
    """Round-trip via sessão real (TRADE_OFFER_ITEM com/sem quantity)."""

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self._fake_login = fake_login
        self.ws_server, self.mgr = make_session_manager()

    async def test_protocolo_manda_quantity_para_add_trade_item(self):
        from shared.messages import encode, MsgType
        session_a, fw_a = await self._fake_login(self.mgr, "s1", "trade_qty_a", 130, 374)
        session_b, _    = await self._fake_login(self.mgr, "s2", "trade_qty_b", 131, 374)
        self.assertIsNone(self.ws_server.request_trade(session_a.entity_id, session_b.entity_id))
        self.ws_server.respond_trade_invite(session_b.entity_id, accept=True)

        inv_a = self.ws_server.world.get_component(session_a.entity_id, Inventory)
        potion = self.ws_server._item_factory_by_id("hp_potion")
        potion.stack = 5
        inv_a.items.append(potion)

        await self.mgr.on_message(session_a, encode(
            MsgType.TRADE_OFFER_ITEM, {"inv_index": 0, "quantity": 2}))

        session = self.ws_server.get_trade_session(session_a.entity_id)
        self.assertEqual(len(session.offer_of(session_a.entity_id)), 1)
        self.assertEqual(session.offer_of(session_a.entity_id)[0].stack, 2)
        self.assertEqual(inv_a.items[0].stack, 3)


if __name__ == "__main__":
    unittest.main()
