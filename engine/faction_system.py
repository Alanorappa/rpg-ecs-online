"""
faction_system.py — Resolve a facção de qualquer entidade e a relação de
combate entre duas entidades. Ponto único de verdade pra "quem pode brigar
com quem" — EnemyAISystem (seleção de alvo/aggro) e CombatSystem (gate de
dano) SEMPRE passam por `can_engage()` aqui, nunca reimplementam a lógica
de facção/hostilidade inline.

Sem estado próprio (funções puras sobre `world` + os dois entity_id) —
testável isoladamente sem precisar rodar nenhum sistema de jogo.
"""
from __future__ import annotations

from engine.world import World
from engine.components import Faction, PlayerControlled
from content.faction_data import PLAYER_FACTION, get_relationship

# Sentinela pra entidade sem Faction e sem PlayerControlled (NPC estático,
# blocker, boneco de treino) — nunca inicia combate, mas também nunca
# quebra os helpers abaixo por ausência de componente.
_NO_FACTION = "__sem_faccao__"


def get_entity_faction(world: World, entity_id: int) -> str:
    """Facção de uma entidade: Faction.faction_id se presente; senão
    PLAYER_FACTION se for um player; senão o sentinela "sem facção"."""
    faction = world.get_component(entity_id, Faction)
    if faction is not None:
        return faction.faction_id
    if world.get_component(entity_id, PlayerControlled) is not None:
        return PLAYER_FACTION
    return _NO_FACTION


def get_relationship_between(world: World, eid_a: int, eid_b: int) -> str:
    """Tier de relação ("hostil"/"neutro"/"amigavel") entre duas
    entidades, resolvendo a facção de cada uma primeiro. Entidade "sem
    facção" nunca é hostil (cai no default "neutro" da tabela, já que
    "__sem_faccao__" nunca aparece em RELATIONSHIP) — nunca ataca nem é
    atacada por iniciativa própria."""
    fa = get_entity_faction(world, eid_a)
    fb = get_entity_faction(world, eid_b)
    return get_relationship(fa, fb)


def can_engage(world: World, attacker_id: int, target_id: int) -> bool:
    """True se attacker_id tem permissão de combate contra target_id —
    False só quando a relação entre as facções é "amigavel" (hostil e
    neutro sempre permitem dano; a diferença entre eles é só QUEM INICIA
    o combate, decidida em EnemyAISystem, não aqui)."""
    return get_relationship_between(world, attacker_id, target_id) != "amigavel"


def is_hostile(world: World, entity_id: int, target_id: int) -> bool:
    """True se entity_id deve agroar target_id por PROXIMIDADE (sem
    precisar ser provocado) — usado pelo gate de aggro por proximidade em
    EnemyAISystem."""
    return get_relationship_between(world, entity_id, target_id) == "hostil"
