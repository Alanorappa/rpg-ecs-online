"""
tests/test_duel.py — Sistema de duelo (contexto PvP por convite, estilo
WoW — ver server/duel_processor.py e ARQUITETURA_ONLINE.md).

Cobre: lifecycle (request→invite→accept→par hostil), golpe letal deixa o
perdedor com 1 HP e encerra (ninguém morre), recusa, encerramento por
distância e por logout, e regressões (terceiro player continua amigável;
NPC amigável inatacável mesmo durante duelo).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from engine.faction_system import can_engage
from engine.world_systems import deal_damage
from engine.components import CombatStats


class TestDuelLifecycle(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)

    def _start_duel(self):
        self.assertIsNone(self.ws.request_duel(self.a, self.b))
        requester, started = self.ws.respond_duel_invite(self.b, accept=True)
        self.assertEqual(requester, self.a)
        self.assertTrue(started)

    def test_request_registra_convite_e_accept_cria_par(self):
        self.assertIsNone(self.ws.request_duel(self.a, self.b))
        self.assertEqual(self.ws._pending_duel_invites.get(self.b), self.a)

        requester, started = self.ws.respond_duel_invite(self.b, accept=True)
        self.assertEqual(requester, self.a)
        self.assertTrue(started)
        self.assertIn(frozenset((self.a, self.b)), self.ws._duel_pairs)
        self.assertTrue(can_engage(self.ws.world, self.a, self.b))
        self.assertTrue(can_engage(self.ws.world, self.b, self.a))

    def test_decline_nao_cria_par(self):
        self.assertIsNone(self.ws.request_duel(self.a, self.b))
        requester, started = self.ws.respond_duel_invite(self.b, accept=False)
        self.assertEqual(requester, self.a)
        self.assertFalse(started)
        self.assertEqual(len(self.ws._duel_pairs), 0)
        self.assertFalse(can_engage(self.ws.world, self.a, self.b))

    def test_request_invalido(self):
        # consigo mesmo
        self.assertEqual(self.ws.request_duel(self.a, self.a), "invalid")
        # fora do alcance do convite (raio do trade = 5 tiles)
        set_entity_tile(self.ws, self.b, 140, 374)
        self.assertEqual(self.ws.request_duel(self.a, self.b), "invalid")
        set_entity_tile(self.ws, self.b, 131, 374)
        # já em duelo
        self._start_duel()
        c = spawn_player(self.ws, "s3", 132, 374)
        self.assertEqual(self.ws.request_duel(self.a, c), "invalid")
        self.assertEqual(self.ws.request_duel(c, self.b), "invalid")

    def test_terceiro_player_continua_amigavel_durante_duelo(self):
        self._start_duel()
        c = spawn_player(self.ws, "s3", 132, 374)
        self.assertFalse(can_engage(self.ws.world, self.a, c))
        self.assertFalse(can_engage(self.ws.world, c, self.b))
        c_cs = self.ws.world.get_component(c, CombatStats)
        hp_before = c_cs.current_hp
        deal_damage(self.a, c, "physical", pre_outcome="hit")
        self.assertEqual(c_cs.current_hp, hp_before,
                         "duelo libera SÓ o par — terceiro player segue protegido")

    def test_golpe_letal_deixa_perdedor_com_1hp_e_encerra(self):
        """Estilo WoW: o golpe que mataria encerra o duelo — perdedor fica
        com 1 HP, ninguém morre, hostilidade acaba na hora."""
        self._start_duel()
        b_cs = self.ws.world.get_component(self.b, CombatStats)
        b_cs.current_hp = 1   # qualquer hit seria letal

        dead, _ = deal_damage(self.a, self.b, "physical", pre_outcome="hit")

        self.assertFalse(dead, "duelo intercepta a morte — ninguém morre")
        self.assertEqual(b_cs.current_hp, 1, "perdedor fica com exatamente 1 HP")
        self.assertEqual(len(self.ws._duel_pairs), 0, "duelo encerra no golpe letal")
        events = self.ws.consume_duel_end_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["winner_eid"], self.a)
        self.assertEqual(events[0]["loser_eid"], self.b)
        self.assertEqual(events[0]["reason"], "win")
        # tx/ty/map (posição do vencedor) — usados pelo broadcast AOI do
        # anúncio na aba Local (server/session.py), não só DM aos duelistas.
        self.assertEqual(events[0]["tx"], 130)
        self.assertEqual(events[0]["ty"], 374)
        self.assertIsNotNone(events[0]["map"])
        # Hostilidade acabou: novo hit é bloqueado
        hp_before = b_cs.current_hp
        deal_damage(self.a, self.b, "physical", pre_outcome="hit")
        self.assertEqual(b_cs.current_hp, hp_before,
                         "após o fim do duelo, o par volta a ser amigável")

    def test_distancia_encerra_o_duelo(self):
        self._start_duel()
        set_entity_tile(self.ws, self.b, 131 + 25, 374)   # > DUEL_MAX_DIST_TILES (20)
        self.ws._tick_duel_distance_check()
        self.assertEqual(len(self.ws._duel_pairs), 0)
        events = self.ws.consume_duel_end_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "distance")
        self.assertEqual(events[0]["winner_eid"], -1)

    def test_logout_encerra_duelo_e_convites(self):
        self._start_duel()
        self.ws.end_duels_of(self.a, "disconnect")
        self.assertEqual(len(self.ws._duel_pairs), 0)
        events = self.ws.consume_duel_end_events()
        self.assertEqual(events[0]["reason"], "disconnect")

        # Convites pendentes envolvendo o eid também morrem
        c = spawn_player(self.ws, "s3", 132, 374)
        self.assertIsNone(self.ws.request_duel(self.a, c))
        self.ws.end_duels_of(self.a, "disconnect")
        self.assertEqual(len(self.ws._pending_duel_invites), 0)

    def test_npc_amigavel_continua_inatacavel_durante_duelo(self):
        """O contexto de duelo nunca vaza pra NPCs — o guarda continua
        protegido mesmo com um duelo ativo do atacante."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState
        self._start_duel()
        guard = create_combat_npc(self.ws.world, 132, 374, faction="guardas_vila")
        self.ws.world.add_component(guard, CombatState())
        g_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = g_cs.current_hp
        deal_damage(self.a, guard, "physical", pre_outcome="hit")
        self.assertEqual(g_cs.current_hp, hp_before)

    def test_kill_switch_bloqueia_novos_duelos(self):
        self.ws.pvp_enabled = False
        try:
            self.assertEqual(self.ws.request_duel(self.a, self.b), "invalid")
        finally:
            self.ws.pvp_enabled = True


