# tileset.py
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List

@dataclass
class TileType:
    """Define as propriedades de um tipo de tile."""
    name:           str
    color:          tuple        # Cor RGB — usada como fallback quando sem sprite
    is_solid:       bool  = False
    sprites:        List[str] = field(default_factory=list)
    overlay_height: int   = 0
    sprite_name:    str   = ""   # nome do arquivo sem extensão (ex: "t1")
    sprite_px_w:    int   = 0    # largura real em pixels do PNG
    sprite_px_h:    int   = 0    # altura real em pixels do PNG
    # ── Colisão ────────────────────────────────────────────────────────────────
    # collision_rect — área sólida em pixels, relativa ao canto superior esquerdo
    # do sprite PNG. Formato pygame: (x, y, largura, altura).
    #
    #   None          → apenas o tile base (32×32 inferior) é sólido (padrão).
    #   "full"        → o sprite inteiro é sólido (calculado com sprite_px_w/h).
    #   (x, y, w, h)  → retângulo explícito em pixels de sprite-space.
    #
    # Exemplos para um sprite de 32×64:
    #   None            → sólido só na metade inferior (tile base)
    #   "full"          → sólido nos 32×64 inteiros (bloqueia tile base + tile acima)
    #   (0, 16, 32, 48) → sólido dos 16px abaixo do topo até o final
    collision_rect: tuple | str | None = None

    # vision_rect — pegada (em tiles, mesmo formato/semântica de
    # collision_rect: None/"full"/(x,y,w,h)) usada SÓ pra decidir quais
    # tiles ficam com `vision_height` alto quando o objeto bloqueia visão
    # (`get_vision_offsets`) — INDEPENDENTE de collision_rect (13/08/2026,
    # pedido do usuário). Existe porque as duas pegadas legitimamente
    # DIVERGEM: bush precisa bloquear visão na largura do sprite inteiro
    # sem virar sólida (collision_rect fica None, andável); árvore
    # precisa manter só o tronco sólido (collision_rect estreito
    # existente, intocado) mas bloquear visão na copa inteira. `None`
    # (default) = só o tile-âncora, mesmo comportamento de
    # collision_rect=None. Nunca deriva is_solid (ao contrário de
    # collision_rect via _resolve_collision) — puramente sobre visão.
    vision_rect: tuple | str | None = None

    # ── Altura visual (para Fog of War) ───────────────────────────────────────
    # Determina se o elemento bloqueia a linha de visão do jogador no FOV.
    #
    #   0 → plano (terreno, água, pedras rasas) — não interfere na visão
    #   1 → médio (arbustos, lápides, bancos)   — não interfere na visão
    #   2 → alto (árvores, paredes, montanhas)  — bloqueia o campo de visão
    #
    # Apenas vision_height >= 2 é considerado bloqueante pelo FogSystem.
    vision_height: int = 0

    # is_bush (01/09/2026) — marca formalmente "isto é um arbusto de
    # stealth", independente de vision_height (árvore também bloqueia
    # visão com vision_height=2, mas NÃO é bush). Único consumidor:
    # server/bush_zone_processor.py::_is_on_bush_tile (feedback de
    # opacidade pro próprio jogador) — NUNCA inferir isso por
    # sprite_name/prefixo em runtime, o dado nasce aqui, na fonte
    # (catálogo), e é propagado por engine/entity_factory.py::create_tilemap
    # junto com vision_height.
    is_bush: bool = False

    # occludes_player (20/09/2026, pedido do usuário) — objeto "alto"
    # (árvore, construção) que fica 50% transparente (ajustado de 70%
    # no mesmo dia) quando o PRÓPRIO jogador está atrás dele na tela
    # (Y-sort: jogador desenhado ANTES dele na mesma passada) E os
    # retângulos de tela se sobrepõem —
    # mesmo efeito clássico de "ver o personagem atrás da árvore".
    # Curado por família/grupo no catálogo (não deriva de vision_height/
    # is_solid — nem todo objeto alto deve participar, decisão do
    # usuário), propagado por discover_object_sheet_tiles(). Único
    # consumidor: ui/render_system.py (ver get_world_objects() em
    # ui/tile_render_system.py, que repassa este campo no dict do objeto).
    occludes_player: bool = False

    # ── Sistema de pisos (estruturas escalonáveis) ────────────────────────────
    # elevation    — piso do tile: 0=chão (padrão), 1=primeiro andar, etc.
    # is_transition— True em tiles de escada/"t": acessíveis de qualquer piso.
    #                Ao sair daqui, a elevation da entidade assume o piso destino.
    # passthrough  — True: jogador no piso abaixo pode passar por trás do tile
    #                (como passar atrás de uma árvore). Não bloqueia movement do
    #                piso inferior. O piso da entidade não muda ao passar por aqui.
    elevation:      int  = 0
    is_transition:  bool = False
    passthrough:    bool = False
    no_ysort:       bool = False  # True → renderiza como terreno (abaixo de entidades, sem Y-sort)

    # light_source (21/09/2026, pedido do usuário — infraestrutura pro
    # sistema de dia/noite: objeto de mundo que emite luz própria, ex:
    # lampião/tocha/fogueira). None = objeto não emite luz (padrão —
    # imensa maioria dos objetos do catálogo).
    #
    #   (off_x, off_y, w, h, intensity_pct, radius_tiles)
    #
    #   off_x, off_y   — posição do PONTO exato da chama/lâmpada dentro do
    #                    sprite, RELATIVA ao canto superior-esquerdo do
    #                    recorte (mesmo espaço que sprite_px_w/h — NÃO é
    #                    coordenada absoluta da folha de sprites; se você
    #                    achou a posição olhando a folha inteira, subtraia
    #                    sx/sy do objeto primeiro).
    #   w, h           — tamanho do retangulozinho ao redor desse ponto
    #                    (só pra precisão visual de onde fica a chama —
    #                    o centro dele é o ponto de onde a luz se espalha,
    #                    não afeta o alcance).
    #   intensity_pct  — brilho máximo da luz, 0-100 (%). Atingido só à
    #                    noite plena — de dia a luz fica apagada/invisível
    #                    (decisão do usuário: não faz sentido ver o brilho
    #                    de uma tocha sob sol forte). Interpolação
    #                    dia→noite é responsabilidade do sistema de
    #                    render/dia-noite (fora do catálogo), não daqui.
    #   radius_tiles   — alcance da luz a partir do ponto, em TILES (mesmo
    #                    padrão de raio em tiles do resto do projeto —
    #                    AOI_RADIUS etc.).
    #
    # Propagado por discover_object_sheet_tiles()/_register_catalog_tiles()
    # (12º campo posicional da tupla de tile, opcional). Consumidor real
    # (renderização da luz/glow) ainda não implementado — este campo é só
    # o dado na fonte, mesmo padrão de is_bush/occludes_player (dado nasce
    # no catálogo, nunca inferido por sprite_name em runtime).
    light_source: tuple | None = None


# ── Terrenos não-sólidos (passáveis) ──────────────────────────────────────────
FLOOR_TILE    = TileType("Floor",      ( 50,  50,  50), is_solid=False)  # Chão genérico
GRASS_TILE    = TileType("Grass",      ( 58, 100,  48), is_solid=False,
                        # sprites=["grass_1"]
                         )  # Grama
STONE_FLOOR   = TileType("StoneFloor", ( 95,  88,  78), is_solid=False)  # Piso de pedra / cidade
SAND_TILE     = TileType("Sand",       (195, 172, 105), is_solid=False)  # Areia
DIRT_TILE     = TileType("Dirt",       (110,  82,  52), is_solid=False)  # Terra batida
CAVE_FLOOR    = TileType("CaveFloor",  ( 35,  28,  22), is_solid=False)  # Chão de caverna
CAVE_ENTRY    = TileType("CaveEntry",  ( 18,  12,   8), is_solid=False)  # Entrada de caverna
PORTAL_TILE   = TileType("Portal",     (200, 160,   0), is_solid=False)  # Portal / saída de zona

# ── Terrenos sólidos (obstáculos) ─────────────────────────────────────────────
WALL_TILE     = TileType("Wall",       (115, 108,  98), is_solid=True,  vision_height=2)  # Parede da cidade
RUINS_WALL    = TileType("RuinsWall",  ( 88,  65,  42), is_solid=True,  vision_height=2)  # Parede das ruínas
CAVE_WALL     = TileType("CaveWall",   ( 48,  38,  28), is_solid=True,  vision_height=2)  # Parede da caverna
MOUNTAIN_TILE = TileType("Mountain",   ( 62,  55,  48), is_solid=True,  vision_height=2)  # Montanha / borda
WATER_TILE    = TileType("Water",      ( 40,  88, 165), is_solid=True,  vision_height=0)  # Água (plana)
TREE_TILE = TileType("Tree", (28, 72, 28), is_solid=True, vision_height=2,
                    # sprites=["tree_1"],
                    # overlay_height=32
                     )   # sprite = 32×64px   # Árvore (bloqueia visão)
BUSH_TILE     = TileType("Bush",       ( 52, 108,  42), is_solid=True,  vision_height=1)  # Arbusto (médio)
ROCK_TILE     = TileType("Rock",       (100,  90,  78), is_solid=True,  vision_height=0)  # Pedra / lápide (rasa)
# Portão de arena (Fase G — preparo físico, WoW-style): sólido, mas vision_height=0
# (não bloqueia FOV — dá pra ver o time adversário pela "grade" antes do portão
# abrir). Aberto em runtime trocando a célula por STONE_FLOOR (ver ARENA_GATE_TILES
# em shared/constants.py) — nunca mutar este TileType em si (singleton reusado em
# toda instância de arena concorrente).
ARENA_GATE_TILE = TileType("ArenaGate", (200,  50,  50), is_solid=True,  vision_height=0)

# ── Paleta de cores para edição de mapas no Paint (PNG → CSV + JSON) ──────────
#
#  1 pixel no PNG = 1 tile no mapa.
#  Use EXATAMENTE as cores abaixo no Paint. Salve como PNG (não JPG).
#  Fundo preto (0,0,0) → montanha sólida (borda do mapa).
#
#  ═══ TERRENO — gera tiles no CSV ══════════════════════════════════════════════
#  ┌─────────────────────┬──────────────┬──────────┬──────┬────────┐
#  │ Elemento            │ Cor RGB      │ Hex      │ Char │ Sólido │
#  ├─────────────────────┼──────────────┼──────────┼──────┼────────┤
#  │ Grama               │ 0, 200, 0    │ #00C800  │  G   │  não   │
#  │ Piso de pedra       │ 200, 200, 200│ #C8C8C8  │  .   │  não   │
#  │ Areia               │ 240, 200, 60 │ #F0C83C  │  ~   │  não   │
#  │ Terra batida        │ 160, 100, 30 │ #A0641E  │  f   │  não   │
#  │ Chão genérico       │ 80, 80, 80   │ #505050  │  _   │  não   │
#  │ Entrada de caverna  │ 100, 50, 0   │ #643200  │  d   │  não   │
#  │ Portal / saída      │ 255, 0, 255  │ #FF00FF  │  O   │  não   │
#  │ Parede da cidade    │ 120, 120, 140│ #78788C  │  #   │  sim   │
#  │ Parede das ruínas   │ 180, 120, 60 │ #B4783C  │  @   │  sim   │
#  │ Parede de caverna   │ 40, 30, 20   │ #281E14  │  c   │  sim   │
#  │ Montanha            │ 60, 60, 80   │ #3C3C50  │  m   │  sim   │
#  │ Água                │ 0, 80, 220   │ #0050DC  │  W   │  sim   │
#  │ Árvore              │ 0, 100, 0    │ #006400  │  t   │  sim   │
#  │ Arbusto             │ 100, 200, 50 │ #64C832  │  b   │  sim   │
#  │ Pedra / lápide      │ 200, 180, 160│ #C8B4A0  │  k   │  sim   │
#  └─────────────────────┴──────────────┴──────────┴──────┴────────┘
#
#  ═══ ENTIDADES — gera _entities.json ════════════════════════════════════════
#  ┌──────────────────────────┬──────────────┬──────────┬─────────────────────┐
#  │ Entidade                 │ Cor RGB      │ Hex      │ Tile CSV gerado     │
#  ├──────────────────────────┼──────────────┼──────────┼─────────────────────┤
#  │ Spawn do Player          │ 255, 255, 255│ #FFFFFF  │ G (grama)           │
#  │ Mercador (loja geral)    │ 0, 220, 220  │ #00DCDC  │ . (piso de pedra)   │
#  │ Guerreiro Humanoide(mel) │ 255, 30, 30  │ #FF1E1E  │ G (grama)           │
#  │ Arqueiro Humanoide (ran) │ 255, 120, 0  │ #FF7800  │ G (grama)           │
#  │ Elemental (melee)        │ 150, 50, 255 │ #9632FF  │ G (grama)           │
#  │ Boss Guerreiro (melee)   │ 180, 0, 0    │ #B40000  │ G (grama)           │
#  └──────────────────────────┴──────────────┴──────────┴─────────────────────┘
#  Padrão no JSON: level_min=1, level_max=1, tier=normal, radius=5, count=3.
#  Edite o JSON gerado para ajustar level/tier de cada zona.

TILE_PALETTE: dict[tuple, str] = {
    (  0, 200,   0): "G",   # Grama
    (200, 200, 200): ".",   # Piso de pedra (cidade)
    (240, 200,  60): "~",   # Areia
    (160, 100,  30): "f",   # Terra batida
    ( 80,  80,  80): "_",   # Chão genérico
    (100,  50,   0): "d",   # Entrada de caverna
    (255,   0, 255): "O",   # Portal / saída de zona
    (120, 120, 140): "#",   # Parede da cidade
    (180, 120,  60): "@",   # Parede das ruínas
    ( 40,  30,  20): "c",   # Parede de caverna
    ( 60,  60,  80): "m",   # Montanha
    (  0,  80, 220): "W",   # Água
    (  0, 100,   0): "t",   # Árvore
    (100, 200,  50): "b",   # Arbusto
    (200, 180, 160): "k",   # Pedra / lápide / rocha
    (255,   0,   0): "D",   # Portão de arena (sólido, abre em runtime)
}

# ── Paleta de entidades — cor RGB → dados da entidade no JSON ─────────────────
#  "entity_type": "player" | "merchant" | "spawn"
#  "terrain": tile CSV que fica sob a entidade no mapa
ENTITY_PALETTE: dict[tuple, dict] = {
    # Especiais
    (255, 255, 255): {
        "entity_type": "player",
        "terrain": "G",
    },
    (  0, 220, 220): {
        "entity_type": "merchant",
        "shop_id": "general",
        "terrain": ".",
    },
    # Spawn zones — padrão: level 1, tier normal
    (255,  30,  30): {
        "entity_type": "spawn",
        "race": "Humanoide", "class": "Guerreiro",
        "enemy_type": "melee", "tier": "normal",
        "terrain": "G",
    },
    (255, 120,   0): {
        "entity_type": "spawn",
        "race": "Humanoide", "class": "Arqueiro",
        "enemy_type": "ranged", "tier": "normal",
        "terrain": "G",
    },
    (150,  50, 255): {
        "entity_type": "spawn",
        "race": "Elemental", "class": "Elemental",
        "enemy_type": "melee", "tier": "normal",
        "terrain": "G",
    },
    (180,   0,   0): {
        "entity_type": "spawn",
        "race": "Humanoide", "class": "Guerreiro",
        "enemy_type": "melee", "tier": "boss",
        "terrain": "G",
    },
}

