# systems.py
from __future__ import annotations
import pygame
import math
import heapq
import random

from components import Position, Renderable, PlayerControlled, Camera, Collider, \
                       Enemy, AIControlled, InitialPosition, DetectionRadius, Tilemap, \
                       TileMovement, CombatStats, Modifier, CombatState, PlayerAutoMove, \
                       Projectile, Corpse, Inventory, EnemyTier, Equipment, Wallet, Merchant, \
                       CharacterStats
from world import World
from tileset import TILE_SIZE
from utils import chebyshev, start_tile_movement
from damage_calculator import resolve_attack_outcome, calculate_base_damage
from combat_log import LOG
from sound_manager import SOUNDS
from floating_text import FLT
from icon_manager import ICONS
from ui_helpers import item_tooltip_lines


class System:
    """
    Classe base para todos os sistemas ECS.
    Sistemas de lógica sobrescrevem update().
    Sistemas de renderização sobrescrevem render().
    Isso elimina isinstance checks no game loop.
    """
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

    def _get_tilemap_component(self):
        if not self.tilemap_comp:
            for _, tm_comp in self.world.get_entities_with(Tilemap):
                self.tilemap_comp = tm_comp
                break
        return self.tilemap_comp

    def is_tile_walkable(self, moving_entity_id: int, target_tile_x: int, target_tile_y: int) -> bool:
        tilemap_comp = self._get_tilemap_component()
        if not tilemap_comp:
            print("Erro: Tilemap não encontrado para validação!")
            return False

        if not (0 <= target_tile_x < tilemap_comp.map_width_tiles and
                0 <= target_tile_y < tilemap_comp.map_height_tiles):
            return False

        tile_type = tilemap_comp.tile_matrix[target_tile_y][target_tile_x]
        if tile_type.is_solid:
            return False

        for entity_id, tile_move_comp in self.world.get_entities_with(TileMovement):
            if entity_id == moving_entity_id:
                continue
            
            if (not tile_move_comp.is_moving and 
                tile_move_comp.current_tile_x == target_tile_x and 
                tile_move_comp.current_tile_y == target_tile_y) or \
               (tile_move_comp.is_moving and 
                tile_move_comp.target_tile_x == target_tile_x and 
                tile_move_comp.target_tile_y == target_tile_y):
                
                if self.world.get_component(moving_entity_id, Enemy) and \
                   self.world.get_component(entity_id, PlayerControlled):
                    ai_control = self.world.get_component(moving_entity_id, AIControlled)
                    if ai_control:
                        ai_control.is_blocked = True
                        ai_control.blocked_by_entity_id = entity_id
                
                return False

        return True


