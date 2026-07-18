# game.py
import pygame
import math
import time as _time
import gc as _gc
from ui.fonts import make as _font
from ui.ui_sizes import UI

from engine.world import World
from engine.components import Position, Tilemap, CombatStats, CharacterStats, PermanentStats, \
                       TileMovement, PlayerAutoMove, CombatState, FogOfWar, Enemy, Visible, \
                       Camera, Renderable, SpellCast, Channeling, IceBlockEffect, AoeTargeting
from ui.ui_components import UIState, ShopUIState, LootUIState, DragState, TradeUIState
from ui.systems import (
    PlayerInputSystem, TileMovementSystem, RenderSystem, CameraSystem,
    EnemyAISystem, TileRenderSystem, TileValidationSystem,
    PathfindingSystem, CombatSystem, CombatStateSystem, MouseTargetingSystem,
    ProjectileSystem, CorpseSystem, LootSystem, ShopSystem, SkillSystem,
    SpawnZoneSystem, ConsumableSystem, DeathHandlerSystem, FogSystem,
    StatusEffectSystem, EnemyAbilitySystem,
    register_services,
)
from engine.stats_system import XPSystem, DeathRespawnSystem
from ui.quest_system import QuestSystem, QuestDialogSystem, QuestJournalSystem
from engine.quest_events import set_quest_system
from engine.entity_factory import create_player, create_camera, create_enemy, create_tilemap, create_merchant, create_spawn_zone, create_quest_giver, create_blacksmith, create_trainer
from ui.god_mode import GodModeEditor
from engine.components import Inventory, Equipment, PlayerSkills, Wallet, GhostState
from engine.map_loader import load_map_csv, validate_map
from engine.tileset import TILE_SIZE
from ui.combat_log import LOG
from ui.floating_text import FLT, DASH_TRAIL, WARN, PROC
from ui.chat_bubble import CHAT_BUBBLE
from ui.icon_manager import ICONS
from ui.sound_manager import SOUNDS
from ui.ui_compare import draw_compare_panel
from ui.talent_system import TalentSystem
from ui.skill_level_ui import SkillLevelUI
from ui.ui_helpers import item_tooltip_lines, draw_stack_count, RARITY_COLORS as _ITEM_RARITY_COLORS, fill_surf
from ui.map_overlay import MapOverlay
from ui.minimap import Minimap
from engine.stat_fns import add_modifier, remove_modifier, learn_recipe
# save_game/load_game/has_save/next_free_slot (saves locais) removidos junto
# do modo offline (item A2 §11) — persistência de personagem é só o servidor.
from client.network_handlers import NetworkHandlers
from client.remote_entity_handlers import RemoteEntityHandlers
from client.save_sync_handlers import SaveSyncHandlers
from client.inventory_handlers import InventoryHandlers
from client.tooltip_handlers import TooltipHandlers
from client.debug_handlers import DebugHandlers
from client.menu_handlers import MenuHandlers
from client.hotbar_editor_handlers import HotbarEditorHandlers
from client.habilidades_handlers import HabilidadesHandlers
from client.online_mode_handlers import OnlineModeHandlers
from client.hotbar_handlers import HotbarHandlers
from client.consumable_bar_handlers import ConsumableBarHandlers
from client.hud_handlers import HudHandlers
from client.death_ui_handlers import DeathUIHandlers
from client.modal_stack_handlers import ModalStackHandlers
from client.trade_handlers import TradeHandlers
from client.duel_handlers import DuelHandlers
from client.party_handlers import PartyHandlers
from client.chat_handlers import ChatHandlers
from client.colors import C_WHITE, C_YELLOW, C_GREEN, C_RED, C_GRAY, C_CYAN, C_ORANGE

# --- Configurações do Jogo ---
FPS = 60

MAP_FILES = [
    "maps/map_1.csv",
]

DEBUG_MODE    = False  # sobrescrito pelo config.json em runtime
PROFILE_FRAMES = False  # sobrescrito pelo config.json em runtime


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


