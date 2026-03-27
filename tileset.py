# tileset.py
from dataclasses import dataclass, field
from typing import List

@dataclass
class TileType:
    """Define as propriedades de um tipo de tile."""
    name:           str
    color:          tuple        # Cor RGB — usada como fallback quando sem sprite
    is_solid:       bool  = False
    sprites:        List[str] = field(default_factory=list)
    overlay_height: int   = 0    # Pixels EXTRAS acima do tile base (0 = tile normal 32×32).
    #                              Ex.: overlay_height=32 → sprite 32×64 (1 tile de base + 1 acima).
    #                              Tiles com overlay_height > 0 são Y-sortados com as entidades:
    #                              o player passa na frente ou atrás dependendo do Y relativo.
    #
    # sprites — lista de nomes de arquivo (sem extensão) em assets/tiles/
    # Exemplo: sprites=["grass_1", "grass_2"],  overlay_height=0
    #          sprites=["tree_1",  "tree_2"],    overlay_height=32
    # Deixe sprites=[] para usar apenas cor.

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

# ── Mapeamento de caracteres CSV → TileType ───────────────────────────────────
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
    "t": TREE_TILE,
    "b": BUSH_TILE,
    "k": ROCK_TILE,
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

# Tamanho global do tile em pixels
TILE_SIZE = 32
