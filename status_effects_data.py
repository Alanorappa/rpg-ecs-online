"""
status_effects_data.py — Definições estáticas de efeitos de estado.

  EffectDef   : dados estáticos de cada tipo de efeito (label, cor, tick_interval).
  EFFECT_DEFS : registro global de todos os efeitos do jogo.

Apenas dados — nenhuma lógica de jogo vive aqui.
apply_effect() foi movida para systems.py (StatusEffectSystem.apply).
"""
from __future__ import annotations
from typing import NamedTuple


class EffectDef(NamedTuple):
    label:         str    # nome exibido em UI / floating text
    color:         tuple  # (R, G, B) para ícone e floating text
    is_buff:       bool   # True = buff, False = debuff
    tick_interval: float  # segundos entre ticks de dano/cura (0.0 = sem tick)


EFFECT_DEFS: dict[str, EffectDef] = {
    # ── Debuffs de controle ──────────────────────────────────────────────────
    "stun":   EffectDef("Atordoado",    (255, 220,  40), False, 0.0),
    "fear":   EffectDef("Medo",         (180,  60, 220), False, 0.0),
    "root":   EffectDef("Imobilizado",  (140, 100,  40), False, 0.0),
    "slow":   EffectDef("Lento",        (100, 160, 220), False, 0.0),
    # ── Debuffs de dano periódico (DoT) ─────────────────────────────────────
    "poison": EffectDef("Veneno",       ( 80, 200,  40), False, 1.0),
    "bleed":  EffectDef("Sangramento",  (200,  30,  30), False, 1.0),
    "burn":   EffectDef("Queimadura",   (255, 120,   0), False, 1.0),
    # ── Buffs ────────────────────────────────────────────────────────────────
    "enraged": EffectDef("Enfurecido",  (255,  80,   0), True,  0.0),
    "haste":   EffectDef("Acelerado",   (100, 220, 255), True,  0.0),
    "regen":   EffectDef("Regeneração", ( 80, 255, 120), True,  1.0),
}
