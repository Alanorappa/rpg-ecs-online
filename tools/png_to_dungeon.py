"""
png_to_dungeon.py — Converte os 2 PNGs de uma DUNGEON instanciada (terreno +
entidades) em CSV + JSON de entidades, usando a MESMA convenção de "1 pixel
= 1 tile/entidade" de tools/png_to_map.py, mas com regras PRÓPRIAS de
dungeon — este arquivo NÃO modifica png_to_map.py nem seu comportamento
pra mapas normais (reusa só o utilitário puro _rgb_dist de lá).

Ver arquitetura/GERAR_DUNGEON_PNG.md pra convenção de cores completa e
exemplos. Resumo:

  PNG de terreno (ex: aracnopolis_dungeon_map.png) — SEM tolerância, sem
  fallback de borda (diferente de png_to_map.py, que trata preto puro
  como BORDER_CHAR="m" sempre):
    branco (255,255,255) → "_"  (chão genérico, estética de caverna)
    preto  (  0,  0,  0) → "c"  (parede de caverna)

  PNG de entidades (ex: aracnopolis_dungeon_entities.png):
    vermelho (255,0,0), clusterizado por CONECTIVIDADE 8 (pixels
    adjacentes/diagonais do MESMO cluster):
      cluster de 1 pixel  → zona de spawn de mob elite (sem respawn)
      cluster de 4 pixels → zona de spawn de BOSS (sem respawn, loot
                             customizado — stub vazio pro autor preencher)
      qualquer outro tamanho → AVISO, pulado (fail loud — não adivinha)
    amarelo (251,242,54), exatamente 2 pixels esperados:
      o de MENOR y (mais ao norte) → transição de SAÍDA (placeholder,
                                      target_map/x/y "EDIT_ME")
      o de MAIOR y (mais ao sul)   → ponto de chegada/respawn ("player")

Uso:
    py tools/png_to_dungeon.py <map_base> [--elite-level-min N] [--elite-level-max N]
                                           [--boss-level N] [--race NOME]
        → lê  <map_base>_map.png
               <map_base>_entities.png
        → gera maps/<nome>.csv
               maps/<nome>_entities.json
    (nome = basename de <map_base>, sem o diretório de origem — os PNGs
    costumam morar em assets/maps/, o CSV/JSON gerado sempre vai pra maps/,
    mesma pasta que o resto do jogo já lê.)

Ambos os arquivos de saída precisam de edição manual depois (o script
avisa no terminal): loot/gold_min/gold_max de cada boss, e
target_map/target_x/target_y da transição de saída.
"""
from __future__ import annotations

import sys
import os
import csv
import json
import argparse

try:
    from PIL import Image
except ImportError:
    print("Pillow não encontrado. Instale com:  pip install pillow")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.png_to_map import _rgb_dist

FLOOR_CHAR = "_"   # (80,80,80) "Chão genérico" — engine/tileset.py::TILE_PALETTE
WALL_CHAR  = "c"   # (40,30,20) "Parede de caverna" — idem

WHITE  = (255, 255, 255)
BLACK  = (0, 0, 0)
RED    = (255, 0, 0)
YELLOW = (251, 242, 54)

ELITE_CLUSTER_SIZE = 1
BOSS_CLUSTER_SIZE  = 4


# ---------------------------------------------------------------------------
# Terreno — binário, SEM tolerância/fallback de borda (diferente de
# png_to_map.py::convert_tiles, que trata preto como BORDER_CHAR="m" sempre).
# ---------------------------------------------------------------------------

def convert_dungeon_tiles(tiles_png: str, csv_path: str) -> "tuple[int, int] | None":
    if not os.path.exists(tiles_png):
        print(f"Tiles  : '{tiles_png}' não encontrado — CSV não gerado.")
        return None

    img = Image.open(tiles_png).convert("RGB")
    width, height = img.size
    pixels = img.load()
    print(f"Tiles  : {tiles_png}  ({width}×{height} pixels → {width}×{height} tiles)")

    unknown = 0
    rows_out = []
    for row in range(height):
        line = []
        for col in range(width):
            color = pixels[col, row][:3]
            if _rgb_dist(color, WHITE) <= _rgb_dist(color, BLACK):
                line.append(FLOOR_CHAR)
            else:
                line.append(WALL_CHAR)
            if color not in (WHITE, BLACK):
                unknown += 1
        rows_out.append(line)

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows_out)
    print(f"CSV    : {csv_path}  ✓")
    if unknown:
        print(f"Aviso tiles: {unknown} pixel(s) fora de branco/preto puro — "
              f"classificados pelo mais próximo (binário, sem tile intermediário).")
    return width, height


# ---------------------------------------------------------------------------
# Entidades — clustering 8-conectado nos pixels vermelhos (classifica por
# TAMANHO do cluster, não por cor — diferente de png_to_map.py, que trata
# cada pixel como 1 entidade isolada).
# ---------------------------------------------------------------------------

def _find_clusters(pixels, coords: set) -> "list[list[tuple[int, int]]]":
    """Agrupa `coords` por conectividade 8 (BFS). Retorna lista de clusters
    (cada um, uma lista de (x,y))."""
    visited: set = set()
    clusters: list = []
    for start in coords:
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        comp = []
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nb = (cx + dx, cy + dy)
                    if nb in coords and nb not in visited:
                        visited.add(nb)
                        stack.append(nb)
        clusters.append(comp)
    return clusters


