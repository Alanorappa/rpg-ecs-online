"""
server/notify.py
Notificação por e-mail de eventos de conta (02/10/2026, pedido do
usuário — publicação no itch.io: avisar quando alguém cria conta,
cria personagem, ou loga).

Ponto único: `notify_async(subject, body)`. Dispara em background
(`loop.run_in_executor`, smtplib é bloqueante/síncrono — nunca rodar
direto na coroutine do handler, travaria o event loop single-thread
pra TODOS os players conectados enquanto o SMTP responde) e NUNCA
propaga exceção pro chamador — falha de e-mail é só um log, nunca
derruba o fluxo real de registro/login/criação de personagem.

Configurado via env vars (mesmo padrão de `server/auth.py::
DATABASE_URL`, `RPG_DATABASE_URL`) — em produção, setadas em
`/opt/rpg_ecs_online/.env` (`EnvironmentFile=` do unit
`rpg-server.service`). Sem elas, `notify_async` é no-op silencioso —
dev local nunca precisa configurar nada disto.
"""
from __future__ import annotations
import asyncio
import os
import smtplib
from email.mime.text import MIMEText

from server.log import log

# Lidas DENTRO das funções (nunca módulo-level) -- mesmo motivo do TLS
# (`server/main.py::_build_server_ssl_context`): permite testar as 2
# configurações (vazia/preenchida) sem reimportar o módulo, e garante
# que um `.env` recarregado (restart do serviço) sempre reflete valor
# atual, nunca o que existia no momento do import.
def _smtp_config() -> dict:
    return {
        "host":     os.environ.get("RPG_SMTP_HOST", ""),
        "port":     int(os.environ.get("RPG_SMTP_PORT", "587")),
        "user":     os.environ.get("RPG_SMTP_USER", ""),
        "password": os.environ.get("RPG_SMTP_PASS", ""),
        "to":       os.environ.get("RPG_NOTIFY_EMAIL_TO", ""),
    }

# Referência forte pras tasks em andamento -- sem isso o event loop só
# guarda referência FRACA e o GC pode coletar a task no meio do envio
# (mesma classe de bug já corrigida em `SessionManager._track_
# autosave_task`, ver `arquitetura/PROBLEMAS_ARQUITETURA.md` §161).
_pending_tasks: set = set()


def _configured(cfg: dict) -> bool:
    return bool(cfg["host"] and cfg["user"] and cfg["password"] and cfg["to"])


def _send_email_sync(cfg: dict, subject: str, body: str) -> None:
    """Chamada bloqueante de verdade -- SÓ roda dentro de
    `loop.run_in_executor`, nunca direto numa coroutine."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = cfg["user"]
    msg["To"]      = cfg["to"]
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as smtp:
        smtp.starttls()
        smtp.login(cfg["user"], cfg["password"])
        smtp.sendmail(cfg["user"], [cfg["to"]], msg.as_string())


def notify_async(subject: str, body: str) -> None:
    """Dispara o envio em background -- no-op silencioso se as env
    vars de SMTP não estiverem configuradas (dev local). Erro de envio
    (SMTP fora do ar, credencial errada, etc.) só vira log, nunca
    exceção pro chamador -- é sempre side-effect, nunca caminho crítico
    de registro/login/criação de personagem."""
    cfg = _smtp_config()
    if not _configured(cfg):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # sem event loop rodando (ex: script standalone) -- no-op seguro

    async def _run() -> None:
        try:
            await loop.run_in_executor(None, _send_email_sync, cfg, subject, body)
        except Exception as exc:
            log.error(f"[Notify] falha ao enviar e-mail ({subject!r}): {exc!r}")

    task = loop.create_task(_run())
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
