# Análise de performance — Rodada 1 (05/09/2026)

> Documento de trabalho pra comparação entre 2 rodadas independentes de
> auditoria multi-agente sobre os sistemas mais custosos do servidor.
> Não é documentação final do projeto — isso vai pra
> `PROBLEMAS_ARQUITETURA.md` só depois de comparado com a Rodada 2.

## Contexto

Depois do incidente real de travamento (teste de carga com 300 bots,
ver `PROBLEMAS_ARQUITETURA.md` §113) e das 2 correções já aplicadas
(`gc.freeze()` + dedup do prefiltro de IA por célula), o usuário pediu
uma investigação mais profunda pra achar os PRÓXIMOS problemas de
performance, escalando 3 agentes especialistas em paralelo, cada um com
uma lente diferente. Metodologia da Rodada 1: cada agente recebeu o
MESMO top-10 (construído por mim, agregando ~2500 linhas de log reais
de 2 testes de carga) e teve liberdade TOTAL pra analisar o projeto
inteiro e montar sua própria priorização.

## Top 10 (dados brutos, agregados de `server_perf_bkp.log` +
`server_perf.log`, soma de tempo toda vez que o rótulo aparece — seja
como líder do caminho crítico OU como "outro ramo" secundário)

| # | Sistema | Tempo total (ms) | Ocorrências | Média (ms) | Pico (ms) |
|---|---|---|---|---|---|
| 1 | `index_build` | 5578.9 | 1271 | 4.39 | 66.70 |
| 2 | `bush_target_clear` | 5048.2 | 1274 | 3.96 | 129.00 |
| 3 | `sys:EnemyAISystem:prefiltro` | 4278.7 | 355 | 12.05 | 152.90 |
| 4 | `sys:EnemyAISystem:loop_mobs` | 3069.1 | 392 | 7.83 | 35.00 |
| 5 | `global_systems` | 2234.9 | 1018 | 2.20 | 32.60 |
| 6 | `aoi_collect` | 2119.0 | 761 | 2.78 | 64.30 |
| 7 | `pre_tick_snapshots` | 2105.3 | 482 | 4.37 | 138.50 |
| 8 | `skill_requests` | 2043.7 | 334 | 6.12 | 69.20 |
| 9 | `post_tick_bookkeeping` | 1998.5 | 566 | 3.53 | 54.30 |
| 10 | `regen_and_status_ticks` | 1417.8 | 270 | 5.25 | 149.40 |

**Ressalva de metodologia (achada pelo agente de QA na própria Rodada
1, confirmada por leitura direta do log)**: essa soma mistura "líder de
verdade do caminho crítico" com "apareceu como ramo secundário" — os
primeiros 7 itens rodam incondicionalmente todo tick (fazem parte do
loop principal sem gate de proximidade), então aparecem em quase toda
linha só por "estar lá", e boa parte do tempo somado é jitter externo
(GC/SO) emprestando o nome de sistemas que, sozinhos, seriam baratos.
Só `EnemyAISystem:prefiltro`/`loop_mobs` têm atribuição 100% honesta
(só entram na cadeia quando são de fato o gargalo). Prova direta: um
cluster de 8 ticks lentos seguidos (`tick#92541-92607`, log antigo,
`players=1 mobs=202 ativos=0` — mundo praticamente ocioso) tem um
"culpado" DIFERENTE a cada tick (`bush_target_clear`, `TileValidationSystem`,
`pre_tick_snapshots`, `EnemyAISystem:prefiltro`, `minion_system`...),
cada um com custo de 3 dígitos — assinatura de pausa externa, não de 8
sistemas ficando lentos por coincidência ao mesmo tempo.

Também achado (mesmo agente): `global_systems:tile_movement` (rótulo
FILHO mais específico, não incluído no top-10 acima por ter ficado
abaixo do corte de 10) teve um pico real de **81.90ms** com média de
**31.85ms** em só 27 ocorrências — muito mais concentrado/severo por
ocorrência que o pai `global_systems` (2.20ms médio). Não é erro na
tabela, é rótulo diferente (mais específico) que ficou de fora do corte.

## Achados por agente (Rodada 1)

