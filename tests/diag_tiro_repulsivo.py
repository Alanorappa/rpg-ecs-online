"""Diagnostico: fluxo completo de Tiro Repulsivo (cast -> completion -> PROJECTILE_HIT_CS)."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, teleport_mob_to_player
from components import CombatStats, CombatState, CharacterStats, Equipment, Item

ws = make_world_server()
eid = spawn_player(ws, "s1", 130, 374, class_id="arqueiro")
run_ticks(ws, 50)
mob = first_mob(ws)
if not mob:
    print("ERRO: sem mobs"); sys.exit(1)
teleport_mob_to_player(ws, mob, eid, offset_x=3)

# Equipa arco + aljava com flechas
bow = Item("Arco Teste", "weapon", "mainhand", subtype="Bow", cast_range=8)
quiver = Item("Aljava Teste", "quiver", "offhand", arrow_count=50, max_arrows=50)
equip = ws.world.get_component(eid, Equipment)
equip.slots["mainhand"] = bow
equip.slots["offhand"]  = quiver

# Garante concentração suficiente
char = ws.world.get_component(eid, CharacterStats)
char.concentration = 200
char.max_concentration = 200

# Garante hit garantido / mob vivo
cs = ws.world.get_component(eid, CombatStats)
cs.acerto = 100.0
mob_cs = ws.world.get_component(mob, CombatStats)
mob_cs.current_hp = mob_cs.max_hp
mob_cs.dodge_rating = 0.0
mob_cs.parry_rating = 0.0

cst = ws.world.get_component(eid, CombatState)
cst.target_entity_id = mob

print(f"Antes: mob_hp={mob_cs.current_hp} arrow_count={quiver.arrow_count} concentration={char.concentration}")

ws.queue_skill("s1", "tiro_repulsivo", mob, 0.0, 0.0, 0)
deltas = run_ticks(ws, 1)
print("--- resultados do tick do CAST_SKILL ---")
print("pending_skill_requests after:", ws._pending_skill_requests)
print("pending_spell_completions:", ws._pending_spell_completions)
print("skill_results (combat dict):", deltas)

# Avança ticks suficientes para cobrir cast_time=2.5s (dt=0.05 -> 60 ticks)
run_ticks(ws, 60)
print("--- apos cast_time completar ---")
print("spells_in_flight_queue:", ws._spells_in_flight_queue)
print(f"mob_hp={mob_cs.current_hp} arrow_count={quiver.arrow_count} concentration={char.concentration}")

if not ws._spells_in_flight_queue:
    print("ERRO: nada na fila de projeteis em voo -- completion não rodou ou falhou silenciosamente")
    sys.exit(1)

# Simula o PROJECTILE_HIT_CS que o cliente enviaria ao colidir com o mob
print("--- chamando _apply_spell_on_projectile_hit ---")
try:
    ws._apply_spell_on_projectile_hit(eid, "tiro_repulsivo", mob)
except Exception as e:
    import traceback
    print(f"EXCEPTION: {e}")
    traceback.print_exc()
    sys.exit(1)

print(f"Depois: mob_hp={mob_cs.current_hp}")
mob_tm = ws.world.get_component(mob, __import__("components").TileMovement)
print(f"mob_tile=({mob_tm.current_tile_x},{mob_tm.current_tile_y})")
print("skill_results_this_tick:", ws._skill_results_this_tick)
