"""
tests/test_shop_atlas_ui.py — loja no atlas de UI (ui/shop_system.py).
Clique DIREITO compra/vende no SOLTAR; Shift+direito abre o modal de
quantidade (peça do kit: atlas_window.quantity_window). Mouse falso.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.components import (CharacterStats, Equipment, Inventory, Wallet, Merchant,
                               PlayerControlled)
from ui.ui_components import ShopUIState
from ui.shop_system import ShopSystem
from ui import atlas_window as aw

_W, _H = 1280, 720


@pytest.fixture(autouse=True)
def _display():
    prev = pygame.display.get_surface()
    prev_size = prev.get_size() if prev is not None else None
    pygame.display.set_mode((_W, _H))
    yield
    if prev_size is not None:
        pygame.display.set_mode(prev_size)


class _Net:
    def __init__(self):
        self.sent = []

    def send(self, mt, p):
        self.sent.append((mt, p))


def _make_item(key):
    from engine.quest_logic import resolve_reward_item_factory
    return resolve_reward_item_factory(key)()


def _shop(gold=10_000):
    world = World()
    player = world.create_entity()
    for c in (CharacterStats(name="T", class_id="guerreiro"), Equipment(), Inventory(),
              Wallet(gold=gold), PlayerControlled(), ShopUIState()):
        world.add_component(player, c)
    merchant = world.create_entity()
    world.add_component(merchant, Merchant(shop_id="general"))
    sh = ShopSystem(world, player, pygame.Surface((_W, _H)))
    sh.set_ui_scale(1.0)
    sh._net = _Net()
    sh.open_for(merchant)
    sh._open_cooldown = 0
    sh._net.sent.clear()   # SHOP_CLOSE de reset do Desfazer ao abrir
    return sh


def _frame(sh, events=(), pos=None, pressed=(False, False, False), shift=False):
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods
    try:
        if pos is not None:
            pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: pressed
        pygame.key.get_mods = lambda: pygame.KMOD_SHIFT if shift else 0
        sh.handle_events(list(events))
        sh.render()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods = orig


def _click(sh, hit_id, button=1, shift=False):
    _frame(sh)
    pos = sh._hit_rects[hit_id].center
    down = (button == 1, False, button == 3)
    _frame(sh, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)], pos, down, shift)
    _frame(sh, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)], pos, shift=shift)


def test_direito_no_item_a_venda_compra():
    sh = _shop()
    _click(sh, "shop:buy:0", button=3)
    assert len(sh._net.sent) == 1
    assert sh._net.sent[0][1]["quantity"] == 1


def test_esquerdo_nao_compra():
    sh = _shop()
    _click(sh, "shop:buy:0", button=1)
    assert sh._net.sent == []


def test_direito_na_mochila_vende():
    sh = _shop()
    inv = sh.world.get_component(sh.player_entity, Inventory)
    inv.items.append(_make_item("training_sword"))
    _click(sh, "shop:sell:0", button=3)
    assert len(sh._net.sent) == 1 and inv.items == []


def test_shift_direito_em_empilhavel_abre_quantidade_e_confirma():
    sh = _shop()
    from content.merchant_data import SHOPS
    stock = SHOPS["general"]["stock"]
    idx = next(i for i, e in enumerate(stock) if e["factory"]().max_stack > 1)
    _click(sh, f"shop:buy:{idx}", button=3, shift=True)
    assert sh._qty_modal is not None and sh._net.sent == []
    _click(sh, "qty:+")
    assert sh._qty_modal["qty"] == 2
    _click(sh, "qty:ok")
    assert sh._qty_modal is None
    assert sh._net.sent[0][1]["quantity"] == 2


def test_x_fecha():
    sh = _shop()
    _click(sh, "shop:close")
    assert not sh.is_open


def test_janela_na_area_segura():
    sh = _shop()
    _frame(sh)
    SW, SH = _W, _H
    assert aw.safe_area(SW, SH, SH).contains(sh._panel_rect)


def test_desfazer_manda_pro_servidor_e_fechar_esquece():
    from shared.messages import MsgType
    sh = _shop()
    _frame(sh)
    assert not sh.transaction_history          # nada pra desfazer: botão desabilitado
    _click(sh, "shop:undo")
    assert sh._net.sent == []
    sh.note_transaction("buy")
    _click(sh, "shop:undo")
    assert sh._net.sent == [(MsgType.SHOP_UNDO, {"npc_eid": sh.open_merchant_id})]
    sh.on_undo_result(0)
    assert sh.transaction_history == []
    sh.note_transaction("sell")
    sh._net.sent.clear()
    _click(sh, "shop:close")
    assert sh.transaction_history == [] and sh._net.sent == [(MsgType.SHOP_CLOSE, {})]


def test_saldo_no_rodape():
    sh = _shop(gold=60)
    texts = []
    orig = aw.Frame.text
    aw.Frame.text = lambda self, t, *a, **k: (texts.append(t), orig(self, t, *a, **k))
    try:
        _frame(sh)
    finally:
        aw.Frame.text = orig
    assert "Saldo: 60g" in texts
