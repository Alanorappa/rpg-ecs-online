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
WALL_TILE     = TileType("Wall",       (115, 108,  98), is_solid=True)   # Parede da cidade
RUINS_WALL    = TileType("RuinsWall",  ( 88,  65,  42), is_solid=True)   # Parede das ruínas
CAVE_WALL     = TileType("CaveWall",   ( 48,  38,  28), is_solid=True)   # Parede da caverna
MOUNTAIN_TILE = TileType("Mountain",   ( 62,  55,  48), is_solid=True)   # Montanha / borda
WATER_TILE    = TileType("Water",      ( 40,  88, 165), is_solid=True)   # Água
TREE_TILE = TileType("Tree", (28, 72, 28), is_solid=True,
                    # sprites=["tree_1"],
                    # overlay_height=32
                     )   # sprite = 32×64px   # Árvore
BUSH_TILE     = TileType("Bush",       ( 52, 108,  42), is_solid=True)   # Arbusto
ROCK_TILE     = TileType("Rock",       (100,  90,  78), is_solid=True)   # Pedra / rocha / lápide

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
    rect = tile.collision_rect
    if rect is None:
        return [(0, 0)]

    sprite_h = tile.sprite_px_h if tile.sprite_px_h > 0 else TILE_SIZE
    sprite_w = tile.sprite_px_w if tile.sprite_px_w > 0 else TILE_SIZE

    if rect == "full":
        rx, ry, rw, rh = 0, 0, sprite_w, sprite_h
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


def _discover_sprite_objects(
    prefix: str,
    base_name: str,
    base_color: tuple,
    is_solid: bool = True,
    collision_rect: tuple | str | None = None,
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
    # prefixo  nome-interno     cor RGB          sólido  label        collision_rect
    ("t",  "SpriteTree",        ( 28,  72,  28),  True,  "Árvore",              None),     # base 32×32 apenas
    ("b",  "SpriteBush",        ( 52, 108,  42),  True,  "Arbusto",             None),     # base 32×32 apenas
    ("g",  "SpriteGrade",       ( 80, 120,  60),  True,  "Grade",               None),     # base 32×32 apenas
    ("s",  "SpriteGrave",       (128, 128, 128),  True,  "Grave",               "full"),    # sprite inteiro sólido
    ("y",  "SpriteGravesHori",  (128, 128, 128),  True,  "GraveHorizontal",     (0, 32, 64, 32)), # base 64px sólido
    ("l",  "SpriteGravestone",  (128, 128, 128),  True,  "GraveStone",          None), # base 32×32 apenas
    ("o",  "SpriteObj",         (128, 128, 128),  True,  "Bench",               "full"),
]


def discover_sprite_objects() -> None:
    """Registra todos os sprites de objetos encontrados em assets/tiles/."""
    for prefix, base_name, color, is_solid, _label, collision_rect in SPRITE_FAMILIES:
        _discover_sprite_objects(prefix, base_name, color, is_solid, collision_rect)


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
        "file":                "TX Tileset Grass",
        "prefix":              "txg",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Grama TX",
        "color":               (58, 100, 48),
        "underlying_terrain":  "G",
    },
]

# Mapeamento id_tile → (sheet_file, sx, sy, tile_w, tile_h) — lido pelo TileSpriteManager
SHEET_TILE_MAP: dict[str, tuple] = {}


