"""
utils.py — Funções utilitárias compartilhadas entre sistemas.

Regra: apenas funções puras sem dependências de estado global.
Sistemas importam daqui; nunca o contrário.
"""
from __future__ import annotations
import math


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """Distância de Chebyshev (máximo entre delta-x e delta-y).

    Usada para distâncias em grade de 8 direções (tiles).
    chebyshev(1,1, 3,2) == 2  (diagonal conta como 1)
    """
    return max(abs(ax - bx), abs(ay - by))


def start_tile_movement(position, tile_movement, tgt_x: int, tgt_y: int,
                        extra_speed_mult: float = 1.0) -> None:
    """Inicia um movimento tile-a-tile para (tgt_x, tgt_y).

    Compartilhado entre PlayerInputSystem e EnemyAISystem para evitar
    duplicação da lógica de cálculo de duração/pixels.

    Args:
        position:         componente Position da entidade.
        tile_movement:    componente TileMovement da entidade.
        tgt_x, tgt_y:    tile destino.
        extra_speed_mult: multiplicador adicional de velocidade (ex: 2.0
                          para o dash de kiting do Hunter).
    """
    from tileset import TILE_SIZE

    tile_movement.is_moving     = True
    tile_movement.progress      = 0.0
    tile_movement.start_pixel_x = position.x
    position.prev_x             = position.x
    tile_movement.start_pixel_y = position.y
    position.prev_y             = position.y
    tile_movement.target_tile_x = tgt_x
    tile_movement.target_tile_y = tgt_y
    tile_movement.target_pixel_x = tgt_x * TILE_SIZE + TILE_SIZE / 2
    tile_movement.target_pixel_y = tgt_y * TILE_SIZE + TILE_SIZE / 2

    if tile_movement.speed > 0:
        is_diag = (tgt_x != tile_movement.current_tile_x and
                   tgt_y != tile_movement.current_tile_y)
        dist = TILE_SIZE * (math.sqrt(2) if is_diag else 1.0)
        effective_speed = tile_movement.speed * tile_movement.slow_mult * extra_speed_mult
        if effective_speed <= 0:
            effective_speed = tile_movement.speed  # fallback: ignora multiplicadores zerados
        tile_movement.move_duration = dist / effective_speed
