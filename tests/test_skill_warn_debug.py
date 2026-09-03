"""
tests/test_skill_warn_debug.py — `SkillHandlers._warn` loga estado real
do caster/alvo (posição/tile/is_moving/is_dash) via `debug/aoi_debug.py`
quando a rejeição é "Fora de alcance"/"Alvo inválido"/"Nenhum alvo"
(28/08/2026) — 2ª rodada de investigação de skill falhando depois de
Interceptar, sem causa raiz confirmada só por leitura de código; log
serve pra pegar a posição/estado reais na próxima reprodução, em vez
de mais um fix às cegas.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from engine.world import World
from engine.components import PlayerControlled
from ui.systems import SkillSystem


class TestSkillWarnDebugLog(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, PlayerControlled())
        self.skill_sys = SkillSystem(self.world, self.player, screen=None)

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_loga_motivos_relevantes(self, mock_log):
        for reason in ("Fora de alcance", "Alvo inválido", "Nenhum alvo"):
            mock_log.reset_mock()
            self.skill_sys._warn(reason)
            mock_log.assert_called_once()
            args, kwargs = mock_log.call_args
            self.assertEqual(args[0], "SKILL_WARN")
            self.assertEqual(kwargs["reason"], reason)
            self.assertEqual(kwargs["caster"], self.player)

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_nao_loga_motivos_irrelevantes(self, mock_log):
        for reason in ("Raiva insuficiente (15)", "Mana insuficiente", "Sem cooldown"):
            mock_log.reset_mock()
            self.skill_sys._warn(reason)
            mock_log.assert_not_called()

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_sem_alvo_selecionado_loga_target_menos_1(self, mock_log):
        self.skill_sys._warn("Nenhum alvo")
        _, kwargs = mock_log.call_args
        self.assertEqual(kwargs["target"], -1)
        self.assertIsNone(kwargs["target_tile"])
        self.assertIsNone(kwargs["target_pos"])


if __name__ == "__main__":
    unittest.main()
