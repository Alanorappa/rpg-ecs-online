"""
tests/test_bush_stealth.py — Stealth de bush estilo MOBA (13/08/2026,
pedido do usuário). Ver server/bush_zone_processor.py e
PROBLEMAS_ARQUITETURA.md.

`_get_bush_zone`/`bush_zones_by_map` (retângulo, `TestGetBushZone`/
`TestCanSeeBush`/`TestBushTargetClear` abaixo) continuam testados com
zona sintética — modelado em cima de tests/test_pvp_zone.py — mas
NUNCA populado em mapa real (nenhum `_entities.json` tem `bush_zones`
pintado). A invisibilidade de verdade vem de outro mecanismo
(`server/tile_los_processor.py::_has_tile_los`, vision_height do
próprio tile); `_is_on_bush_tile`/`_sync_player_bush_dirty`
(`TestIsOnBushTile`/`TestSyncPlayerBushDirty` abaixo, 01/09/2026) usam
`_plant_object()` pra plantar um tile de arbusto REAL do catálogo
(`OBJECT_MAPPING`) — ver PROBLEMAS_ARQUITETURA.md §91.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from server.session import _can_see
from engine.components import Faction, MapLocation, CombatState, Tilemap
from engine.entity_factory import create_minion
from engine.tileset import FLOOR_TILE

_BUSH_A = (100, 100, 105, 105)   # x1,y1,x2,y2 em tiles
_BUSH_B = (200, 200, 205, 205)   # outro bush, longe do A
_IN_A    = (102, 102)
_IN_B    = (202, 202)
_OUTSIDE = (5, 5)


def _plant_object(ws, tx: int, ty: int, obj_tile_id: str) -> None:
    """Planta um tile de objeto REAL do catálogo (`OBJECT_MAPPING`,
    engine/tileset.py) direto no `tile_matrix` — 01/09/2026, testes de
    `_is_on_bush_tile`/`_sync_player_bush_dirty` (fonte trocada de
    `bush_zones` sintético pra arbusto de verdade, ver
    PROBLEMAS_ARQUITETURA.md §91). Mesmo espírito de `_clear_terrain`
    abaixo, só que escreve o TileType do catálogo em vez de FLOOR_TILE."""
    from engine.tileset import OBJECT_MAPPING
    bundle = ws._map_bundles[ws._map_file]
    tm = ws.world.get_component(bundle.tilemap_entity, Tilemap)
    tm.tile_matrix[ty][tx] = OBJECT_MAPPING[obj_tile_id]


def _clear_terrain(ws, x0: int, y0: int, x1: int, y1: int) -> None:
    """Sobrescreve um retângulo com FLOOR_TILE — necessário desde §57
    (linha de visão de terreno universal): `_can_see` agora também
    checa `_has_tile_los` pra QUALQUER par, então testes de zona de
    bush (§47, `_OUTSIDE`/`_IN_A` bem distantes um do outro) precisam
    de um caminho livre de verdade entre eles, ou terreno real de
    `map_1.csv` interfere sem relação nenhuma com o que o teste quer
    validar (a zona de bush em si)."""
    bundle = ws._map_bundles[ws._map_file]
    tm = ws.world.get_component(bundle.tilemap_entity, Tilemap)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            tm.tile_matrix[y][x] = FLOOR_TILE


class TestBushFootprintPropagation(unittest.TestCase):
    """`engine/entity_factory.py::create_tilemap` — `TileType.is_bush`
    (01/09/2026) precisa propagar pra TODAS as células da pegada de
    visão do objeto (`vision_rect="base_row"`), não só a célula-âncora —
    arbustos largos (ex: `pl_b3`, 64×48px = 2 tiles de largura) cobrem
    mais de 1 tile na base. Mesmo mecanismo que já propaga
    `vision_height`."""

    def test_arbusto_largo_marca_is_bush_em_toda_a_base(self):
        from engine.world import World
        from engine.entity_factory import create_tilemap
        from engine.components import Tilemap

        terrain = ["G" * 10 for _ in range(10)]
        obj_matrix = [list("." * 10) for _ in range(10)]
        obj_matrix[5][5] = "pl_b3"   # 64x48px = 2 tiles de largura, base_row

        w = World()
        eid = create_tilemap(w, terrain, obj_matrix)
        tm = w.get_component(eid, Tilemap)
        self.assertTrue(tm.tile_matrix[5][5].is_bush, "célula-âncora deveria ser bush")
        self.assertTrue(tm.tile_matrix[5][6].is_bush, "2ª célula da base do arbusto largo também deveria ser bush")

    def test_arvore_nunca_marca_is_bush(self):
        from engine.world import World
        from engine.entity_factory import create_tilemap
        from engine.components import Tilemap

        terrain = ["G" * 10 for _ in range(10)]
        obj_matrix = [list("." * 10) for _ in range(10)]
        obj_matrix[5][5] = "pl_t4"   # árvore larga, mesma família de vision_height=2

        w = World()
        eid = create_tilemap(w, terrain, obj_matrix)
        tm = w.get_component(eid, Tilemap)
        blocking_cells = [tm.tile_matrix[y][x]
                          for y in range(10) for x in range(10)
                          if tm.tile_matrix[y][x].vision_height >= 2]
        self.assertTrue(blocking_cells, "sanity: árvore ainda deveria bloquear visão em algum tile")
        self.assertTrue(all(not t.is_bush for t in blocking_cells),
                        "árvore bloqueia visão mas nunca deveria marcar is_bush")


class TestGetBushZone(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", *_OUTSIDE)
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = [
            {"name": "Bush A", "rect": _BUSH_A},
            {"name": "Bush B", "rect": _BUSH_B},
        ]

    def test_fora_de_qualquer_bush_retorna_none(self):
        self.assertIsNone(self.ws._get_bush_zone(self.a))

    def test_dentro_do_bush_a_retorna_indice_0(self):
        set_entity_tile(self.ws, self.a, *_IN_A)
        self.assertEqual(self.ws._get_bush_zone(self.a), 0)

    def test_dentro_do_bush_b_retorna_indice_1(self):
        set_entity_tile(self.ws, self.a, *_IN_B)
        self.assertEqual(self.ws._get_bush_zone(self.a), 1)

    def test_mapa_sem_bush_zones_nao_quebra(self):
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = []
        set_entity_tile(self.ws, self.a, *_IN_A)
        self.assertIsNone(self.ws._get_bush_zone(self.a))


class TestCanSeeBush(unittest.TestCase):
    """`_can_see` — visibilidade dentro de bush, com/sem visão de time
    compartilhada."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_terrain(self.ws, 0, 0, 210, 210)
        self.a = spawn_player(self.ws, "s1", *_IN_A)   # vai ficar dentro do bush
        self.b = spawn_player(self.ws, "s2", *_OUTSIDE)  # viewer, fora
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = [{"name": "Bush A", "rect": _BUSH_A}]

    def test_alvo_no_bush_viewer_fora_sem_time_e_invisivel(self):
        self.assertFalse(_can_see(self.ws, self.b, self.a))

    def test_alvo_e_viewer_no_mesmo_bush_e_visivel(self):
        set_entity_tile(self.ws, self.b, *_IN_A)
        self.assertTrue(_can_see(self.ws, self.b, self.a))

    def test_fora_de_bush_visibilidade_normal_nao_muda(self):
        """Sanity check — regressão: sem ninguém em bush, _can_see continua
        se comportando como antes (só is_visible/ghost importam)."""
        set_entity_tile(self.ws, self.a, *_OUTSIDE)
        self.assertTrue(_can_see(self.ws, self.b, self.a))
        self.assertTrue(_can_see(self.ws, self.a, self.b))

    def test_aliado_no_mesmo_bush_revela_pro_time_inteiro(self):
        """Visão de bush compartilhada por time (pedido explícito do
        usuário, estilo LoL): viewer fora do bush, mas um ALIADO dele
        está dentro — o time inteiro enxerga quem está lá."""
        c = spawn_player(self.ws, "s3", *_IN_A)  # aliado de b, dentro do bush
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.ws.world.add_component(c,      Faction(faction_id="time_x"))
        self.assertTrue(_can_see(self.ws, self.b, self.a))

    def test_time_adversario_no_bush_nao_revela(self):
        """Visão de bush é só compartilhada DENTRO do próprio time —
        inimigo no bush não ajuda o time do viewer a enxergar."""
        c = spawn_player(self.ws, "s3", *_IN_A)  # time DIFERENTE de b
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.ws.world.add_component(c,      Faction(faction_id="time_y"))
        self.assertFalse(_can_see(self.ws, self.b, self.a))

    def test_minion_no_bush_tambem_fica_oculto(self):
        map_file = self.ws.get_entity_map(self.a)
        minion = create_minion(self.ws.world, _IN_A[0], _IN_A[1], "minion_melee",
                               faction_id="time_minion", route=[_IN_A])
        self.ws.world.add_component(minion, MapLocation(map_file))
        self.assertFalse(_can_see(self.ws, self.b, minion))

    def test_minion_aliado_no_bush_revela_pro_time(self):
        map_file = self.ws.get_entity_map(self.a)
        minion = create_minion(self.ws.world, _IN_A[0], _IN_A[1], "minion_melee",
                               faction_id="time_x", route=[_IN_A])
        self.ws.world.add_component(minion, MapLocation(map_file))
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.assertTrue(_can_see(self.ws, self.b, self.a))


