"""
npc_interaction.py — Ponto único de "andar até um NPC amigável pra
interagir" (04/09/2026, pedido do usuário).

Antes, cada sistema de NPC amigável (TrainerSystem/CraftingSystem/
QuestDialogSystem/ShopSystem) tinha sua PRÓPRIA cópia quase idêntica
desse cálculo (achar o tile adjacente mais próximo + mandar o
PlayerAutoMove pra lá) — e NENHUMA delas suprimia a perseguição de um
alvo de combate em andamento. Bug real relatado pelo usuário: atacando
o boneco de treino (ou qualquer alvo travado) e clicando com o direito
num NPC pra interagir, o personagem ficava oscilando entre andar até o
NPC e ser puxado de volta pro alvo antigo pelo auto-chase — os dois
competindo quadro a quadro, porque o clique no NPC nunca avisava o
sistema de combate que "o jogador desviou a atenção".

`suppress_chase` liga a MESMA flag que clicar no chão já usa pra
permitir kite (arqueiro se afastando enquanto atira sem perder o
alvo, `ui/systems.py::MouseTargetingSystem`) — NUNCA mexe em
`target_entity_id`/`is_pursuing`, só pausa o auto-walk de volta ao
alvo. Alvo e combate continuam intactos; reengajar (clicar no alvo de
novo, Espaço, skill) volta a perseguir normalmente.
"""
from __future__ import annotations


# ── Abas de serviço do NPC (21/10/2026, pedido do usuário) ──────────────
# NPC com mais de uma função (loja + ferreiro, treinador + quest...) abre
# DIRETO numa janela, com abas no topo pras outras funções — sem o menu
# intermediário de "o que deseja?". Quest disponível/entregável abre
# SEMPRE primeiro. Cada janela continua no seu System; trocar de aba
# fecha a janela atual e pede a outra via NPCInteractionRequest (System
# nunca chama System). Ordem e rótulos: _SERVICES (fonte única).
_SERVICES = (
    ("quest",   "open_quest_dialog", "Missões"),
    ("shop",    "open_shop",         "Negociar"),
    ("recycle", "open_recycle",      "Reciclar"),
    ("forge",   "open_forge",        "Forjar"),
    ("train",   "open_train",        "Treinar"),
)
_KIND = {key: kind for key, kind, _ in _SERVICES}


def npc_services(world, player_entity: int, npc_eid: int) -> list:
    """[(chave, rótulo)] das funções que ESTE NPC oferece a ESTE player,
    na ordem de _SERVICES (Missões primeiro). NPC travado por quest
    (NPCUnlock) só oferece Missões."""
    from engine.components import (NPC, Merchant, Blacksmith, Trainer, CharacterStats,
                                   QuestLog)
    import engine.quest_logic as quest_logic
    npc = world.get_component(npc_eid, NPC)
    name = npc.name if npc else ""
    has = set()
    if (quest_logic.get_available_quests(world, player_entity, npc_eid)
            or quest_logic.get_completable_quests(world, player_entity, npc_eid, name)):
        has.add("quest")
    ql = world.get_component(player_entity, QuestLog)
    if quest_logic.is_npc_unlocked(world, npc_eid, ql):
        if world.get_component(npc_eid, Merchant) is not None:
            has.add("shop")
        if world.get_component(npc_eid, Blacksmith) is not None:
            has.update(("recycle", "forge"))
        tr = world.get_component(npc_eid, Trainer)
        cs = world.get_component(player_entity, CharacterStats)
        if tr is not None and cs is not None and cs.class_id == tr.class_id:
            has.add("train")
    return [(key, label) for key, _, label in _SERVICES if key in has]


def open_npc_service(world, player_entity: int, npc_eid: int, key: str) -> None:
    """Pede a abertura da janela da função `key` (quem abre é o System
    dono dela, no update() — consome o NPCInteractionRequest)."""
    from engine.components import NPCInteractionRequest
    world.add_component(player_entity, NPCInteractionRequest(kind=_KIND[key], target_eid=npc_eid))


def default_npc_service(world, player_entity: int, npc_eid: int) -> "str | None":
    """Função que abre ao clicar no NPC: a 1ª de npc_services (Missões
    quando houver quest pra aceitar/entregar)."""
    services = npc_services(world, player_entity, npc_eid)
    return services[0][0] if services else None


def service_tabs(f, world, player_entity: int, npc_eid: int, active: str, font,
                 hits: "dict | None" = None) -> "str | None":
    """Fileira de abas das funções do NPC na linha 1 da janela (Frame do
    atlas). Só desenha com 2+ funções. Devolve a chave clicada (outra que
    não a ativa) ou None. `hits` recebe "npc:tab:<chave>" → rect de tela."""
    services = npc_services(world, player_entity, npc_eid)
    keys = [k for k, _ in services]
    if len(services) < 2 or active not in keys:
        return None
    labels = [label for _, label in services]
    idx = f.tabs("npc:tabs", 1, 1, labels, keys.index(active), font=font)
    if hits is not None:
        import pygame
        from ui import atlas_window as aw
        col = 1
        for key, label in services:
            w = f.tiles_for([label], 3, font)
            hits[f"npc:tab:{key}"] = pygame.Rect(f.px + col * aw.T * aw.S, f.py + aw.T * aw.S,
                                                 w * aw.T * aw.S, aw.T * aw.S)
            col += w
    return keys[idx] if idx is not None else None


def has_service_tabs(world, player_entity: int, npc_eid: int) -> bool:
    return len(npc_services(world, player_entity, npc_eid)) >= 2


def suppress_chase(world, player_entity: int) -> None:
    """Liga `CombatState.chase_suppressed` — chamado tanto por
    `walk_to_npc` (abaixo) quanto pelo caminho "já adjacente, abre
    direto" (sem precisar andar) de cada sistema de NPC, pra interagir
    com um NPC amigável nunca competir com o auto-chase de um alvo de
    combate anterior."""
    from engine.components import CombatState
    combat_state = world.get_component(player_entity, CombatState)
    if combat_state:
        combat_state.chase_suppressed = True


def walk_to_npc(world, player_entity: int, player_tile: "tuple | None",
                npc_tile: "tuple | None") -> None:
    """Manda o PlayerAutoMove pro tile adjacente a `npc_tile` mais
    próximo de `player_tile`, e suprime a perseguição de combate em
    andamento (ver `suppress_chase`). `player_tile`/`npc_tile` já
    resolvidos pelo CHAMADOR (cada sistema já tem seu próprio jeito de
    achar o tile do NPC — TileMovement vs Position — não reimplementado
    aqui pra não mudar esse comportamento já existente)."""
    from engine.components import PlayerAutoMove
    auto = world.get_component(player_entity, PlayerAutoMove)
    if auto and player_tile and npc_tile:
        adj = [(npc_tile[0] + dx, npc_tile[1] + dy)
              for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        target = min(adj, key=lambda t: abs(t[0] - player_tile[0]) + abs(t[1] - player_tile[1]))
        auto.ground_target     = target
        auto.path              = []
        auto.active            = True
        auto.path_recalc_timer = 0.0
    suppress_chase(world, player_entity)
