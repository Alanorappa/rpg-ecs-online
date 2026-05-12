"""
damage_calculator.py — Matemática pura de combate.

Funções sem efeito colateral e sem acesso ao World. Podem ser testadas
isoladamente. CombatSystem as chama passando os componentes já resolvidos.
"""
from __future__ import annotations
import random

# ── Constantes ────────────────────────────────────────────────────────────────
CRITICAL_DAMAGE_MULTIPLIER = 2.0
RATING_PER_PERCENT         = 20.0   # 20 pontos de rating = 1% de chance
BASE_MISS_CHANCE           = 0.05   # chance base de erro sem nenhum hit_rating


def resolve_attack_outcome(attacker_stats, target_stats,
                           damage_type: str,
                           extra_crit: float = 0.0) -> tuple:
    """Tabela de ataque estilo WoW.

    Retorna (outcome, block_reduction):
      outcome: 'miss' | 'dodge' | 'parry' | 'crit' | 'block' | 'hit'
      block_reduction: dano flat absorvido pelo bloqueio (0 se não bloqueou)
    """
    rpp = RATING_PER_PERCENT * 100.0  # converte rating → fração

    effective_crit = min(1.0, attacker_stats.crit_rating + extra_crit)

    if damage_type == "magical":
        # Mágico: apenas resistência (miss) e crítico
        resist = max(0.0, BASE_MISS_CHANCE - attacker_stats.hit_rating / rpp)
        if random.random() < resist:
            return 'miss', 0.0
        if random.random() < effective_crit:
            return 'crit', 0.0
        return 'hit', 0.0

    # Físico — tabela sequencial: miss → dodge → parry
    miss_chance  = max(0.0, BASE_MISS_CHANCE - attacker_stats.hit_rating / rpp)
    dodge_chance = max(0.0, target_stats.dodge_rating / rpp)
    parry_chance = max(0.0, target_stats.parry_rating / rpp)

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


def calculate_base_damage(attacker_stats, damage_type: str,
                          weapon,
                          base_ability_damage: float = 0.0,
                          multiplier: float = 1.0,
                          outcome: str = 'hit',
                          block_reduction: float = 0.0) -> float:
    """Calcula o dano final dado um outcome já resolvido.

    Args:
        attacker_stats:     CombatStats do atacante (stats efetivos).
        damage_type:        "physical" | "physical_fixed" | "magical".
        weapon:             item equipado na mainhand, ou None.
        base_ability_damage: dano base da habilidade (0 para auto-ataque).
        multiplier:         multiplicador extra (ex: 1.5 para golpe poderoso).
        outcome:            resultado da tabela de ataque.
        block_reduction:    dano absorvido pelo bloqueio.

    Retorna o dano final como float (pode ser 0).
    """
    total = base_ability_damage

    if damage_type == "physical":
        if weapon and weapon.damage_min > 0:
            weapon_dmg = random.randint(weapon.damage_min, weapon.damage_max)
            total += attacker_stats.attack_power + weapon_dmg
        else:
            # Sem arma: rolagem de dados entre min e max (min==max = flat)
            total += attacker_stats.attack_power + random.randint(
                attacker_stats.base_physical_damage,
                attacker_stats.base_physical_damage_max,
            )
    elif damage_type == "physical_fixed":
        # base_ability_damage já é o valor final calculado pelo handler da skill
        pass
    elif damage_type == "magical":
        total += attacker_stats.spell_power + attacker_stats.base_magical_damage
    else:
        return 0.0

    total *= multiplier

    if outcome == 'crit':
        total *= CRITICAL_DAMAGE_MULTIPLIER

    return max(0.0, total - block_reduction)
