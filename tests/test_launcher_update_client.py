"""
tests/test_launcher_update_client.py
launcher/update_client.py — lógica pura do Launcher (08/10/2026, ver
CLAUDE.md), sem UI. Testa contra um servidor aiohttp real (mesmo
server/http_api.py::build_app) pra cobrir o caminho de rede de verdade,
não só mockar urllib.
"""
import os, sys, hashlib, zipfile, tempfile, shutil, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer

from launcher import update_client as uc
from server.http_api import build_app, RELEASES_DIR, ZIP_NAME, SHA_NAME
from shared.auth_utils import hash_password
from tests.helpers import isolate_auth_schema, restore_auth_schema


class TestGetLocalVersion(unittest.TestCase):

    def test_sem_version_txt_devolve_vazio(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(uc.get_local_version(d), "")

    def test_com_version_txt_devolve_conteudo(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, uc.VERSION_FILE), "w", encoding="utf-8") as f:
                f.write("0.53.0\n")
            self.assertEqual(uc.get_local_version(d), "0.53.0")


class TestVerifySha256(unittest.TestCase):

    def test_hash_correto_aceita(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"conteudo de teste")
            path = f.name
        try:
            expected = hashlib.sha256(b"conteudo de teste").hexdigest()
            self.assertTrue(uc.verify_sha256(path, expected))
        finally:
            os.remove(path)

    def test_hash_errado_recusa(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"conteudo de teste")
            path = f.name
        try:
            self.assertFalse(uc.verify_sha256(path, "0" * 64))
        finally:
            os.remove(path)

    def test_hash_vazio_sempre_recusa_fail_closed(self):
        """Sem hash publicado (antes do 1o release), NUNCA aplica — ver
        docstring de verify_sha256 (o early-return e so documentacao
        explicita; um hexdigest real nunca bate com "" de qualquer
        forma, mas o teste formaliza o comportamento esperado)."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"qualquer coisa")
            path = f.name
        try:
            self.assertFalse(uc.verify_sha256(path, ""))
        finally:
            os.remove(path)


class TestApplyUpdate(unittest.TestCase):

    def test_extrai_zip_por_cima_da_pasta(self):
        with tempfile.TemporaryDirectory() as src_dir, \
             tempfile.TemporaryDirectory() as install_dir:
            with open(os.path.join(src_dir, "RPG_Online.exe"), "w") as f:
                f.write("conteudo novo")
            with open(os.path.join(src_dir, uc.VERSION_FILE), "w") as f:
                f.write("0.54.0")
            zip_path = os.path.join(src_dir, "release.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.write(os.path.join(src_dir, "RPG_Online.exe"), "RPG_Online.exe")
                zf.write(os.path.join(src_dir, uc.VERSION_FILE), uc.VERSION_FILE)

            uc.apply_update(zip_path, install_dir)

            self.assertTrue(os.path.isfile(os.path.join(install_dir, "RPG_Online.exe")))
            self.assertEqual(uc.get_local_version(install_dir), "0.54.0")

    def test_nunca_inclui_launcher_exe_no_zip_de_release(self):
        """Documenta por que apply_update é seguro: o zip publicado por
        release_tools/release_client.ps1 nunca contém Launcher.exe — esse
        teste só prova que extrair um zip SEM Launcher.exe não cria/
        sobrescreve um arquivo com esse nome."""
        with tempfile.TemporaryDirectory() as src_dir, \
             tempfile.TemporaryDirectory() as install_dir:
            launcher_path = os.path.join(install_dir, "Launcher.exe")
            with open(launcher_path, "w") as f:
                f.write("launcher original, nunca deveria mudar")
            original_content = open(launcher_path).read()

            with open(os.path.join(src_dir, "RPG_Online.exe"), "w") as f:
                f.write("jogo novo")
            zip_path = os.path.join(src_dir, "release.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.write(os.path.join(src_dir, "RPG_Online.exe"), "RPG_Online.exe")

            uc.apply_update(zip_path, install_dir)
            self.assertEqual(open(launcher_path).read(), original_content)


class TestNetworkAgainstRealServer(unittest.IsolatedAsyncioTestCase):
    """Sobe o MESMO server/http_api.py::build_app() de verdade (aiohttp
    TestServer) e bate com update_client real — cobre o caminho de rede
    ponta a ponta, não só mocka urllib."""

    async def asyncSetUp(self):
        self._orig_schema = await isolate_auth_schema("test_launcher_update_client")
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()
        # TestServer já escuta em localhost:<porta aleatoria> — monta a
        # mesma base_url que o launcher usaria.
        self.base_url = f"http://localhost:{self.client.server.port}"

    async def asyncTearDown(self):
        await self.client.close()
        await restore_auth_schema(self._orig_schema)

    async def test_fetch_version_manifest_bate_com_o_servidor_real(self):
        manifest = await asyncio_to_thread(uc.fetch_version_manifest, self.base_url, None)
        from shared.constants import GAME_VERSION
        self.assertEqual(manifest["version"], GAME_VERSION)

    async def test_download_e_verify_sha256_de_um_release_publicado(self):
        releases_dir_abs = os.path.abspath(RELEASES_DIR)
        os.makedirs(releases_dir_abs, exist_ok=True)
        zip_path = os.path.join(releases_dir_abs, ZIP_NAME)
        sha_path = os.path.join(releases_dir_abs, SHA_NAME)
        content = b"zip de teste"
        try:
            with open(zip_path, "wb") as f:
                f.write(content)
            expected_hash = hashlib.sha256(content).hexdigest()
            with open(sha_path, "w", encoding="utf-8") as f:
                f.write(expected_hash)

            with tempfile.TemporaryDirectory() as d:
                dest = os.path.join(d, "downloaded.zip")
                await asyncio_to_thread(uc.download_release, self.base_url, dest, None)
                self.assertEqual(open(dest, "rb").read(), content)
                self.assertTrue(uc.verify_sha256(dest, expected_hash))
        finally:
            for p in (zip_path, sha_path):
                if os.path.exists(p):
                    os.remove(p)
            if not os.listdir(releases_dir_abs):
                os.rmdir(releases_dir_abs)

    async def test_register_account_cria_conta_real_e_login_funciona_depois(self):
        """Caminho de rede real de update_client.register_account() (não
        só o aiohttp TestClient direto, como tests/test_launcher_api.py
        já cobre) — prova que a conta criada pelo Launcher é a mesma
        tabela que check_login/o jogo usam."""
        ph = hash_password("senha_launcher_123")
        data = await asyncio_to_thread(
            uc.register_account, self.base_url, "launcher_e2e_novo", ph, None)
        self.assertTrue(data["ok"])

        ok = await asyncio_to_thread(uc.check_login, self.base_url, "launcher_e2e_novo", ph, None)
        self.assertTrue(ok)

    async def test_register_account_username_duplicado_devolve_reason(self):
        ph = hash_password("senha_launcher_123")
        await asyncio_to_thread(uc.register_account, self.base_url, "launcher_e2e_dup", ph, None)
        data2 = await asyncio_to_thread(
            uc.register_account, self.base_url, "launcher_e2e_dup", hash_password("outra"), None)
        self.assertFalse(data2["ok"])
        self.assertEqual(data2["reason"], "username_taken")


def asyncio_to_thread(fn, *args):
    import asyncio
    return asyncio.to_thread(fn, *args)


if __name__ == "__main__":
    unittest.main()
