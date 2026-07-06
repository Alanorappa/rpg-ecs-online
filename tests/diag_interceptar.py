"""Diagnostico: Interceptar com caminho bloqueado deve mandar correcao rejected=True."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (make_world_server, spawn_player, run_ticks, first_mob,
                           set_entity_tile, authorize_skill)
from components import CombatState, CombatStats, PlayerSkills, TileMovement, CharacterStats

ws = make_world_server()
eid = spawn_player(ws, "s1", 130, 374, class_id="guerreiro")
run_ticks(ws, 50)
mob = first_mob(ws)
authorize_skill(ws, eid, "interceptar")

# Confirma que (134,374) eh parede (achado em sessao anterior)
from components import Tilemap
tc = None
for _, t in ws.world.get_entities_with(Tilemap):
    tc = t; break
print("tile (134,374) is_solid:", tc.tile_matrix[374][134].is_solid)

# Mob do outro lado da parede -> path bloqueado
set_entity_tile(ws, mob, 136, 374)

cst = ws.world.get_component(eid, CombatState)
cst.target_entity_id = mob
cst.is_pursuing = True

char = ws.world.get_component(eid, CharacterStats)
char.rage = 100

ps = ws.world.get_component(eid, PlayerSkills)
from skill_config import SKILL_CATALOG
sk = next((s for s in ps.skills if s and s.skill_id == 'interceptar'), None)
if sk is None:
    sk = PlayerSkills._make_skill('interceptar', SKILL_CATALOG)
    idx = ps.skills.index(None)
    ps.skills[idx] = sk
sk.current_cooldown = 0.0

tm_before = ws.world.get_component(eid, TileMovement)
_start_tile = (tm_before.current_tile_x, tm_before.current_tile_y)
print(f"--- ANTES: player tile={_start_tile} ---")

ws._skill_position_corrections.clear()
ws._skill_results_this_tick.clear()
ws._pending_skill_requests.append({
    "player_eid": eid, "sid": "interceptar",
    "tid": mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
})
ws._process_skill_requests()

tm_after = ws.world.get_component(eid, TileMovement)
print(f"--- DEPOIS (caminho bloqueado): player tile=({tm_after.current_tile_x},{tm_after.current_tile_y}) ---")
print("skill_results_this_tick:", ws._skill_results_this_tick)
print("skill_position_corrections:", ws._skill_position_corrections)

assert (tm_after.current_tile_x, tm_after.current_tile_y) == _start_tile, "Player NAO deveria ter se movido (caminho bloqueado)"
assert any(c.get("rejected") for c in ws._skill_position_corrections), \
    "ERRO: nenhuma correcao 'rejected' foi enviada -- bug original ainda presente"
print("OK: posicao nao mudou no servidor E correcao rejected foi enviada")

print()
print("--- agora testando sucesso (sem obstaculo) ---")
set_entity_tile(ws, mob, 133, 374)  # 3 tiles (96px > INTERCEPT_MIN_RANGE_PX=74), sem parede no meio
sk.current_cooldown = 0.0
ws._skill_position_corrections.clear()
ws._skill_results_this_tick.clear()
ws._pending_skill_requests.append({
    "player_eid": eid, "sid": "interceptar",
    "tid": mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
})
ws._process_skill_requests()
tm_after2 = ws.world.get_component(eid, TileMovement)
print(f"player tile apos sucesso=({tm_after2.current_tile_x},{tm_after2.current_tile_y})")
print("skill_position_corrections:", ws._skill_position_corrections)
assert (tm_after2.current_tile_x, tm_after2.current_tile_y) != _start_tile, \
    "Player deveria ter se movido (sem obstaculo)"
assert ws._skill_position_corrections and not any(c.get("rejected") for c in ws._skill_position_corrections), \
    "Correcao de sucesso nao deveria ter rejected=True"
print("OK: sucesso ainda move o player normalmente, sem rejected")
