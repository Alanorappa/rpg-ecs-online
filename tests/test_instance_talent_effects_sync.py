"""
tests/test_instance_talent_effects_sync.py — 14/09/2026, bug real
relatado pelo usuário: "Nova Congelante" devia ser instantânea dentro
da BG (Precisão Elemental maxada pela normalização de instância, ver
server/instance_progression.py::enter_normalized_progression), mas
continuava mostrando 1s de cast — som tocava na hora, mas o cast
"fantasma" acontecia depois mesmo assim. Fora da BG funcionava
perfeitamente.

Causa raiz: `client/network_handlers.py::_handle_msg_stats_update`
recebe `talent_allocated` (payload de STATS_UPDATE emitido por
`instance_progression.py::_push_stats_update`) e só substituía
`TalentTree.allocated` — nunca chamava `ui.talent_system.
TalentSystem.apply_talent_effects()`, a única função que recalcula os
`cs_flags` comportamentais (`ice_cast_time_reduction`, entre outros) a
partir da alocação. O SERVIDOR sempre recalculou certo
(`WorldServer._apply_talent_modifiers`, chamado por
`enter_normalized_progression`) — por isso a skill em si (efeito real)
já completava instantaneamente; só a barra de cast VISUAL do cliente
(`ui/systems.py::_use_skill_visual_only`, que lê
`CombatStats.ice_cast_time_reduction` local) ficava presa no valor de
fora da instância. Ver PROBLEMAS_ARQUITETURA.md §136.
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
    PlayerControlled, TalentTree, CombatStats, PlayerSkills, CharacterStats,
)
from client.network_handlers import NetworkHandlers
from ui.talent_system import TalentSystem


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class FakeClientWithTalentSystem(NetworkHandlers):
    """Mesma fixture leve de tests/test_client_instance_sync.py, só que
    com `_talent_system` de verdade — precisa ser real (não mock) porque
    o bug é justamente sobre CHAMAR `apply_talent_effects()`, não só
    sobre o dict `allocated` mudar."""

    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()
        self._remote_players = {}
        self._remote_mobs = {}
        self._logged_char_name = None
        self._talent_system = TalentSystem(world, player_entity, pygame.display.get_surface())


class TestTalentAllocatedSyncAplicaEfeitosDerivados(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, CharacterStats(class_id="mago"))
        self.world.add_component(self.eid, CombatStats())
        self.world.add_component(self.eid, PlayerSkills())
        tt = TalentTree()
        tt.chosen_build = "piromania"
        tt.allocated = {}   # sem Precisão Elemental alocada (fora da instância)
        self.world.add_component(self.eid, tt)
        self.client = FakeClientWithTalentSystem(self.world, self.eid)

    def test_talent_allocated_maxado_zera_cast_time_de_nova_congelante(self):
        cs = self.world.get_component(self.eid, CombatStats)
        self.assertEqual(getattr(cs, "ice_cast_time_reduction", 0.0), 0.0,
            "sem Precisão Elemental alocada, redução deveria ser 0")

        payload = {
            "eid": self.eid, "talent_points": 0,
            "talent_allocated": {"pir_precisao_elemental": 5},  # maxado, BG normalizada
        }
        self.client._handle_msg_stats_update(payload)

        tt = self.world.get_component(self.eid, TalentTree)
        self.assertEqual(tt.allocated, {"pir_precisao_elemental": 5})

        cs = self.world.get_component(self.eid, CombatStats)
        self.assertAlmostEqual(getattr(cs, "ice_cast_time_reduction", 0.0), 1.0,
            msg="talent_allocated precisa recalcular cs_flags (apply_talent_effects), "
                "não só trocar o dict — sem isso Nova Congelante continua com 1s de cast")

    def test_talent_allocated_zerado_reseta_cast_time_reduction(self):
        # Simula saída da instância (fora, talento não maxado de verdade):
        # cs_flag precisa voltar ao valor real, não ficar "grudado" no
        # último valor calculado dentro da instância.
        cs = self.world.get_component(self.eid, CombatStats)
        cs.ice_cast_time_reduction = 1.0  # estado deixado pela instância

        payload = {"eid": self.eid, "talent_points": 3, "talent_allocated": {}}
        self.client._handle_msg_stats_update(payload)

        cs = self.world.get_component(self.eid, CombatStats)
        self.assertEqual(getattr(cs, "ice_cast_time_reduction", None), 0.0)


if __name__ == "__main__":
    unittest.main()
