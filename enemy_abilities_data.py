"""
enemy_abilities_data.py — Definições de habilidades especiais de inimigos.

  AbilityDef  : dados estáticos de uma habilidade (efeito, alcance, dano por tick, etc.)
  ABILITY_DEFS: registro global de habilidades por ability_id.
  MOB_ABILITIES: mapeamento race/entity_class → [(ability_id, cooldown), ...]

Separação de responsabilidades:
  - Dados ficam aqui (o quê e quanto).
  - Lógica de cooldown e aplicação ficam em EnemyAbilitySystem (systems.py).
  - Efeitos resultantes ficam em StatusEffectSystem via apply_effect().
"""
from __future__ import annotations
from typing import NamedTuple


class AbilityDef(NamedTuple):
    name:          str    # nome exibido em log / floating text
    effect_type:   str    # chave em EFFECT_DEFS (status_effects_data.py)
    duration:      float  # duração total do efeito em segundos
    magnitude:     float  # dano/cura por tick
    tick_interval: float  # segundos entre ticks (substitui o default do EFFECT_DEFS)
    range_tiles:   int    # distância máxima (Chebyshev) para usar a habilidade


ABILITY_DEFS: dict[str, AbilityDef] = {
    # Hunter — Mordida Venenosa: 15 dmg/tick a cada 3s por 15s
    "poison_bite": AbilityDef(
        name="Mordida Venenosa",
        effect_type="poison",
        duration=15.0,
        magnitude=15.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
    # Urso — Laceração: 25 dmg/tick a cada 3s por 25s
    "lacerate": AbilityDef(
        name="Laceração",
        effect_type="bleed",
        duration=25.0,
        magnitude=25.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
}

# Mapeamento race ou entity_class → lista de (ability_id, cooldown_segundos).
# A factory de inimigos usa isso para adicionar EnemyAbilities ao criar a entidade.
# Chaves podem ser nomes de raça ("Urso") ou classe de combate ("Hunter").
MOB_ABILITIES: dict[str, list[tuple[str, float]]] = {
    "Hunter": [("poison_bite", 15.0)],  # Goblin, Elfo — qualquer entity_class Hunter
    "Urso":   [("lacerate",    15.0)],  # mob específico pela raça
}
