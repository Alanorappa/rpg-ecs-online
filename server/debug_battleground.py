"""
debug_battleground.py — gancho de DEBUG/teste (01/08/2026) pra validar
rotas de minion + torres + progressão normalizada de instância no mapa
maps/moba_battleground.csv, sem esperar o modo Battlefield de verdade
(fila/matchmaking, ver next_implementations/battlefield_design.md) ser
construído. NÃO é o modo Battlefield final — infraestrutura de teste
descartável/reaproveitável, isolada neste módulo, zero acoplamento com
Arena (server/match_processor.py não é tocado).

Disparado via comando de chat, interceptado em
server/session.py::SessionManager._handle_chat ANTES de virar mensagem
de chat de verdade (sem broadcast, resposta só pro remetente):

  /testbg a       — entra na instância de teste no time A (cria a
                    instância compartilhada se ainda não existir),
                    aplica progressão normalizada (server/
                    instance_progression.py).
  /testbg b       — idem, time B.
  /testbg leave   — sai, restaura estado real (talento/skill/gold/itens/
                    level), remove Faction; descarrega a instância
                    quando os dois times ficam vazios.

Instância COMPARTILHADA (1 por processo de servidor, não 1 por jogador)
— todo mundo que digitar /testbg cai na MESMA partida de teste, suficiente
pra validar rotas/torres a dois ou sozinho.

Portão: mesmo espírito do ARENA_GATE_TILES (shared/constants.py), mas
LOCAL a este módulo — não generaliza a constante da Arena (separação
deliberada, Arena continua intocada). Abre sozinho DEBUG_BG_GATE_S
segundos depois do PRIMEIRO player entrar (não por-jogador), ativando
junto as lanes de minion (`_activate_minion_lanes`, só chamado aqui, no
gate-open — nunca no load da instância, senão minion nasceria durante o
preparo, mesma regra da Arena).

Limitação cosmética conhecida (aceitável — feature de teste, não
produção): o cliente NÃO recebe evento de "portão abriu" (ARENA_GATE_OPEN
é hardcoded pra ARENA_GATE_TILES, client/arena_handlers.py, não genérico)
— o servidor já aceita/valida movimento pela tile aberta e minions
pathfindam contra o tile_matrix server-side normalmente, mas o cliente
pode continuar RENDERIZANDO a célula como portão fechado até trocar de
mapa/relogar. Não afeta a validação de rotas/torres/progressão em si.
"""
from __future__ import annotations

import time

from engine.components import Tilemap, Faction, GhostState
from engine.tileset import STONE_FLOOR
from server.instance_progression import enter_normalized_progression, exit_normalized_progression
from shared.constants import DEBUG_BG_RESPAWN_S, TICK_INTERVAL

DEBUG_BG_TEMPLATE     = "maps/moba_battleground.csv"
DEBUG_BG_INSTANCE_KEY = "maps/moba_battleground.csv::debugtest"
DEBUG_BG_GATE_S       = 15.0
# Fim de partida (02/08/2026, pedido do usuário, confirmado via pergunta:
# botão "Voltar" + timeout automático — mesmo padrão que a Arena já usa
# hoje pra ARENA_RESULT_AUTO_LEAVE_S). Valor local, não reaproveita a
# constante da Arena de propósito (zero acoplamento, ver docstring do
# módulo) — mesmo número por coincidência de gosto, não por import.
DEBUG_BG_RESULT_AUTO_LEAVE_S = 15.0

# Tiles dentro do bolsão de cada time (fora da linha do portão) — confirmados
# não-sólidos (piso de pedra) na geração real do mapa.
DEBUG_BG_SPAWN: dict[str, tuple] = {"a": (3, 96), "b": (96, 3)}

