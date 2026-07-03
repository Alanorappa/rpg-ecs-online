"""
server/respawn_system.py
Mixin para WorldServer: fluxo de morte → espírito (ghost) → cemitério → revive.

Fluxo:
  1. Player morre → _handle_player_death: corpo fica no local da morte
     (CombatState.is_visible=True, current_hp==0 já bloqueia o corpo como
     alvo/atacante em todo combat_processor/spell_completion_processor).
  2. Cliente mostra "Você morreu" após 2s (timer local) e envia RELEASE_SPIRIT.
  3. _handle_release_spirit: espírito teleporta pro cemitério, intangível
     (move_player bypassa CC/walkable), invisível (is_visible=False — mobs e
     outros players não o veem, reaproveitando a regra de Camuflagem).
  4. _tick_ghost_states: ghost dentro do raio do cemitério por
     GHOST_GRAVEYARD_REVIVE_S segundos contínuos → revive full HP no cemitério.
     Ghost dentro do raio do corpo → near_corpse=True (cliente mostra prompt
     "Reviver agora?"); REVIVE_REQUEST → revive com GHOST_CORPSE_REVIVE_HP_FRAC
     de HP no local do corpo.
  5. _revive_player: restaura HP/mana, reseta GhostState, aplica imunidade
     pós-respawn (reaproveita respawn_immunity_ticks existente).
"""
from __future__ import annotations

from shared.constants import (
    TILE_SIZE,
    RESPAWN_TILE,
    GHOST_GRAVEYARD_RADIUS_TILES,
    GHOST_CORPSE_RADIUS_TILES,
    GHOST_GRAVEYARD_REVIVE_S,
    GHOST_CORPSE_REVIVE_HP_FRAC,
)

# Namespace sintético para o eid do marcador de corpo (fora da faixa de
# eids reais do world.create_entity()).
PLAYER_CORPSE_EID_BASE = 3_000_000


