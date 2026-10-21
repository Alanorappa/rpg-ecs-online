"""
tests/test_npc_interaction_request.py — 15/09/2026, item 4 (crítico) da
revisão de performance/arquitetura (arquitetura/CLIENT_PERFORMANCE_REVIEW.md).

Antes, `BlacksmithSystem`/`TrainerSystem` chamavam métodos PRIVADOS de
`ShopSystem`/`QuestDialogSystem` direto numa instância injetada no
construtor (`self._shop_system.open_for(eid)`,
`self._quest_dialog._open_dialog(eid)`) — violava a regra do projeto
("um System nunca chama outro System diretamente"). Fix: componente
`NPCInteractionRequest` (engine/components.py) — quem PEDE escreve
`kind`+`target_eid` no player; quem ABRE lê e consome no início do
próprio `update()`, sem nenhuma referência cruzada entre as classes.

Também cobre um bug real achado corrigindo isto: `BlacksmithSystem.
_draw_action_menu` chamava `self._quest_system.can_start(qid)` — método
que NUNCA existiu em `QuestSystem` (`grep -rn "def can_start"` no
projeto inteiro não acha nada). Como `quest_system` sempre é injetado de
verdade pelo `GameEngine` (game.py), essa chamada quebraria com
AttributeError assim que um ferreiro tivesse QUALQUER quest disponível
pra iniciar — nunca pego porque nenhum teste automatizado exercitava
esse caminho antes.
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
    CharacterStats, QuestLog, PlayerControlled, QuestGiver, Blacksmith,
    NPC, Merchant, NPCInteractionRequest,
)
from ui.crafting_system import BlacksmithSystem
from ui.systems import ShopSystem
from ui.quest_system import QuestDialogSystem, QuestSystem
from ui.ui_components import ShopUIState
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef

QID = "nir_test_quest"


class TestNPCInteractionRequest(unittest.TestCase):

    def setUp(self):
        QUESTS[QID] = QuestDef(
            title="Quest NIR",
            description="d",
            objectives=(ObjectiveDef(type="kill", target="lobo", count=1),),
            reward=QuestReward(xp=10),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Test", class_id="guerreiro"))
        self.world.add_component(self.player, QuestLog())
        self.world.add_component(self.player, PlayerControlled())
        self.world.add_component(self.player, ShopUIState())

        self.npc = self.world.create_entity()
        self.world.add_component(self.npc, NPC(name="Ferreiro"))
        self.world.add_component(self.npc, Merchant())
        self.world.add_component(self.npc, Blacksmith())
        self.world.add_component(self.npc, QuestGiver(quest_ids=(QID,)))

        screen = pygame.Surface((800, 600))
        self.blacksmith = BlacksmithSystem(self.world, self.player, screen)
        self.blacksmith.set_ui_scale(1.0)
        self.shop = ShopSystem(self.world, self.player, screen)
        self.shop.set_ui_scale(1.0)
        self.qs = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(QID, None)

    def test_aba_negociar_abre_a_loja_via_componente_sem_referencia_direta(self):
        """Abas de serviço do NPC (21/10/2026): trocar de aba só escreve
        NPCInteractionRequest — quem abre é o System dono (aqui a loja)."""
        from ui.npc_interaction import open_npc_service
        open_npc_service(self.world, self.player, self.npc, "shop")

        req = self.world.get_component(self.player, NPCInteractionRequest)
        self.assertIsNotNone(req)
        self.assertEqual(req.kind, "open_shop")
        self.assertEqual(req.target_eid, self.npc)

        self.shop.update([], 0.0)
        self.assertEqual(self.shop.open_merchant_id, self.npc,
                         "ShopSystem.update() deveria consumir o request e abrir a loja")
        self.assertIsNone(self.world.get_component(self.player, NPCInteractionRequest),
                          "componente deveria ser removido depois de consumido")
    def test_shop_system_ignora_request_destinado_a_outro_kind(self):
        """Design anti-corrida: cada System só remove o componente quando
        `kind` é o SEU próprio — senão, quem rodar primeiro no frame
        "rouba" o request de quem realmente deveria consumi-lo."""
        self.world.add_component(self.player, NPCInteractionRequest(
            kind="open_quest_dialog", target_eid=self.npc))
        self.shop.update([], 0.0)
        self.assertEqual(self.shop.open_merchant_id, -1,
                         "ShopSystem não deveria agir sobre um request de outro kind")
        req = self.world.get_component(self.player, NPCInteractionRequest)
        self.assertIsNotNone(req, "ShopSystem não deveria consumir um request que não é seu")
        self.assertEqual(req.kind, "open_quest_dialog")

    def test_npc_com_quest_abre_missoes_primeiro_com_abas_das_outras_funcoes(self):
        """Pedido do usuário (21/10/2026): NPC com quest abre SEMPRE a aba
        de Missões primeiro (sem menu de 3 botões); as outras funções
        (Negociar/Reciclar/Forjar) viram abas na mesma janela. Também
        cobre a regressão do can_start (quest disponível não quebra)."""
        from ui.npc_interaction import npc_services
        self.assertEqual([k for k, _ in npc_services(self.world, self.player, self.npc)],
                         ["quest", "shop", "recycle", "forge"])
        self.blacksmith._open_menu(self.npc)
        req = self.world.get_component(self.player, NPCInteractionRequest)
        self.assertEqual(req.kind, "open_quest_dialog")
        self.dlg.update([], 0.0)
        self.assertTrue(self.dlg.is_open)
        self.dlg.render()
        for key in ("shop", "recycle", "forge"):
            self.assertIn(f"npc:tab:{key}", self.dlg._hit_rects)

if __name__ == "__main__":
    unittest.main()
