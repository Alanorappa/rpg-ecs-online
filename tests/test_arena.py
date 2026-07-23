"""
tests/test_arena.py — Arena 1x1/2x2/3x3 (Fase G leva 1 + Fase H):
instanciamento privado por partida, time por Facção, fila FIFO por modo
(grupo pré-formado ou soloqueue no 1x1), eliminação (golpe letal mata de
verdade — fantasma real, sem revive — ver §34.32 ARQUITETURA_ONLINE.md,
20/07/2026). A maioria dos testes deste arquivo é pré-Fase H e exercita
só o modo "2v2" (default de `request_arena_queue_join`) — não precisou
de nenhuma mudança pra continuar passando; `TestArenaModesGeneralization`
no fim do arquivo cobre especificamente 1x1 (soloqueue) e 3x3 (trio) e a
validação de modo. Ver server/match_processor.py::ARENA_MODES.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.faction_system import can_engage
from engine.core_systems import apply_damage_core
from engine.components import CombatStats, CombatState, Faction, TileMovement, GhostState, CharacterStats


def _make_duo(ws, prefix: str, tile=(130, 374)):
    a = spawn_player(ws, f"{prefix}_a", *tile)
    b = spawn_player(ws, f"{prefix}_b", *tile)
    ws.request_party_invite(a, b)
    ws.respond_party_invite(b, accept=True)
    return a, b


def _make_trio(ws, prefix: str, tile=(130, 374)):
    a = spawn_player(ws, f"{prefix}_a", *tile)
    b = spawn_player(ws, f"{prefix}_b", *tile)
    c = spawn_player(ws, f"{prefix}_c", *tile)
    ws.request_party_invite(a, b)
    ws.respond_party_invite(b, accept=True)
    ws.request_party_invite(a, c)
    ws.respond_party_invite(c, accept=True)
    return a, b, c


def _queue_and_pair(ws, team_a, team_b):
    """Pareia os 2 grupos E aceita pelos 4 na hora — preserva o
    comportamento "entrada imediata" que a maioria dos testes deste
    arquivo assume, sem precisar reescrever cada um pro fluxo de aceite
    (ARENA_MATCH_FOUND/ARENA_MATCH_ACCEPT, 21/07/2026 — ver
    TestArenaAceiteContagem pros testes que exercitam o aceite de
    verdade)."""
    ws.request_arena_queue_join(team_a[0])
    ws.request_arena_queue_join(team_b[0])
    ws._tick_arena_queue()
    match_id = ws._pending_arena_invite[team_a[0]]
    for eid in team_a + team_b:
        ws.request_arena_accept(eid)
    return match_id


class TestArenaQueue(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_grupo_de_2_entra_na_fila(self):
        a, b = _make_duo(self.ws, "g1")
        pid = self.ws.get_party_id_of(a)
        self.assertIsNone(self.ws.request_arena_queue_join(a))
        self.assertIn(pid, self.ws._arena_queues["2v2"])

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
        # a+b pareados (partida PROPOSTA — ninguém entrou ainda), c
        # continua sozinho na fila
        self.assertEqual(len(self.ws._active_matches), 1)
        self.assertEqual(len(self.ws._arena_queues["2v2"]), 1)
        for eid in team_a + team_b:
            self.assertIn(eid, self.ws._pending_arena_invite)
            self.assertNotIn(eid, self.ws._player_match_id)
        for eid in team_c:
            self.assertNotIn(eid, self.ws._pending_arena_invite)
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

    def test_golpe_letal_mata_de_verdade_marca_eliminado_e_vira_fantasma(self):
        """Revisado 20/07/2026 (pedido do usuário): eliminação na arena
        agora é MORTE DE VERDADE — mesmo fluxo de PvE (GhostState, corpo,
        "Liberar espírito"), não mais "1 HP + imune" congelado. O
        interceptor de golpe letal da arena (_arena_lethal_interceptor)
        virou só um ponto de NOTIFICAÇÃO — sempre retorna False, nunca
        intercepta de verdade."""
        target = self.team_b[0]
        outcome = apply_damage_core(self.ws.world, target, 999999, killer_eid=self.team_a[0])
        self.assertEqual(outcome, "killed")
        self.assertIn(target, self.ws._active_matches[self.match_id]["eliminated"])
        # simula o processamento de PendingDeath (ServerDeathHandler roda no tick)
        self.ws._handle_player_death(target)
        gst = self.ws.world.get_component(target, GhostState)
        self.assertTrue(gst.is_dead)
        self.assertFalse(gst.is_ghost)
        # partida NÃO acabou — só 1 dos 2 do time B foi eliminado
        self.assertIn(self.match_id, self.ws._active_matches)

    def test_morto_nao_pode_mais_atacar_mesmo_se_cliente_tentar_burlar(self):
        """Bug real relatado pelo usuário 20/07/2026: "continuam
        controlando o personagem mesmo após perder" + "ainda são alvos
        atacáveis". current_hp<=0 (morte real) já bloqueia como ALVO em
        qualquer lugar do jogo (combat_processor/spell_completion_processor,
        comentário em server/respawn_system.py linha 8); aqui provamos
        que TAMBÉM não consegue mais atacar — mesmo se o cliente tentar
        burlar mandando um alvo novo depois de morto (GhostState.is_dead
        bloqueia em combat_processor.py explicitamente, já que
        CombatState.is_alive nunca é setado pelo servidor — só o cliente
        mexe nele)."""
        target = self.team_b[0]
        apply_damage_core(self.ws.world, target, 999999, killer_eid=self.team_a[0])
        self.ws._handle_player_death(target)
        cst = self.ws.world.get_component(target, CombatState)
        self.assertEqual(cst.target_entity_id, -1)   # já limpo por _handle_player_death

        # Simula um cliente "burlado" re-setando o alvo depois de morto
        victim = self.team_a[1]
        cst.target_entity_id = victim
        victim_cs = self.ws.world.get_component(victim, CombatStats)
        hp_before = victim_cs.current_hp
        self.ws._process_player_attacks(1.0, {})
        self.assertEqual(victim_cs.current_hp, hp_before)

    def test_eliminar_time_inteiro_decide_a_partida_mas_nao_restaura_ninguem_ainda(self):
        """Ciclo revisado 20/07/2026 (pedido do usuário — modal de fim de
        partida estilo WoW): time inteiro eliminado DECIDE a partida
        (congela todo mundo, monta o placar) mas NÃO teleporta/restaura
        ninguém ainda — isso só acontece quando cada um clica "Sair da
        Arena" (ver TestArenaMatchResult)."""
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        self.assertIn(self.match_id, self.ws._active_matches)
        match = self.ws._active_matches[self.match_id]
        self.assertTrue(match["decided"])
        self.assertEqual(match["winner_members"], set(self.team_a))
        # ninguém foi restaurado ainda — todos continuam na arena
        for eid in self.team_a + self.team_b:
            self.assertIsNotNone(self.ws.world.get_component(eid, Faction))
        self.assertEqual(self.ws.consume_arena_match_end_events(), [])

    def test_time_inteiro_eliminado_congela_o_vencedor_perdedor_ja_morreu_de_verdade(self):
        """Revisado 20/07/2026: o time PERDEDOR já morreu de verdade
        (fantasma real, não precisa de congelamento nenhum — current_hp<=0
        já é suficiente). O time VENCEDOR, que continua vivo, precisa ser
        congelado explicitamente pra tela de resultado — senão continuaria
        brigando (contra quê? só teria bystanders fora da instância, mas o
        princípio vale) enquanto o placar é mostrado pros 4."""
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        for eid in self.team_a:   # vencedor — congelado explicitamente
            cst = self.ws.world.get_component(eid, CombatState)
            self.assertFalse(cst.can_act())
            self.assertFalse(cst.can_move())
        for eid in self.team_b:   # perdedor — já morto de verdade
            cs = self.ws.world.get_component(eid, CombatStats)
            self.assertLessEqual(cs.current_hp, 0)

    def test_fim_de_partida_nao_limpa_stun_real_de_quem_nao_foi_eliminado(self):
        """Vencedor pode legitimamente estar stunado por um efeito de
        combate não-relacionado no instante exato em que a partida
        termina — _finish_match só REAFIRMA is_stunned=True (já era),
        não pode reescrever um stun_timer real."""
        winner = self.team_a[0]
        cst_winner = self.ws.world.get_component(winner, CombatState)
        cst_winner.is_stunned = True
        cst_winner.stun_timer = 3.0
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        self.assertTrue(cst_winner.is_stunned)
        self.assertEqual(cst_winner.stun_timer, 3.0)

    def test_desconexao_do_time_inteiro_decide_a_partida_sem_remover_o_outro_time(self):
        self.ws.end_matches_of(self.team_b[0])
        self.ws.end_matches_of(self.team_b[1])
        # time B já saiu de verdade (desconectou) — time A só foi DECIDIDO
        # (congelado, aguardando sair), a partida continua existindo
        self.assertIn(self.match_id, self.ws._active_matches)
        match = self.ws._active_matches[self.match_id]
        self.assertTrue(match["decided"])
        for eid in self.team_a:
            self.assertIn(eid, self.ws._player_match_id)
            cst = self.ws.world.get_component(eid, CombatState)
            self.assertFalse(cst.can_act())
        for eid in self.team_b:
            self.assertNotIn(eid, self.ws._player_match_id)


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
        self.assertTrue(ik1.startswith("maps/arena_poco_negro.csv::"))
        self.assertTrue(ik2.startswith("maps/arena_poco_negro.csv::"))
        self.assertIn(ik1, self.ws._map_bundles)
        self.assertIn(ik2, self.ws._map_bundles)

    def test_template_file_of_traduz_pro_arquivo_real(self):
        ik1 = self.ws._active_matches[self.match_1]["instance_key"]
        self.assertEqual(self.ws._template_file_of(ik1), "maps/arena_poco_negro.csv")

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
        # (0,0) é canto de parede no arena_poco_negro.csv (borda inteira é "#")
        self.assertFalse(is_tile_walkable(self.team_a1[0], 0, 0))
        # (13,13) é chão aberto dentro da arena circular central
        self.assertTrue(is_tile_walkable(self.team_a1[0], 13, 13))

        self.ws.register_map_services_for(self.team_a2[0])
        self.assertFalse(is_tile_walkable(self.team_a2[0], 0, 0))
        self.assertTrue(is_tile_walkable(self.team_a2[0], 13, 13))

    def test_terminar_uma_partida_nao_afeta_a_outra(self):
        for eid in self.team_b1:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a1[0])
        self.assertTrue(self.ws._active_matches[self.match_1]["decided"])
        self.assertFalse(self.ws._active_matches[self.match_2]["decided"])
        self.assertIn(self.match_2, self.ws._active_matches)
        ik2 = self.ws._active_matches[self.match_2]["instance_key"]
        self.assertIn(ik2, self.ws._map_bundles)
        for eid in self.team_a2 + self.team_b2:
            self.assertIn(eid, self.ws._player_match_id)
        # match_1 só desaloca de vez quando os 4 saírem de verdade
        for eid in self.team_a1 + self.team_b1:
            self.ws.request_arena_forfeit(eid)
        self.assertNotIn(self.match_1, self.ws._active_matches)


