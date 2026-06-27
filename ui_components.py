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


class DragState:
    """Drag-and-drop de item/skill entre painéis (hotbar, consumable bar,
    habilidades, inventário). NÃO cobre sliders (volume) nem o reorder
    interno do editor de hotbar — ver PROBLEMAS_ARQUITETURA.md item IU3."""
    def __init__(self):
        self.kind:       "str | None" = None   # "skill" | "consumable"
        self.payload:    "str | None" = None   # skill_id ou item_name
        self.source:     "str | None" = None   # "hotbar"|"consumable_bar"|"habilidades"|"inventory"
        self.source_idx: int = -1               # slot de origem (hotbar/consumable_bar); -1 = N/A
        self.start_pos:  "tuple | None" = None  # pos do mouse no MOUSEDOWN (threshold de drag)
        self.active:     bool = False           # True após superar threshold (ghost visível)
        self.shift:      bool = False           # Shift+drag = remover ao soltar fora

    @property
    def dragging(self) -> bool:
        return self.payload is not None

    def reset(self) -> None:
        self.kind = None
        self.payload = None
        self.source = None
        self.source_idx = -1
        self.start_pos = None
        self.active = False
        self.shift = False
