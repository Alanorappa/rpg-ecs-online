"""
mob_combat_debug.py — Debug de combate de mobs.

Para ativar: setar DBG_ENABLED = True abaixo.
Log salvo em  logs/mob_combat.log  (criado automaticamente).

Descreve o ciclo completo de cada mob: aggro → perseguição →
ataque / bloqueio → retorno / sleep.

────────────────────────────────────────────────────────────
Formato de cada linha:
  [HH:MM:SS.mmm] TIPO        eid=N  Nome(Raça/Classe)  chave=valor ...

Tipos de evento:
  AGGRO      mob detectou player por proximidade → AGGRO_DELAY
  AGGRO_DMG  mob aggroed por receber dano
  CHASE      AGGRO_DELAY → CHASING
  IN_RANGE   chegou ao alcance de ataque → ATTACKING
  ATK_FIRE   ataque disparado (melee ou ranged)
  ATK_CAST   ranged iniciou cast timer (antes de disparar)
  ATK_BLOCK  ataque BLOQUEADO neste tick (com motivo)
  KITING     mob recuou por estar perto demais do player
  LEASH      ultrapassou raio de leash → RETURNING
  LOST_TGT   perdeu alvo (grace expirou) → IDLE
  SLEEP      suspenso — player > SLEEP_RADIUS_TILES tiles
  INVISIBLE  player ficou invisível → RETURNING
  ABILITY    habilidade disparada (EnemyAbilitySystem)
  ABILITY_BLK habilidade bloqueada — sem LOS
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import os
import time
from pathlib import Path

# ── Ative aqui ───────────────────────────────────────────────────────────────
DBG_ENABLED: bool = False   # <<< mude para True para gravar o log
# ─────────────────────────────────────────────────────────────────────────────

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "mob_combat.log"

# Intervalo mínimo entre logs ATK_BLOCK por mob (s) — evita spam de 20 linhas/s
_ATK_BLOCK_INTERVAL = 2.0


def _ts() -> str:
    t   = time.time()
    ms  = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _MobCombatLog:
    """Singleton — use a instância global ``MCL``."""

    def __init__(self) -> None:
        self._fh = None
        # Timers rate-limit por mob para ATK_BLOCK (eid → segundos até próximo log)
        self._block_timers: dict[int, float] = {}
        # Estado anterior por mob — para detectar mudanças não instrumentadas
        self._prev_states:  dict[int, str]   = {}

    @property
    def DBG_ENABLED(self) -> bool:
        """Lê o flag do módulo — permite alterar em runtime sem re-importar."""
        import sys
        mod = sys.modules.get("mob_combat_debug")
        return mod.DBG_ENABLED if mod else False

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _open(self) -> None:
        if self._fh is None:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
            sep = "=" * 76
            self._fh.write(f"{sep}\n[SESSION] {time.strftime('%Y-%m-%d %H:%M:%S')}\n{sep}\n")

    def _write(self, line: str) -> None:
        self._fh.write(line + "\n")

    # ── API pública ───────────────────────────────────────────────────────────

    def log(self, event: str, eid: int, name: str, race: str, cls: str = "",
            **kwargs) -> None:
        """Grava uma linha de log.

        ``event``   — tipo de evento (8 chars, preenchido com espaços)
        ``eid``     — entity id do mob
        ``name``    — nome do mob (ex: "Goblin 3")
        ``race``    — raça  (ex: "Humanoide")
        ``cls``     — entity_class  (ex: "Hunter", "Warrior")
        ``**kwargs``— pares chave=valor adicionais
        """
        if not DBG_ENABLED:
            return
        self._open()
        ident = f"eid={eid:<4d}  {name}({race}/{cls})"
        extra = "  ".join(f"{k}={v}" for k, v in kwargs.items())
        self._write(f"[{_ts()}] {event:<12s} {ident:<34s} {extra}")

    def log_state(self, eid: int, name: str, race: str, cls: str,
                  old: str, new: str, reason: str = "") -> None:
        """Registra mudança de estado se old != new."""
        if not DBG_ENABLED or old == new:
            return
        r = f"  ({reason})" if reason else ""
        self.log("STATE", eid, name, race, cls,
                 transition=f"{old}→{new}{r}")

    def log_atk_block(self, eid: int, name: str, race: str, cls: str,
                      dt: float, reasons: list[str]) -> None:
        """Rate-limited: log de ataque bloqueado (no máximo 1 a cada _ATK_BLOCK_INTERVAL s)."""
        if not DBG_ENABLED:
            return
        timer = self._block_timers.get(eid, 0.0) - dt
        if timer > 0:
            self._block_timers[eid] = timer
            return
        self._block_timers[eid] = _ATK_BLOCK_INTERVAL
        self.log("ATK_BLOCK", eid, name, race, cls,
                 why="|".join(reasons) if reasons else "?")

    def reset_block_timer(self, eid: int) -> None:
        """Reseta o rate-limit de ATK_BLOCK quando um ataque é disparado."""
        if DBG_ENABLED:
            self._block_timers.pop(eid, None)


# Instância global
MCL: _MobCombatLog = _MobCombatLog()
