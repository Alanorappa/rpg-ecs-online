"""
tests/test_mail_system_compose.py — ui/mail_system.py, tela de compor
correspondência (25/09/2026, pedidos do usuário após 1ª rodada de teste
em jogo): clique direito (não esquerdo) devolve item do slot pra bolsa;
Shift+clique direito num item empilhável abre modal de quantidade
(mesmo padrão de ShopSystem/trade). Staging é 100% local (nenhuma
mensagem de rede envolvida), então testável sem servidor/banco.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
_screen = pygame.display.set_mode((1280, 720))

from engine.world import World
from engine.entity_factory import create_player, create_mailbox
from engine.components import Inventory
from ui.mail_system import MailSystem
from content.item_table import ITEMS


def _make_fixture():
    world = World()
    player = create_player(world, 10, 10)
    create_mailbox(world, 11, 10, name="Caixa de Correio")
    ms = MailSystem(world, player, _screen)
    ui = ms._ui()
    ui.screen = "compose"
    return world, player, ms, ui


def _give_item(world, player, item_key, stack=1):
    item = ITEMS[item_key]()
    item.stack = stack
    world.get_component(player, Inventory).items.append(item)
    return item


def _click(ms, layout_key, button=3, shift=False):
    L = ms._compose_layout()
    r = L[layout_key]
    center = r.center if hasattr(r, "center") else (r[0].centerx, r[0].centery)
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=center)
    orig_mods = pygame.key.get_mods
    if shift:
        pygame.key.get_mods = lambda: pygame.KMOD_SHIFT
    try:
        ms._handle_compose_click(ms._ui(), event)
    finally:
        pygame.key.get_mods = orig_mods


def _click_bag_row(ms, row_index, button=3, shift=False):
    L = ms._compose_layout()
    row_h = L["bag_row_h"]
    pos = (L["bag"].x + 10, L["bag"].y + row_index * row_h + row_h // 2)
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)
    orig_mods = pygame.key.get_mods
    if shift:
        pygame.key.get_mods = lambda: pygame.KMOD_SHIFT
    try:
        ms._handle_compose_click(ms._ui(), event)
    finally:
        pygame.key.get_mods = orig_mods


class TestMailComposeSlotRemoval(unittest.TestCase):

    def test_clique_direito_no_slot_devolve_item_pra_bolsa(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        ui.compose_items = [(0, 1)]

        L = ms._compose_layout()
        slot_center = L["slots"][0].center
        event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=slot_center)
        ms._handle_compose_click(ui, event)

        self.assertEqual(ui.compose_items, [])

    def test_clique_esquerdo_no_slot_nao_remove_mais(self):
        """Pedido do usuário: clique esquerdo deixou de remover — agora é
        só clique direito."""
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)
        ui.compose_items = [(0, 1)]

        L = ms._compose_layout()
        slot_center = L["slots"][0].center
        event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=slot_center)
        ms._handle_compose_click(ui, event)

        self.assertEqual(ui.compose_items, [(0, 1)],
                         "clique esquerdo nao deveria mais remover o item do slot")


class TestMailComposeQuantityModal(unittest.TestCase):

    def test_shift_clique_direito_em_item_empilhavel_abre_modal_de_quantidade(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)

        _click_bag_row(ms, 0, button=3, shift=True)

        self.assertIsNotNone(ms._compose_qty_modal)
        self.assertEqual(ms._compose_qty_modal["max_qty"], 5)
        self.assertEqual(ui.compose_items, [], "nao deveria anexar direto, so abrir o modal")

    def test_confirmar_modal_anexa_a_quantidade_escolhida(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)
        _click_bag_row(ms, 0, button=3, shift=True)
        ms._compose_qty_modal["qty"] = 3

        ms._confirm_compose_qty_modal(ui, ms._compose_qty_modal)

        self.assertEqual(ui.compose_items, [(0, 3)])
        self.assertIsNone(ms._compose_qty_modal, "modal deveria fechar apos confirmar")

    def test_clique_direito_sem_shift_anexa_a_pilha_inteira_direto(self):
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 5)

        _click_bag_row(ms, 0, button=3, shift=False)

        self.assertEqual(ui.compose_items, [(0, 5)])
        self.assertIsNone(ms._compose_qty_modal)

    def test_item_com_stack_1_nao_abre_modal_mesmo_com_shift(self):
        """Sem stack pra fatiar, Shift+clique direito se comporta igual
        clique direito normal (mesma regra de ShopSystem)."""
        world, player, ms, ui = _make_fixture()
        _give_item(world, player, "hp_potion", 1)

        _click_bag_row(ms, 0, button=3, shift=True)

        self.assertIsNone(ms._compose_qty_modal)
        self.assertEqual(ui.compose_items, [(0, 1)])


class TestMailComposeFieldFocused(unittest.TestCase):

    def test_compose_field_focused_reflete_o_campo_com_foco(self):
        world, player, ms, ui = _make_fixture()
        self.assertFalse(ms.compose_field_focused)
        ui.compose_focus = "to"
        self.assertTrue(ms.compose_field_focused)
        ui.compose_focus = ""
        self.assertFalse(ms.compose_field_focused)

    def test_fechar_o_correio_com_campo_focado_limpa_o_foco(self):
        """Bug real relatado pelo usuário 25/09/2026: fechar o modal do
        correio com um campo de texto ainda focado deixava
        compose_focus preso, bloqueando WASD pra sempre (o personagem
        ficava incontrolável mesmo com o correio já fechado)."""
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
        """Defesa extra: mesmo se compose_focus ficasse preso por algum
        caminho não previsto, sair da tela "compose" já basta pra
        destravar o teclado."""
        world, player, ms, ui = _make_fixture()
        ui.compose_focus = "to"
        ui.screen = "inbox"

        self.assertFalse(ms.compose_field_focused)


class TestMailDetailClaimedItemDisappears(unittest.TestCase):

    def test_item_reclamado_nao_e_mais_desenhado(self):
        """Pedido do usuário 25/09/2026: item reclamado deveria SUMIR do
        slot, não só ficar escurecido. Chama _render_detail DE VERDADE
        (não uma reprodução da lógica) e espiona o que _draw_item_slot
        recebe como `item` pra cada slot."""
        world, player, ms, ui = _make_fixture()
        ui.screen = "detail"
        fake_item = ITEMS["hp_potion"]()
        ui.detail = {
            "mail_id": 1, "sender_name": "Fulano", "subject": "oi", "body": "",
            "items": [{"item_index": 0, "item": fake_item, "claimed": True},
                     {"item_index": 1, "item": fake_item, "claimed": False}],
            "gold": 0, "gold_claimed": False,
        }
        seen = []
        orig = ms._draw_item_slot
        def _spy(r, item, hovered, hint=""):
            seen.append(item)
            return orig(r, item, hovered, hint)
        ms._draw_item_slot = _spy

        ms._render_detail(ui)

        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[0], "item reclamado (item_index=0) nao deveria ser desenhado")
        self.assertIs(seen[1], fake_item, "item nao reclamado (item_index=1) continua visivel")


if __name__ == "__main__":
    unittest.main(verbosity=2)
