"""
tests/test_item_light.py — Item de bag aceso sob demanda (23/09/2026,
pedido do usuário: lampião que emite luz ao redor do jogador quando
ativado via clique direito, reaproveitável pra qualquer item futuro do
mesmo tipo). Ver PlayerLightSource/Item.light_source em
engine/components.py, WorldServer.toggle_item_light em
server/world_server.py.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import PlayerLightSource, Inventory


class TestLampiaoCatalogo(unittest.TestCase):
    """lampião saiu de content/quests_data.py::QUEST_ITEMS e entrou em
    content/item_table.py::ITEMS (23/09/2026, correção apontada pelo
    usuário — deixou de ser material puro de quest ao ganhar
    comportamento real)."""

    def test_lampiao_esta_no_catalogo_geral_com_light_source(self):
        from content.item_table import ITEMS
        self.assertIn("lampiao", ITEMS)
        item = ITEMS["lampiao"]()
        self.assertEqual(item.item_id, "lampiao")
        self.assertIsNotNone(item.light_source)
        self.assertIn("intensity_pct", item.light_source)
        self.assertIn("radius_tiles", item.light_source)

    def test_lampiao_nao_esta_mais_em_quest_items(self):
        from content.quests_data import QUEST_ITEMS
        self.assertNotIn("lampiao", QUEST_ITEMS)

    def test_resolve_item_by_id_ainda_encontra_lampiao(self):
        """A ordem de busca (item_table.py primeiro, quests_data.py depois)
        já é documentada no topo de quests_data.py — mover o item pro
        catálogo geral não pode quebrar reconstrução por item_id (usada
        por save/inventory sanitize)."""
        from content.item_table import resolve_item_by_id
        item = resolve_item_by_id("lampiao")
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, "lampiao")


class TestToggleItemLight(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.inv = self.ws.world.get_component(self.player_eid, Inventory)

    def _grant_lampiao(self):
        granted, _not_granted = self.ws.grant_items_to_player(self.player_eid, ["lampiao"])
        self.assertTrue(granted, "setup: lampião deveria caber na mochila vazia")

    def test_sem_posse_do_item_nao_liga_nada(self):
        self.ws.toggle_item_light("p1", {"item_id": "lampiao"})
        pls = self.ws.world.get_component(self.player_eid, PlayerLightSource)
        self.assertTrue(pls is None or not pls.active)
        self.assertEqual(self.ws._light_toggle_events_this_tick, [])

    def test_item_sem_light_source_nao_liga_nada(self):
        self.ws.grant_items_to_player(self.player_eid, ["training_sword"])
        self.ws.toggle_item_light("p1", {"item_id": "training_sword"})
        pls = self.ws.world.get_component(self.player_eid, PlayerLightSource)
        self.assertTrue(pls is None or not pls.active)
        self.assertEqual(self.ws._light_toggle_events_this_tick, [])

    def test_acender_com_posse_real_liga_e_enfileira_evento(self):
        self._grant_lampiao()
        self.ws.toggle_item_light("p1", {"item_id": "lampiao"})
        pls = self.ws.world.get_component(self.player_eid, PlayerLightSource)
        self.assertIsNotNone(pls)
        self.assertTrue(pls.active)
        self.assertEqual(pls.item_id, "lampiao")
        self.assertGreater(pls.intensity_pct, 0)
        self.assertGreater(pls.radius_tiles, 0)
        events = self.ws._light_toggle_events_this_tick
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eid"], self.player_eid)
        self.assertTrue(events[0]["active"])
        self.assertEqual(events[0]["item_id"], "lampiao")

    def test_clicar_no_mesmo_item_apaga(self):
        self._grant_lampiao()
        self.ws.toggle_item_light("p1", {"item_id": "lampiao"})
        self.ws._light_toggle_events_this_tick.clear()
        self.ws.toggle_item_light("p1", {"item_id": "lampiao"})
        pls = self.ws.world.get_component(self.player_eid, PlayerLightSource)
        self.assertFalse(pls.active)
        self.assertEqual(pls.item_id, "")
        events = self.ws._light_toggle_events_this_tick
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["active"])

    def test_intensidade_e_raio_nunca_vem_do_payload_do_cliente(self):
        """Mesma disciplina de apply_consumable (§152) — servidor NUNCA
        confia em valores de efeito vindos do cliente, só do catálogo real."""
        self._grant_lampiao()
        self.ws.toggle_item_light("p1", {
            "item_id": "lampiao", "intensity_pct": 999, "radius_tiles": 999,
        })
        pls = self.ws.world.get_component(self.player_eid, PlayerLightSource)
        self.assertLess(pls.intensity_pct, 999)
        self.assertLess(pls.radius_tiles, 999)

    def test_item_id_vazio_nao_crasha(self):
        self.ws.toggle_item_light("p1", {})
        self.assertEqual(self.ws._light_toggle_events_this_tick, [])

    def test_player_light_field_vazio_quando_inativo(self):
        self.assertEqual(self.ws.player_light_field(self.player_eid), {})

    def test_player_light_field_populado_quando_ativo(self):
        self._grant_lampiao()
        self.ws.toggle_item_light("p1", {"item_id": "lampiao"})
        field = self.ws.player_light_field(self.player_eid)
        self.assertTrue(field.get("light_active"))
        self.assertEqual(field.get("light_item_id"), "lampiao")
        self.assertIn("light_intensity_pct", field)
        self.assertIn("light_radius_tiles", field)


if __name__ == "__main__":
    unittest.main(verbosity=2)