def _discover_gate_tiles(template_path: str, spawn_points: dict) -> "dict[str, list[tuple]]":
    """Varre o terrain.csv do template e acha os tiles de portão
    (ARENA_GATE_TILE, char 'D') em RUNTIME — nunca hardcoded, pra
    sobreviver a qualquer nova versão do mapa sem precisar atualizar
    coordenada à mão de novo (bug real, 16/08/2026: uma versão antiga
    hardcoded travou na posição do portão de um mapa anterior — quando o
    mapa foi regenerado com arte nova, o portão nunca abria de verdade e
    minion nunca encontrava rota pra sair da base, pra sempre). Agrupado
    por time via tile MAIS PERTO (Chebyshev) do spawn daquele time —
    agrupamento é só organizacional, os consumidores (`_tick_gate`/
    `_im_open_gate_if_ready`) abrem tudo junto de qualquer forma."""
    from engine.map_loader import load_map_csv
    terrain_matrix, _, _, _ = load_map_csv(template_path)
    gate_cells = [(x, y) for y, row in enumerate(terrain_matrix)
                  for x, ch in enumerate(row) if ch == "D"]
    result: dict[str, list[tuple]] = {side: [] for side in spawn_points}
    for gx, gy in gate_cells:
        nearest = min(spawn_points, key=lambda side: max(
            abs(gx - spawn_points[side][0]), abs(gy - spawn_points[side][1])))
        result[nearest].append((gx, gy))
    return result


DEBUG_BG_GATE_TILES: dict[str, list[tuple]] = _discover_gate_tiles(DEBUG_BG_TEMPLATE, DEBUG_BG_SPAWN)

_state = {
    "loaded":        False,
    "gate_deadline": None,   # time.time() alvo; None = ainda não iniciou
    "gate_open":     False,
    "members":       set(),
    "return_pos":    {},     # eid -> (map_file, tx, ty)
    "tick_registered": False,
    # Respawn automático na base (02/08/2026, pedido do usuário — "poderia
    # ter um respawn dele na base, com uma contagem de 15 segundos") —
    # eid -> segundos restantes. Populado quando GhostState.is_dead vira
    # True (ver _process_respawns), decrementado 1x por tick; ao chegar a
    # 0, teleporta pra base do TIME (Faction) do jogador e reaproveita
    # WorldServer._revive_player (mesma rotina de HP/mana/limpeza de
    # corpse/broadcast que o revive manual de cemitério já usa).
    "respawn_timers": {},
    # eids pendentes de notificação (ARENA_GATE_OPEN com gate_tiles) —
    # drenado por server/session.py::_dispatch_tick_deltas a cada tick,
    # mesmo padrão de WorldServer.consume_arena_gate_open_events() (ver
    # drain_gate_open_notifications() abaixo). Populado 1x quando o
    # portão abre de verdade (_tick).
    "pending_gate_open_eids": [],
    # Fim de partida / Nexus (02/08/2026, pedido do usuário) — ver
    # notify_nexus_destroyed()/_tick_bg_results_timeout()/_tick_kda_hud().
    "stat_snapshots":  {},     # eid -> {"kills","deaths","farm","damage"} no momento do /testbg
    "last_kda_sent":   {},     # eid -> (kills,deaths,farm,gold) já mandado, pro dirty-check do HUD
    # Gold GANHO (03/08/2026, bug real: "compra de item deixa o gold da HUD
    # negativo, não precisa descontar o gold usado") — acumulador
    # monotônico, amostrado 1x por tick contra o wallet.gold real: só soma
    # deltas POSITIVOS (kill/loot), nunca subtrai quando o player gasta
    # numa compra — ver _tick_kda_hud/notify_nexus_destroyed.
    "gold_earned":     {},     # eid -> int acumulado desde a entrada
    "last_wallet_gold": {},    # eid -> último wallet.gold amostrado (baseline do delta)
    "match_decided":   False,  # nexus caiu, aguardando players saírem (manual ou timeout)
    "result_deadline": None,   # time.time() alvo do timeout automático de saída
    "pending_match_result":       [],  # [(eid, payload_bg_match_result), ...]
    "pending_forced_leave_notify": [],  # [(eid, zone_change), ...] — timeout forçou _leave()
}


def drain_gate_open_notifications() -> list:
    """Consome (e limpa) os eids pendentes de aviso de portão aberto —
    chamar 1x por tick do dispatch loop (server/session.py). Mesmo
    padrão de WorldServer.consume_arena_gate_open_events()."""
    pending = _state["pending_gate_open_eids"]
    _state["pending_gate_open_eids"] = []
    return pending


def drain_match_result_notifications() -> list:
    """(eid, payload) pendentes de BG_MATCH_RESULT (placar de fim de
    partida — nexus derrubado) — drenado 1x por tick do dispatch loop
    (server/session.py). Mesmo padrão de drain_gate_open_notifications()."""
    pending = _state["pending_match_result"]
    _state["pending_match_result"] = []
    return pending


