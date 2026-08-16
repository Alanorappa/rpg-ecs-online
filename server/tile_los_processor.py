"""
server/tile_los_processor.py
Linha de visão de TERRENO entre entidades (13/08/2026, pedido do
usuário) — servidor nunca tinha checagem contra `vision_height`, só
proximidade (AOI) + zona de bush (`bush_zone_processor.py`, sem dado
real em nenhum mapa). Reaproveita a MESMA malha (`tile_matrix`) que já
bloqueia visão no cliente (Fase 49-53) — `create_tilemap` é código
compartilhado, o servidor já constrói essa malha.

§57 (13/08/2026) — desenho final, depois de 2 correções do usuário:
- **Universal**: `_has_tile_los` roda pra QUALQUER par de entidades,
  em QUALQUER contexto (mundo aberto E BG/instância) — mesma régua que
  já vale pra mob (`server/session.py::_can_see` não filtra mais por
  facção antes de chamar isto).
- **Isenção de vegetação bloqueante sempre a partir da posição do
  VIEWER** (não do alvo, como era em §55) — mesmo princípio já usado
  no cliente (`engine/fov.py::local_vision_blob`, §50): quem já está
  em cima de bush/copa nunca tem a PRÓPRIA visão bloqueada por aquele
  mesmo blob, seja pra onde for que esteja olhando. "2 players na
  mesma bush se veem" cai de graça disso — não precisa de checagem
  separada.
- **Sólido nunca é isento** (parede/pedra grande/tronco) — nenhuma das
  isenções abaixo nunca deixa ver através de um obstáculo de verdade.

§58 (16/08/2026) — visão de time vira UNIÃO de fontes independentes
(estilo LoL: "se um aliado vê, o time inteiro vê"), substituindo a
isenção estreita de bush que existia antes (`_ally_bush_blobs`, só
isentava a célula que um aliado ocupava fisicamente, sem checar raio
de visão nenhum). Agora: se o raycast a partir da PRÓPRIA posição do
viewer não enxerga, e o viewer tem Faction explícita, tenta de novo a
partir da posição de CADA aliado (`ally_centers`, mesma lista que
`SessionManager._compute_ally_vision_centers()` já calcula 1x por
tick — posição + raio próprio por tipo: player/torre/minion) — alvo
dentro do raio daquele aliado E raycast livre a partir dele = visível.
Sólido continua bloqueando de forma absoluta em QUALQUER raycast,
próprio ou de aliado.
"""
from __future__ import annotations

from engine.utils import bresenham_line_tiles, in_aoi
from engine.fov import local_vision_blob


class TileLosProcessorMixin:

    def _tilemap_for_map(self, map_file: "str | None"):
        from engine.components import Tilemap

        if map_file is None:
            return None
        bundle = self._map_bundles.get(map_file)
        if bundle is None:
            return None
        return self.world.get_component(bundle.tilemap_entity, Tilemap)

    def _has_tile_los(self, viewer_eid: int, target_eid: int,
                       ally_centers: "list[tuple[int, int, int]] | None" = None) -> bool:
        """True se o TIME do viewer enxerga o alvo — união de fontes
        independentes (§58, estilo LoL: "se um aliado vê, o time
        inteiro vê"). Tenta primeiro a partir da posição do PRÓPRIO
        viewer (mais barato — curto-circuita a maioria dos casos, visão
        direta sem obstrução); se não enxergar e `ally_centers` foi
        passado (mesma lista, mesmo formato, que `SessionManager.
        _compute_ally_vision_centers()` já calcula 1x por tick —
        posição + raio próprio por tipo de cada aliado: player/torre/
        minion), tenta de novo a partir de CADA aliado — alvo dentro do
        RAIO daquele aliado E raycast livre a partir dele já basta.

        Em cada raycast individual: o tile do PRÓPRIO alvo conta (parado
        em cima de bush = escondido pra aquela fonte, mesmo sem
        obstáculo "no meio do caminho"); o tile de ORIGEM nunca conta
        (`bresenham_line_tiles` já exclui a origem). Sólido (parede/
        pedra grande/tronco) SEMPRE bloqueia, sem exceção, em QUALQUER
        fonte. Vegetação bloqueante andável (bush/copa) é isenta se a
        célula faz parte do blob que a PRÓPRIA fonte ocupa agora, ou se
        o alvo atacou há pouco (`bush_reveal_timer`, §55).

        Mapas diferentes, ou qualquer dado ausente (TileMovement/
        Tilemap), tratam como SEM LOS (conservador)."""
        from engine.components import TileMovement, CombatState

        viewer_map = self.get_entity_map(viewer_eid)
        target_map = self.get_entity_map(target_eid)
        if viewer_map is None or viewer_map != target_map:
            return False

        viewer_tm = self.world.get_component(viewer_eid, TileMovement)
        target_tm = self.world.get_component(target_eid, TileMovement)
        if viewer_tm is None or target_tm is None:
            return False

        tilemap_comp = self._tilemap_for_map(viewer_map)
        if tilemap_comp is None:
            return False

        rows  = tilemap_comp.tile_matrix
        map_w = tilemap_comp.map_width_tiles
        map_h = tilemap_comp.map_height_tiles
        tx_t, ty_t = target_tm.current_tile_x, target_tm.current_tile_y

        def is_blocking(x, y):
            if not (0 <= x < map_w and 0 <= y < map_h):
                return True
            return rows[y][x].vision_height >= 2

        def is_solid(x, y):
            if not (0 <= x < map_w and 0 <= y < map_h):
                return True
            return rows[y][x].is_solid

        target_cst = self.world.get_component(target_eid, CombatState)
        recently_attacked = target_cst is not None and target_cst.bush_reveal_timer > 0

        def ray_clear(ox: int, oy: int) -> bool:
            own_blob = local_vision_blob(ox, oy, is_blocking, is_solid)
            for tx, ty in bresenham_line_tiles(ox, oy, tx_t, ty_t):
                if not (0 <= tx < map_w and 0 <= ty < map_h):
                    continue
                tile = rows[ty][tx]
                if tile.vision_height < 2:
                    continue
                if tile.is_solid:
                    return False  # sólido nunca é isento
                if recently_attacked or (tx, ty) in own_blob:
                    continue
                return False
            return True

        if ray_clear(viewer_tm.current_tile_x, viewer_tm.current_tile_y):
            return True

        if ally_centers:
            for ax, ay, radius in ally_centers:
                if not in_aoi(ax, ay, tx_t, ty_t, radius):
                    continue
                if ray_clear(ax, ay):
                    return True

        return False
