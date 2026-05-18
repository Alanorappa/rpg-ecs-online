"""
minimap.py — Minimapa estilo radar: janela centrada no player.

Exibe sempre RADIUS tiles ao redor do player. O player fica fixo no centro
do frame quadrado (SIZE × SIZE). O mapa "rola" conforme o player se move.

Performance:
  - _map_arr: array numpy (cols × rows × 3) pré-computado no on_map_load().
  - _rebuild: slice C-level do array + fancy indexing numpy para fog.
    Sem loop Python sobre tiles.
  - Cache: rebuild só ocorre quando o player muda de tile ou explored cresce.
"""
from __future__ import annotations
import pygame

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False


class Minimap:
    SIZE         = 220   # frame quadrado em px
    RADIUS       =  25   # raio em tiles ao redor do player
    MARGIN_RIGHT =  10
    MARGIN_TOP   =  68   # abaixo dos textos zona/coords (~y=10..60)
    BORDER_COL   = (100,  80,  50)
    PLAYER_COL   = (255, 255, 255)
    ENEMY_COL    = (220,  50,  50)

    def __init__(self, screen: pygame.Surface, map_overlay) -> None:
        self.screen       = screen
        self._map_overlay = map_overlay

        self._win:          int = 2 * self.RADIUS + 1   # 51 tiles de lado
        self._tile_px:      int = max(1, self.SIZE // self._win)   # 2 px/tile
        self._content_size: int = self._win * self._tile_px        # 102 px
        self._map_ox:       int = (self.SIZE - self._content_size) // 2  # 9 px
        self._map_oy:       int = (self.SIZE - self._content_size) // 2  # 9 px

        self._map_arr: "np.ndarray | None" = None   # (cols, rows, 3) uint8

        self._cached_surf: "pygame.Surface | None" = None
        self._cache_key:   "tuple | None"           = None

        self._load_map_arr()

    # ── Inicialização ────────────────────────────────────────────────────────

    def _load_map_arr(self) -> None:
        """Constrói (ou reconstrói) o array numpy do mapa base."""
        base = self._map_overlay._base_surf
        if base is None:
            self._map_arr = None
            return
        if _NUMPY_OK:
            self._map_arr = pygame.surfarray.array3d(base).copy()
        # fallback sem numpy: usa base_surf direto via get_at no _rebuild_python

    def on_map_load(self) -> None:
        """Chama após carregar um mapa — reconstrói array e invalida cache."""
        self._load_map_arr()
        self._cached_surf = None
        self._cache_key   = None

    # ── Renderização principal ───────────────────────────────────────────────

    def render(
        self,
        player_tx:   int,
        player_ty:   int,
        explored:    set,
        visible:     set,
        enemy_tiles: "list[tuple[int,int]]",
    ) -> None:
        if self._map_overlay._cols == 0:
            return
        # Lazy init caso on_map_load tenha sido chamado antes do pygame estar pronto
        if _NUMPY_OK and self._map_arr is None:
            self._load_map_arr()
        if not _NUMPY_OK and self._map_overlay._base_surf is None:
            return

        cache_key = (player_tx, player_ty, len(explored))
        if self._cached_surf is None or cache_key != self._cache_key:
            self._rebuild(player_tx, player_ty, explored, visible)
            self._cache_key = cache_key

        sw, _ = self.screen.get_size()
        fx = sw - self.SIZE - self.MARGIN_RIGHT
        fy = self.MARGIN_TOP

        self.screen.blit(self._cached_surf, (fx, fy))

        tp  = self._tile_px
        ox  = fx + self._map_ox
        oy  = fy + self._map_oy
        mid = self.RADIUS * tp + tp // 2   # offset do centro no conteúdo

        # Player: sempre no centro exato do frame
        pygame.draw.circle(self.screen, self.PLAYER_COL,
                           (ox + mid, oy + mid), 2)

        # Inimigos visíveis: posição relativa ao player
        for etx, ety in enemy_tiles:
            dx = etx - player_tx
            dy = ety - player_ty
            if abs(dx) <= self.RADIUS and abs(dy) <= self.RADIUS:
                sx = ox + mid + dx * tp
                sy = oy + mid + dy * tp
                pygame.draw.circle(self.screen, self.ENEMY_COL, (sx, sy), 2)

        pygame.draw.rect(self.screen, self.BORDER_COL,
                         (fx - 1, fy - 1, self.SIZE + 2, self.SIZE + 2), 1)

    def get_rect(self) -> "pygame.Rect":
        """Retorna o rect de tela do minimap (mesmo cálculo usado em render)."""
        sw, _ = self.screen.get_size()
        fx = sw - self.SIZE - self.MARGIN_RIGHT
        fy = self.MARGIN_TOP
        return pygame.Rect(fx, fy, self.SIZE, self.SIZE)

    def screen_to_tile(self, mx: int, my: int,
                       player_tx: int, player_ty: int) -> "tuple[int,int] | None":
        """Converte coordenadas de tela (mx, my) em tile do mundo.

        Retorna (tile_x, tile_y) se o clique estiver dentro do minimap, ou None.
        """
        rect = self.get_rect()
        if not rect.collidepoint(mx, my):
            return None
        tp  = self._tile_px
        ox  = rect.x + self._map_ox
        oy  = rect.y + self._map_oy
        # Posição relativa ao conteúdo do minimap
        rx = mx - ox
        ry = my - oy
        content_size = self._content_size
        if not (0 <= rx < content_size and 0 <= ry < content_size):
            return None
        # Tile relativo ao centro (player)
        dtx = rx // tp - self.RADIUS
        dty = ry // tp - self.RADIUS
        return (player_tx + dtx, player_ty + dty)

    # ── Rebuild do cache ─────────────────────────────────────────────────────

    def _rebuild(
        self,
        player_tx: int, player_ty: int,
        explored: set, visible: set,
    ) -> None:
        if _NUMPY_OK and self._map_arr is not None:
            self._cached_surf = self._rebuild_numpy(
                player_tx, player_ty, explored, visible)
        else:
            self._cached_surf = self._rebuild_python(
                player_tx, player_ty, explored, visible)

    def _rebuild_numpy(
        self,
        player_tx: int, player_ty: int,
        explored: set, visible: set,
    ) -> pygame.Surface:
        cols   = self._map_overlay._cols
        rows   = self._map_overlay._rows
        win    = self._win
        tp     = self._tile_px
        RADIUS = self.RADIUS

        x0 = player_tx - RADIUS
        y0 = player_ty - RADIUS

        # ── Extrai janela do mapa ────────────────────────────────────────────
        # Porção do mapa que cai dentro dos limites
        mx0 = max(0, x0);      my0 = max(0, y0)
        mx1 = min(cols, x0 + win);  my1 = min(rows, y0 + win)

        # Offset dentro da janela onde começa a parte válida
        wx0 = mx0 - x0;  wy0 = my0 - y0
        wx1 = wx0 + (mx1 - mx0)
        wy1 = wy0 + (my1 - my0)

        # Array da janela (tiles): preto por padrão (fora do mapa = preto)
        win_arr = np.zeros((win, win, 3), dtype=np.uint8)
        if mx0 < mx1 and my0 < my1:
            win_arr[wx0:wx1, wy0:wy1] = self._map_arr[mx0:mx1, my0:my1]

        # ── Máscara de fog na janela ─────────────────────────────────────────
        # 2=unexplored(preto), 1=explored-not-visible(escuro), 0=visible
        fog = np.full((win, win), 2, dtype=np.uint8)

        if explored:
            exp = np.array(list(explored), dtype=np.int32)
            wx  = exp[:, 0] - x0
            wy  = exp[:, 1] - y0
            ok  = (wx >= 0) & (wx < win) & (wy >= 0) & (wy < win)
            wx, wy = wx[ok], wy[ok]
            if wx.size:
                fog[wx, wy] = 1

        if visible:
            vis = np.array(list(visible), dtype=np.int32)
            wx  = vis[:, 0] - x0
            wy  = vis[:, 1] - y0
            ok  = (wx >= 0) & (wx < win) & (wy >= 0) & (wy < win)
            wx, wy = wx[ok], wy[ok]
            if wx.size:
                fog[wx, wy] = 0

        # ── Aplica fog (C-level) ─────────────────────────────────────────────
        win_arr[fog == 2] = 0
        dark = fog == 1
        win_arr[dark] = (win_arr[dark].astype(np.uint16) * 45 // 100).astype(np.uint8)

        # ── Escala tiles para pixels e monta frame ───────────────────────────
        if tp > 1:
            content_arr = np.repeat(np.repeat(win_arr, tp, axis=0), tp, axis=1)
        else:
            content_arr = win_arr

        content = pygame.surfarray.make_surface(content_arr)

        frame = pygame.Surface((self.SIZE, self.SIZE))
        frame.fill((0, 0, 0))
        frame.blit(content, (self._map_ox, self._map_oy))
        return frame

    def _rebuild_python(
        self,
        player_tx: int, player_ty: int,
        explored: set, visible: set,
    ) -> pygame.Surface:
        """Fallback sem numpy — loop Python por tile da janela."""
        cols   = self._map_overlay._cols
        rows   = self._map_overlay._rows
        win    = self._win
        tp     = self._tile_px
        RADIUS = self.RADIUS
        base   = self._map_overlay._base_surf

        x0 = player_tx - RADIUS
        y0 = player_ty - RADIUS

        content = pygame.Surface((win * tp, win * tp))
        content.fill((0, 0, 0))

        for wy in range(win):
            for wx in range(win):
                tx = x0 + wx
                ty = y0 + wy
                if not (0 <= tx < cols and 0 <= ty < rows):
                    continue
                r, g, b, *_ = base.get_at((tx, ty))
                if (tx, ty) not in explored:
                    r = g = b = 0
                elif (tx, ty) not in visible:
                    r = r * 45 // 100
                    g = g * 45 // 100
                    b = b * 45 // 100
                pygame.draw.rect(content, (r, g, b), (wx * tp, wy * tp, tp, tp))

        frame = pygame.Surface((self.SIZE, self.SIZE))
        frame.fill((0, 0, 0))
        frame.blit(content, (self._map_ox, self._map_oy))
        return frame
