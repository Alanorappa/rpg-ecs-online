"""
tile_sprite_manager.py
======================
Gerencia lazy-loading de sprites de tiles.

Uso:
    from ui.tile_sprite_manager import TILE_SPRITES
    surf = TILE_SPRITES.get(tile_type, map_x, map_y)
    # retorna Surface no tamanho correto, ou None se sem sprite.

Configuração:
    Edite tileset.py — campo `sprites` de cada TileType:
        GRASS_TILE = TileType("Grass", (58, 100, 48), sprites=["grass_1", "grass_2"])
        TREE_TILE  = TileType("Tree",  (28, 72, 28),  sprites=["tree_1"], overlay_height=32)

    Coloque os arquivos PNG em assets/tiles/:
        assets/tiles/grass_1.png   (32×32)
        assets/tiles/tree_1.png    (32×64)  ← tamanho natural; será respeitado
"""
from __future__ import annotations
import os
import pygame
from engine.tileset import TILE_SIZE, TileType

TILES_DIR = os.path.join("assets", "tiles")


class TileSpriteManager:
    """Lazy-carrega e armazena em cache sprites de tiles com suporte a alpha."""

    def __init__(self) -> None:
        self._raw:    dict[str, pygame.Surface | None] = {}    # imagem bruta por filename
        self._scaled: dict[tuple, pygame.Surface | None] = {}  # escalada por (filename, w, h)
        # Linha (relativa ao topo do recorte) do 1º pixel opaco de cima
        # pra baixo, por sprite_id (26/09/2026, pedido do usuário —
        # nameplate de sprite de objeto ancora no pixel VISÍVEL de
        # verdade, não no topo do recorte cru, que pode ter padding
        # transparente/sombra por cima do desenho).
        self._top_opaque_y: dict[str, int] = {}
        # Frames cortados da folha animada de portal, por tile_id
        # (30/09/2026, ver PORTAL_ANIM_SHEET_BY_TILE_ID) — lista vazia
        # cacheada = "já tentei, não tem folha animada pra esse tile_id".
        self._portal_anim_frames: dict[str, list[pygame.Surface]] = {}

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _load_raw(self, filename: str) -> pygame.Surface | None:
        """Carrega a imagem bruta (sem escalar). Mantém canal alpha."""
        if filename in self._raw:
            return self._raw[filename]

        path = os.path.join(TILES_DIR, filename + ".png")
        if not os.path.exists(path):
            self._raw[filename] = None
            return None

        from ui.asset_cache import get_image
        self._raw[filename] = get_image(path)
        if self._raw[filename] is None:
            print(f"[TileSprites] Erro ao carregar '{path}'")

        return self._raw[filename]

    def _get_scaled(self, filename: str, target_w: int, target_h: int) -> pygame.Surface | None:
        """Retorna a imagem escalada para (target_w × target_h). Cache por (filename, w, h)."""
        key = (filename, target_w, target_h)
        if key in self._scaled:
            return self._scaled[key]

        raw = self._load_raw(filename)
        if raw is None:
            self._scaled[key] = None
            return None

        if raw.get_width() == target_w and raw.get_height() == target_h:
            self._scaled[key] = raw
        else:
            self._scaled[key] = pygame.transform.scale(raw, (target_w, target_h))

        return self._scaled[key]

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get(self, tile_type: TileType, tile_x: int, tile_y: int) -> pygame.Surface | None:
        """
        Retorna a Surface para o tile na posição (tile_x, tile_y).

        Tamanho alvo:
          - Tile normal (overlay_height=0):  TILE_SIZE × TILE_SIZE
          - Tile-objeto (overlay_height > 0): TILE_SIZE × (TILE_SIZE + overlay_height)

        Variante escolhida deterministicamente pela posição.
        Retorna None se sem sprites ou arquivo não encontrado.
        """
        sprites = getattr(tile_type, "sprites", None)
        if not sprites:
            return None

        idx = (tile_x * 7 + tile_y * 13) % len(sprites)
        filename = sprites[idx]

        target_w = TILE_SIZE
        target_h = TILE_SIZE + tile_type.overlay_height
        return self._get_scaled(filename, target_w, target_h)

    def get_raw_sprite(self, name: str) -> "pygame.Surface | None":
        """
        Retorna o sprite no tamanho real.
        - Se 'name' for um tile de sheet (SHEET_TILE_MAP ou OBJECT_SHEET_TILE_MAP),
          extrai a sub-região do PNG do sheet.
        - Caso contrário, carrega o arquivo 'name'.png diretamente.
        """
        from engine.tileset import SHEET_TILE_MAP, OBJECT_SHEET_TILE_MAP
        info = SHEET_TILE_MAP.get(name) or OBJECT_SHEET_TILE_MAP.get(name)
        if info is not None:
            sheet_file, sx, sy, tw, th = info
            return self._get_sheet_region(sheet_file, sx, sy, tw, th)
        return self._load_raw(name)

    def get_night_overlay_sprite(self, name: str) -> "pygame.Surface | None":
        """Versão "noturna" de um tile de sheet (22/09/2026, pedido do
        usuário — ex: lanterna acesa do lampião) — MESMO recorte
        (sx,sy,w,h) do tile base, só que extraído da folha COMPANHEIRA
        (`engine.tileset.NIGHT_SHEET_BY_FILE`), se existir uma pra essa
        folha. `None` se a folha base não tiver companheira noturna
        cadastrada, ou se `name` não for um tile de OBJECT_SHEET_TILE_MAP.
        Chamador (`ui/tile_render_system.py::get_world_objects()`) é quem
        decide QUANDO desenhar isso (gate de `TileType.light_source` +
        `is_night()`) — este método só resolve a Surface."""
        from engine.tileset import OBJECT_SHEET_TILE_MAP, NIGHT_SHEET_BY_FILE
        info = OBJECT_SHEET_TILE_MAP.get(name)
        if info is None:
            return None
        sheet_file, sx, sy, tw, th = info
        night_sheet = NIGHT_SHEET_BY_FILE.get(sheet_file)
        if night_sheet is None:
            return None
        return self._get_sheet_region(night_sheet, sx, sy, tw, th)

    def get_portal_anim_frame(self, name: str, now_ms: int,
                              frame_ms: int = 150) -> "pygame.Surface | None":
        """Frame atual da animação de portal pro tile `name` (30/09/2026,
        Sistema de Portais) — None se `name` não for um tile de
        OBJECT_SHEET_TILE_MAP, ou se não houver folha animada cadastrada
        pra ele (`engine.tileset.PORTAL_ANIM_SHEET_BY_TILE_ID`), caso em
        que o chamador (`ui/tile_render_system.py::get_world_objects()`)
        cai pro sprite ESTÁTICO de sempre.

        Frame escolhido por TEMPO REAL (`now_ms // frame_ms % n_frames`)
        — relógio global, sem estado por-instância (mesmo princípio de
        `ui/day_night_system.py::day_night_color()`): todo portal do
        jogo anima em sincronia, não precisa saber QUAL portal é."""
        from engine.tileset import OBJECT_SHEET_TILE_MAP, PORTAL_ANIM_SHEET_BY_TILE_ID
        anim_file = PORTAL_ANIM_SHEET_BY_TILE_ID.get(name)
        if anim_file is None:
            return None
        frames = self._portal_anim_frames.get(name)
        if frames is None:
            frames = self._cut_portal_anim_frames(name, anim_file)
            self._portal_anim_frames[name] = frames
        if not frames:
            return None
        idx = int(now_ms // frame_ms) % len(frames)
        return frames[idx]

    def _cut_portal_anim_frames(self, name: str, anim_file: str) -> list:
        """Corta `anim_file` (tira horizontal) em frames do MESMO w×h do
        tile estático `name` — mesma técnica de corte de
        `engine/character_animation.py::_load_sheet_frames`, só que a
        largura do frame vem do PRÓPRIO tile catalogado, não de uma
        constante fixa."""
        from engine.tileset import OBJECT_SHEET_TILE_MAP
        info = OBJECT_SHEET_TILE_MAP.get(name)
        if info is None:
            return []
        _sheet_file, _sx, _sy, tw, th = info
        sheet = self._load_raw(anim_file)
        if sheet is None:
            return []
        n_frames = sheet.get_width() // tw
        if n_frames <= 0:
            return []
        return [
            sheet.subsurface(pygame.Rect(i * tw, 0, tw, th)).copy()
            for i in range(n_frames)
        ]

    def _get_sheet_region(self, sheet_file: str,
                          sx: int, sy: int, tw: int, th: int) -> "pygame.Surface | None":
        """Extrai e cacheiza uma sub-região de um sheet PNG."""
        key = (sheet_file, sx, sy, tw, th)
        if key in self._scaled:
            return self._scaled[key]
        sheet = self._load_raw(sheet_file)
        if sheet is None:
            self._scaled[key] = None
            return None
        region = pygame.Surface((tw, th), pygame.SRCALPHA)
        region.blit(sheet, (0, 0), (sx, sy, tw, th))
        self._scaled[key] = region
        return region

    def get_top_opaque_y(self, name: str) -> int:
        """Linha (0 = topo do recorte) do 1º pixel opaco de cima pra
        baixo do sprite `name` — ignora padding transparente/sombra
        acima do desenho de verdade (26/09/2026, pedido do usuário).
        Usa o mesmo threshold padrão de `pygame.mask.from_surface`
        (127) que já separa sombra "assada" (alpha ~89 nas folhas
        "with Shadow") de objeto opaco (alpha 255), sem ajuste
        nenhum — confirmado pixel a pixel na implementação do contorno
        de seleção (ver ui/render_system.py). Cacheado por sprite_id —
        computado 1x, nunca por frame (sprite estático de objeto nunca
        muda)."""
        cached = self._top_opaque_y.get(name)
        if cached is not None:
            return cached
        surf = self.get_raw_sprite(name)
        if surf is None:
            self._top_opaque_y[name] = 0
            return 0
        mask = pygame.mask.from_surface(surf)
        rects = mask.get_bounding_rects()
        top = min((r.top for r in rects), default=0)
        self._top_opaque_y[name] = top
        return top

    def invalidate(self) -> None:
        """Limpa o cache (use ao trocar de resolução ou recarregar assets)."""
        self._raw.clear()
        self._scaled.clear()
        self._top_opaque_y.clear()


# Singleton global
TILE_SPRITES = TileSpriteManager()
