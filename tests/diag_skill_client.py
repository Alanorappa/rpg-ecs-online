"""
Diagnóstico: SKILL_RESULT fluxo servidor → cliente

1. WorldServer + player + mob adjacente
2. queue_skill → _tick
3. consume_skill_results → verifica payload
4. Simula handler _apply_combat_result do cliente
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from server.world_server import WorldServer

# ─── Setup ──────────────────────────────────────────────────────────────────
ws = WorldServer()

# Spawna player na posição 10,10
char_data = {
    "tile_x": 10, "tile_y": 10,
    "hp": 200, "hp_max": 200,
    "class_id": "guerreiro",
    "level": 5, "xp": 0,
    "base_strength": 20, "base_agility": 10, "base_intelligence": 5,
    "base_vitality": 15,
    "equipment": {},
    "skills": ["golpe_poderoso"],
    "id": 1,
}
sid_player = "sess_test"
player_eid = ws.spawn_player(sid_player, char_data)
print(f"player_eid={player_eid}")

# Encontra um mob spawnado pelo SpawnZoneSystem ou cria um
from entity_factory import create_enemy
from components import TileMovement, CombatStats, Position

mob_eid = create_enemy(ws.world, 11, 10, attack_range=1, level=3)
ws._mob_eids.add(mob_eid)
print(f"mob_eid={mob_eid}")

# Verifica HP do mob
mob_cs = ws.world.get_component(mob_eid, CombatStats)
print(f"mob HP antes: {mob_cs.current_hp if mob_cs else 'SEM CombatStats'}")

# ─── Fila a skill ────────────────────────────────────────────────────────────
ws.queue_skill(sid_player, "golpe_poderoso", tid=mob_eid)
print(f"skill enfileirada")

# ─── Tick ────────────────────────────────────────────────────────────────────
ws._tick(dt=0.05)
print(f"_tick executado")

# ─── Verifica resultados ─────────────────────────────────────────────────────
results = ws.consume_skill_results()
print(f"\nconsumed skill_results: {len(results)}")
for r in results:
    print(f"  caster_eid={r['caster_eid']} sid={r['sid']} targets={r['targets']}")

mob_cs_after = ws.world.get_component(mob_eid, CombatStats)
print(f"mob HP depois: {mob_cs_after.current_hp if mob_cs_after else 'morto/removido'}")

# ─── Simula handler do cliente ───────────────────────────────────────────────
print("\n--- Simulação do handler cliente ---")

# _remote_mobs[server_eid] = local_eid (aqui server_eid == local_eid pois é teste unitário)
_remote_mobs = {mob_eid: mob_eid}
_mob_hp = {mob_eid: (mob_cs.current_hp if mob_cs else 100, 100)}

for r in results:
    for t in r["targets"]:
        server_target = t.get("eid", -1)
        damage        = t.get("damage", 0)
        hp_after      = t.get("hp_after", -1)
        outcome       = t.get("outcome", "hit")

        local_eid = _remote_mobs.get(server_target)
        print(f"  server_target={server_target} → local_eid={local_eid}")
        if local_eid is None:
            print(f"  BUG: _remote_mobs.get({server_target}) retornou None!")
            print(f"  _remote_mobs keys: {list(_remote_mobs.keys())}")
        else:
            print(f"  OK: damage={damage} outcome={outcome} hp_after={hp_after}")

# ─── Verifica se o campo 'target' do SKILL_RESULT usa server_eid ─────────────
print("\n--- Verificação de campo ---")
print(f"mob_eid no servidor: {mob_eid}")
if results:
    for t in results[0]["targets"]:
        print(f"  t['eid'] = {t.get('eid')} == mob_eid? {t.get('eid') == mob_eid}")

print("\n✓ Diagnóstico concluído")
