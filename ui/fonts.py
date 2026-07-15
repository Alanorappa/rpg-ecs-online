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
    """pygame.font.Font com memoização de render(text, antialias, color).

    - Cache por instância: fontes recriadas na troca de escala de UI
      (UIScaleMixin.set_ui_scale / GameEngine._reload_ui_fonts) começam
      com cache vazio — invalidação automática, sem estado global.
    - Cacheia sempre que não há `background` (chave inclui `antialias` pra
      não misturar variantes) — visual pixelizado do jogo usa
      antialias=False em 100% dos call sites (08/07/2026), então é o caso
      quente de verdade; chamadas com background delegam direto pra
      Font.render (raras, não vale cachear).
    - _CACHE_MAX limita memória: textos dinâmicos (timers, contadores)
      churnam o cache; ao estourar, limpa tudo (mais simples e mais rápido
      que LRU — o refill é barato e o estouro é raro).
    """

    _CACHE_MAX = 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text_cache: dict = {}

    def render(self, text, antialias=False, color=(255, 255, 255), background=None):
        if background is not None:
            return super().render(text, antialias, color, background)
        key = (text, color if not isinstance(color, list) else tuple(color), antialias)
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


# ── Fonte pixel-perfect (teste, 11/07/2026) ────────────────────────────────
# Convive com a Determination acima — usuário pediu pra testar em paralelo
# pra labels pequenos (nome de NPC/mob acima da cabeça), não substitui a
# fonte do projeto inteiro. Sem _SCALE: fontes bitmap/pixel já vêm
# desenhadas na grade certa pro tamanho nativo — aplicar uma correção de
# métrica aqui (como a Determination precisa) distorceria e voltaria a
# ficar borrado. MEGAMAN10 é desenhada pra ~10px (nome do arquivo).
_PIXEL_FONT_NAME = "megaman10"
_PIXEL_FONT_FILE = "assets/fonts/MEGAMAN10.ttf"

_pixel_resolved_path: str | None = None
_pixel_resolved: bool = False


def _pixel_path() -> str | None:
    global _pixel_resolved_path, _pixel_resolved
    if not _pixel_resolved:
        _bundled = resource_path(_PIXEL_FONT_FILE)
        if os.path.isfile(_bundled):
            _pixel_resolved_path = _bundled
        else:
            _pixel_resolved_path = pygame.font.match_font(_PIXEL_FONT_NAME) or None
        _pixel_resolved = True
    return _pixel_resolved_path


def make_pixel(size: int = 16) -> pygame.font.Font:
    """Fonte pixel-perfect (MEGAMAN10) — sem correção de métrica, sem
    antialias (mesma regra do resto do projeto: CachedFont.render default
    já é antialias=False). Agora que só é usada via ui/world_labels.py
    (desenhada em espaço de tela, nunca reamostrada pelo zoom da câmera —
    ver ARQUITETURA_ONLINE.md 23.5), o tamanho é puramente estético: 10
    (nome do arquivo) ficou pequeno demais pra ler em jogo (usuário
    reportou "ilegível" mesmo já pixel-perfect), 16 é o valor "razoável"
    ajustado depois do teste — mude aqui se ainda não bastar."""
    p = _pixel_path()
    return CachedFont(p, size)


# ── Render compacto (corrige bearing desproporcional de glifos estreitos) ──
# MEGAMAN10 (e potencialmente outras fontes pixel) tem bearing esquerdo
# grande em glifos estreitos — medido: "i" tem advance=6px mas a tinta só
# começa em x=3 (metade do avanço é espaço vazio antes do desenho).
# font.render("Zumbi") respeita esse bearing literalmente, produzindo um
# vão visível antes do "i" ("Zumb i") — reportado pelo usuário 11/07/2026.
# Fix: renderiza caractere por caractere e reempacota pela TINTA real
# (pygame.mask) + respiro fixo, descartando o bearing/kerning original da
# fonte. Pra uma fonte pixel (quase monoespaçada, sem kerning fino por
# natureza), isso não perde nada perceptível — e em fontes SEM esse
# problema o resultado fica quase idêntico ao render normal.
_TIGHT_CACHE_MAX = 512
_tight_cache: dict = {}


def render_tight(font: pygame.font.Font, text: str, color: tuple, gap: int = 1) -> pygame.Surface:
    key = (id(font), text, tuple(color) if not isinstance(color, tuple) else color, gap)
    cached = _tight_cache.get(key)
    if cached is not None:
        return cached
    if not text:
        surf = pygame.Surface((1, 1), pygame.SRCALPHA)
    else:
        glyphs = []
        total_w = 0
        max_h = 0
        for ch in text:
            g_surf = font.render(ch, False, color)
            mask = pygame.mask.from_surface(g_surf)
            rects = mask.get_bounding_rects()
            if rects:
                ink = rects[0]
                for r in rects[1:]:
                    ink = ink.union(r)
            else:
                # sem tinta (ex: espaço) — usa a largura nominal do glifo
                ink = pygame.Rect(0, 0, g_surf.get_width(), g_surf.get_height())
            glyphs.append((g_surf, ink))
            total_w += ink.width + gap
            max_h = max(max_h, g_surf.get_height())
        total_w = max(1, total_w - gap)
        surf = pygame.Surface((total_w, max_h), pygame.SRCALPHA)
        x = 0
        for g_surf, ink in glyphs:
            surf.blit(g_surf, (x - ink.x, 0))
            x += ink.width + gap
    if len(_tight_cache) >= _TIGHT_CACHE_MAX:
        _tight_cache.clear()
    _tight_cache[key] = surf
    return surf
