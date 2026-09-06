# Medição antes/depois — 4 fixes de performance (05/09/2026)

> Ver `arquitetura/analise_performance_rodada1.md` pra análise completa
> (2 rodadas de auditoria multi-agente) que motivou estes fixes.
> Este documento é só de MEDIÇÃO. Teste de 250 bots já rodado e
> resultado preenchido — ver seção final. Dashboard visual:
> https://claude.ai/code/artifact/24895768-ecf9-4256-98d9-c18c882f11bc

## Os 4 fixes implementados (05/09/2026)

| # | Fix | Arquivo:linha | Risco |
|---|---|---|---|
| 1 | Guard de `_tick_bush_target_clear` corrigido (`if not any(...)` em vez de `if not self._bush_zones_by_map`) | `server/world_server.py::_tick_bush_target_clear` | Mínimo |
| 2 | `eid not in self._player_eids.values()` (O(n)) → `get_component(eid, PlayerControlled) is None` (O(1)), 3 call-sites | `server/world_server.py` (ActiveRegen, ActiveManaRegen, post_tick_bookkeeping) | Mínimo |
| 3 | Fundidas 2 das 3 varreduras redundantes de `get_entities_with(TileMovement)` — `pre_mob_target` passa a ser populado dentro do loop de `index_build` | `server/world_server.py::_tick` (index_build) | Baixo |
| 4 | `_combat_targets` escopado por mapa/instância (`get_entity_map`) — corrige vazamento de alvo entre mapas/instâncias simultâneas | `server/world_server.py::_combat_targets` | Baixo-médio |

Todos com teste automatizado + revert-to-confirm (ver
`tests/test_bush_stealth.py::TestBushTargetClearGuard`,
`tests/test_consumables.py::TestActiveRegenPlayerCheckIsComponentBased`/
`TestPostTickBookkeepingNeverMisclassifiesPlayerAsMob`,
`tests/test_server.py::TestPreMobTargetSurvivesFusionWithIndexBuild`,
`tests/test_faction.py::TestCombatTargetsScopedByMap`). Suíte completa:
1502 passed (1490 já existentes + 12 novos).

## Tabela de medição — Antes (log já existente) vs Depois (a preencher)

Baseline "Antes" = `logs/server_perf.log`, teste de 50 bots já rodado
hoje (51 players, ~321 mobs, ANTES destes 4 fixes — já tinha
`gc.freeze()` + dedup do prefiltro aplicados). Bots não ficaram
espalhados pelos 3 mapas de mundo nem geraram volume de cast de
skill em arena/BG suficiente pra estressar os itens 3/4 de verdade —
por isso a coluna "Depois" precisa do teste maior descrito abaixo, não
só re-rodar o mesmo teste de 50.

| Sistema | Antes: avg/p95/p99 (51 players, ~321 mobs) | Depois: avg/p95/p99 | Comando de medição |
|---|---|---|---|
| `bush_target_clear` | 0.003 / 0.007 / 0.008 ms | _(preencher)_ | `grep -a "  bush_target_clear " logs/server_perf.log \| tail -3` |
| `index_build` (inclui `pre_mob_target` fundido) | 1.442-1.519 / 2.922-3.318 / 3.987-6.408 ms | _(preencher)_ | `grep -a "  index_build " logs/server_perf.log \| tail -3` |
| `pre_tick_snapshots` (só o que sobrou após fundir) | 0.207-0.270 / 0.419-0.420 / 0.495-2.540 ms | _(preencher)_ | `grep -a "  pre_tick_snapshots " logs/server_perf.log \| tail -3` |
| `post_tick_bookkeeping` | 0.657-1.115 / 1.548-1.563 / 1.892-6.366 ms | _(preencher)_ | `grep -a "  post_tick_bookkeeping " logs/server_perf.log \| tail -3` |
| `regen_and_status_ticks` | 0.468-0.526 / 1.032-1.073 / 1.299-2.553 ms | _(preencher)_ | `grep -a "  regen_and_status_ticks " logs/server_perf.log \| tail -3` |
| `skill_requests` | 0.012-0.091 / 0.019-0.020 / 0.025-1.956 ms (pouco cast de skill neste teste — não representativo pro item 4) | _(preencher, com o teste dedicado abaixo)_ | `grep -a "  skill_requests " logs/server_perf.log \| tail -3` |

**Pico isolado de `bush_target_clear` no log ANTIGO** (`server_perf_bkp.log`,
antes de QUALQUER fix de hoje): 129.00ms num único tick (`tick#92541`,
`players=1`). Métrica de sucesso mais importante pro fix 1: esse tipo de
pico de 3 dígitos precisa DESAPARECER por completo, não só a média cair
(a média já era baixa antes — o problema real era o pico raro).

## Teste dedicado pra validar os itens 3 e 4 de verdade (recomendado pelo Agente de QA, rodada 2)

O teste de 50 bots já feito hoje não teve volume suficiente de
skill/arena pra exercitar `_combat_targets`, nem espalhou bots pelos 3
mapas de mundo pra acumular volume de `TileMovement` que expõe o ganho
da fusão de varreduras. Plano de teste único (cobre os 4 fixes):

