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
    ZONE_CHANGE        = "zone_change"     # S→C  jogador mudou de zona/instância  # TODO: não implementado
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
    PROJECTILE_HIT_CS  = "proj_hit_cs"    # C→S  projétil do player colidiu com o alvo
    CAST_START         = "cast_start"      # S→C  entidade começou cast (barra de cast)  # TODO: não implementado
    CAST_CANCEL        = "cast_cancel"     # S→C  cast interrompido                      # TODO: não implementado
    CAST_COMPLETE      = "cast_complete"   # S→C  cast concluído (dispara efeito)         # TODO: não implementado
    SKILL_RESULT       = "skill_result"    # S→C  efeitos aplicados pela skill

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
    PLAYER_DEATH       = "player_death"    # S→C  player morreu — respawn_tx, respawn_ty, hp_max
    PLAYER_STATS_SYNC  = "player_stats_sync"  # S→C  sincroniza HP autoritativo do player

    # ── Entidades ─────────────────────────────────────────────────
    ENTITY_SPAWN       = "entity_spawn"    # S→C  entidade entrou no AOI (detalhes completos)
    ENTITY_DESPAWN     = "entity_despawn"  # S→C  entidade saiu do AOI ou morreu
    ENTITY_DEATH       = "entity_death"    # S→C  morte com animação (antes de despawn)
    SOUND_EVENT        = "sound_event"     # S→C  evento sonoro posicional (aggro, etc.)
    PLAYER_STAT_SYNC   = "player_stat_sync"  # C→S  stats efetivos do player (equip/buff/consumível)
    CONSUMABLE_USE     = "consumable_use"    # C→S  uso de consumível (heal_instant, HoT, buffs futuros)
    GOLD_UPDATE        = "gold_update"       # C→S  gold mudou (loot de moedas) {gold: N}
    INVENTORY_UPDATE   = "inventory_update"  # C→S  inventário mudou (loot de item) {inventory: [...]}
    TALENT_UPDATE      = "talent_update"     # C→S  talento alocado/desalocado {allocated, available_points}
    HOTBAR_UPDATE      = "hotbar_update"     # C→S  barra de ações/consumíveis mudou {skills, consumables}
    BUY_REQUEST        = "buy_request"       # C→S  compra em loja {shop_id, item_name, quantity}
    BUY_RESULT         = "buy_result"        # S→C  resultado da compra {success, reason, item, new_gold}
    SELL_REQUEST       = "sell_request"      # C→S  {item_name, item_value, stack_sold}
    SELL_RESULT        = "sell_result"       # S→C  {success, item_name, sell_price, new_gold} | {success:False, reason}

    # ── Inventário / Loot ─────────────────────────────────────────
    INVENTORY_UPDATE   = "inv_update"      # S→C  item adicionado/removido/modificado
    LOOT_AVAILABLE     = "loot_available"  # S→C  corpo com loot apareceu no tile
    LOOT_REQUEST       = "loot_request"    # C→S  player clicou no corpo para sacar
    LOOT_TAKE          = "loot_take"       # C→S  pegar item específico do corpo
    LOOT_RESULT        = "loot_result"     # S→C  itens obtidos (ou vazio se não for dono)

    # ── Chat ──────────────────────────────────────────────────────
    CHAT_SEND          = "chat_send"       # C→S  enviar mensagem
    CHAT_MESSAGE       = "chat_message"    # S→C  mensagem recebida

    # ── Save ──────────────────────────────────────────────────────
    SAVE_STATE         = "save_state"      # C→S  cliente envia estado completo para salvar

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
#   "outcome":  str      "hit" | "miss" | "crit" | "dodge" | "parry" | "block"
#   "damage":   int      dano causado (0 em miss/dodge/parry)
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
#   "caster":  int *     eid do caster
#   "sid":     str *     skill_id
#   "targets": list      lista de CombatResult por alvo (mesmo formato de COMBAT_RESULT)
#   "aoe_tx":  int       tile X do centro do AOE (se aplicável)
#   "aoe_ty":  int
#   "effects": list      lista de EffectApplied
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


# ── S→C: ENTITY_SPAWN ────────────────────────────────────────────────────────
# {
#   "eid":       int *
#   "kind":      str *   "player" | "enemy" | "npc" | "corpse"
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
#   "items":     list *  [{name, icon_key, item_type, rarity, value, slot}]
# }
# Enviado APENAS ao dono do loot (first-attacker do mob).
# Outros players recebem ENTITY_SPAWN {kind:"corpse"} apenas (sem itens).

# ── C→S: LOOT_REQUEST ────────────────────────────────────────────────────────
# {
#   "corpse_id": int *   id do corpse clicado
# }
# Servidor valida ownership. Se não for dono: silenciosamente ignora.

# ── S→C: LOOT_RESULT ─────────────────────────────────────────────────────────
# {
#   "corpse_id": int *   id do corpse
#   "items":     list *  itens obtidos [{name, icon_key, item_type, rarity, value, slot}]
# }
# Enviado APENAS se o player for o dono e houver itens para pegar.

# ── C→S: PING / S→C: PONG ────────────────────────────────────────────────────
# PING: { "client_ts": int }
# PONG: { "client_ts": int, "server_ts": int }
# Cliente calcula RTT = now() - client_ts
# Cliente estima offset de relógio = server_ts - (client_ts + RTT/2)


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
