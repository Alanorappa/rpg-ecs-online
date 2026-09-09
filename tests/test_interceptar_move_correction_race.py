"""
tests/test_interceptar_move_correction_race.py — 09/09/2026, bug real
relatado pelo usuário: usar Interceptar às vezes fazia o dash inteiro
acontecer e DEPOIS "desfazia" (personagem volta pra tile de antes),
como se o servidor tivesse corrigido a posição. Mais comum perto de
parede/torre.

Causa raiz (achada via 3 investigações paralelas — cliente, servidor,
histórico §84/§87): `server/session.py::_handle_move` manda uma
correção de posição pro PRÓPRIO cliente sempre que `move_player()`
rejeita um MOVE, usando `WorldServer.get_tile_pos()` ->
`TileMovement.current_tile_x/y`. Esse campo só é atualizado quando o
TWEEN do dash COMPLETA (`TileMovementSystem`, `progress>=1.0`) — durante
o dash ele sempre aponta pra tile de ANTES dele. Quando a rejeição é
especificamente por dash em andamento (`tm.is_dash and tm.is_moving`,
guard de `WorldServer.move_player` desde §87), a correção mandada
carrega essa posição stale; se ela chegar ao cliente DEPOIS da animação
do dash já ter terminado localmente, o cliente aplica sem saber que é
velha e desfaz um dash legítimo — não é reordenação de pacote (TCP
garante ordem), é conteúdo stale num pacote corretamente ordenado.

Fix: `_handle_move` não manda NENHUMA correção quando a rejeição é por
essa causa específica — o dash já tem confirmação própria (via
`consume_skill_position_corrections`/`_dispatch_tick_deltas`), não
precisa (e não deve) de correção nenhuma vinda daqui. Rejeições por
OUTRO motivo (parede, orçamento de velocidade) continuam mandando
correção normalmente — comportamento antigo preservado pra esses casos.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from tests.test_session import FakeWS, get_msgs_of_type
from engine.components import TileMovement
from server.session import Session, SessionManager
from shared.messages import MsgType


def _make_session(ws, session_id: str, eid: int) -> tuple:
    fake_ws = FakeWS()
    session = Session(fake_ws, session_id)
    session.entity_id = eid
    session.authenticated = True
    return session, fake_ws


class TestInterceptarDashMoveRejectionSendsNoCorrection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.mgr = SessionManager(self.ws)
        self.session, self.fake_ws = _make_session(self.ws, "s1", self.eid)

    async def test_rejeicao_por_dash_em_andamento_nao_manda_correcao(self):
        tm = self.ws.world.get_component(self.eid, TileMovement)
        tm.is_dash = True
        tm.is_moving = True
        tm.target_tile_x, tm.target_tile_y = 125, 374  # destino do dash "em curso"

        await self.mgr._handle_move(self.session, {"tx": 129, "ty": 374}, ts=0)

        corrections = get_msgs_of_type(self.fake_ws, MsgType.ENTITY_MOVE)
        self.assertEqual(corrections, [],
                         "rejeição por dash em andamento nunca deveria mandar correção "
                         "de posição — o dash já tem confirmação própria")
        # Confirma que o guard real de move_player (§87) segue intacto —
        # nada mudou de posição.
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (130, 374))

    async def test_rejeicao_por_parede_continua_mandando_correcao(self):
        """Regressão: rejeição por OUTRO motivo (não dash) precisa continuar
        mandando a correção de sempre — só o caso de dash-em-andamento é
        especial."""
        tm = self.ws.world.get_component(self.eid, TileMovement)
        self.assertFalse(tm.is_dash)

        # Tile bem distante o bastante pra exceder o orçamento tempo×velocidade
        # (MOVE_SPEED_TOLERANCE) e ser rejeitado por esse motivo, não por dash.
        await self.mgr._handle_move(self.session, {"tx": 130, "ty": 800}, ts=0)

        corrections = get_msgs_of_type(self.fake_ws, MsgType.ENTITY_MOVE)
        self.assertEqual(len(corrections), 1,
                         "rejeição por motivo comum (não dash) deveria continuar "
                         "mandando a correção de posição de sempre")
        self.assertEqual((corrections[0]["tx"], corrections[0]["ty"]), (130, 374))

    async def test_move_aceito_continua_mandando_broadcast_normal(self):
        """Regressão: MOVE aceito não deve ser afetado pelo fix (esse
        caminho já usa _broadcast_aoi_except, nunca session.send direto,
        então o próprio remetente não deveria receber nada aqui)."""
        await self.mgr._handle_move(self.session, {"tx": 129, "ty": 374}, ts=0)

        corrections = get_msgs_of_type(self.fake_ws, MsgType.ENTITY_MOVE)
        self.assertEqual(corrections, [],
                         "MOVE aceito nunca ecoa ENTITY_MOVE de volta pro próprio remetente")
        tm = self.ws.world.get_component(self.eid, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (129, 374))


if __name__ == "__main__":
    unittest.main()