# ── Paleta para o PNG de entidades (*_entities.png) ─────────────────────────
#
#  Cores RGB saturadas, fáceis de distinguir em qualquer editor de pixel.
#  Preto (0,0,0) = célula vazia — ignorado.
#  Correspondência EXATA (sem tolerância), ao contrário dos tiles.
#
#  ═══ ESPECIAIS ══════════════════════════════════════════════════════════════
#  ┌──────────────────────────┬──────────────┬──────────┐
#  │ Entidade                 │ Cor RGB      │ Hex      │
#  ├──────────────────────────┼──────────────┼──────────┤
#  │ Spawn do Player          │ 255, 255, 255│ #FFFFFF  │
#  │ Mercador (loja geral)    │   0, 220, 220│ #00DCDC  │
#  │ Transition (placeholder) │ 220, 100, 255│ #DC64FF  │
#  └──────────────────────────┴──────────────┴──────────┘
#
#  ═══ MOBS (spawn zones) ══════════════════════════════════════════════════════
#  ┌──────────────┬──────────────┬──────────┐
#  │ Mob          │ Cor RGB      │ Hex      │
#  ├──────────────┼──────────────┼──────────┤
#  │ Aranha       │ 200,  80, 220│ #C850DC  │
#  │ Rato         │ 220, 180,  60│ #DCB43C  │
#  │ Escorpião    │ 220, 200,   0│ #DCC800  │
#  │ Cobra        │  60, 200,  60│ #3CC83C  │
#  │ Lobo         │ 160, 160, 220│ #A0A0DC  │
#  │ Urso         │ 200,  90,  20│ #C85A14  │
#  │ Goblin       │ 100, 220,  40│ #64DC28  │
#  │ Zumbi        │ 120, 200, 120│ #78C878  │
#  │ Orc          │  40, 180,  80│ #28B450  │
#  │ Troll        │  40, 200, 160│ #28C8A0  │
#  │ Elfo         │ 100, 240, 180│ #64F0B4  │
#  │ Minotauro    │ 220, 100,  40│ #DC6428  │
#  │ Vampiro      │ 160,  20, 220│ #A014DC  │
#  │ Dragão       │ 240,  20,  20│ #F01414  │
#  └──────────────┴──────────────┴──────────┘
#  Padrão no JSON: radius=5, cooldown=60, level 1-1, count=3, tier=normal.
#  Edite o JSON gerado para ajustar esses valores por zona.

ENTITY_PNG_PALETTE: dict[tuple, dict] = {
    # Especiais
    (255, 255, 255): {"entity_type": "player"},
    (  0, 220, 220): {"entity_type": "merchant", "shop_id": "general"},
    (220, 100, 255): {"entity_type": "transition"},
    # Feras
    (200,  80, 220): {"entity_type": "spawn", "mob": "Aranha"},
    (220, 180,  60): {"entity_type": "spawn", "mob": "Rato"},
    (220, 200,   0): {"entity_type": "spawn", "mob": "Escorpião"},
    ( 60, 200,  60): {"entity_type": "spawn", "mob": "Cobra"},
    (160, 160, 220): {"entity_type": "spawn", "mob": "Lobo"},
    (200,  90,  20): {"entity_type": "spawn", "mob": "Urso"},
    # Humanoides / Outros
    (100, 220,  40): {"entity_type": "spawn", "mob": "Goblin"},
    (120, 200, 120): {"entity_type": "spawn", "mob": "Zumbi"},
    ( 40, 180,  80): {"entity_type": "spawn", "mob": "Orc"},
    ( 40, 200, 160): {"entity_type": "spawn", "mob": "Troll"},
    (100, 240, 180): {"entity_type": "spawn", "mob": "Elfo"},
    (220, 100,  40): {"entity_type": "spawn", "mob": "Minotauro"},
    (160,  20, 220): {"entity_type": "spawn", "mob": "Vampiro"},
    (240,  20,  20): {"entity_type": "spawn", "mob": "Dragão"},
}

# ── Mapeamento de caracteres CSV → TileType (apenas terreno) ─────────────────
TILE_MAPPING = {
    # Passáveis
    "_": FLOOR_TILE,
    "G": GRASS_TILE,
    ".": STONE_FLOOR,
    "~": SAND_TILE,
    "f": DIRT_TILE,
    "d": CAVE_ENTRY,
    "O": PORTAL_TILE,
    # Sólidos
    "#": WALL_TILE,
    "@": RUINS_WALL,
    "c": CAVE_WALL,
    "m": MOUNTAIN_TILE,
    "W": WATER_TILE,
    "D": ARENA_GATE_TILE,
    # Spawn chars (renderizam como floor subjacente)
    "P": STONE_FLOOR,   # Player spawn → piso de pedra (cidade)
    "N": STONE_FLOOR,   # Merchant spawn
    "E": GRASS_TILE,
    "R": GRASS_TILE,
    "A": GRASS_TILE,
    "T": GRASS_TILE,
    "M": GRASS_TILE,
    "X": GRASS_TILE,
    "B": GRASS_TILE,
}

# ── Objetos sobre o terreno — layer separada ──────────────────────────────────
# Chars que representam objetos visuais colocados sobre o terreno.
# Eles ficam em object_matrix, nunca em terrain_matrix.
OBJECT_CHARS = {"t", "b", "k"}

# Terreno padrão que fica sob cada objeto (grama para a maioria).
# Usado no split automático de mapas legados com uma única camada.
OBJECT_UNDERLYING: dict[str, str] = {
    "t": "G",   # árvore sobre grama
    "b": "G",   # arbusto sobre grama
    "k": "G",   # pedra sobre grama
}

# Mapeamento objeto char → TileType para renderização no pass 2.
OBJECT_MAPPING: dict[str, "TileType"] = {
    "t": TREE_TILE,
    "b": BUSH_TILE,
    "k": ROCK_TILE,
}

# Tamanho global do tile em pixels
TILE_SIZE = 32


def _read_png_size(path: str) -> tuple[int, int]:
    """Lê largura × altura de um PNG pelo cabeçalho IHDR (sem pygame)."""
    import struct
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if len(header) >= 24 and header[12:16] == b"IHDR":
            return struct.unpack(">II", header[16:24])
    except Exception:
        pass
    return 32, 32


def _rect_to_tile_offsets(rect: "tuple | str | None", sprite_px_w: int,
                          sprite_px_h: int) -> list[tuple[int, int]]:
    """Geometria compartilhada entre `get_collision_offsets` e
    `get_vision_offsets` (13/08/2026) — converte um rect no MESMO
    formato de `collision_rect` (None/"full"/(x,y,w,h)) em offsets de
    tile relativos ao tile base. Extraído pra não duplicar a mesma
    matemática nas 2 pegadas (colisão e visão), que agora podem
    divergir (bush/árvore precisam de pegadas diferentes pra cada uma —
    ver `TileType.vision_rect`).

    `"base_row"` (13/08/2026, pedido do usuário — §51) é um sentinel
    NOVO, só pra pegada de VISÃO: largura inteira do sprite, só a
    fileira de baixo (32px) — diferente do `"base"` já usado em
    `collision_rect`/`_resolve_collision`, que ignora a largura e conta
    só o tile-âncora. Existe pra bush: contar o sprite inteiro (`"full"`)
    inflava a pegada com a "metade de cima" do desenho, fazendo bushes
    com espaço de grama visível entre si se fundirem num blob só —
    `"base_row"` deixa só a base (onde o tufo realmente encosta no
    chão) contar pra adjacência."""
    if rect is None:
        return [(0, 0)]

    sprite_h = sprite_px_h if sprite_px_h > 0 else TILE_SIZE
    sprite_w = sprite_px_w if sprite_px_w > 0 else TILE_SIZE

    if rect == "full":
        rx, ry, rw, rh = 0, 0, sprite_w, sprite_h
    elif rect == "base_row":
        rx, ry, rw, rh = 0, sprite_h - TILE_SIZE, sprite_w, TILE_SIZE
    else:
        rx, ry, rw, rh = rect

    # Posição do tile base dentro do sprite (canto sup-esq do tile base)
    base_y_in_sprite = sprite_h - TILE_SIZE   # ex: 64-32 = 32 para sprite 32×64
    base_x_in_sprite = 0

    dy_min = math.floor((ry       - base_y_in_sprite) / TILE_SIZE)
    dy_max = math.floor((ry + rh - 1 - base_y_in_sprite) / TILE_SIZE)
    dx_min = math.floor((rx       - base_x_in_sprite) / TILE_SIZE)
    dx_max = math.floor((rx + rw - 1 - base_x_in_sprite) / TILE_SIZE)

    return [
        (dx, dy)
        for dy in range(dy_min, dy_max + 1)
        for dx in range(dx_min, dx_max + 1)
    ] or [(0, 0)]


def get_collision_offsets(tile: TileType) -> list[tuple[int, int]]:
    """
    Retorna lista de (dx, dy) em tiles relativos ao tile base que devem ser sólidos.
    (0, 0) = tile base (sempre incluído se is_solid=True).
    dy negativo = tile acima do base; dx positivo = tile à direita.

    Baseado em tile.collision_rect:
      None   → só o tile base [(0, 0)]
      "full" → sprite inteiro calculado com sprite_px_w / sprite_px_h
      (x, y, w, h) → retângulo em sprite-space pixels, (0,0) = canto sup-esq do PNG
    """
    return _rect_to_tile_offsets(tile.collision_rect, tile.sprite_px_w, tile.sprite_px_h)


def get_vision_offsets(tile: TileType) -> list[tuple[int, int]]:
    """
    Mesma ideia de `get_collision_offsets`, mas pra bloqueio de VISÃO
    (13/08/2026) — baseado em `tile.vision_rect`, independente de
    `collision_rect`/`is_solid`. `None` (default) → só o tile-âncora.
    Usado por `engine.entity_factory.create_tilemap` pra decidir quais
    tiles ao redor do objeto ganham `vision_height` alto, mesmo que o
    objeto continue andável (bush) ou tenha uma pegada de COLISÃO menor
    que a de visão (árvore — tronco sólido, copa bloqueia visão)."""
    return _rect_to_tile_offsets(tile.vision_rect, tile.sprite_px_w, tile.sprite_px_h)


def _discover_sprite_objects(
    prefix: str,
    base_name: str,
    base_color: tuple,
    is_solid: bool = True,
    collision_rect: tuple | str | None = None,
    vision_height: int = 0,
) -> None:
    """
    Escaneia assets/tiles/ por <prefix>1.png, <prefix>2.png, ... (sem limite).
    Para ao encontrar a primeira lacuna na sequência.
    """
    import os
    tiles_dir = os.path.join("assets", "tiles")
    if not os.path.exists(tiles_dir):
        return
    i = 1
    while True:
        name = f"{prefix}{i}"
        path = os.path.join(tiles_dir, name + ".png")
        if not os.path.exists(path):
            break
        w, h = _read_png_size(path)
        char = name  # "t1", "b1", "g1", etc.
        tile = TileType(
            name=f"{base_name}_{i}",
            color=base_color,
            is_solid=is_solid,
            sprite_name=name,
            sprite_px_w=w,
            sprite_px_h=h,
            collision_rect=collision_rect,
            vision_height=vision_height,
        )
        OBJECT_CHARS.add(char)
        OBJECT_MAPPING[char] = tile
        OBJECT_UNDERLYING[char] = "G"
        i += 1


# ── Registro central de famílias de sprites de objetos ───────────────────────
#
# Para adicionar um novo elemento visual basta incluir uma entrada aqui.
# O sistema auto-descobre prefix1.png, prefix2.png ... em assets/tiles/.
#
# Campos:
#   prefixo        — "g" → g1.png, g2.png ...
#   nome-interno   — prefixo do TileType.name
#   cor RGB        — fallback quando o PNG não carrega
#   is_solid       — True bloqueia passagem
#   label God Mode — nome exibido na paleta do editor
#   collision_rect — área sólida:
#                      None    → só o tile base 32×32 inferior (padrão)
#                      "full"  → sprite inteiro (calculado pela altura do PNG)
#                      (x,y,w,h) → rect explícito em pixels do sprite
#
SPRITE_FAMILIES: list[tuple] = [
    # prefixo  nome-interno       cor RGB          sólido  label              collision_rect   vision_height
    ("t",  "SpriteTree",        ( 28,  72,  28),  True,  "Árvore",           None,             2),  # alta — bloqueia FOV
    ("b",  "SpriteBush",        ( 52, 108,  42),  True,  "Arbusto",          None,             1),  # média — não bloqueia
    ("g",  "SpriteGrade",       ( 80, 120,  60),  True,  "Grade",            None,             1),  # média — não bloqueia
    ("s",  "SpriteGrave",       (128, 128, 128),  True,  "Grave",            "full",           1),  # média — não bloqueia
    ("y",  "SpriteGravesHori",  (128, 128, 128),  True,  "GraveHorizontal",  (0, 32, 64, 32),  0),  # rasa  — não bloqueia
    ("l",  "SpriteGravestone",  (128, 128, 128),  True,  "GraveStone",       None,             1),  # média — não bloqueia
    ("o",  "SpriteObj",         (128, 128, 128),  True,  "Bench",            "full",           1),  # média — não bloqueia
]


def discover_sprite_objects() -> None:
    """Registra todos os sprites de objetos encontrados em assets/tiles/."""
    for prefix, base_name, color, is_solid, _label, collision_rect, vision_height in SPRITE_FAMILIES:
        _discover_sprite_objects(prefix, base_name, color, is_solid, collision_rect, vision_height)


