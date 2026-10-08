"""
server/http_api.py
API HTTP do Launcher (08/10/2026, pedido do usuário — ver CLAUDE.md).
Roda DENTRO do mesmo processo/event loop do servidor do jogo
(server/main.py), porta própria (LAUNCHER_API_PORT), TLS com o MESMO
certificado já usado pelo WebSocket — nunca um serviço systemd separado,
nunca gameplay passando por aqui.

Rotas:
    GET  /launcher/version   → {"version": GAME_VERSION, "sha256": "..."}
    POST /launcher/login     → {"ok": true}/{"ok": false} — delega 100%
                                pra server/auth.py::authenticate(), nunca
                                reimplementa verificação de senha aqui.
    POST /launcher/register  → {"ok": true}/{"ok": false, "reason": ...}
                                (09/10/2026) — delega 100% pra
                                server/auth.py::register(), MESMA função
                                que o REGISTER via WebSocket
                                (server/session.py::_handle_register) já
                                usa. Validação de username (3–20 chars)
                                espelhada aqui porque é a MESMA regra do
                                WS, nunca uma nova.
    GET  /launcher/download  → stream do zip mais recente

Artefatos de release ficam em `releases/` (ao lado de maps/certs/, raiz
do projeto) — gerados por release_tools/release_client.ps1, nunca por
este módulo (ele só LÊ o que já foi publicado).
"""
from __future__ import annotations

import os
from aiohttp import web

from server.log import log
from shared.constants import GAME_VERSION

RELEASES_DIR = "releases"
ZIP_NAME     = "RPG_Online.zip"       # nome FIXO — sempre o build mais recente
SHA_NAME     = "RPG_Online.sha256"


def _releases_path(*parts: str) -> str:
    return os.path.join(RELEASES_DIR, *parts)


async def _handle_version(request: web.Request) -> web.Response:
    sha_path = _releases_path(SHA_NAME)
    sha256 = ""
    if os.path.exists(sha_path):
        with open(sha_path, "r", encoding="utf-8") as f:
            sha256 = f.read().strip()
    return web.json_response({"version": GAME_VERSION, "sha256": sha256})


async def _handle_login(request: web.Request) -> web.Response:
    """Gate de conveniência pro Launcher — NUNCA abre sessão de jogo nem
    devolve personagens; o login de verdade (WS, server/session.py) roda
    de novo quando o jogo abre, igual hoje. Delega 100% pra
    server/auth.py::authenticate() — zero verificação de senha duplicada
    aqui."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "bad_request"}, status=400)

    username = str(body.get("username", ""))
    password_hash = str(body.get("password_hash", ""))
    if not username or not password_hash:
        return web.json_response({"ok": False, "reason": "missing_fields"}, status=400)

    from server.auth import authenticate
    result = await authenticate(username, password_hash)
    return web.json_response({"ok": result is not None})


async def _handle_register(request: web.Request) -> web.Response:
    """Cria conta nova a partir do Launcher — delega 100% pra
    server/auth.py::register(), a MESMA função que o REGISTER via
    WebSocket já usa (server/session.py::_handle_register). Validação de
    username espelhada daqui (3–20 chars) porque é a MESMA regra do WS,
    nunca lógica de senha/duplicidade nova."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "bad_request"}, status=400)

    username = str(body.get("username", "")).strip()
    password_hash = str(body.get("password_hash", ""))
    if not username or not password_hash or len(username) < 3 or len(username) > 20:
        return web.json_response({"ok": False, "reason": "invalid_input"}, status=400)

    from server.auth import register
    ok = await register(username, password_hash)
    if ok:
        from server.notify import notify_async
        notify_async("RPG Online - nova conta", f"Usuário: {username}")
    return web.json_response({"ok": ok, "reason": None if ok else "username_taken"})


async def _handle_download(request: web.Request) -> web.StreamResponse:
    zip_path = _releases_path(ZIP_NAME)
    if not os.path.exists(zip_path):
        return web.json_response({"error": "no_release_available"}, status=404)
    return web.FileResponse(zip_path)


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/launcher/version", _handle_version)
    app.router.add_post("/launcher/login", _handle_login)
    app.router.add_post("/launcher/register", _handle_register)
    app.router.add_get("/launcher/download", _handle_download)
    return app


async def start_http_api(host: str, port: int, ssl_context) -> web.AppRunner:
    """Sobe a API do launcher dentro do MESMO event loop do servidor de
    jogo. Retorna o AppRunner — chamador guarda a referência e chama
    `await runner.cleanup()` no shutdown (mesmo padrão de qualquer recurso
    assíncrono de vida longa já usado em server/world_server.py)."""
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port, ssl_context=ssl_context)
    await site.start()
    scheme = "https" if ssl_context is not None else "http"
    log.info(f"[Server] Launcher API em {scheme}://{host}:{port}")
    return runner
