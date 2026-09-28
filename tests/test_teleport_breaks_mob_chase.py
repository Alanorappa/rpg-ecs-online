"""
tests/test_teleport_breaks_mob_chase.py — bug real relatado pelo usuário
(27/09/2026): zumbi perseguindo o player atravessou o mapa inteiro depois
que o player entrou num portal que teleporta pra outro ponto do MESMO
mapa. Causa raiz: `WorldServer.transfer_player()` só reescrevia a posição
do player — nenhum mob que o perseguia era avisado, então continuava
tentando alcançar a posição NOVA por qualquer distância, só desistindo
quando a distância do MOB até o PRÓPRIO spawn (nunca até o alvo) passasse
do raio de leash (`EnemyAISystem.MAX_LEASH_RADIUS`).

Fix: `engine.core_systems.force_mob_give_up_target()` — mesma transição
CHASING/ATTACKING→RETURNING que o leash normal já faz, extraída pra 1
dono só — chamada tanto pelo leash de sempre (distância acumulada) quanto,
agora, por `transfer_player()` (perda ABRUPTA de alvo, na hora do
teleporte, cobrindo mesmo-mapa e mudança-de-mapa igual).

Ver arquitetura/PROBLEMAS_ARQUITETURA.md §212.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from engine.components import AIControlled, CombatState, CombatStats, Visible, MapLocation, TileMovement
from engine.entity_factory import create_enemy
from engine.core_systems import force_mob_give_up_target


class TestForceMobGiveUpTarget(unittest.TestCase):
    """Teste unitário direto da função extraída — sem WorldServer."""

    def setUp(self):
        from engine.world import World
        self.world = World()
        self.mob_eid = self.world.create_entity()
        self.world.add_component(self.mob_eid, AIControlled())
        self.world.add_component(self.mob_eid, TileMovement(current_tile_x=5, current_tile_y=5))

    def _ai(self):
        return self.world.get_component(self.mob_eid, AIControlled)

    def test_transiciona_chasing_para_returning_e_limpa_alvo(self):
        ai = self._ai()
        ai.state = "CHASING"
        ai.target_eid = 999
        ai.aggroed_by_damage = True
        ai.path_recalc_timer = 5.0
        tm = self.world.get_component(self.mob_eid, TileMovement)
        tm.path = [(1, 1), (2, 2)]

        force_mob_give_up_target(self.world, self.mob_eid, 999)

        self.assertEqual(ai.state, "RETURNING")
        self.assertEqual(ai.target_eid, -1)
        self.assertFalse(ai.aggroed_by_damage)
        self.assertEqual(ai.path_recalc_timer, 0.0)
        self.assertEqual(tm.path, [])

    def test_no_op_se_mob_nao_persegue_este_alvo(self):
        ai = self._ai()
        ai.state = "CHASING"
        ai.target_eid = 111  # alvo DIFERENTE do que estamos "liberando"

        force_mob_give_up_target(self.world, self.mob_eid, 999)

        self.assertEqual(ai.state, "CHASING")
        self.assertEqual(ai.target_eid, 111)

    def test_no_op_se_mob_ja_estava_idle(self):
        ai = self._ai()
        ai.state = "IDLE"
        ai.target_eid = -1

        force_mob_give_up_target(self.world, self.mob_eid, 999)

        self.assertEqual(ai.state, "IDLE")

    def test_funciona_tambem_em_attacking(self):
        ai = self._ai()
        ai.state = "ATTACKING"
        ai.target_eid = 999

        force_mob_give_up_target(self.world, self.mob_eid, 999)

        self.assertEqual(ai.state, "RETURNING")
        self.assertEqual(ai.target_eid, -1)


class TestTransferPlayerLiberaMobsQuePerseguiam(unittest.TestCase):
    """Integração real com WorldServer.transfer_player — objeto de
    produção real, não fixture com o método colado (mesma régua de
    'testar objeto real' já usada no resto do projeto)."""

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.peid = self.ws._player_eids["s1"]

    def _spawn_chasing_mob(self, tx: int, ty: int, target_eid: int,
                           state: str = "CHASING", map_file: str | None = None) -> int:
        eid = create_enemy(self.ws.world, tx, ty, faction="monstros_hostis")
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        self.ws.world.add_component(eid, MapLocation(map_file or self.ws._map_file))
        set_entity_tile(self.ws, eid, tx, ty)
        ai = self.ws.world.get_component(eid, AIControlled)
        ai.state = state
        ai.target_eid = target_eid
        return eid

    def test_mob_perseguindo_desiste_ao_player_teleportar_pro_mesmo_mapa(self):
        mob_eid = self._spawn_chasing_mob(131, 375, self.peid)

        self.ws.transfer_player("s1", self.peid, self.ws._map_file, 210, 480)

        ai = self.ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "RETURNING")
        self.assertEqual(ai.target_eid, -1)

    def test_mob_perseguindo_desiste_ao_player_mudar_de_mapa(self):
        mob_eid = self._spawn_chasing_mob(131, 375, self.peid)

        self.ws.transfer_player("s1", self.peid, "maps/map_cave_east.csv", 10, 10)

        ai = self.ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "RETURNING")
        self.assertEqual(ai.target_eid, -1)

    def test_mob_perseguindo_outro_alvo_nao_e_afetado(self):
        """Só o mob que persegue O PLAYER QUE TELEPORTOU é liberado — um
        mob perseguindo outra entidade (outro player, minion) continua
        intocado."""
        OUTRO_ALVO = 555555
        mob_eid = self._spawn_chasing_mob(131, 375, OUTRO_ALVO)

        self.ws.transfer_player("s1", self.peid, self.ws._map_file, 210, 480)

        ai = self.ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "CHASING")
        self.assertEqual(ai.target_eid, OUTRO_ALVO)

    def test_mob_em_mapa_diferente_do_player_nao_e_afetado(self):
        mob_eid = self._spawn_chasing_mob(
            10, 10, self.peid, map_file="maps/map_cave_east.csv")

        self.ws.transfer_player("s1", self.peid, self.ws._map_file, 210, 480)

        ai = self.ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "CHASING")
        self.assertEqual(ai.target_eid, self.peid)

    def test_mob_idle_nao_e_afetado(self):
        """Mob IDLE (nunca chegou a perseguir de verdade) não deve ser
        tocado pela liberação — só CHASING/ATTACKING."""
        mob_eid = self._spawn_chasing_mob(131, 375, self.peid, state="IDLE")
        ai_before = self.ws.world.get_component(mob_eid, AIControlled)
        ai_before.target_eid = -1  # estado real de um mob IDLE de verdade

        self.ws.transfer_player("s1", self.peid, self.ws._map_file, 210, 480)

        ai = self.ws.world.get_component(mob_eid, AIControlled)
        self.assertEqual(ai.state, "IDLE")


if __name__ == "__main__":
    unittest.main()
