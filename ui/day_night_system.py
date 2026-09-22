# day_night_system.py
"""
DayNightSystem — ciclo dia/noite cosmético (21/09/2026, pedido do
usuário). 100% client-only: relógio determinístico (função do tempo
real), sem mensagem de rede nova, sem o servidor saber de nada — todo
cliente calcula a MESMA hora sozinho. Só mundo aberto tem dia/noite —
BG/Arena ficam sempre num horário fixo (decisão do usuário: evita a luz
mudar no meio de uma partida competitiva).

Desenho (ver PROBLEMAS_ARQUITETURA.md §194):
  1. `day_night_color()` — ponto único de verdade da cor/alpha do
     tingimento atual, derivado de `day_night_fraction()` por
     interpolação entre keyframes.
  2. `DayNightSystem.render()` — desenha esse tingimento por cima de TODO
     o mundo já renderizado (zoom_surf), igual o overlay de Fog of War já
     faz (mesmo padrão de Surface SRCALPHA cacheada).
  3. Objeto com `TileType.light_source` (lampião etc.) "fura" um buraco
     no tingimento com um gradiente radial + `BLEND_RGBA_SUB` — o brilho
     nasce sozinho conforme escurece (de dia o tingimento já é alpha=0,
     então subtrair não muda nada visualmente — sem precisar de um
     "liga/desliga" separado).
"""
from __future__ import annotations
import time
import pygame

from engine.tileset import TILE_SIZE
from engine.world_systems import System
from ui.ui_components import InstanceInventoryUIState


# ── Relógio ────────────────────────────────────────────────────────────────
# 60 minutos reais = 1 dia completo (pedido do usuário, 21/09/2026).
DAY_NIGHT_CYCLE_S = 60.0 * 60.0

# ── Cores-âncora editáveis (F12 → aba Dia/Noite, 21/09/2026, pedido do
# usuário) ────────────────────────────────────────────────────────────
# Só estes 3 pontos são configuráveis em tempo real — os keyframes da
# transição inteira são DERIVADOS deles em _build_keyframes() (nunca
# hardcoded à parte), então mudar uma âncora já ajusta a transição
# inteira automaticamente. Persistidos em config.json (ver
# game.py::_save_config()/_load_day_night_config()) — client/
# debug_handlers.py::_draw_debug_tab_dianoite() é quem edita.
#
# Por que só uma âncora por transição (não 5 pontos como na 1ª versão)
# resolve o problema do acinzentado sozinho: interpolar RGB em linha
# reta direto do azul da noite (20,25,60) pro laranja do dia (255,140,70)
# passava por um acastanhado dessaturado no meio (ex.: (137,82,65) — bug
# real, ver PROBLEMAS_ARQUITETURA.md §195). A âncora de amanhecer/
# entardecer já nasce como um tom ROSA/AURORA (não laranja puro) —
# vizinho o bastante do azul da noite no círculo de cores pra não
# desaturar no meio do caminho. Editar a âncora pra uma cor muito
# diferente (ex.: um amarelo puro) pode reintroduzir alguma
# dessaturação passageira — é o preço de dar controle manual direto ao
# usuário; ele vê o resultado ao vivo (F12) e ajusta se não gostar.
#
# Valores validados pelo usuário em jogo via F12 (22/09/2026) — eram só
# config LOCAL (config.json, não sobrevive ao build do executável, ver
# release_tools/build_client.ps1) até virarem o padrão de verdade aqui.
# "Resetar padrão" no F12 volta pra ESTES valores, não mais os originais
# da v1 (§195/§196) — ver PROBLEMAS_ARQUITETURA.md §200.
DEFAULT_NIGHT_COLOR: tuple[int, int, int] = (10, 5, 30)
DEFAULT_NIGHT_ALPHA: int = 230
DEFAULT_DAWN_COLOR:  tuple[int, int, int] = (190, 100, 130)
DEFAULT_DAWN_ALPHA:  int = 15
DEFAULT_DUSK_COLOR:  tuple[int, int, int] = (220, 90, 110)
DEFAULT_DUSK_ALPHA:  int = 15

