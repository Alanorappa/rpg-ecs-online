"""
tests/test_fog_render_cache.py — cache do overlay de fog na tela
principal ficava "preso" perto de bush pequena (13/08/2026, achado do
usuário validando §52).

Causa: `TileRenderSystem._fog_overlay_surf` só reconstruía quando a
janela de tiles da CÂMERA mudava, nunca quando `FogOfWar.visible`/
`explored` mudavam por si só (jogador mudou de tile sem a câmera ter
cruzado fronteira ainda) — mesma classe de bug já documentada no
CLAUDE.md pro cache de terreno. Fix: `FogOfWar.version` (incrementado
por `FogSystem.update` sempre que recalcula) — `TileRenderSystem` lê
essa versão e reconstrói também quando ela muda, sem System chamar
System direto (comunicação via componente, regra do projeto).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

import unittest
from unittest.mock import patch

from engine.world import World
from engine.entity_factory import create_tilemap
from engine.components import FogOfWar
from ui.systems import TileRenderSystem


def _make_render_system():
    world = World()
    terrain = ["." * 20 for _ in range(20)]
    objects = [["."] * 20 for _ in range(20)]
    create_tilemap(world, terrain, objects, None)
    fog_eid = world.create_entity()
    fog = FogOfWar(radius=8, explore_radius=10)
    world.add_component(fog_eid, fog)
    screen = pygame.Surface((320, 240))
    sys_ = TileRenderSystem(world, screen)
    return sys_, fog


class TestFogOverlayCacheInvalidation(unittest.TestCase):

    def test_primeiro_render_grava_a_versao_atual(self):
        sys_, fog = _make_render_system()
        sys_.render(0, 0)
        sys_.render_fog()
        self.assertEqual(sys_._fog_cache_version, fog.version)

    def test_versao_mudou_sem_camera_mover_ainda_reconstroi(self):
        sys_, fog = _make_render_system()
        sys_.render(0, 0)
        sys_.render_fog()
        cached_ox, cached_oy = sys_._fog_cache_tile_ox, sys_._fog_cache_tile_oy

        # Simula o FogSystem tendo recalculado visible/explored (jogador
        # mudou de tile) SEM a câmera ter cruzado fronteira nenhuma —
        # mesma posição de câmera (0,0) das 2 chamadas.
        fog.version += 1
        sys_.render(0, 0)
        sys_.render_fog()

        self.assertEqual(sys_._fog_cache_tile_ox, cached_ox,
            "câmera não devia ter mudado de tile nesta simulação")
        self.assertEqual(sys_._fog_cache_tile_oy, cached_oy)
        self.assertEqual(sys_._fog_cache_version, fog.version,
            "cache deveria ter reconstruído por causa da versão nova, não da câmera")

    def test_sem_mudanca_nenhuma_nao_reconstroi_de_novo(self):
        sys_, fog = _make_render_system()
        sys_.render(0, 0)
        sys_.render_fog()

        sys_.render(0, 0)
        # pygame.draw.rect é chamado (por tile não-explorado) só dentro do
        # bloco de reconstrução do overlay — espiar isso é mais confiável
        # que mockar método de Surface (tipo C, atributo read-only).
        with patch("pygame.draw.rect") as mock_draw_rect:
            sys_.render_fog()
            mock_draw_rect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
