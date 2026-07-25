"""
shared/messages.py
Protocolo de mensagens cliente ↔ servidor.

Regras:
- Todo pacote é um dict JSON com "type" (str) e "p" (payload dict).
- "seq" (int): número de sequência crescente por conexão (detecção de ordem).
- "ts"  (int): timestamp do cliente em ms (lag compensation).
- Servidor nunca confia em dados de gameplay vindos do cliente sem validar.

Fluxo geral:
  Cliente conecta → envia LOGIN → recebe LOGIN_OK ou LOGIN_ERROR
  LOGIN_OK inclui WORLD_STATE inicial (entidades no AOI)
  A cada tick: servidor envia AOI_UPDATE com deltas

Para adicionar nova mensagem:
  1. Adicionar constante em MsgType
  2. Documentar payload na seção correspondente (C→S ou S→C)
  3. Implementar handler no servidor (server/handlers.py) ou cliente (client/network.py)
"""
from __future__ import annotations
import json
import time
from enum import Enum


# ── Tipos de mensagem ─────────────────────────────────────────────────────────

class MsgType(str, Enum):
    # ── Autenticação ──────────────────────────────────────────────
    LOGIN              = "login"           # C→S
    LOGIN_OK           = "login_ok"        # S→C
    LOGIN_ERROR        = "login_error"     # S→C
    LOGOUT             = "logout"          # C→S (gracioso)

    # ── Mundo / AOI ───────────────────────────────────────────────
    WORLD_STATE        = "world_state"     # S→C  snapshot inicial ao entrar no mundo
    AOI_UPDATE         = "aoi_update"      # S→C  delta a cada tick: spawn/despawn/move
    ZONE_CHANGE_REQ    = "zone_change_req" # C→S  {to_map, target_x, target_y}
    ZONE_CHANGE        = "zone_change"     # S→C  {map_file, target_x, target_y}
    ENTER_INSTANCE     = "enter_instance"  # C→S  pedir entrada em instância        # TODO: não implementado

    # ── Movimento ─────────────────────────────────────────────────
    MOVE               = "move"            # C→S  jogador quer mover para tile
    ENTITY_MOVE        = "entity_move"     # S→C  confirmação/broadcast de movimento

    # ── Combate ───────────────────────────────────────────────────
    AUTO_ATTACK        = "auto_attack"     # C→S  iniciar/parar auto-attack
    COMBAT_RESULT      = "combat_result"   # S→C  resultado de hit (dano, miss, crit…)
    COMBAT_STOP        = "combat_stop"     # S→C  combate encerrado (alvo morreu etc.)

    # ── Skills ────────────────────────────────────────────────────
    CAST_SKILL         = "cast_skill"      # C→S  usar skill
    CANCEL_CAST        = "cancel_cast"     # C→S  player cancelou cast (movimento durante cast)
    CAST_DIR_UPDATE    = "cast_dir_update" # C→S  direção final de skill direcional na conclusão do cast {sid, dir_x, dir_y}
    PROJECTILE_HIT_CS  = "proj_hit_cs"    # C→S  projétil do player colidiu com o alvo
    CAST_START         = "cast_start"      # S→C  entidade começou cast (barra de cast)  # TODO: não implementado
    CAST_CANCEL        = "cast_cancel"     # S→C  cast interrompido                      # TODO: não implementado
    CAST_COMPLETE      = "cast_complete"   # S→C  cast concluído (dispara efeito)         # TODO: não implementado
    SKILL_RESULT       = "skill_result"    # S→C  gameplay: dano, cooldown, GCD, failed
    SKILL_EFFECT       = "skill_effect"    # S→C  apresentação: som/VFX broadcast AOI

    # ── Projéteis (reservado — não implementado ainda) ────────────
    PROJECTILE_SPAWN   = "proj_spawn"      # S→C  projétil criado
    PROJECTILE_HIT     = "proj_hit"        # S→C  projétil acertou (ou errou)
    PROJECTILE_DESPAWN = "proj_despawn"    # S→C  projétil removido

    # ── Efeitos de status ─────────────────────────────────────────
    EFFECT_APPLIED     = "effect_applied"  # S→C  efeito aplicado em entidade
    EFFECT_REMOVED     = "effect_removed"  # S→C  efeito removido

    # ── Stats / HP ────────────────────────────────────────────────
    STATS_UPDATE       = "stats_update"    # S→C  HP/MP/rage/concentration mudou
    LEVEL_UP           = "level_up"        # S→C  jogador levelou
    SKILL_LEVELS_UPDATE = "skill_levels_update"  # S→C  xp/level de skill level mudou (só pro dono, ver SkillLevels)
    PLAYER_DEATH       = "player_death"    # S→C  player morreu — corpse_tx, corpse_ty
    PLAYER_STATS_SYNC  = "player_stats_sync"  # S→C  sincroniza HP autoritativo do player

    # ── Quests ────────────────────────────────────────────────────
    QUEST_ACCEPT       = "quest_accept"    # C→S  aceitar quest no NPC {quest_id}
    QUEST_TURN_IN      = "quest_turn_in"   # C→S  entregar quest no NPC {quest_id, npc_name, chosen_item?} — chosen_item só relevante se QuestReward.choice não-vazio (23/07/2026, recompensa de itens)
    QUEST_UPDATE       = "quest_update"    # S→C  snapshot active/completed (só pro dono, ver QuestLog)

    # ── Morte/respawn: fluxo de espírito (ghost) + cemitério ────────
    RELEASE_SPIRIT     = "release_spirit"  # C→S  player clicou "Liberar espírito"
    REVIVE_REQUEST     = "revive_request"  # C→S  player clicou "Reviver agora?" (no corpo)
    PLAYER_REVIVE      = "player_revive"   # S→C  player reviveu — tx, ty, hp, hp_max, mana, max_mana
    GHOST_STATE        = "ghost_state"     # S→C  atualiza estado do espírito (near_corpse, graveyard_timer)

    # ── Entidades ─────────────────────────────────────────────────
    ENTITY_SPAWN       = "entity_spawn"    # S→C  entidade entrou no AOI (detalhes completos)
    ENTITY_DESPAWN     = "entity_despawn"  # S→C  entidade saiu do AOI ou morreu
    ENTITY_DEATH       = "entity_death"    # S→C  morte com animação (antes de despawn)
    SOUND_EVENT        = "sound_event"     # S→C  evento sonoro posicional (aggro, etc.)
    PLAYER_STAT_SYNC   = "player_stat_sync"  # C→S  OBSOLETO — servidor ignora (handler é no-op). Mantido só por compat.
    PLAYER_HP_SYNC     = "player_hp_sync"    # C→S  OBSOLETO — servidor ignora (handler é no-op). Mantido só por compat.
    EQUIP_SYNC         = "equip_sync"        # C→S  equipamento mudou {equipment: {slot: item_dict}}
    EQUIP_REJECTED     = "equip_rejected"    # S→C  slot recusado (level/classe) {slot, item_name, reason}

    # ── Trade (player↔player) ──────────────────────────────────────
    TRADE_REQUEST      = "trade_request"      # C→S  {target_eid}
    TRADE_INVITE       = "trade_invite"       # S→C  {from_eid, from_name} (só pro alvo)
    TRADE_ACCEPT       = "trade_accept"       # C→S  {} (alvo aceitou o convite pendente)
    TRADE_DECLINE      = "trade_decline"      # C→S  {} (alvo recusou o convite pendente)
    TRADE_OPEN         = "trade_open"         # S→C  {trade_id, other_eid, other_name} (pros dois)
    TRADE_OFFER_ITEM   = "trade_offer_item"   # C→S  {inv_index}
    TRADE_WITHDRAW_ITEM= "trade_withdraw_item"# C→S  {offer_slot}
    TRADE_SET_GOLD     = "trade_set_gold"     # C→S  {amount}
    TRADE_STATE        = "trade_state"        # S→C  {trade_id, my_offer[], my_gold, their_offer[], their_gold, my_confirmed, their_confirmed}
    TRADE_CONFIRM      = "trade_confirm"      # C→S  {}
    TRADE_RESULT       = "trade_result"       # S→C  {trade_id, received_items[], received_gold} (pros dois)
    TRADE_CANCEL       = "trade_cancel"       # C→S  {} (qualquer um dos dois pode cancelar a qualquer momento)
    TRADE_CANCELLED    = "trade_cancelled"    # S→C  {trade_id, reason} declined|cancelled|distance|disconnect|inventory_full|invalid

    # Duelo (contexto PvP por convite — ver server/duel_processor.py)
    DUEL_REQUEST       = "duel_request"       # C→S  {target_eid} — botão "Duelar" do modal de interação
    DUEL_INVITE        = "duel_invite"        # S→C  {from_eid, from_name} — só ao alvo
    DUEL_ACCEPT        = "duel_accept"        # C→S  {} — resposta ao convite pendente
    DUEL_DECLINE       = "duel_decline"       # C→S  {} — idem
    DUEL_START         = "duel_start"         # S→C  {opponent_eid, opponent_name} — pros dois; par vira hostil um ao outro
    DUEL_END           = "duel_end"           # S→C  {winner_eid, loser_eid, reason} win|declined|distance|disconnect — pros dois

    # Party/Grupo (ver server/party_processor.py) — convite via modal OU comando de chat "/convidar"
    PARTY_INVITE        = "party_invite"        # C→S  {target_eid} — botão "Convidar p/ Grupo" ou /convidar
    PARTY_INVITE_RECEIVED = "party_invite_received"  # S→C  {from_eid, from_name} — só ao alvo
    PARTY_INVITE_FAILED = "party_invite_failed" # S→C  {reason} — só ao requester, convite recusado na origem
    PARTY_ACCEPT        = "party_accept"        # C→S  {} — resposta ao convite pendente
    PARTY_DECLINE       = "party_decline"       # C→S  {} — idem
    PARTY_STATE         = "party_state"         # S→C  {party_id, leader_eid, members:[{eid,name,class_id,level,hp,hp_max}]} — pra todos os membros a cada mudança; party_id=-1/members=[] individual pra quem saiu/foi expulso
    PARTY_LEAVE         = "party_leave"         # C→S  {}
    PARTY_KICK          = "party_kick"          # C→S  {target_eid} — só líder

    # Arena 1x1/2x2/3x3 (Fase G leva 1 + Fase H, 23/07/2026 — ver
    # server/match_processor.py::ARENA_MODES). Fila é por MODO + por GRUPO
    # (Party do tamanho exato do modo), não por player solto — EXCEÇÃO: no
    # modo "1v1" ("Duelo (Arena)"), soloqueue, sem grupo exigido, só o
    # líder entra/sai da fila nos demais modos. "Time" não tem protocolo
    # próprio — é o próprio grupo que entrou na fila junto (ou o player
    # sozinho, no 1v1).
    ARENA_QUEUE_JOIN    = "arena_queue_join"    # C→S  {mode: str} — "1v1"|"2v2"|"3v3"; líder do grupo (exceto 1v1, soloqueue)
    ARENA_QUEUE_LEAVE   = "arena_queue_leave"   # C→S  {} — sai da fila em que estiver
    ARENA_QUEUE_STATE   = "arena_queue_state"   # S→C  {in_queue: bool, mode?: str, reason?: str} — reason só quando um JOIN foi recusado (wrong_size|already_queued|in_match|no_party|invalid_mode)
    # Aceite de partida (21/07/2026, pedido do usuário): fila pareia mas não
    # teleporta mais ninguém direto — cada um dos 4 (ou 2, no 1v1) recebe
    # ARENA_MATCH_FOUND e tem ARENA_ACCEPT_WINDOW_S (shared/constants.py)
    # pra mandar ARENA_MATCH_ACCEPT; quem não manda a tempo simplesmente
    # não entra (a partida segue só com quem aceitou). ARENA_MATCH_START
    # passa a disparar POR PLAYER, no momento do aceite dele — não mais em
    # lote pros 4 juntos.
    ARENA_MATCH_FOUND   = "arena_match_found"   # S→C  {mode, teammates:[eid], opponents:[eid]} — partida pareada, aguardando aceite
    ARENA_MATCH_ACCEPT  = "arena_match_accept"  # C→S  {} — aceita a partida encontrada, entra na arena
    ARENA_COUNTDOWN     = "arena_countdown"     # S→C  {remaining: float} — segundos até o combate liberar (mandado junto do ARENA_MATCH_START de cada player que entra; contagem é DA PARTIDA — quem entra depois já recebe remaining menor/zero)
    ARENA_MATCH_START   = "arena_match_start"   # S→C  {mode, map_file, teammates:[eid], opponents:[eid]} — disparado no aceite de CADA player (não mais em lote), junto do ZONE_CHANGE pra instância
    ARENA_MATCH_END     = "arena_match_end"     # S→C  {won: bool} — junto do ZONE_CHANGE de volta pro mapa/posição de antes
    ARENA_FORFEIT       = "arena_forfeit"       # C→S  {} — comando de chat /forfeit ou /ff (ou botão "Sair da Arena"), sai na hora
    ARENA_MATCH_RESULT  = "arena_match_result"  # S→C  {mode, results:[{eid,name,damage,won}]} — placar de fim de partida (modal, não teleporta sozinho)
    # Portão físico de arena (22/07/2026, pedido do usuário — modelo WoW: em vez de
    # travar ação/movimento no preparo, contém cada time numa sala fechada até o
    # portão abrir). Coordenadas das células em ARENA_GATE_TILES (shared/constants.py).
    ARENA_GATE_OPEN     = "arena_gate_open"     # S→C  {} — o(s) portão(ões) da arena abriu(ram) (fim do preparo); mandado a cada um dos 4, inclusive quem aceitar DEPOIS do portão já ter aberto

    CONSUMABLE_USE     = "consumable_use"    # C→S  uso de consumível (heal_instant, HoT, buffs futuros)
    GOLD_UPDATE        = "gold_update"       # C→S  gold mudou (loot de moedas) {gold: N}
    INV_SYNC           = "inv_sync"          # C→S  inventário mudou (loot de item) {inventory: [...]}
    TALENT_UPDATE      = "talent_update"     # C→S  talento alocado/desalocado {talents: {chosen_build, allocated, available_points}}
    HOTBAR_UPDATE      = "hotbar_update"     # C→S  barra de ações/consumíveis mudou {skills, consumables}
    BUY_REQUEST        = "buy_request"       # C→S  compra em loja {shop_id, item_name, quantity}
    BUY_RESULT         = "buy_result"        # S→C  resultado da compra {success, reason, item, new_gold}
    SELL_REQUEST       = "sell_request"      # C→S  {item_name, item_value, stack_sold}
    SELL_RESULT        = "sell_result"       # S→C  {success, item_name, sell_price, new_gold} | {success:False, reason}

    # ── Estatísticas do personagem (Fase E, 23/07/2026) ────────────
    # Request/response sob demanda (não um canal contínuo tipo STATS_UPDATE):
    # CharStatsTracker só muda em eventos raros (dano/kill/duelo/arena) e o
    # modal só é aberto ocasionalmente — ver engine/components.py::CharStatsTracker.
    CHAR_STATS_REQUEST = "char_stats_request"  # C→S  {} — abrir modal de estatísticas
    CHAR_STATS_DATA    = "char_stats_data"     # S→C  privado, snapshot atual (ver CharStatsTracker)

    # ── Inventário / Loot ─────────────────────────────────────────
    INVENTORY_UPDATE   = "inv_update"      # S→C  {items: [{name, icon_key, item_type, rarity, value, slot, stack}, ...]} — item(ns) concedido(s) fora do fluxo normal de loot (23/07/2026: recompensa de item de quest, ver server/session.py::_handle_quest_turn_in). Cliente reconstrói e adiciona ao Inventory local, mesmo mecanismo de LOOT_RESULT.
    SKILL_GRANTED      = "skill_granted"   # S→C  {skill_id: str, name: str} — skill concedida fora do fluxo normal de treinador (25/07/2026: recompensa de skill de quest, ver server/session.py::_handle_quest_turn_in). Diferente de INVENTORY_UPDATE: o servidor JÁ grava em PlayerSkills.learned_skill_ids na hora (skill tem gate de autorização server-side, is_skill_authorized — não dá pra confiar só no cliente materializar depois via sync). Esta mensagem só avisa o cliente pra materializar o mesmo localmente (hotbar).
    LOOT_AVAILABLE     = "loot_available"  # S→C  corpo com loot apareceu no tile
    LOOT_REQUEST       = "loot_request"    # C→S  player clicou no corpo para sacar
    LOOT_TAKE          = "loot_take"       # C→S  pegar item específico do corpo
    LOOT_RESULT        = "loot_result"     # S→C  itens obtidos (ou vazio se não for dono)
    LOOT_UPDATE        = "loot_update"     # S→C  outro membro do grupo sacou algo — sincroniza a cópia LOCAL (sem creditar)

    # ── Chat ──────────────────────────────────────────────────────
    CHAT_SEND          = "chat_send"       # C→S  enviar mensagem
    CHAT_MESSAGE       = "chat_message"    # S→C  mensagem recebida

    # ── Save ──────────────────────────────────────────────────────
    SAVE_STATE         = "save_state"      # C→S  cliente envia estado completo para salvar

    # ── Utilidade ─────────────────────────────────────────────────
    UNSTUCK            = "unstuck"         # C→S  teleporta player para o spawn (cooldown 60s)

    # ── Cadastro / Personagem ─────────────────────────────────────
    REGISTER           = "register"        # C→S  {username, password}
    REGISTER_OK        = "register_ok"     # S→C  {}
    REGISTER_ERROR     = "register_error"  # S→C  {reason}
    AUTH_OK            = "auth_ok"         # S→C  {characters:[...]} login ok, escolher char
    SELECT_CHARACTER   = "select_char"     # C→S  {char_id: int}
    CREATE_CHARACTER   = "create_character"# C→S  {name, class_id}
    CHARACTER_CREATED  = "char_created"    # S→C  {}  (precede LOGIN_OK)
    CHARACTER_ERROR    = "char_error"      # S→C  {reason} — reason: invalid_name_format |
                                            #   name_taken | limit_reached | creation_failed | ...
    DELETE_CHARACTER   = "delete_char"     # C→S  {char_id: int}
    DELETE_CHARACTER_OK= "delete_char_ok"  # S→C  {}
    SUGGEST_NAME       = "suggest_name"    # C→S  {}  pede um nome de fantasia sugerido
    NAME_SUGGESTION    = "name_suggestion" # S→C  {name: str} já verificado único no banco

    # ── Sistema ───────────────────────────────────────────────────
    PING               = "ping"            # C→S  latência
    PONG               = "pong"            # S→C  resposta de latência
    ERROR              = "error"           # S→C  erro genérico


