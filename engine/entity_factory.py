# entity_factory.py
from __future__ import annotations
from engine.world import World
from engine.components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, CombatState, GhostState, PlayerAutoMove, \
                       CharacterStats, PermanentStats, XPReward, EnemyTier, \
                       Corpse, Inventory, Equipment, PlayerSkills, Wallet, TalentTree, Merchant, \
                       SkillLevels, \
                       SpawnZone, EntityIdentity, StatusEffects, ConsumableBar, NpcSounds, FogOfWar, \
                       EnemyAbilities, EnemyAbilitySlot, QuestLog, QuestGiver, NPC, Blacksmith, \
                       LearnedRecipes, Trainer, Faction, Combatant, Harvestable, Tower, Minion
from ui.ui_components import (
    UIState, ShopUIState, LootUIState, DragState, TradeUIState,
    InstanceInventoryUIState,
)
from engine.tileset import TILE_MAPPING, OBJECT_MAPPING, TILE_SIZE, FLOOR_TILE, get_collision_offsets
from content.mob_definitions import MOB_TABLE, ENEMY_TIER_CONFIGS
from content.enemy_abilities_data import ABILITY_DEFS
from content.tower_definitions import TOWER_TABLE
from content.minion_definitions import MINION_TABLE

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

# ENEMY_TIER_CONFIGS agora vive em content/mob_definitions.py (achado 06 do
# benchmark arquitetural, PROBLEMAS_ARQUITETURA.md §12/§13 — dado de
# balanceamento, não lógica; importado acima).


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
    world.add_component(player_entity, InstanceInventoryUIState())
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
    #
    # TOWER_TABLE (29/07/2026) entra no MESMO lookup como fallback: esta
    # função só é chamada aqui pelo CLIENTE reconstruindo o espelho de
    # uma Torre remota (`_spawn_remote_mob`, via `create_enemy` — o
    # servidor cria a torre de verdade por `create_tower`, que NUNCA
    # passa por aqui). `race` chega como o `mob_key` da torre (ex:
    # "torre_de_fogo", via EntityIdentity.mob_key → payload de spawn) —
    # sem este fallback, `mob_def` ficava sempre None (torre não está em
    # MOB_TABLE de propósito, ver content/tower_definitions.py) e o
    # cliente caía no template genérico 100% errado: `entity_class`
    # virava sempre "Arqueiro"/"Guerreiro" (nunca "Mago" de verdade) e
    # `NpcSounds` ficava TOTALMENTE vazio (mob_def=None → sounds={}) —
    # bug real relatado pelo usuário: torre de fogo sem NENHUM som, torre
    # de flechas com som errado. TOWER_TABLE tem o MESMO formato de
    # MOB_TABLE de propósito (attributes/entity_class/color/is_ranged/
    # sounds), então o resto desta função funciona sem nenhuma mudança
    # adicional.
    race = _resolve_mob_race_variant(race, is_ranged)
    # Minion (30/07/2026) tem tabela própria pelo mesmo motivo de Tower
    # (server-side não usa _build_combat_entity pra criar minion — ver
    # entity_factory.py::create_minion) — mas o CLIENTE reconstrói o
    # espelho remoto de QUALQUER "enemy"-kind por aqui, então precisa do
    # mesmo fallback em cadeia que já existe pra torre, senão minion cai
    # no genérico (mesma classe de bug: som/visual errado, §34.70.1).
    mob_def = MOB_TABLE.get(race) or TOWER_TABLE.get(race) or MINION_TABLE.get(race)
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
    world.add_component(enemy_entity, NpcSounds(
        aggro          = _snd.get("aggro",          ""),
        death          = _snd.get("death",          ""),
        attack_melee   = _snd.get("attack_melee",   ""),
        attack_ranged  = _snd.get("attack_ranged",  ""),
        attack_magic   = _snd.get("attack_magic",   ""),
        crit           = _snd.get("crit",           ""),
        emote_attack   = _snd.get("emote_attack",   ""),
        emote_get_crit = _snd.get("emote_get_crit", ""),
        attack_impact  = _snd.get("attack_impact",  ""),
    ))

    tier_label      = tier.capitalize()
    mob_display_name = identity_name or race                 # "Zumbi", "Aranha", ou nome próprio
    mob_actual_race  = mob_def["race"] if mob_def else race  # "Morto-Vivo", "Fera", etc.
    world.add_component(enemy_entity, EntityIdentity(
        name=mob_display_name, race=mob_actual_race, entity_class=entity_class,
        level=level, tier=tier_label,
        # `race` aqui (antes de virar mob_actual_race acima) é a chave EXATA
        # de MOB_TABLE usada nesta criação — precisa sobreviver pro payload
        # de spawn de entidades SEM SpawnZone (Guarda Real, NPC de serviço,
        # boneco de treino), senão o cliente recebe só a categoria ampla
        # (mob_actual_race) e nunca acha o mob_def de verdade pra reconstruir
        # sons/entity_class corretos.
        mob_key=race,
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


_SERVICE_NPC_FACTION = "civis"
# Molde de combate default pra NPC de serviço sem classe inerente
# (mercador/ferreiro/dador-de-missão) — mesmo raciocínio de "qualquer um
# pode se defender" do Guarda Real. Treinador usa um molde por class_id
# (ver _service_npc_race_for_class).
_SERVICE_NPC_DEFAULT_RACE = "Guerreiro (NPC)"


def _service_npc_race_for_class(class_id: str) -> str:
    return {
        "guerreiro": "Guerreiro (NPC)",
        "mago":      "Mago (NPC)",
        "arqueiro":  "Arqueiro (NPC)",
    }.get(class_id, _SERVICE_NPC_DEFAULT_RACE)


def create_merchant(world: World, tile_x: int, tile_y: int, shop_id: str = "general",
                    name: str = "Comerciante", level: int = 1,
                    profession: str = "Comerciante") -> int:
    """NPC de serviço com combate genérico (Fase 1, 21/07/2026, pedido do
    usuário) — HP real + auto-attack igual a qualquer mob (via
    `_build_combat_entity`, mesma infra de `create_combat_npc`/Guarda
    Real), facção `civis` (amigável ao player, hostil a monstro/bandido).
    NÃO usa skills reais do jogador (Interceptar, etc.) — isso é uma Fase
    2 a discutir depois."""
    from content.merchant_data import SHOPS
    shop = SHOPS.get(shop_id, SHOPS["general"])
    npc_name = name if name != "Comerciante" else shop.get("name", name)
    eid = _build_combat_entity(
        world, tile_x, tile_y, ENEMY_MELEE_ATTACK_RANGE, False, "normal",
        _SERVICE_NPC_DEFAULT_RACE, "", level, _SERVICE_NPC_FACTION,
        identity_name=npc_name)
    color = shop.get("color", (80, 200, 80))
    world.add_component(eid, Renderable(color=color, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=npc_name, level=level, profession=profession))
    world.add_component(eid, Merchant(shop_id=shop_id))
    return eid


def create_quest_giver(world: World, tile_x: int, tile_y: int,
                       name: str = "Missiveiro", quest_ids: tuple = (),
                       turn_in_ids: tuple = (),
                       level: int = 1, profession: str = "Missiveiro") -> int:
    """NPC dador de quests com combate genérico (Fase 1 — ver
    create_merchant docstring pro racional completo)."""
    eid = _build_combat_entity(
        world, tile_x, tile_y, ENEMY_MELEE_ATTACK_RANGE, False, "normal",
        _SERVICE_NPC_DEFAULT_RACE, "", level, _SERVICE_NPC_FACTION,
        identity_name=name)
    world.add_component(eid, Renderable(color=(200, 180, 60), width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=name, level=level, profession=profession))
    world.add_component(eid, QuestGiver(quest_ids=tuple(quest_ids),
                                        turn_in_ids=tuple(turn_in_ids)))
    return eid


def create_blacksmith(world: World, tile_x: int, tile_y: int,
                      name: str = "Ferreiro", shop_id: str = "blacksmith",
                      level: int = 1, profession: str = "Ferreiro") -> int:
    """NPC ferreiro (loja + reciclagem/forja) com combate genérico (Fase
    1 — ver create_merchant docstring pro racional completo)."""
    from content.merchant_data import SHOPS
    shop = SHOPS.get(shop_id, SHOPS.get("blacksmith", {}))
    npc_name = name if name != "Ferreiro" else shop.get("name", name)
    eid = _build_combat_entity(
        world, tile_x, tile_y, ENEMY_MELEE_ATTACK_RANGE, False, "normal",
        _SERVICE_NPC_DEFAULT_RACE, "", level, _SERVICE_NPC_FACTION,
        identity_name=npc_name)
    color = shop.get("color", (180, 120, 40))
    world.add_component(eid, Renderable(color=color, width=PLAYER_SIZE, height=PLAYER_SIZE))
    world.add_component(eid, NPC(name=npc_name, level=level, profession=profession))
    world.add_component(eid, Merchant(shop_id=shop_id))
    world.add_component(eid, Blacksmith(shop_id=shop_id))
    return eid


def create_trainer(world: World, tile_x: int, tile_y: int,
                   name: str = "Treinador", class_id: str = "guerreiro",
                   quest_ids: tuple = (), turn_in_ids: tuple = (),
                   level: int = 1, profession: str = "Treinador") -> int:
    """NPC treinador de classe, com combate genérico NA MESMA classe que
    ensina (Fase 1, pedido do usuário: "o treinador do guerreiro luta
    como guerreiro") — ver create_merchant docstring pro racional
    completo. Pode acumular QuestGiver opcionalmente."""
    race = _service_npc_race_for_class(class_id)
    eid = _build_combat_entity(
        world, tile_x, tile_y, ENEMY_MELEE_ATTACK_RANGE, False, "normal",
        race, "", level, _SERVICE_NPC_FACTION, identity_name=name)
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
    world.add_component(eid, NpcSounds())
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


def create_tower(world: World, tile_x: int, tile_y: int, tower_key: str,
                 faction_id: str, respawnable: bool = False,
                 respawn_s: float = 0.0, regen_enabled: bool = False,
                 level: int = 1, is_nexus: bool = False) -> int:
    """Cria uma torre estática (29/07/2026, pedido do usuário) — mesmo
    espírito de `create_training_dummy` (construção MANUAL, sem passar
    por `_build_combat_entity`, que é acoplado ao formato de dict do
    MOB_TABLE — torre agora tem tabela própria, `content/tower_
    definitions.py::TOWER_TABLE`). Sem `AIControlled`/`EnemyAISystem`
    de propósito — a lógica de alvo/ataque é toda do `TowerSystem`
    (`engine/world_systems.py`), que lê o componente `Tower` direto.

    `tower_key` referencia `TOWER_TABLE` (tipo/aparência/atributos/
    sabor de ataque/recompensa). `faction_id`/`respawnable`/
    `respawn_s`/`regen_enabled`/`level` são parâmetros de INSTÂNCIA
    (por colocação no mapa) — não vêm de `TOWER_TABLE`."""
    from shared.constants import ALLY_VISION_RADIUS_TOWER
    tdef = TOWER_TABLE[tower_key]
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    size = ENEMY_TIER_CONFIGS.get(tdef["tier"], ENEMY_TIER_CONFIGS["normal"])["size"]

    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, Renderable(color=tdef.get("color", (120, 120, 130)), width=size, height=size))
    world.add_component(eid, Collider(width=size, height=size))
    world.add_component(eid, Combatant())
    world.add_component(eid, Faction(faction_id=faction_id))
    world.add_component(eid, TileMovement(
        current_tile_x=tile_x, current_tile_y=tile_y,
        target_tile_x=tile_x,  target_tile_y=tile_y,
        start_pixel_x=x,  start_pixel_y=y,
        target_pixel_x=x, target_pixel_y=y,
        move_duration=1.0, speed=0.0,   # torre nunca se move
    ))
    world.add_component(eid, EnemyTier(tier=tdef["tier"]))

    attrs = tdef["attributes"]
    entity_class = tdef["entity_class"]
    stats = CombatStats(
        base_stamina         = attrs["health"],
        base_armor           = attrs.get("armor", 0) * 10,
        base_attack_power    = attrs.get("attack_power", 0),
        base_physical_damage = attrs.get("attack_min", 1),
        base_attack_interval = attrs.get("attack_speed", 3.0),
        base_crit_rating     = attrs.get("crit_chance", 5) / 100.0,
    )
    stats.base_physical_damage_max = attrs.get("attack_max", stats.base_physical_damage)
    stats.base_acerto = float(attrs.get("acerto", 95))
    # Torre "mágica" (entity_class Mago/Mage/Warlock/Bruxo) causa dano
    # magical em vez de physical — mesmo critério de mob (ver
    # _build_combat_entity acima).
    if entity_class in _MAGIC_CASTER_CLASSES:
        stats.base_spell_power    = attrs.get("attack_power", 0)
        attack_min = attrs.get("attack_min", 1)
        attack_max = attrs.get("attack_max", attack_min)
        stats.base_magical_damage = (attack_min + attack_max) / 2.0
    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp
    world.add_component(eid, stats)

    world.add_component(eid, EntityIdentity(
        name=tdef.get("display_name", tower_key), race=tdef["race"],
        entity_class=entity_class, level=level, tier=tdef["tier"],
        mob_key=tower_key,
    ))
    world.add_component(eid, StatusEffects())
    _snd = tdef.get("sounds", {})
    world.add_component(eid, NpcSounds(
        aggro=_snd.get("aggro", ""), death=_snd.get("death", ""),
        attack_melee=_snd.get("attack_melee", ""),
        attack_ranged=_snd.get("attack_ranged", ""),
        attack_magic=_snd.get("attack_magic", ""),
        crit=_snd.get("crit", ""),
        emote_attack=_snd.get("emote_attack", ""),
        emote_get_crit=_snd.get("emote_get_crit", ""),
        attack_impact=_snd.get("attack_impact", ""),
    ))
    # XPReward: componente já existe (mob offline o usa), mas é MORTO no
    # servidor pra mob comum (server_death_handler.py lê MOB_TABLE/tier).
    # Pra Tower é o INVERSO: server_death_handler.py checa o componente
    # `Tower` PRIMEIRO e usa xp_reward/gold_min/gold_max direto, nunca
    # cai no lookup por nome — ver hook lá.
    world.add_component(eid, XPReward(amount=tdef["xp_reward"]))
    world.add_component(eid, Tower(
        tower_key=tower_key,
        attack_range_tiles=tdef["attack_range_tiles"],
        respawnable=respawnable, respawn_s=respawn_s,
        regen_enabled=regen_enabled,
        xp_reward=tdef["xp_reward"],
        gold_min=tdef["gold_min"], gold_max=tdef["gold_max"],
        spawn_tile_x=tile_x, spawn_tile_y=tile_y,
        vision_radius_tiles=tdef.get("vision_radius_tiles", ALLY_VISION_RADIUS_TOWER),
        is_nexus=is_nexus,
    ))
    return eid


def create_minion(world: World, tile_x: int, tile_y: int, minion_key: str,
                  faction_id: str, route: list, level: int = 1) -> int:
    """Cria um minion de lane estilo MOBA (30/07/2026, pedido do usuário)
    — mesmo espírito de `create_tower` (construção MANUAL, sem passar
    por `_build_combat_entity` — que sempre anexa `AIControlled`, e um
    minion é pilotado pelo `MinionSystem` próprio, não pelo
    `EnemyAISystem` compartilhado). DIFERENTE de `create_tower`:
    `TileMovement.speed` é REAL (minion anda de verdade) e não há
    parâmetros de respawn (minion morto não respawna individualmente —
    só a próxima wave agendada cria minions novos, ver
    `WorldServer._tick_minion_waves`).

    `minion_key` referencia `MINION_TABLE` (tipo/aparência/atributos/
    sabor de ataque/recompensa). `faction_id`/`route`/`level` são
    parâmetros de INSTÂNCIA (por wave) — não vêm de `MINION_TABLE`."""
    mdef = MINION_TABLE[minion_key]
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    size = ENEMY_TIER_CONFIGS.get(mdef["tier"], ENEMY_TIER_CONFIGS["normal"])["size"]

    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, Renderable(color=mdef.get("color", (150, 60, 60)), width=size, height=size))
    world.add_component(eid, Collider(width=size, height=size))
    world.add_component(eid, Combatant())
    world.add_component(eid, Faction(faction_id=faction_id))

    attrs = mdef["attributes"]
    # Velocidade REAL (diferente de Torre, que força speed=0.0) — mesma
    # fórmula de _build_combat_entity: ENEMY_SPEED * move_speed_pct/100.
    move_speed = ENEMY_SPEED * (attrs.get("move_speed_pct", 100) / 100.0)
    world.add_component(eid, TileMovement(
        current_tile_x=tile_x, current_tile_y=tile_y,
        target_tile_x=tile_x,  target_tile_y=tile_y,
        start_pixel_x=x,  start_pixel_y=y,
        target_pixel_x=x, target_pixel_y=y,
        move_duration=1.0, speed=move_speed,
    ))
    world.add_component(eid, EnemyTier(tier=mdef["tier"]))

    entity_class = mdef["entity_class"]
    stats = CombatStats(
        base_stamina         = attrs["health"],
        base_armor           = attrs.get("armor", 0) * 10,
        base_attack_power    = attrs.get("attack_power", 0),
        base_physical_damage = attrs.get("attack_min", 1),
        base_attack_interval = attrs.get("attack_speed", 3.0),
        base_crit_rating     = attrs.get("crit_chance", 5) / 100.0,
    )
    stats.base_physical_damage_max = attrs.get("attack_max", stats.base_physical_damage)
    stats.base_acerto = float(attrs.get("acerto", 95))
    if entity_class in _MAGIC_CASTER_CLASSES:
        stats.base_spell_power    = attrs.get("attack_power", 0)
        attack_min = attrs.get("attack_min", 1)
        attack_max = attrs.get("attack_max", attack_min)
        stats.base_magical_damage = (attack_min + attack_max) / 2.0
    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp
    world.add_component(eid, stats)

    world.add_component(eid, EntityIdentity(
        name=mdef.get("display_name", minion_key), race=mdef["race"],
        entity_class=entity_class, level=level, tier=mdef["tier"],
        mob_key=minion_key,
    ))
    world.add_component(eid, StatusEffects())
    _snd = mdef.get("sounds", {})
    world.add_component(eid, NpcSounds(
        aggro=_snd.get("aggro", ""), death=_snd.get("death", ""),
        attack_melee=_snd.get("attack_melee", ""),
        attack_ranged=_snd.get("attack_ranged", ""),
        attack_magic=_snd.get("attack_magic", ""),
        crit=_snd.get("crit", ""),
        emote_attack=_snd.get("emote_attack", ""),
        emote_get_crit=_snd.get("emote_get_crit", ""),
        attack_impact=_snd.get("attack_impact", ""),
    ))
    # XPReward: mesmo padrão de Tower — server_death_handler.py checa o
    # componente `Minion` PRIMEIRO e usa xp_reward direto, nunca cai no
    # lookup por nome/MOB_TABLE — ver hook lá.
    world.add_component(eid, XPReward(amount=mdef["xp_reward"]))
    world.add_component(eid, Minion(
        minion_key=minion_key, route=route,
        aggro_range_tiles=mdef["aggro_range_tiles"],
        attack_range_tiles=mdef["attack_range_tiles"],
        xp_reward=mdef["xp_reward"],
        gold_min=mdef.get("gold_min", 0), gold_max=mdef.get("gold_max", 0),
    ))
    return eid


