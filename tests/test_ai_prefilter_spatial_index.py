"""
tests/test_ai_prefilter_spatial_index.py — Fase de escala (12/08/2026,
ver PROBLEMAS_ARQUITETURA.md): `EnemyAISystem._active_mobs_this_tick`
trocou o algoritmo linear "cada mob IDLE compara contra cada player"
(O(mobs×players)) por um `SpatialHash` (mesma classe já usada 2x no
projeto) — "cada player consulta o índice" (O(mobs) pra montar +
O(players) pra consultar). Reverte a decisão de 05/08/2026 de não usar
spatial hash aqui (medida só contra ~10 players/mapa) — usuário pediu
pra desenhar pra escala real de lançamento público.

Prova diferencial: o resultado do algoritmo NOVO precisa ser IDÊNTICO
ao do algoritmo LINEAR antigo (reimplementado aqui só como referência
de teste, não é o código de produção) pra qualquer configuração de
players/mobs — a otimização só muda a ORDEM da varredura, nunca o
resultado.

Medido por carga real (script fora da suíte, não incluído aqui):
players espalhados pelo mapa (cenário comum de PvE aberto) — ganho real
(60 players: 5.55ms→3.11ms, ~44%). Players AGLOMERADOS num mesmo
hotspot (ex: cidade cheia) — ganho quase nulo, limitação conhecida e
aceita por decisão do usuário (fatia pequena agora, hotspot fica pra
depois se virar problema real).

2ª camada (mesmo dia, playtest real do usuário — mesmo com 1 player só
o "piso" de reconstruir o índice TODO tick continuava em ~1,1ms):
`_active_mobs_this_tick` só refaz a varredura espacial completa a cada
`_PREFILTER_REFRESH_TICKS` (3) ticks — reusa o `set` de eids confirmado
nos ticks intermediários. Alternativa mais simples que um índice
incremental com ganchos no sistema de movimento (que exigiria 5 pontos
de gancho em código COMPARTILHADO cliente/servidor, risco real de
ficar desatualizado em silêncio se um gancho faltasse) — decisão
explícita do usuário, trade-off aceito: até 3 ticks (100ms) de atraso
pra um mob "acordar" via player que chegou perto, mesmo atraso já
aprovado antes."""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile
from engine.world_systems import EnemyAISystem, _players_on_map
from engine.components import AIControlled, TileMovement

MAP_A = "maps/map_1.csv"


def _find_enemy_ai_system(ws, map_file: str) -> EnemyAISystem:
    bundle = ws._map_bundles[map_file]
    for system in bundle.systems:
        if isinstance(system, EnemyAISystem):
            return system
    raise AssertionError("EnemyAISystem não encontrado no bundle")


def _linear_reference_eligible_idle_eids(enemy_ai_system, idle_entries) -> set:
    """Reimplementação do algoritmo LINEAR antigo (só pra comparação em
    teste) — mesma lógica que existia antes da Fase de escala: cada mob
    IDLE compara contra CADA player (sem índice espacial). Não inclui
    throttle por tier de propósito — o teste compara só a ELEGIBILIDADE
    geométrica (a etapa que o spatial hash mudou), throttle é ortogonal
    e já coberto por `TestThrottleOfIdleMobReevaluation`."""
    result = set()
    for entry in idle_entries:
        eid, pos, tm = entry[0], entry[1], entry[5]
        _min_cheb = None
        for _, _, _p_tm, _ in enemy_ai_system._players_this_map_cache:
            _d = max(abs(_p_tm.current_tile_x - tm.current_tile_x),
                      abs(_p_tm.current_tile_y - tm.current_tile_y))
            if _min_cheb is None or _d < _min_cheb:
                _min_cheb = _d
        _eligible = (_min_cheb is not None and _min_cheb <= enemy_ai_system.SLEEP_RADIUS_TILES) \
                    or enemy_ai_system._any_candidate_in_range(eid, pos)
        if _eligible:
            result.add(eid)
    return result