# ── Helpers de serialização ───────────────────────────────────────────────────

def encode(msg_type: MsgType, payload: dict,
           seq: int = 0, ts: int | None = None) -> str:
    """Serializa uma mensagem para string JSON pronta para envio."""
    return json.dumps({
        "type": msg_type.value,
        "p":    payload,
        "seq":  seq,
        "ts":   ts if ts is not None else _now_ms(),
    }, separators=(",", ":"))


def decode(raw: str) -> tuple[MsgType, dict, int, int]:
    """
    Desserializa uma mensagem JSON.
    Retorna (MsgType, payload, seq, ts).
    Lança ValueError em caso de formato inválido.
    """
    try:
        data = json.loads(raw)
        msg_type = MsgType(data["type"])
        return msg_type, data.get("p", {}), data.get("seq", 0), data.get("ts", 0)
    except (KeyError, ValueError) as e:
        raise ValueError(f"Mensagem inválida: {e}  raw={raw[:120]}")


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── Validação de payload na borda C→S ────────────────────────────────────────
# Item B4 da auditoria (PROBLEMAS_ARQUITETURA.md §11): payloads eram dicts
# livres — typo/tipo errado virava bug silencioso dentro do handler
# (`payload.get()` com default engole tudo). Este é o passo RUNTIME do plano
# (schema formal TypedDict completo continua no plano; aqui é a proteção que
# roda de verdade): campos que o handler ASSUME existirem/serem daquele tipo
# são checados ANTES do dispatch (SessionManager.on_message) — mensagem
# malformada leva ERROR de volta e nunca chega ao handler.
#
# Regras de projeto deste schema:
# - PERMISSIVO de propósito: só os campos NÚCLEO de cada mensagem — campos
#   opcionais NÃO entram (senão todo campo novo vira mensagem rejeitada).
# - Numérico é sempre (int, float) — nunca rejeitar um int onde cabe float.
#   bool passa como int (subclasse) — inofensivo, os handlers fazem int().
# - Mensagem sem entrada aqui = sem validação extra (compat por default;
#   adicionar a entrada JUNTO da mensagem nova é o ideal, não obrigatório).
_NUM = (int, float)
C2S_REQUIRED: dict = {
    MsgType.LOGIN:              {"username": str, "password": str},
    MsgType.REGISTER:           {"username": str, "password": str},
    MsgType.SELECT_CHARACTER:   {"char_id": _NUM},
    MsgType.CREATE_CHARACTER:   {"name": str, "class_id": str},
    MsgType.MOVE:               {"tx": _NUM, "ty": _NUM},
    MsgType.CAST_SKILL:         {"sid": str},
    MsgType.CANCEL_CAST:        {"sid": str},
    MsgType.CAST_DIR_UPDATE:    {"sid": str, "dir_x": _NUM, "dir_y": _NUM},
    MsgType.PROJECTILE_HIT_CS:  {"spell_id": str, "target_id": _NUM},
    MsgType.CONSUMABLE_USE:     {"item_name": str},
    MsgType.GOLD_UPDATE:        {"gold": _NUM},
    MsgType.INV_SYNC:           {"inventory": list},
    MsgType.EQUIP_SYNC:         {"equipment": dict},
    # Shape real do cliente (client/save_sync_handlers.py::_send_talent_update):
    # tudo aninhado em "talents" — {"talents": {chosen_build, allocated,
    # available_points}}. O comentário antigo do MsgType ("{allocated, ...}")
    # estava desatualizado e levou o primeiro schema a exigir "allocated" no
    # topo — warning real em teste do usuário 15/07/2026.
    MsgType.TALENT_UPDATE:      {"talents": dict},
    MsgType.HOTBAR_UPDATE:      {"skills": list},
    MsgType.BUY_REQUEST:        {"shop_id": str, "item_name": str},
    MsgType.SELL_REQUEST:       {"item_name": str},
    MsgType.QUEST_ACCEPT:       {"quest_id": str},
    MsgType.QUEST_TURN_IN:      {"quest_id": str},
    MsgType.CHAT_SEND:          {"text": str},
    MsgType.TRADE_REQUEST:      {"target_eid": _NUM},
    MsgType.TRADE_OFFER_ITEM:   {"inv_index": _NUM},
    MsgType.TRADE_WITHDRAW_ITEM:{"offer_slot": _NUM},
    MsgType.TRADE_SET_GOLD:     {"amount": _NUM},
    # Duelo — schema validado contra o send REAL do cliente
    # (client/trade_handlers.py::_send_duel_request / duel_handlers.py):
    MsgType.DUEL_REQUEST:       {"target_eid": _NUM},
    # Party — schema validado contra o send REAL do cliente
    # (client/trade_handlers.py::_send_party_invite_request /
    # client/party_handlers.py::_try_handle_party_chat_command):
    MsgType.PARTY_INVITE:       {"target_eid": _NUM},
    MsgType.PARTY_KICK:         {"target_eid": _NUM},
    MsgType.SAVE_STATE:         {},   # payload inteiro é dict validado a fundo no handler
    MsgType.CHAR_STATS_REQUEST: {},
}


