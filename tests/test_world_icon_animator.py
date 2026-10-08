"""
tests/test_world_icon_animator.py
ui/world_icon_animator.py — ícone animado de mundo (09/10/2026, ver
CLAUDE.md). Testa contra os PNGs reais de quest
(assets/sprites/quest_available.png etc, tira horizontal de 10 quadros
de 32x32) — mesma disciplina de testar o objeto real, não só um mock.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from ui import world_icon_animator as wia


class TestGetFrame(unittest.TestCase):

    def setUp(self):
        wia._sheets.clear()
        wia._frame_cache.clear()

    def test_arquivo_inexistente_devolve_none(self):
        self.assertIsNone(wia.get_frame("nao_existe_de_jeito_nenhum", 32, 0))

    def test_tamanho_nativo_devolve_frame_do_tamanho_pedido(self):
        frame = wia.get_frame("quest_available", 32, 0)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.get_size(), (32, 32))

    def test_tamanho_diferente_do_nativo_reescala(self):
        frame = wia.get_frame("quest_available", 16, 0)
        self.assertEqual(frame.get_size(), (16, 16))

    def test_avanca_de_quadro_com_o_tempo(self):
        """10 quadros, DEFAULT_FRAME_MS=120 — quadro 0 != quadro em
        now_ms=120 (1 quadro adiante), prova que o tempo realmente
        avança a animação."""
        f0 = wia.get_frame("quest_available", 32, 0)
        f1 = wia.get_frame("quest_available", 32, wia.DEFAULT_FRAME_MS)
        # Mesmo tamanho, mas regiões diferentes do sheet — comparar bytes.
        self.assertNotEqual(pygame.image.tobytes(f0, "RGBA"),
                            pygame.image.tobytes(f1, "RGBA"))

    def test_loop_volta_ao_quadro_0_apos_ciclo_completo(self):
        """Sheet real tem 10 quadros — em now_ms = 10*frame_ms deveria
        estar de volta exatamente no quadro 0."""
        f0 = wia.get_frame("quest_available", 32, 0)
        f_loop = wia.get_frame("quest_available", 32, 10 * wia.DEFAULT_FRAME_MS)
        self.assertEqual(pygame.image.tobytes(f0, "RGBA"),
                         pygame.image.tobytes(f_loop, "RGBA"))

    def test_cache_nunca_ultrapassa_o_teto(self):
        old_max = wia._CACHE_MAX
        wia._CACHE_MAX = 5
        try:
            for i in range(50):
                wia.get_frame("quest_available", 16 + i, 0)
                self.assertLessEqual(len(wia._frame_cache), wia._CACHE_MAX)
        finally:
            wia._CACHE_MAX = old_max

    def test_os_3_sprites_de_quest_existem_e_tem_10_quadros(self):
        for name in ("quest_available", "quest_incomplete", "quest_complete"):
            sheet = wia._load_sheet(name)
            self.assertIsNotNone(sheet, f"{name}.png não encontrado")
            frame_h = sheet.get_height()
            self.assertEqual(sheet.get_width() // frame_h, 10,
                             f"{name}.png: esperado 10 quadros")


if __name__ == "__main__":
    unittest.main()
