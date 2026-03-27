"""
png_to_map.py — Converte PNGs de mapa em CSV + JSON de entidades.

Convenção de nomes (usando 'maps/map_2' como base):
    maps/map_2_tiles.png    → terreno   (1 pixel = 1 tile)
    maps/map_2_entities.png → entidades (1 pixel = 1 entidade; preto = vazio)

Uso:
    py png_to_map.py maps/map_2
        → lê  maps/map_2_tiles.png
               maps/map_2_entities.png  (opcional — ignorado se ausente)
        → gera maps/map_2.csv
               maps/map_2_entities.json

    py png_to_map.py --palette
        → imprime o índice de cores no terminal

Regras do PNG de tiles:
  - Cada pixel = 1 tile (char no CSV)
  - Preto (0,0,0) → montanha 'm'
  - Cores sem correspondência exata → tile mais próximo (tolerância ≤ 80)

Regras do PNG de entidades:
  - Preto (0,0,0) → vazio, ignorado
  - Correspondência EXATA com ENTITY_PNG_PALETTE (sem tolerância)
  - Cores não reconhecidas → aviso, ignoradas

Ambient zones NÃO são geradas pelo PNG — adicione-as manualmente no JSON.
Transitions são geradas como placeholders — edite target_map/target_x/target_y.
"""

from __future__ import annotations

import sys
import csv
import json
import math
import os

try:
    from PIL import Image
except ImportError:
    print("Pillow não encontrado. Instale com:  pip install pillow")
    sys.exit(1)

from tileset import TILE_PALETTE, ENTITY_PNG_PALETTE
from mob_definitions import MOB_TABLE


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BORDER_COLOR = (0, 0, 0)
BORDER_CHAR  = "m"
MAX_DIST_TILE = 80   # tolerância RGB para tiles (não se aplica a entidades)

SPAWN_DEFAULTS = {
    "radius":            5,
    "respawn_cooldown":  60,
    "level_min":         1,
    "level_max":         1,
    "count":             3,
}


# ---------------------------------------------------------------------------
# Utilitários de cor
# ---------------------------------------------------------------------------

def _rgb_dist(a: tuple, b: tuple) -> float:
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


def _nearest_terrain_char(color: tuple) -> str:
    """Retorna o char de terreno mais próximo, ou BORDER_CHAR se acima da tolerância."""
    if color in TILE_PALETTE:
        return TILE_PALETTE[color]
    best_char = BORDER_CHAR
    best_dist = float("inf")
    for palette_color, char in TILE_PALETTE.items():
        d = _rgb_dist(color, palette_color)
        if d < best_dist:
            best_dist = d
            best_char = char
    return best_char if best_dist <= MAX_DIST_TILE else BORDER_CHAR


# ---------------------------------------------------------------------------
# Conversão de tiles (PNG → CSV)
# ---------------------------------------------------------------------------

def convert_tiles(tiles_png: str, csv_path: str) -> "tuple[int, int] | None":
    """
    Lê o PNG de terreno e gera o CSV.
    Retorna (width, height) do mapa, ou None se o arquivo não existir.
    """
    if not os.path.exists(tiles_png):
        print(f"Tiles  : '{tiles_png}' não encontrado — CSV não gerado.")
        return None

    img = Image.open(tiles_png).convert("RGB")
    width, height = img.size
    pixels = img.load()

    print(f"Tiles  : {tiles_png}  ({width}×{height} pixels → {width}×{height} tiles)")

    unknown_colors: dict[tuple, int] = {}
    rows_out = []

    for row in range(height):
        line = []
        for col in range(width):
            color = pixels[col, row][:3]

            if color == BORDER_COLOR:
                line.append(BORDER_CHAR)
                continue

            char = _nearest_terrain_char(color)
            line.append(char)

            if color not in TILE_PALETTE:
                d = min(_rgb_dist(color, pc) for pc in TILE_PALETTE)
                if d > 10:
                    unknown_colors[color] = unknown_colors.get(color, 0) + 1

        rows_out.append(line)

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows_out)
    print(f"CSV    : {csv_path}  ✓")

    if unknown_colors:
        print(f"\nAviso tiles: {len(unknown_colors)} cor(es) sem correspondência exata:")
        for color, count in sorted(unknown_colors.items(), key=lambda x: -x[1])[:10]:
            print(f"  RGB{color}  ({count} px) → tile mais próximo")

    return width, height


# ---------------------------------------------------------------------------
# Conversão de entidades (PNG → _entities.json)
# ---------------------------------------------------------------------------

