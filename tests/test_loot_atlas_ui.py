"""
tests/test_loot_atlas_ui.py — modal de loot no atlas de UI
(ui/loot_system.py). Esquerdo pega, direito equipa, X fecha — no SOLTAR,
dentro de render(); clique fora do modal fecha (no apertar, em update).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.entity_factory import create_corpse
from engine.components import Wallet, Inventory, PlayerControlled, Corpse
from ui.ui_components import LootUIState
from ui.loot_system import LootSystem

_W, _H = 1280, 720


@pytest.fixture(autouse=True)
def _display():
    prev = pygame.display.get_surface()
    prev_size = prev.get_size() if prev is not None else None
    pygame.display.set_mode((_W, _H))
    yield
    if prev_size is not None:
        pygame.display.set_mode(prev_size)


def _make_item(key):
    from engine.quest_logic import resolve_reward_item_factory
    return resolve_reward_item_factory(key)()


def _loot(n_items=2, coins=11):
    world = World()
    player = world.create_entity()
    for c in (PlayerControlled(), Wallet(gold=0), Inventory(), LootUIState()):
        world.add_component(player, c)
    items = [_make_item("hp_potion") for _ in range(n_items)]
    corpse = create_corpse(world, 100, 100, items, coins=coins)
    ls = LootSystem(world, pygame.Surface((_W, _H)), player_entity=player)
    ls.set_ui_scale(1.0)
    ls._open_modal(corpse, 400, 300)
    sent = []
    ls.set_online_loot_requester(lambda eid, take, item_id: sent.append((take, item_id)))
    return ls, corpse, sent


def _frame(ls, events=(), pos=None, pressed=(False, False, False)):
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        if pos is not None:
            pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: pressed
        ls.update(list(events))
        ls.render()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig


def _click(ls, hit_id, button=1):
    _frame(ls)
    pos = ls._hit_rects[hit_id].center
    _frame(ls, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)], pos,
           (button == 1, False, button == 3))
    _frame(ls, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)], pos)


def test_esquerdo_no_ouro_e_no_item_pede_ao_servidor():
    ls, corpse, sent = _loot()
    _click(ls, "loot:0")
    assert sent == [("gold", "")]


def test_esquerdo_no_item_pede_item_id():
    ls, corpse, sent = _loot(coins=0)
    item = ls.world.get_component(corpse, Corpse).loot[0]
    _click(ls, "loot:0")
    assert sent == [("item", item.item_id)]


def test_direito_no_item_tambem_pede_ao_servidor():
    """Online, equipar direto do loot vira pedido normal de item (sem
    creditar da cópia local) — mesma proteção de antes."""
    ls, corpse, sent = _loot(coins=0)
    _click(ls, "loot:0", button=3)
    assert sent and sent[0][0] == "item"


def test_x_fecha():
    ls, corpse, sent = _loot()
    _click(ls, "loot:close")
    assert ls.open_corpse_id == -1 and sent == []


def test_clique_fora_fecha():
    ls, corpse, sent = _loot()
    _frame(ls)
    _frame(ls, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(5, 5))], (5, 5))
    assert ls.open_corpse_id == -1


def test_muitos_itens_rolam_e_modal_fica_na_tela():
    ls, corpse, sent = _loot(n_items=9)
    _frame(ls)
    assert "loot:9" not in ls._hit_rects        # fora da área visível
    assert pygame.Rect(0, 0, _W, _H).contains(ls._modal_screen_rect)
