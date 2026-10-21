"""
tests/test_trainer_atlas_ui.py — treinador no atlas de UI
(ui/trainer_system.py): menu de assunto (atlas_window.prompt) e lista de
treinamento com Aprender. Cliques no SOLTAR, dentro de render(). Mouse falso.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

from engine.world import World
from engine.components import (CharacterStats, QuestLog, PlayerControlled, Wallet, PlayerSkills,
                               NPC, Trainer, RemoteEntityMeta)
from ui.trainer_system import TrainerSystem, _skill_order_for
from ui import atlas_window as aw
from content.skill_config import SKILL_LEVEL_REQUIREMENTS
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


def _tr(level=60, gold=1_000_000):
    world = World()
    player = world.create_entity()
    cs = CharacterStats(name="T", class_id="guerreiro")
    cs.level = level
    for c in (cs, QuestLog(),
              PlayerControlled(), Wallet(gold=gold), PlayerSkills()):
        world.add_component(player, c)
    npc = world.create_entity()
    world.add_component(npc, NPC(name="Mestre"))
    world.add_component(npc, Trainer(class_id="guerreiro"))
    world.add_component(npc, RemoteEntityMeta(server_eid=4242))
    tr = TrainerSystem(world, player, pygame.Surface((_W, _H)))
    tr.set_ui_scale(1.0)
    tr._net = _Net()
    tr._open_menu(npc)
    return tr


def _frame(tr, events=(), pos=None, pressed=(False, False, False)):
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        if pos is not None:
            pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: pressed
        tr.handle_events(list(events))
        tr.render()
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig


def _click(tr, hit_id):
    _frame(tr)
    pos = tr._hit_rects[hit_id].center
    _frame(tr, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)], pos, (True, False, False))
    _frame(tr, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)], pos)


def test_abre_direto_no_treinamento_e_aprende_manda_pro_servidor():
    tr = _tr()
    assert tr._state == tr.STATE_TRAINING     # só 1 assunto pra essa classe
    _click(tr, "tr:learn:0")
    sid = _skill_order_for("guerreiro")[0]
    assert tr._net.sent == [(MsgType.TRAIN_SKILL_REQUEST, {"npc_id": 4242, "skill_id": sid})]


def test_aprender_desabilitado_sem_nivel_nao_manda():
    tr = _tr(level=1)
    order = _skill_order_for("guerreiro")
    i = next(i for i, s in enumerate(order) if SKILL_LEVEL_REQUIREMENTS.get(s, 99) > 1)
    _frame(tr)
    if f"tr:learn:{i}" not in tr._hit_rects:
        pytest.skip("habilidade fora da área visível")
    _click(tr, f"tr:learn:{i}")
    assert tr._net.sent == []


def test_x_fecha_e_janela_na_area_segura():
    tr = _tr()
    _frame(tr)
    assert aw.safe_area(_W, _H, _H).contains(tr._panel_rect)
    _click(tr, "tr:close")
    assert not tr.is_open