def drain_forced_leave_notify() -> list:
    """(eid, zone_change) pendentes de ZONE_CHANGE — timeout automático de
    DEBUG_BG_RESULT_AUTO_LEAVE_S segundos da tela de resultado forçou
    _leave() de quem não clicou "Voltar" a tempo. Mesmo padrão de
    drain_gate_open_notifications()."""
    pending = _state["pending_forced_leave_notify"]
    _state["pending_forced_leave_notify"] = []
    return pending


def handle_command(ws, session, eid: int, args: list[str]) -> "tuple[str, dict | None, dict | None]":
    """Processa os args de "/testbg ..." (já sem a palavra do comando).

    Retorna (texto_de_resposta, zone_change_payload_ou_None,
    countdown_payload_ou_None — já no formato exato do MsgType.
    ARENA_COUNTDOWN: `{"remaining": float, "my_faction": str}`). O
    chamador (server/session.py::
    _handle_chat) precisa, ANTES da resposta de chat:
    - mandar ZONE_CHANGE + limpar session.known_eids sempre que
      zone_change não for None — é o MESMO fluxo que toda troca de
      mapa/instância real usa (Arena: request_arena_accept/
      _arena_leave_now via consume_arena_match_start/end_events,
      session.py ~2394/~2431). Sem isso o cliente nunca sabe que trocou
      de mapa e quebra ao receber AOI_UPDATE de entidades de um tilemap
      que ele nunca carregou (bug real, 01/08/2026 — conexão caía
      ~15-20s depois do /testbg).
    - mandar ARENA_COUNTDOWN <countdown_payload> sempre que não for None
      — reaproveita o overlay cosmético que a Arena já tem (client/
      arena_handlers.py::_draw_arena_countdown_overlay, só depende de
      `_arena_countdown_deadline_val`, sem nenhum gate de "está numa
      partida de arena") — pedido do usuário, 01/08/2026: sem isso não
      dá pra saber quando o portão abre pra ir explorar. `my_faction`
      dentro do payload também alimenta a cor de barra de HP de
      mob/torre/minion (`_my_current_faction_val`, ver
      client/remote_entity_handlers.py::_draw_mob_hp_bars)."""
    if not args:
        return "Uso: /testbg a | /testbg b | /testbg leave", None, None
    sub = args[0].lower()
    if sub == "leave":
        return _leave(ws, eid)
    if sub in ("a", "b"):
        return _enter(ws, eid, sub)
    return "Uso: /testbg a | /testbg b | /testbg leave", None, None


def _ensure_tick_registered(ws) -> None:
    if _state["tick_registered"]:
        return
    ws.register_on_tick(lambda tick_count, deltas: _tick(ws))
    _state["tick_registered"] = True