def validate_c2s(msg_type: "MsgType", payload) -> "str | None":
    """None = ok; senão string curta com o motivo (vai no ERROR pro cliente).
    Só valida mensagens com entrada em C2S_REQUIRED — ver regras acima."""
    schema = C2S_REQUIRED.get(msg_type)
    if schema is None:
        return None
    if not isinstance(payload, dict):
        return "payload_not_dict"
    for field, ftype in schema.items():
        if field not in payload:
            return f"missing_field:{field}"
        if not isinstance(payload[field], ftype):
            return f"bad_type:{field}"
    return None


# ── Definição dos payloads ────────────────────────────────────────────────────
#
# Cada seção documenta os campos obrigatórios (*) e opcionais do payload.
# O código de handler valida os campos antes de processar.
#
# Convenções de campo:
#   eid    → entity_id (int)
#   tx/ty  → tile_x / tile_y (int)
#   hp/mp  → hit points / mana points (int)
#   sid    → skill_id (str)  — chave do SKILL_CATALOG
#   tid    → target entity_id (int, -1 = sem alvo)
#
# ─────────────────────────────────────────────────────────────────────────────


# ── C→S: LOGIN ────────────────────────────────────────────────────────────────
# {
#   "username":  str *
#   "password":  str *   (hash SHA-256 no cliente, nunca texto puro)
#   "version":   int *   (PROTOCOL_VERSION — servidor rejeita versões incompatíveis)
# }

