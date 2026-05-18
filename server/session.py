"""
server/session.py
Gerencia sessões de jogadores conectados via WebSocket.

Session       → uma conexão ativa (1 jogador)
SessionManager → registry de todas as sessões + broadcast por AOI
"""
from __future__ import annotations
import asyncio
import time
from typing import TYPE_CHECKING

from shared.messages import MsgType, encode, decode, make_aoi_update
from shared.constants import AOI_RADIUS, TILE_SIZE

if TYPE_CHECKING:
    import websockets


class Session:
    """Representa um jogador conectado."""

    def __init__(self, ws, session_id: str):
        self.ws          = ws
        self.session_id  = session_id
        self.entity_id   = -1     # eid no WorldServer (definido após login)
        self.username    = ""
        self.tile_x      = 0
        self.tile_y      = 0
        self.authenticated = False
        self._seq        = 0      # sequência crescente de mensagens enviadas

    async def send(self, msg_type: MsgType, payload: dict) -> None:
        """Envia uma mensagem para este cliente."""
        try:
            self._seq += 1
            raw = encode(msg_type, payload, seq=self._seq)
            await self.ws.send(raw)
        except Exception:
            pass   # conexão caiu — SessionManager lida com o cleanup

    def update_position(self, tx: int, ty: int) -> None:
        self.tile_x = tx
        self.tile_y = ty

    def __repr__(self) -> str:
        return f"Session({self.username!r}, eid={self.entity_id}, pos=({self.tile_x},{self.tile_y}))"


