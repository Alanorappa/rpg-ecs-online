"""
utils.py — Funções utilitárias compartilhadas entre sistemas.

Regra: apenas funções puras sem dependências de estado global.
Sistemas importam daqui; nunca o contrário.
"""
from __future__ import annotations
import math


def bresenham_ray(dx: int, dy: int, steps: int) -> list[tuple[int, int]]:
    """Gera os próximos `steps` passos de 1 tile que melhor aproximam o
    ângulo real do vetor (dx, dy) — em vez de colapsar pra um dos 8 eixos
    via sinal puro (round(dx/dist) perde toda angulação intermediária,
    ex.: dx=1,dy=3 tem ângulo ~72°, mas sinal colapsa pra 45°).

    Mesmo algoritmo Bresenham usado em is_tile_walkable/_dash_path_clear
    para segmentos entre dois pontos fixos — aqui como raio contínuo a
    partir da origem, usado por empurrões (knockback) que continuam na
    direção real do disparo além da posição do alvo.

    Cada item retornado é (step_x, step_y) com cada componente em {-1,0,1}.
    """
    if dx == 0 and dy == 0:
        dx = 1
    adx, ady = abs(dx), abs(dy)
    sx = 1 if dx > 0 else -1 if dx < 0 else 0
    sy = 1 if dy > 0 else -1 if dy < 0 else 0
    err = adx - ady
    result: list[tuple[int, int]] = []
    for _ in range(steps):
        e2 = 2 * err
        step_x = step_y = 0
        if e2 > -ady:
            err -= ady
            step_x = sx
        if e2 < adx:
            err += adx
            step_y = sy
        result.append((step_x, step_y))
    return result


