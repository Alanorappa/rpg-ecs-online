"""
damage_calculator.py — Matemática pura de combate.

Funções sem acesso ao World. Podem ser testadas isoladamente.
CombatSystem as chama passando os componentes já resolvidos.

Nota: `calculate_base_damage` e `resolve_attack_outcome` usam `random.randint/random`
para dano de arma e avoidance — não são determinísticas. Passe `pre_outcome`
em `deal_damage` ou injete um seed em testes que precisam de resultado fixo.
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
                           extra_crit: float = 0.0,
                           extra_acerto: float = 0.0,
                           extra_block: float = 0.0,
                           extra_avoid: float = 0.0,
                           is_ability: bool = False) -> tuple:
    """Tabela de ataque.

    Retorna (outcome, block_reduction):
      outcome: 'miss' | 'dodge' | 'parry' | 'crit' | 'block' | 'hit'
      block_reduction: dano flat absorvido pelo bloqueio (0 se não bloqueou)

    Unidades dos parâmetros `extra_*` (todos vêm de bônus de skill level,
    ver stats_system.skill_bonus_pct — chamador resolve o valor):
      extra_crit, extra_block, extra_avoid: fração 0.0–1.0 (mesma escala de
        crit_rating/block_rating já normalizado/avoidance rating já dividido).
      extra_acerto: pontos percentuais 0–100 (mesma escala de `acerto`,
        NÃO fração — skill_bonus_pct() retorna fração, multiplique por 100
        antes de passar aqui).
    """
    rpp = RATING_PER_PERCENT * 100.0

    effective_crit = min(1.0, attacker_stats.crit_rating + extra_crit)

    if damage_type == "magical":
        _acerto = min(100.0, getattr(attacker_stats, "acerto", 75.0) + extra_acerto)
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
    _acerto = min(100.0, _acerto_base + _standing_bonus + extra_acerto)
    # Alvo Fácil: +X% acerto quando alvo está sob CC
    _af_acerto = getattr(attacker_stats, "alvo_facil_acerto", 0)
    if _af_acerto > 0 and getattr(target_stats, "is_crowd_controlled", False):
        _acerto = min(100.0, _acerto + _af_acerto)
    # Abilities (skills) não erram por miss — só por dodge/parry do alvo.
    # Auto-attacks podem errar por miss (acerto < 100%).
    miss_chance  = 0.0 if is_ability else max(0.0, (1.0 - _acerto / 100.0) - attacker_stats.hit_rating / rpp)
    # Parry/dodge: 1 rating = 0.1% (1/1000) — válido para auto E abilities.
    # extra_avoid (skill de Defesa do alvo) soma nos dois, igual ao desenho:
    # "aumenta a chance de desviar OU aparar".
    dodge_chance = max(0.0, target_stats.dodge_rating / AVOIDANCE_RATING_PER_PCT + extra_avoid)
    parry_chance = max(0.0, target_stats.parry_rating / AVOIDANCE_RATING_PER_PCT + extra_avoid)

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

    # Block — roll independente; reduz dano mas não cancela.
    # extra_block (skill de Escudo do alvo) soma direto na chance.
    block_reduction = 0.0
    block_chance = max(0.0, target_stats.block_rating / rpp + extra_block)
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


def apply_resistance_reduction(damage: float, resist_pct: float) -> float:
    """Aplica redução de resistência mágica (fogo/gelo/natureza) ao dano.

    Paralela a `apply_armor_reduction`, mas sem exceção de crítico —
    resistência reduz mesmo dano crítico (diferente de armadura física,
    por desenho: resistência é uma defesa "elemental", não física).
    `resist_pct`: fração 0.0–1.0 (ver stats_system.skill_bonus_pct).
    """
    resist_pct = max(0.0, min(1.0, resist_pct))
    return damage * (1.0 - resist_pct)


def calculate_base_damage(attacker_stats, damage_type: str,
                          weapon,
                          base_ability_damage: float = 0.0,
                          multiplier: float = 1.0,
                          outcome: str = 'hit',
                          block_reduction: float = 0.0,
                          extra_dmg_pct: float = 0.0) -> float:
    """Calcula o dano base (sem armadura). A redução de armadura é aplicada
    separadamente em CombatSystem._resolve_damage_modifiers.

    `extra_dmg_pct`: bônus de dano do skill Magic (fração 0.0–0.15, ver
    stats_system.skill_bonus_pct), aplicado SÓ ao dano magical.
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
        pass  # base já setado como base_ability_damage (caller pré-calculou weapon+AP)
              # multiplier, crit e armor ainda se aplicam abaixo
    elif damage_type == "magical":
        total += attacker_stats.spell_power + attacker_stats.base_magical_damage
        total *= (1.0 + extra_dmg_pct)
    else:
        return 0.0

    total *= multiplier

    if outcome == 'crit':
        total *= CRITICAL_DAMAGE_MULTIPLIER

    return max(0.0, total - block_reduction)


