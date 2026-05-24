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
from fonts import make as _font

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from core_systems import apply_effect, StatusEffectSystem as _CoreStatusEffectSystem

from components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, Modifier, CombatState, PlayerAutoMove, \
                       Projectile, Corpse, Inventory, EnemyTier, Equipment, Wallet, Merchant, \
                       CharacterStats, FogOfWar, Visible, ActiveEffect, StatusEffects, \
                       EnemyAbilities, EnemyAbilitySlot, EntityIdentity, \
                       MobSounds, PendingDeath, XPReward, SpawnZoneOwner, SpawnZone, \
                       PlayerSkills, NPC, ActiveRegen, ConsumableBar, \
                       AoeTargeting
from world import World
from tileset import TILE_SIZE, OBJECT_MAPPING
from utils import chebyshev, start_tile_movement
from damage_calculator import resolve_attack_outcome, calculate_base_damage
from combat_log import LOG
from sound_manager import SOUNDS
from floating_text import FLT, PROC, WARN
from icon_manager import ICONS
from ui_helpers import item_tooltip_lines
from status_effects_data import EFFECT_DEFS
from fov import compute_fov
from loot_tables import roll_loot, roll_mob_loot, roll_coins
from entity_factory import create_corpse, create_enemy
from enemy_abilities_data import ABILITY_DEFS
from merchant_data import SHOPS
import quest_events
from quest_events import fire as quest_fire
from stat_fns import add_modifier, remove_modifier, add_timed_modifier, enter_combat
from talent_data import TALENTS as _TT_DATA_SYS

# Lookup reverso: skill_id → (talent_id, min_points) para verificação de lock no servidor
_TALENT_SKILL_REQ_SYS: dict[str, tuple[str, int]] = {
    td["unlocks_skill"]: (tid, td.get("unlock_at", 1))
    for tid, td in _TT_DATA_SYS.items()
    if td.get("unlocks_skill")
}


# ── Registro de serviços ─────────────────────────────────────────────────────
# Elimina acoplamento direto entre instâncias de System.
# Populado por GameEngine.register_services() após criar os sistemas.
_svc: dict = {}


def register_services(combat=None, pathfinding=None, tile_validation=None) -> None:
    """Registra serviços que qualquer sistema pode chamar sem referência direta."""
    if combat:          _svc['combat']          = combat
    if pathfinding:     _svc['pathfinding']     = pathfinding
    if tile_validation: _svc['tile_validation'] = tile_validation


def deal_damage(attacker_id: int, target_id: int, damage_type: str,
                base_ability_damage: float = 0, apply_armor_reduction: bool = True,
                multiplier: float = 1.0, is_ability: bool = False,
                pre_outcome: str = "") -> tuple:
    """Retorna (dead: bool, outcome: str). outcome = 'hit'|'crit'|'block'|'miss'|'dodge'|'parry'|''."""
    return _svc['combat'].deal_damage(
        attacker_id, target_id, damage_type,
        base_ability_damage, apply_armor_reduction, multiplier, is_ability,
        pre_outcome=pre_outcome)


def find_path(start: tuple, end: tuple, dynamic_obstacles=None,
              max_nodes: int = 300, manhattan_limit=60):
    return _svc['pathfinding'].find_path(
        start, end, dynamic_obstacles, max_nodes, manhattan_limit)


def get_tilemap():
    return _svc['pathfinding']._get_tilemap_component()


def is_tile_walkable(entity_id: int, tx: int, ty: int,
                     from_tx=None, from_ty=None) -> bool:
    return _svc['tile_validation'].is_tile_walkable(entity_id, tx, ty, from_tx, from_ty)


def get_mainhand_weapon(world, entity_id: int):
    from components import Equipment
    eq = world.get_component(entity_id, Equipment)
    return eq.slots.get("mainhand") if eq else None


# ─────────────────────────────────────────────────────────────────────────────


# apply_effect é re-exportado de core_systems (importado no topo do arquivo).
# Mantido aqui para backward compatibility com todos os importadores.


class System:
    """
    Classe base para todos os sistemas ECS.

    Superfícies de render separadas por camada:
      world_surf — render de mundo, escala com zoom (atribuído por game.py a cada frame)
      hud_surf   — render de interface, sempre na resolução nativa da tela

    Ambas são atribuídas pelo GameEngine; sistemas não precisam saber sobre zoom.
    """
    world_surf: "pygame.Surface | None" = None  # escala com zoom
    hud_surf:   "pygame.Surface | None" = None  # sempre nativo

    def update(self, events: list = None, dt: float = 0) -> None:
        pass

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        pass


class PathfindingSystem(System):
    def __init__(self, world: World):
        self.world = world
        self.tilemap_comp = None

    def _get_tilemap_component(self):
        if not self.tilemap_comp:
            for _, tm_comp in self.world.get_entities_with(Tilemap):
                self.tilemap_comp = tm_comp
                break
        return self.tilemap_comp

    def _get_distance_heuristic(self, tile1: tuple[int, int], tile2: tuple[int, int]) -> float:
        # Heurística Euclidiana — admissível com custo √2 para diagonais e 1 para cardinais.
        dx = tile1[0] - tile2[0]
        dy = tile1[1] - tile2[1]
        return math.sqrt(dx * dx + dy * dy)

    def find_path(self, start_tile: tuple[int, int], end_tile: tuple[int, int],
                  dynamic_obstacles: set[tuple[int, int]] = None,
                  max_nodes: int = 300,
                  manhattan_limit: "int | None" = 60) -> list[tuple[int, int]] | None:
        """A* com limite de nós explorados para evitar buscas caras em mapas grandes.

        max_nodes:       número máximo de tiles no closed_set antes de desistir.
        manhattan_limit: distância máxima Manhattan aceita (None = sem limite).
                         Padrão 60 serve o AI de inimigos; ground_move passa None.
        """
        if dynamic_obstacles is None:
            dynamic_obstacles = set()

        # Early-exit: distância Manhattan > limite → sem necessidade de pathfind completo
        _manhattan = abs(end_tile[0] - start_tile[0]) + abs(end_tile[1] - start_tile[1])
        if manhattan_limit is not None and _manhattan > manhattan_limit:
            return None

        tilemap_comp = self._get_tilemap_component()
        if not tilemap_comp:
            return None

        map_width = tilemap_comp.map_width_tiles
        map_height = tilemap_comp.map_height_tiles

        if not (0 <= start_tile[0] < map_width and 0 <= start_tile[1] < map_height and
                0 <= end_tile[0] < map_width and 0 <= end_tile[1] < map_height):
            return None

        # Bounded A*: limita a busca ao retângulo entre start e end + padding
        _PAD = 8
        _bx0 = max(0,          min(start_tile[0], end_tile[0]) - _PAD)
        _by0 = max(0,          min(start_tile[1], end_tile[1]) - _PAD)
        _bx1 = min(map_width  - 1, max(start_tile[0], end_tile[0]) + _PAD)
        _by1 = min(map_height - 1, max(start_tile[1], end_tile[1]) + _PAD)

        closed_set    = set()
        open_set      = []
        open_set_coords = set()   # lookup O(1) em vez da varredura O(n) anterior

        heapq.heappush(open_set, (0, 0, start_tile[0], start_tile[1]))
        open_set_coords.add(start_tile)

        came_from = {}
        g_score = {start_tile: 0}

        while open_set:
            if len(closed_set) >= max_nodes:
                return None

            current_f, current_g, current_x, current_y = heapq.heappop(open_set)
            current_tile = (current_x, current_y)
            open_set_coords.discard(current_tile)

            if current_tile == end_tile:
                path = []
                while current_tile in came_from:
                    path.append(current_tile)
                    current_tile = came_from[current_tile]
                path.reverse()
                return path

            if current_tile in closed_set:
                continue
            closed_set.add(current_tile)

            for ndy in (-1, 0, 1):
                for ndx in (-1, 0, 1):
                    if ndx == 0 and ndy == 0:
                        continue
                    neighbor_x = current_x + ndx
                    neighbor_y = current_y + ndy
                    neighbor_tile = (neighbor_x, neighbor_y)

                    if not (_bx0 <= neighbor_x <= _bx1 and _by0 <= neighbor_y <= _by1):
                        continue
                    if neighbor_tile in closed_set:
                        continue
                    if tilemap_comp.tile_matrix[neighbor_y][neighbor_x].is_solid:
                        continue
                    if neighbor_tile in dynamic_obstacles:
                        continue

                    move_cost = 1.414 if (ndx != 0 and ndy != 0) else 1.0
                    tentative_g = g_score.get(current_tile, float('inf')) + move_cost

                    if tentative_g < g_score.get(neighbor_tile, float('inf')):
                        came_from[neighbor_tile] = current_tile
                        g_score[neighbor_tile]   = tentative_g
                        f = tentative_g + self._get_distance_heuristic(neighbor_tile, end_tile)
                        if neighbor_tile not in open_set_coords:
                            heapq.heappush(open_set, (f, tentative_g, neighbor_x, neighbor_y))
                            open_set_coords.add(neighbor_tile)

        return None


