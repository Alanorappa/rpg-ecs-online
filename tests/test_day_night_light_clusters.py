"""Clusters de luz estática do dia/noite (27/09/2026, pedido do usuário).

Antes: DayNightSystem.render() desenhava CADA luz de mapa (lampião/
tocha) separadamente, TODO frame — um vilarejo com 20+ lampiões
agrupados custava ~6-8ms/frame medidos com profiling (ver conversa),
porque a posição de cada lampião nunca muda (só a câmera se move), mas
o custo de somar as luzes sobrepostas era pago de novo a cada frame.

Agora: DayNightSystem.build_static_light_clusters(tilemap) varre o
object_matrix INTEIRO 1x (chamado por game.py a cada troca de mapa),
agrupa lampiões próximos num mesmo cluster e PRÉ-COMPÕE 1 Surface só
por cluster. render() faz 1 blit por cluster VISÍVEL, nunca mais 1 por
luz — luz de item de jogador (dinâmica, se move) continua resolvida
individualmente, fora deste mecanismo.
"""
import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))   # tamanho normal — nunca 1x1 (ver histórico desta sessão)

from engine.world import World
from engine.entity_factory import create_tilemap
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
from ui.day_night_system import DayNightSystem, jump_to_fraction, set_debug_time_offset

_LIGHT_A = "vp_143"   # radius_tiles=6 no catálogo atual
_LIGHT_B = "vp_170"   # radius_tiles=5