class TestArenaForfeit(unittest.TestCase):
    """Comando de chat /forfeit ou /ff (feedback do usuário 20/07/2026:
    precisa de um jeito de sair da arena sem esperar o time inteiro
    perder)."""

    def setUp(self):
        self.ws = make_world_server()
        self.team_a = _make_duo(self.ws, "fta")
        self.team_b = _make_duo(self.ws, "ftb")
        self.match_id = _queue_and_pair(self.ws, self.team_a, self.team_b)

    def test_forfeit_de_um_membro_nao_termina_a_partida(self):
        reason = self.ws.request_arena_forfeit(self.team_a[0])
        self.assertIsNone(reason)
        self.assertIn(self.match_id, self.ws._active_matches)
        self.assertNotIn(self.team_a[0], self.ws._active_matches[self.match_id]["team_a"])
        self.assertNotIn(self.team_a[0], self.ws._player_match_id)
        # o outro membro do time A continua na partida normalmente
        self.assertIn(self.team_a[1], self.ws._player_match_id)

    def test_forfeit_restaura_mapa_posicao_e_faccao_na_hora(self):
        self.ws.request_arena_forfeit(self.team_a[0])
        self.assertIsNone(self.ws.world.get_component(self.team_a[0], Faction))
        self.assertEqual(self.ws.get_entity_map(self.team_a[0]), "maps/map_1.csv")
        tm = self.ws.world.get_component(self.team_a[0], TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (130, 374))

    def test_forfeit_voluntario_nao_limpa_stun_real_nao_relacionado(self):
        """Forfeit nunca passou por _eliminate_player (não foi golpe
        letal) — nunca setou is_stunned, então nunca deveria limpar um
        stun real e coincidente. Simula o preparo (portão físico) já ter
        acabado — cenário realista de forfeit no MEIO da partida, bem
        depois do combate ter liberado."""
        match = self.ws._active_matches[self.match_id]
        match["countdown_deadline"] = -1.0
        self.ws._tick_arena_pending()
        cst = self.ws.world.get_component(self.team_a[0], CombatState)
        cst.is_stunned = True
        cst.stun_timer = 3.0
        self.ws.request_arena_forfeit(self.team_a[0])
        self.assertTrue(cst.is_stunned)
        self.assertEqual(cst.stun_timer, 3.0)

    def test_forfeit_de_todo_o_time_decide_a_partida_time_b_congela_ate_sair(self):
        for eid in self.team_a:
            self.ws.request_arena_forfeit(eid)
        # time A já saiu de verdade — recebeu o próprio ARENA_MATCH_END (derrota)
        events = {e["eid"]: e["won"] for e in self.ws.consume_arena_match_end_events()}
        for eid in self.team_a:
            self.assertFalse(events[eid])
            self.assertIsNone(self.ws.world.get_component(eid, Faction))
        # time B ainda não saiu — só foi DECIDIDO (congelado), continua na arena
        self.assertIn(self.match_id, self.ws._active_matches)
        self.assertTrue(self.ws._active_matches[self.match_id]["decided"])
        for eid in self.team_b:
            self.assertIsNotNone(self.ws.world.get_component(eid, Faction))
            cst = self.ws.world.get_component(eid, CombatState)
            self.assertFalse(cst.can_act())

        # time B sai de verdade (clica "Sair da Arena" == mesmo /forfeit)
        for eid in self.team_b:
            self.ws.request_arena_forfeit(eid)
        self.assertNotIn(self.match_id, self.ws._active_matches)
        events_b = {e["eid"]: e["won"] for e in self.ws.consume_arena_match_end_events()}
        for eid in self.team_b:
            self.assertTrue(events_b[eid])
            self.assertIsNone(self.ws.world.get_component(eid, Faction))

    def test_forfeit_fora_de_partida_retorna_reason(self):
        solo = spawn_player(self.ws, "ftsolo", 130, 374)
        self.assertEqual(self.ws.request_arena_forfeit(solo), "not_in_match")