class GameEngine(NetworkHandlers, RemoteEntityHandlers, SaveSyncHandlers, InventoryHandlers, TooltipHandlers, DebugHandlers, MenuHandlers, HotbarEditorHandlers, HabilidadesHandlers, OnlineModeHandlers, HotbarHandlers, ConsumableBarHandlers, HudHandlers, DeathUIHandlers, ModalStackHandlers, TradeHandlers, DuelHandlers, PartyHandlers, ChatHandlers):
    def __init__(self, scale: float = 1.0, char_data: "dict | None" = None,
                 save_slot: int = 0,
                 net_user: str = "", net_pass: str = "",
                 net_client=None):
        pygame.init()
        # Vincula a façade fx aos gerenciadores reais (FLT/SOUNDS/etc.) —
        # world_systems/skill_handlers usam os proxies de fx.py, que são
        # no-op até este bind (e permanecem no-op no servidor headless).
        import engine.fx as _fx
        _fx.bind_client_fx()
        self._scale      = scale
        self._save_slot  = save_slot
        self._net_user    = net_user
        self._net_pass    = net_pass
        self._net         = net_client  # usa cliente existente se fornecido
        self._my_eid      = -1       # entity_id atribuído pelo servidor
        # Outros jogadores: server_eid → local_eid (entidade ECS real)
        self._remote_players: dict[int, int] = {}
        # Mobs do servidor: server_eid → local_eid (cache O(1) para lookup invertido)
        # Estado (hp, posição, etc.) vive em RemoteEntityMeta no componente ECS.
        self._remote_mobs: dict[int, int] = {}
        # Ghost positions: server_eid → (x, y) populado NO MOMENTO do despawn.
        # NÃO limpo após despawn — usado como fallback de FLT quando SKILL_RESULT
        # chega depois que o mob já foi removido de _remote_mobs.
        self._mob_ghost_pos: dict[int, tuple[float, float]] = {}
        # Kills com animação em andamento (progress >= 0.3): local_eid → {"server_eid": int, "timer": float}
        # Remoção adiada até is_moving=False; mob termina o passo para target_tile.
        self._pending_mob_despawn: dict[int, dict] = {}
        # Redirect de loot: (srv_tx, srv_ty) → (target_tx, target_ty)
        # Quando mob commitou para next tile (progress >= 0.3), loot segue para lá.
        self._pending_loot_redirect: dict[tuple[int, int], tuple[int, int]] = {}
        # Timers de passo para players remotos (server_eid → tempo restante)
        self._remote_step_timers:  dict[int, float]         = {}
        # Snapshot de equipamento para detecção de mudanças e envio de EQUIP_SYNC
        self._equip_snapshot: dict = {}
        self._mob_move_queues:         dict[int, list] = {}  # server_eid → [(tx,ty,is_dash)...]
        self._remote_player_move_queues: dict[int, list] = {}  # server_eid → [(tx,ty,is_dash)]
        # Correções de posição "is_dash" do próprio player (ex: vítima de knockback)
        # encadeadas — animam em sequência igual a um mob/player remoto, em vez de
        # aplicar snap instantâneo (usado para correções normais de anti-cheat).
        self._self_move_queue: list[tuple[int, int]] = []
        # Projéteis de mobs remotos: server_proj_eid → local_eid (entidade visual)
        self._remote_mob_projectiles: dict[int, int] = {}
        # Cache para flechas atrasadas: server_eid → (local_eid, pos_x, pos_y)
        # Preenchido em _apply_combat_result antes do mob despawnar; consumido em is_completion.
        self._fr_pending_target: dict[int, tuple[int, float, float]] = {}
        # Corpses do servidor: corpse_id → (tx, ty)  — apenas marcador visual
        self._remote_corpses: dict[int, tuple[int, int]] = {}
        # Loot disponível para o player local: corpse_id → {items, tx, ty}
        self._available_loot: dict[int, dict] = {}
        # Projéteis com dano diferido: damage info aguardando projétil ser criado.
        # Preenchido no SKILL_RESULT is_completion quando projétil ainda não existe.
        # Processado após os sistemas atualizarem (SpellCastSystem cria o projétil).
        self._bdf_pending: list[dict] = []
        # IDs de skills cujo cast foi cancelado localmente (movimento).
        # Usado para ignorar is_completion tardio do servidor quando o cast
        # já foi cancelado no cliente — evita projétil fantasma.
        self._cancelled_spell_ids: set[str] = set()
        # Último alvo enviado ao servidor (evita reenvios desnecessários)
        self._net_last_target: int = -2
        # Server EID do último player remoto perseguido — reacquire quando respawnar
        self._pvp_respawn_target: int = -1
        # Fluxo de morte/espírito: timer local (2s) até mostrar "Você morreu",
        # e timer visual do contador de revive automático no cemitério (45s).
        self._death_timer: float = 0.0
        self._ghost_timer: float = 0.0
        self._death_release_btn: pygame.Rect | None = None
        self._ghost_revive_btn:  pygame.Rect | None = None
        # Buffer pré-alocado para pygame.transform.grayscale(src, dst) — evita alocação por frame.
        # pygame-ce >=2.4 usa SIMD (AVX2) nessa call: ~2-4ms vs 28ms do pygame upstream.
        self._ghost_gray_buf: "pygame.Surface | None" = None
        # Última posição enviada ao servidor (evita envios duplicados)
        self._net_last_tx: int = -1
        self._net_last_ty: int = -1
        win_w = int(1280 * scale)
        win_h = int(720  * scale)
        # Renderiza na resolução nativa da janela — sem escala no frame final
        # (win_w/win_h == tamanho lógico == tamanho da janela, então
        # pygame.SCALED não faz nenhum upscale de verdade aqui — só troca o
        # caminho de apresentação).
        #
        # SCALED + vsync=1: sem SCALED, set_mode() cria uma surface de
        # software (blit via GDI) — vsync=1 nesse caminho, em modo janela no
        # Windows, faz flip() bloquear esperando o compositor (DWM) de forma
        # bem inconsistente (medido: display_flip variando 5-47ms, sem
        # nenhuma correlação com carga real de trabalho — ver
        # logs/client_prof.log, investigação com o usuário 14/07/2026,
        # ARQUITETURA_ONLINE.md). SCALED troca pro SDL_Renderer acelerado por
        # hardware (caminho "flip model" no Windows/DXGI) — apresenta sem
        # cópia pelo compositor, o mesmo princípio que engines grandes usam
        # (swap chain com present direto). `clock.tick_busy_loop(FPS)` (em
        # run(), abaixo) continua fazendo o pacing de FPS — vsync aqui é só
        # pra eliminar tearing, não pra travar o frame rate.
        self._display = pygame.display.set_mode(
            (win_w, win_h), pygame.DOUBLEBUF | pygame.SCALED, vsync=1)
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
        # Override temporário de escala — cada painel modal calcula, no início do
        # seu próprio _draw_*/_handle_*_click, a escala MÁXIMA que ainda cabe na
        # janela atual (min(_ui_scale, tela/tamanho_base_do_painel)) e guarda aqui
        # antes de chamar self._u(). Sem isso, _ui_scale alto + janela pequena
        # produzia modais maiores que a própria tela (ver PROBLEMAS_ARQUITETURA.md
        # item IU4 — regressão pós-fix original).
        self._u_scale_override: "float | None" = None
        self._reload_ui_fonts()

        self.world = World()
        self.systems = []
        # _show_inventory e _show_talents → UIState component (via property)
        self._show_debug      = False
        self._debug_buttons: list = []      # [(rect, n_levels)] preenchido em _draw_debug_modal
        self._debug_tab: str = "nivel"      # "nivel" | "itens" | "ouro" | "mapa"
        # Atributos brutos/ratings de combate no HUD permanente — desligado
        # por padrão (redundante com a aba Estatísticas do Inventário, só
        # poluía o HUD); liga na aba Nivel do debug (F12) quando precisar
        # verificar item/efeito/talento batendo nos stats.
        self._hud_show_debug_stats: bool = False
        self._debug_hud_toggle_r: "pygame.Rect | None" = None
        self._debug_item_scroll: int = 0
        self._debug_item_buttons: list = []
        self._debug_gold_buttons: list = []
        self._debug_tab_buttons: list = []
        self._debug_item_catalog: list = []
        self._debug_map_buttons:  list = []
        self._debug_teleport_map: str = ""   # quando não-vazio, pending_destination → teleporte
        self._selected_inv_idx = -1
        self._trade_gold_focus: bool = False   # campo de gold da janela de trade tem foco?
        self._trade_gold_text:  str  = ""      # texto digitado enquanto focado
        self._chat_text:    str = ""           # texto digitado enquanto o chat está focado
        self._chat_tab:     str = "local"      # aba ativa: "local" | "world" | "combat"
        from collections import deque as _deque_chat
        self._chat_local: "_deque_chat" = _deque_chat(maxlen=500)  # (sender, text, color)
        self._chat_world: "_deque_chat" = _deque_chat(maxlen=500)  # (sender, text, color)
        self._chat_scroll: dict = {"local": 0, "world": 0, "combat": 0}  # linhas rolado (0 = mais recente)
        self._chat_bs_hold_t:    float = 0.0    # hold-to-repeat do Backspace (ver chat_handlers.py)
        self._chat_bs_repeating: bool  = False
        self._current_map_file: str = MAP_FILES[0]
        self.transition_tiles: dict = {}   # (tile_x, tile_y) → trans_dict
        self._transition_cooldown = 0.0
        self._map_title_timer = 0.0
        self._cam_x = 0.0
        self._cam_y = 0.0
        # _zoom → Camera.zoom component (via property)
        self._zoom_min:  float = 1.5
        self._zoom_max:  float = 2.5
        self._zoom_step: float = 0.25
        # Debounce do zoom via scroll do mouse — cada "clique" da roda mudava
        # self._zoom NA HORA, e o bloco "Zoom surf: dimensiona a world_surf"
        # (run(), mais abaixo) recalcula lw/lh a partir de self._zoom TODO
        # FRAME e já invalida+reconstrói o cache de tiles sozinho sempre que
        # o tamanho lógico muda — ou seja, cada clique de scroll já disparava
        # sua PRÓPRIA reconstrução completa (até ~150ms medido em
        # logs/client_prof.log), mesmo sem nenhuma chamada explícita extra.
        # Um gesto normal de zoom (vários cliques em sequência rápida) virava
        # uma rajada de reconstruções — reportado pelo usuário 14/07/2026.
        # Fix: scroll NÃO escreve mais em self._zoom direto — acumula em
        # _zoom_pending, e só comita (self._zoom = self._zoom_pending) depois
        # que o scroll parar por _ZOOM_DEBOUNCE_S sem novo clique. O bloco
        # "Zoom surf" só vê UMA mudança de tamanho por gesto inteiro, não uma
        # por clique — reconstrói 1x em vez de N. -1.0 = nada pendente.
        self._ZOOM_DEBOUNCE_S = 0.15
        self._zoom_cache_dirty_timer: float = -1.0
        self._zoom_pending: float = self._zoom_min
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
        self._show_skills: bool = False          # painel Skill Level (tecla L)
        self._hab_scroll: int = 0               # scroll do painel Habilidades
        self._ui_events:      list = []            # eventos do frame atual (para _draw_* sem parâmetro)
        self._orig_mouse_pos  = pygame.mouse.get_pos  # kept for compatibility
        # Zonas de ambient
        self._ambient_zones:    list = []   # [{name, ambient, rect:(x1,y1,x2,y2)}]
        self._default_ambient:  str  = ""   # ambient padrão do mapa
        self._current_zone:     str  = ""   # nome da zona onde o jogador está

        self._map_overlay   = MapOverlay(self.screen)
        self._map_overlay.set_ui_scale(self._ui_scale)
        self._minimap       = Minimap(self.screen, self._map_overlay)
        self._minimap.set_ui_scale(self._ui_scale)
        self._loading_save        = False
        # Loading screen: exibida até LOGIN_OK chegar E tempo mínimo decorrer.
        # _server_ready: True quando LOGIN_OK (ou erro) é recebido.
        # _loading_min_t: tempo mínimo de exibição — garante visibilidade mesmo
        #   quando LOGIN_OK já está na fila no primeiro frame (localhost rápido).
        self._server_ready        = False
        self._loading_min_t       = 1.5    # segundos mínimos de tela de loading
        self._loading_timeout     = 15.0   # timeout total antes de continuar offline
        self._loading_anim_t      = 0.0    # timer para animação dos pontos (...)
        self._logged_char_name    = ""     # nome do personagem logado — hotbar per-char

        # Exibe loading screen antes das operações lentas de init (mapa, sistemas,
        # rede) para cobrir a janela preta entre set_mode e run(). Static — um frame.
        self._draw_loading_screen(0.0)
        self._display.blit(self.screen, (0, 0))
        pygame.display.flip()
        pygame.event.pump()   # evita "não responde" no OS durante o carregamento

        self._load_map_and_entities()
        self._init_systems()
        self._talent_system = TalentSystem(self.world, self.player_entity, self.screen)
        self._skill_level_ui = SkillLevelUI(self.world, self.player_entity, self.screen)
        self._shop_system   = ShopSystem(self.world, self.player_entity, self.screen)
        self._quest_system  = QuestSystem(self.world, self.player_entity)
        set_quest_system(self._quest_system)
        self._quest_dialog  = QuestDialogSystem(
            self.world, self.player_entity, self.screen, self._quest_system)
        self._quest_journal = QuestJournalSystem(
            self.world, self.player_entity, self.screen, self._quest_system)
        from ui.crafting_system import BlacksmithSystem
        self._crafting_system = BlacksmithSystem(
            self.world, self.player_entity, self.screen,
            shop_system=self._shop_system,
            quest_dialog=self._quest_dialog,
            quest_system=self._quest_system,
        )
        from ui.trainer_system import TrainerSystem
        self._trainer_system = TrainerSystem(
            self.world, self.player_entity, self.screen,
            quest_dialog=self._quest_dialog,
        )
        # Empurra a escala de UI atual pra todos os sistemas de painel que
        # vivem fora de GameEngine — eles não têm acesso a self._u()/
        # self.font_* direto, então precisam de set_ui_scale() explícito
        # (ver ui_scale_mixin.py e arquitetura/PROBLEMAS_ARQUITETURA.md, IU3).
        for _sys in (self._talent_system, self._skill_level_ui, self._shop_system, self._quest_system,
                     self._quest_dialog, self._quest_journal,
                     self._crafting_system, self._trainer_system):
            _sys.set_ui_scale(self._ui_scale)
        # Insere QuestSystem após xp_system (posição 13, depois do índice de xp_system)
        _xp_idx = next((i for i, s in enumerate(self.systems)
                        if isinstance(s, XPSystem)), len(self.systems))
        self.systems.insert(_xp_idx + 1, self._quest_system)

        # Aplica stats base da classe — necessário offline (char_data) e online (char_data=None)
        char = self.world.get_component(self.player_entity, CharacterStats)
        cs   = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        from engine.stats_system import CLASS_BASE_STATS, apply_char_stats_to_combat, sync_attack_interval
        from engine.components import Equipment as _EqNew

        if char_data:
            # Novo personagem — aplica nome/classe
            if char:
                char.name     = char_data.get("name", "Aventureiro")
                char.class_id = char_data.get("class_id", "guerreiro")

        if char:
            # Atributos base por classe (online: guerreiro padrão até LOGIN_OK confirmar classe)
            _base = CLASS_BASE_STATS.get(char.class_id, CLASS_BASE_STATS["guerreiro"])
            if char_data:  # novo personagem — sempre reseta para base
                char.strength     = _base["strength"]
                char.intelligence = _base["intelligence"]
                char.agility      = _base["agility"]
                char.vitality     = _base["vitality"]
                char.defense      = _base["defense"]
            elif char.vitality <= 3:  # online sem char_data — corrige padrão mínimo
                char.strength     = _base["strength"]
                char.intelligence = _base["intelligence"]
                char.agility      = _base["agility"]
                char.vitality     = _base["vitality"]
                char.defense      = _base["defense"]

        if char and cs:
            apply_char_stats_to_combat(char, cs, perm)
            sync_attack_interval(cs, self.world.get_component(self.player_entity, _EqNew))
            cs.current_hp = cs.max_hp

        if char_data:
            # Cor do personagem por classe
            rend = self.world.get_component(self.player_entity, Renderable)
            if rend and char:
                _CLASS_COLORS = {"mago": (80, 80, 220), "arqueiro": (80, 200, 80)}
                rend.color = _CLASS_COLORS.get(char.class_id, (255, 0, 0))
            # Skills iniciais concedidas automaticamente (ex: Recarregar do arqueiro)
            if char:
                from content.skill_config import INITIAL_SKILLS_BY_CLASS, SKILL_CATALOG
                from engine.components import PlayerSkills as _PS
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
        # auto_start_quests() local removido junto do modo offline (item A2
        # §11): nenhuma quest usa auto_start=True hoje, e o desbloqueio em
        # cadeia pós-entrega roda no SERVIDOR (quest_logic.py) — semear
        # QuestLog local antes do sync só criava divergência em potencial.
        self._quest_system.set_current_map(self._current_map_file)
        self._apply_hotbar_config(new_character=bool(char_data))
        self._load_menu_keys()

        # Registra autosave global — sistemas usam request_autosave() de save_system.py
        from engine.save_system import register_autosave
        register_autosave(self._autosave)

        # Conecta ao servidor após o mundo estar pronto
        self._connect_online()
        # Passa referência de rede ao PirofagiaSystem para modo online
        self._pirofagia_system._net = self._net
        # Modo online: SkillSystem delega dano ao servidor; só aplica feedback visual
        self._skill_system._server_authoritative = True
        self._skill_system._net = self._net
        # Validação client-side agora usa RemoteEntityMeta via world — sem refs extras necessárias
        # TalentSystem: envia só os talentos ao servidor na alocação/desalocação
        self._talent_system._on_change = self._send_talent_update
        # ConsumableSystem: envia CONSUMABLE_USE ao servidor no modo online
        self._consumable_system._net = self._net
        # ManaSystem: online, servidor é autoritativo pro regen de mana — não prediz aqui
        self._mana_system._net = self._net
        # QuestSystem: online, servidor é autoritativo pro progresso/entrega de
        # quest — cliente só exibe QuestLog e envia QUEST_ACCEPT/QUEST_TURN_IN
        # (ver QuestDialogSystem.handle_events, quest_logic.py).
        self._quest_system._net = self._net
        # ShopSystem: envia BUY_REQUEST ao servidor (gold/inventário server-autoritativos)
        self._shop_system._net = self._net
        # LootSystem: envia só a consequência da ação (gold ou inventário), não o state completo
        self._loot_system._on_loot_collected = self._on_loot_action
        # LootSystem: clicar em ouro/item manda LOOT_REQUEST em vez de
        # creditar da cópia LOCAL do corpse — servidor decide o que ainda
        # sobra (bug real relatado pelo usuário 17/07/2026: grupo lootando
        # o mesmo ouro duas vezes). Ver client/save_sync_handlers.py::
        # _send_loot_request_for_local_corpse.
        self._loot_system.set_online_loot_requester(self._send_loot_request_for_local_corpse)
        # AoeTargetingSystem: envia CAST_SKILL com coordenadas ao confirmar posição AOE
        self._aoe_targeting_system._net = self._net
        # PlayerInputSystem: auto-attack ranged do arqueiro vira 100% server-driven
        # (flecha nasce só ao receber COMBAT_RESULT — igual Bola de Fogo)
        # + rage vira só display: _add_rage é no-op online (servidor empurra via STATS_UPDATE)
        self._player_input_system._net = self._net
        # CombatStateSystem: online, rage decay é server-autoritativo (rage_events)
        self._combat_state_sys._net = self._net
        # SpellCastSystem: persiste mudanças de bag/aljava (ex: Recarregar) —
        # sem isso o estado só era salvo no próximo evento de loot, e a recarga
        # se perdia se o jogador deslogasse antes disso
        self._spell_cast_system._on_inventory_changed = self._on_recarregar_changed

        # --- Profiler de frames ---
        self._prof_accum:       dict[str, float] = {}   # tempo acumulado por seção
        self._prof_peak:        dict[str, float] = {}   # pico por seção
        self._prof_frame_accum: dict[str, float] = {}   # acumulado do frame atual (spike)
        self._prof_frames: int = 0
        _PROF_INTERVAL     = 300                   # frames entre relatórios (~5 s)
        self._prof_interval = _PROF_INTERVAL
        self._prof_spike_ms = 10.0                 # ms para considerar spike em uma seção
        # ms de frame total pra disparar o breakdown imediato em [SPIKE] no log
        # (era 50ms fixo — reduzido pra pegar engasgos menores que uma trava
        # grande, rápidos demais pra screenshot manual; pedido do usuário
        # 14/07/2026, investigação de stutter ao mover o arqueiro).
        self._SPIKE_THRESHOLD_S = 0.022
        # Log de profiling gravado em arquivo — limpo a cada execução do cliente.
        import os as _os
        _log_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
        _os.makedirs(_log_dir, exist_ok=True)
        self._prof_log = open(_os.path.join(_log_dir, "client_prof.log"), "w",
                              encoding="utf-8", buffering=1)
        # Overlay de performance (F11)
        self._show_perf_overlay: bool = False
        self._perf_font = None                     # lazy-loaded em _draw_perf_overlay

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
            self.world, self.player_entity, self.screen.get_width(), self.screen.get_height()
        )
        self._zoom = self._zoom_min

    def _spawn_entities_from(self, spawn_points: dict):
        # Modo online: enemies e spawn_zones são gerenciados exclusivamente pelo servidor.
        # O cliente não cria mobs nem zonas de spawn — evita duplicação e conflito de estado.
        _online = bool(getattr(self, "_net", None))

        if not _online:
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
                    faction=zd.get("faction", "monstros_hostis"),
                )

        for col, row, name, shop_id, lvl, prof in spawn_points.get("merchants", []):
            create_merchant(self.world, col, row, name=name, shop_id=shop_id,
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
        loot_system.set_ui_scale(self._ui_scale)
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
        # Nameplate de NPC/mob local usa o MESMO objeto de fonte da janela
        # de chat (pedido do usuário 17/07/2026) — ver
        # ui/systems.py::RenderSystem.set_name_font.
        render_system.set_name_font(self.font_sm)

        self._combat_state_sys = CombatStateSystem(self.world)

        # Sistemas de magia (classe Mago)
        from ui.spell_system import (ManaSystem, SpellCastSystem, PlayerProjectileSystem,
                                  ChannelingSystem, IceBlockSystem, FireShieldSystem,
                                  PirofagiaSystem, AoeTargetingSystem)
        self._mana_system           = ManaSystem(self.world)
        self._spell_cast_system     = SpellCastSystem(self.world, self.screen)
        self._player_proj_system    = PlayerProjectileSystem(self.world, self.screen, self.player_entity)
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

        self._consumable_system = ConsumableSystem(self.world)

        # Ordem dos sistemas por frame — ALTERE COM CUIDADO.
        # As restrições de dependência são verificadas em runtime por _validate_system_order().
        # Se a ordem for violada, o jogo levanta RuntimeError na inicialização.
        self._player_input_system = PlayerInputSystem(self.world, self.screen)

        self.systems = [
            tile_validation,                                                          # 1
            self._aoe_targeting_system,                                               # 2 (antes do targeting)
            MouseTargetingSystem(self.world, self.player_entity, self.screen),        # 3
            loot_system,                                                              # 4
            self._player_input_system,                                                # 5
            skill_system,                                                             # 6
            # IA e spawn de mobs: gerenciados pelo servidor
            projectile_system,                                                        # 9
            self._player_proj_system,                                                 # 10
            self._spell_cast_system,                                                  # 11
            self._channeling_system,                                                  # 12
            self._ice_block_system,                                                   # 13
            self._fire_shield_system,                                                 # 14
            self._pirofagia_system,                                                   # 15
            self._mana_system,                                                        # 15
            death_handler,                                                            # 15
            death_respawn_system,                                                     # 16
            self._consumable_system,                                                  # 20
            self._combat_state_sys,                                                   # 21
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
        self._zoom_surf    = pygame.Surface((self.screen.get_width(), self.screen.get_height()))
        self._zoom_surf_sz = (self.screen.get_width(), self.screen.get_height())

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def _on_god_mode_save(self) -> None:
        """Chamado pelo GodModeEditor após salvar — atualiza minimap/overlay."""
        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            from engine.tileset import OBJECT_MAPPING
            self._map_overlay.load_map(
                _merge_display_matrix(tilemap_comp.terrain_matrix,
                                      tilemap_comp.object_matrix),
                self._current_map_file,
            )
            self._minimap.on_map_load()
            break

    def _autosave(self) -> None:
        """Persiste o estado atual: config local (hotbar) + SAVE_STATE ao servidor.

        O save LOCAL (saves/slot_N.json, save_game) foi removido junto do modo
        offline (15/07/2026, item A2 §11) — online, o banco do servidor é a
        única fonte de persistência de personagem; o arquivo local era
        redundante e enganoso (dava a impressão de que restaurava algo)."""
        if not self._loading_save:
            self._save_config()  # sincroniza layout da hotbar com o config local
            # Sincroniza com servidor imediatamente para não perder dados em crashes.
            # Sem isso, compras no treinador (skills, etc.) só chegam ao DB no fechamento normal.
            self._send_save_state()

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
        from content.skill_config import SKILL_CATALOG
        missing_handlers = [sid for sid in SKILL_CATALOG
                            if not hasattr(self._skill_system, f"_skill_{sid}")]
        if missing_handlers:
            print(f"[WARN] Skills sem handler em SkillSystem: {missing_handlers}")

        try:
            from content.enemy_abilities_data import ABILITY_DEFS, MOB_ABILITIES
            for mob_id, abilities in MOB_ABILITIES.items():
                for ability_id, _ in abilities:
                    if ability_id not in ABILITY_DEFS:
                        print(f"[WARN] Mob '{mob_id}' referencia ability inexistente: '{ability_id}'")
        except Exception:
            pass

    # _apply_save() (restaurar de saves/slot_N.json local) foi REMOVIDO junto
    # do modo offline (15/07/2026, item A2 §11 — já era código morto: nenhum
    # caller no fluxo online; o estado vem do WORLD_STATE do servidor).

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

    def _get_drag(self) -> "DragState | None":
        return self.world.get_component(self.player_entity, DragState)

    def _get_trade_ui(self) -> "TradeUIState | None":
        return self.world.get_component(self.player_entity, TradeUIState)

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

    @property
    def _chat_active(self) -> bool:
        if not hasattr(self, 'player_entity'):
            return False
        ui = self._get_ui()
        return ui.chat_active if ui else False

    @_chat_active.setter
    def _chat_active(self, value: bool) -> None:
        if not hasattr(self, 'player_entity'):
            return
        ui = self._get_ui()
        if ui:
            ui.chat_active = value

    @_show_talents.setter
    def _show_talents(self, value: bool) -> None:
        if not hasattr(self, 'player_entity'):
            return
        ui = self._get_ui()
        if ui:
            ui.show_talents = value

    # ------------------------------------------------------------------
    # ── UI Scale — fontes e helper de pixel ───────────────────────────────────

    _UI_FONT_BASES = {"xs": UI.FONT_XS, "sm": UI.FONT_SM, "md": UI.FONT_MD, "lg": UI.FONT_LG}

    def _reload_ui_fonts(self) -> None:
        """Recarrega font_xs/sm/md/lg no tamanho escalado. Chamado na init e ao mudar ui_scale."""
        s = self._ui_scale
        self.font_xs = _font(round(self._UI_FONT_BASES["xs"] * s))
        self.font_sm = _font(round(self._UI_FONT_BASES["sm"] * s))
        self.font_md = _font(round(self._UI_FONT_BASES["md"] * s))
        self.font_lg = _font(round(self._UI_FONT_BASES["lg"] * s))
        # Balão de fala e nameplate de NPC/mob local usam a MESMA fonte da
        # janela de chat (mesmo objeto, mesmo _ui_scale) — pedido do
        # usuário 17/07/2026, ver ui/chat_bubble.py::set_font e
        # ui/systems.py::RenderSystem.set_name_font. _render_system ainda
        # não existe na 1ª chamada (init, antes da linha que o cria) — a
        # injeção inicial roda logo após a criação (ver mais abaixo).
        CHAT_BUBBLE.set_font(self.font_sm)
        _rs = getattr(self, "_render_system", None)
        if _rs is not None:
            _rs.set_name_font(self.font_sm)

    def _u(self, px: int) -> int:
        """Converte pixels base para pixels escalados pela UI scale (ou pelo
        override de painel ativo — ver _set_panel_scale)."""
        s = self._u_scale_override if self._u_scale_override is not None else self._ui_scale
        return max(1, round(px * s))

    def _set_panel_scale(self, design_w: int, design_h: int, margin: int = 20,
                         margin_h: "int | None" = None) -> None:
        """Chamado no início de cada _draw_*/_handle_*_click de painel modal,
        ANTES de qualquer self._u(). design_w/design_h = tamanho do painel em
        pixels base (escala 1.0) — os mesmos valores passados pro primeiro
        self._u(PW)/self._u(PH) daquele painel. Limita a escala efetiva pra
        garantir que o painel nunca fique maior que a janela atual, mesmo com
        ui_scale alto numa resolução pequena. Draw e click-handler do mesmo
        painel devem chamar isso com os MESMOS design_w/design_h pra garantir
        que o hit-test bata com o que foi desenhado.

        margin = reserva HORIZONTAL (largura); margin_h = reserva VERTICAL
        (altura), default None = usa margin também (comportamento simétrico
        antigo). Separar os dois importa pra quem passa uma reserva grande
        só de largura (ex: _safe_panel_origin reservando HUD/minimapa nas
        laterais) — aplicar essa mesma reserva na ALTURA por engano fazia
        (tela_h - margin) ficar negativo numa tela 1280×720 (720 < ~775 de
        reserva), o que colapsava a escala efetiva pra negativa e, por causa
        do max(1, ...) em _u(), TODA geometria virava 1px — paineis sem caixa
        visível, só texto amontoado (bug real, visto em produção)."""
        mh = margin if margin_h is None else margin_h
        self._u_scale_override = max(0.15, min(
            self._ui_scale,
            (self.screen.get_width()  - margin) / design_w,
            (self.screen.get_height() - mh)     / design_h,
        ))

    # Largura reservada pro HUD (canto superior esquerdo) e pro minimapa
    # (canto superior direito) — painéis centralizados não podem invadir
    # essas zonas. Bases em pixel escala 1.0. HUD_SAFE_W=360 cobre a linha
    # mais larga do HUD (status de atributos "FOR:.. INT:.. AGI:.. VIT:..
    # DEF:.."), não só o bloco de barras — estimativa generosa, não medida
    # dinamicamente a partir do texto renderizado. Minimapa = Minimap.SIZE
    # (220) + MARGIN_RIGHT (10) + folga.
    _HUD_SAFE_W     = UI.HUD_SAFE_W
    _MINIMAP_SAFE_W = UI.MINIMAP_SAFE_W

    def _safe_panel_origin(self, design_w: int, design_h: int, margin: int = 20) -> "tuple[int, int]":
        """Como (SW-pw)//2 tradicional, mas centraliza o painel na área LIVRE
        da tela — excluindo as zonas reservadas pro HUD/minimapa — em vez da
        tela inteira. Chama _set_panel_scale() internamente (mesmo contrato:
        design_w/design_h = tamanho do painel em pixels base). A reserva
        escala junto com ui_scale (o HUD/minimapa também crescem com a
        escala), senão a zona livre ficaria subestimada em ui_scale alto."""
        reserved_w = self._HUD_SAFE_W + self._MINIMAP_SAFE_W + margin
        self._set_panel_scale(design_w, design_h,
                              margin=round(reserved_w * self._ui_scale),
                              margin_h=round(margin * self._ui_scale))
        SW, SH = self.screen.get_width(), self.screen.get_height()
        pw, ph = self._u(design_w), self._u(design_h)
        left   = self._u(self._HUD_SAFE_W)
        right  = self._u(self._MINIMAP_SAFE_W)
        safe_w = max(pw, SW - left - right)
        x0 = left + (safe_w - pw) // 2
        y0 = (SH - ph) // 2
        return x0, y0

    def _set_ui_scale(self, value: float) -> None:
        """Altera ui_scale, recarrega fontes e salva no config."""
        self._ui_scale = round(max(0.5, min(3.0, value)), 2)
        self._reload_ui_fonts()
        # Sistemas de painel fora de GameEngine (BlacksmithSystem, TrainerSystem,
        # ShopSystem, QuestSystem/QuestDialogSystem/QuestJournalSystem,
        # LootSystem, TalentSystem, MapOverlay) têm suas próprias fontes — não
        # reagem a _reload_ui_fonts(), precisam do push explícito abaixo.
        for _sys in (self._talent_system, self._skill_level_ui, self._shop_system, self._quest_system,
                     self._quest_dialog, self._quest_journal,
                     self._crafting_system, self._trainer_system,
                     self._loot_system, self._map_overlay, self._minimap):
            _sys.set_ui_scale(self._ui_scale)
        import config as _cfg
        _cfg.save({"ui_scale": self._ui_scale})

    # ------------------------------------------------------------------
    # ── Fechamento de modais — ESC unificado ─────────────────────────────────
    # _close_top_modal e o registro de prioridade dos modais agora vivem em
    # client/modal_stack_handlers.py (ModalStackHandlers) — único ponto de
    # verdade reaproveitado também pelo filtro de systems_events.

    def _close_modals_if_too_far(self) -> None:
        """Fecha modais de interação quando o player se afasta do elemento (NPC/corpo)."""
        from engine.components import TileMovement as _TM_prox, Position as _Pos_prox
        from engine.utils import chebyshev as _cheb_prox
        from engine.tileset import TILE_SIZE as _TS_prox

        CLOSE_DIST = 3  # fecha ao se afastar mais de 3 tiles (abre a ≤1)

        player_tm = self.world.get_component(self.player_entity, _TM_prox)
        if not player_tm:
            return
        px, py = player_tm.current_tile_x, player_tm.current_tile_y

        def _tile(eid: int):
            pos = self.world.get_component(eid, _Pos_prox)
            if pos:
                return int(pos.x / _TS_prox), int(pos.y / _TS_prox)
            tm2 = self.world.get_component(eid, _TM_prox)
            if tm2:
                return tm2.current_tile_x, tm2.current_tile_y
            return None

        # Loot
        _cid = self._loot_system.open_corpse_id
        if _cid != -1:
            t = _tile(_cid)
            if t and _cheb_prox(px, py, t[0], t[1]) > CLOSE_DIST:
                self._loot_system._close_modal()

        # Shop
        if self._shop_system.is_open:
            t = _tile(self._shop_system.open_merchant_id)
            if t and _cheb_prox(px, py, t[0], t[1]) > CLOSE_DIST:
                self._shop_system._close()

        # Crafting
        if self._crafting_system.is_open:
            t = _tile(self._crafting_system._bs_eid)
            if t and _cheb_prox(px, py, t[0], t[1]) > CLOSE_DIST:
                self._crafting_system._close()

        # Trainer
        if self._trainer_system.is_open:
            t = _tile(self._trainer_system._tr_eid)
            if t and _cheb_prox(px, py, t[0], t[1]) > CLOSE_DIST:
                self._trainer_system._close()

        # Quest dialog
        if self._quest_dialog.is_open:
            t = _tile(self._quest_dialog._dialog_npc_id)
            if t and _cheb_prox(px, py, t[0], t[1]) > CLOSE_DIST:
                self._quest_dialog._close()

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
                or getattr(self._quest_journal, "is_open", False)
                or self._quest_dialog.is_open):
            return

        # Acumula em cima do PENDENTE (não de self._zoom) se já houver um
        # debounce em andamento — senão cada clique subsequente do mesmo
        # gesto recomeçaria do valor antigo já commitado. Ver __init__
        # (_ZOOM_DEBOUNCE_S) e o tick em run() — self._zoom só é escrito lá,
        # quando o gesto de scroll termina, evitando N reconstruções caras
        # de cache por gesto (bloco "Zoom surf" em run() já invalida sozinho
        # sempre que self._zoom muda de tamanho lógico).
        base = self._zoom_pending if self._zoom_cache_dirty_timer >= 0.0 else self._zoom
        step = self._zoom_step if scroll_y > 0 else -self._zoom_step
        new_zoom = round(max(self._zoom_min, min(self._zoom_max, base + step)), 10)
        if new_zoom != base:
            self._zoom_pending           = new_zoom
            self._zoom_cache_dirty_timer = self._ZOOM_DEBOUNCE_S

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
        self._show_habilidades = False
        self._show_skills     = False
        # Cancela qualquer drag em andamento (habilidades/inventário/hotbar/
        # consumable bar) — fechar tudo inclui desistir de um drag pendente.
        # Antes da unificação em DragState, só o drag de habilidades era
        # limpo aqui; os outros 3 ficavam pendurados até o próprio painel
        # processar o próximo MOUSEUP.
        self._get_drag().reset()
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
        self._mkb_scroll         = 0

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
        """Grava relatório acumulado no log e reseta contadores."""
        n = max(self._prof_frames, 1)
        rows = sorted(self._prof_accum.items(), key=lambda x: -x[1])
        total_avg = sum(v for _, v in rows) / n * 1000
        _f = self._prof_log
        print(f"\n[PROF] {n} frames | avg frame total: {total_avg:.2f} ms", file=_f)
        print(f"  {'Seção':<30} {'avg ms':>8} {'peak ms':>9}", file=_f)
        print(f"  {'─'*50}", file=_f)
        for label, acc in rows:
            avg_ms  = acc / n * 1000
            peak_ms = self._prof_peak.get(label, 0.0) * 1000
            bar = "!" if peak_ms > self._prof_spike_ms else " "
            print(f"  {bar}{label:<29} {avg_ms:>8.3f} {peak_ms:>9.3f}", file=_f)
        self._prof_accum  = {}
        self._prof_peak        = {}
        self._prof_frames      = 0
        self._prof_frame_accum: dict[str, float] = {}   # acumulado do frame atual (spike)

    def _draw_perf_overlay(self) -> None:
        """Overlay de performance (F11): FPS + seções mais lentas do último frame."""
        import pygame as _pg
        if self._perf_font is None:
            self._perf_font = _pg.font.SysFont("Consolas,Courier New,monospace", 13)

        fps      = self.clock.get_fps()
        frame_ms = (1000.0 / fps) if fps > 0 else 0.0
        budget   = 1000.0 / FPS

        lines: list[tuple[str, tuple[int,int,int]]] = [
            (f"FPS: {fps:>5.1f}  frame: {frame_ms:>5.1f}ms  budget: {budget:.0f}ms",
             (255, 255, 80) if fps >= FPS * 0.9 else (255, 100, 60)),
        ]

        rows = sorted(self._prof_frame_accum.items(), key=lambda x: -x[1])
        for lbl, t in rows[:12]:
            ms  = t * 1000
            col = (255, 100, 60) if ms > budget * 0.5 else (200, 220, 255)
            lines.append((f"  {lbl:<32} {ms:>6.2f}ms", col))

        pad  = 6
        lh   = self._perf_font.get_linesize()
        w    = max(self._perf_font.size(l)[0] for l, _ in lines) + pad * 2
        h    = lh * len(lines) + pad * 2
        surf = _pg.Surface((w, h), _pg.SRCALPHA)
        surf.fill((0, 0, 0, 180))
        for i, (txt, col) in enumerate(lines):
            self._perf_font.render_to(surf, (pad, pad + i * lh), txt, col) \
                if hasattr(self._perf_font, "render_to") \
                else surf.blit(self._perf_font.render(txt, False, col), (pad, pad + i * lh))
        # y=110: abaixo do HUD de texto (nome+HP+recurso de classe+aljava,
        # ~90px de altura em client/hud_handlers.py::_draw_hud), ambos
        # ancorados no canto superior esquerdo — sem o offset, as duas
        # coisas desenhavam sobrepostas (texto "Conc. XX/XX (+6/s)" do HUD
        # embaralhado com as linhas do profiler). Bug relatado pelo usuário
        # 14/07/2026 (print mostrando o texto ilegível/sobreposto ao apertar
        # F11 andando com o arqueiro — só aparecia então porque a taxa de
        # regen de Concentração/"(+Xs)" só é != 0 e mostrada nessas
        # condições, mas a causa raiz é a MESMA sobreposição, com ou sem
        # arqueiro).
        self.screen.blit(surf, (4, 110))

    def run(self):
        _gc.disable()          # GC manual — evita pauses aleatórias no loop de jogo
        _gc_counter = 0
        running = True
        # Descarta o tempo acumulado durante __init__: sem este tick, o primeiro
        # dt seria o tempo total de init (2-3s), zerando _loading_min_t no frame 1.
        self.clock.tick()
        while running:
            dt = self.clock.tick_busy_loop(FPS) / 1000.0
            _gc_counter += 1
            if _gc_counter >= FPS * 10:   # coleta a cada ~10s, entre frames
                _gc.collect()
                _gc_counter = 0
            self._dt = dt
            SOUNDS.new_frame()  # limpa deduplicação de sons

            # Debounce do zoom (scroll do mouse) — ver __init__/_handle_scroll_zoom.
            # Só comita o valor final acumulado em self._zoom; NÃO chama
            # invalidate_cache() aqui — o bloco "Zoom surf: dimensiona a
            # world_surf" (mais abaixo, roda todo frame) já detecta a
            # mudança de tamanho lógico e invalida sozinho, exatamente uma
            # vez, no primeiro frame após o commit.
            if self._zoom_cache_dirty_timer >= 0.0:
                self._zoom_cache_dirty_timer -= dt
                if self._zoom_cache_dirty_timer <= 0.0:
                    self._zoom = self._zoom_pending
                    self._zoom_cache_dirty_timer = -1.0

            # ── Loading screen ────────────────────────────────────────────────
            # Mostra loading screen enquanto:
            #   • servidor ainda não respondeu (!_server_ready), OU
            #   • tempo mínimo de exibição não decorreu (_loading_min_t > 0)
            # _process_network é chamado APÓS o flip para garantir que ao menos
            # um frame de loading seja renderizado antes de LOGIN_OK ser processado
            # (em localhost o pacote chega antes do primeiro frame).
            _reconnecting = bool(self._net and getattr(self._net, "reconnecting", False))
            if _reconnecting and self._server_ready:
                # Perda de conexão após login bem-sucedido: volta para loading screen
                self._server_ready  = False
                self._loading_min_t = 0.0
                self._loading_timeout = 30.0
            _in_loading = (not self._server_ready) or (self._loading_min_t > 0)
            if _in_loading:
                self._loading_timeout -= dt
                self._loading_min_t   -= dt
                if self._loading_timeout <= 0:
                    # Timeout total: entra no jogo com personagem padrão
                    self._server_ready  = True
                    self._loading_min_t = 0.0
                else:
                    for _ev in pygame.event.get():
                        if _ev.type == pygame.QUIT:
                            self._save_config()
                            running = False
                            break
                    self._draw_loading_screen(dt)
                    self._display.blit(self.screen, (0, 0))  # screen é off-screen; precisa blit
                    pygame.display.flip()
                    self._process_network()  # processa rede APÓS renderizar o frame
                    continue
            # ─────────────────────────────────────────────────────────────────

            # Frame start: _t0 movido para antes da rede — spike window cobre TUDO.
            _t0 = _time.perf_counter()

            # Processa mensagens da rede antes de qualquer sistema (frame normal)
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._process_network()
            if PROFILE_FRAMES:
                self._prof_record("network_recv", _time.perf_counter() - _ts)

            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            events = self._scale_events(pygame.event.get())
            self._ui_events = events          # acesso sem parâmetro em _draw_* helpers
            if PROFILE_FRAMES:
                self._prof_record("events", _time.perf_counter() - _ts)

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
                    self._send_save_state()  # salva ao fechar
                    self._save_config()      # persiste layout da hotbar ao fechar
                    running = False
                elif _god_was_active:
                    pass   # god mode consumiu — ignora input do jogo
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self._chat_active:
                            self._close_chat_input()
                        elif not self._close_top_modal():
                            self._show_pause = True
                    elif self._chat_active:
                        self._handle_chat_key(event)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) \
                            and not self._any_modal_open():
                        self._open_chat_input()
                    elif self._trade_gold_focus:
                        self._handle_trade_gold_key(event)
                    elif self._show_hotbar_editor:
                        pass   # editor de hotbar aberto: bloqueia atalhos de menu
                    elif event.key == pygame.K_F10:
                        self._close_all_modals()
                        self._god_mode.toggle()
                        if self._god_mode.active and self._zoom != 1.0:
                            self._zoom = 1.0
                            self._tile_render_system.invalidate_cache()
                            self._zoom_cache_dirty_timer = -1.0  # cancela debounce pendente de scroll
                    elif event.key == pygame.K_F11:
                        self._show_perf_overlay = not self._show_perf_overlay
                        if self._show_perf_overlay:
                            globals()["PROFILE_FRAMES"] = True
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
                            if getattr(self, "_net", None):
                                self._send_save_state()
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
                    elif event.key == pygame.K_SPACE:
                        self._space_engage_online()
                    elif event.key == self._menu_keys.get("habilidades", pygame.K_h):
                        already_open = self._show_habilidades
                        self._close_all_modals()
                        if not already_open:
                            self._show_habilidades = True
                            self._hab_scroll       = 0
                    elif event.key == self._menu_keys.get("skill_level", pygame.K_l):
                        already_open = self._show_skills
                        self._close_all_modals()
                        if not already_open:
                            self._show_skills = True
                    elif event.key in (pygame.K_EQUALS, pygame.K_KP_PLUS) and not self._god_mode.active:
                        new_zoom = min(self._zoom_max, round(self._zoom + self._zoom_step, 10))
                        if new_zoom != self._zoom:
                            self._zoom = new_zoom
                            self._tile_render_system.invalidate_cache()
                            self._zoom_cache_dirty_timer = -1.0  # cancela debounce pendente de scroll
                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS) and not self._god_mode.active:
                        new_zoom = max(self._zoom_min, round(self._zoom - self._zoom_step, 10))
                        if new_zoom != self._zoom:
                            self._zoom = new_zoom
                            self._tile_render_system.invalidate_cache()
                            self._zoom_cache_dirty_timer = -1.0  # cancela debounce pendente de scroll
                    elif event.key == pygame.K_F12 and DEBUG_MODE:
                        already_open = self._show_debug
                        self._close_all_modals()
                        if not already_open:
                            self._show_debug = True
                elif event.type == pygame.TEXTINPUT:
                    self._handle_chat_text_input(event)
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and self._handle_duel_click(event)):
                    pass
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and self._handle_party_click(event)):
                    pass
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and self._handle_trade_click(event)):
                    pass
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and self._handle_chat_click(event)):
                    pass
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
                elif event.type == pygame.MOUSEWHEEL and self._handle_chat_scroll(event):
                    pass
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

            # Painel de Skill Level consome eventos quando aberto (read-only)
            if self._show_skills:
                panel = self._skill_level_ui._panel_rect()
                self._skill_level_ui.handle_events(events, panel)
                if self._skill_level_ui.wants_close:
                    self._show_skills = False

            # UI de morte/espírito: consome cliques nos botões "Liberar espírito"/"Sim"
            self._update_death_ui(events, dt)

            # Hold-to-repeat do Backspace no campo de chat (ver chat_handlers.py)
            self._update_chat_input(dt)

            # Exclusividade entre os 4 modais de interação de NPC (crafting/
            # trainer/shop/quest_dialog): se QUALQUER um já está aberto (de
            # um frame anterior), os OUTROS não podem abrir por cima dele —
            # sem isso, um right-click numa NPC diferente (ex: um trainer)
            # enquanto a loja já está aberta processa normalmente nesse
            # OUTRO sistema (que não sabe nem se importa que a loja está na
            # tela) e abre um segundo modal simultâneo. Bug real confirmado
            # por teste manual: shop aberto + clique em outro NPC abria o
            # diálogo dele também. Snapshot pego ANTES de qualquer .update()
            # deste bloco rodar neste frame — cada sistema só fica de fora
            # do silenciamento se ele MESMO já é o que está aberto (senão
            # nunca fecharia/reagiria a cliques dentro do próprio modal).
            _any_npc_modal_open = (self._crafting_system.is_open
                                   or self._trainer_system.is_open
                                   or self._shop_system.is_open
                                   or self._quest_dialog.is_open
                                   or self._trade_is_open)

            def _strip_open_click(evs, _already_open: bool):
                if not _any_npc_modal_open or _already_open:
                    return evs
                return [e for e in evs
                        if not (e.type == pygame.MOUSEBUTTONDOWN and e.button == 3)]

            # BlacksmithSystem roda ANTES de shop/quest para consumir cliques nos ferreiros
            self._crafting_system.update(
                _strip_open_click(events, self._crafting_system.is_open), dt)
            if self._crafting_system.is_open:
                self._crafting_system.handle_events(events)

            # TrainerSystem roda após crafting, antes de shop/quest
            _after_craft_ev = (
                [e for e in events
                 if not (e.type == pygame.MOUSEBUTTONDOWN and e.button == 3)]
                if self._crafting_system._right_click_consumed else events
            )
            self._trainer_system.update(
                _strip_open_click(_after_craft_ev, self._trainer_system.is_open), dt)
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
            self._shop_system.update(
                _strip_open_click(_craft_ev, self._shop_system.is_open), dt)
            if self._shop_system.is_open:
                self._shop_system.handle_events(events)

            # Quest dialog recebe eventos (possivelmente filtrados)
            self._quest_dialog.update(
                _strip_open_click(_craft_ev, self._quest_dialog.is_open), dt)
            if self._quest_dialog.is_open:
                self._quest_dialog.handle_events(events)

            # Diário de quests recebe eventos quando aberto
            if self._quest_journal.is_open:
                self._quest_journal.handle_events(events)

            # Right-click em corpo: LootSystem offline detecta via ECS (entidade Corpse
            # criada em LOOT_AVAILABLE). Não precisa de handler manual aqui.

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
                                from engine.components import PlayerAutoMove
                                for _, _auto in self.world.get_entities_with(PlayerAutoMove):
                                    _auto.ground_target = _mm_tile
                                    _auto.path          = []
                                    _auto.active        = True
                                    break
                                _minimap_click_consumed = True
                                break

            # Bloqueia sistemas enquanto um painel estiver aberto ou clique do mapa pendente.
            # _topmost_open_modal() é o único ponto de verdade de prioridade de modal
            # (client/modal_stack_handlers.py) — reaproveita a mesma ordem do ESC.
            systems_events = events
            _top_modal = self._topmost_open_modal()
            if _top_modal in ("hotbar_editor", "shop_qty"):
                # Editor/modal de quantidade abertos: sistemas não recebem NENHUM
                # evento de input (handle_events desses dois recebe raw events
                # separadamente, fora deste filtro).
                systems_events = []
            elif _top_modal is not None:
                # Qualquer outro modal aberto: bloqueia clique, tecla E movimento de
                # mouse pros sistemas ECS. Antes só MOUSEBUTTONDOWN era bloqueado —
                # KEYDOWN/MOUSEMOTION vazavam e disparavam gameplay (TAB cicla alvo,
                # SPACE engaja combate, teclas de hotbar usam skill) com modal aberto.
                _STRIP_TYPES = (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                                pygame.KEYDOWN, pygame.MOUSEMOTION, pygame.MOUSEWHEEL)
                systems_events = [e for e in events if e.type not in _STRIP_TYPES]
            elif (_minimap_click_consumed
                    or self._map_overlay.pending_destination is not None
                    or self._crafting_system._right_click_consumed
                    or self._trainer_system._right_click_consumed
                    or self._shop_system._right_click_consumed
                    or self._quest_dialog._right_click_consumed):
                # Nenhum modal aberto ainda, mas um right-click já foi consumido
                # neste frame pra resolver prioridade entre NPCs adjacentes (ou
                # pelo minimap) — bloqueia só esse clique, não é um modal de fato.
                systems_events = [e for e in events
                                  if not (e.type == pygame.MOUSEBUTTONDOWN
                                          and e.button in (1, 3))]
            _inv_was_open_before   = self._show_inventory
            _shop_was_open_before  = self._shop_system.is_open
            _loot_was_open_before  = self._loot_system.open_corpse_id != -1
            _ps_before = self.world.get_component(self.player_entity, PlayerSkills)
            _learned_count_before  = len(_ps_before.learned_skill_ids) if _ps_before else 0
            for system in self.systems:
                # LootSystem sempre recebe eventos brutos (precisa detectar cliques no modal)
                ev = events if system is self._loot_system else systems_events
                if PROFILE_FRAMES:
                    _ts = _time.perf_counter()
                    system.update(ev, dt)
                    self._prof_record(f"upd:{type(system).__name__}", _time.perf_counter() - _ts)
                else:
                    system.update(ev, dt)

            # Mobs mortos com animação em andamento: remove entidade ao terminar passo
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._flush_pending_mob_despawns(dt)

            # Projéteis que colidiram com mobs online: envia PROJECTILE_HIT_CS ao servidor
            if self._net and self._player_proj_system.pending_proj_hits:
                from shared.messages import MsgType as _MT_ph
                for _hit in self._player_proj_system.pending_proj_hits:
                    self._net.send(_MT_ph.PROJECTILE_HIT_CS, {
                        "spell_id":  _hit["spell_id"],
                        "target_id": _hit["target_server_id"],
                    })
                self._player_proj_system.pending_proj_hits.clear()

            # HP updates diferidos de flechas: aplica quando projétil colide (não no COMBAT_RESULT)
            if self._player_proj_system.deferred_hp_updates:
                for _srv_u, _hp_u, _mx_u in self._player_proj_system.deferred_hp_updates:
                    _meta_u = self._meta(_srv_u)
                    if _meta_u:
                        _meta_u.hp     = _hp_u
                        _meta_u.hp_max = _mx_u if _mx_u >= 0 else _meta_u.hp_max
                self._player_proj_system.deferred_hp_updates.clear()

            # Channeling interrompido: notifica servidor para parar os ticks de dano
            if self._net and self._channeling_system.interrupted_channelings:
                from shared.messages import MsgType as _MT_ch
                for _ch_sid in self._channeling_system.interrupted_channelings:
                    self._net.send(_MT_ch.CANCEL_CAST, {"sid": _ch_sid})
                self._channeling_system.interrupted_channelings.clear()

            # Casts cancelados por movimento: notifica servidor para remover da fila
            if self._net and self._spell_cast_system.interrupted_visual_casts:
                from shared.messages import MsgType as _MT_cc
                for _cc_sid in self._spell_cast_system.interrupted_visual_casts:
                    self._net.send(_MT_cc.CANCEL_CAST, {"sid": _cc_sid})
                    self._cancelled_spell_ids.add(_cc_sid)
                self._spell_cast_system.interrupted_visual_casts.clear()

            # Direção final de skills direcionais (ex: tiro_multiplo): atualiza servidor
            # com a direção do mouse no momento da conclusão do cast (não do início).
            if self._net and self._spell_cast_system.pending_dir_updates:
                from shared.messages import MsgType as _MT_du
                for _du_sid, _du_dx, _du_dy in self._spell_cast_system.pending_dir_updates:
                    self._net.send(_MT_du.CAST_DIR_UPDATE,
                                   {"sid": _du_sid, "dir_x": _du_dx, "dir_y": _du_dy})
                self._spell_cast_system.pending_dir_updates.clear()

            # Sincronização online: movimento + alvo de combate + fila de mobs
            self._send_player_move()
            self._sync_combat_target()
            self._process_mob_move_queues()
            self._ensure_remote_mobs_visible()
            # Fecha modais quando o player se afasta do elemento
            self._close_modals_if_too_far()
            # Autosave local a cada 2 minutos (7200 frames @ 60fps)
            if not hasattr(self, "_save_frame_counter"):
                self._save_frame_counter = 0
            self._save_frame_counter += 1
            if self._save_frame_counter >= 7200:
                self._save_frame_counter = 0
                self._send_save_state()

            # Aprender nova skill: salva só as skills
            _ps_after = self.world.get_component(self.player_entity, PlayerSkills)
            _learned_count_after = len(_ps_after.learned_skill_ids) if _ps_after else 0
            if _learned_count_after > _learned_count_before:
                self._send_hotbar_update()  # hotbar pode ter mudado com nova skill

            # Detecta mudanças de equipamento (equip/unequip de qualquer fonte, incluindo
            # loot direto) e sincroniza com servidor para manter validação server-side correta
            # (bow+quiver para auto-attack/skills do arqueiro, etc.).
            _new_equip = self._get_equip_snapshot()
            if _new_equip != self._equip_snapshot:
                self._send_equip_sync()
                self._equip_snapshot = _new_equip

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
                    from engine.components import PlayerAutoMove
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
                from engine.components import PlayerAutoMove
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
                        if self._net:
                            from shared.messages import MsgType as _MTzc
                            trans = self.transition_tiles[key]
                            self._net.send(_MTzc.ZONE_CHANGE_REQ, {
                                "to_map":   trans["target_map"],
                                "target_x": trans["target_x"],
                                "target_y": trans["target_y"],
                            })
                            self._transition_cooldown = 2.0
                        else:
                            self._do_transition(self.transition_tiles[key])

            if self._map_title_timer > 0:
                self._map_title_timer -= dt
            if PROFILE_FRAMES:
                self._prof_record("online_sync", _time.perf_counter() - _ts)

            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            FLT.update(dt)
            PROC.update(dt)
            WARN.update(dt)
            DASH_TRAIL.update(dt)
            SOUNDS.update(dt)
            CHAT_BUBBLE.update(dt)
            # Decrementa timers de passo de players remotos
            for _seid in list(self._remote_step_timers):
                self._remote_step_timers[_seid] = max(0.0, self._remote_step_timers[_seid] - dt)
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
            dest_w   = self.screen.get_width() - _panel_w          # largura da área de jogo
            lw       = max(1, int(dest_w   / z))        # largura lógica do mundo
            lh       = max(1, int(self.screen.get_height() / z))   # altura  lógica do mundo
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
            self._draw_remote_corpses(cam_x, cam_y)
            self._render_system.render(cam_x, cam_y, world_objects=_world_objs)
            self._shop_system.render_world(cam_x, cam_y)
            self._quest_dialog.render_world(cam_x, cam_y, self._zoom)
            self._crafting_system.render_world(cam_x, cam_y)
            self._trainer_system.render_world(cam_x, cam_y)
            self._draw_remote_players(cam_x, cam_y)
            self._draw_mob_hp_bars(cam_x, cam_y)
            self._tile_render_system.render_fog()
            self._projectile_system.render(cam_x, cam_y)
            self._player_proj_system.render(cam_x, cam_y)
            self._channeling_system.render(cam_x, cam_y)
            self._aoe_targeting_system.render(cam_x, cam_y)
            self._pirofagia_system.render(cam_x, cam_y)
            self._spell_cast_system.render(cam_x, cam_y)
            # FLT (floating text) NÃO renderiza mais aqui (world-space, pré-
            # zoom) — movido pra screen-space, DEPOIS do WORLD_LABELS (ver
            # comentário em FLT.render() e no ponto de chamada abaixo).
            # Pedido do usuário 15/07/2026: floating text ficava atrás dos
            # nameplates.
            CHAT_BUBBLE.render(self.world)

            # Morto/espírito: grayscale no zoom_surf menor (pré-scale) — ~44% menos
            # pixels a zoom=1.5 vs aplicar na tela cheia (853×480 vs 1280×720).
            _gst_gray = self.world.get_component(self.player_entity, GhostState)
            if _gst_gray is not None and _gst_gray.is_dead:
                if PROFILE_FRAMES:
                    _ts = _time.perf_counter()
                _zsz = self._zoom_surf.get_size()
                if self._ghost_gray_buf is None or self._ghost_gray_buf.get_size() != _zsz:
                    self._ghost_gray_buf = pygame.Surface(_zsz)
                pygame.transform.grayscale(self._zoom_surf, self._ghost_gray_buf)
                self._zoom_surf.blit(self._ghost_gray_buf, (0, 0))
                if PROFILE_FRAMES:
                    self._prof_record("ghost_gray", _time.perf_counter() - _ts)
            # ── Escala world_surf → área de jogo na tela nativa (pixel-perfect) ─
            # subsurface evita alocação extra e resolve o caso do painel do God Mode
            # (dest_w < self.screen.get_width() quando o painel está aberto).
            _dest = self.screen.subsurface((0, 0, dest_w, self.screen.get_height()))
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            _zh, _zw = self._zoom_surf.get_height(), self._zoom_surf.get_width()
            if _zw == dest_w and _zh == self.screen.get_height():
                _dest.blit(self._zoom_surf, (0, 0))
            else:
                pygame.transform.scale(self._zoom_surf, (dest_w, self.screen.get_height()), _dest)
            if PROFILE_FRAMES:
                self._prof_record("transform_scale", _time.perf_counter() - _ts)
            # Nomes de NPC/mob + ícone de quest: screen-space, DEPOIS do
            # scale acima — nascem no tamanho final de tela, nunca são
            # reamostrados pelo zoom da câmera (fix 11/07/2026: fonte
            # pixel-perfect borrava quando desenhada dentro do world_surf).
            from ui.world_labels import WORLD_LABELS
            WORLD_LABELS.render(self.screen, cam_x, cam_y, z)
            # Floating text em screen-space, DEPOIS do WORLD_LABELS (nameplates)
            # — pedido do usuário 15/07/2026: antes ficava atrás dos nameplates
            # porque renderizava em world-space (self._zoom_surf), antes do
            # flatten+scale. FLT.render() agora recebe `z` (zoom) e converte
            # coordenadas de mundo pra tela internamente.
            FLT.render(self.screen, cam_x, cam_y, z)
            # Notificações de proc: screen-space, abaixo do player, acima dos avisos
            PROC.render(self.screen)
            # Avisos de ação bloqueada: posição fixa, abaixo do centro
            WARN.render(self.screen)
            # Vinheta vermelha pulsante quando HP < 30%
            self._draw_low_hp_vignette()
            # HUD de conexão online (canto superior direito)
            self._draw_online_hud()
            # Chat: log (canto inferior esquerdo) + campo de digitação quando focado
            self._draw_chat_log()

            self._pending_tooltip       = None
            self._pending_skill_tooltip = None
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._draw_hud()
            self._render_death_ui()
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
            # Minimapa (canto superior direito) — oculto enquanto mapa grande estiver aberto
            if not self._map_overlay.is_open:
                _fog_mm      = self.world.get_component(self.player_entity, FogOfWar)
                _player_tm_m = self.world.get_component(self.player_entity, TileMovement)
                if _fog_mm and _player_tm_m:
                    _enemy_tiles = [
                        (etm.current_tile_x, etm.current_tile_y)
                        for _, _, _, etm in self.world.get_entities_with(Enemy, Visible, TileMovement)
                        if (etm.current_tile_x, etm.current_tile_y) in _fog_mm.visible
                    ]
                    from ui.map_markers import collect_markers
                    self._minimap.render(
                        _player_tm_m.current_tile_x,
                        _player_tm_m.current_tile_y,
                        _fog_mm.explored,
                        _fog_mm.visible,
                        _enemy_tiles,
                        collect_markers(self.world, self.player_entity, self._quest_dialog),
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
            self._draw_trade_ui()
            self._draw_duel_ui()
            self._draw_party_frames()
            self._draw_party_invite_ui()
            if self._show_talents:
                self._talent_system.render()
            if self._show_skills:
                self._skill_level_ui.render()
            if self._show_hotbar_editor:
                self._draw_hotbar_editor(events)
            _drag_render = self._get_drag()
            if (self._show_habilidades
                    or (_drag_render.kind == "skill" and _drag_render.source == "habilidades")):
                self._draw_habilidades_panel(events)
                # Redesenha hotbar POR CIMA do overlay escuro do painel de habilidades,
                # para que os slots fiquem visíveis e acessíveis durante o drag-to-bar.
                self._draw_hotbar()
                self._draw_consumable_bar()
                # Ghost de drag por cima de tudo (incluindo os slots redesenhados acima).
                self._draw_hab_drag_ghost()
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
                from ui.map_markers import collect_markers
                self._map_overlay.render(tx, ty,
                                         explored=_fog_comp.explored if _fog_comp else None,
                                         markers=collect_markers(self.world, self.player_entity,
                                                                 self._quest_dialog))

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
                    self._mkb_scroll         = 0
                elif action == "unstuck":
                    self._show_pause    = False
                    self._pause_submenu = ""
                    if self._net:
                        from shared.messages import MsgType as _MT_us
                        self._net.send(_MT_us.UNSTUCK, {})
                elif action and action.startswith("resolution:"):
                    self._apply_scale(float(action.split(":")[1]))

            # God Mode: por cima de tudo (grade + seleção + painel)
            self._god_mode.update(cam_x, cam_y, dt)
            self._god_mode.render(cam_x, cam_y)

            # Overlay de performance (F11) — por cima de tudo, antes do flip
            if self._show_perf_overlay:
                self._draw_perf_overlay()

            # Copia surface interna → janela (resolução nativa, sem escala)
            if PROFILE_FRAMES:
                _ts = _time.perf_counter()
            self._display.blit(self.screen, (0, 0))
            pygame.display.flip()
            if PROFILE_FRAMES:
                self._prof_record("display_flip", _time.perf_counter() - _ts)
                # Detecção de spike: imprime breakdown imediato se o frame demorou
                # mais que _SPIKE_THRESHOLD_S — reduzido de 50ms pra 22ms (budget
                # é 16.7ms a 60 FPS; 50ms só pegava travadas grandes, não os
                # engasgos menores que o usuário reportou sentir andando com o
                # arqueiro, rápido demais pra capturar num screenshot manual —
                # pedido do usuário 14/07/2026: "grave isso num log pra
                # analisar"). Contexto do player (classe/is_moving/is_pursuing/
                # cooldown de ataque) incluso na linha do spike pra correlacionar
                # direto com "estava andando/perseguindo como arqueiro" sem
                # precisar cruzar timestamp a mão.
                _frame_elapsed = _time.perf_counter() - _t0
                if _frame_elapsed > self._SPIKE_THRESHOLD_S:
                    _fms = _frame_elapsed * 1000
                    _f = self._prof_log
                    _ctx = ""
                    try:
                        _cs_spk  = self.world.get_component(self.player_entity, CharacterStats)
                        _tm_spk  = self.world.get_component(self.player_entity, TileMovement)
                        _cst_spk = self.world.get_component(self.player_entity, CombatState)
                        _cbs_spk = self.world.get_component(self.player_entity, CombatStats)
                        _parts = [
                            f"class={_cs_spk.class_id if _cs_spk else '?'}",
                            f"is_moving={_tm_spk.is_moving if _tm_spk else '?'}",
                            f"is_pursuing={_cst_spk.is_pursuing if _cst_spk else '?'}",
                        ]
                        if _cbs_spk:
                            _parts.append(f"atk_cd={_cbs_spk.attack_cooldown_timer:.2f}")
                        _ctx = " | " + " ".join(_parts)
                    except Exception:
                        pass
                    print(f"\n[SPIKE] {_fms:.0f}ms{_ctx} — breakdown do frame:", file=_f)
                    _spike_rows = sorted(self._prof_frame_accum.items(), key=lambda x: -x[1])
                    for _lbl, _lt in _spike_rows:
                        if _lt > 0.002:
                            print(f"  {'>>':2} {_lbl:<38} {_lt*1000:>7.1f}ms", file=_f)
                self._prof_frame_accum = {}
                self._prof_frames += 1
                if self._prof_frames >= self._prof_interval:
                    self._prof_report()

        self._prof_log.close()
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

        # Limpa caches de entidades remotas — os local_eids foram destruídos acima
        self._remote_mobs.clear()
        self._remote_players.clear()
        self._pending_mob_despawn.clear()
        self._mob_ghost_pos.clear()

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
        surf = self.font_lg.render(text, False, (255, 200, 0))
        x = self.screen.get_width()  // 2 - surf.get_width()  // 2
        y = self.screen.get_height() // 2 - surf.get_height() // 2
        # fundo semitransparente
        pad = 16
        self.screen.blit(fill_surf((surf.get_width() + pad * 2, surf.get_height() + pad * 2), (0, 0, 0, 160)), (x - pad, y - pad))
        self.screen.blit(surf, (x, y))

    # ------------------------------------------------------------------
    def _load_menu_keys(self) -> None:
        """Carrega atalhos de menu do config.json para self._menu_keys."""
        import config as _cfg
        from config import DEFAULTS
        defaults = DEFAULTS.get("menu_keybinds", {})
        saved    = _cfg.load().get("menu_keybinds", {})
        self._menu_keys = {**defaults, **saved}

    def _save_config(self) -> None:
        """Persiste configurações em config.json. Hotbar/consumable são salvos por personagem."""
        import config as _cfg
        data = {
            "scale":          self._scale,
            "music_volume":   SOUNDS.music_volume,
            "sfx_volume":     SOUNDS.sfx_volume,
            "music_enabled":  SOUNDS.music_enabled,
            "sfx_enabled":    SOUNDS.sfx_enabled,
            "debug_mode":     DEBUG_MODE,
            "profile_frames": PROFILE_FRAMES,
            "menu_keybinds":  self._menu_keys,
        }
        if self._logged_char_name:
            existing = _cfg.load()
            chars = existing.get("characters", {})
            chars[self._logged_char_name] = {
                "hotbar":         self._hotbar_to_dict(),
                "consumable_bar": self._consumable_bar_to_dict(),
            }
            data["characters"] = chars
        else:
            data["hotbar"]         = self._hotbar_to_dict()
            data["consumable_bar"] = self._consumable_bar_to_dict()
        _cfg.save(data)
        self._load_menu_keys()
        # Sincroniza barra de ações com o servidor (só hotbar, não o state completo)
        if self._logged_char_name:
            self._send_hotbar_update()

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
        from engine.components import ConsumableBar as _CB
        cbar = self.world.get_component(self.player_entity, _CB)
        if not cbar:
            return {}
        return {
            "slots":    list(cbar.slots),
            "keybinds": list(cbar.keybinds),
        }

    def _apply_hotbar_config(self, new_character: bool = False, char_name: str = "") -> None:
        """Restaura layout e keybinds da hotbar e barra de consumíveis a partir de config.json."""
        import config as _cfg
        from content.skill_config import SKILL_CATALOG, NUM_SLOTS, DEFAULT_KEYBINDS
        data = _cfg.load()

        # Per-character lookup: prefer config["characters"][char_name] over legacy top-level
        if char_name:
            _char_cfg = data.get("characters", {}).get(char_name, {})
            hb      = _char_cfg.get("hotbar")      or data.get("hotbar")
            cb_data = _char_cfg.get("consumable_bar") or data.get("consumable_bar")
        else:
            hb      = data.get("hotbar")
            cb_data = data.get("consumable_bar")

        # ── Skills hotbar ────────────────────────────────────────────────
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
        if cb_data:
            from engine.components import ConsumableBar as _CB
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
        self._scale   = scale
        win_w         = int(1280 * scale)
        win_h         = int(720  * scale)
        self.screen   = pygame.Surface((win_w, win_h))
        # SCALED + vsync=1: mesma razão do set_mode() em __init__ — ver
        # comentário lá.
        self._display = pygame.display.set_mode(
            (win_w, win_h), pygame.DOUBLEBUF | pygame.SCALED, vsync=1)
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
        if hasattr(self._skill_level_ui, "screen"):
            self._skill_level_ui.screen = new_screen
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

