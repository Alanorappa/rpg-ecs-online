"""
atlas_window.py — ponto único pra MONTAR janelas com o atlas de UI
(ui/ui_atlas.py fornece as peças; aqui fica a composição + interação):
painel centralizado na área segura, botão que age no SOLTAR do mouse,
campo de texto, seletor ◀ valor ▶, textos e larguras medidas a partir
do texto. Usado pelo menu de pausa (client/menu_handlers.py), modal de
atalhos (client/hotbar_editor_handlers.py), diálogo de quest
(ui/quest_system.py) e qualquer tela futura com o visual do atlas —
NUNCA reimplementar esses comportamentos numa tela à parte.

Catálogo das peças: arquitetura/ATLAS_UI.md.
"""
from __future__ import annotations
import math

import pygame

from ui import ui_atlas
from ui.ui_helpers import fill_surf

T, S = ui_atlas.TILE, ui_atlas.SCALE

TEXT_PAD    = 6      # px RAW de folga de cada lado do texto (bordas do botão/campo)
SAFE_MARGIN = 0.10   # área segura: 10% da altura (topo / acima da barra de ações), 10% da largura
TITLE_Y     = T      # centro (RAW) do título da janela

TITLE_COL  = (220, 190, 110)
BTN_TXT    = (230, 210, 160)
TEXT_COL   = (240, 225, 185)
ACTIVE_COL = (255, 225, 82)
DISABLED_COL = (130, 120, 105)


class PressState:
    """Qual botão está afundado agora (id) — 1 instância por sistema de
    UI, persiste entre frames. Botões agem no SOLTAR: agir no clique
    fecha/troca a janela no mesmo frame e o estado pressionado nunca
    aparece."""
    __slots__ = ("pressed",)

    def __init__(self) -> None:
        self.pressed = ""


def safe_area(screen_w: int, screen_h: int, hotbar_top: int) -> pygame.Rect:
    """Área segura PADRÃO de toda janela do atlas (decisão do usuário):
    margem de 10% da altura no topo e acima da barra de ações, 10% da
    largura nas laterais."""
    mx, my = round(screen_w * SAFE_MARGIN), round(screen_h * SAFE_MARGIN)
    bottom = hotbar_top - my
    return pygame.Rect(mx, my, screen_w - 2 * mx, max(0, bottom - my))


def tiles_for(font, texts, min_tiles: int) -> int:
    """Largura em tiles que cabe o maior texto + folga. A fonte acompanha
    a escala de UI do jogador, o atlas não — largura com texto NUNCA é
    número fixo de tiles."""
    widest = max(font.size(t)[0] for t in texts) / S
    return max(min_tiles, math.ceil((widest + 2 * TEXT_PAD) / T))


def draw_texts(surf: pygame.Surface, texts: list, default_font, ox: int = 0, oy: int = 0) -> None:
    for text, x, y, color, font, anchor in texts:
        t = (font or default_font).render(text, False, color)
        surf.blit(t, t.get_rect(**{anchor: (ox + x * S, oy + y * S)}))


