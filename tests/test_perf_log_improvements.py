"""
tests/test_perf_log_improvements.py — Fase 4.5 (11/08/2026) + Fase 4.7
(12/08/2026, ver PROBLEMAS_ARQUITETURA.md §27/§30):

1. Percentil p95/p99 por caminho no relatório periódico.
2. Breakdown ("caminho crítico") na linha de "tick lento" sai pra
   QUALQUER tick acima do budget (33ms) — dado calculado de graça pra
   qualquer tick.
3. Trace sob demanda (`_dump_perf_trace`) — dump em formato Chrome
   Trace/Perfetto só quando um tick estoura bem acima do budget
   (`_PERF_TRACE_DUMP_MS`), nunca contínuo. Mantém só os 5 mais recentes.
4. O logger "asyncio" ganha um handler em server/log.py (verificado por
   leitura de código + teste; `loop.slow_callback_duration` em
   server/main.py não tem teste automatizado — subiria o servidor real).
5. Medição separada do pré-filtro dentro de `EnemyAISystem`
   (`sys:EnemyAISystem:prefiltro`/`:loop_mobs`), injetada por
   `WorldServer._load_map_for`.

Fase 4.7 (12/08/2026) reescreveu o mecanismo de medição por trás de tudo
isso: `_perf_mark(label, t0)` (dict FLAT por nome) virou `_perf_push`/
`_perf_pop` (pilha real, chaveada por CAMINHO completo — tupla de
rótulos da raiz até a folha) — corrige a confusão de uma lista flat
misturando soma-de-pai com soma-de-filho na mesma linha (pedido
explícito do usuário). `_perf_accum`/`_perf_tick_now`/`_perf_samples`
(dicts por nome solto) viraram `_perf_tree_accum`/`_perf_tree_tick_now`/
`_perf_tree_samples` (dicts por caminho/tupla) + `_perf_total_accum`/
`_perf_total_samples` (o "TOTAL" do tick, fora da árvore).
"""
import os, sys, json, glob, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, first_ai_mob, teleport_mob_to_player, run_ticks


