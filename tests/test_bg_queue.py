"""
tests/test_bg_queue.py — Fila REAL de matchmaking da BG estilo MOBA
(04/08/2026, pedido do usuário: "vamos criar o sistema de fila,
equivalente ao que já existe na arena" — server/bg_queue_processor.py).

Revisado 10/08/2026 (achado real do usuário): fila ÚNICA sem modo
escolhido virou 3 filas de TAMANHO FIXO (BG_MODES: 2v2/3v3/5v5, mesmo
padrão de Arena) — a versão antiga formava sempre a MAIOR partida
simétrica possível a cada tick numa fila só, sem nenhuma janela de
espera (2 solos já fechavam 1v1 no tick seguinte, nunca dando chance de
uma partida maior se formar). Resto do ciclo de vida (propõe→aceita→
countdown→portão→Nexus derrubado→sai) espelha a Arena 1 pra 1, agora
inclusive na escolha explícita de modo.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.components import Faction, CombatState, TileMovement, CharStatsTracker, Wallet
from server.bg_queue_processor import BG_QUEUE_TEMPLATE, BG_MODES


def _make_group(ws, prefix: str, size: int, tile=(130, 374)):
    """Cria `size` players e agrupa todos sob o primeiro (líder) —
    devolve a lista de eids na ordem de criação. size=1 devolve um
    player sozinho, SEM grupo (mesma distinção que a fila usa pra
    decidir token "solo" vs "party")."""
    eids = [spawn_player(ws, f"{prefix}_{i}", *tile) for i in range(size)]
    for m in eids[1:]:
        ws.request_party_invite(eids[0], m)
        ws.respond_party_invite(m, accept=True)
    return eids


class TestBgQueueJoinLeave(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_solo_entra_livre_sem_grupo(self):
        solo = spawn_player(self.ws, "j_solo", 130, 374)
        self.assertIsNone(self.ws.request_bg_queue_join(solo, "2v2"))
        self.assertIn(("solo", solo), self.ws._bg_queues["2v2"])

    def test_modo_invalido_e_recusado(self):
        solo = spawn_player(self.ws, "j_badmode", 130, 374)
        self.assertEqual(self.ws.request_bg_queue_join(solo, "9v9"), "invalid_mode")

    def test_modo_default_e_5v5(self):
        solo = spawn_player(self.ws, "j_default", 130, 374)
        self.assertIsNone(self.ws.request_bg_queue_join(solo))
        self.assertIn(("solo", solo), self.ws._bg_queues["5v5"])

    def test_so_lider_do_grupo_pode_enfileirar(self):
        a, b = _make_group(self.ws, "j_g2", 2)
        self.assertEqual(self.ws.request_bg_queue_join(b, "2v2"), "not_leader")
        self.assertIsNone(self.ws.request_bg_queue_join(a, "2v2"))
        self.assertIn(("party", self.ws.get_party_id_of(a)), self.ws._bg_queues["2v2"])

    def test_grupo_maior_que_o_time_do_modo_e_recusado(self):
        trio = _make_group(self.ws, "j_g3big", 3)
        self.assertEqual(self.ws.request_bg_queue_join(trio[0], "2v2"), "wrong_size")

    def test_grupo_menor_que_o_time_do_modo_e_aceito(self):
        """Grupo menor que o time é aceito — preenchido por outros tokens
        da MESMA fila (mesma flexibilidade que a fila sempre teve, só
        que agora escopada por tamanho escolhido)."""
        duo = _make_group(self.ws, "j_g2small", 2)
        self.assertIsNone(self.ws.request_bg_queue_join(duo[0], "3v3"))
        self.assertIn(("party", self.ws.get_party_id_of(duo[0])), self.ws._bg_queues["3v3"])

    def test_entrar_2x_e_recusado(self):
        solo = spawn_player(self.ws, "j_dup", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        self.assertEqual(self.ws.request_bg_queue_join(solo, "2v2"), "already_queued")

    def test_nao_pode_estar_em_2_filas_de_modo_diferente_ao_mesmo_tempo(self):
        solo = spawn_player(self.ws, "j_2modes", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        self.assertEqual(self.ws.request_bg_queue_join(solo, "3v3"), "already_queued")

    def test_ja_em_partida_e_recusado(self):
        solo = spawn_player(self.ws, "j_inm", 130, 374)
        self.ws._player_bg_match_id[solo] = "bg_fake"
        self.assertEqual(self.ws.request_bg_queue_join(solo, "2v2"), "in_match")

    def test_sair_da_fila_remove_o_token(self):
        solo = spawn_player(self.ws, "j_leave", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        self.assertTrue(self.ws.request_bg_queue_leave(solo))
        self.assertEqual(self.ws._bg_queues["2v2"], [])

    def test_sair_da_fila_sem_estar_nela_retorna_false(self):
        solo = spawn_player(self.ws, "j_noop", 130, 374)
        self.assertFalse(self.ws.request_bg_queue_leave(solo))

    def test_membro_do_grupo_sai_pelo_token_do_lider(self):
        a, b = _make_group(self.ws, "j_g2s", 2)
        self.ws.request_bg_queue_join(a, "2v2")
        self.assertTrue(self.ws.request_bg_queue_leave(b))
        self.assertEqual(self.ws._bg_queues["2v2"], [])


class TestBgQueueMatchmakingAlgorithm(unittest.TestCase):
    """Núcleo do redesenho (10/08/2026): fila de TAMANHO FIXO por modo —
    "não pode ser assimétrico", empacotamento guloso dentro da fila do
    modo escolhido, grupo nunca dividido entre os 2 lados."""

    def setUp(self):
        self.ws = make_world_server()

    def test_4_solos_no_modo_2v2_formam_1_partida(self):
        eids = [spawn_player(self.ws, f"mm_{i}", 130, 374) for i in range(4)]
        for eid in eids:
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 1)
        match = next(iter(self.ws._bg_active_matches.values()))
        self.assertEqual(match["team_size"], 2)
        self.assertEqual(match["mode_id"], "2v2")
        self.assertEqual(set(match["invited_a"] + match["invited_b"]), set(eids))
        self.assertEqual(self.ws._bg_queues["2v2"], [])

    def test_10_solos_no_modo_5v5_formam_1_partida_5x5(self):
        solos = [spawn_player(self.ws, f"mm_big_{i}", 130, 374) for i in range(10)]
        for eid in solos:
            self.ws.request_bg_queue_join(eid, "5v5")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 1)
        match = next(iter(self.ws._bg_active_matches.values()))
        self.assertEqual(match["team_size"], 5)
        self.assertEqual(len(match["invited_a"]), 5)
        self.assertEqual(len(match["invited_b"]), 5)
        self.assertEqual(self.ws._bg_queues["5v5"], [])

    def test_filas_de_modos_diferentes_nao_se_misturam(self):
        """4 solos no 2v2 e 3 no 3v3 ao mesmo tempo — cada fila forma sua
        própria partida, sem vazar token de uma fila pra outra."""
        eids_2v2 = [spawn_player(self.ws, f"mm_2v2_{i}", 130, 374) for i in range(4)]
        eids_3v3 = [spawn_player(self.ws, f"mm_3v3_{i}", 130, 374) for i in range(6)]
        for eid in eids_2v2:
            self.ws.request_bg_queue_join(eid, "2v2")
        for eid in eids_3v3:
            self.ws.request_bg_queue_join(eid, "3v3")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 2)
        sizes = sorted(m["team_size"] for m in self.ws._bg_active_matches.values())
        self.assertEqual(sizes, [2, 3])
        self.assertEqual(self.ws._bg_queues["2v2"], [])
        self.assertEqual(self.ws._bg_queues["3v3"], [])

    def test_grupo_de_3_nunca_e_dividido_entre_os_2_lados(self):
        trio = _make_group(self.ws, "mm_trio", 3)
        solos = [spawn_player(self.ws, f"mm_fill_{i}", 130, 374) for i in range(3)]
        self.ws.request_bg_queue_join(trio[0], "3v3")
        for eid in solos:
            self.ws.request_bg_queue_join(eid, "3v3")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 1)
        match = next(iter(self.ws._bg_active_matches.values()))
        self.assertEqual(match["team_size"], 3)
        # o trio inteiro tem que estar no MESMO lado
        trio_set = set(trio)
        self.assertTrue(trio_set.issubset(set(match["invited_a"]))
                        or trio_set.issubset(set(match["invited_b"])))

    def test_grupo_preenchido_por_solos_do_outro_lado(self):
        """Grupo de 2 vs 2 solos somados — a fila do modo mistura tokens
        diferentes pra fechar o mesmo tamanho, contanto que nunca separe
        um grupo."""
        duo = _make_group(self.ws, "mm_mix_duo", 2)
        solos = [spawn_player(self.ws, f"mm_mix_s_{i}", 130, 374) for i in range(2)]
        self.ws.request_bg_queue_join(duo[0], "2v2")
        for eid in solos:
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        match = next(iter(self.ws._bg_active_matches.values()))
        all_invited = set(match["invited_a"] + match["invited_b"])
        self.assertEqual(all_invited, set(duo) | set(solos))

    def test_time_pequeno_demais_pro_time_size_e_ignorado_nessa_tentativa(self):
        """Um grupo de 4 esperando sozinho no modo 5v5 (sem mais ninguém
        pra fechar o 5º lugar) NUNCA deveria virar uma partida
        assimétrica — fica na fila até alguém mais aparecer."""
        quarteto = _make_group(self.ws, "mm_solo4", 4)
        self.ws.request_bg_queue_join(quarteto[0], "5v5")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 0)
        self.assertEqual(len(self.ws._bg_queues["5v5"]), 1)

    def test_nunca_produz_partida_assimetrica_dentro_do_mesmo_modo(self):
        """Mistura deliberadamente difícil de empacotar no modo 3v3 (duo
        de 2 + 1 solo = 3 só de um lado, sem mais ninguém pro outro) —
        nenhuma partida deveria se formar até sobrar uma combinação exata."""
        duo = _make_group(self.ws, "mm_asym_duo", 2)
        solo = spawn_player(self.ws, "mm_asym_solo", 130, 374)
        self.ws.request_bg_queue_join(duo[0], "3v3")
        self.ws.request_bg_queue_join(solo, "3v3")
        self.ws._tick_bg_queue()
        self.assertEqual(len(self.ws._bg_active_matches), 0)
        for match in self.ws._bg_active_matches.values():
            self.assertEqual(len(match["invited_a"]), len(match["invited_b"]))

    def test_desfazer_grupo_enquanto_espera_descarta_o_token(self):
        a, b = _make_group(self.ws, "mm_dissolve", 2)
        self.ws.request_bg_queue_join(a, "2v2")
        self.ws.leave_party(b)   # grupo de 2 vira "sem grupo" pros 2 (leave_party desfaz)
        self.ws._tick_bg_queue()
        self.assertEqual(self.ws._bg_queues["2v2"], [])
        self.assertEqual(len(self.ws._bg_active_matches), 0)


class TestBgQueueMatchLifecycle(unittest.TestCase):
    """Modo 2v2 (o menor disponível) — 4 solos, entrando na ordem a, c,
    b, d pra fechar team_a=[a,c]/team_b=[b,d] (bin-packing FIFO
    preenche o lado A até a capacidade antes de começar o B) — mantém
    `self.a`/`self.b` como OPONENTES, mesma relação que os testes
    tinham no 1v1 antigo."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "lc_a", 130, 374)
        self.c = spawn_player(self.ws, "lc_c", 131, 374)
        self.b = spawn_player(self.ws, "lc_b", 132, 374)
        self.d = spawn_player(self.ws, "lc_d", 133, 374)
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        self.match_id = self.ws._pending_bg_invite[self.a]

    def test_pareamento_forma_times_2v2_sem_dividir_lado(self):
        match = self.ws._bg_active_matches[self.match_id]
        self.assertEqual(set(match["invited_a"]), {self.a, self.c})
        self.assertEqual(set(match["invited_b"]), {self.b, self.d})

    def test_pareamento_sozinho_nao_teleporta_nem_atribui_faccao(self):
        match = self.ws._bg_active_matches[self.match_id]
        self.assertEqual(match["team_a"], [])
        self.assertEqual(match["team_b"], [])
        self.assertIsNone(match["instance_key"])
        for eid in (self.a, self.b, self.c, self.d):
            self.assertIsNone(self.ws.world.get_component(eid, Faction))

    def test_pareamento_gera_4_eventos_bg_match_found(self):
        events = self.ws.consume_bg_match_found_events()
        self.assertEqual(len(events), 4)
        eids = {e["eid"] for e in events}
        self.assertEqual(eids, {self.a, self.b, self.c, self.d})
        ev_a = next(e for e in events if e["eid"] == self.a)
        self.assertEqual(ev_a["team_size"], 2)
        self.assertEqual(ev_a["mode"], "2v2")
        self.assertEqual(set(ev_a["opponents"]), {self.b, self.d})

    def test_aceite_carrega_instancia_teleporta_e_atribui_faccao(self):
        reason = self.ws.request_bg_accept(self.a)
        self.assertIsNone(reason)
        match = self.ws._bg_active_matches[self.match_id]
        self.assertEqual(match["team_a"], [self.a])
        self.assertTrue(match["instance_key"].startswith(f"{BG_QUEUE_TEMPLATE}::"))
        self.assertIn(match["instance_key"], self.ws._map_bundles)
        faction = self.ws.world.get_component(self.a, Faction)
        self.assertEqual(faction.faction_id, "arena_time_a")
        self.assertEqual(self.ws._player_bg_match_id[self.a], self.match_id)

    def test_aceite_ativa_progressao_normalizada(self):
        from server.instance_progression import is_in_normalized_progression
        self.ws.request_bg_accept(self.a)
        self.assertTrue(is_in_normalized_progression(self.ws, self.a))

    def test_2_partidas_simultaneas_usam_instance_keys_diferentes(self):
        e = spawn_player(self.ws, "lc_e", 130, 374)
        f = spawn_player(self.ws, "lc_f", 131, 374)
        g = spawn_player(self.ws, "lc_g", 132, 374)
        h = spawn_player(self.ws, "lc_h", 133, 374)
        for eid in (e, f, g, h):
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        match_id_2 = self.ws._pending_bg_invite[e]
        self.assertNotEqual(match_id_2, self.match_id)

        self.ws.request_bg_accept(self.a)
        self.ws.request_bg_accept(e)
        ik1 = self.ws._bg_active_matches[self.match_id]["instance_key"]
        ik2 = self.ws._bg_active_matches[match_id_2]["instance_key"]
        self.assertNotEqual(ik1, ik2)

    def test_countdown_remaining_soma_accept_window_e_countdown(self):
        self.ws.request_bg_accept(self.a)
        events = self.ws.consume_bg_match_start_events()
        self.assertEqual(len(events), 1)
        from shared.constants import ARENA_ACCEPT_WINDOW_S, ARENA_COUNTDOWN_S
        self.assertAlmostEqual(events[0]["countdown_remaining"],
                               ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S, delta=0.5)
        self.assertEqual(events[0]["my_faction"], "arena_time_a")
        self.assertEqual(events[0]["mode"], "2v2")

    def test_fim_do_preparo_abre_portao_e_ativa_lanes(self):
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_accept(eid)
        match = self.ws._bg_active_matches[self.match_id]
        match["countdown_deadline"] = -1.0
        self.ws._tick_bg_pending()
        self.assertTrue(match["fight_started"])
        gate_events = self.ws.consume_bg_gate_open_events()
        self.assertEqual({e["eid"] for e in gate_events}, {self.a, self.b, self.c, self.d})
        # Lanes de minion ativadas pra ESTA instância (nunca durante o
        # preparo, só no gate-open — mesma regra da Arena/debug_bg).
        active_keys = {k[0] for k in self.ws._minion_wave_timers}
        self.assertIn(match["instance_key"], active_keys)

    def test_time_inteiro_no_show_e_descartado_sem_vencedor_por_wo(self):
        """Diferente da Arena (W.O.): BG não elimina por ausência, só por
        Nexus — um lado nunca aparecendo simplesmente devolve quem
        entrou, sem "vitória"."""
        self.ws.request_bg_accept(self.a)
        self.ws.request_bg_accept(self.c)
        match = self.ws._bg_active_matches[self.match_id]
        match["accept_deadline"] = -1.0
        self.ws._tick_bg_pending()
        self.assertNotIn(self.match_id, self.ws._bg_active_matches)
        self.assertIsNone(self.ws.world.get_component(self.a, Faction))
        self.assertNotIn(self.a, self.ws._player_bg_match_id)

    def test_nenhum_dos_4_aceita_partida_e_descartada(self):
        match = self.ws._bg_active_matches[self.match_id]
        match["accept_deadline"] = -1.0
        self.ws._tick_bg_pending()
        self.assertNotIn(self.match_id, self.ws._bg_active_matches)
        for eid in (self.a, self.b, self.c, self.d):
            self.assertNotIn(eid, self.ws._pending_bg_invite)


