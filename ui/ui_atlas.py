"""
ui_atlas.py — ponto único de verdade pra montar janelas/elementos de UI
a partir do atlas desenhado pelo usuário (assets/ui/ui_elements.png).

O atlas é uma GRADE DE TILES 16x16 (256x256 = 16x16 tiles). Janelas são
montadas como um mapa: carimbando tiles inteiros (cantos, bordas e miolo
REPETIDOS — nunca esticados), e elementos (ícone, seta, botão, trilho)
também ocupam tiles inteiros, alinhados à grade da janela. A referência
de como cada janela é montada é o mockup do usuário
(assets/content/exemplo.png) — o mapeamento de tiles foi extraído dele
por comparação pixel a pixel, nunca escolhido de olho.

Composição sempre em espaço RAW (1x, tiles de 16px) e ampliada 1x só no
fim por SCALE fixo (`ui.hud_bars.SCALE`, nearest-neighbor) — mesma escala
dos nameplates, NUNCA a escala de UI ajustável do jogador
(`ui/ui_scale_mixin.py`) — decisão do usuário.
"""
from __future__ import annotations
import pygame
from paths import resource_path
from ui.hud_bars import SCALE

TILE = 16

_ATLAS_PATH = "assets/ui/ui_elements.png"
_atlas: "pygame.Surface | None" = None

# Painéis: cantos, bordas (1ª opção = principal, 2ª = variante
# decorativa usada de vez em quando, como no mockup) e miolo liso.
# Madeira: 4x4 tiles a partir de (0,8). Escuro: 6x4 a partir de (6,0)
# (mapeado da janela de seleção de personagem do mockup).
_PANELS = {
    "wood": {
        "tl": (0, 8),  "tr": (3, 8),  "bl": (0, 11), "br": (3, 11),
        "top":    [(2, 8), (1, 8)],
        "bottom": [(1, 11), (2, 11)],
        "left":   [(0, 10), (0, 9)],
        "right":  [(3, 10), (3, 9)],
        "center": (1, 10),
    },
    "dark": {
        "tl": (6, 0),  "tr": (11, 0), "bl": (6, 3), "br": (11, 3),
        "top":    [(7, 0), (7, 0)],
        "bottom": [(8, 3), (7, 3)],
        "left":   [(6, 1), (6, 1)],
        "right":  [(11, 1), (11, 1)],
        "center": (7, 1),
    },
}
_WOOD = _PANELS["wood"]

# Moldura vazada (contorno fino, miolo transparente) — linhas da lista
# de seleção de personagem no mockup. Mín. 2x2 tiles.
_FRAME = {
    "tl": (6, 4), "top": (7, 4), "tr": (8, 4),
    "left": (6, 5), "right": (8, 5),
    "bl": (6, 6), "bottom": (7, 6), "br": (8, 6),
}

# Campo de texto (pílula escura semitransparente, 1 linha): ponta esq.,
# meio repetível, ponta dir. — usar pra QUALQUER campo onde o jogador
# digita/define um valor (nome, tecla de atalho...). Arte em y=2..13 do
# tile e x a partir de 2px da ponta esq. até 2px antes do fim da dir.
_FIELD_LEFT, _FIELD_MID, _FIELD_RIGHT = (9, 4), (10, 4), (11, 4)
FIELD_ART_Y = (2, 14)
FIELD_ART_INSET_X = 2

# Barra de rolagem vertical (coluna da borda direita no mockup).
SCROLL_UP           = (12, 0)
SCROLL_UP_PRESSED   = (13, 0)
SCROLL_DOWN         = (12, 1)
SCROLL_DOWN_PRESSED = (13, 1)
SCROLL_TRACK        = (12, 5)
SCROLL_GRIP         = (12, 6)
SCROLL_GRIP_PRESSED = (13, 6)

