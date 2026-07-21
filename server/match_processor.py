"""
server/match_processor.py
Mixin para WorldServer: Arena 2x2 (Fase G, leva 1) — fila FIFO de grupos
pré-formados, criação/fim de partida numa instância privada.

Espelha o padrão de DuelProcessorMixin (estado como bookkeeping simples
no WorldServer, API pública chamada pelos handlers de server/session.py,
eventos por tick consumidos pelo broadcast loop) e reusa
PartyProcessorMixin pro conceito de "grupo pré-formado" — um "time" nesta
leva É o grupo (Party) que entrou na fila junto, sem estado de time
próprio (ver ARQUITETURA_ONLINE.md, decisão da leva).

"Instância" é uma cópia privada de maps/arena_2v2.csv carregada via
WorldServer._load_instance/_unload_instance (server/world_server.py) —
UM WorldServer só, chave sintética por partida, nunca outro processo
(ver docstring de _load_map_for pro racional — evita colisão dos globais
module-level de engine/world_systems.py etc.).

Hostilidade entre times é resolvida por FACÇÃO (não pelo contexto PvP
plugável como duelo/zona) — cada player ganha um componente Faction
("arena_time_a"/"arena_time_b", ver content/faction_data.py) enquanto a
partida dura, sobrescrevendo a facção default "jogadores". can_engage()
já libera dano nesse caso sem nenhuma mudança (relação != "amigavel").

Eliminação (não morte de verdade): reusa o MESMO hook de golpe letal do
duelo (engine.core_systems.register_lethal_interceptor, um registro só —
ver WorldServer._lethal_interceptor_composite) — golpe que mataria deixa
o alvo em 1 HP + CombatState.is_immune=True (não pode mais ser
ferido/agir de forma útil) em vez de morrer/virar fantasma. Time com
todos os membros eliminados perde; o outro vence.
"""
from __future__ import annotations

ARENA_TEMPLATE_2V2 = "maps/arena_2v2.csv"
# Spawns opostos dentro do mapa 20x20 (interior walkable = tiles 1..18).
_SPAWN_TEAM_A = [(3, 9), (3, 10)]
_SPAWN_TEAM_B = [(16, 9), (16, 10)]


