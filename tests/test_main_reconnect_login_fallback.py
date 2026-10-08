"""
tests/test_main_reconnect_login_fallback.py — 29/09/2026, bug real
relatado pelo usuário: depois de "Deslogar" dentro do jogo, se a
reconexão automática (main.py::_connect_and_login) não confirmar
AUTH_OK a tempo (ex: servidor reiniciando bem naquele momento), a
tela de seleção de personagem aparecia vazia, sem erro nenhum —
indistinguível de "esta conta não tem personagem nenhum" (não é perda
de dado: o servidor sempre teve os personagens, confirmado por log
real). Fix: `_connect_and_login` passa a devolver (None, []) nesses
casos de falha (em vez de (net, []) fingindo sucesso), e
`_reconnect_or_login_screen` cai na tela de login normal (com erro +
usuário pré-preenchido) em vez de seguir pra seleção de personagem.

Testa só `_connect_and_login` (lógica pura, sem pygame) — o fallback
visual (`_reconnect_or_login_screen` chamando `ui.login_screen.run`)
é comportamento de UI, verificado manualmente pelo usuário.
"""
import os, sys, unittest
from unittest.mock import patch
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import main
from shared.messages import MsgType


class _FakeNetworkClient:
    """Substitui client.network.NetworkClient nos testes — connect()/
    login()/poll()/disconnect() controlados pelo teste, sem socket real."""

    # Controlado por classe (setado pelo teste antes de instanciar).
    CONNECT_SUCCEEDS = True
    POLL_SEQUENCE: list = []   # lista de listas de (MsgType, payload, seq, ts)

    def __init__(self, host="localhost", port=8765, use_tls=False):
        self.host, self.port, self.use_tls = host, port, use_tls
        self.connected = False
        self.disconnected = False
        self._poll_i = 0

    def connect(self):
        self.connected = self.CONNECT_SUCCEEDS

    def login(self, username, password, password_hash=""):
        self._logged_in_as = (username, password)
        self._logged_in_hash = password_hash

    def poll(self):
        if self._poll_i < len(self.POLL_SEQUENCE):
            batch = self.POLL_SEQUENCE[self._poll_i]
            self._poll_i += 1
            return batch
        return []

    def disconnect(self):
        self.disconnected = True
        self.connected = False


class TestConnectAndLoginFalhaNuncaFingeSucesso(unittest.TestCase):
    """`_connect_and_login` deve devolver (None, []) — nunca (net, [])
    — em qualquer cenário de falha, pra nunca ser confundido com uma
    conta legitimamente sem personagem."""

    def setUp(self):
        self._patcher = patch("client.network.NetworkClient", _FakeNetworkClient)
        self._patcher.start()
        self._sleep_patcher = patch("time.sleep", lambda *_a, **_k: None)
        self._sleep_patcher.start()
        _FakeNetworkClient.CONNECT_SUCCEEDS = True
        _FakeNetworkClient.POLL_SEQUENCE = []

    def tearDown(self):
        self._patcher.stop()
        self._sleep_patcher.stop()

    def test_sucesso_devolve_net_e_char_list_reais(self):
        _FakeNetworkClient.POLL_SEQUENCE = [
            [(MsgType.AUTH_OK, {"characters": [{"id": 1, "name": "Fulano"}]}, 0, 0)],
        ]
        net, char_list = main._connect_and_login("h", 1, "u", "p")
        self.assertIsNotNone(net)
        self.assertEqual(char_list, [{"id": 1, "name": "Fulano"}])

    def test_conta_legitimamente_sem_personagem_nao_e_tratada_como_falha(self):
        """AUTH_OK chega de verdade, só que com lista vazia (conta nova) —
        isso É sucesso, `net` não pode vir None."""
        _FakeNetworkClient.POLL_SEQUENCE = [
            [(MsgType.AUTH_OK, {"characters": []}, 0, 0)],
        ]
        net, char_list = main._connect_and_login("h", 1, "u", "p")
        self.assertIsNotNone(net)
        self.assertEqual(char_list, [])

    def test_conexao_nunca_estabelece_devolve_none(self):
        _FakeNetworkClient.CONNECT_SUCCEEDS = False
        with patch("time.time", side_effect=_make_fake_clock()):
            net, char_list = main._connect_and_login("h", 1, "u", "p")
        self.assertIsNone(net)
        self.assertEqual(char_list, [])

    def test_timeout_sem_auth_ok_devolve_none_nao_lista_vazia_fingindo_sucesso(self):
        """Reprodução do bug real: conecta ok, login enviado, mas o
        servidor nunca responde AUTH_OK (reiniciando, lento, etc.) —
        antes do fix isso devolvia (net, []), indistinguível de conta
        vazia; agora devolve (None, [])."""
        _FakeNetworkClient.POLL_SEQUENCE = []  # nunca chega nada
        with patch("time.time", side_effect=_make_fake_clock()):
            net, char_list = main._connect_and_login("h", 1, "u", "p")
        self.assertIsNone(net)
        self.assertEqual(char_list, [])

    def test_login_error_devolve_none(self):
        _FakeNetworkClient.POLL_SEQUENCE = [
            [(MsgType.LOGIN_ERROR, {"reason": "invalid_credentials"}, 0, 0)],
        ]
        net, char_list = main._connect_and_login("h", 1, "u", "p")
        self.assertIsNone(net)
        self.assertEqual(char_list, [])


def _make_fake_clock():
    """Gera valores crescentes o suficiente pra vencer os 2 deadlines de
    10s de `_connect_and_login` sem esperar de verdade (time.sleep já
    virou no-op no setUp, mas o teste ainda precisa que `time.time()`
    avance sozinho pra sair dos loops `while time.time() < deadline`)."""
    state = {"t": 0.0}
    def _next(*_a, **_k):
        state["t"] += 0.5
        return state["t"]
    return _next


if __name__ == "__main__":
    unittest.main()