# Janela de pergaminho (diálogo de quest no mockup): barra de título
# cinza, pergaminho (4x6 tiles a partir de (1,1)), coluna cromada à
# direita (onde vai a barra de rolagem) e rodapé escuro dos botões.
_QUEST = {
    "title_left": (0, 0), "title_notch": (2, 0), "title_mid": (1, 0),
    "chrome_right": (5, 1),
    "p_tl": (1, 1), "p_top": [(2, 1), (3, 1)], "p_tr": (4, 1),
    "p_left":  [(1, 2), (1, 3), (1, 4), (1, 5)], "p_left_rep": (1, 3),
    "p_right": [(4, 2), (4, 3), (4, 4), (4, 5)], "p_right_rep": (4, 3),
    "p_bl": (1, 6), "p_bottom": [(3, 6), (2, 6)], "p_br": (4, 6),
    # Miolo liso + manchas decorativas espalhadas (como no mockup).
    "p_center": (2, 4), "p_deco": [(2, 2), (3, 2), (2, 3), (3, 4), (2, 5)],
    "f_left": (6, 1), "f_bl": (6, 3), "f_center": (7, 1), "f_bottom": (8, 3),
    "f_right": (5, 1), "f_br": (11, 3),
}
QUEST_CLOSE = (5, 0)       # X da barra de título (sem versão pressionada)
QUEST_FOOTER_ROWS = 2      # rodapé = 1 botão "alto"

# Janela escura com barra de título (inventário no mockup): mesma barra
# cinza da janela de pergaminho, corpo escuro, laterais cromadas.
_TITLED_DARK = {
    "left": (0, 1), "right": (5, 1), "center": (7, 1),
    "bl": (0, 7), "bottom": (8, 3), "br": (11, 3),
}

# Slot de item — ícone desenhado por cima. Claro = equipamento /
# recompensa; escuro (marrom) = célula de grade (bolsa). O tile do atlas
# tem 16px, mas o slot no jogo mostra o ícone em 32x32 RAW (decisão do
# usuário): SLOT_SIZE = 32 de ícone + 1px de borda de cada lado, montado
# por slot_raw() repetindo a linha/coluna do meio do tile (nunca esticado).
SLOT = (9, 5)
SLOT_DARK = (10, 5)
SLOT_SIZE = 34

# Linha divisória fina (1px) — mesma cor do contorno da moldura vazada.
DIVIDER_COL = (86, 84, 82)

# Checkbox (1 tile): desmarcado / marcado.
CHECKBOX_OFF = (12, 9)
CHECKBOX_ON  = (13, 9)

# Aba (1 linha, aberta embaixo — encaixa no corpo do painel): ponta esq.,
# meio repetível, ponta dir. Ativa = mesma cor do miolo do painel escuro.
_TAB_ACTIVE_COL0   = 0
_TAB_INACTIVE_COL0 = 3
_TAB_ROW           = 12

# Elementos de 1 tile — (coluna, linha) no atlas.
ICON_SFX_ON    = (12, 7)
ICON_SFX_OFF   = (13, 7)
ICON_MUSIC_ON  = (12, 8)
ICON_MUSIC_OFF = (13, 8)
ARROW_LEFT     = (12, 2)
ARROW_RIGHT    = (12, 3)
ARROW_LEFT_PRESSED  = (13, 2)
ARROW_RIGHT_PRESSED = (13, 3)
SLIDER_TRACK   = (13, 4)
# Barrinha de nível (3px de largura, colada à esquerda do tile) —
# repetida lado a lado por cima do trilho, quantidade proporcional ao
# valor (medidor segmentado).
SLIDER_FILL_BAR   = (14, 4)
SLIDER_FILL_BAR_W = 3

# Botão: 3 colunas (ponta esq., meio repetível, ponta dir.), idle nas
# colunas 6-8 e pressionado nas 9-11. Duas cópias da mesma arte no
# atlas, que só diferem no encaixe vertical na grade:
#  - "baixo" (1 linha, linha 7 do atlas): arte em y=1..15 do tile —
#    preenche exatamente 1 linha da janela (lista empilhada do menu).
#  - "alto" (2 linhas, linhas 8-9): arte em y=10..24 dos 32px —
#    fica "entre" 2 linhas da janela (botão solto, ex: Voltar).
_BTN_IDLE_COL0    = 6
_BTN_PRESSED_COL0 = 9
_BTN_ROW_SHORT    = 7    # atlas reorganizado pelo usuário em 21/10/2026 (era 9)
_BTN_ROW_TALL     = 8    # (era 10)
_BTN_ART_Y_SHORT  = (1, 16)
_BTN_ART_Y_TALL   = (10, 25)


