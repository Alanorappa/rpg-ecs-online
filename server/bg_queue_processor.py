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

Bot 100% servidor (16/08/2026, pedido do usuário — testar a BG sem
precisar de 4 humanos) — SÓ modo 2v2 por enquanto, escopo pequeno de
propósito (3v3/5v5 continuam com `_bg_try_pack` inalterado). Regra:
cada TIME vem de NO MÁXIMO 1 token real (nunca 2 solos estranhos no
MESMO time — ver `_bg_try_pack_with_bots`); o resto do time é
preenchido por bot. Bot é uma entidade normal, criada por
`WorldServer.spawn_player` (mesmo caminho de qualquer login real, sem
fábrica paralela) + uma `Session` FALSA registrada em `SessionManager`
(`ws` nulo que só descarta `send()`) — sem a Session falsa, o bot fica
invisível pros outros players, porque a varredura que descobre OUTROS
PLAYERS (`server/session.py::_build_update_for_session`) só itera
sessões REAIS (`self._sessions.values()`), nunca `WorldServer.
_player_eids` diretamente. Decisão/ação do bot em si (mover, atacar,
usar skill) mora em `server/bot_ai_system.py::BotAISystem` — chama as
MESMAS funções internas (`move_player`/`set_player_target`/
`queue_skill`) que os handlers de rede já chamam por trás, sem
duplicar nenhuma lógica de combate.
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

# Bot backfill — só 2v2 (ver docstring do módulo). Tempo que um token
# SOZINHO na fila espera por outro humano antes do servidor desistir e
# fechar a partida só com bots (decisão do usuário, 16/08/2026).
BG_BOT_BACKFILL_WAIT_S = 20.0
# Nível fixo do bot v1 — mesmo nível de minion_lanes/torre no mapa da BG
# (moba_battleground_entities.json), consistente com o resto do conteúdo.
BG_BOT_LEVEL = 2


