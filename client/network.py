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
import os
import queue
import ssl
import threading
import time
from typing import Callable

from shared.messages  import MsgType, encode, decode
from shared.constants import SERVER_HOST, SERVER_PORT, PROTOCOL_VERSION

# Backoff exponencial para reconnect: 1s → 2s → 4s → 8s → … até MAX
_RECONNECT_INITIAL_DELAY = 1.0
_RECONNECT_MAX_DELAY     = 30.0


def _build_uri(host: str, port: int, use_tls: bool) -> str:
    """`wss://` ou `ws://` — passar `ssl=` pra `websockets.connect()`
    com um URI `ws://` é erro na lib, por isso o esquema precisa
    refletir `use_tls` de verdade."""
    scheme = "wss" if use_tls else "ws"
    return f"{scheme}://{host}:{port}"


def _build_client_ssl_context(use_tls: bool) -> "ssl.SSLContext | None":
    """None (sem TLS) se `use_tls=False` -- default de sempre, conecta
    em `ws://` normal. Com `use_tls=True`, fixa ("pinned") o
    certificado autoassinado do servidor via `cafile=` (02/10/2026,
    pedido do usuário -- sem domínio, não dá pra usar uma autoridade
    certificadora pública; como o cliente é nosso, não um navegador de
    terceiro, confiar especificamente NESSE certificado é seguro e não
    precisa de CA nenhuma). Certificado público bundlado ao lado do
    exe/projeto em `certs/server_cert.pem` (`paths.resource_path()`,
    mesmo ponto único já usado por assets/maps pra resolver caminho
    tanto em dev quanto no `.exe` empacotado via PyInstaller).

    Arquivo ausente com `use_tls=True` levanta — NUNCA conecta sem TLS
    em silêncio quando TLS foi pedido explicitamente."""
    if not use_tls:
        return None
    from paths import resource_path
    cert_path = resource_path(os.path.join("certs", "server_cert.pem"))
    if not os.path.isfile(cert_path):
        raise FileNotFoundError(
            f"use_tls=True mas certificado não encontrado em {cert_path!r} -- "
            "build incompleto (falta certs/server_cert.pem) ou caminho errado.")
    return ssl.create_default_context(cafile=cert_path)


