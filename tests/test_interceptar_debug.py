"""
tests/test_interceptar_debug.py — debug logging novo em
`_skill_interceptar` (`engine/skill_handlers.py`), 28/08/2026, 3ª rodada
da investigação "skill falha depois de Interceptar" — o usuário
confirmou visualmente que o alvo estava PARADO/ADJACENTE mesmo assim, e
apontou que o talento de atordoar deveria estar ativo na BG (2ª
correção presumiu erroneamente que o minion tinha se movido). Estes
logs capturam a leitura INTERNA da posição do alvo no momento exato do
cálculo do destino do dash, e se o atordoamento realmente foi aplicado
com duração > 0 — dados que `SKILL_WARN`/`DASH_COMPLETE` (rodadas
anteriores) não mostravam.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, set_entity_tile, authorize_skill
from engine.components import CombatState, CharacterStats, PlayerSkills, TileMovement


def _ready_skill(ws, eid, sid):
    authorize_skill(ws, eid, sid)
    ps = ws.world.get_component(eid, PlayerSkills)
    from content.skill_config import SKILL_CATALOG
    sk = next((s for s in ps.skills if s and s.skill_id == sid), None)
    if sk is None:
        sk = PlayerSkills._make_skill(sid, SKILL_CATALOG)
        idx = ps.skills.index(None)
        ps.skills[idx] = sk
    sk.current_cooldown = 0.0
    return sk


class TestInterceptarDebugLog(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        run_ticks(self.ws, 50)
        self.mob = first_mob(self.ws)
        # Lado OESTE confirmado andável (129,374 aceito por move_player em
        # outro teste; 131,374/leste é parede nesta área do mapa de teste).
        set_entity_tile(self.ws, self.mob, 127, 374)   # 3 tiles, sem parede — cast válido
        cst = self.ws.world.get_component(self.eid, CombatState)
        cst.target_entity_id = self.mob
        cst.is_pursuing = True
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.rage = 100
        _ready_skill(self.ws, self.eid, "interceptar")

    def _cast_interceptar(self):
        self.ws._skill_position_corrections.clear()
        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "interceptar",
            "tid": self.mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
        })
        self.ws._process_skill_requests()

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_interceptar_cast_loga_leitura_interna_do_alvo(self, mock_log):
        self._cast_interceptar()
        calls = {c.args[0]: c.kwargs for c in mock_log.call_args_list}
        self.assertIn("INTERCEPTAR_CAST", calls)
        kw = calls["INTERCEPTAR_CAST"]
        self.assertEqual(kw["caster"], self.eid)
        self.assertEqual(kw["target"], self.mob)
        self.assertEqual(kw["target_tile_read"], (127, 374))
        # destino tem que ser adjacente ao tile lido do alvo
        dest_x, dest_y = kw["dest"]
        self.assertLessEqual(max(abs(dest_x - 127), abs(dest_y - 374)), 1)

    @patch("debug.aoi_debug.AOI_DBG.log")
    def test_interceptar_stun_check_loga_duracao_real(self, mock_log):
        self._cast_interceptar()
        calls = {c.args[0]: c.kwargs for c in mock_log.call_args_list}
        self.assertIn("INTERCEPTAR_STUN_CHECK", calls)
        kw = calls["INTERCEPTAR_STUN_CHECK"]
        self.assertEqual(kw["target"], self.mob)
        self.assertIn("stun_duration", kw)   # None ou 0.0 se o talento não estiver ativo


if __name__ == "__main__":
    unittest.main()