class TestArenaDisconnectRestoreOrder(unittest.TestCase):
    """Bug real relatado pelo usuário 20/07/2026 (com print): "quando
    reloguei apareci em algum lugar que não era a arena, mas os outros
    players estavam a minha volta". Causa raiz: server/session.py::
    on_disconnect salvava o personagem ANTES de end_matches_of rodar —
    o save capturava o map_id sintético da instância da arena (só existe
    em memória) + o tile relativo ao spawn da arena. No próximo login,
    spawn_player não reconhecia mais aquele map_id (instância já
    descarregada) e caía no mapa principal, mas MANTINHA o tile da
    arena — o jogador aparecia num tile aleatório do mapa principal,
    junto de qualquer outro que tivesse passado pela mesma partida (só 4
    tiles de spawn possíveis). Aqui testamos a parte de WorldServer:
    get_player_map/get_tile_pos/get_player_save_data têm que refletir o
    mapa/tile de ORIGEM logo depois de end_matches_of, nunca a
    instância."""

    def test_end_matches_of_restaura_map_id_e_tile_antes_do_save(self):
        ws = make_world_server()
        team_a = _make_duo(ws, "orda", (140, 380))
        team_b = _make_duo(ws, "ordb", (140, 380))
        _queue_and_pair(ws, team_a, team_b)

        eid = team_a[0]
        sid = ws.get_session_id_for_player(eid)
        # confirma que está DE VERDADE na instância antes de desconectar
        # (senão o teste não prova nada)
        self.assertTrue(ws.get_player_map(sid).startswith("maps/arena_poco_negro.csv::"))

        ws.end_matches_of(eid)

        self.assertEqual(ws.get_player_map(sid), "maps/map_1.csv")
        tx, ty = ws.get_tile_pos(sid)
        self.assertEqual((tx, ty), (140, 380))
        save_data = ws.get_player_save_data(sid)
        self.assertEqual(save_data["map_id"], "maps/map_1.csv")
        self.assertEqual((save_data["tile_x"], save_data["tile_y"]), (140, 380))


