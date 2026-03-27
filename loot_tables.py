# loot_tables.py
"""
Tabelas de drop por tipo e tier de inimigo.
Estrutura de itens refatorada:
  - Armas físicas: damage_min, damage_max, attack_speed (1H ~1.2-1.8s, 2H ~2.5-3.2s)
  - Armas mágicas: spell_power via modifier
  - Escudos: alta armadura + atributos secundários
  - Armaduras: armadura + 1 atributo aleatório
  - Joias: atributos primários e/ou proc ao entrar em combate
  - Proc: {"attribute", "value", "duration", "chance", "label"}
"""
from __future__ import annotations
import random
from components import Item, Modifier

# ---------------------------------------------------------------------------
# Templates de itens
# ---------------------------------------------------------------------------
_T = {
    # ================================================================ COMMON
    "bone_sword": lambda: Item(
        "Espada de Osso", "weapon", "mainhand",
        modifiers=[], rarity="common", value=10,
        damage_min=3, damage_max=8, attack_speed=1.6, subtype="Sword"),

    "cracked_club": lambda: Item(
        "Clava Rachada", "weapon", "mainhand",
        modifiers=[Modifier("stamina", 1)], rarity="common", value=7,
        damage_min=2, damage_max=7, attack_speed=1.8, subtype="Club"),

    "wood_wand": lambda: Item(
        "Varinha de Madeira", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 6)], rarity="common", value=10,
        damage_min=4, damage_max=9, attack_speed=1.4, subtype="Wand"),

    "bone_shield": lambda: Item(
        "Escudo de Osso", "shield", "offhand",
        modifiers=[Modifier("armor", 5), Modifier("stamina", 1)],
        rarity="common", value=8),

    "worn_hood": lambda: Item(
        "Capuz Puído", "armor", "head",
        modifiers=[Modifier("armor", 3), Modifier("stamina", 1)],
        rarity="common", value=5),

    "leather_vest": lambda: Item(
        "Colete de Couro", "armor", "chest",
        modifiers=[Modifier("armor", 4), Modifier("stamina", 2)],
        rarity="common", value=8),

    "bone_shoulders": lambda: Item(
        "Ombreiras de Osso", "armor", "shoulders",
        modifiers=[Modifier("armor", 3), Modifier("stamina", 1)],
        rarity="common", value=5),

    "ragged_gloves": lambda: Item(
        "Luvas Surradas", "armor", "gloves",
        modifiers=[Modifier("armor", 2), Modifier("stamina", 1)],
        rarity="common", value=4),

    "torn_boots": lambda: Item(
        "Botas Rasgadas", "armor", "boots",
        modifiers=[Modifier("armor", 2), Modifier("stamina", 1)],
        rarity="common", value=4),

    "bone_wristguards": lambda: Item(
        "Punhos de Osso", "armor", "wrists",
        modifiers=[Modifier("armor", 2), Modifier("stamina", 1)],
        rarity="common", value=4),

    "tattered_robe": lambda: Item(
        "Robe Surrado", "armor", "chest",
        modifiers=[Modifier("armor", 2), Modifier("spell_power", 3)],
        rarity="common", value=7),

    # ============================================================= UNCOMMON
    "iron_sword": lambda: Item(
        "Espada de Ferro", "weapon", "mainhand",
        modifiers=[Modifier("attack_power", 3)], rarity="uncommon", value=35,
        damage_min=8, damage_max=16, attack_speed=1.4, subtype="Sword"),

    "iron_mace": lambda: Item(
        "Maça de Ferro", "weapon", "mainhand",
        modifiers=[Modifier("stamina", 3)], rarity="uncommon", value=30,
        damage_min=7, damage_max=15, attack_speed=1.7, subtype="Mace"),

    "apprentice_axe": lambda: Item(
        "Machadão do Aprendiz", "weapon", "mainhand",
        modifiers=[Modifier("attack_power", 5)], rarity="uncommon", value=45,
        damage_min=14, damage_max=26, attack_speed=2.6, two_handed=True, subtype="Axe"),

    "arcane_wand": lambda: Item(
        "Varinha Arcana", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 14), Modifier("stamina", 2)],
        rarity="uncommon", value=32,
        damage_min=9, damage_max=18, attack_speed=1.3, subtype="Wand"),

    "iron_shield": lambda: Item(
        "Escudo de Ferro", "shield", "offhand",
        modifiers=[Modifier("armor", 12), Modifier("stamina", 3)],
        rarity="uncommon", value=38),

    "iron_helm": lambda: Item(
        "Elmo de Ferro", "armor", "head",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=28),

    "chain_vest": lambda: Item(
        "Peitoral de Malha", "armor", "chest",
        modifiers=[Modifier("armor", 10), Modifier("stamina", 4)],
        rarity="uncommon", value=38),

    "chain_shoulders": lambda: Item(
        "Ombreiras de Malha", "armor", "shoulders",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=26),

    "leather_gloves": lambda: Item(
        "Luvas de Couro", "armor", "gloves",
        modifiers=[Modifier("armor", 5), Modifier("attack_power", 2)],
        rarity="uncommon", value=22),

    "iron_boots": lambda: Item(
        "Botas de Ferro", "armor", "boots",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=26),

    "chain_wrists": lambda: Item(
        "Punhos de Malha", "armor", "wrists",
        modifiers=[Modifier("armor", 5), Modifier("stamina", 2)],
        rarity="uncommon", value=22),

    "steel_ring": lambda: Item(
        "Anel de Aço", "jewelry", "ring",
        modifiers=[Modifier("attack_power", 4), Modifier("stamina", 2)],
        rarity="uncommon", value=40),

    # ================================================================= RARE
    "shadow_blade": lambda: Item(
        "Lâmina Sombria", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.05), Modifier("attack_power", 5)],
        rarity="rare", value=90,
        damage_min=15, damage_max=27, attack_speed=1.3, subtype="Sword"),

    "war_hammer": lambda: Item(
        "Martelo de Guerra", "weapon", "mainhand",
        modifiers=[Modifier("stamina", 8), Modifier("attack_power", 6)],
        rarity="rare", value=85,
        damage_min=22, damage_max=40, attack_speed=2.9, two_handed=True, subtype="Hammer"),

    "mystic_staff": lambda: Item(
        "Cajado Místico", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 22), Modifier("stamina", 5)],
        rarity="rare", value=80,
        damage_min=18, damage_max=34, attack_speed=2.3, two_handed=True, subtype="Staff"),

    "tower_shield": lambda: Item(
        "Escudo Torre", "shield", "offhand",
        modifiers=[Modifier("armor", 22), Modifier("stamina", 8)],
        rarity="rare", value=95),

    "plate_helm": lambda: Item(
        "Elmo de Placa", "armor", "head",
        modifiers=[Modifier("armor", 14), Modifier("stamina", 8)],
        rarity="rare", value=80),

    "plate_armor": lambda: Item(
        "Armadura de Placa", "armor", "chest",
        modifiers=[Modifier("armor", 18), Modifier("stamina", 10)],
        rarity="rare", value=95),

    "savage_shoulders": lambda: Item(
        "Ombreiras Selvagens", "armor", "shoulders",
        modifiers=[Modifier("armor", 12), Modifier("attack_power", 6)],
        rarity="rare", value=78),

    "arcane_gloves": lambda: Item(
        "Luvas Arcanas", "armor", "gloves",
        modifiers=[Modifier("armor", 9), Modifier("spell_power", 6)],
        rarity="rare", value=72),

    "plate_boots": lambda: Item(
        "Botas de Placa", "armor", "boots",
        modifiers=[Modifier("armor", 12), Modifier("stamina", 7)],
        rarity="rare", value=75),

    "runed_wrists": lambda: Item(
        "Punhos Rúnicos", "armor", "wrists",
        modifiers=[Modifier("armor", 9), Modifier("spell_power", 5)],
        rarity="rare", value=70),

    "ring_power": lambda: Item(
        "Anel do Poder", "jewelry", "ring",
        modifiers=[Modifier("attack_power", 8), Modifier("spell_power", 8)],
        rarity="rare", value=85),

    "amulet_warrior": lambda: Item(
        "Amuleto do Guerreiro", "jewelry", "neck",
        modifiers=[Modifier("stamina", 10), Modifier("attack_power", 5)],
        rarity="rare", value=90,
        proc={"attribute": "attack_power", "value": 24, "duration": 16.0,
              "chance": 0.25, "label": "Fúria do Guerreiro"}),

    # ================================================================= EPIC
    "death_blade": lambda: Item(
        "Lâmina da Morte", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.10), Modifier("attack_power", 10)],
        rarity="epic", value=220,
        damage_min=24, damage_max=42, attack_speed=1.2, subtype="Sword",
        proc={"attribute": "crit_rating", "value": 0.35, "duration": 16.0,
              "chance": 0.15, "label": "Sede de Sangue"}),

    "doom_axe": lambda: Item(
        "Machado da Perdição", "weapon", "mainhand",
        modifiers=[Modifier("attack_power", 14), Modifier("stamina", 12)],
        rarity="epic", value=230,
        damage_min=40, damage_max=72, attack_speed=3.0, two_handed=True, subtype="Axe",
        proc={"attribute": "armor", "value": 50, "duration": 20.0,
              "chance": 0.20, "label": "Carcaça de Ferro"}),

    "lich_scepter": lambda: Item(
        "Cetro do Lich", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 42), Modifier("stamina", 10)],
        rarity="epic", value=215,
        damage_min=30, damage_max=55, attack_speed=1.8, subtype="Scepter"),

    "aegis_shield": lambda: Item(
        "Égide Sagrada", "shield", "offhand",
        modifiers=[Modifier("armor", 35), Modifier("stamina", 14), Modifier("crit_rating", 0.05)],
        rarity="epic", value=240),

    "soul_armor": lambda: Item(
        "Armadura da Alma", "armor", "chest",
        modifiers=[Modifier("armor", 28), Modifier("stamina", 18)],
        rarity="epic", value=225),

    "doom_shoulders": lambda: Item(
        "Ombreiras da Perdição", "armor", "shoulders",
        modifiers=[Modifier("armor", 20), Modifier("attack_power", 14)],
        rarity="epic", value=215),

    "shadow_gloves": lambda: Item(
        "Luvas das Sombras", "armor", "gloves",
        modifiers=[Modifier("armor", 16), Modifier("crit_rating", 0.07)],
        rarity="epic", value=200),

    "death_treads": lambda: Item(
        "Passos da Morte", "armor", "boots",
        modifiers=[Modifier("armor", 20), Modifier("stamina", 12)],
        rarity="epic", value=210),

    "lich_wrists": lambda: Item(
        "Punhos do Lich", "armor", "wrists",
        modifiers=[Modifier("armor", 16), Modifier("spell_power", 12)],
        rarity="epic", value=195),

    "amulet_undying": lambda: Item(
        "Amuleto do Imortal", "jewelry", "neck",
        modifiers=[Modifier("stamina", 20), Modifier("armor", 10)],
        rarity="epic", value=260,
        proc={"attribute": "stamina", "value": 60, "duration": 15.0,
              "chance": 0.30, "label": "Vontade Imortal"}),

    "ring_fury": lambda: Item(
        "Anel da Fúria", "jewelry", "ring",
        modifiers=[Modifier("attack_power", 12), Modifier("crit_rating", 0.05)],
        rarity="epic", value=245,
        proc={"attribute": "attack_power", "value": 50, "duration": 16.0,
              "chance": 0.20, "label": "Fúria Épica"}),
}

