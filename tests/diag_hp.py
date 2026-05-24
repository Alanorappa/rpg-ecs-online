"""Diagnóstico: o que acontece quando client_max_hp=0 ou não enviado."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.world_server import WorldServer
from components import CombatStats

print("=== Cenário 1: client_max_hp=0 (cliente não enviou) ===")
ws = WorldServer()
char_data = {
    'tile_x': 115, 'tile_y': 389, 'hp': 20, 'level': 1,
    'class_id': 'guerreiro', 'name': 'teste',
    'client_max_hp': 0, 'client_ap': 0.0,   # cliente não enviou
    'stats': {}, 'inventory': [], 'skills': {}, 'talents': {}
}
eid = ws.spawn_player('s1', char_data)
cs = ws.world.get_component(eid, CombatStats)
print(f"  spawn: current_hp={cs.current_hp}  max_hp={cs.max_hp}")
# Simula morte
ws._handle_player_death(eid)
print(f"  após respawn: current_hp={cs.current_hp}  max_hp={cs.max_hp}")

print()
print("=== Cenário 2: client_max_hp=210 (cliente enviou correto) ===")
ws2 = WorldServer()
char_data2 = dict(char_data)
char_data2['client_max_hp'] = 210
char_data2['client_ap'] = 50.0
eid2 = ws2.spawn_player('s1', char_data2)
cs2 = ws2.world.get_component(eid2, CombatStats)
print(f"  spawn: current_hp={cs2.current_hp}  max_hp={cs2.max_hp}")
ws2._handle_player_death(eid2)
print(f"  após respawn: current_hp={cs2.current_hp}  max_hp={cs2.max_hp}")

print()
print("=== Cenário 3: stats_json tem max_hp=20 (DB corrompido?) ===")
import json
ws3 = WorldServer()
char_data3 = dict(char_data)
char_data3['stats_json'] = json.dumps({'max_hp': 20, 'attack_power': 50})
eid3 = ws3.spawn_player('s1', char_data3)
cs3 = ws3.world.get_component(eid3, CombatStats)
print(f"  spawn: current_hp={cs3.current_hp}  max_hp={cs3.max_hp}")