class TileValidationSystem(System):
    def __init__(self, world: World):
        self.world = world
        self.tilemap_comp = None
        # Cache de tiles ocupados: {(tx, ty): entity_id}
        # Atualizado em update() a cada frame; consultado em O(1) por is_tile_walkable.
        self._occupied: dict = {}

    def _get_tilemap_component(self):
        if not self.tilemap_comp:
            for _, tm_comp in self.world.get_entities_with(Tilemap):
                self.tilemap_comp = tm_comp
                break
        return self.tilemap_comp

    def update(self, events: list = None, dt: float = 0) -> None:
        """Reconstrói o cache de tiles ocupados a cada frame."""
        occupied = {}
        for entity_id, tm in self.world.get_entities_with(TileMovement):
            occupied[(tm.current_tile_x, tm.current_tile_y)] = entity_id
            if tm.is_moving:
                occupied[(tm.target_tile_x, tm.target_tile_y)] = entity_id
        self._occupied = occupied

    def is_tile_walkable(self, moving_entity_id: int,
                         target_tile_x: int, target_tile_y: int,
                         from_tile_x: int | None = None,
                         from_tile_y: int | None = None) -> bool:
        """Verifica se o tile destino é acessível para a entidade.

        from_tile_x/from_tile_y (opcional): tile de origem para checar colisão
        direcional e regras de elevação. Sem eles, só a colisão base é checada.
        """
        tilemap_comp = self._get_tilemap_component()
        if not tilemap_comp:
            return False

        if not (0 <= target_tile_x < tilemap_comp.map_width_tiles and
                0 <= target_tile_y < tilemap_comp.map_height_tiles):
            return False

        tile_type = tilemap_comp.tile_matrix[target_tile_y][target_tile_x]
        if tile_type.is_solid:
            return False

        # ── Verificações de sistema de pisos ──────────────────────────────────
        if from_tile_x is not None and from_tile_y is not None:
            entity_tm   = self.world.get_component(moving_entity_id, TileMovement)
            entity_elev = entity_tm.elevation if entity_tm else 0

            dest_trans = getattr(tile_type, "is_transition", False)

            def _tile_is_transition(tx: int, ty: int) -> bool:
                """Verifica se (tx, ty) é um tile de transição.

                Checa:
                1. tile_matrix direto
                2. object_matrix direto (base do sprite)
                3. Sprite multi-tile: a posição pode ser coberta pela área superior de
                   um sprite de transição cujo BASE está em tiles abaixo.
                   Ex: escada 64×96 com base em y=475 cobre y=473, y=474, y=475 —
                   todas são válidas como tiles de transição.
                """
                if not (0 <= tx < tilemap_comp.map_width_tiles and
                        0 <= ty < tilemap_comp.map_height_tiles):
                    return False

                # 1. tile_matrix
                if getattr(tilemap_comp.tile_matrix[ty][tx], "is_transition", False):
                    return True

                obj_rows = tilemap_comp.object_matrix

                # 2. object_matrix: verifica na própria linha, incluindo colunas à esquerda
                #    (para capturar o tile direito de sprites largos com base na coluna esquerda)
                if ty < len(obj_rows):
                    for _dxo2 in range(0, 4):
                        _chk = tx - _dxo2
                        if not (0 <= _chk < tilemap_comp.map_width_tiles):
                            break
                        if _chk >= len(obj_rows[ty]):
                            break
                        _oc2 = obj_rows[ty][_chk]
                        if not _oc2 or _oc2 == ".":
                            continue
                        _ot2 = OBJECT_MAPPING.get(_oc2)
                        if not _ot2 or not getattr(_ot2, "is_transition", False):
                            continue
                        _spr_w2 = getattr(_ot2, "sprite_px_w", 0)
                        _tw2 = max(1, _spr_w2 // TILE_SIZE)
                        if _dxo2 < _tw2:
                            return True

                # 3. Sprite multi-tile: verifica se (tx, ty) está dentro da área de
                #    cobertura de um sprite de transição.
                #    Itera colunas à esquerda (dx_off=0..max_w-1) e tiles abaixo (delta_y)
                #    para sprites que podem ser mais largos que 1 tile.
                max_sprite_h_tiles = 6   # altura máxima razoável em tiles (192px)
                max_sprite_w_tiles = 4   # largura máxima razoável em tiles (128px)
                for dx_off in range(0, max_sprite_w_tiles):
                    check_tx = tx - dx_off
                    if not (0 <= check_tx < tilemap_comp.map_width_tiles):
                        break
                    for delta_y in range(1, max_sprite_h_tiles + 1):
                        base_y = ty + delta_y
                        if base_y >= tilemap_comp.map_height_tiles:
                            break
                        if base_y >= len(obj_rows) or check_tx >= len(obj_rows[base_y]):
                            break
                        base_char = obj_rows[base_y][check_tx]
                        if not base_char or base_char == ".":
                            continue
                        base_tile = OBJECT_MAPPING.get(base_char)
                        if not base_tile or not getattr(base_tile, "is_transition", False):
                            continue
                        spr_h = getattr(base_tile, "sprite_px_h", 0)
                        spr_w = getattr(base_tile, "sprite_px_w", 0)
                        tiles_tall = max(1, spr_h // TILE_SIZE)
                        tiles_wide = max(1, spr_w // TILE_SIZE)
                        # (tx, ty) está dentro da área do sprite?
                        if delta_y < tiles_tall and dx_off < tiles_wide:
                            return True

                return False

            def _dest_elevation_and_flags():
                """Retorna (elevation, passthrough) de dest, consultando tile_matrix e object_matrix."""
                elev  = getattr(tile_type, "elevation", 0)
                pthru = getattr(tile_type, "passthrough", False)
                trans = dest_trans
                # Confirma pelo object_matrix se o tile_matrix pode estar desatualizado
                obj_rows = tilemap_comp.object_matrix
                if target_tile_y < len(obj_rows) and target_tile_x < len(obj_rows[target_tile_y]):
                    obj_char = obj_rows[target_tile_y][target_tile_x]
                    if obj_char and obj_char != ".":
                        obj_tile = OBJECT_MAPPING.get(obj_char)
                        if obj_tile:
                            elev  = getattr(obj_tile, "elevation", elev)
                            pthru = getattr(obj_tile, "passthrough", pthru)
                            trans = getattr(obj_tile, "is_transition", trans)
                return elev, pthru, trans

            dest_elev, dest_pass, dest_trans2 = _dest_elevation_and_flags()
            # Usa _tile_is_transition para o destino também, capturando sprites multi-tile
            dest_trans2 = dest_trans2 or _tile_is_transition(target_tile_x, target_tile_y)
            from_trans  = _tile_is_transition(from_tile_x, from_tile_y)

            # Destino é tile de transição → sempre acessível
            if dest_trans2:
                pass  # permite
            # Vem de tile de transição → pode ir para qualquer piso
            elif from_trans:
                pass  # permite
            # Mesmo piso → ok
            elif dest_elev == entity_elev:
                pass  # permite
            # Piso diferente + passthrough → jogador passa por trás (elevation não muda)
            elif dest_pass:
                pass  # permite (elevation não é alterada ao chegar)
            else:
                return False  # piso diferente, sem passagem

        occupant_id = self._occupied.get((target_tile_x, target_tile_y))
        if occupant_id is None or occupant_id == moving_entity_id:
            return True

        # Tile ocupado — verifica se inimigo tenta andar no tile do player
        if self.world.get_component(moving_entity_id, Enemy) and \
           self.world.get_component(occupant_id, PlayerControlled):
            ai_control = self.world.get_component(moving_entity_id, AIControlled)
            if ai_control:
                ai_control.is_blocked = True
                ai_control.blocked_by_entity_id = occupant_id

        return False


# Novo Sistema: CombatSystem
# Gerencia a lógica de dano, cura, morte e outros aspectos de combate.
class CombatSystem(System):
    """Aplica dano entre entidades e delega processamento de morte para DeathHandlerSystem.

    Constantes de cálculo movidas para damage_calculator.py.
    Lógica de morte (loot, cadáver, XP) movida para DeathHandlerSystem.
    """

    def __init__(self, world: World):
        self.world = world
        self.last_outcome: str = "hit"  # captura o outcome do último deal_damage

    def _get_combat_stats(self, entity_id: int) -> CombatStats | None:
        """Helper para obter o componente CombatStats de uma entidade."""
        return self.world.get_component(entity_id, CombatStats)

    def _get_mainhand_weapon(self, entity_id: int):
        """Retorna o item equipado na mão principal, ou None."""
        equip = self.world.get_component(entity_id, Equipment)
        if equip:
            return equip.slots.get("mainhand")
        return None

    def _calculate_damage(self, attacker_id: int, attacker_stats: CombatStats,
                          damage_type: str, base_ability_damage: float = 0,
                          multiplier: float = 1.0,
                          target_stats: CombatStats = None,
                          extra_crit: float = 0.0,
                          is_ability: bool = False) -> tuple:
        """Calcula dano e resolve a tabela de ataque. Retorna (damage, outcome).

        Delega a matemática pura para damage_calculator.py.
        is_ability=True: pula o miss roll (abilities só podem ser dodged/parried, não missed).
        """
        if target_stats is not None:
            outcome, block_reduction = resolve_attack_outcome(
                attacker_stats, target_stats, damage_type, extra_crit,
                is_ability=is_ability)
        else:
            outcome, block_reduction = 'hit', 0.0

        if outcome in ('miss', 'dodge', 'parry'):
            return 0, outcome

        weapon = self._get_mainhand_weapon(attacker_id)
        total_damage = calculate_base_damage(
            attacker_stats, damage_type, weapon,
            base_ability_damage, multiplier, outcome, block_reduction
        )
        return total_damage, outcome

    def deal_damage(self, attacker_id: int, target_id: int,
                    damage_type: str, base_ability_damage: float = 0,
                    apply_armor_reduction: bool = True,
                    multiplier: float = 1.0,
                    is_ability: bool = False,
                    pre_outcome: str = "") -> tuple:
        """Aplica dano de um atacante a um alvo.
        Retorna (dead: bool, outcome: str).
        outcome = 'hit'|'crit'|'block'|'miss'|'dodge'|'parry'|'immune'|''.
        self.last_outcome mantido para backward compat com código offline.
        """
        attacker_stats = self._get_combat_stats(attacker_id)
        target_stats   = self._get_combat_stats(target_id)
        if not attacker_stats or not target_stats:
            return False, ""
        if target_stats.current_hp <= 0:
            return True, "hit"

        # Imunidade (ex: Bloco de Gelo)
        target_state = self.world.get_component(target_id, CombatState)
        if target_state and target_state.is_immune:
            return False, "immune"

        attacker_is_player = self.world.get_component(attacker_id, PlayerControlled) is not None
        target_is_player   = self.world.get_component(target_id,   PlayerControlled) is not None

        target_pos = self.world.get_component(target_id, Position)
        _tx = target_pos.x if target_pos else 0.0
        _ty = target_pos.y if target_pos else 0.0

        extra_crit = self._extra_crit_bonus(attacker_id, target_id, attacker_is_player)

        if pre_outcome:
            # Outcome pré-rolado (ex: por flechas que já verificaram miss visualmente)
            outcome = pre_outcome
            block_reduction = 0.0
            calculated_damage = calculate_base_damage(
                attacker_stats, damage_type,
                self._get_mainhand_weapon(attacker_id),
                base_ability_damage, multiplier, outcome, block_reduction,
            )
            calculated_damage = self._resolve_damage_modifiers(
                attacker_id, target_id, calculated_damage, outcome,
                apply_armor_reduction, damage_type,
                attacker_is_player, target_is_player,
            )
        else:
            calculated_damage, outcome = self._calculate_damage(
                attacker_id, attacker_stats, damage_type,
                base_ability_damage, multiplier,
                target_stats=target_stats, extra_crit=extra_crit,
                is_ability=is_ability,
            )

        if outcome in ('miss', 'dodge', 'parry'):
            self._emit_avoidance_feedback(outcome, _tx, _ty,
                                          attacker_id, target_id,
                                          attacker_is_player, target_is_player)
            self.last_outcome = outcome   # backward compat offline
            return False, outcome

        final_damage = self._resolve_damage_modifiers(
            attacker_id, target_id, calculated_damage, outcome,
            apply_armor_reduction, damage_type,
            attacker_is_player, target_is_player,
        )
        self.last_outcome = outcome   # backward compat offline + skill results fallback
        target_stats.current_hp -= final_damage

        # Aggro por dano: ataque do player força inimigo a perseguir independente do raio.
        # aggroed_by_damage=True desativa o leash de 5 tiles até o mob chegar perto do player.
        if attacker_is_player:
            _ai = self.world.get_component(target_id, AIControlled)
            if _ai and _ai.state in ("IDLE", "RETURNING"):
                _ms_hit = self.world.get_component(target_id, MobSounds)
                SOUNDS.play_mob_sounds(_ms_hit, "aggro", dedup_key=f"dmg_{target_id}")
                _ai.state              = "CHASING"
                _ai.aggroed_by_damage  = True
                _ai.path_recalc_timer  = 0.0

        is_crit  = outcome == 'crit'
        is_block = outcome == 'block'
        self._emit_hit_feedback(attacker_id, target_id, final_damage, outcome,
                                is_crit, is_block, _tx, _ty,
                                attacker_is_player, target_is_player, is_ability)
        self._apply_on_hit_procs(attacker_id, is_crit, attacker_is_player)

        # Dano quebra Polimorfia e Sono
        _t_sfx = self.world.get_component(target_id, StatusEffects)
        if _t_sfx:
            if _t_sfx.remove("polymorph"):
                FLT.add("Polimorfia quebrada!", _tx, _ty, (160, 80, 200), "small",
                        target_id=target_id)
            # Sono quebra ao tomar dano — cancela also o slow encadeado
            _sleep_eff = _t_sfx.get("sleep")
            if _sleep_eff:
                _sleep_eff.on_expire_effect = ""   # desfaz slow pós-sono
                _t_sfx.remove("sleep")
                FLT.add("Acordou!", _tx, _ty, (200, 200, 100), "small",
                        target_id=target_id)

        # Player recebe dano → entra em combate (impede regen de HP)
        if target_is_player and final_damage > 0:
            _player_cs = self.world.get_component(target_id, CombatState)
            if _player_cs:
                enter_combat(_player_cs)

        # Escudo de Fogo: retaliation em quem atacou o jogador
        if target_is_player and final_damage > 0:
            from components import FireShieldEffect
            _shield = self.world.get_component(target_id, FireShieldEffect)
            if _shield:
                _retaliation = 10 + int(target_stats.spell_power * 0.20)
                _att_cs = self._get_combat_stats(attacker_id)
                _att_pos = self.world.get_component(attacker_id, Position)
                if _att_cs and _att_cs.current_hp > 0:
                    _att_cs.current_hp = max(0, _att_cs.current_hp - _retaliation)
                    if _att_pos:
                        FLT.add(f"-{_retaliation}", _att_pos.x, _att_pos.y,
                                (255, 120, 0), "normal", target_id=attacker_id)
                    if _att_cs.current_hp <= 0 and not self.world.get_component(attacker_id, PendingDeath):
                        self.world.add_component(attacker_id, PendingDeath(killer_entity_id=target_id))

        if target_stats.current_hp <= 0:
            return self._handle_death(target_id, attacker_id), outcome
        return False, outcome

    # ------------------------------------------------------------------
    # Helpers internos de deal_damage
    # ------------------------------------------------------------------

    def _extra_crit_bonus(self, attacker_id: int, target_id: int,
                          attacker_is_player: bool) -> float:
        """Explorador de Fraquezas + Alvo Fácil: bônus de crit contra alvos debuffados."""
        if not attacker_is_player:
            return 0.0
        _cs_ef = self._get_combat_stats(attacker_id)
        if not _cs_ef:
            return 0.0
        extra = 0.0
        # Explorador de Fraquezas (cavaleiro): +15% crit contra alvos com slow
        if _cs_ef.explorador_crit_per_point > 0:
            _sfx_ef = self.world.get_component(target_id, StatusEffects)
            if _sfx_ef and _sfx_ef.has("slow"):
                extra += _cs_ef.explorador_crit_per_point * 0.15
        # Alvo Fácil (arqueiro): +X% crit contra qualquer alvo sob CC
        if _cs_ef.alvo_facil_crit > 0:
            _tgt_cs = self._get_combat_stats(target_id)
            if _tgt_cs and _tgt_cs.is_crowd_controlled:
                extra += _cs_ef.alvo_facil_crit
        return extra

    def _resolve_damage_modifiers(self, attacker_id: int, target_id: int,
                                  base: float, outcome: str,
                                  apply_armor: bool, damage_type: str,
                                  attacker_is_player: bool,
                                  target_is_player: bool) -> int:
        """Aplica armadura, talentos de dano e status effects. Retorna dano final inteiro."""
        dmg = float(base)

        # Armadura — 0.1% redução por ponto; críticos ignoram; teto 99%
        if apply_armor and damage_type in ("physical", "physical_fixed"):
            from damage_calculator import apply_armor_reduction as _aar
            _att_cs_arm = self._get_combat_stats(attacker_id)
            _tgt_cs_arm = self._get_combat_stats(target_id)
            if _att_cs_arm and _tgt_cs_arm:
                dmg = _aar(dmg, _att_cs_arm, _tgt_cs_arm, outcome)

        # Foco Mortal: +8% dano/s debilitado contínuo, máx 40%
        if attacker_is_player:
            _cs_fm = self._get_combat_stats(attacker_id)
            if _cs_fm and _cs_fm.foco_mortal_enabled:
                _tm_fm = self.world.get_component(target_id, TileMovement)
                if _tm_fm and _tm_fm.debilitate_elapsed > 0:
                    foco_bonus = min(0.40, 0.08 * int(_tm_fm.debilitate_elapsed))
                    if foco_bonus > 0:
                        dmg *= (1.0 + foco_bonus)

        # Brado Provocativo: enraivecido recebe +10% / causa +5%
        _sfx_target = self.world.get_component(target_id, StatusEffects)
        if _sfx_target and _sfx_target.has("enraged") and not target_is_player:
            dmg *= 1.10
        _sfx_att = self.world.get_component(attacker_id, StatusEffects)
        if _sfx_att and _sfx_att.has("enraged") and not attacker_is_player:
            dmg *= 1.05

        return max(0, int(dmg))

    def _emit_avoidance_feedback(self, outcome: str, tx: float, ty: float,
                                  attacker_id: int, target_id: int,
                                  attacker_is_player: bool,
                                  target_is_player: bool) -> None:
        """Texto flutuante, log e som para ataques evitados (miss/dodge/parry)."""
        _AVOID = {
            'miss':  ("Errou!",   (220, 220, 100), "small"),
            'dodge': ("Desviou!", (100, 210, 230), "small"),
            'parry': ("Aparou!",  (100, 150, 230), "small"),
        }
        _AVOID_LOG = {
            'miss':  ("Voce errou!",      "Inimigo errou!",      (200, 200, 100)),
            'dodge': ("Inimigo desviou!", "Voce desviou!",       (100, 210, 230)),
            'parry': ("Inimigo aparou!",  "Voce aparou!",        (100, 150, 230)),
        }
        _SND = {
            'miss':  ["combat_miss",  "combat_miss_1",  "combat_miss_2",  "combat_miss_3",  "combat_miss_4"],
            'parry': ["combat_parry", "combat_parry_1", "combat_parry_2", "combat_parry_3", "combat_parry_4"],
            'dodge': ["combat_dodge", "combat_dodge_1", "combat_dodge_2", "combat_dodge_3", "combat_dodge_4"],
        }
        flt_text, flt_color, flt_size = _AVOID[outcome]
        FLT.add(flt_text, tx, ty, flt_color, flt_size, target_id=target_id)
        player_msg, enemy_msg, color = _AVOID_LOG[outcome]
        if attacker_is_player:
            LOG.add(player_msg, color)
        elif target_is_player:
            LOG.add(enemy_msg, color)

        # Arco: miss/dodge são silenciosos (flecha apenas falha — sem clang de espada)
        if attacker_is_player:
            _eq_av = self.world.get_component(attacker_id, Equipment)
            _mh_av = _eq_av.slots.get("mainhand") if _eq_av else None
            if _mh_av and getattr(_mh_av, "subtype", "") == "Bow":
                return
        SOUNDS.play_random(_SND[outcome], channel_group=(10, 11))

    def _emit_hit_feedback(self, attacker_id: int, target_id: int,
                           final_damage: int, outcome: str,
                           is_crit: bool, is_block: bool,
                           tx: float, ty: float,
                           attacker_is_player: bool, target_is_player: bool,
                           is_ability: bool) -> None:
        """Texto flutuante, sons e log para golpes que acertaram."""
        # Texto flutuante
        # Cor base: skills=amarelo, auto-ataque=branco; dano ao player=vermelho
        if is_block:
            FLT.add(f"{final_damage}", tx, ty, (160, 160, 160), "normal", target_id=target_id)
        elif target_is_player:
            FLT.add(f"-{final_damage}", tx, ty, (220, 80, 80),
                    target_id=target_id, is_crit=is_crit)
        elif is_crit:
            color = (255, 220, 50) if is_ability else (255, 255, 255)
            FLT.add(f"{final_damage}", tx, ty, color, target_id=target_id, is_crit=True)
        else:
            color = (255, 220, 0) if is_ability else (220, 220, 220)
            FLT.add(f"{final_damage}", tx, ty, color, "normal", target_id=target_id)

        # Sons
        if is_block:
            SOUNDS.play_random(["combat_block", "combat_block_1", "combat_block_2",
                                 "combat_block_3", "combat_block_4"], channel_group=(10, 11))
        if is_crit:
            if target_is_player:
                SOUNDS.play_emote_get_crit(is_player=True)
            else:
                _ms_crit = self.world.get_component(target_id, MobSounds)
                SOUNDS.play_mob_sounds(_ms_crit, "crit")
                SOUNDS.play_emote_get_crit(is_player=False, mob_sounds_comp=_ms_crit)
        if attacker_is_player and not is_ability:
            # Som de impacto melee — suprime se arco equipado na mainhand
            # (flechas já têm sons próprios via PlayerProjectileSystem)
            _equip_hit = self.world.get_component(attacker_id, Equipment)
            _mh = _equip_hit.slots.get("mainhand") if _equip_hit else None
            _is_bow = _mh is not None and getattr(_mh, "subtype", "") == "Bow"
            if not _is_bow:
                keys = (["hit_crit_1", "hit_crit_2", "hit_crit"] if is_crit
                        else ["hit_normal_1", "hit_normal_2", "hit_normal_3", "hit_normal"])
                SOUNDS.play_random(keys, channel_group=(10, 11))

        # Log
        suffix = " CRITICO!" if is_crit else (" (bloqueado)" if is_block else "")
        if attacker_is_player:
            color = (255, 220, 0) if is_crit else (220, 220, 220)
            LOG.add(f"Voce causou {final_damage}{suffix} de dano.", color)
        elif target_is_player:
            color = (255, 120, 0) if is_crit else (220, 80, 80)
            LOG.add(f"Voce recebeu {final_damage}{suffix} de dano.", color)

    def _apply_on_hit_procs(self, attacker_id: int, is_crit: bool,
                             attacker_is_player: bool) -> None:
        """Procs disparados ao acertar um golpe (ex: Embalo ao dar crit)."""
        if not (is_crit and attacker_is_player):
            return
        _cs2 = self.world.get_component(attacker_id, CharacterStats)
        _combat2 = self._get_combat_stats(attacker_id)
        if _cs2 and _combat2 and _combat2.embalo_on_crit:
            _cs2.embalo_charges += 1
            PROC.add("Embalo!", (255, 200, 60))

    def _handle_death(self, dead_entity_id: int, killer_entity_id: int) -> bool:
        """Diferencia morte de jogador vs inimigo.

        Jogador: fica no world; DeathRespawnSystem detecta hp<=0 e respawna.
        Inimigo: adiciona PendingDeath — DeathHandlerSystem processa no mesmo frame.
        """
        is_player = self.world.get_component(dead_entity_id, PlayerControlled) is not None
        if is_player:
            LOG.add("Voce foi derrotado! Renascendo...", (220, 50, 50))
            return True

        self.world.add_component(dead_entity_id, PendingDeath(killer_entity_id=killer_entity_id))
        return True


class DeathHandlerSystem(System):
    """Processa entidades marcadas com PendingDeath.

    Responsabilidades (antes espalhadas em CombatSystem):
      - Emitir som de morte do mob
      - Rolar loot e moedas
      - Criar cadáver no mundo
      - Enfileirar XP (lido por XPSystem)
      - Enfileirar respawn tradicional (lido por MobRespawnSystem)
      - Conceder carga de Vitória Iminente ao assassino
      - Remover a entidade do world

    XPSystem e MobRespawnSystem leem as filas DESTA classe, não de CombatSystem.
    """

    RESPAWN_TIMERS = {
        "normal": 180.0,
        "elite":  300.0,
        "rare":   3600.0,
        "boss":   18000.0,
    }

    def __init__(self, world: World):
        self.world = world
        self.pending_xp: list       = []   # lido por XPSystem
        self.pending_respawns: list = []   # lido por MobRespawnSystem

    def clear_pending(self) -> None:
        """Limpa todas as filas pendentes — chamado ao trocar de mapa ou respawnar."""
        self.pending_xp.clear()
        self.pending_respawns.clear()

    def update(self, events: list = None, dt: float = 0) -> None:
        to_remove = []
        for entity_id, pd in self.world.get_entities_with(PendingDeath):
            # Entidades que morrem são inimigos (jogador nunca recebe PendingDeath)
            ident     = self.world.get_component(entity_id, EntityIdentity)
            _ms_death = self.world.get_component(entity_id, MobSounds)
            SOUNDS.play_mob_sounds(_ms_death, "death", dedup_key=str(entity_id))

            xp_comp = self.world.get_component(entity_id, XPReward)
            if xp_comp:
                self.pending_xp.append(xp_comp.amount)

            pos       = self.world.get_component(entity_id, Position)
            ai        = self.world.get_component(entity_id, AIControlled)
            tier_comp = self.world.get_component(entity_id, EnemyTier)
            init_pos  = self.world.get_component(entity_id, InitialPosition)

            # Evento de quest: kill
            if ident:
                quest_fire("kill", name=ident.name, race=ident.race, tier=tier_comp.tier if tier_comp else "")

            if pos and ai and tier_comp:
                if ident:
                    loot = roll_mob_loot(ident.name, tier_comp.tier)
                else:
                    enemy_type = "ranged" if ai.is_ranged else "melee"
                    loot = roll_loot(enemy_type, tier_comp.tier)
                coins = roll_coins(tier_comp.tier)

                # Drops condicionais de quests (collect_item)
                if quest_events._quest_system_ref is not None and ident:
                    loot.extend(quest_events._quest_system_ref.get_conditional_loot(
                        ident.name, ident.race))

                # Reciclagem: flechas recuperadas aparecem no loot do cadáver
                _dead_cs = self.world.get_component(entity_id, CombatStats)
                if _dead_cs and _dead_cs.arrows_received > 0:
                    _killer_cs = self.world.get_component(pd.killer_entity_id, CombatStats)
                    if _killer_cs and _killer_cs.arrow_recovery_enabled:
                        _pct       = random.randint(50, 100) / 100.0
                        _recovered = max(1, int(_dead_cs.arrows_received * _pct))
                        _equip_r   = self.world.get_component(pd.killer_entity_id, Equipment)
                        _quiver_r  = _equip_r.slots.get("offhand") if _equip_r else None
                        _atype     = _quiver_r.subtype if _quiver_r and _quiver_r.subtype else "Flecha"
                        if _atype and _recovered > 0:
                            from components import Item as _Item
                            _ret = _Item(
                                name=_atype, item_type="ammo", slot="",
                                rarity="common", value=1,
                                damage_min=getattr(_quiver_r, "damage_min", 0),
                                damage_max=getattr(_quiver_r, "damage_max", 0),
                                max_stack=1000,
                            )
                            _ret.stack = _recovered
                            loot.append(_ret)
                            LOG.add(f"Reciclagem! {_recovered} flechas no loot.", (180, 220, 120))

                sz_owner = self.world.get_component(entity_id, SpawnZoneOwner)
                if sz_owner is not None:
                    zone_comp    = self.world.get_component(sz_owner.zone_entity_id, SpawnZone)
                    corpse_decay = (zone_comp.respawn_cooldown if zone_comp
                                    else self.RESPAWN_TIMERS.get(tier_comp.tier, 180.0))
                    create_corpse(self.world, pos.x, pos.y, loot, coins, decay_time=corpse_decay)
                else:
                    respawn_time = self.RESPAWN_TIMERS.get(tier_comp.tier, 180.0)
                    create_corpse(self.world, pos.x, pos.y, loot, coins, decay_time=respawn_time)
                    spawn_x = int(init_pos.x / TILE_SIZE) if init_pos else int(pos.x / TILE_SIZE)
                    spawn_y = int(init_pos.y / TILE_SIZE) if init_pos else int(pos.y / TILE_SIZE)
                    self.pending_respawns.append({
                        "tile_x":    spawn_x,
                        "tile_y":    spawn_y,
                        "is_ranged": ai.is_ranged,
                        "tier":      tier_comp.tier,
                        "timer":     respawn_time,
                    })

            # Vitória Iminente: carga ao assassino
            killer_skills = self.world.get_component(pd.killer_entity_id, PlayerSkills)
            if killer_skills:
                for sk in killer_skills.skills:
                    if sk is None:
                        continue
                    if sk.skill_id == "vitoria_iminente" and sk.max_charges > 0:
                        if sk.charges < sk.max_charges:
                            sk.charges      = sk.max_charges
                            sk.charge_timer = sk.charge_timeout
                            LOG.add("Vitória Iminente carregada!", (100, 255, 120))
                        break

            to_remove.append(entity_id)

        for entity_id in to_remove:
            self.world.remove_entity(entity_id)


class CombatStateSystem(System):
    """Atualiza timers de CombatState, drena modificadores temporários e dispara procs."""

    def __init__(self, world: World):
        self.world = world

    RAGE_DECAY_AMOUNT   = 5
    RAGE_DECAY_INTERVAL = 3.0  # segundos entre cada decaimento

    def update(self, events: list = None, dt: float = 0) -> None:
        for eid, cs in self.world.get_entities_with(CombatState):
            # Timer de saída de combate
            if cs.in_combat and cs.combat_timer > 0:
                cs.combat_timer -= dt
                if cs.combat_timer <= 0:
                    cs.in_combat = False
                    cs.is_pursuing = False
                    cs.combat_timer = 0.0
            # Timer de stun
            if cs.is_stunned and cs.stun_timer > 0:
                cs.stun_timer -= dt
                if cs.stun_timer <= 0:
                    cs.is_stunned = False
                    cs.stun_timer = 0.0

            # Decay de Rage e regen de Concentração (apenas jogador)
            if self.world.get_component(eid, PlayerControlled) is not None:
                char_stats = self.world.get_component(eid, CharacterStats)
                if char_stats:
                    # Rage decay fora de combate
                    if char_stats.rage > 0:
                        if not cs.in_combat:
                            char_stats.rage_decay_timer += dt
                            if char_stats.rage_decay_timer >= self.RAGE_DECAY_INTERVAL:
                                char_stats.rage_decay_timer -= self.RAGE_DECAY_INTERVAL
                                char_stats.rage = max(0, char_stats.rage - self.RAGE_DECAY_AMOUNT)
                        else:
                            char_stats.rage_decay_timer = 0.0

                    # Camuflagem — tick do timer e restauração ao expirar
                    _cs_cam = self.world.get_component(eid, CombatStats)
                    if _cs_cam and _cs_cam.camouflage_timer > 0:
                        _cs_cam.camouflage_timer -= dt
                        if _cs_cam.camouflage_timer <= 0:
                            _cs_cam.camouflage_timer = 0.0
                            # Restaura cor e velocidade originais
                            _rend_cam = self.world.get_component(eid, Renderable)
                            _char_cam = self.world.get_component(eid, CharacterStats)
                            if _rend_cam and _char_cam:
                                _CLASS_COLORS = {"mago": (80, 80, 220), "arqueiro": (80, 200, 80)}
                                _rend_cam.color  = _CLASS_COLORS.get(_char_cam.class_id, (255, 0, 0))
                                _rend_cam.width  = 24
                                _rend_cam.height = 24
                            if _cs_cam:
                                _cs_cam.camouflage_object = ""
                            # Restaura visibilidade do player
                            _cst_cam = self.world.get_component(eid, CombatState)
                            if _cst_cam:
                                _cst_cam.is_visible = True
                            _tm_cam = self.world.get_component(eid, TileMovement)
                            if _tm_cam:
                                _tm_cam.speed = 110.0   # PLAYER_SPEED original

                    # Buff "Só um Gole" — Concentração grátis + acerto 100%
                    _cs_buff = self.world.get_component(eid, CombatStats)
                    if _cs_buff and _cs_buff.concentration_free_timer > 0:
                        _cs_buff.concentration_free_timer -= dt
                        if _cs_buff.concentration_free_timer <= 0:
                            _cs_buff.concentration_free       = False
                            _cs_buff.concentration_free_timer = 0.0

                    # Calmo e Certeiro — acumula segundos parado, reseta ao mover
                    _cs_stand = self.world.get_component(eid, CombatStats)
                    if _cs_stand and _cs_stand.acerto_per_standing_second > 0:
                        _tm_stand = self.world.get_component(eid, TileMovement)
                        if _tm_stand and _tm_stand.is_moving:
                            _cs_stand.standing_seconds = 0.0
                        else:
                            _cs_stand.standing_seconds += dt

                    # Regen de Concentração — taxa definida em CLASS_MELEE_OVERRIDES
                    if char_stats.max_concentration > 0 and char_stats.concentration < char_stats.max_concentration:
                        _cs_conc = self.world.get_component(eid, CombatStats)
                        if _cs_conc:
                            _tm_conc = self.world.get_component(eid, TileMovement)
                            _moving  = _tm_conc.is_moving if _tm_conc else False
                            _rate    = (_cs_conc.concentration_regen_moving
                                        if _moving else
                                        _cs_conc.concentration_regen_idle)
                            if _rate > 0:
                                char_stats.concentration = min(
                                    char_stats.max_concentration,
                                    char_stats.concentration + _rate * dt,
                                )

            combat_stats = self.world.get_component(eid, CombatStats)

            # HP5 — regeneração fora de combate (jogador e mobs)
            if combat_stats and not cs.in_combat and combat_stats.current_hp > 0:
                if combat_stats.current_hp < combat_stats.max_hp:
                    combat_stats.hp5_timer += dt
                    if combat_stats.hp5_timer >= 5.0:
                        combat_stats.hp5_timer -= 5.0
                        regen = max(1, int(combat_stats.max_hp * combat_stats.hp5))
                        combat_stats.current_hp = min(
                            combat_stats.max_hp,
                            combat_stats.current_hp + regen,
                        )
                else:
                    combat_stats.hp5_timer = 0.0  # HP cheio: zera o timer

            if combat_stats and combat_stats.timed_modifiers:
                for entry in combat_stats.timed_modifiers:
                    entry["timer"] -= dt
                expired = [e for e in combat_stats.timed_modifiers if e["timer"] <= 0]
                for entry in expired:
                    combat_stats.timed_modifiers.remove(entry)
                    remove_modifier(combat_stats, entry["modifier"])
                    LOG.add(f"Efeito '{entry['label']}' expirou.", (160, 160, 160))

            if cs._just_entered_combat:
                cs._just_entered_combat = False
                self._trigger_procs(eid, combat_stats)

    def _trigger_procs(self, entity_id: int, combat_stats: CombatStats) -> None:
        """Rola e aplica procs de itens equipados ao entrar em combate."""
        equip = self.world.get_component(entity_id, Equipment)
        if not equip or not combat_stats:
            return
        for item in equip.slots.values():
            if item and item.proc:
                p = item.proc
                if random.random() < p["chance"]:
                    mod = Modifier(p["attribute"], p["value"])
                    add_timed_modifier(combat_stats, mod, p["duration"], p["label"])
                    LOG.add(
                        f"PROC [{item.name}]: {p['label']}! "
                        f"+{p['value']} {p['attribute']} por {p['duration']:.0f}s.",
                        (255, 210, 60)
                    )


class ProjectileSystem(System):
    """
    Move projéteis em direção ao alvo e aplica dano ao acertar.
    Projéteis são entidades com Position + Projectile.
    """
    HIT_THRESHOLD = 10.0  # pixels para considerar colisão

    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen

    def update(self, events: list = None, dt: float = 0) -> None:
        to_remove = []
        for proj_id, proj_pos, proj in self.world.get_entities_with(Position, Projectile):
            target_pos = self.world.get_component(proj.target_id, Position)
            target_stats = self.world.get_component(proj.target_id, CombatStats)

            # Alvo removido ou morto: descarta projétil
            if not target_pos or (target_stats and target_stats.current_hp <= 0):
                to_remove.append(proj_id)
                continue

            dx = target_pos.x - proj_pos.x
            dy = target_pos.y - proj_pos.y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist <= self.HIT_THRESHOLD:
                # Acertou: aplica dano usando stats do atacante no momento do impacto
                deal_damage(
                    proj.attacker_id, proj.target_id,
                    proj.damage_type
                )
                to_remove.append(proj_id)
            else:
                # Move em direção ao alvo
                step = proj.speed * dt
                proj_pos.x += dx / dist * step
                proj_pos.y += dy / dist * step

        for proj_id in to_remove:
            self.world.remove_entity(proj_id)

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        for _, proj_pos, proj in self.world.get_entities_with(Position, Projectile):
            draw_x = int(proj_pos.x - camera_offset_x)
            draw_y = int(proj_pos.y - camera_offset_y)
            color = proj.color
            if proj.is_arrow:
                # Draw arrow as an oriented 4px-wide line segment
                length = 10
                dx = proj.dir_x * length
                dy = proj.dir_y * length
                x1 = int(draw_x - dx)
                y1 = int(draw_y - dy)
                x2 = int(draw_x + dx)
                y2 = int(draw_y + dy)
                pygame.draw.line(self.world_surf, color, (x1, y1), (x2, y2), 4)
            else:
                pygame.draw.circle(self.world_surf, color, (draw_x, draw_y), 4)


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
        for entity_id, pos, renderable, _, _ in self.world.get_entities_with(
                Position, Renderable, Enemy, Visible):
            hw = renderable.width / 2
            hh = renderable.height / 2
            if (pos.x - hw <= world_x <= pos.x + hw and
                    pos.y - hh <= world_y <= pos.y + hh):
                cs = self.world.get_component(entity_id, CombatStats)
                if not cs or cs.current_hp > 0:
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
                                              __import__("components").Position)
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
                                             __import__("components").CombatState)
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
            from components import PirofagiaAiming as _PA
            if (self.world.get_component(self.player_entity_id, AoeTargeting) or
                    self.world.get_component(self.player_entity_id, _PA)):
                continue

            cam_x, cam_y = self._get_camera_offset()
            scale = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
            world_x = event.pos[0] * scale + cam_x
            world_y = event.pos[1] * scale + cam_y
            target_id = self._enemy_at_world_pos(world_x, world_y)

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
        """Adiciona raiva ao jogador, respeitando o limite máximo."""
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
        """Retorna tiles atualmente ocupados por inimigos (obstáculos dinâmicos para o jogador)."""
        occupied = set()
        for _, tm, _ in self.world.get_entities_with(TileMovement, Enemy):
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
                    # Teclado cancela auto-move mas MANTÉM perseguição — player pode
                    # mover manualmente e continuar atacando se ainda estiver em range
                    if auto_move:
                        auto_move.active = False
                        auto_move.path.clear()
                        auto_move.ground_target = None
                    if is_tile_walkable(
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
        target_cs = self.world.get_component(target_id, CombatStats)

        # Alvo morto ou removido: limpa seleção
        if not target_pos or (target_cs and target_cs.current_hp <= 0):
            combat_state.target_entity_id = -1
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
            # Predição de movimento: quando o alvo está na metade do tile, assume destino.
            # Elimina o delay de "esperar o mob completar o tile" durante kiting.
            # Threshold 0.5 = o alvo já percorreu metade → pathfinding aponta para destino.
            if (target_tm.is_moving and target_tm.progress >= 0.5
                    and (target_tm.target_tile_x != target_tm.current_tile_x
                         or target_tm.target_tile_y != target_tm.current_tile_y)):
                tgt_tile_x, tgt_tile_y = target_tm.target_tile_x, target_tm.target_tile_y
            else:
                tgt_tile_x, tgt_tile_y = target_tm.current_tile_x, target_tm.current_tile_y
        else:
            tgt_tile_x = int(target_pos.x / TILE_SIZE)
            tgt_tile_y = int(target_pos.y / TILE_SIZE)

        pl_tile_x = tile_movement.current_tile_x
        pl_tile_y = tile_movement.current_tile_y
        dist = chebyshev(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)

        char_stats  = self.world.get_component(entity_id, CharacterStats)
        is_mage     = char_stats is not None and char_stats.class_id == "mago"
        is_archer   = char_stats is not None and char_stats.class_id == "arqueiro"

        if is_archer:
            self._process_archer_combat(
                entity_id, position, tile_movement, combat_stats, combat_state,
                auto_move, can_act, target_id, tgt_tile_x, tgt_tile_y, dt)
        elif is_mage:
            pursuit_range = self._mage_attack_range(entity_id)
            if dist <= self.PLAYER_ATTACK_RANGE:
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
            if dist <= self.PLAYER_ATTACK_RANGE:
                # Guerreiro no alcance: ataque físico só se estiver perseguindo (botão direito)
                if auto_move:
                    auto_move.path.clear()
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
            elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
                # Guerreiro fora do alcance: persegue até adjacente
                self._auto_move_step(
                    entity_id, position, tile_movement,
                    pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, auto_move, dt,
                    target_eid=target_id,
                )

    def _mage_attack_range(self, entity_id: int) -> int:
        """Retorna o maior cast_range entre as skills equipadas pelo mago (mínimo 5)."""
        skills = self.world.get_component(entity_id, PlayerSkills)
        if not skills:
            return 5
        max_range = 5
        for s in skills.skills:
            if s is not None and s.cast_range > max_range:
                max_range = s.cast_range
        return max_range

    def _process_archer_combat(self, entity_id, position, tile_movement,
                               combat_stats, combat_state, auto_move,
                               can_act, target_id, tgt_tile_x, tgt_tile_y, dt):
        """Auto-attack ranged do arqueiro: verifica arco+aljava e dispara flecha."""
        pl_tile_x = tile_movement.current_tile_x
        pl_tile_y = tile_movement.current_tile_y
        dist = chebyshev(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)

        equip = self.world.get_component(entity_id, Equipment)
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None

        bow_range = getattr(bow, "cast_range", 8) if bow and getattr(bow, "subtype", "") == "Bow" else 0

        if not bow_range:
            # Sem arco: fallback ao melee guerreiro (soco lento)
            if dist <= self.PLAYER_ATTACK_RANGE:
                if auto_move:
                    auto_move.path.clear()
                if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                    SOUNDS.play_emote_attack(is_player=True)
                    deal_damage(entity_id, target_id, "physical")
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    enter_combat(combat_state)
            elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
                self._auto_move_step(entity_id, position, tile_movement,
                                     pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, auto_move, dt,
                                     target_eid=target_id)
            return

        # Arco equipado — verificar aljava
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                from combat_log import LOG as _LOG
                _LOG.add("Precisa de uma aljava equipada para atirar.", (220, 180, 80))
                combat_stats.attack_cooldown_timer = 1.0  # cooldown de aviso
            return

        _PRE_DRAW_THRESHOLD = 1.0   # segundos antes do disparo para tocar o nock

        if dist <= bow_range:
            if auto_move:
                auto_move.path.clear()
                auto_move.active = False

            # Pré-tensionamento: toca ~1s antes do próximo disparo (uma vez por ciclo)
            if (combat_state.is_pursuing and can_act
                    and 0 < combat_stats.attack_cooldown_timer <= _PRE_DRAW_THRESHOLD
                    and combat_stats.arrow_pre_draw_ready):
                combat_stats.arrow_pre_draw_ready = False
                if random.random() < 0.30:
                    SOUNDS.play_random(["arrow_nock_1", "arrow_nock_2"], channel_group=(6, 7))

            if combat_state.is_pursuing and can_act and combat_stats.attack_cooldown_timer <= 0:
                if quiver.arrow_count <= 0:
                    from combat_log import LOG as _LOG
                    _LOG.add("Aljava vazia! Use Recarregar.", (220, 80, 80))
                    combat_stats.attack_cooldown_timer = 1.0
                    return
                tgt_pos = self.world.get_component(target_id, Position)
                tgt_cs  = self.world.get_component(target_id, CombatStats)
                if not tgt_pos or (tgt_cs and tgt_cs.current_hp <= 0):
                    combat_state.target_entity_id = -1
                    combat_state.is_pursuing = False
                    return
                from components import PlayerProjectile as _PP
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
                    from floating_text import PROC as _PROC_FD
                    _PROC_FD.add("Despadronizada!", (220, 130, 20))
                quiver.arrow_count -= 1
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                combat_stats.arrow_pre_draw_ready  = True   # pronto para o próximo ciclo
                enter_combat(combat_state)
                # Disparo: draw (35%) + release (sempre)
                if random.random() < 0.35:
                    SOUNDS.play_random(["arrow_draw_1", "arrow_draw_2"], channel_group=(8, 9))
                SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))
        elif auto_move and not tile_movement.is_moving:
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
            # Remove todos os tiles do mob alvo (current + target) das obstáculos dinâmicos.
            # Sem isso, quando o mob atravessa uma porta de 1 tile, seu target_tile bloqueia
            # a passagem e o jogador desvia para outra entrada em vez de seguir o mob.
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
                # Melee: tenta todos os tiles adjacentes ao alvo, do mais próximo ao mais distante
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
            if is_tile_walkable(entity_id, nx, ny):
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

        best_eid  = -1
        best_dist = float("inf")
        for eid, epos, _, _, etm, ecs, _ in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats, Visible):
            if ecs.current_hp <= 0:
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