class TestArenaRealDeath(unittest.TestCase):
    """Morte de verdade na arena (revisado 20/07/2026, pedido do usuário):
    eliminado vira fantasma real (mesmo fluxo de PvE), mas não pode
    reviver enquanto a partida durar — só ao sair (_arena_leave_now
    revive automaticamente, mesmo padrão de _auto_revive_on_disconnect)."""

    def setUp(self):
        self.ws = make_world_server()
        self.team_a = _make_duo(self.ws, "rda")
        self.team_b = _make_duo(self.ws, "rdb")
        self.match_id = _queue_and_pair(self.ws, self.team_a, self.team_b)

    def _kill(self, target, killer):
        apply_damage_core(self.ws.world, target, 999999, killer_eid=killer)
        self.ws._handle_player_death(target)

    def test_liberar_espirito_na_arena_fica_dentro_da_instancia(self):
        """_handle_release_spirit normalmente transfere o fantasma pro
        mapa principal (cemitério) quando a morte é fora dele — dentro da
        arena isso puxaria o player pra fora da instância antes da hora.
        Fix: fica no próprio tile, sem trocar de mapa."""
        target = self.team_b[0]
        self._kill(target, self.team_a[0])
        tm_before = self.ws.world.get_component(target, TileMovement)
        tx_before, ty_before = tm_before.current_tile_x, tm_before.current_tile_y
        instance_key = self.ws._active_matches[self.match_id]["instance_key"]

        self.ws._handle_release_spirit(target)

        gst = self.ws.world.get_component(target, GhostState)
        self.assertTrue(gst.is_ghost)
        self.assertEqual(self.ws.get_entity_map(target), instance_key)
        tm_after = self.ws.world.get_component(target, TileMovement)
        self.assertEqual((tm_after.current_tile_x, tm_after.current_tile_y),
                         (tx_before, ty_before))

    def test_ghost_tick_nunca_avanca_pra_quem_esta_em_partida(self):
        """Sem esse guard, o corpo (perto do próprio fantasma dentro da
        instância) dispararia "near_corpse" — prompt fantasma "Reviver
        agora?" que não leva a lugar nenhum (revive já é bloqueado)."""
        target = self.team_b[0]
        self._kill(target, self.team_a[0])
        self.ws._handle_release_spirit(target)
        gst = self.ws.world.get_component(target, GhostState)
        self.ws._tick_ghost_states(dt=1.0)
        self.assertFalse(gst.near_corpse)
        self.assertEqual(gst.graveyard_timer, 0.0)

    def test_revive_request_bloqueado_durante_partida_ativa(self):
        import asyncio
        from server.session import SessionManager, Session

        target = self.team_b[0]
        self._kill(target, self.team_a[0])
        self.ws._handle_release_spirit(target)
        gst = self.ws.world.get_component(target, GhostState)
        self.assertTrue(gst.is_ghost)

        mgr = SessionManager(self.ws)
        sid = self.ws.get_session_id_for_player(target)
        session = Session(None, sid)
        session.authenticated = True

        asyncio.run(mgr._handle_revive_request(session, {}, 0))

        gst_after = self.ws.world.get_component(target, GhostState)
        self.assertTrue(gst_after.is_ghost, "revive deveria ter sido recusado")
        self.assertTrue(gst_after.is_dead)

    def test_sair_da_arena_revive_quem_morreu_de_verdade(self):
        """Único jeito de sair da morte durante a arena: sair da arena de
        vez (forfeit/timeout) — _arena_leave_now reaproveita _revive_player
        (mesmo padrão de _auto_revive_on_disconnect)."""
        target = self.team_b[0]
        self._kill(target, self.team_a[0])
        self.ws._handle_release_spirit(target)

        reason = self.ws.request_arena_forfeit(target)
        self.assertIsNone(reason)

        gst = self.ws.world.get_component(target, GhostState)
        self.assertFalse(gst.is_dead)
        self.assertFalse(gst.is_ghost)
        cs = self.ws.world.get_component(target, CombatStats)
        self.assertGreater(cs.current_hp, 0)
        self.assertEqual(self.ws.get_entity_map(target), "maps/map_1.csv")

    def test_sair_da_arena_sem_ter_morrido_nao_mexe_em_ghoststate(self):
        """Forfeit voluntário de quem está vivo não deveria acionar o
        caminho de revive (gst.is_dead é False o tempo todo)."""
        alive = self.team_a[0]
        gst_before = self.ws.world.get_component(alive, GhostState)
        self.assertFalse(gst_before.is_dead)
        reason = self.ws.request_arena_forfeit(alive)
        self.assertIsNone(reason)
        gst_after = self.ws.world.get_component(alive, GhostState)
        self.assertFalse(gst_after.is_dead)
        self.assertFalse(gst_after.is_ghost)


