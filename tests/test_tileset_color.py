"""
tests/test_tileset_color.py — cor por grupo/tile dentro de um arquivo de
tileset (26/08/2026, pedido do usuário). Antes, 1 cor RGB só valia pro
arquivo PNG inteiro, então 2 tipos de terreno/objeto bem diferentes
visualmente no MESMO arquivo (ex: grama e terra em "TX Tileset Terrain")
ficavam indistinguíveis no minimapa/mapa-múndi. Cobre:

  1. `engine/tileset.py` — "groups" no modo catálogo (OBJECT_SHEET_FAMILIES),
     "color_overrides" no modo grade (SHEET_FAMILIES) — mirror exato do
     mecanismo já existente pra `collision_overrides`.
  2. `game.py::_merge_display_matrix` — parou de truncar token multi-char
     pro 1º caractere (bug achado durante a investigação: qualquer objeto
     de sheet mais novo, ex: "vp_001", truncava pra uma letra que não
     bate com NENHUM TileType cadastrado, então nunca aparecia colorido
     no minimapa, mesmo com a cor certa cadastrada em tileset.py).
  3. `ui/map_overlay.py::load_map` — pinta o footprint de colisão inteiro
     de um objeto multi-tile, não só o tile-âncora.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

import engine.tileset as ts


class TestObjectSheetGroups(unittest.TestCase):
    """Modo catálogo com "groups" — cada grupo tem sua própria cor, sem
    vazar pro outro grupo do mesmo arquivo."""

    _REAL_FILE = "TX Grades"   # PNG real já existente em assets/tiles

    def setUp(self):
        self._orig_families = list(ts.OBJECT_SHEET_FAMILIES)
        self._added_ids = ["_testarv_1", "_testarb_1"]
        self.addCleanup(self._restore)

    def _restore(self):
        ts.OBJECT_SHEET_FAMILIES[:] = self._orig_families
        for tid in self._added_ids:
            ts.OBJECT_MAPPING.pop(tid, None)
            ts.OBJECT_CHARS.discard(tid)
            ts.OBJECT_SHEET_TILE_MAP.pop(tid, None)
            ts.OBJECT_UNDERLYING.pop(tid, None)

    def test_grupos_diferentes_no_mesmo_arquivo_tem_cores_diferentes(self):
        fam = {
            "file": self._REAL_FILE,
            "groups": {
                "arvores": {
                    "prefix": "_testarv",
                    "label": "Arvores Teste",
                    "color": (10, 200, 10),
                    "underlying_terrain": "G",
                    "tiles": [("1", 0, 0, 32, 64, None)],
                },
                "arbustos": {
                    "prefix": "_testarb",
                    "label": "Arbustos Teste",
                    "color": (10, 100, 200),
                    "underlying_terrain": "G",
                    "tiles": [("1", 32, 0, 32, 64, None)],
                },
            },
        }
        ts.OBJECT_SHEET_FAMILIES.append(fam)
        ts.discover_object_sheet_tiles()

        self.assertEqual(ts.OBJECT_MAPPING["_testarv_1"].color, (10, 200, 10))
        self.assertEqual(ts.OBJECT_MAPPING["_testarb_1"].color, (10, 100, 200),
                         "grupo 'arbustos' não deveria herdar a cor de 'arvores'")


class TestTxVillagePlantGroupsMigration(unittest.TestCase):
    """"TX Village Plant" (26/08/2026) foi o exemplo real migrado pra
    "groups" — 8 categorias (árvores/arbustos/trigo/repolho/abóbora/
    cenoura/grama fina/flores) que antes compartilhavam 1 cor só. Mapas
    reais (`maps/map_1_objects.csv`, `maps/moba_battleground_objects.csv`)
    já referenciam ids desse arquivo (ex: "pl_t1", "pl_b3") — o `prefix`
    tem que continuar "pl" em TODO grupo, senão essas referências
    quebram (viram "pltree_1" etc., que não existe em lugar nenhum)."""

    def test_ids_preexistentes_continuam_resolvendo(self):
        # Amostra de cada categoria — não precisa ser exaustivo, só provar
        # que o prefixo comum ("pl") sobreviveu à migração pra grupos.
        for tid in ("pl_t1", "pl_b1", "pl_w1", "pl_c1", "pl_p1", "pl_cr1",
                   "pl_gb1", "pl_fl1"):
            self.assertIn(tid, ts.OBJECT_MAPPING, f"{tid} deveria continuar existindo")

    def test_grupos_diferentes_tem_cores_diferentes(self):
        arvore  = ts.OBJECT_MAPPING["pl_t1"].color
        arbusto = ts.OBJECT_MAPPING["pl_b1"].color
        trigo   = ts.OBJECT_MAPPING["pl_w1"].color
        self.assertNotEqual(arvore, arbusto)
        self.assertNotEqual(arvore, trigo)
        self.assertNotEqual(arbusto, trigo)

    def test_dados_de_colisao_e_visao_preservados_na_migracao(self):
        # t1 tinha collision_rect/vision_rect/vision_height explícitos —
        # a migração pra groups não pode ter mudado esses valores, só a cor.
        # vision_rect == collision_rect desde 27/08/2026 (§82 — só o tronco
        # bloqueia visão, não a copa inteira; era "full" antes disso).
        t1 = ts.OBJECT_MAPPING["pl_t1"]
        self.assertEqual(t1.collision_rect, (32, 96, 32, 32))
        self.assertEqual(t1.vision_rect, (32, 96, 32, 32))
        self.assertEqual(t1.vision_height, 2)
        b1 = ts.OBJECT_MAPPING["pl_b1"]
        self.assertEqual(b1.vision_rect, "base_row")


class TestGroupTileIds(unittest.TestCase):
    """`group_tile_ids()` — ponto único de verdade (26/08/2026) reusado
    pelo painel de Objetos do God Mode e pelo pincel aleatório por
    grupo."""

    def test_devolve_ids_completos_do_grupo(self):
        group = {
            "prefix": "pl",
            "tiles": [("t1", 0, 0, 32, 32, None), ("t2", 32, 0, 32, 32, None)],
        }
        self.assertEqual(ts.group_tile_ids(group), ["pl_t1", "pl_t2"])


class TestGridColorOverrides(unittest.TestCase):
    """Modo grade (SHEET_FAMILIES) — color_overrides por (col,row), cai
    no `color` da família fora do override. Mirror exato de
    collision_overrides."""

    _REAL_FILE = "TX Grades"

    def setUp(self):
        self._orig_families = list(ts.SHEET_FAMILIES)
        self._added_ids = ["_testgrid_0_0", "_testgrid_1_0"]
        self.addCleanup(self._restore)

    def _restore(self):
        ts.SHEET_FAMILIES[:] = self._orig_families
        for tid in self._added_ids:
            ts.OBJECT_MAPPING.pop(tid, None)
            ts.OBJECT_CHARS.discard(tid)
            ts.SHEET_TILE_MAP.pop(tid, None)
            ts.OBJECT_UNDERLYING.pop(tid, None)

    def test_color_overrides_aplica_por_celula_cai_no_default_fora_dele(self):
        fam = {
            "file": self._REAL_FILE,
            "prefix": "_testgrid",
            "tile_w": 32, "tile_h": 64,
            "is_solid": False,
            "label": "Teste Grid",
            "color": (50, 50, 50),
            "color_overrides": {(1, 0): (200, 0, 0)},
            "underlying_terrain": "G",
        }
        ts.SHEET_FAMILIES.append(fam)
        ts.discover_sheet_tiles()

        self.assertEqual(ts.OBJECT_MAPPING["_testgrid_0_0"].color, (50, 50, 50),
                         "sem override, cai no default da família")
        self.assertEqual(ts.OBJECT_MAPPING["_testgrid_1_0"].color, (200, 0, 0),
                         "célula com override usa a cor própria")


class TestMapOverlayFootprint(unittest.TestCase):
    """Minimapa/mapa-múndi deveriam colorir o footprint de colisão
    inteiro de um objeto multi-tile, não só o tile-âncora onde ele foi
    posicionado (pedido do usuário, 26/08/2026 — "deveria considerar o
    tamanho da colisão, que também é configurado no mesmo arquivo")."""

    def setUp(self):
        from ui.map_overlay import MapOverlay
        self._orig_tile = ts.OBJECT_MAPPING.get("_testfoot")
        # sprite 32x64, colisão "full" -> footprint cobre a âncora + 1 tile acima
        self._test_tile = ts.TileType(
            name="TestFootprintObj", color=(255, 0, 0), is_solid=True,
            sprite_px_w=32, sprite_px_h=64, collision_rect="full",
        )
        ts.OBJECT_MAPPING["_testfoot"] = self._test_tile
        self.addCleanup(self._restore)
        screen = pygame.Surface((320, 240))
        self.overlay = MapOverlay(screen)

    def _restore(self):
        if self._orig_tile is None:
            ts.OBJECT_MAPPING.pop("_testfoot", None)
        else:
            ts.OBJECT_MAPPING["_testfoot"] = self._orig_tile

    def test_footprint_pinta_alem_da_ancora(self):
        offsets = ts.get_collision_offsets(self._test_tile)
        self.assertIn((0, -1), offsets,
                      "pré-condição do teste: footprint 'full' num sprite 32x64 devia subir 1 tile")

        matrix = [
            ["G", "G", "G"],
            ["G", "_testfoot", "G"],
            ["G", "G", "G"],
        ]
        self.overlay.load_map(matrix, "test_map")
        surf = self.overlay._surfaces["test_map"]

        self.assertEqual(surf.get_at((1, 1))[:3], (255, 0, 0), "âncora deveria estar pintada")
        self.assertEqual(surf.get_at((1, 0))[:3], (255, 0, 0),
                         "footprint extra (1 tile acima da âncora) deveria estar pintado também")
        # Vizinhos fora do footprint continuam com a cor de terreno (grama)
        self.assertNotEqual(surf.get_at((0, 0))[:3], (255, 0, 0))


class TestGodModeObjectPanelGroups(unittest.TestCase):
    """Crash real relatado pelo usuário (26/08/2026): abrir o editor de
    mapa (F10) e clicar na aba "Objetos" derrubava o jogo com `KeyError:
    'prefix'` — `ui/god_mode.py::_render_content` lia `fam["prefix"]`/
    `fam["color"]` direto em CADA família de `OBJECT_SHEET_FAMILIES`,
    sem saber que uma família podia usar `"groups"` (formato novo, sem
    `prefix`/`color` no nível da família — eles moraram pra dentro de
    cada grupo). "TX Village Plant" (migrada pra groups na tarefa
    anterior) é exatamente esse caso — o painel nunca tinha sido
    exercitado com uma família assim."""

    def test_aba_objetos_nao_crasha_com_familia_em_groups(self):
        from ui.god_mode import GodModeEditor
        screen = pygame.Surface((900, 700))
        editor = GodModeEditor(world=None, screen=screen,
                               tile_render_system=None, get_map_file=lambda: "")
        editor._tab = 1   # aba "Objetos" — a que crashou de verdade

        cy = editor._render_content(screen, x0=10, cy=10, avail_w=400, sc=1.0)

        self.assertIsInstance(cy, int)
        # "TX Village Plant" tem 8 grupos — o painel deveria ter desenhado
        # pelo menos 1 label por grupo (label completo, não a família
        # inteira genérica "Vegetação").
        rendered_labels = {entry[2].split(":")[0] for entry in editor._obj_sheet_cell_rects}
        for expected in ("Árvores", "Arbustos", "Trigo", "Repolho",
                         "Abóbora", "Cenoura", "Grama Fina", "Flores"):
            self.assertIn(expected, rendered_labels,
                          f"grupo '{expected}' deveria aparecer no painel de Objetos")


class TestMergeDisplayMatrixNoTruncation(unittest.TestCase):
    """game.py::_merge_display_matrix parou de truncar token multi-char
    pro 1º caractere (26/08/2026) — a truncagem antiga fazia objetos de
    sheet mais novos (ex: "vp_001") nunca aparecerem coloridos no
    minimapa, porque "v" não bate com nenhum TileType cadastrado."""

    def test_objeto_multi_char_nao_e_truncado(self):
        from game import _merge_display_matrix
        result = _merge_display_matrix(["GG"], [["vp_001", "."]])
        self.assertEqual(result[0][0], "vp_001", "token completo, não truncado pro 1º char")
        self.assertEqual(result[0][1], "G")

    def test_terrain_visual_usado_quando_sem_objeto(self):
        from game import _merge_display_matrix
        result = _merge_display_matrix(["GG"], [], [["ter_2_3", ""]])
        self.assertEqual(result[0][0], "ter_2_3")
        self.assertEqual(result[0][1], "G")

    def test_objeto_tem_prioridade_sobre_visual_de_terreno(self):
        from game import _merge_display_matrix
        result = _merge_display_matrix(["G"], [["vp_001"]], [["ter_2_3"]])
        self.assertEqual(result[0][0], "vp_001")


if __name__ == "__main__":
    unittest.main()
