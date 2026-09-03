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

  Eventos de PLAYER REMOTO (bot ou humano) — 28/08/2026, investigação de
  bug real relatado pelo usuário: bot da BG às vezes aparece com sprite/
  HUD de morto (sem barra de HP) ou impossível de atacar, mesmo vivo no
  servidor. `RemoteControlled.hp<=0` decide as 2 coisas (esconde a
  barra E bloqueia alvo) — estes eventos existem pra achar QUANDO/POR
  QUE esse campo fica desatualizado, sem precisar logar o render (roda
  a 60fps, inundaria o arquivo):
  PLAYER_SPAWN        _spawn_remote_player_entity criou entidade local nova
  PLAYER_SPAWN_SKIP   _spawn_remote_player_entity ignorado — server_eid já rastreado
  PLAYER_HP_COMBAT    rc.hp atualizado via _apply_combat_result (combat[] do AOI_UPDATE)
  PLAYER_HP_STATS     rc.hp atualizado via _handle_msg_stats_update (canal normalmente
                       privado — logar toda vez que dispara pra um player QUE NÃO é o
                       próprio, caso raro que merece atenção)
  PLAYER_DESPAWN      _remove_remote_player_entity removeu a entidade local

  Eventos de MOVIMENTO NO SERVIDOR — 28/08/2026, mesma investigação
  (Interceptar seguido de skill "Nenhum alvo"/"Alvo inválido"): achado
  real via SKILL_WARN que o servidor, 1+ segundo depois do dash
  registrar "hit", ainda tinha o player a vários tiles do alvo — estes
  eventos existem pra achar SE/QUANDO o dash completa (ou não) e SE
  algum MOVE normal do cliente é rejeitado logo depois:
  DASH_COMPLETE       TileMovementSystem terminou um tween com is_dash=True
                       (`engine/world_systems.py`) — mostra o tile onde
                       realmente pousou vs o tile-alvo do dash
  MOVE_REJECTED_BUDGET move_player() (`server/world_server.py`) recusou por
                       exceder o orçamento tempo×velocidade (speedhack/dash
                       ainda não confirmado)
  MOVE_REJECTED_WALKABLE move_player() recusou por tile de destino não-andável

  INTERCEPTAR_CAST     `_skill_interceptar` (`engine/skill_handlers.py`) —
                       leitura INTERNA da posição do alvo no instante exato
                       do cálculo do destino do dash (3ª rodada, 28/08/2026
                       — usuário confirmou visualmente que o alvo estava
                       parado/adjacente e mesmo assim a skill seguinte
                       falhava; `target_already_stunned` confirma se um
                       Interceptar anterior ainda tinha o alvo atordoado)
  INTERCEPTAR_STUN_CHECK mesma skill — `stun_duration` real usado (0/None
                       = talento de atordoar não estava ativo pra esse
                       cast, apesar da suposição de "todo talento sempre
                       ativo na BG")
  SKILL_WARN (campos novos) `target_stunned`/`target_is_moving` — estado
                       do alvo no instante da rejeição, pra cruzar com
                       INTERCEPTAR_CAST/INTERCEPTAR_STUN_CHECK do mesmo alvo

  AUTO_ATK_RANGE_OK    (`server/combat_processor.py::_resolve_auto_attack`,
                       31/08/2026) — loga toda vez que um auto-attack passa
                       pelo gate de alcance (chebyshev ≤ attack_range),
                       com tick/posições/`is_dash`/`progress` do atacante.
                       Achou o bug real "Interceptar acerta antes do dash"
                       (ver PROBLEMAS_ARQUITETURA.md §87) — mantido como
                       instrumentação permanente pra qualquer investigação
                       futura de timing de auto-attack.
  MOVE_REJECTED_DASH_IN_PROGRESS (`server/world_server.py::move_player`,
                       31/08/2026) — loga quando um MOVE normal é recusado
                       por chegar enquanto o player tem um dash
                       (Interceptar) genuinamente em andamento
                       (`is_dash=True`/`is_moving=True`) — protege o dash
                       de ser sobrescrito por uma corrida MOVE×CAST_SKILL
                       (ver PROBLEMAS_ARQUITETURA.md §87).
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
