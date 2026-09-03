"""
tests/test_move_grace_dash_guard.py — 30/08/2026, 6ª rodada da investigação
"skill falha depois de selecionar alvo / depois do Interceptar".

Causa raiz achada: `ServerCombatStateSystem._tick_player_move_grace`
(engine/core_systems.py) desliga `TileMovement.is_moving` à força quando o
grace de movimento normal (`_server_move_grace`, fixo em 0.4s pra QUALQUER
movimento) zera — sem olhar se há um dash REAL (`is_dash=True`,
`move_duration` independente, ex: Interceptar = 0.18s) em andamento. Se o
grace restante de um passo normal anterior expira no MEIO do dash mais
curto, `TileMovementSystem` (que só processa `if tile_movement.is_moving`)
para de processar a entidade — `progress`/`Position` travam no meio da
interpolação, `is_dash` fica preso True até o próximo passo normal
completar. Corrigido: nunca desligar is_moving à força enquanto is_dash é
True (mesmo padrão já usado pra proteger dash de stun/sleep).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.core_systems import ServerCombatStateSystem
from engine.components import TileMovement


class TestMoveGraceDashGuard(unittest.TestCase):

    def test_grace_expira_durante_dash_nao_desliga_is_moving(self):
        tm = TileMovement()
        tm.is_moving = True
        tm.is_dash = True
        tm._server_move_grace = 0.05   # sobrou pouco de um passo normal anterior

        ServerCombatStateSystem._tick_player_move_grace(tm, dt=0.1)  # zera o grace

        self.assertEqual(tm._server_move_grace, 0.0)
        self.assertTrue(tm.is_moving, "dash em andamento não pode ser desligado pelo grace fake")
        self.assertTrue(tm.is_dash)

    def test_grace_expira_sem_dash_ainda_desliga_is_moving(self):
        tm = TileMovement()
        tm.is_moving = True
        tm.is_dash = False
        tm._server_move_grace = 0.05

        ServerCombatStateSystem._tick_player_move_grace(tm, dt=0.1)

        self.assertEqual(tm._server_move_grace, 0.0)
        self.assertFalse(tm.is_moving, "comportamento original (sem dash) preservado")

    def test_grace_ainda_nao_expirou_nao_mexe_em_is_moving(self):
        tm = TileMovement()
        tm.is_moving = True
        tm.is_dash = False
        tm._server_move_grace = 0.4

        ServerCombatStateSystem._tick_player_move_grace(tm, dt=0.1)

        self.assertAlmostEqual(tm._server_move_grace, 0.3)
        self.assertTrue(tm.is_moving)


if __name__ == "__main__":
    unittest.main()
