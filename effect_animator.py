# effect_animator.py
"""
Gerencia animações de sprite para efeitos de status.

Convenção de arquivo:
  assets/effects/{effect_type}.png
  Sprite sheet horizontal: N frames de FRAME_W × FRAME_H pixels.

Uso:
  frame = get_frame("sleep")   → Surface ou None (sem animação)

Quando None, o caller exibe o quadrado colorido padrão.
"""
from __future__ import annotations
import os
import pygame

# Dimensões de cada frame e duração de um loop completo
FRAME_W       = 32
FRAME_H       = 32
LOOP_DURATION = 2.0   # segundos por ciclo completo da animação

# Caches — carregados uma vez na primeira solicitação
_sheets:       dict[str, pygame.Surface | None] = {}
_frame_counts: dict[str, int]                   = {}


def _load(effect_type: str) -> pygame.Surface | None:
    """Carrega o sprite sheet de um efeito (lazy). Retorna None se não existir."""
    if effect_type in _sheets:
        return _sheets[effect_type]

    from paths import resource_path
    path = resource_path(f"assets/effects/{effect_type}.png")

    if os.path.isfile(path):
        try:
            sheet = pygame.image.load(path).convert_alpha()
            n_frames = max(1, sheet.get_width() // FRAME_W)
            _sheets[effect_type]       = sheet
            _frame_counts[effect_type] = n_frames
        except Exception:
            _sheets[effect_type]       = None
            _frame_counts[effect_type] = 1
    else:
        _sheets[effect_type]       = None
        _frame_counts[effect_type] = 1

    return _sheets[effect_type]


def get_frame(effect_type: str) -> pygame.Surface | None:
    """
    Retorna o frame atual da animação do efeito, ou None se não houver PNG.

    Usa pygame.time.get_ticks() para calcular o frame — sem dt acumulado,
    sem estado por entidade. Todos os alvos do mesmo efeito ficam em sincronia.
    """
    sheet = _load(effect_type)
    if sheet is None:
        return None

    fc = _frame_counts.get(effect_type, 1)
    t  = pygame.time.get_ticks() / 1000.0          # segundos desde início
    frame_idx = int((t % LOOP_DURATION) / LOOP_DURATION * fc) % fc

    try:
        return sheet.subsurface(pygame.Rect(frame_idx * FRAME_W, 0, FRAME_W, FRAME_H))
    except ValueError:
        return None


def reload() -> None:
    """Limpa o cache — útil ao trocar resolução ou recarregar assets."""
    _sheets.clear()
    _frame_counts.clear()
