# map_loader.py
from __future__ import annotations
"""
Carrega mapas a partir de arquivos CSV + JSON opcional de entidades.

Formato do terrain CSV (unificado):
  Cada célula é OU um char de terreno (G, #, W, ~...) OU um ID de sprite de sheet
  (ex: txg_0_0). Se for um ID de sheet, o terreno subjacente é determinado pela
  família em SHEET_FAMILIES e o sprite serve de visual.

Formato do JSON de entidades (map_N_entities.json ao lado do CSV):
  {
    "player":      {"x": 20, "y": 7},
    "spawn_zones": [...],
    "merchants":   [...]
  }
"""

import csv
import json
import os
from paths import resource_path
from engine.tileset import OBJECT_CHARS, OBJECT_UNDERLYING


# Tipos de comerciantes codificados no mapa: char → shop_id
MERCHANT_TILE_CHARS = {
    "N": "general",   # Mercador Geral
}

# Tipos de inimigos codificados no mapa: (tipo, tier)
ENEMY_TILE_CHARS = {
    "E": ("melee",   "normal"),
    "R": ("ranged",  "normal"),
    "A": ("melee",   "elite"),
    "T": ("ranged",  "elite"),
    "M": ("melee",   "rare"),
    "X": ("ranged",  "rare"),
    "B": ("melee",   "boss"),
}

# Cache: prefixo de sheet → char de terreno subjacente (populado na primeira chamada)
_SHEET_UNDERLYING: dict[str, str] | None = None


def _get_sheet_underlying() -> dict[str, str]:
    """Retorna mapeamento prefix → underlying_terrain das SHEET_FAMILIES."""
    global _SHEET_UNDERLYING
    if _SHEET_UNDERLYING is None:
        from engine.tileset import SHEET_TILE_MAP, SHEET_FAMILIES
        _SHEET_UNDERLYING = {}
        for fam in SHEET_FAMILIES:
            prefix = fam["prefix"]
            under  = fam.get("underlying_terrain", "G")
            _SHEET_UNDERLYING[prefix] = under
    return _SHEET_UNDERLYING


def _parse_terrain_cell(cell: str) -> tuple[str, str]:
    """
    Interpreta uma célula do terrain CSV unificado.
    Retorna (terrain_char, visual_id).
    - terrain_char: char usado para colisão/gameplay (G, #, W...)
    - visual_id: ID de sprite de sheet para override visual ("" se não houver)
    """
    from engine.tileset import SHEET_TILE_MAP
    if cell in SHEET_TILE_MAP:
        # É um sprite de terreno: extrai o char de gameplay do prefixo
        sheet_under = _get_sheet_underlying()
        for prefix, under in sheet_under.items():
            if cell.startswith(prefix + "_"):
                return under, cell
        return "G", cell   # fallback
    if len(cell) > 1:
        # Token desconhecido (sheet removida do catálogo, nunca
        # cadastrada, erro de digitação no editor, etc.) — NUNCA devolver
        # como terrain_char: `terrain_matrix` é sempre 1 char por tile
        # ("".join(terrain_row) em `_load_two_layer`/`_load_single_layer`)
        # — um token de N caracteres aqui infla a linha inteira em N-1
        # posições, desalinhando todo tile seguinte na mesma linha (tanto
        # colisão/andabilidade quanto o que o minimapa desenha). Bug real
        # (10/09/2026): battleground tinha 258 células "txg_*" — sheet
        # nunca registrada em SHEET_TILE_MAP — inflando
        # `map_width_tiles` de 100 pra até 208 e corrompendo a leitura de
        # toda coluna depois de cada ocorrência naquela linha.
        return "G", ""
    return cell, ""