# Novo Sistema: CombatSystem
# Gerencia a lógica de dano, cura, morte e outros aspectos de combate.
class CombatSystem(System):
    """Aplica dano entre entidades e delega processamento de morte para DeathHandlerSystem.

    Constantes de cálculo movidas para damage_calculator.py.
    Lógica de morte (loot, cadáver, XP) movida para DeathHandlerSystem.
    """

    def __init__(self, world: World):
        self.world = world

    def _get_combat_stats(self, entity_id: int) -> CombatStats | None:
        """Helper para obter o componente CombatStats de uma entidade."""
        return self.world.get_component(entity_id, CombatStats)

    def _get_mainhand_weapon(self, entity_id: int):
        """Retorna o item equipado na mão principal, ou None."""
        from components import Equipment
        equip = self.world.get_component(entity_id, Equipment)
        if equip:
            return equip.slots.get("mainhand")
        return None

    def _calculate_damage(self, attacker_id: int, attacker_stats: CombatStats,
                          damage_type: str, base_ability_damage: float = 0,
                          multiplier: float = 1.0,
                          target_stats: CombatStats = None,
                          extra_crit: float = 0.0) -> tuple:
        """Calcula dano e resolve a tabela de ataque. Retorna (damage, outcome).

        Delega a matemática pura para damage_calculator.py.
        """
        if target_stats is not None:
            outcome, block_reduction = resolve_attack_outcome(
                attacker_stats, target_stats, damage_type, extra_crit)
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
                    is_ability: bool = False) -> bool:
        """Aplica dano de um atacante a um alvo. Retorna True se o alvo foi derrotado."""
        attacker_stats = self._get_combat_stats(attacker_id)
        target_stats   = self._get_combat_stats(target_id)
        if not attacker_stats or not target_stats:
            return False
        if target_stats.current_hp <= 0:
            return True

        attacker_is_player = self.world.get_component(attacker_id, PlayerControlled) is not None
        target_is_player   = self.world.get_component(target_id,   PlayerControlled) is not None

        target_pos = self.world.get_component(target_id, Position)
        _tx = target_pos.x if target_pos else 0.0
        _ty = target_pos.y if target_pos else 0.0

        extra_crit = self._extra_crit_bonus(attacker_id, target_id, attacker_is_player)
        calculated_damage, outcome = self._calculate_damage(
            attacker_id, attacker_stats, damage_type,
            base_ability_damage, multiplier,
            target_stats=target_stats, extra_crit=extra_crit,
        )

        if outcome in ('miss', 'dodge', 'parry'):
            self._emit_avoidance_feedback(outcome, _tx, _ty, target_id,
                                          attacker_is_player, target_is_player)
            return False

        final_damage = self._resolve_damage_modifiers(
            attacker_id, target_id, calculated_damage, outcome,
            apply_armor_reduction, damage_type,
            attacker_is_player, target_is_player,
        )
        target_stats.current_hp -= final_damage

        is_crit  = outcome == 'crit'
        is_block = outcome == 'block'
        self._emit_hit_feedback(attacker_id, target_id, final_damage, outcome,
                                is_crit, is_block, _tx, _ty,
                                attacker_is_player, target_is_player, is_ability)
        self._apply_on_hit_procs(attacker_id, is_crit, attacker_is_player)

        if target_stats.current_hp <= 0:
            return self._handle_death(target_id, attacker_id)
        return False

    # ------------------------------------------------------------------
    # Helpers internos de deal_damage
    # ------------------------------------------------------------------

    def _extra_crit_bonus(self, attacker_id: int, target_id: int,
                          attacker_is_player: bool) -> float:
        """Explorador de Fraquezas: +15% crit/ponto contra alvos com slow ativo."""
        if not attacker_is_player:
            return 0.0
        _cs_ef = self._get_combat_stats(attacker_id)
        if not _cs_ef or _cs_ef.explorador_crit_per_point <= 0:
            return 0.0
        _tm_ef = self.world.get_component(target_id, TileMovement)
        if _tm_ef and _tm_ef.slow_timer > 0:
            return _cs_ef.explorador_crit_per_point * 0.15
        return 0.0

    def _resolve_damage_modifiers(self, attacker_id: int, target_id: int,
                                  base: float, outcome: str,
                                  apply_armor: bool, damage_type: str,
                                  attacker_is_player: bool,
                                  target_is_player: bool) -> int:
        """Aplica armadura, talentos de dano e status effects. Retorna dano final inteiro."""
        dmg = float(base)

        # Armadura
        if apply_armor and damage_type in ("physical", "physical_fixed"):
            target_stats = self._get_combat_stats(target_id)
            dmg *= 100.0 / (100.0 + target_stats.armor)

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
        from components import StatusEffects as _SE_BP
        _sfx_target = self.world.get_component(target_id, _SE_BP)
        if _sfx_target and _sfx_target.enraged_timer > 0 and not target_is_player:
            dmg *= 1.10
        _sfx_att = self.world.get_component(attacker_id, _SE_BP)
        if _sfx_att and _sfx_att.enraged_timer > 0 and not attacker_is_player:
            dmg *= 1.05

        return max(0, int(dmg))

    def _emit_avoidance_feedback(self, outcome: str, tx: float, ty: float,
                                  target_id: int,
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
                from components import MobSounds as _MS_crit
                _ms_crit = self.world.get_component(target_id, _MS_crit)
                SOUNDS.play_mob_sounds(_ms_crit, "crit")
                SOUNDS.play_emote_get_crit(is_player=False, mob_sounds_comp=_ms_crit)
        if attacker_is_player and not is_ability:
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
        from components import CharacterStats as _CS2
        _cs2 = self.world.get_component(attacker_id, _CS2)
        _combat2 = self._get_combat_stats(attacker_id)
        if _cs2 and _combat2 and _combat2.embalo_on_crit:
            _cs2.embalo_charges += 1
            _ppos = self.world.get_component(attacker_id, Position)
            if _ppos:
                FLT.add("Embalo!", _ppos.x, _ppos.y,
                        (255, 200, 60), size="large", target_id=attacker_id)

    def _handle_death(self, dead_entity_id: int, killer_entity_id: int) -> bool:
        """Diferencia morte de jogador vs inimigo.

        Jogador: fica no world; DeathRespawnSystem detecta hp<=0 e respawna.
        Inimigo: adiciona PendingDeath — DeathHandlerSystem processa no mesmo frame.
        """
        is_player = self.world.get_component(dead_entity_id, PlayerControlled) is not None
        if is_player:
            LOG.add("Voce foi derrotado! Renascendo...", (220, 50, 50))
            return True

        from components import PendingDeath as _PD
        self.world.add_component(dead_entity_id, _PD(killer_entity_id=killer_entity_id))
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
        from components import PendingDeath, XPReward, EntityIdentity, \
                               SpawnZoneOwner, SpawnZone, PlayerSkills
        from loot_tables import roll_loot, roll_mob_loot, roll_coins
        from entity_factory import create_corpse

        to_remove = []
        for entity_id, pd in self.world.get_entities_with(PendingDeath):
            # Entidades que morrem são inimigos (jogador nunca recebe PendingDeath)
            ident     = self.world.get_component(entity_id, EntityIdentity)
            from components import MobSounds as _MS_death
            _ms_death = self.world.get_component(entity_id, _MS_death)
            SOUNDS.play_mob_sounds(_ms_death, "death", dedup_key=str(entity_id))

            xp_comp = self.world.get_component(entity_id, XPReward)
            if xp_comp:
                self.pending_xp.append(xp_comp.amount)

            pos       = self.world.get_component(entity_id, Position)
            ai        = self.world.get_component(entity_id, AIControlled)
            tier_comp = self.world.get_component(entity_id, EnemyTier)
            init_pos  = self.world.get_component(entity_id, InitialPosition)

            if pos and ai and tier_comp:
                if ident:
                    loot = roll_mob_loot(ident.name, tier_comp.tier)
                else:
                    enemy_type = "ranged" if ai.is_ranged else "melee"
                    loot = roll_loot(enemy_type, tier_comp.tier)
                coins = roll_coins(tier_comp.tier)

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
        from components import Equipment
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

            # Decay de Rage fora de combate (apenas jogador)
            if self.world.get_component(eid, PlayerControlled) is not None:
                char_stats = self.world.get_component(eid, CharacterStats)
                if char_stats and char_stats.rage > 0:
                    if not cs.in_combat:
                        char_stats.rage_decay_timer += dt
                        if char_stats.rage_decay_timer >= self.RAGE_DECAY_INTERVAL:
                            char_stats.rage_decay_timer -= self.RAGE_DECAY_INTERVAL
                            char_stats.rage = max(0, char_stats.rage - self.RAGE_DECAY_AMOUNT)
                    else:
                        char_stats.rage_decay_timer = 0.0

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
                    combat_stats.remove_modifier(entry["modifier"])
                    LOG.add(f"Efeito '{entry['label']}' expirou.", (160, 160, 160))

            if cs._just_entered_combat:
                cs._just_entered_combat = False
                self._trigger_procs(eid, combat_stats)

    def _trigger_procs(self, entity_id: int, combat_stats: CombatStats) -> None:
        """Rola e aplica procs de itens equipados ao entrar em combate."""
        from components import Equipment, Modifier
        equip = self.world.get_component(entity_id, Equipment)
        if not equip or not combat_stats:
            return
        for item in equip.slots.values():
            if item and item.proc:
                p = item.proc
                if random.random() < p["chance"]:
                    mod = Modifier(p["attribute"], p["value"])
                    combat_stats.add_timed_modifier(mod, p["duration"], p["label"])
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

    def __init__(self, world: World, combat_system: CombatSystem, screen: pygame.Surface):
        self.world = world
        self.combat_system = combat_system
        self.screen = screen

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
                self.combat_system.deal_damage(
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
                pygame.draw.line(self.screen, color, (x1, y1), (x2, y2), 4)
            else:
                pygame.draw.circle(self.screen, color, (draw_x, draw_y), 4)


class MouseTargetingSystem(System):
    """
    Detecta clique direito do mouse, identifica o inimigo clicado e define
    CombatState.target_entity_id do jogador. Também ativa PlayerAutoMove.
    """

    def __init__(self, world: World, player_entity_id: int, screen: pygame.Surface):
        self.world = world
        self.player_entity_id = player_entity_id
        self.screen = screen

    def _get_camera_offset(self) -> tuple:
        sw, sh = self.screen.get_width(), self.screen.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - sw / 2, cam_pos.y - sh / 2
        return 0.0, 0.0

    def _enemy_at_world_pos(self, world_x: float, world_y: float) -> int:
        """Retorna o entity_id do inimigo vivo na posição mundo, ou -1."""
        for entity_id, pos, renderable, _ in self.world.get_entities_with(
                Position, Renderable, Enemy):
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
        """Retorna IDs de inimigos vivos visíveis na tela, ordenados por distância ao jogador."""
        sw = self.screen.get_width()
        sh = self.screen.get_height()
        player_pos = self.world.get_component(self.player_entity_id,
                                              __import__("components").Position)
        result = []
        for eid, pos, _ in self.world.get_entities_with(Position, Enemy):
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

            cam_x, cam_y = self._get_camera_offset()
            world_x = event.pos[0] + cam_x
            world_y = event.pos[1] + cam_y
            target_id = self._enemy_at_world_pos(world_x, world_y)

            player_cs   = self.world.get_component(self.player_entity_id, CombatState)
            player_auto = self.world.get_component(self.player_entity_id, PlayerAutoMove)

            if event.button == 1:
                # Clique esquerdo em inimigo → seleciona alvo e cancela perseguição do alvo anterior
                if target_id != -1:
                    if player_cs:
                        player_cs.target_entity_id = target_id
                        player_cs.is_pursuing = False
                # Clique esquerdo no chão → não faz nada aqui (hotbar/inventário cuidam)

            elif event.button == 3:
                if target_id != -1:
                    # Clique direito em inimigo → seleciona alvo, entra em combate e persegue
                    if player_cs:
                        player_cs.target_entity_id = target_id
                        player_cs.is_pursuing = True
                        player_cs.enter_combat()
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
                            player_cs.target_entity_id = -1
                        if player_auto:
                            player_auto.ground_target = (tile_x, tile_y)
                            player_auto.active = True
                            player_auto.path.clear()
                            player_auto.path_recalc_timer = 0.0


class PlayerInputSystem(System):
    PLAYER_ATTACK_RANGE = 1     # tiles de alcance (punhos / melee)
    AUTO_MOVE_RECALC_INTERVAL = 0.3  # segundos entre recálculos de path

    def __init__(self, world: World, tile_validation_system: TileValidationSystem,
                 combat_system: CombatSystem, pathfinding_system: PathfindingSystem,
                 screen: "pygame.Surface | None" = None):
        self.world = world
        self.tile_validation_system = tile_validation_system
        self.combat_system = combat_system
        self.pathfinding_system = pathfinding_system
        self.screen = screen

    def _is_on_screen(self, pos: "Position") -> bool:
        """Retorna True se a entidade está dentro dos limites da câmera atual."""
        if self.screen is None or pos is None:
            return True  # sem tela configurada: não filtra
        sw = self.screen.get_width()
        sh = self.screen.get_height()
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
        from components import PlayerSkills
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
                    # Teclado cancela auto-move e perseguição
                    if auto_move:
                        auto_move.active = False
                        auto_move.path.clear()
                        auto_move.ground_target = None
                    if combat_state:
                        combat_state.is_pursuing = False
                    if self.tile_validation_system.is_tile_walkable(entity_id, tgt_x, tgt_y):
                        self._start_tile_movement(position, tile_movement, tgt_x, tgt_y)

            # --- Auto-move e auto-ataque em direção ao alvo selecionado ---
            if combat_state and combat_state.target_entity_id != -1:
                self._process_target(
                    entity_id, position, tile_movement,
                    combat_stats, combat_state, auto_move, can_act, dt
                )
            # --- Movimento de chão (clique esquerdo) ---
            elif auto_move and auto_move.active and auto_move.ground_target:
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

        if target_tm:
            tgt_tile_x, tgt_tile_y = target_tm.current_tile_x, target_tm.current_tile_y
        else:
            tgt_tile_x = int(target_pos.x / TILE_SIZE)
            tgt_tile_y = int(target_pos.y / TILE_SIZE)

        pl_tile_x = tile_movement.current_tile_x
        pl_tile_y = tile_movement.current_tile_y
        dist = chebyshev(pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y)

        if dist <= self.PLAYER_ATTACK_RANGE:
            # No alcance: limpa o path
            if auto_move:
                auto_move.path.clear()
            if can_act and combat_stats.attack_cooldown_timer <= 0:
                SOUNDS.play_emote_attack(is_player=True)
                _tgt_cs   = self.world.get_component(target_id, CombatStats)
                _hp_before = _tgt_cs.current_hp if _tgt_cs else 0
                dead = self.combat_system.deal_damage(entity_id, target_id, "physical")
                _hit_landed = dead or (_tgt_cs and _tgt_cs.current_hp < _hp_before)
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                self._add_rage(entity_id, 5)
                combat_state.enter_combat()
                self._increment_pnq_counter(entity_id, _hit_landed)
                if dead:
                    combat_state.target_entity_id = -1
                    combat_state.is_pursuing = False
                    if auto_move:
                        auto_move.active = False
        elif combat_state.is_pursuing and auto_move and not tile_movement.is_moving:
            # Em combate e fora do alcance: persegue o alvo
            self._auto_move_step(
                entity_id, position, tile_movement,
                pl_tile_x, pl_tile_y, tgt_tile_x, tgt_tile_y, auto_move, dt
            )

    def _auto_move_step(self, entity_id, position, tile_movement,
                        pl_x, pl_y, tgt_x, tgt_y, auto_move, dt):
        """Calcula e executa um passo de movimento em direção ao alvo."""
        auto_move.path_recalc_timer -= dt
        current_tile = (pl_x, pl_y)

        if not auto_move.path or auto_move.path_recalc_timer <= 0:
            # Tenta todos os tiles adjacentes ao alvo, do mais próximo ao mais distante
            adj = [
                (tgt_x + dx, tgt_y + dy)
                for dy in [-1, 0, 1] for dx in [-1, 0, 1]
                if not (dx == 0 and dy == 0)
                and max(abs(dx), abs(dy)) == 1
            ]
            adj.sort(key=lambda t: abs(t[0] - pl_x) + abs(t[1] - pl_y))

            # Exclui o tile do alvo dos obstáculos (o jogador quer chegar adjacente)
            enemy_tiles = self._get_enemy_tiles()
            enemy_tiles.discard((tgt_x, tgt_y))

            auto_move.path = []
            for tile in adj:
                path = self.pathfinding_system.find_path(current_tile, tile,
                                                         dynamic_obstacles=enemy_tiles)
                if path:
                    auto_move.path = path
                    break
            auto_move.path_recalc_timer = self.AUTO_MOVE_RECALC_INTERVAL

        if auto_move.path:
            nx, ny = auto_move.path[0]
            if self.tile_validation_system.is_tile_walkable(entity_id, nx, ny):
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
            path = self.pathfinding_system.find_path((pl_x, pl_y), (gt_x, gt_y),
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
            if self.tile_validation_system.is_tile_walkable(entity_id, nx, ny):
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
                    dead = self.combat_system.deal_damage(entity_id, tid, "physical")
                    combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                    self._add_rage(entity_id, 5)
                    combat_state.enter_combat()
                    self._increment_pnq_counter(entity_id)
                    if dead:
                        combat_state.target_entity_id = -1
                    return

        # Sem alvo no alcance: ataca o inimigo mais próximo
        for eid, _, _, etm in self.world.get_entities_with(Enemy, AIControlled, TileMovement):
            dist = chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y)
            if dist <= self.PLAYER_ATTACK_RANGE:
                self.combat_system.deal_damage(entity_id, eid, "physical")
                combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
                self._add_rage(entity_id, 5)
                if combat_state:
                    combat_state.enter_combat()
                break

    def _space_engage(self, entity_id, tile_movement, combat_stats, combat_state, auto_move):
        """ESPAÇO: seleciona inimigo mais próximo visível na tela, entra em combate e ataca."""
        pl_x = tile_movement.current_tile_x
        pl_y = tile_movement.current_tile_y

        best_eid  = -1
        best_dist = float("inf")
        for eid, epos, _, _, etm, ecs in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats):
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
            combat_state.enter_combat()
        if auto_move:
            auto_move.ground_target = None
            auto_move.path.clear()
            auto_move.path_recalc_timer = 0.0

        # Ataca imediatamente se já estiver no alcance
        if best_dist <= self.PLAYER_ATTACK_RANGE and combat_stats.attack_cooldown_timer <= 0:
            dead = self.combat_system.deal_damage(entity_id, best_eid, "physical")
            combat_stats.attack_cooldown_timer = combat_stats.get_attack_cooldown()
            self._add_rage(entity_id, 5)
            if dead and combat_state:
                combat_state.target_entity_id = -1


# Modificação no EnemyAISystem para integrar o CombatSystem
class EnemyAISystem(System):
    KITING_MIN_DIST      = 3   # tiles: ranged enemy flees if player is this close
    SLEEP_RADIUS_TILES   = 40  # além desta distância (Chebyshev), a AI é completamente suspensa
    MAX_PATHFINDS_PER_FRAME = 4  # limite de chamadas A* por frame (evita travamento com muitos inimigos)
    # Ranged: kite limitado
    KITE_MAX_TILES       = 3    # máximo de tiles por sessão de kite
    KITE_COOLDOWN        = 1.5  # segundos de pausa entre sessões de kite
    # Ranged: cast e velocidade de ataque
    RANGED_CAST_TIME     = 1.0  # segundos parado antes de disparar o projétil
    RANGED_ATTACK_CD_MULT = 1.3  # multiplicador no cooldown de ataque (velocidade menor)

    def __init__(self, world: World, player_entity_id: int,
                 tile_validation_system: TileValidationSystem,
                 pathfinding_system: PathfindingSystem,
                 combat_system: CombatSystem):
        self.world = world
        self.player_entity_id = player_entity_id
        self.tile_validation_system = tile_validation_system
        self.pathfinding_system = pathfinding_system
        self.combat_system = combat_system
        self.proximity_threshold_pixels = 5.0
        self.proximity_threshold_tiles = 1
        self.path_recalc_interval = 0.8
        self._pathfind_budget = 0  # resetado a cada frame

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
        return self.pathfinding_system.find_path(start, end, dynamic_obstacles=dynamic_obstacles)

    def update(self, events: list = None, dt: float = 0) -> None:
        self._pathfind_budget = self.MAX_PATHFINDS_PER_FRAME

        player_position_comp = None
        player_tile_move_comp = None
        player_combat_stats = None
        # Encontra o jogador e seus componentes relevantes
        for _, pos, tm_comp, _, combat_s in self.world.get_entities_with(Position, TileMovement, PlayerControlled, CombatStats):
            player_position_comp = pos
            player_tile_move_comp = tm_comp
            player_combat_stats = combat_s
            break

        # Se o jogador não existe ou não tem CombatStats, os inimigos ficam ociosos.
        if not player_position_comp or not player_combat_stats or player_combat_stats.current_hp <= 0:
            for _, ai_control, tile_movement, combat_stats in self.world.get_entities_with(AIControlled, TileMovement, CombatStats):
                if not tile_movement.is_moving:
                    ai_control.state = "IDLE"
                ai_control.is_blocked = False
                ai_control.blocked_by_entity_id = -1
                # ai_control.path = None # Não precisa limpar o caminho aqui
                if combat_stats.attack_cooldown_timer > 0: # Atualiza cooldown mesmo parado
                    combat_stats.attack_cooldown_timer -= dt
            return

        all_occupied_tiles = self._get_occupied_tiles()

        player_current_tile_x = player_tile_move_comp.current_tile_x
        player_current_tile_y = player_tile_move_comp.current_tile_y

        for enemy_id, enemy_pos, ai_control, initial_pos, detect_radius, tile_movement, enemy_combat_stats in \
            self.world.get_entities_with(Position, AIControlled, InitialPosition, DetectionRadius, TileMovement, CombatStats):

            # Inimigo morto? Pula!
            if enemy_combat_stats.current_hp <= 0:
                continue

            enemy_current_tile_x = tile_movement.current_tile_x
            enemy_current_tile_y = tile_movement.current_tile_y

            # --- Efeitos de estado (stun / fear) ---
            from components import StatusEffects as _SE
            _sfx = self.world.get_component(enemy_id, _SE)
            if _sfx:
                if _sfx.enraged_timer > 0:
                    _sfx.enraged_timer = max(0.0, _sfx.enraged_timer - dt)
                if _sfx.stun_timer > 0:
                    _sfx.stun_timer = max(0.0, _sfx.stun_timer - dt)
                    enemy_combat_stats.attack_cooldown_timer = max(
                        enemy_combat_stats.attack_cooldown_timer, 0.1)
                    continue  # imóvel e sem ataque
                if _sfx.fear_timer > 0:
                    _sfx.fear_timer = max(0.0, _sfx.fear_timer - dt)
                    # Foge do player: move para o tile oposto
                    if not tile_movement.is_moving:
                        ex, ey = tile_movement.current_tile_x, tile_movement.current_tile_y
                        dx_raw = ex - player_current_tile_x
                        dy_raw = ey - player_current_tile_y
                        # Normaliza para -1/0/+1
                        step_x = (1 if dx_raw > 0 else -1) if dx_raw != 0 else 0
                        step_y = (1 if dy_raw > 0 else -1) if dy_raw != 0 else 0
                        for fx, fy in [(ex + step_x, ey + step_y),
                                       (ex + step_x, ey),
                                       (ex, ey + step_y)]:
                            if self.tile_validation_system.is_tile_walkable(enemy_id, fx, fy):
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

            # --- Sleep zone: inimigos longe do jogador são completamente ignorados ---
            # Usa Chebyshev (sem sqrt) para eficiência máxima.
            chebyshev_dist_to_player = max(
                abs(player_current_tile_x - enemy_current_tile_x),
                abs(player_current_tile_y - enemy_current_tile_y)
            )
            if chebyshev_dist_to_player > self.SLEEP_RADIUS_TILES:
                # Garante que o inimigo fica parado e ocioso ao adormecer
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
                continue

            ai_control.is_blocked = False
            ai_control.blocked_by_entity_id = -1
            
            current_enemy_tile = (enemy_current_tile_x, enemy_current_tile_y)

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

            from components import MobSounds as _MS_atk
            _ms_atk = self.world.get_component(enemy_id, _MS_atk)
            _caster_classes = {"Mage", "Mago", "Warlock", "Bruxo"}
            if ai_control.entity_class in _caster_classes:
                _atk_event = "attack_magic"
            elif ai_control.is_ranged:
                _atk_event = "attack_ranged"
            else:
                _atk_event = "attack_melee"

            # ── Ranged: cast timer (fica parado 1s antes de disparar) ──────
            if ai_control.is_ranged and ai_control.ranged_cast_timer > 0:
                _cast_tm  = self.pathfinding_system._get_tilemap_component()
                _cast_los = (_cast_tm is None or self._has_line_of_sight(
                    _cast_tm,
                    enemy_current_tile_x, enemy_current_tile_y,
                    player_current_tile_x, player_current_tile_y
                ))
                if in_attack_range and _cast_los:
                    ai_control.ranged_cast_timer -= dt
                    if ai_control.ranged_cast_timer <= 0:
                        # Cast concluído: dispara
                        ai_control.ranged_cast_timer = 0.0
                        SOUNDS.play_emote_attack(is_player=False, mob_sounds_comp=_ms_atk)
                        SOUNDS.play_mob_sounds(_ms_atk, _atk_event, dedup_key=str(enemy_id))
                        self._spawn_projectile(enemy_id, self.player_entity_id, damage_type_to_use)
                        enemy_combat_stats.attack_cooldown_timer = (
                            enemy_combat_stats.get_attack_cooldown() * self.RANGED_ATTACK_CD_MULT
                        )
                else:
                    ai_control.ranged_cast_timer = 0.0  # LOS/alcance perdido: cancela

            # ── Inicia ataque (cooldown expirou) ──────────────────────────
            if in_attack_range and enemy_combat_stats.attack_cooldown_timer <= 0:
                if ai_control.is_ranged:
                    # Só inicia cast se não estiver já carregando
                    if ai_control.ranged_cast_timer == 0.0:
                        _atk_tm  = self.pathfinding_system._get_tilemap_component()
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
                    self.combat_system.deal_damage(
                        attacker_id=enemy_id,
                        target_id=self.player_entity_id,
                        damage_type=damage_type_to_use
                    )
                    enemy_combat_stats.attack_cooldown_timer = enemy_combat_stats.get_attack_cooldown()

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

            if needs_to_kite:
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

            if in_attack_range and not needs_to_kite:
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
            _tilemap_for_los = self.pathfinding_system._get_tilemap_component()
            _has_los = (
                _tilemap_for_los is None or
                self._has_line_of_sight(
                    _tilemap_for_los,
                    enemy_current_tile_x, enemy_current_tile_y,
                    player_current_tile_x, player_current_tile_y
                )
            )
            if dist_to_player_pixels <= detect_radius.radius and _has_los:
                if ai_control.state == "IDLE":
                    from components import MobSounds as _MS_aggro
                    _ms_aggro = self.world.get_component(enemy_id, _MS_aggro)
                    SOUNDS.play_mob_sounds(_ms_aggro, "aggro", dedup_key=str(enemy_id))
                    ai_control.state       = "AGGRO_DELAY"
                    ai_control.aggro_delay = 1.0
                elif ai_control.state == "AGGRO_DELAY":
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
                                tilemap_comp = self.pathfinding_system._get_tilemap_component()
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

                    if self.tile_validation_system.is_tile_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y):
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
                        if self.tile_validation_system.is_tile_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y):
                            start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                            ai_control.path.pop(0)
                        else:
                            ai_control.path = None
                            ai_control.path_recalc_timer = 0.0
                    else:
                        ai_control.state = "IDLE"
                else:
                    ai_control.state = "IDLE"
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

        # Cor e tipo de projétil por classe do atacante
        ai_ctrl = self.world.get_component(attacker_id, AIControlled)
        entity_class = ai_ctrl.entity_class if ai_ctrl else ""
        if entity_class == "Warlock":
            proj_color = (160, 0, 220)
            is_arrow   = False
        elif entity_class in ("Hunter", "Arqueiro"):
            proj_color = (120, 80, 40)
            is_arrow   = True
        elif entity_class == "Mage":
            proj_color = (255, 80, 0)
            is_arrow   = False
        else:
            proj_color = (200, 200, 50)
            is_arrow   = False

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
            tilemap_comp = self.pathfinding_system._get_tilemap_component()
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
            if self.tile_validation_system.is_tile_walkable(enemy_id, nx, ny):
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
                    from components import Renderable as _Rend
                    _rend = self.world.get_component(entity_id, _Rend)
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
                    tile_movement.is_dash   = False
                    tile_movement.progress  = 0.0
                    # Som de passo — apenas player, sem dash, respeitando intervalo mínimo
                    if (not tile_movement.is_dash
                            and self._footstep_timer <= 0
                            and self.world.get_component(entity_id, PlayerControlled) is not None):
                        SOUNDS.play_footstep()
                        self._footstep_timer = self.FOOTSTEP_INTERVAL
            # Tick do slow_timer (debuff de velocidade)
            if tile_movement.slow_timer > 0:
                tile_movement.slow_timer = max(0.0, tile_movement.slow_timer - dt)
                tile_movement.debilitate_elapsed += dt
                if tile_movement.slow_timer <= 0:
                    tile_movement.slow_mult = 1.0
                    tile_movement.debilitate_elapsed = 0.0
            else:
                tile_movement.debilitate_elapsed = 0.0


class RenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.screen = screen

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
        from components import FogOfWar
        from tileset import TILE_SIZE as _FOG_TS
        _fog_visible: set | None = None
        for _, _fog in self.world.get_entities_with(FogOfWar):
            _fog_visible = _fog.visible
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
                    etx = int(position.x / _FOG_TS)
                    ety = int(position.y / _FOG_TS)
                    if (etx, ety) not in _fog_visible:
                        continue
            foot_y = position.y + renderable.height / 2
            drawables.append((foot_y, "entity", entity_id, position, renderable, combat_stats))

        # Tile-objetos (árvores, arbustos, pedras grandes, etc.)
        if world_objects:
            for obj in world_objects:
                # Oculta objetos de tile fora do campo de visão
                if _fog_visible is not None:
                    if (obj.get("tile_x", -1), obj.get("tile_y", -1)) not in _fog_visible:
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
                    self.screen.blit(obj["sprite"], (sx, sy))
                else:
                    pygame.draw.rect(self.screen, obj["color"], (sx, sy, w, h))
                continue

            # ── Entidade ──────────────────────────────────────────────────────
            _, _, entity_id, position, renderable, combat_stats = item

            draw_x = position.x - camera_offset_x
            draw_y = position.y - camera_offset_y

            rect = pygame.Rect(
                int(draw_x - renderable.width / 2),
                int(draw_y - renderable.height / 2),
                renderable.width,
                renderable.height
            )
            pygame.draw.rect(self.screen, renderable.color, rect)

            # Borda amarela no alvo selecionado
            if entity_id == target_id:
                pygame.draw.rect(self.screen, (255, 220, 0), rect, 2)

            # Barra de HP acima de entidades com CombatStats
            if combat_stats and combat_stats.max_hp > 0:
                ratio = max(0.0, combat_stats.current_hp / combat_stats.max_hp)
                bar_w = renderable.width
                bar_h = 4
                bar_x = int(draw_x - renderable.width / 2)
                bar_y = int(draw_y - renderable.height / 2) - 7
                pygame.draw.rect(self.screen, (80, 0, 0), (bar_x, bar_y, bar_w, bar_h))
                pygame.draw.rect(self.screen, (0, 200, 60), (bar_x, bar_y, int(bar_w * ratio), bar_h))

                # Ícones de status (quadradinhos coloridos acima da HP bar)
                from components import StatusEffects as _SFX_R, CombatState as _CS_R
                _sfx = self.world.get_component(entity_id, _SFX_R)
                _cst = self.world.get_component(entity_id, _CS_R)
                _icons = []
                if (_sfx and _sfx.stun_timer > 0) or (_cst and _cst.is_stunned and _cst.stun_timer > 0):
                    _icons.append((255, 220, 0))    # amarelo  = atordoado
                if _sfx and _sfx.fear_timer > 0:
                    _icons.append((180, 60, 220))   # roxo     = medo
                if _sfx and _sfx.enraged_timer > 0:
                    _icons.append((255, 120, 0))    # laranja  = enraivecido
                if _icons:
                    _isz, _gap = 6, 2
                    _tw = len(_icons) * (_isz + _gap) - _gap
                    _ix = int(draw_x - _tw / 2)
                    _iy = bar_y - _isz - 2
                    for _col in _icons:
                        pygame.draw.rect(self.screen, _col, (_ix, _iy, _isz, _isz))
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
        self.world  = world
        self.screen = screen
        from tileset import TILE_SIZE as _TS
        _tw = screen.get_width()  // _TS + 2
        _th = screen.get_height() // _TS + 2
        # Cache de surface — pré-alocada; reconstruída apenas quando a câmera cruza fronteira de tile
        self._cache_surf    = pygame.Surface((_tw * _TS, _th * _TS))
        self._cache_tile_x:    int = -99999
        self._cache_tile_y:    int = -99999
        self._cache_tiles_w:   int = _tw
        self._cache_tiles_h:   int = _th
        # Fog of War: surface semi-transparente para tiles explorados mas não visíveis
        self._fog_explored_surf: pygame.Surface = pygame.Surface((_TS, _TS), pygame.SRCALPHA)
        self._fog_explored_surf.fill((0, 0, 0, 160))

    def invalidate_cache(self) -> None:
        """Força reconstrução do cache no próximo frame (chamar após troca de mapa)."""
        self._cache_tile_x = -99999
        self._cache_tile_y = -99999
        from tile_sprite_manager import TILE_SPRITES
        TILE_SPRITES.invalidate()

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            tile_size = tilemap_comp.tile_size

            # Inteiros para evitar gaps de 1px entre tiles (câmera smooth)
            cam_x = int(camera_offset_x)
            cam_y = int(camera_offset_y)

            # Tile de origem (top-left) e offset sub-tile dentro do tile
            tile_ox = cam_x // tile_size
            tile_oy = cam_y // tile_size
            sub_x   = cam_x - tile_ox * tile_size
            sub_y   = cam_y - tile_oy * tile_size

            # Tiles necessários para cobrir a tela + 1 coluna/linha de borda
            tiles_w = self.screen.get_width()  // tile_size + 2
            tiles_h = self.screen.get_height() // tile_size + 2

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

                from tile_sprite_manager import TILE_SPRITES
                rows   = tilemap_comp.tile_matrix
                map_h  = tilemap_comp.map_height_tiles
                map_w  = tilemap_comp.map_width_tiles
                for ty in range(tiles_h):
                    for tx in range(tiles_w):
                        rx, ry = tile_ox + tx, tile_oy + ty
                        dest = (tx * tile_size, ty * tile_size, tile_size, tile_size)
                        if 0 <= ry < map_h and 0 <= rx < map_w:
                            tile_type = rows[ry][rx]
                            if tile_type.overlay_height > 0:
                                # Tile-objeto: mostra só a cor base no pass 1.
                                # O sprite completo (com overlay) é desenhado no pass 2 (Y-sort).
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
            self.screen.blit(self._cache_surf, (-sub_x, -sub_y))

            # ── Fog of War overlay ────────────────────────────────────────────
            from components import FogOfWar
            fog_comp = None
            for _, fog in self.world.get_entities_with(FogOfWar):
                fog_comp = fog
                break

            if fog_comp is not None:
                explored = fog_comp.explored
                visible  = fog_comp.visible
                exp_surf = self._fog_explored_surf
                for ty in range(tiles_h):
                    for tx in range(tiles_w):
                        rx, ry = tile_ox + tx, tile_oy + ty
                        if (rx, ry) in visible:
                            continue
                        sx = tx * tile_size - sub_x
                        sy = ty * tile_size - sub_y
                        if (rx, ry) in explored:
                            self.screen.blit(exp_surf, (sx, sy))
                        else:
                            pygame.draw.rect(self.screen, (0, 0, 0),
                                             (sx, sy, tile_size, tile_size))

    def get_world_objects(self, camera_offset_x: float, camera_offset_y: float) -> list:
        """
        Retorna lista de tile-objetos visíveis (overlay_height > 0) para o pass 2 (Y-sort).
        Cada item: dict com sort_y, screen_x, screen_y, sprite, color, width, height.
        """
        from tile_sprite_manager import TILE_SPRITES
        objects = []
        cam_x = int(camera_offset_x)
        cam_y = int(camera_offset_y)

        for _, tilemap_comp in self.world.get_entities_with(Tilemap):
            tile_size  = tilemap_comp.tile_size
            rows       = tilemap_comp.tile_matrix
            map_h      = tilemap_comp.map_height_tiles
            map_w      = tilemap_comp.map_width_tiles

            # Janela de tiles visíveis (margem extra para overlay que vaza acima)
            tile_ox = cam_x // tile_size
            tile_oy = cam_y // tile_size
            tiles_w = self.screen.get_width()  // tile_size + 2
            tiles_h = self.screen.get_height() // tile_size + 2

            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    rx, ry = tile_ox + tx, tile_oy + ty
                    if not (0 <= ry < map_h and 0 <= rx < map_w):
                        continue
                    tile_type = rows[ry][rx]
                    if tile_type.overlay_height <= 0:
                        continue

                    total_h = tile_size + tile_type.overlay_height
                    # sort_y: Y do "pé" do objeto em espaço de mundo
                    sort_y_world = (ry + 1) * tile_size
                    # Posição de tela do canto superior esquerdo do sprite
                    scr_x = rx * tile_size - cam_x
                    scr_y = sort_y_world - cam_y - total_h

                    sprite = TILE_SPRITES.get(tile_type, rx, ry)
                    objects.append({
                        "sort_y":   sort_y_world,
                        "screen_x": scr_x,
                        "screen_y": scr_y,
                        "sprite":   sprite,
                        "color":    tile_type.color,
                        "width":    tile_size,
                        "height":   total_h,
                        "tile_x":   rx,
                        "tile_y":   ry,
                    })
        return objects


