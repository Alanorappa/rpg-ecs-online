"""
tests/test_minions.py
Sistema de Minions — lane creeps estilo MOBA (30/07/2026, pedido do
usuário): nasce na base do time em waves, anda direto rumo ao destino
final da lane (`route[-1]`, pathfinding pré-calculado, sem checkpoint
intermediário desde 04/08/2026), enfrenta qualquer hostil (torre/NPC/
player) que entrar no raio de aggro, e ao perder o alvo retoma o mesmo
destino de onde já está — nunca volta pro spawn. XP flui pelo mesmo
pipeline que Torre já usa (flat, própria definição, split de grupo
automático). Ver arquitetura/ARQUITETURA_ONLINE.md.
"""
import unittest
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import make_world_server, spawn_player, run_ticks, clear_login_immunity

from engine.world import World
from engine.entity_factory import create_minion, create_enemy, create_tilemap, create_tower
from engine.components import Minion, CombatStats, MapLocation, TileMovement, Faction, Tower, Position
from engine.world_systems import (MinionSystem, register_services,
                        CombatSystem, PathfindingSystem, TileValidationSystem, TileMovementSystem)
from content.faction_data import RELATIONSHIP
from content.minion_definitions import MINION_TABLE

_MAP = "maps/map_1.csv"


def _make_world_with_services():
    """World headless com serviços registrados — suficiente pra
    MinionSystem resolver dano/LOS/pathing sem precisar de WorldServer
    inteiro. Diferente de Torre: minion precisa de MapLocation nas
    entidades (pathing/repath dependem de map_file resolver o bundle
    certo, ver MinionSystem._pathfinding_for).

    register_service_resolver(None): limpa o resolver POR-ENTIDADE
    global (`engine.world_systems._svc_resolver`) que um WorldServer
    real de outro arquivo de teste (ex: tests/test_towers.py::
    TestTowerDeathXpGoldRespawn) pode ter deixado registrado — sem
    isso, `is_tile_walkable` (chamado por MinionSystem._walk_toward)
    resolve o bundle de mapa ERRADO (o do WorldServer antigo, real,
    300x603 tiles) pra um eid deste World headless pequeno, já que ids
    de entidade são só inteiros reaproveitados entre `World()`
    diferentes — causava um bug real (pego em teste manual): pathfind
    falhando silenciosamente contra dado de mapa alheio, sem o throttle
    correto (ver fix em `_walk_toward`), travando a suíte inteira ao
    rodar depois de test_towers.py. register_services() só sobrescreve
    o dict `_svc` (fallback), nunca `_svc_resolver` — por isso precisa
    do reset explícito aqui."""
    from engine.world_systems import register_service_resolver
    register_service_resolver(None)
    w = World()
    terrain = ["." * 40 for _ in range(40)]
    objects = [["."] * 40 for _ in range(40)]
    tm_eid = create_tilemap(w, terrain, objects, None)
    tv = TileValidationSystem(w, tilemap_entity=tm_eid)
    pf = PathfindingSystem(w, tilemap_entity=tm_eid)
    combat = CombatSystem(w, is_server=True)
    register_services(combat=combat, pathfinding=pf, tile_validation=tv)
    return w, pf


class TestMinionReservationTable(unittest.TestCase):
    """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §34/§35, item
    #3 do ranking de consumo acumulado) — `_get_occupied_tiles` trocou
    de fresh-scan do mundo inteiro (1x POR MINION) por uma "reservation
    table" mutável (técnica real de cooperative pathfinding, pesquisada
    antes de aplicar) semeada do índice canônico, mas precisa preservar
    o frescor INTRA-tick: minion processado depois no loop de
    `MinionSystem.update()` precisa ver a reserva de um minion
    processado antes, no MESMO tick — senão reintroduz o "martelando
    repath contra tile ocupado" que motivou este mecanismo em 30/07/2026
    (ver §34.73.2 do histórico). Regressão pedida explicitamente pelo
    usuário antes de aprovar a mudança."""

    def setUp(self):
        self.world, self.pf = _make_world_with_services()
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)

    def test_indice_canonico_bate_com_scan_direto(self):
        a = create_minion(self.world, 5, 5, "minion_melee", faction_id="time_a", route=[(5, 5)])
        self.world.add_component(a, MapLocation(_MAP))
        b = create_minion(self.world, 6, 5, "minion_melee", faction_id="time_a", route=[(6, 5)])
        self.world.add_component(b, MapLocation(_MAP))

        self.ms._tile_movement_by_map = None
        self.ms._working_occupied_by_map = {}
        via_scan = self.ms._get_occupied_tiles(_MAP, except_entity_id=a)

        tm_a = self.world.get_component(a, TileMovement)
        tm_b = self.world.get_component(b, TileMovement)
        self.ms._tile_movement_by_map = {_MAP: [(a, tm_a), (b, tm_b)]}
        self.ms._working_occupied_by_map = {}
        via_indice = self.ms._get_occupied_tiles(_MAP, except_entity_id=a)

        self.assertEqual(via_scan, via_indice,
                         "resultado do índice deveria ser IDÊNTICO ao scan direto")
        self.assertIn((6, 5), via_indice, "minion b deveria ocupar seu próprio tile")

    def test_minion_processado_depois_no_loop_ve_reserva_do_anterior_no_mesmo_tick(self):
        """O teste central desta correção: A e B no MESMO tick, A
        processado primeiro reserva um tile — B, processado logo depois,
        TEM que ver essa reserva (não um snapshot congelado do início do
        tick), senão os 2 poderiam mirar o mesmo tile ao mesmo tempo."""
        a = create_minion(self.world, 5, 5, "minion_melee", faction_id="time_a", route=[(5, 5)])
        self.world.add_component(a, MapLocation(_MAP))
        b = create_minion(self.world, 5, 5, "minion_melee", faction_id="time_a", route=[(5, 5)])
        self.world.add_component(b, MapLocation(_MAP))

        tm_a = self.world.get_component(a, TileMovement)
        tm_b = self.world.get_component(b, TileMovement)
        self.ms._tile_movement_by_map = {_MAP: [(a, tm_a), (b, tm_b)]}
        self.ms._working_occupied_by_map = {}

        occ_before = self.ms._get_occupied_tiles(_MAP, except_entity_id=b)
        self.assertNotIn((7, 5), occ_before, "antes da reserva, (7,5) não deveria estar bloqueado")

        # A "decide" se mover pra (7,5) neste tick (mesmo que start_tile_movement
        # + _reserve_tile fazem juntos em _walk_toward/_advance_along_route).
        self.ms._reserve_tile(_MAP, a, 7, 5)

        occ_after = self.ms._get_occupied_tiles(_MAP, except_entity_id=b)
        self.assertIn((7, 5), occ_after,
                      "minion processado DEPOIS no loop deveria ver a reserva do anterior, "
                      "no MESMO tick — sem isso, reintroduz o bug de 30/07/2026")

    def test_working_occupied_semeado_uma_vez_so_por_mapa(self):
        """Confirma que a semente (a partir do índice canônico) só
        acontece na 1ª consulta de cada mapa — chamadas seguintes reusam
        a MESMA cópia mutável (não resemeia, perdendo reservas já
        feitas)."""
        a = create_minion(self.world, 5, 5, "minion_melee", faction_id="time_a", route=[(5, 5)])
        self.world.add_component(a, MapLocation(_MAP))
        tm_a = self.world.get_component(a, TileMovement)
        self.ms._tile_movement_by_map = {_MAP: [(a, tm_a)]}
        self.ms._working_occupied_by_map = {}

        self.ms._get_occupied_tiles(_MAP, except_entity_id=-1)  # semeia
        self.ms._reserve_tile(_MAP, a, 9, 9)
        working_ref = self.ms._working_occupied_by_map[_MAP]
        self.ms._get_occupied_tiles(_MAP, except_entity_id=-1)  # NÃO deveria resemear
        self.assertIs(self.ms._working_occupied_by_map[_MAP], working_ref,
                      "segunda consulta ao mesmo mapa não deveria reconstruir a reserva")
        self.assertIn((9, 9), self.ms._working_occupied_by_map[_MAP])

    def test_update_reseta_reserva_a_cada_tick(self):
        """`_working_occupied_by_map` precisa esvaziar no início de CADA
        `update()` — senão uma reserva de um tick anterior (minion que já
        se moveu de novo) ficaria bloqueando pra sempre."""
        a = create_minion(self.world, 5, 5, "minion_melee", faction_id="time_a", route=[(5, 5)])
        self.world.add_component(a, MapLocation(_MAP))
        self.ms._working_occupied_by_map = {_MAP: {(9, 9): a}}
        self.ms.update(0.05, tile_movement_by_map=None)
        self.assertEqual(self.ms._working_occupied_by_map, {},
                         "update() deveria resetar a reserva no início de cada tick")


