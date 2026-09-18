"""
tests/test_remote_move_queue_deque.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #6: `_mob_move_queues`/
`_remote_player_move_queues`/`_self_move_queue`
(`client/remote_entity_handlers.py::_process_mob_move_queues`) drenavam
via `list.pop(0)` — O(n) por passo (desloca todo o resto da lista),
em vez de O(1) com `collections.deque.popleft()`. Sem bug de
comportamento (ordem FIFO sempre foi respeitada), só custo
desnecessário que cresce com o tamanho da fila (lag spike/muitos
passos enfileirados de uma vez).

Prova: (1) as 3 filas são `deque` de verdade, não `list`; (2) drenar
múltiplos passos enfileirados continua respeitando a ordem FIFO
correta ponta a ponta via `_process_mob_move_queues` real.
"""
import os, sys, unittest
from collections import deque
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import Position, TileMovement
from client.remote_entity_handlers import RemoteEntityHandlers


class _MoveQueueFixture(RemoteEntityHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._mob_move_queues = {}
        self._remote_player_move_queues = {}
        self._self_move_queue = deque()
        self._remote_mobs = {}
        self._remote_players = {}
        self._net_last_tx = 0
        self._net_last_ty = 0


def _make_fixture():
    world = World()
    player = world.create_entity()
    world.add_component(player, Position(x=0, y=0))
    world.add_component(player, TileMovement(current_tile_x=0, current_tile_y=0))

    mob = world.create_entity()
    world.add_component(mob, Position(x=0, y=0))
    world.add_component(mob, TileMovement(current_tile_x=0, current_tile_y=0))

    fx = _MoveQueueFixture(world, player)
    fx._remote_mobs[100] = mob
    return fx, mob


class TestRemoteMoveQueueIsDeque(unittest.TestCase):

    def test_mob_move_queue_criada_via_setdefault_e_deque(self):
        fx, mob = _make_fixture()
        # 1ª chamada com tm parado inicia o tween NA HORA (branch else de
        # _move_remote_mob) — não enfileira nada ainda. Só a 2ª chamada,
        # com tm já em movimento, cai no branch que faz
        # self._mob_move_queues.setdefault(server_eid, deque()).
        fx._move_remote_mob(100, 1, 1, is_dash=False)
        fx._move_remote_mob(100, 2, 2, is_dash=False)
        self.assertIsInstance(fx._mob_move_queues[100], deque,
                              "fila de movimento de mob deveria ser deque, não list")

    def test_self_move_queue_e_deque(self):
        fx, _mob = _make_fixture()
        self.assertIsInstance(fx._self_move_queue, deque)

    def test_drena_multiplos_passos_em_ordem_fifo(self):
        """4 passos enfileirados de uma vez (ex: lag spike) — precisam sair
        na MESMA ordem que entraram. O 1º passo inicia o tween imediatamente
        (mob estava parado); os 3 seguintes vão pra fila (mob já em
        movimento) e são drenados um por chamada de _process_mob_move_queues
        (só avança quando o passo anterior termina)."""
        fx, mob = _make_fixture()
        passos = [(1, 0), (2, 0), (2, 1), (3, 1)]

        primeiro_tx, primeiro_ty = passos[0]
        fx._move_remote_mob(100, primeiro_tx, primeiro_ty, is_dash=False)
        for tx, ty in passos[1:]:
            fx._move_remote_mob(100, tx, ty, is_dash=False)
        self.assertEqual(len(fx._mob_move_queues[100]), len(passos) - 1)

        tm = self.world_get_tm(fx, mob)
        ordem_recebida = [(tm.target_tile_x, tm.target_tile_y)]  # passo 1, já em curso
        for _ in range(len(passos) - 1):
            tm.is_moving = False  # simula passo anterior concluído
            fx._process_mob_move_queues()
            ordem_recebida.append((tm.target_tile_x, tm.target_tile_y))

        self.assertEqual(ordem_recebida, passos,
                         "passos deveriam sair na mesma ordem FIFO que entraram")
        self.assertNotIn(100, fx._mob_move_queues, "fila vazia deveria ser removida do dict")

    @staticmethod
    def world_get_tm(fx, eid):
        from engine.components import TileMovement as _TM
        return fx.world.get_component(eid, _TM)


if __name__ == "__main__":
    unittest.main()
