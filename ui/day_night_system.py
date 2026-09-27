# day_night_system.py
"""
DayNightSystem — ciclo dia/noite cosmético (21/09/2026, pedido do
usuário). 100% client-only: relógio determinístico (função do tempo
real), sem mensagem de rede nova, sem o servidor saber de nada — todo
cliente calcula a MESMA hora sozinho. Só mundo aberto tem dia/noite —
BG/Arena ficam sempre num horário fixo (decisão do usuário: evita a luz
mudar no meio de uma partida competitiva).

Desenho (22/09/2026, reescrito de alpha blending pra multiplicação de
cor — pedido do usuário, ver PROBLEMAS_ARQUITETURA.md §203; §194 tem o
desenho ORIGINAL, obsoleto):
  1. `day_night_color()` — ponto único de verdade da cor atual (agora um
     FATOR MULTIPLICADOR, não mais um tingimento misturado por
     transparência), derivado de `day_night_fraction()` por
     interpolação entre keyframes. Branco (255,255,255) = sem efeito
     (multiplicar por branco preserva a cor original); quanto mais
     escura a cor, mais a cena escurece — e como é MULTIPLICAÇÃO (não
     mistura), cada pixel escurece proporcional à sua PRÓPRIA
     luminosidade original, em vez de todos levarem a mesma lavada de
     cor por cima (resultado visualmente mais natural — objeto já
     escuro continua quase igual, objeto claro fica visivelmente azulado).
  2. `DayNightSystem.render()` — desenha essa cor por cima de TODO o
     mundo já renderizado (zoom_surf) com `BLEND_RGBA_MULT`.
  3. Objeto com `TileType.light_source` (lampião etc.) clareia a área ao
     redor de volta em direção ao branco com um gradiente radial +
     `BLEND_RGBA_ADD` (SOMA, não mais subtração — numa camada que começa
     ESCURA, precisa SOMAR claridade pra clarear, nunca subtrair, que só
     escureceria ainda mais). Liga/desliga junto com `is_night()`
     (binário, não gradual).
"""
from __future__ import annotations
import time
import pygame

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False   # fallback: loop Python (mesmo padrão de ui/minimap.py)

from engine.tileset import TILE_SIZE, OBJECT_MAPPING
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
# resolve o problema do acinzentado sozinho (histórico, alpha blending):
# interpolar RGB em linha reta direto do azul da noite pro laranja do
# dia passava por um acastanhado dessaturado no meio (bug real, ver
# PROBLEMAS_ARQUITETURA.md §195) — a âncora de amanhecer/entardecer
# nascia como um tom ROSA/AURORA (não laranja puro) só pra evitar isso.
# Com MULTIPLICAÇÃO (22/09/2026, §203) esse problema específico é bem
# mais fraco — multiplicar 2 cores intermediárias não produz o mesmo
# "vale de saturação" que misturar por transparência produzia — mas as
# âncoras continuam próximas do branco/claras de propósito (nunca um
# multiplicador muito escuro no amanhecer/entardecer), porque um
# multiplicador ESCURO no meio da transição escureceria a cena inteira
# ali, não só tingiria — mantém a mesma disciplina de "âncora vizinha",
# agora por um motivo ligeiramente diferente.
#
# "Resetar padrão" no F12 volta pra ESTES valores.
#
# 22/09/2026 — valores redefinidos pra semântica de MULTIPLICAÇÃO (antes
# eram cor de tingimento misturada por transparência, ver histórico em
# §195/§196/§200 — obsoleto). Noite/Dia vieram de instrução explícita do
# usuário; Amanhecer/Entardecer são o palpite inicial desta reescrita
# (usuário decidiu ajustar ao vivo no F12 depois, mesmo fluxo de sempre).
#
# O campo ALPHA continua existindo e editável no F12/config.json por
# decisão explícita do usuário ("manter mesmo sem uso real") — a
# multiplicação de cor não tem conceito de transparência, então nenhum
# cálculo em `day_night_color()`/`DayNightSystem.render()` LÊ esses 3
# valores de alpha pra decidir a cor final. Existem só pra não quebrar o
# editor/config já construído — NUNCA reintroduzir lógica que dependa
# deles sem repensar o desenho primeiro.
DEFAULT_NIGHT_COLOR: tuple[int, int, int] = (30, 40, 80)
DEFAULT_NIGHT_ALPHA: int = 230
DEFAULT_DAWN_COLOR:  tuple[int, int, int] = (255, 205, 190)
DEFAULT_DAWN_ALPHA:  int = 15
DEFAULT_DUSK_COLOR:  tuple[int, int, int] = (255, 190, 175)
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


