"""
tests/test_day_night_light_falloff.py
Curva de queda do gradiente ADITIVO de luz (09/10/2026, ver CLAUDE.md/
PROBLEMAS_ARQUITETURA.md §257/§258) — história em 2 atos:

§257: usuário relatou um "anel escuro" entre 2 postes que só se tocavam
pelas PONTAS (nenhum perto do centro do outro) — causa: queda LINEAR
faz a metade externa do raio contribuir pouco sozinha, "fraco + fraco"
ainda fica fraco. Tentativa de fix: expoente < 1.0 na curva
`(1-t)**p`, que segura o brilho alto por mais tempo.

§258: essa tentativa criou um problema PIOR — qualquer p<1 tem
derivada que tende a infinito perto de t=1 (queda quase vertical bem
na pontinha final do raio). Numa cena com VÁRIAS luzes próximas (cerca
com postes em fileira, cena real do usuário), isso virou bordas
circulares NÍTIDAS e visíveis ("diagrama de Venn"), pior que o anel
escuro original. Revertido pra p=1.0 (reta) — reta é a única curva
testada sem borda dura visível numa cena com várias luzes sobrepostas.

Os 2 testes abaixo existem pra NUNCA MAIS deixar alguém reintroduzir
essa classe de regressão sem perceber — o 1º é um guard explícito no
valor; o 2º é curva-agnóstico (detecta QUALQUER curva futura com
derivada íngreme perto da borda, não só um expoente específico).
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from ui.day_night_system import DayNightSystem


def _pixel_value(surf: "pygame.Surface", x: int, y: int) -> int:
    return surf.get_at((x, y))[0]


class TestFalloffExponentPermaneceLinear(unittest.TestCase):

    def test_falloff_exponent_e_1_0_ver_historico_de_regressao(self):
        """Pino explícito — mudar este valor exige reler
        PROBLEMAS_ARQUITETURA.md §257/§258 e retestar contra uma cena
        com VÁRIAS luzes sobrepostas (não só um par), nunca só a
        matemática isolada."""
        self.assertEqual(DayNightSystem._FALLOFF_EXPONENT, 1.0)


class TestSemQuedaAbruptaPertoDaBorda(unittest.TestCase):
    """Curva-agnóstico: detecta qualquer formato de queda (expoente,
    plateau, etc.) cuja derivada vire íngreme perto da borda — é
    EXATAMENTE essa característica que cria a borda circular nítida e
    visível quando várias luzes se sobrepõem (§258), independente de
    qual curva específica causou isso."""

    def test_passo_perto_da_borda_nao_e_muito_maior_que_a_media(self):
        world = World()
        player = world.create_entity()
        dns = DayNightSystem(world, player)
        radius_px = 200
        surf = dns._get_light_add_gradient(radius_px, 100)
        c = radius_px

        # Amostra fina do centro até quase a borda (passos de 1% do
        # raio) -- uma curva com derivada íngreme perto de t=1 (ex:
        # qualquer expoente < 1.0 na família `(1-t)**p`) só aparece
        # nos últimos 1-2%; uma amostragem grossa (passos de 5%+)
        # não pega o efeito (confirmado: ratio ~2.6x com p=0.4 em
        # passos de 5%, só aparece de verdade — ~6x — em passos de 1%).
        ts = [i / 100 for i in range(1, 100)]   # 0.01 .. 0.99
        values = [_pixel_value(surf, c + round(t * radius_px), c) for t in ts]
        steps = [values[i] - values[i + 1] for i in range(len(values) - 1)]
        avg_step = sum(steps) / len(steps)
        last_step = steps[-1]   # entre t=0.98 e t=0.99 -- bem perto da borda

        self.assertLessEqual(
            last_step, avg_step * 3,
            "queda perto da borda não pode ser muito mais íngreme que a média "
            "(sinal de borda circular dura e visível quando várias luzes se sobrepõem)")


if __name__ == "__main__":
    unittest.main()
