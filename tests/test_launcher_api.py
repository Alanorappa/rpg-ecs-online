"""
tests/test_launcher_api.py
API HTTP do Launcher (08/10/2026, ver CLAUDE.md) — server/http_api.py.
Schema Postgres isolado (mesmo padrão de tests/test_auth_salt.py) —
nunca toca o banco dev real.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from aiohttp.test_utils import TestClient, TestServer

import server.auth as auth
from server.http_api import build_app
from shared.auth_utils import hash_password
from shared.constants import GAME_VERSION
from tests.helpers import isolate_auth_schema, restore_auth_schema


class TestLauncherVersion(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_version_devolve_game_version_atual(self):
        resp = await self.client.get("/launcher/version")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["version"], GAME_VERSION)

    async def test_sem_release_publicado_sha256_fica_vazio(self):
        """releases/RPG_Online.sha256 pode não existir ainda (antes do
        1º release_client.ps1 rodar) — nunca deveria quebrar a rota."""
        resp = await self.client.get("/launcher/version")
        data = await resp.json()
        self.assertIn("sha256", data)


class TestLauncherDownloadSemRelease(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_sem_zip_publicado_devolve_404(self):
        resp = await self.client.get("/launcher/download")
        self.assertEqual(resp.status, 404)


class TestLauncherLogin(unittest.IsolatedAsyncioTestCase):
    """Delega 100% pra server/auth.py::authenticate() — nunca reimplementa
    verificação de senha no endpoint HTTP."""

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_launcher_api")
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()
        self.ch = hash_password("minha_senha_123")
        await auth.register("launcher_tester", self.ch)

    async def asyncTearDown(self):
        await self.client.close()
        await restore_auth_schema(self._orig_schema)

    async def test_credencial_valida_ok_true(self):
        resp = await self.client.post("/launcher/login", json={
            "username": "launcher_tester", "password_hash": self.ch,
        })
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])

    async def test_senha_errada_ok_false(self):
        resp = await self.client.post("/launcher/login", json={
            "username": "launcher_tester", "password_hash": hash_password("errada"),
        })
        data = await resp.json()
        self.assertFalse(data["ok"])

    async def test_usuario_inexistente_ok_false(self):
        resp = await self.client.post("/launcher/login", json={
            "username": "nunca_existiu", "password_hash": self.ch,
        })
        data = await resp.json()
        self.assertFalse(data["ok"])

    async def test_nao_devolve_personagens_nem_sessao(self):
        """Gate de conveniência só — NUNCA abre sessão de jogo real."""
        resp = await self.client.post("/launcher/login", json={
            "username": "launcher_tester", "password_hash": self.ch,
        })
        data = await resp.json()
        self.assertNotIn("characters", data)
        self.assertNotIn("account_id", data)

    async def test_campos_faltando_devolve_400(self):
        resp = await self.client.post("/launcher/login", json={"username": "launcher_tester"})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