def convert_entities(entities_png: str, json_path: str,
                     expected_size: tuple[int, int] | None = None) -> None:
    """
    Lê o PNG de entidades e gera o JSON de entidades.
    Correspondência de cores é EXATA — nenhuma tolerância.
    """
    if not os.path.exists(entities_png):
        print(f"Entidades: '{entities_png}' não encontrado — JSON não gerado.")
        return

    img = Image.open(entities_png).convert("RGB")
    width, height = img.size
    pixels = img.load()

    if expected_size and (width, height) != expected_size:
        print(f"Aviso: entidades ({width}×{height}) ≠ tiles ({expected_size[0]}×{expected_size[1]}). "
              f"As posições podem ficar desalinhadas.")

    print(f"Entidades: {entities_png}  ({width}×{height} pixels)")

    player_pos   = None
    merchants    = []
    transitions  = []
    spawn_zones  = []
    unknown_ent: dict[tuple, int] = {}

    for row in range(height):
        for col in range(width):
            color = pixels[col, row][:3]

            if color == BORDER_COLOR:
                continue  # vazio

            if color not in ENTITY_PNG_PALETTE:
                unknown_ent[color] = unknown_ent.get(color, 0) + 1
                continue

            ent = ENTITY_PNG_PALETTE[color]
            kind = ent["entity_type"]

            if kind == "player":
                if player_pos is None:
                    player_pos = (col, row)

            elif kind == "merchant":
                merchants.append({
                    "x": col,
                    "y": row,
                    "shop_id": ent.get("shop_id", "general"),
                })

            elif kind == "transition":
                transitions.append({
                    "x": col,
                    "y": row,
                    "target_map":  "EDIT_ME.csv",
                    "target_x":    0,
                    "target_y":    0,
                })

            elif kind == "spawn":
                mob_name = ent["mob"]
                mob_data = MOB_TABLE.get(mob_name, {})
                race         = mob_data.get("race", "Humanoide")
                entity_class = mob_data.get("entity_class", "Warrior")
                is_ranged    = mob_data.get("is_ranged", False)
                spawn_type   = "ranged" if is_ranged else "melee"
                spawn_zones.append({
                    "x":                col,
                    "y":                row,
                    "radius":           SPAWN_DEFAULTS["radius"],
                    "respawn_cooldown": SPAWN_DEFAULTS["respawn_cooldown"],
                    "level_min":        SPAWN_DEFAULTS["level_min"],
                    "level_max":        SPAWN_DEFAULTS["level_max"],
                    "race":             mob_name,        # chave do MOB_TABLE
                    "class":            entity_class,
                    "spawns": [
                        {
                            "type":  spawn_type,
                            "tier":  "normal",
                            "count": SPAWN_DEFAULTS["count"],
                        }
                    ],
                })

    entities = {
        "player":      {"x": player_pos[0], "y": player_pos[1]} if player_pos else {"x": 1, "y": 1},
        "merchants":   merchants,
        "transitions": transitions,
        "spawn_zones": spawn_zones,
        "ambient_zones": [],
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)
    print(f"JSON   : {json_path}  ✓")

    # Resumo
    print(f"\nEntidades encontradas:")
    print(f"  Player      : {'sim (' + str(player_pos) + ')' if player_pos else 'NÃO ENCONTRADO — use cor #FFFFFF'}")
    print(f"  Mercadores  : {len(merchants)}")
    print(f"  Transitions : {len(transitions)}  {'(edite target_map/target_x/target_y no JSON)' if transitions else ''}")
    print(f"  Spawn zones : {len(spawn_zones)}")

    if not player_pos:
        print("ATENÇÃO: spawn do player não encontrado. Edite o JSON manualmente.")

    if unknown_ent:
        print(f"\nAviso entidades: {len(unknown_ent)} cor(es) não reconhecida(s) — ignoradas:")
        for color, count in sorted(unknown_ent.items(), key=lambda x: -x[1])[:10]:
            hex_c = "#{:02X}{:02X}{:02X}".format(*color)
            print(f"  RGB{color} / {hex_c}  ({count} px)")
        print("  Use as cores exatas do índice (--palette) no PNG de entidades.")


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def convert(map_base: str) -> None:
    """
    Processa os dois PNGs para um mapa.
    `map_base` é o prefixo sem extensão, ex.: 'maps/map_2'
    Aceita tanto 'maps/map_2_tiles.png' quanto 'maps/map_2.png' como arquivo de terreno.
    """
    # Aceita _tiles.png ou .png diretamente
    if os.path.exists(map_base + "_tiles.png"):
        tiles_png = map_base + "_tiles.png"
    else:
        tiles_png = map_base + ".png"

    ent_png   = map_base + "_entities.png"
    csv_path  = map_base + ".csv"
    json_path = map_base + "_entities.json"

    print(f"\n{'─'*60}")
    print(f"Convertendo: {map_base}")
    print(f"{'─'*60}")

    size = convert_tiles(tiles_png, csv_path)
    convert_entities(ent_png, json_path, expected_size=size)

    print(f"{'─'*60}\nConcluído.\n")


# ---------------------------------------------------------------------------
# Impressão do índice de cores
# ---------------------------------------------------------------------------

