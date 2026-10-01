"""
tests/test_portal.py — Sistema de portais (seleção de destino + cast de
5s, ver server/portal_processor.py). Cobre: validação em 3 passos de
request_portal_enter (portal existe no mapa, player fisicamente no
tile, dest_index no range), teleporte só ao completar o cast (nunca
antes), cancelamento por movimento, e a checagem de segurança central
do desenho — ZONE_CHANGE_REQ direto (bypass de PORTAL_ENTER_REQ) nunca
alcança um destino de portal sem passar pela validação nova.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile

PORTAL_ID = "portal_teste_1"
PORTAL_X, PORTAL_Y = 138, 370


class TestPortalRequestValidation(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", PORTAL_X, PORTAL_Y)

    def test_portal_inexistente_e_recusado(self):
        reason = self.ws.request_portal_enter(self.a, "portal_nao_existe", 0)
        self.assertEqual(reason, "invalid")
        self.assertNotIn(self.a, self.ws._portal_casts)

    def test_dest_index_fora_do_range_e_recusado(self):
        reason = self.ws.request_portal_enter(self.a, PORTAL_ID, 99)
        self.assertEqual(reason, "invalid_destination")
        self.assertNotIn(self.a, self.ws._portal_casts)

    def test_dest_index_negativo_e_recusado(self):
        reason = self.ws.request_portal_enter(self.a, PORTAL_ID, -1)
        self.assertEqual(reason, "invalid_destination")

    def test_player_fora_do_tile_do_portal_e_recusado(self):
        set_entity_tile(self.ws, self.a, PORTAL_X + 3, PORTAL_Y)
        reason = self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.assertEqual(reason, "not_on_portal")
        self.assertNotIn(self.a, self.ws._portal_casts)

    def test_sucesso_cria_entrada_em_portal_casts_com_duracao_certa(self):
        reason = self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.assertIsNone(reason)
        self.assertIn(self.a, self.ws._portal_casts)
        cast = self.ws._portal_casts[self.a]
        self.assertEqual(cast["portal_id"], PORTAL_ID)
        self.assertEqual(cast["dest_index"], 0)
        self.assertEqual(cast["elapsed"], 0.0)
        self.assertEqual(cast["duration"], self.ws.PORTAL_CAST_DURATION_S)

    def test_ja_em_cast_recusa_segundo_pedido(self):
        self.assertIsNone(self.ws.request_portal_enter(self.a, PORTAL_ID, 0))
        reason = self.ws.request_portal_enter(self.a, PORTAL_ID, 1)
        self.assertEqual(reason, "already_casting")


class TestPortalTickAndTeleport(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", PORTAL_X, PORTAL_Y)
        self.assertIsNone(self.ws.request_portal_enter(self.a, PORTAL_ID, 0))

    def test_nao_teleporta_antes_do_tempo(self):
        self.ws._tick_portal_casts(dt=1.0)
        self.assertIn(self.a, self.ws._portal_casts)
        self.assertEqual(self.ws.consume_portal_teleport_events(), [])

    def test_teleporta_so_ao_completar_o_cast(self):
        duration = self.ws.PORTAL_CAST_DURATION_S
        # Ticks somados abaixo da duração: nunca completa.
        self.ws._tick_portal_casts(dt=duration - 0.1)
        self.assertIn(self.a, self.ws._portal_casts)
        self.assertEqual(self.ws.consume_portal_teleport_events(), [])

        # Último tick fecha o cast.
        self.ws._tick_portal_casts(dt=0.2)
        self.assertNotIn(self.a, self.ws._portal_casts)
        events = self.ws.consume_portal_teleport_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eid"], self.a)
        self.assertEqual(events[0]["to_map"], "maps/map_1.csv")
        self.assertEqual(events[0]["target_x"], 210)
        self.assertEqual(events[0]["target_y"], 480)

        from engine.components import TileMovement
        tm = self.ws.world.get_component(self.a, TileMovement)
        self.assertEqual(tm.current_tile_x, 210)
        self.assertEqual(tm.current_tile_y, 480)

    def test_consume_limpa_o_buffer(self):
        self.ws._tick_portal_casts(dt=self.ws.PORTAL_CAST_DURATION_S)
        self.assertEqual(len(self.ws.consume_portal_teleport_events()), 1)
        self.assertEqual(self.ws.consume_portal_teleport_events(), [])


class TestPortalCancelOnMove(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", PORTAL_X, PORTAL_Y)
        self.assertIsNone(self.ws.request_portal_enter(self.a, PORTAL_ID, 0))

    def test_move_player_durante_cast_cancela(self):
        self.ws.move_player("s1", PORTAL_X + 1, PORTAL_Y)
        self.assertNotIn(self.a, self.ws._portal_casts)
        events = self.ws.consume_portal_cast_cancel_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eid"], self.a)
        self.assertEqual(events[0]["reason"], "moved")

    def test_cast_cancelado_nunca_completa_mesmo_esperando(self):
        self.ws.move_player("s1", PORTAL_X + 1, PORTAL_Y)
        self.ws._tick_portal_casts(dt=self.ws.PORTAL_CAST_DURATION_S + 1.0)
        self.assertEqual(self.ws.consume_portal_teleport_events(), [])

    def test_cancel_portal_cast_sem_cast_ativo_e_noop(self):
        self.ws.move_player("s1", PORTAL_X + 1, PORTAL_Y)
        self.ws.consume_portal_cast_cancel_events()
        had_cast = self.ws.cancel_portal_cast(self.a, "moved")
        self.assertFalse(had_cast)
        self.assertEqual(self.ws.consume_portal_cast_cancel_events(), [])


class TestPortalZoneChangeReqBypassBlocked(unittest.TestCase):
    """Checagem de segurança central do desenho: um cliente malicioso que
    manda ZONE_CHANGE_REQ direto (pulando PORTAL_ENTER_REQ) nunca alcança
    um destino de portal por essa via nova — o fluxo de portal nunca
    escreve em `_portal_casts` a partir de ZONE_CHANGE_REQ, e o destino de
    portal só é acessível através de `request_portal_enter`, que valida
    posição+id+índice antes de aceitar qualquer coisa."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", PORTAL_X, PORTAL_Y)

    def test_transfer_player_direto_nao_passa_pelo_catalogo_de_portal(self):
        # transfer_player é o mecanismo BAIXO NÍVEL que tanto o ZONE_CHANGE_REQ
        # antigo quanto o cast de portal usam por baixo — ele em si nunca
        # consulta _portal_gates_by_map, confirmando que a validação de
        # destino-de-portal só existe na camada de cima (request_portal_enter).
        import inspect
        src = inspect.getsource(self.ws.transfer_player)
        self.assertNotIn("_portal_gates_by_map", src,
                         "transfer_player não deve saber nada sobre portais — "
                         "validação de destino é exclusiva de request_portal_enter")

    def test_sem_pedido_de_portal_nao_ha_cast_pendente(self):
        # Nenhum PORTAL_ENTER_REQ foi mandado — _portal_casts deve
        # permanecer vazio independente de qualquer outra ação do player.
        self.ws.move_player("s1", PORTAL_X + 1, PORTAL_Y)
        self.assertEqual(self.ws._portal_casts, {})
        self.ws._tick_portal_casts(dt=999.0)
        self.assertEqual(self.ws.consume_portal_teleport_events(), [])