class TestBushTargetClear(unittest.TestCase):
    """`WorldServer._tick_bush_target_clear` — alvo travado antes da
    entrada no bush precisa ser limpo explicitamente (mesmo motivo já
    documentado na Camuflagem: is_visible/bush sozinho só bloqueia mira
    NOVA, não limpa uma trava já existente)."""

    def setUp(self):
        self.ws = make_world_server()
        self.attacker = spawn_player(self.ws, "s1", *_OUTSIDE)
        self.target   = spawn_player(self.ws, "s2", *_OUTSIDE)
        map_file = self.ws.get_entity_map(self.attacker)
        self.ws._bush_zones_by_map[map_file] = [{"name": "Bush A", "rect": _BUSH_A}]

    def _lock_target(self):
        cst = self.ws.world.get_component(self.attacker, CombatState)
        cst.target_entity_id = self.target

    def test_alvo_trava_some_quando_ele_entra_sozinho_num_bush(self):
        self._lock_target()
        set_entity_tile(self.ws, self.target, *_IN_A)
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, -1)

    def test_alvo_trava_sobrevive_se_os_2_entram_juntos_no_bush(self):
        self._lock_target()
        set_entity_tile(self.ws, self.target,   *_IN_A)
        set_entity_tile(self.ws, self.attacker, *_IN_A)
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)

    def test_sem_transicao_de_bush_nao_mexe_em_nada(self):
        """Ninguém mudou de zona — o tick não deveria fazer nada, nem
        limpar um alvo que continua perfeitamente visível."""
        self._lock_target()
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)

    def test_minion_perde_current_target_eid_quando_alvo_entra_no_bush(self):
        map_file = self.ws.get_entity_map(self.attacker)
        minion = create_minion(self.ws.world, *_OUTSIDE, "minion_melee",
                               faction_id="time_minion", route=[_OUTSIDE])
        self.ws.world.add_component(minion, MapLocation(map_file))
        from engine.components import Minion as _MinTC
        m = self.ws.world.get_component(minion, _MinTC)
        m.current_target_eid = self.target
        m.state = "FIGHTING"

        set_entity_tile(self.ws, self.target, *_IN_A)
        self.ws._tick_bush_target_clear()

        m = self.ws.world.get_component(minion, _MinTC)
        self.assertEqual(m.current_target_eid, -1)
        self.assertEqual(m.state, "ADVANCING")

    def test_torre_nunca_participa(self):
        """Torre não tem TileMovement que muda de tile de verdade — o
        tick não deveria quebrar nem afetar nada por causa dela."""
        from engine.entity_factory import create_tower
        map_file = self.ws.get_entity_map(self.attacker)
        tower = create_tower(self.ws.world, _IN_A[0], _IN_A[1], "torre_de_fogo",
                             faction_id="time_torre")
        self.ws.world.add_component(tower, MapLocation(map_file))
        self._lock_target()
        # Não deveria lançar exceção nem afetar o alvo travado do attacker
        # (que não tem relação nenhuma com a torre).
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)