# ── S→C: LOGIN_OK ─────────────────────────────────────────────────────────────
# {
#   "token":     str     (session token para reconexão)
#   "eid":       int     (entity_id do player nesta sessão)
#   "char":      dict    (CharSnapshot — veja abaixo)
#   "server_ts": int     (timestamp do servidor para sincronizar relógio)
# }

# ── S→C: LOGIN_ERROR ──────────────────────────────────────────────────────────
# {
#   "reason":    str     ("invalid_credentials" | "already_online" | "version_mismatch")
# }


# ── C→S: MOVE ─────────────────────────────────────────────────────────────────
# {
#   "tx": int *          tile destino X
#   "ty": int *          tile destino Y
# }
# Servidor valida: tile walkable? distância de 1 tile? em combate?
# Se válido → move e envia ENTITY_MOVE para todos no AOI
# Se inválido → envia ENTITY_MOVE de volta com posição corrigida (só p/ esse client)

# ── S→C: ENTITY_MOVE ─────────────────────────────────────────────────────────
# {
#   "eid":  int *
#   "tx":   int *        tile destino
#   "ty":   int *
#   "from_tx": int       tile origem (para animação de direção)
#   "from_ty": int
# }


# ── C→S: AUTO_ATTACK ─────────────────────────────────────────────────────────
# {
#   "tid":   int *       -1 = parar auto-attack
# }

