"""
fonts.py — Carregador central de fonte do projeto.

Todos os sistemas importam `make(size)` daqui.
Troca a fonte do projeto inteiro alterando apenas _FONT_NAME.

_SCALE: fator de correção de métricas em relação à fonte padrão do pygame
  (pygame.font.Font(None, size)). A Determination renderiza ~2x mais alta
  que a fonte padrão no mesmo size, por isso _SCALE = 0.5.
"""
from __future__ import annotations
import pygame

_FONT_NAME = "determinationregular"
_SCALE     = 0.5          # ajuste de métrica: Determination ≈ 2× a fonte padrão

_resolved_path: str | None = None
_resolved: bool = False


def _path() -> str | None:
    global _resolved_path, _resolved
    if not _resolved:
        _resolved_path = pygame.font.match_font(_FONT_NAME) or None
        _resolved = True
    return _resolved_path


def make(size: int) -> pygame.font.Font:
    """Retorna uma fonte do projeto no tamanho equivalente à fonte padrão de `size` px."""
    return pygame.font.Font(_path(), max(6, round(size * _SCALE)))
