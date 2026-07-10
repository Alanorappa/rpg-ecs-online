"""
fx.py — Façade de efeitos client-side (floating text, sons, procs, avisos).

Inversão de dependência do problema F (PROBLEMAS_ARQUITETURA.md): sistemas de
gameplay compartilhados (world_systems.py) rodam tanto no cliente quanto no
servidor headless. No cliente, os efeitos visuais/sonoros devem acontecer; no
servidor, devem ser no-ops — SEM o módulo de gameplay importar pygame
(floating_text/sound_manager importam pygame no topo).

Uso:
    from engine.fx import FLT, PROC, WARN, SOUNDS, DASH_TRAIL   # em world_systems etc.
    FLT.add(...)          # cliente: floating text real | servidor: no-op

O cliente (game.py) chama bind_client_fx() UMA vez na inicialização, antes de
construir os sistemas. Sem bind (servidor), toda chamada é no-op silencioso.

LOG (combat_log) NÃO está aqui — combat_log.py é pygame-free e pode ser
importado direto em qualquer lado.
"""
from __future__ import annotations


def _noop(*_a, **_k) -> None:
    return None


class _FxProxy:
    """Proxy que delega ao alvo real quando vinculado; no-op caso contrário.

    Atenção: métodos que RETORNAM valor no alvo real retornam None enquanto
    não vinculado — consumidores de gameplay não devem depender de retorno
    de efeito visual/sonoro (é fire-and-forget por contrato).
    """
    __slots__ = ("_target", "_name")

    def __init__(self, name: str):
        self._target = None
        self._name = name

    def bind(self, target) -> None:
        self._target = target

    def __getattr__(self, attr):
        # __getattr__ só é chamado para atributos não encontrados em __slots__
        target = object.__getattribute__(self, "_target")
        if target is None:
            return _noop
        return getattr(target, attr)

    def __repr__(self) -> str:
        state = "bound" if self._target is not None else "null"
        return f"<FxProxy {self._name} ({state})>"


FLT        = _FxProxy("FLT")
PROC       = _FxProxy("PROC")
WARN       = _FxProxy("WARN")
DASH_TRAIL = _FxProxy("DASH_TRAIL")
SOUNDS     = _FxProxy("SOUNDS")


def bind_client_fx() -> None:
    """Vincula os proxies aos gerenciadores reais do cliente.

    Chamar UMA vez na inicialização do cliente (GameEngine.__init__), antes
    do primeiro frame. Importa floating_text/sound_manager aqui dentro —
    é o único ponto onde fx toca pygame, e só roda no cliente.
    """
    from ui.floating_text import FLT as _flt, PROC as _proc, WARN as _warn, \
                              DASH_TRAIL as _dash
    from ui.sound_manager import SOUNDS as _sounds
    FLT.bind(_flt)
    PROC.bind(_proc)
    WARN.bind(_warn)
    DASH_TRAIL.bind(_dash)
    SOUNDS.bind(_sounds)
