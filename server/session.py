"""
server/session.py
Gerencia sessões de jogadores conectados via WebSocket.
"""
from __future__ import annotations
import asyncio
import time
from typing import TYPE_CHECKING

from shared.messages import MsgType, encode, decode, make_aoi_update
from shared.constants import AOI_RADIUS

if TYPE_CHECKING:
    pass


class Session:
    def __init__(self, ws, session_id: str):
        self.ws            = ws
        self.session_id    = session_id
        self.entity_id     = -1
        self.username      = ""
        self.char_data     = {}
        self.authenticated = False
        self._seq          = 0

    async def send(self, msg_type: MsgType, payload: dict) -> None:
        try:
            self._seq += 1
            await self.ws.send(encode(msg_type, payload, seq=self._seq))
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"Session({self.username!r} eid={self.entity_id})"


class SessionManager:

    def __init__(self, world_server):
        self.world_server = world_server
        self._sessions:   dict[str, Session] = {}   # session_id → Session
        self._eid_to_sid: dict[int, str]     = {}   # entity_id  → session_id
        self.world_server.register_on_tick(self._on_tick)

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def on_connect(self, ws, session_id: str) -> Session:
        session = Session(ws, session_id)
        self._sessions[session_id] = session
        print(f"[Session] +connect {session_id}  total={len(self._sessions)}")
        return session

    async def on_disconnect(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        if session.entity_id != -1:
            self._eid_to_sid.pop(session.entity_id, None)
            self.world_server.despawn_player(session_id)
            # Avisa todos os outros que este jogador saiu
            await self._broadcast_aoi_from_session(session, MsgType.ENTITY_DESPAWN,
                                                   {"eid": session.entity_id})
        print(f"[Session] -disconnect {session.username!r}")

    async def on_message(self, session: Session, raw: str) -> None:
        try:
            msg_type, payload, seq, ts = decode(raw)
        except ValueError as e:
            await session.send(MsgType.ERROR, {"reason": str(e)})
            return
        handler = self._handlers.get(msg_type)
        if handler:
            await handler(self, session, payload, ts)

    # ── Handlers C→S ─────────────────────────────────────────────────────────

    async def _handle_login(self, session: Session, payload: dict, ts: int) -> None:
        from server.auth import authenticate
        username = payload.get("username", "")
        password = payload.get("password", "")

        char_data = await authenticate(username, password)
        if char_data is None:
            await session.send(MsgType.LOGIN_ERROR, {"reason": "invalid_credentials"})
            return

        # Bloqueia login duplo da mesma conta
        for s in self._sessions.values():
            if s.authenticated and s.username == username:
                await session.send(MsgType.LOGIN_ERROR, {"reason": "already_online"})
                return

        # Spawn no mundo
        eid = self.world_server.spawn_player(session.session_id, char_data)

        session.username       = username
        session.entity_id      = eid
        session.char_data      = dict(char_data)
        session.authenticated  = True
        self._eid_to_sid[eid]  = session.session_id

        # Sincroniza posição na sessão
        tx, ty = self.world_server.get_tile_pos(session.session_id)

        await session.send(MsgType.LOGIN_OK, {
            "token":     session.session_id,
            "eid":       eid,
            "char":      dict(char_data),
            "server_ts": int(time.time() * 1000),
        })

        # Envia snapshot inicial: jogadores no AOI
        near_players = self.world_server.get_players_in_aoi(session.session_id, AOI_RADIUS)
        # Adiciona dados de username a cada jogador próximo
        for p in near_players:
            s2 = self._sessions.get(p.get("session_id", ""))
            if s2:
                p["name"]     = s2.username
                p["class_id"] = s2.char_data.get("class_id", "guerreiro")
                p["hp"]       = s2.char_data.get("hp", 100)
                p["hp_max"]   = s2.char_data.get("hp", 100)
                p["level"]    = s2.char_data.get("level", 1)
                p["effects"]  = []

        await session.send(MsgType.WORLD_STATE, {
            "tick":     self.world_server.tick_count,
            "tx":       tx, "ty": ty,
            "entities": near_players,
        })

        # Avisa jogadores próximos que este entrou
        spawn_payload = {
            "eid":      eid,
            "kind":     "player",
            "tx":       tx, "ty": ty,
            "name":     username,
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp":       char_data.get("hp", 100),
            "hp_max":   char_data.get("hp", 100),
            "level":    char_data.get("level", 1),
            "effects":  [],
        }
        await self._broadcast_aoi_except(session, MsgType.ENTITY_SPAWN, spawn_payload)
        print(f"[Session] login ok: {username!r}  eid={eid}  tile=({tx},{ty})")

    async def _handle_move(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        tx = int(payload.get("tx", 0))
        ty = int(payload.get("ty", 0))

        accepted = self.world_server.move_player(session.session_id, tx, ty)

        if accepted:
            # Broadcast para todos no AOI (incluindo quem moveu — confirmação)
            await self._broadcast_aoi_from_session(session, MsgType.ENTITY_MOVE, {
                "eid":     session.entity_id,
                "tx":      tx, "ty": ty,
                "from_tx": payload.get("from_tx", tx),
                "from_ty": payload.get("from_ty", ty),
            })
        else:
            # Rejeita: manda posição correta de volta ao cliente
            real_tx, real_ty = self.world_server.get_tile_pos(session.session_id)
            await session.send(MsgType.ENTITY_MOVE, {
                "eid":     session.entity_id,
                "tx":      real_tx, "ty": real_ty,
                "from_tx": real_tx, "from_ty": real_ty,
            })

    async def _handle_ping(self, session: Session, payload: dict, ts: int) -> None:
        await session.send(MsgType.PONG, {
            "client_ts": payload.get("client_ts", 0),
            "server_ts": int(time.time() * 1000),
        })

    async def _handle_auto_attack(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        # TODO: CombatSystem (Marco 2)
        print(f"[Combat] {session.username} → attack tid={payload.get('tid')}")

    async def _handle_cast_skill(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        # TODO: SkillSystem (Marco 2+)
        print(f"[Skill]  {session.username} → {payload.get('sid')}  ts={ts}")

    async def _handle_chat(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        text    = str(payload.get("text", ""))[:200]
        channel = payload.get("channel", "local")
        msg = {"sender": session.username, "text": text,
               "channel": channel, "color": [220, 210, 150]}
        if channel == "world":
            await self._broadcast_all(MsgType.CHAT_MESSAGE, msg)
        else:
            await self._broadcast_aoi_from_session(session, MsgType.CHAT_MESSAGE, msg)

    _handlers = {
        MsgType.LOGIN:       _handle_login,
        MsgType.MOVE:        _handle_move,
        MsgType.PING:        _handle_ping,
        MsgType.AUTO_ATTACK: _handle_auto_attack,
        MsgType.CAST_SKILL:  _handle_cast_skill,
        MsgType.CHAT_SEND:   _handle_chat,
    }

    # ── Callback do tick ──────────────────────────────────────────────────────

    def _on_tick(self, tick_count: int, deltas: dict) -> None:
        if not any(deltas.values()):
            return
        asyncio.create_task(self._dispatch_tick_deltas(deltas))

    async def _dispatch_tick_deltas(self, deltas: dict) -> None:
        """Distribui deltas do tick para cada cliente, filtrado por AOI."""
        for session in list(self._sessions.values()):
            if not session.authenticated:
                continue
            tx, ty = self.world_server.get_tile_pos(session.session_id)
            filtered = self._filter_deltas_for(deltas, tx, ty)
            if filtered:
                await session.send(MsgType.AOI_UPDATE, filtered)

    def _filter_deltas_for(self, deltas: dict, cx: int, cy: int) -> dict:
        """Retorna apenas os deltas visíveis para um jogador em (cx, cy)."""
        r = AOI_RADIUS

        def in_aoi(tx, ty):
            return abs(tx - cx) <= r and abs(ty - cy) <= r

        moved = [m for m in deltas.get("moved", [])
                 if in_aoi(m["tx"], m["ty"]) or in_aoi(m["from_tx"], m["from_ty"])]
        spawned   = [e for e in deltas.get("spawned", [])
                     if in_aoi(e["tx"], e["ty"])]
        despawned = deltas.get("despawned", [])   # sempre envia — cliente ignora desconhecidos
        stats     = deltas.get("stats", [])
        effects   = deltas.get("effects", [])

        result: dict = {}
        if moved:     result["moved"]     = moved
        if spawned:   result["spawned"]   = spawned
        if despawned: result["despawned"] = despawned
        if stats:     result["stats"]     = stats
        if effects:   result["effects"]   = effects
        return result

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    async def _broadcast_all(self, msg_type: MsgType, payload: dict) -> None:
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions.values() if s.authenticated]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_from_session(self, origin: Session,
                                          msg_type: MsgType, payload: dict) -> None:
        """Envia para todos (incluindo origin) dentro do AOI_RADIUS de origin."""
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        tasks = []
        for s in self._sessions.values():
            if not s.authenticated:
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            if abs(sx - ox) <= AOI_RADIUS and abs(sy - oy) <= AOI_RADIUS:
                tasks.append(s.send(msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_except(self, origin: Session,
                                    msg_type: MsgType, payload: dict) -> None:
        """Igual _broadcast_aoi_from_session mas exclui o origin."""
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        tasks = []
        for s in self._sessions.values():
            if not s.authenticated or s.session_id == origin.session_id:
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            if abs(sx - ox) <= AOI_RADIUS and abs(sy - oy) <= AOI_RADIUS:
                tasks.append(s.send(msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