class TestBgQueueNexusEnd(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "nx_a", 130, 374)
        self.c = spawn_player(self.ws, "nx_c", 131, 374)
        self.b = spawn_player(self.ws, "nx_b", 132, 374)
        self.d = spawn_player(self.ws, "nx_d", 133, 374)
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        self.match_id = self.ws._pending_bg_invite[self.a]
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_accept(eid)
        match = self.ws._bg_active_matches[self.match_id]
        match["countdown_deadline"] = -1.0
        self.ws._tick_bg_pending()   # abre o portão, fight_started=True
        self.consume_all = (self.ws.consume_bg_match_found_events,
                            self.ws.consume_bg_match_start_events,
                            self.ws.consume_bg_gate_open_events)
        for c in self.consume_all:
            c()

    def test_nexus_derrubado_decide_a_partida_e_manda_placar_dos_4(self):
        instance_key = self.ws._bg_active_matches[self.match_id]["instance_key"]
        self.ws.notify_bg_queue_nexus_destroyed(instance_key, "arena_time_b", self.a)
        match = self.ws._bg_active_matches[self.match_id]
        self.assertTrue(match["decided"])
        self.assertEqual(match["winner_faction"], "arena_time_a")

        events = self.ws.consume_bg_match_result_events()
        self.assertEqual(len(events), 4)
        eids = {eid for eid, _ in events}
        self.assertEqual(eids, {self.a, self.b, self.c, self.d})
        _, payload = events[0]
        self.assertEqual(payload["winner_faction"], "arena_time_a")
        self.assertEqual(payload["mode"], "2v2")
        by_eid = {p["eid"]: p for p in payload["players"]}
        self.assertTrue(by_eid[self.a]["won"])
        self.assertFalse(by_eid[self.b]["won"])

    def test_nexus_de_outra_instancia_nao_afeta_esta_partida(self):
        """A causa raiz do bug original (§34.74.30-equivalente pra fila
        real): 2 instâncias coexistindo com a MESMA facção "arena_time_a/
        b" — uma torre caindo em OUTRA instância nunca deveria decidir
        esta partida."""
        self.ws.notify_bg_queue_nexus_destroyed("outra/instancia.csv::999",
                                                "arena_time_b", self.a)
        match = self.ws._bg_active_matches[self.match_id]
        self.assertFalse(match["decided"])
        self.assertEqual(self.ws.consume_bg_match_result_events(), [])

    def test_idempotente_segunda_chamada_nao_sobrescreve(self):
        instance_key = self.ws._bg_active_matches[self.match_id]["instance_key"]
        self.ws.notify_bg_queue_nexus_destroyed(instance_key, "arena_time_b", self.a)
        first_decided_at = self.ws._bg_active_matches[self.match_id]["decided_at"]
        self.ws.notify_bg_queue_nexus_destroyed(instance_key, "arena_time_a", self.b)
        self.assertEqual(self.ws._bg_active_matches[self.match_id]["decided_at"], first_decided_at)
        self.assertEqual(self.ws._bg_active_matches[self.match_id]["winner_faction"], "arena_time_a")

    def test_kda_reflete_delta_desde_o_snapshot_de_entrada(self):
        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        cst_a.players_killed = 2
        cst_a.minions_killed = 15
        wallet_a = self.ws.world.get_component(self.a, Wallet)
        wallet_a.gold += 300
        instance_key = self.ws._bg_active_matches[self.match_id]["instance_key"]
        self.ws.notify_bg_queue_nexus_destroyed(instance_key, "arena_time_b", self.a)
        _, payload = self.ws.consume_bg_match_result_events()[0]
        row_a = next(p for p in payload["players"] if p["eid"] == self.a)
        self.assertEqual(row_a["kills"], 2)
        self.assertEqual(row_a["farm"], 15)
        self.assertEqual(row_a["gold"], 300)


