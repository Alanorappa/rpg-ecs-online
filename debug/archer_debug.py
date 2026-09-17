"""
archer_debug.py — Debug do pipeline de auto-attack ranged do arqueiro
(client E server — mesmo módulo, dois arquivos de log separados porque
são processos diferentes e escrever no mesmo arquivo dos dois lados
arriscaria intercalar linhas de forma confusa).

Para ativar: `set RPG_DEBUG_ARCHER=1` (client e servidor compartilham o
mesmo nome de env var, já que é o mesmo arquivo importado nos dois
lados — precisa ligar nos DOIS processos pra ver o pipeline completo).
Logs em  debug/logs/archer_debug_server.log  e
debug/logs/archer_debug_client.log  (criados automaticamente).

Objetivo original: caçar o bug relatado pelo usuário — "do nada o
arqueiro para de atacar" + "desconta flecha da aljava sem gerar flecha/
dano" — cobrindo TODOS os pontos de decisão do pipeline (client E
server) que determinam se um tiro dispara, é bloqueado, consome
flecha, toca som ou agra o mob.

Extensão 16/09/2026 (ver PROBLEMAS_ARQUITETURA.md §154/§156/§157) —
eventos ARROW/EQUIP passaram a incluir `quiver_item_id`/`quiver_obj_id`
(identidade da aljava, não só a contagem) — objetivo: caçar o bug de
"aljava nova recém-comprada acusa sem flechas com a aljava cheia",
onde a contagem sozinha não bastava pra saber SE a confirmação/equip
realmente correspondiam à aljava certa em cada lado.

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
            "voltou a poder atirar", gera uma linha) — EXCETO o
            "quiver_item_id_mismatch" do STATS_UPDATE (client) e o
            "quiver_empty" do auto-attack (client), que usam log_block
            mas incluem `quiver_item_id`/`quiver_obj_id` extras.
  LOS       (server/client) resultado da checagem de linha de visão
            (sempre logado quando checada, faz parte do BLOCK quando
            bloqueia, mas também logado quando dá OK pra confirmar que
            a checagem rodou).
  FIRE      (server) ataque disparado de verdade — outcome resolvido
            (hit/crit/miss/dodge/parry/evade), inclui dano.
  ARROW     (server) consumo REAL de flecha, com `quiver_item_id`/
            `quiver_obj_id` da aljava que perdeu a flecha (auto-attack
            ou Recarregar, `event_src` distingue). (client) toda vez
            que um STATS_UPDATE com `quiver_arrow_count` chega —
            mostra o `payload_quiver_item_id` (o que o servidor mandou)
            vs `local_quiver_item_id`/`local_quiver_obj_id` (o que está
            equipado agora) ANTES de decidir aplicar ou ignorar — se
            os dois `item_id` não baterem, um BLOCK
            "quiver_item_id_mismatch" segue na linha de baixo.
  EQUIP     (server/client) aljava equipada — `quiver_item_id`,
            `inv_index` (posição na bag), `arrow_count` no momento do
            equip, `quiver_obj_id` (identidade do objeto Python — se o
            MESMO `quiver_obj_id` aparecer depois em ARROW com um
            `quiver_item_id` diferente, é sinal de MUTAÇÃO indevida do
            objeto, não troca de referência).
  AGGRO     (server) mob mudou de estado por causa deste ataque.
  SOUND     (client) som de disparo/nock/impacto tocado.
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
from pathlib import Path

# ── Ativação por env var (nunca constante no código) ─────────────────────────
# `set RPG_DEBUG_ARCHER=1` antes de rodar client/servidor. Antes era uma
# constante editada à mão — DBG_ENABLED=True esquecido ligado já foi parar em
# sessão de teste real (14/07/2026, degradando performance sem ninguém saber
# por quê). Env var nunca entra em commit/build por acidente. Ver
# PROBLEMAS_ARQUITETURA.md seção 11 item D3.
import os as _os
DBG_ENABLED: bool = _os.environ.get("RPG_DEBUG_ARCHER", "") not in ("", "0")
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
