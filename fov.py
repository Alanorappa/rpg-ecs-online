"""
fov.py — Recursive shadowcasting FOV (field of view).

Algoritmo clássico de 8 octantes de Björn Bergstrom.
Sem dependências externas — apenas Python puro.

Uso:
    from fov import compute_fov

    visible = compute_fov(
        ox=player_tile_x,
        oy=player_tile_y,
        radius=8,
        is_blocking=lambda x, y: tilemap[y][x].is_solid,
    )
    # visible: set of (x, y) tile coords visible from (ox, oy)
"""
from __future__ import annotations

# Multipliers para os 8 octantes.
# Transformam (col, row) → (dx, dy) relativo à origem:
#   dx = col * xx + row * xy
#   dy = col * yx + row * yy
_OCTANTS = (
    ( 1,  0,  0,  1),
    ( 0,  1,  1,  0),
    ( 0, -1,  1,  0),
    (-1,  0,  0,  1),
    (-1,  0,  0, -1),
    ( 0, -1, -1,  0),
    ( 0,  1, -1,  0),
    ( 1,  0,  0, -1),
)


def compute_fov(ox: int, oy: int, radius: int, is_blocking) -> set:
    """
    Retorna o conjunto de tiles (x, y) visíveis a partir de (ox, oy).

    Parameters
    ----------
    ox, oy      : posição do observador em coordenadas de tile
    radius      : raio máximo de visibilidade em tiles
    is_blocking : callable(x, y) -> bool — True se o tile bloqueia visão
    """
    visible: set = {(ox, oy)}
    for xx, xy, yx, yy in _OCTANTS:
        _cast(visible, ox, oy, 1, 1.0, 0.0, radius, xx, xy, yx, yy, is_blocking)
    return visible


def _cast(
    visible: set,
    cx: int, cy: int,
    row: int,
    start: float, end: float,
    radius: int,
    xx: int, xy: int, yx: int, yy: int,
    is_blocking,
) -> None:
    """Processa um octante recursivamente, marcando tiles como visíveis."""
    if start < end:
        return

    radius_sq = radius * radius
    new_start = 0.0

    for j in range(row, radius + 1):
        dx, dy = -j - 1, -j
        blocked = False

        while dx <= 0:
            dx += 1
            mx = cx + dx * xx + dy * xy
            my = cy + dx * yx + dy * yy

            l_slope = (dx - 0.5) / (dy + 0.5)
            r_slope = (dx + 0.5) / (dy - 0.5)

            if start < r_slope:
                continue
            if end > l_slope:
                break

            if dx * dx + dy * dy <= radius_sq:
                visible.add((mx, my))

            if blocked:
                if is_blocking(mx, my):
                    new_start = r_slope
                else:
                    blocked = False
                    start = new_start
            else:
                if is_blocking(mx, my) and j < radius:
                    blocked = True
                    _cast(visible, cx, cy, j + 1, start, l_slope, radius,
                          xx, xy, yx, yy, is_blocking)
                    new_start = r_slope

        if blocked:
            break