class FogSystem(System):
    """
    Atualiza o campo de visão do jogador por shadowcasting recursivo (8 octantes).

    Executa apenas quando o jogador muda de tile — custo O(radius²) por frame
    de movimento, zero nos frames sem deslocamento.
    """

    def __init__(self, world: World) -> None:
        self.world = world

    def update(self, events: list = None, dt: float = 0) -> None:
        from components import FogOfWar, TileMovement, Tilemap
        from fov import compute_fov

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
            return rows[y][x].is_solid

        for _, fog, tile_move in self.world.get_entities_with(FogOfWar, TileMovement):
            px, py = tile_move.current_tile_x, tile_move.current_tile_y
            if (px, py) == fog._last_tile:
                return
            fog._last_tile = (px, py)
            fog.visible = compute_fov(px, py, fog.radius, is_blocking)
            fog.explored.update(fog.visible)
            return   # apenas um FogOfWar no jogo (jogador)


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
        from components import SpawnZone, Enemy, Tilemap

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
            if not zone.respawn_timers and alive < zone.max_count:
                needed = zone.max_count - alive
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
                else:
                    still_waiting.append(t)
            zone.respawn_timers = still_waiting

        # --- Drena fila: máximo MAX_SPAWNS_PER_FRAME criações por frame ---
        for _ in range(min(self.MAX_SPAWNS_PER_FRAME, len(self._spawn_queue))):
            zone_eid, zone, dx, dy = self._spawn_queue.pop(0)
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
        from components import SpawnZoneOwner
        from entity_factory import create_enemy
        import random as _random
        is_ranged = zone.enemy_type == "ranged"
        level = _random.randint(zone.level_min, zone.level_max)
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
        from entity_factory import create_enemy
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

    PANEL_W   = 900
    PANEL_H   = 510
    ROW_H     = 46
    ICON_S    = 36
    MAX_ROWS  = 8
    LEFT_W    = 430
    RIGHT_W   = 430
    GAP       = 10
    SELL_RATIO = 0.4   # 40% do valor do item
    MAX_HISTORY = 20

    _RARITY_COLORS = {
        "common":   (200, 200, 200),
        "uncommon": ( 30, 200,  30),
        "rare":     ( 80, 140, 255),
        "epic":     (180,  50, 255),
    }

    def __init__(self, world: World, player_entity: int, screen):
        self.world         = world
        self.player_entity = player_entity
        self.screen        = screen
        self.open_merchant_id: int     = -1
        self._pending_merchant_id: int = -1   # aguardando jogador chegar
        self._right_click_consumed: bool = False
        self.transaction_history: list = []
        self._shop_scroll: int = 0
        self._bag_scroll:  int = 0
        self.pending_tooltip = None
        self._open_cooldown: float = 0.0  # impede compra/venda logo após abrir a loja

        SW, SH = screen.get_size()
        self._font_sm = pygame.font.Font(None, 20)
        self._font_md = pygame.font.Font(None, 26)
        self._font_lg = pygame.font.Font(None, 32)

    @property
    def is_open(self) -> bool:
        return self.open_merchant_id != -1

    def _panel_origin(self):
        SW, SH = self.screen.get_size()
        return (SW - self.PANEL_W) // 2, (SH - self.PANEL_H) // 2

    def _get_cam(self):
        from components import Camera
        SW, SH = self.screen.get_size()
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
                wx, wy = mx + cam_x, my + cam_y
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

    def _buy(self, entry: dict) -> None:
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet:
            return
        price = entry["price"]
        if wallet.gold < price:
            return

        # Tenta empilhar em slot existente do mesmo item
        preview = entry["factory"]()
        if getattr(preview, "max_stack", 1) > 1:
            for existing in inv.items:
                if existing.name == preview.name and existing.stack < existing.max_stack:
                    wallet.gold -= price
                    existing.stack += 1
                    self.transaction_history.append({"type": "buy", "item": existing, "price": price})
                    if len(self.transaction_history) > self.MAX_HISTORY:
                        self.transaction_history.pop(0)
                    return

        # Sem slot empilhável — ocupa novo slot
        if len(inv.items) >= inv.max_slots:
            return
        item = entry["factory"]()
        wallet.gold -= price
        inv.items.append(item)
        self.transaction_history.append({"type": "buy", "item": item, "price": price})
        if len(self.transaction_history) > self.MAX_HISTORY:
            self.transaction_history.pop(0)

    def _sell(self, item_idx: int) -> None:
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet or item_idx >= len(inv.items):
            return
        item     = inv.items[item_idx]
        sell_val = self._sell_price(item)
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
        from tileset import TILE_SIZE as TS
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TS), int(pos.y / TS)) if pos else None

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
        from merchant_data import SHOPS

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
        body_y  = y0 + 40 + 36 + 30   # header + undo bar + col headers

        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return

            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            mx, my = event.pos

            # Botão fechar
            close_r = pygame.Rect(x0 + self.PANEL_W - 36, y0 + 4, 32, 32)
            if event.button == 1 and close_r.collidepoint(mx, my):
                self._close()
                return

            # Botão desfazer
            undo_r = pygame.Rect(x0 + self.GAP, y0 + 42, 120, 28)
            if event.button == 1 and undo_r.collidepoint(mx, my):
                self._undo()
                return

            # Painel esquerdo: comprar (clique direito)
            if event.button == 3 and mx < mid_x and self._open_cooldown <= 0:
                for i, entry in enumerate(stock):
                    vis_i = i - self._shop_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(x0 + self.GAP,
                                        body_y + vis_i * self.ROW_H,
                                        self.LEFT_W - 4, self.ROW_H - 2)
                        if r.collidepoint(mx, my):
                            self._buy(entry)
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

    # ------------------------------------------------------------------
    # Render — NPC no mundo
    # ------------------------------------------------------------------

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        """Desenha indicador flutuante 'LOJA' acima de cada NPC comerciante."""

        for eid, pos, rend, _ in self.world.get_entities_with(
                Position, Renderable, Merchant):
            sx = int(pos.x - cam_x)
            sy = int(pos.y - cam_y)
            # Indicador acima do NPC
            lbl = self._font_sm.render("LOJA", True, (255, 240, 120))
            lx  = sx - lbl.get_width() // 2
            ly  = sy - rend.height // 2 - lbl.get_height() - 2
            self.screen.blit(lbl, (lx, ly))

    # ------------------------------------------------------------------
    # Render — painel de loja
    # ------------------------------------------------------------------

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        self.pending_tooltip = None
        if not self.is_open:
            return

        from merchant_data import SHOPS

        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return

        shop  = SHOPS.get(merch.shop_id, {})
        stock = shop.get("stock", [])
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        bag    = inv.items if inv else []

        SW, SH  = self.screen.get_size()
        x0, y0  = self._panel_origin()
        W, H    = self.PANEL_W, self.PANEL_H
        mid_x   = x0 + self.GAP + self.LEFT_W
        mx, my  = pygame.mouse.get_pos()

        # Overlay escuro
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 160))
        self.screen.blit(ov, (0, 0))

        # Fundo do painel
        bg = pygame.Surface((W, H), pygame.SRCALPHA)
        bg.fill((15, 10, 5, 235))
        self.screen.blit(bg, (x0, y0))
        pygame.draw.rect(self.screen, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        # --- Header ---
        title = self._font_lg.render(f"  {shop.get('name', 'Comerciante')}", True, (255, 220, 120))
        self.screen.blit(title, (x0 + 8, y0 + 8))

        close_r   = pygame.Rect(x0 + W - 36, y0 + 4, 32, 32)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (100, 35, 35), close_r, border_radius=3)
        xs = self._font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        pygame.draw.line(self.screen, (90, 70, 40), (x0 + 4, y0 + 40), (x0 + W - 4, y0 + 40))

        # --- Barra de desfazer ---
        undo_y   = y0 + 42
        undo_r   = pygame.Rect(x0 + self.GAP, undo_y, 130, 28)
        has_hist = bool(self.transaction_history)
        undo_hov = undo_r.collidepoint(mx, my) and has_hist
        undo_bg  = (55, 80, 55) if undo_hov else ((38, 55, 38) if has_hist else (28, 28, 28))
        undo_col = (150, 220, 150) if has_hist else (70, 70, 70)
        pygame.draw.rect(self.screen, undo_bg,  undo_r, border_radius=3)
        pygame.draw.rect(self.screen, undo_col, undo_r, 1, border_radius=3)
        self.screen.blit(self._font_sm.render("↩ Desfazer", True, undo_col),
                         (undo_r.x + 8, undo_r.y + 7))

        if self.transaction_history:
            tx  = self.transaction_history[-1]
            if tx["type"] == "buy":
                desc = f"Ultima: comprou {tx['item'].name} por {tx['price']}g"
            else:
                desc = f"Ultima: vendeu {tx['item'].name} por {tx['sell_value']}g"
            self.screen.blit(self._font_sm.render(desc, True, (150, 150, 150)),
                             (x0 + self.GAP + 138, undo_y + 7))

        pygame.draw.line(self.screen, (90, 70, 40), (x0 + 4, y0 + 78), (x0 + W - 4, y0 + 78))

        # Divisor vertical
        pygame.draw.line(self.screen, (90, 70, 40), (mid_x, y0 + 40), (mid_x, y0 + H - 36))

        # --- Cabeçalhos das colunas ---
        col_y   = y0 + 82
        hdr_col = (160, 130, 80)
        hint    = (90, 80, 60)
        self.screen.blit(self._font_md.render(f"LOJA  ({len(stock)} itens)", True, hdr_col),
                         (x0 + self.GAP + 4, col_y))
        self.screen.blit(self._font_sm.render("clique dir. p/ comprar", True, hint),
                         (x0 + self.GAP + 4, col_y + 20))
        self.screen.blit(self._font_md.render(f"MOCHILA  ({len(bag)}/{inv.max_slots if inv else 0})", True, hdr_col),
                         (mid_x + self.GAP + 4, col_y))
        self.screen.blit(self._font_sm.render("clique dir. p/ vender", True, hint),
                         (mid_x + self.GAP + 4, col_y + 20))

        body_y = y0 + 110
        pygame.draw.line(self.screen, (70, 55, 30), (x0 + 4, body_y - 2), (x0 + W - 4, body_y - 2))

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

            pygame.draw.rect(self.screen, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.screen, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(preview.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + 4, r.y + (self.ROW_H - 2 - self.ICON_S) // 2,
                                  self.ICON_S, self.ICON_S)
            pygame.draw.rect(self.screen, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(preview), self.ICON_S)
            if icon_surf:
                self.screen.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.screen, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.screen, rar_col, (ic_r.right - 4, ic_r.bottom - 4), 3)

            name_col = rar_col if (can_afford and not inv_full) else (90, 70, 70)
            self.screen.blit(self._font_sm.render(preview.name,      True, name_col),
                             (ic_r.right + 6, r.y + 6))
            self.screen.blit(self._font_sm.render(preview.item_type, True, (95, 85, 65)),
                             (ic_r.right + 6, r.y + 24))

            price_col = (255, 215, 0) if (can_afford and not inv_full) else (130, 70, 70)
            ps = self._font_sm.render(f"{entry['price']}g", True, price_col)
            self.screen.blit(ps, (r.right - ps.get_width() - 8, r.y + 14))

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
            pygame.draw.rect(self.screen, (45, 35, 20), (sb_x, body_y, 5, sb_h), border_radius=2)
            pygame.draw.rect(self.screen, (140, 110, 60), (sb_x, ty, 5, th), border_radius=2)

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
            pygame.draw.rect(self.screen, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.screen, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + 4, r.y + (self.ROW_H - 2 - self.ICON_S) // 2,
                                  self.ICON_S, self.ICON_S)
            pygame.draw.rect(self.screen, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(item), self.ICON_S)
            if icon_surf:
                self.screen.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.screen, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.screen, rar_col, (ic_r.right - 4, ic_r.bottom - 4), 3)

            stack = getattr(item, "stack", 1)
            name_label = f"{item.name}" if stack <= 1 else f"{item.name} x{stack}"
            self.screen.blit(self._font_sm.render(name_label, True, rar_col),
                             (ic_r.right + 6, r.y + 6))
            slot_label = item.slot if item.slot else item.item_type
            self.screen.blit(self._font_sm.render(slot_label, True, (95, 85, 65)),
                             (ic_r.right + 6, r.y + 24))

            sp     = self._sell_price(item)
            sp_s   = self._font_sm.render(f"+{sp}g", True, (120, 200, 100))
            self.screen.blit(sp_s, (r.right - sp_s.get_width() - 8, r.y + 14))

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
            pygame.draw.rect(self.screen, (45, 35, 20), (sb_x, body_y, 5, sb_h), border_radius=2)
            pygame.draw.rect(self.screen, (140, 110, 60), (sb_x, ty, 5, th), border_radius=2)

        # --- Footer: ouro do jogador ---
        foot_y = y0 + H - 34
        pygame.draw.line(self.screen, (90, 70, 40), (x0 + 4, foot_y), (x0 + W - 4, foot_y))
        if wallet:
            gold_s = self._font_md.render(f"Seu ouro: {wallet.gold}g", True, (255, 215, 0))
            self.screen.blit(gold_s, (x0 + W // 2 - gold_s.get_width() // 2, foot_y + 6))


class ConsumableSystem(System):
    """Processa a barra de consumíveis (keybinds + uso) e ActiveRegen (HoT)."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        from components import ActiveRegen, ConsumableBar, Inventory, CombatState

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
                if pos_c and healed > 0:
                    FLT.add(f"+{healed}", pos_c.x, pos_c.y - 16,
                            (80, 220, 120), "small", eid)
                if regen.ticks_remaining <= 0:
                    to_remove.append(eid)

        for eid in to_remove:
            self.world.remove_component(eid, ActiveRegen)

    def _use_consumable(self, entity_id: int, item_name: str, cbar) -> None:
        from components import Inventory, CombatState, CombatStats as _CS, \
                               Position as _Pos, ActiveRegen, ConsumableBar

        inv   = self.world.get_component(entity_id, Inventory)
        cs    = self.world.get_component(entity_id, _CS)
        pos_c = self.world.get_component(entity_id, _Pos)
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
            from floating_text import WARN as _W
            _W.add("Não pode usar em combate")
            return

        # Não pode ser usado com HP cheio
        if cs.current_hp >= cs.max_hp:
            from floating_text import WARN as _W
            _W.add("HP já está cheio")
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

        # Cooldown global
        cbar.global_cooldown = ConsumableBar.GCD_DURATION


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

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.screen = screen
        self.open_corpse_id: int         = -1
        self.pending_loot_corpse_id: int = -1
        self.pending_tooltip             = None  # lido por GameEngine no fim do frame
        self.font_sm = pygame.font.Font(None, 20)
        self.font_md = pygame.font.Font(None, 24)
        self._modal_x       = 0   # posição X do modal (definida ao abrir)
        self._modal_y       = 0   # posição Y do modal
        self._scroll_offset = 0   # índice da primeira linha visível
        self._pending_cursor = (0, 0)  # cursor quando o loot foi solicitado

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
        for _, cam, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - cam.offset_x, cam_pos.y - cam.offset_y
        return 0.0, 0.0

    def _try_open_corpse(self, mx: int, my: int) -> None:
        cam_x, cam_y = self._get_camera_offset()
        world_x = mx + cam_x
        world_y = my + cam_y

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
        sw, sh = self.screen.get_size()
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
                    from components import Wallet
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
                        if len(inv.items) < inv.max_slots:
                            inv.items.append(item)
                            corpse.loot.pop(i)
                            col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                            LOG.add(f"Coletado: {item.name} ({item.rarity})", col)
                            SOUNDS.play_ui("loot_item")
                            # Corrige scroll se necessário
                            total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                            self._scroll_offset = min(self._scroll_offset, max(0, total - self.MAX_ROWS))
                        else:
                            LOG.add("Inventario cheio!", (255, 160, 0))
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
                    from components import Equipment, Inventory, CombatStats as _CS_eq
                    equip        = None
                    inv          = None
                    combat_stats = None
                    for _, eq, _pc in self.world.get_entities_with(Equipment, PlayerControlled):
                        equip = eq
                        break
                    for _, iv, _pc in self.world.get_entities_with(Inventory, PlayerControlled):
                        inv = iv
                        break
                    for _, cs, _pc in self.world.get_entities_with(_CS_eq, PlayerControlled):
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

                    # Arma de duas mãos → desequipa offhand
                    if getattr(item, 'two_handed', False) and target_slot == "mainhand":
                        old_oh = equip.slots.get("offhand")
                        if old_oh and inv and len(inv.items) < inv.max_slots:
                            for mod in old_oh.modifiers:
                                combat_stats.remove_modifier(mod)
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
                                combat_stats.remove_modifier(mod)
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
                        combat_stats.add_modifier(mod)
                    col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                    LOG.add(f"Equipado: {item.name} ({item.rarity})", col)
                    SOUNDS.play_ui("equip_item")
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
            pygame.draw.ellipse(self.screen, color,
                                (int(draw_x - 10), int(draw_y - 6), 20, 12))
            pygame.draw.ellipse(self.screen, (80, 55, 25),
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
        self.screen.blit(bg, modal.topleft)
        pygame.draw.rect(self.screen, self.BORDER_COLOR, modal, 2, border_radius=4)

        # --- Barra de título ---
        title = self.font_sm.render("Loot", True, self.BORDER_COLOR)
        self.screen.blit(title, (modal.x + self.PAD, modal.y + (self.TITLE_H - title.get_height()) // 2))

        # Botão X
        close_r = self._close_btn_rect(modal)
        pygame.draw.rect(self.screen, (90, 30, 30), close_r, border_radius=2)
        x_surf = self.font_sm.render("X", True, (220, 100, 100))
        self.screen.blit(x_surf, (close_r.centerx - x_surf.get_width() // 2,
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
            pygame.draw.rect(self.screen, (40, 30, 18),
                             (track_x, track_y, self.SCROLL_W, track_h), border_radius=3)
            # Thumb
            thumb_h = max(20, track_h * self.MAX_ROWS // total)
            max_scroll = total - self.MAX_ROWS
            thumb_y = track_y + (track_h - thumb_h) * self._scroll_offset // max(1, max_scroll)
            pygame.draw.rect(self.screen, (130, 100, 55),
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
                pygame.draw.rect(self.screen, (70, 55, 15) if hovered else (28, 20, 8), rr, border_radius=3)
                pygame.draw.rect(self.screen, (200, 170, 50) if hovered else (100, 80, 20), rr, 1, border_radius=3)
                icon_r = pygame.Rect(rr.x + 4, rr.centery - self.ICON_S // 2, self.ICON_S, self.ICON_S)
                r_out = self.ICON_S // 2
                r_in  = max(1, r_out - 4)
                pygame.draw.circle(self.screen, (180, 140, 0),  icon_r.center, r_out)
                pygame.draw.circle(self.screen, (255, 215, 0),  icon_r.center, r_in)
                pygame.draw.circle(self.screen, (120, 90, 0),   icon_r.center, r_out, 1)
                g_surf = self.font_sm.render("G", True, (120, 90, 0))
                self.screen.blit(g_surf, (icon_r.centerx - g_surf.get_width() // 2,
                                          icon_r.centery - g_surf.get_height() // 2))
                tx = icon_r.right + 8
                ty = rr.centery - self.font_md.get_height() // 2
                self.screen.blit(self.font_md.render(f"{corpse.coins} moedas", True, (255, 215, 0)), (tx, ty))
                if hovered:
                    self.pending_tooltip = (mx, my, "Moedas",
                                            [(f"{corpse.coins} moedas disponíveis", (255, 215, 0)),
                                             ("Clique p/ coletar tudo", (140, 140, 140))])

            else:
                # --- Linha de item ---
                item = entry[1]
                bg_col = self.HOVER_COLOR if hovered else (28, 20, 8)
                pygame.draw.rect(self.screen, bg_col, rr, border_radius=3)

                icon_r = pygame.Rect(rr.x + 4, rr.centery - self.ICON_S // 2, self.ICON_S, self.ICON_S)
                icon_surf = ICONS.get(ICONS.item_key(item), self.ICON_S)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb = self.RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb, icon_r, border_radius=2)

                rc = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                tx = icon_r.right + 8
                name_surf = self.font_md.render(item.name, True, rc)
                sub_surf  = self.font_sm.render(f"{item.item_type}  •  {item.slot}", True, (130, 115, 95))
                total_h   = name_surf.get_height() + 2 + sub_surf.get_height()
                ty = rr.centery - total_h // 2
                self.screen.blit(name_surf, (tx, ty))
                self.screen.blit(sub_surf,  (tx, ty + name_surf.get_height() + 2))

                border_col = (180, 140, 60) if hovered else (55, 40, 22)
                pygame.draw.rect(self.screen, border_col, rr, 1, border_radius=3)

                if hovered:
                    hovered_item = item

        # Vazio
        if not virtual:
            empty = self.font_sm.render("(vazio)", True, (120, 100, 80))
            rr = self._row_rect(modal, 0)
            self.screen.blit(empty, (rr.x + 4, rr.centery - empty.get_height() // 2))

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

    def __init__(self, world: World, player_entity_id: int, combat_system: CombatSystem,
                 tile_validation_system: "TileValidationSystem" = None,
                 screen: "pygame.Surface | None" = None):
        self.world = world
        self.player_entity_id = player_entity_id
        self.combat_system = combat_system
        self.tile_validation_system = tile_validation_system
        self.screen = screen

    def _is_on_screen(self, pos: "Position") -> bool:
        if self.screen is None or pos is None:
            return True
        sw = self.screen.get_width()
        sh = self.screen.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            cam_x = cam_pos.x - sw / 2
            cam_y = cam_pos.y - sh / 2
            sx = pos.x - cam_x
            sy = pos.y - cam_y
            return 0 <= sx <= sw and 0 <= sy <= sh
        return True

    def update(self, events: list = None, dt: float = 0) -> None:
        from components import PlayerSkills
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
                if tile_move:
                    self._fatiador_aoe_tick(tile_move)
            if char_stats.fatiador_timer <= 0:
                LOG.add("Fatiador de Corpos terminou.", (200, 160, 100))

        if not events:
            return

        combat_state = self.world.get_component(self.player_entity_id, CombatState)
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
        from components import PlayerSkills as _PS
        combat_state = self.world.get_component(self.player_entity_id, CombatState)
        if combat_state and not combat_state.can_act():
            return False

        player_skills = self.world.get_component(self.player_entity_id, _PS)

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

        # Sempre seleciona alvo ao pressionar skill, mesmo em cooldown
        self._resolve_target(combat_state, tile_move)

        # Qualquer tentativa de usar skill inicia combate e perseguição, independente de cooldown
        if isinstance(combat_state, CombatState):
            combat_state.enter_combat()
            combat_state.is_pursuing = True

        if not skill.is_ready():
            from floating_text import WARN as _WARN
            if skill.current_cooldown > 0:
                _WARN.add(f"Em recarga ({skill.current_cooldown:.1f}s)")
            elif skill.max_charges > 0:
                _WARN.add("Sem cargas")
            return False

        # Habilidades de talento usam handler dinâmico
        if skill.handler:
            handler = getattr(self, f"_talent_{skill.handler}", None)
            if handler:
                SOUNDS.play_emote_attack(is_player=True)
                success = handler(skill, combat_stats, combat_state, tile_move)
                if success:
                    if skill.sound_name:
                        SOUNDS.play_skill(skill.sound_name)
                    if player_skills:
                        player_skills.gcd_timer = _PS.GCD_DURATION
                return bool(success)
            else:
                LOG.add(f"{skill.name}: handler '{skill.handler}' não encontrado.", (180, 60, 60))
            return False

        # Habilidades de config (skill_config.py) — dispatch por skill_id
        if skill.skill_id:
            handler_fn = getattr(self, f"_skill_{skill.skill_id}", None)
            if handler_fn:
                success = handler_fn(skill, combat_stats, combat_state, tile_move)
                if success:
                    if skill.sound_name:
                        SOUNDS.play_skill(skill.sound_name)
                    if player_skills:
                        player_skills.gcd_timer = _PS.GCD_DURATION
                return bool(success)
            else:
                LOG.add(f"{skill.name}: sem implementacao para '{skill.skill_id}'.", (180, 60, 60))
            return False

        return False

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
        # Auto-seleciona o inimigo mais próximo visível na tela
        px, py    = tile_move.current_tile_x, tile_move.current_tile_y
        best_id   = -1
        best_dist = float("inf")
        for eid, epos, _, _, etm, ecs in self.world.get_entities_with(
                Position, Enemy, AIControlled, TileMovement, CombatStats):
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
            combat_state.enter_combat()
        return best_id

    # Todos os handlers (_skill_* e _talent_*) estão em skill_handlers.py via SkillHandlers.
