"""
server/log.py — logger único do servidor (item D4 da auditoria,
PROBLEMAS_ARQUITETURA.md §11: print() sem nível/timestamp/rotação).

Uso:
    from server.log import log
    log.info("[World] spawn player eid=%s", eid)   # ou f-string direto

- Console com timestamp (HH:MM:SS.mmm) + nível.
- Nível via env var RPG_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR; default INFO).
- Arquivo rotativo logs/server.log (2 MB × 3 backups) — o console continua
  sendo a visão principal em dev; o arquivo cobre "o que aconteceu ontem".
"""
from __future__ import annotations
import logging
import logging.handlers
import os
import datetime

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)


class _MsFormatter(logging.Formatter):
    """Timestamp com milissegundos — %f não funciona no strftime do Windows.
    Mesmo formatter de server/spell_debug_log.py (padrão do projeto)."""
    def formatTime(self, record, datefmt=None):
        ct = datetime.datetime.fromtimestamp(record.created)
        return ct.strftime("%H:%M:%S") + f".{ct.microsecond // 1000:03d}"


log = logging.getLogger("server")
if not log.handlers:
    _fmt = _MsFormatter("%(asctime)s %(levelname)-7s %(message)s")

    _con = logging.StreamHandler()
    _con.setFormatter(_fmt)
    log.addHandler(_con)

    _file = logging.handlers.RotatingFileHandler(
        os.path.join(_LOG_DIR, "server.log"),
        maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _file.setFormatter(_fmt)
    log.addHandler(_file)

    log.setLevel(os.environ.get("RPG_LOG_LEVEL", "INFO").upper())
    log.propagate = False

    # Logger nativo do asyncio (avisos de callback lento — "Executing
    # <Task ...> took X.XXX seconds", disparado por
    # `loop.slow_callback_duration`, ver server/main.py) — sem handler
    # plugado nele antes disso, esses avisos ficavam mudos (Fase 4.5,
    # 11/08/2026, ver PROBLEMAS_ARQUITETURA.md §27). Reusa o MESMO
    # RotatingFileHandler acima em vez de abrir arquivo novo.
    _asyncio_log = logging.getLogger("asyncio")
    if not _asyncio_log.handlers:
        _asyncio_log.addHandler(_file)
        _asyncio_log.setLevel("WARNING")
        _asyncio_log.propagate = False
