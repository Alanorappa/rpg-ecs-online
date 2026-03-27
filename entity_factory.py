# entity_factory.py
from world import World
from components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, CombatState, PlayerAutoMove, \
                       CharacterStats, PermanentStats, XPReward, EnemyTier, \
                       Corpse, Inventory, Equipment, PlayerSkills, Wallet, TalentTree, Merchant, \
                       SpawnZone, EntityIdentity, StatusEffects, ConsumableBar, MobSounds, FogOfWar
from tileset import TILE_MAPPING, TILE_SIZE, FLOOR_TILE
from mob_definitions import MOB_TABLE

# --- Configurações para as entidades ---
PLAYER_COLOR = (255, 0, 0)
PLAYER_SIZE = 24
PLAYER_SPEED = 110

TREE_COLOR = (34, 139, 34)
TREE_SIZE = 40

ENEMY_COLOR = (0, 0, 255)
ENEMY_SIZE = 24
ENEMY_SPEED = 85.0
ENEMY_DETECTION_RADIUS = 250.0
ENEMY_MELEE_ATTACK_RANGE = 1 # Nova constante para inimigos corpo a corpo

# --- Configurações de tier de inimigos ---
# Cada tier define: multiplicador de HP, dano, XP; tamanho; cor (melee, ranged)
ENEMY_TIER_CONFIGS = {
    "normal": {"hp": 1.0, "dmg": 1.0, "xp": 1,  "size": 24, "col_melee": (0, 0, 255),     "col_ranged": (0, 0, 200)},
    "elite":  {"hp": 2.0, "dmg": 1.5, "xp": 2,  "size": 28, "col_melee": (140, 140, 255),  "col_ranged": (100, 100, 220)},
    "rare":   {"hp": 3.0, "dmg": 2.0, "xp": 4,  "size": 30, "col_melee": (255, 165, 0),    "col_ranged": (220, 130, 0)},
    "boss":   {"hp": 5.0, "dmg": 3.0, "xp": 10, "size": 40, "col_melee": (180, 0, 180),    "col_ranged": (140, 0, 140)},
}


def create_tilemap(world: World, map_matrix: list) -> int:
    map_height_tiles = len(map_matrix)
    map_width_tiles = len(map_matrix[0]) if map_height_tiles > 0 else 0

    tile_data = []
    for row_idx, row_str in enumerate(map_matrix):
        if not row_str:
            continue
        current_row = []
        for col_idx, char in enumerate(row_str):
            tile_type = TILE_MAPPING.get(char, FLOOR_TILE)
            current_row.append(tile_type)
        # Pad short rows to map_width_tiles to prevent IndexError
        while len(current_row) < map_width_tiles:
            current_row.append(FLOOR_TILE)
        tile_data.append(current_row)

    tilemap_entity = world.create_entity()
    world.add_component(tilemap_entity, Tilemap(
        tile_matrix=tile_data,
        tile_size=TILE_SIZE,
        map_width_tiles=map_width_tiles,
        map_height_tiles=len(tile_data)
    ))
    return tilemap_entity


