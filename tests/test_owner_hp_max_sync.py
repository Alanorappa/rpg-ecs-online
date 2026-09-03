"""
tests/test_owner_hp_max_sync.py — 01/09/2026, bug real relatado pelo
usuário: proc de item (Vontade Imortal, +stamina → +max_hp) fazia a
barra de HP do HUD "crescer" além do normal.

Causa raiz: `WorldServer._sync_player_hp_dirty()` já detecta QUALQUER
mudança de current_hp/max_hp genericamente (usado pra sincronizar outros
players via broadcast AOI), mas EXCLUÍA o próprio dono desse broadcast —
presumindo que ele "já tem via STATS_UPDATE" de outro canal. Falso pra
qualquer fonte que só muda stats via add_modifier/timed_modifiers sem
chamar queue_stats_update explicitamente (proc de item, buff de chefe de
jungle, self-buff de skill) — o max_hp do PRÓPRIO cliente nunca era
corrigido, current_hp eventualmente chegava via HP5/combate (que só
manda "hp_after", nunca "hp_max") com um max_hp desatualizado do lado
cliente — ratio current/max passava de 1.0, barra "crescia".

Fix: `_sync_player_hp_dirty()` agora TAMBÉM enfileira um STATS_UPDATE
privado pro próprio dono, reusando a MESMA detecção genérica — nenhuma
skill/talento precisa chamar isso à mão.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from unittest.mock import patch

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatState, CombatStats, Equipment
from content.item_table import resolve_item_by_id


def _stats_update_for(ws, eid):
    for entry in ws.consume_stats_updates():
        if entry.get("player_eid") == eid:
            return entry
    return None


class TestOwnerReceivesOwnHpMaxUpdate(unittest.TestCase):

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.cs  = self.ws.world.get_component(self.eid, CombatStats)
        self.cst = self.ws.world.get_component(self.eid, CombatState)
        # Drena qualquer STATS_UPDATE gerado pelo spawn, pra não contaminar
        # a asserção de "gerou 1 update por causa do proc".
        self.ws.consume_stats_updates()

    @patch("random.random", return_value=0.0)   # força o proc (chance=0.30)
    def test_proc_que_aumenta_stamina_notifica_o_dono(self, _mock_rand):
        equip = self.ws.world.get_component(self.eid, Equipment)
        equip.slots["neck"] = resolve_item_by_id("amulet_undying")   # +stamina proc

        max_before = self.cs.max_hp
        self.cst._just_entered_combat = True
        self.ws._combat_state_sys._roll_procs(self.eid, self.cs)
        self.assertGreater(self.cs.max_hp, max_before, "proc deveria ter aumentado max_hp")

        self.ws._sync_player_hp_dirty()
        entry = _stats_update_for(self.ws, self.eid)
        self.assertIsNotNone(entry, "dono deveria ter recebido STATS_UPDATE privado com o novo hp_max")
        self.assertEqual(entry["hp_max"], self.cs.max_hp)
        self.assertEqual(entry["hp"], self.cs.current_hp)

    def test_sem_mudanca_de_hp_nao_gera_update_novo(self):
        self.ws._sync_player_hp_dirty()   # baseline: registra o estado atual no cache
        self.ws.consume_stats_updates()
        self.ws._sync_player_hp_dirty()   # nada mudou desde a chamada anterior
        self.assertIsNone(_stats_update_for(self.ws, self.eid))

    def test_dano_normal_tambem_notifica_o_dono(self):
        self.cs.current_hp -= 10
        self.ws._sync_player_hp_dirty()
        entry = _stats_update_for(self.ws, self.eid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["hp"], self.cs.current_hp)
        self.assertEqual(entry["hp_max"], self.cs.max_hp)


if __name__ == "__main__":
    unittest.main()
