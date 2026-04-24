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

import pygame

from world import World
from systems import System
from components import (
    Position, PlayerControlled, CombatState, CombatStats, CharacterStats,
    Equipment, Enemy, AIControlled, TileMovement, StatusEffects, Camera,
    SpellCast, Channeling, IceBlockEffect, PlayerProjectile, AoeTargeting,
    PendingDeath, PlayerAutoMove,
)
from tileset import TILE_SIZE
from utils import chebyshev
from combat_log import LOG
from floating_text import FLT, WARN
from sound_manager import SOUNDS


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


def _apply_magic_damage(attacker_id: int, target_id: int, dmg: int, world: World) -> bool:
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
        FLT.add(f"-{dmg}", pos.x, pos.y, (180, 100, 255), size="normal", target_id=target_id)
    attacker_cs = world.get_component(attacker_id, CombatState)
    if attacker_cs:
        attacker_cs.enter_combat()
    # Aggro imediato: qualquer dano mágico do player força inimigos ociosos a perseguir
    _ai = world.get_component(target_id, AIControlled)
    if _ai and _ai.state in ("IDLE", "RETURNING"):
        _ai.state             = "CHASING"
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
        for entity_id, char_stats, _, cs in self.world.get_entities_with(
                CharacterStats, PlayerControlled, CombatState):
            if char_stats.max_mana <= 0:
                continue
            char_stats.mana_regen_timer += dt
            if char_stats.mana_regen_timer >= self.REGEN_INTERVAL:
                char_stats.mana_regen_timer -= self.REGEN_INTERVAL
                in_combat = cs and cs.in_combat
                rate  = self.REGEN_IC_PCT if in_combat else self.REGEN_OOC_PCT
                regen = max(1, int(char_stats.max_mana * rate))
                char_stats.mana = min(char_stats.max_mana, char_stats.mana + regen)


# ---------------------------------------------------------------------------
# SpellCastSystem
# ---------------------------------------------------------------------------

class SpellCastSystem(System):
    """Processa a barra de cast e dispara o efeito da magia ao completar."""

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.screen = screen

    def update(self, events=None, dt: float = 0) -> None:
        for entity_id, spell_cast, combat_state, _ in self.world.get_entities_with(
                SpellCast, CombatState, PlayerControlled):

            # Movimento cancela cast
            tm = self.world.get_component(entity_id, TileMovement)
            if tm and tm.is_moving:
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False
                WARN.add("Cast interrompido!")
                return

            spell_cast.elapsed += dt
            if spell_cast.elapsed >= spell_cast.cast_time:
                self._complete_cast(entity_id, spell_cast, combat_state)
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False

    def _complete_cast(self, entity_id: int, spell_cast: SpellCast,
                       combat_state: CombatState) -> None:
        sid = spell_cast.spell_id
        if sid == "bola_de_fogo":
            self._launch_fireball(entity_id, spell_cast.target_id)

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


# ---------------------------------------------------------------------------
# PlayerProjectileSystem
# ---------------------------------------------------------------------------

class PlayerProjectileSystem(System):
    """Move projéteis do jogador e aplica dano ao acertar o alvo."""

    HIT_THRESHOLD = 12.0

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.screen = screen

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
        dmg = _spell_damage(proj.attacker_id, self.world,
                            proj.dmg_weapon_pct, proj.dmg_sp_coeff)
        _apply_magic_damage(proj.attacker_id, proj.target_id, dmg, self.world)
        SOUNDS.play_spell(proj.spell_id, "impact")

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for _, pos, proj in self.world.get_entities_with(Position, PlayerProjectile):
            dx = int(pos.x - cam_x)
            dy = int(pos.y - cam_y)
            pygame.draw.circle(self.screen, proj.color, (dx, dy), 6)


# ---------------------------------------------------------------------------
# ChannelingSystem
# ---------------------------------------------------------------------------

class ChannelingSystem(System):
    """Processa ticks de dano durante canalização (Calamidade Flamejante)."""

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.screen = screen

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
                # Verifica mana
                if char_stats.mana < channeling.mana_per_tick:
                    LOG.add("Mana insuficiente — canalização interrompida.", (180, 100, 255))
                    interrupted.append(entity_id)
                    continue
                char_stats.mana -= channeling.mana_per_tick
                self._apply_tick(entity_id, channeling)

            if channeling.elapsed >= channeling.duration:
                to_finish.append(entity_id)

        for entity_id in interrupted:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            WARN.add("Canalização interrompida!")

        for entity_id in to_finish:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            LOG.add("Calamidade Flamejante terminou.", (255, 160, 60))

    def _apply_tick(self, entity_id: int, ch: Channeling) -> None:
        dmg = _spell_damage(entity_id, self.world, ch.dmg_weapon_pct, ch.dmg_sp_coeff)
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
            _apply_magic_damage(entity_id, eid, dmg, self.world)
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
            pygame.draw.circle(self.screen, (255, 160, 60), (cx, cy), r, 2)
            pygame.draw.circle(self.screen, (255, 200, 80), (cx, cy), 4)


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
# AoeTargetingSystem
# ---------------------------------------------------------------------------

class AoeTargetingSystem(System):
    """Intercepta clique esquerdo quando AOE targeting está ativo para posicionar a magia."""

    def __init__(self, world: World, player_entity: int, screen: pygame.Surface):
        self.world         = world
        self.player_entity = player_entity
        self.screen        = screen

    def _camera_offset(self) -> tuple[float, float]:
        sw, sh = self.screen.get_width(), self.screen.get_height()
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
                world_x = ev.pos[0] + cam_x
                world_y = ev.pos[1] + cam_y
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
                combat_state.enter_combat()
            SOUNDS.play_spell("calamidade_flamejante", "cast")
            LOG.add("Calamidade Flamejante — canalizando!", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        """Desenha o círculo de mira AOE na posição do mouse."""
        aoe = self.world.get_component(self.player_entity, AoeTargeting)
        if not aoe:
            return
        mx, my = pygame.mouse.get_pos()
        world_x = mx + cam_x
        world_y = my + cam_y
        in_range = self._in_cast_range(aoe, world_x, world_y)
        ring_col   = (255, 200,  80) if in_range else (220,  60,  60)
        center_col = (255, 220, 100) if in_range else (255, 100, 100)
        r = int(aoe.radius_tiles * TILE_SIZE)
        pygame.draw.circle(self.screen, ring_col,   (mx, my), r, 2)
        pygame.draw.circle(self.screen, center_col, (mx, my), 4)
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
                self.screen.blit(range_surf, (scr_px - range_r, scr_py - range_r))
