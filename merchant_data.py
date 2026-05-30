# merchant_data.py
"""
Configuração dos comerciantes e estoques de lojas.

Para adicionar um novo comerciante:
  1. Crie uma nova entrada em SHOPS com um shop_id único.
  2. Adicione itens ao "stock" com factory (função sem args → Item) e price.
  3. Coloque o char 'N' (ou registre outro char em MERCHANT_TILE_CHARS) no CSV.
  4. No map_loader.py MERCHANT_TILE_CHARS mapeie char → shop_id.
"""
from components import Item, Modifier


def _make_item(name, item_type, slot, rarity, value,
               mods=None, two_handed=False,
               damage_min=0, damage_max=0, attack_speed=0.0, proc=None,
               subtype="", cast_range=0):
    """Retorna uma factory sem args que cria um Item novo a cada chamada."""
    mods = mods or []
    def factory():
        return Item(
            name=name, item_type=item_type, slot=slot,
            modifiers=[Modifier(attr, val, typ) for attr, val, typ in mods],
            rarity=rarity, value=value, two_handed=two_handed,
            damage_min=damage_min, damage_max=damage_max,
            attack_speed=attack_speed, proc=proc, subtype=subtype,
            cast_range=cast_range,
        )
    return factory


def _make_consumable(name, rarity, value, consumable: dict, max_stack: int = 20):
    """Retorna uma factory sem args que cria um Item consumível (empilhável)."""
    def factory():
        return Item(
            name=name, item_type="consumable", slot="",
            rarity=rarity, value=value,
            consumable=consumable,
            max_stack=max_stack,
        )
    return factory


def _make_quiver(name, rarity, value, max_arrows: int = 100, mods=None,
                 default_arrow: str = "Flecha"):
    """Retorna factory de aljava (off-hand, carrega flechas)."""
    mods = mods or []
    def factory():
        return Item(
            name=name, item_type="quiver", slot="offhand",
            modifiers=[Modifier(attr, val, typ) for attr, val, typ in mods],
            rarity=rarity, value=value,
            arrow_count=max_arrows, max_arrows=max_arrows,
            subtype=default_arrow,   # tipo de flecha pré-carregada
        )
    return factory


def _make_ammo(name, rarity, value, max_stack: int = 1000,
               damage_min: int = 0, damage_max: int = 0):
    """Retorna factory de munição (empilhável na bag)."""
    def factory():
        return Item(
            name=name, item_type="ammo", slot="",
            rarity=rarity, value=value,
            damage_min=damage_min, damage_max=damage_max,
            max_stack=max_stack,
        )
    return factory