class TestMinionSystemTargeting(unittest.TestCase):
    """MinionSystem isolado — advance/aggro/fight/return-to-checkpoint,
    ataque melee/ranged. Não cobre wave spawning/XP (WorldServer real,
    ver classes abaixo)."""

    def setUp(self):
        self.world, self.pf = _make_world_with_services()
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)
        self.tms = TileMovementSystem(self.world)
        RELATIONSHIP[("time_a", "time_b")] = "hostil"
        RELATIONSHIP[("time_b", "time_a")] = "hostil"

    def _make_minion(self, x, y, target, key="minion_melee", faction="time_a"):
        route = [(x, y)] + list(self.pf.find_path((x, y), target))
        eid = create_minion(self.world, x, y, key, faction_id=faction, route=route)
        self.world.add_component(eid, MapLocation(_MAP))
        return eid

    def _run(self, n=1, dt=0.05):
        for _ in range(n):
            self.ms.update(dt)
            self.tms.update([], dt)

    def test_minion_avanca_ao_longo_da_rota_sem_hostil_por_perto(self):
        minion_eid = self._make_minion(5, 5, (5, 20))
        self._run(400)
        m = self.world.get_component(minion_eid, Minion)
        tm = self.world.get_component(minion_eid, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (5, 20))

    def test_minion_contorna_player_aliado_parado_no_caminho(self):
        """Bug real relatado pelo usuário no playtest do battleground MOBA
        (01/08/2026): "se um player aliado fica no caminho deles eles não
        contornam". Raiz: ADVANCING mirava sempre o PRÓXIMO tile da rota
        (distância 1) — `_walk_toward` exclui o DESTINO de
        `dynamic_obstacles` (senão o A* rejeitaria o destino inteiro), mas
        com destino a só 1 tile de distância não sobra espaço NENHUM pra
        desviar; se esse único tile está ocupado, o passo de movimento é
        rejeitado (`is_tile_walkable`) pra sempre, travando o minion ali.
        Corrigido mirando o PRÓXIMO CHECKPOINT (até 5 tiles à frente) em
        vez do próximo tile — dá ao A* espaço real pra rotear ao redor de
        um bloqueio no meio do caminho. Aqui: player aliado parado bem no
        meio do caminho reto; minion precisa terminar a rota mesmo assim."""
        from engine.components import Position as _PosBlk, TileMovement as _TMBlk
        blocker_eid = self.world.create_entity()
        self.world.add_component(blocker_eid, _PosBlk(5 * 32 + 16, 12 * 32 + 16))
        self.world.add_component(blocker_eid, _TMBlk(current_tile_x=5, current_tile_y=12,
                                                      target_tile_x=5, target_tile_y=12,
                                                      is_moving=False))
        # MapLocation é OBRIGATÓRIO — _get_occupied_tiles filtra por
        # map_file; sem isso o bloqueador é invisível pro pathfinder E pro
        # cache de ocupação (is_tile_walkable), o teste passaria mesmo sem
        # o fix (achado ao provar a regressão — ver differential abaixo).
        self.world.add_component(blocker_eid, MapLocation(_MAP))

        minion_eid = self._make_minion(5, 5, (5, 20))
        # _run() normal NÃO chama TileValidationSystem.update() — o cache
        # `_occupied` (que is_tile_walkable consulta de verdade pra
        # REJEITAR o passo de movimento) fica vazio pra sempre, e o
        # bloqueio nunca é aplicado de verdade (achado ao provar a
        # regressão — mesmo com o bug antigo, o teste passava porque a
        # rejeição de movimento nunca disparava). Chamando manualmente
        # aqui, mesma ordem da produção (TileValidationSystem antes de
        # MinionSystem, ver SISTEMAS_ECS.md).
        from engine.world_systems import _svc as _svc_blk
        _tv_blk = _svc_blk["tile_validation"]
        for _ in range(600):
            _tv_blk.update()
            self.ms.update(0.05)
            self.tms.update([], 0.05)
        m = self.world.get_component(minion_eid, Minion)
        tm = self.world.get_component(minion_eid, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (5, 20),
                         "minion deveria ter contornado o bloqueio e chegado no fim da rota")

    def test_minion_nao_faz_pausa_artificial_entre_tiles_da_rota(self):
        """Bug real reportado pelo usuário (30/07/2026): minion andava 1
        tile, parava ~0.8s (PATH_RECALC_INTERVAL), repetia — o backoff de
        retry de pathfind estava sendo aplicado até no PRIMEIRO tile de
        cada segmento novo, não só quando o pathfind pro MESMO destino
        falhava de verdade. Sem hostil por perto (só avanço em linha
        reta, sem pileup de wave), nenhum passo deveria ficar > alguns
        ticks parado esperando pra sair andando de novo."""
        minion_eid = self._make_minion(5, 5, (5, 30))
        tm = self.world.get_component(minion_eid, TileMovement)
        idle_ticks_between_steps = []
        idle_run = 0
        last_tile = (tm.current_tile_x, tm.current_tile_y)
        for _ in range(300):
            self._run(1)
            cur = (tm.current_tile_x, tm.current_tile_y)
            if cur == last_tile and not tm.is_moving:
                idle_run += 1
            else:
                if idle_run:
                    idle_ticks_between_steps.append(idle_run)
                idle_run = 0
            last_tile = cur
        # PATH_RECALC_INTERVAL=0.8s a dt=0.05 = 16 ticks — o bug produzia
        # exatamente ~16 ticks parado entre CADA tile. Corrigido, a única
        # pausa esperada é o 1 tick natural entre "chegou" e "emitiu o
        # próximo passo" (ver _walk_toward/ADVANCING).
        self.assertTrue(all(n <= 2 for n in idle_ticks_between_steps),
                        f"pausa artificial entre tiles detectada: {idle_ticks_between_steps}")

    def test_walk_toward_respeita_orcamento_de_pathfind_por_tick(self):
        """04/08/2026 — freeze/reconexão que voltou a acontecer mesmo após
        a rota compartilhada por lane (§34.74.26): a causa real era
        `_walk_toward` recalculando A* POR MINION a cada ~0.8s durante
        toda a travessia (não só no spawn), e como minions da mesma wave
        andam em lockstep, esses recálculos tendiam a sincronizar — vários
        de uma vez no MESMO tick. `MAX_PATHFINDS_PER_FRAME=15` (pedido do
        usuário) limita quantos `find_path` `_walk_toward` pode disparar
        POR TICK; o resto espera o próximo tick, sem penalidade.

        Desde §34.74.28 (`_advance_along_route`), o caso NORMAL (próximo
        tile da rota adjacente e andável) não chama pathfinding nenhum —
        o orçamento só entra em jogo no desvio LOCAL (bloqueio real, ou
        minion fisicamente fora da rota). Aqui força esse caso: 20
        minions com `route_idx` adiantado à força (simula "saiu da rota"
        — mesma situação de voltar de perseguir um alvo em FIGHTING) — o
        próximo tile da rota fica NÃO-adjacente pra todos, então TODOS
        precisam do desvio no mesmo tick — no máximo 15 chamadas de
        `find_path` devem sair."""
        minion_eids = [self._make_minion(x, 5, (x, 30)) for x in range(5, 25)]
        self.assertEqual(len(minion_eids), 20)
        for eid in minion_eids:
            m = self.world.get_component(eid, Minion)
            m.route_idx = 5  # "fora da rota" — route[6] não é mais adjacente ao spawn

        calls = []
        real_find_path = self.pf.find_path

        def _spy_find_path(*args, **kwargs):
            calls.append(1)
            return real_find_path(*args, **kwargs)

        self.pf.find_path = _spy_find_path

        self.ms.update(0.05)
        self.assertLessEqual(len(calls), MinionSystem.MAX_PATHFINDS_PER_FRAME,
                             "não deveria disparar mais de MAX_PATHFINDS_PER_FRAME "
                             "chamadas de find_path num único tick")

        pending = sum(1 for eid in minion_eids
                     if not self.world.get_component(eid, Minion).current_path)
        self.assertGreater(pending, 0,
                           "com 20 minions fora da rota e orçamento 15, ao menos 1 "
                           "deveria ficar sem path calculado neste tick (adiado)")

    def test_acquire_target_com_spatial_hash_nao_varre_todas_as_entidades(self):
        """04/08/2026, pedido do usuário (log de perf mostrou minion_system
        como o maior consumidor de tick com a BG ativa): minion sem alvo
        em ADVANCING varria TODAS as entidades do jogo TODO tick só pra
        achar "tem hostil por perto?" — `spatial_hash` (`engine.utils.
        SpatialHash`, mesma técnica já usada pro AOI de sessão) limita
        aos candidatos realmente próximos. Aqui: 1 hostil DENTRO do raio
        de aggro + 30 hostis bem LONGE — confirma que o minion ainda
        acha o certo, e que `world.get_entities_with` (sweep completo)
        NUNCA é chamado quando o índice espacial é fornecido."""
        from engine.utils import SpatialHash
        minion_eid = self._make_minion(5, 5, (5, 30))
        close_eid = create_enemy(self.world, 5, 7, race="Lobo", faction="time_b")
        self.world.add_component(close_eid, MapLocation(_MAP))
        for i in range(30):
            far_eid = create_enemy(self.world, 5 + 50 + i, 5, race="Lobo", faction="time_b")
            self.world.add_component(far_eid, MapLocation(_MAP))

        spatial_hash = {_MAP: SpatialHash(cell_size=9)}
        for eid, pos, cs, tm in self.world.get_entities_with(Position, CombatStats, TileMovement):
            if cs.current_hp > 0:
                spatial_hash[_MAP].insert(eid, tm.current_tile_x, tm.current_tile_y)

        calls = []
        real_get_entities_with = self.world.get_entities_with

        def _spy_get_entities_with(*a, **k):
            calls.append(a)
            return real_get_entities_with(*a, **k)

        self.world.get_entities_with = _spy_get_entities_with
        self.ms.update(0.05, spatial_hash=spatial_hash)

        m = self.world.get_component(minion_eid, Minion)
        self.assertEqual(m.current_target_eid, close_eid,
                         "minion deveria ter achado o hostil dentro do raio mesmo com o índice espacial")
        _sweep_calls = [c for c in calls if c and c[0] is Position]
        self.assertEqual(len(_sweep_calls), 0,
                         "_acquire_target não deveria ter feito o sweep completo "
                         "(world.get_entities_with) com spatial_hash fornecido")

    def test_minion_engaja_hostil_dentro_do_raio_de_aggro(self):
        minion_eid = self._make_minion(5, 5, (5, 20))
        enemy_eid = create_enemy(self.world, 5, 8, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        self._run(1)
        m = self.world.get_component(minion_eid, Minion)
        self.assertEqual(m.state, "FIGHTING")
        self.assertEqual(m.current_target_eid, enemy_eid)

    def test_minion_melee_aplica_dano_direto_ao_alcancar_o_alvo(self):
        minion_eid = self._make_minion(5, 5, (5, 20))
        enemy_eid = create_enemy(self.world, 5, 7, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        enemy_cs = self.world.get_component(enemy_eid, CombatStats)
        hp_before = enemy_cs.current_hp
        self._run(200)
        self.assertLess(enemy_cs.current_hp, hp_before,
                        "minion melee deveria ter causado dano direto no alvo")

    def test_minion_ranged_spawna_projetil_ao_atacar(self):
        from engine.components import Projectile
        minion_eid = self._make_minion(5, 5, (5, 20), key="minion_ranged")
        enemy_eid = create_enemy(self.world, 5, 9, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        self._run(60)
        projectiles = list(self.world.get_entities_with(Projectile))
        self.assertGreater(len(projectiles), 0,
                           "minion ranged deveria ter spawnado ao menos 1 Projectile")
        _, proj = projectiles[0]
        self.assertEqual(proj.attacker_id, minion_eid)

    def test_minion_nunca_ataca_hostil_de_time_amigo(self):
        minion_eid = self._make_minion(5, 5, (5, 20), faction="time_a")
        ally_eid = create_enemy(self.world, 5, 7, race="Lobo", faction="time_a")
        self.world.add_component(ally_eid, MapLocation(_MAP))
        self._run(30)
        m = self.world.get_component(minion_eid, Minion)
        self.assertEqual(m.state, "ADVANCING",
                         "minion não deveria engajar aliado do mesmo time")

    def test_ao_perder_alvo_minion_segue_direto_sem_voltar_ao_checkpoint(self):
        """Reversão do comportamento antigo (pedido do usuário, 02/08/2026 —
        "esquecer os checkpoints"): ao perder o alvo, o minion NUNCA mais
        entra em modo de retorno (backtrack) — retoma direto rumo ao mesmo
        destino final de sempre (`route[-1]`, sem checkpoint intermediário
        desde 04/08/2026) a partir de onde já está, atacando qualquer
        hostil que entre no raio de aggro no caminho. Força a situação
        matando o alvo depois do minion já ter avançado boa parte da rota,
        e confirma que ele NUNCA anda de volta rumo ao spawn (rota deste
        teste é uma reta vertical — `tile_y` cresce monotonicamente)."""
        minion_eid = self._make_minion(5, 5, (5, 30))
        m = self.world.get_component(minion_eid, Minion)
        tm = self.world.get_component(minion_eid, TileMovement)
        while tm.current_tile_y < 17:
            self._run(1)
        self.assertGreaterEqual(tm.current_tile_y, 15)
        y_ao_perder_futuro = tm.current_tile_y

        enemy_eid = create_enemy(self.world, tm.current_tile_x, tm.current_tile_y + 1,
                                 race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        # Mata o alvo rapidamente (HP artificialmente baixo) pra soltar o minion.
        self.world.get_component(enemy_eid, CombatStats).current_hp = 1

        min_y_seen = tm.current_tile_y
        for _ in range(60):
            self._run(1)
            min_y_seen = min(min_y_seen, tm.current_tile_y)
            self.assertNotEqual(m.state, "RETURNING",
                                "estado RETURNING não deveria mais existir")
            if m.state == "ADVANCING" and m.current_target_eid == -1:
                break

        self.assertGreaterEqual(min_y_seen, y_ao_perder_futuro,
                                "minion nunca deveria andar de volta rumo ao spawn")
        self.assertEqual(m.state, "ADVANCING")

    def test_apos_perder_alvo_minion_continua_avancando_ate_a_base(self):
        minion_eid = self._make_minion(5, 5, (5, 30))
        m = self.world.get_component(minion_eid, Minion)
        tm = self.world.get_component(minion_eid, TileMovement)
        while tm.current_tile_y < 17:
            self._run(1)
        enemy_eid = create_enemy(self.world, tm.current_tile_x, tm.current_tile_y + 1,
                                 race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        self.world.get_component(enemy_eid, CombatStats).current_hp = 1
        # Mata o alvo (leva alguns ticks até o minion realmente desferir o
        # golpe/cooldown de ataque zerar) e solta o minion de volta pra
        # ADVANCING.
        for _ in range(60):
            self._run(1)
            if m.state == "ADVANCING":
                break

        self.assertEqual(m.state, "ADVANCING")
        for _ in range(400):
            if (tm.current_tile_x, tm.current_tile_y) == m.route[-1]:
                break
            self._run(1)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), m.route[-1],
                         "minion deveria ter continuado e completado a rota até a base inimiga")


class TestMinionCrowdControl(unittest.TestCase):
    """Bug real relatado pelo usuário (03/08/2026): "os efeitos, slow,
    sleep etc, não estão funcionando na instância". Causa raiz:
    MinionSystem/TowerSystem não usam AIControlled/EnemyAISystem de
    propósito (ver docstrings das classes) — então nunca passavam pelo
    bloco de stun/sleep/fear/polymorph/disoriented/root que EnemyAISystem
    aplica pra mobs normais. Fix: mesmo choke-point único
    (`engine.utils.is_action_locked`/`is_movement_locked`) já usado por
    combat_processor.py/skill_processor.py/PlayerInputSystem."""

    def setUp(self):
        self.world, self.pf = _make_world_with_services()
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)
        self.tms = TileMovementSystem(self.world)
        RELATIONSHIP[("time_a", "time_b")] = "hostil"
        RELATIONSHIP[("time_b", "time_a")] = "hostil"

    def _make_minion(self, x, y, target, key="minion_melee", faction="time_a"):
        route = [(x, y)] + list(self.pf.find_path((x, y), target))
        eid = create_minion(self.world, x, y, key, faction_id=faction, route=route)
        self.world.add_component(eid, MapLocation(_MAP))
        return eid

    def _run(self, n=1, dt=0.05):
        for _ in range(n):
            self.ms.update(dt)
            self.tms.update([], dt)

    def test_minion_atordoado_nao_ataca(self):
        """Movimento sob stun/sleep já era parado por um safeguard PRÉ-
        EXISTENTE e genérico em `TileMovementSystem.update()` (cancela
        `is_moving` de qualquer entidade com esses 2 efeitos ativos,
        independente de quem iniciou o passo) — não é o gap deste fix. O
        gap real (confirmado só existir aqui) é ATACAR: nada impedia
        `MinionSystem._attack` de disparar pra um minion atordoado/
        dormindo PARADO no lugar."""
        from engine.core_systems import apply_effect
        minion_eid = self._make_minion(5, 5, (5, 20))
        m = self.world.get_component(minion_eid, Minion)
        enemy_eid = create_enemy(self.world, 6, 5, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        enemy_cs = self.world.get_component(enemy_eid, CombatStats)
        enemy_hp_before = enemy_cs.current_hp
        m.current_target_eid = enemy_eid
        m.state = "FIGHTING"
        apply_effect(self.world, minion_eid, "stun", duration=5.0)
        self._run(60)
        self.assertEqual(enemy_cs.current_hp, enemy_hp_before,
                         "minion atordoado não deveria conseguir atacar")

    def test_minion_dormindo_nao_ataca(self):
        from engine.core_systems import apply_effect
        minion_eid = self._make_minion(5, 5, (5, 20))
        m = self.world.get_component(minion_eid, Minion)
        enemy_eid = create_enemy(self.world, 6, 5, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        enemy_cs = self.world.get_component(enemy_eid, CombatStats)
        enemy_hp_before = enemy_cs.current_hp
        m.current_target_eid = enemy_eid
        m.state = "FIGHTING"
        apply_effect(self.world, minion_eid, "sleep", duration=5.0)
        self._run(60)
        self.assertEqual(enemy_cs.current_hp, enemy_hp_before,
                         "minion dormindo não deveria conseguir atacar")

    def test_minion_enraizado_nao_avanca_rumo_ao_alvo(self):
        from engine.core_systems import apply_effect
        minion_eid = self._make_minion(5, 5, (5, 20))
        m = self.world.get_component(minion_eid, Minion)
        # Alvo LONGE (fora do alcance de ataque melee) — sem root, o minion
        # deveria perseguir (_walk_toward); enraizado, deveria ficar parado.
        enemy_eid = create_enemy(self.world, 5, 15, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        m.current_target_eid = enemy_eid
        m.state = "FIGHTING"
        apply_effect(self.world, minion_eid, "root", duration=20.0)
        self._run(400)
        tm = self.world.get_component(minion_eid, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (5, 5),
                         "minion enraizado não deveria perseguir o alvo")

    def test_minion_enraizado_ainda_ataca_alvo_ja_no_alcance(self):
        from engine.core_systems import apply_effect
        minion_eid = self._make_minion(5, 5, (5, 20))
        m = self.world.get_component(minion_eid, Minion)
        enemy_eid = create_enemy(self.world, 6, 5, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        enemy_cs = self.world.get_component(enemy_eid, CombatStats)
        enemy_hp_before = enemy_cs.current_hp
        m.current_target_eid = enemy_eid
        m.state = "FIGHTING"
        apply_effect(self.world, minion_eid, "root", duration=5.0)
        self._run(60)
        self.assertLess(enemy_cs.current_hp, enemy_hp_before,
                        "minion enraizado ainda deveria conseguir atacar um alvo já no alcance")

    def test_torre_atordoada_continua_atacando(self):
        """Decisão do usuário (03/08/2026, ao revisar o fix de CC em
        minion/torre): torre é ESTRUTURA, não "ser vivo" — stun/sleep/
        fear/polymorph/disoriented/root não deveriam afetá-la, só dano.
        Diferente de minion (que É afetado por CC, ver testes acima) —
        TowerSystem NÃO tem (e não deve ganhar) o gate de `is_action_
        locked`/`is_movement_locked`."""
        from engine.core_systems import apply_effect
        tower_eid = create_tower(self.world, 10, 10, "torre_de_fogo", faction_id="time_a")
        self.world.add_component(tower_eid, MapLocation(_MAP))
        enemy_eid = create_enemy(self.world, 11, 10, race="Lobo", faction="time_b")
        self.world.add_component(enemy_eid, MapLocation(_MAP))
        enemy_cs = self.world.get_component(enemy_eid, CombatStats)
        enemy_hp_before = enemy_cs.current_hp
        from engine.world_systems import TowerSystem, ProjectileSystem
        ts = TowerSystem(self.world)
        proj_sys = ProjectileSystem(self.world, None)
        apply_effect(self.world, tower_eid, "stun", duration=5.0)
        for _ in range(60):
            ts.update(0.05)
            proj_sys.update(dt=0.05)
        self.assertLess(enemy_cs.current_hp, enemy_hp_before,
                        "torre é estrutura — CC não deveria impedi-la de atacar")


class TestMinionTowerAggro(unittest.TestCase):
    """MinionSystem._check_tower_aggro (03/08/2026, pedido do usuário:
    "quando a torre ataca um minion, ele agra na torre e os minions em
    torno também agram") — mesmo mecanismo de combat_this_tick já usado
    pelo aggro-switch de Torre, consumido aqui pro lado do minion."""

    def setUp(self):
        self.world, self.pf = _make_world_with_services()
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)
        RELATIONSHIP[("time_a", "time_b")] = "hostil"
        RELATIONSHIP[("time_b", "time_a")] = "hostil"

    def _make_minion(self, x, y, target, key="minion_melee", faction="time_a"):
        route = [(x, y)] + list(self.pf.find_path((x, y), target))
        eid = create_minion(self.world, x, y, key, faction_id=faction, route=route)
        self.world.add_component(eid, MapLocation(_MAP))
        return eid

    def _make_tower(self, x, y, faction="time_b"):
        eid = create_tower(self.world, x, y, "torre_de_fogo", faction_id=faction)
        self.world.add_component(eid, MapLocation(_MAP))
        return eid

    def test_minion_atingido_troca_alvo_pra_torre(self):
        minion_eid = self._make_minion(10, 10, (10, 30))
        tower_eid  = self._make_tower(15, 10)
        self.ms.update(0.05, combat_this_tick=[
            {"attacker": tower_eid, "target": minion_eid}])

        m = self.world.get_component(minion_eid, Minion)
        self.assertEqual(m.current_target_eid, tower_eid)
        self.assertEqual(m.state, "FIGHTING")

    def test_minion_aliado_em_area_tambem_agra_na_torre(self):
        hit_eid  = self._make_minion(10, 10, (10, 30))
        ally_eid = self._make_minion(11, 10, (11, 30))
        tower_eid = self._make_tower(15, 10)
        self.ms.update(0.05, combat_this_tick=[
            {"attacker": tower_eid, "target": hit_eid}])

        ally = self.world.get_component(ally_eid, Minion)
        self.assertEqual(ally.current_target_eid, tower_eid,
                         "minion aliado próximo deveria ter agrado na torre também")
        self.assertEqual(ally.state, "FIGHTING")

    def test_minion_aliado_fora_do_raio_nao_agra(self):
        hit_eid = self._make_minion(10, 10, (10, 30))
        far_eid = self._make_minion(
            10 + MinionSystem.TOWER_AGGRO_SPREAD_RADIUS_TILES + 5, 10, (30, 30))
        tower_eid = self._make_tower(15, 10)
        self.ms.update(0.05, combat_this_tick=[
            {"attacker": tower_eid, "target": hit_eid}])

        far = self.world.get_component(far_eid, Minion)
        self.assertEqual(far.current_target_eid, -1,
                         "minion fora do raio de espalhamento não deveria agrar")

    def test_minion_inimigo_da_torre_atacante_nao_agra(self):
        """Minion do MESMO time da torre atacante nunca deveria "agrar" na
        própria torre — is_hostile(minion, tower) precisa ser True pro
        aggro disparar; torre e minion aliados nunca são hostis entre si."""
        minion_eid = self._make_minion(10, 10, (10, 30), faction="time_b")
        tower_eid  = self._make_tower(20, 20, faction="time_b")
        self.ms.update(0.05, combat_this_tick=[
            {"attacker": tower_eid, "target": minion_eid}])

        m = self.world.get_component(minion_eid, Minion)
        self.assertEqual(m.current_target_eid, -1)

    def test_minion_aliado_ja_lutando_outro_alvo_nao_e_puxado(self):
        hit_eid  = self._make_minion(10, 10, (10, 30))
        ally_eid = self._make_minion(11, 10, (11, 30))
        tower_eid = self._make_tower(15, 10)
        # Alvo "outro" precisa parecer vivo de verdade (CombatStats +
        # Position) — senão a validação normal de FIGHTING (rodada logo
        # depois de _check_tower_aggro, no mesmo update()) já invalida
        # sozinha por "alvo sem CombatStats", mascarando o que este teste
        # quer provar (que _check_tower_aggro não MEXE nele).
        from engine.components import Position as _PosOther
        other_target = self.world.create_entity()
        self.world.add_component(other_target, _PosOther(x=11 * 32, y=11 * 32))
        self.world.add_component(other_target, CombatStats())
        self.world.add_component(other_target, MapLocation(_MAP))

        ally = self.world.get_component(ally_eid, Minion)
        ally.state = "FIGHTING"
        ally.current_target_eid = other_target

        self.ms.update(0.05, combat_this_tick=[
            {"attacker": tower_eid, "target": hit_eid}])

        self.assertEqual(ally.current_target_eid, other_target,
                         "minion já engajado com outro alvo não deveria ser desviado pra torre")


class TestMinionWaveSpawning(unittest.TestCase):
    """Composição da wave + gate de lane ativa/inativa — via WorldServer
    real (precisa de _map_bundles/pathfinding por mapa)."""

    def setUp(self):
        self.ws = make_world_server()

    def _lane(self, faction="arena_time_a", spawn=(130, 374), target=(130, 390), interval=45.0):
        # target_tile é sempre uma LISTA de waypoints (normalizado por
        # engine/map_loader.py — este helper monta o dict direto, sem
        # passar pelo loader, então precisa espelhar o mesmo formato).
        return {"faction": faction, "spawn_tile": spawn, "target_tile": [target],
                "wave_interval_s": interval, "level": 1}

    def test_lane_inativa_nunca_spawna(self):
        self.ws._minion_lanes[self.ws._map_file] = [self._lane()]
        # NÃO chama _activate_minion_lanes — lane fica registrada mas inativa.
        run_ticks(self.ws, 60 * 60)  # bem além do wave_interval_s, se estivesse ativa
        minions = [eid for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertEqual(minions, [], "lane não-ativada não deveria nunca spawnar")

    def test_wave_spawna_composicao_exata_1_2_1(self):
        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0)]
        self.ws._activate_minion_lanes(self.ws._map_file)
        # 10.0s simulados: wave_interval_s=6.0 + até 3.0s de spawn escalonado
        # (4º minion da leva, MINION_SPAWN_STAGGER_S=0.5 — 01/08/2026,
        # pedido do usuário: minions da mesma wave nascem 1 por vez, não
        # todos no mesmo tick, senão travavam uns aos outros logo ao nascer)
        # + margem, sem alcançar a 2ª wave (dispararia aos 12.0s).
        run_ticks(self.ws, 200)
        minions = [self.ws.world.get_component(eid, Minion)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        keys = [m.minion_key for m in minions]
        self.assertEqual(keys.count("minion_melee"), 1)
        self.assertEqual(keys.count("minion_ranged"), 2)
        self.assertEqual(keys.count("minion_ranged_raro"), 1)
        self.assertEqual(len(minions), 4)

    def test_minions_da_wave_nascem_escalonados_nao_todos_no_mesmo_tick(self):
        """Bug real relatado pelo usuário no primeiro playtest num mapa
        MOBA de verdade (01/08/2026): os minions da wave nascendo no
        MESMO tick travavam uns aos outros (MinionSystem via os vizinhos
        recém-nascidos como dynamic_obstacle antes de terem espaço pra se
        afastar). Pedido do usuário: espaçar o nascimento em
        MINION_SPAWN_STAGGER_S (0.5s) entre um minion e outro da mesma
        wave — aqui confirma que a contagem de minions cresce aos poucos
        ao longo dos ticks, não pula de 0 pra 4 num tick só."""
        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0)]
        self.ws._activate_minion_lanes(self.ws._map_file)
        # Roda até o instante em que o wave timer deveria ter acabado de
        # disparar (pouco depois de 6.0s = tick 120) e confirma que NENHUM
        # minion existe ainda de imediato (a fila de spawn escalonado
        # ainda não teve tempo de materializar nem o 1º).
        run_ticks(self.ws, 121)
        count_at_trigger = len(self.ws.world.get_entities_with(Minion))
        self.assertLess(count_at_trigger, 4,
                        "não deveriam existir os 4 minions imediatamente ao disparar a wave")
        # Depois de esperar o tempo total de escalonamento (1.5s pro 4º
        # minion) + margem, os 4 já deveriam ter nascido.
        run_ticks(self.ws, 80)  # +4.0s
        count_final = len(self.ws.world.get_entities_with(Minion))
        self.assertEqual(count_final, 4)
        self.assertLess(count_at_trigger, count_final,
                        "contagem deveria ter crescido aos poucos, não tudo de uma vez")

    def test_minions_da_wave_tem_rota_calculada_ate_o_alvo(self):
        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0)]
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)
        minions = [self.ws.world.get_component(eid, Minion)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        for m in minions:
            self.assertGreater(len(m.route), 1, "rota deveria ter sido calculada via pathfinding")
            self.assertEqual(m.route[-1], (130, 390))

    def test_rota_da_wave_desvia_de_torre_no_caminho_reto(self):
        """Bug real relatado pelo usuário no playtest do mapa MOBA
        (01/08/2026): minion "travado atrás da torre", nunca desviando.
        Raiz: a rota era calculada sem saber onde as torres estavam — se
        cruzava o tile exato de uma, o minion recebia esse tile como
        PRÓXIMO PASSO em ADVANCING, e `_walk_toward` só pede ao
        pathfinder "chegar no PRÓXIMO tile" (distância 1) — sem espaço
        pra desviar quando esse único tile está permanentemente ocupado
        (torre nunca sai do lugar). Corrigido passando as torres do mapa
        como `dynamic_obstacles` pro cálculo da rota (`_tick_minion_
        waves`/`_get_tower_tiles_for_map`) — aqui confirma que uma torre
        plantada EXATAMENTE no caminho reto spawn→alvo nunca aparece na
        rota calculada."""
        from engine.entity_factory import create_tower as _ctwr
        tower_tile = (130, 380)  # no meio do caminho reto (130,374)->(130,390)
        tower_eid = _ctwr(self.ws.world, tower_tile[0], tower_tile[1], "torre_de_fogo",
                          faction_id="monstros_hostis")
        self.ws.world.add_component(tower_eid, MapLocation(self.ws._map_file))

        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0)]
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)
        minions = [self.ws.world.get_component(eid, Minion)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions)
        for m in minions:
            self.assertNotIn(tower_tile, m.route,
                            "rota não deveria atravessar o tile exato de uma torre")
            self.assertEqual(m.route[-1], (130, 390), "ainda deveria chegar no alvo, só contornando")

    def test_lane_com_multiplos_waypoints_passa_por_cada_um_em_ordem(self):
        """Pedido do usuário (30/07/2026): lane com curva (top/bot) — não
        uma reta só — precisa de waypoints intermediários antes da base
        inimiga de verdade. `target_tile` vira lista `[wp1, wp2]`; a
        rota final deve terminar no ÚLTIMO (base) e passar perto do
        primeiro (curva) no meio do caminho."""
        wp1 = (140, 374)   # ponto de curva intermediário
        wp2 = (140, 390)   # base inimiga de verdade (última perna)
        lane = self._lane(spawn=(130, 374), interval=6.0)
        lane["target_tile"] = [wp1, wp2]
        self.ws._minion_lanes[self.ws._map_file] = [lane]
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)
        minions = [self.ws.world.get_component(eid, Minion)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions)
        for m in minions:
            self.assertEqual(m.route[-1], wp2, "rota deveria terminar no ÚLTIMO waypoint (base)")
            self.assertIn(wp1, m.route, "rota deveria passar pelo waypoint intermediário (curva)")
            # A curva tem que vir ANTES da base na ordem da rota.
            self.assertLess(m.route.index(wp1), m.route.index(wp2))

    def test_todas_as_lanes_reais_da_bg_produzem_rota_valida(self):
        """Bug real relatado pelo usuário (03/08/2026): "até agora eu só
        testei a rota do mid... quero liberar todas as rotas". Causa
        raiz DUPLA, só visível no mapa REAL da BG (100×100, não nos
        dados sintéticos curtos do resto desta classe):
        1. `max_nodes=300` (default do pathfinder) é baixo demais pras
           lanes com curva (top/bot) — cada perna precisa de ~450-590
           nós explorados; mid é reta e cabe em ~100-270, por isso era a
           ÚNICA que sempre funcionava (falha virava silêncio total: 0
           minions, sem log nenhum).
        2. `target_tile` da lane mid do time B caía EXATAMENTE no tile
           do Nexus do time A (torre = dynamic_obstacle da rota),
           tornando o alvo inalcançável por definição, independente do
           budget de busca.
        Carrega o mapa/entities REAIS da BG (não substitui spawn_tile/
        target_tile por dado sintético) pra travar essa classe de
        regressão — só acelera wave_interval_s pro teste não demorar."""
        from server.debug_battleground import DEBUG_BG_GATE_TILES
        from engine.tileset import STONE_FLOOR
        from engine.components import Tilemap

        key = "maps/moba_battleground.csv::testkey"
        bundle = self.ws._load_instance("maps/moba_battleground.csv", key)
        tm = self.ws.world.get_component(bundle.tilemap_entity, Tilemap)
        # Abre os portões dos dois times — igual debug_battleground.py::
        # _tick_gate faz de verdade quando o portão abre (sem isso, as
        # bases ficam ilhas isoladas de ~25 tiles, sem conexão NENHUMA
        # com o resto do mapa, pra qualquer max_nodes).
        for tiles in DEBUG_BG_GATE_TILES.values():
            for gx, gy in tiles:
                tm.tile_matrix[gy][gx] = STONE_FLOOR

        self.assertEqual(len(self.ws._minion_lanes[key]), 6,
                         "mapa real deveria ter as 6 lanes (top/mid/bot x 2 times)")
        for lane in self.ws._minion_lanes[key]:
            lane["wave_interval_s"] = 12.0  # só acelera o teste
            # first_wave_delay_s do JSON real (13/08/2026) é um atraso
            # ABSOLUTO em segundos, independente de wave_interval_s — sem
            # zerar aqui, o mutation acima não aceleraria a 1ª wave (ficaria
            # travada nos 15/16.5/18s reais do mapa), quebrando a janela de
            # ticks abaixo. None cai no fallback _LANE_GROUP_STAGGER_S, que
            # É o mecanismo que este teste quer exercitar.
            lane["first_wave_delay_s"] = None

        self.ws._activate_minion_lanes(key)
        # 400 ticks = 20.0s. As 6 lanes NÃO disparam mais no mesmo tick
        # (03/08/2026, mesmo pedido do usuário — grupos top→bot→mid, +1.5s
        # por grupo, ver _LANE_GROUP_STAGGER_S): top dispara aos 12.0s
        # (materializa até ~15.0s), bot aos 13.5s (~16.5s), mid aos 15.0s
        # (~18.0s) — janela precisa cobrir até mid materializar (18.0s) SEM
        # alcançar a 2ª wave de top (24.0s). 20.0s fica confortavelmente no
        # meio dos dois limites.
        run_ticks(self.ws, 400)

        minion_eids = [eid for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertEqual(len(minion_eids), 24,  # 6 lanes × 4 minions/wave
                         "todas as 6 lanes deveriam conseguir spawnar a wave "
                         "completa no mapa real da BG (verifica max_nodes E "
                         "target_tile de cada lane)")

        # 3ª causa raiz do mesmo pedido: _MINION_WAVE_OFFSETS tem 3 offsets
        # que caem em parede nas lanes de base "estreita" (A-top/A-bot/
        # B-bot) — minion nascia PRESO dentro da parede (visualmente errado,
        # mesmo que a rota em si computasse certo a partir dali). Fix:
        # spawn_x/spawn_y cai pro tile PURO da lane (sempre andável, é onde
        # o minion nasceria sem offset nenhum) quando o offset dá parede.
        for eid in minion_eids:
            tm_m = self.ws.world.get_component(eid, TileMovement)
            self.assertFalse(
                tm.tile_matrix[tm_m.current_tile_y][tm_m.current_tile_x].is_solid,
                f"minion {eid} nasceu dentro de uma parede em "
                f"({tm_m.current_tile_x},{tm_m.current_tile_y})")

    def test_minion_fisicamente_segue_a_propria_rota_nao_atalho_pelo_meio(self):
        """Bug real relatado pelo usuário (04/08/2026): "os minions não
        estão indo para sua rota, todos estão indo para o mid". Causa
        raiz confirmada por simulação: ADVANCING mirava `route[-1]`
        direto via A* LIVRE (§34.74.27) — que, num mapa com "gargalo" (só
        1 passagem livre cruzando a selva central desta BG), redescobre
        o caminho mais curto do mapa a partir de QUALQUER posição física,
        não o polyline por-lane. Resultado real observado: minions de
        TODAS as 6 lanes (mesmo com rota calculada certo no spawn)
        convergindo fisicamente pro centro do mapa, a dezenas de tiles de
        distância da própria rota. `_advance_along_route` (§34.74.28)
        consome `route` tile a tile — o minion nunca deveria se afastar
        muito do polyline calculado. Roda com o mapa/entities REAIS da
        BG (6 lanes reais, incluindo top/bot com curva pela borda) por
        tempo suficiente pra sair da wave e andar de verdade."""
        key = "maps/moba_battleground.csv::routecheck"
        from server.debug_battleground import DEBUG_BG_GATE_TILES
        from engine.tileset import STONE_FLOOR
        from engine.components import Tilemap
        bundle = self.ws._load_instance("maps/moba_battleground.csv", key)
        tm_route = self.ws.world.get_component(bundle.tilemap_entity, Tilemap)
        for tiles in DEBUG_BG_GATE_TILES.values():
            for gx, gy in tiles:
                tm_route.tile_matrix[gy][gx] = STONE_FLOOR
        for lane in self.ws._minion_lanes[key]:
            lane["wave_interval_s"] = 6.0
            lane["first_wave_delay_s"] = None  # ver comentário equivalente acima
        self.ws._activate_minion_lanes(key)
        run_ticks(self.ws, 200)  # 10.0s — todos os 3 grupos disparam e materializam
        run_ticks(self.ws, 400)  # +20.0s de caminhada real

        minions = [(eid, self.ws.world.get_component(eid, Minion))
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions, "deveriam existir minions vivos após 30s de wave+andar")
        for eid, m in minions:
            tmv = self.ws.world.get_component(eid, TileMovement)
            cur = (tmv.current_tile_x, tmv.current_tile_y)
            min_dist = min(max(abs(cur[0] - rx), abs(cur[1] - ry)) for rx, ry in m.route)
            self.assertLessEqual(min_dist, 15,
                f"minion {eid} se afastou {min_dist} tiles da própria rota "
                f"(pos={cur}, rota termina em {m.route[-1]}) — provável atalho "
                f"pelo meio do mapa em vez de seguir o polyline da lane")

    def test_wave_calcula_1_rota_por_lane_nao_por_minion(self):
        """Bug real relatado pelo usuário (03/08/2026): ao liberar as 6
        lanes, o servidor travava e a conexão caía com "keepalive ping
        timeout" — as 6 lanes disparavam no MESMO tick (timer idêntico) e
        cada uma recalculava o A* individualmente pra CADA minion da wave
        (até dezenas de buscas síncronas — `_tick()` é uma função comum,
        não `async def`, então nada mais no asyncio processa enquanto ela
        roda), tempo suficiente pra estourar o timeout de keepalive ping
        da lib `websockets` e derrubar a conexão sozinha. Fix: a rota é
        calculada 1x por LANE (o "tronco" compartilhado, a partir do tile
        PURO — sem offset — da lane), e cada minion só prepende seu
        próprio tile de nascimento na frente do tronco JÁ CALCULADO —
        nunca refaz o A* por minion. Espiona `find_path` pra confirmar:
        no máximo 1 chamada por PERNA por lane (não por minion)."""
        pf = self.ws._get_pathfinding_for_map_file(self.ws._map_file)
        calls = []
        real_find_path = pf.find_path
        def _spy_find_path(*a, **k):
            calls.append(a)
            return real_find_path(*a, **k)
        pf.find_path = _spy_find_path

        # 2 lanes, 1 perna cada (reta), 4 minions/wave cada (04/08/2026,
        # composição reduzida) — sem o cache, seriam 4+4=8 chamadas (1 por
        # minion); com o cache, só 1+1=2 (1 por lane). Chama
        # `_tick_minion_waves` DIRETO (não o `_tick()` completo) — isola
        # só a função sob teste, sem o ruído de OUTROS sistemas do mapa
        # padrão (EnemyAISystem etc.) que também chamam find_path pros
        # mobs reais que `make_world_server()` já povoa.
        lane_a = self._lane(faction="arena_time_a", spawn=(130, 374),
                            target=(130, 390), interval=6.0)
        lane_a["lane_id"] = "top"
        lane_b = self._lane(faction="arena_time_b", spawn=(132, 374),
                            target=(132, 390), interval=6.0)
        lane_b["lane_id"] = "top"
        self.ws._minion_lanes[self.ws._map_file] = [lane_a, lane_b]
        self.ws._activate_minion_lanes(self.ws._map_file)
        self.ws._tick_minion_waves(6.5)  # empurra as 2 lanes (mesmo grupo "top") de uma vez

        self.assertLessEqual(len(calls), 2,
                             "deveria calcular NO MÁXIMO 1 rota por lane (não por minion) — "
                             f"{len(calls)} chamadas de find_path pra 2 lanes de 4 minions cada")
        self.assertEqual(len(self.ws._minion_spawn_queue), 8,
                         "as 2 lanes ainda deveriam ter enfileirado 4 minions cada")

    def test_grupos_de_lane_disparam_escalonados_nao_no_mesmo_tick(self):
        """Mesmo pedido do usuário (03/08/2026), 2ª parte da mitigação:
        mesmo com o cache por lane, as 6 lanes disparando no MESMO tick
        (timer idêntico pra todas) ainda concentra o trabalho — grupos
        top→bot→mid (dos 2 times juntos, pra não desbalancear) disparam
        `_LANE_GROUP_STAGGER_S` (1.5s) um depois do outro. Confirma que,
        pouco depois da wave "top" dar tempo de materializar, SÓ os
        minions de "top" existem — "bot"/"mid" ainda não dispararam."""
        key = "maps/moba_battleground.csv::testkey"
        from server.debug_battleground import DEBUG_BG_GATE_TILES
        from engine.tileset import STONE_FLOOR
        from engine.components import Tilemap
        bundle = self.ws._load_instance("maps/moba_battleground.csv", key)
        tm = self.ws.world.get_component(bundle.tilemap_entity, Tilemap)
        for tiles in DEBUG_BG_GATE_TILES.values():
            for gx, gy in tiles:
                tm.tile_matrix[gy][gx] = STONE_FLOOR
        for lane in self.ws._minion_lanes[key]:
            lane["wave_interval_s"] = 6.0
            lane["first_wave_delay_s"] = None  # ver comentário equivalente acima

        self.ws._activate_minion_lanes(key)
        # Destinos das 2 lanes "top" (dos 2 times) — lidos do JSON real em
        # vez de hardcoded: coordenadas de mapa já mudaram 2x nesta sessão
        # por edição do usuário, hardcoded quebra toda vez (mesma classe
        # de bug do target_tile colidindo com torre — dado de mapa editado
        # em paralelo invalida suposição implícita de teste).
        top_dests = {tuple(l["target_tile"][-1]) for l in self.ws._minion_lanes[key]
                    if l.get("lane_id", "default") == "top"}
        self.assertEqual(len(top_dests), 2, "deveriam existir 2 lanes 'top' (1 por time)")

        # 190 ticks = 9.5s: "top" dispara aos 6.0s e materializa até 9.0s
        # (2 lanes × 7 × até 3.0s de escalonamento). "bot" só dispara aos
        # 7.5s (+1.5s de atraso de grupo) — ainda não teve tempo de
        # materializar NENHUM minion (o 1º só nasce em 7.5s, e mesmo esse
        # já entraria na contagem — por isso a janela pára logo ANTES,
        # em 9.5s, com folga underneath do materializar completo de "top"
        # mas sem alcançar minions reais de "bot"/"mid" além do que já
        # começou). Verifica só a COMPOSIÇÃO: nenhum minion nasceu ainda
        # fora do grupo "top" nesta janela inicial mais curta.
        run_ticks(self.ws, 122)  # 6.1s: só passou o disparo de "top" (6.0s)
        minions_early = [self.ws.world.get_component(eid, Minion)
                         for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions_early, "lane 'top' já deveria ter começado a nascer")
        self.assertTrue(all(m.route[-1] in top_dests for m in minions_early),
                        f"nesta janela, só minions da lane 'top' (rota terminando em {top_dests}) "
                        "deveriam existir — 'bot'/'mid' ainda não dispararam")

    def test_map_loader_normaliza_target_tile_par_unico_e_lista_de_waypoints(self):
        """Confirma os 2 formatos aceitos no JSON — par único `[x,y]`
        (mid, reta) e lista de waypoints `[[x,y],[x,y]]` (top/bot, curva)
        — ambos normalizados pro MESMO formato interno (lista de tuplas)
        que `_tick_minion_waves` consome."""
        import json, tempfile, os as _os
        from engine.map_loader import _merge_entities_json
        data = {
            "minion_lanes": [
                {"faction": "a", "spawn_tile": [1, 1], "target_tile": [5, 5]},
                {"faction": "b", "spawn_tile": [1, 1], "target_tile": [[2, 2], [5, 5]]},
            ]
        }
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            # _merge_entities_json espera um spawn_points já populado com
            # os defaults de load_map (não um dict vazio) — só "merchants"
            # (via marcador legado no terreno) importa pro branch que
            # este teste exercita, mas outros branches (elif "x" in data)
            # também leem chaves default se o JSON tiver aquela seção.
            spawn_points = {"minion_lanes": [], "merchants": [], "enemies": []}
            _merge_entities_json(path, spawn_points)
        finally:
            _os.remove(path)
        lanes = spawn_points["minion_lanes"]
        self.assertEqual(lanes[0]["target_tile"], [(5, 5)],
                         "par único deveria virar lista de 1 waypoint")
        self.assertEqual(lanes[1]["target_tile"], [(2, 2), (5, 5)],
                         "lista de waypoints deveria ser preservada (como tuplas)")

    def test_wave_intervalo_configuravel_por_lane_no_json(self):
        """Pedido do usuário: intervalo tem que ser configurável (não uma
        constante global) — 2 lanes com intervalos diferentes no MESMO
        mapa disparam em momentos diferentes."""
        self.ws._minion_lanes[self.ws._map_file] = [
            self._lane(faction="arena_time_a", interval=6.0),
            self._lane(faction="arena_time_b", spawn=(130, 390), target=(130, 374), interval=100.0),
        ]
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)  # 10.0s (trigger + spawn escalonado) — só a lane de 6.0s deveria ter disparado
        minions = [self.ws.world.get_component(eid, Minion)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertEqual(len(minions), 4, "só a lane com intervalo curto deveria ter spawnado")

    def test_first_wave_delay_s_controla_1a_wave_independente_do_intervalo(self):
        """Pedido do usuário (13/08/2026): atraso da 1ª wave configurável
        por lane, INDEPENDENTE de wave_interval_s (que só passa a valer a
        partir da 2ª wave) — ex: 1ª wave de "top" aos 3.0s, "bot" aos
        4.5s, mas as duas com wave_interval_s=10.0 daí em diante. A
        diferença de 1.5s entre as duas se propaga pra SEMPRE (2ª wave de
        "top" aos 13.0s, "bot" aos 14.5s — mesmo gap)."""
        lane_top = self._lane(faction="arena_time_a", spawn=(130, 374),
                              target=(130, 390), interval=10.0)
        lane_top["lane_id"] = "top"
        lane_top["first_wave_delay_s"] = 3.0
        lane_bot = self._lane(faction="arena_time_a", spawn=(132, 374),
                              target=(132, 390), interval=10.0)
        lane_bot["lane_id"] = "bot"
        lane_bot["first_wave_delay_s"] = 4.5
        self.ws._minion_lanes[self.ws._map_file] = [lane_top, lane_bot]
        self.ws._activate_minion_lanes(self.ws._map_file)

        # Chama _tick_minion_waves DIRETO com dt precisos (mesmo padrão de
        # test_wave_calcula_1_rota_por_lane_nao_por_minion) — evita ruído
        # de granularidade de tick e isola só a função sob teste.
        self.ws._tick_minion_waves(2.9)
        self.assertEqual(len(self.ws._minion_spawn_queue), 0,
            "nenhuma lane deveria ter disparado antes do próprio first_wave_delay_s")

        self.ws._tick_minion_waves(0.2)  # total 3.1s — passa do delay de "top" (3.0s)
        self.assertEqual(len(self.ws._minion_spawn_queue), 4,
            "'top' deveria ter disparado exatamente no first_wave_delay_s dela (3.0s), "
            "não em wave_interval_s (10.0s)")
        self.ws._minion_spawn_queue.clear()

        self.ws._tick_minion_waves(1.5)  # total 4.6s — passa do delay de "bot" (4.5s)
        self.assertEqual(len(self.ws._minion_spawn_queue), 4,
            "'bot' deveria ter disparado exatamente no first_wave_delay_s dela (4.5s)")
        self.ws._minion_spawn_queue.clear()

        # 2ª wave de "top": 1ª foi aos 3.0s + wave_interval_s (10.0s) = 13.0s.
        # Já passamos 4.6s — faltam 8.4s pra completar os 10.0s de intervalo.
        self.ws._tick_minion_waves(8.3)
        self.assertEqual(len(self.ws._minion_spawn_queue), 0,
            "2ª wave de 'top' não deveria disparar antes de completar wave_interval_s")
        self.ws._tick_minion_waves(0.2)  # total 13.1s — passa dos 13.0s
        self.assertEqual(len(self.ws._minion_spawn_queue), 4,
            "2ª wave de 'top' deveria disparar em first_wave_delay_s + wave_interval_s "
            "(3.0 + 10.0 = 13.0s) — o atraso inicial se propaga pras waves seguintes")

    def test_2_lanes_do_mesmo_time_disparam_independente_lane_id(self):
        """Pedido do usuário (30/07/2026): separar rotas por lane
        (top/mid/bot) — sem `lane_id` distinguindo, 2 lanes do MESMO
        time colidiam na mesma chave de wave timer e só a primeira
        jamais disparava."""
        lane_top = self._lane(faction="arena_time_a", spawn=(130, 374),
                              target=(130, 390), interval=6.0)
        lane_top["lane_id"] = "top"
        lane_bot = self._lane(faction="arena_time_a", spawn=(132, 374),
                              target=(132, 390), interval=6.0)
        lane_bot["lane_id"] = "bot"
        self.ws._minion_lanes[self.ws._map_file] = [lane_top, lane_bot]
        self.ws._activate_minion_lanes(self.ws._map_file)
        # 220 ticks = 11.0s: cobre a 1ª wave de "bot" (dispara aos 7.5s —
        # +1.5s de atraso de grupo sobre "top", ver _LANE_GROUP_STAGGER_S,
        # top e bot dos 2 times disparam em GRUPOS separados desde
        # 03/08/2026) + escalonamento de nascimento (4×0.5s=1.5s → 9.0s) +
        # margem, sem alcançar a 2ª wave de "top" (dispararia aos 12.0s).
        run_ticks(self.ws, 220)
        minions = [eid for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertEqual(len(minions), 8, "as 2 lanes (top/bot) deveriam ter disparado, 4 cada")

    def test_level_do_minion_e_a_media_do_time_recalculada_a_cada_wave(self):
        """Pedido do usuário: level do minion = média do time, recalculada
        a CADA wave (acompanha level up dos players, não trava no valor
        de quando o combate começou)."""
        from engine.components import Faction, CharacterStats
        p1 = spawn_player(self.ws, "lvl1", 130, 374)
        p2 = spawn_player(self.ws, "lvl2", 130, 375)
        self.ws.world.add_component(p1, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(p2, Faction(faction_id="arena_time_a"))
        self.ws.world.get_component(p1, CharacterStats).level = 4
        self.ws.world.get_component(p2, CharacterStats).level = 8
        # Ambos precisam estar na MESMA instância/mapa da lane.
        from engine.components import MapLocation
        self.ws.world.add_component(p1, MapLocation(self.ws._map_file))
        self.ws.world.add_component(p2, MapLocation(self.ws._map_file))

        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0, )]
        self.ws._minion_lanes[self.ws._map_file][0]["level"] = 1  # fallback estático (não deveria ser usado)
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)

        from engine.components import EntityIdentity
        minions = [self.ws.world.get_component(eid, EntityIdentity)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions)
        for ident in minions:
            self.assertEqual(ident.level, 6, "level deveria ser a média do time (4+8)/2=6, não o fallback")

    def test_level_cai_no_fallback_estatico_sem_player_do_time_na_instancia(self):
        self.ws._minion_lanes[self.ws._map_file] = [self._lane(interval=6.0)]
        self.ws._minion_lanes[self.ws._map_file][0]["level"] = 3
        self.ws._activate_minion_lanes(self.ws._map_file)
        run_ticks(self.ws, 200)
        from engine.components import EntityIdentity
        minions = [self.ws.world.get_component(eid, EntityIdentity)
                  for eid, _ in self.ws.world.get_entities_with(Minion)]
        self.assertTrue(minions)
        for ident in minions:
            self.assertEqual(ident.level, 3, "sem player do time, deveria usar o level estático da lane")


