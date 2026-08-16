# crafting_data.py
"""
Dados de crafting: materiais, tabela de reciclagem e receitas da forja.

RECYCLE_TABLE  — chaveada por (item_type, subtype, rarity)
RECIPES        — chaveado por recipe_id
MATERIALS      — factories de itens de material
RECIPE_ITEMS   — factories de itens de receita (droppáveis / vendáveis)
"""
from __future__ import annotations
from engine.components import Item, Modifier
from content.item_table import _derive_item_level


# ---------------------------------------------------------------------------
# Custos por raridade
# ---------------------------------------------------------------------------
RARITY_RECYCLE_COST: dict = {
    "common":    250,
    "uncommon":  500,
    "rare":      1000,
    "epic":      2500,
    "legendary": 5000,
    "mythic":    10000,
}
RARITY_FORGE_COST: dict = RARITY_RECYCLE_COST


# ---------------------------------------------------------------------------
# Materiais
# ---------------------------------------------------------------------------
def _mat(name: str, rarity: str = "common", value: int = 0):
    def factory(_item_id: str = ""):
        item = Item(name=name, item_type="material", slot="",
                    rarity=rarity, value=value, max_stack=99)
        item.item_id = _item_id
        return item
    return factory


# item_id = a própria chave do dict (débito C2, 10/08/2026 — mesmo padrão
# de content/item_table.py::ITEMS). `_mat`'s factory recebe `_item_id` mas
# não sabe a chave sozinha — MATERIALS é montado com dict comprehension
# injetando a chave em cada factory (mesmo princípio de
# item_table.py::_with_derived_level).
_RAW_MATERIALS: dict = {
    "fragmento_ferro":  _mat("Fragmento de Ferro",  "common",   500),
    "fibra_madeira":    _mat("Fibra de Madeira",     "common",   300),
    "tira_couro":       _mat("Tira de Couro",        "common",   300),
    "fibra_resistente": _mat("Fibra Resistente",     "uncommon", 700),
    "po_de_joia":       _mat("Po de Joia",           "common",   400),
    "fio_de_prata":     _mat("Fio de Prata",         "uncommon", 800),
    "essencia_comum":   _mat("Essencia Comum",       "uncommon", 1000),
    "essencia_arcana":  _mat("Essencia Arcana",      "rare",     2000),
    "joia_bruta":       _mat("Joia Bruta",           "rare",     2500),
    "cristal_poder":    _mat("Cristal de Poder",     "epic",     5000),
}


def _with_item_id(key: str, factory):
    def wrapped():
        return factory(key)
    return wrapped


MATERIALS: dict = {key: _with_item_id(key, f) for key, f in _RAW_MATERIALS.items()}


