"""
spell_system.py — Sistemas de magia do jogador (classe Mago).

Sistemas incluídos:
  ManaSystem            — regen de mana ao longo do tempo
  SpellCastSystem       — processa barra de cast e dispara projéteis ao completar
  PlayerProjectileSystem — move projéteis do jogador e aplica dano/morte
  ChannelingSystem      — processa ticks de canalização (Calamidade Flamejante)
  IceBlockSystem        — processa Bloco de Gelo (imunidade + cura por segundo)
  AoeTargetingSystem    — intercepta clique esquerdo para posicionar magia AOE
"""
from __future__ import annotations
import math
import random

import pygame

from world import World
from systems import System
from components import (
    Position, PlayerControlled, CombatState, CombatStats, CharacterStats,
    Equipment, Enemy, AIControlled, TileMovement, StatusEffects, Camera,
    SpellCast, Channeling, IceBlockEffect, FireShieldEffect, PirofagiaAiming,
    PlayerProjectile, AoeTargeting, PlayerSkills,
    PendingDeath, PlayerAutoMove, MobSounds,
)
from tileset import TILE_SIZE
from utils import chebyshev
from combat_log import LOG
from floating_text import FLT, WARN
from sound_manager import SOUNDS
from stat_fns import enter_combat
from damage_calculator import resolve_attack_outcome, CRITICAL_DAMAGE_MULTIPLIER


# ---------------------------------------------------------------------------
# Utilitário interno — cálculo de dano de magia
# ---------------------------------------------------------------------------

def _spell_damage(attacker_id: int, world: World,
                  dmg_weapon_pct: float, sp_coeff: float) -> int:
    """Dano de magia = dano_arma*pct + spell_power*coeff (mínimo 1)."""
    cs = world.get_component(attacker_id, CombatStats)
    eq = world.get_component(attacker_id, Equipment)
    if not cs:
        return 1
    weapon_dmg = float(cs.base_physical_damage)
    if eq:
        wep = eq.slots.get("mainhand")
        if wep and wep.damage_min and wep.damage_max:
            weapon_dmg = (wep.damage_min + wep.damage_max) / 2.0
    return max(1, int(weapon_dmg * dmg_weapon_pct + cs.spell_power * sp_coeff))


def _apply_magic_damage(attacker_id: int, target_id: int, dmg: int, world: World,
                        is_crit: bool = False) -> bool:
    """Aplica dano mágico ao alvo. Retorna True se o alvo morreu."""
    target_cs = world.get_component(target_id, CombatStats)
    if not target_cs or target_cs.current_hp <= 0:
        return False
    target_state = world.get_component(target_id, CombatState)
    if target_state and target_state.is_immune:
        return False
    target_cs.current_hp = max(0, target_cs.current_hp - dmg)
    pos = world.get_component(target_id, Position)
    if pos:
        if is_crit:
            FLT.add(f"{dmg}", pos.x, pos.y, (255, 180, 80), target_id=target_id, is_crit=True)
        else:
            FLT.add(f"-{dmg}", pos.x, pos.y, (180, 100, 255), size="normal", target_id=target_id)
    # Dano mágico também quebra Polimorfia
    from components import StatusEffects as _SE
    _t_sfx = world.get_component(target_id, _SE)
    if _t_sfx and _t_sfx.remove("polymorph"):
        if pos:
            FLT.add("Polimorfia quebrada!", pos.x, pos.y, (160, 80, 200),
                    "small", target_id=target_id)
    attacker_cs = world.get_component(attacker_id, CombatState)
    if attacker_cs:
        enter_combat(attacker_cs)
    # Aggro por dano mágico: define aggroed_by_damage=True para que o leash
    # estendido seja aplicado (mob persegue mesmo além do raio normal de detecção)
    _ai = world.get_component(target_id, AIControlled)
    if _ai and _ai.state in ("IDLE", "RETURNING"):
        _ms = world.get_component(target_id, MobSounds)
        SOUNDS.play_mob_sounds(_ms, "aggro", dedup_key=f"dmg_{target_id}")
        _ai.state             = "CHASING"
        _ai.aggroed_by_damage = True
        _ai.path_recalc_timer = 0.0
    if target_cs.current_hp <= 0:
        if not world.get_component(target_id, PendingDeath):
            world.add_component(target_id, PendingDeath(killer_entity_id=attacker_id))
        return True
    return False


# ---------------------------------------------------------------------------
# ManaSystem
# ---------------------------------------------------------------------------

