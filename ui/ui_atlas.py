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

# Slot de item (quadrado claro de 1 tile) — ícone desenhado por cima.
SLOT = (9, 5)

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
#  - "baixo" (1 linha, linha 9 do atlas): arte em y=1..15 do tile —
#    preenche exatamente 1 linha da janela (lista empilhada do menu).
#  - "alto" (2 linhas, linhas 10-11): arte em y=10..24 dos 32px —
#    fica "entre" 2 linhas da janela (botão solto, ex: Voltar).
_BTN_IDLE_COL0    = 6
_BTN_PRESSED_COL0 = 9
_BTN_ROW_SHORT    = 9
_BTN_ROW_TALL     = 10
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


def quest_window_raw(tw: int, th: int) -> "pygame.Surface":
    """Janela de pergaminho de tw x th tiles (mín. 4x6), RAW, cacheada:
    linha 0 = barra de título (X na última coluna); linhas 1..th-3 =
    pergaminho nas colunas 0..tw-2 + coluna cromada tw-1 (barra de
    rolagem); 2 últimas linhas = rodapé escuro dos botões."""
    key = ("quest", tw, th)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    q = _QUEST
    surf = pygame.Surface((tw * TILE, th * TILE), pygame.SRCALPHA)
    pc, p_top, p_bot = tw - 2, 1, th - QUEST_FOOTER_ROWS - 1
    for i in range(tw):
        t = (q["title_left"] if i == 0 else QUEST_CLOSE if i == tw - 1
             else q["title_notch"] if i == 1 else q["title_mid"])
        blit_tile(surf, t, i, 0)
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
    for i in range(w):
        col = col0 if i == 0 else (col0 + 2 if i == w - 1 else col0 + 1)
        for k in range(rows):
            blit_tile(dst, (col, row0 + k), tx + i, ty + k)


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
