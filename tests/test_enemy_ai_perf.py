"""
tests/test_enemy_ai_perf.py — EnemyAISystem custava ~7-8ms/tick mesmo com
1 player sozinho no mundo aberto (05/08/2026, log real de perf analisado
com o usuário). Achados (discussão de estratégia de escala — ver
ARQUITETURA_ONLINE.md):

1. `_select_target()` (2 scans: players do mapa + outros combatentes)
   rodava INCONDICIONALMENTE pra todo mob acordado, mesmo um já
   `ATTACKING`/`CHASING` com alvo retido e válido — a retenção só
   sobrescrevia o resultado DEPOIS, jogando o scan fora. Fix: reordena a
   validação de retenção pra ANTES, só chama `_select_target` quando ela
   falha.
2. Sleep-check (mob IDLE decidindo se continua dormindo) e
   `_select_target` faziam `get_entities_with(...PlayerControlled...)`
   por MOB, todo tick, sem cache. Fix: índice canônico
   `players_by_map`, construído 1x por tick em `WorldServer._tick()`,
   injetado em `EnemyAISystem`/`EnemyAbilitySystem`/`SpawnZoneSystem`.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import (make_world_server, spawn_player, run_ticks, first_mob,
                           teleport_mob_to_player, set_entity_tile)
from engine.world_systems import EnemyAISystem
from engine.components import AIControlled, CombatStats, Position, TileMovement, MapLocation
from engine.utils import snap_to_tile

MAP_A = "maps/map_1.csv"


def _find_enemy_ai_system(ws, map_file: str) -> EnemyAISystem:
    bundle = ws._map_bundles[map_file]
    for system in bundle.systems:
        if isinstance(system, EnemyAISystem):
            return system
    raise AssertionError("EnemyAISystem não encontrado no bundle")


class TestSelectTargetSkippedWhenTargetRetained(unittest.TestCase):
    """Achado 1: mob já engajado com alvo válido não deveria rechamar
    _select_target (2 scans jogados fora) — só quando perde o alvo."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")

        # Engaja o mob no player, adjacente, com alvo já retido/válido.
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        mob_tm  = self.ws.world.get_component(self.mob_eid, TileMovement)
        # Teleporta o player pro lado do mob (adjacente) e trava o combate.
        # snap_to_tile (não escrita direta de current_tile_x/y — regra do
        # projeto): também sincroniza Position (pixel), da qual o pré-filtro
        # `_any_candidate_in_range` (05/08/2026) depende pra matemática de
        # distância — escrever só TileMovement deixava Position velha/longe,
        # fazendo o pré-filtro (corretamente) descartar o candidato.
        snap_to_tile(self.ws.world, self.player_eid, mob_tm.current_tile_x + 1, mob_tm.current_tile_y)
        ai.state = "ATTACKING"
        ai.target_eid = self.player_eid
        ai.aggroed_by_damage = True

        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

    def _count_select_target_calls(self) -> dict:
        """Conta só chamadas PRO MOB DO TESTE — outros ~190 mobs do mapa
        aberto podem legitimamente chamar _select_target (procurando
        alvo pela primeira vez), isso não é o que está sendo provado
        aqui."""
        calls = {"n": 0}
        orig = self.enemy_ai_system._select_target
        def spy(mob_eid):
            if mob_eid == self.mob_eid:
                calls["n"] += 1
            return orig(mob_eid)
        self.enemy_ai_system._select_target = spy
        return calls

    def test_alvo_retido_nao_rechama_select_target(self):
        calls = self._count_select_target_calls()
        run_ticks(self.ws, 5)
        self.assertEqual(calls["n"], 0,
            "_select_target não deveria ser chamado enquanto o mob retém um alvo válido")

    def test_perder_o_alvo_volta_a_chamar_select_target(self):
        # Mata o player (target fica inválido) — a retenção deve falhar
        # e o mob volta a chamar _select_target pra procurar de novo.
        cs = self.ws.world.get_component(self.player_eid, CombatStats)
        cs.current_hp = 0
        calls = self._count_select_target_calls()
        run_ticks(self.ws, 3)
        self.assertGreater(calls["n"], 0,
            "sem alvo válido, o mob deveria voltar a chamar _select_target")


