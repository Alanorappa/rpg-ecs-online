"""
tests/test_equip_item_protocol.py — Débito A4 (10-11/08/2026, ver
PROBLEMAS_ARQUITETURA.md): equipar/desequipar deixou de mandar o estado
completo do Equipment (EQUIP_SYNC) e passou a mandar só a INTENÇÃO
(EQUIP_ITEM {inv_index}/UNEQUIP_ITEM {slot}) — o servidor lê o item de
verdade no seu próprio Inventory ao vivo, nunca confia em item mandado
pelo cliente.

Prova diferencial central desta migração: um EQUIP_ITEM com posição
forjada/vazia é ignorado (não "equipa nada do nada"), e as validações de
classe/level/offhand travado continuam bloqueando exatamente como no
protocolo antigo.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import Inventory, Equipment, Item


def _item(name="Item", item_type="armor", slot="chest", armor_class="placa",
         level_requirement=1, two_handed=False):
    return Item(name, item_type, slot, armor_class=armor_class,
               level_requirement=level_requirement, two_handed=two_handed)


class TestEquipItemFromInventory(unittest.TestCase):
    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.inv   = self.ws.world.get_component(self.eid, Inventory)
        self.equip = self.ws.world.get_component(self.eid, Equipment)

    def test_equip_item_move_da_bag_pro_slot(self):
        item = _item("Peitoral de Placa")
        self.inv.items.append(item)

        rejected = self.ws.equip_item_from_inventory("s1", 0)

        self.assertIsNone(rejected)
        self.assertIs(self.equip.slots["chest"], item)
        self.assertNotIn(item, self.inv.items)

    def test_equip_item_troca_item_ja_equipado_volta_pro_fim_da_bag(self):
        old = _item("Peitoral Velho")
        self.equip.slots["chest"] = old
        filler = _item("Enchimento", slot="head", armor_class="couro")
        new = _item("Peitoral Novo")
        self.inv.items.extend([filler, new])

        rejected = self.ws.equip_item_from_inventory("s1", 1)   # índice de `new`

        self.assertIsNone(rejected)
        self.assertIs(self.equip.slots["chest"], new)
        # Item antigo volta pro FIM da bag, não pro índice de origem —
        # mesma UX já provada em client/inventory_handlers.py::_equip_item.
        self.assertEqual(self.inv.items, [filler, old])

    def test_equip_item_arma_duas_maos_desequipa_offhand(self):
        shield = _item("Escudo", item_type="shield", slot="offhand", armor_class="")
        self.equip.slots["offhand"] = shield
        axe = _item("Machado Grande", item_type="weapon", slot="mainhand",
                    armor_class="", two_handed=True)
        self.inv.items.append(axe)

        rejected = self.ws.equip_item_from_inventory("s1", 0)

        self.assertIsNone(rejected)
        self.assertIs(self.equip.slots["mainhand"], axe)
        self.assertIsNone(self.equip.slots["offhand"])
        self.assertIn(shield, self.inv.items)

    def test_equip_item_recusa_por_classe_nao_move_item(self):
        # guerreiro (CLASS_ARMOR_ALLOWED) não usa "tecido"? Na verdade usa —
        # troca de classe pra "mago" (só "tecido") tentando equipar "placa".
        self.eid = spawn_player(self.ws, "s2", 131, 374, class_id="mago")
        inv   = self.ws.world.get_component(self.eid, Inventory)
        equip = self.ws.world.get_component(self.eid, Equipment)
        item = _item("Peitoral de Placa", armor_class="placa")
        inv.items.append(item)

        rejected = self.ws.equip_item_from_inventory("s2", 0)

        self.assertEqual(rejected, {"slot": "chest", "item_name": "Peitoral de Placa",
                                    "reason": "class"})
        self.assertIsNone(equip.slots["chest"])
        self.assertIn(item, inv.items)

    def test_equip_item_recusa_por_level_nao_move_item(self):
        item = _item("Peitoral Lendário", level_requirement=99)
        self.inv.items.append(item)

        rejected = self.ws.equip_item_from_inventory("s1", 0)

        self.assertEqual(rejected["reason"], "level")
        self.assertIsNone(self.equip.slots["chest"])
        self.assertIn(item, self.inv.items)

    def test_equip_item_offhand_travado_recusa_mesmo_via_chamada_direta(self):
        """Defesa em profundidade: cliente legítimo nem manda EQUIP_ITEM
        pro offhand com mão dupla equipada, mas um cliente forjado pode —
        o servidor tem que recusar sozinho, sem depender do guard do
        cliente."""
        axe = _item("Machado Grande", item_type="weapon", slot="mainhand",
                    armor_class="", two_handed=True)
        self.equip.slots["mainhand"] = axe
        shield = _item("Escudo", item_type="shield", slot="offhand", armor_class="")
        self.inv.items.append(shield)

        rejected = self.ws.equip_item_from_inventory("s1", 0)

        self.assertEqual(rejected, {"slot": "offhand", "item_name": "Escudo",
                                    "reason": "offhand_locked"})
        self.assertIsNone(self.equip.slots["offhand"])
        self.assertIn(shield, self.inv.items)

    def test_equip_item_inv_index_fora_do_alcance_e_no_op(self):
        rejected = self.ws.equip_item_from_inventory("s1", 99)
        self.assertIsNone(rejected)
        self.assertEqual(self.inv.items, [])
        self.assertTrue(all(v is None for v in self.equip.slots.values()))

    def test_equip_item_inv_index_negativo_e_no_op(self):
        item = _item("Peitoral de Placa")
        self.inv.items.append(item)
        rejected = self.ws.equip_item_from_inventory("s1", -1)
        self.assertIsNone(rejected)
        self.assertIn(item, self.inv.items)
        self.assertIsNone(self.equip.slots["chest"])

    def test_equip_item_slot_none_no_meio_da_bag_e_no_op(self):
        """Posição existe mas está vazia (None) — não deve levantar exceção
        nem equipar nada."""
        self.inv.items.append(None)
        rejected = self.ws.equip_item_from_inventory("s1", 0)
        self.assertIsNone(rejected)
        self.assertTrue(all(v is None for v in self.equip.slots.values()))


class TestUnequipItemSlot(unittest.TestCase):
    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.inv   = self.ws.world.get_component(self.eid, Inventory)
        self.equip = self.ws.world.get_component(self.eid, Equipment)

    def test_unequip_move_pro_fim_da_bag(self):
        item = _item("Peitoral de Placa")
        self.equip.slots["chest"] = item
        filler = _item("Enchimento", slot="head", armor_class="couro")
        self.inv.items.append(filler)

        self.ws.unequip_item_slot("s1", "chest")

        self.assertIsNone(self.equip.slots["chest"])
        self.assertEqual(self.inv.items, [filler, item])

    def test_unequip_slot_vazio_e_no_op(self):
        self.ws.unequip_item_slot("s1", "chest")
        self.assertEqual(self.inv.items, [])

    def test_unequip_bag_cheia_e_no_op(self):
        item = _item("Peitoral de Placa")
        self.equip.slots["chest"] = item
        self.inv.max_slots = 0

        self.ws.unequip_item_slot("s1", "chest")

        self.assertIs(self.equip.slots["chest"], item)
        self.assertEqual(self.inv.items, [])


class TestEquipItemProtocolDispatch(unittest.IsolatedAsyncioTestCase):
    """Fim-a-fim via protocolo real (encode/on_message), não chamada direta
    de método — prova que EQUIP_ITEM/UNEQUIP_ITEM estão amarrados certo no
    dispatch (server/session.py) e que EQUIP_REJECTED só sai quando deve."""

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_eq_a", 130, 374,
                                                  class_id="guerreiro")

    async def _send(self, msg_type, payload):
        from shared.messages import encode
        await self.mgr.on_message(self.session, encode(msg_type, payload))

    async def test_equip_item_sucesso_move_item_e_nao_rejeita(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        inv   = self.ws.world.get_component(self.session.entity_id, Inventory)
        equip = self.ws.world.get_component(self.session.entity_id, Equipment)
        item = _item("Peitoral de Placa")
        inv.items.append(item)
        self.fw.sent.clear()

        await self._send(MsgType.EQUIP_ITEM, {"inv_index": 0})

        self.assertIs(equip.slots["chest"], item)
        self.assertEqual(get_msgs_of_type(self.fw, MsgType.EQUIP_REJECTED), [])

    async def test_equip_item_recusado_manda_equip_rejected(self):
        from tests.test_session import get_msgs_of_type, fake_login
        from shared.messages import MsgType
        # Troca pra mago (só usa "tecido") e tenta equipar "placa".
        self.session, self.fw = await fake_login(
            self.mgr, "s2", "user_eq_b", 131, 374, class_id="mago")
        inv   = self.ws.world.get_component(self.session.entity_id, Inventory)
        equip = self.ws.world.get_component(self.session.entity_id, Equipment)
        item = _item("Peitoral de Placa")
        inv.items.append(item)
        self.fw.sent.clear()

        await self._send(MsgType.EQUIP_ITEM, {"inv_index": 0})

        rejected = get_msgs_of_type(self.fw, MsgType.EQUIP_REJECTED)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "class")
        self.assertIsNone(equip.slots["chest"])
        self.assertIn(item, inv.items)

    async def test_equip_item_inv_index_forjado_nao_quebra_nem_equipa(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        equip = self.ws.world.get_component(self.session.entity_id, Equipment)
        self.fw.sent.clear()

        await self._send(MsgType.EQUIP_ITEM, {"inv_index": 9999})

        self.assertTrue(all(v is None for v in equip.slots.values()))
        self.assertEqual(get_msgs_of_type(self.fw, MsgType.EQUIP_REJECTED), [])

    async def test_unequip_item_via_protocolo(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        inv   = self.ws.world.get_component(self.session.entity_id, Inventory)
        equip = self.ws.world.get_component(self.session.entity_id, Equipment)
        item = _item("Peitoral de Placa")
        equip.slots["chest"] = item
        self.fw.sent.clear()

        await self._send(MsgType.UNEQUIP_ITEM, {"slot": "chest"})

        self.assertIsNone(equip.slots["chest"])
        self.assertIn(item, inv.items)


if __name__ == "__main__":
    unittest.main(verbosity=2)