class TestPerfPushPopAndPercentiles(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def tearDown(self):
        try:
            self.ws._perf_log.close()
        except Exception:
            pass

    def test_perf_push_pop_grava_amostra_sob_caminho_completo(self):
        import time
        self.ws._perf_push("teste_label")
        time.sleep(0.01)
        self.ws._perf_pop()
        _path = ("teste_label",)
        self.assertIn(_path, self.ws._perf_tree_accum)
        self.assertIn(_path, self.ws._perf_tree_tick_now)
        self.assertIn(_path, self.ws._perf_tree_samples)
        self.assertEqual(len(self.ws._perf_tree_samples[_path]), 1)
        self.assertGreaterEqual(self.ws._perf_tree_accum[_path], 0.01)
        self.assertGreaterEqual(self.ws._perf_tree_tick_now[_path], 0.01)

    def test_perf_push_pop_aninhado_gera_caminho_com_pai(self):
        """Chamar push/pop aninhado (como os sistemas fazem dentro de
        ai_bundles/bnd:map_1) precisa gerar um caminho de tupla com mais
        de 1 elemento — é isso que dá hierarquia real, diferente do
        `_perf_mark` antigo que só gravava o nome solto."""
        self.ws._perf_push("pai")
        self.ws._perf_push("filho")
        self.ws._perf_pop()
        self.ws._perf_pop()
        self.assertIn(("pai",), self.ws._perf_tree_accum)
        self.assertIn(("pai", "filho"), self.ws._perf_tree_accum)

    def test_mesmo_rotulo_sob_pais_diferentes_nao_se_mistura(self):
        """Bug corrigido de brinde pela Fase 4.7: `sys:EnemyAISystem` de
        mapas diferentes não deve mais somar num único número — cada
        caminho (pai, filho) é uma chave própria."""
        self.ws._perf_push("bnd:map_1")
        self.ws._perf_push("sys:X")
        self.ws._perf_pop()
        self.ws._perf_pop()
        self.ws._perf_push("bnd:map_2")
        self.ws._perf_push("sys:X")
        self.ws._perf_pop()
        self.ws._perf_pop()
        self.assertIn(("bnd:map_1", "sys:X"), self.ws._perf_tree_accum)
        self.assertIn(("bnd:map_2", "sys:X"), self.ws._perf_tree_accum)
        self.assertNotEqual(self.ws._perf_tree_accum[("bnd:map_1", "sys:X")],
                            None)

    def test_relatorio_periodico_imprime_p95_e_p99(self):
        # Rótulo fictício com amostra conhecida — 100 valores de 1..100ms,
        # p95 e p99 têm resultado previsível pela mesma fórmula usada no
        # código real (índice = int(percentil * (n-1))).
        samples_s = [i / 1000.0 for i in range(1, 101)]  # 0.001s .. 0.100s
        _path = ("teste_fixo",)
        self.ws._perf_tree_samples[_path] = list(samples_s)
        self.ws._perf_tree_accum[_path] = sum(samples_s)
        self.ws._perf_total_accum = sum(samples_s)
        self.ws._perf_count = self.ws._PERF_REPORT_TICKS - 1

        self.ws._tick(0.01)  # completa a janela de 300 ticks, dispara o relatório
        self.ws._perf_log.flush()
        with open(self.ws._perf_log.name, encoding="utf-8") as f:
            content = f.read()

        self.assertIn("teste_fixo", content)
        linha = next(l for l in content.splitlines() if "teste_fixo" in l)
        self.assertIn("p95=", linha)
        self.assertIn("p99=", linha)
        # p95 esperado: samples[int(0.95*99)] = samples[94] = 95ms
        import re
        p95_val = float(re.search(r"p95=\s*([\d.]+)ms", linha).group(1))
        p99_val = float(re.search(r"p99=\s*([\d.]+)ms", linha).group(1))
        self.assertAlmostEqual(p95_val, 95.0, places=1)
        self.assertAlmostEqual(p99_val, 99.0, places=1)

    def test_relatorio_periodico_imprime_arvore_indentada(self):
        """Fase 4.7: caminho de profundidade 2 deve aparecer MAIS
        indentado que o pai, provando que a impressão é uma árvore de
        verdade — não a lista flat antiga onde tudo tinha a mesma
        indentação."""
        self.ws._perf_tree_accum[("pai_teste",)] = 0.05
        self.ws._perf_tree_accum[("pai_teste", "filho_teste")] = 0.03
        self.ws._perf_total_accum = 0.05
        self.ws._perf_count = self.ws._PERF_REPORT_TICKS - 1
        self.ws._tick(0.01)
        self.ws._perf_log.flush()
        with open(self.ws._perf_log.name, encoding="utf-8") as f:
            linhas = f.read().splitlines()
        linha_pai = next(l for l in linhas if "pai_teste" in l and "filho_teste" not in l)
        linha_filho = next(l for l in linhas if "filho_teste" in l)
        _indent_pai = len(linha_pai) - len(linha_pai.lstrip(" "))
        _indent_filho = len(linha_filho) - len(linha_filho.lstrip(" "))
        self.assertGreater(_indent_filho, _indent_pai,
                           "filho deveria estar mais indentado que o pai na árvore")


class TestTickLentoCaminhoCritico(unittest.TestCase):
    """Fase 4.7 — o alerta de "tick lento" agora mostra o caminho crítico
    (desce sempre pelo filho mais caro até a folha), não a lista top-8
    flat antiga."""

    def setUp(self):
        self.ws = make_world_server()

    def tearDown(self):
        try:
            self.ws._perf_log.close()
        except Exception:
            pass

    def test_top_aparece_mesmo_para_tick_so_um_pouco_acima_do_budget(self):
        self.ws._PERF_BUDGET_MS = -1.0  # qualquer tick real já excede
        self.ws._tick(0.01)
        self.ws._perf_log.flush()
        with open(self.ws._perf_log.name, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("tick lento", content)
        self.assertIn("caminho critico:", content)

    def test_caminho_critico_aponta_sistema_lento_de_verdade(self):
        """Trava real do usuário: sistema específico consumindo 100+ms
        num tick só. Simula travando `MinionSystem.update` de propósito e
        confirma que o caminho crítico aponta esse sistema."""
        import time
        orig_update = self.ws._minion_system.update

        def _slow_update(*a, **k):
            time.sleep(0.15)
            return orig_update(*a, **k)

        self.ws._minion_system.update = _slow_update
        self.ws._tick(0.05)
        self.ws._perf_log.flush()
        with open(self.ws._perf_log.name, encoding="utf-8") as f:
            content = f.read()
        slow_lines = [l for l in content.splitlines() if "tick lento" in l]
        self.assertTrue(slow_lines, "deveria ter gravado uma linha de tick lento")
        self.assertIn("minion_system(", slow_lines[-1],
                      "caminho crítico deveria apontar minion_system como consumidor no pico")

    def test_caminho_desce_ate_a_folha_quando_ha_sub_marks(self):
        """Um caminho com 2 níveis de profundidade (pai->filho) deve
        aparecer como 2 hops encadeados no caminho crítico, não só o
        nível mais alto."""
        self.ws._perf_tree_tick_now[("pai_x",)] = 0.20
        self.ws._perf_tree_tick_now[("pai_x", "filho_y")] = 0.19
        _str, _root = self.ws._perf_critical_path(200.0)
        self.assertIn("pai_x(", _str)
        self.assertIn("filho_y(", _str)
        self.assertEqual(_root, ("pai_x",))

    def test_caminho_critico_sem_dados_nao_quebra(self):
        _str, _root = self.ws._perf_critical_path(50.0)
        self.assertEqual(_str, "(nada instrumentado)")
        self.assertIsNone(_root)


class TestPerfTraceDump(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self._log_dir = os.path.dirname(self.ws._perf_log.name)
        self._before = set(glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json")))

    def tearDown(self):
        try:
            self.ws._perf_log.close()
        except Exception:
            pass
        after = set(glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json")))
        for f in after - self._before:
            try:
                os.remove(f)
            except OSError:
                pass

    def test_tick_muito_acima_do_threshold_dispara_dump(self):
        self.ws._PERF_BUDGET_MS = -1.0
        self.ws._PERF_TRACE_DUMP_MS = -1.0  # qualquer tick real já excede
        self.ws._tick(0.01)

        after = set(glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json")))
        novos = after - self._before
        self.assertEqual(len(novos), 1, "deveria ter criado exatamente 1 arquivo de trace")

        with open(next(iter(novos)), encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("traceEvents", data)
        self.assertTrue(len(data["traceEvents"]) > 0)
        ev = data["traceEvents"][0]
        self.assertEqual(ev["ph"], "X")
        self.assertIn("dur", ev)
        self.assertIn("ts", ev)
        self.assertIn("name", ev)

    def test_eventos_do_trace_usam_mesma_tid_e_ts_real(self):
        """Fase 4.7: antes cada rótulo virava um `tid` único com `ts=0`
        (barras paralelas soltas no viewer). Agora todos os eventos
        compartilham `tid=0` e usam offset REAL de início — é isso que
        deixa o Perfetto/chrome-trace aninhar visualmente por conter
        timestamp."""
        self.ws._perf_tree_tick_now = {("a",): 0.01, ("a", "b"): 0.005}
        self.ws._perf_tree_tick_start_offset = {("a",): 0.0, ("a", "b"): 0.002}
        self.ws._dump_perf_trace(50.0)
        after = set(glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json")))
        novos = list(after - self._before)
        self.assertEqual(len(novos), 1)
        with open(novos[0], encoding="utf-8") as f:
            data = json.load(f)
        _tids = {e["tid"] for e in data["traceEvents"]}
        self.assertEqual(_tids, {0}, "todos os eventos deveriam estar na mesma tid (single-thread)")
        _ev_b = next(e for e in data["traceEvents"] if e["name"] == "b")
        self.assertEqual(_ev_b["ts"], 2000, "ts deveria ser o offset real (2ms = 2000us), não 0")

    def test_tick_normal_nao_dispara_dump(self):
        """Regressão: threshold real (300ms) não dispara em tick comum
        de teste (rápido, poucos ms) — sob demanda de verdade, não
        contínuo."""
        self.ws._tick(0.01)
        after = set(glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json")))
        self.assertEqual(after, self._before)

    def test_mantem_so_5_dumps_mais_recentes(self):
        self.ws._perf_tree_tick_now = {("fake_sys",): 0.05}
        for _ in range(8):
            self.ws._dump_perf_trace(500.0)
            self.ws.tick_count += 1  # cada dump usa tick_count no nome, precisa variar

        restantes = glob.glob(os.path.join(self._log_dir, "perf_trace_tick*.json"))
        novos_desta_execucao = [f for f in restantes if f not in self._before]
        self.assertLessEqual(len(novos_desta_execucao), 5,
                             "nunca deveria manter mais de 5 dumps")


class TestCollectDeltasSubMarks(unittest.TestCase):
    """Fase 4.7 (12/08/2026) — `aoi_collect` ganhou sub-marks internos
    (`_sync_player_hp_dirty`, `_sync_player_skill_levels_dirty`,
    `_process_quest_events`, `_build_deltas_dict`), motivado pelo pico
    real de 394ms (tick#11503) que antes não tinha NENHUM breakdown por
    dentro."""

    def setUp(self):
        self.ws = make_world_server()

    def tearDown(self):
        try:
            self.ws._perf_log.close()
        except Exception:
            pass

    def test_collect_deltas_gera_sub_marks_aninhados_sob_aoi_collect(self):
        self.ws._perf_push("aoi_collect")
        self.ws._collect_deltas()
        self.ws._perf_pop()
        self.assertIn(("aoi_collect", "_sync_player_hp_dirty"), self.ws._perf_tree_accum)
        self.assertIn(("aoi_collect", "_sync_player_skill_levels_dirty"), self.ws._perf_tree_accum)
        self.assertIn(("aoi_collect", "_process_quest_events"), self.ws._perf_tree_accum)
        self.assertIn(("aoi_collect", "_build_deltas_dict"), self.ws._perf_tree_accum)

    def test_collect_deltas_ainda_retorna_deltas_normalmente(self):
        """Regressão: instrumentação nova não pode quebrar o retorno
        real usado pelo resto do tick (AOI update pro cliente)."""
        deltas = self.ws._collect_deltas()
        self.assertIn("moved", deltas)
        self.assertIn("combat", deltas)
        self.assertIn("despawned", deltas)


class TestEnemyAISystemPrefiltroBreakdown(unittest.TestCase):
    """Item 5 (12/08/2026, atualizado Fase 4.7) — medição separada do
    pré-filtro dentro de EnemyAISystem, agora via perf_push/perf_pop
    injetados (não mais perf_mark)."""

    def setUp(self):
        self.ws = make_world_server()

    def tearDown(self):
        try:
            self.ws._perf_log.close()
        except Exception:
            pass

    def test_enemy_ai_system_recebe_perf_push_pop_injetados(self):
        bundle = self.ws._map_bundles[self.ws._map_file]
        _found = [s for s in bundle.systems if type(s).__name__ == "EnemyAISystem"]
        self.assertEqual(len(_found), 1)
        self.assertIsNotNone(_found[0]._perf_push_fn,
                             "EnemyAISystem deveria ter perf_push injetado por _load_map_for")
        self.assertIsNotNone(_found[0]._perf_pop_fn)

    def test_tick_real_com_mob_gera_caminhos_prefiltro_e_loop_mobs(self):
        eid = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 50)  # SpawnZoneSystem spawna mobs ao longo de ticks, não na construção
        mob = first_ai_mob(self.ws)
        self.assertIsNotNone(mob, "precisa de pelo menos 1 mob real no mapa de teste")
        teleport_mob_to_player(self.ws, mob, eid)

        self.ws._tick(0.033)

        # Caminho completo esperado: ai_bundles -> bnd:<mapa> ->
        # sys:EnemyAISystem -> sys:EnemyAISystem:prefiltro/loop_mobs
        _prefiltro_paths = [p for p in self.ws._perf_tree_accum
                            if p[-1] == "sys:EnemyAISystem:prefiltro"]
        _loop_paths = [p for p in self.ws._perf_tree_accum
                      if p[-1] == "sys:EnemyAISystem:loop_mobs"]
        self.assertTrue(_prefiltro_paths, "deveria existir ao menos 1 caminho com prefiltro")
        self.assertTrue(_loop_paths, "deveria existir ao menos 1 caminho com loop_mobs")
        for _p in _prefiltro_paths + _loop_paths:
            self.assertEqual(_p[-2], "sys:EnemyAISystem",
                             "prefiltro/loop_mobs deveriam estar aninhados sob sys:EnemyAISystem")
            self.assertGreaterEqual(self.ws._perf_tree_accum[_p], 0.0)
        # A soma das 2 partes nunca deveria passar do total do sistema
        # inteiro (medido por fora) — senão a medição interna estaria
        # contando tempo que não existe.
        _sys_path = _prefiltro_paths[0][:-1]  # (..., "sys:EnemyAISystem")
        _total_sys = self.ws._perf_tree_accum.get(_sys_path, 0.0)
        _soma_partes = sum(self.ws._perf_tree_accum[p] for p in _prefiltro_paths + _loop_paths)
        self.assertLessEqual(_soma_partes, _total_sys + 1e-6)

    def test_sem_perf_push_pop_injetado_nao_gera_caminhos_novos(self):
        """Regressão: EnemyAISystem construído SEM perf_push/perf_pop
        (cliente offline/legado, ou teste que instancia direto) não
        deveria quebrar nem gerar caminhos — default no-op é o caminho
        seguro."""
        from engine.world_systems import EnemyAISystem
        sys_no_perf = EnemyAISystem(self.ws.world, map_filter=self.ws._map_file)
        # Não deve levantar exceção mesmo sem nenhum player/mob configurado.
        sys_no_perf.update(dt=0.033, players_by_map={}, mobs_by_map={}, tick_count=0)


class TestAsyncioLoggerWired(unittest.TestCase):

    def test_logger_asyncio_tem_handler_plugado(self):
        import server.log  # garante que o setup do módulo já rodou
        import logging
        asyncio_logger = logging.getLogger("asyncio")
        self.assertTrue(asyncio_logger.handlers,
                        "logger 'asyncio' deveria ter pelo menos 1 handler — "
                        "antes ficava mudo, avisos de callback lento eram descartados")
        self.assertFalse(asyncio_logger.propagate)


if __name__ == "__main__":
    unittest.main()