def discover_sheet_tiles() -> None:
    """
    Para cada entrada em SHEET_FAMILIES, lê as dimensões do PNG e registra
    um TileType por célula em OBJECT_MAPPING / OBJECT_CHARS / SHEET_TILE_MAP.
    """
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
        under     = fam.get("underlying_terrain", "G")
        tw, th    = fam["tile_w"], fam["tile_h"]
        for row in range(rows):
            for col in range(cols):
                tile_id = f"{prefix}_{col}_{row}"
                sx = col * tw
                sy = row * th
                tile = TileType(
                    name=f"Sheet_{prefix}_{col}_{row}",
                    color=color,
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
#       # (id,        sx,  sy,   w,   h,   collision_rect)
#       ("banco",      0,   0,  64,  32,   None),     # 64×32, só base
#       ("lampiao",   64,   0,  32,  64,   "full"),   # 32×64, inteiro sólido
#       ("caixao",    96,   0,  32,  64,   None),     # 32×64, só base
#   ],
#
# Campos comuns:
#   file               — nome do PNG sem extensão, em assets/tiles/
#   prefix             — prefixo curto para IDs internos
#   label              — nome exibido no God Mode (aba Objetos)
#   color              — cor RGB de fallback
#   underlying_terrain — char de terreno subjacente (padrão "G")
#
OBJECT_SHEET_FAMILIES: list[dict] = [
    {
        "file":                "TX Grades",
        "prefix":              "gc",
        "tile_w":              32,
        "tile_h":              64,
        "label":               "Grade Cemitério",
        "color":               (90, 80, 70),
        "default_collision":   None,
        "collision_overrides": {
            (0, 0): None, (0, 1): "full", (0, 2): "full", (0, 3): "full",
            (1, 0): None, (1, 3): None,
            (2, 0): None, (2, 3): None,
            (3, 0): None, (3, 3): None,
            (4, 0): None, (4, 3): None,
            (5, 0): None, (5, 3): None,
            (6, 0): None, (6, 3): None,
            (7, 0): None, (7, 1): "full", (7, 2): "full", (7, 3): "full",
        },
        "underlying_terrain":  "G",
    },
    
     {
         "file":   "TX Props",
         "prefix": "pr",
         "label":  "Adereços",
         "color":  (90, 80, 70),
         "underlying_terrain": "G",
         "tiles": [
             # (id,              sx,  sy,   w,   h,   collision_rect)
             ("chest",           97,   0,  32,  64,   None),
             ("box1",           161,   0,  32,  64,   None),
             ("box2",           161,  65,  32,  64,   None),
             ("barrel",         161, 129,  32,  64,   None),
             ("urn",            161, 193,  32,  64,   None),
             ("pot",            161, 258,  32,  64,   None),
             ("urn2",           161, 321,  32,  64,   None),
             ("soulstone",      225,   0,  32,  64,   None),
             ("soulstone2",     225,  65,  32,  96,   None),
             ("soulstone3",     225, 161,  32,  64,   None),
             ("gravestone",     225, 225,  32,  64,   None),
             ("cross",          225, 289,  32,  64,   None),
             ("bench1",         289,   0,  64,  64,   (0, 32, 64, 32)),
             ("tomb1",          289,  65,  64,  64,   (0, 32, 64, 32)),
             ("tomb2",          289, 129,  32,  96,   (0, 32, 32, 64)),
             ("gravestone2",    289, 225,  32,  64,   None),
             ("cube",           289, 289,  32,  64,   None),
             ("opentomb",       289, 385,  64,  32,   "full"),
             ("opentomb2",      289, 417,  64,  32,   (0, 0, 32, 32)),
            # ("soulstone3",     225, 161,  32,  96,   None),
         ],
     },
]

# Mapeamento id_tile → (sheet_file, sx, sy, tile_w, tile_h) — lido pelo TileSpriteManager
OBJECT_SHEET_TILE_MAP: dict[str, tuple] = {}


def discover_object_sheet_tiles() -> None:
    """
    Para cada entrada em OBJECT_SHEET_FAMILIES, registra TileTypes em
    OBJECT_MAPPING / OBJECT_CHARS / OBJECT_SHEET_TILE_MAP.

    Suporta dois modos:
      - Grade uniforme: usa tile_w/tile_h e auto-descobre cols/rows pelo PNG.
      - Catálogo explícito: usa "tiles" = [(id, sx, sy, w, h, collision_rect), ...]
    """
    import os
    for fam in OBJECT_SHEET_FAMILIES:
        sheet_file = fam["file"]
        path = os.path.join("assets", "tiles", sheet_file + ".png")
        if not os.path.exists(path):
            continue

        prefix = fam["prefix"]
        color  = fam["color"]
        under  = fam.get("underlying_terrain", "G")

        if "tiles" in fam:
            # Modo catálogo: cada tile tem (id, sx, sy, w, h, collision_rect)
            for entry in fam["tiles"]:
                id_suffix, sx, sy, tw, th, col_rect = entry
                tile_id = f"{prefix}_{id_suffix}"
                tile = TileType(
                    name=f"ObjSheet_{tile_id}",
                    color=color,
                    is_solid=True,
                    sprite_name=tile_id,
                    sprite_px_w=tw,
                    sprite_px_h=th,
                    collision_rect=col_rect,
                )
                OBJECT_SHEET_TILE_MAP[tile_id] = (sheet_file, sx, sy, tw, th)
                OBJECT_CHARS.add(tile_id)
                OBJECT_MAPPING[tile_id] = tile
                OBJECT_UNDERLYING[tile_id] = under
        else:
            # Modo grade: tile_w × tile_h uniforme, auto-descobre cols/rows
            sw, sh_px   = _read_png_size(path)
            tw, th      = fam["tile_w"], fam["tile_h"]
            cols        = sw    // tw
            rows        = sh_px // th
            default_col = fam.get("default_collision", None)
            overrides   = fam.get("collision_overrides", {})
            for row in range(rows):
                for col in range(cols):
                    tile_id  = f"{prefix}_{col}_{row}"
                    col_rect = overrides.get((col, row), default_col)
                    tile = TileType(
                        name=f"ObjSheet_{prefix}_{col}_{row}",
                        color=color,
                        is_solid=True,
                        sprite_name=tile_id,
                        sprite_px_w=tw,
                        sprite_px_h=th,
                        collision_rect=col_rect,
                    )
                    OBJECT_SHEET_TILE_MAP[tile_id] = (sheet_file, col * tw, row * th, tw, th)
                    OBJECT_CHARS.add(tile_id)
                    OBJECT_MAPPING[tile_id] = tile
                    OBJECT_UNDERLYING[tile_id] = under


# Registra automaticamente ao importar o módulo
discover_sprite_objects()
discover_sheet_tiles()
discover_object_sheet_tiles()
