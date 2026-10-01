"""
engine/portal_anim_state.py
Estado de animação de EVENTO de portal (ativar/desativar), 100%
client-only, chaveado por posição de tile (01/10/2026, Sistema de
Portais) — ponte entre `client/portal_handlers.py` (escreve ao
receber PORTAL_ANIM_START/STOP/ARRIVE do servidor, ver
shared/messages.py) e `ui/tile_render_system.py::get_world_objects()`
(lê a cada frame pra decidir se desenha o sprite estático do portal ou
o frame atual da animação).

Dict de MÓDULO (mesmo padrão de `core_systems.register_lethal_interceptor`/
`faction_system.register_pvp_context`, CLAUDE.md "proibido estado
mutável ad-hoc fora do ECS") — nunca atributo de classe/`hasattr`.
Conjunto naturalmente pequeno (1 entrada por portal ativo no momento,
nunca por-entidade): expira sozinho na leitura (`get_active`), sem
precisar de um tick dedicado pra limpar.
"""
from __future__ import annotations

# (tile_x, tile_y) -> {"phase": "activate" | "deactivate", "started_ms": int, "duration_ms": float}
_active: dict[tuple[int, int], dict] = {}


def start_activate(tx: int, ty: int, now_ms: int, duration_ms: float) -> None:
    """Portal de ORIGEM começou a animar (cast começou nele —
    PORTAL_ANIM_START). Sobrescreve qualquer estado anterior nessa
    posição (não deveria haver um, mas um START novo sempre vence)."""
    _active[(tx, ty)] = {"phase": "activate", "started_ms": now_ms, "duration_ms": duration_ms}


def stop_activate(tx: int, ty: int) -> None:
    """Cast cancelado antes do tempo (PORTAL_ANIM_STOP) — encerra a
    animação de ativação JÁ, sem esperar a duração original passar.
    No-op se a posição não estava com "activate" ativo (ex: mensagem
    chegou atrasada depois de um ARRIVE já ter tomado o lugar)."""
    cur = _active.get((tx, ty))
    if cur is not None and cur["phase"] == "activate":
        del _active[(tx, ty)]


def start_deactivate(tx: int, ty: int, now_ms: int, duration_ms: float) -> None:
    """Portal de DESTINO recebeu alguém (PORTAL_ANIM_ARRIVE) — toca a
    animação de desativação 1x, na duração natural do sheet (calculada
    pelo chamador, ver client/portal_handlers.py). Substitui qualquer
    estado anterior nessa posição (chegada sempre "vence" visualmente)."""
    _active[(tx, ty)] = {"phase": "deactivate", "started_ms": now_ms, "duration_ms": duration_ms}


def get_active(tx: int, ty: int, now_ms: int) -> "tuple[str, int, float] | None":
    """(phase, elapsed_ms, duration_ms) se a posição tem animação ativa
    e ainda dentro da duração; None caso contrário (chamador cai pro
    sprite estático). Expira e remove sozinho na leitura quando
    `elapsed_ms >= duration_ms` — sem necessidade de um tick/update
    dedicado só pra limpeza."""
    cur = _active.get((tx, ty))
    if cur is None:
        return None
    elapsed = now_ms - cur["started_ms"]
    if elapsed >= cur["duration_ms"]:
        del _active[(tx, ty)]
        return None
    return (cur["phase"], elapsed, cur["duration_ms"])


def clear_all() -> None:
    """Chamado ao trocar de mapa (`game.py::_do_transition`) — animação
    de portal nunca deve sobreviver a uma troca de mapa (tile (x,y) de
    um mapa anterior não tem relação nenhuma com o mesmo (x,y) no mapa
    novo)."""
    _active.clear()
