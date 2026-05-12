# loot_tables.py
"""
Tabelas de drop por tipo e tier de inimigo.

armor_class (apenas item_type == "armor"):
  "placa"  — foco em força/stamina/armadura.  Permitido: guerreiro
  "couro"  — foco em crítico/agilidade.       Permitido: guerreiro, arqueiro
  "tecido" — foco em intelecto/spell_power.   Permitido: guerreiro, arqueiro, mago

Restrições por classe definidas em stats_system.CLASS_ARMOR_ALLOWED.
"""
from __future__ import annotations
import random
from components import Item, Modifier

# ---------------------------------------------------------------------------
# Templates de itens
# ---------------------------------------------------------------------------
_T = {

    # ================================================================ WEAPONS (sem armor_class)
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

    "hunter_bow": lambda: Item(
        "Arco do Caçador", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.04), Modifier("attack_power", 4)],
        rarity="uncommon", value=38,
        damage_min=10, damage_max=20, attack_speed=2.0, two_handed=True, subtype="Sword"),

    "arcane_wand": lambda: Item(
        "Varinha Arcana", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 14), Modifier("stamina", 2)],
        rarity="uncommon", value=32,
        damage_min=9, damage_max=18, attack_speed=1.3, subtype="Wand"),

    "iron_shield": lambda: Item(
        "Escudo de Ferro", "shield", "offhand",
        modifiers=[Modifier("armor", 12), Modifier("stamina", 3)],
        rarity="uncommon", value=38),

    "steel_ring": lambda: Item(
        "Anel de Aço", "jewelry", "ring",
        modifiers=[Modifier("attack_power", 4), Modifier("stamina", 2)],
        rarity="uncommon", value=40),

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

    "shadow_bow": lambda: Item(
        "Arco das Sombras", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.08), Modifier("attack_power", 8)],
        rarity="rare", value=88,
        damage_min=18, damage_max=34, attack_speed=1.9, two_handed=True, subtype="Sword"),

    "tower_shield": lambda: Item(
        "Escudo Torre", "shield", "offhand",
        modifiers=[Modifier("armor", 22), Modifier("stamina", 8)],
        rarity="rare", value=95),

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

    "amulet_hunter": lambda: Item(
        "Amuleto do Caçador", "jewelry", "neck",
        modifiers=[Modifier("crit_rating", 0.06), Modifier("stamina", 6)],
        rarity="rare", value=88,
        proc={"attribute": "crit_rating", "value": 0.20, "duration": 12.0,
              "chance": 0.20, "label": "Olho de Águia"}),

    "amulet_arcane": lambda: Item(
        "Amuleto Arcano", "jewelry", "neck",
        modifiers=[Modifier("spell_power", 14), Modifier("stamina", 5)],
        rarity="rare", value=85,
        proc={"attribute": "spell_power", "value": 30, "duration": 12.0,
              "chance": 0.20, "label": "Surto Arcano"}),

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

    "phantom_bow": lambda: Item(
        "Arco Fantasma", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.12), Modifier("attack_power", 14)],
        rarity="epic", value=225,
        damage_min=30, damage_max=56, attack_speed=1.8, two_handed=True, subtype="Sword",
        proc={"attribute": "crit_rating", "value": 0.40, "duration": 12.0,
              "chance": 0.15, "label": "Tiro Fantasma"}),

    "lich_scepter": lambda: Item(
        "Cetro do Lich", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 42), Modifier("stamina", 10)],
        rarity="epic", value=215,
        damage_min=30, damage_max=55, attack_speed=1.8, subtype="Scepter"),

    "aegis_shield": lambda: Item(
        "Égide Sagrada", "shield", "offhand",
        modifiers=[Modifier("armor", 35), Modifier("stamina", 14), Modifier("crit_rating", 0.05)],
        rarity="epic", value=240),

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

    # ================================================================ PLACA — Guerreiro
    # COMMON placa
    "bone_shoulders": lambda: Item(
        "Ombreiras de Osso", "armor", "shoulders",
        modifiers=[Modifier("armor", 3), Modifier("stamina", 1)],
        rarity="common", value=5, armor_class="placa"),

    "bone_wristguards": lambda: Item(
        "Punhos de Osso", "armor", "wrists",
        modifiers=[Modifier("armor", 2), Modifier("stamina", 1)],
        rarity="common", value=4, armor_class="placa"),

    "iron_breastplate": lambda: Item(
        "Peitoral de Ferro", "armor", "chest",
        modifiers=[Modifier("armor", 5), Modifier("stamina", 2)],
        rarity="common", value=9, armor_class="placa"),

    "iron_greaves": lambda: Item(
        "Grevas de Ferro", "armor", "boots",
        modifiers=[Modifier("armor", 3), Modifier("stamina", 1)],
        rarity="common", value=5, armor_class="placa"),

    "iron_gauntlets": lambda: Item(
        "Manoplas de Ferro", "armor", "gloves",
        modifiers=[Modifier("armor", 3), Modifier("attack_power", 1)],
        rarity="common", value=5, armor_class="placa"),

    "iron_coif": lambda: Item(
        "Coifa de Ferro", "armor", "head",
        modifiers=[Modifier("armor", 3), Modifier("stamina", 1)],
        rarity="common", value=5, armor_class="placa"),

    # UNCOMMON placa
    "iron_helm": lambda: Item(
        "Elmo de Ferro", "armor", "head",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=28, armor_class="placa"),

    "chain_vest": lambda: Item(
        "Peitoral de Malha", "armor", "chest",
        modifiers=[Modifier("armor", 10), Modifier("stamina", 4)],
        rarity="uncommon", value=38, armor_class="placa"),

    "chain_shoulders": lambda: Item(
        "Ombreiras de Malha", "armor", "shoulders",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=26, armor_class="placa"),

    "iron_boots": lambda: Item(
        "Botas de Ferro", "armor", "boots",
        modifiers=[Modifier("armor", 7), Modifier("stamina", 3)],
        rarity="uncommon", value=26, armor_class="placa"),

    "chain_wrists": lambda: Item(
        "Punhos de Malha", "armor", "wrists",
        modifiers=[Modifier("armor", 5), Modifier("stamina", 2)],
        rarity="uncommon", value=22, armor_class="placa"),

    "plate_gauntlets": lambda: Item(
        "Manoplas de Placa", "armor", "gloves",
        modifiers=[Modifier("armor", 6), Modifier("attack_power", 3)],
        rarity="uncommon", value=24, armor_class="placa"),

    # RARE placa
    "plate_helm": lambda: Item(
        "Elmo de Placa", "armor", "head",
        modifiers=[Modifier("armor", 14), Modifier("stamina", 8)],
        rarity="rare", value=80, armor_class="placa"),

    "plate_armor": lambda: Item(
        "Armadura de Placa", "armor", "chest",
        modifiers=[Modifier("armor", 18), Modifier("stamina", 10)],
        rarity="rare", value=95, armor_class="placa"),

    "savage_shoulders": lambda: Item(
        "Ombreiras Selvagens", "armor", "shoulders",
        modifiers=[Modifier("armor", 12), Modifier("attack_power", 6)],
        rarity="rare", value=78, armor_class="placa"),

    "plate_boots": lambda: Item(
        "Botas de Placa", "armor", "boots",
        modifiers=[Modifier("armor", 12), Modifier("stamina", 7)],
        rarity="rare", value=75, armor_class="placa"),

    "plate_wrists": lambda: Item(
        "Punhos de Placa", "armor", "wrists",
        modifiers=[Modifier("armor", 9), Modifier("stamina", 5)],
        rarity="rare", value=68, armor_class="placa"),

    "plate_gloves": lambda: Item(
        "Luvas de Placa", "armor", "gloves",
        modifiers=[Modifier("armor", 9), Modifier("attack_power", 5)],
        rarity="rare", value=70, armor_class="placa"),

    # EPIC placa
    "soul_armor": lambda: Item(
        "Armadura da Alma", "armor", "chest",
        modifiers=[Modifier("armor", 28), Modifier("stamina", 18)],
        rarity="epic", value=225, armor_class="placa"),

    "doom_shoulders": lambda: Item(
        "Ombreiras da Perdição", "armor", "shoulders",
        modifiers=[Modifier("armor", 20), Modifier("attack_power", 14)],
        rarity="epic", value=215, armor_class="placa"),

    "death_treads": lambda: Item(
        "Passos da Morte", "armor", "boots",
        modifiers=[Modifier("armor", 20), Modifier("stamina", 12)],
        rarity="epic", value=210, armor_class="placa"),

    "titan_helm": lambda: Item(
        "Elmo do Titã", "armor", "head",
        modifiers=[Modifier("armor", 22), Modifier("stamina", 16)],
        rarity="epic", value=218, armor_class="placa"),

    "titan_wrists": lambda: Item(
        "Punhos do Titã", "armor", "wrists",
        modifiers=[Modifier("armor", 16), Modifier("stamina", 10)],
        rarity="epic", value=200, armor_class="placa"),

    "titan_gloves": lambda: Item(
        "Manoplas do Titã", "armor", "gloves",
        modifiers=[Modifier("armor", 16), Modifier("attack_power", 12)],
        rarity="epic", value=205, armor_class="placa"),

    # ================================================================ COURO — Arqueiro
    # COMMON couro
    "leather_vest": lambda: Item(
        "Colete de Couro", "armor", "chest",
        modifiers=[Modifier("armor", 4), Modifier("stamina", 2)],
        rarity="common", value=8, armor_class="couro"),

    "light_hood": lambda: Item(
        "Capuz Leve", "armor", "head",
        modifiers=[Modifier("armor", 3), Modifier("crit_rating", 0.01)],
        rarity="common", value=5, armor_class="couro"),

    "light_shoulders": lambda: Item(
        "Ombros Leves", "armor", "shoulders",
        modifiers=[Modifier("armor", 2), Modifier("crit_rating", 0.01)],
        rarity="common", value=4, armor_class="couro"),

    "padded_gloves": lambda: Item(
        "Luvas Acolchoadas", "armor", "gloves",
        modifiers=[Modifier("armor", 2), Modifier("crit_rating", 0.01)],
        rarity="common", value=4, armor_class="couro"),

    "light_boots": lambda: Item(
        "Botas Leves", "armor", "boots",
        modifiers=[Modifier("armor", 2), Modifier("crit_rating", 0.01)],
        rarity="common", value=4, armor_class="couro"),

    "padded_wrists": lambda: Item(
        "Pulsos Acolchoados", "armor", "wrists",
        modifiers=[Modifier("armor", 2), Modifier("stamina", 1)],
        rarity="common", value=3, armor_class="couro"),

    # UNCOMMON couro
    "hunter_helm": lambda: Item(
        "Elmo do Caçador", "armor", "head",
        modifiers=[Modifier("armor", 6), Modifier("crit_rating", 0.03)],
        rarity="uncommon", value=26, armor_class="couro"),

    "hunter_vest": lambda: Item(
        "Colete do Caçador", "armor", "chest",
        modifiers=[Modifier("armor", 9), Modifier("crit_rating", 0.03)],
        rarity="uncommon", value=36, armor_class="couro"),

    "hunter_shoulders": lambda: Item(
        "Ombros do Caçador", "armor", "shoulders",
        modifiers=[Modifier("armor", 6), Modifier("crit_rating", 0.02)],
        rarity="uncommon", value=24, armor_class="couro"),

    "leather_gloves": lambda: Item(
        "Luvas de Couro", "armor", "gloves",
        modifiers=[Modifier("armor", 5), Modifier("attack_power", 2)],
        rarity="uncommon", value=22, armor_class="couro"),

    "hunter_boots": lambda: Item(
        "Botas do Caçador", "armor", "boots",
        modifiers=[Modifier("armor", 6), Modifier("crit_rating", 0.02)],
        rarity="uncommon", value=24, armor_class="couro"),

    "hunter_wrists": lambda: Item(
        "Pulsos do Caçador", "armor", "wrists",
        modifiers=[Modifier("armor", 4), Modifier("crit_rating", 0.02)],
        rarity="uncommon", value=20, armor_class="couro"),

    # RARE couro
    "stalker_helm": lambda: Item(
        "Elmo do Perseguidor", "armor", "head",
        modifiers=[Modifier("armor", 11), Modifier("crit_rating", 0.05)],
        rarity="rare", value=76, armor_class="couro"),

    "stalker_vest": lambda: Item(
        "Colete do Perseguidor", "armor", "chest",
        modifiers=[Modifier("armor", 14), Modifier("crit_rating", 0.05)],
        rarity="rare", value=88, armor_class="couro"),

    "stalker_shoulders": lambda: Item(
        "Ombros do Perseguidor", "armor", "shoulders",
        modifiers=[Modifier("armor", 10), Modifier("crit_rating", 0.04)],
        rarity="rare", value=74, armor_class="couro"),

    "stalker_gloves": lambda: Item(
        "Luvas do Perseguidor", "armor", "gloves",
        modifiers=[Modifier("armor", 8), Modifier("crit_rating", 0.04)],
        rarity="rare", value=68, armor_class="couro"),

    "stalker_boots": lambda: Item(
        "Botas do Perseguidor", "armor", "boots",
        modifiers=[Modifier("armor", 10), Modifier("crit_rating", 0.04)],
        rarity="rare", value=72, armor_class="couro"),

    "stalker_wrists": lambda: Item(
        "Pulsos do Perseguidor", "armor", "wrists",
        modifiers=[Modifier("armor", 7), Modifier("crit_rating", 0.03)],
        rarity="rare", value=65, armor_class="couro"),

    # EPIC couro
    "shadow_gloves": lambda: Item(
        "Luvas das Sombras", "armor", "gloves",
        modifiers=[Modifier("armor", 16), Modifier("crit_rating", 0.07)],
        rarity="epic", value=200, armor_class="couro"),

    "assassin_helm": lambda: Item(
        "Capuz do Assassino", "armor", "head",
        modifiers=[Modifier("armor", 18), Modifier("crit_rating", 0.08)],
        rarity="epic", value=205, armor_class="couro"),

    "assassin_vest": lambda: Item(
        "Colete do Assassino", "armor", "chest",
        modifiers=[Modifier("armor", 22), Modifier("crit_rating", 0.08)],
        rarity="epic", value=218, armor_class="couro"),

    "assassin_shoulders": lambda: Item(
        "Ombros do Assassino", "armor", "shoulders",
        modifiers=[Modifier("armor", 16), Modifier("crit_rating", 0.06)],
        rarity="epic", value=198, armor_class="couro"),

    "assassin_boots": lambda: Item(
        "Botas do Assassino", "armor", "boots",
        modifiers=[Modifier("armor", 16), Modifier("crit_rating", 0.06)],
        rarity="epic", value=200, armor_class="couro"),

    "assassin_wrists": lambda: Item(
        "Pulsos do Assassino", "armor", "wrists",
        modifiers=[Modifier("armor", 12), Modifier("crit_rating", 0.05)],
        rarity="epic", value=192, armor_class="couro"),

    # ================================================================ TECIDO — Mago
    # COMMON tecido
    "worn_hood": lambda: Item(
        "Capuz Puído", "armor", "head",
        modifiers=[Modifier("armor", 1), Modifier("stamina", 1)],
        rarity="common", value=4, armor_class="tecido"),

    "tattered_robe": lambda: Item(
        "Robe Surrado", "armor", "chest",
        modifiers=[Modifier("armor", 2), Modifier("spell_power", 3)],
        rarity="common", value=7, armor_class="tecido"),

    "cloth_shoulders": lambda: Item(
        "Ombros de Tecido", "armor", "shoulders",
        modifiers=[Modifier("armor", 1), Modifier("spell_power", 2)],
        rarity="common", value=3, armor_class="tecido"),

    "cloth_gloves": lambda: Item(
        "Luvas de Tecido", "armor", "gloves",
        modifiers=[Modifier("armor", 1), Modifier("spell_power", 2)],
        rarity="common", value=3, armor_class="tecido"),

    "cloth_boots": lambda: Item(
        "Botas de Tecido", "armor", "boots",
        modifiers=[Modifier("armor", 1), Modifier("spell_power", 2)],
        rarity="common", value=3, armor_class="tecido"),

    "cloth_wrists": lambda: Item(
        "Pulsos de Tecido", "armor", "wrists",
        modifiers=[Modifier("armor", 1), Modifier("spell_power", 1)],
        rarity="common", value=2, armor_class="tecido"),

    # UNCOMMON tecido
    "silk_hood": lambda: Item(
        "Capuz de Seda", "armor", "head",
        modifiers=[Modifier("armor", 4), Modifier("spell_power", 6)],
        rarity="uncommon", value=24, armor_class="tecido"),

    "silk_robe": lambda: Item(
        "Manto de Seda", "armor", "chest",
        modifiers=[Modifier("armor", 6), Modifier("spell_power", 9)],
        rarity="uncommon", value=34, armor_class="tecido"),

    "silk_shoulders": lambda: Item(
        "Ombros de Seda", "armor", "shoulders",
        modifiers=[Modifier("armor", 4), Modifier("spell_power", 6)],
        rarity="uncommon", value=22, armor_class="tecido"),

    "silk_gloves": lambda: Item(
        "Luvas de Seda", "armor", "gloves",
        modifiers=[Modifier("armor", 3), Modifier("spell_power", 5)],
        rarity="uncommon", value=20, armor_class="tecido"),

    "silk_boots": lambda: Item(
        "Botas de Seda", "armor", "boots",
        modifiers=[Modifier("armor", 4), Modifier("spell_power", 5)],
        rarity="uncommon", value=22, armor_class="tecido"),

    "silk_wrists": lambda: Item(
        "Pulsos de Seda", "armor", "wrists",
        modifiers=[Modifier("armor", 3), Modifier("spell_power", 5)],
        rarity="uncommon", value=18, armor_class="tecido"),

    # RARE tecido
    "arcane_hood": lambda: Item(
        "Capuz Arcano", "armor", "head",
        modifiers=[Modifier("armor", 8), Modifier("spell_power", 12)],
        rarity="rare", value=74, armor_class="tecido"),

    "arcane_robe": lambda: Item(
        "Manto Arcano", "armor", "chest",
        modifiers=[Modifier("armor", 10), Modifier("spell_power", 16)],
        rarity="rare", value=88, armor_class="tecido"),

    "arcane_shoulders": lambda: Item(
        "Ombros Arcanos", "armor", "shoulders",
        modifiers=[Modifier("armor", 7), Modifier("spell_power", 11)],
        rarity="rare", value=70, armor_class="tecido"),

    "arcane_gloves": lambda: Item(
        "Luvas Arcanas", "armor", "gloves",
        modifiers=[Modifier("armor", 6), Modifier("spell_power", 9)],
        rarity="rare", value=68, armor_class="tecido"),

    "arcane_boots": lambda: Item(
        "Botas Arcanas", "armor", "boots",
        modifiers=[Modifier("armor", 7), Modifier("spell_power", 10)],
        rarity="rare", value=70, armor_class="tecido"),

    "runed_wrists": lambda: Item(
        "Punhos Rúnicos", "armor", "wrists",
        modifiers=[Modifier("armor", 5), Modifier("spell_power", 9)],
        rarity="rare", value=65, armor_class="tecido"),

    # EPIC tecido
    "lich_hood": lambda: Item(
        "Capuz do Lich", "armor", "head",
        modifiers=[Modifier("armor", 14), Modifier("spell_power", 20)],
        rarity="epic", value=200, armor_class="tecido"),

    "lich_robe": lambda: Item(
        "Manto do Lich", "armor", "chest",
        modifiers=[Modifier("armor", 18), Modifier("spell_power", 28)],
        rarity="epic", value=220, armor_class="tecido"),

    "lich_shoulders": lambda: Item(
        "Ombros do Lich", "armor", "shoulders",
        modifiers=[Modifier("armor", 12), Modifier("spell_power", 18)],
        rarity="epic", value=196, armor_class="tecido"),

    "lich_gloves": lambda: Item(
        "Luvas do Lich", "armor", "gloves",
        modifiers=[Modifier("armor", 10), Modifier("spell_power", 16)],
        rarity="epic", value=190, armor_class="tecido"),

    "lich_boots": lambda: Item(
        "Botas do Lich", "armor", "boots",
        modifiers=[Modifier("armor", 12), Modifier("spell_power", 16)],
        rarity="epic", value=194, armor_class="tecido"),

    "lich_wrists": lambda: Item(
        "Punhos do Lich", "armor", "wrists",
        modifiers=[Modifier("armor", 9), Modifier("spell_power", 14)],
        rarity="epic", value=188, armor_class="tecido"),
}

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
MOB_LOOT_TABLES: dict[str, list] = {
    "Aranha": [
        (_T["padded_gloves"],    0.05),
        (_T["cloth_wrists"],     0.03),
        (_T["worn_hood"],        0.02),
    ],
    "Rato": [
        (_T["cloth_boots"],      0.05),
        (_T["padded_gloves"],    0.03),
        (_T["cloth_wrists"],     0.01),
    ],
    "Escorpião": [
        (_T["padded_wrists"],    0.04),
        (_T["light_boots"],      0.03),
        (_T["light_hood"],       0.02),
        (_T["light_shoulders"],  0.01),
    ],
    "Cobra": [
        (_T["light_boots"],      0.04),
        (_T["padded_gloves"],    0.03),
        (_T["leather_vest"],     0.02),
    ],
    "Lobo": [
        (_T["leather_vest"],     0.05),
        (_T["light_boots"],      0.03),
        (_T["light_hood"],       0.02),
    ],
    "Urso": [
        (_T["leather_vest"],     0.05),
        (_T["bone_shield"],      0.03),
        (_T["cracked_club"],     0.02),
        (_T["iron_breastplate"], 0.02),
    ],
    "Goblin": [
        (_T["bone_sword"],       0.05),
        (_T["cracked_club"],     0.04),
        (_T["iron_gauntlets"],   0.03),
        (_T["iron_greaves"],     0.02),
    ],
    "Zumbi": [
        (_T["bone_sword"],       0.04),
        (_T["iron_coif"],        0.03),
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
        (_T["hunter_helm"],      0.03),
        (_T["hunter_boots"],     0.03),
        (_T["arcane_wand"],      0.02),
        (_T["silk_robe"],        0.01),
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
        (_T["lich_robe"],        0.01),
        (_T["mystic_staff"],     0.005),
    ],
}


def roll_mob_loot(mob_name: str, tier: str) -> list:
    """Rola drops usando a tabela específica do mob. Fallback para LOOT_TABLES."""
    table = MOB_LOOT_TABLES.get(mob_name)
    if table is None:
        return roll_loot("melee", tier)

    tier_mult = {"normal": 1.0, "elite": 1.5, "rare": 2.5, "boss": 4.0}
    mult = tier_mult.get(tier, 1.0)

    result = []
    for factory, chance in table:
        if random.random() < chance * mult:
            result.append(factory())
    return result[:4]
