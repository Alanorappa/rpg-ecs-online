"""
tests/test_mail.py — server/mail_processor.py + server/auth.py (correio,
25/09/2026). Cobre os pontos que mexem em item/gold real (mesma classe de
risco de loot/trade/quest reward, que já têm teste automatizado) — posse
real validada no Inventory/Wallet AO VIVO, ownership por char_id (nunca só
mail_id), e idempotência de claim (2ª tentativa do mesmo slot é recusada).

Usa 2 sessões reais (fake_login, banco real de dev) pra sender/recipient —
mesmo padrão de tests/test_quest_turn_in.py.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_session import make_session_manager, fake_login, get_msgs_of_type, _valid_char_name
from tests.helpers import AuthPoolCleanupMixin
from shared.messages import MsgType
from engine.components import Inventory, Wallet


def _give_item(ws, eid: int, item_key: str, stack: int = 1):
    from content.item_table import ITEMS
    inv = ws.world.get_component(eid, Inventory)
    item = ITEMS[item_key]()
    item.stack = stack
    inv.items.append(item)
    return item


class TestMailSend(AuthPoolCleanupMixin, unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def _two_sessions(self, suffix: str):
        sender, fw_s = await fake_login(self.mgr, f"mail_send_{suffix}", f"mailsendu{suffix}")
        recip,  fw_r = await fake_login(self.mgr, f"mail_recv_{suffix}", f"mailrecvu{suffix}")
        inv_s = self.ws.world.get_component(sender.entity_id, Inventory)
        inv_s.items = []
        fw_s.sent.clear()
        fw_r.sent.clear()
        return sender, fw_s, recip, fw_r

    async def test_envia_item_real_remove_do_inventory_do_remetente(self):
        sender, fw_s, recip, _fw_r = await self._two_sessions("a")
        item = _give_item(self.ws, sender.entity_id, "hp_potion", 1)
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "", "body": "",
            "items": [{"inv_index": 0}], "gold": 0,
        }, 0)

        results = get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])

        inv_s = self.ws.world.get_component(sender.entity_id, Inventory)
        self.assertEqual(len(inv_s.items), 0, "item nao saiu do inventory REAL do remetente")

        removed = get_msgs_of_type(fw_s, MsgType.INVENTORY_UPDATE)
        self.assertEqual(removed[0]["removed"],
                         [{"name": item.name, "item_id": item.item_id, "stack": 1}])

    async def test_envia_quantidade_parcial_de_uma_pilha_deixa_o_resto_na_bag(self):
        """Bug real relatado pelo usuário 25/09/2026: escolher 2 de uma
        pilha de 4 no modal de quantidade (Shift+clique direito) mandava
        os 4 mesmo assim — client nunca incluía "quantity" no payload E
        o servidor sempre usava o stack INTEIRO, ignorando qualquer
        quantidade pedida. Mesma lógica de fatiar stack que trade já usa
        (trade_processor.py::add_trade_item)."""
        sender, fw_s, recip, _fw_r = await self._two_sessions("q")
        item = _give_item(self.ws, sender.entity_id, "hp_potion", 4)
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "", "body": "",
            "items": [{"inv_index": 0, "quantity": 2}], "gold": 0,
        }, 0)

        self.assertTrue(get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]["success"])
        inv_s = self.ws.world.get_component(sender.entity_id, Inventory)
        self.assertEqual(len(inv_s.items), 1, "item nao deveria ter sumido da bag, so a pilha diminuido")
        self.assertEqual(inv_s.items[0].stack, 2, "deveriam sobrar 2 na bag do remetente")

        from server import auth
        inbox = await auth.list_inbox(recip.char_data["id"])
        detail = await auth.get_mail_detail(inbox[0]["mail_id"], recip.char_data["id"])
        self.assertEqual(detail["items_json"], [[item.item_id, 2]],
                         "a correspondencia deveria carregar so os 2 escolhidos, nao os 4")

    async def test_quantity_maior_que_o_stack_real_e_clampada(self):
        """Cliente adulterado/dessincronizado pedindo mais do que existe
        de verdade — servidor nunca confia, clampa pro stack real."""
        sender, fw_s, recip, _fw_r = await self._two_sessions("r")
        _give_item(self.ws, sender.entity_id, "hp_potion", 3)
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "", "body": "",
            "items": [{"inv_index": 0, "quantity": 999}], "gold": 0,
        }, 0)

        self.assertTrue(get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]["success"])
        inv_s = self.ws.world.get_component(sender.entity_id, Inventory)
        self.assertEqual(len(inv_s.items), 0, "pediu 999 mas so tinha 3 - manda os 3 e some da bag")

    async def test_envia_gold_real_debita_da_wallet_do_remetente(self):
        sender, fw_s, recip, _fw_r = await self._two_sessions("b")
        wallet = self.ws.world.get_component(sender.entity_id, Wallet)
        wallet.gold = 500
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "presente", "body": "",
            "items": [], "gold": 300,
        }, 0)

        self.assertTrue(get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]["success"])
        self.assertEqual(wallet.gold, 200)

    async def test_recusa_item_que_o_remetente_nao_tem(self):
        """Índice de inventário inválido — nada sai do inventory (tudo-ou-nada,
        mesmo princípio de process_shop_buy)."""
        sender, fw_s, recip, _fw_r = await self._two_sessions("c")
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "", "body": "",
            "items": [{"inv_index": 0}], "gold": 0,
        }, 0)

        result = get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "item_invalido")

    async def test_recusa_gold_maior_que_a_wallet_do_remetente(self):
        sender, fw_s, recip, _fw_r = await self._two_sessions("d")
        wallet = self.ws.world.get_component(sender.entity_id, Wallet)
        wallet.gold = 10
        recip_name = recip.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip_name, "subject": "", "body": "",
            "items": [], "gold": 999,
        }, 0)

        result = get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "ouro_insuficiente")
        self.assertEqual(wallet.gold, 10, "gold nao pode ter sido debitado numa tentativa recusada")

    async def test_recusa_destinatario_inexistente(self):
        sender, fw_s, _recip, _fw_r = await self._two_sessions("e")
        _give_item(self.ws, sender.entity_id, "hp_potion", 1)

        await self.mgr._handle_mail_send(sender, {
            "to_name": "NomeQueNaoExisteDeVerdade999", "subject": "", "body": "",
            "items": [{"inv_index": 0}], "gold": 0,
        }, 0)

        result = get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "destinatario_nao_encontrado")
        inv_s = self.ws.world.get_component(sender.entity_id, Inventory)
        self.assertEqual(len(inv_s.items), 1, "item nao pode ter saido numa tentativa recusada")

    async def test_recusa_mandar_para_si_mesmo(self):
        sender, fw_s, _recip, _fw_r = await self._two_sessions("f")
        own_name = sender.char_data["name"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": own_name, "subject": "", "body": "", "items": [], "gold": 50,
        }, 0)

        result = get_msgs_of_type(fw_s, MsgType.MAIL_SEND_RESULT)[0]
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "nao_pode_mandar_pra_si_mesmo")

    async def test_assunto_vazio_autopreenche_com_nome_do_primeiro_item_no_servidor(self):
        """Regra aplicada no SERVIDOR também (não só client-side pre-fill) —
        nunca depender de um cliente modificado preencher certo."""
        sender, fw_s, recip, _fw_r = await self._two_sessions("g")
        _give_item(self.ws, sender.entity_id, "hp_potion", 1)
        recip_char_id = recip.char_data["id"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip.char_data["name"], "subject": "", "body": "",
            "items": [{"inv_index": 0}], "gold": 0,
        }, 0)

        from server import auth
        inbox = await auth.list_inbox(recip_char_id)
        self.assertEqual(inbox[0]["subject"], "Poção de Vida")

    async def test_assunto_vazio_autopreenche_com_valor_de_gold_quando_so_gold(self):
        sender, fw_s, recip, _fw_r = await self._two_sessions("h")
        wallet = self.ws.world.get_component(sender.entity_id, Wallet)
        wallet.gold = 777
        recip_char_id = recip.char_data["id"]

        await self.mgr._handle_mail_send(sender, {
            "to_name": recip.char_data["name"], "subject": "", "body": "",
            "items": [], "gold": 777,
        }, 0)

        from server import auth
        inbox = await auth.list_inbox(recip_char_id)
        self.assertEqual(inbox[0]["subject"], "777")


class TestMailClaimAndOwnership(AuthPoolCleanupMixin, unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def _mail_from_a_to_b(self, suffix: str, item_key: str = "hp_potion", gold: int = 50):
        sender, fw_s = await fake_login(self.mgr, f"mail_claim_a_{suffix}", f"mailclaimau{suffix}")
        recip,  fw_r = await fake_login(self.mgr, f"mail_claim_b_{suffix}", f"mailclaimbu{suffix}")
        # Contas reusadas entre execuções da suíte (banco dev real
        # compartilhado) — zera a bag dos 2 lados antes do teste, mesma
        # ressalva de tests/test_quest_turn_in.py::_ready_session.
        self.ws.world.get_component(sender.entity_id, Inventory).items = []
        self.ws.world.get_component(recip.entity_id, Inventory).items  = []
        _give_item(self.ws, sender.entity_id, item_key, 1)
        wallet = self.ws.world.get_component(sender.entity_id, Wallet)
        wallet.gold = gold
        await self.mgr._handle_mail_send(sender, {
            "to_name": recip.char_data["name"], "subject": "presente", "body": "oi",
            "items": [{"inv_index": 0}], "gold": gold,
        }, 0)
        from server import auth
        inbox = await auth.list_inbox(recip.char_data["id"])
        mail_id = inbox[0]["mail_id"]
        fw_r.sent.clear()
        return sender, recip, fw_r, mail_id

    async def test_claim_item_concede_de_verdade_no_inventory_do_destinatario(self):
        _sender, recip, fw_r, mail_id = await self._mail_from_a_to_b("i")

        await self.mgr._handle_mail_claim_item(recip, {"mail_id": mail_id, "item_index": 0}, 0)

        result = get_msgs_of_type(fw_r, MsgType.MAIL_CLAIM_RESULT)[0]
        self.assertTrue(result["success"])
        inv_r = self.ws.world.get_component(recip.entity_id, Inventory)
        self.assertEqual(len(inv_r.items), 1)
        self.assertEqual(inv_r.items[0].item_id, "hp_potion")
        inv_update = get_msgs_of_type(fw_r, MsgType.INVENTORY_UPDATE)[0]
        self.assertEqual(len(inv_update["items"]), 1)

    async def test_claim_item_atualiza_o_indicador_de_correspondencia_nao_lida(self):
        """Bug real relatado pelo usuário 25/09/2026: a exclamação sobre
        a caixa de correio só sumia relogando — reclamar item nunca
        recalculava o indicador (só o login fazia isso). `mail_unread`
        chega via STATS_UPDATE (buffer, drenado por
        WorldServer.consume_stats_updates — mesmo canal do gold).

        Compara ANTES/DEPOIS (nunca um valor absoluto) — a conta de
        teste é reusada entre execuções da suíte (banco dev real
        compartilhado), então pode ter correspondência residual de
        rodadas anteriores contando pro total."""
        from server import auth
        _sender, recip, _fw_r, mail_id = await self._mail_from_a_to_b("iu", gold=0)
        before = await auth.count_unread_mail(recip.char_data["id"])

        await self.mgr._handle_mail_claim_item(recip, {"mail_id": mail_id, "item_index": 0}, 0)

        updates = [u for u in self.ws.consume_stats_updates()
                  if u.get("player_eid") == recip.entity_id and "mail_unread" in u]
        self.assertTrue(updates, "deveria ter recalculado mail_unread apos reclamar o item")
        self.assertEqual(updates[-1]["mail_unread"], before - 1,
                         "reclamar o unico anexo (sem gold) deveria reduzir o indicador em 1")

    async def test_claim_gold_atualiza_o_indicador_de_correspondencia_nao_lida(self):
        from server import auth
        _sender, recip, _fw_r, mail_id = await self._mail_from_a_to_b("gu")
        await self.mgr._handle_mail_claim_item(recip, {"mail_id": mail_id, "item_index": 0}, 0)
        self.ws.consume_stats_updates()   # limpa o que o claim de item ja gerou
        before = await auth.count_unread_mail(recip.char_data["id"])

        await self.mgr._handle_mail_claim_gold(recip, {"mail_id": mail_id}, 0)

        updates = [u for u in self.ws.consume_stats_updates()
                  if u.get("player_eid") == recip.entity_id and "mail_unread" in u]
        self.assertTrue(updates, "deveria ter recalculado mail_unread apos reclamar o gold")
        self.assertEqual(updates[-1]["mail_unread"], before - 1,
                         "item ja reclamado, reclamar o gold tambem deveria reduzir o indicador em 1")

    async def test_deletar_mail_atualiza_o_indicador_de_correspondencia_nao_lida(self):
        """Deletar uma correspondência AINDA não reclamada também precisa
        recalcular — some da inbox, então não pode continuar contando."""
        from server import auth
        _sender, recip, _fw_r, mail_id = await self._mail_from_a_to_b("du")
        before = await auth.count_unread_mail(recip.char_data["id"])

        await self.mgr._handle_mail_delete(recip, {"mail_id": mail_id}, 0)

        updates = [u for u in self.ws.consume_stats_updates()
                  if u.get("player_eid") == recip.entity_id and "mail_unread" in u]
        self.assertTrue(updates, "deveria ter recalculado mail_unread apos deletar")
        self.assertEqual(updates[-1]["mail_unread"], before - 1,
                         "deletar a unica correspondencia nao-reclamada deveria reduzir o indicador em 1")

    async def test_claim_item_duas_vezes_a_segunda_e_recusada_idempotente(self):
        _sender, recip, fw_r, mail_id = await self._mail_from_a_to_b("j")

        await self.mgr._handle_mail_claim_item(recip, {"mail_id": mail_id, "item_index": 0}, 0)
        fw_r.sent.clear()
        await self.mgr._handle_mail_claim_item(recip, {"mail_id": mail_id, "item_index": 0}, 0)

        result = get_msgs_of_type(fw_r, MsgType.MAIL_CLAIM_RESULT)[0]
        self.assertFalse(result["success"])
        inv_r = self.ws.world.get_component(recip.entity_id, Inventory)
        self.assertEqual(len(inv_r.items), 1, "2a tentativa nao pode duplicar o item")

    async def test_claim_gold_credita_de_verdade_na_wallet_do_destinatario(self):
        _sender, recip, fw_r, mail_id = await self._mail_from_a_to_b("k", gold=123)
        wallet_r = self.ws.world.get_component(recip.entity_id, Wallet)
        wallet_r.gold = 0

        await self.mgr._handle_mail_claim_gold(recip, {"mail_id": mail_id}, 0)

        result = get_msgs_of_type(fw_r, MsgType.MAIL_CLAIM_RESULT)[0]
        self.assertTrue(result["success"])
        self.assertEqual(wallet_r.gold, 123)

    async def test_claim_gold_duas_vezes_a_segunda_e_recusada_idempotente(self):
        _sender, recip, fw_r, mail_id = await self._mail_from_a_to_b("l", gold=99)
        wallet_r = self.ws.world.get_component(recip.entity_id, Wallet)
        wallet_r.gold = 0

        await self.mgr._handle_mail_claim_gold(recip, {"mail_id": mail_id}, 0)
        fw_r.sent.clear()
        await self.mgr._handle_mail_claim_gold(recip, {"mail_id": mail_id}, 0)

        result = get_msgs_of_type(fw_r, MsgType.MAIL_CLAIM_RESULT)[0]
        self.assertFalse(result["success"])
        self.assertEqual(wallet_r.gold, 99, "2a tentativa nao pode creditar de novo")

    async def test_outro_player_nao_consegue_reclamar_mail_alheio_adivinhando_o_id(self):
        """Ownership por char_id, nunca só mail_id — mesmo princípio de
        get_mail_detail/claim_mail_item em server/auth.py."""
        _sender, _recip, _fw_r, mail_id = await self._mail_from_a_to_b("m")
        intruder, fw_i = await fake_login(self.mgr, "mail_intruder_m", "mailintruderm")
        fw_i.sent.clear()

        await self.mgr._handle_mail_claim_item(intruder, {"mail_id": mail_id, "item_index": 0}, 0)
        result = get_msgs_of_type(fw_i, MsgType.MAIL_CLAIM_RESULT)[0]
        self.assertFalse(result["success"])
        inv_i = self.ws.world.get_component(intruder.entity_id, Inventory)
        self.assertEqual(len(inv_i.items), 0)

    async def test_outro_player_nao_consegue_abrir_mail_alheio(self):
        _sender, _recip, _fw_r, mail_id = await self._mail_from_a_to_b("n")
        intruder, fw_i = await fake_login(self.mgr, "mail_intruder_n", "mailintrudern")
        fw_i.sent.clear()

        await self.mgr._handle_mail_open_item(intruder, {"mail_id": mail_id}, 0)
        self.assertEqual(get_msgs_of_type(fw_i, MsgType.MAIL_ITEM_DETAIL), [],
                         "intruso nunca deveria receber o detalhe de correspondencia alheia")

    async def test_outro_player_nao_consegue_deletar_mail_alheio(self):
        """Checa por PRESENÇA do mail_id, não por tamanho absoluto do inbox
        — a conta de teste do destinatário é reusada entre execuções da
        suíte (banco dev real compartilhado, mesma ressalva já documentada
        em tests/test_quest_turn_in.py::_ready_session), então pode ter
        correspondência residual de rodadas anteriores."""
        _sender, recip, _fw_r, mail_id = await self._mail_from_a_to_b("o")
        intruder, fw_i = await fake_login(self.mgr, "mail_intruder_o", "mailintrudero")
        fw_i.sent.clear()

        await self.mgr._handle_mail_delete(intruder, {"mail_id": mail_id}, 0)

        from server import auth
        inbox = await auth.list_inbox(recip.char_data["id"])
        ids = {m["mail_id"] for m in inbox}
        self.assertIn(mail_id, ids, "mail alheio nao pode ter sido deletado")

    async def test_deletar_mail_proprio_remove_da_inbox(self):
        _sender, recip, fw_r, mail_id = await self._mail_from_a_to_b("p")

        await self.mgr._handle_mail_delete(recip, {"mail_id": mail_id}, 0)

        from server import auth
        inbox = await auth.list_inbox(recip.char_data["id"])
        ids = {m["mail_id"] for m in inbox}
        self.assertNotIn(mail_id, ids)
        # resposta é um MAIL_INBOX_LIST atualizado (sem mensagem própria de delete)
        inbox_msgs = get_msgs_of_type(fw_r, MsgType.MAIL_INBOX_LIST)
        self.assertNotIn(mail_id, {m["mail_id"] for m in inbox_msgs[-1]["mails"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
