"""
tests/test_buff_bar_row_cache.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #3: `BuffBar.render()`
(`ui/buff_bar.py`) chamava `_build_effects_row()` TODO frame, que aloca
uma `Surface` nova e re-renderiza glifo de fonte pra CADA buff, mesmo
quando a composição visual não muda (a contagem regressiva só muda 1x
por segundo, não 60x). Sem bug de comportamento, só custo de alocação/
render de texto desnecessário.

Prova: (1) 2 frames com o MESMO buff e a mesma contagem em segundos
reusam a MESMA Surface (`assertIs`); (2) contagem cruzando um segundo
inteiro invalida o cache (nova Surface, texto atualizado); (3) um buff
novo entrando na barra invalida o cache mesmo com o mesmo 1º buff.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import StatusEffects, ActiveEffect
from ui.buff_bar import BuffBar


def _make_buff_bar_with_effect(duration: float):
    world = World()
    player = world.create_entity()
    sfx = StatusEffects()
    sfx.effects["proc_amulet_warrior"] = ActiveEffect(
        effect_type="proc_amulet_warrior", duration=duration)
    world.add_component(player, sfx)
    bb = BuffBar(world, player)
    return bb, sfx, world


class TestBuffBarRowCache(unittest.TestCase):

    def test_mesma_contagem_reusa_surface_do_cache(self):
        bb, _sfx, _world = _make_buff_bar_with_effect(16.4)
        screen = pygame.Surface((1280, 720))
        bb.render(screen)
        surf1 = bb._row_cache_surf
        self.assertIsNotNone(surf1)

        bb.render(screen)  # 2º frame, duração ainda em 16.4 (mesmo segundo exibido)
        self.assertIs(bb._row_cache_surf, surf1,
                      "mesma contagem em segundos deveria reusar a MESMA Surface")

    def test_cruzar_segundo_inteiro_invalida_cache(self):
        bb, sfx, _world = _make_buff_bar_with_effect(16.4)
        screen = pygame.Surface((1280, 720))
        bb.render(screen)
        surf1 = bb._row_cache_surf

        sfx.effects["proc_amulet_warrior"].duration = 15.4  # cruzou de 17 pra 16 segundos exibidos
        bb.render(screen)
        self.assertIsNot(bb._row_cache_surf, surf1,
                         "contagem mudando de segundo exibido deveria invalidar o cache")

    def test_buff_novo_invalida_cache_mesmo_com_1o_buff_igual(self):
        bb, sfx, world = _make_buff_bar_with_effect(16.4)
        screen = pygame.Surface((1280, 720))
        bb.render(screen)
        surf1 = bb._row_cache_surf

        sfx.effects["cc_immune"] = ActiveEffect(
            effect_type="cc_immune", duration=5.0)
        bb.render(screen)
        self.assertIsNot(bb._row_cache_surf, surf1,
                         "buff novo entrando na barra deveria invalidar o cache")


if __name__ == "__main__":
    unittest.main()