class _NullWebSocket:
    """WebSocket falso pra Session de bot — `send()` descarta em
    silêncio, sem rede nenhuma de verdade. `Session.send()` já captura
    qualquer exceção (server/session.py), mas aqui nem chega a lançar."""
    async def send(self, data) -> None:
        return None

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

    # ── Fila (token = ("solo", eid, join_ts) ou ("party", party_id, join_ts),
    #    1 por modo — join_ts usado só pelo backfill de bot do 2v2, ver
    #    `_bg_try_pack_with_bots`) ────────────────────────────────────────────

    def request_bg_queue_join(self, requester_eid: int, mode_id: str = "5v5") -> "str | None":
        """None = entrou na fila do modo pedido. Reason str = recusado
        (handler manda BG_QUEUE_STATE {in_queue:False, reason} só pro
        requester). Solo entra livre; em grupo, só o líder pode
        enfileirar (mesma regra de `kick_from_party`/arena). Grupo MENOR
        que o time do modo é aceito (preenchido por outros tokens da
        MESMA fila, ver `_bg_try_pack`/`_bg_try_pack_with_bots`) — só
        maior que o time é recusado. Um requester só pode estar em UMA
        fila de modo por vez (checa TODAS antes de aceitar, mesmo padrão
        de `request_arena_queue_join`)."""
        mode = BG_MODES.get(mode_id)
        if mode is None:
            return "invalid_mode"
        if requester_eid in self._player_bg_match_id:
            return "in_match"
        party_id = self.get_party_id_of(requester_eid)
        team_size = mode["team_size"]
        import time as _time_bqj

        if party_id == -1:
            for q in self._bg_queues.values():
                if any(t[0] == "solo" and t[1] == requester_eid for t in q):
                    return "already_queued"
            self._bg_queues[mode_id].append(("solo", requester_eid, _time_bqj.time()))
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
        for q in self._bg_queues.values():
            if any(t[0] == "party" and t[1] == party_id for t in q):
                return "already_queued"
        self._bg_queues[mode_id].append(("party", party_id, _time_bqj.time()))
        return None

    def request_bg_queue_leave(self, requester_eid: int) -> bool:
        """True = o token do requester (solo ou grupo) estava em alguma
        fila de modo e saiu."""
        party_id = self.get_party_id_of(requester_eid)
        for q in self._bg_queues.values():
            for token in list(q):
                kind, val = token[0], token[1]
                if (kind == "solo" and val == requester_eid) or (kind == "party" and val == party_id):
                    q.remove(token)
                    return True
        return False

    def _bg_members_for_token(self, token: tuple) -> "list[int] | None":
        """Resolve um token da fila pro roster ATUAL de eids — recarrega
        do estado VIVO (não um snapshot congelado no join): se o grupo se
        desfez ou o player desconectou enquanto esperava, devolve `None`
        (chamador descarta o token)."""
        kind, val = token[0], token[1]
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

    def _bg_try_pack_with_bots(self, mode_id: str, now: float) -> "tuple[list, list] | None":
        """Só modo 2v2 (ver docstring do módulo) — versão com backfill de
        bot. Diferente de `_bg_try_pack`: cada TIME vem de NO MÁXIMO 1
        token real (nunca 2 solos estranhos combinados no MESMO time —
        pedido explícito do usuário: solo nunca divide time com
        desconhecido, só com bot). O resto de cada time (se sobrar
        espaço) é preenchido por bot pelo CHAMADOR (`_tick_bg_queue`) —
        esta função só decide QUEM entra em cada time real:
        - ≥2 tokens reais disponíveis → fecha na hora, 1 por lado (FIFO —
          quem entrou primeiro joga primeiro), cada lado incompleto ganha
          bot depois.
        - Só 1 token disponível E já esperou ≥ BG_BOT_BACKFILL_WAIT_S
          sozinho → fecha sozinho (lado B fica 100% bot).
        - Só 1 token disponível E ainda não esperou o suficiente → não
          fecha ainda (continua esperando, mesmo comportamento de antes
          pra esse caso)."""
        team_size = BG_MODES[mode_id]["team_size"]
        usable = []
        for token in self._bg_queues[mode_id]:
            members = self._bg_members_for_token(token)
            if members is None or len(members) > team_size:
                continue
            usable.append(token)
        if not usable:
            return None
        if len(usable) >= 2:
            return [usable[0]], [usable[1]]
        join_ts = usable[0][2]
        if now - join_ts >= BG_BOT_BACKFILL_WAIT_S:
            return [usable[0]], []
        return None

    def _spawn_bot_player(self, class_id: str = "") -> int:
        """Cria um bot 100% servidor (sem rede) — reaproveita o MESMO
        `WorldServer.spawn_player` que qualquer login real usa (nenhuma
        fábrica paralela). `session_id` sintético, registrado nas MESMAS
        tabelas que um login real (`_player_eids`/`_player_eid_to_sid`,
        dentro de `spawn_player`) + numa `Session` FALSA em
        `SessionManager` (`ws` nulo — `_NullWebSocket`) — sem isso, o bot
        fica invisível pros outros players (a varredura que descobre
        OUTROS PLAYERS em `server/session.py::_build_update_for_session`
        só itera sessões REAIS, nunca `_player_eids` direto). Marcado com
        `BotControlled` pra `BotAISystem` saber quem controlar e pro
        cleanup de fim de partida saber quem despawnar (`request_bg_leave`).

        `class_id`: força a classe em vez de sortear (16/08/2026) — só
        usado por teste que precisa de comportamento determinístico;
        produção nunca passa (fica "", sorteia entre as 3)."""
        import uuid as _uuid_bot, json as _json_bot, random as _rand_bot
        bot_session_id = f"bot_{_uuid_bot.uuid4().hex[:8]}"
        # Classe sorteada (16/08/2026, pedido do usuário — bot v1 sempre
        # nascia Guerreiro; agora varia pra compra inicial por classe
        # (BotAISystem.do_initial_shopping) fazer sentido de verdade).
        _bot_class = class_id or _rand_bot.choice(["guerreiro", "arqueiro", "mago"])
        # 1ª skill de instância da classe (mesma tabela ÚNICA que
        # enter_normalized_progression já usa pra conceder a 1ª skill de
        # graça, server/instance_progression.py — nunca hardcodear skill
        # aqui: pra arqueiro/mago precisaria reinventar o mapeamento).
        from content.skill_config import INSTANCE_SKILL_UNLOCK_ORDER as _ISUO_bot
        _bot_skill = _ISUO_bot.get(_bot_class, [""])[0]
        char_data = {
            "tile_x": 0, "tile_y": 0,   # request_bg_accept reposiciona de verdade
            "name":      f"Bot {bot_session_id[-4:]}",
            "class_id":  _bot_class,
            "hp":        0,             # spawn_player usa max_hp quando 0
            "level":     BG_BOT_LEVEL,
            "stats_json": _json_bot.dumps({"level": BG_BOT_LEVEL}),
            # INITIAL_SKILLS_BY_CLASS é vazio pras 3 classes (skill só vem
            # de treinador/talento) — sem isso o bot nasceria sem NENHUMA
            # skill pra usar. `_bot_skill` (1ª skill de
            # INSTANCE_SKILL_UNLOCK_ORDER, ofensiva pra guerreiro/mago;
            # arqueiro recebe "recarregar", não-ofensiva — luta de
            # auto-attack à distância mesmo, igual arqueiro de verdade
            # nesse nível de instância).
            "skills_json": _json_bot.dumps({
                "learned": [_bot_skill] if _bot_skill else [],
                "hotbar":  [_bot_skill] if _bot_skill else [],
            }),
        }
        eid = self.spawn_player(bot_session_id, char_data)

        from engine.components import BotControlled as _BotCSpawn
        self.world.add_component(eid, _BotCSpawn())

        mgr = getattr(self, "_session_manager", None)
        if mgr is not None:
            from server.session import Session as _SessBot
            bot_sess = _SessBot(ws=_NullWebSocket(), session_id=bot_session_id)
            bot_sess.entity_id     = eid
            bot_sess.char_data     = dict(char_data)
            bot_sess.authenticated = True
            mgr._sessions[bot_session_id]  = bot_sess
            mgr._eid_to_sid[eid]           = bot_session_id
        return eid

    def _despawn_bot_player(self, eid: int) -> None:
        """Remove um bot por completo — entidade ECS (`WorldServer.
        despawn_player`, mesmo caminho de qualquer disconnect real, sem
        nenhum efeito colateral de save/DB) + a Session FALSA registrada
        em `SessionManager` (mesmas 2 tabelas que um disconnect real
        limpa, `_sessions`/`_eid_to_sid` — ver `server/session.py`,
        bloco de `on_disconnect`)."""
        sid = self._player_eid_to_sid.get(eid)
        if sid is None:
            return
        self.despawn_player(sid)
        mgr = getattr(self, "_session_manager", None)
        if mgr is not None:
            mgr._sessions.pop(sid, None)
            mgr._eid_to_sid.pop(eid, None)

    def _tick_bg_queue(self) -> None:
        """Roda 1x por tick (mesmo padrão de `_tick_arena_queue`). Pra
        CADA modo (2v2/3v3/5v5, filas independentes desde 10/08/2026 —
        achado real do usuário, ver docstring do módulo): limpa tokens
        que desapareceram, depois forma o máximo de partidas possível
        dessa fila (loop — 10 solos esperando no 5v5 podem virar 2
        partidas na mesma passada, não só 1). 2v2 usa
        `_bg_try_pack_with_bots` (backfill de bot, 16/08/2026); 3v3/5v5
        continuam com `_bg_try_pack` inalterado (fora de escopo, sem bot
        ainda)."""
        for mode_id, queue in self._bg_queues.items():
            for token in list(queue):
                if self._bg_members_for_token(token) is None:
                    queue.remove(token)

        import time as _time_tbq
        now = _time_tbq.time()

        for mode_id, queue in self._bg_queues.items():
            while True:
                if mode_id == "2v2":
                    packed = self._bg_try_pack_with_bots(mode_id, now)
                else:
                    packed = self._bg_try_pack(mode_id)
                if packed is None:
                    break
                side_a_tokens, side_b_tokens = packed
                team_a_eids = [eid for t in side_a_tokens for eid in self._bg_members_for_token(t)]
                team_b_eids = [eid for t in side_b_tokens for eid in self._bg_members_for_token(t)]
                for t in side_a_tokens + side_b_tokens:
                    queue.remove(t)

                team_size = BG_MODES[mode_id]["team_size"]
                bot_eids_this_match: list[int] = []
                while len(team_a_eids) < team_size:
                    bot_eid = self._spawn_bot_player()
                    team_a_eids.append(bot_eid)
                    bot_eids_this_match.append(bot_eid)
                while len(team_b_eids) < team_size:
                    bot_eid = self._spawn_bot_player()
                    team_b_eids.append(bot_eid)
                    bot_eids_this_match.append(bot_eid)

                # Lane sorteada por bot (16/08/2026, pedido do usuário) —
                # 1 por TIME, sem reposição (random.sample), garante que 2
                # bots do MESMO time nunca caem na mesma lane. Bot do time
                # oposto pode sortear a mesma lane normalmente (é a MESMA
                # lane, dois lados empurrando um contra o outro — padrão
                # de MOBA, não colisão).
                if bot_eids_this_match:
                    from engine.components import BotControlled as _BotCLane
                    import random as _rand_lane
                    for _team_eids in (team_a_eids, team_b_eids):
                        _team_bots = [e for e in _team_eids if e in bot_eids_this_match]
                        if not _team_bots:
                            continue
                        _lanes = _rand_lane.sample(["top", "mid", "bot"], k=len(_team_bots))
                        for _bot_eid, _lane_id in zip(_team_bots, _lanes):
                            _bc = self.world.get_component(_bot_eid, _BotCLane)
                            if _bc is not None:
                                _bc.lane_id = _lane_id

                self._propose_bg_match(team_a_eids, team_b_eids, mode_id)
                # Bot não tem cliente pra mandar BG_MATCH_ACCEPT — aceita
                # sozinho na hora, exatamente como request_bg_accept já
                # aceitaria pra um humano (mesma função, sem mudança).
                for bot_eid in bot_eids_this_match:
                    self.request_bg_accept(bot_eid)

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

        # Bot AI v2 (16/08/2026, pedido do usuário) — compra/equipa o kit
        # inicial e agenda quando pode começar a empurrar a PRÓPRIA lane
        # (10s depois da wave nascer). Só aqui: instância já garantidamente
        # carregada (_minion_lanes[instance_key] populado) e Wallet com
        # INSTANCE_STARTING_GOLD já concedido pela linha acima.
        from engine.components import BotControlled as _BotCAccept
        if self.world.get_component(eid, _BotCAccept) is not None:
            from server.bot_ai_system import BotAISystem as _BotAIAccept
            _BotAIAccept.do_initial_shopping(self, eid)
            _BotAIAccept.schedule_lane_push(
                self, eid, match["instance_key"], faction_id, match["countdown_deadline"])

        from engine.components import CharStatsTracker as _CST_bg
        _cst_bg = self.world.get_component(eid, _CST_bg)
        match["stat_snapshots"][eid] = {
            "kills":   _cst_bg.players_killed if _cst_bg else 0,
            "deaths":  _cst_bg.deaths if _cst_bg else 0,
            "assists": _cst_bg.assists if _cst_bg else 0,
            "farm":    _cst_bg.minions_killed if _cst_bg else 0,
            "damage":  (_cst_bg.pve_damage + _cst_bg.pvp_damage) if _cst_bg else 0,
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
        # remove_component já tolera alvo ausente sozinho — ver PROBLEMAS_ARQUITETURA.md §165.
        from engine.components import Faction as _FactionBgL
        self.world.remove_component(eid, _FactionBgL)

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

        from engine.components import BotControlled as _BotCLeave

        team_key = "team_a" if eid in match["team_a"] else "team_b"
        match[team_key] = [m for m in match[team_key] if m != eid]

        _remaining = match["team_a"] + match["team_b"]
        # Nenhum jogador REAL (não-bot) resta nos 2 times (20/09/2026,
        # pedido do usuário) — critério pra uma partida da fila REAL
        # existir é ter um humano nela; sem isso a instância ficava viva
        # até cada bot sair sozinho (Nexus cair organicamente + timeout de
        # resultado), podendo levar bem mais tempo que o necessário depois
        # que o único jogador real já foi embora. `bg_destroy_when_no_real_
        # players` (WorldServer.__init__) é a válvula de escape pra teste
        # que precisa inspecionar uma partida só-de-bot ainda viva.
        _no_real_player_left = (self.bg_destroy_when_no_real_players
                                and not any(self.world.get_component(o, _BotCLeave) is None
                                           for o in _remaining))

        if (not match["team_a"] and not match["team_b"]) or _no_real_player_left:
            from server.log import log as _log_bglv
            _reason_bglv = ("sem jogador real restante" if _no_real_player_left
                            else "times 100% vazios")
            _log_bglv.info(f"[BG] partida {match_id} encerrada ({_reason_bglv}) "
                          f"— destruindo {match['instance_key']}")
            self._bg_active_matches.pop(match_id, None)
            # Despawna os bots que sobravam ANTES de descarregar a
            # instância — `_despawn_bot_player` precisa do componente
            # `BotControlled` ainda vivo pra achar a sessão falsa/mapeamento
            # (bug real pego escrevendo o teste desta mudança: invertida, a
            # entidade já tinha sido removida por `_unload_instance` antes
            # da checagem "é bot?" rodar, então `get_component` voltava
            # None pra TODOS e nenhum bot era limpo de verdade — ficavam
            # órfãos em `_player_eid_to_sid`/`SessionManager._sessions`
            # mesmo com a entidade ECS já apagada). `_unload_instance`
            # depois só limpa o que sobrar (torre/minion/tilemap) — sua
            # varredura por `MapLocation` já tolera entidade ausente.
            for _other_eid in _remaining:
                if self.world.get_component(_other_eid, _BotCLeave) is not None:
                    self._despawn_bot_player(_other_eid)
            self._unload_instance(match["instance_key"])
            match["team_a"] = []
            match["team_b"] = []

        # Bot não tem cliente pra sair sozinho nem personagem pra salvar —
        # ao sair da partida (Nexus destruído + timeout de resultado, OU
        # time incompleto descartado em _tick_bg_pending), despawna de
        # vez. Centralizado AQUI (não em cada chamador) porque TODOS os
        # caminhos de saída (timeout de resultado via _im_results_timeout,
        # sweep de time incompleto, saída manual) já convergem pra esta
        # mesma função.
        if self.world.get_component(eid, _BotCLeave) is not None:
            self._despawn_bot_player(eid)
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
        self._finish_bg_match(match, match_id, winner_faction)

    def _finish_bg_match(self, match: dict, match_id: str, winner_faction: str) -> None:
        """Ponto único que decide uma partida da fila REAL (01/09/2026,
        extraído de `notify_bg_queue_nexus_destroyed` pra também ser
        reaproveitado por `request_bg_forfeit`) — monta o placar
        (K/D/A/Farm/Gold/Dano por DELTA, mesmo cálculo dos dois
        chamadores) e enfileira `BG_MATCH_RESULT` pra TODOS os membros
        dos dois times. Não teleporta ninguém (mesma filosofia de 2 fases
        da Arena) — quem sai de verdade é via `request_bg_leave`, manual
        ou por timeout (`_tick_bg_results_timeout`)."""
        from engine.components import CharStatsTracker as _CSTn, CharacterStats as _CharN, Faction as _FactionN

        players = []
        for m_eid in match["team_a"] + match["team_b"]:
            fac = self.world.get_component(m_eid, _FactionN)
            team_faction = fac.faction_id if fac else None
            cst = self.world.get_component(m_eid, _CSTn)
            snap = match["stat_snapshots"].get(
                m_eid, {"kills": 0, "deaths": 0, "assists": 0, "farm": 0, "damage": 0})
            char = self.world.get_component(m_eid, _CharN)
            players.append({
                "eid":     m_eid,
                "name":    char.name if char else "",
                "team":    "a" if team_faction == "arena_time_a" else "b",
                "kills":   (cst.players_killed if cst else 0) - snap["kills"],
                "deaths":  (cst.deaths if cst else 0) - snap["deaths"],
                "assists": (cst.assists if cst else 0) - snap.get("assists", 0),
                "farm":    (cst.minions_killed if cst else 0) - snap["farm"],
                "gold":    self._bg_sample_gold_earned(match, m_eid),
                "damage":  ((cst.pve_damage + cst.pvp_damage) if cst else 0) - snap["damage"],
                "won":     team_faction == winner_faction,
            })

        import time as _time_nex
        match["decided"]        = True
        match["decided_at"]     = _time_nex.time()
        match["winner_faction"] = winner_faction
        result_payload = {"winner_faction": winner_faction, "players": players,
                          "mode": match.get("mode_id", "5v5")}
        for m_eid in match["team_a"] + match["team_b"]:
            self._bg_match_result_events_this_tick.append((m_eid, result_payload))

    def request_bg_forfeit(self, eid: int) -> "str | None":
        """Comando de chat /forfeit ou /ff na fila REAL (01/09/2026,
        pedido do usuário — mesma UX da Arena, sem votação por enquanto,
        versão de testes). Diferente de `request_bg_leave`: não é "eu
        saio", é "a partida acaba agora" — declara o time ADVERSÁRIO de
        `eid` vencedor na hora, reaproveitando `_finish_bg_match` (MESMO
        placar que o Nexus caindo gera). Só funciona depois que a luta
        começou (portão aberto) e antes de decidida — retorna motivo em
        string se recusado, None em sucesso."""
        match_id = self._player_bg_match_id.get(eid)
        if match_id is None:
            return "not_in_match"
        match = self._bg_active_matches.get(match_id)
        if match is None:
            return "not_in_match"
        if match["decided"]:
            return "already_decided"
        if not match["fight_started"]:
            return "fight_not_started"
        winner_faction = "arena_time_b" if eid in match["team_a"] else "arena_time_a"
        self._finish_bg_match(match, match_id, winner_faction)
        return None

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
                kills   = (cst.players_killed if cst else 0) - snap["kills"]
                deaths  = (cst.deaths if cst else 0) - snap["deaths"]
                assists = (cst.assists if cst else 0) - snap.get("assists", 0)
                farm    = (cst.minions_killed if cst else 0) - snap["farm"]
                gold    = self._bg_sample_gold_earned(match, eid)
                cur = (kills, deaths, assists, farm, gold)
                if match["last_kda_sent"].get(eid) == cur:
                    continue
                match["last_kda_sent"][eid] = cur
                self.queue_stats_update({
                    "player_eid":    eid,
                    "match_kills":   kills,   "match_deaths": deaths,
                    "match_assists": assists,
                    "match_farm":    farm,    "match_gold":   gold,
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