def convert_dungeon_entities(entities_png: str, json_path: str,
                             expected_size: "tuple[int, int] | None" = None,
                             race: str = "Aranha", entity_class: str = "Guerreiro",
                             elite_level_min: int = 5, elite_level_max: int = 7,
                             boss_level: int = 7) -> None:
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

    red_coords: set = set()
    yellow_coords: list = []
    for row in range(height):
        for col in range(width):
            color = pixels[col, row][:3]
            if color == RED:
                red_coords.add((col, row))
            elif color == YELLOW:
                yellow_coords.append((col, row))

    spawn_zones = []
    n_elite = 0
    n_boss = 0
    n_skipped = 0
    for cluster in _find_clusters(pixels, red_coords):
        size = len(cluster)
        cx = round(sum(p[0] for p in cluster) / size)
        cy = round(sum(p[1] for p in cluster) / size)
        if size == ELITE_CLUSTER_SIZE:
            n_elite += 1
            spawn_zones.append({
                "x": cx, "y": cy, "radius": 1, "respawn_cooldown": 90.0,
                "respawnable": False,
                "level_min": elite_level_min, "level_max": elite_level_max,
                "race": race, "class": entity_class,
                "spawns": [{"type": "melee", "tier": "elite", "count": 1}],
            })
        elif size == BOSS_CLUSTER_SIZE:
            n_boss += 1
            spawn_zones.append({
                "x": cx, "y": cy, "radius": 1, "respawn_cooldown": 90.0,
                "respawnable": False,
                "level_min": boss_level, "level_max": boss_level,
                "race": race, "class": entity_class,
                "loot": {}, "gold_min": 0, "gold_max": 0,
                "spawns": [{"type": "melee", "tier": "boss", "count": 1}],
            })
        else:
            n_skipped += 1
            print(f"  AVISO: cluster vermelho de tamanho {size} em ~({cx},{cy}) "
                  f"não bate com elite ({ELITE_CLUSTER_SIZE}px) nem boss "
                  f"({BOSS_CLUSTER_SIZE}px) — pulado, ajuste o PNG.")

    if len(yellow_coords) != 2:
        print(f"  AVISO: esperava exatamente 2 pixels amarelos (entrada+saída), "
              f"achei {len(yellow_coords)} — transição/player não gerados "
              f"automaticamente, edite o JSON na mão.")
        player_pos = None
        exit_pos = None
    else:
        yellow_coords.sort(key=lambda p: p[1])  # menor y primeiro (norte)
        exit_pos, player_pos = yellow_coords[0], yellow_coords[1]

    entities = {
        "player": {"x": player_pos[0], "y": player_pos[1]} if player_pos else {"x": 1, "y": 1},
        # target_map/x/y usam um placeholder NUMÉRICO/caminho real (nunca
        # string tipo "EDIT_ME") — _load_map_for faz int(target_x) assim
        # que a instância carrega, mesmo antes de qualquer player entrar;
        # uma string quebraria a carga da dungeon inteira, não só a
        # transição de saída. Placeholder aponta pro mapa principal em
        # (0,0) — claramente errado o bastante pra não passar despercebido
        # em teste, mas nunca quebra o load.
        "transitions": (
            [{"x": exit_pos[0], "y": exit_pos[1],
              "target_map": "maps/map_1.csv", "target_x": 0, "target_y": 0}]
            if exit_pos else []
        ),
        "spawn_zones": spawn_zones,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)
    print(f"JSON   : {json_path}  ✓")
    print(f"\nEntidades encontradas:")
    print(f"  Elite (sem respawn) : {n_elite}")
    print(f"  Boss  (sem respawn) : {n_boss}  (edite loot/gold_min/gold_max no JSON)")
    if n_skipped:
        print(f"  Clusters ignorados  : {n_skipped}")
    print(f"  Entrada/respawn     : {player_pos if player_pos else 'NÃO ENCONTRADO'}")
    print(f"  Saída (transição)   : {exit_pos if exit_pos else 'NÃO ENCONTRADO'} "
          f"(edite target_map/target_x/target_y no JSON)")


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def convert(map_base: str, out_name: str, race: str, entity_class: str,
            elite_level_min: int, elite_level_max: int, boss_level: int) -> None:
    tiles_png = map_base + "_map.png"
    ent_png   = map_base + "_entities.png"
    csv_path  = os.path.join("maps", out_name + ".csv")
    json_path = os.path.join("maps", out_name + "_entities.json")

    print(f"\n{'─'*60}")
    print(f"Convertendo dungeon: {map_base}")
    print(f"{'─'*60}")

    size = convert_dungeon_tiles(tiles_png, csv_path)
    convert_dungeon_entities(ent_png, json_path, expected_size=size,
                             race=race, entity_class=entity_class,
                             elite_level_min=elite_level_min,
                             elite_level_max=elite_level_max,
                             boss_level=boss_level)
    print(f"{'─'*60}\nConcluído. Edite o JSON gerado antes de usar em jogo "
          f"(ver arquitetura/GERAR_DUNGEON_PNG.md).\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("map_base", help="ex: assets/maps/aracnopolis_dungeon")
    parser.add_argument("--race", default="Aranha")
    parser.add_argument("--class", dest="entity_class", default="Guerreiro")
    parser.add_argument("--elite-level-min", type=int, default=5)
    parser.add_argument("--elite-level-max", type=int, default=7)
    parser.add_argument("--boss-level", type=int, default=7)
    args = parser.parse_args()

    out_name = os.path.basename(args.map_base)
    convert(args.map_base, out_name, args.race, args.entity_class,
            args.elite_level_min, args.elite_level_max, args.boss_level)