def create_player(world: World, tile_x: int, tile_y: int,
                  spawn_map: str = "maps/map_1.csv") -> int:
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    player_entity = world.create_entity()
    world.add_component(player_entity, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(player_entity, Renderable(color=PLAYER_COLOR, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(player_entity, PlayerControlled())
    world.add_component(player_entity, Collider(width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(player_entity, TileMovement(
        current_tile_x=tile_x,
        current_tile_y=tile_y,
        target_tile_x=tile_x,
        target_tile_y=tile_y,
        start_pixel_x=x,
        start_pixel_y=y,
        target_pixel_x=x,
        target_pixel_y=y,
        move_duration=TILE_SIZE / PLAYER_SPEED,
        speed=PLAYER_SPEED
    ))
    # Punhos: 7 attack_power + 5 base_physical_damage = ~12 dano, 1.5s intervalo
    world.add_component(player_entity, CombatStats(
        base_stamina=20,
        base_armor=5,
        base_attack_power=20,
        base_physical_damage=3,  # punhos: attack_power(7) + 3 = 10 sem arma
        base_crit_rating=0.15,
        base_attack_interval=1.95  # 1.5s base +30% (punhos)
    ))
    world.add_component(player_entity, CombatState())
    world.add_component(player_entity, PlayerAutoMove())
    # Atributos base (STR=1,INT=1,AGI=1,VIT=3,DEF=2) → CombatStats inicial já está calibrado
    world.add_component(player_entity, CharacterStats(
        spawn_tile_x=tile_x, spawn_tile_y=tile_y, spawn_map=spawn_map
    ))
    world.add_component(player_entity, PermanentStats())
    world.add_component(player_entity, Inventory())
    world.add_component(player_entity, Equipment())
    world.add_component(player_entity, PlayerSkills())
    world.add_component(player_entity, Wallet())
    world.add_component(player_entity, TalentTree())
    world.add_component(player_entity, ConsumableBar())
    world.add_component(player_entity, FogOfWar())
    world.add_component(player_entity, EntityIdentity(
        name="Aventureiro", race="Humano", entity_class="Guerreiro",
        level=1, tier="Normal",
    ))
    return player_entity

def create_tree(world: World, x: float, y: float) -> int:
    tree_entity = world.create_entity()
    world.add_component(tree_entity, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(tree_entity, Renderable(color=TREE_COLOR, width=TREE_SIZE, height=TREE_SIZE))
    world.add_component(tree_entity, Collider(width=TREE_SIZE, height=TREE_SIZE))
    return tree_entity

def create_camera(world: World, target_entity_id: int, screen_width: int, screen_height: int) -> int:
    camera_entity = world.create_entity()
    world.add_component(camera_entity, Position(x=screen_width / 2, y=screen_height / 2,
                                               prev_x=screen_width / 2, prev_y=screen_height / 2))
    world.add_component(camera_entity, Camera(target_entity_id=target_entity_id,
                                               offset_x=screen_width / 2,
                                               offset_y=screen_height / 2))
    return camera_entity

def create_enemy(world: World, tile_x: int, tile_y: int,
                 attack_range: int = ENEMY_MELEE_ATTACK_RANGE,
                 is_ranged: bool = False,
                 tier: str = "normal",
                 race: str = "Humanoide",
                 entity_class: str = "",
                 level: int = 1) -> int:
    cfg = ENEMY_TIER_CONFIGS.get(tier, ENEMY_TIER_CONFIGS["normal"])
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    size = cfg["size"]

    # Busca definição do mob pela raça; se encontrado, usa cor e classe específicas
    mob_def = MOB_TABLE.get(race)
    if mob_def:
        base_color = mob_def["color"]
        # elite/rare/boss: clarea a cor base levemente
        tier_brightness = {"normal": 1.0, "elite": 1.25, "rare": 1.50, "boss": 1.80}
        mult = tier_brightness.get(tier, 1.0)
        color = tuple(min(255, int(c * mult)) for c in base_color)
        entity_class = mob_def["entity_class"]
        is_ranged = mob_def["is_ranged"]
        attack_range = 5 if is_ranged else ENEMY_MELEE_ATTACK_RANGE
    else:
        color = cfg["col_ranged"] if is_ranged else cfg["col_melee"]
        # Para mobs genéricos, a classe de combate é sempre derivada de is_ranged,
        # independente do label passado pelo JSON (evita "Arqueiro melee" / "Guerreiro ranged")
        entity_class = "Arqueiro" if is_ranged else "Guerreiro"

    enemy_entity = world.create_entity()
    world.add_component(enemy_entity, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(enemy_entity, Renderable(color=color, width=size, height=size))
    world.add_component(enemy_entity, Collider(width=size, height=size))
    world.add_component(enemy_entity, Enemy())
    world.add_component(enemy_entity, AIControlled(
        state="IDLE", attack_range_tiles=attack_range, is_ranged=is_ranged,
        entity_class=entity_class))
    world.add_component(enemy_entity, InitialPosition(x=x, y=y))
    world.add_component(enemy_entity, DetectionRadius(radius=ENEMY_DETECTION_RADIUS))
    world.add_component(enemy_entity, TileMovement(
        current_tile_x=tile_x,
        current_tile_y=tile_y,
        target_tile_x=tile_x,
        target_tile_y=tile_y,
        start_pixel_x=x,
        start_pixel_y=y,
        target_pixel_x=x,
        target_pixel_y=y,
        move_duration=TILE_SIZE / ENEMY_SPEED,
        speed=ENEMY_SPEED
    ))
    world.add_component(enemy_entity, EnemyTier(tier=tier))

    if is_ranged:
        stats = CombatStats(
            base_stamina=10,
            base_armor=2,
            base_attack_power=0,
            base_spell_power=12,
            base_crit_rating=0.10,
            base_attack_interval=2.5,
            base_magical_damage=3
        )
    else:
        stats = CombatStats(
            base_stamina=15,
            base_armor=5,
            base_attack_power=7,
            base_crit_rating=0.05,
            base_attack_interval=3.0
        )

    # Aplica multiplicadores de tier
    if tier != "normal":
        stats.base_stamina = int(stats.base_stamina * cfg["hp"])
        stats.base_attack_power = int(stats.base_attack_power * cfg["dmg"])
        stats.base_spell_power = int(stats.base_spell_power * cfg["dmg"])
        stats.base_magical_damage = int(stats.base_magical_damage * cfg["dmg"])

    # Aplica scaling de level (mesmo progression que o player por nível acima de 1)
    # VIT+1 → stamina+5 | STR+1 → AP+2 | AGI+1 → crit+0.01 | DEF+2 → armor+4
    if level > 1:
        bonus = level - 1
        stats.base_stamina       += bonus * 5
        stats.base_attack_power  += bonus * 2
        stats.base_armor         += bonus * 4
        stats.base_crit_rating    = min(0.5, stats.base_crit_rating + bonus * 0.01)
        if is_ranged:
            stats.base_spell_power   += bonus * 2
            stats.base_magical_damage += bonus

    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp

    world.add_component(enemy_entity, stats)
    # XP escala com o level: level * 15, modificado pelo multiplicador de tier
    world.add_component(enemy_entity, XPReward(amount=int(level * 15 * cfg["xp"])))
    world.add_component(enemy_entity, StatusEffects())

    # Sons específicos do mob (definidos em mob_definitions.py)
    _snd = mob_def.get("sounds", {}) if mob_def else {}
    world.add_component(enemy_entity, MobSounds(
        aggro          = _snd.get("aggro",          ""),
        death          = _snd.get("death",          ""),
        attack_melee   = _snd.get("attack_melee",   ""),
        attack_ranged  = _snd.get("attack_ranged",  ""),
        attack_magic   = _snd.get("attack_magic",   ""),
        crit           = _snd.get("crit",           ""),
        emote_attack   = _snd.get("emote_attack",   ""),
        emote_get_crit = _snd.get("emote_get_crit", ""),
    ))

    tier_label      = tier.capitalize()
    mob_display_name = race                                  # "Zumbi", "Aranha", etc.
    mob_actual_race  = mob_def["race"] if mob_def else race  # "Morto-Vivo", "Fera", etc.
    world.add_component(enemy_entity, EntityIdentity(
        name=mob_display_name, race=mob_actual_race, entity_class=entity_class,
        level=level, tier=tier_label,
    ))
    return enemy_entity


def create_merchant(world: World, tile_x: int, tile_y: int, shop_id: str = "general") -> int:
    from merchant_data import SHOPS
    shop = SHOPS.get(shop_id, SHOPS["general"])
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    color = shop.get("color", (80, 200, 80))
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, Renderable(color=color, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, Merchant(name=shop["name"], shop_id=shop_id))
    return eid


def create_corpse(world: World, x: float, y: float, loot: list, coins: int = 0,
                  decay_time: float = None) -> int:
    corpse_entity = world.create_entity()
    world.add_component(corpse_entity, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(corpse_entity, Corpse(loot=loot, coins=coins, decay_time=decay_time))
    return corpse_entity


def create_spawn_zone(world: World,
                      center_x: int, center_y: int,
                      enemy_type: str, enemy_tier: str,
                      radius: int, max_count: int,
                      respawn_cooldown: float,
                      level_min: int = 1, level_max: int = 1,
                      race: str = "Humanoide", entity_class: str = "") -> int:
    """
    Cria uma entidade invisível de SpawnZone centrada em (center_x, center_y).
    O SpawnZoneSystem gerencia o spawn e respawn dos inimigos desta zona.
    """
    eid = world.create_entity()
    world.add_component(eid, Position(
        x=center_x * TILE_SIZE + TILE_SIZE / 2,
        y=center_y * TILE_SIZE + TILE_SIZE / 2,
    ))
    world.add_component(eid, SpawnZone(
        center_x=center_x, center_y=center_y,
        enemy_type=enemy_type, enemy_tier=enemy_tier,
        radius=radius, max_count=max_count,
        respawn_cooldown=respawn_cooldown,
        level_min=level_min, level_max=level_max,
        race=race, entity_class=entity_class,
    ))
    return eid