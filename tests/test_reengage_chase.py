"""
tests/test_reengage_chase.py — 09/09/2026, bug real relatado pelo
usuário: usando Interceptar (ou qualquer skill ofensiva) enquanto um
clique de chão ainda estava em andamento, o personagem fazia o dash e
depois ficava num loop "indo e voltando" entre o tile clicado e o alvo.

Causa raiz: reativar a perseguição de combate (`chase_suppressed =
False`) ao usar uma skill NUNCA cancelava o clique de chão pendente
(`PlayerAutoMove.ground_target`/`path`) — os dois sistemas (auto-chase
e auto-move de chão) competiam pela posição do player todo frame.
Mesma classe de bug já corrigida pra clique em NPC
(`ui/npc_interaction.py::suppress_chase`, PROBLEMAS_ARQUITETURA.md
§112) — o inverso (reengajar) nunca tinha o cancelamento simétrico.

Fix: `engine/utils.py::reengage_chase` — ponto único de verdade,
chamado nos 6 lugares que reativavam `chase_suppressed` (2 deles já
cancelavam `ground_target` na mão, de forma duplicada; 2 outros —
`ui/systems.py::_use_skill_visual_only` e
`engine/skill_handlers.py::_skill_bola_de_fogo` — nunca cancelavam,
essa é a causa real do bug relatado)."""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import CombatState, PlayerAutoMove
from engine.utils import reengage_chase


def _make_world_with_pending_ground_click():
    """Simula o cenário exato relatado: alvo de combate travado (chase
    suprimido por um clique de chão anterior) + clique de chão ainda em
    andamento rumo a um tile distante do alvo."""
    world = World()
    player = world.create_entity()
    cs = CombatState()
    cs.target_entity_id = 9999   # mob/bot travado como alvo
    cs.is_pursuing = True
    cs.chase_suppressed = True   # já suprimido pelo clique de chão anterior
    world.add_component(player, cs)
    auto = PlayerAutoMove()
    auto.active = True
    auto.ground_target = (50, 50)   # tile clicado, longe do alvo
    auto.path = [(11, 10), (12, 10), (13, 10)]
    auto.follow_eid = -1
    world.add_component(player, auto)
    return world, player, cs, auto


class TestReengageChase(unittest.TestCase):

    def test_reengage_chase_reativa_perseguicao_e_cancela_clique_de_chao(self):
        world, player, cs, auto = _make_world_with_pending_ground_click()

        reengage_chase(world, player)

        self.assertFalse(cs.chase_suppressed,
                         "reengajar deveria desligar chase_suppressed")
        self.assertIsNone(auto.ground_target,
                          "clique de chão pendente deveria ser cancelado — "
                          "é a causa real do loop 'indo e voltando'")
        self.assertEqual(auto.path, [])
        self.assertFalse(auto.active)
        self.assertEqual(auto.follow_eid, -1)

    def test_reengage_chase_nunca_toca_target_entity_id_ou_is_pursuing(self):
        """Mesma garantia de `suppress_chase` (seu inverso simétrico) —
        só a flag de auto-walk e o auto-move de chão são afetados; alvo
        e combate em si continuam 100% intocados."""
        world, player, cs, auto = _make_world_with_pending_ground_click()

        reengage_chase(world, player)

        self.assertEqual(cs.target_entity_id, 9999)
        self.assertTrue(cs.is_pursuing)

    def test_reengage_chase_sem_playerautomove_nao_quebra(self):
        """Roda em código compartilhado client/servidor
        (`engine/skill_handlers.py`) — no servidor a entidade não tem
        `PlayerAutoMove` (componente client-only). Não deve lançar."""
        world = World()
        player = world.create_entity()
        cs = CombatState()
        cs.chase_suppressed = True
        world.add_component(player, cs)

        reengage_chase(world, player)   # não deve lançar

        self.assertFalse(cs.chase_suppressed)

    def test_reengage_chase_cancela_seguir_pendente_tambem(self):
        world, player, cs, auto = _make_world_with_pending_ground_click()
        auto.ground_target = None
        auto.follow_eid = 42   # "Seguir" outro player em andamento

        reengage_chase(world, player)

        self.assertEqual(auto.follow_eid, -1)


if __name__ == "__main__":
    unittest.main()
