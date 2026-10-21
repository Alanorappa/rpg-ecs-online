"""
tests/test_mail_system_compose.py — ui/mail_system.py (correio), telas de
compor e ler correspondência. Pedidos do usuário de 25/09/2026 mantidos:
clique direito (não esquerdo) devolve item do slot pra bolsa; Shift+clique
direito num item empilhável abre modal de quantidade; item reclamado some
do slot; ouro e item reclamam com qualquer um dos 2 botões. Visual no atlas
de UI desde 21/10/2026 (abas Entrada/Escrever): cliques agem no SOLTAR,
dentro de render() — os testes clicam sobre `ms._hit_rects[<id>]`.
Staging é 100% local, testável sem servidor/banco.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from engine.world import World
from engine.entity_factory import create_player, create_mailbox
from engine.components import Inventory
from ui.mail_system import MailSystem
from ui import atlas_window as aw
from content.item_table import ITEMS
from shared.messages import MsgType

_W, _H = 1280, 720


class _FakeNet:
    def __init__(self):
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


def _make_fixture():
    pygame.display.set_mode((_W, _H))
    world = World()
    player = create_player(world, 10, 10)
    create_mailbox(world, 11, 10, name="Caixa de Correio")
    ms = MailSystem(world, player, pygame.Surface((_W, _H)))
    ms.set_ui_scale(1.0)
    ms._net = _FakeNet()
    ui = ms._ui()
    ui.screen = "compose"
    return world, player, ms, ui


def _give_item(world, player, item_key, stack=1):
    item = ITEMS[item_key]()
    item.stack = stack
    world.get_component(player, Inventory).items.append(item)
    return item


def _frame(ms, events=(), pos=None, pressed=(False, False, False), shift=False):
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods
    try:
        if pos is not None:
            pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: pressed
        pygame.key.get_mods = lambda: pygame.KMOD_SHIFT if shift else 0
        ms.handle_events(list(events))
        ms.render()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods = orig


def _click(ms, hit_id, button=1, shift=False):
    _frame(ms)
    pos = ms._hit_rects[hit_id].center
    _frame(ms, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)], pos,
           (button == 1, False, button == 3), shift)
    _frame(ms, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)], pos, shift=shift)


def _spy_slots(ms):
    """Grava (id, ícone, quantidade, habilitado) de cada slot desenhado."""
    seen = {}
    orig = aw.Frame.slot

    def _spy(self, slot_id, x, y, icon=None, count=0, **kw):
        seen[slot_id] = (icon, count, kw.get("enabled", True))
        return orig(self, slot_id, x, y, icon, count, **kw)
    aw.Frame.slot = _spy
    try:
        _frame(ms)
    finally:
        aw.Frame.slot = orig
    return seen


class TestMailComposeSlotRemoval(unittest.TestCase):

    def test_clique_direito_no_slot_devolve_item_pra_bolsa(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        ui.compose_items = [(0, 1)]
        _click(ms, "mail:att:0", button=3)
        self.assertEqual(ui.compose_items, [])

    def test_clique_esquerdo_no_slot_nao_remove_mais(self):
        """Pedido do usuário: clique esquerdo deixou de remover — agora é
        só clique direito."""
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        ui.compose_items = [(0, 1)]
        _click(ms, "mail:att:0", button=1)
        self.assertEqual(ui.compose_items, [(0, 1)],
                         "clique esquerdo nao deveria mais remover o item do slot")


class TestMailComposeQuantityModal(unittest.TestCase):

    def test_shift_clique_direito_em_item_empilhavel_abre_modal_de_quantidade(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)
        _click(ms, "mail:bag:0", button=3, shift=True)
        self.assertIsNotNone(ms._compose_qty_modal)
        self.assertEqual(ms._compose_qty_modal["max_qty"], 5)
        self.assertEqual(ui.compose_items, [], "nao deveria anexar direto, so abrir o modal")

    def test_confirmar_modal_anexa_a_quantidade_escolhida(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)
        _click(ms, "mail:bag:0", button=3, shift=True)
        _click(ms, "qty:+")
        _click(ms, "qty:+")
        _click(ms, "qty:ok")
        self.assertEqual(ui.compose_items, [(0, 3)])
        self.assertIsNone(ms._compose_qty_modal, "modal deveria fechar apos confirmar")

    def test_slot_de_envio_mostra_a_quantidade_escolhida_nao_a_pilha_cheia(self):
        """Bug real relatado pelo usuário 25/09/2026 (2ª vez): escolher 2
        de uma pilha de 4 no modal continuava mostrando "4" no slot de
        envio."""
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 4)
        ui.compose_items = [(0, 2)]
        seen = _spy_slots(ms)
        self.assertEqual(seen["mail:att:0"][1], 2,
                         "slot deveria mostrar a quantidade ESCOLHIDA (2), nao a pilha real da bolsa (4)")
        self.assertEqual(world.get_component(player, Inventory).items[0].stack, 4,
                         "item de verdade na bolsa nao pode ter sido alterado so por desenhar o slot")

    def test_clique_direito_sem_shift_anexa_a_pilha_inteira_direto(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)
        _click(ms, "mail:bag:0", button=3)
        self.assertEqual(ui.compose_items, [(0, 5)])
        self.assertIsNone(ms._compose_qty_modal)

    def test_item_com_stack_1_nao_abre_modal_mesmo_com_shift(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        _click(ms, "mail:bag:0", button=3, shift=True)
        self.assertIsNone(ms._compose_qty_modal)
        self.assertEqual(ui.compose_items, [(0, 1)])


class TestMailComposeFieldFocused(unittest.TestCase):

    def test_clicar_no_campo_foca_e_clicar_fora_desfoca(self):
        world, player, ms, ui = _make_fixture()
        _click(ms, "mail:to")
        self.assertEqual(ui.compose_focus, "to")
        _click(ms, "mail:att:0")    # slot de anexo vazio — fora de qualquer campo
        self.assertEqual(ui.compose_focus, "")

    def test_compose_field_focused_reflete_o_campo_com_foco(self):
        world, player, ms, ui = _make_fixture()
        self.assertFalse(ms.compose_field_focused)
        ui.compose_focus = "to"
        self.assertTrue(ms.compose_field_focused)
        ui.compose_focus = ""
        self.assertFalse(ms.compose_field_focused)

    def test_fechar_o_correio_com_campo_focado_limpa_o_foco(self):
        """Bug real relatado pelo usuário 25/09/2026: fechar com campo
        focado deixava compose_focus preso, bloqueando WASD."""
        world, player, ms, ui = _make_fixture()
        ui.compose_focus = "to"
        ms._close()
        self.assertEqual(ui.compose_focus, "")
        self.assertFalse(ms.compose_field_focused)

    def test_voltar_pro_inbox_com_campo_focado_tambem_limpa_o_foco(self):
        world, player, ms, ui = _make_fixture()
        ui.compose_focus = "subject"
        ms._back_to_inbox()
        self.assertEqual(ui.compose_focus, "")
        self.assertFalse(ms.compose_field_focused)

    def test_compose_field_focused_e_false_fora_da_tela_de_compor(self):
        world, player, ms, ui = _make_fixture()
        ui.compose_focus = "to"
        ui.screen = "inbox"
        self.assertFalse(ms.compose_field_focused)

    def test_enviar_manda_mail_send(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        ui.compose_to = "Fulano"
        ui.compose_items = [(0, 1)]
        _click(ms, "mail:send")
        self.assertEqual(ms._net.sent[-1][0], MsgType.MAIL_SEND)
        self.assertEqual(ms._net.sent[-1][1]["to_name"], "Fulano")


class TestMailTabs(unittest.TestCase):

    def test_abas_trocam_entre_entrada_e_escrever(self):
        world, player, ms, ui = _make_fixture()
        _click(ms, "mail:tab:0")
        self.assertEqual(ui.screen, "inbox")
        _click(ms, "mail:tab:1")
        self.assertEqual(ui.screen, "compose")

    def test_clicar_numa_carta_pede_pra_abrir(self):
        world, player, ms, ui = _make_fixture()
        ui.screen = "inbox"
        ui.inbox = [{"mail_id": 9, "sender_name": "Fulano", "subject": "oi", "has_attachment": True}]
        _click(ms, "mail:open:0")
        self.assertEqual(ms._net.sent[-1], (MsgType.MAIL_OPEN_ITEM, {"mail_id": 9}))


class TestMailDetail(unittest.TestCase):

    def _detail(self, item=None, gold=50, claimed_first=False):
        world, player, ms, ui = _make_fixture()
        ui.screen = "detail"
        items = []
        if item is not None:
            items = [{"item_index": 0, "item": item, "claimed": claimed_first},
                     {"item_index": 1, "item": item, "claimed": False}]
        ui.detail = {"mail_id": 7, "sender_name": "Fulano", "subject": "oi", "body": "",
                     "items": items, "gold": gold, "gold_claimed": False}
        return ms, ui

    def test_item_reclamado_nao_e_mais_desenhado(self):
        """Pedido do usuário 25/09/2026: item reclamado deveria SUMIR do slot."""
        ms, ui = self._detail(item=ITEMS["hp_potion"](), gold=0, claimed_first=True)
        seen = _spy_slots(ms)
        self.assertIsNone(seen["mail:claim:0"][0], "item reclamado nao deveria ser desenhado")
        self.assertFalse(seen["mail:claim:0"][2])
        self.assertIsNotNone(seen["mail:claim:1"][0], "item nao reclamado continua visivel")

    def test_gold_reclama_com_os_dois_botoes(self):
        for button in (1, 3):
            ms, ui = self._detail(gold=50)
            _click(ms, "mail:gold", button=button)
            self.assertEqual(ms._net.sent, [(MsgType.MAIL_CLAIM_GOLD, {"mail_id": 7})], button)

    def test_item_reclama_com_os_dois_botoes(self):
        for button in (1, 3):
            ms, ui = self._detail(item=ITEMS["hp_potion"](), gold=0)
            _click(ms, "mail:claim:0", button=button)
            self.assertEqual(ms._net.sent, [(MsgType.MAIL_CLAIM_ITEM, {"mail_id": 7, "item_index": 0})],
                             button)

    def test_deletar_e_voltar(self):
        ms, ui = self._detail(gold=0)
        _click(ms, "mail:delete")
        self.assertEqual(ms._net.sent[-1], (MsgType.MAIL_DELETE, {"mail_id": 7}))
        self.assertEqual(ui.screen, "inbox")
        ms, ui = self._detail(gold=0)
        _click(ms, "mail:back")
        self.assertEqual(ui.screen, "inbox")

    def test_janela_na_area_segura(self):
        ms, ui = self._detail(gold=0)
        _frame(ms)
        self.assertTrue(aw.safe_area(_W, _H, _H).contains(ms._panel_rect))


if __name__ == "__main__":
    unittest.main(verbosity=2)
