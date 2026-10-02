"""
tests/test_tls.py — TLS (wss://) via certificado autoassinado fixado
no cliente (02/10/2026, pedido do usuário: publicar o jogo exige
criptografia, mas sem domínio -- certificado autoassinado, SAN=IP do
servidor, fixado via `cafile=` no cliente em vez de depender de uma
autoridade certificadora pública).

Fixture de teste descartável (`tests/fixtures/tls/test_cert.pem`/
`test_key.pem`, CN genérico, 365 dias, gerada 1x via openssl) -- nunca
usada pra conexão real, só pra provar que as funções de montagem de
SSLContext carregam um certificado válido de verdade.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssl
import unittest

_FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tls")
_TEST_CERT   = os.path.join(_FIXTURE_DIR, "test_cert.pem")
_TEST_KEY    = os.path.join(_FIXTURE_DIR, "test_key.pem")


class TestBuildServerSslContext(unittest.TestCase):
    """`server/main.py::_build_server_ssl_context` -- None sem cert/key
    (dev local, ws:// normal); SSLContext real com os 2 arquivos
    válidos; levanta (nunca cai pra ws:// em silêncio) se o caminho foi
    informado mas o arquivo não existe."""

    def setUp(self):
        from server.main import _build_server_ssl_context
        self.build = _build_server_ssl_context

    def test_sem_cert_nem_key_devolve_none(self):
        self.assertIsNone(self.build("", ""))

    def test_so_cert_sem_key_devolve_none(self):
        self.assertIsNone(self.build(_TEST_CERT, ""))

    def test_so_key_sem_cert_devolve_none(self):
        self.assertIsNone(self.build("", _TEST_KEY))

    def test_com_os_2_arquivos_validos_devolve_sslcontext_real(self):
        ctx = self.build(_TEST_CERT, _TEST_KEY)
        self.assertIsInstance(ctx, ssl.SSLContext)

    def test_caminho_informado_mas_arquivo_inexistente_levanta(self):
        """Nunca cai pra ws:// em silêncio quando o operador pediu TLS
        explicitamente (env vars preenchidas) -- falha ALTO, visível
        no boot, em vez de servir sem criptografia sem avisar."""
        with self.assertRaises((FileNotFoundError, ssl.SSLError, OSError)):
            self.build("/caminho/que/nao/existe/cert.pem", "/caminho/que/nao/existe/key.pem")


class TestBuildClientSslContext(unittest.TestCase):
    """`client/network.py::_build_client_ssl_context` -- None sem TLS
    (default, ws:// normal); fixa ("pinned") o certificado bundlado
    via `cafile=` quando `use_tls=True`; levanta se o certificado
    bundlado não existir (nunca conecta sem TLS em silêncio quando TLS
    foi pedido)."""

    def setUp(self):
        from client.network import _build_client_ssl_context
        self.build = _build_client_ssl_context

    def test_use_tls_false_devolve_none(self):
        self.assertIsNone(self.build(False))

    def test_use_tls_true_sem_certificado_bundlado_levanta(self):
        import unittest.mock as mock
        with mock.patch("os.path.isfile", return_value=False):
            with self.assertRaises(FileNotFoundError):
                self.build(True)

    def test_use_tls_true_com_certificado_bundlado_devolve_sslcontext_real(self):
        import unittest.mock as mock
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("paths.resource_path", return_value=_TEST_CERT):
            ctx = self.build(True)
        self.assertIsInstance(ctx, ssl.SSLContext)


class TestBuildUri(unittest.TestCase):
    """`client/network.py::_build_uri` -- esquema certo pras 4
    combinações host/port/use_tls. Passar `ssl=` pra uma URI `ws://`
    é erro na lib `websockets`, por isso o esquema tem que refletir
    `use_tls` de verdade, nunca ficar fixo em `ws://`."""

    def setUp(self):
        from client.network import _build_uri
        self.build = _build_uri

    def test_sem_tls_usa_ws(self):
        self.assertEqual(self.build("1.2.3.4", 8765, False), "ws://1.2.3.4:8765")

    def test_com_tls_usa_wss(self):
        self.assertEqual(self.build("1.2.3.4", 8765, True), "wss://1.2.3.4:8765")

    def test_localhost_sem_tls(self):
        self.assertEqual(self.build("localhost", 8765, False), "ws://localhost:8765")

    def test_localhost_com_tls(self):
        self.assertEqual(self.build("localhost", 8765, True), "wss://localhost:8765")


if __name__ == "__main__":
    unittest.main(verbosity=2)
