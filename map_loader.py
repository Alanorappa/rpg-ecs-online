# map_loader.py
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


def load_map_csv(filepath: str) -> tuple[list[str], dict]:
    """
    Lê um arquivo CSV e retorna a matriz de tiles + pontos de spawn.

    Args:
        filepath: Caminho para o arquivo .csv do mapa.

    Returns:
        tile_matrix: Lista de strings, cada string = uma linha do mapa.
                     Células de spawn são substituídas por '.' (chão) para renderização.
        spawn_points: Dicionário com:
            "player": (col, row) ou None se não houver P no mapa
            "enemies": lista de (col, row, enemy_type_str, tier_str)
    """
    filepath = resource_path(filepath)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Mapa não encontrado: {filepath}")

    tile_matrix = []
    player_spawn = None
    enemy_spawns = []
    portal_spawns = []
    merchant_spawns = []

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            row_chars = []
            for col_idx, cell in enumerate(row):
                char = cell.strip()
                if not char:
                    char = "."  # célula vazia vira chão

                # Extrai spawn do jogador
                if char == "P":
                    player_spawn = (col_idx, row_idx)
                    row_chars.append(".")  # renderiza como chão

                # Extrai portais/saídas de zona
                elif char == "O":
                    portal_spawns.append((col_idx, row_idx))
                    row_chars.append("O")  # mantém para renderização como PORTAL_TILE

                # Extrai spawns de comerciantes
                elif char in MERCHANT_TILE_CHARS:
                    merchant_spawns.append((col_idx, row_idx, MERCHANT_TILE_CHARS[char]))
                    row_chars.append(".")  # renderiza como chão

                # Extrai spawns de inimigos
                elif char in ENEMY_TILE_CHARS:
                    enemy_type, enemy_tier = ENEMY_TILE_CHARS[char]
                    enemy_spawns.append((col_idx, row_idx, enemy_type, enemy_tier))
                    row_chars.append(".")  # renderiza como chão

                else:
                    row_chars.append(char)

            tile_matrix.append("".join(row_chars))

    spawn_points = {
        "player":          player_spawn,
        "enemies":         enemy_spawns,
        "portals":         portal_spawns,
        "merchants":       merchant_spawns,
        "spawn_zones":     [],
        "transitions":     [],
        "ambient_zones":   [],   # lista de {name, ambient, rect:[x1,y1,x2,y2]}
        "default_ambient": "",   # ambient padrão do mapa (vazio = usa padrão do engine)
    }

    # Tenta carregar JSON de entidades ao lado do CSV (sistema novo)
    json_path = os.path.splitext(filepath)[0] + "_entities.json"
    if os.path.exists(json_path):
        _merge_entities_json(json_path, spawn_points)

    return tile_matrix, spawn_points


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
            (m["x"], m["y"], m.get("shop_id", "general"))
            for m in data["merchants"]
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
