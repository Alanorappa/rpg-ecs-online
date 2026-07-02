"""
quest_logic.py — Lógica pura de progresso de quest (sem Pygame).

Extraído de quest_system.py (QuestSystem) pra poder rodar tanto no
cliente (caminho offline/local, via quest_system.py) quanto no servidor
(server/world_server.py::_process_quest_events, único produtor
autoritativo no modo online — ver arquitetura/PROBLEMAS_ARQUITETURA.md).

Nenhuma função aqui toca LOG/PROC/SOUNDS/request_autosave — quem chama
decide a apresentação (cliente) ou os canais de rede (servidor).
"""
from __future__ import annotations
import random

from quests_data import QUESTS, QUEST_ITEMS, ObjectiveDef, QuestReward


def _skill_already_learned(learned_skill_ids, skill_id: str) -> bool:
    if skill_id == "*":
        return bool(learned_skill_ids)
    return skill_id in learned_skill_ids


def match_objective(event_type: str, data: dict, obj: ObjectiveDef) -> bool:
    """True se o evento (event_type, data) satisfaz o objetivo `obj`."""
    if event_type != obj.type:
        return False
    t = obj.target
    if obj.type == "kill":
        return t in ("*", data.get("name", ""), data.get("race", ""))
    if obj.type == "collect_item":
        return data.get("item_name", "") == obj.loot_item
    if obj.type == "reach_tile":
        loc = obj.location
        if not loc:
            return False
        if t and t != "*" and data.get("map", "") != t:
            return False
        tx, ty = data.get("tx", -1), data.get("ty", -1)
        if len(loc) == 2:
            return tx == loc[0] and ty == loc[1]
        if len(loc) == 4:
            return loc[0] <= tx <= loc[2] and loc[1] <= ty <= loc[3]
        return False
    if obj.type == "use_skill":
        if t not in ("*", data.get("skill_id", "")):
            return False
        if obj.params.get("on_dummy") and not data.get("on_dummy", False):
            return False
        return True
    if obj.type == "learn_skill":
        return t in ("*", data.get("skill_id", ""))
    if obj.type == "use_consumable":
        return t in ("*", data.get("item_name", ""))
    if obj.type == "reach_level":
        return data.get("level", 0) >= obj.count
    if obj.type == "talk_to_npc":
        return t in ("*", data.get("npc_name", ""))
    if obj.type == "equip_item":
        return t in ("*", data.get("item_name", ""), data.get("item_type", ""))
    if obj.type == "use_item_on_target":
        if data.get("item_name", "") != obj.params.get("item_name", ""):
            return False
        return t in ("*", data.get("target_name", ""), data.get("target_race", ""))
    return False


def apply_event(ql, event_type: str, data: dict) -> bool:
    """Aplica 1 evento a todos os objetivos ativos compatíveis em `ql`.
    Retorna True se algum progresso mudou."""
    changed = False
    for qid in list(ql.active.keys()):
        qdef = QUESTS.get(qid)
        if qdef is None:
            continue
        prog = ql.active[qid]
        for i, obj in enumerate(qdef.objectives):
            if prog[i] >= obj.count:
                continue
            if match_objective(event_type, data, obj):
                if obj.type == "reach_level":
                    prog[i] = obj.count   # nível-alvo, não cumulativo
                else:
                    prog[i] += 1
                changed = True
    return changed


def sync_collect_progress(ql, inventory) -> bool:
    """Sincroniza objetivos collect_item com o Inventory real (servidor:
    autoritativo; cliente offline: o mesmo, local). Retorna True se mudou."""
    if inventory is None:
        return False
    changed = False
    for qid, prog in ql.active.items():
        qdef = QUESTS.get(qid)
        if qdef is None:
            continue
        for i, obj in enumerate(qdef.objectives):
            if obj.type != "collect_item" or not obj.loot_item:
                continue
            owned = sum(item.stack for item in inventory.items if item.name == obj.loot_item)
            new_prog = min(owned, obj.count)
            if prog[i] != new_prog:
                prog[i] = new_prog
                changed = True
    return changed


def sync_learn_skill_progress(ql, learned_skill_ids) -> bool:
    """Sincroniza objetivos learn_skill direto contra o set de skills
    aprendidas (sem evento dedicado — ver PROBLEMAS_ARQUITETURA.md, gap
    pré-existente de trainer_system.py não ser server-validado)."""
    changed = False
    for qid, prog in ql.active.items():
        qdef = QUESTS.get(qid)
        if qdef is None:
            continue
        for i, obj in enumerate(qdef.objectives):
            if obj.type != "learn_skill" or prog[i] >= obj.count:
                continue
            if _skill_already_learned(learned_skill_ids, obj.target):
                prog[i] = obj.count
                changed = True
    return changed


