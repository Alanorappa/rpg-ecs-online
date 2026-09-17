"""
tests/test_graceful_shutdown.py — servidor desligado (Ctrl+C) perdia todo
o progresso não salvo de todo mundo conectado (05/08/2026, bug real
relatado pelo usuário: "a penúltima vez que eu tinha logado, havia salvo
a config... agora... as habilidades não estavam na mesma configuração" —
suspeita confirmada: `server/main.py` não tinha handler de shutdown
gracioso, `KeyboardInterrupt` só era capturado DEPOIS do loop asyncio já
fechado, tarde demais pra salvar).

Fix: `main()` captura `KeyboardInterrupt` ainda dentro do `async with
websockets.serve(...)` (loop vivo) e chama `SessionManager._autosave_all()`
(o MESMO chokepoint do autosave periódico de 5 em 5 minutos) antes de
deixar a exceção propagar.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import server.main as main_mod


class TestGracefulShutdownSavesConnectedPlayers(unittest.IsolatedAsyncioTestCase):

    async def test_keyboard_interrupt_aciona_autosave_all_antes_de_propagar(self):
        calls = []
        orig_autosave_all = main_mod.SessionManager._autosave_all
        async def spy_autosave_all(self):
            calls.append(True)
            return await orig_autosave_all(self)
        main_mod.SessionManager._autosave_all = spy_autosave_all

        async def fake_run(self):
            raise KeyboardInterrupt()
        orig_run = main_mod.WorldServer.run
        main_mod.WorldServer.run = fake_run

        try:
            with self.assertRaises(KeyboardInterrupt):
                await main_mod.main("127.0.0.1", 0)  # porta 0 = OS escolhe livre
        finally:
            main_mod.SessionManager._autosave_all = orig_autosave_all
            main_mod.WorldServer.run = orig_run

        self.assertEqual(len(calls), 1,
            "_autosave_all deveria rodar 1x ao receber KeyboardInterrupt, "
            "ANTES do processo encerrar — sem isso, todo progresso desde o "
            "último autosave periódico se perde no Ctrl+C")


class TestAutosaveTaskTracked(unittest.IsolatedAsyncioTestCase):
    """16/09/2026 — ver arquitetura/PROJECT_WIDE_REVIEW.md (achado de
    qualidade "Alta"): `_on_tick` disparava `_autosave_all` via
    `asyncio.create_task` sem guardar a Task em lugar nenhum. O event loop
    só mantém referência FRACA — o GC pode coletar a task NO MEIO da
    execução, interrompendo o autosave de todo mundo ainda não salvo
    naquele lote (5 em 5 min), silenciosamente, sem exceção nem log. Fix:
    mesmo padrão já usado por `_track_dispatch_task`/`_dispatch_tasks`,
    agora dedicado (`_track_autosave_task`/`_autosave_tasks`).

    Reproduzir o bug de verdade (GC coletando no meio) não é praticável
    num teste determinístico — o teste aqui prova a fiação certa: a task
    fica com referência forte assim que criada, e some do registro quando
    termina (nunca cresce sem limite)."""

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager
        from shared.constants import TICK_RATE
        self.ws, self.mgr = make_session_manager()
        self.TICK_RATE = TICK_RATE

    async def test_autosave_task_fica_com_referencia_forte_e_some_ao_terminar(self):
        autosave_tick = self.TICK_RATE * 300
        self.assertEqual(len(self.mgr._autosave_tasks), 0)

        self.mgr._on_tick(autosave_tick, {})

        self.assertEqual(len(self.mgr._autosave_tasks), 1,
                         "task de autosave deveria estar registrada (referência forte) "
                         "imediatamente após ser criada")
        task = next(iter(self.mgr._autosave_tasks))
        await task  # sem sessões conectadas — _autosave_all termina rápido, sem tocar DB
        self.assertEqual(len(self.mgr._autosave_tasks), 0,
                         "registro deveria se limpar sozinho quando a task termina")

    async def test_shutdown_cancela_autosave_ainda_pendente(self):
        import asyncio
        started = asyncio.Event()
        release  = asyncio.Event()

        async def slow_autosave_all(self):
            started.set()
            await release.wait()

        orig = self.mgr._autosave_all
        self.mgr._autosave_all = slow_autosave_all.__get__(self.mgr)
        try:
            self.mgr._on_tick(self.TICK_RATE * 300, {})
            await asyncio.wait_for(started.wait(), timeout=1.0)
            self.assertEqual(len(self.mgr._autosave_tasks), 1)

            await self.mgr.shutdown_dispatch_tasks()

            self.assertEqual(len(self.mgr._autosave_tasks), 0,
                             "shutdown deveria cancelar e esperar a task de autosave "
                             "ainda pendente, não deixá-la órfã")
        finally:
            self.mgr._autosave_all = orig
            release.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