class _AddBlitSpySurface(pygame.Surface):
    """`pygame.Surface.blit` é read-only na classe/instância (tipo C
    imutável) — não dá pra monkeypatchar direto (mesmo padrão já usado
    em tests/test_character_animation.py::_BlitSpySurface). Subclasse
    Python pura só pra registrar blits com special_flags==BLEND_RGBA_ADD
    (a assinatura da luz — tanto cluster quanto luz individual) antes de
    delegar pro blit real."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.add_blit_calls = []

    def blit(self, source, dest, area=None, special_flags=0):
        if special_flags == pygame.BLEND_RGBA_ADD:
            self.add_blit_calls.append((source, dest))
        return super().blit(source, dest, area, special_flags)


def _make_tilemap(width, height, placements):
    """placements: lista de (obj_char, x, y)."""
    world = World()
    terrain = ["." * width for _ in range(height)]
    objects = [["."] * width for _ in range(height)]
    for obj_char, x, y in placements:
        objects[y][x] = obj_char
    eid = create_tilemap(world, terrain, objects, None)
    from engine.components import Tilemap
    return world, world.get_component(eid, Tilemap)


class TestBuildStaticLightClustersAgrupamento(unittest.TestCase):

    def test_sem_luz_no_mapa_nao_cria_cluster(self):
        _, tilemap = _make_tilemap(20, 20, [])
        dns = DayNightSystem(World(), -1)
        dns.build_static_light_clusters(tilemap)
        self.assertEqual(dns._static_clusters, [])

    def test_luzes_proximas_viram_1_cluster_so(self):
        # vp_143 tem radius_tiles=6 -> 2 luzes a 5 tiles de distancia
        # estao bem dentro do alcance combinado (6+6+1 de folga = 13).
        _, tilemap = _make_tilemap(40, 40, [
            (_LIGHT_A, 10, 10), (_LIGHT_A, 15, 10),
        ])
        dns = DayNightSystem(World(), -1)
        dns.build_static_light_clusters(tilemap)
        self.assertEqual(len(dns._static_clusters), 1)

    def test_luzes_distantes_viram_clusters_separados(self):
        # 60 tiles de distancia — bem alem do alcance combinado (6+6+1).
        _, tilemap = _make_tilemap(120, 40, [
            (_LIGHT_A, 5, 10), (_LIGHT_A, 65, 10),
        ])
        dns = DayNightSystem(World(), -1)
        dns.build_static_light_clusters(tilemap)
        self.assertEqual(len(dns._static_clusters), 2)

    def test_cluster_com_luzes_de_raio_misto(self):
        # vp_143 (raio 6) + vp_170 (raio 5), proximas -> ainda 1 cluster.
        _, tilemap = _make_tilemap(40, 40, [
            (_LIGHT_A, 10, 10), (_LIGHT_B, 14, 10),
        ])
        dns = DayNightSystem(World(), -1)
        dns.build_static_light_clusters(tilemap)
        self.assertEqual(len(dns._static_clusters), 1)
        cluster = dns._static_clusters[0]
        self.assertIn("add_surf", cluster)
        self.assertIn("glow_surf", cluster)
        self.assertGreater(cluster["rect"].width, 0)
        self.assertGreater(cluster["rect"].height, 0)


class TestRenderUsaClusterEmVezDeLuzPorLuz(unittest.TestCase):

    def setUp(self):
        jump_to_fraction(0.0)   # meia-noite garantida (is_night() == True)

    def tearDown(self):
        set_debug_time_offset(0.0)   # nunca vazar override pros outros testes

    def _make_dns_with_spy(self, tilemap):
        """DayNightSystem com _overlay_surf JÁ setado como a Surface-espiã
        (do tamanho certo) ANTES do render() — assim render() reusa essa
        instância (só cria uma nova se o tamanho não bater, ver
        `self._overlay_surf is None or self._overlay_size != (...)`),
        sem precisar monkeypatchar nada."""
        world = World()
        player = world.create_entity()
        dns = DayNightSystem(world, player)
        dns.world_surf = pygame.Surface((320, 240))
        dns.build_static_light_clusters(tilemap)
        spy = _AddBlitSpySurface((320, 240))
        dns._overlay_surf = spy
        dns._overlay_size = (320, 240)
        return dns, spy

    def test_um_blit_aditivo_por_cluster_visivel_nao_por_luz(self):
        _, tilemap = _make_tilemap(40, 40, [
            (_LIGHT_A, 10, 10), (_LIGHT_A, 15, 10), (_LIGHT_A, 12, 14),
        ])
        dns, spy = self._make_dns_with_spy(tilemap)
        self.assertEqual(len(dns._static_clusters), 1, "as 3 luzes deveriam virar 1 cluster")

        cluster_rect = dns._static_clusters[0]["rect"]
        cam_x = cluster_rect.centerx - 160
        cam_y = cluster_rect.centery - 120

        dns.render(world_objects=[], camera_offset_x=cam_x, camera_offset_y=cam_y)

        self.assertEqual(len(spy.add_blit_calls), 1,
            "deveria ter exatamente 1 blit aditivo (o cluster inteiro), nao 1 por luz")

    def test_cluster_fora_da_tela_nao_gera_blit(self):
        _, tilemap = _make_tilemap(200, 40, [
            (_LIGHT_A, 10, 10), (_LIGHT_A, 15, 10),
        ])
        dns, spy = self._make_dns_with_spy(tilemap)

        # camera bem longe do cluster
        cam_x = 190 * TILE_SIZE
        cam_y = 10 * TILE_SIZE

        dns.render(world_objects=[], camera_offset_x=cam_x, camera_offset_y=cam_y)

        self.assertEqual(len(spy.add_blit_calls), 0, "cluster fora da tela nao deveria gerar blit nenhum")

    def test_luz_de_jogador_continua_individual_mesmo_com_clusters(self):
        _, tilemap = _make_tilemap(40, 40, [(_LIGHT_A, 10, 10)])
        dns, spy = self._make_dns_with_spy(tilemap)

        cluster_rect = dns._static_clusters[0]["rect"]
        cam_x = cluster_rect.centerx - 160
        cam_y = cluster_rect.centery - 120

        # luz de jogador MUITO longe do cluster estatico (nao deveria se
        # fundir com o cluster de jeito nenhum — mecanismos independentes)
        player_light_obj = {
            "screen_x": 300, "screen_y": 200,
            "light_source": (0, 0, 0, 0, 75, 4),
            "player_light": True,
        }

        dns.render(world_objects=[player_light_obj], camera_offset_x=cam_x, camera_offset_y=cam_y)

        # 1 blit do cluster estatico + 1 blit da luz individual do jogador
        self.assertEqual(len(spy.add_blit_calls), 2)

    def test_de_dia_nao_desenha_cluster_nenhum(self):
        _, tilemap = _make_tilemap(40, 40, [
            (_LIGHT_A, 10, 10), (_LIGHT_A, 15, 10),
        ])
        dns, spy = self._make_dns_with_spy(tilemap)

        cluster_rect = dns._static_clusters[0]["rect"]
        cam_x = cluster_rect.centerx - 160
        cam_y = cluster_rect.centery - 120

        # Amanhecer (fração 0.25, entre 0.20 e 0.29): ambiente NÃO é
        # branco puro (não cai no early-return de "dia pleno" que já
        # existia antes desta mudança) mas is_night()==False — exercita
        # de propósito o gate NOVO (_ambient_night) do cluster, não o
        # early-return antigo.
        jump_to_fraction(0.25)
        from ui.day_night_system import is_night, _MULT_IDENTITY, day_night_color
        self.assertFalse(is_night())
        self.assertNotEqual(day_night_color()[:3], _MULT_IDENTITY)

        dns.render(world_objects=[], camera_offset_x=cam_x, camera_offset_y=cam_y)

        self.assertEqual(len(spy.add_blit_calls), 0, "de dia/amanhecer nenhum lampiao deveria acender")


if __name__ == "__main__":
    unittest.main()
