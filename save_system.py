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
import threading
import queue

from components import (
    CharacterStats, PermanentStats, TalentTree,
    Inventory, Equipment, Wallet, TileMovement, CombatStats,
    Item, Modifier, FogOfWar, LearnedRecipes,
)

SAVE_DIR     = "saves"
SAVE_FILE    = os.path.join(SAVE_DIR, "save.json")   # legado — slot único
SAVE_VERSION = 1
MAX_SLOTS    = 8


def _slot_file(slot: int) -> str:
    return os.path.join(SAVE_DIR, f"slot_{slot}.json")


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
        "arrow_count":  item.arrow_count,
        "max_arrows":   item.max_arrows,
        "cast_range":   item.cast_range,
        "item_level":         item.item_level,
        "level_requirement":  item.level_requirement,
        "description":        item.description,
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
        arrow_count  = d.get("arrow_count", 0),
        max_arrows   = d.get("max_arrows", 0),
        cast_range   = d.get("cast_range", 0),
        item_level        = d.get("item_level", 1),
        level_requirement = d.get("level_requirement", 1),
        description       = d.get("description", ""),
    )
    item.stack = d.get("stack", 1)
    return item


# ---------------------------------------------------------------------------
# Save — worker thread persistente (evita overhead de criação no Windows)
# ---------------------------------------------------------------------------

_save_queue: queue.Queue = queue.Queue(maxsize=1)  # descarta saves em fila se ocupada


def _save_worker() -> None:
    """Thread persistente — aguarda itens na fila e grava em disco."""
    while True:
        item = _save_queue.get()
        if item is None:   # sentinel de shutdown
            _save_queue.task_done()
            break
        data, slot = item
        _write_save(data, slot)
        _save_queue.task_done()


_save_worker_thread = threading.Thread(target=_save_worker, daemon=True)
_save_worker_thread.start()


def flush() -> None:
    """Aguarda todos os saves pendentes terminarem. Chamar antes de encerrar o processo."""
    _save_queue.join()


_last_save_time: float = 0.0
_MIN_SAVE_INTERVAL: float = 2.0  # segundos mínimos entre saves automáticos


def save_game(world, player_entity: int, current_map_file: str, slot: int = 0) -> None:
    """Serializa o estado do jogador. Build do dict na thread principal; I/O em background."""
    global _last_save_time

    # Sai cedo se a fila estiver cheia (worker ainda ocupado) ou intervalo mínimo não atingido.
    import time as _t
    now = _t.monotonic()
    if _save_queue.full() or (now - _last_save_time < _MIN_SAVE_INTERVAL and _last_save_time > 0):
        return
    _last_save_time = now

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

    # Build the dict on the main thread — pure Python, no I/O, fast snapshot.
    data = {
        "version":  SAVE_VERSION,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),

        # ── Progressão do personagem ──────────────────────────────────────
        "character": {
            "name":             char.name,
            "class_id":         char.class_id,
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

        # ── Fog of War — tiles explorados por mapa ───────────────────────
        "fog": {
            map_key.replace("\\", "/"): [[x, y] for x, y in tiles]
            for map_key, tiles in (fog._explored_maps.items() if fog else {}.items())
        },

        # ── Quests ────────────────────────────────────────────────────────
        "quests": _quests_to_dict(world, player_entity),

        # ── Receitas aprendidas ───────────────────────────────────────────
        "learned_recipes": world.get_component(player_entity, LearnedRecipes).known
                           if world.get_component(player_entity, LearnedRecipes) else [],

        # ── Skills aprendidas com treinador ───────────────────────────────
        "learned_skills": _save_learned_skills(world, player_entity),
    }

    _save_queue.put_nowait((data, slot))


