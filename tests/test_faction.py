"""
tests/test_faction.py — Fase 1 do sistema de facções (ARQUITETURA_ONLINE.md,
"Sistema de Facções"). Testa content/faction_data.py e engine/faction_system.py
isoladamente, sem rodar EnemyAISystem/CombatSystem (ainda não os consultam
nesta fase — infraestrutura pura).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import Faction, PlayerControlled
from content.faction_data import get_relationship, PLAYER_FACTION, DEFAULT_RELATIONSHIP
from engine.faction_system import (get_entity_faction, get_relationship_between,
                                   can_engage, is_hostile)


class TestGetRelationship(unittest.TestCase):

    def test_mesma_faccao_e_sempre_amigavel(self):
        self.assertEqual(get_relationship("bandidos", "bandidos"), "amigavel")

    def test_par_conhecido_hostil(self):
        self.assertEqual(get_relationship("monstros_hostis", PLAYER_FACTION), "hostil")

    def test_par_conhecido_neutro(self):
        self.assertEqual(get_relationship("vida_selvagem", PLAYER_FACTION), "neutro")

    def test_par_conhecido_amigavel(self):
        self.assertEqual(get_relationship("guardas_vila", PLAYER_FACTION), "amigavel")

    def test_busca_e_simetrica(self):
        # ("bandidos", "guardas_vila") está na tabela nessa ordem — a busca
        # invertida também deve resolver ao mesmo tier.
        direto    = get_relationship("bandidos", "guardas_vila")
        invertido = get_relationship("guardas_vila", "bandidos")
        self.assertEqual(direto, invertido)
        self.assertEqual(direto, "hostil")

    def test_par_desconhecido_cai_no_default(self):
        self.assertEqual(get_relationship("faccao_inventada_no_teste", PLAYER_FACTION),
                         DEFAULT_RELATIONSHIP)


class TestFactionSystemHelpers(unittest.TestCase):

    def setUp(self):
        self.world = World()

    def _make_faction_entity(self, faction_id: str) -> int:
        eid = self.world.create_entity()
        self.world.add_component(eid, Faction(faction_id=faction_id))
        return eid

    def _make_player_entity(self) -> int:
        eid = self.world.create_entity()
        self.world.add_component(eid, PlayerControlled())
        return eid

    def test_get_entity_faction_com_componente_faction(self):
        eid = self._make_faction_entity("bandidos")
        self.assertEqual(get_entity_faction(self.world, eid), "bandidos")

    def test_get_entity_faction_de_player(self):
        eid = self._make_player_entity()
        self.assertEqual(get_entity_faction(self.world, eid), PLAYER_FACTION)

    def test_get_entity_faction_sem_componente_nenhum(self):
        eid = self.world.create_entity()   # NPC estático/blocker/dummy
        # Não deve lançar exceção, e não deve resolver pra nenhuma facção
        # de verdade (sentinela interno).
        faction = get_entity_faction(self.world, eid)
        self.assertNotEqual(faction, PLAYER_FACTION)
        self.assertNotEqual(faction, "bandidos")

    def test_relationship_between_mob_hostil_e_player(self):
        mob    = self._make_faction_entity("monstros_hostis")
        player = self._make_player_entity()
        self.assertEqual(get_relationship_between(self.world, mob, player), "hostil")

    def test_relationship_between_entidade_sem_faccao_nunca_e_hostil(self):
        blocker = self.world.create_entity()   # sem Faction, sem PlayerControlled
        player  = self._make_player_entity()
        rel = get_relationship_between(self.world, blocker, player)
        self.assertNotEqual(rel, "hostil")

    def test_can_engage_tabela_verdade(self):
        hostil_eid   = self._make_faction_entity("monstros_hostis")
        neutro_eid   = self._make_faction_entity("vida_selvagem")
        amigavel_eid = self._make_faction_entity("guardas_vila")
        player       = self._make_player_entity()

        self.assertTrue(can_engage(self.world, hostil_eid, player))
        self.assertTrue(can_engage(self.world, neutro_eid, player))
        self.assertFalse(can_engage(self.world, amigavel_eid, player))

    def test_is_hostile_so_true_pro_tier_hostil(self):
        hostil_eid   = self._make_faction_entity("monstros_hostis")
        neutro_eid   = self._make_faction_entity("vida_selvagem")
        amigavel_eid = self._make_faction_entity("guardas_vila")
        player       = self._make_player_entity()

        self.assertTrue(is_hostile(self.world, hostil_eid, player))
        self.assertFalse(is_hostile(self.world, neutro_eid, player))
        self.assertFalse(is_hostile(self.world, amigavel_eid, player))

    def test_bandidos_vs_guardas_vila_e_hostil_sem_envolver_player(self):
        bandido = self._make_faction_entity("bandidos")
        guarda  = self._make_faction_entity("guardas_vila")
        self.assertTrue(is_hostile(self.world, bandido, guarda))
        # guardas_vila-vs-bandidos é "hostil" (não amigavel) -> can_engage True
        self.assertTrue(can_engage(self.world, guarda, bandido))


if __name__ == "__main__":
    unittest.main()
