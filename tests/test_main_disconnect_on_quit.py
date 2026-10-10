"""
tests/test_main_disconnect_on_quit.py
Bug real relatado pelo usuário (09/10/2026): fechar o jogo (botão X da
janela OU "Sair do jogo" no menu de pausa) nunca chamava
`net.disconnect()` — só o caminho de "Deslogar" chamava. O servidor só
detectava a queda da conexão via ping_timeout (60s,
server/session.py::_handle_login varre `_sessions` por
`authenticated and username==` pra recusar login duplicado) — um login
novo nesse meio-tempo batia em `LOGIN_ERROR "already_online"` mesmo a
conta estando de fato livre. Mesmo mecanismo já catalogado em
PROBLEMAS_ARQUITETURA.md §119 (lá era vazamento de conexão num bot de
loadtest; aqui é o cliente real nunca fechando a conexão no quit
normal).

Guarda via inspeção de fonte (não dá pra testar o fluxo inteiro de
main.py sem montar um GameEngine real) — garante que
`net.disconnect()` é chamado dentro do branch "ação != logout" ANTES
de `raise SystemExit(0)`, nunca só nesse último.
"""
import os, re, unittest


class TestNetDisconnectChamadoAoFecharOJogo(unittest.TestCase):

    def test_branch_nao_logout_chama_net_disconnect_antes_do_systemexit(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(project_root, "main.py"), encoding="utf-8") as f:
            src = f.read()

        # Isola o bloco `if action != "logout": ... raise SystemExit(0)`
        match = re.search(
            r'if action != "logout":(.*?)raise SystemExit\(0\)',
            src, re.DOTALL)
        self.assertIsNotNone(match, "não achei o branch 'action != logout' em main.py")

        block = match.group(1)
        self.assertIn("net.disconnect()", block,
                      "fechar o jogo (botão X / 'Sair do jogo') precisa avisar o servidor "
                      "via net.disconnect() antes de sair — sem isso, o servidor só "
                      "percebe a queda via ping_timeout (60s), bloqueando um login "
                      "novo com 'already_online' nesse meio-tempo")


if __name__ == "__main__":
    unittest.main()
