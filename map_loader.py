# map_loader.py
from __future__ import annotations
"""
Carrega mapas a partir de arquivos CSV + JSON opcional de entidades.

Formato do CSV (terrain only):
  # → parede   . → chão   G → grama   O → portal

Formato do JSON de entidades (map_N_entities.json ao lado do CSV):
  {
    "player":      {"x": 20, "y": 7},
    "spawn_zones": [
      {"x":5, "y":4, "radius":3, "respawn_cooldown":30,
       "level_min":1, "level_max":3,
       "spawns": [{"type":"melee","tier":"normal","count":2},
                  {"type":"ranged","tier":"normal","count":2}]}
    ],
    "merchants": [{"x":17, "y":7, "shop_id":"general"}]
  }

Se nenhum JSON existir, o loader usa o sistema legado de caracteres E/R/P/N no CSV.
"""

import csv
import json
import os
from paths import resource_path
from tileset import OBJECT_CHARS, OBJECT_UNDERLYING


# Tipos de comerciantes codificados no mapa: char → shop_id
MERCHANT_TILE_CHARS = {
    "N": "general",   # Mercador Geral
}

# Tipos de inimigos codificados no mapa: (tipo, tier)
ENEMY_TILE_CHARS = {
    "E": ("melee",   "normal"),   # Normal melee
    "R": ("ranged",  "normal"),   # Normal ranged
    "A": ("melee",   "elite"),    # Elite melee
    "T": ("ranged",  "elite"),    # Elite ranged
    "M": ("melee",   "rare"),     # Rare melee
    "X": ("ranged",  "rare"),     # Rare ranged
    "B": ("melee",   "boss"),     # Boss
}


def load_map_csv(filepath: str) -> tuple[list[str], list[str], dict, list | None]:
    """
    Carrega um mapa e retorna (terrain_matrix, object_matrix, spawn_points, terrain_visual).

    terrain_visual — list[list[str]] com sprite IDs de sheet por tile, ou None se não existir.
    """
    filepath  = resource_path(filepath)
    base      = os.path.splitext(filepath)[0]
    t_path    = base + "_terrain.csv"
    o_path    = base + "_objects.csv"

    if os.path.exists(t_path) and os.path.exists(o_path):
        terrain_matrix, object_matrix, player_spawn, enemy_spawns, \
            portal_spawns, merchant_spawns = _load_two_layer(t_path, o_path)
    elif os.path.exists(filepath):
        terrain_matrix, object_matrix, player_spawn, enemy_spawns, \
            portal_spawns, merchant_spawns = _load_single_layer(filepath)
    else:
        raise FileNotFoundError(f"Mapa não encontrado: {filepath}")

    spawn_points = {
        "player":          player_spawn,
        "enemies":         enemy_spawns,
        "portals":         portal_spawns,
        "merchants":       merchant_spawns,
        "quest_givers":    [],
        "blacksmiths":     [],
        "trainers":        [],
        "spawn_zones":     [],
        "transitions":     [],
        "ambient_zones":   [],
        "default_ambient": "",
    }

    json_path = base + "_entities.json"
    if os.path.exists(json_path):
        _merge_entities_json(json_path, spawn_points)

    # Carrega terrain_visual (sheet overrides) se existir
    terrain_visual = None
    v_path = base + "_terrain_sprites.csv"
    if os.path.exists(v_path):
        terrain_visual = _load_terrain_visual(v_path)

    return terrain_matrix, object_matrix, spawn_points, terrain_visual


def _load_terrain_visual(path: str) -> list[list[str]]:
    """Lê o CSV de sprite IDs de terreno."""
    matrix = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            matrix.append([c.strip() for c in row])
    return matrix


