# loot_tables.py
"""
Tabelas de drop por tipo e tier de inimigo.

Definição dos itens em si mora em item_table.py (catálogo único —
loot/loja compartilham as mesmas factories, sem duplicata). `_T` é mantido
aqui como alias de compatibilidade — todo `from loot_tables import _T`
existente no resto do código continua funcionando sem mudança.

armor_class (apenas item_type == "armor"):
  "placa"  — foco em força/stamina/armadura.  Permitido: guerreiro
  "couro"  — foco em crítico/agilidade.       Permitido: guerreiro, arqueiro
  "tecido" — foco em intelecto/spell_power.   Permitido: guerreiro, arqueiro, mago

Restrições por classe definidas em stats_system.CLASS_ARMOR_ALLOWED.

Gold por mob: `roll_mob_coins(mob_name, tier)` lê `gold_chance`/`gold_min`/
`gold_max` de MOB_TABLE[mob_name] (mob_definitions.py) — sem essa config,
cai no genérico por tier (COIN_DROPS/roll_coins), igual antes.
"""
from __future__ import annotations
import random
from content.item_table import ITEMS as _T

# ---------------------------------------------------------------------------
# Tabelas de drop: (enemy_type, tier) → [(factory, chance), ...]
# ---------------------------------------------------------------------------
LOOT_TABLES: dict = {
    ("melee", "normal"): [
        (_T["bone_sword"],       0.01),
        (_T["cracked_club"],     0.01),
        (_T["bone_shield"],      0.01),
        (_T["iron_coif"],        0.03),
        (_T["iron_breastplate"], 0.03),
        (_T["bone_shoulders"],   0.02),
        (_T["iron_gauntlets"],   0.03),
        (_T["iron_greaves"],     0.03),
        (_T["bone_wristguards"], 0.03),
    ],
    ("ranged", "normal"): [
        (_T["wood_wand"],        0.01),
        (_T["tattered_robe"],    0.04),
        (_T["worn_hood"],        0.04),
        (_T["cloth_shoulders"],  0.03),
        (_T["cloth_gloves"],     0.03),
        (_T["cloth_boots"],      0.03),
        (_T["cloth_wrists"],     0.03),
    ],
    ("melee", "elite"): [
        (_T["iron_sword"],       0.01),
        (_T["iron_mace"],        0.01),
        (_T["iron_shield"],      0.01),
        (_T["iron_helm"],        0.04),
        (_T["chain_vest"],       0.04),
        (_T["chain_shoulders"],  0.03),
        (_T["plate_gauntlets"],  0.03),
        (_T["iron_boots"],       0.04),
        (_T["chain_wrists"],     0.03),
        (_T["steel_ring"],       0.01),
    ],
    ("ranged", "elite"): [
        (_T["arcane_wand"],      0.01),
        (_T["hunter_bow"],       0.01),
        (_T["silk_hood"],        0.03),
        (_T["silk_robe"],        0.03),
        (_T["silk_shoulders"],   0.02),
        (_T["silk_gloves"],      0.02),
        (_T["silk_boots"],       0.02),
        (_T["silk_wrists"],      0.02),
        (_T["hunter_helm"],      0.02),
        (_T["hunter_vest"],      0.02),
        (_T["steel_ring"],       0.01),
    ],
    ("melee", "rare"): [
        (_T["shadow_blade"],     0.01),
        (_T["war_hammer"],       0.01),
        (_T["tower_shield"],     0.01),
        (_T["plate_helm"],       0.04),
        (_T["plate_armor"],      0.04),
        (_T["savage_shoulders"], 0.03),
        (_T["plate_gloves"],     0.03),
        (_T["plate_boots"],      0.03),
        (_T["plate_wrists"],     0.03),
        (_T["ring_power"],       0.02),
        (_T["amulet_warrior"],   0.02),
    ],
    ("ranged", "rare"): [
        (_T["mystic_staff"],     0.02),
        (_T["shadow_bow"],       0.02),
        (_T["arcane_hood"],      0.03),
        (_T["arcane_robe"],      0.03),
        (_T["arcane_shoulders"], 0.02),
        (_T["arcane_gloves"],    0.02),
        (_T["arcane_boots"],     0.02),
        (_T["runed_wrists"],     0.02),
        (_T["stalker_helm"],     0.02),
        (_T["stalker_vest"],     0.02),
        (_T["stalker_gloves"],   0.02),
        (_T["ring_power"],       0.02),
        (_T["amulet_hunter"],    0.02),
        (_T["amulet_arcane"],    0.02),
    ],
    ("melee", "boss"): [
        (_T["death_blade"],      0.15),
        (_T["doom_axe"],         0.05),
        (_T["aegis_shield"],     0.05),
        (_T["soul_armor"],       0.05),
        (_T["doom_shoulders"],   0.05),
        (_T["titan_helm"],       0.05),
        (_T["titan_gloves"],     0.05),
        (_T["death_treads"],     0.05),
        (_T["titan_wrists"],     0.05),
        (_T["amulet_undying"],   0.05),
        (_T["ring_fury"],        0.05),
    ],
    ("ranged", "boss"): [
        (_T["lich_scepter"],     0.05),
        (_T["phantom_bow"],      0.05),
        (_T["aegis_shield"],     0.05),
        (_T["lich_robe"],        0.05),
        (_T["lich_hood"],        0.05),
        (_T["lich_shoulders"],   0.05),
        (_T["lich_gloves"],      0.05),
        (_T["lich_boots"],       0.05),
        (_T["lich_wrists"],      0.05),
        (_T["assassin_vest"],    0.05),
        (_T["assassin_helm"],    0.05),
        (_T["shadow_gloves"],    0.05),
        (_T["amulet_undying"],   0.05),
        (_T["ring_fury"],        0.05),
    ],
}

