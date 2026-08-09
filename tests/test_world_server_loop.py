"""
tests/test_world_server_loop.py — Fase 5 de escala (06/08/2026, ver
arquitetura/ARQUITETURA_ONLINE.md §34.74.44). Achado real investigando
"orçamento de tempo por tick + degradação graciosa": o loop principal
(`WorldServer.run()`) só chamava `await asyncio.sleep()` no branch em que
NÃO está atrasado — sob sobrecarga SUSTENTADA (`_tick()` consistentemente
mais lenta que `TICK_INTERVAL`), o branch que roda `_tick()` é sempre
verdadeiro e o loop nunca cedia controle ao event loop do asyncio
(WebSocket para de responder — o "crashar com muitos players" real).
Fix: `_run_tick_or_sleep()` (extraído de `run()` pra ser testável
isoladamente) tem `await asyncio.sleep(0)` incondicional nesse branch.

Complementar: `_update_overbudget_streak()` detecta sobrecarga sustentada
(ticks CONSECUTIVOS acima do budget, diferente do aviso de "tick lento"
por incidente isolado) e loga entrada/saída do estado — só observabilidade.
"""
import os, sys, unittest, asyncio
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from unittest.mock import patch
from tests.helpers import make_world_server


class TestRunLoopYieldsUnderSustainedOverload(unittest.TestCase):
    def test_yield_garantido_mesmo_com_tick_sempre_atrasado(self):
        ws = make_world_server()
        sleep_calls = []
        real_sleep = asyncio.sleep

        async def _spy_sleep(delay):
            sleep_calls.append(delay)
            await real_sleep(0)

        async def _run():
            with patch("server.world_server.asyncio.sleep", _spy_sleep):
                next_tick = 0.0  # sempre no passado -> sempre entra no branch de tick
                nt = next_tick
                for _ in range(5):
                    nt = await ws._run_tick_or_sleep(nt)

        asyncio.run(_run())
        self.assertEqual(len(sleep_calls), 5,
            "await asyncio.sleep() deveria ser chamado em TODA iteração, mesmo "
            "com next_tick sempre no passado (sobrecarga sustentada simulada) — "
            "sem isso o event loop nunca cede pro asyncio processar WebSocket")


class TestOverbudgetStreakDetection(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.over  = self.ws._PERF_BUDGET_MS + 1.0
        self.under = self.ws._PERF_BUDGET_MS - 1.0
        self.threshold = self.ws._PERF_DEGRADED_STREAK_THRESHOLD

    def test_streak_ativa_degradado_exatamente_no_threshold(self):
        for i in range(self.threshold - 1):
            self.ws._update_overbudget_streak(self.over)
            self.assertFalse(self.ws._perf_degraded,
                f"não deveria degradar antes do threshold (tick {i + 1}/{self.threshold})")
        self.ws._update_overbudget_streak(self.over)
        self.assertTrue(self.ws._perf_degraded,
            "deveria degradar exatamente no tick que cruza o threshold")

    def test_tick_sob_budget_reseta_streak_e_recupera(self):
        for _ in range(self.threshold):
            self.ws._update_overbudget_streak(self.over)
        self.assertTrue(self.ws._perf_degraded)
        self.ws._update_overbudget_streak(self.under)
        self.assertFalse(self.ws._perf_degraded,
            "1 tick sob o budget deveria sair do estado degradado")
        self.assertEqual(self.ws._perf_overbudget_streak, 0)

    def test_incidente_isolado_nao_ativa_degradado(self):
        # 1 tick acima do budget, depois volta — nunca deveria cruzar o
        # threshold (mesmo padrão dos "tick lento" isolados vistos nos
        # logs reais desta sessão, nunca consecutivos).
        self.ws._update_overbudget_streak(self.over)
        self.ws._update_overbudget_streak(self.under)
        self.assertFalse(self.ws._perf_degraded)
        self.assertEqual(self.ws._perf_overbudget_streak, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
