"""
tests/test_character_names.py — validação/unicidade/geração de nome de
personagem (feedback do usuário, 20/07/2026: personagens de teste
duplicados "Aventureiro"/"Juugo" porque não havia checagem nenhuma de
formato nem unicidade no banco). Usa schema Postgres isolado (mesmo
padrão de test_auth_salt.py, 07/09/2026 §123) — unicidade é GLOBAL (todas
as contas), não faz sentido testar isso contra o schema dev real
compartilhado.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import server.auth as auth
from tests.helpers import isolate_auth_schema, restore_auth_schema
from shared.character_names import (
    is_valid_name, is_valid_name_char, generate_name_candidate,
    normalize_name, NAME_MIN_LEN, NAME_MAX_LEN,
)


class TestNormalizeName(unittest.TestCase):

    def test_capitaliza_primeira_letra(self):
        self.assertEqual(normalize_name("fulano"), "Fulano")

    def test_ja_capitalizado_fica_igual(self):
        self.assertEqual(normalize_name("Fulano"), "Fulano")

    def test_nao_mexe_no_resto_do_nome(self):
        self.assertEqual(normalize_name("fUlAno"), "FUlAno")

    def test_string_vazia_fica_vazia(self):
        self.assertEqual(normalize_name(""), "")

    def test_acento_na_primeira_letra(self):
        self.assertEqual(normalize_name("ágata"), "Ágata")


class TestIsValidName(unittest.TestCase):

    def test_nome_normal_e_valido(self):
        self.assertTrue(is_valid_name("Aventureiro"))
        self.assertTrue(is_valid_name("Joao"))

    def test_nome_com_acento_e_valido(self):
        self.assertTrue(is_valid_name("João"))
        self.assertTrue(is_valid_name("Ágata"))

    def test_nome_com_espaco_invalido(self):
        self.assertFalse(is_valid_name("Fulano Silva"))

    def test_nome_com_numero_invalido(self):
        self.assertFalse(is_valid_name("Juugo1"))

    def test_nome_com_simbolo_invalido(self):
        self.assertFalse(is_valid_name("Fulano_"))
        self.assertFalse(is_valid_name("Fulano@"))

    def test_nome_vazio_invalido(self):
        self.assertFalse(is_valid_name(""))

    def test_nome_curto_demais_invalido(self):
        self.assertFalse(is_valid_name("A" * (NAME_MIN_LEN - 1)))

    def test_nome_no_limite_minimo_valido(self):
        self.assertTrue(is_valid_name("A" * NAME_MIN_LEN))

    def test_nome_no_limite_maximo_valido(self):
        self.assertTrue(is_valid_name("A" * NAME_MAX_LEN))

    def test_nome_longo_demais_invalido(self):
        self.assertFalse(is_valid_name("A" * (NAME_MAX_LEN + 1)))


class TestIsValidNameChar(unittest.TestCase):

    def test_letra_valida(self):
        self.assertTrue(is_valid_name_char("a"))
        self.assertTrue(is_valid_name_char("Z"))
        self.assertTrue(is_valid_name_char("ç"))

    def test_espaco_numero_simbolo_invalidos(self):
        self.assertFalse(is_valid_name_char(" "))
        self.assertFalse(is_valid_name_char("5"))
        self.assertFalse(is_valid_name_char("_"))
        self.assertFalse(is_valid_name_char("@"))

    def test_string_vazia_invalida(self):
        self.assertFalse(is_valid_name_char(""))


class TestGenerateNameCandidate(unittest.TestCase):

    def test_candidato_sempre_valido(self):
        import random
        rng = random.Random(1234)
        for _ in range(200):
            name = generate_name_candidate(rng)
            self.assertTrue(is_valid_name(name), f"candidato inválido: {name!r}")

    def test_candidatos_variam(self):
        import random
        rng = random.Random(42)
        names = {generate_name_candidate(rng) for _ in range(30)}
        self.assertGreater(len(names), 1, "gerador sempre produz o mesmo nome")


class TestCreateCharacterValidation(unittest.IsolatedAsyncioTestCase):
    """Schema Postgres isolado — unicidade é GLOBAL, testar contra o
    schema dev real (compartilhado com test_session.py) daria falso
    positivo/negativo dependendo do que já foi criado antes."""

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_charnames_create")
        self.acc_a = await self._make_account("conta_a")
        self.acc_b = await self._make_account("conta_b")

    async def asyncTearDown(self):
        await restore_auth_schema(self._orig_schema)

    async def _make_account(self, username: str) -> int:
        ch = auth._hash("senha123")
        await auth._register_account(username, ch)
        return await auth._get_account_id(username)

    async def test_criacao_com_nome_valido_funciona(self):
        reason = await auth._create_character(self.acc_a, "Fulano", "guerreiro")
        self.assertEqual(reason, "ok")

    async def test_nome_minusculo_e_salvo_com_1a_letra_maiuscula(self):
        """Defesa em profundidade — cliente já normaliza, mas um cliente
        modificado que mande o nome cru minúsculo não deve escapar disso."""
        reason = await auth._create_character(self.acc_a, "fulano", "guerreiro")
        self.assertEqual(reason, "ok")
        pool = await auth._get_pool()
        row = await pool.fetchrow(
            "SELECT name FROM characters WHERE account_id=$1", self.acc_a)
        self.assertEqual(row["name"], "Fulano")

    async def test_nome_com_espaco_rejeitado(self):
        reason = await auth._create_character(self.acc_a, "Fulano Silva", "guerreiro")
        self.assertEqual(reason, "invalid_name_format")

    async def test_nome_com_numero_rejeitado(self):
        reason = await auth._create_character(self.acc_a, "Juugo1", "guerreiro")
        self.assertEqual(reason, "invalid_name_format")

    async def test_nome_ja_usado_na_mesma_conta_rejeitado(self):
        await auth._create_character(self.acc_a, "Fulano", "guerreiro")
        reason = await auth._create_character(self.acc_a, "Fulano", "mago")
        self.assertEqual(reason, "name_taken")

    async def test_nome_ja_usado_em_OUTRA_conta_rejeitado(self):
        """Unicidade é GLOBAL — nameplate/chat/trade mostram o nome pra
        todo mundo, então duas contas não podem ter o mesmo "Aventureiro"."""
        await auth._create_character(self.acc_a, "Fulano", "guerreiro")
        reason = await auth._create_character(self.acc_b, "Fulano", "mago")
        self.assertEqual(reason, "name_taken")

    async def test_nome_ja_usado_case_insensitive_rejeitado(self):
        await auth._create_character(self.acc_a, "Fulano", "guerreiro")
        reason = await auth._create_character(self.acc_b, "fulano", "mago")
        self.assertEqual(reason, "name_taken")

    async def test_limite_de_3_personagens_por_conta(self):
        self.assertEqual(await auth._create_character(self.acc_a, "Alfa", "guerreiro"), "ok")
        self.assertEqual(await auth._create_character(self.acc_a, "Beta", "guerreiro"), "ok")
        self.assertEqual(await auth._create_character(self.acc_a, "Gama", "guerreiro"), "ok")
        reason = await auth._create_character(self.acc_a, "Delta", "guerreiro")
        self.assertEqual(reason, "limit_reached")

    async def test_nomes_iguais_concorrentes_so_1_sucede(self):
        """07/09/2026, §123 — a corrida REAL (TOCTOU) agora é fechada pelo
        índice único do banco (`characters_name_lower_idx`), não por um
        lock de processo (removido na migração pra Postgres — não
        protegeria múltiplos processos futuros de qualquer forma). 2
        criações concorrentes do MESMO nome, mesma variação de caixa:
        exatamente 1 sucede, a outra recebe "name_taken" — nunca as 2
        "ok" (duplicata sobrevivendo) nem as 2 falhando."""
        import asyncio
        results = await asyncio.gather(
            auth._create_character(self.acc_a, "Concorrente", "guerreiro"),
            auth._create_character(self.acc_b, "concorrente", "mago"),
        )
        self.assertEqual(sorted(results), ["name_taken", "ok"],
                         f"esperado exatamente 1 'ok' e 1 'name_taken', veio {results}")


class TestSuggestCharacterName(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_charnames_suggest")

    async def asyncTearDown(self):
        await restore_auth_schema(self._orig_schema)

    async def test_sugestao_e_valida(self):
        name = await auth.suggest_character_name()
        self.assertTrue(is_valid_name(name))

    async def test_sugestao_nao_repete_nome_ja_existente(self):
        ch = auth._hash("senha123")
        await auth._register_account("conta_sugestao", ch)
        acc_id = await auth._get_account_id("conta_sugestao")

        first = await auth.suggest_character_name()
        await auth._create_character(acc_id, first, "guerreiro")

        second = await auth.suggest_character_name()
        self.assertNotEqual(first, second,
                            "sugestão repetiu um nome que já existe no banco")


class TestSessionCreateCharacterWiring(unittest.IsolatedAsyncioTestCase):
    """CREATE_CHARACTER/SUGGEST_NAME fim-a-fim via SessionManager — schema
    Postgres isolado (unicidade global não pode ser testada contra o
    schema dev real compartilhado com test_session.py)."""

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_session_charnames")

        from tests.test_session import make_session_manager, FakeWS, get_msgs_of_type
        self._FakeWS = FakeWS
        self._get_msgs = get_msgs_of_type
        self.ws, self.mgr = make_session_manager()

    async def asyncTearDown(self):
        await restore_auth_schema(self._orig_schema)

    async def _login(self, username: str):
        from shared.messages import encode, MsgType
        from shared.constants import PROTOCOL_VERSION
        ch = auth._hash("senha123")
        await auth._register_account(username, ch)
        fake_ws = self._FakeWS()
        session = await self.mgr.on_connect(fake_ws, username)
        await self.mgr.on_message(session, encode(MsgType.LOGIN, {
            "username": username, "password": ch, "version": PROTOCOL_VERSION
        }))
        return session, fake_ws

    async def test_create_character_com_nome_valido_retorna_character_created(self):
        from shared.messages import encode, MsgType
        session, fake_ws = await self._login("wire_ok")
        await self.mgr.on_message(session, encode(MsgType.CREATE_CHARACTER, {
            "name": "Fulano", "class_id": "guerreiro"
        }))
        created = self._get_msgs(fake_ws, MsgType.CHARACTER_CREATED)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["char"]["name"], "Fulano")

    async def test_create_character_com_nome_invalido_retorna_character_error(self):
        from shared.messages import encode, MsgType
        session, fake_ws = await self._login("wire_bad")
        await self.mgr.on_message(session, encode(MsgType.CREATE_CHARACTER, {
            "name": "Fulano Silva", "class_id": "guerreiro"
        }))
        errs = self._get_msgs(fake_ws, MsgType.CHARACTER_ERROR)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["reason"], "invalid_name_format")

    async def test_create_character_com_nome_duplicado_retorna_name_taken(self):
        from shared.messages import encode, MsgType
        session_a, fake_ws_a = await self._login("wire_dup_a")
        await self.mgr.on_message(session_a, encode(MsgType.CREATE_CHARACTER, {
            "name": "Duplicado", "class_id": "guerreiro"
        }))

        session_b, fake_ws_b = await self._login("wire_dup_b")
        await self.mgr.on_message(session_b, encode(MsgType.CREATE_CHARACTER, {
            "name": "Duplicado", "class_id": "mago"
        }))
        errs = self._get_msgs(fake_ws_b, MsgType.CHARACTER_ERROR)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["reason"], "name_taken")

    async def test_suggest_name_retorna_nome_valido(self):
        from shared.messages import encode, MsgType
        session, fake_ws = await self._login("wire_suggest")
        await self.mgr.on_message(session, encode(MsgType.SUGGEST_NAME, {}))
        suggestions = self._get_msgs(fake_ws, MsgType.NAME_SUGGESTION)
        self.assertEqual(len(suggestions), 1)
        self.assertTrue(is_valid_name(suggestions[0]["name"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