class TestBgQueueLeave(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "lv_a", 140, 380)
        self.c = spawn_player(self.ws, "lv_c", 141, 380)
        self.b = spawn_player(self.ws, "lv_b", 142, 380)
        self.d = spawn_player(self.ws, "lv_d", 143, 380)
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_queue_join(eid, "2v2")
        self.ws._tick_bg_queue()
        self.match_id = self.ws._pending_bg_invite[self.a]
        for eid in (self.a, self.c, self.b, self.d):
            self.ws.request_bg_accept(eid)

    def test_sair_no_meio_da_luta_restaura_mapa_posicao_e_faccao(self):
        reason = self.ws.request_bg_leave(self.a)
        self.assertIsNone(reason)
        self.assertIsNone(self.ws.world.get_component(self.a, Faction))
        self.assertEqual(self.ws.get_entity_map(self.a), "maps/map_1.csv")
        tm = self.ws.world.get_component(self.a, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (140, 380))
        self.assertNotIn(self.a, self.ws._player_bg_match_id)

    def test_sair_restaura_progressao_real(self):
        from server.instance_progression import is_in_normalized_progression
        self.assertTrue(is_in_normalized_progression(self.ws, self.a))
        self.ws.request_bg_leave(self.a)
        self.assertFalse(is_in_normalized_progression(self.ws, self.a))

    def test_sair_de_quem_nao_esta_em_partida_retorna_not_in_match(self):
        solo = spawn_player(self.ws, "lv_solo", 130, 374)
        self.assertEqual(self.ws.request_bg_leave(solo), "not_in_match")

    def test_ultimo_a_sair_desaloca_a_instancia(self):
        instance_key = self.ws._bg_active_matches[self.match_id]["instance_key"]
        self.ws.request_bg_leave(self.a)
        self.ws.request_bg_leave(self.c)
        self.ws.request_bg_leave(self.b)
        self.assertIn(instance_key, self.ws._map_bundles)
        self.ws.request_bg_leave(self.d)
        self.assertNotIn(self.match_id, self.ws._bg_active_matches)
        self.assertNotIn(instance_key, self.ws._map_bundles)

    def test_desconexao_chama_end_bg_matches_of(self):
        self.ws.end_bg_matches_of(self.a)
        self.assertIsNone(self.ws.world.get_component(self.a, Faction))
        self.assertNotIn(self.a, self.ws._player_bg_match_id)
        # os outros continuam na partida normalmente
        self.assertIn(self.b, self.ws._player_bg_match_id)

    def test_timeout_automatico_forca_saida_apos_decisao(self):
        match = self.ws._bg_active_matches[self.match_id]
        match["countdown_deadline"] = -1.0
        self.ws._tick_bg_pending()
        instance_key = match["instance_key"]
        self.ws.notify_bg_queue_nexus_destroyed(instance_key, "arena_time_b", self.a)
        import time as _time_test
        match["decided_at"] = _time_test.time() - 9999.0
        self.ws._tick_bg_results_timeout()
        self.assertNotIn(self.match_id, self.ws._bg_active_matches)
        for eid in (self.a, self.b, self.c, self.d):
            self.assertIsNone(self.ws.world.get_component(eid, Faction))


if __name__ == "__main__":
    unittest.main()