class TestDuelClientSide(unittest.TestCase):
    """Lado CLIENTE do contexto de duelo — bug real relatado pelo usuário
    16/07/2026: durante o duelo, clique direito no oponente ainda abria o
    modal de interação em vez de atacar. Causa raiz: o gate do contexto
    PvP em can_engage exigia PlayerControlled dos DOIS lados, mas no
    cliente o oponente é um proxy RemoteControlled — o contexto registrado
    pelo DUEL_START nunca era consultado (can_engage False → ramo do
    modal). Fix: _is_player_entity aceita RemoteControlled também."""

    def setUp(self):
        from engine.world import World
        from engine.components import PlayerControlled, RemoteControlled
        from engine.faction_system import register_pvp_context
        self.w = World()
        self.me     = self.w.create_entity()
        self.remote = self.w.create_entity()
        self.w.add_component(self.me, PlayerControlled())
        self.w.add_component(self.remote, RemoteControlled())
        # Espelho exato do _duel_ctx de client/duel_handlers.py
        pair = {self.me, self.remote}
        register_pvp_context(lambda _w, a, b: {a, b} == pair)

    def tearDown(self):
        from engine.faction_system import register_pvp_context
        register_pvp_context(None)

    def test_oponente_de_duelo_vira_atacavel_no_cliente(self):
        """can_engage(local, proxy remoto) com contexto de duelo ativo →
        True — o clique direito cai no ramo de ATAQUE, não no modal."""
        self.assertTrue(can_engage(self.w, self.me, self.remote),
                        "com duelo ativo, o oponente remoto deve ser engajável no cliente")

    def test_terceiro_proxy_remoto_continua_amigavel(self):
        from engine.components import RemoteControlled
        other = self.w.create_entity()
        self.w.add_component(other, RemoteControlled())
        self.assertFalse(can_engage(self.w, self.me, other),
                         "contexto de duelo libera SÓ o par — outro player remoto segue amigável (modal)")

    def test_sem_contexto_volta_ao_modal(self):
        from engine.faction_system import register_pvp_context
        register_pvp_context(None)   # DUEL_END desregistra
        self.assertFalse(can_engage(self.w, self.me, self.remote),
                         "sem duelo ativo, oponente volta a ser amigável (clique direito → modal)")


if __name__ == "__main__":
    unittest.main()
