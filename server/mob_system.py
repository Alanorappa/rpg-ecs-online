"""
server/mob_system.py
Sistema de IA de mobs para o servidor — sem pathfinding, sem pygame.

Responsabilidades:
- Aggro: detecta players próximos e define CombatState.target_entity_id
- Movimento: aproxima 1 tile do player por vez (sem pathfinding completo)
- Não usa register_services() nem qualquer recurso exclusivo do cliente
"""
from __future__ import annotations
import random
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


AGGRO_RANGE   = 8    # tiles — distância para agrrar
LEASH_RANGE   = 20   # tiles — distância para largar o alvo
# MOVE_INTERVAL não é constante — calculado por mob a partir de TileMovement.speed


class ServerMobSystem:
    """
    IA de mob mínima e segura para o servidor.
    Substitui EnemyAISystem sem dependências do cliente.
    """

    def __init__(self, world, player_eids_ref: dict, mob_eids_ref: set):
        self.world         = world
        self._player_eids  = player_eids_ref   # referência ao dict do WorldServer
        self._mob_eids     = mob_eids_ref       # referência ao set do WorldServer
        self._move_timers: dict[int, float] = {}  # mob_eid → tempo até próximo passo

    def update(self, dt: float = 0) -> None:
        from components import CombatState, TileMovement
        from utils import chebyshev

        for mob_eid in list(self._mob_eids):
            mob_cs = self.world.get_component(mob_eid, CombatState)
            mob_tm = self.world.get_component(mob_eid, TileMovement)
            if not mob_cs or not mob_tm:
                continue

            # ── 1. Aggro ──────────────────────────────────────────────
            if mob_cs.target_entity_id == -1:
                self._try_aggro(mob_eid, mob_cs, mob_tm)
            else:
                # Verifica se player ainda existe e está dentro do leash
                self._check_leash(mob_eid, mob_cs, mob_tm)

            # ── 2. Movimento em direção ao alvo ───────────────────────
            if mob_cs.target_entity_id != -1:
                self._move_toward_target(mob_eid, mob_cs, mob_tm, dt)

    def _try_aggro(self, mob_eid: int, mob_cs, mob_tm) -> None:
        from utils import chebyshev
        from components import TileMovement, CombatStats, CombatState as _CSTp
        best_dist = AGGRO_RANGE + 1
        best_eid  = -1
        for player_eid in self._player_eids.values():
            pcs = self.world.get_component(player_eid, CombatStats)
            if pcs and pcs.current_hp <= 0:
                continue  # não aggra cadáver
            pcst = self.world.get_component(player_eid, _CSTp)
            if pcst and not pcst.is_visible:
                continue  # não aggra ghost/camuflagem
            ptm = self.world.get_component(player_eid, TileMovement)
            if not ptm:
                continue
            d = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                          ptm.current_tile_x,    ptm.current_tile_y)
            if d < best_dist:
                best_dist = d
                best_eid  = player_eid
        if best_eid != -1:
            mob_cs.target_entity_id = best_eid

    def _is_walkable(self, tx: int, ty: int) -> bool:
        """Verifica walkability via tilemap do servidor — mesma lógica do offline."""
        from components import Tilemap
        for _, tc in self.world.get_entities_with(Tilemap):
            rows = tc.tile_matrix
            if not rows or ty < 0 or ty >= len(rows):
                return False
            row = rows[ty]
            if tx < 0 or tx >= len(row):
                return False
            return not row[tx].is_solid
        return True  # sem tilemap carregado

    def _check_leash(self, mob_eid: int, mob_cs, mob_tm) -> None:
        from components import TileMovement, CombatStats, CombatState as _CSTl
        from utils import chebyshev
        target = mob_cs.target_entity_id
        # Larga alvo morto ou invisível imediatamente
        pcs = self.world.get_component(target, CombatStats)
        if pcs and pcs.current_hp <= 0:
            mob_cs.target_entity_id = -1
            return
        pcst = self.world.get_component(target, _CSTl)
        if pcst and not pcst.is_visible:
            mob_cs.target_entity_id = -1
            return
        ptm = self.world.get_component(target, TileMovement)
        if not ptm:
            mob_cs.target_entity_id = -1
            return
        d = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                      ptm.current_tile_x,    ptm.current_tile_y)
        if d > LEASH_RANGE:
            mob_cs.target_entity_id = -1

    def _move_toward_target(self, mob_eid: int, mob_cs, mob_tm, dt: float) -> None:
        """Move 1 tile em direção ao player se não estiver em melee range."""
        from components import TileMovement, Position
        from utils import chebyshev, start_tile_movement
        from tileset import TILE_SIZE

        ptm = self.world.get_component(mob_cs.target_entity_id, TileMovement)
        if not ptm:
            return
        dist = chebyshev(mob_tm.current_tile_x, mob_tm.current_tile_y,
                         ptm.current_tile_x,    ptm.current_tile_y)
        if dist <= 1:
            return   # já em melee range, combate cuida do resto

        # Intervalo de movimento = move_duration do mob (mesma velocidade do offline)
        move_interval = mob_tm.move_duration if mob_tm.move_duration > 0 else 0.35
        timer = self._move_timers.get(mob_eid, 0.0) - dt
        if timer > 0:
            self._move_timers[mob_eid] = timer
            return
        self._move_timers[mob_eid] = move_interval

        # Direção em direção ao player — tenta eixo principal, depois alternativo
        dx = ptm.current_tile_x - mob_tm.current_tile_x
        dy = ptm.current_tile_y - mob_tm.current_tile_y
        step_x = (1 if dx > 0 else -1) if dx != 0 else 0
        step_y = (1 if dy > 0 else -1) if dy != 0 else 0

        # Candidatos de movimento em ordem de preferência
        if abs(dx) >= abs(dy):
            candidates = [
                (mob_tm.current_tile_x + step_x, mob_tm.current_tile_y),
                (mob_tm.current_tile_x,           mob_tm.current_tile_y + step_y),
            ]
        else:
            candidates = [
                (mob_tm.current_tile_x,           mob_tm.current_tile_y + step_y),
                (mob_tm.current_tile_x + step_x,  mob_tm.current_tile_y),
            ]

        new_tx, new_ty = None, None
        for cx, cy in candidates:
            if self._is_walkable(cx, cy):
                new_tx, new_ty = cx, cy
                break

        if new_tx is None:
            return  # bloqueado em todas as direções

        mob_tm.current_tile_x = new_tx
        mob_tm.current_tile_y = new_ty
        mob_tm.target_tile_x  = new_tx
        mob_tm.target_tile_y  = new_ty
        pos = self.world.get_component(mob_eid, Position)
        if pos:
            pos.prev_x = pos.x
            pos.prev_y = pos.y
            pos.x = new_tx * TILE_SIZE + TILE_SIZE // 2
            pos.y = new_ty * TILE_SIZE + TILE_SIZE // 2