# ── Tilesheet — múltiplos tiles em um único PNG ───────────────────────────────
#
# Cada entrada define um PNG de sheet e gera tiles automáticos para cada célula.
# Os tiles são registrados na camada de OBJETO (não terrain), não bloqueiam a
# passagem por padrão, e aparecem no God Mode no tab "Folhas".
#
# Campos:
#   file     — nome do PNG sem extensão, em assets/tiles/
#   prefix   — prefixo curto para IDs dos tiles (ex: "txg" → "txg_0_0", "txg_1_0" ...)
#   tile_w   — largura de cada tile em pixels
#   tile_h   — altura de cada tile em pixels
#   is_solid — True bloqueia passagem (padrão False para terreno decorativo)
#   label    — nome exibido no tab Folhas do God Mode
#   color    — cor de fallback RGB
#   underlying_terrain — char de terreno que fica sob o tile (padrão "G")
#
SHEET_FAMILIES: list[dict] = [
    {
        "file":                "TX Tileset Terrain",
        "prefix":              "ter",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Chão",
        "color":               (58, 100, 48),   # fallback — só usado se nenhuma zona/override bater
        # "zones" (26-27/08/2026, pedido do usuário) — catálogo por
        # CATEGORIA de terreno dentro do arquivo (grade 14×16 de 32px).
        # Fronteiras medidas por PIXEL REAL (script 1x, script sobre
        # `assets/tiles/TX Tileset Terrain_Explanation.png` procurando os
        # segmentos de linha vermelha da anotação do usuário e convertendo
        # pra múltiplo de 32px) — corrigidas a partir da 1ª leitura visual
        # aproximada (ver PROBLEMAS_ARQUITETURA.md §81, "Correção").
        # `terra_pura`/`terra_flor` sem gap: a leitura por pixel achou
        # linha 15 dentro de `terra_pura` (não parava em 14) e `terra_flor`
        # começando na col 5 (não 4 — col 4 é área sem categoria/gap real
        # na imagem, fica fora de qualquer zona de propósito).
        #
        # `transicao_terra_grama`/`transicao_grama_terra` NÃO são mais uma
        # lista solta de células — são 2 quebra-cabeças de 3×3 peças
        # interligadas ("mancha" arredondada, "cruz"), confirmado
        # recortando as 9 células de cada um a partir do PNG original (não
        # a imagem anotada): canto = grama cobrindo ~3/4 do tile com corte
        # diagonal, borda-meio = metade grama/metade terra, centro = cor
        # sólida. Tratar como 18 tiles soltos pra sortear 1 por vizinho
        # (1ª versão desta feature) produzia um mosaico sem nexo — bug
        # relatado pelo usuário, ver PROBLEMAS_ARQUITETURA.md §81
        # "Correção". `shapes` guarda só a ORIGEM (canto superior-esquerdo)
        # de cada quebra-cabeça; a peça certa por vizinho é resolvida por
        # `resolve_blob3x3_piece()` (autotile posicional, não sorteio).
        "zones": {
            "transicao_terra_grama": {
                "purpose": "transition", "from": "terra", "to": "grama",
                "shapes": {"mancha": (0, 0), "cruz": (3, 0)},
            },
            "transicao_grama_terra": {
                "purpose": "transition", "from": "grama", "to": "terra",
                "shapes": {"mancha": (0, 3), "cruz": (3, 3)},
                # Achado por medição de pixel real (27/08/2026, usuário
                # reportou "sem transição nenhuma, nem dentro nem fora"
                # numa curva de caminho de terra sobre grama) — as 4
                # peças de CANTO (2 lados adjacentes) desta zona têm
                # presença fraca da cor nova (terra) em AMBOS os
                # estilos (mancha E cruz): fração de terra no lado
                # "não-de-fora" só ~0.3-0.5 (nunca perto de 1.0), contra
                # ~1.0 na zona irmã `transicao_terra_grama`. Não é bug
                # de cálculo — é o próprio desenho do PNG (o "buraco de
                # terra" é menor/mais arredondado que a "ilha de
                # grama"). Marca pra `_apply_smart_terrain` preferir 1
                # peça de BORDA (sempre forte/nítida nos 2 estilos,
                # confirmado) no lugar da peça de canto dedicada nesses
                # casos — ver PROBLEMAS_ARQUITETURA.md §81 "Correção 4".
                "weak_corners": True,
            },
            # Recatalogado 02/09/2026 — usuário substituiu o PNG (sheet
            # "TX Tileset Terrain" ganhou mais variações de grama, layout
            # mudou de posição). Área de grama agora é c6-13/r0-7 (8×8=64
            # células, confirmado pelo usuário). Split flor/pura
            # confirmado por inspeção visual (crop ampliado, célula por
            # célula) + decisão do usuário: `grama_flor` = só onde tem
            # flor visível (c6-9,r0-3, mesma posição de antes, coincidência);
            # `grama_pura` junta textura-de-folhinha-sem-flor (c10-13,r0-3)
            # + grama 100% lisa sem textura (c6-13,r4-7, inclui pedras
            # espalhadas nas linhas 4-5 — usuário confirmou incluir no
            # mesmo pool, sem zona separada).
            "grama_flor": {
                "purpose": "terrain_variant", "terrain": "grama",
                "cells": [(c, r) for c in range(6, 10) for r in range(0, 4)],
            },
            "grama_pura": {
                "purpose": "terrain", "terrain": "grama",
                "cells": ([(c, r) for c in range(10, 14) for r in range(0, 4)]
                         + [(c, r) for c in range(6, 14) for r in range(4, 8)]),
            },
            "piso_sobre_terra": {
                "purpose": "floor_full", "base": "terra",
                # Recatalogado 02/09/2026 (usuário moveu a posição no PNG,
                # mesma leva da grama) — agora c8-13/r8-12. Abaixo disso
                # (mais linhas) também é transição grama-terra, mas
                # colocada manualmente pelo usuário — fora do catálogo de
                # propósito, o pincel/`_apply_smart_terrain` nunca deve
                # usar essas células.
                "cells": [(c, r) for c in range(8, 14) for r in range(8, 13)],
            },
            "piso_sobre_grama": {
                "purpose": "floor_full", "base": "grama",
                "cells": [(c, r) for c in range(0, 2) for r in range(6, 11)],
            },
            "terra_pura": {
                "purpose": "terrain", "terrain": "terra",
                "cells": [(c, r) for c in range(0, 4) for r in range(12, 16)],
            },
            "terra_flor": {
                "purpose": "terrain_variant", "terrain": "terra",
                "cells": [(c, r) for c in range(5, 8) for r in range(13, 16)],
            },
        },
        # color_overrides DERIVADO das zonas (ponto único de verdade —
        # nunca diverge) + a mesma divisão por FAIXA DE LINHA de antes
        # (§78) como fallback pras células fora de qualquer zona nomeada
        # (piso parcial/customizável, ainda sem cor própria).
        "color_overrides": {
            **{(c, r): GRASS_TILE.color  for c in range(14) for r in range(0, 4)},
            **{(c, r): STONE_FLOOR.color for c in range(14) for r in range(4, 12)},
            **{(c, r): DIRT_TILE.color   for c in range(14) for r in range(12, 16)},
            **{cell: GRASS_TILE.color  for cell in [(c, r) for c in range(0, 3) for r in range(0, 3)]
                                                   + [(c, r) for c in range(3, 6) for r in range(0, 3)]},
            **{cell: DIRT_TILE.color   for cell in [(c, r) for c in range(0, 3) for r in range(3, 6)]
                                                   + [(c, r) for c in range(3, 6) for r in range(3, 6)]},
            **{cell: STONE_FLOOR.color for cell in [(c, r) for c in range(6, 8) for r in range(4, 7)]},
        },
        "underlying_terrain":  "G",
    },
        {
        # Cadastro inicial (13/09/2026, pedido do usuário) — mesmo padrão
        # mínimo de "TX Tileset Woodfloor"/"TX Tileset Cave" (sem "zones":
        # a categorização fina por pixel real, como a de "TX Tileset
        # Terrain", só faz sentido pro pincel inteligente de terreno, que
        # está PAUSADO por decisão do usuário — ver
        # `project_terrain_brush_paused` na memória; não reabrir sem
        # pedido). Cor de fallback amostrada por script direto do PNG
        # (média de pixels não-transparentes), não chutada à mão.
        "file":                "TX Tileset Terrain Beach",
        "prefix":              "bch",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Praia",
        "color":               (134, 144, 106),
        "underlying_terrain":  "G",
    },
        {
        "file":                "TX Tileset Woodfloor",
        "prefix":              "twf",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Assoalho TX",
        "color":               (120, 92, 62),
        "underlying_terrain":  ".",
    },
        {
        "file":                "TX Tileset Cave",
        "prefix":              "txc",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Caverna TX",
        "color":               ( 47, 46, 57),
        "underlying_terrain":  ".",
    },
]


def _derive_terrain_zone_colors(fam: dict) -> dict:
    """Deriva cor de minimapa A PARTIR das próprias `zones` do family
    (02/09/2026) — o `color_overrides` de "TX Tileset Terrain" dizia no
    comentário ser "DERIVADO das zonas (ponto único de verdade — nunca
    diverge)", mas na prática era escrito à mão; ficava desatualizado
    toda vez que o usuário recatalogava o PNG (achado durante o
    recatálogo de 02/09/2026 — grama nova em c6-13/r4-7 caía no
    fallback de faixa "stone", mostrando cor de pedra pra tile de
    grama). Mapeia zona→cor pelo `purpose`: `floor_full` sempre
    `STONE_FLOOR`; `terrain`/`terrain_variant` pelo campo `terrain`
    (hoje só "grama"/"terra"). Zona sem `cells` (transição — usa
    `shapes`, não uma lista de células) não gera override nenhum, fica
    com o que já estava no `color_overrides` hand-written (fallback de
    faixa de linha, ou override específico antigo da área de
    transição — nenhum dos dois mexido aqui)."""
    terrain_color = {"grama": GRASS_TILE.color, "terra": DIRT_TILE.color}
    derived = {}
    for zone in fam.get("zones", {}).values():
        cells = zone.get("cells")
        if not cells:
            continue
        if zone.get("purpose") == "floor_full":
            color = STONE_FLOOR.color
        else:
            color = terrain_color.get(zone.get("terrain"))
        if color is None:
            continue
        for cell in cells:
            derived[cell] = color
    return derived


for _fam in SHEET_FAMILIES:
    if _fam.get("zones"):
        _fam["color_overrides"] = {**_fam.get("color_overrides", {}),
                                   **_derive_terrain_zone_colors(_fam)}
del _fam

# Mapeamento id_tile → (sheet_file, sx, sy, tile_w, tile_h) — lido pelo TileSpriteManager
SHEET_TILE_MAP: dict[str, tuple] = {}


def discover_sheet_tiles() -> None:
    """
    Para cada entrada em SHEET_FAMILIES, lê as dimensões do PNG e registra
    um TileType por célula em OBJECT_MAPPING / OBJECT_CHARS / SHEET_TILE_MAP.

    `color_overrides: {(col,row): (r,g,b)}` (26/08/2026, pedido do
    usuário) escolhe a cor por célula — mirror exato de
    `collision_overrides` (discover_object_sheet_tiles) — caindo no
    `color` da família quando a célula não está no dict. Existe porque um
    único arquivo de terreno (ex: "TX Tileset Terrain") pode misturar
    grama/terra/calçamento na mesma imagem — sem isso, TODAS as células
    do arquivo usavam a mesma cor no minimapa, mesmo sendo visualmente
    bem diferentes (bug real relatado pelo usuário)."""
    import os
    for fam in SHEET_FAMILIES:
        sheet_file = fam["file"]
        path = os.path.join("assets", "tiles", sheet_file + ".png")
        if not os.path.exists(path):
            continue
        sw, sh_px = _read_png_size(path)
        cols = sw  // fam["tile_w"]
        rows = sh_px // fam["tile_h"]
        prefix    = fam["prefix"]
        is_solid  = fam.get("is_solid", False)
        color     = fam["color"]
        color_overrides = fam.get("color_overrides", {})
        under     = fam.get("underlying_terrain", "G")
        tw, th    = fam["tile_w"], fam["tile_h"]
        for row in range(rows):
            for col in range(cols):
                tile_id = f"{prefix}_{col}_{row}"
                sx = col * tw
                sy = row * th
                tile = TileType(
                    name=f"Sheet_{prefix}_{col}_{row}",
                    color=color_overrides.get((col, row), color),
                    is_solid=is_solid,
                    sprite_name=tile_id,
                    sprite_px_w=tw,
                    sprite_px_h=th,
                )
                SHEET_TILE_MAP[tile_id] = (sheet_file, sx, sy, tw, th)
                OBJECT_CHARS.add(tile_id)
                OBJECT_MAPPING[tile_id] = tile
                OBJECT_UNDERLYING[tile_id] = under


