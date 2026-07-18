"""
server/pvp_zone_processor.py
Mixin para WorldServer: Zona PvP (Fase F do roadmap) — "sozinho = todos
hostis dentro da zona; em grupo = só quem está fora do grupo".

Diferente de duelo/trade, uma zona PvP não tem estado próprio (nenhum par
registrado, nenhuma checagem por tick): é um predicado 100% computado a
partir da posição atual dos dois players. `can_engage()` já é consultado a
cada tentativa de ataque (auto-attack, skill, magia/tiro — ver
`server/combat_processor.py`, `server/skill_processor.py`,
`server/spell_completion_processor.py`), então sair da zona no meio de uma
luta já bloqueia o próximo golpe automaticamente, sem precisar de nenhum
código de "encerrar combate" (ao contrário do duelo, que precisa de
`_tick_duel_distance_check` porque é um acordo persistente que sobrevive à
distância até o limite configurado).

Geometria das zonas vem de `pvp_zones` em `<mapa>_entities.json` (mesmo
padrão de `ambient_zones`, ver `engine/map_loader.py`), carregada em
`self._pvp_zones_by_map` por `WorldServer._load_map_for`.

Consultado por `WorldServer._pvp_allowed_between` (o resolver composto
registrado em `engine.faction_system.register_pvp_context`).
"""
from __future__ import annotations


class PvpZoneProcessorMixin:

    def _in_pvp_zone(self, eid: int) -> bool:
        """True se `eid` está, agora, dentro do retângulo de alguma zona
        PvP do mapa em que ele se encontra."""
        from engine.components import TileMovement

        map_file = self.get_entity_map(eid)
        zones = self._pvp_zones_by_map.get(map_file, [])
        if not zones:
            return False
        tm = self.world.get_component(eid, TileMovement)
        if tm is None:
            return False
        for z in zones:
            x1, y1, x2, y2 = z["rect"]
            if x1 <= tm.current_tile_x <= x2 and y1 <= tm.current_tile_y <= y2:
                return True
        return False

    def _pvp_zone_allows(self, attacker_id: int, target_id: int) -> bool:
        """Ambos precisam estar dentro da MESMA zona (implícito: mesmo
        mapa, já que `_in_pvp_zone` só compara contra as zonas do mapa de
        cada um). Exceção de grupo: mesmo dentro da zona, membros do
        MESMO grupo continuam amigáveis entre si — só quem está fora do
        grupo do atacante é hostil (`get_party_id_of` vem de
        `PartyProcessorMixin`, já misturado em `WorldServer`)."""
        if not (self._in_pvp_zone(attacker_id) and self._in_pvp_zone(target_id)):
            return False
        pid = self.get_party_id_of(attacker_id)
        if pid != -1 and pid == self.get_party_id_of(target_id):
            return False
        return True
