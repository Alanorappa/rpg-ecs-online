"""
aoi_debug.py — Debug de sincronização AOI cliente↔servidor (spawn/despawn de mobs remotos).

Para ativar: setar DBG_ENABLED = True abaixo.
Log salvo em  logs/aoi_debug.log  (criado automaticamente).

Investiga: mob causa dano real mas não existe localmente em _remote_mobs —
sintoma observado: mob "invisível" + som de fallback (hit_normal do player)
em vez do som NpcSounds do mob (ex: "bite"/mordida).

────────────────────────────────────────────────────────────
Tipos de evento:
  SPAWN_RAW      entrada bruta recebida em payload["spawned"] (antes de filtrar kind)
  SPAWN_OK       _spawn_remote_mob criou entidade local e registrou em _remote_mobs
  SPAWN_SKIP     _spawn_remote_mob ignorado — server_eid já estava em _remote_mobs
  DESPAWN_AOI    despawn via AOI_UPDATE["despawned"]
  DESPAWN_ENT    despawn via ENTITY_DESPAWN
  COMBAT_FALLBACK combat_result com attacker fora de _remote_mobs (som genérico tocado)
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
from pathlib import Path

# ── Ativação por env var (nunca constante no código) ─────────────────────────
# `set RPG_DEBUG_AOI=1` antes de rodar. Ver debug/archer_debug.py pro porquê
# (flag esquecida ligada degradou sessão de teste real — item D3, seção 11 de
# PROBLEMAS_ARQUITETURA.md).
import os as _os
DBG_ENABLED: bool = _os.environ.get("RPG_DEBUG_AOI", "") not in ("", "0")
# ─────────────────────────────────────────────────────────────────────────────

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "aoi_debug.log"


def _ts() -> str:
    t  = time.time()
    ms = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _AoiDebugLog:
    """Singleton — use a instância global ``AOI_DBG``."""

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
        self._fh.write(f"[{_ts()}] {event:<15s} {extra}\n")


# Instância global
AOI_DBG: _AoiDebugLog = _AoiDebugLog()
