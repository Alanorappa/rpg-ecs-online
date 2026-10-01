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


if __name__ == "__main__":
    unittest.main()
