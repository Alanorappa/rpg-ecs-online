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
        # Campo de chat focado — acessível sem referência direta pois
        # PlayerInputSystem precisa bloquear movimento (WASD é lido via
        # pygame.key.get_pressed(), não pelo filtro de systems_events que já
        # bloqueia KEYDOWN/clique dos outros sistemas com modal aberto).
        self.chat_active:     bool = False


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


class TradeUIState:
    """Estado do sistema de trade (player↔player) — acessível por qualquer
    sistema sem referência direta. Ver server/trade_processor.py (autoridade)
    e client/trade_handlers.py (UI). Itens só entram/saem de my_offer/
    Inventory quando o servidor confirma via TRADE_STATE — nunca otimista."""
    def __init__(self):
        self.trade_id:    int = -1   # -1 = janela de trade fechada
        self.other_eid:   int = -1
        self.other_name:  str = ""
        self.my_offer:    list = []   # até 5 Item (dicts reconstruídos p/ exibição)
        self.their_offer: list = []
        self.my_gold:        int = 0
        self.their_gold:     int = 0
        self.my_confirmed:    bool = False
        self.their_confirmed: bool = False
        # Convite recebido, aguardando eu aceitar/recusar (-1 = nenhum)
        self.pending_invite_from_eid:  int = -1
        self.pending_invite_from_name: str = ""
        # Convite que EU mandei, aguardando resposta do outro (-1 = nenhum)
        self.awaiting_response_to_eid:  int = -1
        self.awaiting_response_to_name: str = ""
        # Mini-popup "Trade" (Shift+clique num player remoto, ANTES de
        # qualquer coisa de rede) — eid LOCAL (não server_eid) do alvo clicado.
        self.popup_target_eid:   int = -1
        self.popup_target_name:  str = ""
        self.popup_screen_pos:   tuple = (0, 0)

    @property
    def is_open(self) -> bool:
        return self.trade_id != -1

    def reset(self) -> None:
        self.trade_id = -1
        self.other_eid = -1
        self.other_name = ""
        self.my_offer = []
        self.their_offer = []
        self.my_gold = 0
        self.their_gold = 0
        self.my_confirmed = False
        self.their_confirmed = False

    def clear_invite(self) -> None:
        self.pending_invite_from_eid = -1
        self.pending_invite_from_name = ""
        self.awaiting_response_to_eid = -1
        self.awaiting_response_to_name = ""
