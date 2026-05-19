"""
client/network.py
Camada de rede do cliente. Gerencia a conexão WebSocket em background thread
e expõe duas filas thread-safe:
  - outbox: mensagens a enviar (game loop → rede)
  - inbox:  mensagens recebidas (rede → game loop)

O game loop nunca bloqueia: apenas coloca/retira itens das filas.
"""
from __future__ import annotations
import asyncio
import queue
import threading
import time
from typing import Callable

from shared.messages  import MsgType, encode, decode
from shared.constants import SERVER_HOST, SERVER_PORT, PROTOCOL_VERSION


class NetworkClient:
    """
    Roda o WebSocket em uma thread separada (asyncio loop próprio).
    O game loop Pygame interage via inbox/outbox thread-safe.
    """

    def __init__(self, host: str = SERVER_HOST, port: int = SERVER_PORT):
        self.host = host
        self.port = port

        # Filas thread-safe
        self.inbox:  queue.Queue = queue.Queue()   # (MsgType, payload, seq, ts)
        self.outbox: queue.Queue = queue.Queue()   # (MsgType, payload)

        self.connected    = False
        self.latency_ms   = 0      # RTT mais recente
        self._seq         = 0
        self._ping_ts     = 0

        self._thread: threading.Thread | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._ws      = None

    # ── API do game loop ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """Inicia a thread de rede. Não bloqueia o game loop."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def send(self, msg_type: MsgType, payload: dict) -> None:
        """Enfileira uma mensagem para envio. Thread-safe."""
        self.outbox.put((msg_type, payload))

    def poll(self) -> list[tuple[MsgType, dict, int, int]]:
        """
        Retorna todas as mensagens recebidas desde o último poll.
        Deve ser chamado a cada frame pelo game loop.
        """
        messages = []
        try:
            while True:
                messages.append(self.inbox.get_nowait())
        except queue.Empty:
            pass
        return messages

    def disconnect(self) -> None:
        """Encerra a conexão e a thread de rede."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    def login(self, username: str, password: str,
              ap: float = 0.0, max_hp: int = 0) -> None:
        """Envia LOGIN com stats reais do personagem para o servidor usar."""
        import hashlib
        ph = hashlib.sha256(password.encode()).hexdigest()
        self.send(MsgType.LOGIN, {
            "username": username,
            "password": ph,
            "version":  PROTOCOL_VERSION,
            "ap":       ap,       # attack_power real do cliente
            "max_hp":   max_hp,   # max_hp real do cliente
        })

    def move(self, tx: int, ty: int) -> None:
        self.send(MsgType.MOVE, {"tx": tx, "ty": ty})

    def cast_skill(self, sid: str, tid: int = -1,
                   tx: int = 0, ty: int = 0,
                   dir_x: float = 0, dir_y: float = 0) -> None:
        self.send(MsgType.CAST_SKILL, {
            "sid": sid, "tid": tid,
            "tx": tx, "ty": ty,
            "dir_x": dir_x, "dir_y": dir_y,
        })

    def ping(self) -> None:
        self._ping_ts = int(time.time() * 1000)
        self.send(MsgType.PING, {"client_ts": self._ping_ts})

    # ── Thread de rede (asyncio em background) ────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_run())
        finally:
            self._loop.close()

    async def _connect_and_run(self) -> None:
        import websockets
        uri = f"ws://{self.host}:{self.port}"
        print(f"[Network] conectando em {uri}")
        try:
            async with websockets.connect(uri) as ws:
                self._ws       = ws
                self.connected = True
                print("[Network] conectado.")
                await asyncio.gather(
                    self._recv_loop(ws),
                    self._send_loop(ws),
                    self._ping_loop(),
                )
        except Exception as e:
            print(f"[Network] erro de conexão: {e}")
        finally:
            self.connected = False
            print("[Network] desconectado.")

    async def _recv_loop(self, ws) -> None:
        """Recebe mensagens do servidor e coloca na inbox."""
        async for raw in ws:
            try:
                msg_type, payload, seq, ts = decode(raw)
                # Latência: processa PONG aqui mesmo
                if msg_type == MsgType.PONG and self._ping_ts:
                    self.latency_ms = int(time.time() * 1000) - self._ping_ts
                self.inbox.put((msg_type, payload, seq, ts))
            except ValueError:
                pass   # mensagem malformada — ignora

    async def _send_loop(self, ws) -> None:
        """Consome a outbox e envia mensagens ao servidor."""
        while True:
            try:
                msg_type, payload = self.outbox.get_nowait()
                self._seq += 1
                raw = encode(msg_type, payload, seq=self._seq)
                await ws.send(raw)
            except queue.Empty:
                await asyncio.sleep(0.005)   # 5ms — não queima CPU

    async def _ping_loop(self) -> None:
        """Envia ping a cada 5s para medir latência."""
        while True:
            await asyncio.sleep(5)
            self.ping()

    async def _close(self) -> None:
        if self._ws:
            await self._ws.close()