# Multiplicador identidade — multiplicar qualquer cor por branco a
# preserva exatamente. É o "meio-dia"/"sem efeito nenhum" da técnica de
# MULTIPLICAÇÃO (22/09/2026, §203) — equivalente ao antigo alpha=0 da
# técnica de transparência, só que expresso como cor, não como
# transparência (esta técnica não tem canal de transparência).
_MULT_IDENTITY: tuple[int, int, int] = (255, 255, 255)


def _build_keyframes() -> list[tuple[float, tuple[int, int, int], int]]:
    """Keyframes (fração 0.0-1.0, RGB multiplicador, alpha 0-255 — alpha
    é VESTIGIAL, ver comentário nas constantes DEFAULT_*, não influencia
    `day_night_color()`) derivados das 3 âncoras atuais — recalculado a
    cada chamada (8 tuplas fixas, custo desprezível). Ordem crescente de
    fração, terminando em 1.00 com o MESMO valor de 0.00 (loop sem
    costura)."""
    night = (tuple(_night_color), _night_alpha)
    dawn  = (tuple(_dawn_color),  _dawn_alpha)
    dusk  = (tuple(_dusk_color),  _dusk_alpha)
    clear = (_MULT_IDENTITY, 0)
    return [
        (0.00, *night),   # meia-noite
        (0.15, *night),   # madrugada (platô)
        (0.23, *dawn),    # amanhecer pleno
        (0.32, *clear),   # manhã — sem efeito (multiplica por branco)
        (0.68, *clear),   # tarde — sem efeito (multiplica por branco)
        (0.77, *dusk),    # entardecer pleno
        (0.85, *night),   # anoitecer -> noite
        (1.00, *night),   # volta pra meia-noite
    ]

