"""
server/trade_processor.py
Mixin para WorldServer: sessão de trade (player↔player) — convite, ofertas de
item/gold, confirmação e execução/cancelamento. Sem ECS (mesmo nível de
bookkeeping que _corpses/_mob_damage_log em WorldServer) — TradeSession é um
dict de estado simples, não um Entity.

Regra de custódia (ver arquitetura/PROBLEMAS_ARQUITETURA.md): item ofertado é
REMOVIDO da Inventory real na hora (não só "travado") e gold ofertado é
debitado da Wallet na hora — fica em custódia na TradeSession até confirmar
(soma no outro lado) ou cancelar (devolve pro dono original). Isso elimina de
graça qualquer chance de vender/equipar/usar um item ofertado enquanto o
trade está aberto, sem precisar checar nada em nenhum sistema existente.
"""
from __future__ import annotations

from shared.constants import TRADE_MAX_DIST_TILES


class TradeSession:
    """Estado de uma negociação ativa entre dois players."""

    __slots__ = ("trade_id", "player_a", "player_b", "offer_a", "offer_b",
                 "gold_a", "gold_b", "confirmed_a", "confirmed_b")

    def __init__(self, trade_id: int, player_a: int, player_b: int) -> None:
        self.trade_id: int = trade_id
        self.player_a: int = player_a
        self.player_b: int = player_b
        self.offer_a: list = []
        self.offer_b: list = []
        self.gold_a: int = 0
        self.gold_b: int = 0
        self.confirmed_a: bool = False
        self.confirmed_b: bool = False

    def other_of(self, eid: int) -> int:
        return self.player_b if eid == self.player_a else self.player_a

    def offer_of(self, eid: int) -> list:
        return self.offer_a if eid == self.player_a else self.offer_b

    def gold_of(self, eid: int) -> int:
        return self.gold_a if eid == self.player_a else self.gold_b

    def set_gold_of(self, eid: int, amount: int) -> None:
        if eid == self.player_a:
            self.gold_a = amount
        else:
            self.gold_b = amount

    def set_confirmed_of(self, eid: int, value: bool) -> None:
        if eid == self.player_a:
            self.confirmed_a = value
        else:
            self.confirmed_b = value

    def reset_confirms(self) -> None:
        self.confirmed_a = False
        self.confirmed_b = False


