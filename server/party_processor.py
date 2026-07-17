"""
server/party_processor.py
Mixin para WorldServer: party/grupo (N players) — convite (via modal ou
comando de chat "/convidar"), aceite, saída, expulsão, promoção de líder.

Espelha o padrão do DuelProcessorMixin/TradeProcessorMixin: estado como
bookkeeping simples no WorldServer, API pública chamada pelos handlers
de server/session.py, e eventos por tick (`consume_party_state_events`)
consumidos pelo broadcast loop do SessionManager.

Diferença estrutural do duelo/trade: grupo é N-ário (até PARTY_MAX_SIZE),
não um par fixo — por isso o estado é `_parties: dict[party_id, dict]`
(não um frozenset de 2 eids) com índice reverso `_player_party_id` pra
achar rápido "de qual grupo eid faz parte".

Sem checagem de distância pra convidar/permanecer agrupado (decisão de
design 17/07/2026, documentada em ARQUITETURA_ONLINE.md §34.19) —
diferente de trade/duelo, replica o /invite de WoW (funciona no mapa
inteiro); a única restrição de fato é o cliente só resolver nomes de
players VISÍVEIS (RemoteControlled local) pro comando de chat.
"""
from __future__ import annotations

from shared.constants import PARTY_MAX_SIZE


