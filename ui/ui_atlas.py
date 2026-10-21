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

# Painel de madeira (4x4 tiles no atlas, origem (0,8)). Bordas têm uma
# variante decorativa usada de vez em quando (mesmo padrão do exemplo);
# miolo é sempre o tile liso (1,10).
_WOOD = {
    "tl": (0, 8),  "tr": (3, 8),  "bl": (0, 11), "br": (3, 11),
    "top":    [(2, 8), (1, 8)],
    "bottom": [(1, 11), (2, 11)],
    "left":   [(0, 10), (0, 9)],
    "right":  [(3, 10), (3, 9)],
    "center": (1, 10),
}

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

# Botão: 3 colunas (ponta esq., meio repetível, ponta dir.) x 2 linhas,
# idle nas colunas 6-8 e pressionado nas 9-11, linhas 10-11 do atlas.
# A arte ocupa só a faixa y=10..24 dentro dos 32px (2 tiles) do bloco.
_BTN_IDLE_COL0    = 6
_BTN_PRESSED_COL0 = 9
_BTN_ROW0         = 10
BTN_ART_Y0, BTN_ART_Y1 = 10, 25


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


def wood_panel_raw(tw: int, th: int) -> "pygame.Surface":
    """Painel de madeira de tw x th tiles, em espaço RAW (1x). Cacheado
    por tamanho — o caller faz copy() se for desenhar por cima."""
    key = ("wood", tw, th)
    cached = _panel_cache.get(key)
    if cached is not None:
        return cached
    surf = pygame.Surface((tw * TILE, th * TILE), pygame.SRCALPHA)
    for j in range(th):
        for i in range(tw):
            top, bottom = j == 0, j == th - 1
            left, right = i == 0, i == tw - 1
            if top and left:       t = _WOOD["tl"]
            elif top and right:    t = _WOOD["tr"]
            elif bottom and left:  t = _WOOD["bl"]
            elif bottom and right: t = _WOOD["br"]
            elif top:              t = _variant(_WOOD["top"], i, j)
            elif bottom:           t = _variant(_WOOD["bottom"], i, j)
            elif left:             t = _variant(_WOOD["left"], i, j)
            elif right:            t = _variant(_WOOD["right"], i, j)
            else:                  t = _WOOD["center"]
            blit_tile(surf, t, i, j)
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        _panel_cache.clear()
    _panel_cache[key] = surf
    return surf


def blit_button(dst: "pygame.Surface", tx: int, ty: int, width_tiles: int,
                pressed: bool = False) -> None:
    """Botão de `width_tiles` tiles (mín. 2) x 2 tiles, carimbado em
    (tx, ty) de `dst` (RAW). Pontas fixas, meio repetido."""
    col0 = _BTN_PRESSED_COL0 if pressed else _BTN_IDLE_COL0
    w = max(2, width_tiles)
    for i in range(w):
        col = col0 if i == 0 else (col0 + 2 if i == w - 1 else col0 + 1)
        for k in range(2):
            blit_tile(dst, (col, _BTN_ROW0 + k), tx + i, ty + k)


def scale_up(raw: "pygame.Surface") -> "pygame.Surface":
    """Amplia por SCALE (nearest-neighbor)."""
    w, h = raw.get_size()
    return pygame.transform.scale(raw, (w * SCALE, h * SCALE))
