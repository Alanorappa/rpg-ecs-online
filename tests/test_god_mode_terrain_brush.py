"""
tests/test_god_mode_terrain_brush.py — pincel inteligente de terreno no
editor de mapa (F10, aba Terreno, "TX Tileset Terrain"), 26-27/08/2026,
pedido do usuário.

Pintar "Grama" num tile que hoje é "Terra" (ou vice-versa) escreve no
tile clicado 1 variante sorteada (pura/com flor, 80/20). Nos vizinhos,
usa AUTOTILE POSICIONAL de verdade (`resolve_blob3x3_piece`,
`engine/tileset.py`) — a região de transição são 2 quebra-cabeças de
3×3 peças interligadas ("mancha"/"cruz"), não 18 tiles soltos pra
sortear (1ª versão sorteava e produzia mosaico sem nexo — bug relatado
pelo usuário, ver PROBLEMAS_ARQUITETURA.md §81 "Correção"). Cada
vizinho recebe a peça EXATA de acordo com quais dos PRÓPRIOS vizinhos
dele já são a categoria nova; combinação sem peça no asset (ex: tile
isolado) não é tocada. "Piso sobre grama/terra" é um pincel de grupo
aleatório cujo pool nunca inclui os tiles parciais/customizáveis (fora
da zona `floor_full`).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from engine.components import Tilemap
from engine.tileset import SHEET_FAMILIES, terrain_zone_of, resolve_blob3x3_piece
from ui.god_mode import GodModeEditor


def _make_editor() -> GodModeEditor:
    screen = pygame.Surface((900, 700))
    return GodModeEditor(world=None, screen=screen,
                         tile_render_system=None, get_map_file=lambda: "")


def _make_tilemap(w: int = 5, h: int = 5, terrain_char: str = "f") -> Tilemap:
    return Tilemap(
        tile_matrix=[[None] * w for _ in range(h)],
        terrain_matrix=[terrain_char * w for _ in range(h)],
        object_matrix=[["."] * w for _ in range(h)],
        tile_size=32, map_width_tiles=w, map_height_tiles=h,
        terrain_visual=[[""] * w for _ in range(h)],
    )


def _set_terrain_char(tilemap: Tilemap, x: int, y: int, char: str) -> None:
    row = tilemap.terrain_matrix[y]
    tilemap.terrain_matrix[y] = row[:x] + char + row[x + 1:]


def _terrain_family() -> dict:
    return next(f for f in SHEET_FAMILIES if f.get("zones"))


class TestTerrainZoneOf(unittest.TestCase):

    def test_resolve_categoria_grama_pura(self):
        fam = _terrain_family()
        col, row = fam["zones"]["grama_pura"]["cells"][0]
        zone = terrain_zone_of(fam, f"{fam['prefix']}_{col}_{row}")
        self.assertEqual(zone["terrain"], "grama")

    def test_resolve_categoria_transicao_shape_inteiro(self):
        fam = _terrain_family()
        prefix = fam["prefix"]
        col0, row0 = fam["zones"]["transicao_terra_grama"]["shapes"]["mancha"]
        # as 9 células do bloco 3x3 inteiro devem resolver pra essa zona
        for dc in range(3):
            for dr in range(3):
                zone = terrain_zone_of(fam, f"{prefix}_{col0+dc}_{row0+dr}")
                self.assertEqual(zone["purpose"], "transition")
                self.assertEqual(zone["from"], "terra")
                self.assertEqual(zone["to"], "grama")

    def test_tile_fora_de_qualquer_zona_devolve_none(self):
        fam = _terrain_family()
        self.assertIsNone(terrain_zone_of(fam, f"{fam['prefix']}_999_999"))

    def test_familia_sem_zones_devolve_none(self):
        self.assertIsNone(terrain_zone_of({"file": "x"}, "x_0_0"))


class TestResolveBlob3x3Piece(unittest.TestCase):

    def test_8_combinacoes_validas(self):
        casos = {
            frozenset({"N"}): (1, 0),
            frozenset({"S"}): (1, 2),
            frozenset({"E"}): (2, 1),
            frozenset({"W"}): (0, 1),
            frozenset({"N", "W"}): (0, 0),
            frozenset({"N", "E"}): (2, 0),
            frozenset({"S", "W"}): (0, 2),
            frozenset({"S", "E"}): (2, 2),
        }
        for missing, offset in casos.items():
            self.assertEqual(resolve_blob3x3_piece(missing), offset)

    def test_combinacoes_sem_peca_devolvem_none(self):
        # frozenset() (vazio) também não tem peça própria — significa
        # "sem contexto real nenhum ainda" (ex: diagonal de clique
        # isolado), tratado à parte como fallback em `_apply_smart_terrain`,
        # nunca como "peça de centro" (27/08/2026, Correção 3).
        for missing in (frozenset(), frozenset({"N", "S"}), frozenset({"E", "W"}),
                        frozenset({"N", "S", "E"}), frozenset({"N", "S", "E", "W"})):
            self.assertIsNone(resolve_blob3x3_piece(missing))


class TestWeakCornerFallback(unittest.TestCase):
    """Correção 4 (27/08/2026) — medição de pixel real mostrou que as 4
    peças de CANTO de `transicao_grama_terra` (mancha E cruz) têm a cor
    nova só ~30-50% no lado "não-de-fora", nunca perto de 100% como na
    zona irmã — usuário reportou "sem transição nenhuma" numa curva de
    caminho de terra sobre grama. `_resolve_piece_offset` troca o canto
    dedicado (fraco) por 1 peça de borda (sempre forte) quando a zona é
    `weak_corners`."""

    def test_zona_weak_corners_troca_canto_por_borda(self):
        fam = _terrain_family()
        trans_zone = fam["zones"]["transicao_grama_terra"]
        self.assertTrue(trans_zone.get("weak_corners"))
        for missing in (frozenset({"N", "W"}), frozenset({"N", "E"}),
                        frozenset({"S", "W"}), frozenset({"S", "E"})):
            offset = GodModeEditor._resolve_piece_offset(missing, trans_zone)
            # tem que ser 1 dos 2 offsets de BORDA (1 lado só) do canto pedido,
            # nunca o offset de canto dedicado
            edge_offsets = {resolve_blob3x3_piece(frozenset({s})) for s in missing}
            self.assertIn(offset, edge_offsets)
            self.assertNotEqual(offset, resolve_blob3x3_piece(missing))

    def test_zona_sem_weak_corners_mantem_o_canto_dedicado(self):
        fam = _terrain_family()
        trans_zone = fam["zones"]["transicao_terra_grama"]
        self.assertFalse(trans_zone.get("weak_corners"))
        missing = frozenset({"N", "W"})
        offset = GodModeEditor._resolve_piece_offset(missing, trans_zone)
        self.assertEqual(offset, resolve_blob3x3_piece(missing))


class TestSmartTerrainBrush(unittest.TestCase):

    def test_clique_isolado_preenche_o_anel_de_8_vizinhos_na_hora(self):
        """1 clique isolado, longe de qualquer área já pintada — o
        usuário confirmou (com print) que espera o anel de 8 vizinhos
        completo JÁ na 1ª pincelada, tratando o clique como o centro
        fixo de um carimbo 3×3 novo (correção 2, PROBLEMAS_ARQUITETURA.md
        §81) — cada vizinho recebe a peça exata da SUA direção."""
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        editor._terrain_smart_style   = "mancha"
        tilemap = _make_tilemap(terrain_char="f")

        editor._apply_smart_terrain(tilemap, 2, 2)

        fam = _terrain_family()
        prefix = fam["prefix"]
        col0, row0 = fam["zones"]["transicao_terra_grama"]["shapes"]["mancha"]
        center_zone = terrain_zone_of(fam, tilemap.terrain_visual[2][2])
        self.assertEqual(center_zone["terrain"], "grama")

        esperado = {
            (0, -1): (1, 0), (0, 1): (1, 2), (1, 0): (2, 1), (-1, 0): (0, 1),
            (-1, -1): (0, 0), (1, -1): (2, 0), (-1, 1): (0, 2), (1, 1): (2, 2),
        }
        for (dx, dy), (oc, orow) in esperado.items():
            expected = f"{prefix}_{col0 + oc}_{row0 + orow}"
            self.assertEqual(tilemap.terrain_visual[2 + dy][2 + dx], expected,
                              f"vizinho ({dx},{dy}) devia ter a peça {expected}")

    def test_arrastar_so_estende_a_borda_mantendo_o_tile_solido_anterior(self):
        """Simula arrasto: 2 cliques em sequência, 1 tile abaixo do
        outro (mesmo padrão do 2º print que o usuário mandou). O tile do
        1º clique (já sólido) não deve ser tocado pelo 2º clique — só os
        vizinhos NOVOS do 2º clique (que ainda são a categoria oposta)
        ganham borda."""
        editor = _make_editor()
        editor._terrain_smart_terrain = "terra"
        editor._terrain_smart_style   = "mancha"
        tilemap = _make_tilemap(w=7, h=7, terrain_char="G")   # tudo grama

        editor._apply_smart_terrain(tilemap, 3, 2)   # 1º clique (tx=3, ty=2)
        primeiro_centro = tilemap.terrain_visual[2][3]
        fam = _terrain_family()
        self.assertEqual(terrain_zone_of(fam, primeiro_centro)["terrain"], "terra")

        editor._apply_smart_terrain(tilemap, 3, 3)   # arrasta 1 tile pra baixo (tx=3, ty=3)

        # o tile do 1º clique continua exatamente como estava (sólido, não virou borda)
        self.assertEqual(tilemap.terrain_visual[2][3], primeiro_centro,
                          "tile já sólido do clique anterior não deveria ter sido tocado")
        # o novo centro também é sólido terra
        self.assertEqual(terrain_zone_of(fam, tilemap.terrain_visual[3][3])["terrain"], "terra")
        # um vizinho do 2º clique que ainda era grama (ex: leste, (4,3)) ganhou borda
        east_zone = terrain_zone_of(fam, tilemap.terrain_visual[3][4])
        self.assertEqual(east_zone["purpose"], "transition")

    def test_vizinho_totalmente_cercado_cura_pra_pura_flor(self):
        """Vizinho leste (3,2) já tem uma peça de borda de uma pincelada
        anterior; ao terminar de cercá-lo (N/S/E já grama, W vira grama
        agora) ele deve virar tile normal (pura/flor) — e o
        `terrain_matrix` dele (categoria REAL, não só o sprite) também
        precisa virar grama, não só a decoração."""
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        tilemap = _make_tilemap(w=5, h=5, terrain_char="f")
        fam = _terrain_family()
        prefix = fam["prefix"]
        col0, row0 = fam["zones"]["transicao_terra_grama"]["shapes"]["mancha"]
        tilemap.terrain_visual[2][3] = f"{prefix}_{col0 + 2}_{row0 + 1}"   # peça de borda antiga
        _set_terrain_char(tilemap, 3, 1, "G")   # N de (3,2)
        _set_terrain_char(tilemap, 3, 3, "G")   # S de (3,2)
        _set_terrain_char(tilemap, 4, 2, "G")   # E de (3,2)

        editor._apply_smart_terrain(tilemap, 2, 2)   # centro vira grama = W de (3,2)

        zone = terrain_zone_of(fam, tilemap.terrain_visual[2][3])
        self.assertIsNotNone(zone)
        self.assertIn(zone["purpose"], ("terrain", "terrain_variant"))
        self.assertEqual(tilemap.terrain_matrix[2][3], "G",
                          "categoria REAL do vizinho curado deveria ter virado grama")

    def test_arrasto_corrige_peca_de_vizinho_decorado_por_pincelada_anterior(self):
        """Pedido explícito do usuário: durante o arrasto, um tile
        decorado numa pincelada anterior deve ser ATUALIZADO quando mais
        contexto real se acumula. Clique 1 (isolado) decora o vizinho
        diagonal NE só via fallback (peça de CANTO, sem contexto real).
        Clique 2 (a leste do 1º, fazendo desse mesmo tile o vizinho NORTE
        dele) dá contexto real o bastante pra recalcular — a peça deve
        MUDAR pra uma peça de BORDA (não mais o canto do fallback)."""
        # Usa "grama" (zona transicao_terra_grama, sem `weak_corners` —
        # ver Correção 4) pra manter o canto do fallback determinístico
        # e garantidamente diferente da borda real, sem depender de
        # sorteio (o sorteio de borda só existe na zona `weak_corners`).
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        editor._terrain_smart_style   = "mancha"
        tilemap = _make_tilemap(w=9, h=9, terrain_char="f")   # tudo terra

        editor._apply_smart_terrain(tilemap, 4, 4)   # clique 1
        depois_do_clique_1 = tilemap.terrain_visual[3][5]   # NE diagonal = (5,3)

        editor._apply_smart_terrain(tilemap, 5, 4)   # clique 2, a leste — (5,3) vira o N dele
        depois_do_clique_2 = tilemap.terrain_visual[3][5]

        fam = _terrain_family()
        self.assertEqual(terrain_zone_of(fam, depois_do_clique_1)["purpose"], "transition")
        self.assertEqual(terrain_zone_of(fam, depois_do_clique_2)["purpose"], "transition")
        self.assertNotEqual(depois_do_clique_1, depois_do_clique_2,
                             "peça deveria ter sido recalculada (canto → borda) com o contexto novo")

    def test_arrasto_reto_nao_deixa_toque_diagonal_pior_sobrescrever_peca_boa(self):
        """Arrasto reto em 3 cliques (leste): o vizinho ao norte do 1º
        clique primeiro ganha borda por fallback (relação ORTOGONAL,
        direta); quando o 3º clique passa a enxergar esse MESMO tile só
        como vizinho DIAGONAL dele (sem ganhar contexto real novo pra
        ele), a peça boa (borda) não pode ser trocada por uma peça pior
        (canto) só por causa dessa relação mais fraca — reproduz o bug
        achado testando arrasto em várias direções (Correção 3)."""
        editor = _make_editor()
        editor._terrain_smart_terrain = "terra"
        editor._terrain_smart_style   = "mancha"
        tilemap = _make_tilemap(w=9, h=9, terrain_char="G")

        editor._apply_smart_terrain(tilemap, 4, 4)
        peca_apos_clique1 = tilemap.terrain_visual[3][4]   # N do clique 1 — relação ortogonal

        editor._apply_smart_terrain(tilemap, 5, 4)   # ainda ortogonal-irrelevante pra (4,3)
        editor._apply_smart_terrain(tilemap, 6, 4)   # (4,3) agora é só diagonal dele

        peca_final = tilemap.terrain_visual[3][4]
        self.assertEqual(peca_final, peca_apos_clique1,
                          "peça de borda boa (ortogonal) não deveria ter sido trocada por canto (diagonal)")

    def test_pintar_grama_sobre_grama_so_sorteia_variante_nao_mexe_em_vizinho(self):
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        tilemap = _make_tilemap(terrain_char="G")   # tudo grama

        editor._apply_smart_terrain(tilemap, 2, 2)

        fam = _terrain_family()
        center_zone = terrain_zone_of(fam, tilemap.terrain_visual[2][2])
        self.assertEqual(center_zone["terrain"], "grama")

        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                self.assertEqual(tilemap.terrain_visual[2 + dy][2 + dx], "",
                                  "vizinho não deveria ter sido tocado")

    def test_vizinho_que_e_piso_nunca_e_tocado(self):
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        tilemap = _make_tilemap(terrain_char="f")   # tudo terra

        fam = _terrain_family()
        floor_col, floor_row = fam["zones"]["piso_sobre_terra"]["cells"][0]
        floor_id = f"{fam['prefix']}_{floor_col}_{floor_row}"
        tilemap.terrain_visual[2][3] = floor_id   # vizinho direto (dx=1,dy=0) já é piso

        editor._apply_smart_terrain(tilemap, 2, 2)

        self.assertEqual(tilemap.terrain_visual[2][3], floor_id,
                          "vizinho que é piso não deveria virar transição")

    def test_variante_nunca_repete_a_ultima(self):
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        fam = _terrain_family()
        prefix = fam["prefix"]
        pure = [f"{prefix}_{c}_{r}" for c, r in fam["zones"]["grama_pura"]["cells"]]
        flor = [f"{prefix}_{c}_{r}" for c, r in fam["zones"]["grama_flor"]["cells"]]

        prev = editor._pick_terrain_variant(pure, flor)
        for _ in range(50):
            picked = editor._pick_terrain_variant(pure, flor)
            self.assertNotEqual(picked, prev)
            self.assertIn(picked, pure + flor)
            prev = picked


class TestFloorBrushPool(unittest.TestCase):

    def test_pool_do_piso_sobre_grama_nunca_inclui_tile_parcial(self):
        fam = _terrain_family()
        zone = fam["zones"]["piso_sobre_grama"]
        ids = {f"{fam['prefix']}_{c}_{r}" for c, r in zone["cells"]}
        # célula de transição sabidamente fora da zona de piso
        col, row = fam["zones"]["transicao_terra_grama"]["shapes"]["mancha"]
        partial_id = f"{fam['prefix']}_{col}_{row}"
        self.assertNotIn(partial_id, ids)


class TestSmartBrushActivationUI(unittest.TestCase):

    def test_clicar_no_label_grama_ativa_pincel_inteligente(self):
        editor = _make_editor()
        rect = pygame.Rect(10, 10, 80, 20)
        editor._terrain_smart_rects = [(rect, "grama")]
        editor._scroll_viewport_h = 500
        editor._viewport_top      = 0

        editor._handle_panel_click((20, 15), 1)

        self.assertEqual(editor._terrain_smart_terrain, "grama")
        self.assertIn(editor._terrain_smart_style, ("mancha", "cruz"))
        self.assertIsNone(editor._terrain_last_id)

    def test_clicar_em_celula_especifica_cancela_pincel_inteligente(self):
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        editor._terrain_smart_style   = "mancha"
        editor._scroll_viewport_h = 500
        editor._viewport_top      = 0

        rect = pygame.Rect(10, 40, 30, 30)
        editor._sheet_cell_rects = [(rect, "ter_0_0", "Chão (0,0)", (58, 100, 48))]

        editor._handle_panel_click((20, 50), 1)

        self.assertIsNone(editor._terrain_smart_terrain)
        self.assertIsNone(editor._terrain_smart_style)
        self.assertEqual(editor._sel_char, "ter_0_0")

    def test_clicar_no_label_piso_ativa_pincel_de_grupo_normal(self):
        editor = _make_editor()
        rect = pygame.Rect(10, 10, 80, 20)
        editor._terrain_floor_group_rects = [(rect, ["ter_0_6", "ter_1_6"], "Piso (aleatório)")]
        editor._scroll_viewport_h = 500
        editor._viewport_top      = 0

        editor._handle_panel_click((20, 15), 1)

        self.assertEqual(editor._brush_group_ids, ["ter_0_6", "ter_1_6"])
        self.assertEqual(editor._sel_name, "Piso (aleatório)")
        self.assertIsNone(editor._terrain_smart_terrain)
        self.assertIsNone(editor._terrain_smart_style)

    def test_chao_caverna_tem_pincel_aleatorio_so_com_tiles_registrados(self):
        """22/10/2026 — "Chão Caverna" (random_brushes da família cvg):
        só células de textura de chão, todas registradas e andáveis."""
        from engine.tileset import SHEET_FAMILIES, SHEET_TILE_MAP, OBJECT_MAPPING
        fam = next(f for f in SHEET_FAMILIES if f["prefix"] == "cvg")
        cells = fam["random_brushes"]["Chão Caverna"]
        self.assertEqual(len(cells), 30)
        for c, r in cells:
            tid = f"cvg_{c}_{r}"
            self.assertIn(tid, SHEET_TILE_MAP)
            self.assertFalse(OBJECT_MAPPING[tid].is_solid)

    def test_pintar_borda_de_lava_bloqueia_na_hora_e_chao_libera(self):
        """23/10/2026, bug relatado: tile de transição chão→lava/água/
        buraco pintado no F10 continuava andável até recarregar o mapa."""
        from engine.tileset import SHEET_FAMILIES
        fam = next(f for f in SHEET_FAMILIES if f["prefix"] == "cvg")
        solid = "cvg_%d_%d" % next(iter(fam["cell_underlying"]))
        editor = _make_editor()
        tm = _make_tilemap(terrain_char="_")
        editor._get_tilemap = lambda: tm
        editor._tile_render = type("R", (), {"invalidate_cache": lambda self: None})()
        editor._sel_char = solid
        editor._apply(2, 2)
        self.assertEqual(tm.terrain_matrix[2][2], "W")
        self.assertTrue(tm.tile_matrix[2][2].is_solid)
        self.assertEqual(tm.terrain_visual[2][2], solid)
        editor._sel_char = "cvg_6_0"
        editor._apply(2, 2)
        self.assertFalse(tm.tile_matrix[2][2].is_solid)
        self.assertEqual(tm.terrain_visual[2][2], "cvg_6_0")

    def test_pintar_tile_andavel_nao_mexe_no_char_de_terreno(self):
        """Grama de sheet sobre terra "f": as duas andáveis, char continua
        "f" (pincel inteligente lê o terrain_matrix)."""
        editor = _make_editor()
        tm = _make_tilemap(terrain_char="f")
        editor._get_tilemap = lambda: tm
        editor._tile_render = type("R", (), {"invalidate_cache": lambda self: None})()
        editor._sel_char = "cvg_6_0"
        editor._apply(1, 1)
        self.assertEqual(tm.terrain_matrix[1][1], "f")

    def _cave_editor(self, mode):
        from engine.tileset import TILE_MAPPING
        editor = _make_editor()
        tm = _make_tilemap(20, 20, terrain_char=".")
        tm.tile_matrix = [[TILE_MAPPING["."]] * 20 for _ in range(20)]
        editor._get_tilemap = lambda: tm
        editor._tile_render = type("R", (), {"invalidate_cache": lambda self: None})()
        editor._cave_brush = mode
        return editor, tm

    def test_pincel_caverna_pinta_carimbo_3x3_montado(self):
        """24/10/2026 — pincel "Parede de caverna (inteligente)": 1 clique
        = parede 3x3 já com as peças certas e colisão."""
        editor, tm = self._cave_editor("full")
        editor._apply(10, 10)
        O = tm.object_matrix
        self.assertEqual((O[9][9], O[10][9], O[11][11]), ("cv001", "cv009", "cv016"))
        self.assertIn(O[11][10], ("cv014", "cv015"))
        self.assertEqual(O[10][10], {"cv014": "cv010", "cv015": "cv011"}[O[11][10]])
        for y in (9, 10, 11):
            for x in (9, 10, 11):
                self.assertTrue(tm.tile_matrix[y][x].is_solid, (x, y))
        self.assertFalse(tm.tile_matrix[12][10].is_solid)

    def test_pincel_caverna_apagar_remove_e_limpa_sobra_fina(self):
        editor, tm = self._cave_editor("full")
        editor._apply(10, 10)
        editor._apply(13, 10)                      # parede 6x3
        editor._cave_stroke(12, 10, erase=True)    # tira o meio: sobra de 2 de largura sai junto
        walls = [(x, y) for y in range(20) for x in range(20)
                 if tm.object_matrix[y][x] != "." or tm.terrain_matrix[y][x] == "c"]
        self.assertEqual(walls, [])
        self.assertFalse(any(tm.tile_matrix[y][x].is_solid for y in range(20) for x in range(20)))

    def test_pincel_caverna_so_topo_sem_face(self):
        editor, tm = self._cave_editor("top")
        editor._apply(10, 10)
        editor._apply(12, 10)                      # 2x2 + 2x2 = parede 4x2
        row = tm.object_matrix[10][9:13]
        self.assertEqual(row, ["cv020", "cv031", "cv031", "cv021"])

    def test_clique_no_mapa_considera_zoom(self):
        """22/10/2026 — God Mode passou a editar com zoom: clique em tela
        é dividido pela escala antes de virar tile."""
        editor = _make_editor()
        editor.view_scale = (3.0, 3.0)
        self.assertEqual(editor._screen_to_tile(95, 97, 0, 0), (0, 1))
        self.assertEqual(editor._screen_to_tile(96, 0, 64, 32), (3, 1))
        r = editor._tile_screen_rect(3, 1, 64, 32)
        self.assertEqual((r.x, r.y, r.w, r.h), (96, 0, 96, 96))

    def test_trocar_de_aba_cancela_pincel_inteligente(self):
        editor = _make_editor()
        editor._terrain_smart_terrain = "grama"
        editor._terrain_smart_style   = "cruz"
        editor._terrain_last_id       = "ter_10_0"
        editor._tab_rects = [pygame.Rect(0, 0, 40, 20), pygame.Rect(40, 0, 40, 20)]

        editor._handle_panel_click((10, 10), 1)

        self.assertIsNone(editor._terrain_smart_terrain)
        self.assertIsNone(editor._terrain_smart_style)
        self.assertIsNone(editor._terrain_last_id)


if __name__ == "__main__":
    unittest.main()
