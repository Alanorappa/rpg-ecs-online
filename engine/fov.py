"""
fov.py — Recursive shadowcasting FOV (field of view).

Algoritmo clássico de 8 octantes de Björn Bergstrom.
Sem dependências externas — apenas Python puro. Mora em `engine/` (não
`ui/`, onde vivia até 13/08/2026) justamente por isso — é ponto único
de verdade compartilhado de verdade entre cliente (fog, `ui/systems.py`)
e servidor (`server/tile_los_processor.py`), não "cliente que o
servidor por acaso também consegue importar".

Uso:
    from engine.fov import compute_fov

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


def local_vision_blob(ox: int, oy: int, is_blocking, is_solid=None,
                       radius_cap: int = 64) -> frozenset:
    """
    Blob conectado (4-direções) de tiles bloqueantes ANDÁVEIS que contém
    (ox, oy) — usado pra "furar" o próprio bloqueio de visão quando o
    observador está EM CIMA de um objeto bloqueante andável (bush, copa de
    árvore fora do tronco). Referência: brush de MOBA bloqueia de
    fora-pra-dentro, nunca de dentro-pra-fora (wiki oficial de League of
    Legends) — de dentro de uma bush a visão deveria continuar normal, só
    quem está FORA que não vê o que tem dentro/atrás.

    `is_solid`, se fornecido, PARA a expansão do blob em qualquer tile
    sólido (parede, pedra grande, tronco de árvore) — mesmo que ele seja
    geometricamente vizinho de uma bush real (ex: bush encostada numa
    parede no mapa). Sem isso, uma bush tocando uma parede "vazaria" a
    exceção pra dentro da parede inteira, deixando ela transparente por
    engano — a origem em si nunca é sólida na prática (jogador não pisa
    em cima de sólido), então essa checagem só importa pros VIZINHOS.

    Retorna frozenset() se (ox, oy) não for bloqueante (caminho comum,
    custo zero — a maioria das posições não está em cima de bush/copa).
    `radius_cap` limita o tamanho do blob por segurança (aglomerados reais
    de bush são pequenos, dezenas de tiles) — nunca esperado bater no
    caminho normal.
    """
    if not is_blocking(ox, oy):
        return frozenset()

    seen = {(ox, oy)}
    stack = [(ox, oy)]
    while stack:
        if len(seen) >= radius_cap:
            break
        cx, cy = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in seen:
                continue
            if is_solid is not None and is_solid(nx, ny):
                continue
            if is_blocking(nx, ny):
                seen.add((nx, ny))
                stack.append((nx, ny))
    return frozenset(seen)


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
