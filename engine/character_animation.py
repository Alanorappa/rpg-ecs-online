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
  {class_id}_attack_{direction}.png  — golpe genérico de AUTO-ATTACK
                                        (fallback ÚLTIMO de qualquer golpe,
                                        skill incluída)
  {class_id}_skill_{direction}.png   — golpe genérico de SKILL sem
                                        sprite própria (10/09/2026,
                                        pedido do usuário — fica ENTRE o
                                        sid específico e o "attack" puro
                                        na cascata, só pra skills, nunca
                                        pra auto-attack)
  {class_id}_{skill_id}_{direction}.png — golpe ESPECÍFICO de uma skill
                                        (ex: "guerreiro_golpe_poderoso_
                                        down.png") — opcional, o mais
                                        específico da cascata
  Cascata pra golpe de SKILL (`resolve_attack_state_name`, sid não-vazio):
  1. `{class_id}_{skill_id}_{direction}.png` (própria da skill)
  2. `{class_id}_skill_{direction}.png` (genérica de skill)
  3. `{class_id}_attack_{direction}.png` (genérica de auto-attack)
  Auto-attack (sid vazio) vai direto pro nível 3 — nunca passa pelo
  genérico de skill, que não faz sentido pra ele.
  direction ∈ "down"/"up"/"left"/"right"
  Cada sheet é cortada em frames QUADRADOS (15/10/2026, revisão da
  regra de 09/09/2026 — decisão do usuário: "vamos deixar com que o
  sistema identifique a quantidade de frames fazendo a divisão pela
  altura" — mesma convenção que `engine/mob_animation.py` já usa pra
  mob): largura = altura = altura REAL do sheet, contagem de frames =
  largura_total // altura — nenhum valor fixo, nenhuma metadata extra
  por arquivo. `CHAR_TILE_SIZE` (96) vira só o FALLBACK/default dos
  parâmetros de `character_anchor_offset()` pra chamador sem Surface em
  mãos (ex: nameplate de player remoto), nunca mais usado pra cortar
  sheet nenhuma. **Exceção, state in `_FIXED_WIDTH_STATES`**
  (`portal_in`/`portal_out`): sheet INTENCIONALMENTE não-quadrada
  (96×640 — pilar de luz vertical estreito, ver `character_
  anchor_offset`) continua na regra ANTIGA (largura fixa
  `CHAR_TILE_SIZE`) — decisão explícita do usuário (15/10/2026) de não
  redesenhar essa arte pra se encaixar na regra nova. Altura pode ser
  maior que 1 tile — personagem ancorado no PÉ, mesma convenção de
  `entity_disguise.py`. Diferente de `entity_disguise.py`: aqui IDLE
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

CHAR_TILE_SIZE = 96  # só fallback/default de character_anchor_offset() — nunca mais usado pra cortar sheet
DEFAULT_FRAME_MS = 78  # 65 * 1.2 — animação 20% mais lenta (09/09/2026, pedido do usuário)

# Estados cuja sheet é INTENCIONALMENTE não-quadrada (15/10/2026, decisão
# do usuário) — continuam cortados em largura FIXA `CHAR_TILE_SIZE`, nunca
# a regra quadrada (largura=altura) que todo o resto usa. portal_in/
# portal_out: pilar de luz vertical estreito (96×640), re-desenhar pra
# quadrado destruiria o efeito visual. Nunca checar `state.startswith(
# "portal")` ou parecido — lista explícita, igual `_LOOPING_ATTACK_SKILLS`.
_FIXED_WIDTH_STATES = {"portal_in", "portal_out"}

# Tile de colisão/pé real (32×32, == TILE_SIZE) dentro do frame —
# (10/09/2026, decisão do usuário — convenção fixa de arte, mesma pra
# idle/run/attack/qualquer skill futura). Offset é a posição do CANTO
# SUPERIOR ESQUERDO desse tile dentro do frame — horizontal cai
# CENTRADO por construção (`character_anchor_offset` deriva isso direto
# de `frame_width/2`, 15/10/2026 — antes hardcoded assumindo frame
# sempre 96 de largura; agora a largura varia por sheet, já que os
# frames são quadrados), vertical NÃO (mais espaço acima do tile —
# corpo/cabeça — que abaixo). Usado tanto pra ancorar o blit
# (`character_anchor_offset`) quanto pro Y-sort (`ui/systems.py::
# RenderSystem`) — NUNCA usar a largura/altura CRUA do frame pra
# nenhum dos dois, é sempre este tile de 32×32 que importa.
CHAR_COLLISION_OFFSET_X = 32
CHAR_COLLISION_OFFSET_Y = 32
CHAR_COLLISION_SIZE     = 32

# Altura de frame das sheets "normais" (idle/run/attack/skill sem
# tratamento especial) — 80px, ex: guerreiro_idle_down.png. Serve só
# de REFERÊNCIA pra derivar a margem abaixo; uma sheet com altura
# DIFERENTE (ex: portal_in/portal_out, 640px) não precisa deste valor
# em si, só da margem (constante, qualquer altura).
CHAR_BASELINE_FRAME_H = 80

# Distância entre a BASE do tile de colisão (y=64 numa sheet de 80px,
# CHAR_COLLISION_OFFSET_Y + CHAR_COLLISION_SIZE) e a base do FRAME —
# 16px, derivada (não um número livre). Esta margem é o dado estável
# entre sheets de alturas diferentes; CHAR_COLLISION_OFFSET_Y sozinho
# (medido a partir do TOPO) só vale pra sheets de altura BASELINE.
CHAR_FRAME_BOTTOM_MARGIN_PX = CHAR_BASELINE_FRAME_H - CHAR_COLLISION_OFFSET_Y - CHAR_COLLISION_SIZE


def character_anchor_offset(frame_height: int = CHAR_BASELINE_FRAME_H,
                            frame_width: int = CHAR_TILE_SIZE) -> "tuple[float, float]":
    """(dx, dy) a subtrair de (draw_x, draw_y) — ponto de referência do
    ECS (centro do tile lógico da entidade, em espaço de tela) — pra
    achar o canto superior esquerdo de onde blitar o frame do
    personagem. Alinha o CENTRO do tile de colisão (32×32, ver
    `CHAR_COLLISION_OFFSET_X/Y`) com (draw_x, draw_y), não o centro/
    canto do frame inteiro.

    Ancorado pela BASE do frame, não mais um offset fixo a partir do
    topo (02/10/2026, pedido do usuário — bug real: sprite de portal
    foi redesenhada mais alta, 80px->640px, pensada pra crescer PRA
    CIMA mantendo os pés no mesmo lugar; com o offset fixo antigo —
    sempre medido do topo — a sheet mais alta "crescia pra baixo",
    enterrando os pés). `dy` é derivado da altura REAL do frame:
    `frame_height - CHAR_FRAME_BOTTOM_MARGIN_PX - CHAR_COLLISION_SIZE/2`
    -- numa sheet BASELINE (80px) isso dá exatamente 48.

    `dx` (15/10/2026, frames viraram QUADRADOS — largura varia por
    sheet, não é mais sempre 96) — derivado DIRETO de `frame_width/2`
    (o tile de colisão é centralizado horizontalmente por construção,
    `CHAR_COLLISION_OFFSET_X` não entra mais nessa conta — antes dava
    na mesma só porque o frame era sempre 96 de largura: 32+16==96/2).
    Default de ambos os parâmetros preserva o valor de sempre (48, 48)
    pra quem chama sem Surface em mãos (ex: nameplate de player remoto
    em `client/remote_entity_handlers.py`, que usa o default DE
    PROPÓSITO — fixo, não deve "balançar" seguindo a sheet de cada
    animação). Chamador que JÁ tem o Surface do frame em mãos
    (RenderSystem, luz de personagem do portal) deve passar
    `frame.get_height()`/`frame.get_width()`."""
    dy = frame_height - CHAR_FRAME_BOTTOM_MARGIN_PX - CHAR_COLLISION_SIZE / 2
    return (frame_width / 2, dy)

# Cache de módulo, chaveado por (class_id, state, direction) — conjunto
# BEM pequeno e fixo (3 classes × 2 estados × 4 direções = 24 no
# máximo), nunca por-entidade — sem risco de crescer sem teto (ver
# CLAUDE.md "proibido estado mutável ad-hoc fora do ECS").
_frame_cache: dict[tuple, list] = {}


def _load_sheet_frames(class_id: str, state: str, direction: str) -> list:
    """Carrega e corta `{class_id}_{state}_{direction}.png` em frames
    QUADRADOS (largura = altura REAL do sheet, 15/10/2026) — EXCETO
    `state in _FIXED_WIDTH_STATES` (portal_in/portal_out), que continua
    cortado em largura FIXA `CHAR_TILE_SIZE` (sheet intencionalmente
    não-quadrada). Lista vazia se o arquivo não existir.

    Fallback pra arquivo SEM direção (01/10/2026, pedido do usuário —
    `{class_id}_{state}.png`, ex: `guerreiro_portal_in.png`): alguns
    estados não variam por direção de verdade (um pilar de luz vertical
    engolindo/materializando o personagem é visualmente igual de
    qualquer lado) — tentar o arquivo direcional primeiro preserva o
    comportamento de QUALQUER estado que já tenha variantes por direção
    (idle/run/attack/skill), e só cai pro genérico se o direcional não
    existir."""
    import pygame
    import os
    from paths import resource_path

    fname = f"{class_id}_{state}_{direction}.png"
    path  = resource_path(os.path.join("assets", "sprites", fname))
    if not os.path.exists(path):
        fname_nodir = f"{class_id}_{state}.png"
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
    frame_w = CHAR_TILE_SIZE if state in _FIXED_WIDTH_STATES else frame_h
    if frame_w <= 0:
        return []
    frames = [
        sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, frame_h)).copy()
        for i in range(sheet.get_width() // frame_w)
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


def _attack_loop_still_active(tile_movement, now_ms: int) -> bool:
    """True se um golpe em LOOP (`_LOOPING_ATTACK_SKILLS`) já ligado em
    `tile_movement` ainda está dentro da própria duração — usado por
    `trigger_attack_anim` pra decidir se um golpe NOVO concorrente pode
    sobrescrever ou não."""
    if tile_movement.anim_attack_loop_duration_ms <= 0:
        return False
    return (now_ms - tile_movement.anim_attack_started_ms) < tile_movement.anim_attack_loop_duration_ms


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
    """Cascata de 3 níveis pra golpe de SKILL (10/09/2026, pedido do
    usuário): sid específico → "skill" genérico → "attack" genérico.
    Ponto único de verdade pra "que sprite essa skill usa?" — auto-attack
    (`sid=""`) pula direto pro nível 3, nunca passa pelo "skill" genérico
    (arquivo "{class_id}__{direction}.png" nunca existe, então
    `_frames_for` já retorna vazio naturalmente pro nível 1 quando
    `sid=""`, e o `if sid` abaixo já pula o nível 2 de propósito)."""
    if sid and _frames_for(class_id, sid, direction):
        return sid
    if sid and _frames_for(class_id, "skill", direction):
        return "skill"
    return "attack"


def resolve_cast_state_name(class_id: str, sid: str, direction: str) -> "str | None":
    """Cascata de 2 níveis pro WINDUP de cast (15/10/2026, pedido do
    usuário — "uma animação pro cast, outra pro lançamento"): sid
    específico (`f"{sid}_cast"`, ex: "bola_de_fogo_cast") → "cast"
    genérico (qualquer skill com cast_time, sem arte própria) → None
    (sem arte de cast pra essa combinação ainda — personagem fica em
    idle/run durante o cast inteiro, zero regressão). Convenção de
    arquivo: `{class_id}_{sid}_cast_{direction}.png` /
    `{class_id}_cast_{direction}.png`."""
    if sid and _frames_for(class_id, f"{sid}_cast", direction):
        return f"{sid}_cast"
    if _frames_for(class_id, "cast", direction):
        return "cast"
    return None


def trigger_cast_anim(tile_movement, class_id: str, sid: str, now_ms: int,
                      hold_frame_index: int = 5) -> None:
    """Liga a animação de CAST (windup) — chamada no evento de rede
    `SKILL_EFFECT{event:"cast_start"}` (já existia só pra tocar som —
    ver `client/network_handlers.py::_handle_msg_skill_effect`), tanto
    pro conjurador local quanto pra qualquer observador (mesmo evento,
    já broadcast AOI).

    Em 2 FASES (15/10/2026, revisão — pedido do usuário: "pode iniciar
    a animação no início do cast, congelar no 5º frame da animação até
    sair o projétil então finaliza a animação"; substitui a técnica de
    ESTICAR o sheet inteiro, usada antes no mesmo dia): toca normal até
    `hold_frame_index` e CONGELA ali (`trigger_anticipation_anim`), até
    o evento de LANÇAMENTO (`SKILL_EFFECT{event:"launch"|"impact"|
    "miss"}`) chamar `release_anticipation_anim()` — ver `client/
    network_handlers.py::_handle_msg_skill_effect`. Sem arte de cast OU
    sem `cast_time` de verdade (skill instantânea) → não faz nada,
    personagem continua em idle/run normalmente."""
    state_name = resolve_cast_state_name(class_id, sid, tile_movement.facing)
    if state_name is None:
        return
    from content.skill_config import SKILL_CATALOG
    cast_s = SKILL_CATALOG.get(sid, {}).get("cast_time", 0.0) if sid else 0.0
    if cast_s <= 0:
        return
    trigger_anticipation_anim(tile_movement, class_id, state_name, now_ms,
                              hold_frame_index=hold_frame_index, sid=sid)


CHAR_DEATH_STATE = "death"


def trigger_death_anim(tile_movement, class_id: str, now_ms: int) -> None:
    """Liga a animação de MORTE do personagem (15/10/2026, pedido do
    usuário — "a animação precisa ser feita assim que o player recebe o
    hit que o mata, e o último frame deve ficar congelado até o
    personagem libertar espírito ou spawnar novamente") — mesma técnica
    já usada por `engine.mob_animation.trigger_mob_death_anim`: toca 1
    ciclo do sheet e TRAVA no último frame (`anim_attack_hold_last_
    frame=True`), nunca expira sozinha. Convenção de arquivo:
    `{class_id}_death_{direction}.png` (sem cascata de sid — morte não é
    específica de skill). Chamado nos mesmos 2 eventos de rede que já
    confirmam a morte, pro caster local E pra observadores (nunca um
    canal novo): `PLAYER_DEATH` (local, `client/network_handlers.py::
    _handle_msg_player_death`) e `ENTITY_DEATH` (remoto, `_handle_msg_
    entity_death`). Quem chama é responsável por LIMPAR `anim_attack_
    state`/`anim_attack_hold_last_frame` explicitamente no revive
    (`_handle_msg_player_revive` local; transição de HP 0→positivo
    remota) — nunca expira sozinha, mesmo padrão de golpe com hold
    (portal/cast). Sem arte de morte pra essa classe/direção → não faz
    nada, `ui/render_system.py` cai no retângulo cinza de sempre."""
    if not _frames_for(class_id, CHAR_DEATH_STATE, tile_movement.facing):
        return
    tile_movement.anim_attack_state            = CHAR_DEATH_STATE
    tile_movement.anim_attack_sid              = ""
    tile_movement.anim_attack_started_ms       = now_ms
    tile_movement.anim_attack_frame_ms         = DEFAULT_FRAME_MS
    tile_movement.anim_attack_loop_duration_ms = 0
    tile_movement.anim_attack_hold_last_frame  = True


def clear_attack_anim_state(tile_movement) -> None:
    """Limpa `anim_attack_state`/`anim_attack_hold_last_frame` na mão —
    pro revive (15/10/2026): estados com `hold_last_frame=True` (morte,
    cast, portal) NUNCA expiram sozinhos, então sem isso o personagem
    revivido ficaria preso pra sempre na última pose de ANTES de
    morrer. Seguro chamar mesmo sem nenhum estado ativo (`anim_attack_
    state` já vazio vira no-op efetivo)."""
    tile_movement.anim_attack_state           = ""
    tile_movement.anim_attack_hold_last_frame = False


MISS_ATTACK_SLOWDOWN = 1.5  # 50% mais lento — golpe que erra (miss/dodge/parry); 25% não deu diferença perceptível, usuário pediu +25pp (10/09/2026)

# Skills cujo golpe deve ficar em LOOP durante toda a duração REAL da
# skill (efeito contínuo — giro, canalização —, não um golpe único),
# em vez do padrão "1 ciclo do sheet só" (10/09/2026, pedido do
# usuário: "fatiador de corpos dura 5s, a animação deve ficar em loop
# durante esse tempo"). Duração lida de SKILL_CATALOG (ponto único de
# verdade, nunca duplicada aqui) — se um dia o design mudar a duração
# da skill, a animação acompanha sozinha.
_LOOPING_ATTACK_SKILLS = {"fatiador_de_corpos"}


def _looping_attack_duration_ms(sid: str) -> "int | None":
    """Duração (ms) pra tocar o golpe de `sid` em LOOP, se ele estiver
    em `_LOOPING_ATTACK_SKILLS` — lida de
    `SKILL_CATALOG[sid]["params"]["duration"]`. None = golpe normal (1
    ciclo do sheet só), a esmagadora maioria das skills."""
    if sid not in _LOOPING_ATTACK_SKILLS:
        return None
    from content.skill_config import SKILL_CATALOG
    duration_s = SKILL_CATALOG.get(sid, {}).get("params", {}).get("duration")
    return int(duration_s * 1000) if duration_s else None


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
    (hoje sempre cai no "attack"/sid normal, só o ritmo muda). Skills em
    `_LOOPING_ATTACK_SKILLS` (10/09/2026) ficam em loop pela duração REAL
    da skill em vez de 1 ciclo só — ver `_looping_attack_duration_ms`.
    `resolve_display_state()` decide quando ela termina e volta pra
    idle/run — puramente cosmético, nunca lido por gameplay/combate.

    Golpe em LOOP ativo tem prioridade sobre qualquer golpe concorrente
    (auto-attack, ou skill sem loop) enquanto durar — sem essa guarda, um
    auto-attack que acerta um mob DURANTE a Fatiador de Corpos sobrescrevia
    o estado sem checar nada, cancelando a animação bem antes do fim real
    da skill (bug real relatado 10/09/2026: "a animação do tornado é
    cancelada quando o personagem se move" — causa raiz de verdade era o
    auto-attack concorrente que o movimento provoca ao entrar em alcance
    de um mob, não o movimento em si). Um golpe em loop NOVO (uma skill de
    loop DIFERENTE da que já está tocando) substitui um loop ainda ativo.

    Um TICK do MESMO loop já ativo (mesmo `sid` — ex: Fatiador de Corpos
    manda 1 resultado de combate por tick de dano, ~1/s, durante os 5s
    inteiros) é IGNORADO (02/10/2026, bug real relatado pelo usuário:
    "a skill dura 5 segundos mas a animação fica um bom tempo depois")
    — sem este guard, CADA tick reiniciava `anim_attack_started_ms`
    (e o frame, de volta pro 0), empurrando o fim VISUAL do loop pra
    bem depois do fim de verdade da skill (e fazendo a animação
    "piscar" a cada tick). Recast de verdade da MESMA skill, ainda
    dentro da janela antiga, re-arma do zero — mas isso só seria
    alcançável se o cooldown da skill fosse menor que sua própria
    duração, o que não acontece com nenhuma skill de loop hoje
    (Fatiador: 45s de cooldown, 5s de duração)."""
    _new_loop_ms = _looping_attack_duration_ms(sid) or 0
    # Cast (`trigger_cast_anim`) nunca usa o mecanismo de LOOP
    # (`anim_attack_loop_duration_ms` sempre 0 — é ESTICADO, não
    # repetido) — `_attack_loop_still_active()` já devolve False
    # sozinho nesse caso, então o lançamento sempre interrompe o cast
    # livremente, sem precisar de nenhuma exceção aqui. Esta guarda só
    # protege golpe em loop DE VERDADE (ex: Fatiador de Corpos).
    if _attack_loop_still_active(tile_movement, now_ms):
        if _new_loop_ms == 0:
            return
        if sid and sid == tile_movement.anim_attack_sid:
            return
    tile_movement.anim_attack_state      = resolve_attack_state_name(class_id, sid, tile_movement.facing)
    tile_movement.anim_attack_sid        = sid
    tile_movement.anim_attack_started_ms = now_ms
    # int() — frame_ms indexa frames[] via // e %; float quebraria o
    # indexing (bug real pego testando: 78*1.25=97.5, frames[2.0] explode).
    tile_movement.anim_attack_frame_ms   = int(DEFAULT_FRAME_MS * (MISS_ATTACK_SLOWDOWN if missed else 1.0))
    tile_movement.anim_attack_loop_duration_ms = _new_loop_ms
    # Sempre False aqui — "segurar na última frame" é exclusivo de quem
    # seta isso NA MÃO fora deste caminho (ex: portal "in", ver
    # client/portal_handlers.py). Sem este reset, um golpe NORMAL
    # disparado logo depois de um golpe com hold ativo herdaria a flag
    # (campo do COMPONENTE, não limpo automaticamente) e ficaria preso
    # na última frame em vez de voltar pro idle sozinho.
    tile_movement.anim_attack_hold_last_frame = False
    # Idem — reseta qualquer teto de anticipação deixado por um golpe/cast
    # anterior (ver `trigger_anticipation_anim`), senão um golpe NOVO
    # disparado logo depois herdaria o teto por engano e nunca tocaria
    # além do frame congelado antigo.
    tile_movement.anim_attack_frame_cap_ms = -1


def trigger_anticipation_anim(tile_movement, class_id: str, state_name: str,
                              now_ms: int, hold_frame_index: int = 5,
                              frame_ms: int = DEFAULT_FRAME_MS, sid: str = "") -> bool:
    """Liga a FASE 1 (preparação) de um golpe/cast em 2 tempos
    (15/10/2026, pedido do usuário — sincronizar auto-attack/cast do
    arqueiro: "a animação deve acontecer antes de sair o projétil...
    congelar no Nº frame... até sair o projétil então finaliza"): toca
    `state_name` normalmente até `hold_frame_index` (inclusive) e
    CONGELA ali — `anim_attack_frame_cap_ms = hold_frame_index *
    frame_ms` trava o `elapsed` nesse teto (ver `resolve_display_
    state`), nunca avança sozinho daí em diante. `hold_frame_index` é
    parâmetro (não uma constante fixa pro jogo inteiro) — cada chamador
    decide o valor certo pra PRÓPRIA animação, sheets com contagem de
    frames diferente podem usar valores diferentes.

    Devolve True se ligou de verdade (havia arte pra `state_name`),
    False se não (sem arte = no-op, chamador decide o fallback — ex:
    não tocar nock/draw nenhum, auto-attack cai direto pro golpe normal
    no COMBAT_RESULT como sempre). Quem chama é responsável por chamar
    `release_anticipation_anim()` quando o evento real (projétil sai)
    chegar — nunca expira sozinho (mesmo padrão de `hold_last_frame`)."""
    if not _frames_for(class_id, state_name, tile_movement.facing):
        return False
    tile_movement.anim_attack_state            = state_name
    tile_movement.anim_attack_sid              = sid
    tile_movement.anim_attack_started_ms       = now_ms
    tile_movement.anim_attack_frame_ms         = frame_ms
    tile_movement.anim_attack_loop_duration_ms = 0
    tile_movement.anim_attack_hold_last_frame  = True
    tile_movement.anim_attack_frame_cap_ms     = max(0, hold_frame_index) * frame_ms
    return True


def release_anticipation_anim(tile_movement, now_ms: int) -> bool:
    """Libera a FASE 2 (conclusão) de um golpe/cast travado por
    `trigger_anticipation_anim` — continua EXATAMENTE de onde a
    animação pausou (nunca reinicia do frame 0): rebate `anim_attack_
    started_ms` pra trás pelo valor do teto antigo, removendo o teto
    (`anim_attack_frame_cap_ms = -1`) — o cálculo normal de `elapsed`
    em `resolve_display_state` passa a avançar sozinho a partir do
    frame congelado até o fim do sheet, depois volta pro idle/run
    (hold_last_frame=False — "então finaliza a animação", não trava no
    frame final).

    Devolve True se havia de fato uma anticipação ativa pra liberar;
    False se não (`anim_attack_frame_cap_ms < 0` — ex: player remoto,
    que nunca teve fase 1, ou primeiro golpe antes da fase 1 chegar a
    disparar) — chamador deve cair pro `trigger_attack_anim()` normal
    nesse caso, nunca assumir que sempre há algo pra liberar."""
    if tile_movement.anim_attack_frame_cap_ms < 0:
        return False
    tile_movement.anim_attack_started_ms      = now_ms - tile_movement.anim_attack_frame_cap_ms
    tile_movement.anim_attack_frame_cap_ms    = -1
    tile_movement.anim_attack_hold_last_frame = False
    return True


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
    sistema novo pra isso) quando o tempo desde o trigger passa da
    duração — `anim_attack_loop_duration_ms` pras skills em
    `_LOOPING_ATTACK_SKILLS` (10/09/2026, fica em LOOP por esse tempo
    todo, o `get_character_frame` já cicla os frames via módulo sozinho,
    nenhuma lógica extra aqui pra isso), ou `len(frames) *
    anim_attack_frame_ms` (1 ciclo só) pra qualquer outro golpe — OU na
    hora se o sprite resolvido não existir pra essa direção — nesses
    casos cai pro idle/run do MESMO frame, sem 1 frame de retângulo/
    estado errado no meio.

    EXCEÇÃO: `anim_attack_hold_last_frame=True` (02/10/2026, pedido do
    usuário — portal "in") segura na ÚLTIMA frame em vez de cair pro
    idle/run quando a duração expira — `anim_attack_state` continua o
    mesmo, nunca reseta sozinho. Usado por golpes cuja duração
    "esticada" é só uma ESTIMATIVA (ex: cast de rede, onde a conclusão
    de verdade depende de latência imprevisível) — sem isso, o
    personagem "pisca" de volta pro idle por 1+ frame bem perceptível
    entre a animação acabar e o evento real (ZONE_CHANGE) chegar.
    Quem usa isso é responsável por LIMPAR `anim_attack_state`/
    `anim_attack_hold_last_frame` explicitamente quando o evento real
    chegar (ex: `_trigger_portal_out_if_pending`/`_cancel_portal_cast_local`)
    — nunca expira sozinho.

    `anim_attack_frame_cap_ms` (15/10/2026, ver `trigger_anticipation_
    anim`/`release_anticipation_anim`) — quando >=0, trava `elapsed`
    nesse teto ANTES de qualquer outro cálculo: pausa a animação num
    frame ESPECÍFICO do meio do sheet (não necessariamente o último),
    por construção, sem precisar de `hold_last_frame` nem de recalcular
    duração nenhuma."""
    if tile_movement.anim_attack_state:
        _attack_frame_ms = tile_movement.anim_attack_frame_ms or frame_ms
        frames  = _frames_for(class_id, tile_movement.anim_attack_state, tile_movement.facing)
        elapsed = now_ms - tile_movement.anim_attack_started_ms
        if tile_movement.anim_attack_frame_cap_ms >= 0:
            elapsed = min(elapsed, tile_movement.anim_attack_frame_cap_ms)
        _duration = tile_movement.anim_attack_loop_duration_ms or (len(frames) * _attack_frame_ms if frames else 0)
        if frames and elapsed < _duration:
            return tile_movement.anim_attack_state, elapsed, _attack_frame_ms
        if frames and tile_movement.anim_attack_hold_last_frame:
            held_elapsed = (len(frames) - 1) * _attack_frame_ms
            return tile_movement.anim_attack_state, held_elapsed, _attack_frame_ms
        tile_movement.anim_attack_state = ""
    return resolve_anim_state(tile_movement), now_ms, frame_ms