class TestArenaMatchResult(unittest.TestCase):
    """Modal de fim de partida (placar + "Sair da Arena", estilo WoW —
    pedido do usuário 20/07/2026): dano rastreado por player durante a
    partida, ARENA_MATCH_RESULT mandado uma vez na decisão, restauração
    de verdade só quando cada um sai (ARENA_FORFEIT, reusado pelo botão)."""

    def setUp(self):
        self.ws = make_world_server()
        self.team_a = _make_duo(self.ws, "mra")
        self.team_b = _make_duo(self.ws, "mrb")
        self.match_id = _queue_and_pair(self.ws, self.team_a, self.team_b)

    def test_dano_e_rastreado_por_player_durante_a_partida(self):
        apply_damage_core(self.ws.world, self.team_b[0], 50, killer_eid=self.team_a[0])
        apply_damage_core(self.ws.world, self.team_b[1], 30, killer_eid=self.team_a[0])
        apply_damage_core(self.ws.world, self.team_a[0], 10, killer_eid=self.team_b[0])
        dmg = self.ws._active_matches[self.match_id]["damage_by_eid"]
        self.assertEqual(dmg[self.team_a[0]], 80)
        self.assertEqual(dmg[self.team_b[0]], 10)
        self.assertEqual(dmg[self.team_a[1]], 0)

    def test_dano_fora_de_qualquer_partida_nao_quebra_nada(self):
        solo_a = spawn_player(self.ws, "mrsolo_a", 130, 374)
        solo_b = spawn_player(self.ws, "mrsolo_b", 131, 374)
        # sem killer_eid em partida — não deveria levantar exceção nem
        # tocar em nenhum match ativo
        apply_damage_core(self.ws.world, solo_b, 10, killer_eid=solo_a)
        self.assertEqual(self.ws._active_matches[self.match_id]["damage_by_eid"][self.team_a[0]], 0)

    def test_arena_match_result_manda_nome_dano_vitoria_dos_4(self):
        apply_damage_core(self.ws.world, self.team_b[0], 999999, killer_eid=self.team_a[0])
        apply_damage_core(self.ws.world, self.team_b[1], 40, killer_eid=self.team_a[1])
        apply_damage_core(self.ws.world, self.team_b[1], 999999, killer_eid=self.team_a[1])

        events = self.ws.consume_arena_match_result_events()
        self.assertEqual(len(events), 4)   # 1 por player da partida
        eids_notificados = {e["eid"] for e in events}
        self.assertEqual(eids_notificados, set(self.team_a + self.team_b))

        results = events[0]["results"]
        self.assertEqual(len(results), 4)
        by_name = {r["name"]: r for r in results}
        from engine.components import CharacterStats
        name_a0 = self.ws.world.get_component(self.team_a[0], CharacterStats).name
        name_a1 = self.ws.world.get_component(self.team_a[1], CharacterStats).name
        self.assertEqual(by_name[name_a0]["damage"], 999999)
        self.assertEqual(by_name[name_a1]["damage"], 40 + 999999)
        self.assertTrue(by_name[name_a0]["won"])
        self.assertTrue(by_name[name_a1]["won"])
        name_b0 = self.ws.world.get_component(self.team_b[0], CharacterStats).name
        self.assertFalse(by_name[name_b0]["won"])

        # Cada linha carrega o eid de quem ela pertence — o cliente usa
        # isso (não o nome) pra achar "minha linha" no modal de resultado,
        # já que nomes de personagem podem se repetir entre contas (bug
        # real: resultado invertido quando 2 personagens tinham o mesmo
        # nome na mesma partida, ver PROBLEMAS_ARQUITETURA.md).
        by_eid = {r["eid"]: r for r in results}
        self.assertEqual(set(by_eid.keys()), set(self.team_a + self.team_b))
        self.assertTrue(by_eid[self.team_a[0]]["won"])
        self.assertFalse(by_eid[self.team_b[0]]["won"])

    def test_sair_da_arena_depois_de_decidida_restaura_so_quem_saiu(self):
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        self.ws.consume_arena_match_result_events()

        reason = self.ws.request_arena_forfeit(self.team_a[0])
        self.assertIsNone(reason)
        self.assertIsNone(self.ws.world.get_component(self.team_a[0], Faction))
        cst_a0 = self.ws.world.get_component(self.team_a[0], CombatState)
        self.assertTrue(cst_a0.can_act())
        # o resto ainda está congelado, esperando sair
        for eid in [self.team_a[1]] + list(self.team_b):
            self.assertIsNotNone(self.ws.world.get_component(eid, Faction))
        self.assertIn(self.match_id, self.ws._active_matches)

    def test_ultimo_a_sair_desaloca_a_instancia(self):
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        instance_key = self.ws._active_matches[self.match_id]["instance_key"]
        for eid in self.team_a + self.team_b:
            self.ws.request_arena_forfeit(eid)
        self.assertNotIn(self.match_id, self.ws._active_matches)
        self.assertNotIn(instance_key, self.ws._map_bundles)

    def test_timeout_automatico_forca_saida_de_quem_nao_clicou(self):
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        match = self.ws._active_matches[self.match_id]
        import time as _time_test
        match["decided_at"] = _time_test.time() - 9999.0   # já bem depois do timeout
        self.ws._tick_arena_results_timeout()
        self.assertNotIn(self.match_id, self.ws._active_matches)
        for eid in self.team_a + self.team_b:
            self.assertIsNone(self.ws.world.get_component(eid, Faction))
            self.assertNotIn(eid, self.ws._player_match_id)

    def test_timeout_nao_dispara_antes_da_hora(self):
        for eid in self.team_b:
            apply_damage_core(self.ws.world, eid, 999999, killer_eid=self.team_a[0])
        self.ws._tick_arena_results_timeout()
        self.assertIn(self.match_id, self.ws._active_matches)
        for eid in self.team_a + self.team_b:
            self.assertIsNotNone(self.ws.world.get_component(eid, Faction))


