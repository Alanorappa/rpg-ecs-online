"""
tests/test_restore_attack_interval.py — bug real reportado pelo usuário
(04/09/2026): personagem com espada equipada (attack_speed=2.1) mostrava
"Vel. Ataque: 2.60s" (default da classe) depois de relogar, mesmo com a
arma certa equipada — e o intervalo de ataque REAL em combate também
ficava no valor errado até algum evento aleatório (ex: outro modifier)
disparar um recálculo por acaso.

Causa raiz: client/save_sync_handlers.py::_restore_save_state escrevia
`cs.base_attack_interval` direto pra arma da mainhand, sem chamar
`_recalculate_effective_stats()` depois — CombatStats.attack_interval
(o campo EFETIVO, o que combate/UI realmente leem) só é atualizado por
esse recálculo, nunca lendo `base_attack_interval` sozinho. Uma arma
sem `modifiers` (ex: "training_sword") nunca disparava recálculo por
nenhum outro caminho, então o valor errado sobrevivia a sessão inteira.
"""
import os, sys, unittest, json
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.entity_factory import create_player
from engine.components import CombatStats, Equipment
from client.network_handlers import NetworkHandlers
from client.save_sync_handlers import SaveSyncHandlers
from client.inventory_handlers import InventoryHandlers


class _FakeNet:
    connected = True
    def send(self, *a, **k): pass


class _FakeClient(NetworkHandlers, SaveSyncHandlers, InventoryHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()


class TestRestoreAttackInterval(unittest.TestCase):

    def setUp(self):
        self.world  = World()
        self.eid    = create_player(self.world, 10, 10)
        self.client = _FakeClient(self.world, self.eid)

    def test_arma_sem_modifiers_atualiza_attack_interval_efetivo(self):
        """Espada de treinamento (attack_speed=2.1, modifiers=[]) — o caso
        exato relatado: sem NENHUM modifier pra disparar recálculo por
        acaso, o campo efetivo precisa ser corrigido pelo próprio restore
        de equipamento."""
        cs = self.world.get_component(self.eid, CombatStats)
        # Simula estado "errado" que a sessão anterior deixou (default da
        # classe, sem arma) — igual seria logo após criar o player.
        self.assertNotEqual(cs.base_attack_interval, 2.1)

        char_data = {
            "class_id": "guerreiro",
            "equipment_json": json.dumps({
                "mainhand": {"item_id": "training_sword", "name": "Espada de treinamento",
                            "item_type": "weapon", "slot": "mainhand", "rarity": "common",
                            "value": 10, "damage_min": 1, "damage_max": 2,
                            "attack_speed": 2.1, "subtype": "Sword"},
            }),
        }
        self.client._restore_save_state(char_data)

        equip = self.world.get_component(self.eid, Equipment)
        self.assertIsNotNone(equip.slots["mainhand"])
        self.assertEqual(cs.base_attack_interval, 2.1)
        # O bug real: base corrigia, mas attack_interval (efetivo, o que
        # combate/UI de fato leem) continuava travado no valor antigo.
        self.assertEqual(cs.attack_interval, 2.1)

    def test_sem_arma_equipada_usa_default_da_classe(self):
        cs = self.world.get_component(self.eid, CombatStats)
        default = cs._default_attack_interval
        self.client._restore_save_state({"class_id": "guerreiro", "equipment_json": "{}"})
        self.assertEqual(cs.base_attack_interval, default)
        self.assertEqual(cs.attack_interval, default)


if __name__ == "__main__":
    unittest.main()