# ---------------------------------------------------------------------------
# Moedas por tier
# ---------------------------------------------------------------------------
COIN_DROPS = {
    "normal": (0,   20),
    "elite":  (0,  55),
    "rare":   (0, 130),
    "boss":   (160, 3200),
}


def roll_coins(tier: str) -> int:
    lo, hi = COIN_DROPS.get(tier, (5, 20))
    return random.randint(lo, hi)


# Multiplicador de tier — mesma escala usada pra chance de item (roll_mob_loot)
# e agora também pra faixa de gold por mob (roll_mob_coins). Único lugar:
# mudar a escala de um tier específico não exige tocar em duas funções.
_TIER_MULT: dict = {"normal": 1.0, "elite": 1.5, "rare": 2.5, "boss": 4.0}


def roll_mob_coins(mob_name: str, tier: str) -> int:
    """Rola o gold dropado por um mob específico.

    `gold_chance`/`gold_min`/`gold_max` em MOB_TABLE[mob_name]
    (mob_definitions.py) têm prioridade — sem essa configuração (ou mob não
    cadastrado), cai no comportamento genérico por tier (COIN_DROPS via
    roll_coins), igual antes desta função existir.

    `gold_chance` (0.0–1.0, default 1.0): chance de dropar gold NESTE kill —
    0.0 pra mobs que não fazem sentido carregar moedas (Fera: Lobo, Aranha
    etc.). `gold_min`/`gold_max`: faixa na tier "normal" (mesma convenção de
    `attributes` em mob_definitions.py); escalada pelo mesmo `_TIER_MULT` de
    roll_mob_loot pra elite/rare/boss.
    """
    from content.mob_definitions import MOB_TABLE
    mob_def = MOB_TABLE.get(mob_name)
    if not mob_def:
        return roll_coins(tier)

    gold_chance = mob_def.get("gold_chance", 1.0)
    if random.random() >= gold_chance:
        return 0

    gold_min = mob_def.get("gold_min")
    gold_max = mob_def.get("gold_max")
    if gold_min is None or gold_max is None:
        return roll_coins(tier)

    mult = _TIER_MULT.get(tier, 1.0)
    lo = max(0, round(gold_min * mult))
    hi = max(lo, round(gold_max * mult))
    return random.randint(lo, hi)


def roll_loot(enemy_type: str, tier: str) -> list:
    """Rola os drops para um inimigo do tipo/tier dado. Retorna até 4 itens."""
    table = LOOT_TABLES.get((enemy_type, tier), [])
    result = []
    for factory, chance in table:
        if random.random() < chance:
            result.append(factory())
    return result[:4]


# ---------------------------------------------------------------------------
# Drop por mob específico: fonte única é MOB_TABLE[mob_name]["loot"]
# (mob_definitions.py), um dict {item_key: chance} — item_key é a chave
# nesta tabela (_T). Sem entrada / mob não cadastrado → fallback LOOT_TABLES.
# ---------------------------------------------------------------------------
def roll_mob_loot(mob_name: str, tier: str) -> list:
    """Rola drops usando o loot cadastrado do mob (mob_definitions.py).
    Fallback para LOOT_TABLES se o mob não tiver loot cadastrado."""
    from content.mob_definitions import MOB_TABLE
    mob_def  = MOB_TABLE.get(mob_name)
    loot_def = mob_def.get("loot") if mob_def else None
    if not loot_def:
        return roll_loot("melee", tier)

    mult = _TIER_MULT.get(tier, 1.0)

    result = []
    for item_key, chance in loot_def.items():
        factory = _T.get(item_key)
        if factory and random.random() < chance * mult:
            result.append(factory())
    return result[:4]
