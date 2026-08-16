"""
tests/test_idle_map_perf.py — Desperdício de CPU em mapa sem player
(04/08/2026, pedido do usuário: "quero solução definitiva").

Achado real (log de playtest, 2 contas, servidor real): mapas abertos sem
NENHUM player conectado continuavam custando ~1-2.5ms/tick cada, mesmo
"[ativo 0/300 ticks, 0p agora]". Causa raiz (código, não suposição):

1. `TileValidationSystem.update()` reconstrói seu cache `_occupied` via
   scan GLOBAL de `TileMovement` (todo o mundo) todo tick, por bundle —
   mesmo quando ninguém consulta esse cache (pathfinding/AI já desligados
   sem player). Fix: entra no MESMO gate que já pulava EnemyAISystem/
   EnemyAbilitySystem sem player (`_MapBundle.ai_systems`).
2. `SpawnZoneSystem.update()` fazia o MESMO scan global (redundante),
   incondicional, só pra saber tiles ocupados — mas só é USADO dentro de
   `_pick_tile()`, chamado SÓ quando um timer de respawn realmente zera.
   Fix: scan vira preguiçoso (`_get_occupied()`), só constrói se algum
   spawn for de fato necessário este tick.
3. `combat_spatial_hash` (server/world_server.py::_tick) hasheava TODO
   combatente de TODO mapa, mas só Tower/Minion consultam — e esses só
   existem em instância de Battleground. Fix: só insere entidades de
   mapas com lane registrada (`self._minion_lanes`).
4. `MinionSystem.MAX_PATHFINDS_PER_FRAME` era um orçamento ÚNICO
   compartilhado entre TODAS as partidas de BG concorrentes — uma
   partida podia roubar o orçamento de pathfind de outra. Fix: dict por
   `map_file`, criado sob demanda.

Uso:
    py -3.10 -m pytest tests/test_idle_map_perf.py -v
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks

MAP_A = "maps/map_1.csv"
MAP_B = "maps/map_cave_west.csv"


# ─────────────────────────────────────────────────────────────────────────
# 1) TileValidationSystem pulado em mapa sem player
# ─────────────────────────────────────────────────────────────────────────

class TestTileValidationSkippedWhenMapEmpty(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def _count_calls(self, bundle_key: str) -> dict:
        bundle = self.ws._map_bundles.get(bundle_key)
        self.assertIsNotNone(bundle, f"bundle {bundle_key} não carregado")
        calls = {"n": 0}
        orig = bundle.tile_validation.update
        def counted(*a, **kw):
            calls["n"] += 1
            return orig(*a, **kw)
        bundle.tile_validation.update = counted
        return calls

    def test_nao_roda_em_mapa_sem_player(self):
        """map_cave_west fica sem NENHUM player durante o teste inteiro —
        TileValidationSystem desse bundle não deve rodar nem 1x."""
        spawn_player(self.ws, "s1", 130, 374)  # nasce em map_1
        run_ticks(self.ws, 5)  # deixa o mundo assentar antes de medir
        calls = self._count_calls(MAP_B)
        run_ticks(self.ws, 20)
        self.assertEqual(calls["n"], 0,
            "TileValidationSystem não deveria rodar em mapa sem player conectado")

    def test_roda_normalmente_em_mapa_com_player(self):
        """Sanity check do mecanismo de contagem + prova de que o mapa
        ATIVO continua funcionando normal (nenhuma regressão pra quem
        está jogando)."""
        spawn_player(self.ws, "s1", 130, 374)  # nasce em map_1
        calls = self._count_calls(MAP_A)
        run_ticks(self.ws, 5)
        self.assertGreater(calls["n"], 0,
            "TileValidationSystem deveria rodar todo tick no mapa com player")


# ─────────────────────────────────────────────────────────────────────────
# 2) SpawnZoneSystem — scan de tiles ocupados só quando precisa spawnar
# ─────────────────────────────────────────────────────────────────────────

class TestSpawnZoneOccupiedLazy(unittest.TestCase):

    def _make_zone_world(self, max_count=1, alive=True, timer_ready=False):
        """Tilemap + PathfindingSystem REAIS, injetados direto no
        SpawnZoneSystem (`pathfinding=`) — evita depender do `_svc`
        global (que outro teste, rodando antes na mesma sessão do
        pytest, pode ou não ter populado; sem isso o teste passava só
        por sorte de ordem de execução)."""
        from engine.world import World
        from engine.components import SpawnZone, MapLocation, TileMovement, Enemy
        from engine.entity_factory import create_tilemap
        from engine.world_systems import PathfindingSystem
        w = World()
        terrain = ["." * 20 for _ in range(20)]
        objects = [["."] * 20 for _ in range(20)]
        tm_eid = create_tilemap(w, terrain, objects, None)
        pf = PathfindingSystem(w, tilemap_entity=tm_eid)

        zeid = w.create_entity()
        zone = SpawnZone(center_x=5, center_y=5, enemy_type="melee", enemy_tier="normal",
                         radius=3, max_count=max_count, respawn_cooldown=10.0)
        w.add_component(zeid, zone)
        w.add_component(zeid, MapLocation(MAP_A))

        if alive:
            mob_eid = w.create_entity()
            w.add_component(mob_eid, TileMovement(current_tile_x=5, current_tile_y=5))
            w.add_component(mob_eid, Enemy())
            w.add_component(mob_eid, MapLocation(MAP_A))
            zone.active_entity_ids = {mob_eid}
        if timer_ready:
            zone.respawn_timers = [0.0]  # já vencido — força _pick_tile neste tick

        return w, zeid, zone, pf

    def _count_tilemovement_scans(self, w) -> dict:
        """Conta só chamadas get_entities_with(TileMovement) — a mesma
        assinatura usada pelo scan de 'occupied' (a de player-position usa
        2 componentes, não colide)."""
        from engine.components import TileMovement
        orig = w.get_entities_with
        calls = {"n": 0}
        def counted(*components):
            if components == (TileMovement,):
                calls["n"] += 1
            return orig(*components)
        w.get_entities_with = counted
        return calls

    def test_zona_ja_cheia_nao_dispara_scan_global(self):
        from engine.world_systems import SpawnZoneSystem
        w, zeid, zone, pf = self._make_zone_world(max_count=1, alive=True, timer_ready=False)
        sys = SpawnZoneSystem(w, map_filter=MAP_A, pathfinding=pf)
        sys.ACTIVATION_RADIUS = 999999
        calls = self._count_tilemovement_scans(w)

        sys.update(dt=0.05)

        self.assertEqual(calls["n"], 0,
            "zona já no max_count, sem timer pendente, não deveria escanear tiles ocupados")

    def test_timer_vencido_dispara_scan_e_spawna(self):
        from engine.world_systems import SpawnZoneSystem
        w, zeid, zone, pf = self._make_zone_world(max_count=2, alive=True, timer_ready=True)
        sys = SpawnZoneSystem(w, map_filter=MAP_A, pathfinding=pf)
        sys.ACTIVATION_RADIUS = 999999
        calls = self._count_tilemovement_scans(w)

        sys.update(dt=0.05)

        self.assertGreaterEqual(calls["n"], 1,
            "timer vencido deveria mesmo assim disparar o scan (precisa saber onde NÃO nascer)")
        self.assertEqual(len(zone.active_entity_ids), 2,
            "spawn deveria ter acontecido (zone estava com 1/2, timer venceu)")


# ─────────────────────────────────────────────────────────────────────────
# 3) combat_spatial_hash — só mapas com Tower/Minion (lane registrada)
# ─────────────────────────────────────────────────────────────────────────

class TestCombatSpatialHashScopedToBgMaps(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    # cell_size da combat_spatial_hash real (server/world_server.py,
    # `_SpatialHashCH(cell_size=9)`) — discriminador pra distinguir ELA
    # de qualquer outro uso de `SpatialHash` no processo. Ficou
    # necessário na Fase 4.6 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md
    # §29): `EnemyAISystem._active_mobs_this_tick` passou a usar a MESMA
    # classe (célula = SLEEP_RADIUS_TILES = 20) pro pré-filtro de IA —
    # legítimo em mapa aberto (o cenário que este teste monta de
    # propósito), então um monkey-patch cego em `SpatialHash.insert`
    # conta as duas coisas juntas e quebra a suposição original
    # ("mapa aberto sem lane nunca insere na hash de COMBATE").
    _COMBAT_HASH_CELL_SIZE = 9

    def _count_hash_inserts(self) -> dict:
        from engine.utils import SpatialHash
        orig_insert = SpatialHash.insert
        calls = {"n": 0}
        def counted(self_hash, *a, **kw):
            if getattr(self_hash, "_cs", None) == self._COMBAT_HASH_CELL_SIZE:
                calls["n"] += 1
            return orig_insert(self_hash, *a, **kw)
        SpatialHash.insert = counted
        self.addCleanup(setattr, SpatialHash, "insert", orig_insert)
        return calls

    def test_mapa_aberto_sem_lane_nao_entra_na_hash(self):
        calls = self._count_hash_inserts()
        spawn_player(self.ws, "s1", 130, 374)  # map_1, sem nenhuma lane registrada
        run_ticks(self.ws, 5)
        self.assertEqual(calls["n"], 0,
            "mapa aberto sem Tower/Minion não deveria inserir nada na combat_spatial_hash")

    def test_mapa_com_lane_registrada_entra_na_hash(self):
        calls = self._count_hash_inserts()
        spawn_player(self.ws, "s1", 130, 374)
        # simula uma instância de BG carregada nesse mesmo mapa — mesmo
        # sinal (`_minion_lanes`) que _create_minion_lanes grava de verdade
        self.ws._minion_lanes[MAP_A] = [{"faction": "arena_time_a", "lane_id": "top"}]
        run_ticks(self.ws, 5)
        self.assertGreater(calls["n"], 0,
            "mapa com lane registrada deveria inserir o player (candidato a alvo) na hash")


# ─────────────────────────────────────────────────────────────────────────
# 4) MinionSystem — orçamento de pathfind isolado por map_file
# ─────────────────────────────────────────────────────────────────────────

class TestMinionPathfindBudgetPerMap(unittest.TestCase):

    def setUp(self):
        from engine.world_systems import (MinionSystem, register_services, register_service_resolver,
                                          CombatSystem, PathfindingSystem, TileValidationSystem)
        from engine.entity_factory import create_tilemap, create_minion
        register_service_resolver(None)
        self.world = __import__("engine.world", fromlist=["World"]).World()
        terrain = ["." * 40 for _ in range(40)]
        objects = [["."] * 40 for _ in range(40)]
        tm_eid = create_tilemap(self.world, terrain, objects, None)
        self.pf = PathfindingSystem(self.world, tilemap_entity=tm_eid)
        tv = TileValidationSystem(self.world, tilemap_entity=tm_eid)
        combat = CombatSystem(self.world, is_server=True)
        register_services(combat=combat, pathfinding=self.pf, tile_validation=tv)
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)
        self._create_minion = create_minion

    def _make_minion(self, x, y, target, map_file):
        from engine.components import MapLocation
        route = [(x, y)] + list(self.pf.find_path((x, y), target))
        eid = self._create_minion(self.world, x, y, "minion_melee",
                                  faction_id="time_a", route=route)
        self.world.add_component(eid, MapLocation(map_file))
        return eid

    def test_orcamento_de_uma_partida_nao_rouba_de_outra(self):
        """Antes (int único global): MAX_PATHFINDS_PER_FRAME=1 esgotado
        pelo minion do mapa A deixaria o minion do mapa B sem pathfind
        neste tick — prova diferencial: reverter pra `self.
        _pathfind_budget = self.MAX_PATHFINDS_PER_FRAME` (int) faz este
        teste falhar (minion2.current_path fica vazio)."""
        from engine.components import Minion, Position, TileMovement
        self.ms.MAX_PATHFINDS_PER_FRAME = 1
        self.ms._pathfind_budget = {}

        m1 = self._make_minion(5, 5, (5, 20), "maps/map_1.csv")
        m2 = self._make_minion(6, 6, (6, 20), "moba_battleground::bg_test")

        minion1, pos1, tm1 = (self.world.get_component(m1, Minion),
                              self.world.get_component(m1, Position),
                              self.world.get_component(m1, TileMovement))
        minion2, pos2, tm2 = (self.world.get_component(m2, Minion),
                              self.world.get_component(m2, Position),
                              self.world.get_component(m2, TileMovement))

        self.ms._walk_toward(m1, minion1, pos1, tm1, 5, 20, "maps/map_1.csv", dt=0.05)
        self.ms._walk_toward(m2, minion2, pos2, tm2, 6, 20, "moba_battleground::bg_test", dt=0.05)

        self.assertTrue(minion1.current_path, "minion1 deveria conseguir pathfind no próprio mapa")
        self.assertTrue(minion2.current_path,
            "minion2 (mapa DIFERENTE) não deveria ser bloqueado pelo orçamento "
            "já gasto por minion1 — orçamento de pathfind é por instância")


if __name__ == "__main__":
    unittest.main(verbosity=2)
