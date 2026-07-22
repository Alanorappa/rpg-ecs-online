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

    # ── Altura visual (para Fog of War) ───────────────────────────────────────
    # Determina se o elemento bloqueia a linha de visão do jogador no FOV.
    #
    #   0 → plano (terreno, água, pedras rasas) — não interfere na visão
    #   1 → médio (arbustos, lápides, bancos)   — não interfere na visão
    #   2 → alto (árvores, paredes, montanhas)  — bloqueia o campo de visão
    #
    # Apenas vision_height >= 2 é considerado bloqueante pelo FogSystem.
    vision_height: int = 0

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
        "file":                "TX Tileset Grass",
        "prefix":              "txg",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Grama TX",
        "color":               (58, 100, 48),
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
        "file":                "TX Mud",
        "prefix":              "txm",
        "tile_w":              32,
        "tile_h":              32,
        "is_solid":            False,
        "label":               "Terra Seca TX",
        "color":               (158, 90, 36),
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
#       # (id,        sx,  sy,   w,   h,   collision_rect,  vision_height)
#       ("banco",      0,   0,  64,  32,   None,            0),  # raso, não bloqueia FOV
#       ("lampiao",   64,   0,  32,  64,   "full",          2),  # alto, bloqueia FOV
#       ("caixao",    96,   0,  32,  64,   None,            1),  # médio, não bloqueia
#   ],
#   O 7º campo (vision_height) é opcional — omita para usar default_vision_height.
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
        "file":                "TX Grades",
        "prefix":              "gc",
        "tile_w":              32,
        "tile_h":              64,
        "label":               "Grade Cemitério",
        "color":               (90, 80, 70),
        "default_collision":   "full",
        "collision_overrides": {
            (0, 0): "base", 
            (0, 1): "full", 
            (0, 2): "full", 
            (0, 3): "full",
            (1, 0): "base", 
            (1, 3): "base",
            (2, 0): "base", 
            (2, 3): "base",
            (3, 0): "base", 
            (3, 3): "base",
            (4, 0): "base", 
            (4, 3): "base",
            (5, 0): "base", 
            (5, 3): "base",
            (6, 0): "base", 
            (6, 3): "base",
            (7, 0): "base", 
            (7, 1): "full", 
            (7, 2): "full", 
            (7, 3): "full",
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
             ("chest",           96,   0,  32,  64,           "base"),
             ("box1",           160,   0,  32,  64,           "base"),
             ("box2",           160,  64,  32,  64,           "base"),
             ("barrel",         160, 128,  32,  64,           "base"),
             ("urn",            160, 192,  32,  64,           "base"),
             ("pot",            160, 258,  32,  64,           "base"),
             ("urn2",           160, 320,  32,  64,           "base"),
             ("soulstone",      224,   0,  32,  64,           "base"),
             ("soulstone2",     224,  64,  32,  96,           "base"),
             ("soulstone3",     224, 160,  32,  64,           "base"),
             ("gravestone",     224, 224,  32,  64,           "base"),
             ("cross",          224, 288,  32,  64,           "base"),
             ("bench1",         288,   0,  64,  64, ( 0, 32, 64, 32)),
             ("tomb1",          288,  64,  64,  64, ( 0, 32, 64, 32)),
             ("tomb2",          288, 128,  32,  96, ( 0, 32, 32, 64)),
             ("gravestone2",    288, 224,  32,  64,           "base"),
             ("cube",           288, 288,  32,  64,           "base"),
             ("opentomb",       288, 384,  64,  32,           "full"),
             ("opentomb2",      288, 416,  64,  32, ( 0,  0, 32, 32)),
             ("sheets1",         32, 256,  64,  64, ( 0, 32, 64, 32)),
             ("bench2",         384,   0,  32,  64,           "full"),
             ("bench3",         384,  96,  32,  64,           "full"),
             ("pillar1",        352, 160,  32,  96,           "base"),
             ("pillar2",        416, 192,  32,  64,           "base"),
             ("statue",         416,   0,  96,  96, (32, 64, 32, 32)),
             ("lighting",       448,  96,  32,  64,           "base"),
             ("pit",            416, 352,  64,  64,           "full"),
             ("bigrock",          0, 416,  64,  64,           "full"),
             ("rock1",            0, 480,  32,  32,             None),
             ("rock2",           32, 480,  32,  32,             None),
             ("rock3",           64, 480,  32,  32,           "full"),
             ("rock4",           96, 480,  32,  32,           "full"),
             ("rock5",          128, 480,  32,  32,           "full"),
             ("rock6",          160, 480,  32,  32,           "full"),
             ("closedoor",        0,  96,  96,  64,           "full"),
             ("opendoor",         0,  160, 96,  64,             None),

         ],
     },

     {
         "file":   "TX Plant",
         "prefix": "pl",
         "label":  "Vegetação",
         "color":  (96, 99, 21),
         "underlying_terrain": "G",
         "tiles": [
             # (id,              sx,  sy,   w,   h,   collision_rect)  
             ("tree1",             0,   0, 160, 160,   ( 64, 128, 32,32)),
             ("tree2",           160,   0,  96, 160,   ( 32, 128, 32,32)),
             ("tree3",           256,   0,  96, 160,   ( 32, 128, 32,32)),
             ("bush1",             0, 192,  32,  32,   (  0,   0, 32,32)),
             ("bush2",            32, 192,   32, 32,   (  0,   0, 32,32)),
             ("bush3",            64, 160,  64, 64,    ( 32,  32, 32,32)),
             ("bush4",           128, 160,  64,  64,   ( 32,  32, 32,32)),
             ("bush5",           192, 160,  64,  64,   ( 32,  32, 32,32)),
             ("bush6",           256, 160,  64,  64,   (  0,  32, 32,32)),
             ("grassblade1",       0, 224,  32,  32,                None),
             ("grassblade2",      32, 224,  32,  32,                None),
             ("grassblade3",      64, 224,  32,  32,                None),
             ("grassblade4",      96, 224,  32,  32,                None),
             ("grassblade5",       0, 256,  32,  32,                None),
             ("grassblade6",      32, 256,  32,  32,                None),
             ("grassblade7",      64, 256,  32,  32,                None),
             ("grassblade8",      96, 256,  32,  32,                None),
             ("grassblade9",       0, 288,  32,  32,                None),
             ("grassblade10",     32, 288,  32,  32,                None),
             ("grassblade11",     64, 288,  32,  32,                None),
             ("grassblade12",     96, 288,  32,  32,                None),
             ("grassblade13",      0, 322,  32,  32,                None),
             ("grassblade14",     32, 322,  32,  32,                None),
             ("grassblade15",     64, 322,  32,  32,                None),
             ("grassblade16",     96, 322,  32,  32,                None),
             
         ]

     },
{
         "file":   "TX Tileset Cave Objects",
         "prefix": "cv",
         "label":  "Caverna",
         "color":  (41, 39, 55),
         "underlying_terrain": ",",
         "tiles": [
             # (id,              sx,  sy,  w,   h,     collision_rect)  
             ("cv1",             0,   0,   32,  64,    "full"),
             ("cv2",            32,   0,   32,  64,    "full"),
             ("cv3",            64,   0,   32,  64,    (0, 0, 32, 32)),
             ("cv4",            96,   0,   32,  64,    "full"),                                       
             ("cv5",           128,   0,   32,  64,    "full"),
             ("cv6",           160,   0,   32,  32,    "full"),
             ("cv7",           192,   0,   32,  32,    "full"),
             ("cv8",           160,  32,   32,  32,    "full"),
             ("cv9",           192,  32,   32,  32,    "full"),
             ("cv28",          224,   0,   32,  32,    "full"),
             ("cv29",          224,  64,   32,  64,    "full"),
             ("cv10",          224,  32,   32,  32,    "full"),
             ("cv11",            0,  64,   32,  32,    "full"),
             ("cv12",           32,  64,   32,  32,    "full"),
             ("cv13",           64,  64,   32,  32,      None),
             ("cv14",           96,  64,   32,  32,    "full"),
             ("cv15",          124,  64,   32,  32,    "full"),
             ("cv16",          160,  64,   32,  64,    "full"),
             ("cv17",          192,  64,   32,  64,    "full"),
             ("cv18",            0,  96,   32,  32,      None),
             ("cv19",           32,  96,   32,  64,    "base"),
             ("cv20",           64,  96,   32,  64,    "base"),
             ("cv21",           96,  96,   32,  64,    "base"),
             ("cv22",          128,  96,   32,  32,      None),
             ("cv23",            0, 128,   32,  32,    "full"),
             ("cv24",          128, 128,   32,  32,    "full"),
             ("cv25",          160, 128,   32,  32,    "full"),
             ("cv26",          192, 128,   32,  32,    "full"),
             ("cv27",          224, 128,   32,  32,    "full"),                                      

         ]

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
            ("face_C8",  160, 256,  32,  64,  "full", 0,  0),
            ("face_C9",  384, 320,  32,  64,  "full", 0,  0),
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
        "file":   "TX Medieval Mansion",
        "prefix": "ms",
        "label":  "Medieval Mansion",
        "color":  (120, 100, 80),
        "underlying_terrain": ".",
        "tiles": [
            # (id,   sx,  sy,    w,    h,   col,                 piso, transp,   sort)
            ("ms1",   0,  32,   64,  192,   (  0,  96,  64,  96), 1,    1,      1),
            ("ms2",  64,   0,  128,  128,   None,                 1,    1,      1),
            ("ms3",  64,  96,  128,  128,   "full",               1,    1,      1),
            ("ms4",  64, 224,  128,   32,   None,                 1,    1,      1),
            ("ms5", 192,  32,   64,  192,   (  0,  96,  64,  96),  1,    1,      1),
        ],
    },
]

