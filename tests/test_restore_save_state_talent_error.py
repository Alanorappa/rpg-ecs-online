"""
tests/test_restore_save_state_talent_error.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md: `client/save_sync_handlers.py::
_restore_save_state` engolia QUALQUER exceção de
`self._talent_system.apply_talent_effects()` em silêncio total (`except
Exception: pass`) — se algum dado de save corrompido/inesperado fizesse
essa chamada falhar, o personagem entrava sem NENHUM efeito de talento
aplicado, sem nenhum log, sem nenhum jeito de saber depois o que
aconteceu. Fix: mantém a captura (dado de save não é controlado por
nós), mas loga via print (mesma convenção client-side já usada em
client/network_handlers.py, ex: "[Client] login erro: ...").
"""
import os, sys, unittest, io, contextlib
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.entity_factory import create_player
from engine.components import TalentTree
from client.network_handlers import NetworkHandlers
from client.save_sync_handlers import SaveSyncHandlers
from client.inventory_handlers import InventoryHandlers


class _FakeNet:
    connected = True
    def send(self, *a, **k): pass


class _BoomingTalentSystem:
    """Stub que sempre falha — simula apply_talent_effects() batendo em
    dado de talento corrompido/inesperado vindo do save."""
    def apply_talent_effects(self):
        raise ValueError("talento corrompido de propósito, pro teste")


class _FakeClient(NetworkHandlers, SaveSyncHandlers, InventoryHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()
        self._talent_system = _BoomingTalentSystem()


class TestRestoreSaveStateTalentErrorIsLogged(unittest.TestCase):

    def setUp(self):
        self.world  = World()
        self.eid    = create_player(self.world, 10, 10)
        self.world.add_component(self.eid, TalentTree())
        self.client = _FakeClient(self.world, self.eid)

    def test_excecao_no_apply_talent_effects_nao_propaga_e_e_logada(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # Não deveria levantar — só logar. Se _restore_save_state
            # deixar a exceção escapar, este teste já falha na chamada.
            self.client._restore_save_state({"class_id": "guerreiro"})

        saida = buf.getvalue()
        self.assertIn("[Client]", saida)
        self.assertIn("apply_talent_effects", saida)
        self.assertIn("talento corrompido de propósito, pro teste", saida,
                      "a exceção real deveria aparecer no log, não só um aviso genérico")


if __name__ == "__main__":
    unittest.main()