class TestArenaResourceReset(unittest.TestCase):
    """Recursos restaurados ao entrar E ao sair da arena (pedido do
    usuário 20/07/2026): "os personagens que entram na arena, precisam
    ter todos os recursos restaurados, HP, Mana, concentração, cooldowns
    de skills e quando saem da arena é a mesma coisa"."""

    def setUp(self):
        self.ws = make_world_server()

    def _damage_resources(self, eid):
        from engine.components import PlayerSkills, Skill
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.current_hp = 1
        char = self.ws.world.get_component(eid, CharacterStats)
        char.max_mana = 100
        char.mana = 5
        char.max_concentration = 50
        char.concentration = 0
        ps = self.ws.world.get_component(eid, PlayerSkills)
        if ps is None:
            ps = PlayerSkills()
            self.ws.world.add_component(eid, ps)
        sk = Skill("Teste", "desc", 10.0)
        sk.current_cooldown = 8.0
        ps.gcd_timer = 0.5
        ps.skills[0] = sk
        self.ws._skill_last_used[(eid, "teste_skill")] = 999999.0

    def _assert_resources_restored(self, eid):
        cs = self.ws.world.get_component(eid, CombatStats)
        self.assertEqual(cs.current_hp, cs.max_hp)
        char = self.ws.world.get_component(eid, CharacterStats)
        self.assertEqual(char.mana, char.max_mana)
        self.assertEqual(char.concentration, char.max_concentration)
        # _skill_last_used é o cooldown AUTORITATIVO (server/skill_processor.py:
        # "Impede spam mesmo que o cliente manipule current_cooldown local") —
        # PlayerSkills.skills[i].current_cooldown é só exibição, ticado e
        # resetado no CLIENTE (client/arena_handlers.py::
        # _reset_local_arena_resources), não faz sentido testar aqui.
        self.assertNotIn((eid, "teste_skill"), self.ws._skill_last_used)

    def test_entrar_na_arena_restaura_recursos(self):
        team_a = _make_duo(self.ws, "rra")
        for eid in team_a:
            self._damage_resources(eid)
        team_b = _make_duo(self.ws, "rrb")
        _queue_and_pair(self.ws, team_a, team_b)
        for eid in team_a:
            self._assert_resources_restored(eid)

    def test_sair_da_arena_restaura_recursos(self):
        team_a = _make_duo(self.ws, "rrc")
        team_b = _make_duo(self.ws, "rrd")
        _queue_and_pair(self.ws, team_a, team_b)
        # danifica de novo DENTRO da partida (entrar já limpou uma vez)
        for eid in team_a:
            self._damage_resources(eid)
        for eid in team_a:
            self.ws.request_arena_forfeit(eid)
        for eid in team_a:
            self._assert_resources_restored(eid)


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