# ── Object Sheets — sheets de múltiplos objetos em um único PNG ──────────────
#
# Existem dois modos de catalogar os tiles de um sheet:
#
# MODO GRADE (tile_w + tile_h)
#   Todos os tiles têm o mesmo tamanho. O sistema auto-descobre quantas colunas
#   e linhas existem lendo as dimensões do PNG.
#   IDs gerados: {prefix}_{col}_{row}  ex: gc_0_0, gc_1_0 ...
#
#   "tile_w": 32, "tile_h": 64,          — tamanho uniforme de cada célula
#   "default_collision": None,           — colisão padrão (None=base, "full"=inteiro)
#   "collision_overrides": {             — sobrescreve por (col, row)
#       (0, 0): "full",
#       (1, 2): None,
#   },
#
# MODO CATÁLOGO (tiles=[...])
#   Cada tile tem posição e tamanho próprios no PNG. Use quando os objetos têm
#   tamanhos variados (ex: 32x32 misturado com 32x64 no mesmo arquivo).
#   IDs gerados: {prefix}_{id}  ex: pr_banco, pr_lampiao ...
#
#   "tiles": [
#       # (id,        sx,  sy,   w,   h,   collision_rect,  vision_height)
#       ("banco",      0,   0,  64,  32,   None,            0),  # raso, não bloqueia FOV
#       ("lampiao",   64,   0,  32,  64,   "full",          2),  # alto, bloqueia FOV
#       ("caixao",    96,   0,  32,  64,   None,            1),  # médio, não bloqueia
#   ],
#   O 7º campo (vision_height) é opcional — omita para usar default_vision_height.
#   Campos posicionais adicionais (piso, transpassável, sort, vision_rect,
#   vision_h, light) — ver docstring completa de _register_catalog_tiles.
#
# Campos comuns:
#   file                 — nome do PNG sem extensão, em assets/tiles/
#   prefix               — prefixo curto para IDs internos
#   label                — nome exibido no God Mode (aba Objetos)
#   color                — cor RGB de fallback
#   underlying_terrain   — char de terreno subjacente (padrão "G")
#   default_vision_height — vision_height padrão para tiles sem override (padrão 0)
#
# Para collision_overrides (modo grade), cada entrada pode ser:
#   (col, row): collision_rect                        — usa default_vision_height
#   (col, row): (collision_rect, vision_height)       — vision_height explícito por tile
#
# Valores de collision_rect:
#   None          → SEM colisão (tile passável)
#   "base"        → colisão só no tile base 32×32 inferior  (antigo comportamento de None)
#   "full"        → colisão no sprite inteiro
#   (x, y, w, h) → retângulo explícito em pixels
#
#     ex: (0, 1): ("full", 2)   → grade alta, sólida, bloqueia FOV
#         (0, 0): (None,   0)   → sem colisão, sem bloqueio de FOV
#
OBJECT_SHEET_FAMILIES: list[dict] = [
    {
        "file":                "TX Grades2",
        "prefix":              "gcn",
        "tile_w":              32,
        "tile_h":              64,
        "label":               "Grade Cemitério New",
        "color":               (90, 80, 70),
        "default_collision":   "full",
         "underlying_terrain": "G",
         "tiles": [
             # (id,          sx,  sy,   w,   h,   collision_rect)
             ("1",            0,   0,  32,  96,           "base"),
             ("2",           32,   0,  32,  96,           "base"),
             ("2b",          64,   0,  32,  96,           "base"),             
             ("3",           96,   0,  32,  96,           "base"), 
             ("3b",         128,   0,  32,  96,           "base"),              
             ("4",          160,   0,  32,  96,           "base"),  
             ("5",          192,   0,  32,  96,           "base"),  
             ("6",          224,   0,  32,  96,           "base"),  
             ("7",            0,  96,  32,  32,           "full"), 
             ("8",            0, 128,  32,  64,           "full"),
             ("9",           32,  96,  32,  96,           "base"), 
             ("10",          64,  96,  32,  96,           "base"),
             ("11",          96,  96,  32,  96,           "full"),
             ("12",         128,  96,  32,  96,           "base"),
             ("13",         224,  96,  32,  32,           "full"),
             ("14",         224, 128,  32,  96,           "full"),
             ("15",           0, 192,  32,  96,           "base"),
             ("16",          32, 192,  32,  96,           "base"),
             ("17",          64, 192,  32,  96,           "full"),
             ("18",          96, 192,  32,  96,           "full"),
             ("19",         160, 160,  64, 128,  (0, 96, 64, 32)),                                                    

         ]
    },    
    
#     {
#         "file":   "TX Props",
#         "prefix": "pr",
#         "label":  "Adereços",
#         "color":  (90, 80, 70),
#         "underlying_terrain": "G",
#         "tiles": [
#             # (id,              sx,  sy,   w,   h,   collision_rect)
#             ("chest",           96,   0,  32,  64,           "base"),
#             ("box1",           160,   0,  32,  64,           "base"),
#             ("box2",           160,  64,  32,  64,           "base"),
#             ("barrel",         160, 128,  32,  64,           "base"),
#             ("urn",            160, 192,  32,  64,           "base"),
#             ("pot",            160, 258,  32,  64,           "base"),
#             ("urn2",           160, 320,  32,  64,           "base"),
#             ("soulstone",      224,   0,  32,  64,           "base"),
#             ("soulstone2",     224,  64,  32,  96,           "base"),
#             ("soulstone3",     224, 160,  32,  64,           "base"),
#             ("gravestone",     224, 224,  32,  64,           "base"),
#             ("cross",          224, 288,  32,  64,           "base"),
#             ("bench1",         288,   0,  64,  64, ( 0, 32, 64, 32)),
#             ("tomb1",          288,  64,  64,  64, ( 0, 32, 64, 32)),
#             ("tomb2",          288, 128,  32,  96, ( 0, 32, 32, 64)),
#             ("gravestone2",    288, 224,  32,  64,           "base"),
#             ("cube",           288, 288,  32,  64,           "base"),
#             ("opentomb",       288, 384,  64,  32,           "full"),
#             ("opentomb2",      288, 416,  64,  32, ( 0,  0, 32, 32)),
#             ("sheets1",         32, 256,  64,  64, ( 0, 32, 64, 32)),
#             ("bench2",         384,   0,  32,  64,           "full"),
#             ("bench3",         384,  96,  32,  64,           "full"),
#             ("pillar1",        352, 160,  32,  96,           "base"),
#             ("pillar2",        416, 192,  32,  64,           "base"),
#             ("statue",         416,   0,  96,  96, (32, 64, 32, 32)),
#             ("lighting",       448,  96,  32,  64,           "base"),
#             ("pit",            416, 352,  64,  64,           "full"),
#             # Pedras grandes (bigrock/rock3-6) ganharam bloqueio de visão em
#             # 13/08/2026, mesma decisão de árvore/bush acima — rock1/rock2
#             # (baixas, já sem colisão) ficam intocadas.
#             ("bigrock",          0, 416,  64,  64,           "full", 0, False, 1, "full", 2),
#             ("rock1",            0, 480,  32,  32,             None),
#             ("rock2",           32, 480,  32,  32,             None),
#             ("rock3",           64, 480,  32,  32,           "full", 0, False, 1, "full", 2),
#             ("rock4",           96, 480,  32,  32,           "full", 0, False, 1, "full", 2),
#             ("rock5",          128, 480,  32,  32,           "full", 0, False, 1, "full", 2),
#             ("rock6",          160, 480,  32,  32,           "full", 0, False, 1, "full", 2),
#             ("closedoor",        0,  96,  96,  64,           "full"),
#             ("opendoor",         0,  160, 96,  64,             None),
#             ("artmister",       32,  320, 32,  32,           "full"),
#             ("pa",              64,  320, 32,  64,           "base"),
#             ("lampiao",         96,  320, 32,  32,           "base"),
#           #  ("lampiao",         96,  320, 32,  32,           "base"),
#
#         ],
#     },

#     {
#         "file":   "TX Plant",
#         "prefix": "pl",
#         "label":  "Vegetação",
#         "color":  (96, 99, 21),
#         "underlying_terrain": "G",
#         "tiles": [
#             # (id,              sx,  sy,   w,   h,   collision_rect)  
#             ("tree1",             0,   0, 160, 160,   ( 64, 128, 32,32)),
#             ("tree2",           160,   0,  96, 160,   ( 32, 128, 32,32)),
#             ("tree3",           256,   0,  96, 160,   ( 32, 128, 32,32)),
#             ("bush1",             0, 192,  32,  32,   (  0,   0, 32,32)),
#             ("bush2",            32, 192,   32, 32,   (  0,   0, 32,32)),
#             ("bush3",            64, 160,  64, 64,    ( 32,  32, 32,32)),
#             ("bush4",           128, 160,  64,  64,   ( 32,  32, 32,32)),
#             ("bush5",           192, 160,  64,  64,   ( 32,  32, 32,32)),
#             ("bush6",           256, 160,  64,  64,   (  0,  32, 32,32)),
#             ("grassblade1",       0, 224,  32,  32,                None),
#             ("grassblade2",      32, 224,  32,  32,                None),
#             ("grassblade3",      64, 224,  32,  32,                None),
#             ("grassblade4",      96, 224,  32,  32,                None),
#             ("grassblade5",       0, 256,  32,  32,                None),
#             ("grassblade6",      32, 256,  32,  32,                None),
#             ("grassblade7",      64, 256,  32,  32,                None),
#             ("grassblade8",      96, 256,  32,  32,                None),
#             ("grassblade9",       0, 288,  32,  32,                None),
#             ("grassblade10",     32, 288,  32,  32,                None),
#             ("grassblade11",     64, 288,  32,  32,                None),
#             ("grassblade12",     96, 288,  32,  32,                None),
#             ("grassblade13",      0, 322,  32,  32,                None),
#             ("grassblade14",     32, 322,  32,  32,                None),
#             ("grassblade15",     64, 322,  32,  32,                None),
#             ("grassblade16",     96, 322,  32,  32,                None),
#             ("vomito",          128, 224,  32,  32,                None),             
#             
#         ]
#
#     },
    {
             "file":   "TX Village Plant",
             "underlying_terrain": "G",
             # "groups" (26/08/2026, pedido do usuário — prova de conceito
             # real do mecanismo novo, ver PROBLEMAS_ARQUITETURA.md §78):
             # este arquivo já misturava 8 categorias BEM diferentes
             # visualmente (árvore verde/outono, arbusto, trigo dourado,
             # repolho, abóbora, cenoura, grama fina, flor) — todas com a
             # MESMA cor `(96, 99, 21)` antes, então nenhuma aparecia
             # certo no minimapa. `prefix` continua "pl" em TODO grupo
             # (nunca mudar) — mapas reais (`maps/map_1_objects.csv`,
             # `maps/moba_battleground_objects.csv`) já referenciam os
             # ids "pl_t1", "pl_b3" etc.; um prefixo diferente por grupo
             # quebraria essas referências. Cores por inspeção visual do
             # PNG (1024×1024) — árvore tem variante verde E outono
             # misturada no mesmo grupo (cor é uma média aproximada, não
             # pixel-perfect); repolho/cenoura são sprites pequenos
             # (32×32), cor mais incerta que os outros grupos.
             "groups": {
                 "arvores": {
                     "prefix": "pl",
                     "label":  "Árvores",
                     "color":  (46, 92, 40),
                     # occludes_player (20/09/2026, pedido do usuário) —
                     # árvore fica 50% transparente quando o jogador está
                     # atrás dela na tela. Ver TileType.occludes_player.
                     "occludes_player": True,
                     "tiles": [
                         # (id, sx, sy, w, h, collision_rect, piso, transpassavel, sort, vision_rect, vision_h)
                         # Árvore/bush ganharam bloqueio de visão em 13/08/2026
                         # (pedido do usuário — antes NENHUM objeto decorativo
                         # bloqueava LOS, só parede). Correção 27/08/2026 (usuário
                         # de novo): `vision_rect="full"` cobria a copa inteira do
                         # sprite (metade de CIMA, bem acima do tronco), escondendo
                         # muito mais do que devia — regra nova, igual à bush:
                         # vision_rect = MESMO retângulo do collision_rect (só a
                         # base/tronco bloqueia visão, não a copa toda). Ver
                         # PROBLEMAS_ARQUITETURA.md.
                         ("t1",       0,  64,  96, 128,   ( 32,  96, 32,32), 0, False, 1, ( 32,  96, 32,32), 2),
                         ("t2",      96,  32,  96, 160,   ( 32, 128, 32,32), 0, False, 1, ( 32, 128, 32,32), 2),
                         ("t3",     192,  32, 128, 160,   ( 32, 128, 32,32), 0, False, 1, ( 32, 128, 32,32), 2),
                         ("t4",     320,   0, 128, 192,   ( 32, 160, 32,32), 0, False, 1, ( 32, 160, 32,32), 2),
                         ("t5",     448,   0, 128, 192,   ( 32, 160, 32,32), 0, False, 1, ( 32, 160, 32,32), 2),
                         ("t6",       0, 256,  96, 128,   ( 32,  96, 32,32), 0, False, 1, ( 32,  96, 32,32), 2),
                         ("t7",      96, 256,  96, 128,   ( 32,  96, 32,32), 0, False, 1, ( 32,  96, 32,32), 2),
                         ("t8",     192, 224, 128, 160,   ( 32, 128, 32,32), 0, False, 1, ( 32, 128, 32,32), 2),
                         ("t9",     320, 192, 128, 192,   ( 32, 160, 32,32), 0, False, 1, ( 32, 160, 32,32), 2),
                         ("t10",    448, 192, 128, 192,   ( 32, 160, 32,32), 0, False, 1, ( 32, 160, 32,32), 2),
                         ("t11",      0, 384, 160, 160,   ( 64, 128, 32,32), 0, False, 1, ( 64, 128, 32,32), 2),
                         ("t12",    160, 384,  96, 160,   ( 32, 128, 32,32), 0, False, 1, ( 32, 128, 32,32), 2),
                         ("t13",    288, 416,  96, 128,   ( 32,  96, 32,32), 0, False, 1, ( 32,  96, 32,32), 2),
                         ("t14",    672,   0, 320, 256,   ( 96, 224, 96,32), 0, False, 1, ( 96, 224, 96,32), 2),
                         ("t15",    704, 288, 256, 224,   ( 96, 192, 64,32), 0, False, 1, ( 96, 192, 64,32), 2),
                     ],
                 },
                 "arbustos": {
                     "prefix": "pl",
                     "label":  "Arbustos",
                     "color":  (52, 108, 42),   # BUSH_TILE
                     # is_bush (01/09/2026) — único grupo do catálogo que
                     # marca isto; feedback de opacidade (ver TileType.is_bush)
                     # só dispara pra tiles desta família.
                     "is_bush": True,
                     "tiles": [
                         # vision_rect="base_row" (13/08/2026, §51, pedido do
                         # usuário) — só a fileira de baixo do sprite (largura
                         # inteira) conta pra bloqueio de visão/adjacência, não
                         # o sprite inteiro. Evita bushes com espaço de grama
                         # visível entre si se fundirem num blob só; bushes com
                         # a BASE colada continuam formando um clump único de
                         # propósito. Ver `_rect_to_tile_offsets`.
                         ("b1",      0,  864,  32,  32,                None, 0, False, 1, "base_row", 2),
                         ("b2",     64,  864,  32,  32,                None, 0, False, 1, "base_row", 2),
                         ("b3",    112,  848,  64,  48,                None, 0, False, 1, "base_row", 2),
                         ("b4",    208,  848,  64,  48,                None, 0, False, 1, "base_row", 2),
                         ("b5",    288,  848,  96,  48,                None, 0, False, 1, "base_row", 2),
                         ("b6",    384,  848,  64,  48,                None, 0, False, 1, "base_row", 2),
                         ("b7",      0,  928,  32,  32,                None, 0, False, 1, "base_row", 2),
                         ("b8",     32,  928,  64,  32,                None, 0, False, 1, "base_row", 2),
                         ("b9",     96,  896,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b10",   160,  896,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b11",   224,  896,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b12",   304,  896,  96,  64,                None, 0, False, 1, "base_row", 2),
                         ("b13",   400,  896,  96,  64,                None, 0, False, 1, "base_row", 2),
                         ("b14",     0,  992,  32,  32,                None, 0, False, 1, "base_row", 2),
                         ("b15",    32,  992,  64,  32,                None, 0, False, 1, "base_row", 2),
                         ("b16",    96,  960,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b17",   160,  960,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b18",   224,  960,  64,  64,                None, 0, False, 1, "base_row", 2),
                         ("b19",   304,  960,  96,  64,                None, 0, False, 1, "base_row", 2),
                         ("b20",   400,  960,  96,  64,                None, 0, False, 1, "base_row", 2),
                     ],
                 },
                 "trigo": {
                     "prefix": "pl",
                     "label":  "Trigo",
                     "color":  (188, 158, 68),
                     "tiles": [
                         ("w1" ,     0,  704,  64,  64,                None),
                         ("w2" ,    64,  704,  64,  64,                None),
                         ("w3" ,   128,  704,  64,  64,                None),
                         ("w4" ,   192,  704,  64,  64,                None),
                         ("w5" ,   256,  704,  64,  64,                None),
                         ("w6" ,   320,  704,  64,  64,                None),
                         ("w7" ,   384,  704,  64,  64,                None),
                         ("w8" ,   448,  704,  64,  64,                None),
                         ("w9" ,     0,  768,  64,  64,                None),
                         ("w10",    64,  768,  64,  64,                None),
                         ("w11",   128,  768,  64,  64,                None),
                         ("w12",   192,  768,  64,  64,                None),
                         ("w13",   256,  768,  64,  64,                None),
                         ("w14",   320,  768,  64,  64,                None),
                         ("w15",   384,  768,  64,  64,                None),
                         ("w16",   448,  768,  64,  64,                None),
                     ],
                 },
                 "repolho": {
                     "prefix": "pl",
                     "label":  "Repolho",
                     "color":  (94, 138, 66),
                     "tiles": [
                         ("c3",    864,  640,  32,  32,              "full"),
                         ("c2",    928,  768,  32,  32,              "full"),
                         ("c1",    992,  640,  32,  32,              "full"),
                     ],
                 },
                 "abobora": {
                     "prefix": "pl",
                     "label":  "Abóbora",
                     "color":  (206, 128, 34),
                     "tiles": [
                         ("p3",    864,  672,  64,  32,              "full"),
                         ("p2",    928,  672,  32,  32,              "full"),
                         ("p1",    992,  672,  32,  32,              "full"),
                     ],
                 },
                 "cenoura": {
                     "prefix": "pl",
                     "label":  "Cenoura",
                     "color":  (196, 112, 44),
                     "tiles": [
                         ("cr3",   864,  704,  32,  32,             "full"),
                         ("cr2",   928,  704,  32,  32,             "full"),
                         ("cr1",   992,  704,  32,  32,             "full"),
                     ],
                 },
                 "grama_fina": {
                     "prefix": "pl",
                     "label":  "Grama Fina",
                     "color":  (58, 100, 48),
                     "tiles": [
                         ("gb1" ,  768,  864,  32,  32,               None),
                         ("gb2" ,  800,  864,  32,  32,               None),
                         ("gb3" ,  832,  864,  32,  32,               None),
                         ("gb4" ,  864,  864,  32,  32,               None),
                         ("gb5" ,  768,  896,  32,  32,               None),
                         ("gb6" ,  800,  896,  32,  32,               None),
                         ("gb7" ,  832,  896,  32,  32,               None),
                         ("gb8" ,  864,  896,  32,  32,               None),
                         ("gb9" ,  768,  928,  32,  32,               None),
                         ("gb10",  800,  928,  32,  32,               None),
                         ("gb11",  832,  928,  32,  32,               None),
                         ("gb12",  864,  928,  32,  32,               None),
                         ("gb13",  768,  960,  32,  32,               None),
                         ("gb14",  800,  960,  32,  32,               None),
                         ("gb15",  832,  960,  32,  32,               None),
                     ],
                 },
                 "flores": {
                     "prefix": "pl",
                     "label":  "Flores",
                     "color":  (58, 100, 48),
                     "tiles": [
                         ("fl1" ,  864,  960,  32,  32,               None),
                         ("fl2" ,  896,  864,  32,  32,               None),
                         ("fl3" ,  928,  864,  32,  32,               None),
                         ("fl4" ,  960,  864,  32,  32,               None),
                         ("fl5" ,  992,  864,  32,  32,               None),
                         ("fl6" ,  896,  896,  32,  32,               None),
                         ("fl7" ,  928,  896,  32,  32,               None),
                         ("fl8" ,  960,  896,  32,  32,               None),
                         ("fl9" ,  992,  896,  32,  32,               None),
                         ("fl10",  896,  928,  32,  32,               None),
                         ("fl11",  928,  928,  32,  32,               None),
                         ("fl12",  960,  928,  32,  32,               None),
                         ("fl13",  992,  928,  32,  32,               None),
                         ("fl14",  896,  960,  32,  32,               None),
                         ("fl15",  928,  960,  32,  32,               None),
                         ("fl16",  960,  960,  32,  32,               None),
                         ("fl17",  992,  960,  32,  32,               None),
                         ("fl18",  896,  992,  32,  32,               None),
                         ("fl19",  928,  992,  32,  32,               None),
                         ("fl20",  960,  992,  32,  32,               None),
                         ("fl21",  992,  992,  32,  32,               None),
                     ],
                 },
             },
         },
{
        "file":   "TX Village Props with Shadow",
        "prefix": "vp",
        "label":  "Objetos",
        "color":  (41, 39, 55),
        "underlying_terrain": ",",
        # Grupos por categoria (02/09/2026, pedido do usuário — cor própria
        # por grupo no minimapa, mesmo padrão de "TX Village Plant" §78).
        # "prefix" continua "vp" em TODOS os grupos de propósito — mapas reais
        # já referenciam objetos plantados como "vp_XXX"; um prefixo por grupo
        # quebraria esses ids. Só a COR muda; ids e geometria intocados.
        "groups": {
            "cemiterio": {
                "prefix": "vp",
                "label":  "Cemitério",
                "color":  (120, 115, 125),
                "tiles": [
                    ("116",  304, 1520,   35,   64, (  0,  32,  32,  32)),
                    ("117",  304, 1600,   40,   80, (  0,  48,  32,  32)),
                    ("141",   16, 1712,   48,   80, (  0,  48,  32,  32)),
                    ("142",   80, 1728,   48,   64, (  0,  32,  32,  32)),
                    ("143",  128, 1744,   33,   48, (  0,  16,  32,  32), 0, 0, 1, None, 0, (12, 27, 8, 3, 65, 6)),
                    ("144",  192, 1696,   98,   96, None, 0, False, 0),
                    ("145",  304, 1696,   34,   48, (  0,  16,  32,  32)),
                    ("146",   16, 1808,   64,   80, ( 16,  64,  32,  16)),
                    ("147",   64, 1792,   96,   96, ( 32,  64,  32,  32)),
                    ("148",  160, 1808,   48,   48, (  0,  16,  32,  32)),
                    ("149",  160, 1856,   48,   64, (  0,   0,  32,  64)),
                    ("150",  224, 1808,   64,   48, (  0,  32,  64,  32)),
                    ("151",  224, 1872,   64,   48, (  0,  16,  64,  32)),
                    ("152",  320, 1792,   32,   64, (  0,   0,  32,  64)),
                    ("153",  320, 1856,   32,   64, (  0,   0,  32,  64)),
                    ("154",   32, 1936,   48,   48, (  0,  32,  32,  16)),
                    ("155",   96, 1952,   48,   32, (  0,  16,  32,  16)),
                    ("156",  160, 1936,   48,   48, (  0,  32,  32,  32)),
                    ("157",  224, 1952,   64,   32, (  0,   0,  64,  32)),
                    ("158",  320, 1952,   64,   32, (  0,   0,  64,  32)),
                    ("159",   32, 2000,   48,   48, (  0,  16,  32,  32)),
                    ("160",   96, 2000,   48,   48, (  0,  16,  32,  32)),
                    ("161",  144, 1984,   48,   64, (  0,  32,  32,  32)),
                    ("162",  224, 1984,   32,   64, (  0,  16,  32,  48)),
                    ("163",  288, 1984,   32,   64, (  0,  16,  32,  48)),
                    ("213", 1056, 1968,   64,   64, (  0,  16,  32,  48)),
                ],
            },
            "pedras": {
                "prefix": "vp",
                "label":  "Pedras",
                "color":  (150, 145, 130),
                "tiles": [
                    ("185",  688, 1872,   64,   48, (  0,  16,  64,  32)),
                    ("186",  688, 1920,   32,   32, None),
                    ("187",  720, 1920,   32,   32, None),
                    ("188",  752, 1920,   32,   32, None),
                    ("189",  784, 1920,   32,   32, (  0,  16,  32,  16)),
                    ("190",  816, 1920,   32,   32, (  0,  16,  32,  16)),
                    ("191",  848, 1920,   32,   32, (  0,  16,  32,  16)),
                    ("192",  672, 1984,   32,   32, None),
                    ("193",  688, 1952,   32,   32, None),
                    ("194",  704, 1984,   32,   32, None),
                    ("195",  720, 1952,   32,   32, None),
                    ("196",  736, 1984,   32,   32, (  0,  16,  32,  16)),
                    ("197",  752, 1952,   32,   32, (  0,  16,  32,  16)),
                    ("198",  768, 1984,   32,   32, (  0,  16,  32,  16)),
                    ("200",  800, 1984,   32,   32, (  0,  16,  32,  16)),
                    ("201",  832, 1952,   48,   32, ( 16,  16,  32,  16)),
                    ("202",  832, 1984,   48,   32, ( 16,  16,  32,  16)),
                    ("203",  896, 1952,   64,   32, ( 16,  16,  32,  16)),
                    ("204",  896, 1984,   64,   32, ( 16,  16,  32,  16)),
                    ("205",  960, 1952,   64,   64, ( 16,  32,  32,  16)),
                    ("206",  672, 2016,   32,   32, None),
                    ("207",  704, 2016,   32,   32, None),
                    ("208",  736, 2016,   32,   32, None),
                    ("209",  768, 2016,   32,   32, (  0,  16,  32,  16)),
                    ("210",  848, 2016,   48,   32, (  0,  16,  32,  16)),
                    ("211",  896, 2016,   64,   32, ( 16,  16,  32,  16)),
                    ("212",  960, 2016,   64,   32, ( 16,  16,  32,  16)),
                ],
            },
            "plantas_jardim": {
                "prefix": "vp",
                "label":  "Plantas de Jardim",
                "color":  (80, 140, 60),
                "tiles": [
                    ("064",  928, 1056,   80,   32, ( 16,  16,  48,  16)),
                    ("065",  928, 1088,   80,   32, ( 16,  16,  48,  16)),
                    ("067", 1056, 1024,   32,   64, (  0,  16,  32,  48)),
                    ("068", 1088, 1024,   32,   64, (  0,  16,  32,  48)),
                    ("069", 1120, 1024,   32,   64, (  0,  16,  32,  48)),
                    ("073",  928, 1120,   80,   32, ( 16,  16,  48,  16)),
                    ("080",  352, 1104,   48,   48, (  0,  32,  32,  16)),
                    ("089",  960, 1312,   32,   32, (  0,  16,  16,  16)),
                    ("090",  992, 1312,   32,   32, (  0,  16,  16,  16)),
                    ("091", 1024, 1312,   32,   32, (  0,  16,  16,  16)),
                ],
            },
            "moveis": {
                "prefix": "vp",
                "label":  "Móveis",
                "color":  (150, 110, 60),
                "tiles": [
                    ("002",   96,   16,   48,   48, (  0,  16,  32,  32)),
                    ("003",  160,   16,   48,   48, (  0,  16,  32,  32)),
                    ("004",  224,   16,   80,   48, (  0,  16,  64,  32)),
                    ("005",  320,   32,   48,   32, (  0,   0,  32,  32)),
                    ("007",   96,   80,   48,   48, (  0,  16,  32,  32)),
                    ("008",  160,   80,   48,   48, (  0,  16,  32,  32)),
                    ("009",  224,   80,   80,   48, (  0,  16,  64,  32)),
                    ("010",  320,   64,   48,   64, (  0,  32,  32,  32)),
                    ("021",  224,  208,   64,   48, (  0,  16,  64,  32)),
                    ("054",  528,  912,   96,   48, ( 16,  32,  63,  16)),
                    ("055",  528,  960,   48,   96, (  0,  32,  32,  64)),
                    ("056",  544, 1072,   96,   48, ( 16,  16,  64,  32)),
                    ("057",  592,  960,   48,   96, (  0,  32,  32,  63)),
                    ("058",  640, 1040,   64,   80, ( 16,  16,  32,  64)),
                    ("059",  704, 1040,   32,   80, (  8,  16,  16,  64)),
                    ("060",  736, 1024,  112,   32, ( 16,  16,  80,  16)),
                    ("061",  736, 1056,   32,   64, (  9,  16,  16,  48)),
                    ("062",  864, 1024,   64,   32, (  0,  16,  64,  16)),
                    ("063",  928, 1024,   80,   32, ( 16,  16,  48,  16)),
                    ("066", 1024, 1024,   32,   64, (  0,  16,  32,  48)),
                    ("070", 1152, 1024,   32,   64, (  0,  16,  32,  48)),
                    ("071",  544, 1120,   96,   64, ( 16,  32,  64,  32)),
                    ("072",  640, 1136,   64,   80, ( 16,  16,  32,  64)),
                    ("074",  928, 1152,   80,   32, ( 16,  16,  48,  16)),
                    ("075",  544, 1200,   48,   48, (  0,  16,  32,  32)),
                    ("076",  608, 1216,   32,   32, (  0,  16,  16,  16)),
                    ("077",  144, 1120,   64,   32, ( 16,  16,  32,  16)),
                    ("101",  384, 1376,   80,   64, (  0,  48,  64,  16)),
                    ("112",  128, 1488,   96,   80, (  0,  64,  79,  16)),
                    ("180",  976, 1696,   48,   64, (  0,  32,  32,  32)),
                ],
            },
            "potes_e_recipientes": {
                "prefix": "vp",
                "label":  "Potes e Recipientes",
                "color":  (190, 130, 70),
                "tiles": [
                    ("012",   32,  144,   32,   48, (  0,  32,  32,  16)),
                    ("013",   64,  144,   32,   48, (  0,  32,  32,  16)),
                    ("014",   96,  144,   32,   48, (  0,  32,  32,  16)),
                    ("015",   32,  192,   32,   32, (  0,  16,  32,  16)),
                    ("016",   32,  224,   32,   32, (  0,  16,  32,  16)),
                    ("017",   96,  192,   32,   32, (  0,  16,  32,  16)),
                    ("018",   96,  224,   32,   32, (  0,  16,  32,  16)),
                    ("019",  160,  176,   48,   48, (  0,  32,  31,  16)),
                    ("020",  160,  224,   32,   32, (  0,  16,  32,  16)),
                    ("024",   32,  272,   32,   48, (  0,  32,  32,  16)),
                    ("025",   64,  272,   32,   48, (  0,  32,  32,  16)),
                    ("026",   96,  272,   32,   48, (  0,  32,  32,  16)),
                    ("027",  128,  272,   32,   48, (  0,  32,  32,  16)),
                    ("028",  160,  272,   32,   48, (  0,  32,  32,  16)),
                    ("078",  224, 1104,   48,   48, (  0,  32,  33,  16)),
                    ("079",  288, 1104,   48,   48, (  0,  32,  32,  16)),
                    ("081",  224, 1168,   48,   48, (  0,  32,  32,  16)),
                    ("082",  288, 1168,   48,   48, (  0,  32,  32,  16)),
                    ("083",  352, 1168,   32,   48, (  0,  32,  32,  16)),
                    ("084",  224, 1232,   48,   48, (  0,  16,  32,  32)),
                    ("085",  288, 1216,   64,   64, (  0,  32,  64,  32)),
                    ("087",  896, 1312,   32,   32, (  0,  16,  16,  16)),
                    ("088",  928, 1312,   32,   32, (  0,  16,  16,  16)),
                    ("094", 1152, 1360,   32,   48, (  0,  16,  32,  32)),
                    ("122",  512, 1456,   64,   48, ( 16,  16,  32,  32)),
                    ("135",  736, 1440,   32,   32, (  0,  16,  32,  16)),
                    ("138",  768, 1440,   32,   32, (  0,  16,  32,  16)),
                    ("139",  800, 1440,   32,   32, (  0,  16,  32,  16)),
                    ("140",  832, 1440,   48,   32, (  0,  16,  32,  16)),
                    ("166",  608, 1408,   32,   32, (  0,  16,  32,  16)),
                    ("167",  640, 1408,   32,   32, (  0,  16,  32,  16)),
                ],
            },
            "comida": {
                "prefix": "vp",
                "label":  "Comida",
                "color":  (210, 160, 50),
                "tiles": [
                    ("022",  288,  208,   64,   48, (  0,  16,  64,  32)),
                    ("023",  352,  208,   64,   48, (  0,  16,  64,  32)),
                    ("029",  224,  272,   64,   48, (  0,  16,  64,  32)),
                    ("030",  288,  272,   64,   48, (  0,  16,  64,  32)),
                    ("031", 1792,   32,   32,   32, None),
                    ("032", 1792,   64,   32,   32, None),
                    ("033", 1792,   96,   32,   32, None),
                    ("034", 1856,   32,   32,   32, None),
                    ("035", 1856,   64,   32,   32, None),
                    ("036", 1856,   96,   32,   32, None),
                    ("037", 1920,   32,   32,   32, None),
                    ("038", 1952,   32,   32,   32, None),
                    ("039", 1984,   32,   32,   32, None),
                    ("040", 1920,   64,   32,   32, None),
                    ("041", 1952,   64,   32,   32, None),
                    ("042", 1984,   64,   32,   32, None),
                    ("043", 1920,   96,   32,   32, None),
                    ("044", 1952,   96,   32,   32, None),
                    ("045", 1984,   96,   32,   32, None),
                    ("046", 1920,  128,   32,   32, None),
                    ("047", 1952,  128,   32,   32, None),
                    ("048", 1984,  128,   32,   32, None),
                    ("049", 1920,  160,   32,   32, None),
                    ("050", 1952,  160,   32,   32, None),
                    ("051", 1920,  192,   32,   32, None),
                    ("052", 1920,  224,   32,   32, None),
                    ("053", 1920,  256,   32,   32, None),
                ],
            },
            "madeira_e_cercas": {
                "prefix": "vp",
                "label":  "Madeira e Cercas",
                "color":  (160, 130, 85),
                "tiles": [
                    ("099",  784, 1312,   64,   32, ( 16,  16,  32,  16)),
                    ("100",   32, 1328,   32,   80, (  0,  64,  32,  16)),
                    ("103",    0, 1584,   96,   48, (  0,  32,  80,  16)),
                    ("104",    0, 1648,   32,   48, (  0,  32,  16,  16)),
                    ("105",   32, 1440,   64,   32, (  0,  16,  48,  16)),
                    ("106",   32, 1472,   32,   64, (  0,  16,  16,  48)),
                    ("108",   64, 1472,   32,   96, (  0,  16,  16,  80)),
                    ("109",   96, 1440,   96,   32, (  0,  16,  80,  16)),
                    ("110",   96, 1472,   32,   32, (  0,  16,  16,  16)),
                    ("111",  112, 1568,   32,  128, (  0,  32,  32,  96)),
                    ("113",  160, 1632,   32,   96, (  0,  32,  32,  64)),
                    ("123",  544, 1520,   32,   48, None),
                    ("124",  544, 1568,   64,   32, None),
                    ("125",  544, 1600,   64,   32, None),
                    ("126",  576, 1520,   32,   48, None),
                    ("127",  608, 1472,   80,   32, (  0,  16,  64,  16)),
                    ("128",  608, 1520,   32,   48, None),
                    ("129",  608, 1568,   64,   32, None),
                    ("130",  608, 1600,   64,   32, None),
                    ("131",  640, 1520,   32,   48, None),
                    ("132",  672, 1536,   32,   32, None),
                    ("133",  672, 1600,   32,   32, None),
                    ("134",  688, 1472,   48,   64, ( 16,  16,  16,  48)),
                    ("136",  736, 1536,   32,   16, None),
                    ("137",  736, 1552,   32,   80, (  1,  16,  30,  48)),
                    ("164",  864, 1376,   48,   32, (  0,  16,  32,  16)),
                    ("165",  912, 1376,   64,   32, ( 16,  16,  32,  16)),
                    ("172",  544, 1712,   64,   48, (  0,  32,  64,  16)),
                    ("173",  608, 1712,   64,   48, ( 16,  32,  32,  16)),
                ],
            },
            "decoracao_vila": {
                "prefix": "vp",
                "label":  "Decoração de Vila",
                "color":  (100, 120, 130),
                "tiles": [
                    ("006",  384,    0,  128,   64, (  0,  48, 112,  16)),
                    ("011",  384,   64,  112,   64, (  0,  48,  96,  16)),
                    ("086",  880, 1280,   64,   32, ( 16,  16,  32,  16)),
                    ("092", 1008, 1360,   80,   80, ( 16,  64,  32,  16)),
                    ("093", 1088, 1376,   48,   32, (  0,  16,  32,  16)),
                    ("095", 1056, 1456,   64,   48, ( 16,  16,  32,  32)),
                    ("096", 1120, 1440,   96,   64, ( 16,  32,  64,  32)),
                    ("097", 1216, 1424,   96,   80, ( 16,  32,  64,  32)),
                    ("098",  736, 1296,   48,   48, (  0,  32,  32,  16)),
                    ("102",  512, 1392,   64,   48, ( 16,  16,  32,  32)),
                    ("107",   32, 1632,   64,   64, (  0,  32,  64,  32)),
                    ("115",  224, 1616,   64,   64, (  0,  16,  64,  32)),
                    ("118",  320, 1456,   48,   48, (  0,  32,  32,  16)),
                    ("119",  384, 1456,   32,   48, (  0,  32,  32,  16)),
                    ("120",  416, 1456,   32,   48, (  0,  32,  32,  16)),
                    ("121",  448, 1456,   48,   48, (  0,  32,  32,  16)),
                    ("168",  864, 1504,   64,   64, None),
                    ("169",  960, 1504,   64,   64, None),
                    # "170" — 1º objeto do catálogo marcado como fonte de luz
                    # (21/09/2026, pedido do usuário). Ponto da chama a
                    # (44, 41) do canto do sprite (368,1664 na folha inteira
                    # menos sx,sy), retângulo 5×6, 75% de intensidade máxima
                    # (só à noite), raio 3 tiles.
                    ("170",  368, 1664,   80,   96, (  0,  80,  32,  16), 0, 0, 1, None, 0, (44, 41, 5, 6, 75, 5)),
                    ("171",  448, 1664,   80,   96, ( 32,  80,  32,  16), 0, 0, 1, None, 0, (16, 41, 5, 6, 75, 5)),
                    ("215",  912, 1808,   48,   96, (  0,  64,  32,  32), 0, 0, 1, None, 0, (13, 51, 5, 6, 75, 5)),
                    ("216",  960, 1808,   48,   96, (  0,  64,  32,  32), 0, 0, 1, None, 0, (13, 51, 5, 6, 75, 5)),
                    ("174",  720, 1680,   48,   32, None),
                    ("175",  736, 1712,   48,   48, (  0,  32,  32,  16)),
                    ("176",  800, 1664,   80,   64, (  0,  32,  63,  32)),
                    ("177",  800, 1728,   64,   32, ( 16,   0,  32,  32)),
                    ("178",  896, 1664,   80,   64, (  0,  32,  64,  32)),
                    ("179",  896, 1728,   64,   32, ( 16,   0,  32,  31)),
                    ("181", 1056, 1696,   48,   64, (  0,  48,  32,  15)),
                    ("182",  384, 1792,  128,   64, (  0,  16, 112,  48)),
                    ("183",  512, 1792,  128,   64, (  0,  16, 112,  48)),
                    ("184",  656, 1760,   48,   64, (  0,  32,  32,  32)),
                ],
            },
            "diversos": {
                "prefix": "vp",
                "label":  "Diversos",
                "color":  (41, 39, 55),
                "tiles": [
                    ("001",   32,   16,   48,   48, (  0,  16,  32,  32)),
                    # vision_rect="full"/vision_h=2 (02/09/2026) -- pedra
                    # grande com colisão cobrindo quase o sprite inteiro,
                    # mesma regra de "pedra grande bloqueia visão como
                    # árvore/bush" (13/08/2026) que existia no antigo
                    # "TX Props" ("pr_bigrock"/"pr_rock3-6", removido nesta
                    # sessão) -- perdida na migração pra este catálogo até
                    # os testes de tests/test_vision_blocking.py apontarem
                    # a regressão. Ver PROBLEMAS_ARQUITETURA.md §101.
                    ("199",  783, 1951,   50,   34, (  0,   0,  50,  33), 0, False, 1, "full", 2),
                    #COLISÃO INVISÍVEL
                    ("214",  352, 2016,   32,   32, (  0,   0,  32,  32))
                ],
            },
        },
    },

    # ── TX Tileset Wall — estrutura escalonável (512×512, tiles 32×32) ────────
    #
    # Configuração de exemplo: uma plataforma/muro que o jogador pode subir.
    #
    # Quando o player está NO CHÃO (elevation=0):
    #   → Tiles com elevation=1 são transparentes para colisão (passa por baixo/atrás)
    #   → Apenas o tile de escada (is_transition=True) permite entrar na estrutura
    #
    # Quando o player está EM CIMA (elevation=1):
    #   → Tiles de borda com collision_dirs impedem sair para direções inválidas
    #   → A escada (is_transition=True, elevation=0) permite descer
    #
    # Legenda de collision_dirs (bitmask):
    # AJUSTE as posições sx,sy conforme o layout real do seu PNG.
    {
        "file":   "TX Tileset Wall",
        "prefix": "wall",
        "label":  "Muros",
        "color":  (120, 100, 80),
        "underlying_terrain": ".",
        "tiles": [
            # Formato: (id, sx, sy, w, h, col, piso, transpassavel)
            #   col          — None=sem colisão, "base"=base 32x32, "full"=sprite inteiro
            #   piso         — 0=chão, 1=primeiro andar, "t"=transição entre pisos
            #   transpassavel— 1: jogador no piso abaixo passa por trás sem ser bloqueado
            #                  0: bloqueia em todos os pisos

            # ── Superfície do piso superior (piso=1, transpassavel=1) ────────
            # Jogadores no chão passam por baixo; no piso 1 andam livremente.
            # O sistema de pisos já impede sair da borda (floor 1 → floor 0 bloqueado).
            # (id,       sx,  sy,  w,   h,     col,    piso, transp, sort)
            ("surf_NW",  32,  32,  32,  32,    None,   1,    1,      0),
            ("surf_N",   64,  32,  32,  32,    None,   1,    1,      0),
            ("surf_NE",  96,  32,  32,  32,    None,   1,    1,      0),
            ("surf_W",   32,  64,  32,  32,    None,   1,    0,      0),
            ("surf_C",   64,  64,  32,  32,    None,   1,    0,      0),
            ("surf_E",   96,  64,  32,  32,    None,   1,    0,      0),

            # ── Face sólida (piso=0, transpassavel=0) ────────────────────────
            # Bloqueia ao nível do chão E em cima — é a parede da estrutura.
            ("face_W",    32,  96,  32,  64,  "full", 0,  0),
            ("face_C",    64,  96,  32,  64,  "full", 0,  0),
            ("face_C1",   32, 192,  32,  64,  "full", 0,  0),
            ("face_C2",   64, 192,  32,  64,  "full", 0,  0),
            ("face_C3",   96, 192,  32,  64,  "full", 0,  0),
            ("face_C4",  128, 192,  32,  64,  "full", 0,  0),
            ("face_C5",  160, 192,  32,  64,  "full", 0,  0),
            ("face_C6",   32, 256,  32,  64,  "full", 0,  0),
            ("face_C7",   64, 256,  32,  64,  "full", 0,  0),
            ("face_C8",  128, 256,  32,  64,  "full", 0,  0),
            ("face_C9",  160, 256,  32,  64,  "full", 0,  0),
            ("face_C10",  384, 320,  32,  64,  "full", 0,  0),
            ("open_C1",  416, 320,  32,  64,    None, 0,  0),
            ("face_C10", 448, 320,  32,  64,  "full", 0,  0),
            ("face_C11", 384, 192,  32,  64,  "full", 0,  0),
            ("face_C12", 416, 192,  32,  64,  "full", 0,  0),
            ("face_C13", 448, 192,  32,  64,  "full", 0,  0),
            ("face_C14", 480, 192,  32,  64,  "full", 0,  0),
            ("lat_W1",   288,  32,  32,  32,  "full", 0,  0),
            ("lat_W2",   288,  64,  32,  32,  "full", 0,  0),
            ("lat_W3",   288,  96,  32,  32,  "full", 0,  0),
            ("lat_W4",   288, 128,  32,  32,  "full", 0,  0),
            ("lat_E1",   320,  32,  32,  32,  "full", 0,  0),
            ("lat_E2",   320,  64,  32,  32,  "full", 0,  0),
            ("lat_E3",   320,  96,  32,  32,  "full", 0,  0),
            ("lat_E4",   320, 128,  32,  32,  "full", 0,  0),
            ("face_E",    96,  96,  32,  64,  "full", 0,  0),


            # ── Escada / transição (piso="t") ─────────────────────────────────
            # Acessível de qualquer piso. Ao SAIR daqui para um tile de piso N,
            # a elevation da entidade assume N.
            ("stair1",    192, 192,  64,  96,  None, "t", 0, 0, 0),
            ("stair2",    256, 192,  64,  96,  None, "t", 0, 0, 0),
            ("stair3",    320, 192,  64,  96,  None, "t", 0, 0, 0),
            ("stair4",    192, 288,  64,  96,  None, "t", 0, 0, 0),
            ("stair5",    256, 288,  64,  96,  None, "t", 0, 0, 0),
            ("stair6",    320, 288,  64,  96,  None, "t", 0, 0, 0),
        ],
    },
    {
        "file":   "TX Village Building with Shadow",
        "prefix": "vb",
        "label":  "Construções",
        "color":  (115, 108, 98),
        "underlying_terrain": "G",
        # occludes_player (20/09/2026, pedido do usuário) — no nível de
        # FAMÍLIA: cascateia pra TODOS os sub-grupos de construção
        # abaixo (b01..b0N), sem precisar marcar cada tipo de prédio na
        # mão. Ver TileType.occludes_player / discover_object_sheet_tiles.
        "occludes_player": True,
        # Catalogado em 02/09/2026 a partir de 4 arquivos de apoio (ver
        # §98/§99/§100), REcatalogado em 03/09/2026 depois do usuário
        # corrigir os 4 PNGs de apoio (piezas re-slicedas, colisões
        # ajustadas) -- 104 peças viraram 97 (uma construção inteira,
        # "casebre pequeno", saiu da arte nova; outras foram resliced
        # sem mudar de conceito). Reprocessado do zero (mesmo script de
        # component-labeling do §98), reatribuição de peça-pra-construção
        # feita por OVERLAP geométrico contra o catálogo anterior (não a
        # olho) -- cada peça nova herda o grupo/label da peça antiga que
        # mais se sobrepõe espacialmente, robusto a resize/reslice desde
        # que a construção não tenha se movido de lugar no sheet (não
        # moveu, confirmado: os 30 retângulos verdes bateram 1-a-1 na
        # mesma posição do catálogo anterior). Ver PROBLEMAS_ARQUITETURA.md
        # §103.
        #
        # IDs viraram sequenciais puros ("vb001".."vb097", sem código de
        # construção nem "_N" embutido) -- pedido do usuário (03/09/2026):
        # o nome descritivo já mora no "label" do grupo, o id da peça só
        # precisa ser único, não precisa carregar a qual construção ela
        # pertence. Chave do grupo (`"b01"` etc, dict key, nunca exibida)
        # continua curta só por organização interna do arquivo.
        #
        # `vision_height` (bloqueio de FOV, só >=2 conta) é 2 pra
        # qualquer peça com colisão em construções/muralhas (mesmo padrão
        # de WALL_TILE) -- EXCETO em "mercado_e_diversos", onde só peças
        # genuinamente altas (tenda indígena, casinha do poço) bloqueiam;
        # bancas de mercado e poços cobertos têm colisão (não atravessa)
        # mas NÃO bloqueiam visão (altura de balcão, não de parede).
"groups": {
            "b01": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Casa pequena com chaminé",
                "color":  (115, 108, 98),
                "tiles": [
                    ("001",   128,    16,  176,  304, (0, 144, 128, 160), 0, False, 1, None, 2),
                    ("002",    64,    64,   64,  224, (16, 96, 48, 96), 0, False, 1, None, 2),
                ],
            },
            "b02": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre-pombal com varanda",
                "color":  (115, 108, 98),
                "tiles": [
                    ("003",   352,     0,  224,  352, (80, 192, 80, 160), 0, False, 1, None, 2),
                ],
            },
            "b03": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Estábulo arqueado",
                "color":  (115, 108, 98),
                "tiles": [
                    ("004",   608,    96,   64,  224, (32, 192, 32, 32), 0, False, 1, None, 2),
                    ("005",   672,    96,  128,  224, (32, 192, 64, 32), 0, False, 1, None, 2),
                    ("006",   800,    96,   32,  224, (0, 192, 32, 32), 0, False, 1, None, 2),
                    ("007",   832,    96,   64,  224, (0, 128, 64, 96), 0, False, 1, None, 2),
                ],
            },
            "b04": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Coreto de poço coberto",
                "color":  (115, 108, 98),
                "tiles": [
                    ("008",   928,    64,  128,  160, (0, 128, 32, 32), 0, False, 1, None, 2),
                    ("009",  1056,    64,   32,  160, (0, 128, 32, 32), 0, False, 1, None, 2),
                    ("010",  1088,    64,   96,  224, (0, 64, 96, 160), 0, False, 1, None, 2),
                    ("011",  1184,    64,   64,  224, (32, 128, 32, 32), 0, False, 1, None, 2),
                ],
            },
            "b06": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Chaminé dupla A",
                "color":  (115, 108, 98),
                "tiles": [
                    ("012",  1312,     0,   96,  192, (0, 160, 64, 32), 0, False, 1, None, 2),
                ],
            },
            "b07": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Chaminé dupla B",
                "color":  (115, 108, 98),
                "tiles": [
                    ("013",  1408,     0,   96,  192, (0, 160, 64, 32), 0, False, 1, None, 2),
                ],
            },
            "b08": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Casarão com torre",
                "color":  (115, 108, 98),
                "tiles": [
                    ("014",  1664,     0,  160,  352, (32, 160, 128, 192), 0, False, 1, None, 2),
                    ("015",  1824,     0,  192,  352, (0, 160, 128, 96), 0, False, 1, None, 2),
                ],
            },
            "b09": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre de vigia de madeira",
                "color":  (115, 108, 98),
                "tiles": [
                    ("016",    32,   352,   96,  448, (32, 352, 64, 64), 0, False, 1, None, 2),
                    ("017",   128,   352,   96,  448, (0, 352, 96, 96), 0, False, 1, None, 2),
                    ("018",   224,   352,   96,  448, (0, 384, 32, 64), 0, False, 1, None, 2),
                ],
            },
            "b10": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre redonda fortificada",
                "color":  (115, 108, 98),
                "tiles": [
                    ("019",   320,   384,  192,  288, (32, 160, 128, 128), 0, False, 1, None, 2),
                    ("020",   352,   672,  128,   48, (32, 0, 64, 32), 0, False, 1, None, 2),
                ],
            },
            "b11": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre-capela com chaminé",
                "color":  (115, 108, 98),
                "tiles": [
                    ("021",   544,   416,  192,  288, (32, 128, 128, 160), 0, False, 1, None, 2),
                ],
            },
            #"b12": {
            #    "prefix": "vb",
            #    "no_prefix_separator": True,
            #    "label":  "Torre com janela em arco",
            #    "color":  (115, 108, 98),
            #    "tiles": [
            #        ("022",   832,   352,  160,  288, (32, 224, 96, 64), 0, False, 1, None, 2),
            #    ],
            #},
            #"b13": {
            #    "prefix": "vb",
            #    "no_prefix_separator": True,
            #    "label":  "Alpendre com banco",
            #    "color":  (115, 108, 98),
            #    "tiles": [
            #        ("023",   768,   512,   64,  128, (0, 96, 64, 32), 0, False, 1, None, 2),
            #    ],
            #},
            #"b14": {
            #    "prefix": "vb",
            #    "no_prefix_separator": True,
            #    "label":  "Estábulo de três arcos",
            #    "color":  (115, 108, 98),
            #    "tiles": [
            #        ("024",   992,   480,   64,  160, (0, 96, 32, 64), 0, False, 1, None, 2),
            #        ("025",  1056,   480,   64,  160, (0, 96, 64, 64), 0, False, 1, None, 2),
            #        ("026",  1120,   480,   96,  160, (32, 96, 32, 64), 0, False, 1, None, 2),
            #    ],
            #},
            "b15": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Coreto alto sobre pilares",
                "color":  (115, 108, 98),
                "tiles": [
                    ("027",   864,   640,   64,  224, (0, 192, 32, 32), 0, False, 1, None, 2),
                    ("028",   928,   640,   64,  224, (0, 192, 32, 32), 0, False, 1, None, 2),
                ],
            },
            "b16": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Casa de telhado alto com arcada",
                "color":  (115, 108, 98),
                "tiles": [
                    ("029",  1248,   368,   64,  336, ( 32, 144,  32, 192), 0, False, 1, None, 2),
                    ("030",  1312,   368,   64,  336, (  0, 144,  64,  96), 0, False, 1, None, 2),
                    ("031",  1376,   368,   32,  336, (  0, 144,  32, 192), 0, False, 1, None, 2),
                    ("032",  1408,   416,  112,  288, (  0,  96,  64,  96), 0, False, 1, None, 2),
                ],
            },
            "b17": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Passagem coberta dupla",
                "color":  (115, 108, 98),
                "tiles": [
                    ("033",  1664,   464,  128,  144, (32, 80, 96, 64), 0, False, 1, None, 2),
                    ("034",  1792,   464,  128,  144, (64, 112, 32, 32), 0, False, 1, None, 2),
                ],
            },
            "b18": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre com celeiro anexo",
                "color":  (115, 108, 98),
                "tiles": [
                    ("035",  1536,   624,   32,  368, (0, 112,  32,  64), 0, False, 1, None, 2),
                    ("036",  1568,   624,  128,  368, (0, 112, 128, 256), 0, False, 1, None, 2),
                    ("037",  1696,   672,  256,  320, (0, 224, 224,  64), 0, False, 1, None, 2),
                ],
            },
            "b19": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Casa enxaimel com porta",
                "color":  (115, 108, 98),
                "tiles": [
                    ("038",   416,   736,   64,  224, (0, 96, 64, 128), 0, False, 1, None, 2),
                    ("039",   480,   736,   32,  224, (0, 96, 32, 96), 0, False, 1, None, 2),
                    ("040",   512,   736,   32,  224, (0, 96, 32, 128), 0, False, 1, None, 2),
                    ("041",   544,   736,   64,  224, (0, 96, 64, 64), 0, False, 1, None, 2),
                ],
            },
            "b20": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Celeiro de telhado cinza",
                "color":  (115, 108, 98),
                "tiles": [
                    ("042",    32,   832,  352,  224, (32, 128, 256, 96), 0, False, 1, None, 2),
                ],
            },
            "b21": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Casa comprida enxaimel",
                "color":  (115, 108, 98),
                "tiles": [
                    ("043",  1216,   832,  224,  192, (0, 128, 192, 64), 0, False, 1, None, 2),
                ],
            },
            "b22": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre redonda alta",
                "color":  (115, 108, 98),
                "tiles": [
                    ("044",   768,   896,  256,  352, (32, 64, 128, 192), 0, False, 1, None, 2),
                ],
            },
            "b23": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Chalé com chaminé",
                "color":  (115, 108, 98),
                "tiles": [
                    ("045",   544,   992,  192,  256, (32, 96, 128, 96), 0, False, 1, None, 2),
                ],
            },
            "b24": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Porta dupla pequena",
                "color":  (115, 108, 98),
                "tiles": [
                    ("046",  1216,  1040,   32,  112, (0, 80, 32, 32), 0, False, 1, None, 2),
                    ("047",  1248,  1040,   64,  112, (32, 80, 32, 32), 0, False, 1, None, 2),
                ],
            },
            "b25": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Celeiro de telhado vermelho",
                "color":  (115, 108, 98),
                "tiles": [
                    ("048",    32,  1056,  288,  160, (32, 96, 208, 64), 0, False, 1, None, 2),
                ],
            },
            "b26": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre com alicerce",
                "color":  (115, 108, 98),
                "tiles": [
                    ("049",   352,  1056,  160,  224, (32, 96, 96, 96), 0, False, 1, None, 2),
                ],
            },
            "b27": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Salão comprido com torre",
                "color":  (115, 108, 98),
                "tiles": [
                    ("050",  1855,  1088,   98,  256, (0, 96, 98, 159), 0, False, 1, None, 2),
                    ("051",  1952,  1088,   64,  256, (0, 96, 32, 64), 0, False, 1, None, 2),
                    ("052",  1536,  1120,   96,  160, (0, 96, 96, 32), 0, False, 1, None, 2),
                    ("053",  1632,  1120,  224,  224, (0, 96, 224, 64), 0, False, 1, None, 2),
                    ("054",  1504,  1136,   32,  144, (0, 80, 32, 64), 0, False, 1, None, 2),
                ],
            },
            "b28": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Torre-portão com arcada",
                "color":  (115, 108, 98),
                "tiles": [
                    ("055",  1120,  1248,  128,  256, (0, 192, 128, 64), 0, False, 1, None, 2),
                    ("056",  1248,  1360,   32,  144, (0, 80, 32, 64), 0, False, 1, None, 2),
                    ("057",  1280,  1360,   64,  144, (0, 80, 64, 64), 0, False, 1, None, 2),
                    ("058",  1344,  1360,   32,  144, (0, 80, 32, 64), 0, False, 1, None, 2),
                    ("059",  1376,  1360,   64,  144, (0, 80, 32, 64), 0, False, 1, None, 2),
                    ("060",  1056,  1376,   64,  128, (32, 64, 32, 64), 0, False, 1, None, 2),
                    ("061",  1120,  1504,  128,  160, None, 0, False, 1, None, 0),
                ],
            },
            "b29": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Capela de arco aberto",
                "color":  (115, 108, 98),
                "tiles": [
                    ("062",    64,  1216,   64,  160, (0, 128, 32, 32), 0, False, 1, None, 2),
                    ("063",   128,  1216,   64,  160, (32, 128, 32, 32), 0, False, 1, None, 2),
                ],
            },
            "muralhas": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Muralhas e Muros",
                "color":  (115, 108, 98),
                "tiles": [
                    ("064",    32,  1536,   64,   96, None, 0, False, 1, None, 0),
                    ("065",   416,  1568,   32,  128, (0, 96, 32, 32), 0, False, 1, None, 2),
                    ("066",   448,  1568,   32,  128, (0, 64, 32, 64), 0, False, 1, None, 2),
                    ("067",   480,  1568,   32,  128, (0, 96, 32, 32), 0, False, 1, None, 2),
                    ("068",   256,  1600,   64,  144, (0, 80, 64, 64), 0, False, 1, None, 2),
                    ("069",   320,  1600,   64,   96, (0, 64, 64, 32), 0, False, 1, None, 2),
                    ("070",    32,  1632,   64,   96, (0, 64, 64, 32), 0, False, 1, None, 2),
                    ("071",   128,  1632,   32,   96, (0, 64, 32, 32), 0, False, 1, None, 2),
                    ("072",   160,  1632,   64,   96, (32, 64, 32, 32), 0, False, 1, None, 2),
                    ("073",   319,  1695,   66,   50, (0, 0, 65, 49), 0, False, 1, None, 2),
                    ("074",   384,  1696,   32,  128, (0, 64, 32, 64), 0, False, 1, None, 2),
                    ("075",   416,  1696,   64,  128, (0, 96, 64, 32), 0, False, 1, None, 2),
                    ("076",   496,  1696,   64,  128, (0, 64, 64, 64), 0, False, 1, None, 2),
                    ("077",    32,  1728,   64,   96, (0, 64, 64, 32), 0, False, 1, None, 2),
                    ("078",   128,  1728,   32,   96, (0, 64, 32, 32), 0, False, 1, None, 2),
                    ("079",   160,  1728,   64,   96, (32, 64, 32, 32), 0, False, 1, None, 2),
                    ("080",   256,  1744,   64,   80, (0, 48, 64, 32), 0, False, 1, None, 2),
                    ("081",   320,  1744,   64,  128, (0, 64, 64, 64), 0, False, 1, None, 2),
                ],
            },
            "mercado_e_diversos": {
                "prefix": "vb",
                "no_prefix_separator": True,
                "label":  "Mercado e Diversos",
                "color":  (115, 108, 98),
                "tiles": [
                    ("082",   608,  1664,   80,  160, None, 0, False, 1, None, 0),
                    ("083",   832,  1664,  112,  160, None, 0, False, 1, None, 0),
                    ("084",   688,  1712,  132,  112, None, 0, False, 1, None, 0),
                    ("085",   976,  1712,   96,  144, (0, 80, 96, 32), 0, False, 1, None, 2),
                    ("086",   304,  1888,   80,  144, None, 0, False, 1, None, 0),
                    ("087",  1120,  1888,  144,  128, (32, 96, 96, 32), 0, False, 1, None, 0),
                    ("088",  1280,  1888,  144,  128, (32, 96, 96, 32), 0, False, 1, None, 0),
                    ("089",  1440,  1888,  144,  128, (32, 96, 96, 32), 0, False, 1, None, 0),
                    ("090",   400,  1904,  128,  112, None, 0, False, 1, None, 0),
                    ("091",   528,  1904,   96,  144, None, 0, False, 1, None, 0),
                    ("092",   640,  1904,  144,  112, (32, 80, 96, 32), 0, False, 1, None, 0),
                    ("093",   800,  1904,  144,  112, (32, 80, 96, 32), 0, False, 1, None, 0),
                    ("094",   960,  1904,  144,  112, (32, 80, 96, 32), 0, False, 1, None, 0),
                    ("095",     0,  1920,  128,   96, (32, 48, 64, 48), 0, False, 1, None, 2),
                    ("096",  1600,  1920,   80,   96, (32, 64, 32, 32), 0, False, 1, None, 0),
                    ("097",  1696,  1920,   80,   96, (32, 64, 32, 32), 0, False, 1, None, 0),
                ],
            },
        },
    },
]