# ── S→C: COMBAT_RESULT ───────────────────────────────────────────────────────
# {
#   "attacker": int      eid do atacante
#   "target":   int      eid do alvo
#   "outcome":  str      "hit" | "miss" | "crit" | "dodge" | "parry" | "block" |
#                        "evade" (mob em modo evasão/RETURNING — dano 0, sem
#                        aggro, ver ARQUITETURA_ONLINE.md)
#   "damage":   int      dano causado (0 em miss/dodge/parry/evade)
#   "hp_after": int      HP do alvo após o golpe
#   "source":   str      "auto" | skill_id (o que causou o dano)
# }


# ── C→S: CAST_SKILL ──────────────────────────────────────────────────────────
# {
#   "sid":    str *      skill_id (ex: "bola_de_fogo")
#   "tid":    int        target entity_id (-1 se AOE sem alvo específico)
#   "tx":     int        tile X alvo (para skills de posição/AoE)
#   "ty":     int        tile Y alvo
#   "dir_x":  float      direção X normalizada (para skills de cone: Pirofagia, Tiro Múltiplo)
#   "dir_y":  float      direção Y normalizada
# }
# O servidor valida: skill aprendida? cooldown zerado? recursos suficientes?

# ── S→C: CAST_START ──────────────────────────────────────────────────────────
# {
#   "eid":       int *   quem está castando
#   "sid":       str *   skill_id
#   "cast_time": float   duração do cast em segundos
# }

# ── S→C: SKILL_RESULT ────────────────────────────────────────────────────────
# {
#   "caster_eid":  int *     eid do caster
#   "sid":         str *     skill_id
#   "targets":     list      lista de CombatResult por alvo
#   "cooldown":    float     CD efetivo (com talentos)
#   "failed":      bool      True = servidor rejeitou
#   "cast_started":bool      cast com tempo foi aceito (GCD sem som/CD)
#   "is_completion":bool     cast completou (CD real, dano aplicado)
#   "is_proj_damage":bool    projétil acertou (só mostra dano — sem GCD/CD/som)
# }

# ── S→C: SKILL_EFFECT ───────────────────────────────────────────────────────
# Broadcast de apresentação (som/VFX) para todos no AOI do caster.
# Separado de SKILL_RESULT para que client nunca precise deduzir timing de som.
# {
#   "sid":        str *   skill_id (chave de SKILL_CATALOG["effects"])
#   "event":      str *   "cast_start" | "launch" | "impact" | "miss"
#   "caster_eid": int *   quem usou a skill
#   "tx":         int *   tile X do caster (posição do som)
#   "ty":         int *   tile Y do caster
#   "target_eid": int     eid do alvo (opcional, para impact em alvo específico)
# }


# ── S→C: PROJECTILE_SPAWN ────────────────────────────────────────────────────
# {
#   "pid":      int *    id único do projétil nesta sessão
#   "sid":      str *    spell_id (ex: "bola_de_fogo", "arrow")
#   "origin_x": float *  posição mundo X de origem
#   "origin_y": float *
#   "tid":      int      entity_id do alvo (-1 = posição fixa)
#   "target_x": float    posição mundo X do alvo (se tid == -1)
#   "target_y": float
#   "speed":    float *  pixels/segundo
# }
# Cliente simula o projétil localmente. PROJECTILE_HIT é a confirmação do servidor.

# ── S→C: PROJECTILE_HIT ──────────────────────────────────────────────────────
# {
#   "pid":    int *
#   "hit":    bool *     True = acertou; False = errou (miss visual)
#   "end_x":  float      posição final se miss (cliente redireciona)
#   "end_y":  float
# }


# ── S→C: EFFECT_APPLIED ──────────────────────────────────────────────────────
# {
#   "eid":       int *
#   "effect":    str *   ex: "slow" | "root" | "stun" | "burn" | "sleep"
#   "duration":  float   segundos
#   "magnitude": float   intensidade (ex: 0.5 = 50% slow)
# }

# ── S→C: STATS_UPDATE ────────────────────────────────────────────────────────
# {
#   "eid":   int *
#   "hp":    int         atual
#   "hp_max":int
#   "mp":    int
#   "mp_max":int
#   "rage":  int         (Guerreiro)
#   "conc":  int         (Arqueiro — concentration)
#   "xp":    int
#   "xp_next":int
# }
# Servidor envia apenas os campos que mudaram. Cliente faz merge.


