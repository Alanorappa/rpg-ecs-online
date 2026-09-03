"""
tests/test_god_mode_random_brush.py — pincel aleatório por grupo no
editor de mapa (F10, aba Objetos), 26/08/2026, pedido do usuário.

Clicar no NOME de um grupo (só famílias com "groups" em
`OBJECT_SHEET_FAMILIES` — ver `engine/tileset.py`/`PROBLEMAS_
ARQUITETURA.md` §78) ativa um pincel que, a cada tile pintado no mapa,
sorteia 1 elemento do grupo — nunca repetindo o último colocado. Clicar
numa célula específica (ou trocar de aba) cancela o pincel aleatório,
voltando ao comportamento normal (1 tile fixo).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from ui.god_mode import GodModeEditor


def _make_editor() -> GodModeEditor:
    screen = pygame.Surface((900, 700))
    return GodModeEditor(world=None, screen=screen,
                         tile_render_system=None, get_map_file=lambda: "")


class TestResolveBrushChar(unittest.TestCase):

    def test_pincel_normal_devolve_sel_char_direto(self):
        editor = _make_editor()
        editor._sel_char = "pl_t3"
        self.assertIsNone(editor._brush_group_ids)
        self.assertEqual(editor._resolve_brush_char(), "pl_t3")

    def test_pincel_aleatorio_nunca_repete_o_ultimo(self):
        editor = _make_editor()
        editor._brush_group_ids = ["a", "b", "c"]
        editor._brush_last_id   = None

        prev = editor._resolve_brush_char()
        for _ in range(50):
            picked = editor._resolve_brush_char()
            self.assertNotEqual(picked, prev, "2 escolhas seguidas nunca deveriam ser iguais")
            self.assertIn(picked, ["a", "b", "c"])
            prev = picked

    def test_pincel_aleatorio_com_1_tile_so_nao_trava(self):
        editor = _make_editor()
        editor._brush_group_ids = ["unico"]
        editor._brush_last_id   = "unico"
        # sem o fallback "or self._brush_group_ids", a lista de escolhas
        # ficaria vazia (random.choice([]) levanta IndexError).
        self.assertEqual(editor._resolve_brush_char(), "unico")


class TestGroupLabelClickActivatesBrush(unittest.TestCase):

    def test_clicar_no_nome_do_grupo_ativa_pincel(self):
        editor = _make_editor()
        rect = pygame.Rect(10, 10, 80, 20)
        editor._obj_group_label_rects = [(rect, "Árvores", ["pl_t1", "pl_t2"], (46, 92, 40))]
        editor._scroll_viewport_h = 500
        editor._viewport_top      = 0

        editor._handle_panel_click((20, 15), 1)

        self.assertEqual(editor._brush_group_ids, ["pl_t1", "pl_t2"])
        self.assertIsNone(editor._brush_last_id)
        self.assertIn("aleatório", editor._sel_name)

    def test_clicar_em_celula_especifica_cancela_pincel_aleatorio(self):
        editor = _make_editor()
        editor._brush_group_ids = ["pl_t1", "pl_t2"]
        editor._scroll_viewport_h = 500
        editor._viewport_top      = 0

        rect = pygame.Rect(10, 40, 30, 30)
        editor._obj_sheet_cell_rects = [(rect, "pl_t5", "Árvores: pl_t5", (46, 92, 40))]

        editor._handle_panel_click((20, 50), 1)

        self.assertIsNone(editor._brush_group_ids,
                          "escolher 1 tile específico deveria cancelar o pincel aleatório")
        self.assertEqual(editor._sel_char, "pl_t5")

    def test_trocar_de_aba_cancela_pincel_aleatorio(self):
        editor = _make_editor()
        editor._brush_group_ids = ["pl_t1", "pl_t2"]
        editor._brush_last_id   = "pl_t1"
        editor._tab_rects = [pygame.Rect(0, 0, 40, 20), pygame.Rect(40, 0, 40, 20)]

        editor._handle_panel_click((10, 10), 1)   # clica na aba 0 (Terreno)

        self.assertIsNone(editor._brush_group_ids)
        self.assertIsNone(editor._brush_last_id)


if __name__ == "__main__":
    unittest.main()