# Mapeamento id_tile → (sheet_file, sx, sy, tile_w, tile_h) — lido pelo TileSpriteManager
OBJECT_SHEET_TILE_MAP: dict[str, tuple] = {}

# Folha "noturna" companheira de uma folha base (22/09/2026, pedido do
# usuário — camada de sprite que liga à noite, ex: lanterna acesa do
# lampião). MESMAS dimensões e MESMAS coordenadas (sx,sy,w,h) de cada
# tile que a folha base — usuário desenhou a versão "ligada" de cada
# objeto exatamente por cima da posição correspondente na folha base,
# deixando o resto do canvas transparente. `TileSpriteManager.
# get_night_overlay_sprite()` usa este dict pra saber qual folha
# companheira existe (se existir) pra um `sheet_file` — NUNCA associar
# isso por tile individual, é uma propriedade da FOLHA inteira. Gate de
# verdade (só objetos com TileType.light_source ganham a camada de
# verdade, mesmo que a folha noturna tenha arte extra desenhada pra
# outras coisas — "próximas interações", ainda não ativadas) fica em
# `ui/tile_render_system.py::get_world_objects()`, não aqui.
NIGHT_SHEET_BY_FILE: dict[str, str] = {
    "TX Village Props with Shadow": "TX Village Props Light",
}