# ---------------------------------------------------------------------------
# Tabelas de drop: (enemy_type, tier) → [(factory, chance), ...]
# ---------------------------------------------------------------------------
LOOT_TABLES: dict = {
    ("melee", "normal"): [
        (_T["bone_sword"],       0.01),
        (_T["cracked_club"],     0.01),
        (_T["bone_shield"],      0.01),
        (_T["worn_hood"],        0.05),
        (_T["leather_vest"],     0.05),
        (_T["bone_shoulders"],   0.01),
        (_T["ragged_gloves"],    0.05),
        (_T["torn_boots"],       0.05),
        (_T["bone_wristguards"], 0.05),
    ],
    ("ranged", "normal"): [
        (_T["wood_wand"],        0.01),
        (_T["tattered_robe"],    0.05),
        (_T["worn_hood"],        0.05),
        (_T["bone_shoulders"],   0.01),
        (_T["ragged_gloves"],    0.05),
        (_T["torn_boots"],       0.05),
        (_T["bone_wristguards"], 0.05),
    ],
    ("melee", "elite"): [
        (_T["iron_sword"],       0.01),
        (_T["iron_mace"],        0.01),
        (_T["iron_shield"],      0.01),
        (_T["chain_vest"],       0.05),
        (_T["iron_helm"],        0.05),
        (_T["chain_shoulders"],  0.01),
        (_T["leather_gloves"],   0.05),
        (_T["iron_boots"],       0.05),
        (_T["chain_wrists"],     0.05),
        (_T["steel_ring"],       0.01),
    ],
    ("ranged", "elite"): [
        (_T["arcane_wand"],      0.01),
        (_T["iron_shield"],      0.01),
        (_T["chain_vest"],       0.03),
        (_T["iron_helm"],        0.01),
        (_T["chain_shoulders"],  0.01),
        (_T["leather_gloves"],   0.04),
        (_T["iron_boots"],       0.04),
        (_T["chain_wrists"],     0.02),
        (_T["steel_ring"],       0.01),
    ],
    ("melee", "rare"): [
        (_T["shadow_blade"],     0.01),
        (_T["war_hammer"],       0.01),
        (_T["tower_shield"],     0.01),
        (_T["plate_armor"],      0.05),
        (_T["plate_helm"],       0.05),
        (_T["savage_shoulders"], 0.03),
        (_T["plate_boots"],      0.05),
        (_T["ring_power"],       0.05),
        (_T["amulet_warrior"],   0.05),
    ],
    ("ranged", "rare"): [
        (_T["mystic_staff"],     0.05),
        (_T["tower_shield"],     0.05),
        (_T["plate_armor"],      0.05),
        (_T["plate_helm"],       0.05),
        (_T["arcane_gloves"],    0.05),
        (_T["runed_wrists"],     0.05),
        (_T["plate_boots"],      0.05),
        (_T["ring_power"],       0.05),
        (_T["amulet_warrior"],   0.05),
    ],
    ("melee", "boss"): [
        (_T["death_blade"],      0.15),
        (_T["doom_axe"],         0.05),
        (_T["aegis_shield"],     0.05),
        (_T["soul_armor"],       0.05),
        (_T["doom_shoulders"],   0.05),
        (_T["shadow_gloves"],    0.05),
        (_T["death_treads"],     0.05),
        (_T["amulet_undying"],   0.05),
        (_T["ring_fury"],        0.05),
    ],
    ("ranged", "boss"): [
        (_T["lich_scepter"],     0.05),
        (_T["aegis_shield"],     0.05),
        (_T["soul_armor"],       0.05),
        (_T["doom_shoulders"],   0.05),
        (_T["lich_wrists"],      0.05),
        (_T["death_treads"],     0.05),
        (_T["amulet_undying"],   0.05),
        (_T["ring_fury"],        0.05),
    ],
}

