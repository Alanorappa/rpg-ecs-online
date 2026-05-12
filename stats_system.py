# stats_system.py
"""
Sistemas de progressão do personagem:
  - XPSystem          : concede XP ao jogador após kills e dispara level-up
  - DeathRespawnSystem     : mecânica roguelike — acumula stats na morte e respawna
"""
from __future__ import annotations
import pygame

from world import World
from components import (
    CharacterStats, PermanentStats, CombatStats, CombatState,
    PlayerControlled, PlayerAutoMove, TileMovement, Position, StatusEffects,
)
from systems import System
from tileset import TILE_SIZE
from combat_log import LOG
from floating_text import FLT
from sound_manager import SOUNDS
from save_system import request_autosave


# ---------------------------------------------------------------------------
# Ganhos de atributo por level up — específicos por classe.
# Adicionar nova classe: inserir entrada aqui.
# Campos ausentes recebem 0 (atributo não cresce).
# ---------------------------------------------------------------------------
CLASS_LEVEL_GAINS: dict[str, dict] = {
    "guerreiro": {"strength": 2, "vitality": 2, "agility": 1, "intelligence": 0, "defense": 2},
    "mago":      {"strength": 0, "vitality": 1, "agility": 0, "intelligence": 3, "defense": 0},
    "arqueiro":  {"strength": 0, "vitality": 1, "agility": 3, "intelligence": 0, "defense": 0},
}

# Atributos base ao criar um personagem novo — específicos por classe.
# Adicionar nova classe: inserir entrada aqui. game.py lê deste dict.
CLASS_BASE_STATS: dict[str, dict] = {
    "guerreiro": {"strength": 1, "intelligence": 1, "agility": 1, "vitality": 3, "defense": 2},
    "mago":      {"strength": 1, "intelligence": 5, "agility": 1, "vitality": 2, "defense": 1},
    "arqueiro":  {"strength": 1, "intelligence": 1, "agility": 5, "vitality": 2, "defense": 1},
}


# ---------------------------------------------------------------------------
# Dados de melee por classe — independentes do crescimento de atributos.
# Adicionar uma nova classe: inserir uma entrada aqui.
# Campos omitidos mantêm o valor calculado por apply_char_stats_to_combat.
# ---------------------------------------------------------------------------
# Tipos de armadura permitidos por classe.
# Adicionar nova classe: inserir entrada aqui.
CLASS_ARMOR_ALLOWED: dict[str, frozenset] = {
    "guerreiro": frozenset({"placa", "couro", "tecido"}),
    "arqueiro":  frozenset({"couro", "tecido"}),
    "mago":      frozenset({"tecido"}),
}

# base_attack_interval NÃO entra aqui — é gerenciado pelo equip/unequip de armas.
# _default_attack_interval = velocidade sem arma (restaurada ao desequipar).
CLASS_MELEE_OVERRIDES: dict[str, dict] = {
    "guerreiro": {
        "_default_attack_interval": 2.6,
        "base_physical_damage":     3,
        "base_physical_damage_max": 6,
    },
    "mago": {
        "base_attack_power":        0,
        "_default_attack_interval": 3.2,
        "base_physical_damage":     2,
        "base_physical_damage_max": 5   ,
    },
}


# ---------------------------------------------------------------------------
# Função utilitária compartilhada
# ---------------------------------------------------------------------------

