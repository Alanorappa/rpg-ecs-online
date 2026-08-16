"""
tools/png_to_map_layers.py — Converte PNG de terreno + PNG de objetos em
`<map_base>_terrain.csv` + `<map_base>_objects.csv` (formato de 2 camadas
usado por `engine/map_loader.py::load_map_csv` — ver `_load_two_layer`).

Diferente de `tools/png_to_map.py` (formato antigo, 1 CSV único): aqui
terrain.png E objects.png usam a MESMA paleta (`engine/tileset.py::
TILE_PALETTE`, ver `py tools/png_to_map.py --palette`) — correspondência
EXATA, sem tolerância.

Regra do PNG de objetos: uma cor que mapeia pra um char de terreno
SÓLIDO/ESTRUTURAL (parede '#', portão de arena 'D', montanha 'm', ...)
SOBRESCREVE o terrain.csv naquela célula — esses chars não existem no
catálogo de objects.csv (`OBJECT_CHARS`), então escrevê-los lá seria
descartado em silêncio pelo loader (`_load_two_layer`). Uma cor que mapeia
pra objeto decorativo legado (árvore 't', arbusto 'b', pedra 'k') entra
no objects.csv normalmente. Preto (0,0,0) = vazio (sem objeto/override).

Uso:
    py tools/png_to_map_layers.py <terrain.png> <objects.png> <map_base> \
        [R,G,B=chave ...]
        → gera <map_base>_terrain.csv e <map_base>_objects.csv
        → overrides opcionais: força uma cor específica do PNG de objetos a
          virar uma chave exata do catálogo (ex: sprite variant) em vez do
          char legado de TILE_PALETTE — ex: 100,200,50=pl_b11
"""
from __future__ import annotations

import sys
import csv

try:
    from PIL import Image
except ImportError:
    print("Pillow não encontrado. Instale com: pip install pillow")
    sys.exit(1)

from engine.tileset import TILE_PALETTE

# Chars de TILE_PALETTE que representam terreno SÓLIDO/ESTRUTURAL — se
# aparecerem no PNG de OBJETOS, sobrescrevem o terrain.csv (não entram em
# objects.csv, que não reconhece esses chars).
_STRUCTURAL_TERRAIN_CHARS = {"#", "@", "c", "m", "W", "D"}

# Chars legados de objeto decorativo (árvore/arbusto/pedra) — herdados do
# formato de CSV único antigo, ainda são chaves válidas de OBJECT_MAPPING
# no formato de 2 camadas.
_LEGACY_OBJECT_CHARS = {"t", "b", "k"}


def convert_layers(terrain_png: str, objects_png: str, map_base: str,
                    object_overrides: "dict[tuple, str] | None" = None) -> None:
    object_overrides = object_overrides or {}

    t_img = Image.open(terrain_png).convert("RGB")
    o_img = Image.open(objects_png).convert("RGB")
    if t_img.size != o_img.size:
        print(f"ERRO: terrain {t_img.size} != objects {o_img.size} — tamanhos precisam bater.")
        sys.exit(1)
    w, h = t_img.size
    tp = t_img.load()
    op = o_img.load()

    terrain_rows: list[list[str]] = []
    object_rows: list[list[str]] = []
    unknown_terrain: dict[tuple, int] = {}
    unknown_object: dict[tuple, int] = {}

    for y in range(h):
        t_line, o_line = [], []
        for x in range(w):
            tc = tp[x, y][:3]
            terrain_char = TILE_PALETTE.get(tc)
            if terrain_char is None:
                unknown_terrain[tc] = unknown_terrain.get(tc, 0) + 1
                terrain_char = "G"  # fallback conservador

            oc = op[x, y][:3]
            obj_token = None
            if oc != (0, 0, 0):
                if oc in object_overrides:
                    obj_token = object_overrides[oc]
                elif oc in TILE_PALETTE:
                    mapped = TILE_PALETTE[oc]
                    if mapped in _STRUCTURAL_TERRAIN_CHARS:
                        terrain_char = mapped  # objects.png SOBRESCREVE terrain
                    elif mapped in _LEGACY_OBJECT_CHARS:
                        obj_token = mapped
                    else:
                        unknown_object[oc] = unknown_object.get(oc, 0) + 1
                else:
                    unknown_object[oc] = unknown_object.get(oc, 0) + 1

            t_line.append(terrain_char)
            o_line.append(obj_token if obj_token else ".")
        terrain_rows.append(t_line)
        object_rows.append(o_line)

    t_path = f"{map_base}_terrain.csv"
    o_path = f"{map_base}_objects.csv"
    with open(t_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(terrain_rows)
    with open(o_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(object_rows)

    print(f"Terrain: {t_path}  ({w}x{h})")
    print(f"Objects: {o_path}  ({w}x{h})")
    if unknown_terrain:
        print(f"Aviso: {len(unknown_terrain)} cor(es) de terreno sem correspondencia (viraram 'G'): {unknown_terrain}")
    if unknown_object:
        print(f"Aviso: {len(unknown_object)} cor(es) de objeto sem correspondencia (ignoradas): {unknown_object}")


def _parse_override(arg: str) -> "tuple[tuple, str]":
    rgb_part, key = arg.split("=", 1)
    r, g, b = (int(x) for x in rgb_part.split(","))
    return (r, g, b), key


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: py tools/png_to_map_layers.py <terrain.png> <objects.png> "
              "<map_base> [R,G,B=chave ...]")
        sys.exit(1)

    overrides = dict(_parse_override(a) for a in sys.argv[4:])
    convert_layers(sys.argv[1], sys.argv[2], sys.argv[3], overrides)