_night_color: list[int] = list(DEFAULT_NIGHT_COLOR)
_night_alpha: int = DEFAULT_NIGHT_ALPHA
_dawn_color:  list[int] = list(DEFAULT_DAWN_COLOR)
_dawn_alpha:  int = DEFAULT_DAWN_ALPHA
_dusk_color:  list[int] = list(DEFAULT_DUSK_COLOR)
_dusk_alpha:  int = DEFAULT_DUSK_ALPHA


def _clamp255(v: int) -> int:
    return max(0, min(255, int(v)))


def set_night(color: tuple[int, int, int] | None = None, alpha: int | None = None) -> None:
    global _night_color, _night_alpha
    if color is not None:
        _night_color = [_clamp255(c) for c in color]
    if alpha is not None:
        _night_alpha = _clamp255(alpha)


def set_dawn(color: tuple[int, int, int] | None = None, alpha: int | None = None) -> None:
    global _dawn_color, _dawn_alpha
    if color is not None:
        _dawn_color = [_clamp255(c) for c in color]
    if alpha is not None:
        _dawn_alpha = _clamp255(alpha)


def set_dusk(color: tuple[int, int, int] | None = None, alpha: int | None = None) -> None:
    global _dusk_color, _dusk_alpha
    if color is not None:
        _dusk_color = [_clamp255(c) for c in color]
    if alpha is not None:
        _dusk_alpha = _clamp255(alpha)


def reset_anchors_to_default() -> None:
    set_night(DEFAULT_NIGHT_COLOR, DEFAULT_NIGHT_ALPHA)
    set_dawn(DEFAULT_DAWN_COLOR, DEFAULT_DAWN_ALPHA)
    set_dusk(DEFAULT_DUSK_COLOR, DEFAULT_DUSK_ALPHA)


def get_anchor_config() -> dict:
    """Snapshot pra persistir em config.json (game.py::_save_config())."""
    return {
        "night_color": list(_night_color), "night_alpha": _night_alpha,
        "dawn_color":  list(_dawn_color),  "dawn_alpha":  _dawn_alpha,
        "dusk_color":  list(_dusk_color),  "dusk_alpha":  _dusk_alpha,
    }


def set_anchor_config(cfg: dict) -> None:
    """Aplica config salva (game.py, no boot) — chave ausente mantém o
    default do módulo (save antigo sem essas chaves, ou 1ª vez)."""
    set_night(cfg.get("night_color", DEFAULT_NIGHT_COLOR), cfg.get("night_alpha", DEFAULT_NIGHT_ALPHA))
    set_dawn(cfg.get("dawn_color", DEFAULT_DAWN_COLOR), cfg.get("dawn_alpha", DEFAULT_DAWN_ALPHA))
    set_dusk(cfg.get("dusk_color", DEFAULT_DUSK_COLOR), cfg.get("dusk_alpha", DEFAULT_DUSK_ALPHA))


def _build_keyframes() -> list[tuple[float, tuple[int, int, int], int]]:
    """Keyframes (fração 0.0-1.0, RGB, alpha 0-255) derivados das 3
    âncoras atuais — recalculado a cada chamada de `day_night_color()`
    (8 tuplas fixas, custo desprezível). Ordem crescente de fração,
    terminando em 1.00 com o MESMO valor de 0.00 (loop sem costura)."""
    night = (tuple(_night_color), _night_alpha)
    dawn  = (tuple(_dawn_color),  _dawn_alpha)
    dusk  = (tuple(_dusk_color),  _dusk_alpha)
    # "Limpo" (alpha=0) herda a COR da âncora vizinha em vez de (0,0,0) —
    # sem isso, o alpha caindo pra 0 e o RGB caindo pra preto ao mesmo
    # tempo faziam a cor "escurecer pro preto" bem no fim do fade em vez
    # de simplesmente sumir mantendo o tom (achado ao escrever o teste de
    # regressão — a diferença só é visível em alpha bem baixo, mas ainda
    # contava como "quase cinza" pro teste, e é mais correto de qualquer
    # forma: sumir de vista não deveria mudar a matiz do que está sumindo).
    clear_pos_amanhecer = (tuple(_dawn_color), 0)
    clear_pre_entardecer = (tuple(_dusk_color), 0)
    return [
        (0.00, *night),                  # meia-noite
        (0.15, *night),                  # madrugada (platô)
        (0.23, *dawn),                   # amanhecer pleno
        (0.32, *clear_pos_amanhecer),    # manhã — sem tingimento
        (0.68, *clear_pre_entardecer),   # tarde — sem tingimento
        (0.77, *dusk),                   # entardecer pleno
        (0.85, *night),                  # anoitecer -> noite
        (1.00, *night),                  # volta pra meia-noite
    ]