# Cache: filepath resolvido → (terrain_matrix, object_matrix, spawn_points,
# terrain_visual) já parseados — débito de performance achado por profiling
# real (11/08/2026, ver PROBLEMAS_ARQUITETURA.md §27): _parse_terrain_cell
# sozinho respondia por ~84% do tempo de construção de um WorldServer
# (~330ms de ~396ms), chamado ~63 mil vezes POR MAPA — cada
# WorldServer()/_load_map_for reparseava os mesmos 3 CSVs (conteúdo
# ESTÁTICO em runtime, nunca muda) do zero. `load_map_csv` sempre devolve
# uma CÓPIA independente do que está em cache (nunca o objeto cacheado
# direto) — os chamadores mutam essas estruturas livremente depois
# (spawn_points vira insumo de _create_harvestables_for_map/etc.), então
# compartilhar o MESMO objeto entre múltiplas instâncias de WorldServer
# contaminaria uma instância com mutação de outra (ex: harvestable
# reabastecido numa instância "vazando" pra outra). Nunca invalidado em
# runtime — arquivo de mapa não muda durante a vida do processo; reiniciar
# o processo já reseta o cache (dict de módulo).
_MAP_CSV_CACHE: "dict[str, tuple]" = {}


def _copy_matrix(matrix):
    """Copia uma matrix list[list[str]] (linhas mutáveis) — strings são
    imutáveis, só a lista OUTER e cada linha INNER precisam de cópia
    própria pra isolar de mutação entre chamadores."""
    if matrix is None:
        return None
    return [row[:] if isinstance(row, list) else row for row in matrix]


def load_map_csv(filepath: str) -> tuple[list[str], list[str], dict, list | None]:
    """
    Carrega um mapa e retorna (terrain_matrix, object_matrix, spawn_points, terrain_visual).

    terrain_visual — list[list[str]] com sprite IDs de sheet por tile.

    Cacheado por filepath resolvido (ver `_MAP_CSV_CACHE` acima) — sempre
    retorna uma cópia independente, nunca o objeto cacheado.
    """
    filepath = resource_path(filepath)
    cached = _MAP_CSV_CACHE.get(filepath)
    if cached is not None:
        terrain_matrix, object_matrix, spawn_points, terrain_visual = cached
        import copy as _copy_map
        return (list(terrain_matrix), _copy_matrix(object_matrix),
               _copy_map.deepcopy(spawn_points), _copy_matrix(terrain_visual))

    base      = os.path.splitext(filepath)[0]
    t_path    = base + "_terrain.csv"
    o_path    = base + "_objects.csv"

    if os.path.exists(t_path) and os.path.exists(o_path):
        terrain_matrix, object_matrix, terrain_visual, player_spawn, enemy_spawns, \
            portal_spawns, merchant_spawns = _load_two_layer(t_path, o_path)
    elif os.path.exists(filepath):
        terrain_matrix, object_matrix, terrain_visual, player_spawn, enemy_spawns, \
            portal_spawns, merchant_spawns = _load_single_layer(filepath)
    else:
        raise FileNotFoundError(f"Mapa não encontrado: {filepath}")

    spawn_points = {
        "player":            player_spawn,
        "enemies":           enemy_spawns,
        "portals":           portal_spawns,
        "merchants":         merchant_spawns,
        "quest_givers":      [],
        "blacksmiths":       [],
        "trainers":          [],
        "mailboxes":         [],
        "spawn_zones":       [],
        "transitions":       [],
        "portal_gates":      [],
        "ambient_zones":     [],
        "pvp_zones":         [],
        "bush_zones":        [],
        "default_ambient":   "",
        "day_night_affected": True,
        "training_dummies":  [],
        "combat_npcs":       [],
        "towers":            [],
        "jungle_mobs":       [],
        "jungle_bosses":     [],
        "minion_lanes":      [],
    }

    json_path = base + "_entities.json"
    if os.path.exists(json_path):
        _merge_entities_json(json_path, spawn_points)

    _MAP_CSV_CACHE[filepath] = (terrain_matrix, object_matrix, spawn_points, terrain_visual)
    return terrain_matrix, object_matrix, spawn_points, terrain_visual