class ManaSystem(System):
    """Regenera mana do jogador periodicamente."""

    REGEN_INTERVAL = 5.0   # segundos entre ticks
    REGEN_OOC_PCT  = 0.04  # 4% max_mana por tick fora de combate
    REGEN_IC_PCT   = 0.01  # 1% max_mana por tick em combate

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        for entity_id, char_stats, _, cs, combat_stats in self.world.get_entities_with(
                CharacterStats, PlayerControlled, CombatState, CombatStats):
            if char_stats.max_mana <= 0:
                continue
            char_stats.mana_regen_timer += dt
            if char_stats.mana_regen_timer >= self.REGEN_INTERVAL:
                char_stats.mana_regen_timer -= self.REGEN_INTERVAL
                in_combat = cs and cs.in_combat
                rate  = self.REGEN_IC_PCT if in_combat else self.REGEN_OOC_PCT
                regen = max(1, int(char_stats.max_mana * rate))
                char_stats.mana = min(char_stats.max_mana, char_stats.mana + regen)

            # Decrementa janela de crits de fogo para Lapso Elemental
            if combat_stats.fire_crit_timer > 0:
                combat_stats.fire_crit_timer -= dt
                if combat_stats.fire_crit_timer <= 0:
                    combat_stats.fire_crit_timer   = 0.0
                    combat_stats.fire_crit_counter = 0

            # Choque Térmico: atualiza indicador de proc a cada frame
            if getattr(combat_stats, "thermal_shock_enabled", False) and cs:
                target_id = cs.target_entity_id
                if target_id != -1:
                    _t_sfx = self.world.get_component(target_id, StatusEffects)
                    char_stats.thermal_shock_active = (
                        _t_sfx is not None and _t_sfx.has("root"))
                else:
                    char_stats.thermal_shock_active = False
            else:
                char_stats.thermal_shock_active = False


# ---------------------------------------------------------------------------
# SpellCastSystem
# ---------------------------------------------------------------------------

