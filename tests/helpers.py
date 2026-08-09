"""
tests/helpers.py
Utilitários compartilhados pelos testes.
"""
import os, sys, asyncio
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()


def make_world_server():
    from server.world_server import WorldServer
    return WorldServer()


def spawn_player(ws, session_id: str, tile_x: int, tile_y: int,
                 class_id: str = "guerreiro", hp: int = 200) -> int:
    char = {
        "tile_x": tile_x, "tile_y": tile_y,
        "name": session_id, "class_id": class_id,
        "hp": hp, "level": 1,
        "stats_json": '{"attack_power": 50, "max_hp": ' + str(hp) + '}',
    }
    eid = ws.spawn_player(session_id, char)
    clear_login_immunity(ws, eid)
    return eid


def clear_login_immunity(ws, eid: int) -> None:
    """Zera a imunidade/invisibilidade pós-login (3s = 90 ticks, ver
    WorldServer.spawn_player) — simula "o loading já terminou faz tempo".

    Sem isso, TODO teste que age logo após o spawn falha silenciosamente:
    o player é invisível pra broadcasts (`_can_see` → False, então nenhum
    ENTITY_MOVE/COMBAT_RESULT/DESPAWN chega ao outro player) e imune a
    dano (`blocked_immune`, então mob nunca gera delta de combate). Foi a
    causa raiz ÚNICA dos 7 testes "permanentemente vermelhos" da suíte
    (baseline 7F/85P) — a feature de imunidade é intencional e posterior
    aos testes; o que faltava era os testes simularem a janela expirada."""
    from engine.components import CombatState
    cst = ws.world.get_component(eid, CombatState)
    if cst:
        _was_invisible = not cst.is_visible
        cst.respawn_immunity_ticks = 0
        cst.is_visible = True
        cst.is_immune  = False
        # Imita a expiração real (_tick_respawn_immunity): a restauração de
        # visibilidade é anunciada via delta "visibility_changed" — sem isso,
        # outros players nunca recebem o ENTITY_SPAWN deste eid (o broadcast
        # de login foi filtrado por _can_see enquanto invisível).
        if _was_invisible:
            ws._visibility_changed_this_tick.append(eid)


def authorize_skill(ws, eid: int, sid: str) -> None:
    """Autoriza `sid` pro player no servidor: learned_skill_ids + talento
    (quando a skill é desbloqueada por talento). Necessário desde o gate
    autoritativo de world_systems.is_skill_authorized — fixtures de teste
    que castam skill devem 'aprender' primeiro, como um player real."""
    from engine.components import PlayerSkills, TalentTree
    from engine.world_systems import _TALENT_SKILL_REQS
    ps = ws.world.get_component(eid, PlayerSkills)
    if ps is not None:
        ps.learned_skill_ids.add(sid)
    req = _TALENT_SKILL_REQS.get(sid)
    if req is not None:
        tid, _tname, min_pts = req
        tt = ws.world.get_component(eid, TalentTree)
        if tt is not None:
            tt.allocated[tid] = max(tt.allocated.get(tid, 0), min_pts)


def enter_instance_progression(ws, eid: int) -> None:
    """Coloca `eid` em progressão normalizada de instância (espelha o
    formato de authorize_skill() acima) — atalho de setup pra
    tests/test_instance_progression.py."""
    from server.instance_progression import enter_normalized_progression
    enter_normalized_progression(ws, eid)


def run_ticks(ws, n: int, dt: float = 0.05) -> dict:
    """Roda N ticks e retorna todos os deltas acumulados."""
    accumulated = {
        "combat": [], "spawned": [], "despawned": [],
        "moved": [], "player_deaths": [],
    }
    orig = ws._collect_deltas
    def patched():
        d = orig()
        for k in accumulated:
            accumulated[k].extend(d.get(k, []))
        return d
    ws._collect_deltas = patched

    async def _run():
        for _ in range(n):
            ws._tick(dt)

    asyncio.run(_run())
    ws._collect_deltas = orig
    return accumulated


def set_entity_tile(ws, eid: int, tx: int, ty: int) -> None:
    """Sincroniza TileMovement E Position para o tile dado."""
    from engine.components import TileMovement, Position
    from engine.tileset import TILE_SIZE
    tm = ws.world.get_component(eid, TileMovement)
    pos = ws.world.get_component(eid, Position)
    if tm:
        tm.current_tile_x = tx;  tm.current_tile_y = ty
        tm.target_tile_x  = tx;  tm.target_tile_y  = ty
        tm.is_moving = False;    tm.progress = 0.0
    if pos:
        pos.x = tx * TILE_SIZE + TILE_SIZE // 2
        pos.y = ty * TILE_SIZE + TILE_SIZE // 2
        pos.prev_x = pos.x;     pos.prev_y = pos.y


