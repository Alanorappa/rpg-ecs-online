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
  {class_id}_idle_{direction}.png    — sheet horizontal, personagem parado
  {class_id}_run_{direction}.png     — sheet horizontal, personagem andando
  {class_id}_attack_{direction}.png  — golpe genérico (auto-attack, ou
                                        skill SEM sprite própria)
  {class_id}_{skill_id}_{direction}.png — golpe ESPECÍFICO de uma skill
                                        (ex: "guerreiro_golpe_poderoso_
                                        down.png") — opcional; sem ela,
                                        cai no "attack" genérico acima
                                        (ver `resolve_attack_state_name`)
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

# Tile de colisão/pé real (32×32, == TILE_SIZE) dentro do frame de
# 96×H (10/09/2026, decisão do usuário — convenção fixa de arte,
# mesma pra idle/run/attack/qualquer skill futura). Offset é a posição
# do CANTO SUPERIOR ESQUERDO desse tile dentro do frame — horizontal
# cai centrado por construção (32 + 32/2 == CHAR_TILE_SIZE/2 == 48),
# vertical NÃO (mais espaço acima do tile — corpo/cabeça — que abaixo).
# Usado tanto pra ancorar o blit (`character_anchor_offset`) quanto
# pro Y-sort (`ui/systems.py::RenderSystem`) — NUNCA usar
# CHAR_TILE_SIZE (largura do FRAME) nem a altura do sheet pra nenhum
# dos dois, é sempre este tile de 32×32 que importa.
CHAR_COLLISION_OFFSET_X = 32
CHAR_COLLISION_OFFSET_Y = 32
CHAR_COLLISION_SIZE     = 32


def character_anchor_offset() -> "tuple[float, float]":
    """(dx, dy) a subtrair de (draw_x, draw_y) — ponto de referência do
    ECS (centro do tile lógico da entidade, em espaço de tela) — pra
    achar o canto superior esquerdo de onde blitar o frame do
    personagem. Alinha o CENTRO do tile de colisão (32×32, ver
    `CHAR_COLLISION_OFFSET_X/Y`) com (draw_x, draw_y), não o centro/
    canto do frame inteiro (96×H)."""
    return (CHAR_COLLISION_OFFSET_X + CHAR_COLLISION_SIZE / 2,
            CHAR_COLLISION_OFFSET_Y + CHAR_COLLISION_SIZE / 2)

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
    idx = int(anim_time_ms // frame_ms) % len(frames)
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


def resolve_attack_state_name(class_id: str, sid: str, direction: str) -> str:
    """"attack" (genérico) ou o próprio `sid` da skill, se ela tiver
    sprite dedicado nessa direção (direto ou espelhado). Ponto único de
    verdade pra "essa skill tem animação própria?" — auto-attack
    (`sid=""`) cai direto no genérico, sem branch especial (arquivo
    "{class_id}__{direction}.png" nunca existe, então `_frames_for`
    já retorna vazio naturalmente)."""
    if sid and _frames_for(class_id, sid, direction):
        return sid
    return "attack"


MISS_ATTACK_SLOWDOWN = 1.5  # 50% mais lento — golpe que erra (miss/dodge/parry); 25% não deu diferença perceptível, usuário pediu +25pp (10/09/2026)


def trigger_attack_anim(tile_movement, class_id: str, sid: str, now_ms: int,
                        missed: bool = False) -> None:
    """Liga a animação de golpe — chamada no momento em que o servidor
    confirma o resultado do golpe (COMBAT_RESULT, auto-attack OU skill),
    nunca em resposta a DoT/HoT (09/09/2026, pedido do usuário).
    `missed=True` (miss/dodge/parry — golpe que não causa dano, 10/09/2026,
    pedido do usuário: "sem isso o personagem fica congelado quando erra")
    reusa a MESMA animação, só `MISS_ATTACK_SLOWDOWN` mais lenta — nenhuma
    arte nova precisa existir; se um dia quiser uma animação DEDICADA de
    erro, é só criar `{class_id}_miss_{direction}.png` e resolver aqui
    (hoje sempre cai no "attack"/sid normal, só o ritmo muda).
    `resolve_display_state()` decide quando ela termina (1 ciclo completo
    do sheet resolvido aqui) e volta pra idle/run — puramente cosmético,
    nunca lido por gameplay/combate."""
    tile_movement.anim_attack_state      = resolve_attack_state_name(class_id, sid, tile_movement.facing)
    tile_movement.anim_attack_started_ms = now_ms
    # int() — frame_ms indexa frames[] via // e %; float quebraria o
    # indexing (bug real pego testando: 78*1.25=97.5, frames[2.0] explode).
    tile_movement.anim_attack_frame_ms   = int(DEFAULT_FRAME_MS * (MISS_ATTACK_SLOWDOWN if missed else 1.0))


def resolve_display_state(tile_movement, class_id: str, now_ms: int,
                          frame_ms: int = DEFAULT_FRAME_MS) -> "tuple[str, int, float]":
    """(state_name, anim_time_ms, frame_ms) prontos pra passar direto a
    `get_character_frame` — ponto único de verdade pra "qual animação
    mostrar agora" (golpe tem prioridade sobre andando/parado, enquanto
    durar 1 ciclo do sheet), "que tempo usar pra escolher o frame" (desde
    o INÍCIO do golpe — sempre começa do frame 0 — vs. tempo absoluto
    pra idle/run, que só precisam ciclar pra sempre), e "com que duração
    de frame" (`tile_movement.anim_attack_frame_ms` — golpe que errou é
    mais lento, setado por `trigger_attack_anim`, nunca o `DEFAULT_FRAME_MS`
    fixo pra golpe).

    Golpe expira sozinho (limpa `anim_attack_state`, sem componente/
    sistema novo pra isso) quando o tempo desde o trigger passa de
    `len(frames) * anim_attack_frame_ms`, OU na hora se o sprite resolvido
    não existir pra essa direção — nesses casos cai pro idle/run do MESMO
    frame, sem 1 frame de retângulo/estado errado no meio."""
    if tile_movement.anim_attack_state:
        _attack_frame_ms = tile_movement.anim_attack_frame_ms or frame_ms
        frames  = _frames_for(class_id, tile_movement.anim_attack_state, tile_movement.facing)
        elapsed = now_ms - tile_movement.anim_attack_started_ms
        if frames and elapsed < len(frames) * _attack_frame_ms:
            return tile_movement.anim_attack_state, elapsed, _attack_frame_ms
        tile_movement.anim_attack_state = ""
    return resolve_anim_state(tile_movement), now_ms, frame_ms
