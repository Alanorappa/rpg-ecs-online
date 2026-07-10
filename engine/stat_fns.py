# stat_fns.py — funções puras que manipulam components de stats.
# Em ECS, components são dados; lógica de mutação fica aqui.
from __future__ import annotations
from engine.components import CombatStats, CombatState, Modifier, LearnedRecipes


# ---------------------------------------------------------------------------
# Tabela de atributos modificáveis — fonte única do recálculo de stats
# ---------------------------------------------------------------------------
# Para adicionar um atributo novo: criar o par base_X/X em CombatStats e
# acrescentar "X" aqui (+ clamp em _STAT_CLAMPS se precisar de limite).
# Antes isso era uma cadeia de ~60 linhas de elif em components.py — cada
# atributo novo exigia editar a cadeia à mão (e esquecer = modifier ignorado
# silenciosamente).
_MODIFIABLE_ATTRS: tuple = (
    "stamina", "armor", "attack_power", "spell_power", "haste_rating",
    "crit_rating", "attack_interval", "hit_rating", "armor_penetration",
    "dodge_rating", "parry_rating", "block_rating", "block_value", "acerto",
)
_MODIFIABLE_SET = frozenset(_MODIFIABLE_ATTRS)

# Escala aplicada ao valor FLAT de um modifier (percentages não escalam).
# stamina: 1 ponto de modifier = 10 HP (mesma regra de vitality).
_FLAT_SCALE: dict = {"stamina": 10.0}

# Clamps pós-recálculo: atributo → (mínimo, máximo). None = sem limite.
_STAT_CLAMPS: dict = {
    "attack_interval":   (0.5, None),   # nunca ataque instantâneo
    "crit_rating":       (0.0, 1.0),    # fração 0..1
    "hit_rating":        (0.0, None),
    "acerto":            (0.0, 100.0),  # percentual 0..100
    "armor_penetration": (0.0, None),
    "dodge_rating":      (0.0, None),
    "parry_rating":      (0.0, None),
    "block_rating":      (0.0, None),
    "block_value":       (0.0, None),
}


def recalculate_combat_stats(cs: CombatStats) -> None:
    """Recalcula atributos efetivos (base + modificadores) — implementação
    data-driven via _MODIFIABLE_ATTRS. Preserva o HP absoluto se a vida
    máxima mudar (limitando ao novo máximo).
    """
    old_current_hp = cs.current_hp

    # Reset: efetivo = base
    for attr in _MODIFIABLE_ATTRS:
        setattr(cs, attr, float(getattr(cs, "base_" + attr)))

    # Aplica modificadores na ordem em que foram adicionados
    for mod in cs.modifiers:
        attr = mod.attribute
        if attr not in _MODIFIABLE_SET:
            continue  # atributo desconhecido: ignorado (mesma semântica antiga)
        cur = getattr(cs, attr)
        if mod.type == "flat":
            cur += mod.value * _FLAT_SCALE.get(attr, 1.0)
        elif mod.type == "percentage":
            cur *= (1 + mod.value)
        setattr(cs, attr, cur)

    # Haste → intervalo de ataque: a cada 10 de haste, -1s de intervalo
    cs.attack_interval -= (cs.haste_rating / 10.0)

    # Clamps
    for attr, (lo, hi) in _STAT_CLAMPS.items():
        v = getattr(cs, attr)
        if lo is not None and v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        setattr(cs, attr, v)

    # Vida máxima deriva da estamina efetiva; preserva HP absoluto
    cs.max_hp = cs._calculate_max_hp()
    cs.current_hp = max(0, min(old_current_hp, cs.max_hp))


def add_modifier(cs: CombatStats, modifier: Modifier) -> None:
    cs.modifiers.append(modifier)
    cs._recalculate_effective_stats()


def remove_modifier(cs: CombatStats, modifier: Modifier) -> None:
    if modifier in cs.modifiers:
        cs.modifiers.remove(modifier)
        cs._recalculate_effective_stats()


def add_timed_modifier(cs: CombatStats, modifier: Modifier, duration: float, label: str = "") -> None:
    """Adiciona modificador temporário; reseta timer se mesmo label já ativo."""
    for entry in cs.timed_modifiers:
        if entry["label"] == label and label:
            entry["timer"] = duration
            return
    cs.timed_modifiers.append({"modifier": modifier, "timer": duration, "label": label})
    add_modifier(cs, modifier)


def enter_combat(state: CombatState) -> None:
    """Coloca entidade em modo combate e reinicia timer."""
    if not state.in_combat:
        state._just_entered_combat = True
    state.in_combat = True
    state.combat_timer = CombatState.OUT_OF_COMBAT_DURATION


def learn_recipe(lr: LearnedRecipes, recipe_id: str) -> bool:
    """Aprende receita. Retorna True se era nova."""
    if recipe_id in lr.known:
        return False
    lr.known.append(recipe_id)
    return True
