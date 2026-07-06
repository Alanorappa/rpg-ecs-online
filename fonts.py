"""
fonts.py — Carregador central de fonte do projeto.

Todos os sistemas importam `make(size)` daqui.
Troca a fonte do projeto inteiro alterando apenas _FONT_NAME.

_SCALE: fator de correção de métricas em relação à fonte padrão do pygame
  (pygame.font.Font(None, size)). A Determination renderiza ~2x mais alta
  que a fonte padrão no mesmo size, por isso _SCALE = 0.5.

CachedFont: camada de cache de texto na fonte (padrão glyph/text-cache de
engine). font.render() com TTF custa 0.5-3ms por chamada — chamado todo
frame por painéis de HUD causava spikes medidos de 13-22ms (combat_log,
talents). Com o cache aqui, TODOS os ~260 call sites de font.render() do
cliente ficam O(dict lookup) sem precisar de cache local em cada painel.

REGRA para consumidores: a Surface retornada por render() é COMPARTILHADA
(cacheada). NÃO mutar (set_alpha/fill/blit nela). Quem precisa mutar deve
usar .copy() (barato: ~10µs vs ~1ms do render) ou restaurar o estado após
usar. Sites que mutam hoje: floating_text.py, combat_log.py — já adaptados.
"""
from __future__ import annotations
import os
import pygame

from paths import resource_path

_FONT_NAME = "determinationregular"
_FONT_FILE = "assets/fonts/determination.ttf"   # embarcada — vai no build
_SCALE     = 0.5          # ajuste de métrica: Determination ≈ 2× a fonte padrão

_resolved_path: str | None = None
_resolved: bool = False


def _path() -> str | None:
    """Resolve a fonte do projeto, nesta ordem:
    1. arquivo embarcado em assets/fonts/ (funciona no build distribuído —
       match_font procurava só nas fontes INSTALADAS no Windows: na máquina
       de dev achava, na do testador caía no default do pygame e ainda com
       _SCALE 0.5 ficava minúscula);
    2. fonte instalada no sistema (dev sem o arquivo);
    3. None → default do pygame (e make() pula o _SCALE, senão o fallback
       renderiza na metade do tamanho).
    """
    global _resolved_path, _resolved
    if not _resolved:
        _bundled = resource_path(_FONT_FILE)
        if os.path.isfile(_bundled):
            _resolved_path = _bundled
        else:
            _resolved_path = pygame.font.match_font(_FONT_NAME) or None
        _resolved = True
    return _resolved_path


class CachedFont(pygame.font.Font):
    """pygame.font.Font com memoização de render(text, True, color).

    - Cache por instância: fontes recriadas na troca de escala de UI
      (UIScaleMixin.set_ui_scale / GameEngine._reload_ui_fonts) começam
      com cache vazio — invalidação automática, sem estado global.
    - Só cacheia o caso quente (antialias=True, sem background); chamadas
      com background delegam direto pra Font.render.
    - _CACHE_MAX limita memória: textos dinâmicos (timers, contadores)
      churnam o cache; ao estourar, limpa tudo (mais simples e mais rápido
      que LRU — o refill é barato e o estouro é raro).
    """

    _CACHE_MAX = 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text_cache: dict = {}

    def render(self, text, antialias=True, color=(255, 255, 255), background=None):
        if background is not None or not antialias:
            return super().render(text, antialias, color, background)
        key = (text, color if not isinstance(color, list) else tuple(color))
        surf = self._text_cache.get(key)
        if surf is None:
            if len(self._text_cache) >= self._CACHE_MAX:
                self._text_cache.clear()
            surf = super().render(text, antialias, color, background)
            self._text_cache[key] = surf
        return surf


def make(size: int) -> pygame.font.Font:
    """Retorna uma fonte do projeto no tamanho equivalente à fonte padrão de `size` px."""
    p = _path()
    # _SCALE corrige a métrica da Determination; sem ela (fallback default),
    # aplicar o scale renderizaria tudo na metade do tamanho.
    scale = _SCALE if p else 1.0
    return CachedFont(p, max(6, round(size * scale)))
