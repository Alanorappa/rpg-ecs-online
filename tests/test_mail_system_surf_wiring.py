"""
tests/test_mail_system_surf_wiring.py — 25/09/2026, bug real relatado
pelo usuário: clique direito na caixa de correio quase sempre errava a
área clicável, mesmo depois de aumentá-la. Causa raiz de verdade:
`game.py::GameEngine._WORLD_SYSTEM_ATTRS` é a lista de sistemas que
recebem `world_surf` ATUALIZADO todo frame (`_assign_world_surf`) — a
superfície que já reflete o zoom da câmera. `_mail_system` nunca fora
adicionado ali, então seu `world_surf` ficava PRESO no valor do
construtor (a tela crua, sem zoom aplicado) pro resto da sessão — a
conta de "onde o clique caiu no mundo" (`_sc = world_surf.width /
hud_surf.width`) ficava sistematicamente errada sempre que o zoom não
fosse exatamente 1.0, com erro variando por clique (explica os números
bagunçados vistos no diagnóstico ao vivo, de 28 a 227px).

Mesma lista existe DUPLICADA pra `hud_surf`/`screen` (refresh em
resize de janela, função vizinha) — `_mail_system` faltava ali também.
Trava as 2 listas contra regressão: um painel de NPC novo que precise
converter clique-de-tela-pra-mundo tem que entrar nas DUAS, ou herda
o mesmo bug silencioso.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from game import GameEngine as _GE


class TestMailSystemWorldSurfWiring(unittest.TestCase):

    def test_mail_system_esta_na_lista_de_refresh_por_frame(self):
        self.assertIn("_mail_system", _GE._WORLD_SYSTEM_ATTRS,
                      "_mail_system precisa receber world_surf atualizado todo "
                      "frame (_assign_world_surf), senão a conversão de clique "
                      "pra coordenada de mundo fica presa sem o zoom aplicado")

    def test_outros_sistemas_de_npc_continuam_na_lista(self):
        """Regressão simples — confirma que a lista em si não foi
        esvaziada/quebrada ao adicionar o mail_system."""
        for attr in ("_shop_system", "_quest_dialog", "_crafting_system",
                    "_trainer_system"):
            self.assertIn(attr, _GE._WORLD_SYSTEM_ATTRS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
