# item_table.py
"""
Catálogo único de itens do jogo — fonte de verdade para loot, loja e (fora
deste arquivo) receitas de forja.

Antes desta refatoração, itens existiam duplicados em loot_tables.py e
merchant_data.py — 3 nomes ("Espada de Ferro", "Grevas de Ferro", "Luvas de
Couro") já tinham divergido de verdade (raridade/dano/valor/mods diferentes
dependendo de onde vinham). Consolidado aqui: nos 3 casos, prevalece a
versão que já estava em loot_tables.py (decisão do usuário).

`loot_tables.py` reexporta este dict como `_T` (compat — nenhum consumidor
existente precisa mudar). `merchant_data.py` referencia as entradas daqui
para o estoque da loja "general"; consumíveis (poções, comida, odres)
também moraram sempre só na loja e migraram pra cá.

armor_class (apenas item_type == "armor"):
  "placa"  — foco em força/stamina/armadura.  Permitido: guerreiro
  "couro"  — foco em crítico/agilidade.       Permitido: guerreiro, arqueiro
  "tecido" — foco em intelecto/spell_power.   Permitido: guerreiro, arqueiro, mago

Restrições por classe definidas em stats_system.CLASS_ARMOR_ALLOWED.

item_level é derivado automaticamente de (rarity, value) — ver
_LEVEL_BANDS/_VALUE_RANGES no fim do arquivo. Não precisa ser passado nas
factories abaixo. level_requirement default é 1 (vem do próprio Item) — pra
subir o requisito de um item específico, passe `level_requirement=N` direto
na chamada de `Item(...)` (itens em lambda) ou no `_make_item(...)`/
`_make_consumable(...)`/etc. (itens que usam os helpers abaixo).
"""
from __future__ import annotations
from components import Item, Modifier


# ---------------------------------------------------------------------------
# Helpers de factory (migrados de merchant_data.py — _make_item ganhou
# armor_class, que faltava lá: toda armadura vendida em loja tinha
# armor_class="" por omissão, ou seja, SEM restrição de material nenhuma —
# bug real fechado nesta consolidação, ver itens novos abaixo)
# ---------------------------------------------------------------------------
def _make_item(name, item_type, slot, rarity, value,
               mods=None, two_handed=False,
               damage_min=0, damage_max=0, attack_speed=0.0, proc=None,
               subtype="", cast_range=0, armor_class="",
               level_requirement: int = 1):
    """Retorna uma factory sem args que cria um Item novo a cada chamada."""
    mods = mods or []
    def factory():
        return Item(
            name=name, item_type=item_type, slot=slot,
            modifiers=[Modifier(attr, val, typ) for attr, val, typ in mods],
            rarity=rarity, value=value, two_handed=two_handed,
            damage_min=damage_min, damage_max=damage_max,
            attack_speed=attack_speed, proc=proc, subtype=subtype,
            cast_range=cast_range, armor_class=armor_class,
            level_requirement=level_requirement,
        )
    return factory


def _make_consumable(name, rarity, value, consumable: dict, max_stack: int = 20,
                     level_requirement: int = 1):
    """Retorna uma factory sem args que cria um Item consumível (empilhável)."""
    def factory():
        return Item(
            name=name, item_type="consumable", slot="",
            rarity=rarity, value=value,
            consumable=consumable,
            max_stack=max_stack,
            level_requirement=level_requirement,
        )
    return factory


def _make_quiver(name, rarity, value, max_arrows: int = 100, mods=None,
                 default_arrow: str = "Flecha", level_requirement: int = 1):
    """Retorna factory de aljava (off-hand, carrega flechas)."""
    mods = mods or []
    def factory():
        return Item(
            name=name, item_type="quiver", slot="offhand",
            modifiers=[Modifier(attr, val, typ) for attr, val, typ in mods],
            rarity=rarity, value=value,
            arrow_count=max_arrows, max_arrows=max_arrows,
            subtype=default_arrow,   # tipo de flecha pré-carregada
            level_requirement=level_requirement,
        )
    return factory


def _make_ammo(name, rarity, value, max_stack: int = 1000,
               damage_min: int = 0, damage_max: int = 0,
               level_requirement: int = 1):
    """Retorna factory de munição (empilhável na bag)."""
    def factory():
        return Item(
            name=name, item_type="ammo", slot="",
            rarity=rarity, value=value,
            damage_min=damage_min, damage_max=damage_max,
            max_stack=max_stack,
            level_requirement=level_requirement,
        )
    return factory


