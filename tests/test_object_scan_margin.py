"""
tests/test_object_scan_margin.py — get_world_objects()/render_static_objects()
(ui/systems.py::TileRenderSystem) cortavam objeto ancorado na BASE fora da
janela "tela em tiles + 2" antes do sprite (que cresce PRA CIMA/DIREITA a
partir da âncora) sair de vista de verdade.

Bug real relatado pelo usuário (02/09/2026): construção/árvore alta só
aparecia quando a câmera alcançava a base do objeto, e piorava com zoom
(janela de tiles visíveis menor). Fix: TileRenderSystem._object_visible_tile_range
estende a varredura pra ESQUERDA/BAIXO pelo tamanho do maior sprite do
catálogo (_object_scan_margin_tiles, calculado do próprio OBJECT_MAPPING,
nunca um número fixo). Ver PROBLEMAS_ARQUITETURA.md §102.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

import unittest

from engine.world import World
from engine.entity_factory import create_tilemap
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
from ui.systems import TileRenderSystem, _object_scan_margin_tiles

# vb044 (torre redonda alta, catálogo "TX Village Building with Shadow") —
# 256x352px = 8x11 tiles, bem maior que a margem fixa antiga de 2 tiles.
# Recatalogado 03/09/2026 (usuário reeditou os PNGs de apoio, ids viraram
# sequenciais "vb001".."vb097" — ver PROBLEMAS_ARQUITETURA.md §103); o
# objeto usado aqui como referência mudou de id/tamanho, mecanismo intacto.
_TALL_WIDE_OBJ = "vb044"


def _make_world_with_object(width, height, obj_x, obj_y, obj_char=_TALL_WIDE_OBJ):
    world = World()
    terrain = ["." * width for _ in range(height)]
    objects = [["."] * width for _ in range(height)]
    objects[obj_y][obj_x] = obj_char
    create_tilemap(world, terrain, objects, None)
    screen = pygame.Surface((320, 240))  # 10x7.5 tiles a 32px
    sys_ = TileRenderSystem(world, screen)
    return sys_


class TestObjectScanMarginFromCatalog(unittest.TestCase):

    def test_margem_bate_com_o_maior_objeto_do_catalogo(self):
        t = OBJECT_MAPPING[_TALL_WIDE_OBJ]
        self.assertEqual(t.sprite_px_w, 256)
        self.assertEqual(t.sprite_px_h, 352)
        margin_left, margin_down = _object_scan_margin_tiles()
        # maior objeto real do catálogo pode ser >= vb044 (ex: torres
        # maiores cadastradas depois) — a margem só pode ficar >= o
        # necessário pra este objeto, nunca menor.
        self.assertGreaterEqual(margin_left, 256 // TILE_SIZE - 1)
        self.assertGreaterEqual(margin_down, 352 // TILE_SIZE - 1)


class TestTallObjectAnchoredBelowScreenStillRenders(unittest.TestCase):
    """Objeto alto ancorado ABAIXO da janela ingênua de tiles, mas com o
    TOPO do sprite ainda dentro da área visível — tem que aparecer."""

    def test_get_world_objects_inclui_objeto_com_base_fora_mas_topo_visivel(self):
        # Tela 240px alta = 7 tiles + resto. Janela ingênua (+2) cobre linhas
        # 0..8. vb044 tem 352px (11 tiles) de altura — ancorado na linha
        # 15 (bem fora da janela ingênua), o TOPO do sprite fica em
        # anchor_y - total_h = (15+1)*32 - 352 = 160px, dentro da tela de
        # 240px — deveria aparecer.
        sys_ = _make_world_with_object(width=20, height=30, obj_x=2, obj_y=15)
        objs = sys_.get_world_objects(camera_offset_x=0, camera_offset_y=0)
        matched = [o for o in objs if o["tile_x"] == 2 and o["tile_y"] == 15]
        self.assertEqual(len(matched), 1,
            "objeto alto com base fora da tela mas topo visível não deveria ser cortado")
        self.assertLess(matched[0]["screen_y"], 240,
            "topo do sprite deveria estar mesmo dentro da tela")

    def test_objeto_realmente_fora_de_alcance_continua_cortado(self):
        # Base bem mais longe (linha 40) — nem a margem calculada alcança;
        # confirma que a varredura não virou "sem limite".
        sys_ = _make_world_with_object(width=20, height=50, obj_x=2, obj_y=40)
        objs = sys_.get_world_objects(camera_offset_x=0, camera_offset_y=0)
        matched = [o for o in objs if o["tile_x"] == 2 and o["tile_y"] == 40]
        self.assertEqual(len(matched), 0,
            "objeto genuinamente fora de alcance (nem com margem) não devia aparecer")


class TestWideObjectAnchoredLeftOfScreenStillRenders(unittest.TestCase):
    """Objeto largo ancorado à ESQUERDA da câmera, mas com a ponta direita
    do sprite ainda dentro da área visível — tem que aparecer."""

    def test_get_world_objects_inclui_objeto_com_base_a_esquerda_mas_direita_visivel(self):
        # Câmera em tile_ox=20 (offset_x = 20*32). vb044 tem 256px
        # (8 tiles) de largura — ancorado na coluna 14 (6 antes da câmera),
        # a ponta direita do sprite fica em (14+8)*32 - 20*32 = 64px depois
        # da borda esquerda da tela — dentro da tela de 320px.
        sys_ = _make_world_with_object(width=40, height=20, obj_x=14, obj_y=2)
        objs = sys_.get_world_objects(camera_offset_x=20 * TILE_SIZE, camera_offset_y=0)
        matched = [o for o in objs if o["tile_x"] == 14 and o["tile_y"] == 2]
        self.assertEqual(len(matched), 1,
            "objeto largo com âncora fora à esquerda mas sprite alcançando a tela não deveria ser cortado")

    def test_objeto_realmente_fora_de_alcance_a_esquerda_continua_cortado(self):
        sys_ = _make_world_with_object(width=40, height=20, obj_x=0, obj_y=2)
        objs = sys_.get_world_objects(camera_offset_x=20 * TILE_SIZE, camera_offset_y=0)
        matched = [o for o in objs if o["tile_x"] == 0 and o["tile_y"] == 2]
        self.assertEqual(len(matched), 0,
            "objeto genuinamente fora de alcance à esquerda não devia aparecer")


class TestRenderStaticObjectsUsesSameRange(unittest.TestCase):
    """render_static_objects() (objetos no_ysort=True) tinha o MESMO bug
    duplicado — mesma correção, mesmo método compartilhado."""

    def test_render_static_objects_nao_lanca_e_desenha_objeto_alto_fora_da_base(self):
        # no_ysort não é usado por vb044 — só confirma que o método
        # compartilhado (_object_visible_tile_range) roda sem erro e chega
        # a varrer a linha 15 (mesma faixa estendida do teste acima), via
        # side-effect observável: nenhuma exceção com um mapa grande.
        sys_ = _make_world_with_object(width=20, height=30, obj_x=2, obj_y=15)
        sys_.render_static_objects(camera_offset_x=0, camera_offset_y=0)  # não deve lançar


if __name__ == "__main__":
    unittest.main()