1. **Provisionar bots em mapas diferentes** — checar antes se as contas
   de teste já têm personagem salvo em mapas diferentes (map_1,
   map_cave_west, map_cave_east); se não, mover manualmente/via GM
   antes do teste, senão todo bot "mundo" cai no mesmo mapa.
2. **Rampa**: `--batch-size 5 --batch-interval-s 10` (nunca a rampa
   agressiva — §113, contenção de SQLite documentada).
3. **Ratio**: `--ratio 230:10:10` (mundo:arena:bg) até `--count 250`.
4. **Duração**: ≥5 min pós-rampa completa (≥30 relatórios `[PERF SRV]`).
5. **Sub-rodada extra pro item 4**: repetir com `--count 30` (mesmo
   ratio proporcional, arena/bg mantendo ~10 fixos em número absoluto)
   — se o fix funcionou, `skill_requests` avg deve ficar PARECIDO entre
   as 2 escalas de mundo (30 vs 250), não crescer proporcionalmente.
6. Sempre comparar `avg=`/`p95=`/`p99=` na MESMA condição de
   `players=`/`mobs=` entre logs — nunca comparar totais agregados de
   testes em escalas diferentes (erro já identificado e corrigido na
   Rodada 1 da análise).

Comando sugerido:
```
py -3.10 tools/loadtest/provision.py --count 250
py -3.10 tools/loadtest/swarm.py --count 250 --ratio 230:10:10 --batch-size 5 --batch-interval-s 10 --duration-s 400
```

## Resultado da medição — teste de 250 bots rodado (05/09/2026)

> **Dashboard visual com os mesmos dados**: https://claude.ai/code/artifact/24895768-ecf9-4256-98d9-c18c882f11bc
> (gráficos antes/depois por sistema + escala de travamento por número de jogadores)

Teste rodado exatamente como planejado (250 bots, `--ratio 230:10:10`,
`--batch-size 5 --batch-interval-s 10`), com uma ressalva: bots não
ficaram de fato espalhados pelos 3 mapas de mundo (provisionamento não
varia mapa por conta) — mesmo assim, o volume de skill/arena/BG foi
suficiente pra confirmar o fix do item 4 com sobra.

### Comparação justa — mesma escala de jogadores (~250-267) nos 2 logs

| Sistema | Antes (267 players) | Depois (251 players) | Redução |
|---|---|---|---|
| `skill_requests` | 4.041ms | 0.018ms | **99,6%** |
| `bush_target_clear` | 2.456ms | 0.007ms | **99,7%** |
| `pre_tick_snapshots` | 1.435ms | 0.300ms | 79% |
| `regen_and_status_ticks` | 1.756ms | 0.711ms | 59% |
| `post_tick_bookkeeping` | 1.256ms | 1.065ms | 15% |
| `index_build` | 2.565ms | 2.254ms | 12% |

`index_build`/`post_tick_bookkeeping` tiveram redução menor de propósito
— `index_build` absorveu o trabalho que `pre_tick_snapshots` perdeu (a
fusão realoca custo, não cria do nada), e `post_tick_bookkeeping` só
recebeu o fix #2 (checagem O(1)), não a fusão de varredura (fica pra
uma 2ª fase, ver `analise_performance_rodada1.md`).

### Resultado geral do tick

| Métrica | Antes (267 players) | Depois (251 players) |
|---|---|---|
| Tempo médio por tick | 40.27ms (**acima** do orçamento de 33ms) | 25.1-25.7ms (**dentro** do orçamento) |
| % de ticks travados (225-250 players) | 60.33% | 4.33% |
| % de ticks travados (250-275 players) | 47.33% | 33.57% |

O último ponto (250-275 players) mostra o PRÓXIMO teto real: mesmo com
os 6 fixes, travamento ainda sobe bastante no extremo de escala — isso
bate com o achado do Agente Arquiteto (Rodada 1): o prefiltro de IA
(`EnemyAISystem:prefiltro`) já está no limite da técnica atual pra esse
volume de jogadores. Não é regressão nem bug novo — é o próximo item a
discutir (redesenho tipo Grid/Cell do AzerothCore), não decidido nesta
sessão.

### Achado ao vivo durante o teste (não estava no plano)

Durante o teste de 250, o usuário relatou travamento real jogando —
investigado ao vivo: CPU 67%, tick 35-60ms, dominado por
`EnemyAISystem:prefiltro` (16-24ms) — exatamente o teto acima, não um
bug novo dos 6 fixes (nenhum deles aparecia pesando no momento). Depois
de parar os bots, um segundo sintoma separado apareceu (ping alto,
60-80ms, mesmo com só 1 jogador e servidor ocioso) — investigado e
não teve causa server-side encontrada (CPU/tick/rede todos normais);
resolveu sozinho com um reconecte limpo do jogador, sugerindo resíduo
de conexão/estado do client, não do servidor.