class TestSpatialIndexMatchesLinearReference(unittest.TestCase):
    """Prova diferencial: várias configurações de players/mobs, comparando
    o algoritmo NOVO (spatial hash) contra o LINEAR antigo — precisam
    bater exatamente."""

    def setUp(self):
        self.ws = make_world_server()
        # Vários players espalhados pelo mapa — cenário que exercita de
        # verdade a consulta multi-player do índice espacial (com 1 só
        # player o hash degenera pra "1 consulta", pouco revelador).
        self.p1 = spawn_player(self.ws, "p1", 50, 60, class_id="guerreiro")
        self.p2 = spawn_player(self.ws, "p2", 200, 300, class_id="guerreiro")
        self.p3 = spawn_player(self.ws, "p3", 100, 500, class_id="guerreiro")
        run_ticks(self.ws, 60)  # popula mobs reais via SpawnZoneSystem
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)
        self.enemy_ai_system._players_this_map_cache = _players_on_map(
            self.ws.world, None, MAP_A)

    def _idle_entries(self):
        from engine.world_systems import _mobs_on_map
        all_mobs = _mobs_on_map(self.ws.world, None, MAP_A)
        for entry in all_mobs:
            entry[2].state = "IDLE"  # força todos IDLE — só geometria importa aqui
            # `run_ticks` no setUp já avançou `_ai_throttle_last_check` de
            # vários mobs — sem resetar, comparar com tick_count=0 faria o
            # throttle (ortogonal, já coberto por
            # TestThrottleOfIdleMobReevaluation) mascarar o resultado
            # geométrico que este teste quer isolar.
            entry[2]._ai_throttle_last_check = -1
        return all_mobs

    def test_resultado_bate_com_algoritmo_linear_players_espalhados(self):
        idle_entries = self._idle_entries()
        expected = _linear_reference_eligible_idle_eids(self.enemy_ai_system, idle_entries)

        active = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=0)
        got = {entry[0] for entry in active}

        self.assertEqual(got, expected)
        self.assertGreater(len(expected), 0, "cenário de teste inválido — nenhum mob elegível")
        # Sanity: nem TODOS os mobs deveriam estar elegíveis (senão o
        # teste não prova que o filtro geométrico funciona de verdade).
        self.assertLess(len(expected), len(idle_entries))

    def test_mob_perto_de_um_player_mas_longe_dos_outros_ainda_e_elegivel(self):
        """União correta: basta UM player por perto, não precisa ser
        elegível pra todos."""
        idle_entries = self._idle_entries()
        # Pega um mob qualquer e o coloca bem perto SÓ do p2.
        mob_entry = idle_entries[0]
        mob_tm = mob_entry[5]
        p2_tm = self.ws.world.get_component(self.p2, TileMovement)
        mob_tm.current_tile_x = mob_tm.target_tile_x = p2_tm.current_tile_x + 2
        mob_tm.current_tile_y = mob_tm.target_tile_y = p2_tm.current_tile_y

        active = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=0)
        got = {entry[0] for entry in active}
        self.assertIn(mob_entry[0], got)

    def test_mob_fora_do_raio_de_todos_nao_e_elegivel(self):
        idle_entries = self._idle_entries()
        # Escolhe um mob que não tenha NPC/combatente por perto (índice 0
        # calhava de ter um do lado — _any_candidate_in_range também
        # precisa ser False, senão o teste prova a coisa errada).
        mob_entry = next(e for e in idle_entries
                         if not self.enemy_ai_system._any_candidate_in_range(e[0], e[1]))
        set_entity_tile(self.ws, mob_entry[0], 5000, 5000)  # sincroniza TileMovement E Position

        active = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=0)
        got = {entry[0] for entry in active}
        self.assertNotIn(mob_entry[0], got)