def button_art_y(tall: bool) -> tuple:
    """(y0, y1) RAW da arte do botão dentro do bloco carimbado — usar no
    hit-test (o resto do bloco é transparente)."""
    return _BTN_ART_Y_TALL if tall else _BTN_ART_Y_SHORT


def _load() -> "pygame.Surface":
    global _atlas
    if _atlas is None:
        from ui.asset_cache import get_image
        _atlas = get_image(resource_path(_ATLAS_PATH))
    return _atlas


def blit_tile(dst: "pygame.Surface", tile: tuple, tx: int, ty: int) -> None:
    """Carimba 1 tile do atlas em `dst` (RAW) na posição de tile (tx, ty)."""
    c, r = tile
    dst.blit(_load(), (tx * TILE, ty * TILE), (c * TILE, r * TILE, TILE, TILE))


def blit_tile_px(dst: "pygame.Surface", tile: tuple, x: int, y: int) -> None:
    """Igual blit_tile, mas em pixel RAW livre (ex: polegar do slider)."""
    c, r = tile
    dst.blit(_load(), (x, y), (c * TILE, r * TILE, TILE, TILE))


def _variant(options: list, i: int, j: int):
    # Determinístico por posição (nunca pisca entre frames) — variante
    # decorativa ~1 em cada 5 tiles, igual a frequência do exemplo.
    return options[1] if (i * 7 + j * 3) % 5 == 2 else options[0]


_panel_cache: dict = {}
_PANEL_CACHE_MAX = 32


def panel_raw(style: str, tw: int, th: int) -> "pygame.Surface":
    """Painel (`style` = "wood" | "dark") de tw x th tiles, em espaço RAW
    (1x). Cacheado por (estilo, tamanho) — o caller faz copy() se for
    desenhar por cima."""
    key = (style, tw, th)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    p = _PANELS[style]
    surf = pygame.Surface((tw * TILE, th * TILE), pygame.SRCALPHA)
    for j in range(th):
        for i in range(tw):
            top, bottom = j == 0, j == th - 1
            left, right = i == 0, i == tw - 1
            if top and left:       t = p["tl"]
            elif top and right:    t = p["tr"]
            elif bottom and left:  t = p["bl"]
            elif bottom and right: t = p["br"]
            elif top:              t = _variant(p["top"], i, j)
            elif bottom:           t = _variant(p["bottom"], i, j)
            elif left:             t = _variant(p["left"], i, j)
            elif right:            t = _variant(p["right"], i, j)
            else:                  t = p["center"]
            blit_tile(surf, t, i, j)
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


def wood_panel_raw(tw: int, th: int) -> "pygame.Surface":
    return panel_raw("wood", tw, th)


def dark_list_window_raw(tw: int, th: int) -> "pygame.Surface":
    """Painel escuro com a coluna da direita cromada (5,1) nas linhas
    1..th-2 — onde vai a barra de rolagem (janela de seleção de
    personagem do mockup). RAW, cacheado."""
    key = ("dark_list", tw, th)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    surf = panel_raw("dark", tw, th).copy()
    for j in range(1, th - 1):
        blit_tile(surf, _QUEST["chrome_right"], tw - 1, j)
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


