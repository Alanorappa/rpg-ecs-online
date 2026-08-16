"""
server/bg_queue_processor.py
Mixin para WorldServer: fila REAL de matchmaking pro battleground estilo
MOBA (04/08/2026, pedido do usuário — "vamos criar o sistema de fila,
equivalente ao que já existe na arena"). O player entra sozinho ou com
um grupo já formado (PartyProcessorMixin, até o tamanho do modo) numa
das 3 filas de TAMANHO FIXO (BG_MODES abaixo — 2v2/3v3/5v5, mesmo
padrão de ARENA_MODES).

Revisado 10/08/2026 (achado real do usuário): a versão original era 1
fila ÚNICA sem escolha de modo, onde `_tick_bg_queue` tentava formar a
MAIOR partida simétrica possível a cada tick — mas sem nenhuma janela de
espera, 2 solos na fila já fechavam 1v1 no tick seguinte (33ms depois),
nunca dando chance de uma partida maior se formar. Migrar pra filas
fixas por tamanho (jogador escolhe, como a Arena já faz) resolve o
problema na raiz — não tem mais "qual tamanho formar", então não precisa
de timer de espera artificial (a "waiting room" que o WoW usa no
Escaramuça serve pra balancear papel/MMR dentro do bracket já fixo,
problema que não existe aqui ainda, sem sistema de papel/rating).

Espelha a FORMA de `server/match_processor.py` (fila → propõe → aceite →
countdown → portão → luta → decidida → sai), reaproveitando:
- PartyProcessorMixin pro conceito de "grupo pré-formado" = 1 time.
- `WorldServer._load_instance`/`_unload_instance` (instância privada por
  PARTIDA, `f"{DEBUG_BG_TEMPLATE}::{match_id}"` — diferente da instância
  ÚNICA compartilhada de `server/debug_battleground.py`, que continua
  existindo intocada pra debug solo via "/testbg").
- `server/instance_progression.py::enter_/exit_normalized_progression`
  (finalmente ativado por um processador de jogo de verdade — até aqui
  só "/testbg" chamava).
- Geometria do mapa (spawn/portão) IMPORTADA de `debug_battleground.py`
  (`DEBUG_BG_TEMPLATE`/`DEBUG_BG_SPAWN`/`DEBUG_BG_GATE_TILES`) — é o
  MESMO mapa físico pros dois sistemas, não um conceito "só de debug";
  reaproveitar evita 2 listas de tile podendo divergir.
- Fim de partida por NEXUS destruído (não eliminação de time — MOBA,
  não Arena) + respawn automático + HUD de KDA ao vivo, mesmo padrão já
  validado em debug_battleground.py, mas por PARTIDA (não 1 estado
  global compartilhado — múltiplas partidas da fila podem coexistir).

Algoritmo de pareamento por fila (`_bg_try_pack`) é bin-packing guloso —
grupo nunca é dividido entre os 2 lados; o resto de um time pode ser
preenchido por outro grupo do mesmo tamanho OU por vários tokens menores
somando o tamanho certo — nunca comita um pareamento que não feche
EXATAMENTE dos 2 lados, então nunca produz partida assimétrica, mesmo
que o empacotamento guloso não seja o teoricamente ótimo. Mesma lógica
de antes da revisão, só que agora escopada a UM tamanho fixo por fila em
vez de tentar os 5 tamanhos numa fila só.
"""
from __future__ import annotations

from shared.constants import ARENA_ACCEPT_WINDOW_S, ARENA_COUNTDOWN_S, PARTY_MAX_SIZE
from server.debug_battleground import (
    DEBUG_BG_TEMPLATE as BG_QUEUE_TEMPLATE,
    DEBUG_BG_SPAWN as BG_QUEUE_SPAWN,
    DEBUG_BG_GATE_TILES as BG_QUEUE_GATE_TILES,
    DEBUG_BG_RESULT_AUTO_LEAVE_S as BG_QUEUE_RESULT_AUTO_LEAVE_S,
)
from server.instanced_match_processor import InstancedMatchMixin

