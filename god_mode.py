"""
god_mode.py — Editor de mapa em tempo real.

Ativação: F10

Controles:
  Clique esquerdo no mapa      — seleciona o tile (efeito pulsante)
  Arrasto com botão esquerdo   — pinta continuamente com o elemento da paleta
  Delete (com tile selecionado)— apaga o elemento do tile selecionado
  Ctrl+S                       — salva os dois CSVs
  F10 / Escape                 — fecha o editor
"""
from __future__ import annotations
import csv
import os
import pygame
from components import Tilemap
from tileset import (TILE_MAPPING, OBJECT_MAPPING, OBJECT_CHARS,
                     OBJECT_UNDERLYING, FLOOR_TILE, TILE_SIZE, SPRITE_FAMILIES,
                     SHEET_FAMILIES, SHEET_TILE_MAP, get_collision_offsets,
                     OBJECT_SHEET_FAMILIES, OBJECT_SHEET_TILE_MAP)
from fonts import make as _font

# ── Paletas ───────────────────────────────────────────────────────────────────

_TERRAIN_PALETTE = [
    ("G", "Grama",       ( 58, 100,  48)),
    ("W", "Água",        ( 40,  88, 165)),
    ("~", "Areia",       (195, 172, 105)),
    ("f", "Terra",       (110,  82,  52)),
    (".", "Piso Pedra",  ( 95,  88,  78)),
    ("_", "Chão",        ( 50,  50,  50)),
    ("m", "Montanha",    ( 62,  55,  48)),
    ("#", "Parede",      (115, 108,  98)),
    ("@", "Ruínas",      ( 88,  65,  42)),
    ("c", "Cav. Parede", ( 48,  38,  28)),
    ("d", "Cav. Entrada",( 18,  12,   8)),
    ("O", "Portal",      (200, 160,   0)),
]

def _build_object_palette() -> list:
    """Paleta base + sprites descobertos via SPRITE_FAMILIES (auto-expandido)."""
    palette = [
        (".", "Apagar", ( 80,  80,  80)),
        ("k", "Pedra",  (100,  90,  78)),
    ]
    # Famílias genéricas: entradas individuais (t, b, ...) e numeradas (t1, t2, ...)
    for prefix, _base_name, color, _solid, label, *_ in SPRITE_FAMILIES:
        # Entrada genérica do char base (ex: "t" → fallback sem sprite numerado)
        if prefix in OBJECT_MAPPING:
            palette.append((prefix, label, color))
        # Entradas numeradas descobertas (t1, t2, g1, g2, ...)
        i = 1
        while True:
            char = f"{prefix}{i}"
            tile = OBJECT_MAPPING.get(char)
            if tile is None:
                break
            palette.append((char, f"{label} {i}", tile.color))
            i += 1
    return palette

_OBJECT_PALETTE = _build_object_palette()

# ── Cores do painel ──────────────────────────────────────────────────────────

_C_PANEL    = ( 28,  28,  34)
_C_BORDER   = ( 60,  60,  72)
_C_SEL      = (255, 220,  50)
_C_TAB_ACT  = ( 50,  50,  62)
_C_TAB_IDLE = ( 38,  38,  48)
_C_TEXT     = (210, 210, 220)
_C_SAVE_N   = ( 50, 130,  60)
_C_SAVE_H   = ( 70, 170,  80)
_C_SAVE_OK  = ( 40, 200,  80)

# ── Layout base (referência 720p) — multiplicado pelo ui_scale em runtime ─────

_BASE_PANEL_W = 220
_BASE_MARGIN  = 10
_COLS         = 4


