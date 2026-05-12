# stat_fns.py — funções puras que manipulam components de stats.
# Em ECS, components são dados; lógica de mutação fica aqui.
from __future__ import annotations
from components import CombatStats, CombatState, Modifier, LearnedRecipes


def recalculate_combat_stats(cs: CombatStats) -> None:
    """Recalcula atributos efetivos (base + modificadores)."""
    cs._recalculate_effective_stats()


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
