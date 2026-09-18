# tools/build_icon_atlas.py
"""
build_icon_atlas.py — Empacota ícones individuais de assets/icons/ em
atlas grandes (17/09/2026, ver PROBLEMAS_ARQUITETURA.md §181 e
PROJECT_WIDE_REVIEW.md item #10).

Uso:
    py tools/build_icon_atlas.py
        → escaneia assets/icons/skill_*.png e assets/icons/item_*.png
          (só o nível raiz — subpastas tipo "Equipment/" não são
          escaneadas, são um pacote de assets morto sem uso no jogo)
        → pra cada ícone que ainda NÃO está em
          ui/icon_atlas_catalog.py::ICON_ATLAS: acha o próximo slot
          32×32 LIVRE no atlas da categoria (skill → atlas_skills.png,
          item → atlas_items.png), cola o ícone lá, e adiciona a
          entrada nova no catálogo.
        → escreve/atualiza:
            assets/icons/atlas_skills.png  (512×512, 16×16 slots)
            assets/icons/atlas_items.png   (512×512, 16×16 slots)
            ui/icon_atlas_catalog.py       (dict ICON_ATLAS mesclado)

ADITIVO por design — nunca reconstrói os PNGs de atlas do zero:
  - Se o atlas ainda não existe, começa de uma tela 512×512 transparente.
  - Se já existe, abre e cola só os ícones NOVOS nos slots livres —
    slots já ocupados (catalogados OU com pixel não-transparente,
    ex: célula pintada à mão num editor de imagem fora deste script)
    NUNCA são sobrescritos.
  - O catálogo é MESCLADO (entradas existentes, inclusive adicionadas
    à mão, são preservadas) — só ganha entradas novas.

Ícone cujo arquivo de origem não é exatamente 32×32 é redimensionado
(NEAREST, preserva o estilo pixel-art — mesma escolha que
`pygame.transform.scale` sem smoothscale já usa em outros lugares do
projeto).
"""
from __future__ import annotations

import os
import re
import sys

try:
    from PIL import Image
except ImportError:
    print("Precisa de Pillow instalado (pip install Pillow).")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ICONS_DIR   = "assets/icons"
CELL_SIZE   = 32
GRID_COLS   = 16
GRID_ROWS   = 16
ATLAS_SIZE  = CELL_SIZE * GRID_COLS   # 512

# prefixo do nome do arquivo → nome do atlas
_CATEGORY_ATLAS = {
    "skill_": "atlas_skills.png",
    "item_":  "atlas_items.png",
}


def _load_catalog_source() -> str:
    path = os.path.join("ui", "icon_atlas_catalog.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_catalog(source: str) -> "dict[str, tuple[str, int, int]]":
    """Importa o módulo de catálogo de verdade (fonte de verdade real,
    nunca reimplementa um parser de texto pro dict)."""
    from ui.icon_atlas_catalog import ICON_ATLAS
    return dict(ICON_ATLAS)


def _write_catalog(catalog: "dict[str, tuple[str, int, int]]") -> None:
    path = os.path.join("ui", "icon_atlas_catalog.py")
    header = (
        "# icon_atlas_catalog.py\n"
        '"""\n'
        "Catálogo do atlas de ícones — GERADO/ATUALIZADO por\n"
        "`tools/build_icon_atlas.py` (17/09/2026, ver PROBLEMAS_ARQUITETURA.md\n"
        "§181 e PROJECT_WIDE_REVIEW.md item #10).\n"
        "\n"
        "O script MESCLA — nunca apaga uma entrada existente ao rodar de novo.\n"
        "Entrada adicionada à mão (ícone pintado direto numa célula em branco\n"
        "do atlas, fora do script) é permitida e sobrevive a uma execução\n"
        "futura do script — só siga o mesmo formato de tupla abaixo.\n"
        "\n"
        "Formato: nome do ícone (mesma chave que `IconManager` já usa — sem\n"
        '".png") → (arquivo do atlas em assets/icons/, coluna, linha). Célula\n'
        "= 32×32px, grade 16×16 por atlas (ver ui/icon_atlas.py::CELL_SIZE/\n"
        "GRID_COLS).\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        'ICON_ATLAS: "dict[str, tuple[str, int, int]]" = {\n'
    )
    lines = [header]
    for name in sorted(catalog):
        filename, col, row = catalog[name]
        lines.append(f"    {name!r}: ({filename!r}, {col}, {row}),\n")
    lines.append("}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _slot_is_transparent(canvas: "Image.Image", col: int, row: int) -> bool:
    sx, sy = col * CELL_SIZE, row * CELL_SIZE
    region = canvas.crop((sx, sy, sx + CELL_SIZE, sy + CELL_SIZE))
    return region.getbbox() is None   # None = totalmente transparente


def _occupied_slots(canvas: "Image.Image", catalog: "dict[str, tuple[str, int, int]]",
                    atlas_filename: str) -> set:
    occupied = {(c, r) for (fname, c, r) in catalog.values() if fname == atlas_filename}
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            if (col, row) not in occupied and not _slot_is_transparent(canvas, col, row):
                occupied.add((col, row))
    return occupied


def _next_free_slot(occupied: set) -> "tuple[int, int] | None":
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            if (col, row) not in occupied:
                return (col, row)
    return None


def build() -> None:
    catalog = _parse_catalog(_load_catalog_source())
    added = 0

    for prefix, atlas_filename in _CATEGORY_ATLAS.items():
        atlas_path = os.path.join(ICONS_DIR, atlas_filename)
        if os.path.isfile(atlas_path):
            canvas = Image.open(atlas_path).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (ATLAS_SIZE, ATLAS_SIZE), (0, 0, 0, 0))

        occupied = _occupied_slots(canvas, catalog, atlas_filename)

        pattern = re.compile(rf"^{re.escape(prefix)}.*\.png$")
        source_files = sorted(
            f for f in os.listdir(ICONS_DIR)
            if os.path.isfile(os.path.join(ICONS_DIR, f)) and pattern.match(f)
        )

        for filename in source_files:
            icon_name = filename[:-4]   # remove ".png"
            if icon_name in catalog:
                continue

            slot = _next_free_slot(occupied)
            if slot is None:
                print(f"[AVISO] {atlas_filename} esta cheio (256/256) - "
                      f"'{icon_name}' nao foi adicionado.")
                continue

            icon_img = Image.open(os.path.join(ICONS_DIR, filename)).convert("RGBA")
            if icon_img.size != (CELL_SIZE, CELL_SIZE):
                icon_img = icon_img.resize((CELL_SIZE, CELL_SIZE), Image.NEAREST)

            col, row = slot
            canvas.paste(icon_img, (col * CELL_SIZE, row * CELL_SIZE))
            occupied.add(slot)
            catalog[icon_name] = (atlas_filename, col, row)
            added += 1
            print(f"  + {icon_name} -> {atlas_filename} slot ({col},{row})")

        canvas.save(atlas_path)

    _write_catalog(catalog)
    print(f"\n{added} icone(s) novo(s) catalogado(s). "
          f"Total no catalogo: {len(catalog)}.")


if __name__ == "__main__":
    build()
