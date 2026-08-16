"""
tests/test_vision_blocking.py — Bush/árvore/pedra bloqueando linha de
visão (LOS), (13/08/2026, pedido do usuário: bush/árvore/pedra grande
nunca bloqueavam visão como parede já bloqueia).

Mecanismo: `TileType.vision_rect` (pegada independente de
`collision_rect`), `get_vision_offsets()` (engine/tileset.py) e o
overlay não-destrutivo em `create_tilemap` (engine/entity_factory.py).
Ver PROBLEMAS_ARQUITETURA.md e engine/tileset.py::_rect_to_tile_offsets.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import unittest

from engine.world import World
from engine.entity_factory import create_tilemap
from engine.components import Tilemap
from engine.tileset import get_collision_offsets, get_vision_offsets, OBJECT_MAPPING
from engine.fov import compute_fov, local_vision_blob


def _make_map(width: int, height: int, obj_char: str, obj_x: int, obj_y: int):
    """World headless com terreno todo aberto ('.') e UM objeto de catálogo
    (obj_char, ex: 'pl_b1') plantado em (obj_x, obj_y)."""
    w = World()
    terrain = ["." * width for _ in range(height)]
    objects = [["."] * width for _ in range(height)]
    objects[obj_y][obj_x] = obj_char
    tm_eid = create_tilemap(w, terrain, objects, None)
    return w.get_component(tm_eid, Tilemap)


def _make_map_multi(width: int, height: int, placements):
    """Mesmo que `_make_map`, mas aceita vários objetos:
    placements = [(obj_char, x, y), ...]."""
    w = World()
    terrain = ["." * width for _ in range(height)]
    objects = [["."] * width for _ in range(height)]
    for obj_char, x, y in placements:
        objects[y][x] = obj_char
    tm_eid = create_tilemap(w, terrain, objects, None)
    return w.get_component(tm_eid, Tilemap)


class TestBushVisionFootprint(unittest.TestCase):
    """Caso 1 do plano: bush bloqueia visão na largura do sprite inteiro,
    mas continua andável (is_solid=False) em toda essa área."""

    def test_pegada_de_visao_bloqueia_mas_nao_solidifica(self):
        tile = OBJECT_MAPPING["pl_b1"]
        vision_offsets = get_vision_offsets(tile)
        self.assertGreaterEqual(len(vision_offsets), 1)

        tm = _make_map(width=6, height=6, obj_char="pl_b1", obj_x=2, obj_y=3)
        for dx, dy in vision_offsets:
            nx, ny = 2 + dx, 3 + dy
            cell = tm.tile_matrix[ny][nx]
            self.assertGreaterEqual(cell.vision_height, 2,
                f"tile ({nx},{ny}) da pegada de visão do bush deveria bloquear LOS")
            self.assertFalse(cell.is_solid,
                f"tile ({nx},{ny}) do bush não deveria virar sólido (precisa continuar andável)")


class TestTreeTrunkVsCanopyVision(unittest.TestCase):
    """Caso 2 do plano: tronco (pegada de colisão) sólido + bloqueia
    visão; copa (só pegada de visão, fora do tronco) bloqueia visão mas
    NÃO é sólida — preserva o terreno original ali."""

    def test_tronco_solido_e_bloqueia_visao(self):
        tile = OBJECT_MAPPING["pl_t1"]
        collision_offsets = set(get_collision_offsets(tile))
        self.assertTrue(collision_offsets, "árvore t1 deveria ter pegada de colisão (tronco)")

        tm = _make_map(width=8, height=8, obj_char="pl_t1", obj_x=3, obj_y=5)
        for dx, dy in collision_offsets:
            nx, ny = 3 + dx, 5 + dy
            cell = tm.tile_matrix[ny][nx]
            self.assertTrue(cell.is_solid, f"tronco em ({nx},{ny}) deveria continuar sólido")
            self.assertGreaterEqual(cell.vision_height, 2,
                f"tronco em ({nx},{ny}) deveria bloquear visão")

    def test_copa_bloqueia_visao_sem_virar_solida(self):
        tile = OBJECT_MAPPING["pl_t1"]
        collision_offsets = set(get_collision_offsets(tile))
        vision_offsets = set(get_vision_offsets(tile))
        canopy_only = vision_offsets - collision_offsets
        self.assertTrue(canopy_only, "copa da árvore deveria cobrir tiles fora do tronco")

        tm = _make_map(width=8, height=8, obj_char="pl_t1", obj_x=3, obj_y=5)
        for dx, dy in canopy_only:
            nx, ny = 3 + dx, 5 + dy
            cell = tm.tile_matrix[ny][nx]
            self.assertGreaterEqual(cell.vision_height, 2,
                f"copa em ({nx},{ny}) deveria bloquear visão")
            self.assertFalse(cell.is_solid,
                f"copa em ({nx},{ny}) NÃO deveria virar sólida (preserva o chão original)")


class TestSmallRockRegression(unittest.TestCase):
    """Caso 3 do plano: pedra pequena (rock1, sem colisão nem marcação
    nova) continua sem bloquear visão — nada muda pro que não foi
    explicitamente marcado."""

    def test_rock1_continua_vision_height_zero(self):
        tile = OBJECT_MAPPING["pr_rock1"]
        self.assertEqual(tile.vision_height, 0)

        tm = _make_map(width=6, height=6, obj_char="pr_rock1", obj_x=2, obj_y=2)
        cell = tm.tile_matrix[2][2]
        self.assertLess(cell.vision_height, 2)


class TestFovBehindBush(unittest.TestCase):
    """Caso 4 do plano: ponta a ponta via engine.fov.compute_fov — um tile
    atrás de um bush não entra no conjunto visível."""

    def test_tile_atras_do_bush_fica_fora_do_fov(self):
        # Observador em (1, 3), bush em (3, 3) (pegada larga o bastante
        # pra cobrir toda a coluna x=3 nas linhas próximas), alvo atrás
        # em (5, 3) — mesma linha reta.
        tm = _make_map(width=9, height=7, obj_char="pl_b1", obj_x=3, obj_y=3)

        def is_blocking(x, y):
            if not (0 <= y < len(tm.tile_matrix) and 0 <= x < len(tm.tile_matrix[y])):
                return True
            return tm.tile_matrix[y][x].vision_height >= 2

        visible = compute_fov(1, 3, radius=8, is_blocking=is_blocking)
        self.assertNotIn((5, 3), visible,
            "tile atrás do bush não deveria estar no conjunto visível")


class TestVisionOffsetsRegressionWithoutVisionRect(unittest.TestCase):
    """Caso 5 do plano: tiles sem vision_rect configurado continuam se
    comportando exatamente como antes do refactor de
    _rect_to_tile_offsets (get_vision_offsets == só o tile-âncora,
    get_collision_offsets inalterado)."""

    def test_tile_sem_vision_rect_so_ancora(self):
        tile = OBJECT_MAPPING["pr_rock1"]
        self.assertIsNone(tile.vision_rect)
        self.assertEqual(get_vision_offsets(tile), [(0, 0)])

    def test_get_collision_offsets_inalterado_para_tile_com_rect_explicito(self):
        tile = OBJECT_MAPPING["pl_t1"]
        # t1: collision_rect=(32, 96, 32, 32), sprite 96x128 -> mesmo
        # cálculo de antes do refactor (base_y_in_sprite = 128-32=96).
        offsets = get_collision_offsets(tile)
        self.assertEqual(offsets, [(1, 0)])


class TestBushBaseRowFootprint(unittest.TestCase):
    """§51 (13/08/2026, pedido do usuário) — bush usa `vision_rect=
    "base_row"` (só a fileira de baixo, largura inteira) em vez de
    "full" (sprite inteiro), pra bushes com espaço de grama visível
    entre si não se fundirem num blob só de visão."""

    def test_bush_multitile_so_conta_fileira_de_baixo(self):
        tile = OBJECT_MAPPING["pl_b11"]  # 64x64 = 2x2 tiles em "full"
        self.assertEqual(tile.vision_rect, "base_row")
        offsets = get_vision_offsets(tile)
        self.assertEqual(set(offsets), {(0, 0), (1, 0)})
        self.assertNotIn((0, -1), offsets, "não deveria mais contar a metade de cima do sprite")
        self.assertNotIn((1, -1), offsets, "não deveria mais contar a metade de cima do sprite")

    def test_bush_1_tile_fica_igual(self):
        tile = OBJECT_MAPPING["pl_b14"]  # 32x32 = já era 1 tile só
        self.assertEqual(get_vision_offsets(tile), [(0, 0)])

    def test_arvore_e_pedra_grande_continuam_full(self):
        # Confirma que o sentinel novo não vazou pra fora da família de bush.
        tree = OBJECT_MAPPING["pl_t1"]
        rock = OBJECT_MAPPING["pr_bigrock"]
        self.assertEqual(tree.vision_rect, "full")
        self.assertEqual(rock.vision_rect, "full")

    def test_bushes_diagonais_que_so_tocavam_pela_metade_de_cima_nao_se_conectam(self):
        # 2 pl_b11 em diagonal: A em (2,5), B em (4,4). Sob "full" (pegada
        # 2x2), A cobre até (3,4) e B cobre até (4,4)/(4,3) — as pegadas
        # se tocam em (3,4)-(4,4) (mesma linha, colunas vizinhas), mesmo
        # com as BASES (fileira de baixo de cada uma) não se tocando de
        # verdade. Com base_row, A vira só {(2,5),(3,5)} e B só
        # {(4,4),(5,4)} — nenhuma adjacência 4-direções entre elas
        # (a única aproximação, (3,5)-(4,4), é diagonal, não conta).
        tm = _make_map_multi(width=10, height=8, placements=[
            ("pl_b11", 2, 5), ("pl_b11", 4, 4),
        ])

        def is_blocking(x, y):
            if not (0 <= y < len(tm.tile_matrix) and 0 <= x < len(tm.tile_matrix[y])):
                return True
            return tm.tile_matrix[y][x].vision_height >= 2

        blob = local_vision_blob(2, 5, is_blocking)
        self.assertNotIn((4, 4), blob)
        self.assertNotIn((5, 4), blob)

    def test_bushes_com_base_colada_continuam_1_blob(self):
        # 2 pl_b11 lado a lado, base tocando (x=2 e x=4 -> footprint de
        # 2 termina em x=3, x=4 começa logo em seguida = adjacente).
        tm = _make_map_multi(width=10, height=8, placements=[
            ("pl_b11", 2, 5), ("pl_b11", 4, 5),
        ])

        def is_blocking(x, y):
            if not (0 <= y < len(tm.tile_matrix) and 0 <= x < len(tm.tile_matrix[y])):
                return True
            return tm.tile_matrix[y][x].vision_height >= 2

        blob = local_vision_blob(2, 5, is_blocking)
        self.assertIn((4, 5), blob)
        self.assertIn((5, 5), blob)


if __name__ == "__main__":
    unittest.main()
