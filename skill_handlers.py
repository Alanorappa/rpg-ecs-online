"""
skill_handlers.py — Mixin com todos os handlers de habilidades do jogador.

Separado de systems.py para manter SkillSystem conciso. Esta classe NÃO deve
ser instanciada diretamente — ela é herdada por SkillSystem, que fornece
self.world, self.player_entity_id, self.combat_system e self.tile_validation_system.

Para adicionar uma nova skill base:
  def _skill_<skill_id>(self, skill, combat_stats, combat_state, tile_move): ...

Para adicionar uma nova skill de talento:
  def _talent_<handler_name>(self, skill, combat_stats, combat_state, tile_move): ...
"""
from __future__ import annotations
import math
import random

import pygame

from components import (
    Position, Enemy, AIControlled, TileMovement, CombatStats, CombatState,
    CharacterStats, Tilemap, PlayerAutoMove, StatusEffects,
    SpellCast, AoeTargeting, IceBlockEffect,
)
from tileset import TILE_SIZE
from utils import chebyshev
from combat_log import LOG
from sound_manager import SOUNDS
from floating_text import FLT, WARN, PROC
from systems import apply_effect


class SkillHandlers:
    """Mixin com implementações de _skill_* e _talent_* para SkillSystem."""

    # Constantes usadas por Interceptar
    INTERCEPT_MIN_RANGE = 2     # tiles mínimos para usar Interceptar
    INTERCEPT_MAX_RANGE = 6     # tiles máximos para usar Interceptar
    INTERCEPT_DURATION  = 0.18  # segundos do dash

    AoE_RADIUS = 3  # raio do Impacto em tiles

    # ==================================================================
    # Utilitários internos
    # ==================================================================

    def _warn(self, text: str) -> None:
        """Exibe aviso de ação bloqueada em posição fixa na tela (não vai para o log)."""
        WARN.add(text)

    # ==================================================================
    # Habilidades base (skill_config.py)
    # ==================================================================

    def _skill_golpe_poderoso(self, skill, _combat_stats, combat_state, tile_move):
        """3x dano em alvo adjacente — custa 15 de Raiva (talento Veterano reduz até 10)."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        rage_cost = _combat_stats.golpe_poderoso_rage_cost if _combat_stats else 15
        if not char_stats or char_stats.rage < rage_cost:
            self._warn(f"Raiva insuficiente ({rage_cost})")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_tm or not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return
        dist = max(abs(tile_move.current_tile_x - target_tm.current_tile_x),
                   abs(tile_move.current_tile_y - target_tm.current_tile_y))
        if dist > 1:
            self._warn("Fora de alcance")
            return
        char_stats.rage -= rage_cost

        # Embalo: consome carga e aumenta o multiplicador de dano
        embalo_bonus = 0.0
        if _combat_stats and _combat_stats.embalo_on_crit and char_stats.embalo_charges > 0:
            embalo_bonus = _combat_stats.embalo_bonus_per_charge
            char_stats.embalo_charges -= 1

        self.combat_system.deal_damage(
            self.player_entity_id, target_id, "physical", multiplier=3.0 + embalo_bonus,
            is_ability=True)
        if combat_state:
            combat_state.enter_combat()
        return True

    # ------------------------------------------------------------------
    def _skill_vitoria_iminente(self, skill, combat_stats, combat_state, tile_move):
        """2× dano físico em alvo adjacente + cura 30% do HP máximo. Consome a carga."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_tm or not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return
        dist = max(abs(tile_move.current_tile_x - target_tm.current_tile_x),
                   abs(tile_move.current_tile_y - target_tm.current_tile_y))
        if dist > 1:
            self._warn("Fora de alcance")
            return
        skill.charges -= 1
        skill.charge_timer = 0.0
        self.combat_system.deal_damage(self.player_entity_id, target_id, "physical",
                                       multiplier=2.0, is_ability=True)
        heal = int(combat_stats.max_hp * 0.30)
        combat_stats.current_hp = min(combat_stats.max_hp, combat_stats.current_hp + heal)
        LOG.add(f"Vitória Iminente: +{heal} HP recuperados!", (80, 220, 80))
        if combat_state:
            combat_state.enter_combat()
        return True

    # ------------------------------------------------------------------
    def _skill_impacto(self, skill, _combat_stats, combat_state, tile_move):
        """50% dano em todos os inimigos dentro de AoE_RADIUS tiles.
        Máquina de Matar: +15% por inimigo no raio (checado antes do dano)."""
        px, py = tile_move.current_tile_x, tile_move.current_tile_y

        targets = []
        for enemy_id, _, enemy_tm, enemy_cs in self.world.get_entities_with(
                Enemy, TileMovement, CombatStats):
            if enemy_cs.current_hp <= 0:
                continue
            dist = max(abs(px - enemy_tm.current_tile_x),
                       abs(py - enemy_tm.current_tile_y))
            if dist <= self.AoE_RADIUS:
                targets.append(enemy_id)

        if not targets:
            self._warn("Nenhum inimigo no alcance")
            return False

        player_cs = self.world.get_component(self.player_entity_id, CombatStats)
        ap = player_cs.attack_power if player_cs else 0.0
        weapon = self.combat_system._get_mainhand_weapon(self.player_entity_id)
        if weapon and weapon.damage_min > 0:
            weapon_dmg = random.randint(weapon.damage_min, weapon.damage_max)
        else:
            weapon_dmg = player_cs.base_physical_damage if player_cs else 0.0
        dano_base = weapon_dmg + 0.5 * ap

        bonus = 0.15 * len(targets) if (player_cs and player_cs.impacto_maquina_matar) else 0.0
        dano_final = dano_base * (1.0 + bonus)

        for enemy_id in targets:
            self.combat_system.deal_damage(
                self.player_entity_id, enemy_id, "physical_fixed",
                base_ability_damage=dano_final, is_ability=True)

        msg = f"Impacto! Atingiu {len(targets)} inimigo(s)."
        if bonus > 0:
            msg += f" [Máquina de Matar +{bonus*100:.0f}%]"
        LOG.add(msg, (255, 180, 0))

        # Proc Assassino: 5% por alvo acertado → 1 carga livre de Executar
        if player_cs and player_cs.impacto_assassino:
            proc_chance = 0.05 * len(targets)
            if random.random() < proc_chance:
                char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
                if char_stats:
                    char_stats.free_executar_charges = 1
                    LOG.add("Assassino: Executar disponivel! (sem custo, sem restricao de HP)",
                            (255, 80, 80))
                    PROC.add("Assassino!", (255, 80, 80))
        if combat_state:
            combat_state.enter_combat()
        skill.current_cooldown = skill.cooldown
        return True

    # ------------------------------------------------------------------
    def _skill_executar(self, _skill, _combat_stats, combat_state, tile_move):
        """5x dano em alvo com menos de 30% HP — custa 10 de Raiva.
        Com carga livre (proc Assassino): ignora HP e custo de Raiva."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        free_charge = char_stats and char_stats.free_executar_charges > 0

        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        if not free_charge:
            if not char_stats or char_stats.rage < 10:
                self._warn("Raiva insuficiente (10)")
                return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return
        if not free_charge and target_cs.current_hp / max(1, target_cs.max_hp) >= 0.30:
            self._warn("Alvo precisa ter <30% HP")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm:
            return
        dist = max(abs(tile_move.current_tile_x - target_tm.current_tile_x),
                   abs(tile_move.current_tile_y - target_tm.current_tile_y))
        if dist > 1:
            self._warn("Fora de alcance")
            return

        if free_charge:
            char_stats.free_executar_charges -= 1
            LOG.add("Executar [Assassino]!", (255, 80, 80))
        else:
            char_stats.rage -= 10
        self.combat_system.deal_damage(
            self.player_entity_id, target_id, "physical", multiplier=5.0, is_ability=True)

        # Talento "Horrorizante": se alvo sobreviveu, aplica medo por 1s
        _cs_exec = self.world.get_component(self.player_entity_id, CombatStats)
        if _cs_exec and _cs_exec.executar_horrorizante:
            _tgt_cs = self.world.get_component(target_id, CombatStats)
            if _tgt_cs and _tgt_cs.current_hp > 0:
                apply_effect(self.world, target_id, "fear", 1.0)
                _tpos = self.world.get_component(target_id, Position)
                if _tpos:
                    FLT.add("Medo!", _tpos.x, _tpos.y,
                            (255, 140, 0), size="normal", target_id=target_id)

        if combat_state:
            combat_state.enter_combat()
        return True

    # ------------------------------------------------------------------
    def _has_los(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """Bresenham — retorna True se não há tile sólido entre (x0,y0) e (x1,y1)."""
        tilemap_comp = None
        for _, tc in self.world.get_entities_with(Tilemap):
            tilemap_comp = tc
            break
        if not tilemap_comp:
            return True

        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x1 > x0 else -1
        sy = 1 if y1 > y0 else -1
        err = dx - dy
        cx, cy = x0, y0

        while True:
            if cx == x1 and cy == y1:
                break
            if not (cx == x0 and cy == y0):
                rows = tilemap_comp.tile_matrix
                if (0 <= cy < len(rows) and 0 <= cx < len(rows[cy])
                        and rows[cy][cx].is_solid):
                    return False
            e2 = err * 2
            if e2 > -dy:
                err -= dy
                cx  += sx
            if e2 < dx:
                err += dx
                cy  += sy
        return True

    # ------------------------------------------------------------------
    def _skill_interceptar(self, skill, _combat_stats, combat_state, tile_move):
        """Dash até o tile adjacente ao alvo (animado, alcance 2–6 tiles)."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_tm or not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return

        tx, ty = target_tm.current_tile_x, target_tm.current_tile_y
        px, py = tile_move.current_tile_x, tile_move.current_tile_y
        dist = chebyshev(px, py, tx, ty)

        if dist < self.INTERCEPT_MIN_RANGE:
            self._warn("Alvo muito próximo")
            return
        if dist > self.INTERCEPT_MAX_RANGE:
            self._warn("Alvo muito longe")
            return

        adj = [(tx + dx, ty + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        walkable = [t for t in adj
                    if self.tile_validation_system.is_tile_walkable(self.player_entity_id, t[0], t[1])]
        if not walkable:
            self._warn("Sem espaço ao redor do alvo")
            return
        dest_x, dest_y = min(walkable, key=lambda t: abs(t[0] - px) + abs(t[1] - py))

        if not self._has_los(px, py, dest_x, dest_y):
            self._warn("Caminho bloqueado")
            return

        player_pos = self.world.get_component(self.player_entity_id, Position)
        new_px = dest_x * TILE_SIZE + TILE_SIZE / 2
        new_py = dest_y * TILE_SIZE + TILE_SIZE / 2

        if isinstance(player_pos, Position):
            tile_move.start_pixel_x = player_pos.x
            tile_move.start_pixel_y = player_pos.y
        tile_move.target_pixel_x  = new_px
        tile_move.target_pixel_y  = new_py
        tile_move.target_tile_x   = dest_x
        tile_move.target_tile_y   = dest_y
        tile_move.progress        = 0.0
        tile_move.move_duration   = self.INTERCEPT_DURATION
        tile_move.is_moving       = True
        tile_move.is_dash         = True

        auto = self.world.get_component(self.player_entity_id, PlayerAutoMove)
        if auto:
            auto.active        = False
            auto.path          = []
            auto.ground_target = None

        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        _cs_int = self.world.get_component(self.player_entity_id, CombatStats)
        if char_stats and _cs_int and _cs_int.interceptar_rage_bonus > 0:
            char_stats.rage = min(char_stats.max_rage, char_stats.rage + _cs_int.interceptar_rage_bonus)
        skill.current_cooldown = max(0.0, skill.cooldown - (_cs_int.interceptar_cooldown_reduction if _cs_int else 0.0))
        if _cs_int and _cs_int.interceptar_stun_duration > 0:
            apply_effect(self.world, target_id, "stun", _cs_int.interceptar_stun_duration)
            _tpos = self.world.get_component(target_id, Position)
            if _tpos:
                FLT.add("Atordoado!", _tpos.x, _tpos.y,
                        (180, 180, 255), size="normal", target_id=target_id)

        if combat_state:
            combat_state.enter_combat()
        LOG.add("Interceptar: dash!", (100, 200, 255))
        return True

    # ==================================================================
    # Handlers de habilidades de TALENTO
    # ==================================================================

    def _talent_golpe_debilitante(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Golpe Debilitante: 50% dano + -50% velocidade por 5s. Custa 5 Raiva."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not char_stats or char_stats.rage < 5:
            self._warn("Raiva insuficiente (5)")
            return False
        target_tm = self.world.get_component(target_id, TileMovement)
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_tm or not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return False
        dist = max(abs(tile_move.current_tile_x - target_tm.current_tile_x),
                   abs(tile_move.current_tile_y - target_tm.current_tile_y))
        if dist > 1:
            self._warn("Fora de alcance")
            return False
        char_stats.rage -= 5
        self.combat_system.deal_damage(self.player_entity_id, target_id, "physical",
                                       multiplier=0.5, is_ability=True)
        apply_effect(self.world, target_id, "slow", 5.0, magnitude=0.5)
        _tpos = self.world.get_component(target_id, Position)
        if _tpos:
            FLT.add("Lento!", _tpos.x, _tpos.y,
                    (100, 220, 80), size="normal", target_id=target_id)
        skill.current_cooldown = skill.cooldown
        if combat_state:
            combat_state.enter_combat()
        LOG.add("Golpe Debilitante: alvo com -50% velocidade por 5s!", (180, 220, 80))
        return True

    def _talent_punho_no_queixo(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Punho no Queixo: 45% AP + stun escalonável (1/2/3s por ponto). Consome 1 carga."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False
        if skill.charges <= 0:
            self._warn("Sem cargas")
            return False
        target_tm = self.world.get_component(target_id, TileMovement)
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_tm or not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return False
        dist = max(abs(tile_move.current_tile_x - target_tm.current_tile_x),
                   abs(tile_move.current_tile_y - target_tm.current_tile_y))
        if dist > 1:
            self._warn("Fora de alcance")
            return False

        # Duração do stun vem do flag pnq_stun_duration (calculado por apply_talent_effects)
        _cs_pnq = self.world.get_component(self.player_entity_id, CombatStats)
        stun_duration = _cs_pnq.pnq_stun_duration if _cs_pnq else 1.0

        skill.charges -= 1
        hp_before = target_cs.current_hp
        killed = self.combat_system.deal_damage(self.player_entity_id, target_id, "physical",
                                                multiplier=0.45, is_ability=True)
        hit = killed or target_cs.current_hp < hp_before
        if hit:
            apply_effect(self.world, target_id, "stun", stun_duration)
            LOG.add(f"Punho no Queixo: alvo atordoado por {stun_duration:.0f}s!", (255, 180, 80))
        skill.current_cooldown = skill.cooldown
        if combat_state:
            combat_state.enter_combat()
        return True

    def _talent_fatiador_de_corpos(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Fatiador de Corpos: spin AoE 45% dano/s por 5s."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not char_stats:
            return False
        char_stats.fatiador_timer = 5.0
        char_stats.fatiador_tick  = 1.0
        self._fatiador_aoe_tick(tile_move)
        skill.current_cooldown = skill.cooldown
        if combat_state:
            combat_state.enter_combat()
        LOG.add("Fatiador de Corpos: girando!", (255, 120, 60))
        return True

    def _fatiador_aoe_tick(self, tile_move) -> None:
        """Aplica 45% de dano a todos os inimigos em raio 2 tiles (tick do Fatiador)."""
        pl_x, pl_y = tile_move.current_tile_x, tile_move.current_tile_y
        radius = 2
        hit = 0
        for eid, _, etm, ecs in self.world.get_entities_with(Enemy, TileMovement, CombatStats):
            dist = chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y)
            if dist <= radius and ecs.current_hp > 0:
                self.combat_system.deal_damage(self.player_entity_id, eid, "physical",
                                               multiplier=0.45, is_ability=True)
                hit += 1
        if hit:
            LOG.add(f"Fatiador de Corpos: {hit} atingidos!", (255, 120, 60))

    def _talent_brado_provocativo(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Brado Provocativo: provoca inimigos em raio 3, enlouquecendo-os por 10s."""
        from components import StatusEffects as _SE_BP2
        pl_x = tile_move.current_tile_x
        pl_y = tile_move.current_tile_y
        taunted = 0
        for eid, _, etm, ecs in self.world.get_entities_with(Enemy, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > 3:
                continue
            apply_effect(self.world, eid, "enraged", 10.0)
            taunted += 1
        PROC.add("Brado!", (255, 100, 50))
        skill.current_cooldown = skill.cooldown
        if combat_state:
            combat_state.enter_combat()
        if not taunted:
            self._warn("Nenhum inimigo no raio")
        return True

    # ==================================================================
    # Habilidades do Mago
    # ==================================================================

    def _check_mana(self, char_stats: CharacterStats, cost: int) -> bool:
        if not char_stats or char_stats.mana < cost:
            self._warn(f"Mana insuficiente ({cost})")
            return False
        return True

    def _skill_bola_de_fogo(self, skill, combat_stats, combat_state, tile_move):
        """1.5s cast — 50% dano + 100% SP — 25 mana."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, skill.mana_cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, 30)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return False

        char_stats.mana -= skill.mana_cost

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id  = "bola_de_fogo",
            cast_time = skill.cast_time,
            elapsed   = 0.0,
            target_id = target_id,
        ))
        if combat_state:
            combat_state.is_casting = True
            combat_state.enter_combat()
            combat_state.is_pursuing = True

        SOUNDS.play_spell("bola_de_fogo", "cast")
        LOG.add("Lançando Bola de Fogo...", (255, 160, 60))
        return True

    def _cancel_pursuit_for_targeting(self) -> None:
        """
        Cancela perseguição de alvo e interrompe qualquer movimento em curso.
        Chamado por skills de mira/área antes de entrar no modo de targeting.
        Reutilizável para futuras skills com needs_aoe_target=True.
        """
        cs = self.world.get_component(self.player_entity_id, CombatState)
        if cs:
            cs.is_pursuing = False

        am = self.world.get_component(self.player_entity_id, PlayerAutoMove)
        if am:
            am.active         = False
            am.path.clear()
            am.ground_target  = None

        # Interrompe o tile movement em curso — snap para o tile atual
        tm = self.world.get_component(self.player_entity_id, TileMovement)
        if tm and tm.is_moving:
            tm.is_moving = False
            tm.progress  = 0.0
            pos = self.world.get_component(self.player_entity_id, Position)
            if pos:
                pos.x = tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2
                pos.y = tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2

    def _skill_calamidade_flamejante(self, skill, combat_stats, combat_state, tile_move):
        """Ativa o modo de mira AOE; clique esquerdo inicia a canalização."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, skill.mana_cost):
            return False

        if self.world.get_component(self.player_entity_id, AoeTargeting):
            return False  # já em modo de mira

        self._cancel_pursuit_for_targeting()
        self.world.add_component(self.player_entity_id, AoeTargeting(
            spell_id         = "calamidade_flamejante",
            radius_tiles     = 3.0,
            cast_range_tiles = 8.0,
        ))
        LOG.add("Clique para posicionar Calamidade Flamejante.", (255, 200, 80))
        return True

    def _skill_nova_congelante(self, skill, combat_stats, combat_state, tile_move):
        """Instantânea — raiz 5s em todos a 3 tiles + 50% SP. 10 mana."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, skill.mana_cost):
            return False

        char_stats.mana -= skill.mana_cost
        SOUNDS.play_spell("nova_congelante", "cast")

        pl_x = tile_move.current_tile_x
        pl_y = tile_move.current_tile_y
        pl_pos = self.world.get_component(self.player_entity_id, Position)

        hit = 0
        for eid, epos, _, _, etm, ecs in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > 3:
                continue
            # Dano
            sp = combat_stats.spell_power if combat_stats else 0
            dmg = max(1, int(sp * 0.5))
            from spell_system import _apply_magic_damage
            _apply_magic_damage(self.player_entity_id, eid, dmg, self.world)
            # Raiz
            sfx = self.world.get_component(eid, StatusEffects)
            if sfx:
                apply_effect(self.world, eid, "root", 5.0)
                etm_c = self.world.get_component(eid, TileMovement)
            hit += 1

        if hit == 0:
            self._warn("Nenhum inimigo no raio")
        else:
            SOUNDS.play_spell("nova_congelante", "impact")
            LOG.add(f"Nova Congelante — {hit} inimigo(s) enraizados.", (100, 180, 255))

        if combat_state:
            combat_state.enter_combat()

        skill.current_cooldown = skill.cooldown
        return True

    def _skill_bloco_de_gelo(self, skill, combat_stats, combat_state, tile_move):
        """Imunidade + cura 10% HP/s durante 5s. Imóvel durante efeito."""
        if self.world.get_component(self.player_entity_id, IceBlockEffect):
            self._warn("Bloco de Gelo já ativo")
            return False

        self.world.add_component(self.player_entity_id, IceBlockEffect(
            duration=5.0, elapsed=0.0, heal_interval=1.0, last_heal=0.0,
        ))
        SOUNDS.play_spell("bloco_de_gelo", "cast")
        if combat_state:
            combat_state.is_stunned = True
            combat_state.is_immune  = True

        skill.current_cooldown = skill.cooldown
        LOG.add("Bloco de Gelo ativado! Imune por 5s.", (100, 180, 255))
        return True