class TestPortalAnimBroadcastEvents(unittest.TestCase):
    """Animação do TILE do portal (01/10/2026), visível por AOI pra
    qualquer sessão perto — não confundir com PORTAL_CAST_START/CANCEL
    (privados, só pro dono do cast). Cobre: só o 1º caster de um portal
    dispara START (2º+ caster simultâneo não reinicia a animação pra
    quem já via); só o ÚLTIMO caster saindo cedo dispara STOP; conclusão
    NATURAL nunca dispara STOP (cliente já sabia a duração real); chegada
    num tile que é um portal cadastrado dispara ARRIVE; chegada num tile
    comum não dispara nada."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", PORTAL_X, PORTAL_Y)
        self.b = spawn_player(self.ws, "s2", PORTAL_X, PORTAL_Y)

    def test_primeiro_caster_dispara_start_com_dados_do_portal(self):
        self.assertIsNone(self.ws.request_portal_enter(self.a, PORTAL_ID, 0))
        events = self.ws.consume_portal_anim_start_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["portal_id"], PORTAL_ID)
        self.assertEqual(events[0]["x"], PORTAL_X)
        self.assertEqual(events[0]["y"], PORTAL_Y)
        self.assertEqual(events[0]["duration"], self.ws.PORTAL_CAST_DURATION_S)

    def test_segundo_caster_simultaneo_nao_reinicia_start(self):
        self.assertIsNone(self.ws.request_portal_enter(self.a, PORTAL_ID, 0))
        self.ws.consume_portal_anim_start_events()
        self.assertIsNone(self.ws.request_portal_enter(self.b, PORTAL_ID, 1))
        self.assertEqual(self.ws.consume_portal_anim_start_events(), [],
                         "2º caster no MESMO portal não deve re-disparar START")

    def test_cancelar_com_outro_caster_ainda_ativo_nao_dispara_stop(self):
        self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.ws.request_portal_enter(self.b, PORTAL_ID, 1)
        self.ws.consume_portal_anim_start_events()
        self.ws.cancel_portal_cast(self.a, "moved")
        self.assertEqual(self.ws.consume_portal_anim_stop_events(), [],
                         "ainda tem o B castando — animação não deve parar pros outros")

    def test_cancelar_ultimo_caster_dispara_stop(self):
        self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.ws.consume_portal_anim_start_events()
        self.ws.cancel_portal_cast(self.a, "moved")
        events = self.ws.consume_portal_anim_stop_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["portal_id"], PORTAL_ID)
        self.assertEqual(events[0]["x"], PORTAL_X)
        self.assertEqual(events[0]["y"], PORTAL_Y)

    def test_conclusao_natural_nunca_dispara_stop(self):
        self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.ws.consume_portal_anim_start_events()
        self.ws._tick_portal_casts(dt=self.ws.PORTAL_CAST_DURATION_S)
        self.assertEqual(self.ws.consume_portal_anim_stop_events(), [],
                         "conclusão natural não deve mandar STOP — cliente já sabia a duração real")
        self.assertNotIn((PORTAL_ID,), [(k[1],) for k in self.ws._portal_anim_casters])

    def test_chegada_em_portal_cadastrado_dispara_arrive(self):
        # Destino "Vila Sul" do portal_teste_1 é exatamente o tile do
        # portal_teste_2 (210,480) — ver maps/map_1_entities.json.
        self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.ws._tick_portal_casts(dt=self.ws.PORTAL_CAST_DURATION_S)
        events = self.ws.consume_portal_anim_arrive_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["portal_id"], "portal_teste_2")
        self.assertEqual(events[0]["x"], 210)
        self.assertEqual(events[0]["y"], 480)

    def test_chegada_em_tile_comum_nao_dispara_arrive(self):
        # Destino "Caverna Oeste" não tem nenhum portal_gate cadastrado
        # naquele tile — nada deve animar lá.
        self.ws.request_portal_enter(self.a, PORTAL_ID, 1)
        self.ws._tick_portal_casts(dt=self.ws.PORTAL_CAST_DURATION_S)
        self.assertEqual(self.ws.consume_portal_anim_arrive_events(), [])

    def test_disconnect_cancela_cast_e_dispara_stop(self):
        self.ws.request_portal_enter(self.a, PORTAL_ID, 0)
        self.ws.consume_portal_anim_start_events()
        had_cast = self.ws.cancel_portal_cast(self.a, "disconnect")
        self.assertTrue(had_cast)
        self.assertNotIn(self.a, self.ws._portal_casts)
        events = self.ws.consume_portal_anim_stop_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["portal_id"], PORTAL_ID)


class TestPortalClientAnimationClock(unittest.TestCase):
    """Regressão (01/10/2026, bug real relatado pelo usuário: "estragou
    as animações de ataque do personagem, não funcionam mais"). Causa
    raiz: `PortalHandlers._handle_msg_portal_cast_start` setava
    `TileMovement.anim_attack_started_ms` com `time.time()*1000`
    (relógio de PAREDE, epoch — número gigante, ~1.7 trilhão) enquanto
    TODO o resto do sistema de animação de ataque usa
    `pygame.time.get_ticks()` (ver `engine/character_animation.py::
    trigger_attack_anim`, chamado com `pygame.time.get_ticks()` de
    `client/remote_entity_handlers.py::_apply_combat_result`). Como
    epoch é sempre MUITO maior que ticks do pygame (que zeram a cada
    sessão), a subtração em `_attack_loop_still_active` dava um número
    NEGATIVO enorme — sempre "menor que a duração", bloqueando
    PERMANENTEMENTE qualquer golpe novo depois do 1º cast de portal."""

    def setUp(self):
        import pygame
        pygame.init()
        from engine.world import World
        from engine.components import TileMovement
        self.world = World()
        self.player_entity = self.world.create_entity()
        self.tm = TileMovement()
        self.tm.facing = "down"
        self.world.add_component(self.player_entity, self.tm)

    def _make_engine(self):
        from client.portal_handlers import PortalHandlers
        class _FakeEngine(PortalHandlers):
            pass
        eng = _FakeEngine()
        eng.world = self.world
        eng.player_entity = self.player_entity
        return eng

    def test_anim_attack_started_ms_usa_ticks_do_pygame_nao_relogio_de_parede(self):
        import pygame
        eng = self._make_engine()
        before = pygame.time.get_ticks()
        eng._handle_msg_portal_cast_start({"portal_id": "x", "dest_index": 0, "duration": 5.0})
        after = pygame.time.get_ticks()
        self.assertTrue(
            before <= self.tm.anim_attack_started_ms <= after + 50,
            "anim_attack_started_ms tem que estar na escala de pygame.time.get_ticks() "
            "(dezenas de milhares), não de time.time()*1000 (epoch, trilhões) -- "
            f"valor gravado: {self.tm.anim_attack_started_ms}, ticks agora: {after}")

    def test_golpe_normal_nao_fica_bloqueado_para_sempre_apos_cast(self):
        import pygame
        from engine.character_animation import trigger_attack_anim, _attack_loop_still_active
        eng = self._make_engine()
        eng._handle_msg_portal_cast_start({"portal_id": "x", "dest_index": 0, "duration": 5.0})
        # `now_ms` vem de um pygame.time.get_ticks() INDEPENDENTE (igual
        # a todo chamador real — client/remote_entity_handlers.py::
        # _apply_combat_result), NUNCA derivado de
        # anim_attack_started_ms — senão o teste mediria só a diferença
        # relativa ao próprio valor (potencialmente já errado) em vez de
        # reproduzir o bug de verdade (relógio de parede fica sempre no
        # "passado" em relação a QUALQUER tick independente do pygame).
        now_ms = pygame.time.get_ticks() + 6000
        self.assertFalse(_attack_loop_still_active(self.tm, now_ms))
        trigger_attack_anim(self.tm, "guerreiro", "", now_ms)
        self.assertEqual(self.tm.anim_attack_state, "attack",
                         "golpe normal precisa conseguir tocar depois do cast de portal terminar")

    def test_cancelar_cast_zera_loop_duration_e_destrava_golpe_na_hora(self):
        import pygame
        from engine.character_animation import trigger_attack_anim
        eng = self._make_engine()
        eng._handle_msg_portal_cast_start({"portal_id": "x", "dest_index": 0, "duration": 5.0})
        eng._cancel_portal_cast_local()
        self.assertEqual(self.tm.anim_attack_loop_duration_ms, 0)
        # Golpe IMEDIATAMENTE após cancelar (sem esperar nada) já
        # funciona — now_ms independente, mesmo motivo do teste acima.
        trigger_attack_anim(self.tm, "guerreiro", "", pygame.time.get_ticks() + 10)
        self.assertEqual(self.tm.anim_attack_state, "attack")


class TestPortalAnimAnchorResolution(unittest.TestCase):
    """Regressão (01/10/2026, bug real relatado pelo usuário: "a
    animação do portal ainda não funciona"). Causa raiz: o servidor
    manda a posição do GATE (onde o jogador fica em pé pra interagir),
    mas `ui/tile_render_system.py` desenha e consulta a animação pela
    posição do OBJETO no `object_matrix` — que pode ser (e, nos 2
    portais de teste reais do mapa, É) um tile DIFERENTE do gate (ambos
    a (-1,+1) do gate). Sem traduzir uma posição pra outra, o estado de
    animação era escrito numa chave que o renderer nunca consultava —
    a animação nunca aparecia, mesmo com tudo mais funcionando."""

    def setUp(self):
        from engine.tileset import OBJECT_MAPPING, TileType, PORTAL_ACTIVATE_SHEET_BY_TILE_ID
        self._orig_mapping = dict(OBJECT_MAPPING)
        self._orig_activate = dict(PORTAL_ACTIVATE_SHEET_BY_TILE_ID)
        OBJECT_MAPPING["P"] = TileType(name="TestePortal", color=(0, 0, 0),
                                       sprite_name="test_portal_obj")
        PORTAL_ACTIVATE_SHEET_BY_TILE_ID["test_portal_obj"] = "fake_sheet"

    def tearDown(self):
        from engine.tileset import OBJECT_MAPPING, PORTAL_ACTIVATE_SHEET_BY_TILE_ID
        from engine import portal_anim_state
        OBJECT_MAPPING.clear(); OBJECT_MAPPING.update(self._orig_mapping)
        PORTAL_ACTIVATE_SHEET_BY_TILE_ID.clear(); PORTAL_ACTIVATE_SHEET_BY_TILE_ID.update(self._orig_activate)
        portal_anim_state.clear_all()

    @staticmethod
    def _matrix(obj_x: int, obj_y: int, size: int = 10) -> list:
        rows = [["." for _ in range(size)] for _ in range(size)]
        rows[obj_y][obj_x] = "P"
        return rows

    def test_acha_objeto_deslocado_do_gate(self):
        from client.portal_handlers import _find_portal_anim_anchor
        # Gate em (5,5), objeto em (4,6) -- (-1,+1), igual ao caso real.
        matrix = self._matrix(obj_x=4, obj_y=6)
        self.assertEqual(_find_portal_anim_anchor(matrix, 5, 5), (4, 6))

    def test_sem_objeto_por_perto_cai_pro_proprio_gate(self):
        from client.portal_handlers import _find_portal_anim_anchor
        matrix = self._matrix(obj_x=0, obj_y=0)  # longe demais de (5,5)
        self.assertEqual(_find_portal_anim_anchor(matrix, 5, 5), (5, 5))

    def test_build_portal_tiles_resolve_anchor_de_verdade(self):
        from client.portal_handlers import PortalHandlers
        class _FakeEngine(PortalHandlers):
            pass
        eng = _FakeEngine()
        matrix = self._matrix(obj_x=4, obj_y=6)
        spawn_points = {"portal_gates": [{"id": "px", "x": 5, "y": 5, "destinations": []}]}
        eng._build_portal_tiles(spawn_points, matrix)
        self.assertEqual(eng._portal_anim_anchor[(5, 5)], (4, 6))

    def test_handler_de_rede_escreve_no_anchor_nao_no_gate(self):
        import time
        from client.portal_handlers import PortalHandlers
        from engine import portal_anim_state
        class _FakeEngine(PortalHandlers):
            pass
        eng = _FakeEngine()
        matrix = self._matrix(obj_x=4, obj_y=6)
        spawn_points = {"portal_gates": [{"id": "px", "x": 5, "y": 5, "destinations": []}]}
        eng._build_portal_tiles(spawn_points, matrix)
        eng._handle_msg_portal_anim_start({"portal_id": "px", "x": 5, "y": 5, "duration": 5.0})
        now_ms = int(time.time() * 1000)
        self.assertIsNone(portal_anim_state.get_active(5, 5, now_ms),
                          "não deve escrever na posição do GATE")
        self.assertIsNotNone(portal_anim_state.get_active(4, 6, now_ms),
                             "deve escrever na posição real do OBJETO (anchor)")


class TestPortalLightPunchesNightLayer(unittest.TestCase):
    """Pedido do usuário (02/10/2026, duas rodadas): a animação do
    portal é uma "iluminação" azul, precisa furar a camada de
    escuridão da noite igual à luz de lampião/item — E a FORMA do furo
    tem que vir do desenho real da animação (canal alpha do frame
    ativo), não de um círculo sintético centrado no objeto (1ª versão,
    corrigida nesta rodada). Cobre:
    `engine.portal_anim_state.active_positions()` só lista tiles de
    verdade ativos; `TileRenderSystem.get_portal_light_sources()` gera
    a entrada (frame + posição de tela EXATA, igual à do desenho
    visível) no formato que `DayNightSystem.render()` espera — nunca
    mistura isso com a lista passada pro RenderSystem."""

    def setUp(self):
        import pygame
        pygame.init()
        from engine.tileset import OBJECT_MAPPING, TileType, PORTAL_ACTIVATE_SHEET_BY_TILE_ID
        from engine import portal_anim_state
        from ui.tile_sprite_manager import TILE_SPRITES
        self._orig_mapping = dict(OBJECT_MAPPING)
        self._orig_activate = dict(PORTAL_ACTIVATE_SHEET_BY_TILE_ID)
        self._orig_frames = dict(TILE_SPRITES._portal_event_frames)
        OBJECT_MAPPING["P"] = TileType(name="TestePortal", color=(0, 0, 0),
                                       sprite_name="test_portal_obj",
                                       sprite_px_w=32, sprite_px_h=32)
        PORTAL_ACTIVATE_SHEET_BY_TILE_ID["test_portal_obj"] = "fake_sheet"
        # Injeta o frame direto no cache (sem precisar de um PNG real em
        # disco) — mesma chave que get_portal_event_frame consultaria
        # depois de resolver anim_file via PORTAL_ACTIVATE_SHEET_BY_TILE_ID.
        self._fake_frame = pygame.Surface((32, 32), pygame.SRCALPHA)
        self._fake_frame.fill((110, 170, 255, 200))
        TILE_SPRITES._portal_event_frames[("test_portal_obj", "activate")] = [self._fake_frame]
        portal_anim_state.clear_all()

    def tearDown(self):
        from engine.tileset import OBJECT_MAPPING, PORTAL_ACTIVATE_SHEET_BY_TILE_ID
        from engine import portal_anim_state
        from ui.tile_sprite_manager import TILE_SPRITES
        OBJECT_MAPPING.clear(); OBJECT_MAPPING.update(self._orig_mapping)
        PORTAL_ACTIVATE_SHEET_BY_TILE_ID.clear(); PORTAL_ACTIVATE_SHEET_BY_TILE_ID.update(self._orig_activate)
        TILE_SPRITES._portal_event_frames.clear()
        TILE_SPRITES._portal_event_frames.update(self._orig_frames)
        portal_anim_state.clear_all()

    def test_active_positions_so_lista_quem_esta_ativo_agora(self):
        import time
        from engine import portal_anim_state
        now_ms = int(time.time() * 1000)
        portal_anim_state.start_activate(5, 5, now_ms, 5000.0)
        self.assertEqual(portal_anim_state.active_positions(now_ms), [(5, 5)])
        # bem depois da duração: já não conta mais.
        self.assertEqual(portal_anim_state.active_positions(now_ms + 6000), [])

    def test_get_portal_light_sources_devolve_o_frame_real_na_posicao_do_desenho(self):
        import time, pygame
        from engine.entity_factory import create_tilemap
        from engine.world import World
        from engine.components import Tilemap
        from engine import portal_anim_state
        from ui.tile_render_system import TileRenderSystem

        world = World()
        terrain = ["." * 10 for _ in range(10)]
        objects = [["."] * 10 for _ in range(10)]
        objects[5][5] = "P"
        create_tilemap(world, terrain, objects, None)

        now_ms = int(time.time() * 1000)
        portal_anim_state.start_activate(5, 5, now_ms, 5000.0)

        trs = TileRenderSystem(world, pygame.Surface((64, 64)))
        lights = trs.get_portal_light_sources(0, 0)
        self.assertEqual(len(lights), 1)
        light = lights[0]
        self.assertIs(light["portal_light_frame"], self._fake_frame,
                      "tem que ser o FRAME de verdade (sua forma/alpha é a luz), "
                      "nunca uma geometria sintética solta")
        self.assertNotIn("player_light", light, "nunca marcar como player_light -- são fontes distintas")
        self.assertNotIn("light_source", light, "formato novo não usa mais geometria genérica")
        # Posição de tela tem que ser EXATAMENTE onde o sprite/overlay é
        # desenhado (mesma fórmula de get_world_objects()/
        # render_static_objects()) -- senão o furo na escuridão aparece
        # num lugar diferente de onde o brilho é visível.
        tile_size = world.get_component(
            next(iter(world.get_entities_with(Tilemap)))[0], Tilemap).tile_size
        expected_scr_x = 5 * tile_size
        expected_scr_y = 6 * tile_size - 32
        self.assertAlmostEqual(light["screen_x"], expected_scr_x)
        self.assertAlmostEqual(light["screen_y"], expected_scr_y)

    def test_get_portal_light_sources_vazio_sem_animacao_ativa(self):
        import pygame
        from engine.entity_factory import create_tilemap
        from engine.world import World
        from ui.tile_render_system import TileRenderSystem

        world = World()
        terrain = ["." * 10 for _ in range(10)]
        objects = [["."] * 10 for _ in range(10)]
        objects[5][5] = "P"
        create_tilemap(world, terrain, objects, None)

        trs = TileRenderSystem(world, pygame.Surface((64, 64)))
        self.assertEqual(trs.get_portal_light_sources(0, 0), [])

    def test_get_portal_light_sources_vazio_sem_folha_cadastrada(self):
        """Fallback gracioso: animação ativa, mas sem frame resolvível
        (arte ainda não cadastrada/sem sheet) -- não deve gerar entrada
        nenhuma, nunca quebrar."""
        import time, pygame
        from engine.entity_factory import create_tilemap
        from engine.world import World
        from engine import portal_anim_state
        from ui.tile_sprite_manager import TILE_SPRITES
        from ui.tile_render_system import TileRenderSystem

        del TILE_SPRITES._portal_event_frames[("test_portal_obj", "activate")]

        world = World()
        terrain = ["." * 10 for _ in range(10)]
        objects = [["."] * 10 for _ in range(10)]
        objects[5][5] = "P"
        create_tilemap(world, terrain, objects, None)

        now_ms = int(time.time() * 1000)
        portal_anim_state.start_activate(5, 5, now_ms, 5000.0)

        trs = TileRenderSystem(world, pygame.Surface((64, 64)))
        self.assertEqual(trs.get_portal_light_sources(0, 0), [])

    def test_get_portal_frame_light_mask_assa_intensidade_no_rgb(self):
        """`_get_portal_frame_light_mask` precisa ASSAR a intensidade no
        RGB (branco × alpha/255 × intensidade) -- BLEND_RGBA_ADD não
        pondera por alpha de origem, então um blit direto do frame
        criaria um furo de borda dura em vez do brilho suave da arte."""
        import pygame
        from ui.day_night_system import DayNightSystem
        from engine.world import World

        dns = DayNightSystem(World(), -1)
        frame = pygame.Surface((4, 4), pygame.SRCALPHA)
        frame.fill((110, 170, 255, 255))      # pixel totalmente opaco
        frame.set_at((0, 0), (110, 170, 255, 0))   # pixel totalmente transparente

        mask_full = dns._get_portal_frame_light_mask(frame, 100)
        mask_half = dns._get_portal_frame_light_mask(frame, 50)

        # Pixel opaco: intensidade 100% vira branco cheio; 50% vira a metade.
        self.assertEqual(tuple(mask_full.get_at((1, 1)))[:3], (255, 255, 255))
        self.assertEqual(tuple(mask_half.get_at((1, 1)))[:3], (128, 128, 128))
        # Pixel transparente na arte: nenhuma luz, em qualquer intensidade.
        self.assertEqual(tuple(mask_full.get_at((0, 0)))[:3], (0, 0, 0))

    def test_render_player_light_nao_quebra_com_raio_fracionario(self):
        """Regressão (02/10/2026, erro real relatado pelo usuário) —
        continua valendo como defesa GENÉRICA mesmo depois da luz do
        portal deixar de usar radius_tiles (ver classe acima, agora usa
        a forma do frame direto): `radius_tiles` fracionário × `TILE_SIZE`
        virava um `radius_px` FLOAT, usado como dimensão de array numpy/
        Surface em `_get_light_add_gradient`, que exige int — crashava
        com `TypeError: 'float' object cannot be interpreted as an
        integer`. Simula qualquer luz de ITEM futura com radius_tiles
        fracionário pra garantir que o round() defensivo continua
        protegendo esse caminho. Força o relógio pra meia-noite (senão
        o early-return de "dia pleno" nunca chegaria no código que
        crashava)."""
        import pygame
        from engine.world import World
        from ui.day_night_system import DayNightSystem
        import ui.day_night_system as dns_mod

        orig_offset = dns_mod._debug_time_offset_s
        try:
            dns_mod.jump_to_fraction(0.0)   # meia-noite -- garante noite real
            world = World()
            dns = DayNightSystem(world, -1)
            dns.world_surf = pygame.Surface((200, 200))
            fake_light = {
                "screen_x": 100, "screen_y": 100,
                "light_source": (0, 0, 0, 0, 90, 2.5),   # raio FRACIONÁRIO
                "player_light": True,
            }
            dns.render(world_objects=[fake_light])   # não deve lançar TypeError
        finally:
            dns_mod.set_debug_time_offset(orig_offset)


class TestPortalCastBarClearsOnZoneChange(unittest.TestCase):
    """Bug real relatado pelo usuário (02/10/2026): a barra de cast do
    portal só sumia depois que o jogador se movia — `_update_portal_state`
    só limpa `_portal_cast_active` em 2 casos (movimento local,
    PORTAL_CAST_CANCEL do servidor), e a conclusão NATURAL do cast
    (chegou ao destino) nunca era nenhum dos dois: o ZONE_CHANGE
    chegava, o jogador teleportava, mas a barra continuava na tela até
    o 1º passo no mapa novo. Fix: `_handle_msg_zone_change` cancela
    qualquer cast de portal ativo incondicionalmente, mesmo padrão dos
    outros resets que já rodam ali (resultado de BG, contexto de PvP,
    câmera livre)."""

    def _make_engine(self):
        from client.network_handlers import NetworkHandlers
        from client.portal_handlers import PortalHandlers
        from engine.world import World
        from engine.components import TileMovement

        class _FakeEngine(NetworkHandlers, PortalHandlers):
            pass

        eng = _FakeEngine()
        eng.world = World()
        eng.player_entity = eng.world.create_entity()
        tm = TileMovement()
        eng.world.add_component(eng.player_entity, tm)
        return eng, tm

    def test_zone_change_limpa_cast_ativo_mesmo_sem_mover(self):
        eng, tm = self._make_engine()
        eng._portal_cast_active = {"elapsed": 1.0, "duration": 5.0,
                                   "portal_id": "x", "dest_index": 0}
        tm.anim_attack_state = "portal"
        tm.anim_attack_loop_duration_ms = 5000

        # map_file vazio -- exercita só os resets leves (inclui o fix),
        # sem precisar simular o carregamento de mapa inteiro de
        # _do_transition (fora do escopo deste teste).
        eng._handle_msg_zone_change({"map_file": "", "target_x": 0, "target_y": 0})

        self.assertIsNone(eng._portal_cast_active,
                          "ZONE_CHANGE tem que limpar a barra de cast na hora, sem esperar movimento")
        self.assertEqual(tm.anim_attack_state, "")
        self.assertEqual(tm.anim_attack_loop_duration_ms, 0)

    def test_zone_change_sem_cast_ativo_e_no_op_seguro(self):
        eng, _tm = self._make_engine()
        eng._handle_msg_zone_change({"map_file": "", "target_x": 0, "target_y": 0})
        self.assertIsNone(eng._portal_cast_active)


class TestPortalModalSuppressedOnTeleportArrival(unittest.TestCase):
    """Bug real relatado pelo usuário (02/10/2026): chegar num portal
    DE OUTRO portal (teleporte) abria o modal de seleção na hora, como
    se o jogador tivesse ANDADO até lá — os 2 portais de teste se
    miram (destino de um é exatamente o tile do outro). Fix:
    `_suppress_portal_modal_here()`, chamado por `game.py::
    _do_transition()` logo após reposicionar o jogador por teleporte,
    marca o tile de chegada como "já dispensado" — só suprime a
    CHEGADA; sair e voltar ANDANDO depois abre normal."""

    def _make_engine(self, gate_x: int, gate_y: int):
        from client.portal_handlers import PortalHandlers
        from engine.world import World
        from engine.components import TileMovement

        class _FakeEngine(PortalHandlers):
            def _any_modal_open(self):
                return False

        eng = _FakeEngine()
        eng.world = World()
        eng.player_entity = eng.world.create_entity()
        tm = TileMovement()
        tm.current_tile_x = tm.target_tile_x = gate_x
        tm.current_tile_y = tm.target_tile_y = gate_y
        tm.is_moving = False
        eng.world.add_component(eng.player_entity, tm)
        eng.portal_tiles = {
            (gate_x, gate_y): {"id": "px", "x": gate_x, "y": gate_y, "destinations": []},
        }
        return eng, tm

    def test_chegada_por_teleporte_nao_abre_modal(self):
        eng, _tm = self._make_engine(10, 10)
        eng._suppress_portal_modal_here()   # mesma chamada que _do_transition faz
        eng._update_portal_state(0.05)
        self.assertIsNone(eng._portal_modal_open)

    def test_sem_suprimir_abriria_normalmente(self):
        """Controle negativo -- confirma que o teste acima não passa
        "à toa" (sem a chamada de supressão, o comportamento ANTIGO —
        abrir na hora — continua existindo pra quem ANDA até lá)."""
        eng, _tm = self._make_engine(10, 10)
        eng._update_portal_state(0.05)
        self.assertIsNotNone(eng._portal_modal_open)

    def test_sair_e_voltar_andando_depois_da_supressao_abre_normal(self):
        eng, tm = self._make_engine(10, 10)
        eng._suppress_portal_modal_here()
        eng._update_portal_state(0.05)
        self.assertIsNone(eng._portal_modal_open, "ainda suprimido logo após chegar")

        # Anda pra um tile vizinho (fora do portal) -- reseta a supressão.
        tm.current_tile_x = tm.target_tile_x = 11
        eng._update_portal_state(0.05)

        # Volta ANDANDO pro tile do portal -- agora deve abrir normal.
        tm.current_tile_x = tm.target_tile_x = 10
        eng._update_portal_state(0.05)
        self.assertIsNotNone(eng._portal_modal_open,
                             "voltar ANDANDO depois de sair deve abrir o modal normalmente")


if __name__ == "__main__":
    unittest.main()
