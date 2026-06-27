"""Diagnostico: fluxo completo de Tiro Multiplo (cast -> completion -> N flechas
individuais via PROJECTILE_HIT_CS, uma por alvo no cone)."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from entity_factory import create_enemy
from components import CombatStats, CombatState, CharacterStats, Equipment, Item

ws = make_world_server()
eid = spawn_player(ws, "s1", 10, 10, class_id="arqueiro")

# Equipa arco + aljava com flechas
bow = Item("Arco Teste", "weapon", "mainhand", subtype="Bow", cast_range=12)
quiver = Item("Aljava Teste", "quiver", "offhand", arrow_count=50, max_arrows=50)
equip = ws.world.get_component(eid, Equipment)
equip.slots["mainhand"] = bow
equip.slots["offhand"]  = quiver

char = ws.world.get_component(eid, CharacterStats)
char.concentration = 200
char.max_concentration = 200

cs = ws.world.get_component(eid, CombatStats)
cs.acerto = 100.0
cs.tiro_multiplo_targets = 3  # talento 2pt = 3 alvos

# 3 mobs no cone (leste do player), 1 fora do cone (norte) -- nao deve ser
# selecionado mesmo com talento de 3 alvos
mob1 = create_enemy(ws.world, 11, 10, race="Goblin")
mob2 = create_enemy(ws.world, 12, 10, race="Goblin")
mob3 = create_enemy(ws.world, 13, 10, race="Goblin")
mob_fora_cone = create_enemy(ws.world, 10, 5, race="Goblin")
for m in (mob1, mob2, mob3, mob_fora_cone):
    ws._mob_eids.add(m)
    set_entity_tile(ws, m, *{
        mob1: (11, 10), mob2: (12, 10), mob3: (13, 10), mob_fora_cone: (10, 5),
    }[m])

hp_before = {m: ws.world.get_component(m, CombatStats).current_hp
             for m in (mob1, mob2, mob3, mob_fora_cone)}
print(f"HP antes: {hp_before}  arrow_count={quiver.arrow_count}")

# Direcao do cast: leste (dir_x=1, dir_y=0) -- mira nos 3 mobs alinhados, dispara
# pela fila de skill (mesmo entrypoint que server/skill_processor.py usa).
ws.queue_skill("s1", "tiro_multiplo", -1, 1.0, 0.0, 0)
ws.run_ticks if False else None  # noop, mantém import limpo

from tests.helpers import run_ticks
run_ticks(ws, 1)
print("pending_spell_completions apos CAST_SKILL:", len(ws._pending_spell_completions))

# cast_time=1.5s, dt=0.05 -> 30+ ticks para completar
run_ticks(ws, 35)
print("--- apos cast_time completar ---")
print("spells_in_flight_queue (1 por alvo esperado):", ws._spells_in_flight_queue)
print(f"arrow_count apos completion (nao deve ter mudado ainda): {quiver.arrow_count}")

queued_targets = [e["target_id"] for e in ws._spells_in_flight_queue if e["spell_id"] == "tiro_multiplo"]
print(f"alvos enfileirados: {queued_targets}")

if mob_fora_cone in queued_targets:
    print("ERRO: mob fora do cone foi selecionado!")
    sys.exit(1)
if len(queued_targets) != 3:
    print(f"ERRO: esperava 3 alvos (talento=3pt), veio {len(queued_targets)}")
    sys.exit(1)
if len(set(queued_targets)) != len(queued_targets):
    print("ERRO: algum alvo duplicado na fila!")
    sys.exit(1)

# Simula o PROJECTILE_HIT_CS de cada flecha colidindo, uma por vez
print("--- confirmando cada flecha individualmente ---")
for t in queued_targets:
    before = ws.world.get_component(t, CombatStats).current_hp
    ws._apply_spell_on_projectile_hit(eid, "tiro_multiplo", t)
    after = ws.world.get_component(t, CombatStats).current_hp
    print(f"  alvo={t} hp {before} -> {after}  arrow_count={quiver.arrow_count}")
    if after >= before:
        print(f"ERRO: alvo {t} nao recebeu dano")
        sys.exit(1)

print(f"--- resultado final ---")
print(f"arrow_count final: {quiver.arrow_count} (esperado: 50 - 3 = 47)")
print(f"mob_fora_cone hp inalterado: {ws.world.get_component(mob_fora_cone, CombatStats).current_hp}")
if quiver.arrow_count != 47:
    print("ERRO: contagem de flechas incorreta")
    sys.exit(1)
if ws._spells_in_flight_queue:
    print("ERRO: fila deveria estar vazia apos todas as confirmacoes")
    sys.exit(1)

print("OK: Tiro Multiplo dispara 1 flecha por alvo no cone, respeitando talento e municao.")