def titled_dark_window_raw(tw: int, th: int) -> "pygame.Surface":
    """Janela escura com barra de título cinza (linha 0, X na última
    coluna, puxadores na 3ª e na 5ª-a-partir-do-fim — como no mockup do
    inventário). RAW, cacheada. Mín. 6x3."""
    key = ("titled_dark", tw, th)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    q, d = _QUEST, _TITLED_DARK
    surf = pygame.Surface((tw * TILE, th * TILE), pygame.SRCALPHA)
    notches = {2, tw - 5}
    for i in range(tw):
        t = (q["title_left"] if i == 0 else QUEST_CLOSE if i == tw - 1
             else q["title_notch"] if i in notches else q["title_mid"])
        blit_tile(surf, t, i, 0)
    for j in range(1, th):
        last = j == th - 1
        for i in range(tw):
            if i == 0:
                t = d["bl"] if last else d["left"]
            elif i == tw - 1:
                t = d["br"] if last else d["right"]
            else:
                t = d["bottom"] if last else d["center"]
            blit_tile(surf, t, i, j)
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


def slot_raw(dark: bool = False, size: int = SLOT_SIZE) -> "pygame.Surface":
    """Slot de `size` x `size` px RAW a partir do tile de 16: os 4 cantos
    (8x8) do tile nos cantos e a linha/coluna do meio repetidas no resto
    — mesma ideia de carimbar o tile de meio, só que de 1px. Cacheado."""
    key = ("slot", dark, size)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    c, r = SLOT_DARK if dark else SLOT
    tile = _load().subsurface((c * TILE, r * TILE, TILE, TILE))
    h = TILE // 2
    src = lambda v: v if v < h else (v - (size - TILE) if v >= size - h else h)
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    for y in range(size):
        sy = src(y)
        for x in range(size):
            surf.set_at((x, y), tile.get_at((src(x), sy)))
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


# ── Peças do HUD (21/10/2026, adicionadas pelo usuário ao atlas) ──────────
# Retângulos em px do atlas (x, y, w, h). Peças que mudam de tamanho são
# montadas por stretch_raw(): mantêm as bordas e REPETEM uma coluna/linha
# do meio (nunca esticam/interpolam) — mesma ideia de slot_raw().
HUD_SKILL_FRAME   = (78, 158, 20, 20)    # moldura de slot da barra de habilidades (cinza, borda 2px)
HUD_CONS_FRAME    = (110, 158, 20, 20)   # moldura de slot da barra de consumíveis (oliva, borda 2px)
HUD_SLOT_BORDER   = 2
MINIMAP_FRAME     = (96, 192, 48, 50)    # moldura do minimapa com a barra do nome embutida no topo
MINIMAP_CONTENT   = (4, 6, 4, 3)         # bordas (esq., topo, dir., base) até a área do mapa, px RAW
MINIMAP_FRAME_MID = (24, 28)             # coluna/linha repetidas ao crescer (meio da barra / laterais)
MINIMAP_NAME_BAR  = (9, 1, 9, 6)         # barra do nome: x0 / y0 / recuo direito / altura, px RAW na moldura
MINIMAP_ICON_CELL = 16                   # célula de cada ícone (círculo + ícone encaixam nela)
MINIMAP_ICON_CIRCLE = (168, 232, 16, 19) # círculo de fundo (célula com o círculo, que passa 3px da célula)
MINIMAP_ICON_ROWS = (168, 200)           # y das fileiras de ícones: apagados / acesos
MINIMAP_ICON_X0   = 168                  # x da 1ª célula de ícone (5 células de 16px)
MINIMAP_ICON_COUNT = 5
MINIMAP_ICON_NUDGE = {1: (-1, 1)}       # ajuste fino por ícone (px RAW) medido no esboço — espadas 1px pra esquerda/baixo
MINIMAP_ICON_DY   = 8                    # topo da célula do círculo = altura da moldura − 8 (círculo começa 4px acima da linha de baixo — medido no esboço exemplo_minimapa.png)
PORTRAIT          = (0, 208, 88, 32)     # anel do retrato + faixa com 3 ranhuras
PORTRAIT_FACE     = (16, 16)             # um ponto DENTRO da abertura do anel (semente do preenchimento)
PORTRAIT_SCALE    = 3                    # retrato desenhado em 3x (decisão do usuário: ranhuras legíveis)
PORTRAIT_BUST_TOP = 2                    # px entre o topo da abertura e o topo da cabeça
# Munição do arqueiro: "pinos" pendurados embaixo da faixa (exemplo_munição.png)
PORTRAIT_AMMO_PIN  = (27, 249, 3, 5)     # 1 pino (contorno compartilhado com o vizinho)
PORTRAIT_AMMO_POS  = (27, 25)            # 1º pino na peça do retrato (px RAW)
PORTRAIT_AMMO_STEP = 2
PORTRAIT_AMMO_MAX  = 18                  # aljava cheia = 18 pinos (proporcional, não a contagem exata)


