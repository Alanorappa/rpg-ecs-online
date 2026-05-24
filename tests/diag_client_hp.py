"""Diagnóstico: qual max_hp o cliente envia ao servidor."""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Simula criação do player entity como o GameEngine faz online (char_data=None)
from world import World
from entity_factory import create_player
from components import CombatStats, CharacterStats

world = World()
player_eid = create_player(world, 115 * 32 + 16, 389 * 32 + 16, "maps/map_1.csv")

cs   = world.get_component(player_eid, CombatStats)
char = world.get_component(player_eid, CharacterStats)

print(f"[DIAG] Sem char_data (online mode):")
print(f"  cs.max_hp         = {cs.max_hp if cs else 'N/A'}")
print(f"  cs.current_hp     = {cs.current_hp if cs else 'N/A'}")
print(f"  cs.base_stamina   = {getattr(cs, 'base_stamina', 'N/A')}")
print(f"  char.class_id     = {char.class_id if char else 'N/A'}")
print(f"  char.vitality     = {char.vitality if char else 'N/A'}")
print()

# Simula o que apply_char_stats_to_combat faria
if char and cs:
    from stats_system import apply_char_stats_to_combat, CLASS_BASE_STATS
    _base = CLASS_BASE_STATS.get(char.class_id, CLASS_BASE_STATS["guerreiro"])
    char.strength     = _base["strength"]
    char.intelligence = _base["intelligence"]
    char.agility      = _base["agility"]
    char.vitality     = _base["vitality"]
    char.defense      = _base["defense"]
    apply_char_stats_to_combat(char, cs, None)
    cs.current_hp = cs.max_hp
    print(f"[DIAG] Com apply_char_stats_to_combat (correto):")
    print(f"  cs.max_hp     = {cs.max_hp}")
    print(f"  cs.current_hp = {cs.current_hp}")
