"""
faction_system.py — Resolve a facção de qualquer entidade e a relação de
combate entre duas entidades. Ponto único de verdade pra "quem pode brigar
com quem" — EnemyAISystem (seleção de alvo/aggro) e CombatSystem (gate de
dano) SEMPRE passam por `can_engage()` aqui, nunca reimplementam a lógica
de facção/hostilidade inline.

Sem estado próprio além do contexto PvP plugável (funções puras sobre
`world` + os dois entity_id) — testável isoladamente sem precisar rodar
nenhum sistema de jogo.
"""
from __future__ import annotations

from engine.world import World
from engine.components import Faction, PlayerControlled, RemoteControlled
from content.faction_data import PLAYER_FACTION, get_relationship

# Sentinela pra entidade sem Faction e sem PlayerControlled (NPC estático,
# blocker, boneco de treino) — nunca inicia combate, mas também nunca
# quebra os helpers abaixo por ausência de componente.
_NO_FACTION = "__sem_faccao__"

# ── Contexto PvP plugável ────────────────────────────────────────────────
# PvP entre players é CONTEXTUAL, não uma regra fixa de facção (decisão do
# usuário 16/07/2026: duelo por convite, arenas, campos de batalha, zonas —
# mesmo modelo do WoW, onde duelo/war mode/arena são camadas de permissão
# por cima das facções). O gate de facção continua o padrão (amigavel =
# bloqueado, o que inclui "mesma facção" — fogo amigo de time), e este
# resolver é o ÚNICO ponto onde um contexto libera exceção — e SÓ entre
# dois players (mob/NPC nunca ganha permissão por contexto).
#
# Implementações previstas: flag global pvp_enabled (a atual, registrada
# pelo WorldServer — equivale a "mundo inteiro é zona PvP"), duelo aceito
# entre um par específico, zona de arena, campo de batalha por times.
# `fn(world, attacker_id, target_id) -> bool`.
_pvp_context_resolver = None


def register_pvp_context(resolver) -> None:
    """Registra o resolver de contexto PvP (server: no boot do
    WorldServer). `None` desregistra (PvP entre facções amigáveis volta a
    ser sempre bloqueado — comportamento seguro por default)."""
    global _pvp_context_resolver
    _pvp_context_resolver = resolver


def get_entity_faction(world: World, entity_id: int) -> str:
    """Facção de uma entidade: Faction.faction_id se presente; senão
    PLAYER_FACTION se for um player — local (PlayerControlled, os dois
    lados) OU remoto (RemoteControlled, proxy client-side de outro
    player); senão o sentinela "sem facção".

    RemoteControlled entrou em 16/07/2026 (players amigáveis por
    default): sem ele, o proxy de player remoto no CLIENTE caía no
    sentinela (neutro → atacável) e clique direito/SPACE iniciavam
    ataque contra qualquer player. No servidor todo player é
    PlayerControlled, então o braço RemoteControlled só executa no
    cliente."""
    faction = world.get_component(entity_id, Faction)
    if faction is not None:
        return faction.faction_id
    if world.get_component(entity_id, PlayerControlled) is not None:
        return PLAYER_FACTION
    if world.get_component(entity_id, RemoteControlled) is not None:
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
    o combate, decidida em EnemyAISystem, não aqui).

    Exceção única: entre DOIS PLAYERS, um contexto PvP registrado
    (`register_pvp_context`) pode liberar mesmo com relação amigável —
    duelo/arena/zona/flag global (ver comentário do resolver acima).
    Regressão real que motivou isto: o PvP de mundo aberto ficou
    silenciosamente bloqueado quando o gate de facção nasceu (dois
    players = mesma facção "jogadores" = amigavel = dano 0), e nenhum
    teste de dano PvP ponta a ponta existia pra acusar."""
    if get_relationship_between(world, attacker_id, target_id) != "amigavel":
        return True
    if (_pvp_context_resolver is not None
            and world.get_component(attacker_id, PlayerControlled) is not None
            and world.get_component(target_id, PlayerControlled) is not None):
        return bool(_pvp_context_resolver(world, attacker_id, target_id))
    return False


def is_hostile(world: World, entity_id: int, target_id: int) -> bool:
    """True se entity_id deve agroar target_id por PROXIMIDADE (sem
    precisar ser provocado) — usado pelo gate de aggro por proximidade em
    EnemyAISystem."""
    return get_relationship_between(world, entity_id, target_id) == "hostil"