def piece_raw(rect: tuple) -> "pygame.Surface":
    """Recorte de uma peça do atlas (px), RAW."""
    return _load().subsurface(pygame.Rect(rect))


def stretch_raw(rect: tuple, w: int, h: int, mid_x: "int | None" = None,
                mid_y: "int | None" = None) -> "pygame.Surface":
    """Peça `rect` montada em w x h px RAW repetindo UMA coluna (mid_x) e
    UMA linha (mid_y) da peça — bordas/cantos intactos. Cacheada."""
    key = ("stretch", tuple(rect), w, h, mid_x, mid_y)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    rx, ry, rw, rh = rect
    mx = rw // 2 if mid_x is None else mid_x
    my = rh // 2 if mid_y is None else mid_y
    src = piece_raw(rect)
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    # colunas: [0, mx) | mx repetida | (mx, rw) alinhado à direita
    right_w = rw - mx - 1
    bottom_h = rh - my - 1
    for (sx, sw, dx, dw) in ((0, mx, 0, mx), (mx, 1, mx, w - mx - right_w), (mx + 1, right_w, w - right_w, right_w)):
        if dw <= 0:
            continue
        for (sy, sh, dy, dh) in ((0, my, 0, my), (my, 1, my, h - my - bottom_h),
                                 (my + 1, bottom_h, h - bottom_h, bottom_h)):
            if dh <= 0:
                continue
            part = src.subsurface((sx, sy, sw, sh))
            if (sw, sh) != (dw, dh):
                # só a coluna/linha de 1px é repetida (nearest = cópia do pixel)
                part = pygame.transform.scale(part, (dw, dh))
            surf.blit(part, (dx, dy))
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


_GROOVE_COLS = {(93, 54, 39), (101, 62, 41)}   # fundo das ranhuras do retrato
_portrait_grooves: "list | None" = None


def portrait_grooves() -> list:
    """[(x0, y0, w, h)] px RAW das 3 ranhuras do retrato (de cima pra
    baixo), medidas do próprio atlas (cor de fundo da ranhura) — nunca
    número fixo: se o desenho mudar no atlas, a barra acompanha."""
    global _portrait_grooves
    if _portrait_grooves is None:
        src = piece_raw(PORTRAIT)
        rows = {}
        for y in range(src.get_height()):
            xs = [x for x in range(src.get_width()) if tuple(src.get_at((x, y)))[:3] in _GROOVE_COLS
                  and src.get_at((x, y))[3] > 0 and x > 26]
            if len(xs) > 10:
                rows[y] = (min(xs), max(xs))
        grooves, cur = [], None
        for y in sorted(rows):
            x0, x1 = rows[y]
            if cur and y == cur[1] + cur[3]:
                cur = (min(cur[0], x0), cur[1], max(cur[0] + cur[2] - 1, x1) - min(cur[0], x0) + 1, cur[3] + 1)
                grooves[-1] = cur
            else:
                cur = (x0, y, x1 - x0 + 1, 1)
                grooves.append(cur)
        _portrait_grooves = grooves[:3]
    return _portrait_grooves


_portrait_opening: "pygame.Surface | None" = None


