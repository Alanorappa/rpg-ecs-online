"""
tests/test_transition_requires_movement.py

Bug real relatado pelo usuário (10/10/2026): personagem desconectava
DENTRO de uma dungeon instanciada; `DungeonProcessorMixin.end_dungeon_of`
(server/dungeon_processor.py) o devolvia pro `return_pos` salvo — que é,
por definição, o próprio tile de transição que ele pisou pra entrar (a
posição capturada no momento de `request_dungeon_enter`). Isso ficava
salvo no personagem. No login seguinte ele nascia de novo exatamente
EM CIMA desse tile de transição — e o check de "jogador parado num tile
de transição → auto-entra" (game.py, loop principal) disparava sozinho
no frame seguinte, sem o jogador ter andado até lá, reentrando a dungeon
a cada relog.

Fix: o check só dispara se o tile ATUAL for diferente do último tile
observado (ou seja, o jogador chegou ali andando) — nunca só por estar
parado em cima, o que cobre qualquer spawn/respawn/teleporte futuro que
caia sobre um tile de transição, não só este caso de dungeon.

Guarda via inspeção de fonte (mesmo padrão de
test_main_disconnect_on_quit.py — não dá pra montar um GameEngine real
num teste automatizado, pygame/net/world completos demais pra isolar).
"""
import os, re, unittest


class TestTransicaoExigeMovimento(unittest.TestCase):

    def _read_game_py(self) -> str:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(project_root, "game.py"), encoding="utf-8") as f:
            return f.read()

    def test_tracker_inicializado_no_init(self):
        src = self._read_game_py()
        self.assertIn("self._last_transition_check_tile", src)

    def test_build_transition_tiles_reseta_o_tracker(self):
        src = self._read_game_py()
        match = re.search(
            r'def _build_transition_tiles\(self, spawn_points: dict\):(.*?)\n    def ',
            src, re.DOTALL)
        self.assertIsNotNone(match, "não achei _build_transition_tiles em game.py")
        block = match.group(1)
        self.assertIn(
            "self._last_transition_check_tile = None", block,
            "_build_transition_tiles precisa resetar o tracker — sem isso, trocar de "
            "mapa carrega o último tile observado do mapa ANTERIOR por engano")

    def test_zone_change_req_so_dispara_se_tile_mudou(self):
        src = self._read_game_py()
        # Isola o bloco de detecção de transição inteiro (do comentário
        # até o fim do if/elif, delimitado pelo próximo comentário de
        # nível igual "# Portais com seleção de destino").
        match = re.search(
            r'# --- Detecção de transição \(cavernas / portais\) ---(.*?)'
            r'# Portais com seleção de destino',
            src, re.DOTALL)
        self.assertIsNotNone(match, "não achei o bloco de detecção de transição em game.py")
        block = match.group(1)

        self.assertIn(
            "key != self._last_transition_check_tile", block,
            "o disparo da transição precisa comparar contra o último tile observado "
            "(só dispara se o jogador CHEGOU andando) — sem isso, nascer/reaparecer "
            "em cima de um tile de transição (login, respawn, retorno de dungeon) "
            "reentra a transição sozinho no frame seguinte")

        # A condição que de fato manda ZONE_CHANGE_REQ precisa depender
        # da flag calculada acima, não só de `key in self.transition_tiles`.
        send_match = re.search(r'if (.*?) and key in self\.transition_tiles and self\._net:', block)
        self.assertIsNotNone(send_match, "não achei a condição que dispara ZONE_CHANGE_REQ")
        self.assertIn("_arrived_by_movement", send_match.group(1))


if __name__ == "__main__":
    unittest.main()