# ── S→C: SKILL_LEVELS_UPDATE ─────────────────────────────────────────────────
# {
#   "levels":     dict[str, int]   *   snapshot completo, 1 entrada por SKILL_IDS
#   "xp":         dict[str, int]   *   snapshot completo, 1 entrada por SKILL_IDS
#   "leveled_up": list[{"skill_id": str, "level": int}]   opcional, omitido se nada levelou
# }
# Enviado SÓ ao dono (nunca broadcast AOI — progressão é privada). Dirty-check
# por tick em WorldServer._sync_player_skill_levels_dirty(): qualquer grant_skill_xp
# (cast de magia, auto-attack, skill de arco, DoT resistido) muda o componente
# SkillLevels do servidor; este snapshot mantém o painel do cliente (tecla L,
# skill_level_ui.py) atualizado sem precisar de relog. Cliente faz merge direto
# (replace, não soma) no componente SkillLevels local — nunca usado em cálculo
# de dano client-side, só exibição (ver PROBLEMAS_ARQUITETURA.md).
# "leveled_up": cliente mostra "Parabéns, você subiu o nível de sua habilidade
# com {skill} para o nível {level}." (LOG) + som de level-up — ver
# client/network_handlers.py::_handle_msg_skill_levels_update.


# ── S→C: PLAYER_DEATH ────────────────────────────────────────────────────────
# {
#   "eid":       int *   eid do player que morreu
#   "corpse_tx": int *   tile X onde o corpo ficou
#   "corpse_ty": int *   tile Y onde o corpo ficou
# }
# Enviado APENAS ao dono. Corpo fica visível no AOI normalmente (current_hp==0).
# Cliente: marca GhostState.is_dead=True, inicia timer de 2s p/ modal "Você morreu".

# ── S→C: ENTITY_DEATH ────────────────────────────────────────────────────────
# {
#   "eid": int *         eid da entidade que morreu
#   "tx":  int *         tile X onde morreu (posição do corpo)
#   "ty":  int *         tile Y onde morreu
# }
# Broadcast para AOI (outros players veem o corpo/animação de morte).

# ── C→S: RELEASE_SPIRIT ──────────────────────────────────────────────────────
# {} — player com GhostState.is_dead=True clicou "Liberar espírito".
# Servidor teleporta o player (ghost, intangível, invisível) para o cemitério.

# ── C→S: REVIVE_REQUEST ──────────────────────────────────────────────────────
# {} — ghost dentro do raio do corpo (GHOST_CORPSE_RADIUS_TILES) clicou "Sim".
# Servidor revalida distância e revive com GHOST_CORPSE_REVIVE_HP_FRAC no corpo.

# ── S→C: PLAYER_REVIVE ───────────────────────────────────────────────────────
# {
#   "tx":       int *    tile X de destino (cemitério ou corpo)
#   "ty":       int *    tile Y de destino
#   "hp":       int *
#   "hp_max":   int *
#   "mana":     int *
#   "max_mana": int *
# }
# Enviado APENAS ao dono. Cliente restaura HP/mana, teleporta, limpa GhostState.

# ── S→C: GHOST_STATE ──────────────────────────────────────────────────────────
# {
#   "is_ghost":        bool *
#   "near_corpse":     bool *   true = mostra prompt "Reviver agora?"
#   "graveyard_timer": float    segundos contínuos dentro do raio do cemitério
# }
# Enviado APENAS ao dono, quando near_corpse muda (ou periodicamente p/ resync).


# ── C→S: QUEST_ACCEPT ────────────────────────────────────────────────────────
# {
#   "quest_id": str *
# }
# Servidor valida pré-requisitos/nível com seu PRÓPRIO QuestLog/CharacterStats
# (nunca confia em progresso reportado pelo cliente). Responde com QUEST_UPDATE
# (sucesso ou não — cliente só reflete o que o servidor confirma).

# ── C→S: QUEST_TURN_IN ───────────────────────────────────────────────────────
# {
#   "quest_id": str *
# }
# Servidor valida que todos os objetivos estão completos no QuestLog dele.
# Se válido: concede XP (via canal já usado por STATS_UPDATE/process_levelups),
# gold (Wallet.gold direto), remove itens de quest do Inventory, e responde
# QUEST_UPDATE com completed_qid setado (cliente mostra LOG/PROC/som locais).

# ── S→C: QUEST_UPDATE ────────────────────────────────────────────────────────
# {
#   "active":       dict   {quest_id: [progresso_por_objetivo, ...]}
#   "completed":    list   [quest_id, ...]
#   "completed_qid": str   setado só na entrega bem-sucedida de uma quest —
#                          sinaliza o cliente a mostrar o feedback de "completa"
# }
# Enviado APENAS ao dono (nunca AOI — progresso de quest é dado privado).
# Snapshot completo, não incremental — cliente substitui QuestLog.active/
# .completed inteiro (mesmo padrão de SKILL_LEVELS_UPDATE).

# ── S→C: ENTITY_SPAWN ────────────────────────────────────────────────────────
# {
#   "eid":       int *
#   "kind":      str *   "player" | "enemy" | "npc" | "corpse" | "player_corpse"
#                        ("player_corpse": eid sintético 3_000_000+player_eid,
#                         marcador visual do corpo após liberar o espírito)
#   "tx":        int *   tile X atual
#   "ty":        int *   tile Y atual
#   "name":      str
#   "class_id":  str     "guerreiro" | "mago" | "arqueiro"  (se player)
#   "mob_id":    str     id da definição do mob (se enemy)
#   "hp":        int
#   "hp_max":    int
#   "level":     int
#   "effects":   list    efeitos de status ativos  [{effect, duration, magnitude}]
#   "is_visible":bool    False = está invisível (só enviado para o próprio player)
# }

# ── S→C: AOI_UPDATE ──────────────────────────────────────────────────────────
# {
#   "spawned":   list    lista de EntitySpawn (entidades que entraram no AOI)
#   "despawned": list    lista de eids que saíram do AOI
#   "moved":     list    lista de EntityMove (entidades que se moveram)
#   "stats":     list    lista de StatsUpdate (HP/MP que mudaram)
#   "effects":   list    lista de EffectApplied/Removed
# }
# Este é o pacote mais frequente — enviado todo tick com apenas os deltas.
# Se não houver mudanças, o servidor pode omitir o pacote (otimização).


# ── S→C: CHAT_MESSAGE ────────────────────────────────────────────────────────
# {
#   "sender":  str *     nome do remetente
#   "text":    str *
#   "channel": str *     "world" | "local" | "party" | "system"
#   "color":   list      [R, G, B]
# }