class SessionManager:
    """
    Registry de todas as sessões ativas.
    Responsável por:
    - Autenticar novas conexões
    - Distribuir AOI_UPDATE do tick para cada jogador
    - Broadcast de eventos pontuais (chat, morte, etc.)
    """

    def __init__(self, world_server):
        self.world_server  = world_server
        self._sessions: dict[str, Session] = {}   # session_id → Session
        self._eid_to_sid:  dict[int, str]  = {}   # entity_id  → session_id

        # Registra callback no WorldServer para receber deltas a cada tick
        self.world_server.register_on_tick(self._on_tick)

    # ── Ciclo de vida da sessão ───────────────────────────────────────────────

    async def on_connect(self, ws, session_id: str) -> Session:
        session = Session(ws, session_id)
        self._sessions[session_id] = session
        print(f"[SessionManager] conectado: {session_id}  total={len(self._sessions)}")
        return session

    async def on_disconnect(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session and session.entity_id != -1:
            self._eid_to_sid.pop(session.entity_id, None)
            # TODO: salvar personagem no banco antes de remover do mundo
            print(f"[SessionManager] desconectado: {session.username!r}  eid={session.entity_id}")

    async def on_message(self, session: Session, raw: str) -> None:
        """Despacha mensagem recebida de um cliente para o handler correto."""
        try:
            msg_type, payload, seq, ts = decode(raw)
        except ValueError as e:
            await session.send(MsgType.ERROR, {"reason": str(e)})
            return

        handler = self._handlers.get(msg_type)
        if handler:
            await handler(self, session, payload, ts)
        else:
            await session.send(MsgType.ERROR, {"reason": f"unknown_type:{msg_type.value}"})

    # ── Handlers de mensagens C→S ─────────────────────────────────────────────

    async def _handle_login(self, session: Session, payload: dict, ts: int) -> None:
        from server.auth import authenticate
        username = payload.get("username", "")
        password = payload.get("password", "")

        char_data = await authenticate(username, password)
        if char_data is None:
            await session.send(MsgType.LOGIN_ERROR, {"reason": "invalid_credentials"})
            return

        session.username       = username
        session.authenticated  = True
        session.entity_id      = self._spawn_player(char_data)
        session.tile_x         = char_data.get("tile_x", 10)
        session.tile_y         = char_data.get("tile_y", 10)
        self._eid_to_sid[session.entity_id] = session.session_id

        await session.send(MsgType.LOGIN_OK, {
            "token":     session.session_id,
            "eid":       session.entity_id,
            "char":      char_data,
            "server_ts": int(time.time() * 1000),
        })

        # Envia snapshot inicial do mundo (entidades no AOI)
        world_state = self._build_world_state(session)
        await session.send(MsgType.WORLD_STATE, world_state)

        print(f"[SessionManager] login ok: {username!r}  eid={session.entity_id}")

    async def _handle_move(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        tx = payload.get("tx", session.tile_x)
        ty = payload.get("ty", session.tile_y)

        # Validação básica: distância máxima de 1 tile por move
        dx = abs(tx - session.tile_x)
        dy = abs(ty - session.tile_y)
        if dx > 1 or dy > 1:
            # Manda de volta a posição correta (anti-cheat de teleporte)
            await session.send(MsgType.ENTITY_MOVE, {
                "eid": session.entity_id,
                "tx":  session.tile_x, "ty":  session.tile_y,
                "from_tx": session.tile_x, "from_ty": session.tile_y,
            })
            return

        old_tx, old_ty = session.tile_x, session.tile_y
        session.update_position(tx, ty)

        # TODO: validar colisão com tilemap no WorldServer

        # Broadcast para todos no AOI do jogador
        move_msg = {
            "eid": session.entity_id,
            "tx": tx, "ty": ty,
            "from_tx": old_tx, "from_ty": old_ty,
        }
        await self._broadcast_to_aoi(session, MsgType.ENTITY_MOVE, move_msg)

    async def _handle_ping(self, session: Session, payload: dict, ts: int) -> None:
        await session.send(MsgType.PONG, {
            "client_ts": payload.get("client_ts", 0),
            "server_ts": int(time.time() * 1000),
        })

    async def _handle_auto_attack(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        # TODO: passar para CombatSystem no WorldServer
        target_id = payload.get("tid", -1)
        print(f"[Combat] {session.username} → auto_attack target={target_id}")

    async def _handle_cast_skill(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        # TODO: passar para SkillSystem no WorldServer com timestamp para lag comp
        sid = payload.get("sid", "")
        print(f"[Skill] {session.username} → cast_skill={sid}  ts={ts}")

    async def _handle_chat(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        text    = str(payload.get("text", ""))[:200]   # limite de caracteres
        channel = payload.get("channel", "local")
        msg = {
            "sender":  session.username,
            "text":    text,
            "channel": channel,
            "color":   [220, 210, 150],
        }
        if channel == "world":
            await self._broadcast_all(MsgType.CHAT_MESSAGE, msg)
        else:
            await self._broadcast_to_aoi(session, MsgType.CHAT_MESSAGE, msg)

    # Tabela de dispatch: MsgType → handler
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
        """Chamado pelo WorldServer ao fim de cada tick. Envia AOI_UPDATE relevante."""
        if not any(deltas.values()):
            return   # nada mudou, não envia pacote

        # Agenda envio assíncrono (não bloqueia o tick)
        asyncio.create_task(self._dispatch_aoi_updates(deltas))

    async def _dispatch_aoi_updates(self, deltas: dict) -> None:
        """Distribui os deltas do tick para cada cliente segundo seu AOI."""
        for session in list(self._sessions.values()):
            if not session.authenticated:
                continue
            # Filtra deltas relevantes para este jogador (por agora manda tudo)
            # TODO: filtrar por AOI_RADIUS
            filtered = make_aoi_update(**{k: v for k, v in deltas.items() if v})
            if filtered:
                await session.send(MsgType.AOI_UPDATE, filtered)

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    async def _broadcast_all(self, msg_type: MsgType, payload: dict) -> None:
        """Envia para todos os jogadores autenticados."""
        tasks = [
            s.send(msg_type, payload)
            for s in self._sessions.values() if s.authenticated
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_to_aoi(self, origin: Session,
                                 msg_type: MsgType, payload: dict) -> None:
        """Envia para jogadores dentro do AOI_RADIUS do origin (incluindo ele mesmo)."""
        tasks = []
        for s in self._sessions.values():
            if not s.authenticated:
                continue
            dx = abs(s.tile_x - origin.tile_x)
            dy = abs(s.tile_y - origin.tile_y)
            if dx <= AOI_RADIUS and dy <= AOI_RADIUS:
                tasks.append(s.send(msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _spawn_player(self, char_data: dict) -> int:
        """Cria entidade do jogador no WorldServer. Retorna entity_id."""
        # TODO: criar entidade ECS completa com components do personagem
        # Por agora retorna um ID incremental simples
        return len(self._eid_to_sid) + 1000

    def _build_world_state(self, session: Session) -> dict:
        """Monta snapshot inicial do AOI para enviar no login."""
        # TODO: buscar entidades reais do WorldServer no AOI
        return {
            "tick":    self.world_server.tick_count,
            "tx":      session.tile_x,
            "ty":      session.tile_y,
            "entities": [],   # lista de EntitySpawn dos jogadores/mobs próximos
        }
