"""
engine/cave_autotile.py — escolha automática das peças de parede da caverna
("TX Tileset Cave", ids cv001..cv095) a partir do FORMATO da parede.

Ponto único de verdade das regras de encaixe — usado pelo pincel "Parede de
caverna (inteligente)" do God Mode (`ui/god_mode.py`). Sem pygame: lógica
pura sobre um conjunto de células de parede.

Regras aprendidas (24/10/2026) do mapa de Aracnópolis decorado à mão pelo
usuário + 2 rodadas de laboratório corrigidas por ele (ver
arquitetura/HISTORICO_FEATURES.md §84). Acerto medido contra esse gabarito:
~97% no mapa inteiro, ~99% no laboratório.

Papéis (cada célula de parede recebe 1):
  fileira de baixo (chão embaixo):
    face_lo 14/15 · pontas 13/16 · coluna alta de degrau 27/26 (32x96)
    base 31 · pontas 20/21  (degrau de até 2 de altura, ou modo "só topo")
  face_hi 10/11 · pontas 9/12 — só em cima de face baixa; 10 casa com 14, 11 com 15
  top 2/3/36 · quinas 1/4 · laterais 5/34 e 8/33
  degrau lateral 29/28 (32x96) — lateral cuja diagonal de cima é ponta de face
  quinas internas 37/35/30/32 · miolo 17
Peças 32x96 (26-29) cobrem visualmente as 2 células acima: a face logo acima
delas fica vazia ('.').
"""
from __future__ import annotations

import random

_P = lambda *ids: tuple(f"cv{i:03d}" for i in ids)
ROLE_PIECES: dict[str, tuple] = {
    'face_lo': _P(14, 15), 'face_lo_L': _P(13), 'face_lo_R': _P(16),
    'tall_L': _P(27), 'tall_R': _P(26),
    'face_hi': _P(10, 11), 'face_hi_L': _P(9), 'face_hi_R': _P(12),
    'base': _P(31), 'base_L': _P(20), 'base_R': _P(21),
    'top': _P(2, 3, 36), 'top_L': _P(1), 'top_R': _P(4),
    'left': _P(5, 34), 'right': _P(8, 33), 'step_L': _P(29), 'step_R': _P(28),
    'in_NW': _P(37), 'in_NE': _P(35), 'in_SE': _P(30), 'in_SW': _P(32),
    'fill': _P(17),
}
FACE_LO = {'face_lo', 'face_lo_L', 'face_lo_R', 'tall_L', 'tall_R'}
FACE = {'face_lo', 'face_lo_L', 'face_lo_R', 'face_hi', 'face_hi_L', 'face_hi_R'}
TALL = {'tall_L', 'tall_R', 'step_L', 'step_R'}
PAIR_HI = {'cv014': 'cv010', 'cv015': 'cv011'}      # face alta casa com a face baixa embaixo
FILL = 'cv017'
COVERED = '.'                                        # célula coberta por peça 32x96 de baixo
WALL_PIECES = frozenset(p for ps in ROLE_PIECES.values() for p in ps)

# Tamanho mínimo do carimbo do pincel por modo (largura x altura) — formas
# menores não têm peça que as monte (decisão do usuário: o pincel corrige).
MIN_SIZE = {'full': 3, 'top': 2}


def roles_for(F: set, W: int, H: int, faces: bool = True, region: "set | None" = None,
              thin: int = 3) -> dict:
    """Papel de cada célula de parede. `F` = células de parede (formato);
    fora do mapa conta como parede. `faces=False` = modo "só topo" (nenhuma
    face de rocha, toda fileira de baixo vira base). `region` limita quais
    células recebem papel (com folga interna pra dependências)."""
    inside = lambda x, y: 0 <= x < W and 0 <= y < H
    wall = lambda x, y: (not inside(x, y)) or (x, y) in F

    if region is None:
        cells = [p for p in F if inside(*p)]
    else:
        xs = [p[0] for p in region]; ys = [p[1] for p in region]
        x0, x1, y0, y1 = min(xs) - 4, max(xs) + 4, min(ys) - 4, max(ys) + 4
        cells = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if (x, y) in F]

    def height(x, y):
        h = 0
        while h < 4 and wall(x, y - h):
            h += 1
        return h

    def side_height(x, y, dx):
        h = 0
        while h < 4 and wall(x, y - h) and not wall(x + dx, y - h):
            h += 1
        if h < 4 and not inside(x, y - h):          # borda do mapa: parede continua
            h = 4
        return h

    def run(x, y):
        a = x
        while wall(a - 1, y) and x - a < 12:
            a -= 1
        b = x
        while wall(b + 1, y) and b - x < 12:
            b += 1
        return a, b

    roles: dict = {}

    def eff_side(x, y, dx):
        """Degrau curto (<=2) pendurado embaixo de uma BASE ainda ganha face;
        só embaixo de face ele vira base."""
        sh = side_height(x, y, dx)
        if sh <= 2 and wall(x + dx, y - sh) and roles.get((x + dx, y - sh)) not in FACE_LO:
            return max(sh, height(x, y))
        return sh

    def bottom_role(x, y):
        w_, e = wall(x - 1, y), wall(x + 1, y)
        h = height(x, y)
        if not faces:
            return 'base' if (w_ and e) else ('base_L' if not w_ else 'base_R')
        if h == 1:
            return 'face_lo'
        if not w_ and not e:
            return 'face_lo_L' if h >= 3 else 'base_L'
        if w_ and e:
            a, b = x, x
            while wall(a - 1, y) and not wall(a - 1, y + 1):
                a -= 1
            while wall(b + 1, y) and not wall(b + 1, y + 1):
                b += 1

            def end_h(ex, dx):                       # ponta em degrau de escada conta como face
                return h if wall(ex + dx, y - 1) else eff_side(ex, y, dx)
            hs = ([end_h(a, -1)] if not wall(a - 1, y) else []) + \
                 ([end_h(b, 1)] if not wall(b + 1, y) else [])
            sh = max(hs) if hs else h
            return 'face_lo' if min(sh, h) >= 3 else 'base'
        side, dx = ('L', -1) if not w_ else ('R', 1)
        if wall(x + dx, y - 1):                      # degrau de escada: segue o de cima
            up = roles.get((x + dx, y - 1))
            return ('base_' if up in ('base_L', 'base_R', 'base') else 'tall_') + side
        sh = eff_side(x, y, dx)
        if sh <= 2:
            return 'base_' + side
        a, b = run(x, y)                             # parede fina que só desvia: sem face
        continues = any(wall(i, y + 1) for i in range(a, b + 1))
        a1, b1 = run(x, y - 1)
        if continues and (b1 - a1 + 1) <= thin:
            return 'base_' + side
        return 'face_lo_' + side

    for (x, y) in sorted(cells, key=lambda p: (p[1], p[0])):   # de cima pra baixo
        if not wall(x, y + 1):
            roles[(x, y)] = bottom_role(x, y)

    def role(x, y):
        n, w_, e = wall(x, y - 1), wall(x - 1, y), wall(x + 1, y)
        if roles.get((x, y + 1)) in FACE_LO and height(x, y + 1) >= 3:
            return 'face_hi' if (w_ and e) else ('face_hi_L' if not w_ else 'face_hi_R')
        if not n:
            return 'top' if (w_ and e) else ('top_L' if not w_ else 'top_R')
        if not w_:
            return 'step_L' if roles.get((x - 1, y - 1)) in FACE_LO else 'left'
        if not e:
            return 'step_R' if roles.get((x + 1, y - 1)) in FACE_LO else 'right'
        if not wall(x - 1, y - 1): return 'in_NW'
        if not wall(x + 1, y - 1): return 'in_NE'
        if not wall(x + 1, y + 1): return 'in_SE'
        if not wall(x - 1, y + 1): return 'in_SW'
        return 'fill'

    for p in cells:
        if p not in roles:
            roles[p] = role(*p)
    if region is not None:
        roles = {p: r for p, r in roles.items() if p in region}
    return roles


