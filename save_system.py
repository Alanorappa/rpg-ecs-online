"""
save_system.py — Serialização e desserialização do estado do jogador.

Formato JSON plano — projetado para migração futura para banco de dados.
Cada chave do JSON corresponde a uma "tabela" ou campo de registro.

Arquivo de save: saves/save.json
Escrita atômica: escreve em .tmp e renomeia para evitar corrupção.
"""
from __future__ import annotations

import json
import os
import datetime

from components import (
    CharacterStats, PermanentStats, TalentTree,
    Inventory, Equipment, Wallet, TileMovement, CombatStats,
    Item, Modifier, FogOfWar,
)

SAVE_DIR     = "saves"
SAVE_FILE    = os.path.join(SAVE_DIR, "save.json")
SAVE_VERSION = 1


# ---------------------------------------------------------------------------
# Serialização de Item
# ---------------------------------------------------------------------------

def _item_to_dict(item: Item) -> dict:
    return {
        "name":         item.name,
        "item_type":    item.item_type,
        "slot":         item.slot,
        "rarity":       item.rarity,
        "value":        item.value,
        "two_handed":   item.two_handed,
        "damage_min":   item.damage_min,
        "damage_max":   item.damage_max,
        "attack_speed": item.attack_speed,
        "subtype":      item.subtype,
        "stack":        item.stack,
        "max_stack":    item.max_stack,
        "modifiers":    [
            {"attribute": m.attribute, "value": m.value, "type": m.type}
            for m in item.modifiers
        ],
        "proc":         item.proc,
        "consumable":   item.consumable,
    }


