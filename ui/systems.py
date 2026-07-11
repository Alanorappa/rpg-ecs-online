# systems.py
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
                       MobSounds, PendingDeath, XPReward, SpawnZoneOwner, SpawnZone, \
                       PlayerSkills, NPC, ActiveRegen, ConsumableBar, \
                       AoeTargeting, RemoteControlled, GhostState, MapLocation
from engine.world import World
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
from engine.utils import chebyshev, start_tile_movement
from engine.damage_calculator import resolve_attack_outcome, calculate_base_damage
from ui.combat_log import LOG
from ui.sound_manager import SOUNDS
from ui.floating_text import FLT, PROC, WARN
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines, fill_surf
from content.status_effects_data import EFFECT_DEFS
from ui.fov import compute_fov
from content.loot_tables import roll_loot, roll_mob_loot, roll_coins
from engine.entity_factory import create_corpse, create_enemy
from content.enemy_abilities_data import ABILITY_DEFS
from content.merchant_data import SHOPS
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
    SpawnZoneSystem, MobRespawnSystem,
    _svc, register_services, deal_damage, find_path, get_tilemap,
    is_tile_walkable, get_mainhand_weapon,
)

# ── Cache de sprites de efeito de chão (lazy-loaded) ─────────────────────────
_GROUND_EFFECT_SPRITES: dict[str, "pygame.Surface | None"] = {}

_GROUND_EFFECT_TYPES_ICONS = {"root"}  # efeitos com sprite de chão: sem ícone na barra


def _draw_effect_icons(
    surf: "pygame.Surface",
    draw_x: float,
    bar_y: int,
    active_effects: list,
    font: "pygame.font.Font",
) -> None:
    """Desenha ícones de efeito ativos acima da barra de HP.

    Ícones 16×16 centralizados no eixo X, enfileirados horizontalmente,
    10 px acima da barra. Sobreposição do tempo restante em vermelho.
    """
    from math import ceil as _ceil
    from ui.effect_animator import get_frame as _get_frame, FRAME_W, FRAME_H
    from content.status_effects_data import EFFECT_DEFS as _EDEFS

    visible = [e for e in active_effects
               if e.effect_type not in _GROUND_EFFECT_TYPES_ICONS]
    if not visible:
        return

    ICON = FRAME_W   # 16
    GAP  = 3
    n    = len(visible)
    total_w = n * ICON + GAP * (n - 1)
    ix = int(draw_x - total_w / 2)
    iy = bar_y - ICON - 10

    for eff in visible:
        frame = _get_frame(eff.effect_type)
        if frame is not None:
            surf.blit(frame, (ix, iy))
        else:
            defn = _EDEFS.get(eff.effect_type)
            col  = defn.color if defn else (150, 150, 150)
            pygame.draw.rect(surf, col, (ix, iy, ICON, ICON))

        dur = getattr(eff, 'duration', 0.0)
        if 0 < dur <= 99:
            txt   = str(max(1, _ceil(dur)))
            shad  = font.render(txt, False, (0, 0, 0))
            label = font.render(txt, False, (255, 60, 60))
            tx = ix + (ICON - label.get_width())  // 2
            ty = iy + (ICON - label.get_height()) // 2
            surf.blit(shad,  (tx + 1, ty + 1))
            surf.blit(label, (tx,     ty))

        ix += ICON + GAP

def _get_ground_effect_sprite(effect_type: str) -> "pygame.Surface | None":
    """Retorna sprite de efeito de chão para o tipo dado; carrega na primeira vez.

    Usa o mesmo padrão lazy-load do effect_animator — falha silenciosa se
    o arquivo não existir ou pygame ainda não tiver modo de vídeo ativo.
    """
    if effect_type in _GROUND_EFFECT_SPRITES:
        return _GROUND_EFFECT_SPRITES[effect_type]
    _PATH_MAP = {
        "root": "assets/effects/frozen_root.png",
    }
    rel_path = _PATH_MAP.get(effect_type)
    surf = None
    if rel_path:
        try:
            from paths import resource_path as _rp
            surf = pygame.image.load(_rp(rel_path)).convert_alpha()
        except Exception:
            surf = None
    _GROUND_EFFECT_SPRITES[effect_type] = surf
    return surf














# ─────────────────────────────────────────────────────────────────────────────


# apply_effect é re-exportado de core_systems (importado no topo do arquivo).
# Mantido aqui para backward compatibility com todos os importadores.
















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
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - sw / 2, cam_pos.y - sh / 2
        return 0.0, 0.0

    def _enemy_at_world_pos(self, world_x: float, world_y: float) -> int:
        """Retorna o entity_id do inimigo vivo e visível na posição mundo, ou -1."""
        _fog_vis = None
        for _, _fw in self.world.get_entities_with(FogOfWar):
            _fog_vis = _fw.visible
            break
        for entity_id, pos, renderable, _, _, etm in self.world.get_entities_with(
                Position, Renderable, Enemy, Visible, TileMovement):
            if _fog_vis is not None and \
                    (etm.current_tile_x, etm.current_tile_y) not in _fog_vis:
                continue
            hw = renderable.width / 2
            hh = renderable.height / 2
            if (pos.x - hw <= world_x <= pos.x + hw and
                    pos.y - hh <= world_y <= pos.y + hh):
                cs = self.world.get_component(entity_id, CombatStats)
                if not cs or cs.current_hp > 0:
                    return entity_id
        return -1

    def _remote_player_at_world_pos(self, world_x: float, world_y: float) -> int:
        """Retorna o entity_id local de um player remoto clicado, ou -1.

        Usado para PvP: permite selecionar outros players como alvo.
        A verificação de zona PvP fica no servidor — o cliente só faz targeting.
        """
        from engine.components import RemoteControlled as _RC
        for entity_id, pos, renderable, _, _rc in self.world.get_entities_with(
                Position, Renderable, Visible, _RC):
            if entity_id == self.player_entity_id:
                continue  # não seleciona a si mesmo
            if _rc.hp <= 0:
                continue  # player morto
            hw = renderable.width / 2
            hh = renderable.height / 2
            if (pos.x - hw <= world_x <= pos.x + hw and
                    pos.y - hh <= world_y <= pos.y + hh):
                return entity_id
        return -1

    def _corpse_at_world_pos(self, world_x: float, world_y: float) -> bool:
        """Retorna True se há um cadáver na posição mundo."""
        for _, pos, _ in self.world.get_entities_with(Position, Corpse):
            if abs(world_x - pos.x) <= 14 and abs(world_y - pos.y) <= 10:
                return True
        return False

    def _visible_enemies_sorted(self, cam_x: float, cam_y: float) -> list[int]:
        """Retorna IDs de inimigos vivos e visíveis na tela, ordenados por distância ao jogador."""
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        player_pos = self.world.get_component(self.player_entity_id,
                                              __import__("engine.components", fromlist=["Position"]).Position)
        result = []
        for eid, pos, _, _ in self.world.get_entities_with(Position, Enemy, Visible):
            cs = self.world.get_component(eid, CombatStats)
            if cs and cs.current_hp <= 0:
                continue
            # Verifica se está dentro dos limites da tela
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
                                             __import__("engine.components", fromlist=["CombatState"]).CombatState)
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

            # Shift+clique esquerdo num player remoto → popup "Trade" local
            # (sem rede ainda — só abre o mini-popup; TRADE_REQUEST só sai
            # quando o botão "Trade" dentro dele é clicado, ver
            # client/trade_handlers.py). Não seleciona como alvo de combate.
            if (event.button == 1 and remote_player_id != -1
                    and pygame.key.get_mods() & pygame.KMOD_SHIFT):
                from engine.components import RemoteControlled as _RCtp
                from ui.ui_components import TradeUIState as _TUStp
                rc = self.world.get_component(remote_player_id, _RCtp)
                tui = self.world.get_component(self.player_entity_id, _TUStp)
                if tui is not None:
                    tui.popup_target_eid  = remote_player_id
                    tui.popup_target_name = rc.name if rc else "Jogador"
                    tui.popup_screen_pos  = event.pos
                continue

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
                    # Clique direito em inimigo → seleciona alvo, entra em combate e persegue
                    if player_cs:
                        player_cs.target_entity_id = target_id
                        player_cs.is_pursuing = True
                        enter_combat(player_cs)
                    if player_auto:
                        player_auto.ground_target = None
                        player_auto.path.clear()
                        player_auto.path_recalc_timer = 0.0
                else:
                    # Clique direito no chão → move para aquele tile
                    # (ignora se o clique foi num cadáver; LootSystem cuida disso)
                    if not self._corpse_at_world_pos(world_x, world_y):
                        tile_x = int(world_x / TILE_SIZE)
                        tile_y = int(world_y / TILE_SIZE)
                        if player_cs:
                            player_cs.is_pursuing = False  # para de perseguir, mantém alvo selecionado
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
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            cam_x = cam_pos.x - sw / 2
            cam_y = cam_pos.y - sh / 2
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            return 0 <= sx <= sw and 0 <= sy <= sh
        return True

    def _add_rage(self, entity_id: int, amount: int) -> None:
        """Adiciona raiva ao jogador, respeitando o limite máximo.

        Online: no-op — o servidor é o único produtor de rage (ganho por
        auto-attack em combat_processor.py, decay em ServerCombatStateSystem)
        e empurra todo valor novo via STATS_UPDATE. Gerar aqui também criava
        dois relógios independentes: a hotbar acendia com rage local que o
        servidor não tinha e o CAST_SKILL voltava "Raiva insuficiente"."""
        if self._net is not None:
            return
        cs = self.world.get_component(entity_id, CharacterStats)
        if cs:
            cs.rage = min(cs.max_rage, cs.rage + amount)

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
        occupied = set()
        for _, tm, _ in self.world.get_entities_with(TileMovement, Enemy):
            occupied.add((tm.current_tile_x, tm.current_tile_y))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        for _, tm, _ in self.world.get_entities_with(TileMovement, NPC):
            occupied.add((tm.current_tile_x, tm.current_tile_y))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        for _, tm, _ in self.world.get_entities_with(TileMovement, RemoteControlled):
            occupied.add((tm.current_tile_x, tm.current_tile_y))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))
        return occupied

    def update(self, events: list = None, dt: float = 0) -> None:
        if events is None:
            events = []
        keys = pygame.key.get_pressed()

        for entity_id, position, tile_movement, _, combat_stats in \
                self.world.get_entities_with(Position, TileMovement, PlayerControlled, CombatStats):

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

            # Disoriented/Polymorph/Sleep: bloqueia input (CombatStateSystem força
            # movimento aleatório em disoriented/polymorph; sleep fica imóvel até
            # expirar ou ser quebrado por dano). is_action_locked centraliza os 3 —
            # mesma checagem usada no gate de skills do servidor (skill_processor.py)
            # e de auto-attack (combat_processor.py), pra não divergir.
            from engine.utils import is_action_locked
            if is_action_locked(self.world, entity_id):
                can_move = False   # input bloqueado; CombatStateSystem move aleatoriamente
                can_act  = False   # não pode usar skills nem ataques

            # Campo de chat focado: WASD é lido via pygame.key.get_pressed()
            # aqui embaixo, não pelos eventos KEYDOWN que o filtro de
            # systems_events já bloqueia com modal aberto — sem este check,
            # digitar "w"/"a"/"s"/"d" numa mensagem também moveria o player.
            from ui.ui_components import UIState as _UIStateInp
            _ui_inp = self.world.get_component(entity_id, _UIStateInp)
            if _ui_inp is not None and _ui_inp.chat_active:
                can_move = False

            # --- Movimento por teclado ---
            if can_move and not tile_movement.is_moving:
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
                    # Teclado cancela auto-move e perseguição — Space re-engaja — B5
                    if auto_move:
                        auto_move.active = False
                        auto_move.path.clear()
                        auto_move.ground_target = None
                    # Arqueiro (can_kite): mover não cancela perseguição — pode atirar em movimento
                    _can_kite_kbm = combat_stats and getattr(combat_stats, "can_kite", False)
                    if combat_state and not _can_kite_kbm:
                        combat_state.is_pursuing = False
                    if _is_ghost or is_tile_walkable(
                            entity_id, tgt_x, tgt_y, cur_x, cur_y):
                        self._start_tile_movement(position, tile_movement, tgt_x, tgt_y)

            # --- Auto-move e auto-ataque em direção ao alvo selecionado ---
            _aoe_targeting = self.world.get_component(entity_id, AoeTargeting)
            _has_ground = auto_move and auto_move.active and auto_move.ground_target
            if combat_state and combat_state.target_entity_id != -1 and not _aoe_targeting:
                # Sempre chama _process_target para validação (limpa alvo morto/fora de visão)
                self._process_target(
                    entity_id, position, tile_movement,
                    combat_stats, combat_state, auto_move, can_act, dt
                )
                # Se não está perseguindo e há destino de chão, move para lá
                if not combat_state.is_pursuing and _has_ground:
                    self._process_ground_move(entity_id, position, tile_movement, auto_move, dt)
            # --- Movimento de chão (sem alvo selecionado) ---
            elif _has_ground:
                self._process_ground_move(entity_id, position, tile_movement, auto_move, dt)

            # --- ESPAÇO: seleciona inimigo mais próximo, entra em combate e ataca ---
            for event in events:
                if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    if can_act:
                        self._space_engage(entity_id, tile_movement, combat_stats, combat_state, auto_move)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _process_target(self, entity_id, position, tile_movement,
                        combat_stats, combat_state, auto_move, can_act, dt):
        """Auto-move e auto-ataque em direção ao alvo selecionado."""
        target_id = combat_state.target_entity_id

        target_pos = self.world.get_component(target_id, Position)
        target_tm = self.world.get_component(target_id, TileMovement)

        # Alvo morto ou removido: limpa seleção. is_target_alive cobre mob
        # local (CombatStats) E player remoto em PvP (RemoteControlled, sem
        # CombatStats — sem isso, o ciclo de ataque/som de nock fica girando
        # contra um corpo, já que current_hp não existe nesse alvo).
        from engine.utils import is_target_alive
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
                auto_move, can_act, target_id, tgt_tile_x, tgt_tile_y, dt,
                _px_chase, _melee_chase_px, dist_attack=dist_attack)
        elif is_mage:
            pursuit_range = self._mage_attack_range(entity_id)
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
                        if auto_move:
                            auto_move.active = False
            elif dist <= pursuit_range:
                # Dentro do alcance de skill: para e aguarda cast manual
                if auto_move:
                    auto_move.path.clear()
            elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
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
            elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
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
                               can_act, target_id, tgt_tile_x, tgt_tile_y, dt,
                               px_chase: float = 0.0,
                               melee_chase_px: float = float("inf"),
                               dist_attack: int = -1):
        """Auto-attack ranged do arqueiro: verifica arco+aljava e dispara flecha."""
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

        if dist <= bow_range:
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
                    _ADBG_c_empty.log_block(entity_id, target_id, "quiver_empty")
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

                # Online: flecha 100% server-driven — nasce em _apply_combat_result
                # ao chegar o COMBAT_RESULT (source="auto"), igual Bola de Fogo nasce
                # no is_completion. O desconto da aljava TAMBÉM só acontece lá agora
                # (mesmo evento que cria a flecha visual) — NUNCA aqui antecipado.
                # Bug real (10/07/2026): este cooldown LOCAL não congela do mesmo
                # jeito que o do servidor (server/combat_processor.py congela o
                # tick INTEIRO enquanto bloqueado por LOS/alcance/perseguição; aqui
                # só decrementa sem parar, então zera mais cedo sempre que há
                # bloqueio) — o cliente "atirava" (descontava flecha) bem mais vezes
                # que o servidor de verdade disparava (medido: 52 descontos locais
                # vs 29 tiros reais do servidor no mesmo teste), causando flecha
                # descontada sem projétil nenhum aparecer. Aqui só avançamos
                # cooldown/pré-tensionamento locais (feel de UI); ver
                # client/remote_entity_handlers.py::_apply_combat_result pro
                # desconto real.
                if self._net:
                    _ADBG_c_los.log_fire_ok(entity_id)
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    combat_stats.arrow_pre_draw_ready  = True
                    enter_combat(combat_state)
                    return

                from engine.components import PlayerProjectile as _PP
                proj_id = self.world.create_entity()
                self.world.add_component(proj_id, Position(
                    x=position.x, y=position.y,
                    prev_x=position.x, prev_y=position.y))
                # Flechas Despadronizadas: 15% de proc → +50% dano
                _proc_chance = getattr(combat_stats, "flechas_despadronizadas_chance", 0.0)
                _is_proc     = _proc_chance > 0 and random.random() < _proc_chance
                _dmg_mult    = 1.5 if _is_proc else 1.0
                _arrow_color = (220, 130, 20) if _is_proc else (101, 67, 33)

                self.world.add_component(proj_id, _PP(
                    spell_id="arrow",
                    attacker_id=entity_id,
                    target_id=target_id,
                    speed=700.0,
                    dmg_weapon_pct=1.0,
                    dmg_sp_coeff=0.0,
                    color=_arrow_color,
                    damage_type="physical",
                    arrow_dmg_min=getattr(quiver, "damage_min", 0),
                    arrow_dmg_max=getattr(quiver, "damage_max", 0),
                    damage_multiplier=_dmg_mult,
                ))
                if _is_proc:
                    from ui.floating_text import PROC as _PROC_FD
                    _PROC_FD.add("Despadronizada!", (220, 130, 20))
                quiver.arrow_count -= 1
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                combat_stats.arrow_pre_draw_ready  = True   # pronto para o próximo ciclo
                enter_combat(combat_state)
                # Disparo: draw (35%) + release (sempre)
                if random.random() < 0.35:
                    SOUNDS.play_random(["arrow_draw_1", "arrow_draw_2"], channel_group=(8, 9))
                SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))
        elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
            # Persegue o mob apenas quando is_pursuing=True — evita sobrescrever
            # ground_target (clique de chão com is_pursuing=False).
            auto_move.active = True
            self._auto_move_step(entity_id, position, tile_movement,
                                 pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y,
                                 auto_move, dt, attack_range=bow_range,
                                 target_eid=target_id)

    def _ranged_stop_tile(self, pl_x: int, pl_y: int,
                          tgt_x: int, tgt_y: int, attack_range: int) -> tuple:
        """Tile de parada para ranged: (attack_range-1) tiles do alvo na direção do player."""
        dx = pl_x - tgt_x
        dy = pl_y - tgt_y
        cheb = max(abs(dx), abs(dy))
        if cheb == 0:
            return (pl_x, pl_y)
        ratio = (attack_range - 1) / cheb
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

    def _process_ground_move(self, entity_id, position, tile_movement, auto_move, dt):
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

    def _start_tile_movement(self, position, tile_movement, tgt_x, tgt_y):
        start_tile_movement(position, tile_movement, tgt_x, tgt_y)

    def _manual_attack(self, entity_id, tile_movement, combat_stats, combat_state):
        """ESPAÇO: ataca alvo selecionado se no alcance, senão o inimigo mais próximo."""
        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y

        # Tenta alvo selecionado primeiro
        if combat_state and combat_state.target_entity_id != -1:
            tid = combat_state.target_entity_id
            tm = self.world.get_component(tid, TileMovement)
            if tm:
                dist = chebyshev(pl_x, pl_y, tm.current_tile_x, tm.current_tile_y)
                if dist <= self.PLAYER_ATTACK_RANGE:
                    dead, _ = deal_damage(entity_id, tid, "physical")
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    self._add_rage(entity_id, 5)
                    enter_combat(combat_state)
                    self._increment_pnq_counter(entity_id)
                    if dead:
                        combat_state.target_entity_id = -1
                    return

        # Sem alvo no alcance: ataca o inimigo mais próximo
        for eid, _, _, etm in self.world.get_entities_with(Enemy, AIControlled, TileMovement):
            dist = chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y)
            if dist <= self.PLAYER_ATTACK_RANGE:
                deal_damage(entity_id, eid, "physical")
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                self._add_rage(entity_id, 5)
                if combat_state:
                    enter_combat(combat_state)
                break

    def _space_engage(self, entity_id, tile_movement, combat_stats, combat_state, auto_move):
        """ESPAÇO: seleciona inimigo mais próximo visível na tela, entra em combate e ataca."""
        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y

        _fog_vis_se = None
        for _, _fw_se in self.world.get_entities_with(FogOfWar):
            _fog_vis_se = _fw_se.visible
            break

        best_eid  = -1
        best_dist = float("inf")
        for eid, epos, _, _, etm, ecs, _ in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats, Visible):
            if ecs.current_hp <= 0:
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

        if best_eid == -1:
            return

        if combat_state:
            combat_state.target_entity_id = best_eid
            combat_state.is_pursuing = True
            enter_combat(combat_state)
        if auto_move:
            auto_move.ground_target = None
            auto_move.path.clear()
            auto_move.path_recalc_timer = 0.0

        # Ataca imediatamente se já estiver no alcance
        if best_dist <= self.PLAYER_ATTACK_RANGE and combat_stats.attack_cooldown_timer <= 0:
            dead, _ = deal_damage(entity_id, best_eid, "physical")
            combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
            self._add_rage(entity_id, 5)
            if dead and combat_state:
                combat_state.target_entity_id = -1






class RenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen
        # CachedFont: timers de efeito re-renderizam todo frame (texto "3.4s"
        # por entidade) — cache na fonte evita o custo do TTF render por frame.
        from ui.fonts import CachedFont as _CF
        self._effect_dur_font = _CF(None, 18)

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0,
               world_objects: list = None) -> None:
        """
        Renderiza entidades e tile-objetos (árvores, pedras, etc.) em Y-sort.

        world_objects — lista retornada por TileRenderSystem.get_world_objects().
        Objetos com sort_y menor são desenhados primeiro (ficam atrás de quem está
        mais ao sul na tela), criando o efeito de profundidade.
        """
        # Descobre qual entidade o jogador tem como alvo
        target_id = -1
        for _, cs, _ in self.world.get_entities_with(CombatState, PlayerControlled):
            target_id = cs.target_entity_id
            break

        # ── Fog of War: conjunto de tiles visíveis neste frame ────────────────
        _fog_visible:   set | None = None
        _fog_explored:  set | None = None
        for _, _fog in self.world.get_entities_with(FogOfWar):
            _fog_visible  = _fog.visible
            _fog_explored = _fog.explored
            break

        # ── Coleta drawables: (sort_y_world, tipo, dados) ─────────────────────
        drawables = []

        # Entidades vivas
        for entity_id, position, renderable in self.world.get_entities_with(Position, Renderable):
            combat_stats = self.world.get_component(entity_id, CombatStats)
            _gst_rnd = self.world.get_component(entity_id, GhostState)
            # Corpo (morto, espírito ainda não liberado): permanece visível no
            # local da morte. Ghost (espírito liberado): renderiza semi-transparente.
            _is_corpse_rnd = _gst_rnd is not None and _gst_rnd.is_dead and not _gst_rnd.is_ghost
            _is_ghost_rnd  = _gst_rnd is not None and _gst_rnd.is_ghost
            if combat_stats and combat_stats.current_hp <= 0 and not _is_corpse_rnd and not _is_ghost_rnd:
                continue
            # Oculta entidades fora do campo de visão (jogador local e players
            # remotos nunca são ocultos por fog — LOS/exploração é mecânica de
            # "neblina sobre mobs/mapa", não deve esconder outro jogador real
            # dentro do AOI; invisibilidade de player é regra do servidor
            # (CombatState.is_visible/_can_see), não de fog of war).
            if _fog_visible is not None:
                is_player = (self.world.get_component(entity_id, PlayerControlled) is not None
                             or self.world.get_component(entity_id, RemoteControlled) is not None)
                if not is_player:
                    etx = int(position.x / TILE_SIZE)
                    ety = int(position.y / TILE_SIZE)
                    if (etx, ety) not in _fog_visible:
                        continue
            foot_y = position.y + renderable.height / 2
            drawables.append((foot_y, "entity", entity_id, position, renderable, combat_stats, _gst_rnd))

        # Tile-objetos (árvores, arbustos, pedras — objetos estáticos do mapa)
        if world_objects:
            for obj in world_objects:
                # Objetos estáticos aparecem tanto em tiles visíveis quanto
                # explorados (o fog overlay escurece os explorados automaticamente).
                if _fog_visible is not None and _fog_explored is not None:
                    tx, ty = obj.get("tile_x", -1), obj.get("tile_y", -1)
                    if (tx, ty) not in _fog_visible and (tx, ty) not in _fog_explored:
                        continue
                drawables.append((obj["sort_y"], "object", obj))

        # ── Ordena por Y do pé (sul = frente) ─────────────────────────────────
        drawables.sort(key=lambda d: d[0])

        # ── Desenha na ordem ──────────────────────────────────────────────────
        for item in drawables:
            if item[1] == "object":
                obj = item[2]
                sx, sy, w, h = obj["screen_x"], obj["screen_y"], obj["width"], obj["height"]
                if obj["sprite"] is not None:
                    self.world_surf.blit(obj["sprite"], (sx, sy))
                else:
                    pygame.draw.rect(self.world_surf, obj["color"], (sx, sy, w, h))
                continue

            # ── Entidade ──────────────────────────────────────────────────────
            _, _, entity_id, position, renderable, combat_stats, _gst_draw = item
            _is_corpse_draw = _gst_draw is not None and _gst_draw.is_dead and not _gst_draw.is_ghost
            _is_ghost_draw  = _gst_draw is not None and _gst_draw.is_ghost

            draw_x = position.x - camera_offset_x
            draw_y = position.y - camera_offset_y

            _sfx_rnd = self.world.get_component(entity_id, StatusEffects)
            _polymorphed = _sfx_rnd is not None and _sfx_rnd.has("polymorph")

            # ── Camuflagem: desenha sprite animado idle/run do disfarce ────────
            # Gate por camouflage_timer (não pela string de camouflage_object —
            # a variante base tem sufixo "", que seria falsy numa checagem direta).
            _cam_active = bool(combat_stats and getattr(combat_stats, "camouflage_timer", 0.0) > 0)
            if _cam_active:
                from engine.tileset import get_camouflage_disguise_frame
                _cam_tm     = self.world.get_component(entity_id, TileMovement)
                _cam_moving = bool(_cam_tm and _cam_tm.is_moving)
                _cam_suffix = getattr(combat_stats, "camouflage_object", "") or ""
                _cam_sprite = get_camouflage_disguise_frame(
                    _cam_suffix, _cam_moving, pygame.time.get_ticks())
                if _cam_sprite:
                    _sw = _cam_sprite.get_width()
                    _sh = _cam_sprite.get_height()
                    # Alinha o fundo do sprite ao pé da entidade
                    _blit_x = int(draw_x - _sw / 2)
                    _blit_y = int(draw_y + 12 - _sh)  # +12 = offset do pé do jogador
                    self.world_surf.blit(_cam_sprite, (_blit_x, _blit_y))
                rect = pygame.Rect(int(draw_x - 16), int(draw_y - 16), 32, 32)
            elif _polymorphed:
                _cx = int(draw_x)
                _cy = int(draw_y)
                pygame.draw.circle(self.world_surf, (160, 80, 200), (_cx, _cy), 14)
                pygame.draw.circle(self.world_surf, (220, 180, 255), (_cx, _cy), 14, 2)
                rect = pygame.Rect(_cx - 14, _cy - 14, 28, 28)
            else:
                rect = pygame.Rect(
                    int(draw_x - renderable.width / 2),
                    int(draw_y - renderable.height / 2),
                    renderable.width,
                    renderable.height
                )
                if _is_corpse_draw:
                    # Corpo morto: dessatura pra cinza (pose "morto", sem barra de HP)
                    _gray = sum(renderable.color[:3]) // 3
                    pygame.draw.rect(self.world_surf, (_gray, _gray, _gray), rect)
                elif _is_ghost_draw:
                    # Espírito: semi-transparente
                    self.world_surf.blit(fill_surf((rect.width, rect.height), (*renderable.color[:3], 127)), rect.topleft)
                else:
                    pygame.draw.rect(self.world_surf, renderable.color, rect)

            # ── Efeitos de chão: desenhados NA FRENTE do retângulo da entidade ──
            # Verificação direta em StatusEffects (sem exigir CombatStats) para
            # funcionar com mobs remotos, player local e futuros players PvP.
            if _sfx_rnd is not None:
                # Root (Nova Congelante e similares): frozen_root.png na base
                if _sfx_rnd.has("root"):
                    _root_surf = _get_ground_effect_sprite("root")
                    if _root_surf is not None:
                        _rw = _root_surf.get_width()
                        _rh = _root_surf.get_height()
                        # Centralizado no mesmo tile da entidade (ambos 32×32)
                        _rx = int(draw_x - _rw / 2)
                        _ry = int(draw_y - _rh / 2)
                        self.world_surf.blit(_root_surf, (_rx, _ry))

            if entity_id == target_id:
                pygame.draw.rect(self.world_surf, (255, 220, 0), rect, 2)

            # ── HP bar: mobs/player offline (CombatStats) ou players remotos (RemoteControlled) ──
            _rc_hp = None if combat_stats else self.world.get_component(entity_id, RemoteControlled)
            _draw_hp_bar = ((combat_stats and combat_stats.max_hp > 0) or
                            (_rc_hp is not None and _rc_hp.hp_max > 0)) \
                           and not _is_corpse_draw and not _is_ghost_draw
            if _draw_hp_bar:
                if combat_stats:
                    ratio  = max(0.0, min(1.0, combat_stats.current_hp / combat_stats.max_hp))
                    bar_fg = (0, 200, 60)
                    bar_bg = (80, 0, 0)
                else:
                    # Player remoto (PvP) — roxo para diferenciar de mob
                    ratio  = max(0.0, min(1.0, _rc_hp.hp / _rc_hp.hp_max))
                    bar_fg = (200, 80, 220)
                    bar_bg = (50, 0, 60)
                bar_w = renderable.width
                bar_h = 4
                bar_x = int(draw_x - renderable.width / 2)
                bar_y = int(draw_y - renderable.height / 2) - 7
                pygame.draw.rect(self.world_surf, bar_bg, (bar_x, bar_y, bar_w, bar_h))
                pygame.draw.rect(self.world_surf, bar_fg, (bar_x, bar_y, int(bar_w * ratio), bar_h))

                _sfx = self.world.get_component(entity_id, StatusEffects)
                _cst = self.world.get_component(entity_id, CombatState)

                # Reúne efeitos ativos
                _active_effects = list(_sfx.effects.values()) if _sfx else []
                if _cst and _cst.is_stunned and _cst.stun_timer > 0:
                    if not (_sfx and _sfx.has("stun")):
                        _stun_timer_val = _cst.stun_timer
                        class _FakeEff:
                            effect_type = "stun"
                            duration    = _stun_timer_val
                        _active_effects.append(_FakeEff())

                if _active_effects:
                    _draw_effect_icons(self.world_surf, draw_x, bar_y,
                                       _active_effects, self._effect_dur_font)

class CameraSystem(System):
    def __init__(self, world: World):
        self.world = world

    LERP_SPEED = 8.0  # maior = mais rápido; ~8 é suave mas responsivo

    def update(self, events: list = None, dt: float = 0) -> None:
        for entity_id, camera_component, camera_position in self.world.get_entities_with(Camera, Position):
            target_entity_id = camera_component.target_entity_id

            if target_entity_id != -1:
                target_position = self.world.get_component(target_entity_id, Position)
                if target_position:
                    t = min(1.0, self.LERP_SPEED * dt)
                    camera_position.x += (target_position.x - camera_position.x) * t
                    camera_position.y += (target_position.y - camera_position.y) * t

class TileRenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen
        from ui.tile_sprite_manager import TILE_SPRITES as _TS
        from engine.tileset import TILE_MAPPING as _TM, FLOOR_TILE as _FT
        self._tile_sprites = _TS
        self._tile_mapping = _TM
        self._floor_tile   = _FT
        _tw = screen.get_width()  // TILE_SIZE + 2
        _th = screen.get_height() // TILE_SIZE + 2
        # Cache de surface — pré-alocada; reconstruída apenas quando a câmera cruza fronteira de tile
        self._cache_surf    = pygame.Surface((_tw * TILE_SIZE, _th * TILE_SIZE))
        self._cache_tile_x:    int = -99999
        self._cache_tile_y:    int = -99999
        self._cache_tiles_w:   int = _tw
        self._cache_tiles_h:   int = _th
        # Fog of War: névoa leve para tiles explorados mas fora do campo de visão
        self._fog_explored_surf: pygame.Surface = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
        self._fog_explored_surf.fill((0, 0, 0, 25))
        # Fog of War: gradiente de fade na borda do campo de visão.
        # FOG_FADE_LEVELS superfícies com alpha crescente de ~0 até FOG_FADE_MAX_ALPHA.
        # FOG_FADE_START: fração do raio a partir da qual o fade começa (0.0–1.0).
        FOG_FADE_LEVELS    = 10
        FOG_FADE_START     = 0.60   # fade começa a 60% do raio
        FOG_FADE_MAX_ALPHA = 25     # alpha máximo na borda — igual ao explorado (transição contínua)
        self._fog_fade_start:     float = FOG_FADE_START
        self._fog_fade_surfs: list = []
        for i in range(FOG_FADE_LEVELS):
            t     = (i + 1) / FOG_FADE_LEVELS          # 0.1 → 1.0
            alpha = max(1, int(t * FOG_FADE_MAX_ALPHA))
            s = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
            s.fill((0, 0, 0, alpha))
            self._fog_fade_surfs.append(s)
        # Cache do overlay de fog — reconstrói apenas quando o tile de origem muda
        self._fog_overlay_surf: "pygame.Surface | None" = None
        self._fog_cache_tile_ox: int = -99999
        self._fog_cache_tile_oy: int = -99999
        self._pending_fog_blit = None

    def invalidate_cache(self) -> None:
        """Força reconstrução do cache no próximo frame (chamar após troca de mapa)."""
        self._cache_tile_x      = -99999
        self._cache_tile_y      = -99999
        self._fog_cache_tile_ox = -99999
        self._fog_cache_tile_oy = -99999
        self._tile_sprites.invalidate()

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            tile_size = tilemap_comp.tile_size

            cam_x = int(camera_offset_x)
            cam_y = int(camera_offset_y)

            tile_ox = cam_x // tile_size
            tile_oy = cam_y // tile_size
            sub_x   = cam_x - tile_ox * tile_size
            sub_y   = cam_y - tile_oy * tile_size

            tiles_w = self.world_surf.get_width()  // tile_size + 2
            tiles_h = self.world_surf.get_height() // tile_size + 2

            _size_changed = (tiles_w != self._cache_tiles_w
                             or tiles_h != self._cache_tiles_h
                             or self._cache_surf is None)
            dx = tile_ox - self._cache_tile_x
            dy = tile_oy - self._cache_tile_y

            # Reconstrói ou redimensiona cache se necessário
            surf_w = tiles_w * tile_size
            surf_h = tiles_h * tile_size
            if (_size_changed
                    or self._cache_surf is None
                    or self._cache_surf.get_width()  != surf_w
                    or self._cache_surf.get_height() != surf_h):
                self._cache_surf = pygame.Surface((surf_w, surf_h))
                _size_changed = True

            # Pré-cache de todos os locais pesados — evita lookup de atributo por tile.
            # (closure captura esses locais; chamada de método re-faria os lookups 1760x)
            cache_surf   = self._cache_surf
            TILE_SPRITES = self._tile_sprites
            _TM          = self._tile_mapping
            _FT          = self._floor_tile
            rows         = tilemap_comp.tile_matrix
            terrain_rows = tilemap_comp.terrain_matrix
            vis_rows     = tilemap_comp.terrain_visual
            obj_rows     = tilemap_comp.object_matrix
            map_h        = tilemap_comp.map_height_tiles
            map_w        = tilemap_comp.map_width_tiles
            _blit        = cache_surf.blit
            _rect        = pygame.draw.rect
            _black       = (0, 0, 0)
            _get_spr     = TILE_SPRITES.get
            _get_raw     = TILE_SPRITES.get_raw_sprite

            def _draw(tx: int, ty: int, rx: int, ry: int) -> None:
                dest = (tx * tile_size, ty * tile_size, tile_size, tile_size)
                if 0 <= ry < map_h and 0 <= rx < map_w:
                    tile_type = rows[ry][rx]
                    vis_id = (vis_rows[ry][rx]
                              if vis_rows and ry < len(vis_rows) and rx < len(vis_rows[ry])
                              else "")
                    has_obj = (obj_rows and ry < len(obj_rows)
                               and rx < len(obj_rows[ry])
                               and obj_rows[ry][rx] not in ("", "."))
                    if vis_id:
                        spr = _get_raw(vis_id)
                        if spr is not None:
                            _blit(spr, dest[:2])
                        else:
                            t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                            _rect(cache_surf, _TM.get(t_char, _FT).color, dest)
                    elif has_obj or getattr(tile_type, "sprite_px_h", 0) > 0:
                        t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                        _rect(cache_surf, _TM.get(t_char, _FT).color, dest)
                    elif tile_type.overlay_height > 0:
                        _rect(cache_surf, tile_type.color, dest)
                    else:
                        sprite = _get_spr(tile_type, rx, ry)
                        if sprite is not None:
                            _blit(sprite, dest[:2])
                        else:
                            _rect(cache_surf, tile_type.color, dest)
                else:
                    _rect(cache_surf, _black, dest)

            if _size_changed or abs(dx) > 2 or abs(dy) > 2:
                # Rebuild completo
                for ty in range(tiles_h):
                    for tx in range(tiles_w):
                        _draw(tx, ty, tile_ox + tx, tile_oy + ty)
            elif dx != 0 or dy != 0:
                # Cache-shift: desloca pixels e redesenha só as bordas expostas.
                # dx>0 = câmera foi p/ direita (conteúdo desloca p/ esquerda no cache).
                cache_surf.scroll(-dx * tile_size, -dy * tile_size)

                # Colunas expostas
                if dx > 0:
                    col_range = range(tiles_w - dx, tiles_w)
                elif dx < 0:
                    col_range = range(0, -dx)
                else:
                    col_range = range(0, 0)

                # Linhas expostas
                if dy > 0:
                    row_range = range(tiles_h - dy, tiles_h)
                elif dy < 0:
                    row_range = range(0, -dy)
                else:
                    row_range = range(0, 0)

                # Redesenha somente tiles novos (coluna nova + linha nova)
                drawn: set = set()
                for tx in col_range:
                    for ty in range(tiles_h):
                        _draw(tx, ty, tile_ox + tx, tile_oy + ty)
                        drawn.add((tx, ty))
                for ty in row_range:
                    for tx in range(tiles_w):
                        if (tx, ty) not in drawn:
                            _draw(tx, ty, tile_ox + tx, tile_oy + ty)

            self._cache_tile_x  = tile_ox
            self._cache_tile_y  = tile_oy
            self._cache_tiles_w = tiles_w
            self._cache_tiles_h = tiles_h

            # 1 blit por frame
            self.world_surf.blit(cache_surf, (-sub_x, -sub_y))

            # Fog é desenhado separadamente via render_fog() para permitir
            # que outros sistemas (quest, shop) desenhem seus indicadores
            # world-space ANTES do overlay de fog ser aplicado.
            self._pending_fog_blit = (tile_ox, tile_oy, tiles_w, tiles_h, sub_x, sub_y, tile_size)

    def render_fog(self) -> None:
        """Aplica o overlay de fog of war sobre tudo que foi desenhado até agora.
        Deve ser chamado APÓS render() e APÓS render_world() de todos os sistemas world-space."""
        if not hasattr(self, "_pending_fog_blit") or self._pending_fog_blit is None:
            return
        tile_ox, tile_oy, tiles_w, tiles_h, sub_x, sub_y, tile_size = self._pending_fog_blit
        self._pending_fog_blit = None

        fog_comp = None
        for _, fog in self.world.get_entities_with(FogOfWar):
            fog_comp = fog
            break

        if fog_comp is None:
            return

        # Pega tilemap para checar extensões superiores de objetos altos
        tilemap_fog = None
        for _, tm in self.world.get_entities_with(Tilemap):
            tilemap_fog = tm
            break

        if (tile_ox != self._fog_cache_tile_ox
                or tile_oy != self._fog_cache_tile_oy
                or self._fog_overlay_surf is None):

            surf_w = tiles_w * tile_size
            surf_h = tiles_h * tile_size
            if (self._fog_overlay_surf is None
                    or self._fog_overlay_surf.get_width()  != surf_w
                    or self._fog_overlay_surf.get_height() != surf_h):
                self._fog_overlay_surf = pygame.Surface((surf_w, surf_h), pygame.SRCALPHA)

            self._fog_overlay_surf.fill((0, 0, 0, 0))

            explored   = fog_comp.explored
            visible    = fog_comp.visible
            exp_surf   = self._fog_explored_surf
            fade_surfs = self._fog_fade_surfs
            n_levels   = len(fade_surfs)
            fade_start = self._fog_fade_start
            px, py     = fog_comp._last_tile
            radius     = fog_comp.radius
            fade_begin = fade_start * radius
            fade_range = radius - fade_begin

            # Pré-calcula tiles que são extensão superior de objetos visíveis altos.
            # Para esses tiles o fog não é aplicado: o sprite do objeto cobre essa área
            # e deve aparecer acima do fog quando a base está visível.
            _skip_fog: set = set()
            if tilemap_fog is not None:
                _rows    = tilemap_fog.tile_matrix
                _obj     = tilemap_fog.object_matrix
                _map_h   = tilemap_fog.map_height_tiles
                _map_w   = tilemap_fog.map_width_tiles
                for _ry in range(tile_oy, min(tile_oy + tiles_h, _map_h)):
                    for _rx in range(tile_ox, min(tile_ox + tiles_w, _map_w)):
                        if (_rx, _ry) in visible:
                            continue  # visível → fog já não cobre, irrelevante
                        # Tile tem TileType de sprite alto sem objeto direto aqui?
                        _tile = _rows[_ry][_rx] if _ry < len(_rows) and _rx < len(_rows[_ry]) else None
                        if _tile is None:
                            continue
                        _sprite_h = getattr(_tile, "sprite_px_h", 0)
                        if _sprite_h <= TILE_SIZE:
                            continue  # sprite não é multi-tile
                        _obj_here = (_obj[_ry][_rx]
                                     if _ry < len(_obj) and _rx < len(_obj[_ry]) else ".")
                        if _obj_here and _obj_here != ".":
                            continue  # tem objeto direto aqui — não é extensão superior
                        # Verifica tiles abaixo: algum tem objeto visível que sobe até aqui?
                        _max_delta = _sprite_h // TILE_SIZE
                        for _dy in range(1, _max_delta + 1):
                            _base_y = _ry + _dy
                            if not (0 <= _base_y < _map_h):
                                break
                            _obj_base = (_obj[_base_y][_rx]
                                         if _base_y < len(_obj) and _rx < len(_obj[_base_y]) else ".")
                            if _obj_base and _obj_base != "." and (_rx, _base_y) in visible:
                                _skip_fog.add((_rx, _ry))
                                break

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    dx_s   = tx * tile_size
                    dy_s   = ty * tile_size
                    if (rx, ry) in visible:
                        if fade_range > 0:
                            d = max(abs(rx - px), abs(ry - py))
                            if d > fade_begin:
                                t     = (d - fade_begin) / fade_range
                                level = min(n_levels - 1, int(t * n_levels))
                                self._fog_overlay_surf.blit(fade_surfs[level], (dx_s, dy_s))
                    elif (rx, ry) in _skip_fog:
                        pass  # extensão superior de objeto visível — não aplica fog
                    elif (rx, ry) in explored:
                        self._fog_overlay_surf.blit(exp_surf, (dx_s, dy_s))
                    else:
                        pygame.draw.rect(self._fog_overlay_surf, (0, 0, 0, 255),
                                         (dx_s, dy_s, tile_size, tile_size))

            self._fog_cache_tile_ox = tile_ox
            self._fog_cache_tile_oy = tile_oy

        self.world_surf.blit(self._fog_overlay_surf, (-sub_x, -sub_y))

    def get_world_objects(self, camera_offset_x: float, camera_offset_y: float) -> list:
        """
        Retorna lista de objetos visíveis (object_matrix) para o pass 2 (Y-sort).
        Cada item: dict com sort_y, screen_x, screen_y, sprite, color, width, height.
        """
        from ui.tile_sprite_manager import TILE_SPRITES
        objects = []
        cam_x = int(camera_offset_x)
        cam_y = int(camera_offset_y)

        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            tile_size = tilemap_comp.tile_size
            obj_rows  = tilemap_comp.object_matrix
            map_h     = tilemap_comp.map_height_tiles
            map_w     = tilemap_comp.map_width_tiles

            tile_ox = cam_x // tile_size
            tile_oy = cam_y // tile_size
            tiles_w = self.world_surf.get_width()  // tile_size + 2
            tiles_h = self.world_surf.get_height() // tile_size + 2

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    if not (0 <= ry < map_h and 0 <= rx < map_w):
                        continue
                    if ry >= len(obj_rows):
                        continue
                    obj_row  = obj_rows[ry]
                    obj_char = obj_row[rx] if rx < len(obj_row) else "."
                    if not obj_char or obj_char == ".":
                        continue

                    tile_type = OBJECT_MAPPING.get(obj_char)
                    if tile_type is None:
                        continue

                    # Objetos com no_ysort=True são renderizados por render_static_objects()
                    if getattr(tile_type, "no_ysort", False):
                        continue

                    # Sprite tree PNG (tamanho real) ou objeto legacy (overlay_height)
                    spr_px_w = getattr(tile_type, "sprite_px_w", 0)
                    spr_px_h = getattr(tile_type, "sprite_px_h", 0)
                    spr_name = getattr(tile_type, "sprite_name", "")
                    if spr_px_w > 0 and spr_px_h > 0 and spr_name:
                        sprite_w = spr_px_w
                        total_h  = spr_px_h
                        sprite   = TILE_SPRITES.get_raw_sprite(spr_name)
                    else:
                        tiles_wide = getattr(tile_type, "sprite_tiles_wide", 1)
                        sprite_w   = tile_size * tiles_wide
                        total_h    = tile_size + tile_type.overlay_height
                        sprite     = TILE_SPRITES.get(tile_type, rx, ry)

                    sort_y   = ry * tile_size + tile_size // 2
                    anchor_y = (ry + 1) * tile_size
                    scr_x    = rx * tile_size - cam_x
                    scr_y    = anchor_y - cam_y - total_h

                    objects.append({
                        "sort_y":   sort_y,
                        "screen_x": scr_x,
                        "screen_y": scr_y,
                        "sprite":   sprite,
                        "color":    tile_type.color,
                        "width":    sprite_w,
                        "height":   total_h,
                        "tile_x":   rx,
                        "tile_y":   ry,
                    })
        return objects

    def render_static_objects(self, camera_offset_x: float, camera_offset_y: float) -> None:
        """Renderiza objetos com no_ysort=True diretamente no world_surf, abaixo de entidades."""
        from ui.tile_sprite_manager import TILE_SPRITES
        cam_x = int(camera_offset_x)
        cam_y = int(camera_offset_y)

        # Fog: só objetos em tiles visíveis ou explorados
        _fog_visible  = None
        _fog_explored = None
        for _, fog in self.world.get_entities_with(FogOfWar):
            _fog_visible  = fog.visible
            _fog_explored = fog.explored
            break

        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            tile_size = tilemap_comp.tile_size
            obj_rows  = tilemap_comp.object_matrix
            map_h     = tilemap_comp.map_height_tiles
            map_w     = tilemap_comp.map_width_tiles
            tile_ox   = cam_x // tile_size
            tile_oy   = cam_y // tile_size
            tiles_w   = self.world_surf.get_width()  // tile_size + 2
            tiles_h   = self.world_surf.get_height() // tile_size + 2

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    if not (0 <= ry < map_h and 0 <= rx < map_w):
                        continue
                    if _fog_visible is not None and _fog_explored is not None:
                        if (rx, ry) not in _fog_visible and (rx, ry) not in _fog_explored:
                            continue
                    if ry >= len(obj_rows):
                        continue
                    obj_row  = obj_rows[ry]
                    obj_char = obj_row[rx] if rx < len(obj_row) else "."
                    if not obj_char or obj_char == ".":
                        continue
                    tile_type = OBJECT_MAPPING.get(obj_char)
                    if tile_type is None or not getattr(tile_type, "no_ysort", False):
                        continue

                    spr_px_w = getattr(tile_type, "sprite_px_w", 0)
                    spr_px_h = getattr(tile_type, "sprite_px_h", 0)
                    spr_name = getattr(tile_type, "sprite_name", "")
                    if spr_px_w > 0 and spr_px_h > 0 and spr_name:
                        sprite  = TILE_SPRITES.get_raw_sprite(spr_name)
                        total_h = spr_px_h
                        sprite_w = spr_px_w
                    else:
                        sprite   = TILE_SPRITES.get(tile_type, rx, ry)
                        total_h  = tile_size + tile_type.overlay_height
                        sprite_w = tile_size

                    anchor_y = (ry + 1) * tile_size
                    scr_x    = rx * tile_size - cam_x
                    scr_y    = anchor_y - cam_y - total_h

                    if sprite is not None:
                        self.world_surf.blit(sprite, (scr_x, scr_y))
                    else:
                        pygame.draw.rect(self.world_surf, tile_type.color,
                                         (scr_x, scr_y, sprite_w, total_h))


class FogSystem(System):
    """
    Atualiza o campo de visão do jogador por shadowcasting recursivo (8 octantes).

    Executa apenas quando o jogador muda de tile — custo O(radius²) por frame
    de movimento, zero nos frames sem deslocamento.
    """

    def __init__(self, world: World) -> None:
        self.world = world

    def update(self, events: list = None, dt: float = 0) -> None:
        tilemap = None
        for _, tm in self.world.get_entities_with(Tilemap):
            tilemap = tm
            break
        if tilemap is None:
            return

        rows  = tilemap.tile_matrix
        map_h = tilemap.map_height_tiles
        map_w = tilemap.map_width_tiles

        def is_blocking(x: int, y: int) -> bool:
            if not (0 <= x < map_w and 0 <= y < map_h):
                return True
            return rows[y][x].vision_height >= 2

        fog = None
        for _, f, tile_move in self.world.get_entities_with(FogOfWar, TileMovement):
            fog = f
            px, py = tile_move.current_tile_x, tile_move.current_tile_y

            # Recomputa LOS apenas quando o jogador muda de tile
            if (px, py) != fog._last_tile:
                fog._last_tile = (px, py)

                # LOS (shadowcasting, raio pequeno) — controla quais entidades são visíveis
                fog.visible = compute_fov(px, py, fog.radius, is_blocking)

                # Exploração (círculo largo) — descobre tiles para o mapa/tela sem LOS
                er = fog.explore_radius
                er_sq = er * er
                for dy in range(-er, er + 1):
                    for dx in range(-er, er + 1):
                        if dx * dx + dy * dy <= er_sq:
                            ex, ey = px + dx, py + dy
                            if 0 <= ex < map_w and 0 <= ey < map_h:
                                fog.explored.add((ex, ey))
            break  # apenas um FogOfWar no jogo (jogador)

        if fog is None:
            return

        # Atualiza tag Visible em todos os inimigos e NPCs a cada frame.
        # Necessário mesmo sem movimento do jogador (entidades podem mudar de tile).
        for eid, _, etm in self.world.get_entities_with(Enemy, TileMovement):
            in_sight = (etm.current_tile_x, etm.current_tile_y) in fog.visible
            has_tag  = self.world.get_component(eid, Visible) is not None
            if in_sight and not has_tag:
                self.world.add_component(eid, Visible())
            elif not in_sight and has_tag:
                self.world.remove_component(eid, Visible)

        # NPCs estáticos — rastreados via componente NPC (único ponto independente de capacidades)
        for eid, _, npos in self.world.get_entities_with(NPC, Position):
            ntx = int(npos.x / TILE_SIZE)
            nty = int(npos.y / TILE_SIZE)
            in_sight = (ntx, nty) in fog.visible
            has_tag  = self.world.get_component(eid, Visible) is not None
            if in_sight and not has_tag:
                self.world.add_component(eid, Visible())
            elif not in_sight and has_tag:
                self.world.remove_component(eid, Visible)












