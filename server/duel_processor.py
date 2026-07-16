"""
server/duel_processor.py
Mixin para WorldServer: duelo (player↔player) — convite, aceite, fim
estilo WoW (golpe letal deixa o perdedor com 1 HP, ninguém morre).

Espelha o padrão do TradeProcessorMixin (server/trade_processor.py):
estado como bookkeeping simples no WorldServer (`_duel_pairs`,
`_pending_duel_invites`, criados em WorldServer.__init__), API pública
chamada pelos handlers de server/session.py, e eventos por tick
(`consume_duel_end_events`) consumidos pelo broadcast loop do
SessionManager — necessário porque o fim por golpe letal acontece DENTRO
de apply_damage_core (síncrono, no meio do tick), longe de qualquer
sessão async.

O par em `_duel_pairs` é o que o contexto PvP consulta
(WorldServer._pvp_allowed_between) — dupla em duelo é hostil somente um
ao outro; o resto do mundo continua amigável.
"""
from __future__ import annotations

from shared.constants import TRADE_MAX_DIST_TILES, DUEL_MAX_DIST_TILES


class DuelProcessorMixin:

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _duel_pair_of(self, eid: int) -> "frozenset | None":
        """Par de duelo que contém `eid` (None se não está duelando).
        Scan linear — duelos ativos são raríssimos (dezenas no máximo)."""
        for pair in self._duel_pairs:
            if eid in pair:
                return pair
        return None

    def _duel_in_range(self, eid_a: int, eid_b: int, max_dist: int) -> bool:
        """Mesmo mapa e chebyshev <= max_dist (convite usa o raio do trade;
        manutenção do duelo usa DUEL_MAX_DIST_TILES)."""
        if self.get_entity_map(eid_a) != self.get_entity_map(eid_b):
            return False
        from engine.components import TileMovement as _DuelTM
        from engine.utils import chebyshev
        tm_a = self.world.get_component(eid_a, _DuelTM)
        tm_b = self.world.get_component(eid_b, _DuelTM)
        if not tm_a or not tm_b:
            return False
        return chebyshev(tm_a.current_tile_x, tm_a.current_tile_y,
                         tm_b.current_tile_x, tm_b.current_tile_y) <= max_dist

    # ── API pública (handlers de server/session.py) ──────────────────────────

    def request_duel(self, requester_eid: int, target_eid: int) -> "str | None":
        """None = convite registrado (handler manda DUEL_INVITE só pro
        target). Reason str = recusado na origem (handler manda DUEL_END
        {reason} só pro requester, sem gerar convite)."""
        if not self.pvp_enabled:            # kill-switch global de PvP
            return "invalid"
        if requester_eid == target_eid:
            return "invalid"
        if self._duel_pair_of(requester_eid) or self._duel_pair_of(target_eid):
            return "invalid"                # um dos dois já está duelando
        if target_eid in self._pending_duel_invites:
            return "invalid"                # alvo já tem convite aguardando
        if requester_eid in self._pending_duel_invites.values():
            return "invalid"                # requester já convidou alguém
        if (self.get_session_id_for_player(requester_eid) is None
                or self.get_session_id_for_player(target_eid) is None):
            return "invalid"
        if not self._duel_in_range(requester_eid, target_eid, TRADE_MAX_DIST_TILES):
            return "invalid"
        self._pending_duel_invites[target_eid] = requester_eid
        return None

    def respond_duel_invite(self, target_eid: int, accept: bool):
        """target_eid respondeu ao convite pendente.
        Retorna (requester_eid, started: bool). (None, False) se não havia
        convite. started=False com requester != None = recusado/inválido
        (handler manda DUEL_END{reason:"declined"} pro requester)."""
        requester_eid = self._pending_duel_invites.pop(target_eid, None)
        if requester_eid is None:
            return None, False
        if (not accept
                or self._duel_pair_of(requester_eid) or self._duel_pair_of(target_eid)
                or not self._duel_in_range(requester_eid, target_eid, TRADE_MAX_DIST_TILES)):
            return requester_eid, False
        self._duel_pairs[frozenset((requester_eid, target_eid))] = {
            "started_tick": self.tick_count,
        }
        return requester_eid, True

    def end_duel(self, pair: frozenset, winner_eid: int, reason: str) -> None:
        """Encerra o duelo e enfileira o DUEL_END pros dois lados (consumido
        pelo broadcast loop do SessionManager — consume_duel_end_events).
        winner_eid=-1 quando não há vencedor (distance/disconnect)."""
        if self._duel_pairs.pop(pair, None) is None:
            return
        eids = list(pair)
        loser_eid = -1
        if winner_eid in eids:
            loser_eid = eids[0] if eids[1] == winner_eid else eids[1]
        self._duel_end_events_this_tick.append({
            "player_a":   eids[0],
            "player_b":   eids[1],
            "winner_eid": winner_eid,
            "loser_eid":  loser_eid,
            "reason":     reason,
        })

    def end_duels_of(self, eid: int, reason: str) -> None:
        """Encerra qualquer duelo envolvendo `eid` (logout/morte externa) —
        chamado pelo disconnect do SessionManager, mesmo ponto do trade."""
        pair = self._duel_pair_of(eid)
        if pair is not None:
            self.end_duel(pair, winner_eid=-1, reason=reason)
        # Convites pendentes envolvendo o eid morrem junto
        self._pending_duel_invites.pop(eid, None)
        for tgt, req in list(self._pending_duel_invites.items()):
            if req == eid:
                self._pending_duel_invites.pop(tgt, None)

    # ── Interceptor de golpe letal (estilo WoW) ──────────────────────────────

    def _duel_lethal_interceptor(self, world, killer_eid: int, target_id: int) -> bool:
        """Registrado em engine/core_systems.register_lethal_interceptor no
        boot (_load_all_maps). True = intercepta a morte: apply_damage_core
        deixa o alvo com 1 HP em vez de matar, e o duelo termina com o
        atacante como vencedor — ninguém morre, sem corpse/respawn/perda."""
        pair = frozenset((killer_eid, target_id))
        if pair not in self._duel_pairs:
            return False
        self.end_duel(pair, winner_eid=killer_eid, reason="win")
        return True

    # ── Tick: encerramento por distância ─────────────────────────────────────

    def _tick_duel_distance_check(self) -> None:
        for pair in list(self._duel_pairs.keys()):
            eids = list(pair)
            if not self._duel_in_range(eids[0], eids[1], DUEL_MAX_DIST_TILES):
                self.end_duel(pair, winner_eid=-1, reason="distance")

    def consume_duel_end_events(self) -> list[dict]:
        """Retorna e limpa os DUEL_END do tick atual (broadcast loop)."""
        result = list(self._duel_end_events_this_tick)
        self._duel_end_events_this_tick.clear()
        return result