def _enter(ws, eid: int, side: str) -> "tuple[str, dict | None, float | None]":
    if eid in _state["members"]:
        return "Você já está no battleground de teste.", None, None

    _ensure_tick_registered(ws)

    if not _state["loaded"]:
        ws._load_instance(DEBUG_BG_TEMPLATE, DEBUG_BG_INSTANCE_KEY)
        _state["loaded"] = True

    sid = ws.get_session_id_for_player(eid)
    if sid is None:
        return "Sessão não encontrada.", None, None
    tx, ty = ws.get_tile_pos(sid)
    _state["return_pos"][eid] = (ws.get_player_map(sid), tx, ty)

    faction_id = "arena_time_a" if side == "a" else "arena_time_b"
    ws.world.add_component(eid, Faction(faction_id))
    spawn_x, spawn_y = DEBUG_BG_SPAWN[side]
    ws.transfer_player(sid, eid, DEBUG_BG_INSTANCE_KEY, spawn_x, spawn_y)

    enter_normalized_progression(ws, eid)

    # Snapshot pro HUD ao vivo e pro placar final (02/08/2026, pedido do
    # usuário) — kills/deaths/farm/damage viram "estatística da partida"
    # por DELTA contra este momento (CharStatsTracker é vitalício, nunca
    # reseta sozinho).
    from engine.components import CharStatsTracker as _CST_enter
    _cst_enter = ws.world.get_component(eid, _CST_enter)
    _state["stat_snapshots"][eid] = {
        "kills":  _cst_enter.players_killed if _cst_enter else 0,
        "deaths": _cst_enter.deaths if _cst_enter else 0,
        "farm":   _cst_enter.minions_killed if _cst_enter else 0,
        "damage": (_cst_enter.pve_damage + _cst_enter.pvp_damage) if _cst_enter else 0,
    }
    _state["last_kda_sent"].pop(eid, None)
    # Gold GANHO (03/08/2026 — ver docstring de _state acima): baseline no
    # gold inicial de instância (INSTANCE_STARTING_GOLD, já aplicado por
    # enter_normalized_progression acima) — acumulador começa zerado.
    from server.instance_progression import INSTANCE_STARTING_GOLD as _ISG_enter
    _state["gold_earned"][eid]      = 0
    _state["last_wallet_gold"][eid] = _ISG_enter

    _state["members"].add(eid)
    if _state["gate_deadline"] is None:
        _state["gate_deadline"] = time.time() + DEBUG_BG_GATE_S
    # Quem entra depois do primeiro já vê o tempo restante MENOR (contagem
    # é da "partida", não por-jogador) — mesmo espírito do ARENA_COUNTDOWN
    # real (documentado no MsgType).
    countdown_remaining = max(0.0, _state["gate_deadline"] - time.time())

    reply = (f"Battleground de teste — time {side.upper()}. Portão abre em "
             f"{countdown_remaining:.0f}s. '/testbg leave' pra sair.")
    # ZONE_CHANGE pro cliente usa o TEMPLATE puro (DEBUG_BG_TEMPLATE), nunca
    # a instance_key sintética (DEBUG_BG_INSTANCE_KEY) — o cliente sempre
    # carrega o mesmo CSV compartilhado por todas as instâncias; quem
    # diferencia "qual instância" é 100% server-side (MapLocation/AOI).
    # Mesmo "de-para" que Arena já faz via _template_file_of
    # (server/world_server.py) antes de montar o ARENA_MATCH_START — bug
    # real, 01/08/2026: mandar a instance_key crua aqui quebrava
    # load_map_csv no cliente (FileNotFoundError, "arquivo::sufixo" não
    # existe em disco).
    zone_change = {"map_file": DEBUG_BG_TEMPLATE,
                   "target_x": spawn_x, "target_y": spawn_y}
    # "my_faction" (01/08/2026, bug real: barra de HP de minion/torre
    # aliado saía amarela — client/remote_entity_handlers.py::
    # _draw_mob_hp_bars comparava contra o PLAYER_FACTION genérico
    # hardcoded, nunca contra o time real do jogador, porque o cliente
    # nunca aprendia essa string em lugar nenhum). Cliente guarda em
    # self._my_current_faction_val (client/arena_handlers.py::
    # _handle_msg_arena_countdown) e usa no lugar do PLAYER_FACTION
    # genérico pra colorir barra de HP de mob/torre/minion.
    countdown = {"remaining": countdown_remaining, "my_faction": faction_id}
    return reply, zone_change, countdown


def _leave(ws, eid: int) -> "tuple[str, dict | None, dict | None]":
    if eid not in _state["members"]:
        return "Você não está no battleground de teste.", None, None

    exit_normalized_progression(ws, eid)
    ws.world.remove_component(eid, Faction)

    sid = ws.get_session_id_for_player(eid)
    map_file, tx, ty = _state["return_pos"].pop(eid, (ws.MAP_FILE, 115, 389))
    if sid is not None:
        ws.transfer_player(sid, eid, map_file, tx, ty)

    _state["members"].discard(eid)
    # Timer de respawn pendente (morreu e saiu antes dos 15s acabarem) —
    # sem isso, _process_respawns tentaria snap_to_tile o jogador de volta
    # pra base do battleground DEPOIS dele já ter sido transferido pro
    # mapa real por transfer_player() acima (classe de bug: teletransporte
    # fantasma pro mapa errado).
    _state["respawn_timers"].pop(eid, None)
    _state["stat_snapshots"].pop(eid, None)
    _state["last_kda_sent"].pop(eid, None)
    _state["gold_earned"].pop(eid, None)
    _state["last_wallet_gold"].pop(eid, None)
    if not _state["members"]:
        ws._unload_instance(DEBUG_BG_INSTANCE_KEY)
        _state["loaded"]        = False
        _state["gate_deadline"] = None
        _state["gate_open"]     = False
        _state["respawn_timers"] = {}
        _state["match_decided"]   = False
        _state["result_deadline"] = None

    zone_change = {"map_file": map_file, "target_x": tx, "target_y": ty}
    # countdown=None: nenhuma contagem nova — o cliente já limpa
    # _my_current_faction_val/_arena_countdown_deadline_val sozinho ao
    # processar o ZONE_CHANGE acima (_handle_msg_zone_change).
    return "Saiu do battleground de teste — estado real restaurado.", zone_change, None