def bresenham_line_tiles(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Segmento Bresenham entre 2 pontos fixos — retorna os tiles do
    PRIMEIRO PASSO após (x0,y0) até (x1,y1) INCLUSIVE (início excluído,
    fim incluído).

    Semântica de endpoint DIFERENTE de `_has_los`/`_dash_path_clear`
    (`engine/skill_handlers.py`) — aquelas excluem os 2 extremos (servem
    pra "tiro limpo": nem a origem nem o alvo bloqueiam o próprio tiro).
    Esta função inclui o tile FINAL de propósito (13/08/2026,
    `server/world_server.py::_has_tile_los`) — um alvo parado EM CIMA de
    um tile que bloqueia visão (ex: base de bush) precisa contar como
    escondido, não só obstáculos "no meio do caminho".
    """
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    cx, cy = x0, y0
    tiles: list[tuple[int, int]] = []
    while not (cx == x1 and cy == y1):
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy
        tiles.append((cx, cy))
    return tiles


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """Distância de Chebyshev (máximo entre delta-x e delta-y).

    Usada para distâncias em grade de 8 direções (tiles) — MOVIMENTO/
    pathing/leash/adjacência/spawn (diagonal custa o mesmo passo que
    reto, sem penalidade de √2). NUNCA usar pra decidir alcance de
    ATAQUE (skill/auto-attack/torre/minion/mob) — ver `in_range()`
    abaixo, ponto único pra isso (14/09/2026, ver
    PROBLEMAS_ARQUITETURA.md §135).
    chebyshev(1,1, 3,2) == 2  (diagonal conta como 1)
    """
    return max(abs(ax - bx), abs(ay - by))


def in_range(ax: int, ay: int, bx: int, by: int, max_range: float) -> bool:
    """Alcance de ATAQUE de alvo único (skill/auto-attack/torre/minion/
    mob) — distância REAL (Euclidiana) em tile, círculo de verdade, NUNCA
    Chebyshev (que conta diagonal igual a reto e faz o alcance "esticar"
    ~41% na diagonal pro mesmo número de tiles — bug real relatado pelo
    usuário, 14/09/2026, ver PROBLEMAS_ARQUITETURA.md §135). Ponto único
    de verdade pra esse tipo de decisão — qualquer checagem nova de
    "alvo X está a alcance de ataque Y" usa isto, nunca `chebyshev()`
    nem uma comparação de distância ao quadrado feita na mão.

    Compara distância AO QUADRADO (sem sqrt) — mesma decisão, mais
    barato. `max_range` em tiles (pode ser float, ex: alcance derivado
    de item/talento).
    """
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy <= max_range * max_range


_VERTICAL_FACING_HALF_ANGLE_DEG = 20.0
# Ângulo (em cada lado do eixo vertical) dentro do qual a direção ainda
# conta como "down"/"up" — pedido do usuário (10/09/2026): reto pra
# baixo/cima é 0°; até 20° pra qualquer lado disso continua down/up;
# passando de 20° (os 70° restantes de cada quadrante), vira left/right.
# NÃO é mais um corte 45/45 simétrico — a faixa de down/up é bem mais
# estreita que a de left/right, de propósito.


def facing_from_delta(dx: int, dy: int) -> str:
    """Direção cardinal (4 vias) pra animação de personagem, a partir de
    um delta (de tile de um passo — target-current — ou de pixel, pra
    "olhar pro alvo" parado). Ângulo medido a partir do eixo VERTICAL
    (`math.degrees(math.atan2(abs(dx), abs(dy)))`, 0°=reto pra
    baixo/cima, 90°=reto pro lado) contra
    `_VERTICAL_FACING_HALF_ANGLE_DEG` — dentro do cone estreito ao redor
    do vertical, vence down/up; fora dele, left/right (10/09/2026,
    pedido do usuário — antes era um corte simétrico 45/45 via
    `abs(dy)>=abs(dx)`). dx==dy==0 cai no default "down" (não
    deveria ser chamado nesse caso — só faz sentido durante um passo
    real, `TileMovement.is_moving`)."""
    if dy == 0 and dx == 0:
        return "down"
    angle_from_vertical = math.degrees(math.atan2(abs(dx), abs(dy)))
    if angle_from_vertical <= _VERTICAL_FACING_HALF_ANGLE_DEG:
        return "down" if dy > 0 else "up"
    return "right" if dx > 0 else "left"


def in_aoi(cx: int, cy: int, ex: int, ey: int, radius: int) -> bool:
    """Retorna True se (ex, ey) está dentro do AOI centrado em (cx, cy).

    Métrica canônica: Chebyshev (quadrado de tiles) — mais barata que Euclidiana
    e consistente com a grade de 8 direções usada em todo o projeto.
    in_aoi(5, 5, 8, 7, 3) == True  (dx=3, dy=2, max=3 <= 3)
    """
    return chebyshev(cx, cy, ex, ey) <= radius


class SpatialHash:
    """Grade de células para lookup O(candidatos_no_AOI) em vez de O(total_entidades).

    cell_size deve ser >= AOI_RADIUS: garante que verificar células ±1 em cada
    dimensão cobre todos os candidatos dentro do raio (3×3 = 9 células).
    Se cell_size < AOI_RADIUS, o span é aumentado automaticamente.
    """

    def __init__(self, cell_size: int):
        self._cs = cell_size
        self._cells: dict[tuple[int, int], set[int]] = {}
        # span = número de células a verificar em cada direção além da célula central
        # ceil(AOI_RADIUS / cell_size) via divisão inteira: -(-a // b)
        self._span = 1  # atualizado em nearby com o radius recebido

    def clear(self) -> None:
        self._cells.clear()

    def insert(self, eid: int, tx: int, ty: int) -> None:
        key = tx // self._cs, ty // self._cs
        bucket = self._cells.get(key)
        if bucket is None:
            self._cells[key] = {eid}
        else:
            bucket.add(eid)

    def nearby(self, tx: int, ty: int, radius: int) -> set[int]:
        """Candidatos em células que podem conter entidades dentro de `radius` tiles.

        Retorna um superconjunto — chamador ainda deve filtrar pela distância exata.
        """
        span = -(-radius // self._cs)   # ceil(radius / cell_size)
        cx, cy = tx // self._cs, ty // self._cs
        result: set[int] = set()
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                bucket = self._cells.get((cx + dx, cy + dy))
                if bucket:
                    result.update(bucket)
        return result


def start_tile_movement(position, tile_movement, tgt_x: int, tgt_y: int,
                        extra_speed_mult: float = 1.0,
                        override_duration: "float | None" = None) -> None:
    """Inicia um movimento tile-a-tile para (tgt_x, tgt_y).

    Compartilhado entre PlayerInputSystem e EnemyAISystem para evitar
    duplicação da lógica de cálculo de duração/pixels.

    Args:
        position:         componente Position da entidade.
        tile_movement:    componente TileMovement da entidade.
        tgt_x, tgt_y:    tile destino.
        extra_speed_mult: multiplicador adicional de velocidade (ex: 2.0
                          para o dash de kiting do Hunter).
        override_duration: se fornecido, usa esse valor diretamente como
                          move_duration em vez de calcular por distância/speed.
                          Necessário para deslocamentos de múltiplos tiles em
                          UM passo só (ex: knockback) — a fórmula padrão abaixo
                          assume sempre 1 tile de distância (ou diagonal),
                          então um salto de 5 tiles animaria na duração de 1,
                          parecendo "rápido demais". O chamador (servidor)
                          calcula a duração certa pra distância real e ambos
                          os lados usam o MESMO valor — fonte única de verdade.
    """
    from engine.tileset import TILE_SIZE

    tile_movement.is_moving     = True
    tile_movement.progress      = 0.0
    tile_movement.start_pixel_x = position.x
    position.prev_x             = position.x
    tile_movement.start_pixel_y = position.y
    position.prev_y             = position.y
    tile_movement.target_tile_x = tgt_x
    tile_movement.target_tile_y = tgt_y
    tile_movement.target_pixel_x = tgt_x * TILE_SIZE + TILE_SIZE / 2
    tile_movement.target_pixel_y = tgt_y * TILE_SIZE + TILE_SIZE / 2

    if override_duration is not None:
        tile_movement.move_duration = override_duration
    elif tile_movement.speed > 0:
        is_diag = (tgt_x != tile_movement.current_tile_x and
                   tgt_y != tile_movement.current_tile_y)
        dist = TILE_SIZE * (math.sqrt(2) if is_diag else 1.0)
        effective_speed = tile_movement.speed * tile_movement.slow_mult * extra_speed_mult
        if effective_speed <= 0:
            effective_speed = tile_movement.speed  # fallback: ignora multiplicadores zerados
        tile_movement.move_duration = dist / effective_speed


def snap_to_tile(world, entity_id: int, tx: int, ty: int,
                 carry_prev: bool = True) -> bool:
    """Teleporta/snap uma entidade para (tx, ty) — ÚNICA forma correta de
    escrever current_tile_x/y diretamente (knockback, teleporte, respawn,
    troca de mapa). Retorna False se a entidade não tem TileMovement.

    Faz TODO o conjunto de escritas que um snap seguro exige:
    - current_tile + target_tile = destino
    - is_moving = False + progress = 0: cancela qualquer tween em andamento.
      Sem isso, um passo de movimento interrompido "sobrevive" ao snap e o
      TileMovementSystem sobrescreve a posição no tick seguinte com
      start/target_pixel ANTIGOS (bug real: Tiro Repulsivo causava "sprint"
      visual pós-knockback em mob mid-chase; mesmo risco existia no
      Interceptar — ver PROBLEMAS_ARQUITETURA.md, rodada 21-22/06).
    - start/target_pixel = centro do tile destino: sem isso, checks de
      pixel-range de skills leem valores stale da última animação (bug real:
      golpe_poderoso "Fora de alcance" adjacente ao alvo).
    - Position (se existir) = centro do tile destino. carry_prev=True (padrão)
      preserva prev_* = posição antiga; carry_prev=False zera prev_* no destino
      (troca de mapa: interpolar da coordenada do mapa antigo não faz sentido).

    NÃO usar para os "rewinds" temporários de lag-compensation (salvar tile,
    testar, restaurar) — aqueles manipulam current_tile de propósito sem
    cancelar movimento; este helper é para mudanças REAIS de posição.
    """
    from engine.components import TileMovement, Position
    from shared.constants import TILE_SIZE
    tm = world.get_component(entity_id, TileMovement)
    if tm is None:
        return False
    tm.current_tile_x = tm.target_tile_x = tx
    tm.current_tile_y = tm.target_tile_y = ty
    tm.is_moving = False
    tm.progress  = 0.0
    # Baseline anti-cheat (WorldServer.move_player()): todo deslocamento
    # forçado real (knockback/teleporte/respawn) precisa resincronizar aqui,
    # senão o PRIMEIRO MOVE normal depois dele compara contra uma posição
    # antiga e parece um salto implausível pra velocidade de caminhada.
    import time as _time_snap
    tm._last_valid_tile_x = tx
    tm._last_valid_tile_y = ty
    tm._last_valid_ts     = _time_snap.time()
    cx = tx * TILE_SIZE + TILE_SIZE // 2
    cy = ty * TILE_SIZE + TILE_SIZE // 2
    tm.start_pixel_x = tm.target_pixel_x = cx
    tm.start_pixel_y = tm.target_pixel_y = cy
    pos = world.get_component(entity_id, Position)
    if pos is not None:
        if carry_prev:
            pos.prev_x, pos.prev_y = pos.x, pos.y
        else:
            pos.prev_x, pos.prev_y = float(cx), float(cy)
        pos.x, pos.y = float(cx), float(cy)
    return True


def is_target_alive(world, target_id: int) -> bool:
    """Único ponto de verdade pra "esse alvo ainda está vivo?" no cliente.

    target_id pode ser: mob/player LOCAL (tem CombatStats), player remoto em
    PvP (só tem RemoteControlled — sem CombatStats) ou mob remoto (só tem
    RemoteEntityMeta). Checar current_hp de CombatStats sozinho NUNCA detecta
    a morte de um alvo remoto, pois esse componente não existe nele — esse foi
    o bug do auto-attack do arqueiro contra um guerreiro remoto morto (nock
    tocando pra sempre). Qualquer código novo (skill, classe, sistema) que
    precise validar um alvo deve chamar esta função em vez de reimplementar
    o check à mão.
    """
    from engine.components import CombatStats, RemoteControlled, RemoteEntityMeta
    cs = world.get_component(target_id, CombatStats)
    if cs is not None:
        return cs.current_hp > 0
    rc = world.get_component(target_id, RemoteControlled)
    if rc is not None:
        return rc.hp > 0
    meta = world.get_component(target_id, RemoteEntityMeta)
    return meta is not None and meta.hp > 0


def get_camera_offset(world, screen_w: float, screen_h: float) -> tuple:
    """Único ponto de verdade pra "canto superior-esquerdo do mundo
    visível em tela" — 15/09/2026, revisão de performance (ver
    arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 6). Antes disso, o
    mesmo loop (`for _, _, cam_pos in world.get_entities_with(Camera,
    Position): return cam_pos.x - sw/2, cam_pos.y - sh/2`) estava
    reimplementado em 13+ pontos — inclusive DUAS definições idênticas
    de `_get_camera_offset()` no mesmo arquivo (`ui/systems.py`,
    `MouseTargetingSystem` e `LootSystem`, copy-paste em vez de função
    de módulo). Sem câmera no mundo (ex: cena de login/tela de
    transição): (0.0, 0.0), mesmo fallback que todas as cópias já
    usavam.

    Clamp de borda de mapa (20/09/2026, pedido do usuário — perto do
    canto, a câmera parava de mostrar mapa e passava a mostrar vazio,
    mantendo o personagem sempre centralizado mesmo sem ter mais mundo
    pra mostrar naquela direção). Limita o offset entre 0 e
    `dimensão_do_mapa_em_px - dimensão_da_tela`, nos 2 eixos — vale
    tanto pra câmera travada no personagem quanto pra câmera livre
    (`Camera.free_mode`, ver ui/render_system.py::CameraSystem), já que
    as duas passam por aqui. Mapa MENOR que a tela num eixo (não deve
    acontecer em produção — mapas são desenhados maiores que a tela de
    propósito) cairia num intervalo invertido (`min>max` do clamp);
    `max(0.0, ...)` no limite superior evita clamp() explodir nesse
    caso, resultado só fica sem centralizar (aceitável, cenário que não
    deveria existir de verdade)."""
    from engine.components import Camera, Position, Tilemap
    for _, _, cam_pos in world.get_entities_with(Camera, Position):
        offset_x = cam_pos.x - screen_w / 2
        offset_y = cam_pos.y - screen_h / 2
        _tm_first = world.get_first(Tilemap)
        if _tm_first is not None:
            _tilemap = _tm_first[1]
            _map_px_w = _tilemap.map_width_tiles * _tilemap.tile_size
            _map_px_h = _tilemap.map_height_tiles * _tilemap.tile_size
            offset_x = max(0.0, min(offset_x, max(0.0, _map_px_w - screen_w)))
            offset_y = max(0.0, min(offset_y, max(0.0, _map_px_h - screen_h)))
        return offset_x, offset_y
    return 0.0, 0.0


def reengage_chase(world, player_entity: int) -> None:
    """Único ponto de verdade pra "reativar a perseguição de combate"
    (09/09/2026) — inverso simétrico de `ui/npc_interaction.py::
    suppress_chase` (que só liga `chase_suppressed`, nunca toca
    `PlayerAutoMove`). Liga `chase_suppressed=False` E cancela qualquer
    clique de chão/"Seguir" pendente (`PlayerAutoMove.ground_target`/
    `path`/`follow_eid`) — sem isso, usar QUALQUER skill ofensiva
    enquanto um clique de chão ainda está em andamento reativa o
    auto-chase (`ui/systems.py::PlayerInputSystem._process_target`) sem
    cancelar o auto-move de chão, e os dois competem pela posição do
    player todo frame — mesma classe de bug já corrigida pra clique em
    NPC (PROBLEMAS_ARQUITETURA.md §112), nunca fechada pro caso "usar
    skill". Fica mais visível com Interceptar (teleporta o player,
    tornando o zigue-zague óbvio: dash pro mob, depois puxado de volta
    pro tile clicado, repete), mas o conflito existe em qualquer skill
    ofensiva com auto-chase ativo. NUNCA toca `target_entity_id`/
    `is_pursuing` — quem chama decide isso, igual `suppress_chase` nunca
    decidia alvo. Roda em código compartilhado client/servidor (ex:
    `engine/skill_handlers.py`) — `PlayerAutoMove` não existe no lado
    servidor (componente client-only), por isso o guard `if auto_move`."""
    from engine.components import CombatState, PlayerAutoMove
    cs = world.get_component(player_entity, CombatState)
    if cs is not None:
        cs.chase_suppressed = False
    auto_move = world.get_component(player_entity, PlayerAutoMove)
    if auto_move is not None:
        auto_move.active = False
        auto_move.path.clear()
        auto_move.ground_target = None
        auto_move.follow_eid = -1


def _cc_blocks(world, entity_id: int, attr: str) -> bool:
    """Lê `content/status_effects_data.py::EFFECT_DEFS[*].{blocks_move,
    blocks_act}` — fonte única de "este efeito tira o controle do
    jogador". Um efeito de CC novo só precisa marcar a flag certa lá; não
    precisa tocar em `is_action_locked`/`is_movement_locked` nunca mais
    (regra do usuário 21/07/2026, ver PROBLEMAS_ARQUITETURA.md — bug real:
    "stun" já existia como StatusEffects de verdade, mas nenhum gate de
    ação/movimento olhava pra ele antes desta função existir)."""
    from engine.components import StatusEffects
    from content.status_effects_data import EFFECT_DEFS
    sfx = world.get_component(entity_id, StatusEffects)
    if sfx is None:
        return False
    for effect_type in sfx.effects:
        _def = EFFECT_DEFS.get(effect_type)
        if _def is not None and getattr(_def, attr, False):
            return True
    return False


def is_action_locked(world, entity_id: int) -> bool:
    """True se a entidade está impedida de iniciar uma ação (skill OU
    auto-attack) por controle mental — todo efeito com `blocks_act=True`
    em `EFFECT_DEFS` (hoje: stun, sleep, fear, polymorph, disoriented).

    Esses efeitos vivem em StatusEffects, não em CombatState — por isso
    `CombatState.can_act()` (is_alive/is_stunned/is_casting/is_camouflaged)
    NUNCA os cobre, e qualquer gate de ação precisa checar os dois
    separadamente: `not can_act() or is_action_locked(...)`. Sem isso, um
    player sob uma dessas CCs consegue continuar agindo em qualquer
    caminho que só olhe can_act() (bug real encontrado: o auto-attack do
    servidor — server/combat_processor.py — checava só can_act(), deixando
    passar essas CCs; só o cast de skills checava). Único choke-point
    também usado por PlayerInputSystem (ui/systems.py) pra bloquear
    can_act do jogador local.
    """
    return _cc_blocks(world, entity_id, "blocks_act")


def is_movement_locked(world, entity_id: int) -> bool:
    """True se a entidade está impedida de se mover manualmente (WASD,
    clique de chão, "Seguir", perseguição de alvo) — todo efeito com
    `blocks_move=True` em `EFFECT_DEFS` (hoje: stun, sleep, fear,
    polymorph, disoriented, root — root é o único que bloqueia SÓ
    movimento, permitindo agir; os outros 5 bloqueiam os dois, ver
    `is_action_locked`).

    Irmã de `is_action_locked` — mesmo padrão, usada por
    `PlayerInputSystem.update()` (ui/systems.py, cliente) e pelo
    anti-cheat de pacote de movimento bruto (`server/world_server.py`),
    pra bloquear QUALQUER movimento manual sob CC, não só teclado (bug
    real corrigido 21/07/2026 — clique de chão/perseguição ignoravam
    polimorfia/desorientado por completo).
    """
    return _cc_blocks(world, entity_id, "blocks_move")


def incr_char_stat(world, eid: int, field: str, amount: int = 1) -> None:
    """Incrementa um campo escalar de `CharStatsTracker` (Fase E, modal de
    estatísticas) — no-op silencioso se `eid` não tiver o componente (mob,
    NPC, ou entidade sintética) ou não for um dos campos escalares
    conhecidos. Fonte única — todo ponto de tracking (dano, kills, duelo)
    chama isto em vez de buscar o componente na mão, pra nunca divergir do
    schema de `CharStatsTracker` (engine/components.py)."""
    from engine.components import CharStatsTracker
    cst = world.get_component(eid, CharStatsTracker)
    if cst is None or not hasattr(cst, field):
        return
    setattr(cst, field, getattr(cst, field) + amount)


def incr_char_stat_mode(world, eid: int, field: str, mode: str, amount: int = 1) -> None:
    """Irmã de `incr_char_stat` pros campos por-modo (`arena_wins`/
    `arena_losses`, dict[mode_id → int]) — usa `setdefault` porque contas
    antigas podem não ter um `mode_id` novo (ex: "3v3" adicionado depois)
    no dict carregado do banco."""
    from engine.components import CharStatsTracker
    cst = world.get_component(eid, CharStatsTracker)
    if cst is None or not hasattr(cst, field):
        return
    d = getattr(cst, field)
    d[mode] = d.setdefault(mode, 0) + amount


def slugify(name: str) -> str:
    """"Goblin Guerreiro" -> "goblin_guerreiro" — minúsculo, sem acento,
    espaço/pontuação vira "_" (colapsado, sem "_" sobrando nas pontas).
    Usado por `engine/mob_animation.py` pra derivar a chave de arquivo
    de sprite a partir de `EntityIdentity.mob_key or race` (10/10/2026)
    — ponto único de verdade, nenhum outro código reimplementa isso."""
    import re
    import unicodedata
    normalized = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stripped.lower()).strip("_")
    return slug
