# entity_disguise.py
"""
Mecanismo genérico de aparência-sobreposta-temporária de entidade —
descoberta de variantes + frame animado + resolução de "qual override
está ativo agora". Categoria de efeito (mesmo padrão de
`ui/effect_animator.py`/`ui/systems.py::_get_ground_effect_sprite`:
1 módulo por CATEGORIA, tabela de dado, nunca 1 arquivo/branch por
skill), não uma skill específica — nenhuma skill é hardcoded no
MECANISMO, só na TABELA de fontes conhecidas no fim do arquivo.

Referência real (pesquisada, não copiada de memória — ver
PROBLEMAS_ARQUITETURA.md §12/§13): WoW resolve troca de aparência
(poção, skill em si mesmo, skill em alvo, zona) por UM mecanismo
genérico (`Unit::SetDisplayId`) + prioridade entre auras de
transformação ativas — a Blizzard consolidou isso no patch 6.0.2
depois de ter implementações fragmentadas por spell (mesmo erro que
este módulo já cometeu 2x antes de acertar). Adaptado pra ECS: em vez
de um componente NOVO sincronizado por rede (que duplicaria estado já
sincronizado por outro caminho), a resolução lê o estado que CADA
efeito já mantém do jeito que já mantém (`CombatStats.camouflage_*`,
`StatusEffects`, etc.) — zero mudança de protocolo, zero componente
novo. `_APPEARANCE_SOURCES` é a tabela; adicionar uma 4ª fonte futura é
1 função checadora pequena + 1 linha na tabela, nunca um novo `elif`.

Convenção de arquivo em assets/sprites/, por `base_name`:
  {base_name}_idle{suf}.png  — frame parado (32×32)
  {base_name}_run{suf}.png   — sheet horizontal de frames de movimento
  sufixo "" = variante base; variantes extras seguem _2, _3, _4...
  (descoberta dinâmica — adicionar uma variante nova não exige mudança
  de código, só soltar o PNG na pasta).

`discover_sprite_variants()` é headless (sem pygame) de propósito —
chamada tanto pelo cliente (`ui/`) quanto pelo servidor
(`server/spell_completion_processor.py`, pra sortear a variante
autoritativamente). `get_animated_disguise_frame()` usa pygame e é
client-only (renderização de fato) — import local, mesmo padrão já
tolerado em `engine/world_systems.py` (pygame só dentro de métodos que
renderizam, nunca no topo do módulo). `get_active_appearance_override()`
é headless também (só lê componentes, não desenha nada).
"""
from __future__ import annotations

TILE_SIZE = 32

# Duração padrão de cada frame da animação de "run" — tunado originalmente
# pra Camuflagem (-15% de velocidade vs 55ms); fonte nova pode passar
# frame_ms próprio se precisar de um ritmo diferente.
DEFAULT_FRAME_MS = 65

# Cache de módulo, chaveado por base_name (padrão do projeto — ver
# ui/floating_text.py::_outline_cache e CLAUDE.md "Proibido estado
# mutável ad-hoc fora do ECS").
_variants_cache: dict[str, list[str]] = {}
_frame_cache: dict[tuple, list] = {}


def discover_sprite_variants(base_name: str) -> list[str]:
    """Sufixos de variantes disponíveis pra `{base_name}_idle{suf}.png` +
    `{base_name}_run{suf}.png` em assets/sprites/. Ver docstring do
    módulo pra convenção completa."""
    import os
    from paths import resource_path

    if base_name in _variants_cache:
        return _variants_cache[base_name]

    variants: list[str] = []
    base_dir = resource_path(os.path.join("assets", "sprites"))
    n = 1
    suf = ""
    while True:
        idle_path = os.path.join(base_dir, f"{base_name}_idle{suf}.png")
        run_path  = os.path.join(base_dir, f"{base_name}_run{suf}.png")
        if os.path.exists(idle_path) and os.path.exists(run_path):
            variants.append(suf)
            n += 1
            suf = f"_{n}"
        else:
            break

    _variants_cache[base_name] = variants
    return variants


