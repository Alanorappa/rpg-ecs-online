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
    # Campos para habilidades ranged (range_tiles > 1) — projétil visual
    proj_color:    tuple = (200, 80, 200)  # cor do projétil de habilidade
    proj_is_arrow: bool  = False           # True → flecha orientada; False → orbe


ABILITY_DEFS: dict[str, AbilityDef] = {
    # Hunter — Flecha Envenenada: projétil ranged que aplica veneno ao acertar
    "poison_arrow": AbilityDef(
        name="Flecha Envenenada",
        effect_type="poison",
        duration=15.0,
        magnitude=15.0,
        tick_interval=3.0,
        range_tiles=5,
        proj_color=(60, 200, 80),  # verde-venenoso
        proj_is_arrow=True,
    ),
    # Urso — Laceração: 25 dmg/tick a cada 3s por 25s (melee range)
    "lacerate": AbilityDef(
        name="Laceração",
        effect_type="bleed",
        duration=25.0,
        magnitude=25.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
    # Escorpião / Cobra — Picada Venenosa: veneno corpo-a-corpo
    "poison_bite": AbilityDef(
        name="Mordida Venenosa",
        effect_type="poison",
        duration=12.0,
        magnitude=10.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
    # Zumbi — Mordida Apodrecida: bleed mais leve, melee
    "rotting_bite": AbilityDef(
        name="Mordida Apodrecida",
        effect_type="bleed",
        duration=12.0,
        magnitude=12.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
    # Vampiro — Drenar Vida: projétil ranged que drena HP (burn DoT)
    "drain_life": AbilityDef(
        name="Drenar Vida",
        effect_type="burn",
        duration=8.0,
        magnitude=20.0,
        tick_interval=2.0,
        range_tiles=5,
        proj_color=(130, 0, 220),  # roxo vampírico
        proj_is_arrow=False,
    ),
    # Aranha — Teia: slow + bleed
    "web_bite": AbilityDef(
        name="Teia Venenosa",
        effect_type="poison",
        duration=10.0,
        magnitude=8.0,
        tick_interval=3.0,
        range_tiles=1,
    ),
}

# Mapeamento race ou entity_class → lista de (ability_id, cooldown_segundos).
# A factory de inimigos usa isso para adicionar EnemyAbilities ao criar a entidade.
# Chaves podem ser nomes de raça ("Urso") ou classe de combate ("Hunter").
MOB_ABILITIES: dict[str, list[tuple[str, float]]] = {
    "Hunter":  [("poison_arrow", 12.0)],  # Goblin, Elfo — ranged poison (range=5)
    "Urso":    [("lacerate",     15.0)],  # Urso — melee bleed
    "Escorpião": [("poison_bite", 12.0)], # Escorpião — melee poison
    "Cobra":   [("poison_bite",  10.0)],  # Cobra — melee poison (faster CD)
    "Zumbi":   [("rotting_bite", 18.0)],  # Zumbi — melee bleed
    "Vampiro": [("drain_life",   10.0)],  # Vampiro — ranged burn (draining)
    "Aranha":  [("web_bite",     14.0)],  # Aranha — melee poison
}
