"""
ui/world_icon_animator.py
Ícone animado pro MUNDO (acima da cabeça de NPC/entidade, via
WORLD_LABELS) — spritesheet horizontal, N quadros quadrados lado a lado,
avança por tempo real em loop (09/10/2026).

Convenção: assets/sprites/{name}.png — tira horizontal de quadros
QUADRADOS (largura do quadro == altura da imagem inteira). N quadros =
largura total / altura — nunca hardcoded, robusto a arte nova com
contagem de quadros diferente.

Usado hoje só pelo marcador de quest (ui/quest_system.py::render_world)
— nome do arquivo e da função são genéricos de propósito, pra qualquer
ícone animado de mundo futuro reusar sem precisar de módulo novo.

Diferente de `ui/effect_animator.py` (ícone ESTÁTICO de efeito,
assets/effects/) e `ui/icon_manager.py` (ícones de UI — slot/item/
inimigo, assets/icons/, com atlas) — categoria própria de asset
(spritesheet de mundo), por isso módulo próprio.
"""
from __future__ import annotations
import os
import pygame
from paths import resource_path

SPRITE_DIR = "assets/sprites"
DEFAULT_FRAME_MS = 120   # ~8.3 fps — ciclo de 10 quadros em ~1.2s, ritmo
                          # de "chamar atenção" sem ficar frenético.

_sheets: "dict[str, pygame.Surface | None]" = {}
_frame_cache: "dict[tuple, pygame.Surface | None]" = {}
_CACHE_MAX = 256   # mesmo padrão de teto+clear total de ui/icon_manager.py


def _load_sheet(name: str) -> "pygame.Surface | None":
    if name in _sheets:
        return _sheets[name]
    path = resource_path(os.path.join(SPRITE_DIR, f"{name}.png"))
    if os.path.isfile(path):
        from ui.asset_cache import get_image
        _sheets[name] = get_image(path)
    else:
        _sheets[name] = None
    return _sheets[name]


def get_frame(name: str, size: int, now_ms: int,
              frame_ms: int = DEFAULT_FRAME_MS) -> "pygame.Surface | None":
    """Quadro atual da animação `name`, escalado pra size×size (nearest-
    neighbor via pygame.transform.scale, mesmo tratamento pixel-art de
    ui/icon_manager.py). None se assets/sprites/{name}.png não existir —
    chamador cai pro próprio fallback (ícone estático/forma desenhada)."""
    sheet = _load_sheet(name)
    if sheet is None:
        return None
    frame_h = sheet.get_height()
    frame_count = max(1, sheet.get_width() // frame_h)
    frame_idx = (now_ms // frame_ms) % frame_count
    key = (name, size, frame_idx)
    if key in _frame_cache:
        return _frame_cache[key]
    raw = sheet.subsurface((frame_idx * frame_h, 0, frame_h, frame_h))
    surf = raw if size == frame_h else pygame.transform.scale(raw, (size, size))
    if len(_frame_cache) >= _CACHE_MAX:
        _frame_cache.clear()
    _frame_cache[key] = surf
    return surf
