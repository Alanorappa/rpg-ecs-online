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

# ============================================================================
# Loja de instância (BG) — catálogo completo, filtrado por classe, preço por
# raridade (25/08/2026, pedido do usuário). Ponto único de verdade: server
# (process_shop_buy, autoritativo) e cliente (painel da loja) chamam
# instance_shop_class_allows() — nunca reimplementar esse filtro em outro
# lugar. Regra de EQUIPAR (is_weapon_allowed_for_class/CLASS_ARMOR_ALLOWED em
# engine/stats_system.py) é mais permissiva e continua intacta — o filtro
# abaixo é só TEMÁTICO (o que aparece à venda), não afeta o que já pode ser
# equipado depois de comprado.
# ============================================================================

INSTANCE_SHOP_RARITY_PRICE = {
    "common": 25, "uncommon": 833, "rare": 2742,
    "epic": 7327, "legendary": 57236, "mythic": 135937,
}

INSTANCE_SHOP_WEAPON_SUBTYPES = {
    "guerreiro": frozenset({"Sword", "Mace", "Axe", "Club", "Hammer", "Dagger"}),
    "arqueiro":  frozenset({"Bow", "Dagger"}),
    "mago":      frozenset({"Wand", "Staff", "Scepter"}),
}
INSTANCE_SHOP_ARMOR_CLASS = {
    "guerreiro": "placa", "arqueiro": "couro", "mago": "tecido",
}
# categoria → posição na lista (ordenação, evita parede de itens sem ordem)
_INSTANCE_SHOP_CATEGORY_ORDER = {
    "weapon": 0, "shield": 1, "quiver": 2, "ammo": 2,
    "armor": 3, "jewelry": 4, "consumable": 5,
}
_INSTANCE_SHOP_RARITY_ORDER = {
    "common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4, "mythic": 5,
}


def instance_shop_class_allows(item, class_id: str) -> bool:
    """Filtro temático da loja de instância — guerreiro só vê placa/espada/
    maça/machado/martelo/adaga/escudo; arqueiro só vê couro/arco/adaga/
    aljava/munição; mago só vê tecido/varinha/cajado/cetro. Joia e
    consumível são gerais (qualquer classe)."""
    if item.item_type in ("jewelry", "consumable"):
        return True
    if item.item_type == "armor":
        return item.armor_class == INSTANCE_SHOP_ARMOR_CLASS.get(class_id, "")
    if item.item_type == "weapon":
        return item.subtype in INSTANCE_SHOP_WEAPON_SUBTYPES.get(class_id, frozenset())
    if item.item_type == "shield":
        return class_id == "guerreiro"
    if item.item_type in ("quiver", "ammo"):
        return class_id == "arqueiro"
    return False  # item_type desconhecido/futuro — não vende até alguém decidir


def _instance_shop_price(item) -> int:
    # Consumível: TODOS têm rarity="common" no catálogo hoje — usar a
    # fórmula de raridade igualaria poção/comida/água ao mesmo preço.
    # Mantém o value individual do próprio item.
    if item.item_type == "consumable":
        return int(item.value)
    return INSTANCE_SHOP_RARITY_PRICE.get(item.rarity, 0)


def _instance_shop_sort_key(item):
    return (
        _INSTANCE_SHOP_CATEGORY_ORDER.get(item.item_type, 9),
        _INSTANCE_SHOP_RARITY_ORDER.get(item.rarity, 9),
        item.name,
    )


def _build_instance_shop_stock() -> list[dict]:
    """Estoque COMPLETO (todo o catálogo, sem filtro de classe) — mesma
    forma que server/world_server.py::_build_item_caches já consome
    genericamente pra qualquer loja, sem precisar mudar aquela função."""
    return [{"factory": factory, "price": _instance_shop_price(factory())}
            for factory in _I.values()]


_INSTANCE_SHOP_FULL_STOCK = _build_instance_shop_stock()

# Pré-filtrado/ordenado por classe, calculado 1x na carga do módulo (não por
# frame) — usado só pela UI (server valida via instance_shop_class_allows
# direto no momento da compra, não precisa da lista pronta).
INSTANCE_SHOP_STOCK_BY_CLASS = {
    cls: sorted(
        (e for e in _INSTANCE_SHOP_FULL_STOCK
         if instance_shop_class_allows(e["factory"](), cls)),
        key=lambda e: _instance_shop_sort_key(e["factory"]()),
    )
    for cls in ("guerreiro", "arqueiro", "mago")
}

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
            {"factory": _I["iron_sword"],        "price": 904},   # "Espada de Ferro"
            {"factory": _I["shadow_blade"],      "price": 6570},
            # --- Arma mágica ---
            {"factory": _I["oak_wand"],          "price": 45},
            # --- Arqueiro: arco, aljava, flechas ---
            {"factory": _I["short_bow"],         "price": 35},   # "Arco Curto"
            {"factory": _I["basic_quiver"],      "price": 15},   # "Aljava Básica"
            {"factory": _I["arrow"],             "price": 1},    # "Flecha"
            {"factory": _I["arrow_broadhead"],   "price": 688},    # "Flecha Perfurante"
            {"factory": _I["arrow_heavy"],       "price": 1562},   # "Flecha Pesada"
            # --- Escudo ---
            {"factory": _I["wood_shield"],       "price": 32},
            # --- Armaduras ---
            {"factory": _I["leather_helm"],      "price": 35},
            {"factory": _I["leather_mail"],      "price": 55},
            {"factory": _I["traveler_cloak"],    "price": 30},
            {"factory": _I["iron_greaves"],      "price": 42},   # "Grevas de Ferro"
            {"factory": _I["leather_gloves"],    "price": 484},   # "Luvas de Couro"
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

    # Loja de instância (01/08/2026, expandida 25/08/2026) — vendedor
    # colocado em cada base do battleground de teste (maps/
    # moba_battleground_entities.json::merchants). Estoque = TODO o
    # catálogo (content/item_table.py::ITEMS), preço por raridade — ver
    # bloco INSTANCE_SHOP_* no topo deste arquivo. "stock" aqui é a lista
    # SEM filtro de classe (é o que server/world_server.py::
    # _build_item_caches consome pra validar compra por item_id/nome, tem
    # que conter todo item comprável); o filtro por classe
    # (instance_shop_class_allows) é aplicado em process_shop_buy
    # (servidor, autoritativo) e via INSTANCE_SHOP_STOCK_BY_CLASS (cliente,
    # cosmético).
    "instance_shop": {
        "name": "Vendedor de Instância",
        "color": (200, 170, 60),
        "stock": _INSTANCE_SHOP_FULL_STOCK,
    },
}
