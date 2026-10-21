"""
tests/test_crafting_atlas_ui.py — ferreiro/artesão no atlas de UI
(ui/crafting_system.py): abre direto na função padrão do NPC (sem menu de
3 botões), janela Reciclar/Forjar com as abas de serviço do NPC
(ui/npc_interaction.py), mochila em grade. Cliques no SOLTAR. Mouse falso.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.components import (CharacterStats, QuestLog, PlayerControlled, Inventory, Wallet,
                               Equipment, NPC, Blacksmith, Merchant, LearnedRecipes,
                               NPCInteractionRequest)
from ui.crafting_system import BlacksmithSystem
from ui import atlas_window as aw
from content.crafting_data import RECIPES
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


def _bs(gold=100_000, recipes=(), tab="recycle"):
    world = World()
    player = world.create_entity()
    for c in (CharacterStats(name="T", class_id="guerreiro"), QuestLog(), PlayerControlled(),
              Inventory(), Wallet(gold=gold), Equipment()):
        world.add_component(player, c)
    lr = LearnedRecipes()
    lr.known = list(recipes)
    world.add_component(player, lr)
    npc = world.create_entity()
    world.add_component(npc, NPC(name="Grum"))
    world.add_component(npc, Merchant(shop_id="general"))   # ferreiro real também vende
    world.add_component(npc, Blacksmith())
    bs = BlacksmithSystem(world, player, pygame.Surface((_W, _H)))
    bs.set_ui_scale(1.0)
    bs._net = _Net()
    bs._npc = npc
    if tab:
        bs._open_window(npc, tab)
    return bs


def _frame(bs, events=(), pos=None, pressed=(False, False, False)):
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        if pos is not None:
            pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: pressed
        bs.handle_events(list(events))
        bs.render()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig


def _click(bs, hit_id, button=1):
    _frame(bs)
    pos = bs._hit_rects[hit_id].center
    _frame(bs, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)], pos,
           (button == 1, False, button == 3))
    _frame(bs, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)], pos)


def test_clique_no_ferreiro_abre_direto_em_negociar():
    bs = _bs(tab=None)
    bs._open_menu(bs._npc)
    req = bs.world.get_component(bs.player_entity, NPCInteractionRequest)
    assert req is not None and req.kind == "open_shop" and not bs.is_open


def test_abas_trocam_reciclar_forjar_e_negociar_pede_loja():
    bs = _bs()
    _click(bs, "npc:tab:forge")
    assert bs._state == bs.STATE_FORGE
    _click(bs, "npc:tab:recycle")
    assert bs._state == bs.STATE_RECYCLE
    _click(bs, "npc:tab:shop")
    req = bs.world.get_component(bs.player_entity, NPCInteractionRequest)
    assert req is not None and req.kind == "open_shop" and not bs.is_open


def test_pedido_de_aba_de_outra_janela_abre_forjar():
    bs = _bs(tab=None)
    bs.world.add_component(bs.player_entity, NPCInteractionRequest(kind="open_forge", target_eid=bs._npc))
    bs.update([], 0)
    assert bs._state == bs.STATE_FORGE
    assert bs.world.get_component(bs.player_entity, NPCInteractionRequest) is None

def test_reciclar_equipamento_da_mochila_e_enviar():
    bs = _bs()
    inv = bs.world.get_component(bs.player_entity, Inventory)
    inv.items.append(_make_item("training_sword"))
    _click(bs, "bs:bag:0", button=3)
    assert bs._rec_item is not None and inv.items == []
    _click(bs, "bs:Reciclar")
    assert bs._net.sent[-1] == (MsgType.RECYCLE_REQUEST, {"inv_index": 0, "npc_eid": bs._bs_eid})


def test_direito_em_nao_equipamento_abre_deletar_e_clique_fora_fecha():
    bs = _bs()
    inv = bs.world.get_component(bs.player_entity, Inventory)
    inv.items.append(_make_item("hp_potion"))
    _click(bs, "bs:bag:0", button=3)
    assert bs._ctx_bag_idx == 0
    _frame(bs, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(3, 3))], (3, 3), (True, False, False))
    assert bs._ctx_bag_idx == -1 and len(inv.items) == 1


def test_forjar_seleciona_receita_e_envia():
    rid = next(iter(RECIPES))
    bs = _bs(recipes=[rid], tab="forge")
    inv = bs.world.get_component(bs.player_entity, Inventory)
    from content.crafting_data import MATERIALS
    for mat_id, qty in RECIPES[rid]["materials"]:
        m = MATERIALS[mat_id]()
        m.stack = qty
        inv.items.append(m)
    _click(bs, "bs:recipe:0")
    assert bs._frg_selected == rid
    _click(bs, "bs:Forjar")
    assert bs._net.sent[-1] == (MsgType.CRAFT_REQUEST, {"recipe_id": rid, "npc_eid": bs._bs_eid})


def test_x_fecha_e_janela_na_area_segura():
    bs = _bs(tab="forge")
    _frame(bs)
    assert aw.safe_area(_W, _H, _H).contains(bs._panel_rect)
    _click(bs, "bs:close")
    assert not bs.is_open
