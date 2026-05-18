"""
damage_calculator.py — Matemática pura de combate.

Funções sem efeito colateral e sem acesso ao World. Podem ser testadas
isoladamente. CombatSystem as chama passando os componentes já resolvidos.
"""
from __future__ import annotations
import random

# ── Constantes ────────────────────────────────────────────────────────────────
CRITICAL_DAMAGE_MULTIPLIER = 2.0
RATING_PER_PERCENT         = 20.0    # usado para hit_rating de itens
AVOIDANCE_RATING_PER_PCT   = 1000.0  # 1 parry/dodge_rating = 0.1%
ARMOR_REDUCTION_PER_POINT  = 0.001   # 1 armor = 0.1% de redução de dano
ARMOR_REDUCTION_CAP        = 0.99    # teto: máximo 99% de redução


def resolve_attack_outcome(attacker_stats, target_stats,
                           damage_type: str,
                           extra_crit: float = 0.0) -> tuple:
    """Tabela de ataque.

    Retorna (outcome, block_reduction):
      outcome: 'miss' | 'dodge' | 'parry' | 'crit' | 'block' | 'hit'
      block_reduction: dano flat absorvido pelo bloqueio (0 se não bloqueou)
    """
    rpp = RATING_PER_PERCENT * 100.0

    effective_crit = min(1.0, attacker_stats.crit_rating + extra_crit)

    if damage_type == "magical":
        _acerto = min(100.0, getattr(attacker_stats, "acerto", 75.0))
        resist = max(0.0, (1.0 - _acerto / 100.0) - attacker_stats.hit_rating / rpp)
        if random.random() < resist:
            return 'miss', 0.0
        if random.random() < effective_crit:
            return 'crit', 0.0
        return 'hit', 0.0

    # Físico — tabela sequencial: miss → dodge → parry
    _acerto_base    = getattr(attacker_stats, "acerto", 50.0)
    _standing_rate  = getattr(attacker_stats, "acerto_per_standing_second", 0.0)
    _standing_secs  = getattr(attacker_stats, "standing_seconds", 0.0)
    _standing_bonus = _standing_rate * _standing_secs if _standing_rate > 0 else 0.0
    _acerto = min(100.0, _acerto_base + _standing_bonus)
    # Alvo Fácil: +X% acerto quando alvo está sob CC
    _af_acerto = getattr(attacker_stats, "alvo_facil_acerto", 0)
    if _af_acerto > 0 and getattr(target_stats, "is_crowd_controlled", False):
        _acerto = min(100.0, _acerto + _af_acerto)
    miss_chance  = max(0.0, (1.0 - _acerto / 100.0) - attacker_stats.hit_rating / rpp)
    # Parry/dodge: 1 rating = 0.1% (1/1000)
    dodge_chance = max(0.0, target_stats.dodge_rating / AVOIDANCE_RATING_PER_PCT)
    parry_chance = max(0.0, target_stats.parry_rating / AVOIDANCE_RATING_PER_PCT)

    roll       = random.random()
    cumulative = miss_chance
    if roll < cumulative:
        return 'miss', 0.0
    cumulative += dodge_chance
    if roll < cumulative:
        return 'dodge', 0.0
    cumulative += parry_chance
    if roll < cumulative:
        return 'parry', 0.0

    # Block — roll independente; reduz dano mas não cancela
    block_reduction = 0.0
    block_chance = max(0.0, target_stats.block_rating / rpp)
    if block_chance > 0.0 and random.random() < block_chance:
        block_reduction = target_stats.block_value

    if random.random() < effective_crit:
        return 'crit', block_reduction

    return ('block' if block_reduction > 0.0 else 'hit'), block_reduction


def apply_armor_reduction(damage: float, attacker_stats, target_stats,
                          outcome: str) -> float:
    """Aplica redução de armadura ao dano.

    Regras:
    - Críticos NÃO sofrem redução de armadura.
    - Armor penetration do atacante reduz flat a armor efetiva do alvo.
    - Teto: 99% de redução (mínimo 1% de dano sempre passa).
    """
    if outcome == 'crit':
        return damage   # críticos ignoram armadura

    target_armor  = max(0.0, getattr(target_stats, "armor", 0.0))
    armor_pen     = max(0.0, getattr(attacker_stats, "armor_penetration", 0.0))
    eff_armor     = max(0.0, target_armor - armor_pen)
    reduction     = min(ARMOR_REDUCTION_CAP, eff_armor * ARMOR_REDUCTION_PER_POINT)
    return damage * (1.0 - reduction)


def calculate_base_damage(attacker_stats, damage_type: str,
                          weapon,
                          base_ability_damage: float = 0.0,
                          multiplier: float = 1.0,
                          outcome: str = 'hit',
                          block_reduction: float = 0.0) -> float:
    """Calcula o dano base (sem armadura). A redução de armadura é aplicada
    separadamente em CombatSystem._resolve_damage_modifiers.
    """
    total = base_ability_damage

    if damage_type == "physical":
        if weapon and weapon.damage_min > 0:
            weapon_dmg = random.randint(weapon.damage_min, weapon.damage_max)
            total += attacker_stats.attack_power + weapon_dmg
        else:
            total += attacker_stats.attack_power + random.randint(
                attacker_stats.base_physical_damage,
                attacker_stats.base_physical_damage_max,
            )
    elif damage_type == "physical_fixed":
        pass
    elif damage_type == "magical":
        total += attacker_stats.spell_power + attacker_stats.base_magical_damage
    else:
        return 0.0

    total *= multiplier

    if outcome == 'crit':
        total *= CRITICAL_DAMAGE_MULTIPLIER

    return max(0.0, total - block_reduction)
