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
    # CC estrutural (21/07/2026): fonte única de "este efeito tira o controle
    # do jogador" — engine/utils.py (is_action_locked/is_movement_locked) e
    # server/world_server.py (anti-cheat de movimento bruto) leem DAQUI em
    # vez de manter listas de nomes hardcodadas duplicadas em cada gate; um
    # efeito de CC novo só precisa marcar as flags certas aqui, nunca mais
    # caçar call site por call site (pedido do usuário, ver
    # PROBLEMAS_ARQUITETURA.md — bug real: "stun" já existia como
    # StatusEffects de verdade mas nenhum gate de ação/movimento olhava pra
    # ele, só pra CombatState.is_stunned setado manualmente em 3 lugares
    # sem relação nenhuma com este efeito).
    blocks_move:   bool = False   # bloqueia movimento manual (WASD/clique/perseguição)
    blocks_act:    bool = False   # bloqueia skill/auto-attack


EFFECT_DEFS: dict[str, EffectDef] = {
    # ── Debuffs de controle ──────────────────────────────────────────────────
    "stun":   EffectDef("Atordoado",    (255, 220,  40), False, 0.0, blocks_move=True, blocks_act=True),
    "sleep":  EffectDef("Dormindo",     (160, 200, 255), False, 0.0, blocks_move=True, blocks_act=True),  # imóvel; quebra ao tomar dano
    "fear":   EffectDef("Medo",         (180,  60, 220), False, 0.0, blocks_move=True, blocks_act=True),
    "root":   EffectDef("Imobilizado",  (140, 100,  40), False, 0.0, blocks_move=True),  # pode agir, só não se mover
    "slow":   EffectDef("Lento",        (100, 160, 220), False, 0.0),
    "polymorph":   EffectDef("Polimorfizado",  (160,  80, 200), False, 1.0, blocks_move=True, blocks_act=True),  # tick: regen HP
    "disoriented": EffectDef("Desorientado",   (200, 100, 255), False, 0.0, blocks_move=True, blocks_act=True),  # wander sem regen
    # ── Debuffs de dano periódico (DoT) ─────────────────────────────────────
    "poison": EffectDef("Veneno",       ( 80, 200,  40), False, 1.0),
    "bleed":  EffectDef("Sangramento",  (200,  30,  30), False, 1.0),
    "burn":   EffectDef("Queimadura",   (255, 120,   0), False, 3.0),
    # ── Buffs ────────────────────────────────────────────────────────────────
    "enraged":         EffectDef("Enfurecido",      (255,  80,   0), True,  0.0),
    "elemental_lapse": EffectDef("Lapso Elemental", (255, 100, 200), False, 1.0),  # tick: auto-burn
    "exhaustion":      EffectDef("Exaustão",        (200, 140,  60), False, 0.0),  # rastreia stacks
    "haste":   EffectDef("Acelerado",   (100, 220, 255), True,  0.0),
    "regen":   EffectDef("Regeneração", ( 80, 255, 120), True,  1.0),
}
