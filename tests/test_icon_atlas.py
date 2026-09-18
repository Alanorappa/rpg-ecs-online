"""
tests/test_icon_atlas.py — 17/09/2026, ver PROJECT_WIDE_REVIEW.md item
#10 / PROBLEMAS_ARQUITETURA.md §181: ícone pequeno (32×32) era carregado
como arquivo PNG PRÓPRIO (pygame.image.load por nome) — com o catálogo
crescendo (skill + talento reaproveitando ícone de skill + item),
agrupar em atlas grandes (poucos PNGs, um por categoria) reduz o
número de arquivos abertos. Mesmo padrão já usado pra tiles
(ui/tile_sprite_manager.py::_get_sheet_region).

Prova: (1) `_IconAtlasSheets.get_region()` extrai a sub-Surface certa
pra um ícone catalogado, e `None` pra um não catalogado; (2)
`IconManager.get()` usa o atlas quando o ícone está catalogado, e cai
pro arquivo individual de sempre quando não está — sem quebrar nenhum
ícone que ainda não foi migrado pro atlas; (3) os PNGs de atlas reais
gerados por tools/build_icon_atlas.py batem pixel a pixel com os
arquivos individuais de origem, pra uma amostra de ícones catalogados.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from ui.icon_atlas import _IconAtlasSheets, CELL_SIZE
from ui.icon_manager import IconManager


class TestIconAtlasSheets(unittest.TestCase):

    def setUp(self):
        self.sheets = _IconAtlasSheets()

    def test_icone_nao_catalogado_devolve_none(self):
        self.assertIsNone(self.sheets.get_region("isso_nao_existe_em_nenhum_catalogo"))

    def test_icone_catalogado_devolve_surface_do_tamanho_da_celula(self):
        region = self.sheets.get_region("item_iron_sword")
        self.assertIsNotNone(region, "item_iron_sword deveria estar catalogado (gerado por build_icon_atlas.py)")
        self.assertEqual(region.get_size(), (CELL_SIZE, CELL_SIZE))

    def test_dois_icones_diferentes_devolvem_regioes_diferentes(self):
        r1 = self.sheets.get_region("item_iron_sword")
        r2 = self.sheets.get_region("item_hp_potion")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        # Slots diferentes -> bytes de pixel diferentes (ícones reais distintos)
        self.assertNotEqual(pygame.image.tobytes(r1, "RGBA"),
                            pygame.image.tobytes(r2, "RGBA"))

    def test_sheet_cacheada_nao_recarrega_arquivo(self):
        r1 = self.sheets.get_region("item_iron_sword")
        self.assertEqual(len(self.sheets._sheets), 1)
        self.sheets.get_region("item_hp_potion")   # mesmo arquivo de atlas
        self.assertEqual(len(self.sheets._sheets), 1,
                         "2º ícone do MESMO atlas não deveria recarregar o PNG")


class TestIconManagerAtlasFallback(unittest.TestCase):

    def setUp(self):
        self.mgr = IconManager()

    def test_icone_catalogado_vem_do_atlas(self):
        surf = self.mgr.get("item_iron_sword", 32)
        self.assertIsNotNone(surf)
        self.assertEqual(surf.get_size(), (32, 32))

    def test_icone_nao_catalogado_cai_pro_arquivo_individual(self):
        # map_death.png existe em assets/icons/ mas NÃO está no atlas
        # (categoria fora de escopo desta migração) — precisa continuar
        # funcionando pelo caminho de sempre.
        surf = self.mgr.get("map_death", 32)
        self.assertIsNotNone(surf, "ícone fora do atlas deveria cair pro fallback de arquivo individual")

    def test_icone_inexistente_devolve_none(self):
        self.assertIsNone(self.mgr.get("nao_existe_em_lugar_nenhum_xyz", 32))

    def test_escala_pro_tamanho_pedido(self):
        surf = self.mgr.get("item_iron_sword", 64)
        self.assertEqual(surf.get_size(), (64, 64))


class TestGeneratedAtlasMatchesSourceFiles(unittest.TestCase):
    """Confirma que os PNGs de atlas REAIS (gerados por
    tools/build_icon_atlas.py, já commitados em assets/icons/) batem
    pixel a pixel com os arquivos de origem — não testa o script em si
    (dev tool, sem suíte própria, mesmo padrão dos outros scripts de
    tools/), só o RESULTADO que o jogo consome de verdade."""

    def test_amostra_de_icones_bate_com_arquivo_original(self):
        from ui.icon_atlas_catalog import ICON_ATLAS
        from ui.icon_atlas import ATLAS_DIR

        amostra = ["item_iron_sword", "item_hp_potion", "skill_tiro_multiplo",
                   "skill_golpe_poderoso"]
        for nome in amostra:
            self.assertIn(nome, ICON_ATLAS, f"{nome} deveria estar catalogado")
            filename, col, row = ICON_ATLAS[nome]

            original_path = os.path.join(ATLAS_DIR, f"{nome}.png")
            original = pygame.image.load(original_path).convert_alpha()

            atlas_path = os.path.join(ATLAS_DIR, filename)
            atlas_sheet = pygame.image.load(atlas_path).convert_alpha()
            region = pygame.Surface((CELL_SIZE, CELL_SIZE), pygame.SRCALPHA)
            region.blit(atlas_sheet, (0, 0),
                       (col * CELL_SIZE, row * CELL_SIZE, CELL_SIZE, CELL_SIZE))

            self.assertEqual(pygame.image.tobytes(region, "RGBA"),
                             pygame.image.tobytes(original, "RGBA"),
                             f"{nome}: slot do atlas não bate pixel a pixel com o original")


if __name__ == "__main__":
    unittest.main()
