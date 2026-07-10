"""Diagnostico: SKILL_EFFECT (som) emitido para punho_no_queixo vs golpe_poderoso."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, teleport_mob_to_player
from engine.components import CombatStats, CombatState, CharacterStats, PlayerSkills

ws = make_world_server()
eid = spawn_player(ws, "s1", 130, 374, class_id="guerreiro")
run_ticks(ws, 50)
mob = first_mob(ws)
teleport_mob_to_player(ws, mob, eid)

cs = ws.world.get_component(eid, CombatStats)
cs.pnq_enabled = True
cs.pnq_stun_duration = 1.0
cs.acerto = 100.0

ps = ws.world.get_component(eid, PlayerSkills)
from content.skill_config import SKILL_CATALOG
pnq_sk = next((sk for sk in ps.skills if sk and sk.skill_id == 'punho_no_queixo'), None)
if pnq_sk is None:
    pnq_sk = PlayerSkills._make_skill('punho_no_queixo', SKILL_CATALOG)
    idx = ps.skills.index(None)
    ps.skills[idx] = pnq_sk
pnq_sk.charges = 1
pnq_sk.current_cooldown = 0.0

mob_cs = ws.world.get_component(mob, CombatStats)
mob_cs.current_hp = mob_cs.max_hp
mob_cs.dodge_rating = 0.0
mob_cs.parry_rating = 0.0

cst = ws.world.get_component(eid, CombatState)
cst.target_entity_id = mob
cst.is_pursuing = True

print(f"--- ANTES: pnq charges={pnq_sk.charges} cd={pnq_sk.current_cooldown} ---")
ws._skill_effects_this_tick.clear()
ws._skill_results_this_tick.clear()
ws._pending_skill_requests.append({
    "player_eid": eid, "sid": "punho_no_queixo",
    "tid": mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
})
ws._process_skill_requests()
print("skill_effects_this_tick (PNQ):", ws._skill_effects_this_tick)
print("skill_results_this_tick (PNQ):", ws._skill_results_this_tick)
print(f"mob_hp_after={mob_cs.current_hp}  pnq_charges_after={pnq_sk.charges}")

print()
print("--- comparando com golpe_poderoso (instant skill conhecida) ---")
gp_sk = next((sk for sk in ps.skills if sk and sk.skill_id == 'golpe_poderoso'), None)
if gp_sk is None:
    gp_sk = PlayerSkills._make_skill('golpe_poderoso', SKILL_CATALOG)
    idx = ps.skills.index(None)
    ps.skills[idx] = gp_sk
gp_sk.current_cooldown = 0.0
char = ws.world.get_component(eid, CharacterStats)
char.rage = 100
mob_cs.current_hp = mob_cs.max_hp
ws._skill_effects_this_tick.clear()
ws._skill_results_this_tick.clear()
ws._pending_skill_requests.append({
    "player_eid": eid, "sid": "golpe_poderoso",
    "tid": mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
})
ws._process_skill_requests()
print("skill_effects_this_tick (GP):", ws._skill_effects_this_tick)
print("skill_results_this_tick (GP):", ws._skill_results_this_tick)
