# tile_render_system.py
"""
TileRenderSystem — desenho de terreno/objetos do mapa (cache de Surface
por câmera). FogSystem — shadowcasting/névoa de guerra; ficam no mesmo
arquivo porque são caches irmãos (`FogOfWar.version`, incrementado por
FogSystem, é lido por TileRenderSystem a cada frame pra saber quando
invalidar o overlay — ver CLAUDE.md "Atualização coesa" e
PROBLEMAS_ARQUITETURA.md §53). Extraído de ui/systems.py (18/09/2026,
ver PROBLEMAS_ARQUITETURA.md §185, item #2 do backlog de 14).
"""
from __future__ import annotations
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_sizes import UI

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, Enemy, Tilemap, TileMovement, FogOfWar, Visible, NPC
from engine.world import World
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from ui.tile_sprite_manager import TILE_SPRITES
from engine.fov import compute_fov, local_vision_blob

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem


# ── Margem de corte pra objetos do object_matrix maiores que 1 tile ─────────
#
# Bug real relatado pelo usuário (02/09/2026): construção/árvore alta só
# aparecia na tela quando a câmera alcançava a BASE do objeto (o tile-âncora
# em object_matrix), mesmo que o topo do sprite já devesse estar visível bem
# antes disso -- e piorava com zoom (janela de tiles visíveis menor). Causa:
# get_world_objects()/render_static_objects() varriam só "tela em tiles + 2"
# (margem fixa pequena) pra decidir quais tiles-âncora considerar, mas o
# sprite de um objeto cresce PRA CIMA e PRA DIREITA a partir da âncora --
# um objeto alto ancorado abaixo da tela, ou largo ancorado à esquerda da
# tela, tem sprite real ainda dentro da área visível mesmo com a âncora
# fora. Mesma classe de bug documentada pra Tilemap Renderer da Unity
# ("Chunk/Cull Bounds" -- corte por grade sempre precisa de margem do
# tamanho do MAIOR sprite do conjunto, não uma constante pequena). Fix:
# estender a varredura pra ESQUERDA (objeto largo) e pra BAIXO (objeto
# alto) pelo tamanho do maior sprite do catálogo -- NUNCA um número fixo
# hardcoded, pra não ficar errado se um asset maior for adicionado depois
# (ponto único de verdade, mesmo racional de qualquer "maior X do catálogo"
# já usado no projeto). Topo/direita não precisavam de margem PELO SPRITE
# (que só cresce pra longe da tela nesses casos) -- mas um objeto com
# `TileType.light_source` (lampião/tocha, 21/09/2026) PRECISA dos 4 lados:
# luz se espalha em círculo, não "cresce numa direção só" como sprite (bug
# real relatado pelo usuário 27/09/2026, luz só aparecia com o objeto já
# em tela). Ver PROBLEMAS_ARQUITETURA.md §102.
_OBJECT_SCAN_MARGIN_CACHE: "tuple[int, int, int, int] | None" = None


def _tile_effective_size_px(tile_type) -> tuple[int, int]:
    """(largura, altura) em px do sprite renderizado desse TileType --
    sprite real (sprite_px_w/h) quando existe, senão o cálculo legado
    (tile_size × sprite_tiles_wide, tile_size + overlay_height). Mesma
    lógica de get_world_objects()/render_static_objects(), extraída aqui
    só pra achar o MAIOR objeto do catálogo (não duplica o cálculo de
    desenho, que continua nos 2 métodos)."""
    spr_w = getattr(tile_type, "sprite_px_w", 0)
    spr_h = getattr(tile_type, "sprite_px_h", 0)
    if spr_w > 0 and spr_h > 0:
        return spr_w, spr_h
    tiles_wide = getattr(tile_type, "sprite_tiles_wide", 1)
    return TILE_SIZE * tiles_wide, TILE_SIZE + getattr(tile_type, "overlay_height", 0)


