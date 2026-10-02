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
import ssl
import ctypes

# Comentário antigo dizia que o servidor precisava disso por importar
# EnemyAISystem/CombatSystem de ui/systems.py — desatualizado (essas
# classes vêm de engine/world_systems.py há tempo, e o débito B3 que
# ainda arrastava pygame via ui.systems.SkillSystem foi fechado
# 08/08/2026, ver engine/skill_handlers.py). Mantido como rede de
# segurança barata contra qualquer import futuro que volte a arrastar
# pygame sem querer, já que o processo inteiro roda headless (container
# Linux sem display, CI). setdefault: não sobrescreve se o operador já
# setou algo explicitamente (ex: rodar com display real por algum motivo).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

# Garante que a raiz do projeto está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# IMPORTANTE: qualquer import `server.*`/`shared.*` só funciona DEPOIS do
# sys.path.insert acima quando este arquivo roda como script
# (`python server/main.py`) — import no topo quebra com ModuleNotFoundError.
# Regressão real (15/07/2026): a migração print→logging injetou
# `from server.log import log` antes do bootstrap e o servidor não subia;
# a suíte não pegou porque importa módulos como pacote, nunca executa o
# entry point — ver tests/test_server_entrypoint.py.
from server.log import log

try:
    import websockets
except ImportError:
    log.error("[ERRO] websockets não instalado. Execute: pip install websockets")
    sys.exit(1)

import gc as _gc
_gc.disable()   # GC manual — evita pauses de 50-200ms no loop de ticks.
                # Coleta periódica é feita manualmente em WorldServer.run().

from server.auth         import init_db
from server.world_server import WorldServer
from server.session      import SessionManager
from shared.constants    import SERVER_HOST, SERVER_PORT, PROTOCOL_VERSION


