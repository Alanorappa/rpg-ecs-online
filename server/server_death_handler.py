"""
server/server_death_handler.py
Processa mortes de mobs no servidor (sem Pygame).

Responsabilidades neste módulo:
  - Detectar entidades com PendingDeath a cada tick
  - Calcular XP baseado no EnemyTier do mob
  - Determinar first-attacker via damage_log_fn
  - Rolar loot via roll_mob_loot e enfileirar pending_loot
  - Notificar SpawnZone (remove eid de active_entity_ids imediatamente)
  - Enfileirar despawns para o WorldServer enviar ENTITY_DESPAWN aos clientes
  - Remover a entidade do world

Intencional NÃO fazer aqui:
  - Criar entidade ECS de cadáver (WorldServer faz via dict simples)
  - Tocar sons (SOUNDS)
  - Chamar quest_fire
  - Qualquer import de Pygame
"""
from __future__ import annotations


_XP_BY_TIER: dict[str, int] = {
    "normal": 50,
    "elite":  150,
    "rare":   300,
    "boss":   1000,
}


def _serialize_item(item) -> dict:
    """Extrai apenas campos serializáveis de um Item ECS (sem objetos Pygame)."""
    return {
        "name":      getattr(item, "name",      ""),
        "icon_key":  getattr(item, "icon_key",  ""),
        "item_type": getattr(item, "item_type", ""),
        "rarity":    getattr(item, "rarity",    "common"),
        "value":     getattr(item, "value",     0),
        "slot":      getattr(item, "slot",      ""),
    }


class ServerDeathHandler:
    """Processa todas as entidades com PendingDeath a cada tick do servidor."""

    def __init__(self, world, world_server=None,
                 damage_log_fn=None) -> None:
        self.world          = world
        self.world_server   = world_server
        # damage_log_fn(mob_eid) -> dict[player_eid, total_damage]
        # Injetado pelo WorldServer; se None usa apenas killer_eid
        self.damage_log_fn  = damage_log_fn
        self.pending_xp:       list[dict] = []
        # Cada entrada: {"eid": int, "tx": int, "ty": int}
        self.pending_despawns: list[dict] = []
        self.pending_loot:     list[dict] = []

    # ── API pública ──────────────────────────────────────────────────────────

    def update(self) -> None:
        """Processa todas as entidades com PendingDeath este tick."""
        from components import PendingDeath, EnemyTier, SpawnZoneOwner, SpawnZone, TileMovement

        to_remove: list[int] = []

        for eid, pd in self.world.get_entities_with(PendingDeath):
            killer_eid = pd.killer_entity_id

            # 1. Log
            print(f"[Death] mob {eid} morto por {killer_eid}")

            # 2. XP proporcional por dano causado
            tier_comp = self.world.get_component(eid, EnemyTier)
            tier      = tier_comp.tier if tier_comp else "normal"
            base_xp   = _XP_BY_TIER.get(tier, _XP_BY_TIER["normal"])

            damage_log: dict = {}
            if self.world_server:
                damage_log = self.world_server.get_damage_log(eid)

            if damage_log:
                total_damage = sum(damage_log.values())
                for p_eid, dmg in damage_log.items():
                    proportion = dmg / total_damage
                    xp_earned  = max(1, int(base_xp * proportion))
                    self.pending_xp.append({
                        "player_eid": p_eid,
                        "xp":         xp_earned,
                        "mob_eid":    eid,
                    })
            else:
                # Fallback: killer leva tudo
                if killer_eid != -1:
                    self.pending_xp.append({
                        "player_eid": killer_eid,
                        "xp":         base_xp,
                        "mob_eid":    eid,
                    })

            # 2b. Vitória Iminente: killer ganha carga ao matar mob
            try:
                if killer_eid != -1:
                    from components import PlayerSkills as _PSdh
                    _ks = self.world.get_component(killer_eid, _PSdh)
                    if _ks:
                        for _sk in _ks.skills:
                            if _sk and _sk.skill_id == "vitoria_iminente" and _sk.max_charges > 0:
                                if _sk.charges < _sk.max_charges:
                                    _sk.charges      = _sk.max_charges
                                    _sk.charge_timer = _sk.charge_timeout
                                self.pending_xp.append({
                                    "player_eid":              killer_eid,
                                    "xp":                      0,
                                    "mob_eid":                 eid,
                                    "vitoria_iminente_charge": True,
                                })
                                break
            except Exception:
                pass

            # 3. Determina first-attacker (primeiro a bater = dono do loot)
            first_attacker_eid = killer_eid
            if damage_log:
                # damage_log é dict preservado em ordem de inserção (Python 3.7+)
                first_attacker_eid = next(iter(damage_log))

            # 4. Posição do mob para registrar corpse
            mob_tx, mob_ty = 0, 0
            tm = self.world.get_component(eid, TileMovement)
            if tm:
                mob_tx, mob_ty = tm.current_tile_x, tm.current_tile_y

            # 5. Rola loot usando EntityIdentity.name (= race display, ex: "Aranha")
            from components import EntityIdentity
            identity = self.world.get_component(eid, EntityIdentity)
            mob_name = identity.name if identity else ""

            try:
                from loot_tables import roll_mob_loot, roll_coins as _roll_coins
                loot_items = roll_mob_loot(mob_name, tier) if mob_name else []
                coins      = _roll_coins(tier) if tier else 0
            except Exception:
                loot_items = []
                coins      = 0

            # Sempre registra o corpse (visual) — só inclui itens se houve drop.
            # Sem essa entrada, o world_server nunca cria o body e o cliente
            # nunca recebe ENTITY_SPAWN(kind="corpse").
            if first_attacker_eid != -1:
                self.pending_loot.append({
                    "mob_eid":   eid,
                    "owner_eid": first_attacker_eid,
                    "items":     [_serialize_item(it) for it in loot_items],
                    "coins":     coins,
                    "tx":        mob_tx,
                    "ty":        mob_ty,
                })

            # 6. Notifica SpawnZone — remove de active_entity_ids e agenda respawn
            #    com o cooldown da zona (igual ao offline: SpawnZoneSystem adicionava
            #    zone.respawn_cooldown ao detectar deaths na cleanup sweep).
            #    Fazemos aqui para não perder o timer quando SpawnZoneSystem roda
            #    depois e vê before == alive (entidade já removida).
            szo = self.world.get_component(eid, SpawnZoneOwner)
            if szo is not None:
                zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
                if zone is not None:
                    zone.active_entity_ids.discard(eid)
                    zone.respawn_timers.append(zone.respawn_cooldown)

            # 7. Agenda despawn para o WorldServer emitir ENTITY_DESPAWN
            if not any(d["eid"] == eid for d in self.pending_despawns):
                self.pending_despawns.append({"eid": eid, "tx": mob_tx, "ty": mob_ty})

            to_remove.append(eid)

        # Remove PendingDeath ANTES de remove_entity (evita iteração inválida)
        for eid in to_remove:
            try:
                from components import PendingDeath as _PD
                self.world.remove_component(eid, _PD)
            except Exception:
                pass
            try:
                self.world.remove_entity(eid)
            except Exception:
                pass

    def consume_despawns(self) -> list[int]:
        """Retorna e limpa pending_despawns."""
        result = list(self.pending_despawns)
        self.pending_despawns.clear()
        return result

    def consume_xp(self) -> list[dict]:
        """Retorna e limpa pending_xp."""
        result = list(self.pending_xp)
        self.pending_xp.clear()
        return result

    def consume_loot(self) -> list[dict]:
        """Retorna e limpa pending_loot."""
        result = list(self.pending_loot)
        self.pending_loot.clear()
        return result
