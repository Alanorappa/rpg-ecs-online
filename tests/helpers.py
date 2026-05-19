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


def teleport_mob_to_player(ws, mob_eid: int, player_eid: int, offset_x: int = 1):
    """Move um mob para o tile adjacente ao player (para testar combate melee)."""
    from components import TileMovement
    mob_tm = ws.world.get_component(mob_eid, TileMovement)
    ptm    = ws.world.get_component(player_eid, TileMovement)
    if mob_tm and ptm:
        mob_tm.current_tile_x = ptm.current_tile_x + offset_x
        mob_tm.current_tile_y = ptm.current_tile_y


def first_mob(ws) -> int | None:
    return next(iter(ws._mob_eids), None)


def get_mob_hp(ws, mob_eid: int) -> tuple[int, int]:
    from components import CombatStats
    cs = ws.world.get_component(mob_eid, CombatStats)
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)


def get_player_hp(ws, session_id: str) -> tuple[int, int]:
    from components import CombatStats
    eid = ws._player_eids.get(session_id)
    cs  = ws.world.get_component(eid, CombatStats) if eid else None
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)