class TestPlayersByMapIndexIsActuallyUsed(unittest.TestCase):
    """Achado 2: EnemyAISystem não deveria mais escanear players sozinho
    quando o índice canônico é injetado — prova: um player "fantasma"
    (entidade real no world, mas SEM `PlayerControlled` — nunca apareceria
    num `get_entities_with(...PlayerControlled...)` de verdade) precisa
    ser o suficiente pra `_select_target` adquirir ele como alvo. Se o
    sistema estivesse ignorando o índice injetado e escaneando sozinho, o
    fantasma jamais seria encontrado (falta o componente que o scan real
    exige)."""

    def setUp(self):
        from engine.components import TrainingDummy, NPC, Tower, Faction
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        # first_mob() não garante mapa — precisa ser um mob de MAP_A
        # especificamente (é o bundle/EnemyAISystem que este teste usa).
        self.mob_eid = None
        for eid in self.ws._mob_eids:
            if (self.ws.world.get_component(eid, TrainingDummy) is None
                    and self.ws.world.get_component(eid, NPC) is None
                    and self.ws.world.get_component(eid, Tower) is None):
                ml = self.ws.world.get_component(eid, MapLocation)
                if ml is not None and ml.map_file == MAP_A:
                    self.mob_eid = eid
                    break
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real em MAP_A")

        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "IDLE"
        ai.target_eid = -1  # sem retenção — força _select_target a rodar

        # Entidade REAL no world (pra Faction/CombatStats resolverem
        # normal), na MESMA posição do mob (bem dentro do raio de
        # aggro), com a facção de player ("jogadores" — hostil a mob
        # comum) — mas SEM PlayerControlled, então nunca aparece num
        # scan real de `get_entities_with(...PlayerControlled...)`.
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        self.ghost_eid = self.ws.world.create_entity()
        self.ws.world.add_component(self.ghost_eid, Position(
            mob_tm.current_tile_x * 32, mob_tm.current_tile_y * 32))
        self.ws.world.add_component(self.ghost_eid, TileMovement(
            current_tile_x=mob_tm.current_tile_x, current_tile_y=mob_tm.current_tile_y))
        _ghost_cs = CombatStats()
        _ghost_cs.current_hp = 100
        _ghost_cs.max_hp = 100
        self.ws.world.add_component(self.ghost_eid, _ghost_cs)
        self.ws.world.add_component(self.ghost_eid, Faction(faction_id="jogadores"))
        self.ws.world.add_component(self.ghost_eid, MapLocation(MAP_A))
        self.fake_index = {MAP_A: [(self.ghost_eid,
                                    self.ws.world.get_component(self.ghost_eid, Position),
                                    self.ws.world.get_component(self.ghost_eid, TileMovement),
                                    _ghost_cs)]}

    def test_indice_manual_com_fantasma_e_adquirido_como_alvo(self):
        self.enemy_ai_system.update(dt=0.05, players_by_map=self.fake_index)
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai.target_eid, self.ghost_eid,
            "mob deveria ter adquirido o fantasma do índice injetado como alvo — "
            "se o sistema tivesse escaneado sozinho (ignorando o índice), o fantasma "
            "(sem PlayerControlled) nunca apareceria e o alvo continuaria -1")