# Folhas animadas de EVENTO de um PORTAL (01/10/2026, Sistema de
# Portais, pedido do usuário) — diferente de NIGHT_SHEET_BY_FILE
# (companheira da FOLHA inteira, 1 frame, condicional à hora do dia):
# aqui é por TILE_ID específico (o portal é 1 elemento só do catálogo,
# não uma família inteira) e disparado por EVENTO de rede
# (PORTAL_ANIM_START/STOP/ARRIVE, ver shared/messages.py), nunca por
# relógio — fica parado no sprite ESTÁTICO de sempre fora desses
# eventos. Cada arquivo é uma tira horizontal em assets/sprites/ (NÃO
# assets/tiles/ — mesma pasta/convenção de animação de personagem,
# `engine/character_animation.py`), com N frames do MESMO w×h do tile
# estático catalogado. "activate" toca ESTICADO pra caber a duração do
# cast (5s hoje) no portal de ORIGEM; "deactivate" toca 1x na duração
# natural do próprio sheet no portal de DESTINO, ao receber alguém (ver
# `engine/portal_anim_state.py` + `client/portal_handlers.py`). Vazio
# até o usuário desenhar a arte e cadastrar a entrada — sem entrada
# aqui, `ui/tile_sprite_manager.py::get_portal_event_frame()` devolve
# None e `ui/tile_render_system.py::get_world_objects()` cai pro
# sprite ESTÁTICO de sempre (fallback gracioso, mesmo espírito de
# character_animation.py pra sprite de personagem ausente).
PORTAL_ACTIVATE_SHEET_BY_TILE_ID: dict[str, str] = {
    "vp_144": "portal_activate",
}
PORTAL_DEACTIVATE_SHEET_BY_TILE_ID: dict[str, str] = {
    "vp_144": "portal_deactivate",
}


