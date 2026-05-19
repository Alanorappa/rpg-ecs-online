"""
server/session.py
Gerencia sessões de jogadores conectados via WebSocket.

Implementa AOI subscription por sessão: cada Session mantém known_eids
(conjunto de entidades que o cliente já sabe sobre). Isso garante que:
- Mobs que entram no FOV após o login aparecem via ENTITY_SPAWN
- Mobs que saem do FOV são despawnados no cliente
- ENTITY_MOVE só é enviado para entidades já conhecidas
"""
from __future__ import annotations
import asyncio
import time

from shared.messages import MsgType, encode, decode
from shared.constants import AOI_RADIUS


class Session:
    def __init__(self, ws, session_id: str):
        self.ws            = ws
        self.session_id    = session_id
        self.entity_id     = -1
        self.username      = ""
        self.char_data     = {}
        self.authenticated = False
        self._seq          = 0
        self.known_eids: set[int] = set()   # entidades que este cliente conhece

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
        self._sessions:   dict[str, Session] = {}
        self._eid_to_sid: dict[int, str]     = {}
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
            await self._broadcast_all(MsgType.ENTITY_DESPAWN, {"eid": session.entity_id})
            self.world_server.despawn_player(session_id)
        print(f"[Session] -disconnect {session.username!r}")

    async def on_message(self, session: Session, raw: str) -> None:
        try:
            msg_type, payload, seq, ts = decode(raw)
        except ValueError as e:
            await session.send(MsgType.ERROR, {"reason": str(e)})
            return
        handler = self._handlers.get(msg_type)
        if handler:
            try:
                await handler(self, session, payload, ts)
            except Exception as e:
                import traceback
                print(f"[Session] ERRO em handler {msg_type}: {e}")
                traceback.print_exc()
                await session.send(MsgType.ERROR, {"reason": f"server_error:{type(e).__name__}"})

    # ── Handlers C→S ─────────────────────────────────────────────────────────

    async def _handle_login(self, session: Session, payload: dict, ts: int) -> None:
        from server.auth import authenticate
        username = payload.get("username", "")
        password = payload.get("password", "")

        char_data = await authenticate(username, password)
        if char_data is None:
            await session.send(MsgType.LOGIN_ERROR, {"reason": "invalid_credentials"})
            return

        for s in self._sessions.values():
            if s.authenticated and s.username == username:
                await session.send(MsgType.LOGIN_ERROR, {"reason": "already_online"})
                return

        eid = self.world_server.spawn_player(session.session_id, char_data)

        session.username       = username
        session.entity_id      = eid
        session.char_data      = dict(char_data)
        session.authenticated  = True
        self._eid_to_sid[eid]  = session.session_id

        tx, ty = self.world_server.get_tile_pos(session.session_id)

        await session.send(MsgType.LOGIN_OK, {
            "token":     session.session_id,
            "eid":       eid,
            "char":      dict(char_data),
            "server_ts": int(time.time() * 1000),
        })

        # Snapshot inicial: jogadores próximos
        near_players = self.world_server.get_players_in_aoi(session.session_id, AOI_RADIUS)
        for p in near_players:
            s2 = self._sessions.get(p.get("session_id", ""))
            if s2:
                p["name"]     = s2.username
                p["class_id"] = s2.char_data.get("class_id", "guerreiro")
                p["hp"]       = s2.char_data.get("hp", 100)
                p["hp_max"]   = s2.char_data.get("hp", 100)
                p["level"]    = s2.char_data.get("level", 1)
                p["effects"]  = []

        # Snapshot inicial: mobs próximos
        near_mobs = self.world_server.get_mobs_in_aoi(tx, ty, AOI_RADIUS)

        all_entities = near_players + near_mobs
        await session.send(MsgType.WORLD_STATE, {
            "tick":     self.world_server.tick_count,
            "tx":       tx, "ty": ty,
            "entities": all_entities,
        })

        # Popula known_eids com tudo que foi enviado no WORLD_STATE
        session.known_eids.add(eid)  # próprio player
        for ent in all_entities:
            session.known_eids.add(ent["eid"])

        # Avisa outros que este player entrou
        spawn_payload = {
            "eid":      eid, "kind":     "player",
            "tx":       tx,  "ty":       ty,
            "name":     username,
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp":       char_data.get("hp", 100),
            "hp_max":   char_data.get("hp", 100),
            "level":    char_data.get("level", 1),
            "effects":  [],
        }
        await self._broadcast_aoi_except(session, MsgType.ENTITY_SPAWN, spawn_payload)
        # Outros players passam a conhecer este
        for s in self._sessions.values():
            if s.authenticated and s.session_id != session.session_id:
                sx, sy = self.world_server.get_tile_pos(s.session_id)
                if abs(sx - tx) <= AOI_RADIUS and abs(sy - ty) <= AOI_RADIUS:
                    s.known_eids.add(eid)

        print(f"[Session] login ok: {username!r}  eid={eid}  tile=({tx},{ty})")

    async def _handle_move(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        tx = int(payload.get("tx", 0))
        ty = int(payload.get("ty", 0))
        accepted = self.world_server.move_player(session.session_id, tx, ty)
        if accepted:
            await self._broadcast_aoi_from_session(session, MsgType.ENTITY_MOVE, {
                "eid":     session.entity_id,
                "tx":      tx, "ty": ty,
                "from_tx": payload.get("from_tx", tx),
                "from_ty": payload.get("from_ty", ty),
            })
        else:
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
        target_eid = int(payload.get("tid", -1))
        self.world_server.set_player_target(session.session_id, target_eid)

    async def _handle_cast_skill(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
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

    # ── AOI subscription — núcleo do sistema ─────────────────────────────────

    def _on_tick(self, tick_count: int, deltas: dict) -> None:
        if not any(deltas.values()):
            return
        asyncio.create_task(self._dispatch_tick_deltas(deltas))

    async def _dispatch_tick_deltas(self, deltas: dict) -> None:
        """Distribui deltas para cada cliente respeitando known_eids (AOI subscription)."""
        try:
            for session in list(self._sessions.values()):
                if not session.authenticated:
                    continue
                tx, ty = self.world_server.get_tile_pos(session.session_id)
                update = self._build_update_for_session(session, deltas, tx, ty)
                if update:
                    await session.send(MsgType.AOI_UPDATE, update)
        except Exception as e:
            import traceback
            print(f"[Session] ERRO em _dispatch_tick_deltas: {e}")
            traceback.print_exc()

    def _build_update_for_session(self, session: Session,
                                   deltas: dict, cx: int, cy: int) -> dict:
        """
        Constrói AOI_UPDATE para uma sessão específica, com subscription tracking:
        - Entidade entra no AOI → ENTITY_SPAWN + adiciona a known_eids
        - Entidade sai do AOI  → ENTITY_DESPAWN + remove de known_eids
        - Entidade em AOI conhecida → ENTITY_MOVE
        """
        r = AOI_RADIUS
        result: dict = {}

        def in_aoi(tx: int, ty: int) -> bool:
            return abs(tx - cx) <= r and abs(ty - cy) <= r

        # ── Moves: verifica entradas/saídas de AOI ────────────────────
        confirmed_moves = []
        aoi_exits       = []
        aoi_entries     = []

        for m in deltas.get("moved", []):
            eid    = m["eid"]
            in_new = in_aoi(m["tx"],      m["ty"])
            in_old = in_aoi(m["from_tx"], m["from_ty"])

            if eid in session.known_eids:
                if in_new:
                    confirmed_moves.append(m)   # ainda no AOI, envia move
                else:
                    aoi_exits.append(eid)       # saiu do AOI
                    session.known_eids.discard(eid)
            else:
                if in_new:
                    aoi_entries.append(eid)     # entrou no AOI pela primeira vez

        # ── Novas entidades no AOI (via move) ─────────────────────────
        for eid in aoi_entries:
            spawn_data = self.world_server.get_entity_spawn_data(eid)
            if spawn_data:
                result.setdefault("spawned", []).append(spawn_data)
                session.known_eids.add(eid)

        # ── Spawns novos (entidades criadas neste tick) ───────────────
        for sp in deltas.get("spawned", []):
            if in_aoi(sp["tx"], sp["ty"]):
                result.setdefault("spawned", []).append(sp)
                session.known_eids.add(sp["eid"])

        # ── Despawns ──────────────────────────────────────────────────
        final_despawned = list(aoi_exits)
        for eid in deltas.get("despawned", []):
            if eid in session.known_eids:
                final_despawned.append(eid)
                session.known_eids.discard(eid)

        # ── Monta resultado ───────────────────────────────────────────
        if confirmed_moves:  result["moved"]     = confirmed_moves
        if final_despawned:  result["despawned"] = list(set(final_despawned))
        for key in ("stats", "effects", "combat"):
            if deltas.get(key):
                result[key] = deltas[key]

        return result

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    async def _broadcast_all(self, msg_type: MsgType, payload: dict) -> None:
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions.values() if s.authenticated]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_from_session(self, origin: Session,
                                          msg_type: MsgType, payload: dict) -> None:
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
