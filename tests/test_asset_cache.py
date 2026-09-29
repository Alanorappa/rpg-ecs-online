"""
tests/test_asset_cache.py — cache único de imagem/som por caminho de
arquivo (28/09/2026). Ponto único de verdade: todo gerenciador de
asset do cliente (TileSpriteManager, IconManager, SoundManager, etc.)
passou a rotear seu carregamento "cru" por aqui, pra que o
pré-carregamento (`ui/asset_preloader.py`) tenha efeito real — sem
isso, o pré-carregamento decodificaria cada arquivo e o resultado
seria jogado fora (cada gerenciador tinha cache próprio, nunca
compartilhado).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))
pygame.mixer.init()

from ui import asset_cache


class TestGetImage(unittest.TestCase):
    def setUp(self):
        asset_cache._images.clear()

    def test_carrega_arquivo_real_e_cacheia_por_caminho(self):
        # Qualquer PNG real do projeto serve — usa um pequeno e estável.
        path = "assets/effects/frozen_root.png"
        if not os.path.isfile(path):
            self.skipTest("assets/effects/frozen_root.png nao existe neste checkout")
        surf1 = asset_cache.get_image(path)
        self.assertIsNotNone(surf1)
        surf2 = asset_cache.get_image(path)
        self.assertIs(surf1, surf2, "2a chamada deveria devolver o MESMO objeto (cache)")

    def test_arquivo_inexistente_devolve_none_sem_lancar(self):
        surf = asset_cache.get_image("assets/isso_nao_existe_de_verdade.png")
        self.assertIsNone(surf)
        # 2a chamada também não deve tentar reabrir o arquivo (cache do None)
        surf2 = asset_cache.get_image("assets/isso_nao_existe_de_verdade.png")
        self.assertIsNone(surf2)


class TestGetSound(unittest.TestCase):
    def setUp(self):
        asset_cache._sounds.clear()

    def test_arquivo_inexistente_devolve_none_sem_lancar(self):
        snd = asset_cache.get_sound("assets/isso_nao_existe_de_verdade.ogg")
        self.assertIsNone(snd)

    def test_carrega_som_real_e_cacheia_por_caminho(self):
        import glob
        candidates = glob.glob("assets/sounds/sfx/*.ogg")
        if not candidates:
            self.skipTest("nenhum .ogg em assets/sounds/sfx neste checkout")
        path = candidates[0]
        snd1 = asset_cache.get_sound(path)
        self.assertIsNotNone(snd1)
        snd2 = asset_cache.get_sound(path)
        self.assertIs(snd1, snd2)


if __name__ == "__main__":
    unittest.main()
