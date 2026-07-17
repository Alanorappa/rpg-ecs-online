"""
world_systems.py — Sistemas ECS de gameplay HEADLESS (cliente E servidor).

Extraído de systems.py (problema F, PROBLEMAS_ARQUITETURA.md): o servidor
importava EnemyAISystem/CombatSystem/etc. de systems.py e arrastava
pygame/SOUNDS/FLT inteiros. Este módulo NÃO importa pygame no topo —
efeitos visuais/sonoros passam pela façade fx.py (no-op no servidor,
vinculada aos gerenciadores reais pelo cliente via fx.bind_client_fx()).

Regras deste módulo:
- NENHUM import de pygame/fonts/icon_manager/ui_* no topo (headless puro).
  A única exceção tolerada é pygame DENTRO de métodos render() — que só
  o cliente chama.
- systems.py re-exporta tudo daqui — código cliente segue importando de
  systems; código de servidor deve importar daqui diretamente.
- Nó restante do problema F: SkillSystem (input/UI, fica em systems.py) —
  o servidor ainda o importa para os handlers; a separação completa exige
  um ServerSkillSystem sobre SkillHandlers (sessão dedicada).
"""
from __future__ import annotations
import math
import heapq
import random

from engine.core_systems import (apply_effect, apply_damage_core,
                          StatusEffectSystem as _CoreStatusEffectSystem,
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
                       AoeTargeting, RemoteControlled, GhostState, MapLocation, Combatant
from engine.world import World
from engine.tileset import TILE_SIZE, OBJECT_MAPPING
from engine.utils import chebyshev, start_tile_movement
from engine.damage_calculator import resolve_attack_outcome, calculate_base_damage
from ui.combat_log import LOG
from engine.fx import FLT, PROC, WARN, SOUNDS, DASH_TRAIL
from content.status_effects_data import EFFECT_DEFS
from content.loot_tables import roll_loot, roll_mob_loot, roll_coins, roll_mob_coins
from engine.entity_factory import create_corpse, create_enemy
from content.enemy_abilities_data import ABILITY_DEFS
import engine.quest_events as quest_events
from engine.quest_events import fire as quest_fire
from engine.stat_fns import add_modifier, remove_modifier, add_timed_modifier, enter_combat
from engine.faction_system import is_hostile, can_engage


# ── Registro de serviços ─────────────────────────────────────────────────────
# Elimina acoplamento direto entre instâncias de System.
# Populado por GameEngine.register_services() após criar os sistemas.
_svc: dict = {}

# Resolver por-entidade (SÓ servidor, multi-mapa): callable(eid) -> objeto
# com .tile_validation/.pathfinding do bundle do MAPA daquela entidade, ou
# None. Guarda AUTOMÁTICA contra a classe de bug "_svc apontando pro último
# mapa carregado" (Interceptar 'bloqueado' em terreno aberto, Tiro Repulsivo
# stunando em parede fantasma — ver PROBLEMAS_ARQUITETURA.md §11 item A3):
# funções de módulo que RECEBEM entity_id (is_tile_walkable) resolvem o
# bundle certo por chamada, sem depender de ninguém lembrar de chamar
# register_map_services_for() antes. find_path()/get_tilemap() não têm eid
# na assinatura — pra essas a regra do register_map_services_for continua
# obrigatória (CLAUDE.md). Cliente nunca instala resolver (mapa único).
_svc_resolver = None


def register_service_resolver(resolver) -> None:
    """Instala o resolver por-entidade (WorldServer) — ver comentário acima."""
    global _svc_resolver
    _svc_resolver = resolver


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
                     from_tx=None, from_ty=None,
                     ignore_eid: int = -1) -> bool:
    # Servidor multi-mapa: resolve o tile_validation do bundle do MAPA da
    # PRÓPRIA entidade (guarda automática — ver _svc_resolver acima).
    if _svc_resolver is not None:
        _bundle = _svc_resolver(entity_id)
        if _bundle is not None and getattr(_bundle, 'tile_validation', None) is not None:
            return _bundle.tile_validation.is_tile_walkable(
                entity_id, tx, ty, from_tx, from_ty, ignore_eid=ignore_eid)
    return _svc['tile_validation'].is_tile_walkable(entity_id, tx, ty, from_tx, from_ty,
                                                    ignore_eid=ignore_eid)


def get_mainhand_weapon(world, entity_id: int):
    from engine.components import Equipment
    eq = world.get_component(entity_id, Equipment)
    return eq.slots.get("mainhand") if eq else None


# ── Autorização de skill ──────────────────────────────────────────────────────
# Lookup reverso skill_id → (talent_id, min_points) derivado de talent_data —
# fonte única (systems.py re-exporta como _TALENT_SKILL_REQ_SYS pro gate de UI).
from content.talent_data import TALENTS as _TT_DATA_WS
_TALENT_SKILL_REQS: dict[str, tuple[str, int]] = {
    td["unlocks_skill"]: (tid, td.get("unlock_at", 1))
    for tid, td in _TT_DATA_WS.items()
    if td.get("unlocks_skill")
}