class TestArenaAceiteContagem(unittest.TestCase):
    """Aceite de partida + contagem regressiva de preparo (feedback do
    usuário 21/07/2026): fila pareia mas não teleporta ninguém — cada um
    dos 4 precisa mandar ARENA_MATCH_ACCEPT (aqui: request_arena_accept)
    dentro de ARENA_ACCEPT_WINDOW_S, senão simplesmente não entra. Preparo
    de ARENA_COUNTDOWN_S começa no primeiro aceite de QUALQUER um dos 4 —
    é da PARTIDA, não por-jogador."""

    def setUp(self):
        self.ws = make_world_server()
        self.team_a = _make_duo(self.ws, "aca")
        self.team_b = _make_duo(self.ws, "acb")
        self.ws.request_arena_queue_join(self.team_a[0])
        self.ws.request_arena_queue_join(self.team_b[0])
        self.ws._tick_arena_queue()
        self.match_id = self.ws._pending_arena_invite[self.team_a[0]]

    def test_pareamento_sozinho_nao_teleporta_nem_atribui_faccao(self):
        match = self.ws._active_matches[self.match_id]
        self.assertEqual(match["team_a"], [])
        self.assertEqual(match["team_b"], [])
        self.assertIsNone(match["instance_key"])
        for eid in self.team_a + self.team_b:
            self.assertIsNone(self.ws.world.get_component(eid, Faction))
            self.assertIn(eid, self.ws._pending_arena_invite)

    def test_pareamento_gera_4_eventos_arena_match_found(self):
        events = self.ws.consume_arena_match_found_events()
        self.assertEqual(len(events), 4)
        eids = {e["eid"] for e in events}
        self.assertEqual(eids, set(self.team_a + self.team_b))
        ev_a0 = next(e for e in events if e["eid"] == self.team_a[0])
        self.assertEqual(set(ev_a0["teammates"]), {self.team_a[1]})
        self.assertEqual(set(ev_a0["opponents"]), set(self.team_b))

    def test_aceite_teleporta_so_esse_player_e_atribui_faccao(self):
        reason = self.ws.request_arena_accept(self.team_a[0])
        self.assertIsNone(reason)
        match = self.ws._active_matches[self.match_id]
        self.assertEqual(match["team_a"], [self.team_a[0]])
        self.assertEqual(match["team_b"], [])
        self.assertNotIn(self.team_a[0], self.ws._pending_arena_invite)
        self.assertEqual(self.ws._player_match_id[self.team_a[0]], self.match_id)
        faction = self.ws.world.get_component(self.team_a[0], Faction)
        self.assertEqual(faction.faction_id, "arena_time_a")

    def test_aceite_dispara_arena_match_start_com_countdown_cheio(self):
        """countdown_remaining logo após o pareamento = ACCEPT_WINDOW +
        COUNTDOWN somados (ancorado em propose_time, revisado 21/07/2026 —
        pedido do usuário: "unindo os 2 tempos, será 30 segundos pra
        iniciar a arena a partir do momento que a arena chamou")."""
        self.ws.request_arena_accept(self.team_a[0])
        events = self.ws.consume_arena_match_start_events()
        self.assertEqual(len(events), 1)
        from shared.constants import ARENA_ACCEPT_WINDOW_S, ARENA_COUNTDOWN_S
        self.assertAlmostEqual(events[0]["countdown_remaining"],
                               ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S, delta=0.5)
        # Revisado 22/07/2026 (pedido do usuário — modelo WoW): preparo NÃO
        # trava mais ação/movimento, a contenção é o portão físico (sala de
        # espera fechada, ver test_fim_do_preparo_abre_portao_fisico).
        cst = self.ws.world.get_component(self.team_a[0], CombatState)
        self.assertTrue(cst.can_act())
        self.assertTrue(cst.can_move())

    def test_segundo_aceite_mais_tarde_recebe_countdown_menor(self):
        """Contagem é DA PARTIDA (ancorada em propose_time) — quem entra
        depois já vê o tempo restante menor, nunca reinicia."""
        self.ws.request_arena_accept(self.team_a[0])
        self.ws.consume_arena_match_start_events()
        match = self.ws._active_matches[self.match_id]
        match["countdown_deadline"] -= 7.0   # simula 7s de partida já passados

        self.ws.request_arena_accept(self.team_b[0])
        events = self.ws.consume_arena_match_start_events()
        self.assertEqual(len(events), 1)
        from shared.constants import ARENA_ACCEPT_WINDOW_S, ARENA_COUNTDOWN_S
        self.assertAlmostEqual(events[0]["countdown_remaining"],
                               ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S - 7.0, delta=0.5)

    def test_time_cujo_parceiro_nunca_aceita_segue_so_com_quem_entrou(self):
        self.ws.request_arena_accept(self.team_a[0])
        self.ws.request_arena_accept(self.team_b[0])
        self.ws.request_arena_accept(self.team_b[1])
        # team_a[1] nunca aceita — vence o prazo
        match = self.ws._active_matches[self.match_id]
        match["accept_deadline"] = -1.0
        self.ws._tick_arena_pending()

        self.assertEqual(match["team_a"], [self.team_a[0]])
        self.assertEqual(set(match["team_b"]), set(self.team_b))
        self.assertNotIn(self.team_a[1], self.ws._pending_arena_invite)
        self.assertFalse(match["decided"])   # os dois times têm gente — segue normal, só desbalanceado

    def test_time_inteiro_no_show_perde_por_wo(self):
        self.ws.request_arena_accept(self.team_a[0])
        self.ws.request_arena_accept(self.team_a[1])
        # ninguém do team_b aceita
        match = self.ws._active_matches[self.match_id]
        match["accept_deadline"] = -1.0
        self.ws._tick_arena_pending()

        self.assertTrue(match["decided"])
        self.assertEqual(match["winner_members"], set(self.team_a))

    def test_nenhum_dos_4_aceita_partida_e_descartada(self):
        match = self.ws._active_matches[self.match_id]
        match["accept_deadline"] = -1.0
        self.ws._tick_arena_pending()
        self.assertNotIn(self.match_id, self.ws._active_matches)
        for eid in self.team_a + self.team_b:
            self.assertNotIn(eid, self.ws._pending_arena_invite)

    def test_fim_do_preparo_abre_portao_fisico(self):
        """Revisado 22/07/2026 (pedido do usuário — modelo WoW): em vez de
        travar ação/movimento, o preparo contém cada time numa sala fechada
        — o portão (ARENA_GATE_TILES) é sólido até o countdown vencer, e
        abre sozinho (vira passável) pros dois lados ao mesmo tempo."""
        from engine.world_systems import is_tile_walkable
        from shared.constants import ARENA_GATE_TILES
        self.ws.request_arena_accept(self.team_a[0])
        self.ws.request_arena_accept(self.team_b[0])
        match = self.ws._active_matches[self.match_id]

        self.ws.register_map_services_for(self.team_a[0])
        for gx, gy in ARENA_GATE_TILES:
            self.assertFalse(is_tile_walkable(self.team_a[0], gx, gy))

        match["countdown_deadline"] = -1.0
        self.ws._tick_arena_pending()

        self.assertTrue(match["fight_started"])
        for gx, gy in ARENA_GATE_TILES:
            self.assertTrue(is_tile_walkable(self.team_a[0], gx, gy))

        # Ambos os 2 que já tinham aceitado (só eles estão na instância)
        # recebem o evento de portão aberto, uma vez cada.
        gate_events = self.ws.consume_arena_gate_open_events()
        self.assertEqual({e["eid"] for e in gate_events}, {self.team_a[0], self.team_b[0]})

    def test_aceite_tardio_depois_do_portao_aberto_recebe_evento_na_hora(self):
        """Quem aceita DEPOIS do portão já ter aberto pra essa instância
        carrega o mapa do disco (portão sempre nasce fechado no CSV) — sem
        o evento imediato, o cliente dele nunca saberia que já pode
        atravessar."""
        self.ws.request_arena_accept(self.team_a[0])
        self.ws.request_arena_accept(self.team_b[0])
        match = self.ws._active_matches[self.match_id]
        match["countdown_deadline"] = -1.0
        self.ws._tick_arena_pending()
        self.ws.consume_arena_gate_open_events()   # drena os 2 do fim do preparo

        self.ws.request_arena_accept(self.team_a[1])
        gate_events = self.ws.consume_arena_gate_open_events()
        self.assertEqual([e["eid"] for e in gate_events], [self.team_a[1]])