def create_corpse(world: World, x: float, y: float, loot: list, coins: int = 0,
                  decay_time: float = None) -> int:
    corpse_entity = world.create_entity()
    world.add_component(corpse_entity, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(corpse_entity, Corpse(loot=loot, coins=coins, decay_time=decay_time))
    return corpse_entity


def create_harvestable_entity(world: World, tile_x: int, tile_y: int,
                              corpse_id: int, sprite_id: str = "",
                              name: str = "Objeto",
                              requires_quest: str = "") -> int:
    """Item de mapa saqueável (Fase M1, revisão 2, 25/07/2026) — entidade
    real, parada, SEM combate/diálogo (nada de Combatant/AIControlled/
    Faction) — só posição + aparência + o vínculo com o loot. Colisão e
    Y-sort vêm de graça dos sistemas genéricos (TileValidationSystem via
    TileMovement; RenderSystem via Position+Renderable), sem nenhum
    código dedicado. `corpse_id` aponta pro dict em
    WorldServer._corpses[corpse_id] — a fonte de verdade do loot em si
    NUNCA morou aqui, continua lá (request_loot/LOOT_AVAILABLE
    inalterados)."""
    x = tile_x * TILE_SIZE + TILE_SIZE / 2
    y = tile_y * TILE_SIZE + TILE_SIZE / 2
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, TileMovement(
        current_tile_x=tile_x, current_tile_y=tile_y,
        target_tile_x=tile_x, target_tile_y=tile_y,
        start_pixel_x=x, start_pixel_y=y,
        target_pixel_x=x, target_pixel_y=y,
        move_duration=1.0, speed=0.0,
    ))
    world.add_component(eid, Renderable(color=(120, 90, 60), width=32, height=32,
                                        sprite_id=sprite_id))
    # Colisão real do catálogo (25/07/2026, bug real relatado pelo usuário:
    # "pl_vomito" é passável no catálogo, mas travava o tile de qualquer
    # jeito) — TileMovement continua SEMPRE presente (discovery/AOI
    # dependem dele pra posição), mas TileValidationSystem só trata o
    # tile como ocupado se Harvestable.solid for True. Sem entrada no
    # catálogo (sprite_id vazio ou id desconhecido) = default sólido,
    # preserva o comportamento de antes desta fix.
    from engine.tileset import OBJECT_MAPPING
    _obj_tt = OBJECT_MAPPING.get(sprite_id) if sprite_id else None
    solid = _obj_tt.is_solid if _obj_tt is not None else True
    world.add_component(eid, Harvestable(corpse_id=corpse_id,
                                         requires_quest=requires_quest,
                                         solid=solid))
    world.add_component(eid, EntityIdentity(name=name, race="Objeto", entity_class=""))
    return eid


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