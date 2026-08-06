"""tests/test_debug_battleground_match.py — server/debug_battleground.py::
notify_nexus_destroyed / _tick_bg_results_timeout / _tick_kda_hud
(02/08/2026, pedido do usuário: Nexus termina a partida, placar final dos
dois times, timeout automático de 15s de saída, HUD ao vivo de
Kills/Deaths/Farm/Gold). Mesmo padrão leve de manipular bg._state
diretamente já usado em test_debug_battleground_respawn.py.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.components import Faction, CharStatsTracker, Wallet
from server import debug_battleground as bg


class TestNotifyNexusDestroyed(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.p_a = spawn_player(self.ws, "m_a", 10, 10, class_id="guerreiro")
        self.p_b = spawn_player(self.ws, "m_b", 12, 10, class_id="guerreiro")
        self.ws.world.add_component(self.p_a, Faction("arena_time_a"))
        self.ws.world.add_component(self.p_b, Faction("arena_time_b"))
        bg._state["members"] = {self.p_a, self.p_b}
        bg._state["stat_snapshots"] = {
            self.p_a: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0},
            self.p_b: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0},
        }
        # gold_earned/last_wallet_gold — mesma baseline que _enter() monta
        # de verdade (ver docstring de _sample_gold_earned): sem isso,
        # _sample_gold_earned trata o eid como "nunca entrou" e sempre
        # retorna 0. Baseline = wallet.gold ATUAL (spawn_player não passa
        # por enter_normalized_progression aqui, então não é
        # necessariamente INSTANCE_STARTING_GOLD).
        wallet_a0 = self.ws.world.get_component(self.p_a, Wallet)
        wallet_b0 = self.ws.world.get_component(self.p_b, Wallet)
        bg._state["gold_earned"] = {self.p_a: 0, self.p_b: 0}
        bg._state["last_wallet_gold"] = {
            self.p_a: wallet_a0.gold, self.p_b: wallet_b0.gold,
        }
        bg._state["match_decided"]        = False
        bg._state["result_deadline"]      = None
        bg._state["pending_match_result"] = []
        bg._state["pending_forced_leave_notify"] = []

    def tearDown(self):
        bg._state["members"]            = set()
        bg._state["stat_snapshots"]     = {}
        bg._state["gold_earned"]        = {}
        bg._state["last_wallet_gold"]   = {}
        bg._state["match_decided"]      = False
        bg._state["result_deadline"]    = None
        bg._state["pending_match_result"] = []
        bg._state["pending_forced_leave_notify"] = []
        bg._state["last_kda_sent"] = {}

    def test_placar_reflete_delta_de_kda_e_ouro_desde_o_snapshot(self):
        cst_a = self.ws.world.get_component(self.p_a, CharStatsTracker)
        cst_a.players_killed  = 3
        cst_a.deaths          = 1
        cst_a.minions_killed  = 20
        cst_a.pve_damage      = 500
        wallet_a = self.ws.world.get_component(self.p_a, Wallet)
        wallet_a.gold += 250   # ganhou 250 desde a entrada (baseline do setUp)

        bg.notify_nexus_destroyed(self.ws, bg.DEBUG_BG_INSTANCE_KEY, "arena_time_b", self.p_a)

        self.assertTrue(bg._state["match_decided"])
        _, payload = bg._state["pending_match_result"][0]
        row_a = next(p for p in payload["players"] if p["eid"] == self.p_a)
        self.assertEqual(row_a["kills"], 3)
        self.assertEqual(row_a["deaths"], 1)
        self.assertEqual(row_a["farm"], 20)
        self.assertEqual(row_a["damage"], 500)
        self.assertEqual(row_a["gold"], 250)
        self.assertTrue(row_a["won"])

    def test_gold_ganho_nao_diminui_quando_player_gasta_na_loja(self):
        """Bug real relatado pelo usuário (03/08/2026): "compra de itens faz
        o contador de gold da HUD ficar negativo, não é necessário
        descontar o gold usado". Cálculo antigo era NET (`wallet.gold -
        INSTANCE_STARTING_GOLD`) — caía abaixo de 0 assim que o player
        gastasse mais do que tinha ganho. Fix: `_sample_gold_earned`
        acumula só deltas POSITIVOS; uma compra (delta negativo) só
        atualiza o baseline, nunca subtrai do total mostrado."""
        wallet_a = self.ws.world.get_component(self.p_a, Wallet)

        wallet_a.gold += 100   # ganhou 100 (kill/loot)
        bg._tick_kda_hud(self.ws)
        self.assertEqual(bg._state["gold_earned"][self.p_a], 100)

        wallet_a.gold -= 80    # gastou 80 numa compra na loja de instância
        bg._tick_kda_hud(self.ws)
        self.assertEqual(bg._state["gold_earned"][self.p_a], 100,
                         "gasto não deveria diminuir o gold GANHO mostrado na HUD")
        self.assertGreaterEqual(bg._state["gold_earned"][self.p_a], 0)

        wallet_a.gold += 30    # ganhou mais 30 depois da compra
        bg._tick_kda_hud(self.ws)
        self.assertEqual(bg._state["gold_earned"][self.p_a], 130)

    def test_idempotente_segunda_chamada_nao_sobrescreve(self):
        bg.notify_nexus_destroyed(self.ws, bg.DEBUG_BG_INSTANCE_KEY, "arena_time_b", self.p_a)
        first_deadline = bg._state["result_deadline"]
        bg.notify_nexus_destroyed(self.ws, bg.DEBUG_BG_INSTANCE_KEY, "arena_time_a", self.p_b)
        self.assertEqual(bg._state["result_deadline"], first_deadline,
                         "segunda notificação não deveria reabrir/sobrescrever a partida já decidida")

    def test_sem_partida_de_teste_ativa_e_no_op(self):
        bg._state["members"] = set()
        bg.notify_nexus_destroyed(self.ws, bg.DEBUG_BG_INSTANCE_KEY, "arena_time_b", self.p_a)
        self.assertFalse(bg._state["match_decided"])
        self.assertEqual(bg._state["pending_match_result"], [])


class TestResultsTimeout(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.p_a = spawn_player(self.ws, "t_a", 10, 10, class_id="guerreiro")
        self.ws.world.add_component(self.p_a, Faction("arena_time_a"))
        bg._state["members"] = {self.p_a}
        bg._state["return_pos"] = {self.p_a: (self.ws.MAP_FILE, 50, 50)}
        bg._state["stat_snapshots"] = {
            self.p_a: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0}}
        bg._state["match_decided"]        = False
        bg._state["result_deadline"]      = None
        bg._state["pending_forced_leave_notify"] = []

    def tearDown(self):
        bg._state["members"]  = set()
        bg._state["return_pos"] = {}
        bg._state["stat_snapshots"] = {}
        bg._state["match_decided"]   = False
        bg._state["result_deadline"] = None
        bg._state["pending_forced_leave_notify"] = []

    def test_forca_saida_apos_deadline_vencido(self):
        import time
        bg._state["match_decided"]   = True
        bg._state["result_deadline"] = time.time() - 1  # já venceu
        bg._tick_bg_results_timeout(self.ws)
        self.assertNotIn(self.p_a, bg._state["members"], "deveria ter forçado _leave")
        self.assertEqual(len(bg._state["pending_forced_leave_notify"]), 1)

    def test_nao_forca_saida_antes_do_deadline(self):
        import time
        bg._state["match_decided"]   = True
        bg._state["result_deadline"] = time.time() + 30
        bg._tick_bg_results_timeout(self.ws)
        self.assertIn(self.p_a, bg._state["members"])
        self.assertEqual(bg._state["pending_forced_leave_notify"], [])


class TestKdaHudDirtyCheck(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.p_a = spawn_player(self.ws, "hud_a", 10, 10, class_id="guerreiro")
        bg._state["members"] = {self.p_a}
        bg._state["stat_snapshots"] = {
            self.p_a: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0}}
        bg._state["last_kda_sent"] = {}

    def tearDown(self):
        bg._state["members"]        = set()
        bg._state["stat_snapshots"] = {}
        bg._state["last_kda_sent"]  = {}

    def test_emite_stats_update_quando_kills_muda(self):
        sent = []
        self.ws.queue_stats_update = lambda payload: sent.append(payload)
        bg._tick_kda_hud(self.ws)
        self.assertEqual(len(sent), 1, "primeira chamada sempre emite (baseline 0,0,0,0)")

        # Nada mudou — não deveria emitir de novo (prova do dirty-check).
        bg._tick_kda_hud(self.ws)
        self.assertEqual(len(sent), 1, "sem mudança nenhuma, não deveria reemitir")

        cst = self.ws.world.get_component(self.p_a, CharStatsTracker)
        cst.players_killed = 1
        bg._tick_kda_hud(self.ws)
        self.assertEqual(len(sent), 2, "kills mudou — deveria emitir de novo")
        self.assertEqual(sent[-1]["match_kills"], 1)


if __name__ == "__main__":
    unittest.main()