# Rótulo simples por faixa de fração — usado pra exibição (F12/debug) E
# como corte da fase "Noite" que `is_night()` consulta (luz de lampião +
# camada de sprite "aceso", ver docstring de `is_night()`). Fronteiras de
# Noite ANCORADAS em minuto do ciclo de 60min (23/09/2026, pedido
# explícito do usuário: luz apaga no minuto 12, acende no minuto 49) — só
# o início de Amanhecer e o fim de Entardecer se movem pra abrir espaço;
# Dia e o resto de Amanhecer/Entardecer não mudam. Ver
# PROBLEMAS_ARQUITETURA.md §204.
_LIGHT_OFF_MINUTE = 12.0
_LIGHT_ON_MINUTE = 49.0
_CYCLE_MINUTES = DAY_NIGHT_CYCLE_S / 60.0
_light_off_frac = _LIGHT_OFF_MINUTE / _CYCLE_MINUTES
_light_on_frac = _LIGHT_ON_MINUTE / _CYCLE_MINUTES
_PHASE_LABELS: list[tuple[float, float, str]] = [
    (0.00, _light_off_frac, "Noite"),
    (_light_off_frac, 0.29, "Amanhecer"),
    (0.29, 0.71, "Dia"),
    (0.71, _light_on_frac, "Entardecer"),
    (_light_on_frac, 1.00, "Noite"),
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


def is_night(now: float | None = None) -> bool:
    """Corte BINÁRIO — True só durante a fase "Noite" (22/09/2026, pedido
    do usuário). Ponto único de verdade pra qualquer efeito que deve
    ligar/desligar exatamente junto (luz de lampião + camada de sprite
    "aceso", ver DayNightSystem.render() e
    ui/tile_render_system.py::get_world_objects()) — NUNCA reimplementar
    esse corte comparando fração/keyframe em outro lugar. Diferente do
    tingimento ambiente (`day_night_color()`), que continua suave/
    gradual — só os efeitos de LUZ (lampião etc.) são binários."""
    return current_phase_label(now) == "Noite"


def day_night_color(now: float | None = None) -> tuple[int, int, int, int]:
    """Fator multiplicador (R,G,B) atual + alpha VESTIGIAL (4º valor,
    nunca consumido por `DayNightSystem.render()` — ver comentário nas
    constantes DEFAULT_*) — ver docstring do módulo. (255,255,255) =
    sem efeito algum. Keyframes recalculados a cada chamada
    (`_build_keyframes()`) pra sempre refletir as âncoras atuais,
    inclusive editadas ao vivo pelo F12."""
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
        # Mapa fechado (caverna/dungeon) — setado por game.py::
        # _load_ambient_zones() sempre que o mapa troca (ver
        # engine/map_loader.py, chave "day_night_affected" do
        # <mapa>_entities.json). True = padrão (mundo aberto, segue o
        # relógio real). False = sempre escuro (âncora de Noite fixa,
        # NUNCA a "dia pleno" do relógio real) — ver render(), correção
        # 23/09/2026: a 1ª versão desligava o tingimento inteiro nesse
        # caso, o que na prática deixava a caverna sempre no brilho
        # MÁXIMO (multiplicar por branco = sem escurecer nada), o
        # oposto do pretendido pra um lugar sem luz do sol.
        self.map_affected: bool = True
        self._overlay_surf: "pygame.Surface | None" = None
        self._overlay_size: tuple[int, int] = (0, 0)
        # Cache de gradiente ADITIVO por (raio em PIXELS, intensidade%) —
        # 22/09/2026, §203. Precisa incluir intensity_pct na CHAVE (não só
        # o raio, como na v1 baseada em SUB) porque BLEND_RGBA_ADD, igual
        # BLEND_RGBA_SUB antes dele, NÃO é ponderado por `Surface.
        # set_alpha()` — confirmado empiricamente (script de verificação,
        # scratchpad) antes de escrever este código: aplicar
        # `set_alpha(128)` numa Surface e usá-la com BLEND_RGBA_ADD dá
        # EXATAMENTE o mesmo resultado que sem `set_alpha()` nenhum — a
        # intensidade só afeta o resultado se for "assada" nos próprios
        # valores RGB do gradiente no momento em que ele é gerado. Poucas
        # combinações reais existem na prática (catálogo pequeno), sem
        # risco de o cache crescer sem controle.
        self._light_gradient_cache: dict[tuple[int, int], "pygame.Surface"] = {}
        # Cache do brilho quente — SÓ por raio (não por intensidade, ver
        # `_get_warm_glow_gradient`): usa blend NORMAL (sem special_flags),
        # que é ponderado por `set_alpha()` de verdade — diferente do
        # gradiente aditivo acima, dá pra escalar a intensidade no BLIT,
        # sem precisar assar ela nos pixels no momento de gerar.
        self._warm_glow_cache: dict[int, "pygame.Surface"] = {}
        # Clusters de luz ESTÁTICA do mapa (lampião/tocha etc.) — 1 Surface
        # pré-composta por cluster inteiro, construída 1x em
        # build_static_light_clusters() (chamado por game.py a cada troca
        # de mapa), nunca recalculada por frame. Ver docstring do método
        # (27/09/2026 — vilarejo com 20+ lampiões agrupados custava
        # ~6-8ms/frame desenhando cada luz separada TODO frame).
        self._static_clusters: list[dict] = []

    def _get_light_add_gradient(self, radius_px: int, intensity_pct: int) -> "pygame.Surface":
        """Gradiente radial ADITIVO cacheado por (raio, intensidade) —
        branco (255,255,255) no centro ESCALADO pela intensidade,
        decaindo linearmente até preto (0,0,0) na borda. Usado com
        BLEND_RGBA_ADD sobre a camada de escuridão (que começa ESCURA,
        ver `_MULT_IDENTITY`): somar clareia de volta em direção ao
        branco — a borda soma (0,0,0), no-op, then o centro soma o
        máximo permitido pela intensidade. NUNCA usar `set_alpha()` pra
        modular a força — ver comentário do cache acima, não tem efeito
        nenhum nesse blend flag.

        27/09/2026 (achado da revisão de performance, documento do plano
        de ação): o cache já evita recalcular pra um (raio, intensidade)
        já visto, mas o cache-miss em si usava um loop Python pixel a
        pixel (`set_at`) — O(raio²), ~102 mil chamadas pra um raio de 5
        tiles. Como luz de MAPA (estática) é toda pré-computada 1x no
        load (`build_static_light_clusters`), esse custo só sobra pra luz
        de ITEM DE JOGADOR (dinâmica) na primeira vez que um raio/
        intensidade novo aparece — vetorizado com numpy (fallback pro
        loop antigo se numpy não estiver disponível, mesmo padrão de
        `ui/minimap.py`)."""
        key = (radius_px, intensity_pct)
        cached = self._light_gradient_cache.get(key)
        if cached is not None:
            return cached
        strength = max(0.0, min(1.0, intensity_pct / 100.0))
        d = radius_px * 2
        if _NUMPY_OK:
            xs = np.arange(d)[:, None]
            ys = np.arange(d)[None, :]
            dist = np.sqrt((xs - radius_px) ** 2 + (ys - radius_px) ** 2)
            v = np.zeros((d, d), dtype=np.float64)
            mask = dist <= radius_px
            v[mask] = 255 * (1.0 - dist[mask] / radius_px) * strength
            v = np.round(v).astype(np.uint8)
            arr = np.repeat(v[:, :, None], 3, axis=2)
            surf = pygame.surfarray.make_surface(arr)
        else:
            surf = pygame.Surface((d, d))
            surf.fill((0, 0, 0))
            for y in range(d):
                for x in range(d):
                    dist_ = ((x - radius_px) ** 2 + (y - radius_px) ** 2) ** 0.5
                    if dist_ <= radius_px:
                        v_ = round(255 * (1.0 - dist_ / radius_px) * strength)
                        surf.set_at((x, y), (v_, v_, v_))
        self._light_gradient_cache[key] = surf
        return surf

    def _get_warm_glow_gradient(self, radius_px: int) -> "pygame.Surface":
        """Brilho quente (22/09/2026, pedido do usuário) — toque de cor
        amarela/laranja sutil sobre o ponto de luz, desenhado DEPOIS da
        multiplicação, direto no mundo (não na camada_noite) — a
        multiplicação sozinha só CLAREIA de volta a cor original ali
        (via `_get_light_add_gradient`), nunca pinta uma cor de fogo por
        cima; este é o toque estético que reproduz isso. Blend NORMAL
        (sem special_flags) de propósito — é o único gradiente deste
        arquivo que PODE usar `Surface.set_alpha()` pra escalar por
        intensidade no momento do blit (blend normal É ponderado por
        alpha de superfície; ADD/SUB/MULT não são, ver os outros
        métodos) — por isso cacheado só por raio, sem precisar da
        intensidade na chave.

        27/09/2026 — mesmo achado/mesma vetorização de
        `_get_light_add_gradient` (ver docstring lá), aplicado aqui."""
        cached = self._warm_glow_cache.get(radius_px)
        if cached is not None:
            return cached
        d = radius_px * 2
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        peak_alpha = 90   # opacidade BAIXA de propósito (pedido do usuário)
        if _NUMPY_OK:
            xs = np.arange(d)[:, None]
            ys = np.arange(d)[None, :]
            dist = np.sqrt((xs - radius_px) ** 2 + (ys - radius_px) ** 2)
            mask = dist <= radius_px
            alpha = np.zeros((d, d), dtype=np.uint8)
            alpha[mask] = np.round(peak_alpha * (1.0 - dist[mask] / radius_px)).astype(np.uint8)
            rgb = np.zeros((d, d, 3), dtype=np.uint8)
            rgb[..., 0] = 255
            rgb[..., 1] = 200
            rgb[..., 2] = 100
            pygame.surfarray.pixels3d(surf)[:] = rgb
            pygame.surfarray.pixels_alpha(surf)[:] = alpha
            self._warm_glow_cache[radius_px] = surf
            return surf
        for y in range(d):
            for x in range(d):
                dist = ((x - radius_px) ** 2 + (y - radius_px) ** 2) ** 0.5
                if dist <= radius_px:
                    a = round(peak_alpha * (1.0 - dist / radius_px))
                    surf.set_at((x, y), (255, 200, 100, a))
        self._warm_glow_cache[radius_px] = surf
        return surf

    def build_static_light_clusters(self, tilemap) -> None:
        """Varre o `object_matrix` INTEIRO do mapa (1x por troca de mapa,
        chamado por game.py::_load_ambient_zones — nunca no hot path por
        frame) procurando tiles com `TileType.light_source`, agrupa os que
        se alcançam num mesmo cluster e PRÉ-COMPÕE uma única Surface por
        cluster com a soma de todas as luzes daquele grupo (27/09/2026,
        pedido do usuário).

        Motivo: um vilarejo com 20+ lampiões agrupados custava ~6-8ms de
        frame (medido com profiling antes de mexer aqui) porque
        `render()` desenhava cada luz separada TODO frame — o custo de
        somar N luzes sobrepostas (N blits com blend aditivo, cada um
        proporcional à própria área) é o mesmo esteja a câmera parada ou
        andando, já que as posições nunca mudam. Como a posição de um
        lampião é ESTÁTICA (só a câmera se move), esse custo só precisa
        ser pago 1 VEZ — aqui, no carregamento do mapa — e não mais a
        cada frame: `render()` passa a fazer só 1 blit por cluster
        VISÍVEL (não 1 por luz).

        Luz de ITEM DE JOGADOR (dinâmica — se move com quem carrega)
        fica de fora deste mecanismo de propósito, resolvida por luz em
        `render()` como sempre foi — nunca mais que 1-2 simultâneas,
        nunca foi o gargalo medido."""
        self._static_clusters = []
        tile_size = tilemap.tile_size
        lights: list[tuple[float, float, int, float]] = []   # (cx, cy, radius_px, intensity_pct)
        for ry, row in enumerate(tilemap.object_matrix):
            for rx, obj_char in enumerate(row):
                if not obj_char or obj_char == ".":
                    continue
                tile_type = OBJECT_MAPPING.get(obj_char)
                if tile_type is None or tile_type.light_source is None:
                    continue
                # Mesma fórmula de âncora/altura de
                # ui/tile_render_system.py::get_world_objects() (linhas
                # ~508-534), só que em coordenadas de MUNDO (sem câmera) —
                # manter em sincronia se aquele cálculo mudar.
                spr_px_h = tile_type.sprite_px_h
                total_h = spr_px_h if spr_px_h > 0 else tile_size + tile_type.overlay_height
                anchor_y = (ry + 1) * tile_size
                world_x = rx * tile_size
                world_y = anchor_y - total_h
                off_x, off_y, lw, lh, intensity_pct, radius_tiles = tile_type.light_source
                cx = world_x + off_x + lw / 2
                cy = world_y + off_y + lh / 2
                radius_px = radius_tiles * tile_size
                if radius_px <= 0:
                    continue
                lights.append((cx, cy, radius_px, intensity_pct))

        if not lights:
            return

        # União por alcance (union-find) — 2 luzes cujos círculos se
        # tocam (+ folga de 1 tile) entram no mesmo cluster. O(n²) aqui é
        # desprezível: catálogo de luzes por mapa é dezenas, não milhares,
        # e isto roda 1x só por troca de mapa.
        n = len(lights)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        merge_padding_px = tile_size
        for i in range(n):
            xi, yi, ri_, _ = lights[i]
            for j in range(i + 1, n):
                xj, yj, rj_, _ = lights[j]
                dist = ((xi - xj) ** 2 + (yi - yj) ** 2) ** 0.5
                if dist <= ri_ + rj_ + merge_padding_px:
                    union(i, j)

        groups: dict[int, list[int]] = {}
        for idx in range(n):
            groups.setdefault(find(idx), []).append(idx)

        for member_idxs in groups.values():
            members = [lights[i] for i in member_idxs]
            x0 = min(cx - r for cx, cy, r, _i in members)
            y0 = min(cy - r for cx, cy, r, _i in members)
            x1 = max(cx + r for cx, cy, r, _i in members)
            y1 = max(cy + r for cx, cy, r, _i in members)
            rect = pygame.Rect(int(x0), int(y0), int(x1 - x0), int(y1 - y0))
            if rect.width <= 0 or rect.height <= 0:
                continue

            # Camada ADITIVA (mesma técnica de sempre, ver
            # _get_light_add_gradient) — composta 1x, nunca mais.
            add_surf = pygame.Surface((rect.width, rect.height))
            add_surf.fill((0, 0, 0))
            # Camada de brilho quente — alpha real (SRCALPHA), também
            # composta 1x. Cada luz aplica set_alpha NUMA CÓPIA da
            # gradiente cacheada (nunca muta o cache compartilhado, mesma
            # disciplina de bush stealth/corpo — ver comentário em
            # RenderSystem sobre nunca mutar Surface cacheada sem copy()).
            glow_surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            for cx, cy, radius_px, intensity_pct in members:
                local_x = cx - radius_px - rect.x
                local_y = cy - radius_px - rect.y
                gradient = self._get_light_add_gradient(radius_px, round(intensity_pct))
                add_surf.blit(gradient, (local_x, local_y), special_flags=pygame.BLEND_RGBA_ADD)
                glow = self._get_warm_glow_gradient(radius_px).copy()
                glow.set_alpha(round(255 * max(0.0, min(1.0, intensity_pct / 100.0))))
                glow_surf.blit(glow, (local_x, local_y))

            self._static_clusters.append({
                "rect": rect, "add_surf": add_surf, "glow_surf": glow_surf,
            })

    def warm_player_light_gradients(self) -> None:
        """Pré-aquece o cache de gradiente (27/09/2026, achado de
        playtest pós-plano-de-performance) pra toda combinação
        (raio, intensidade) de luz de ITEM já definida no catálogo —
        hoje só `lampiao` (`content/item_table.py`), mas cobre qualquer
        item de luz futuro automaticamente, sem precisar tocar este
        método de novo.

        Luz de item é DINÂMICA (não passa por build_static_light_clusters,
        que só resolve luz de MAPA) — sem pré-aquecer, o cache-miss do
        gradiente só acontece na hora em que o jogador liga a luz de
        verdade (`_get_light_add_gradient`/`_get_warm_glow_gradient`),
        e mesmo vetorizado com numpy isso ainda custa ~12-18ms pra um
        raio como o do lampião (7 tiles → array 448×448) — confirmado
        por medição direta. Chamado 1x no carregamento do jogo (mesmo
        lugar/momento de `build_static_light_clusters`), nunca por
        frame — o custo é o mesmo, só que pago onde não é sentido."""
        from content.item_table import ITEMS
        seen_radius_intensity: set[tuple[int, int]] = set()
        for factory in ITEMS.values():
            item = factory()
            light = getattr(item, "light_source", None)
            if not light:
                continue
            radius_px = round(light["radius_tiles"] * TILE_SIZE)
            intensity_pct = round(light["intensity_pct"])
            if radius_px <= 0:
                continue
            key = (radius_px, intensity_pct)
            if key in seen_radius_intensity:
                continue
            seen_radius_intensity.add(key)
            self._get_light_add_gradient(radius_px, intensity_pct)
            self._get_warm_glow_gradient(radius_px)

    def render(self, world_objects: list | None = None,
              camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        if self.world_surf is None:
            return
        # Só mundo aberto — BG/Arena ficam sempre num horário fixo
        # (decisão do usuário: luz não deve mudar no meio de uma partida).
        iius = self.world.get_component(self.player_entity, InstanceInventoryUIState)
        if iius is not None and iius.active:
            return

        # Luz de item de jogador (23/09/2026) precisa do brilho quente
        # mesmo em dia pleno mundo aberto — o jogador ligou por vontade
        # própria, o toggle não deve "sumir" só porque é meio-dia (mesmo
        # sem efeito visual de furar escuridão nenhuma, que não existe de
        # dia). Sem isso, o early-return de "dia pleno" logo abaixo
        # cortaria a função inteira antes do loop de luz sequer rodar.
        _has_player_light = bool(world_objects) and any(o.get("player_light") for o in world_objects)

        if self.map_affected:
            r, g, b, _a = day_night_color()   # _a vestigial, ver docstring
            if (r, g, b) == _MULT_IDENTITY and not _has_player_light:
                return  # dia pleno e ninguém com luz acesa — economiza o frame inteiro
        else:
            # Mapa fechado (caverna/dungeon) — sem luz do sol, o padrão
            # correto é SEMPRE escuro (âncora de Noite), nunca "amanhece"
            # lá (23/09/2026, correção do usuário: a 1ª versão desligava
            # o efeito inteiro, o que na prática deixava a caverna sempre
            # no brilho MÁXIMO — o oposto do pretendido). NUNCA cai no
            # early-return de "dia pleno" acima — não existe "dia" aqui.
            r, g, b = tuple(_night_color)

        surf_w, surf_h = self.world_surf.get_size()
        if self._overlay_surf is None or self._overlay_size != (surf_w, surf_h):
            # SEM SRCALPHA de propósito (22/09/2026, §203) — a técnica de
            # MULTIPLICAÇÃO precisa dos canais RGB cheios; um canal alpha
            # aqui não teria papel nenhum (BLEND_RGBA_MULT multiplicaria
            # o alpha do MUNDO por ele também, sem necessidade).
            self._overlay_surf = pygame.Surface((surf_w, surf_h))
            self._overlay_size = (surf_w, surf_h)
        self._overlay_surf.fill((r, g, b))

        # Luz de objeto de MAPA (lampião etc.) é BINÁRIA — só na fase
        # Noite, não segue o gradual do multiplicador ambiente (22/09/2026,
        # pedido do usuário — mesmo corte que liga a camada de sprite
        # "aceso" em ui/tile_render_system.py::get_world_objects()). Mapa
        # fechado conta como "sempre noite" pra esse corte — se um dia
        # houver lampião numa dungeon, ele acende o tempo todo (nenhum
        # objeto desse tipo existe nas cavernas atuais, mas o corte já
        # nasce certo).
        #
        # 27/09/2026: luz de mapa (ESTÁTICA) não é mais processada luz-a-
        # luz aqui — já vem pré-composta por CLUSTER inteiro (ver
        # build_static_light_clusters()), 1 blit por cluster VISÍVEL. Só
        # a luz de ITEM DE JOGADOR (23/09/2026, `"player_light": True`,
        # DINÂMICA — se move com quem carrega) continua resolvida
        # individualmente aqui, sempre funciona (dia/noite/mapa aberto ou
        # fechado — ferramenta pra enxergar numa caverna sempre escura,
        # não faria sentido gateá-la igual ao poste automático).
        _ambient_night = is_night() or not self.map_affected
        _lit_glow_blits: list[tuple[float, float, "pygame.Surface"]] = []
        _lit_single_positions: list[tuple[float, float, int, float]] = []

        if _ambient_night and self._static_clusters:
            _screen_rect = pygame.Rect(int(camera_offset_x), int(camera_offset_y), surf_w, surf_h)
            for cluster in self._static_clusters:
                if not cluster["rect"].colliderect(_screen_rect):
                    continue   # cluster inteiro fora da tela — nem olha pro conteúdo
                sx = cluster["rect"].x - camera_offset_x
                sy = cluster["rect"].y - camera_offset_y
                self._overlay_surf.blit(cluster["add_surf"], (sx, sy),
                                        special_flags=pygame.BLEND_RGBA_ADD)
                _lit_glow_blits.append((sx, sy, cluster["glow_surf"]))

        if world_objects:
            for obj in world_objects:
                if not obj.get("player_light"):
                    continue   # luz de mapa (estática) já resolvida acima via cluster
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
                gradient = self._get_light_add_gradient(radius_px, round(intensity_pct))
                self._overlay_surf.blit(
                    gradient, (cx - radius_px, cy - radius_px),
                    special_flags=pygame.BLEND_RGBA_ADD)
                _lit_single_positions.append((cx, cy, radius_px, intensity_pct))

        self.world_surf.blit(self._overlay_surf, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Brilho quente — DEPOIS da multiplicação, direto no mundo (ver
        # docstring de _get_warm_glow_gradient). Reusa as posições já
        # resolvidas nos 2 loops acima, sem recalcular offset/tela à
        # parte. Cluster já vem com o brilho quente de TODOS os membros
        # pré-composto (glow_surf) — 1 blit só, alpha já embutido nos
        # pixels desde build_static_light_clusters() (nunca set_alpha
        # aqui pra cluster — só a luz individual/dinâmica precisa disso).
        for sx, sy, glow_surf in _lit_glow_blits:
            self.world_surf.blit(glow_surf, (sx, sy))
        for cx, cy, radius_px, intensity_pct in _lit_single_positions:
            warm = self._get_warm_glow_gradient(radius_px)
            warm.set_alpha(round(255 * max(0.0, min(1.0, intensity_pct / 100.0))))
            self.world_surf.blit(warm, (cx - radius_px, cy - radius_px))