def on_disconnect(ws, eid: int) -> None:
    """Chamar do disconnect handler (server/session.py::on_disconnect)
    INCONDICIONALMENTE, ANTES do save-on-disconnect — mesma classe de bug
    já resolvida pra Arena (`end_matches_of` chamado antes do save por
    exatamente esse motivo, ver comentário em session.py::on_disconnect).

    Incidente real, 01/08/2026: personagem entrou no battleground de
    teste (level real 23 → level de instância 1), o cliente caiu por um
    bug não relacionado (ZONE_CHANGE com instance_key crua — corrigido
    junto, ver zone_change em _enter) ANTES de rodar "/testbg leave", e o
    disconnect salvou o estado NORMALIZADO (level 1) como se fosse o
    personagem real — level 23 perdido, sem backup recente (`data/
    game.db` não é commitado com frequência). Sem este hook, QUALQUER
    crash/disconnect/fechar-o-jogo enquanto dentro da instância de teste
    reproduz o mesmo incidente. Ver ARQUITETURA_ONLINE.md §34.74.1."""
    if eid not in _state["members"]:
        return
    _leave(ws, eid)


def _sample_gold_earned(ws, eid: int) -> int:
    """Amostra o `wallet.gold` atual e acumula em `_state["gold_earned"]`
    — só deltas POSITIVOS (kill/loot/recompensa) somam; um delta negativo
    (comprou item na loja de instância) só atualiza o baseline, nunca
    subtrai do acumulado (03/08/2026, bug real: "compra de item deixa o
    gold da HUD negativo, não precisa descontar o gold usado" — o cálculo
    antigo, `wallet.gold - INSTANCE_STARTING_GOLD`, é NET e ficava
    negativo assim que o player gastasse mais do que tinha ganho).

    Chamado tanto pelo HUD ao vivo (`_tick_kda_hud`, 1x/tick) quanto por
    `notify_nexus_destroyed` (o Nexus cai ANTES do gold do PRÓPRIO kill
    ser creditado no mesmo tick — ver ordem em server_death_handler.py —
    reamostra na hora pra não perder esse último delta no placar final).
    Retorna o total acumulado; 0 se `eid` não estiver em `_state
    ["gold_earned"]` (nunca entrou/já saiu)."""
    if eid not in _state["gold_earned"]:
        return 0
    from engine.components import Wallet as _WalletGE
    wallet = ws.world.get_component(eid, _WalletGE)
    if wallet is None:
        return _state["gold_earned"][eid]
    last  = _state["last_wallet_gold"].get(eid, wallet.gold)
    delta = wallet.gold - last
    if delta > 0:
        _state["gold_earned"][eid] += delta
    _state["last_wallet_gold"][eid] = wallet.gold
    return _state["gold_earned"][eid]