def can_turn_in(ql, qid: str) -> bool:
    """True se a quest está ativa e todos os objetivos concluídos."""
    if qid not in ql.active:
        return False
    qdef = QUESTS.get(qid)
    if qdef is None:
        return False
    prog = ql.active[qid]
    return all(prog[i] >= obj.count for i, obj in enumerate(qdef.objectives))


def can_turn_in_after_talk(ql, qid: str, npc_name: str) -> bool:
    """True se a quest ficaria completável após talk_to_npc com npc_name.

    Espelha o comportamento do servidor (session.py aplica talk_to_npc antes
    de can_turn_in no handler de QUEST_TURN_IN). Usado pelo cliente online
    para abrir o modal de entrega mesmo quando o objetivo talk_to_npc ainda
    não foi confirmado localmente — sem mutar o QuestLog."""
    if qid not in ql.active:
        return False
    qdef = QUESTS.get(qid)
    if qdef is None:
        return False
    prog = ql.active[qid]
    for i, obj in enumerate(qdef.objectives):
        if prog[i] >= obj.count:
            continue
        if obj.type == "talk_to_npc" and obj.target in ("*", npc_name):
            continue
        return False
    return True


def try_start(world, player_eid: int, ql, qid: str) -> bool:
    """Tenta iniciar a quest `qid` pra `player_eid`. Valida pré-requisitos/
    nível usando dados do PRÓPRIO `world` (nunca confia em valor externo).
    Retorna True se iniciou."""
    if qid in ql.active or qid in ql.completed:
        return False
    qdef = QUESTS.get(qid)
    if qdef is None:
        return False
    if not all(r in ql.completed for r in qdef.requires):
        return False

    from components import CharacterStats, PlayerSkills
    char = world.get_component(player_eid, CharacterStats)
    plvl = char.level if char else 1
    if qdef.level_req > 0 and plvl < qdef.level_req:
        return False
    if qdef.class_req and (not char or char.class_id != qdef.class_req):
        return False

    ps = world.get_component(player_eid, PlayerSkills)
    learned = ps.learned_skill_ids if ps else set()

    prog = []
    for obj in qdef.objectives:
        if obj.type == "reach_level" and plvl >= obj.count:
            prog.append(obj.count)
        elif obj.type == "learn_skill" and _skill_already_learned(learned, obj.target):
            prog.append(obj.count)
        else:
            prog.append(0)
    ql.active[qid] = prog
    return True


def complete_quest(world, player_eid: int, ql, qid: str) -> "QuestReward | None":
    """Marca `qid` como completa em `ql`, remove itens de quest do
    Inventory de `player_eid` e desbloqueia quests auto_start dependentes.
    NÃO concede XP/gold nem toca LOG/PROC/request_autosave — quem chama
    decide como aplicar a recompensa (servidor: canais já existentes
    server-autoritativos; cliente offline: direto em CharacterStats/Wallet).
    Retorna a QuestReward (xp, gold) ou None se a quest não pôde ser
    completada (não está ativa / não existe)."""
    if qid not in ql.active:
        return None
    qdef = QUESTS.get(qid)
    if qdef is None:
        return None

    del ql.active[qid]
    if not qdef.repeatable:
        ql.completed.add(qid)

    from components import Inventory
    inv = world.get_component(player_eid, Inventory)
    if inv:
        for obj in qdef.objectives:
            if obj.type != "collect_item" or not obj.loot_item:
                continue
            needed = obj.count
            i = 0
            while i < len(inv.items) and needed > 0:
                item = inv.items[i]
                if item.name == obj.loot_item:
                    if item.stack <= needed:
                        needed -= item.stack
                        inv.items.pop(i)
                    else:
                        item.stack -= needed
                        needed = 0
                        i += 1
                else:
                    i += 1

    for qid2, qdef2 in QUESTS.items():
        if qid2 not in ql.active and qid2 not in ql.completed and qdef2.auto_start:
            try_start(world, player_eid, ql, qid2)

    return qdef.reward


def roll_conditional_loot(ql, enemy_name: str, enemy_race: str) -> list:
    """Drop bônus condicional pra objetivos collect_item ativos que aceitam
    esse mob como alvo. Retorna lista de Item (factories de QUEST_ITEMS)."""
    extras = []
    for qid, prog in ql.active.items():
        qdef = QUESTS.get(qid)
        if qdef is None:
            continue
        for i, obj in enumerate(qdef.objectives):
            if obj.type != "collect_item":
                continue
            if obj.target not in ("*", enemy_name, enemy_race):
                continue
            if prog[i] >= obj.count:
                continue
            if not obj.loot_item:
                continue
            if random.random() <= obj.loot_chance:
                factory = QUEST_ITEMS.get(obj.loot_item)
                if factory:
                    extras.append(factory())
    return extras
