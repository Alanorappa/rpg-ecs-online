"""
instance_shop.py — lista curada de itens compráveis dentro de uma instância
de progressão normalizada (31/07/2026, futuro modo Battlefield, ver
server/instance_progression.py). Subconjunto pequeno do catálogo REAL de
content/item_table.py::ITEMS — não um schema de item novo, só uma lista de
ids já existentes. Escolhido para cobrir: 1 arma inicial por classe, defesa
básica, 1 peça de armadura por armor_class (placa/couro/tecido), 2
acessórios genéricos e 2 consumíveis — o suficiente pra um inventário de 6
slots de instância sem expor o catálogo completo (itens raros/épicos de
progressão real ficam de fora).
"""

INSTANCE_SHOP_ITEM_IDS: list[str] = [
    # armas iniciais por classe
    "iron_sword", "iron_mace", "hunter_bow", "arcane_wand",
    # defesa
    "iron_shield",
    # aljava de ALTA capacidade (01/08/2026, pedido do usuário — munição
    # não deve atrapalhar o PvP dentro da instância; item exclusivo desta
    # loja, ver content/item_table.py::ITEMS["battleground_quiver"])
    "battleground_quiver",
    # armadura básica (1 por armor_class)
    "iron_breastplate", "leather_vest", "tattered_robe",
    # acessórios — ring_power (não steel_ring: level_requirement=12,
    # inequipável no level 1 de instância — bug real relatado pelo
    # usuário, "comprei um anel mas não consegui equipar")
    "ring_power", "simple_amulet",
    # consumíveis
    "hp_potion", "mana_potion",
]
