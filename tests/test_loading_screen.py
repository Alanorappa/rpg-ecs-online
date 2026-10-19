"""
tests/test_loading_screen.py — 2 fases da tela de loading inicial
(28/09/2026, pedido do usuário): "assets" (preload_all rodando) e
"server" (baseada em _loading_min_t). Desde 19/10/2026 as 2 fases
compartilham UMA barra de progresso contínua (`_ASSETS_PHASE_WEIGHT`
em client/habilidades_handlers.py) em vez de cada uma reiniciar seu
próprio ciclo 0%→100% — ver HISTORICO_FEATURES.md.
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
        # 1280x720 (19/10/2026) — a barra de sprite escalada (SCALE do
        # nameplate, hoje 2x) é mais larga que a tela de teste antiga de
        # 640x480 comportava (760px de barra não cabia), causando
        # IndexError ao ler pixel fora da Surface.
        self.screen         = pygame.Surface((1280, 720))
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
    """Mede a barra de progresso contando pixels da cor de preenchimento
    (#291000, ver _LOADING_BAR_MARKER_COLOR) na linha horizontal do meio
    do retângulo de preenchimento — geometria lida DINAMICAMENTE de
    _get_loading_bar() (19/10/2026, barra virou sprite — nunca hardcoda
    de novo a posição/cor antigas, que mudam se o PNG ou o SCALE do
    nameplate mudarem)."""
    from client.habilidades_handlers import _get_loading_bar, _LOADING_BAR_MARKER_COLOR
    bar = _get_loading_bar()
    sw, sh = screen.get_size()
    bar_img = bar["img"]
    fill_r  = bar["fill_rect"]
    bar_w, bar_h = bar_img.get_size()
    bx = sw // 2 - bar_w // 2
    by = sh - 20 - bar_h
    y = by + fill_r.y + fill_r.h // 2
    count = 0
    for x in range(bx + fill_r.x, bx + fill_r.x + fill_r.w):
        if screen.get_at((x, y))[:3] == _LOADING_BAR_MARKER_COLOR:
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
        """Mesmo com _loading_min_t no valor inicial (1.5, "fase server
        ainda não começou"), a fase assets deve mostrar o progresso REAL
        do preload, não a barra antiga. Progresso ÚNICO (19/10/2026,
        pedido do usuário): preload 100% completo mostra a barra em
        _ASSETS_PHASE_WEIGHT (hoje 0.5 — metade), NÃO quase cheia — a
        outra metade é reservada pra fase "server" que ainda não
        rodou, pra barra não "resetar" visualmente na troca de fase."""
        fx = _LoadingScreenFixture()
        fx._loading_min_t = 1.5   # fase server ainda não avançou nada
        fx._preload_total = 100
        fx._preload_done  = 100   # preload: 100% completo
        fx._draw_loading_screen(0.0)
        fill = _bar_fill_px(fx.screen)
        from client.habilidades_handlers import _get_loading_bar
        _full_w = _get_loading_bar()["fill_rect"].w
        self.assertAlmostEqual(fill / _full_w, 0.5, delta=0.05,
            msg="preload 100% deveria mostrar a barra na metade (peso da fase assets)")

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
