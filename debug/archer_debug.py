"""
archer_debug.py — Debug do pipeline de auto-attack ranged do arqueiro
(client E server — mesmo módulo, dois arquivos de log separados porque
são processos diferentes e escrever no mesmo arquivo dos dois lados
arriscaria intercalar linhas de forma confusa).

Para ativar: setar DBG_ENABLED = True abaixo (client e server compartilham
o mesmo flag, já que é o mesmo arquivo importado nos dois lados).
Logs em  logs/archer_debug_server.log  e  logs/archer_debug_client.log
(criados automaticamente).

Objetivo: caçar o bug relatado pelo usuário — "do nada o arqueiro para de
atacar" + "desconta flecha da aljava sem gerar flecha/dano" — cobrindo
TODOS os pontos de decisão do pipeline (client E server) que determinam
se um tiro dispara, é bloqueado, consome flecha, toca som ou agra o mob.

────────────────────────────────────────────────────────────
Formato de cada linha:
  [HH:MM:SS.mmm][LADO] TIPO   player_eid=N target_eid=M  chave=valor ...

Tipos de evento:
  ATTEMPT   (server) chegou no ponto de decisão do auto-attack pro par
            player/alvo neste tick — sempre inclui dist/attack_range/
            cooldown_restante/quiver_arrows/is_pursuing, pra reconstruir
            o estado exato do tick.
  BLOCK     (server/client) ataque bloqueado neste tick — só loga na
            TRANSIÇÃO de motivo (evita spam de "esperando cooldown" a
            cada tick; qualquer MUDANÇA de motivo, inclusive
            "voltou a poder atirar", gera uma linha).
  LOS       (server/client) resultado da checagem de linha de visão
            (sempre logado quando checada, faz parte do BLOCK quando
            bloqueia, mas também logado quando dá OK pra confirmar que
            a checagem rodou).
  FIRE      (server) ataque disparado de verdade — outcome resolvido
            (hit/crit/miss/dodge/parry/evade), inclui dano.
  ARROW     (server) consumo REAL de flecha (arrows_before→arrows_after).
            (client) decremento LOCAL/otimista de flecha (previsão de UI,
            pode divergir do servidor — é exatamente o que causa o bug
            "desconta sem gerar flecha" se o client decrementa mas o
            server não confirma o disparo).
  AGGRO     (server) mob mudou de estado por causa deste ataque.
  SOUND     (client) som de disparo/nock/impacto tocado.
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
from pathlib import Path

# ── Ative aqui ───────────────────────────────────────────────────────────────
DBG_ENABLED: bool = False  # dev only — NUNCA em build de distribuição
# ─────────────────────────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).parent / "logs"


def _ts() -> str:
    t  = time.time()
    ms = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _ArcherDebugLog:
    """Uma instância por LADO (client/server) — cada uma com seu próprio
    arquivo, para não intercalar linhas de dois processos diferentes."""

    def __init__(self, side: str, filename: str) -> None:
        self._side = side
        self._path = _LOG_DIR / filename
        self._fh = None
        # Timestamp do último log periódico por (evento, player_eid) —
        # usado por log_periodic pra não gravar 30 linhas/s de "ainda em
        # cooldown, tudo normal" (ATTEMPT rodaria 1x por tick senão).
        self._last_periodic: dict[tuple, float] = {}
        # Último motivo de bloqueio por (player_eid) — só loga na transição.
        self._prev_block_reason: dict[int, str] = {}
        # Última LOS checada por (player_eid, target_eid) — só loga na
        # transição (senão vira 1 linha por tick o jogo inteiro).
        self._prev_los: dict[tuple, bool] = {}

    def _enabled(self) -> bool:
        import sys
        mod = sys.modules.get("debug.archer_debug")
        return mod.DBG_ENABLED if mod else DBG_ENABLED

    def _open(self) -> None:
        if self._fh is None:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
            sep = "=" * 76
            self._fh.write(f"{sep}\n[SESSION] {time.strftime('%Y-%m-%d %H:%M:%S')} ({self._side})\n{sep}\n")

    def log(self, event: str, player_eid: int, target_eid: int = -1, **kwargs) -> None:
        if not self._enabled():
            return
        self._open()
        extra = "  ".join(f"{k}={v}" for k, v in kwargs.items())
        self._fh.write(
            f"[{_ts()}][{self._side}] {event:<8s} player_eid={player_eid:<4d} "
            f"target_eid={target_eid:<4d} {extra}\n"
        )

    def log_periodic(self, event: str, player_eid: int, target_eid: int,
                     interval: float = 1.0, **kwargs) -> None:
        """Como log(), mas no máximo 1x a cada `interval` segundos por
        (evento, player_eid) — pra contexto tipo ATTEMPT (que senão vira
        30 linhas/s, 1 por tick) sem perder o "ainda tentando, cooldown=X"
        periódico. Eventos de TRANSIÇÃO de verdade (FIRE/BLOCK/LOS/ARROW/
        AGGRO) usam log()/log_block()/log_los() sem throttle — só o
        heartbeat de contexto passa por aqui."""
        if not self._enabled():
            return
        key = (event, player_eid)
        now = time.time()
        last = self._last_periodic.get(key, 0.0)
        if now - last < interval:
            return
        self._last_periodic[key] = now
        self.log(event, player_eid, target_eid, **kwargs)

    def log_block(self, player_eid: int, target_eid: int, reason: str, **kwargs) -> None:
        """Só grava se o motivo de bloqueio MUDOU desde o último tick pra
        este player (evita 1 linha por tick durante o cooldown normal, mas
        NUNCA perde uma transição — inclusive "parou de bloquear")."""
        if not self._enabled():
            return
        prev = self._prev_block_reason.get(player_eid)
        if prev == reason:
            return
        self._prev_block_reason[player_eid] = reason
        self.log("BLOCK", player_eid, target_eid, reason=reason, **kwargs)

    def log_fire_ok(self, player_eid: int) -> None:
        """Limpa o motivo de bloqueio quando o ataque dispara de verdade —
        garante que o PRÓXIMO bloqueio (se houver) sempre logue, mesmo que
        seja pelo mesmo motivo de antes de disparar."""
        self._prev_block_reason.pop(player_eid, None)

    def log_los(self, player_eid: int, target_eid: int, has_los: bool, **kwargs) -> None:
        """Só grava na transição (True→False ou False→True) — sem isso,
        um alvo parado atrás de parede gera 1 linha de LOS por tick."""
        if not self._enabled():
            return
        key = (player_eid, target_eid)
        prev = self._prev_los.get(key)
        if prev == has_los:
            return
        self._prev_los[key] = has_los
        self.log("LOS", player_eid, target_eid, has_los=has_los, **kwargs)


# Duas instâncias — uma por lado, arquivos separados.
ADBG_SERVER: _ArcherDebugLog = _ArcherDebugLog("SERVER", "archer_debug_server.log")
ADBG_CLIENT: _ArcherDebugLog = _ArcherDebugLog("CLIENT", "archer_debug_client.log")
