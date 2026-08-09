# Visão e Roteiro do Produto — RPG ECS Online

> Documento vivo, construído em conjunto com o usuário. **Não é um mapa
> técnico** (isso já existe em `MAPA_PROJETO.md`, "onde encontro o quê no
> código") — é o **rumo**: o que o jogo quer ser, pra quem, e em que ordem
> chegamos lá. Toda decisão de arquitetura registrada em
> `PROBLEMAS_ARQUITETURA.md` deve ser avaliada à luz do que está aqui, não
> o contrário — ver `CLAUDE.md`, "Antes de agir sobre qualquer pedido".
>
> Regra deste arquivo: nunca inventar resposta no lugar do usuário. Onde
> a visão ainda não foi definida, fica registrado em "Perguntas em
> aberto" até ele responder — mesma régua de `feedback_ask_before_deciding`.

---

## Decisões de rumo já tomadas

| Data | Decisão |
|------|---------|
| 06/08/2026 | Ordem de trabalho: **visão primeiro, débito técnico depois** — arquitetura serve o produto, não o contrário. |
| 06/08/2026 | Conflito entre pedido do usuário e arquitetura ECS: **nunca decido sozinho** — paro, explico, sugiro alternativa, espero decisão dele. Sem exceção por tamanho. |
| 06/08/2026 | Testes: **sem política fixa automática** — nível de teste decidido junto, por mudança. |
| 06/08/2026 | Horizonte de escala: **mirando lançamento público eventual**, sem data definida — vale considerar segurança/escala (TLS, hashing forte, índice espacial) com mais peso do que "só grupo fechado de testers", mas isso não muda a ordem de trabalho acima. |
| 07/08/2026 | Talentos: hoje só existe **1 build por classe**; o plano é ter **3 builds por classe** no futuro. Quando existirem, o personagem deve poder **salvar as 3** e **usar 1 por vez**, trocando só fora de combate (mesmo modelo do WoW real). Achado durante o benchmark arquitetural (`BENCHMARK_ARQUITETURA.md` A.2) — registro de rumo, não implementado ainda. |
| 07/08/2026 | **Escopo controlado**: quests diárias/repetíveis, modo de loot de grupo selecionável, e reputação de facção acumulável (perguntas levantadas pelo benchmark, `BENCHMARK_ARQUITETURA.md`) — usuário decidiu NÃO implementar agora, "vai só aumentar nosso escopo". Foco atual: melhorar o que já existe (corrigir bugs, deixar a arquitetura coesa) antes de considerar features novas. Não perguntar de novo até o usuário trazer o assunto. |

---

## Visão definida (respondido pelo usuário, 06/08/2026)

**Pitch**: *"Um MMORPG com o charme visual clássico de Tibia, com quests
legais, combate fluido de WoW e arenas competitivas no estilo MOBA de
LoL, feito para quem ama jogar solo ou em grupo."*

**Modalidades — prioridade real**:
- **Core**: mundo aberto PvE — é onde vai a maior parte do polish/conteúdo.
- Mundo aberto também tem **zonas PvP** (já existe, `pvp_zone_processor.py`)
  pra disputa de item/buff temporário — parte do design do PvE aberto,
  não uma modalidade à parte.
- **Duelo**: também no mundo aberto (já existe) — suporte ao core, não
  centro de atenção própria.
- **Arena e Battleground**: instanciados, competitivos — importantes mas
  SECUNDÁRIOS ao PvE aberto em prioridade de polish.

**Público-alvo**: jogadores de MMORPG que curtem principalmente Tibia,
WoW e LoL — ponto de partida claro pra decidir tom, dificuldade e ritmo
de qualquer conteúdo novo.

**Estilo/tom**: oldschool estilo Tibia como base, com a fluidez e o
humor do WoW por cima — mesmo sendo um pouco dark. (Nota: "dark" +
"humor de WoW" é uma combinação específica, não "sombrio o tempo todo"
nem "cômico o tempo todo" — vale ter isso em mente ao escrever textos de
quest/NPC/flavor no futuro, e perguntar quando um caso concreto não
deixar claro qual dos dois lados pesa mais.)

**Definição de "versão 1.0"**: a maioria dos sistemas funcionando
perfeitamente + pelo menos 1 mapa inteiro construído com uma cadeia de
quests construída — o suficiente pra disponibilizar para testes reais
(não só o grupo fechado atual).

---

## O que NÃO queremos (respondido pelo usuário, 07/08/2026)

- **Pay-to-win — evitado.** Decisão confirmada: qualquer monetização
  futura (não desenhada ainda) não pode virar vantagem de poder — fica
  registrado aqui pra qualquer sistema de loja/premium futuro nascer já
  respeitando isso, em vez de precisar retrofit depois.
- **Grief em zona PvP — mitigado por design, não por mecanismo extra.**
  Decisão do usuário: as zonas PvP serão PEQUENAS, contidas — quem quer
  PvP vai até lá de propósito; quem não quer, não é forçado a atravessar
  a zona pra jogar o resto do PvE. Não é matchmaking de nível/gear (a
  sugestão que eu tinha levantado) — é geometria/posicionamento da zona
  no mapa que resolve o problema na raiz. Registrar isso como restrição
  de design real pra quando o mapa oficial for desenhado: zona PvP não
  pode ficar no caminho obrigatório de rota/quest normal.
- **Power creep — Skill Level é pra ficar e crescer.** Usuário confia
  no sistema e quer mantê-lo (talvez melhorá-lo) como o eixo de
  progressão que diferencia o jogo de gear-check puro — não é só "não
  mexer", é candidato a receber atenção/polish no futuro.

---

## Estado real de conteúdo pra v1.0 (respondido pelo usuário, 07/08/2026)

- **Mapa**: ainda não existe um mapa oficial desenhado — os mapas atuais
  (incluindo `moba_battleground`) foram feitos pra testar sistema, não
  como conteúdo final. **Este é o maior gap real pra v1.0.**
- **Quests**: as quests que existem hoje também foram feitas pra testar
  sistemas (aceitar/progredir/entregar, tipos de objetivo), não como
  cadeia narrativa de conteúdo real. Segundo gap.
- **Sistemas core (PvE aberto / zona PvP / duelo)**: segundo o usuário,
  funcionam perfeitamente até onde ele sabe — não é um "não sei", é
  avaliação de quem joga. Ponto de partida: tratar como prontos pra
  receber conteúdo em cima, não como pendência técnica.

**Conclusão prática**: o gap real pra v1.0 é CONTEÚDO (mapa oficial +
cadeia de quests de verdade), não sistema. Isso muda o próximo passo —
não é uma auditoria técnica dos 3 sistemas core, é decidir como
avançamos na produção de conteúdo (design de mundo, ferramentas,
processo de criação de quest).

---

## Modalidades — estado real hoje (fato, não opinião — pra responder as perguntas acima em cima de dados)

| Modalidade | Estado | Notas |
|---|---|---|
| PvE aberto (mundo, quests, mobs, spawns) | Em produção, base herdada do offline | Fundação do jogo desde antes do branch online |
| Duelo (1x1, convite/aceite) | Em produção | Golpe letal → 1 HP, não mata de verdade |
| Arena 2x2 | Em produção, fila real | `server/match_processor.py` |
| Battleground estilo MOBA (torres, minions, fila real, progressão normalizada) | Em produção, mais recente, ainda recebendo correção de playtest | `server/bg_queue_processor.py`; a maioria dos bugs desta sessão veio daqui |
| Trade, Party/Grupo, Chat | Sistemas de suporte, em produção | Servem todas as modalidades acima |

---

## Roteiro

**Alvo da v1.0**: maioria dos sistemas funcionando perfeitamente (dado
como atingido pelos 3 sistemas core, ver acima) + 1 mapa inteiro com
uma cadeia de quests construída, pronto pra testes reais fora do grupo
fechado atual.

**Gap real confirmado (07/08/2026): conteúdo, não sistema.** Falta (1)
um mapa oficial desenhado de propósito — os mapas atuais são todos de
teste de sistema — e (2) uma cadeia de quests real em cima dele — as
quests atuais também são de teste, não narrativa. A zona PvP pequena/
contida (ver "O que não queremos" acima) é uma restrição de design que
precisa entrar no desenho do mapa oficial desde o início, não ser
encaixada depois.

**Decisão do usuário (07/08/2026, atualizada): benchmark antes do roteiro.**
O roteiro de 8 fases (abaixo) está PAUSADO — antes de continuar
executando, vamos comparar sistema por sistema do nosso projeto contra
2 arquiteturas de referência (Veloren pra forma ECS, AzerothCore pra
conteúdo de quest/instância/talento), pra ter um parâmetro externo real
do que "certo" significa, em vez de só bom-senso meu lendo nosso
próprio código. Ver `arquitetura/BENCHMARK_ARQUITETURA.md` (documento
vivo, cresce um sistema por vez). Ao terminar, o passo final é
sintetizar uma "nova estratégia" que pode revisar o roteiro abaixo
antes de retomar a execução.

**Decisão do usuário (07/08/2026): sistemas antes de conteúdo.** Antes
de qualquer trabalho de mundo/quest/arte, os sistemas precisam ficar
modulares e escaláveis de verdade — não patches pontuais. Roteiro
completo de 8 fases (A2 matar dual-mode, B2/B3 unificar handlers de
skill, A4 inverter autoridade de persistência, A1 decompor componentes-
deus, B1 pipeline declarativa, C1/C2 pré-lançamento) aprovado e em
execução — ver `C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`
e `PROBLEMAS_ARQUITETURA.md` §12/§13 pro detalhe técnico e progresso
fase a fase. A conversa sobre design de mundo/mapa oficial/cadeia de
quests fica pra depois desse roteiro fechar.

---

## Como este documento se relaciona com os outros

- `MAPA_PROJETO.md` — onde o código de cada coisa mora (técnico).
- `PROBLEMAS_ARQUITETURA.md` — débito técnico e histórico de bugs (o quê
  está quebrado/frágil).
- `ARQUITETURA_ONLINE.md` — decisões de protocolo/implementação (como
  foi construído).
- **Este arquivo** — por que estamos construindo, pra quem, e em que
  ordem (o único que nenhum código determina sozinho — só o usuário).
