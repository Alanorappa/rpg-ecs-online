"""
tests/test_move_player_debug.py — debug logging novo em
`WorldServer.move_player()` (rejeição por orçamento/walkability) e
`TileMovementSystem` (conclusão de dash), 28/08/2026 — 2ª rodada da
investigação de skill falhando ("Nenhum alvo"/"Alvo inválido") logo
depois de usar Interceptar. 1ª leitura de código (lag-compensation do
skill_processor + conclusão de dash da TileMovementSystem) não achou
causa raiz; o log de `SKILL_WARN` (ver tests/test_skill_warn_debug.py)
mostrou que 1+ segundo depois do Interceptar registrar "hit", o
servidor ainda tinha o player a vários tiles do alvo — estes 2 pontos
novos existem pra achar SE o dash completa certo e SE algum MOVE normal
subsequente é rejeitado.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from tests.helpers import make_world_server, spawn_player


class TestMovePlayerRejectionDebug(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "mv1", 130, 374)

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_rejeicao_por_orcamento_loga(self, mock_log):
        from engine.components import TileMovement
        # 1º move válido inicializa o baseline anti-cheat (_last_valid_ts).
        self.ws.move_player("mv1", 129, 374)
        mock_log.reset_mock()
        # Pedido MUITO além do orçamento (elapsed ~0 → budget mínimo).
        moved = self.ws.move_player("mv1", 129 - 50, 374)
        self.assertFalse(moved)
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[0], "MOVE_REJECTED_BUDGET")
        self.assertEqual(kwargs["eid"], self.eid)
        self.assertEqual(kwargs["requested"], (79, 374))

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_rejeicao_por_walkable_loga(self, mock_log):
        from engine.components import TileMovement
        # Sobe a velocidade só pra este teste, pra garantir que o check de
        # ORÇAMENTO (distância×tempo) nunca seja o motivo da rejeição — só
        # o de walkability importa aqui.
        import time
        tm = self.ws.world.get_component(self.eid, TileMovement)
        tm.speed = 1_000_000
        tm._last_valid_tile_x = tm.current_tile_x
        tm._last_valid_tile_y = tm.current_tile_y
        tm._last_valid_ts = time.time() - 5.0   # já "velho" — evita o auto-init de orçamento zero
        mock_log.reset_mock()
        # Tile fora do mapa — sempre não-andável.
        moved = self.ws.move_player("mv1", -5, -5)
        self.assertFalse(moved)
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[0], "MOVE_REJECTED_WALKABLE")
        self.assertEqual(kwargs["requested"], (-5, -5))

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_move_aceito_nao_loga(self, mock_log):
        moved = self.ws.move_player("mv1", 129, 374)
        self.assertTrue(moved)
        mock_log.assert_not_called()


class TestDashCompleteDebug(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "mv2", 130, 374)

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_dash_completo_loga_tile_de_pouso(self, mock_log):
        from engine.components import TileMovement
        tm = self.ws.world.get_component(self.eid, TileMovement)
        tm.is_dash        = True
        tm.is_moving      = True
        tm.progress       = 0.95
        tm.move_duration  = 0.1
        tm.target_tile_x  = 135
        tm.target_tile_y  = 374
        tm.start_pixel_x  = tm.target_pixel_x = 0
        tm.start_pixel_y  = tm.target_pixel_y = 0
        self.ws._global_tms.update(dt=1.0)   # dt grande o bastante pra progress >= 1.0
        self.assertFalse(tm.is_moving)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (135, 374))
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[0], "DASH_COMPLETE")
        self.assertEqual(kwargs["eid"], self.eid)
        self.assertEqual(kwargs["landed_tile"], (135, 374))

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_movimento_normal_nao_loga_dash_complete(self, mock_log):
        from engine.components import TileMovement
        tm = self.ws.world.get_component(self.eid, TileMovement)
        tm.is_dash        = False
        tm.is_moving      = True
        tm.progress       = 0.95
        tm.move_duration  = 0.1
        tm.target_tile_x  = 131
        tm.target_tile_y  = 374
        tm.start_pixel_x  = tm.target_pixel_x = 0
        tm.start_pixel_y  = tm.target_pixel_y = 0
        self.ws._global_tms.update(dt=1.0)
        self.assertFalse(tm.is_moving)
        mock_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