def teleport_mob_to_player(ws, mob_eid: int, player_eid: int, offset_x: int = 1, offset_y: int = 0):
    """Move mob para o tile adjacente ao player, sincroniza Position e reseta AI.

    Também realinha InitialPosition (âncora do leash) pro tile novo — sem
    isso, o mob fica "impossivelmente longe" do próprio spawn original e
    EnemyAISystem entra em RETURNING/evasão assim que o teste roda um tick
    (ver ARQUITETURA_ONLINE.md, Decisão 20: mob em RETURNING é imune a
    dano/aggro). Testes que quiserem exercitar leash/RETURNING de propósito
    devem mover o mob SEM essa realinhagem (ou setar InitialPosition manualmente
    de volta pra longe, depois de chamar este helper).

    `offset_y` (05/08/2026, bug real): nem toda direção a partir do spawn do
    player tem linha de visão livre — ex: (131,374) em map_1 é sólido, bem
    ao lado do spawn de teste (130,374) na direção +x. Testes que dependem
    de LOS (aggro por proximidade, não só por dano) devem escolher um eixo
    livre em vez de assumir que `offset_x` sempre funciona."""
    from engine.components import TileMovement, AIControlled, InitialPosition
    from engine.tileset import TILE_SIZE as _TS_tp
    ptm = ws.world.get_component(player_eid, TileMovement)
    if ptm:
        _tx, _ty = ptm.current_tile_x + offset_x, ptm.current_tile_y + offset_y
        set_entity_tile(ws, mob_eid, _tx, _ty)
        ip = ws.world.get_component(mob_eid, InitialPosition)
        if ip:
            ip.x = _tx * _TS_tp + _TS_tp // 2
            ip.y = _ty * _TS_tp + _TS_tp // 2
    # Reseta AI state para IDLE — garante que aggro check vai funcionar
    ai = ws.world.get_component(mob_eid, AIControlled)
    if ai:
        ai.state              = "IDLE"
        ai.path               = []
        ai.aggroed_by_damage  = False
        ai.path_recalc_timer  = 0.0


def first_mob(ws) -> int | None:
    """Primeiro mob HOSTIL real (pula o TrainingDummy — ele não ataca, não
    anda e não morre; testes de combate/morte/loot com ele falham
    silenciosamente — e pula NPC de combate, ex. "Guarda Real" em
    map_1_entities.json::combat_npcs, Sistema de Facções Fase 4: sua
    facção normalmente é amigável ao player, então `deal_damage` contra
    ele é bloqueado por `apply_damage_core`'s `can_engage()` — testes que
    esperam dano/morte precisam de um mob de verdade, `Enemy`-tagged.
    Pula também Tower (29/07/2026, map_1_entities.json::towers): entra
    em `_mob_eids` pelo mesmo gate `Combatant`, mas não anda/persegue
    (sem `AIControlled`) — mesma classe de "Combatant que não é um mob
    de combate normal" de TrainingDummy/NPC acima."""
    from engine.components import TrainingDummy, NPC, Tower
    for eid in ws._mob_eids:
        if (ws.world.get_component(eid, TrainingDummy) is None
                and ws.world.get_component(eid, NPC) is None
                and ws.world.get_component(eid, Tower) is None):
            return eid
    return None


def first_ai_mob(ws) -> int | None:
    """Returns first HOSTILE mob in _mob_eids with AIControlled (pula NPC
    de combate — ver first_mob() acima)."""
    from engine.components import AIControlled, NPC
    for eid in ws._mob_eids:
        if (ws.world.get_component(eid, AIControlled)
                and ws.world.get_component(eid, NPC) is None):
            return eid
    return None


def get_mob_hp(ws, mob_eid: int) -> tuple[int, int]:
    from engine.components import CombatStats
    cs = ws.world.get_component(mob_eid, CombatStats)
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)


def get_player_hp(ws, session_id: str) -> tuple[int, int]:
    from engine.components import CombatStats
    eid = ws._player_eids.get(session_id)
    cs  = ws.world.get_component(eid, CombatStats) if eid else None
    return (cs.current_hp, cs.max_hp) if cs else (0, 0)
