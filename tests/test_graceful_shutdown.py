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


if __name__ == "__main__":
    unittest.main(verbosity=2)
