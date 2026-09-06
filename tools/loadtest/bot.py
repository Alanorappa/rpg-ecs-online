"""
bot.py — `LoadBot`: 1 conexão WebSocket real, 1 máquina de estado,
comportamento variado (andar, lutar contra mob ou outro bot, usar
skill) — ver plano/PROBLEMAS_ARQUITETURA.md pro desenho completo
(04/09/2026).

Nunca mira no jogador real: `is_attackable()` só considera `kind==
"enemy"` de facção hostil/neutra, OU `kind=="player"` cujo eid esteja
no registro compartilhado `bot_eids` (nunca contém o eid do jogador
real, que nunca se anuncia como bot)."""
from __future__ import annotations

import asyncio
import hashlib
import random
import time

from content.skill_config import SKILL_CATALOG
from server.spell_completion_processor import (
    _BOT_PROJECTILE_SPEED_PX_S as _PROJECTILE_SPEED_PX_S,
    _BOT_PROJECTILE_DEFAULT_SPEED_PX_S as _PROJECTILE_DEFAULT_SPEED_PX_S,
)
from shared.constants import PROTOCOL_VERSION, TILE_SIZE
from shared.messages import MsgType
from tools.loadtest import map_data
from tools.loadtest.identity import (
    FRIENDLY_NPC_FACTIONS, bot_char_name, bot_class, bot_password,
    bot_username, starter_skills_for_class,
)
from tools.loadtest.protocol_client import ProtocolClient

# Skills com projétil que só aplicam dano ao receber PROJECTILE_HIT_CS do
# cliente (achado 06/09/2026, ver PROBLEMAS_ARQUITETURA.md §115) — sem o
# bot mandar essa confirmação, o cast completa (mana/cooldown consumidos,
# SKILL_RESULT chega) mas o servidor fica pra sempre esperando o "impacto
# visual" que só um cliente de verdade manda. `_schedule_bot_projectile_
# autohit` (server/spell_completion_processor.py) já resolve isso pros
# bots de BG (`BotControlled`), mas nosso bot de load-test é um player de
# rede de verdade (sem esse componente) — precisa mandar a confirmação
# ele mesmo, igual o client real (`game.py`, `_player_proj_system.
# pending_proj_hits`).
_PROJECTILE_SPELLS = {"bola_de_fogo", "flecha_reiterada", "picada_escorpiao", "tiro_repulsivo"}

_ADJ = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))

# Estilo de combate: mantém AUTO_ATTACK como ação de base (é o que gera
# raiva pro guerreiro — golpe_poderoso não sai sem isso) e só CAMADA
# CAST_SKILL por cima, nunca troca um pelo outro.
_SKILL_CHANCE_PER_TICK = 0.35
_FAILED_SKILL_BACKOFF_S = 2.0
_AGGRO_RADIUS_TILES = 6
_WANDER_ANCHOR_DRIFT_MAX = 10   # tiles — além disso, wander tende a voltar