# Modos de fila da BG (10/08/2026, achado real do usuário — mesmo padrão
# de server/match_processor.py::ARENA_MODES) — 3 tamanhos fixos, não os
# 5 possíveis (PARTY_MAX_SIZE): menos filas fragmentando quem espera,
# mesma simetria que a Arena já usa (1v1/2v2/3v3). Adicionar um 4º
# tamanho no futuro é só 1 entrada nova aqui — `_tick_bg_queue`/
# `request_bg_queue_join`/etc. iteram BG_MODES, nunca hardcoded.
BG_MODES: dict[str, dict] = {
    "2v2": {"team_size": 2, "label": "Battleground 2x2"},
    "3v3": {"team_size": 3, "label": "Battleground 3x3"},
    "5v5": {"team_size": 5, "label": "Battleground 5x5"},
}

# Offsets de spawn (mesmo espírito de WorldServer._MINION_WAVE_OFFSETS)
# pra até 5 membros de um time não nascerem todos empilhados no MESMO
# tile — `request_bg_accept` valida cada um contra tile_validation e cai
# pro tile PURO de `BG_QUEUE_SPAWN` se o offset cair em parede (mesma
# regra dos minions).
_BG_QUEUE_SPAWN_OFFSETS = ((0, 0), (1, 0), (-1, 0), (0, 1), (-1, 1))

# Achatado 1x no import do módulo (não a cada tick) — critério de
# performance em caminho quente (CLAUDE.md, "Performance e concorrência").
_BG_QUEUE_GATE_TILES_FLAT = [t for tiles in BG_QUEUE_GATE_TILES.values() for t in tiles]