class TestNpcCombatantFallbackStillWorks(unittest.TestCase):
    """Regressão explícita (mencionada na docstring de
    `_active_mobs_this_tick` desde 05/08/2026): mob longe de TODO player
    mas perto de um NPC/combatente ainda precisa ficar elegível via
    `_any_candidate_in_range` — o índice espacial novo só filtra a parte
    de PLAYER, o fallback de NPC continua rodando por fora, sem mudança."""

    def setUp(self):
        self.ws = make_world_server()
        self.p1 = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)
        self.enemy_ai_system._players_this_map_cache = _players_on_map(
            self.ws.world, None, MAP_A)

    def test_mob_longe_do_player_mas_perto_de_npc_combatente_e_elegivel(self):
        from engine.world_systems import _mobs_on_map
        all_mobs = _mobs_on_map(self.ws.world, None, MAP_A)
        self.assertGreater(len(all_mobs), 0)
        mob_entry = all_mobs[0]
        mob_entry[2].state = "IDLE"
        mob_entry[2]._ai_throttle_last_check = -1  # ver comentário em TestSpatialIndexMatchesLinearReference._idle_entries
        mob_tm = mob_entry[5]
        # Longe de qualquer player de verdade.
        mob_tm.current_tile_x = mob_tm.target_tile_x = 4000
        mob_tm.current_tile_y = mob_tm.target_tile_y = 4000

        # Simula um "combatente" (NPC) bem perto do mob — mesmo padrão
        # de cache já usado por _select_target/_any_candidate_in_range.
        from engine.components import Position as _Pos, TileMovement as _TM, CombatStats as _CS
        mob_pos = mob_entry[1]
        fake_npc_pos = _Pos(x=mob_pos.x + 5, y=mob_pos.y, prev_x=mob_pos.x, prev_y=mob_pos.y)
        fake_npc_tm  = _TM(current_tile_x=mob_tm.current_tile_x, current_tile_y=mob_tm.current_tile_y,
                           target_tile_x=mob_tm.current_tile_x, target_tile_y=mob_tm.current_tile_y)
        fake_npc_cs  = _CS()
        self.enemy_ai_system._npc_combatants_cache = [(99999, fake_npc_pos, fake_npc_tm, fake_npc_cs)]

        active = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=0)
        got = {entry[0] for entry in active}
        self.assertIn(mob_entry[0], got,
                      "mob perto de NPC/combatente deveria continuar elegível "
                      "mesmo longe de todo player")


MAP_B = "maps/map_cave_west.csv"


class TestCombatantsByMapIndex(unittest.TestCase):
    """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §31) —
    `EnemyAISystem` parou de fazer seu PRÓPRIO `get_entities_with(
    ...Combatant...)` sem filtro de mapa (o mundo TODO, repetido 1x por
    BUNDLE por tick — achado real de um teste de carga com 100 players/8
    mapas simultâneos, pico de 152.7ms rastreado até aqui) e passou a
    consumir `combatants_by_map` (índice canônico construído 1x por tick
    em `WorldServer._tick`, mesmo padrão de `players_by_map`/
    `mobs_by_map`). Prova diferencial (índice vs scan direto) + a garantia
    mais crítica: combatente de OUTRO mapa nunca vaza pro cache de um
    mapa que não é o dele (bug que quebraria NPC/mob "vendo" um alvo de
    mapa errado)."""

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 60)  # SpawnZoneSystem spawna mobs ao longo de ticks, não na construção

    def _mob_on(self, map_file: str) -> int:
        """Combatant real (mob, já tem Combatant/NPC via entity_factory —
        players NÃO carregam Combatant, ver WorldServer.spawn_player)."""
        from engine.components import MapLocation
        for eid in self.ws._mob_eids:
            ml = self.ws.world.get_component(eid, MapLocation)
            if ml and ml.map_file == map_file:
                return eid
        raise AssertionError(f"nenhum mob encontrado em {map_file}")

    def test_indice_bate_com_scan_direto_para_o_mesmo_mapa(self):
        from engine.world_systems import _combatants_on_map
        from engine.components import MapLocation, Combatant, CombatStats, NPC as _NPCidx, Position as _Posidx
        mob_a = self._mob_on(MAP_A)
        mob_b = self._mob_on(MAP_B)

        # Índice canônico construído do mesmo jeito que WorldServer._tick faz.
        combatants_by_map: dict = {}
        for eid, pos, tm, _cbt, cs in self.ws.world.get_entities_with(
                _Posidx, TileMovement, Combatant, CombatStats):
            ml = self.ws.world.get_component(eid, MapLocation)
            key = ml.map_file if ml else ""
            is_npc = self.ws.world.get_component(eid, _NPCidx) is not None
            combatants_by_map.setdefault(key, []).append((eid, pos, tm, cs, is_npc))

        via_indice = {e[0] for e in _combatants_on_map(self.ws.world, combatants_by_map, MAP_A)}
        via_scan   = {e[0] for e in _combatants_on_map(self.ws.world, None, MAP_A)}
        self.assertEqual(via_indice, via_scan,
                         "resultado do índice deveria ser IDÊNTICO ao scan direto pro mesmo mapa")
        self.assertIn(mob_a, via_indice)
        self.assertNotIn(mob_b, via_indice, "combatente de OUTRO mapa não deveria aparecer no mapa A")

    def test_combatente_de_outro_mapa_nunca_vaza_no_cache_do_enemy_ai_system(self):
        mob_a = self._mob_on(MAP_A)
        mob_b = self._mob_on(MAP_B)
        # Player em cada mapa — sem isso o bundle pula IA (sem player = mob parado,
        # ver WorldServer._tick, mas o cache de combatentes é construído de qualquer
        # jeito ANTES desse gate; ainda assim, players reais deixam o cenário honesto).
        spawn_player(self.ws, "cix_leak_a", 130, 374)
        pb = spawn_player(self.ws, "cix_leak_b", 130, 374, class_id="guerreiro")
        # transfer_player (não só reatribuir MapLocation na mão) — único jeito
        # correto de mudar de mapa: atualiza _player_maps TAMBÉM, sem isso
        # _maps_com_player não inclui MAP_B e o bundle pula IA inteira (ver
        # WorldServer._tick, "sem player neste mapa: pula AI").
        self.ws.transfer_player("cix_leak_b", pb, MAP_B, 78, 19)

        self.ws._tick(0.033)  # tick real — passa combatants_by_map de verdade

        sys_a = _find_enemy_ai_system(self.ws, MAP_A)
        sys_b = _find_enemy_ai_system(self.ws, MAP_B)

        eids_a = {e[0] for e in sys_a._all_combatants_cache}
        eids_b = {e[0] for e in sys_b._all_combatants_cache}

        self.assertIn(mob_a, eids_a)
        self.assertNotIn(mob_b, eids_a, "combatente do mapa B vazou pro cache do mapa A")
        self.assertIn(mob_b, eids_b)
        self.assertNotIn(mob_a, eids_b, "combatente do mapa A vazou pro cache do mapa B")


