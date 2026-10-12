"""
engine/mob_animation.py
Animação base de MOB (idle/andar/golpe × 4 direções × mob) — irmão
separado de `engine/character_animation.py` (personagem jogável), nunca
uma extensão dele: a geometria de frame é diferente de propósito.
Jogador usa frame de largura FIXA (`CHAR_TILE_SIZE=96`, só 3 classes,
tamanho humano uniforme); mob varia muito de tamanho (rato a dragão),
então o frame aqui é QUADRADO, derivado da própria altura da folha
(largura = altura, contagem = largura_total // altura) — mesma
convenção que `ui/world_icon_animator.py` já usa pra ícone animado no
mundo. A MÁQUINA DE ESTADOS (golpe > andando/parado, expiração sozinha)
espelha `character_animation.py` de propósito — mesmo princípio, API
parecida — mas não importa dele, porque ele carrega frame com a largura
fixa de personagem.

Convenção de arquivo em assets/sprites/, por chave de mob (slug, ver
`engine.utils.slugify`, derivado de `EntityIdentity.mob_key or race` —
já chega pela rede hoje, nenhum campo de protocolo novo):
  {slug}_idle_{direction}.png    — sheet horizontal, mob parado
  {slug}_run_{direction}.png     — sheet horizontal, mob andando
  {slug}_attack_{direction}.png  — golpe (só auto-attack por enquanto,
                                    mob não tem skill própria com sprite
                                    dedicada ainda — sem cascata de 3
                                    níveis como personagem)
  direction ∈ "down"/"up"/"left"/"right"

Implementação incremental (10/10/2026, pedido do usuário — "quando não
acha a animação na pasta, da fallback para o quadrado"): arquivo
ausente → None → `RenderSystem` cai pro retângulo colorido de sempre,
por ESTADO/direção específico, nunca tudo-ou-nada (mob pode ter só
idle_down pronto e já usa ele, o resto continua retângulo).

Espelhamento (mesma convenção do personagem): falta "left" mas existe
"right" (ou vice-versa) → usa o que existir e espelha horizontalmente.

Headless por padrão (import de pygame só dentro da função de render) —
só faz sentido no cliente (servidor nunca desenha nada)."""
from __future__ import annotations

MOB_DEFAULT_FRAME_MS = 120  # mesmo ritmo de ui/world_icon_animator.py

MOB_ATTACK_STATE = "attack"

# Cache de módulo, chaveado por (mob_key, state, direction) — mesmo
# padrão de `character_animation._frame_cache`: conjunto pequeno (1
# entrada por combinação de mob/estado/direção que já apareceu em
# tela), nunca por-entidade (ver CLAUDE.md "proibido estado mutável
# ad-hoc fora do ECS").
_mob_frame_cache: dict[tuple, list] = {}


