# Teste de carga com bots reais (`tools/loadtest/`)

Enxame de bots que conectam via WebSocket de verdade e falam o
protocolo real do jogo (`shared/messages.py`) — substitui o teste
antigo (nunca commitado) que injetava estado direto no ECS sem passar
pela rede (ver `arquitetura/PROBLEMAS_ARQUITETURA.md` §31-38 pro
histórico).

## Pré-requisitos

- Servidor já rodando (`python server/main.py`).
- Rodar da raiz do projeto (`py -3.10 tools/loadtest/...`).

## Uso

1. **Provisionar as contas de bot** (1x só — idempotente, pode rodar de
   novo sem duplicar nada):

   ```
   py -3.10 tools/loadtest/provision.py --count 300
   ```

   Cria (ou confirma já existentes) 300 contas+personagens
   (`loadbot0000`..`loadbot0299`, classes guerreiro/mago/arqueiro em
   round-robin), libera todas as skills treináveis de cada classe, e
   equipa cada arqueiro com arco+aljava de verdade (`hunter_bow`+
   `battleground_quiver`, 999 flechas — 08/09/2026, ver
   `PROBLEMAS_ARQUITETURA.md` §125). Sem arco/aljava equipados o
   servidor bloqueia o auto-attack do arqueiro por completo.

2. **Subir o enxame**:

   ```
   py -3.10 tools/loadtest/swarm.py --count 300 --duration-s 600
   ```

   Sem `--duration-s`, roda até Ctrl+C (cada bot manda `LOGOUT` antes
   de fechar). `--ratio "68:12:20"` (default) controla a proporção
   mundo aberto / fila de arena / fila de BG.

3. **Jogador real**: logue com o client normal (`python main.py`) de
   **outra máquina** na mesma rede, apontando pro IP do servidor — não
   precisa de nenhum passo extra, o servidor já aceita `--host 0.0.0.0`
   hoje. Bots nunca miram no jogador real (só em mobs e, dentro de
   arena/BG, uns nos outros).

## Aliados de treino (`aliados_treino.py`)

3 jogadores parados perto dos bonecos de treino pra testar cura/buff de
suporte (contas JÁ existentes `loadbot0000/0003/0006`, nunca cria conta).
Os 2 primeiros duelam em ciclos (duelo nunca mata — fica 1 de vida) pra
ficarem machucados; `--sem-duelo` deixa todos parados.

```
py -3.10 tools/loadtest/aliados_treino.py --host 5.161.104.174 --tls
```

## Opções (`swarm.py`)

| Flag | Default | Descrição |
|---|---|---|
| `--count` | 300 | quantidade de bots |
| `--host` / `--port` | localhost / 8765 | endereço do servidor |
| `--ratio` | `68:12:20` | proporção mundo:arena:bg |
| `--batch-size` / `--batch-interval-s` | 8 / 1.0 | rampa de conexão (evita logar todo mundo no mesmo tick) |
| `--duration-s` | (nenhum) | encerra sozinho após N segundos |
| `--seed` | 42 | RNG determinística por bot (`seed + índice`) |
| `--think-min-s` / `--think-max-s` | 0.6 / 1.8 | intervalo aleatório entre decisões de cada bot |
| `--no-requeue` | desligado | bot de arena/BG não entra na fila de novo depois de uma partida |

## Comportamento do bot (`bot.py`)

**Movimento e decisão têm cadências SEPARADAS** (08/09/2026, ver
`PROBLEMAS_ARQUITETURA.md` §125) — `_move_loop` manda 1 `MOVE` a cada
~0,29s (mesma velocidade de fallback de um jogador real,
`PLAYER_SPEED_TILES_FALLBACK`), independente do `_think_loop` (que
continua decidindo a cada 0,6-1,8s, via `--think-min-s`/`--think-max-s`).
Antes os dois eram a mesma cadência — bot andava 3-9x mais devagar que
um jogador real, e o hiato longo entre passos parecia teleporte
(nenhum `MOVE` real pula mais de 1 tile).

**IA de combate por classe** (08/09/2026, ver `PROBLEMAS_ARQUITETURA.md`
§125) — `_priority_skill()` substitui a escolha aleatória de skill por
prioridade tática: guerreiro usa Interceptar ao entrar em alcance
(1<dist≤6) e Vitória Iminente sempre que pronta com HP<90% (é uma
carga ganha ao MATAR um alvo, não cura garantida ao usar); mago usa
Bola de Fogo como padrão à distância e troca pra Bloco de
Gelo/Polimorfia com HP≤30%; arqueiro mantém distância segura
(`_ARCHER_SAFE_RANGE_TILES`, abaixo do alcance real do arco) e usa
Picada de Escorpião/Tiro Repulsivo pra abrir espaço. **Kite real com
histerese** pras 3 classes (`_kiting` liga a HP≤30%, só desliga a
HP≥50% — evita oscilar fugir/lutar bem na borda de um threshold só):
mago/arqueiro recuam mantendo distância enquanto ainda atacam;
guerreiro também foge agora (sem skill de escape, só corre mesmo) e,
enquanto fugindo, não aceita alvo novo até a histerese desligar.