def _dict_to_item(d: dict) -> Item:
    modifiers = [
        Modifier(m["attribute"], m["value"], m["type"])
        for m in d.get("modifiers", [])
    ]
    item = Item(
        name         = d["name"],
        item_type    = d["item_type"],
        slot         = d["slot"],
        rarity       = d.get("rarity", "common"),
        value        = d.get("value", 0),
        two_handed   = d.get("two_handed", False),
        damage_min   = d.get("damage_min", 0),
        damage_max   = d.get("damage_max", 0),
        attack_speed = d.get("attack_speed", 0.0),
        subtype      = d.get("subtype", ""),
        modifiers    = modifiers,
        proc         = d.get("proc"),
        consumable   = d.get("consumable"),
        max_stack    = d.get("max_stack", 1),
    )
    item.stack = d.get("stack", 1)
    return item


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_game(world, player_entity: int, current_map_file: str) -> None:
    """Serializa o estado completo do jogador em saves/save.json."""
    char  = world.get_component(player_entity, CharacterStats)
    perm  = world.get_component(player_entity, PermanentStats)
    tt    = world.get_component(player_entity, TalentTree)
    inv   = world.get_component(player_entity, Inventory)
    equip = world.get_component(player_entity, Equipment)
    wlt   = world.get_component(player_entity, Wallet)
    tm    = world.get_component(player_entity, TileMovement)
    cs    = world.get_component(player_entity, CombatStats)
    fog   = world.get_component(player_entity, FogOfWar)

    if not char:
        return

    data = {
        "version":  SAVE_VERSION,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),

        # ── Progressão do personagem ──────────────────────────────────────
        "character": {
            "level":            char.level,
            "current_xp":       char.current_xp,
            "xp_to_next_level": char.xp_to_next_level,
            "strength":         char.strength,
            "intelligence":     char.intelligence,
            "agility":          char.agility,
            "vitality":         char.vitality,
            "defense":          char.defense,
            "spawn_tile_x":     char.spawn_tile_x,
            "spawn_tile_y":     char.spawn_tile_y,
            "spawn_map":        char.spawn_map,
            "current_hp":       int(cs.current_hp) if cs else None,
            "max_hp":           int(cs.max_hp)     if cs else None,
            "permanent_stats": {
                "strength":     perm.strength     if perm else 0,
                "intelligence": perm.intelligence if perm else 0,
                "agility":      perm.agility      if perm else 0,
                "vitality":     perm.vitality     if perm else 0,
                "defense":      perm.defense      if perm else 0,
            },
        },

        # ── Talentos ─────────────────────────────────────────────────────
        "talents": {
            "build":            tt.chosen_build     if tt else "cavaleiro",
            "allocated":        dict(tt.allocated)  if tt else {},
            "available_points": tt.available_points if tt else 0,
        },

        # ── Inventário e equipamentos ─────────────────────────────────────
        "inventory": [
            _item_to_dict(it) for it in (inv.items if inv else [])
        ],
        "equipment": {
            slot: (_item_to_dict(item) if item else None)
            for slot, item in (equip.slots if equip else {}).items()
        },

        # ── Economia ─────────────────────────────────────────────────────
        "wallet": {"gold": wlt.gold if wlt else 0},

        # ── Posição no mundo ─────────────────────────────────────────────
        "position": {
            "map":    current_map_file,
            "tile_x": tm.current_tile_x if tm else 1,
            "tile_y": tm.current_tile_y if tm else 1,
        },

        # ── Fog of War — tiles explorados ────────────────────────────────
        "fog": {
            "explored": [[x, y] for x, y in fog.explored] if fog else [],
        },

        # ── Quests ────────────────────────────────────────────────────────
        "quests": _quests_to_dict(world, player_entity),
    }

    os.makedirs(SAVE_DIR, exist_ok=True)
    tmp = SAVE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SAVE_FILE)  # escrita atômica — evita arquivo corrompido


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_game(world, player_entity: int) -> dict | None:
    """
    Lê saves/save.json e aplica os dados ao player_entity já existente no world.

    Retorna {"map": str, "tile_x": int, "tile_y": int} para o GameEngine
    reposicionar o jogador e carregar o mapa correto.
    Retorna None se não há save ou a versão for incompatível.

    Nota: talentos são carregados aqui apenas como dados (tt.allocated).
    O chamador deve invocar talent_system.apply_talent_effects() e
    apply_char_stats_to_combat() depois para recalcular os atributos.
    """
    if not os.path.exists(SAVE_FILE):
        return None

    with open(SAVE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("version", 0) != SAVE_VERSION:
        print(f"[save] versão incompatível ({data.get('version')} ≠ {SAVE_VERSION}) — ignorado")
        return None

    # ── Personagem ─────────────────────────────────────────────────────────
    c    = data.get("character", {})
    char = world.get_component(player_entity, CharacterStats)
    if char:
        char.level            = c.get("level", 1)
        char.current_xp       = c.get("current_xp", 0)
        char.xp_to_next_level = c.get("xp_to_next_level", CharacterStats.BASE_XP)
        char.strength         = c.get("strength", 1)
        char.intelligence     = c.get("intelligence", 1)
        char.agility          = c.get("agility", 1)
        char.vitality         = c.get("vitality", 3)
        char.defense          = c.get("defense", 2)
        char.spawn_tile_x = c.get("spawn_tile_x", 1)
        char.spawn_tile_y = c.get("spawn_tile_y", 1)
        char.spawn_map    = c.get("spawn_map", "maps/map_1.csv")

    perm = world.get_component(player_entity, PermanentStats)
    if perm:
        ps = c.get("permanent_stats", {})
        perm.strength     = ps.get("strength", 0)
        perm.intelligence = ps.get("intelligence", 0)
        perm.agility      = ps.get("agility", 0)
        perm.vitality     = ps.get("vitality", 0)
        perm.defense      = ps.get("defense", 0)

    # ── Talentos (dados apenas — apply_talent_effects() pelo chamador) ────
    t  = data.get("talents", {})
    tt = world.get_component(player_entity, TalentTree)
    if tt:
        tt.chosen_build      = t.get("build", "cavaleiro")
        tt.allocated         = t.get("allocated", {})
        tt.available_points  = t.get("available_points", 0)
        tt._applied_modifiers   = []
        tt._unlocked_skill_ids  = set()

    # ── Inventário ─────────────────────────────────────────────────────────
    inv = world.get_component(player_entity, Inventory)
    if inv:
        inv.items = [_dict_to_item(d) for d in data.get("inventory", [])]

    # ── Equipamentos ───────────────────────────────────────────────────────
    equip = world.get_component(player_entity, Equipment)
    if equip:
        for slot, item_dict in data.get("equipment", {}).items():
            if slot in equip.slots:
                equip.slots[slot] = _dict_to_item(item_dict) if item_dict else None

    # ── Carteira ───────────────────────────────────────────────────────────
    wlt = world.get_component(player_entity, Wallet)
    if wlt:
        wlt.gold = data.get("wallet", {}).get("gold", 0)

    # ── HP (restaurado após apply_char_stats_to_combat pelo chamador) ─────
    cs = world.get_component(player_entity, CombatStats)
    if cs:
        saved_hp  = c.get("current_hp")
        saved_max = c.get("max_hp")
        if saved_hp is not None and saved_max is not None:
            cs._saved_hp  = saved_hp   # armazena para restaurar após recálculo
            cs._saved_max = saved_max

    # ── Fog of War ─────────────────────────────────────────────────────────
    fog = world.get_component(player_entity, FogOfWar)
    if fog:
        fog.explored = {(x, y) for x, y in data.get("fog", {}).get("explored", [])}

    # ── Quests ─────────────────────────────────────────────────────────────
    _quests_from_dict(world, player_entity, data.get("quests", {}))

    pos = data.get("position", {})
    return {
        "map":    pos.get("map", ""),
        "tile_x": pos.get("tile_x", 1),
        "tile_y": pos.get("tile_y", 1),
    }


# ---------------------------------------------------------------------------
# Quests — serialização / desserialização
# ---------------------------------------------------------------------------

def _quests_to_dict(world, player_entity: int) -> dict:
    from components import QuestLog
    ql = world.get_component(player_entity, QuestLog)
    if ql is None:
        return {}
    return {
        "active":    {qid: prog for qid, prog in ql.active.items()},
        "completed": list(ql.completed),
    }


def _quests_from_dict(world, player_entity: int, data: dict) -> None:
    from components import QuestLog
    ql = world.get_component(player_entity, QuestLog)
    if ql is None:
        return
    ql.active    = {qid: prog for qid, prog in data.get("active", {}).items()}
    ql.completed = set(data.get("completed", []))


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def has_save() -> bool:
    """Retorna True se existe um arquivo de save."""
    return os.path.exists(SAVE_FILE)


def delete_save() -> None:
    """Remove o arquivo de save (usado em New Game)."""
    if os.path.exists(SAVE_FILE):
        os.remove(SAVE_FILE)


# ---------------------------------------------------------------------------
# Autosave signal — desacopla sistemas do GameEngine
# ---------------------------------------------------------------------------
# Sistemas chamam request_autosave() sem conhecer o GameEngine.
# GameEngine registra seu callback uma única vez via register_autosave().

_autosave_listeners: list = []


def register_autosave(callback) -> None:
    """Registra um callable a ser invocado quando qualquer sistema solicitar autosave."""
    if callback not in _autosave_listeners:
        _autosave_listeners.append(callback)


def request_autosave() -> None:
    """Solicita autosave. Invoca todos os listeners registrados."""
    for cb in _autosave_listeners:
        cb()
