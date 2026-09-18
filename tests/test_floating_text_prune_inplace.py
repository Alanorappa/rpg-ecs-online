"""
tests/test_floating_text_prune_inplace.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #9:
`FloatingTextManager`/`DashTrailManager`/`ProcTextManager`
(`ui/floating_text.py`) reconstruíam a lista "viva" inteira via
`alive = [...]; self._entries = alive` TODO frame, mesmo quando nada
expirou — alocação de lista nova desnecessária, custo que cresce com
o número de entradas (AoE com muitos hits simultâneos). Sem bug de
comportamento (a remoção sempre funcionou certo), só custo.

Prova: (1) o helper genérico `_prune_expired_inplace` remove
expirados preservando ordem dos vivos, SEM trocar o objeto lista
(mesmo `id()`); (2) cada um dos 3 managers reais (`FLT`/`DASH_TRAIL`/
`PROC`, via classes próprias — não os singletons globais, pra não
vazar estado entre testes) continua expirando/removendo entradas
certo depois da troca pro helper.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from ui.floating_text import (
    _prune_expired_inplace, FloatingTextManager, DashTrailManager, ProcTextManager,
)


class TestPruneExpiredInplaceHelper(unittest.TestCase):

    def test_remove_expirados_preserva_ordem_dos_vivos(self):
        class _E:
            def __init__(self, tag, timer):
                self.tag = tag
                self.timer = timer

        entries = [_E("a", 1.0), _E("b", -0.5), _E("c", 2.0), _E("d", -1.0), _E("e", 0.5)]
        original_list_id = id(entries)

        def _tick(e, dt):
            e.timer -= dt
            return e.timer > 0

        _prune_expired_inplace(entries, 0.0, _tick)

        self.assertEqual([e.tag for e in entries], ["a", "c", "e"],
                         "vivos deveriam sobrar na MESMA ordem relativa que entraram")
        self.assertEqual(id(entries), original_list_id,
                         "deveria mutar a MESMA lista, não trocar por uma nova")

    def test_lista_vazia_nao_quebra(self):
        entries = []
        _prune_expired_inplace(entries, 0.1, lambda e, dt: True)
        self.assertEqual(entries, [])

    def test_todos_expirados_esvazia_lista(self):
        class _E:
            def __init__(self):
                self.timer = 0.01
        entries = [_E(), _E(), _E()]
        _prune_expired_inplace(entries, 1.0, lambda e, dt: (setattr(e, "timer", e.timer - dt) or e.timer > 0))
        self.assertEqual(entries, [])


class TestManagersStillPruneCorrectly(unittest.TestCase):

    def test_floating_text_manager_remove_expirado_mantem_vivo(self):
        mgr = FloatingTextManager()
        mgr.add("morre logo", 0, 0, (255, 255, 255), size="small")
        mgr.add("vive",       0, 0, (255, 255, 255), size="small")
        # "morre logo" tem duration=1.0 (preset "small") — avança além disso
        mgr._entries[0].timer = 0.01
        mgr.update(0.1)
        self.assertEqual(len(mgr._entries), 1)
        self.assertEqual(mgr._entries[0].text, "vive")

    def test_dash_trail_manager_remove_expirado(self):
        mgr = DashTrailManager()
        mgr.emit(0, 0)
        mgr.emit(10, 10)
        mgr._segments[0].timer = 0.01
        mgr.update(0.1)
        self.assertEqual(len(mgr._segments), 1)

    def test_proc_text_manager_remove_expirado(self):
        mgr = ProcTextManager()
        mgr.add("proc 1")
        mgr.add("proc 2")
        mgr._entries[0].timer = 0.01
        mgr.update(0.1)
        self.assertEqual(len(mgr._entries), 1)
        self.assertEqual(mgr._entries[0].text, "proc 2")


if __name__ == "__main__":
    unittest.main()