def is_skill_authorized(world, entity_id: int, sid: str) -> "tuple[bool, str]":
    """O player PODE usar essa skill? (classe + talento + aprendizado)

    Gate autoritativo usado pelo servidor (skill_processor) ANTES de executar
    qualquer handler — sem isso, qualquer `sid` do SKILL_CATALOG era aceito e
    o único bloqueio real era o custo de recurso dentro do handler (cliente
    modificado castava skill de outra classe / de talento não alocado / não
    comprada no treinador). O cliente tem o mesmo gate na UI (systems.py,
    via _TALENT_SKILL_REQ_SYS) — este é a versão servidor, contra os
    componentes AUTORITATIVOS (CharacterStats/TalentTree/PlayerSkills).

    Retorna (ok, motivo) — motivo vai no SKILL_RESULT failed=True (WARN no
    cliente).
    """
    from content.skill_config import (SKILL_CATALOG, SKILL_ORDER_BY_CLASS,
                              INITIAL_SKILLS_BY_CLASS)
    from engine.components import CharacterStats, TalentTree, PlayerSkills

    sdef = SKILL_CATALOG.get(sid)
    if sdef is None:
        return False, "Skill desconhecida"

    char    = world.get_component(entity_id, CharacterStats)
    cls     = char.class_id if char else ""
    req_cls = sdef.get("class_id", "")
    if req_cls and cls and req_cls != cls:
        return False, "Skill de outra classe"

    # Skill desbloqueada por talento: exige pontos suficientes no TalentTree
    _treq = _TALENT_SKILL_REQS.get(sid)
    if _treq is not None:
        tid, min_pts = _treq
        tt = world.get_component(entity_id, TalentTree)
        if tt is None or tt.allocated.get(tid, 0) < min_pts:
            return False, "Requer talento"
        return True, ""

    # Skill de treinador (não-inicial): exige constar em learned_skill_ids
    _cls_key = req_cls or cls
    if (sid in SKILL_ORDER_BY_CLASS.get(_cls_key, ())
            and sid not in INITIAL_SKILLS_BY_CLASS.get(_cls_key, ())):
        ps = world.get_component(entity_id, PlayerSkills)
        if ps is None or sid not in ps.learned_skill_ids:
            return False, "Skill nao aprendida"

    return True, ""


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
    def __init__(self, world: World, tilemap_entity: int = -1):
        self.world = world
        self.tilemap_comp = None
        self._tilemap_entity = tilemap_entity

    def _get_tilemap_component(self):
        if not self.tilemap_comp:
            if self._tilemap_entity >= 0:
                self.tilemap_comp = self.world.get_component(self._tilemap_entity, Tilemap)
            else:
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
    def __init__(self, world: World, tilemap_entity: int = -1, map_filter: str = ""):
        self.world = world
        self.tilemap_comp = None
        self._tilemap_entity = tilemap_entity
        self._map_filter = map_filter
        # Cache de tiles ocupados: {(tx, ty): entity_id}
        # Atualizado em update() a cada frame; consultado em O(1) por is_tile_walkable.
        self._occupied: dict = {}

    def _get_tilemap_component(self):
        if not self.tilemap_comp:
            if self._tilemap_entity >= 0:
                self.tilemap_comp = self.world.get_component(self._tilemap_entity, Tilemap)
            else:
                for _, tm_comp in self.world.get_entities_with(Tilemap):
                    self.tilemap_comp = tm_comp
                    break
        return self.tilemap_comp

    def update(self, events: list = None, dt: float = 0) -> None:
        """Reconstrói o cache de tiles ocupados a cada frame."""
        occupied = {}
        if self._map_filter:
            for entity_id, tm in self.world.get_entities_with(TileMovement):
                ml = self.world.get_component(entity_id, MapLocation)
                if ml is None or ml.map_file != self._map_filter:
                    continue
                occupied[(tm.current_tile_x, tm.current_tile_y)] = entity_id
                if tm.is_moving:
                    occupied[(tm.target_tile_x, tm.target_tile_y)] = entity_id
        else:
            for entity_id, tm in self.world.get_entities_with(TileMovement):
                occupied[(tm.current_tile_x, tm.current_tile_y)] = entity_id
                if tm.is_moving:
                    occupied[(tm.target_tile_x, tm.target_tile_y)] = entity_id
        self._occupied = occupied

    def is_tile_walkable(self, moving_entity_id: int,
                         target_tile_x: int, target_tile_y: int,
                         from_tile_x: int | None = None,
                         from_tile_y: int | None = None,
                         ignore_eid: int = -1) -> bool:
        """Verifica se o tile destino é acessível para a entidade.

        from_tile_x/from_tile_y (opcional): tile de origem para checar colisão
        direcional e regras de elevação. Sem eles, só a colisão base é checada.
        ignore_eid (opcional): ignora ocupação desse entity_id (ex: mob-alvo do chase).
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
        if ignore_eid != -1 and occupant_id == ignore_eid:
            return True

        # Tile ocupado — verifica se inimigo tenta andar no tile do player
        if self.world.get_component(moving_entity_id, Enemy) and \
           self.world.get_component(occupant_id, PlayerControlled):
            ai_control = self.world.get_component(moving_entity_id, AIControlled)
            if ai_control:
                ai_control.is_blocked = True
                ai_control.blocked_by_entity_id = occupant_id

        # Jogador pode mover para o tile do alvo explícito (perseguição via A*)
        # WASD passa ignore_eid=-1 → tile do mob bloqueado normalmente — B1
        if self.world.get_component(moving_entity_id, PlayerControlled) and \
           self.world.get_component(occupant_id, Enemy):
            return occupant_id == ignore_eid

        return False


# Novo Sistema: CombatSystem
# Gerencia a lógica de dano, cura, morte e outros aspectos de combate.
class CombatSystem(System):
    """Aplica dano entre entidades e delega processamento de morte para DeathHandlerSystem.

    Constantes de cálculo movidas para damage_calculator.py.
    Lógica de morte (loot, cadáver, XP) movida para DeathHandlerSystem.
    """

    def __init__(self, world: World, is_server: bool = False, on_damage_dealt=None):
        self.world = world
        self.last_outcome: str = "hit"  # captura o outcome do último deal_damage
        # Avoidances de mob→player (parry/dodge/miss) que o servidor deve repassar ao cliente.
        # Preenchido em deal_damage; consumido e limpo por combat_processor cada tick.
        self.mob_avoidance_events: list[tuple] = []  # (attacker_id, target_id, outcome, pos_hp)
        # True só na instância do servidor (server/world_server.py) — esta classe é
        # compartilhada client+server (roda também no client offline). Concessão de xp
        # de SkillLevels (Tibia-like) só pode rodar no servidor; ler bônus já calculados
        # é seguro nos dois lados (cliente nunca chama apply_skill_bonuses_to_combat,
        # então os campos *_skill_bonus ficam sempre 0 lá — ver stats_system.py).
        self.is_server: bool = is_server
        # Callback opcional `fn(attacker_eid, target_id, dmg)` — só setado pelo
        # servidor (WorldServer._log_mob_damage_hit), repassado pro núcleo único
        # apply_damage_core em deal_damage(). Ponto ÚNICO de log de "quem ataca
        # primeiro" (dono do loot/quest kill) — cobre TODA skill física
        # (Golpe Poderoso, Executar, Fatiador, etc.) e auto-attack melee, sem
        # precisar de write manual espalhado por handler (ver
        # PROBLEMAS_ARQUITETURA.md — skills nunca logavam, loot ia pra quem
        # desse a sorte de dar o PRIMEIRO auto-attack, não quem realmente
        # aggrou/lutou primeiro).
        self.on_damage_dealt = on_damage_dealt

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
                          extra_acerto: float = 0.0,
                          extra_block: float = 0.0,
                          extra_avoid: float = 0.0,
                          is_ability: bool = False) -> tuple:
        """Calcula dano e resolve a tabela de ataque. Retorna (damage, outcome).

        Delega a matemática pura para damage_calculator.py.
        is_ability=True: pula o miss roll (abilities só podem ser dodged/parried, não missed).
        extra_acerto/extra_block/extra_avoid: bônus de skill level (arma do
        atacante / escudo+defesa do alvo) — resolvidos pelo chamador (deal_damage).
        """
        if target_stats is not None:
            outcome, block_reduction = resolve_attack_outcome(
                attacker_stats, target_stats, damage_type, extra_crit,
                extra_acerto=extra_acerto, extra_block=extra_block,
                extra_avoid=extra_avoid, is_ability=is_ability)
        else:
            outcome, block_reduction = 'hit', 0.0

        if outcome in ('miss', 'dodge', 'parry'):
            return 0, outcome

        weapon = self._get_mainhand_weapon(attacker_id)
        # Auto-attack (damage_type=="physical"): AP escala com skill_level da
        # arma equipada, mesma fórmula das skills físicas — ver
        # damage_calculator.ability_physical_damage/PROBLEMAS_ARQUITETURA.md.
        # Skills usam "physical_fixed" (bônus já embutido no valor pré-calculado
        # que chega em base_ability_damage) — não passam por aqui.
        _ap_skill_mult = 1.0
        if damage_type == "physical":
            from engine.stats_system import weapon_skill_level as _wsl_auto
            _ap_skill_mult = 1.0 + 0.01 * _wsl_auto(self.world, attacker_id, weapon)
        total_damage = calculate_base_damage(
            attacker_stats, damage_type, weapon,
            base_ability_damage, multiplier, outcome, block_reduction,
            ap_skill_mult=_ap_skill_mult,
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
        outcome = 'hit'|'crit'|'block'|'miss'|'dodge'|'parry'|'immune'|'evade'|''.
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

        # Modo evasão (estilo WoW): mob em RETURNING (voltando pro spawn após
        # estourar o leash) é imune a dano/aggro até chegar — diferente da
        # imunidade acima, aqui queremos feedback visível ("Evadiu!"), já que
        # é o comportamento explicitamente pedido pelo usuário (sem isso,
        # bater no mob durante o retorno resetava a perseguição de graça).
        # Rede de segurança final em apply_damage_core::"blocked_evade".
        _ai_evade = self.world.get_component(target_id, AIControlled)
        if _ai_evade and _ai_evade.state == "RETURNING":
            self._emit_avoidance_feedback("evade", _tx, _ty, attacker_id, target_id,
                                          attacker_is_player, target_is_player)
            return False, "evade"

        extra_crit = self._extra_crit_bonus(attacker_id, target_id, attacker_is_player)

        # Skill level — arma do atacante (acerto+crit) e escudo/defesa do alvo
        # (block+avoid). Leitura é sempre segura (campos ficam 0 no cliente,
        # que nunca chama apply_skill_bonuses_to_combat); concessão de xp só
        # roda no servidor (self.is_server) — ver stats_system.py.
        from engine.stats_system import weapon_skill_extras, defense_skill_extras
        _weapon = self._get_mainhand_weapon(attacker_id)
        _wsk_acerto, _wsk_crit = weapon_skill_extras(self.world, attacker_id, _weapon)
        _def_block, _def_avoid = defense_skill_extras(self.world, target_id)
        extra_crit += _wsk_crit
        if self.is_server:
            from engine.stats_system import grant_weapon_skill_xp, grant_defense_skill_xp
            grant_weapon_skill_xp(self.world, attacker_id, _weapon)
            grant_defense_skill_xp(self.world, target_id)

        if pre_outcome:
            # Outcome pré-rolado (ex: por flechas que já verificaram miss visualmente)
            outcome = pre_outcome
            block_reduction = 0.0
            # Mesmo bônus de skill_level no AP que _calculate_damage aplica
            # (ver comentário lá) — este branch é outro caminho pro mesmo
            # damage_type=="physical" de auto-attack, só com outcome pré-rolado.
            _ap_skill_mult_pre = 1.0
            if damage_type == "physical":
                from engine.stats_system import weapon_skill_level as _wsl_pre
                _ap_skill_mult_pre = 1.0 + 0.01 * _wsl_pre(self.world, attacker_id, _weapon)
            calculated_damage = calculate_base_damage(
                attacker_stats, damage_type, _weapon,
                base_ability_damage, multiplier, outcome, block_reduction,
                ap_skill_mult=_ap_skill_mult_pre,
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
                extra_acerto=_wsk_acerto, extra_block=_def_block, extra_avoid=_def_avoid,
                is_ability=is_ability,
            )

        if outcome in ('miss', 'dodge', 'parry'):
            self._emit_avoidance_feedback(outcome, _tx, _ty,
                                          attacker_id, target_id,
                                          attacker_is_player, target_is_player)
            self.last_outcome = outcome   # backward compat offline
            if not attacker_is_player and target_is_player:
                _tgt_cs_av = self._get_combat_stats(target_id)
                _hp_av = _tgt_cs_av.current_hp if _tgt_cs_av else 0
                self.mob_avoidance_events.append((attacker_id, target_id, outcome, _hp_av))
            return False, outcome

        final_damage = self._resolve_damage_modifiers(
            attacker_id, target_id, calculated_damage, outcome,
            apply_armor_reduction, damage_type,
            attacker_is_player, target_is_player,
        )
        self.last_outcome = outcome   # backward compat offline + skill results fallback

        # Escrita final de HP + quebra de polymorph/sleep: núcleo ÚNICO
        # compartilhado com o servidor (_apply_final_damage) e o caminho
        # mágico do cliente (_apply_magic_damage) — problemas B/H.
        # add_pending_death=False: a morte aqui passa por _handle_death
        # (XP/loot/corpse), não pelo PendingDeath genérico do núcleo.
        def _cc_break_fx(kind: str) -> None:
            if kind == "polymorph":
                FLT.add("Polimorfia quebrada!", _tx, _ty, (160, 80, 200), "small",
                        target_id=target_id)
            elif kind == "sleep":
                FLT.add("Acordou!", _tx, _ty, (200, 200, 100), "small",
                        target_id=target_id)
        apply_damage_core(self.world, target_id, final_damage,
                          killer_eid=attacker_id, add_pending_death=False,
                          on_cc_break=_cc_break_fx,
                          on_damage_dealt=self.on_damage_dealt)

        # Aggro por dano: ataque força o alvo a perseguir independente do raio
        # (cobre players E outros combatentes — Sistema de Facções, Fase 5:
        # um NPC/mob atacado à distância por outro NPC/mob também precisa
        # revidar, não só quando o atacante é o player).
        # aggroed_by_damage=True desativa o leash de 5 tiles até o mob chegar perto do alvo.
        # Só IDLE — RETURNING nunca chega aqui de verdade (early-return acima,
        # modo evasão), mas o gate fica explícito pra não reintroduzir o bug
        # numa refatoração futura que reordene esses blocos.
        # Facção "amigavel": nunca agroa por dano, mesmo que o dano em si já
        # tenha sido bloqueado por apply_damage_core — sem este gate aqui, um
        # NPC de combate amigável passava a "perseguir" o player só por ter
        # recebido uma tentativa de ataque (aggro visual incorreto, bug real
        # relatado pelo usuário 15/07/2026 testando o "Guarda Real" de teste;
        # ver também os gates em set_player_target()/combat_processor.py, que
        # impedem o auto-attack de sequer tentar contra alvo amigavel).
        if can_engage(self.world, attacker_id, target_id):
            _ai = self.world.get_component(target_id, AIControlled)
            if _ai and _ai.state == "IDLE":
                _ms_hit = self.world.get_component(target_id, MobSounds)
                SOUNDS.play_mob_sounds(_ms_hit, "aggro", dedup_key=f"dmg_{target_id}")
                _ai.state              = "AGGRO_DELAY"
                _ai.aggro_delay        = 0.5   # mesmo comportamento do range aggro, mas mais curto
                _ai.aggroed_by_damage  = True
                # QUEM bateu vira o alvo — semântica de threat table (dano
                # põe o atacante na tabela). Antes o alvo vinha por acidente
                # do loop de aquisição SEM filtro de facção em
                # _select_target; com a aquisição filtrada por is_hostile
                # (§34.9) e limitada ao raio de aggro (§34.10), um mob
                # NEUTRO atacado em melee nunca mais receberia alvo nenhum
                # e "desistia" da revidada em 600ms sem nunca golpear.
                _ai.target_eid         = attacker_id
                _ai.path_recalc_timer  = 0.0
                if _MCL:
                    _ident_dmg = self.world.get_component(target_id, EntityIdentity)
                    _n = _ident_dmg.name         if _ident_dmg else f"mob#{target_id}"
                    _r = _ident_dmg.race         if _ident_dmg else "?"
                    _c = _ident_dmg.entity_class if _ident_dmg else "?"
                    _MCL.log("AGGRO_DMG", target_id, _n, _r, _c,
                             dmg=final_damage, attacker=attacker_id)

        is_crit  = outcome == 'crit'
        is_block = outcome == 'block'
        self._emit_hit_feedback(attacker_id, target_id, final_damage, outcome,
                                is_crit, is_block, _tx, _ty,
                                attacker_is_player, target_is_player, is_ability)
        self._apply_on_hit_procs(attacker_id, is_crit, attacker_is_player)

        # (Quebra de Polimorfia/Sono: feita pelo apply_damage_core acima)

        # Player recebe dano → entra em combate (impede regen de HP)
        if target_is_player and final_damage > 0:
            _player_cs = self.world.get_component(target_id, CombatState)
            if _player_cs:
                enter_combat(_player_cs)

        # Escudo de Fogo: retaliation em quem atacou o jogador
        if target_is_player and final_damage > 0:
            from engine.components import FireShieldEffect
            _shield = self.world.get_component(target_id, FireShieldEffect)
            if _shield:
                _retaliation = 10 + int(target_stats.spell_power * 0.20)
                _att_cs  = self._get_combat_stats(attacker_id)
                _att_pos = self.world.get_component(attacker_id, Position)
                # Núcleo único: guards alive/immune + overkill + PendingDeath
                _ret_result = apply_damage_core(self.world, attacker_id,
                                                _retaliation, killer_eid=target_id)
                if _ret_result in ("applied", "killed") and _att_cs:
                    if _att_pos:
                        FLT.add(f"-{_retaliation}", _att_pos.x, _att_pos.y,
                                (255, 120, 0), "normal", target_id=attacker_id)
                    # Online: notifica cliente via _svc callback (servidor não tem FLT)
                    _emit_ret = _svc.get("emit_retaliation")
                    if _emit_ret:
                        _emit_ret(target_id, attacker_id, _retaliation,
                                  max(0, _att_cs.current_hp))

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
            from engine.damage_calculator import apply_armor_reduction as _aar
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
        # Funciona para mobs E players (PvP) — sem restrição de tipo de entidade
        _sfx_target = self.world.get_component(target_id, StatusEffects)
        if _sfx_target and _sfx_target.has("enraged"):
            dmg *= 1.10
        _sfx_att = self.world.get_component(attacker_id, StatusEffects)
        if _sfx_att and _sfx_att.has("enraged"):
            dmg *= 1.05

        return max(0, int(dmg))

    def _emit_avoidance_feedback(self, outcome: str, tx: float, ty: float,
                                  attacker_id: int, target_id: int,
                                  attacker_is_player: bool,
                                  target_is_player: bool) -> None:
        """Texto flutuante, log e som para ataques evitados (miss/dodge/parry/evade)."""
        _AVOID = {
            'miss':  ("Errou!",   (220, 220, 100), "small"),
            'dodge': ("Desviou!", (100, 210, 230), "small"),
            'parry': ("Aparou!",  (100, 150, 230), "small"),
            'evade': ("Evadiu!",  (150, 150, 150), "small"),
        }
        _AVOID_LOG = {
            'miss':  ("Voce errou!",      "Inimigo errou!",      (200, 200, 100)),
            'dodge': ("Inimigo desviou!", "Voce desviou!",       (100, 210, 230)),
            'parry': ("Inimigo aparou!",  "Voce aparou!",        (100, 150, 230)),
            # Alvo em modo evasão só existe pro lado mob (RETURNING) — enemy_msg
            # nunca dispara aqui (target_is_player sempre False nesse caso).
            'evade': ("Alvo evadiu!",     "",                    (150, 150, 150)),
        }
        _SND = {
            'miss':  ["combat_miss",  "combat_miss_1",  "combat_miss_2",  "combat_miss_3",  "combat_miss_4"],
            'parry': ["combat_parry", "combat_parry_1", "combat_parry_2", "combat_parry_3", "combat_parry_4"],
            'dodge': ["combat_dodge", "combat_dodge_1", "combat_dodge_2", "combat_dodge_3", "combat_dodge_4"],
            # Reaproveita os sons de "miss" — evasão soa como um golpe que não conecta.
            'evade': ["combat_miss",  "combat_miss_1",  "combat_miss_2",  "combat_miss_3",  "combat_miss_4"],
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
                coins = (roll_mob_coins(ident.name, tier_comp.tier) if ident
                        else roll_coins(tier_comp.tier))

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
                            from engine.components import Item as _Item
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


class CombatStateSystem(_BaseCombatStateSystem, System):
    """Atualiza timers de CombatState, drena modificadores temporários e dispara procs.

    Herda BaseCombatStateSystem (core_systems.py) — timers de combate, rage decay,
    HP5 e concentração ficam em um único lugar compartilhado com o servidor.
    Este sistema adiciona lógica cliente-only: wander de disoriented/polymorph,
    timer de camuflagem (visual), timed_modifiers e procs de equipamento.
    """

    # RAGE_DECAY_AMOUNT / RAGE_DECAY_INTERVAL herdados de BaseCombatStateSystem
    DIS_MOVE_DELAY = 1.3  # segundos de pausa entre passos aleatórios

    def __init__(self, world: World):
        _BaseCombatStateSystem.__init__(self, world)
        self._net = None  # injetado pelo GameEngine no modo online
        # Timer por eid: segundos até o próximo passo aleatório (disoriented/polymorph)
        self._dis_move_timers: dict[int, float] = {}

    def update(self, events: list = None, dt: float = 0) -> None:
        for eid, cs in self.world.get_entities_with(CombatState):
            combat_stats = self.world.get_component(eid, CombatStats)

            # ── Timers headless (compartilhados com servidor) ──────────────
            self._tick_combat_timer(cs, dt)
            self._tick_stun_timer(cs, dt)

            if self.world.get_component(eid, PlayerControlled) is not None:
                char_stats = self.world.get_component(eid, CharacterStats)
                tm         = self.world.get_component(eid, TileMovement)

                # Rage é 100% server-autoritativa (decay incluso) — chega via
                # STATS_UPDATE. O decay local era do modo offline, removido
                # deste branch (15/07/2026, item A2 §11 — decisão do usuário:
                # offline vive só no master). Decair aqui dobraria o decay
                # entre um push e outro do servidor.

                if combat_stats:
                    self._tick_concentration_free_timer(combat_stats, dt)
                    self._tick_standing_seconds(combat_stats, tm, dt)
                    if char_stats:
                        self._tick_concentration_regen(cs, char_stats, combat_stats, tm, dt)

                # ── Disoriented / Polymorph: wander aleatório (cliente-only) ──
                # Para mobs: EnemyAISystem faz o wander; para players: aqui.
                _sfx_dis = self.world.get_component(eid, StatusEffects)
                if _sfx_dis is not None and (
                        _sfx_dis.has("disoriented") or _sfx_dis.has("polymorph")):
                    cs.is_pursuing = False
                    _auto_dis = self.world.get_component(eid, PlayerAutoMove)
                    _tm_dis   = tm
                    _dis_t = self._dis_move_timers.get(eid, 0.0) - dt
                    self._dis_move_timers[eid] = max(0.0, _dis_t)
                    if _auto_dis and _tm_dis and not _tm_dis.is_moving and _dis_t <= 0:
                        _dirs = [(0,1),(0,-1),(1,0),(-1,0)]
                        random.shuffle(_dirs)
                        for _ddx, _ddy in _dirs:
                            _fx = _tm_dis.current_tile_x + _ddx
                            _fy = _tm_dis.current_tile_y + _ddy
                            if is_tile_walkable(eid, _fx, _fy):
                                _auto_dis.ground_target = (_fx, _fy)
                                _auto_dis.active        = True
                                _auto_dis.path.clear()
                                self._dis_move_timers[eid] = self.DIS_MOVE_DELAY
                                break

                # ── Camuflagem — timer e restauração visual (cliente-only) ──
                if combat_stats and combat_stats.camouflage_timer > 0:
                    combat_stats.camouflage_timer -= dt
                    if combat_stats.camouflage_timer <= 0:
                        combat_stats.camouflage_timer  = 0.0
                        combat_stats.camouflage_object = ""
                        _rend_cam = self.world.get_component(eid, Renderable)
                        _char_cam = char_stats
                        if _rend_cam and _char_cam:
                            _CLASS_COLORS = {"mago": (80, 80, 220), "arqueiro": (80, 200, 80)}
                            _rend_cam.color  = _CLASS_COLORS.get(_char_cam.class_id, (255, 0, 0))
                            _rend_cam.width  = 24
                            _rend_cam.height = 24
                        _cst_cam = self.world.get_component(eid, CombatState)
                        if _cst_cam:
                            _cst_cam.is_visible    = True
                            _cst_cam.is_immune     = False
                            _cst_cam.is_camouflaged = False
                        if tm:
                            tm.speed = 110.0   # PLAYER_SPEED original

            # HP5 — regen via base method (compartilhado com servidor)
            if combat_stats:
                self._tick_hp5(cs, combat_stats, dt)  # retorno ignorado no cliente

            if combat_stats and combat_stats.timed_modifiers:
                for entry in combat_stats.timed_modifiers:
                    entry["timer"] -= dt
                expired = [e for e in combat_stats.timed_modifiers if e["timer"] <= 0]
                for entry in expired:
                    combat_stats.timed_modifiers.remove(entry)
                    _max_before_exp = combat_stats.max_hp
                    _pct_hp = (combat_stats.current_hp / _max_before_exp
                               if _max_before_exp > 0 else 1.0)
                    remove_modifier(combat_stats, entry["modifier"])
                    # Se o max HP reduziu (ex: stamina expirou), mantém percentual de vida
                    if combat_stats.max_hp < _max_before_exp:
                        combat_stats.current_hp = max(1, int(_pct_hp * combat_stats.max_hp))
                    LOG.add(f"Efeito '{entry['label']}' expirou.", (160, 160, 160))

            if cs._just_entered_combat:
                cs._just_entered_combat = False
                self._trigger_procs(eid, combat_stats)

    def _trigger_procs(self, entity_id: int, combat_stats: CombatStats) -> None:
        """Rola procs de itens equipados ao entrar em combate — versão LOCAL/cosmética
        (LOG + feedback visual). O servidor rola sua própria versão autoritativa
        independentemente (core_systems.ServerCombatStateSystem._roll_procs) e é quem
        decide o resultado real de stats/HP, sincronizado de volta via STATS_UPDATE/AOI —
        ver arquitetura/PROBLEMAS_ARQUITETURA.md. Por isso este roll local pode divergir
        do servidor (chance independente); é só feedback, não fonte de verdade."""
        equip = self.world.get_component(entity_id, Equipment)
        if not equip or not combat_stats:
            return
        for item in equip.slots.values():
            if item and item.proc:
                p = item.proc
                if random.random() < p["chance"]:
                    _max_before = combat_stats.max_hp
                    _pct_before = (combat_stats.current_hp / _max_before
                                   if _max_before > 0 else 1.0)
                    mod = Modifier(p["attribute"], p["value"], source="buff")
                    add_timed_modifier(combat_stats, mod, p["duration"], p["label"])
                    # Se o proc aumentou o max HP, escala o HP atual pelo mesmo percentual
                    if combat_stats.max_hp > _max_before:
                        combat_stats.current_hp = max(1, int(_pct_before * combat_stats.max_hp))
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
                if proj.ability_id:
                    # Projétil de habilidade: aplica DoT/debuff (não dano direto)
                    from content.enemy_abilities_data import ABILITY_DEFS as _ABD_P
                    _defn_p = _ABD_P.get(proj.ability_id)
                    if _defn_p:
                        # magnitude é multiplicador do attack_power do caster
                        # (ver enemy_abilities_data.py) — escala com level/tier.
                        _atk_cs_p = self.world.get_component(proj.attacker_id, CombatStats)
                        _ability_dmg_p = _defn_p.magnitude * (_atk_cs_p.base_attack_power if _atk_cs_p else 0.0)
                        apply_effect(
                            self.world, proj.target_id,
                            _defn_p.effect_type, _defn_p.duration, _ability_dmg_p,
                            tick_interval=_defn_p.tick_interval,
                        )
                        PROC.add(_defn_p.name, (220, 80, 180))
                        _atk_ident_p = self.world.get_component(proj.attacker_id, EntityIdentity)
                        _mob_nm_p = _atk_ident_p.name if _atk_ident_p else "Inimigo"
                        LOG.add(f"{_mob_nm_p} usou {_defn_p.name}!", (220, 80, 180))
                else:
                    # Projétil de ataque: aplica dano usando stats do atacante
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
        # import local: único uso de pygame neste módulo headless — só o
        # cliente chama render() (servidor nunca desenha).
        import pygame
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


# Modificação no EnemyAISystem para integrar o CombatSystem
class EnemyAISystem(System):
    KITING_MIN_DIST      = 3   # tiles: ranged enemy flees if player is this close
    # Raio de AQUISIÇÃO de alvo (aggro por proximidade E handoff pós-morte
    # do alvo atual) — antes era um local `_aggro_range_px = 5*TILE_SIZE`
    # usado só no gate IDLE→AGGRO_DELAY; virou constante única porque
    # _select_target também precisa dele (§34.10): sem o cap na aquisição,
    # um mob em estado de combate cujo alvo morre recebia o próximo hostil
    # a QUALQUER distância (até o leash de 20 tiles) e saía "caçando" pelo
    # mapa — bug real relatado pelo usuário 16/07/2026 (Guarda Real matou
    # o Bandido e saiu atacando zumbis longe). RETENÇÃO do alvo já
    # engajado NÃO usa este raio (perseguição além dele é normal — só o
    # leash limita); aggro por DANO também não (revidada em qualquer
    # distância, ver blocos aggroed_by_damage).
    AGGRO_RADIUS_TILES   = 5
    SLEEP_RADIUS_TILES   = 40  # além desta distância (Chebyshev), a AI é completamente suspensa
    MAX_LEASH_RADIUS     = 20  # tiles: mob retorna ao spawn se afastar mais do que isso (aggro normal)
    MAX_LEASH_RADIUS_DMG = 25  # tiles: raio maior quando aggroed por dano (evita reset por 1 hit + recuo)
    MAX_PATHFINDS_PER_FRAME = 10  # limite de chamadas A* por frame (evita travamento com muitos inimigos)
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

    def __init__(self, world: World, map_filter: str = "",
                 pathfinding=None, tile_validation=None):
        self.world = world
        self._map_filter = map_filter
        # Serviços injetados diretamente (P4): elimina dependência no global _svc.
        # None → fallback para as funções de módulo get_tilemap()/is_tile_walkable().
        self._pathfinding    = pathfinding
        self._tile_validation = tile_validation
        self.proximity_threshold_pixels = 5.0
        self.proximity_threshold_tiles = 1
        self.path_recalc_interval = 0.8
        self._pathfind_budget = 0  # resetado a cada frame
        self._dbg_atk_timers: dict[int, float] = {}  # eid → tempo acumulado desde último log
        # Caches de "outros combatentes" pra _select_target — populados de
        # verdade em update() (1x por tick); vazios aqui só como fallback
        # seguro caso _select_target seja chamado antes do 1º update().
        self._npc_combatants_cache: list = []
        self._all_combatants_cache: list = []

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
        _check_map = bool(self._map_filter)
        for entity_id, tile_move_comp in self.world.get_entities_with(TileMovement):
            if entity_id == except_entity_id:
                continue
            if _check_map:
                _ml_occ = self.world.get_component(entity_id, MapLocation)
                if _ml_occ is None or _ml_occ.map_file != self._map_filter:
                    continue
            if not tile_move_comp.is_moving:
                occupied_tiles.add((tile_move_comp.current_tile_x, tile_move_comp.current_tile_y))
            else:
                occupied_tiles.add((tile_move_comp.target_tile_x, tile_move_comp.target_tile_y))
        return occupied_tiles

    def _get_tilemap(self):
        """Retorna tilemap do bundle injetado, ou global como fallback (offline)."""
        if self._pathfinding:
            return self._pathfinding._get_tilemap_component()
        return get_tilemap()

    def _is_walkable(self, eid, tx, ty, from_tx=None, from_ty=None, ignore_eid=-1):
        """Routes is_tile_walkable para o tile_validation do bundle, ou global."""
        if self._tile_validation:
            return self._tile_validation.is_tile_walkable(eid, tx, ty, from_tx, from_ty, ignore_eid)
        return is_tile_walkable(eid, tx, ty, from_tx, from_ty, ignore_eid)

    def _find_path_budgeted(self, start, end, dynamic_obstacles=None):
        """Chama find_path apenas se o budget do frame ainda não foi esgotado."""
        if self._pathfind_budget <= 0:
            return None
        self._pathfind_budget -= 1
        if self._pathfinding:
            return self._pathfinding.find_path(start, end, dynamic_obstacles=dynamic_obstacles)
        return find_path(start, end, dynamic_obstacles=dynamic_obstacles)

    def _select_target(self, mob_eid: int):
        """Retorna (target_eid, pos, tile_move, combat_stats, combat_state) do
        alvo mais próximo — player OU outra entidade `Combatant` (mob/NPC de
        combate, Sistema de Facções Fase 5) cuja relação com este mob não
        seja `amigavel` (nunca vale a pena rastrear um alvo que nunca vai
        ser atacado). A decisão de agroar OU NÃO por proximidade continua
        inteiramente no chamador (`is_hostile()`, ver bloco "Detecção
        inicial" em `update()`) — aqui só se escolhe o candidato mais
        próximo, exatamente como sempre foi feito só com players.

        Prioriza o alvo já agredido via dano (AIControlled.aggroed_by_damage + target_eid).
        Ignora alvos mortos e invisíveis.
        Retorna (-1, None, None, None, None) se nenhum alvo válido.
        """
        ai_ctrl = self.world.get_component(mob_eid, AIControlled)
        mob_pos = self.world.get_component(mob_eid, Position)
        if not mob_pos:
            return (-1, None, None, None, None)

        best_eid   = -1
        # AQUISIÇÃO limitada ao raio de aggro (§34.10) — candidato mais
        # distante que isso nunca é adquirido, nem no handoff pós-morte do
        # alvo atual (o mob reseta e volta pra casa em vez de "caçar" o
        # próximo hostil do mapa). Inicializar best_dist com o raio (em
        # vez de infinito) É o cap: só distâncias menores vencem. O +0.01
        # preserva a semântica INCLUSIVA do gate de aggro antigo
        # (`dist <= 5*TILE_SIZE`): alvo a exatamente 5 tiles no mesmo eixo
        # dá 160.0px cravados, e `160.0 < 160.0` o rejeitaria.
        best_dist  = self.AGGRO_RADIUS_TILES * TILE_SIZE + 0.01
        best_pos   = None
        best_tm    = None
        best_cs    = None
        best_cst   = None

        # Filtra candidatos pelo mesmo mapa do mob — evita cross-map targeting.
        mob_ml  = self.world.get_component(mob_eid, MapLocation)
        mob_map = mob_ml.map_file if mob_ml else ""

        for p_eid, p_pos, p_tm, _, p_cs in self.world.get_entities_with(
                Position, TileMovement, PlayerControlled, CombatStats):
            if mob_map:
                p_ml = self.world.get_component(p_eid, MapLocation)
                if not p_ml or p_ml.map_file != mob_map:
                    continue
            if p_cs.current_hp <= 0:
                continue
            # AQUISIÇÃO por proximidade só considera alvos HOSTIS — mesmo
            # modelo do WoW (aggro radius só se aplica a unidades "red") e
            # dos minions do LoL (aquisição só considera o time inimigo).
            # Este loop era o legado de "todo mob é hostil ao player" (sem
            # filtro nenhum) — bug real relatado pelo usuário 15/07/2026:
            # o Guarda Real (amigável) matava o Bandido e, ainda em estado
            # de combate, recebia o player mais próximo como alvo daqui e
            # PERSEGUIA o player (a janela de grace de 600ms nunca expirava
            # porque este loop sempre devolvia um alvo "válido"). RETENÇÃO
            # de um alvo neutro já engajado (ex: lobo revidando) NÃO passa
            # por aqui — é o bloco de alvo fixo no chamador (ver update()).
            if not is_hostile(self.world, mob_eid, p_eid):
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

        # Outros combatentes que este mob REALMENTE quer brigar por
        # proximidade (Sistema de Facções, Fase 5) — exige `is_hostile`,
        # não só `can_engage` (que só exclui "amigavel"). Diferença real:
        # um bystander sem facção (ex: boneco de treino) resolve pra
        # "neutro" (não "amigavel"), então passaria em `can_engage` — mas
        # nunca deveria "roubar a vaga" de um alvo hostil de verdade só
        # por estar mais perto (bug real encontrado testando o Bandido de
        # conteúdo: o guarda sempre escolhia o boneco de treino mais
        # próximo em vez do bandido hostil, e como a relação com o boneco
        # é neutra, nada nunca acontecia). Aggro por DANO (mob neutro
        # atacado por alguém) não passa por aqui — é o "sticky target"
        # (`aggroed_by_damage`, checado ANTES desta função) que já cobre
        # esse caso sem depender de hostilidade.
        #
        # Exclui a si mesmo. Lê dos caches computados 1x por tick em
        # update() — NÃO requery `get_entities_with` aqui (regressão real
        # medida: ~50s pra ~150s na suíte completa com um scan
        # Combatant×Combatant feito por mob). Quem procura NÃO é NPC usa
        # só `_npc_combatants_cache` (pool pequeno — mob-vs-mob nunca
        # entra aqui, custo O(mobs×npcs)). Quem procura É NPC (guarda
        # defendendo, etc.) usa `_all_combatants_cache` (pool maior, mas
        # só os poucos NPCs do mapa pagam esse scan mais largo).
        _searcher_is_npc = self.world.get_component(mob_eid, NPC) is not None
        _other_candidates = (self._all_combatants_cache if _searcher_is_npc
                            else self._npc_combatants_cache)
        for c_eid, c_pos, c_tm, c_cs in _other_candidates:
            if c_eid == mob_eid:
                continue
            if c_cs.current_hp <= 0:
                continue
            if not is_hostile(self.world, mob_eid, c_eid):
                continue
            c_cst = self.world.get_component(c_eid, CombatState)
            if c_cst is not None and not c_cst.is_visible:
                continue
            dist_px = math.sqrt((c_pos.x - mob_pos.x) ** 2 + (c_pos.y - mob_pos.y) ** 2)
            if dist_px < best_dist:
                best_dist  = dist_px
                best_eid   = c_eid
                best_pos   = c_pos
                best_tm    = c_tm
                best_cs    = c_cs
                best_cst   = c_cst

        return (best_eid, best_pos, best_tm, best_cs, best_cst)

    def update(self, events: list = None, dt: float = 0) -> None:
        self._pathfind_budget = self.MAX_PATHFINDS_PER_FRAME

        # Caches de "outros combatentes" pra _select_target (Sistema de
        # Facções, Fase 5) — computados UMA VEZ por tick aqui, não por mob
        # (evitava um scan O(mobs²) real: cada mob reconsultando
        # get_entities_with(Combatant) do zero — regressão medida de ~50s
        # pra ~150s na suíte completa antes deste cache). NPCs são sempre
        # um conjunto pequeno (poucos por mapa); `_all_combatants_cache`
        # só é de fato iterado quando QUEM PROCURA é um NPC (ver
        # _select_target), então seu tamanho maior não pesa no caso comum
        # (mob normal só varre `_npc_combatants_cache`).
        def _same_map(eid: int) -> bool:
            if not self._map_filter:
                return True
            _ml = self.world.get_component(eid, MapLocation)
            return _ml is not None and _ml.map_file == self._map_filter

        self._npc_combatants_cache = [
            (eid, pos, tm, cs) for eid, pos, tm, _npc, _cbt, cs in self.world.get_entities_with(
                Position, TileMovement, NPC, Combatant, CombatStats)
            if _same_map(eid)
        ]
        self._all_combatants_cache = [
            (eid, pos, tm, cs) for eid, pos, tm, _cbt, cs in self.world.get_entities_with(
                Position, TileMovement, Combatant, CombatStats)
            if _same_map(eid)
        ]

        # Verifica se existe ao menos um player NESTE mapa — sem isso, todos os
        # mobs do bundle ficam ociosos. Não checa current_hp: player morto/ghost
        # ainda precisa que os mobs voltem ao spawn (RETURNING).
        any_player_exists = False
        for _ap_eid, _, _, _, _ in self.world.get_entities_with(
                Position, TileMovement, PlayerControlled, CombatStats):
            if self._map_filter:
                _ap_ml = self.world.get_component(_ap_eid, MapLocation)
                if not _ap_ml or _ap_ml.map_file != self._map_filter:
                    continue
            any_player_exists = True
            break

        if not any_player_exists:
            for _idle_eid, ai_control, tile_movement, combat_stats in self.world.get_entities_with(
                    AIControlled, TileMovement, CombatStats):
                if self._map_filter:
                    _idle_ml = self.world.get_component(_idle_eid, MapLocation)
                    if not _idle_ml or _idle_ml.map_file != self._map_filter:
                        continue
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

            # Filtra por mapa (multi-map): pula entidades que não são deste bundle
            if self._map_filter:
                _ml = self.world.get_component(enemy_id, MapLocation)
                if _ml is None or _ml.map_file != self._map_filter:
                    continue

            enemy_current_tile_x = tile_movement.current_tile_x
            enemy_current_tile_y = tile_movement.current_tile_y
            current_enemy_tile   = (enemy_current_tile_x, enemy_current_tile_y)

            # Identidade para debug (lookup lazy — custo zero quando MCL desativado)
            if _MCL and _MCL.DBG_ENABLED:
                _dbg_ident = self.world.get_component(enemy_id, EntityIdentity)
                _dbg_name  = _dbg_ident.name         if _dbg_ident else f"mob#{enemy_id}"
                _dbg_race  = _dbg_ident.race         if _dbg_ident else "?"
                _dbg_cls   = _dbg_ident.entity_class if _dbg_ident else "?"
                _dbg_prev_state = ai_control.state   # captura estado antes do tick
            else:
                _dbg_name = _dbg_race = _dbg_cls = ""
                _dbg_prev_state = ""

            # --- Seleciona alvo para este mob (N-player support) ---
            target_eid, player_position_comp, player_tile_move_comp, player_combat_stats, _target_cst = \
                self._select_target(enemy_id)

            # RETENÇÃO do alvo engajado — modelo WoW de threat table: uma vez
            # em combate com alguém, o mob MANTÉM esse alvo enquanto ele for
            # válido (vivo, visível, atacável via can_engage), independente
            # da relação ser "hostil" ou "neutra" — um lobo neutro socado
            # pelo player continua revidando mesmo depois que
            # aggroed_by_damage é limpo na aproximação (ver bloco "chegou
            # perto" abaixo). Hostilidade só importa na AQUISIÇÃO
            # (_select_target, filtrada por is_hostile) — nunca na retenção.
            # Quando o alvo morre/some, a retenção falha, a aquisição não
            # acha ninguém hostil, e o mob "reseta" e volta pra casa (grace
            # → RETURNING → IDLE) — o equivalente do "threat table vazia →
            # evade/reset" do WoW. Antes: a retenção exigia
            # aggroed_by_damage=True, e a revidada do neutro dependia (por
            # acidente) do loop de players sem filtro em _select_target.
            _in_combat_state = ai_control.state in (
                "AGGRO_DELAY", "CHASING", "ATTACKING", "KITING", "BLOCKED_BY_PLAYER")
            if ai_control.target_eid != -1 and (ai_control.aggroed_by_damage or _in_combat_state):
                _fx_pos = self.world.get_component(ai_control.target_eid, Position)
                _fx_tm  = self.world.get_component(ai_control.target_eid, TileMovement)
                _fx_cs  = self.world.get_component(ai_control.target_eid, CombatStats)
                _fx_cst = self.world.get_component(ai_control.target_eid, CombatState)
                _fx_pc  = self.world.get_component(ai_control.target_eid, PlayerControlled)
                _fx_cbt = self.world.get_component(ai_control.target_eid, Combatant)
                if (_fx_pos and _fx_tm and _fx_cs and _fx_cs.current_hp > 0
                        and (_fx_pc or _fx_cbt)
                        and can_engage(self.world, enemy_id, ai_control.target_eid)):
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
                ai_control.target_eid = -1
                ai_control.target_lost_timer = 0.0
                if enemy_combat_stats.attack_cooldown_timer > 0:
                    enemy_combat_stats.attack_cooldown_timer -= dt

                # Sem alvo válido (player morto/ghost/invisível): se o mob está
                # fora do spawn, processa RETURNING aqui mesmo — o bloco de
                # perseguição abaixo (leash → RETURNING) só roda com
                # target_eid != -1, então sem isso o mob travaria em IDLE
                # parado ao lado do corpo até o player reviver.
                initial_tile_x = int(initial_pos.x / TILE_SIZE)
                initial_tile_y = int(initial_pos.y / TILE_SIZE)
                dist_to_initial_tiles = (abs(initial_tile_x - enemy_current_tile_x) +
                                          abs(initial_tile_y - enemy_current_tile_y))

                if dist_to_initial_tiles > self.proximity_threshold_tiles:
                    if _MCL and ai_control.state != "RETURNING":
                        _MCL.log("LOST_TGT", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                 prev=_dbg_prev_state, ret="RETURNING")
                    if ai_control.state != "RETURNING":
                        # Primeira vez entrando em RETURNING: força recálculo imediato
                        ai_control.path_recalc_timer = 0.0
                    ai_control.state = "RETURNING"
                    # Recalcula só quando o timer expira — se a busca falhar (path
                    # vazio), o timer já foi renovado e evita recálculo a cada tick
                    # (com muitos mobs retornando ao mesmo tempo, isso saturava o
                    # orçamento de pathfinding e causava movimento "desorientado").
                    if ai_control.path_recalc_timer <= 0:
                        dynamic_obstacles_for_return = self._get_occupied_tiles(except_entity_id=enemy_id)
                        ai_control.path = self._find_path_budgeted(
                            current_enemy_tile, (initial_tile_x, initial_tile_y),
                            dynamic_obstacles=dynamic_obstacles_for_return)
                        ai_control.path_recalc_timer = self.path_recalc_interval
                    else:
                        ai_control.path_recalc_timer -= dt

                    if ai_control.path and not ai_control.is_blocked:
                        if not tile_movement.is_moving:
                            next_tile_on_path_x, next_tile_on_path_y = ai_control.path[0]
                            _next_ret_tile = (next_tile_on_path_x, next_tile_on_path_y)
                            if self._is_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y) and \
                                    _next_ret_tile not in all_occupied_tiles:
                                start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                                ai_control.path.pop(0)
                                all_occupied_tiles.add(_next_ret_tile)
                            else:
                                ai_control.path = None
                                ai_control.path_recalc_timer = 0.0
                elif not tile_movement.is_moving:
                    # Log só na transição real → IDLE — sem esse guard, todo mob
                    # já IDLE gerava 1 linha LOST_TGT por tick (30/s), inflando o
                    # log pra GBs.
                    if _MCL and ai_control.state != "IDLE":
                        _MCL.log("LOST_TGT", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                 prev=_dbg_prev_state,
                                 grace=f"{ai_control.target_lost_timer:.2f}s")
                    # Reset completo estilo WoW SÓ na transição de verdade
                    # RETURNING→IDLE (saindo da evasão) — este branco também é
                    # o "no-op de manutenção" rodado em TODO tick pra qualquer
                    # mob já IDLE parado perto de casa sem alvo (a grande
                    # maioria dos mobs do mundo, o tempo todo); sem este guard,
                    # a cura/limpeza de debuff disparava every tick pra
                    # QUALQUER mob parado, não só ao sair de RETURNING (bug
                    # real pego pela suíte de testes — via
                    # tests/helpers.py::teleport_mob_to_player).
                    _was_returning_a = ai_control.state == "RETURNING"
                    ai_control.state = "IDLE"
                    ai_control.aggroed_by_damage = False
                    enemy_pos.x = initial_pos.x
                    enemy_pos.y = initial_pos.y
                    if _was_returning_a:
                        # HP não cura mais instantaneamente aqui — regen
                        # gradual (1% max_hp/3s, fora de combate) cuida disso
                        # com sync correto pro client (ver WorldServer._tick,
                        # bloco "Regen de mob fora de combate", e Decisão 20
                        # em ARQUITETURA_ONLINE.md). Debuff/DoT residual ainda
                        # limpa aqui — reset de status effects continua instantâneo.
                        _sfx_reset_a = self.world.get_component(enemy_id, StatusEffects)
                        if _sfx_reset_a:
                            _sfx_reset_a.effects.clear()
                            # StatusEffectSystem.update() pula a entidade inteira
                            # quando effects fica vazio (ver core_systems.py) —
                            # sem resetar aqui, slow_mult ficava PARADO no valor
                            # antigo até o mob ganhar um efeito novo (nunca, se
                            # ele voltar a ficar parado em casa pro resto do jogo).
                            tile_movement.slow_mult = 1.0
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
                    if _MCL: _MCL.log("INVISIBLE", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                      prev=_dbg_prev_state)
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
                            if self._is_walkable(enemy_id, fx, fy):
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
                            if self._is_walkable(enemy_id, fx, fy):
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

            # --- Sleep zone: mob já IDLE (parado em casa, sem nada pendente)
            # dorme se NENHUM player estiver a menos de 40 tiles — pula o
            # resto do processamento (economia de CPU pra mobs ociosos longe
            # de qualquer player). Só se aplica a IDLE: um mob CHASING/
            # ATTACKING/RETURNING tem trabalho pendente (perseguir até o
            # leash ou terminar de voltar pro spawn) e precisa continuar
            # mesmo que o player tenha corrido bem além de 40 tiles — senão
            # ele trava no meio do caminho pra sempre (bug real: o player
            # corria mais rápido que o mob, o gap passava de 40 tiles ANTES
            # do mob se afastar 20 tiles do spawn — leash nunca disparava,
            # e esse freeze sobrescrevia o RETURNING e descartava o path no
            # meio da volta). Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
            # Usa Chebyshev (sem sqrt) para eficiência máxima.
            chebyshev_dist_to_player = max(
                abs(player_current_tile_x - enemy_current_tile_x),
                abs(player_current_tile_y - enemy_current_tile_y)
            )
            if ai_control.state == "IDLE":
                # Verifica players do mesmo mapa — acorda se qualquer um estiver próximo
                _min_cheb = chebyshev_dist_to_player
                for _p_eid_slp, _ptm, _ in self.world.get_entities_with(TileMovement, PlayerControlled):
                    if self._map_filter:
                        _p_ml_slp = self.world.get_component(_p_eid_slp, MapLocation)
                        if not _p_ml_slp or _p_ml_slp.map_file != self._map_filter:
                            continue
                    _d = max(abs(_ptm.current_tile_x - enemy_current_tile_x),
                             abs(_ptm.current_tile_y - enemy_current_tile_y))
                    if _d < _min_cheb:
                        _min_cheb = _d
                if _min_cheb > self.SLEEP_RADIUS_TILES:
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

            # Limpa flags de bloqueio independente de estar se movendo
            ai_control.is_blocked = False
            ai_control.blocked_by_entity_id = -1

            dist_to_player_pixels = math.sqrt(
                (player_position_comp.x - enemy_pos.x)**2 +
                (player_position_comp.y - enemy_pos.y)**2
            )

            # chebyshev_dist_to_player já calculado acima no sleep check
            not_same_tile = current_enemy_tile != (player_current_tile_x, player_current_tile_y)
            # RETURNING: mob já desistiu e está voltando pro spawn — nunca ataca,
            # mesmo que o player ainda esteja dentro do range físico (ex: leash
            # dispara no meio do trajeto de volta). NÃO exclui IDLE/AGGRO_DELAY
            # aqui — são estados pré-combate normais, e o bloco abaixo
            # (in_attack_range → state="ATTACKING") é o próprio mecanismo que
            # promove um mob IDLE/AGGRO_DELAY adjacente pra ATTACKING; excluí-los
            # travaria o mob preso em IDLE pra sempre quando já nasce adjacente
            # ao player (regressão real, pego por tests/test_server.py).
            # Sem o gate de RETURNING: o mob continuava desferindo golpes depois
            # de decidir voltar pro spawn — e como o estado já tinha virado
            # RETURNING quando server/combat_processor.py monta o mapa de
            # atacantes (roda DEPOIS de EnemyAISystem.update() no mesmo tick), o
            # dano chegava ao cliente com attacker=-1 (sem mob pra apontar
            # visualmente) sempre que não havia um ataque anterior em cache —
            # ver arquitetura/PROBLEMAS_ARQUITETURA.md.
            # Facção "neutra"/"amigavel" nunca ataca a partir de IDLE, mesmo
            # adjacente — só quando já saiu de IDLE por algum motivo legítimo
            # (AGGRO_DELAY/CHASING/ATTACKING/KITING, sempre alcançados via
            # aggro por proximidade — já filtrado por is_hostile() acima — ou
            # aggro por dano, que já move o mob pra fora de IDLE no mesmo
            # tick em que acontece). Sistema de Facções, Fase 2. Mob hostil
            # continua podendo atacar direto de IDLE quando nasce/fica
            # adjacente (comportamento pré-existente, ver comentário acima
            # sobre a regressão de test_server.py — não mexer nisso pra
            # facção hostil).
            in_attack_range = (
                not_same_tile and
                ai_control.state != "RETURNING" and
                1 <= chebyshev_dist_to_player <= ai_control.attack_range_tiles and
                (ai_control.state != "IDLE"
                 or is_hostile(self.world, enemy_id, ai_control.target_eid))
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
                _cast_tm  = self._get_tilemap()
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
                        if _MCL:
                            _MCL.reset_block_timer(enemy_id)
                            _MCL.log("ATK_FIRE", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                     type="ranged", dist=f"{chebyshev_dist_to_player}t",
                                     cd_set=f"{enemy_combat_stats.attack_cooldown_timer:.2f}s")
                else:
                    if _MCL and ai_control.ranged_cast_timer > 0:
                        _MCL.log("ATK_BLOCK", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                 why="cast_cancelled(los_or_range_lost)")
                    ai_control.ranged_cast_timer = 0.0  # LOS/alcance perdido: cancela

            # ── Debug de ataque ──────────────────────────────────────────────
            # _DBG_ATK_RACES: debug legado por nome de raça (console print).
            # _MCL: debug completo em arquivo (ativado via mob_combat_debug.DBG_ENABLED).
            if self._DBG_ATK_RACES and ai_control.state == "ATTACKING":
                _dbg_id2 = self.world.get_component(enemy_id, EntityIdentity)
                if _dbg_id2 and _dbg_id2.name in self._DBG_ATK_RACES:
                    import time as _time
                    _ts_dbg = _time.strftime("%H:%M:%S") + f".{int(_time.time() * 1000) % 1000:03d}"
                    _cd_now = enemy_combat_stats.attack_cooldown_timer
                    _attack_fires = (
                        not _player_invisible and in_attack_range and _cd_now <= 0
                    )
                    if _attack_fires:
                        print(f"[MOB-ATK] {_ts_dbg} eid={enemy_id} → ATAQUE_OK  "
                              f"cd={_cd_now:.3f} cheb={chebyshev_dist_to_player}")
                        self._dbg_atk_timers[enemy_id] = 0.0
                    else:
                        _dbg_t2 = self._dbg_atk_timers.get(enemy_id, 0.0)
                        if _dbg_t2 <= 0:
                            _bloq = []
                            if _player_invisible:       _bloq.append("invisivel")
                            if not in_attack_range:     _bloq.append(f"fora_range(cheb={chebyshev_dist_to_player})")
                            if _cd_now > 0:             _bloq.append(f"cd={_cd_now:.2f}s")
                            if tile_movement.is_moving: _bloq.append("moving")
                            print(f"[MOB-ATK] {_ts_dbg} eid={enemy_id} → bloqueado: {'/'.join(_bloq) or '?'}")
                            self._dbg_atk_timers[enemy_id] = self._DBG_ATK_INTERVAL
                        else:
                            self._dbg_atk_timers[enemy_id] = _dbg_t2 - dt

            # _MCL: log de ATK_BLOCK para todos os mobs quando em combate
            if _MCL and ai_control.state in ("ATTACKING", "CHASING"):
                _cd_mcl = enemy_combat_stats.attack_cooldown_timer
                _can_atk = not _player_invisible and in_attack_range and _cd_mcl <= 0
                if not _can_atk:
                    _reasons: list[str] = []
                    if _player_invisible:                       _reasons.append("invisible")
                    if not in_attack_range:                     _reasons.append(f"out_range(cheb={chebyshev_dist_to_player})")
                    if _cd_mcl > 0:                             _reasons.append(f"cd={_cd_mcl:.2f}s")
                    if tile_movement.is_moving:                 _reasons.append("moving")
                    if ai_control.ranged_cast_timer > 0:        _reasons.append(f"casting={ai_control.ranged_cast_timer:.2f}s")
                    if _sfx and _sfx.has("stun"):               _reasons.append("stun")
                    if _sfx and _sfx.has("root"):               _reasons.append("root")
                    _MCL.log_atk_block(enemy_id, _dbg_name, _dbg_race, _dbg_cls, dt, _reasons)

            # ── Inicia ataque (cooldown expirou) ──────────────────────────
            if not _player_invisible and in_attack_range and enemy_combat_stats.attack_cooldown_timer <= 0:
                if ai_control.is_ranged:
                    # Só inicia cast se não estiver já carregando
                    if ai_control.ranged_cast_timer == 0.0:
                        _atk_tm  = self._get_tilemap()
                        _atk_los = (_atk_tm is None or self._has_line_of_sight(
                            _atk_tm,
                            enemy_current_tile_x, enemy_current_tile_y,
                            player_current_tile_x, player_current_tile_y
                        ))
                        if _atk_los:
                            ai_control.ranged_cast_timer = self.RANGED_CAST_TIME
                            if _MCL: _MCL.log("ATK_CAST", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                              cast_s=f"{self.RANGED_CAST_TIME:.1f}s",
                                              dist=f"{chebyshev_dist_to_player}t")
                        else:
                            if _MCL: _MCL.log("ATK_BLOCK", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                              why="no_los(ranged_start)")
                else:
                    SOUNDS.play_emote_attack(is_player=False, mob_sounds_comp=_ms_atk)
                    SOUNDS.play_mob_sounds(_ms_atk, _atk_event, dedup_key=str(enemy_id))
                    _atk_dead, _atk_outcome = deal_damage(
                        attacker_id=enemy_id,
                        target_id=ai_control.target_eid,
                        damage_type=damage_type_to_use
                    )
                    enemy_combat_stats.attack_cooldown_timer = enemy_combat_stats.get_attack_cooldown()
                    if _MCL:
                        try:
                            _MCL.reset_block_timer(enemy_id)
                            _tgt_cs = self.world.get_component(ai_control.target_eid, CombatStats)
                            _tgt_hp = f"{_tgt_cs.current_hp:.0f}/{_tgt_cs.max_hp:.0f}" if _tgt_cs else "?"
                            _MCL.log("ATK_FIRE", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                     type="melee",
                                     outcome=_atk_outcome or "hit",
                                     dist=f"{chebyshev_dist_to_player}t",
                                     mob_tile=f"({enemy_current_tile_x},{enemy_current_tile_y})",
                                     tgt_tile=f"({player_current_tile_x},{player_current_tile_y})",
                                     tgt_hp=_tgt_hp,
                                     cd_set=f"{enemy_combat_stats.attack_cooldown_timer:.2f}s")
                        except Exception as _e_atk:
                            _MCL._open()
                            _MCL._write(f"[ATK_FIRE_ERR] eid={enemy_id} err={type(_e_atk).__name__}: {_e_atk}")

            _is_rooted = _sfx is not None and _sfx.has("root")
            if _is_rooted or tile_movement.is_moving:
                continue  # atacou (se estava em range); bloqueia só o pathfinding

            # --- Hunter Disengage: dash 4 tiles ao se sentir encurralado ---
            if (ai_control.entity_class == "Hunter" and
                    chebyshev_dist_to_player <= 3 and
                    ai_control.disengage_cd <= 0 and
                    dist_to_player_pixels <= detect_radius.radius):
                ai_control.disengage_cd = 8.0
                ai_control.disengage_boost = 3.0
                if _MCL: _MCL.log("KITING", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                  reason="disengage", dist=f"{chebyshev_dist_to_player}t",
                                  prev=_dbg_prev_state)
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
                if _MCL and ai_control.state != "KITING":
                    _MCL.log("KITING", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                             reason="too_close", dist=f"{chebyshev_dist_to_player}t",
                             prev=_dbg_prev_state)
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
                if _MCL and ai_control.state != "ATTACKING":
                    _MCL.log("IN_RANGE", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                             dist=f"{chebyshev_dist_to_player}t",
                             cd=f"{enemy_combat_stats.attack_cooldown_timer:.2f}s",
                             prev=_dbg_prev_state)
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
            _aggro_range_px = self.AGGRO_RADIUS_TILES * TILE_SIZE
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
                if _MCL: _MCL.log("LEASH", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                  dist_spawn=f"{_dist_from_spawn}t",
                                  leash_r=_leash_radius,
                                  prev=_dbg_prev_state)
                ai_control.state              = "RETURNING"
                ai_control.aggroed_by_damage  = False
                ai_control.path_recalc_timer  = 0.0
                ai_control.target_eid         = -1
                tile_movement.path            = []

            # Quando o mob aggroed_by_damage chega perto do player (range normal),
            # transiciona para aggro de proximidade normal
            if ai_control.aggroed_by_damage and dist_to_player_pixels <= _aggro_range_px:
                ai_control.aggroed_by_damage = False

            # Detecção inicial: apenas mobs IDLE, e somente se o player estiver visível.
            # Facção "neutra" (ou "amigavel") nunca agroa por proximidade — só
            # entra em combate se atacada primeiro (ver bloco de aggro por dano
            # em CombatSystem.deal_damage, que já é incondicional e cobre esse
            # caso). Sistema de Facções, Fase 2 (ARQUITETURA_ONLINE.md).
            if (not _player_invisible and dist_to_player_pixels <= _aggro_range_px
                    and ai_control.state == "IDLE"
                    and is_hostile(self.world, enemy_id, target_eid)):
                _tilemap_for_los = self._get_tilemap()
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
                    if _MCL: _MCL.log("AGGRO", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                      dist=f"{dist_to_player_pixels/TILE_SIZE:.1f}t",
                                      target=target_eid)

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
                        if _MCL: _MCL.log("CHASE", enemy_id, _dbg_name, _dbg_race, _dbg_cls,
                                          dist=f"{chebyshev_dist_to_player}t")
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
                                tilemap_comp = self._get_tilemap()
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

                    # Budget exhausted (not a genuine dead-end) → short jitter so mobs
                    # don't all retry at the same frame 0.8 s later (storm effect).
                    if not found_path_to_target and available_attack_tiles and self._pathfind_budget <= 0:
                        ai_control.path_recalc_timer = random.uniform(0.05, 0.25)
                    else:
                        ai_control.path_recalc_timer = self.path_recalc_interval
                    ai_control.last_known_player_tile = player_tile_now
                
                if ai_control.path and not ai_control.is_blocked:
                    if not tile_movement.is_moving:
                        next_tile_on_path_x, next_tile_on_path_y = ai_control.path[0]
                        _next_tile = (next_tile_on_path_x, next_tile_on_path_y)

                        # B2: also guard against another mob already claiming this tile
                        # in the same frame (all_occupied_tiles tracks target tiles too)
                        if self._is_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y) and \
                                _next_tile not in all_occupied_tiles:
                            start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                            ai_control.path.pop(0)
                            all_occupied_tiles.add(_next_tile)  # claim tile for rest of frame
                        else:
                            ai_control.path = None
                            ai_control.path_recalc_timer = 0.0
                elif ai_control.is_blocked:
                    ai_control.state = "BLOCKED_BY_PLAYER"
                else:
                    # Sem caminho disponível: só desiste se o jogador saiu do raio de detecção.
                    # Se ainda estiver em alcance (ex: kite_cooldown ativo), mantém CHASING para
                    # que o pathfinding seja tentado novamente no próximo ciclo.
                    if dist_to_player_pixels > detect_radius.radius:
                        # Desiste de perseguir — mas se estiver longe do spawn, volta pra
                        # casa (RETURNING) em vez de travar em IDLE no meio do caminho.
                        # detect_radius (~8 tiles) é bem menor que o leash (20 tiles), então
                        # esse desistir-por-falha-de-pathfinding podia disparar bem antes do
                        # leash e deixar o mob parado longe do spawn pra sempre (mesma classe
                        # de bug do Sleep Zone — ver arquitetura/PROBLEMAS_ARQUITETURA.md).
                        _ix = int(initial_pos.x / TILE_SIZE)
                        _iy = int(initial_pos.y / TILE_SIZE)
                        _dist_init = abs(_ix - enemy_current_tile_x) + abs(_iy - enemy_current_tile_y)
                        if _dist_init > self.proximity_threshold_tiles:
                            ai_control.state             = "RETURNING"
                            ai_control.target_eid         = -1
                            ai_control.path_recalc_timer  = 0.0
                        else:
                            # Já perto do spawn (desistiu do pathfind sem
                            # nunca ter passado por RETURNING) — mesmo reset
                            # completo dos outros dois pontos de chegada, por
                            # consistência (ver ARQUITETURA_ONLINE.md).
                            ai_control.state = "IDLE"
                            ai_control.aggroed_by_damage = False
                            enemy_combat_stats.current_hp = enemy_combat_stats.max_hp
                            _sfx_reset_c = self.world.get_component(enemy_id, StatusEffects)
                            if _sfx_reset_c:
                                _sfx_reset_c.effects.clear()
                                tile_movement.slow_mult = 1.0   # ver comentário irmão acima

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
                        if not tile_movement.is_moving:
                            next_tile_on_path_x, next_tile_on_path_y = ai_control.path[0]
                            _next_ret_tile = (next_tile_on_path_x, next_tile_on_path_y)
                            if self._is_walkable(enemy_id, next_tile_on_path_x, next_tile_on_path_y) and \
                                    _next_ret_tile not in all_occupied_tiles:
                                start_tile_movement(enemy_pos, tile_movement, next_tile_on_path_x, next_tile_on_path_y)
                                ai_control.path.pop(0)
                                all_occupied_tiles.add(_next_ret_tile)
                            else:
                                ai_control.path = None
                                ai_control.path_recalc_timer = 0.0
                    # Sem path nesta tentativa (ex: budget de pathfinding do
                    # frame esgotado por outras buscas no mesmo tick — comum
                    # quando o mob estava CHASING um alvo distante e a leash
                    # acabou de disparar no mesmo tick) — NÃO desiste pra
                    # IDLE: mantém RETURNING e força recálculo imediato no
                    # próximo tick, mesmo padrão do bloco "sem alvo válido"
                    # (linhas ~2297+) que já lida com isso corretamente. Dar
                    # IDLE aqui travava o mob longe do spawn pra sempre na
                    # primeira falha passageira de pathfinding (bug real,
                    # ver arquitetura/PROBLEMAS_ARQUITETURA.md).
                    elif not ai_control.path:
                        ai_control.path_recalc_timer = 0.0
                else:
                    # Guard igual ao do bloco irmão "sem alvo válido" acima —
                    # este else roda pra QUALQUER mob IDLE ou RETURNING já
                    # perto de casa (a condição externa é
                    # `state not in ("IDLE","RETURNING")` pro bloco de
                    # perseguição, então isto é o "else" de AMBOS), não só na
                    # transição de saída de RETURNING — sem o guard, cura/
                    # limpa debuff every tick de qualquer mob parado em casa.
                    _was_returning_b = ai_control.state == "RETURNING"
                    ai_control.state             = "IDLE"
                    ai_control.aggroed_by_damage = False
                    enemy_pos.x = initial_pos.x
                    enemy_pos.y = initial_pos.y
                    if _was_returning_b:
                        # HP cura via regen gradual, não instantâneo aqui —
                        # ver comentário irmão acima e WorldServer._tick().
                        _sfx_reset_b = self.world.get_component(enemy_id, StatusEffects)
                        if _sfx_reset_b:
                            _sfx_reset_b.effects.clear()
                            tile_movement.slow_mult = 1.0   # ver comentário irmão acima

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
        from content.mob_definitions import PROJECTILE_BY_CLASS as _PBC
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
            tilemap_comp = self._get_tilemap()
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

        if ai_control.path and not tile_movement.is_moving:
            nx, ny = ai_control.path[0]
            if self._is_walkable(enemy_id, nx, ny):
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
                # Stun/sleep interrompe um passo normal já em andamento — sem isso,
                # um mob que estava no meio de um chase-step (iniciado por
                # EnemyAISystem ANTES do stun existir, ex: stun por colisão do
                # Tiro Repulsivo no mesmo tick) terminava o passo mesmo já
                # stunado. NÃO se aplica a is_dash (knockback/Interceptar): esses
                # são deslocamentos forçados que devem completar a animação
                # mesmo que o alvo fique stunado ao final — só passo NORMAL
                # (caminhada/perseguição) é cancelado.
                if not tile_movement.is_dash:
                    _sfx_stop = self.world.get_component(entity_id, StatusEffects)
                    if _sfx_stop and (_sfx_stop.has("stun") or _sfx_stop.has("sleep")):
                        tile_movement.is_moving     = False
                        tile_movement.target_tile_x = tile_movement.current_tile_x
                        tile_movement.target_tile_y = tile_movement.current_tile_y
                        position.x = tile_movement.current_tile_x * TILE_SIZE + TILE_SIZE / 2
                        position.y = tile_movement.current_tile_y * TILE_SIZE + TILE_SIZE / 2
                        continue

                # Emite rastro antes de mover (posição atual do frame)
                if tile_movement.is_dash:
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
                    # Baseline anti-cheat (WorldServer.move_player()): dash do
                    # Interceptar termina AQUI (é o mesmo tween de qualquer
                    # movimento, só com move_duration curto) — sem isto, o
                    # primeiro MOVE normal após o dash pareceria um salto
                    # implausível comparado à posição pré-dash congelada.
                    import time as _time_tw
                    tile_movement._last_valid_tile_x = tile_movement.current_tile_x
                    tile_movement._last_valid_tile_y = tile_movement.current_tile_y
                    tile_movement._last_valid_ts     = _time_tw.time()

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

    def __init__(self, world: World, map_filter: str = "", pathfinding=None) -> None:
        self.world        = world
        self._map_filter  = map_filter
        self._pathfinding = pathfinding

    def _get_tilemap(self):
        if self._pathfinding:
            return self._pathfinding._get_tilemap_component()
        return get_tilemap()

    def update(self, events: list = None, dt: float = 0) -> None:
        # Constrói mapa eid→tile de players vivos NO MESMO MAPA que os mobs deste bundle
        player_tiles: dict[int, tuple[int, int]] = {}
        for p_eid, p_tm, _, p_cs in self.world.get_entities_with(
                TileMovement, PlayerControlled, CombatStats):
            if self._map_filter:
                p_ml = self.world.get_component(p_eid, MapLocation)
                if p_ml is None or p_ml.map_file != self._map_filter:
                    continue
            if p_cs.current_hp > 0:
                player_tiles[p_eid] = (p_tm.current_tile_x, p_tm.current_tile_y)

        if not player_tiles:
            return

        for eid, abilities, etm, ecs in self.world.get_entities_with(
                EnemyAbilities, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue

            # Filtra por mapa (multi-map): mesmo padrão de EnemyAISystem
            if self._map_filter:
                _ml_ab = self.world.get_component(eid, MapLocation)
                if _ml_ab is None or _ml_ab.map_file != self._map_filter:
                    continue

            # Só age se o inimigo está em combate ativo (KITING incluso — mob ranged
            # ainda pode usar habilidades enquanto recua)
            ai = self.world.get_component(eid, AIControlled)
            if not ai or ai.state not in ("ATTACKING", "CHASING", "KITING"):
                continue

            # Inimigo atordoado não usa habilidades
            sfx = self.world.get_component(eid, StatusEffects)
            if sfx and sfx.has("stun"):
                continue

            ex, ey = etm.current_tile_x, etm.current_tile_y

            # Determina o alvo: usa target_eid do AIControlled se válido, senão player mais próximo
            target_p_eid = ai.target_eid if ai.target_eid in player_tiles else -1
            if target_p_eid == -1:
                # Alvo real do mob é OUTRO combatente (mob/NPC — Sistema de
                # Facções, Fase 5): não dispara habilidade em bystander
                # player nenhum. Habilidades contra alvo não-player ainda
                # não são suportadas (os handlers de efeito/projétil abaixo
                # assumem player) — anotado como trabalho futuro; auto-attack
                # normal do mob já cobre o combate mob-vs-mob.
                if ai.target_eid != -1 and ai.target_eid not in player_tiles:
                    continue
                # Fallback: player mais próximo — só HOSTIL (um guarda
                # amigável com habilidades nunca deve mirar num player).
                best_dist = float("inf")
                for p_eid, (px, py) in player_tiles.items():
                    if not is_hostile(self.world, eid, p_eid):
                        continue
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

                if defn.range_tiles > 1:
                    # ── Habilidade ranged: requer LOS + lança projétil ──────
                    _tm_ab = self._get_tilemap()
                    if _tm_ab is not None and not EnemyAISystem._has_line_of_sight(
                            _tm_ab, ex, ey, px, py):
                        if _MCL:
                            _ident_ab = self.world.get_component(eid, EntityIdentity)
                            _n_ab = _ident_ab.name if _ident_ab else f"mob#{eid}"
                            _r_ab = _ident_ab.race if _ident_ab else "?"
                            _c_ab = _ident_ab.entity_class if _ident_ab else "?"
                            _MCL.log("ABILITY_BLK", eid, _n_ab, _r_ab, _c_ab,
                                     ability=slot.ability_id, dist=f"{dist}t")
                        continue  # sem LOS — não dispara, não consome cooldown

                    # Calcula direção do projétil
                    _atk_pos_ab = self.world.get_component(eid, Position)
                    if not _atk_pos_ab:
                        continue
                    _tgt_pos_ab = self.world.get_component(target_p_eid, Position)
                    _dir_x_ab, _dir_y_ab = 1.0, 0.0
                    if _tgt_pos_ab:
                        _dpx_ab = _tgt_pos_ab.x - _atk_pos_ab.x
                        _dpy_ab = _tgt_pos_ab.y - _atk_pos_ab.y
                        _dd_ab  = math.sqrt(_dpx_ab * _dpx_ab + _dpy_ab * _dpy_ab)
                        if _dd_ab > 0:
                            _dir_x_ab, _dir_y_ab = _dpx_ab / _dd_ab, _dpy_ab / _dd_ab

                    # Cria projétil com ability_id — ProjectileSystem aplica o DoT ao acertar
                    _aproj = self.world.create_entity()
                    self.world.add_component(
                        _aproj,
                        Position(x=_atk_pos_ab.x, y=_atk_pos_ab.y,
                                 prev_x=_atk_pos_ab.x, prev_y=_atk_pos_ab.y))
                    self.world.add_component(
                        _aproj,
                        Projectile(
                            attacker_id = eid,
                            target_id   = target_p_eid,
                            damage_type = "magical",
                            speed       = 320.0,
                            color       = defn.proj_color,
                            is_arrow    = defn.proj_is_arrow,
                            dir_x       = _dir_x_ab,
                            dir_y       = _dir_y_ab,
                            ability_id  = slot.ability_id,
                        ))
                    if _MCL:
                        _ident_ab = self.world.get_component(eid, EntityIdentity)
                        _n_ab = _ident_ab.name if _ident_ab else f"mob#{eid}"
                        _r_ab = _ident_ab.race if _ident_ab else "?"
                        _c_ab = _ident_ab.entity_class if _ident_ab else "?"
                        _MCL.log("ABILITY", eid, _n_ab, _r_ab, _c_ab,
                                 ability=slot.ability_id, dist=f"{dist}t",
                                 cd_set=f"{slot.cooldown:.0f}s")
                else:
                    # ── Habilidade melee (range=1): aplica efeito direto ───
                    # magnitude é multiplicador do attack_power do mob (ver
                    # enemy_abilities_data.py) — escala com level/tier.
                    _ability_dmg = defn.magnitude * ecs.base_attack_power
                    apply_effect(
                        self.world, target_p_eid,
                        defn.effect_type, defn.duration, _ability_dmg,
                        tick_interval=defn.tick_interval,
                    )
                    PROC.add(defn.name, (220, 80, 180))
                    ident = self.world.get_component(eid, EntityIdentity)
                    mob_name = ident.name if ident else "Inimigo"
                    LOG.add(f"{mob_name} usou {defn.name}!", (220, 80, 180))
                    if _MCL:
                        _MCL.log("ABILITY", eid, mob_name,
                                 ident.race if ident else "?",
                                 ident.entity_class if ident else "?",
                                 ability=slot.ability_id, dist=f"{dist}t",
                                 cd_set=f"{slot.cooldown:.0f}s")

                slot.current_cooldown = slot.cooldown


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

    def __init__(self, world: World, map_filter: str = "", pathfinding=None):
        self.world = world
        self._map_filter = map_filter
        self._pathfinding = pathfinding
        # Fila de spawns pendentes: (zone_eid, zone, tile_x, tile_y)
        self._spawn_queue: list = []

    def _get_tilemap(self):
        if self._pathfinding:
            return self._pathfinding._get_tilemap_component()
        return get_tilemap()

    def update(self, events=None, dt: float = 0) -> None:
        # Posição do player do mesmo mapa para culling de zonas distantes
        player_tx, player_ty = 0, 0
        for _sz_peid, ptm, _ in self.world.get_entities_with(TileMovement, PlayerControlled):
            if self._map_filter:
                _sz_pml = self.world.get_component(_sz_peid, MapLocation)
                if _sz_pml is None or _sz_pml.map_file != self._map_filter:
                    continue
            player_tx = ptm.current_tile_x
            player_ty = ptm.current_tile_y
            break

        # Tiles ocupados (evita spawnar em cima de outra entidade) — filtrado por mapa.
        occupied: set = set()
        if self._map_filter:
            for _occ_eid, tm in self.world.get_entities_with(TileMovement):
                _ml_occ = self.world.get_component(_occ_eid, MapLocation)
                if _ml_occ is None or _ml_occ.map_file != self._map_filter:
                    continue
                occupied.add((tm.current_tile_x, tm.current_tile_y))
                if tm.is_moving:
                    occupied.add((tm.target_tile_x, tm.target_tile_y))
        else:
            for _, tm in self.world.get_entities_with(TileMovement):
                occupied.add((tm.current_tile_x, tm.current_tile_y))
                if tm.is_moving:
                    occupied.add((tm.target_tile_x, tm.target_tile_y))

        # Tilemap correto para este bundle (via injeção direta, P4).
        tilemap_comp = self._get_tilemap()

        for zone_eid, zone in self.world.get_entities_with(SpawnZone):
            # Filtra por mapa (multi-map): pula zonas que não são deste bundle
            if self._map_filter:
                _ml_sz = self.world.get_component(zone_eid, MapLocation)
                if _ml_sz is None or _ml_sz.map_file != self._map_filter:
                    continue

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
            faction=zone.faction,
        )
        self.world.add_component(new_eid, SpawnZoneOwner(zone_eid))
        if self._map_filter:
            self.world.add_component(new_eid, MapLocation(self._map_filter))
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
