"""
protocol_client.py — cliente WebSocket assíncrono fino, fala só o
protocolo (`shared/messages.py::encode`/`decode`) — NUNCA reaproveita
`client/network.py` de propósito: aquele é uma ponte thread+fila
pensada pro loop síncrono do pygame; aqui tudo já é asyncio nativo, uma
ponte a mais só adicionaria complexidade sem ganho (04/09/2026).
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import websockets
from websockets.protocol import State

from shared.messages import MsgType, decode, encode


class ProtocolClient:
    def __init__(self, host: str, port: int):
        self._uri = f"ws://{host}:{port}"
        self._ws: "websockets.WebSocketClientProtocol | None" = None
        self._seq = 0

    async def connect(self, timeout: float = 10.0) -> None:
        self._ws = await asyncio.wait_for(websockets.connect(self._uri), timeout=timeout)

    async def send(self, msg_type: MsgType, payload: dict) -> None:
        if self._ws is None:
            raise RuntimeError("ProtocolClient.send chamado sem connect()")
        self._seq += 1
        await self._ws.send(encode(msg_type, payload, seq=self._seq))

    async def recv_one(self, timeout: float = 10.0) -> "tuple[MsgType, dict, int, int] | None":
        """Lê 1 mensagem (descarta malformadas até achar uma válida ou
        estourar o timeout) — usado pelo fluxo simples de request/response
        de `provision.py` (registro/login/criação, sem tráfego concorrente).
        `None` no timeout (conexão pendurada) em vez de lançar, pro
        chamador decidir o que fazer sem try/except em toda chamada."""
        assert self._ws is not None
        try:
            return await asyncio.wait_for(self._recv_one_inner(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _recv_one_inner(self) -> "tuple[MsgType, dict, int, int]":
        while True:
            raw = await self._ws.recv()
            try:
                return decode(raw)
            except ValueError:
                continue

    async def messages(self) -> AsyncIterator[tuple[MsgType, dict, int, int]]:
        """Gerador assíncrono de (msg_type, payload, seq, ts). Mensagem
        malformada é descartada silenciosamente (não deveria acontecer
        vindo do servidor real, mas nunca derruba o bot por isso)."""
        assert self._ws is not None
        async for raw in self._ws:
            try:
                yield decode(raw)
            except ValueError:
                continue

    @property
    def closed(self) -> bool:
        return self._ws is None or self._ws.state is not State.OPEN

    async def close(self) -> None:
        if self._ws is not None and self._ws.state is State.OPEN:
            await self._ws.close()