def notify_nexus_destroyed(ws, tower_map_file: str, destroyed_faction: str,
                           killer_eid: int) -> None:
    """Nexus derrubado — termina a partida de teste (02/08/2026, pedido
    do usuário). `destroyed_faction` é o time DONO da torre destruída
    (perdeu); o vencedor é o outro time. Chamado por
    server/server_death_handler.py só quando `Tower.is_nexus` — no-op se
    não houver partida de teste carregada com esse time, ou se a partida
    já estiver decidida (idempotente — protege contra os dois nexus
    caindo no mesmo tick, caso raríssimo/só possível com dano em área).

    `tower_map_file` (04/08/2026, pedido do usuário — fila real de
    matchmaking, `server/bg_queue_processor.py`): antes deste módulo
    supunha que só existia UMA instância do mapa MOBA por vez (a
    compartilhada de debug) — com partidas REAIS da fila coexistindo
    (cada uma na sua própria instância, `template::match_id`, mesmas
    strings de facção "arena_time_a/b"), uma torre caindo numa partida
    da fila ficaria AMBÍGUA com esta instância de debug se as duas
    estivessem carregadas ao mesmo tempo. Checa que a torre morta é
    DESTA instância (`DEBUG_BG_INSTANCE_KEY`) antes de prosseguir — nunca
    reage a um Nexus de outra instância.

    Monta o placar final (K/D/Farm/Gold/Dano por DELTA contra
    `_state["stat_snapshots"]`, tirado no `/testbg a|b` de cada um — ver
    docstring de `_enter`) pra TODOS os members dos dois times (confirmado
    com o usuário: tabela completa, não só o próprio player) e agenda o
    timeout automático de saída (`DEBUG_BG_RESULT_AUTO_LEAVE_S`) — NÃO
    teleporta ninguém ainda, mesma forma de 2 fases que a Arena já usa
    (`_finish_match` decide sem teleportar; `_arena_leave_now` teleporta
    quando cada um sai, manual ou por timeout — ver `_tick_bg_results_
    timeout` abaixo)."""
    if tower_map_file != DEBUG_BG_INSTANCE_KEY:
        return
    if _state["match_decided"] or not _state["members"]:
        return
    if destroyed_faction not in ("arena_time_a", "arena_time_b"):
        return
    winner_faction = ("arena_time_b" if destroyed_faction == "arena_time_a"
                      else "arena_time_a")

    from engine.components import CharStatsTracker, CharacterStats

    players = []
    for m_eid in _state["members"]:
        fac = ws.world.get_component(m_eid, Faction)
        team_faction = fac.faction_id if fac else None
        cst = ws.world.get_component(m_eid, CharStatsTracker)
        snap = _state["stat_snapshots"].get(
            m_eid, {"kills": 0, "deaths": 0, "farm": 0, "damage": 0})
        char = ws.world.get_component(m_eid, CharacterStats)
        players.append({
            "eid":    m_eid,
            "name":   char.name if char else "",
            "team":   "a" if team_faction == "arena_time_a" else "b",
            "kills":  (cst.players_killed if cst else 0) - snap["kills"],
            "deaths": (cst.deaths if cst else 0) - snap["deaths"],
            "farm":   (cst.minions_killed if cst else 0) - snap["farm"],
            "gold":   _sample_gold_earned(ws, m_eid),
            "damage": ((cst.pve_damage + cst.pvp_damage) if cst else 0) - snap["damage"],
            "won":    team_faction == winner_faction,
        })

    _state["match_decided"]   = True
    _state["result_deadline"] = time.time() + DEBUG_BG_RESULT_AUTO_LEAVE_S
    result_payload = {"winner_faction": winner_faction, "players": players}
    _state["pending_match_result"] = [(m_eid, result_payload) for m_eid in _state["members"]]


def _tick_bg_results_timeout(ws) -> None:
    """Timeout automático de saída da tela de resultado (02/08/2026,
    pedido do usuário, confirmado via pergunta: botão "Voltar" sai na
    hora + timeout força quem não clicar — mesmo padrão que a Arena já
    usa, `_tick_arena_results_timeout`/`ARENA_RESULT_AUTO_LEAVE_S`).
    `_leave()` já teleporta de volta (transfer_player) — só falta avisar
    o cliente via ZONE_CHANGE, o que `session.py` faz ao drenar
    `drain_forced_leave_notify()` (mesma forma do bloco Arena
    equivalente, `consume_arena_match_end_events`)."""
    if not _state["match_decided"] or _state["result_deadline"] is None:
        return
    if time.time() < _state["result_deadline"]:
        return
    for eid in list(_state["members"]):
        _, zone_change, _ = _leave(ws, eid)
        if zone_change is not None:
            _state["pending_forced_leave_notify"].append((eid, zone_change))


def _tick_kda_hud(ws) -> None:
    """HUD ao vivo de Kills/Deaths/Farm/Gold (02/08/2026, pedido do
    usuário) — dirty-check por tick (mesmo padrão de
    `WorldServer._sync_player_hp_dirty`): só enfileira STATS_UPDATE
    quando algum dos 4 valores muda de verdade pro member, não a cada
    tick. Reaproveita o canal STATS_UPDATE já 100% conectado
    (`ws.queue_stats_update`) — sem buffer novo, sem tocar `has_pending`."""
    if not _state["members"]:
        return
    from engine.components import CharStatsTracker

    for eid in list(_state["members"]):
        snap = _state["stat_snapshots"].get(eid)
        if snap is None:
            continue
        cst = ws.world.get_component(eid, CharStatsTracker)
        kills  = (cst.players_killed if cst else 0) - snap["kills"]
        deaths = (cst.deaths if cst else 0) - snap["deaths"]
        farm   = (cst.minions_killed if cst else 0) - snap["farm"]
        gold   = _sample_gold_earned(ws, eid)
        cur = (kills, deaths, farm, gold)
        if _state["last_kda_sent"].get(eid) == cur:
            continue
        _state["last_kda_sent"][eid] = cur
        ws.queue_stats_update({
            "player_eid":  eid,
            "match_kills":  kills, "match_deaths": deaths,
            "match_farm":   farm,  "match_gold":   gold,
        })