class MatchProcessorMixin:

    # ── Fila (grupos de exatamente 2) ────────────────────────────────────────

    def request_arena_queue_join(self, requester_eid: int) -> "str | None":
        """Só o líder do grupo pode enfileirar (mesma regra de
        kick_from_party). None = entrou na fila. Reason str = recusado
        (handler manda ARENA_QUEUE_STATE{in_queue:False, reason} só pro
        requester)."""
        party_id = self.get_party_id_of(requester_eid)
        party = self._parties.get(party_id)
        if party is None:
            return "no_party"
        if party["leader_eid"] != requester_eid:
            return "not_leader"
        if len(party["members"]) != 2:
            return "wrong_size"
        if party_id in self._arena_queue_2v2:
            return "already_queued"
        for m_eid in party["members"]:
            if m_eid in self._player_match_id:
                return "in_match"
        self._arena_queue_2v2.append(party_id)
        return None

    def request_arena_queue_leave(self, requester_eid: int) -> bool:
        """True = o grupo do requester estava na fila e saiu."""
        party_id = self.get_party_id_of(requester_eid)
        if party_id in self._arena_queue_2v2:
            self._arena_queue_2v2.remove(party_id)
            return True
        return False

    def _tick_arena_queue(self) -> None:
        """Pareamento FIFO puro — sem balanceamento/rank nesta leva. Roda
        1x por tick (mesmo padrão de _tick_duel_distance_check)."""
        while len(self._arena_queue_2v2) >= 2:
            pid_a = self._arena_queue_2v2.pop(0)
            pid_b = self._arena_queue_2v2.pop(0)
            party_a = self._parties.get(pid_a)
            party_b = self._parties.get(pid_b)
            if party_a is None or party_b is None:
                continue  # grupo se desfez enquanto esperava — descarta, segue tentando o resto
            self._create_match(list(party_a["members"]), list(party_b["members"]))

    # ── Ciclo de vida de partida ─────────────────────────────────────────────

    def _create_match(self, team_a_eids: list[int], team_b_eids: list[int]) -> None:
        from engine.components import Faction as _FactionM

        match_id     = f"arena2v2_{self._next_match_id}"
        self._next_match_id += 1
        instance_key = f"{ARENA_TEMPLATE_2V2}::{match_id}"
        self._load_instance(ARENA_TEMPLATE_2V2, instance_key)

        return_pos: dict[int, tuple] = {}
        for eid in team_a_eids + team_b_eids:
            sid = self.get_session_id_for_player(eid)
            if sid is None:
                continue
            tx, ty = self.get_tile_pos(sid)
            return_pos[eid] = (self.get_player_map(sid), tx, ty)

        self._active_matches[match_id] = {
            "instance_key": instance_key,
            "team_a":       list(team_a_eids),
            "team_b":       list(team_b_eids),
            "eliminated":   set(),
            "return_pos":   return_pos,
        }
        for eid in team_a_eids + team_b_eids:
            self._player_match_id[eid] = match_id

        spawn_pos: dict[int, tuple[int, int]] = {}
        for i, eid in enumerate(team_a_eids):
            sid = self.get_session_id_for_player(eid)
            if sid is None:
                continue
            sx, sy = _SPAWN_TEAM_A[i % len(_SPAWN_TEAM_A)]
            self.transfer_player(sid, eid, instance_key, sx, sy)
            self.world.add_component(eid, _FactionM(faction_id="arena_time_a"))
            spawn_pos[eid] = (sx, sy)
        for i, eid in enumerate(team_b_eids):
            sid = self.get_session_id_for_player(eid)
            if sid is None:
                continue
            sx, sy = _SPAWN_TEAM_B[i % len(_SPAWN_TEAM_B)]
            self.transfer_player(sid, eid, instance_key, sx, sy)
            self.world.add_component(eid, _FactionM(faction_id="arena_time_b"))
            spawn_pos[eid] = (sx, sy)

        _map_file = self._template_file_of(instance_key)
        for eid in team_a_eids + team_b_eids:
            sx, sy = spawn_pos.get(eid, (0, 0))
            self._arena_match_start_events_this_tick.append({
                "eid":       eid,
                "map_file":  _map_file,
                "target_x":  sx, "target_y": sy,
                "teammates": [e for e in (team_a_eids if eid in team_a_eids else team_b_eids) if e != eid],
                "opponents": team_b_eids if eid in team_a_eids else team_a_eids,
            })

    def _eliminate_player(self, match_id: str, eid: int) -> None:
        """Marca `eid` eliminado (não pode mais ser ferido/agir de forma
        útil — CombatState.is_immune). Encerra a partida se o time inteiro
        dele já estiver eliminado."""
        match = self._active_matches.get(match_id)
        if match is None or eid in match["eliminated"]:
            return
        match["eliminated"].add(eid)
        from engine.components import CombatState as _CSElim
        cst = self.world.get_component(eid, _CSElim)
        if cst:
            cst.is_immune = True

        team_key  = "team_a" if eid in match["team_a"] else "team_b"
        other_key = "team_b" if team_key == "team_a" else "team_a"
        if all(m in match["eliminated"] for m in match[team_key]):
            self._end_match(match_id, other_key)

    def _end_match(self, match_id: str, winner_team_key: "str | None") -> None:
        """`winner_team_key` = "team_a"/"team_b" (elimination normal) ou
        None (ninguém vence — ex: os dois times ficaram vazios)."""
        match = self._active_matches.pop(match_id, None)
        if match is None:
            return
        from engine.components import Faction as _FactionE, CombatState as _CSE
        winner_members = set(match[winner_team_key]) if winner_team_key else set()
        for eid in match["team_a"] + match["team_b"]:
            self._player_match_id.pop(eid, None)
            try:
                self.world.remove_component(eid, _FactionE)
            except Exception:
                pass
            cst = self.world.get_component(eid, _CSE)
            if cst:
                cst.is_immune = False
            r_map, r_x, r_y = match["return_pos"].get(eid, (self._map_file, 115, 389))
            sid = self.get_session_id_for_player(eid)
            if sid is not None:
                self.transfer_player(sid, eid, r_map, r_x, r_y)
            self._arena_match_end_events_this_tick.append({
                "eid":      eid,
                "won":      eid in winner_members,
                "map_file": r_map, "target_x": r_x, "target_y": r_y,
            })
        self._unload_instance(match["instance_key"])

    def _arena_leave_now(self, match_id: str, eid: int) -> None:
        """`eid` sai da partida IMEDIATAMENTE (desconexão ou /forfeit) —
        diferente de `_eliminate_player` (golpe letal): ali o player fica
        PARADO na arena, imune, só espectando até o time inteiro perder;
        aqui ele é removido do roster do time (não só marcado eliminado) e
        teleportado de volta na hora, porque ele está saindo de verdade
        (desconectando OU desistindo) — não faz sentido ele continuar
        contando como presente na instância.

        Precisa rodar ANTES do save do disconnect
        (server/session.py::on_disconnect) — sem isso, `get_player_save_
        data` capturava o `map_id` sintético da instância da arena (só
        existe em memória, nunca em disco) + o tile relativo ao spawn da
        arena, persistindo os dois no banco. No próximo login,
        `spawn_player` não reconhece mais aquele map_id (instância já
        descarregada) e cai no mapa principal, mas MANTÉM o tile da
        arena — o jogador aparecia num tile essencialmente aleatório do
        mapa principal, e como só existem 4 tiles de spawn de arena,
        vários jogadores que passaram pela mesma partida colidiam no
        mesmo lugar (bug real relatado pelo usuário 20/07/2026: "reloguei
        em algum lugar que não era a arena, com outros players em
        volta").

        Remover do roster (não só do set `eliminated`) também evita que
        `_end_match`, quando a partida terminar de verdade depois, tente
        restaurar/notificar este `eid` de novo — o que poderia
        teleportá-lo pra fora de onde quer que ele esteja àquela altura
        (já numa fila nova, ou dentro de outra partida)."""
        match = self._active_matches.get(match_id)
        if match is None:
            return
        from engine.components import Faction as _FactionL, CombatState as _CSL
        r_map, r_x, r_y = match["return_pos"].get(eid, (self._map_file, 115, 389))
        sid = self.get_session_id_for_player(eid)
        if sid is not None:
            self.transfer_player(sid, eid, r_map, r_x, r_y)
        try:
            self.world.remove_component(eid, _FactionL)
        except Exception:
            pass
        cst = self.world.get_component(eid, _CSL)
        if cst:
            cst.is_immune = False
        self._player_match_id.pop(eid, None)
        self._arena_match_end_events_this_tick.append({
            "eid": eid, "won": False,
            "map_file": r_map, "target_x": r_x, "target_y": r_y,
        })

        team_key  = "team_a" if eid in match["team_a"] else "team_b"
        other_key = "team_b" if team_key == "team_a" else "team_a"
        match[team_key]     = [m for m in match[team_key] if m != eid]
        match["eliminated"].discard(eid)
        if not match[team_key]:
            winner = other_key if match[other_key] else None
            self._end_match(match_id, winner)

    def end_matches_of(self, eid: int) -> None:
        """Desconexão em partida ativa = sair na hora (chamado ANTES de
        despawn_player E antes do save — mesmo ponto de end_duels_of/
        end_parties_of, ver server/session.py::on_disconnect)."""
        match_id = self._player_match_id.get(eid)
        if match_id is None:
            return
        self._arena_leave_now(match_id, eid)

    def request_arena_forfeit(self, eid: int) -> "str | None":
        """Comando de chat /forfeit ou /ff — desiste da partida atual e
        sai na hora, sem esperar o time inteiro ser eliminado. Retorna
        None em sucesso, motivo em string se recusado (não está numa
        partida)."""
        match_id = self._player_match_id.get(eid)
        if match_id is None:
            return "not_in_match"
        self._arena_leave_now(match_id, eid)
        return None

    # ── Interceptor de golpe letal ────────────────────────────────────────────

    def _arena_lethal_interceptor(self, world, killer_eid: int, target_id: int) -> bool:
        """Registrado via WorldServer._lethal_interceptor_composite (um
        registro só, composto com o de duelo — ver register_lethal_
        interceptor). True = intercepta a morte (apply_damage_core deixa
        o alvo em 1 HP em vez de matar)."""
        match_id = self._player_match_id.get(target_id)
        if match_id is None:
            return False
        self._eliminate_player(match_id, target_id)
        return True

    # ── Eventos por tick (broadcast loop do SessionManager) ─────────────────

    def consume_arena_match_start_events(self) -> list[dict]:
        result = list(self._arena_match_start_events_this_tick)
        self._arena_match_start_events_this_tick.clear()
        return result

    def consume_arena_match_end_events(self) -> list[dict]:
        result = list(self._arena_match_end_events_this_tick)
        self._arena_match_end_events_this_tick.clear()
        return result