class PartyProcessorMixin:

    # ── Helpers internos ─────────────────────────────────────────────────────

    def get_party_id_of(self, eid: int) -> int:
        return self._player_party_id.get(eid, -1)

    def get_party_members(self, eid: int) -> list[int]:
        """Membros do grupo de `eid` (inclui o próprio eid). `[]` se não
        está em nenhum grupo."""
        pid = self.get_party_id_of(eid)
        if pid == -1:
            return []
        party = self._parties.get(pid)
        return list(party["members"]) if party else []

    def get_party_snapshot(self, party_id: int) -> "dict | None":
        """Monta o payload de PARTY_STATE lendo componentes direto (sem
        depender de Session — este mixin vive no WorldServer, que não
        tem acesso aos objetos Session do SessionManager)."""
        party = self._parties.get(party_id)
        if party is None:
            return None
        from engine.components import CharacterStats, CombatStats
        members = []
        for m_eid in party["members"]:
            char = self.world.get_component(m_eid, CharacterStats)
            cs   = self.world.get_component(m_eid, CombatStats)
            members.append({
                "eid":      m_eid,
                "name":     char.name if char else "?",
                "class_id": char.class_id if char else "",
                "level":    char.level if char else 1,
                "hp":       cs.current_hp if cs else 0,
                "hp_max":   cs.max_hp if cs else 0,
            })
        return {
            "party_id":   party_id,
            "leader_eid": party["leader_eid"],
            "members":    members,
        }

    def _party_members_in_range(self, party_id: int, tx: int, ty: int,
                                map_file: "str | None", radius: int) -> list[int]:
        """Membros do grupo dentro do raio (chebyshev) de (tx,ty), no
        MESMO mapa — usado pelo split de XP compartilhado. Mesmo padrão
        de `DuelProcessorMixin._duel_in_range`, generalizado pra N
        membros."""
        party = self._parties.get(party_id)
        if party is None:
            return []
        from engine.components import TileMovement as _PartyTM
        from engine.utils import chebyshev
        result = []
        for m_eid in party["members"]:
            if map_file is not None and self.get_entity_map(m_eid) != map_file:
                continue
            tm = self.world.get_component(m_eid, _PartyTM)
            if tm is None:
                continue
            if chebyshev(tx, ty, tm.current_tile_x, tm.current_tile_y) <= radius:
                result.append(m_eid)
        return result

    # ── API pública (handlers de server/session.py) ──────────────────────────

    def request_party_invite(self, requester_eid: int, target_eid: int) -> "str | None":
        """None = convite registrado (handler manda PARTY_INVITE_RECEIVED
        só pro target). Reason str = recusado na origem (handler manda
        PARTY_INVITE_FAILED só pro requester)."""
        if requester_eid == target_eid:
            return "invalid"
        if self.get_party_id_of(target_eid) != -1:
            return "invalid"                # alvo já está em um grupo
        if target_eid in self._pending_party_invites:
            return "invalid"                # alvo já tem convite aguardando
        requester_pid = self.get_party_id_of(requester_eid)
        if requester_pid != -1:
            party = self._parties[requester_pid]
            if len(party["members"]) >= PARTY_MAX_SIZE:
                return "invalid"            # grupo já no limite
        if (self.get_session_id_for_player(requester_eid) is None
                or self.get_session_id_for_player(target_eid) is None):
            return "invalid"
        self._pending_party_invites[target_eid] = requester_eid
        return None

    def respond_party_invite(self, target_eid: int, accept: bool):
        """target_eid respondeu ao convite pendente.
        Retorna (requester_eid, started: bool). (None, False) se não havia
        convite. started=False com requester != None = recusado/inválido
        (handler manda PARTY_INVITE_FAILED{reason:"declined"} pro requester)."""
        requester_eid = self._pending_party_invites.pop(target_eid, None)
        if requester_eid is None:
            return None, False
        if not accept or self.get_party_id_of(target_eid) != -1:
            return requester_eid, False

        requester_pid = self.get_party_id_of(requester_eid)
        if requester_pid == -1:
            # Cria grupo novo: requester vira líder
            requester_pid = self._next_party_id
            self._next_party_id += 1
            self._parties[requester_pid] = {
                "leader_eid": requester_eid,
                "members":    [requester_eid],
            }
            self._player_party_id[requester_eid] = requester_pid

        party = self._parties[requester_pid]
        if len(party["members"]) >= PARTY_MAX_SIZE:
            return requester_eid, False     # encheu entre o convite e o aceite

        party["members"].append(target_eid)
        self._player_party_id[target_eid] = requester_pid
        self._party_state_events_this_tick.append(requester_pid)
        return requester_eid, True

    def leave_party(self, eid: int) -> None:
        """Remove eid do grupo — promove o próximo membro se eid era
        líder, desfaz o grupo inteiro se sobrar só 1 (ou 0)."""
        pid = self.get_party_id_of(eid)
        if pid == -1:
            return
        party = self._parties[pid]
        party["members"].remove(eid)
        del self._player_party_id[eid]

        if len(party["members"]) <= 1:
            # Grupo de 1 não faz sentido — desfaz e libera quem sobrou
            for m_eid in party["members"]:
                del self._player_party_id[m_eid]
                self._party_state_events_this_tick.append(("left", m_eid))
            del self._parties[pid]
        else:
            if party["leader_eid"] == eid:
                party["leader_eid"] = party["members"][0]   # promove o mais antigo
            self._party_state_events_this_tick.append(pid)

        # Avisa quem SAIU (não está em nenhum snapshot de grupo mais)
        self._party_state_events_this_tick.append(("left", eid))

    def kick_from_party(self, leader_eid: int, target_eid: int) -> bool:
        """True = expulsou. False = leader_eid não é líder do grupo de
        target_eid (ou target não está em grupo nenhum)."""
        pid = self.get_party_id_of(target_eid)
        if pid == -1:
            return False
        party = self._parties[pid]
        if party["leader_eid"] != leader_eid or target_eid == leader_eid:
            return False
        self.leave_party(target_eid)
        return True

    def end_parties_of(self, eid: int) -> None:
        """Encerra a participação de `eid` em grupo (logout) + limpa
        convites pendentes envolvendo ele, como target ou requester."""
        self.leave_party(eid)
        self._pending_party_invites.pop(eid, None)
        for tgt, req in list(self._pending_party_invites.items()):
            if req == eid:
                self._pending_party_invites.pop(tgt, None)

    def consume_party_state_events(self) -> list:
        """Retorna e limpa os eventos de grupo do tick atual — cada item
        é um `party_id` (int, grupo mudou — broadcast PARTY_STATE pra
        todos os membros atuais) ou uma tupla `("left", eid)` (eid saiu/
        foi expulso e não está mais em nenhum grupo — manda um
        PARTY_STATE vazio só pra ele)."""
        result = list(self._party_state_events_this_tick)
        self._party_state_events_this_tick.clear()
        return result
