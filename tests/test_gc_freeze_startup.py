"""
tests/test_gc_freeze_startup.py — gc.freeze() no boot do servidor
(05/09/2026, ver PROBLEMAS_ARQUITETURA.md §113).

`server/main.py` já tinha `gc.disable()` pra evitar pausas de GC
automático durante o tick — mas o `gc.collect()` MANUAL periódico
(`WorldServer.run()`, a cada ~10s) continuava escaneando todo objeto
vivo desde o boot (mapas/catálogos/entidades), 20-50ms de pausa por
disparo, medido em teste de carga real. `gc.freeze()` logo depois do
mundo carregar (mundo pronto, ninguém ainda conectado) move esse
baseline pra fora do alcance de qualquer collect() futuro.

Este teste prova a ORDEM: collect() e freeze() precisam acontecer
DEPOIS do WorldServer estar pronto e ANTES do servidor começar a aceitar
conexões (websockets.serve) — congelar cedo demais (antes do mundo
carregar) deixaria o próprio mundo de fora do freeze, sem ganho nenhum;
congelar tarde demais (depois de já aceitar conexão) arriscaria congelar
estado de player real junto com o baseline estático."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server.main as main_mod


class _FakeServeCM:
    """Substitui `websockets.serve(...)` — nunca abre socket de verdade."""
    def __init__(self, order):
        self._order = order

    async def __aenter__(self):
        self._order.append("serve_start")
        return self

    async def __aexit__(self, *exc):
        return False


class TestGcFreezeAfterWorldBoot(unittest.TestCase):

    def test_collect_e_freeze_chamados_apos_world_pronto_antes_de_servir(self):
        order: list = []

        async def fake_world_run(self_ws):
            order.append("world_run")

        with patch.object(main_mod, "init_db"), \
             patch.object(main_mod._gc, "collect", side_effect=lambda *a, **kw: order.append("collect")), \
             patch.object(main_mod._gc, "freeze", side_effect=lambda: order.append("freeze")), \
             patch.object(main_mod.websockets, "serve", return_value=_FakeServeCM(order)), \
             patch.object(main_mod.WorldServer, "run", fake_world_run):
            asyncio.run(main_mod.main("localhost", 0))

        self.assertIn("collect", order)
        self.assertIn("freeze", order)
        self.assertIn("serve_start", order)
        self.assertIn("world_run", order)

        self.assertLess(order.index("collect"), order.index("freeze"),
                        "collect() deveria rodar ANTES do freeze() (limpa lixo do boot "
                        "antes de congelar o que sobrar)")
        self.assertLess(order.index("freeze"), order.index("serve_start"),
                        "freeze() deveria acontecer ANTES do servidor aceitar conexão — "
                        "congelar depois arriscaria prender estado de player real no baseline")

    def test_gc_desabilitado_continua_ativo(self):
        """`gc.disable()` roda no import de server/main.py (nível de
        módulo) — freeze()/collect() novos não podem reabilitar a coleta
        automática sem querer."""
        import gc
        self.assertFalse(gc.isenabled(),
                         "gc.disable() do módulo deveria continuar em vigor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
