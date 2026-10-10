"""
tests/test_network_disconnect_blocks.py

`NetworkClient.disconnect()` precisa ESPERAR o close de verdade (await
ws.close()) terminar antes de devolver o controle pro chamador — ver
client/network.py::disconnect() e PROBLEMAS_ARQUITETURA.md (09/10/2026).

Sem isso, `main.py` seguia pra `SystemExit(0)` logo depois de chamar
`disconnect()`, e como a thread de rede é daemon, o processo podia morrer
ANTES do close chegar a sair pela rede de verdade — reproduzindo
`already_online` no login seguinte, mesmo com o fix de main.py (§260) no
lugar certo.

Revert-to-confirm: com o `asyncio.run_coroutine_threadsafe(...)` antigo
(sem `.result()`), este teste falha de forma determinística — o `_close()`
fake tem um `sleep` que dá tempo de sobra pro `disconnect()` "sem esperar"
retornar ANTES da flag ser setada.
"""
import asyncio
import threading
import time

from client.network import NetworkClient


class _FakeWS:
    def __init__(self, closed_event: threading.Event):
        self._closed_event = closed_event

    async def close(self) -> None:
        await asyncio.sleep(0.05)
        self._closed_event.set()


def _start_bare_loop(client: NetworkClient) -> threading.Thread:
    """Sobe só o event loop da thread de rede, sem conectar de verdade
    (equivalente ao que `_run_loop`/`connect()` fariam, sem o websocket
    real) — o suficiente pro `disconnect()` ter um `self._loop` pra agendar
    `_close()` nele."""
    def _runner():
        loop = asyncio.new_event_loop()
        client._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    while client._loop is None:
        time.sleep(0.001)
    return t


def test_disconnect_blocks_until_close_completes():
    client = NetworkClient()
    closed_event = threading.Event()
    client._ws = _FakeWS(closed_event)
    _start_bare_loop(client)

    client.disconnect()

    # Se disconnect() voltou SEM esperar o close, a flag ainda não foi
    # setada aqui (o fake close só seta depois de 50ms) — é exatamente a
    # corrida real que causava already_online.
    assert closed_event.is_set(), (
        "disconnect() voltou antes do close() de verdade terminar — "
        "mesma corrida que causava already_online (ver PROBLEMAS_ARQUITETURA.md)"
    )

    client._loop.call_soon_threadsafe(client._loop.stop)


def test_disconnect_does_not_hang_when_ws_close_never_returns():
    """Rede morta/travada: disconnect() não pode travar o quit do jogo pra
    sempre — o timeout de 2s precisa valer."""
    class _HangingWS:
        async def close(self) -> None:
            await asyncio.sleep(999)

    client = NetworkClient()
    client._ws = _HangingWS()
    _start_bare_loop(client)

    start = time.monotonic()
    client.disconnect()
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"disconnect() travou por {elapsed:.1f}s — timeout não funcionou"

    client._loop.call_soon_threadsafe(client._loop.stop)
