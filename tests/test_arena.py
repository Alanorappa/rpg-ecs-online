"""
tests/test_arena.py — Arena 2x2 (Fase G, leva 1): instanciamento privado
por partida, time por Facção, fila FIFO de grupos, eliminação (golpe
letal não mata de verdade). Ver server/match_processor.py e
ARQUITETURA_ONLINE.md.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.faction_system import can_engage
from engine.core_systems import apply_damage_core
from engine.components import CombatStats, CombatState, Faction, TileMovement


def _make_duo(ws, prefix: str, tile=(130, 374)):
    a = spawn_player(ws, f"{prefix}_a", *tile)
    b = spawn_player(ws, f"{prefix}_b", *tile)
    ws.request_party_invite(a, b)
    ws.respond_party_invite(b, accept=True)
    return a, b


def _queue_and_pair(ws, team_a, team_b):
    ws.request_arena_queue_join(team_a[0])
    ws.request_arena_queue_join(team_b[0])
    ws._tick_arena_queue()
    return ws._player_match_id[team_a[0]]


class TestArenaQueue(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_grupo_de_2_entra_na_fila(self):
        a, b = _make_duo(self.ws, "g1")
        pid = self.ws.get_party_id_of(a)
        self.assertIsNone(self.ws.request_arena_queue_join(a))
        self.assertIn(pid, self.ws._arena_queue_2v2)

    def test_grupo_com_tamanho_diferente_de_2_e_recusado(self):
        solo = spawn_player(self.ws, "solo", 130, 374)
        # solo não está em grupo nenhum
        self.assertEqual(self.ws.request_arena_queue_join(solo), "no_party")

        trio_a = spawn_player(self.ws, "t_a", 130, 374)
        trio_b = spawn_player(self.ws, "t_b", 131, 374)
        trio_c = spawn_player(self.ws, "t_c", 132, 374)
        self.ws.request_party_invite(trio_a, trio_b)
        self.ws.respond_party_invite(trio_b, accept=True)
        self.ws.request_party_invite(trio_a, trio_c)
        self.ws.respond_party_invite(trio_c, accept=True)
        self.assertEqual(self.ws.request_arena_queue_join(trio_a), "wrong_size")

    def test_so_lider_pode_enfileirar(self):
        a, b = _make_duo(self.ws, "g2")
        self.assertEqual(self.ws.request_arena_queue_join(b), "not_leader")

    def test_fila_pareia_fifo_os_2_primeiros_grupos(self):
        team_a = _make_duo(self.ws, "fa")
        team_b = _make_duo(self.ws, "fb")
        team_c = _make_duo(self.ws, "fc")
        self.ws.request_arena_queue_join(team_a[0])
        self.ws.request_arena_queue_join(team_b[0])
        self.ws.request_arena_queue_join(team_c[0])
        self.ws._tick_arena_queue()
        # a+b pareados (partida criada), c continua sozinho na fila
        self.assertEqual(len(self.ws._active_matches), 1)
        self.assertEqual(len(self.ws._arena_queue_2v2), 1)
        for eid in team_a + team_b:
            self.assertIn(eid, self.ws._player_match_id)
        for eid in team_c:
            self.assertNotIn(eid, self.ws._player_match_id)


class TestArenaMatch(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.team_a = _make_duo(self.ws, "ta")
        self.team_b = _make_duo(self.ws, "tb")
        self.match_id = _queue_and_pair(self.ws, self.team_a, self.team_b)

    def test_times_ganham_faccao_oposta(self):
        fac_a0 = self.ws.world.get_component(self.team_a[0], Faction)
        fac_b0 = self.ws.world.get_component(self.team_b[0], Faction)
        self.assertEqual(fac_a0.faction_id, "arena_time_a")
        self.assertEqual(fac_b0.faction_id, "arena_time_b")

    def test_can_engage_libera_entre_times_opostos_bloqueia_mesmo_time(self):
        self.assertTrue(can_engage(self.ws.world, self.team_a[0], self.team_b[0]))
        self.assertTrue(can_engage(self.ws.world, self.team_b[0], self.team_a[0]))
        self.assertFalse(can_engage(self.ws.world, self.team_a[0], self.team_a[1]))
        self.assertFalse(can_engage(self.ws.world, self.team_b[0], self.team_b[1]))

    def test_par_arena_vs_jogadores_default_cai_em_neutro_nao_amigavel(self):
        """Documenta o comportamento real (não um bug): "arena_time_a" x
        "jogadores" não está em RELATIONSHIP, cai no DEFAULT_RELATIONSHIP
        "neutro" — e can_engage já libera "neutro" (só "amigavel" bloqueia;
        a diferença hostil/neutro é só quem INICIA o combate, ver
        engine/faction_system.py::can_engage). Isso nunca vira um cenário
        real porque um bystander no mundo aberto NUNCA compartilha AOI com
        quem está dentro da instância da arena — a proteção de verdade é
        o isolamento de instância, não a tabela de facção."""
        bystander = spawn_player(self.ws, "bystander", 130, 374)
        from content.faction_data import get_relationship
        self.assertEqual(get_relationship("arena_time_a", "jogadores"), "neutro")
        self.assertTrue(can_engage(self.ws.world, self.team_a[0], bystander))

    def test_golpe_letal_nao_mata_de_verdade_marca_eliminado(self):
        target = self.team_b[0]
        cs = self.ws.world.get_component(target, CombatStats)
        outcome = apply_damage_core(self.ws.world, target, 999999, killer_eid=self.team_a[0])
        self.assertEqual(outcome, "applied")
        self.assertEqual(cs.current_hp, 1)
        self.assertIn(target, self.ws._active_matches[self.match_id]["eliminated"])
        cst = self.ws.world.get_component(target, CombatState)
        self.assertTrue(cst.is_immune)
        # partida NÃO acabou — só 1 dos 2 do time B foi eliminado
        self.assertIn(self.match_id, self.ws._active_matches)

    def test_eliminar_time_inteiro_termina_partida_e_restaura_tudo(self):
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        self.assertNotIn(self.match_id, self.ws._active_matches)
        self.assertEqual(self.ws._player_match_id, {})
        for eid in self.team_a + self.team_b:
            self.assertIsNone(self.ws.world.get_component(eid, Faction))
            self.assertEqual(self.ws.get_entity_map(eid), "maps/map_1.csv")
            tm = self.ws.world.get_component(eid, TileMovement)
            self.assertEqual((tm.current_tile_x, tm.current_tile_y), (130, 374))
        events = self.ws.consume_arena_match_end_events()
        won = {e["eid"]: e["won"] for e in events}
        for eid in self.team_a:
            self.assertTrue(won[eid])
        for eid in self.team_b:
            self.assertFalse(won[eid])

    def test_desconexao_em_partida_conta_como_eliminacao(self):
        self.ws.end_matches_of(self.team_b[0])
        self.ws.end_matches_of(self.team_b[1])
        self.assertNotIn(self.match_id, self.ws._active_matches)


class TestArenaInstanceIsolation(unittest.TestCase):
    """2 partidas simultâneas da MESMA arena não vazam tile/pathfinding
    entre si — prova que a chave sintética (instance_key) isola
    corretamente mesmo reusando o mesmo arquivo de mapa."""

    def setUp(self):
        self.ws = make_world_server()
        self.team_a1 = _make_duo(self.ws, "m1a")
        self.team_b1 = _make_duo(self.ws, "m1b")
        self.match_1 = _queue_and_pair(self.ws, self.team_a1, self.team_b1)

        self.team_a2 = _make_duo(self.ws, "m2a")
        self.team_b2 = _make_duo(self.ws, "m2b")
        self.match_2 = _queue_and_pair(self.ws, self.team_a2, self.team_b2)

    def test_instance_keys_diferentes_para_o_mesmo_template(self):
        ik1 = self.ws._active_matches[self.match_1]["instance_key"]
        ik2 = self.ws._active_matches[self.match_2]["instance_key"]
        self.assertNotEqual(ik1, ik2)
        self.assertTrue(ik1.startswith("maps/arena_2v2.csv::"))
        self.assertTrue(ik2.startswith("maps/arena_2v2.csv::"))
        self.assertIn(ik1, self.ws._map_bundles)
        self.assertIn(ik2, self.ws._map_bundles)

    def test_template_file_of_traduz_pro_arquivo_real(self):
        ik1 = self.ws._active_matches[self.match_1]["instance_key"]
        self.assertEqual(self.ws._template_file_of(ik1), "maps/arena_2v2.csv")

    def test_can_engage_nao_vaza_entre_partidas(self):
        """Time A da partida 1 não deveria conseguir engajar ninguém da
        partida 2 (facções diferentes, "arena_time_a" é amigável consigo
        mesma independente de instância — mas nunca compartilham AOI de
        verdade; aqui testamos só o gate de facção em si)."""
        self.assertFalse(can_engage(self.ws.world, self.team_a1[0], self.team_a2[0]))

    def test_pathfinding_resolve_bundle_da_instancia_correta(self):
        """register_map_services_for(eid) + is_tile_walkable resolvem o
        bundle da PRÓPRIA instância do player, não o último carregado."""
        from engine.world_systems import is_tile_walkable
        self.ws.register_map_services_for(self.team_a1[0])
        # (0,0) é canto de parede no arena_2v2.csv (borda inteira é "#")
        self.assertFalse(is_tile_walkable(self.team_a1[0], 0, 0))
        # (5,5) é chão aberto dentro do template 20x20
        self.assertTrue(is_tile_walkable(self.team_a1[0], 5, 5))

        self.ws.register_map_services_for(self.team_a2[0])
        self.assertFalse(is_tile_walkable(self.team_a2[0], 0, 0))
        self.assertTrue(is_tile_walkable(self.team_a2[0], 5, 5))

    def test_terminar_uma_partida_nao_afeta_a_outra(self):
        for eid in self.team_b1:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a1[0])
        self.assertNotIn(self.match_1, self.ws._active_matches)
        self.assertIn(self.match_2, self.ws._active_matches)
        ik2 = self.ws._active_matches[self.match_2]["instance_key"]
        self.assertIn(ik2, self.ws._map_bundles)
        for eid in self.team_a2 + self.team_b2:
            self.assertIn(eid, self.ws._player_match_id)


class TestArenaRegressionOpenWorld(unittest.TestCase):
    """O mundo aberto/duelo continuam funcionando normalmente depois de
    Fase G (nenhum reuso de global de interceptor/resolver colide)."""

    def test_duelo_continua_funcionando_com_matchprocessor_registrado(self):
        ws = make_world_server()
        a = spawn_player(ws, "d1", 130, 374)
        b = spawn_player(ws, "d2", 131, 374)
        self.assertIsNone(ws.request_duel(a, b))
        ws.respond_duel_invite(b, accept=True)
        self.assertTrue(can_engage(ws.world, a, b))
        # golpe letal em duelo (fora de qualquer arena) continua intercedido
        cs_b = ws.world.get_component(b, CombatStats)
        outcome = apply_damage_core(ws.world, b, 999999, killer_eid=a)
        self.assertEqual(outcome, "applied")
        self.assertEqual(cs_b.current_hp, 1)
        # duelo terminou (não é mais hostil um ao outro fora de duelo ativo)
        self.assertFalse(can_engage(ws.world, a, b))

    def test_mundo_aberto_sem_faccao_de_arena_continua_amigavel(self):
        ws = make_world_server()
        a = spawn_player(ws, "w1", 130, 374)
        b = spawn_player(ws, "w2", 131, 374)
        self.assertFalse(can_engage(ws.world, a, b))


if __name__ == "__main__":
    unittest.main()
