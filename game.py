# game.py
import pygame
import math
import time as _time
import gc as _gc
from fonts import make as _font

from world import World
from components import Position, Tilemap, CombatStats, CharacterStats, PermanentStats, \
                       TileMovement, PlayerAutoMove, CombatState, FogOfWar, Enemy, Visible, \
                       Camera, Renderable, SpellCast, Channeling, IceBlockEffect, AoeTargeting, \
                       UIState, ShopUIState, LootUIState
from systems import (
    PlayerInputSystem, TileMovementSystem, RenderSystem, CameraSystem,
    EnemyAISystem, TileRenderSystem, TileValidationSystem,
    PathfindingSystem, CombatSystem, CombatStateSystem, MouseTargetingSystem,
    ProjectileSystem, CorpseSystem, LootSystem, ShopSystem, SkillSystem,
    SpawnZoneSystem, ConsumableSystem, DeathHandlerSystem, FogSystem,
    StatusEffectSystem, EnemyAbilitySystem,
    register_services,
)
from stats_system import XPSystem, DeathRespawnSystem
from quest_system import QuestSystem, QuestDialogSystem, QuestJournalSystem
from quest_events import set_quest_system
from entity_factory import create_player, create_camera, create_enemy, create_tilemap, create_merchant, create_spawn_zone, create_quest_giver, create_blacksmith, create_trainer
from god_mode import GodModeEditor
from components import Inventory, Equipment, PlayerSkills, Wallet
from map_loader import load_map_csv, validate_map
from tileset import TILE_SIZE
from combat_log import LOG
from floating_text import FLT, DASH_TRAIL, WARN, PROC
from icon_manager import ICONS
from sound_manager import SOUNDS
from ui_compare import draw_compare_panel
from talent_system import TalentSystem
from ui_helpers import item_tooltip_lines, draw_stack_count, RARITY_COLORS as _ITEM_RARITY_COLORS
from map_overlay import MapOverlay
from minimap import Minimap
from stat_fns import add_modifier, remove_modifier, learn_recipe
from save_system import save_game, load_game, has_save, next_free_slot

# --- Configurações do Jogo ---
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
FPS = 60

MAP_FILES = [
    "maps/map_1.csv",
]

DEBUG_MODE    = False  # sobrescrito pelo config.json em runtime
PROFILE_FRAMES = False  # sobrescrito pelo config.json em runtime

# Nomes amigáveis opcionais — mapas sem entrada aqui usam o nome do arquivo
_DEBUG_MAP_NAMES: dict[str, str] = {
    "maps/map_1.csv":           "Mapa Principal",
    "maps/map_cave_west.csv":   "Caverna Oeste",
    "maps/map_cave_east.csv":   "Caverna Leste",
}

# Cores HUD
C_WHITE   = (255, 255, 255)
C_YELLOW  = (255, 220,   0)
C_GREEN   = (  0, 220,  80)
C_RED     = (220,  50,  50)
C_GRAY    = (160, 160, 160)
C_CYAN    = (  0, 220, 220)
C_ORANGE  = (255, 160,   0)


def _merge_display_matrix(terrain: list[str], objects: list) -> list[str]:
    """
    Combina terrain_matrix e object_matrix em uma matriz de exibição.
    Posições com objeto sobrescrevem o char de terreno — usado pelo
    map_overlay e minimap para mostrar a cor correta de árvores, pedras, etc.
    IDs multi-char (ex: "t1", "b2") são reduzidos ao char base ("t", "b")
    para que o lookup de cor no TileType funcione corretamente.
    """
    result = []
    for r, t_row in enumerate(terrain):
        if r >= len(objects):
            result.append(t_row)
            continue
        o_row = objects[r]
        row = list(t_row)
        for c, obj_char in enumerate(o_row):
            if obj_char and obj_char != "." and c < len(row):
                display = obj_char[0] if len(obj_char) > 1 else obj_char
                row[c] = display
        result.append("".join(row))
    return result


