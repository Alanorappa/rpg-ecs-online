# game.py
import pygame
import math
import time as _time

from world import World
from components import Position, Tilemap, CombatStats, CharacterStats, PermanentStats, \
                       TileMovement, PlayerAutoMove, CombatState
from systems import (
    PlayerInputSystem, TileMovementSystem, RenderSystem, CameraSystem,
    EnemyAISystem, TileRenderSystem, TileValidationSystem,
    PathfindingSystem, CombatSystem, CombatStateSystem, MouseTargetingSystem,
    ProjectileSystem, CorpseSystem, MobRespawnSystem, LootSystem, ShopSystem, SkillSystem,
    SpawnZoneSystem, ConsumableSystem, DeathHandlerSystem, FogSystem,
)
from stats_system import XPSystem, DeathRespawnSystem
from entity_factory import create_player, create_camera, create_enemy, create_tilemap, create_merchant, create_spawn_zone
from components import Inventory, Equipment, PlayerSkills, Wallet
from map_loader import load_map_csv, validate_map
from tileset import TILE_SIZE
from combat_log import LOG
from floating_text import FLT, DASH_TRAIL, WARN
from icon_manager import ICONS
from sound_manager import SOUNDS
from ui_compare import draw_compare_panel
from talent_system import TalentSystem
from ui_helpers import item_tooltip_lines, RARITY_COLORS as _ITEM_RARITY_COLORS
from map_overlay import MapOverlay
from save_system import save_game, load_game, has_save

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


