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
TITLE_BAR_Y = T / 2  # centro (RAW) do título numa janela COM barra de título (linha 0)
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


class ScrollState:
    """Estado de 1 barra de rolagem (persiste entre frames): `offset` em px
    RAW, seta segurada (`hold` = (direção, próximo_ms)) e puxador sendo
    arrastado (`drag` = offset do clique dentro do puxador)."""
    __slots__ = ("offset", "hold", "drag")

    def __init__(self) -> None:
        self.offset = 0
        self.hold: "tuple | None" = None
        self.drag: "float | None" = None


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


def draw_overlays(surf: pygame.Surface, overlays: list, ox: int = 0, oy: int = 0) -> None:
    """Imagens em resolução de TELA (ícone de item/skill) por cima do painel
    já ampliado — nunca ampliadas em bloco. (surface, x_raw, y_raw)."""
    for img, x, y in overlays:
        surf.blit(img, (ox + round(x * S), oy + round(y * S)))


def wrap_text(text: str, max_px: int, font) -> list:
    """Quebra `text` em linhas de até `max_px` px de TELA (fonte de desenho).
    Respeita "\n" como quebra forçada."""
    lines: list = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if cur and font.size(test)[0] > max_px:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
    return lines


class Frame:
    """1 janela durante 1 frame: painel RAW (carimbe por cima), mouse em
    espaço RAW, textos a desenhar no fim. `area` = área segura (Rect de
    tela) onde a janela é centralizada."""

    def __init__(self, screen: pygame.Surface, events: list, tw: int, th: int, *,
                 area: pygame.Rect, press: PressState, font, style: str = "wood",
                 offset=(0, 0), overlay: bool = True,
                 raw: "pygame.Surface | None" = None) -> None:
        """`raw` = painel já montado (ex: ui_atlas.quest_window_raw) no
        lugar de `style`."""
        self.screen, self.press_state, self.font = screen, press, font
        self.raw = (raw if raw is not None else ui_atlas.panel_raw(style, tw, th)).copy()
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
        self.wheel    = sum(e.y for e in events if e.type == pygame.MOUSEWHEEL)
        self.texts: list = []   # (texto, x_raw, y_raw, cor, fonte, âncora)
        self.overlays: list = []   # (surface de tela, x_raw, y_raw) — ver draw_overlays

    def child(self, raw: pygame.Surface, ry: float) -> "Frame":
        """Área filha (ex: conteúdo rolável montado num Surface à parte):
        mesmo mouse/press, `raw`/textos próprios e `ry` já convertido pro
        espaço dela (passar -10000 quando o mouse está fora)."""
        c = object.__new__(Frame)
        c.__dict__.update(self.__dict__)
        c.raw, c.ry, c.texts, c.overlays = raw, ry, [], []
        return c

    def hover(self, x0, y0, x1, y1) -> bool:
        return x0 <= self.rx < x1 and y0 <= self.ry < y1

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
                   active: bool = False, mask: bool = False) -> bool:
        """Campo de texto do atlas (w tiles, 1 linha). `active` = aguardando
        entrada (texto destacado). `mask` = senha (mostra "*"). True =
        clicado (no soltar)."""
        if mask:
            text = "*" * len(text)
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

    def scrollbar(self, st: ScrollState, col: int, up_row: int, down_row: int,
                  max_scroll: int, step: int = T, hold_ms: int = 250,
                  always: bool = False) -> int:
        """Barra de rolagem vertical na coluna `col`, seta ▲ em `up_row` e
        ▼ em `down_row`, trilho entre elas. Clique na seta = 1 passo,
        segurar repete a cada `hold_ms`; puxador arrastável; clique no
        trilho pula o puxador pra lá; roda do mouse sobre a janela. Sem
        nada pra rolar: não desenha, ou (`always`) desenha parada com o
        puxador no topo. Devolve `st.offset` (px RAW)."""
        now = pygame.time.get_ticks()
        if self.released or not self.down:
            st.hold = None
            st.drag = None
        if 0 <= self.rx < self.pw / S and 0 <= self.ry < self.ph / S:
            st.offset -= self.wheel * step
        st.offset = max(0, min(max_scroll, st.offset))
        if max_scroll <= 0:
            st.hold = st.drag = None
            if always:
                self._draw_scrollbar(st, col, up_row, down_row, (up_row + 1) * T)
            return st.offset

        ry = self.ry
        in_col = col * T <= self.rx < (col + 1) * T
        grip_min, grip_max = (up_row + 1) * T, down_row * T - T
        grip_y = grip_min + round((grip_max - grip_min) * st.offset / max_scroll)
        if self.clicked and in_col:
            if up_row * T <= ry < (up_row + 1) * T:
                st.offset -= step
                st.hold = (-1, now + hold_ms)
            elif down_row * T <= ry < (down_row + 1) * T:
                st.offset += step
                st.hold = (+1, now + hold_ms)
            elif grip_min <= ry < grip_max + T:
                st.drag = ry - grip_y if grip_y <= ry < grip_y + T else T / 2
        if st.hold and now >= st.hold[1]:
            st.offset += st.hold[0] * step
            st.hold = (st.hold[0], now + hold_ms)
        if st.drag is not None and grip_max > grip_min:
            frac = (ry - st.drag - grip_min) / (grip_max - grip_min)
            st.offset = round(max(0.0, min(1.0, frac)) * max_scroll)
        st.offset = max(0, min(max_scroll, st.offset))
        grip_y = grip_min + round((grip_max - grip_min) * st.offset / max_scroll)
        self._draw_scrollbar(st, col, up_row, down_row, grip_y)
        return st.offset

    def _draw_scrollbar(self, st: ScrollState, col: int, up_row: int, down_row: int,
                        grip_y: int) -> None:
        hold = st.hold
        ui_atlas.blit_tile(self.raw, ui_atlas.SCROLL_UP_PRESSED if hold and hold[0] < 0
                           else ui_atlas.SCROLL_UP, col, up_row)
        for r in range(up_row + 1, down_row):
            ui_atlas.blit_tile(self.raw, ui_atlas.SCROLL_TRACK, col, r)
        ui_atlas.blit_tile(self.raw, ui_atlas.SCROLL_DOWN_PRESSED if hold and hold[0] > 0
                           else ui_atlas.SCROLL_DOWN, col, down_row)
        ui_atlas.blit_tile_px(self.raw, ui_atlas.SCROLL_GRIP_PRESSED if st.drag is not None
                              else ui_atlas.SCROLL_GRIP, col * T, grip_y)

    # ── Peças do kit (20/10/2026) ────────────────────────────────────────

    def checkbox(self, cb_id: str, tx: int, ty: int, checked: bool, label: str = "",
                 color=None, font=None) -> bool:
        """Checkbox (1 tile) + rótulo à direita (clicável junto). True =
        alternou (no soltar) — o chamador inverte o próprio estado."""
        ui_atlas.blit_tile(self.raw, ui_atlas.CHECKBOX_ON if checked else ui_atlas.CHECKBOX_OFF, tx, ty)
        x1 = (tx + 1) * T
        if label:
            self.text(label, x1 + 4, ty * T + T / 2, color, font, anchor="midleft")
            x1 += 8 + (font or self.font).size(label)[0] / S
        return self.press(cb_id, tx * T, ty * T, x1, (ty + 1) * T)[1]

    def tabs(self, tabs_id: str, tx: int, ty: int, labels: list, active: int,
             font=None, min_tiles: int = 3) -> "int | None":
        """Fileira de abas (1 linha) a partir de (tx, ty), dentro do painel,
        encostada na borda de cima. Largura de cada uma pelo texto. Devolve
        o índice clicado (no soltar) ou None."""
        clicked = None
        col = tx
        for i, label in enumerate(labels):
            w = self.tiles_for([label], min_tiles, font)
            on = i == active
            ui_atlas.blit_tab(self.raw, col, ty, w, on)
            self.text(label, (col + w / 2) * T, ty * T + T / 2 + 1,
                      ACTIVE_COL if on else BTN_TXT, font)
            if not on and self.press(f"{tabs_id}:{i}", col * T, ty * T, (col + w) * T, (ty + 1) * T)[1]:
                clicked = i
            col += w
        return clicked

    def slot(self, slot_id: str, x: float, y: float, icon=None, count: int = 0,
             dark: bool = False, selected: bool = False, dim: bool = False,
             enabled: bool = True, count_font=None) -> tuple:
        """Slot de item (1 tile) em px RAW livre. Ícone (Surface de TELA, já
        no tamanho `slot_icon_px()`) vai pras sobreposições. Devolve
        (hover, disparou) — clique no soltar."""
        ui_atlas.blit_tile_px(self.raw, ui_atlas.SLOT_DARK if dark else ui_atlas.SLOT, round(x), round(y))
        if icon is not None:
            ic = icon
            if dim:
                ic = icon.copy()
                ic.fill((90, 90, 90, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self.overlays.append((ic, x + 1, y + 1))
        if selected:
            ring = pygame.Surface((T * S, T * S), pygame.SRCALPHA)
            pygame.draw.rect(ring, (255, 215, 60), ring.get_rect(), 2)
            self.overlays.append((ring, x, y))
        if count > 1:
            f = count_font or self.font
            t = f.render(str(count), False, (255, 255, 255))
            px = (x + T - 1) * S - t.get_width()
            py = (y + T - 1) * S - t.get_height()
            self.overlays.append((t, px / S, py / S))
        hov = self.hover(x, y, x + T, y + T)
        fired = self.press(slot_id, x, y, x + T, y + T)[1] if enabled else False
        return hov, fired

    def grid(self, grid_id: str, tx: int, ty: int, cols: int, rows: int, cells: list,
             dark: bool = True, gap: int = 0, selected: int = -1, count_font=None) -> tuple:
        """Grade cols x rows de slots a partir do tile (tx, ty). `cells[i]` =
        (ícone, quantidade) ou None (vazio). Devolve (índice sob o mouse,
        índice clicado) — -1 quando nenhum."""
        hov_i, hit_i = -1, -1
        for i in range(cols * rows):
            c, r = i % cols, i // cols
            x, y = (tx + c) * T + c * gap, (ty + r) * T + r * gap
            cell = cells[i] if i < len(cells) else None
            icon, count = cell if cell else (None, 0)
            hov, fired = self.slot(f"{grid_id}:{i}", x, y, icon, count, dark=dark,
                                   selected=i == selected, count_font=count_font)
            if hov:
                hov_i = i
            if fired:
                hit_i = i
        return hov_i, hit_i

    def text_block(self, text: str, x: float, y: float, max_w: float, color=None,
                   font=None, anchor: str = "topleft", line_gap: int = 0) -> float:
        """Texto com quebra de linha em `max_w` px RAW a partir de (x, y).
        Devolve o y RAW logo abaixo da última linha."""
        f = font or self.font
        lh = math.ceil(f.get_linesize() / S) + line_gap
        for line in wrap_text(text, int(max_w * S), f):
            self.text(line, x, y, color, font, anchor=anchor)
            y += lh
        return y

    def divider_h(self, y: int, x0: int, x1: int) -> None:
        """Linha divisória horizontal de 1px RAW (cor do contorno da moldura)."""
        pygame.draw.line(self.raw, ui_atlas.DIVIDER_COL, (x0, y), (x1 - 1, y))

    def divider_v(self, x: int, y0: int, y1: int) -> None:
        pygame.draw.line(self.raw, ui_atlas.DIVIDER_COL, (x, y0), (x, y1 - 1))

    def close_button(self, close_id: str, tx: int, ty: int = 0) -> bool:
        """Área do X da barra de título (já carimbado pelo painel com título).
        True = clicado (no soltar)."""
        return self.press(close_id, tx * T, ty * T, (tx + 1) * T, (ty + 1) * T)[1]

    def end(self, title: str = "", title_col=TITLE_COL) -> None:
        """Amplia o painel, desenha título e textos e encerra o estado de
        botão pressionado ao soltar o mouse."""
        self.screen.blit(ui_atlas.scale_up(self.raw), (self.px, self.py))
        if title:
            surf = self.font.render(title, False, title_col)
            self.screen.blit(surf, surf.get_rect(center=(self.px + self.pw // 2, self.py + TITLE_Y * S)))
        draw_texts(self.screen, self.texts, self.font, self.px, self.py)
        draw_overlays(self.screen, self.overlays, self.px, self.py)
        if self.released or not self.down:
            self.press_state.pressed = ""


def slot_icon_px() -> int:
    """Tamanho em px de TELA do ícone dentro de um slot (1px de borda RAW)."""
    return (T - 2) * S
