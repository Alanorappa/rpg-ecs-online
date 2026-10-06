"""
tests/test_ranking.py — server/auth.py::get_ranking + server/
ranking_processor.py (06/10/2026, pedido do usuário: "ranking de level,
skill level dos players"). Inclui TODOS os personagens já criados,
online ou não — por isso lê direto do banco (fonte de verdade),
nunca varre só sessões conectadas agora.

NÃO EXECUTADO nesta sessão (ambiente sem Postgres/Docker local
disponível) — mesma limitação já registrada em tests/test_session.py::
TestPartyPositionsNoAoiUpdate. Segue o MESMO padrão de
tests/test_mail.py (fake_login + banco real de dev), revisado com
cuidado redobrado por não poder confirmar via execução.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_session import make_session_manager, fake_login
from tests.helpers import AuthPoolCleanupMixin
from server import auth


class TestRankingLevel(AuthPoolCleanupMixin, unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws, self.mgr = make_session_manager()

    async def _set_level(self, char_id: int, level: int) -> None:
        pool = await auth._get_pool()
        await pool.execute("UPDATE characters SET level=$1 WHERE id=$2", level, char_id)

    async def _set_skill_level(self, char_id: int, skill_id: str, value: int) -> None:
        import json
        pool = await auth._get_pool()
        await pool.execute(
            "UPDATE characters SET skill_levels_json=$1 WHERE id=$2",
            json.dumps({"levels": {skill_id: value}, "xp": {}}), char_id)

    async def _set_name(self, char_id: int, name: str) -> None:
        pool = await auth._get_pool()
        await pool.execute("UPDATE characters SET name=$1 WHERE id=$2", name, char_id)

    async def test_ranking_de_level_ordena_desc(self):
        sa, _ = await fake_login(self.mgr, "ranka", "rankusera")
        sb, _ = await fake_login(self.mgr, "rankb", "rankuserb")
        await self._set_level(sa.char_data["id"], 50)
        await self._set_level(sb.char_data["id"], 10)

        entries = await auth.get_ranking("level")
        names = [e["name"] for e in entries]
        idx_a = names.index(sa.char_data["name"])
        idx_b = names.index(sb.char_data["name"])
        self.assertLess(idx_a, idx_b, "level maior deveria vir antes no ranking")

    async def test_ranking_de_skill_le_do_skill_levels_json(self):
        sa, _ = await fake_login(self.mgr, "rankc", "rankuserc")
        await self._set_skill_level(sa.char_data["id"], "espada", 150)

        entries = await auth.get_ranking("espada")
        found = next(e for e in entries if e["name"] == sa.char_data["name"])
        self.assertEqual(found["value"], 150)

    async def test_skill_level_zerado_por_padrao(self):
        """Personagem recém-criado (fake_login) nunca passou por
        _set_skill_level -- skill_levels_json default '{}' precisa virar
        value=0, nunca erro/exceção."""
        sa, _ = await fake_login(self.mgr, "rankd", "rankuserd")
        entries = await auth.get_ranking("machado")
        found = next(e for e in entries if e["name"] == sa.char_data["name"])
        self.assertEqual(found["value"], 0)

    async def test_categoria_invalida_devolve_vazio(self):
        entries = await auth.get_ranking("categoria_que_nao_existe")
        self.assertEqual(entries, [])

    async def test_bot_de_loadtest_nunca_aparece_no_ranking(self):
        """Bug real achado pelo usuário (06/10/2026): 300 contas
        loadbot0000-loadbot0299 (tools/loadtest/) já existem em produção
        e poluiriam o ranking de verdade. Nome do personagem segue
        SEMPRE o padrão de tools/loadtest/identity.py::bot_char_name
        ("LoadBot" + letras) -- filtro por esse prefixo, nunca lista de
        id mantida à mão."""
        sa, _ = await fake_login(self.mgr, "rankf", "rankuserf")
        await self._set_name(sa.char_data["id"], "LoadBotzzzz")
        await self._set_level(sa.char_data["id"], 200)   # nível MAIS alto possível
        await self._set_skill_level(sa.char_data["id"], "espada", 200)

        level_entries = await auth.get_ranking("level")
        self.assertNotIn("LoadBotzzzz", [e["name"] for e in level_entries])

        skill_entries = await auth.get_ranking("espada")
        self.assertNotIn("LoadBotzzzz", [e["name"] for e in skill_entries])

    async def test_ranking_request_handler_responde_result(self):
        """Fluxo completo via protocolo (RANKING_REQUEST -> RANKING_RESULT),
        não só a função pura de auth.py."""
        from shared.messages import MsgType, encode
        sa, fw_a = await fake_login(self.mgr, "ranke", "rankusere")
        await self._set_level(sa.char_data["id"], 77)
        fw_a.sent.clear()

        await self.mgr.on_message(sa, encode(MsgType.RANKING_REQUEST, {"category": "level"}))

        from tests.test_session import get_msgs_of_type
        results = get_msgs_of_type(fw_a, MsgType.RANKING_RESULT)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "level")
        names = [e["name"] for e in results[0]["entries"]]
        self.assertIn(sa.char_data["name"], names)


if __name__ == "__main__":
    unittest.main()
