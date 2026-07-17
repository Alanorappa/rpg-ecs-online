"""
server/server_death_handler.py
Processa mortes de mobs no servidor (sem Pygame).

Responsabilidades neste módulo:
  - Detectar entidades com PendingDeath a cada tick
  - Calcular XP baseado no EnemyTier do mob
  - Determinar first-attacker via world_server.get_damage_log()
  - Rolar loot via roll_mob_loot e enfileirar pending_loot
  - Notificar SpawnZone (remove eid de active_entity_ids imediatamente)
  - Enfileirar despawns para o WorldServer enviar ENTITY_DESPAWN aos clientes
  - Remover a entidade do world

Intencional NÃO fazer aqui:
  - Criar entidade ECS de cadáver (WorldServer faz via dict simples)
  - Tocar sons (SOUNDS)
  - Qualquer import de Pygame
"""
from __future__ import annotations
from server.log import log


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
        "stack":     getattr(item, "stack",     1),
    }


class ServerDeathHandler:
    """Processa todas as entidades com PendingDeath a cada tick do servidor."""

    def __init__(self, world, world_server=None) -> None:
        self.world          = world
        self.world_server   = world_server
        self.pending_xp:       list[dict] = []
        # Cada entrada: {"eid": int, "tx": int, "ty": int}
        self.pending_despawns: list[dict] = []
        self.pending_loot:     list[dict] = []

    # ── API pública ──────────────────────────────────────────────────────────

    def update(self) -> None:
        """Processa todas as entidades com PendingDeath este tick."""
        from engine.components import PendingDeath, EnemyTier, SpawnZoneOwner, SpawnZone, TileMovement

        to_remove:    list[int] = []
        to_pd_only:   list[int] = []  # boneco de treino: só remove PendingDeath

        for eid, pd in self.world.get_entities_with(PendingDeath):
            # Player morreu (PvP): usa _handle_player_death, NÃO o fluxo de mob
            # (que criaria corpse com loot, daria XP de mob e despawnaria a entidade).
            if self.world_server and eid in self.world_server._player_eids.values():
                self.world_server._handle_player_death(eid)
                try:
                    self.world.remove_component(eid, PendingDeath)
                except Exception:
                    pass
                continue

            # Boneco de treino: reseta HP em vez de morrer
            from engine.components import TrainingDummy as _TDdh, CombatStats as _CSdh
            if self.world.get_component(eid, _TDdh) is not None:
                _td_cs = self.world.get_component(eid, _CSdh)
                if _td_cs:
                    _td_cs.current_hp = _td_cs.max_hp
                    if self.world_server:
                        self.world_server._combat_this_tick.append({
                            "attacker": -1, "target": eid,
                            "damage":   0,  "outcome": "regen",
                            "hp_after": _td_cs.max_hp, "source": "regen",
                        })
                if self.world_server:
                    self.world_server.get_damage_log(eid)  # descarta log acumulado
                to_pd_only.append(eid)
                continue

            killer_eid = pd.killer_entity_id

            # 1. Log
            log.info(f"[Death] mob {eid} morto por {killer_eid}")

            # Posição/mapa do mob — precisa vir ANTES do bloco de XP (usado
            # pelo split de XP compartilhado de grupo, passo 2c abaixo) e
            # também é usada mais adiante pro registro de corpse.
            mob_tx, mob_ty = 0, 0
            tm = self.world.get_component(eid, TileMovement)
            if tm:
                mob_tx, mob_ty = tm.current_tile_x, tm.current_tile_y
            mob_map = self.world_server.get_entity_map(eid) if self.world_server else None

            # 2. XP proporcional por dano causado — base por level do mob ×
            # xp_given_by_lvl (mob_definitions.py), modificado pelo
            # multiplicador de tier. Mobs sem cadastro (ex: "Elemental")
            # caem no fallback flat por tier (_XP_BY_TIER).
            tier_comp = self.world.get_component(eid, EnemyTier)
            tier      = tier_comp.tier if tier_comp else "normal"

            from engine.components import EntityIdentity
            from content.mob_definitions import MOB_TABLE
            from engine.entity_factory import ENEMY_TIER_CONFIGS
            identity = self.world.get_component(eid, EntityIdentity)
            mob_def  = MOB_TABLE.get(identity.name) if identity else None
            if mob_def and "xp_given_by_lvl" in mob_def:
                mob_level  = identity.level if identity else 1
                tier_mult  = ENEMY_TIER_CONFIGS.get(tier, ENEMY_TIER_CONFIGS["normal"])["xp"]
                base_xp    = int(mob_level * mob_def["xp_given_by_lvl"] * tier_mult)
            else:
                base_xp = _XP_BY_TIER.get(tier, _XP_BY_TIER["normal"])

            damage_log: dict = {}
            if self.world_server:
                damage_log = self.world_server.get_damage_log(eid)

            _xp_entries_start = len(self.pending_xp)
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

            # 2c. Party: XP compartilhado (decisão do usuário 17/07/2026,
            # ver ARQUITETURA_ONLINE.md §34.19). As fatias proporcionais por
            # dano acima continuam valendo entre atacantes SEM grupo em
            # comum — mas entre membros do MESMO grupo, a soma das fatias
            # que esse grupo ganharia forma um "pool", redistribuído
            # IGUALMENTE entre todos os membros do grupo dentro do raio da
            # morte (PARTY_XP_SHARE_RADIUS_TILES), incluindo quem não bateu.
            # Quem está fora do raio não ganha nada dessa morte. Atacantes
            # de outro grupo (ou sem grupo) mantêm a fatia individual.
            if self.world_server:
                from shared.constants import PARTY_XP_SHARE_RADIUS_TILES as _PXPR
                _this_death_entries = self.pending_xp[_xp_entries_start:]
                _by_party: dict = {}
                _solo_entries = []
                for _entry in _this_death_entries:
                    _pid = self.world_server.get_party_id_of(_entry["player_eid"])
                    if _pid == -1:
                        _solo_entries.append(_entry)
                    else:
                        _by_party.setdefault(_pid, []).append(_entry)

                if _by_party:
                    _new_entries = list(_solo_entries)
                    for _pid, _party_entries in _by_party.items():
                        _pool = sum(_e["xp"] for _e in _party_entries)
                        _in_range = self.world_server._party_members_in_range(
                            _pid, mob_tx, mob_ty, mob_map, _PXPR)
                        if not _in_range:
                            # Ninguém do grupo por perto (raro) — mantém as
                            # fatias originais dos que bateram.
                            _new_entries.extend(_party_entries)
                            continue
                        _share = max(1, _pool // len(_in_range))
                        for _m_eid in _in_range:
                            _new_entries.append({
                                "player_eid": _m_eid,
                                "xp":         _share,
                                "mob_eid":    eid,
                            })
                    self.pending_xp[_xp_entries_start:] = _new_entries

            # 2b. Skills com on_kill=="charge" (Vitória Iminente): killer ganha
            # carga ao matar mob. Itera o CATÁLOGO (não ps.skills) com
            # lazy-create: skill comprada no treinador DURANTE a sessão só
            # atualiza learned_skill_ids no servidor (sync_player_skills) — o
            # objeto Skill nunca entrava em ps.skills até o relog e a carga
            # nunca era concedida (bug real; mesmo padrão do PnQ em
            # combat_processor).
            try:
                if killer_eid != -1:
                    from engine.components import PlayerSkills as _PSdh
                    from content.skill_config import SKILL_CATALOG as _SC
                    _ks = self.world.get_component(killer_eid, _PSdh)
                    if _ks:
                        for _sid_ok, _defn in _SC.items():
                            if _defn.get("on_kill") != "charge":
                                continue
                            _sk = _ks.skill_by_id(_sid_ok)
                            if _sk is None:
                                from engine.world_systems import is_skill_authorized as _auth_dh
                                if not _auth_dh(self.world, killer_eid, _sid_ok)[0]:
                                    continue
                                _sk = _PSdh._make_skill(_sid_ok, _SC)
                                if _sk is None:
                                    continue
                                try:
                                    _idx_ok = _ks.skills.index(None)
                                    _ks.skills[_idx_ok] = _sk
                                except ValueError:
                                    _ks.skills.append(_sk)
                            if _sk.max_charges <= 0:
                                continue
                            if _sk.charges < _sk.max_charges:
                                _sk.charges      = _sk.max_charges
                                _sk.charge_timer = _sk.charge_timeout
                            self.pending_xp.append({
                                "player_eid":   killer_eid,
                                "xp":           0,
                                "mob_eid":      eid,
                                "on_kill_skill": _sk.skill_id,
                            })
                            break
            except Exception:
                pass

            # 3. Determina first-attacker (primeiro a bater = dono do loot)
            first_attacker_eid = killer_eid
            if damage_log:
                # damage_log é dict preservado em ordem de inserção (Python 3.7+)
                first_attacker_eid = next(iter(damage_log))

            # 3b. Evento de quest "kill" — first-attacker é o dono do
            # progresso (mesmo critério de dono do loot). Server-autoritativo
            # — ver quest_logic.py/PROBLEMAS_ARQUITETURA.md.
            if first_attacker_eid != -1 and identity:
                from engine.quest_events import fire as _qfire_kill
                _qfire_kill("kill", player_eid=first_attacker_eid,
                            name=identity.name, race=identity.race, tier=tier)

            # 4. Posição do mob para registrar corpse — já lida antes do
            # bloco de XP (mob_tx/mob_ty), reaproveitada aqui.

            # 5. Rola loot usando EntityIdentity.name (= race display, ex: "Aranha")
            # (identity já buscado no passo 2, pro cálculo de XP por level)
            mob_name = identity.name if identity else ""

            try:
                from content.loot_tables import roll_mob_loot, roll_mob_coins as _roll_mob_coins
                loot_items = roll_mob_loot(mob_name, tier) if mob_name else []
                coins      = _roll_mob_coins(mob_name, tier) if (mob_name and tier) else 0
            except Exception:
                loot_items = []
                coins      = 0

            # 5a. Drop condicional de quest (collect_item, ex: Pelo de Urso) —
            # mesma lógica de quest_system.py::get_conditional_loot, agora
            # server-autoritativa contra o QuestLog real do first-attacker.
            if first_attacker_eid != -1 and mob_name:
                from engine.components import QuestLog as _QLdh
                _ql_killer = self.world.get_component(first_attacker_eid, _QLdh)
                if _ql_killer:
                    import engine.quest_logic as _qlogic_dh
                    loot_items.extend(
                        _qlogic_dh.roll_conditional_loot(_ql_killer, mob_name, identity.race if identity else ""))

            # 5b. Reciclagem: flechas que acertaram este mob (contadas em
            # _server_apply_ranged_physical) voltam como loot pro matador, se ele
            # tiver o talento. Mesma fórmula do offline (systems.py): 50-100% das
            # flechas recebidas, mínimo 1.
            from engine.components import CombatStats as _CSdh, Equipment as _EqDh, Item as _ItemDh
            _dead_cs = self.world.get_component(eid, _CSdh)
            if _dead_cs and _dead_cs.arrows_received > 0 and first_attacker_eid != -1:
                _killer_cs = self.world.get_component(first_attacker_eid, _CSdh)
                if _killer_cs and getattr(_killer_cs, "arrow_recovery_enabled", False):
                    import random as _rand_dh
                    _pct       = _rand_dh.randint(50, 100) / 100.0
                    _recovered = max(1, int(_dead_cs.arrows_received * _pct))
                    _equip_r   = self.world.get_component(first_attacker_eid, _EqDh)
                    _quiver_r  = _equip_r.slots.get("offhand") if _equip_r else None
                    _atype     = getattr(_quiver_r, "subtype", "") or "Flecha"
                    _ret = _ItemDh(
                        name=_atype, item_type="ammo", slot="",
                        rarity="common", value=1,
                        damage_min=getattr(_quiver_r, "damage_min", 0),
                        damage_max=getattr(_quiver_r, "damage_max", 0),
                        max_stack=1000,
                    )
                    _ret.stack = _recovered
                    loot_items.append(_ret)

            # Sempre registra o corpse (visual) — só inclui itens se houve drop.
            # Sem essa entrada, o world_server nunca cria o body e o cliente
            # nunca recebe ENTITY_SPAWN(kind="corpse").
            if first_attacker_eid != -1:
                # "map": capturado ANTES do remove_entity — corpse/loot são
                # broadcast por loops diretos (não AOI_UPDATE) e precisam do
                # mapa pro filtro cross-map (ver session._sessions_in_aoi).
                from engine.components import MapLocation as _MLdh
                _ml_dh = self.world.get_component(eid, _MLdh)
                self.pending_loot.append({
                    "mob_eid":   eid,
                    "owner_eid": first_attacker_eid,
                    "items":     [_serialize_item(it) for it in loot_items],
                    "coins":     coins,
                    "tx":        mob_tx,
                    "ty":        mob_ty,
                    "map":       _ml_dh.map_file if _ml_dh else None,
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

        # Boneco de treino: só remove PendingDeath, mantém entidade
        for eid in to_pd_only:
            try:
                from engine.components import PendingDeath as _PD
                self.world.remove_component(eid, _PD)
            except Exception:
                pass

        # Remove PendingDeath ANTES de remove_entity (evita iteração inválida)
        for eid in to_remove:
            try:
                from engine.components import PendingDeath as _PD
                self.world.remove_component(eid, _PD)
            except Exception:
                pass
            try:
                self.world.remove_entity(eid)
            except Exception:
                pass

    def consume_despawns(self) -> list[dict]:
        """Retorna e limpa pending_despawns. Cada entry: {"eid": int, "tx": int, "ty": int}"""
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
