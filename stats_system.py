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
# Função utilitária compartilhada
# ---------------------------------------------------------------------------

def apply_char_stats_to_combat(char_stats: CharacterStats,
                                combat_stats: CombatStats,
                                permanent: PermanentStats | None = None) -> None:
    """
    Recalcula os valores base de CombatStats a partir de CharacterStats + PermanentStats.

    Fórmulas:
      base_stamina      = 5 + VIT_total * 5       (VIT=3 → 20)
      base_armor        = DEF_total * 2            (DEF=2 → 4)
      base_attack_power = 5 + STR_total * 2        (STR=1 → 7)
      base_spell_power  = INT_total * 2            (INT=1 → 2)
      base_crit_rating  = 0.10 + AGI_total * 0.01  (AGI=1 → 11%)

    base_physical_damage e base_attack_interval mantêm seus valores
    (são controlados por arma equipada, definido em fases futuras).
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
    # AGI → dodge (2 pontos de rating por ponto de agi → 20 agi = 2% dodge)
    combat_stats.base_dodge_rating = total_agi * 2.0
    # STR → parry (1 ponto de rating por ponto de str → 20 str = 1% parry)
    combat_stats.base_parry_rating = total_str * 1.0

    combat_stats._recalculate_effective_stats()


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

            while char_stats.current_xp >= char_stats.xp_to_next_level:
                char_stats.current_xp -= char_stats.xp_to_next_level
                char_stats.level += 1
                char_stats.xp_to_next_level = CharacterStats.xp_for_level(char_stats.level)
                # Atributos base por nível
                char_stats.vitality      += 1   # Stamina
                char_stats.strength      += 1
                char_stats.agility       += 1
                char_stats.intelligence  += 1
                char_stats.defense       += 2   # Armor
                # Concede 1 ponto de talento por nível
                from components import TalentTree
                tt = self.world.get_component(entity_id, TalentTree)
                if tt is not None:
                    tt.available_points += 1
                SOUNDS.play_ui("levelup")
                LOG.add(f"Level up! Nivel {char_stats.level} — 1 ponto de talento disponivel (T).", (255, 200, 0))
                from quest_events import fire as _qfire
                _qfire("reach_level", level=char_stats.level)

            apply_char_stats_to_combat(char_stats, combat_stats, perm)

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
        char_stats.embalo_charges = 0
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