class GameEngine:
    def __init__(self, scale: float = 1.0):
        pygame.init()
        self._scale   = scale
        win_w = int(SCREEN_WIDTH  * scale)
        win_h = int(SCREEN_HEIGHT * scale)
        self._display = pygame.display.set_mode((win_w, win_h))
        # Surface interna sempre em resolução base — todos os sistemas renderizam aqui
        self.screen   = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("RPG ECS")
        self.clock = pygame.time.Clock()

        # Monkey-patch: pygame.mouse.get_pos() devolve coordenadas no espaço interno (1280x720)
        if scale != 1.0:
            _orig_get_pos = pygame.mouse.get_pos
            _s = scale
            pygame.mouse.get_pos = lambda: (
                int(_orig_get_pos()[0] / _s),
                int(_orig_get_pos()[1] / _s),
            )
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

        self.font_xs = pygame.font.Font(None, 18)
        self.font_sm = pygame.font.Font(None, 22)
        self.font_md = pygame.font.Font(None, 28)
        self.font_lg = pygame.font.Font(None, 36)

        self.world = World()
        self.systems = []
        self._show_inventory  = False
        self._show_talents    = False
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
        self._cam_x = 0.0             # câmera atual (para world tooltip)
        self._cam_y = 0.0
        self._pending_tooltip       = None  # (mx, my, title, lines) – render no fim do frame
        self._pending_skill_tooltip = None  # igual, mas usa font_xs nas linhas de descrição

        self._show_pause        = False
        self._pause_submenu     = ""
        self._sound_drag        = ""
        self._show_hotbar_editor = False
        self._hbe_drag_from: tuple | None = None  # ("panel", skill_id) | ("slot", idx)
        self._hbe_rebind_slot: int | None = None
        self._hbe_cons_drag_from: tuple | None = None  # ("panel", item_name) | ("slot", idx)
        self._hbe_cons_rebind_slot: int | None = None
        self._orig_mouse_pos  = pygame.mouse.get_pos
        # Zonas de ambient
        self._ambient_zones:    list = []   # [{name, ambient, rect:(x1,y1,x2,y2)}]
        self._default_ambient:  str  = ""   # ambient padrão do mapa
        self._current_zone:     str  = ""   # nome da zona onde o jogador está

        self._map_overlay   = MapOverlay(self.screen)
        self._loading_save  = False

        self._load_map_and_entities()
        self._init_systems()
        self._talent_system = TalentSystem(self.world, self.player_entity, self.screen)
        self._shop_system   = ShopSystem(self.world, self.player_entity, self.screen)

        self._apply_save()
        self._apply_hotbar_config()

        # Registra autosave global — sistemas usam request_autosave() de save_system.py
        from save_system import register_autosave
        register_autosave(self._autosave)

        # --- Profiler de frames ---
        self._prof_accum:  dict[str, float] = {}   # tempo acumulado por seção
        self._prof_peak:   dict[str, float] = {}   # pico por seção
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
        tile_matrix, spawn_points = load_map_csv(map_file)
        self._map_overlay.load_map(tile_matrix, map_file)
        for w in validate_map(tile_matrix):
            print(f"[MAPA] Aviso: {w}")

        self.tilemap_entity = create_tilemap(self.world, tile_matrix)
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

        for col, row, shop_id in spawn_points.get("merchants", []):
            create_merchant(self.world, col, row, shop_id=shop_id)

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

        death_handler         = DeathHandlerSystem(self.world)
        xp_system             = XPSystem(self.world, death_handler)
        skill_system          = SkillSystem(self.world, self.player_entity, combat, tile_validation, self.screen)
        loot_system           = LootSystem(self.world, self.screen)
        projectile_system     = ProjectileSystem(self.world, combat, self.screen)
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

        # Ordem dos sistemas por frame — ALTERE COM CUIDADO.
        # As restrições de dependência são verificadas em runtime por _validate_system_order().
        # Se a ordem for violada, o jogo levanta RuntimeError na inicialização.
        self.systems = [
            tile_validation,                                                          # 1
            MouseTargetingSystem(self.world, self.player_entity, self.screen),        # 2
            loot_system,                                                              # 3
            PlayerInputSystem(self.world, tile_validation, combat, pathfinding, self.screen),  # 4
            skill_system,                                                             # 5
            EnemyAISystem(self.world, self.player_entity, tile_validation, pathfinding, combat),  # 6
            projectile_system,                                                        # 7
            death_handler,                                                            # 8
            CorpseSystem(self.world),                                                 # 9
            SpawnZoneSystem(self.world),                                              # 10
            MobRespawnSystem(self.world, death_handler),                              # 11
            xp_system,                                                                # 12
            death_respawn_system,                                                     # 13
            ConsumableSystem(self.world),                                             # 14
            CombatStateSystem(self.world),                                            # 15
            TileMovementSystem(self.world),                                           # 16
            FogSystem(self.world),                                                    # 17
            CameraSystem(self.world),                                                 # 18
            tile_render_system,                                                       # 19
            render_system,                                                            # 20
        ]
        self._validate_system_order()

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def _autosave(self) -> None:
        """Salva o estado atual se não estiver no meio de um carregamento."""
        if not self._loading_save:
            save_game(self.world, self.player_entity, self._current_map_file)

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
        (DeathHandlerSystem,    MobRespawnSystem,     "MobRespawn lê pending_respawns de DeathHandler"),
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

    def _apply_save(self):
        """Carrega save.json e reposiciona o jogador no mapa salvo (se houver save)."""
        if not has_save():
            return

        self._loading_save = True
        pos_data = load_game(self.world, self.player_entity)
        if pos_data is None:
            self._loading_save = False
            return

        # Recalcula atributos com talentos e stats permanentes
        self._talent_system.apply_talent_effects()
        char = self.world.get_component(self.player_entity, CharacterStats)
        cs   = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        if char and cs:
            from stats_system import apply_char_stats_to_combat
            apply_char_stats_to_combat(char, cs, perm)
            # Restaura HP salvo (proporcional ao max_hp recalculado)
            if hasattr(cs, "_saved_hp") and cs._saved_hp is not None:
                cs.current_hp = min(float(cs._saved_hp), cs.max_hp)
                del cs._saved_hp
            if hasattr(cs, "_saved_max"):
                del cs._saved_max

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
        self._map_overlay.is_open = False
        self._show_pause         = False
        self._pause_submenu      = ""
        self._sound_drag         = ""
        self._show_hotbar_editor = False
        self._hbe_drag_from      = None
        self._hbe_rebind_slot    = None

    # Game loop
    # ------------------------------------------------------------------

    def _scale_events(self, events: list) -> list:
        """Reescreve pos/rel dos eventos de mouse para o espaço interno (1280x720)."""
        if self._scale == 1.0:
            return events
        # Colapsa todos os MOUSEMOTION em um único evento (o último) por frame
        last_motion = None
        for e in events:
            if e.type == pygame.MOUSEMOTION:
                last_motion = e

        scaled = []
        s = self._scale
        for e in events:
            if e.type == pygame.MOUSEMOTION:
                if e is not last_motion:
                    continue  # descarta motion intermediários
                d = dict(e.__dict__)
                d["pos"] = (int(e.pos[0] / s), int(e.pos[1] / s))
                d["rel"] = (int(e.rel[0] / s), int(e.rel[1] / s))
                scaled.append(pygame.event.Event(e.type, d))
            elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                d = dict(e.__dict__)
                d["pos"] = (int(e.pos[0] / s), int(e.pos[1] / s))
                scaled.append(pygame.event.Event(e.type, d))
            else:
                scaled.append(e)
        return scaled

    # ------------------------------------------------------------------
    # Helpers do profiler
    # ------------------------------------------------------------------

    def _prof_record(self, label: str, elapsed: float) -> None:
        """Acumula tempo (segundos) de uma seção no profiler."""
        self._prof_accum[label] = self._prof_accum.get(label, 0.0) + elapsed
        if elapsed > self._prof_peak.get(label, 0.0):
            self._prof_peak[label] = elapsed

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
        self._prof_peak   = {}
        self._prof_frames = 0

    def run(self):
        running = True
        while running:
            dt = self.clock.tick(FPS) / 1000.0
            self._dt = dt
            SOUNDS.new_frame()  # limpa deduplicação de sons
            _t0 = _time.perf_counter()
            events = self._scale_events(pygame.event.get())
            if PROFILE_FRAMES:
                self._prof_record("events", _time.perf_counter() - _t0)

            for event in events:
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self._map_overlay.is_open:
                            SOUNDS.play_ui("map_close")
                            self._map_overlay.is_open = False
                        elif self._loot_system.open_corpse_id != -1:
                            self._loot_system._close_modal()
                        elif self._shop_system.is_open:
                            self._shop_system._close()
                        elif self._show_debug:
                            self._show_debug = False
                        elif self._show_talents:
                            SOUNDS.play_ui("talent_close")
                            self._show_talents = False
                        elif self._show_inventory:
                            SOUNDS.play_ui("inventory_close")
                            self._show_inventory = False
                            self._selected_inv_idx = -1
                        elif self._show_pause:
                            if self._pause_submenu:
                                self._pause_submenu = ""
                                self._sound_drag = ""
                            else:
                                self._show_pause = False
                        else:
                            self._show_pause = True
                    elif event.key == pygame.K_m:
                        already_open = self._map_overlay.is_open
                        self._close_all_modals()
                        if not already_open:
                            player_tm = self.world.get_component(
                                self.player_entity, TileMovement)
                            tx = player_tm.current_tile_x if player_tm else -1
                            ty = player_tm.current_tile_y if player_tm else -1
                            self._map_overlay.toggle(tx, ty)
                            SOUNDS.play_ui("map_open")
                    elif event.key == pygame.K_i:
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
                    elif event.key == pygame.K_t:
                        already_open = self._show_talents
                        self._close_all_modals()
                        if not already_open:
                            self._show_talents = True
                            SOUNDS.play_ui("talent_open")
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

            # Shop recebe eventos brutos (para detectar clique em NPC no mundo)
            self._shop_system.update(events, dt)
            if self._shop_system.is_open:
                self._shop_system.handle_events(events)

            # Bloqueia sistemas enquanto um painel estiver aberto ou clique do mapa pendente
            systems_events = events
            if self._show_hotbar_editor:
                # Editor aberto: sistemas não recebem nenhum evento de input
                systems_events = []
            elif (self._map_overlay.is_open or self._map_overlay.pending_destination is not None
                    or self._show_inventory or self._show_talents
                    or (self._show_debug and DEBUG_MODE)
                    or self._shop_system.is_open
                    or self._shop_system._right_click_consumed
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
            cam_x = camera_pos.x - SCREEN_WIDTH  / 2 if camera_pos else 0
            cam_y = camera_pos.y - SCREEN_HEIGHT / 2 if camera_pos else 0
            self._cam_x, self._cam_y = cam_x, cam_y

            self.screen.fill((0, 0, 0))

            # Tiles e sistemas sem render relevante
            for system in self.systems:
                if system is not self._projectile_system \
                        and system is not self._loot_system \
                        and system is not self._render_system:
                    if PROFILE_FRAMES:
                        _ts = _time.perf_counter()
                        system.render(cam_x, cam_y)
                        self._prof_record(f"rnd:{type(system).__name__}", _time.perf_counter() - _ts)
                    else:
                        system.render(cam_x, cam_y)
            # Cadáveres: sobre os tiles, sob as entidades vivas
            self._loot_system.render_world(cam_x, cam_y)
            # Rastro do dash (Interceptar): sob as entidades
            DASH_TRAIL.render(self.screen, cam_x, cam_y)
            # Entidades + tile-objetos (árvores, pedras, etc.) em Y-sort
            _world_objs = self._tile_render_system.get_world_objects(cam_x, cam_y)
            self._render_system.render(cam_x, cam_y, world_objects=_world_objs)
            # Indicadores de loja: sobre entidades
            self._shop_system.render_world(cam_x, cam_y)
            # Projéteis: sobre tudo no mundo
            self._projectile_system.render(cam_x, cam_y)
            # Textos flutuantes de dano: sobre projéteis, sob HUD
            FLT.render(self.screen, cam_x, cam_y)
            # Avisos de ação bloqueada: posição fixa, abaixo do centro
            WARN.render(self.screen)
            # Vinheta vermelha pulsante quando HP < 30%
            self._draw_low_hp_vignette()

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
                from components import FogOfWar as _FogOfWar
                _fog_comp = self.world.get_component(self.player_entity, _FogOfWar)
                self._map_overlay.render(tx, ty,
                                         explored=_fog_comp.explored if _fog_comp else None)

            # Menu de pausa (por cima de tudo)
            if self._show_pause:
                action = self._draw_pause_menu(events)
                if action == "quit":
                    running = False
                elif action == "resume":
                    self._show_pause    = False
                    self._pause_submenu = ""
                elif action == "open_hotbar_editor":
                    self._show_pause         = False
                    self._pause_submenu      = ""
                    self._show_hotbar_editor = True
                elif action and action.startswith("resolution:"):
                    self._apply_scale(float(action.split(":")[1]))

            # Escala surface interna → janela de exibição
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            if self._scale == 1.0:
                self._display.blit(self.screen, (0, 0))
            else:
                pygame.transform.scale(self.screen, self._display.get_size(), self._display)
            pygame.display.flip()
            if PROFILE_FRAMES:
                self._prof_record("display_flip", _time.perf_counter() - _ts)
                self._prof_frames += 1
                if self._prof_frames >= self._prof_interval:
                    self._prof_report()

        self._autosave()
        pygame.quit()

    # ------------------------------------------------------------------
    # Transição de mapa (cavernas / retorno)
    # ------------------------------------------------------------------

    def _do_transition(self, trans: dict):
        target_file = trans["target_map"]
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
        self._show_inventory = False

        # Carrega novo mapa
        tile_matrix, spawn_points = load_map_csv(target_file)
        self._current_map_file = target_file
        # Contexto acústico e ambiente
        if "cave" in target_file:
            SOUNDS.set_context("cave")
            SOUNDS.play_ambient("ambient_cave")
        else:
            SOUNDS.set_context("surface")
            SOUNDS.play_ambient("ambient_surface")
        self._map_overlay.load_map(tile_matrix, target_file)
        self._map_overlay.set_active_map(target_file)
        self.tilemap_entity = create_tilemap(self.world, tile_matrix)
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
        from components import TalentTree
        from stats_system import apply_char_stats_to_combat
        cs   = self.world.get_component(self.player_entity, CharacterStats)
        comb = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        tt   = self.world.get_component(self.player_entity, TalentTree)
        if not cs or not comb:
            return
        for _ in range(n):
            cs.level += 1
            cs.xp_to_next_level = CharacterStats.xp_for_level(cs.level)
            cs.vitality     += 1
            cs.strength     += 1
            cs.agility      += 1
            cs.intelligence += 1
            cs.defense      += 2
            if tt is not None:
                tt.available_points += 1
        apply_char_stats_to_combat(cs, comb, perm)
        LOG.add(f"[DEBUG] Nivel {cs.level} — {tt.available_points if tt else 0} pontos de talento.", (120, 200, 255))

    def _handle_debug_click(self, event) -> None:
        PW, PH = 600, 480
        px = SCREEN_WIDTH  // 2 - PW // 2
        py = SCREEN_HEIGHT // 2 - PH // 2
        mx, my = event.pos

        # Botão fechar (X) — botão esquerdo
        if event.button == 1:
            close_r = pygame.Rect(px + PW - 34, py + 6, 28, 28)
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
        tile_matrix, _ = load_map_csv(map_file)
        player_tm = self.world.get_component(self.player_entity, TileMovement)
        ptx = player_tm.current_tile_x if player_tm else 0
        pty = player_tm.current_tile_y if player_tm else 0
        self._map_overlay.load_map(tile_matrix, map_file)
        self._map_overlay.set_active_map(map_file)
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

        PW, PH = 600, 480
        px = SCREEN_WIDTH  // 2 - PW // 2
        py = SCREEN_HEIGHT // 2 - PH // 2
        mx, my = pygame.mouse.get_pos()

        font_md = pygame.font.Font(None, 22)
        font_sm = pygame.font.Font(None, 18)
        font_lg = pygame.font.Font(None, 28)

        # Fundo
        bg = pygame.Surface((PW, PH), pygame.SRCALPHA)
        bg.fill((10, 8, 5, 238))
        self.screen.blit(bg, (px, py))
        pygame.draw.rect(self.screen, (100, 80, 50), (px, py, PW, PH), 2)

        # Título
        title = font_lg.render("DEBUG  [F12]", True, (120, 200, 255))
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + 9))

        # Botão fechar
        close_r = pygame.Rect(px + PW - 34, py + 6, 28, 28)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (80, 30, 30),
                         close_r, border_radius=3)
        xs = font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        pygame.draw.line(self.screen, (80, 65, 40), (px + 4, py + 40), (px + PW - 4, py + 40))

        # --- Abas ---
        TAB_DEFS = [("nivel", "Nivel"), ("itens", "Itens"), ("ouro", "Ouro"), ("mapa", "Mapa")]
        tab_w, tab_h, tab_gap = 120, 30, 8
        tabs_total_w = len(TAB_DEFS) * tab_w + (len(TAB_DEFS) - 1) * tab_gap
        tx0 = px + PW // 2 - tabs_total_w // 2
        ty0 = py + 46

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

        pygame.draw.line(self.screen, (80, 65, 40), (px + 4, py + 82), (px + PW - 4, py + 82))

        content_y = py + 90

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
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, py + PH - 22))

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
        btn_w, btn_h, gap = 130, 36, 10
        cols = 2
        bx0 = px + PW // 2 - (cols * btn_w + (cols - 1) * gap) // 2
        by0 = content_y + 60
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
        btn_w, btn_h, gap = 140, 44, 16
        total_w = len(btn_labels) * btn_w + (len(btn_labels) - 1) * gap
        bx0 = px + PW // 2 - total_w // 2
        by0 = content_y + 50
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
        csv_files = sorted(_glob.glob("maps/*.csv"))
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
            ("Resume",     "resume"),
            ("Resolution", "submenu:resolution"),
            ("Sound",      "submenu:sound"),
            ("Action Bar", "open_hotbar_editor"),
            ("Quit",       "submenu:quit_confirm"),
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

    _HBE_SZ  = 52   # tamanho de slot no editor
    _HBE_GAP = 10   # espaço entre slots

    def _close_hotbar_editor(self) -> None:
        self._show_hotbar_editor    = False
        self._hbe_drag_from         = None
        self._hbe_rebind_slot       = None
        self._hbe_cons_drag_from    = None
        self._hbe_cons_rebind_slot  = None
        self._save_config()

    def _draw_hotbar_editor(self, events: list) -> None:
        from skill_config import SKILL_CATALOG, NUM_SLOTS
        from components import ConsumableBar as _CB, Inventory as _Inv
        ps   = self.world.get_component(self.player_entity, PlayerSkills)
        cbar = self.world.get_component(self.player_entity, _CB)
        inv  = self.world.get_component(self.player_entity, _Inv)
        if not ps:
            return

        SZ  = self._HBE_SZ
        GAP = self._HBE_GAP
        mx, my  = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)

        # ── Teclas ────────────────────────────────────────────────────────
        for e in events:
            if e.type != pygame.KEYDOWN:
                continue
            if self._hbe_rebind_slot is not None:
                if e.key == pygame.K_ESCAPE:
                    self._hbe_rebind_slot = None
                else:
                    ps.keybinds[self._hbe_rebind_slot] = e.key
                    self._hbe_rebind_slot = None
                    self._save_config()
            elif self._hbe_cons_rebind_slot is not None:
                if e.key == pygame.K_ESCAPE:
                    self._hbe_cons_rebind_slot = None
                elif cbar:
                    cbar.keybinds[self._hbe_cons_rebind_slot] = e.key
                    self._hbe_cons_rebind_slot = None
                    self._save_config()
            elif e.key == pygame.K_ESCAPE:
                self._close_hotbar_editor()
                return

        # ── Skills disponíveis ────────────────────────────────────────────
        avail: list[str] = list(SKILL_CATALOG.keys())
        for s in ps.skills:
            if s and getattr(s, "talent_id", None) and s.skill_id not in avail:
                avail.append(s.skill_id)

        # ── Consumíveis disponíveis (do inventário) ───────────────────────
        cons_avail: list = []  # lista de Item únicos por nome
        if inv:
            seen_names: set = set()
            for it in inv.items:
                if it.consumable and it.name not in seen_names and it.stack > 0:
                    cons_avail.append(it)
                    seen_names.add(it.name)

        # ── Geometria do painel ───────────────────────────────────────────
        avail_row_w  = len(avail)          * (SZ + GAP) - GAP
        slots_row_w  = NUM_SLOTS           * (SZ + GAP) - GAP
        cons_avail_w = max(1, len(cons_avail)) * (SZ + GAP) - GAP
        cons_slots_w = _CB.NUM_SLOTS       * (SZ + GAP) - GAP
        PW = max(avail_row_w, slots_row_w, cons_avail_w, cons_slots_w) + 80
        PW = max(PW, 600)
        PH = 500
        px = SCREEN_WIDTH  // 2 - PW // 2
        py = SCREEN_HEIGHT // 2 - PH // 2

        # Overlay escurecido
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 170))
        self.screen.blit(ov, (0, 0))

        # Fundo do painel
        pygame.draw.rect(self.screen, (28, 22, 12), (px, py, PW, PH), border_radius=8)
        pygame.draw.rect(self.screen, (90, 72, 44), (px, py, PW, PH), 2, border_radius=8)

        # Título
        title_s = self.font_md.render("Action Bar", True, (220, 190, 110))
        self.screen.blit(title_s, (px + PW // 2 - title_s.get_width() // 2, py + 12))

        # Botão [X]
        close_r = pygame.Rect(px + PW - 28, py + 8, 22, 22)
        hov_x   = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (80, 40, 30) if hov_x else (50, 30, 20), close_r, border_radius=4)
        pygame.draw.rect(self.screen, (180, 80, 60), close_r, 1, border_radius=4)
        xs = self.font_md.render("X", True, (220, 120, 100))
        self.screen.blit(xs, xs.get_rect(center=close_r.center))
        if clicked and hov_x:
            self._close_hotbar_editor()
            return

        # ── Seção "Skills disponíveis" ────────────────────────────────────
        self.screen.blit(
            self.font_sm.render("Skills disponíveis:", True, (160, 150, 110)),
            (px + 16, py + 50))

        avail_y  = py + 70
        avail_x0 = px + PW // 2 - avail_row_w // 2
        avail_rects: list[pygame.Rect] = []
        placed_ids = {s.skill_id for s in ps.skills if s}

        for j, sid in enumerate(avail):
            ax = avail_x0 + j * (SZ + GAP)
            ar = pygame.Rect(ax, avail_y, SZ, SZ)
            avail_rects.append(ar)

            placed = sid in placed_ids
            bg     = (22, 18, 10) if placed else (40, 32, 18)
            pygame.draw.rect(self.screen, bg, ar, border_radius=4)
            pygame.draw.rect(self.screen, (80, 65, 38), ar, 1, border_radius=4)

            ik = ICONS.skill_key_by_name(f"skill_{sid}") or ICONS.skill_key(j)
            ic = ICONS.get(ik, SZ - 4)
            if ic:
                self.screen.blit(ic, (ax + 2, avail_y + 2))

            if placed:
                dim = pygame.Surface((SZ, SZ), pygame.SRCALPHA)
                dim.fill((0, 0, 0, 140))
                self.screen.blit(dim, ar.topleft)

            if clicked and ar.collidepoint(mx, my) and not self._hbe_drag_from:
                self._hbe_drag_from = ("panel", sid)

        # ── Linha divisória ───────────────────────────────────────────────
        div_y = avail_y + SZ + 14
        pygame.draw.line(self.screen, (60, 50, 30), (px + 16, div_y), (px + PW - 16, div_y))

        # ── Seção "Barra de Habilidades" ──────────────────────────────────
        self.screen.blit(
            self.font_sm.render("Barra de Habilidades:", True, (160, 150, 110)),
            (px + 16, div_y + 8))

        slot_y  = div_y + 28
        slot_x0 = px + PW // 2 - slots_row_w // 2
        slot_rects: list[pygame.Rect] = []

        for i in range(NUM_SLOTS):
            sx = slot_x0 + i * (SZ + GAP)
            sr = pygame.Rect(sx, slot_y, SZ, SZ)
            slot_rects.append(sr)
            skill = ps.skills[i]

            is_drag_src   = self._hbe_drag_from == ("slot", i)
            is_drop_hover = self._hbe_drag_from and sr.collidepoint(mx, my)

            bg = (15, 12, 6) if is_drag_src else ((40, 32, 18) if skill else (22, 18, 10))
            pygame.draw.rect(self.screen, bg, sr, border_radius=4)
            bd = (160, 130, 60) if is_drop_hover else (80, 65, 38)
            pygame.draw.rect(self.screen, bd, sr, 2 if is_drop_hover else 1, border_radius=4)

            if skill and not is_drag_src:
                ik = ICONS.skill_key_by_name(skill.icon_name) or ICONS.skill_key(i)
                ic = ICONS.get(ik, SZ - 4)
                if ic:
                    self.screen.blit(ic, (sx + 2, slot_y + 2))

            kb_name = pygame.key.name(ps.keybinds[i]).upper()
            kb_r    = pygame.Rect(sx, slot_y + SZ + 4, SZ, 18)

            if self._hbe_rebind_slot == i:
                pygame.draw.rect(self.screen, (70, 55, 18), kb_r, border_radius=3)
                pygame.draw.rect(self.screen, (220, 180, 60), kb_r, 1, border_radius=3)
                kbs = self.font_xs.render("...", True, (255, 220, 80))
            elif kb_r.collidepoint(mx, my) and not self._hbe_drag_from:
                pygame.draw.rect(self.screen, (50, 42, 22), kb_r, border_radius=3)
                pygame.draw.rect(self.screen, (120, 100, 50), kb_r, 1, border_radius=3)
                kbs = self.font_xs.render(kb_name, True, (220, 200, 130))
                if clicked:
                    self._hbe_rebind_slot = i
                    self._hbe_drag_from   = None
            else:
                kbs = self.font_xs.render(kb_name, True, (110, 100, 65))
            self.screen.blit(kbs, kbs.get_rect(centerx=sx + SZ // 2, y=kb_r.y + 1))

            if clicked and sr.collidepoint(mx, my) and skill and not self._hbe_drag_from \
                    and self._hbe_rebind_slot is None:
                self._hbe_drag_from = ("slot", i)

        # ── Linha divisória ───────────────────────────────────────────────
        div2_y = slot_y + SZ + 22 + 10
        pygame.draw.line(self.screen, (60, 50, 30), (px + 16, div2_y), (px + PW - 16, div2_y))

        # ── Seção "Consumíveis disponíveis" ───────────────────────────────
        self.screen.blit(
            self.font_sm.render("Consumíveis disponíveis:", True, (120, 180, 140)),
            (px + 16, div2_y + 8))

        cons_avail_y  = div2_y + 28
        cons_avail_x0 = px + PW // 2 - cons_avail_w // 2
        cons_avail_rects: list[pygame.Rect] = []

        if cons_avail:
            placed_cons = {name for name in (cbar.slots if cbar else []) if name}
            for j, item in enumerate(cons_avail):
                ax = cons_avail_x0 + j * (SZ + GAP)
                ar = pygame.Rect(ax, cons_avail_y, SZ, SZ)
                cons_avail_rects.append(ar)

                placed = item.name in placed_cons
                bg     = (12, 22, 14) if placed else (18, 36, 22)
                pygame.draw.rect(self.screen, bg, ar, border_radius=4)
                pygame.draw.rect(self.screen, (50, 90, 60), ar, 1, border_radius=4)

                # Ícone do item (fallback: inicial do nome)
                _ic = ICONS.get(ICONS.item_key(item), SZ - 4)
                if _ic:
                    self.screen.blit(_ic, (ax + 2, cons_avail_y + 2))
                else:
                    letter = self.font_sm.render(item.name[0].upper(), True, (100, 220, 140))
                    self.screen.blit(letter, letter.get_rect(center=ar.center))

                # Quantidade no canto
                stack_s = self.font_xs.render(str(item.stack), True, (200, 200, 160))
                self.screen.blit(stack_s, (ar.right - stack_s.get_width() - 2, ar.bottom - stack_s.get_height() - 1))

                if placed:
                    dim = pygame.Surface((SZ, SZ), pygame.SRCALPHA)
                    dim.fill((0, 0, 0, 130))
                    self.screen.blit(dim, ar.topleft)

                if clicked and ar.collidepoint(mx, my) and not self._hbe_cons_drag_from \
                        and not self._hbe_drag_from:
                    self._hbe_cons_drag_from = ("panel", item.name)
        else:
            empty_s = self.font_xs.render("Nenhum consumível no inventário", True, (80, 90, 75))
            self.screen.blit(empty_s, (px + PW // 2 - empty_s.get_width() // 2, cons_avail_y + SZ // 2 - 8))

        # ── Linha divisória ───────────────────────────────────────────────
        div3_y = cons_avail_y + SZ + 14
        pygame.draw.line(self.screen, (60, 50, 30), (px + 16, div3_y), (px + PW - 16, div3_y))

        # ── Seção "Barra de Consumíveis" ──────────────────────────────────
        self.screen.blit(
            self.font_sm.render("Barra de Consumíveis:", True, (120, 180, 140)),
            (px + 16, div3_y + 8))

        cons_slot_y  = div3_y + 28
        cons_slot_x0 = px + PW // 2 - cons_slots_w // 2
        cons_slot_rects: list[pygame.Rect] = []

        for i in range(_CB.NUM_SLOTS):
            sx = cons_slot_x0 + i * (SZ + GAP)
            sr = pygame.Rect(sx, cons_slot_y, SZ, SZ)
            cons_slot_rects.append(sr)
            item_name = cbar.slots[i] if cbar else None

            is_drag_src   = self._hbe_cons_drag_from == ("slot", i)
            is_drop_hover = self._hbe_cons_drag_from and sr.collidepoint(mx, my)

            bg = (8, 16, 10) if is_drag_src else ((18, 36, 22) if item_name else (12, 22, 14))
            pygame.draw.rect(self.screen, bg, sr, border_radius=4)
            bd = (100, 180, 120) if is_drop_hover else (50, 90, 60)
            pygame.draw.rect(self.screen, bd, sr, 2 if is_drop_hover else 1, border_radius=4)

            if item_name and not is_drag_src:
                _ic_key = "item_" + item_name.lower().replace(" ", "_")
                _ic = ICONS.get(_ic_key, SZ - 4)
                if _ic:
                    self.screen.blit(_ic, (sr.x + 2, sr.y + 2))
                else:
                    letter = self.font_sm.render(item_name[0].upper(), True, (100, 220, 140))
                    self.screen.blit(letter, letter.get_rect(center=sr.center))
                # Stack count from inventory
                if inv:
                    it = next((x for x in inv.items if x.name == item_name), None)
                    if it:
                        stk_s = self.font_xs.render(str(it.stack), True, (200, 200, 160))
                        self.screen.blit(stk_s, (sr.right - stk_s.get_width() - 2, sr.bottom - stk_s.get_height() - 1))

            kb_name = pygame.key.name(cbar.keybinds[i]).upper() if cbar else "?"
            kb_r    = pygame.Rect(sx, cons_slot_y + SZ + 4, SZ, 18)

            if self._hbe_cons_rebind_slot == i:
                pygame.draw.rect(self.screen, (18, 55, 28), kb_r, border_radius=3)
                pygame.draw.rect(self.screen, (80, 220, 120), kb_r, 1, border_radius=3)
                kbs = self.font_xs.render("...", True, (100, 255, 140))
            elif kb_r.collidepoint(mx, my) and not self._hbe_cons_drag_from \
                    and not self._hbe_drag_from:
                pygame.draw.rect(self.screen, (18, 42, 22), kb_r, border_radius=3)
                pygame.draw.rect(self.screen, (60, 120, 70), kb_r, 1, border_radius=3)
                kbs = self.font_xs.render(kb_name, True, (140, 220, 160))
                if clicked:
                    self._hbe_cons_rebind_slot = i
                    self._hbe_cons_drag_from   = None
            else:
                kbs = self.font_xs.render(kb_name, True, (65, 110, 75))
            self.screen.blit(kbs, kbs.get_rect(centerx=sx + SZ // 2, y=kb_r.y + 1))

            if clicked and sr.collidepoint(mx, my) and item_name \
                    and not self._hbe_cons_drag_from and not self._hbe_drag_from \
                    and self._hbe_cons_rebind_slot is None:
                self._hbe_cons_drag_from = ("slot", i)

        # ── Dica de uso ───────────────────────────────────────────────────
        hint = self.font_xs.render(
            "Arraste entre slots  |  Clique na tecla para rebindear  |  ESC para fechar",
            True, (90, 80, 55))
        self.screen.blit(hint, hint.get_rect(centerx=px + PW // 2, y=py + PH - 20))

        # ── Tooltip de nome no hover ───────────────────────────────────────
        hover_name = None
        for sid, ar in zip(avail, avail_rects):
            if ar.collidepoint(mx, my) and not self._hbe_drag_from:
                entry = SKILL_CATALOG.get(sid)
                hover_name = entry["name"] if isinstance(entry, dict) else sid
                break
        if hover_name is None:
            for i, sr in enumerate(slot_rects):
                sk = ps.skills[i]
                if sk and sr.collidepoint(mx, my) and not self._hbe_drag_from:
                    hover_name = sk.name
                    break
        if hover_name is None:
            for item, ar in zip(cons_avail, cons_avail_rects):
                if ar.collidepoint(mx, my) and not self._hbe_cons_drag_from:
                    hover_name = item.name
                    break
        if hover_name is None and cbar:
            for i, sr in enumerate(cons_slot_rects):
                if cbar.slots[i] and sr.collidepoint(mx, my) and not self._hbe_cons_drag_from:
                    hover_name = cbar.slots[i]
                    break
        if hover_name:
            ns = self.font_sm.render(hover_name, True, (230, 210, 150))
            nb = pygame.Rect(mx + 10, my - 22, ns.get_width() + 10, ns.get_height() + 6)
            if nb.right > SCREEN_WIDTH:
                nb.right = mx - 4
            pygame.draw.rect(self.screen, (30, 24, 12), nb, border_radius=3)
            pygame.draw.rect(self.screen, (90, 72, 44), nb, 1, border_radius=3)
            self.screen.blit(ns, (nb.x + 5, nb.y + 3))

        # ── Processa drop — skills ─────────────────────────────────────────
        if released and self._hbe_drag_from:
            drop_idx = next((i for i, sr in enumerate(slot_rects)
                             if sr.collidepoint(mx, my)), None)
            src = self._hbe_drag_from

            if drop_idx is not None:
                if src[0] == "panel":
                    sid = src[1]
                    src_slot = next(
                        (k for k in range(NUM_SLOTS)
                         if ps.skills[k] and ps.skills[k].skill_id == sid), None)
                    if src_slot is not None:
                        if src_slot != drop_idx:
                            ps.skills[src_slot], ps.skills[drop_idx] = \
                                ps.skills[drop_idx], ps.skills[src_slot]
                    else:
                        new_s = PlayerSkills._make_skill(sid, SKILL_CATALOG)
                        if new_s:
                            ps.skills[drop_idx] = new_s
                else:
                    si = src[1]
                    if si != drop_idx:
                        ps.skills[si], ps.skills[drop_idx] = ps.skills[drop_idx], ps.skills[si]
            elif src[0] == "slot":
                panel_r = pygame.Rect(px, py, PW, PH)
                if not panel_r.collidepoint(mx, my):
                    ps.skills[src[1]] = None

            self._hbe_drag_from = None
            self._save_config()

        # ── Processa drop — consumíveis ────────────────────────────────────
        if released and self._hbe_cons_drag_from and cbar:
            drop_idx = next((i for i, sr in enumerate(cons_slot_rects)
                             if sr.collidepoint(mx, my)), None)
            src = self._hbe_cons_drag_from

            if drop_idx is not None:
                if src[0] == "panel":
                    # Coloca item no slot (sobrescreve)
                    cbar.slots[drop_idx] = src[1]
                else:
                    # slot → slot: troca
                    si = src[1]
                    if si != drop_idx:
                        cbar.slots[si], cbar.slots[drop_idx] = \
                            cbar.slots[drop_idx], cbar.slots[si]
            elif src[0] == "slot":
                # Soltou fora: limpa o slot de origem
                panel_r = pygame.Rect(px, py, PW, PH)
                if not panel_r.collidepoint(mx, my):
                    cbar.slots[src[1]] = None

            self._hbe_cons_drag_from = None
            self._save_config()

        # ── Ícone arrastado no cursor — skills ────────────────────────────
        if self._hbe_drag_from:
            drag_sid = None
            if self._hbe_drag_from[0] == "panel":
                drag_sid = self._hbe_drag_from[1]
            else:
                s = ps.skills[self._hbe_drag_from[1]]
                drag_sid = s.skill_id if s else None
            if drag_sid:
                ik = ICONS.skill_key_by_name(f"skill_{drag_sid}") or ICONS.skill_key(0)
                ic = ICONS.get(ik, SZ - 4)
                if ic:
                    ic_copy = ic.copy()
                    ic_copy.set_alpha(210)
                    self.screen.blit(ic_copy, ic_copy.get_rect(center=(mx, my)))

        # ── Ícone arrastado no cursor — consumíveis ───────────────────────
        if self._hbe_cons_drag_from:
            drag_name = None
            if self._hbe_cons_drag_from[0] == "panel":
                drag_name = self._hbe_cons_drag_from[1]
            elif cbar:
                drag_name = cbar.slots[self._hbe_cons_drag_from[1]]
            if drag_name:
                drag_s = pygame.Surface((SZ, SZ), pygame.SRCALPHA)
                drag_s.fill((18, 36, 22, 180))
                pygame.draw.rect(drag_s, (50, 90, 60), (0, 0, SZ, SZ), 1, border_radius=4)
                _ic_key = "item_" + drag_name.lower().replace(" ", "_")
                _ic = ICONS.get(_ic_key, SZ - 4)
                if _ic:
                    drag_s.blit(_ic, (2, 2))
                else:
                    letter = self.font_sm.render(drag_name[0].upper(), True, (100, 220, 140))
                    drag_s.blit(letter, letter.get_rect(center=(SZ // 2, SZ // 2)))
                self.screen.blit(drag_s, drag_s.get_rect(center=(mx, my)))

    # ------------------------------------------------------------------
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
        })

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

    def _apply_hotbar_config(self) -> None:
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
                    elif sid in SKILL_CATALOG:
                        new_skills[i] = PlayerSkills._make_skill(sid, SKILL_CATALOG)
                ps.skills = new_skills

                for i in range(min(len(saved_keybinds), NUM_SLOTS)):
                    ps.keybinds[i] = saved_keybinds[i]

        # ── Consumable bar ────────────────────────────────────────────────
        cb_data = data.get("consumable_bar")
        if cb_data:
            from components import ConsumableBar as _CB
            cbar = self.world.get_component(self.player_entity, _CB)
            if cbar:
                saved_slots    = cb_data.get("slots",    [])
                saved_keybinds = cb_data.get("keybinds", [])
                for i in range(min(len(saved_slots), _CB.NUM_SLOTS)):
                    cbar.slots[i] = saved_slots[i]
                for i in range(min(len(saved_keybinds), _CB.NUM_SLOTS)):
                    cbar.keybinds[i] = saved_keybinds[i]

    # ── Aplica nova escala de resolução ────────────────────────────────────
    def _apply_scale(self, scale: float) -> None:
        self._scale = scale
        win_w = int(SCREEN_WIDTH  * scale)
        win_h = int(SCREEN_HEIGHT * scale)
        self._display = pygame.display.set_mode((win_w, win_h))
        self._save_config()
        _orig = self._orig_mouse_pos
        if scale != 1.0:
            _s = scale
            pygame.mouse.get_pos = lambda: (
                int(_orig()[0] / _s),
                int(_orig()[1] / _s),
            )
        else:
            pygame.mouse.get_pos = _orig

    # ------------------------------------------------------------------
    # HUD
    # ------------------------------------------------------------------
    # Hotbar de habilidades (1-4)
    # ------------------------------------------------------------------

    _HB_W   = 34
    _HB_H   = 34
    _HB_ICO = 32
    _HB_PAD = 4

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

        # Apenas slots ocupados, compactados e centralizados
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
                    stk_s = self.font_xs.render(str(item.stack), True, (200, 200, 160))
                    self.screen.blit(stk_s, (r.right - stk_s.get_width() - 2,
                                             r.bottom - stk_s.get_height() - 2))
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

        else:
            lines.append((skill.description, C_INFO))

        lines.append(("", C_INFO))  # separador

        # ── Cooldown / Custo ─────────────────────────────────────────────
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
        """Renderiza tooltip de habilidade usando font_xs (fonte menor para descrições)."""
        if not self._pending_skill_tooltip:
            return
        mx, my, title, lines = self._pending_skill_tooltip[:4]
        self._draw_tooltip(mx, my, title, lines, body_font=self.font_xs)

    def _draw_world_tooltip(self):
        """Mostra tooltip ao passar o mouse sobre inimigos/NPCs no mundo."""
        from components import Enemy, EnemyTier, Renderable, Merchant
        mx, my = pygame.mouse.get_pos()
        wx = mx + self._cam_x
        wy = my + self._cam_y

        # Comerciantes
        for eid, pos, rend, merch in self.world.get_entities_with(
                Position, Renderable, Merchant):
            hw, hh = rend.width / 2, rend.height / 2
            if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                continue
            self._pending_tooltip = (mx, my, merch.name,
                                     [("Clique direito para abrir a loja", (150, 220, 150))])
            return

        # Inimigos
        from components import EntityIdentity
        for eid, pos, rend, _ in self.world.get_entities_with(
                Position, Renderable, Enemy):
            cs = self.world.get_component(eid, CombatStats)
            if cs and cs.current_hp <= 0:
                continue
            hw, hh = rend.width / 2, rend.height / 2
            if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                continue
            tier_c = self.world.get_component(eid, EnemyTier)
            ident  = self.world.get_component(eid, EntityIdentity)
            tier   = tier_c.tier if tier_c else "Normal"
            mob_name  = ident.name  if ident else "Inimigo"
            mob_race  = ident.race  if ident else "?"
            mob_level = ident.level if ident else 1
            tier_colors = {"normal": (200,200,200), "elite": (140,140,255),
                           "rare": (255,165,0), "boss": (220,80,220)}
            title_col = tier_colors.get(tier.lower(), (200,200,200))
            lines = [
                (f"Level {mob_level}  {mob_race}", (160, 160, 160)),
            ]
            if cs:
                hp_pct = int(cs.current_hp / max(1, cs.max_hp) * 100)
                lines.append((f"HP: {cs.current_hp}/{cs.max_hp} ({hp_pct}%)",
                               C_GREEN if hp_pct > 50 else C_YELLOW if hp_pct > 25 else C_RED))
            self._pending_tooltip = (mx, my, mob_name, lines, title_col)
            return

    # ------------------------------------------------------------------

    def _draw_hud(self):
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        char_stats   = self.world.get_component(self.player_entity, CharacterStats)
        perm_stats   = self.world.get_component(self.player_entity, PermanentStats)
        if not combat_stats:
            return

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

        # --- HP ---
        hp_ratio = max(0, combat_stats.current_hp / max(1, combat_stats.max_hp))
        bar_w = 200
        pygame.draw.rect(self.screen, (80, 0, 0),   (10, y, bar_w, 14))
        pygame.draw.rect(self.screen, C_RED,         (10, y, int(bar_w * hp_ratio), 14))
        hp_surf = self.font_sm.render(
            f"HP {combat_stats.current_hp}/{combat_stats.max_hp}", True, C_WHITE)
        self.screen.blit(hp_surf, (14, y))
        y += 18

        # --- Rage ---
        if char_stats:
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
            True, C_CYAN)
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
    _PANEL_W    = 680
    _PANEL_H    = 540
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

        # Arma de duas mãos → desequipa offhand se houver
        if getattr(item, 'two_handed', False) and target_slot == "mainhand":
            old_oh = equip.slots.get("offhand")
            if old_oh:
                for mod in old_oh.modifiers:
                    combat_stats.remove_modifier(mod)
                inv.items.append(old_oh)
                equip.slots["offhand"] = None

        # Offhand bloqueado por arma de duas mãos
        if target_slot == "offhand" and equip.is_offhand_locked():
            return

        # Troca com item já equipado
        old_item = equip.slots[target_slot]
        if old_item:
            for mod in old_item.modifiers:
                combat_stats.remove_modifier(mod)
            inv.items.append(old_item)

        # Equipa o novo item
        equip.slots[target_slot] = item
        inv.items.remove(item)
        if target_slot == "mainhand" and getattr(item, "attack_speed", 0.0) > 0:
            combat_stats.base_attack_interval = item.attack_speed
        for mod in item.modifiers:
            combat_stats.add_modifier(mod)

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
            combat_stats.remove_modifier(mod)
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
                stack = getattr(item, "stack", 1)
                if stack > 1 or getattr(item, "max_stack", 1) > 1:
                    stk_s = self.font_sm.render(str(stack), True, (255, 255, 255))
                    self.screen.blit(stk_s, (r.right - stk_s.get_width() - 2,
                                             r.bottom - stk_s.get_height() - 1))

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

        # ---- Seção de estatísticas ----
        sy2 = divider_y + PAD
        self.screen.blit(self.font_sm.render("Estatísticas", True, (180, 150, 90)), (x0 + PAD, sy2))
        sy2 += 18
        col_b = x0 + 180
        col_e = x0 + 280
        col_t = x0 + 380
        for lbl in ("Atributo", "Base", "+Equip", "Total"):
            cx = x0 + PAD if lbl == "Atributo" else (col_b if lbl == "Base" else col_e if lbl == "+Equip" else col_t)
            self.screen.blit(self.font_sm.render(lbl, True, (160, 140, 100)), (cx, sy2))
        sy2 += 18

        def stat_row(display, attribute, base_val, fmt="{:.0f}"):
            eq_val  = equip.get_modifier_total(attribute)
            tot_val = base_val + eq_val
            self.screen.blit(self.font_sm.render(display,              True, C_GRAY),  (x0 + PAD, sy2))
            self.screen.blit(self.font_sm.render(fmt.format(base_val), True, C_WHITE), (col_b, sy2))
            self.screen.blit(self.font_sm.render(f"+{fmt.format(eq_val)}", True, C_GREEN if eq_val > 0 else C_GRAY), (col_e, sy2))
            self.screen.blit(self.font_sm.render(fmt.format(tot_val), True, C_YELLOW), (col_t, sy2))

        stat_row("Atq Fisico",  "attack_power", combat_stats.base_attack_power); sy2 += 18
        stat_row("Atq Magico",  "spell_power",  combat_stats.base_spell_power);  sy2 += 18
        stat_row("Armadura",    "armor",         combat_stats.base_armor);        sy2 += 18
        stat_row("Estamina",    "stamina",       combat_stats.base_stamina);      sy2 += 18
        base_crit = combat_stats.base_crit_rating * 100
        eq_crit   = equip.get_modifier_total("crit_rating") * 100
        self.screen.blit(self.font_sm.render("Critico",          True, C_GRAY),  (x0 + PAD, sy2))
        self.screen.blit(self.font_sm.render(f"{base_crit:.1f}%", True, C_WHITE), (col_b, sy2))
        self.screen.blit(self.font_sm.render(f"+{eq_crit:.1f}%",  True, C_GREEN if eq_crit > 0 else C_GRAY), (col_e, sy2))
        self.screen.blit(self.font_sm.render(f"{base_crit+eq_crit:.1f}%", True, C_YELLOW), (col_t, sy2))

        # ---- Rodapé: moedas ----
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            footer_y = y0 + H - 28
            pygame.draw.line(self.screen, (90, 70, 40),
                             (x0 + PAD, footer_y - 4), (x0 + W - PAD, footer_y - 4))
            # Ícone de moeda
            coin_x, coin_y = x0 + PAD + 10, footer_y + 8
            pygame.draw.circle(self.screen, (180, 140, 0),  (coin_x, coin_y), 9)
            pygame.draw.circle(self.screen, (255, 215, 0),  (coin_x, coin_y), 7)
            pygame.draw.circle(self.screen, (120, 90, 0),   (coin_x, coin_y), 9, 1)
            g_surf = self.font_sm.render("G", True, (120, 90, 0))
            self.screen.blit(g_surf, (coin_x - g_surf.get_width() // 2,
                                      coin_y - g_surf.get_height() // 2))
            gold_surf = self.font_md.render(f"{wallet.gold} moedas", True, (255, 215, 0))
            self.screen.blit(gold_surf, (x0 + PAD + 24, footer_y + 4))