class NetworkClient:
    """
    Roda o WebSocket em uma thread separada (asyncio loop próprio).
    O game loop Pygame interage via inbox/outbox thread-safe.

    Reconexão automática: ao perder a conexão, tenta reconectar com
    backoff exponencial (1s → 2s → 4s → … → 30s).
    """

    def __init__(self, host: str = SERVER_HOST, port: int = SERVER_PORT,
                 use_tls: bool = False):
        self.host = host
        self.port = port
        self.use_tls = use_tls

        # Filas thread-safe
        self.inbox:  queue.Queue = queue.Queue()   # (MsgType, payload, seq, ts)
        self.outbox: queue.Queue = queue.Queue()   # (MsgType, payload)

        self.connected      = False
        self.reconnecting   = False   # True entre tentativas de reconexão
        self.latency_ms     = 0       # RTT mais recente
        self._seq           = 0
        self._ping_ts       = 0
        self._stop          = False   # True = encerrar permanentemente

        # Credenciais para re-login automático após reconnect
        self._login_username: str = ""
        self._login_password_hash: str = ""
        self._login_ap: float  = 0.0
        self._login_max_hp: int = 0

        self._thread: threading.Thread | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._ws      = None

    # ── API do game loop ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """Inicia a thread de rede. Não bloqueia o game loop."""
        self._stop  = False
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
        """Encerra a conexão permanentemente (sem reconnect).

        BLOQUEIA até o close de verdade sair (timeout curto de rede morta) —
        09/10/2026, ver PROBLEMAS_ARQUITETURA.md. `_close()` roda na thread
        de rede, que é daemon (`self._thread`, `daemon=True`); sem esperar o
        Future aqui, `main.py` seguia pra `SystemExit(0)` imediatamente
        depois de chamar `disconnect()`, e o processo podia morrer ANTES da
        thread daemon ter chance de rodar `await self._ws.close()` — corrida
        que fazia o close nem sempre chegar ao servidor, reproduzindo
        `already_online` de novo mesmo com o fix de `main.py` (§260) no
        lugar certo. Timeout de 2s: se a rede já estiver morta/travada, não
        trava o quit do jogo pra sempre — pior caso volta a ser o
        comportamento antigo (ping_timeout do servidor limpa depois).
        """
        self._stop = True
        if self._loop:
            future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
            try:
                future.result(timeout=2.0)
            except Exception:
                pass

    def login(self, username: str, password: str,
              ap: float = 0.0, max_hp: int = 0, password_hash: str = "") -> None:
        """Envia LOGIN com stats reais do personagem para o servidor usar.

        `password_hash` (08/10/2026, ver CLAUDE.md — auto-login vindo do
        Launcher): quando preenchido, usa ESSE hash direto, sem hashear
        `password` de novo — o Launcher só tem o hash (nunca guarda a
        senha crua pra "lembrar login"), então não tem como repassar a
        senha original pro jogo. `password` pode vir vazio nesse caso."""
        from shared.auth_utils import hash_password
        ph = password_hash or hash_password(password)
        # Guarda credenciais para re-login automático após reconnect
        self._login_username      = username
        self._login_password_hash = ph
        self._login_ap            = ap
        self._login_max_hp        = max_hp
        self.send(MsgType.LOGIN, {
            "username": username,
            "password": ph,
            "version":  PROTOCOL_VERSION,
            "ap":       ap,
            "max_hp":   max_hp,
        })

    def register(self, username: str, password: str,
                 class_id: str = "guerreiro", char_name: str = "") -> None:
        """Envia pedido de cadastro. Resposta chega como REGISTER_OK / REGISTER_ERROR."""
        from shared.auth_utils import hash_password
        ph = hash_password(password)
        self.send(MsgType.REGISTER, {
            "username":  username,
            "password":  ph,
            "class_id":  class_id,
            "char_name": char_name,
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

    def release_spirit(self) -> None:
        self.send(MsgType.RELEASE_SPIRIT, {})

    def revive_request(self) -> None:
        self.send(MsgType.REVIVE_REQUEST, {})

    def ping(self) -> None:
        self._ping_ts = int(time.time() * 1000)
        self.send(MsgType.PING, {"client_ts": self._ping_ts})

    # ── Thread de rede (asyncio em background) ────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_with_retry())
        finally:
            self._loop.close()

    async def _connect_with_retry(self) -> None:
        """Loop de conexão com backoff exponencial. Para quando _stop=True."""
        delay = _RECONNECT_INITIAL_DELAY
        first = True
        while not self._stop:
            if not first:
                self.reconnecting = True
                print(f"[Network] tentando reconectar em {delay:.0f}s…")
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)
            first = False

            try:
                await self._connect_and_run()
                delay = _RECONNECT_INITIAL_DELAY   # reconectou com sucesso → reseta backoff
            except Exception as e:
                if not self._stop:
                    print(f"[Network] erro de conexão: {e}")

        self.reconnecting = False

    async def _connect_and_run(self) -> None:
        import websockets
        uri = _build_uri(self.host, self.port, self.use_tls)
        ssl_context = _build_client_ssl_context(self.use_tls)
        print(f"[Network] conectando em {uri}")
        connect_kwargs = {"ssl": ssl_context} if self.use_tls else {}
        async with websockets.connect(uri, **connect_kwargs) as ws:
            self._ws        = ws
            self.connected  = True
            self.reconnecting = False
            print("[Network] conectado.")
            # Se há credenciais salvas, faz re-login automaticamente
            if self._login_username:
                self.send(MsgType.LOGIN, {
                    "username": self._login_username,
                    "password": self._login_password_hash,
                    "version":  PROTOCOL_VERSION,
                    "ap":       self._login_ap,
                    "max_hp":   self._login_max_hp,
                })
            try:
                await asyncio.gather(
                    self._recv_loop(ws),
                    self._send_loop(ws),
                    self._ping_loop(),
                )
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
