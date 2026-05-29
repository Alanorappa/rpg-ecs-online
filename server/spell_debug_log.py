"""
server/spell_debug_log.py
Log de debug para spells do Mago — escreve em logs/spells_debug.log.

Padrão do projeto: todos os logs vão para a pasta  logs/  na raiz.
Para desativar: SPELL_DEBUG = False
"""
import logging
import os
import datetime

SPELL_DEBUG = True

_log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)


class _MsFormatter(logging.Formatter):
    """Formatter com milissegundos — %f não funciona no strftime do Windows."""
    def formatTime(self, record, datefmt=None):
        ct = datetime.datetime.fromtimestamp(record.created)
        return ct.strftime("%H:%M:%S") + f".{ct.microsecond // 1000:03d}"


_logger = logging.getLogger("spell_debug")
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(_log_dir, "spells_debug.log"),
                               encoding="utf-8", mode="a")
    _fh.setFormatter(_MsFormatter("%(asctime)s %(message)s"))
    _logger.addHandler(_fh)
    _logger.setLevel(logging.DEBUG)


def splog(msg: str) -> None:
    if SPELL_DEBUG:
        _logger.debug(msg)
