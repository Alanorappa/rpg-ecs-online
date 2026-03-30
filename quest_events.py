"""
quest_events.py — Barramento de eventos para o sistema de quests.

Outros sistemas disparam eventos via fire(). QuestSystem os consome no update().
A fila é drenada frame a frame — eventos nunca acumulam entre frames.

Tipos de evento:
    kill            {"name": str, "race": str, "tier": str}
    collect_item    {"item_name": str}
    reach_tile      {"tx": int, "ty": int}
    use_skill       {"skill_id": str}
    use_consumable  {"item_name": str}
    reach_level     {"level": int}
    talk_to_npc     {"npc_name": str}
    equip_item      {"item_name": str, "item_type": str}
"""
from __future__ import annotations
from collections import deque

QUEST_EVENTS: deque = deque()

# Referência ao QuestSystem — definida por GameEngine após criar o sistema.
# Permite que DeathHandlerSystem consulte drops condicionais sem importar quest_system.
_quest_system_ref = None


def fire(event_type: str, **data) -> None:
    """Enfileira um evento de quest. Chamado por qualquer sistema."""
    QUEST_EVENTS.append((event_type, data))


def set_quest_system(qs) -> None:
    """Chamado por GameEngine para registrar o QuestSystem."""
    global _quest_system_ref
    _quest_system_ref = qs
