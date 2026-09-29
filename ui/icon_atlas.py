# icon_atlas.py
"""
Atlas de ícones (17/09/2026, ver PROBLEMAS_ARQUITETURA.md §181 e
PROJECT_WIDE_REVIEW.md item #10).

Ícone pequeno (32×32) era carregado como arquivo PNG PRÓPRIO
(`pygame.image.load` por nome, `IconManager._cache`) — cada um é um
`open()` de disco + `convert_alpha()` na 1ª vez que aparece em jogo.
Com o catálogo crescendo (skill + talento reaproveitando ícone de
skill + item), agrupar em poucos PNGs grandes ("atlas") reduz o número
de arquivos abertos — mesmo padrão já usado pra tiles
(`ui/tile_sprite_manager.py::_get_sheet_region`, `SHEET_TILE_MAP`/
`OBJECT_SHEET_TILE_MAP`).

Convenção: 1 atlas por CATEGORIA, não 1 atlas único pra tudo — grade
uniforme (todo slot 32×32) por categoria é muito mais simples que
empacotar retângulos de tamanhos diferentes na mesma folha:
    assets/icons/atlas_skills.png — 512×512, 16×16 = 256 slots
    assets/icons/atlas_items.png  — 512×512, 16×16 = 256 slots

`ICON_ATLAS` (ui/icon_atlas_catalog.py) é gerado/mesclado por
`tools/build_icon_atlas.py` — nome do ícone → (arquivo do atlas, col,
row). Ícone ainda NÃO catalogado cai pro carregamento por arquivo
individual de sempre (`IconManager._load_individual`) — fallback
incremental, nunca quebra nada que ainda não migrou pro atlas.
"""
from __future__ import annotations
import os
import pygame

from ui.icon_atlas_catalog import ICON_ATLAS

ATLAS_DIR = "assets/icons"
CELL_SIZE = 32
GRID_COLS = 16   # 512 / 32 — usado só pelo script de empacotamento

# Variante "ativa" de um atlas inteiro (23/09/2026, pedido do usuário) —
# mesmo mecanismo de engine/tileset.py::NIGHT_SHEET_BY_FILE (folha
# companheira do MESMO grid, célula por célula, só troca o arquivo).
# Genérico: qualquer item interativo futuro (não só luz) que precise de
# um ícone "ligado"/"em uso" reaproveita isso, desde que o desenho esteja
# na MESMA (col, row) do item normal, só noutro arquivo.
ICON_CHANGE_SHEET_BY_FILE: dict[str, str] = {
    "atlas_items.png": "atlas_items_change.png",
}


class _IconAtlasSheets:
    """Cache dos PNGs de atlas carregados — 1 Surface por arquivo,
    nunca recarregado (mesmo padrão de `TileSpriteManager._raw`)."""

    def __init__(self) -> None:
        self._sheets: "dict[str, pygame.Surface | None]" = {}

    def _get_sheet(self, filename: str) -> "pygame.Surface | None":
        if filename in self._sheets:
            return self._sheets[filename]
        path = os.path.join(ATLAS_DIR, filename)
        surf = None
        if os.path.isfile(path):
            from ui.asset_cache import get_image
            surf = get_image(path)
        self._sheets[filename] = surf
        return surf

    def get_region(self, icon_name: str) -> "pygame.Surface | None":
        """Sub-Surface do slot (32×32, tamanho NATURAL — quem chama
        escala pro tamanho pedido, igual `IconManager.get()` já faz
        pro caminho de arquivo individual). `None` se `icon_name` não
        está catalogado, ou se o arquivo do atlas não existe/falhou."""
        entry = ICON_ATLAS.get(icon_name)
        if entry is None:
            return None
        filename, col, row = entry
        sheet = self._get_sheet(filename)
        if sheet is None:
            return None
        sx, sy = col * CELL_SIZE, row * CELL_SIZE
        region = pygame.Surface((CELL_SIZE, CELL_SIZE), pygame.SRCALPHA)
        region.blit(sheet, (0, 0), (sx, sy, CELL_SIZE, CELL_SIZE))
        return region

    def get_active_variant_region(self, icon_name: str) -> "pygame.Surface | None":
        """Sub-Surface da variante "ativa" do ícone (23/09/2026) — MESMO
        (col, row) do ícone normal, só lida da folha companheira
        (`ICON_CHANGE_SHEET_BY_FILE`). `None` se `icon_name` não está
        catalogado, se o atlas dele não tem folha companheira registrada,
        ou se o arquivo companheiro não existe/falhou — chamador cai pro
        ícone normal nesses casos (nunca quebra por falta de variante)."""
        entry = ICON_ATLAS.get(icon_name)
        if entry is None:
            return None
        filename, col, row = entry
        change_filename = ICON_CHANGE_SHEET_BY_FILE.get(filename)
        if change_filename is None:
            return None
        sheet = self._get_sheet(change_filename)
        if sheet is None:
            return None
        sx, sy = col * CELL_SIZE, row * CELL_SIZE
        region = pygame.Surface((CELL_SIZE, CELL_SIZE), pygame.SRCALPHA)
        region.blit(sheet, (0, 0), (sx, sy, CELL_SIZE, CELL_SIZE))
        return region

    def invalidate(self) -> None:
        """Limpa o cache de sheets — usar se o atlas for editado em
        disco durante a mesma sessão (ex: rodou o script de novo)."""
        self._sheets.clear()


ICON_ATLAS_SHEETS = _IconAtlasSheets()
