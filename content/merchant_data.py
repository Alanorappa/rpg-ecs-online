# merchant_data.py
"""
Configuração dos comerciantes e estoques de lojas.

Definição dos itens em si mora em item_table.py (catálogo único — loot e
loja compartilham as mesmas factories, sem duplicata). Este arquivo só lista
QUAIS itens do catálogo aparecem em cada loja e por quanto.

Para adicionar um novo comerciante:
  1. Crie uma nova entrada em SHOPS com um shop_id único.
  2. Adicione itens ao "stock" com factory (referência a item_table.ITEMS[...],
     ou de outro catálogo, ex. crafting_data) e price.
  3. Coloque o char 'N' (ou registre outro char em MERCHANT_TILE_CHARS) no CSV.
  4. No map_loader.py MERCHANT_TILE_CHARS mapeie char → shop_id.
"""
from content.item_table import ITEMS as _I

SHOPS = {
    "general": {
        "name": "Mercador Geral",
        "color": (80, 200, 80),
        "stock": [
            # --- Poções ---
            {"factory": _I["small_hp_potion"],   "price": 10},
            {"factory": _I["hp_potion"],         "price": 25},
            {"factory": _I["large_hp_potion"],   "price": 50},

            # --- Comida ---
            {"factory": _I["simple_bread"],      "price": 5},
            {"factory": _I["roasted_meat"],      "price": 12},
            {"factory": _I["hearty_stew"],       "price": 20},

            # --- Odres (mana HoT, fora de combate) ---
            {"factory": _I["small_waterskin"],   "price": 2},
            {"factory": _I["medium_waterskin"],  "price": 5},
            {"factory": _I["large_waterskin"],   "price": 8},

            # --- Poções de mana (instantâneas) ---
            {"factory": _I["small_mana_potion"], "price": 10},
            {"factory": _I["mana_potion"],       "price": 15},
            {"factory": _I["large_mana_potion"], "price": 25},

            # --- Armas 1H físicas ---
            {"factory": _I["bone_dagger"],       "price": 30},
            {"factory": _I["iron_sword"],        "price": 55},   # "Espada de Ferro"
            {"factory": _I["shadow_blade"],      "price": 185},
            # --- Arma mágica ---
            {"factory": _I["oak_wand"],          "price": 45},
            # --- Arqueiro: arco, aljava, flechas ---
            {"factory": _I["short_bow"],         "price": 35},   # "Arco Curto"
            {"factory": _I["basic_quiver"],      "price": 15},   # "Aljava Básica"
            {"factory": _I["arrow"],             "price": 1},    # "Flecha"
            {"factory": _I["arrow_broadhead"],   "price": 5},    # "Flecha Perfurante"
            {"factory": _I["arrow_heavy"],       "price": 10},   # "Flecha Pesada"
            # --- Escudo ---
            {"factory": _I["wood_shield"],       "price": 32},
            # --- Armaduras ---
            {"factory": _I["leather_helm"],      "price": 35},
            {"factory": _I["leather_mail"],      "price": 55},
            {"factory": _I["traveler_cloak"],    "price": 30},
            {"factory": _I["iron_greaves"],      "price": 42},   # "Grevas de Ferro"
            {"factory": _I["leather_gloves"],    "price": 28},   # "Luvas de Couro"
            {"factory": _I["iron_wristguards"],  "price": 30},
            # --- Joias ---
            {"factory": _I["simple_amulet"],     "price": 48},
        ],
    },

    "blacksmith": {
        "name": "Ferreiro",
        "color": (180, 120, 40),
        "stock": [
            # --- Materiais (caros para incentivar reciclagem) ---
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["fragmento_ferro"](),  "price": 500},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["fibra_madeira"](),    "price": 300},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["tira_couro"](),       "price": 300},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["fibra_resistente"](), "price": 700},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["po_de_joia"](),       "price": 400},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["fio_de_prata"](),     "price": 800},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["essencia_comum"](),   "price": 1000},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["essencia_arcana"](),  "price": 2000},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["joia_bruta"](),       "price": 2500},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["MATERIALS"]).MATERIALS["cristal_poder"](),    "price": 5000},
            # --- Receitas ---
            {"factory": lambda: __import__("content.crafting_data", fromlist=["RECIPE_ITEMS"]).RECIPE_ITEMS["espada_afiada"](),    "price": 200},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["RECIPE_ITEMS"]).RECIPE_ITEMS["cota_reforcada"](),   "price": 200},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["RECIPE_ITEMS"]).RECIPE_ITEMS["amuleto_protecao"](), "price": 350},
            {"factory": lambda: __import__("content.crafting_data", fromlist=["RECIPE_ITEMS"]).RECIPE_ITEMS["espada_runica"](),    "price": 1200},
        ],
    },
}