class ShopSystem(UIScaleMixin, System):
    """
    Sistema de comerciantes NPC.
    - Clique direito no NPC → abre painel de loja.
    - Painel esquerdo: itens à venda (clique direito = comprar).
    - Painel direito: mochila do jogador (clique direito = vender).
    - Botão [↩ Desfazer] reverte a última transação.
    """

    _FONT_BASES = {"_font_sm": 22, "_font_md": 30, "_font_lg": 38}

    PANEL_W       = UI.SHOP_W
    PANEL_H       = UI.SHOP_H
    ROW_H         = UI.SHOP_ROW_H
    ICON_S        = UI.SHOP_ICON_SZ
    MAX_ROWS      = UI.SHOP_MAX_ROWS
    LEFT_W        = UI.SHOP_LEFT_W
    RIGHT_W       = UI.SHOP_RIGHT_W
    GAP           = UI.SHOP_GAP
    BODY_Y_OFFSET = UI.SHOP_BODY_Y_OFFSET   # distância do topo do painel até a primeira linha de item
    FOOTER_H      = UI.SHOP_FOOTER_H        # altura reservada para ouro + dica no rodapé
    SELL_RATIO    = 0.4
    MAX_HISTORY   = 20

    _RARITY_COLORS = {
        "common":    (200, 200, 200),
        "uncommon":  ( 30, 200,  30),
        "rare":      ( 80, 140, 255),
        "epic":      (180,  50, 255),
        "legendary": (224, 135,  47),
        "mythic":    (221,  68,  68),
    }

    def __init__(self, world: World, player_entity: int, screen):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        # open_merchant_id → ShopUIState component (via property abaixo)
        self._pending_merchant_id: int   = -1   # aguardando jogador chegar
        self._right_click_consumed: bool = False
        self.transaction_history: list = []
        self._shop_scroll: int = 0
        self._bag_scroll:  int = 0
        # Online: injetado pelo GameEngine. None = modo offline (lógica local)
        self._net = None
        self.pending_tooltip = None
        self._open_cooldown: float = 0.0  # impede compra/venda logo após abrir a loja
        # Modal de quantidade (Shift+clique direito em item stackável)
        self._qty_modal: dict | None = None  # None = fechado

        SW, SH = screen.get_size()

    @property
    def open_merchant_id(self) -> int:
        from ui.ui_components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        return ui.open_merchant_id if ui else -1

    @open_merchant_id.setter
    def open_merchant_id(self, value: int) -> None:
        from ui.ui_components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        if ui:
            ui.open_merchant_id = value

    @property
    def is_open(self) -> bool:
        return self.open_merchant_id != -1

    @property
    def qty_modal_open(self) -> bool:
        return self._qty_modal is not None

    def open_for(self, merchant_eid: int) -> None:
        """Abre a loja para o merchant_eid especificado, resetando estado interno."""
        self.open_merchant_id   = merchant_eid
        self._pending_merchant_id = -1
        self._shop_scroll       = 0
        self._bag_scroll        = 0
        self._open_cooldown     = 0.3

    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(self.PANEL_W, self.PANEL_H)
        return x0 + UI.SHOP_OFFSET_X, y0 + UI.SHOP_OFFSET_Y

    def _get_cam(self):
        SW = self.world_surf.get_width()
        SH = self.world_surf.get_height()
        for eid, pos, _ in self.world.get_entities_with(Position, Camera):
            return pos.x - SW / 2, pos.y - SH / 2
        return 0.0, 0.0

    # ------------------------------------------------------------------
    # Update — detecção de clique no mundo
    # ------------------------------------------------------------------

    def update(self, events=None, dt: float = 0) -> None:
        self._right_click_consumed = False
        if self._open_cooldown > 0:
            self._open_cooldown = max(0.0, self._open_cooldown - dt)

        # --- Verifica se o jogador chegou perto do merchant pendente ---
        if self._pending_merchant_id != -1:
            mt = self._merchant_tile(self._pending_merchant_id)
            pt = self._player_tile()
            if mt is None:
                # Merchant não existe mais
                self._pending_merchant_id = -1
            elif pt and self._cheby(pt, mt) <= 1:
                # Chegou — para o auto-move e abre a loja
                auto = self.world.get_component(self.player_entity, PlayerAutoMove)
                if auto:
                    auto.active        = False
                    auto.path          = []
                    auto.ground_target = None
                self.open_merchant_id     = self._pending_merchant_id
                self._pending_merchant_id = -1
                self._shop_scroll = 0
                self._bag_scroll  = 0
                self._open_cooldown = 0.5
                _m = self.world.get_component(self.open_merchant_id, Merchant)
                if _m:
                    _npc = self.world.get_component(self.open_merchant_id, NPC)
                    quest_fire("talk_to_npc", npc_name=_npc.name if _npc else "Comerciante")

        if not events:
            return

        cam_x, cam_y = self._get_cam()

        for event in events:
            if event.type == pygame.MOUSEWHEEL and self.is_open and self._qty_modal is None:
                x0, y0  = self._panel_origin()
                mx, _my = pygame.mouse.get_pos()
                mid_x   = x0 + self.GAP + self.LEFT_W
                if mx < mid_x:
                    self._shop_scroll = max(0, self._shop_scroll - event.y)
                else:
                    self._bag_scroll  = max(0, self._bag_scroll  - event.y)

            elif (event.type == pygame.MOUSEBUTTONDOWN
                  and event.button == 3
                  and not self.is_open):
                mx, my = event.pos
                _sc = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                wx, wy = mx * _sc + cam_x, my * _sc + cam_y
                for eid, pos, rend, _ in self.world.get_entities_with(
                        Position, Renderable, Merchant):
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        mt = self._merchant_tile(eid)
                        if pt and mt and self._cheby(pt, mt) <= 1:
                            # Já adjacente — abre direto
                            self.open_merchant_id     = eid
                            self._pending_merchant_id = -1
                            self._shop_scroll = 0
                            self._bag_scroll  = 0
                            self._open_cooldown = 0.5
                            _m2 = self.world.get_component(eid, Merchant)
                            if _m2:
                                _npc2 = self.world.get_component(eid, NPC)
                                quest_fire("talk_to_npc", npc_name=_npc2.name if _npc2 else "Comerciante")
                        else:
                            # Inicia caminhada até tile adjacente
                            self._pending_merchant_id = eid
                            self._walk_to_merchant(eid)
                        self._right_click_consumed = True
                        break

    # ------------------------------------------------------------------
    # Transações
    # ------------------------------------------------------------------

    def _sell_price(self, item) -> int:
        return max(1, int(item.value * self.SELL_RATIO))

    def _buy(self, entry: dict, shop_id: str = "") -> None:
        """Compra 1 unidade. Online: envia BUY_REQUEST ao servidor (autoritativo).
        Offline: aplica localmente como antes."""
        if self._net and shop_id:
            # Online: servidor valida e responde com BUY_RESULT
            from shared.messages import MsgType as _MTShop
            _wallet_buy = self.world.get_component(self.player_entity, Wallet)
            preview = entry["factory"]()
            self._net.send(_MTShop.BUY_REQUEST, {
                "shop_id":     shop_id,
                "item_name":   preview.name,
                "quantity":    1,
                "current_gold": _wallet_buy.gold if _wallet_buy else 0,
            })
            return  # UI atualizada quando BUY_RESULT chegar

        # Offline: lógica local (sem rede)
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet:
            return
        price = entry["price"]
        if wallet.gold < price:
            return

        preview = entry["factory"]()
        if getattr(preview, "max_stack", 1) > 1:
            for existing in inv.items:
                if existing is None:
                    continue
                if existing.name == preview.name and existing.stack < existing.max_stack:
                    wallet.gold -= price
                    existing.stack += 1
                    self.transaction_history.append({"type": "buy", "item": existing, "price": price})
                    if len(self.transaction_history) > self.MAX_HISTORY:
                        self.transaction_history.pop(0)
                    return

        if len(inv.items) >= inv.max_slots:
            LOG.add("Inventario cheio!", (255, 160, 0))
            return
        item = entry["factory"]()
        wallet.gold -= price
        inv.items.append(item)
        self.transaction_history.append({"type": "buy", "item": item, "price": price})
        if len(self.transaction_history) > self.MAX_HISTORY:
            self.transaction_history.pop(0)

    def _buy_qty(self, entry: dict, qty: int, shop_id: str = "") -> None:
        """Compra qty unidades de um item stackável. Online: envia BUY_REQUEST."""
        if self._net and shop_id:
            from shared.messages import MsgType as _MTShop
            _wallet_qty = self.world.get_component(self.player_entity, Wallet)
            preview = entry["factory"]()
            self._net.send(_MTShop.BUY_REQUEST, {
                "shop_id":     shop_id,
                "item_name":   preview.name,
                "quantity":    qty,
                "current_gold": _wallet_qty.gold if _wallet_qty else 0,
            })
            return

        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet or qty <= 0:
            return
        price     = entry["price"]
        total_cost = price * qty
        if wallet.gold < total_cost:
            qty        = wallet.gold // price
            total_cost = price * qty
        if qty <= 0:
            return

        preview   = entry["factory"]()
        remaining = qty

        # Preenche stacks existentes primeiro
        if getattr(preview, "max_stack", 1) > 1:
            for existing in inv.items:
                if existing is None or remaining <= 0:
                    continue
                if existing.name == preview.name and existing.stack < existing.max_stack:
                    can_add = min(remaining, existing.max_stack - existing.stack)
                    existing.stack += can_add
                    remaining      -= can_add

        # Cria novos slots para o restante
        while remaining > 0:
            if len(inv.items) >= inv.max_slots:
                break
            new_item       = entry["factory"]()
            take           = min(remaining, new_item.max_stack)
            new_item.stack = take
            inv.items.append(new_item)
            remaining -= take

        actually_bought = qty - remaining
        wallet.gold    -= price * actually_bought
        if actually_bought > 0:
            _item_ref = next((it for it in inv.items
                              if it is not None and it.name == preview.name), preview)
            self.transaction_history.append({
                "type": "buy", "item": _item_ref,
                "price": price * actually_bought,
            })
            if len(self.transaction_history) > self.MAX_HISTORY:
                self.transaction_history.pop(0)

    def _open_qty_modal(self, entry: dict, shop_id: str = "") -> None:
        """Abre o modal de seleção de quantidade para um item stackável."""
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet:
            return
        price   = entry["price"]
        preview = entry["factory"]()
        # Máximo limitado por ouro e por espaço de stack disponível
        max_by_gold  = wallet.gold // max(1, price)
        existing_cap = sum(
            (it.max_stack - it.stack)
            for it in inv.items
            if it is not None and it.name == preview.name and it.stack < it.max_stack
        )
        free_slots  = inv.max_slots - len(inv.items)
        max_by_inv  = existing_cap + free_slots * preview.max_stack
        if max_by_gold == 0:
            LOG.add("Ouro insuficiente!", (220, 80, 80))
            return
        if max_by_inv == 0:
            LOG.add("Inventario cheio!", (255, 160, 0))
            return
        max_qty     = max(1, min(max_by_gold, max_by_inv, preview.max_stack * 10))
        self._qty_modal = {
            "entry":    entry,
            "shop_id":  shop_id,
            "preview":  preview,
            "max_qty":  max_qty,
            "qty":      1,
            "text":     "1",
            "dragging": False,
        }

    def _close_qty_modal(self) -> None:
        self._qty_modal = None

    def _sell(self, item_idx: int) -> None:
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet or item_idx >= len(inv.items):
            return
        item     = inv.items[item_idx]
        sell_val = self._sell_price(item)

        if self._net:
            # Online: remove item imediatamente (feedback visual), mas NÃO altera gold.
            # Gold é atualizado quando SELL_RESULT chegar (servidor é autoritativo).
            # Padrão igual ao buy online que também não altera gold antes da confirmação.
            from shared.messages import MsgType as _MTS
            self._net.send(_MTS.SELL_REQUEST, {
                "item_name":    item.name,
                "item_value":   getattr(item, "value", 0),
                "stack_sold":   1,
                "current_gold": wallet.gold,
            })
            item.stack -= 1
            if item.stack <= 0:
                inv.items.pop(item_idx)
            if self._bag_scroll > 0 and self._bag_scroll >= len(inv.items):
                self._bag_scroll = max(0, len(inv.items) - 1)
            return  # gold atualizado via SELL_RESULT

        # Offline: aplica tudo localmente
        wallet.gold += sell_val
        item.stack -= 1
        if item.stack <= 0:
            inv.items.pop(item_idx)
        self.transaction_history.append({"type": "sell", "item": item, "sell_value": sell_val})
        if len(self.transaction_history) > self.MAX_HISTORY:
            self.transaction_history.pop(0)
        # Ajusta scroll se necessário
        inv_len = len(inv.items)
        if self._bag_scroll > 0 and self._bag_scroll >= inv_len:
            self._bag_scroll = max(0, inv_len - 1)

    def _undo(self) -> None:
        if not self.transaction_history:
            return
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet:
            return
        tx = self.transaction_history.pop()
        if tx["type"] == "buy":
            for i, it in enumerate(inv.items):
                if it is tx["item"]:
                    inv.items.pop(i)
                    wallet.gold += tx["price"]
                    break
        elif tx["type"] == "sell":
            if len(inv.items) < inv.max_slots:
                inv.items.append(tx["item"])
                wallet.gold = max(0, wallet.gold - tx["sell_value"])

    def _close(self) -> None:
        self.open_merchant_id     = -1
        self._pending_merchant_id = -1
        self._shop_scroll         = 0
        self._bag_scroll          = 0

    # ------------------------------------------------------------------
    # Helpers de posição/tile
    # ------------------------------------------------------------------

    def _player_tile(self):
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _merchant_tile(self, eid: int):
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TILE_SIZE), int(pos.y / TILE_SIZE)) if pos else None

    @staticmethod
    def _cheby(t1, t2) -> int:
        return chebyshev(t1[0], t1[1], t2[0], t2[1])

    def _walk_to_merchant(self, merchant_eid: int) -> None:
        """Define ground_target do PlayerAutoMove para o tile adjacente mais próximo."""
        pt  = self._player_tile()
        mt  = self._merchant_tile(merchant_eid)
        if not pt or not mt:
            return
        adj = [(mt[0]+dx, mt[1]+dy) for dx, dy in ((-1,0),(1,0),(0,-1),(0,1))]
        target = min(adj, key=lambda t: abs(t[0]-pt[0]) + abs(t[1]-pt[1]))
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        if auto:
            auto.ground_target     = target
            auto.path              = []
            auto.active            = True
            auto.path_recalc_timer = 0.0

    # ------------------------------------------------------------------
    # Eventos de UI
    # ------------------------------------------------------------------

    def handle_events(self, events: list) -> None:
        if not self.is_open:
            return
        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return

        stock  = SHOPS.get(merch.shop_id, {}).get("stock", [])
        inv    = self.world.get_component(self.player_entity, Inventory)
        x0, y0 = self._panel_origin()
        gap     = self._u(self.GAP)
        left_w  = self._u(self.LEFT_W)
        right_w = self._u(self.RIGHT_W)
        row_h   = self._u(self.ROW_H)
        mid_x   = x0 + gap + left_w
        body_y  = y0 + self._u(self.BODY_Y_OFFSET)

        for event in events:
            # ── Modal de quantidade aberto → processa antes de tudo ────────
            if self._qty_modal is not None:
                self._handle_qty_modal_event(event)
                continue  # consome o evento; não deixa cair na lógica da loja

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return

            if event.type == pygame.MOUSEWHEEL:
                if mx < mid_x:
                    self._shop_scroll = max(0, self._shop_scroll - event.y)
                else:
                    self._bag_scroll  = max(0, self._bag_scroll  - event.y)
                continue

            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            mx, my = event.pos

            # Botão fechar
            close_r = pygame.Rect(x0 + self._u(self.PANEL_W) - self._u(40), y0 + self._u(6), self._u(34), self._u(34))
            if event.button == 1 and close_r.collidepoint(mx, my):
                self._close()
                return

            # Botão desfazer (desabilitado online — servidor já processou a transação)
            undo_r = pygame.Rect(x0 + gap, y0 + self._u(54), self._u(145), self._u(32))
            if event.button == 1 and undo_r.collidepoint(mx, my) and not self._net:
                self._undo()
                return

            mods = pygame.key.get_mods()
            shift = bool(mods & pygame.KMOD_SHIFT)

            # Painel esquerdo: comprar
            if event.button == 3 and mx < mid_x and self._open_cooldown <= 0:
                for i, entry in enumerate(stock):
                    vis_i = i - self._shop_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(x0 + gap,
                                        body_y + vis_i * row_h,
                                        left_w - self._u(4), row_h - self._u(2))
                        if r.collidepoint(mx, my):
                            preview = entry["factory"]()
                            if shift and getattr(preview, "max_stack", 1) > 1:
                                self._open_qty_modal(entry, shop_id=merch.shop_id)
                            else:
                                self._buy(entry, shop_id=merch.shop_id)
                            return

            # Painel direito: vender (clique direito)
            if event.button == 3 and mx >= mid_x and inv and self._open_cooldown <= 0:
                for i, item in enumerate(inv.items):
                    vis_i = i - self._bag_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(mid_x + gap,
                                        body_y + vis_i * row_h,
                                        right_w - self._u(4), row_h - self._u(2))
                        if r.collidepoint(mx, my):
                            self._sell(i)
                            return

    def _handle_qty_modal_event(self, event) -> None:
        """Processa eventos enquanto o modal de quantidade está aberto."""
        m = self._qty_modal

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._close_qty_modal()
            elif event.key == pygame.K_RETURN or event.key == pygame.K_KP_ENTER:
                self._buy_qty(m["entry"], m["qty"], shop_id=m.get("shop_id", ""))
                self._close_qty_modal()
            elif event.key == pygame.K_BACKSPACE:
                m["text"] = m["text"][:-1] or "0"
                try:
                    m["qty"] = max(1, min(int(m["text"]), m["max_qty"]))
                except ValueError:
                    m["qty"] = 1
            elif event.unicode.isdigit():
                new_text = (m["text"] if m["text"] != "0" else "") + event.unicode
                if len(new_text) <= 6:
                    m["text"] = new_text
                    try:
                        m["qty"] = max(1, min(int(new_text), m["max_qty"]))
                    except ValueError:
                        pass
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
            SW, SH  = self.hud_surf.get_size()
            mw, mh  = self._u(UI.SHOP_QTY_MODAL_W), self._u(UI.SHOP_QTY_MODAL_H)
            mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
            my0     = (SH - mh) // 2 + UI.SHOP_QTY_MODAL_OFFSET_Y

            # Slider
            sl_x  = mx0 + self._u(20)
            sl_y  = my0 + self._u(130)
            sl_w  = mw - self._u(40)
            sl_r  = pygame.Rect(sl_x, sl_y - self._u(10), sl_w, self._u(20))
            if sl_r.collidepoint(mx, my):
                ratio      = max(0.0, min(1.0, (mx - sl_x) / sl_w))
                m["qty"]   = max(1, round(ratio * m["max_qty"]))
                m["text"]  = str(m["qty"])
                m["dragging"] = True
                return

            # Botão Cancelar
            btn_cancel = pygame.Rect(mx0 + self._u(20),      my0 + mh - self._u(54), self._u(190), self._u(38))
            btn_ok     = pygame.Rect(mx0 + mw - self._u(210), my0 + mh - self._u(54), self._u(190), self._u(38))
            if btn_cancel.collidepoint(mx, my):
                self._close_qty_modal()
                return
            if btn_ok.collidepoint(mx, my):
                self._buy_qty(m["entry"], m["qty"], shop_id=m.get("shop_id", ""))
                self._close_qty_modal()
                return

            # Clique fora fecha
            modal_r = pygame.Rect(mx0, my0, mw, mh)
            if not modal_r.collidepoint(mx, my):
                self._close_qty_modal()

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            m["dragging"] = False

        elif event.type == pygame.MOUSEMOTION and m.get("dragging"):
            self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
            SW, SH  = self.hud_surf.get_size()
            mw      = self._u(UI.SHOP_QTY_MODAL_W)
            mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
            sl_x, sl_w = mx0 + self._u(20), mw - self._u(40)
            mx_now  = event.pos[0]
            ratio   = max(0.0, min(1.0, (mx_now - sl_x) / sl_w))
            m["qty"]  = max(1, round(ratio * m["max_qty"]))
            m["text"] = str(m["qty"])

    # ------------------------------------------------------------------
    # Render — NPC no mundo
    # ------------------------------------------------------------------

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        pass  # indicador "LOJA" removido — NPC segue o padrão de tooltip

    # ------------------------------------------------------------------
    # Render — painel de loja
    # ------------------------------------------------------------------

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        self.pending_tooltip = None
        if not self.is_open:
            return

        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return

        shop  = SHOPS.get(merch.shop_id, {})
        stock = shop.get("stock", [])
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        bag    = inv.items if inv else []
        _char_shop  = self.world.get_component(self.player_entity, CharacterStats)
        _viewer_cls = _char_shop.class_id if _char_shop else None

        SW, SH  = self.hud_surf.get_size()
        x0, y0  = self._panel_origin()
        W, H    = self._u(self.PANEL_W), self._u(self.PANEL_H)
        gap     = self._u(self.GAP)
        left_w  = self._u(self.LEFT_W)
        right_w = self._u(self.RIGHT_W)
        row_h   = self._u(self.ROW_H)
        icon_s  = self._u(self.ICON_S)
        mid_x   = x0 + gap + left_w
        mx, my  = pygame.mouse.get_pos()
        _modal_open = self._qty_modal is not None

        # Overlay escuro
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 160)), (0, 0))

        # Fundo do painel
        self.hud_surf.blit(fill_surf((W, H), (15, 10, 5, 235)), (x0, y0))
        pygame.draw.rect(self.hud_surf, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        # --- Header ---
        title = self._font_lg.render(f"  {shop.get('name', 'Comerciante')}", False, (255, 220, 120))
        self.hud_surf.blit(title, (x0 + self._u(8), y0 + self._u(8)))

        close_r   = pygame.Rect(x0 + W - self._u(40), y0 + self._u(6), self._u(34), self._u(34))
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (180, 60, 60) if close_hov else (100, 35, 35), close_r, border_radius=3)
        xs = self._font_md.render("X", False, (255, 255, 255))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        LINE1 = y0 + self._u(50)   # linha após o título
        UNDO_Y = y0 + self._u(54)  # barra de desfazer
        LINE2 = y0 + self._u(92)   # linha após desfazer
        COL_Y = y0 + self._u(96)   # cabeçalhos das colunas
        body_y = y0 + self._u(self.BODY_Y_OFFSET)

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), LINE1), (x0 + W - self._u(4), LINE1))

        # --- Barra de desfazer ---
        undo_r   = pygame.Rect(x0 + gap, UNDO_Y, self._u(145), self._u(32))
        has_hist = bool(self.transaction_history)
        undo_hov = undo_r.collidepoint(mx, my) and has_hist
        undo_bg  = (55, 80, 55) if undo_hov else ((38, 55, 38) if has_hist else (28, 28, 28))
        undo_col = (150, 220, 150) if has_hist else (70, 70, 70)
        pygame.draw.rect(self.hud_surf, undo_bg,  undo_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, undo_col, undo_r, 1, border_radius=3)
        self.hud_surf.blit(self._font_sm.render("↩ Desfazer", False, undo_col),
                         (undo_r.x + self._u(8), undo_r.y + self._u(7)))

        if self.transaction_history:
            tx  = self.transaction_history[-1]
            if tx["type"] == "buy":
                desc = f"Ultima: comprou {tx['item'].name} por {tx['price']}g"
            else:
                desc = f"Ultima: vendeu {tx['item'].name} por {tx['sell_value']}g"
            self.hud_surf.blit(self._font_sm.render(desc, False, (150, 150, 150)),
                             (x0 + gap + self._u(155), UNDO_Y + self._u(7)))

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), LINE2), (x0 + W - self._u(4), LINE2))

        # Divisor vertical
        pygame.draw.line(self.hud_surf, (90, 70, 40), (mid_x, LINE1), (mid_x, y0 + H - self._u(36)))

        # --- Cabeçalhos das colunas ---
        hdr_col = (160, 130, 80)
        hint    = (90, 80, 60)
        self.hud_surf.blit(self._font_md.render(f"LOJA  ({len(stock)} itens)", False, hdr_col),
                         (x0 + gap + self._u(4), COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ comprar  |  Shift+dir. = qtd.", False, hint),
                         (x0 + gap + self._u(4), COL_Y + self._u(26)))
        self.hud_surf.blit(self._font_md.render(f"MOCHILA  ({len(bag)}/{inv.max_slots if inv else 0})", False, hdr_col),
                         (mid_x + gap + self._u(4), COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ vender", False, hint),
                         (mid_x + gap + self._u(4), COL_Y + self._u(26)))

        pygame.draw.line(self.hud_surf, (70, 55, 30), (x0 + self._u(4), body_y - self._u(2)), (x0 + W - self._u(4), body_y - self._u(2)))

        # --- Painel esquerdo: itens da loja ---
        max_shop = max(0, len(stock) - self.MAX_ROWS)
        self._shop_scroll = min(self._shop_scroll, max_shop)

        for i, entry in enumerate(stock):
            vis_i = i - self._shop_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * row_h
            r     = pygame.Rect(x0 + gap, row_y, left_w - self._u(4), row_h - self._u(2))

            # Preview do item (instância temporária apenas para display)
            preview    = entry["factory"]()
            can_afford = wallet and wallet.gold >= entry["price"]
            inv_full   = inv and len(inv.items) >= inv.max_slots

            hov  = r.collidepoint(mx, my) and not _modal_open
            if not can_afford or inv_full:
                bg_c   = (40, 18, 18) if hov else (22, 10, 10)
                bord_c = (110, 45, 45) if hov else (48, 22, 22)
            else:
                bg_c   = (50, 40, 20) if hov else (28, 20, 10)
                bord_c = (180, 140, 60) if hov else (60, 45, 25)

            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(preview.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + self._u(4), r.y + (row_h - self._u(2) - icon_s) // 2,
                                  icon_s, icon_s)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(preview), icon_s)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - self._u(4), ic_r.bottom - self._u(4)), self._u(3))

            name_col = rar_col if (can_afford and not inv_full) else (90, 70, 70)
            self.hud_surf.blit(self._font_sm.render(preview.name, False, name_col),
                             (ic_r.right + self._u(6), r.y + self._u(6)))
            self.hud_surf.blit(self._font_sm.render(preview.item_type, False, (95, 85, 65)),
                             (ic_r.right + self._u(6), r.y + self._u(24)))

            price_col = (255, 215, 0) if (can_afford and not inv_full) else (130, 70, 70)
            ps = self._font_sm.render(f"{entry['price']}g", False, price_col)
            self.hud_surf.blit(ps, (r.right - ps.get_width() - self._u(8), r.y + self._u(14)))

            if hov:
                lines = item_tooltip_lines(preview, _viewer_cls)
                if not can_afford:
                    lines.append(("Ouro insuficiente!", (220, 80, 80)))
                elif inv_full:
                    lines.append(("Mochila cheia!", (220, 150, 50)))
                lines.append((f"Preco: {entry['price']}g | Venda estimada: {self._sell_price(preview)}g",
                              (120, 120, 120)))
                # 7-tuple: title_color + Shift+hover comparison with equipped item
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(preview.slot) if equip_c and preview.slot else None
                self.pending_tooltip = (mx, my, preview.name, lines, rar_col, preview, eq_item)

        # Scrollbar loja
        if len(stock) > self.MAX_ROWS:
            sb_h    = self.MAX_ROWS * row_h
            sb_x    = x0 + gap + left_w - self._u(8)
            th      = max(20, sb_h * self.MAX_ROWS // len(stock))
            ty      = body_y + (sb_h - th) * self._shop_scroll // max(1, max_shop)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, self._u(5), th), border_radius=2)

        # --- Painel direito: mochila ---
        max_bag = max(0, len(bag) - self.MAX_ROWS)
        self._bag_scroll = min(self._bag_scroll, max_bag)

        for i, item in enumerate(bag):
            vis_i = i - self._bag_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * row_h
            r     = pygame.Rect(mid_x + gap, row_y, right_w - self._u(4), row_h - self._u(2))

            hov    = r.collidepoint(mx, my) and not _modal_open
            bg_c   = (50, 40, 20) if hov else (28, 20, 10)
            bord_c = (180, 140, 60) if hov else (60, 45, 25)
            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + self._u(4), r.y + (row_h - self._u(2) - icon_s) // 2,
                                  icon_s, icon_s)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(item), icon_s)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - self._u(4), ic_r.bottom - self._u(4)), self._u(3))
            from ui.ui_helpers import draw_stack_count as _dsc
            _dsc(self.hud_surf, item, ic_r, self._font_sm)

            stack = getattr(item, "stack", 1)
            name_label = f"{item.name}" if stack <= 1 else f"{item.name} x{stack}"
            self.hud_surf.blit(self._font_sm.render(name_label, False, rar_col),
                             (ic_r.right + self._u(6), r.y + self._u(6)))
            slot_label = item.slot if item.slot else item.item_type
            self.hud_surf.blit(self._font_sm.render(slot_label, False, (95, 85, 65)),
                             (ic_r.right + self._u(6), r.y + self._u(24)))

            sp     = self._sell_price(item)
            sp_s   = self._font_sm.render(f"+{sp}g", False, (120, 200, 100))
            self.hud_surf.blit(sp_s, (r.right - sp_s.get_width() - self._u(8), r.y + self._u(14)))

            if hov:
                lines = item_tooltip_lines(item, _viewer_cls)
                if stack > 1:
                    lines.append((f"Quantidade: {stack}", (180, 180, 180)))
                lines.append((f"Venda: {sp}g | Valor base: {item.value}g", (120, 120, 120)))
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(item.slot) if equip_c and item.slot else None
                self.pending_tooltip = (mx, my, item.name, lines, rar_col, item, eq_item)

        # Scrollbar mochila
        if len(bag) > self.MAX_ROWS:
            sb_h = self.MAX_ROWS * row_h
            sb_x = mid_x + gap + right_w - self._u(8)
            th   = max(20, sb_h * self.MAX_ROWS // len(bag))
            ty   = body_y + (sb_h - th) * self._bag_scroll // max(1, max_bag)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, self._u(5), th), border_radius=2)

        # --- Footer: ouro do jogador ---
        foot_y = y0 + H - self._u(self.FOOTER_H)
        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), foot_y), (x0 + W - self._u(4), foot_y))
        if wallet:
            gold_s = self._font_md.render(f"Seu ouro: {wallet.gold}g", False, (255, 215, 0))
            self.hud_surf.blit(gold_s, (x0 + W // 2 - gold_s.get_width() // 2, foot_y + self._u(10)))

        # --- Modal de quantidade ---
        if self._qty_modal is not None:
            self.pending_tooltip = None  # modal suprime tooltip dos itens abaixo
            self._render_qty_modal(wallet)

    def _render_qty_modal(self, wallet) -> None:
        """Renderiza o modal de seleção de quantidade."""
        m       = self._qty_modal
        self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
        SW, SH  = self.hud_surf.get_size()
        mw, mh  = self._u(UI.SHOP_QTY_MODAL_W), self._u(UI.SHOP_QTY_MODAL_H)
        mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
        my0     = (SH - mh) // 2 + UI.SHOP_QTY_MODAL_OFFSET_Y

        # Overlay semitransparente
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 130)), (0, 0))

        # Fundo do modal
        self.hud_surf.blit(fill_surf((mw, mh), (18, 14, 8, 245)), (mx0, my0))
        pygame.draw.rect(self.hud_surf, (180, 140, 70), (mx0, my0, mw, mh), 2, border_radius=6)

        # Título
        title_s = self._font_md.render(m["preview"].name, False, (255, 220, 100))
        self.hud_surf.blit(title_s, (mx0 + mw // 2 - title_s.get_width() // 2, my0 + self._u(12)))

        # Preço
        price    = m["entry"]["price"]
        total    = price * m["qty"]
        gold_avail = wallet.gold if wallet else 0
        price_col  = (255, 215, 0) if total <= gold_avail else (220, 80, 80)
        price_s  = self._font_sm.render(
            f"{price}g por unidade  |  Total: {total}g  (ouro: {gold_avail}g)", False, price_col)
        self.hud_surf.blit(price_s, (mx0 + mw // 2 - price_s.get_width() // 2, my0 + self._u(42)))

        # ── Slider ────────────────────────────────────────────────────────
        sl_x  = mx0 + self._u(20)
        sl_y  = my0 + self._u(130)
        sl_w  = mw - self._u(40)
        ratio = (m["qty"] - 1) / max(1, m["max_qty"] - 1) if m["max_qty"] > 1 else 0.0
        handle_x = sl_x + int(ratio * sl_w)

        pygame.draw.rect(self.hud_surf, (50, 40, 25), (sl_x, sl_y - self._u(3), sl_w, self._u(6)), border_radius=3)
        pygame.draw.rect(self.hud_surf, (160, 120, 50), (sl_x, sl_y - self._u(3), int(ratio * sl_w), self._u(6)), border_radius=3)
        pygame.draw.circle(self.hud_surf, (220, 180, 80), (handle_x, sl_y), self._u(10))
        pygame.draw.circle(self.hud_surf, (255, 220, 120), (handle_x, sl_y), self._u(10), 2)

        # Labels min/max do slider
        self.hud_surf.blit(self._font_sm.render("1", False, (130, 110, 70)),
                           (sl_x, sl_y + self._u(14)))
        max_s = self._font_sm.render(str(m["max_qty"]), False, (130, 110, 70))
        self.hud_surf.blit(max_s, (sl_x + sl_w - max_s.get_width(), sl_y + self._u(14)))

        # ── Campo de texto ────────────────────────────────────────────────
        qty_s = self._font_lg.render(str(m["qty"]), False, (255, 255, 255))
        txt_x = mx0 + mw // 2 - qty_s.get_width() // 2
        self.hud_surf.blit(qty_s, (txt_x, my0 + self._u(76)))
        # Cursor piscante
        if (pygame.time.get_ticks() // 500) % 2 == 0:
            cx = txt_x + qty_s.get_width() + 2
            pygame.draw.line(self.hud_surf, (200, 200, 200),
                             (cx, my0 + self._u(78)), (cx, my0 + self._u(78) + qty_s.get_height() - 4), 2)

        # ── Botões ────────────────────────────────────────────────────────
        btn_cancel = pygame.Rect(mx0 + self._u(20),       my0 + mh - self._u(54), self._u(190), self._u(38))
        btn_ok     = pygame.Rect(mx0 + mw - self._u(210), my0 + mh - self._u(54), self._u(190), self._u(38))
        mmx, mmy   = pygame.mouse.get_pos()

        for btn, label, ok in ((btn_cancel, "Cancelar", False), (btn_ok, f"Comprar {m['qty']}", True)):
            can_buy  = ok and total <= gold_avail
            hov      = btn.collidepoint(mmx, mmy)
            if ok:
                col_bg   = (40, 100, 40) if (can_buy and hov) else ((30, 75, 30) if can_buy else (50, 25, 25))
                col_brd  = (100, 220, 100) if can_buy else (120, 60, 60)
                col_txt  = (150, 255, 150) if can_buy else (180, 100, 100)
            else:
                col_bg  = (70, 40, 30) if hov else (50, 28, 20)
                col_brd = (180, 100, 60)
                col_txt = (220, 160, 100)
            pygame.draw.rect(self.hud_surf, col_bg,  btn, border_radius=4)
            pygame.draw.rect(self.hud_surf, col_brd, btn, 1, border_radius=4)
            lbl_s = self._font_sm.render(label, False, col_txt)
            self.hud_surf.blit(lbl_s, (btn.centerx - lbl_s.get_width() // 2,
                                        btn.centery - lbl_s.get_height() // 2))

        # Dica ESC
        esc_s = self._font_sm.render("ESC cancela  |  ENTER confirma", False, (80, 70, 50))
        self.hud_surf.blit(esc_s, (mx0 + mw // 2 - esc_s.get_width() // 2, my0 + mh - self._u(14)))


class ConsumableSystem(System):
    """Processa a barra de consumíveis (keybinds + uso) e ActiveRegen (HoT).

    Online: injete `system._net = self._net` após criação para que o
    uso de consumíveis seja comunicado ao servidor via CONSUMABLE_USE.
    O servidor é autoritativo — cura/mana/HoT e a remoção do item da bag
    só se aplicam no cliente APÓS a confirmação (STATS_UPDATE com
    `item_name`+`consumable_ok`, ver client/network_handlers.py). Sem
    isso, um consumível bloqueado no servidor (ex: HP já cheio lá, mesmo
    que o cliente ache que não está) era perdido em silêncio: cliente já
    tinha curado localmente e consumido o item antes de saber que o
    servidor não fez nada (bug real reportado por testers — ver
    PROBLEMAS_ARQUITETURA.md). Offline: aplica tudo localmente, sem espera.
    """

    def __init__(self, world: World):
        self.world = world
        self._net  = None   # injetado pelo GameEngine no modo online
        # Item aguardando confirmação do servidor (online) — resolvido por
        # network_handlers.py._handle_msg_stats_update ao chegar a resposta.
        self.pending_item_name: str = ""

    def update(self, events=None, dt: float = 0) -> None:
        # ── Barra de consumíveis: cooldown + keybinds ─────────────────────
        for eid, cbar in self.world.get_entities_with(ConsumableBar):
            if cbar.global_cooldown > 0:
                cbar.global_cooldown = max(0.0, cbar.global_cooldown - dt)

            if events:
                for ev in events:
                    if ev.type != pygame.KEYDOWN:
                        continue
                    for slot_i, kb in enumerate(cbar.keybinds):
                        if ev.key == kb:
                            item_name = cbar.slots[slot_i]
                            if item_name and cbar.global_cooldown <= 0:
                                self._use_consumable(eid, item_name, cbar)
                            break

        # ── ActiveRegen: ticks de regeneração de HP ───────────────────────
        to_remove = []
        for eid, regen in self.world.get_entities_with(ActiveRegen):
            cs     = self.world.get_component(eid, CombatStats)
            pos_c  = self.world.get_component(eid, Position)
            if not cs:
                to_remove.append(eid)
                continue

            regen.tick_timer -= dt
            if regen.tick_timer <= 0:
                regen.tick_timer += regen.interval
                regen.ticks_remaining -= 1
                # Offline: aplica HP localmente e exibe FLT.
                # Online: servidor aplica e envia STATS_UPDATE autoritativo.
                # Nao alterar HP local no online — evita desync e "correcao" visual ao tomar dano.
                if not self._net:
                    healed = min(regen.heal_per_tick, cs.max_hp - cs.current_hp)
                    cs.current_hp = min(cs.max_hp, cs.current_hp + regen.heal_per_tick)
                    if pos_c and healed > 0:
                        FLT.add(f"+{healed}", pos_c.x, pos_c.y - 16,
                                (80, 220, 120), "small", eid)
                if regen.ticks_remaining <= 0:
                    to_remove.append(eid)

        for eid in to_remove:
            self.world.remove_component(eid, ActiveRegen)

        # ── ActiveManaRegen: ticks de regeneração de mana (odres) ─────────
        from engine.components import ActiveManaRegen as _AMR, CharacterStats as _CHSr
        _mana_remove = []
        for eid, mregen in self.world.get_entities_with(_AMR):
            char_r = self.world.get_component(eid, _CHSr)
            pos_r  = self.world.get_component(eid, Position)
            if not char_r or char_r.max_mana <= 0:
                _mana_remove.append(eid)
                continue

            mregen.tick_timer -= dt
            if mregen.tick_timer <= 0:
                mregen.tick_timer += mregen.interval
                mregen.ticks_remaining -= 1
                if not self._net:
                    restored = min(mregen.mana_per_tick, char_r.max_mana - char_r.mana)
                    char_r.mana = min(char_r.max_mana, char_r.mana + mregen.mana_per_tick)
                    if pos_r and restored > 0:
                        FLT.add(f"+{restored} MP", pos_r.x, pos_r.y - 16,
                                (100, 180, 255), "small", eid)
                if mregen.ticks_remaining <= 0:
                    _mana_remove.append(eid)

        for eid in _mana_remove:
            self.world.remove_component(eid, _AMR)

    def _use_consumable(self, entity_id: int, item_name: str, cbar) -> None:
        inv   = self.world.get_component(entity_id, Inventory)
        cs    = self.world.get_component(entity_id, CombatStats)
        pos_c = self.world.get_component(entity_id, Position)
        if not inv or not cs:
            return

        item = next((it for it in inv.items
                     if it.name == item_name and it.consumable), None)
        if not item:
            return

        cons   = item.consumable
        cstate = self.world.get_component(entity_id, CombatState)

        # Consumíveis ooc_only (comida, ensopados) não podem ser usados em combate
        if cons.get("ooc_only", False) and cstate and cstate.in_combat:
            WARN.add("Não pode usar em combate")
            return

        # Determina que recursos o consumível restaura
        _has_hp   = bool(cons.get("heal_instant", 0) or cons.get("heal_per_tick", 0))
        _has_mana = bool(cons.get("mana_restore", 0) or cons.get("mana_per_tick", 0))

        # Bloqueia se o recurso relevante já está cheio (checagem LOCAL —
        # feedback rápido; o servidor, online, faz a checagem real e pode
        # rejeitar mesmo que passe aqui, ver abaixo)
        from engine.components import CharacterStats as _CHSu
        _char_u = self.world.get_component(entity_id, _CHSu)
        if _has_hp and not _has_mana and cs.current_hp >= cs.max_hp:
            WARN.add("HP já está cheio")
            return
        if _has_mana and not _has_hp and _char_u and _char_u.max_mana > 0:
            if _char_u.mana >= _char_u.max_mana:
                WARN.add("Mana já está cheia")
                return

        heal_per_tick = cons.get("heal_per_tick", 0)
        ticks         = cons.get("ticks", 0)
        interval      = cons.get("interval", 2.0)
        mana_per_tick = cons.get("mana_per_tick", 0)
        heal_instant  = cons.get("heal_instant", 0)
        mana_restore  = cons.get("mana_restore", 0)

        if self._net:
            # Online: SÓ manda o pedido — nada é mutado aqui (nem HP/mana,
            # nem HoT, nem o item). O servidor é quem decide se aceita, e só
            # ao confirmar (STATS_UPDATE com item_name+consumable_ok, ver
            # network_handlers.py) o item é removido e os efeitos aplicados.
            # Sem isso, um consumível bloqueado no SERVIDOR (drift natural
            # entre os dois lados — ex: HP5 regen que o cliente ainda não
            # viu) era perdido em silêncio: cliente já tinha curado local e
            # consumido o item antes de saber que nada aconteceu de verdade.
            if cbar is not None:
                cbar.global_cooldown = ConsumableBar.GCD_DURATION
            self.pending_item_name = item_name
            from shared.messages import MsgType as _MTC
            _hot = {"heal_per_tick": heal_per_tick, "interval": interval, "ticks": ticks} \
                   if heal_per_tick > 0 and ticks > 0 else None
            _mana_hot = {"mana_per_tick": mana_per_tick, "interval": interval, "ticks": ticks} \
                        if mana_per_tick > 0 and ticks > 0 else None
            self._net.send(_MTC.CONSUMABLE_USE, {
                "item_name":    item_name,
                "heal_instant": heal_instant,
                "mana_restore": mana_restore,
                "hot":          _hot,
                "mana_hot":     _mana_hot,
                "ooc_only":     cons.get("ooc_only", False),
                "buffs":        [],
            })
            return

        # Offline: aplica tudo localmente, sem espera.
        if heal_instant > 0:
            healed = min(heal_instant, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_instant)
            if pos_c and healed > 0:
                FLT.add(f"+{healed}", pos_c.x, pos_c.y - 16,
                        (80, 220, 120), "small", entity_id)

        if mana_restore > 0 and _char_u and _char_u.max_mana > 0:
            restored = min(mana_restore, _char_u.max_mana - _char_u.mana)
            _char_u.mana = min(_char_u.max_mana, _char_u.mana + mana_restore)
            if pos_c and restored > 0:
                FLT.add(f"+{restored} MP", pos_c.x, pos_c.y - 16,
                        (100, 180, 255), "small", entity_id)

        if heal_per_tick > 0 and ticks > 0:
            self.world.add_component(entity_id, ActiveRegen(
                heal_per_tick=heal_per_tick,
                interval=interval,
                ticks_total=ticks,
            ))

        from engine.components import ActiveManaRegen as _AMRu
        if mana_per_tick > 0 and ticks > 0 and _char_u and _char_u.max_mana > 0:
            try:
                self.world.remove_component(entity_id, _AMRu)
            except Exception:
                pass
            self.world.add_component(entity_id, _AMRu(
                mana_per_tick=mana_per_tick,
                interval=interval,
                ticks_total=ticks,
            ))

        item.stack -= 1
        if item.stack <= 0:
            inv.items.remove(item)

        if cbar is not None:
            cbar.global_cooldown = ConsumableBar.GCD_DURATION

        quest_fire("use_consumable", item_name=item.name)

    def _finalize_consumable(self, entity_id: int, item_name: str) -> None:
        """Chamado por network_handlers.py ao chegar consumable_ok do servidor
        — SÓ AGORA remove 1 unidade do item da bag local (online). Aplica os
        HoTs locais (ActiveRegen/ActiveManaRegen) — a cura/mana instantânea já
        chega via heal_amount/mana_amount no mesmo STATS_UPDATE, tratada em
        network_handlers.py."""
        inv = self.world.get_component(entity_id, Inventory)
        if not inv:
            return
        item = next((it for it in inv.items
                     if it.name == item_name and it.consumable), None)
        if not item:
            return
        cons = item.consumable
        heal_per_tick = cons.get("heal_per_tick", 0)
        ticks         = cons.get("ticks", 0)
        interval      = cons.get("interval", 2.0)
        mana_per_tick = cons.get("mana_per_tick", 0)
        if heal_per_tick > 0 and ticks > 0:
            self.world.add_component(entity_id, ActiveRegen(
                heal_per_tick=heal_per_tick, interval=interval, ticks_total=ticks,
            ))
        from engine.components import ActiveManaRegen as _AMRu2
        if mana_per_tick > 0 and ticks > 0:
            try:
                self.world.remove_component(entity_id, _AMRu2)
            except Exception:
                pass
            self.world.add_component(entity_id, _AMRu2(
                mana_per_tick=mana_per_tick, interval=interval, ticks_total=ticks,
            ))
        item.stack -= 1
        if item.stack <= 0:
            inv.items.remove(item)
        quest_fire("use_consumable", item_name=item_name)


class LootSystem(UIScaleMixin, System):
    """
    Detecta clique direito em cadáveres e exibe modal de loot.
    Clique esquerdo em item no modal → move para o inventário do jogador.
    Hover → tooltip com detalhes do item.
    Shift + hover → painel de comparação com item equipado no mesmo slot.
    """

    _FONT_BASES = {"font_sm": 20, "font_md": 24}

    # Layout do modal em lista vertical (tamanho fixo) — ui_sizes.py (UI.LOOT_*)
    MODAL_W   = UI.LOOT_MODAL_W    # largura fixa do modal
    ROW_H     = UI.LOOT_ROW_H      # altura de cada linha (ícone + texto)
    ICON_S    = UI.LOOT_ICON_S     # tamanho do ícone dentro da linha
    MAX_ROWS  = UI.LOOT_MAX_ROWS   # linhas visíveis (scroll se houver mais)
    PAD       = UI.LOOT_PAD
    TITLE_H   = UI.LOOT_TITLE_H
    SCROLL_W  = UI.LOOT_SCROLL_W   # largura da barra de rolagem
    MODAL_H   = TITLE_H + MAX_ROWS * (ROW_H + PAD // 2) + PAD  # altura fixa

    # Cores
    BG_COLOR     = (20, 14, 8, 220)
    BORDER_COLOR = (140, 100, 60)
    HOVER_COLOR  = (60, 45, 20)

    RARITY_COLORS = {
        "common":    (200, 200, 200),
        "uncommon":  ( 30, 200,  30),
        "rare":      ( 80, 140, 255),
        "epic":      (180,  50, 255),
        "legendary": (224, 135,  47),
        "mythic":    (221,  68,  68),
    }

    def __init__(self, world: World, screen: pygame.Surface, player_entity: int = -1):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        # open_corpse_id → LootUIState component (via property abaixo)
        self.pending_loot_corpse_id: int = -1
        self.pending_tooltip             = None  # lido por GameEngine no fim do frame
        # Callback chamado após cada ação de loot (moeda ou item) — injetado pelo GameEngine.
        # Online: aponta para _send_save_state() para salvar imediatamente na ação.
        self._on_loot_collected = None
        self._modal_x       = 0   # posição X do modal (definida ao abrir)
        self._modal_y       = 0   # posição Y do modal
        self._scroll_offset = 0   # índice da primeira linha visível
        self._pending_cursor = (0, 0)  # cursor quando o loot foi solicitado

    @property
    def open_corpse_id(self) -> int:
        from ui.ui_components import LootUIState
        ui = self.world.get_component(self.player_entity, LootUIState)
        return ui.open_corpse_id if ui else -1

    @open_corpse_id.setter
    def open_corpse_id(self, value: int) -> None:
        from ui.ui_components import LootUIState
        ui = self.world.get_component(self.player_entity, LootUIState)
        if ui:
            ui.open_corpse_id = value

    # ------------------------------------------------------------------ #
    def update(self, events: list = None, dt: float = 0) -> None:
        if events is None:
            return

        # Se o cadáver aberto foi removido, fecha o modal
        if self.open_corpse_id != -1:
            if self.world.get_component(self.open_corpse_id, Corpse) is None:
                self.open_corpse_id = -1

        # Verifica se o jogador chegou ao cadáver pendente
        if self.pending_loot_corpse_id != -1:
            p_corpse = self.world.get_component(self.pending_loot_corpse_id, Corpse)
            if not p_corpse:
                self.pending_loot_corpse_id = -1
            else:
                player_tm = None
                for _, tm, _ in self.world.get_entities_with(TileMovement, PlayerControlled):
                    player_tm = tm
                    break
                corpse_pos = None
                for eid, pos, _ in self.world.get_entities_with(Position, Corpse):
                    if eid == self.pending_loot_corpse_id:
                        corpse_pos = pos
                        break
                if player_tm and corpse_pos:
                    c_tile_x = int(corpse_pos.x / TILE_SIZE)
                    c_tile_y = int(corpse_pos.y / TILE_SIZE)
                    dist = max(abs(player_tm.current_tile_x - c_tile_x),
                               abs(player_tm.current_tile_y - c_tile_y))
                    if dist <= 1:
                        corpse_id = self.pending_loot_corpse_id
                        self.pending_loot_corpse_id = -1
                        if not p_corpse.loot and p_corpse.coins <= 0:
                            return
                        p_corpse.is_open = True
                        p_corpse.looted  = True
                        ax, ay = self._pending_cursor
                        self._open_modal(corpse_id, ax, ay)

        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos

                if event.button == 3:
                    if self.open_corpse_id != -1 and self._point_in_modal(mx, my):
                        # Clique direito dentro do modal aberto → equipa item
                        self._try_equip_item(mx, my)
                    else:
                        # Clique direito fora → tenta abrir cadáver
                        self._pending_cursor = (mx, my)
                        self._try_open_corpse(mx, my)

                elif event.button == 1:  # clique esquerdo → tenta pegar item
                    if self.open_corpse_id != -1:
                        modal = self._modal_rect()
                        if self._close_btn_rect(modal).collidepoint(mx, my):
                            self._close_modal()
                        else:
                            taken = self._try_take_item(mx, my)
                            if not taken:
                                # Clique fora do modal → fecha
                                if not self._point_in_modal(mx, my):
                                    self._close_modal()

            elif event.type == pygame.MOUSEWHEEL:
                if self.open_corpse_id != -1:
                    modal_r = self._modal_rect()
                    if modal_r.collidepoint(pygame.mouse.get_pos()):
                        corpse = self.world.get_component(self.open_corpse_id, Corpse)
                        if corpse:
                            total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                            max_scroll = max(0, total - self.MAX_ROWS)
                            self._scroll_offset = max(0, min(self._scroll_offset - event.y, max_scroll))

    def _get_camera_offset(self):
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - sw / 2, cam_pos.y - sh / 2
        return 0.0, 0.0

    def _try_open_corpse(self, mx: int, my: int) -> None:
        cam_x, cam_y = self._get_camera_offset()
        scale = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
        world_x = mx * scale + cam_x
        world_y = my * scale + cam_y

        # Coleta todos os cadáveres no alcance; prioriza os que ainda têm loot
        candidates = []
        for entity_id, pos, corpse in self.world.get_entities_with(Position, Corpse):
            half_w, half_h = 10, 6
            if (abs(world_x - pos.x) <= half_w + 4 and
                    abs(world_y - pos.y) <= half_h + 4):
                has_loot = bool(corpse.loot) or corpse.coins > 0
                candidates.append((entity_id, pos, corpse, has_loot))

        if not candidates:
            self.open_corpse_id = -1
            return

        # Cadáveres com loot primeiro; dentro do grupo, qualquer ordem serve
        candidates.sort(key=lambda c: 0 if c[3] else 1)
        entity_id, pos, corpse, _ = candidates[0]

        player_tm = None
        player_auto = None
        for _, tm, _, auto in self.world.get_entities_with(
                TileMovement, PlayerControlled, PlayerAutoMove):
            player_tm = tm
            player_auto = auto
            break

        c_tile_x = int(pos.x / TILE_SIZE)
        c_tile_y = int(pos.y / TILE_SIZE)
        if player_tm:
            dist = max(abs(player_tm.current_tile_x - c_tile_x),
                       abs(player_tm.current_tile_y - c_tile_y))
            if dist > 1:
                self.pending_loot_corpse_id = entity_id
                # Cancela combate para o movimento ao corpo não ser interrompido
                for _, _, p_cs in self.world.get_entities_with(PlayerControlled, CombatState):
                    p_cs.target_entity_id = -1
                    p_cs.is_pursuing = False
                    break
                if player_auto:
                    player_auto.ground_target = (c_tile_x, c_tile_y)
                    player_auto.active = True
                    player_auto.path.clear()
                    player_auto.path_recalc_timer = 0.0
                return

        if not corpse.loot and corpse.coins <= 0:
            return
        corpse.is_open = True
        corpse.looted  = True
        self._open_modal(entity_id, mx, my)

    def _open_modal(self, entity_id: int, ax: int, ay: int) -> None:
        """Abre o modal ancorado próximo ao ponto (ax, ay), clamped à tela."""
        self.open_corpse_id = entity_id
        self._scroll_offset = 0
        self._set_panel_scale(self.MODAL_W, self.MODAL_H)
        sw, sh = self.hud_surf.get_size()
        modal_w = self._u(self.MODAL_W)
        modal_h = self._u(self.MODAL_H)
        x = max(4, min(ax + 16, sw - modal_w - 4))
        y = max(4, min(ay - self._u(self.TITLE_H), sh - modal_h - 4))
        self._modal_x, self._modal_y = x, y

    def _modal_rect(self) -> pygame.Rect:
        """Tamanho sempre fixo — não depende do conteúdo."""
        self._set_panel_scale(self.MODAL_W, self.MODAL_H)
        return pygame.Rect(self._modal_x, self._modal_y, self._u(self.MODAL_W), self._u(self.MODAL_H))

    def _close_btn_rect(self, modal: pygame.Rect) -> pygame.Rect:
        """Botão X no canto superior direito da barra de título."""
        sz = self._u(self.TITLE_H) - self._u(6)
        return pygame.Rect(modal.right - sz - self._u(4), modal.y + self._u(3), sz, sz)

    def _row_rect(self, modal: pygame.Rect, row: int) -> pygame.Rect:
        """Rect de uma linha da lista (row 0 = primeira linha)."""
        y = modal.y + self._u(self.TITLE_H) + row * (self._u(self.ROW_H) + self._u(self.PAD) // 2)
        return pygame.Rect(modal.x + self._u(self.PAD), y, self._u(self.MODAL_W) - self._u(self.PAD) * 2, self._u(self.ROW_H))

    def _point_in_modal(self, mx: int, my: int) -> bool:
        return self._modal_rect().collidepoint(mx, my)

    def _try_take_item(self, mx: int, my: int) -> bool:
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False

        modal     = self._modal_rect()
        has_coins = corpse.coins > 0

        # Constrói a lista virtual de linhas considerando scroll
        # Linha virtual 0 = moedas (se houver), depois itens
        virtual_row = 0
        screen_row  = 0  # linha visível (0 = primeira visível)

        if has_coins:
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    for _, wallet, _ in self.world.get_entities_with(Wallet, PlayerControlled):
                        wallet.gold += corpse.coins
                        LOG.add(f"+{corpse.coins} moedas coletadas!", (255, 215, 0))
                        corpse.coins = 0
                        SOUNDS.play_ui("loot_gold")
                        if self._on_loot_collected:
                            self._on_loot_collected("gold")
                        break
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        for i, item in enumerate(corpse.loot):
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    for _, inv, _ in self.world.get_entities_with(Inventory, PlayerControlled):
                        # Tenta empilhar em stack existente
                        stacked = False
                        if item.max_stack > 1:
                            for existing in inv.items:
                                if existing is None:
                                    continue
                                if existing.name == item.name and existing.stack < existing.max_stack:
                                    existing.stack += item.stack
                                    stacked = True
                                    break
                        if stacked:
                            corpse.loot.pop(i)
                        elif len(inv.items) < inv.max_slots:
                            inv.items.append(item)
                            corpse.loot.pop(i)
                        else:
                            LOG.add("Inventario cheio!", (255, 160, 0))
                            break
                        col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                        LOG.add(f"Coletado: {item.name} ({item.rarity})", col)
                        SOUNDS.play_ui("loot_item")
                        quest_fire("collect_item", item_name=item.name)
                        if self._on_loot_collected:
                            self._on_loot_collected("item")
                        # Corrige scroll se necessário
                        total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                        self._scroll_offset = min(self._scroll_offset, max(0, total - self.MAX_ROWS))
                        break
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        return False

    def _try_equip_item(self, mx: int, my: int) -> bool:
        """Clique direito num item do loot: equipa diretamente, sem passar pelo inventário."""
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False

        modal     = self._modal_rect()
        has_coins = corpse.coins > 0
        virtual_row = 0
        screen_row  = 0

        # Pula linha de moedas (não é equipável)
        if has_coins:
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                screen_row += 1
            virtual_row += 1

        for i, item in enumerate(corpse.loot):
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    equip        = None
                    inv          = None
                    combat_stats = None
                    for _, eq, _pc in self.world.get_entities_with(Equipment, PlayerControlled):
                        equip = eq
                        break
                    for _, iv, _pc in self.world.get_entities_with(Inventory, PlayerControlled):
                        inv = iv
                        break
                    for _, cs, _pc in self.world.get_entities_with(CombatStats, PlayerControlled):
                        combat_stats = cs
                        break

                    if not (equip and combat_stats):
                        return False

                    target_slot = getattr(item, 'slot', None)
                    if target_slot is None or target_slot not in equip.slots:
                        # Item sem slot (consumível etc.) → vai para inventário
                        if inv and len(inv.items) < inv.max_slots:
                            inv.items.append(item)
                            corpse.loot.pop(i)
                            col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                            LOG.add(f"Coletado: {item.name} ({item.rarity})", col)
                            SOUNDS.play_ui("loot_item")
                            self._check_auto_close(corpse)
                        else:
                            LOG.add("Inventario cheio!", (255, 160, 0))
                        return True

                    # Restrição de armor_class por classe
                    if getattr(item, "armor_class", "") and item.item_type == "armor":
                        from engine.stats_system import CLASS_ARMOR_ALLOWED
                        from engine.components import CharacterStats as _CST
                        _char = self.world.get_component(self.player_entity, _CST)
                        _allowed = CLASS_ARMOR_ALLOWED.get(_char.class_id if _char else "", frozenset())
                        if item.armor_class not in _allowed:
                            _names = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}
                            LOG.add(f"Sua classe não pode usar armadura de {_names.get(item.armor_class, item.armor_class)}.", (255, 100, 80))
                            if inv and len(inv.items) < inv.max_slots:
                                inv.items.append(item)
                                corpse.loot.pop(i)
                            return True

                    # Arma de duas mãos → desequipa offhand
                    if getattr(item, 'two_handed', False) and target_slot == "mainhand":
                        old_oh = equip.slots.get("offhand")
                        if old_oh and inv and len(inv.items) < inv.max_slots:
                            for mod in old_oh.modifiers:
                                remove_modifier(combat_stats, mod)
                            inv.items.append(old_oh)
                            equip.slots["offhand"] = None

                    # Offhand bloqueado por arma de duas mãos
                    if target_slot == "offhand" and equip.is_offhand_locked():
                        LOG.add("Desequipe a arma de duas maos primeiro.", (255, 160, 0))
                        return True

                    # Devolve item já equipado ao inventário (se houver e houver espaço)
                    old_item = equip.slots[target_slot]
                    if old_item:
                        if inv and len(inv.items) < inv.max_slots:
                            for mod in old_item.modifiers:
                                remove_modifier(combat_stats, mod)
                            inv.items.append(old_item)
                        else:
                            LOG.add("Inventario cheio para trocar o item equipado!", (255, 160, 0))
                            return True

                    # Equipa o item do loot
                    equip.slots[target_slot] = item
                    corpse.loot.pop(i)
                    if target_slot == "mainhand" and getattr(item, "attack_speed", 0.0) > 0:
                        combat_stats.base_attack_interval = item.attack_speed
                    for mod in item.modifiers:
                        add_modifier(combat_stats, mod)
                    col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                    LOG.add(f"Equipado: {item.name} ({item.rarity})", col)
                    SOUNDS.play_ui("equip_item")
                    quest_fire("equip_item", item_name=item.name, item_type=item.item_type)
                    total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                    self._scroll_offset = min(self._scroll_offset, max(0, total - self.MAX_ROWS))
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        return False

    def _check_auto_close(self, corpse) -> None:
        """Fecha o modal automaticamente se o cadáver estiver vazio."""
        if corpse.coins == 0 and not corpse.loot:
            # Todo o loot foi retirado — reduz o timer para 30s
            corpse.timer = min(corpse.timer, Corpse.LOOTED_DECAY_TIME)
            self._close_modal()

    def _close_modal(self) -> None:
        if self.open_corpse_id != -1:
            corpse = self.world.get_component(self.open_corpse_id, Corpse)
            if corpse:
                corpse.is_open = False
        self.open_corpse_id = -1

    # ------------------------------------------------------------------ #
    def render_world(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        """Desenha cadáveres no mundo: vazios primeiro (embaixo), com loot por cima."""
        all_corpses = list(self.world.get_entities_with(Position, Corpse))
        all_corpses.sort(key=lambda c: 0 if (not c[2].loot and c[2].coins <= 0) else 1)
        for entity_id, pos, corpse in all_corpses:
            draw_x = pos.x - camera_offset_x
            draw_y = pos.y - camera_offset_y
            color = (180, 150, 30) if corpse.coins > 0 else ((120, 80, 40) if corpse.loot else (60, 40, 20))
            pygame.draw.ellipse(self.world_surf, color,
                                (int(draw_x - 10), int(draw_y - 6), 20, 12))
            pygame.draw.ellipse(self.world_surf, (80, 55, 25),
                                (int(draw_x - 10), int(draw_y - 6), 20, 12), 1)

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        """Renderiza apenas o modal de loot (chamado por cima de tudo)."""
        self.pending_tooltip = None

        if self.open_corpse_id == -1:
            return
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return
        _char_loot  = self.world.get_component(self.player_entity, CharacterStats)
        _viewer_cls = _char_loot.class_id if _char_loot else None

        modal = self._modal_rect()
        self.hud_surf.blit(fill_surf((modal.w, modal.h), self.BG_COLOR), modal.topleft)
        pygame.draw.rect(self.hud_surf, self.BORDER_COLOR, modal, 2, border_radius=4)

        # --- Barra de título ---
        title = self.font_sm.render("Loot", False, self.BORDER_COLOR)
        self.hud_surf.blit(title, (modal.x + self._u(self.PAD), modal.y + (self._u(self.TITLE_H) - title.get_height()) // 2))

        # Botão X
        close_r = self._close_btn_rect(modal)
        pygame.draw.rect(self.hud_surf, (90, 30, 30), close_r, border_radius=2)
        x_surf = self.font_sm.render("X", False, (220, 100, 100))
        self.hud_surf.blit(x_surf, (close_r.centerx - x_surf.get_width() // 2,
                                  close_r.centery - x_surf.get_height() // 2))

        mx, my = pygame.mouse.get_pos()
        has_coins = corpse.coins > 0

        # Lista virtual: [("coin",) se has_coins] + [("item", item) ...]
        virtual: list = []
        if has_coins:
            virtual.append(("coin",))
        for item in corpse.loot:
            virtual.append(("item", item))

        total = len(virtual)

        # --- Barra de rolagem (se necessário) ---
        need_scroll = total > self.MAX_ROWS
        scroll_w = self._u(self.SCROLL_W)
        row_w = self._u(self.MODAL_W) - self._u(self.PAD) * 2 - (scroll_w + self._u(4) if need_scroll else 0)

        if need_scroll:
            # Trilho
            track_x = modal.right - scroll_w - self._u(4)
            track_y = modal.y + self._u(self.TITLE_H) + self._u(self.PAD) // 2
            track_h = self.MAX_ROWS * (self._u(self.ROW_H) + self._u(self.PAD) // 2) - self._u(self.PAD) // 2
            pygame.draw.rect(self.hud_surf, (40, 30, 18),
                             (track_x, track_y, scroll_w, track_h), border_radius=3)
            # Thumb
            thumb_h = max(20, track_h * self.MAX_ROWS // total)
            max_scroll = total - self.MAX_ROWS
            thumb_y = track_y + (track_h - thumb_h) * self._scroll_offset // max(1, max_scroll)
            pygame.draw.rect(self.hud_surf, (130, 100, 55),
                             (track_x, thumb_y, scroll_w, thumb_h), border_radius=3)

        # --- Renderiza linhas visíveis ---
        visible = virtual[self._scroll_offset: self._scroll_offset + self.MAX_ROWS]
        hovered_item = None

        for screen_row, entry in enumerate(visible):
            # Rect da linha (largura ajustada se há scrollbar)
            base_rr = self._row_rect(modal, screen_row)
            rr = pygame.Rect(base_rr.x, base_rr.y, row_w, base_rr.h)
            hovered = rr.collidepoint(mx, my)

            if entry[0] == "coin":
                # --- Linha de moedas ---
                icon_s = self._u(self.ICON_S)
                pygame.draw.rect(self.hud_surf, (70, 55, 15) if hovered else (28, 20, 8), rr, border_radius=3)
                pygame.draw.rect(self.hud_surf, (200, 170, 50) if hovered else (100, 80, 20), rr, 1, border_radius=3)
                icon_r = pygame.Rect(rr.x + self._u(4), rr.centery - icon_s // 2, icon_s, icon_s)
                r_out = icon_s // 2
                r_in  = max(1, r_out - self._u(4))
                pygame.draw.circle(self.hud_surf, (180, 140, 0),  icon_r.center, r_out)
                pygame.draw.circle(self.hud_surf, (255, 215, 0),  icon_r.center, r_in)
                pygame.draw.circle(self.hud_surf, (120, 90, 0),   icon_r.center, r_out, 1)
                g_surf = self.font_sm.render("G", False, (120, 90, 0))
                self.hud_surf.blit(g_surf, (icon_r.centerx - g_surf.get_width() // 2,
                                          icon_r.centery - g_surf.get_height() // 2))
                tx = icon_r.right + self._u(8)
                ty = rr.centery - self.font_md.get_height() // 2
                self.hud_surf.blit(self.font_md.render(f"{corpse.coins} moedas", False, (255, 215, 0)), (tx, ty))
                if hovered:
                    self.pending_tooltip = (mx, my, "Moedas",
                                            [(f"{corpse.coins} moedas disponíveis", (255, 215, 0)),
                                             ("Clique p/ coletar tudo", (140, 140, 140))])

            else:
                # --- Linha de item ---
                item = entry[1]
                icon_s = self._u(self.ICON_S)
                bg_col = self.HOVER_COLOR if hovered else (28, 20, 8)
                pygame.draw.rect(self.hud_surf, bg_col, rr, border_radius=3)

                icon_r = pygame.Rect(rr.x + self._u(4), rr.centery - icon_s // 2, icon_s, icon_s)
                icon_surf = ICONS.get(ICONS.item_key(item), icon_s)
                if icon_surf:
                    self.hud_surf.blit(icon_surf, icon_r)
                else:
                    fb = self.RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.hud_surf, fb, icon_r, border_radius=2)
                from ui.ui_helpers import draw_stack_count as _dsc2
                _dsc2(self.hud_surf, item, icon_r, self.font_sm)

                rc = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                tx = icon_r.right + self._u(8)
                _stack = getattr(item, "stack", 1)
                _max_s = getattr(item, "max_stack", 1)
                _name_lbl = f"{item.name} x{_stack}" if _max_s > 1 else item.name
                name_surf = self.font_md.render(_name_lbl, False, rc)
                sub_surf  = self.font_sm.render(f"{item.item_type}  •  {item.slot}", False, (130, 115, 95))
                total_h   = name_surf.get_height() + 2 + sub_surf.get_height()
                ty = rr.centery - total_h // 2
                self.hud_surf.blit(name_surf, (tx, ty))
                self.hud_surf.blit(sub_surf,  (tx, ty + name_surf.get_height() + 2))

                border_col = (180, 140, 60) if hovered else (55, 40, 22)
                pygame.draw.rect(self.hud_surf, border_col, rr, 1, border_radius=3)

                if hovered:
                    hovered_item = item

        # Vazio
        if not virtual:
            empty = self.font_sm.render("(vazio)", False, (120, 100, 80))
            rr = self._row_rect(modal, 0)
            self.hud_surf.blit(empty, (rr.x + self._u(4), rr.centery - empty.get_height() // 2))

        # Tooltip + comparação no hover
        if hovered_item is not None:
            lines = item_tooltip_lines(hovered_item, _viewer_cls)
            lines.append(("Clique p/ pegar | Shift p/ comparar", (140, 140, 140)))
            equip = None
            for _, eq, _ in self.world.get_entities_with(Equipment, PlayerControlled):
                equip = eq
                break
            equipped_item = equip.slots.get(hovered_item.slot) if equip else None
            name_col = self.RARITY_COLORS.get(hovered_item.rarity, (255, 220, 100))
            self.pending_tooltip = (mx, my, hovered_item.name, lines, name_col,
                                    hovered_item, equipped_item)


# ---------------------------------------------------------------------------
# SkillSystem
# ---------------------------------------------------------------------------
from ui.skill_handlers import SkillHandlers

class SkillSystem(System, SkillHandlers):
    """Gerencia habilidades ativas do jogador (teclas 1-7, incluindo talentos).

    Handlers de habilidades (_skill_* e _talent_*) vivem em skill_handlers.py
    via herança de SkillHandlers — edite lá para adicionar ou alterar skills.
    """

    SKILL_KEYS = [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
                  pygame.K_5, pygame.K_6, pygame.K_7]

    def __init__(self, world: World, player_entity_id: int,
                 screen: "pygame.Surface | None" = None):
        self.world = world
        self.player_entity_id = player_entity_id
        self.world_surf = screen
        self.hud_surf   = screen
        # Em modo online o servidor é autoritativo: cliente só aplica feedback visual.
        # Injetado por game.py após _connect_online(). False = comportamento offline normal.
        self._server_authoritative: bool = False
        self._net = None  # NetworkClient — injetado por game.py para enviar CAST_SKILL

    def _is_on_screen(self, pos: "Position") -> bool:
        if self.world_surf is None or pos is None:
            return True
        sw = self.world_surf.get_width()
        sh = self.world_surf.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            cam_x = cam_pos.x - sw / 2
            cam_y = cam_pos.y - sh / 2
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            return 0 <= sx <= sw and 0 <= sy <= sh
        return True

    def update(self, events: list = None, dt: float = 0) -> None:
        player_skills = self.world.get_component(self.player_entity_id, PlayerSkills)
        if not player_skills:
            return

        # Atualiza GCD e cooldowns de habilidades
        if player_skills.gcd_timer > 0:
            player_skills.gcd_timer = max(0.0, player_skills.gcd_timer - dt)
        for skill in player_skills.skills:
            if skill is None:
                continue
            if skill.current_cooldown > 0:
                skill.current_cooldown = max(0.0, skill.current_cooldown - dt)
            if skill.max_charges > 0 and skill.charges > 0 and skill.charge_timeout > 0:
                skill.charge_timer = max(0.0, skill.charge_timer - dt)
                if skill.charge_timer <= 0:
                    skill.charges = 0
                    LOG.add(f"{skill.name}: carga expirou!", (200, 100, 50))

        # Fatiador de Corpos: tick de dano AoE a cada 1s durante 5s
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if char_stats and char_stats.fatiador_timer > 0:
            char_stats.fatiador_timer = max(0.0, char_stats.fatiador_timer - dt)
            char_stats.fatiador_tick  = max(0.0, char_stats.fatiador_tick  - dt)
            if char_stats.fatiador_tick <= 0 and char_stats.fatiador_timer > 0:
                char_stats.fatiador_tick = 1.0
                tile_move = self.world.get_component(self.player_entity_id, TileMovement)
                ps = self.world.get_component(self.player_entity_id, PlayerSkills)
                skill_obj = ps.skill_by_id("fatiador_de_corpos") if ps else None
                if tile_move:
                    self._fatiador_aoe_tick(skill_obj, tile_move)
            if char_stats.fatiador_timer <= 0:
                LOG.add("Fatiador de Corpos terminou.", (200, 160, 100))

        if not events:
            return

        combat_state = self.world.get_component(self.player_entity_id, CombatState)
        # Canalização activa: tecla de skill cancela a canalização antes de processar
        if combat_state and combat_state.is_casting:
            from engine.components import Channeling, SpellCast as _SCGuard
            channeling = self.world.get_component(self.player_entity_id, Channeling)
            _sc_guard  = self.world.get_component(self.player_entity_id, _SCGuard)
            # Safety valve: is_casting preso sem SpellCast nem Channeling → libera input
            if not channeling and _sc_guard is None:
                combat_state.is_casting = False
            else:
                if channeling:
                    for event in events:
                        if event.type == pygame.KEYDOWN:
                            any_skill_key = any(
                                skill is not None and event.key == player_skills.keybinds[i]
                                for i, skill in enumerate(player_skills.skills)
                            )
                            if any_skill_key:
                                self.world.remove_component(self.player_entity_id, Channeling)
                                combat_state.is_casting = False
                                from ui.combat_log import LOG as _LOG
                                from ui.floating_text import WARN as _WARN
                                _WARN.add("Canalização interrompida!")
                                break
                return  # aguarda próximo frame para usar a nova skill

        if combat_state and not combat_state.can_act():
            return

        for event in events:
            if event.type != pygame.KEYDOWN:
                continue
            for i, skill in enumerate(player_skills.skills):
                if skill is None:
                    continue
                if event.key == player_skills.keybinds[i]:
                    if not self._use_skill(i, skill):
                        skill.fail_flash_timer = 0.2
                    break

    # ------------------------------------------------------------------
    def _use_skill(self, _idx: int, skill) -> bool:
        """Tenta usar a skill. Retorna True se executou, False se falhou."""
        # Em modo online, o servidor calcula dano e efeitos.
        # O cliente executa apenas cooldown/GCD/som e envia CAST_SKILL.
        if self._server_authoritative:
            return self._use_skill_visual_only(_idx, skill)

        # Talent lock check (offline também)
        if skill.skill_id and skill.skill_id in _TALENT_SKILL_REQ_SYS:
            _tl_tid_off, _tl_min_off = _TALENT_SKILL_REQ_SYS[skill.skill_id]
            from engine.components import TalentTree as _TTLockOff
            _tt_lk_off = self.world.get_component(self.player_entity_id, _TTLockOff)
            if _tt_lk_off is not None and _tt_lk_off.allocated.get(_tl_tid_off, 0) < _tl_min_off:
                WARN.add("Requer talento")
                return False

        combat_state = self.world.get_component(self.player_entity_id, CombatState)
        if combat_state and not combat_state.can_act():
            return False
        # Sleep/disoriented/polymorph: can_act() não cobre (StatusEffects, não
        # CombatState) — igual ao bloqueio de PlayerInputSystem para movimento/ações.
        from engine.utils import is_action_locked
        if is_action_locked(self.world, self.player_entity_id):
            return False

        player_skills = self.world.get_component(self.player_entity_id, PlayerSkills)

        # Bloqueia qualquer skill se o GCD ainda não zerou
        if player_skills and player_skills.gcd_timer > 0:
            return False

        # Bloqueia qualquer skill enquanto Fatiador de Corpos está em channel
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if char_stats and char_stats.fatiador_timer > 0:
            return False

        combat_stats = self.world.get_component(self.player_entity_id, CombatStats)
        tile_move    = self.world.get_component(self.player_entity_id, TileMovement)
        if not combat_stats or not tile_move:
            return False

        # Skills ofensivas: selecionam alvo e iniciam combate.
        # is_pursuing só é setado para skills INSTANTÂNEAS — skills com cast_time
        # aguardam o cast completar para não aggrar o mob prematuramente.
        if getattr(skill, "offensive", True):
            self._resolve_target(combat_state, tile_move)
            if isinstance(combat_state, CombatState):
                has_cast = getattr(skill, "cast_time", 0.0) > 0
                enter_combat(combat_state)
                if not has_cast:
                    combat_state.is_pursuing = True

        if not skill.is_ready():
            if skill.current_cooldown > 0:
                WARN.add(f"Em recarga ({skill.current_cooldown:.1f}s)")
            elif skill.max_charges > 0:
                WARN.add("Sem cargas")
            return False

        # Todas as skills são despachadas por skill_id — catálogo como fonte única
        if skill.skill_id:
            handler_fn = getattr(self, f"_skill_{skill.skill_id}", None)
            if handler_fn:
                success = handler_fn(skill, combat_stats, combat_state, tile_move)
                if success:
                    has_cast = getattr(skill, "cast_time", 0.0) > 0
                    is_aoe   = getattr(skill, "needs_aoe_target", False)
                    # Só bloqueia movimento se o SpellCast criado for interruptível.
                    # Casts não-interruptíveis (Calcinar, Recarregar+Prático) permitem mover.
                    if has_cast and isinstance(combat_state, CombatState):
                        from engine.components import SpellCast as _SpellCast
                        _sc = self.world.get_component(self.player_entity_id, _SpellCast)
                        if _sc is None or _sc.interruptible:
                            combat_state.is_casting = True
                    if skill.sound_name and not has_cast and not is_aoe:
                        SOUNDS.play_skill(skill.sound_name)
                    if player_skills:
                        player_skills.gcd_timer = PlayerSkills.GCD_DURATION
                    # Skills instantâneas (sem cast_time) já causaram dano de forma
                    # síncrona dentro de handler_fn (ex: deal_damage chamado ali) —
                    # contar a quest aqui é seguro. Skills com cast (ex: Bola de Fogo)
                    # só causam dano depois (projétil/PROJECTILE), então a contagem
                    # acontece em PlayerProjectileSystem._on_hit, após o dano efetivo
                    # (não aqui, no momento de ativar a skill).
                    if not has_cast:
                        from engine.components import TrainingDummy as _TDsk_off
                        _qf_target_off = getattr(combat_state, "target_entity_id", -1)
                        _on_dummy_off = (_qf_target_off != -1
                                        and self.world.get_component(_qf_target_off, _TDsk_off) is not None)
                        quest_fire("use_skill", skill_id=skill.skill_id, on_dummy=_on_dummy_off)
                return bool(success)
            else:
                LOG.add(f"{skill.name}: sem implementacao para '{skill.skill_id}'.", (180, 60, 60))
            return False

        return False

    # ------------------------------------------------------------------
    def _use_skill_visual_only(self, _idx: int, skill) -> bool:
        """Modo online: replica as verificações do offline ANTES do visual.

        O offline faz em _use_skill():
          1. can_act() check
          2. GCD check
          3. skill.is_ready() check
          4. Para ofensivas: _resolve_target → se -1, retorna False
          5. enter_combat + is_pursuing
          6. Chama handler → handler verifica rage/mana/range e retorna False se falhar
          7. Só então: cooldown, GCD, som

        Online não tem o handler local, então replicamos as verificações que dependem
        de estado local disponível no cliente.
        """
        combat_state  = self.world.get_component(self.player_entity_id, CombatState)
        player_skills = self.world.get_component(self.player_entity_id, PlayerSkills)
        _tile_move_sk = self.world.get_component(self.player_entity_id,
                                                  __import__("engine.components", fromlist=["TileMovement"]).TileMovement)

        # 0. Talent lock: skill requer talento que não está alocado
        if skill.skill_id and skill.skill_id in _TALENT_SKILL_REQ_SYS:
            _tl_tid, _tl_min = _TALENT_SKILL_REQ_SYS[skill.skill_id]
            from engine.components import TalentTree as _TTLock
            _tt_lk = self.world.get_component(self.player_entity_id, _TTLock)
            if _tt_lk is not None and _tt_lk.allocated.get(_tl_tid, 0) < _tl_min:
                WARN.add("Requer talento")
                return False

        # 1. can_act (igual offline)
        if combat_state and not combat_state.can_act():
            return False
        # 1b. Sleep/disoriented/polymorph: can_act() não cobre (StatusEffects, não
        # CombatState) — igual ao bloqueio de PlayerInputSystem para movimento/ações.
        from engine.utils import is_action_locked
        if is_action_locked(self.world, self.player_entity_id):
            return False
        # 2. GCD (igual offline)
        if player_skills and player_skills.gcd_timer > 0:
            return False
        # 3. Cooldown/cargas + pending server (igual offline + online)
        if getattr(skill, "_server_pending", False):
            return False  # aguardando confirmação do servidor
        if not skill.is_ready():
            if skill.current_cooldown > 0:
                WARN.add(f"Em recarga ({skill.current_cooldown:.1f}s)")
            elif skill.max_charges > 0:
                WARN.add("Sem cargas")
            return False

        _is_offensive = getattr(skill, "offensive", True)
        _has_cast     = getattr(skill, "cast_time", 0.0) > 0
        _is_aoe       = getattr(skill, "needs_aoe_target", False)

        _is_online = self._net is not None

        # Skills AOE (Calamidade Flamejante): chama o handler diretamente para mostrar
        # a mira antes do clique — CAST_SKILL é enviado pelo AoeTargetingSystem ao clicar.
        if _is_aoe and _is_online and skill.skill_id:
            _aoe_handler = getattr(self, f"_skill_{skill.skill_id}", None)
            if _aoe_handler:
                _cs_aoe   = self.world.get_component(self.player_entity_id,
                                                      __import__("engine.components", fromlist=["CombatStats"]).CombatStats)
                _tm_aoe   = self.world.get_component(self.player_entity_id,
                                                      __import__("engine.components", fromlist=["TileMovement"]).TileMovement)
                _cst_aoe  = self.world.get_component(self.player_entity_id,
                                                      __import__("engine.components", fromlist=["CombatState"]).CombatState)
                return bool(_aoe_handler(skill, _cs_aoe, _cst_aoe, _tm_aoe))
            return False
        _char = self.world.get_component(self.player_entity_id,
                                          __import__("engine.components", fromlist=["CharacterStats"]).CharacterStats)
        _cs   = self.world.get_component(self.player_entity_id,
                                          __import__("engine.components", fromlist=["CombatStats"]).CombatStats)

        # 4. Para ofensivas: resolve alvo + inicia chase + verifica range
        _needs_target = getattr(skill, "needs_target", True)
        if _is_offensive and combat_state and _tile_move_sk:
            if _is_online:
                _target_local = combat_state.target_entity_id
                if _needs_target:
                    # Limpa target inválido: removido do mundo OU fora da visão (fog/parede)
                    if _target_local != -1:
                        _stale_pos = self.world.get_component(_target_local,
                                         __import__("engine.components", fromlist=["Position"]).Position)
                        _stale_vis = self.world.get_component(_target_local,
                                         __import__("engine.components", fromlist=["Visible"]).Visible)
                        # Alvo morto (mob com HP<=0 ou player remoto cujo corpo
                        # ficou no chão): não é mais alvo válido — limpa igual
                        # entidade removida, evita tocar som/iniciar cast num corpo.
                        _stale_cs = self.world.get_component(_target_local,
                                         __import__("engine.components", fromlist=["CombatStats"]).CombatStats)
                        _stale_rc = self.world.get_component(_target_local,
                                         __import__("engine.components", fromlist=["RemoteControlled"]).RemoteControlled)
                        _is_dead_target = (
                            (_stale_cs is not None and _stale_cs.current_hp <= 0) or
                            (_stale_rc is not None and _stale_rc.hp <= 0)
                        )
                        if _stale_pos is None or _stale_vis is None or _is_dead_target:
                            _target_local = -1
                            combat_state.target_entity_id = -1
                    if _target_local == -1:
                        # Auto-select: mesmo comportamento do offline — B6
                        _params_pre = getattr(skill, "params", {}) or {}
                        _auto_range = _params_pre.get("max_range") or getattr(skill, "cast_range", 6)
                        _target_local = self._resolve_target(
                            combat_state, _tile_move_sk, _max_range=_auto_range)
                    if _target_local == -1:
                        WARN.add("Nenhum alvo")
                        return False

                # enter_combat + is_pursuing ANTES do range check (igual offline _use_skill:5307-5313)
                # Garante que pressionar skill inicia o chase/auto-attack mesmo fora de alcance.
                # is_pursuing=True para todas as ofensivas — cast-time skills bloqueiam
                # auto-attack via is_casting=True em can_act() durante o cast.
                from engine.stat_fns import enter_combat as _ec_pre
                _ec_pre(combat_state)
                combat_state.is_pursuing = True

                # Range check — apenas para skills que exigem alvo explícito
                if _needs_target and _target_local != -1:
                    _params          = getattr(skill, "params", {}) or {}
                    # Mago usa cast_range no catálogo; guerreiro/arqueiro usam params.max_range
                    _max_range_tiles = _params.get("max_range") or getattr(skill, "cast_range", 0)
                    # cast_range=0 significa melee (sem range explícito no catálogo);
                    # usa 1 tile para o check online — equivale a MELEE_RANGE_PX (1t + tolerance)
                    if _max_range_tiles == 0:
                        _max_range_tiles = 1
                    _min_range_tiles = _params.get("min_range", 0)
                    _tol = SkillHandlers.RANGE_TOLERANCE_PX
                    _max_px = _max_range_tiles * TILE_SIZE + _tol
                    _min_px = max(0.0, _min_range_tiles * TILE_SIZE - _tol) if _min_range_tiles > 0 else 0.0
                    _pl_pos  = self.world.get_component(self.player_entity_id,
                                                         __import__("engine.components", fromlist=["Position"]).Position)
                    _tgt_pos = self.world.get_component(_target_local,
                                                         __import__("engine.components", fromlist=["Position"]).Position)
                    if _pl_pos and _tgt_pos:
                        _dx_r = _pl_pos.x - _tgt_pos.x
                        _dy_r = _pl_pos.y - _tgt_pos.y
                        _d_sq = _dx_r*_dx_r + _dy_r*_dy_r
                        if _d_sq > _max_px * _max_px:
                            WARN.add("Fora de alcance")
                            return False
                        if _min_px > 0 and _d_sq < _min_px * _min_px:
                            WARN.add("Alvo muito próximo")
                            return False
                        # LOS check para skills com projétil: bloqueia se há parede no caminho.
                        # Evita deduzir mana/cooldown quando o projétil seria destruído na parede.
                        _skill_has_proj = getattr(skill, "skill_id", "") in {"bola_de_fogo"}
                        if _skill_has_proj:
                            _tmap_los = get_tilemap()
                            if _tmap_los:
                                _ptx_los = int(_pl_pos.x / TILE_SIZE)
                                _pty_los = int(_pl_pos.y / TILE_SIZE)
                                _ttx_los = int(_tgt_pos.x / TILE_SIZE)
                                _tty_los = int(_tgt_pos.y / TILE_SIZE)
                                if not EnemyAISystem._has_line_of_sight(
                                        _tmap_los, _ptx_los, _pty_los, _ttx_los, _tty_los):
                                    WARN.add("Há obstáculos no caminho")
                                    return False
            else:
                # Offline: _resolve_target auto-seleciona e verifica CombatStats
                _target = self._resolve_target(combat_state, _tile_move_sk)
                if _target == -1 and _needs_target:
                    WARN.add("Nenhum alvo")
                    return False

        # 5. Rage/mana/HP threshold — verifica com awareness de proc (igual offline)
        _rage_cost = 0
        _mana_cost = 0
        # Proc: free charge que ignora custo e restrição de HP (ex: Assassino → Executar)
        _proc_attr         = getattr(skill, "proc_attr",         "")
        _proc_ignores_cost = getattr(skill, "proc_ignores_cost", False)
        _is_procced = bool(
            _proc_attr and _char and getattr(_char, _proc_attr, 0) > 0
        )
        if skill.skill_id and _char and _cs:
            # Rage: usa custo base do skill, mas prefere custo modificado por talentos
            # (ex: Golpe Poderoso tem golpe_poderoso_rage_cost = 15 - pontos Veterano)
            _rage_cost = getattr(skill, "rage_cost", 0)
            _talent_cost = getattr(_cs, f"{skill.skill_id}_rage_cost", None)
            if _talent_cost is not None:
                _rage_cost = _talent_cost  # talento modificou o custo (ex: Veterano)
            if _rage_cost > 0 and not (_is_procced and _proc_ignores_cost):
                if _char.rage < _rage_cost:
                    WARN.add(f"Raiva insuficiente ({_rage_cost})")
                    return False
            # Mana custo fixo (ex: Bola de Fogo, Calcinar, Nova Congelante)
            _fixed_mana = getattr(skill, "mana_cost", 0)
            if _fixed_mana > 0 and not (_is_procced and _proc_ignores_cost):
                # Aplica descontos de mana por talento (mesma lógica dos handlers offline)
                from content.skill_config import SKILL_CATALOG as _SC_mc
                _skill_mc = _SC_mc.get(skill.skill_id, {})
                # Desconto flat (ex: Frieza → fire_mana_discount)
                _disc_attr = _skill_mc.get("mana_discount_attr", "")
                if _disc_attr and _cs:
                    _fixed_mana = max(0, _fixed_mana - int(getattr(_cs, _disc_attr, 0)))
                # Desconto percentual (ex: Piromaníaco → pyromania_bonus)
                _pct_attr = _skill_mc.get("mana_pct_discount_attr", "")
                if _pct_attr and _cs:
                    _pct = getattr(_cs, _pct_attr, 0.0)
                    if _pct > 0:
                        _fixed_mana = max(0, int(_fixed_mana * (1.0 - _pct)))
                # Chama Interna: proc ativo → cast grátis (não bloqueia por mana)
                _fire_free = _char and getattr(_char, "fire_instant_ready", False)
                if not _fire_free:
                    _char_mana = getattr(_char, "mana", 0) if _char else 0
                    if _char_mana < _fixed_mana:
                        WARN.add("Mana insuficiente")
                        return False
            # Mana custo percentual (ex: Polimorfia = 10% mana máxima)
            _mana_pct = getattr(skill, "mana_cost_pct", 0.0)
            if _mana_pct > 0 and hasattr(_cs, "max_mana"):
                _mana_cost = int(_cs.max_mana * _mana_pct)
                if _mana_cost > 0 and getattr(_char, "mana", 0) < _mana_cost:
                    WARN.add("Mana insuficiente")
                    return False
            # Concentração (arqueiro) — mesmo check do offline _check_concentration
            _conc_cost = (getattr(skill, "params", {}) or {}).get("concentration_cost", 0)
            if _conc_cost > 0 and _char:
                _conc_free = _cs and getattr(_cs, "concentration_free", False)
                if not _conc_free and getattr(_char, "concentration", 0) < _conc_cost:
                    WARN.add(f"Concentração insuficiente ({int(getattr(_char, 'concentration', 0))}/{_conc_cost})")
                    return False
        # HP threshold (ex: Executar exige alvo <30% HP) — verifica no cliente via RemoteEntityMeta
        if not (_is_procced and _proc_ignores_cost) and _is_online and combat_state:
            _params_sk   = getattr(skill, "params", {}) or {}
            _hp_threshold = _params_sk.get("hp_threshold", 0.0) if isinstance(_params_sk, dict) else 0.0
            if _hp_threshold > 0:
                _tgt_local = combat_state.target_entity_id
                from engine.components import RemoteEntityMeta as _REM_sk
                _meta_sk = self.world.get_component(_tgt_local, _REM_sk)
                if _meta_sk and _meta_sk.hp_max > 0:
                    if _meta_sk.hp / _meta_sk.hp_max >= _hp_threshold:
                        WARN.add(f"Alvo precisa ter <{int(_hp_threshold * 100)}% HP")
                        return False

        # 6. Aplica efeitos locais
        if _is_online:
            # Online: não aplica GCD nem cooldown — espera confirmação do servidor.
            # Mostra flash de "botão pressionado" (igual ao de falha por recursos).
            # GCD + cooldown + som são aplicados em SKILL_RESULT quando confirmado.
            skill.fail_flash_timer          = 0.15   # flash escuro rápido = "registrado"
            skill._server_pending           = True   # bloqueia reuso até confirmação
            skill._server_pending_timeout   = 0.40   # fallback: libera após 400ms (dentro do GCD 0.8s)
        else:
            # Offline: aplica tudo imediatamente (sem servidor para confirmar).
            # Sincroniza cooldown em TODOS os slots com a mesma skill_id (Bug 4).
            for _sk_sync in player_skills.skills:
                if _sk_sync and _sk_sync.skill_id == skill.skill_id:
                    _sk_sync.current_cooldown = skill.cooldown
            if player_skills:
                player_skills.gcd_timer = PlayerSkills.GCD_DURATION
            if skill.sound_name and not _has_cast:
                SOUNDS.play_skill(skill.sound_name)
        # Para skills baseadas em cargas: consome localmente (igual ao handler offline)
        # Servidor também consume a sua cópia; cargas são regrantadas via morte de mob.
        if skill.max_charges > 0 and skill.charges > 0:
            skill.charges     -= 1
            skill.charge_timer = 0.0

        # Cura NÃO é predita localmente — servidor confirma via STATS_UPDATE (heal_amount).
        # Isso garante que Vitória Iminente só cura se o dano for aplicado no servidor.

        # NÃO deduz rage/mana localmente — servidor é autoritativo
        # Motivo: se servidor falhar (range/target), rage não deve ser consumida.
        # Servidor envia STATS_UPDATE com rage real após processar a skill.
        # Cliente atualiza a partir desse valor (sem "dip" visual falso).
        _rage_pre  = getattr(_char, "rage",  0) if _char else 0
        _mana_pre  = getattr(_cs,   "mana",  0) if _cs   else 0
        # (dedução acontece no servidor via sync_player_resources + handler)

        # 7. enter_combat + is_pursuing — já feito no passo 4 para online ofensivas.
        # Para não-ofensivas ou offline, aplica aqui.
        if _is_offensive and combat_state and not _is_online:
            from engine.stat_fns import enter_combat as _ec_sk
            _ec_sk(combat_state)
            if not _has_cast:
                combat_state.is_pursuing = True

        # Envia CAST_SKILL com rage/mana PRÉ-dedução para o servidor validar corretamente
        if self._net:
            from shared.messages import MsgType as _MT
            _tid_local  = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _tid_server = -1
            if _tid_local != -1:
                from engine.components import RemoteEntityMeta as _REM_cast
                _meta_cast = self.world.get_component(_tid_local, _REM_cast)
                if _meta_cast:
                    _tid_server = _meta_cast.server_eid
            # PvP: alvo pode ser player remoto (não está em RemoteEntityMeta)
            if _tid_server == -1 and _tid_local != -1:
                from engine.components import RemoteControlled as _RCcast
                _rc_cast = self.world.get_component(_tid_local, _RCcast)
                if _rc_cast is not None:
                    _tid_server = _rc_cast.server_eid

            # Direção do player até o mouse (mundo) — capturada no instante do cast,
            # igual ao alvo (_tid_server) já é. Sem isso ficava hardcoded em (0,0):
            # skills de cone sem alvo travado (ex: Tiro Múltiplo) chegavam no
            # servidor com direção nula, _server_tiro_multiplo descartava
            # (dlen < 0.001) e a skill nunca acertava nada — mostrava a mira,
            # mas não disparava (ver arquitetura/PROBLEMAS_ARQUITETURA.md).
            # Pirofagia já calcula isso à parte (PirofagiaSystem, spell_system.py);
            # aqui cobre as demais skills de cone que passam pelo fluxo genérico.
            _dir_x, _dir_y = 0.0, 0.0
            _pos_cast = self.world.get_component(self.player_entity_id, Position)
            if _pos_cast:
                _cam_x_cast = _cam_y_cast = 0.0
                for _, _camc_cast, _camp_cast in self.world.get_entities_with(Camera, Position):
                    _lw_cast = self.world_surf.get_width()  if self.world_surf else 1280
                    _lh_cast = self.world_surf.get_height() if self.world_surf else 720
                    _cam_x_cast = _camp_cast.x - _lw_cast / 2
                    _cam_y_cast = _camp_cast.y - _lh_cast / 2
                    break
                _scale_cast = (self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                               if self.world_surf and self.hud_surf else 1.0)
                _msx_cast, _msy_cast = pygame.mouse.get_pos()
                _mx_cast = _msx_cast * _scale_cast
                _my_cast = _msy_cast * _scale_cast
                _ddx_cast = _mx_cast - (_pos_cast.x - _cam_x_cast)
                _ddy_cast = _my_cast - (_pos_cast.y - _cam_y_cast)
                _dlen_cast = math.hypot(_ddx_cast, _ddy_cast) or 1.0
                _dir_x, _dir_y = _ddx_cast / _dlen_cast, _ddy_cast / _dlen_cast

            self._net.send(_MT.CAST_SKILL, {
                "sid":   skill.skill_id,
                "tid":   _tid_server,
                "dir_x": _dir_x,
                "dir_y": _dir_y,
                "rage":  _rage_pre,
                "mana":  _mana_pre,
            })

        # Spells com cast_time: cria SpellCast visual_only para exibir a barra de cast.
        # Servidor processa o efeito real; cliente remove o SpellCast ao encher sem disparar.
        if _has_cast and _is_online:
            from engine.components import SpellCast as _SCVis
            from content.skill_config import SKILL_CATALOG as _SC_int
            _sc_tid    = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _sc_cast   = getattr(skill, "cast_time", 0.0)
            _skill_cat = _SC_int.get(skill.skill_id, {})
            _interruptible = _skill_cat.get("interruptible", True)
            # Aplica redução de cast_time ANTES de decidir criar SpellCast.
            # Se a redução zerar o tempo (ex: Nova Congelante com 5pts Precisão Elemental),
            # a skill vira instantânea: sem barra de cast e sem SpellCast interruptível.
            # Criar SpellCast com cast_time=0 + interruptible=True causava cancelamento
            # imediato quando o player estava em movimento, bloqueando som e efeito.
            _ct_red_attr = _skill_cat.get("cast_time_reduction_attr", "")
            if _ct_red_attr and _cs:
                _sc_cast = max(0.0, _sc_cast - getattr(_cs, _ct_red_attr, 0.0))
            # Chama Interna: proc ativo → BdF instantânea, sem barra de cast
            if skill.skill_id == "bola_de_fogo" and _char and getattr(_char, "fire_instant_ready", False):
                _sc_cast = 0.0
                _char.fire_instant_ready = False  # consome proc (servidor já consumiu a sua cópia)
            if _sc_cast > 0.0:
                self.world.add_component(self.player_entity_id, _SCVis(
                    spell_id     = skill.skill_id,
                    cast_time    = _sc_cast,
                    elapsed      = 0.0,
                    target_id    = _sc_tid,
                    mana_cost    = 0,
                    visual_only  = True,
                    interruptible= _interruptible,
                ))
                if combat_state:
                    combat_state.is_casting = True

        # Interceptar: executa animação de dash localmente (client-side prediction)
        # O servidor confirma a posição final via ENTITY_MOVE; se coincidir, não interrompe.
        if skill.skill_id == "interceptar" and _tile_move_sk and combat_state:
            self._interceptar_dash_visual(combat_state, _tile_move_sk)

        # Fatiador de Corpos: seta timer local para a animação de channeling (hotbar overlay).
        # Ticks reais são processados no servidor; o cliente só usa o timer para o visual.
        # _fatiador_aoe_tick local não causa dano (mobs online não têm CombatStats).
        if skill.skill_id == "fatiador_de_corpos" and _is_online and _char:
            _fat_p = getattr(skill, "params", {}) or {}
            _char.fatiador_timer = _fat_p.get("duration",      5.0)
            _char.fatiador_tick  = _fat_p.get("tick_interval", 1.0)

        # Previne auto-attack no mesmo frame que a skill: se o timer já zerou, dá um
        # mínimo de 1 frame (0.05s) para que PlayerInputSystem não dispare o auto-attack
        # simultaneamente, evitando o som de ataque sobreposto com o da skill.
        if _cs and _cs.attack_cooldown_timer <= 0:
            _cs.attack_cooldown_timer = 0.05

        # Skills sem cast (instantâneas) já causaram dano de forma síncrona — conta
        # aqui. Skills com cast (ex: Bola de Fogo) só confirmam dano depois, via
        # SKILL_RESULT(is_proj_damage) — contadas em
        # network_handlers.py::_handle_msg_skill_result, não aqui (ativação ≠ dano).
        if skill.skill_id and not _has_cast:
            from engine.components import TrainingDummy as _TDsk_vo
            _qf_target_vo = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _on_dummy_vo = (_qf_target_vo != -1
                           and self.world.get_component(_qf_target_vo, _TDsk_vo) is not None)
            quest_fire("use_skill", skill_id=skill.skill_id, on_dummy=_on_dummy_vo)

        return True

    def _interceptar_dash_visual(self, combat_state, tile_move) -> None:
        """Anima o dash do Interceptar localmente, sem verificações de HP (servidor já validou).

        Replica a MESMA validação de caminho do handler autoritativo
        (_skill_interceptar/_dash_path_clear, skill_handlers.py) — sem isso, a
        predição local tocava a animação mesmo com obstáculo entre o player e o
        destino, e o servidor rejeitava depois (desync: cliente "no destino",
        servidor na posição antiga — ataques seguintes falham por range).
        O servidor continua autoritativo (correção via skill_rejected cobre
        qualquer divergência restante); isto só evita o caso comum visível.
        """
        target_id = getattr(combat_state, "target_entity_id", -1)
        if target_id == -1:
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm:
            return
        tx, ty = target_tm.current_tile_x, target_tm.current_tile_y
        px, py = tile_move.current_tile_x, tile_move.current_tile_y
        # Tile adjacente mais próximo do player
        adj = [(tx + dx, ty + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        walkable = [t for t in adj if is_tile_walkable(self.player_entity_id, t[0], t[1])]
        if not walkable:
            return
        dest_x, dest_y = min(walkable, key=lambda t: abs(t[0] - px) + abs(t[1] - py))
        if not self._dash_path_clear(px, py, dest_x, dest_y):
            return
        player_pos = self.world.get_component(self.player_entity_id, Position)
        new_px = dest_x * TILE_SIZE + TILE_SIZE / 2
        new_py = dest_y * TILE_SIZE + TILE_SIZE / 2
        if isinstance(player_pos, Position):
            tile_move.start_pixel_x = player_pos.x
            tile_move.start_pixel_y = player_pos.y
        tile_move.target_pixel_x = new_px
        tile_move.target_pixel_y = new_py
        tile_move.target_tile_x  = dest_x
        tile_move.target_tile_y  = dest_y
        tile_move.progress       = 0.0
        tile_move.move_duration  = self.INTERCEPT_DURATION
        tile_move.is_moving      = True
        tile_move.is_dash        = True
        auto = self.world.get_component(self.player_entity_id, PlayerAutoMove)
        if auto:
            auto.active        = False
            auto.path          = []
            auto.ground_target = None

    # ------------------------------------------------------------------
    def _resolve_target(self, combat_state: "CombatState", tile_move: "TileMovement",
                        _max_range: int = 1) -> int:
        """
        Retorna o target_entity_id válido do combat_state.
        Se não houver alvo selecionado (ou alvo morto), seleciona o inimigo mais próximo
        (igual ao comportamento da tecla Espaço) e entra em combate automaticamente.
        """
        if combat_state is None or tile_move is None:
            return -1
        current = combat_state.target_entity_id
        if current != -1:
            # Alvo já selecionado também precisa estar dentro de _max_range —
            # sem isso, um target_entity_id setado a partir do "tid" que o
            # CLIENTE manda em CAST_SKILL (server/skill_processor.py) deixava
            # QUALQUER skill acertar QUALQUER entidade do mapa, porque esse
            # check só rodava no fallback de auto-seleção abaixo, nunca pro
            # alvo já setado (ver arquitetura/PROBLEMAS_ARQUITETURA.md,
            # vulnerabilidade de range/LOS de skill). Fora de range cai pro
            # mesmo fallback de auto-seleção usado quando o alvo está morto.
            _cur_tm = self.world.get_component(current, TileMovement)
            _in_range = (_max_range <= 0 or (_cur_tm is not None and chebyshev(
                tile_move.current_tile_x, tile_move.current_tile_y,
                _cur_tm.current_tile_x,   _cur_tm.current_tile_y) <= _max_range))
            if _in_range:
                cs = self.world.get_component(current, CombatStats)
                if cs and cs.current_hp > 0:
                    return current
                # Alvo é player remoto (RemoteControlled): sem CombatStats local,
                # valida via rc.hp (sincronizado pelo servidor via SKILL_RESULT/STATS_UPDATE).
                if cs is None:
                    from engine.components import RemoteControlled as _RCtgt
                    _rc_tgt = self.world.get_component(current, _RCtgt)
                    if _rc_tgt is not None and _rc_tgt.hp > 0:
                        return current
        # Auto-seleciona o inimigo em range com menor HP (desempate por distância) — B6
        px, py    = tile_move.current_tile_x, tile_move.current_tile_y
        best_id   = -1
        best_dist = float("inf")
        best_hp   = float("inf")
        for eid, epos, _, _, etm, ecs, _ in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats, Visible):
            if ecs.current_hp <= 0:
                continue
            if not self._is_on_screen(epos):
                continue
            d = chebyshev(px, py, etm.current_tile_x, etm.current_tile_y)
            if _max_range > 0 and d > _max_range:
                continue
            if ecs.current_hp < best_hp or (ecs.current_hp == best_hp and d < best_dist):
                best_dist = d
                best_hp   = ecs.current_hp
                best_id   = eid
        # Online mobs don't have CombatStats — fall back to closest visible enemy
        if best_id == -1:
            for eid, epos, _, etm in self.world.get_entities_with(Position, Enemy, TileMovement):
                if self.world.get_component(eid, CombatStats):
                    continue  # already handled above
                if not self.world.get_component(eid, Visible):
                    continue
                if not self._is_on_screen(epos):
                    continue
                d = chebyshev(px, py, etm.current_tile_x, etm.current_tile_y)
                if _max_range > 0 and d > _max_range:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_id   = eid
        # PvP: também considera players remotos (RemoteControlled) como alvos válidos
        if best_id == -1:
            _rc_cls = __import__("engine.components", fromlist=["RemoteControlled"]).RemoteControlled
            for eid, epos, _rc_auto, etm in self.world.get_entities_with(Position, _rc_cls, TileMovement):
                if eid == self.player_entity_id:
                    continue  # não auto-seleciona a si mesmo
                if _rc_auto.hp <= 0:
                    continue  # player morto (corpo) — não é alvo válido
                if not self.world.get_component(eid, Visible):
                    continue
                if not self._is_on_screen(epos):
                    continue
                d = chebyshev(px, py, etm.current_tile_x, etm.current_tile_y)
                if _max_range > 0 and d > _max_range:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_id   = eid
        if best_id != -1:
            combat_state.target_entity_id = best_id
            combat_state.is_pursuing      = True
            enter_combat(combat_state)
        return best_id

    # Todos os handlers (_skill_* e _talent_*) estão em skill_handlers.py via SkillHandlers.