# Modificação no EnemyAISystem para integrar o CombatSystem
class EnemyAISystem(System):
    KITING_MIN_DIST      = 3   # tiles: ranged enemy flees if player is this close
    SLEEP_RADIUS_TILES   = 40  # além desta distância (Chebyshev), a AI é completamente suspensa
    MAX_LEASH_RADIUS     = 20  # tiles: mob retorna ao spawn se afastar mais do que isso (aggro normal)
    MAX_LEASH_RADIUS_DMG = 25  # tiles: raio maior quando aggroed por dano (evita reset por 1 hit + recuo)
    MAX_PATHFINDS_PER_FRAME = 4  # limite de chamadas A* por frame (evita travamento com muitos inimigos)
    # Ranged: kite limitado
    KITE_MAX_TILES       = 3    # máximo de tiles por sessão de kite
    KITE_COOLDOWN        = 1.5  # segundos de pausa entre sessões de kite
    # Ranged: cast e velocidade de ataque
    RANGED_CAST_TIME     = 1.0  # segundos parado antes de disparar o projétil
    RANGED_ATTACK_CD_MULT = 1.3  # multiplicador no cooldown de ataque (velocidade menor)

    # Raças monitoradas pelo debug de ataque (vazio = todas)
    _DBG_ATK_RACES: set[str] = set()  # vazio = debug desativado
    # Intervalo mínimo entre logs por mob (segundos) — evita spam no console
    _DBG_ATK_INTERVAL = 2.0

    def __init__(self, world: World, player_entity_id: int = -1):
        self.world = world
        self.player_entity_id = player_entity_id  # mantido por backward-compat (não usado internamente)
        self.proximity_threshold_pixels = 5.0
        self.proximity_threshold_tiles = 1
        self.path_recalc_interval = 0.8
        self._pathfind_budget = 0  # resetado a cada frame
        self._dbg_atk_timers: dict[int, float] = {}  # eid → tempo acumulado desde último log

    @staticmethod
    def _has_line_of_sight(tilemap_comp, x0: int, y0: int, x1: int, y1: int) -> bool:
        """Bresenham: retorna True se não houver tile sólido entre (x0,y0) e (x1,y1)."""
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        x, y = x0, y0
        sx = 1 if x1 > x0 else -1
        sy = 1 if y1 > y0 else -1
        err = dx - dy
        while True:
            if (x, y) != (x0, y0) and (x, y) != (x1, y1):
                if (0 <= x < tilemap_comp.map_width_tiles and
                        0 <= y < tilemap_comp.map_height_tiles):
                    if tilemap_comp.tile_matrix[y][x].is_solid:
                        return False
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
        return True

    def _get_occupied_tiles(self, except_entity_id: int = None) -> set[tuple[int, int]]:
        occupied_tiles = set()
        for entity_id, tile_move_comp in self.world.get_entities_with(TileMovement):
            if entity_id == except_entity_id:
                continue
            if not tile_move_comp.is_moving:
                occupied_tiles.add((tile_move_comp.current_tile_x, tile_move_comp.current_tile_y))
            else:
                # Se a entidade está se movendo, seu tile futuro também está "ocupado"
                occupied_tiles.add((tile_move_comp.target_tile_x, tile_move_comp.target_tile_y))
        return occupied_tiles

    def _find_path_budgeted(self, start, end, dynamic_obstacles=None):
        """Chama find_path apenas se o budget do frame ainda não foi esgotado."""
        if self._pathfind_budget <= 0:
            return None
        self._pathfind_budget -= 1
        return find_path(start, end, dynamic_obstacles=dynamic_obstacles)

    def _select_target(self, mob_eid: int):
        """Retorna (player_eid, pos, tile_move, combat_stats, combat_state) do alvo mais próximo.

        Prioriza o alvo já agredido via dano (AIControlled.aggroed_by_damage + target_eid).
        Ignora players mortos e invisíveis.
        Retorna (-1, None, None, None, None) se nenhum player válido.
        """
        ai_ctrl = self.world.get_component(mob_eid, AIControlled)
        mob_pos = self.world.get_component(mob_eid, Position)
        if not mob_pos:
            return (-1, None, None, None, None)

        best_eid   = -1
        best_dist  = float("inf")
        best_pos   = None
        best_tm    = None
        best_cs    = None
        best_cst   = None

        for p_eid, p_pos, p_tm, _, p_cs in self.world.get_entities_with(
                Position, TileMovement, PlayerControlled, CombatStats):
            if p_cs.current_hp <= 0:
                continue
            p_cst = self.world.get_component(p_eid, CombatState)
            if p_cst is not None and not p_cst.is_visible:
                continue
            dist_px = math.sqrt((p_pos.x - mob_pos.x) ** 2 + (p_pos.y - mob_pos.y) ** 2)
            if dist_px < best_dist:
                best_dist  = dist_px
                best_eid   = p_eid
                best_pos   = p_pos
                best_tm    = p_tm
                best_cs    = p_cs
                best_cst   = p_cst

        return (best_eid, best_pos, best_tm, best_cs, best_cst)

    def update(self, events: list = None, dt: float = 0) -> None:
        self._pathfind_budget = self.MAX_PATHFINDS_PER_FRAME

        # Verifica se há pelo menos um player vivo; caso contrário, todos os mobs ficam ociosos.
        any_player_alive = False
        for _, _, _, _, _p_cs in self.world.get_entities_with(
                Position, TileMovement, PlayerControlled, CombatStats):
            if _p_cs.current_hp > 0:
                any_player_alive = True
                break

        if not any_player_alive:
            for _, ai_control, tile_movement, combat_stats in self.world.get_entities_with(AIControlled, TileMovement, CombatStats):
                if not tile_movement.is_moving:
                    ai_control.state = "IDLE"
                ai_control.is_blocked = False
                ai_control.blocked_by_entity_id = -1
                if combat_stats.attack_cooldown_timer > 0:
                    combat_stats.attack_cooldown_timer -= dt
            return

        all_occupied_tiles = self._get_occupied_tiles()

        for enemy_id, enemy_pos, ai_control, initial_pos, detect_radius, tile_movement, enemy_combat_stats in \
            self.world.get_entities_with(Position, AIControlled, InitialPosition, DetectionRadius, TileMovement, CombatStats):

            # Inimigo morto? Pula!
            if enemy_combat_stats.current_hp <= 0:
                continue

            enemy_current_tile_x = tile_movement.current_tile_x
            enemy_current_tile_y = tile_movement.current_tile_y
            current_enemy_tile   = (enemy_current_tile_x, enemy_current_tile_y)

            # --- Seleciona alvo para este mob (N-player support) ---
            target_eid, player_position_comp, player_tile_move_comp, player_combat_stats, _target_cst = \
                self._select_target(enemy_id)

            # Se mob tem aggro fixo por dano e esse alvo ainda é válido, mantém.
            if ai_control.aggroed_by_damage and ai_control.target_eid != -1:
                _fx_pos = self.world.get_component(ai_control.target_eid, Position)
                _fx_tm  = self.world.get_component(ai_control.target_eid, TileMovement)
                _fx_cs  = self.world.get_component(ai_control.target_eid, CombatStats)
                _fx_cst = self.world.get_component(ai_control.target_eid, CombatState)
                _fx_pc  = self.world.get_component(ai_control.target_eid, PlayerControlled)
                if _fx_pos and _fx_tm and _fx_cs and _fx_cs.current_hp > 0 and _fx_pc:
                    _invis = _fx_cst is not None and not _fx_cst.is_visible
                    if not _invis:
                        target_eid           = ai_control.target_eid
                        player_position_comp = _fx_pos
                        player_tile_move_comp = _fx_tm
                        player_combat_stats  = _fx_cs
                        _target_cst          = _fx_cst

            # Sem alvo válido → grace period antes de ir pro IDLE
            # Evita ciclo IDLE → AGGRO_DELAY (1s) por perda momentânea de alvo (1-2 ticks)
            if target_eid == -1 or player_position_comp is None:
                _was_in_combat = ai_control.state in ("CHASING", "ATTACKING", "AGGRO_DELAY")
                if _was_in_combat:
                    ai_control.target_lost_timer += dt
                    if ai_control.target_lost_timer < 0.6:  # 600ms de grace
                        if enemy_combat_stats.attack_cooldown_timer > 0:
                            enemy_combat_stats.attack_cooldown_timer -= dt
                        continue  # ainda não vai pro IDLE
                # Grace expirou ou mob já estava IDLE/RETURNING
                if not tile_movement.is_moving:
                    ai_control.state = "IDLE"
                ai_control.target_eid = -1
                ai_control.target_lost_timer = 0.0
                if enemy_combat_stats.attack_cooldown_timer > 0:
                    enemy_combat_stats.attack_cooldown_timer -= dt
                continue

            # Persiste o alvo no componente
            ai_control.target_eid = target_eid
            ai_control.target_lost_timer = 0.0  # alvo encontrado → reset grace

            player_current_tile_x = player_tile_move_comp.current_tile_x
            player_current_tile_y = player_tile_move_comp.current_tile_y

            # --- Invisibilidade do alvo atual ---
            _player_invisible = _target_cst is not None and not _target_cst.is_visible
            if _player_invisible:
                if ai_control.state in ("CHASING", "ATTACKING", "AGGRO_DELAY"):
                    ai_control.state              = "RETURNING"
                    ai_control.aggroed_by_damage  = False
                    ai_control.path_recalc_timer  = 0.0
                    ai_control.ranged_cast_timer  = 0.0
                    ai_control.target_eid         = -1
                if ai_control.state != "RETURNING":
                    continue

            # --- Efeitos de estado (stun / sleep / fear / root) ---
            _sfx = self.world.get_component(enemy_id, StatusEffects)
            if _sfx:
                if _sfx.has("stun") or _sfx.has("sleep"):
                    enemy_combat_stats.attack_cooldown_timer = max(
                        enemy_combat_stats.attack_cooldown_timer, 0.1)
                    continue  # imóvel e sem ataque
                if _sfx.has("polymorph") or _sfx.has("disoriented"):
                    # Desorientado: anda aleatoriamente a 40% da velocidade normal
                    enemy_combat_stats.attack_cooldown_timer = max(
                        enemy_combat_stats.attack_cooldown_timer, 0.1)
                    if not tile_movement.is_moving:
                        ex, ey = tile_movement.current_tile_x, tile_movement.current_tile_y
                        dirs = [(0,1),(0,-1),(1,0),(-1,0),(1,1),(1,-1),(-1,1),(-1,-1)]
                        random.shuffle(dirs)
                        for ddx, ddy in dirs:
                            fx, fy = ex + ddx, ey + ddy
                            if is_tile_walkable(enemy_id, fx, fy):
                                start_tile_movement(enemy_pos, tile_movement,
                                                    fx, fy, extra_speed_mult=0.4)
                                break
                    continue  # não ataca enquanto polimorfizado
                if _sfx.has("fear"):
                    # Foge do player: move para o tile oposto
                    if not tile_movement.is_moving:
                        ex, ey = tile_movement.current_tile_x, tile_movement.current_tile_y
                        dx_raw = ex - player_current_tile_x
                        dy_raw = ey - player_current_tile_y
                        step_x = (1 if dx_raw > 0 else -1) if dx_raw != 0 else 0
                        step_y = (1 if dy_raw > 0 else -1) if dy_raw != 0 else 0
                        for fx, fy in [(ex + step_x, ey + step_y),
                                       (ex + step_x, ey),
                                       (ex, ey + step_y)]:
                            if is_tile_walkable(enemy_id, fx, fy):
                                tile_movement.target_tile_x  = fx
                                tile_movement.target_tile_y  = fy
                                tile_movement.target_pixel_x = fx * TILE_SIZE + TILE_SIZE / 2
                                tile_movement.target_pixel_y = fy * TILE_SIZE + TILE_SIZE / 2
                                tile_movement.start_pixel_x  = enemy_pos.x
                                tile_movement.start_pixel_y  = enemy_pos.y
                                tile_movement.progress       = 0.0
                                tile_movement.is_moving      = True
                                break
                    continue  # não ataca enquanto com medo
                if _sfx.has("root"):
                    # Enraizado: pode atacar mas não se move
                    if tile_movement.is_moving:
                        tile_movement.is_moving = False
                        tile_movement.target_tile_x = tile_movement.current_tile_x
                        tile_movement.target_tile_y = tile_movement.current_tile_y
                    ai_control.path = None
                    ai_control.path_recalc_timer = 0.0
                    # Permite continuar para lógica de ataque (não dá continue aqui)

            # --- Sleep zone: mob dorme se NENHUM player estiver a menos de 40 tiles ---
            # Usa Chebyshev (sem sqrt) para eficiência máxima.
            chebyshev_dist_to_player = max(
                abs(player_current_tile_x - enemy_current_tile_x),
                abs(player_current_tile_y - enemy_current_tile_y)
            )
            # Verifica todos os players — acorda se qualquer um estiver próximo
            _min_cheb = chebyshev_dist_to_player
            for _, _ptm, _ in self.world.get_entities_with(TileMovement, PlayerControlled):
                _d = max(abs(_ptm.current_tile_x - enemy_current_tile_x),
                         abs(_ptm.current_tile_y - enemy_current_tile_y))
                if _d < _min_cheb:
                    _min_cheb = _d
            if _min_cheb > self.SLEEP_RADIUS_TILES:
                if ai_control.state != "IDLE":
                    ai_control.state = "IDLE"
                    ai_control.path = None
                continue

            # Atualiza o cooldown de ataque do inimigo
            if enemy_combat_stats.attack_cooldown_timer > 0:
                enemy_combat_stats.attack_cooldown_timer -= dt

            # Atualiza timers de Disengage (Hunter)
            if ai_control.disengage_cd > 0:
                ai_control.disengage_cd -= dt
            if ai_control.disengage_boost > 0:
                ai_control.disengage_boost -= dt

            # Cooldown de kite (ranged): ao expirar, reseta contador de tiles
            if ai_control.kite_cooldown > 0:
                ai_control.kite_cooldown -= dt
                if ai_control.kite_cooldown <= 0:
                    ai_control.kite_tiles_moved = 0

            # Se o inimigo está se movendo, não faz nada além de atualizar cooldown
            if tile_movement.is_moving:
                ai_control.is_blocked = False
                ai_control.blocked_by_entity_id = -1
                # Debug: mob em estado ATTACKING mas ainda em movimento → não ataca neste tick
                continue

            ai_control.is_blocked = False
            ai_control.blocked_by_entity_id = -1

            dist_to_player_pixels = math.sqrt(
                (player_position_comp.x - enemy_pos.x)**2 +
                (player_position_comp.y - enemy_pos.y)**2
            )

            # chebyshev_dist_to_player já calculado acima no sleep check
            not_same_tile = current_enemy_tile != (player_current_tile_x, player_current_tile_y)
            in_attack_range = (
                not_same_tile and
                1 <= chebyshev_dist_to_player <= ai_control.attack_range_tiles
            )

            # --- Ataque (separado do movimento) ---
            damage_type_to_use = "magical" if (
                enemy_combat_stats.spell_power > 0 or enemy_combat_stats.base_magical_damage > 0
            ) else "physical"

            _ms_atk = self.world.get_component(enemy_id, MobSounds)
            _caster_classes = {"Mage", "Mago", "Warlock", "Bruxo"}
            if ai_control.entity_class in _caster_classes:
                _atk_event = "attack_magic"
            elif ai_control.is_ranged:
                _atk_event = "attack_ranged"
            else:
                _atk_event = "attack_melee"

            # ── Ranged: cast timer (fica parado 1s antes de disparar) ──────
            if not _player_invisible and ai_control.is_ranged and ai_control.ranged_cast_timer > 0:
                _cast_tm  = get_tilemap()
                _cast_los = (_cast_tm is None or self._has_line_of_sight(
                    _cast_tm,
                    enemy_current_tile_x, enemy_current_tile_y,
                    player_current_tile_x, player_current_tile_y
                ))
                if in_attack_range and _cast_los:
                    ai_control.ranged_cast_timer -= dt
                    if ai_control.ranged_cast_timer <= 0:
                        # Cast concluído: dispara contra o alvo deste mob
                        ai_control.ranged_cast_timer = 0.0
                        SOUNDS.play_emote_attack(is_player=False, mob_sounds_comp=_ms_atk)
                        SOUNDS.play_mob_sounds(_ms_atk, _atk_event, dedup_key=str(enemy_id))
                        self._spawn_projectile(enemy_id, ai_control.target_eid, damage_type_to_use)
                        enemy_combat_stats.attack_cooldown_timer = (
                            enemy_combat_stats.get_attack_cooldown() * self.RANGED_ATTACK_CD_MULT
                        )
                else:
                    ai_control.ranged_cast_timer = 0.0  # LOS/alcance perdido: cancela

            # ── Debug de ataque ──────────────────────────────────────────────
            # ATAQUE_OK: sem rate-limit — cada disparo real é logado com timestamp.
            # Estado bloqueado: rate-limited a cada _DBG_ATK_INTERVAL s para evitar spam.
            if self._DBG_ATK_RACES and ai_control.state == "ATTACKING":
                _dbg_id2 = self.world.get_component(enemy_id, EntityIdentity)
                if _dbg_id2 and _dbg_id2.name in self._DBG_ATK_RACES:
                    import time as _time
                    _ts = _time.strftime("%H:%M:%S") + f".{int(_time.time() * 1000) % 1000:03d}"
                    _cd_now = enemy_combat_stats.attack_cooldown_timer
                    _attack_fires = (
                        not _player_invisible and in_attack_range and _cd_now <= 0
                    )
                    if _attack_fires:
                        # Sempre loga quando o ataque dispara de fato
                        print(f"[MOB-ATK] {_ts} eid={enemy_id} → ATAQUE_OK  "
                              f"cd={_cd_now:.3f} cheb={chebyshev_dist_to_player}")
                        self._dbg_atk_timers[enemy_id] = 0.0  # reseta rate-limit
                    else:
                        # Estado bloqueado: rate-limited
                        _dbg_t2 = self._dbg_atk_timers.get(enemy_id, 0.0)
                        if _dbg_t2 <= 0:
                            _bloq = []
                            if _player_invisible:       _bloq.append("invisivel")
                            if not in_attack_range:     _bloq.append(f"fora_range(cheb={chebyshev_dist_to_player})")
                            if _cd_now > 0:             _bloq.append(f"cd={_cd_now:.2f}s")
                            if tile_movement.is_moving: _bloq.append("moving")
                            print(f"[MOB-ATK] {_ts} eid={enemy_id} → bloqueado: {'/'.join(_bloq) or '?'}")
                            self._dbg_atk_timers[enemy_id] = self._DBG_ATK_INTERVAL
                        else:
                            self._dbg_atk_timers[enemy_id] = _dbg_t2 - dt

            # ── Inicia ataque (cooldown expirou) ──────────────────────────
            if not _player_invisible and in_attack_range and enemy_combat_stats.attack_cooldown_timer <= 0:
                if ai_control.is_ranged:
                    # Só inicia cast se não estiver já carregando
                    if ai_control.ranged_cast_timer == 0.0:
                        _atk_tm  = get_tilemap()
                        _atk_los = (_atk_tm is None or self._has_line_of_sight(
                            _atk_tm,
                            enemy_current_tile_x, enemy_current_tile_y,
                            player_current_tile_x, player_current_tile_y
                        ))
                        if _atk_los:
                            ai_control.ranged_cast_timer = self.RANGED_CAST_TIME
                else:
                    SOUNDS.play_emote_attack(is_player=False, mob_sounds_comp=_ms_atk)
                    SOUNDS.play_mob_sounds(_ms_atk, _atk_event, dedup_key=str(enemy_id))
                    deal_damage(
                        attacker_id=enemy_id,
                        target_id=ai_control.target_eid,
                        damage_type=damage_type_to_use
                    )
                    enemy_combat_stats.attack_cooldown_timer = enemy_combat_stats.get_attack_cooldown()

            _is_rooted = _sfx is not None and _sfx.has("root")
            if _is_rooted:
                continue  # pode atacar já foi processado acima; só bloqueia movimento

            # --- Hunter Disengage: dash 4 tiles ao se sentir encurralado ---
            if (ai_control.entity_class == "Hunter" and
                    chebyshev_dist_to_player <= 3 and
                    ai_control.disengage_cd <= 0 and
                    dist_to_player_pixels <= detect_radius.radius):
                ai_control.disengage_cd = 8.0
                ai_control.disengage_boost = 3.0
                ai_control.state = "KITING"
                ai_control.path = None  # força recalculo imediato
                self._do_kiting(
                    enemy_id, enemy_pos, ai_control, tile_movement,
                    enemy_current_tile_x, enemy_current_tile_y,
                    player_current_tile_x, player_current_tile_y,
                    all_occupied_tiles, dt
                )
                continue

            # --- Decisão de movimento ---
            needs_to_kite = (
                ai_control.is_ranged and
                chebyshev_dist_to_player < self.KITING_MIN_DIST and
                dist_to_player_pixels <= detect_radius.radius and
                ai_control.kite_cooldown <= 0  # respeita pausa entre sessões de kite
            )

            if not _player_invisible and needs_to_kite:
                # Ranged muito perto: recua para manter distância ideal
                ai_control.state = "KITING"
                ai_control.ranged_cast_timer = 0.0  # cancela cast em andamento
                self._do_kiting(
                    enemy_id, enemy_pos, ai_control, tile_movement,
                    enemy_current_tile_x, enemy_current_tile_y,
                    player_current_tile_x, player_current_tile_y,
                    all_occupied_tiles, dt
                )
                continue

            if not _player_invisible and in_attack_range and not needs_to_kite:
                # Melee em alcance, ou ranged a boa distância: fica parado
                ai_control.state = "ATTACKING"
                continue

            player_tile_now = (player_current_tile_x, player_current_tile_y)
            should_recalculate_path = (
                ai_control.path is None or
                not ai_control.path or
                ai_control.path_recalc_timer <= 0 or
                ai_control.is_blocked or
                ai_control.last_known_player_tile != player_tile_now
            )

            # --- Perseguição do Jogador ---
            in_detect_range = dist_to_player_pixels <= detect_radius.radius

            # Leash: mob saiu da área do spawn → volta independente de onde o player está.
            # Calcula distância Chebyshev da posição ATUAL do mob até seu ponto de SPAWN,
            # não até o player — evita perseguição infinita quando player foge.
            # aggroed_by_damage usa raio maior (25 tiles) para não resetar por 1 hit + recuo.
            _aggro_range_px = 5 * TILE_SIZE
            _spawn_tile_x = int(initial_pos.x / TILE_SIZE)
            _spawn_tile_y = int(initial_pos.y / TILE_SIZE)
            _dist_from_spawn = chebyshev(
                tile_movement.current_tile_x, tile_movement.current_tile_y,
                _spawn_tile_x, _spawn_tile_y
            )
            _leash_radius = (self.MAX_LEASH_RADIUS_DMG
                             if ai_control.aggroed_by_damage
                             else self.MAX_LEASH_RADIUS)

            if ai_control.state in ("CHASING", "ATTACKING") and \
                    _dist_from_spawn > _leash_radius:
                ai_control.state              = "RETURNING"
                ai_control.aggroed_by_damage  = False
                ai_control.path_recalc_timer  = 0.0
                ai_control.target_eid         = -1
                tile_movement.path            = []

            # Quando o mob aggroed_by_damage chega perto do player (range normal),
            # transiciona para aggro de proximidade normal
            if ai_control.aggroed_by_damage and dist_to_player_pixels <= _aggro_range_px:
                ai_control.aggroed_by_damage = False

            # Detecção inicial: apenas mobs IDLE, e somente se o player estiver visível
            if not _player_invisible and dist_to_player_pixels <= _aggro_range_px and ai_control.state == "IDLE":
                _tilemap_for_los = get_tilemap()
                _has_los = (
                    _tilemap_for_los is None or
                    self._has_line_of_sight(
                        _tilemap_for_los,
                        enemy_current_tile_x, enemy_current_tile_y,
                        player_current_tile_x, player_current_tile_y
                    )
                )
                if _has_los:
                    _ms_aggro = self.world.get_component(enemy_id, MobSounds)
                    SOUNDS.play_mob_sounds(_ms_aggro, "aggro", dedup_key=str(enemy_id))
                    ai_control.state       = "AGGRO_DELAY"
                    ai_control.aggro_delay = 1.0

            # Inimigo perseguindo/atacando → alvo entra em combate (só se visível)
            if not _player_invisible and ai_control.state in ("CHASING", "ATTACKING", "AGGRO_DELAY"):
                if _target_cst and not _target_cst.in_combat:
                    enter_combat(_target_cst)

            # Perseguição ativa — CHASING persiste mesmo fora do detect_radius (ex: agro por dano)
            if ai_control.state not in ("IDLE", "RETURNING"):
                if ai_control.state == "AGGRO_DELAY":
                    ai_control.aggro_delay -= dt
                    if ai_control.aggro_delay <= 0:
                        ai_control.state = "CHASING"
                        # Jitter: escala o 1º pathfind para não bater com outros inimigos
                        ai_control.path_recalc_timer = random.uniform(0.0, 0.4)
                ai_control.path_recalc_timer -= dt

                # Durante o delay de aggro o mob fica parado
                if ai_control.state == "AGGRO_DELAY":
                    continue

                if should_recalculate_path:
                    ai_control.is_blocked = False

                    # Reutiliza all_occupied_tiles (já calculado uma vez no topo do frame)
                    # removendo o próprio inimigo para não bloquear a si mesmo
                    dynamic_obstacles_for_pathfinding = all_occupied_tiles - {
                        (tile_movement.current_tile_x, tile_movement.current_tile_y),
                        (tile_movement.target_tile_x,  tile_movement.target_tile_y),
                    }
                    
                    potential_attack_tiles = []
                    # Itera por uma área ao redor do player para encontrar tiles no alcance de ataque.
                    # A área de busca deve ser pelo menos attack_range_tiles + 1 para ter opções.
                    search_grid_radius = ai_control.attack_range_tiles + 1 
                    
                    for dy_offset in range(-search_grid_radius, search_grid_radius + 1):
                        for dx_offset in range(-search_grid_radius, search_grid_radius + 1):
                            target_tile_around_player_x = player_current_tile_x + dx_offset
                            target_tile_around_player_y = player_current_tile_y + dy_offset
                            
                            dist_x_to_player = abs(player_current_tile_x - target_tile_around_player_x)
                            dist_y_to_player = abs(player_current_tile_y - target_tile_around_player_y)
                            
                            chebyshev_dist_from_target_to_player = max(dist_x_to_player, dist_y_to_player)

                            is_in_desired_range = False
                            if ai_control.attack_range_tiles == 1 and chebyshev_dist_from_target_to_player == 1:
                                is_in_desired_range = True
                            elif ai_control.attack_range_tiles > 1 and \
                                 0 < chebyshev_dist_from_target_to_player <= ai_control.attack_range_tiles:
                                is_in_desired_range = True


                            if is_in_desired_range:
                                tilemap_comp = get_tilemap()
                                if tilemap_comp and \
                                   (0 <= target_tile_around_player_x < tilemap_comp.map_width_tiles and
                                    0 <= target_tile_around_player_y < tilemap_comp.map_height_tiles) and \
                                   not tilemap_comp.tile_matrix[target_tile_around_player_y][target_tile_around_player_x].is_solid:
                                    
                                    potential_attack_tiles.append((target_tile_around_player_x, target_tile_around_player_y))
                    
                    available_attack_tiles = [
                        tile for tile in potential_attack_tiles
                        if tile not in all_occupied_tiles
                    ]
                    
                    # Ordena os tiles de ataque potenciais pelo mais próximo ao inimigo
                    available_attack_tiles.sort(key=lambda t: abs(t[0] - enemy_current_tile_x) + abs(t[1] - enemy_current_tile_y))

                    found_path_to_target = False
                    if not available_attack_tiles:
                        ai_control.path = None
                    else:
                        for target_path_x, target_path_y in available_attack_tiles:
                            ai_control.path = self._find_path_budgeted(
                                current_enemy_tile,
                                (target_path_x, target_path_y),
                                dynamic_obstacles=dynamic_obstacles_for_pathfinding
                            )
                            if ai_control.path:
                                found_path_to_target = True
                                break
                        
                        if not found_path_to_target:
                            ai_control.path = None

                    ai_control.path_recalc_timer = self.path_recalc_interval
                    ai_control.last_known_player_tile = player_tile_now
                
                if ai_control.path and not ai_control.is_blocked:
                    next_tile_on_path_x, next_tile_on_path_y = ai_control.path[0]

                    if is_tile_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y):
                        start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                        ai_control.path.pop(0)
                    else:
                        ai_control.path = None
                        ai_control.path_recalc_timer = 0.0
                elif ai_control.is_blocked:
                    ai_control.state = "BLOCKED_BY_PLAYER"
                else:
                    # Sem caminho disponível: só vai para IDLE se o jogador saiu do raio de detecção.
                    # Se ainda estiver em alcance (ex: kite_cooldown ativo), mantém CHASING para
                    # que o pathfinding seja tentado novamente no próximo ciclo.
                    if dist_to_player_pixels > detect_radius.radius:
                        ai_control.state = "IDLE"

            # --- Retorno à Posição Inicial ---
            else: # Comportamento de retorno à posição inicial
                ai_control.path = None
                ai_control.target_eid = -1
                initial_tile_x = int(initial_pos.x / TILE_SIZE)
                initial_tile_y = int(initial_pos.y / TILE_SIZE)

                dist_to_initial_tiles = abs(initial_tile_x - enemy_current_tile_x) + abs(initial_tile_y - enemy_current_tile_y)

                if dist_to_initial_tiles > self.proximity_threshold_tiles:
                    ai_control.state = "RETURNING"
                    if should_recalculate_path:
                        dynamic_obstacles_for_return = self._get_occupied_tiles(except_entity_id=enemy_id)
                        
                        ai_control.path = self._find_path_budgeted(
                            current_enemy_tile,
                            (initial_tile_x, initial_tile_y),
                            dynamic_obstacles=dynamic_obstacles_for_return
                        )
                        ai_control.path_recalc_timer = self.path_recalc_interval

                    if ai_control.path and not ai_control.is_blocked:
                        next_tile_on_path_x, next_tile_on_path_y = ai_control.path[0]
                        if is_tile_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y):
                            start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                            ai_control.path.pop(0)
                        else:
                            ai_control.path = None
                            ai_control.path_recalc_timer = 0.0
                    else:
                        ai_control.state = "IDLE"
                else:
                    ai_control.state             = "IDLE"
                    ai_control.aggroed_by_damage = False
                    enemy_pos.x = initial_pos.x
                    enemy_pos.y = initial_pos.y
            
            # Garante que a posição pixel da entidade esteja alinhada ao tile quando está parada.
            if not tile_movement.is_moving and ai_control.state == "IDLE":
                enemy_pos.x = enemy_current_tile_x * TILE_SIZE + TILE_SIZE / 2
                enemy_pos.y = enemy_current_tile_y * TILE_SIZE + TILE_SIZE / 2

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _spawn_projectile(self, attacker_id: int, target_id: int, damage_type: str):
        """Cria uma entidade de projétil na posição do atacante em direção ao alvo."""
        attacker_pos = self.world.get_component(attacker_id, Position)
        if not attacker_pos:
            return

        # Cor e tipo de projétil por classe — data-driven via PROJECTILE_BY_CLASS
        from mob_definitions import PROJECTILE_BY_CLASS as _PBC
        ai_ctrl      = self.world.get_component(attacker_id, AIControlled)
        entity_class = ai_ctrl.entity_class if ai_ctrl else ""
        _proj_data   = _PBC.get(entity_class, _PBC["_default"])
        proj_color   = _proj_data["color"]
        is_arrow     = _proj_data["is_arrow"]

        # Direção inicial (para renderizar flecha orientada corretamente)
        dir_x, dir_y = 1.0, 0.0
        target_pos = self.world.get_component(target_id, Position)
        if target_pos:
            dx = target_pos.x - attacker_pos.x
            dy = target_pos.y - attacker_pos.y
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > 0:
                dir_x, dir_y = dx / dist, dy / dist

        proj_entity = self.world.create_entity()
        self.world.add_component(
            proj_entity, Position(x=attacker_pos.x, y=attacker_pos.y,
                                  prev_x=attacker_pos.x, prev_y=attacker_pos.y))
        self.world.add_component(
            proj_entity, Projectile(attacker_id=attacker_id, target_id=target_id,
                                    damage_type=damage_type, speed=380.0,
                                    color=proj_color, is_arrow=is_arrow,
                                    dir_x=dir_x, dir_y=dir_y))

    def _do_kiting(self, enemy_id, enemy_pos, ai_control, tile_movement,
                   ex, ey, px, py, all_occupied_tiles, dt):
        """Move inimigo ranged para longe do jogador, mantendo distância de ataque."""
        ai_control.path_recalc_timer -= dt
        current_tile = (ex, ey)
        attack_range = ai_control.attack_range_tiles

        if not ai_control.path or ai_control.path_recalc_timer <= 0:
            tilemap_comp = get_tilemap()
            if not tilemap_comp:
                return

            # Direção de fuga: afasta do player
            dir_x = ex - px
            dir_y = ey - py

            # Candidatos: tiles a exatamente attack_range de Chebyshev do player
            candidates = []
            r = attack_range
            for dy in range(-r - 1, r + 2):
                for dx in range(-r - 1, r + 2):
                    tx, ty = px + dx, py + dy
                    if not (0 <= tx < tilemap_comp.map_width_tiles and
                            0 <= ty < tilemap_comp.map_height_tiles):
                        continue
                    if tilemap_comp.tile_matrix[ty][tx].is_solid:
                        continue
                    cdist = chebyshev(tx, ty, px, py)
                    if cdist != attack_range:
                        continue
                    if (tx, ty) in all_occupied_tiles and (tx, ty) != current_tile:
                        continue
                    # Prefere tiles na direção de fuga (dot product com dir_fuga)
                    dot = (tx - px) * dir_x + (ty - py) * dir_y
                    candidates.append((dot, tx, ty))

            # Ordena: maior dot primeiro (mais "longe" na direção de fuga)
            candidates.sort(reverse=True)

            ai_control.path = None
            dynamic_obs = self._get_occupied_tiles(except_entity_id=enemy_id)
            for _, tx, ty in candidates[:4]:  # tenta os 4 melhores
                path = self._find_path_budgeted(current_tile, (tx, ty),
                                                dynamic_obstacles=dynamic_obs)
                if path:
                    ai_control.path = path
                    break
            ai_control.path_recalc_timer = self.path_recalc_interval

        # Para após 3 tiles kitados nesta sessão
        if ai_control.kite_tiles_moved >= self.KITE_MAX_TILES:
            ai_control.kite_cooldown    = self.KITE_COOLDOWN
            ai_control.kite_tiles_moved = 0
            ai_control.path             = None
            return

        if ai_control.path:
            nx, ny = ai_control.path[0]
            if is_tile_walkable(enemy_id, nx, ny):
                boost = 2.0 if ai_control.disengage_boost > 0 else 1.0
                start_tile_movement(enemy_pos, tile_movement, nx, ny, extra_speed_mult=boost)
                ai_control.path.pop(0)
                ai_control.kite_tiles_moved += 1
            else:
                ai_control.path = None
                ai_control.path_recalc_timer = 0.0


