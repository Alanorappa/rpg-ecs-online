"""
tests/test_character_names.py — validação/unicidade/geração de nome de
personagem (feedback do usuário, 20/07/2026: personagens de teste
duplicados "Aventureiro"/"Juugo" porque não havia checagem nenhuma de
formato nem unicidade no banco). Usa banco temporário isolado (mesmo
padrão de test_auth_salt.py) — unicidade é GLOBAL (todas as contas), não
faz sentido testar isso contra o banco dev real compartilhado.
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import server.auth as auth
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


class TestCreateCharacterValidation(unittest.TestCase):
    """Banco temporário isolado — unicidade é GLOBAL, testar contra o
    banco dev real (compartilhado com test_session.py) daria falso
    positivo/negativo dependendo do que já foi criado antes."""

    def setUp(self):
        self._orig_db     = auth.DB_PATH
        self._orig_schema = auth._schema_ensured
        auth.DB_PATH         = os.path.join(tempfile.mkdtemp(), "test_charnames.db")
        auth._schema_ensured = False
        auth.init_db()
        self.acc_a = self._make_account("conta_a")
        self.acc_b = self._make_account("conta_b")

    def tearDown(self):
        auth.DB_PATH         = self._orig_db
        auth._schema_ensured = self._orig_schema

    def _make_account(self, username: str) -> int:
        ch = auth._hash("senha123")
        auth._register_account_sync(username, ch)
        return auth._get_account_id_sync(username)

    def test_criacao_com_nome_valido_funciona(self):
        reason = auth._create_character_sync(self.acc_a, "Fulano", "guerreiro")
        self.assertEqual(reason, "ok")

    def test_nome_minusculo_e_salvo_com_1a_letra_maiuscula(self):
        """Defesa em profundidade — cliente já normaliza, mas um cliente
        modificado que mande o nome cru minúsculo não deve escapar disso."""
        reason = auth._create_character_sync(self.acc_a, "fulano", "guerreiro")
        self.assertEqual(reason, "ok")
        with auth._get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM characters WHERE account_id=?", (self.acc_a,)
            ).fetchone()
        self.assertEqual(row["name"], "Fulano")

    def test_nome_com_espaco_rejeitado(self):
        reason = auth._create_character_sync(self.acc_a, "Fulano Silva", "guerreiro")
        self.assertEqual(reason, "invalid_name_format")

    def test_nome_com_numero_rejeitado(self):
        reason = auth._create_character_sync(self.acc_a, "Juugo1", "guerreiro")
        self.assertEqual(reason, "invalid_name_format")

    def test_nome_ja_usado_na_mesma_conta_rejeitado(self):
        auth._create_character_sync(self.acc_a, "Fulano", "guerreiro")
        reason = auth._create_character_sync(self.acc_a, "Fulano", "mago")
        self.assertEqual(reason, "name_taken")

    def test_nome_ja_usado_em_OUTRA_conta_rejeitado(self):
        """Unicidade é GLOBAL — nameplate/chat/trade mostram o nome pra
        todo mundo, então duas contas não podem ter o mesmo "Aventureiro"."""
        auth._create_character_sync(self.acc_a, "Fulano", "guerreiro")
        reason = auth._create_character_sync(self.acc_b, "Fulano", "mago")
        self.assertEqual(reason, "name_taken")

    def test_nome_ja_usado_case_insensitive_rejeitado(self):
        auth._create_character_sync(self.acc_a, "Fulano", "guerreiro")
        reason = auth._create_character_sync(self.acc_b, "fulano", "mago")
        self.assertEqual(reason, "name_taken")

    def test_limite_de_3_personagens_por_conta(self):
        self.assertEqual(auth._create_character_sync(self.acc_a, "Alfa", "guerreiro"), "ok")
        self.assertEqual(auth._create_character_sync(self.acc_a, "Beta", "guerreiro"), "ok")
        self.assertEqual(auth._create_character_sync(self.acc_a, "Gama", "guerreiro"), "ok")
        reason = auth._create_character_sync(self.acc_a, "Delta", "guerreiro")
        self.assertEqual(reason, "limit_reached")


class TestSuggestCharacterName(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._orig_db     = auth.DB_PATH
        self._orig_schema = auth._schema_ensured
        auth.DB_PATH         = os.path.join(tempfile.mkdtemp(), "test_suggest.db")
        auth._schema_ensured = False
        auth.init_db()

    def tearDown(self):
        auth.DB_PATH         = self._orig_db
        auth._schema_ensured = self._orig_schema

    async def test_sugestao_e_valida(self):
        name = await auth.suggest_character_name()
        self.assertTrue(is_valid_name(name))

    async def test_sugestao_nao_repete_nome_ja_existente(self):
        ch = auth._hash("senha123")
        auth._register_account_sync("conta_sugestao", ch)
        acc_id = auth._get_account_id_sync("conta_sugestao")

        first = await auth.suggest_character_name()
        auth._create_character_sync(acc_id, first, "guerreiro")

        second = await auth.suggest_character_name()
        self.assertNotEqual(first, second,
                            "sugestão repetiu um nome que já existe no banco")


class TestSessionCreateCharacterWiring(unittest.IsolatedAsyncioTestCase):
    """CREATE_CHARACTER/SUGGEST_NAME fim-a-fim via SessionManager — banco
    temporário isolado (unicidade global não pode ser testada contra o
    banco dev real compartilhado com test_session.py)."""

    def setUp(self):
        self._orig_db     = auth.DB_PATH
        self._orig_schema = auth._schema_ensured
        auth.DB_PATH         = os.path.join(tempfile.mkdtemp(), "test_session_charnames.db")
        auth._schema_ensured = False
        auth.init_db()

        from tests.test_session import make_session_manager, FakeWS, get_msgs_of_type
        self._FakeWS = FakeWS
        self._get_msgs = get_msgs_of_type
        self.ws, self.mgr = make_session_manager()

    def tearDown(self):
        auth.DB_PATH         = self._orig_db
        auth._schema_ensured = self._orig_schema

    async def _login(self, username: str):
        from shared.messages import encode, MsgType
        from shared.constants import PROTOCOL_VERSION
        ch = auth._hash("senha123")
        auth._register_account_sync(username, ch)
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