# ── S→C: LOOT_AVAILABLE ──────────────────────────────────────────────────────
# {
#   "corpse_id": int *   id do corpse no servidor
#   "tx":        int *   tile X do corpse
#   "ty":        int *   tile Y do corpse
#   "items":     list *  [{name, icon_key, item_type, rarity, value, slot, stack}]
#   "coins":     int *   moedas no corpo
# }
# Enviado ao dono do loot (first-attacker do mob) E, se ele estiver em
# grupo, a TODO o grupo (free-for-all — ver server/loot_processor.py).
# Fora do dono/grupo: ENTITY_SPAWN {kind:"corpse"} apenas (sem itens).

# ── C→S: LOOT_REQUEST ────────────────────────────────────────────────────────
# {
#   "corpse_id": int *   id do corpse clicado
#   "take":      str     "gold" | "item" | "all" (default "all")
#   "item_name": str     nome do item — obrigatório se take=="item" (pega o
#                        PRIMEIRO item da lista atual com esse nome; nunca
#                        índice cru, que quebraria se outro membro do grupo
#                        já tivesse tirado algo antes e deslocado a lista)
# }
# Servidor valida ownership/grupo (WorldServer.request_loot). Fora do
# dono/grupo: silenciosamente ignora. `take` granular desde 17/07/2026 —
# sacar só o ouro não deveria levar junto o resto do loot.

# ── S→C: LOOT_RESULT ─────────────────────────────────────────────────────────
# {
#   "corpse_id": int *   id do corpse
#   "items":     list *  itens obtidos [{name, icon_key, item_type, rarity, value, slot, stack}]
#   "coins":     int *   moedas obtidas (0 se o pedido era só "item")
# }
# Vazio (items=[], coins=0) = já não tinha mais nada pra pegar (outro
# membro do grupo já levou). Corpo só recebe ENTITY_DESPAWN quando fica
# REALMENTE vazio — sacar parcial mantém o resto visível/lootável.
# Enviado APENAS se o player for o dono e houver itens para pegar.

# ── S→C: LOOT_UPDATE ─────────────────────────────────────────────────────────
# {
#   "corpse_id":         int *
#   "coins_taken":       int *  > 0 se ALGUÉM (outro membro do grupo) sacou ouro
#   "item_names_taken":  list * nomes dos itens que ALGUÉM sacou
# }
# Enviado a TODO o grupo do dono do corpse EXCETO quem fez o LOOT_REQUEST
# (esse já sabe via LOOT_RESULT) sempre que um saque bem-sucedido
# acontece (17/07/2026 — bug real: sem isso, quem não clicou continuava
# vendo ouro/item já pego por outro membro do grupo — "ouro fantasma",
# clicar nele voltava vazio e o corpo sumia sem nunca ter dado nada).
# Cliente só SINCRONIZA a cópia local (zera coins/remove item por nome)
# — NUNCA credita Wallet/Inventory aqui (quem recebe isso não pegou
# nada, só está sendo avisado que sumiu).

# ── C→S: EQUIP_SYNC ──────────────────────────────────────────────────────────
# {
#   "equipment": dict *   {slot: item_dict}  — snapshot completo do Equipment
#                         local (slots ausentes = desequipados)
# }
# Servidor reconstrói cada item via catálogo (loot/loja/forja, por nome) e
# valida por slot: armor_class contra stats_system.CLASS_ARMOR_ALLOWED[classe]
# e level_requirement contra CharacterStats.level. Slot que falha NÃO é
# aplicado (mantém o que já estava equipado) — servidor responde
# EQUIP_REJECTED pra esse slot; slots válidos são aplicados normalmente
# (sem confirmação — EQUIP_SYNC nunca falha silenciosamente sem aviso, mas
# sucesso também não gera reply, só o AOI_UPDATE natural refletindo o estado).

# ── S→C: EQUIP_REJECTED ──────────────────────────────────────────────────────
# {
#   "slot":      str *   slot recusado (ex: "chest")
#   "item_name": str *   nome do item que não pôde ser equipado
#   "reason":    str *   "class" (armor_class incompatível) | "level" (level_requirement)
# }
# Enviado APENAS ao dono, um por slot recusado. Cliente reverte o slot local
# pro estado anterior e mostra aviso via combat_log.

# ── C→S: TRADE_REQUEST ───────────────────────────────────────────────────────
# {
#   "target_eid": int *   eid do player remoto que o requester quer negociar
# }
# Servidor valida: target existe/online, não é o próprio requester, distância
# ≤ TRADE_MAX_DIST_TILES, nenhum dos dois já em trade ou convite pendente. Se
# inválido, responde ao requester com TRADE_CANCELLED{trade_id:-1, reason}
# sem gerar TRADE_INVITE. Se válido, manda TRADE_INVITE só pro target.

# ── S→C: TRADE_INVITE ────────────────────────────────────────────────────────
# {
#   "from_eid":  int *   eid de quem pediu o trade
#   "from_name": str *   nome de exibição de quem pediu
# }
# Enviado APENAS ao target. Cliente mostra modal "Fulano quer negociar —
# Aceitar/Recusar".

# ── C→S: TRADE_ACCEPT / TRADE_DECLINE ────────────────────────────────────────
# {} — sem payload. Resposta do target a um convite pendente. Aceitar cria a
# TradeSession no servidor e dispara TRADE_OPEN pros dois lados; recusar só
# limpa o convite pendente e manda TRADE_CANCELLED{reason:"declined"} pro
# requester original.

# ── S→C: TRADE_OPEN ──────────────────────────────────────────────────────────
# {
#   "trade_id":  int *   id da sessão de trade recém-criada
#   "other_eid": int *   eid do OUTRO participante (não o destinatário)
#   "other_name":str *   nome de exibição do outro participante
# }
# Enviado pros DOIS lados (payload com "other_*" já resolvido por sessão —
# cada cliente só enxerga o lado oposto). Cliente abre a janela de trade
# zerada (ofertas/gold/confirmação limpos).

# ── C→S: TRADE_OFFER_ITEM ────────────────────────────────────────────────────
# {
#   "inv_index": int *   índice do item na Inventory do próprio player
# }
# Servidor remove o item da Inventory real e adiciona no lado do ofertante
# na TradeSession (máx 5 slots), reseta confirmed dos dois lados, responde
# TRADE_STATE pros dois. Índice inválido/sessão inativa/já 5 itens: ignorado
# silenciosamente (não há reason dedicado — não deveria ocorrer com client
# correto).