class Frame:
    """1 janela durante 1 frame: painel RAW (carimbe por cima), mouse em
    espaço RAW, textos a desenhar no fim. `area` = área segura (Rect de
    tela) onde a janela é centralizada."""

    def __init__(self, screen: pygame.Surface, events: list, tw: int, th: int, *,
                 area: pygame.Rect, press: PressState, font, style: str = "wood",
                 offset=(0, 0), overlay: bool = True) -> None:
        self.screen, self.press_state, self.font = screen, press, font
        self.raw = ui_atlas.panel_raw(style, tw, th).copy()
        self.pw, self.ph = self.raw.get_width() * S, self.raw.get_height() * S
        self.px = area.centerx - self.pw // 2 + offset[0]
        self.py = area.centery - self.ph // 2 + offset[1]
        if overlay:
            screen.blit(fill_surf(screen.get_size(), (0, 0, 0, 150)), (0, 0))
        mx, my = pygame.mouse.get_pos()
        self.rx, self.ry = (mx - self.px) / S, (my - self.py) / S
        self.clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        self.released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)
        self.down     = pygame.mouse.get_pressed()[0]
        self.texts: list = []   # (texto, x_raw, y_raw, cor, fonte, âncora)

    def child(self, raw: pygame.Surface, ry: float) -> "Frame":
        """Área filha (ex: conteúdo rolável montado num Surface à parte):
        mesmo mouse/press, `raw`/textos próprios e `ry` já convertido pro
        espaço dela (passar -10000 quando o mouse está fora)."""
        c = object.__new__(Frame)
        c.__dict__.update(self.__dict__)
        c.raw, c.ry, c.texts = raw, ry, []
        return c

    def tiles_for(self, texts, min_tiles: int, font=None) -> int:
        return tiles_for(font or self.font, texts, min_tiles)

    def press(self, btn_id: str, x0, y0, x1, y1) -> tuple:
        """(desenhar_pressionado, disparou) da área RAW (x0,y0)-(x1,y1)."""
        hov = x0 <= self.rx < x1 and y0 <= self.ry < y1
        ps = self.press_state
        if self.clicked and hov:
            ps.pressed = btn_id
        held  = ps.pressed == btn_id
        fired = self.released and hov and held
        return held and hov and not self.released, fired

    def text(self, text: str, x: float, y: float, color=None, font=None,
             anchor: str = "center") -> None:
        self.texts.append((text, x, y, color or TEXT_COL, font, anchor))

    def button(self, btn_id: str, tx: int, ty: int, w: int, label: str,
               tall: bool = True, enabled: bool = True) -> bool:
        """Botão do atlas (w tiles) com texto. True = disparou.
        `enabled=False`: mesmo sprite, texto cinza, nunca dispara (o atlas
        não tem versão desabilitada — decisão do usuário)."""
        ay0, ay1 = ui_atlas.button_art_y(tall)
        x0, x1 = tx * T, (tx + w) * T
        y0, y1 = ty * T + ay0, ty * T + ay1
        pressed, fired = self.press(btn_id, x0, y0, x1, y1) if enabled else (False, False)
        ui_atlas.blit_button(self.raw, tx, ty, w, pressed=pressed, tall=tall)
        self.text(label, (x0 + x1) / 2, (y0 + y1) / 2, BTN_TXT if enabled else DISABLED_COL)
        return fired

    def text_field(self, field_id: str, tx: int, ty: int, w: int, text: str,
                   active: bool = False) -> bool:
        """Campo de texto do atlas (w tiles, 1 linha). `active` = aguardando
        entrada (texto destacado). True = clicado (no soltar)."""
        inset = ui_atlas.FIELD_ART_INSET_X
        ay0, ay1 = ui_atlas.FIELD_ART_Y
        x0, x1 = tx * T + inset, (tx + w) * T - inset
        y0, y1 = ty * T + ay0, ty * T + ay1
        _, fired = self.press(field_id, x0, y0, x1, y1)
        ui_atlas.blit_text_field(self.raw, tx, ty, w)
        self.text(text, (x0 + x1) / 2, (y0 + y1) / 2, ACTIVE_COL if active else TEXT_COL)
        return fired

    def selector(self, sel_id: str, ty: int, col_l: int, col_r: int, text: str) -> int:
        """Linha ◀ texto ▶. -1/+1 quando uma seta dispara (no soltar)."""
        step = 0
        for col, d, idle, pressed_t in ((col_l, -1, ui_atlas.ARROW_LEFT, ui_atlas.ARROW_LEFT_PRESSED),
                                        (col_r, +1, ui_atlas.ARROW_RIGHT, ui_atlas.ARROW_RIGHT_PRESSED)):
            pressed, fired = self.press(f"{sel_id}:{d}", col * T, ty * T, (col + 1) * T, (ty + 1) * T)
            ui_atlas.blit_tile(self.raw, pressed_t if pressed else idle, col, ty)
            if fired:
                step = d
        self.text(text, (col_l + 1 + col_r) * T / 2, ty * T + T / 2)
        return step

    def end(self, title: str = "", title_col=TITLE_COL) -> None:
        """Amplia o painel, desenha título e textos e encerra o estado de
        botão pressionado ao soltar o mouse."""
        self.screen.blit(ui_atlas.scale_up(self.raw), (self.px, self.py))
        if title:
            surf = self.font.render(title, False, title_col)
            self.screen.blit(surf, surf.get_rect(center=(self.px + self.pw // 2, self.py + TITLE_Y * S)))
        draw_texts(self.screen, self.texts, self.font, self.px, self.py)
        if self.released or not self.down:
            self.press_state.pressed = ""
