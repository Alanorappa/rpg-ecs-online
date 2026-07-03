"""
tests/helpers.py
Utilitários compartilhados pelos testes.
"""
import os, sys, asyncio
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()


def make_world_server():
    from server.world_server import WorldServer
    return WorldServer()


def spawn_player(ws, session_id: str, tile_x: int, tile_y: int,
                 class_id: str = "guerreiro", hp: int = 200) -> int:
    char = {
        "tile_x": tile_x, "tile_y": tile_y,
        "name": session_id, "class_id": class_id,
        "hp": hp, "level": 1,
        "stats_json": '{"attack_power": 50, "max_hp": ' + str(hp) + '}',
    }
    return ws.spawn_player(session_id, char)


def authorize_skill(ws, eid: int, sid: str) -> None:
    """Autoriza `sid` pro player no servidor: learned_skill_ids + talento
    (quando a skill é desbloqueada por talento). Necessário desde o gate
    autoritativo de world_systems.is_skill_authorized — fixtures de teste
    que castam skill devem 'aprender' primeiro, como um player real."""
    from components import PlayerSkills, TalentTree
    from world_systems import _TALENT_SKILL_REQS
    ps = ws.world.get_component(eid, PlayerSkills)
    if ps is not None:
        ps.learned_skill_ids.add(sid)
    req = _TALENT_SKILL_REQS.get(sid)
    if req is not None:
        tid, min_pts = req
        tt = ws.world.get_component(eid, TalentTree)
        if tt is not None:
            tt.allocated[tid] = max(tt.allocated.get(tid, 0), min_pts)


def run_ticks(ws, n: int, dt: float = 0.05) -> dict:
    """Roda N ticks e retorna todos os deltas acumulados."""
    accumulated = {
        "combat": [], "spawned": [], "despawned": [],
        "moved": [], "player_deaths": [],
    }
    orig = ws._collect_deltas
    def patched():
        d = orig()
        for k in accumulated:
            accumulated[k].extend(d.get(k, []))
        return d
    ws._collect_deltas = patched

    async def _run():
        for _ in range(n):
            ws._tick(dt)

    asyncio.run(_run())
    ws._collect_deltas = orig
    return accumulated


def set_entity_tile(ws, eid: int, tx: int, ty: int) -> None:
    """Sincroniza TileMovement E Position para o tile dado."""
    from components import TileMovement, Position
    from tileset import TILE_SIZE
    tm = ws.world.get_component(eid, TileMovement)
    pos = ws.world.get_component(eid, Position)
    if tm:
        tm.current_tile_x = tx;  tm.current_tile_y = ty
        tm.target_tile_x  = tx;  tm.target_tile_y  = ty
        tm.is_moving = False;    tm.progress = 0.0
    if pos:
        pos.x = tx * TILE_SIZE + TILE_SIZE // 2
        pos.y = ty * TILE_SIZE + TILE_SIZE // 2
        pos.prev_x = pos.x;     pos.prev_y = pos.y


def teleport_mob_to_player(ws, mob_eid: int, player_eid: int, offset_x: int = 1):
    """Move mob para o tile adjacente ao player, sincroniza Position e reseta AI."""
    from components import TileMovement, AIControlled
    ptm = ws.world.get_component(player_eid, TileMovement)
    if ptm:
        set_entity_tile(ws, mob_eid, ptm.current_tile_x + offset_x, ptm.current_tile_y)
    # Reseta AI state para IDLE — garante que aggro check vai funcionar
    ai = ws.world.get_component(mob_eid, AIControlled)
    if ai:
        ai.state              = "IDLE"
        ai.path               = []
        ai.aggroed_by_damage  = False
        ai.path_recalc_timer  = 0.0


def first_mob(ws) -> int | None:
    return next(iter(ws._mob_eids), None)


def first_ai_mob(ws) -> int | None:
    """Returns first mob in _mob_eids that has an AIControlled component."""
    from components import AIControlled
    for eid in ws._mob_eids:
        if ws.world.get_component(eid, AIControlled):
            return eid
    return None


def get_mob_hp(ws, mob_eid: int) -> tuple[int, int]:
    from components import CombatStats
    cs = ws.world.get_component(mob_eid, CombatStats)
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)


def get_player_hp(ws, session_id: str) -> tuple[int, int]:
    from components import CombatStats
    eid = ws._player_eids.get(session_id)
    cs  = ws.world.get_component(eid, CombatStats) if eid else None
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)