class TestArenaModesGeneralization(unittest.TestCase):
    """Fase H (23/07/2026, pedido do usuário): "Duelo" 1x1 (soloqueue) e
    Arena 3x3 além do 2x2 existente — ver server/match_processor.py::
    ARENA_MODES."""

    def setUp(self):
        self.ws = make_world_server()

    def test_modo_invalido_e_recusado(self):
        solo = spawn_player(self.ws, "inv_solo", 130, 374)
        self.assertEqual(self.ws.request_arena_queue_join(solo, "4v4"), "invalid_mode")

    def test_1v1_e_soloqueue_sem_exigir_grupo(self):
        solo = spawn_player(self.ws, "s1v1", 130, 374)
        self.assertIsNone(self.ws.request_arena_queue_join(solo, "1v1"))
        self.assertIn(solo, self.ws._arena_queues["1v1"])

    def test_1v1_pareia_2_solos_e_usa_prefixo_de_modo_no_match_id(self):
        a = spawn_player(self.ws, "duel_a", 130, 374)
        b = spawn_player(self.ws, "duel_b", 132, 374)
        self.assertIsNone(self.ws.request_arena_queue_join(a, "1v1"))
        self.assertIsNone(self.ws.request_arena_queue_join(b, "1v1"))
        self.ws._tick_arena_queue()
        match_id = self.ws._pending_arena_invite[a]
        self.assertTrue(match_id.startswith("arena1v1_"))
        match = self.ws._active_matches[match_id]
        self.assertEqual(match["mode_id"], "1v1")
        self.assertEqual(set(match["invited_a"] + match["invited_b"]), {a, b})

    def test_1v1_aceite_spawna_1_por_lado_e_libera_1v1_de_verdade(self):
        a = spawn_player(self.ws, "duel_x", 130, 374)
        b = spawn_player(self.ws, "duel_y", 132, 374)
        self.ws.request_arena_queue_join(a, "1v1")
        self.ws.request_arena_queue_join(b, "1v1")
        self.ws._tick_arena_queue()
        self.ws.request_arena_accept(a)
        self.ws.request_arena_accept(b)
        self.assertTrue(can_engage(self.ws.world, a, b))
        outcome = apply_damage_core(self.ws.world, b, 50, killer_eid=a)
        self.assertEqual(outcome, "applied")

    def test_ja_em_fila_1v1_nao_pode_entrar_em_2v2(self):
        solo = spawn_player(self.ws, "dup_solo", 130, 374)
        self.assertIsNone(self.ws.request_arena_queue_join(solo, "1v1"))
        self.assertEqual(self.ws.request_arena_queue_join(solo, "2v2"), "already_queued")

    def test_3v3_exige_grupo_de_exatamente_3(self):
        a, b = _make_duo(self.ws, "d3v3")
        self.assertEqual(self.ws.request_arena_queue_join(a, "3v3"), "wrong_size")

    def test_3v3_pareia_2_trios_e_spawna_3_por_lado(self):
        team_a = _make_trio(self.ws, "t3a")
        team_b = _make_trio(self.ws, "t3b")
        self.assertIsNone(self.ws.request_arena_queue_join(team_a[0], "3v3"))
        self.assertIsNone(self.ws.request_arena_queue_join(team_b[0], "3v3"))
        self.ws._tick_arena_queue()
        match_id = self.ws._pending_arena_invite[team_a[0]]
        self.assertTrue(match_id.startswith("arena3v3_"))
        for eid in team_a + team_b:
            self.assertIsNone(self.ws.request_arena_accept(eid))
        match = self.ws._active_matches[match_id]
        self.assertEqual(set(match["team_a"]), set(team_a))
        self.assertEqual(set(match["team_b"]), set(team_b))
        for eid in team_a + team_b:
            self.assertTrue(can_engage(self.ws.world, team_a[0], team_b[0]))

    def test_vitoria_credita_arena_wins_no_modo_certo_nao_no_2v2(self):
        from engine.components import CharStatsTracker
        a = spawn_player(self.ws, "dw_a", 130, 374)
        b = spawn_player(self.ws, "dw_b", 132, 374)
        self.ws.request_arena_queue_join(a, "1v1")
        self.ws.request_arena_queue_join(b, "1v1")
        self.ws._tick_arena_queue()
        self.ws.request_arena_accept(a)
        self.ws.request_arena_accept(b)
        apply_damage_core(self.ws.world, b, 999999, killer_eid=a)
        cst_a = self.ws.world.get_component(a, CharStatsTracker)
        cst_b = self.ws.world.get_component(b, CharStatsTracker)
        self.assertEqual(cst_a.arena_wins["1v1"], 1)
        self.assertEqual(cst_a.arena_wins["2v2"], 0)
        self.assertEqual(cst_b.arena_losses["1v1"], 1)

    def test_fila_de_modos_diferentes_nao_interfere_entre_si(self):
        """2 solos na fila 1x1 e 1 duo na fila 2x2 ao mesmo tempo — o
        pareamento de um modo não deveria consumir/pareiar tokens do
        outro (bug potencial se _tick_arena_queue não isolasse por modo)."""
        solo_a = spawn_player(self.ws, "mix_s1", 130, 374)
        solo_b = spawn_player(self.ws, "mix_s2", 132, 374)
        duo = _make_duo(self.ws, "mix_duo")
        self.ws.request_arena_queue_join(solo_a, "1v1")
        self.ws.request_arena_queue_join(solo_b, "1v1")
        self.ws.request_arena_queue_join(duo[0], "2v2")
        self.ws._tick_arena_queue()
        self.assertEqual(len(self.ws._arena_queues["2v2"]), 1)  # duo sozinho, ninguém pareou com ele
        self.assertEqual(len(self.ws._arena_queues["1v1"]), 0)
        self.assertIn(solo_a, self.ws._pending_arena_invite)
        self.assertNotIn(duo[0], self.ws._pending_arena_invite)


if __name__ == "__main__":
    unittest.main()
