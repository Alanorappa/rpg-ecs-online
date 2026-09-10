"""
engine/character_animation.py
Animação base de personagem (idle/andar × 4 direções × classe) —
implementação incremental: cada arquivo ausente cai em `None`, e quem
chama (`ui/systems.py::RenderSystem`) decide o fallback (retângulo do
pygame). Nunca decide sozinho o desenho do jogo; é chamado só depois de
`RenderSystem` já ter resolvido que a entidade não tem override de
aparência ativo (Camuflagem/Polimorfia, ver `engine/entity_disguise.py`
— mecanismo IRMÃO, mas conceitualmente diferente: aquele é override
TEMPORÁRIO condicional, este é a aparência BASE, sempre presente).

Convenção de arquivo em assets/sprites/, por `class_id`:
  {class_id}_idle_{direction}.png  — sheet horizontal, personagem parado
  {class_id}_run_{direction}.png   — sheet horizontal, personagem andando
  direction ∈ "down"/"up"/"left"/"right"
  Cada sheet é cortado em frames de CHAR_TILE_SIZE (96px, 09/09/2026 —
  frames NÃO são quadrados, 96 largura × altura variável conforme o
  arquivo; largura fixa por decisão do usuário, não derivável da altura
  do sheet) de largura, altura = altura do sheet (pode ser maior que 1
  tile — personagem ancorado no PÉ, mesma convenção de
  `entity_disguise.py`). Diferente de `entity_disguise.py`: aqui IDLE
  também é uma sheet animada (várias frames), nunca um frame único
  parado — personagem "respira" mesmo quieto.

  Espelhamento (decisão do usuário, 09/09/2026): se faltar o arquivo de
  "left" mas existir o de "right" (ou vice-versa), usa o que existir e
  espelha horizontalmente pro lado que falta — evita desenhar os 2 lados
  à mão. Se NENHUM dos dois existir, retorna None (fallback de quem
  chama).

Headless por padrão (import de pygame só dentro da função de render,
mesmo padrão de `entity_disguise.py`) — mas este módulo só faz sentido
no cliente (servidor nunca desenha nada, `facing`/animação são
puramente visuais).
"""
from __future__ import annotations

CHAR_TILE_SIZE = 96
DEFAULT_FRAME_MS = 78  # 65 * 1.2 — animação 20% mais lenta (09/09/2026, pedido do usuário)

# Cache de módulo, chaveado por (class_id, state, direction) — conjunto
# BEM pequeno e fixo (3 classes × 2 estados × 4 direções = 24 no
# máximo), nunca por-entidade — sem risco de crescer sem teto (ver
# CLAUDE.md "proibido estado mutável ad-hoc fora do ECS").
_frame_cache: dict[tuple, list] = {}


def _load_sheet_frames(class_id: str, state: str, direction: str) -> list:
    """Carrega e corta `{class_id}_{state}_{direction}.png` em frames de
    CHAR_TILE_SIZE de largura. Lista vazia se o arquivo não existir."""
    import pygame
    import os
    from paths import resource_path

    fname = f"{class_id}_{state}_{direction}.png"
    path  = resource_path(os.path.join("assets", "sprites", fname))
    if not os.path.exists(path):
        return []
    try:
        sheet = pygame.image.load(path).convert_alpha()
    except Exception:
        return []
    frame_h = sheet.get_height()
    frames  = [
        sheet.subsurface(pygame.Rect(i * CHAR_TILE_SIZE, 0, CHAR_TILE_SIZE, frame_h)).copy()
        for i in range(sheet.get_width() // CHAR_TILE_SIZE)
    ]
    return frames


_MIRROR_OF = {"left": "right", "right": "left"}


def _frames_for(class_id: str, state: str, direction: str) -> list:
    """Frames de (class_id, state, direction), com fallback de
    espelhamento pra "left"/"right" quando só o lado oposto existe."""
    key = (class_id, state, direction)
    if key in _frame_cache:
        return _frame_cache[key]

    frames = _load_sheet_frames(class_id, state, direction)
    if not frames and direction in _MIRROR_OF:
        # Espelha só a partir do arquivo DIRETO do lado oposto — nunca
        # via _frames_for recursivo, senão "left" sem arquivo nenhum
        # tenta espelhar "right" sem arquivo nenhum, que tenta espelhar
        # "left" de volta, infinitamente (bug real pego testando).
        mirror_dir = _MIRROR_OF[direction]
        source = _load_sheet_frames(class_id, state, mirror_dir)
        if source:
            import pygame
            frames = [pygame.transform.flip(f, True, False) for f in source]

    _frame_cache[key] = frames
    return frames


def get_character_frame(class_id: str, state: str, direction: str,
                         anim_time_ms: int, frame_ms: int = DEFAULT_FRAME_MS):
    """Retorna o Surface do frame atual da animação BASE do personagem,
    ou None se não existir nenhum sprite pra essa combinação ainda
    (nem direto, nem espelhado) — caller (RenderSystem) cai pro
    retângulo colorido.

    state: "idle" ou "run" (ver `TileMovement.is_moving`).
    direction: "down"/"up"/"left"/"right" (ver `TileMovement.facing`,
    calculado por `engine.utils.facing_from_delta`)."""
    frames = _frames_for(class_id, state, direction)
    if not frames:
        return None
    idx = (anim_time_ms // frame_ms) % len(frames)
    return frames[idx]


def resolve_anim_state(tile_movement) -> str:
    """"idle" ou "run" pra animação — ponto único de verdade pra "o que
    conta como andando" (09/09/2026). NÃO é só `tile_movement.is_moving`
    puro: `anim_walk_grace_s` (setado por `TileMovementSystem` no
    instante em que um passo termina) cobre o frame único em que
    `is_moving` pisca False no MEIO de uma caminhada contínua —
    `PlayerInputSystem` (ordem 5) só inicia o passo seguinte no PRÓXIMO
    frame, depois de `TileMovementSystem` (ordem 23) já ter terminado o
    atual no frame corrente. Sem esse grace, a animação flicava pro
    estado "idle" àquele frame, pra qualquer direção (bug real relatado
    pelo usuário). Puramente cosmético — combate/gameplay continuam
    lendo `is_moving` puro, nunca isto."""
    return "run" if (tile_movement.is_moving or tile_movement.anim_walk_grace_s > 0) else "idle"
