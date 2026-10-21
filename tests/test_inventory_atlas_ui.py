"""
tests/test_inventory_atlas_ui.py — painel de inventário no atlas de UI
(client/inventory_handlers.py::_draw_inventory_panel). Ações no SOLTAR
do mouse (esquerdo e direito), dentro do desenho — `_handle_inventory_click`
só segura o clique. Mouse falso (driver dummy não move o cursor).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.components import (CharacterStats, CombatStats, Equipment, Inventory,
                               Wallet, PlayerControlled)
from client.inventory_handlers import InventoryHandlers
from ui import atlas_window as aw
from ui.ui_components import DragState
from ui.fonts import make as _font

_W, _H = 1280, 720
T, S = aw.T, aw.S


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


class _Fx(InventoryHandlers):
    def __init__(self, font_size=24):
        self.screen = pygame.Surface((_W, _H))
        self.font_md, self.font_sm, self.font_xs = _font(font_size), _font(int(font_size * .8)), _font(15)
        self.world = World()
        self.player_entity = self.world.create_entity()
        self.world.add_component(self.player_entity, CharacterStats(name="Teste", class_id="guerreiro"))
        self.world.add_component(self.player_entity, CombatStats())
        self.world.add_component(self.player_entity, Equipment())
        self.world.add_component(self.player_entity, Inventory())
        self.world.add_component(self.player_entity, Wallet(gold=123))
        self.world.add_component(self.player_entity, PlayerControlled())
        self._ui_events = []
        self._inv_press = aw.PressState()
        self._selected_inv_idx = -1
        self._show_inventory = True
        self._pending_tooltip = None
        self._drag = DragState()
        self.calls = []

    def _mm_safe_area(self):
        return aw.safe_area(_W, _H, _H - 80)

    def _get_drag(self):
        return self._drag

    def _get_trade_ui(self):
        return None

    def _try_open_item_quest_dialog(self, item):
        return False

    def _equip_item(self, item):
        self.calls.append(("equip", item.name))

    def _unequip_slot(self, slot):
        self.calls.append(("unequip", slot))

    def _use_consumable(self, item, idx, inv):
        self.calls.append(("use", idx))

    @property
    def inv(self):
        return self.world.get_component(self.player_entity, Inventory)

    @property
    def equip(self):
        return self.world.get_component(self.player_entity, Equipment)

    def draw(self, events=()):
        self._ui_events = list(events)
        self._pending_tooltip = None
        self._draw_inventory_panel()

    def origin(self):
        self.draw()
        r = self._inv_panel_rect
        return r.x, r.y

    def screen_pos(self, raw_x, raw_y):
        ox, oy = self.origin()
        return ox + int(raw_x * S), oy + int(raw_y * S)

    def click(self, raw_pos, button=1):
        pos = self.screen_pos(*raw_pos)
        orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
        try:
            pygame.mouse.get_pos = lambda: pos
            pressed = (button == 1, False, button == 3)
            pygame.mouse.get_pressed = lambda num_buttons=3: pressed
            self.draw([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)])
            pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
            self.draw([pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)])
        finally:
            pygame.mouse.get_pos, pygame.mouse.get_pressed = orig

    def hover(self, raw_pos):
        pos = self.screen_pos(*raw_pos)
        orig = pygame.mouse.get_pos
        try:
            pygame.mouse.get_pos = lambda: pos
            self.draw()
        finally:
            pygame.mouse.get_pos = orig


def _bag_cell(fx, i):
    tw = fx._inv_layout(fx._inv_stat_rows(fx.world.get_component(fx.player_entity, CombatStats),
                                          fx.world.get_component(fx.player_entity, CharacterStats)))["tw"]
    col0 = tw - 1 - fx._INV_BAG_COLS
    c, r = i % fx._INV_BAG_COLS, i // fx._INV_BAG_COLS
    return ((col0 + c) * T + 8, (fx._INV_BAG_ROW0 + r) * T + 8)


def test_clique_esquerdo_seleciona_e_direito_equipa_no_soltar():
    fx = _Fx()
    fx.inv.items.append(_make_item("training_sword"))
    fx.click(_bag_cell(fx, 0))
    assert fx._selected_inv_idx == 0
    fx.click(_bag_cell(fx, 0))
    assert fx._selected_inv_idx == -1       # 2º clique desseleciona
    fx.click(_bag_cell(fx, 0), button=3)
    assert fx.calls == [("equip", fx.inv.items[0].name)]


def test_direito_em_consumivel_usa():
    fx = _Fx()
    fx.inv.items.append(_make_item("hp_potion"))
    fx.click(_bag_cell(fx, 0), button=3)
    assert fx.calls == [("use", 0)]


def test_clique_no_slot_equipado_desequipa():
    fx = _Fx()
    fx.equip.slots["head"] = _make_item("training_sword")   # qualquer item serve aqui
    c, r = fx._INV_EQ_POS["head"]
    fx.click((c * T + 8, r * T + 8))
    assert fx.calls == [("unequip", "head")]


def test_slot_vazio_mostra_nome_do_slot_no_tooltip():
    fx = _Fx()
    c, r = fx._INV_EQ_POS["boots"]
    fx.hover((c * T + 8, r * T + 8))
    assert fx._pending_tooltip[2] == "Botas"


def test_x_fecha():
    fx = _Fx()
    tw = fx._inv_panel_rect.w // (T * S) if hasattr(fx, "_inv_panel_rect") else None
    fx.draw()
    tw = fx._inv_panel_rect.w // (T * S)
    fx.click(((tw - 1) * T + 8, 8))
    assert fx._show_inventory is False


def test_janela_na_area_segura_com_fonte_grande():
    fx = _Fx(font_size=40)
    fx.draw()
    assert fx._mm_safe_area().contains(fx._inv_panel_rect)
