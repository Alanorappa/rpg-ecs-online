# Documentação do Projeto — RPG ECS

> RPG de estilo Tibia/WoW em Python + Pygame com arquitetura ECS.

---

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Arquitetura ECS](#2-arquitetura-ecs)
3. [Como Criar Itens](#3-como-criar-itens)
4. [Como Criar Inimigos](#4-como-criar-inimigos)
5. [Como Criar Quests](#5-como-criar-quests)
6. [Como Criar Mapas](#6-como-criar-mapas)
7. [Como Criar Skills do Jogador](#7-como-criar-skills-do-jogador)
8. [Como Criar Talentos](#8-como-criar-talentos)
9. [Como Criar Lojas e Mercadores](#9-como-criar-lojas-e-mercadores)
10. [Como Criar Receitas de Crafting](#10-como-criar-receitas-de-crafting)
11. [Sistema de Sons](#11-sistema-de-sons)
12. [Referência de Componentes](#12-referência-de-componentes)
13. [Referência de Arquivos](#13-referência-de-arquivos)

---

## 1. Visão Geral

| Propriedade       | Valor                          |
|-------------------|-------------------------------|
| Linguagem         | Python 3.9+                   |
| Biblioteca        | Pygame 2.x                    |
| Resolução interna | 1280 × 720 px                 |
| FPS alvo          | 60                            |
| Tamanho do tile   | 32 × 32 px                    |
| Arquitetura       | ECS (Entity-Component-System) |

---

## 2. Arquitetura ECS

O padrão ECS separa **dados** de **lógica**:

- **Entidade** → `int` (apenas um ID)
- **Componente** → objeto de dados puro (sem lógica de jogo)
- **Sistema** → lógica que opera em entidades com determinados componentes

```
world.py          ← registro ECS (entidades + componentes)
components.py     ← todos os componentes de dados
systems.py        ← todos os sistemas de lógica (4500+ linhas)
entity_factory.py ← funções que montam entidades com seus componentes
game.py           ← loop principal, HUD, entrada
```

### Regras para novos sistemas

```python
from systems import System

class MeuSistema(System):
    def __init__(self, world):
        self.world     = world
        self.world_surf = None   # atribuído pelo engine por frame
        self.hud_surf   = None

    def update(self, events, dt):
        for eid, comp_a, comp_b in self.world.get_entities_with(CompA, CompB):
            ...

    def render(self, cam_x=0, cam_y=0):
        ...  # desenha em self.world_surf (espaço de mundo) ou self.hud_surf (HUD)
```

---

## 3. Como Criar Itens

**Arquivo:** `loot_tables.py`

### Schema de Item

```python
Item(
    name        = "Nome do Item",     # string — exibido na UI
    item_type   = "weapon",           # ver tabela abaixo
    slot        = "mainhand",         # ver tabela abaixo
    modifiers   = [],                 # lista de Modifier (ver abaixo)
    rarity      = "common",           # "common"|"uncommon"|"rare"|"epic"
    value       = 10,                 # preço base em ouro
    # --- campos de arma ---
    damage_min  = 5,
    damage_max  = 12,
    attack_speed= 1.6,                # segundos por ataque
    subtype     = "Sword",            # ver tabela abaixo
    two_handed  = False,
    # --- proc (opcional) ---
    proc        = {
        "attribute": "attack_power",
        "value":     24,
        "duration":  16.0,
        "chance":    0.25,
        "label":     "Fúria do Guerreiro",
    },
    # --- consumível ---
    consumable  = {"heal_instant": 50, "ooc_only": False},
    max_stack   = 1,                  # >1 para materiais/consumíveis
)
```

### Tipos e Slots

| `item_type`   | `slot` válidos                                                        |
|---------------|-----------------------------------------------------------------------|
| `"weapon"`    | `"mainhand"` `"offhand"`                                              |
| `"shield"`    | `"offhand"`                                                           |
| `"armor"`     | `"head"` `"chest"` `"shoulders"` `"gloves"` `"boots"` `"wrists"`      |
| `"jewelry"`   | `"ring"` `"neck"`                                                     |
| `"consumable"`| `""` (sem slot)                                                       |
| `"material"`  | `""` (sem slot)                                                       |
| `"recipe"`    | `""` (sem slot)                                                       |

### Subtipos de arma (`subtype`)

`"Sword"` `"Club"` `"Wand"` `"Mace"` `"Axe"` `"Hammer"` `"Staff"` `"Scepter"` `"Dagger"`

### Modificadores (`Modifier`)

```python
from components import Modifier

Modifier(
    attribute = "attack_power",   # ver atributos abaixo
    value     = 15,               # valor
    type      = "flat",           # "flat" (soma) | "percentage" (multiplica)
)
```

**Atributos válidos:** `stamina` `armor` `attack_power` `spell_power` `haste_rating` `crit_rating` `hit_rating` `dodge_rating` `parry_rating` `block_rating` `block_value` `attack_interval`

### Exemplo completo — adicionar uma espada nova

```python
# loot_tables.py — dicionário _T (Template Items)
_T["espada_llama"] = lambda: Item(
    "Espada Chama",
    "weapon", "mainhand",
    modifiers=[
        Modifier("attack_power", 20, "flat"),
        Modifier("crit_rating",  0.05, "flat"),
    ],
    rarity="rare", value=250,
    damage_min=18, damage_max=28,
    attack_speed=1.4, subtype="Sword",
    proc={
        "attribute": "attack_power",
        "value":     30,
        "duration":  10.0,
        "chance":    0.15,
        "label":     "Chama Interior",
    }
)
```

### Adicionar na tabela de loot de inimigo

```python
# loot_tables.py — LOOT_TABLES ou MOB_LOOT_TABLES
("melee", "elite"): [
    (_T["espada_llama"], 0.03),   # 3% de chance
    ...
],

# Por nome de mob específico:
MOB_LOOT_TABLES["Goblin Chefe"] = [
    (_T["espada_llama"], 0.10),   # 10% de chance
]
```

### Item de quest (drop condicional)

```python
# quests_data.py — QUEST_ITEMS
QUEST_ITEMS["Escama de Dragão"] = lambda: Item(
    "Escama de Dragão", "material",
    slot=None, rarity="uncommon", value=20, max_stack=5
)
```

---

## 4. Como Criar Inimigos

### Passo 1 — Definir o mob (`mob_definitions.py`)

```python
MOB_TABLE["Kobold Arqueiro"] = {
    "race":         "Humanoide",     # "Fera"|"Humanoide"|"Morto-Vivo"|"Orc"|"Troll"|"Dragão"
    "entity_class": "Hunter",        # "Warrior"|"Mage"|"Warlock"|"Hunter"
    "is_ranged":    True,
    "color":        (180, 120, 60),  # cor do retângulo (sem sprite)
    "sounds": {
        "aggro":          "mob_kobold_aggro",   # base name do .ogg (sem extensão)
        "death":          "mob_kobold_death",
        "attack_ranged":  "mob_kobold_attack",
        "crit":           None,                 # None = sem som
    },
}
```

### Passo 2 — Adicionar loot (`loot_tables.py`)

```python
MOB_LOOT_TABLES["Kobold Arqueiro"] = [
    (_T["worn_boots"], 0.08),
    (_T["bone_arrow"],  0.40),
]
```

### Passo 3 — Colocar no mapa (`maps/map_X_entities.json`)

```json
{
  "spawn_zones": [
    {
      "x": 120, "y": 80,
      "radius": 25,
      "respawn_cooldown": 120,
      "level_min": 5, "level_max": 10,
      "race": "Humanoide",
      "spawns": [
        { "type": "ranged", "tier": "normal", "count": 4 },
        { "type": "melee",  "tier": "elite",  "count": 1 }
      ]
    }
  ]
}
```

### Tiers de inimigo

| Tier       | Multiplicador HP | Multiplicador dano | Cor HUD |
|------------|------------------|--------------------|---------|
| `"normal"` | 1×               | 1×                 | branco  |
| `"elite"`  | 2×               | 1.5×               | amarelo |
| `"rare"`   | 5×               | 2×                 | laranja |
| `"boss"`   | 15×              | 3×                 | vermelho|

### Habilidades de inimigo (`enemy_abilities_data.py`)

```python
from enemy_abilities_data import AbilityDef, ABILITY_DEFS, MOB_ABILITIES

# 1. Definir a habilidade
ABILITY_DEFS["web_shot"] = AbilityDef(
    name         = "Teia Venenosa",
    effect_type  = "slow",      # chave em status_effects_data.EFFECT_DEFS
    duration     = 6.0,
    magnitude    = 0.5,         # 50% de lentidão
    tick_interval= 0.0,
    range_tiles  = 5,
)

# 2. Associar a uma raça ou classe
MOB_ABILITIES["Aranha"] = [
    ("web_shot", 12.0),   # (ability_id, cooldown_segundos)
]
```

---

## 5. Como Criar Quests

**Arquivo:** `quests_data.py`

### Schema de Quest

```python
QuestDef(
    title       = "Nome da Quest",
    description = "Texto exibido ao aceitar.",
    objectives  = (                    # tupla de ObjectiveDef
        ObjectiveDef(...),
    ),
    reward      = QuestReward(xp=200, gold=15),
    auto_start  = False,   # True = começa sem NPC
    repeatable  = False,   # True = pode completar várias vezes
    requires    = (),      # ("quest_id_anterior",) para encadeamento
    next_quest  = "",      # quest_id iniciada automaticamente ao completar
    level_req   = 0,       # nível mínimo para aceitar
    completion  = "",      # fala do NPC na entrega (vazio = usa title)
)
```

### Tipos de Objetivo (`ObjectiveDef`)

```python
# Matar N inimigos
ObjectiveDef(type="kill", target="Lobo", count=10)
ObjectiveDef(type="kill", target="Fera", count=5)   # por raça
ObjectiveDef(type="kill", target="*",    count=3)   # qualquer

# Coletar item (drop condicional na morte do alvo)
ObjectiveDef(
    type="collect_item", target="Lobo", count=5,
    loot_item="Presa de Lobo", loot_chance=0.60
)

# Chegar a um tile específico
ObjectiveDef(type="reach_tile", count=1,
             location=(150, 300))            # (tx, ty)

# Chegar a uma área retangular
ObjectiveDef(type="reach_tile", count=1,
             location=(100, 200, 200, 350))  # (x0, y0, x1, y1)

# Alcançar um nível
ObjectiveDef(type="reach_level", count=10)

# Usar uma skill
ObjectiveDef(type="use_skill", target="golpe_poderoso", count=5)

# Usar consumível
ObjectiveDef(type="use_consumable", target="*", count=3)

# Falar com NPC
ObjectiveDef(type="talk_to_npc", target="Aldeão Mário")

# Equipar item
ObjectiveDef(type="equip_item", target="weapon", count=1)
ObjectiveDef(type="equip_item", target="Espada de Osso", count=1)
```

### Exemplo completo — quest encadeada

```python
QUESTS["cacar_lobos"] = QuestDef(
    title       = "A Ameaça dos Lobos",
    description = "Lobos atacam nossos fazendeiros. Mate 8 deles.",
    objectives  = (
        ObjectiveDef(type="kill", target="Lobo", count=8),
    ),
    reward    = QuestReward(xp=150, gold=10),
    requires  = ("first_blood",),  # só aparece após "first_blood" ser completa
    next_quest= "presas_de_lobo",  # inicia automaticamente ao completar
    completion= "Excelente! Os fazendeiros agradecem.",
)

QUESTS["presas_de_lobo"] = QuestDef(
    title       = "Presas para a Forja",
    description = "O ferreiro precisa de 4 presas de lobo.",
    objectives  = (
        ObjectiveDef(
            type="collect_item", target="Lobo", count=4,
            loot_item="Presa de Lobo", loot_chance=0.70
        ),
    ),
    reward = QuestReward(xp=200, gold=20),
)
```

### Atribuir quest a um NPC (`maps/map_X_entities.json`)

```json
{
  "quest_givers": [
    {
      "x": 155, "y": 310,
      "name": "Aldeão Mário",
      "level": 5,
      "profession": "Fazendeiro",
      "quest_ids":   ["cacar_lobos"],
      "turn_in_ids": ["presas_de_lobo"]
    }
  ]
}
```

---

## 6. Como Criar Mapas

Um mapa é composto por **3 arquivos CSV** + **1 arquivo JSON** de entidades.

```
maps/
  map_nome.csv           ← mapa legado (apenas terreno simples)
  map_nome_terrain.csv   ← terreno (char codes — ver tabela)
  map_nome_objects.csv   ← objetos/props (IDs de sprite)
  map_nome_entities.json ← NPCs, spawns, transições, zonas de áudio
```

### Char codes do terreno (`map_nome_terrain.csv`)

| Char | Tile          | Sólido |
|------|---------------|--------|
| `G`  | Grama         | Não    |
| `.`  | Piso de pedra | Não    |
| `~`  | Areia         | Não    |
| `f`  | Terra         | Não    |
| `_`  | Piso liso     | Não    |
| `d`  | Entrada caverna| Não  |
| `O`  | Portal        | Não    |
| `#`  | Parede        | **Sim**|
| `@`  | Parede em ruínas | **Sim** |
| `c`  | Parede de caverna | **Sim** |
| `m`  | Montanha      | **Sim**|
| `W`  | Água          | **Sim**|
| `T`  | Árvore        | **Sim**|
| `B`  | Arbusto       | **Sim**|
| `R`  | Rocha         | **Sim**|

### Objetos no `map_nome_objects.csv`

Cada célula contém um **ID de sprite** do catálogo em `tileset.py` (`OBJECT_SHEET_FAMILIES`), ou `.` para vazio. Exemplo:

```
., ., wall_NW, wall_N, wall_NE, ., .
., ., wall_W,  wall_C, wall_E,  ., .
., ., wall_SW, wall_S, wall_SE, ., .
```

### `map_nome_entities.json` — estrutura completa

```json
{
  "player": { "x": 10, "y": 10 },

  "merchants": [
    {
      "x": 50, "y": 30,
      "shop_id": "general",
      "level": 10,
      "profession": "Comerciante"
    }
  ],

  "blacksmiths": [
    {
      "x": 55, "y": 30,
      "shop_id": "blacksmith",
      "name": "João Ferreiro",
      "level": 15,
      "profession": "Ferreiro"
    }
  ],

  "trainers": [
    {
      "x": 60, "y": 30,
      "name": "Mestre Armas",
      "class_id": "guerreiro",
      "level": 20,
      "profession": "Treinador",
      "quest_ids": [],
      "turn_in_ids": []
    }
  ],

  "quest_givers": [
    {
      "x": 45, "y": 28,
      "name": "Capitão Silva",
      "level": 12,
      "profession": "Capitão da Guarda",
      "quest_ids":   ["quest_id_1", "quest_id_2"],
      "turn_in_ids": ["quest_id_2"]
    }
  ],

  "transitions": [
    {
      "x": 5, "y": 10,
      "target_map": "maps/map_cave_east.csv",
      "target_x": 90,
      "target_y": 5
    }
  ],

  "spawn_zones": [
    {
      "x": 80, "y": 60,
      "radius": 30,
      "respawn_cooldown": 90,
      "level_min": 5, "level_max": 10,
      "race": "Fera",
      "spawns": [
        { "type": "melee",  "tier": "normal", "count": 6 },
        { "type": "ranged", "tier": "elite",  "count": 2 }
      ]
    }
  ],

  "ambient_zones": [
    {
      "name": "Floresta Sombria",
      "rect": [0, 0, 200, 150],
      "ambient": "amb_forest",
      "music":   ["mus_forest_day"]
    }
  ]
}
```

### Criar e registrar um novo mapa

1. Crie os arquivos CSV e JSON em `maps/`
2. Abra `game.py` e adicione o caminho na lista `MAP_FILES`:

```python
MAP_FILES = [
    "maps/map_1.csv",
    "maps/map_nome.csv",   # ← adicione aqui
]
```

3. Para conectar via transição, use `"transitions"` no JSON do mapa de origem.

---

## 7. Como Criar Skills do Jogador

**Arquivo:** `skill_config.py`

### Schema de skill base (Guerreiro)

```python
SKILL_CATALOG["minha_skill"] = {
    "name":    "Nome da Skill",
    "desc":    "Descrição exibida no tooltip.",
    "cooldown": 8.0,       # segundos (0.0 = sem cooldown)
    "rage_cost": 20,       # custo em Raiva (0 = gratuito)
    "class_id": "guerreiro",
    "icon":    "skill_minha_skill",   # arquivo em assets/icons/ (sem .png)
}

# Custo para aprender com treinador
SKILL_COSTS["minha_skill"] = 50          # ouro
SKILL_LEVEL_REQUIREMENTS["minha_skill"] = 5  # nível mínimo
```

### Schema de skill de mago

```python
SKILL_CATALOG["nova_magia"] = {
    "name":       "Nova Magia",
    "desc":       "Lança projétil explosivo.",
    "cooldown":   0.0,
    "mana_cost":  40,
    "cast_time":  2.0,       # segundos (0 = instantâneo)
    "cast_range": 20,        # em tiles
    "class_id":   "mago",
    "icon":       "skill_nova_magia",
    # Para magia AOE:
    "needs_aoe_target": True,
    "aoe_radius_tiles": 3,
    # Para magia canalizada:
    "is_channeled":      True,
    "channel_duration":  5.0,
}
```

### Implementar a lógica (`skill_handlers.py`)

```python
# Guerreiro: método _skill_<skill_id>
def _skill_minha_skill(self, skill, combat_stats, combat_state, tile_move):
    target_id = self._resolve_target(combat_state, tile_move, 2)  # alcance 2 tiles
    if target_id == -1:
        self._warn("Nenhum alvo")
        return
    deal_damage(self.player_entity_id, target_id, "physical",
                multiplier=2.5, is_ability=True)
    enter_combat(combat_state)
    return True  # True = sucesso (aciona cooldown)

# Skill de talento: método _talent_<handler_name>
def _talent_meu_talento(self, skill, combat_stats, combat_state, tile_move):
    ...
```

---

## 8. Como Criar Talentos

**Arquivo:** `talent_data.py`

### Schema de talento

```python
TALENTS["cav_meu_talento"] = {
    "name":        "Nome do Talento",
    "description": "Aumenta {v} pontos de força.",   # {v} = value × points
    "build":       "cavaleiro",
    "col": 0, "row": 2,       # posição na grade (col 0–2, row 0–5)
    "max_points":  3,
    "requires": {
        "cav_talento_pai": 1,   # pré-requisito: 1 ponto em cav_talento_pai
    },
    "effects": [
        {"attribute": "attack_power", "value": 10.0, "type": "flat"},
    ],
    "unlocks_skill": None,    # ou nome do handler: "golpe_poderoso"
    "skill_def":     None,    # ou {"name": "...", "description": "...", "cooldown": 8.0}
}
```

### Talento que desbloqueia uma skill

```python
TALENTS["cav_meu_talento_skill"] = {
    ...
    "max_points":   1,
    "effects":      None,              # sem bônus de stat
    "unlocks_skill": "minha_skill",    # handler em skill_handlers.py
    "skill_def": {
        "name":        "Minha Skill",
        "description": "Descrição para o tooltip.",
        "cooldown":    10.0,
    },
    "unlock_at": 1,   # pontos necessários para desbloquear (padrão = max_points)
}
```

---

## 9. Como Criar Lojas e Mercadores

**Arquivo:** `merchant_data.py`

### Adicionar uma nova loja

```python
SHOPS["minha_loja"] = {
    "name":  "Alquimista",
    "color": (120, 80, 200),
    "stock": [
        {
            "factory": lambda: Item(
                "Elixir de Poder", "consumable", "",
                consumable={"attack_power_bonus": 20, "duration": 30.0},
                rarity="uncommon", value=0, max_stack=5,
            ),
            "price": 75,
        },
        {
            "factory": _T["small_potion"],   # referência ao template de loot
            "price": 12,
        },
    ],
}
```

### Tipos de consumível

```python
# Cura instantânea
consumable={"heal_instant": 80, "ooc_only": False}

# Cura ao longo do tempo
consumable={"heal_per_tick": 15, "interval": 3.0, "ticks": 5, "ooc_only": True}

# Aprende receita ao usar
consumable={"learn_recipe": "espada_afiada"}
```

### Adicionar o mercador no mapa (`entities.json`)

```json
{
  "merchants": [
    {
      "x": 70, "y": 40,
      "shop_id": "minha_loja",
      "level": 18,
      "profession": "Alquimista"
    }
  ]
}
```

---

## 10. Como Criar Receitas de Crafting

**Arquivo:** `crafting_data.py`

### Adicionar um material novo

```python
MATERIALS["escama_dragao"] = lambda: Item(
    "Escama de Dragão", "material", "",
    rarity="rare", value=50, max_stack=99
)
```

### Adicionar uma receita

```python
RECIPES["armadura_dragao"] = {
    "name":           "Armadura de Dragão",
    "result_rarity":  "epic",
    "result_factory": lambda: Item(
        "Armadura de Dragão", "armor", "chest",
        modifiers=[
            Modifier("stamina", 40, "flat"),
            Modifier("armor",   60, "flat"),
        ],
        rarity="epic", value=1200,
    ),
    "materials": [
        ("escama_dragao",  8),
        ("fragmento_ferro", 10),
        ("essencia_arcana",  2),
    ],
}
```

### Receita ensinável (item consumível)

```python
# O jogador usa o item e aprende a receita
_RECIPE_ITEMS["receita_armadura_dragao"] = lambda: Item(
    "Receita: Armadura de Dragão", "recipe", "",
    consumable={"learn_recipe": "armadura_dragao"},
    rarity="epic", value=200,
)
```

### Tabela de reciclagem

```python
# (item_type, subtype, rarity) → [(material_id, qtd), ...]
RECYCLE_TABLE[("armor", "", "rare")] = [
    ("fragmento_ferro", 5),
    ("fibra_madeira",   2),
    ("po_de_joia",      1),
]
```

---

## 11. Sistema de Sons

**Pasta:** `assets/sounds/sfx/`

### Regras de nomenclatura

```
mob_{nome}_{evento}.ogg         ← sons de mob (aggro, death, attack_melee, crit, ...)
skill_{skill_id}.ogg            ← som de skill do jogador
ui_{nome}.ogg                   ← sons de interface (levelup, inventory_open, ...)
amb_{nome}.ogg                  ← sons ambiente (loop)
mus_{nome}.ogg                  ← músicas de fundo (loop)
```

O `SoundManager` carrega variações automaticamente: se `mob_lobo_death.ogg` existe, também tenta `mob_lobo_death_2.ogg`, `_3.ogg`, `_4.ogg`.

### Tocar um som em um sistema

```python
from sound_manager import SOUNDS

SOUNDS.play("meu_som")                       # SFX genérico
SOUNDS.play_ui("levelup")                    # canal de UI
SOUNDS.play_skill("skill_golpe_poderoso")    # canal de skill
SOUNDS.play_mob_sounds(mob_sounds_comp, "aggro", dedup_key=f"ag_{eid}")
```

---

## 12. Referência de Componentes

Componentes principais em `components.py`:

| Componente         | Dados que contém                                      |
|--------------------|-------------------------------------------------------|
| `Position`         | `x`, `y` (float) — posição em pixels                 |
| `TileMovement`     | `current_tile_x/y`, `target_tile_x/y`, `is_moving`, `elevation` |
| `CombatStats`      | HP, ataque, defesa, crit, haste — stats efetivos      |
| `CharacterStats`   | Level, XP, STR/INT/AGI/VIT/DEF, raiva, mana          |
| `CombatState`      | `in_combat`, `is_stunned`, `is_rooted`, `target_entity_id` |
| `AIControlled`     | `state` (IDLE/CHASING/ATTACKING), `is_ranged`, `entity_class` |
| `Inventory`        | `items: list[Item]`, `max_slots`                      |
| `Equipment`        | `slots: dict[str, Item]` — mainhand, offhand, etc.    |
| `FogOfWar`         | `visible`, `explored` (sets de tiles)                 |
| `Merchant`         | `shop_id`, `name`, `level`                            |
| `QuestLog`         | `active: dict`, `completed: set`                      |
| `UIState`          | `show_inventory`, `show_talents`                      |
| `ShopUIState`      | `open_merchant_id`, `is_open`                         |
| `LootUIState`      | `open_corpse_id`                                      |
| `Camera`           | `target_entity_id`, `offset_x/y`, `zoom`             |

---

## 13. Referência de Arquivos

| Arquivo                  | O que editar para...                                   |
|--------------------------|--------------------------------------------------------|
| `loot_tables.py`         | Criar itens, ajustar drops, tabelas de loot            |
| `mob_definitions.py`     | Criar inimigos, definir cor e sons                     |
| `enemy_abilities_data.py`| Criar habilidades de inimigo                           |
| `quests_data.py`         | Criar e encadear quests                                |
| `quest_events.py`        | Disparar e registrar tipos de evento de quest          |
| `merchant_data.py`       | Criar lojas, ajustar estoque                           |
| `crafting_data.py`       | Criar materiais, receitas e tabela de reciclagem       |
| `skill_config.py`        | Definir skills do jogador (custos, cooldowns, mana)    |
| `skill_handlers.py`      | Implementar lógica de cada skill                       |
| `talent_data.py`         | Criar nós de talento, builds                           |
| `tileset.py`             | Definir novos tipos de tile e sprites                  |
| `maps/*.csv`             | Editar layout do mapa (terreno + objetos)              |
| `maps/*_entities.json`   | Posicionar NPCs, spawns, transições, zonas de áudio    |
| `status_effects_data.py` | Criar novos efeitos de status (veneno, slow, etc.)     |
| `damage_calculator.py`   | Ajustar fórmulas de dano                               |
| `entity_factory.py`      | Montar novas entidades a partir de componentes         |
| `components.py`          | Criar novos componentes de dados                       |
| `systems.py`             | Criar ou modificar sistemas de lógica                  |