def _tick(ws) -> None:
    """Registrado 1x via WorldServer.register_on_tick — abre o portão,
    ativa as lanes de minion quando o deadline vence, processa respawn
    automático de membros mortos, o HUD ao vivo de KDA/Farm/Gold, e o
    timeout automático de saída da tela de resultado. Idempotente."""
    _process_respawns(ws)
    _tick_gate(ws)
    _tick_kda_hud(ws)
    _tick_bg_results_timeout(ws)


def _process_respawns(ws) -> None:
    """Respawn automático na base do time (02/08/2026, pedido do usuário:
    "poderia ter um respawn dele na base, com uma contagem de 15
    segundos") — estilo MOBA, sem "Liberar espírito"/caminhada de
    fantasma nem prompt "Reviver agora?" (esses continuam existindo pro
    resto do jogo — PvE normal, Arena — só o battleground de teste pula
    pra esse fluxo mais simples).

    Detecta member com `GhostState.is_dead=True` ainda não rastreado →
    inicia timer (`DEBUG_BG_RESPAWN_S`). Cada tick decrementa
    `TICK_INTERVAL`; ao chegar a 0, teleporta pro spawn do TIME (Faction)
    do jogador e reaproveita `WorldServer._revive_player` (mesma rotina
    de HP/mana/limpeza de corpse/broadcast PLAYER_REVIVE que o revive
    manual de cemitério já usa — nenhuma lógica de revive duplicada
    aqui)."""
    if not _state["members"]:
        return
    from engine.utils import snap_to_tile

    for eid in list(_state["members"]):
        gst = ws.world.get_component(eid, GhostState)
        if gst is None or not gst.is_dead:
            _state["respawn_timers"].pop(eid, None)
            continue

        if eid not in _state["respawn_timers"]:
            _state["respawn_timers"][eid] = DEBUG_BG_RESPAWN_S
            continue

        _state["respawn_timers"][eid] -= TICK_INTERVAL
        if _state["respawn_timers"][eid] > 0:
            continue

        del _state["respawn_timers"][eid]
        fac = ws.world.get_component(eid, Faction)
        side = "a" if (fac is None or fac.faction_id != "arena_time_b") else "b"
        spawn_x, spawn_y = DEBUG_BG_SPAWN[side]
        snap_to_tile(ws.world, eid, spawn_x, spawn_y, carry_prev=False)
        ws._revive_player(eid, hp_frac=1.0, at_corpse=False)


def _tick_gate(ws) -> None:
    """Abre o portão e ativa as lanes de minion quando o deadline vence.

    Abrir o portão aqui só troca o `tile_matrix` do SERVIDOR — sem
    avisar o cliente, ele continua RENDERIZANDO a célula como fechada
    pra sempre (bug real relatado pelo usuário, 01/08/2026: "fez a
    contagem de 15 segundos mas não abriu o gate"). Popula
    `pending_gate_open_eids` aqui pra `server/session.py` drenar
    (`drain_gate_open_notifications`) e mandar ARENA_GATE_OPEN com
    `gate_tiles` pro cliente fazer o MESMO swap localmente — mesmo
    padrão de `WorldServer._arena_gate_open_events_this_tick`/
    `consume_arena_gate_open_events`."""
    if _state["gate_open"] or _state["gate_deadline"] is None:
        return
    if time.time() < _state["gate_deadline"]:
        return

    bundle = ws._map_bundles.get(DEBUG_BG_INSTANCE_KEY)
    if bundle is not None:
        tilemap = ws.world.get_component(bundle.tilemap_entity, Tilemap)
        if tilemap is not None:
            for tiles in DEBUG_BG_GATE_TILES.values():
                for gx, gy in tiles:
                    tilemap.tile_matrix[gy][gx] = STONE_FLOOR
    ws._activate_minion_lanes(DEBUG_BG_INSTANCE_KEY)
    _state["gate_open"] = True
    _state["pending_gate_open_eids"] = list(_state["members"])