class TestMobsByMapIndexIsActuallyUsed(unittest.TestCase):
    """Achado secundário do §34.74.38: `EnemyAISystem.update()` buscava
    `get_entities_with(...)` SEM filtro de mapa (mundo TODO) — fix injeta
    `mobs_by_map` (mesmo padrão de `players_by_map`). Prova: um mob
    "fantasma" (entidade real no world, mas SEM `InitialPosition`/
    `DetectionRadius` — nunca apareceria no `get_entities_with(Position,
    AIControlled, InitialPosition, DetectionRadius, TileMovement,
    CombatStats)` real) precisa ser processado (adquirir o player como
    alvo) quando presente SÓ no índice injetado — prova que o sistema lê
    do índice em vez de escanear sozinho."""

    def setUp(self):
        from engine.components import InitialPosition, DetectionRadius
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        # Fantasma: entidade real (Position/TileMovement/CombatStats/
        # AIControlled/MapLocation), mas SEM InitialPosition/DetectionRadius
        # — 2 dos 6 componentes exigidos pelo scan real de EnemyAISystem.
        # Adjacente ao player (dentro do raio real de aquisição).
        player_tm = self.ws.world.get_component(self.player_eid, TileMovement)
        self.ghost_eid = self.ws.world.create_entity()
        _ghost_tx, _ghost_ty = player_tm.current_tile_x + 1, player_tm.current_tile_y
        _ghost_pos = Position(_ghost_tx * 32, _ghost_ty * 32)
        self.ws.world.add_component(self.ghost_eid, _ghost_pos)
        _ghost_tm = TileMovement(current_tile_x=_ghost_tx, current_tile_y=_ghost_ty)
        self.ws.world.add_component(self.ghost_eid, _ghost_tm)
        _ghost_cs = CombatStats()
        _ghost_cs.current_hp = 100
        _ghost_cs.max_hp = 100
        self.ws.world.add_component(self.ghost_eid, _ghost_cs)
        _ghost_ai = AIControlled(state="IDLE", target_eid=-1)
        self.ws.world.add_component(self.ghost_eid, _ghost_ai)
        self.ws.world.add_component(self.ghost_eid, MapLocation(MAP_A))
        # Faction "monstros_hostis" (hostil a "jogadores", ver
        # content/faction_data.py) — sem isso, `is_hostile()` resolve o
        # ghost como "sem facção" = nunca hostil, e _select_target nunca
        # aceitaria o player como candidato (regra do projeto).
        from engine.components import Faction
        self.ws.world.add_component(self.ghost_eid, Faction(faction_id="monstros_hostis"))
        # InitialPosition/DetectionRadius NUNCA viram componente do ghost —
        # só valores soltos, usados apenas pra montar a tupla do índice.
        self.fake_mobs_index = {MAP_A: [(self.ghost_eid, _ghost_pos, _ghost_ai,
                                          InitialPosition(_ghost_pos.x, _ghost_pos.y),
                                          DetectionRadius(160.0), _ghost_tm, _ghost_cs)]}

    def test_indice_manual_com_mob_fantasma_e_processado(self):
        self.enemy_ai_system.update(dt=0.05, mobs_by_map=self.fake_mobs_index)
        ai = self.ws.world.get_component(self.ghost_eid, AIControlled)
        self.assertEqual(ai.target_eid, self.player_eid,
            "mob fantasma (sem InitialPosition/DetectionRadius — nunca apareceria "
            "no scan real) deveria ter sido processado a partir do índice injetado "
            "e adquirido o player como alvo")


