# effect_animator.py
"""
Exibe ícones estáticos de efeitos de status.

Convenção de arquivo:
  assets/effects/{effect_type}.png
  Imagem estática de ICON_W × ICON_H pixels (sem animação).

Uso:
  frame = get_frame("sleep")   → Surface ou None (sem ícone)

Quando None, o caller exibe o quadrado colorido padrão.
"""
from __future__ import annotations
import os
import pygame

# Dimensões do ícone estático
ICON_W = 8
ICON_H = 8

# Aliases para compatibilidade com código que lê FRAME_W/FRAME_H
FRAME_W = ICON_W
FRAME_H = ICON_H

# Cache — carregado uma vez na primeira solicitação
_icons: dict[str, pygame.Surface | None] = {}


def _load(effect_type: str) -> pygame.Surface | None:
    """Carrega o ícone de um efeito (lazy). Retorna None se não existir."""
    if effect_type in _icons:
        return _icons[effect_type]

    from paths import resource_path
    path = resource_path(f"assets/effects/{effect_type}.png")

    if os.path.isfile(path):
        try:
            icon = pygame.image.load(path).convert_alpha()
            _icons[effect_type] = icon
        except Exception:
            _icons[effect_type] = None
    else:
        _icons[effect_type] = None

    return _icons[effect_type]


def get_frame(effect_type: str) -> pygame.Surface | None:
    """Retorna o ícone estático do efeito, ou None se não houver PNG."""
    return _load(effect_type)


def reload() -> None:
    """Limpa o cache — útil ao trocar resolução ou recarregar assets."""
    _icons.clear()