def _resolve_collision(col_rect) -> tuple[bool, object]:
    """
    Converte o valor de collision_rect do config para (is_solid, internal_rect).

    Semântica:
      None          → passável (is_solid=False, internal_rect=None)
      "base"        → sólido só no tile base 32×32 (is_solid=True, internal_rect=None)
      "full"        → sprite inteiro sólido (is_solid=True, internal_rect="full")
      (x, y, w, h) → rect explícito (is_solid=True, internal_rect=(x,y,w,h))
    """
    if col_rect is None:
        return False, None          # passável — sem colisão
    if col_rect == "base":
        return True, None           # base tile 32×32 (antigo None)
    return True, col_rect           # "full" ou (x,y,w,h)


def _unpack_override(value, default_vision_height: int) -> tuple:
    """
    Interpreta um valor de collision_overrides.
    Retorna (collision_rect_config, vision_height).

    Formatos suportados:
      None / "base" / "full" / (x,y,w,h)  → usa default_vision_height
      (collision_rect, vision_height)       → vision_height explícito por tile
    """
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        return value[0], value[1]
    return value, default_vision_height


# Sprites de disfarce de Camuflagem saíram daqui pra
# engine/entity_disguise.py (07/08/2026, achado do usuário — isso é
# lógica de SKILL de personagem, não dado de tile/mapa, e forçava o
# servidor a importar este módulo inteiro só por causa delas). Módulo
# final genérico (discover_sprite_variants/get_animated_disguise_frame/
# get_active_appearance_override), não hardcoded pra Camuflagem — ver
# PROBLEMAS_ARQUITETURA.md §12/§13.