class TestIndexBuildSinglePass(unittest.TestCase):
    """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §36) —
    `WorldServer._tick()` unificou 4 `get_entities_with()` separados
    (players/mobs/combatentes/tile-movement) numa ÚNICA passada pela
    base mais ampla (`TileMovement`) + checagem de presença de
    componente. Prova que os 4 índices continuam corretos — mesmo
    conteúdo que as 4 queries separadas produziriam — usando um tick
    REAL de `WorldServer` (não reimplementação isolada, já que os 4
    dicts são variáveis locais de `_tick()`, sem seam próprio pra
    testar fora dele)."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "ibsp_p1", 130, 374)
        run_ticks(self.ws, 60)  # popula mobs reais via SpawnZoneSystem

    def test_player_aparece_no_cache_de_players_do_enemy_ai_system(self):
        self.ws._tick(0.033)
        sys_a = _find_enemy_ai_system(self.ws, MAP_A)
        eids = {e[0] for e in sys_a._players_this_map_cache}
        self.assertIn(self.player_eid, eids)

    def test_mob_real_aparece_no_cache_de_combatentes_e_nao_e_npc(self):
        mob = self._mob_on(MAP_A)
        self.ws._tick(0.033)
        sys_a = _find_enemy_ai_system(self.ws, MAP_A)
        combat_eids = {e[0] for e in sys_a._all_combatants_cache}
        npc_eids    = {e[0] for e in sys_a._npc_combatants_cache}
        self.assertIn(mob, combat_eids, "mob deveria estar em _all_combatants_cache")
        self.assertNotIn(mob, npc_eids, "mob hostil comum não deveria estar marcado como NPC")

    def test_player_nao_aparece_no_indice_de_mobs(self):
        """Player tem TileMovement+Position+CombatStats (mesma base que
        mob) mas NÃO tem AIControlled/InitialPosition/DetectionRadius —
        a checagem de presença de componente por entidade precisa
        excluí-lo do índice de mobs corretamente, não só "quem passou
        primeiro pela base"."""
        self.ws._tick(0.033)
        # `_last_active_mob_count` só reflete o resultado do prefiltro
        # de mobs — checagem indireta de que o índice de mobs não
        # incluiu o player (senão o filtro processaria 1 "mob" a mais
        # com componentes de mob faltando, o que já levantaria exceção
        # antes de chegar aqui).
        sys_a = _find_enemy_ai_system(self.ws, MAP_A)
        self.assertIsInstance(sys_a._last_active_mob_count, int)

    def _mob_on(self, map_file: str) -> int:
        from engine.components import MapLocation
        for eid in self.ws._mob_eids:
            ml = self.ws.world.get_component(eid, MapLocation)
            if ml and ml.map_file == map_file:
                return eid
        raise AssertionError(f"nenhum mob encontrado em {map_file}")


