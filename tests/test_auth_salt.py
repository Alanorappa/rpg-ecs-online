"""
tests/test_auth_salt.py — Salt por conta + upgrade transparente (item C1,
PROBLEMAS_ARQUITETURA.md §11). Usa banco temporário isolado; restaura
DB_PATH/_schema_ensured no teardown pra não contaminar os testes de sessão
(que usam o banco dev real).
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import server.auth as auth


class TestAuthSalt(unittest.TestCase):

    def setUp(self):
        self._orig_db     = auth.DB_PATH
        self._orig_schema = auth._schema_ensured
        auth.DB_PATH          = os.path.join(tempfile.mkdtemp(), "test_auth.db")
        auth._schema_ensured  = False
        auth.init_db()
        self.ch = auth._hash("minha_senha_123")   # client_hash (o que o cliente manda)

    def tearDown(self):
        auth.DB_PATH         = self._orig_db
        auth._schema_ensured = self._orig_schema

    def _row(self, username):
        with sqlite3.connect(auth.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT password_hash, salt FROM accounts WHERE username=?",
                (username,)).fetchone()

    def test_conta_nova_e_salted(self):
        self.assertTrue(auth._register_account_sync("novo_user", self.ch))
        row = self._row("novo_user")
        self.assertTrue(row["salt"], "conta nova sem salt")
        self.assertNotEqual(row["password_hash"], self.ch,
                            "banco guardou o client_hash cru")

    def test_login_certo_passa_errado_nao(self):
        auth._register_account_sync("novo_user", self.ch)
        self.assertIsNotNone(auth._authenticate_sync("novo_user", self.ch))
        self.assertIsNone(auth._authenticate_sync("novo_user", auth._hash("errada")))

    def test_conta_legada_migra_no_login(self):
        with sqlite3.connect(auth.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO accounts (username, password_hash, salt) VALUES ('legado', ?, NULL)",
                (self.ch,))
        self.assertIsNotNone(auth._authenticate_sync("legado", self.ch),
                             "login legado falhou")
        row = self._row("legado")
        self.assertTrue(row["salt"], "conta legada não migrou")
        self.assertNotEqual(row["password_hash"], self.ch)
        # pós-migração continua logando; errada continua falhando
        self.assertIsNotNone(auth._authenticate_sync("legado", self.ch))
        self.assertIsNone(auth._authenticate_sync("legado", auth._hash("errada")))

    def test_hash_vazado_do_banco_nao_autentica(self):
        auth._register_account_sync("novo_user", self.ch)
        stored = self._row("novo_user")["password_hash"]
        self.assertIsNone(auth._authenticate_sync("novo_user", stored),
                          "dump do banco autenticou (pass-the-hash)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