# Comportamentos "de excursão" (05/09/2026, ver PROBLEMAS_ARQUITETURA.md
# §115) — chance por think ocioso (sem alvo de combate nem excursão já
# em andamento) de iniciar cada um. Soma bem menor que 1.0 de propósito:
# maioria das decisões continua sendo o wander curto de sempre, senão
# TODO bot vira uma excursão de pathfinding o tempo todo (não é
# realista, e gera mais custo no PRÓPRIO processo do swarm do que
# necessário pro teste).
_NOTICE_RADIUS_TILES = 30       # "vejo um mob ali longe, vou até lá"
_BUSH_EXCURSION_CHANCE = 0.05
_POI_EXCURSION_CHANCE = 0.05
_GOTO_STUCK_TIMEOUT_S = 8.0     # sem avançar no caminho por tanto tempo = desiste
_LOOT_TAKE_DELAY_S = (0.3, 0.9)  # jitter de "reação" antes de saquear (não instantâneo)


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LoadBot:
    def __init__(self, index: int, role: str, host: str, port: int, seed: int,
                bot_eids: set, think_min_s: float, think_max_s: float,
                requeue: bool, stats: dict):
        self.index = index
        self.role = role  # "world" | "arena" | "bg"
        self.host = host
        self.port = port
        self.rng = random.Random(seed + index)
        self.bot_eids = bot_eids
        self.think_min_s = think_min_s
        self.think_max_s = think_max_s
        self.requeue = requeue
        self.stats = stats  # dict compartilhado, só pra contadores/log (ver swarm.py)

        self.class_id = bot_class(index)
        self.learned_skills = starter_skills_for_class(self.class_id)
        self.username = bot_username(index)
        self.password = _hash_password(bot_password(index))
        self.char_name = bot_char_name(index)

        self.client = ProtocolClient(host, port)
        self.eid = -1
        self.tx = 0
        self.ty = 0
        self.anchor: "tuple[int, int] | None" = None
        self.state = "LOGGING_IN"
        self.target_eid = -1
        self.known_entities: dict = {}          # eid -> {tx, ty, kind, faction, hp, ...}
        self.skill_ready_at: dict = {}           # sid -> monotonic time
        self._blocked_dirs: dict = {}            # (dx,dy) -> expire monotonic time
        self._stop = asyncio.Event()
        self._reconnect_attempt = 0

        # Loot (05/09/2026, ver PROBLEMAS_ARQUITETURA.md §115) — fila de
        # corpses avisados por LOOT_AVAILABLE, drenada com jitter no think
        # loop (nunca instantâneo no recv, pra não sincronizar todo bot
        # saqueando no MESMO tick de rede).
        self._pending_loot: list = []

        # Excursão de longa distância (bush real / ponto de interesse /
        # mob visto de longe) — só UMA ativa por vez; `_goto_path` é a
        # lista de tiles (calculada 1x via A* real, offloaded pra thread
        # pra nunca bloquear o loop do PRÓPRIO swarm) que o bot segue
        # tile a tile, igual o client de verdade consumiria `auto_move.path`.
        self._goto_path: "list | None" = None
        self._goto_index = 0
        self._goto_started_at = 0.0
        self._goto_last_progress_at = 0.0
        self._goto_last_pos: "tuple | None" = None
        self._goto_pending = False  # True enquanto o A* roda na thread — evita disparar 2x

    # ── ciclo de vida ────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._login_cycle()
                self._reconnect_attempt = 0
                recv_task = asyncio.create_task(self._recv_loop())
                think_task = asyncio.create_task(self._think_loop())
                done, pending = await asyncio.wait(
                    {recv_task, think_task}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    exc = t.exception() if not t.cancelled() else None
                    if exc:
                        raise exc
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                # Fecha a conexão desta tentativa ANTES de reconectar — sem
                # isso (achado real 06/09/2026, ver PROBLEMAS_ARQUITETURA.md
                # §113) o WebSocket velho fica pendurado (o servidor só
                # percebe via ping_timeout=60s, `server/main.py`) enquanto o
                # REGISTER/LOGIN que ainda estava em voo nele (atrasado por
                # contenção de SQLite) continua processando. Se completar,
                # essa sessão "zumbi" fica autenticada e bloqueia CADA
                # reconexão seguinte com o MESMO username via LOGIN_ERROR
                # "already_online" (`server/session.py::_handle_login`) até
                # o ping-timeout derrubar o zumbi — cada rejeição gera outra
                # reconexão que TAMBÉM não fechava a própria conexão, criando
                # um novo zumbi e piorando a mesma contenção que causou o
                # timeout original (efeito medido: 1181 reconexões pra 250
                # bots, quase 5x). `close()` é best-effort (a conexão pode já
                # estar quebrada) — nunca deixa uma falha de close() mascarar
                # a exceção real que causou a reconexão.
                try:
                    await self.client.close()
                except Exception:
                    pass
                if self._stop.is_set():
                    break
                self.bot_eids.discard(self.eid)
                self.eid = -1
                self.state = "RECONNECTING"
                await self._reconnect_backoff(e)
        self.bot_eids.discard(self.eid)
        try:
            await self.client.close()
        except Exception:
            pass

    async def _reconnect_backoff(self, exc: Exception) -> None:
        self._reconnect_attempt += 1
        # "Full jitter" (padrão AWS/gRPC, 06/09/2026 — ver
        # PROBLEMAS_ARQUITETURA.md, achado real: 250 bots que falham juntos
        # (ex: contenção de SQLite numa rajada de conexão, §113) tentavam
        # reconectar TODOS no mesmo segundo — `2**tentativa` sem jitter é
        # determinístico, então o "rebanho" nunca se desincronizava sozinho,
        # e a próxima reconexão em massa virava uma rajada sincronizada de
        # CAST_SKILL (todo bot "pensa imediato" ao reconectar). `uniform(0, teto)`
        # em vez do teto fixo espalha as tentativas de verdade.
        _teto = min(30.0, 2 ** min(self._reconnect_attempt, 5))
        delay = self.rng.uniform(0, _teto)
        self.stats["reconnects"] = self.stats.get("reconnects", 0) + 1
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    # ── login/registro ───────────────────────────────────────────────

    async def _login_cycle(self) -> None:
        self.client = ProtocolClient(self.host, self.port)
        await self.client.connect()

        await self.client.send(MsgType.REGISTER, {
            "username": self.username, "password": self.password,
        })
        reg = await self.client.recv_one()
        if reg and reg[0] == MsgType.REGISTER_ERROR and reg[1].get("reason") != "username_taken":
            raise RuntimeError(f"REGISTER falhou: {reg[1]}")

        await self.client.send(MsgType.LOGIN, {
            "username": self.username, "password": self.password,
            "version": PROTOCOL_VERSION,
        })
        login = await self.client.recv_one()
        if login is None or login[0] != MsgType.AUTH_OK:
            raise RuntimeError(f"LOGIN falhou: {login}")
        characters = login[1].get("characters") or []

        if not characters:
            # Rede de segurança — o fluxo normal é `provision.py` já ter
            # criado o personagem antes do swarm rodar.
            await self.client.send(MsgType.CREATE_CHARACTER, {
                "name": self.char_name, "class_id": self.class_id,
            })
            created = await self.client.recv_one()
            if created is None or created[0] != MsgType.CHARACTER_CREATED:
                raise RuntimeError(f"CREATE_CHARACTER falhou: {created}")
            char_id = int(created[1]["char"]["id"])
        else:
            char_id = int(characters[0]["id"])

        await self.client.send(MsgType.SELECT_CHARACTER, {"char_id": char_id})
        # LOGIN_OK e WORLD_STATE chegam pelo _recv_loop (mesmo canal de tudo
        # mais) — espera aqui só o suficiente pra ter eid/posição antes de
        # começar a pensar.
        deadline = time.monotonic() + 10.0
        while self.eid == -1 and time.monotonic() < deadline:
            msg = await self.client.recv_one(timeout=2.0)
            if msg is None:
                continue
            self._handle_message(msg[0], msg[1])
        if self.eid == -1:
            raise RuntimeError("LOGIN_OK nunca chegou")

    # ── recepção ──────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        async for msg_type, payload, _seq, _ts in self.client.messages():
            self._handle_message(msg_type, payload)

    def _handle_message(self, msg_type: MsgType, payload: dict) -> None:
        if msg_type == MsgType.LOGIN_OK:
            self.eid = int(payload["eid"])
            self.bot_eids.add(self.eid)
            char = payload.get("char") or {}
            self.tx = int(char.get("tile_x", self.tx))
            self.ty = int(char.get("tile_y", self.ty))
            self.anchor = (self.tx, self.ty)
            self.state = "QUEUEING" if self.role != "world" else "WANDER"

        elif msg_type == MsgType.WORLD_STATE:
            self.known_entities.clear()
            for ent in payload.get("entities", []):
                self.known_entities[ent["eid"]] = ent

        elif msg_type == MsgType.AOI_UPDATE:
            for ent in payload.get("spawned", []):
                self.known_entities[ent["eid"]] = ent
            for mv in payload.get("moved", []):
                eid = mv.get("eid")
                if eid in self.known_entities:
                    self.known_entities[eid].update(mv)
                if eid == self.eid:
                    self.tx = int(mv.get("tx", self.tx))
                    self.ty = int(mv.get("ty", self.ty))
            for eid in payload.get("despawned", []):
                self.known_entities.pop(eid, None)
                if eid == self.target_eid:
                    self.target_eid = -1

        elif msg_type == MsgType.ENTITY_SPAWN:
            self.known_entities[payload["eid"]] = payload
        elif msg_type == MsgType.ENTITY_DESPAWN or msg_type == MsgType.ENTITY_DEATH:
            eid = payload.get("eid")
            self.known_entities.pop(eid, None)
            if eid == self.target_eid:
                self.target_eid = -1

        elif msg_type == MsgType.ENTITY_MOVE:
            eid = payload.get("eid")
            if eid == self.eid:
                self.tx = int(payload.get("tx", self.tx))
                self.ty = int(payload.get("ty", self.ty))
            elif eid in self.known_entities:
                self.known_entities[eid].update(payload)

        elif msg_type == MsgType.STATS_UPDATE:
            # Só nos interessa se o próprio bot morreu/reviveu — o resto
            # (mana/raiva) o bot não precisa acompanhar de perto.
            pass

        elif msg_type == MsgType.LOOT_AVAILABLE:
            # Sem checagem de distância (confirmado em código,
            # `server/loot_processor.py::request_loot` nunca valida tile
            # do saqueador) — enfileira, o think loop drena com jitter.
            corpse_id = payload.get("corpse_id")
            if corpse_id is not None:
                self._pending_loot.append(corpse_id)

        elif msg_type == MsgType.LOOT_RESULT:
            pass  # nada a fazer — só confirma que o saque foi processado

        elif msg_type == MsgType.SKILL_RESULT:
            if payload.get("caster_eid") == self.eid:
                sid = payload.get("sid", "")
                if payload.get("failed"):
                    self.skill_ready_at[sid] = time.monotonic() + _FAILED_SKILL_BACKOFF_S
                else:
                    cd = payload.get("cooldown") or 0.0
                    self.skill_ready_at[sid] = time.monotonic() + max(cd, 0.5)
                    proj_target = payload.get("projectile_target", -1)
                    if sid in _PROJECTILE_SPELLS and proj_target not in (-1, None):
                        asyncio.create_task(
                            self._confirm_projectile_hit(sid, int(proj_target)))

        elif msg_type in (MsgType.ARENA_MATCH_FOUND, MsgType.BG_MATCH_FOUND):
            self.state = "MATCH_ACCEPT_PENDING"

        elif msg_type in (MsgType.ARENA_MATCH_START, MsgType.BG_MATCH_START):
            self.state = "IN_MATCH"
            self.target_eid = -1
            self.known_entities.clear()

        elif msg_type in (MsgType.ARENA_MATCH_END,):
            self.state = "QUEUEING" if self.requeue else "DONE"
            self.target_eid = -1

        elif msg_type == MsgType.ZONE_CHANGE:
            self.known_entities.clear()
            self.target_eid = -1

        elif msg_type == MsgType.PLAYER_DEATH:
            self.target_eid = -1

    # ── decisão ───────────────────────────────────────────────────────

    async def _think_loop(self) -> None:
        # 1ª ação já no spawn (sem esperar o intervalo aleatório) — pedido
        # do usuário (06/09/2026): com rampa de conexão mais lenta, quer
        # ver cada leva de bots agindo assim que loga, não parada por até
        # `think_max_s` antes da 1ª decisão.
        first = True
        while not self._stop.is_set():
            if not first:
                await asyncio.sleep(self.rng.uniform(self.think_min_s, self.think_max_s))
            first = False
            try:
                await self._think_once()
            except Exception:
                self.stats["errors"] = self.stats.get("errors", 0) + 1

    async def _think_once(self) -> None:
        if self._pending_loot:
            # Sem checagem de distância server-side (confirmado em código)
            # — bot só drena a fila com o jitter natural do próprio think
            # loop, nunca instantâneo no recv, pra não sincronizar todo
            # bot saqueando no mesmo pacote de rede.
            corpse_id = self._pending_loot.pop(0)
            await self.client.send(MsgType.LOOT_REQUEST, {"corpse_id": corpse_id, "take": "all"})

        if self.state == "QUEUEING":
            await self._send_queue_join()
        elif self.state == "MATCH_ACCEPT_PENDING":
            await self._send_match_accept()
        elif self.state in ("WANDER", "IN_MATCH"):
            await self._wander_or_engage()
        # LOGGING_IN/RECONNECTING/DONE: nada a fazer neste tick de pensamento.

    async def _send_queue_join(self) -> None:
        if self.role == "arena":
            await self.client.send(MsgType.ARENA_QUEUE_JOIN, {"mode": "1v1"})
        elif self.role == "bg":
            await self.client.send(MsgType.BG_QUEUE_JOIN, {})
        self.state = "IN_QUEUE"

    async def _send_match_accept(self) -> None:
        msg_type = MsgType.ARENA_MATCH_ACCEPT if self.role == "arena" else MsgType.BG_MATCH_ACCEPT
        await self.client.send(msg_type, {})
        self.state = "IN_QUEUE"   # aguarda MATCH_START real

    def _is_attackable(self, ent: dict) -> bool:
        kind = ent.get("kind")
        if kind == "enemy":
            # Boneco de treino nunca é escolhido como alvo por conta
            # própria (05-06/09/2026, ver PROBLEMAS_ARQUITETURA.md §115)
            # — HP altíssimo (999999) e nunca morre, então um bot que o
            # engaja fica "grudado" nele pra sempre, raramente ficando
            # "ocioso" o suficiente pra sortear uma excursão. `is_dummy`
            # vem de `WorldServer._build_mob_spawn_payload` — ÚNICA fonte
            # usada por WORLD_STATE/AOI_UPDATE/ENTITY_SPAWN, sempre
            # presente quando a entidade é mesmo o boneco (nunca precisa
            # checar nome/raça na mão).
            if ent.get("is_dummy"):
                return False
            return ent.get("faction") not in FRIENDLY_NPC_FACTIONS
        if kind == "player":
            # Mundo aberto não é PvP livre (precisa de duelo) — só mira em
            # outro player DENTRO de uma partida de arena/BG, nunca fora
            # (decisão do plano aprovado: "dentro de arena/BG, bots podem
            # lutar uns contra os outros" — não em mundo aberto).
            if self.state != "IN_MATCH":
                return False
            eid = ent.get("eid")
            return eid is not None and eid != self.eid and eid in self.bot_eids
        return False

    def _nearest_target(self, radius: int = _AGGRO_RADIUS_TILES, min_dist: int = 0) -> "int | None":
        best_eid, best_dist = None, None
        for eid, ent in self.known_entities.items():
            if not self._is_attackable(ent):
                continue
            etx, ety = ent.get("tx"), ent.get("ty")
            if etx is None or ety is None:
                continue
            dist = max(abs(etx - self.tx), abs(ety - self.ty))
            if dist > radius or dist < min_dist:
                continue
            if best_dist is None or dist < best_dist:
                best_eid, best_dist = eid, dist
        return best_eid

    async def _wander_or_engage(self) -> None:
        if self.target_eid == -1 or self.target_eid not in self.known_entities:
            self.target_eid = self._nearest_target() or -1

        if self.target_eid != -1:
            # Alvo de combate de verdade sempre ganha de uma excursão em
            # andamento (bush/POI/mob distante) — realista (reage a
            # ameaça no meio de um passeio) e evita competir com o
            # watchdog de goto.
            self._cancel_goto()
            await self._engage(self.target_eid)
            return

        if self._goto_path is not None:
            await self._continue_goto()
            return

        if not await self._maybe_start_excursion():
            await self._wander()

    async def _engage(self, target_eid: int) -> None:
        target = self.known_entities.get(target_eid)
        if target is None:
            self.target_eid = -1
            return
        etx, ety = target.get("tx", self.tx), target.get("ty", self.ty)
        dist = max(abs(etx - self.tx), abs(ety - self.ty))
        ready = [sid for sid in self.learned_skills
                if self.skill_ready_at.get(sid, 0.0) <= time.monotonic()]

        if dist > 1:
            # Skill com alcance real pode disparar sem precisar chegar
            # adjacente — achado real 05/09/2026 (ver
            # PROBLEMAS_ARQUITETURA.md §115): o bot sempre andava até
            # melee antes de castar QUALQUER coisa, nunca exercitando a
            # validação de alcance de verdade do servidor pra skill à
            # distância (`cast_range` do catálogo, ex.: Bola de Fogo=6,
            # Flecha Reiterada=8). `cast_range` ausente/0 = trata como
            # exigindo adjacência (comportamento antigo preservado).
            in_range = [sid for sid in ready
                       if dist <= (SKILL_CATALOG.get(sid, {}).get("cast_range") or 1)]
            if in_range and self.rng.random() < _SKILL_CHANCE_PER_TICK:
                sid = self.rng.choice(in_range)
                await self.client.send(MsgType.CAST_SKILL, {
                    "sid": sid, "tid": target_eid, "dir_x": 0.0, "dir_y": 0.0,
                })
                return
            await self._step_toward(etx, ety)
            return

        # Adjacente: auto-attack de base + skill por cima, ocasionalmente.
        await self.client.send(MsgType.AUTO_ATTACK, {"tid": target_eid})
        if ready and self.rng.random() < _SKILL_CHANCE_PER_TICK:
            sid = self.rng.choice(ready)
            await self.client.send(MsgType.CAST_SKILL, {
                "sid": sid, "tid": target_eid, "dir_x": 0.0, "dir_y": 0.0,
            })

    async def _confirm_projectile_hit(self, sid: str, target_eid: int) -> None:
        """Espera o tempo de voo do projétil (mesma tabela de velocidade que
        o servidor usa pra auto-confirmar bot de BG,
        `server/spell_completion_processor.py::_BOT_PROJECTILE_SPEED_PX_S`)
        e manda `PROJECTILE_HIT_CS` — sem isso o cast completa mas o
        servidor fica esperando pra sempre a confirmação visual que só um
        cliente de verdade manda, e o dano nunca é aplicado (achado real
        06/09/2026, ver PROBLEMAS_ARQUITETURA.md §115)."""
        target = self.known_entities.get(target_eid)
        if target is not None:
            dist_px = (max(abs(target.get("tx", self.tx) - self.tx),
                           abs(target.get("ty", self.ty) - self.ty)) * TILE_SIZE)
        else:
            dist_px = TILE_SIZE * 3  # mesmo fallback conservador do servidor
        speed = _PROJECTILE_SPEED_PX_S.get(sid, _PROJECTILE_DEFAULT_SPEED_PX_S)
        travel_time = max(0.05, dist_px / speed)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=travel_time)
            return  # bot parou antes do "impacto" — não manda nada
        except asyncio.TimeoutError:
            pass
        await self.client.send(MsgType.PROJECTILE_HIT_CS, {
            "spell_id": sid, "target_id": target_eid,
        })

    # ── excursão de longa distância (bush real / POI / mob visto de longe) ──

    async def _maybe_start_excursion(self) -> bool:
        """Retorna True se uma excursão foi iniciada (ou já está sendo
        calculada) neste think — nesse caso o wander curto de sempre não
        roda neste ciclo. Prioridade: mob visto de longe (mais realista
        — "clicar com o direito" num alvo fora do raio de aggro curto,
        mas ainda visível) > chance pequena de excursão a esmo (bush ou
        ponto de interesse) > wander curto (caso comum, de propósito)."""
        if self._goto_pending:
            return True  # A* já rodando numa thread — não decide de novo

        distant_eid = self._nearest_target(radius=_NOTICE_RADIUS_TILES,
                                           min_dist=_AGGRO_RADIUS_TILES + 1)
        if distant_eid is not None:
            ent = self.known_entities.get(distant_eid)
            etx, ety = (ent or {}).get("tx"), (ent or {}).get("ty")
            if etx is not None and ety is not None:
                self._start_goto((etx, ety))
                return True

        roll = self.rng.random()
        if roll < _BUSH_EXCURSION_CHANCE:
            bushes = map_data.bush_tiles()
            if bushes:
                self._start_goto(self.rng.choice(bushes))
                return True
        elif roll < _BUSH_EXCURSION_CHANCE + _POI_EXCURSION_CHANCE:
            pois = map_data.poi_points()
            if pois:
                self._start_goto(self.rng.choice(pois))
                return True
        return False

    def _start_goto(self, dest: "tuple[int, int]") -> None:
        self._goto_pending = True
        self.stats["goto_started"] = self.stats.get("goto_started", 0) + 1
        asyncio.create_task(self._compute_goto(dest))

    async def _compute_goto(self, dest: "tuple[int, int]") -> None:
        """A* real, OFFLOADED pra thread (`run_in_executor`) — mesmo um
        cálculo custoso (visto em teste manual: até ~150ms pra travessia
        de mapa inteiro) nunca trava o loop do PRÓPRIO swarm enquanto
        roda, deixando os outros bots livres pra continuar mandando
        rede no meio tempo."""
        try:
            loop = asyncio.get_running_loop()
            path = await loop.run_in_executor(None, map_data.find_path, (self.tx, self.ty), dest)
        except Exception:
            path = None
        finally:
            self._goto_pending = False
        if not path:
            # Inalcançável (zona sem conexão andável direta) ou A* desistiu
            # dentro do próprio orçamento de nós — nunca insiste, só cai
            # de volta pro wander/decisão normal no próximo think.
            self.stats["goto_unreachable"] = self.stats.get("goto_unreachable", 0) + 1
            return
        self._goto_path = path
        self._goto_index = 0
        now = time.monotonic()
        self._goto_started_at = now
        self._goto_last_progress_at = now
        self._goto_last_pos = (self.tx, self.ty)

    async def _continue_goto(self) -> None:
        now = time.monotonic()
        # Watchdog (recomendação do QA da rodada de pesquisa, ver
        # PROBLEMAS_ARQUITETURA.md §115) — sem avançar de verdade por
        # muito tempo (parede que o A* não previu, outra entidade no
        # caminho, MOVE rejeitado em sequência): desiste da excursão,
        # nunca fica preso repetindo o mesmo destino pra sempre.
        if (self.tx, self.ty) != self._goto_last_pos:
            self._goto_last_pos = (self.tx, self.ty)
            self._goto_last_progress_at = now
        elif now - self._goto_last_progress_at > _GOTO_STUCK_TIMEOUT_S:
            self.stats["goto_abandoned"] = self.stats.get("goto_abandoned", 0) + 1
            self._cancel_goto()
            return

        if self._goto_path is None or self._goto_index >= len(self._goto_path):
            self._cancel_goto()
            return

        next_tx, next_ty = self._goto_path[self._goto_index]
        self._goto_index += 1
        await self._send_move(next_tx, next_ty)

    def _cancel_goto(self) -> None:
        self._goto_path = None
        self._goto_index = 0

    async def _step_toward(self, etx: int, ety: int) -> None:
        dx = 0 if etx == self.tx else (1 if etx > self.tx else -1)
        dy = 0 if ety == self.ty else (1 if ety > self.ty else -1)
        if dx == 0 and dy == 0:
            return
        await self._send_move(self.tx + dx, self.ty + dy)

    async def _send_move(self, target_tx: int, target_ty: int) -> None:
        """Move é otimista, igual ao client real (`server/session.py`
        NUNCA ecoa MOVE aceito de volta pro remetente — só corrige quem
        errou). Sem aplicar aqui na hora, `self.tx/ty` nunca avançam e
        todo passo seguinte calcula a partir da posição velha (bug real
        achado 05/09/2026 — bot ficava andando "no lugar" o teste
        inteiro). `_handle_message` já reverte via ENTITY_MOVE(self) se
        o servidor rejeitar."""
        self.tx, self.ty = target_tx, target_ty
        await self.client.send(MsgType.MOVE, {"tx": target_tx, "ty": target_ty})

    async def _wander(self) -> None:
        now = time.monotonic()
        candidates = [d for d in _ADJ if self._blocked_dirs.get(d, 0.0) <= now]
        if not candidates:
            candidates = list(_ADJ)

        if self.anchor is not None:
            ax, ay = self.anchor
            drift = max(abs(self.tx - ax), abs(self.ty - ay))
            if drift > _WANDER_ANCHOR_DRIFT_MAX:
                # Puxa de volta pro anchor em vez de continuar se afastando.
                candidates.sort(key=lambda d: abs((self.tx + d[0]) - ax) + abs((self.ty + d[1]) - ay))
                dx, dy = candidates[0]
            else:
                dx, dy = self.rng.choice(candidates)
        else:
            dx, dy = self.rng.choice(candidates)

        await self._send_move(self.tx + dx, self.ty + dy)
        # Não sabemos ainda se o servidor aceitou — bloqueia a direção por
        # um tempo curto, autolimitando ruído contra paredes sem precisar
        # mapear o CSV do mapa (correção real vem via ENTITY_MOVE(self) se
        # for rejeitado — nunca fica "preso" numa direção pra sempre).
        self._blocked_dirs[(dx, dy)] = now + 1.5