class TestBushTargetClearGuard(unittest.TestCase):
    """05/09/2026 (ver PROBLEMAS_ARQUITETURA.md §113) — o guard antigo
    (`if not self._bush_zones_by_map: return`) checava o dict inteiro,
    nunca o CONTEÚDO: `_load_map_for` grava uma entrada por mapa mesmo
    com lista vazia (`spawn_points.get("bush_zones", [])`), então o
    dict nunca ficava vazio depois do boot — o guard nunca disparava em
    produção (nenhum mapa real tem `bush_zones` populado) e a função
    escaneava o mundo inteiro todo tick pra sempre achar zona nenhuma.
    Corrigido pra `if not any(self._bush_zones_by_map.values()): return`."""

    def setUp(self):
        self.ws = make_world_server()

    def _count_tile_movement_scans(self) -> int:
        from engine.components import TileMovement as _TM_guard
        orig = self.ws.world.get_entities_with
        calls = {"n": 0}
        def counted(*component_types):
            if component_types == (_TM_guard,):
                calls["n"] += 1
            return orig(*component_types)
        self.ws.world.get_entities_with = counted
        try:
            self.ws._tick_bush_target_clear()
        finally:
            self.ws.world.get_entities_with = orig
        return calls["n"]

    def test_mapas_reais_carregados_tem_bush_zones_vazio(self):
        """Sanity check do cenário que motivou o bug — confirma que
        `make_world_server()` reproduz a condição real de produção."""
        self.assertTrue(self.ws._bush_zones_by_map, "esperava ao menos 1 mapa carregado")
        self.assertFalse(any(self.ws._bush_zones_by_map.values()),
                         "nenhum mapa real deveria ter bush_zones povoado — "
                         "se isso falhar, o cenário de teste não reproduz mais o bug original")

    def test_guard_impede_scan_quando_todo_mapa_tem_lista_vazia(self):
        self.assertEqual(self._count_tile_movement_scans(), 0,
                         "guard deveria ter retornado ANTES de qualquer "
                         "get_entities_with(TileMovement) — nenhum mapa tem zona real")

    def test_guard_nao_bloqueia_quando_algum_mapa_tem_zona_real(self):
        map_file = next(iter(self.ws._bush_zones_by_map))
        self.ws._bush_zones_by_map[map_file] = [{"name": "Zona real", "rect": (0, 0, 5, 5)}]
        self.assertGreater(self._count_tile_movement_scans(), 0,
                           "com pelo menos 1 mapa com zona de verdade, a função "
                           "precisa continuar escaneando normalmente")


