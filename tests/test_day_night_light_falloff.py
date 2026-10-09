"""
tests/test_day_night_light_falloff.py
Curva de queda do gradiente ADITIVO de luz (09/10/2026, ver CLAUDE.md/
PROBLEMAS_ARQUITETURA.md) — bug real relatado pelo usuário via
screenshot: 2 postes de luz que só se tocam pelas PONTAS (nenhum perto
do centro do outro) somavam "fraco + fraco" ali, aparecendo como um
anel escuro entre os dois, mesmo cada um tecnicamente alcançando o
outro. Causa: a curva de queda era uma RETA (`1 - dist/raio`) — a
metade externa do raio já contribui pouco sozinha. Fix: curva
`(1-dist/raio)**p` com p<1 (`DayNightSystem._FALLOFF_EXPONENT`),
que segura o brilho alto por mais tempo e só cai forte bem perto da
borda de verdade.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from ui.day_night_system import DayNightSystem


def _pixel_value(surf: "pygame.Surface", x: int, y: int) -> int:
    return surf.get_at((x, y))[0]   # gradiente é cinza (R=G=B) — 1 canal basta


class TestFalloffCurvaMantemBrilhoPertoDaBorda(unittest.TestCase):

    def setUp(self):
        world = World()
        player = world.create_entity()
        self.dns = DayNightSystem(world, player)
        self.radius_px = 160
        self.intensity_pct = 100
        self.surf = self.dns._get_light_add_gradient(self.radius_px, self.intensity_pct)

    def test_centro_continua_no_maximo(self):
        """t=0 — (1-0)**p == 1 pra qualquer p; centro sempre no pico,
        independente da curva escolhida."""
        c = self.radius_px
        self.assertEqual(_pixel_value(self.surf, c, c), 255)

    def test_borda_continua_zero(self):
        """t=1 — (1-1)**p == 0 pra qualquer p; borda sempre no-op pro
        BLEND_RGBA_ADD, igual sempre foi."""
        c = self.radius_px
        # pixel a EXATAMENTE radius_px de distância do centro (x=0,y=c)
        # -- dist == radius_px bate a condição "<=", inclusa no círculo.
        self.assertEqual(_pixel_value(self.surf, 0, c), 0)

    def test_regiao_proxima_da_borda_fica_mais_forte_que_a_reta_antiga(self):
        """Ponto a 70% do raio (bem dentro da faixa que causava o anel
        escuro quando 2 luzes se tocavam só pelas pontas) precisa ficar
        MAIS BRILHANTE do que a fórmula reta antiga (`255*(1-t)`) dava —
        é exatamente essa diferença que resolve o bug: uma luz que
        chega "fraca" nessa faixa somada com a outra luz, também
        fraca ali, não cria mais um vale escuro perceptível."""
        c = self.radius_px
        t = 0.7
        x = c + round(t * self.radius_px)
        actual = _pixel_value(self.surf, x, c)
        old_linear_value = round(255 * (1.0 - t))
        self.assertGreater(actual, old_linear_value,
                           "a 70% do raio, a curva nova precisa ser mais forte que a reta antiga")

    def test_intensidade_ainda_escala_o_pico(self):
        """strength (intensity_pct) continua multiplicando o resultado
        inteiro — não foi substituído pelo expoente novo."""
        surf_50 = self.dns._get_light_add_gradient(self.radius_px, 50)
        c = self.radius_px
        self.assertEqual(_pixel_value(surf_50, c, c), round(255 * 0.5))


class TestDuasLuzesTocandoPelasPontasNaoCriaAnelEscuro(unittest.TestCase):
    """Reprodução direta do sintoma relatado: 2 luzes com raios que só se
    tocam na parte externa (nenhuma chega perto do centro da outra) —
    o PONTO MÉDIO entre as duas precisa somar uma luminosidade real,
    não ficar quase zero feito antes."""

    def test_soma_no_meio_fica_claramente_acima_de_cada_luz_isolada_ali(self):
        world = World()
        player = world.create_entity()
        dns = DayNightSystem(world, player)
        radius_px = 160
        # Centros a 256px de distância -- overlap só nos últimos 64px de
        # cada (raio 160 cada, soma 320 > 256), igual o caso real.
        gradient = dns._get_light_add_gradient(radius_px, 100)

        def value_at_dist(dist: float) -> int:
            d = radius_px * 2
            c = radius_px
            x = min(d - 1, max(0, c + round(dist)))
            return _pixel_value(gradient, x, c)

        dist_between_centers = 256
        midpoint = dist_between_centers / 2   # 128px de CADA centro

        # Contribuição de 1 luz sozinha no ponto médio (a 128px do SEU
        # próprio centro, raio 160 -> t=0.8).
        single_contribution = value_at_dist(midpoint)
        total_at_midpoint = single_contribution * 2   # as 2 luzes contribuem igual ali (simetria)

        # Bug antigo (reta): a 80% do raio, reta dava só 255*0.2=51 por
        # luz -> total ~102, bem mais escuro que o pico de qualquer uma
        # das duas sozinha no CENTRO dela (255). Com a curva nova, o
        # total no meio precisa continuar relevante (> metade do pico).
        self.assertGreater(total_at_midpoint, 255 * 0.5,
                           "o meio de 2 luzes se tocando pelas pontas não pode ficar escuro")


if __name__ == "__main__":
    unittest.main()