def _write_save(data: dict, slot: int) -> None:
    """Runs in a background thread: JSON encode + atomic disk write."""
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        target = _slot_file(slot)
        tmp    = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception as exc:
        print(f"[save] erro ao salvar: {exc}")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_game(world, player_entity: int, slot: int = 0) -> dict | None:
    """
    Lê saves/save.json e aplica os dados ao player_entity já existente no world.

    Retorna {"map": str, "tile_x": int, "tile_y": int} para o GameEngine
    reposicionar o jogador e carregar o mapa correto.
    Retorna None se não há save ou a versão for incompatível.

    Nota: talentos são carregados aqui apenas como dados (tt.allocated).
    O chamador deve invocar talent_system.apply_talent_effects() e
    apply_char_stats_to_combat() depois para recalcular os atributos.
    """
    target = _slot_file(slot)
    # Migração legada: se slot 0 pedido e não existe, tenta save.json antigo
    if not os.path.exists(target) and slot == 0 and os.path.exists(SAVE_FILE):
        target = SAVE_FILE

    if not os.path.exists(target):
        return None

    with open(target, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return None
    data = json.loads(content)

    if data.get("version", 0) != SAVE_VERSION:
        print(f"[save] versão incompatível ({data.get('version')} ≠ {SAVE_VERSION}) — ignorado")
        return None

    # ── Personagem ─────────────────────────────────────────────────────────
    c    = data.get("character", {})
    char = world.get_component(player_entity, CharacterStats)
    if char:
        char.name             = c.get("name", "Aventureiro")
        char.class_id         = c.get("class_id", "guerreiro")
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
        fog_data = data.get("fog", {})
        # Formato novo: {"maps/map_1.csv": [[x,y],...], ...}
        # Formato legado: {"explored": [[x,y],...]} — ignora silenciosamente
        # Normaliza separadores (Windows pode ter gravado "maps\\map_1.csv")
        for map_key, coords in fog_data.items():
            if map_key == "explored":
                continue  # entrada legada, descarta
            normalized = map_key.replace("\\", "/")
            tile_set = {(x, y) for x, y in coords}
            if normalized in fog._explored_maps:
                fog._explored_maps[normalized].update(tile_set)
            else:
                fog._explored_maps[normalized] = tile_set
        # Atualiza o ponteiro para o mapa atual (já definido via switch_map na transição)
        if fog._current_map in fog._explored_maps:
            fog.explored = fog._explored_maps[fog._current_map]

    # ── Quests ─────────────────────────────────────────────────────────────
    _quests_from_dict(world, player_entity, data.get("quests", {}))

    # ── Receitas aprendidas ────────────────────────────────────────────────
    lr = world.get_component(player_entity, LearnedRecipes)
    if lr:
        lr.known = list(data.get("learned_recipes", []))

    # ── Skills aprendidas com treinador ───────────────────────────────────
    _load_learned_skills(world, player_entity, data.get("learned_skills", []))

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


def _save_learned_skills(world, player_entity: int) -> list:
    from components import PlayerSkills
    ps = world.get_component(player_entity, PlayerSkills)
    if ps is None:
        return []
    # Salva em ordem de slot (None = slot vazio) para preservar posições na hotbar
    return [s.skill_id if s is not None else None for s in ps.skills]


def _load_learned_skills(world, player_entity: int, skill_ids: list) -> None:
    from components import PlayerSkills
    from skill_config import SKILL_CATALOG
    ps = world.get_component(player_entity, PlayerSkills)
    if ps is None:
        return

    # Formato novo: lista ordenada por slot (pode conter None)
    # Formato legado: lista de strings sem None
    is_slot_ordered = any(s is None for s in skill_ids)

    if is_slot_ordered:
        for slot_idx, sid in enumerate(skill_ids):
            if sid is None:
                continue
            if sid not in ps.learned_skill_ids:
                ps.learned_skill_ids.add(sid)
            if ps.skill_by_id(sid) is None:
                new_skill = ps._make_skill(sid, SKILL_CATALOG)
                if new_skill:
                    while len(ps.skills) <= slot_idx:
                        ps.skills.append(None)
                    if ps.skills[slot_idx] is None:
                        ps.skills[slot_idx] = new_skill
                    else:
                        try:
                            ps.skills[ps.skills.index(None)] = new_skill
                        except ValueError:
                            ps.skills.append(new_skill)
    else:
        # Compatibilidade com saves antigos: insere no primeiro slot vazio
        for sid in skill_ids:
            if sid in ps.learned_skill_ids:
                continue
            ps.learned_skill_ids.add(sid)
            if ps.skill_by_id(sid) is None:
                new_skill = ps._make_skill(sid, SKILL_CATALOG)
                if new_skill:
                    try:
                        ps.skills[ps.skills.index(None)] = new_skill
                    except ValueError:
                        ps.skills.append(new_skill)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def has_save(slot: int = 0) -> bool:
    """Retorna True se existe save no slot especificado (ou save.json legado no slot 0)."""
    if os.path.exists(_slot_file(slot)):
        return True
    if slot == 0 and os.path.exists(SAVE_FILE):
        return True
    return False


def delete_save(slot: int = 0) -> None:
    """Remove o arquivo de save do slot especificado."""
    for path in (_slot_file(slot), SAVE_FILE if slot == 0 else None):
        if path and os.path.exists(path):
            os.remove(path)


def list_saves() -> list:
    """
    Retorna lista de metadados de todos os slots preenchidos, ordenada por slot.
    Cada item: {"slot": int, "name": str, "class_id": str, "level": int, "saved_at": str}
    Inclui save.json legado como slot 0 se existir e slot_0.json não existir.
    """
    result = []
    seen_zero = False
    for slot in range(MAX_SLOTS):
        path = _slot_file(slot)
        # Migração legada para slot 0
        if slot == 0 and not os.path.exists(path) and os.path.exists(SAVE_FILE):
            path = SAVE_FILE
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            c = data.get("character", {})
            result.append({
                "slot":     slot,
                "name":     c.get("name", "Aventureiro"),
                "class_id": c.get("class_id", "guerreiro"),
                "level":    c.get("level", 1),
                "saved_at": data.get("saved_at", ""),
            })
        except Exception:
            pass  # arquivo corrompido — ignora
    return result


def next_free_slot() -> int:
    """Retorna o menor slot vazio disponível (0-7). Levanta ValueError se todos ocupados."""
    for slot in range(MAX_SLOTS):
        if not has_save(slot):
            return slot
    raise ValueError("Todos os slots de save estão ocupados.")


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
