"""
tests/test_loading_screen.py — 2 fases da tela de loading inicial
(28/09/2026, pedido do usuário): "assets" (preload_all rodando, barra
mostra done/total real) e "server" (barra antiga, baseada em
_loading_min_t). Ver PROBLEMAS_ARQUITETURA.md §215.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))

from client.habilidades_handlers import HabilidadesHandlers


class _LoadingScreenFixture(HabilidadesHandlers):
    def __init__(self):
        self.screen         = pygame.Surface((640, 480))
        self.font_lg         = pygame.font.SysFont(None, 32)
        self.font_sm         = pygame.font.SysFont(None, 18)
        self.font_xs         = pygame.font.SysFont(None, 14)
        self._net            = None
        self._loading_anim_t = 0.0
        self._loading_min_t  = 1.5
        self._loading_timeout = 15.0
        self._loading_phase  = "assets"
        self._preload_done   = 0
        self._preload_total  = 0


def _bar_fill_px(screen: pygame.Surface) -> int:
    """Mede a barra de progresso contando pixels verdes (25,90,25) na
    linha vertical do meio da barra — mesma geometria de
    _draw_loading_screen (sw//2 - 200 .. sw//2 + 200, sh//2 + 32)."""
    sw, sh = screen.get_size()
    y = sh // 2 + 32 + 5
    count = 0
    for x in range(sw // 2 - 200, sw // 2 + 200):
        if screen.get_at((x, y))[:3] == (25, 90, 25):
            count += 1
    return count


class TestLoadingScreenFaseAssets(unittest.TestCase):
    def test_barra_reflete_progresso_real_do_preload(self):
        fx = _LoadingScreenFixture()
        fx._preload_total = 100
        fx._preload_done  = 10
        fx._draw_loading_screen(0.0)
        fill_10pct = _bar_fill_px(fx.screen)

        fx._preload_done = 90
        fx._draw_loading_screen(0.0)
        fill_90pct = _bar_fill_px(fx.screen)

        self.assertGreater(fill_90pct, fill_10pct,
            "barra em 90% deveria estar mais preenchida que em 10%")

    def test_nao_usa_loading_min_t_durante_fase_assets(self):
        """Mesmo com _loading_min_t no valor inicial (1.5, "0% pronto"
        na fase server), a fase assets deve mostrar o progresso REAL
        do preload, não a barra antiga."""
        fx = _LoadingScreenFixture()
        fx._loading_min_t = 1.5   # equivalente a "server: 0%"
        fx._preload_total = 100
        fx._preload_done  = 100   # preload: 100% completo
        fx._draw_loading_screen(0.0)
        fill = _bar_fill_px(fx.screen)
        self.assertGreater(fill, 380, "preload 100% deveria mostrar a barra quase cheia")

    def test_nao_lanca_com_preload_total_zero(self):
        fx = _LoadingScreenFixture()
        fx._preload_total = 0
        fx._preload_done  = 0
        fx._draw_loading_screen(0.0)  # não deve lançar ZeroDivisionError


class TestLoadingScreenFaseServer(unittest.TestCase):
    def test_barra_usa_loading_min_t_na_fase_server(self):
        fx = _LoadingScreenFixture()
        fx._loading_phase = "server"
        fx._loading_min_t = 1.5
        fx._draw_loading_screen(0.0)
        fill_start = _bar_fill_px(fx.screen)

        fx._loading_min_t = 0.0
        fx._draw_loading_screen(0.0)
        fill_end = _bar_fill_px(fx.screen)

        self.assertGreater(fill_end, fill_start)


if __name__ == "__main__":
    unittest.main()
