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
