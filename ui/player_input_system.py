# player_input_system.py
"""
Extraido de ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md item #2
do backlog de 14, "ui/systems.py monolitico") - candidato em revisao
multiagente antes de aplicar de verdade.
"""
from __future__ import annotations
import pygame
import math
import heapq
import random
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI
from ui.world_labels import WORLD_LABELS
from ui.hud_bars import (build_player_hud, build_mob_hud, build_npc_badge, build_simple_hp_bar,
                       RESOURCE_COLORS, HUD_GAP_PX, effects_row_offset)

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import (apply_effect, StatusEffectSystem as _CoreStatusEffectSystem,
                          BaseCombatStateSystem as _BaseCombatStateSystem)
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, Modifier, CombatState, PlayerAutoMove, \
                       Projectile, Corpse, Inventory, EnemyTier, Equipment, Wallet, Merchant, \
                       CharacterStats, FogOfWar, Visible, ActiveEffect, StatusEffects, \
                       EnemyAbilities, EnemyAbilitySlot, EntityIdentity, \
                       NpcSounds, PendingDeath, XPReward, SpawnZoneOwner, SpawnZone, \
                       PlayerSkills, NPC, ActiveRegen, ConsumableBar, \
                       AoeTargeting, RemoteControlled, GhostState, MapLocation, Minion, \
                       NPCInteractionRequest
from engine.world import World
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
from engine.utils import chebyshev, in_range, start_tile_movement, get_camera_offset, is_target_alive
from engine.damage_calculator import resolve_attack_outcome, calculate_base_damage
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from engine.entity_disguise import get_active_appearance_override, get_animated_disguise_frame
from engine.character_animation import (get_character_frame, resolve_display_state,
                                        character_anchor_offset)
from ui.combat_log import LOG
from ui.sound_manager import SOUNDS
from ui.floating_text import FLT, PROC, WARN
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines, fill_surf
from ui.tile_sprite_manager import TILE_SPRITES
from ui.npc_interaction import walk_to_npc, suppress_chase
from content.status_effects_data import EFFECT_DEFS
from engine.fov import compute_fov, local_vision_blob
from content.enemy_abilities_data import ABILITY_DEFS
from content.merchant_data import SHOPS, INSTANCE_SHOP_STOCK_BY_CLASS
from content.item_table import RARITY_COLORS as _SHARED_RARITY_COLORS
import engine.quest_events as quest_events
from engine.quest_events import fire as quest_fire
from engine.stat_fns import add_modifier, remove_modifier, add_timed_modifier, enter_combat

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.
from engine.world_systems import _TALENT_SKILL_REQS as _TALENT_SKILL_REQ_SYS



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import (
    System, PathfindingSystem, TileValidationSystem, CombatSystem,
    DeathHandlerSystem, CombatStateSystem, ProjectileSystem, EnemyAISystem,
    TileMovementSystem, StatusEffectSystem, EnemyAbilitySystem, CorpseSystem,
    SpawnZoneSystem,
    _svc, register_services, deal_damage, find_path, get_tilemap,
    is_tile_walkable, get_mainhand_weapon,
)
from ui.click_rect_helpers import _corpse_click_rect