# Bônus de damage_multiplier por level do skill da arma equipada (skills físicas)
DMG_MULT_PER_WEAPON_SKILL_LEVEL = 0.01


def ability_physical_damage(world, attacker_id: int, params: dict,
                            extra_mult: float = 0.0) -> float:
    """Dano base de skill FÍSICA — ponto ÚNICO da fórmula (pré-crit/armadura):

        arma_roll × dmg_weapon_pct + AP × (damage_multiplier + 0.01×skill_level + extra_mult)

    - `params`         : skill.params do SKILL_CATALOG (fonte única — handlers
                         NUNCA hardcodeiam multiplicador; classe de bug real:
                         golpe_poderoso com 3.0 fixo ignorando o catálogo)
    - `dmg_weapon_pct` : default 1.0 (skill DE arma soma o dano da arma);
                         0.0 = skill sem arma (ex.: Punho no Queixo)
    - `skill_level`    : level do skill da ARMA EQUIPADA (espada/machado/maça/
                         arco) — trocar de arma muda o bônus; desarmado = 0
    - `extra_mult`     : bônus dinâmicos de talento (ex.: Embalo) somados ao
                         multiplicador

    Usar o retorno com deal_damage(..., "physical_fixed",
    base_ability_damage=resultado, is_ability=True) — crit e armadura
    continuam sendo aplicados depois, no caminho normal.
    """
    from components import CombatStats, Equipment
    from stats_system import weapon_skill_level
    cs = world.get_component(attacker_id, CombatStats)
    if cs is None:
        return 0.0
    eq     = world.get_component(attacker_id, Equipment)
    weapon = eq.slots.get("mainhand") if eq else None

    weapon_pct = params.get("dmg_weapon_pct", 1.0)
    weapon_dmg = 0.0
    skill_lvl  = 0
    if weapon_pct > 0:
        if weapon is not None and getattr(weapon, "damage_min", 0) > 0:
            weapon_dmg = random.randint(weapon.damage_min, weapon.damage_max)
        else:
            weapon_dmg = random.randint(cs.base_physical_damage,
                                        cs.base_physical_damage_max)
        # Bônus de skill level só pra skill que USA a arma — soco (dmg_weapon_pct=0)
        # não escala com o skill da espada equipada.
        skill_lvl = weapon_skill_level(world, attacker_id, weapon)

    mult = (params.get("damage_multiplier", 1.0)
            + DMG_MULT_PER_WEAPON_SKILL_LEVEL * skill_lvl
            + extra_mult)
    return weapon_dmg * weapon_pct + cs.attack_power * mult


def spell_damage(world, attacker_id: int, dmg_weapon_pct: float, sp_coeff: float) -> int:
    """Dano de magia = dano_arma*pct + spell_power*coeff (mínimo 1).

    Fonte única de verdade compartilhada entre spell_system (cliente/offline)
    e spell_completion_processor (servidor). Elimina duplicação entre
    _spell_damage() e _server_spell_damage().
    """
    from components import CombatStats, Equipment
    cs = world.get_component(attacker_id, CombatStats)
    eq = world.get_component(attacker_id, Equipment)
    if not cs:
        return 1
    weapon_dmg = float(cs.base_physical_damage)
    if eq:
        wep = eq.slots.get("mainhand")
        if wep and getattr(wep, "damage_min", 0) and getattr(wep, "damage_max", 0):
            weapon_dmg = (wep.damage_min + wep.damage_max) / 2.0
    return max(1, int(weapon_dmg * dmg_weapon_pct + cs.spell_power * sp_coeff))
