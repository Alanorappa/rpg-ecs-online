"""
quest_events.py — Barramento de eventos para o sistema de quests.

Cliente (offline/local): QuestSystem consome direto no update(), ignorando
player_eid (só existe 1 player local). Servidor: WorldServer._process_quest_events()
consome 1x/tick e usa player_eid pra achar o QuestLog de quem disparou —
cada processo (cliente e servidor) tem sua PRÓPRIA QUEST_EVENTS (módulo
importado separadamente em cada um), não há fila compartilhada entre eles.
A fila é drenada frame/tick a frame/tick — eventos nunca acumulam.

Tipos de evento:
    kill                {"name": str, "race": str, "tier": str}
    collect_item        {"item_id": str}
    reach_tile          {"tx": int, "ty": int, "map": str}   # map = arquivo do mapa atual
    use_skill           {"skill_id": str, "on_dummy": bool}  # on_dummy = alvo tinha TrainingDummy
    use_consumable      {"item_id": str}
    reach_level         {"level": int}
    talk_to_npc         {"npc_name": str}
    equip_item          {"item_id": str, "item_type": str}
    use_item_on_target  {"item_name": str, "target_name": str, "target_race": str}  # não migrado p/ item_id (débito C2) — feature sem chamador real (nenhum quest_events.fire("use_item_on_target",...) no código)
    learn_skill         {"skill_id": str}   # disparado ao aprender no treinador (trainer_system.py)
"""
from __future__ import annotations
from collections import deque

QUEST_EVENTS: deque = deque()

# Referência ao QuestSystem — definida por GameEngine após criar o sistema.
# Permite que DeathHandlerSystem consulte drops condicionais sem importar quest_system.
_quest_system_ref = None


def fire(event_type: str, player_eid: int = -1, **data) -> None:
    """Enfileira um evento de quest. Chamado por qualquer sistema.

    player_eid: default -1 = "player local implícito" (todo chamador
    client-only existente continua funcionando sem mudança — só há 1
    player no processo cliente). Chamadores server-side SEMPRE passam o
    eid explícito de quem disparou o evento, já que o servidor processa
    múltiplos players no mesmo loop (ver WorldServer._process_quest_events).
    """
    QUEST_EVENTS.append((event_type, player_eid, data))


def set_quest_system(qs) -> None:
    """Chamado por GameEngine para registrar o QuestSystem."""
    global _quest_system_ref
    _quest_system_ref = qs