def _object_scan_margin_tiles() -> tuple[int, int, int, int]:
    """(esquerda, baixo, cima, direita) — colunas/linhas extras que a
    varredura de object_matrix precisa somar à janela ingênua de
    tela-em-tiles. Calculada 1x (catálogo é estático depois do
    startup) a partir do maior sprite_px_w/sprite_px_h de
    OBJECT_MAPPING inteiro (cima/direita ficam 0 nesta parte — sprite
    só cresce pra longe da tela nesses 2 lados, ver comentário acima).

    Objeto com `TileType.light_source` (27/09/2026, bug real relatado
    pelo usuário: luz só aparecia quando o objeto entrava na tela)
    quebra essa premissa — a luz se espalha em CÍRCULO ao redor do
    ponto da chama, alcançando a tela em QUALQUER direção mesmo com a
    âncora do objeto ainda longe fora dela. Margem de luz = maior
    `radius_tiles` do catálogo inteiro (+1 tile de folga, já que o
    raio é medido a partir do ponto da chama — `light_source[0:2]` —
    não da borda do tile-âncora), aplicada nos 4 lados (une com a
    margem de sprite via `max()`, nunca substitui — um objeto grande
    E luminoso precisa da maior das duas)."""
    global _OBJECT_SCAN_MARGIN_CACHE
    if _OBJECT_SCAN_MARGIN_CACHE is None:
        max_w = 0
        max_h = 0
        max_light_radius_tiles = 0
        for tile_type in OBJECT_MAPPING.values():
            w, h = _tile_effective_size_px(tile_type)
            max_w = max(max_w, w)
            max_h = max(max_h, h)
            if tile_type.light_source is not None:
                max_light_radius_tiles = max(max_light_radius_tiles, tile_type.light_source[5])
        # -(-x//n) = ceil(x/n); -1 porque a margem fixa "+2" já cobre 1 tile
        # de sobra em cada direção.
        margin_left_sprite = max(0, -(-max_w // TILE_SIZE) - 1)
        margin_down_sprite = max(0, -(-max_h // TILE_SIZE) - 1)
        light_margin = max_light_radius_tiles + 1 if max_light_radius_tiles > 0 else 0
        _OBJECT_SCAN_MARGIN_CACHE = (
            max(margin_left_sprite, light_margin),
            max(margin_down_sprite, light_margin),
            light_margin,
            light_margin,
        )
    return _OBJECT_SCAN_MARGIN_CACHE



class TileRenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen
        from engine.tileset import TILE_MAPPING as _TM, FLOOR_TILE as _FT
        self._tile_sprites = TILE_SPRITES
        self._tile_mapping = _TM
        self._floor_tile   = _FT
        _tw = screen.get_width()  // TILE_SIZE + 2
        _th = screen.get_height() // TILE_SIZE + 2
        # Cache de surface — pré-alocada; reconstruída apenas quando a câmera cruza fronteira de tile
        self._cache_surf    = pygame.Surface((_tw * TILE_SIZE, _th * TILE_SIZE))
        self._cache_tile_x:    int = -99999
        self._cache_tile_y:    int = -99999
        self._cache_tiles_w:   int = _tw
        self._cache_tiles_h:   int = _th
        # Fog of War: névoa leve para tiles explorados mas fora do campo de visão
        self._fog_explored_surf: pygame.Surface = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
        self._fog_explored_surf.fill((0, 0, 0, 25))
        # Fog of War: gradiente de fade na borda do campo de visão.
        # FOG_FADE_LEVELS superfícies com alpha crescente de ~0 até FOG_FADE_MAX_ALPHA.
        # FOG_FADE_START: fração do raio a partir da qual o fade começa (0.0–1.0).
        FOG_FADE_LEVELS    = 10
        FOG_FADE_START     = 0.60   # fade começa a 60% do raio
        FOG_FADE_MAX_ALPHA = 25     # alpha máximo na borda — igual ao explorado (transição contínua)
        self._fog_fade_start:     float = FOG_FADE_START
        self._fog_fade_surfs: list = []
        for i in range(FOG_FADE_LEVELS):
            t     = (i + 1) / FOG_FADE_LEVELS          # 0.1 → 1.0
            alpha = max(1, int(t * FOG_FADE_MAX_ALPHA))
            s = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
            s.fill((0, 0, 0, alpha))
            self._fog_fade_surfs.append(s)
        # Cache do overlay de fog — reconstrói quando o tile de origem da
        # câmera muda OU quando FogOfWar.version muda (13/08/2026 — sem
        # isso, o overlay ficava "preso" perto de bush pequena: a visão
        # mudava mais rápido do que a câmera cruzava fronteira de tile).
        self._fog_overlay_surf: "pygame.Surface | None" = None
        self._fog_cache_tile_ox: int = -99999
        self._fog_cache_tile_oy: int = -99999
        self._fog_cache_version: int = -1
        self._pending_fog_blit = None

    def invalidate_cache(self) -> None:
        """Força reconstrução do cache no próximo frame (chamar após troca de mapa)."""
        self._cache_tile_x      = -99999
        self._cache_tile_y      = -99999
        self._fog_cache_tile_ox = -99999
        self._fog_cache_tile_oy = -99999
        self._fog_cache_version = -1
        self._tile_sprites.invalidate()

    def _object_visible_tile_range(self, tile_size: int, camera_offset_x: float,
                                    camera_offset_y: float) -> tuple[int, int, int, int]:
        """(tile_ox, tile_oy, tiles_w, tiles_h) pra varredura de
        object_matrix -- mesma janela "tela em tiles + 2" que
        TileRenderSystem.render() usa pro terreno (1×1, sem margem),
        estendida pelo maior sprite do catálogo (ESQUERDA/BAIXO) E pelo
        maior raio de luz do catálogo (os 4 lados — ver
        _object_scan_margin_tiles, PROBLEMAS_ARQUITETURA.md §102 e
        §27/09/2026). Ponto único de verdade pra get_world_objects()/
        render_static_objects() -- os 2 duplicavam esse cálculo antes."""
        margin_left, margin_down, margin_top, margin_right = _object_scan_margin_tiles()
        tile_ox = int(camera_offset_x // tile_size) - margin_left
        tile_oy = int(camera_offset_y // tile_size) - margin_top
        tiles_w = self.world_surf.get_width()  // tile_size + 2 + margin_left + margin_right
        tiles_h = self.world_surf.get_height() // tile_size + 2 + margin_down + margin_top
        return tile_ox, tile_oy, tiles_w, tiles_h

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        _tm_render_first = self.world.get_first(Tilemap)
        if _tm_render_first is not None:
            tilemap_comp = _tm_render_first[1]
            tile_size = tilemap_comp.tile_size

            # tile_ox/tile_oy PRECISAM ser inteiros (índice de qual bloco de
            # tiles está em cache — dx/dy abaixo compara contra o índice do
            # frame anterior). sub_x/sub_y NÃO — ficam em ponto flutuante
            # (derivados direto de camera_offset_x/y, sem truncar antes) e só
            # são arredondados pelo blit() do pygame no fim da função, o
            # MESMO ponto onde toda entidade (personagem, mob, etc) também é
            # truncada — antes, truncar aqui ANTES do resto do frame fazia o
            # fundo "saltar" em passos de pixel inteiro num instante
            # diferente de quando as entidades truncavam, e a câmera
            # suavizada (que muda a cada frame) deixava esse descompasso
            # visível como "tremida" relativa fundo↔personagem. Bug real
            # relatado pelo usuário 19/07/2026 — ver ARQUITETURA_ONLINE.md
            # (o fix anterior, arredondar cam_x uma vez em game.py, resolveu
            # a tremida mas trocou por "saltar de pixel em pixel" ao mover,
            # porque forçava a câmera a só existir em posições de pixel
            # NATIVO inteiro — bem mais grosseiro que 1 pixel de tela depois
            # do zoom 1.5x-2.5x. Este fix é o correto: preserva o sub-pixel
            # até o blit, sem forçar nenhum salto artificial).
            tile_ox = int(camera_offset_x // tile_size)
            tile_oy = int(camera_offset_y // tile_size)
            sub_x   = camera_offset_x - tile_ox * tile_size
            sub_y   = camera_offset_y - tile_oy * tile_size

            tiles_w = self.world_surf.get_width()  // tile_size + 2
            tiles_h = self.world_surf.get_height() // tile_size + 2

            _size_changed = (tiles_w != self._cache_tiles_w
                             or tiles_h != self._cache_tiles_h
                             or self._cache_surf is None)
            dx = tile_ox - self._cache_tile_x
            dy = tile_oy - self._cache_tile_y

            # Reconstrói ou redimensiona cache se necessário
            surf_w = tiles_w * tile_size
            surf_h = tiles_h * tile_size
            if (_size_changed
                    or self._cache_surf is None
                    or self._cache_surf.get_width()  != surf_w
                    or self._cache_surf.get_height() != surf_h):
                self._cache_surf = pygame.Surface((surf_w, surf_h))
                _size_changed = True

            # Pré-cache de todos os locais pesados — evita lookup de atributo por tile.
            # (closure captura esses locais; chamada de método re-faria os lookups 1760x)
            cache_surf   = self._cache_surf
            TILE_SPRITES = self._tile_sprites
            _TM          = self._tile_mapping
            _FT          = self._floor_tile
            rows         = tilemap_comp.tile_matrix
            terrain_rows = tilemap_comp.terrain_matrix
            vis_rows     = tilemap_comp.terrain_visual
            obj_rows     = tilemap_comp.object_matrix
            map_h        = tilemap_comp.map_height_tiles
            map_w        = tilemap_comp.map_width_tiles
            _blit        = cache_surf.blit
            _rect        = pygame.draw.rect
            _black       = (0, 0, 0)
            _get_spr     = TILE_SPRITES.get
            _get_raw     = TILE_SPRITES.get_raw_sprite

            def _draw(tx: int, ty: int, rx: int, ry: int) -> None:
                dest = (tx * tile_size, ty * tile_size, tile_size, tile_size)
                if 0 <= ry < map_h and 0 <= rx < map_w:
                    tile_type = rows[ry][rx]
                    vis_id = (vis_rows[ry][rx]
                              if vis_rows and ry < len(vis_rows) and rx < len(vis_rows[ry])
                              else "")
                    has_obj = (obj_rows and ry < len(obj_rows)
                               and rx < len(obj_rows[ry])
                               and obj_rows[ry][rx] not in ("", "."))
                    if vis_id:
                        spr = _get_raw(vis_id)
                        if spr is not None:
                            _blit(spr, dest[:2])
                        else:
                            t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                            _rect(cache_surf, _TM.get(t_char, _FT).color, dest)
                    elif has_obj or getattr(tile_type, "sprite_px_h", 0) > 0:
                        t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                        _rect(cache_surf, _TM.get(t_char, _FT).color, dest)
                    elif tile_type.overlay_height > 0:
                        _rect(cache_surf, tile_type.color, dest)
                    else:
                        sprite = _get_spr(tile_type, rx, ry)
                        if sprite is not None:
                            _blit(sprite, dest[:2])
                        else:
                            _rect(cache_surf, tile_type.color, dest)
                else:
                    _rect(cache_surf, _black, dest)

            if _size_changed or abs(dx) > 2 or abs(dy) > 2:
                # Rebuild completo
                for ty in range(tiles_h):
                    for tx in range(tiles_w):
                        _draw(tx, ty, tile_ox + tx, tile_oy + ty)
            elif dx != 0 or dy != 0:
                # Cache-shift: desloca pixels e redesenha só as bordas expostas.
                # dx>0 = câmera foi p/ direita (conteúdo desloca p/ esquerda no cache).
                cache_surf.scroll(-dx * tile_size, -dy * tile_size)

                # Colunas expostas
                if dx > 0:
                    col_range = range(tiles_w - dx, tiles_w)
                elif dx < 0:
                    col_range = range(0, -dx)
                else:
                    col_range = range(0, 0)

                # Linhas expostas
                if dy > 0:
                    row_range = range(tiles_h - dy, tiles_h)
                elif dy < 0:
                    row_range = range(0, -dy)
                else:
                    row_range = range(0, 0)

                # Redesenha somente tiles novos (coluna nova + linha nova)
                drawn: set = set()
                for tx in col_range:
                    for ty in range(tiles_h):
                        _draw(tx, ty, tile_ox + tx, tile_oy + ty)
                        drawn.add((tx, ty))
                for ty in row_range:
                    for tx in range(tiles_w):
                        if (tx, ty) not in drawn:
                            _draw(tx, ty, tile_ox + tx, tile_oy + ty)

            self._cache_tile_x  = tile_ox
            self._cache_tile_y  = tile_oy
            self._cache_tiles_w = tiles_w
            self._cache_tiles_h = tiles_h

            # 1 blit por frame
            self.world_surf.blit(cache_surf, (-sub_x, -sub_y))

            # Fog é desenhado separadamente via render_fog() para permitir
            # que outros sistemas (quest, shop) desenhem seus indicadores
            # world-space ANTES do overlay de fog ser aplicado.
            self._pending_fog_blit = (tile_ox, tile_oy, tiles_w, tiles_h, sub_x, sub_y, tile_size)

    def render_fog(self) -> None:
        """Aplica o overlay de fog of war sobre tudo que foi desenhado até agora.
        Deve ser chamado APÓS render() e APÓS render_world() de todos os sistemas world-space."""
        if not hasattr(self, "_pending_fog_blit") or self._pending_fog_blit is None:
            return
        tile_ox, tile_oy, tiles_w, tiles_h, sub_x, sub_y, tile_size = self._pending_fog_blit
        self._pending_fog_blit = None

        _fow_fog_first = self.world.get_first(FogOfWar)
        fog_comp = _fow_fog_first[1] if _fow_fog_first is not None else None

        if fog_comp is None:
            return

        # Pega tilemap para checar extensões superiores de objetos altos
        _tm_fog_first = self.world.get_first(Tilemap)
        tilemap_fog = _tm_fog_first[1] if _tm_fog_first is not None else None

        if (tile_ox != self._fog_cache_tile_ox
                or tile_oy != self._fog_cache_tile_oy
                or fog_comp.version != self._fog_cache_version
                or self._fog_overlay_surf is None):

            surf_w = tiles_w * tile_size
            surf_h = tiles_h * tile_size
            if (self._fog_overlay_surf is None
                    or self._fog_overlay_surf.get_width()  != surf_w
                    or self._fog_overlay_surf.get_height() != surf_h):
                self._fog_overlay_surf = pygame.Surface((surf_w, surf_h), pygame.SRCALPHA)

            self._fog_overlay_surf.fill((0, 0, 0, 0))

            explored   = fog_comp.explored
            visible    = fog_comp.visible
            exp_surf   = self._fog_explored_surf
            fade_surfs = self._fog_fade_surfs
            n_levels   = len(fade_surfs)
            fade_start = self._fog_fade_start
            px, py     = fog_comp._last_tile
            radius     = fog_comp.radius
            fade_begin = fade_start * radius
            fade_range = radius - fade_begin

            # Pré-calcula tiles que são extensão superior de objetos visíveis altos.
            # Para esses tiles o fog não é aplicado: o sprite do objeto cobre essa área
            # e deve aparecer acima do fog quando a base está visível.
            _skip_fog: set = set()
            if tilemap_fog is not None:
                _rows    = tilemap_fog.tile_matrix
                _obj     = tilemap_fog.object_matrix
                _map_h   = tilemap_fog.map_height_tiles
                _map_w   = tilemap_fog.map_width_tiles
                for _ry in range(tile_oy, min(tile_oy + tiles_h, _map_h)):
                    for _rx in range(tile_ox, min(tile_ox + tiles_w, _map_w)):
                        if (_rx, _ry) in visible:
                            continue  # visível → fog já não cobre, irrelevante
                        # Tile tem TileType de sprite alto sem objeto direto aqui?
                        _tile = _rows[_ry][_rx] if _ry < len(_rows) and _rx < len(_rows[_ry]) else None
                        if _tile is None:
                            continue
                        _sprite_h = getattr(_tile, "sprite_px_h", 0)
                        if _sprite_h <= TILE_SIZE:
                            continue  # sprite não é multi-tile
                        _obj_here = (_obj[_ry][_rx]
                                     if _ry < len(_obj) and _rx < len(_obj[_ry]) else ".")
                        if _obj_here and _obj_here != ".":
                            continue  # tem objeto direto aqui — não é extensão superior
                        # Verifica tiles abaixo: algum tem objeto visível que sobe até aqui?
                        _max_delta = _sprite_h // TILE_SIZE
                        for _dy in range(1, _max_delta + 1):
                            _base_y = _ry + _dy
                            if not (0 <= _base_y < _map_h):
                                break
                            _obj_base = (_obj[_base_y][_rx]
                                         if _base_y < len(_obj) and _rx < len(_obj[_base_y]) else ".")
                            if _obj_base and _obj_base != "." and (_rx, _base_y) in visible:
                                _skip_fog.add((_rx, _ry))
                                break

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    dx_s   = tx * tile_size
                    dy_s   = ty * tile_size
                    if (rx, ry) in visible:
                        if fade_range > 0:
                            d = max(abs(rx - px), abs(ry - py))
                            if d > fade_begin:
                                t     = (d - fade_begin) / fade_range
                                level = min(n_levels - 1, int(t * n_levels))
                                self._fog_overlay_surf.blit(fade_surfs[level], (dx_s, dy_s))
                    elif (rx, ry) in _skip_fog:
                        pass  # extensão superior de objeto visível — não aplica fog
                    elif (rx, ry) in explored:
                        self._fog_overlay_surf.blit(exp_surf, (dx_s, dy_s))
                    else:
                        pygame.draw.rect(self._fog_overlay_surf, (0, 0, 0, 255),
                                         (dx_s, dy_s, tile_size, tile_size))

            self._fog_cache_tile_ox = tile_ox
            self._fog_cache_tile_oy = tile_oy
            self._fog_cache_version = fog_comp.version

        self.world_surf.blit(self._fog_overlay_surf, (-sub_x, -sub_y))

    def get_world_objects(self, camera_offset_x: float, camera_offset_y: float) -> list:
        """
        Retorna lista de objetos visíveis (object_matrix) para o pass 2 (Y-sort).
        Cada item: dict com sort_y, screen_x, screen_y, sprite, color, width, height.
        """
        objects = []
        # tile_ox/tile_oy: índice inteiro (limite do loop de tiles). scr_x/
        # scr_y usam camera_offset_x/y em ponto flutuante direto (não um
        # cam_x pré-truncado) — mesmo racional de TileRenderSystem.render()
        # acima: objetos estáticos (árvore, arbusto) não podem truncar a
        # câmera num instante diferente de onde as entidades truncam, senão
        # "tremem" relativo ao personagem ao mover.

        _tm_objs_first = self.world.get_first(Tilemap)
        if _tm_objs_first is not None:
            tilemap_comp = _tm_objs_first[1]
            tile_size = tilemap_comp.tile_size
            obj_rows  = tilemap_comp.object_matrix
            map_h     = tilemap_comp.map_height_tiles
            map_w     = tilemap_comp.map_width_tiles

            tile_ox, tile_oy, tiles_w, tiles_h = self._object_visible_tile_range(
                tile_size, camera_offset_x, camera_offset_y)

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    if not (0 <= ry < map_h and 0 <= rx < map_w):
                        continue
                    if ry >= len(obj_rows):
                        continue
                    obj_row  = obj_rows[ry]
                    obj_char = obj_row[rx] if rx < len(obj_row) else "."
                    if not obj_char or obj_char == ".":
                        continue

                    tile_type = OBJECT_MAPPING.get(obj_char)
                    if tile_type is None:
                        continue

                    # Objetos com no_ysort=True são renderizados por render_static_objects()
                    if getattr(tile_type, "no_ysort", False):
                        continue

                    # Sprite tree PNG (tamanho real) ou objeto legacy (overlay_height)
                    spr_px_w = getattr(tile_type, "sprite_px_w", 0)
                    spr_px_h = getattr(tile_type, "sprite_px_h", 0)
                    spr_name = getattr(tile_type, "sprite_name", "")
                    light_source = getattr(tile_type, "light_source", None)
                    night_overlay_sprite = None
                    if spr_px_w > 0 and spr_px_h > 0 and spr_name:
                        sprite_w = spr_px_w
                        total_h  = spr_px_h
                        sprite   = TILE_SPRITES.get_raw_sprite(spr_name)
                        # Camada "aceso" à noite (22/09/2026, pedido do
                        # usuário) — só resolvida pra objetos que JÁ são
                        # fonte de luz (nunca pra qualquer tile com folha
                        # noturna companheira — a folha pode ter arte
                        # extra reservada pra outras interações futuras,
                        # ainda não é pra acender sozinha).
                        if light_source:
                            night_overlay_sprite = TILE_SPRITES.get_night_overlay_sprite(spr_name)
                    else:
                        tiles_wide = getattr(tile_type, "sprite_tiles_wide", 1)
                        sprite_w   = tile_size * tiles_wide
                        total_h    = tile_size + tile_type.overlay_height
                        sprite     = TILE_SPRITES.get(tile_type, rx, ry)

                    sort_y   = ry * tile_size + tile_size // 2
                    anchor_y = (ry + 1) * tile_size
                    scr_x    = rx * tile_size - camera_offset_x
                    scr_y    = anchor_y - camera_offset_y - total_h

                    objects.append({
                        "sort_y":   sort_y,
                        "screen_x": scr_x,
                        "screen_y": scr_y,
                        "sprite":   sprite,
                        "color":    tile_type.color,
                        "width":    sprite_w,
                        "height":   total_h,
                        "tile_x":   rx,
                        "tile_y":   ry,
                        # occludes_player (20/09/2026) — repassado pro
                        # RenderSystem decidir a transparência de "jogador
                        # atrás do objeto" (ver TileType.occludes_player).
                        "occludes_player": getattr(tile_type, "occludes_player", False),
                        # light_source (21/09/2026) — repassado pro
                        # DayNightSystem calcular a posição de tela da luz
                        # (screen_x/screen_y + off_x/off_y, sem recalcular
                        # offset de câmera à parte — ver TileType.light_source).
                        "light_source": light_source,
                        # night_overlay_sprite (22/09/2026) — Surface da
                        # versão "acesa" (ou None) — RenderSystem desenha
                        # por cima do sprite normal quando
                        # ui.day_night_system.is_night() for True. Mesma
                        # posição/tamanho do sprite base (screen_x/screen_y/
                        # width/height acima) — nunca recalcula âncora à
                        # parte.
                        "night_overlay_sprite": night_overlay_sprite,
                    })
        return objects

    def render_static_objects(self, camera_offset_x: float, camera_offset_y: float) -> None:
        """Renderiza objetos com no_ysort=True diretamente no world_surf, abaixo de entidades."""
        # Mesmo racional de get_world_objects()/render() acima — tile_ox/oy
        # inteiro só pra limite do loop, scr_x/scr_y ficam em ponto
        # flutuante direto.

        # Fog: só objetos em tiles visíveis ou explorados
        _fog_visible  = None
        _fog_explored = None
        _fow_static_first = self.world.get_first(FogOfWar)
        if _fow_static_first is not None:
            _fog_visible  = _fow_static_first[1].visible
            _fog_explored = _fow_static_first[1].explored

        _tm_static_first = self.world.get_first(Tilemap)
        if _tm_static_first is not None:
            tilemap_comp = _tm_static_first[1]
            tile_size = tilemap_comp.tile_size
            obj_rows  = tilemap_comp.object_matrix
            map_h     = tilemap_comp.map_height_tiles
            map_w     = tilemap_comp.map_width_tiles
            tile_ox, tile_oy, tiles_w, tiles_h = self._object_visible_tile_range(
                tile_size, camera_offset_x, camera_offset_y)

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    if not (0 <= ry < map_h and 0 <= rx < map_w):
                        continue
                    if _fog_visible is not None and _fog_explored is not None:
                        if (rx, ry) not in _fog_visible and (rx, ry) not in _fog_explored:
                            continue
                    if ry >= len(obj_rows):
                        continue
                    obj_row  = obj_rows[ry]
                    obj_char = obj_row[rx] if rx < len(obj_row) else "."
                    if not obj_char or obj_char == ".":
                        continue
                    tile_type = OBJECT_MAPPING.get(obj_char)
                    if tile_type is None or not getattr(tile_type, "no_ysort", False):
                        continue

                    spr_px_w = getattr(tile_type, "sprite_px_w", 0)
                    spr_px_h = getattr(tile_type, "sprite_px_h", 0)
                    spr_name = getattr(tile_type, "sprite_name", "")
                    if spr_px_w > 0 and spr_px_h > 0 and spr_name:
                        sprite  = TILE_SPRITES.get_raw_sprite(spr_name)
                        total_h = spr_px_h
                        sprite_w = spr_px_w
                    else:
                        sprite   = TILE_SPRITES.get(tile_type, rx, ry)
                        total_h  = tile_size + tile_type.overlay_height
                        sprite_w = tile_size

                    anchor_y = (ry + 1) * tile_size
                    scr_x    = rx * tile_size - camera_offset_x
                    scr_y    = anchor_y - camera_offset_y - total_h

                    if sprite is not None:
                        self.world_surf.blit(sprite, (scr_x, scr_y))
                    else:
                        pygame.draw.rect(self.world_surf, tile_type.color,
                                         (scr_x, scr_y, sprite_w, total_h))



class FogSystem(System):
    """
    Atualiza o campo de visão do jogador por shadowcasting recursivo (8 octantes).

    Executa apenas quando o jogador muda de tile — custo O(radius²) por frame
    de movimento, zero nos frames sem deslocamento.
    """

    def __init__(self, world: World) -> None:
        self.world = world

    def update(self, events: list = None, dt: float = 0) -> None:
        _tm_upd_first = self.world.get_first(Tilemap)
        tilemap = _tm_upd_first[1] if _tm_upd_first is not None else None
        if tilemap is None:
            return

        rows  = tilemap.tile_matrix
        map_h = tilemap.map_height_tiles
        map_w = tilemap.map_width_tiles

        def is_blocking(x: int, y: int) -> bool:
            if not (0 <= x < map_w and 0 <= y < map_h):
                return True
            return rows[y][x].vision_height >= 2

        def is_solid(x: int, y: int) -> bool:
            if not (0 <= x < map_w and 0 <= y < map_h):
                return True
            return rows[y][x].is_solid

        def _is_blocking_from(ox: int, oy: int):
            # Bush/copa de árvore bloqueiam de fora-pra-dentro, nunca de
            # dentro-pra-fora (13/08/2026, ver PROBLEMAS_ARQUITETURA.md) —
            # se o observador está em cima de um tile bloqueante andável,
            # o blob conectado que o contém vira transparente SÓ pro FOV
            # calculado a partir dessa origem; outra bush separada continua
            # bloqueando normal. `is_solid` impede o blob de vazar pra
            # dentro de parede/pedra/tronco encostados na bush.
            blob = local_vision_blob(ox, oy, is_blocking, is_solid)
            if not blob:
                return is_blocking

            def _blocking(x: int, y: int) -> bool:
                if (x, y) in blob:
                    return False
                return is_blocking(x, y)
            return _blocking

        fog = None
        _fow_tm_first = self.world.get_first(FogOfWar, TileMovement)
        if _fow_tm_first is not None:
            _, f, tile_move = _fow_tm_first
            fog = f
            px, py = tile_move.current_tile_x, tile_move.current_tile_y

            # Recomputa quando o jogador muda de tile OU quando os centros de
            # visão de time mudam (torre/minion/teammate aliado se move) —
            # visão compartilhada de time (30/07/2026): torres/minions/
            # teammates aliados também "exploram" a névoa, com shadowcasting
            # PRÓPRIO a partir da posição deles (não da do jogador) — é assim
            # que uma torre do outro lado de um pilar revela área que o
            # próprio jogador não enxergaria em linha reta.
            _ally_key = tuple(fog.ally_centers)
            if (px, py) != fog._last_tile or _ally_key != fog._last_ally_centers:
                fog._last_tile         = (px, py)
                fog._last_ally_centers = _ally_key

                # LOS (shadowcasting, raio pequeno) — controla quais entidades são visíveis
                fog.visible = compute_fov(px, py, fog.radius, _is_blocking_from(px, py))

                # Exploração (círculo largo) — descobre tiles para o mapa/tela sem LOS
                er = fog.explore_radius
                er_sq = er * er
                for dy in range(-er, er + 1):
                    for dx in range(-er, er + 1):
                        if dx * dx + dy * dy <= er_sq:
                            ex, ey = px + dx, py + dy
                            if 0 <= ex < map_w and 0 <= ey < map_h:
                                fog.explored.add((ex, ey))

                # Cache por aliado (15/09/2026, ver PROBLEMAS_ARQUITETURA.md
                # §146) — aliado (player/torre/minion) que não mudou de
                # posição/raio desde o último tick reaproveita o FOV já
                # calculado, em vez de rodar o shadowcasting de novo. Sem
                # isso, QUALQUER aliado mudando de tile recalculava TODOS —
                # em BG com vários minions/torres, esse "todos" é bem maior
                # que mundo aberto (zero aliados) ou Arena (time pequeno,
                # sem minion/torre). `_ALLY_FOV_CACHE_MAX` evita crescimento
                # sem limite numa partida longa com muitas posições
                # diferentes (mesmo padrão de `_outline_cache`,
                # ui/floating_text.py).
                _ALLY_FOV_CACHE_MAX = 300
                for ax, ay, ar in fog.ally_centers:
                    _ally_key2 = (ax, ay, ar)
                    _ally_fov = fog._ally_fov_cache.get(_ally_key2)
                    if _ally_fov is None:
                        _ally_fov = compute_fov(ax, ay, ar, _is_blocking_from(ax, ay))
                        if len(fog._ally_fov_cache) >= _ALLY_FOV_CACHE_MAX:
                            fog._ally_fov_cache.clear()
                        fog._ally_fov_cache[_ally_key2] = _ally_fov
                    fog.visible |= _ally_fov
                    fog.explored |= _ally_fov

                fog.version += 1

        if fog is None:
            return

        # Atualiza tag Visible em todos os inimigos e NPCs a cada frame.
        # Necessário mesmo sem movimento do jogador (entidades podem mudar de tile).
        for eid, _, etm in self.world.get_entities_with(Enemy, TileMovement):
            in_sight = (etm.current_tile_x, etm.current_tile_y) in fog.visible
            has_tag  = self.world.get_component(eid, Visible) is not None
            if in_sight and not has_tag:
                self.world.add_component(eid, Visible())
            elif not in_sight and has_tag:
                self.world.remove_component(eid, Visible)

        # NPCs estáticos — rastreados via componente NPC (único ponto independente de capacidades)
        for eid, _, npos in self.world.get_entities_with(NPC, Position):
            ntx = int(npos.x / TILE_SIZE)
            nty = int(npos.y / TILE_SIZE)
            in_sight = (ntx, nty) in fog.visible
            has_tag  = self.world.get_component(eid, Visible) is not None
            if in_sight and not has_tag:
                self.world.add_component(eid, Visible())
            elif not in_sight and has_tag:
                self.world.remove_component(eid, Visible)












