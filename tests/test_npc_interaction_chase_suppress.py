"""
tests/test_npc_interaction_chase_suppress.py — bug real reportado pelo
usuário (04/09/2026): atacando um alvo travado (ex: boneco de treino),
clicar com o direito num NPC amigável (treinador/ferreiro/quem dá
quest/mercador) fazia o personagem oscilar entre andar até o NPC e ser
puxado de volta pro alvo antigo pelo auto-chase — nenhum dos 4 sistemas
de NPC suprimia a perseguição de combate ao serem clicados.

Fix: `ui/npc_interaction.py::walk_to_npc`/`suppress_chase` — ponto único
de verdade, chamado pelos 4 sistemas (TrainerSystem/CraftingSystem/
QuestDialogSystem/ShopSystem) tanto ao iniciar a caminhada até o NPC
quanto ao abrir o diálogo direto (já adjacente). NUNCA mexe em
`target_entity_id`/`is_pursuing` — só pausa o auto-walk de volta ao
alvo (mesma flag que clicar no chão já usa, o que preserva o kite do
arqueiro: alvo continua selecionado, combate não é interrompido).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import (
    CombatState, PlayerAutoMove, TileMovement, Position, Renderable,
    NPC, Trainer, Merchant, QuestGiver, Blacksmith,
)
from ui.npc_interaction import walk_to_npc, suppress_chase
from ui.trainer_system import TrainerSystem
from ui.crafting_system import BlacksmithSystem
from ui.quest_system import QuestSystem, QuestDialogSystem
from ui.systems import ShopSystem


def _make_world_with_player(px=10, py=10):
    world = World()
    player = world.create_entity()
    world.add_component(player, TileMovement(current_tile_x=px, current_tile_y=py))
    world.add_component(player, Position(x=px * 32, y=py * 32))
    world.add_component(player, PlayerAutoMove())
    cs = CombatState()
    cs.target_entity_id = 9999   # simula "boneco de treino" travado como alvo
    cs.is_pursuing = True
    world.add_component(player, cs)
    return world, player


class TestWalkToNpcHelper(unittest.TestCase):
    """Testa a função compartilhada isoladamente."""

    def test_walk_to_npc_seta_ground_target_e_suprime_chase(self):
        world, player = _make_world_with_player()
        walk_to_npc(world, player, (10, 10), (15, 15))

        auto = world.get_component(player, PlayerAutoMove)
        self.assertIsNotNone(auto.ground_target)
        self.assertTrue(auto.active)

        cs = world.get_component(player, CombatState)
        self.assertTrue(cs.chase_suppressed)
        # NUNCA mexe no alvo/perseguição — só pausa o auto-walk de volta.
        self.assertEqual(cs.target_entity_id, 9999)
        self.assertTrue(cs.is_pursuing)

    def test_walk_to_npc_escolhe_tile_adjacente_mais_perto_do_player(self):
        world, player = _make_world_with_player(px=10, py=10)
        walk_to_npc(world, player, (10, 10), (20, 20))
        auto = world.get_component(player, PlayerAutoMove)
        # Dos 4 adjacentes de (20,20), o mais perto de (10,10) é (19,20) ou (20,19)
        self.assertIn(auto.ground_target, [(19, 20), (20, 19)])

    def test_walk_to_npc_sem_playerautomove_nao_crasha(self):
        world = World()
        player = world.create_entity()
        world.add_component(player, CombatState())
        walk_to_npc(world, player, (0, 0), (5, 5))  # não deve lançar exceção

    def test_suppress_chase_nao_mexe_em_mais_nada(self):
        world, player = _make_world_with_player()
        auto_before = world.get_component(player, PlayerAutoMove)
        auto_before.ground_target = "sentinela"
        suppress_chase(world, player)
        cs = world.get_component(player, CombatState)
        self.assertTrue(cs.chase_suppressed)
        self.assertEqual(auto_before.ground_target, "sentinela")   # intocado


class TestNpcSystemsSuppressChaseOnInteract(unittest.TestCase):
    """Reproduz o cenário exato relatado: alvo travado (boneco/mob) +
    clique com o direito num NPC amigável de cada um dos 4 sistemas."""

    def setUp(self):
        self.world, self.player = _make_world_with_player(px=10, py=10)
        self.screen = pygame.Surface((800, 600))

    def _cs(self):
        return self.world.get_component(self.player, CombatState)

    def test_trainer_system_ja_adjacente_suprime_chase(self):
        npc = self.world.create_entity()
        self.world.add_component(npc, NPC(name="Treinador"))
        self.world.add_component(npc, Trainer(class_id="guerreiro"))
        self.world.add_component(npc, Position(x=11 * 32, y=10 * 32))
        self.world.add_component(npc, Renderable(color=(200,200,200), width=32, height=32))
        self.world.add_component(npc, TileMovement(current_tile_x=11, current_tile_y=10))

        sysm = TrainerSystem(self.world, self.player, self.screen)
        sysm.set_ui_scale(1.0)
        sysm.world_surf = self.screen
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(400, 300), button=3)
        # Câmera não configurada no teste (sem entidade Camera) — cx/cy=0,
        # então a posição de tela bate direto com a posição de mundo do NPC.
        self.world.get_component(npc, Position).x = 400
        self.world.get_component(npc, Position).y = 300
        sysm.update([ev], dt=0.016)

        self.assertTrue(self._cs().chase_suppressed)
        self.assertEqual(self._cs().target_entity_id, 9999)   # alvo intocado

    def test_trainer_system_longe_anda_e_suprime_chase(self):
        npc = self.world.create_entity()
        self.world.add_component(npc, NPC(name="Treinador"))
        self.world.add_component(npc, Trainer(class_id="guerreiro"))
        self.world.add_component(npc, Position(x=400, y=300))
        self.world.add_component(npc, Renderable(color=(200,200,200), width=32, height=32))
        self.world.add_component(npc, TileMovement(current_tile_x=50, current_tile_y=50))

        sysm = TrainerSystem(self.world, self.player, self.screen)
        sysm.set_ui_scale(1.0)
        sysm.world_surf = self.screen
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(400, 300), button=3)
        sysm.update([ev], dt=0.016)

        self.assertTrue(self._cs().chase_suppressed)
        auto = self.world.get_component(self.player, PlayerAutoMove)
        self.assertIsNotNone(auto.ground_target)

    def test_quest_dialog_system_ja_adjacente_suprime_chase(self):
        npc = self.world.create_entity()
        self.world.add_component(npc, NPC(name="Missiveiro"))
        self.world.add_component(npc, QuestGiver(quest_ids=(), turn_in_ids=()))
        self.world.add_component(npc, Position(x=400, y=300))
        self.world.add_component(npc, Renderable(color=(200,200,200), width=32, height=32))
        self.world.add_component(npc, TileMovement(current_tile_x=11, current_tile_y=10))

        qs  = QuestSystem(self.world, self.player)
        qs.set_ui_scale(1.0)
        dlg = QuestDialogSystem(self.world, self.player, self.screen, qs)
        dlg.set_ui_scale(1.0)
        dlg.world_surf = self.screen
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(400, 300), button=3)
        dlg.update([ev], dt=0.016)

        self.assertTrue(self._cs().chase_suppressed)

    def test_shop_system_ja_adjacente_suprime_chase(self):
        npc = self.world.create_entity()
        self.world.add_component(npc, NPC(name="Comerciante"))
        self.world.add_component(npc, Merchant())
        self.world.add_component(npc, Position(x=400, y=300))
        self.world.add_component(npc, Renderable(color=(200,200,200), width=32, height=32))
        self.world.add_component(npc, TileMovement(current_tile_x=11, current_tile_y=10))

        shop = ShopSystem(self.world, self.player, self.screen)
        shop.set_ui_scale(1.0)
        shop.world_surf = self.screen
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(400, 300), button=3)
        shop.update([ev], dt=0.016)

        self.assertTrue(self._cs().chase_suppressed)

    def test_crafting_system_ja_adjacente_suprime_chase(self):
        npc = self.world.create_entity()
        self.world.add_component(npc, NPC(name="Ferreiro"))
        self.world.add_component(npc, Blacksmith())
        self.world.add_component(npc, Position(x=400, y=300))
        self.world.add_component(npc, Renderable(color=(200,200,200), width=32, height=32))
        self.world.add_component(npc, TileMovement(current_tile_x=11, current_tile_y=10))

        cft = BlacksmithSystem(self.world, self.player, self.screen)
        cft.set_ui_scale(1.0)
        cft.world_surf = self.screen
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(400, 300), button=3)
        cft.update([ev], dt=0.016)

        self.assertTrue(self._cs().chase_suppressed)


if __name__ == "__main__":
    unittest.main()
