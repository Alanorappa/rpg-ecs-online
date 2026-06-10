# ui_components.py
"""Componentes de estado de UI do cliente (inventário, talentos, loja, loot).

Separados de components.py porque components.py é compartilhado com o
servidor (combate, movimento, etc.) — estes componentes existem apenas
para a renderização local do jogador.
"""
from __future__ import annotations


class UIState:
    """Estado de painéis de UI do jogador (inventário, talentos)."""
    def __init__(self):
        self.show_inventory: bool = False
        self.show_talents:   bool = False


class ShopUIState:
    """Estado do modal de loja — acessível por qualquer sistema sem referência direta."""
    def __init__(self):
        self.open_merchant_id: int = -1   # -1 = fechado

    @property
    def is_open(self) -> bool:
        return self.open_merchant_id != -1


class LootUIState:
    """Estado do modal de loot — acessível por qualquer sistema sem referência direta."""
    def __init__(self):
        self.open_corpse_id: int = -1   # -1 = fechado
