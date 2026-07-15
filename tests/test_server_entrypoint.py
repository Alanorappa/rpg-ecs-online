"""
tests/test_server_entrypoint.py — o entry point REAL do servidor sobe?

A suíte inteira importa server.* como pacote (raiz já no sys.path via
tests/helpers.py) — nenhum teste executava `python server/main.py` do jeito
que o usuário roda. Regressão real que passou batida (15/07/2026): a
migração print→logging injetou `from server.log import log` ANTES do
bootstrap de sys.path do main.py → ModuleNotFoundError só ao rodar como
script. Este teste executa o arquivo como subprocess, igual produção.

`--help` é suficiente: o argparse só roda DEPOIS de todos os imports de
módulo — se qualquer import quebrar, o returncode != 0.
"""
import os, sys, subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestServerEntrypoint(unittest.TestCase):

    def test_main_py_executa_como_script(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "server", "main.py"), "--help"],
            capture_output=True, text=True, timeout=60,
            cwd=_ROOT,   # mesmo cwd de `python server/main.py` na raiz
        )
        self.assertEqual(
            proc.returncode, 0,
            f"server/main.py nao sobe como script:\n{proc.stderr[-2000:]}")

    def test_main_py_executa_de_qualquer_cwd(self):
        """`python C:\\...\\server\\main.py` de OUTRO diretório também deve
        funcionar — o bootstrap de sys.path usa caminho absoluto do arquivo."""
        proc = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "server", "main.py"), "--help"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(_ROOT),   # cwd = pai da raiz do projeto
        )
        self.assertEqual(
            proc.returncode, 0,
            f"server/main.py depende do cwd:\n{proc.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