# ---------------------------------------------------------------------------
# Itens (antigo loot_tables._T — 106 entradas, intocadas)
# ---------------------------------------------------------------------------
_RAW_ITEMS = {

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

    # ── Bows (arqueiro) ───────────────────────────────────────────────────────
    "short_bow": lambda: Item(
        "Arco Curto", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.01)],
        rarity="common", value=20,
        damage_min=5, damage_max=22, attack_speed=1.8, subtype="Bow", cast_range=7),

    "hunter_bow": lambda: Item(
        "Arco do Caçador", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.01), Modifier("attack_power", 4)],
        rarity="uncommon", value=38,
        damage_min=8, damage_max=30, attack_speed=2.0, subtype="Bow", cast_range=8),

    "elven_bow": lambda: Item(
        "Arco Élfico", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.06), Modifier("attack_power", 6), Modifier("agility", 2)],
        rarity="rare", value=95,
        damage_min=12, damage_max=42, attack_speed=1.6, subtype="Bow", cast_range=9),

    # ── Quivers (arqueiro off-hand) ────────────────────────────────────────────
    "basic_quiver": lambda: Item(
        "Aljava Básica", "quiver", "offhand",
        modifiers=[], rarity="common", value=5,
        arrow_count=75, max_arrows=75, subtype="Flecha"),

    "sturdy_quiver": lambda: Item(
        "Aljava Reforçada", "quiver", "offhand",
        modifiers=[Modifier("crit_rating", 0.01)], rarity="uncommon", value=25,
        arrow_count=100, max_arrows=100, subtype="Flecha"),

    # ── Ammo ──────────────────────────────────────────────────────────────────
    "arrow": lambda: Item(
        "Flecha", "ammo", "",
        modifiers=[], rarity="common", value=1,
        damage_min=0, damage_max=0, max_stack=500),

    "arrow_broadhead": lambda: Item(
        "Flecha Perfurante", "ammo", "",
        modifiers=[], rarity="uncommon", value=2,
        damage_min=4, damage_max=8, max_stack=750),

    "arrow_heavy": lambda: Item(
        "Flecha Pesada", "ammo", "",
        modifiers=[], rarity="rare", value=4,
        damage_min=8, damage_max=14, max_stack=500),

  # ──────────────────────────────────────────────────────────────────
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
        rarity="uncommon", value=40, level_requirement=12),

    "shadow_blade": lambda: Item(
        "Lâmina Sombria", "weapon", "mainhand",
        modifiers=[Modifier("crit_rating", 0.05), Modifier("attack_power", 5)],
        rarity="rare", value=90,
        damage_min=15, damage_max=27, attack_speed=1.3, subtype="Sword", level_requirement=15),

    "war_hammer": lambda: Item(
        "Martelo de Guerra", "weapon", "mainhand",
        modifiers=[Modifier("stamina", 8), Modifier("attack_power", 6)],
        rarity="rare", value=85,
        damage_min=22, damage_max=40, attack_speed=2.9, two_handed=True, subtype="Hammer"),

    "mystic_staff": lambda: Item(
        "Cajado Místico", "weapon", "mainhand",
        modifiers=[Modifier("spell_power", 22), Modifier("stamina", 5), Modifier("crit_rating", 0.5)],
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
        rarity="epic", value=215, two_handed=True,
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

    # ================================================================ ITENS EXCLUSIVOS DA LOJA
    # (antes definidos direto em merchant_data.py — sem duplicata em loot_tables)
    "bone_dagger": _make_item(
        "Adaga de Osso", "weapon", "mainhand", "common", 30,
        [("crit_rating", 0.03, "flat")],
        damage_min=2, damage_max=6, attack_speed=1.3, subtype="Dagger"),

    "oak_wand": _make_item(
        "Varinha de Carvalho", "weapon", "mainhand", "common", 45,
        [("spell_power", 8, "flat")],
        damage_min=5, damage_max=11, attack_speed=1.4, subtype="Wand"),

    "wood_shield": _make_item(
        "Escudo de Madeira", "shield", "offhand", "common", 32,
        [("armor", 6, "flat"), ("stamina", 1, "flat")]),

    "leather_helm": _make_item(
        "Elmo de Couro", "armor", "head", "common", 35,
        [("armor", 4, "flat"), ("stamina", 2, "flat")], armor_class="couro"),

    "leather_mail": _make_item(
        "Cota de Couro", "armor", "chest", "common", 55,
        [("armor", 6, "flat"), ("stamina", 2, "flat")], armor_class="couro"),

    "traveler_cloak": _make_item(
        "Manto de Viajante", "armor", "shoulders", "common", 30,
        [("armor", 4, "flat"), ("stamina", 1, "flat")], armor_class="couro"),

    "iron_wristguards": _make_item(
        "Punhos de Ferro", "armor", "wrists", "common", 30,
        [("armor", 4, "flat"), ("stamina", 1, "flat")], armor_class="placa"),

    "simple_amulet": _make_item(
        "Amuleto Simples", "jewelry", "neck", "common", 48,
        [("stamina", 4, "flat")]),

    # ── Consumíveis (poções, comida, odres) ────────────────────────────────
    "small_hp_potion": _make_consumable(
        "Poção Pequena de Vida", "common", 10,
        {"desc": "Restaura 50 de vida instantaneamente.",
         "heal_instant": 50, "ooc_only": False}),

    "hp_potion": _make_consumable(
        "Poção de Vida", "common", 25,
        {"desc": "Restaura 120 de vida instantaneamente.",
         "heal_instant": 120, "ooc_only": False}),

    "large_hp_potion": _make_consumable(
        "Poção Grande de Vida", "common", 50,
        {"desc": "Restaura 250 de vida instantaneamente.",
         "heal_instant": 250, "ooc_only": False}),

    "simple_bread": _make_consumable(
        "Pão Simples", "common", 5,
        {"desc": "Restaura 100 de vida ao longo de 30 segundos.",
         "heal_per_tick": 10, "interval": 3.0, "ticks": 10, "ooc_only": True}),

    "roasted_meat": _make_consumable(
        "Carne Assada", "common", 12,
        {"desc": "Restaura 200 de vida ao longo de 30 segundos.",
         "heal_per_tick": 20, "interval": 3.0, "ticks": 10, "ooc_only": True}),

    "hearty_stew": _make_consumable(
        "Ensopado Revigorante", "common", 20,
        {"desc": "Restaura 350 de vida ao longo de 30 segundos.",
         "heal_per_tick": 35, "interval": 3.0, "ticks": 10, "ooc_only": True}),

    "small_waterskin": _make_consumable(
        "Odre Pequeno", "common", 2,
        {"desc": "Restaura 100 de mana ao longo de 20 segundos.",
         "mana_per_tick": 25, "interval": 5.0, "ticks": 4, "ooc_only": True}),

    "medium_waterskin": _make_consumable(
        "Odre Médio", "common", 5,
        {"desc": "Restaura 200 de mana ao longo de 20 segundos.",
         "mana_per_tick": 50, "interval": 5.0, "ticks": 4, "ooc_only": True}),

    "large_waterskin": _make_consumable(
        "Odre Grande", "common", 8,
        {"desc": "Restaura 500 de mana ao longo de 20 segundos.",
         "mana_per_tick": 125, "interval": 5.0, "ticks": 4, "ooc_only": True}),

    "small_mana_potion": _make_consumable(
        "Poção Pequena de Mana", "common", 10,
        {"desc": "Restaura 50 de mana instantaneamente.",
         "mana_restore": 50, "ooc_only": False}),

    "mana_potion": _make_consumable(
        "Poção de Mana", "common", 15,
        {"desc": "Restaura 150 de mana instantaneamente.",
         "mana_restore": 150, "ooc_only": False}),

    "large_mana_potion": _make_consumable(
        "Poção Grande de Mana", "common", 25,
        {"desc": "Restaura 350 de mana instantaneamente.",
         "mana_restore": 350, "ooc_only": False}),
}