def _load_two_layer(terrain_path: str, objects_path: str):
    """Carrega terrain e objects de dois CSVs separados (formato unificado)."""
    terrain_matrix  = []
    terrain_visual  = []
    player_spawn    = None
    enemy_spawns    = []
    portal_spawns   = []
    merchant_spawns = []

    with open(terrain_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row or all(c.strip() == "" for c in row):
                continue
            terrain_row = []
            visual_row  = []
            for col_idx, cell in enumerate(row):
                raw = cell.strip() or "."
                if raw == "P":
                    player_spawn = (col_idx, row_idx)
                    terrain_row.append(".")
                    visual_row.append("")
                elif raw == "O":
                    portal_spawns.append((col_idx, row_idx))
                    terrain_row.append("O")
                    visual_row.append("")
                elif raw in MERCHANT_TILE_CHARS:
                    merchant_spawns.append((col_idx, row_idx, MERCHANT_TILE_CHARS[raw]))
                    terrain_row.append(".")
                    visual_row.append("")
                elif raw in ENEMY_TILE_CHARS:
                    enemy_type, enemy_tier = ENEMY_TILE_CHARS[raw]
                    enemy_spawns.append((col_idx, row_idx, enemy_type, enemy_tier))
                    terrain_row.append("G")
                    visual_row.append("")
                else:
                    tc, vis = _parse_terrain_cell(raw)
                    terrain_row.append(tc)
                    visual_row.append(vis)
            terrain_matrix.append("".join(terrain_row))
            terrain_visual.append(visual_row)

    object_matrix = []
    with open(objects_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or all(c.strip() == "" for c in row):
                continue
            object_matrix.append([
                (c.strip() if c.strip() in OBJECT_CHARS else ".") for c in row
            ])

    while len(object_matrix) < len(terrain_matrix):
        w = len(terrain_matrix[len(object_matrix)]) if terrain_matrix else 0
        object_matrix.append(["."] * w)

    return terrain_matrix, object_matrix, terrain_visual, player_spawn, \
           enemy_spawns, portal_spawns, merchant_spawns


def _load_single_layer(filepath: str):
    """Carrega mapa legado de um único CSV."""
    terrain_matrix  = []
    terrain_visual  = []
    object_matrix   = []
    player_spawn    = None
    enemy_spawns    = []
    portal_spawns   = []
    merchant_spawns = []

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            terrain_row = []
            visual_row  = []
            object_row  = []
            for col_idx, cell in enumerate(row):
                raw = cell.strip() or "."
                if raw == "P":
                    player_spawn = (col_idx, row_idx)
                    terrain_row.append(".")
                    visual_row.append("")
                    object_row.append(".")
                elif raw == "O":
                    portal_spawns.append((col_idx, row_idx))
                    terrain_row.append("O")
                    visual_row.append("")
                    object_row.append(".")
                elif raw in MERCHANT_TILE_CHARS:
                    merchant_spawns.append((col_idx, row_idx, MERCHANT_TILE_CHARS[raw]))
                    terrain_row.append(".")
                    visual_row.append("")
                    object_row.append(".")
                elif raw in ENEMY_TILE_CHARS:
                    enemy_type, enemy_tier = ENEMY_TILE_CHARS[raw]
                    enemy_spawns.append((col_idx, row_idx, enemy_type, enemy_tier))
                    terrain_row.append("G")
                    visual_row.append("")
                    object_row.append(".")
                elif raw in OBJECT_CHARS:
                    terrain_row.append(OBJECT_UNDERLYING.get(raw, "G"))
                    visual_row.append("")
                    object_row.append(raw)
                else:
                    tc, vis = _parse_terrain_cell(raw)
                    terrain_row.append(tc)
                    visual_row.append(vis)
                    object_row.append(".")
            terrain_matrix.append("".join(terrain_row))
            terrain_visual.append(visual_row)
            object_matrix.append(object_row)

    return terrain_matrix, object_matrix, terrain_visual, player_spawn, \
           enemy_spawns, portal_spawns, merchant_spawns


def _merge_entities_json(json_path: str, spawn_points: dict) -> None:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    if "player" in data:
        p = data["player"]
        spawn_points["player"] = (p["x"], p["y"])
        spawn_points["enemies"] = [e for e in spawn_points["enemies"]]

    if "merchants" in data:
        spawn_points["merchants"] = [
            (m["x"], m["y"],
             m.get("name", "Comerciante"),        # nome customizado do JSON
             m.get("shop_id", "general"),
             m.get("level", 1), m.get("profession", "Comerciante"),
             # quest_ids/turn_in_ids (06/10/2026) — mercador também pode
             # aceitar entrega de quest (ex: "Fabian Hardek"); unlock_quest_id
             # — trava genérica de interação, ver engine.components.NPCUnlock.
             tuple(m.get("quest_ids", [])),
             tuple(m.get("turn_in_ids", [])),
             m.get("unlock_quest_id", ""))
            for m in data["merchants"]
        ]
    else:
        spawn_points["merchants"] = [
            (col, row, "Comerciante", sid, 1, "Comerciante", (), (), "")
            for col, row, sid in spawn_points["merchants"]
        ]

    if "quest_givers" in data:
        spawn_points["quest_givers"] = [
            (q["x"], q["y"],
             q.get("name", "Missiveiro"),
             tuple(q.get("quest_ids", [])),
             tuple(q.get("turn_in_ids", [])),
             q.get("level", 1),
             q.get("profession", "Missiveiro"),
             q.get("unlock_quest_id", ""))
            for q in data["quest_givers"]
        ]

    if "blacksmiths" in data:
        spawn_points["blacksmiths"] = [
            (b["x"], b["y"],
             b.get("name", "Ferreiro"),
             b.get("shop_id", "blacksmith"),
             b.get("level", 1),
             b.get("profession", "Ferreiro"),
             # quest_ids/turn_in_ids (07/10/2026) — qualquer NPC pode dar/
             # aceitar quest, ver engine.entity_factory.create_blacksmith.
             tuple(b.get("quest_ids", [])),
             tuple(b.get("turn_in_ids", [])),
             b.get("unlock_quest_id", ""))
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
             t.get("profession", "Treinador"),
             # Trava de quest (04/10/2026) — "" = sem trava, comportamento
             # de sempre. Ver content/merchant_data.py (mesma ideia pra
             # loja) e engine.quest_logic.has_unlocked.
             t.get("unlock_quest_id", ""))
            for t in data["trainers"]
        ]

    if "mailboxes" in data:
        spawn_points["mailboxes"] = [
            (mb["x"], mb["y"],
             mb.get("name", "Correio"),
             mb.get("level", 1),
             mb.get("profession", "Correio"),
             # quest_ids/turn_in_ids (07/10/2026) — qualquer NPC pode dar/
             # aceitar quest, ver engine.entity_factory.create_mailbox.
             tuple(mb.get("quest_ids", [])),
             tuple(mb.get("turn_in_ids", [])),
             mb.get("unlock_quest_id", ""))
            for mb in data["mailboxes"]
        ]

    if "combat_npcs" in data:
        # NPC de combate (guarda, etc — Sistema de Facções, Fase 4):
        # entidade plenamente sincronizada (Combatant/CombatStats/AIControlled),
        # não confundir com merchants/quest_givers/blacksmiths/trainers
        # acima (100% estáticos, sem HP, nunca sincronizados pelo servidor).
        spawn_points["combat_npcs"] = [
            {
                "x":            c["x"], "y": c["y"],
                "faction":      c.get("faction", "guardas_vila"),
                "name":         c.get("name", ""),
                "profession":   c.get("profession", "Guarda"),
                "race":         c.get("race", "Humanoide"),
                "entity_class": c.get("entity_class", ""),
                "level":        c.get("level", 1),
                "tier":         c.get("tier", "normal"),
                "is_ranged":    c.get("is_ranged", False),
            }
            for c in data["combat_npcs"]
        ]

    if "towers" in data:
        # Torre estática com facção (29/07/2026, pedido do usuário) —
        # mesmo padrão de "combat_npcs" (entidade real, sincronizada
        # pelo servidor), mas via create_tower() (server/world_server.py::
        # _create_towers). "tower_key" referencia content/tower_
        # definitions.py::TOWER_TABLE (tipo/atributos/sabor de ataque/
        # recompensa) — "faction"/"respawnable"/"respawn_s"/
        # "regen_enabled"/"level" são parâmetros de INSTÂNCIA (mesma
        # torre pode respawnar no mundo aberto e não respawnar numa
        # arena, por exemplo).
        spawn_points["towers"] = [
            {
                "x":              tw["x"], "y": tw["y"],
                "tower_key":      tw["tower_key"],
                "faction":        tw.get("faction", "monstros_hostis"),
                "level":          tw.get("level", 1),
                "respawnable":    tw.get("respawnable", False),
                "respawn_s":      tw.get("respawn_s", 0),
                "regen_enabled":  tw.get("regen_enabled", False),
                # Nexus (02/08/2026, pedido do usuário — battleground de
                # teste): destruir a torre principal termina a partida,
                # ver server/server_death_handler.py.
                "is_nexus":       tw.get("is_nexus", False),
            }
            for tw in data["towers"]
        ]

    if "jungle_mobs" in data:
        # Monstro de jungle estilo MOBA, normal — neutro aos 2 times,
        # só entra em combate se atacado (16/08/2026, pedido do
        # usuário — antes era "monstros_hostis"/tier "hostil", aggrava
        # por proximidade; ver content/faction_data.py::"monstros_jungle").
        # Mesmo padrão de "towers": "mob_key" referencia
        # content/jungle_definitions.py::JUNGLE_MOB_TABLE; "faction"/
        # "level"/"respawn_s" são parâmetros de INSTÂNCIA. Criado via
        # WorldServer._create_jungle_camps.
        spawn_points["jungle_mobs"] = [
            {
                "x":         jm["x"], "y": jm["y"],
                "mob_key":   jm["mob_key"],
                "faction":   jm.get("faction", "monstros_jungle"),
                "level":     jm.get("level", 1),
                "respawn_s": jm.get("respawn_s", 90.0),
            }
            for jm in data["jungle_mobs"]
        ]

    if "jungle_bosses" in data:
        # Boss de jungle estilo MOBA (13/08/2026, pedido do usuário;
        # facção "monstros_jungle" desde 16/08/2026, ver comentário
        # acima) — mesmo padrão de "jungle_mobs", "mob_key" referencia
        # content/jungle_definitions.py::JUNGLE_BOSS_TABLE (que também
        # carrega o ciclo de buff por abate, "buff_cycle").
        spawn_points["jungle_bosses"] = [
            {
                "x":         jb["x"], "y": jb["y"],
                "mob_key":   jb["mob_key"],
                "faction":   jb.get("faction", "monstros_jungle"),
                "level":     jb.get("level", 1),
                "respawn_s": jb.get("respawn_s", 300.0),
            }
            for jb in data["jungle_bosses"]
        ]

    if "minion_lanes" in data:
        # Lane de minion estilo MOBA (30/07/2026, pedido do usuário) —
        # mesmo padrão de "towers": "faction"/"spawn_tile"/"target_tile"/
        # "wave_interval_s"/"level" são parâmetros de INSTÂNCIA (a lane
        # em si, não o TIPO de minion — esse vem de content/minion_
        # definitions.py::MINION_TABLE). Registrado (não criado na hora)
        # por server/world_server.py::_create_minion_lanes — a criação
        # real dos minions é periódica, via _tick_minion_waves.
        # "lane_id" (30/07/2026, pedido do usuário — múltiplas rotas por
        # time, ex: top/mid/bot): distingue lanes da MESMA facção — sem
        # isso, 2 lanes do mesmo time colidiriam na mesma chave de wave
        # timer (server/world_server.py::_minion_wave_timers) e só a
        # primeira jamais dispararia. Default "default" — mapa com só
        # 1 lane por time (como a arena hoje) não precisa declarar nada.
        # "level" (config estática) só é usado como FALLBACK quando não
        # há player nenhum do time na instância pra calcular a média —
        # ver WorldServer._compute_team_avg_level.
        # "target_tile" (30/07/2026, pedido do usuário — lanes com curva,
        # ex: top/bot que não são retas) aceita 2 formatos: um par único
        # `[x,y]` (mid, reta — pathfind direto do spawn até lá) OU uma
        # LISTA de waypoints `[[x,y],[x,y],...]` (o minion passa por cada
        # um em ordem antes do último = base inimiga de verdade).
        # Normalizado AQUI pra sempre virar uma lista de tuplas — quem
        # consome (`WorldServer._tick_minion_waves`) nunca precisa saber
        # qual dos 2 formatos foi usado no JSON.
        spawn_points["minion_lanes"] = [
            {
                "faction":         ml.get("faction", "monstros_hostis"),
                "lane_id":         ml.get("lane_id", "default"),
                "spawn_tile":      tuple(ml["spawn_tile"]),
                "target_tile":     ([tuple(_wp) for _wp in ml["target_tile"]]
                                    if ml["target_tile"] and isinstance(ml["target_tile"][0], (list, tuple))
                                    else [tuple(ml["target_tile"])]),
                "wave_interval_s": ml.get("wave_interval_s", 45.0),
                "level":           ml.get("level", 1),
                # "first_wave_delay_s" (13/08/2026, pedido do usuário) —
                # atraso EXPLÍCITO da 1ª wave dessa lane, independente do
                # `wave_interval_s` (que passa a valer só a partir da 2ª
                # wave em diante). Opcional: None = cai no fallback antigo
                # (WorldServer._LANE_GROUP_STAGGER_S por lane_id) — mapas
                # que não declararem continuam com o comportamento de
                # sempre, sem precisar migrar nada.
                "first_wave_delay_s": (float(ml["first_wave_delay_s"])
                                       if "first_wave_delay_s" in ml else None),
            }
            for ml in data["minion_lanes"]
        ]

    if "harvestables" in data:
        # Item interativo de mapa (planta/pergaminho/ferramenta — Fase M1,
        # 25/07/2026, pedido do usuário) — abre o mesmo modal de loot de
        # um corpo de inimigo (server/world_server.py::
        # _create_harvestables_for_map). "items" usa o MESMO formato de
        # QuestReward.items ("item_key" ou ["item_key", stack] no JSON) —
        # listas viram tuple aqui porque JSON não tem tupla, e
        # engine.quest_logic.normalize_reward_entry só reconhece tuple.
        spawn_points["harvestables"] = [
            {
                "x":     hv["x"], "y": hv["y"],
                "name":  hv.get("name", "Objeto"),
                "items": [tuple(e) if isinstance(e, list) else e
                          for e in hv.get("items", [])],
                "coins": hv.get("coins", 0),
                # ID do catálogo de sprites de objeto de mapa (engine/tileset.py,
                # ex: "pr_box1") — vai pro Renderable.sprite_id da entidade
                # real (Fase M1, revisão 2). "" = sem sprite catalogado, cai
                # no retângulo cinza padrão de Renderable.
                "sprite": hv.get("sprite", ""),
                # Fase M2 (25/07/2026): reabastece sozinho depois de
                # esvaziar por completo. 0/ausente = nunca (M1 original).
                "respawn_s": hv.get("respawn_s", 0),
                # Fase M3 (25/07/2026): "" = sem trava, visível pra todo
                # mundo. Setado = totalmente invisível pra quem não tem
                # a quest ativa (mesmo princípio de class_req).
                "requires_quest": hv.get("requires_quest", ""),
            }
            for hv in data["harvestables"]
        ]

    if "harvestable_zones" in data:
        # Zona de itens (mistura de sub-tipos, mesmo padrão de
        # "spawn_zones" de mob) — nascimento/reposicionamento dos nós em
        # si é responsabilidade de server/world_server.py::
        # _tick_harvestable_zones (não achatamos aqui como "spawn_zones"
        # porque cada zona precisa continuar como UMA unidade, com sua
        # própria lista de sub-tipos, pra sortear entre eles ao repor um
        # slot vago).
        spawn_points["harvestable_zones"] = [
            {
                "x":                z["x"], "y": z["y"],
                "radius":           z.get("radius", 10),
                "respawn_cooldown": float(z.get("respawn_cooldown", 60.0)),
                "requires_quest":   z.get("requires_quest", ""),
                "spawns": [
                    {
                        "name":   sp.get("name", "Objeto"),
                        "sprite": sp.get("sprite", ""),
                        "items":  [tuple(e) if isinstance(e, list) else e
                                  for e in sp.get("items", [])],
                        "coins":  sp.get("coins", 0),
                        "count":  sp.get("count", 1),
                    }
                    for sp in z.get("spawns", [])
                ],
            }
            for z in data["harvestable_zones"]
        ]

    if "transitions" in data:
        spawn_points["transitions"] = data["transitions"]

    if "portal_gates" in data:
        spawn_points["portal_gates"] = data["portal_gates"]

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
                    "faction":          sp.get("faction", zone.get("faction", "monstros_hostis")),
                    # Nome exibido customizado (08/10/2026, ver CLAUDE.md) —
                    # "" (default) = mostra a raça, igual sempre. Mesmo
                    # fallback por-spawn/por-zona de race/class acima.
                    "name":             sp.get("name", zone.get("name", "")),
                    # Kit de animação customizado (13/10/2026, ver CLAUDE.md)
                    # — "" (default) = usa o sprite genérico da raça. Mesmo
                    # fallback por-spawn/por-zona de name/race/class acima.
                    "spritesheet":      sp.get("spritesheet", zone.get("spritesheet", "")),
                    # Dungeon instanciada (07/10/2026, ver CLAUDE.md) — zona
                    # sem respawn + loot de boss específico deste mapa.
                    "respawnable":      zone.get("respawnable", True),
                    "loot":             zone.get("loot"),
                    "gold_min":         zone.get("gold_min", 0),
                    "gold_max":         zone.get("gold_max", 0),
                })
        if spawn_points["spawn_zones"]:
            spawn_points["enemies"] = []

    if "training_dummies" in data:
        spawn_points["training_dummies"] = [
            # reflect_damage (07/10/2026) — 0 = boneco normal. sprite
            # (07/10/2026) — "vp_184" = aparência de sempre. ranged_only
            # (07/10/2026) — False = aceita qualquer tipo de dano. Ver
            # engine.components.TrainingDummy pro racional completo.
            (d["x"], d["y"], d.get("reflect_damage", 0),
             d.get("sprite", "vp_184"), d.get("ranged_only", False))
            for d in data["training_dummies"]
        ]

    if "default_ambient" in data:
        spawn_points["default_ambient"] = data["default_ambient"]

    if "day_night_affected" in data:
        # Mapa fechado (caverna/dungeon) nunca recebe luz do sol — cliente
        # (ui/day_night_system.py::DayNightSystem/ui/day_night_clock_hud.py)
        # lê essa flag pra pular o tingimento ambiente/luz de lampião/HUD
        # do relógio inteiro nesses mapas, em vez de escurecer um lugar que
        # já não tem noção de "hora do dia". Ausente = True (mapa aberto,
        # comportamento de sempre) — só precisa marcar False nos fechados.
        spawn_points["day_night_affected"] = bool(data["day_night_affected"])

    if "ambient_zones" in data:
        for z in data["ambient_zones"]:
            r = z.get("rect", [0, 0, 0, 0])
            music_raw = z.get("music", [])
            if isinstance(music_raw, str):
                music_raw = [music_raw]
            ambient_raw = z.get("ambient", [])
            if isinstance(ambient_raw, str):
                ambient_raw = [ambient_raw] if ambient_raw else []
            spawn_points["ambient_zones"].append({
                "name":    z.get("name", ""),
                "ambient": ambient_raw,
                "music":   music_raw,
                "rect":    (int(r[0]), int(r[1]), int(r[2]), int(r[3])),
            })

    if "pvp_zones" in data:
        for z in data["pvp_zones"]:
            r = z.get("rect", [0, 0, 0, 0])
            spawn_points["pvp_zones"].append({
                "name": z.get("name", ""),
                "rect": (int(r[0]), int(r[1]), int(r[2]), int(r[3])),
            })

    if "bush_zones" in data:
        for z in data["bush_zones"]:
            r = z.get("rect", [0, 0, 0, 0])
            spawn_points["bush_zones"].append({
                "name": z.get("name", ""),
                "rect": (int(r[0]), int(r[1]), int(r[2]), int(r[3])),
            })


def validate_map(tile_matrix: list[str]) -> list[str]:
    """Antes (até 02/09/2026) comparava toda linha contra a largura da
    LINHA 0 — falso positivo pra mapa de forma ORGÂNICA/irregular de
    propósito (map_1: toda linha começa/termina com "m", borda natural,
    só a extensão real do terreno varia por altura), gerando 600+ avisos
    no console pra um mapa 100% correto. `engine/entity_factory.py::
    create_tilemap` já usa a MAIOR linha como `map_width_tiles` e já
    preenche linha mais curta com chão genérico de propósito — linha mais
    curta que a máxima NÃO é mais erro, é a borda natural de um mapa
    não-retangular. Removida a checagem de largura (não há mais uma
    "largura esperada" única que faça sentido pra qualquer mapa); função
    mantida (lista vazia = mapa não-vazio, assinatura intacta pros
    chamadores) pra checagens futuras que façam sentido pra qualquer
    formato de mapa."""
    warnings = []
    if not tile_matrix:
        warnings.append("Mapa vazio.")
    return warnings
