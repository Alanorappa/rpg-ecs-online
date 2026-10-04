"""
tests/test_mid_session_reconnect.py — bug real relatado pelo usuário
(03/10/2026): quando a conexão cai NO MEIO do jogo, o jogo mostra
"Reconectando", mas depois de "reconectar" o cliente fica num estado
zumbi — personagem anda, mas mob nunca agroa, outros players ficam
congelados.

Causa raiz: `client/network.py::NetworkClient._connect_and_run` reconecta
o socket e reenvia LOGIN com as credenciais salvas sozinho — o servidor
autentica de novo e manda AUTH_OK, mas NADA no cliente reenviava
SELECT_CHARACTER depois disso (esse passo só existia no fluxo de login
INICIAL, `ui/login_screen.py`, que tem seu próprio loop separado, nunca
reusado numa reconexão no meio do jogo). Sem SELECT_CHARACTER, a sessão
nova no servidor nunca vira uma entidade no mundo de novo — zero
AOI_UPDATE/COMBAT_RESULT chega, mesmo com o socket "conectado". O
timeout de 30s de `game.py::run()` eventualmente desiste e libera a
tela mesmo sem sincronizar nada ("entra no jogo com personagem
padrão") — daí a aparência de "reconectou" sem nunca ter reconectado
de verdade.

Fix: `_handle_msg_login_ok` guarda `self._selected_char_id` (o id do
personagem, já presente no payload). `_handle_msg_auth_ok` (novo,
registrado em `_NET_DISPATCH_NAMES` — antes AUTH_OK não tinha handler
NENHUM na gameplay) reenvia `SELECT_CHARACTER` automaticamente quando
esse id já existe — exatamente o sinal de que isto é uma reconexão NO
MEIO do jogo (login inicial nunca passa por este dispatch).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import unittest

from client.network_handlers import NetworkHandlers


class _FakeNet:
    def __init__(self):
        self.sent: list = []

    def send(self, msg_type, payload: dict) -> None:
        self.sent.append((msg_type, payload))


class _Fixture(NetworkHandlers):
    def __init__(self):
        self._net = _FakeNet()


class TestAuthOkReenviaSelectCharacterNaReconexao(unittest.TestCase):

    def test_reenvia_select_character_com_o_char_id_guardado(self):
        from shared.messages import MsgType
        fx = _Fixture()
        fx._selected_char_id = 7   # setado por _handle_msg_login_ok num login anterior

        fx._handle_msg_auth_ok({"characters": [{"id": 7, "name": "Fulano"}]})

        self.assertEqual(fx._net.sent, [(MsgType.SELECT_CHARACTER, {"char_id": 7})])

    def test_nao_reenvia_nada_sem_char_id_guardado(self):
        """Guard defensivo -- na prática, AUTH_OK do login/seleção INICIAL
        nunca passa por este dispatch (ui/login_screen.py tem seu próprio
        loop, GameEngine ainda nem existe nesse momento), mas o handler
        não deve fazer nada destrutivo se for chamado sem char_id."""
        fx = _Fixture()
        fx._handle_msg_auth_ok({"characters": []})
        self.assertEqual(fx._net.sent, [])

    def test_char_id_negativo_nao_reenvia(self):
        fx = _Fixture()
        fx._selected_char_id = -1
        fx._handle_msg_auth_ok({})
        self.assertEqual(fx._net.sent, [])

    def test_sem_net_nao_crasha(self):
        fx = _Fixture()
        fx._selected_char_id = 3
        fx._net = None
        fx._handle_msg_auth_ok({})   # não deve lançar


class TestAuthOkRegistradoNoDispatch(unittest.TestCase):
    """Garante que AUTH_OK de fato chega em _handle_msg_auth_ok durante a
    gameplay -- sem esta entrada, a mensagem seria silenciosamente
    ignorada por _handle_net_message (mesmo bug, causa diferente)."""

    def test_auth_ok_mapeado_no_dispatch(self):
        from shared.messages import MsgType
        self.assertEqual(
            NetworkHandlers._NET_DISPATCH_NAMES.get(MsgType.AUTH_OK),
            "_handle_msg_auth_ok")

    def test_dispatch_chama_o_handler_de_verdade(self):
        from shared.messages import MsgType
        fx = _Fixture()
        fx._selected_char_id = 9
        fx._handle_net_message(MsgType.AUTH_OK, {"characters": []})
        self.assertEqual(fx._net.sent, [(MsgType.SELECT_CHARACTER, {"char_id": 9})])


if __name__ == "__main__":
    unittest.main(verbosity=2)
