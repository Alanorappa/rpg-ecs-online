"""
status_effects_data.py — Definições de efeitos de estado e helper de aplicação.

  EffectDef   : dados estáticos de cada tipo de efeito (cor, tick, stacks).
  EFFECT_DEFS : registro global de todos os efeitos do jogo.
  apply_effect: função auxiliar para aplicar ou refrescar um efeito em uma entidade.

Separa dados de lógica: StatusEffectSystem (systems.py) consume esses dados;
nenhuma lógica de jogo vive aqui.
"""
from __future__ import annotations
from typing import NamedTuple


class EffectDef(NamedTuple):
    label:         str    # nome exibido em UI / floating text
    color:         tuple  # (R, G, B) para ícone e floating text
    is_buff:       bool   # True = buff, False = debuff
    tick_interval: float  # segundos entre ticks de dano/cura (0.0 = sem tick)
    max_stacks:    int    # máximo de stacks simultâneos por entidade


EFFECT_DEFS: dict[str, EffectDef] = {
    # ── Debuffs de controle ──────────────────────────────────────────────────
    "stun":   EffectDef("Atordoado",    (255, 220,  40), False, 0.0, 1),
    "fear":   EffectDef("Medo",         (180,  60, 220), False, 0.0, 1),
    "root":   EffectDef("Imobilizado",  (140, 100,  40), False, 0.0, 1),
    "slow":   EffectDef("Lento",        (100, 160, 220), False, 0.0, 1),
    # ── Debuffs de dano periódico (DoT) ─────────────────────────────────────
    "poison": EffectDef("Veneno",       ( 80, 200,  40), False, 1.0, 1),
    "bleed":  EffectDef("Sangramento",  (200,  30,  30), False, 1.0, 1),
    "burn":   EffectDef("Queimadura",   (255, 120,   0), False, 1.0, 1),
    # ── Buffs ────────────────────────────────────────────────────────────────
    "enraged": EffectDef("Enfurecido",  (255,  80,   0), True,  0.0, 1),
    "haste":   EffectDef("Acelerado",   (100, 220, 255), True,  0.0, 1),
    "regen":   EffectDef("Regeneração", ( 80, 255, 120), True,  1.0, 1),
}


def apply_effect(
    world,
    entity_id: int,
    effect_type: str,
    duration: float,
    magnitude: float = 0.0,
    tick_interval: float | None = None,
    source_id: int = -1,
) -> None:
    """
    Aplica ou atualiza um efeito ativo em uma entidade.

    Regras:
    - Se o efeito ainda não existir: cria e adiciona à lista da entidade.
    - Se já existir: faz refresh da duração (máximo entre atual e novo) e
      incrementa stacks até max_stacks.
    - Efeito "slow": sincroniza TileMovement.slow_mult imediatamente.
    - Adiciona StatusEffects à entidade automaticamente se necessário.
    """
    from components import StatusEffects, ActiveEffect, TileMovement

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
    effect = ActiveEffect(
        effect_type=effect_type,
        duration=duration,
        magnitude=magnitude,
        tick_interval=resolved_tick,
        source_id=source_id,
    )
    sfx.effects.append(effect)

    # Slow: sincroniza multiplicador de movimento imediatamente
    if effect_type == "slow":
        tm = world.get_component(entity_id, TileMovement)
        if tm:
            tm.slow_mult = magnitude if 0.0 < magnitude < 1.0 else 0.5
