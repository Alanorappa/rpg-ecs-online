"""
tile_sprite_manager.py
======================
Gerencia lazy-loading de sprites de tiles.

Uso:
    from tile_sprite_manager import TILE_SPRITES
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
from tileset import TILE_SIZE, TileType

TILES_DIR = os.path.join("assets", "tiles")


class TileSpriteManager:
    """Lazy-carrega e armazena em cache sprites de tiles com suporte a alpha."""

    def __init__(self) -> None:
        self._raw:    dict[str, pygame.Surface | None] = {}    # imagem bruta por filename
        self._scaled: dict[tuple, pygame.Surface | None] = {}  # escalada por (filename, w, h)

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

        try:
            self._raw[filename] = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f"[TileSprites] Erro ao carregar '{path}': {e}")
            self._raw[filename] = None

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
        from tileset import SHEET_TILE_MAP, OBJECT_SHEET_TILE_MAP
        info = SHEET_TILE_MAP.get(name) or OBJECT_SHEET_TILE_MAP.get(name)
        if info is not None:
            sheet_file, sx, sy, tw, th = info
            return self._get_sheet_region(sheet_file, sx, sy, tw, th)
        return self._load_raw(name)

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

    def invalidate(self) -> None:
        """Limpa o cache (use ao trocar de resolução ou recarregar assets)."""
        self._raw.clear()
        self._scaled.clear()


# Singleton global
TILE_SPRITES = TileSpriteManager()
