"""Diagnóstico: combat event de player B chega na sessão de player A?"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob
from server.session import Session, SessionManager
from engine.components import CombatStats, AIControlled, TileMovement, CombatState

ws = make_world_server()

# Sessões simuladas
class FakeSession(Session):
    def __init__(self, ws, sid):
        self.ws = ws
        self.session_id = sid
        self.entity_id = -1
        self.username = sid
        self.char_data = {'class_id':'guerreiro','level':1,'id':1}
        self.authenticated = True
        self._seq = 0
        self.known_eids = set()
        self.messages = []
    async def send(self, msg_type, payload):
        self.messages.append((msg_type, payload))

# Spawna dois players
eid_a = spawn_player(ws, "s_a", 115, 389)
eid_b = spawn_player(ws, "s_b", 117, 389)

sess_a = FakeSession(ws, "s_a")
sess_b = FakeSession(ws, "s_b")
sess_a.entity_id = eid_a
sess_b.entity_id = eid_b
sess_a.known_eids.add(eid_a)
sess_a.known_eids.add(eid_b)  # A conhece B
sess_b.known_eids.add(eid_b)
sess_b.known_eids.add(eid_a)  # B conhece A

# Instancia SessionManager com sessões fake
sm = SessionManager(ws)
sm._sessions = {"s_a": sess_a, "s_b": sess_b}
sm._eid_to_sid = {eid_a: "s_a", eid_b: "s_b"}

# Cria mobs e configura mob para atacar player B
run_ticks(ws, 40)
mob = first_mob(ws)
print(f"Mob: {mob}")

if mob:
    mob_cs  = ws.world.get_component(mob, CombatStats)
    mob_ai  = ws.world.get_component(mob, AIControlled)
    mob_tm  = ws.world.get_component(mob, TileMovement)
    mob_cst = ws.world.get_component(mob, CombatState)
    ptm_b   = ws.world.get_component(eid_b, TileMovement)
    pcs_b   = ws.world.get_component(eid_b, CombatStats)

    mob_tm.current_tile_x = ptm_b.current_tile_x + 1
    mob_tm.current_tile_y = ptm_b.current_tile_y
    if mob_ai:
        mob_ai.target_eid = eid_b
        mob_ai.aggroed_by_damage = True
        mob_ai.state = "ATTACKING"
    if mob_cs:
        mob_cs.attack_cooldown_timer = 0.0
    if mob_cst:
        mob_cst.target_entity_id = eid_b

    hp_b_before = pcs_b.current_hp
    print(f"HP de B antes: {hp_b_before}")

    deltas = run_ticks(ws, 5)
    hp_b_after = pcs_b.current_hp
    print(f"HP de B depois: {hp_b_after}")

    # Verificar combat deltas
    combat = deltas.get("combat", [])
    player_b_hits = [cr for cr in combat if cr.get("target") == eid_b]
    print(f"Combat events com target=eid_b: {len(player_b_hits)}")
    if player_b_hits:
        print(f"  Primeiro: {player_b_hits[0]}")

    # Simular build_update_for_session para sessão A
    import asyncio
    from shared.constants import AOI_RADIUS
    tx_a, ty_a = ws.get_tile_pos("s_a")
    update = sm._build_update_for_session(sess_a, deltas, tx_a, ty_a)
    combat_in_update = update.get("combat", [])
    player_b_in_update = [cr for cr in combat_in_update if cr.get("target") == eid_b]
    print(f"Combat events para sessão A (player vê dano em B): {len(player_b_in_update)}")
    if player_b_in_update:
        print(f"  hp_after: {player_b_in_update[0].get('hp_after')}")
    else:
        print(f"  PROBLEMA: eid_b ({eid_b}) in sess_a.known_eids: {eid_b in sess_a.known_eids}")
        print(f"  All combat targets: {[cr.get('target') for cr in combat]}")
        print(f"  sess_a.known_eids: {sess_a.known_eids}")