class BgQueueProcessorMixin(InstancedMatchMixin):

    # ── Fila (token = ("solo", eid) ou ("party", party_id), 1 por modo) ──────

    def request_bg_queue_join(self, requester_eid: int, mode_id: str = "5v5") -> "str | None":
        """None = entrou na fila do modo pedido. Reason str = recusado
        (handler manda BG_QUEUE_STATE {in_queue:False, reason} só pro
        requester). Solo entra livre; em grupo, só o líder pode
        enfileirar (mesma regra de `kick_from_party`/arena). Grupo MENOR
        que o time do modo é aceito (preenchido por outros tokens da
        MESMA fila, ver `_bg_try_pack`) — só maior que o time é recusado.
        Um requester só pode estar em UMA fila de modo por vez (checa
        TODAS antes de aceitar, mesmo padrão de `request_arena_queue_join`)."""
        mode = BG_MODES.get(mode_id)
        if mode is None:
            return "invalid_mode"
        if requester_eid in self._player_bg_match_id:
            return "in_match"
        party_id = self.get_party_id_of(requester_eid)
        team_size = mode["team_size"]

        if party_id == -1:
            token = ("solo", requester_eid)
            for q in self._bg_queues.values():
                if token in q:
                    return "already_queued"
            self._bg_queues[mode_id].append(token)
            return None

        party = self._parties.get(party_id)
        if party is None:
            return "no_party"
        if party["leader_eid"] != requester_eid:
            return "not_leader"
        if len(party["members"]) > team_size:
            return "wrong_size"
        for m_eid in party["members"]:
            if m_eid in self._player_bg_match_id:
                return "in_match"
        token = ("party", party_id)
        for q in self._bg_queues.values():
            if token in q:
                return "already_queued"
        self._bg_queues[mode_id].append(token)
        return None

    def request_bg_queue_leave(self, requester_eid: int) -> bool:
        """True = o token do requester (solo ou grupo) estava em alguma
        fila de modo e saiu."""
        party_id = self.get_party_id_of(requester_eid)
        for q in self._bg_queues.values():
            for token in list(q):
                kind, val = token
                if (kind == "solo" and val == requester_eid) or (kind == "party" and val == party_id):
                    q.remove(token)
                    return True
        return False

    def _bg_members_for_token(self, token: tuple) -> "list[int] | None":
        """Resolve um token da fila pro roster ATUAL de eids — recarrega
        do estado VIVO (não um snapshot congelado no join): se o grupo se
        desfez ou o player desconectou enquanto esperava, devolve `None`
        (chamador descarta o token)."""
        kind, val = token
        if kind == "solo":
            return [val] if val in self._player_eid_to_sid else None
        party = self._parties.get(val)
        return list(party["members"]) if party is not None else None

    def _bg_try_pack(self, mode_id: str) -> "tuple[list, list] | None":
        """Tenta empacotar 2 lados de tamanho EXATO `team_size` do modo a
        partir da fila DESSE modo, respeitando a ordem FIFO (quem entrou
        primeiro tem prioridade de entrar num time). Bin-packing guloso:
        preenche o lado A primeiro, depois o B; um token grande demais
        pro tamanho do modo é ignorado (não deveria acontecer — `join` já
        valida no ato de entrar, mas o grupo pode ter crescido depois).
        Só devolve algo quando os DOIS lados fecham EXATAMENTE — nunca
        uma partida assimétrica, mesmo que o empacotamento guloso não
        seja o teoricamente ótimo (pode deixar tokens combináveis de fora
        numa tentativa e formar mesmo assim no próximo tick, quando mais
        gente tiver entrado)."""
        team_size = BG_MODES[mode_id]["team_size"]
        side_a: list = []
        side_b: list = []
        sum_a = sum_b = 0
        for token in self._bg_queues[mode_id]:
            members = self._bg_members_for_token(token)
            if members is None:
                continue
            size = len(members)
            if size > team_size:
                continue
            if sum_a + size <= team_size:
                side_a.append(token)
                sum_a += size
            elif sum_b + size <= team_size:
                side_b.append(token)
                sum_b += size
            if sum_a == team_size and sum_b == team_size:
                return side_a, side_b
        return None

    def _tick_bg_queue(self) -> None:
        """Roda 1x por tick (mesmo padrão de `_tick_arena_queue`). Pra
        CADA modo (2v2/3v3/5v5, filas independentes desde 10/08/2026 —
        achado real do usuário, ver docstring do módulo): limpa tokens
        que desapareceram, depois forma o máximo de partidas possível
        dessa fila (loop — 10 solos esperando no 5v5 podem virar 2
        partidas na mesma passada, não só 1)."""
        for mode_id, queue in self._bg_queues.items():
            for token in list(queue):
                if self._bg_members_for_token(token) is None:
                    queue.remove(token)

        for mode_id, queue in self._bg_queues.items():
            while True:
                packed = self._bg_try_pack(mode_id)
                if packed is None:
                    break
                side_a_tokens, side_b_tokens = packed
                team_a_eids = [eid for t in side_a_tokens for eid in self._bg_members_for_token(t)]
                team_b_eids = [eid for t in side_b_tokens for eid in self._bg_members_for_token(t)]
                for t in side_a_tokens + side_b_tokens:
                    queue.remove(t)
                self._propose_bg_match(team_a_eids, team_b_eids, mode_id)

    def _tick_bg_results_timeout(self) -> None:
        """Partidas DECIDIDAS há mais de BG_QUEUE_RESULT_AUTO_LEAVE_S
        segundos forçam a saída de quem ainda não clicou "Voltar" —
        mesmo padrão de `_tick_arena_results_timeout`."""
        self._im_results_timeout(
            self._bg_active_matches, BG_QUEUE_RESULT_AUTO_LEAVE_S,
            lambda match_id, eid: self.request_bg_leave(eid))

    # ── Ciclo de vida de partida ─────────────────────────────────────────────

    def _propose_bg_match(self, team_a_eids: list[int], team_b_eids: list[int],
                          mode_id: str) -> None:
        """Fila (do modo `mode_id`) empacotou 2 lados do MESMO tamanho —
        ninguém é teleportado ainda (mesmo modelo de `_propose_match`):
        os convidados recebem BG_MATCH_FOUND e têm ARENA_ACCEPT_WINDOW_S
        pra mandar BG_MATCH_ACCEPT. `mode_id` (10/08/2026 — antes o
        tamanho vinha só de `len(team_a_eids)`, sem saber de QUAL fila;
        agora fixo por escolha do player, ver BG_MODES) usado pro cliente
        saber o rótulo/estado do botão certo no modal."""
        match_id = f"bg_{self._next_bg_match_id}"
        self._next_bg_match_id += 1
        import time as _time_pbg
        _propose_now = _time_pbg.time()

        self._bg_active_matches[match_id] = {
            "mode_id":            mode_id,
            "team_size":          len(team_a_eids),
            "instance_key":       None,
            "team_a":             [],
            "team_b":             [],
            "invited_a":          list(team_a_eids),
            "invited_b":          list(team_b_eids),
            "return_pos":         {},
            "decided":            False,
            "decided_at":         0.0,
            "winner_faction":     None,
            "accept_deadline":    _propose_now + ARENA_ACCEPT_WINDOW_S,
            "countdown_deadline": _propose_now + ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S,
            "fight_started":      False,
            "accept_swept":       False,
            "gate_open":          False,
            "respawn_timers":     {},
            "stat_snapshots":     {},
            "last_kda_sent":      {},
            "gold_earned":        {},
            "last_wallet_gold":   {},
        }
        for eid in team_a_eids + team_b_eids:
            self._pending_bg_invite[eid] = match_id
        for eid in team_a_eids + team_b_eids:
            self._bg_match_found_events_this_tick.append({
                "eid":       eid,
                "mode":      mode_id,
                "team_size": len(team_a_eids),
                "teammates": [e for e in (team_a_eids if eid in team_a_eids else team_b_eids) if e != eid],
                "opponents": team_b_eids if eid in team_a_eids else team_a_eids,
            })

    def request_bg_accept(self, eid: int) -> "str | None":
        """Aceita a partida encontrada — entra IMEDIATAMENTE, sozinho,
        sem esperar o resto do time (mesmo estilo de
        `request_arena_accept`). Primeiro aceite de QUALQUER um carrega a
        instância (lazy, chave POR PARTIDA — `f"{BG_QUEUE_TEMPLATE}::
        {match_id}"`, diferente da instância única de debug_battleground.py).
        Ativa a progressão normalizada (`enter_normalized_progression`) —
        primeira vez que um processador de jogo de verdade chama isso."""
        import time as _time_ba
        match_id = self._pending_bg_invite.get(eid)
        if match_id is None:
            return "no_pending_invite"
        match = self._bg_active_matches.get(match_id)
        if match is None or match["decided"]:
            self._pending_bg_invite.pop(eid, None)
            return "expired"
        if _time_ba.time() >= match["accept_deadline"]:
            self._pending_bg_invite.pop(eid, None)
            return "expired"
        del self._pending_bg_invite[eid]

        from engine.components import Faction as _FactionBg
        side       = "a" if eid in match["invited_a"] else "b"
        other_side = "b" if side == "a" else "a"

        if match["instance_key"] is None:
            match["instance_key"] = f"{BG_QUEUE_TEMPLATE}::{match_id}"
            self._load_instance(BG_QUEUE_TEMPLATE, match["instance_key"])

        sid = self.get_session_id_for_player(eid)
        if sid is None:
            return "disconnected"
        tx, ty = self.get_tile_pos(sid)
        match["return_pos"][eid] = (self.get_player_map(sid), tx, ty)

        faction_id = f"arena_time_{side}"
        base_x, base_y = BG_QUEUE_SPAWN[side]
        idx = len(match[f"team_{side}"])
        dx, dy = _BG_QUEUE_SPAWN_OFFSETS[idx % len(_BG_QUEUE_SPAWN_OFFSETS)]
        spawn_x, spawn_y = base_x + dx, base_y + dy
        _bundle_bg = self._map_bundles.get(match["instance_key"])
        if (_bundle_bg is not None and _bundle_bg.tile_validation is not None
                and not _bundle_bg.tile_validation.is_tile_walkable(-1, spawn_x, spawn_y)):
            spawn_x, spawn_y = base_x, base_y

        self.transfer_player(sid, eid, match["instance_key"], spawn_x, spawn_y)
        self.world.add_component(eid, _FactionBg(faction_id=faction_id))
        self._reset_combat_resources(eid)

        from server.instance_progression import enter_normalized_progression as _enter_np_bg
        _enter_np_bg(self, eid)

        from engine.components import CharStatsTracker as _CST_bg
        _cst_bg = self.world.get_component(eid, _CST_bg)
        match["stat_snapshots"][eid] = {
            "kills":  _cst_bg.players_killed if _cst_bg else 0,
            "deaths": _cst_bg.deaths if _cst_bg else 0,
            "farm":   _cst_bg.minions_killed if _cst_bg else 0,
            "damage": (_cst_bg.pve_damage + _cst_bg.pvp_damage) if _cst_bg else 0,
        }
        from server.instance_progression import INSTANCE_STARTING_GOLD as _ISG_bg
        match["gold_earned"][eid]      = 0
        match["last_wallet_gold"][eid] = _ISG_bg

        match[f"team_{side}"].append(eid)
        self._player_bg_match_id[eid] = match_id

        remaining = max(0.0, match["countdown_deadline"] - _time_ba.time())
        self._bg_match_start_events_this_tick.append({
            "eid":                 eid,
            "mode":                match.get("mode_id", "5v5"),
            "map_file":            BG_QUEUE_TEMPLATE,
            "target_x":            spawn_x, "target_y": spawn_y,
            "team_size":           match["team_size"],
            "teammates":           [e for e in match[f"invited_{side}"] if e != eid],
            "opponents":           match[f"invited_{other_side}"],
            "countdown_remaining": remaining,
            "my_faction":          faction_id,
        })
        # Entrada tardia (portão desta instância já aberto) — mesma regra
        # de request_arena_accept: o evento de abertura só sai 1x, no
        # tick em que fight_started vira True.
        if match["fight_started"]:
            self._bg_gate_open_events_this_tick.append({"eid": eid})
        return None

    def _tick_bg_pending(self) -> None:
        """Roda 1x por tick. Mesmas 2 varreduras de `_tick_arena_pending`:
        (a) janela de aceite vencida — quem não aceitou é descartado; só
        descarta a partida inteira se NINGUÉM apareceu (instância nunca
        chegou a carregar); um lado inteiro ausente e o outro presente
        também é só descartado (sem "vencedor" — partida de fila nunca
        chegou a começar de verdade, diferente da Arena que decide W.O.
        aqui: BG não elimina por ausência, só por Nexus, então não faz
        sentido premiar vitória por not-show). (b) contagem de preparo
        vencida — abre o portão físico (BG_QUEUE_GATE_TILES) da
        instância e ativa as lanes de minion, mesmo padrão de
        debug_battleground.py::_tick_gate, só que por PARTIDA."""
        import time as _time_tbp
        now = _time_tbp.time()

        for match_id, match in list(self._bg_active_matches.items()):
            if match["decided"] or match["fight_started"]:
                continue

            if self._im_sweep_accept_deadline(match, self._pending_bg_invite, now):
                if not match["team_a"] and not match["team_b"]:
                    self._bg_active_matches.pop(match_id, None)
                    continue
                elif not match["team_a"] or not match["team_b"]:
                    # só 1 lado apareceu — sem instância carregada de fato
                    # pro outro, nada pra jogar; devolve os que entraram.
                    for eid in list(match["team_a"] + match["team_b"]):
                        self.request_bg_leave(eid)
                    continue
                # os dois lados têm gente (só desbalanceado) — segue pro combate normalmente

            self._im_open_gate_if_ready(
                match, now, _BG_QUEUE_GATE_TILES_FLAT,
                lambda eid: self._bg_gate_open_events_this_tick.append({"eid": eid}))

    def request_bg_leave(self, eid: int) -> "str | None":
        """`eid` sai da partida DE VERDADE — no meio da luta (desistência,
        botão/comando de sair) ou da tela de resultado (botão "Voltar"/
        timeout). Restaura progressão real (`exit_normalized_progression`),
        Faction, posição de origem — mesmo padrão de
        `debug_battleground.py::_leave`/`_arena_leave_now`. Retorna None
        em sucesso, "not_in_match" se `eid` não está em nenhuma partida da
        fila (defesa contra cliente dessincronizado)."""
        match_id = self._player_bg_match_id.get(eid)
        if match_id is None:
            return "not_in_match"
        match = self._bg_active_matches.get(match_id)
        if match is None:
            self._player_bg_match_id.pop(eid, None)
            return "not_in_match"

        from server.instance_progression import exit_normalized_progression as _exit_np_bg
        _exit_np_bg(self, eid)
        try:
            from engine.components import Faction as _FactionBgL
            self.world.remove_component(eid, _FactionBgL)
        except Exception:
            pass

        from engine.components import GhostState as _GSBg
        gst = self.world.get_component(eid, _GSBg)
        if gst and gst.is_dead:
            self._revive_player(eid, hp_frac=1.0, at_corpse=False)
        self._reset_combat_resources(eid)

        r_map, r_x, r_y = match["return_pos"].get(eid, (self._map_file, 115, 389))
        sid = self.get_session_id_for_player(eid)
        if sid is not None:
            self.transfer_player(sid, eid, r_map, r_x, r_y)

        match["respawn_timers"].pop(eid, None)
        match["stat_snapshots"].pop(eid, None)
        match["last_kda_sent"].pop(eid, None)
        match["gold_earned"].pop(eid, None)
        match["last_wallet_gold"].pop(eid, None)
        self._player_bg_match_id.pop(eid, None)
        self._bg_match_leave_events_this_tick.append({
            "eid": eid, "map_file": r_map, "target_x": r_x, "target_y": r_y,
        })

        team_key = "team_a" if eid in match["team_a"] else "team_b"
        match[team_key] = [m for m in match[team_key] if m != eid]

        if not match["team_a"] and not match["team_b"]:
            self._bg_active_matches.pop(match_id, None)
            self._unload_instance(match["instance_key"])
        return None

    def end_bg_matches_of(self, eid: int) -> None:
        """Desconexão em partida ativa OU na tela de resultado = sair na
        hora (chamado ANTES de despawn_player E antes do save — mesmo
        ponto de end_matches_of/end_duels_of/end_parties_of,
        server/session.py::on_disconnect)."""
        if eid in self._player_bg_match_id:
            self.request_bg_leave(eid)

    # ── Nexus destruído (fim de partida) ──────────────────────────────────

    def notify_bg_queue_nexus_destroyed(self, tower_map_file: str,
                                        destroyed_faction: str, killer_eid: int) -> None:
        """Nexus derrubado numa instância de partida DA FILA (não a
        instância de debug — ver server/debug_battleground.py::
        notify_nexus_destroyed, que checa a PRÓPRIA instância_key). Acha
        a partida ativa cujo `instance_key` bate com `tower_map_file` —
        necessário porque múltiplas partidas da fila (+ a instância de
        debug) podem coexistir, todas usando a MESMA facção "arena_time_a/
        b" — sem checar o mapa/instância exata, uma torre caindo numa
        partida notificaria (ou ficaria ambígua com) as outras. No-op se
        nenhuma partida ativa usa essa instância, ou se já decidida
        (idempotente)."""
        if destroyed_faction not in ("arena_time_a", "arena_time_b"):
            return
        match = None
        match_id = None
        for _mid, _m in self._bg_active_matches.items():
            if _m["instance_key"] == tower_map_file and not _m["decided"] and _m["fight_started"]:
                match, match_id = _m, _mid
                break
        if match is None:
            return
        winner_faction = ("arena_time_b" if destroyed_faction == "arena_time_a"
                          else "arena_time_a")

        from engine.components import CharStatsTracker as _CSTn, CharacterStats as _CharN, Faction as _FactionN

        players = []
        for m_eid in match["team_a"] + match["team_b"]:
            fac = self.world.get_component(m_eid, _FactionN)
            team_faction = fac.faction_id if fac else None
            cst = self.world.get_component(m_eid, _CSTn)
            snap = match["stat_snapshots"].get(
                m_eid, {"kills": 0, "deaths": 0, "farm": 0, "damage": 0})
            char = self.world.get_component(m_eid, _CharN)
            players.append({
                "eid":    m_eid,
                "name":   char.name if char else "",
                "team":   "a" if team_faction == "arena_time_a" else "b",
                "kills":  (cst.players_killed if cst else 0) - snap["kills"],
                "deaths": (cst.deaths if cst else 0) - snap["deaths"],
                "farm":   (cst.minions_killed if cst else 0) - snap["farm"],
                "gold":   self._bg_sample_gold_earned(match, m_eid),
                "damage": ((cst.pve_damage + cst.pvp_damage) if cst else 0) - snap["damage"],
                "won":    team_faction == winner_faction,
            })

        import time as _time_nex
        match["decided"]        = True
        match["decided_at"]     = _time_nex.time()
        match["winner_faction"] = winner_faction
        result_payload = {"winner_faction": winner_faction, "players": players,
                          "mode": match.get("mode_id", "5v5")}
        for m_eid in match["team_a"] + match["team_b"]:
            self._bg_match_result_events_this_tick.append((m_eid, result_payload))

    def _bg_sample_gold_earned(self, match: dict, eid: int) -> int:
        """Mesmo mecanismo de `debug_battleground.py::_sample_gold_earned`
        (só soma deltas POSITIVOS de wallet.gold — compra na loja de
        instância nunca deixa o total ficar negativo), só que o estado
        vive no `match` da partida em vez de num dict global módulo."""
        if eid not in match["gold_earned"]:
            return 0
        from engine.components import Wallet as _WalletBgGE
        wallet = self.world.get_component(eid, _WalletBgGE)
        if wallet is None:
            return match["gold_earned"][eid]
        last  = match["last_wallet_gold"].get(eid, wallet.gold)
        delta = wallet.gold - last
        if delta > 0:
            match["gold_earned"][eid] += delta
        match["last_wallet_gold"][eid] = wallet.gold
        return match["gold_earned"][eid]

    # ── Respawn automático + HUD de KDA (por partida) ─────────────────────

    def _tick_bg_respawns(self) -> None:
        """Respawn automático estilo MOBA (sem "Liberar espírito"/prompt de
        reviver) — mesmo mecanismo de `debug_battleground.py::
        _process_respawns`, só que escopado por PARTIDA em vez do estado
        global único."""
        if not self._bg_active_matches:
            return
        from engine.components import GhostState as _GSResp, Faction as _FacResp
        from engine.utils import snap_to_tile
        from shared.constants import DEBUG_BG_RESPAWN_S, TICK_INTERVAL as _TIresp

        for match in self._bg_active_matches.values():
            if not match["fight_started"] or match["decided"]:
                continue
            for eid in list(match["team_a"] + match["team_b"]):
                gst = self.world.get_component(eid, _GSResp)
                if gst is None or not gst.is_dead:
                    match["respawn_timers"].pop(eid, None)
                    continue
                if eid not in match["respawn_timers"]:
                    match["respawn_timers"][eid] = DEBUG_BG_RESPAWN_S
                    continue
                match["respawn_timers"][eid] -= _TIresp
                if match["respawn_timers"][eid] > 0:
                    continue
                del match["respawn_timers"][eid]
                fac = self.world.get_component(eid, _FacResp)
                side = "a" if (fac is None or fac.faction_id != "arena_time_b") else "b"
                spawn_x, spawn_y = BG_QUEUE_SPAWN[side]
                snap_to_tile(self.world, eid, spawn_x, spawn_y, carry_prev=False)
                self._revive_player(eid, hp_frac=1.0, at_corpse=False)

    def _tick_bg_kda_hud(self) -> None:
        """HUD ao vivo de Kills/Deaths/Farm/Gold — mesmo dirty-check de
        `debug_battleground.py::_tick_kda_hud`, por partida."""
        if not self._bg_active_matches:
            return
        from engine.components import CharStatsTracker as _CSTk
        for match in self._bg_active_matches.values():
            for eid in list(match["team_a"] + match["team_b"]):
                snap = match["stat_snapshots"].get(eid)
                if snap is None:
                    continue
                cst = self.world.get_component(eid, _CSTk)
                kills  = (cst.players_killed if cst else 0) - snap["kills"]
                deaths = (cst.deaths if cst else 0) - snap["deaths"]
                farm   = (cst.minions_killed if cst else 0) - snap["farm"]
                gold   = self._bg_sample_gold_earned(match, eid)
                cur = (kills, deaths, farm, gold)
                if match["last_kda_sent"].get(eid) == cur:
                    continue
                match["last_kda_sent"][eid] = cur
                self.queue_stats_update({
                    "player_eid":   eid,
                    "match_kills":  kills, "match_deaths": deaths,
                    "match_farm":   farm,  "match_gold":   gold,
                })

    # ── Eventos por tick (broadcast loop do SessionManager) ─────────────────

    def consume_bg_match_found_events(self) -> list[dict]:
        result = list(self._bg_match_found_events_this_tick)
        self._bg_match_found_events_this_tick.clear()
        return result

    def consume_bg_match_start_events(self) -> list[dict]:
        result = list(self._bg_match_start_events_this_tick)
        self._bg_match_start_events_this_tick.clear()
        return result

    def consume_bg_gate_open_events(self) -> list[dict]:
        result = list(self._bg_gate_open_events_this_tick)
        self._bg_gate_open_events_this_tick.clear()
        return result

    def consume_bg_match_leave_events(self) -> list[dict]:
        result = list(self._bg_match_leave_events_this_tick)
        self._bg_match_leave_events_this_tick.clear()
        return result

    def consume_bg_match_result_events(self) -> list[tuple[int, dict]]:
        result = list(self._bg_match_result_events_this_tick)
        self._bg_match_result_events_this_tick.clear()
        return result