class TestIsOnBushTile(unittest.TestCase):
    """`BushZoneProcessorMixin._is_on_bush_tile()` (01/09/2026) — detecção
    baseada em arbusto REAL do mapa (`TileType.is_bush`), não no
    retângulo `bush_zones` (nunca pintado em mapa real, ver
    PROBLEMAS_ARQUITETURA.md §91)."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", *_OUTSIDE)

    def test_fora_de_qualquer_objeto_retorna_false(self):
        self.assertFalse(self.ws._is_on_bush_tile(self.eid))

    def test_em_cima_de_arbusto_real_retorna_true(self):
        _plant_object(self.ws, *_IN_A, "pl_b1")
        set_entity_tile(self.ws, self.eid, *_IN_A)
        self.assertTrue(self.ws._is_on_bush_tile(self.eid))

    def test_em_cima_de_arvore_retorna_false(self):
        """Guarda de regressão: árvore também bloqueia visão
        (vision_height=2, mesma família de dado) mas NÃO é bush — o
        usuário confirmou explicitamente que "atrás da árvore" não deve
        ativar a opacidade."""
        _plant_object(self.ws, *_IN_A, "pl_t1")
        set_entity_tile(self.ws, self.eid, *_IN_A)
        self.assertFalse(self.ws._is_on_bush_tile(self.eid))


class TestSyncPlayerBushDirty(unittest.TestCase):
    """`WorldServer._sync_player_bush_dirty()` (01/09/2026, feedback visual
    de opacidade 50% pro próprio jogador) — dirty-check privado, MESMO
    princípio de `_sync_player_hp_dirty` (ver tests/test_owner_hp_max_sync.py):
    só enfileira STATS_UPDATE quando o estado "está em cima de um arbusto
    real" MUDA. Fonte: `_is_on_bush_tile` (arbusto plantado de verdade no
    tile_matrix), NÃO `_bush_zones_by_map` — troca feita depois que o
    usuário testou em jogo e não viu a opacidade (nenhum mapa real tem
    `bush_zones`, ver PROBLEMAS_ARQUITETURA.md §91)."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", *_OUTSIDE)
        _plant_object(self.ws, *_IN_A, "pl_b1")
        self.ws.consume_stats_updates()  # drena qualquer update do spawn

    def _entry_for(self, eid):
        for entry in self.ws.consume_stats_updates():
            if entry.get("player_eid") == eid:
                return entry
        return None

    def test_entrar_na_bush_gera_update_com_in_bush_true(self):
        set_entity_tile(self.ws, self.eid, *_IN_A)
        self.ws._sync_player_bush_dirty()
        entry = self._entry_for(self.eid)
        self.assertIsNotNone(entry)
        self.assertTrue(entry["in_bush"])

    def test_sair_da_bush_gera_update_com_in_bush_false(self):
        set_entity_tile(self.ws, self.eid, *_IN_A)
        self.ws._sync_player_bush_dirty()
        self.ws.consume_stats_updates()
        set_entity_tile(self.ws, self.eid, *_OUTSIDE)
        self.ws._sync_player_bush_dirty()
        entry = self._entry_for(self.eid)
        self.assertIsNotNone(entry)
        self.assertFalse(entry["in_bush"])

    def test_sem_transicao_nao_gera_update(self):
        self.ws._sync_player_bush_dirty()   # baseline: fora da bush, registra no cache
        self.ws.consume_stats_updates()
        self.ws._sync_player_bush_dirty()   # nada mudou
        self.assertIsNone(self._entry_for(self.eid))

    def test_permanecer_dentro_da_bush_nao_gera_update_novo(self):
        set_entity_tile(self.ws, self.eid, *_IN_A)
        self.ws._sync_player_bush_dirty()
        self.ws.consume_stats_updates()
        self.ws._sync_player_bush_dirty()   # continua no mesmo bush
        self.assertIsNone(self._entry_for(self.eid))

    def test_tile_igual_ao_anterior_nem_chama_is_on_bush_tile(self):
        """§121 (06/09/2026) — gate de posição (`_player_bush_tile_cache`):
        se o tile não mudou desde o tick anterior, `_is_on_bush_tile`
        (resolve mapa+tilemap+bounds-check, medido como custo real) nem é
        chamada — prova a otimização de verdade, não só que o resultado
        final continua certo (os 4 testes acima já cobrem isso)."""
        self.ws._sync_player_bush_dirty()  # 1ª chamada: popula o cache de tile
        _orig = self.ws._is_on_bush_tile
        _calls = {"n": 0}
        def _spy(eid):
            _calls["n"] += 1
            return _orig(eid)
        self.ws._is_on_bush_tile = _spy
        try:
            self.ws._sync_player_bush_dirty()  # mesmo tile — não deveria chamar
        finally:
            self.ws._is_on_bush_tile = _orig
        self.assertEqual(_calls["n"], 0,
                         "tile igual ao tick anterior não deveria chamar _is_on_bush_tile")

    def test_tile_diferente_chama_is_on_bush_tile(self):
        """Contrapartida do teste acima — mudar de tile TEM que continuar
        disparando a checagem real, senão o gate quebra a detecção."""
        self.ws._sync_player_bush_dirty()  # popula o cache no tile inicial
        set_entity_tile(self.ws, self.eid, *_IN_A)
        _orig = self.ws._is_on_bush_tile
        _calls = {"n": 0}
        def _spy(eid):
            _calls["n"] += 1
            return _orig(eid)
        self.ws._is_on_bush_tile = _spy
        try:
            self.ws._sync_player_bush_dirty()
        finally:
            self.ws._is_on_bush_tile = _orig
        self.assertEqual(_calls["n"], 1, "tile mudou — tem que rechecar de verdade")


if __name__ == "__main__":
    unittest.main()