class TestMinionDeathXpGold(unittest.TestCase):
    """XP/ouro da própria definição (nunca MOB_TABLE), split de grupo
    automático, is_ranged correto no payload — via WorldServer real
    (precisa do death handler completo)."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10)

    def _kill_minion(self, minion_eid):
        from engine.components import PendingDeath
        cs = self.ws.world.get_component(minion_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(minion_eid, PendingDeath(killer_entity_id=self.player_eid))
        self.ws._mob_damage_log[minion_eid] = {self.player_eid: 999}

    def test_xp_vem_da_propria_definicao_do_minion(self):
        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        self._kill_minion(eid)
        run_ticks(self.ws, 1)
        from engine.components import CharacterStats
        char = self.ws.world.get_component(self.player_eid, CharacterStats)
        self.assertGreaterEqual(char.current_xp, MINION_TABLE["minion_melee"]["xp_reward"])

    def test_xp_do_minion_escala_com_o_level(self):
        """Pedido do usuário (30/07/2026): já que o level do minion virou
        dinâmico (média do time), o XP passa a escalar com ele
        (xp_reward × level), não mais flat.

        Compara XP GANHO TOTAL (não `current_xp` cru): um level-up
        consome `xp_to_next_level` de `current_xp`, deixando só o
        excedente — comparar `current_xp` direto contra `expected` quebra
        sempre que o total cruzar um threshold de level (achado real:
        `xp_reward` de `minion_melee` tunado pelo usuário deixou 45×3=135
        > BASE_XP=100, e 135 not>= 135 falhava mesmo com o cálculo
        CORRETO de XP, só por causa do level-up no meio)."""
        from engine.components import CharacterStats
        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route, level=3)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        char = self.ws.world.get_component(self.player_eid, CharacterStats)
        level_before = char.level
        self._kill_minion(eid)
        run_ticks(self.ws, 1)
        xp_consumido_em_levelups = sum(CharacterStats.xp_for_level(lvl)
                                       for lvl in range(level_before, char.level))
        xp_ganho_total = xp_consumido_em_levelups + char.current_xp
        expected = MINION_TABLE["minion_melee"]["xp_reward"] * 3
        self.assertGreaterEqual(xp_ganho_total, expected,
                               f"XP deveria ser xp_reward*level ({expected}), não flat")

    def test_gold_de_instancia_vai_pro_killer_dentro_da_progressao_normalizada(self):
        """Pedido do usuário (01/08/2026): "quando um oponente morre deve
        ir automaticamente pro inventário do player que o matou". Fora de
        uma instância de progressão normalizada, matar minion continua
        SEM dar ouro (coins=0 fixo, comportamento de sempre —
        test_morte_de_minion_nao_da_ouro_nem_loot abaixo cobre isso).
        Dentro da instância, `Minion.gold_min/gold_max` (novo,
        content/minion_definitions.py) vira ouro de instância pro
        `killer_eid`."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import Wallet
        enter_normalized_progression(self.ws, self.player_eid)
        wallet = self.ws.world.get_component(self.player_eid, Wallet)
        gold_antes = wallet.gold

        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        self._kill_minion(eid)
        run_ticks(self.ws, 1)

        self.assertGreater(wallet.gold, gold_antes,
                           "gold de instância deveria ter subido pro killer")
        self.assertLessEqual(wallet.gold - gold_antes, MINION_TABLE["minion_melee"]["gold_max"])
        self.assertGreaterEqual(wallet.gold - gold_antes, MINION_TABLE["minion_melee"]["gold_min"])

    def test_xp_de_minion_e_por_proximidade_sem_precisar_bater(self):
        """Pedido do usuário (02/08/2026): "a xp não é para ser necessário
        bater no mob, se o mob morrer perto dos players, tem que ser
        dividido entre os players, sem necessidade de dar um hit sequer
        nos minions" — estilo LoL. Mata o minion SEM nenhum damage_log e
        SEM killer_entity_id apontando pro player (ninguém bateu) — o
        player, só por estar perto, ainda deve ganhar XP de instância."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import CharacterStats, PendingDeath
        enter_normalized_progression(self.ws, self.player_eid)
        char = self.ws.world.get_component(self.player_eid, CharacterStats)
        xp_antes = char.current_xp

        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        # Mata SEM registrar dano do player nenhum (killer_entity_id=-1,
        # damage_log vazio) — simula "morreu perto de mim, mas quem bateu
        # foi outra coisa (torre/outro minion)".
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(eid, PendingDeath(killer_entity_id=-1))
        run_ticks(self.ws, 1)

        self.assertGreater(char.current_xp, xp_antes,
                           "player perto do minion deveria ganhar xp mesmo sem bater")

    def test_xp_de_minion_por_proximidade_e_dividido_entre_players_perto(self):
        route = [(11, 10), (12, 10)]

        def _mk(sid, tx, ty):
            eid = spawn_player(self.ws, sid, tx, ty)
            from server.instance_progression import enter_normalized_progression
            enter_normalized_progression(self.ws, eid)
            return eid

        p2 = _mk("p2", 12, 10)
        from engine.components import CharacterStats
        char1 = self.ws.world.get_component(self.player_eid, CharacterStats)
        char2 = self.ws.world.get_component(p2, CharacterStats)
        from server.instance_progression import enter_normalized_progression
        enter_normalized_progression(self.ws, self.player_eid)
        xp1_antes, xp2_antes = char1.current_xp, char2.current_xp

        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        from engine.components import PendingDeath
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(eid, PendingDeath(killer_entity_id=-1))
        run_ticks(self.ws, 1)

        self.assertGreater(char1.current_xp, xp1_antes)
        self.assertGreater(char2.current_xp, xp2_antes)
        base_xp = MINION_TABLE["minion_melee"]["xp_reward"]  # level 1 -> mult 1
        self.assertLess(char1.current_xp - xp1_antes, base_xp,
                        "xp dividido entre 2 players perto deveria ser menor que o total")

    def test_dano_de_minion_nao_dilui_xp_do_player(self):
        """Pedido do usuário (01/08/2026): "a experiência deve ser
        compartilhada somente entre players, os minions não devem receber
        xp". Antes do fix, o dano de um atacante NÃO-player no
        damage_log ainda entrava no denominador da proporção — um mob
        50% morto por minion + 50% por player dava só METADE do XP pro
        player. Aqui: 2º "atacante" no damage_log é um minion aliado (não
        é player) — o player devia receber o XP CHEIO mesmo assim."""
        from engine.components import CharacterStats, PendingDeath
        ally_minion_eid = create_minion(self.ws.world, 9, 10, "minion_melee",
                                        faction_id="arena_time_a", route=[(9, 10)])
        self.ws.world.add_component(ally_minion_eid, MapLocation(self.ws._map_file))

        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)

        cs = self.ws.world.get_component(eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(eid, PendingDeath(killer_entity_id=self.player_eid))
        # Dano dividido 50/50 entre o player e um minion aliado (não-player).
        self.ws._mob_damage_log[eid] = {self.player_eid: 50, ally_minion_eid: 50}
        run_ticks(self.ws, 1)

        char = self.ws.world.get_component(self.player_eid, CharacterStats)
        self.assertGreaterEqual(char.current_xp, MINION_TABLE["minion_melee"]["xp_reward"],
                               "player deveria ganhar o XP CHEIO, sem diluição pelo dano do minion aliado")

    def test_morte_de_minion_nao_da_ouro_nem_loot(self):
        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        self._kill_minion(eid)
        run_ticks(self.ws, 1)
        loot = self.ws._corpses.get(max(self.ws._corpses.keys()))
        self.assertIsNotNone(loot)
        self.assertEqual(loot["coins"], 0)
        self.assertEqual(loot["items"], [])

    def test_payload_de_spawn_reporta_is_ranged_correto_por_tipo(self):
        route = [(11, 10), (12, 10)]
        melee_eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                                  faction_id="monstros_hostis", route=route)
        ranged_eid = create_minion(self.ws.world, 13, 10, "minion_ranged",
                                   faction_id="monstros_hostis", route=route)
        self.ws.world.add_component(melee_eid, MapLocation(self.ws._map_file))
        self.ws.world.add_component(ranged_eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        self.assertFalse(self.ws.get_entity_spawn_data(melee_eid)["is_ranged"])
        self.assertTrue(self.ws.get_entity_spawn_data(ranged_eid)["is_ranged"])

    def test_minion_sincroniza_como_enemy_com_faction_correta(self):
        route = [(11, 10), (12, 10)]
        eid = create_minion(self.ws.world, 11, 10, "minion_melee",
                            faction_id="arena_time_a", route=route)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 1)
        self.assertIn(eid, self.ws._mob_eids)
        data = self.ws.get_entity_spawn_data(eid)
        self.assertEqual(data["kind"], "enemy")
        self.assertEqual(data["faction"], "arena_time_a")


class TestThrottleOfMinionFightingRecalcOnTargetMove(unittest.TestCase):
    """Fase 4 (06/08/2026, mesmo achado da Fase 3 mas em MinionSystem/
    FIGHTING): o destino passado pra `_walk_toward` era a tile ATUAL do
    alvo, recalculada fresh todo tick — `_walk_toward` descarta
    current_path/path_recalc_timer IMEDIATO toda vez que o destino muda,
    ignorando o backoff normal (0.8s) quase todo tick com o alvo em
    movimento. Com uma wave inteira FIGHTING alvos em movimento ao mesmo
    tempo (cenário de PC), isso dispara scan de occupied_tiles + A* todo
    tick que qualquer alvo muda de tile. _TARGET_RECALC_MIN_TICKS_BY_TIER
    limita a frequência que o destino é de fato atualizado."""

    def setUp(self):
        self.world, self.pf = _make_world_with_services()
        self.ms = MinionSystem(self.world, get_tilemap_for_map=lambda mf: None,
                               get_pathfinding_for_map=lambda mf: self.pf)
        RELATIONSHIP[("time_a", "time_b")] = "hostil"
        RELATIONSHIP[("time_b", "time_a")] = "hostil"

        # Minion parado em (5,5), FIGHTING um alvo hostil a 3 tiles —
        # dentro do aggro_range_tiles=4 de minion_melee (nunca perde o
        # alvo por _target_still_valid), fora do attack_range_tiles=1
        # (precisa passar pela perseguição, não pelo branch de ataque).
        route = [(5, 5), (5, 30)]
        self.minion_eid = create_minion(self.world, 5, 5, "minion_melee",
                                        faction_id="time_a", route=route)
        self.world.add_component(self.minion_eid, MapLocation(_MAP))

        self.target_eid = create_enemy(self.world, 5, 8, race="Lobo", faction="time_b")
        self.world.add_component(self.target_eid, MapLocation(_MAP))
        self.target_pos = self.world.get_component(self.target_eid, Position)
        self.target_tm = self.world.get_component(self.target_eid, TileMovement)

        m = self.world.get_component(self.minion_eid, Minion)
        m.state = "FIGHTING"
        m.current_target_eid = self.target_eid
        m.path_dest = None
        m._target_recalc_last_tick = -1

        from engine.components import EnemyTier
        tier = self.world.get_component(self.minion_eid, EnemyTier)
        self.assertIsNotNone(tier, "create_minion deveria atribuir EnemyTier")
        tier.tier = "normal"
        self.interval = MinionSystem._TARGET_RECALC_MIN_TICKS_BY_TIER["normal"]

    def _move_target(self, tx: int, ty: int) -> None:
        from engine.tileset import TILE_SIZE
        self.target_tm.current_tile_x = self.target_tm.target_tile_x = tx
        self.target_tm.current_tile_y = self.target_tm.target_tile_y = ty
        self.target_tm.is_moving = False
        self.target_pos.x = tx * TILE_SIZE + TILE_SIZE // 2
        self.target_pos.y = ty * TILE_SIZE + TILE_SIZE // 2

    def _replan_ticks(self, n: int) -> list:
        """Chama update() n vezes, movendo o alvo pra uma tile NOVA (nunca
        repetida dentro da janela, `x = 1 + i % 9`, `y = 8` — chebyshev
        3-4 do minion parado em (5,5): dentro do aggro_range=4, fora do
        attack_range=1) ANTES de cada uma. Precisa ser sempre distinta da
        anterior — um padrão que alterna entre só 2 tiles pode "voltar"
        pro MESMO destino que `minion.path_dest` ficou congelado (throttle
        ainda não permitiu atualizar), fazendo o código enxergar como "sem
        mudança" e produzir um falso replan (achado real escrevendo este
        teste). Nunca sai do raio de aggro (senão o minion perde o alvo e
        volta pra ADVANCING, mesma armadilha do detect_radius da Fase 3).
        Devolve os tick_count em que _target_recalc_last_tick mudou."""
        m = self.world.get_component(self.minion_eid, Minion)
        replans = []
        for i in range(n):
            self._move_target(1 + (i % 9), 8)
            before = m._target_recalc_last_tick
            self.ms.update(dt=0.05, tick_count=1000 + i)
            if m._target_recalc_last_tick != before:
                replans.append(1000 + i)
        return replans

    def test_normal_throttla_recalculo_por_mudanca_de_tile(self):
        replans = self._replan_ticks(self.interval * 2 + 1)
        self.assertGreaterEqual(len(replans), 2,
            "precisa ver pelo menos 2 replans nesta janela pra provar o gap")
        gaps = [b - a for a, b in zip(replans, replans[1:])]
        for gap in gaps:
            self.assertGreaterEqual(gap, self.interval,
                f"minion normal (intervalo={self.interval}) não deveria trocar "
                f"de destino antes do intervalo completo (gap real={gap})")

    def test_boss_nunca_throttla_recalculo(self):
        from engine.components import EnemyTier
        tier = self.world.get_component(self.minion_eid, EnemyTier)
        tier.tier = "boss"
        replans = self._replan_ticks(5)
        self.assertEqual(len(replans), 5,
            "boss (intervalo=1) deveria trocar de destino TODO tick, nunca throttlado")

    def test_alvo_parado_nao_precisa_de_throttle(self):
        # Alvo no MESMO tile sempre — path_dest nunca muda de verdade,
        # então não há "troca de destino" pra throttlar: _walk_dest ==
        # _new_dest em todo tick, _target_recalc_last_tick acompanha.
        m = self.world.get_component(self.minion_eid, Minion)
        self._move_target(5, 8)
        for i in range(3):
            self.ms.update(dt=0.05, tick_count=2000 + i)
        self.assertEqual(m._target_recalc_last_tick, 2002,
            "alvo parado não precisa de throttle — destino já é sempre o mesmo")


if __name__ == "__main__":
    unittest.main()