class TradeProcessorMixin:

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _trade_in_range(self, eid_a: int, eid_b: int) -> bool:
        """Mesmo mapa e distância chebyshev <= TRADE_MAX_DIST_TILES."""
        if self.get_entity_map(eid_a) != self.get_entity_map(eid_b):
            return False
        from engine.components import TileMovement as _TradeTM
        from engine.utils import chebyshev
        tm_a = self.world.get_component(eid_a, _TradeTM)
        tm_b = self.world.get_component(eid_b, _TradeTM)
        if not tm_a or not tm_b:
            return False
        return chebyshev(tm_a.current_tile_x, tm_a.current_tile_y,
                          tm_b.current_tile_x, tm_b.current_tile_y) <= TRADE_MAX_DIST_TILES

    def _cancel_trade_session(self, session: TradeSession, reason: str) -> None:
        """Devolve itens/gold em custódia dos DOIS lados e remove a sessão."""
        from engine.components import Inventory as _TradeInv, Wallet as _TradeWallet
        inv_a = self.world.get_component(session.player_a, _TradeInv)
        inv_b = self.world.get_component(session.player_b, _TradeInv)
        wallet_a = self.world.get_component(session.player_a, _TradeWallet)
        wallet_b = self.world.get_component(session.player_b, _TradeWallet)
        if inv_a is not None:
            inv_a.items.extend(session.offer_a)
        if inv_b is not None:
            inv_b.items.extend(session.offer_b)
        if wallet_a is not None:
            wallet_a.gold += session.gold_a
        if wallet_b is not None:
            wallet_b.gold += session.gold_b
        self._remove_trade_session(session.trade_id)

    def _remove_trade_session(self, trade_id: int) -> None:
        session = self._trade_sessions.pop(trade_id, None)
        if session is not None:
            self._player_trade.pop(session.player_a, None)
            self._player_trade.pop(session.player_b, None)

    def _execute_trade(self, session: TradeSession) -> str:
        """Troca itens/gold cruzados. Valida espaço de Inventory nos dois
        lados ANTES de mexer em qualquer coisa — se faltar espaço, cancela
        (devolve tudo) e retorna "inventory_full" em vez de "executed"."""
        from engine.components import Inventory as _TradeInv, Wallet as _TradeWallet
        inv_a = self.world.get_component(session.player_a, _TradeInv)
        inv_b = self.world.get_component(session.player_b, _TradeInv)
        if inv_a is None or inv_b is None:
            self._cancel_trade_session(session, "invalid")
            return "invalid"
        free_a = inv_a.max_slots - len(inv_a.items)
        free_b = inv_b.max_slots - len(inv_b.items)
        if len(session.offer_b) > free_a or len(session.offer_a) > free_b:
            self._cancel_trade_session(session, "inventory_full")
            return "inventory_full"
        wallet_a = self.world.get_component(session.player_a, _TradeWallet)
        wallet_b = self.world.get_component(session.player_b, _TradeWallet)
        inv_a.items.extend(session.offer_b)
        inv_b.items.extend(session.offer_a)
        if wallet_a is not None:
            wallet_a.gold += session.gold_b
        if wallet_b is not None:
            wallet_b.gold += session.gold_a
        self._remove_trade_session(session.trade_id)
        return "executed"

    # ── API pública (chamada pelos handlers de server/session.py) ───────────

    def request_trade(self, requester_eid: int, target_eid: int) -> str | None:
        """None = convite registrado com sucesso (handler manda TRADE_INVITE
        só pro target). Reason str = recusado (handler manda TRADE_CANCELLED
        {trade_id:-1, reason} só pro requester, sem gerar TRADE_INVITE)."""
        if requester_eid == target_eid:
            return "invalid"
        if requester_eid in self._player_trade or target_eid in self._player_trade:
            return "invalid"
        if target_eid in self._pending_trade_invites:
            return "invalid"  # já tem convite aguardando resposta
        if requester_eid in self._pending_trade_invites.values():
            return "invalid"  # requester já tem convite pendente noutro lugar
        if (self.get_session_id_for_player(requester_eid) is None
                or self.get_session_id_for_player(target_eid) is None):
            return "invalid"
        if not self._trade_in_range(requester_eid, target_eid):
            return "invalid"
        self._pending_trade_invites[target_eid] = requester_eid
        return None

    def respond_trade_invite(self, target_eid: int, accept: bool):
        """target_eid respondeu a um convite pendente.
        Retorna (requester_eid, trade_id) — trade_id é None se recusado ou
        se a validação falhou ao aceitar (handler decide o reason a partir
        do `accept` que ele mesmo recebeu: False→"declined", True→"invalid").
        Retorna (None, None) se não havia convite pendente (nada a fazer)."""
        requester_eid = self._pending_trade_invites.pop(target_eid, None)
        if requester_eid is None:
            return None, None
        if (not accept
                or requester_eid in self._player_trade
                or target_eid in self._player_trade
                or not self._trade_in_range(requester_eid, target_eid)):
            return requester_eid, None
        trade_id = self._next_trade_id
        self._next_trade_id += 1
        session = TradeSession(trade_id, requester_eid, target_eid)
        self._trade_sessions[trade_id] = session
        self._player_trade[requester_eid] = trade_id
        self._player_trade[target_eid] = trade_id
        return requester_eid, trade_id

    def add_trade_item(self, player_eid: int, inv_index: int) -> str | None:
        """None = ok. Reason str = rejeitado (sem side effect)."""
        session = self._trade_sessions.get(self._player_trade.get(player_eid, -1))
        if session is None:
            return "invalid"
        offer = session.offer_of(player_eid)
        if len(offer) >= 5:
            return "invalid"
        from engine.components import Inventory as _TradeInv
        inv = self.world.get_component(player_eid, _TradeInv)
        if not inv or not (0 <= inv_index < len(inv.items)):
            return "invalid"
        item = inv.items.pop(inv_index)
        offer.append(item)
        session.reset_confirms()
        return None

    def withdraw_trade_item(self, player_eid: int, offer_slot: int) -> str | None:
        session = self._trade_sessions.get(self._player_trade.get(player_eid, -1))
        if session is None:
            return "invalid"
        offer = session.offer_of(player_eid)
        if not (0 <= offer_slot < len(offer)):
            return "invalid"
        from engine.components import Inventory as _TradeInv
        inv = self.world.get_component(player_eid, _TradeInv)
        if not inv:
            return "invalid"
        if len(inv.items) >= inv.max_slots:
            return "inventory_full"
        item = offer.pop(offer_slot)
        inv.items.append(item)
        session.reset_confirms()
        return None

    def set_trade_gold(self, player_eid: int, amount: int) -> str | None:
        session = self._trade_sessions.get(self._player_trade.get(player_eid, -1))
        if session is None:
            return "invalid"
        if amount < 0:
            return "invalid"
        from engine.components import Wallet as _TradeWallet
        wallet = self.world.get_component(player_eid, _TradeWallet)
        if not wallet:
            return "invalid"
        current_offered = session.gold_of(player_eid)
        available = wallet.gold + current_offered
        if amount > available:
            return "invalid"
        wallet.gold -= (amount - current_offered)
        session.set_gold_of(player_eid, amount)
        session.reset_confirms()
        return None

    def confirm_trade(self, player_eid: int):
        """Retorna (status, session):
        status "invalid" (sem sessão, session=None), "state" (só confirmou,
        aguardando o outro), "executed" ou "inventory_full" (execução
        tentada e recusada — sessão já foi cancelada/devolvida nesse caso).
        `session` continua com offer_a/offer_b/gold_a/gold_b intactos MESMO
        após "executed" (removida do dict, mas extend() não esvazia a lista
        de origem) — handler usa isso pra montar TRADE_RESULT de cada lado
        sem precisar reconsultar uma sessão que já não existe mais."""
        session = self._trade_sessions.get(self._player_trade.get(player_eid, -1))
        if session is None:
            return "invalid", None
        session.set_confirmed_of(player_eid, True)
        if session.confirmed_a and session.confirmed_b:
            status = self._execute_trade(session)
            return status, session
        return "state", session

    def cancel_trade(self, player_eid: int, reason: str = "cancelled") -> int | None:
        """Cancela a trade ativa do player, devolvendo tudo dos dois lados.
        Retorna o trade_id cancelado (pra handler notificar o outro lado) ou
        None se o player não estava em nenhuma trade."""
        trade_id = self._player_trade.get(player_eid)
        if trade_id is None:
            return None
        session = self._trade_sessions.get(trade_id)
        if session is None:
            return None
        self._cancel_trade_session(session, reason)
        return trade_id

    def get_trade_session(self, player_eid: int) -> TradeSession | None:
        return self._trade_sessions.get(self._player_trade.get(player_eid, -1))

    def build_trade_state_payload(self, session: TradeSession, viewer_eid: int) -> dict:
        """Monta o TRADE_STATE do PONTO DE VISTA de viewer_eid (my_*/their_*
        já resolvidos — cliente nunca precisa comparar eid)."""
        other_eid = session.other_of(viewer_eid)
        return {
            "trade_id":        session.trade_id,
            "my_offer":        [self._item_data_from_obj(i) for i in session.offer_of(viewer_eid)],
            "my_gold":         session.gold_of(viewer_eid),
            "their_offer":     [self._item_data_from_obj(i) for i in session.offer_of(other_eid)],
            "their_gold":      session.gold_of(other_eid),
            "my_confirmed":    session.confirmed_a if viewer_eid == session.player_a else session.confirmed_b,
            "their_confirmed": session.confirmed_b if viewer_eid == session.player_a else session.confirmed_a,
        }

    # ── Tick: cancelamento por distância ─────────────────────────────────────

    def _tick_trade_distance_check(self) -> None:
        for trade_id in list(self._trade_sessions.keys()):
            session = self._trade_sessions.get(trade_id)
            if session is None:
                continue
            if not self._trade_in_range(session.player_a, session.player_b):
                self._trade_cancellations_this_tick.append({
                    "trade_id": trade_id,
                    "player_a": session.player_a,
                    "player_b": session.player_b,
                    "reason":   "distance",
                })
                self._cancel_trade_session(session, "distance")

    def consume_trade_cancellations(self) -> list[dict]:
        """Retorna e limpa cancelamentos de trade (por distância) do tick atual."""
        result = list(self._trade_cancellations_this_tick)
        self._trade_cancellations_this_tick.clear()
        return result
