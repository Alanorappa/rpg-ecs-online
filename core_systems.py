"""
core_systems.py — Sistemas ECS compartilhados entre cliente e servidor.

Regra: NENHUMA dependência de Pygame aqui.
Sistemas visuais (FLT, LOG, PROC, sons) ficam nos subclasses de cada lado.

Exporta:
  apply_effect()         — aplica/atualiza status effect numa entidade
  StatusEffectSystem     — processa ciclo de vida de status effects (ticks, expiração)
"""
from __future__ import annotations


# ── apply_effect ──────────────────────────────────────────────────────────────

def apply_effect(
    world,
    entity_id: int,
    effect_type: str,
    duration: float,
    magnitude: float = 0.0,
    tick_interval: float | None = None,
    on_expire_effect: str = "",
    on_expire_duration: float = 0.0,
    on_expire_magnitude: float = 0.0,
) -> None:
    """
    Aplica ou atualiza um efeito de estado em uma entidade.

    - Se o efeito não existir: cria e adiciona.
    - Se já existir: refresh pelo maior valor (duração e magnitude).
    - Cria o componente StatusEffects automaticamente se necessário.
    - on_expire_effect: efeito encadeado aplicado quando este expira.
    """
    from components import StatusEffects, ActiveEffect
    from status_effects_data import EFFECT_DEFS

    defn = EFFECT_DEFS.get(effect_type)
    if defn is None:
        return

    sfx = world.get_component(entity_id, StatusEffects)
    if sfx is None:
        sfx = StatusEffects()
        world.add_component(entity_id, sfx)

    existing = sfx.get(effect_type)
    if existing is not None:
        existing.duration  = max(existing.duration, duration)
        existing.magnitude = max(existing.magnitude, magnitude)
        return

    resolved_tick = tick_interval if tick_interval is not None else defn.tick_interval
    sfx.effects[effect_type] = ActiveEffect(
        effect_type=effect_type,
        duration=duration,
        magnitude=magnitude,
        tick_interval=resolved_tick,
        on_expire_effect=on_expire_effect,
        on_expire_duration=on_expire_duration,
        on_expire_magnitude=on_expire_magnitude,
    )


# ── StatusEffectSystem ────────────────────────────────────────────────────────

class StatusEffectSystem:
    """
    Gerencia o ciclo de vida de todos os efeitos de estado (buffs/debuffs).

    Responsabilidades:
      - Decrementar duração de cada ActiveEffect por dt.
      - Disparar dano/cura periódica (poison, bleed, burn, regen).
      - Remover efeitos expirados e encadear on_expire_effect.
      - Sincronizar TileMovement.slow_mult a cada frame.
      - Sincronizar CombatState.is_rooted e is_crowd_controlled.

    Hooks virtuais (sobrescrever em subclasses):
      _emit_damage(eid, amount, effect_type, pos, color)
      _emit_heal  (eid, amount, effect_type, pos, color)

    Servidor: sobrescreve para emitir COMBAT_RESULT.
    Cliente : sobrescreve para chamar FLT.add().
    """

    def __init__(self, world) -> None:
        self.world = world

    # ── Loop principal ─────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        from components import (StatusEffects, TileMovement, CombatState,
                                 CombatStats, Position, PlayerControlled,
                                 PendingDeath)
        from status_effects_data import EFFECT_DEFS

        for eid, sfx in self.world.get_entities_with(StatusEffects):
            if not sfx.effects:
                continue

            to_remove = []
            for effect in list(sfx.effects.values()):
                effect.duration -= dt

                if effect.tick_interval > 0:
                    effect.tick_timer -= dt
                    if effect.tick_timer <= 0:
                        effect.tick_timer += effect.tick_interval
                        self._apply_tick(eid, effect)

                if effect.duration <= 0:
                    to_remove.append((
                        effect.effect_type,
                        effect.on_expire_effect,
                        effect.on_expire_duration,
                        effect.on_expire_magnitude,
                    ))

            for key, expire_eff, expire_dur, expire_mag in to_remove:
                sfx.effects.pop(key, None)
                if expire_eff:
                    apply_effect(self.world, eid, expire_eff, expire_dur,
                                 magnitude=expire_mag)

            # Sincroniza slow_mult no TileMovement
            tm = self.world.get_component(eid, TileMovement)
            if tm:
                slow = sfx.get("slow")
                if slow:
                    tm.slow_mult = slow.magnitude if 0.0 < slow.magnitude < 1.0 else 0.5
                else:
                    tm.slow_mult          = 1.0
                    tm.debilitate_elapsed = 0.0

            # Sincroniza is_rooted
            cst = self.world.get_component(eid, CombatState)
            if cst:
                cst.is_rooted = sfx.has("root")

            # Sincroniza is_crowd_controlled (lido por damage_calculator)
            cs_cc = self.world.get_component(eid, CombatStats)
            if cs_cc:
                _CC = ("stun", "sleep", "fear", "polymorph", "slow",
                       "disoriented", "root")
                cs_cc.is_crowd_controlled = any(sfx.has(e) for e in _CC)

    # ── Tick de dano/cura ──────────────────────────────────────────────────

    def _apply_tick(self, eid: int, effect) -> None:
        from components import (CombatStats, Position, PlayerControlled,
                                 PendingDeath)
        from status_effects_data import EFFECT_DEFS

        cs  = self.world.get_component(eid, CombatStats)
        pos = self.world.get_component(eid, Position)
        if not cs or cs.current_hp <= 0:
            return

        defn  = EFFECT_DEFS.get(effect.effect_type)
        color = defn.color if defn else (255, 255, 255)
        dmg   = max(1, int(effect.magnitude))

        if effect.effect_type == "regen":
            healed = min(dmg, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + dmg)
            if healed > 0:
                self._emit_heal(eid, healed, effect.effect_type, pos, color)

        elif effect.effect_type == "polymorph":
            healed = min(dmg, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + dmg)
            if healed > 0:
                self._emit_heal(eid, healed, effect.effect_type, pos, color)

        elif effect.effect_type == "elemental_lapse":
            burn = max(1, int(cs.max_hp * 0.01))
            cs.current_hp = max(1, cs.current_hp - burn)
            self._emit_damage(eid, burn, effect.effect_type, pos, color)

        else:
            # DoT genérico (poison, bleed, burn, ...)
            cs.current_hp = max(0, cs.current_hp - dmg)
            self._emit_damage(eid, dmg, effect.effect_type, pos, color)

            if cs.current_hp <= 0:
                is_player = self.world.get_component(eid, PlayerControlled) is not None
                if not is_player and not self.world.get_component(eid, PendingDeath):
                    # Tenta atribuir o kill ao player que causou o efeito
                    killer_id = -1
                    for pid, _ in self.world.get_entities_with(PlayerControlled):
                        killer_id = pid
                        break
                    self.world.add_component(eid, PendingDeath(killer_entity_id=killer_id))

    # ── Hooks visuais (sem implementação base) ────────────────────────────

    def _emit_damage(self, eid: int, amount: int, effect_type: str,
                     pos, color: tuple) -> None:
        """Sobrescrever: cliente → FLT; servidor → COMBAT_RESULT."""
        pass

    def _emit_heal(self, eid: int, amount: int, effect_type: str,
                   pos, color: tuple) -> None:
        """Sobrescrever: cliente → FLT; servidor → COMBAT_RESULT."""
        pass
