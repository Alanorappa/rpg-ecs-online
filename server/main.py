"""
server/main.py
Ponto de entrada do servidor. Inicia o WebSocket e o loop de ticks.

Uso:
    python server/main.py
    python server/main.py --host 0.0.0.0 --port 8765
"""
from __future__ import annotations
import asyncio
import argparse
import sys
import os
import ctypes

# systems.py importa pygame no topo (servidor importa EnemyAISystem/CombatSystem
# de lá) — sem driver de vídeo/áudio, pygame.init() crasha em ambiente headless
# (container Linux sem display, CI). setdefault: não sobrescreve se o operador já
# setou algo explicitamente (ex: rodar com display real por algum motivo).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

# Garante que a raiz do projeto está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
except ImportError:
    print("[ERRO] websockets não instalado. Execute: pip install websockets")
    sys.exit(1)

import gc as _gc
_gc.disable()   # GC manual — evita pauses de 50-200ms no loop de ticks.
                # Coleta periódica é feita manualmente em WorldServer.run().

from server.auth         import init_db
from server.world_server import WorldServer
from server.session      import SessionManager
from shared.constants    import SERVER_HOST, SERVER_PORT, PROTOCOL_VERSION


async def handle_connection(ws, session_manager: SessionManager) -> None:
    """Callback chamado para cada nova conexão WebSocket."""
    import uuid
    session_id = str(uuid.uuid4())[:8]
    session    = await session_manager.on_connect(ws, session_id)

    try:
        async for raw_message in ws:
            await session_manager.on_message(session, raw_message)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await session_manager.on_disconnect(session_id)


async def main(host: str, port: int) -> None:
    # Inicializa banco de dados (cria contas de teste via _seed_test_accounts)
    init_db()

    # Inicializa o mundo e o gerenciador de sessões
    world = WorldServer(zone_id="world_main")
    mgr   = SessionManager(world)
    world._session_manager = mgr  # referência para saves pontuais (XP/level)

    print(f"[Server] RPG Online v{PROTOCOL_VERSION}")
    print(f"[Server] WebSocket em ws://{host}:{port}")
    print(f"[Server] Ctrl+C para encerrar\n")

    # Roda servidor WebSocket e loop de ticks em paralelo
    async with websockets.serve(
        lambda ws: handle_connection(ws, mgr),
        host, port,
        max_size=1_048_576,    # 1 MB máximo por mensagem
        ping_interval=20,      # keepalive
        ping_timeout=60,
    ):
        await world.run()      # loop de ticks — roda indefinidamente


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RPG ECS — Servidor Online")
    # 0.0.0.0 = escuta em TODAS as interfaces (LAN/internet). O default antigo
    # (SERVER_HOST="localhost", constante pensada pro CLIENTE) fazia o servidor
    # aceitar conexão só da própria máquina — port forwarding do roteador nunca
    # chegava nele. Pra voltar ao modo só-local: --host localhost.
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    args = parser.parse_args()

    # Windows: eleva resolução do timer de ~15ms → 1ms para asyncio.sleep preciso.
    # Sem isso, ticks a 30+ TPS ficam instáveis (jitter de ±10ms).
    _timer_set = False
    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            _timer_set = True
            print("[Server] Windows timer: resolução elevada para 1ms")
        except Exception:
            pass

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[Server] encerrado.")
    finally:
        if _timer_set:
            ctypes.windll.winmm.timeEndPeriod(1)
