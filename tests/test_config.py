"""
tests/test_config.py — config.py::load/save (feedback do usuário
20/07/2026: crash ao entrar na arena com "JSONDecodeError: Expecting
value" — causa real: 2 clientes na MESMA pasta salvando quase ao mesmo
tempo, um lia config.json bem no instante em que o outro tinha acabado
de truncar o arquivo pra 0 bytes). Usa CONFIG_FILE apontado pra um
arquivo temporário isolado — nunca toca no config.json real do dev.
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import config


class TestConfigLoadSave(unittest.TestCase):

    def setUp(self):
        self._orig_file = config.CONFIG_FILE
        self._tmp_dir = tempfile.mkdtemp()
        config.CONFIG_FILE = os.path.join(self._tmp_dir, "config_test.json")

    def tearDown(self):
        config.CONFIG_FILE = self._orig_file

    def test_load_sem_arquivo_cria_com_defaults(self):
        self.assertFalse(os.path.exists(config.CONFIG_FILE))
        data = config.load()
        self.assertEqual(data["server_host"], config.DEFAULTS["server_host"])
        self.assertTrue(os.path.exists(config.CONFIG_FILE))

    def test_save_depois_load_faz_roundtrip(self):
        config.save({"scale": 2.0, "server_host": "example.com"})
        data = config.load()
        self.assertEqual(data["scale"], 2.0)
        self.assertEqual(data["server_host"], "example.com")

    def test_save_mescla_sem_apagar_chaves_existentes(self):
        config.save({"scale": 1.5})
        config.save({"server_host": "1.2.3.4"})
        data = config.load()
        self.assertEqual(data["scale"], 1.5)
        self.assertEqual(data["server_host"], "1.2.3.4")

    def test_load_arquivo_vazio_nao_derruba_o_jogo(self):
        """Reprodução direta do bug: arquivo truncado pra 0 bytes (2
        clientes na mesma pasta, save() de um colidindo com load() do
        outro) — load() tinha que voltar pros defaults, nunca lançar
        JSONDecodeError pra cima."""
        with open(config.CONFIG_FILE, "w") as f:
            f.write("")
        data = config.load()
        self.assertEqual(data["server_host"], config.DEFAULTS["server_host"])

    def test_load_arquivo_corrompido_nao_derruba_o_jogo(self):
        with open(config.CONFIG_FILE, "w") as f:
            f.write("{not valid json")
        data = config.load()
        self.assertEqual(data["scale"], config.DEFAULTS["scale"])

    def test_save_nao_deixa_arquivo_temporario_para_tras(self):
        config.save({"scale": 3.0})
        self.assertFalse(os.path.exists(config.CONFIG_FILE + ".tmp"))
        with open(config.CONFIG_FILE) as f:
            # arquivo final sempre é JSON completo/válido — write atômico
            json.load(f)

    def test_save_apos_arquivo_corrompido_recupera(self):
        """Mesmo se um load() anterior bateu num arquivo corrompido, o
        próximo save() ainda produz um config.json válido (não propaga
        o estado quebrado pra sempre)."""
        with open(config.CONFIG_FILE, "w") as f:
            f.write("")
        config.save({"scale": 4.0})
        data = config.load()
        self.assertEqual(data["scale"], 4.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