# ---------------------------------------------------------------------------
# Tabela de reciclagem
# ---------------------------------------------------------------------------
RECYCLE_TABLE: dict = {
    # --- Sword ---
    ("weapon", "Sword", "common"):    [("fragmento_ferro", 2), ("fibra_madeira", 1)],
    ("weapon", "Sword", "uncommon"):  [("fragmento_ferro", 3), ("fibra_madeira", 1), ("essencia_comum", 1)],
    ("weapon", "Sword", "rare"):      [("fragmento_ferro", 4), ("essencia_arcana", 1), ("joia_bruta", 1)],
    ("weapon", "Sword", "epic"):      [("fragmento_ferro", 5), ("essencia_arcana", 2), ("cristal_poder", 1)],
    ("weapon", "Sword", "legendary"): [("fragmento_ferro", 6), ("essencia_arcana", 3), ("cristal_poder", 2)],
    ("weapon", "Sword", "mythic"):    [("fragmento_ferro", 8), ("essencia_arcana", 4), ("cristal_poder", 3)],
    # --- Dagger ---
    ("weapon", "Dagger", "common"):   [("fragmento_ferro", 1), ("tira_couro", 1)],
    ("weapon", "Dagger", "uncommon"): [("fragmento_ferro", 2), ("tira_couro", 1), ("essencia_comum", 1)],
    ("weapon", "Dagger", "rare"):     [("fragmento_ferro", 2), ("tira_couro", 2), ("essencia_arcana", 1)],
    ("weapon", "Dagger", "epic"):     [("fragmento_ferro", 3), ("tira_couro", 2), ("essencia_arcana", 1), ("cristal_poder", 1)],
    ("weapon", "Dagger", "legendary"):[("fragmento_ferro", 4), ("tira_couro", 3), ("essencia_arcana", 2), ("cristal_poder", 2)],
    ("weapon", "Dagger", "mythic"):   [("fragmento_ferro", 5), ("tira_couro", 4), ("essencia_arcana", 3), ("cristal_poder", 3)],
    # --- Wand ---
    ("weapon", "Wand", "common"):     [("fibra_madeira", 2), ("fragmento_ferro", 1)],
    ("weapon", "Wand", "uncommon"):   [("fibra_madeira", 2), ("essencia_comum", 1), ("fragmento_ferro", 1)],
    ("weapon", "Wand", "rare"):       [("fibra_madeira", 3), ("essencia_arcana", 2)],
    ("weapon", "Wand", "epic"):       [("fibra_madeira", 3), ("essencia_arcana", 2), ("cristal_poder", 1)],
    ("weapon", "Wand", "legendary"):  [("fibra_madeira", 4), ("essencia_arcana", 3), ("cristal_poder", 2)],
    ("weapon", "Wand", "mythic"):     [("fibra_madeira", 5), ("essencia_arcana", 4), ("cristal_poder", 3)],
    # --- Shield ---
    ("shield", "", "common"):         [("fragmento_ferro", 2), ("fibra_madeira", 2)],
    ("shield", "", "uncommon"):       [("fragmento_ferro", 3), ("fibra_madeira", 2), ("essencia_comum", 1)],
    ("shield", "", "rare"):           [("fragmento_ferro", 4), ("fibra_madeira", 2), ("joia_bruta", 1)],
    ("shield", "", "epic"):           [("fragmento_ferro", 5), ("fibra_madeira", 2), ("essencia_arcana", 1), ("cristal_poder", 1)],
    ("shield", "", "legendary"):      [("fragmento_ferro", 6), ("fibra_madeira", 3), ("essencia_arcana", 2), ("cristal_poder", 2)],
    ("shield", "", "mythic"):         [("fragmento_ferro", 8), ("fibra_madeira", 4), ("essencia_arcana", 3), ("cristal_poder", 3)],
    # --- Armor ---
    ("armor", "", "common"):          [("tira_couro", 2), ("fibra_resistente", 1)],
    ("armor", "", "uncommon"):        [("tira_couro", 3), ("fibra_resistente", 1), ("essencia_comum", 1)],
    ("armor", "", "rare"):            [("tira_couro", 4), ("fibra_resistente", 2), ("joia_bruta", 1)],
    ("armor", "", "epic"):            [("tira_couro", 4), ("fibra_resistente", 2), ("essencia_arcana", 1), ("cristal_poder", 1)],
    ("armor", "", "legendary"):       [("tira_couro", 5), ("fibra_resistente", 3), ("essencia_arcana", 2), ("cristal_poder", 2)],
    ("armor", "", "mythic"):          [("tira_couro", 6), ("fibra_resistente", 4), ("essencia_arcana", 3), ("cristal_poder", 3)],
    # --- Jewelry ---
    ("jewelry", "", "common"):        [("po_de_joia", 1)],
    ("jewelry", "", "uncommon"):      [("po_de_joia", 2), ("fio_de_prata", 1)],
    ("jewelry", "", "rare"):          [("po_de_joia", 3), ("fio_de_prata", 1), ("joia_bruta", 1)],
    ("jewelry", "", "epic"):          [("po_de_joia", 3), ("fio_de_prata", 2), ("joia_bruta", 1), ("cristal_poder", 1)],
    ("jewelry", "", "legendary"):     [("po_de_joia", 4), ("fio_de_prata", 3), ("joia_bruta", 2), ("cristal_poder", 2)],
    ("jewelry", "", "mythic"):        [("po_de_joia", 5), ("fio_de_prata", 4), ("joia_bruta", 3), ("cristal_poder", 3)],
}


def get_recycle_materials(item: Item) -> list:
    """Retorna [(mat_id, qty), ...] para um item. Lista vazia se não reciclável."""
    if item.item_type not in ("weapon", "armor", "shield", "jewelry"):
        return []
    subtype = getattr(item, "subtype", "")
    key = (item.item_type, subtype, item.rarity)
    if key in RECYCLE_TABLE:
        return list(RECYCLE_TABLE[key])
    # Fallback sem subtype (armor/shield/jewelry normalmente não têm subtype)
    return list(RECYCLE_TABLE.get((item.item_type, "", item.rarity), []))


# ---------------------------------------------------------------------------
# Receitas
# ---------------------------------------------------------------------------
def _make_result(name, item_type, slot, rarity, value, mods=None,
                 two_handed=False, damage_min=0, damage_max=0,
                 attack_speed=0.0, subtype=""):
    mods = mods or []
    def factory(_item_id: str = ""):
        item = Item(
            name=name, item_type=item_type, slot=slot,
            modifiers=[Modifier(attr, val, typ) for attr, val, typ in mods],
            rarity=rarity, value=value, two_handed=two_handed,
            damage_min=damage_min, damage_max=damage_max,
            attack_speed=attack_speed, subtype=subtype,
            item_level=_derive_item_level(rarity, value), level_requirement=1,
        )
        item.item_id = _item_id
        return item
    return factory