class GodModeEditor:
    """Editor de mapa em tempo real."""

    def __init__(self, world, screen,
                 tile_render_system,
                 get_map_file,        # callable → str (caminho do CSV atual)
                 on_map_changed=None  # callable chamado após salvar (atualiza minimap)
                 ):
        self._world       = world
        self._screen      = screen
        self._tile_render = tile_render_system
        self._get_map     = get_map_file
        self._on_changed  = on_map_changed
        self._active      = False

        self._tab         = 0          # 0 = Terreno, 1 = Objetos, 2 = Folhas
        self._sel_char    = "G"
        self._sel_name    = "Grama"
        self._sel_color   = ( 58, 100, 48)

        self._hover_tile    = None     # (tx, ty) tile sob o cursor
        self._selected_tile = None     # (tx, ty) último tile pintado/clicado
        self._painting      = False    # botão esquerdo mantido (arrasto)
        self._pulse_timer   = 0.0      # timer para animação pulsante do tile selecionado
        self._save_flash    = 0.0      # timer para flash de "salvo"
        self._unsaved       = False

        self._ui_scale: float = 0.0   # detectado em render; 0 força rebuild
        self._font_sm  = _font(14)
        self._font_med = _font(16)
        self._panel_w  = _BASE_PANEL_W
        self._margin   = _BASE_MARGIN
        self._slot     = (_BASE_PANEL_W - _BASE_MARGIN * 2) // _COLS
        self._swatch   = self._slot - 6

        # Grid overlay — superfície semi-transparente reusada
        self._grid_surf: pygame.Surface | None = None
        self._grid_size: tuple | None = None

        # Tabs
        self._tab_rects: list[pygame.Rect] = []
        self._palette_rects: list[tuple[pygame.Rect, str]] = []
        self._sheet_cell_rects: list[tuple[pygame.Rect, str, str, tuple]] = []
        self._obj_sheet_cell_rects: list[tuple[pygame.Rect, str, str, tuple]] = []

        # Scroll state — por aba
        self._scroll_y:          dict[int, int] = {0: 0, 1: 0}
        self._content_h:         dict[int, int] = {0: 0, 1: 0}
        self._scroll_viewport_h: int = 0
        self._viewport_top:      int = 0
        self._scrollbar_rect:       "pygame.Rect | None" = None
        self._scrollbar_thumb_rect: "pygame.Rect | None" = None
        self._dragging_scroll:   bool = False
        self._drag_start_y:      int  = 0
        self._drag_start_scroll: int  = 0

    # ── Ativação ──────────────────────────────────────────────────────────────

    def toggle(self):
        self._active = not self._active
        if self._active:
            self._sel_char  = "G"
            self._sel_name  = "Grama"
            self._sel_color = ( 58, 100, 48)
            self._tab       = 0

    @property
    def active(self) -> bool:
        return self._active

    # ── Eventos ───────────────────────────────────────────────────────────────

    def handle_events(self, events: list, cam_x: float, cam_y: float) -> bool:
        """Processa eventos. Retorna True se consumiu algo (bloqueia input do jogo)."""
        if not self._active:
            return False

        mx, my = pygame.mouse.get_pos()
        in_panel = mx >= self._screen.get_width() - self._panel_w
        consumed = False

        for event in events:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F10 or event.key == pygame.K_ESCAPE:
                    self._active = False
                    return True
                mods = pygame.key.get_mods()
                if event.key == pygame.K_s and (mods & pygame.KMOD_CTRL):
                    self._save()
                    consumed = True
                elif event.key == pygame.K_DELETE:
                    if self._selected_tile is not None:
                        self._erase(*self._selected_tile)
                        self._selected_tile = None
                    consumed = True

            elif event.type == pygame.MOUSEWHEEL:
                if in_panel:
                    self._scroll(self._tab, -event.y * 30)
                    consumed = True

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and self._scrollbar_thumb_rect and \
                        self._scrollbar_thumb_rect.collidepoint(event.pos):
                    self._dragging_scroll   = True
                    self._drag_start_y      = event.pos[1]
                    self._drag_start_scroll = self._scroll_y.get(self._tab, 0)
                    consumed = True
                elif event.button == 1 and self._scrollbar_rect and \
                        self._scrollbar_rect.collidepoint(event.pos) and \
                        (self._scrollbar_thumb_rect is None or
                         not self._scrollbar_thumb_rect.collidepoint(event.pos)):
                    # Clique na trilha: pula para a posição
                    ratio = ((event.pos[1] - self._scrollbar_rect.top)
                             / max(self._scrollbar_rect.height, 1))
                    ch = self._content_h.get(self._tab, 0)
                    self._scroll_y[self._tab] = int(ratio * max(0, ch - self._scroll_viewport_h))
                    consumed = True
                elif in_panel:
                    self._handle_panel_click(event.pos, event.button)
                    consumed = True
                elif event.button == 1:
                    tx, ty = self._screen_to_tile(event.pos[0], event.pos[1], cam_x, cam_y)
                    self._apply(tx, ty)
                    self._selected_tile = (tx, ty)
                    self._pulse_timer   = 0.0
                    self._painting      = True
                    consumed = True

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1:
                    self._dragging_scroll = False
                    self._painting        = False

            elif event.type == pygame.MOUSEMOTION:
                tx, ty = self._screen_to_tile(mx, my, cam_x, cam_y)
                self._hover_tile = (tx, ty)
                if self._dragging_scroll:
                    dy  = event.pos[1] - self._drag_start_y
                    ch  = self._content_h.get(self._tab, 0)
                    vh  = self._scroll_viewport_h
                    if ch > vh:
                        thumb_h     = max(20, int(vh * vh / ch))
                        track_range = vh - thumb_h
                        scroll_range = ch - vh
                        delta = int(dy * scroll_range / max(track_range, 1))
                        self._scroll_y[self._tab] = max(0, min(
                            scroll_range, self._drag_start_scroll + delta))
                    consumed = True
                elif in_panel:
                    consumed = True

        return consumed or in_panel

    def _scroll(self, tab: int, delta: int) -> None:
        ch  = self._content_h.get(tab, 0)
        vh  = self._scroll_viewport_h
        cur = self._scroll_y.get(tab, 0)
        self._scroll_y[tab] = max(0, min(max(0, ch - vh), cur + delta))

    def update(self, cam_x: float, cam_y: float, dt: float = 0):
        """Chamado a cada frame para pintura contínua ao arrastar e animação."""
        if not self._active:
            return
        if self._save_flash > 0:
            self._save_flash -= dt
        self._pulse_timer += dt
        # Arrasto: pinta o tile sob o cursor a cada frame
        if self._painting:
            mx, my = pygame.mouse.get_pos()
            if mx < self._screen.get_width() - self._panel_w:
                tx, ty = self._screen_to_tile(mx, my, cam_x, cam_y)
                # Só pinta se mudou de tile (evita invalidar cache a cada frame)
                if (tx, ty) != self._selected_tile:
                    self._apply(tx, ty)
                    self._selected_tile = (tx, ty)

    # ── UI scale adaptativo ───────────────────────────────────────────────────

    def _refresh_ui_scale(self):
        """Recalcula dimensões e fontes quando a resolução muda."""
        scale = self._screen.get_height() / 720.0
        if abs(scale - self._ui_scale) < 0.01:
            return  # sem mudança
        self._ui_scale = scale
        self._panel_w  = int(_BASE_PANEL_W * scale)
        self._margin   = max(6, int(_BASE_MARGIN * scale))
        self._slot     = (self._panel_w - self._margin * 2) // _COLS
        self._swatch   = self._slot - int(6 * scale)
        self._font_sm  = _font(max(12, int(14 * scale)))
        self._font_med = _font(max(14, int(16 * scale)))
        # Invalida grid para ser reconstruída no novo tamanho
        self._grid_surf = None
        self._grid_size = None

    # ── Renderização ─────────────────────────────────────────────────────────

    def render(self, cam_x: float, cam_y: float):
        if not self._active:
            return
        self._refresh_ui_scale()
        sw    = self._screen.get_width()
        sh    = self._screen.get_height()
        map_w = sw - self._panel_w

        self._draw_grid(map_w, sh, cam_x, cam_y)
        self._draw_cursor_preview(cam_x, cam_y, map_w)
        self._draw_panel(map_w, sh)

    def _draw_grid(self, map_w: int, sh: int, cam_x: float, cam_y: float):
        """Grade semi-transparente sobre o mapa."""
        ts = TILE_SIZE
        if (self._grid_surf is None
                or self._grid_size != (map_w, sh)):
            surf = pygame.Surface((map_w, sh), pygame.SRCALPHA)
            for x in range(0, map_w, ts):
                pygame.draw.line(surf, (255, 255, 255, 22), (x, 0), (x, sh))
            for y in range(0, sh, ts):
                pygame.draw.line(surf, (255, 255, 255, 22), (0, y), (map_w, y))
            self._grid_surf  = surf
            self._grid_size  = (map_w, sh)

        # Offset para alinhar a grade ao scroll da câmera
        ox = int(cam_x) % ts
        oy = int(cam_y) % ts
        self._screen.blit(self._grid_surf, (-ox, -oy))

    def _draw_cursor_preview(self, cam_x: float, cam_y: float, map_w: int):
        """Destaca o tile sob o cursor e desenha efeito pulsante no tile selecionado."""
        import math
        sh = self._screen.get_height()

        # ── Tile selecionado — borda pulsante ────────────────────────────────
        if self._selected_tile is not None:
            stx, sty = self._selected_tile
            sx = stx * TILE_SIZE - int(cam_x)
            sy = sty * TILE_SIZE - int(cam_y)
            if -TILE_SIZE <= sx < map_w and -TILE_SIZE <= sy < sh:
                pulse = (math.sin(self._pulse_timer * 7) + 1) / 2  # 0..1
                alpha = int(140 + pulse * 115)                       # 140..255
                thick = 2 if pulse < 0.5 else 3
                surf = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
                # Fundo semi-transparente vermelho-laranja
                surf.fill((255, 80, 30, int(40 + pulse * 40)))
                # Borda pulsante
                pygame.draw.rect(surf, (255, 200, 50, alpha),
                                 (0, 0, TILE_SIZE, TILE_SIZE), thick)
                self._screen.blit(surf, (sx, sy))
                # Label "DEL" acima do tile
                lbl = self._font_sm.render("DEL", True, (255, 200, 50))
                self._screen.blit(lbl, (sx + 2, sy - lbl.get_height() - 1))

        # ── Tile sob o cursor — preview do elemento selecionado ───────────────
        mx, my = pygame.mouse.get_pos()
        if mx >= map_w:
            return
        tx, ty = self._screen_to_tile(mx, my, cam_x, cam_y)
        # Não sobrepõe o tile já selecionado
        if (tx, ty) == self._selected_tile:
            return
        sx = tx * TILE_SIZE - int(cam_x)
        sy = ty * TILE_SIZE - int(cam_y)
        if 0 <= sx < map_w and 0 <= sy < sh:
            hl = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
            r, g, b = self._sel_color
            hl.fill((r, g, b, 90))
            pygame.draw.rect(hl, (255, 255, 255, 160), (0, 0, TILE_SIZE, TILE_SIZE), 1)
            self._screen.blit(hl, (sx, sy))

    def _static_section_h(self, sc: float) -> int:
        """Altura da seção fixa (Selecionado + botões + dicas) em pixels."""
        fh      = self._font_sm.get_height()
        sw_size = int(20 * sc)
        btn_h   = int(32 * sc)
        return (
            2 + int(8 * sc)               # separador topo + margem
            + fh + int(4 * sc)            # label "Selecionado:"
            + sw_size + int(8 * sc)       # swatch selecionado
            + fh + int(4 * sc)            # tile hover
            + fh + int(4 * sc)            # unsaved
            + 2 + int(8 * sc)             # separador + margem
            + btn_h + int(8 * sc)         # botão Salvar
            + btn_h + int(12 * sc)        # botão Sair
            + 3 * (fh + int(2 * sc))      # dicas
        )

    def _draw_panel(self, panel_x: int, sh: int):
        """Renderiza o painel lateral com área rolável + seção estática fixa."""
        screen = self._screen
        sw     = screen.get_width()
        PW     = self._panel_w
        M      = self._margin
        sc     = self._ui_scale
        SB_W   = max(6, int(8 * sc))   # largura da scrollbar

        # Fundo + borda
        pygame.draw.rect(screen, _C_PANEL, pygame.Rect(panel_x, 0, PW, sh))
        pygame.draw.line(screen, _C_BORDER, (panel_x, 0), (panel_x, sh), 2)

        y  = int(8 * sc)
        x0 = panel_x + M

        # Título
        title = self._font_med.render("GOD MODE", True, _C_SEL)
        screen.blit(title, (x0, y))
        y += title.get_height() + int(6 * sc)
        pygame.draw.line(screen, _C_BORDER, (panel_x + 4, y), (sw - 4, y))
        y += int(6 * sc)

        # Tabs
        tab_h      = int(28 * sc)
        tab_labels = ("Terreno", "Objetos")
        tab_w      = (PW - M * 2) // len(tab_labels)
        self._tab_rects = []
        for i, label in enumerate(tab_labels):
            r = pygame.Rect(x0 + i * tab_w, y, tab_w, tab_h)
            pygame.draw.rect(screen, _C_TAB_ACT if self._tab == i else _C_TAB_IDLE, r)
            pygame.draw.rect(screen, _C_BORDER, r, 1)
            txt = self._font_sm.render(label, True, _C_TEXT)
            screen.blit(txt, txt.get_rect(center=r.center))
            self._tab_rects.append(r)
        y += tab_h + int(8 * sc)

        # ── Área rolável ──────────────────────────────────────────────────────
        viewport_top    = y
        self._viewport_top = viewport_top
        static_h        = self._static_section_h(sc)
        viewport_h      = max(40, sh - viewport_top - static_h)
        self._scroll_viewport_h = viewport_h

        # Clamp scroll
        tab        = self._tab
        ch         = self._content_h.get(tab, 0)
        scroll_y   = max(0, min(self._scroll_y.get(tab, 0), max(0, ch - viewport_h)))
        self._scroll_y[tab] = scroll_y

        # Largura útil do conteúdo (deixa espaço para a scrollbar)
        avail_w = PW - M * 2 - SB_W

        # Reseta listas de clique
        self._palette_rects        = []
        self._sheet_cell_rects     = []
        self._obj_sheet_cell_rects = []

        # Clip → renderiza conteúdo deslocado pelo scroll
        screen.set_clip(pygame.Rect(panel_x, viewport_top, PW - SB_W, viewport_h))
        cy = self._render_content(screen, x0, viewport_top - scroll_y, avail_w, sc)
        screen.set_clip(None)

        # Atualiza altura total do conteúdo
        self._content_h[tab] = (cy + scroll_y) - viewport_top

        # ── Scrollbar ─────────────────────────────────────────────────────────
        sb_x = panel_x + PW - SB_W
        self._scrollbar_rect = pygame.Rect(sb_x, viewport_top, SB_W, viewport_h)
        pygame.draw.rect(screen, (32, 32, 42), self._scrollbar_rect)

        total_h = self._content_h.get(tab, 0)
        if total_h > viewport_h:
            thumb_h = max(20, int(viewport_h * viewport_h / total_h))
            thumb_y = viewport_top + int(
                scroll_y * (viewport_h - thumb_h) / max(total_h - viewport_h, 1)
            )
            self._scrollbar_thumb_rect = pygame.Rect(sb_x + 1, thumb_y, SB_W - 2, thumb_h)
            col_sb = (150, 150, 170) if self._dragging_scroll else (100, 100, 120)
            pygame.draw.rect(screen, col_sb, self._scrollbar_thumb_rect, border_radius=3)
        else:
            self._scrollbar_thumb_rect = None

        # ── Seção estática ────────────────────────────────────────────────────
        y = viewport_top + viewport_h

        pygame.draw.line(screen, _C_BORDER, (panel_x + 4, y), (sw - 4, y))
        y += int(8 * sc)

        txt = self._font_sm.render("Selecionado:", True, (160, 160, 170))
        screen.blit(txt, (x0, y))
        y += txt.get_height() + int(4 * sc)

        sw_size = int(20 * sc)
        sw_r    = pygame.Rect(x0, y, sw_size, sw_size)
        pygame.draw.rect(screen, self._sel_color, sw_r, border_radius=3)
        sel_lbl = self._font_sm.render(f"[{self._sel_char}] {self._sel_name}", True, _C_TEXT)
        screen.blit(sel_lbl, (x0 + sw_size + int(6 * sc), y + 2))
        y += sw_size + int(8 * sc)

        if self._hover_tile:
            hx, hy = self._hover_tile
            screen.blit(self._font_sm.render(f"Tile: ({hx}, {hy})", True, (150, 150, 160)), (x0, y))
        y += self._font_sm.get_height() + int(4 * sc)

        if self._unsaved:
            screen.blit(self._font_sm.render("* alteracoes nao salvas", True, (220, 120, 60)), (x0, y))
        y += self._font_sm.get_height() + int(4 * sc)

        pygame.draw.line(screen, _C_BORDER, (panel_x + 4, y), (sw - 4, y))
        y += int(8 * sc)

        save_w = PW - M * 2
        save_h = int(32 * sc)

        save_rect = pygame.Rect(x0, y, save_w, save_h)
        if self._save_flash > 0:
            btn_col, btn_lbl = _C_SAVE_OK, "Salvo!"
        else:
            mx_now, my_now = pygame.mouse.get_pos()
            btn_col = _C_SAVE_H if save_rect.collidepoint(mx_now, my_now) else _C_SAVE_N
            btn_lbl = "Salvar  (Ctrl+S)"
        pygame.draw.rect(screen, btn_col, save_rect, border_radius=6)
        pygame.draw.rect(screen, _C_BORDER, save_rect, 1, border_radius=6)
        s_lbl = self._font_med.render(btn_lbl, True, (240, 240, 240))
        screen.blit(s_lbl, s_lbl.get_rect(center=save_rect.center))
        self._save_btn_rect = save_rect
        y += save_h + int(8 * sc)

        quit_rect = pygame.Rect(x0, y, save_w, save_h)
        mx_now, my_now = pygame.mouse.get_pos()
        quit_col = (110, 40, 40) if quit_rect.collidepoint(mx_now, my_now) else (70, 28, 28)
        pygame.draw.rect(screen, quit_col, quit_rect, border_radius=6)
        pygame.draw.rect(screen, (140, 60, 60), quit_rect, 1, border_radius=6)
        q_lbl = self._font_med.render("Sair  (F10 / Esc)", True, (240, 200, 200))
        screen.blit(q_lbl, q_lbl.get_rect(center=quit_rect.center))
        self._quit_btn_rect = quit_rect
        y += save_h + int(12 * sc)

        for hint in ("Clique — seleciona tile", "Arrasto — pinta", "Delete — apaga selecionado"):
            h = self._font_sm.render(hint, True, (100, 100, 110))
            screen.blit(h, (x0, y))
            y += h.get_height() + int(2 * sc)

    def _render_content(self, screen: "pygame.Surface", x0: int, cy: int,
                        avail_w: int, sc: float) -> int:
        """
        Renderiza paleta + sheet pickers dentro da área rolável.
        cy — y inicial já com offset de scroll aplicado.
        Retorna y final (posição logo abaixo do último elemento).
        """
        SLOT   = self._slot
        SWATCH = self._swatch

        # Swatches da paleta principal
        palette = _TERRAIN_PALETTE if self._tab == 0 else _OBJECT_PALETTE
        col   = 0
        row_y = cy
        for char, name, color in palette:
            cx_sw       = x0 + col * SLOT
            swatch_rect = pygame.Rect(cx_sw, row_y, SWATCH, SWATCH)
            pygame.draw.rect(screen, color, swatch_rect, border_radius=4)

            tile = OBJECT_MAPPING.get(char)
            if tile and getattr(tile, "sprite_name", ""):
                from tile_sprite_manager import TILE_SPRITES
                raw = TILE_SPRITES.get_raw_sprite(tile.sprite_name)
                if raw:
                    screen.blit(pygame.transform.smoothscale(raw, (SWATCH - 2, SWATCH - 2)),
                                (cx_sw + 1, row_y + 1))

            bw = 2 if char == self._sel_char else 1
            pygame.draw.rect(screen, _C_SEL if char == self._sel_char else _C_BORDER,
                             swatch_rect, bw, border_radius=4)
            lbl = self._font_sm.render(char, True, (255, 255, 255) if tile and getattr(tile, "sprite_name", "") else (230, 230, 230))
            screen.blit(lbl, (swatch_rect.right - lbl.get_width() - 2,
                               swatch_rect.bottom - lbl.get_height() - 2))
            self._palette_rects.append((swatch_rect, char, name, color))
            col += 1
            if col >= _COLS:
                col   = 0
                row_y += SLOT
        cy = row_y + (SLOT if col > 0 else 0) + int(8 * sc)

        # Sheet picker de terreno (tab 0)
        if self._tab == 0 and SHEET_FAMILIES:
            from tile_sprite_manager import TILE_SPRITES
            sep = self._font_sm.render("Tiles de terreno:", True, (140, 140, 150))
            screen.blit(sep, (x0, cy))
            cy += sep.get_height() + int(4 * sc)

            for fam in SHEET_FAMILIES:
                prefix = fam["prefix"]
                cols = max((int(t.split("_")[1]) for t in SHEET_TILE_MAP
                            if t.startswith(prefix + "_")), default=-1) + 1
                rows = max((int(t.split("_")[2]) for t in SHEET_TILE_MAP
                            if t.startswith(prefix + "_")), default=-1) + 1
                if not cols or not rows:
                    continue
                cell_size = max(16, min(avail_w // cols, int(36 * sc)))
                for row in range(rows):
                    for col in range(cols):
                        tid  = f"{prefix}_{col}_{row}"
                        cx_t = x0 + col * cell_size
                        cr   = pygame.Rect(cx_t, cy, cell_size, cell_size)
                        pygame.draw.rect(screen, fam["color"], cr)
                        raw = TILE_SPRITES.get_raw_sprite(tid)
                        if raw:
                            screen.blit(pygame.transform.smoothscale(raw, (cell_size, cell_size)), (cx_t, cy))
                        pygame.draw.rect(screen, _C_SEL if tid == self._sel_char else _C_BORDER, cr,
                                         2 if tid == self._sel_char else 1)
                        self._sheet_cell_rects.append((cr, tid, f"{fam['label']} ({col},{row})", fam["color"]))
                    cy += cell_size
                cy += int(6 * sc)

        # Sheet picker de objetos (tab 1)
        elif self._tab == 1 and OBJECT_SHEET_FAMILIES:
            from tile_sprite_manager import TILE_SPRITES
            sep = self._font_sm.render("Tiles de objetos:", True, (140, 140, 150))
            screen.blit(sep, (x0, cy))
            cy += sep.get_height() + int(4 * sc)

            for fam in OBJECT_SHEET_FAMILIES:
                prefix       = fam["prefix"]
                family_tiles = [(tid, info) for tid, info in OBJECT_SHEET_TILE_MAP.items()
                                if tid.startswith(prefix + "_")]
                if not family_tiles:
                    continue
                lbl = self._font_sm.render(fam["label"], True, (140, 140, 150))
                screen.blit(lbl, (x0, cy))
                cy += lbl.get_height() + int(3 * sc)

                cell_w  = max(16, min(avail_w // 8, int(36 * sc)))
                cur_x   = x0
                row_y   = cy
                row_h   = 0
                for tid, (_sf, _sx, _sy, tw, th) in family_tiles:
                    cell_h = max(int(cell_w * th / max(tw, 1)), cell_w)
                    if cur_x + cell_w > x0 + avail_w:
                        cur_x = x0
                        row_y += row_h
                        row_h  = 0
                    row_h = max(row_h, cell_h)
                    cr = pygame.Rect(cur_x, row_y, cell_w, cell_h)
                    pygame.draw.rect(screen, fam["color"], cr)
                    raw = TILE_SPRITES.get_raw_sprite(tid)
                    if raw:
                        screen.blit(pygame.transform.smoothscale(raw, (cell_w, cell_h)), (cur_x, row_y))
                    pygame.draw.rect(screen, _C_SEL if tid == self._sel_char else _C_BORDER, cr,
                                     2 if tid == self._sel_char else 1)
                    self._obj_sheet_cell_rects.append((cr, tid, f"{fam['label']}: {tid}", fam["color"]))
                    cur_x += cell_w
                cy = row_y + row_h + int(6 * sc)

        return cy

    # ── Interação com a paleta ────────────────────────────────────────────────

    def _handle_panel_click(self, pos, button):
        # Tabs (sempre clicáveis)
        for i, r in enumerate(self._tab_rects):
            if r.collidepoint(pos):
                if i != self._tab:
                    self._scroll_y[i] = 0   # reseta scroll ao trocar de aba
                self._tab = i
                if i == 0:
                    self._sel_char, self._sel_name, self._sel_color = _TERRAIN_PALETTE[0]
                elif i == 1:
                    self._sel_char, self._sel_name, self._sel_color = _OBJECT_PALETTE[0]
                self._selected_tile = None
                return

        # Itens da área rolável — só responde se o clique está dentro do viewport
        in_viewport = (self._scroll_viewport_h > 0
                       and self._viewport_top <= pos[1] <= self._viewport_top + self._scroll_viewport_h)

        if in_viewport:
            for r, char, name, color in self._palette_rects:
                if r.collidepoint(pos):
                    self._sel_char, self._sel_name, self._sel_color = char, name, color
                    return

            for r, tile_id, name, color in self._sheet_cell_rects:
                if r.collidepoint(pos):
                    self._sel_char, self._sel_name, self._sel_color = tile_id, name, color
                    return

            for r, tile_id, name, color in self._obj_sheet_cell_rects:
                if r.collidepoint(pos):
                    self._sel_char, self._sel_name, self._sel_color = tile_id, name, color
                    return

        # Botões estáticos (sempre clicáveis)
        if hasattr(self, "_save_btn_rect") and self._save_btn_rect.collidepoint(pos):
            self._save()
            return

        if hasattr(self, "_quit_btn_rect") and self._quit_btn_rect.collidepoint(pos):
            self._active = False

    # ── Edição de tiles ───────────────────────────────────────────────────────

    def _screen_to_tile(self, mx: int, my: int,
                        cam_x: float, cam_y: float) -> tuple[int, int]:
        tx = int((mx + int(cam_x)) / TILE_SIZE)
        ty = int((my + int(cam_y)) / TILE_SIZE)
        return tx, ty

    def _apply_at_screen(self, pos, cam_x: float, cam_y: float):
        tx, ty = self._screen_to_tile(pos[0], pos[1], cam_x, cam_y)
        self._apply(tx, ty)

    def _erase_at_screen(self, pos, cam_x: float, cam_y: float):
        tx, ty = self._screen_to_tile(pos[0], pos[1], cam_x, cam_y)
        self._erase(tx, ty)

    def _apply(self, tx: int, ty: int):
        tilemap = self._get_tilemap()
        if tilemap is None:
            return
        if not (0 <= ty < tilemap.map_height_tiles
                and 0 <= tx < tilemap.map_width_tiles):
            return

        char = self._sel_char
        if char in SHEET_TILE_MAP:
            # Sheet tile → visual override de terreno, nunca toca objects
            self._set_terrain_visual(tilemap, tx, ty, char)
        elif self._tab == 0:
            if char in OBJECT_CHARS:
                return
            self._set_terrain(tilemap, tx, ty, char)
        elif self._tab == 1:
            self._set_object(tilemap, tx, ty, char)

        self._unsaved = True
        self._tile_render.invalidate_cache()

    def _erase(self, tx: int, ty: int):
        tilemap = self._get_tilemap()
        if tilemap is None:
            return
        if self._tab == 0:
            self._set_terrain(tilemap, tx, ty, "G")
            self._set_terrain_visual(tilemap, tx, ty, "")
        elif self._tab == 1:
            self._set_object(tilemap, tx, ty, ".")
        self._unsaved = True
        self._tile_render.invalidate_cache()

    def _set_terrain(self, tilemap: "Tilemap", tx: int, ty: int, char: str):
        row = tilemap.terrain_matrix[ty]
        if tx >= len(row):
            return
        tilemap.terrain_matrix[ty] = row[:tx] + char + row[tx + 1:]
        # tile_matrix: terreno, a menos que haja um objeto sobre ele
        obj_char = (tilemap.object_matrix[ty][tx]
                    if ty < len(tilemap.object_matrix) and tx < len(tilemap.object_matrix[ty])
                    else ".")
        if obj_char and obj_char != "." and obj_char in OBJECT_CHARS:
            tile_type = OBJECT_MAPPING.get(obj_char)
        else:
            tile_type = TILE_MAPPING.get(char, FLOOR_TILE)
        if ty < len(tilemap.tile_matrix) and tx < len(tilemap.tile_matrix[ty]):
            tilemap.tile_matrix[ty][tx] = tile_type

    def _set_object(self, tilemap: "Tilemap", tx: int, ty: int, char: str):
        obj_row = tilemap.object_matrix[ty]
        if tx >= len(obj_row):
            return

        map_h = len(tilemap.tile_matrix)

        # 1. Limpa tile_matrix do objeto ANTIGO (se não era puramente decorativo)
        old_char = obj_row[tx]
        if old_char and old_char != "." and old_char in OBJECT_CHARS:
            old_tile = OBJECT_MAPPING.get(old_char)
            _old_not_deco = old_tile and (
                old_tile.is_solid or old_tile.elevation > 0 or old_tile.is_transition
            )
            if _old_not_deco:
                for dx, dy in get_collision_offsets(old_tile):
                    nr, nc = ty + dy, tx + dx
                    if not (0 <= nr < map_h and 0 <= nc < len(tilemap.tile_matrix[nr])):
                        continue
                    # Restaura para o terreno subjacente (ou outro objeto sólido, se houver)
                    other = (tilemap.object_matrix[nr][nc]
                             if nr < len(tilemap.object_matrix) and nc < len(tilemap.object_matrix[nr])
                             else ".")
                    other_tile = OBJECT_MAPPING.get(other) if other and other != "." else None
                    if other_tile and other_tile.is_solid and (nr != ty or nc != tx):
                        tilemap.tile_matrix[nr][nc] = other_tile
                    else:
                        tc = (tilemap.terrain_matrix[nr][nc]
                              if nr < len(tilemap.terrain_matrix) and nc < len(tilemap.terrain_matrix[nr])
                              else "G")
                        tilemap.tile_matrix[nr][nc] = TILE_MAPPING.get(tc, FLOOR_TILE)

        # 2. Atualiza object_matrix
        row_copy = list(obj_row)
        row_copy[tx] = char
        tilemap.object_matrix[ty] = row_copy

        # 3. Aplica tile_matrix para o objeto NOVO.
        # Objetos puramente decorativos (is_solid=False, elevation=0, não-transição)
        # não tocam tile_matrix, preservando a colisão do terreno subjacente.
        # Objetos com elevation>0 ou is_transition PRECISAM atualizar tile_matrix
        # para que o sistema de pisos (TileValidationSystem) os enxergue.
        if char and char != "." and char in OBJECT_CHARS:
            new_tile = OBJECT_MAPPING.get(char, FLOOR_TILE)
            purely_decorative = (
                not new_tile.is_solid
                and new_tile.elevation == 0
                and not new_tile.is_transition
            )
            if not purely_decorative:
                for dx, dy in get_collision_offsets(new_tile):
                    nr, nc = ty + dy, tx + dx
                    if 0 <= nr < map_h and 0 <= nc < len(tilemap.tile_matrix[nr]):
                        tilemap.tile_matrix[nr][nc] = new_tile
        else:
            # Removendo objeto: base tile volta ao terreno
            tc = tilemap.terrain_matrix[ty][tx] if tx < len(tilemap.terrain_matrix[ty]) else "G"
            if ty < map_h and tx < len(tilemap.tile_matrix[ty]):
                tilemap.tile_matrix[ty][tx] = TILE_MAPPING.get(tc, FLOOR_TILE)

    def _set_terrain_visual(self, tilemap: "Tilemap", tx: int, ty: int, sprite_id: str):
        """Escreve (ou limpa) o visual override de terreno sem tocar nos objetos."""
        vis = tilemap.terrain_visual
        if vis is None or ty >= len(vis) or tx >= len(vis[ty]):
            return
        vis[ty][tx] = sprite_id

    # ── Salvamento ────────────────────────────────────────────────────────────

    def _save(self):
        tilemap = self._get_tilemap()
        if tilemap is None:
            return

        map_file = self._get_map()
        base     = os.path.splitext(map_file)[0]
        t_path   = base + "_terrain.csv"
        o_path   = base + "_objects.csv"

        # Formato unificado: célula é o sprite de sheet quando há override visual,
        # senão é o char de terreno normal (G, #, W...).
        vis  = tilemap.terrain_visual or []
        merged = []
        for r, terrain_row in enumerate(tilemap.terrain_matrix):
            merged_row = []
            vis_row = vis[r] if r < len(vis) else []
            for c, tc in enumerate(terrain_row):
                vis_id = vis_row[c] if c < len(vis_row) else ""
                merged_row.append(vis_id if vis_id else tc)
            merged.append(merged_row)

        self._write_csv(t_path, merged)
        self._write_csv(o_path, tilemap.object_matrix)

        # Remove arquivo legado de sprites separado, se ainda existir
        import os as _os
        v_path = base + "_terrain_sprites.csv"
        if _os.path.exists(v_path):
            _os.remove(v_path)

        self._unsaved    = False
        self._save_flash = 1.5

        if self._on_changed:
            self._on_changed()

    @staticmethod
    def _write_csv(path: str, matrix):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in matrix:
                # row pode ser str (terrain) ou list[str] (objects — suporta IDs multi-char)
                writer.writerow(list(row) if isinstance(row, str) else row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_tilemap(self) -> "Tilemap | None":
        for _, tm in self._world.get_entities_with(Tilemap):
            return tm
        return None
