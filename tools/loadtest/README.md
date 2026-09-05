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
   round-robin) e libera todas as skills treináveis de cada classe.

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

## Limites conhecidos

- `MAX_PLAYERS_PER_WORLD = 500` (`shared/constants.py`) — 300 bots + o
  jogador real fica bem dentro do teto.
- Bot não conhece o mapa (CSV) — "anda cego": tenta um tile adjacente
  aleatório e, se for rejeitado (parede, fora do mapa), o próprio
  servidor corrige a posição via `ENTITY_MOVE` e o bot tenta outra
  direção depois. Ruído ocasional contra parede é esperado e
  inofensivo.
- Skills liberadas são só as treináveis (`content/skill_config.py::
  SKILL_ORDER_BY_CLASS`) — skills que dependem de talento alocado
  (`INSTANCE_SKILL_UNLOCK_ORDER`) ficam de fora de propósito (simular
  alocação de talento é escopo à parte).

## Análise pós-teste

Comparar `logs/server_perf.log` (breakdown por sistema, avg/p95/p99)
contra a baseline sem bots — mesmo formato já usado nas medições
manuais anteriores do projeto.