# item_id do item CRAFTADO (resultado) = a própria chave de RECIPES (débito
# C2, 10/08/2026) — reusa `recipe_id` como item_id, já que a relação é 1:1
# (1 receita produz sempre o mesmo item resultado). Distinto do item_id da
# RECEITA EM SI (RECIPE_ITEMS abaixo, "recipe_"+recipe_id) — são 2 itens
# diferentes ("Espada Afiada" craftada vs "Receita: Espada Afiada", o
# pergaminho que ensina a receita), não podem colidir no mesmo id.
_RAW_RECIPES: dict = {
    "espada_afiada": {
        "name":           "Espada Afiada",
        "result_rarity":  "uncommon",
        "result_factory": _make_result(
            "Espada Afiada", "weapon", "mainhand", "uncommon", 120,
            mods=[("attack_power", 6, "flat"), ("crit_rating", 0.03, "flat")],
            damage_min=10, damage_max=18, attack_speed=1.5, subtype="Sword",
        ),
        "materials": [("fragmento_ferro", 5), ("fibra_madeira", 2), ("tira_couro", 1)],
    },
    "cota_reforcada": {
        "name":           "Cota Reforcada",
        "result_rarity":  "uncommon",
        "result_factory": _make_result(
            "Cota Reforcada", "armor", "chest", "uncommon", 110,
            mods=[("armor", 12, "flat"), ("stamina", 4, "flat")],
        ),
        "materials": [("tira_couro", 4), ("fibra_resistente", 3), ("fragmento_ferro", 2)],
    },
    "amuleto_protecao": {
        "name":           "Amuleto de Protecao",
        "result_rarity":  "uncommon",
        "result_factory": _make_result(
            "Amuleto de Protecao", "jewelry", "neck", "uncommon", 130,
            mods=[("stamina", 6, "flat"), ("armor", 4, "flat")],
        ),
        "materials": [("po_de_joia", 3), ("fio_de_prata", 2), ("fragmento_ferro", 1)],
    },
    "espada_runica": {
        "name":           "Espada Runica",
        "result_rarity":  "rare",
        "result_factory": _make_result(
            "Espada Runica", "weapon", "mainhand", "rare", 300,
            mods=[("attack_power", 12, "flat"), ("spell_power", 6, "flat"),
                  ("crit_rating", 0.05, "flat")],
            damage_min=16, damage_max=28, attack_speed=1.6, subtype="Sword",
        ),
        "materials": [("fragmento_ferro", 8), ("essencia_arcana", 3), ("joia_bruta", 2)],
    },
}


def _bind_result_item_id(key: str, result_factory):
    def wrapped():
        return result_factory(key)
    return wrapped


def _recipes_with_item_id(raw: dict) -> dict:
    out = {}
    for key, entry in raw.items():
        new_entry = dict(entry)
        new_entry["result_factory"] = _bind_result_item_id(key, entry["result_factory"])
        out[key] = new_entry
    return out


RECIPES: dict = _recipes_with_item_id(_RAW_RECIPES)


# ---------------------------------------------------------------------------
# Itens de receita (droppáveis de humanoides / vendidos pelo ferreiro)
# ---------------------------------------------------------------------------
def _make_recipe_item(display_name: str, recipe_id: str,
                      rarity: str = "common", value: int = 80):
    def factory():
        item = Item(name=display_name, item_type="recipe", slot="",
                    rarity=rarity, value=value,
                    consumable={"learn_recipe": recipe_id},
                    # "recipe_" prefix: distingue do item_id do item
                    # CRAFTADO (RECIPES[recipe_id], mesma chave sem
                    # prefixo) — são 2 itens diferentes, nunca podem
                    # colidir no mesmo id (débito C2, 10/08/2026).
                    item_id=f"recipe_{recipe_id}")
        item.recipe_id = recipe_id
        return item
    return factory


RECIPE_ITEMS: dict = {
    "espada_afiada":    _make_recipe_item("Receita: Espada Afiada",       "espada_afiada",    "common",   80),
    "cota_reforcada":   _make_recipe_item("Receita: Cota Reforcada",      "cota_reforcada",   "common",   80),
    "amuleto_protecao": _make_recipe_item("Receita: Amuleto de Protecao", "amuleto_protecao", "uncommon", 150),
    "espada_runica":    _make_recipe_item("Receita: Espada Runica",       "espada_runica",    "rare",     500),
}
