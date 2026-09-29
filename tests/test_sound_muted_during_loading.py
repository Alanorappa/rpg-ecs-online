"""
tests/test_sound_muted_during_loading.py — 28/09/2026, pedido do
usuário: o cliente já processa mensagens de rede (AOI_UPDATE de quem
está por perto, incluindo combate) durante a tela de loading inicial —
sem gate nenhum, `SoundManager.play()` tocava esses sons ANTES do
jogador "entrar" de fato no jogo. `SOUNDS.set_game_ready(False)`
suprime SFX de gameplay (nunca ambiente/música) até a tela de loading
terminar. Ver PROBLEMAS_ARQUITETURA.md §215.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame

pygame.init()
pygame.mixer.init()

from ui.sound_manager import SoundManager


class TestSetGameReady(unittest.TestCase):
    def setUp(self):
        pygame.mixer.set_num_channels(28)   # mesmo teto que SoundManager.init() usa
        pygame.mixer.stop()   # zera qualquer canal ocupado por um teste anterior
        self.sm = SoundManager()
        self.sm._ready = True   # simula init() já ter rodado com sucesso

    def _played_something(self, name: str) -> bool:
        """`play()` só de fato chama `ch.play(sound)` se achar um canal
        livre E o som estiver em cache — usamos um som fake direto no
        cache pra não depender de arquivo real, e checamos via
        `pygame.mixer.get_busy()` teria falso-positivo de outros
        testes; em vez disso, verificamos indiretamente: se o canal
        NUNCA tocou nada, `_find_free_channel` continua livre depois."""
        self.sm.play(name)
        # Se tocou de verdade, o canal escolhido fica ocupado (get_busy).
        # Procura QUALQUER canal ocupado no grupo geral.
        import ui.sound_manager as sm_mod
        for ch_id in sm_mod._CH_GENERAL:
            if pygame.mixer.Channel(ch_id).get_busy():
                return True
        return False

    def _make_fake_sound(self) -> "pygame.mixer.Sound":
        # Sound() a partir de um buffer vazio válido — não precisa de
        # arquivo real no disco pra este teste.
        return pygame.mixer.Sound(buffer=bytes(4410 * 2))

    def test_play_funciona_normalmente_por_default(self):
        self.sm._cache["fake_sfx"] = self._make_fake_sound()
        self.assertTrue(self.sm._game_ready, "default deveria ser True")
        self.assertTrue(self._played_something("fake_sfx"))

    def test_set_game_ready_false_suprime_play(self):
        self.sm._cache["fake_sfx2"] = self._make_fake_sound()
        self.sm.set_game_ready(False)
        self.assertFalse(self._played_something("fake_sfx2"))

    def test_set_game_ready_true_restaura_play(self):
        self.sm._cache["fake_sfx3"] = self._make_fake_sound()
        self.sm.set_game_ready(False)
        self.sm.set_game_ready(True)
        self.assertTrue(self._played_something("fake_sfx3"))


if __name__ == "__main__":
    unittest.main()