class SpellCastSystem(System):
    """Processa a barra de cast e dispara o efeito da magia ao completar.

    Para adicionar nova spell com cast_time: registrar em _CAST_HANDLERS abaixo.
    O método recebe (entity_id, target_id). Para AOE sem alvo, target_id = -1.
    """

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen
        # Dispatch de conclusão de cast — sem if/elif por spell_id.
        # Chave = spell_id do SKILL_CATALOG. Valor = nome do método nesta classe.
        # Adicionar nova spell com cast: inserir entrada aqui.
        self._CAST_HANDLERS: dict[str, str] = {
            "bola_de_fogo":    "_launch_fireball",
            "nova_congelante": "_apply_nova_congelante",
            "polimorfia":      "_apply_polymorph",
            "calcinar":        "_apply_calcinar",
        }

    def update(self, events=None, dt: float = 0) -> None:
        for entity_id, spell_cast, combat_state, _ in self.world.get_entities_with(
                SpellCast, CombatState, PlayerControlled):

            # Movimento cancela cast — exceto se spell_cast.interruptible == False
            tm = self.world.get_component(entity_id, TileMovement)
            if tm and tm.is_moving and spell_cast.interruptible:
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False
                SOUNDS.fadeout_skills(300)   # fadeout 0.3s
                WARN.add("Cast interrompido!")
                return

            spell_cast.elapsed += dt
            if spell_cast.elapsed >= spell_cast.cast_time:
                self._complete_cast(entity_id, spell_cast, combat_state)
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False

    def _complete_cast(self, entity_id: int, spell_cast: SpellCast,
                       combat_state: CombatState) -> None:
        # Deduz mana aqui — cast completado com sucesso.
        # Interrupções (silence, interrupt, movimento) removem SpellCast sem chegar aqui.
        if spell_cast.mana_cost > 0:
            char_stats = self.world.get_component(entity_id, CharacterStats)
            if char_stats:
                char_stats.mana = max(0, char_stats.mana - spell_cast.mana_cost)

        # Aplica cooldown da skill ao completar o cast (não no início —
        # cast interrompido não consome cooldown)
        from components import PlayerSkills
        _ps = self.world.get_component(entity_id, PlayerSkills)
        if _ps:
            _sk = _ps.skill_by_id(spell_cast.spell_id)
            if _sk and _sk.cooldown > 0:
                _sk.current_cooldown = _sk.cooldown

        # Dispatch por spell_id — data-driven, sem if/elif
        handler_name = self._CAST_HANDLERS.get(spell_cast.spell_id)
        if handler_name:
            handler = getattr(self, handler_name, None)
            if handler:
                handler(entity_id, spell_cast.target_id)
            else:
                print(f"[WARN] SpellCastSystem: handler '{handler_name}' não encontrado")

    def _launch_fireball(self, attacker_id: int, target_id: int) -> None:
        pos = self.world.get_component(attacker_id, Position)
        target_pos = self.world.get_component(target_id, Position)
        if not pos or not target_pos:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if target_cs and target_cs.current_hp <= 0:
            return
        proj = self.world.create_entity()
        self.world.add_component(proj, Position(pos.x, pos.y, pos.x, pos.y))
        self.world.add_component(proj, PlayerProjectile(
            spell_id="bola_de_fogo",
            attacker_id=attacker_id,
            target_id=target_id,
            speed=300.0,
            dmg_weapon_pct=0.5,
            dmg_sp_coeff=1.0,
            color=(255, 120, 20),
        ))
        LOG.add("Bola de Fogo!", (255, 160, 60))
        SOUNDS.play_spell("bola_de_fogo", "launch")

    def _apply_calcinar(self, attacker_id: int, target_id: int) -> None:
        """Calcinar — hit instantâneo: 50 + 25% SP. Escola fogo. Pode ser castado em movimento."""
        from combat_log import LOG
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        sp = attacker_cs.spell_power if attacker_cs else 0
        dmg = max(1, 50 + int(sp * 0.25))
        _apply_magic_damage(attacker_id, target_id, dmg, self.world)
        LOG.add(f"Calcinar! {dmg} de dano.", (255, 140, 40))
        SOUNDS.play_spell("calcinar", "impact")

    def _apply_nova_congelante(self, attacker_id: int, target_id: int = -1) -> None:
        """AOE: raiz 5s + dano 50% SP em todos os inimigos a 3 tiles."""
        from systems import apply_effect
        from floating_text import FLT
        from combat_log import LOG
        from components import Enemy, AIControlled, TileMovement as _TM, StatusEffects
        from utils import chebyshev

        attacker_tm = self.world.get_component(attacker_id, _TM)
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        if not attacker_tm:
            return

        pl_x, pl_y = attacker_tm.current_tile_x, attacker_tm.current_tile_y
        sp = attacker_cs.spell_power if attacker_cs else 0

        hit = 0
        for eid, _, _, etm, ecs in self.world.get_entities_with(
                Enemy, AIControlled, _TM, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > 3:
                continue
            dmg = max(1, int(sp * 0.5))
            _apply_magic_damage(attacker_id, eid, dmg, self.world)
            apply_effect(self.world, eid, "root", 5.0)
            hit += 1

        # Som toca sempre ao completar o cast — AoE não depende de acertar alvo
        SOUNDS.play_spell("nova_congelante", "impact")
        if hit > 0:
            LOG.add(f"Nova Congelante — {hit} inimigo(s) enraizados.", (100, 180, 255))
        else:
            LOG.add("Nova Congelante — nenhum inimigo no raio.", (100, 180, 255))

    def _apply_polymorph(self, attacker_id: int, target_id: int) -> None:
        from systems import apply_effect
        from floating_text import FLT
        from combat_log import LOG
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return  # alvo morreu durante o cast
        regen_per_tick = max(1, int(target_cs.max_hp * 0.10))
        apply_effect(self.world, target_id, "polymorph",
                     duration=6.0, magnitude=regen_per_tick)
        # Garante que o atacante NÃO retoma auto-ataque após o cast
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            attacker_state.is_pursuing = False
        pos = self.world.get_component(target_id, Position)
        if pos:
            FLT.add("Polimorfizado!", pos.x, pos.y, (160, 80, 200),
                    size="normal", target_id=target_id)
        LOG.add("Polimorfia!", (160, 80, 200))
        SOUNDS.play_spell("polimorfia", "launch")


# ---------------------------------------------------------------------------
# PlayerProjectileSystem
# ---------------------------------------------------------------------------

class PlayerProjectileSystem(System):
    """Move projéteis do jogador e aplica dano ao acertar o alvo."""

    HIT_THRESHOLD = 12.0

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen

    def update(self, events=None, dt: float = 0) -> None:
        to_remove = []
        for proj_id, proj_pos, proj in self.world.get_entities_with(
                Position, PlayerProjectile):
            target_pos = self.world.get_component(proj.target_id, Position)
            target_cs  = self.world.get_component(proj.target_id, CombatStats)

            if not target_pos or (target_cs and target_cs.current_hp <= 0):
                to_remove.append(proj_id)
                continue

            dx   = target_pos.x - proj_pos.x
            dy   = target_pos.y - proj_pos.y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist <= self.HIT_THRESHOLD:
                self._on_hit(proj)
                to_remove.append(proj_id)
            else:
                step = proj.speed * dt
                proj_pos.x += dx / dist * step
                proj_pos.y += dy / dist * step

        for pid in to_remove:
            self.world.remove_entity(pid)

    def _on_hit(self, proj: PlayerProjectile) -> None:
        attacker_cs = self.world.get_component(proj.attacker_id, CombatStats)
        target_cs   = self.world.get_component(proj.target_id,   CombatStats)

        # Resolve miss/crit usando a tabela de ataque mágica
        outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "magical")
        if outcome == "miss":
            pos = self.world.get_component(proj.target_id, Position)
            if pos:
                FLT.add("Resistiu!", pos.x, pos.y, (180, 100, 255), "small",
                        target_id=proj.target_id)
            return

        is_crit = (outcome == "crit")
        base_dmg = _spell_damage(proj.attacker_id, self.world,
                                 proj.dmg_weapon_pct, proj.dmg_sp_coeff)
        final_dmg = int(base_dmg * CRITICAL_DAMAGE_MULTIPLIER) if is_crit else base_dmg

        # Determina escola da spell pelo campo school do Skill (sem hardcode de nomes)
        _ps = self.world.get_component(proj.attacker_id, PlayerSkills)
        _sk = _ps.skill_by_id(proj.spell_id) if _ps else None
        _spell_school = _sk.school if _sk else ""

        # Piromaníaco: +X% dano em spells de fogo
        if _spell_school == "fogo" and attacker_cs:
            _pyr = getattr(attacker_cs, "pyromania_bonus", 0.0)
            if _pyr > 0:
                final_dmg = int(final_dmg * (1.0 + _pyr))

        # Crematória: +25% dano de fogo em alvos com menos de 20% de vida
        if _spell_school == "fogo" and attacker_cs:
            if getattr(attacker_cs, "crematoria_enabled", False) and target_cs:
                if target_cs.max_hp > 0 and target_cs.current_hp / target_cs.max_hp < 0.20:
                    final_dmg = int(final_dmg * 1.25)

        # Choque Térmico: dobra dano de fogo em alvos enraizados (Nova Congelante)
        if _spell_school == "fogo" and attacker_cs:
            if getattr(attacker_cs, "thermal_shock_enabled", False):
                _t_sfx = self.world.get_component(proj.target_id, StatusEffects)
                if _t_sfx and _t_sfx.has("root"):
                    final_dmg = int(final_dmg * 2.0)

        _apply_magic_damage(proj.attacker_id, proj.target_id, final_dmg, self.world,
                            is_crit=is_crit)
        SOUNDS.play_spell(proj.spell_id, "impact")

        # Queimaduras Profundas: crit de BdF aplica burn (duração escala com pontos)
        if is_crit and proj.spell_id == "bola_de_fogo" and attacker_cs:
            if getattr(attacker_cs, "fire_burns_on_crit", False):
                from systems import apply_effect
                burn_dmg      = max(1, int(attacker_cs.spell_power * 0.3))
                burn_duration = getattr(attacker_cs, "fire_burn_duration", 3.0)
                apply_effect(self.world, proj.target_id, "burn",
                             duration=burn_duration, magnitude=burn_dmg)

        # Lapso Elemental: conta crits de fogo; 3 dentro de 6s → proc
        if is_crit and _spell_school == "fogo" and attacker_cs:
            attacker_cs.fire_crit_counter += 1
            attacker_cs.fire_crit_timer   = 6.0
            _lapse_bonus = getattr(attacker_cs, "elemental_lapse_crit_bonus", 0.0)
            if attacker_cs.fire_crit_counter >= 3 and _lapse_bonus > 0:
                attacker_cs.fire_crit_counter = 0
                attacker_cs.fire_crit_timer   = 0.0
                from systems import apply_effect
                apply_effect(self.world, proj.attacker_id, "elemental_lapse",
                             duration=5.0, magnitude=0)   # auto-burn via tick
                # Aplica bônus de crit como modifier temporário
                from components import Modifier
                from stat_fns import add_timed_modifier
                _mod = Modifier("crit_rating", _lapse_bonus, "flat")
                add_timed_modifier(attacker_cs, _mod, 5.0, "lapso_elemental")
                LOG.add("Lapso Elemental! +crit por 5s (auto-burn ativo).", (255, 100, 200))
                from floating_text import PROC as _PROC2
                _PROC2.add("Lapso Elemental!", (255, 100, 200))

        # Exaustão: slow progressivo por Bola de Fogo consecutiva
        if proj.spell_id == "bola_de_fogo" and attacker_cs:
            if getattr(attacker_cs, "fire_exhaustion_enabled", False):
                from components import ActiveEffect as _AEX
                _t_sfx = self.world.get_component(proj.target_id, StatusEffects)
                if _t_sfx is None:
                    _t_sfx = StatusEffects()
                    self.world.add_component(proj.target_id, _t_sfx)
                # Incrementa stack (rastreado em magnitude do efeito "exhaustion")
                _exh = _t_sfx.get("exhaustion")
                if _exh:
                    _new_stacks = min(_exh.magnitude + 1, 5)
                    _exh.magnitude = _new_stacks
                    _exh.duration  = 6.0  # refresh
                else:
                    _new_stacks = 1
                    _t_sfx.effects["exhaustion"] = _AEX(
                        effect_type="exhaustion", duration=6.0,
                        magnitude=1, tick_interval=0.0)
                # Slow: começa no 2º stack — 5% por stack acima do 1º
                _slow_pct = (_new_stacks - 1) * 0.05
                if _slow_pct > 0:
                    _slow_mult = 1.0 - _slow_pct
                    _slow = _t_sfx.get("slow")
                    if _slow:
                        _slow.magnitude = min(_slow.magnitude, _slow_mult)  # mantém o mais forte
                        _slow.duration  = 6.0
                    else:
                        _t_sfx.effects["slow"] = _AEX(
                            effect_type="slow", duration=6.0,
                            magnitude=_slow_mult, tick_interval=0.0)

        # Chama Interna: rola proc após qualquer hit de spell de escola fogo
        if _spell_school == "fogo" and attacker_cs:
            _proc_chance = getattr(attacker_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                _char = self.world.get_component(proj.attacker_id, CharacterStats)
                if _char and not _char.fire_instant_ready:
                    _char.fire_instant_ready = True
                    from combat_log import LOG as _LOG
                    _LOG.add("Chama Interna! Próxima Bola de Fogo é instantânea.", (255, 160, 60))
                    from floating_text import PROC as _PROC
                    _PROC.add("Chama Interna!", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for _, pos, proj in self.world.get_entities_with(Position, PlayerProjectile):
            dx = int(pos.x - cam_x)
            dy = int(pos.y - cam_y)
            pygame.draw.circle(self.world_surf, proj.color, (dx, dy), 6)


# ---------------------------------------------------------------------------
# ChannelingSystem
# ---------------------------------------------------------------------------

class ChannelingSystem(System):
    """Processa ticks de dano durante canalização (Calamidade Flamejante)."""

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen

    def update(self, events=None, dt: float = 0) -> None:
        to_finish  = []
        interrupted = []
        for entity_id, channeling, combat_state, char_stats, _ in self.world.get_entities_with(
                Channeling, CombatState, CharacterStats, PlayerControlled):

            # Movimento cancela canalização — verifica intenção de mover (teclas ou clique direito)
            _keys = pygame.key.get_pressed()
            _move_intent = (
                _keys[pygame.K_LEFT] or _keys[pygame.K_a] or
                _keys[pygame.K_RIGHT] or _keys[pygame.K_d] or
                _keys[pygame.K_UP] or _keys[pygame.K_w] or
                _keys[pygame.K_DOWN] or _keys[pygame.K_s] or
                any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 3
                    for e in (events or []))
            )
            if _move_intent:
                interrupted.append(entity_id)
                continue

            channeling.elapsed   += dt
            channeling.last_tick += dt

            if channeling.last_tick >= channeling.tick_interval:
                channeling.last_tick -= channeling.tick_interval
                # Piromaníaco: desconto no custo de mana por tick (escola fogo)
                _pyr_cs  = self.world.get_component(entity_id, CombatStats)
                _pyr_b   = getattr(_pyr_cs, "pyromania_bonus", 0.0) if _pyr_cs else 0.0
                tick_mana = max(0, int(channeling.mana_per_tick * (1.0 - _pyr_b)))
                # Verifica mana
                if char_stats.mana < tick_mana:
                    LOG.add("Mana insuficiente — canalização interrompida.", (180, 100, 255))
                    interrupted.append(entity_id)
                    continue
                char_stats.mana -= tick_mana
                self._apply_tick(entity_id, channeling)

            if channeling.elapsed >= channeling.duration:
                to_finish.append(entity_id)

        for entity_id in interrupted:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            SOUNDS.fadeout_skills(800)   # fadeout 0.8s
            WARN.add("Canalização interrompida!")

        for entity_id in to_finish:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            SOUNDS.fadeout_skills(800)   # fadeout 0.8s ao finalizar naturalmente
            LOG.add("Calamidade Flamejante terminou.", (255, 160, 60))

    def _apply_tick(self, entity_id: int, ch: Channeling) -> None:
        base_dmg  = _spell_damage(entity_id, self.world, ch.dmg_weapon_pct, ch.dmg_sp_coeff)
        attacker_cs = self.world.get_component(entity_id, CombatStats)
        radius_px = ch.radius_tiles * TILE_SIZE
        hit_any = False
        for eid, epos, _, _, ecs in self.world.get_entities_with(
                Position, Enemy, AIControlled, CombatStats):
            if ecs.current_hp <= 0:
                continue
            dx = epos.x - ch.target_x
            dy = epos.y - ch.target_y
            if math.sqrt(dx * dx + dy * dy) > radius_px:
                continue
            tick_dmg = base_dmg
            # Piromaníaco: +X% dano em spells de fogo
            if attacker_cs:
                _pyr = getattr(attacker_cs, "pyromania_bonus", 0.0)
                if _pyr > 0:
                    tick_dmg = int(tick_dmg * (1.0 + _pyr))
            # Crematória: +25% dano em alvos com menos de 20% de vida
            if attacker_cs and getattr(attacker_cs, "crematoria_enabled", False):
                if ecs.max_hp > 0 and ecs.current_hp / ecs.max_hp < 0.20:
                    tick_dmg = int(tick_dmg * 1.25)
            # Choque Térmico: Calamidade Flamejante é escola fogo — dobra em alvos enraizados
            if attacker_cs and getattr(attacker_cs, "thermal_shock_enabled", False):
                _t_sfx = self.world.get_component(eid, StatusEffects)
                if _t_sfx and _t_sfx.has("root"):
                    tick_dmg = int(tick_dmg * 2.0)
            _apply_magic_damage(entity_id, eid, tick_dmg, self.world)
            hit_any = True
            # Slow — magnitude = slow_mult final (StatusEffectSystem sincroniza a cada frame)
            if ch.slow_pct > 0:
                from systems import apply_effect
                apply_effect(self.world, eid, "slow", 2.0, max(0.05, 1.0 - ch.slow_pct))
        if hit_any:
            SOUNDS.play_spell("calamidade_flamejante", "impact")

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for entity_id, ch, _ in self.world.get_entities_with(Channeling, PlayerControlled):
            cx = int(ch.target_x - cam_x)
            cy = int(ch.target_y - cam_y)
            r  = int(ch.radius_tiles * TILE_SIZE)
            pygame.draw.circle(self.world_surf, (255, 160, 60), (cx, cy), r, 2)
            pygame.draw.circle(self.world_surf, (255, 200, 80), (cx, cy), 4)


# ---------------------------------------------------------------------------
# IceBlockSystem
# ---------------------------------------------------------------------------

class IceBlockSystem(System):
    """Processa Bloco de Gelo: imunidade + cura por segundo durante 5s."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        to_finish = []
        for entity_id, ice, combat_state, cs, _ in self.world.get_entities_with(
                IceBlockEffect, CombatState, CombatStats, PlayerControlled):

            ice.elapsed    += dt
            ice.last_heal  += dt

            if ice.last_heal >= ice.heal_interval:
                ice.last_heal -= ice.heal_interval
                heal = max(1, int(cs.max_hp * 0.10))
                cs.current_hp = min(cs.max_hp, cs.current_hp + heal)
                pos = self.world.get_component(entity_id, Position)
                if pos:
                    FLT.add(f"+{heal}", pos.x, pos.y, (80, 200, 255),
                            size="normal", target_id=entity_id)

            if ice.elapsed >= ice.duration:
                to_finish.append(entity_id)

        for entity_id in to_finish:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_stunned = False
                cs.is_immune  = False
            if self.world.get_component(entity_id, IceBlockEffect):
                self.world.remove_component(entity_id, IceBlockEffect)
            LOG.add("Bloco de Gelo terminou.", (100, 180, 255))


# ---------------------------------------------------------------------------
# FireShieldSystem
# ---------------------------------------------------------------------------

class FireShieldSystem(System):
    """Controla a duração do Escudo de Fogo. Retaliation aplicada em CombatSystem."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        to_finish = []
        for entity_id, shield, _ in self.world.get_entities_with(
                FireShieldEffect, PlayerControlled):
            shield.elapsed += dt
            if shield.elapsed >= shield.duration:
                to_finish.append(entity_id)

        for entity_id in to_finish:
            self.world.remove_component(entity_id, FireShieldEffect)
            LOG.add("Escudo de Fogo expirou.", (255, 120, 0))


# ---------------------------------------------------------------------------
# AoeTargetingSystem
# ---------------------------------------------------------------------------

class AoeTargetingSystem(System):
    """Intercepta clique esquerdo quando AOE targeting está ativo para posicionar a magia."""

    def __init__(self, world: World, player_entity: int, screen: pygame.Surface):
        self.world         = world
        self.player_entity = player_entity
        self.world_surf    = screen
        self.hud_surf      = screen

    def _camera_offset(self) -> tuple[float, float]:
        sw, sh = self.world_surf.get_width(), self.world_surf.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - sw / 2, cam_pos.y - sh / 2
        return 0.0, 0.0

    @property
    def is_targeting(self) -> bool:
        return self.world.get_component(self.player_entity, AoeTargeting) is not None

    def _player_world_pos(self) -> "tuple[float, float] | None":
        from components import TileMovement as _TM
        tm = self.world.get_component(self.player_entity, _TM)
        if tm:
            return tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2, \
                   tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2
        pos = self.world.get_component(self.player_entity, Position)
        if pos:
            return pos.x, pos.y
        return None

    def _in_cast_range(self, aoe: AoeTargeting, world_x: float, world_y: float) -> bool:
        if aoe.cast_range_tiles <= 0:
            return True
        pp = self._player_world_pos()
        if pp is None:
            return True
        dx, dy = world_x - pp[0], world_y - pp[1]
        return math.sqrt(dx * dx + dy * dy) <= aoe.cast_range_tiles * TILE_SIZE

    def _walk_toward_range(self, aoe: AoeTargeting) -> None:
        """Auto-move até o tile em que a distância ao alvo pendente entra no alcance."""
        import math as _math
        pp = self._player_world_pos()
        if pp is None:
            return
        tx, ty = aoe.pending_world_x, aoe.pending_world_y
        dx, dy = tx - pp[0], ty - pp[1]
        dist = _math.sqrt(dx * dx + dy * dy)
        if dist < 1:
            return
        stop_dist = (aoe.cast_range_tiles - 0.5) * TILE_SIZE
        ratio = max(0.0, (dist - stop_dist) / dist)
        stop_px = pp[0] + dx * ratio
        stop_py = pp[1] + dy * ratio
        tile_x = int(stop_px / TILE_SIZE)
        tile_y = int(stop_py / TILE_SIZE)
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        if auto:
            auto.ground_target     = (tile_x, tile_y)
            auto.active            = True
            auto.path.clear()
            auto.path_recalc_timer = 0.0

    def update(self, events=None, dt: float = 0) -> None:
        aoe = self.world.get_component(self.player_entity, AoeTargeting)
        if not aoe:
            return

        # Remove componentes marcados como cancelados no frame anterior
        if aoe.cancel_pending:
            self.world.remove_component(self.player_entity, AoeTargeting)
            return

        # Checagem de chegada ao alcance: se estava esperando, verifica todo frame
        if aoe.waiting_for_range:
            if self._in_cast_range(aoe, aoe.pending_world_x, aoe.pending_world_y):
                self._start_channel(aoe, aoe.pending_world_x, aoe.pending_world_y)
                self.world.remove_component(self.player_entity, AoeTargeting)
                return

        if not events:
            return

        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                self.world.remove_component(self.player_entity, AoeTargeting)
                LOG.add("Mira cancelada.", (180, 180, 180))
                return

            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                aoe.cancel_pending = True
                LOG.add("Mira cancelada.", (180, 180, 180))
                return

            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                cam_x, cam_y = self._camera_offset()
                scale   = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                world_x = ev.pos[0] * scale + cam_x
                world_y = ev.pos[1] * scale + cam_y
                if self._in_cast_range(aoe, world_x, world_y):
                    self._start_channel(aoe, world_x, world_y)
                    self.world.remove_component(self.player_entity, AoeTargeting)
                else:
                    aoe.pending_world_x   = world_x
                    aoe.pending_world_y   = world_y
                    aoe.waiting_for_range = True
                    self._walk_toward_range(aoe)
                    LOG.add("Fora do alcance — aproximando...", (220, 120, 60))
                return

    def _start_channel(self, aoe: AoeTargeting, world_x: float, world_y: float) -> None:
        combat_state = self.world.get_component(self.player_entity, CombatState)
        char_stats   = self.world.get_component(self.player_entity, CharacterStats)

        if aoe.spell_id == "calamidade_flamejante":
            if char_stats and char_stats.mana < 10:
                WARN.add("Mana insuficiente")
                return
            self.world.add_component(self.player_entity, Channeling(
                spell_id      = "calamidade_flamejante",
                duration      = 5.0,
                tick_interval = 1.0,
                mana_per_tick = 10,
                target_x      = world_x,
                target_y      = world_y,
                radius_tiles  = 3.0,
                slow_pct      = 0.75,
                dmg_weapon_pct = 0.15,
                dmg_sp_coeff   = 1.0,
            ))
            if combat_state:
                combat_state.is_casting = True
                enter_combat(combat_state)
            SOUNDS.play_skill("skill_calamidade_flamejante")
            LOG.add("Calamidade Flamejante — canalizando!", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        """Desenha o círculo de mira AOE na posição do mouse."""
        aoe = self.world.get_component(self.player_entity, AoeTargeting)
        if not aoe:
            return

        # Converte posição do mouse de coordenadas de tela para coordenadas de world_surf.
        # Com zoom > 1, world_surf é menor que a tela — o mouse precisa ser escalonado.
        sx, sy = pygame.mouse.get_pos()
        scale  = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
        mx = int(sx * scale)
        my = int(sy * scale)

        world_x = mx + cam_x
        world_y = my + cam_y
        in_range = self._in_cast_range(aoe, world_x, world_y)
        ring_col   = (255, 200,  80) if in_range else (220,  60,  60)
        center_col = (255, 220, 100) if in_range else (255, 100, 100)
        r = int(aoe.radius_tiles * TILE_SIZE)
        pygame.draw.circle(self.world_surf, ring_col,   (mx, my), r, 2)
        pygame.draw.circle(self.world_surf, center_col, (mx, my), 4)
        # Círculo de alcance máximo ao redor do player (só quando targeting ativo)
        if aoe.cast_range_tiles > 0:
            pp = self._player_world_pos()
            if pp:
                scr_px = int(pp[0] - cam_x)
                scr_py = int(pp[1] - cam_y)
                range_r = int(aoe.cast_range_tiles * TILE_SIZE)
                range_surf = pygame.Surface((range_r * 2, range_r * 2), pygame.SRCALPHA)
                pygame.draw.circle(range_surf, (255, 255, 255, 30),
                                   (range_r, range_r), range_r)
                pygame.draw.circle(range_surf, (200, 200, 200, 80),
                                   (range_r, range_r), range_r, 1)
                self.world_surf.blit(range_surf, (scr_px - range_r, scr_py - range_r))


# ---------------------------------------------------------------------------
# PirofagiaSystem
# ---------------------------------------------------------------------------

# Cone base apontando para a direita (ângulo 0) em offsets de tile (dx, dy)
_PIRO_CONE = (
    (1,  0),
    (2,  0),
    (3, -1), (3,  0), (3,  1),
    (4, -2), (4, -1), (4,  0), (4,  1), (4,  2),
)


class PirofagiaSystem(System):
    """Pirofagia com mira: segura a tecla para apontar o cone, solta para disparar."""

    def __init__(self, world: World, screen: pygame.Surface):
        self.world      = world
        self.world_surf = screen
        self.hud_surf   = screen
        # Surface pré-alocada para o preenchimento do cone — reutilizada a cada frame
        _max = int(4.5 * TILE_SIZE + 2.5 * TILE_SIZE) + 10
        self._cone_surf = pygame.Surface((_max * 2, _max * 2), pygame.SRCALPHA)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _mouse_screen(self) -> tuple:
        """Coordenadas do mouse em world_surf space (igual AoeTargetingSystem)."""
        sx, sy = pygame.mouse.get_pos()
        scale  = (self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                  if self.world_surf and self.hud_surf else 1.0)
        return int(sx * scale), int(sy * scale)

    def _cone_tiles(self, tile_x: int, tile_y: int, angle: float) -> set:
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        tiles = set()
        for dx, dy in _PIRO_CONE:
            tiles.add((tile_x + round(dx * cos_a - dy * sin_a),
                       tile_y + round(dx * sin_a + dy * cos_a)))
        return tiles

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        to_fire  = []
        to_cancel = []
        for entity_id, aiming in self.world.get_entities_with(PirofagiaAiming):
            aiming.elapsed += dt
            for event in (events or []):
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:   # clique esquerdo → dispara
                        to_fire.append(entity_id)
                    elif event.button == 3: # clique direito → cancela sem disparar
                        to_cancel.append(entity_id)

        for entity_id in to_fire:
            if self.world.get_component(entity_id, PirofagiaAiming):
                self.world.remove_component(entity_id, PirofagiaAiming)
            self._fire_cone(entity_id)

        for entity_id in to_cancel:
            if self.world.get_component(entity_id, PirofagiaAiming):
                self.world.remove_component(entity_id, PirofagiaAiming)
            LOG.add("Pirofagia cancelada.", (180, 80, 30))

    def _fire_cone(self, entity_id: int) -> None:
        from components import Enemy, CharacterStats, PlayerSkills
        from systems import apply_effect

        pos = self.world.get_component(entity_id, Position)
        tm  = self.world.get_component(entity_id, TileMovement)
        cs  = self.world.get_component(entity_id, CombatStats)
        if not pos or not tm or not cs:
            return

        # Verifica e deduz mana ao disparar (não na ativação)
        char_stats = self.world.get_component(entity_id, CharacterStats)
        ps         = self.world.get_component(entity_id, PlayerSkills)
        skill_obj  = ps.skill_by_id("pirofagia") if ps else None
        mana_cost  = skill_obj.mana_cost if skill_obj else 75
        if char_stats:
            if char_stats.mana < mana_cost:
                LOG.add("Mana insuficiente — Pirofagia cancelada.", (255, 100, 30))
                return
            char_stats.mana -= mana_cost
        # Inicia cooldown somente ao disparar
        if skill_obj:
            skill_obj.current_cooldown = skill_obj.cooldown

        # Ângulo do cone baseado na posição atual do mouse
        mx, my = self._mouse_screen()
        # cam_x/cam_y a partir da diferença entre posição world e posição em screen
        # Para não duplicar, calcula diretamente pelo Camera
        cam_x = cam_y = 0.0
        for _, cam, cam_pos in self.world.get_entities_with(Camera, Position):
            lw = self.world_surf.get_width()  if self.world_surf else 1280
            lh = self.world_surf.get_height() if self.world_surf else 720
            cam_x = cam_pos.x - lw / 2
            cam_y = cam_pos.y - lh / 2
            break

        px    = pos.x - cam_x
        py    = pos.y - cam_y
        angle = math.atan2(my - py, mx - px)
        cone  = self._cone_tiles(tm.current_tile_x, tm.current_tile_y, angle)

        hit = 0
        for eid, etm, ecs in self.world.get_entities_with(TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if not self.world.get_component(eid, Enemy):
                continue
            if (etm.current_tile_x, etm.current_tile_y) in cone:
                dmg = max(1, 150 + int(cs.spell_power * 1.50))
                _apply_magic_damage(entity_id, eid, dmg, self.world)
                apply_effect(self.world, eid, "disoriented", 3.0)
                hit += 1

        if hit > 0:
            LOG.add(f"Pirofagia! {hit} alvo(s) atingido(s).", (255, 100, 30))
        else:
            LOG.add("Pirofagia — nenhum alvo no cone.", (255, 100, 30))
        SOUNDS.play_skill("skill_pirofagia")

    # ── Render ───────────────────────────────────────────────────────────────

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for entity_id, _ in self.world.get_entities_with(PirofagiaAiming):
            pos = self.world.get_component(entity_id, Position)
            if pos is None or self.world_surf is None:
                continue

            # Mouse em coordenadas de world_surf (mesmo padrão do AoeTargetingSystem)
            sx, sy = pygame.mouse.get_pos()
            scale  = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
            mx = int(sx * scale)
            my = int(sy * scale)

            # Player em coordenadas de world_surf (eixo do cone)
            px = int(pos.x - cam_x)
            py = int(pos.y - cam_y)

            # Ângulo player → mouse
            angle = math.atan2(my - py, mx - px)
            ca    = math.cos(angle)
            sa    = math.sin(angle)

            L = 4.5 * TILE_SIZE   # comprimento do cone
            W = 2.5 * TILE_SIZE   # meia-largura na boca

            # Vértices: rotação de (L, ±W) em torno do player
            v_top = (int(px + L * ca - W * (-sa)), int(py + L * sa + W * (-ca)))
            v_bot = (int(px + L * ca - W *   sa ), int(py + L * sa + W *   ca ))
            pts   = [(px, py), v_top, v_bot]

            # Preenchimento semi-transparente
            all_x = [px, v_top[0], v_bot[0]]
            all_y = [py, v_top[1], v_bot[1]]
            bx = min(all_x) - 2;  by = min(all_y) - 2
            bw = max(all_x) - bx + 4;  bh = max(all_y) - by + 4
            if bw > 0 and bh > 0:
                # Reutiliza surface pré-alocada — evita alloc por frame
                cx = self._cone_surf.get_width()  // 2
                cy = self._cone_surf.get_height() // 2
                self._cone_surf.fill((0, 0, 0, 0))
                local  = [(px - bx, py - by),
                          (v_top[0] - bx, v_top[1] - by),
                          (v_bot[0] - bx, v_bot[1] - by)]
                pygame.draw.polygon(self._cone_surf, (220, 50, 0, 100), local)
                self.world_surf.blit(self._cone_surf, (bx, by))

            # Contorno opaco e ponto no vértice
            pygame.draw.polygon(self.world_surf, (255, 200, 60), pts, 2)
            pygame.draw.circle(self.world_surf,  (255, 240, 80), (px, py), 5)
