"""
tests/test_auth_salt.py — Salt por conta + upgrade transparente (item C1,
PROBLEMAS_ARQUITETURA.md §11). Usa schema Postgres isolado (07/09/2026,
§123 — migração de SQLite pra PostgreSQL/asyncpg), restaurado no
asyncTearDown pra não contaminar os testes de sessão (que usam o schema
"public", mesmo banco dev real).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import server.auth as auth
from tests.helpers import isolate_auth_schema, restore_auth_schema


class TestAuthSalt(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_auth_salt")
        self.ch = auth._hash("minha_senha_123")   # client_hash (o que o cliente manda)

    async def asyncTearDown(self):
        await restore_auth_schema(self._orig_schema)

    async def _row(self, username):
        pool = await auth._get_pool()
        return await pool.fetchrow(
            "SELECT password_hash, salt FROM accounts WHERE username=$1", username)

    async def test_conta_nova_e_salted(self):
        self.assertTrue(await auth._register_account("novo_user", self.ch))
        row = await self._row("novo_user")
        self.assertTrue(row["salt"], "conta nova sem salt")
        self.assertNotEqual(row["password_hash"], self.ch,
                            "banco guardou o client_hash cru")

    async def test_login_certo_passa_errado_nao(self):
        await auth._register_account("novo_user", self.ch)
        self.assertIsNotNone(await auth.authenticate("novo_user", self.ch))
        self.assertIsNone(await auth.authenticate("novo_user", auth._hash("errada")))

    async def test_conta_legada_migra_no_login(self):
        pool = await auth._get_pool()
        await pool.execute(
            "INSERT INTO accounts (username, password_hash, salt) VALUES ('legado', $1, NULL)",
            self.ch)
        self.assertIsNotNone(await auth.authenticate("legado", self.ch),
                             "login legado falhou")
        row = await self._row("legado")
        self.assertTrue(row["salt"], "conta legada não migrou")
        self.assertNotEqual(row["password_hash"], self.ch)
        # pós-migração continua logando; errada continua falhando
        self.assertIsNotNone(await auth.authenticate("legado", self.ch))
        self.assertIsNone(await auth.authenticate("legado", auth._hash("errada")))

    async def test_hash_vazado_do_banco_nao_autentica(self):
        await auth._register_account("novo_user", self.ch)
        row = await self._row("novo_user")
        self.assertIsNone(await auth.authenticate("novo_user", row["password_hash"]),
                          "dump do banco autenticou (pass-the-hash)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