def apply_char_stats_to_combat(char_stats: CharacterStats,
                                combat_stats: CombatStats,
                                permanent: PermanentStats | None = None) -> None:
    """
    Deriva CombatStats a partir dos atributos do personagem (STR/INT/AGI/VIT/DEF).

    Responsabilidade única: derivação de atributos → stats de combate.
    NÃO toca base_attack_interval nem _default_attack_interval — esses são
    gerenciados por sync_attack_interval (init/load) e pelo sistema de equip/unequip.
    """
    p = permanent
    total_str = char_stats.strength      + (p.strength      if p else 0)
    total_int = char_stats.intelligence  + (p.intelligence  if p else 0)
    total_agi = char_stats.agility       + (p.agility       if p else 0)
    total_vit = char_stats.vitality      + (p.vitality      if p else 0)
    total_def = char_stats.defense       + (p.defense       if p else 0)

    combat_stats.base_stamina      = 5  + total_vit * 5
    combat_stats.base_armor        =      total_def * 2
    combat_stats.base_attack_power = 5  + total_str * 2
    combat_stats.base_spell_power  =      total_int * 2
    combat_stats.base_crit_rating  = 0.10 + total_agi * 0.01
    combat_stats.base_dodge_rating = total_agi * 2.0
    combat_stats.base_parry_rating = total_str * 1.0

    # Overrides de classe: dano sem arma e AP base (sem tocar velocidade de ataque)
    overrides = CLASS_MELEE_OVERRIDES.get(char_stats.class_id)
    if overrides:
        for _attr, _val in overrides.items():
            setattr(combat_stats, _attr, _val)

    combat_stats._recalculate_effective_stats()

    # Mana — escala com INT (relevante para o mago, calculado para todos)
    new_max_mana = 100 + total_int * 15
    if char_stats.max_mana != new_max_mana:
        if char_stats.max_mana == 0:
            char_stats.mana = new_max_mana
        elif char_stats.mana > new_max_mana:
            char_stats.mana = new_max_mana
        char_stats.max_mana = new_max_mana


def sync_attack_interval(combat_stats: CombatStats, equipment=None) -> None:
    """
    Sincroniza base_attack_interval com o estado atual do equipamento.

    Deve ser chamada APENAS em dois momentos:
      1. Criação de novo personagem (classe definida, sem arma)
      2. Load de save (reconstituição do estado completo)

    Nunca chamar em level-up ou respawn — velocidade de ataque não muda com evolução.

    Regras:
      - Arma equipada na mainhand com attack_speed > 0 → usa velocidade da arma
      - Sem arma → usa _default_attack_interval (padrão da classe)
    """
    weapon = equipment.slots.get("mainhand") if equipment else None
    weapon_speed = getattr(weapon, "attack_speed", 0.0) if weapon else 0.0
    if weapon_speed > 0:
        combat_stats.base_attack_interval = weapon_speed
    else:
        combat_stats.base_attack_interval = combat_stats._default_attack_interval
    combat_stats._recalculate_effective_stats()


# ---------------------------------------------------------------------------
# Função utilitária de level-up
# ---------------------------------------------------------------------------

def process_levelups(world: World, entity_id: int,
                     char: CharacterStats, cs: CombatStats,
                     perm: "PermanentStats | None") -> None:
    """Processa todos os level-ups pendentes em `char` e aplica os efeitos.

    Centraliza a lógica que estava triplicada em XPSystem, QuestSystem e
    GameEngine._debug_levelup. Deve ser chamada após adicionar XP a `char`.
    """
    from components import TalentTree
    from quest_events import fire as _qfire

    leveled = False
    while char.current_xp >= char.xp_to_next_level:
        char.current_xp     -= char.xp_to_next_level
        char.level          += 1
        char.xp_to_next_level = CharacterStats.xp_for_level(char.level)
        gains = CLASS_LEVEL_GAINS.get(char.class_id, {})
        char.strength     += gains.get("strength",     0)
        char.intelligence += gains.get("intelligence", 0)
        char.agility      += gains.get("agility",      0)
        char.vitality     += gains.get("vitality",     0)
        char.defense      += gains.get("defense",      0)
        tt = world.get_component(entity_id, TalentTree)
        if tt is not None:
            tt.available_points += 1
        SOUNDS.play_ui("levelup")
        gains_str = ", ".join(f"+{v} {k[:3].upper()}" for k, v in gains.items() if v > 0)
        LOG.add(f"Level up! Nivel {char.level} — {gains_str} | 1 ponto de talento (T).", (255, 200, 0))
        _qfire("reach_level", level=char.level)
        leveled = True

    if leveled:
        apply_char_stats_to_combat(char, cs, perm)


# ---------------------------------------------------------------------------
# XPSystem
# ---------------------------------------------------------------------------

