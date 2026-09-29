"""
hotbar_slot_debug.py — Debug temporário da reconciliação de
skills_hotbar (28-29/09/2026, bug real relatado pelo usuário: "Fatiador
de Corpos" apareceu no slot "5" ao desbloquear dentro da BG, depois
mudou pra "F", só terminando no slot certo "T" — mesmo já existindo
uma preferência salva pra ela em config.json).

Investigação por simulação (código atual, dados reais do personagem)
NÃO reproduziu o sintoma — o mecanismo (`ui/skill_slot_prefs.py::
preferred_slot_for_skill` + bloco `skills_hotbar` em
`client/network_handlers.py::_handle_msg_stats_update`) foi pro slot
certo de primeira em todo cenário simulado, incluindo o interloper já
catalogado (Punho no Queixo, ver PROBLEMAS_ARQUITETURA.md §74). Este
log existe pra capturar a sequência REAL de payloads na próxima
reprodução em jogo, em vez de continuar simulando às escuras.

Para ativar: `set RPG_DEBUG_HOTBAR_SLOT=1` antes de rodar o cliente.
Log salvo em `logs/hotbar_slot_debug.log` (criado automaticamente).

Eventos:
  STATS_UPDATE_RAW   payload["skills_hotbar"] bruto (array esparso do
                      servidor) + in_instance resolvido (payload vs
                      componente) + prefs salvas do personagem
  REMOVED            skill que estava em ps.skills mas saiu do payload
                      — mostra skill_id + slot antigo
  PLACED             skill nova colocada — skill_id + slot escolhido +
                      preferência salva (se houver) — se slot != prefs,
                      mostra por que caiu no fallback
  SKIPPED_NO_SLOT     preferred_slot_for_skill devolveu None (raro —
                      só quando NENHUM slot está livre)
"""
from __future__ import annotations
import time
from pathlib import Path

import os as _os
DBG_ENABLED: bool = _os.environ.get("RPG_DEBUG_HOTBAR_SLOT", "") not in ("", "0")

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "hotbar_slot_debug.log"


def _ts() -> str:
    t  = time.time()
    ms = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _HotbarSlotDebugLog:
    """Singleton — use a instância global ``HSD``."""

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
        self._fh.write(f"[{_ts()}] {event:<16s} {extra}\n")


HSD: _HotbarSlotDebugLog = _HotbarSlotDebugLog()