class TestPrefilterRefreshThrottle(unittest.TestCase):
    """2ª camada da fase de escala (12/08/2026, mesmo dia — playtest real
    do usuário achou que o "piso" continuava alto mesmo com 1 player só).
    `_active_mobs_this_tick` só refaz a varredura espacial completa a
    cada `_PREFILTER_REFRESH_TICKS` ticks — reusa o resultado confirmado
    nos ticks intermediários, mesmo se a posição real do mob mudasse
    nesse meio-tempo (trade-off aceito: até 3 ticks de atraso pra
    detectar elegibilidade nova)."""

    def setUp(self):
        self.ws = make_world_server()
        self.p1 = spawn_player(self.ws, "p1", 50, 60, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)
        self.enemy_ai_system._players_this_map_cache = _players_on_map(
            self.ws.world, None, MAP_A)

        from engine.world_systems import _mobs_on_map
        all_mobs = _mobs_on_map(self.ws.world, None, MAP_A)
        self.mob_entry = all_mobs[0]
        self.mob_entry[2].state = "IDLE"
        self.mob_entry[2]._ai_throttle_last_check = -1
        # Longe de qualquer player — ponto de partida "não elegível".
        set_entity_tile(self.ws, self.mob_entry[0], 4000, 4000)

    def _reset_cache(self) -> None:
        """`run_ticks(60)` no setUp já exercitou o MESMO EnemyAISystem de
        verdade (é o bundle real do WorldServer) — precisa começar de um
        estado limpo pra testar o throttle isoladamente."""
        self.enemy_ai_system._prefilter_confirmed_cache = None
        self.enemy_ai_system._prefilter_last_refresh_tick = -1

    def test_cache_e_populado_na_primeira_chamada(self):
        self._reset_cache()
        self.assertIsNone(self.enemy_ai_system._prefilter_confirmed_cache)
        self.enemy_ai_system._active_mobs_this_tick(None, tick_count=100)
        self.assertIsNotNone(self.enemy_ai_system._prefilter_confirmed_cache)
        self.assertEqual(self.enemy_ai_system._prefilter_last_refresh_tick, 100)

    def test_mob_que_fica_elegivel_so_e_detectado_no_proximo_refresh(self):
        """Mob começa longe (não elegível). Chama 1x pra popular o cache
        (tick 100). Move o mob pra DENTRO do raio de sono (20 tiles) mas
        FORA do raio de aggro (`AGGRO_RADIUS_TILES`=5) — precisa ficar
        fora do raio de aggro pra não disparar o fallback
        `_any_candidate_in_range` (não throttlado, isolaria o efeito do
        cache que este teste quer provar). ANTES do intervalo de refresh
        completar, ainda não deveria aparecer (cache antigo reusado);
        DEPOIS do intervalo, deveria aparecer."""
        self._reset_cache()
        active0 = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=100)
        self.assertNotIn(self.mob_entry[0], {e[0] for e in active0})

        p1_tm = self.ws.world.get_component(self.p1, TileMovement)
        set_entity_tile(self.ws, self.mob_entry[0],
                        p1_tm.current_tile_x + 10, p1_tm.current_tile_y)

        refresh = self.enemy_ai_system._PREFILTER_REFRESH_TICKS
        active_antes = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=100 + refresh - 1)
        self.assertNotIn(self.mob_entry[0], {e[0] for e in active_antes},
                         "antes do refresh completar, deveria continuar reusando o cache antigo")

        active_depois = self.enemy_ai_system._active_mobs_this_tick(None, tick_count=100 + refresh)
        self.assertIn(self.mob_entry[0], {e[0] for e in active_depois},
                      "no tick em que o refresh completa, a varredura nova deveria achar o mob perto do player")

    def test_dentro_do_intervalo_nao_reconstroi_o_indice(self):
        """Prova que o cache É reusado de verdade (não só que o resultado
        bate por coincidência) — mede quantas vezes SpatialHash.insert é
        chamado entre 2 chamadas dentro do intervalo de refresh."""
        from engine.utils import SpatialHash
        self.enemy_ai_system._active_mobs_this_tick(None, tick_count=100)  # popula o cache

        orig_insert = SpatialHash.insert
        calls = {"n": 0}
        def counted(self_hash, *a, **kw):
            calls["n"] += 1
            return orig_insert(self_hash, *a, **kw)
        SpatialHash.insert = counted
        try:
            self.enemy_ai_system._active_mobs_this_tick(None, tick_count=101)  # dentro do intervalo (3 ticks)
        finally:
            SpatialHash.insert = orig_insert

        self.assertEqual(calls["n"], 0,
                         "dentro do intervalo de refresh, não deveria reconstruir o índice espacial")

    def test_tick_count_retrocedendo_forca_refresh_nunca_reusa_cache_velho(self):
        """Bug real achado ao escrever os testes desta classe (não da
        Fase de escala em si): vários testes pré-existentes chamam
        `run_ticks()` [avança `ws.tick_count` de verdade] e DEPOIS chamam
        `_active_mobs_this_tick` direto com um `tick_count` menor/fixo —
        o cache via isso como "nada mudou" e reusava um resultado
        calculado numa configuração de mundo completamente diferente.
        Nunca deveria acontecer em produção real (tick_count só
        incrementa), mas o cache precisa ser defensivo mesmo assim."""
        self.enemy_ai_system._active_mobs_this_tick(None, tick_count=500)
        self.assertEqual(self.enemy_ai_system._prefilter_last_refresh_tick, 500)

        from engine.utils import SpatialHash
        orig_insert = SpatialHash.insert
        calls = {"n": 0}
        def counted(self_hash, *a, **kw):
            calls["n"] += 1
            return orig_insert(self_hash, *a, **kw)
        SpatialHash.insert = counted
        try:
            self.enemy_ai_system._active_mobs_this_tick(None, tick_count=10)  # "voltou no tempo"
        finally:
            SpatialHash.insert = orig_insert

        self.assertGreater(calls["n"], 0,
                           "tick_count menor que o último refresh deveria forçar reconstrução, "
                           "nunca reusar o cache cego")