class XPSystem(System):
    """
    Processa os prêmios de XP acumulados por DeathHandlerSystem.pending_xp
    e dispara level-up quando o limiar é atingido.
    """

    def __init__(self, world: World, death_handler):
        self.world         = world
        self.death_handler = death_handler

    def update(self, events: list = None, dt: float = 0) -> None:
        if not self.death_handler.pending_xp:
            return

        for entity_id, char_stats, combat_stats, _ in \
                self.world.get_entities_with(CharacterStats, CombatStats, PlayerControlled):
            perm = self.world.get_component(entity_id, PermanentStats)

            pos = self.world.get_component(entity_id, Position)
            for xp in self.death_handler.pending_xp:
                char_stats.current_xp += xp
                if pos:
                    FLT.add(f"+{xp} xp", pos.x, pos.y,
                            (255, 160, 0), size="normal", target_id=entity_id)

            process_levelups(self.world, entity_id, char_stats, combat_stats, perm)

        self.death_handler.pending_xp.clear()
        request_autosave()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# DeathRespawnSystem
# ---------------------------------------------------------------------------

class DeathRespawnSystem(System):
    """
    Mecânica roguelike: quando o jogador morre (hp <= 0),
      1. Acumula os atributos atuais em PermanentStats
      2. Reseta CharacterStats para os valores iniciais
      3. Recalcula CombatStats (agora mais forte pelos permanentes)
      4. Restaura HP cheio e sinaliza ao GameEngine para trocar de mapa
    """

    INITIAL_STATS = dict(strength=1, intelligence=1, agility=1, vitality=3, defense=2)

    def __init__(self, world: World):
        self.world = world
        self.pending_respawn: "dict | None" = None  # lido e consumido pelo GameEngine

    def update(self, events: list = None, dt: float = 0) -> None:
        for entity_id, combat_stats, char_stats, _ in \
                self.world.get_entities_with(CombatStats, CharacterStats, PlayerControlled):
            if combat_stats.current_hp > 0:
                continue
            self._respawn(entity_id, combat_stats, char_stats)

    def _respawn(self, entity_id: int, combat_stats: CombatStats,
                 char_stats: CharacterStats) -> None:
        perm = self.world.get_component(entity_id, PermanentStats)

        # Nenhum reset de level, atributos ou talentos — apenas restaura HP
        char_stats.free_executar_charges = 0
        char_stats.embalo_charges        = 0
        char_stats.fire_instant_ready    = False
        char_stats.thermal_shock_active  = False
        char_stats.fatiador_timer        = 0.0
        char_stats.fatiador_tick         = 0.0
        apply_char_stats_to_combat(char_stats, combat_stats, perm)
        combat_stats.current_hp = combat_stats.max_hp
        combat_stats.attack_cooldown_timer = 0.0

        # 4. Sinaliza ao GameEngine para carregar mapa principal e teleportar ao cemitério
        self.pending_respawn = {
            "target_map": char_stats.spawn_map,
            "target_x":   char_stats.spawn_tile_x,
            "target_y":   char_stats.spawn_tile_y,
        }

        # 5. Limpa efeitos de estado ativos
        sfx = self.world.get_component(entity_id, StatusEffects)
        if sfx:
            sfx.effects.clear()
        tm_player = self.world.get_component(entity_id, TileMovement)
        if tm_player:
            tm_player.slow_mult = 1.0
            tm_player.debilitate_elapsed = 0.0

        # 6. Limpa estado de combate
        cs = self.world.get_component(entity_id, CombatState)
        if cs:
            cs.target_entity_id = -1
            cs.in_combat = False
            cs.is_pursuing = False
        am = self.world.get_component(entity_id, PlayerAutoMove)
        if am:
            am.active = False
            am.path.clear()

        perm_str = f"FOR+{perm.strength} VIT+{perm.vitality} DEF+{perm.defense}" if perm else ""
        LOG.add(f"Renasceu no nivel 1. Bônus permanentes: {perm_str}", (200, 100, 220))
        LOG.add(f"HP maximo agora: {combat_stats.max_hp}", (200, 100, 220))
