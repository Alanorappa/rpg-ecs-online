"""
server/dungeon_processor.py
Mixin para WorldServer: dungeons PvE instanciadas (07/10/2026, pedido do
usuário — ver CLAUDE.md).

Entrada é IMEDIATA ao pisar no tile de transição marcado com "dungeon_key"
(server/session.py::_handle_zone_change_req) — sem fila/aceite/pareamento de
oponente, diferente de Arena/BG (server/match_processor.py,
server/bg_queue_processor.py). Instância por GRUPO (Party,
PartyProcessorMixin.get_party_id_of) ou SOLO (o próprio eid) — reaproveita o
instanciamento genérico já usado por Arena/BG
(WorldServer._load_instance/_unload_instance).

Morte dentro de uma dungeon revive automaticamente (igual BG) via
_tick_dungeon_respawns — ver server/respawn_system.py pro cálculo de
GhostState.auto_respawn/respawn_s (NUNCA InstanceInventoryUIState/progressão
normalizada: jogador de dungeon mantém personagem real, nível/equipamento
verdadeiros, diferente de BG).
"""
from __future__ import annotations

from shared.constants import DUNGEON_RESPAWN_S, TICK_INTERVAL


class DungeonProcessorMixin:

    def request_dungeon_enter(self, session_id: str, player_eid: int,
                               dungeon_key: str, entrance_x: int, entrance_y: int) -> bool:
        """Chamado por session.py::_handle_zone_change_req quando a
        transição pisada carrega `dungeon_key`. Resolve instance_key (grupo
        → party_id, solo → o próprio eid), cria a instância sob demanda
        (fica viva até o último membro sair), transfere o player pro ponto
        de entrada. Retorna False (sem nenhum efeito) se `dungeon_key` não
        existe no catálogo — quem chama decide o que fazer nesse caso (ex:
        tratar como JSON mal configurado, nunca travar o player)."""
        from content.dungeon_definitions import DUNGEON_TABLE
        dungeon_def = DUNGEON_TABLE.get(dungeon_key)
        if dungeon_def is None:
            return False
        template_file = dungeon_def["template_file"]

        party_id = self.get_party_id_of(player_eid)
        group_key = f"party{party_id}" if party_id != -1 else f"solo{player_eid}"
        instance_key = f"{template_file}::{dungeon_key}_{group_key}"

        if instance_key not in self._dungeon_instances:
            if instance_key not in self._map_bundles:
                self._load_instance(template_file, instance_key)
            self._dungeon_instances[instance_key] = {
                "dungeon_key":     dungeon_key,
                "members":         [],
                "respawn_tile":    (entrance_x, entrance_y),
                "respawn_timers":  {},
                "return_pos":      {},
            }
        inst = self._dungeon_instances[instance_key]

        # Onde restaurar o player se ele desconectar DENTRO da dungeon (ver
        # end_dungeon_of) — mesmo padrão de MatchProcessorMixin's
        # match["return_pos"], gravado ANTES do transfer_player abaixo.
        inst["return_pos"][player_eid] = (
            self.get_player_map(session_id), *self.get_tile_pos(session_id))

        if player_eid not in inst["members"]:
            inst["members"].append(player_eid)
        self._player_dungeon_instance[player_eid] = instance_key

        self.transfer_player(session_id, player_eid, instance_key,
                              entrance_x, entrance_y, emit_moved_delta=True)
        return True

    def _dungeon_remove_member(self, player_eid: int, instance_key: str) -> None:
        """Bookkeeping de saída (voluntária via dungeon_handle_zone_change
        OU desconexão via end_dungeon_of) — último membro a sair derruba a
        instância (mesmo padrão 'último a sair fecha a porta' de
        MatchProcessorMixin._arena_leave_now)."""
        self._player_dungeon_instance.pop(player_eid, None)
        inst = self._dungeon_instances.get(instance_key)
        if inst is None:
            return
        inst["members"] = [m for m in inst["members"] if m != player_eid]
        inst["respawn_timers"].pop(player_eid, None)
        inst["return_pos"].pop(player_eid, None)
        if not inst["members"]:
            self._dungeon_instances.pop(instance_key, None)
            self._unload_instance(instance_key)

    def dungeon_handle_zone_change(self, player_eid: int, to_map: str) -> None:
        """Chamado por _handle_zone_change_req SEMPRE que um player completa
        QUALQUER transição normal (não só a de dungeon — cobre a transição
        de SAÍDA no extremo norte, que é uma transição comum igual qualquer
        outra, sem precisar de lógica dedicada nela). Detecta que o player
        estava numa instância de dungeon e o destino não é essa MESMA
        instância (ele saiu de verdade) e limpa membership."""
        instance_key = self._player_dungeon_instance.get(player_eid)
        if instance_key is None or to_map == instance_key:
            return
        self._dungeon_remove_member(player_eid, instance_key)

    def end_dungeon_of(self, player_eid: int) -> None:
        """Desconexão (server/session.py::on_disconnect) — PRECISA rodar
        ANTES do save, mesmo motivo de end_matches_of/end_bg_matches_of
        (match_processor.py/bg_queue_processor.py): sem isso, o save
        persistiria o map_id SINTÉTICO da instância (só existe em memória)
        — próximo login cairia num mapa inexistente. Teleporta de volta pro
        return_pos registrado na entrada."""
        instance_key = self._player_dungeon_instance.get(player_eid)
        if instance_key is None:
            return
        inst = self._dungeon_instances.get(instance_key)
        if inst is not None:
            r_map, r_x, r_y = inst["return_pos"].get(
                player_eid, (self._map_file, 115, 389))
            sid = self.get_session_id_for_player(player_eid)
            if sid is not None:
                self.transfer_player(sid, player_eid, r_map, r_x, r_y)
        self._dungeon_remove_member(player_eid, instance_key)

    def _tick_dungeon_respawns(self) -> None:
        """Espelho de BgQueueProcessorMixin._tick_bg_respawns — timer
        automático, cura cheia, MESMA instância, ponto fixo
        (inst["respawn_tile"] = o ponto de entrada da dungeon), SEM
        ghost/liberar-espírito."""
        if not self._dungeon_instances:
            return
        from engine.components import GhostState as _GSDun
        from engine.utils import snap_to_tile

        for inst in self._dungeon_instances.values():
            for eid in list(inst["members"]):
                gst = self.world.get_component(eid, _GSDun)
                if gst is None or not gst.is_dead:
                    inst["respawn_timers"].pop(eid, None)
                    continue
                if eid not in inst["respawn_timers"]:
                    inst["respawn_timers"][eid] = DUNGEON_RESPAWN_S
                    continue
                inst["respawn_timers"][eid] -= TICK_INTERVAL
                if inst["respawn_timers"][eid] > 0:
                    continue
                del inst["respawn_timers"][eid]
                rx, ry = inst["respawn_tile"]
                snap_to_tile(self.world, eid, rx, ry, carry_prev=False)
                self._revive_player(eid, hp_frac=1.0, at_corpse=False)