SHOPS = {
    "general": {
        "name": "Mercador Geral",
        "color": (80, 200, 80),
        "stock": [
            # --- Poções ---
            {"factory": _make_consumable(
                "Poção Pequena de Vida", "common", 10,
                {"heal_instant": 50, "ooc_only": False}),
             "price": 10},
            {"factory": _make_consumable(
                "Poção de Vida", "common", 25,
                {"heal_instant": 120, "ooc_only": False}),
             "price": 25},
            {"factory": _make_consumable(
                "Poção Grande de Vida", "common", 50,
                {"heal_instant": 250, "ooc_only": False}),
             "price": 50},

            # --- Comida ---
            {"factory": _make_consumable(
                "Pão Simples", "common", 5,
                {"heal_per_tick": 10, "interval": 3.0, "ticks": 10, "ooc_only": True}),
             "price": 5},
            {"factory": _make_consumable(
                "Carne Assada", "common", 12,
                {"heal_per_tick": 20, "interval": 3.0, "ticks": 10, "ooc_only": True}),
             "price": 12},
            {"factory": _make_consumable(
                "Ensopado Revigorante", "common", 20,
                {"heal_per_tick": 35, "interval": 3.0, "ticks": 10, "ooc_only": True}),
             "price": 20},

            # --- Odres (mana HoT, fora de combate) ---
            {"factory": _make_consumable(
                "Odre Pequeno", "common", 2,
                {"desc": "Restaura 100 de mana ao longo de 20 segundos.",
                 "mana_per_tick": 25, "interval": 5.0, "ticks": 4, "ooc_only": True}),
             "price": 2},
            {"factory": _make_consumable(
                "Odre Médio", "common", 5,
                {"desc": "Restaura 200 de mana ao longo de 20 segundos.",
                 "mana_per_tick": 50, "interval": 5.0, "ticks": 4, "ooc_only": True}),
             "price": 5},
            {"factory": _make_consumable(
                "Odre Grande", "common", 8,
                {"desc": "Restaura 500 de mana ao longo de 20 segundos.",
                 "mana_per_tick": 125, "interval": 5.0, "ticks": 4, "ooc_only": True}),
             "price": 8},

            # --- Poções de mana (instantâneas) ---
            {"factory": _make_consumable(
                "Poção Pequena de Mana", "common", 10,
                {"desc": "Restaura 50 de mana instantaneamente.",
                 "mana_restore": 50, "ooc_only": False}),
             "price": 10},
            {"factory": _make_consumable(
                "Poção de Mana", "common", 15,
                {"desc": "Restaura 150 de mana instantaneamente.",
                 "mana_restore": 150, "ooc_only": False}),
             "price": 15},
            {"factory": _make_consumable(
                "Poção Grande de Mana", "common", 25,
                {"desc": "Restaura 350 de mana instantaneamente.",
                 "mana_restore": 350, "ooc_only": False}),
             "price": 25},

            # --- Armas 1H físicas ---
            {"factory": _make_item("Adaga de Osso",    "weapon", "mainhand", "common",   30,
                                   [("crit_rating", 0.03, "flat")],
                                   damage_min=2, damage_max=6, attack_speed=1.3, subtype="Dagger"),
             "price": 30},
            {"factory": _make_item("Espada de Ferro",  "weapon", "mainhand", "common",   55,
                                   [("attack_power", 3, "flat")],
                                   damage_min=7, damage_max=14, attack_speed=1.5, subtype="Sword"),
             "price": 55},
            # --- Arma mágica ---
            {"factory": _make_item("Varinha de Carvalho", "weapon", "mainhand", "common", 45,
                                   [("spell_power", 8, "flat")],
                                   damage_min=5, damage_max=11, attack_speed=1.4, subtype="Wand"),
             "price": 45},
            # --- Arqueiro: arco, aljava, flechas ---
            {"factory": _make_item("Arco Curto", "weapon", "mainhand", "common", 20,
                                   [("crit_rating", 0.02, "flat")],
                                   damage_min=5, damage_max=22, attack_speed=1.8,
                                   subtype="Bow", cast_range=7),
             "price": 35},
            {"factory": _make_quiver("Aljava Básica",    "common",   5,  max_arrows=100),
             "price": 15},
            {"factory": _make_ammo("Flecha", "common", 1, max_stack=1000),
             "price": 1},
            {"factory": _make_ammo("Flecha Perfurante", "uncommon", 3,
                                   max_stack=1000, damage_min=4, damage_max=8),
             "price": 5},
            {"factory": _make_ammo("Flecha Pesada", "rare", 6,
                                   max_stack=1000, damage_min=8, damage_max=14),
             "price": 10},
            # --- Escudo ---
            {"factory": _make_item("Escudo de Madeira", "shield", "offhand", "common",   32,
                                   [("armor", 6, "flat"), ("stamina", 1, "flat")]),
             "price": 32},
            # --- Armaduras ---
            {"factory": _make_item("Elmo de Couro",     "armor", "head",      "common",   35,
                                   [("armor", 4, "flat"), ("stamina", 2, "flat")]),
             "price": 35},
            {"factory": _make_item("Cota de Couro",     "armor", "chest",     "common",   55,
                                   [("armor", 6, "flat"), ("stamina", 2, "flat")]),
             "price": 55},
            {"factory": _make_item("Manto de Viajante", "armor", "shoulders", "common",   30,
                                   [("armor", 4, "flat"), ("stamina", 1, "flat")]),
             "price": 30},
            {"factory": _make_item("Grevas de Ferro",   "armor", "boots",     "common",   42,
                                   [("armor", 5, "flat"), ("haste_rating", 3, "flat")]),
             "price": 42},
            {"factory": _make_item("Luvas de Couro",    "armor", "gloves",    "common",   28,
                                   [("armor", 3, "flat"), ("crit_rating", 0.02, "flat")]),
             "price": 28},
            {"factory": _make_item("Punhos de Ferro",   "armor", "wrists",    "common",   30,
                                   [("armor", 4, "flat"), ("stamina", 1, "flat")]),
             "price": 30},
            # --- Joias ---
            {"factory": _make_item("Amuleto Simples",   "jewelry", "neck", "common",   48,
                                   [("stamina", 4, "flat")]),
             "price": 48},
        ],
    },

    "blacksmith": {
        "name": "Ferreiro",
        "color": (180, 120, 40),
        "stock": [
            # --- Materiais (caros para incentivar reciclagem) ---
            {"factory": lambda: __import__("crafting_data").MATERIALS["fragmento_ferro"](),  "price": 500},
            {"factory": lambda: __import__("crafting_data").MATERIALS["fibra_madeira"](),    "price": 300},
            {"factory": lambda: __import__("crafting_data").MATERIALS["tira_couro"](),       "price": 300},
            {"factory": lambda: __import__("crafting_data").MATERIALS["fibra_resistente"](), "price": 700},
            {"factory": lambda: __import__("crafting_data").MATERIALS["po_de_joia"](),       "price": 400},
            {"factory": lambda: __import__("crafting_data").MATERIALS["fio_de_prata"](),     "price": 800},
            {"factory": lambda: __import__("crafting_data").MATERIALS["essencia_comum"](),   "price": 1000},
            {"factory": lambda: __import__("crafting_data").MATERIALS["essencia_arcana"](),  "price": 2000},
            {"factory": lambda: __import__("crafting_data").MATERIALS["joia_bruta"](),       "price": 2500},
            {"factory": lambda: __import__("crafting_data").MATERIALS["cristal_poder"](),    "price": 5000},
            # --- Receitas ---
            {"factory": lambda: __import__("crafting_data").RECIPE_ITEMS["espada_afiada"](),    "price": 200},
            {"factory": lambda: __import__("crafting_data").RECIPE_ITEMS["cota_reforcada"](),   "price": 200},
            {"factory": lambda: __import__("crafting_data").RECIPE_ITEMS["amuleto_protecao"](), "price": 350},
            {"factory": lambda: __import__("crafting_data").RECIPE_ITEMS["espada_runica"](),    "price": 1200},
        ],
    },
}
