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
        leftover_tmp = [f for f in os.listdir(self._tmp_dir) if f.endswith(".tmp")]
        self.assertEqual(leftover_tmp, [])
        with open(config.CONFIG_FILE) as f:
            # arquivo final sempre é JSON completo/válido — write atômico
            json.load(f)

    def test_tmp_path_e_unico_por_processo_nao_colide_entre_2_saves(self):
        """Bug real 07/08/2026: 2 clientes na mesma pasta salvando quase ao
        mesmo tempo colidiam no MESMO nome de arquivo temporário
        (config.json.tmp fixo) — um processo podia achar o .tmp do outro
        já aberto ao tentar os.replace(), PermissionError sem tratamento,
        crash. Simula 2 "processos" (PIDs diferentes) chamando save() —
        os caminhos .tmp não podem ser iguais."""
        import unittest.mock as mock
        with mock.patch("os.getpid", return_value=111):
            tmp_a = f"{config.CONFIG_FILE}.{os.getpid()}.tmp"
        with mock.patch("os.getpid", return_value=222):
            tmp_b = f"{config.CONFIG_FILE}.{os.getpid()}.tmp"
        self.assertNotEqual(tmp_a, tmp_b)

    def test_save_nao_derruba_o_jogo_se_replace_falhar(self):
        """Prova diferencial do crash relatado: mesmo se os.replace()
        falhar (arquivo temporário sumiu, outro processo segurando o
        destino etc.), save() não pode propagar a exceção pra cima —
        autosave falhar é aceitável, crashar o jogo durante alocação de
        talento não é."""
        import unittest.mock as mock
        with mock.patch("os.replace", side_effect=PermissionError(5, "Acesso negado")):
            try:
                config.save({"scale": 5.0})
            except PermissionError:
                self.fail("save() propagou PermissionError — deveria ter engolido")

    def test_window_mode_default_e_maximized(self):
        """Fase G (23/07/2026, pedido do usuário): janela maximizada é o
        padrão pra TODOS os jogadores, novos e existentes."""
        self.assertEqual(config.DEFAULTS["window_mode"], "maximized")
        data = config.load()
        self.assertEqual(data["window_mode"], "maximized")

    def test_config_existente_sem_window_mode_recebe_o_default(self):
        """Simula um config.json salvo ANTES desta chave existir (chave
        ausente do arquivo em disco) — o merge de load() com DEFAULTS tem
        que preencher 'maximized' mesmo assim, sem exigir migração manual."""
        with open(config.CONFIG_FILE, "w") as f:
            json.dump({"server_host": "1.2.3.4"}, f)
        data = config.load()
        self.assertEqual(data["window_mode"], "maximized")
        self.assertEqual(data["server_host"], "1.2.3.4")

    def test_window_mode_restaurado_pelo_jogador_e_persistido(self):
        config.save({"window_mode": "windowed"})
        data = config.load()
        self.assertEqual(data["window_mode"], "windowed")

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