def _load_mob_sheet_frames(mob_key: str, state: str, direction: str) -> list:
    """Carrega e corta `{mob_key}_{state}_{direction}.png` em frames
    QUADRADOS (largura = altura da folha). Lista vazia se o arquivo não
    existir — mesmo fallback pra arquivo sem direção
    (`{mob_key}_{state}.png`) que `character_animation.py` já usa."""
    import pygame
    import os
    from paths import resource_path

    fname = f"{mob_key}_{state}_{direction}.png"
    path  = resource_path(os.path.join("assets", "sprites", fname))
    if not os.path.exists(path):
        fname_nodir = f"{mob_key}_{state}.png"
        path = resource_path(os.path.join("assets", "sprites", fname_nodir))
        if not os.path.exists(path):
            return []
    from ui.asset_cache import get_image
    sheet = get_image(path)
    if sheet is None:
        return []
    frame_h = sheet.get_height()
    if frame_h <= 0:
        return []
    frames = [
        sheet.subsurface(pygame.Rect(i * frame_h, 0, frame_h, frame_h)).copy()
        for i in range(sheet.get_width() // frame_h)
    ]
    return frames


_MOB_MIRROR_OF = {"left": "right", "right": "left"}


def _mob_frames_for(mob_key: str, state: str, direction: str) -> list:
    """Frames de (mob_key, state, direction), com fallback de
    espelhamento pra "left"/"right" quando só o lado oposto existe —
    mesma lógica de `character_animation._frames_for`."""
    key = (mob_key, state, direction)
    if key in _mob_frame_cache:
        return _mob_frame_cache[key]

    frames = _load_mob_sheet_frames(mob_key, state, direction)
    if not frames and direction in _MOB_MIRROR_OF:
        mirror_dir = _MOB_MIRROR_OF[direction]
        source = _load_mob_sheet_frames(mob_key, state, mirror_dir)
        if source:
            import pygame
            frames = [pygame.transform.flip(f, True, False) for f in source]

    _mob_frame_cache[key] = frames
    return frames


_mob_top_opaque_cache: dict[str, int] = {}


def get_mob_top_opaque_offset(mob_key: str) -> int:
    """Linha (a partir do topo do FRAME, 0 = topo) do pixel opaco mais
    alto da animação "idle"/"down" de `mob_key` — mesma técnica de
    `ui/tile_sprite_manager.py::get_top_opaque_y` (`pygame.mask.
    from_surface().get_bounding_rects()`, threshold padrão 127),
    aplicada ao MÍNIMO entre todos os frames do idle (cobre variação de
    "respiração" sem recalcular por pose atual).

    Calculado 1x e CACHEADO por mob_key — referência estável pro
    nameplate (`ui/render_system.py`/`client/remote_entity_handlers.py::
    _draw_mob_hp_bars`), nunca recomputada pela pose/direção
    EXIBIDA agora (evitaria "balançar" entre estados — mesmo motivo de
    `character_animation.NAMEPLATE_CHAR_FRAME_TOP_INSET_PX` ser fixo
    pro personagem, 26/09/2026). Diferença: ali o valor é um número
    universal fixo (só 3 classes humanas uniformes); aqui é DERIVADO da
    arte real de cada mob, porque a proporção varia demais (rato a
    dragão) pra um único número servir — sem isso, mob com bastante
    padding transparente no topo do frame (comum — frame quadrado
    "sobra" espaço que a largura/altura do personagem de verdade não
    usa) deixa o nameplate flutuando longe da cabeça (10/10/2026, bug
    real relatado pelo usuário com o Goblin Guerreiro: frame 64×64, mas
    o goblin só ocupa as linhas 14–45; usar o topo CRU do frame como
    referência deixava 14px + o gap de verdade entre nameplate e
    cabeça). 0 se não existir nenhum frame idle ainda."""
    cached = _mob_top_opaque_cache.get(mob_key)
    if cached is not None:
        return cached
    frames = _mob_frames_for(mob_key, "idle", "down")
    if not frames:
        return 0
    import pygame
    top = min(
        (r.top for f in frames for r in pygame.mask.from_surface(f).get_bounding_rects()),
        default=0)
    _mob_top_opaque_cache[mob_key] = top
    return top


def get_mob_frame(mob_key: str, state: str, direction: str,
                   anim_time_ms: int, frame_ms: int = MOB_DEFAULT_FRAME_MS):
    """Surface do frame atual de animação de mob, ou None se não existir
    nenhum sprite pra essa combinação (nem direto, nem espelhado) —
    `RenderSystem` cai pro retângulo colorido nesse caso."""
    frames = _mob_frames_for(mob_key, state, direction)
    if not frames:
        return None
    idx = int(anim_time_ms // frame_ms) % len(frames)
    return frames[idx]


def resolve_mob_anim_state(tile_movement) -> str:
    """"idle" ou "run" — simplificado em relação a
    `character_animation.resolve_anim_state` (sem `anim_walk_grace_s`):
    aquele grace cobre um detalhe de timing específico do
    `PlayerInputSystem` local; mob (local por IA ou espelho remoto por
    ENTITY_MOVE) não passa por esse caminho, então `is_moving` puro já
    basta."""
    return "run" if tile_movement.is_moving else "idle"


def trigger_mob_attack_anim(tile_movement, mob_key: str, now_ms: int,
                            missed: bool = False) -> None:
    """Liga a animação de golpe de um mob — chamada no momento em que o
    servidor confirma o resultado do golpe (COMBAT_RESULT), mesmo
    princípio de `character_animation.trigger_attack_anim`: servidor
    confirma, cliente só reage. Simplificado (sem cascata de sid/golpe
    em loop) porque mob só tem auto-attack por enquanto — sem skill
    própria com sprite dedicada."""
    from .character_animation import MISS_ATTACK_SLOWDOWN, DEFAULT_FRAME_MS
    tile_movement.anim_attack_state      = MOB_ATTACK_STATE
    tile_movement.anim_attack_started_ms = now_ms
    tile_movement.anim_attack_frame_ms   = int(
        MOB_DEFAULT_FRAME_MS * (MISS_ATTACK_SLOWDOWN if missed else 1.0))
    tile_movement.anim_attack_loop_duration_ms = 0
    tile_movement.anim_attack_hold_last_frame  = False


def resolve_mob_display_state(tile_movement, mob_key: str, now_ms: int,
                              frame_ms: int = MOB_DEFAULT_FRAME_MS) -> "tuple[str, int, float]":
    """(state_name, anim_time_ms, frame_ms) prontos pra `get_mob_frame`
    — mesmo princípio de `character_animation.resolve_display_state`:
    golpe tem prioridade enquanto durar 1 ciclo do sheet, expira sozinho
    (ou na hora, se o sprite não existir pra essa direção) e cai pro
    idle/run."""
    if tile_movement.anim_attack_state:
        _attack_frame_ms = tile_movement.anim_attack_frame_ms or frame_ms
        frames  = _mob_frames_for(mob_key, tile_movement.anim_attack_state, tile_movement.facing)
        elapsed = now_ms - tile_movement.anim_attack_started_ms
        duration = len(frames) * _attack_frame_ms if frames else 0
        if frames and elapsed < duration:
            return tile_movement.anim_attack_state, elapsed, _attack_frame_ms
        tile_movement.anim_attack_state = ""
    return resolve_mob_anim_state(tile_movement), now_ms, frame_ms