def get_animated_disguise_frame(base_name: str, variant_suffix: str, moving: bool,
                                anim_time_ms: int, frame_ms: int = DEFAULT_FRAME_MS):
    """Retorna o Surface do frame atual do disfarce `base_name` (ver
    docstring do módulo pra convenção de arquivo).

    moving=False → frame único de {base_name}_idle{suf}.png (32×32).
    moving=True  → frame cíclico de {base_name}_run{suf}.png (sheet
                   32×H, N frames lado a lado), avançando 1 frame a
                   cada `frame_ms`.
    Cache de módulo por (base_name, kind, variante). Retorna None se o
    arquivo não existir (caller decide o fallback — ex: placeholder
    geométrico enquanto o asset real não existe).
    """
    import pygame
    import os
    from paths import resource_path

    kind = "run" if moving else "idle"
    key  = (base_name, kind, variant_suffix)
    if key not in _frame_cache:
        fname      = f"{base_name}_{kind}{variant_suffix}.png"
        sheet_path = resource_path(os.path.join("assets", "sprites", fname))
        frames: list = []
        if os.path.exists(sheet_path):
            try:
                sheet = pygame.image.load(sheet_path).convert_alpha()
                if moving:
                    frame_w = TILE_SIZE  # frames sempre 1 tile de largura
                    frame_h = sheet.get_height()
                    for i in range(sheet.get_width() // frame_w):
                        frames.append(sheet.subsurface(
                            pygame.Rect(i * frame_w, 0, frame_w, frame_h)).copy())
                else:
                    frames.append(sheet)
            except Exception:
                frames = []
        _frame_cache[key] = frames

    frames = _frame_cache[key]
    if not frames:
        return None
    if not moving:
        return frames[0]
    idx = (anim_time_ms // frame_ms) % len(frames)
    return frames[idx]


# ─────────────────────────────────────────────────────────────────────
# Resolução de qual override de aparência está ativo numa entidade —
# tabela de checadores, uma por fonte conhecida. Mutuamente exclusivas
# na prática hoje (decisão do usuário, 07/08/2026) — a primeira que
# bater vence; não há prioridade/empilhamento porque não é um caso real
# ainda (se um dia precisar, é aqui que entra, sem mexer no resto).
# ─────────────────────────────────────────────────────────────────────

def _check_camouflage(world, eid: int) -> "str | None":
    """Retorna a variante ativa da Camuflagem (sufixo, pode ser ""),
    ou None se não estiver camuflado. Lê CombatStats.camouflage_timer/
    camouflage_object — já sincronizado por rede pelo caminho normal de
    STATS_UPDATE, nenhum estado novo precisa ser mantido aqui."""
    from engine.components import CombatStats as _CS_check
    cs = world.get_component(eid, _CS_check)
    if cs is not None and getattr(cs, "camouflage_timer", 0.0) > 0:
        return getattr(cs, "camouflage_object", "") or ""
    return None


def _check_polymorph(world, eid: int) -> "str | None":
    """Retorna a variante ativa da Polimorfia, ou None se não estiver
    polimorfizado. Lê StatusEffects.has("polymorph") — já sincronizado
    pelo caminho genérico de status effect, nenhum estado novo. Sem
    variante própria ainda (sempre "" — Polimorfia hoje não sorteia
    "qual animal", só ativa/inativa; variantes por animal são extensão
    futura, se/quando pedido)."""
    from engine.components import StatusEffects as _SFX_check
    sfx = world.get_component(eid, _SFX_check)
    if sfx is not None and sfx.has("polymorph"):
        return ""
    return None


# sprite_base → função checadora. Adicionar uma fonte nova de troca de
# aparência (poção, skill em alvo, o que for): 1 função checadora +
# 1 linha aqui. Nunca um novo branch no render loop.
_APPEARANCE_SOURCES: "list[tuple[str, object]]" = [
    ("camuflagem", _check_camouflage),
    ("polimorfia", _check_polymorph),
]


def get_active_appearance_override(world, eid: int) -> "tuple[str, str] | None":
    """Retorna (sprite_base, variant_suffix) da fonte de override de
    aparência ATIVA nesta entidade agora, ou None se nenhuma estiver
    ativa. Percorre `_APPEARANCE_SOURCES` na ordem — primeira que bater
    vence (sem prioridade real hoje, ver docstring da seção acima)."""
    for sprite_base, checker in _APPEARANCE_SOURCES:
        variant = checker(world, eid)
        if variant is not None:
            return sprite_base, variant
    return None
