"""
hp_rollback_debug.py — Debug do bug "HP de mob/torre volta sozinho" (12/08/2026,
relatado pelo usuário como bug antigo, nunca resolvido — ver
arquitetura/PROBLEMAS_ARQUITETURA.md §44).

Para ativar: `set RPG_DEBUG_HP_ROLLBACK=1` antes de rodar o cliente.
Log salvo em  debug/logs/hp_rollback.log  (criado automaticamente).

Mecanismo: compara `RemoteEntityMeta.hp` de CADA mob/torre remoto contra
o valor visto no frame anterior, 1x por frame (mesmo espírito do
"DEBUG Bug2" já existente no servidor, `server/world_server.py` — aqui é
o lado CLIENTE, comparando o que a TELA mostra, não o que o servidor
calcula). Loga toda vez que o HP SOBE — dano nunca aumenta HP, então
qualquer subida é regen legítimo (raro, mob fora de combate) OU o
sintoma relatado (uma atualização mais "velha"/maior sobrescrevendo uma
mais nova/menor, sem nenhum evento de cura de verdade por trás).

Também loga o `seq` da ÚLTIMA mensagem de rede processada no momento da
subida (setado por client/online_mode_handlers.py::_process_network) —
se um `seq` MENOR aparecer DEPOIS de um `seq` maior já visto para o
mesmo dado, é evidência direta de entrega fora de ordem.

────────────────────────────────────────────────────────────
Formato de cada linha:
  [HH:MM:SS.mmm] HP_UP  eid=N  Nome(Raça)  N1 -> N2 (+delta)  last_seq=S
────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import os
import time
from pathlib import Path

# ── Ativação por env var (nunca constante no código) ─────────────────────────
DBG_ENABLED: bool = os.environ.get("RPG_DEBUG_HP_ROLLBACK", "") not in ("", "0")
# ─────────────────────────────────────────────────────────────────────────────

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "hp_rollback.log"


def _ts() -> str:
    t  = time.time()
    ms = int(t * 1000) % 1000
    return time.strftime("%H:%M:%S") + f".{ms:03d}"


class _HpRollbackLog:
    """Singleton — use a instância global ``HPR``."""

    def __init__(self) -> None:
        self._fh = None
        # Último HP visto por local_eid — só pra detectar subida frame a frame.
        self._last_hp: dict[int, int] = {}
        # Setado por client/online_mode_handlers.py a cada mensagem processada.
        self.last_seq: int = -1
        self._frames_checked = 0

    def _open(self) -> None:
        if self._fh is None:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
            sep = "=" * 76
            self._fh.write(f"{sep}\n[SESSION] {time.strftime('%Y-%m-%d %H:%M:%S')}\n{sep}\n")

    def check(self, world) -> None:
        """Chamado 1x por frame (client/online_mode_handlers.py::_process_network).
        Compara RemoteEntityMeta.hp de cada mob/torre remoto contra o frame
        anterior — loga toda subida (nunca deveria acontecer por dano).

        Abre o arquivo/escreve o cabeçalho de sessão na PRIMEIRA chamada,
        independente de detectar alguma subida — sem isso, uma sessão sem
        nenhum rollback não deixava NENHUM rastro no disco, e não dava pra
        distinguir "ferramenta desligada" de "ferramenta ligada, nada
        aconteceu" (bug real do próprio debug, achado quando o usuário
        testou e não achou o arquivo — ver PROBLEMAS_ARQUITETURA.md §44)."""
        if not DBG_ENABLED:
            return
        self._open()   # garante que o arquivo existe desde o 1º frame
        self._frames_checked += 1
        # Heartbeat a cada ~10s (600 frames a 60fps) — prova de vida no log
        # mesmo sem nenhum rollback detectado ainda.
        if self._frames_checked % 600 == 1:
            self._fh.write(f"[{_ts()}] ALIVE   frames_checados={self._frames_checked}  "
                           f"entidades_remotas={len(self._last_hp)}  last_seq={self.last_seq}\n")
        from engine.components import RemoteEntityMeta, EntityIdentity
        seen: set = set()
        for eid, meta in world.get_entities_with(RemoteEntityMeta):
            seen.add(eid)
            prev = self._last_hp.get(eid)
            if prev is not None and meta.hp > prev:
                ident = world.get_component(eid, EntityIdentity)
                name  = ident.name if ident else "?"
                race  = ident.race if ident else "?"
                self._fh.write(
                    f"[{_ts()}] HP_UP   eid={eid:<5d} {name}({race})  "
                    f"{prev} -> {meta.hp}  (+{meta.hp - prev})  "
                    f"last_seq={self.last_seq}\n")
            self._last_hp[eid] = meta.hp
        # Limpa entidades que sumiram (despawn) — evita crescer pra sempre.
        for gone_eid in (self._last_hp.keys() - seen):
            del self._last_hp[gone_eid]


# Instância global
HPR: _HpRollbackLog = _HpRollbackLog()

if DBG_ENABLED:
    # Prova de vida IMEDIATA no console — se isso não aparecer no terminal
    # que rodou o jogo, a variável de ambiente não chegou neste processo
    # (ex: foi setada num terminal diferente do que rodou `python main.py`).
    print("[HP_ROLLBACK_DEBUG] ATIVO — log em debug/logs/hp_rollback.log")