### Agente 1 — Arquiteto MMORPG tile-based (ACEITO nota 10, verificado)

Leu toda a documentação do projeto antes de julgar. Vereditos por
sistema:

- **Correto** (sem incongruência de gênero): `index_build`, `global_systems`,
  `aoi_collect`.
- **Incongruente**: `bush_target_clear` (não reusa `_tile_movement_by_map`
  já construído no mesmo tick), `sys:EnemyAISystem:loop_mobs` (máquina de
  estado monolítica, sem decomposição por domínio — decisão/movimento/
  combate fundidos), `pre_tick_snapshots` (2ª passada completa por
  `TileMovement` no mesmo tick que `index_build` já faz), `skill_requests`
  (`_combat_targets` escaneia `self._mob_eids`/`self._player_eids`
  GLOBAIS — todos os mapas/instâncias — mesmo quando o cast é numa
  arena/BG isolada; eixo de escala real pro cenário normal do jogo,
  PvE+Arena+BG simultâneos), `post_tick_bookkeeping` (3ª passada
  completa por `TileMovement` no mesmo tick, sub-bloco de "combatente
  novo").
- **`sys:EnemyAISystem:prefiltro`**: técnica correta (já é o "piso" de
  §29/§38), mas dado NOVO — a escala medida hoje (267 players) já
  excede o benchmark que validou a técnica (100 players). Recomenda
  REABRIR a conversa sobre redesenho maior (Grid/Cell estilo
  AzerothCore) com o usuário, sem decidir sozinho.
- **`regen_and_status_ticks`**: bucket procedural sem decomposição por
  domínio (8 sub-sistemas colados); 1 sub-bloco real (regen de mob fora
  de combate) escaneia TODOS os mobs do mundo mesmo em mapas sem
  nenhum player, quebrando a mesma disciplina que `EnemyAISystem` já
  aplica corretamente.

**Priorização do agente**: 1º `bush_target_clear` (ganho grande, risco
baixíssimo) → 2º consolidar as 4 passadas redundantes de `TileMovement`
num esforço só → 3º `skill_requests`/`_combat_targets` (escopo de
instância, eixo de escala real do produto).

### Agente 2 — Engenheiro gamedev, auditoria de incongruências (ACEITO nota 10, verificado)

Achado mais grave da Rodada 1: **`bush_target_clear` tem um BUG real,
não só ineficiência**. `self._bush_zones_by_map[key] = spawn_points.get("bush_zones", [])`
(`server/world_server.py:779`) grava uma entrada por MAPA mesmo com
lista vazia — então `self._bush_zones_by_map` nunca fica vazio depois
do boot, e o guard `if not self._bush_zones_by_map: return`
(`server/world_server.py:1841`) NUNCA dispara em produção. Confirmado:
`_get_bush_zone` sempre recebe lista vazia (`zones = ... .get(map_file, [])`,
`server/bush_zone_processor.py:31`) e sempre retorna `None`
(`if not zones: return None`, linha 32-33). **A função roda 100% do
tempo pra produzir 100% de resultado vazio, sempre.** Fix de 1 linha:
`if not any(self._bush_zones_by_map.values()): return`.

Segundo achado grave: `eid not in self._player_eids.values()`
(`server/world_server.py:5619`, `:5663`, `:6077`) é O(n) por checagem —
Python não indexa `dict.values()` — em vez de O(1). Único bug que
PIORA sozinho com mais jogadores online, sem precisar de cenário raro.

Terceiro achado: confirma a mesma redundância de scans de
`TileMovement` que o Agente 1 achou (via ângulo de arquitetura) — aqui
com prova de COMPLEXIDADE (não só estilo): `pre_tick_snapshots` (linha
5325), `index_build` (linha 5415) e `post_tick_bookkeeping` (linha
6075) fazem a MESMA query no mesmo tick.

`sys:EnemyAISystem:prefiltro`: confirma que o crivo de distância exata
por jogador é uma limitação ESTRUTURAL correta da técnica (2 players na
mesma célula podem ter distâncias reais diferentes ao mesmo candidato),
não um bug — já documentado no próprio código e no
`PROBLEMAS_ARQUITETURA.md`.

**Ranking do agente por gravidade real** (não por custo total do log):
1º `bush_target_clear` (bug estrutural, 100% desperdício) → 2º
`eid not in dict.values()` em `regen_and_status_ticks`/`post_tick_bookkeeping`
(único que piora com escala de jogadores) → 3º as 3 passadas
redundantes de `TileMovement`.

### Agente 3 — QA de performance (ACEITO nota 10, após 1 correção)

Contribuição principal: **desafiou a própria metodologia da tabela**
(ver ressalva acima) — mediu quantas vezes cada rótulo REALMENTE
liderou o caminho crítico vs. quantas vezes só apareceu de raspão, e
achou que 7 dos 10 itens têm atribuição majoritariamente espúria
(3-9% de liderança real), enquanto `EnemyAISystem:prefiltro`/`loop_mobs`
têm 100%.

Avaliação cética das 2 correções já aplicadas hoje:
- **`gc.freeze()`**: evidência FORTE — comparação isolada em
  `players=0` (elimina a diferença de escala entre os 2 logs), duração
  média caiu de 28.77ms→2.68ms (10.7x), pico de 153.3ms→6.8ms (22.5x),
  contagem de pausas >50ms caiu de 17→0.
- **Dedup do prefiltro por célula**: evidência FRACA — comparando a
  MESMA condição (`players=51`) entre os 2 logs, o custo do prefiltro é
  estatisticamente IDÊNTICO antes/depois (ligeiramente pior no log
  novo, dentro do ruído). Não prova que a correção seja inútil — só que
  o teste de hoje não clusterizou os bots na MESMA célula (raio de
  20 tiles), condição necessária pra exercitar o ganho. Reprodução
  recomendada: 50+ bots parados literalmente no MESMO tile/área pequena
  (não espalhados pelo mapa como no teste de hoje).

Sobre o incidente §113 (travamento de 3-4 minutos): **nenhum dos 10
sistemas**, nem no pior pico isolado (~150ms), chega perto de explicar
MINUTOS de travamento — confirma que a causa real documentada
(contenção de escrita SQLite + tempestade de reconexão) é um mecanismo
inteiramente separado, não relacionado a esta lista.

**Top 3 do agente por impacto real no jogador** (diferente da ordem da
tabela): 1º `gc.collect()` manual (nem estava no top-10 — só aparece
como evento isolado fora do breakdown por tick; JÁ corrigido hoje,
evidência forte) → 2º `skill_requests` (único item com pico
correlacionado a carga real de combate, 241+ players, sustentado por
múltiplos ticks) → 3º `EnemyAISystem:prefiltro` (único item de IA com
atribuição honesta e escala real, mesmo sem prova de ganho ainda).

**Correção pós-feedback**: o agente inicialmente errou uma comparação
(achou que a tabela reportava pico errado pro `bush_target_clear` —
não reportava, o erro era dele comparando contra a linha errada da
tabela). Corrigido após 1 rodada de revisão. Na auto-checagem pedida,
achou 1 discrepância REAL adicional: `global_systems:tile_movement`
(rótulo filho, pico 81.90ms) não estava no top-10 por ter ficado
abaixo do corte — não é erro, é rótulo mais específico que o pai
`global_systems` (pico 32.60ms na tabela).

## Divergência entre agentes a resolver na Rodada 2

O Agente 1 (arquiteto) afirma que `_combat_targets`/`_snapshot_combat_targets`
(chamado por `_process_skill_requests`) escaneia `self._mob_eids`/
`self._player_eids` GLOBAIS (todos os mapas/instâncias) em vez de
escopar pelo mapa/instância do caster — uma incongruência real de
escopo. O Agente 2 (engenheiro), auditando o MESMO rótulo
(`skill_requests`), descreveu a complexidade como O(R) por request×O(1)
por checagem, **sem mencionar `_combat_targets` nem seu escopo global**
— não confirma nem contradiz explicitamente a alegação do Agente 1.
**Não sabemos se o Agente 2 simplesmente não aprofundou até essa
chamada específica, ou se a alegação do Agente 1 está incorreta.**
Precisa de verificação direta na Rodada 2.

## Minha priorização consolidada (rodada 1)

1. **`bush_target_clear`** — guard quebrado, bug de corretude/desperdício
   confirmado por 2 agentes independentes com evidência de código
   idêntica. Risco de correção: mínimo.
2. **`eid not in self._player_eids.values()`** (3 call-sites) — único
   bug que escala sozinho com jogadores online. Risco de correção:
   mínimo.
3. **Consolidar as 4 passadas redundantes de `get_entities_with(TileMovement)`**
   no mesmo tick (`index_build`, `pre_tick_snapshots`,
   `post_tick_bookkeeping`, e o scan interno de `bush_target_clear` já
   coberto no item 1) — achado por 2 agentes independentes
   (arquitetura E engenharia), mesmo padrão de correção já usado 3x
   nesta sessão (§31/§34/§36). Risco de correção: baixo.
4. **`skill_requests`/`_combat_targets`** (se a divergência da Rodada 2
   confirmar o achado do Agente 1) — escopar por mapa/instância em vez
   de varrer globalmente. Risco de correção: baixo-médio (precisa
   confirmar que nenhum caminho legítimo depende do escopo global).
5. **Redesenho maior do `EnemyAISystem` (Grid/Cell)** — sinalizado pelo
   Agente 1 como possivelmente necessário pra escala de lançamento,
   mas EXPLICITAMENTE deixado como decisão do usuário, não uma tarefa
   de código imediata. Recomendo revisitar DEPOIS dos itens 1-4, com
   teste de carga corretamente clusterizado (recomendação do Agente 3)
   pra saber se ainda é necessário.

## Não investigado / fora do escopo desta rodada

- `_sync_player_skill_levels_dirty` (aloca tupla nova por jogador todo
  tick antes de checar se algo mudou) — achado do Agente 2 como
  ineficiência linear, baixo ganho relativo, não entrou no top 3 de
  ninguém.
- `global_systems:tile_movement` como item PRÓPRIO (só apareceu como
  nota lateral do Agente 3) — pico de 81.90ms/31.85ms médio merece
  checagem própria, não coberta em profundidade nesta rodada.

---

# Rodada 2 (05/09/2026, mesmo dia) — validação escopada

Metodologia diferente de propósito: em vez de cada agente montar sua
própria priorização do zero, EU construí uma lista de 5 prioridades a
partir da Rodada 1 (ver acima) e dei aos 3 agentes tarefas ESCOPADAS —
validar diagnóstico + fix proposto (arquiteto), achar risco de
implementação (engenheiro), desenhar verificação/teste (QA) — cada um
resolvendo de forma independente a divergência não fechada da Rodada 1
(`_combat_targets` escaneia globalmente?).

## Resultado da comparação: a conclusão MUDOU em 3 dos 5 itens

| Item | Rodada 1 | Rodada 2 | Mudou? |
|---|---|---|---|
| 1. `bush_target_clear` | guard quebrado, fix de 1 linha | confirmado, fix aprovado sem ressalva | **Não** — validado tal como estava |
| 2. `eid not in dict.values()` | trocar por `set` compartilhado | (a) existe fix mais idiomático (checar `PlayerControlled` via componente, sem cache novo); (b) o fix de `set` como descrito tem BUG real — um bot de BG spawnado por `_tick_bg_queue` no MEIO do tick (entre `index_build` e `post_tick_bookkeeping`) ficaria de fora de um set construído cedo, sendo registrado como MOB por engano | **Sim** — fix original tinha um bug não percebido |
| 3. Fundir as 3 varreduras de `TileMovement` | fundir as 3 numa passada só | só 2 das 3 podem ser fundidas (`pre_tick_snapshots`+`index_build`) — a 3ª (`post_tick_bookkeeping`) tem 2 motivos estruturais pra rodar separada e depois: `SpawnZoneSystem` (dentro de `ai_bundles`, que roda DEPOIS de `index_build`) cria mobs no mesmo tick que essa varredura precisa detectar; e a detecção de "mob começou a mover" compara um snapshot do INÍCIO do tick contra o estado do FIM — não é fundível em 1 única passada mesmo em teoria | **Sim** — fundir as 3 como proposto quebraria funcionalidade real (confirmado em código pelos 2 agentes independentemente) |
| 4. `_combat_targets` escopo global | divergência não resolvida entre 2 agentes | CONFIRMADO por 2 agentes independentes: escaneia `_mob_eids`/`_player_eids` globais, sem filtro de mapa/instância. Escalada de gravidade: não é só performance — `spell_completion_processor.py:1065-1072` usa coordenada de tile CRUA (sem comparar mapa) pro alcance da AoE de gelo, então em tese um alvo de OUTRO mapa/instância na mesma coordenada numérica pode receber dano cruzado. Fix como descrito na Rodada 1 também estava incompleto (`_combatants_by_map` é variável LOCAL de `_tick()`, precisa virar atributo persistente pra `_combat_targets` conseguir usar) | **Sim** — de "acho que é só performance" pra "é bug de corretude/possível exploit, prioridade sobe" |
| 5. `global_systems:tile_movement` | não investigado a fundo | hipótese fundamentada (não definitiva): custo real, ligado ao fallback de resolução de elevação (até 24 leituras de `object_matrix` por entidade que chega num tile), correlacionado a quantas entidades terminam um passo no MESMO tick — não jitter externo, mas precisa de correlação real (contagem de chegadas por tick) pra confirmar | **Mudou de "sem dado" pra "hipótese testável com plano concreto"** |

## Conclusão da comparação

A Rodada 1 (agentes com liberdade total de análise e priorização)
achou os PROBLEMAS certos — nenhum dos 5 itens foi invalidado, todos
continuam reais. Mas 3 das 5 CORREÇÕES PROPOSTAS por mim (baseadas na
Rodada 1) tinham problema: 1 tinha alternativa melhor sem estado novo,
1 causaria regressão real se implementada como descrita, e 1 estava
incompleta (faltava um passo) além de ter a gravidade subestimada. A
Rodada 2, mais restrita e focada em VALIDAR IMPLEMENTAÇÃO em vez de
achar problema, pegou exatamente o tipo de erro que só aparece quando
se pergunta "como eu implementaria isso de verdade" — validando a
premissa do usuário de rodar 2 vezes com metodologias diferentes.

## Plano de execução final (pós-comparação)

1. **`bush_target_clear`** (`server/world_server.py:1841`) — `if not any(self._bush_zones_by_map.values()): return`. Sem mudança de plano.
2. **`eid not in self._player_eids.values()`** (3 call-sites) — usar `world.get_component(eid, PlayerControlled) is not None` (padrão já usado no arquivo), NÃO um `set` compartilhado entre os 3 pontos.
3. **Fundir só `pre_tick_snapshots` + `index_build`** (2 de 3) — preservar a captura de `player_hp_snap`/`pre_mob_target` ANTES do `continue` por falta de `Position`/`CombatStats`. `post_tick_bookkeeping` fica como está por enquanto (otimização separada, maior escopo — registrar `_spawned_this_tick` na origem, no `SpawnZoneSystem`, é a sugestão do arquiteto pra uma 2ª fase).
4. **`_combat_targets`** — escopar por mapa/instância via `get_entity_map(exclude_eid)` + persistir `_combatants_by_map` como atributo (`self._combatants_by_map`) ao fim de `index_build`. Confirmar antes que todo player tem `Combatant` sempre (invariante a validar, não assumir).
5. **`global_systems:tile_movement`** — não corrigir ainda; rodar o teste de correlação desenhado pela QA (contagem de chegadas de tile por tick vs. duração do sistema) antes de decidir se há algo pra otimizar.

Teste único de carga (desenhado pela QA, cobre os 5 itens): 250 bots,
rampa `--batch-size 5 --batch-interval-s 10`, ratio `230:10:10`
(mundo:arena:bg), bots espalhados pelos 3 mapas de mundo (checar que
as contas de teste realmente têm personagem em mapa diferente — o
provisionamento padrão não varia isso), duração ≥5 min pós-rampa, mais
1 sub-rodada com mundo pequeno (`--count 30`, mesmo ratio proporcional)
pra provar especificamente que o fix do item 4 não escala mais com o
tamanho do mundo, e 1 sub-rodada com bots IDLE (sem cast/movimento)
pra isolar o ganho do item 2/3 sem o ruído de projétil/movimento no
mesmo `post_tick_bookkeeping`.