# Mapeamento id_tile → (sheet_file, sx, sy, tile_w, tile_h) — lido pelo TileSpriteManager
OBJECT_SHEET_TILE_MAP: dict[str, tuple] = {}


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


def discover_camouflage_variants() -> list[str]:
    """Sufixos de variantes de disfarce da Camuflagem disponíveis em assets/sprites/.

    Cada variante precisa do par camuflagem_idle{suf}.png + camuflagem_run{suf}.png.
    Variante base = sufixo "" (camuflagem_idle.png/camuflagem_run.png); variantes
    extras seguem _2, _3, _4... Descoberta dinâmica: só checa arquivos no disco,
    sem pygame — adicionar uma nova variante não exige mudança de código.
    """
    import os
    from paths import resource_path

    if hasattr(discover_camouflage_variants, "_cache"):
        return discover_camouflage_variants._cache

    variants: list[str] = []
    base_dir = resource_path(os.path.join("assets", "sprites"))
    n = 1
    suf = ""
    while True:
        idle_path = os.path.join(base_dir, f"camuflagem_idle{suf}.png")
        run_path  = os.path.join(base_dir, f"camuflagem_run{suf}.png")
        if os.path.exists(idle_path) and os.path.exists(run_path):
            variants.append(suf)
            n += 1
            suf = f"_{n}"
        else:
            break

    discover_camouflage_variants._cache = variants
    return variants


