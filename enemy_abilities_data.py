"""
enemy_abilities_data.py — Definições de habilidades especiais de inimigos.

  AbilityDef  : dados estáticos de uma habilidade (efeito, alcance, dano por tick, etc.)
  ABILITY_DEFS: registro global de habilidades por ability_id.

A lista de habilidades de CADA mob fica em mob_definitions.py (campo
"abilities" do MOB_TABLE) — não mais aqui. Cada entrada da lista pode ser só
o ability_id (usa default_cooldown desta tabela) ou (ability_id, cooldown)
pra sobrescrever o cooldown nesse mob específico (ex: Cobra usa poison_bite
com cooldown diferente do Escorpião).

magnitude é MULTIPLICADOR do attack_power do mob que usa a habilidade (não
mais dano flat) — aplicado em EnemyAbilitySystem (systems.py, habilidade
melee) e ProjectileSystem (systems.py, habilidade ranged) no momento de
causar o efeito: dano_por_tick = magnitude × CombatStats.attack_power do
caster. Isso faz o dano da habilidade escalar com level/tier do mob
automaticamente, igual ao dano do auto-attack. Os valores abaixo ainda são
os antigos (dano flat, ex: 25.0) — ajustar para a escala de multiplicador
(ex: 2.0–4.0) antes de usar em produção.

Separação de responsabilidades:
  - Dados ficam aqui (o quê e quanto) + lista por mob em mob_definitions.py.
  - Lógica de cooldown e aplicação ficam em EnemyAbilitySystem (systems.py).
  - Efeitos resultantes ficam em StatusEffectSystem via apply_effect().
"""
from __future__ import annotations
from typing import NamedTuple


class AbilityDef(NamedTuple):
    name:             str    # nome exibido em log / floating text
    effect_type:      str    # chave em EFFECT_DEFS (status_effects_data.py)
    duration:         float  # duração total do efeito em segundos
    magnitude:        float  # multiplicador do attack_power do caster (dano/cura por tick = magnitude × attack_power)
    tick_interval:    float  # segundos entre ticks (substitui o default do EFFECT_DEFS)
    range_tiles:      int    # distância máxima (Chebyshev) para usar a habilidade
    default_cooldown: float = 12.0  # cooldown padrão; mob_definitions.py pode sobrescrever por mob
    # Campos para habilidades ranged (range_tiles > 1) — projétil visual
    proj_color:    tuple = (200, 80, 200)  # cor do projétil de habilidade
    proj_is_arrow: bool  = False           # True → flecha orientada; False → orbe


ABILITY_DEFS: dict[str, AbilityDef] = {
    # Hunter — Flecha Envenenada: projétil ranged que aplica veneno ao acertar
    "poison_arrow": AbilityDef(
        name="Flecha Envenenada",
        effect_type="poison",
        duration=6.0,
        magnitude=2.0,
        tick_interval=2.0,
        range_tiles=5,
        default_cooldown=8.0,
        proj_color=(60, 200, 80),  # verde-venenoso
        proj_is_arrow=True,
    ),
    # Urso — Laceração: 25 dmg/tick a cada 3s por 25s (melee range)
    "lacerate": AbilityDef(
        name="Laceração",
        effect_type="bleed",
        duration=12.0,
        magnitude=2.0,
        tick_interval=3.0,
        range_tiles=1,
        default_cooldown=8.0,
    ),
    # Escorpião / Cobra — Picada Venenosa: veneno corpo-a-corpo
    "poison_bite": AbilityDef(
        name="Mordida Venenosa",
        effect_type="poison",
        duration=12.0,
        magnitude=3.0,
        tick_interval=3.0,
        range_tiles=1,
        default_cooldown=5.0,
    ),
    # Zumbi — Mordida Apodrecida: bleed mais leve, melee
    "rotting_bite": AbilityDef(
        name="Mordida Apodrecida",
        effect_type="bleed",
        duration=12.0,
        magnitude=2.0,
        tick_interval=3.0,
        range_tiles=1,
        default_cooldown=15.0,
    ),
    # Vampiro — Drenar Vida: projétil ranged que drena HP (burn DoT)
    "drain_life": AbilityDef(
        name="Drenar Vida",
        effect_type="burn",
        duration=8.0,
        magnitude=2.0,
        tick_interval=2.0,
        range_tiles=5,
        default_cooldown=5.0,
        proj_color=(130, 0, 220),  # roxo vampírico
        proj_is_arrow=False,
    ),
    # Aranha — Teia: slow + bleed
    "web_bite": AbilityDef(
        name="Teia Venenosa",
        effect_type="poison",
        duration=10.0,
        magnitude=1.0,
        tick_interval=3.0,
        range_tiles=1,
        default_cooldown=12.0,
    ),
}
