"""
tests/test_death_log_perf.py — travamento real relatado pelo usuário numa
troca de lane da BG (05/08/2026): log de perf mostrou ticks de até 177ms
(budget 33ms) com `death_handling`/`loot_drops` dominando, enquanto todo
o resto do tick continuava barato.

Causa raiz: `log.info()` síncrono (2 handlers, console + arquivo —
`server/log.py`) chamado 1x por morte ([Death], [Loot]) e 1x por ENTRADA
de XP ([XP]/[LevelUp] — proximidade pode gerar várias entradas por
morte). Várias mortes quase simultâneas (choque de lane) viravam dezenas
de chamadas de I/O síncrono na MESMA tick. Fix: rebaixado pra
`log.debug` — no nível padrão (INFO), nem chega nos handlers.
"""
import os, sys, unittest, logging
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, first_mob, run_ticks
from engine.components import PendingDeath


class TestDeathXpLootLogNaoRodamEmInfo(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)  # ~3s — deixa SpawnZoneSystem popular os mobs iniciais
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")

    def _kill_mob(self) -> None:
        self.ws.world.add_component(self.mob_eid, PendingDeath(killer_entity_id=self.player_eid))
        self.ws._mob_damage_log[self.mob_eid] = {self.player_eid: 999}

    def test_morte_real_nao_gera_log_info(self):
        """Nenhuma linha [Death]/[XP]/[LevelUp]/[Loot] deveria sair em
        INFO (nível padrão do servidor) — só em DEBUG."""
        info_msgs: list = []
        orig_info = logging.Logger.info
        def spy_info(self_logger, msg, *a, **kw):
            if self_logger.name == "server":
                info_msgs.append(msg % a if a else msg)
            return orig_info(self_logger, msg, *a, **kw)
        logging.Logger.info = spy_info
        try:
            self._kill_mob()
            run_ticks(self.ws, 1)
        finally:
            logging.Logger.info = orig_info

        offending = [m for m in info_msgs if any(
            tag in m for tag in ("[Death]", "[XP]", "[LevelUp]", "[Loot]"))]
        self.assertEqual(offending, [],
            f"log.info() ainda dispara por morte/xp/loot (deveria ser debug): {offending}")

    def test_morte_real_ainda_gera_log_debug(self):
        """Garante que a informação não sumiu — só desceu de nível."""
        debug_msgs: list = []
        orig_debug = logging.Logger.debug
        def spy_debug(self_logger, msg, *a, **kw):
            if self_logger.name == "server":
                debug_msgs.append(msg % a if a else msg)
            return orig_debug(self_logger, msg, *a, **kw)
        logging.Logger.debug = spy_debug
        try:
            self._kill_mob()
            run_ticks(self.ws, 1)
        finally:
            logging.Logger.debug = orig_debug

        self.assertTrue(any("[Death]" in m for m in debug_msgs),
            "log de morte deveria continuar existindo em DEBUG")


if __name__ == "__main__":
    unittest.main(verbosity=2)