def portrait_opening_mask() -> "pygame.Surface":
    """Máscara (alpha 255 dentro) da abertura do anel do retrato: região
    da cor de fundo do círculo ligada à semente PORTRAIT_FACE — medida do
    próprio atlas (preenchimento), nunca um círculo fixo, pra o rosto
    nunca cobrir a borda do anel."""
    global _portrait_opening
    if _portrait_opening is None:
        src = piece_raw(PORTRAIT)
        w, h = src.get_size()
        sx, sy = PORTRAIT_FACE
        bg = tuple(src.get_at((sx, sy)))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        seen, stack = set(), [(sx, sy)]
        while stack:
            x, y = stack.pop()
            if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
                continue
            seen.add((x, y))
            if tuple(src.get_at((x, y))) != bg:
                continue
            mask.set_at((x, y), (255, 255, 255, 255))
            stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        _portrait_opening = mask
    return _portrait_opening


HUD_SLOT_RAW = 32 + 2 * HUD_SLOT_BORDER   # moldura de slot da hotbar: ícone 32x32 + borda (decisão do usuário)
HUD_SLOT_PX  = HUD_SLOT_RAW * SCALE       # lado do slot na tela (fixo, nunca a escala de UI)


def hud_slot_frame(kind: str) -> "pygame.Surface":
    """Moldura de slot da barra de habilidades (`kind`="skill", cinza) ou
    de consumíveis ("cons", oliva), montada pra caber o ícone 32x32 e já
    ampliada (HUD_SLOT_PX). Cacheada."""
    key = ("hud_slot_frame", kind)
    cached = _panel_cache.get(key)
    if cached is None:
        rect = HUD_SKILL_FRAME if kind == "skill" else HUD_CONS_FRAME
        cached = scale_up(stretch_raw(rect, HUD_SLOT_RAW, HUD_SLOT_RAW))
        if len(_panel_cache) >= _PANEL_CACHE_MAX:
            _panel_cache.clear()
        _panel_cache[key] = cached
    return cached


def minimap_icon_raw(i: int, lit: bool) -> "pygame.Surface":
    """Ícone `i` do minimapa (célula 16x16), apagado ou aceso."""
    y = MINIMAP_ICON_ROWS[1 if lit else 0]
    return piece_raw((MINIMAP_ICON_X0 + i * MINIMAP_ICON_CELL, y, MINIMAP_ICON_CELL, MINIMAP_ICON_CELL))


def blit_tab(dst: "pygame.Surface", tx: int, ty: int, width_tiles: int, active: bool) -> None:
    """Aba de `width_tiles` tiles (mín. 2) em (tx, ty) — pontas fixas, meio repetido."""
    col0 = _TAB_ACTIVE_COL0 if active else _TAB_INACTIVE_COL0
    w = max(2, width_tiles)
    for i in range(w):
        col = col0 if i == 0 else (col0 + 2 if i == w - 1 else col0 + 1)
        blit_tile(dst, (col, _TAB_ROW), tx + i, ty)