def _load_two_layer(terrain_path: str, objects_path: str):
    """Carrega terrain e objects de dois CSVs separados."""
    terrain_matrix = []
    player_spawn   = None
    enemy_spawns   = []
    portal_spawns  = []
    merchant_spawns = []

    with open(terrain_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row or all(c.strip() == "" for c in row):
                continue
            terrain_row = []
            for col_idx, cell in enumerate(row):
                char = cell.strip() or "."
                if char == "P":
                    player_spawn = (col_idx, row_idx)
                    terrain_row.append(".")
                elif char == "O":
                    portal_spawns.append((col_idx, row_idx))
                    terrain_row.append("O")
                elif char in MERCHANT_TILE_CHARS:
                    merchant_spawns.append((col_idx, row_idx, MERCHANT_TILE_CHARS[char]))
                    terrain_row.append(".")
                elif char in ENEMY_TILE_CHARS:
                    enemy_type, enemy_tier = ENEMY_TILE_CHARS[char]
                    enemy_spawns.append((col_idx, row_idx, enemy_type, enemy_tier))
                    terrain_row.append("G")
                else:
                    terrain_row.append(char)
            terrain_matrix.append("".join(terrain_row))

    object_matrix = []
    with open(objects_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or all(c.strip() == "" for c in row):
                continue
            object_matrix.append([
                (c.strip() if c.strip() in OBJECT_CHARS else ".") for c in row
            ])

    # Garante que object_matrix tem o mesmo número de linhas que terrain_matrix
    while len(object_matrix) < len(terrain_matrix):
        w = len(terrain_matrix[len(object_matrix)]) if terrain_matrix else 0
        object_matrix.append(["."] * w)

    return terrain_matrix, object_matrix, player_spawn, enemy_spawns, \
           portal_spawns, merchant_spawns


def _load_single_layer(filepath: str):
    """Carrega mapa legado de um único CSV e separa terrain/objects automaticamente."""
    terrain_matrix = []
    object_matrix  = []
    player_spawn   = None
    enemy_spawns   = []
    portal_spawns  = []
    merchant_spawns = []

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            terrain_row = []
            object_row  = []
            for col_idx, cell in enumerate(row):
                char = cell.strip() or "."
                if char == "P":
                    player_spawn = (col_idx, row_idx)
                    terrain_row.append(".")
                    object_row.append(".")
                elif char == "O":
                    portal_spawns.append((col_idx, row_idx))
                    terrain_row.append("O")
                    object_row.append(".")
                elif char in MERCHANT_TILE_CHARS:
                    merchant_spawns.append((col_idx, row_idx, MERCHANT_TILE_CHARS[char]))
                    terrain_row.append(".")
                    object_row.append(".")
                elif char in ENEMY_TILE_CHARS:
                    enemy_type, enemy_tier = ENEMY_TILE_CHARS[char]
                    enemy_spawns.append((col_idx, row_idx, enemy_type, enemy_tier))
                    terrain_row.append("G")
                    object_row.append(".")
                elif char in OBJECT_CHARS:
                    terrain_row.append(OBJECT_UNDERLYING.get(char, "G"))
                    object_row.append(char)
                else:
                    terrain_row.append(char)
                    object_row.append(".")
            terrain_matrix.append("".join(terrain_row))
            object_matrix.append(object_row)

    return terrain_matrix, object_matrix, player_spawn, enemy_spawns, \
           portal_spawns, merchant_spawns


def _merge_entities_json(json_path: str, spawn_points: dict) -> None:
    """
    Lê o JSON de entidades e sobrescreve / complementa spawn_points.
    O JSON pode definir: player, spawn_zones, merchants.
    Portais continuam sendo extraídos do CSV (tile O).
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    if "player" in data:
        p = data["player"]
        spawn_points["player"] = (p["x"], p["y"])
        # Remove qualquer P que o CSV tivesse extraído
        spawn_points["enemies"] = [
            e for e in spawn_points["enemies"]
        ]

    if "merchants" in data:
        spawn_points["merchants"] = [
            (m["x"], m["y"], m.get("shop_id", "general"),
             m.get("level", 1), m.get("profession", "Comerciante"))
            for m in data["merchants"]
        ]
    else:
        # Normaliza tuplas CSV (3 elementos) para o formato completo (5 elementos)
        spawn_points["merchants"] = [
            (col, row, sid, 1, "Comerciante")
            for col, row, sid in spawn_points["merchants"]
        ]

    if "quest_givers" in data:
        spawn_points["quest_givers"] = [
            (q["x"], q["y"],
             q.get("name", "Missiveiro"),
             tuple(q.get("quest_ids", [])),
             tuple(q.get("turn_in_ids", [])),
             q.get("level", 1),
             q.get("profession", "Missiveiro"))
            for q in data["quest_givers"]
        ]

    if "blacksmiths" in data:
        spawn_points["blacksmiths"] = [
            (b["x"], b["y"],
             b.get("name", "Ferreiro"),
             b.get("shop_id", "blacksmith"),
             b.get("level", 1),
             b.get("profession", "Ferreiro"))
            for b in data["blacksmiths"]
        ]

    if "trainers" in data:
        spawn_points["trainers"] = [
            (t["x"], t["y"],
             t.get("name", "Treinador"),
             t.get("class_id", "guerreiro"),
             tuple(t.get("quest_ids", [])),
             tuple(t.get("turn_in_ids", [])),
             t.get("level", 1),
             t.get("profession", "Treinador"))
            for t in data["trainers"]
        ]

    if "transitions" in data:
        spawn_points["transitions"] = data["transitions"]

    if "spawn_zones" in data:
        for zone in data["spawn_zones"]:
            for sp in zone.get("spawns", []):
                spawn_points["spawn_zones"].append({
                    "x":                zone["x"],
                    "y":                zone["y"],
                    "radius":           zone.get("radius", 3),
                    "respawn_cooldown": zone.get("respawn_cooldown", 60.0),
                    "level_min":        zone.get("level_min", 1),
                    "level_max":        zone.get("level_max", 1),
                    "enemy_type":       sp["type"],
                    "enemy_tier":       sp.get("tier", "normal"),
                    "count":            sp.get("count", 1),
                    "race":             sp.get("race", zone.get("race", "Humanoide")),
                    "entity_class":     sp.get("class", zone.get("class", "")),
                })
        # Se o JSON define zonas, limpa os spawns legados do CSV
        if spawn_points["spawn_zones"]:
            spawn_points["enemies"] = []

    if "default_ambient" in data:
        spawn_points["default_ambient"] = data["default_ambient"]

    if "ambient_zones" in data:
        for z in data["ambient_zones"]:
            r = z.get("rect", [0, 0, 0, 0])
            music_raw = z.get("music", [])
            # aceita string única ou lista
            if isinstance(music_raw, str):
                music_raw = [music_raw]
            ambient_raw = z.get("ambient", [])
            if isinstance(ambient_raw, str):
                ambient_raw = [ambient_raw] if ambient_raw else []
            spawn_points["ambient_zones"].append({
                "name":    z.get("name", ""),
                "ambient": ambient_raw,   # lista de filenames em assets/sounds/sfx/
                "music":   music_raw,     # lista de filenames em assets/sounds/music/
                "rect":    (int(r[0]), int(r[1]), int(r[2]), int(r[3])),
            })


def validate_map(tile_matrix: list[str]) -> list[str]:
    """
    Valida consistência do mapa (todas as linhas com o mesmo comprimento).
    Retorna lista de avisos. Lista vazia = mapa válido.
    """
    warnings = []
    if not tile_matrix:
        warnings.append("Mapa vazio.")
        return warnings

    expected_width = len(tile_matrix[0])
    for idx, row in enumerate(tile_matrix):
        if len(row) != expected_width:
            warnings.append(
                f"Linha {idx} tem {len(row)} tiles, esperado {expected_width}."
            )

    return warnings