# ---------------------------------------------------------------------------
# item_level automático — derivado de (rarity, value), sem precisar tocar
# cada factory acima. Bandas aproximadas; level_requirement fica em 1 pra
# todo item por enquanto (ajuste manual futuro).
# ---------------------------------------------------------------------------
_LEVEL_BANDS: dict[str, tuple[int, int]] = {
    "common":    (1, 15),
    "uncommon":  (10, 25),
    "rare":      (20, 40),
    "epic":      (35, 55),
    "legendary": (50, 70),
    "mythic":    (65, 85),
}
_VALUE_RANGES: dict[str, tuple[int, int]] = {
    "common":    (1, 55),
    "uncommon":  (15, 45),
    "rare":      (60, 95),
    "epic":      (175, 260),
    "legendary": (400, 1500),
    "mythic":    (1500, 3500),
}


def _derive_item_level(rarity: str, value: int) -> int:
    lvl_lo, lvl_hi = _LEVEL_BANDS.get(rarity, (1, 15))
    val_lo, val_hi = _VALUE_RANGES.get(rarity, (0, 100))
    if val_hi <= val_lo:
        return lvl_lo
    t = max(0.0, min(1.0, (value - val_lo) / (val_hi - val_lo)))
    return round(lvl_lo + t * (lvl_hi - lvl_lo))


def _with_derived_level(factory):
    """item_level é sempre recalculado (nunca setado à mão nas factories
    acima). level_requirement NÃO é tocado aqui — respeita o que a factory
    passou (default 1 do próprio Item, ou o valor explícito que você botar
    direto na lambda/`_make_item(...)` quando quiser subir o requisito de
    um item específico)."""
    def wrapped():
        item = factory()
        item.item_level = _derive_item_level(item.rarity, item.value)
        return item
    return wrapped


ITEMS: dict = {key: _with_derived_level(f) for key, f in _RAW_ITEMS.items()}
