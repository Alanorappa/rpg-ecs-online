"""
server/respawn_system.py
Mixin para WorldServer: death handling, respawn, imunidade pós-respawn.
"""
from __future__ import annotations

from shared.constants import TILE_SIZE


class RespawnMixin:

    # TODO: hardcoded para map_1. Com múltiplos mapas/zonas, mover para
    #       map_1_entities.json ou propriedade de SpawnZone do player (B3).
    RESPAWN_TILE = (115, 389)   # deve coincidir com spawn do mapa offline

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
        """Player morreu: reseta HP, teleporta para respawn, mobs param de atacar."""
        from components import CombatStats, CombatState, TileMovement, Position, CharacterStats
        player_cs = self.world.get_component(player_eid, CombatStats)
        if player_cs:
            player_cs.current_hp = player_cs.max_hp
        # Reseta campos voláteis de combate (cargas, procs, contadores) — P3
        player_char = self.world.get_component(player_eid, CharacterStats)
        if player_char:
            player_char.mana = player_char.max_mana   # restaura mana cheia no respawn
            player_char.reset_volatile()

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
        # "fantasma" que acertam mobs após o respawn do player
        self._pending_spell_completions = [
            e for e in self._pending_spell_completions
            if e.get("player_eid") != player_eid
        ]
        self._spells_in_flight_queue = [
            e for e in self._spells_in_flight_queue
            if e.get("player_eid") != player_eid
        ]

        # Remove contribuição de dano do player morto nos logs de mob
        # (XP de kills após a morte não deve ser atribuído a este player)
        for _log in self._mob_damage_log.values():
            _log.pop(player_eid, None)

        # Teleporta o player para o spawn no servidor ANTES de limpar aggro.
        # Isso garante que ServerMobSystem._try_aggro não re-agre imediatamente
        # porque o player está fora do AGGRO_RANGE após o teleporte.
        rx, ry = self.RESPAWN_TILE
        ptm = self.world.get_component(player_eid, TileMovement)
        pos = self.world.get_component(player_eid, Position)
        if ptm:
            ptm.current_tile_x = rx;  ptm.current_tile_y = ry
            ptm.target_tile_x  = rx;  ptm.target_tile_y  = ry
        if pos:
            pos.x = rx * TILE_SIZE + TILE_SIZE // 2
            pos.y = ry * TILE_SIZE + TILE_SIZE // 2

        # Notifica AOI do respawn (teleporte): outros clientes atualizam a posição
        self._moved_this_tick.append({
            "eid":     player_eid,
            "tx":      rx, "ty":      ry,
            "from_tx": rx, "from_ty": ry,
        })

        # Imunidade pós-respawn: 4s a 20 tps — _select_target ignora o player
        player_cst = self.world.get_component(player_eid, CombatState)
        if player_cst:
            player_cst.respawn_immunity_ticks = 80  # 4 segundos
            player_cst.is_visible = False           # _select_target filtra invisíveis

        # Limpa alvo de todos os mobs (CombatState + AIControlled)
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

        # Encontra session_id para notificar o cliente
        session_id = None
        for sid, eid in self._player_eids.items():
            if eid == player_eid:
                session_id = sid
                break

        self._player_deaths_this_tick.append({
            "session_id": session_id,
            "player_eid": player_eid,
            "respawn_tx": rx,
            "respawn_ty": ry,
        })
        # Broadcast HP restaurado — outros players atualizam a barra de HP do respawnado
        if player_cs:
            self._player_hp_broadcasts_this_tick.append({
                "eid": player_eid, "hp": player_cs.current_hp, "hp_max": player_cs.max_hp,
            })