def _entity_click_rect(world: World, entity_id: int, pos,
                       renderable) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) da área clicável de uma entidade VIVA
    (mob/player) — ponto único de verdade, 15/09/2026 (item 5 da revisão
    de performance, arquitetura/CLIENT_PERFORMANCE_REVIEW.md). Usa o
    sprite REAL do catálogo (`TILE_SPRITES`) quando `renderable.sprite_id`
    existe (torre, mob com sprite fixo) — mesma âncora de base que
    `RenderSystem.render()` usa pra desenhar (12/08/2026, bug real:
    clique não acertava sprite grande de torre, só um retângulo pequeno
    de sempre). Cai pro retângulo simples (`renderable.width/height`)
    quando não há sprite no catálogo (players, que desenham via
    animação de personagem — `engine/character_animation.py` — nunca
    via `Renderable.sprite_id`).

    Antes desta função, `MouseTargetingSystem._enemy_at_world_pos` já
    fazia essa conta certa (sprite real), mas `_live_target_at_world_pos`
    (abaixo) e `_remote_player_at_world_pos`
    (`MouseTargetingSystem`) reimplementavam só o retângulo simples —
    mob/torre grande podia ficar clicável num caminho e não no outro.
    NUNCA usada pra corpse — `_corpse_click_rect` (acima) é caso à parte,
    com tolerância pequena deliberada pra quando não há sprite."""
    spr = TILE_SPRITES.get_raw_sprite(renderable.sprite_id) if renderable.sprite_id else None
    if spr:
        sw, sh = spr.get_size()
        left   = pos.x - TILE_SIZE / 2
        right  = left + sw
        bottom = pos.y + TILE_SIZE / 2
        top    = bottom - sh
        return left, top, right, bottom
    hw = renderable.width / 2
    hh = renderable.height / 2
    return pos.x - hw, pos.y - hh, pos.x + hw, pos.y + hh


def _live_target_at_world_pos(world: World, player_entity_id: int,
                              world_x: float, world_y: float) -> bool:
    """True se há um inimigo vivo OU player remoto vivo na posição mundo
    (mesma bounding box de `_entity_click_rect`/`MouseTargetingSystem.
    _enemy_at_world_pos`/`_remote_player_at_world_pos` — LootSystem e
    MouseTargetingSystem são Systems independentes no mesmo frame, sem
    referência uma à outra, então cada um chama a função compartilhada
    em vez de acoplar).

    Usado por LootSystem pra NUNCA competir pelo clique quando um alvo
    (NPC/Player/Mob) ocupa o mesmo tile de um cadáver — bug real relatado
    pelo usuário (03/08/2026): clique direito num inimigo em cima de um
    corpo com loot fazia o personagem CAMINHAR PRO LOOT em vez de atacar
    (LootSystem._try_open_corpse rodava sem checar se havia um alvo vivo
    ali, e até cancelava o CombatState.target_entity_id que
    MouseTargetingSystem já tinha setado no MESMO evento). Prioridade de
    clique pedida pelo usuário: alvo (NPC/Player/Mob) sempre antes de
    loot.

    Checa FogOfWar.visible pro Enemy (15/09/2026 — antes não checava,
    então um inimigo escondido no fog ainda "roubava" o clique do loot;
    RemoteControlled nunca é fog-gated, mesma regra de
    RenderSystem.render(), player real nunca some por fog)."""
    _fog_vis = None
    _fow_first = world.get_first(FogOfWar)
    if _fow_first is not None:
        _fog_vis = _fow_first[1].visible
    for entity_id, pos, renderable, _, _, etm in world.get_entities_with(
            Position, Renderable, Enemy, Visible, TileMovement):
        if _fog_vis is not None and \
                (etm.current_tile_x, etm.current_tile_y) not in _fog_vis:
            continue
        left, top, right, bottom = _entity_click_rect(world, entity_id, pos, renderable)
        if left <= world_x <= right and top <= world_y <= bottom:
            cs = world.get_component(entity_id, CombatStats)
            if not cs or cs.current_hp > 0:
                return True
    for entity_id, pos, renderable, _, rc in world.get_entities_with(
            Position, Renderable, Visible, RemoteControlled):
        if entity_id == player_entity_id or rc.hp <= 0:
            continue
        left, top, right, bottom = _entity_click_rect(world, entity_id, pos, renderable)
        if left <= world_x <= right and top <= world_y <= bottom:
            return True
    return False



class MouseTargetingSystem(System):
    """
    Detecta clique direito do mouse, identifica o inimigo clicado e define
    CombatState.target_entity_id do jogador. Também ativa PlayerAutoMove.
    """

    def __init__(self, world: World, player_entity_id: int, screen: pygame.Surface):
        self.world = world
        self.player_entity_id = player_entity_id
        self.world_surf = screen
        self.hud_surf   = screen

    def _get_camera_offset(self) -> tuple:
        return get_camera_offset(self.world, self.world_surf.get_width(), self.world_surf.get_height())

    def _enemy_at_world_pos(self, world_x: float, world_y: float) -> int:
        """Retorna o entity_id do inimigo vivo e visível na posição mundo, ou -1.

        Área clicável via `_entity_click_rect()` (ponto único de verdade,
        sprite real do catálogo quando existe — ver docstring lá)."""
        _fog_vis = None
        _fow_first = self.world.get_first(FogOfWar)
        if _fow_first is not None:
            _fog_vis = _fow_first[1].visible
        for entity_id, pos, renderable, _, _, etm in self.world.get_entities_with(
                Position, Renderable, Enemy, Visible, TileMovement):
            if _fog_vis is not None and \
                    (etm.current_tile_x, etm.current_tile_y) not in _fog_vis:
                continue
            left, top, right, bottom = _entity_click_rect(self.world, entity_id, pos, renderable)
            if left <= world_x <= right and top <= world_y <= bottom:
                cs = self.world.get_component(entity_id, CombatStats)
                if not cs or cs.current_hp > 0:
                    return entity_id
        return -1

    def _remote_player_at_world_pos(self, world_x: float, world_y: float) -> int:
        """Retorna o entity_id local de um player remoto clicado, ou -1.

        Usado para PvP: permite selecionar outros players como alvo.
        A verificação de zona PvP fica no servidor — o cliente só faz targeting.
        """
        for entity_id, pos, renderable, _, _rc in self.world.get_entities_with(
                Position, Renderable, Visible, RemoteControlled):
            if entity_id == self.player_entity_id:
                continue  # não seleciona a si mesmo
            if _rc.hp <= 0:
                continue  # player morto
            left, top, right, bottom = _entity_click_rect(self.world, entity_id, pos, renderable)
            if left <= world_x <= right and top <= world_y <= bottom:
                return entity_id
        return -1

    def _corpse_at_world_pos(self, world_x: float, world_y: float) -> bool:
        """Retorna True se há um cadáver na posição mundo."""
        for entity_id, pos, _ in self.world.get_entities_with(Position, Corpse):
            left, top, right, bottom = _corpse_click_rect(self.world, entity_id, pos)
            if left <= world_x <= right and top <= world_y <= bottom:
                return True
        return False

    def _visible_enemies_sorted(self, cam_x: float, cam_y: float) -> list[int]:
        """Retorna IDs de inimigos vivos e visíveis na tela, ordenados por
        distância ao jogador.

        Mobs/torres/minions (`Enemy`) usam `can_engage` (hostil OU
        neutro, só bloqueia "amigavel") — antes usava `is_hostile` (só
        hostil de verdade, decisão do usuário de 01/08/2026), mas isso
        foi ANTES da jungle existir (tier "neutro", 16/08/2026); usuário
        pediu (25/08/2026) que TAB alcance alvo neutro também (ex: mob
        de jungle). MESMA função que o loop de player remoto abaixo já
        usa — nenhuma lógica nova. Antes da checagem de hostilidade
        (quando ainda era `is_hostile`) a volta de `Enemy` (torres/
        minions/NPCs aliados também carregam esse componente do lado do
        cliente, `create_enemy` genérico) não filtrava hostilidade
        NENHUMA — bug real relatado pelo usuário: TAB podia ciclar pra
        um minion/torre do PRÓPRIO time.

        Players remotos (`RemoteControlled`) continuam usando
        `can_engage` (não `is_hostile`) DE PROPÓSITO — permissão de PvP
        entre players é CONTEXTUAL (duelo/arena/zona), não uma tier fixa
        de facção; um oponente de duelo normalmente está na MESMA facção
        default ("jogadores" = amigavel), só o contexto plugável libera o
        engajamento (`_pvp_context_resolver`, engine/faction_system.py) —
        trocar pra `is_hostile` aqui quebraria TAB durante duelo/arena
        (nunca resolveria hostil por tier de facção). `can_engage` já
        bloqueia "amigavel" (aliado de verdade), que é o que o pedido do
        usuário pede pra players também."""
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        player_pos = self.world.get_component(self.player_entity_id,
                                              Position)
        from engine.faction_system import can_engage as _can_engage_tab_mob
        result = []
        for eid, pos, _, _ in self.world.get_entities_with(Position, Enemy, Visible):
            cs = self.world.get_component(eid, CombatStats)
            if cs and cs.current_hp <= 0:
                continue
            if not _can_engage_tab_mob(self.world, self.player_entity_id, eid):
                continue
            # Verifica se está dentro dos limites da tela
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            if 0 <= sx <= sw and 0 <= sy <= sh:
                dist = (pos.x - player_pos.x) ** 2 + (pos.y - player_pos.y) ** 2 if player_pos else 0
                result.append((dist, eid))

        from engine.faction_system import can_engage as _can_engage_tab
        for eid, pos, _viz, _rc in self.world.get_entities_with(Position, Visible, RemoteControlled):
            if eid == self.player_entity_id or _rc.hp <= 0:
                continue
            if not _can_engage_tab(self.world, self.player_entity_id, eid):
                continue
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            if 0 <= sx <= sw and 0 <= sy <= sh:
                dist = (pos.x - player_pos.x) ** 2 + (pos.y - player_pos.y) ** 2 if player_pos else 0
                result.append((dist, eid))

        result.sort()
        return [eid for _, eid in result]

    def _cycle_tab_target(self) -> None:
        """Seleciona o próximo inimigo visível ao pressionar TAB."""
        cam_x, cam_y = self._get_camera_offset()
        enemies = self._visible_enemies_sorted(cam_x, cam_y)
        if not enemies:
            return

        player_cs = self.world.get_component(self.player_entity_id,
                                             CombatState)
        if not player_cs:
            return

        current = player_cs.target_entity_id
        if current in enemies:
            idx = (enemies.index(current) + 1) % len(enemies)
        else:
            idx = 0

        player_cs.target_entity_id = enemies[idx]
        player_cs.is_pursuing = False  # TAB só seleciona, não persegue

    def update(self, events: list = None, dt: float = 0) -> None:
        if not events:
            return
        for event in events:
            # --- TAB: cicla entre inimigos visíveis ---
            if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
                self._cycle_tab_target()
                continue

            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            if event.button not in (1, 3):
                continue

            # Ignora cliques enquanto qualquer modo de mira estiver ativo
            from engine.components import PirofagiaAiming as _PA
            if (self.world.get_component(self.player_entity_id, AoeTargeting) or
                    self.world.get_component(self.player_entity_id, _PA)):
                continue

            cam_x, cam_y = self._get_camera_offset()
            scale = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
            world_x = event.pos[0] * scale + cam_x
            world_y = event.pos[1] * scale + cam_y
            target_id = self._enemy_at_world_pos(world_x, world_y)
            remote_player_id = -1
            # PvP: se nenhum mob clicado, verifica player remoto
            if target_id == -1:
                remote_player_id = self._remote_player_at_world_pos(world_x, world_y)
                target_id = remote_player_id

            player_cs   = self.world.get_component(self.player_entity_id, CombatState)
            player_auto = self.world.get_component(self.player_entity_id, PlayerAutoMove)

            if event.button == 1:
                if target_id != -1:
                    # Clique esquerdo em inimigo → seleciona alvo, cancela perseguição
                    if player_cs:
                        player_cs.target_entity_id = target_id
                        player_cs.is_pursuing = False
                else:
                    # Clique esquerdo no chão → deseleciona alvo atual
                    if player_cs:
                        player_cs.target_entity_id = -1
                        player_cs.is_pursuing = False

            elif event.button == 3:
                if target_id != -1:
                    # Alvo AMIGÁVEL (facção — Guarda Real, player remoto
                    # sem contexto PvP, futuro companheiro de time): clique
                    # direito NÃO inicia combate nem perseguição (bug real
                    # 16/07/2026 — o servidor já recusava o alvo, mas o
                    # cliente perseguia/marcava in_combat localmente).
                    # PLAYER remoto amigável → abre o modal de interação
                    # (Negociar/Duelar/Seguir — substitui o shift+clique
                    # antigo, decisão do usuário 16/07/2026); NPC amigável
                    # (guarda) → seleção pura, modal é só pra players.
                    from engine.faction_system import can_engage as _can_engage_click
                    if not _can_engage_click(self.world, self.player_entity_id, target_id):
                        if player_cs:
                            player_cs.target_entity_id = target_id
                            player_cs.is_pursuing = False
                        if remote_player_id != -1:
                            from ui.ui_components import TradeUIState as _TUStp
                            rc  = self.world.get_component(remote_player_id, RemoteControlled)
                            tui = self.world.get_component(self.player_entity_id, _TUStp)
                            if tui is not None:
                                tui.popup_target_eid  = remote_player_id
                                tui.popup_target_name = rc.name if rc else "Jogador"
                                tui.popup_screen_pos  = event.pos
                        continue
                    # Clique direito em inimigo → seleciona alvo, entra em combate e persegue
                    if player_cs:
                        player_cs.target_entity_id = target_id
                        player_cs.is_pursuing = True
                        enter_combat(player_cs)
                        from engine.utils import reengage_chase
                        reengage_chase(self.world, self.player_entity_id)
                    if player_auto:
                        player_auto.path_recalc_timer = 0.0
                else:
                    # Clique direito no chão → move para aquele tile
                    # (ignora se o clique foi num cadáver; LootSystem cuida disso)
                    if not self._corpse_at_world_pos(world_x, world_y):
                        tile_x = int(world_x / TILE_SIZE)
                        tile_y = int(world_y / TILE_SIZE)
                        # NÃO mexe em is_pursuing (28/07/2026) — desligar
                        # is_pursuing quebrava o combate inteiro (ranged
                        # parava de disparar, e _sync_combat_target mandava
                        # AUTO_ATTACK{tid:-1}, limpando o alvo NO SERVIDOR —
                        # ver PlayerInputSystem.update()). Desliga
                        # chase_suppressed (persistente, client-side puro,
                        # engine/components.py::CombatState) pra suprimir só
                        # o auto-walk de perseguição, mantendo alvo/combate
                        # intactos — STICKY até um reengajamento de verdade
                        # (clique direito no alvo/Espaço/skill).
                        if player_cs:
                            player_cs.chase_suppressed = True
                        if player_auto:
                            player_auto.ground_target = (tile_x, tile_y)
                            player_auto.active = True
                            player_auto.path.clear()
                            player_auto.path_recalc_timer = 0.0



class PlayerInputSystem(System):
    PLAYER_ATTACK_RANGE = 1     # tiles de alcance (punhos / melee)
    AUTO_MOVE_RECALC_INTERVAL = 0.3  # segundos entre recálculos de path

    def __init__(self, world: World, screen: "pygame.Surface | None" = None):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen
        self._net = None  # injetado pelo GameEngine no modo online

    def _is_on_screen(self, pos: "Position") -> bool:
        """Retorna True se a entidade está dentro dos limites da câmera atual."""
        if self.world_surf is None or pos is None:
            return True
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        _cam_first = self.world.get_first(Camera, Position)
        if _cam_first is not None:
            _, _, cam_pos = _cam_first
            cam_x = cam_pos.x - sw / 2
            cam_y = cam_pos.y - sh / 2
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            return 0 <= sx <= sw and 0 <= sy <= sh
        return True

    def _add_rage(self, entity_id: int, amount: int) -> None:
        """No-op — o servidor é o único produtor de rage (ganho por
        auto-attack em combat_processor.py, decay em ServerCombatStateSystem)
        e empurra todo valor novo via STATS_UPDATE. Gerar rage aqui também
        criava dois relógios independentes: a hotbar acendia com rage local
        que o servidor não tinha e o CAST_SKILL voltava "Raiva insuficiente".
        Mantido como método (não removido) porque os call sites já esperam
        chamar `self._add_rage(entity_id, N)` — `CharacterStats.rage` local
        é só display, alimentado por STATS_UPDATE."""
        return

    def _increment_pnq_counter(self, entity_id: int, hit_landed: bool = True) -> None:
        """Incrementa contador de Punho no Queixo. A cada 3 golpes efetivos adiciona 1 carga.

        Só conta hits efetivos (miss/parry/block/dodge ignorados via hit_landed=False).
        Não conta se a skill estiver em cooldown.
        """
        if not hit_landed:
            return
        char_stats = self.world.get_component(entity_id, CharacterStats)
        if not char_stats:
            return
        _combat_pnq = self.world.get_component(entity_id, CombatStats)
        if not _combat_pnq or not _combat_pnq.pnq_enabled:
            return
        # Não conta enquanto a skill está em cooldown
        ps = self.world.get_component(entity_id, PlayerSkills)
        if ps:
            for sk in ps.skills:
                if sk is not None and sk.skill_id == "punho_no_queixo":
                    if sk.current_cooldown > 0:
                        return
                    break
        char_stats.pnq_counter += 1
        if char_stats.pnq_counter >= 3:
            char_stats.pnq_counter = 0
            ps = self.world.get_component(entity_id, PlayerSkills)
            if ps:
                for sk in ps.skills:
                    if sk is None:
                        continue
                    if sk.skill_id == "punho_no_queixo" and sk.charges < sk.max_charges:
                        sk.charges += 1
                        LOG.add("Punho no Queixo: pronto!", (255, 180, 80))
                        break

    def _get_enemy_tiles(self) -> set:
        """Retorna tiles ocupados por inimigos, NPCs e jogadores remotos (obstáculos dinâmicos)."""
        from engine.world_systems import entity_footprint_tiles as _footprint_ui
        occupied = set()
        for eid, tm, _ in self.world.get_entities_with(TileMovement, Enemy):
            occupied.update(_footprint_ui(self.world, eid, tm))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        for eid, tm, _ in self.world.get_entities_with(TileMovement, NPC):
            occupied.update(_footprint_ui(self.world, eid, tm))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        for eid, tm, _ in self.world.get_entities_with(TileMovement, RemoteControlled):
            occupied.update(_footprint_ui(self.world, eid, tm))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        return occupied

    def update(self, events: list = None, dt: float = 0) -> None:
        if events is None:
            events = []
        keys = pygame.key.get_pressed()

        _player_first = self.world.get_first(Position, TileMovement, PlayerControlled, CombatStats)
        if _player_first is not None:
            entity_id, position, tile_movement, _, combat_stats = _player_first

            # Cooldown de ataque
            if combat_stats.attack_cooldown_timer > 0:
                combat_stats.attack_cooldown_timer -= dt

            combat_state = self.world.get_component(entity_id, CombatState)
            auto_move = self.world.get_component(entity_id, PlayerAutoMove)
            can_move = combat_state.can_move() if combat_state else True
            can_act = combat_state.can_act() if combat_state else True

            # Fluxo de morte/espírito: corpo (is_dead) não se move nem age
            # (aguarda "Liberar espírito"); ghost (is_ghost) anda livremente
            # (intangível, sem walkable check) mas não pode agir.
            _gst_inp = self.world.get_component(entity_id, GhostState)
            _is_ghost = _gst_inp is not None and _gst_inp.is_ghost
            if _gst_inp is not None and _gst_inp.is_dead and not _gst_inp.is_ghost:
                can_move = False
                can_act  = False
            elif _is_ghost:
                can_move = True
                can_act  = False

            # CC (stun/sleep/fear/polymorph/disoriented/root): bloqueia
            # movimento e/ou ação conforme blocks_move/blocks_act de cada
            # efeito (content/status_effects_data.py::EFFECT_DEFS — fonte
            # única, ver engine/utils.py). is_action_locked/is_movement_locked
            # são os mesmos choke-points usados no gate de skills do
            # servidor (skill_processor.py) e de auto-attack
            # (combat_processor.py), pra não divergir. Checados separado
            # (não os dois juntos) porque root bloqueia só movimento,
            # permitindo continuar agindo.
            from engine.utils import is_action_locked, is_movement_locked
            if is_movement_locked(self.world, entity_id):
                can_move = False   # input bloqueado; CombatStateSystem move aleatoriamente
            if is_action_locked(self.world, entity_id):
                can_act  = False   # não pode usar skills nem ataques

            # Taunt (Brado Provocativo — Fase D, 23/07/2026): NÃO usa o
            # mecanismo genérico blocks_move/blocks_act de EFFECT_DEFS de
            # propósito — um taunt precisa CONTINUAR autoatacando o taunter
            # (via combat_state.target_entity_id/is_pursuing, mecanismo
            # genérico de auto-attack em server/combat_processor.py), então
            # marcar blocks_act=True bloquearia esse auto-attack forçado
            # junto (combat_processor.py também usa is_action_locked como
            # early-exit). Em vez disso, bloqueia só o INPUT livre do
            # jogador aqui (movimento manual + iniciar outra skill) — o
            # movimento FORÇADO em si é 100% conduzido pelo servidor
            # (TauntMovementSystem, server/world_server.py) via
            # ENTITY_MOVE normal, nunca por input local.
            from engine.components import StatusEffects as _SfxTaunt
            _sfx_taunt = self.world.get_component(entity_id, _SfxTaunt)
            if _sfx_taunt is not None and _sfx_taunt.has("taunted"):
                can_move = False
                can_act  = False

            # Campo de chat focado: WASD é lido via pygame.key.get_pressed()
            # aqui embaixo, não pelos eventos KEYDOWN que o filtro de
            # systems_events já bloqueia com modal aberto — sem este check,
            # digitar "w"/"a"/"s"/"d" numa mensagem também moveria o player.
            # Bloqueia SÓ a leitura de WASD como movimento manual (abaixo) —
            # bug real relatado pelo usuário 22/07/2026: zerar `can_move`
            # inteiro também travava clique-pra-andar/auto-move/perseguição
            # de combate enquanto o chat estivesse só ABERTO (sem digitar
            # nada), o que nunca foi a intenção original.
            from ui.ui_components import UIState as _UIStateInp
            _ui_inp = self.world.get_component(entity_id, _UIStateInp)
            _chat_blocks_keyboard_move = _ui_inp is not None and _ui_inp.chat_active

            # --- Movimento por teclado ---
            if can_move and not _chat_blocks_keyboard_move and not tile_movement.is_moving:
                cur_x = tile_movement.current_tile_x
                cur_y = tile_movement.current_tile_y
                tgt_x, tgt_y = cur_x, cur_y

                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    tgt_x -= 1
                elif keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    tgt_x += 1
                elif keys[pygame.K_UP] or keys[pygame.K_w]:
                    tgt_y -= 1
                elif keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    tgt_y += 1

                if tgt_x != cur_x or tgt_y != cur_y:
                    # Teclado cancela auto-move (path/ground-target/Seguir) e
                    # desliga a PERSEGUIÇÃO — mas via chase_suppressed
                    # (persistente, client-side puro), NUNCA is_pursuing.
                    # is_pursuing controla se o servidor permite ranged
                    # disparar E se o alvo continua sincronizado no servidor
                    # (_sync_combat_target manda AUTO_ATTACK{tid:-1} quando
                    # is_pursuing vira False, limpando o alvo no servidor de
                    # vez — set_player_target). Desligar is_pursuing aqui
                    # quebrava o combate inteiro (28/07/2026). chase_suppressed
                    # é STICKY de propósito (pedido explícito do usuário no
                    # mesmo dia) — continua True mesmo depois de soltar as
                    # teclas; só volta a False num reengajamento de verdade
                    # (clique direito no alvo, Espaço, skill — ver todos os
                    # pontos que setam is_pursuing=True, cada um também zera
                    # chase_suppressed). Combate/alvo/ataque continuam
                    # intocados — se o alvo alcançar o player parado, o
                    # ataque dispara normal (guiado só por alcance).
                    if combat_state:
                        combat_state.chase_suppressed = True
                    if auto_move:
                        auto_move.active = False
                        auto_move.path.clear()
                        auto_move.ground_target = None
                        auto_move.follow_eid = -1
                    if _is_ghost or is_tile_walkable(
                            entity_id, tgt_x, tgt_y, cur_x, cur_y):
                        self._start_tile_movement(position, tile_movement, tgt_x, tgt_y)

            # --- Auto-move e auto-ataque em direção ao alvo selecionado ---
            _aoe_targeting = self.world.get_component(entity_id, AoeTargeting)
            _has_ground = auto_move and auto_move.active and auto_move.ground_target
            _is_following = auto_move is not None and auto_move.follow_eid != -1
            if combat_state and combat_state.target_entity_id != -1 and not _aoe_targeting:
                # Perseguição de combate tem precedência sobre "Seguir"
                if _is_following and combat_state.is_pursuing:
                    auto_move.follow_eid = -1
                    _is_following = False
                # Sempre chama _process_target para validação (limpa alvo morto/fora de visão)
                self._process_target(
                    entity_id, position, tile_movement,
                    combat_stats, combat_state, auto_move, can_act, can_move, dt,
                )
                # Destino de chão/"Seguir" sempre processado quando presente —
                # chase_suppressed (setado acima ou pelo clique de chão,
                # MouseTargetingSystem) já impede _process_target de brigar
                # pelo mesmo tick.
                if _has_ground:
                    self._process_ground_move(entity_id, position, tile_movement, auto_move, can_move, dt)
                elif _is_following:
                    self._process_follow(entity_id, position, tile_movement, auto_move, can_move, dt)
            # --- "Seguir" player (modal de interação, 16/07/2026) ---
            elif _is_following:
                self._process_follow(entity_id, position, tile_movement, auto_move, can_move, dt)
            # --- Movimento de chão (sem alvo selecionado) ---
            elif _has_ground:
                self._process_ground_move(entity_id, position, tile_movement, auto_move, can_move, dt)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _process_target(self, entity_id, position, tile_movement,
                        combat_stats, combat_state, auto_move, can_act, can_move, dt):
        """Auto-move e auto-ataque em direção ao alvo selecionado.

        `combat_state.chase_suppressed` (28/07/2026, pedido do usuário —
        REVISADO no mesmo dia): True bloqueia SÓ as chamadas de
        _auto_move_step abaixo (o auto-walk de perseguição de volta ao
        alvo) — nunca o ataque em si nem is_pursuing/target_entity_id.
        Setado por movimento manual (WASD/clique de chão) e STICKY —
        continua True mesmo depois de soltar as teclas, só volta a False
        num reengajamento de verdade (clique direito no alvo, Espaço,
        skill — pedido explícito do usuário: "só volta a perseguir se eu
        apertar skill/espaço/clique direito", nunca sozinho ao parar de
        andar). Nunca usar is_pursuing pra isso — server/combat_
        processor.py exige is_pursuing pra RANGED disparar, e
        _sync_combat_target manda AUTO_ATTACK{tid:-1} que limpa o alvo
        NO SERVIDOR quando is_pursuing vira False (§34.62)."""
        target_id = combat_state.target_entity_id

        target_pos = self.world.get_component(target_id, Position)
        target_tm = self.world.get_component(target_id, TileMovement)

        # Alvo morto ou removido: limpa seleção. is_target_alive cobre mob
        # local (CombatStats) E player remoto em PvP (RemoteControlled, sem
        # CombatStats — sem isso, o ciclo de ataque/som de nock fica girando
        # contra um corpo, já que current_hp não existe nesse alvo).
        if not target_pos or not is_target_alive(self.world, target_id):
            combat_state.target_entity_id = -1
            combat_state.is_pursuing = False
            if auto_move:
                auto_move.active = False
                auto_move.path.clear()
            return

        # Alvo fora da visão (fog): cancela perseguição e seleção
        if self.world.get_component(target_id, Visible) is None:
            combat_state.target_entity_id = -1
            combat_state.is_pursuing = False
            if auto_move:
                auto_move.active = False
                auto_move.path.clear()
            return

        if target_tm:
            # tgt_tile (CHASE) — tile destino do mob: direciona a perseguição.
            # Usa target_tile assim que mob inicia movimento → player começa a
            # seguir no mesmo frame, sem esperar 50% do passo completar.
            if (target_tm.is_moving
                    and (target_tm.target_tile_x != target_tm.current_tile_x
                         or target_tm.target_tile_y != target_tm.current_tile_y)):
                tgt_tile_x, tgt_tile_y = target_tm.target_tile_x, target_tm.target_tile_y
            else:
                tgt_tile_x, tgt_tile_y = target_tm.current_tile_x, target_tm.current_tile_y
            # cur_tile (ATAQUE) — tile autoritativo do servidor.
            # server_tile_x/y: gravado via new_tx/new_ty (destino) do ENTITY_MOVE;
            # espelha a posição atual do mob no servidor (move instantâneo server-side).
            # Sem isso: cliente usaria posição visual animada (atrás do servidor).
            _stx = getattr(target_tm, 'server_tile_x', 0)
            _sty = getattr(target_tm, 'server_tile_y', 0)
            if _stx or _sty:          # inicializado (online)
                cur_tile_x, cur_tile_y = _stx, _sty
            else:                     # offline / não inicializado → fallback
                cur_tile_x, cur_tile_y = target_tm.current_tile_x, target_tm.current_tile_y
        else:
            tgt_tile_x = int(target_pos.x / TILE_SIZE)
            tgt_tile_y = int(target_pos.y / TILE_SIZE)
            cur_tile_x, cur_tile_y = tgt_tile_x, tgt_tile_y

        pl_tile_x = tile_movement.current_tile_x
        pl_tile_y = tile_movement.current_tile_y
        dist        = chebyshev(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)  # chase
        dist_attack = chebyshev(pl_tile_x, pl_tile_y, cur_tile_x, cur_tile_y)  # ataque

        # Pixel distance: guard secundário — evita atacar com mob visualmente longe.
        _px_chase       = max(abs(position.x - target_pos.x), abs(position.y - target_pos.y))
        _melee_chase_px = (self.PLAYER_ATTACK_RANGE + 0.5) * TILE_SIZE  # 48 px


        char_stats  = self.world.get_component(entity_id, CharacterStats)
        is_mage     = char_stats is not None and char_stats.class_id == "mago"
        is_archer   = char_stats is not None and char_stats.class_id == "arqueiro"

        # Arqueiro só entra no combate ranged se tiver um ARCO de verdade
        # equipado — desarmado ou com arma melee equipada, cai no MESMO
        # branch de melee do guerreiro logo abaixo (chase até adjacente +
        # deal_damage "physical"). _add_rage/_increment_pnq_counter nesse
        # branch são sempre no-op pra quem não é guerreiro (checam
        # self._net e combat_stats.pnq_enabled respectivamente) — seguro
        # reusar sem duplicar lógica. O skill_level da arma melee equipada
        # entra automaticamente via deal_damage → CombatSystem.
        # _calculate_damage (damage_calculator.ap_skill_mult). Antes: um
        # arqueiro sem arco só recebia o aviso "Precisa de um arco
        # equipado" e ficava parado, sem NENHUMA opção de ataque. Ver
        # PROBLEMAS_ARQUITETURA.md.
        _archer_has_bow = False
        if is_archer:
            _equip_ac    = self.world.get_component(entity_id, Equipment)
            _mainhand_ac = _equip_ac.slots.get("mainhand") if _equip_ac else None
            _archer_has_bow = (_mainhand_ac is not None
                               and getattr(_mainhand_ac, "subtype", "") == "Bow")

        if is_archer and _archer_has_bow:
            self._process_archer_combat(
                entity_id, position, tile_movement, combat_stats, combat_state,
                auto_move, can_act, can_move, target_id, tgt_tile_x, tgt_tile_y, dt,
                _px_chase, _melee_chase_px, dist_attack=dist_attack)
        elif is_mage:
            # Skill específica apertada (CombatState.pursuit_skill_range,
            # ver docstring lá) tem prioridade sobre o mínimo genérico —
            # senão uma skill de mira curta na hotbar (ex: Calamidade
            # Flamejante) fazia a perseguição parar cedo demais pra
            # QUALQUER outra skill de alcance maior (bug real relatado
            # pelo usuário, 14/09/2026). Mínimo genérico só entra quando
            # o jogador ainda não apertou nenhuma skill (engajou só por
            # clique direito/Espaço) — garante que pelo menos alguma
            # ofensiva já esteja em alcance nesse caso.
            pursuit_range = combat_state.pursuit_skill_range or self._mage_attack_range(entity_id)
            # pursuit_range é sempre alcance de skill (ranged, >1 tile) —
            # usa in_range() (Euclidiana), igual ao gate real do servidor
            # (SkillHandlers._resolve_target), não chebyshev() (dist,
            # calculado acima só pra este fim). Ver §135. dist_attack
            # (melee/adjacência) continua chebyshev(), sem mudança.
            _mage_skill_in_range = in_range(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, pursuit_range)
            if dist_attack <= self.PLAYER_ATTACK_RANGE:
                # Adjacente: melee idêntico ao guerreiro (sem geração de Raiva)
                if auto_move:
                    auto_move.path.clear()
                if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                    SOUNDS.play_emote_attack(is_player=True)
                    _tgt_cs = self.world.get_component(target_id, CombatStats)
                    dead, _ = deal_damage(entity_id, target_id, "physical")
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    enter_combat(combat_state)
                    if dead:
                        combat_state.target_entity_id = -1
                        combat_state.is_pursuing = False
                        combat_state.pursuit_skill_range = 0
                        if auto_move:
                            auto_move.active = False
            elif _mage_skill_in_range:
                # Dentro do alcance de skill: para e aguarda cast manual
                if auto_move:
                    auto_move.path.clear()
            elif (can_move and combat_state.is_pursuing and auto_move
                    and not tile_movement.is_moving and not combat_state.chase_suppressed):
                # Fora do alcance de skill: persegue até o alcance de skill
                self._auto_move_step(
                    entity_id, position, tile_movement,
                    pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, auto_move, dt,
                    attack_range=pursuit_range, target_eid=target_id,
                )
        else:
            # Ataque: usa o mínimo entre server tile e tile visual — B (PnQ moving mob)
            # server_tile pode estar 1 passo à frente se o servidor processou movimento
            # antes do cliente receber, criando dist_attack=2 com mob visualmente adjacente.
            _vis_dist = chebyshev(pl_tile_x, pl_tile_y,
                                   target_tm.current_tile_x if target_tm else cur_tile_x,
                                   target_tm.current_tile_y if target_tm else cur_tile_y)
            if min(dist_attack, _vis_dist) <= self.PLAYER_ATTACK_RANGE:
                if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                    SOUNDS.play_emote_attack(is_player=True)
                    _tgt_cs    = self.world.get_component(target_id, CombatStats)
                    _hp_before = _tgt_cs.current_hp if _tgt_cs else 0
                    dead, _ = deal_damage(entity_id, target_id, "physical")
                    _hit_landed = dead or (_tgt_cs and _tgt_cs.current_hp < _hp_before)
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    # Rage sempre gerada ao atacar — idêntico ao offline (systems.py:1491)
                    # Em online, deal_damage não funciona em mobs remotos mas rage é local
                    self._add_rage(entity_id, 5)
                    enter_combat(combat_state)
                    self._increment_pnq_counter(entity_id, _hit_landed)
                    if dead:
                        combat_state.target_entity_id = -1
                        combat_state.is_pursuing = False
                        if auto_move:
                            auto_move.active = False
                        return

            # Chase: para quando adjacente ao tile predito (dist <= range).
            if dist <= self.PLAYER_ATTACK_RANGE:
                if auto_move:
                    auto_move.path.clear()
            elif (can_move and combat_state.is_pursuing and auto_move
                    and not tile_movement.is_moving and not combat_state.chase_suppressed):
                self._auto_move_step(
                    entity_id, position, tile_movement,
                    pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, auto_move, dt,
                    target_eid=target_id,
                )

    def _mage_attack_range(self, entity_id: int) -> int:
        """Retorna o MENOR cast_range entre as skills ofensivas com alvo da
        hotbar do mago — garante que, quando a perseguição genérica (clique
        direito) parar, TODAS as skills do jogador já estão em alcance.

        Antes usava o MAIOR cast_range: com Bola de Fogo (6) + Polimorfia (7)
        na hotbar, a perseguição parava a 7 tiles — distância em que Bola de
        Fogo (alcance 6) já dava "Fora de alcance" ao apertar, mesmo o
        personagem tendo acabado de parar de andar em direção ao alvo (ver
        PROBLEMAS_ARQUITETURA.md). Ignora skills self-cast/utilitárias
        (needs_target=False, ex. Bloco de Gelo) e sem alcance definido
        (cast_range<=0, corpo-a-corpo) — essas não participam da checagem
        de distância pra perseguir.
        """
        skills = self.world.get_component(entity_id, PlayerSkills)
        if not skills:
            return 5
        min_range = None
        for s in skills.skills:
            if s is None:
                continue
            if not getattr(s, "offensive", True) or not getattr(s, "needs_target", True):
                continue
            if s.cast_range <= 0:
                continue
            if min_range is None or s.cast_range < min_range:
                min_range = s.cast_range
        return min_range if min_range is not None else 5

    def _process_archer_combat(self, entity_id, position, tile_movement,
                               combat_stats, combat_state, auto_move,
                               can_act, can_move, target_id, tgt_tile_x, tgt_tile_y, dt,
                               px_chase: float = 0.0,
                               melee_chase_px: float = float("inf"),
                               dist_attack: int = -1):
        """Auto-attack ranged do arqueiro: verifica arco+aljava e dispara flecha.

        `combat_state.chase_suppressed` (28/07/2026): bloqueia só o
        auto-move de reposicionamento (persegue até ficar dentro de
        bow_range) — nunca o disparo em si, que já não dependia de
        tile_movement.is_moving (por isso o arqueiro sempre pôde atirar
        andando, dentro do alcance)."""
        pl_tile_x = tile_movement.current_tile_x
        pl_tile_y = tile_movement.current_tile_y
        dist = chebyshev(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)
        # dist_attack: distância do tile ATUAL do mob (= verificação do servidor).
        # Se não fornecido, usa dist (retrocompatibilidade com offline).
        if dist_attack < 0:
            dist_attack = dist

        equip = self.world.get_component(entity_id, Equipment)
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None

        bow_range = getattr(bow, "cast_range", 8) if bow and getattr(bow, "subtype", "") == "Bow" else 0

        if not bow_range:
            # Sem arco: avisa e para — não faz auto-move para melee nem ataca
            if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                from ui.combat_log import LOG as _LOG_bow
                _LOG_bow.add("Precisa de um arco equipado para atirar.", (220, 180, 80))
                combat_stats.attack_cooldown_timer = 2.0  # throttle do aviso
            if auto_move and not auto_move.ground_target:
                auto_move.active = False
                auto_move.path.clear()
            return

        # Arco equipado — verificar aljava
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                from ui.combat_log import LOG as _LOG
                _LOG.add("Precisa de uma aljava equipada para atirar.", (220, 180, 80))
                combat_stats.attack_cooldown_timer = 1.0  # cooldown de aviso
                from debug.archer_debug import ADBG_CLIENT as _ADBG_c_nq
                _ADBG_c_nq.log_block(entity_id, target_id, "no_quiver_equipped")
            return

        _PRE_DRAW_THRESHOLD = 1.0   # segundos antes do disparo para tocar o nock

        # in_range() (Euclidiana), igual ao gate real do servidor
        # (server/combat_processor.py::_resolve_auto_attack pro arco) — não
        # chebyshev() (`dist`, ainda usado abaixo só como fallback de
        # dist_attack). Ver §135.
        if in_range(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, bow_range):
            # Limpa perseguição apenas quando não há ground_target — se houver,
            # _process_ground_move cuida do movimento e active deve permanecer True.
            if auto_move and not auto_move.ground_target:
                auto_move.path.clear()
                auto_move.active = False

            # Pré-tensionamento: toca ~1s antes do próximo disparo (uma vez por ciclo)
            if (combat_state.is_pursuing and can_act
                    and 0 < combat_stats.attack_cooldown_timer <= _PRE_DRAW_THRESHOLD
                    and combat_stats.arrow_pre_draw_ready):
                combat_stats.arrow_pre_draw_ready = False
                if random.random() < 0.30:
                    SOUNDS.play_random(["arrow_nock_1", "arrow_nock_2"], channel_group=(6, 7))
                    from debug.archer_debug import ADBG_CLIENT as _ADBG_c_nock
                    _ADBG_c_nock.log("SOUND", entity_id, target_id, which="nock")

            if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                if quiver.arrow_count <= 0:
                    from ui.combat_log import LOG as _LOG
                    _LOG.add("Aljava vazia! Use Recarregar.", (220, 80, 80))
                    combat_stats.attack_cooldown_timer = 1.0
                    from debug.archer_debug import ADBG_CLIENT as _ADBG_c_empty
                    _ADBG_c_empty.log_block(entity_id, target_id, "quiver_empty",
                                            quiver_item_id=getattr(quiver, "item_id", None),
                                            quiver_obj_id=id(quiver),
                                            max_arrows=getattr(quiver, "max_arrows", -1))
                    return

                # LOS: bloqueia ANTES de descontar flecha/cooldown otimisticamente
                # — mesmo padrão já usado pra Bola de Fogo (ver bloco
                # "_skill_has_proj" acima). Sem isso, um tiro com parede na
                # frente do alvo tocava som + descontava aljava no cliente
                # mesmo quando o servidor ia bloquear o disparo (bug real
                # reportado pelo usuário — ver
                # server/spell_completion_processor.py::_server_apply_ranged_physical,
                # ARQUITETURA_ONLINE.md).
                from debug.archer_debug import ADBG_CLIENT as _ADBG_c_los
                _tmap_arch_los = get_tilemap()
                _has_los_c = (not _tmap_arch_los) or EnemyAISystem._has_line_of_sight(
                        _tmap_arch_los, pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)
                _ADBG_c_los.log_los(entity_id, target_id, _has_los_c,
                                    p_tile=(pl_tile_x, pl_tile_y), t_tile=(tgt_tile_x, tgt_tile_y))
                if not _has_los_c:
                    WARN.add("Há obstáculos no caminho")
                    combat_stats.attack_cooldown_timer = 0.5  # throttle do aviso
                    _ADBG_c_los.log_block(entity_id, target_id, "los_blocked")
                    return

                # Flecha 100% server-driven — nasce em _apply_combat_result
                # ao chegar o COMBAT_RESULT (source="auto"), igual Bola de Fogo nasce
                # no is_completion. O desconto da aljava TAMBÉM só acontece lá
                # (mesmo evento que cria a flecha visual) — NUNCA aqui antecipado.
                # Bug real (10/07/2026): este cooldown LOCAL não congela do mesmo
                # jeito que o do servidor (server/combat_processor.py congela o
                # tick INTEIRO enquanto bloqueado por LOS/alcance/perseguição; aqui
                # só decrementa sem parar, então zera mais cedo sempre que há
                # bloqueio) — o cliente "atirava" (descontava flecha) bem mais vezes
                # que o servidor de verdade disparava. Aqui só avançamos
                # cooldown/pré-tensionamento locais (feel de UI); ver
                # client/remote_entity_handlers.py::_apply_combat_result pro
                # desconto real. O bloco offline (criava PlayerProjectile local +
                # descontava aljava + som) foi REMOVIDO junto do modo offline
                # (15/07/2026, item A2 §11 — offline vive só no master).
                _ADBG_c_los.log_fire_ok(entity_id)
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                combat_stats.arrow_pre_draw_ready  = True
                enter_combat(combat_state)
        elif (can_move and combat_state.is_pursuing and auto_move
                and not tile_movement.is_moving and not combat_state.chase_suppressed):
            # Persegue o mob apenas quando is_pursuing=True e chase_suppressed
            # não foi ligado por movimento manual (ver docstring do método).
            auto_move.active = True
            self._auto_move_step(entity_id, position, tile_movement,
                                 pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y,
                                 auto_move, dt, attack_range=bow_range,
                                 target_eid=target_id)

    def _ranged_stop_tile(self, pl_x: int, pl_y: int,
                          tgt_x: int, tgt_y: int, attack_range: int) -> tuple:
        """Tile de parada para ranged: (attack_range-1) tiles REAIS (Euclidiana)
        do alvo, na direção do player.

        Escala pela distância REAL (√(dx²+dy²)), não Chebyshev — precisa ficar
        consistente com o gate de alcance autoritativo (in_range(), círculo de
        verdade, ver §135). Escalar por Chebyshev fazia o destino, numa
        aproximação diagonal, pousar numa distância REAL de até attack_range×√2
        do alvo — o personagem parava de andar "achando" que já tinha
        chegado, mas o servidor (e o check de skill do próprio cliente)
        considerava fora de alcance.
        """
        dx = pl_x - tgt_x
        dy = pl_y - tgt_y
        real_dist = math.sqrt(dx * dx + dy * dy)
        if real_dist == 0:
            return (pl_x, pl_y)
        ratio = (attack_range - 1) / real_dist
        return (tgt_x + int(round(dx * ratio)), tgt_y + int(round(dy * ratio)))

    def _auto_move_step(self, entity_id, position, tile_movement,
                        pl_x, pl_y, tgt_x, tgt_y, auto_move, dt,
                        attack_range: int = 1, target_eid: int = -1):
        """Calcula e executa um passo de movimento em direção ao alvo."""
        auto_move.path_recalc_timer -= dt
        current_tile = (pl_x, pl_y)

        if not auto_move.path or auto_move.path_recalc_timer <= 0:
            enemy_tiles = self._get_enemy_tiles()
            enemy_tiles.discard((tgt_x, tgt_y))
            # Remove tiles animados do mob alvo: artefatos de animação, mob já saiu deles.
            if target_eid != -1:
                _tgt_tm = self.world.get_component(target_eid, TileMovement)
                if _tgt_tm:
                    enemy_tiles.discard((_tgt_tm.current_tile_x, _tgt_tm.current_tile_y))
                    enemy_tiles.discard((_tgt_tm.target_tile_x, _tgt_tm.target_tile_y))

            if attack_range > 1:
                # Ranged: caminha até tile a (attack_range-1) tiles do alvo
                dest = self._ranged_stop_tile(pl_x, pl_y, tgt_x, tgt_y, attack_range)
                path = find_path(current_tile, dest,
                                                         dynamic_obstacles=enemy_tiles)
                auto_move.path = path or []
            else:
                # Melee: tenta todos os tiles adjacentes ao alvo, do mais próximo ao mais distante.
                # Exclui server_tile das candidatas: mob está lá, A* já evita via enemy_tiles,
                # mas excluir como destino garante que a rota final não termine no server_tile.
                adj = [
                    (tgt_x + dx, tgt_y + dy)
                    for dy in [-1, 0, 1] for dx in [-1, 0, 1]
                    if not (dx == 0 and dy == 0)
                    and max(abs(dx), abs(dy)) == 1
                ]
                adj.sort(key=lambda t: abs(t[0] - pl_x) + abs(t[1] - pl_y))
                auto_move.path = []
                for tile in adj:
                    path = find_path(current_tile, tile,
                                                             dynamic_obstacles=enemy_tiles)
                    if path:
                        auto_move.path = path
                        break

            auto_move.path_recalc_timer = self.AUTO_MOVE_RECALC_INTERVAL

        if auto_move.path:
            nx, ny = auto_move.path[0]
            if is_tile_walkable(entity_id, nx, ny, ignore_eid=target_eid):
                self._start_tile_movement(position, tile_movement, nx, ny)
                auto_move.path.pop(0)
            else:
                auto_move.path.clear()
                auto_move.path_recalc_timer = 0.0

    def _process_ground_move(self, entity_id, position, tile_movement, auto_move, can_move, dt):
        """Move o jogador passo a passo até o tile de destino definido por clique esquerdo."""
        gt_x, gt_y = auto_move.ground_target
        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y

        # Chegou ao destino
        if pl_x == gt_x and pl_y == gt_y:
            auto_move.ground_target = None
            auto_move.active = False
            auto_move.path.clear()
            return

        # CC (sono/desorientado/polimorfia/etc, ver is_action_locked/can_move
        # em PlayerInputSystem.update()) bloqueia TODA movimento, não só
        # teclado — clique de chão fica pausado (path preservado) até liberar.
        if not can_move:
            return

        if tile_movement.is_moving:
            return

        auto_move.path_recalc_timer -= dt
        if not auto_move.path or auto_move.path_recalc_timer <= 0:
            enemy_tiles = self._get_enemy_tiles()
            enemy_tiles.discard((gt_x, gt_y))
            dist = abs(gt_x - pl_x) + abs(gt_y - pl_y)
            nodes_limit = min(30000, max(8000, dist * 80))
            path = find_path((pl_x, pl_y), (gt_x, gt_y),
                                                     dynamic_obstacles=enemy_tiles,
                                                     max_nodes=nodes_limit,
                                                     manhattan_limit=None)
            if path:
                auto_move.path = path
            elif not auto_move.path:
                auto_move.ground_target = None
                auto_move.active = False
                return
            auto_move.path_recalc_timer = self.AUTO_MOVE_RECALC_INTERVAL

        if auto_move.path:
            nx, ny = auto_move.path[0]
            cx = tile_movement.current_tile_x
            cy = tile_movement.current_tile_y
            if is_tile_walkable(entity_id, nx, ny, cx, cy):
                self._start_tile_movement(position, tile_movement, nx, ny)
                auto_move.path.pop(0)
            else:
                auto_move.path.clear()
                auto_move.path_recalc_timer = 0.0

    def _process_follow(self, entity_id, position, tile_movement, auto_move, can_move, dt):
        """"Seguir" (modal de interação com player, 16/07/2026): acompanha
        o player em follow_eid, parando adjacente — reutiliza o pathing de
        alvo MÓVEL da perseguição de combate (_auto_move_step lê o tile
        vivo do alvo a cada recalc), sem nenhum combate envolvido."""
        if not can_move:
            return
        target_tm  = self.world.get_component(auto_move.follow_eid, TileMovement)
        target_vis = self.world.get_component(auto_move.follow_eid, Visible)
        # Alvo sumiu (deslogou/despawnou/fora da visão): para de seguir.
        if (target_tm is None or target_vis is None
                or self.world.get_component(auto_move.follow_eid, RemoteControlled) is None):
            auto_move.follow_eid = -1
            auto_move.path.clear()
            return

        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y
        # Tile "vivo" do seguido: destino do passo atual se estiver andando
        # (mesma predição da perseguição de combate — começa a seguir no
        # mesmo frame em que o alvo inicia o movimento).
        if target_tm.is_moving:
            tgt_x, tgt_y = target_tm.target_tile_x, target_tm.target_tile_y
        else:
            tgt_x, tgt_y = target_tm.current_tile_x, target_tm.current_tile_y

        if chebyshev(pl_x, pl_y, tgt_x, tgt_y) <= 1:
            auto_move.path.clear()   # adjacente: fica parado (não empurra)
            return
        if tile_movement.is_moving:
            return
        self._auto_move_step(entity_id, position, tile_movement,
                             pl_x, pl_y, tgt_x, tgt_y, auto_move, dt,
                             attack_range=1, target_eid=auto_move.follow_eid)

    def _start_tile_movement(self, position, tile_movement, tgt_x, tgt_y):
        start_tile_movement(position, tile_movement, tgt_x, tgt_y)

    def _space_engage(self, entity_id, tile_movement, combat_stats, combat_state, auto_move):
        """ESPAÇO: ataca o alvo JÁ selecionado (TAB/clique), se ele ainda
        estiver vivo — só busca o inimigo mais próximo visível na tela
        quando NÃO há alvo válido selecionado. Antes sempre recalculava
        "mais próximo" do zero, ignorando `combat_state.target_entity_id`
        por completo — bug real relatado pelo usuário (25/08/2026): apertar
        espaço trocava de alvo em vez de atacar o que já estava
        selecionado, em QUALQUER parte do jogo (não só BG). Usa
        `utils.is_target_alive()` (ponto único de verdade — cobre mob
        local, player remoto em PvP e mob remoto, nenhum dos 3 detectável
        só por CombatStats.current_hp).

        Players remotos (RemoteControlled) entram na busca DE FALLBACK
        junto com mobs (Enemy) — só quando engajáveis AGORA (duelo/arena/
        zona PvP, mesmo `can_engage`/`_client_pvp_context` do clique). Bug
        real relatado pelo usuário 22/07/2026: ESPAÇO nunca conseguia
        iniciar combate contra um oponente de duelo, porque a busca só
        olhava Enemy."""
        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y

        best_eid  = combat_state.target_entity_id if combat_state else -1
        best_dist = None
        if best_eid != -1 and is_target_alive(self.world, best_eid):
            _cur_tm_space = self.world.get_component(best_eid, TileMovement)
            if _cur_tm_space is not None:
                best_dist = chebyshev(pl_x, pl_y, _cur_tm_space.current_tile_x,
                                      _cur_tm_space.current_tile_y)
            else:
                best_eid = -1
        else:
            best_eid = -1

        if best_eid == -1:
            _fog_vis_se = None
            _fow_se_first = self.world.get_first(FogOfWar)
            if _fow_se_first is not None:
                _fog_vis_se = _fow_se_first[1].visible

            from engine.faction_system import can_engage as _can_engage_space
            best_dist = float("inf")
            for eid, epos, _, _, etm, ecs, _ in self.world.get_entities_with(
                    Position, Enemy, AIControlled, TileMovement, CombatStats, Visible):
                if ecs.current_hp <= 0:
                    continue
                # Alvo amigável (facção) nunca é engajável — mesmo gate do
                # clique direito (o proxy do NPC de combate remoto carrega a
                # tag Enemy no cliente, então sem este filtro o SPACE podia
                # engajar o Guarda Real se ele fosse o mais próximo).
                if not _can_engage_space(self.world, entity_id, eid):
                    continue
                if not self._is_on_screen(epos):
                    continue
                if _fog_vis_se is not None and \
                        (etm.current_tile_x, etm.current_tile_y) not in _fog_vis_se:
                    continue
                dist = chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y)
                if dist < best_dist:
                    best_dist = dist
                    best_eid  = eid

            for eid, epos, etm, _viz, _rc in self.world.get_entities_with(
                    Position, TileMovement, Visible, RemoteControlled):
                if eid == entity_id or _rc.hp <= 0:
                    continue
                if not _can_engage_space(self.world, entity_id, eid):
                    continue
                if not self._is_on_screen(epos):
                    continue
                dist = chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y)
                if dist < best_dist:
                    best_dist = dist
                    best_eid  = eid

        if best_eid == -1:
            return

        if combat_state:
            combat_state.target_entity_id = best_eid
            combat_state.is_pursuing = True
            enter_combat(combat_state)
            from engine.utils import reengage_chase
            reengage_chase(self.world, entity_id)
        if auto_move:
            auto_move.path_recalc_timer = 0.0

        # Ataca imediatamente se já estiver no alcance
        if best_dist <= self.PLAYER_ATTACK_RANGE and combat_stats.attack_cooldown_timer <= 0:
            dead, _ = deal_damage(entity_id, best_eid, "physical")
            combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
            self._add_rage(entity_id, 5)
            if dead and combat_state:
                combat_state.target_entity_id = -1






