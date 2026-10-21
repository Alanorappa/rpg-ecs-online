"""
tests/test_trade_atlas_ui.py — troca, menu de player e convites (troca/
duelo/grupo) no atlas de UI (client/trade_handlers.py, duel_handlers.py,
party_handlers.py; peças atlas_window.prompt/quantity_window). Ações no
SOLTAR, dentro do desenho. Mouse falso.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.components import Inventory, Wallet, PlayerControlled
from client.trade_handlers import TradeHandlers
from client.duel_handlers import DuelHandlers
from client.party_handlers import PartyHandlers
from ui.ui_components import TradeUIState
from ui import atlas_window as aw
from ui.fonts import make as _font
from shared.messages import MsgType

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


class _Fx(TradeHandlers, DuelHandlers, PartyHandlers):
    def __init__(self):
        self.screen = pygame.Surface((_W, _H))
        self.font_md, self.font_sm, self.font_xs = _font(24), _font(19), _font(15)
        self.world = World()
        self.player_entity = self.world.create_entity()
        for c in (Inventory(), Wallet(gold=500), PlayerControlled()):
            self.world.add_component(self.player_entity, c)
        self._tui = TradeUIState()
        self._net = _Net()
        self._ui_events = []
        self._trade_press = aw.PressState()
        self._trade_gold_focus = False
        self._trade_gold_text = ""
        self._trade_qty_modal = None
        self._pending_tooltip = None
        self._my_eid = 1

    def _get_trade_ui(self):
        return self._tui

    def _mm_safe_area(self):
        return aw.safe_area(_W, _H, _H - 80)

    @property
    def inv(self):
        return self.world.get_component(self.player_entity, Inventory)

    def draw(self, events=()):
        self._ui_events = list(events)
        self._handle_trade_qty_modal_events(self._ui_events)
        self._draw_trade_ui()
        self._draw_duel_ui()
        self._draw_party_invite_ui()

    def rects(self):
        self.draw()
        r = dict(self._trade_rects)
        r.update(getattr(self, "_duel_hits", {}) if self._duel_invite_from else {})
        r.update(getattr(self, "_party_hits", {}) if self._party_invite_from else {})
        return r

    def click(self, hit_id, button=1, shift=False):
        pos = self.rects()[hit_id].center
        orig = pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods
        try:
            pygame.mouse.get_pos = lambda: pos
            pygame.key.get_mods = lambda: pygame.KMOD_SHIFT if shift else 0
            pygame.mouse.get_pressed = lambda num_buttons=3: (button == 1, False, button == 3)
            ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)
            consumed = (self._handle_trade_click(ev) or self._handle_duel_click(ev)
                        or self._handle_party_click(ev))
            self.draw([ev])
            pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
            self.draw([pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)])
        finally:
            pygame.mouse.get_pos, pygame.mouse.get_pressed, pygame.key.get_mods = orig
        return consumed


def _open(fx):
    fx._tui.trade_id = 7
    fx._tui.other_name = "Fulano"


def test_direito_na_mochila_oferta_e_shift_abre_quantidade():
    fx = _Fx()
    _open(fx)
    pot = _make_item("hp_potion"); pot.stack = 5
    fx.inv.items.extend([_make_item("training_sword"), pot])
    assert fx.click("bag:0", button=3) is True
    assert fx._net.sent[-1] == (MsgType.TRADE_OFFER_ITEM, {"inv_index": 0, "quantity": 1})
    fx.click("bag:1", button=3, shift=True)
    assert fx._trade_qty_modal is not None
    fx.click("qty:+")
    fx.click("qty:ok")
    assert fx._net.sent[-1] == (MsgType.TRADE_OFFER_ITEM, {"inv_index": 1, "quantity": 2})


def test_direito_na_minha_oferta_retira():
    fx = _Fx()
    _open(fx)
    fx._tui.my_offer = [_make_item("training_sword")]
    fx.click("my:0", button=3)
    assert fx._net.sent[-1] == (MsgType.TRADE_WITHDRAW_ITEM, {"offer_slot": 0})


def test_campo_de_ouro_foca_e_confirma_ao_clicar_fora():
    fx = _Fx()
    _open(fx)
    fx.click("trade:gold")
    assert fx._trade_gold_focus is True
    fx._trade_gold_text = "42"
    fx.click("trade:confirm")
    assert (MsgType.TRADE_SET_GOLD, {"amount": 42}) in fx._net.sent
    assert fx._net.sent[-1] == (MsgType.TRADE_CONFIRM, {})


def test_cancelar_e_x():
    fx = _Fx()
    _open(fx)
    fx.click("trade:cancel")
    assert fx._net.sent[-1] == (MsgType.TRADE_CANCEL, {})


def test_menu_de_player_negociar_e_clique_fora_fecha():
    fx = _Fx()
    fx._tui.popup_target_eid = 55
    fx._tui.popup_target_name = "Beltrano"
    fx._tui.popup_screen_pos = (600, 300)
    called = []
    fx._send_trade_request = lambda eid: called.append(eid)
    fx.click("popup:0")
    assert called == [55] and fx._tui.popup_target_eid == -1
    fx._tui.popup_target_eid = 55
    fx.draw()
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(5, 5))
    assert fx._handle_trade_click(ev) is False and fx._tui.popup_target_eid == -1


@pytest.mark.parametrize("kind,accept,decline", [
    ("trade", MsgType.TRADE_ACCEPT, MsgType.TRADE_DECLINE),
    ("duel", MsgType.DUEL_ACCEPT, MsgType.DUEL_DECLINE),
    ("party", MsgType.PARTY_ACCEPT, MsgType.PARTY_DECLINE),
])
def test_convites_aceitar_e_recusar(kind, accept, decline):
    for idx, expected in ((0, accept), (1, decline)):
        fx = _Fx()
        if kind == "trade":
            fx._tui.pending_invite_from_eid, fx._tui.pending_invite_from_name = 3, "Ciclano"
            hid = f"invite:{idx}"
        elif kind == "duel":
            fx._duel_invite_from_val = (3, "Ciclano")
            hid = f"duel:{idx}"
        else:
            fx._party_invite_from_val = (3, "Ciclano")
            hid = f"party:{idx}"
        assert fx.click(hid) is True     # modal bloqueante segura o clique
        assert fx._net.sent[-1] == (expected, {})


def test_janela_de_troca_na_area_segura():
    fx = _Fx()
    _open(fx)
    fx.draw()
    assert fx._mm_safe_area().contains(fx._trade_rects["window"])
