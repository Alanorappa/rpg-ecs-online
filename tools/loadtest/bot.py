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

from shared.constants import PROTOCOL_VERSION
from shared.messages import MsgType
from tools.loadtest.identity import (
    FRIENDLY_NPC_FACTIONS, bot_char_name, bot_class, bot_password,
    bot_username, starter_skills_for_class,
)
from tools.loadtest.protocol_client import ProtocolClient

_ADJ = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))

# Estilo de combate: mantém AUTO_ATTACK como ação de base (é o que gera
# raiva pro guerreiro — golpe_poderoso não sai sem isso) e só CAMADA
# CAST_SKILL por cima, nunca troca um pelo outro.
_SKILL_CHANCE_PER_TICK = 0.35
_FAILED_SKILL_BACKOFF_S = 2.0
_AGGRO_RADIUS_TILES = 6
_WANDER_ANCHOR_DRIFT_MAX = 10   # tiles — além disso, wander tende a voltar


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
                if self._stop.is_set():
                    break
                self.bot_eids.discard(self.eid)
                self.eid = -1
                self.state = "RECONNECTING"
                await self._reconnect_backoff(e)
        self.bot_eids.discard(self.eid)
        await self.client.close()

    async def _reconnect_backoff(self, exc: Exception) -> None:
        self._reconnect_attempt += 1
        delay = min(30.0, 2 ** min(self._reconnect_attempt, 5))
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

        elif msg_type == MsgType.SKILL_RESULT:
            if payload.get("caster_eid") == self.eid:
                sid = payload.get("sid", "")
                if payload.get("failed"):
                    self.skill_ready_at[sid] = time.monotonic() + _FAILED_SKILL_BACKOFF_S
                else:
                    cd = payload.get("cooldown") or 0.0
                    self.skill_ready_at[sid] = time.monotonic() + max(cd, 0.5)

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
        while not self._stop.is_set():
            await asyncio.sleep(self.rng.uniform(self.think_min_s, self.think_max_s))
            try:
                await self._think_once()
            except Exception:
                self.stats["errors"] = self.stats.get("errors", 0) + 1

    async def _think_once(self) -> None:
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

    def _nearest_target(self) -> "int | None":
        best_eid, best_dist = None, None
        for eid, ent in self.known_entities.items():
            if not self._is_attackable(ent):
                continue
            etx, ety = ent.get("tx"), ent.get("ty")
            if etx is None or ety is None:
                continue
            dist = max(abs(etx - self.tx), abs(ety - self.ty))
            if dist > _AGGRO_RADIUS_TILES:
                continue
            if best_dist is None or dist < best_dist:
                best_eid, best_dist = eid, dist
        return best_eid

    async def _wander_or_engage(self) -> None:
        if self.target_eid == -1 or self.target_eid not in self.known_entities:
            self.target_eid = self._nearest_target() or -1

        if self.target_eid != -1:
            await self._engage(self.target_eid)
        else:
            await self._wander()

    async def _engage(self, target_eid: int) -> None:
        target = self.known_entities.get(target_eid)
        if target is None:
            self.target_eid = -1
            return
        etx, ety = target.get("tx", self.tx), target.get("ty", self.ty)
        dist = max(abs(etx - self.tx), abs(ety - self.ty))
        if dist > 1:
            await self._step_toward(etx, ety)
            return

        # Adjacente: auto-attack de base + skill por cima, ocasionalmente.
        await self.client.send(MsgType.AUTO_ATTACK, {"tid": target_eid})
        ready = [sid for sid in self.learned_skills
                if self.skill_ready_at.get(sid, 0.0) <= time.monotonic()]
        if ready and self.rng.random() < _SKILL_CHANCE_PER_TICK:
            sid = self.rng.choice(ready)
            await self.client.send(MsgType.CAST_SKILL, {
                "sid": sid, "tid": target_eid, "dir_x": 0.0, "dir_y": 0.0,
            })

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