_CAMOUFLAGE_FRAME_MS = 65  # duração de cada frame da animação de correr (-15% de velocidade vs 55ms)


def get_camouflage_disguise_frame(variant_suffix: str, moving: bool, anim_time_ms: int):
    """Retorna o Surface do frame atual do disfarce de Camuflagem.

    moving=False → frame único de camuflagem_idle{suf}.png (32×32).
    moving=True  → frame cíclico de camuflagem_run{suf}.png (sheet 32×H, N frames
                   lado a lado), avançando 1 frame a cada _CAMOUFLAGE_FRAME_MS.
    Usa cache interno por (kind, variante). Retorna None se o arquivo não existir.
    """
    import pygame
    import os
    from paths import resource_path

    if not hasattr(get_camouflage_disguise_frame, "_cache"):
        get_camouflage_disguise_frame._cache = {}
    cache = get_camouflage_disguise_frame._cache

    kind = "run" if moving else "idle"
    key  = (kind, variant_suffix)
    if key not in cache:
        fname      = f"camuflagem_{kind}{variant_suffix}.png"
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
        cache[key] = frames

    frames = cache[key]
    if not frames:
        return None
    if not moving:
        return frames[0]
    idx = (anim_time_ms // _CAMOUFLAGE_FRAME_MS) % len(frames)
    return frames[idx]


def discover_object_sheet_tiles() -> None:
    """
    Para cada entrada em OBJECT_SHEET_FAMILIES, registra TileTypes em
    OBJECT_MAPPING / OBJECT_CHARS / OBJECT_SHEET_TILE_MAP.

    Suporta dois modos:
      - Grade uniforme: usa tile_w/tile_h e auto-descobre cols/rows pelo PNG.
      - Catálogo explícito: usa "tiles" = [(id, sx, sy, w, h, col_rect[, vision_h]), ...]
    """
    import os
    for fam in OBJECT_SHEET_FAMILIES:
        sheet_file = fam["file"]
        path = os.path.join("assets", "tiles", sheet_file + ".png")
        if not os.path.exists(path):
            continue

        prefix   = fam["prefix"]
        color    = fam["color"]
        under    = fam.get("underlying_terrain", "G")
        default_vh = fam.get("default_vision_height", 0)

        if "tiles" in fam:
            # Modo catálogo:
            # (id, sx, sy, w, h, col, piso, transpassavel)
            #   piso         — int (0/1/2...) ou "t" (transição)
            #   transpassavel— 1 = jogador de piso inferior passa por trás
            for entry in fam["tiles"]:
                n = len(entry)
                id_suffix, sx, sy, tw, th, col_rect_cfg = entry[:6]
                piso_raw    = entry[6] if n > 6 else 0
                transpass   = bool(entry[7]) if n > 7 else False
                sort_val    = entry[8] if n > 8 else 1   # 1=Y-sort (padrão), 0=sem Y-sort

                # Interpreta piso: int → elevation normal; "t" → tile de transição
                if piso_raw == "t":
                    elev     = 0
                    is_trans = True
                else:
                    elev     = int(piso_raw)
                    is_trans = False

                tile_id = f"{prefix}_{id_suffix}"
                is_solid, internal_rect = _resolve_collision(col_rect_cfg)
                tile = TileType(
                    name=f"ObjSheet_{tile_id}",
                    color=color,
                    is_solid=is_solid,
                    sprite_name=tile_id,
                    sprite_px_w=tw,
                    sprite_px_h=th,
                    collision_rect=internal_rect,
                    vision_height=default_vh,
                    elevation=elev,
                    is_transition=is_trans,
                    passthrough=transpass,
                    no_ysort=(sort_val == 0),
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
                    tile_id        = f"{prefix}_{col}_{row}"
                    raw            = overrides.get((col, row), default_col)
                    col_rect_cfg, vh = _unpack_override(raw, default_vh)
                    is_solid, internal_rect = _resolve_collision(col_rect_cfg)
                    tile = TileType(
                        name=f"ObjSheet_{prefix}_{col}_{row}",
                        color=color,
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
