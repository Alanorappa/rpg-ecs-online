"""
tests/test_auth_write_lock.py — `auth._write_lock` (06/09/2026,
PROBLEMAS_ARQUITETURA.md §113/§118). Sem serializar register/
create_character/save_character no wrapper async, uma rajada de conexão
concorrente (rampa de bots de loadtest) disparava N execuções de
`_register_account_sync`/`_create_character_sync`/`_save_character_sync`
ao mesmo tempo, cada uma em sua própria thread do executor padrão do
asyncio, todas disputando o lock de escrita do arquivo SQLite — com
`sqlite3.connect()`'s timeout default de 5s, a CAUDA de espera sob rajada
crescia bem além do timeout do cliente (10s pro REGISTER/LOGIN,
`tools/loadtest/protocol_client.py::recv_one`).

Usa banco temporário isolado (mesmo padrão de test_auth_salt.py) e um
monkeypatch em `_register_account_sync`/`_create_character_sync` que
detecta reentrância concorrente via um contador — prova que `_write_lock`
realmente serializa (nunca 2 execuções sobrepostas), sem quebrar o
resultado funcional (todas as contas/personagens são criados certos).
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server.auth as auth


class TestAuthWriteLockSerializes(unittest.TestCase):

    def setUp(self):
        self._orig_db     = auth.DB_PATH
        self._orig_schema = auth._schema_ensured
        auth.DB_PATH         = os.path.join(tempfile.mkdtemp(), "test_auth_lock.db")
        auth._schema_ensured = False
        auth.init_db()
        # `_get_write_lock()` (não um `asyncio.Lock()` de módulo usado
        # direto) já se realoca sozinho quando o event loop muda entre
        # chamadas de `asyncio.run()` — reset explícito aqui só evita
        # qualquer resíduo entre testes desta classe, não é estritamente
        # necessário pra correção (ver comentário em server/auth.py sobre
        # por que um Lock de módulo cru quebraria entre testes).
        auth._write_lock = None
        auth._write_lock_loop = None

    def tearDown(self):
        auth.DB_PATH         = self._orig_db
        auth._schema_ensured = self._orig_schema

    def test_registers_concorrentes_nunca_sobrepoem_e_todos_criam_conta(self):
        """N `register()` concorrentes (usernames distintos) — com o lock,
        a execução real de `_register_account_sync` nunca roda 2x ao mesmo
        tempo (prova via contador de reentrância em threads do executor);
        sem quebrar o resultado: todas as N contas existem no final."""
        N = 12
        concurrent_count = {"now": 0, "max": 0}
        lock_for_counter = threading.Lock()
        orig_sync = auth._register_account_sync

        def instrumented_sync(username, password):
            with lock_for_counter:
                concurrent_count["now"] += 1
                concurrent_count["max"] = max(concurrent_count["max"], concurrent_count["now"])
            try:
                # Segura um pouco a thread pra dar chance de sobreposição
                # SE o lock não estivesse funcionando (sem isso o teste
                # passaria mesmo com o bug, só por sorte de timing).
                time.sleep(0.02)
                return orig_sync(username, password)
            finally:
                with lock_for_counter:
                    concurrent_count["now"] -= 1

        auth._register_account_sync = instrumented_sync
        try:
            async def run_all():
                return await asyncio.gather(*[
                    auth.register(f"loadtest_bot_{i}", auth._hash(f"senha{i}"))
                    for i in range(N)
                ])
            results = asyncio.run(run_all())
        finally:
            auth._register_account_sync = orig_sync

        self.assertTrue(all(results), "algum register concorrente falhou (deveria ter sucesso, usernames distintos)")
        self.assertEqual(
            concurrent_count["max"], 1,
            f"_write_lock não serializou: {concurrent_count['max']} execuções de "
            "_register_account_sync rodando ao mesmo tempo (esperado: nunca mais que 1)")

        for i in range(N):
            self.assertIsNotNone(auth._get_account_id_sync(f"loadtest_bot_{i}"),
                                 f"conta loadtest_bot_{i} não foi criada")

    def test_authenticate_nao_serializado_pelo_write_lock(self):
        """`authenticate` (LOGIN, predominantemente leitura) fica FORA do
        `_write_lock` de propósito (ver comentário no topo de auth.py) —
        confirma que múltiplos logins concorrentes não disputam o mesmo
        lock que register/create_character usam (não deveriam serializar
        entre si por causa dele)."""
        ch = auth._hash("minha_senha")
        auth._register_account_sync("user_login_test", ch)

        async def run_logins():
            return await asyncio.gather(*[
                auth.authenticate("user_login_test", ch) for _ in range(5)
            ])

        results = asyncio.run(run_logins())
        self.assertTrue(all(r is not None for r in results), "algum login concorrente falhou")


if __name__ == "__main__":
    unittest.main(verbosity=2)