def _build_server_ssl_context(cert_path: str, key_path: str) -> "ssl.SSLContext | None":
    """None (sem TLS, `ws://` normal) se `cert_path`/`key_path` vierem
    vazios — default de dev local, zero configuração extra precisa
    (02/10/2026, pedido do usuário: publicar o jogo exige TLS, mas sem
    domínio -- certificado autoassinado, SAN=IP do servidor, fixado no
    cliente via `cafile=`, ver `client/network.py::
    _build_client_ssl_context`). Lido via env vars
    (`RPG_TLS_CERT`/`RPG_TLS_KEY`) no mesmo padrão já usado por
    `server/auth.py::DATABASE_URL` (`RPG_DATABASE_URL`) -- em produção,
    setado em `/opt/rpg_ecs_online/.env` (`EnvironmentFile=` do unit
    `rpg-server.service`), nunca hardcoded.

    Se as env vars vierem PREENCHIDAS mas o arquivo não existir,
    `load_cert_chain` levanta — NUNCA cai pra `ws://` em silêncio
    quando o operador pediu TLS explicitamente (falha alto, visível no
    boot, em vez de servir sem criptografia sem avisar)."""
    if not cert_path or not key_path:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


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
    # Diagnóstico nativo do asyncio (Fase 4.5, 11/08/2026, ver
    # PROBLEMAS_ARQUITETURA.md §27) — avisa via logger "asyncio" (ligado
    # a logs/server.log em server/log.py) sempre que um callback do
    # event loop trava por mais de 50ms. Sinal INDEPENDENTE do profiler
    # próprio (_perf_mark só mede o que está explicitamente instrumentado
    # dentro de `_tick`) — pega stall em qualquer callback, incluindo
    # processamento de mensagem de rede/I/O que não passa por `_tick`.
    # `set_debug(True)` completo FICA DE FORA de propósito: adiciona
    # overhead a TODO callback (rastreamento de origem, etc.), arriscado
    # com o orçamento de tick já apertado (33ms) — só o sinalizador leve
    # (`slow_callback_duration`) é ligado por padrão. `asyncio.run()`
    # não dá hook pra configurar o loop ANTES dele rodar, por isso isso
    # fica aqui dentro (primeira linha da coroutine), não em __main__.
    asyncio.get_running_loop().slow_callback_duration = 0.05

    # Inicializa banco de dados (cria contas de teste via _seed_test_accounts)
    # -- async desde a migração pra PostgreSQL/asyncpg (07/09/2026, §123)
    await init_db()

    # Inicializa o mundo e o gerenciador de sessões
    world = WorldServer(zone_id="world_main")
    mgr   = SessionManager(world)
    world._session_manager = mgr  # referência para saves pontuais (XP/level)

    # gc.freeze() (05/09/2026, ver PROBLEMAS_ARQUITETURA.md §113) — o
    # gc.collect() manual periódico (WorldServer.run(), a cada ~10s)
    # ainda escaneava TODO objeto rastreado vivo (mapas/catálogos/
    # entidades carregados no boot), mesmo esses nunca virando lixo —
    # 20-50ms de pausa a cada disparo, medido em teste de carga real.
    # gc.disable() (linha acima) só evita a coleta AUTOMÁTICA; não isenta
    # o collect() manual de escanear esse baseline. freeze() move tudo
    # que existe ATÉ AQUI (mundo carregado, ninguém ainda conectado) pra
    # uma geração permanente que nenhum collect() futuro revisita — só o
    # lixo de churn pós-boot (por tick) é escaneado dali em diante.
    _gc.collect()
    _gc.freeze()

    ssl_context = _build_server_ssl_context(
        os.environ.get("RPG_TLS_CERT", ""), os.environ.get("RPG_TLS_KEY", ""))
    scheme = "wss" if ssl_context is not None else "ws"

    log.info(f"[Server] RPG Online v{PROTOCOL_VERSION}")
    log.info(f"[Server] WebSocket em {scheme}://{host}:{port}")
    log.info(f"[Server] Ctrl+C para encerrar\n")

    # Roda servidor WebSocket e loop de ticks em paralelo
    async with websockets.serve(
        lambda ws: handle_connection(ws, mgr),
        host, port,
        ssl=ssl_context,        # None = ws:// normal (dev local, default)
        max_size=1_048_576,    # 1 MB máximo por mensagem
        ping_interval=20,      # keepalive
        ping_timeout=60,
    ):
        try:
            await world.run()  # loop de ticks — roda indefinidamente
        except KeyboardInterrupt:
            # Sem isso, Ctrl+C matava o processo sem salvar NINGUÉM
            # conectado (05/08/2026, bug real relatado pelo usuário: "a
            # penúltima vez que eu tinha logado, havia salvo a config...
            # agora... as habilidades não estavam na mesma configuração" —
            # tudo desde o último autosave/5min se perdia, não só a
            # hotbar). O `except KeyboardInterrupt` original só existia lá
            # embaixo, em `__main__`, DEPOIS do `asyncio.run()` já ter
            # fechado o loop — tarde demais pra rodar mais async. Captura
            # AQUI, ainda dentro do loop vivo, e reaproveita o MESMO
            # chokepoint do autosave periódico (`SessionManager.
            # _autosave_all` — já pula sozinho quem estiver dentro de uma
            # BG, via `_persist_character`/`is_in_normalized_progression`,
            # nenhuma lógica nova de guard precisa entrar aqui).
            log.info("[Server] Ctrl+C recebido — salvando players conectados antes de encerrar...")
            await mgr._autosave_all()
            # Encerra qualquer _run_dispatch ainda pendente AQUI, ainda
            # dentro do loop vivo — sem isso, uma corrida entre este
            # Ctrl+C e a limpeza automática do asyncio.run() podia deixar
            # a exceção de uma dessas tasks "sem ser retirada", gerando um
            # ImportError cosmético já com o interpretador em desligamento
            # (ver SessionManager.shutdown_dispatch_tasks).
            await mgr.shutdown_dispatch_tasks()
            raise


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
            log.info("[Server] Windows timer: resolução elevada para 1ms")
        except Exception:
            pass

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        log.info("\n[Server] encerrado.")
    finally:
        if _timer_set:
            ctypes.windll.winmm.timeEndPeriod(1)
