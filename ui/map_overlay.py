"""
MapOverlay — Visão geral do mapa (tecla M).
Escala ajustável de 2 a 100 px/tile. Scroll com roda do mouse, arrastar para mover.
"""
from __future__ import annotations
import math
import pygame
from ui.ui_helpers import fill_surf
from engine.tileset import TILE_MAPPING, OBJECT_MAPPING, FLOOR_TILE
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI


class MapOverlay(UIScaleMixin):
    MIN_SCALE  = 2.0
    MAX_SCALE  = 100.0
    W_RATIO    = UI.MAP_OVERLAY_W_RATIO
    H_RATIO    = UI.MAP_OVERLAY_H_RATIO
    BORDER_COL = (140, 100, 60)
    BG_COL     = (8, 6, 4)

    _FONT_BASES = {"_font": 20}

    # Cor-chave do fog 1px/tile (pixel "explorado" = transparente via colorkey)
    _FOG_KEY = (255, 0, 255)

    def __init__(self, screen: pygame.Surface):
        super().__init__()
        self.screen   = screen
        self.is_open  = False
        self._surfaces: dict = {}              # map_file → pygame.Surface (1px/tile)
        self._dims: dict = {}                  # map_file → (cols, rows)
        # Fog 1px/tile por mapa: preto = não explorado; pixel _FOG_KEY = explorado
        # (transparente via colorkey). Escala junto com o mapa — substitui o loop
        # Python de ~100k tiles/frame que derrubava o FPS no zoom mínimo.
        self._fog_surfs: dict = {}             # map_file → pygame.Surface (1px/tile)
        self._fog_cleared: dict = {}           # map_file → set de tiles já revelados no fog
        # Cache da view composta (mapa+fog escalados): rebuild só quando o
        # viewport/zoom/fog mudam — parado, o render é 1 blit.
        self._view_canvas: "pygame.Surface | None" = None
        self._view_key: tuple = ()
        self._active_key: str = ""             # chave do mapa atual
        self._cols = 0
        self._rows = 0
        self.scale    = self.MIN_SCALE
        self.offset_x = 0.0   # px no espaço escalado (scroll horizontal)
        self.offset_y = 0.0   # px no espaço escalado (scroll vertical)
        self._drag_active = False
        self._drag_start  = (0, 0)
        self._drag_offset = (0.0, 0.0)
        self._free_view   = False          # True após arrasto: não recentra até o player mover
        self._last_player_tile = (-1, -1)  # último tile do player para detectar movimento
        self.pending_destination: "tuple | None" = None   # (tile_x, tile_y) a entregar ao game
        self._dest_marker: "tuple | None" = None          # marcador visual no mapa

    # ── Inicialização ────────────────────────────────────────────────────────

    def load_map(self, tile_matrix: list[str], map_key: str = "") -> None:
        """
        Constrói (ou reconstrói) a Surface 1px/tile para o mapa indicado.
        map_key: identificador único (normalmente o caminho do arquivo).
        """
        rows = len(tile_matrix)
        cols = max((len(row) for row in tile_matrix), default=0)
        if cols == 0 or rows == 0:
            return
        surf = pygame.Surface((cols, rows))
        for r, row in enumerate(tile_matrix):
            for c in range(cols):
                char = row[c] if c < len(row) else "_"
                tile = TILE_MAPPING.get(char) or OBJECT_MAPPING.get(char, FLOOR_TILE)
                surf.set_at((c, r), tile.color[:3])
        key = map_key or f"_map_{len(self._surfaces)}"
        self._surfaces[key] = surf
        self._dims[key] = (cols, rows)
        # (Re)inicia o fog deste mapa: tudo não-explorado (preto opaco)
        fog = pygame.Surface((cols, rows))
        fog.fill((0, 0, 0))
        fog.set_colorkey(self._FOG_KEY)
        self._fog_surfs[key]   = fog
        self._fog_cleared[key] = set()
        self._view_key = ()  # invalida cache da view
        # Ativa automaticamente o primeiro mapa carregado
        if not self._active_key:
            self._active_key = key
            self._cols, self._rows = cols, rows

    def set_active_map(self, map_key: str) -> None:
        """Troca qual mapa é exibido no overlay (chamado ao entrar/sair de caverna)."""
        if map_key in self._surfaces:
            self._active_key = map_key
            self._cols, self._rows = self._dims[map_key]

    @property
    def _base_surf(self) -> "pygame.Surface | None":
        return self._surfaces.get(self._active_key)

    # ── Controle de abertura ─────────────────────────────────────────────────

    def toggle(self, player_tile_x: int = -1, player_tile_y: int = -1) -> None:
        self.is_open = not self.is_open
        if self.is_open and player_tile_x >= 0:
            self._free_view = False
            self._last_player_tile = (player_tile_x, player_tile_y)
            self._center_on_player(player_tile_x, player_tile_y)
        else:
            self._free_view = False

    def _center_on_player(self, tx: int, ty: int) -> None:
        """Centraliza o viewport no tile do jogador com o zoom atual."""
        modal = self._modal_rect()
        self.offset_x = tx * self.scale - modal.w / 2
        self.offset_y = ty * self.scale - modal.h / 2
        self._clamp_offset(modal)

    # ── Geometria ────────────────────────────────────────────────────────────

    def _modal_rect(self) -> pygame.Rect:
        sw, sh = self.screen.get_size()
        mw = int(sw * self.W_RATIO)
        mh = int(sh * self.H_RATIO)
        return pygame.Rect((sw - mw) // 2, (sh - mh) // 2, mw, mh)

    def _close_btn_rect(self, modal: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(modal.right - self._u(36), modal.y + self._u(4),
                            self._u(32), self._u(32))

    def _clamp_offset(self, modal: pygame.Rect) -> None:
        map_w = self._cols * self.scale
        map_h = self._rows * self.scale
        # Mapas menores que o modal ficam centralizados (offset negativo é permitido)
        if map_w <= modal.w:
            self.offset_x = (map_w - modal.w) / 2
        else:
            self.offset_x = max(0.0, min(self.offset_x, map_w - modal.w))
        if map_h <= modal.h:
            self.offset_y = (map_h - modal.h) / 2
        else:
            self.offset_y = max(0.0, min(self.offset_y, map_h - modal.h))

    # ── Eventos ──────────────────────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Processa evento; retorna True se o evento foi consumido."""
        if not self.is_open:
            return False

        modal = self._modal_rect()
        mx, my = pygame.mouse.get_pos()

        if event.type == pygame.MOUSEWHEEL:
            if modal.collidepoint(mx, my):
                factor = 1.15 if event.y > 0 else (1.0 / 1.15)
                old_scale = self.scale
                new_scale = max(self.MIN_SCALE,
                                min(self.MAX_SCALE, old_scale * factor))
                # Zoom em direção ao cursor
                rel_x = (mx - modal.x) + self.offset_x
                rel_y = (my - modal.y) + self.offset_y
                ratio = new_scale / old_scale
                self.scale    = new_scale
                self.offset_x = rel_x * ratio - (mx - modal.x)
                self.offset_y = rel_y * ratio - (my - modal.y)
                self._clamp_offset(modal)
                return True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Clique direito dentro do modal → define destino de caminhada
            if modal.collidepoint(mx, my):
                rel_x  = mx - modal.x
                rel_y  = my - modal.y
                tile_x = int((rel_x + self.offset_x) / self.scale)
                tile_y = int((rel_y + self.offset_y) / self.scale)
                tile_x = max(0, min(tile_x, self._cols - 1))
                tile_y = max(0, min(tile_y, self._rows - 1))
                self.pending_destination = (tile_x, tile_y)
                self._dest_marker        = (tile_x, tile_y)
                self.is_open = False
                return True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._close_btn_rect(modal).collidepoint(event.pos):
                self.is_open = False
                return True
            if modal.collidepoint(event.pos):
                self._drag_active = True
                self._drag_start  = event.pos
                self._drag_offset = (self.offset_x, self.offset_y)
                return True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_active:
                self._drag_active = False
                self._free_view   = True   # mantém posição arrastada até o player mover
                return True

        elif event.type == pygame.MOUSEMOTION:
            if self._drag_active:
                dx = self._drag_start[0] - event.pos[0]
                dy = self._drag_start[1] - event.pos[1]
                self.offset_x = self._drag_offset[0] + dx
                self.offset_y = self._drag_offset[1] + dy
                self._clamp_offset(modal)
                return True

        return False

    # ── Renderização ─────────────────────────────────────────────────────────

    def render(self, player_tile_x: int = -1, player_tile_y: int = -1,
               explored: "set | None" = None) -> None:
        if not self.is_open or self._base_surf is None:
            return

        sw, sh = self.screen.get_size()
        modal  = self._modal_rect()
        mx, my = pygame.mouse.get_pos()
        scale  = self.scale

        # Recentra no player automaticamente, a menos que:
        #   - esteja arrastando (_drag_active), ou
        #   - já arrastou e o player ainda não se moveu (_free_view)
        if player_tile_x >= 0 and player_tile_y >= 0:
            cur_tile = (player_tile_x, player_tile_y)
            if self._free_view and cur_tile != self._last_player_tile:
                # Player se moveu → sai do modo livre e recentra
                self._free_view = False
            self._last_player_tile = cur_tile

            if not self._drag_active and not self._free_view:
                off_x = player_tile_x * scale - modal.w / 2
                off_y = player_tile_y * scale - modal.h / 2
                map_w = self._cols * scale
                map_h = self._rows * scale
                if map_w <= modal.w:
                    off_x = (map_w - modal.w) / 2
                else:
                    off_x = max(0.0, min(off_x, map_w - modal.w))
                if map_h <= modal.h:
                    off_y = (map_h - modal.h) / 2
                else:
                    off_y = max(0.0, min(off_y, map_h - modal.h))
                self.offset_x = off_x
                self.offset_y = off_y
        off_x = self.offset_x
        off_y = self.offset_y

        # ── Fundo escurecido ─────────────────────────────
        self.screen.blit(fill_surf((sw, sh), (0, 0, 0, 160)), (0, 0))

        # ── Conteúdo do mapa ─────────────────────────────
        # Calcula quais tiles são visíveis (em coords 1px/tile)
        src_x0 = off_x / scale
        src_y0 = off_y / scale
        src_x1 = (off_x + modal.w) / scale
        src_y1 = (off_y + modal.h) / scale

        ix0 = max(0, int(src_x0))
        iy0 = max(0, int(src_y0))
        ix1 = min(self._cols, math.ceil(src_x1))
        iy1 = min(self._rows, math.ceil(src_y1))

        # ── Fog incremental: revela no fog 1px/tile só os tiles NOVOS ────
        # (antes: loop Python de (ix1-ix0)×(iy1-iy0) tiles POR FRAME — ~113k
        # iterações + draw.rect no zoom mínimo, a causa da queda de FPS)
        fog = self._fog_surfs.get(self._active_key)
        fog_len = -1
        if explored is not None and fog is not None:
            cleared = self._fog_cleared[self._active_key]
            if len(explored) != len(cleared):
                _set_at = fog.set_at
                _key    = self._FOG_KEY
                _cols, _rows = self._cols, self._rows
                for t in explored:
                    if t not in cleared:
                        if 0 <= t[0] < _cols and 0 <= t[1] < _rows:
                            _set_at(t, _key)
                        cleared.add(t)
            fog_len = len(cleared)

        # ── View composta (mapa + fog escalados) com cache ───────────────
        # Rebuild só quando viewport/zoom/fog/tamanho mudam; parado = 1 blit.
        view_key = (self._active_key, ix0, iy0, ix1, iy1, scale,
                    int(off_x), int(off_y), modal.w, modal.h, fog_len)
        if view_key != self._view_key or self._view_canvas is None:
            self._view_key = view_key
            if (self._view_canvas is None
                    or self._view_canvas.get_size() != (modal.w, modal.h)):
                self._view_canvas = pygame.Surface((modal.w, modal.h))
            canvas = self._view_canvas
            canvas.fill(self.BG_COL)
            if ix0 < ix1 and iy0 < iy1:
                src_rect = pygame.Rect(ix0, iy0, ix1 - ix0, iy1 - iy0)
                dst_w  = max(1, int((ix1 - ix0) * scale))
                dst_h  = max(1, int((iy1 - iy0) * scale))
                blit_x = int(ix0 * scale - off_x)
                blit_y = int(iy0 * scale - off_y)
                scaled = pygame.transform.scale(
                    self._base_surf.subsurface(src_rect), (dst_w, dst_h))
                canvas.blit(scaled, (blit_x, blit_y))
                if explored is not None and fog is not None:
                    # transform.scale é nearest-neighbor: preserva a cor-chave
                    fog_scaled = pygame.transform.scale(
                        fog.subsurface(src_rect), (dst_w, dst_h))
                    canvas.blit(fog_scaled, (blit_x, blit_y))

        self.screen.blit(self._view_canvas, modal.topleft)

        # ── Marcador de destino ──────────────────────────
        if self._dest_marker:
            dx = int(self._dest_marker[0] * scale - off_x) + modal.x
            dy = int(self._dest_marker[1] * scale - off_y) + modal.y
            if modal.collidepoint(dx, dy):
                r = max(4, int(scale * 0.5))
                arm = max(3, r)
                pygame.draw.line(self.screen, (255, 220,   0),
                                 (dx - arm, dy - arm), (dx + arm, dy + arm), 2)
                pygame.draw.line(self.screen, (255, 220,   0),
                                 (dx + arm, dy - arm), (dx - arm, dy + arm), 2)

        # ── Marcador do jogador ──────────────────────────
        if 0 <= player_tile_x < self._cols and 0 <= player_tile_y < self._rows:
            px = int(player_tile_x * scale - off_x) + modal.x
            py = int(player_tile_y * scale - off_y) + modal.y
            if modal.collidepoint(px, py):
                r = max(4, int(scale * 0.5))
                pygame.draw.circle(self.screen, (220,  40,  40), (px, py), r)
                pygame.draw.circle(self.screen, (255, 200, 200), (px, py), max(2, r // 2))

        # ── Borda ────────────────────────────────────────
        pygame.draw.rect(self.screen, self.BORDER_COL, modal, self._u(2), border_radius=4)

        # ── Botão X ──────────────────────────────────────
        close_r   = self._close_btn_rect(modal)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen,
                         (180, 60, 60) if close_hov else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self._font.render("X", False, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))

        # ── Legenda ──────────────────────────────────────
        hint = self._font.render(
            "MAPA   [M] fechar  |  scroll: zoom  |  arrastar: mover", False, (180, 150, 100))
        zoom_txt = self._font.render(f"Zoom: {scale:.1f}x", False, (140, 120, 80))
        bar_y = modal.y - hint.get_height() - self._u(4)
        self.screen.blit(hint,     (modal.x + self._u(6),                            bar_y))
        self.screen.blit(zoom_txt, (modal.right - zoom_txt.get_width() - self._u(6), bar_y))