# Rótulo simples por faixa de fração — só pra exibição (F12/debug), nunca
# consultado pelo overlay/luz (que só leem day_night_color()). Faixas
# alinhadas com os keyframes acima.
_PHASE_LABELS: list[tuple[float, float, str]] = [
    (0.00, 0.17, "Noite"),
    (0.17, 0.29, "Amanhecer"),
    (0.29, 0.71, "Dia"),
    (0.71, 0.83, "Entardecer"),
    (0.83, 1.00, "Noite"),
]

# Override de debug (F12, só GM/local) — desloca o relógio sem congelar
# (o tempo continua correndo normalmente a partir do novo ponto). 0.0 =
# sem override, comportamento normal. Nome de módulo visível + funções
# dedicadas pra mutar (nunca setado direto por fora) — mesmo padrão de
# estado plugável já usado em engine/core_systems.py (`_svc`/
# `_lethal_interceptor`), ver CLAUDE.md "Proibido estado mutável ad-hoc".
_debug_time_offset_s: float = 0.0


def set_debug_time_offset(offset_s: float) -> None:
    global _debug_time_offset_s
    _debug_time_offset_s = offset_s


def jump_to_fraction(target_fraction: float) -> None:
    """Pula o relógio pra `target_fraction` (0.0-1.0) SEM congelar — o
    tempo continua correndo normalmente a partir daí. Usado só pelo
    painel de debug (F12/GM), 100% local — não existe conceito de "hora"
    no servidor, é puramente cosmético."""
    now = time.time()
    current_fraction = ((now + _debug_time_offset_s) % DAY_NIGHT_CYCLE_S) / DAY_NIGHT_CYCLE_S
    delta_fraction = (target_fraction - current_fraction) % 1.0
    set_debug_time_offset(_debug_time_offset_s + delta_fraction * DAY_NIGHT_CYCLE_S)


def day_night_fraction(now: float | None = None) -> float:
    """0.0-1.0 — posição no ciclo. Mesma fórmula em TODO cliente (função
    pura de `time.time()`, sem estado/servidor) — todo mundo vê a mesma
    hora automaticamente, sem sincronizar nada pela rede. `now` explícito
    (usado pelos testes) ignora o override de debug de propósito — só o
    relógio "de verdade" (`now=None`) passa por `_debug_time_offset_s`."""
    if now is not None:
        return (now % DAY_NIGHT_CYCLE_S) / DAY_NIGHT_CYCLE_S
    return ((time.time() + _debug_time_offset_s) % DAY_NIGHT_CYCLE_S) / DAY_NIGHT_CYCLE_S


def current_phase_label(now: float | None = None) -> str:
    """Rótulo textual da fase atual (F12/debug) — ex: "Amanhecer"."""
    frac = day_night_fraction(now)
    for f0, f1, label in _PHASE_LABELS:
        if f0 <= frac < f1:
            return label
    return _PHASE_LABELS[-1][2]


def day_night_color(now: float | None = None) -> tuple[int, int, int, int]:
    """RGBA do tingimento atual — ver docstring do módulo. Keyframes
    recalculados a cada chamada (`_build_keyframes()`) pra sempre
    refletir as âncoras atuais, inclusive editadas ao vivo pelo F12."""
    frac = day_night_fraction(now)
    keyframes = _build_keyframes()
    for i in range(len(keyframes) - 1):
        f0, rgb0, a0 = keyframes[i]
        f1, rgb1, a1 = keyframes[i + 1]
        if f0 <= frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            r = round(rgb0[0] + (rgb1[0] - rgb0[0]) * t)
            g = round(rgb0[1] + (rgb1[1] - rgb0[1]) * t)
            b = round(rgb0[2] + (rgb1[2] - rgb0[2]) * t)
            a = round(a0 + (a1 - a0) * t)
            return (r, g, b, a)
    last = keyframes[-1]
    return (last[1][0], last[1][1], last[1][2], last[2])