def pick_pieces(roles: dict, current: "dict | None" = None, rng: "random.Random | None" = None) -> dict:
    """Peça de cada célula a partir do papel. `current` (célula -> peça que já
    está lá): se ela ainda serve pro papel, é mantida (ajuste à mão do autor
    sobrevive). Aplica o par face alta/baixa e o "coberto" das peças 32x96."""
    rng = rng or random.Random()
    current = current or {}
    out = {}
    for p, r in roles.items():
        cur = current.get(p)
        out[p] = cur if cur in ROLE_PIECES[r] else rng.choice(ROLE_PIECES[r])
    for (x, y), r in roles.items():
        if r == 'face_hi' and out.get((x, y + 1)) in PAIR_HI:
            out[(x, y)] = PAIR_HI[out[(x, y + 1)]]
    for (x, y), r in roles.items():
        if r in TALL:
            for k in (1, 2):
                q = (x, y - k)
                if q in out and ((k == 1 and r in ('tall_L', 'tall_R')) or roles.get(q) in FACE):
                    out[q] = COVERED
    return out


def tile(F: set, W: int, H: int, faces: bool = True, seed: "int | None" = None) -> dict:
    """Atalho: papel + peça de todo o formato `F` (mapa inteiro)."""
    return pick_pieces(roles_for(F, W, H, faces=faces), rng=random.Random(seed))


def is_cave_wall_piece(obj_id: str) -> bool:
    """Qualquer peça da família de paredes ("cv" + número), inclusive as
    variações raras que o pincel não usa mas o autor coloca à mão."""
    return obj_id[:2] == 'cv' and obj_id[2:].isdigit()


def footprint(terrain_rows, object_rows, x0: int = 0, y0: int = 0,
              x1: "int | None" = None, y1: "int | None" = None) -> set:
    """Formato da parede de caverna num mapa: terreno 'c' OU peça da família
    (peça 32x96 conta também as 2 células que cobre). Janela opcional."""
    from engine.tileset import OBJECT_SHEET_TILE_MAP
    H = len(object_rows)
    y1 = H if y1 is None else min(y1, H)
    F = set()
    for y in range(max(0, y0), y1):
        orow, trow = object_rows[y], terrain_rows[y]
        xe = len(orow) if x1 is None else min(x1, len(orow))
        for x in range(max(0, x0), xe):
            o = orow[x]
            if is_cave_wall_piece(o):
                for k in range(OBJECT_SHEET_TILE_MAP[o][4] // 32):
                    F.add((x, y - k))
            elif x < len(trow) and trow[x] == 'c':
                F.add((x, y))
    return F


def thin_cells(F: set, cells, min_w: int, min_h: int = 2) -> set:
    """Células de `cells` (dentro de F) finas demais pra montar: trecho
    horizontal < min_w ou vertical < min_h. Usado pra limpar sobra ao apagar."""
    out = set()
    for (x, y) in cells:
        if (x, y) not in F:
            continue
        a = x
        while (a - 1, y) in F:
            a -= 1
        b = x
        while (b + 1, y) in F:
            b += 1
        c = y
        while (x, c - 1) in F:
            c -= 1
        d = y
        while (x, d + 1) in F:
            d += 1
        if b - a + 1 < min_w or d - c + 1 < min_h:
            out.add((x, y))
    return out
