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
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI
from ui.icon_manager import ICONS

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False


class Minimap(UIScaleMixin):
    SIZE         = UI.MINIMAP_SIZE          # frame quadrado em px (base, escala 1.0)
    RADIUS       = UI.MINIMAP_RADIUS_TILES  # raio em tiles ao redor do player — não escala
    MARGIN_RIGHT = UI.MINIMAP_MARGIN_RIGHT
    MARGIN_TOP   = UI.MINIMAP_MARGIN_TOP    # abaixo dos textos zona/coords (~y=10..60)
    BORDER_COL   = (100,  80,  50)
    PLAYER_COL   = (255, 255, 255)
    ENEMY_COL    = (220,  50,  50)

    _FONT_BASES = {"_font": 12}   # glifo de fallback dos marcadores (!/?/T/$)

    def __init__(self, screen: pygame.Surface, map_overlay) -> None:
        self.screen       = screen
        self._map_overlay = map_overlay

        self._win: int = 2 * self.RADIUS + 1   # 51 tiles de lado — não escala

        self._map_arr: "np.ndarray | None" = None   # (cols, rows, 3) uint8
        self._cached_surf: "pygame.Surface | None" = None
        self._cache_key:   "tuple | None"           = None

        # set_ui_scale(1.0) é chamado por super().__init__() (UIScaleMixin) e
        # calcula _tile_px/_content_size/_map_ox/_map_oy abaixo, a partir do
        # SIZE já escalado — precisa rodar depois de self._win estar setado.
        super().__init__()

        self._load_map_arr()

    def set_ui_scale(self, scale: float) -> None:
        """Recalcula a geometria derivada (px/tile, offset de centralização)
        e invalida o cache do minimapa quando a escala muda — antes desta
        correção, o minimapa nunca reagia à "Escala da UI" do menu de
        pausa (ver arquitetura/PROBLEMAS_ARQUITETURA.md item IU4)."""
        changed = scale != self._ui_scale_applied
        super().set_ui_scale(scale)
        if not changed:
            return
        size = self._u(self.SIZE)
        self._tile_px:      int = max(1, size // self._win)
        self._content_size: int = self._win * self._tile_px
        self._map_ox:       int = (size - self._content_size) // 2
        self._map_oy:       int = (size - self._content_size) // 2
        self._cached_surf = None
        self._cache_key   = None

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
        markers:     "list | None" = None,
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
        fx = sw - self._u(self.SIZE) - self._u(self.MARGIN_RIGHT)
        fy = self._u(self.MARGIN_TOP)

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

        # Marcadores (morte, quest givers, treinadores, mercadores...) —
        # ícone se existir; senão círculo colorido + glifo pequeno. Corpo é
        # o único que não expira com o fog (é a posição já conhecida do
        # próprio player) — os demais já vêm pré-filtrados por Visible em
        # ui/map_markers.py::collect_markers. Tamanho FIXO (só escala com
        # "Escala da UI", não com tp) e posições desconflitadas antes de
        # desenhar — mesmo motivo do mapa grande (ver MapOverlay.render).
        if markers:
            # Tamanho fixo de tela, independente de tp/zoom — ver
            # ui/map_markers.py::MAP_ICON_SIZE pro histórico (8px nativo
            # ficou ilegível em jogo, subiu pra 16 = 2x nearest-neighbor).
            from ui.map_markers import deconflict_positions, MAP_ICON_SIZE
            icon_size = MAP_ICON_SIZE
            _in_range = [mk for mk in markers
                        if abs(mk.tile_x - player_tx) <= self.RADIUS
                        and abs(mk.tile_y - player_ty) <= self.RADIUS]
            _raw_pts = [(ox + mid + (mk.tile_x - player_tx) * tp,
                        oy + mid + (mk.tile_y - player_ty) * tp) for mk in _in_range]
            _placed_pts = deconflict_positions(_raw_pts, icon_size * 0.9)
            for mk, (sx, sy) in zip(_in_range, _placed_pts):
                icon = ICONS.get(mk.icon_name, icon_size)
                if icon is not None:
                    self.screen.blit(icon, (sx - icon_size // 2, sy - icon_size // 2))
                else:
                    r = icon_size // 2
                    pygame.draw.circle(self.screen, mk.fallback_color, (sx, sy), r)
                    if mk.fallback_symbol:
                        glyph = self._font.render(mk.fallback_symbol, False, (20, 20, 20))
                        self.screen.blit(glyph, (sx - glyph.get_width() // 2,
                                             sy - glyph.get_height() // 2))

        sz = self._u(self.SIZE)
        pygame.draw.rect(self.screen, self.BORDER_COL,
                         (fx - 1, fy - 1, sz + 2, sz + 2), 1)

    def render_fullmap(
        self,
        explored:    set,
        visible:     set,
        dots:        "list[tuple[int, int, tuple, int]]",
    ) -> None:
        """Modo alternativo pra dentro de instância estilo MOBA (13/08/2026,
        pedido do usuário) — mostra o MAPA INTEIRO encolhido pra caber no
        frame (sem seguir o player, sem centralizar em ninguém), em vez da
        janela de RADIUS tiles ao redor do player do `render()` normal.
        Névoa de guerra continua valendo (`explored`/`visible` — inclusive
        visão compartilhada de time, já embutida nesses 2 sets pelo
        servidor, ver `_ally_vision_centers`), só a MOLDURA que muda.

        `dots`: lista pré-resolvida pelo CHAMADOR (game.py) de
        `(tile_x, tile_y, color, radius)` — minion/torre/player já
        filtrados por visibilidade e coloridos por time. Minimap fica
        genérico (só desenha), igual já era pra `markers`/`enemy_tiles`
        no `render()` normal — não sabe o que é Faction/Minion/Tower."""
        cols = self._map_overlay._cols
        rows = self._map_overlay._rows
        if cols == 0 or rows == 0:
            return
        if _NUMPY_OK and self._map_arr is None:
            self._load_map_arr()
        if not _NUMPY_OK and self._map_overlay._base_surf is None:
            return

        cache_key = ("fullmap", len(explored), len(visible))
        if self._cached_surf is None or cache_key != self._cache_key:
            self._cached_surf = self._rebuild_fullmap(cols, rows, explored, visible)
            self._cache_key = cache_key

        sw, _ = self.screen.get_size()
        sz = self._u(self.SIZE)
        fx = sw - sz - self._u(self.MARGIN_RIGHT)
        fy = self._u(self.MARGIN_TOP)
        self.screen.blit(self._cached_surf, (fx, fy))

        # Mesma geometria de encolhimento usada no rebuild — precisa bater
        # exatamente pra os pontos caírem em cima do terreno certo.
        scale = min(sz / cols, sz / rows)
        content_w = max(1, int(cols * scale))
        content_h = max(1, int(rows * scale))
        ox = fx + (sz - content_w) // 2
        oy = fy + (sz - content_h) // 2

        for tile_x, tile_y, color, radius in dots:
            sx = ox + int(tile_x * scale)
            sy = oy + int(tile_y * scale)
            pygame.draw.circle(self.screen, color, (sx, sy), radius)

        pygame.draw.rect(self.screen, self.BORDER_COL,
                         (fx - 1, fy - 1, sz + 2, sz + 2), 1)

    def _rebuild_fullmap(self, cols: int, rows: int,
                         explored: set, visible: set) -> pygame.Surface:
        """Aplica névoa no mapa INTEIRO (sem recorte de janela — diferente
        de `_rebuild_numpy`, que faz isso só na janela de RADIUS tiles) e
        encolhe pro tamanho do frame via `pygame.transform.smoothscale`
        (C-level, evita downsample manual tile-a-tile em Python)."""
        sz = self._u(self.SIZE)
        if _NUMPY_OK and self._map_arr is not None:
            arr = self._map_arr.copy()
            fog = np.full((cols, rows), 2, dtype=np.uint8)
            if explored:
                exp = np.array(list(explored), dtype=np.int32)
                m = (exp[:, 0] >= 0) & (exp[:, 0] < cols) & (exp[:, 1] >= 0) & (exp[:, 1] < rows)
                exp = exp[m]
                if len(exp):
                    fog[exp[:, 0], exp[:, 1]] = 1
            if visible:
                vis = np.array(list(visible), dtype=np.int32)
                m = (vis[:, 0] >= 0) & (vis[:, 0] < cols) & (vis[:, 1] >= 0) & (vis[:, 1] < rows)
                vis = vis[m]
                if len(vis):
                    fog[vis[:, 0], vis[:, 1]] = 0
            arr[fog == 2] = 0
            dark = fog == 1
            arr[dark] = (arr[dark].astype(np.uint16) * 45 // 100).astype(np.uint8)
            content = pygame.surfarray.make_surface(arr)
        else:
            content = self._map_overlay._base_surf.copy()
            for tx in range(cols):
                for ty in range(rows):
                    if (tx, ty) not in explored:
                        content.set_at((tx, ty), (0, 0, 0))
                    elif (tx, ty) not in visible:
                        r, g, b, *_ = content.get_at((tx, ty))
                        content.set_at((tx, ty), (r * 45 // 100, g * 45 // 100, b * 45 // 100))

        scale = min(sz / cols, sz / rows)
        content_w = max(1, int(cols * scale))
        content_h = max(1, int(rows * scale))
        scaled = pygame.transform.smoothscale(content, (content_w, content_h))

        frame = pygame.Surface((sz, sz))
        frame.fill((0, 0, 0))
        frame.blit(scaled, ((sz - content_w) // 2, (sz - content_h) // 2))
        return frame

    def get_rect(self) -> "pygame.Rect":
        """Retorna o rect de tela do minimap (mesmo cálculo usado em render)."""
        sw, _ = self.screen.get_size()
        sz = self._u(self.SIZE)
        fx = sw - sz - self._u(self.MARGIN_RIGHT)
        fy = self._u(self.MARGIN_TOP)
        return pygame.Rect(fx, fy, sz, sz)

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

    def screen_to_tile_fullmap(self, mx: int, my: int) -> "tuple[int, int] | None":
        """Equivalente de `screen_to_tile` pro modo tela-cheia da BG
        (`render_fullmap`) — geometria BEM diferente (sem RADIUS, sem
        centralizar no player, escala derivada de cols/rows do mapa
        inteiro) — bug real relatado pelo usuário (13/08/2026): clicar
        no minimapa da BG parou de mover o personagem porque o clique
        continuava passando pelo `screen_to_tile` normal, que faz a
        conta errada pra este modo. Chamador (`game.py`) decide qual
        dos dois chamar, mesmo critério (`InstanceInventoryUIState.
        active`) que já decide entre `render`/`render_fullmap`."""
        rect = self.get_rect()
        if not rect.collidepoint(mx, my):
            return None
        cols = self._map_overlay._cols
        rows = self._map_overlay._rows
        if cols == 0 or rows == 0:
            return None
        sz = rect.width
        scale = min(sz / cols, sz / rows)
        content_w = max(1, int(cols * scale))
        content_h = max(1, int(rows * scale))
        ox = rect.x + (sz - content_w) // 2
        oy = rect.y + (sz - content_h) // 2
        rx = mx - ox
        ry = my - oy
        if not (0 <= rx < content_w and 0 <= ry < content_h):
            return None
        return (int(rx / scale), int(ry / scale))

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

        # Interseção com o conjunto de tiles da JANELA (bounded, win*win ~2601)
        # ANTES de converter pra numpy — `explored` só CRESCE ao longo da
        # sessão (nunca encolhe, ver engine/components.py::FogOfWar), então
        # `np.array(list(explored))` reprocessava o set INTEIRO toda vez que
        # o player mudava de tile (= toda vez que o cache do minimap invalida,
        # linha ~108) — custo crescendo sem limite conforme mais mapa é
        # explorado (medido: ~10-13ms por rebuild numa sessão já explorada,
        # rótulo enganoso "hud:combat_log" no profiler — na verdade cronometra
        # o render do minimapa). `set & set` em CPython sempre itera o MENOR
        # dos dois operandos — com window_tiles bounded, o custo fica
        # O(win²) CONSTANTE, nunca O(len(explored)). Bug relatado pelo
        # usuário 14/07/2026 (spike de frame ao andar com o arqueiro, achado
        # via log de profiler — ver ARQUITETURA_ONLINE.md).
        window_tiles = {(x0 + wx, y0 + wy) for wx in range(win) for wy in range(win)}

        exp_in_window = explored & window_tiles
        if exp_in_window:
            exp = np.array(list(exp_in_window), dtype=np.int32)
            fog[exp[:, 0] - x0, exp[:, 1] - y0] = 1

        vis_in_window = visible & window_tiles
        if vis_in_window:
            vis = np.array(list(vis_in_window), dtype=np.int32)
            fog[vis[:, 0] - x0, vis[:, 1] - y0] = 0

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

        frame = pygame.Surface((self._u(self.SIZE), self._u(self.SIZE)))
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

        frame = pygame.Surface((self._u(self.SIZE), self._u(self.SIZE)))
        frame.fill((0, 0, 0))
        frame.blit(content, (self._map_ox, self._map_oy))
        return frame
