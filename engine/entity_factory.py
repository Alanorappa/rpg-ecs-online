# entity_factory.py
from __future__ import annotations
from engine.world import World
from engine.components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, CombatState, GhostState, PlayerAutoMove, \
                       CharacterStats, PermanentStats, XPReward, EnemyTier, \
                       Corpse, Inventory, Equipment, PlayerSkills, Wallet, TalentTree, Merchant, \
                       SkillLevels, \
                       SpawnZone, EntityIdentity, StatusEffects, ConsumableBar, MobSounds, FogOfWar, \
                       EnemyAbilities, EnemyAbilitySlot, QuestLog, QuestGiver, NPC, Blacksmith, \
                       LearnedRecipes, Trainer, Faction, Combatant
from ui.ui_components import UIState, ShopUIState, LootUIState, DragState, TradeUIState
from engine.tileset import TILE_MAPPING, OBJECT_MAPPING, TILE_SIZE, FLOOR_TILE, get_collision_offsets
from content.mob_definitions import MOB_TABLE
from content.enemy_abilities_data import ABILITY_DEFS

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

# Mesmo conjunto de entity_class usado em systems.py (EnemyAISystem,
# escolha de som) pra decidir se o mob causa dano "magical" (ignora
# armadura do alvo) em vez de "physical".
_MAGIC_CASTER_CLASSES = {"Mage", "Mago", "Warlock", "Bruxo"}

# --- Configurações de tier de inimigos ---
# Cada tier define: multiplicador de HP, dano, XP; tamanho; cor (melee, ranged)
ENEMY_TIER_CONFIGS = {
    "normal": {"hp": 1.0, "dmg": 1.0, "xp": 1,  "size": 24, "col_melee": (0, 0, 255),     "col_ranged": (0, 0, 200)},
    "elite":  {"hp": 2.0, "dmg": 1.5, "xp": 2,  "size": 28, "col_melee": (140, 140, 255),  "col_ranged": (100, 100, 220)},
    "rare":   {"hp": 3.0, "dmg": 2.0, "xp": 4,  "size": 30, "col_melee": (255, 165, 0),    "col_ranged": (220, 130, 0)},
    "boss":   {"hp": 5.0, "dmg": 3.0, "xp": 10, "size": 40, "col_melee": (180, 0, 180),    "col_ranged": (140, 0, 140)},
}