Além disso, o bot (05-06/09/2026, ver `PROBLEMAS_ARQUITETURA.md` §115)
também:

- **Saqueia loot** de verdade após matar mob (`LOOT_REQUEST {take:
  "all"}`, sem checagem de distância — o próprio jogo não valida isso).
- **Usa skill com alcance real** — skill com `cast_range` no catálogo
  (ex.: Bola de Fogo=6) pode disparar sem precisar chegar adjacente;
  auto-attack continua exigindo adjacência.
- **Confirma acerto de projétil** (`PROJECTILE_HIT_CS`, 06/09/2026) —
  Bola de Fogo/Flecha Reiterada/Picada de Escorpião só aplicam dano no
  servidor depois dessa confirmação (o cast por si só só consome mana/
  cooldown). Um cliente real manda isso quando o projétil visual
  "chega" no alvo; o bot manda a mesma mensagem depois do tempo de voo
  calculado (mesma tabela de velocidade do servidor,
  `_BOT_PROJECTILE_SPEED_PX_S`, importada direto — nunca reinventada).
  Sem isso o mago/arqueiro "atirava" pra sempre sem nunca causar dano.
- **Faz excursões de longa distância** com pathfinding REAL (A* real,
  mesmo código do client, `tools/loadtest/map_data.py` — nunca uma
  invenção pro bot): ocasionalmente vai até um ponto de interesse real
  do mapa (zona de spawn de mob), até um tile de arbusto real (testa
  stealth), ou até um mob visto de longe (raio de 30 tiles) mas fora do
  raio de aggro curto — o mais parecido com "clicar com o direito num
  alvo distante" que dá pra fazer sem client gráfico. Cálculo de rota
  roda numa thread própria, nunca bloqueia o loop do swarm; se não achar
  caminho ou ficar "preso" 8s sem avançar, desiste sozinho e volta ao
  comportamento normal — nunca fica preso pra sempre. Chance de ir até
  uma zona de spawn (POI) subiu de 5% pra 15% por think ocioso
  (08/09/2026, §125), e o "anchor" do wander curto passa a assentar
  onde a excursão termina, não mais no spawn original do player — sem
  isso, um bot cujo spawn ficava longe de qualquer zona de mob raramente
  decidia ir até uma, e mesmo quando ia, era puxado de volta pro spawn
  assim que a excursão terminava.
- **Ignora o boneco de treino como alvo** (06/09/2026) — ele tem HP
  altíssimo e nunca morre; sem essa exclusão o bot ficava "grudado"
  nele pra sempre perto do spawn, raramente ociando o suficiente pra
  sortear uma excursão. Detectado via `is_dummy` (payload de spawn,
  `WorldServer._build_mob_spawn_payload`), nunca por nome/raça na mão.

**Reconexão e rampa de conexão têm jitter de propósito** (06/09/2026,
ver `PROBLEMAS_ARQUITETURA.md` §117) — sem isso, bots do MESMO lote (ou
que falham reconexão juntos, ex: numa rajada real de conexão) acordam/
retentam exatamente no mesmo instante, sincronizando ações (inclusive
`CAST_SKILL`) no mesmo tick do servidor de um jeito que um jogador
humano de verdade nunca replicaria. `_reconnect_backoff` usa "full
jitter" (`uniform(0, teto)`, não o teto fixo); a rampa de conexão soma
um jitter uniforme dentro de cada lote.

**Achado importante que embasa isso tudo**: o SERVIDOR nunca calcula
rota pro player (só valida passo-a-passo) — pathfinding de bot roda no
PRÓPRIO processo do swarm, nunca compete com o orçamento de
pathfinding do servidor (`MAX_PATHFINDS_PER_FRAME`, que só existe pra
IA de mob/minion). Risco real de rodar muitos bots fazendo pathfinding
ao mesmo tempo é CPU do processo de bots competindo com o servidor na
mesma máquina — não trava o tick do servidor.

## Limites conhecidos

- `MAX_PLAYERS_PER_WORLD = 500` (`shared/constants.py`) — 300 bots + o
  jogador real fica bem dentro do teto.
- Skills liberadas são só as treináveis (`content/skill_config.py::
  SKILL_ORDER_BY_CLASS`) — skills que dependem de talento alocado
  (`INSTANCE_SKILL_UNLOCK_ORDER`) ficam de fora de propósito (simular
  alocação de talento é escopo à parte). Nenhuma delas exige direção de
  cone (`dir_x/dir_y`) — se um dia entrar skill assim no set liberado,
  `_engage()` precisa de tratamento novo (hoje sempre manda
  `dir_x=0, dir_y=0`).
- `map_data.py` só carrega `maps/map_1` — bots que entram em
  arena/BG (mapas diferentes) não têm arbusto/POI real desses mapas
  disponível ainda.

## Análise pós-teste

Comparar `logs/server_perf.log` (breakdown por sistema, avg/p95/p99)
contra a baseline sem bots — mesmo formato já usado nas medições
manuais anteriores do projeto.