def quest_window_raw(tw: int, th: int, tab_rows: int = 0) -> "pygame.Surface":
    """Janela de pergaminho de tw x th tiles (mín. 4x6), RAW, cacheada:
    linha 0 = barra de título (X na última coluna); `tab_rows` linhas
    escuras logo abaixo (fileira de abas do NPC); depois o pergaminho nas
    colunas 0..tw-2 + coluna cromada tw-1 (barra de rolagem); 2 últimas
    linhas = rodapé escuro dos botões."""
    key = ("quest", tw, th, tab_rows)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    q, d = _QUEST, _TITLED_DARK
    surf = pygame.Surface((tw * TILE, th * TILE), pygame.SRCALPHA)
    pc, p_top, p_bot = tw - 2, 1 + tab_rows, th - QUEST_FOOTER_ROWS - 1
    for i in range(tw):
        t = (q["title_left"] if i == 0 else QUEST_CLOSE if i == tw - 1
             else q["title_notch"] if i == 1 else q["title_mid"])
        blit_tile(surf, t, i, 0)
    for j in range(1, p_top):
        for i in range(tw):
            blit_tile(surf, d["left"] if i == 0 else d["right"] if i == tw - 1 else d["center"], i, j)
    for j in range(p_top, p_bot + 1):
        k = j - p_top   # linha dentro do pergaminho
        for i in range(tw):
            if i == tw - 1:
                t = q["chrome_right"]
            elif j == p_top:
                t = q["p_tl"] if i == 0 else q["p_tr"] if i == pc else q["p_top"][i % 4 == 2]
            elif j == p_bot:
                t = q["p_bl"] if i == 0 else q["p_br"] if i == pc else q["p_bottom"][i % 6 == 1]
            elif i == 0:
                t = q["p_left"][k - 1] if k - 1 < len(q["p_left"]) else q["p_left_rep"]
            elif i == pc:
                t = q["p_right"][k - 1] if k - 1 < len(q["p_right"]) else q["p_right_rep"]
            else:
                h = (i * 7 + j * 13) % 17
                t = q["p_deco"][h % len(q["p_deco"])] if h < 2 else q["p_center"]
            blit_tile(surf, t, i, j)
    for j in range(p_bot + 1, th):
        last = j == th - 1
        for i in range(tw):
            if i == 0:
                t = q["f_bl"] if last else q["f_left"]
            elif i == tw - 1:
                t = q["f_br"] if last else q["f_right"]
            else:
                t = q["f_bottom"] if last else q["f_center"]
            blit_tile(surf, t, i, j)
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


def blit_frame(dst: "pygame.Surface", tx: int, ty: int, tw: int, th: int) -> None:
    """Moldura vazada de tw x th tiles (mín. 2x2) em (tx, ty) de `dst`."""
    for j in range(th):
        for i in range(tw):
            top, bottom = j == 0, j == th - 1
            left, right = i == 0, i == tw - 1
            if top:
                key = "tl" if left else ("tr" if right else "top")
            elif bottom:
                key = "bl" if left else ("br" if right else "bottom")
            elif left:
                key = "left"
            elif right:
                key = "right"
            else:
                continue
            blit_tile(dst, _FRAME[key], tx + i, ty + j)


def blit_button(dst: "pygame.Surface", tx: int, ty: int, width_tiles: int,
                pressed: bool = False, tall: bool = True) -> None:
    """Botão de `width_tiles` tiles (mín. 2) carimbado em (tx, ty) de
    `dst` (RAW) — 2 linhas de altura se `tall`, 1 se não (ver cópias
    "alto"/"baixo" acima). Pontas fixas, meio repetido."""
    col0 = _BTN_PRESSED_COL0 if pressed else _BTN_IDLE_COL0
    row0, rows = (_BTN_ROW_TALL, 2) if tall else (_BTN_ROW_SHORT, 1)
    w = max(2, width_tiles)
    # Botão alto: a 2ª linha só é copiada até o fim da arte — logo abaixo,
    # na mesma linha de tiles do atlas, ficam as molduras da hotbar (y 158).
    last_h = (_BTN_ART_Y_TALL[1] - TILE) if tall else TILE
    atlas = _load()
    for i in range(w):
        col = col0 if i == 0 else (col0 + 2 if i == w - 1 else col0 + 1)
        for k in range(rows):
            h = last_h if k == rows - 1 else TILE
            dst.blit(atlas, ((tx + i) * TILE, (ty + k) * TILE),
                     (col * TILE, (row0 + k) * TILE, TILE, h))


def blit_text_field(dst: "pygame.Surface", tx: int, ty: int, width_tiles: int) -> None:
    """Campo de texto de `width_tiles` tiles (mín. 2) x 1 linha em (tx, ty)."""
    w = max(2, width_tiles)
    for i in range(w):
        t = _FIELD_LEFT if i == 0 else (_FIELD_RIGHT if i == w - 1 else _FIELD_MID)
        blit_tile(dst, t, tx + i, ty)


def scale_up(raw: "pygame.Surface") -> "pygame.Surface":
    """Amplia por SCALE (nearest-neighbor)."""
    w, h = raw.get_size()
    return pygame.transform.scale(raw, (w * SCALE, h * SCALE))
