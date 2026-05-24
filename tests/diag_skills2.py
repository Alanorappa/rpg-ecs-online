"""Diagnóstico: rage sync + skill flow."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob
from components import CombatStats, CombatState, TileMovement, CharacterStats, Position

ws = make_world_server()
eid = spawn_player(ws, "s1", 130, 374)
run_ticks(ws, 40)

mob = first_mob(ws)
if not mob:
    print("ERRO: sem mobs"); sys.exit(1)

# Setup
ptm = ws.world.get_component(eid, TileMovement)
mob_tm = ws.world.get_component(mob, TileMovement)
mob_pos = ws.world.get_component(mob, Position)
mob_tm.current_tile_x = ptm.current_tile_x + 1
mob_tm.current_tile_y = ptm.current_tile_y
if mob_pos:
    mob_pos.x = mob_tm.current_tile_x * 32 + 16
    mob_pos.y = mob_tm.current_tile_y * 32 + 16

cs_mob = ws.world.get_component(mob, CombatState)
if cs_mob: cs_mob.target_entity_id = eid
player_cst = ws.world.get_component(eid, CombatState)
if player_cst: player_cst.target_entity_id = mob

print("=== TESTE RAGE SYNC ===")
char = ws.world.get_component(eid, CharacterStats)
print(f"1. char.rage inicial no servidor: {char.rage}")

# Simula sync_player_resources (cliente enviaria rage=30)
ws.sync_player_resources("s1", rage=30, mana=100)
print(f"2. char.rage após sync(30): {char.rage}")

# Processa a skill
ws.queue_skill("s1", "golpe_poderoso", tid=mob, dir_x=0, dir_y=0, ts=0)
ws._process_skill_requests()
print(f"3. char.rage após golpe_poderoso: {char.rage}")
print(f"4. _pending_xp_deliveries: {ws._pending_xp_deliveries}")

print()
print("=== TESTE RANGE CHECK ===")
# Testar com mob distante (5 tiles) — deve falhar
ws.sync_player_resources("s1", rage=100, mana=100)
mob_tm.current_tile_x = ptm.current_tile_x + 5  # fora de range
mob_cs = ws.world.get_component(mob, CombatStats)
hp_before = mob_cs.current_hp if mob_cs else 0
ws.queue_skill("s1", "golpe_poderoso", tid=mob, dir_x=0, dir_y=0, ts=0)
ws._process_skill_requests()
hp_after = mob_cs.current_hp if mob_cs else 0
print(f"Mob a 5 tiles — HP mudou: {hp_before != hp_after} (esperado: False = range check ok)")
print(f"Rage consumida: {100 - char.rage} (esperado: 0 se range check falhou)")