class RespawnMixin:

    # TODO: hardcoded para map_1. Com múltiplos mapas/zonas, mover para
    #       map_1_entities.json ou propriedade de SpawnZone do player (B3).
    RESPAWN_TILE = RESPAWN_TILE   # shared/constants.py — fonte única (auth.py também usa)

    def _tick_respawn_immunity(self) -> None:
        """Decrementa imunidade pós-respawn; restaura visibilidade ao expirar."""
        from components import CombatState as _CS
        for peid in self._player_eids.values():
            _pcst = self.world.get_component(peid, _CS)
            if _pcst and _pcst.respawn_immunity_ticks > 0:
                _pcst.respawn_immunity_ticks -= 1
                if _pcst.respawn_immunity_ticks == 0:
                    _pcst.is_visible = True

    def _handle_player_death(self, player_eid: int) -> None:
        """Player morreu: corpo fica no local da morte, espírito ainda não liberado."""
        from components import CombatState, TileMovement, GhostState, CharacterStats

        # Limpa efeitos ativos (DoT/HoT) do player — B8
        from components import StatusEffects as _SFX, ActiveRegen as _AR
        sfx = self.world.get_component(player_eid, _SFX)
        if sfx:
            sfx.effects.clear()
        try:
            self.world.remove_component(player_eid, _AR)
        except Exception:
            pass

        # Cancela Channeling ativo (evita dano AoE pós-morte)
        from components import Channeling as _Chan
        try:
            self.world.remove_component(player_eid, _Chan)
        except Exception:
            pass

        # Cancela spells em voo / com cast_time pendente — evita flechas
        # "fantasma" que acertam mobs após o respawn do player. Filtra tanto
        # pelo caster (player_eid) quanto pelo alvo (target_id), pois flechas
        # de Flecha Reiterada já em voo continuam acertando o player morto
        # mesmo após ele respawnar com HP restaurado.
        self._pending_spell_completions = [
            e for e in self._pending_spell_completions
            if e.get("player_eid") != player_eid and e.get("target_id") != player_eid
        ]
        self._spells_in_flight_queue = [
            e for e in self._spells_in_flight_queue
            if e.get("player_eid") != player_eid and e.get("target_id") != player_eid
        ]
        self._pending_knockback_landings = [
            e for e in self._pending_knockback_landings
            if e.get("target_id") != player_eid and e.get("collided_eid") != player_eid
        ]

        # Remove contribuição de dano do player morto nos logs de mob
        # (XP de kills após a morte não deve ser atribuído a este player)
        for _log in self._mob_damage_log.values():
            _log.pop(player_eid, None)

        # Limpa alvo de todos os mobs (CombatState + AIControlled) — corpo não
        # deve continuar sendo perseguido/atacado
        from components import AIControlled as _AIC
        for mob_eid in self._mob_eids:
            mob_state = self.world.get_component(mob_eid, CombatState)
            if mob_state and mob_state.target_entity_id == player_eid:
                mob_state.target_entity_id = -1
            mob_ai = self.world.get_component(mob_eid, _AIC)
            if mob_ai and mob_ai.target_eid == player_eid:
                mob_ai.target_eid        = -1
                mob_ai.aggroed_by_damage = False
                mob_ai.state             = "RETURNING"
                mob_ai.path              = None

        # Corpo fica no local da morte, visível pra todos
        tm  = self.world.get_component(player_eid, TileMovement)
        cst = self.world.get_component(player_eid, CombatState)
        gst = self.world.get_component(player_eid, GhostState)
        ctx, cty = (tm.current_tile_x, tm.current_tile_y) if tm else self.RESPAWN_TILE
        if gst:
            gst.is_dead   = True
            gst.is_ghost  = False
            gst.corpse_tx = ctx
            gst.corpse_ty = cty
            gst.graveyard_timer = 0.0
            gst.near_corpse = False
        if cst:
            cst.is_visible = True
            cst.target_entity_id = -1

        char = self.world.get_component(player_eid, CharacterStats)
        self._player_corpses[player_eid] = {
            "tx":       ctx, "ty": cty,
            "name":     char.name     if char else "",
            "class_id": char.class_id if char else "",
        }

        session_id = self._player_eid_to_sid.get(player_eid)
        self._player_deaths_this_tick.append({
            "session_id": session_id,
            "player_eid": player_eid,
            "corpse_tx":  ctx,
            "corpse_ty":  cty,
        })
        self._entity_deaths_this_tick.append({
            "eid": player_eid, "tx": ctx, "ty": cty,
        })

    def _handle_release_spirit(self, player_eid: int) -> None:
        """Player clicou 'Liberar espírito': teleporta o espírito pro cemitério,
        intangível e invisível. Corpo permanece no local da morte (marcador)."""
        from components import CombatState, TileMovement, Position, GhostState, CharacterStats

        gst = self.world.get_component(player_eid, GhostState)
        if not gst or not gst.is_dead or gst.is_ghost:
            return

        gst.is_ghost = True
        gst.graveyard_timer = 0.0
        gst.near_corpse = False

        rx, ry = self.RESPAWN_TILE
        tm = self.world.get_component(player_eid, TileMovement)
        old_tx, old_ty = (tm.current_tile_x, tm.current_tile_y) if tm else (rx, ry)
        # snap_to_tile: cancela tween em andamento + sincroniza pixels/Position.
        # O write manual antigo não resetava is_moving — player que morria no
        # meio de um passo respawnava com o tween antigo vivo (classe de bug
        # do Tiro Repulsivo).
        from utils import snap_to_tile as _snap_rs
        _snap_rs(self.world, player_eid, rx, ry, carry_prev=False)

        # Se player morreu num mapa não-principal (ex: cave), transfere o ghost pro
        # mapa principal antes de tudo. O cliente recebe ZONE_CHANGE junto com
        # GHOST_STATE via flag "zone_change_map" consumida em _send_ghost_state_updates.
        session_id   = self._player_eid_to_sid.get(player_eid)
        from components import MapLocation as _MLrs
        _ml_rs      = self.world.get_component(player_eid, _MLrs)
        current_map  = _ml_rs.map_file if _ml_rs else self._map_file
        _zone_change = None
        if current_map != self._map_file and session_id:
            self.transfer_player(session_id, player_eid, self._map_file, rx, ry)
            _zone_change = self._map_file

        # Não usa _moved_this_tick aqui: isso faria o player remoto enxergar
        # o "espírito" se mover/aparecer no cemitério. Em vez disso, despawna
        # a entidade pra quem já a conhecia (igual a um player saindo do AOI)
        # — o dono recebe a nova posição via GHOST_STATE (tx/ty acima).
        self._despawned_this_tick.append({
            "eid": player_eid, "tx": old_tx, "ty": old_ty,
        })

        cst = self.world.get_component(player_eid, CombatState)
        if cst:
            cst.is_visible = False  # ghost invisível p/ mobs e outros players

        # Avisa o cliente imediatamente que o espírito foi liberado (fecha a
        # janela "Você morreu" e mostra o HUD do espírito). _tick_ghost_states
        # só envia GHOST_STATE quando near_corpse MUDA — aqui o estado inicial
        # (near_corpse=False) não conta como mudança, então sem este envio
        # explícito o cliente nunca saberia que is_ghost virou True.
        _ghost_upd = {
            "session_id":      session_id,
            "is_ghost":        True,
            "near_corpse":     False,
            "graveyard_timer": 0.0,
            "tx":              rx,
            "ty":              ry,
        }
        if _zone_change:
            _ghost_upd["zone_change_map"] = _zone_change
        self._ghost_state_updates_this_tick.append(_ghost_upd)

        # Marcador de corpo pra quem está no AOI (a entidade do player teleportou
        # pro cemitério, mas o corpo deve continuar visível no local da morte)
        char = self.world.get_component(player_eid, CharacterStats)
        self._spawned_this_tick.append({
            "eid":      PLAYER_CORPSE_EID_BASE + player_eid,
            "kind":     "player_corpse",
            "tx":       gst.corpse_tx,
            "ty":       gst.corpse_ty,
            "name":     char.name if char else "",
            "class_id": char.class_id if char else "",
            "hp":       0,
            "hp_max":   0,
            "level":    1,
            "effects":  [],
        })

    def _tick_ghost_states(self, dt: float) -> None:
        """Atualiza timers/raios de cada espírito ativo; dispara revive automático
        no cemitério ou atualiza near_corpse (prompt 'Reviver agora?')."""
        from components import TileMovement, GhostState

        gx, gy = self.RESPAWN_TILE
        for peid in list(self._player_eids.values()):
            gst = self.world.get_component(peid, GhostState)
            if not gst or not gst.is_ghost:
                continue
            tm = self.world.get_component(peid, TileMovement)
            if not tm:
                continue

            in_graveyard = (abs(tm.current_tile_x - gx) <= GHOST_GRAVEYARD_RADIUS_TILES
                            and abs(tm.current_tile_y - gy) <= GHOST_GRAVEYARD_RADIUS_TILES)
            if in_graveyard:
                gst.graveyard_timer += dt
                if gst.graveyard_timer >= GHOST_GRAVEYARD_REVIVE_S:
                    self._revive_player(peid, hp_frac=1.0, at_corpse=False)
                    continue
            else:
                gst.graveyard_timer = 0.0

            near_corpse = (abs(tm.current_tile_x - gst.corpse_tx) <= GHOST_CORPSE_RADIUS_TILES
                           and abs(tm.current_tile_y - gst.corpse_ty) <= GHOST_CORPSE_RADIUS_TILES)
            if near_corpse != gst.near_corpse:
                gst.near_corpse = near_corpse
                self._ghost_state_updates_this_tick.append({
                    "session_id":      self._player_eid_to_sid.get(peid),
                    "is_ghost":        True,
                    "near_corpse":     near_corpse,
                    "graveyard_timer": gst.graveyard_timer,
                })

    def _revive_player(self, player_eid: int, hp_frac: float, at_corpse: bool) -> None:
        """Revive o player: restaura HP/mana, reseta GhostState, aplica imunidade
        pós-respawn, na posição atual do espírito. at_corpse=True → 15% HP
        (perto do corpo); at_corpse=False → full HP (cemitério)."""
        from components import (CombatStats, CombatState, CharacterStats,
                                 TileMovement, GhostState)

        gst = self.world.get_component(player_eid, GhostState)
        if not gst:
            return

        cs = self.world.get_component(player_eid, CombatStats)
        if cs:
            cs.current_hp = max(1, int(cs.max_hp * hp_frac))

        char = self.world.get_component(player_eid, CharacterStats)
        if char:
            char.mana = char.max_mana
            char.reset_volatile()

        # Revive sempre na posição atual do espírito (cemitério ou perto do
        # corpo) — não teleporta pro corpo, o jogador já está perto dele.
        tm  = self.world.get_component(player_eid, TileMovement)
        tx, ty = (tm.current_tile_x, tm.current_tile_y) if tm else self.RESPAWN_TILE

        gst.is_dead   = False
        gst.is_ghost  = False
        gst.corpse_tx = -1
        gst.corpse_ty = -1
        gst.graveyard_timer = 0.0
        gst.near_corpse = False

        cst = self.world.get_component(player_eid, CombatState)
        if cst:
            cst.is_visible = False           # imunidade pós-respawn (igual C28/C29)
            cst.respawn_immunity_ticks = 80  # 4 segundos

        session_id = self._player_eid_to_sid.get(player_eid)
        self._player_revives_this_tick.append({
            "session_id": session_id,
            "player_eid": player_eid,
            "tx": tx, "ty": ty,
            "hp": cs.current_hp if cs else 0,
            "hp_max": cs.max_hp if cs else 0,
            "mana": char.mana if char else 0,
            "max_mana": char.max_mana if char else 0,
        })
        if cs:
            self._player_hp_broadcasts_this_tick.append({
                "eid": player_eid, "hp": cs.current_hp, "hp_max": cs.max_hp,
                "bcast_tx": tx, "bcast_ty": ty,
            })

        # Remove marcador de corpo (broadcast de despawn pra AOI)
        if player_eid in self._player_corpses:
            del self._player_corpses[player_eid]
            self._despawned_this_tick.append({
                "eid": PLAYER_CORPSE_EID_BASE + player_eid, "tx": None, "ty": None,
            })

        # Re-anuncia o player pra quem está no AOI — a entidade foi despawnada
        # para outros players ao liberar o espírito (ghost invisível), então
        # precisa reaparecer (cor normal) explicitamente ao reviver.
        self._spawned_this_tick.append({
            "eid":      player_eid,
            "kind":     "player",
            "tx":       tx, "ty": ty,
            "name":     char.name if char else "",
            "class_id": char.class_id if char else "",
            "hp":       cs.current_hp if cs else 0,
            "hp_max":   cs.max_hp if cs else 0,
            "level":    char.level if char else 1,
            "effects":  [],
        })