# ---------------------------------------------------------------------------
# Moedas por tier
# ---------------------------------------------------------------------------
COIN_DROPS = {
    "normal": (5,   20),
    "elite":  (20,  55),
    "rare":   (55, 130),
    "boss":   (160, 320),
}


def roll_coins(tier: str) -> int:
    lo, hi = COIN_DROPS.get(tier, (5, 20))
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
# Tabelas de drop por mob específico (2-4 itens, 0.5%-5% chance)
# Prioridade sobre LOOT_TABLES quando o nome do mob é reconhecido.
# ---------------------------------------------------------------------------
# chance: common 3-5%, uncommon 1-3%, rare 0.5-1%, epic 0.5%
MOB_LOOT_TABLES: dict[str, list] = {
    "Aranha": [
        (_T["ragged_gloves"],    0.05),
        (_T["bone_wristguards"], 0.03),
        (_T["worn_hood"],        0.02),
    ],
    "Rato": [
        (_T["torn_boots"],       0.05),
        (_T["ragged_gloves"],    0.03),
        (_T["bone_shield"],      0.01),
    ],
    "Escorpião": [
        (_T["bone_shield"],      0.04),
        (_T["bone_wristguards"], 0.03),
        (_T["worn_hood"],        0.02),
        (_T["leather_vest"],     0.01),
    ],
    "Cobra": [
        (_T["torn_boots"],       0.04),
        (_T["ragged_gloves"],    0.03),
        (_T["leather_vest"],     0.02),
    ],
    "Lobo": [
        (_T["leather_vest"],     0.05),
        (_T["torn_boots"],       0.03),
        (_T["ragged_gloves"],    0.02),
    ],
    "Urso": [
        (_T["leather_vest"],     0.05),
        (_T["bone_shield"],      0.03),
        (_T["cracked_club"],     0.02),
        (_T["worn_hood"],        0.02),
    ],
    "Goblin": [
        (_T["bone_sword"],       0.05),
        (_T["cracked_club"],     0.04),
        (_T["ragged_gloves"],    0.03),
        (_T["torn_boots"],       0.02),
    ],
    "Zumbi": [
        (_T["bone_sword"],       0.04),
        (_T["worn_hood"],        0.03),
        (_T["bone_shield"],      0.02),
        (_T["bone_wristguards"], 0.02),
    ],
    "Orc": [
        (_T["iron_sword"],       0.03),
        (_T["iron_mace"],        0.02),
        (_T["chain_vest"],       0.01),
        (_T["iron_shield"],      0.005),
    ],
    "Troll": [
        (_T["cracked_club"],     0.05),
        (_T["bone_shield"],      0.03),
        (_T["iron_mace"],        0.02),
        (_T["chain_vest"],       0.01),
    ],
    "Elfo": [
        (_T["leather_gloves"],   0.03),
        (_T["iron_boots"],       0.03),
        (_T["arcane_wand"],      0.02),
        (_T["iron_sword"],       0.01),
    ],
    "Minotauro": [
        (_T["apprentice_axe"],   0.03),
        (_T["chain_vest"],       0.02),
        (_T["iron_helm"],        0.02),
        (_T["iron_shield"],      0.01),
    ],
    "Vampiro": [
        (_T["arcane_gloves"],    0.03),
        (_T["runed_wrists"],     0.02),
        (_T["shadow_blade"],     0.01),
        (_T["ring_power"],       0.005),
    ],
    "Dragão": [
        (_T["plate_armor"],      0.02),
        (_T["war_hammer"],       0.02),
        (_T["ring_power"],       0.01),
        (_T["mystic_staff"],     0.005),
    ],
}


def roll_mob_loot(mob_name: str, tier: str) -> list:
    """Rola drops usando a tabela específica do mob. Fallback para LOOT_TABLES."""
    table = MOB_LOOT_TABLES.get(mob_name)
    if table is None:
        # mob desconhecido — usa tabela genérica
        return roll_loot("melee", tier)

    # Multiplicador de chance por tier
    tier_mult = {"normal": 1.0, "elite": 1.5, "rare": 2.5, "boss": 4.0}
    mult = tier_mult.get(tier, 1.0)

    result = []
    for factory, chance in table:
        if random.random() < min(chance * mult, 1.0):
            result.append(factory())
    return result[:4]