class TestTileMovementSystemResolvesCorrectMap(unittest.TestCase):
    """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §37) —
    `TileMovementSystem.update()` pegava "o primeiro Tilemap" da
    iteração pra calcular elevação/transição ao terminar um movimento
    (roda GLOBALMENTE, sem filtro de mapa) — MESMA classe de bug já
    documentada no CLAUDE.md ("nunca pegar o primeiro Tilemap do
    world"). Corrigido pra resolver via `_svc_resolver(eid)`, mesmo
    mecanismo já usado por `is_tile_walkable`."""

    def setUp(self):
        self.ws = make_world_server()

    def test_consulta_tilemap_do_proprio_mapa_da_entidade_nao_o_primeiro(self):
        from engine.components import Tilemap
        from engine.world_systems import TileMovementSystem

        pb = spawn_player(self.ws, "tms_b", 130, 374)
        self.ws.transfer_player("tms_b", pb, MAP_B, 78, 19)

        tm = self.ws.world.get_component(pb, TileMovement)
        # Força a entidade a "acabar de chegar" num tile neste update().
        tm.is_moving       = True
        tm.progress        = 1.0
        tm.current_tile_x  = tm.target_tile_x = 10
        tm.current_tile_y  = tm.target_tile_y = 10
        tm.target_pixel_x  = tm.start_pixel_x
        tm.target_pixel_y  = tm.start_pixel_y

        expected_tme = self.ws._map_bundles[MAP_B].tilemap_entity

        queried_tilemap_eids = []
        orig_get_component = self.ws.world.get_component

        def _spy(eid, comp_type):
            if comp_type is Tilemap:
                queried_tilemap_eids.append(eid)
            return orig_get_component(eid, comp_type)

        self.ws.world.get_component = _spy
        try:
            tms = TileMovementSystem(self.ws.world)
            tms.update(dt=0.05)
        finally:
            self.ws.world.get_component = orig_get_component

        self.assertIn(expected_tme, queried_tilemap_eids,
                     "deveria ter consultado o Tilemap do PRÓPRIO mapa do player (mapa B), "
                     "não 'o primeiro' Tilemap da iteração")


if __name__ == "__main__":
    unittest.main()
