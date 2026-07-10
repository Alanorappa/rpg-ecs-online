"""Diagnóstico: loot pipeline end-to-end + XP level up."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.world_server import WorldServer
from engine.components import CombatStats, PendingDeath, EntityIdentity, AIControlled, TileMovement

ws = WorldServer()
char_data = {'tile_x':130,'tile_y':374,'hp':200,'level':1,'class_id':'guerreiro',
             'name':'teste','client_max_hp':200,'client_ap':50.0,
             'stats':{},'inventory':[],'skills':{},'talents':{}}
eid = ws.spawn_player('s1', char_data)

import asyncio
async def run(n):
    for _ in range(n): ws._tick(0.05)

asyncio.run(run(50))

mob = next(iter(ws._mob_eids), None)
print(f"Mob: {mob}")
if mob:
    mob_cs = ws.world.get_component(mob, CombatStats)
    identity = ws.world.get_component(mob, EntityIdentity)
    print(f"  identity.name='{identity.name if identity else None}'")

    # Simula kill via damage log (como _process_player_attacks faz)
    ws._mob_damage_log[mob] = {eid: mob_cs.max_hp}
    mob_cs.current_hp = 0
    ws.world.add_component(mob, PendingDeath(killer_entity_id=eid))

    print(f"  Antes do tick: pending_loot={ws._death_handler.pending_loot}")
    asyncio.run(run(1))
    print(f"  Após tick: _corpses={ws._corpses}")
    print(f"  Após tick: _pending_loot_notifications={ws._pending_loot_notifications}")

# === XP: process_levelups ===
print()
print("=== XP ===")
from engine.stats_system import process_levelups
from engine.components import CharacterStats, PermanentStats
from engine.world import World
w2 = World()
from engine.entity_factory import create_player
p2 = create_player(w2, 1000, 1000, "maps/map_1.csv")
char = w2.get_component(p2, CharacterStats)
cs = w2.get_component(p2, CombatStats)
perm = w2.get_component(p2, PermanentStats)
print(f"  Antes: level={char.level}, xp={char.current_xp}/{char.xp_to_next_level}")
char.current_xp = char.xp_to_next_level + 10
process_levelups(w2, p2, char, cs, perm)
print(f"  Após process_levelups: level={char.level}, xp={char.current_xp}/{char.xp_to_next_level}")