# ── C→S: TRADE_WITHDRAW_ITEM ─────────────────────────────────────────────────
# {
#   "offer_slot": int *   índice dentro da PRÓPRIA oferta (não da Inventory)
# }
# Inverso do OFFER_ITEM — devolve o item pra Inventory real do ofertante
# (valida espaço antes), reseta confirmed dos dois lados, responde
# TRADE_STATE pros dois.

# ── C→S: TRADE_SET_GOLD ──────────────────────────────────────────────────────
# {
#   "amount": int *   gold total que o próprio player quer ofertar (>= 0)
# }
# Substitui (não soma) o valor ofertado; servidor valida contra
# wallet.gold + valor já em custódia desse lado, debita/credita a diferença,
# reseta confirmed dos dois lados, responde TRADE_STATE pros dois.

# ── S→C: TRADE_STATE ─────────────────────────────────────────────────────────
# {
#   "trade_id":        int *
#   "my_offer":        list *  itens que EU ofertei [{name, icon_key, item_type, rarity, value, slot}]
#   "my_gold":         int *
#   "their_offer":     list *  itens que o OUTRO ofertou (mesmo formato)
#   "their_gold":      int *
#   "my_confirmed":    bool *
#   "their_confirmed": bool *
# }
# Enviado individualmente pros DOIS lados (cada um recebe sua própria versão
# com "my"/"their" já resolvido — nunca broadcast bruto) toda vez que a
# oferta muda (item ofertado/retirado, gold alterado, confirmação). É o
# ÚNICO ponto em que o cliente remove o item ofertado da Inventory local de
# verdade — nunca otimisticamente antes disso (mesmo racional do
# EQUIP_REJECTED: servidor confirma primeiro, cliente reflete depois).

# ── C→S: TRADE_CONFIRM ───────────────────────────────────────────────────────
# {} — sem payload. Seta a flag de confirmação do próprio lado. Se os dois
# lados já confirmados, servidor executa a troca (valida espaço de Inventory
# nos dois lados ANTES de mexer em qualquer coisa — falha vira
# TRADE_CANCELLED{reason:"inventory_full"} sem perder nada de ninguém) e
# responde TRADE_RESULT pros dois; senão só reenvia TRADE_STATE.

# ── S→C: TRADE_RESULT ────────────────────────────────────────────────────────
# {
#   "trade_id":        int *
#   "received_items":  list *  itens que ESTE player recebeu (formato de item)
#   "received_gold":   int *   gold que este player recebeu
# }
# Enviado pros DOIS lados (cada um com o que É seu) quando a troca é
# executada com sucesso. Cliente soma os itens/gold na Inventory/Wallet
# local e fecha a janela de trade.

# ── C→S: TRADE_CANCEL ────────────────────────────────────────────────────────
# {} — sem payload. Qualquer um dos dois lados pode cancelar a qualquer
# momento enquanto a sessão está aberta (inclusive antes de qualquer oferta).
# Servidor devolve itens/gold em custódia dos DOIS lados e responde
# TRADE_CANCELLED{reason:"cancelled"} pros dois.

# ── S→C: TRADE_CANCELLED ─────────────────────────────────────────────────────
# {
#   "trade_id": int *   -1 se o trade nunca chegou a abrir (ex: TRADE_REQUEST inválido)
#   "reason":   str *   "declined" | "cancelled" | "distance" | "disconnect" |
#                       "inventory_full" | "invalid"
# }
# Cliente devolve pra Inventory/Wallet local qualquer item/gold que estava em
# my_offer/my_gold (o servidor já devolveu de verdade — isso só sincroniza a
# cópia local), fecha a janela de trade e mostra o motivo no combat_log.

# ── C→S: PING / S→C: PONG ────────────────────────────────────────────────────
# PING: { "client_ts": int }
# PONG: { "client_ts": int, "server_ts": int }
# Cliente calcula RTT = now() - client_ts
# Cliente estima offset de relógio = server_ts - (client_ts + RTT/2)


# ── C→S: CHAR_STATS_REQUEST ──────────────────────────────────────────────────
# {} — jogador abriu o modal de estatísticas do personagem.

# ── S→C: CHAR_STATS_DATA ──────────────────────────────────────────────────────
# {
#   "pve_damage":     int *
#   "pvp_damage":     int *
#   "mobs_killed":    int *
#   "players_killed": int *
#   "duel_wins":      int *
#   "duel_losses":    int *
#   "arena_wins":     dict *   {"1v1": int, "2v2": int, "3v3": int}
#   "arena_losses":   dict *   {"1v1": int, "2v2": int, "3v3": int}
#   "quests_completed": int *  deriva de len(QuestLog.completed), sem campo próprio
# }
# Enviado APENAS ao dono, em resposta a CHAR_STATS_REQUEST (ver
# engine/components.py::CharStatsTracker — fonte única dos 8 primeiros campos).

# ── Helpers de construção de payloads ────────────────────────────────────────
# Funções factory para os payloads mais complexos.
# NOTE: ainda não usadas pelos callers — servidor constrói dicts inline.
# Reservadas para quando o cliente for implementado e precisar de formato canônico.

def make_entity_spawn(eid: int, kind: str, tx: int, ty: int, **kwargs) -> dict:
    d = {"eid": eid, "kind": kind, "tx": tx, "ty": ty}
    d.update(kwargs)
    return d

def make_combat_result(attacker: int, target: int, outcome: str,
                       damage: int, hp_after: int, source: str = "auto") -> dict:
    return {
        "attacker": attacker, "target": target, "outcome": outcome,
        "damage": damage, "hp_after": hp_after, "source": source,
    }

def make_stats_update(eid: int, **fields) -> dict:
    return {"eid": eid, **fields}

def make_aoi_update(spawned=None, despawned=None, moved=None,
                    stats=None, effects=None) -> dict:
    d: dict = {}
    if spawned:   d["spawned"]   = spawned
    if despawned: d["despawned"] = despawned
    if moved:     d["moved"]     = moved
    if stats:     d["stats"]     = stats
    if effects:   d["effects"]   = effects
    return d

def make_projectile_spawn(pid: int, sid: str, origin_x: float, origin_y: float,
                          speed: float, tid: int = -1,
                          target_x: float = 0, target_y: float = 0) -> dict:
    return {
        "pid": pid, "sid": sid,
        "origin_x": origin_x, "origin_y": origin_y,
        "tid": tid, "target_x": target_x, "target_y": target_y,
        "speed": speed,
    }