def group_tile_ids(group: dict) -> list[str]:
    """IDs completos (prefix + id_suffix) dos tiles de 1 grupo de
    `OBJECT_SHEET_FAMILIES["groups"]` — ponto único de verdade pra essa
    derivação (26/08/2026), usado por `ui/god_mode.py::_render_content`
    (painel da aba Objetos) e pelo pincel aleatório por grupo
    (`_resolve_brush_char`) — antes cada um recalculava o mesmo
    comprehension separadamente. Precisa espelhar EXATAMENTE a mesma
    regra de junção que `_register_catalog_tiles` usa pra cadastrar o
    tile de verdade (`no_prefix_separator`, 03/09/2026) — senão o id
    calculado aqui não bate com nenhum id real em `OBJECT_MAPPING` e o
    grupo inteiro some da aba Objetos, mesmo com os tiles cadastrados
    certos (bug real, ver PROBLEMAS_ARQUITETURA.md §104)."""
    prefix = group["prefix"]
    no_sep = group.get("no_prefix_separator", False)
    if no_sep:
        return [f"{prefix}{entry[0]}" for entry in group["tiles"]]
    return [f"{prefix}_{entry[0]}" for entry in group["tiles"]]


def terrain_zone_of(family: dict, tile_id: str) -> "dict | None":
    """Zona de `family["zones"]` (SHEET_FAMILIES, modo grade) que contém
    `tile_id`, ou None se o tile não está em nenhuma zona nomeada — piso
    parcial/transição sem categoria própria, só seleção manual, nunca
    sorteio automático. `tile_id` no formato `f"{prefix}_{col}_{row}"`
    (`discover_sheet_tiles`) — extrai (col,row) do sufixo. Ponto único
    de verdade, mesmo padrão de `group_tile_ids` (26/08/2026) — usado
    pelo pincel inteligente de terreno do God Mode pra descobrir a
    categoria atual de um tile (grama/terra/transição/piso) antes de
    decidir o que pintar."""
    zones = family.get("zones")
    if not zones:
        return None
    try:
        _, col_s, row_s = tile_id.rsplit("_", 2)
        col, row = int(col_s), int(row_s)
    except (ValueError, IndexError):
        return None
    for zone in zones.values():
        if (col, row) in _zone_cells(zone):
            return zone
    return None


def _zone_cells(zone: dict) -> list:
    """Células `(col,row)` cobertas por uma zona — `zone["cells"]` direto
    (zona simples), ou os 9 tiles de cada quebra-cabeça 3×3 em
    `zone["shapes"]` (zona de transição, ver `resolve_blob3x3_piece`)."""
    if "cells" in zone:
        return zone["cells"]
    cells = []
    for (c0, r0) in zone.get("shapes", {}).values():
        cells.extend((c0 + dc, r0 + dr) for dc in range(3) for dr in range(3))
    return cells


# Autotile posicional "3×3 mínimo" (26-27/08/2026) — este tileset só tem 9
# peças por par de terreno (canto/borda/centro), sem peças de canto INTERNO/
# côncavo como um Blob47 completo teria. Mapeia "quais lados (N/S/E/W) do
# vizinho ainda NÃO são a categoria nova" pro offset (col,row) da peça certa
# dentro do bloco 3×3 (origem = canto superior-esquerdo, ver `zone["shapes"]`
# em SHEET_FAMILIES["TX Tileset Terrain"]["zones"]). Combinação de lados que
# não bate com nenhuma entrada (ex: 2 lados OPOSTOS de fora, ou 3+/isolado —
# o caso mais comum ao clicar 1 tile longe de qualquer área já pintada) não
# tem peça correspondente no asset — `resolve_blob3x3_piece` devolve None e
# quem chama não sobrescreve o vizinho, em vez de forçar uma peça errada
# (era o bug relatado pelo usuário: sorteio cego entre as 9 peças produzia
# um mosaico sem nexo). Referência: Red Blob Games, "Autotiling" — máscara
# de bits N/E/S/W, versão reduzida (sem peças de canto côncavo).
_BLOB3X3_PIECE_OFFSET = {
    # frozenset() (nenhum lado "de fora") NÃO entra aqui — significa
    # "ninguém convertido perto de verdade ainda" (sem contexto real,
    # ex: vizinho diagonal de um clique isolado), não "totalmente
    # cercado pela categoria nova" (esse é o oposto: os 4 lados "de
    # fora" ao mesmo tempo — tratado à parte, vira cura pra tile
    # normal, não uma peça deste catálogo). Deixar de fora garante que
    # `resolve_blob3x3_piece(frozenset())` devolve None e cai pro
    # fallback por direção (`_direction_missing_sides`), não uma peça
    # de centro que nunca deveria aparecer aqui.
    frozenset({"N"}): (1, 0),
    frozenset({"S"}): (1, 2),
    frozenset({"E"}): (2, 1),
    frozenset({"W"}): (0, 1),
    frozenset({"N", "W"}): (0, 0),
    frozenset({"N", "E"}): (2, 0),
    frozenset({"S", "W"}): (0, 2),
    frozenset({"S", "E"}): (2, 2),
}


def resolve_blob3x3_piece(missing_sides: frozenset) -> "tuple | None":
    """Offset `(col,row)` da peça certa dentro de um bloco 3×3 de
    transição, dado o `frozenset` de lados ("N"/"S"/"E"/"W") que ainda
    não são a categoria nova — ou None se a combinação não tem peça no
    asset (ver `_BLOB3X3_PIECE_OFFSET`).
    Ponto único de verdade, mesmo padrão de `group_tile_ids`/
    `terrain_zone_of` — usado pelo pincel inteligente de terreno."""
    return _BLOB3X3_PIECE_OFFSET.get(missing_sides)


def _register_catalog_tiles(sheet_file: str, prefix: str, color: tuple,
                            under: str, tiles: list, default_vh: int,
                            is_bush: bool = False,
                            no_prefix_separator: bool = False,
                            occludes_player: bool = False) -> None:
    """
    Registra um lote de tiles em modo CATÁLOGO (posição/tamanho próprios
    por tile) em OBJECT_MAPPING/OBJECT_CHARS/OBJECT_SHEET_TILE_MAP/
    OBJECT_UNDERLYING — corpo compartilhado entre o modo `"tiles"` plano
    (legado) e cada grupo de `"groups"` (26/08/2026, cor por grupo dentro
    do mesmo arquivo — ver docstring de `discover_object_sheet_tiles`),
    pra não duplicar a lógica de `_resolve_collision`/elevation/etc. em 2
    lugares.

    Cada entrada de `tiles`:
      (id, sx, sy, w, h, col, piso, transpassavel, sort, vision_rect, vision_h, light)
        piso         — int (0/1/2...) ou "t" (transição)
        transpassavel— 1 = jogador de piso inferior passa por trás
        vision_rect  — (13/08/2026) pegada de BLOQUEIO DE VISÃO,
                       independente de `col` (colisão) — mesmo formato
                       (None/"full"/(x,y,w,h)). Opcional, omita pra usar
                       só o tile-âncora (None). Existe pra permitir
                       pegada de visão MAIOR que a de colisão (bush
                       andável bloqueando visão na largura do sprite;
                       árvore com tronco estreito sólido mas copa
                       inteira bloqueando visão) — ver TileType.vision_rect.
        vision_h     — vision_height explícito por tile; omita pra usar
                       default_vh.
        light        — (21/09/2026) tupla (off_x, off_y, w, h,
                       intensity_pct, radius_tiles) pra objeto que emite
                       luz (lampião/tocha/fogueira) — ver docstring de
                       TileType.light_source pro formato completo.
                       Opcional, omita pra objeto sem luz (imensa maioria).

    no_prefix_separator (03/09/2026, pedido do usuário) — pula o "_"
    entre `prefix` e o id de cada peça (`tile_id = f"{prefix}{id_suffix}"`
    em vez de `f"{prefix}_{id_suffix}"`). Opt-in por família/grupo
    (`"no_prefix_separator": True` no dict do grupo/família no catálogo)
    — default False, comportamento de TODA outra família intacto. Serve
    pra catálogo com id sequencial "puro" (ex: "vb001".."vb097" em "TX
    Village Building with Shadow") onde o próprio id_suffix já embute o
    prefixo visualmente (`"vb001"`) e o campo `"prefix"` continua
    dizendo `"vb"` só por padronização/legibilidade do arquivo, não pra
    ser concatenado com separador.
    """
    for entry in tiles:
        n = len(entry)
        id_suffix, sx, sy, tw, th, col_rect_cfg = entry[:6]
        piso_raw       = entry[6] if n > 6 else 0
        transpass      = bool(entry[7]) if n > 7 else False
        sort_val       = entry[8] if n > 8 else 1   # 1=Y-sort (padrão), 0=sem Y-sort
        vision_rect_cfg = entry[9] if n > 9 else None
        vision_h        = entry[10] if n > 10 else default_vh
        light_cfg       = entry[11] if n > 11 else None

        # Interpreta piso: int → elevation normal; "t" → tile de transição
        if piso_raw == "t":
            elev     = 0
            is_trans = True
        else:
            elev     = int(piso_raw)
            is_trans = False

        tile_id = f"{prefix}{id_suffix}" if no_prefix_separator else f"{prefix}_{id_suffix}"
        is_solid, internal_rect = _resolve_collision(col_rect_cfg)
        tile = TileType(
            name=f"ObjSheet_{tile_id}",
            color=color,
            is_solid=is_solid,
            sprite_name=tile_id,
            sprite_px_w=tw,
            sprite_px_h=th,
            collision_rect=internal_rect,
            vision_rect=vision_rect_cfg,
            vision_height=vision_h,
            is_bush=is_bush,
            occludes_player=occludes_player,
            elevation=elev,
            is_transition=is_trans,
            passthrough=transpass,
            no_ysort=(sort_val == 0),
            light_source=light_cfg,
        )
        OBJECT_SHEET_TILE_MAP[tile_id] = (sheet_file, sx, sy, tw, th)
        OBJECT_CHARS.add(tile_id)
        OBJECT_MAPPING[tile_id] = tile
        OBJECT_UNDERLYING[tile_id] = under


def discover_object_sheet_tiles() -> None:
    """
    Para cada entrada em OBJECT_SHEET_FAMILIES, registra TileTypes em
    OBJECT_MAPPING / OBJECT_CHARS / OBJECT_SHEET_TILE_MAP.

    Suporta três modos:
      - Grade uniforme: usa tile_w/tile_h e auto-descobre cols/rows pelo
        PNG. `color_overrides: {(col,row): (r,g,b)}` (26/08/2026) escolhe
        a cor por célula — mirror exato de `collision_overrides` — caindo
        no `color` da família quando a célula não está no dict.
      - Catálogo explícito: usa "tiles" = [(id, sx, sy, w, h, col_rect[, vision_h]), ...],
        1 cor só pra família inteira.
      - Grupos dentro do catálogo (26/08/2026, pedido do usuário — um
        único PNG pode misturar categorias bem diferentes visualmente,
        ex: "árvores"/"arbustos"/"muros" no mesmo arquivo, cada uma
        precisando da PRÓPRIA cor no minimapa): "groups" = {nome:
        {prefix, label, color, underlying_terrain, tiles: [...]}} — cada
        grupo tem sua cor/prefixo/label próprios, mesma forma de tupla
        por tile de "tiles" plano. Só 2 níveis (arquivo→grupo, sem
        override adicional por tile dentro do grupo — decisão do
        usuário: se 1 tile for muito diferente, vira outro grupo).
    """
    import os
    for fam in OBJECT_SHEET_FAMILIES:
        sheet_file = fam["file"]
        path = os.path.join("assets", "tiles", sheet_file + ".png")
        if not os.path.exists(path):
            continue

        prefix   = fam.get("prefix", "")
        color    = fam.get("color", (128, 128, 128))
        under    = fam.get("underlying_terrain", "G")
        default_vh = fam.get("default_vision_height", 0)
        # occludes_player no nível de FAMÍLIA (20/09/2026) cascateia pra
        # TODOS os sub-grupos dela — diferente de is_bush (só 1 grupo
        # precisou até hoje), "Construções" tem dezenas de sub-grupos
        # (1 por tipo de prédio) que devem participar juntos; setar 1 vez
        # na família evita repetir a flag em cada um. Grupo pode ainda
        # sobrescrever individualmente via group.get(...) abaixo.
        default_occludes = fam.get("occludes_player", False)

        if "groups" in fam:
            for group in fam["groups"].values():
                _register_catalog_tiles(
                    sheet_file, group["prefix"], group["color"],
                    group.get("underlying_terrain", under),
                    group["tiles"],
                    group.get("default_vision_height", default_vh),
                    group.get("is_bush", False),
                    group.get("no_prefix_separator", False),
                    group.get("occludes_player", default_occludes),
                )
        elif "tiles" in fam:
            _register_catalog_tiles(sheet_file, prefix, color, under,
                                    fam["tiles"], default_vh,
                                    no_prefix_separator=fam.get("no_prefix_separator", False),
                                    occludes_player=default_occludes)
        else:
            # Modo grade: tile_w × tile_h uniforme, auto-descobre cols/rows
            sw, sh_px   = _read_png_size(path)
            tw, th      = fam["tile_w"], fam["tile_h"]
            cols        = sw    // tw
            rows        = sh_px // th
            default_col = fam.get("default_collision", None)
            overrides   = fam.get("collision_overrides", {})
            color_overrides = fam.get("color_overrides", {})
            for row in range(rows):
                for col in range(cols):
                    tile_id        = f"{prefix}_{col}_{row}"
                    raw            = overrides.get((col, row), default_col)
                    col_rect_cfg, vh = _unpack_override(raw, default_vh)
                    is_solid, internal_rect = _resolve_collision(col_rect_cfg)
                    cell_color = color_overrides.get((col, row), color)
                    tile = TileType(
                        name=f"ObjSheet_{prefix}_{col}_{row}",
                        color=cell_color,
                        is_solid=is_solid,
                        sprite_name=tile_id,
                        sprite_px_w=tw,
                        sprite_px_h=th,
                        collision_rect=internal_rect,
                        vision_height=vh,
                    )
                    OBJECT_SHEET_TILE_MAP[tile_id] = (sheet_file, col * tw, row * th, tw, th)
                    OBJECT_CHARS.add(tile_id)
                    OBJECT_MAPPING[tile_id] = tile
                    OBJECT_UNDERLYING[tile_id] = under


# Registra automaticamente ao importar o módulo
discover_sprite_objects()
discover_sheet_tiles()
discover_object_sheet_tiles()
