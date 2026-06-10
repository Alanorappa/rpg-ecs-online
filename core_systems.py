"""
core_systems.py — Sistemas ECS compartilhados entre cliente e servidor.

Regra: NENHUMA dependência de Pygame aqui.
Sistemas visuais (FLT, LOG, PROC, sons) ficam nos subclasses de cada lado.

Exporta:
  apply_effect()              — aplica/atualiza status effect numa entidade
  StatusEffectSystem          — processa ciclo de vida de status effects (ticks, expiração)
  ServerCombatStateSystem     — in_combat timer + rage decay + HP5 regen (somente servidor)
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

    # Boneco de treino: recebe DoT e slow para testes de dano/talentos,
    # mas não recebe polymorph (transformação não faz sentido num manequim).
    if effect_type == "polymorph":
        from components import TrainingDummy as _TDcheck
        if world.get_component(entity_id, _TDcheck) is not None:
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
                elif sfx.has("disoriented") or sfx.has("polymorph"):
                    # Disoriented/polymorph: 50% da velocidade normal.
                    # Sem guard de CombatStats — aplica a qualquer entidade com
                    # TileMovement+StatusEffects (local, remota, com ou sem CombatStats).
                    tm.slow_mult = 0.5
                else:
                    # Sem slow/disoriented/polymorph ativos: reseta para velocidade normal.
                    # Aplica a TODAS as entidades com TileMovement+StatusEffects — incluindo
                    # mobs remotos online. O slow agora é aplicado localmente via apply_effect
                    # (com duração real do servidor) e expira naturalmente por aqui.
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
        from components import (CombatStats, CombatState, Position,
                                 PlayerControlled, PendingDeath)
        from status_effects_data import EFFECT_DEFS

        cs  = self.world.get_component(eid, CombatStats)
        pos = self.world.get_component(eid, Position)
        if not cs or cs.current_hp <= 0:
            return

        # DoT não pode ser esquivado, aparado ou reduzido por armadura.
        # Entidades imunes (Bloco de Gelo) são a única exceção.
        cst = self.world.get_component(eid, CombatState)
        if cst and cst.is_immune:
            return

        defn  = EFFECT_DEFS.get(effect.effect_type)
        color = defn.color if defn else (255, 255, 255)
        # Dano mínimo garantido de 1 — magnitude nunca resulta em 0
        dmg   = max(1, round(effect.magnitude))

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


# ── ServerCombatStateSystem ───────────────────────────────────────────────────

class ServerCombatStateSystem:
    """Gerencia in_combat timer, rage decay e HP5 regen para players no servidor.

    Subconjunto headless do CombatStateSystem offline (systems.py).
    Skips: stun visual, camuflagem, timed_modifiers, procs — são tratados
    pelo cliente ou não existem no servidor headless.

    Uso:
        sys = ServerCombatStateSystem(world)
        sys.update(world_server._player_eids, dt)
        for ev in sys.hp5_events:
            # ev = {player_eid, old_hp, new_hp, hp_max}
            ...
    """

    RAGE_DECAY_AMOUNT   = 5
    RAGE_DECAY_INTERVAL = 3.0  # s entre cada decaimento (idêntico ao offline)

    def __init__(self, world) -> None:
        self.world = world
        # Populado a cada update(); limpo no início do próximo update().
        # Cada entry: {"player_eid": int, "old_hp": int, "new_hp": int, "hp_max": int}
        self.hp5_events: list[dict] = []

    def update(self, player_eids: dict, dt: float) -> None:
        """Processa todos os players em player_eids (session_id → eid)."""
        self.hp5_events.clear()
        from components import CombatState, CombatStats, CharacterStats

        for _sid, peid in list(player_eids.items()):
            cs   = self.world.get_component(peid, CombatState)
            cst  = self.world.get_component(peid, CombatStats)
            char = self.world.get_component(peid, CharacterStats)
            if not cs or not cst or cst.current_hp <= 0:
                continue

            # ── Timer de in_combat ────────────────────────────────────────────
            if cs.in_combat:
                cs.combat_timer = max(0.0, cs.combat_timer - dt)
                if cs.combat_timer <= 0.0:
                    cs.in_combat    = False
                    cs.is_pursuing  = False
                    cs.combat_timer = 0.0

            # ── Rage decay (apenas fora de combate) ───────────────────────────
            if char and char.rage > 0:
                if not cs.in_combat:
                    char.rage_decay_timer += dt
                    if char.rage_decay_timer >= self.RAGE_DECAY_INTERVAL:
                        char.rage_decay_timer -= self.RAGE_DECAY_INTERVAL
                        char.rage = max(0, char.rage - self.RAGE_DECAY_AMOUNT)
                else:
                    char.rage_decay_timer = 0.0

            # ── HP5 regen (apenas fora de combate, HP < max) ──────────────────
            if not cs.in_combat and cst.current_hp < cst.max_hp:
                hp5_timer = getattr(cst, 'hp5_timer', 0.0) + dt
                if hp5_timer >= 5.0:
                    hp5_timer -= 5.0
                    regen  = max(1, int(cst.max_hp * cst.hp5))
                    old_hp = cst.current_hp
                    cst.current_hp = min(cst.max_hp, cst.current_hp + regen)
                    if cst.current_hp != old_hp:
                        self.hp5_events.append({
                            "player_eid": peid,
                            "old_hp":     old_hp,
                            "new_hp":     cst.current_hp,
                            "hp_max":     cst.max_hp,
                        })
                cst.hp5_timer = hp5_timer
            elif cst.current_hp >= cst.max_hp:
                cst.hp5_timer = 0.0
