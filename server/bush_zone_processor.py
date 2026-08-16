"""
server/bush_zone_processor.py
Mixin para WorldServer: stealth de bush estilo MOBA (13/08/2026, pedido do
usuário) — jogador ou minion dentro de um bush fica invisível pra quem não
tem presença (própria ou de time) dentro do MESMO bush, mesmo que o bush
esteja dentro do raio de visão normal (AOI) do viewer.

Mesmo espírito de `pvp_zone_processor.py::_in_pvp_zone` (predicado 100%
computado a partir da posição atual, sem cache, sem checagem por tick) —
geometria vem de `bush_zones` em `<mapa>_entities.json` (mesmo padrão de
`pvp_zones`, ver `engine/map_loader.py`), carregada em
`self._bush_zones_by_map` por `WorldServer._load_map_for`.

Consultado por `server/session.py::_can_see` (gate central de visibilidade
de AOI, já usado por Camuflagem/ghost). Torres nunca participam — não têm
`TileMovement` que muda de tile (mesma exceção já usada pra imunidade a
CC, ver CLAUDE.md).
"""
from __future__ import annotations


class BushZoneProcessorMixin:

    def _get_bush_zone(self, eid: int) -> "int | None":
        """Índice (posição na lista de zonas do mapa) do bush em que `eid`
        está agora, ou None se não está em nenhum. Sob demanda, sem cache
        — mesmo espírito de `_in_pvp_zone`."""
        from engine.components import TileMovement

        map_file = self.get_entity_map(eid)
        zones = self._bush_zones_by_map.get(map_file, [])
        if not zones:
            return None
        tm = self.world.get_component(eid, TileMovement)
        if tm is None:
            return None
        for i, z in enumerate(zones):
            x1, y1, x2, y2 = z["rect"]
            if x1 <= tm.current_tile_x <= x2 and y1 <= tm.current_tile_y <= y2:
                return i
        return None

    def _team_sees_bush_zone(self, viewer_eid: int, zone_idx: int) -> bool:
        """True se QUALQUER unidade (player ou minion) do MESMO time de
        `viewer_eid`, no MESMO mapa, está fisicamente na zona `zone_idx`
        agora — visão de bush é compartilhada por time (pedido explícito
        do usuário, estilo League of Legends: 1 unidade do time dentro do
        bush revela pra todo o time), mesmo espírito de
        `SessionManager._compute_ally_vision_centers` pro raio normal de
        visão, mas aplicado a presença DENTRO do bush, não a um raio."""
        from engine.components import Faction

        viewer_fac = self.world.get_component(viewer_eid, Faction)
        if viewer_fac is None:
            return False  # sem time = sem visão compartilhada (open world)
        viewer_map = self.get_entity_map(viewer_eid)
        for eid, fac in self.world.get_entities_with(Faction):
            if eid == viewer_eid or fac.faction_id != viewer_fac.faction_id:
                continue
            if self.get_entity_map(eid) != viewer_map:
                continue
            if self._get_bush_zone(eid) == zone_idx:
                return True
        return False