class TestSelectTargetSkippedWhenNoCandidateInRange(unittest.TestCase):
    """Achado 3 (05/08/2026, observação do usuário): os ~190 mobs do mundo
    estão espalhados por 11 zonas de spawn de map_1 (ver map_1_entities.json
    — zonas a 400+ tiles de distância umas das outras), e o sleep-check de
    `update()` (SLEEP_RADIUS_TILES=40) só roda DEPOIS do branch "sem alvo
    válido" já ter dado `continue` — nunca alcançado por um mob que já não
    tinha alvo. Resultado real medido: `mobs_ativos=0` no log de perf (log
    real gerado pelo usuário), mas `sys:EnemyAISystem` custando 8-16ms mesmo
    assim — TODO mob IDLE sem alvo chamava `_select_target` (is_hostile +
    CombatState por candidato) todo tick, mesmo mobs isolados a centenas de
    tiles de qualquer player. Fix: `_any_candidate_in_range` (só matemática
    de posição, mesmo raio que `_select_target` usa de verdade) evita a
    chamada cara quando ninguém (player OU NPC) está dentro do raio real de
    aquisição."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        # Isola o mob: longe de qualquer player OU NPC/combatente (bem além
        # do raio real de aquisição, AGGRO_RADIUS_TILES=5) — o cenário
        # "mob numa zona de spawn distante, sem ninguém por perto" real.
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "IDLE"
        ai.target_eid = -1
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        mob_pos = self.ws.world.get_component(self.mob_eid, Position)
        mob_tm.current_tile_x = mob_tm.target_tile_x = 5000
        mob_tm.current_tile_y = mob_tm.target_tile_y = 5000
        mob_pos.x = 5000 * 32
        mob_pos.y = 5000 * 32

    def _count_select_target_calls(self) -> dict:
        calls = {"n": 0}
        orig = self.enemy_ai_system._select_target
        def spy(mob_eid):
            if mob_eid == self.mob_eid:
                calls["n"] += 1
            return orig(mob_eid)
        self.enemy_ai_system._select_target = spy
        return calls

    def test_mob_isolado_nao_chama_select_target(self):
        calls = self._count_select_target_calls()
        self.enemy_ai_system.update(dt=0.05)
        self.assertEqual(calls["n"], 0,
            "mob sem NINGUÉM (player ou NPC) dentro do raio real de aquisição "
            "não deveria chamar _select_target — a chamada real sempre devolveria "
            "-1 mesmo, então isso é economia pura, não mudança de comportamento")

    def test_player_proximo_ainda_chama_e_adquire(self):
        # Aproxima o player do mob isolado (dentro do raio real de aquisição)
        # — prova que o pré-filtro não quebra aquisição legítima. snap_to_tile
        # sincroniza Position também (não só TileMovement) — necessário pra
        # matemática de distância do pré-filtro achar o candidato.
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        snap_to_tile(self.ws.world, self.player_eid, mob_tm.current_tile_x + 1, mob_tm.current_tile_y)
        calls = self._count_select_target_calls()
        self.enemy_ai_system.update(dt=0.05)
        self.assertGreater(calls["n"], 0,
            "mob com player dentro do raio real de aquisição precisa continuar "
            "chamando _select_target normalmente")
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai.target_eid, self.player_eid,
            "e precisa ter adquirido o player como alvo, exatamente como antes do pré-filtro")


class TestReturningReusesOccupiedTilesInsteadOfRescanning(unittest.TestCase):
    """Achado 4 (05/08/2026, log real: pico isolado de 162ms concentrado em
    sys:EnemyAISystem, coincidindo com uma queda de 191→180 mobs — leva de
    leash/perda de alvo simultânea). `_get_occupied_tiles()` (sem
    `mobs_by_map`/`players_by_map` — escaneia `get_entities_with
    (TileMovement)` SEM filtro de mapa, MUNDO TODO) era rechamada UMA VEZ
    POR MOB nos 3 branches de recálculo de caminho (RETURNING x2, kite) —
    com vários mobs recalculando no mesmo tick, cada um pagava um rescan
    global inteiro. Fix: reaproveita `all_occupied_tiles` (já calculado 1x
    no topo de `update()`) via `_tile_of()`, nunca mais rechama
    `_get_occupied_tiles` por mob."""

    def setUp(self):
        from engine.components import TrainingDummy, NPC, Tower, InitialPosition
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = None
        for eid in self.ws._mob_eids:
            if (self.ws.world.get_component(eid, TrainingDummy) is None
                    and self.ws.world.get_component(eid, NPC) is None
                    and self.ws.world.get_component(eid, Tower) is None):
                ml = self.ws.world.get_component(eid, MapLocation)
                if ml is not None and ml.map_file == MAP_A:
                    self.mob_eid = eid
                    break
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real em MAP_A")
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        # Isola o mob (longe de qualquer player/NPC — mesmo padrão da classe
        # de teste do Achado 3) e longe da própria InitialPosition, força
        # "acabou de perder o alvo, precisa recalcular caminho de volta pro
        # spawn" — o branch que chamava `_get_occupied_tiles` por mob.
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        mob_pos = self.ws.world.get_component(self.mob_eid, Position)
        mob_tm.current_tile_x = mob_tm.target_tile_x = 3000
        mob_tm.current_tile_y = mob_tm.target_tile_y = 3000
        mob_pos.x = 3000 * 32
        mob_pos.y = 3000 * 32
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "CHASING"          # estava em combate...
        ai.target_eid = -1            # ...mas perdeu o alvo
        ai.target_lost_timer = 1.0    # grace (0.6s) já expirou
        ai.aggroed_by_damage = False

    def _count_get_occupied_tiles_calls(self) -> dict:
        calls = {"n": 0}
        orig = self.enemy_ai_system._get_occupied_tiles
        def spy(*args, **kwargs):
            calls["n"] += 1
            return orig(*args, **kwargs)
        self.enemy_ai_system._get_occupied_tiles = spy
        return calls

    def test_mob_recalculando_retorno_nao_rechama_get_occupied_tiles(self):
        calls = self._count_get_occupied_tiles_calls()
        self.enemy_ai_system.update(dt=0.05)
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai.state, "RETURNING",
            "setup não engatou o branch de recálculo de retorno — teste inválido")
        self.assertEqual(calls["n"], 1,
            "só a chamada do topo de update() (all_occupied_tiles, 1x por tick) "
            "deveria acontecer — o branch de RETURNING não deveria rechamar "
            "_get_occupied_tiles por conta própria (reaproveita all_occupied_tiles)")


class TestIdleMobFarFromPlayerNeverEntersMainLoop(unittest.TestCase):
    """Achado 5 (05/08/2026, observação do usuário): mesmo depois dos
    achados 1-4, o loop principal de `EnemyAISystem.update()` ainda
    ITERAVA todo mob do mapa (~130 em map_1) todo tick — o sleep-check
    (`SLEEP_RADIUS_TILES=40`) só descartava os distantes DEPOIS de cada
    um já ter passado por checagem de morto, retenção, etc. Fix:
    `_active_mobs_this_tick` aplica o MESMO raio/critério ANTES do loop
    (mais o candidato NPC/combatente via `_any_candidate_in_range` — sem
    isso, mob-vs-NPC longe de todo player quebrava, ver
    tests/test_faction.py::TestMultiTargetCombat, regressão real pega na
    validação deste fix) — um mob IDLE fora do raio de todo player E sem
    NPC/combatente por perto nem entra mais no corpo do loop principal.

    Prova: verifica DIRETO a lista devolvida por `_active_mobs_this_tick`
    (não conta chamadas de função auxiliar — `_any_candidate_in_range` é
    usada tanto pelo pré-filtro quanto pelo corpo do loop, então contar
    chamadas não distingue "filtrado antes" de "entrou e foi rejeitado
    dentro"; a lista em si é o sinal inequívoco)."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "IDLE"
        ai.target_eid = -1
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        mob_pos = self.ws.world.get_component(self.mob_eid, Position)
        mob_tm.current_tile_x = mob_tm.target_tile_x = 5000
        mob_tm.current_tile_y = mob_tm.target_tile_y = 5000
        mob_pos.x = 5000 * 32
        mob_pos.y = 5000 * 32
        # `_players_this_map_cache` normalmente é montado no topo de
        # `update()`; aqui precisa existir ANTES de chamar o método isolado
        # (teste inspeciona só o pré-filtro, sem rodar `update()` inteiro).
        # `_npc_combatants_cache`/`_all_combatants_cache` já existem na
        # instância — populados pelas 60 chamadas reais a `update()` no
        # `run_ticks` acima.
        from engine.world_systems import _players_on_map
        self.enemy_ai_system._players_this_map_cache = _players_on_map(
            self.ws.world, None, MAP_A)

    def test_mob_idle_isolado_nunca_entra_no_corpo_do_loop(self):
        active = self.enemy_ai_system._active_mobs_this_tick(None)
        active_eids = {entry[0] for entry in active}
        self.assertNotIn(self.mob_eid, active_eids,
            "mob IDLE sem player OU NPC/combatente dentro do raio não deveria "
            "aparecer na lista de mobs ativos deste tick")

    def test_mob_nao_idle_isolado_ainda_e_processado(self):
        # Mob em CHASING (trabalho pendente: leash/retorno) precisa
        # continuar entrando no loop mesmo isolado — não é sobre "está
        # perto", é sobre "tem trabalho pendente".
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "CHASING"
        active = self.enemy_ai_system._active_mobs_this_tick(None)
        active_eids = {entry[0] for entry in active}
        self.assertIn(self.mob_eid, active_eids,
            "mob CHASING (não-IDLE) precisa continuar na lista mesmo isolado "
            "— senão quebra leash/retorno pro spawn")


class TestThrottleOfIdleMobReevaluation(unittest.TestCase):
    """Fase 2 (05/08/2026): mob IDLE elegível (dentro do raio) ainda passa
    por throttle por tier antes de entrar na lista de mobs ativos —
    `EnemyAISystem._THROTTLE_INTERVAL_BY_TIER`. Nunca afeta mob não-IDLE
    (CHASING/ATTACKING/RETURNING/etc. sempre full-rate)."""

    def setUp(self):
        from engine.components import EnemyTier
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        # Mob IDLE, adjacente ao player (bem dentro do raio) — elegível
        # todo tick pelo Achado 5; tier fixado em "normal" (intervalo=12)
        # pra previsibilidade, independente do tier real que o spawn deu.
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "IDLE"
        ai.target_eid = -1
        ai._ai_throttle_last_check = -1
        player_tm = self.ws.world.get_component(self.player_eid, TileMovement)
        snap_to_tile(self.ws.world, self.mob_eid, player_tm.current_tile_x + 1, player_tm.current_tile_y)
        tier = self.ws.world.get_component(self.mob_eid, EnemyTier)
        if tier is None:
            tier = EnemyTier()
            self.ws.world.add_component(self.mob_eid, tier)
        tier.tier = "normal"
        self.interval = self.enemy_ai_system._THROTTLE_INTERVAL_BY_TIER["normal"]

    def _is_active(self, tick_count: int) -> bool:
        active = self.enemy_ai_system._active_mobs_this_tick(None, tick_count)
        return self.mob_eid in {entry[0] for entry in active}

    def test_mob_recem_elegivel_entra_no_primeiro_tick(self):
        # _ai_throttle_last_check == -1 (nunca checado) sempre passa,
        # independente do tick_count.
        self.assertTrue(self._is_active(500),
            "mob IDLE recém-elegível (nunca checado) deveria entrar no "
            "primeiro tick em que aparece, não esperar o intervalo do tier")

    def test_mob_nao_entra_de_novo_antes_do_intervalo(self):
        self._is_active(500)  # 1ª checagem, marca _ai_throttle_last_check=500
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai._ai_throttle_last_check, 500)
        self.assertFalse(self._is_active(500 + self.interval - 1),
            f"mob normal (intervalo={self.interval}) não deveria ser "
            "reavaliado antes do intervalo completo")

    def test_mob_entra_de_novo_apos_o_intervalo(self):
        self._is_active(500)  # 1ª checagem
        self.assertTrue(self._is_active(500 + self.interval),
            "mob normal deveria voltar a ser reavaliado exatamente "
            "no tick em que o intervalo completa")

    def test_boss_nunca_throttla(self):
        from engine.components import EnemyTier
        tier = self.ws.world.get_component(self.mob_eid, EnemyTier)
        tier.tier = "boss"
        self.assertTrue(self._is_active(500))
        self.assertTrue(self._is_active(501),
            "boss (intervalo=1) deveria ser reavaliado TODO tick, nunca throttlado")

    def test_mob_nao_idle_nunca_e_throttlado(self):
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "CHASING"
        ai._ai_throttle_last_check = 500  # "acabou de ser checado"
        # Mesmo com _ai_throttle_last_check recente, mob não-IDLE tem que
        # aparecer TODO tick — throttle nunca se aplica a ele.
        self.assertTrue(self._is_active(501))
        self.assertTrue(self._is_active(502))
        self.assertTrue(self._is_active(503))