def create_tilemap(world: World, terrain_matrix: list, object_matrix: list,
                   terrain_visual: list | None = None) -> int:
    map_height_tiles = len(terrain_matrix)
    map_width_tiles = len(terrain_matrix[0]) if map_height_tiles > 0 else 0

    # Pass 1: terrain tile_data (colisão base)
    tile_data = []
    for row_str in terrain_matrix:
        if not row_str:
            continue
        current_row = []
        for char in row_str:
            current_row.append(TILE_MAPPING.get(char, FLOOR_TILE))
        while len(current_row) < map_width_tiles:
            current_row.append(FLOOR_TILE)
        tile_data.append(current_row)

    # Pass 2: sobrescreve colisão com tiles de objeto.
    # collision_rect permite que objetos altos (ex: 32×64) bloqueiem tiles acima do base.
    map_h = len(tile_data)
    map_w = map_width_tiles
    for r, obj_row in enumerate(object_matrix):
        if r >= map_h:
            break
        for c, obj_char in enumerate(obj_row):
            if not obj_char or obj_char == "." or c >= map_w:
                continue
            obj_tile = OBJECT_MAPPING.get(obj_char)
            if obj_tile is None:
                continue
            # Objetos puramente decorativos (sem colisão, sem elevação, sem transição)
            # não alteram tile_matrix para preservar a colisão do terreno subjacente.
            # Objetos com elevation ou is_transition PRECISAM ser escritos em
            # tile_matrix para que TileValidationSystem os enxergue.
            purely_decorative = (
                not obj_tile.is_solid
                and obj_tile.elevation == 0
                and not obj_tile.is_transition
            )
            if purely_decorative:
                continue
            for dx, dy in get_collision_offsets(obj_tile):
                nr, nc = r + dy, c + dx
                if 0 <= nr < map_h and 0 <= nc < len(tile_data[nr]):
                    tile_data[nr][nc] = obj_tile

    if terrain_visual is None:
        terrain_visual = [[""] * map_width_tiles for _ in range(map_h)]
    else:
        # Garante dimensões corretas ao carregar do CSV
        while len(terrain_visual) < map_h:
            terrain_visual.append([""] * map_width_tiles)
        for row in terrain_visual:
            while len(row) < map_width_tiles:
                row.append("")

    tilemap_entity = world.create_entity()
    world.add_component(tilemap_entity, Tilemap(
        tile_matrix=tile_data,
        terrain_matrix=terrain_matrix,
        object_matrix=object_matrix,
        tile_size=TILE_SIZE,
        terrain_visual=terrain_visual,
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
    world.add_component(player_entity, GhostState())
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
    world.add_component(player_entity, SkillLevels())
    world.add_component(player_entity, ConsumableBar())
    from shared.constants import FOG_RADIUS
    _fog = FogOfWar(radius=FOG_RADIUS)
    _fog.switch_map(spawn_map)
    world.add_component(player_entity, _fog)
    world.add_component(player_entity, QuestLog())
    world.add_component(player_entity, LearnedRecipes())
    world.add_component(player_entity, UIState())
    world.add_component(player_entity, ShopUIState())
    world.add_component(player_entity, LootUIState())
    world.add_component(player_entity, DragState())
    world.add_component(player_entity, TradeUIState())
    world.add_component(player_entity, EntityIdentity(
        name="Aventureiro", race="Humano", entity_class="Guerreiro",
        level=1, tier="normal",
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

def _resolve_mob_race_variant(race: str, want_ranged: bool) -> str:
    """Troca pra um `alt_variant` cadastrado quando o modo de combate pedido
    (want_ranged, vindo do "type" melee/ranged da spawn zone no JSON do mapa)
    não bate com o modo FIXO da raça em MOB_TABLE — ex.: zona pede "melee"
    pra "Goblin" (sempre ranged, entity_class="Hunter" fixo); se "Goblin"
    tiver `alt_variant: "Goblin Guerreiro"` e essa entrada for melee de
    verdade, usa ela em vez de silenciosamente ignorar o "type" pedido
    (era o comportamento antigo — toda entrada "melee" de uma zona cujo
    race só existe ranged em MOB_TABLE nascia ranged do mesmo jeito).

    Sem `alt_variant` cadastrado (ou variant que também não bate o modo
    pedido, ou raça sem entrada em MOB_TABLE) — devolve a raça pedida sem
    mudança: fallback pro que já existe, nunca quebra."""
    mob_def = MOB_TABLE.get(race)
    if mob_def is None or mob_def["is_ranged"] == want_ranged:
        return race
    alt_race = mob_def.get("alt_variant")
    alt_def  = MOB_TABLE.get(alt_race) if alt_race else None
    if alt_def is not None and alt_def["is_ranged"] == want_ranged:
        return alt_race
    return race


def _build_combat_entity(world: World, tile_x: int, tile_y: int,
                         attack_range: int, is_ranged: bool, tier: str,
                         race: str, entity_class: str, level: int,
                         faction: str, identity_name: str = "") -> int:
    """Corpo compartilhado entre `create_enemy()` (mob hostil "clássico")
    e `create_combat_npc()` (NPC de combate — guarda, etc: mesma
    infraestrutura de IA/combate, só com facção tipicamente amigável e
    identidade de NPC em vez de Enemy). Retorna a entidade SEM o
    componente de tag (`Enemy`/`NPC`) — quem chama decide qual anexar.

    `identity_name`, se não-vazio, sobrescreve o nome exibido
    (`EntityIdentity.name`, default = raça) — usado por
    `create_combat_npc()` pra dar um nome próprio ("Guarda Real") em vez
    de mostrar a raça genérica, igual ao `_server_name` que mobs remotos
    já recebem via `ENTITY_SPAWN.name` (ver client/remote_entity_handlers.py)."""
    cfg = ENEMY_TIER_CONFIGS.get(tier, ENEMY_TIER_CONFIGS["normal"])
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    size = cfg["size"]

    # Busca definição do mob pela raça; mobs cadastrados em MOB_TABLE usam
    # atributos próprios (attributes/abilities/loot/xp_given_by_lvl, ver
    # mob_definitions.py); mobs sem entrada (ex: "Elemental", definidos só na
    # SpawnZone do mapa) caem no template genérico por is_ranged (bloco else).
    race = _resolve_mob_race_variant(race, is_ranged)
    mob_def = MOB_TABLE.get(race)
    attrs   = mob_def.get("attributes") if mob_def else None
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
    world.add_component(enemy_entity, Combatant())
    world.add_component(enemy_entity, Faction(faction_id=faction))
    world.add_component(enemy_entity, AIControlled(
        state="IDLE", attack_range_tiles=attack_range, is_ranged=is_ranged,
        entity_class=entity_class))
    world.add_component(enemy_entity, InitialPosition(x=x, y=y))
    world.add_component(enemy_entity, DetectionRadius(radius=ENEMY_DETECTION_RADIUS))

    # Velocidade de movimento: mobs cadastrados usam move_speed_pct (% de
    # ENEMY_SPEED); sem cadastro, usa ENEMY_SPEED puro (comportamento antigo).
    move_speed = ENEMY_SPEED * (attrs["move_speed_pct"] / 100.0) if attrs else ENEMY_SPEED

    world.add_component(enemy_entity, TileMovement(
        current_tile_x=tile_x,
        current_tile_y=tile_y,
        target_tile_x=tile_x,
        target_tile_y=tile_y,
        start_pixel_x=x,
        start_pixel_y=y,
        target_pixel_x=x,
        target_pixel_y=y,
        move_duration=TILE_SIZE / move_speed,
        speed=move_speed
    ))
    world.add_component(enemy_entity, EnemyTier(tier=tier))

    if attrs:
        # Atributos próprios do mob (mob_definitions.py) — substitui os 2
        # templates fixos (melee/ranged) que antes eram iguais pra todo mob.
        stats = CombatStats(
            base_stamina         = attrs["health"],
            base_armor           = attrs.get("armor", 0) * 10,  # % -> rating (1 rating = 0.1% de redução)
            base_attack_power    = attrs.get("attack_power", 0),
            base_physical_damage = attrs.get("attack_min", 1),
            base_attack_interval = attrs.get("attack_speed", 3.0),
            base_crit_rating     = attrs.get("crit_chance", 5) / 100.0,
        )
        stats.base_physical_damage_max = attrs.get("attack_max", stats.base_physical_damage)
        stats.base_acerto = float(attrs.get("acerto", 95))
        # Mobs ranged "mágicos" (Mage/Warlock) causam dano magical, não
        # physical — ignora armadura do alvo (reduzido só por resistência,
        # nunca implementada pra auto-attack genérico de mob — ver
        # PROBLEMAS_ARQUITETURA.md). damage_type_to_use (EnemyAISystem)
        # decide physical/magical olhando spell_power/magical_damage > 0.
        # base_attack_power continua setado (não-zerado) porque alimenta o
        # multiplicador de dano de habilidade (magnitude × base_attack_power).
        if entity_class in _MAGIC_CASTER_CLASSES:
            stats.base_spell_power    = attrs.get("attack_power", 0)
            attack_min = attrs.get("attack_min", 1)
            attack_max = attrs.get("attack_max", attack_min)
            stats.base_magical_damage = (attack_min + attack_max) / 2.0
    elif is_ranged:
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
            base_stamina=100,
            base_armor=5,
            base_attack_power=7,
            base_crit_rating=0.05,
            base_attack_interval=3.0
        )

    # Aplica multiplicadores de tier
    if tier != "normal":
        stats.base_stamina             = int(stats.base_stamina * cfg["hp"])
        stats.base_attack_power        = int(stats.base_attack_power * cfg["dmg"])
        stats.base_spell_power         = int(stats.base_spell_power * cfg["dmg"])
        stats.base_magical_damage      = int(stats.base_magical_damage * cfg["dmg"])
        stats.base_physical_damage     = int(stats.base_physical_damage * cfg["dmg"])
        stats.base_physical_damage_max = int(stats.base_physical_damage_max * cfg["dmg"])

    # Aplica scaling de level. Mob cadastrado (attrs): health e attack_power
    # (físico e/ou mágico) MULTIPLICAM pelo level — armor/acerto/crit/
    # velocidade ficam fixos, vêm só de mob_definitions.py + tier (level do
    # mob vem da SpawnZone no JSON do mapa, level_min/level_max).
    # Mob sem cadastro (fallback, ex: "Elemental"): scaling aditivo antigo,
    # inalterado (mesma progressão que o player por nível acima de 1).
    if level > 1:
        if attrs:
            stats.base_stamina      = int(stats.base_stamina * level)
            stats.base_attack_power = int(stats.base_attack_power * level)
            if entity_class in _MAGIC_CASTER_CLASSES:
                stats.base_spell_power    = int(stats.base_spell_power * level)
                stats.base_magical_damage = int(stats.base_magical_damage * level)
        else:
            # VIT+1 → stamina+5 | STR+1 → AP+2 | AGI+1 → crit+0.01 | DEF+2 → armor+4
            bonus = level - 1
            stats.base_stamina       += bonus * 75
            stats.base_attack_power  += bonus * 2
            stats.base_armor         += bonus * 4
            stats.base_crit_rating    = min(0.5, stats.base_crit_rating + bonus * 0.01)

    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp

    world.add_component(enemy_entity, stats)
    # XP escala com o level do mob × xp_given_by_lvl (mob_definitions.py),
    # modificado pelo multiplicador de tier. Mobs sem cadastro: fallback 15/level.
    _xp_per_lvl = mob_def.get("xp_given_by_lvl", 15) if mob_def else 15
    world.add_component(enemy_entity, XPReward(amount=int(level * _xp_per_lvl * cfg["xp"])))
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
    mob_display_name = identity_name or race                 # "Zumbi", "Aranha", ou nome próprio
    mob_actual_race  = mob_def["race"] if mob_def else race  # "Morto-Vivo", "Fera", etc.
    world.add_component(enemy_entity, EntityIdentity(
        name=mob_display_name, race=mob_actual_race, entity_class=entity_class,
        level=level, tier=tier_label,
    ))

    # Habilidades especiais — lista explícita por mob em mob_definitions.py
    # (substitui o lookup antigo por raça/classe). Cada entrada é o
    # ability_id sozinho (usa default_cooldown de ABILITY_DEFS) ou
    # (ability_id, cooldown) pra sobrescrever o cooldown nesse mob.
    _ability_entries = []
    for _a in (mob_def.get("abilities", []) if mob_def else []):
        if isinstance(_a, tuple):
            _aid, _acd = _a
        else:
            _aid  = _a
            _adef = ABILITY_DEFS.get(_aid)
            _acd  = _adef.default_cooldown if _adef else 12.0
        _ability_entries.append((_aid, _acd))
    if _ability_entries:
        slots = [EnemyAbilitySlot(aid, cd) for aid, cd in _ability_entries]
        world.add_component(enemy_entity, EnemyAbilities(slots))

    return enemy_entity


def create_enemy(world: World, tile_x: int, tile_y: int,
                 attack_range: int = ENEMY_MELEE_ATTACK_RANGE,
                 is_ranged: bool = False,
                 tier: str = "normal",
                 race: str = "Humanoide",
                 entity_class: str = "",
                 level: int = 1,
                 faction: str = "monstros_hostis") -> int:
    eid = _build_combat_entity(world, tile_x, tile_y, attack_range, is_ranged,
                               tier, race, entity_class, level, faction)
    world.add_component(eid, Enemy())
    return eid


def create_combat_npc(world: World, tile_x: int, tile_y: int,
                      faction: str,
                      name: str = "",
                      profession: str = "Guarda",
                      attack_range: int = ENEMY_MELEE_ATTACK_RANGE,
                      is_ranged: bool = False,
                      tier: str = "normal",
                      race: str = "Humanoide",
                      entity_class: str = "",
                      level: int = 1) -> int:
    """NPC de combate (guarda, etc.) — mesma infraestrutura de IA/combate/
    sync de `create_enemy()` (via `_build_combat_entity`), mas com tag
    `NPC` (identidade nome/level/profissão) em vez de `Enemy`. Facção
    tipicamente amigável ou neutra ao player (ex: "guardas_vila") — não é
    obrigatório, um NPC de combate hostil também é válido (ex: bandido
    "civil"). Sistema de Facções, Fase 4 (ARQUITETURA_ONLINE.md).

    Diferente de vendedor/quest-giver/treinador/ferreiro (`create_merchant`
    etc.): estes continuam 100% estáticos e client-side, sem `CombatStats`
    nem sync com o servidor. `create_combat_npc` produz uma entidade
    plenamente sincronizada (`Combatant`, ver server/world_server.py),
    com HP real e IA de combate — pode lutar, ser atacado (sujeito ao
    gate de facção `amigavel` em `apply_damage_core`), e (Fase 5) brigar
    com outras entidades não-jogador."""
    eid = _build_combat_entity(world, tile_x, tile_y, attack_range, is_ranged,
                               tier, race, entity_class, level, faction,
                               identity_name=name)
    world.add_component(eid, NPC(name=name or race, level=level, profession=profession))
    return eid


def create_merchant(world: World, tile_x: int, tile_y: int, shop_id: str = "general",
                    name: str = "Comerciante", level: int = 1,
                    profession: str = "Comerciante") -> int:
    from content.merchant_data import SHOPS
    shop = SHOPS.get(shop_id, SHOPS["general"])
    npc_name = name if name != "Comerciante" else shop.get("name", name)
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    color = shop.get("color", (80, 200, 80))
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, TileMovement(current_tile_x=tile_x, current_tile_y=tile_y,
                                          target_tile_x=tile_x, target_tile_y=tile_y))
    world.add_component(eid, Renderable(color=color, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=npc_name, level=level, profession=profession))
    world.add_component(eid, Merchant(shop_id=shop_id))
    return eid


def create_quest_giver(world: World, tile_x: int, tile_y: int,
                       name: str = "Missiveiro", quest_ids: tuple = (),
                       turn_in_ids: tuple = (),
                       level: int = 1, profession: str = "Missiveiro") -> int:
    """Cria um NPC dador de quests no mapa."""
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, TileMovement(current_tile_x=tile_x, current_tile_y=tile_y,
                                          target_tile_x=tile_x, target_tile_y=tile_y))
    world.add_component(eid, Renderable(color=(200, 180, 60), width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=name, level=level, profession=profession))
    world.add_component(eid, QuestGiver(quest_ids=tuple(quest_ids),
                                        turn_in_ids=tuple(turn_in_ids)))
    return eid


def create_blacksmith(world: World, tile_x: int, tile_y: int,
                      name: str = "Ferreiro", shop_id: str = "blacksmith",
                      level: int = 1, profession: str = "Ferreiro") -> int:
    """Cria um NPC ferreiro com capacidade de loja, reciclagem e forja."""
    from content.merchant_data import SHOPS
    shop = SHOPS.get(shop_id, SHOPS.get("blacksmith", {}))
    npc_name = name if name != "Ferreiro" else shop.get("name", name)
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    color = shop.get("color", (180, 120, 40))
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, TileMovement(current_tile_x=tile_x, current_tile_y=tile_y,
                                          target_tile_x=tile_x, target_tile_y=tile_y))
    world.add_component(eid, Renderable(color=color, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=npc_name, level=level, profession=profession))
    world.add_component(eid, Merchant(shop_id=shop_id))
    world.add_component(eid, Blacksmith(shop_id=shop_id))
    return eid


