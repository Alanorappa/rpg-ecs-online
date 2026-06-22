"""Teste: mob ranged (Goblin Hunter) aplica dano ao player via projétil."""
from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile
from components import AIControlled, CombatState, Visible, Projectile


def test_ranged_mob_damages_player():
    ws  = make_world_server()
    pid = spawn_player(ws, "s1", 130, 374)
    peid = ws._player_eids["s1"]

    # Pega o primeiro mob ranged spawnado pelo mapa
    from components import Enemy
    ranged_eid = None
    for eid in list(ws._mob_eids):
        ai = ws.world.get_component(eid, AIControlled)
        if ai and ai.is_ranged:
            ranged_eid = eid
            break

    if ranged_eid is None:
        # Cria um mob ranged manualmente se o mapa não tiver nenhum perto
        from entity_factory import create_enemy
        ranged_eid = create_enemy(ws.world, 132, 374, is_ranged=True, race="Goblin")
        ws._mob_eids.add(ranged_eid)
        ws.world.add_component(ranged_eid, CombatState())
        ws.world.add_component(ranged_eid, Visible())

    # Coloca mob a 3 tiles do player (dentro do ataque ranged, fora do kiting min)
    set_entity_tile(ws, ranged_eid, 133, 374)

    # Força aggro direto
    ai = ws.world.get_component(ranged_eid, AIControlled)
    ai.state      = "ATTACKING"
    ai.target_eid = peid

    from components import CombatStats
    cs_p = ws.world.get_component(peid, CombatStats)
    hp_before = cs_p.current_hp

    # 40 ticks × 33ms ≈ 1.3s — suficiente para cast (0.5s) + viagem do projétil
    deltas = run_ticks(ws, 40)

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