def print_palette() -> None:
    from tileset import TILE_MAPPING

    print("\n  PNG → MAPA: ÍNDICE DE CORES")
    print("  Salve SEMPRE como PNG (não JPG). Correspondência é por pixel exato.")

    # ── Terreno ──────────────────────────────────────────────────────────────
    print("\n  ── ARQUIVO *_tiles.png  (terreno) ──────────────────────────────────")
    print(f"  {'Elemento':<24} {'RGB':<18} {'Hex':<10} Char  Sólido")
    print(f"  {'─'*66}")

    terrain_order = [
        ("G", "Grama"),
        (".", "Piso de pedra (cidade)"),
        ("~", "Areia"),
        ("f", "Terra batida"),
        ("_", "Chão genérico"),
        ("d", "Entrada de caverna"),
        ("O", "Portal / saída"),
        ("#", "Parede da cidade"),
        ("@", "Parede das ruínas"),
        ("c", "Parede de caverna"),
        ("m", "Montanha / borda"),
        ("W", "Água"),
        ("t", "Árvore"),
        ("b", "Arbusto"),
        ("k", "Pedra / lápide"),
    ]
    char_to_color = {v: k for k, v in TILE_PALETTE.items()}
    for char, name in terrain_order:
        color = char_to_color.get(char, (0, 0, 0))
        hex_c = "#{:02X}{:02X}{:02X}".format(*color)
        tile  = TILE_MAPPING.get(char)
        solid = "sim" if tile and tile.is_solid else "não"
        print(f"  {name:<24} {str(color):<18} {hex_c:<10} '{char}'   {solid}")
    print("  (Preto 0,0,0 = borda → 'm'  |  tolerância RGB ≤ 80)")

    # ── Entidades ─────────────────────────────────────────────────────────────
    print("\n  ── ARQUIVO *_entities.png  (entidades) ─────────────────────────────")
    print("  Correspondência EXATA. Preto (0,0,0) = vazio.")
    print(f"\n  {'Entidade':<28} {'RGB':<20} {'Hex':<10} Raça / Classe")
    print(f"  {'─'*72}")

    special_labels = {
        "player":     "Spawn do Player",
        "merchant":   "Mercador (loja geral)",
        "transition": "Transition (placeholder)",
    }

    for color, ent in ENTITY_PNG_PALETTE.items():
        hex_c = "#{:02X}{:02X}{:02X}".format(*color)
        kind  = ent["entity_type"]

        if kind in special_labels:
            label = special_labels[kind]
            info  = ent.get("shop_id", "") or "edite target_map no JSON"
            print(f"  {label:<28} {str(color):<20} {hex_c:<10} {info}")
        elif kind == "spawn":
            mob_name = ent["mob"]
            mob_data = MOB_TABLE.get(mob_name, {})
            race     = mob_data.get("race", "?")
            cls      = mob_data.get("entity_class", "?")
            ranged   = "ranged" if mob_data.get("is_ranged") else "melee"
            print(f"  {mob_name:<28} {str(color):<20} {hex_c:<10} {race} / {cls} ({ranged})")

    print(f"\n  Padrões no JSON: radius=5, cooldown=60, level 1-1, count=3, tier=normal.")
    print(f"  Edite o JSON gerado para ajustar level/tier/radius de cada zona.\n")


# ---------------------------------------------------------------------------
# Scan automático da pasta maps/
# ---------------------------------------------------------------------------

def _collect_bases(maps_dir: str = "maps") -> list[str]:
    """
    Retorna a lista de map_base encontrados na pasta maps/.

    Regras:
      - Arquivos *_tiles.png    → base = maps/<stem sem '_tiles'>
      - Arquivos *.png restantes (sem '_entities' nem '_tiles') → base = maps/<stem>
      - Duplicatas são removidas (ex.: 'map_2_tiles.png' e 'map_2.png' → um único base).
    """
    if not os.path.isdir(maps_dir):
        print(f"Pasta '{maps_dir}' não encontrada.")
        return []

    bases: dict[str, str] = {}   # base_key → caminho completo sem ext

    for fname in sorted(os.listdir(maps_dir)):
        if not fname.lower().endswith(".png"):
            continue
        stem = fname[:-4]  # remove .png

        if stem.endswith("_entities"):
            continue  # PNG de entidades — ignorado aqui

        if stem.endswith("_tiles"):
            key = os.path.join(maps_dir, stem[:-6])  # remove '_tiles'
        else:
            key = os.path.join(maps_dir, stem)

        if key not in bases:
            bases[key] = key

    return list(bases.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--palette" in sys.argv:
        print_palette()
        sys.exit(0)

    if len(sys.argv) >= 2:
        # Argumento explícito: converte só aquele mapa
        convert(sys.argv[1])
    else:
        # Sem argumento: converte todos os PNGs da pasta maps/
        bases = _collect_bases("maps")
        if not bases:
            print("Nenhum PNG encontrado em maps/.")
            print("Uso: py png_to_map.py <map_base>  (ex: maps/map_2)")
            sys.exit(1)

        print(f"Encontrados {len(bases)} mapa(s) em maps/  →  convertendo todos...\n")
        for base in bases:
            convert(base)
