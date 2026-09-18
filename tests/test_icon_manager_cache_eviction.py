"""
tests/test_icon_manager_cache_eviction.py — 18/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #8: `IconManager._cache`
crescia sem limite — cada chave nova é `f"{name}@{size}"`, e o mesmo
ícone pode ser pedido em vários tamanhos diferentes (hotbar, tooltip,
inventário, etc.), então o cache nunca tinha teto nem `clear()`,
diferente do padrão já usado em `ui/floating_text.py::_outline_cache`
e `ui/ui_helpers.py::_FILL_CACHE`.

Prova: cache tem teto explícito (`IconManager._CACHE_MAX`) — ao
estourar, é totalmente limpo (mesma disciplina simples de
`_outline_cache`/`_FILL_CACHE`, não LRU parcial) antes de guardar a
entrada nova, e continua funcionando certo depois (não quebra, só
reconstrói sob demanda).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from ui.icon_manager import IconManager


class TestIconManagerCacheEviction(unittest.TestCase):

    def setUp(self):
        self.mgr = IconManager()
        self.mgr._CACHE_MAX = 5   # teto pequeno pra testar sem gerar centenas de ícones

    def test_cache_nunca_ultrapassa_o_teto(self):
        for i in range(50):
            self.mgr.get("item_iron_sword", 16 + i)   # tamanho diferente = chave nova
            self.assertLessEqual(len(self.mgr._cache), self.mgr._CACHE_MAX)

    def test_limpa_tudo_ao_estourar_nao_evicta_parcial(self):
        for i in range(self.mgr._CACHE_MAX):
            self.mgr.get("item_iron_sword", 16 + i)
        self.assertEqual(len(self.mgr._cache), self.mgr._CACHE_MAX)

        self.mgr.get("item_iron_sword", 999)   # estoura o teto -> clear() total
        # só a entrada que acabou de ser inserida sobra (clear + insere 1)
        self.assertEqual(len(self.mgr._cache), 1)
        self.assertIn("item_iron_sword@999", self.mgr._cache)

    def test_continua_funcionando_certo_depois_de_limpar(self):
        for i in range(self.mgr._CACHE_MAX + 3):
            surf = self.mgr.get("item_iron_sword", 16 + i)
            self.assertIsNotNone(surf)
            self.assertEqual(surf.get_size(), (16 + i, 16 + i))

    def test_icone_inexistente_tambem_conta_pro_teto_mas_nao_quebra(self):
        for i in range(self.mgr._CACHE_MAX + 3):
            self.assertIsNone(self.mgr.get(f"nao_existe_{i}", 32))
        self.assertLessEqual(len(self.mgr._cache), self.mgr._CACHE_MAX)


if __name__ == "__main__":
    unittest.main()
