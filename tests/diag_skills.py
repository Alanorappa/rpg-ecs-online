"""Diagnostico: skill damage com um unico WorldServer."""
import os, sys, asyncio
os.environ['SDL_VIDEODRIVER'] = 'dummy'; os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame; pygame.init()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob
from server.session import Session, SessionManager
from engine.components import CombatStats, CombatState, TileMovement, Position

# UM unico WorldServer
ws = make_world_server()
eid = spawn_player(ws, "s1", 130, 374)
run_ticks(ws, 40)
mob = first_mob(ws)
if not mob:
    print("ERRO: sem mobs"); sys.exit(1)

ptm = ws.world.get_component(eid, TileMovement)
mob_tm = ws.world.get_component(mob, TileMovement)
mob_pos = ws.world.get_component(mob, Position)
tx, ty = ptm.current_tile_x+1, ptm.current_tile_y
mob_tm.current_tile_x = tx; mob_tm.target_tile_x = tx
mob_tm.current_tile_y = ty; mob_tm.target_tile_y = ty
mob_tm.is_moving = False
if mob_pos: mob_pos.x = tx*32+16; mob_pos.y = ty*32+16

player_cst = ws.world.get_component(eid, CombatState)
if player_cst: player_cst.target_entity_id = mob

ws.sync_player_resources("s1", rage=50, mana=100)

class FakeSession(Session):
    def __init__(self, sid, entity_id):
        self.session_id = sid; self.entity_id = entity_id
        self.username = sid; self.char_data = {'class_id':'guerreiro','id':1,'level':1}
        self.authenticated = True; self._seq = 0
        self.known_eids = {entity_id, mob}
        self.messages = []
    async def send(self, msg_type, payload):
        self.messages.append((msg_type, payload))

sess = FakeSession("s1", eid)
sm = SessionManager(ws)
sm._sessions = {"s1": sess}
sm._eid_to_sid = {eid: "s1"}

mob_cs = ws.world.get_component(mob, CombatStats)
hp_before = mob_cs.current_hp
print(f"Mob HP antes: {hp_before}")
print(f"Player tile: ({ptm.current_tile_x},{ptm.current_tile_y})")
print(f"Mob tile: ({mob_tm.current_tile_x},{mob_tm.current_tile_y})")

ws.queue_skill("s1", "golpe_poderoso", tid=mob, dir_x=0, dir_y=0, ts=0)

async def run():
    ws._tick(0.05)
    print(f"_skill_results_this_tick: {ws._skill_results_this_tick}")

    deltas = {"moved":[],"stats":[],"effects":[],"spawned":[],
              "despawned":list(ws._despawned_this_tick),
              "combat":list(ws._combat_this_tick),"player_deaths":[]}
    await sm._dispatch_tick_deltas(deltas)

    skill_msgs = [(t,p) for t,p in sess.messages
                  if (t.value if hasattr(t,'value') else str(t)) == 'skill_result']
    print(f"SKILL_RESULT enviado: {len(skill_msgs) > 0}")
    if skill_msgs:
        print(f"Targets: {skill_msgs[0][1].get('targets')}")

    mob_cs2 = ws.world.get_component(mob, CombatStats)
    hp_after = mob_cs2.current_hp if mob_cs2 else 0
    print(f"Mob HP depois: {hp_after} (dano: {hp_before - hp_after})")

asyncio.run(run())