def create_trainer(world: World, tile_x: int, tile_y: int,
                   name: str = "Treinador", class_id: str = "guerreiro",
                   quest_ids: tuple = (), turn_in_ids: tuple = (),
                   level: int = 1, profession: str = "Treinador") -> int:
    """Cria um NPC treinador de classe. Pode acumular QuestGiver opcionalmente."""
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, TileMovement(current_tile_x=tile_x, current_tile_y=tile_y,
                                          target_tile_x=tile_x, target_tile_y=tile_y))
    world.add_component(eid, Renderable(color=(80, 140, 220), width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=name, level=level, profession=profession))
    world.add_component(eid, Trainer(class_id=class_id))
    if quest_ids:
        world.add_component(eid, QuestGiver(quest_ids=tuple(quest_ids),
                                            turn_in_ids=tuple(turn_in_ids) or tuple(quest_ids)))
    return eid


def create_training_dummy(world: World, tile_x: int, tile_y: int) -> int:
    """Cria boneco de treino estático para testes de skills e talentos.

    HP: 999 999 | Armadura: 100 | hp5: ~99 999/5s | Sem IA, sem ataque, sem XP.
    """
    from engine.components import TrainingDummy as _TD
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2

    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, Renderable(color=(255, 215, 0), width=28, height=28))
    world.add_component(eid, Collider(width=28, height=28))
    world.add_component(eid, Enemy())
    # Combatant: gate de registro em _mob_eids/sync agora é este componente,
    # não Enemy sozinho (Sistema de Facções, Fase 4) — sem isso o boneco de
    # treino pararia de ser sincronizado pro cliente.
    world.add_component(eid, Combatant())
    world.add_component(eid, TileMovement(
        current_tile_x=tile_x, current_tile_y=tile_y,
        target_tile_x=tile_x,  target_tile_y=tile_y,
        start_pixel_x=x,  start_pixel_y=y,
        target_pixel_x=x, target_pixel_y=y,
        move_duration=1.0, speed=0.0,
    ))
    world.add_component(eid, EnemyTier(tier="boss"))
    world.add_component(eid, StatusEffects())
    world.add_component(eid, MobSounds())
    world.add_component(eid, _TD())

    stats = CombatStats(
        base_stamina=999_999,
        base_armor=100,
        base_attack_power=0,
        base_crit_rating=0.0,
        base_attack_interval=999.0,
    )
    stats.hp5 = 99_999 / 999_999   # ≈ 10% de max_hp por tick de 5s
    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp
    world.add_component(eid, stats)

    world.add_component(eid, EntityIdentity(
        name="Boneco de treino", race="Mecânico", entity_class="Guerreiro",
        level=99, tier="Boss",
    ))
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
                      race: str = "Humanoide", entity_class: str = "",
                      faction: str = "monstros_hostis") -> int:
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
        faction=faction,
    ))
    return eid