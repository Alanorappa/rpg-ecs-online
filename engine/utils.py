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

    Usada para distâncias em grade de 8 direções (tiles).
    chebyshev(1,1, 3,2) == 2  (diagonal conta como 1)
    """
    return max(abs(ax - bx), abs(ay - by))


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