class GameEngine:
    def __init__(self, scale: float = 1.0, char_data: "dict | None" = None,
                 save_slot: int = 0, online: bool = False,
                 net_user: str = "", net_pass: str = ""):
        pygame.init()
        self._scale      = scale
        self._save_slot  = save_slot
        # ── Modo online ───────────────────────────────────────────
        self._online_mode = online
        self._net_user    = net_user
        self._net_pass    = net_pass
        self._net         = None     # NetworkClient (iniciado após world estar pronto)
        self._my_eid      = -1       # entity_id atribuído pelo servidor
        # Outros jogadores: server_eid → local_eid (entidade ECS real)
        self._remote_players: dict[int, int] = {}
        # Mobs do servidor: server_eid → local_eid e reverse
        self._remote_mobs:         dict[int, int]          = {}
        self._remote_mobs_reverse: dict[int, int]          = {}  # local_eid → server_eid
        self._mob_hp:              dict[int, tuple[int,int]] = {}  # server_eid → (hp, max_hp)
        # Último alvo enviado ao servidor (evita reenvios desnecessários)
        self._net_last_target: int = -2
        # Última posição enviada ao servidor (evita envios duplicados)
        self._net_last_tx: int = -1
        self._net_last_ty: int = -1
        win_w = int(1280 * scale)
        win_h = int(720  * scale)
        # Renderiza na resolução nativa da janela — sem escala no frame final
        global SCREEN_WIDTH, SCREEN_HEIGHT
        SCREEN_WIDTH  = win_w
        SCREEN_HEIGHT = win_h
        self._display = pygame.display.set_mode(
            (win_w, win_h), pygame.DOUBLEBUF, vsync=1)
        self.screen   = pygame.Surface((win_w, win_h))
        pygame.display.set_caption("RPG ECS")
        self.clock = pygame.time.Clock()
        SOUNDS.init()

        # Aplica configurações de áudio salvas
        import config as _cfg
        _cfg_data = _cfg.load()
        SOUNDS.music_volume  = _cfg_data.get("music_volume",  0.4)
        SOUNDS.sfx_volume    = _cfg_data.get("sfx_volume",    1.0)
        SOUNDS.music_enabled = _cfg_data.get("music_enabled", True)
        SOUNDS.sfx_enabled   = _cfg_data.get("sfx_enabled",   True)

        global DEBUG_MODE, PROFILE_FRAMES
        DEBUG_MODE     = _cfg_data.get("debug_mode",     False)
        PROFILE_FRAMES = _cfg_data.get("profile_frames", False)

        # Filtra eventos irrelevantes — reduz custo do pump no Windows
        pygame.event.set_allowed([
            pygame.QUIT,
            pygame.KEYDOWN, pygame.KEYUP,
            pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
            pygame.MOUSEWHEEL, pygame.MOUSEMOTION,
        ])

        self._ui_scale: float = _cfg_data.get("ui_scale", 1.0)
        self._reload_ui_fonts()

        self.world = World()
        self.systems = []
        # _show_inventory e _show_talents → UIState component (via property)
        self._show_debug      = False
        self._debug_buttons: list = []      # [(rect, n_levels)] preenchido em _draw_debug_modal
        self._debug_tab: str = "nivel"      # "nivel" | "itens" | "ouro" | "mapa"
        self._debug_item_scroll: int = 0
        self._debug_item_buttons: list = []
        self._debug_gold_buttons: list = []
        self._debug_tab_buttons: list = []
        self._debug_item_catalog: list = []
        self._debug_map_buttons:  list = []
        self._debug_teleport_map: str = ""   # quando não-vazio, pending_destination → teleporte
        self._selected_inv_idx = -1
        self._current_map_file: str = MAP_FILES[0]
        self.transition_tiles: dict = {}   # (tile_x, tile_y) → trans_dict
        self._transition_cooldown = 0.0
        self._map_title_timer = 0.0
        self._cam_x = 0.0
        self._cam_y = 0.0
        # _zoom → Camera.zoom component (via property)
        self._zoom_min:  float = 0.75
        self._zoom_max:  float = 2.5
        self._zoom_step: float = 0.25
        self._zoom_surf: "pygame.Surface | None" = None
        self._zoom_surf_sz: tuple = (0, 0)
        self._pending_tooltip       = None  # (mx, my, title, lines) – render no fim do frame
        self._pending_skill_tooltip = None  # igual, mas usa font_xs nas linhas de descrição

        self._show_pause        = False
        self._pause_submenu     = ""
        self._pending_quit      = False
        self._sound_drag        = ""
        self._show_hotbar_editor = False
        self._hbe_drag_from: tuple | None = None  # ("panel", skill_id) | ("slot", idx)
        self._hbe_rebind_slot: int | None = None
        self._hbe_cons_drag_from: tuple | None = None  # ("panel", item_name) | ("slot", idx)
        self._hbe_cons_rebind_slot: int | None = None
        self._hbe_tab: int = 0                    # 0=Habilidades, 1=Atalhos
        self._mkb_rebind: "str | None" = None     # "menu:x" | "slot:0" | "cons:0"
        self._menu_keys: dict = {}                # carregado do config — veja _load_menu_keys
        self._hbe_skill_scroll: int = 0
        self._hbe_expand_slots: bool = False
        self._show_habilidades: bool = False     # painel Habilidades (tecla H)
        self._hab_scroll: int = 0               # scroll do painel Habilidades
        self._hab_drag_skill: "str | None" = None  # skill sendo arrastada do painel H
        self._orig_mouse_pos  = pygame.mouse.get_pos  # kept for compatibility
        # Zonas de ambient
        self._ambient_zones:    list = []   # [{name, ambient, rect:(x1,y1,x2,y2)}]
        self._default_ambient:  str  = ""   # ambient padrão do mapa
        self._current_zone:     str  = ""   # nome da zona onde o jogador está

        self._map_overlay   = MapOverlay(self.screen)
        self._minimap       = Minimap(self.screen, self._map_overlay)
        self._loading_save  = False

        self._load_map_and_entities()
        self._init_systems()
        self._talent_system = TalentSystem(self.world, self.player_entity, self.screen)
        self._shop_system   = ShopSystem(self.world, self.player_entity, self.screen)
        self._quest_system  = QuestSystem(self.world, self.player_entity)
        set_quest_system(self._quest_system)
        self._quest_dialog  = QuestDialogSystem(
            self.world, self.player_entity, self.screen, self._quest_system)
        self._quest_journal = QuestJournalSystem(
            self.world, self.player_entity, self.screen, self._quest_system)
        from crafting_system import BlacksmithSystem
        self._crafting_system = BlacksmithSystem(
            self.world, self.player_entity, self.screen,
            shop_system=self._shop_system,
            quest_dialog=self._quest_dialog,
            quest_system=self._quest_system,
        )
        from trainer_system import TrainerSystem
        self._trainer_system = TrainerSystem(
            self.world, self.player_entity, self.screen,
            quest_dialog=self._quest_dialog,
        )
        # Insere QuestSystem após xp_system (posição 13, depois do índice de xp_system)
        _xp_idx = next((i for i, s in enumerate(self.systems)
                        if isinstance(s, XPSystem)), len(self.systems))
        self.systems.insert(_xp_idx + 1, self._quest_system)

        if char_data:
            # Novo personagem — aplica nome/classe e ignora save existente
            char = self.world.get_component(self.player_entity, CharacterStats)
            if char:
                char.name     = char_data.get("name", "Aventureiro")
                char.class_id = char_data.get("class_id", "guerreiro")
                # Atributos base por classe — data-driven, sem if/elif por classe
                from stats_system import CLASS_BASE_STATS
                _base = CLASS_BASE_STATS.get(char.class_id, CLASS_BASE_STATS["guerreiro"])
                char.strength     = _base["strength"]
                char.intelligence = _base["intelligence"]
                char.agility      = _base["agility"]
                char.vitality     = _base["vitality"]
                char.defense      = _base["defense"]
            # Recalcula combat stats com os atributos da classe
            cs   = self.world.get_component(self.player_entity, CombatStats)
            perm = self.world.get_component(self.player_entity, PermanentStats)
            if char and cs:
                from stats_system import apply_char_stats_to_combat, sync_attack_interval
                from components import Equipment as _EqNew
                apply_char_stats_to_combat(char, cs, perm)
                sync_attack_interval(cs, self.world.get_component(self.player_entity, _EqNew))
                cs.current_hp = cs.max_hp
            # Cor do personagem por classe
            rend = self.world.get_component(self.player_entity, Renderable)
            if rend and char:
                _CLASS_COLORS = {"mago": (80, 80, 220), "arqueiro": (80, 200, 80)}
                rend.color = _CLASS_COLORS.get(char.class_id, (255, 0, 0))
            # Skills iniciais concedidas automaticamente (ex: Recarregar do arqueiro)
            if char:
                from skill_config import INITIAL_SKILLS_BY_CLASS, SKILL_CATALOG
                from components import PlayerSkills as _PS
                ps = self.world.get_component(self.player_entity, _PS)
                if ps:
                    for _sid in INITIAL_SKILLS_BY_CLASS.get(char.class_id, []):
                        ps.learned_skill_ids.add(_sid)
                        if ps.skill_by_id(_sid) is None:
                            _sk = _PS._make_skill(_sid, SKILL_CATALOG)
                            if _sk:
                                try:
                                    _idx = ps.skills.index(None)
                                    ps.skills[_idx] = _sk
                                except ValueError:
                                    ps.skills.append(_sk)
        else:
            self._apply_save()
        self._quest_system.auto_start_quests()
        self._quest_system.set_current_map(self._current_map_file)
        self._apply_hotbar_config(new_character=bool(char_data))
        self._load_menu_keys()

        # Registra autosave global — sistemas usam request_autosave() de save_system.py
        from save_system import register_autosave
        register_autosave(self._autosave)

        # Modo online: conecta ao servidor após o mundo estar pronto
        if self._online_mode:
            self._connect_online()

        # --- Profiler de frames ---
        self._prof_accum:       dict[str, float] = {}   # tempo acumulado por seção
        self._prof_peak:        dict[str, float] = {}   # pico por seção
        self._prof_frame_accum: dict[str, float] = {}   # acumulado do frame atual (spike)
        self._prof_frames: int = 0
        _PROF_INTERVAL     = 300                   # frames entre relatórios (~5 s)
        self._prof_interval = _PROF_INTERVAL
        self._prof_spike_ms = 10.0                 # ms para considerar spike em uma seção

    def _load_map_and_entities(self):
        map_file = MAP_FILES[0]
        self._current_map_file = map_file
        if "cave" in map_file:
            SOUNDS.set_context("cave")
            SOUNDS.play_ambient("ambient_cave")
        else:
            SOUNDS.set_context("surface")
            SOUNDS.play_ambient("ambient_surface")
        terrain_matrix, object_matrix, spawn_points, terrain_visual = load_map_csv(map_file)
        self._map_overlay.load_map(
            _merge_display_matrix(terrain_matrix, object_matrix), map_file)
        self._minimap.on_map_load()
        for w in validate_map(terrain_matrix):
            print(f"[MAPA] Aviso: {w}")

        self.tilemap_entity = create_tilemap(self.world, terrain_matrix, object_matrix, terrain_visual)
        tilemap_comp = self.world.get_component(self.tilemap_entity, Tilemap)
        self.map_width_px  = tilemap_comp.map_width_tiles  * TILE_SIZE
        self.map_height_px = tilemap_comp.map_height_tiles * TILE_SIZE
        self._build_transition_tiles(spawn_points)
        self._load_ambient_zones(spawn_points)

        px, py = spawn_points["player"] if spawn_points["player"] else (1, 1)
        self.player_entity = create_player(self.world, px, py, self._current_map_file)

        self._spawn_entities_from(spawn_points)

        self.camera_entity = create_camera(
            self.world, self.player_entity, SCREEN_WIDTH, SCREEN_HEIGHT
        )

    def _spawn_entities_from(self, spawn_points: dict):
        for col, row, enemy_type, enemy_tier in spawn_points.get("enemies", []):
            is_ranged = (enemy_type == "ranged")
            create_enemy(self.world, col, row,
                         attack_range=3 if is_ranged else 1,
                         is_ranged=is_ranged,
                         tier=enemy_tier)

        for zd in spawn_points.get("spawn_zones", []):
            create_spawn_zone(
                self.world,
                center_x=zd["x"], center_y=zd["y"],
                enemy_type=zd["enemy_type"], enemy_tier=zd["enemy_tier"],
                radius=zd["radius"], max_count=zd["count"],
                respawn_cooldown=zd["respawn_cooldown"],
                level_min=zd.get("level_min", 1), level_max=zd.get("level_max", 1),
                race=zd.get("race", "Humanoide"),
                entity_class=zd.get("entity_class", ""),
            )

        for col, row, shop_id, lvl, prof in spawn_points.get("merchants", []):
            create_merchant(self.world, col, row, shop_id=shop_id,
                            level=lvl, profession=prof)

        for col, row, name, quest_ids, turn_in_ids, lvl, prof in spawn_points.get("quest_givers", []):
            create_quest_giver(self.world, col, row, name=name,
                               quest_ids=quest_ids, turn_in_ids=turn_in_ids,
                               level=lvl, profession=prof)

        for col, row, name, shop_id, lvl, prof in spawn_points.get("blacksmiths", []):
            create_blacksmith(self.world, col, row, name=name,
                              shop_id=shop_id, level=lvl, profession=prof)

        for col, row, name, class_id, quest_ids, turn_in_ids, lvl, prof in spawn_points.get("trainers", []):
            create_trainer(self.world, col, row, name=name,
                           class_id=class_id, quest_ids=quest_ids, turn_in_ids=turn_in_ids,
                           level=lvl, profession=prof)

    def _build_transition_tiles(self, spawn_points: dict):
        self.transition_tiles = {}
        for t in spawn_points.get("transitions", []):
            key = (t["x"], t["y"])
            self.transition_tiles[key] = t

    def _load_ambient_zones(self, spawn_points: dict):
        """Armazena zonas de ambient do mapa recém-carregado e pré-carrega todos os áudios."""
        # Para sempre os áudios do mapa anterior antes de qualquer coisa
        SOUNDS.stop_ambient_stingers()
        SOUNDS.stop_music()
        self._ambient_zones   = spawn_points.get("ambient_zones", [])
        self._default_ambient = spawn_points.get("default_ambient", "")
        self._current_zone    = "\x00"  # sentinel: força reavaliação no 1º frame

        # Pré-carrega todos os sons das zonas para evitar freeze na primeira entrada
        all_stingers: list[str] = []
        all_music:    list[str] = []
        for z in self._ambient_zones:
            all_stingers.extend(z.get("ambient", []))
            all_music.extend(z.get("music", []))
        if all_stingers:
            SOUNDS.preload_stingers(all_stingers)
        if all_music:
            SOUNDS.preload_music(all_music)

    def _update_ambient_zone(self, tile_x: int, tile_y: int):
        """
        Verifica em qual zona de ambient o jogador está e dispara transições se mudou.
        Chamado a cada frame com a posição de tile do jogador.
        """
        new_zone        = ""
        new_stingers: list = []
        new_playlist: list = []
        for z in self._ambient_zones:
            x1, y1, x2, y2 = z["rect"]
            if x1 <= tile_x <= x2 and y1 <= tile_y <= y2:
                new_zone     = z["name"]
                new_stingers = z.get("ambient", [])
                new_playlist = z.get("music", [])
                break

        if new_zone == self._current_zone:
            return
        self._current_zone = new_zone

        # Stingers de ambient (SFX aleatórios 15-60 s)
        if new_stingers:
            SOUNDS.start_ambient_stingers(new_stingers)
        else:
            SOUNDS.stop_ambient_stingers()

        # Playlist de música sequencial
        if new_playlist:
            SOUNDS.play_music_playlist(new_playlist)
        else:
            SOUNDS.stop_music()

    def _init_systems(self):
        tile_validation = TileValidationSystem(self.world)
        pathfinding     = PathfindingSystem(self.world)
        combat          = CombatSystem(self.world)
        # Guarda refs para reset de cache na transição de mapa
        self._tile_validation_system = tile_validation
        self._pathfinding_system     = pathfinding
        self._combat_system          = combat

        # Registra serviços acessíveis por qualquer sistema sem acoplamento direto
        register_services(combat=combat, pathfinding=pathfinding,
                          tile_validation=tile_validation)

        death_handler         = DeathHandlerSystem(self.world)
        xp_system             = XPSystem(self.world, death_handler)
        skill_system          = SkillSystem(self.world, self.player_entity, self.screen)
        loot_system           = LootSystem(self.world, self.screen, self.player_entity)
        projectile_system     = ProjectileSystem(self.world, self.screen)
        render_system         = RenderSystem(self.world, self.screen)
        tile_render_system    = TileRenderSystem(self.world, self.screen)
        death_respawn_system  = DeathRespawnSystem(self.world)
        self._death_handler          = death_handler
        self._xp_system              = xp_system
        self._skill_system           = skill_system
        self._loot_system            = loot_system
        self._projectile_system      = projectile_system
        self._render_system          = render_system
        self._tile_render_system     = tile_render_system
        self._death_respawn_system   = death_respawn_system

        # Sistemas de magia (classe Mago)
        from spell_system import (ManaSystem, SpellCastSystem, PlayerProjectileSystem,
                                  ChannelingSystem, IceBlockSystem, FireShieldSystem,
                                  PirofagiaSystem, AoeTargetingSystem)
        self._mana_system           = ManaSystem(self.world)
        self._spell_cast_system     = SpellCastSystem(self.world, self.screen)
        self._player_proj_system    = PlayerProjectileSystem(self.world, self.screen)
        self._channeling_system     = ChannelingSystem(self.world, self.screen)
        self._ice_block_system      = IceBlockSystem(self.world)
        self._fire_shield_system    = FireShieldSystem(self.world)
        self._pirofagia_system      = PirofagiaSystem(self.world, self.screen)
        self._aoe_targeting_system  = AoeTargetingSystem(self.world, self.player_entity, self.screen)

        self._god_mode = GodModeEditor(
            world             = self.world,
            screen            = self.screen,
            tile_render_system= tile_render_system,
            get_map_file      = lambda: self._current_map_file,
            on_map_changed    = self._on_god_mode_save,
        )

        # Ordem dos sistemas por frame — ALTERE COM CUIDADO.
        # As restrições de dependência são verificadas em runtime por _validate_system_order().
        # Se a ordem for violada, o jogo levanta RuntimeError na inicialização.
        self.systems = [
            tile_validation,                                                          # 1
            self._aoe_targeting_system,                                               # 2 (antes do targeting)
            MouseTargetingSystem(self.world, self.player_entity, self.screen),        # 3
            loot_system,                                                              # 4
            PlayerInputSystem(self.world, self.screen),                                   # 5
            skill_system,                                                             # 6
            # IA e spawn de mobs: gerenciados pelo servidor no modo online
            *([EnemyAISystem(self.world, self.player_entity),                         # 7
               EnemyAbilitySystem(self.world, self.player_entity)]                    # 8
              if not self._online_mode else []),
            projectile_system,                                                        # 9
            self._player_proj_system,                                                 # 10
            self._spell_cast_system,                                                  # 11
            self._channeling_system,                                                  # 12
            self._ice_block_system,                                                   # 13
            self._fire_shield_system,                                                 # 14
            self._pirofagia_system,                                                   # 15
            self._mana_system,                                                        # 15
            death_handler,                                                            # 15
            CorpseSystem(self.world),                                                 # 16
            *([SpawnZoneSystem(self.world)] if not self._online_mode else []),        # 17
            xp_system,                                                                # 18
            death_respawn_system,                                                     # 19
            ConsumableSystem(self.world),                                             # 20
            CombatStateSystem(self.world),                                            # 21
            StatusEffectSystem(self.world),                                           # 22
            TileMovementSystem(self.world),                                           # 23
            FogSystem(self.world),                                                    # 24
            CameraSystem(self.world),                                                 # 25
            tile_render_system,                                                       # 26
            render_system,                                                            # 27
        ]
        self._validate_system_order()
        self._validate_skill_handlers()

        # Inicializa world_surf e hud_surf em todos os sistemas.
        # world_surf começa como self.screen (zoom=1); será atualizado cada frame.
        # zoom_surf criada com tamanho inicial (zoom=1). world_surf é atribuída
        # a cada sistema todo frame pelo render loop — não precisa estar aqui.
        self._zoom_surf    = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        self._zoom_surf_sz = (SCREEN_WIDTH, SCREEN_HEIGHT)

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def _on_god_mode_save(self) -> None:
        """Chamado pelo GodModeEditor após salvar — atualiza minimap/overlay."""
        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            from tileset import OBJECT_MAPPING
            self._map_overlay.load_map(
                _merge_display_matrix(tilemap_comp.terrain_matrix,
                                      tilemap_comp.object_matrix),
                self._current_map_file,
            )
            self._minimap.on_map_load()
            break

    def _autosave(self) -> None:
        """Salva o estado atual se não estiver no meio de um carregamento."""
        if not self._loading_save:
            save_game(self.world, self.player_entity, self._current_map_file, self._save_slot)

    # ------------------------------------------------------------------
    # Validação de ordem de sistemas
    # ------------------------------------------------------------------

    # Cada tupla: (Tipo_que_deve_vir_antes, Tipo_que_deve_vir_depois, motivo)
    _SYSTEM_ORDER_CONSTRAINTS = [
        (TileValidationSystem,  PlayerInputSystem,    "Input depende de tiles bloqueados calculados"),
        (TileValidationSystem,  EnemyAISystem,        "AI depende de tiles bloqueados calculados"),
        (MouseTargetingSystem,  PlayerInputSystem,    "Input precisa do alvo já definido"),
        (PlayerInputSystem,     SkillSystem,          "Skills processam após input do jogador"),
        (PlayerInputSystem,     CombatStateSystem,    "Timers de combate atualizados após input"),
        (EnemyAISystem,         CombatStateSystem,    "Timers de combate atualizados após AI"),
        (DeathHandlerSystem,    XPSystem,             "XPSystem lê pending_xp de DeathHandler"),
        (XPSystem,              DeathRespawnSystem,   "Player respawn ocorre após XP distribuído"),
        (CombatStateSystem,     TileMovementSystem,   "Movimento interpolado após estados atualizados"),
        (TileMovementSystem,    FogSystem,            "Fog lê posição de tile após movimento"),
        (FogSystem,             CameraSystem,         "Câmera segue posições após movimento"),
        (CameraSystem,          TileRenderSystem,     "Tiles renderizados com câmera já calculada"),
        (TileRenderSystem,      RenderSystem,         "Entidades renderizadas sobre os tiles"),
    ]

    def _validate_system_order(self) -> None:
        """
        Verifica se as restrições de dependência entre sistemas estão satisfeitas.
        Levanta RuntimeError na inicialização caso a ordem esteja errada.
        """
        type_index = {type(s): i for i, s in enumerate(self.systems)}
        errors = []
        for before_type, after_type, reason in self._SYSTEM_ORDER_CONSTRAINTS:
            idx_before = type_index.get(before_type)
            idx_after  = type_index.get(after_type)
            if idx_before is None or idx_after is None:
                continue  # sistema ausente — não valida
            if idx_before >= idx_after:
                errors.append(
                    f"  {before_type.__name__} (pos {idx_before}) deve vir ANTES de "
                    f"{after_type.__name__} (pos {idx_after}): {reason}"
                )
        if errors:
            raise RuntimeError(
                "ORDEM DE SISTEMAS INVÁLIDA:\n" + "\n".join(errors)
            )

    def _validate_skill_handlers(self) -> None:
        """Verifica em startup que toda skill no catálogo tem handler e que
        abilities referenciadas por mobs existem em ABILITY_DEFS."""
        from skill_config import SKILL_CATALOG
        missing_handlers = [sid for sid in SKILL_CATALOG
                            if not hasattr(self._skill_system, f"_skill_{sid}")]
        if missing_handlers:
            print(f"[WARN] Skills sem handler em SkillSystem: {missing_handlers}")

        try:
            from enemy_abilities_data import ABILITY_DEFS, MOB_ABILITIES
            for mob_id, abilities in MOB_ABILITIES.items():
                for ability_id, _ in abilities:
                    if ability_id not in ABILITY_DEFS:
                        print(f"[WARN] Mob '{mob_id}' referencia ability inexistente: '{ability_id}'")
        except Exception:
            pass

    def _apply_save(self):
        """Carrega o save do slot ativo e reposiciona o jogador no mapa salvo."""
        if not has_save(self._save_slot):
            return

        self._loading_save = True
        pos_data = load_game(self.world, self.player_entity, self._save_slot)
        if pos_data is None:
            self._loading_save = False
            return

        # Recalcula atributos com talentos e stats permanentes
        self._talent_system.apply_talent_effects()
        char = self.world.get_component(self.player_entity, CharacterStats)
        cs   = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        if char and cs:
            # Migração de save: recalcula atributos base a partir de classe+nível,
            # garantindo consistência com CLASS_BASE_STATS e CLASS_LEVEL_GAINS atuais.
            from stats_system import CLASS_BASE_STATS, CLASS_LEVEL_GAINS
            _base   = CLASS_BASE_STATS.get(char.class_id, CLASS_BASE_STATS["guerreiro"])
            _gains  = CLASS_LEVEL_GAINS.get(char.class_id, {})
            _lvls   = max(0, char.level - 1)
            char.strength     = _base["strength"]     + _gains.get("strength",     0) * _lvls
            char.intelligence = _base["intelligence"] + _gains.get("intelligence", 0) * _lvls
            char.agility      = _base["agility"]      + _gains.get("agility",      0) * _lvls
            char.vitality     = _base["vitality"]     + _gains.get("vitality",     0) * _lvls
            char.defense      = _base["defense"]      + _gains.get("defense",      0) * _lvls

            from stats_system import apply_char_stats_to_combat, sync_attack_interval
            from components import Equipment as _EqLoad
            apply_char_stats_to_combat(char, cs, perm)
            sync_attack_interval(cs, self.world.get_component(self.player_entity, _EqLoad))
            # Restaura HP salvo (proporcional ao max_hp recalculado)
            if cs._saved_hp > 0:
                cs.current_hp = min(float(cs._saved_hp), cs.max_hp)
                cs._saved_hp  = 0
                cs._saved_max = 0
        # Cor do personagem por classe (atualiza ao carregar save)
        if char:
            rend = self.world.get_component(self.player_entity, Renderable)
            if rend:
                _CLASS_COLORS = {"mago": (80, 80, 220), "arqueiro": (80, 200, 80)}
                rend.color = _CLASS_COLORS.get(char.class_id, (255, 0, 0))

        # Carrega mapa correto se diferente do atual
        saved_map = pos_data.get("map", "")
        tx = pos_data.get("tile_x", 1)
        ty = pos_data.get("tile_y", 1)
        if saved_map and saved_map != self._current_map_file:
            self._do_transition({"target_map": saved_map, "target_x": tx, "target_y": ty})
        else:
            self._reposition_player(tx, ty)

        self._loading_save = False
        LOG.add("Partida carregada.", (100, 220, 100))

    # ------------------------------------------------------------------
    # ── Properties que roteiam para components ECS ───────────────────────────

    def _get_cam(self) -> "Camera | None":
        return self.world.get_component(self.camera_entity, Camera)

    def _get_ui(self) -> "UIState | None":
        return self.world.get_component(self.player_entity, UIState)

    def _get_shop_ui(self) -> "ShopUIState | None":
        return self.world.get_component(self.player_entity, ShopUIState)

    def _get_loot_ui(self) -> "LootUIState | None":
        return self.world.get_component(self.player_entity, LootUIState)

    @property
    def _zoom(self) -> float:
        if not hasattr(self, 'camera_entity'):
            return 1.0
        cam = self._get_cam()
        return cam.zoom if cam else 1.0

    @_zoom.setter
    def _zoom(self, value: float) -> None:
        if not hasattr(self, 'camera_entity'):
            return
        cam = self._get_cam()
        if cam:
            cam.zoom = value

    @property
    def _show_inventory(self) -> bool:
        if not hasattr(self, 'player_entity'):
            return False
        ui = self._get_ui()
        return ui.show_inventory if ui else False

    @_show_inventory.setter
    def _show_inventory(self, value: bool) -> None:
        if not hasattr(self, 'player_entity'):
            return
        ui = self._get_ui()
        if ui:
            ui.show_inventory = value

    @property
    def _show_talents(self) -> bool:
        if not hasattr(self, 'player_entity'):
            return False
        ui = self._get_ui()
        return ui.show_talents if ui else False

    @_show_talents.setter
    def _show_talents(self, value: bool) -> None:
        if not hasattr(self, 'player_entity'):
            return
        ui = self._get_ui()
        if ui:
            ui.show_talents = value

    # ------------------------------------------------------------------
    # ── UI Scale — fontes e helper de pixel ───────────────────────────────────

    _UI_FONT_BASES = {"xs": 18, "sm": 22, "md": 28, "lg": 36}

    def _reload_ui_fonts(self) -> None:
        """Recarrega font_xs/sm/md/lg no tamanho escalado. Chamado na init e ao mudar ui_scale."""
        s = self._ui_scale
        self.font_xs = _font(round(self._UI_FONT_BASES["xs"] * s))
        self.font_sm = _font(round(self._UI_FONT_BASES["sm"] * s))
        self.font_md = _font(round(self._UI_FONT_BASES["md"] * s))
        self.font_lg = _font(round(self._UI_FONT_BASES["lg"] * s))

    def _u(self, px: int) -> int:
        """Converte pixels base para pixels escalados pela UI scale."""
        return max(1, round(px * self._ui_scale))

    def _set_ui_scale(self, value: float) -> None:
        """Altera ui_scale, recarrega fontes e salva no config."""
        self._ui_scale = round(max(0.5, min(3.0, value)), 2)
        self._reload_ui_fonts()
        import config as _cfg
        _cfg.save({"ui_scale": self._ui_scale})

    # ------------------------------------------------------------------
    # ── Fechamento de modais — ESC unificado ─────────────────────────────────

    def _close_top_modal(self) -> bool:
        """Fecha o modal de maior prioridade atualmente aberto.
        Retorna True se fechou algo, False se nada estava aberto."""
        # Rebind ativo no editor de atalhos — cancela só o rebind, não fecha o painel
        if self._mkb_rebind is not None:
            self._mkb_rebind = None
            return True
        # Painel de Habilidades
        if self._show_habilidades:
            self._show_habilidades = False
            self._hab_drag_skill   = None
            return True
        if self._map_overlay.is_open:
            SOUNDS.play_ui("map_close")
            self._map_overlay.is_open = False
            return True
        if self._loot_system.open_corpse_id != -1:
            self._loot_system._close_modal()
            return True
        if self._crafting_system.is_open:
            self._crafting_system._close()
            return True
        if self._trainer_system.is_open:
            self._trainer_system._close()
            return True
        if self._quest_dialog.is_open:
            self._quest_dialog._close()
            return True
        if self._quest_journal.is_open:
            self._quest_journal.close()
            return True
        if self._shop_system.is_open:
            self._shop_system._close()
            return True
        if self._show_hotbar_editor:
            self._close_hotbar_editor()
            return True
        if self._show_debug:
            self._show_debug = False
            return True
        if self._show_talents:
            SOUNDS.play_ui("talent_close")
            self._show_talents = False
            return True
        if self._show_inventory:
            SOUNDS.play_ui("inventory_close")
            self._show_inventory  = False
            self._selected_inv_idx = -1
            return True
        if self._show_pause:
            if self._pause_submenu:
                self._pause_submenu = ""
                self._sound_drag    = ""
            else:
                self._show_pause = False
            return True
        return False

    # ------------------------------------------------------------------
    # ── Zoom ──────────────────────────────────────────────────────────────────

    def _handle_scroll_zoom(self, scroll_y: int) -> None:
        """Aplica zoom com scroll do mouse — apenas quando o cursor está sobre a área do jogo."""
        mx, my = pygame.mouse.get_pos()

        # Ignora se qualquer modal de UI está aberto, ou se o God Mode está ativo
        if (self._show_inventory or self._show_talents or self._show_debug
                or self._map_overlay.is_open or self._show_pause
                or self._show_hotbar_editor or self._god_mode.active
                or self._show_habilidades
                or getattr(self._shop_system, "is_open", False)
                or getattr(self._quest_journal, "is_open", False)):
            return

        step = self._zoom_step if scroll_y > 0 else -self._zoom_step
        new_zoom = round(max(self._zoom_min, min(self._zoom_max, self._zoom + step)), 10)
        if new_zoom != self._zoom:
            self._zoom = new_zoom
            self._tile_render_system.invalidate_cache()

    _WORLD_SYSTEM_ATTRS = (
        "_tile_render_system", "_render_system", "_loot_system",
        "_projectile_system",  "_player_proj_system",
        "_channeling_system",  "_aoe_targeting_system",
        "_spell_cast_system",  "_shop_system",
        "_quest_dialog",       "_crafting_system", "_trainer_system",
    )

    def _assign_world_surf(self, surf: "pygame.Surface") -> None:
        """Atribui world_surf a todos os sistemas de mundo. Chamado todo frame."""
        for attr in self._WORLD_SYSTEM_ATTRS:
            s = getattr(self, attr, None)
            if s is not None:
                s.world_surf = surf
        for s in self.systems:
            s.world_surf = surf

    # ── Modais ────────────────────────────────────────────────────────────────

    def _close_all_modals(self):
        """Fecha todos os modais abertos e emite som do modal que estava aberto."""
        if self._map_overlay.is_open:
            SOUNDS.play_ui("map_close")
        elif self._show_inventory:
            SOUNDS.play_ui("inventory_close")
        elif self._show_talents:
            SOUNDS.play_ui("talent_close")
        self._dt              = 0.0
        self._show_inventory  = False
        self._selected_inv_idx = -1
        self._show_talents    = False
        self._show_debug      = False
        if self._loot_system.open_corpse_id != -1:
            self._loot_system._close_modal()
        if self._shop_system.is_open:
            self._shop_system._close()
        if self._crafting_system.is_open:
            self._crafting_system._close()
        if self._trainer_system.is_open:
            self._trainer_system._close()
        self._quest_dialog._pending_npc_id = -1
        if self._quest_dialog.is_open:
            self._quest_dialog._close()
        if self._quest_journal.is_open:
            self._quest_journal.close()
        self._map_overlay.is_open = False
        self._show_pause         = False
        self._pause_submenu      = ""
        self._sound_drag         = ""
        self._show_hotbar_editor = False
        self._hbe_drag_from      = None
        self._hbe_rebind_slot    = None
        self._hbe_tab            = 0
        self._mkb_rebind         = None

    # Game loop
    # ------------------------------------------------------------------

    def _scale_events(self, events: list) -> list:
        """Renderização nativa: event.pos já está no espaço da janela, sem conversão."""
        # Colapsa MOUSEMOTION intermediários para reduzir custo no Windows
        last_motion = None
        for e in events:
            if e.type == pygame.MOUSEMOTION:
                last_motion = e
        if last_motion is None:
            return events
        return [e for e in events if e.type != pygame.MOUSEMOTION or e is last_motion]

    # ------------------------------------------------------------------
    # Helpers do profiler
    # ------------------------------------------------------------------

    def _prof_record(self, label: str, elapsed: float) -> None:
        """Acumula tempo (segundos) de uma seção no profiler."""
        self._prof_accum[label] = self._prof_accum.get(label, 0.0) + elapsed
        if elapsed > self._prof_peak.get(label, 0.0):
            self._prof_peak[label] = elapsed
        self._prof_frame_accum[label] = self._prof_frame_accum.get(label, 0.0) + elapsed

    def _prof_report(self) -> None:
        """Imprime relatório acumulado e reseta contadores."""
        n = max(self._prof_frames, 1)
        rows = sorted(self._prof_accum.items(), key=lambda x: -x[1])
        total_avg = sum(v for _, v in rows) / n * 1000
        print(f"\n[PROF] {n} frames | avg frame total: {total_avg:.2f} ms")
        print(f"  {'Seção':<30} {'avg ms':>8} {'peak ms':>9}")
        print(f"  {'─'*50}")
        for label, acc in rows:
            avg_ms  = acc / n * 1000
            peak_ms = self._prof_peak.get(label, 0.0) * 1000
            bar = "!" if peak_ms > self._prof_spike_ms else " "
            print(f"  {bar}{label:<29} {avg_ms:>8.3f} {peak_ms:>9.3f}")
        self._prof_accum  = {}
        self._prof_peak        = {}
        self._prof_frames      = 0
        self._prof_frame_accum: dict[str, float] = {}   # acumulado do frame atual (spike)

    def run(self):
        _gc.disable()          # GC manual — evita pauses aleatórias no loop de jogo
        _gc_counter = 0
        running = True
        while running:
            dt = self.clock.tick_busy_loop(FPS) / 1000.0
            _gc_counter += 1
            if _gc_counter >= FPS * 10:   # coleta a cada ~10s, entre frames
                _gc.collect()
                _gc_counter = 0
            self._dt = dt
            SOUNDS.new_frame()  # limpa deduplicação de sons
            # Processa mensagens da rede antes de qualquer sistema
            if self._online_mode:
                self._process_network()
            _t0 = _time.perf_counter()
            events = self._scale_events(pygame.event.get())
            if PROFILE_FRAMES:
                self._prof_record("events", _time.perf_counter() - _t0)

            # God Mode consome eventos quando ativo (bloqueia input do jogo).
            # Salva estado ANTES de handle_events: se o god mode fechar via F10/Esc
            # dentro de handle_events, o estado pré-frame ainda bloqueia os eventos.
            _god_was_active = self._god_mode.active
            if _god_was_active:
                self._god_mode.handle_events(events,
                                             getattr(self, "_cam_x", 0),
                                             getattr(self, "_cam_y", 0))

            for event in events:
                if event.type == pygame.QUIT:
                    running = False
                elif _god_was_active:
                    pass   # god mode consumiu — ignora input do jogo
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if not self._close_top_modal():
                            self._show_pause = True
                    elif self._show_hotbar_editor or self._show_habilidades:
                        pass   # modais abertos: bloqueia atalhos de menu
                    elif event.key == pygame.K_F10:
                        self._close_all_modals()
                        self._god_mode.toggle()
                        if self._god_mode.active and self._zoom != 1.0:
                            self._zoom = 1.0
                            self._tile_render_system.invalidate_cache()
                    elif event.key == self._menu_keys.get("mapa", pygame.K_m):
                        already_open = self._map_overlay.is_open
                        self._close_all_modals()
                        if not already_open:
                            player_tm = self.world.get_component(
                                self.player_entity, TileMovement)
                            tx = player_tm.current_tile_x if player_tm else -1
                            ty = player_tm.current_tile_y if player_tm else -1
                            self._map_overlay.toggle(tx, ty)
                            SOUNDS.play_ui("map_open")
                    elif event.key == self._menu_keys.get("inventario", pygame.K_i):
                        already_open = self._show_inventory
                        self._close_all_modals()
                        if not already_open:
                            self._show_inventory = True
                            SOUNDS.play_ui("inventory_open")
                    elif event.key == pygame.K_DELETE and self._show_inventory:
                        inv = self.world.get_component(self.player_entity, Inventory)
                        if inv and 0 <= self._selected_inv_idx < len(inv.items):
                            inv.items.pop(self._selected_inv_idx)
                            self._selected_inv_idx = -1
                    elif event.key == self._menu_keys.get("talentos", pygame.K_t):
                        already_open = self._show_talents
                        self._close_all_modals()
                        if not already_open:
                            self._show_talents = True
                            SOUNDS.play_ui("talent_open")
                    elif event.key == self._menu_keys.get("diario", pygame.K_j):
                        already_open = self._quest_journal.is_open
                        self._close_all_modals()
                        if not already_open:
                            self._quest_journal.open()
                    elif event.key == pygame.K_h:
                        self._show_habilidades = not self._show_habilidades
                        self._hab_scroll       = 0
                        self._hab_drag_skill   = None
                    elif event.key in (pygame.K_EQUALS, pygame.K_KP_PLUS) and not self._god_mode.active:
                        new_zoom = min(self._zoom_max, round(self._zoom + self._zoom_step, 10))
                        if new_zoom != self._zoom:
                            self._zoom = new_zoom
                            self._tile_render_system.invalidate_cache()
                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS) and not self._god_mode.active:
                        new_zoom = max(self._zoom_min, round(self._zoom - self._zoom_step, 10))
                        if new_zoom != self._zoom:
                            self._zoom = new_zoom
                            self._tile_render_system.invalidate_cache()
                    elif event.key == pygame.K_F12 and DEBUG_MODE:
                        already_open = self._show_debug
                        self._close_all_modals()
                        if not already_open:
                            self._show_debug = True
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and self._show_inventory):
                    self._handle_inventory_click(event)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self._show_debug:
                        self._handle_debug_click(event)
                    elif event.button == 1 and not self._show_hotbar_editor:
                        self._handle_hotbar_click(event)
                        self._handle_consumable_bar_click(event)
                elif event.type == pygame.MOUSEWHEEL and self._show_debug:
                    if self._debug_tab == "itens" and self._debug_item_catalog:
                        max_sc = max(0, len(self._debug_item_catalog) - 8)
                        self._debug_item_scroll = max(0, min(max_sc,
                                                             self._debug_item_scroll - event.y))
                elif event.type == pygame.MOUSEWHEEL:
                    self._handle_scroll_zoom(event.y)

            # Mapa consome eventos quando aberto
            if self._map_overlay.is_open:
                for event in events:
                    self._map_overlay.handle_event(event)

            # Árvore de talentos consome eventos quando aberta
            if self._show_talents:
                panel = self._talent_system._panel_rect()
                self._talent_system.handle_events(events, panel)
                if self._talent_system.wants_close:
                    self._show_talents = False

            # BlacksmithSystem roda ANTES de shop/quest para consumir cliques nos ferreiros
            self._crafting_system.update(events, dt)
            if self._crafting_system.is_open:
                self._crafting_system.handle_events(events)

            # TrainerSystem roda após crafting, antes de shop/quest
            _after_craft_ev = (
                [e for e in events
                 if not (e.type == pygame.MOUSEBUTTONDOWN and e.button == 3)]
                if self._crafting_system._right_click_consumed else events
            )
            self._trainer_system.update(_after_craft_ev, dt)
            if self._trainer_system.is_open:
                self._trainer_system.handle_events(events)

            # Filtra right-click de shop/quest se crafting ou trainer já consumiu
            _craft_ev = (
                [e for e in events
                 if not (e.type == pygame.MOUSEBUTTONDOWN and e.button == 3)]
                if (self._crafting_system._right_click_consumed
                    or self._trainer_system._right_click_consumed) else events
            )

            # Shop recebe eventos (possivelmente filtrados se crafting consumiu o clique)
            self._shop_system.update(_craft_ev, dt)
            if self._shop_system.is_open:
                self._shop_system.handle_events(events)

            # Quest dialog recebe eventos (possivelmente filtrados)
            self._quest_dialog.update(_craft_ev, dt)
            if self._quest_dialog.is_open:
                self._quest_dialog.handle_events(events)

            # Diário de quests recebe eventos quando aberto
            if self._quest_journal.is_open:
                self._quest_journal.handle_events(events)

            # Clique no minimap — detectado ANTES dos sistemas para consumir o evento
            _minimap_click_consumed = False
            if not self._map_overlay.is_open:
                _player_tm_mm = self.world.get_component(self.player_entity, TileMovement)
                if _player_tm_mm:
                    for _ev in events:
                        if _ev.type == pygame.MOUSEBUTTONDOWN and _ev.button == 3:
                            _mm_tile = self._minimap.screen_to_tile(
                                _ev.pos[0], _ev.pos[1],
                                _player_tm_mm.current_tile_x,
                                _player_tm_mm.current_tile_y,
                            )
                            if _mm_tile is not None:
                                from components import PlayerAutoMove
                                for _, _auto in self.world.get_entities_with(PlayerAutoMove):
                                    _auto.ground_target = _mm_tile
                                    _auto.path          = []
                                    _auto.active        = True
                                    break
                                _minimap_click_consumed = True
                                break

            # Bloqueia sistemas enquanto um painel estiver aberto ou clique do mapa pendente
            systems_events = events
            if self._show_hotbar_editor:
                # Editor aberto: sistemas não recebem nenhum evento de input
                systems_events = []
            elif (_minimap_click_consumed
                    or self._map_overlay.is_open or self._map_overlay.pending_destination is not None
                    or self._show_inventory or self._show_talents
                    or (self._show_debug and DEBUG_MODE)
                    or self._crafting_system.is_open
                    or self._crafting_system._right_click_consumed
                    or self._trainer_system.is_open
                    or self._trainer_system._right_click_consumed
                    or self._shop_system.is_open
                    or self._shop_system._right_click_consumed
                    or self._quest_dialog.is_open
                    or self._quest_dialog._right_click_consumed
                    or self._quest_journal.is_open
                    or self._loot_system.open_corpse_id != -1):
                systems_events = [e for e in events
                                  if not (e.type == pygame.MOUSEBUTTONDOWN
                                          and e.button in (1, 3))]
            _shop_was_open_before  = self._shop_system.is_open
            _loot_was_open_before  = self._loot_system.open_corpse_id != -1
            for system in self.systems:
                # LootSystem sempre recebe eventos brutos (precisa detectar cliques no modal)
                ev = events if system is self._loot_system else systems_events
                if PROFILE_FRAMES:
                    _ts = _time.perf_counter()
                    system.update(ev, dt)
                    self._prof_record(f"upd:{type(system).__name__}", _time.perf_counter() - _ts)
                else:
                    system.update(ev, dt)

            # Sincronização online: movimento + alvo de combate
            if self._online_mode:
                self._send_player_move()
                self._sync_combat_target()

            # Se shop ou loot acabaram de abrir, fechar os outros modais
            if (not _shop_was_open_before and self._shop_system.is_open) or \
               (not _loot_was_open_before and self._loot_system.open_corpse_id != -1):
                self._show_inventory  = False
                self._selected_inv_idx = -1
                self._show_talents    = False
                self._map_overlay.is_open = False

            # Destino do mapa (M) aplicado APÓS systems.update — evita sobrescrita pelo MouseTargetingSystem
            if self._map_overlay.pending_destination is not None:
                tx, ty = self._map_overlay.pending_destination
                self._map_overlay.pending_destination = None
                if self._debug_teleport_map:
                    # Debug: teleporte imediato, possivelmente trocando de mapa
                    target = self._debug_teleport_map
                    self._debug_teleport_map = ""
                    self._do_transition({"target_map": target, "target_x": tx, "target_y": ty})
                else:
                    from components import PlayerAutoMove
                    for _, auto in self.world.get_entities_with(PlayerAutoMove):
                        auto.ground_target = (tx, ty)
                        auto.path          = []
                        auto.active        = True
                        break

            # Se overlay de debug foi cancelado (fechou sem clicar), restaura mapa ativo atual
            if not self._map_overlay.is_open and self._debug_teleport_map:
                self._debug_teleport_map = ""
                self._map_overlay.set_active_map(self._current_map_file)

            # Limpa marcador X do mapa quando ground_target foi cancelado ou chegou ao destino
            if self._map_overlay._dest_marker is not None:
                from components import PlayerAutoMove
                _auto = self.world.get_component(self.player_entity, PlayerAutoMove)
                if _auto is None or _auto.ground_target is None:
                    self._map_overlay._dest_marker = None

            # --- Respawn de morte: troca de mapa antes de qualquer outra coisa ---
            if self._death_respawn_system.pending_respawn:
                self._do_transition(self._death_respawn_system.pending_respawn)
                self._death_respawn_system.pending_respawn = None

            # --- Detecção de transição (cavernas / portais) ---
            if self._transition_cooldown > 0:
                self._transition_cooldown -= dt
            elif self.transition_tiles:
                player_tm = self.world.get_component(self.player_entity, TileMovement)
                if player_tm and not player_tm.is_moving:
                    key = (player_tm.current_tile_x, player_tm.current_tile_y)
                    if key in self.transition_tiles:
                        self._do_transition(self.transition_tiles[key])

            if self._map_title_timer > 0:
                self._map_title_timer -= dt

            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            LOG.update(dt)
            FLT.update(dt)
            PROC.update(dt)
            WARN.update(dt)
            DASH_TRAIL.update(dt)
            SOUNDS.update(dt)
            # Verifica zona de ambient do jogador
            _ptm = self.world.get_component(self.player_entity, TileMovement)
            if _ptm:
                self._update_ambient_zone(_ptm.current_tile_x, _ptm.current_tile_y)
            if PROFILE_FRAMES:
                self._prof_record("sounds+ambient", _time.perf_counter() - _ts)

            camera_pos = self.world.get_component(self.camera_entity, Position)

            # ── Zoom surf: dimensiona a world_surf uma vez por frame ──────────
            z        = self._zoom
            _panel_w = self._god_mode._panel_w if self._god_mode.active else 0
            dest_w   = SCREEN_WIDTH - _panel_w          # largura da área de jogo
            lw       = max(1, int(dest_w   / z))        # largura lógica do mundo
            lh       = max(1, int(SCREEN_HEIGHT / z))   # altura  lógica do mundo
            if self._zoom_surf_sz != (lw, lh):
                self._zoom_surf    = pygame.Surface((lw, lh))
                self._zoom_surf_sz = (lw, lh)
                self._tile_render_system.invalidate_cache()
            # Atribui world_surf todo frame — garante que sistemas inicializados
            # depois do _init_systems (quest, crafting, trainer) sempre recebam a surf correta
            self._assign_world_surf(self._zoom_surf)

            # cam_x/cam_y derivam das dimensões da world_surf — consistente com
            # _get_camera_offset() de qualquer sistema
            cam_x = (camera_pos.x - lw / 2) if camera_pos else 0
            cam_y = (camera_pos.y - lh / 2) if camera_pos else 0
            self._cam_x, self._cam_y = cam_x, cam_y

            # ── Passe de mundo — renderiza em world_surf ──────────────────────
            self._zoom_surf.fill((0, 0, 0))
            self.screen.fill((0, 0, 0))

            for system in self.systems:
                if system is not self._projectile_system \
                        and system is not self._loot_system \
                        and system is not self._render_system \
                        and system is not self._pirofagia_system \
                        and system is not self._spell_cast_system:
                    if PROFILE_FRAMES:
                        _ts = _time.perf_counter()
                        system.render(cam_x, cam_y)
                        self._prof_record(f"rnd:{type(system).__name__}", _time.perf_counter() - _ts)
                    else:
                        system.render(cam_x, cam_y)
            self._loot_system.render_world(cam_x, cam_y)
            # Objetos no_ysort (ex: escadas) renderizam aqui, abaixo das entidades
            self._tile_render_system.render_static_objects(cam_x, cam_y)
            DASH_TRAIL.render(self._zoom_surf, cam_x, cam_y)
            _world_objs = self._tile_render_system.get_world_objects(cam_x, cam_y)
            self._render_system.render(cam_x, cam_y, world_objects=_world_objs)
            self._shop_system.render_world(cam_x, cam_y)
            self._quest_dialog.render_world(cam_x, cam_y)
            self._crafting_system.render_world(cam_x, cam_y)
            self._trainer_system.render_world(cam_x, cam_y)
            self._tile_render_system.render_fog()
            self._projectile_system.render(cam_x, cam_y)
            self._player_proj_system.render(cam_x, cam_y)
            self._channeling_system.render(cam_x, cam_y)
            self._aoe_targeting_system.render(cam_x, cam_y)
            self._pirofagia_system.render(cam_x, cam_y)
            self._spell_cast_system.render(cam_x, cam_y)
            if self._online_mode:
                self._draw_remote_players(cam_x, cam_y)
                self._draw_mob_hp_bars(cam_x, cam_y)
            FLT.render(self._zoom_surf, cam_x, cam_y)

            # ── Escala world_surf → área de jogo na tela nativa (pixel-perfect) ─
            # subsurface evita alocação extra e resolve o caso do painel do God Mode
            # (dest_w < SCREEN_WIDTH quando o painel está aberto).
            _dest = self.screen.subsurface((0, 0, dest_w, SCREEN_HEIGHT))
            pygame.transform.scale(self._zoom_surf, (dest_w, SCREEN_HEIGHT), _dest)
            # Notificações de proc: screen-space, abaixo do player, acima dos avisos
            PROC.render(self.screen)
            # Avisos de ação bloqueada: posição fixa, abaixo do centro
            WARN.render(self.screen)
            # Vinheta vermelha pulsante quando HP < 30%
            self._draw_low_hp_vignette()
            # HUD de conexão online (canto superior direito)
            if self._online_mode:
                self._draw_online_hud()

            self._pending_tooltip       = None
            self._pending_skill_tooltip = None
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._draw_hud()
            if PROFILE_FRAMES:
                self._prof_record("hud:draw_hud", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            self._draw_hotbar()
            self._draw_consumable_bar()
            if PROFILE_FRAMES:
                self._prof_record("hud:hotbar", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            if not self._show_inventory:
                self._draw_world_tooltip()
            if PROFILE_FRAMES:
                self._prof_record("hud:tooltip", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            LOG.draw(self.screen, self.font_sm, x=10, bottom_y=SCREEN_HEIGHT - 78)

            # Minimapa (canto superior direito) — oculto enquanto mapa grande estiver aberto
            if not self._map_overlay.is_open:
                _fog_mm      = self.world.get_component(self.player_entity, FogOfWar)
                _player_tm_m = self.world.get_component(self.player_entity, TileMovement)
                if _fog_mm and _player_tm_m:
                    _enemy_tiles = [
                        (etm.current_tile_x, etm.current_tile_y)
                        for _, _, _, etm in self.world.get_entities_with(Enemy, Visible, TileMovement)
                    ]
                    self._minimap.render(
                        _player_tm_m.current_tile_x,
                        _player_tm_m.current_tile_y,
                        _fog_mm.explored,
                        _fog_mm.visible,
                        _enemy_tiles,
                    )
                # HUD de quests — abaixo do minimap
                self._quest_system.render_hud(self.screen)
            if PROFILE_FRAMES:
                self._prof_record("hud:combat_log", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            if self._show_inventory:
                self._draw_inventory_panel()
            if PROFILE_FRAMES:
                self._prof_record("hud:inventory", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            if self._show_talents:
                self._talent_system.render()
            if self._show_hotbar_editor:
                self._draw_hotbar_editor(events)
            if self._show_habilidades or self._hab_drag_skill:
                self._draw_habilidades_panel(events)
            if PROFILE_FRAMES:
                self._prof_record("hud:talents", _time.perf_counter() - _ts)
                _ts = _time.perf_counter()
            if self._map_title_timer > 0:
                self._draw_map_title()
            if PROFILE_FRAMES:
                self._prof_record("hud:map_title", _time.perf_counter() - _ts)

            # Loot modal por cima de tudo (inclusive inventory panel)
            self._loot_system.render(cam_x, cam_y)
            if self._loot_system.pending_tooltip:
                self._pending_tooltip = self._loot_system.pending_tooltip

            # Shop modal: por cima de tudo
            self._shop_system.render(cam_x, cam_y)
            if self._shop_system.pending_tooltip:
                self._pending_tooltip = self._shop_system.pending_tooltip

            # Quest dialog e diário: por cima de tudo
            self._quest_dialog.render()
            self._quest_journal.render()

            # Crafting (ferreiro): por cima de tudo
            self._crafting_system.render()
            if self._crafting_system.pending_tooltip:
                self._pending_tooltip = self._crafting_system.pending_tooltip

            # Trainer (treinador): por cima de tudo
            self._trainer_system.render()

            if self._pending_tooltip:
                self._flush_tooltip()
            if self._pending_skill_tooltip:
                self._flush_skill_tooltip()

            # Modal de debug (F12): por cima de tudo exceto mapa
            if self._show_debug and DEBUG_MODE:
                self._draw_debug_modal()

            # Mapa por cima de tudo
            if self._map_overlay.is_open:
                player_tm = self.world.get_component(self.player_entity, TileMovement)
                tx = player_tm.current_tile_x if player_tm else -1
                ty = player_tm.current_tile_y if player_tm else -1
                _fog_comp = self.world.get_component(self.player_entity, FogOfWar)
                self._map_overlay.render(tx, ty,
                                         explored=_fog_comp.explored if _fog_comp else None)

            # Menu de pausa (por cima de tudo)
            if self._pending_quit:
                running = False
            elif self._show_pause:
                action = self._draw_pause_menu(events)
                if action == "quit":
                    self._show_pause    = False
                    self._pause_submenu = ""
                    self._pending_quit  = True
                elif action == "resume":
                    self._show_pause    = False
                    self._pause_submenu = ""
                elif action == "open_hotbar_editor":
                    self._show_pause         = False
                    self._pause_submenu      = ""
                    self._show_hotbar_editor = True
                elif action and action.startswith("resolution:"):
                    self._apply_scale(float(action.split(":")[1]))

            # God Mode: por cima de tudo (grade + seleção + painel)
            self._god_mode.update(cam_x, cam_y, dt)
            self._god_mode.render(cam_x, cam_y)

            # Copia surface interna → janela (resolução nativa, sem escala)
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._display.blit(self.screen, (0, 0))
            pygame.display.flip()
            if PROFILE_FRAMES:
                self._prof_record("display_flip", _time.perf_counter() - _ts)
                # Detecção de spike: imprime breakdown imediato se o frame demorou >50ms
                _frame_elapsed = _time.perf_counter() - _t0
                if _frame_elapsed > 0.050:
                    _fms = _frame_elapsed * 1000
                    print(f"\n[SPIKE] {_fms:.0f}ms — breakdown do frame:")
                    _spike_rows = sorted(self._prof_frame_accum.items(), key=lambda x: -x[1])
                    for _lbl, _lt in _spike_rows:
                        if _lt > 0.002:
                            print(f"  {'>>':2} {_lbl:<38} {_lt*1000:>7.1f}ms")
                self._prof_frame_accum = {}
                self._prof_frames += 1
                if self._prof_frames >= self._prof_interval:
                    self._prof_report()

        self._autosave()
        from save_system import flush as _flush_save
        _flush_save()   # garante que o I/O de disco termine antes do processo encerrar
        pygame.quit()

    # ------------------------------------------------------------------
    # Transição de mapa (cavernas / retorno)
    # ------------------------------------------------------------------

    def _do_transition(self, trans: dict):
        target_file = trans["target_map"].replace("\\", "/")
        target_x    = trans["target_x"]
        target_y    = trans["target_y"]

        # Morte no mesmo mapa: apenas reposiciona o player, mobs continuam vivos
        if target_file == self._current_map_file:
            self._reposition_player(target_x, target_y)
            self._transition_cooldown = 1.5
            self._loot_system.open_corpse_id         = -1
            self._loot_system.pending_loot_corpse_id = -1
            self._death_handler.clear_pending()
            self._show_inventory = False
            LOG.add("Você morreu e voltou ao ponto de spawn.", (200, 80, 80))
            self._autosave()
            return

        # Remove todas as entidades exceto jogador e câmera
        keep = {self.player_entity, self.camera_entity}
        for eid in list(self.world._components.keys()):
            if eid not in keep:
                self.world.remove_entity(eid)

        # Reseta caches de tilemap
        self._tile_validation_system.tilemap_comp = None
        self._pathfinding_system.tilemap_comp     = None
        # Invalida cache de render de tiles (novo mapa = nova paleta)
        for sys in self.systems:
            if isinstance(sys, TileRenderSystem):
                sys.invalidate_cache()
                break

        # Reseta estado de combate / loot / loja
        self._loot_system.open_corpse_id         = -1
        self._loot_system.pending_loot_corpse_id = -1
        self._death_handler.clear_pending()
        self._shop_system._close()
        self._quest_dialog._pending_npc_id = -1
        if self._quest_dialog.is_open:
            self._quest_dialog._close()
        if self._quest_journal.is_open:
            self._quest_journal.close()
        self._show_inventory = False

        # Carrega novo mapa
        terrain_matrix, object_matrix, spawn_points, terrain_visual = load_map_csv(target_file)
        self._current_map_file = target_file
        self._quest_system.set_current_map(target_file)
        fog = self.world.get_component(self.player_entity, FogOfWar)
        if fog:
            fog.switch_map(target_file)
        # Contexto acústico e ambiente
        if "cave" in target_file:
            SOUNDS.set_context("cave")
            SOUNDS.play_ambient("ambient_cave")
        else:
            SOUNDS.set_context("surface")
            SOUNDS.play_ambient("ambient_surface")
        self._map_overlay.load_map(
            _merge_display_matrix(terrain_matrix, object_matrix), target_file)
        self._map_overlay.set_active_map(target_file)
        self._minimap.on_map_load()
        self.tilemap_entity = create_tilemap(self.world, terrain_matrix, object_matrix, terrain_visual)
        tilemap_comp = self.world.get_component(self.tilemap_entity, Tilemap)
        self.map_width_px  = tilemap_comp.map_width_tiles  * TILE_SIZE
        self.map_height_px = tilemap_comp.map_height_tiles * TILE_SIZE

        self._build_transition_tiles(spawn_points)
        self._load_ambient_zones(spawn_points)
        self._spawn_entities_from(spawn_points)

        # Reposiciona o jogador
        self._reposition_player(target_x, target_y)

        self._transition_cooldown = 1.5
        self._map_title_timer     = 3.0
        is_cave = "cave" in target_file
        zone_name = "Caverna" if is_cave else "Mundo Externo"
        LOG.add(f"Entrando: {zone_name}", (200, 160, 0))
        self._autosave()

    def _reposition_player(self, tile_x: int, tile_y: int):
        new_x = tile_x * TILE_SIZE + TILE_SIZE / 2
        new_y = tile_y * TILE_SIZE + TILE_SIZE / 2

        pos  = self.world.get_component(self.player_entity, Position)
        tm   = self.world.get_component(self.player_entity, TileMovement)
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        cs   = self.world.get_component(self.player_entity, CombatState)

        pos.x = pos.prev_x = new_x
        pos.y = pos.prev_y = new_y

        tm.current_tile_x = tm.target_tile_x = tile_x
        tm.current_tile_y = tm.target_tile_y = tile_y
        tm.start_pixel_x  = tm.target_pixel_x = new_x
        tm.start_pixel_y  = tm.target_pixel_y = new_y
        tm.is_moving = False
        tm.progress  = 0.0

        if auto:
            auto.active = False
            auto.path.clear()
            auto.ground_target     = None
            auto.path_recalc_timer = 0.0
        if cs:
            cs.target_entity_id = -1

    def _draw_map_title(self):
        import os
        text = os.path.splitext(os.path.basename(self._current_map_file))[0].replace("_", " ").title()
        surf = self.font_lg.render(text, True, (255, 200, 0))
        x = SCREEN_WIDTH  // 2 - surf.get_width()  // 2
        y = SCREEN_HEIGHT // 2 - surf.get_height() // 2
        # fundo semitransparente
        pad = 16
        bg = pygame.Surface((surf.get_width() + pad * 2, surf.get_height() + pad * 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        self.screen.blit(bg, (x - pad, y - pad))
        self.screen.blit(surf, (x, y))

    # ------------------------------------------------------------------
    # Debug modal (F12)
    # ------------------------------------------------------------------

    def _debug_levelup(self, n: int) -> None:
        """Sobe n níveis instantaneamente, concedendo 1 ponto de talento por nível."""
        from stats_system import process_levelups
        from components import TalentTree
        cs   = self.world.get_component(self.player_entity, CharacterStats)
        comb = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        tt   = self.world.get_component(self.player_entity, TalentTree)
        if not cs or not comb:
            return
        # Força XP suficiente para n level-ups e processa via função centralizada
        for _ in range(n):
            cs.current_xp = cs.xp_to_next_level
            process_levelups(self.world, self.player_entity, cs, comb, perm)
        LOG.add(f"[DEBUG] Nivel {cs.level} — {tt.available_points if tt else 0} pontos de talento.", (120, 200, 255))

    def _handle_debug_click(self, event) -> None:
        PW, PH = 820, 620
        px = SCREEN_WIDTH  // 2 - PW // 2
        py = SCREEN_HEIGHT // 2 - PH // 2
        mx, my = event.pos

        # Botão fechar (X) — botão esquerdo
        if event.button == 1:
            close_r = pygame.Rect(px + PW - 44, py + 8, 36, 36)
            if close_r.collidepoint(mx, my):
                self._show_debug = False
                return

        # Clique fora fecha
        if not pygame.Rect(px, py, PW, PH).collidepoint(mx, my):
            self._show_debug = False
            return

        # Troca de aba (botão esquerdo)
        if event.button == 1:
            for rect, tab_id in self._debug_tab_buttons:
                if rect.collidepoint(mx, my):
                    self._debug_tab = tab_id
                    return

            if self._debug_tab == "nivel":
                for rect, n in self._debug_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_levelup(n)
                        return

            elif self._debug_tab == "ouro":
                for rect, amount in self._debug_gold_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_add_gold(amount)
                        return

            elif self._debug_tab == "mapa":
                for rect, map_file in self._debug_map_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_open_map(map_file)
                        return

        # Aba de itens: clique direito = adicionar ao inventário
        elif event.button == 3 and self._debug_tab == "itens":
            for rect, factory in self._debug_item_buttons:
                if rect.collidepoint(mx, my):
                    self._debug_add_item(factory)
                    return

    def _debug_add_gold(self, amount: int) -> None:
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            wallet.gold += amount
            LOG.add(f"[DEBUG] +{amount}g adicionado. Total: {wallet.gold}g", (120, 200, 255))

    def _debug_add_item(self, factory) -> None:
        inv = self.world.get_component(self.player_entity, Inventory)
        if not inv:
            return
        if len(inv.items) >= inv.max_slots:
            LOG.add("[DEBUG] Mochila cheia!", (220, 100, 50))
            return
        item = factory()
        inv.items.append(item)
        LOG.add(f"[DEBUG] {item.name} adicionado a mochila.", (120, 200, 255))

    def _debug_open_map(self, map_file: str) -> None:
        """Carrega map_file no overlay e abre para o jogador clicar o destino de teleporte."""
        from map_loader import load_map_csv
        terrain_matrix, _, __, ___ = load_map_csv(map_file)
        player_tm = self.world.get_component(self.player_entity, TileMovement)
        ptx = player_tm.current_tile_x if player_tm else 0
        pty = player_tm.current_tile_y if player_tm else 0
        self._map_overlay.load_map(terrain_matrix, map_file)
        self._map_overlay.set_active_map(map_file)
        self._minimap.on_map_load()
        self._map_overlay.toggle(ptx, pty)
        self._debug_teleport_map = map_file
        self._show_debug = False

    def _get_debug_item_catalog(self) -> list:
        """Retorna lista de (name, rarity, factory) de todos os itens do jogo. Cache lazy."""
        if self._debug_item_catalog:
            return self._debug_item_catalog
        from loot_tables import _T
        from merchant_data import SHOPS
        rarity_order = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3}
        catalog = []
        for factory in _T.values():
            sample = factory()
            catalog.append((sample.name, sample.rarity, factory))
        seen = set(n for n, _, _ in catalog)
        for shop in SHOPS.values():
            for entry in shop["stock"]:
                sample = entry["factory"]()
                if sample.name not in seen:
                    seen.add(sample.name)
                    catalog.append((sample.name, sample.rarity, entry["factory"]))
        catalog.sort(key=lambda x: (rarity_order.get(x[1], 0), x[0]))
        self._debug_item_catalog = catalog
        return catalog

    def _draw_debug_modal(self) -> None:
        """Desenha o painel de debug (F12) com abas: Nivel / Itens / Ouro."""
        from components import TalentTree
        cs = self.world.get_component(self.player_entity, CharacterStats)
        tt = self.world.get_component(self.player_entity, TalentTree)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not cs:
            return

        PW, PH = 820, 620
        px = SCREEN_WIDTH  // 2 - PW // 2
        py = SCREEN_HEIGHT // 2 - PH // 2
        mx, my = pygame.mouse.get_pos()

        font_md = _font(28)
        font_sm = _font(24)
        font_lg = _font(36)

        # Fundo
        bg = pygame.Surface((PW, PH), pygame.SRCALPHA)
        bg.fill((10, 8, 5, 238))
        self.screen.blit(bg, (px, py))
        pygame.draw.rect(self.screen, (100, 80, 50), (px, py, PW, PH), 2)

        # Título
        title = font_lg.render("DEBUG  [F12]", True, (120, 200, 255))
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 10))

        # Botão fechar
        close_r = pygame.Rect(px + PW - 44, py + 8, 36, 36)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (80, 30, 30),
                         close_r, border_radius=3)
        xs = font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        pygame.draw.line(self.screen, (80, 65, 40), (px + 4, py + 52), (px + PW - 4, py + 52))

        # --- Abas ---
        TAB_DEFS = [("nivel", "Nivel"), ("itens", "Itens"), ("ouro", "Ouro"), ("mapa", "Mapa")]
        tab_w, tab_h, tab_gap = 160, 38, 10
        tabs_total_w = len(TAB_DEFS) * tab_w + (len(TAB_DEFS) - 1) * tab_gap
        tx0 = px + PW // 2 - tabs_total_w // 2
        ty0 = py + 58

        self._debug_tab_buttons = []
        for i, (tab_id, label) in enumerate(TAB_DEFS):
            tr = pygame.Rect(tx0 + i * (tab_w + tab_gap), ty0, tab_w, tab_h)
            self._debug_tab_buttons.append((tr, tab_id))
            is_active = self._debug_tab == tab_id
            tab_hov   = tr.collidepoint(mx, my)
            if is_active:
                col_bg, col_bord, col_txt = (40, 60, 110), (100, 160, 255), (200, 230, 255)
            elif tab_hov:
                col_bg, col_bord, col_txt = (35, 35, 50), (80, 100, 150), (180, 180, 220)
            else:
                col_bg, col_bord, col_txt = (20, 20, 28), (55, 50, 40), (110, 110, 120)
            pygame.draw.rect(self.screen, col_bg,   tr, border_radius=3)
            pygame.draw.rect(self.screen, col_bord, tr, 1, border_radius=3)
            lbl = font_md.render(label, True, col_txt)
            self.screen.blit(lbl, (tr.centerx - lbl.get_width() // 2,
                                   tr.centery - lbl.get_height() // 2))

        pygame.draw.line(self.screen, (80, 65, 40), (px + 4, py + 104), (px + PW - 4, py + 104))

        content_y = py + 114

        if self._debug_tab == "nivel":
            self._draw_debug_tab_nivel(px, content_y, PW, font_md, font_sm, cs, tt, mx, my)
        elif self._debug_tab == "itens":
            self._draw_debug_tab_itens(px, content_y, PW, font_md, font_sm, mx, my)
        elif self._debug_tab == "ouro":
            self._draw_debug_tab_ouro(px, content_y, PW, font_md, font_sm, wallet, mx, my)
        elif self._debug_tab == "mapa":
            self._draw_debug_tab_mapa(px, content_y, PW, font_md, font_sm, mx, my)

        # Footer
        hint = font_sm.render("ESC para fechar", True, (80, 75, 60))
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, py + PH - 28))

    def _draw_debug_tab_nivel(self, px, content_y, PW, font_md, font_sm, cs, tt, mx, my) -> None:
        points = tt.available_points if tt else 0
        for i, line in enumerate([
            f"Nivel atual:       {cs.level}",
            f"Pontos de talento: {points}",
        ]):
            surf = font_sm.render(line, True, (200, 190, 160))
            self.screen.blit(surf, (px + 20, content_y + 8 + i * 22))

        self._debug_buttons = []
        btn_labels = [("+1 nivel", 1), ("+5 niveis", 5), ("+10 niveis", 10), ("+50 niveis", 50)]
        btn_w, btn_h, gap = 170, 48, 14
        cols = 2
        bx0 = px + PW // 2 - (cols * btn_w + (cols - 1) * gap) // 2
        by0 = content_y + 70
        for i, (label, n) in enumerate(btn_labels):
            bx = bx0 + (i % cols) * (btn_w + gap)
            by = by0 + (i // cols) * (btn_h + gap)
            rect = pygame.Rect(bx, by, btn_w, btn_h)
            hov = rect.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (60, 100, 160) if hov else (30, 50, 80),
                             rect, border_radius=4)
            pygame.draw.rect(self.screen, (80, 130, 200), rect, 1, border_radius=4)
            lbl = font_sm.render(label, True, (220, 220, 255))
            self.screen.blit(lbl, (bx + btn_w // 2 - lbl.get_width() // 2,
                                   by + btn_h // 2 - lbl.get_height() // 2))
            self._debug_buttons.append((rect, n))

    def _draw_debug_tab_ouro(self, px, content_y, PW, font_md, font_sm, wallet, mx, my) -> None:
        gold = wallet.gold if wallet else 0
        gold_s = font_md.render(f"Ouro atual: {gold}g", True, (255, 215, 0))
        self.screen.blit(gold_s, (px + PW // 2 - gold_s.get_width() // 2, content_y + 10))

        self._debug_gold_buttons = []
        btn_labels = [("+10g", 10), ("+100g", 100), ("+1000g", 1000)]
        btn_w, btn_h, gap = 180, 54, 18
        total_w = len(btn_labels) * btn_w + (len(btn_labels) - 1) * gap
        bx0 = px + PW // 2 - total_w // 2
        by0 = content_y + 60
        for i, (label, amount) in enumerate(btn_labels):
            bx = bx0 + i * (btn_w + gap)
            rect = pygame.Rect(bx, by0, btn_w, btn_h)
            hov = rect.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (60, 130, 60) if hov else (30, 65, 30),
                             rect, border_radius=4)
            pygame.draw.rect(self.screen, (80, 200, 80), rect, 1, border_radius=4)
            lbl = font_sm.render(label, True, (200, 255, 200))
            self.screen.blit(lbl, (bx + btn_w // 2 - lbl.get_width() // 2,
                                   by0 + btn_h // 2 - lbl.get_height() // 2))
            self._debug_gold_buttons.append((rect, amount))

    def _draw_debug_tab_mapa(self, px, content_y, PW, font_md, font_sm, mx, my) -> None:
        import glob as _glob
        csv_files = sorted(
            f.replace("\\", "/") for f in _glob.glob("maps/*.csv")
            if not f.replace("\\", "/").endswith(("_terrain.csv", "_objects.csv"))
        )
        all_maps  = [
            (f, _DEBUG_MAP_NAMES.get(f, f.replace("maps/", "").replace(".csv", "")))
            for f in csv_files
        ]
        hint = font_sm.render("Clique no mapa para abrir e depois clique dir. para teleportar", True, (90, 80, 60))
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, content_y + 4))

        self._debug_map_buttons = []
        ROW_H, ROW_W = 52, PW - 80
        rx0 = px + 40
        ry0 = content_y + 28
        for i, (map_file, map_name) in enumerate(all_maps):
            r = pygame.Rect(rx0, ry0 + i * (ROW_H + 10), ROW_W, ROW_H)
            is_current = map_file == self._current_map_file
            hov = r.collidepoint(mx, my)
            if hov:
                col_bg, col_bord = (50, 80, 50), (100, 220, 100)
            elif is_current:
                col_bg, col_bord = (28, 45, 28), (60, 140, 60)
            else:
                col_bg, col_bord = (22, 18, 10), (55, 50, 40)
            pygame.draw.rect(self.screen, col_bg,   r, border_radius=4)
            pygame.draw.rect(self.screen, col_bord, r, 1, border_radius=4)
            col_txt = (180, 255, 180) if hov else (200, 190, 160)
            lbl = font_md.render(map_name, True, col_txt)
            self.screen.blit(lbl, (r.x + 16, r.centery - lbl.get_height() // 2 - 6))
            tag = "[mapa atual]" if is_current else map_file
            tag_s = font_sm.render(tag, True, (80, 160, 80) if is_current else (70, 65, 55))
            self.screen.blit(tag_s, (r.x + 16, r.centery + 4))
            self._debug_map_buttons.append((r, map_file))

    def _draw_debug_tab_itens(self, px, content_y, PW, font_md, font_sm, mx, my) -> None:
        _RARITY_COLORS = {
            "common":   (200, 200, 200),
            "uncommon": ( 30, 200,  30),
            "rare":     ( 80, 140, 255),
            "epic":     (180,  50, 255),
        }
        catalog = self._get_debug_item_catalog()
        inv = self.world.get_component(self.player_entity, Inventory)
        inv_full = inv and len(inv.items) >= inv.max_slots

        hint_col = (150, 80, 80) if inv_full else (90, 80, 60)
        hint_txt = "Mochila cheia!" if inv_full else "Clique direito: adicionar a mochila"
        hint = font_sm.render(hint_txt, True, hint_col)
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, content_y + 2))

        ROW_H, MAX_ROWS = 36, 9
        LIST_W = PW - 28
        list_x = px + 14
        list_y = content_y + 24

        max_scroll = max(0, len(catalog) - MAX_ROWS)
        self._debug_item_scroll = min(self._debug_item_scroll, max_scroll)

        self._debug_item_buttons = []
        for i, (name, rarity, factory) in enumerate(catalog):
            vis_i = i - self._debug_item_scroll
            if not (0 <= vis_i < MAX_ROWS):
                continue
            ry = list_y + vis_i * ROW_H
            r  = pygame.Rect(list_x, ry, LIST_W - 12, ROW_H - 2)
            hov     = r.collidepoint(mx, my)
            rar_col = _RARITY_COLORS.get(rarity, (100, 100, 100))
            pygame.draw.rect(self.screen, (40, 35, 20) if hov else (22, 18, 10),
                             r, border_radius=3)
            pygame.draw.rect(self.screen, rar_col if hov else (50, 40, 25),
                             r, 1, border_radius=3)
            pygame.draw.circle(self.screen, rar_col, (r.x + 12, r.centery), 5)
            name_s = font_sm.render(name, True, rar_col)
            self.screen.blit(name_s, (r.x + 26, r.centery - name_s.get_height() // 2))
            rar_s = font_sm.render(rarity.capitalize(), True, rar_col)
            self.screen.blit(rar_s, (r.right - rar_s.get_width() - 6,
                                     r.centery - rar_s.get_height() // 2))
            self._debug_item_buttons.append((r, factory))

        # Scrollbar
        if len(catalog) > MAX_ROWS:
            sb_h = MAX_ROWS * ROW_H
            sb_x = list_x + LIST_W - 10
            th   = max(20, sb_h * MAX_ROWS // len(catalog))
            ty   = list_y + (sb_h - th) * self._debug_item_scroll // max(1, max_scroll)
            pygame.draw.rect(self.screen, (35, 30, 18), (sb_x, list_y, 5, sb_h), border_radius=2)
            pygame.draw.rect(self.screen, (120, 100, 60), (sb_x, ty, 5, th), border_radius=2)

    # ------------------------------------------------------------------
    # Menu de pausa
    # ------------------------------------------------------------------

    # ── helpers de estilo reutilizados pelos submenus ──────────────────────
    _MM_BG_COL    = (14, 10, 6)
    _MM_BORDER    = (90, 72, 44)
    _MM_TITLE_COL = (220, 190, 110)
    _MM_BTN_HOV   = (80, 62, 28)
    _MM_BTN_NRM   = (38, 30, 16)
    _MM_BTN_TXT   = (230, 210, 160)
    _MM_BORDER_HOV= (200, 160, 60)

    def _mm_overlay(self):
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 150))
        self.screen.blit(ov, (0, 0))

    def _mm_panel(self, pw, ph):
        px = SCREEN_WIDTH  // 2 - pw // 2
        py = SCREEN_HEIGHT // 2 - ph // 2
        bg = pygame.Surface((pw, ph), pygame.SRCALPHA)
        bg.fill((*self._MM_BG_COL, 240))
        self.screen.blit(bg, (px, py))
        pygame.draw.rect(self.screen, self._MM_BORDER,
                         (px, py, pw, ph), 2, border_radius=8)
        return px, py

    def _mm_button(self, rect, label, hov):
        bg_c = self._MM_BTN_HOV if hov else self._MM_BTN_NRM
        bdr  = self._MM_BORDER_HOV if hov else self._MM_BORDER
        pygame.draw.rect(self.screen, bg_c, rect, border_radius=5)
        pygame.draw.rect(self.screen, bdr,  rect, 1, border_radius=5)
        lbl = self.font_md.render(label, True, self._MM_BTN_TXT)
        self.screen.blit(lbl, lbl.get_rect(center=rect.center))

    # ── Menu principal ─────────────────────────────────────────────────────
    def _draw_pause_menu(self, events: list) -> "str | None":
        """Dispatcher: redireciona para o submenu activo ou desenha o menu principal."""
        if self._pause_submenu == "resolution":
            return self._draw_resolution_submenu(events)
        if self._pause_submenu == "sound":
            return self._draw_sound_submenu(events)
        if self._pause_submenu == "interface":
            return self._draw_interface_submenu(events)
        if self._pause_submenu == "quit_confirm":
            return self._draw_quit_confirm(events)
        return self._draw_main_menu(events)

    def _draw_quit_confirm(self, events: list) -> "str | None":
        PW, PH  = 320, 150
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH)
        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        msg  = self.font_md.render("Tem certeza que deseja sair?", True, (210, 190, 150))
        self.screen.blit(msg, msg.get_rect(center=(px + PW // 2, py + 40)))

        btn_w, btn_h = 100, 36
        gap   = 20
        total = btn_w * 2 + gap
        bx    = px + PW // 2 - total // 2
        by    = py + PH - btn_h - 20

        sim_rect = pygame.Rect(bx, by, btn_w, btn_h)
        nao_rect = pygame.Rect(bx + btn_w + gap, by, btn_w, btn_h)

        # "Sim" em vermelho
        hov_sim = sim_rect.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (100, 30, 20) if hov_sim else (70, 20, 15),
                         sim_rect, border_radius=5)
        pygame.draw.rect(self.screen, (200, 60, 40), sim_rect, 1, border_radius=5)
        lbl = self.font_md.render("Sim", True, (255, 140, 120))
        self.screen.blit(lbl, lbl.get_rect(center=sim_rect.center))
        if hov_sim and clicked:
            return "quit"

        # "Não" normal
        self._mm_button(nao_rect, "Nao", nao_rect.collidepoint(mx, my))
        if nao_rect.collidepoint(mx, my) and clicked:
            self._pause_submenu = ""
        return None

    def _draw_main_menu(self, events: list) -> "str | None":
        _BTNS = [
            ("Resume",              "resume"),
            ("Resolution",          "submenu:resolution"),
            ("Sound",               "submenu:sound"),
            ("Interface",           "submenu:interface"),
            ("Atalhos do teclado",  "open_hotbar_editor"),
            ("Quit",                "submenu:quit_confirm"),
        ]
        PW, PH = 260, 60 + len(_BTNS) * 50 + 10
        self._mm_overlay()
        px, py = self._mm_panel(PW, PH)

        title = self.font_md.render("Main Menu", True, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 16))

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        btn_w, btn_h = 200, 38
        bx = px + PW // 2 - btn_w // 2
        for i, (label, action) in enumerate(_BTNS):
            rect = pygame.Rect(bx, py + 56 + i * (btn_h + 10), btn_w, btn_h)
            hov  = rect.collidepoint(mx, my)
            self._mm_button(rect, label, hov)
            if hov and clicked:
                if action.startswith("submenu:"):
                    self._pause_submenu = action.split(":")[1]
                    return None
                return action
        return None

    # ── Submenu Resolution ─────────────────────────────────────────────────
    def _draw_resolution_submenu(self, events: list) -> "str | None":
        from settings_screen import SCALE_OPTIONS
        PW, PH = 360, 220
        self._mm_overlay()
        px, py = self._mm_panel(PW, PH)

        title = self.font_md.render("Resolution", True, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 14))

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        opt_w, opt_h = 290, 36
        ox = px + PW // 2 - opt_w // 2
        for i, (label, val) in enumerate(SCALE_OPTIONS):
            oy   = py + 52 + i * (opt_h + 8)
            rect = pygame.Rect(ox, oy, opt_w, opt_h)
            hov  = rect.collidepoint(mx, my)
            is_sel = abs(val - self._scale) < 0.01
            bg_c = (55, 44, 22) if is_sel else (self._MM_BTN_HOV if hov else self._MM_BTN_NRM)
            bdr  = (200, 160, 60) if (is_sel or hov) else self._MM_BORDER
            bdr_w = 2 if is_sel else 1
            pygame.draw.rect(self.screen, bg_c, rect, border_radius=4)
            pygame.draw.rect(self.screen, bdr,  rect, bdr_w, border_radius=4)
            lbl = self.font_md.render(label, True, (200, 160, 60) if is_sel else self._MM_BTN_TXT)
            self.screen.blit(lbl, lbl.get_rect(center=rect.center))
            if hov and clicked and not is_sel:
                return f"resolution:{val}"

        # Botão Voltar
        back = pygame.Rect(px + PW // 2 - 80, py + PH - 44, 160, 34)
        hov  = back.collidepoint(mx, my)
        self._mm_button(back, "Back", hov)
        if hov and clicked:
            self._pause_submenu = ""
        return None

    # ── Submenu Sound ──────────────────────────────────────────────────────
    # ── Submenu Interface (UI Scale) ───────────────────────────────────────
    def _draw_interface_submenu(self, events: list) -> "str | None":
        _STEPS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        PW, PH  = 340, 200
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH)

        title = self.font_md.render("Interface", True, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 14))

        # Label
        lbl = self.font_sm.render("Escala da UI", True, (190, 175, 130))
        self.screen.blit(lbl, (px + 24, py + 62))

        # Valor atual
        cur_s = self.font_sm.render(f"{self._ui_scale:.2f}×", True, (230, 210, 120))
        self.screen.blit(cur_s, (px + PW - cur_s.get_width() - 24, py + 62))

        # Barra / botões −  +
        mx, my = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        btn_y = py + 100
        btn_w, btn_h = 44, 32
        gap = 12

        minus_r = pygame.Rect(px + 24, btn_y, btn_w, btn_h)
        plus_r  = pygame.Rect(px + PW - 24 - btn_w, btn_y, btn_w, btn_h)

        for r, sym in [(minus_r, "−"), (plus_r, "+")]:
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (55, 45, 25) if hov else (38, 30, 14), r, border_radius=5)
            pygame.draw.rect(self.screen, (140, 115, 60), r, 1, border_radius=5)
            ss = self.font_md.render(sym, True, (230, 210, 120))
            self.screen.blit(ss, ss.get_rect(center=r.center))

        # Pontinhos de passo
        total_pip_w = len(_STEPS) * 18
        pip_x0 = px + PW // 2 - total_pip_w // 2
        for i, step in enumerate(_STEPS):
            active = abs(step - self._ui_scale) < 0.01
            col = (220, 190, 80) if active else (80, 65, 35)
            pygame.draw.circle(self.screen, col, (pip_x0 + i * 18, btn_y + btn_h // 2), 5)

        if clicked:
            cur_idx = min(range(len(_STEPS)), key=lambda i: abs(_STEPS[i] - self._ui_scale))
            if minus_r.collidepoint(mx, my) and cur_idx > 0:
                self._set_ui_scale(_STEPS[cur_idx - 1])
            elif plus_r.collidepoint(mx, my) and cur_idx < len(_STEPS) - 1:
                self._set_ui_scale(_STEPS[cur_idx + 1])

        # Botão Voltar
        back_r = pygame.Rect(px + PW // 2 - 70, py + PH - 48, 140, 34)
        hov_b  = back_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (55, 44, 24) if hov_b else (38, 30, 14), back_r, border_radius=6)
        pygame.draw.rect(self.screen, (110, 90, 50), back_r, 1, border_radius=6)
        bs = self.font_sm.render("← Voltar", True, (210, 192, 135))
        self.screen.blit(bs, bs.get_rect(center=back_r.center))
        if clicked and hov_b:
            self._pause_submenu = ""
        return None

    def _draw_sound_submenu(self, events: list) -> "str | None":
        PW, PH  = 400, 230
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH)

        title = self.font_md.render("Sound", True, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 14))

        mx, my   = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)
        if released:
            if self._sound_drag:
                self._save_config()
            self._sound_drag = ""

        # Layout: [ label | ████ slider ████ | toggle ]
        SLX     = px + 100          # slider start x
        SL_W    = 190               # slider track width
        SL_H    = 10                # slider track height
        TOG_X   = px + 305          # toggle start x
        TOG_W, TOG_H = 64, 26

        rows = [
            ("Music",   "music",  SOUNDS.music_volume,  SOUNDS.music_enabled),
            ("Effects", "sfx",    SOUNDS.sfx_volume,    SOUNDS.sfx_enabled),
        ]

        for i, (label, key, vol, enabled) in enumerate(rows):
            row_y = py + 70 + i * 60

            # Label
            lbl = self.font_sm.render(label, True, (200, 185, 155))
            self.screen.blit(lbl, (px + 16, row_y + 2))

            # Slider track
            track = pygame.Rect(SLX, row_y, SL_W, SL_H)
            pygame.draw.rect(self.screen, (50, 42, 28), track, border_radius=5)
            fill_w = int(SL_W * vol)
            if fill_w > 0:
                fill_col = (200, 160, 60) if enabled else (100, 90, 60)
                pygame.draw.rect(self.screen, fill_col,
                                 pygame.Rect(SLX, row_y, fill_w, SL_H), border_radius=5)
            pygame.draw.rect(self.screen, (90, 72, 44), track, 1, border_radius=5)

            # Slider handle
            hx = SLX + int(SL_W * vol)
            pygame.draw.circle(self.screen, (220, 190, 110), (hx, row_y + SL_H // 2), 7)

            # Click / drag on slider
            handle_area = pygame.Rect(SLX - 8, row_y - 8, SL_W + 16, SL_H + 16)
            if clicked and handle_area.collidepoint(mx, my):
                self._sound_drag = key
            if self._sound_drag == key:
                ratio = max(0.0, min(1.0, (mx - SLX) / SL_W))
                if key == "music":
                    SOUNDS.music_volume = ratio
                    SOUNDS.apply_music_settings()
                else:
                    SOUNDS.sfx_volume = ratio

            # Toggle
            tog_rect = pygame.Rect(TOG_X, row_y - 8, TOG_W, TOG_H)
            tog_on_c = (50, 160, 80) if enabled else (60, 50, 40)
            tog_bd_c = (80, 200, 100) if enabled else self._MM_BORDER
            pygame.draw.rect(self.screen, tog_on_c,  tog_rect, border_radius=13)
            pygame.draw.rect(self.screen, tog_bd_c,  tog_rect, 1, border_radius=13)
            tog_txt = self.font_sm.render("ON" if enabled else "OFF", True,
                                          (200, 255, 200) if enabled else (160, 140, 120))
            self.screen.blit(tog_txt, tog_txt.get_rect(center=tog_rect.center))
            if clicked and tog_rect.collidepoint(mx, my):
                if key == "music":
                    SOUNDS.music_enabled = not SOUNDS.music_enabled
                    SOUNDS.apply_music_settings()
                else:
                    SOUNDS.sfx_enabled = not SOUNDS.sfx_enabled
                self._save_config()

            # Percent label
            pct = self.font_sm.render(f"{int(vol * 100)}%", True, (160, 150, 120))
            self.screen.blit(pct, (SLX + SL_W + 4, row_y - 1))

        # Botão Voltar
        back = pygame.Rect(px + PW // 2 - 80, py + PH - 44, 160, 34)
        hov  = back.collidepoint(mx, my)
        self._mm_button(back, "Back", hov)
        if hov and clicked:
            self._pause_submenu = ""
            self._sound_drag    = ""
        return None

    # ------------------------------------------------------------------
    # Editor da hotbar (K)
    # ------------------------------------------------------------------

    _HBE_SZ  = 52
    _HBE_GAP = 10

    def _close_hotbar_editor(self) -> None:
        self._show_hotbar_editor    = False
        self._hbe_drag_from         = None
        self._hbe_rebind_slot       = None
        self._hbe_cons_drag_from    = None
        self._hbe_cons_rebind_slot  = None
        self._hbe_tab               = 0
        self._mkb_rebind            = None
        self._hbe_skill_scroll      = 0
        self._hbe_expand_slots      = False
        self._save_config()

    def _draw_hotbar_editor(self, events: list) -> None:
        """Painel 'Atalhos do teclado' — tabela de rebind + Salvar / Fechar."""
        from skill_config import SKILL_CATALOG, NUM_SLOTS
        from components import ConsumableBar as _CB

        ps   = self.world.get_component(self.player_entity, PlayerSkills)
        cbar = self.world.get_component(self.player_entity, _CB)
        if not ps:
            return

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        # ── Captura de tecla para rebind ──────────────────────────────────
        for e in events:
            if e.type != pygame.KEYDOWN:
                continue
            if self._mkb_rebind is not None:
                if e.key == pygame.K_ESCAPE:
                    self._mkb_rebind = None
                else:
                    action, idx = self._mkb_rebind.split(":", 1)
                    if action == "menu":
                        self._menu_keys[idx] = e.key
                    elif action == "slot":
                        ps.keybinds[int(idx)] = e.key
                    elif action == "cons" and cbar:
                        cbar.keybinds[int(idx)] = e.key
                    self._mkb_rebind = None
            elif e.key == pygame.K_ESCAPE:
                self._close_hotbar_editor()
                return

        # ── Geometria ─────────────────────────────────────────────────────
        PW  = 560
        ROW_H  = 38
        KEY_W  = 90
        KEY_H  = 28
        BTN_W  = 110
        BTN_H  = 34

        # Conteúdo: 4 menu rows + divider + 10 slot rows + divider + 2 cons rows + buttons
        n_rows  = 4 + NUM_SLOTS + _CB.NUM_SLOTS
        PH      = 60 + 22 + n_rows * ROW_H + 20 + BTN_H + 20
        PH      = max(PH, 400)
        ppx     = SCREEN_WIDTH  // 2 - PW // 2
        ppy     = SCREEN_HEIGHT // 2 - PH // 2

        COL_NAME = ppx + 20
        COL_KEY  = ppx + PW - KEY_W - 20

        # Overlay
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 170))
        self.screen.blit(ov, (0, 0))

        # Painel
        pygame.draw.rect(self.screen, (28, 22, 12), (ppx, ppy, PW, PH), border_radius=8)
        pygame.draw.rect(self.screen, (90, 72, 44), (ppx, ppy, PW, PH), 2, border_radius=8)

        # Título
        title_s = self.font_md.render("Atalhos do teclado", True, (220, 190, 110))
        self.screen.blit(title_s, (ppx + PW // 2 - title_s.get_width() // 2, ppy + 12))

        # ── Cabeçalho de colunas ──────────────────────────────────────────
        cy = ppy + 42
        self.screen.blit(self.font_sm.render("Ação", True, (150, 135, 85)),
                         (COL_NAME, cy))
        self.screen.blit(self.font_sm.render("Tecla", True, (150, 135, 85)),
                         (COL_KEY + KEY_W // 2 - 22, cy))
        cy += 20
        pygame.draw.line(self.screen, (72, 58, 32), (ppx + 12, cy), (ppx + PW - 12, cy))
        cy += 6

        def draw_section(label, color=(185, 158, 80)):
            nonlocal cy
            s = self.font_sm.render(label, True, color)
            self.screen.blit(s, (COL_NAME, cy))
            cy += 20
            pygame.draw.line(self.screen, (60, 48, 28),
                             (ppx + 12, cy), (ppx + PW - 12, cy))
            cy += 4

        def draw_row(row_label, key_code, key_id):
            nonlocal cy
            alt = ((cy - ppy) // ROW_H) % 2 == 1
            if alt:
                pygame.draw.rect(self.screen, (34, 28, 16),
                                 (ppx + 10, cy, PW - 20, ROW_H - 2), border_radius=2)
            name_s = self.font_sm.render(row_label, True, (205, 192, 150))
            self.screen.blit(name_s, (COL_NAME, cy + (ROW_H - name_s.get_height()) // 2))

            waiting  = (self._mkb_rebind == key_id)
            key_name = pygame.key.name(key_code).upper() if key_code else "—"
            kr       = pygame.Rect(COL_KEY, cy + (ROW_H - KEY_H) // 2, KEY_W, KEY_H)
            hov      = kr.collidepoint(mx, my)

            if waiting:
                bg, bd, kt, kc = (72,56,18), (225,185,62), "...", (255,225,82)
            elif hov:
                bg, bd, kt, kc = (52,44,22), (165,135,62), key_name, (240,215,135)
            else:
                bg, bd, kt, kc = (38,30,14), (82,67,40), key_name, (175,155,92)

            pygame.draw.rect(self.screen, bg, kr, border_radius=4)
            pygame.draw.rect(self.screen, bd, kr, 1, border_radius=4)
            ks = self.font_sm.render(kt, True, kc)
            self.screen.blit(ks, ks.get_rect(center=kr.center))
            if clicked and hov and not waiting:
                self._mkb_rebind = key_id
            cy += ROW_H

        # ── Menus ─────────────────────────────────────────────────────────
        draw_section("Menus")
        for label, mid in [("Inventário", "inventario"), ("Talentos", "talentos"),
                            ("Mapa", "mapa"), ("Diário de Quests", "diario")]:
            draw_row(label, self._menu_keys.get(mid, 0), f"menu:{mid}")

        # ── Barra de Habilidades ──────────────────────────────────────────
        draw_section("Barra de Habilidades")
        for i in range(NUM_SLOTS):
            key_code = ps.keybinds[i] if i < len(ps.keybinds) else 0
            draw_row(f"Slot {i + 1}", key_code, f"slot:{i}")

        # ── Consumíveis ───────────────────────────────────────────────────
        draw_section("Barra de Consumíveis")
        for i in range(_CB.NUM_SLOTS):
            key_code = cbar.keybinds[i] if cbar and i < len(cbar.keybinds) else 0
            draw_row(f"Slot {NUM_SLOTS + i + 1}", key_code, f"cons:{i}")

        # ── Botões Salvar / Fechar ────────────────────────────────────────
        btn_y   = ppy + PH - BTN_H - 14
        btn_gap = 16
        total_btns_w = 2 * BTN_W + btn_gap
        btn_x0  = ppx + PW // 2 - total_btns_w // 2

        for bi, (blabel, bcolor, bhover) in enumerate([
            ("Salvar",  (38, 72, 38),  (55, 100, 55)),
            ("Fechar",  (60, 30, 20),  (90, 45, 30)),
        ]):
            br   = pygame.Rect(btn_x0 + bi * (BTN_W + btn_gap), btn_y, BTN_W, BTN_H)
            hov  = br.collidepoint(mx, my)
            pygame.draw.rect(self.screen, bhover if hov else bcolor, br, border_radius=6)
            pygame.draw.rect(self.screen, (120, 100, 55), br, 1, border_radius=6)
            bs   = self.font_sm.render(blabel, True, (220, 205, 150))
            self.screen.blit(bs, bs.get_rect(center=br.center))
            if clicked and hov:
                self._save_config()
                if blabel == "Fechar":
                    self._close_hotbar_editor()
                return

        # Dica
        if self._mkb_rebind:
            hint = self.font_xs.render(
                "Pressione a nova tecla  |  ESC para cancelar", True, (200, 180, 80))
        else:
            hint = self.font_xs.render(
                "Clique na tecla para rebindear  |  ESC para fechar", True, (90, 82, 56))
        self.screen.blit(hint, hint.get_rect(centerx=ppx + PW // 2, y=btn_y - 18))

    # ── Painel de Habilidades (H) ─────────────────────────────────────────────

    def _draw_habilidades_panel(self, events: list) -> None:
        """Modal central de habilidades — lista scrollável com descrição completa + drag para hotbar."""
        from skill_config import SKILL_CATALOG, NUM_SLOTS
        from components import TalentTree as _TT

        ps = self.world.get_component(self.player_entity, PlayerSkills)
        if not ps:
            return

        mx, my   = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)

        for e in events:
            if e.type == pygame.MOUSEWHEEL:
                self._hab_scroll = max(0, self._hab_scroll - e.y)

        # Coleta skills aprendidas
        avail: list[str] = [sid for sid in SKILL_CATALOG if sid in ps.learned_skill_ids]
        for s in ps.skills:
            if s and getattr(s, "talent_id", None) and s.skill_id not in avail:
                avail.append(s.skill_id)
        _tt = self.world.get_component(self.player_entity, _TT)
        if _tt:
            from talent_data import TALENTS as _TAL
            for tid, pts in _tt.allocated.items():
                t = _TAL.get(tid)
                if not t or not t.get("unlocks_skill"):
                    continue
                if pts >= t.get("unlock_at", t["max_points"]) and t["unlocks_skill"] not in avail:
                    avail.append(t["unlocks_skill"])

        # ── Geometria — modal centralizado ────────────────────────────────
        PW      = 720
        PH      = 560
        ppx     = SCREEN_WIDTH  // 2 - PW // 2
        ppy     = SCREEN_HEIGHT // 2 - PH // 2
        ICON_SZ = 48
        ROW_H   = 80          # altura de cada linha (ícone + nome + descrição completa)
        LIST_X  = ppx + 12
        LIST_W  = PW - 24
        CONTENT_Y = ppy + 44  # abaixo do header
        FOOTER_H  = 28

        max_vis    = max(1, (PH - (CONTENT_Y - ppy) - FOOTER_H - 10) // ROW_H)
        max_scroll = max(0, len(avail) - max_vis)
        self._hab_scroll = min(self._hab_scroll, max_scroll)

        if self._show_habilidades:
            # Overlay escurecido
            ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 160))
            self.screen.blit(ov, (0, 0))

            # Fundo do modal
            pygame.draw.rect(self.screen, (26, 20, 10), (ppx, ppy, PW, PH), border_radius=8)
            pygame.draw.rect(self.screen, (90, 72, 44), (ppx, ppy, PW, PH), 2, border_radius=8)

            # Título
            ts = self.font_md.render("Habilidades", True, (225, 195, 110))
            self.screen.blit(ts, (ppx + PW // 2 - ts.get_width() // 2, ppy + 10))

            # Botão [X]
            cr = pygame.Rect(ppx + PW - 30, ppy + 8, 24, 24)
            pygame.draw.rect(self.screen, (80, 40, 30) if cr.collidepoint(mx, my) else (50, 30, 20),
                             cr, border_radius=4)
            pygame.draw.rect(self.screen, (180, 80, 60), cr, 1, border_radius=4)
            xs = self.font_md.render("X", True, (220, 120, 100))
            self.screen.blit(xs, xs.get_rect(center=cr.center))
            if clicked and cr.collidepoint(mx, my):
                self._show_habilidades = False
                return

            pygame.draw.line(self.screen, (70, 56, 32),
                             (ppx + 12, ppy + 38), (ppx + PW - 12, ppy + 38))

            # ── Lista de skills ──────────────────────────────────────────
            vis = avail[self._hab_scroll: self._hab_scroll + max_vis]
            for idx, sid in enumerate(vis):
                ry    = CONTENT_Y + idx * ROW_H
                r     = pygame.Rect(LIST_X, ry, LIST_W, ROW_H - 4)
                is_src = (self._hab_drag_skill == sid)
                hov    = r.collidepoint(mx, my) and not self._hab_drag_skill
                alt    = idx % 2 == 1

                bg = (16, 12, 6) if is_src else ((46, 38, 22) if hov else (32, 26, 14) if alt else (26, 20, 10))
                pygame.draw.rect(self.screen, bg, r, border_radius=5)
                pygame.draw.rect(self.screen, (120, 98, 52) if hov else (62, 50, 30),
                                 r, 1, border_radius=5)

                # Ícone
                ic_key = ICONS.skill_key_by_name(f"skill_{sid}") or ICONS.skill_key(idx)
                ic     = ICONS.get(ic_key, ICON_SZ)
                if ic:
                    self.screen.blit(ic, (LIST_X + 8, ry + (ROW_H - 4 - ICON_SZ) // 2))

                entry    = SKILL_CATALOG.get(sid)
                name_txt = entry["name"] if isinstance(entry, dict) else sid
                desc_txt = entry.get("desc", "")   if isinstance(entry, dict) else ""
                cd       = entry.get("cooldown", 0) if isinstance(entry, dict) else 0
                cast_t   = entry.get("cast_time", 0) if isinstance(entry, dict) else 0

                tx = LIST_X + ICON_SZ + 20

                # Nome
                self.screen.blit(self.font_sm.render(name_txt, True, (230, 210, 148)),
                                 (tx, ry + 6))

                # Descrição completa — quebrada em duas linhas se necessário
                max_chars = (LIST_W - ICON_SZ - 28) // 7  # aprox chars por linha a font_xs
                if len(desc_txt) > max_chars:
                    cut = desc_txt.rfind(" ", 0, max_chars) or max_chars
                    line1, line2 = desc_txt[:cut], desc_txt[cut:].strip()
                else:
                    line1, line2 = desc_txt, ""
                self.screen.blit(self.font_xs.render(line1, True, (155, 140, 95)),
                                 (tx, ry + 28))
                if line2:
                    self.screen.blit(self.font_xs.render(line2, True, (155, 140, 95)),
                                     (tx, ry + 44))

                # Metadados (CD / cast) — canto direito da linha
                meta_parts = []
                if cd:
                    meta_parts.append(f"CD {cd:.0f}s")
                if cast_t:
                    meta_parts.append(f"Cast {cast_t:.1f}s")
                if meta_parts:
                    meta_s = self.font_xs.render("  ·  ".join(meta_parts), True, (110, 100, 65))
                    self.screen.blit(meta_s, (LIST_X + LIST_W - meta_s.get_width() - 10,
                                              ry + ROW_H - meta_s.get_height() - 8))

                if clicked and r.collidepoint(mx, my):
                    self._hab_drag_skill = sid

            # Barra de scroll
            if len(avail) > max_vis:
                list_h = max_vis * ROW_H
                pct    = self._hab_scroll / max(1, max_scroll)
                bar_h  = max(20, list_h * max_vis // max(1, len(avail)))
                bar_y  = CONTENT_Y + int((list_h - bar_h) * pct)
                bar_x  = ppx + PW - 8
                pygame.draw.rect(self.screen, (44, 35, 18), (bar_x, CONTENT_Y, 5, list_h), border_radius=2)
                pygame.draw.rect(self.screen, (125, 100, 55), (bar_x, bar_y, 5, bar_h), border_radius=2)

            # Dica de rodapé
            hint = self.font_xs.render(
                "Clique e arraste uma habilidade para um slot da hotbar  |  H ou [X] para fechar",
                True, (90, 82, 55))
            self.screen.blit(hint, hint.get_rect(centerx=ppx + PW // 2, y=ppy + PH - 20))

        # ── Ghost de drag ─────────────────────────────────────────────────
        if self._hab_drag_skill:
            GSZ   = 48
            ghost = pygame.Surface((GSZ, GSZ), pygame.SRCALPHA)
            ghost.fill((30, 24, 12, 180))
            ic_k = ICONS.skill_key_by_name(f"skill_{self._hab_drag_skill}") or ICONS.skill_key(0)
            ic   = ICONS.get(ic_k, GSZ - 4)
            if ic:
                ghost.blit(ic, (2, 2))
            self.screen.blit(ghost, ghost.get_rect(center=(mx, my)))

        # ── Drop sobre a hotbar ────────────────────────────────────────────
        if released and self._hab_drag_skill:
            sid     = self._hab_drag_skill
            self._hab_drag_skill = None
            # Calcula posições de TODOS os 10 slots (incluindo vazios) para detecção
            total_w = NUM_SLOTS * self._HB_W + (NUM_SLOTS - 1) * self._HB_PAD
            x0      = SCREEN_WIDTH // 2 - total_w // 2
            y0      = SCREEN_HEIGHT - self._HB_H - 10
            for i in range(NUM_SLOTS):
                sx = x0 + i * (self._HB_W + self._HB_PAD)
                if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                    # Verifica se a skill já está em outro slot (swap)
                    ex = next((k for k in range(NUM_SLOTS)
                               if ps.skills[k] and ps.skills[k].skill_id == sid), None)
                    if ex is not None and ex != i:
                        ps.skills[ex], ps.skills[i] = ps.skills[i], ps.skills[ex]
                    elif ex is None:
                        new_s = type(ps)._make_skill(sid, SKILL_CATALOG)
                        if new_s is None:
                            new_s = next((s for s in ps.skills if s and s.skill_id == sid), None)
                        if new_s:
                            ps.skills[i] = new_s
                    self._save_config()
                    break

    # ── Modo Online ───────────────────────────────────────────────────────────

    def _connect_online(self) -> None:
        """Inicia conexão com o servidor e faz login."""
        import config as _cfg
        from client.network import NetworkClient
        data = _cfg.load()
        host = data.get("server_host", "localhost")
        port = int(data.get("server_port", 8765))
        self._net = NetworkClient(host=host, port=port)
        self._net.connect()
        # Login após breve delay para conexão estabelecer
        import threading
        threading.Timer(0.5, self._do_login).start()

    def _do_login(self) -> None:
        if self._net and self._net.connected:
            self._net.login(self._net_user, self._net_pass)
        else:
            # Ainda não conectou — tenta de novo em 1s
            import threading
            threading.Timer(1.0, self._do_login).start()

    def _process_network(self) -> None:
        """
        Processa todas as mensagens recebidas do servidor neste frame.
        Chamado uma vez por frame no game loop, antes dos sistemas.
        """
        if not self._net:
            return
        for msg_type, payload, seq, ts in self._net.poll():
            self._handle_net_message(msg_type, payload)

    def _handle_net_message(self, msg_type, payload: dict) -> None:
        from shared.messages import MsgType
        from components import TileMovement

        if msg_type == MsgType.LOGIN_OK:
            self._my_eid = payload.get("eid", -1)
            char = payload.get("char", {})
            tx   = int(char.get("tile_x", 10))
            ty   = int(char.get("tile_y", 10))
            # Sincroniza posição local com o servidor — evita conflito com save offline
            from components import Position
            from shared.constants import TILE_SIZE as _TS
            tm = self.world.get_component(self.player_entity, TileMovement)
            if tm:
                tm.current_tile_x = tx;  tm.current_tile_y = ty
                tm.target_tile_x  = tx;  tm.target_tile_y  = ty
            pos = self.world.get_component(self.player_entity, Position)
            if pos:
                pos.x = tx * _TS + _TS // 2;  pos.y = ty * _TS + _TS // 2
                pos.prev_x = pos.x;            pos.prev_y = pos.y
            self._net_last_tx = tx
            self._net_last_ty = ty
            print(f"[Client] login ok  eid={self._my_eid}  "
                  f"user={char.get('name','?')}  tile=({tx},{ty})")

        elif msg_type == MsgType.LOGIN_ERROR:
            print(f"[Client] login erro: {payload.get('reason')}")

        elif msg_type == MsgType.WORLD_STATE:
            for ent in payload.get("entities", []):
                eid  = ent.get("eid", -1)
                kind = ent.get("kind", "player")
                if eid == -1 or eid == self._my_eid:
                    continue
                if kind == "enemy":
                    self._spawn_remote_mob(eid, ent)
                else:
                    self._spawn_remote_player_entity(eid, {
                        "tx": ent.get("tx", 0), "ty": ent.get("ty", 0),
                        "name":     ent.get("name", "?"),
                        "class_id": ent.get("class_id", "guerreiro"),
                        "hp":       ent.get("hp", 100),
                        "hp_max":   ent.get("hp_max", 100),
                    })

        elif msg_type == MsgType.ENTITY_SPAWN:
            eid  = payload.get("eid", -1)
            kind = payload.get("kind", "player")
            if eid == -1 or eid == self._my_eid:
                pass
            elif kind == "enemy":
                self._spawn_remote_mob(eid, payload)
            else:
                self._spawn_remote_player_entity(eid, {
                    "tx": payload.get("tx", 0), "ty": payload.get("ty", 0),
                    "name":     payload.get("name", "?"),
                    "class_id": payload.get("class_id", "guerreiro"),
                    "hp":       payload.get("hp", 100),
                    "hp_max":   payload.get("hp_max", 100),
                })

        elif msg_type == MsgType.ENTITY_DESPAWN:
            eid = payload.get("eid", -1)
            self._remove_remote_player_entity(eid)
            # Remove mob do ECS local se era um mob do servidor
            local_eid = self._remote_mobs.pop(eid, None)
            self._mob_hp.pop(eid, None)
            if local_eid is not None:
                self._remote_mobs_reverse.pop(local_eid, None)
                try:
                    self.world.remove_entity(local_eid)
                except Exception:
                    pass

        elif msg_type == MsgType.ENTITY_MOVE:
            eid = payload.get("eid", -1)
            if eid == self._my_eid:
                # Servidor corrigiu nossa posição — aplica
                real_tx = payload.get("tx", 0)
                real_ty = payload.get("ty", 0)
                player_tm = self.world.get_component(self.player_entity, TileMovement)
                if player_tm:
                    # Só corrige se diferente (evita jitter)
                    if (player_tm.current_tile_x != real_tx or
                            player_tm.current_tile_y != real_ty):
                        player_tm.current_tile_x = real_tx
                        player_tm.current_tile_y = real_ty
                        player_tm.target_tile_x  = real_tx
                        player_tm.target_tile_y  = real_ty
            elif eid in self._remote_players:
                self._apply_remote_move(eid, payload.get("tx", 0), payload.get("ty", 0))

        elif msg_type == MsgType.AOI_UPDATE:
            for m in payload.get("moved", []):
                eid = m.get("eid", -1)
                if eid == self._my_eid:
                    continue
                if eid in self._remote_players:
                    self._apply_remote_move(eid, m["tx"], m["ty"])
                elif eid in self._remote_mobs:
                    self._move_remote_mob(eid, m["tx"], m["ty"])
            for sp in payload.get("spawned", []):
                eid  = sp.get("eid", -1)
                kind = sp.get("kind", "player")
                if eid != -1 and eid != self._my_eid:
                    if kind == "enemy" and eid not in self._remote_mobs:
                        self._spawn_remote_mob(eid, sp)
                        continue
            for sp in payload.get("spawned", []):
                eid = sp.get("eid", -1)
                if eid != -1 and eid != self._my_eid and sp.get("kind","player") != "enemy":
                    self._spawn_remote_player_entity(eid, {
                        "tx": sp.get("tx", 0), "ty": sp.get("ty", 0),
                        "name":     sp.get("name", "?"),
                        "class_id": sp.get("class_id", "guerreiro"),
                        "hp":       sp.get("hp", 100),
                        "hp_max":   sp.get("hp_max", 100),
                    })
            for eid in payload.get("despawned", []):
                self._remote_players.pop(eid, None)
            # Resultados de combate: atualiza HP e mostra texto flutuante
            for cr in payload.get("combat", []):
                self._apply_combat_result(cr)

        elif msg_type == MsgType.STATS_UPDATE:
            eid = payload.get("eid", -1)
            if eid in self._remote_players:
                if "hp" in payload:
                    self._remote_players[eid]["hp"] = payload["hp"]

        elif msg_type == MsgType.PONG:
            if self._net:
                rtt = int(__import__("time").time() * 1000) - payload.get("client_ts", 0)
                self._net.latency_ms = rtt

    def _apply_combat_result(self, cr: dict) -> None:
        """Aplica resultado de combate do servidor: HP + texto flutuante."""
        from components import Position, CombatStats
        from floating_text import FLT
        server_target = cr.get("target", -1)
        damage        = cr.get("damage", 0)
        outcome       = cr.get("outcome", "hit")
        hp_after      = cr.get("hp_after", -1)

        col_crit = (255, 255, 80)
        col_hit  = (255, 80,  80)
        col_self = (255, 140, 140)

        # ── Mob foi atacado (player → mob) ────────────────────────────
        local_eid = self._remote_mobs.get(server_target)
        if local_eid is not None:
            if hp_after >= 0:
                _, hp_max = self._mob_hp.get(server_target, (hp_after, hp_after))
                self._mob_hp[server_target] = (hp_after, hp_max)
            pos = self.world.get_component(local_eid, Position)
            if pos and damage > 0:
                col = col_crit if outcome == "crit" else col_hit
                txt = f"CRÍTICO! {damage}" if outcome == "crit" else str(damage)
                FLT.add(txt, pos.x, pos.y, col, size="normal")
                # Sons: o PlayerInputSystem offline já toca o som para o atacante local.
                # Para o observador remoto, nenhum som adicional por ora.
            return

        # ── Player local foi atacado (mob → player) ───────────────────
        if server_target == self._my_eid and damage > 0:
            # Aplica dano no HP local (CombatStats.current_hp → barra de HP atualiza)
            cs = self.world.get_component(self.player_entity, CombatStats)
            if cs:
                cs.current_hp = max(0, cs.current_hp - damage)
            player_pos = self.world.get_component(self.player_entity, Position)
            if player_pos:
                txt = f"CRÍTICO! -{damage}" if outcome == "crit" else f"-{damage}"
                FLT.add(txt, player_pos.x, player_pos.y, col_self, size="normal")

    def _sync_combat_target(self) -> None:
        """Envia AUTO_ATTACK ao servidor quando o alvo do jogador muda."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from components import CombatState
        cs = self.world.get_component(self.player_entity, CombatState)
        if not cs:
            return
        local_target = cs.target_entity_id
        # Converte local_eid para server_eid (só mobs remotos são válidos)
        server_target = self._remote_mobs_reverse.get(local_target, -1)
        # Se não é mob remoto e não é -1 (desfoque de alvo), usa -1
        if local_target != -1 and server_target == -1:
            server_target = -1
        if server_target != self._net_last_target:
            self._net.send(
                __import__("shared.messages", fromlist=["MsgType"]).MsgType.AUTO_ATTACK,
                {"tid": server_target})
            self._net_last_target = server_target

    def _send_player_move(self) -> None:
        """
        Envia MOVE ao servidor se o jogador se moveu desde o último envio.
        Chamado após sistemas.update() no game loop.
        """
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        if not tm:
            return
        # Usa target (início do movimento) em vez de current (fim da animação)
        # → outro jogador vê o movimento começar junto com a animação local
        tx, ty = tm.target_tile_x, tm.target_tile_y
        if tx != self._net_last_tx or ty != self._net_last_ty:
            self._net.move(tx, ty)
            self._net_last_tx = tx
            self._net_last_ty = ty

    def _draw_online_hud(self) -> None:
        """HUD minimalista de conexão — canto superior direito da tela."""
        if not self._net:
            return
        connected = self._net.connected
        latency   = self._net.latency_ms
        n_players = len(self._remote_players)

        # Status de conexão
        if connected and self._my_eid != -1:
            status_txt = f"Online  {latency}ms  |  {n_players} jogador(es) próximo(s)"
            status_col = (80, 220, 80)
        elif connected:
            status_txt = "Conectado — aguardando login..."
            status_col = (220, 220, 80)
        else:
            status_txt = "Desconectado"
            status_col = (220, 80, 80)

        surf = self.font_xs.render(status_txt, True, status_col)
        x = SCREEN_WIDTH - surf.get_width() - 8
        y = 6
        bg = pygame.Surface((surf.get_width() + 6, surf.get_height() + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        self.screen.blit(bg, (x - 3, y - 2))
        self.screen.blit(surf, (x, y))

        # Posição local (debug)
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        if tm:
            pos_txt = f"tile ({tm.current_tile_x}, {tm.current_tile_y})"
            ps = self.font_xs.render(pos_txt, True, (160, 160, 160))
            self.screen.blit(ps, (SCREEN_WIDTH - ps.get_width() - 8, y + surf.get_height() + 2))

    def _spawn_remote_mob(self, server_eid: int, data: dict) -> None:
        """Cria mob no ECS local a partir de dados do servidor."""
        if server_eid in self._remote_mobs:
            return
        from entity_factory import create_enemy
        from components import Renderable
        local_eid = create_enemy(
            self.world,
            data.get("tx", 0),
            data.get("ty", 0),
            attack_range = 3 if data.get("is_ranged", False) else 1,
            is_ranged    = data.get("is_ranged", False),
            tier         = data.get("tier", "normal"),
            race         = data.get("race", "Humanoide"),
            entity_class = data.get("entity_class", ""),
            level        = data.get("level", 1),
        )
        # Aplica cor do servidor
        server_color = data.get("color")
        if server_color:
            ren = self.world.get_component(local_eid, Renderable)
            if ren:
                ren.color = tuple(server_color)

        # Remove CombatStats do mob remoto — evita que PlayerInputSystem calcule dano local.
        # HP é rastreado em _mob_hp e atualizado exclusivamente pelo servidor.
        from components import CombatStats
        self.world.remove_component(local_eid, CombatStats)

        # Armazena HP autoritativo do servidor
        hp_max = data.get("hp_max", 100)
        hp     = data.get("hp",     hp_max)
        self._mob_hp[server_eid]              = (hp, hp_max)
        self._remote_mobs[server_eid]         = local_eid
        self._remote_mobs_reverse[local_eid]  = server_eid

    def _move_remote_mob(self, server_eid: int, new_tx: int, new_ty: int) -> None:
        """Atualiza posição de mob remoto no ECS local."""
        from components import TileMovement, Position
        from shared.constants import TILE_SIZE as _TS
        local_eid = self._remote_mobs.get(server_eid)
        if local_eid is None:
            return
        tm = self.world.get_component(local_eid, TileMovement)
        if tm:
            tm.current_tile_x = new_tx;  tm.current_tile_y = new_ty
            tm.target_tile_x  = new_tx;  tm.target_tile_y  = new_ty
        pos = self.world.get_component(local_eid, Position)
        if pos:
            pos.prev_x = pos.x;  pos.prev_y = pos.y
            pos.x = new_tx * _TS + _TS // 2
            pos.y = new_ty * _TS + _TS // 2

    # ── Jogadores remotos — abordagem ECS ────────────────────────────────────

    def _spawn_remote_player_entity(self, server_eid: int, data: dict) -> None:
        """Cria entidade ECS real para jogador remoto. TileMovementSystem anima."""
        if server_eid in self._remote_players:
            return
        from components import (Position, TileMovement, Renderable,
                                 Visible, RemoteControlled)
        from utils import start_tile_movement
        from tileset import TILE_SIZE as _TS

        tx = data.get("tx", 0)
        ty = data.get("ty", 0)
        px = tx * _TS + _TS // 2
        py = ty * _TS + _TS // 2

        _CLASS_COLORS = {"guerreiro": (200,80,80), "mago": (80,80,220), "arqueiro": (80,200,80)}
        col = _CLASS_COLORS.get(data.get("class_id", "guerreiro"), (180, 180, 180))

        local_eid = self.world.create_entity()
        self.world.add_component(local_eid, Position(x=px, y=py, prev_x=px, prev_y=py))
        self.world.add_component(local_eid, TileMovement(
            current_tile_x=tx, current_tile_y=ty,
            target_tile_x=tx,  target_tile_y=ty,
        ))
        self.world.add_component(local_eid, Renderable(
            color=col, width=_TS - 4, height=_TS - 4))
        self.world.add_component(local_eid, Visible())
        self.world.add_component(local_eid, RemoteControlled(
            server_eid=server_eid,
            name=data.get("name", "?"),
            class_id=data.get("class_id", "guerreiro"),
            hp=data.get("hp", 100),
            hp_max=data.get("hp_max", 100),
        ))
        self._remote_players[server_eid] = local_eid

    def _apply_remote_move(self, eid: int, new_tx: int, new_ty: int) -> None:
        """Atualiza target_tile do jogador remoto — TileMovementSystem anima."""
        from components import TileMovement, Position
        from utils import start_tile_movement
        local_eid = self._remote_players.get(eid)
        if local_eid is None:
            return
        tm  = self.world.get_component(local_eid, TileMovement)
        pos = self.world.get_component(local_eid, Position)
        if not tm or not pos:
            return
        if not tm.is_moving:
            start_tile_movement(pos, tm, new_tx, new_ty)
        else:
            # Entidade ainda animando: atualiza target para encadear suavemente
            tm.target_tile_x = new_tx
            tm.target_tile_y = new_ty

    def _draw_mob_hp_bars(self, cam_x: float, cam_y: float) -> None:
        """Desenha barras de HP dos mobs remotos com dados autoritativos do servidor."""
        if not self._mob_hp:
            return
        from components import Position
        from tileset import TILE_SIZE as _TS
        W = _TS - 4
        zoom_surf = self._zoom_surf
        for server_eid, (hp, hp_max) in self._mob_hp.items():
            local_eid = self._remote_mobs.get(server_eid)
            if local_eid is None:
                continue
            pos = self.world.get_component(local_eid, Position)
            if not pos:
                continue
            # Posição idêntica ao RenderSystem offline:
            # bar_y = int(draw_y - height/2) - 7  →  7px acima do topo do sprite
            draw_x = pos.x - cam_x
            draw_y = pos.y - cam_y
            bar_x  = int(draw_x - W / 2)
            bar_y  = int(draw_y - W / 2) - 7
            if hp_max > 0:
                ratio = max(0.0, hp / hp_max)
                pygame.draw.rect(zoom_surf, (80, 0, 0),    (bar_x, bar_y, W, 4))
                pygame.draw.rect(zoom_surf, (0, 200, 60),  (bar_x, bar_y, int(W * ratio), 4))

    def _remove_remote_player_entity(self, server_eid: int) -> None:
        local_eid = self._remote_players.pop(server_eid, None)
        if local_eid is not None:
            try:
                self.world.remove_entity(local_eid)
            except Exception:
                pass

    def _draw_remote_players(self, cam_x: float, cam_y: float) -> None:
        """Nome + HP dos jogadores remotos. Posição lida do ECS (TileMovementSystem anima)."""
        if not self._remote_players:
            return
        from components import Position, RemoteControlled
        from tileset import TILE_SIZE as _TS
        W = H = _TS - 4
        zoom_surf = self._zoom_surf

        for server_eid, local_eid in self._remote_players.items():
            pos = self.world.get_component(local_eid, Position)
            rc  = self.world.get_component(local_eid, RemoteControlled)
            if not pos or not rc:
                continue
            px = pos.x - W // 2 - cam_x
            py = pos.y - H // 2 - cam_y
            ns = self.font_xs.render(rc.name, True, (255, 255, 200))
            zoom_surf.blit(ns, (int(px) + W // 2 - ns.get_width() // 2,
                                int(py) - ns.get_height() - 2))
            if rc.hp_max > 0:
                fill_w = max(0, int(W * rc.hp / rc.hp_max))
                pygame.draw.rect(zoom_surf, (100, 0, 0),
                                 (int(px), int(py) + H + 2, W, 4))
                pygame.draw.rect(zoom_surf, (0, 200, 0),
                                 (int(px), int(py) + H + 2, fill_w, 4))

    def _draw_remote_players_OLD(self, cam_x, cam_y) -> None:
        if not self._remote_players:
            return
        import time as _t
        from shared.constants import TILE_SIZE as _TS
        _CLASS_COLORS = {
            "guerreiro": (200, 80,  80),
            "mago":      (80,  80,  220),
            "arqueiro":  (80,  200, 80),
        }
        W = H = _TS - 4
        zoom_surf = self._zoom_surf
        now = _t.monotonic()

        for eid, data in self._remote_players.items():
            # Posição pixel com dead reckoning + easing
            cur_px, cur_py = self._interp_pixel(data, _TS)
            px = cur_px - W // 2 - cam_x
            py = cur_py - H // 2 - cam_y

            col = _CLASS_COLORS.get(data.get("class_id", "guerreiro"), (180, 180, 180))
            pygame.draw.rect(zoom_surf, col, (int(px), int(py), W, H))
            pygame.draw.rect(zoom_surf, (255, 255, 255), (int(px), int(py), W, H), 1)

            # Nome acima
            ns = self.font_xs.render(data.get("name", "?"), True, (255, 255, 200))
            zoom_surf.blit(ns, (int(px) + W // 2 - ns.get_width() // 2,
                                int(py) - ns.get_height() - 2))

            # Barra de HP
            hp     = data.get("hp", 100)
            hp_max = data.get("hp_max", 100)
            if hp_max > 0:
                bar_w  = W
                fill_w = max(0, int(bar_w * hp / hp_max))
                pygame.draw.rect(zoom_surf, (100, 0, 0),
                                 (int(px), int(py) + H + 2, bar_w, 4))
                pygame.draw.rect(zoom_surf, (0, 200, 0),
                                 (int(px), int(py) + H + 2, fill_w, 4))

    # ------------------------------------------------------------------
    def _load_menu_keys(self) -> None:
        """Carrega atalhos de menu do config.json para self._menu_keys."""
        import config as _cfg
        from config import DEFAULTS
        defaults = DEFAULTS.get("menu_keybinds", {})
        saved    = _cfg.load().get("menu_keybinds", {})
        self._menu_keys = {**defaults, **saved}

    def _save_config(self) -> None:
        """Persiste todas as configurações (resolução + áudio + hotbar) em config.json."""
        import config as _cfg
        _cfg.save({
            "scale":          self._scale,
            "music_volume":   SOUNDS.music_volume,
            "sfx_volume":     SOUNDS.sfx_volume,
            "music_enabled":  SOUNDS.music_enabled,
            "sfx_enabled":    SOUNDS.sfx_enabled,
            "debug_mode":     DEBUG_MODE,
            "profile_frames": PROFILE_FRAMES,
            "hotbar":         self._hotbar_to_dict(),
            "consumable_bar": self._consumable_bar_to_dict(),
            "menu_keybinds":  self._menu_keys,
        })
        self._load_menu_keys()   # sincroniza em memória após salvar

    def _hotbar_to_dict(self) -> dict:
        """Serializa a hotbar atual (slots + keybinds) para persistência."""
        ps = self.world.get_component(self.player_entity, PlayerSkills)
        if not ps:
            return {}
        return {
            "slots":    [s.skill_id if s else None for s in ps.skills],
            "keybinds": list(ps.keybinds),
        }

    def _consumable_bar_to_dict(self) -> dict:
        """Serializa a barra de consumíveis para persistência."""
        from components import ConsumableBar as _CB
        cbar = self.world.get_component(self.player_entity, _CB)
        if not cbar:
            return {}
        return {
            "slots":    list(cbar.slots),
            "keybinds": list(cbar.keybinds),
        }

    def _apply_hotbar_config(self, new_character: bool = False) -> None:
        """Restaura layout e keybinds da hotbar e barra de consumíveis a partir de config.json."""
        import config as _cfg
        from skill_config import SKILL_CATALOG, NUM_SLOTS, DEFAULT_KEYBINDS
        data = _cfg.load()

        # ── Skills hotbar ────────────────────────────────────────────────
        hb = data.get("hotbar")
        if hb:
            ps = self.world.get_component(self.player_entity, PlayerSkills)
            if ps:
                saved_slots    = hb.get("slots",    [])
                saved_keybinds = hb.get("keybinds", [])

                talent_skills = [s for s in ps.skills if s and getattr(s, "talent_id", None)]
                new_skills: list = [None] * NUM_SLOTS
                for i in range(NUM_SLOTS):
                    sid = saved_slots[i] if i < len(saved_slots) else None
                    if sid is None:
                        continue
                    ts = next((t for t in talent_skills if t.skill_id == sid), None)
                    if ts:
                        new_skills[i] = ts
                    elif sid in SKILL_CATALOG and sid in ps.learned_skill_ids:
                        new_skills[i] = PlayerSkills._make_skill(sid, SKILL_CATALOG)

                # Talent skills que não estavam salvas no config (ex: desbloqueadas após
                # último save) precisam ser reinseridas no primeiro slot livre.
                placed_ids = {s.skill_id for s in new_skills if s}
                for ts in talent_skills:
                    if ts.skill_id not in placed_ids:
                        try:
                            idx = new_skills.index(None)
                            new_skills[idx] = ts
                        except ValueError:
                            new_skills.append(ts)
                        placed_ids.add(ts.skill_id)

                # Skills aprendidas (trainer/etc.) ausentes do config — reinserir.
                # Garante que skills aprendidas durante a sessão anterior (sem save
                # da config naquele momento) não desapareçam da hotbar.
                for sid in ps.learned_skill_ids:
                    if sid not in placed_ids and sid in SKILL_CATALOG:
                        sk = PlayerSkills._make_skill(sid, SKILL_CATALOG)
                        if sk:
                            try:
                                idx = new_skills.index(None)
                                new_skills[idx] = sk
                            except ValueError:
                                new_skills.append(sk)
                            placed_ids.add(sid)

                ps.skills = new_skills

                for i in range(min(len(saved_keybinds), NUM_SLOTS)):
                    ps.keybinds[i] = saved_keybinds[i]

        # ── Consumable bar ────────────────────────────────────────────────
        cb_data = data.get("consumable_bar")
        if cb_data:
            from components import ConsumableBar as _CB
            cbar = self.world.get_component(self.player_entity, _CB)
            if cbar:
                saved_keybinds = cb_data.get("keybinds", [])
                for i in range(min(len(saved_keybinds), _CB.NUM_SLOTS)):
                    cbar.keybinds[i] = saved_keybinds[i]
                # Slots de itens só são restaurados para personagens existentes;
                # novos personagens começam com a barra vazia.
                if not new_character:
                    saved_slots = cb_data.get("slots", [])
                    for i in range(min(len(saved_slots), _CB.NUM_SLOTS)):
                        cbar.slots[i] = saved_slots[i]

    # ── Aplica nova escala de resolução ────────────────────────────────────
    def _apply_scale(self, scale: float) -> None:
        global SCREEN_WIDTH, SCREEN_HEIGHT
        self._scale   = scale
        win_w         = int(1280 * scale)
        win_h         = int(720  * scale)
        SCREEN_WIDTH  = win_w
        SCREEN_HEIGHT = win_h
        self.screen   = pygame.Surface((win_w, win_h))
        self._display = pygame.display.set_mode(
            (win_w, win_h), pygame.DOUBLEBUF, vsync=1)
        self._rebuild_screen_refs(self.screen)
        # Atualiza Camera component para que offset_x/offset_y reflitam a nova resolução
        for _, cam, _ in self.world.get_entities_with(Camera, Position):
            cam.offset_x = win_w / 2
            cam.offset_y = win_h / 2
        self._save_config()

    def _rebuild_screen_refs(self, new_screen: "pygame.Surface") -> None:
        """Atualiza referências à surface de render em todos os subsistemas."""
        # Sistemas ECS na lista principal
        for sys in self.systems:
            if hasattr(sys, "screen"):
                sys.screen = new_screen
        # Sistemas com refs diretas fora da lista
        for attr in ("_skill_system", "_loot_system", "_projectile_system",
                     "_render_system", "_tile_render_system"):
            obj = getattr(self, attr, None)
            if obj and hasattr(obj, "screen"):
                obj.screen = new_screen
        # Overlays e UI
        self._map_overlay.screen = new_screen
        self._minimap.screen     = new_screen
        if hasattr(self._talent_system, "screen"):
            self._talent_system.screen = new_screen
        if hasattr(self._shop_system, "screen"):
            self._shop_system.screen = new_screen
        if hasattr(self._quest_dialog, "screen"):
            self._quest_dialog.screen = new_screen
        if hasattr(self._quest_journal, "screen"):
            self._quest_journal.screen = new_screen
        for attr in ("_blacksmith_system", "_crafting_system", "_trainer_system"):
            obj = getattr(self, attr, None)
            if obj and hasattr(obj, "screen"):
                obj.screen = new_screen
        # God Mode
        if hasattr(self, "_god_mode") and hasattr(self._god_mode, "_screen"):
            self._god_mode._screen = new_screen

    # ------------------------------------------------------------------
    # HUD
    # ------------------------------------------------------------------
    # Hotbar de habilidades (1-4)
    # ------------------------------------------------------------------

    _HB_W   = 68
    _HB_H   = 68
    _HB_ICO = 64
    _HB_PAD = 6

    _SKILL_FALLBACK_COLORS = [
        (180,  60,  60),   # 1 Golpe Poderoso
        ( 60, 180,  80),   # 2 Cura
        (200, 130,   0),   # 3 Impacto
        (140,  60, 200),   # 4 Executar
    ]

    def _handle_hotbar_click(self, event):
        """Aciona habilidade ao clicar com botão esquerdo em slot da hotbar."""
        from components import PlayerSkills
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        if not player_skills:
            return
        occupied = [(i, s) for i, s in enumerate(player_skills.skills) if s is not None]
        if not occupied:
            return
        n_occ   = len(occupied)
        total_w = n_occ * self._HB_W + (n_occ - 1) * self._HB_PAD
        x0      = SCREEN_WIDTH  // 2 - total_w // 2
        y0      = SCREEN_HEIGHT - self._HB_H - 10
        mx, my  = event.pos
        for j, (i, skill) in enumerate(occupied):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                if not self._skill_system._use_skill(i, skill):
                    skill.fail_flash_timer = 0.2
                break

    def _handle_consumable_bar_click(self, event) -> None:
        """Usa consumível ao clicar com botão esquerdo em slot da barra de consumíveis."""
        from components import ConsumableBar as _CB, PlayerSkills as _PS
        cbar = self.world.get_component(self.player_entity, _CB)
        if not cbar:
            return

        cons_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS) if cbar.slots[i]]
        if not cons_occ:
            return

        ps           = self.world.get_component(self.player_entity, _PS)
        n_skills_occ = sum(1 for s in ps.skills if s) if ps else 0
        skills_w     = n_skills_occ * self._HB_W + max(0, n_skills_occ - 1) * self._HB_PAD
        x0           = SCREEN_WIDTH // 2 - skills_w // 2 + skills_w + 20
        y0           = SCREEN_HEIGHT - self._HB_H - 10
        mx, my       = event.pos

        for j, (i, item_name) in enumerate(cons_occ):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                if cbar.global_cooldown <= 0:
                    for sys in self.systems:
                        if isinstance(sys, ConsumableSystem):
                            sys._use_consumable(self.player_entity, item_name, cbar)
                            break
                break

    # rage_cost e proc_attr são agora lidos diretamente do objeto Skill (via skill_config.py)

    def _draw_low_hp_vignette(self) -> None:
        """Vinheta vermelha pulsante na borda da tela quando HP < 30%."""
        from components import CombatStats as _CS_V
        cs = self.world.get_component(self.player_entity, _CS_V)
        if not cs or cs.max_hp <= 0:
            return
        ratio = cs.current_hp / cs.max_hp
        if ratio >= 0.30:
            return
        # Pulso senoidal: 0.0 → 1.0 → 0.0, ~1 ciclo/s
        pulse = (math.sin(pygame.time.get_ticks() * 0.005) + 1.0) * 0.5
        # Intensifica quanto mais baixo o HP: 0% HP → alpha máx 190; 30% → 0
        intensity = 1.0 - (ratio / 0.30)
        alpha = int(pulse * intensity * 190)
        if alpha <= 0:
            return
        sw, sh = self.screen.get_width(), self.screen.get_height()
        bw = 28
        surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
        col = (200, 20, 20, alpha)
        pygame.draw.rect(surf, col, (0,       0,       sw, bw))
        pygame.draw.rect(surf, col, (0,       sh - bw, sw, bw))
        pygame.draw.rect(surf, col, (0,       0,       bw, sh))
        pygame.draw.rect(surf, col, (sw - bw, 0,       bw, sh))
        self.screen.blit(surf, (0, 0))

    def _draw_hotbar(self):
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        if not player_skills:
            return

        # --- Rage atual do jogador ---
        from components import CharacterStats as _CS
        _char = self.world.get_component(self.player_entity, _CS)
        player_rage = _char.rage if _char else 0

        # --- Dados para verificar proc do Executar (HP% do alvo) ---
        target_hp_ratio = 1.0
        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state and combat_state.target_entity_id != -1:
            tgt_cs = self.world.get_component(combat_state.target_entity_id, CombatStats)
            if tgt_cs and tgt_cs.max_hp > 0:
                target_hp_ratio = tgt_cs.current_hp / tgt_cs.max_hp

        # Pulso animado para o brilho (0..1, ciclo ~1.6s)
        pulse = (math.sin(pygame.time.get_ticks() / 250.0) + 1) / 2

        # Channeling de Fatiador de Corpos — bloqueia visualmente todas as skills
        channeling = _char is not None and _char.fatiador_timer > 0

        # Durante drag do painel Habilidades: mostra TODOS os slots (incluindo vazios)
        dragging_skill = getattr(self, "_hab_drag_skill", None)
        if dragging_skill:
            from skill_config import NUM_SLOTS as _NS
            occupied = [(i, player_skills.skills[i]) for i in range(_NS)]
        else:
            occupied = [(i, s) for i, s in enumerate(player_skills.skills) if s is not None]
        n_occ   = len(occupied)
        if n_occ == 0:
            return
        total_w = n_occ * self._HB_W + (n_occ - 1) * self._HB_PAD
        x0      = SCREEN_WIDTH  // 2 - total_w // 2
        y0      = SCREEN_HEIGHT - self._HB_H - 10
        mx, my  = pygame.mouse.get_pos()

        for j, (i, skill) in enumerate(occupied):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            r  = pygame.Rect(sx, y0, self._HB_W, self._HB_H)

            # Slot vazio exibido durante drag — apenas fundo destacado
            if skill is None:
                pygame.draw.rect(self.screen, (38, 32, 16), r, border_radius=5)
                pygame.draw.rect(self.screen, (160, 130, 50), r, 2, border_radius=5)
                num_s = self.font_xs.render(str(i + 1), True, (100, 85, 48))
                self.screen.blit(num_s, (sx + 4, y0 + 4))
                continue

            # --- Estado de proc por skill ---
            # Charge-based: proc = tem cargas disponíveis
            if skill.max_charges > 0:
                is_procced   = skill.charges > 0
                visual_ready = is_procced
            elif skill.skill_id == "executar":
                # Caso especial: proc composto — carga livre OU (HP% baixo + rage + alvo vivo)
                free_charge  = _char is not None and _char.free_executar_charges > 0
                rage_ok      = player_rage >= skill.rage_cost
                target_alive = (combat_state is not None and
                                combat_state.target_entity_id != -1 and
                                target_hp_ratio > 0)
                hp_proc      = rage_ok and target_alive and target_hp_ratio <= 0.30
                is_procced   = free_charge or hp_proc
                visual_ready = is_procced
            elif skill.proc_attr:
                # Proc genérico: lê atributo declarado em skill_config.py
                is_procced   = _char is not None and getattr(_char, skill.proc_attr, 0) > 0
                visual_ready = skill.is_ready()
            else:
                is_procced   = False
                visual_ready = skill.is_ready()

            # Choque Térmico: brilho em skills ofensivas de escola fogo quando alvo tem root
            if (not is_procced
                    and getattr(skill, "school", "") == "fogo"
                    and getattr(skill, "offensive", True)
                    and _char is not None
                    and getattr(_char, "thermal_shock_active", False)):
                is_procced = True

            # --- Efeito de brilho (proc) — desenhado antes do slot ---
            if is_procced:
                glow_r = int(180 + 75 * pulse)
                glow_g = int(120 + 60 * pulse)
                glow_b = int(20  + 30 * pulse)
                for expand in range(5, 0, -1):
                    gr    = r.inflate(expand * 2, expand * 2)
                    gs    = pygame.Surface((gr.width, gr.height), pygame.SRCALPHA)
                    alpha = max(0, int((50 + 100 * pulse) * (1.0 - expand / 6)))
                    gs.fill((glow_r, glow_g, glow_b, alpha))
                    self.screen.blit(gs, gr.topleft)

            # --- Fundo do slot ---
            bg = (35, 28, 14) if visual_ready else (20, 15, 8)
            pygame.draw.rect(self.screen, bg, r, border_radius=5)

            # --- Ícone ---
            ic        = self._HB_ICO
            pad       = (self._HB_W - ic) // 2
            icon_r    = pygame.Rect(sx + pad, y0 + pad, ic, ic)
            _icon_key = (ICONS.skill_key_by_name(skill.icon_name)
                         if skill.icon_name else ICONS.skill_key(i))
            icon_surf = ICONS.get(_icon_key, ic)
            if icon_surf:
                self.screen.blit(icon_surf, icon_r)
            else:
                fb      = self._SKILL_FALLBACK_COLORS[i % len(self._SKILL_FALLBACK_COLORS)]
                alpha   = 200 if visual_ready else 80
                fb_surf = pygame.Surface((ic, ic), pygame.SRCALPHA)
                fb_surf.fill((*fb, alpha))
                self.screen.blit(fb_surf, icon_r)

            # --- Overlay de cooldown / inatividade / carga ---
            if skill.skill_id == "executar":
                if skill.current_cooldown > 0:
                    # Em cooldown — overlay normal com contador
                    cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                    ov_h     = int(self._HB_H * cd_ratio)
                    ov       = pygame.Surface((self._HB_W, ov_h), pygame.SRCALPHA)
                    ov.fill((0, 0, 0, 160))
                    self.screen.blit(ov, (sx, y0))
                    cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                    self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                               y0 + self._HB_H // 2 - cd_surf.get_height() // 2))
                elif not is_procced:
                    # Pronto mas alvo com HP alto — inativo sem texto
                    ov = pygame.Surface((self._HB_W, self._HB_H), pygame.SRCALPHA)
                    ov.fill((0, 0, 0, 160))
                    self.screen.blit(ov, (sx, y0))
            elif skill.max_charges > 0:
                # Habilidade baseada em cargas (vitoria_iminente etc.)
                if skill.current_cooldown > 0:
                    # Em cooldown: overlay progressivo + contador (igual skills normais)
                    cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                    ov_h     = int(self._HB_H * cd_ratio)
                    ov       = pygame.Surface((self._HB_W, ov_h), pygame.SRCALPHA)
                    ov.fill((0, 0, 0, 160))
                    self.screen.blit(ov, (sx, y0))
                    cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                    self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                               y0 + self._HB_H // 2 - cd_surf.get_height() // 2))
                elif is_procced:
                    if skill.charge_timeout > 0 and skill.charge_timer > 0:
                        timer_surf = self.font_sm.render(f"{skill.charge_timer:.0f}s", True, (100, 255, 120))
                        self.screen.blit(timer_surf, (sx + self._HB_W // 2 - timer_surf.get_width() // 2,
                                                      y0 + self._HB_H // 2 - timer_surf.get_height() // 2))
                else:
                    ov = pygame.Surface((self._HB_W, self._HB_H), pygame.SRCALPHA)
                    ov.fill((0, 0, 0, 160))
                    self.screen.blit(ov, (sx, y0))
            elif not visual_ready:
                # Cooldown normal
                cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                ov_h     = int(self._HB_H * cd_ratio)
                ov       = pygame.Surface((self._HB_W, ov_h), pygame.SRCALPHA)
                ov.fill((0, 0, 0, 160))
                self.screen.blit(ov, (sx, y0))
                cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                           y0 + self._HB_H // 2 - cd_surf.get_height() // 2))

            # --- Borda (por cima do overlay) ---
            if is_procced:
                br = int(200 + 55 * pulse)
                bg_ = int(150 + 60 * pulse)
                border = (br, bg_, 30)
                pygame.draw.rect(self.screen, border, r, 3, border_radius=5)
            else:
                border = (200, 160, 60) if visual_ready else (70, 55, 28)
                pygame.draw.rect(self.screen, border, r, 2, border_radius=5)

            # --- Overlay de channeling (Fatiador de Corpos) ---
            if channeling and getattr(skill, "skill_id", None) != "fatiador_de_corpos":
                ch_ratio = _char.fatiador_timer / 5.0
                ov_h = int(self._HB_H * ch_ratio)
                ov   = pygame.Surface((self._HB_W, ov_h), pygame.SRCALPHA)
                ov.fill((80, 0, 0, 180))
                self.screen.blit(ov, (sx, y0))

            # --- Overlay de GCD ---
            from components import PlayerSkills as _PS2
            _pskills = self.world.get_component(self.player_entity, _PS2)
            if not channeling and _pskills and _pskills.gcd_timer > 0:
                gcd_ratio = _pskills.gcd_timer / _PS2.GCD_DURATION
                ov_h = int(self._HB_H * gcd_ratio)
                ov   = pygame.Surface((self._HB_W, ov_h), pygame.SRCALPHA)
                ov.fill((0, 0, 0, 160))
                self.screen.blit(ov, (sx, y0))

            # --- Overlay de Rage insuficiente ---
            rage_cost = skill.rage_cost
            if skill.skill_id == "golpe_poderoso":
                _cs_hb = self.world.get_component(self.player_entity, CombatStats)
                rage_cost = _cs_hb.golpe_poderoso_rage_cost if _cs_hb else rage_cost
            # O overlay só é suprimido se o proc explicitamente dispensa o custo
            cost_bypassed = is_procced and skill.proc_ignores_cost
            if rage_cost > 0 and player_rage < rage_cost and not cost_bypassed:
                ov = pygame.Surface((self._HB_W, self._HB_H), pygame.SRCALPHA)
                ov.fill((0, 0, 0, 140))
                self.screen.blit(ov, (sx, y0))

            # --- Flash de falha (tentativa sem sucesso) ---
            if skill.fail_flash_timer > 0:
                skill.fail_flash_timer = max(0.0, skill.fail_flash_timer - self._dt)
                ov = pygame.Surface((self._HB_W, self._HB_H), pygame.SRCALPHA)
                ov.fill((0, 0, 0, 50))  # ~20% escurecimento
                self.screen.blit(ov, (sx, y0))

            # --- Etiqueta da tecla (keybind configurável) ---
            kb_name = pygame.key.name(player_skills.keybinds[i]).upper()
            key_col = (220, 200, 140) if visual_ready else (80, 70, 50)
            self.screen.blit(self.font_sm.render(kb_name, True, key_col), (sx + 3, y0 + 2))

            # --- Tooltip no hover ---
            if r.collidepoint(mx, my):
                lines = self._skill_tooltip_lines(
                    skill, is_procced, visual_ready, target_hp_ratio, player_rage)
                self._pending_skill_tooltip = (mx, y0 - 4, skill.name, lines)

    # ------------------------------------------------------------------
    # Barra de consumíveis
    # ------------------------------------------------------------------

    def _draw_consumable_bar(self) -> None:
        from components import ConsumableBar as _CB, Inventory as _Inv
        cbar = self.world.get_component(self.player_entity, _CB)
        inv  = self.world.get_component(self.player_entity, _Inv)
        if not cbar:
            return

        W   = self._HB_W
        H   = self._HB_H
        PAD = self._HB_PAD

        # Âncora à direita da skills bar compactada
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        n_skills_occ  = sum(1 for s in player_skills.skills if s) if player_skills else 0
        skills_w      = n_skills_occ * W + max(0, n_skills_occ - 1) * PAD
        skills_x0     = SCREEN_WIDTH // 2 - skills_w // 2

        # Apenas slots de consumíveis ocupados, compactados
        cons_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS) if cbar.slots[i]]
        if not cons_occ:
            return
        cons_w = len(cons_occ) * W + (len(cons_occ) - 1) * PAD
        x0     = skills_x0 + skills_w + 20
        y0     = SCREEN_HEIGHT - H - 10
        mx, my = pygame.mouse.get_pos()

        for j, (i, item_name) in enumerate(cons_occ):
            sx = x0 + j * (W + PAD)
            r  = pygame.Rect(sx, y0, W, H)

            # Background
            pygame.draw.rect(self.screen, (18, 36, 22), r, border_radius=5)

            if item_name and inv:
                item = next((it for it in inv.items if it.name == item_name), None)
                if item:
                    _ic_key = "item_" + item_name.lower().replace(" ", "_")
                    _ic = ICONS.get(_ic_key, W - 2)
                    if _ic:
                        self.screen.blit(_ic, (r.x + 2, r.y + 2))
                    else:
                        letter = self.font_sm.render(item_name[0].upper(), True, (100, 220, 140))
                        self.screen.blit(letter, letter.get_rect(center=r.center))
                    # Stack count
                    draw_stack_count(self.screen, item, r, self.font_xs)
                else:
                    # Item esgotado — slot visível mas sem ícone (stack chegou a 0)
                    pass

            # GCD overlay
            if cbar.global_cooldown > 0:
                gcd_ratio = cbar.global_cooldown / _CB.GCD_DURATION
                ov_h = int(H * gcd_ratio)
                ov   = pygame.Surface((W, ov_h), pygame.SRCALPHA)
                ov.fill((0, 0, 0, 160))
                self.screen.blit(ov, (sx, y0))
                if j == 0:
                    cd_s = self.font_xs.render(f"{cbar.global_cooldown:.1f}", True, (160, 210, 170))
                    self.screen.blit(cd_s, cd_s.get_rect(
                        centerx=sx + W // 2, y=y0 + H // 2 - cd_s.get_height() // 2))

            # Border
            border_col = (80, 160, 100) if item_name else (40, 70, 50)
            pygame.draw.rect(self.screen, border_col, r, 2, border_radius=5)

            # Keybind label
            kb_name = pygame.key.name(cbar.keybinds[i]).upper()
            key_col = (140, 210, 160) if item_name else (50, 80, 60)
            self.screen.blit(self.font_sm.render(kb_name, True, key_col), (sx + 3, y0 + 2))

            # Tooltip
            if r.collidepoint(mx, my) and item_name:
                item = next((it for it in inv.items if it.name == item_name), None) if inv else None
                if item and item.consumable:
                    lines = []
                    h_inst = item.consumable.get("heal_instant", 0)
                    h_tick = item.consumable.get("heal_per_tick", 0)
                    ticks  = item.consumable.get("ticks", 0)
                    interv = item.consumable.get("interval", 2.0)
                    ooc    = item.consumable.get("ooc_only", False)
                    if h_inst:
                        lines.append((f"Cura: +{h_inst} HP instantâneo", (80, 220, 120)))
                    if h_tick and ticks:
                        lines.append((f"Regen: +{h_tick} HP a cada {interv:.0f}s ({ticks}x)", (80, 200, 140)))
                    if ooc:
                        lines.append(("Apenas fora de combate", (220, 160, 60)))
                    lines.append((f"Quantidade: {item.stack}", (160, 160, 160)))
                    self._pending_skill_tooltip = (mx, y0 - 4, item_name, lines)

    # ------------------------------------------------------------------
    # Helpers de tooltip de habilidades
    # ------------------------------------------------------------------

    def _player_spell_dmg(self, dmg_weapon_pct: float = 0.0,
                          sp_coeff: float = 1.0) -> int:
        """Calcula dano de magia usando a fórmula de _spell_damage (média de arma)."""
        cs = self.world.get_component(self.player_entity, CombatStats)
        if not cs:
            return 0
        from components import Equipment as _EqSpell
        equip  = self.world.get_component(self.player_entity, _EqSpell)
        weapon = equip.slots.get("mainhand") if equip else None
        if weapon and getattr(weapon, "damage_min", 0) > 0:
            weapon_avg = (weapon.damage_min + weapon.damage_max) / 2.0
        else:
            weapon_avg = float(cs.base_physical_damage)
        return max(1, int(weapon_avg * dmg_weapon_pct + cs.spell_power * sp_coeff))

    def _player_dmg_range(self, multiplier: float = 1.0) -> tuple[int, int]:
        """Retorna (min, max) de dano físico do jogador × multiplier."""
        cs = self.world.get_component(self.player_entity, CombatStats)
        if not cs:
            return (0, 0)
        from components import Equipment as _Eq
        equip = self.world.get_component(self.player_entity, _Eq)
        weapon = equip.slots.get("mainhand") if equip else None
        if weapon and weapon.damage_min > 0:
            base_min = cs.attack_power + weapon.damage_min
            base_max = cs.attack_power + weapon.damage_max
        else:
            base = cs.attack_power + cs.base_physical_damage
            base_min = base_max = base
        return (int(base_min * multiplier), int(base_max * multiplier))

    def _skill_tooltip_lines(self, skill, is_procced: bool, visual_ready: bool,
                              target_hp_ratio: float, player_rage: int) -> list:
        """Monta linhas de tooltip no formato: Dano / Cooldown / Status."""
        C_INFO   = (180, 180, 180)
        C_OK     = (80, 220, 80)
        C_WARN   = (220, 150, 50)
        C_RAGE   = (220, 100, 30)
        C_HEAL   = (80, 220, 150)

        sid = skill.skill_id
        lines = []

        # ── Dano / Efeito ────────────────────────────────────────────────
        if sid == "golpe_poderoso":
            lo, hi = self._player_dmg_range(3.0)
            lines.append((f"Dano: {lo}–{hi}  (3× dano físico)", C_INFO))

        elif sid == "impacto":
            lo, hi = self._player_dmg_range(0.5)
            lines.append((f"Dano: {lo}–{hi}  (50% dano físico — área 3 tiles)", C_INFO))

        elif sid == "executar":
            lo, hi = self._player_dmg_range(5.0)
            lines.append((f"Dano: {lo}–{hi}  (5× dano físico)", C_INFO))
            hp_txt = f"Alvo: {target_hp_ratio*100:.0f}% HP" if target_hp_ratio < 1.0 else "Alvo: —"
            lines.append((hp_txt, C_WARN if target_hp_ratio > 0.30 else C_OK))

        elif sid == "vitoria_iminente":
            cs = self.world.get_component(self.player_entity, CombatStats)
            cura = int(cs.max_hp * 0.30) if cs else 0
            lines.append((f"Cura: {cura} HP  (30% do HP máximo)", C_HEAL))
            lines.append((f"Cargas: {skill.charges}/{skill.max_charges}", C_INFO))

        elif sid == "interceptar":
            lines.append(("Dano: nenhum  (dash até o alvo)", C_INFO))

        elif sid == "golpe_debilitante":
            lo, hi = self._player_dmg_range(0.5)
            lines.append((f"Dano: {lo}–{hi}  (50% dano físico)", C_INFO))
            lines.append(("-50% velocidade do alvo por 5s", C_WARN))

        elif sid == "punho_no_queixo":
            cs = self.world.get_component(self.player_entity, CombatStats)
            dmg = int((cs.attack_power if cs else 0) * 0.45)
            lines.append((f"Dano: {dmg}  (45% do poder de ataque)", C_INFO))
            lines.append(("Atordoa o alvo por 3s", C_WARN))

        elif sid == "fatiador_de_corpos":
            lo, hi = self._player_dmg_range(0.45)
            lines.append((f"Dano: {lo}–{hi}/s por 5s  (45% dano físico — AoE)", C_INFO))

        elif sid == "brado_provocativo":
            lines.append(("Enlouquece inimigos em raio 3 tiles por 10s", C_WARN))
            lines.append(("+5% dano causado  /  +10% dano recebido", C_INFO))

        # ── Skills do Mago ───────────────────────────────────────────────
        elif sid == "bola_de_fogo":
            dmg = self._player_spell_dmg(0.5, 1.0)
            lines.append((f"Dano: ~{dmg}  (50% arma + 100% SP)", C_INFO))

        elif sid == "nova_congelante":
            dmg = self._player_spell_dmg(0.0, 0.5)
            lines.append((f"Dano: ~{dmg}  (50% SP)", C_INFO))
            lines.append(("Enraíza inimigos a 3 tiles por 5s", C_WARN))

        elif sid == "bloco_de_gelo":
            cs = self.world.get_component(self.player_entity, CombatStats)
            cura_s = int((cs.max_hp if cs else 0) * 0.10)
            lines.append(("Imunidade total por 5s  (imóvel)", C_WARN))
            lines.append((f"Regenera +{cura_s} HP/s  (10% HP máx)", C_HEAL))

        elif sid == "polimorfia":
            lines.append(("Transforma o alvo: perde controle", C_WARN))
            cs = self.world.get_component(self.player_entity, CombatStats)
            char = self.world.get_component(self.player_entity, CharacterStats)
            regen = int((cs.max_hp if cs else 0) * 0.10)
            lines.append((f"Alvo regenera +{regen} HP/s por 6s", C_INFO))
            lines.append(("Quebra ao receber dano", C_WARN))

        elif sid == "calamidade_flamejante":
            dmg = self._player_spell_dmg(0.15, 1.0)
            lines.append((f"Dano: ~{dmg}/s por 5s  (área 2 tiles)", C_INFO))
            lines.append(("-50% velocidade dos alvos por 5s", C_WARN))

        else:
            lines.append((skill.description, C_INFO))

        lines.append(("", C_INFO))  # separador

        # ── Custo de Mana ────────────────────────────────────────────────
        if skill.mana_cost > 0 or skill.mana_cost_pct > 0:
            _cs_m  = self.world.get_component(self.player_entity, CombatStats)
            _ch_m  = self.world.get_component(self.player_entity, CharacterStats)
            if skill.mana_cost_pct > 0 and _ch_m:
                eff_cost = max(1, int(_ch_m.max_mana * skill.mana_cost_pct))
            else:
                discount = getattr(_cs_m, "fire_mana_discount", 0) if _cs_m and sid == "bola_de_fogo" else 0
                eff_cost = max(0, skill.mana_cost - discount)
            cur_mana = int(_ch_m.mana) if _ch_m else 0
            max_mana = int(_ch_m.max_mana) if _ch_m else 0
            lines.append((f"Mana: {eff_cost}  (atual: {cur_mana}/{max_mana})",
                           C_OK if cur_mana >= eff_cost else C_RAGE))

        # ── Cast time ────────────────────────────────────────────────────
        if skill.cast_time > 0:
            _cs_ct = self.world.get_component(self.player_entity, CombatStats)
            if sid in ("bola_de_fogo", "polimorfia", "calamidade_flamejante"):
                red = getattr(_cs_ct, "fire_cast_time_reduction", 0.0) if _cs_ct else 0.0
            elif sid == "nova_congelante":
                red = getattr(_cs_ct, "ice_cast_time_reduction", 0.0) if _cs_ct else 0.0
            else:
                red = 0.0
            eff_cast = max(0.0, skill.cast_time - red)
            if eff_cast == 0.0:
                lines.append(("Cast: instantâneo  (talento)", C_OK))
            else:
                lines.append((f"Cast: {eff_cast:.1f}s", C_INFO))

        # ── Cooldown / Custo de Raiva ─────────────────────────────────────
        if skill.rage_cost > 0:
            cost = skill.rage_cost
            if sid == "golpe_poderoso":
                _cs_tt = self.world.get_component(self.player_entity, CombatStats)
                cost = _cs_tt.golpe_poderoso_rage_cost if _cs_tt else cost
            lines.append((f"Custo: {cost} Raiva  (atual: {player_rage})",
                           C_OK if player_rage >= cost else C_RAGE))

        elif sid == "interceptar":
            _cs_tt = self.world.get_component(self.player_entity, CombatStats)
            cd_ef = max(0.0, skill.cooldown - (_cs_tt.interceptar_cooldown_reduction if _cs_tt else 0.0))
            lines.append((f"Cooldown: {cd_ef:.0f}s", C_INFO))

        elif skill.cooldown > 0:
            lines.append((f"Cooldown: {skill.cooldown:.0f}s", C_INFO))

        # ── Status ───────────────────────────────────────────────────────
        if sid == "executar":
            if is_procced:
                status = ("Status: PRONTO — execute!", C_OK)
            elif target_hp_ratio <= 0.30 and target_hp_ratio > 0:
                status = ("Status: Raiva insuficiente", C_RAGE)
            elif target_hp_ratio < 1.0:
                status = ("Status: Alvo com HP alto", C_WARN)
            elif not visual_ready:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)
            else:
                status = ("Status: Aguardando alvo < 30% HP", C_WARN)

        elif skill.max_charges > 0:
            if is_procced:
                status = ("Status: Carga disponível!", C_OK)
            else:
                status = ("Status: Sem cargas  (mate um inimigo)", C_WARN)

        elif skill.rage_cost > 0:
            cost6 = skill.rage_cost
            if sid == "golpe_poderoso":
                _cs_tt6 = self.world.get_component(self.player_entity, CombatStats)
                cost6 = _cs_tt6.golpe_poderoso_rage_cost if _cs_tt6 else cost6
            if player_rage < cost6 and not (is_procced and skill.proc_ignores_cost):
                status = ("Status: Raiva insuficiente", C_RAGE)
            elif is_procced:
                status = ("Status: PRONTO — proc ativo!", C_OK)
            elif not visual_ready:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)
            else:
                status = ("Status: Pronto", C_OK)

        else:
            if visual_ready:
                status = ("Status: Pronto", C_OK)
            else:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)

        lines.append(status)
        return lines

    # ------------------------------------------------------------------
    # Tooltip
    # ------------------------------------------------------------------

    def _draw_tooltip(self, mx: int, my: int, title: str, lines: list,
                      title_color=(255, 220, 100),
                      body_font: "pygame.font.Font | None" = None) -> pygame.Rect:
        """Renderiza caixa de tooltip próxima ao cursor. Retorna o rect desenhado.
        Cada entrada de `lines` pode ser:
          (text, color)                                    -- linha simples
          ((left_text, left_color), (right_text, right_color))  -- duas colunas
        body_font: fonte para as linhas de conteúdo (default: self.font_sm).
        """
        bf        = body_font or self.font_sm
        PAD       = 8
        LINE_H    = bf.get_height() + 3
        COL_GAP   = 20   # espaço mínimo entre coluna esquerda e direita

        t_surf = self.font_md.render(title, True, title_color)

        # Pré-renderizar todas as linhas
        rendered = []
        for line in lines:
            if isinstance(line[0], tuple):
                (lt, lc), (rt, rc) = line
                rendered.append(("two", bf.render(lt, True, lc),
                                         bf.render(rt, True, rc)))
            else:
                t, c = line
                rendered.append(("one", bf.render(t, True, c)))

        def _line_w(r):
            if r[0] == "two":
                return r[1].get_width() + COL_GAP + r[2].get_width()
            return r[1].get_width()

        content_w = max(((_line_w(r)) for r in rendered), default=0)
        tw = max(t_surf.get_width(), content_w) + PAD * 2
        th = self.font_md.get_height() + len(rendered) * LINE_H + PAD * 2 + 4

        tx = mx + 14
        ty = my - th - 4
        if tx + tw > SCREEN_WIDTH:
            tx = mx - tw - 4
        ty = max(0, min(ty, SCREEN_HEIGHT - th))

        bg = pygame.Surface((tw, th), pygame.SRCALPHA)
        bg.fill((10, 8, 5, 220))
        self.screen.blit(bg, (tx, ty))
        pygame.draw.rect(self.screen, (120, 90, 50), (tx, ty, tw, th), 1, border_radius=3)
        self.screen.blit(t_surf, (tx + PAD, ty + PAD))
        for i, r in enumerate(rendered):
            y = ty + PAD + self.font_md.get_height() + 4 + i * LINE_H
            if r[0] == "two":
                self.screen.blit(r[1], (tx + PAD, y))
                self.screen.blit(r[2], (tx + tw - PAD - r[2].get_width(), y))
            else:
                self.screen.blit(r[1], (tx + PAD, y))
        return pygame.Rect(tx, ty, tw, th)

    def _flush_tooltip(self):
        if not self._pending_tooltip:
            return
        # _pending_tooltip: (mx, my, title, lines[, title_color | new_item, equipped_item])
        # Elemento 4 pode ser:
        #   - tuple de 3 ints  → cor do título (mob tooltip com cor de tier)
        #   - Item object      → item para comparação (shop/bag tooltip)
        mx, my, title, lines = self._pending_tooltip[:4]
        rest = self._pending_tooltip[4:]
        title_color = (255, 220, 100)
        if rest and isinstance(rest[0], tuple):
            title_color = rest[0]
            rest = rest[1:]
        tip_rect = self._draw_tooltip(mx, my, title, lines, title_color)
        # Shift + hover com dados de comparação → dois painéis ao lado do tooltip
        if (len(rest) >= 2
                and pygame.key.get_mods() & pygame.KMOD_SHIFT):
            new_item, eq_item = rest[0], rest[1]
            draw_compare_panel(self.screen, self.font_sm, self.font_md,
                               new_item, eq_item, tip_rect)

    def _flush_skill_tooltip(self):
        """Renderiza tooltip de habilidade com a mesma escala do tooltip de itens."""
        if not self._pending_skill_tooltip:
            return
        mx, my, title, lines = self._pending_skill_tooltip[:4]
        self._draw_tooltip(mx, my, title, lines, body_font=self.font_sm)

    def _draw_world_tooltip(self):
        """Mostra tooltip fixo no canto inferior direito ao passar o mouse sobre NPCs/mobs."""
        from components import Enemy, EnemyTier, Renderable, Merchant, QuestGiver, NPC, Visible, EntityIdentity
        mx, my = pygame.mouse.get_pos()
        z  = self._zoom
        wx = mx / z + self._cam_x
        wy = my / z + self._cam_y

        title      = None
        lines      = []
        title_col  = (255, 220, 100)

        # NPCs — qualquer entidade com componente NPC (Merchant, QuestGiver, futuro Trainer...)
        for eid, pos, rend, npc, _ in self.world.get_entities_with(
                Position, Renderable, NPC, Visible):
            hw, hh = rend.width / 2, rend.height / 2
            if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                continue
            title = npc.name
            lines = [(f"Nivel {npc.level}  {npc.profession}", (160, 160, 160))]
            # Dica de interação baseada nas capacidades do NPC
            has_shop  = self.world.get_component(eid, Merchant)  is not None
            has_quest = self.world.get_component(eid, QuestGiver) is not None
            if has_shop and has_quest:
                lines.append(("Clique direito para interagir", (220, 200, 100)))
            elif has_shop:
                lines.append(("Clique direito para abrir a loja", (150, 220, 150)))
            elif has_quest:
                lines.append(("Clique direito para interagir", (220, 200, 100)))
            break

        # Inimigos
        if title is None:
            tier_colors = {"normal": (200, 200, 200), "elite": (140, 140, 255),
                           "rare": (255, 165, 0), "boss": (220, 80, 220)}
            for eid, pos, rend, _, _ in self.world.get_entities_with(
                    Position, Renderable, Enemy, Visible):
                cs = self.world.get_component(eid, CombatStats)
                if cs and cs.current_hp <= 0:
                    continue
                hw, hh = rend.width / 2, rend.height / 2
                if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                    continue
                tier_c    = self.world.get_component(eid, EnemyTier)
                ident     = self.world.get_component(eid, EntityIdentity)
                tier      = tier_c.tier if tier_c else "normal"
                title     = ident.name  if ident else "Inimigo"
                mob_race  = ident.race  if ident else "?"
                mob_level = ident.level if ident else 1
                title_col = tier_colors.get(tier.lower(), (200, 200, 200))
                lines = [(f"Level {mob_level}  {mob_race}", (160, 160, 160))]
                if cs:
                    hp_pct = int(cs.current_hp / max(1, cs.max_hp) * 100)
                    lines.append((f"HP: {cs.current_hp}/{cs.max_hp} ({hp_pct}%)",
                                  C_GREEN if hp_pct > 50 else C_YELLOW if hp_pct > 25 else C_RED))
                break

        if title is None:
            return

        # Renderizar tooltip fixo no canto inferior direito
        PAD    = 10
        LINE_H = self.font_sm.get_height() + 3
        t_surf = self.font_md.render(title, True, title_col)
        line_surfs = [self.font_sm.render(l[0], True, l[1]) for l in lines]
        tw = max(t_surf.get_width(), *(s.get_width() for s in line_surfs)) + PAD * 2
        th = self.font_md.get_height() + len(line_surfs) * LINE_H + PAD * 2 + 4
        tx = SCREEN_WIDTH  - tw  - 12
        ty = SCREEN_HEIGHT - th  - 12
        bg = pygame.Surface((tw, th), pygame.SRCALPHA)
        bg.fill((10, 8, 5, 210))
        self.screen.blit(bg, (tx, ty))
        pygame.draw.rect(self.screen, (120, 90, 50), (tx, ty, tw, th), 1, border_radius=3)
        self.screen.blit(t_surf, (tx + PAD, ty + PAD))
        for i, s in enumerate(line_surfs):
            self.screen.blit(s, (tx + PAD, ty + PAD + self.font_md.get_height() + 4 + i * LINE_H))

    # ------------------------------------------------------------------

    def _draw_hud(self):
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        char_stats   = self.world.get_component(self.player_entity, CharacterStats)
        perm_stats   = self.world.get_component(self.player_entity, PermanentStats)
        if not combat_stats:
            return

        # --- Indicador de zoom (canto superior esquerdo, só quando ≠ 100%) ---
        if self._zoom != 1.0:
            z_pct  = int(round(self._zoom * 100))
            z_surf = self.font_xs.render(f"zoom {z_pct}%", True, C_YELLOW)
            self.screen.blit(z_surf, (10, 10))

        # --- Zona atual + coordenadas (canto superior direito) ---
        import os
        map_name = os.path.splitext(os.path.basename(self._current_map_file))[0].replace("_", " ").title()
        zone_surf = self.font_sm.render(map_name, True, C_YELLOW)
        self.screen.blit(zone_surf, (SCREEN_WIDTH - zone_surf.get_width() - 10, 10))

        tile_move = self.world.get_component(self.player_entity, TileMovement)
        if tile_move:
            map_file = os.path.basename(self._current_map_file)
            coord_txt = f"Map: {map_file} | Tile: {tile_move.current_tile_x}, {tile_move.current_tile_y}"
            coord_surf = self.font_sm.render(coord_txt, True, (180, 180, 180))
            self.screen.blit(coord_surf, (SCREEN_WIDTH - coord_surf.get_width() - 10, 28))
            if self._current_zone:
                zone_txt  = self.font_sm.render(self._current_zone, True, (160, 200, 160))
                self.screen.blit(zone_txt, (SCREEN_WIDTH - zone_txt.get_width() - 10, 46))

        y = 10  # cursor vertical

        # --- Nome do personagem ---
        if char_stats:
            name_surf = self.font_sm.render(
                f"{char_stats.name}  [{char_stats.class_id.capitalize()}]",
                True, (210, 185, 255))
            self.screen.blit(name_surf, (10, y))
            y += name_surf.get_height() + 2

        # --- HP ---
        hp_ratio = max(0, combat_stats.current_hp / max(1, combat_stats.max_hp))
        bar_w = 200
        pygame.draw.rect(self.screen, (80, 0, 0),   (10, y, bar_w, 14))
        pygame.draw.rect(self.screen, C_RED,         (10, y, int(bar_w * hp_ratio), 14))
        hp_surf = self.font_sm.render(
            f"HP {combat_stats.current_hp}/{combat_stats.max_hp}", True, C_WHITE)
        self.screen.blit(hp_surf, (14, y))
        y += 18

        # --- Rage (Guerreiro) / Mana (Mago) / Aljava (Arqueiro) ---
        if char_stats:
            if char_stats.class_id == "mago":
                mana_ratio = char_stats.mana / max(1, char_stats.max_mana)
                pygame.draw.rect(self.screen, (0, 20, 80),    (10, y, bar_w, 14))
                pygame.draw.rect(self.screen, (50, 100, 255), (10, y, int(bar_w * mana_ratio), 14))
                mana_surf = self.font_sm.render(
                    f"Mana {char_stats.mana}/{char_stats.max_mana}", True, C_WHITE)
                self.screen.blit(mana_surf, (14, y))
            elif char_stats.class_id == "arqueiro":
                # Barra de Concentração
                _conc_ratio = char_stats.concentration / max(1, char_stats.max_concentration)
                _conc_col   = (80, 160, 220) if _conc_ratio > 0.3 else (180, 100, 60)
                pygame.draw.rect(self.screen, (10, 30, 55),  (10, y, bar_w, 14))
                pygame.draw.rect(self.screen, _conc_col, (10, y, int(bar_w * _conc_ratio), 14))
                _cs_conc  = self.world.get_component(self.player_entity, CombatStats)
                _tm_conc  = self.world.get_component(self.player_entity, TileMovement)
                _moving   = _tm_conc.is_moving if _tm_conc else False
                _rate     = (getattr(_cs_conc, "concentration_regen_moving", 0.0)
                             if _moving else
                             getattr(_cs_conc, "concentration_regen_idle",   0.0))
                _rate_str = f"  (+{_rate:.0f}/s)" if _rate > 0 else ""
                conc_surf = self.font_sm.render(
                    f"Conc. {int(char_stats.concentration)}/{char_stats.max_concentration}{_rate_str}",
                    True, C_WHITE)
                self.screen.blit(conc_surf, (14, y))
                y += 18
                # Barra de Aljava
                from components import Equipment as _EqHUD
                _eq_hud = self.world.get_component(self.player_entity, _EqHUD)
                _quiver = _eq_hud.slots.get("offhand") if _eq_hud else None
                if _quiver and getattr(_quiver, "item_type", "") == "quiver":
                    _arrow_ratio = _quiver.arrow_count / max(1, _quiver.max_arrows)
                    pygame.draw.rect(self.screen, (40, 30, 10),  (10, y, bar_w, 14))
                    pygame.draw.rect(self.screen, (200, 160, 60), (10, y, int(bar_w * _arrow_ratio), 14))
                    arrow_surf = self.font_sm.render(
                        f"Aljava {_quiver.arrow_count}/{_quiver.max_arrows}", True, C_WHITE)
                    self.screen.blit(arrow_surf, (14, y))
                else:
                    no_q_surf = self.font_sm.render("Sem aljava", True, (180, 130, 50))
                    self.screen.blit(no_q_surf, (14, y))
            else:
                rage_ratio = char_stats.rage / max(1, char_stats.max_rage)
                pygame.draw.rect(self.screen, (60, 20, 0),   (10, y, bar_w, 14))
                pygame.draw.rect(self.screen, C_ORANGE,       (10, y, int(bar_w * rage_ratio), 14))
                rage_surf = self.font_sm.render(
                    f"Raiva {char_stats.rage}/{char_stats.max_rage}", True, C_WHITE)
                self.screen.blit(rage_surf, (14, y))
            y += 18

        # --- Ataque CD ---
        if combat_stats.attack_cooldown_timer > 0:
            cd_surf = self.font_sm.render(
                f"Ataque CD: {combat_stats.attack_cooldown_timer:.1f}s", True, C_YELLOW)
        else:
            cd_surf = self.font_sm.render("Ataque: Pronto", True, C_GREEN)
        self.screen.blit(cd_surf, (10, y))
        y += 18

        if not char_stats:
            return

        # --- Level e XP ---
        xp_ratio = char_stats.current_xp / max(1, char_stats.xp_to_next_level)
        pygame.draw.rect(self.screen, (0, 40, 80),  (10, y, bar_w, 10))
        pygame.draw.rect(self.screen, C_CYAN,        (10, y, int(bar_w * xp_ratio), 10))
        lv_surf = self.font_sm.render(
            f"Nv {char_stats.level}  XP {char_stats.current_xp}/{char_stats.xp_to_next_level}",
            True, (255, 255, 255))
        self.screen.blit(lv_surf, (14, y))
        y += 14

        # --- Atributos compactos ---
        s = char_stats
        p = perm_stats
        attrs = (
            f"FOR:{s.strength}(+{p.strength if p else 0})  "
            f"INT:{s.intelligence}(+{p.intelligence if p else 0})  "
            f"AGI:{s.agility}(+{p.agility if p else 0})  "
            f"VIT:{s.vitality}(+{p.vitality if p else 0})  "
            f"DEF:{s.defense}(+{p.defense if p else 0})"
        )
        attr_surf = self.font_sm.render(attrs, True, C_GRAY)
        self.screen.blit(attr_surf, (10, y))
        y += 16

        # --- Ratings de combate ---
        RPP = 20.0
        crit_pct  = combat_stats.crit_rating  * 100
        parry_pct = combat_stats.parry_rating / RPP
        dodge_pct = combat_stats.dodge_rating / RPP
        ratings = (
            f"Crit:{crit_pct:.1f}%  "
            f"Aparo:{parry_pct:.1f}%  "
            f"Esquiva:{dodge_pct:.1f}%"
        )
        rating_surf = self.font_sm.render(ratings, True, C_GRAY)
        self.screen.blit(rating_surf, (10, y))
        y += 16

        # --- Ouro ---
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            # Pequeno círculo dourado + valor
            pygame.draw.circle(self.screen, (210, 175, 30), (18, y + 7), 6)
            pygame.draw.circle(self.screen, (255, 220, 60), (18, y + 7), 5)
            gold_surf = self.font_sm.render(f"{wallet.gold}", True, (255, 215, 0))
            self.screen.blit(gold_surf, (28, y))
            y += 16

        # --- Inventário ---
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv:
            inv_surf = self.font_sm.render(
                f"Mochila: {len(inv.items)}/{inv.max_slots}  [I] itens  [T] talentos  [ESC] menu  [clic direito em cadáver p/ saquear]",
                True, C_GRAY)
            self.screen.blit(inv_surf, (10, y))
            y += 16

        # --- Talentos pendentes ---
        from components import TalentTree
        tt = self.world.get_component(self.player_entity, TalentTree)
        if tt and tt.available_points > 0:
            blink = int(pygame.time.get_ticks() / 500) % 2 == 0
            color = (180, 120, 255) if blink else (130, 80, 220)
            tal_surf = self.font_md.render(
                f"+{tt.available_points} TALENTO(S)! Pressione T para alocar",
                True, color)
            self.screen.blit(tal_surf, (10, y))

        # --- Barra de cast / canalização (centro inferior da tela) ---
        self._draw_cast_bar(char_stats)

    def _draw_cast_bar(self, char_stats: "CharacterStats | None") -> None:
        """Barra de cast/canalização — exibida no centro inferior da tela."""
        sw, sh = self.screen.get_size()
        BAR_W, BAR_H = 280, 20
        bx = sw // 2 - BAR_W // 2
        by = sh - 140  # acima da hotbar

        spell_cast = self.world.get_component(self.player_entity, SpellCast)
        channeling = self.world.get_component(self.player_entity, Channeling)
        ice_block  = self.world.get_component(self.player_entity, IceBlockEffect)

        if spell_cast:
            ratio  = min(1.0, spell_cast.elapsed / max(0.01, spell_cast.cast_time))
            label  = f"Lançando... {spell_cast.elapsed:.1f}/{spell_cast.cast_time:.1f}s"
            bar_col = (255, 160, 60)
            bg_col  = (60, 30, 0)
        elif channeling:
            remaining = max(0.0, channeling.duration - channeling.elapsed)
            ratio  = remaining / max(0.01, channeling.duration)
            label  = f"Canalizando... {remaining:.1f}s"
            bar_col = (255, 100, 20)
            bg_col  = (50, 20, 0)
        else:
            return

        # Fundo
        bg_r = pygame.Rect(bx - 2, by - 2, BAR_W + 4, BAR_H + 4)
        pygame.draw.rect(self.screen, (0, 0, 0), bg_r, border_radius=4)
        pygame.draw.rect(self.screen, bg_col, (bx, by, BAR_W, BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, bar_col, (bx, by, int(BAR_W * ratio), BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, (200, 200, 200), (bx, by, BAR_W, BAR_H), 1, border_radius=3)
        txt = self.font_sm.render(label, True, (255, 255, 255))
        self.screen.blit(txt, (bx + BAR_W // 2 - txt.get_width() // 2,
                               by + BAR_H // 2 - txt.get_height() // 2))

    # ------------------------------------------------------------------
    # Painel de equipamentos + inventário (tecla I)
    # ------------------------------------------------------------------

    _RARITY_COLORS = {
        "common":   (200, 200, 200),
        "uncommon": ( 30, 200,  30),
        "rare":     ( 80, 140, 255),
        "epic":     (180,  50, 255),
    }

    # Constantes do painel de inventário (usadas por draw E click)
    _PANEL_W    = 720
    _PANEL_H    = 660
    _PAD        = 10
    _HEADER_H   = 28
    _EQ_W       = 230   # largura da coluna de equipamento
    _EQ_SLOT_H  = 36    # altura de cada slot equipado
    _EQ_ICON    = 28    # ícone dentro do slot de equip
    _BODY_H     = 360   # 10 slots × 36px
    _INV_SLOT   = 60    # tamanho do slot de inventário (quadrado)
    _INV_COLS   = 5     # colunas na grade de inventário
    _INV_GAP    = 4     # espaço entre slots
    _STATS_H    = 110   # seção de estatísticas

    # Constantes da hotbar
    _SLOT_W     = 64
    _SLOT_H     = 64
    _SLOT_PAD   = 6

    def _panel_origin(self):
        x0 = SCREEN_WIDTH  // 2 - self._PANEL_W // 2
        y0 = SCREEN_HEIGHT // 2 - self._PANEL_H // 2
        return x0, y0

    # ------------------------------------------------------------------ #
    #  Equip / Unequip
    # ------------------------------------------------------------------ #

    def _equip_item(self, item):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        target_slot = item.slot
        if target_slot not in equip.slots:
            return

        # Restrição de armor_class por classe do personagem
        if getattr(item, "armor_class", "") and item.item_type == "armor":
            from stats_system import CLASS_ARMOR_ALLOWED
            char = self.world.get_component(self.player_entity, CharacterStats)
            allowed = CLASS_ARMOR_ALLOWED.get(char.class_id if char else "", frozenset())
            if item.armor_class not in allowed:
                _names = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}
                LOG.add(f"Sua classe não pode usar armadura de {_names.get(item.armor_class, item.armor_class)}.", (255, 100, 80))
                return

        # Arma de duas mãos → desequipa offhand se houver
        if getattr(item, 'two_handed', False) and target_slot == "mainhand":
            old_oh = equip.slots.get("offhand")
            if old_oh:
                for mod in old_oh.modifiers:
                    remove_modifier(combat_stats, mod)
                inv.items.append(old_oh)
                equip.slots["offhand"] = None

        # Offhand bloqueado por arma de duas mãos
        if target_slot == "offhand" and equip.is_offhand_locked():
            return

        # Troca com item já equipado
        old_item = equip.slots[target_slot]
        if old_item:
            for mod in old_item.modifiers:
                remove_modifier(combat_stats, mod)
            inv.items.append(old_item)

        # Equipa o novo item
        equip.slots[target_slot] = item
        inv.items.remove(item)
        if target_slot == "mainhand" and getattr(item, "attack_speed", 0.0) > 0:
            combat_stats.base_attack_interval = item.attack_speed
        for mod in item.modifiers:
            add_modifier(combat_stats, mod)

    def _unequip_slot(self, slot_name: str):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        item = equip.slots.get(slot_name)
        if item is None:
            return
        if len(inv.items) >= inv.max_slots:
            return  # inventário cheio

        if slot_name == "mainhand":
            combat_stats.base_attack_interval = combat_stats._default_attack_interval
        for mod in item.modifiers:
            remove_modifier(combat_stats, mod)
        equip.slots[slot_name] = None
        inv.items.append(item)

    # ------------------------------------------------------------------ #
    #  Click handling
    # ------------------------------------------------------------------ #

    def _handle_inventory_click(self, event):
        if event.button not in (1, 3):
            return
        mx, my = event.pos
        x0, y0 = self._panel_origin()
        body_y  = y0 + self._PAD + self._HEADER_H

        # --- Botão fechar ---
        if event.button == 1:
            close_r = pygame.Rect(x0 + self._PANEL_W - 36, y0 + 4, 32, 32)
            if close_r.collidepoint(mx, my):
                self._show_inventory = False
                self._selected_inv_idx = -1
                return

        # --- Grade de inventário (esquerdo = selecionar, direito = equipar) ---
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv:
            gx = x0 + self._EQ_W + self._PAD * 3
            step = self._INV_SLOT + self._INV_GAP
            for i, item in enumerate(inv.items):
                col = i % self._INV_COLS
                row = i // self._INV_COLS
                r = pygame.Rect(gx + col * step, body_y + row * step,
                                self._INV_SLOT, self._INV_SLOT)
                if r.collidepoint(mx, my):
                    if event.button == 3:
                        if getattr(item, "consumable", None):
                            self._use_consumable(item, i, inv)
                        else:
                            self._equip_item(item)
                        self._selected_inv_idx = -1
                    else:
                        self._selected_inv_idx = i if self._selected_inv_idx != i else -1
                    return
            # Clique fora dos slots da grade deseleciona
            if event.button == 1:
                self._selected_inv_idx = -1

        # --- Coluna de equipamentos (qualquer clique = desequipar) ---
        equip = self.world.get_component(self.player_entity, Equipment)
        if equip:
            for i, slot_name in enumerate(Equipment.SLOT_LABELS):
                r = pygame.Rect(x0 + self._PAD,
                                body_y + i * self._EQ_SLOT_H,
                                self._EQ_W - 2, self._EQ_SLOT_H - 2)
                if r.collidepoint(mx, my) and equip.slots[slot_name] is not None:
                    self._unequip_slot(slot_name)
                    return

    def _use_consumable(self, item, idx: int, inv) -> None:
        """Usa um item consumível do inventário."""
        from components import CombatStats, CombatState, ActiveRegen
        from combat_log import LOG
        from floating_text import FLT

        cs     = self.world.get_component(self.player_entity, CombatStats)
        state  = self.world.get_component(self.player_entity, CombatState)
        pos_c  = self.world.get_component(self.player_entity, Position)
        if not cs:
            return

        c = item.consumable

        # Receita: aprende e remove da bag imediatamente
        if "learn_recipe" in c:
            from components import LearnedRecipes
            lr = self.world.get_component(self.player_entity, LearnedRecipes)
            if lr:
                recipe_id = c["learn_recipe"]
                if learn_recipe(lr, recipe_id):
                    from crafting_data import RECIPES
                    rname = RECIPES.get(recipe_id, {}).get("name", recipe_id)
                    LOG.add(f"Receita aprendida: {rname}!", (220, 180, 50))
                    SOUNDS.play_ui("levelup")
                else:
                    LOG.add("Voce ja conhece essa receita.", (160, 140, 80))
            item.stack -= 1
            if item.stack <= 0:
                inv.items.pop(idx)
            return

        ooc_only = c.get("ooc_only", False)
        if ooc_only and state and state.in_combat:
            LOG.add("Não pode usar comida em combate!", (220, 100, 60))
            return

        # Cura instantânea
        heal_now = c.get("heal_instant", 0)
        if heal_now:
            actual = min(heal_now, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_now)
            if pos_c and actual > 0:
                FLT.add(f"+{actual}", pos_c.x, pos_c.y - 16, (80, 220, 120), "normal",
                        self.player_entity)
            LOG.add(f"Usou {item.name}: +{actual} HP", (80, 220, 120))

        # Regeneração ao longo do tempo
        ticks = c.get("ticks", 0)
        if ticks and c.get("heal_per_tick", 0):
            # Sobrescreve regen ativa existente
            existing = self.world.get_component(self.player_entity, ActiveRegen)
            if existing:
                self.world.remove_component(self.player_entity, ActiveRegen)
            self.world.add_component(
                self.player_entity,
                ActiveRegen(c["heal_per_tick"], c["interval"], ticks),
            )
            total = c["heal_per_tick"] * ticks
            LOG.add(f"Usou {item.name}: recupera {total} HP ao longo do tempo", (80, 220, 120))

        # Decrementa stack; remove o slot apenas quando esgotado
        item.stack -= 1
        if item.stack <= 0:
            inv.items.pop(idx)

    # ------------------------------------------------------------------ #
    #  Draw
    # ------------------------------------------------------------------ #

    def _draw_inventory_panel(self):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        x0, y0  = self._panel_origin()
        W, H    = self._PANEL_W, self._PANEL_H
        PAD     = self._PAD
        mx, my  = pygame.mouse.get_pos()

        # ---- Fundo ----
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((15, 10, 5, 220))
        self.screen.blit(overlay, (x0, y0))
        pygame.draw.rect(self.screen, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        title = self.font_md.render("Equipamentos", True, (200, 170, 100))
        self.screen.blit(title, (x0 + PAD, y0 + 4))

        # Botão X (fechar)
        close_r = pygame.Rect(x0 + W - 36, y0 + 4, 32, 32)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))

        body_y    = y0 + PAD + self._HEADER_H
        divider_y = body_y + self._BODY_H + PAD
        col_x     = x0 + self._EQ_W + PAD * 3

        pygame.draw.line(self.screen, (90, 70, 40), (x0 + PAD, divider_y), (x0 + W - PAD, divider_y))
        pygame.draw.line(self.screen, (90, 70, 40), (col_x - PAD, y0 + PAD), (col_x - PAD, divider_y))

        # ---- Cabeçalhos ----
        hdr = (160, 130, 80)
        self.screen.blit(self.font_sm.render("Equipado  (clique p/ desequipar)", True, hdr), (x0 + PAD, y0 + PAD + 2))
        bag_hint = "  [DEL] deletar selecionado" if self._selected_inv_idx >= 0 else "  clique esq. p/ selecionar | dir. p/ equipar"
        self.screen.blit(self.font_sm.render(f"Mochila ({len(inv.items)}/{inv.max_slots}){bag_hint}", True, hdr), (col_x, y0 + PAD + 2))

        # ---- Coluna de equipamentos (ícone + label + nome) ----
        for i, (slot_name, label) in enumerate(Equipment.SLOT_LABELS.items()):
            ry     = body_y + i * self._EQ_SLOT_H
            r      = pygame.Rect(x0 + PAD, ry, self._EQ_W - 2, self._EQ_SLOT_H - 2)
            item   = equip.slots[slot_name]
            locked = (slot_name == "offhand" and equip.is_offhand_locked())

            hovered = r.collidepoint(mx, my)
            bg     = (50, 20, 20) if locked else ((55, 42, 18) if hovered else (32, 22, 12))
            border = (100, 40, 40) if locked else ((180, 140, 60) if hovered else (70, 50, 30))
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            pygame.draw.rect(self.screen, border, r, 1, border_radius=3)

            # ícone (quadrado _EQ_ICON × _EQ_ICON)
            ic = self._EQ_ICON
            icon_r = pygame.Rect(r.x + 3, r.y + (self._EQ_SLOT_H - 2 - ic) // 2, ic, ic)
            if item:
                icon_surf = ICONS.get(ICONS.item_key(item), ic)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb_color = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb_color, icon_r, border_radius=2)
            else:
                pygame.draw.rect(self.screen, (45, 35, 20) if not locked else (60, 20, 20), icon_r, border_radius=2)
                pygame.draw.rect(self.screen, border, icon_r, 1, border_radius=2)

            # Label do slot
            lbl_surf = self.font_sm.render(f"{label}", True, (120, 100, 70))
            self.screen.blit(lbl_surf, (icon_r.right + 4, r.y + 3))

            # Nome do item (linha 2)
            if item:
                col_name = self._RARITY_COLORS.get(item.rarity, (200, 200, 200))
                self.screen.blit(self.font_sm.render(item.name, True, col_name), (icon_r.right + 4, r.y + 18))
            elif locked:
                self.screen.blit(self.font_sm.render("(2 maos)", True, (100, 60, 60)), (icon_r.right + 4, r.y + 18))

            # Tooltip no hover
            if hovered and item:
                lines = item_tooltip_lines(item)
                lines.append(("Clique p/ desequipar", (140, 140, 140)))
                self._pending_tooltip = (mx, my, item.name, lines)

        # ---- Grade de inventário (ícones) ----
        step  = self._INV_SLOT + self._INV_GAP
        total = inv.max_slots
        for i in range(total):
            col_i = i % self._INV_COLS
            row_i = i // self._INV_COLS
            sx = col_x + col_i * step
            sy = body_y + row_i * step
            r  = pygame.Rect(sx, sy, self._INV_SLOT, self._INV_SLOT)
            item = inv.items[i] if i < len(inv.items) else None

            hovered  = r.collidepoint(mx, my)
            selected = (i == self._selected_inv_idx)
            bg     = (55, 42, 18) if hovered else (30, 22, 12)
            if selected:
                border = (220, 60, 60)
            elif hovered and item:
                border = (180, 140, 60)
            else:
                border = (65, 48, 28)
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            pygame.draw.rect(self.screen, border, r, 2 if selected else 1, border_radius=3)

            if item:
                ic        = self._INV_SLOT - 8
                icon_r    = pygame.Rect(sx + 4, sy + 4, ic, ic)
                icon_surf = ICONS.get(ICONS.item_key(item), ic)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb, icon_r, border_radius=2)
                # Ponto de raridade (canto inferior direito) — só em não-empilháveis
                if getattr(item, "max_stack", 1) <= 1:
                    dot_col = self._RARITY_COLORS.get(item.rarity, (150, 150, 150))
                    pygame.draw.circle(self.screen, dot_col, (r.right - 5, r.bottom - 5), 4)

                # Contador de stack (canto inferior direito)
                draw_stack_count(self.screen, item, r, self.font_sm)

                if hovered:
                    del_hint = "DEL p/ deletar | " if selected else ""
                    lines = item_tooltip_lines(item)
                    is_consumable = getattr(item, "consumable", None)
                    if is_consumable:
                        lines.append((f"{del_hint}Clique dir. p/ usar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines)
                    else:
                        lines.append((f"{del_hint}Clique dir. p/ equipar | Shift p/ comparar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines,
                                                 item, equip.slots.get(item.slot))

        # ---- Seção de estatísticas (2 colunas) ----
        sy2   = divider_y + PAD
        half  = W // 2
        cL    = x0 + PAD          # coluna esquerda: rótulo
        cLv   = x0 + 130          # coluna esquerda: valor
        cR    = x0 + half + PAD   # coluna direita: rótulo
        cRv   = x0 + half + 130   # coluna direita: valor
        HDR   = (160, 140, 100)
        VAL   = (255, 220, 120)
        ROW   = 20                 # altura de linha

        self.screen.blit(self.font_sm.render("── Estatísticas ──", True, (180, 150, 90)), (x0 + PAD, sy2))
        sy2 += ROW

        char_stats = self.world.get_component(self.player_entity, CharacterStats)

        def sv(label, value, col_lbl, col_val, y, color=VAL):
            self.screen.blit(self.font_sm.render(label + ":", True, HDR), (col_lbl, y))
            self.screen.blit(self.font_sm.render(value,        True, color), (col_val, y))

        # Linha 1
        sv("HP",        f"{int(combat_stats.current_hp)}/{combat_stats.max_hp}", cL, cLv, sy2)
        sv("Acerto",    f"{combat_stats.acerto:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 2
        sv("Atq. Físico", f"{int(combat_stats.attack_power)}", cL, cLv, sy2)
        sv("Esquiva",   f"{combat_stats.dodge_rating / 20:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 3
        sv("Atq. Mágico", f"{int(combat_stats.spell_power)}", cL, cLv, sy2)
        sv("Aparo",     f"{combat_stats.parry_rating / 20:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 4
        sv("Armadura",  f"{int(combat_stats.armor)}", cL, cLv, sy2)
        sv("Vel. Ataque", f"{combat_stats.attack_interval:.2f}s", cR, cRv, sy2)
        sy2 += ROW

        # Linha 5
        sv("Estamina",  f"{int(combat_stats.stamina)}", cL, cLv, sy2)
        sv("Crítico",   f"{combat_stats.crit_rating * 100:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 6 — recurso da classe
        if char_stats:
            if char_stats.max_mana > 0:
                sv("Mana",  f"{int(char_stats.mana)}/{char_stats.max_mana}", cL, cLv, sy2)
            elif char_stats.max_concentration > 0:
                sv("Concentração", f"{int(char_stats.concentration)}/{char_stats.max_concentration}", cL, cLv, sy2)
            else:
                sv("Raiva", f"{char_stats.rage}/{char_stats.max_rage}", cL, cLv, sy2)
        sy2 += ROW

        # ---- Rodapé: moedas (sempre no rodapé do painel) ----
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            footer_y = y0 + H - 34
            pygame.draw.line(self.screen, (90, 70, 40),
                             (x0 + PAD, footer_y - 4), (x0 + W - PAD, footer_y - 4))
            coin_x, coin_y = x0 + PAD + 10, footer_y + 12
            pygame.draw.circle(self.screen, (180, 140, 0), (coin_x, coin_y), 9)
            pygame.draw.circle(self.screen, (255, 215, 0), (coin_x, coin_y), 7)
            pygame.draw.circle(self.screen, (120, 90, 0),  (coin_x, coin_y), 9, 1)
            g_surf = self.font_sm.render("G", True, (120, 90, 0))
            self.screen.blit(g_surf, (coin_x - g_surf.get_width() // 2,
                                      coin_y - g_surf.get_height() // 2))
            gold_surf = self.font_md.render(f"{wallet.gold} moedas", True, (255, 215, 0))
            self.screen.blit(gold_surf, (x0 + PAD + 24, footer_y + 6))
