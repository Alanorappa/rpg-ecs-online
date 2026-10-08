"""
tests/test_network_login_hash.py
NetworkClient.login() aceita password_hash pronto (08/10/2026, ver
CLAUDE.md) — bug real relatado pelo usuário: logar no Launcher e ter
que digitar usuário/senha DE NOVO no jogo, porque o Launcher só tem o
hash (nunca a senha crua) e não tinha como repassar isso pro login do
jogo sem hashear de novo (o que daria um hash ERRADO — hash do hash).
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.network import NetworkClient
from shared.auth_utils import hash_password
from shared.messages import MsgType


class TestLoginComPasswordHashPronto(unittest.TestCase):

    def setUp(self):
        self.net = NetworkClient()

    def test_sem_password_hash_hasheia_a_senha_crua_como_sempre(self):
        self.net.login("alice", "senha123")
        mt, payload = self.net.outbox.get_nowait()
        self.assertEqual(mt, MsgType.LOGIN)
        self.assertEqual(payload["password"], hash_password("senha123"))

    def test_com_password_hash_usa_ele_direto_sem_re_hashear(self):
        ph = hash_password("senha123")
        self.net.login("alice", "", password_hash=ph)
        mt, payload = self.net.outbox.get_nowait()
        self.assertEqual(mt, MsgType.LOGIN)
        self.assertEqual(payload["password"], ph,
                         "deveria usar o hash pronto, nunca hashear de novo (hash do hash)")

    def test_password_hash_pronto_nao_e_igual_a_hashear_a_string_vazia(self):
        """Regressão específica: se o código re-hasheasse `password=""` por
        engano (ignorando password_hash), o valor enviado seria
        hash_password(""), nunca o hash real da senha."""
        ph = hash_password("senha123")
        self.net.login("alice", "", password_hash=ph)
        _, payload = self.net.outbox.get_nowait()
        self.assertNotEqual(payload["password"], hash_password(""))

    def test_guarda_o_hash_usado_pra_re_login_automatico(self):
        ph = hash_password("senha123")
        self.net.login("alice", "", password_hash=ph)
        self.assertEqual(self.net._login_password_hash, ph)


if __name__ == "__main__":
    unittest.main()
