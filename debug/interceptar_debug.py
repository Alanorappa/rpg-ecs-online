"""
interceptar_debug.py — Debug do rollback do Interceptar (06/08/2026,
relatado pelo usuário: dash anima INTEIRO e depois é desfeito de
repente). Já confirmado que a predição visual local foi removida em
22/07/2026 (ver ui/systems.py:4769-4780) — o rollback vem de OUTRA
fonte, provavelmente uma segunda correção de posição (movimento normal/
perseguição) chegando DEPOIS da confirmação do dash. Este módulo só
instrumenta os pontos candidatos pra ver a sequência REAL de eventos —
sem fix ainda, ver arquitetura/ARQUITETURA_ONLINE.md §34.74.50.

Para ativar: `set RPG_DEBUG_INTERCEPTAR=1` (Windows) ANTES de rodar —
servidor e cliente são processos separados, cada um escreve seu próprio
log local em `debug/logs/interceptar_debug.log`. Mesmo padrão de
debug/aoi_debug.py — nunca ativado por constante fixa no código (ver
debug/archer_debug.py pro porquê: flag esquecida ligada degradou uma
sessão de teste real).

────────────────────────────────────────────────────────────
Tipos de evento (servidor):
  DASH_OK        Interceptar teve sucesso — posição confirmada enviada
                 ao caster (is_dash=True)
  DASH_REJECTED  Interceptar falhou — correção "rejected=True" enviada
  MOVE_REQUEST   MOVE normal do player processado (aceito ou rejeitado)
                 no mesmo tick — candidato a colidir com o dash

Tipos de evento (cliente), todos em _handle_msg_entity_move pro próprio eid:
  DASH_KEEP      já dashando pro mesmo tile que a mensagem confirma — mantém
  DASH_FORCED    correção com duration explícito (ex: knockback) — preempta
  DASH_START     inicia a animação de dash agora (fluxo normal do Interceptar)
  DESYNC_CORR    branch de correção de desync genuína — CANCELA dash em
                 andamento se houver (é aqui que o "rollback" acontece)
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
from pathlib import Path

# ── Ativação por env var (nunca constante no código, ver docstring acima) ──
import os as _os
DBG_ENABLED: bool = _os.environ.get("RPG_DEBUG_INTERCEPTAR", "") not in ("", "0")
# ─────────────────────────────────────────────────────────────────────────

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "interceptar_debug.log"


def _ts() -> str:
    t  = time.time()
    ms = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _InterceptarDebugLog:
    """Singleton — use a instância global ``INTERCEPTAR_DBG``."""

    def __init__(self) -> None:
        self._fh = None

    def _open(self) -> None:
        if self._fh is None:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
            sep = "=" * 76
            self._fh.write(f"{sep}\n[SESSION] {time.strftime('%Y-%m-%d %H:%M:%S')}\n{sep}\n")

    def log(self, event: str, **kwargs) -> None:
        if not DBG_ENABLED:
            return
        self._open()
        extra = "  ".join(f"{k}={v}" for k, v in kwargs.items())
        self._fh.write(f"[{_ts()}] {event:<14s} {extra}\n")


# Instância global
INTERCEPTAR_DBG: _InterceptarDebugLog = _InterceptarDebugLog()
