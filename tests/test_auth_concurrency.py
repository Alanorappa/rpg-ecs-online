"""
tests/test_auth_concurrency.py — correção de `server/auth.py` sob
concorrência, pós-migração pra PostgreSQL/asyncpg (07/09/2026, §123).

Substitui `tests/test_auth_write_lock.py` (removido): aquele arquivo
testava que `_write_lock`/`_identity_lock` (locks em memória de UM
processo, existiam só porque SQLite aceita 1 escritor por vez) NUNCA
deixavam 2 execuções sobreporem no tempo. Isso não existe mais — Postgres
aceita múltiplos escritores reais (MVCC), e um lock de processo NUNCA
protegeria de verdade contra múltiplos PROCESSOS (sharding futuro) mesmo
que ainda existisse. O que importa agora não é "nunca sobrepõe", é
"nunca corrompe o resultado": N registros concorrentes com usernames
distintos todos têm que suceder; 2 concorrentes com o MESMO username novo
têm que resultar em exatamente 1 sucesso (garantido pelo índice único do
banco, não por lock de processo).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import server.auth as auth
from tests.helpers import isolate_auth_schema, restore_auth_schema


class TestAuthConcurrency(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_auth_concurrency")

    async def asyncTearDown(self):
        await restore_auth_schema(self._orig_schema)

    async def test_registers_concorrentes_usernames_distintos_todos_sucedem(self):
        """N `register()` concorrentes com usernames DIFERENTES — sem lock
        nenhum, Postgres processa em paralelo de verdade; o que importa é
        o resultado final: todas as N contas existem, nenhuma se perde."""
        N = 12
        results = await asyncio.gather(*[
            auth.register(f"loadtest_bot_{i}", auth._hash(f"senha{i}"))
            for i in range(N)
        ])
        self.assertTrue(all(results), "algum register concorrente falhou (deveria ter sucesso, usernames distintos)")
        for i in range(N):
            self.assertIsNotNone(await auth._get_account_id(f"loadtest_bot_{i}"),
                                 f"conta loadtest_bot_{i} não foi criada")

    async def test_registers_concorrentes_MESMO_username_novo_so_1_sucede(self):
        """07/09/2026, §123 — a corrida REAL (2 registros do MESMO
        username NOVO ao mesmo tempo) agora é fechada pelo índice único
        `accounts.username` do próprio banco, não por um lock de processo.
        Exatamente 1 dos N concorrentes tem que suceder — nunca 2 (dado
        corrompido) nem 0 (regressão de disponibilidade)."""
        N = 8
        ch = auth._hash("senha_disputada")
        results = await asyncio.gather(*[
            auth.register("username_disputado", ch) for _ in range(N)
        ])
        self.assertEqual(sum(1 for r in results if r), 1,
                         f"esperado exatamente 1 sucesso entre {N} registros concorrentes "
                         f"do MESMO username novo, veio {sum(1 for r in results if r)}")
        self.assertIsNotNone(await auth._get_account_id("username_disputado"))

    async def test_authenticate_concorrente_funciona(self):
        """`authenticate` (LOGIN, predominantemente leitura) nunca foi
        serializado por nenhum lock de propósito — continua não sendo,
        e múltiplos logins concorrentes da mesma conta todos sucedem."""
        ch = auth._hash("minha_senha")
        await auth._register_account("user_login_test", ch)

        results = await asyncio.gather(*[
            auth.authenticate("user_login_test", ch) for _ in range(5)
        ])
        self.assertTrue(all(r is not None for r in results), "algum login concorrente falhou")

    async def test_register_conta_existente_usa_fast_path_sem_escrever(self):
        """Fast-path (§120, preservado na migração pra Postgres): SELECT
        de existência ANTES do INSERT — caso comum de verdade é reconexão
        de conta já existente (todo bot/cliente manda REGISTER de novo a
        cada LOGIN). Prova aqui que `_register_account` (caminho de
        ESCRITA de verdade) nunca é chamado quando a conta já existe."""
        ch = auth._hash("senha_existente")
        await auth._register_account("conta_ja_existe", ch)

        orig = auth._register_account
        called = {"n": 0}

        async def spy(username, password):
            called["n"] += 1
            return await orig(username, password)

        auth._register_account = spy
        try:
            result = await auth.register("conta_ja_existe", ch)
        finally:
            auth._register_account = orig

        self.assertFalse(result, "register() devia recusar username já existente")
        self.assertEqual(called["n"], 0,
                         "fast-path falhou: _register_account (caminho de escrita) "
                         "foi chamado mesmo com a conta já existindo")

    async def test_register_conta_nova_ainda_escreve_de_verdade(self):
        """Contrapartida do teste acima — garante que o fast-path não vira
        um jeito de nunca escrever nada: conta genuinamente NOVA continua
        chamando o caminho de escrita real e persistindo no banco."""
        ch = auth._hash("senha_nova")
        result = await auth.register("conta_totalmente_nova", ch)
        self.assertTrue(result, "register() devia aceitar username novo")
        self.assertIsNotNone(await auth._get_account_id("conta_totalmente_nova"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