class TestThrottleOfChaseRecalcOnTargetMove(unittest.TestCase):
    """Fase 3 (06/08/2026): dentro do bloco de perseguição, o gatilho "alvo
    mudou de tile" de should_recalculate_path (busca de tile de ataque —
    loop O((attack_range+1)²) — mais chamada A* via _find_path_budgeted)
    disparava recálculo IMEDIATO toda vez que o alvo mudava de tile,
    ignorando o cooldown normal de path_recalc_timer (0.8s) quase todo
    tick enquanto o alvo está em movimento. Com muitos mobs perseguindo
    simultaneamente (cenário de PC — vários minions convergindo pro mesmo
    player), isso rodava TODO tick pra cada um, não 1x a cada 800ms como o
    timer normal sugere. `_CHASE_RECALC_MIN_TICKS_BY_TIER` limita a
    FREQUÊNCIA desse gatilho específico por tier; os outros 3 gatilhos de
    should_recalculate_path (path None/vazio/is_blocked) continuam
    imediatos, nunca throttlados — cobertos no 3º teste abaixo."""

    def setUp(self):
        from engine.components import EnemyTier, DetectionRadius
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")
        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

        # Mob CHASING, 5 tiles do player (fora do attack_range_tiles=1) —
        # precisa passar pelo bloco de recálculo de path, não pela branch
        # ATTACKING (que dá `continue` antes de chegar lá). is_ranged=False
        # força chase melee determinístico (sem kiting, outro branch com
        # `continue` próprio que também bypassaria o bloco sob teste).
        # teleport_mob_to_player realinha InitialPosition (âncora do leash)
        # pro tile novo — sem isso o leash dispararia e sobrescreveria o
        # CHASING setado abaixo.
        teleport_mob_to_player(self.ws, self.mob_eid, self.player_eid, offset_x=5)
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "CHASING"
        ai.target_eid = self.player_eid
        ai.aggroed_by_damage = True
        ai.is_ranged = False
        ai.attack_range_tiles = 1
        ai.last_known_player_tile = (-1, -1)
        ai._chase_recalc_last_tick = -1

        # DetectionRadius bem generoso (achado real de depuração: com o
        # valor padrão do mob, "desistir de perseguir" — dist_to_player >
        # detect_radius, engine/world_systems.py ~linha 3494 — disparava
        # sozinho no meio do teste, já que este teste afasta o player
        # deliberadamente pra nunca entrar em attack_range; isso reseta
        # state/aggroed_by_damage pra IDLE por um motivo TOTALMENTE
        # alheio ao throttle sob teste). Content real nunca afasta o
        # alvo tão rápido sem o mob também se mover — aqui o mob fica
        # parado de propósito (ver _replan_ticks), então o raio precisa
        # ser generoso pra isolar só o mecanismo sendo provado.
        dr = self.ws.world.get_component(self.mob_eid, DetectionRadius)
        if dr:
            dr.radius = 2000.0

        tier = self.ws.world.get_component(self.mob_eid, EnemyTier)
        if tier is None:
            tier = EnemyTier()
            self.ws.world.add_component(self.mob_eid, tier)
        tier.tier = "normal"
        self.interval = self.enemy_ai_system._CHASE_RECALC_MIN_TICKS_BY_TIER["normal"]

        self.player_tm = self.ws.world.get_component(self.player_eid, TileMovement)
        self.mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)

    def _replan_ticks(self, n: int) -> list:
        """Chama update() direto n vezes, teleportando o alvo (player) pra
        um tile novo ANTES de cada uma — garante last_known_player_tile !=
        player_tile_now TODO tick (o gatilho sob teste). Entre chamadas,
        neutraliza qualquer efeito colateral do "andar 1 passo" da chamada
        anterior (path consumido, is_blocked, path_recalc_timer, is_moving)
        — o CONTEÚDO do path é irrelevante pro que está sendo provado aqui
        (só a FREQUÊNCIA do recálculo), então não depende de pathfinding
        real ter sucesso. Devolve os tick_count em que _chase_recalc_last_tick
        realmente mudou (o corpo pesado de recálculo rodou de verdade)."""
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        replans = []
        base_x = self.player_tm.current_tile_x
        for i in range(n):
            set_entity_tile(self.ws, self.player_eid, base_x - i - 1, self.player_tm.current_tile_y)
            ai.path = [(9999, 9999)]
            ai.is_blocked = False
            ai.path_recalc_timer = 999.0
            self.mob_tm.is_moving = False
            before = ai._chase_recalc_last_tick
            self.enemy_ai_system.update(dt=0.05, tick_count=1000 + i)
            if ai._chase_recalc_last_tick != before:
                replans.append(1000 + i)
        return replans

    def test_normal_throttla_recalculo_por_mudanca_de_tile(self):
        replans = self._replan_ticks(self.interval * 2 + 1)
        self.assertGreaterEqual(len(replans), 2,
            "precisa ver pelo menos 2 replans nesta janela pra provar o gap")
        gaps = [b - a for a, b in zip(replans, replans[1:])]
        for gap in gaps:
            self.assertGreaterEqual(gap, self.interval,
                f"mob normal (intervalo={self.interval}) não deveria replanejar "
                f"de novo antes do intervalo completo (gap real={gap})")

    def test_boss_nunca_throttla_recalculo(self):
        from engine.components import EnemyTier
        tier = self.ws.world.get_component(self.mob_eid, EnemyTier)
        tier.tier = "boss"
        replans = self._replan_ticks(5)
        self.assertEqual(len(replans), 5,
            "boss (intervalo=1) deveria replanejar TODO tick, nunca throttlado")

    def test_path_vazio_ignora_throttle_de_mudanca_de_tile(self):
        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.path = [(9999, 9999)]
        ai.is_blocked = False
        ai.path_recalc_timer = 999.0
        self.mob_tm.is_moving = False
        self.enemy_ai_system.update(dt=0.05, tick_count=2000)
        ai._chase_recalc_last_tick = 2000  # "acabou de replanejar agora mesmo"
        ai.path = []  # esgotado — gatilho SEMPRE imediato, nunca lê a tabela de tier
        ai.is_blocked = False
        ai.path_recalc_timer = 999.0
        self.mob_tm.is_moving = False
        # Alvo permanece NO MESMO tile (sem novo set_entity_tile) — só o
        # path vazio deve bastar pra forçar o recálculo.
        self.enemy_ai_system.update(dt=0.05, tick_count=2001)
        self.assertEqual(ai._chase_recalc_last_tick, 2001,
            "path vazio deveria forçar recálculo imediato mesmo dentro da janela "
            "de throttle — o gatilho de mudança de tile nem chega a ser avaliado "
            "aqui (alvo não mudou de tile), então só path vazio explica o recálculo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
