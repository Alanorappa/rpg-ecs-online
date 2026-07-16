"""Teste: mob ranged (Goblin Hunter) aplica dano ao player via projétil."""
from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import AIControlled, CombatState, Visible, Projectile


def test_ranged_mob_damages_player():
    # (50,300): terreno aberto confirmado (scan de tile_matrix) e longe de
    # QUALQUER SpawnZone real de map_1_entities.json — usar (130,374), bem
    # no meio da zona real de Zumbi (centro 130,374, raio 12), deixava o
    # teste sujeito a um Zumbi real vagando entre o mob e o player e
    # bloqueando a linha de visão do tiro de forma intermitente (bug real
    # descoberto rodando a suíte após o Sistema de Facções, Fase 5 —
    # generalizar EnemyAISystem._select_target tornou o timing por tick
    # sensível o bastante pra expor essa flakiness pré-existente, mesmo sem
    # nenhuma relação de facção envolvida no mecanismo em si).
    ws  = make_world_server()
    pid = spawn_player(ws, "s1", 50, 300)
    peid = ws._player_eids["s1"]

    # Sempre cria um mob ranged sintético (não reaproveita mob real do mapa)
    # — reaproveitar um mob real de zona distante causava viagem de volta ao
    # spawn original (RETURNING) quando teleportado sem realinhar
    # InitialPosition.
    from engine.entity_factory import create_enemy
    from engine.components import MapLocation
    ranged_eid = create_enemy(ws.world, 53, 300, is_ranged=True, race="Goblin")
    ws._mob_eids.add(ranged_eid)
    ws.world.add_component(ranged_eid, CombatState())
    ws.world.add_component(ranged_eid, Visible())
    # EnemyAISystem tem 1 instância por mapa, filtrada por MapLocation — mob
    # sintético sem o componente é ignorado por TODA instância (nunca
    # processado, nunca ataca). SpawnZoneSystem sempre anexa isso em mobs
    # reais; um mob criado à mão via create_enemy() direto, como aqui, não.
    ws.world.add_component(ranged_eid, MapLocation(ws._map_file))

    # Força aggro direto
    ai = ws.world.get_component(ranged_eid, AIControlled)
    ai.state      = "ATTACKING"
    ai.target_eid = peid

    from engine.components import CombatStats
    # Acerto garantido — remove dependência de RNG (mob_definitions.py pode
    # dar acerto<100% por raça/tier; sem isso o teste dependia do tiro
    # ACERTAR dentro da janela de 350 ticks, instável mesmo com a lógica de
    # aggro/ataque em si 100% correta).
    _mob_cs_rng = ws.world.get_component(ranged_eid, CombatStats)
    if _mob_cs_rng:
        _mob_cs_rng.base_acerto = 100.0
        _mob_cs_rng.acerto      = 100.0
    cs_p = ws.world.get_component(peid, CombatStats)
    hp_before = cs_p.current_hp

    # ~350 ticks x 33ms ~= 11.5s -- cobre varios ciclos completos de
    # ataque ranged (cast 1.0s + cooldown ~3.1s cada) para nao depender de
    # um unico tiro acertar.
    deltas = run_ticks(ws, 350)

    hp_after = cs_p.current_hp
    dmg      = hp_before - hp_after
    print(f"\n  HP: {hp_before} -> {hp_after}  (dano={dmg})")
    print(f"  COMBAT_RESULT no delta: {len(deltas['combat'])} eventos")

    assert dmg > 0, (
        f"Player não recebeu dano do mob ranged após 2s "
        f"(hp={hp_after}/{cs_p.max_hp})"
    )


if __name__ == "__main__":
    test_ranged_mob_damages_player()
    print("PASS")