class TileMovementSystem(System):
    FOOTSTEP_INTERVAL = 0.20  # segundos mínimos entre passos

    def __init__(self, world: World):
        self.world = world
        self._footstep_timer: float = 0.0

    def update(self, events: list = None, dt: float = 0) -> None:
        self._footstep_timer = max(0.0, self._footstep_timer - dt)
        for entity_id, position, tile_movement in self.world.get_entities_with(Position, TileMovement):
            if tile_movement.is_moving:
                # Emite rastro antes de mover (posição atual do frame)
                if tile_movement.is_dash:
                    from floating_text import DASH_TRAIL
                    _rend = self.world.get_component(entity_id, Renderable)
                    _w = _rend.width  if _rend else 24
                    _h = _rend.height if _rend else 24
                    DASH_TRAIL.emit(position.x, position.y, _w, _h)

                tile_movement.progress += dt / tile_movement.move_duration
                tile_movement.progress = min(tile_movement.progress, 1.0)

                position.x = tile_movement.start_pixel_x + (tile_movement.target_pixel_x - tile_movement.start_pixel_x) * tile_movement.progress
                position.y = tile_movement.start_pixel_y + (tile_movement.target_pixel_y - tile_movement.start_pixel_y) * tile_movement.progress

                if tile_movement.progress >= 1.0:
                    position.x = tile_movement.target_pixel_x
                    position.y = tile_movement.target_pixel_y
                    tile_movement.current_tile_x = tile_movement.target_tile_x
                    tile_movement.current_tile_y = tile_movement.target_tile_y
                    tile_movement.is_moving = False

                    # Atualiza elevation ao chegar num tile:
                    # • tile de transição ("t"): elevation NÃO muda aqui —
                    #   muda ao SAIR do "t" para um tile de piso N.
                    # • tile com passthrough: elevation NÃO muda (jogador passa por baixo).
                    # • tile normal com elevation N: elevation ASSUME N.
                    _tm_comp = None
                    for _, _tc in self.world.get_entities_with(Tilemap):
                        _tm_comp = _tc
                        break
                    if _tm_comp:
                        _tx = tile_movement.current_tile_x
                        _ty = tile_movement.current_tile_y
                        if (0 <= _ty < _tm_comp.map_height_tiles and
                                0 <= _tx < _tm_comp.map_width_tiles):
                            _arrived = _tm_comp.tile_matrix[_ty][_tx]
                            _is_trans = getattr(_arrived, "is_transition", False)
                            _passthru = getattr(_arrived, "passthrough", False)
                            _elev     = getattr(_arrived, "elevation", 0)
                            # Fallback 1: object_matrix no próprio tile
                            _obj_rows = _tm_comp.object_matrix
                            if _ty < len(_obj_rows) and _tx < len(_obj_rows[_ty]):
                                _oc = _obj_rows[_ty][_tx]
                                if _oc and _oc != ".":
                                    _ot = OBJECT_MAPPING.get(_oc)
                                    if _ot:
                                        _is_trans = getattr(_ot, "is_transition", _is_trans)
                                        _passthru = getattr(_ot, "passthrough", _passthru)
                                        _elev     = getattr(_ot, "elevation", _elev)
                            # Fallback 2: sprite multi-tile com suporte a largura.
                            if not _is_trans:
                                for _dxo in range(0, 4):   # até 4 tiles de largura
                                    if _is_trans:
                                        break
                                    _ctx = _tx - _dxo
                                    if not (0 <= _ctx < _tm_comp.map_width_tiles):
                                        break
                                    for _dy in range(1, 7):
                                        _by = _ty + _dy
                                        if _by >= _tm_comp.map_height_tiles or _by >= len(_obj_rows):
                                            break
                                        if _ctx >= len(_obj_rows[_by]):
                                            break
                                        _bc = _obj_rows[_by][_ctx]
                                        if not _bc or _bc == ".":
                                            continue
                                        _bt = OBJECT_MAPPING.get(_bc)
                                        if not _bt or not getattr(_bt, "is_transition", False):
                                            continue
                                        _spr_h = getattr(_bt, "sprite_px_h", 0)
                                        _spr_w = getattr(_bt, "sprite_px_w", 0)
                                        _tt = max(1, _spr_h // TILE_SIZE)
                                        _tw = max(1, _spr_w // TILE_SIZE)
                                        if _dy < _tt and _dxo < _tw:
                                            _is_trans = True
                                            break
                            if not _is_trans and not _passthru:
                                tile_movement.elevation = _elev
                    tile_movement.is_dash   = False
                    tile_movement.progress  = 0.0
                    # Som de passo — apenas player, sem dash, respeitando intervalo mínimo
                    if (not tile_movement.is_dash
                            and self._footstep_timer <= 0
                            and self.world.get_component(entity_id, PlayerControlled) is not None):
                        SOUNDS.play_footstep()
                        self._footstep_timer = self.FOOTSTEP_INTERVAL
            # debilitate_elapsed: acumula tempo enquanto sob slow (Foco Mortal)
            _sfx_tm = self.world.get_component(entity_id, StatusEffects)
            if _sfx_tm and _sfx_tm.has("slow"):
                tile_movement.debilitate_elapsed += dt
            else:
                tile_movement.debilitate_elapsed = 0.0


class RenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen

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
            if combat_stats and combat_stats.current_hp <= 0:
                continue
            # Oculta entidades fora do campo de visão (jogador nunca é oculto)
            if _fog_visible is not None:
                is_player = self.world.get_component(entity_id, PlayerControlled) is not None
                if not is_player:
                    etx = int(position.x / TILE_SIZE)
                    ety = int(position.y / TILE_SIZE)
                    if (etx, ety) not in _fog_visible:
                        continue
            foot_y = position.y + renderable.height / 2
            drawables.append((foot_y, "entity", entity_id, position, renderable, combat_stats))

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
            _, _, entity_id, position, renderable, combat_stats = item

            draw_x = position.x - camera_offset_x
            draw_y = position.y - camera_offset_y

            _sfx_rnd = self.world.get_component(entity_id, StatusEffects)
            _polymorphed = _sfx_rnd is not None and _sfx_rnd.has("polymorph")

            # ── Camuflagem: desenha sprite do objeto do tileset ───────────────
            _cam_obj = getattr(combat_stats, "camouflage_object", "") if combat_stats else ""
            if _cam_obj:
                from tileset import get_camouflage_sprite
                _cam_sprite = get_camouflage_sprite(_cam_obj)
                if _cam_sprite:
                    _sw = _cam_sprite.get_width()   # 32
                    _sh = _cam_sprite.get_height()  # 32 ou 64
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
                pygame.draw.rect(self.world_surf, renderable.color, rect)

            if entity_id == target_id:
                pygame.draw.rect(self.world_surf, (255, 220, 0), rect, 2)

            if combat_stats and combat_stats.max_hp > 0:
                ratio = max(0.0, combat_stats.current_hp / combat_stats.max_hp)
                bar_w = renderable.width
                bar_h = 4
                bar_x = int(draw_x - renderable.width / 2)
                bar_y = int(draw_y - renderable.height / 2) - 7
                pygame.draw.rect(self.world_surf, (80, 0, 0), (bar_x, bar_y, bar_w, bar_h))
                pygame.draw.rect(self.world_surf, (0, 200, 60), (bar_x, bar_y, int(bar_w * ratio), bar_h))

                _sfx = self.world.get_component(entity_id, StatusEffects)
                _cst = self.world.get_component(entity_id, CombatState)

                # Reúne efeitos ativos
                _active_effects = list(_sfx.effects.values()) if _sfx else []
                if _cst and _cst.is_stunned and _cst.stun_timer > 0:
                    if not (_sfx and _sfx.has("stun")):
                        class _FakeEff:
                            effect_type = "stun"
                        _active_effects.append(_FakeEff())

                if _active_effects:
                    from effect_animator import get_frame as _get_effect_frame
                    from status_effects_data import EFFECT_DEFS as _EDEFS

                    _anim_frames = []   # (Surface, effect_type) — com animação
                    _sq_colors   = []   # (R,G,B)               — sem animação

                    for _eff in _active_effects:
                        _frame = _get_effect_frame(_eff.effect_type)
                        if _frame is not None:
                            _anim_frames.append(_frame)
                        else:
                            _defn = _EDEFS.get(_eff.effect_type)
                            if _defn:
                                _sq_colors.append(_defn.color)

                    # ── Frames animados: centralizados acima da barra de HP ──
                    if _anim_frames:
                        from effect_animator import FRAME_W, FRAME_H
                        _gap_f = 4
                        _total_w = len(_anim_frames) * FRAME_W + (_gap_f * (len(_anim_frames) - 1))
                        _fx = int(draw_x - _total_w / 2)
                        _fy = bar_y - FRAME_H - 4
                        for _surf in _anim_frames:
                            self.world_surf.blit(_surf, (_fx, _fy))
                            _fx += FRAME_W + _gap_f

                    # ── Quadrados coloridos para efeitos sem animação ─────────
                    if _sq_colors:
                        _isz, _gap = 6, 2
                        _tw = len(_sq_colors) * (_isz + _gap) - _gap
                        _ix = int(draw_x - _tw / 2)
                        # Fica abaixo dos frames animados (ou na posição padrão)
                        _sq_offset = (FRAME_H + 6) if _anim_frames else 0
                        _iy = bar_y - _isz - 2 - _sq_offset
                        for _col in _sq_colors:
                            pygame.draw.rect(self.world_surf, _col, (_ix, _iy, _isz, _isz))
                            _ix += _isz + _gap

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
        from tile_sprite_manager import TILE_SPRITES as _TS
        from tileset import TILE_MAPPING as _TM, FLOOR_TILE as _FT
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

            # int() mantém offset monotônico (sem jitter ao desacelerar)
            cam_x = int(camera_offset_x)
            cam_y = int(camera_offset_y)

            # Tile de origem (top-left) e offset sub-tile dentro do tile
            tile_ox = cam_x // tile_size
            tile_oy = cam_y // tile_size
            sub_x   = cam_x - tile_ox * tile_size
            sub_y   = cam_y - tile_oy * tile_size

            # Tiles necessários para cobrir a tela + 1 coluna/linha de borda
            tiles_w = self.world_surf.get_width()  // tile_size + 2
            tiles_h = self.world_surf.get_height() // tile_size + 2

            # Reconstrói cache apenas quando o tile de origem muda
            if (tile_ox != self._cache_tile_x
                    or tile_oy != self._cache_tile_y
                    or tiles_w != self._cache_tiles_w
                    or tiles_h != self._cache_tiles_h
                    or self._cache_surf is None):

                surf_w = tiles_w * tile_size
                surf_h = tiles_h * tile_size
                if (self._cache_surf is None
                        or self._cache_surf.get_width()  != surf_w
                        or self._cache_surf.get_height() != surf_h):
                    self._cache_surf = pygame.Surface((surf_w, surf_h))

                TILE_SPRITES = self._tile_sprites
                _TM          = self._tile_mapping
                _FT          = self._floor_tile
                rows          = tilemap_comp.tile_matrix
                terrain_rows  = tilemap_comp.terrain_matrix
                vis_rows      = tilemap_comp.terrain_visual  # sheet visual overrides
                obj_rows      = tilemap_comp.object_matrix   # world objects layer
                map_h  = tilemap_comp.map_height_tiles
                map_w  = tilemap_comp.map_width_tiles
                for ty in range(tiles_h):
                    for tx in range(tiles_w):
                        rx, ry = tile_ox + tx, tile_oy + ty
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
                                # Sheet terrain override — always highest priority
                                spr = TILE_SPRITES.get_raw_sprite(vis_id)
                                if spr is not None:
                                    self._cache_surf.blit(spr, dest[:2])
                                else:
                                    t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                                    pygame.draw.rect(self._cache_surf, _TM.get(t_char, _FT).color, dest)
                            elif has_obj or getattr(tile_type, "sprite_px_h", 0) > 0:
                                # Tile with a world object or multi-tile sprite upper cell:
                                # draw terrain background so the object renders cleanly on top
                                t_char = terrain_rows[ry][rx] if ry < len(terrain_rows) and rx < len(terrain_rows[ry]) else "G"
                                pygame.draw.rect(self._cache_surf, _TM.get(t_char, _FT).color, dest)
                            elif tile_type.overlay_height > 0:
                                pygame.draw.rect(self._cache_surf, tile_type.color, dest)
                            else:
                                sprite = TILE_SPRITES.get(tile_type, rx, ry)
                                if sprite is not None:
                                    self._cache_surf.blit(sprite, dest[:2])
                                else:
                                    pygame.draw.rect(self._cache_surf, tile_type.color, dest)
                        else:
                            pygame.draw.rect(self._cache_surf, (0, 0, 0), dest)

                self._cache_tile_x  = tile_ox
                self._cache_tile_y  = tile_oy
                self._cache_tiles_w = tiles_w
                self._cache_tiles_h = tiles_h

            # 1 blit por frame — ~0.1ms ao invés de ~880 draw.rect
            self.world_surf.blit(self._cache_surf, (-sub_x, -sub_y))

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
        from tile_sprite_manager import TILE_SPRITES
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
        from tile_sprite_manager import TILE_SPRITES
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


class StatusEffectSystem(_CoreStatusEffectSystem, System):
    """
    Versão cliente do StatusEffectSystem.
    Herda toda a lógica ECS de _CoreStatusEffectSystem e adiciona floating text.

    _CoreStatusEffectSystem (core_systems.py) é a fonte de verdade — sem Pygame.
    Esta subclasse só acrescenta FLT.add() nos hooks visuais.
    """

    def __init__(self, world: World) -> None:
        _CoreStatusEffectSystem.__init__(self, world)

    def _emit_damage(self, eid: int, amount: int, effect_type: str,
                     pos, color: tuple) -> None:
        if pos:
            FLT.add(f"-{amount}", pos.x, pos.y, color, size="normal", target_id=eid)

    def _emit_heal(self, eid: int, amount: int, effect_type: str,
                   pos, color: tuple) -> None:
        if pos:
            FLT.add(f"+{amount}", pos.x, pos.y, color, size="normal", target_id=eid)


class EnemyAbilitySystem(System):
    """
    Gerencia o uso de habilidades especiais de inimigos.

    Responsabilidades:
      - Decrementar cooldowns de EnemyAbilitySlot por dt.
      - Disparar habilidades quando o inimigo está em combate e no alcance.
      - Aplicar efeitos no jogador via apply_effect().

    Não contém dados de habilidade — lê de ABILITY_DEFS (enemy_abilities_data.py).
    Não lida com IA de movimento — isso é EnemyAISystem.
    """

    def __init__(self, world: World, player_entity_id: int = -1) -> None:
        self.world             = world
        self.player_entity_id  = player_entity_id  # mantido por backward-compat

    def update(self, events: list = None, dt: float = 0) -> None:
        # Constrói um mapa rápido eid→(px, py) de todos os players vivos
        player_tiles: dict[int, tuple[int, int]] = {}
        for p_eid, p_tm, _, p_cs in self.world.get_entities_with(
                TileMovement, PlayerControlled, CombatStats):
            if p_cs.current_hp > 0:
                player_tiles[p_eid] = (p_tm.current_tile_x, p_tm.current_tile_y)

        if not player_tiles:
            return

        for eid, abilities, etm, ecs in self.world.get_entities_with(
                EnemyAbilities, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue

            # Só age se o inimigo está em combate ativo
            ai = self.world.get_component(eid, AIControlled)
            if not ai or ai.state not in ("ATTACKING", "CHASING"):
                continue

            # Inimigo atordoado não usa habilidades
            sfx = self.world.get_component(eid, StatusEffects)
            if sfx and sfx.has("stun"):
                continue

            ex, ey = etm.current_tile_x, etm.current_tile_y

            # Determina o alvo: usa target_eid do AIControlled se válido, senão player mais próximo
            target_p_eid = ai.target_eid if ai.target_eid in player_tiles else -1
            if target_p_eid == -1:
                # Fallback: player mais próximo
                best_dist = float("inf")
                for p_eid, (px, py) in player_tiles.items():
                    d = max(abs(ex - px), abs(ey - py))
                    if d < best_dist:
                        best_dist   = d
                        target_p_eid = p_eid
            if target_p_eid == -1:
                continue

            px, py = player_tiles[target_p_eid]
            dist   = max(abs(ex - px), abs(ey - py))  # Chebyshev

            for slot in abilities.slots:
                # Tick de cooldown
                if slot.current_cooldown > 0:
                    slot.current_cooldown -= dt
                    continue

                defn = ABILITY_DEFS.get(slot.ability_id)
                if not defn or dist > defn.range_tiles:
                    continue

                # Aplica o efeito no alvo
                apply_effect(
                    self.world, target_p_eid,
                    defn.effect_type, defn.duration, defn.magnitude,
                    tick_interval=defn.tick_interval,
                )
                slot.current_cooldown = slot.cooldown

                # Feedback visual e no log
                PROC.add(defn.name, (220, 80, 180))

                ident = self.world.get_component(eid, EntityIdentity)
                mob_name = ident.name if ident else "Inimigo"
                LOG.add(f"{mob_name} usou {defn.name}!", (220, 80, 180))


class CorpseSystem(System):
    """Decrementa timers de cadáveres e remove os que expiraram."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events: list = None, dt: float = 0) -> None:
        to_remove = []
        for entity_id, corpse in self.world.get_entities_with(Corpse):
            corpse.timer -= dt
            if corpse.timer <= 0:
                to_remove.append(entity_id)
        for eid in to_remove:
            self.world.remove_entity(eid)


class SpawnZoneSystem(System):
    """
    Gerencia zonas de spawn (SpawnZone).
    - Detecta inimigos mortos e decrementa o contador de vivos.
    - Quando vivos < max_count e o timer expirou, spawna um novo inimigo
      em um tile caminhável aleatório dentro do raio da zona.
    - Inimigos spawnadoss recebem SpawnZoneOwner para que _handle_death
      não os adicione à fila de respawn tradicional.
    """

    # Zonas além desta distância (Chebyshev em tiles) do player são pausadas.
    ACTIVATION_RADIUS   = 80
    # Máximo de entidades criadas por frame (globalmente entre todas as zonas)
    MAX_SPAWNS_PER_FRAME = 2

    def __init__(self, world: World):
        self.world = world
        # Fila de spawns pendentes: (zone_eid, zone, tile_x, tile_y)
        self._spawn_queue: list = []

    def update(self, events=None, dt: float = 0) -> None:
        # Posição do player para culling de zonas distantes
        player_tx, player_ty = 0, 0
        for _, ptm, _ in self.world.get_entities_with(TileMovement, PlayerControlled):
            player_tx = ptm.current_tile_x
            player_ty = ptm.current_tile_y
            break

        # Tiles ocupados (evita spawnar em cima de outra entidade)
        occupied: set = set()
        for _, tm in self.world.get_entities_with(TileMovement):
            occupied.add((tm.current_tile_x, tm.current_tile_y))
            if tm.is_moving:
                occupied.add((tm.target_tile_x, tm.target_tile_y))

        # Tilemap para checar solidez
        tilemap_comp = None
        for _, tc in self.world.get_entities_with(Tilemap):
            tilemap_comp = tc
            break

        for zone_eid, zone in self.world.get_entities_with(SpawnZone):
            # Pula zonas fora do raio de ativação — preserva timers, não spawna
            dist = chebyshev(zone.center_x, zone.center_y, player_tx, player_ty)
            if dist > self.ACTIVATION_RADIUS:
                continue
            # Remove IDs de inimigos que foram deletados do world
            before = len(zone.active_entity_ids)
            zone.active_entity_ids = {
                eid for eid in zone.active_entity_ids
                if self.world.get_component(eid, Enemy) is not None
            }
            alive = len(zone.active_entity_ids)

            # Cada morte enfileira um timer independente
            deaths = before - alive
            for _ in range(deaths):
                zone.respawn_timers.append(zone.respawn_cooldown)

            # --- Preenchimento inicial: escalonar com timers (evita spike de criação) ---
            # Inclui mobs já na _spawn_queue (ainda não spawnados) para não exceder max_count
            _pending = getattr(zone, "_pending_spawns", 0)
            if not zone.respawn_timers and alive + _pending < zone.max_count:
                needed = zone.max_count - alive - _pending
                for i in range(needed):
                    zone.respawn_timers.append(i * 0.15 + random.uniform(0.0, 0.05))
                continue

            # --- Decrementa timers e enfileira spawns prontos ---
            still_waiting = []
            for t in zone.respawn_timers:
                t -= dt
                if t <= 0:
                    dest = self._pick_tile(zone, tilemap_comp, occupied)
                    if dest is None:
                        still_waiting.append(2.0)  # reagenda em 2s
                        continue
                    dx, dy = dest
                    occupied.add((dx, dy))  # reserva o tile imediatamente
                    self._spawn_queue.append((zone_eid, zone, dx, dy))
                    zone._pending_spawns = getattr(zone, "_pending_spawns", 0) + 1
                else:
                    still_waiting.append(t)
            zone.respawn_timers = still_waiting

        # --- Drena fila: máximo MAX_SPAWNS_PER_FRAME criações por frame ---
        for _ in range(min(self.MAX_SPAWNS_PER_FRAME, len(self._spawn_queue))):
            zone_eid, zone, dx, dy = self._spawn_queue.pop(0)
            zone._pending_spawns = max(0, getattr(zone, "_pending_spawns", 1) - 1)
            new_eid = self._spawn_one(zone_eid, zone, dx, dy)
            zone.active_entity_ids.add(new_eid)

    def _pick_tile(self, zone, tilemap_comp, occupied: set):
        """Retorna tile (x, y) caminhável aleatório dentro do raio, ou None.

        Usa amostragem aleatória (O(tentativas)) em vez de varredura completa
        O(raio²), eliminando o spike quando o raio é grande.
        """
        r = zone.radius
        rows = tilemap_comp.tile_matrix if tilemap_comp else None
        map_h = len(rows) if rows else 0
        map_w = len(rows[0]) if (rows and map_h) else 0

        for _ in range(40):
            tx = zone.center_x + random.randint(-r, r)
            ty = zone.center_y + random.randint(-r, r)
            if (tx, ty) in occupied:
                continue
            if rows is not None:
                if not (0 <= tx < map_w and 0 <= ty < map_h):
                    continue
                if rows[ty][tx].is_solid:
                    continue
            return (tx, ty)
        return None

    def _spawn_one(self, zone_eid: int, zone, dx: int, dy: int) -> int:
        """Cria um inimigo para a zona e retorna o entity ID."""
        is_ranged = zone.enemy_type == "ranged"
        level = random.randint(zone.level_min, zone.level_max)
        new_eid = create_enemy(
            self.world, dx, dy,
            attack_range=3 if is_ranged else 1,
            is_ranged=is_ranged,
            tier=zone.enemy_tier,
            race=zone.race,
            entity_class=zone.entity_class,
            level=level,
        )
        self.world.add_component(new_eid, SpawnZoneOwner(zone_eid))
        return new_eid


class MobRespawnSystem(System):
    """
    Processa a fila de respawn de inimigos.
    Lê pending_respawns do DeathHandlerSystem, decrementa timers e cria novos inimigos.

    Cooldowns por tier (definidos em DeathHandlerSystem.RESPAWN_TIMERS):
      normal → 180s (3 min)
      elite  → 300s (5 min)
      rare   → 3600s (1h)
      boss   → 18000s (5h)
    """

    MAX_RESPAWNS_PER_FRAME = 2

    def __init__(self, world: World, death_handler: DeathHandlerSystem):
        self.world         = world
        self.death_handler = death_handler
        self._respawn_queue: list = []

    def update(self, events: list = None, dt: float = 0) -> None:
        still_waiting = []

        for entry in self.death_handler.pending_respawns:
            entry["timer"] -= dt
            if entry["timer"] <= 0:
                self._respawn_queue.append(entry)
            else:
                still_waiting.append(entry)

        self.death_handler.pending_respawns = still_waiting

        # Drena fila: máximo MAX_RESPAWNS_PER_FRAME por frame
        for _ in range(min(self.MAX_RESPAWNS_PER_FRAME, len(self._respawn_queue))):
            entry        = self._respawn_queue.pop(0)
            attack_range = 3 if entry["is_ranged"] else 1
            create_enemy(
                self.world,
                tile_x=entry["tile_x"],
                tile_y=entry["tile_y"],
                attack_range=attack_range,
                is_ranged=entry["is_ranged"],
                tier=entry["tier"],
            )


class ShopSystem(System):
    """
    Sistema de comerciantes NPC.
    - Clique direito no NPC → abre painel de loja.
    - Painel esquerdo: itens à venda (clique direito = comprar).
    - Painel direito: mochila do jogador (clique direito = vender).
    - Botão [↩ Desfazer] reverte a última transação.
    """

    PANEL_W       = 1120
    PANEL_H       = 700
    ROW_H         = 50
    ICON_S        = 42
    MAX_ROWS      = 10
    LEFT_W        = 530
    RIGHT_W       = 530
    GAP           = 12
    BODY_Y_OFFSET = 126   # distância do topo do painel até a primeira linha de item
    FOOTER_H      = 62    # altura reservada para ouro + dica no rodapé
    SELL_RATIO    = 0.4
    MAX_HISTORY   = 20

    _RARITY_COLORS = {
        "common":   (200, 200, 200),
        "uncommon": ( 30, 200,  30),
        "rare":     ( 80, 140, 255),
        "epic":     (180,  50, 255),
    }

    def __init__(self, world: World, player_entity: int, screen):
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
        self._font_sm = _font(22)
        self._font_md = _font(30)
        self._font_lg = _font(38)

    @property
    def open_merchant_id(self) -> int:
        from components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        return ui.open_merchant_id if ui else -1

    @open_merchant_id.setter
    def open_merchant_id(self, value: int) -> None:
        from components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        if ui:
            ui.open_merchant_id = value

    @property
    def is_open(self) -> bool:
        return self.open_merchant_id != -1

    def open_for(self, merchant_eid: int) -> None:
        """Abre a loja para o merchant_eid especificado, resetando estado interno."""
        self.open_merchant_id   = merchant_eid
        self._pending_merchant_id = -1
        self._shop_scroll       = 0
        self._bag_scroll        = 0
        self._open_cooldown     = 0.3

    def _panel_origin(self):
        SW, SH = self.hud_surf.get_size()
        return (SW - self.PANEL_W) // 2, (SH - self.PANEL_H) // 2

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
            if event.type == pygame.MOUSEWHEEL and self.is_open:
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
            preview = entry["factory"]()
            self._net.send(_MTShop.BUY_REQUEST, {
                "shop_id":   shop_id,
                "item_name": preview.name,
                "quantity":  1,
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
            preview = entry["factory"]()
            self._net.send(_MTShop.BUY_REQUEST, {
                "shop_id":   shop_id,
                "item_name": preview.name,
                "quantity":  qty,
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

        # Modo online: notifica servidor ANTES de aplicar localmente.
        # Servidor recalcula sell_price do catálogo e atualiza gold autoritativamente.
        # Cliente aplica otimisticamente — SELL_RESULT corrige gold se diferir.
        if self._net:
            from shared.messages import MsgType as _MTS
            self._net.send(_MTS.SELL_REQUEST, {
                "item_name":    item.name,
                "item_value":   getattr(item, "value", 0),
                "stack_sold":   1,
            })

        wallet.gold += sell_val
        # Decrementa stack; remove o slot ao esgotar
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
        mid_x   = x0 + self.GAP + self.LEFT_W
        body_y  = y0 + self.BODY_Y_OFFSET

        for event in events:
            # ── Modal de quantidade aberto → processa antes de tudo ────────
            if self._qty_modal is not None:
                self._handle_qty_modal_event(event)
                return   # bloqueia eventos da loja enquanto modal está aberto

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
            close_r = pygame.Rect(x0 + self.PANEL_W - 40, y0 + 6, 34, 34)
            if event.button == 1 and close_r.collidepoint(mx, my):
                self._close()
                return

            # Botão desfazer
            undo_r = pygame.Rect(x0 + self.GAP, y0 + 54, 145, 32)
            if event.button == 1 and undo_r.collidepoint(mx, my):
                self._undo()
                return

            mods = pygame.key.get_mods()
            shift = bool(mods & pygame.KMOD_SHIFT)

            # Painel esquerdo: comprar
            if event.button == 3 and mx < mid_x and self._open_cooldown <= 0:
                for i, entry in enumerate(stock):
                    vis_i = i - self._shop_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(x0 + self.GAP,
                                        body_y + vis_i * self.ROW_H,
                                        self.LEFT_W - 4, self.ROW_H - 2)
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
                        r = pygame.Rect(mid_x + self.GAP,
                                        body_y + vis_i * self.ROW_H,
                                        self.RIGHT_W - 4, self.ROW_H - 2)
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
            SW, SH  = self.hud_surf.get_size()
            mw, mh  = 460, 240
            mx0     = (SW - mw) // 2
            my0     = (SH - mh) // 2

            # Slider
            sl_x  = mx0 + 20
            sl_y  = my0 + 130
            sl_w  = mw - 40
            sl_r  = pygame.Rect(sl_x, sl_y - 10, sl_w, 20)
            if sl_r.collidepoint(mx, my):
                ratio      = max(0.0, min(1.0, (mx - sl_x) / sl_w))
                m["qty"]   = max(1, round(ratio * m["max_qty"]))
                m["text"]  = str(m["qty"])
                m["dragging"] = True
                return

            # Botão Cancelar
            btn_cancel = pygame.Rect(mx0 + 20,      my0 + mh - 54, 190, 38)
            btn_ok     = pygame.Rect(mx0 + mw - 210, my0 + mh - 54, 190, 38)
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
            SW, SH  = self.hud_surf.get_size()
            mw      = 460
            mx0     = (SW - mw) // 2
            sl_x, sl_w = mx0 + 20, mw - 40
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

        SW, SH  = self.hud_surf.get_size()
        x0, y0  = self._panel_origin()
        W, H    = self.PANEL_W, self.PANEL_H
        mid_x   = x0 + self.GAP + self.LEFT_W
        mx, my  = pygame.mouse.get_pos()

        # Overlay escuro
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 160))
        self.hud_surf.blit(ov, (0, 0))

        # Fundo do painel
        bg = pygame.Surface((W, H), pygame.SRCALPHA)
        bg.fill((15, 10, 5, 235))
        self.hud_surf.blit(bg, (x0, y0))
        pygame.draw.rect(self.hud_surf, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        # --- Header ---
        title = self._font_lg.render(f"  {shop.get('name', 'Comerciante')}", True, (255, 220, 120))
        self.hud_surf.blit(title, (x0 + 8, y0 + 8))

        close_r   = pygame.Rect(x0 + W - 40, y0 + 6, 34, 34)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (180, 60, 60) if close_hov else (100, 35, 35), close_r, border_radius=3)
        xs = self._font_md.render("X", True, (255, 255, 255))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        LINE1 = y0 + 50   # linha após o título
        UNDO_Y = y0 + 54  # barra de desfazer
        LINE2 = y0 + 92   # linha após desfazer
        COL_Y = y0 + 96   # cabeçalhos das colunas
        body_y = y0 + self.BODY_Y_OFFSET

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + 4, LINE1), (x0 + W - 4, LINE1))

        # --- Barra de desfazer ---
        undo_r   = pygame.Rect(x0 + self.GAP, UNDO_Y, 145, 32)
        has_hist = bool(self.transaction_history)
        undo_hov = undo_r.collidepoint(mx, my) and has_hist
        undo_bg  = (55, 80, 55) if undo_hov else ((38, 55, 38) if has_hist else (28, 28, 28))
        undo_col = (150, 220, 150) if has_hist else (70, 70, 70)
        pygame.draw.rect(self.hud_surf, undo_bg,  undo_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, undo_col, undo_r, 1, border_radius=3)
        self.hud_surf.blit(self._font_sm.render("↩ Desfazer", True, undo_col),
                         (undo_r.x + 8, undo_r.y + 7))

        if self.transaction_history:
            tx  = self.transaction_history[-1]
            if tx["type"] == "buy":
                desc = f"Ultima: comprou {tx['item'].name} por {tx['price']}g"
            else:
                desc = f"Ultima: vendeu {tx['item'].name} por {tx['sell_value']}g"
            self.hud_surf.blit(self._font_sm.render(desc, True, (150, 150, 150)),
                             (x0 + self.GAP + 155, UNDO_Y + 7))

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + 4, LINE2), (x0 + W - 4, LINE2))

        # Divisor vertical
        pygame.draw.line(self.hud_surf, (90, 70, 40), (mid_x, LINE1), (mid_x, y0 + H - 36))

        # --- Cabeçalhos das colunas ---
        hdr_col = (160, 130, 80)
        hint    = (90, 80, 60)
        self.hud_surf.blit(self._font_md.render(f"LOJA  ({len(stock)} itens)", True, hdr_col),
                         (x0 + self.GAP + 4, COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ comprar  |  Shift+dir. = qtd.", True, hint),
                         (x0 + self.GAP + 4, COL_Y + 26))
        self.hud_surf.blit(self._font_md.render(f"MOCHILA  ({len(bag)}/{inv.max_slots if inv else 0})", True, hdr_col),
                         (mid_x + self.GAP + 4, COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ vender", True, hint),
                         (mid_x + self.GAP + 4, COL_Y + 26))

        pygame.draw.line(self.hud_surf, (70, 55, 30), (x0 + 4, body_y - 2), (x0 + W - 4, body_y - 2))

        # --- Painel esquerdo: itens da loja ---
        max_shop = max(0, len(stock) - self.MAX_ROWS)
        self._shop_scroll = min(self._shop_scroll, max_shop)

        for i, entry in enumerate(stock):
            vis_i = i - self._shop_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * self.ROW_H
            r     = pygame.Rect(x0 + self.GAP, row_y, self.LEFT_W - 4, self.ROW_H - 2)

            # Preview do item (instância temporária apenas para display)
            preview    = entry["factory"]()
            can_afford = wallet and wallet.gold >= entry["price"]
            inv_full   = inv and len(inv.items) >= inv.max_slots

            hov  = r.collidepoint(mx, my)
            if not can_afford or inv_full:
                bg_c   = (40, 18, 18) if hov else (22, 10, 10)
                bord_c = (110, 45, 45) if hov else (48, 22, 22)
            else:
                bg_c   = (50, 40, 20) if hov else (28, 20, 10)
                bord_c = (180, 140, 60) if hov else (60, 45, 25)

            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(preview.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + 4, r.y + (self.ROW_H - 2 - self.ICON_S) // 2,
                                  self.ICON_S, self.ICON_S)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(preview), self.ICON_S)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - 4, ic_r.bottom - 4), 3)

            name_col = rar_col if (can_afford and not inv_full) else (90, 70, 70)
            self.hud_surf.blit(self._font_sm.render(preview.name,      True, name_col),
                             (ic_r.right + 6, r.y + 6))
            self.hud_surf.blit(self._font_sm.render(preview.item_type, True, (95, 85, 65)),
                             (ic_r.right + 6, r.y + 24))

            price_col = (255, 215, 0) if (can_afford and not inv_full) else (130, 70, 70)
            ps = self._font_sm.render(f"{entry['price']}g", True, price_col)
            self.hud_surf.blit(ps, (r.right - ps.get_width() - 8, r.y + 14))

            if hov:
                lines = item_tooltip_lines(preview)
                if not can_afford:
                    lines.append(("Ouro insuficiente!", (220, 80, 80)))
                elif inv_full:
                    lines.append(("Mochila cheia!", (220, 150, 50)))
                lines.append((f"Preco: {entry['price']}g | Venda estimada: {self._sell_price(preview)}g",
                              (120, 120, 120)))
                # 6-tuple enables Shift+hover comparison with equipped item
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(preview.slot) if equip_c and preview.slot else None
                self.pending_tooltip = (mx, my, preview.name, lines, preview, eq_item)

        # Scrollbar loja
        if len(stock) > self.MAX_ROWS:
            sb_h    = self.MAX_ROWS * self.ROW_H
            sb_x    = x0 + self.GAP + self.LEFT_W - 8
            th      = max(20, sb_h * self.MAX_ROWS // len(stock))
            ty      = body_y + (sb_h - th) * self._shop_scroll // max(1, max_shop)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, 5, sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, 5, th), border_radius=2)

        # --- Painel direito: mochila ---
        max_bag = max(0, len(bag) - self.MAX_ROWS)
        self._bag_scroll = min(self._bag_scroll, max_bag)

        for i, item in enumerate(bag):
            vis_i = i - self._bag_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * self.ROW_H
            r     = pygame.Rect(mid_x + self.GAP, row_y, self.RIGHT_W - 4, self.ROW_H - 2)

            hov    = r.collidepoint(mx, my)
            bg_c   = (50, 40, 20) if hov else (28, 20, 10)
            bord_c = (180, 140, 60) if hov else (60, 45, 25)
            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + 4, r.y + (self.ROW_H - 2 - self.ICON_S) // 2,
                                  self.ICON_S, self.ICON_S)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(item), self.ICON_S)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - 4, ic_r.bottom - 4), 3)
            from ui_helpers import draw_stack_count as _dsc
            _dsc(self.hud_surf, item, ic_r, self._font_sm)

            stack = getattr(item, "stack", 1)
            name_label = f"{item.name}" if stack <= 1 else f"{item.name} x{stack}"
            self.hud_surf.blit(self._font_sm.render(name_label, True, rar_col),
                             (ic_r.right + 6, r.y + 6))
            slot_label = item.slot if item.slot else item.item_type
            self.hud_surf.blit(self._font_sm.render(slot_label, True, (95, 85, 65)),
                             (ic_r.right + 6, r.y + 24))

            sp     = self._sell_price(item)
            sp_s   = self._font_sm.render(f"+{sp}g", True, (120, 200, 100))
            self.hud_surf.blit(sp_s, (r.right - sp_s.get_width() - 8, r.y + 14))

            if hov:
                lines = item_tooltip_lines(item)
                if stack > 1:
                    lines.append((f"Quantidade: {stack}", (180, 180, 180)))
                lines.append((f"Venda: {sp}g | Valor base: {item.value}g", (120, 120, 120)))
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(item.slot) if equip_c and item.slot else None
                self.pending_tooltip = (mx, my, item.name, lines, item, eq_item)

        # Scrollbar mochila
        if len(bag) > self.MAX_ROWS:
            sb_h = self.MAX_ROWS * self.ROW_H
            sb_x = mid_x + self.GAP + self.RIGHT_W - 8
            th   = max(20, sb_h * self.MAX_ROWS // len(bag))
            ty   = body_y + (sb_h - th) * self._bag_scroll // max(1, max_bag)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, 5, sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, 5, th), border_radius=2)

        # --- Footer: ouro do jogador ---
        foot_y = y0 + H - self.FOOTER_H
        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + 4, foot_y), (x0 + W - 4, foot_y))
        if wallet:
            gold_s = self._font_md.render(f"Seu ouro: {wallet.gold}g", True, (255, 215, 0))
            self.hud_surf.blit(gold_s, (x0 + W // 2 - gold_s.get_width() // 2, foot_y + 10))

        # --- Modal de quantidade ---
        if self._qty_modal is not None:
            self._render_qty_modal(wallet)

    def _render_qty_modal(self, wallet) -> None:
        """Renderiza o modal de seleção de quantidade."""
        m       = self._qty_modal
        SW, SH  = self.hud_surf.get_size()
        mw, mh  = 460, 240
        mx0     = (SW - mw) // 2
        my0     = (SH - mh) // 2

        # Overlay semitransparente
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 130))
        self.hud_surf.blit(ov, (0, 0))

        # Fundo do modal
        bg = pygame.Surface((mw, mh), pygame.SRCALPHA)
        bg.fill((18, 14, 8, 245))
        self.hud_surf.blit(bg, (mx0, my0))
        pygame.draw.rect(self.hud_surf, (180, 140, 70), (mx0, my0, mw, mh), 2, border_radius=6)

        # Título
        title_s = self._font_md.render(m["preview"].name, True, (255, 220, 100))
        self.hud_surf.blit(title_s, (mx0 + mw // 2 - title_s.get_width() // 2, my0 + 12))

        # Preço
        price    = m["entry"]["price"]
        total    = price * m["qty"]
        gold_avail = wallet.gold if wallet else 0
        price_col  = (255, 215, 0) if total <= gold_avail else (220, 80, 80)
        price_s  = self._font_sm.render(
            f"{price}g por unidade  |  Total: {total}g  (ouro: {gold_avail}g)",
            True, price_col)
        self.hud_surf.blit(price_s, (mx0 + mw // 2 - price_s.get_width() // 2, my0 + 42))

        # ── Slider ────────────────────────────────────────────────────────
        sl_x  = mx0 + 20
        sl_y  = my0 + 130
        sl_w  = mw - 40
        ratio = (m["qty"] - 1) / max(1, m["max_qty"] - 1) if m["max_qty"] > 1 else 0.0
        handle_x = sl_x + int(ratio * sl_w)

        pygame.draw.rect(self.hud_surf, (50, 40, 25), (sl_x, sl_y - 3, sl_w, 6), border_radius=3)
        pygame.draw.rect(self.hud_surf, (160, 120, 50), (sl_x, sl_y - 3, int(ratio * sl_w), 6), border_radius=3)
        pygame.draw.circle(self.hud_surf, (220, 180, 80), (handle_x, sl_y), 10)
        pygame.draw.circle(self.hud_surf, (255, 220, 120), (handle_x, sl_y), 10, 2)

        # Labels min/max do slider
        self.hud_surf.blit(self._font_sm.render("1", True, (130, 110, 70)),
                           (sl_x, sl_y + 14))
        max_s = self._font_sm.render(str(m["max_qty"]), True, (130, 110, 70))
        self.hud_surf.blit(max_s, (sl_x + sl_w - max_s.get_width(), sl_y + 14))

        # ── Campo de texto ────────────────────────────────────────────────
        qty_s = self._font_lg.render(str(m["qty"]), True, (255, 255, 255))
        txt_x = mx0 + mw // 2 - qty_s.get_width() // 2
        self.hud_surf.blit(qty_s, (txt_x, my0 + 76))
        # Cursor piscante
        if (pygame.time.get_ticks() // 500) % 2 == 0:
            cx = txt_x + qty_s.get_width() + 2
            pygame.draw.line(self.hud_surf, (200, 200, 200),
                             (cx, my0 + 78), (cx, my0 + 78 + qty_s.get_height() - 4), 2)

        # ── Botões ────────────────────────────────────────────────────────
        btn_cancel = pygame.Rect(mx0 + 20,       my0 + mh - 54, 190, 38)
        btn_ok     = pygame.Rect(mx0 + mw - 210, my0 + mh - 54, 190, 38)
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
            lbl_s = self._font_sm.render(label, True, col_txt)
            self.hud_surf.blit(lbl_s, (btn.centerx - lbl_s.get_width() // 2,
                                        btn.centery - lbl_s.get_height() // 2))

        # Dica ESC
        esc_s = self._font_sm.render("ESC cancela  |  ENTER confirma", True, (80, 70, 50))
        self.hud_surf.blit(esc_s, (mx0 + mw // 2 - esc_s.get_width() // 2, my0 + mh - 14))


class ConsumableSystem(System):
    """Processa a barra de consumíveis (keybinds + uso) e ActiveRegen (HoT).

    Online: injete `system._net = self._net` após criação para que o
    uso de consumíveis seja comunicado ao servidor via CONSUMABLE_USE.
    O servidor aplica o heal autoritativo; o cliente aplica localmente
    como predição (floating text + HP visual imediato).
    """

    def __init__(self, world: World):
        self.world = world
        self._net  = None   # injetado pelo GameEngine no modo online

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
                healed = min(regen.heal_per_tick, cs.max_hp - cs.current_hp)
                cs.current_hp = min(cs.max_hp, cs.current_hp + regen.heal_per_tick)
                # Online: servidor envia COMBAT_RESULT com heal_amount por tick —
                # suprime FLT local para evitar texto duplicado (+X e +X HP).
                if pos_c and healed > 0 and not self._net:
                    FLT.add(f"+{healed}", pos_c.x, pos_c.y - 16,
                            (80, 220, 120), "small", eid)
                if regen.ticks_remaining <= 0:
                    to_remove.append(eid)

        for eid in to_remove:
            self.world.remove_component(eid, ActiveRegen)

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

        # Não pode ser usado com HP cheio
        if cs.current_hp >= cs.max_hp:
            WARN.add("HP já está cheio")
            return

        # Cura instantânea
        heal_instant = cons.get("heal_instant", 0)
        if heal_instant > 0:
            healed = min(heal_instant, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_instant)
            if pos_c and healed > 0:
                FLT.add(f"+{healed}", pos_c.x, pos_c.y - 16,
                        (80, 220, 120), "small", entity_id)

        # HoT (ActiveRegen)
        heal_per_tick = cons.get("heal_per_tick", 0)
        ticks         = cons.get("ticks", 0)
        interval      = cons.get("interval", 2.0)
        if heal_per_tick > 0 and ticks > 0:
            self.world.add_component(entity_id, ActiveRegen(
                heal_per_tick=heal_per_tick,
                interval=interval,
                ticks_total=ticks,
            ))

        # Consome 1 unidade do stack
        item.stack -= 1
        if item.stack <= 0:
            inv.items.remove(item)

        # Cooldown global (cbar pode ser None se usado pelo painel de inventário)
        if cbar is not None:
            cbar.global_cooldown = ConsumableBar.GCD_DURATION

        quest_fire("use_consumable", item_name=item.name)

        # Modo online: notifica servidor para aplicar o mesmo efeito autoritativamente.
        # Payload extensível: "buffs" reservado para efeitos futuros (stat boosts etc.)
        if self._net:
            from shared.messages import MsgType as _MTC
            _hot = None
            if heal_per_tick > 0 and ticks > 0:
                _hot = {
                    "heal_per_tick": heal_per_tick,
                    "interval":      interval,
                    "ticks":         ticks,
                }
            self._net.send(_MTC.CONSUMABLE_USE, {
                "item_name":    item_name,
                "heal_instant": cons.get("heal_instant", 0),
                "hot":          _hot,
                "ooc_only":     cons.get("ooc_only", False),
                "buffs":        [],
            })


class LootSystem(System):
    """
    Detecta clique direito em cadáveres e exibe modal de loot.
    Clique esquerdo em item no modal → move para o inventário do jogador.
    Hover → tooltip com detalhes do item.
    Shift + hover → painel de comparação com item equipado no mesmo slot.
    """

    # Layout do modal em lista vertical (tamanho fixo)
    MODAL_W   = 260   # largura fixa do modal
    ROW_H     = 46    # altura de cada linha (ícone + texto)
    ICON_S    = 36    # tamanho do ícone dentro da linha
    MAX_ROWS  = 5     # linhas visíveis (scroll se houver mais)
    PAD       = 8
    TITLE_H   = 28
    SCROLL_W  = 8     # largura da barra de rolagem
    MODAL_H   = TITLE_H + MAX_ROWS * (ROW_H + PAD // 2) + PAD  # altura fixa

    # Cores
    BG_COLOR     = (20, 14, 8, 220)
    BORDER_COLOR = (140, 100, 60)
    HOVER_COLOR  = (60, 45, 20)

    RARITY_COLORS = {
        "common":   (200, 200, 200),
        "uncommon": ( 30, 200,  30),
        "rare":     ( 80, 140, 255),
        "epic":     (180,  50, 255),
    }

    def __init__(self, world: World, screen: pygame.Surface, player_entity: int = -1):
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        # open_corpse_id → LootUIState component (via property abaixo)
        self.pending_loot_corpse_id: int = -1
        self.pending_tooltip             = None  # lido por GameEngine no fim do frame
        self.font_sm = _font(20)
        self.font_md = _font(24)
        self._modal_x       = 0   # posição X do modal (definida ao abrir)
        self._modal_y       = 0   # posição Y do modal
        self._scroll_offset = 0   # índice da primeira linha visível
        self._pending_cursor = (0, 0)  # cursor quando o loot foi solicitado

    @property
    def open_corpse_id(self) -> int:
        from components import LootUIState
        ui = self.world.get_component(self.player_entity, LootUIState)
        return ui.open_corpse_id if ui else -1

    @open_corpse_id.setter
    def open_corpse_id(self, value: int) -> None:
        from components import LootUIState
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
        sw, sh = self.hud_surf.get_size()
        x = max(4, min(ax + 16, sw - self.MODAL_W - 4))
        y = max(4, min(ay - self.TITLE_H, sh - self.MODAL_H - 4))
        self._modal_x, self._modal_y = x, y

    def _modal_rect(self) -> pygame.Rect:
        """Tamanho sempre fixo — não depende do conteúdo."""
        return pygame.Rect(self._modal_x, self._modal_y, self.MODAL_W, self.MODAL_H)

    def _close_btn_rect(self, modal: pygame.Rect) -> pygame.Rect:
        """Botão X no canto superior direito da barra de título."""
        sz = self.TITLE_H - 6
        return pygame.Rect(modal.right - sz - 4, modal.y + 3, sz, sz)

    def _row_rect(self, modal: pygame.Rect, row: int) -> pygame.Rect:
        """Rect de uma linha da lista (row 0 = primeira linha)."""
        y = modal.y + self.TITLE_H + row * (self.ROW_H + self.PAD // 2)
        return pygame.Rect(modal.x + self.PAD, y, self.MODAL_W - self.PAD * 2, self.ROW_H)

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
                        from stats_system import CLASS_ARMOR_ALLOWED
                        from components import CharacterStats as _CST
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

        modal = self._modal_rect()
        bg = pygame.Surface((modal.w, modal.h), pygame.SRCALPHA)
        bg.fill(self.BG_COLOR)
        self.hud_surf.blit(bg, modal.topleft)
        pygame.draw.rect(self.hud_surf, self.BORDER_COLOR, modal, 2, border_radius=4)

        # --- Barra de título ---
        title = self.font_sm.render("Loot", True, self.BORDER_COLOR)
        self.hud_surf.blit(title, (modal.x + self.PAD, modal.y + (self.TITLE_H - title.get_height()) // 2))

        # Botão X
        close_r = self._close_btn_rect(modal)
        pygame.draw.rect(self.hud_surf, (90, 30, 30), close_r, border_radius=2)
        x_surf = self.font_sm.render("X", True, (220, 100, 100))
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
        row_w = self.MODAL_W - self.PAD * 2 - (self.SCROLL_W + 4 if need_scroll else 0)

        if need_scroll:
            # Trilho
            track_x = modal.right - self.SCROLL_W - 4
            track_y = modal.y + self.TITLE_H + self.PAD // 2
            track_h = self.MAX_ROWS * (self.ROW_H + self.PAD // 2) - self.PAD // 2
            pygame.draw.rect(self.hud_surf, (40, 30, 18),
                             (track_x, track_y, self.SCROLL_W, track_h), border_radius=3)
            # Thumb
            thumb_h = max(20, track_h * self.MAX_ROWS // total)
            max_scroll = total - self.MAX_ROWS
            thumb_y = track_y + (track_h - thumb_h) * self._scroll_offset // max(1, max_scroll)
            pygame.draw.rect(self.hud_surf, (130, 100, 55),
                             (track_x, thumb_y, self.SCROLL_W, thumb_h), border_radius=3)

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
                pygame.draw.rect(self.hud_surf, (70, 55, 15) if hovered else (28, 20, 8), rr, border_radius=3)
                pygame.draw.rect(self.hud_surf, (200, 170, 50) if hovered else (100, 80, 20), rr, 1, border_radius=3)
                icon_r = pygame.Rect(rr.x + 4, rr.centery - self.ICON_S // 2, self.ICON_S, self.ICON_S)
                r_out = self.ICON_S // 2
                r_in  = max(1, r_out - 4)
                pygame.draw.circle(self.hud_surf, (180, 140, 0),  icon_r.center, r_out)
                pygame.draw.circle(self.hud_surf, (255, 215, 0),  icon_r.center, r_in)
                pygame.draw.circle(self.hud_surf, (120, 90, 0),   icon_r.center, r_out, 1)
                g_surf = self.font_sm.render("G", True, (120, 90, 0))
                self.hud_surf.blit(g_surf, (icon_r.centerx - g_surf.get_width() // 2,
                                          icon_r.centery - g_surf.get_height() // 2))
                tx = icon_r.right + 8
                ty = rr.centery - self.font_md.get_height() // 2
                self.hud_surf.blit(self.font_md.render(f"{corpse.coins} moedas", True, (255, 215, 0)), (tx, ty))
                if hovered:
                    self.pending_tooltip = (mx, my, "Moedas",
                                            [(f"{corpse.coins} moedas disponíveis", (255, 215, 0)),
                                             ("Clique p/ coletar tudo", (140, 140, 140))])

            else:
                # --- Linha de item ---
                item = entry[1]
                bg_col = self.HOVER_COLOR if hovered else (28, 20, 8)
                pygame.draw.rect(self.hud_surf, bg_col, rr, border_radius=3)

                icon_r = pygame.Rect(rr.x + 4, rr.centery - self.ICON_S // 2, self.ICON_S, self.ICON_S)
                icon_surf = ICONS.get(ICONS.item_key(item), self.ICON_S)
                if icon_surf:
                    self.hud_surf.blit(icon_surf, icon_r)
                else:
                    fb = self.RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.hud_surf, fb, icon_r, border_radius=2)
                from ui_helpers import draw_stack_count as _dsc2
                _dsc2(self.hud_surf, item, icon_r, self.font_sm)

                rc = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                tx = icon_r.right + 8
                _stack = getattr(item, "stack", 1)
                _max_s = getattr(item, "max_stack", 1)
                _name_lbl = f"{item.name} x{_stack}" if _max_s > 1 else item.name
                name_surf = self.font_md.render(_name_lbl, True, rc)
                sub_surf  = self.font_sm.render(f"{item.item_type}  •  {item.slot}", True, (130, 115, 95))
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
            empty = self.font_sm.render("(vazio)", True, (120, 100, 80))
            rr = self._row_rect(modal, 0)
            self.hud_surf.blit(empty, (rr.x + 4, rr.centery - empty.get_height() // 2))

        # Tooltip + comparação no hover
        if hovered_item is not None:
            lines = item_tooltip_lines(hovered_item)
            lines.append(("Clique p/ pegar | Shift p/ comparar", (140, 140, 140)))
            equip = None
            for _, eq, _ in self.world.get_entities_with(Equipment, PlayerControlled):
                equip = eq
                break
            equipped_item = equip.slots.get(hovered_item.slot) if equip else None
            self.pending_tooltip = (mx, my, hovered_item.name, lines,
                                    hovered_item, equipped_item)


# ---------------------------------------------------------------------------
# SkillSystem
# ---------------------------------------------------------------------------
from skill_handlers import SkillHandlers

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
            from components import Channeling
            channeling = self.world.get_component(self.player_entity_id, Channeling)
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
                            from combat_log import LOG as _LOG
                            from floating_text import WARN as _WARN
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
            from components import TalentTree as _TTLockOff
            _tt_lk_off = self.world.get_component(self.player_entity_id, _TTLockOff)
            if _tt_lk_off is not None and _tt_lk_off.allocated.get(_tl_tid_off, 0) < _tl_min_off:
                WARN.add("Requer talento")
                return False

        combat_state = self.world.get_component(self.player_entity_id, CombatState)
        if combat_state and not combat_state.can_act():
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
                        from components import SpellCast as _SpellCast
                        _sc = self.world.get_component(self.player_entity_id, _SpellCast)
                        if _sc is None or _sc.interruptible:
                            combat_state.is_casting = True
                    if skill.sound_name and not has_cast and not is_aoe:
                        SOUNDS.play_skill(skill.sound_name)
                    if player_skills:
                        player_skills.gcd_timer = PlayerSkills.GCD_DURATION
                    quest_fire("use_skill", skill_id=skill.skill_id)
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
                                                  __import__("components").TileMovement)

        # 0. Talent lock: skill requer talento que não está alocado
        if skill.skill_id and skill.skill_id in _TALENT_SKILL_REQ_SYS:
            _tl_tid, _tl_min = _TALENT_SKILL_REQ_SYS[skill.skill_id]
            from components import TalentTree as _TTLock
            _tt_lk = self.world.get_component(self.player_entity_id, _TTLock)
            if _tt_lk is not None and _tt_lk.allocated.get(_tl_tid, 0) < _tl_min:
                WARN.add("Requer talento")
                return False

        # 1. can_act (igual offline)
        if combat_state and not combat_state.can_act():
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

        _is_online = getattr(self, "_remote_mobs_reverse", None) is not None
        _char = self.world.get_component(self.player_entity_id,
                                          __import__("components").CharacterStats)
        _cs   = self.world.get_component(self.player_entity_id,
                                          __import__("components").CombatStats)

        # 4. Para ofensivas: resolve alvo + inicia chase + verifica range
        if _is_offensive and combat_state and _tile_move_sk:
            if _is_online:
                # Online: mobs remotos não têm CombatStats — verifica target_entity_id diretamente
                _target_local = combat_state.target_entity_id
                if _target_local == -1:
                    WARN.add("Nenhum alvo")
                    return False

                # enter_combat + is_pursuing ANTES do range check (igual offline _use_skill:5307-5313)
                # Garante que pressionar skill inicia o chase mesmo fora de alcance.
                from stat_fns import enter_combat as _ec_pre
                _ec_pre(combat_state)
                if not _has_cast:
                    combat_state.is_pursuing = True

                # Range check UNIVERSAL em pixels.
                # max_range_px = max_range_tiles * TILE_SIZE + TOLERANCE
                # min_range_px = min_range_tiles * TILE_SIZE - TOLERANCE  (se > 0)
                _params          = getattr(skill, "params", {}) or {}
                _max_range_tiles = _params.get("max_range", 1)
                _min_range_tiles = _params.get("min_range", 0)
                _tol = SkillHandlers.RANGE_TOLERANCE_PX
                _max_px = _max_range_tiles * TILE_SIZE + _tol
                _min_px = max(0.0, _min_range_tiles * TILE_SIZE - _tol) if _min_range_tiles > 0 else 0.0
                _pl_pos  = self.world.get_component(self.player_entity_id,
                                                     __import__("components").Position)
                _tgt_pos = self.world.get_component(_target_local,
                                                     __import__("components").Position)
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
            else:
                # Offline: _resolve_target auto-seleciona e verifica CombatStats
                _target = self._resolve_target(combat_state, _tile_move_sk)
                if _target == -1:
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
            # Mana
            _mana_pct = getattr(skill, "mana_cost_pct", 0.0)
            if _mana_pct > 0 and hasattr(_cs, "max_mana"):
                _mana_cost = int(_cs.max_mana * _mana_pct)
                if _mana_cost > 0 and getattr(_cs, "mana", 0) < _mana_cost:
                    WARN.add("Mana insuficiente")
                    return False
        # HP threshold (ex: Executar exige alvo <30% HP) — verifica no cliente via _mob_hp
        if not (_is_procced and _proc_ignores_cost) and _is_online and combat_state:
            _params_sk   = getattr(skill, "params", {}) or {}
            _hp_threshold = _params_sk.get("hp_threshold", 0.0) if isinstance(_params_sk, dict) else 0.0
            if _hp_threshold > 0:
                _mob_hp_dict = getattr(self, "_mob_hp", {})
                _rev_sk      = getattr(self, "_remote_mobs_reverse", {})
                _tgt_local   = combat_state.target_entity_id
                _srv_eid_sk  = _rev_sk.get(_tgt_local, -1)
                if _srv_eid_sk in _mob_hp_dict:
                    _tgt_hp, _tgt_hp_max = _mob_hp_dict[_srv_eid_sk]
                    if _tgt_hp / max(1, _tgt_hp_max) >= _hp_threshold:
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
            skill.current_cooldown = skill.cooldown
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
            from stat_fns import enter_combat as _ec_sk
            _ec_sk(combat_state)
            if not _has_cast:
                combat_state.is_pursuing = True

        # Envia CAST_SKILL com rage/mana PRÉ-dedução para o servidor validar corretamente
        if self._net:
            from shared.messages import MsgType as _MT
            _tid_local  = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _rev = getattr(self, "_remote_mobs_reverse", {})
            _tid_server = _rev.get(_tid_local, -1)

            self._net.send(_MT.CAST_SKILL, {
                "sid":   skill.skill_id,
                "tid":   _tid_server,
                "dir_x": 0.0,
                "dir_y": 0.0,
                "rage":  _rage_pre,
                "mana":  _mana_pre,
            })

        # Interceptar: executa animação de dash localmente (client-side prediction)
        # O servidor confirma a posição final via ENTITY_MOVE; se coincidir, não interrompe.
        if skill.skill_id == "interceptar" and _tile_move_sk and combat_state:
            self._interceptar_dash_visual(combat_state, _tile_move_sk)

        return True

    def _interceptar_dash_visual(self, combat_state, tile_move) -> None:
        """Anima o dash do Interceptar localmente, sem verificações de HP (servidor já validou)."""
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
            cs = self.world.get_component(current, CombatStats)
            if cs and cs.current_hp > 0:
                return current
        # Auto-seleciona o inimigo mais próximo visível na tela e no campo de visão
        px, py    = tile_move.current_tile_x, tile_move.current_tile_y
        best_id   = -1
        best_dist = float("inf")
        for eid, epos, _, _, etm, ecs, _ in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats, Visible):
            if ecs.current_hp <= 0:
                continue
            if not self._is_on_screen(epos):
                continue
            d = chebyshev(px, py, etm.current_tile_x, etm.current_tile_y)
            if d < best_dist:
                best_dist = d
                best_id   = eid
        if best_id != -1:
            combat_state.target_entity_id = best_id
            combat_state.is_pursuing      = True
            enter_combat(combat_state)
        return best_id

    # Todos os handlers (_skill_* e _talent_*) estão em skill_handlers.py via SkillHandlers.