class DayNightSystem(System):
    def __init__(self, world, player_entity: int):
        self.world         = world
        self.player_entity = player_entity
        self._overlay_surf: "pygame.Surface | None" = None
        self._overlay_size: tuple[int, int] = (0, 0)
        # Cache de gradiente radial por raio em PIXELS (poucos valores
        # possíveis — radius_tiles do catálogo é um inteiro pequeno) —
        # mesmo padrão de recurso cacheado com teto natural de
        # `ui/floating_text.py::_outline_cache`.
        self._light_gradient_cache: dict[int, "pygame.Surface"] = {}

    def _get_light_gradient(self, radius_px: int) -> "pygame.Surface":
        """Gradiente radial cacheado por raio — alpha máximo no centro,
        decaindo linearmente até 0 na borda, cor SEMPRE (0,0,0) (só o
        canal alpha varia). Usado com BLEND_RGBA_SUB: como RGB do
        gradiente é 0, subtrair nunca altera a COR do overlay — só
        reduz seu alpha, "abrindo um buraco" suave nele. Se a cor do
        gradiente não fosse 0, BLEND_RGBA_SUB subtrairia RGB também
        (não ponderado pelo alpha do próprio pixel), estourando pra
        preto puro em vez de revelar a cor normal do mundo por baixo."""
        cached = self._light_gradient_cache.get(radius_px)
        if cached is not None:
            return cached
        d = radius_px * 2
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        for y in range(d):
            for x in range(d):
                dist = ((x - radius_px) ** 2 + (y - radius_px) ** 2) ** 0.5
                if dist <= radius_px:
                    a = round(255 * (1.0 - dist / radius_px))
                    surf.set_at((x, y), (0, 0, 0, a))
        self._light_gradient_cache[radius_px] = surf
        return surf

    def render(self, world_objects: list | None = None) -> None:
        if self.world_surf is None:
            return
        # Só mundo aberto — BG/Arena ficam sempre num horário fixo
        # (decisão do usuário: luz não deve mudar no meio de uma partida).
        iius = self.world.get_component(self.player_entity, InstanceInventoryUIState)
        if iius is not None and iius.active:
            return

        r, g, b, a = day_night_color()
        if a <= 0:
            return  # dia pleno — nada pra desenhar, economiza o frame inteiro

        surf_w, surf_h = self.world_surf.get_size()
        if self._overlay_surf is None or self._overlay_size != (surf_w, surf_h):
            self._overlay_surf = pygame.Surface((surf_w, surf_h), pygame.SRCALPHA)
            self._overlay_size = (surf_w, surf_h)
        self._overlay_surf.fill((r, g, b, a))

        if world_objects:
            for obj in world_objects:
                light = obj.get("light_source")
                if not light:
                    continue
                off_x, off_y, lw, lh, intensity_pct, radius_tiles = light
                cx = obj["screen_x"] + off_x + lw / 2
                cy = obj["screen_y"] + off_y + lh / 2
                radius_px = radius_tiles * TILE_SIZE
                if radius_px <= 0:
                    continue
                if (cx < -radius_px or cx > surf_w + radius_px
                        or cy < -radius_px or cy > surf_h + radius_px):
                    continue  # fora da tela — não desenha
                gradient = self._get_light_gradient(radius_px)
                strength = max(0.0, min(1.0, intensity_pct / 100.0))
                gradient.set_alpha(round(255 * strength))
                self._overlay_surf.blit(
                    gradient, (cx - radius_px, cy - radius_px),
                    special_flags=pygame.BLEND_RGBA_SUB)

        self.world_surf.blit(self._overlay_surf, (0, 0))
