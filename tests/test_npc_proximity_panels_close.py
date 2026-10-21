"""
tests/test_npc_proximity_panels_close.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #1 (auditoria anterior,
client-side): `game.py::_close_modals_if_too_far` (fecha painel de NPC
individualmente ao o player se afastar) e `_close_all_modals` (fecha
TUDO incondicionalmente) tinham cada um sua PRÓPRIA lista de `if` por
painel (Loot/Shop/Crafting/Trainer/QuestDialog) — duplicação real: um
painel novo adicionado só num dos dois métodos nunca fecha pelo outro
gatilho.

Trava a lista única (`_npc_proximity_panels`) contra os dois
comportamentos ANTES existentes: fecha por distância (um painel por
vez, só o que está longe) e fecha tudo (todos, incondicional) —
usando os métodos REAIS de `game.GameEngine` (padrão já usado no
projeto pra testar método de GameEngine sem instanciar a classe
inteira, ver `tests/test_client_ui.py`, `_GE_pvp`/`_GE_rsr`/`_GE_mm`).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import TileMovement, Position
from game import GameEngine as _GE


class _StubPanel:
    """Stand-in leve pra loot/shop/crafting/trainer/quest_dialog — só o
    suficiente pro que os 2 métodos reais leem/chamam."""

    def __init__(self, npc_id_attr: str, uses_open_flag: bool = True):
        self._npc_id_attr = npc_id_attr
        self.closed = False
        if uses_open_flag:
            self.is_open = False
        setattr(self, npc_id_attr, -1)
        # quest_dialog também zera isto em _close_all_modals — inofensivo
        # nos outros stands-in, presente em todos por simplicidade.
        self._pending_npc_id = -1

    def set_npc(self, eid: int) -> None:
        setattr(self, self._npc_id_attr, eid)
        if hasattr(self, "is_open"):
            self.is_open = True

    def _close(self) -> None:
        self.closed = True
        self.is_open = False

    def _close_modal(self) -> None:
        """Loot usa esse nome, e "aberto" = open_corpse_id != -1 (sem `is_open`)."""
        self.closed = True
        setattr(self, self._npc_id_attr, -1)


class _ProximityPanelsFixture:
    """Fixture mínima — usa os métodos REAIS de GameEngine
    (_close_modals_if_too_far, _close_all_modals, _npc_proximity_panels),
    com stand-ins pros 5 subsistemas de painel."""
    _close_modals_if_too_far = _GE._close_modals_if_too_far
    _close_all_modals        = _GE._close_all_modals
    _npc_proximity_panels    = _GE._npc_proximity_panels
    _get_drag                = _GE._get_drag

    def __init__(self, world, player_entity):
        self.world          = world
        self.player_entity  = player_entity
        self._loot_system     = _StubPanel("open_corpse_id", uses_open_flag=False)
        self._shop_system     = _StubPanel("open_merchant_id")
        self._crafting_system = _StubPanel("_bs_eid")
        self._trainer_system  = _StubPanel("_tr_eid")
        self._quest_dialog    = _StubPanel("_dialog_npc_id")
        self._mail_system     = _StubPanel("mailbox_eid")
        self._quest_journal   = _StubPanel("_dummy_id")
        self._map_overlay     = _StubPanel("_dummy_id2")
        self._map_overlay.is_open = False
        # Estado que _close_all_modals também zera — presente só pra não
        # levantar AttributeError, valor não é o foco deste teste.
        self._dt = 0.0
        self._show_inventory = False
        self._selected_inv_idx = -1
        self._show_talents = False
        self._show_debug = False
        self._show_habilidades = False
        self._show_skills = False
        self._show_char_stats = False
        self._arena_modal_open_val = False
        self._show_pause = False
        self._pause_submenu = ""
        self._sound_drag = ""
        self._show_hotbar_editor = False
        self._hbe_drag_from = None
        self._hbe_rebind_slot = None
        self._hbe_tab = 0
        self._mkb_rebind = None
        from ui.atlas_window import ScrollState
        self._hbe_scroll = ScrollState()
        self._drag_state = None

    def _get_drag(self):
        class _Drag:
            def reset(self_inner):
                pass
        if self._drag_state is None:
            self._drag_state = _Drag()
        return self._drag_state


def _make_world_with_player_and_npc(npc_tile: tuple[int, int]):
    world = World()
    player = world.create_entity()
    world.add_component(player, TileMovement(current_tile_x=0, current_tile_y=0,
                                              target_tile_x=0, target_tile_y=0))
    npc = world.create_entity()
    from shared.constants import TILE_SIZE
    world.add_component(npc, Position(x=npc_tile[0] * TILE_SIZE, y=npc_tile[1] * TILE_SIZE,
                                      prev_x=0, prev_y=0))
    return world, player, npc


class TestCloseModalsIfTooFar(unittest.TestCase):

    def test_fecha_painel_individual_quando_player_se_afasta(self):
        world, player, npc = _make_world_with_player_and_npc((5, 0))  # 5 tiles de distância
        fx = _ProximityPanelsFixture(world, player)
        fx._shop_system.set_npc(npc)

        fx._close_modals_if_too_far()

        self.assertTrue(fx._shop_system.closed, "loja deveria fechar — NPC a 5 tiles, limite é 3")

    def test_nao_fecha_painel_quando_player_esta_perto(self):
        world, player, npc = _make_world_with_player_and_npc((1, 0))  # adjacente
        fx = _ProximityPanelsFixture(world, player)
        fx._shop_system.set_npc(npc)

        fx._close_modals_if_too_far()

        self.assertFalse(fx._shop_system.closed, "loja não deveria fechar — NPC adjacente")

    def test_loot_usa_open_corpse_id_em_vez_de_is_open(self):
        """Loot é o único dos 5 sem atributo `is_open` — a lista única
        precisa continuar tratando esse caso especial corretamente."""
        world, player, npc = _make_world_with_player_and_npc((6, 0))
        fx = _ProximityPanelsFixture(world, player)
        fx._loot_system.set_npc(npc)
        self.assertEqual(fx._loot_system.open_corpse_id, npc)

        fx._close_modals_if_too_far()

        self.assertTrue(fx._loot_system.closed)
        self.assertEqual(fx._loot_system.open_corpse_id, -1)

    def test_painel_ja_fechado_nao_e_afetado(self):
        world, player, npc = _make_world_with_player_and_npc((10, 0))
        fx = _ProximityPanelsFixture(world, player)
        # shop nunca foi aberto (npc_id continua -1, is_open=False)

        fx._close_modals_if_too_far()  # não deveria levantar exceção

        self.assertFalse(fx._shop_system.closed)


class TestCloseAllModals(unittest.TestCase):

    def test_fecha_todos_os_paineis_de_npc_incondicionalmente(self):
        world, player, npc = _make_world_with_player_and_npc((0, 0))  # bem perto
        fx = _ProximityPanelsFixture(world, player)
        for panel in (fx._loot_system, fx._shop_system, fx._crafting_system,
                     fx._trainer_system, fx._quest_dialog):
            panel.set_npc(npc)

        fx._close_all_modals()

        self.assertTrue(fx._loot_system.closed)
        self.assertTrue(fx._shop_system.closed)
        self.assertTrue(fx._crafting_system.closed)
        self.assertTrue(fx._trainer_system.closed)
        self.assertTrue(fx._quest_dialog.closed)

    def test_painel_novo_na_lista_unica_fecha_pelos_dois_gatilhos(self):
        """Prova a motivação real do fix: um painel presente na lista
        única (_npc_proximity_panels) é fechado tanto por proximidade
        quanto por 'fechar tudo' — sem precisar lembrar de tocar em 2
        lugares separados."""
        world, player, npc = _make_world_with_player_and_npc((5, 0))
        fx = _ProximityPanelsFixture(world, player)
        fx._trainer_system.set_npc(npc)

        fx._close_modals_if_too_far()
        self.assertTrue(fx._trainer_system.closed, "deveria fechar por distância")

        fx._trainer_system.closed = False
        fx._trainer_system.set_npc(npc)
        fx._close_all_modals()
        self.assertTrue(fx._trainer_system.closed, "deveria fechar por 'fechar tudo'")


if __name__ == "__main__":
    unittest.main()
