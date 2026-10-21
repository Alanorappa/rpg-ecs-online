# Instruções para Claude — RPG ECS Online

> Versão multiplayer — não existe mais versão offline separada.

---

## Workflow obrigatório

### Início de qualquer sessão
1. Ler `arquitetura/VISAO_PRODUTO.md` — o que o jogo quer ser, prioridade
   de modalidades, horizonte de escala, decisões de rumo (06/08/2026,
   documento vivo — leitura obrigatória ANTES de julgar se uma mudança
   arquitetural vale o esforço agora)
2. Ler `arquitetura/ARQUITETURA ONLINE.md` — decisões, protocolo, estado de implementação
3. Ler `arquitetura/MAPA_PROJETO.md` — localização de tudo (online + herdado)
4. Ler `arquitetura/SISTEMAS_ECS.md` — sistemas base do cliente (ECS) + sistemas do servidor
5. Ler `arquitetura/COMPONENTES_ECS.md` — componentes disponíveis
6. Usar esses arquivos como referência ANTES de ler código-fonte

**Procurando algo específico (um bug antigo, uma feature já implementada,
qualquer mudança passada)?** `arquitetura/INDICE.md` é o ponto de partida
— 1 linha por mudança já registrada, com tags e o ponteiro exato (arquivo
+ §N). `grep -i "<termo>" arquitetura/INDICE.md` ANTES de abrir
`PROBLEMAS_ARQUITETURA.md`/`HISTORICO_FEATURES.md`/`HISTORICO_BUGS.md`
inteiros (19/10/2026 — ver regra de classificação em "Após qualquer
mudança").

**Regra:** Se a resposta está nos arquivos de arquitetura, não varrer o codebase.
**Contrapartida desta regra (06/08/2026):** ela só funciona se os arquivos
estiverem certos. Auditoria de 06/08/2026 achou 4 casos de doc dizendo o
OPOSTO do código real (`gold` documentado como "cliente autoritativo"
quando é servidor; `MAPA_PROJETO.md` afirmando "servidor nunca importa
`ui/`" quando importa de propósito; `instance_progression.py` ainda
descrito como "inerte" um mês depois de entrar em produção via fila de
BG; fluxo de "nova stat" ensinando um mecanismo removido do código). Se
algo que você lê num arquivo de arquitetura conflita com o que o código
realmente faz, **o código vence, sempre** — e o doc errado é ele mesmo
um problema a registrar (`PROBLEMAS_ARQUITETURA.md`), não só a ignorar.

### Checkpoint de commit antes de qualquer tarefa (02/09/2026, decisão explícita do usuário)
Antes de começar qualquer tarefa nova (implementação ou correção),
checar `git status`. Se houver qualquer mudança não commitada,
commitar tudo primeiro (`git add` + `git commit`, mensagem descrevendo
o estado — ex: "checkpoint antes de <tarefa>") ANTES de tocar em
qualquer arquivo pra tarefa em si. Esse commit de checkpoint é sempre
local e sempre reversível — é justamente o que permite trabalhar com
edição de arquivo e comando de terminal liberados sem pedir permissão
a cada passo (ver `.claude/settings.json`): qualquer coisa pode ser
desfeita depois com git. **Não pedir permissão pra esse commit de checkpoint, `git add`, nem
pra comandos git só de leitura** (`status`, `diff`, `log`, `show`,
`branch` sem `-D`) — os únicos comandos git que continuam pedindo
permissão são os que mudam remoto ou descartam histórico de verdade
(`push`, `push --force`, `reset --hard`, `checkout` descartando
mudança, `branch -D`, `rebase`), nunca um commit local comum ou uma
consulta.

### Após qualquer mudança
Atualizar os arquivos de arquitetura relevantes:
- Nova mensagem no protocolo → `ARQUITETURA ONLINE.md` (tabela de mensagens) + `shared/messages.py`
- Nova decisão arquitetural → `ARQUITETURA ONLINE.md` (seção de decisões)
- Mudança de status de implementação → `ARQUITETURA ONLINE.md` (tabela de estado)
- Novo sistema no servidor → `SISTEMAS_ECS.md` (seção "Sistemas do Servidor")
- Novo arquivo online → `MAPA_PROJETO.md` (tabela "Onde encontrar o quê — Online")
- Novo componente ECS → `COMPONENTES_ECS.md`
- **Classificar TODA mudança em um dos 3 tipos abaixo ANTES de escrever a
  entrada datada (19/10/2026, decisão explícita do usuário — o arquivo
  único de histórico tinha passado de 28 mil linhas por misturar os 3
  tipos indiscriminadamente, ver `arquitetura/PROBLEMAS_ARQUITETURA.md`,
  nota no topo do arquivo, e `INDICE.md`).** Pergunta pra decidir:
  1. **"Isso é sintoma de um padrão ESTRUTURAL que vai gerar bug de novo
     em outro lugar se não for nomeado?"** (2 sistemas paralelos que
     deviam ser 1, autoridade híbrida, débito de performance, auditoria,
     limpeza de código morto, investigação multi-rodada) → SIM =
     **problema arquitetural** → `PROBLEMAS_ARQUITETURA.md`.
  2. **"Isso é algo NOVO que o usuário pediu (sistema, melhoria, ajuste de
     UX/visual)?"** → SIM = **feature** → `arquitetura/HISTORICO_FEATURES.md`.
  3. **"Isso é um defeito CONCRETO e localizado** (índice errado, ordem
     trocada, offset que não bateu, crash pontual) **sem padrão maior por
     trás?"** → SIM = **bug** → `arquitetura/HISTORICO_BUGS.md`.
  Ambíguo (ex: uma feature revertida que também ensina uma lição
  estrutural) → registra no arquivo da ação PRINCIPAL (normalmente
  feature/bug) e linka o problema estrutural separadamente em
  `PROBLEMAS_ARQUITETURA.md` se o padrão for realmente reusável.
- **SEMPRE, não importa qual dos 3 acima: 1 linha nova em
  `arquitetura/INDICE.md`** (mesmo commit/sessão) — formato `- [DD/MM/AAAA]
  (arch|feature|bug) Título — tags: tag1, tag2 — ARQUIVO.md §N`. É o único
  lugar pensado pra busca rápida (`grep -i "<termo>" arquitetura/INDICE.md`)
  sem precisar abrir um arquivo de milhares de linhas. Tags são livres
  (cresce organicamente, reusa as que já existem quando fizer sentido —
  ver cabeçalho do próprio `INDICE.md` pra lista implícita).
- **Regra nova a partir daqui: separar regra de histórico.** Ao registrar
  uma regra nova neste arquivo, escrever a regra em forma crua e atemporal
  (sem data, sem narrativa do incidente) e colocar o contexto completo
  (data, sintoma, tentativas erradas) no arquivo de histórico certo —
  `PROBLEMAS_ARQUITETURA.md`/`HISTORICO_FEATURES.md`/`HISTORICO_BUGS.md`,
  conforme a classificação acima — referenciado por ponteiro (`ver
  ARQUIVO.md §N`). O objetivo é este arquivo continuar enxuto e sempre
  atual pra releitura a cada sessão — a história detalhada já tem
  endereço certo. Entradas datadas já existentes continuam válidas como
  estão; consolidá-las mais é decisão a ser tomada com o usuário, não
  automática. **Migração de 19/10/2026**: `PROBLEMAS_ARQUITETURA.md`
  tinha 293 entradas misturando os 3 tipos — as que não eram arquitetura
  de verdade saíram pros 2 arquivos novos, com NÚMERO NOVO sequencial lá;
  a posição antiga ficou um stub de 1 linha ("(movido) ... — ver
  ARQUIVO.md §K") — qualquer "ver PROBLEMAS_ARQUITETURA.md §N" antigo
  (inclusive os citados neste próprio CLAUDE.md) continua resolvendo,
  só que indiretamente através desse stub. Nunca "limpar" os stubs —
  são o que mantém todo ponteiro histórico vivo.
- **Isso vale NO MESMO commit/sessão da mudança, nunca "depois que sobrar
  tempo"** (06/08/2026 — `PROBLEMAS_ARQUITETURA.md` ficou 16 dias sem
  entrada nova enquanto pelo menos 4 bugs da mesma família catalogada em
  §11/A4 aconteciam de verdade; nenhum foi cruzado de volta com a
  auditoria que já os previa). Um débito documentado e nunca revisitado
  vale tanto quanto um débito nunca documentado.

---

## Disciplina de qualidade (06/08/2026 — resposta a bugs recorrentes e a uma auditoria que se repetiu sozinha)

### Antes de agir sobre QUALQUER pedido — implementação OU correção (06/08/2026, decisão explícita do usuário)
- **Nunca tratar o primeiro achado "relacionado" ao sintoma/pedido como
  se fosse a causa raiz ou a única forma de implementar.** Antes de
  escrever qualquer código, mapear explicitamente QUAIS sistemas/
  componentes ECS a mudança toca e o efeito colateral em cada um —
  histórico real desta sessão: mais de uma correção "isolada" não
  resolveu o problema relatado, e num caso o mecanismo real só foi
  achado numa camada totalmente diferente da que o fix inicial mexeu
  (ver `PROBLEMAS_ARQUITETURA.md` §12, "Lesson" da investigação do PNQ).
- **Se um pedido do usuário (ou o caminho mais rápido de implementar
  algo) conflita com a arquitetura ECS** — acoplamento novo, estado
  global fora do ECS, lógica duplicada em vez de reusar um ponto único
  de verdade, componente virando "deus" com responsabilidade que não é
  dele, etc. — **NUNCA decidir sozinho, nem para casos que pareçam
  pequenos.** Parar, explicar o conflito, sugerir uma alternativa que
  respeita a arquitetura, e esperar a decisão do usuário antes de agir.
  Sem exceção por tamanho — decisão explícita do usuário, 06/08/2026
  ("nunca decido sozinho — sempre pergunto").
- **Testes: sem política automática fixa, EXCETO os "pontos únicos de
  verdade" catalogados abaixo** (`apply_damage_core`, `is_skill_authorized`,
  `_sessions_in_aoi`, etc.) — essas funções concentram risco em cascata por
  definição (é por isso que viraram ponto único), então mudanças nelas
  exigem teste automatizado sempre. Fora desse catálogo, nível de teste
  (nenhum, manual em playtest, automatizado) é decidido junto, por
  mudança, não presumido — decisão explícita do usuário, 06/08/2026.
- **Explicar mudanças em linguagem simples.** Ao final de qualquer
  implementação ou correção, resumir em português sem jargão técnico o
  que mudou, por que, e quais partes do jogo isso pode afetar — o usuário
  não tem profundo conhecimento de arquitetura de software e depende
  desse resumo pra decidir se aprova. Se o resumo exigir mais de 3 frases
  técnicas complexas pra fazer sentido, é sinal de que a mudança era
  grande demais pra não ter sido discutida em mais detalhe antes.
- Ler `arquitetura/VISAO_PRODUTO.md` antes de julgar se uma refatoração
  arquitetural vale o esforço agora — a ordem de trabalho decidida é
  VISÃO primeiro, débito técnico depois (a arquitetura serve a visão do
  produto, não o contrário).
- **PESQUISAR/DESENHAR ANTES DE CODAR — pra QUALQUER correção ou
  implementação, sem exceção de tamanho.** Decisão explícita do
  usuário, 07/08/2026, depois de um incidente com 3 tentativas
  seguidas erradas no mesmo arquivo (`engine/tileset.py` →
  `engine/camouflage.py` → parametrização escrita direto no código, SEM
  pesquisa nova — só reciclando o benchmark geral de Veloren feito
  antes, não uma checagem específica pra ESTA decisão) — a 3ª tentativa
  aconteceu MESMO DEPOIS de eu já ter escrito a versão anterior desta
  regra, o que prova que "já pesquisei algo parecido antes" não é a
  mesma coisa que pesquisar a decisão específica em jogo. Fluxo
  obrigatório antes de tocar em qualquer arquivo de código:
  1. Mapear o problema arquiteturalmente — que sistema/categoria isso
     é de verdade, não só o sintoma imediato.
  2. Consultar as referências de verdade (Veloren/AzerothCore, ou
     pesquisa nova via WebSearch/WebFetch se o caso for específico e
     não coberto pelo benchmark já feito — nunca assumir que uma
     pesquisa ANTERIOR sobre um tópico mais amplo já cobre uma decisão
     nova e específica).
  3. Verificar se já existe um ponto único de verdade no PRÓPRIO
     projeto que já resolve isso (`fx.py`, `ui/effect_animator.py`,
     `ui/icon_manager.py`, `ui/sound_manager.py` são o padrão certo pra
     "1 módulo por CATEGORIA de asset/efeito, usado por QUALQUER skill
     que precisar" — nunca 1 arquivo por skill, nunca hardcode do nome
     da skill dentro da função quando o mecanismo é genérico).
  4. Propor o desenho pro usuário e ESPERAR aprovação antes de
     escrever/editar qualquer arquivo — mesmo que a mudança pareça
     pequena ou óbvia.
  Caso registrado (revertido depois de 3 idas e vindas):
  `arquitetura/PROBLEMAS_ARQUITETURA.md` §12/§13.

Contexto que gerou esta seção: uma auditoria arquitetural ampla já
existia (`PROBLEMAS_ARQUITETURA.md` §11, 15/07/2026) e já tinha nomeado
corretamente a causa raiz de bugs que se repetiram três semanas depois
(autoridade híbrida na persistência = item A4; servidor despachando
skill via `getattr` numa classe de UI do cliente = item B3). Os fixes
pontuais aconteceram, mas ninguém cruzou o bug novo com o item já
catalogado — cada um foi tratado como incidente isolado. Isso é o
padrão a quebrar, não só os bugs individuais.

- **Antes de investigar qualquer bug, checar `arquitetura/INDICE.md`
  pelo sintoma** (19/10/2026 — ponto de entrada único desde a separação
  em 3 arquivos; o índice já diz se o achado mais próximo é arquitetura,
  feature ou bug, e aponta pro arquivo/§ certo). Se bater com um item de
  `PROBLEMAS_ARQUITETURA.md` marcado "MITIGADO" (não "RESOLVIDO"), o fix
  de verdade é terminar aquele item — não outro patch pontual em cima do
  sintoma novo. Um item "mitigado" que gera um 4º/5º incidente da mesma
  família é sinal de que a mitigação nunca foi suficiente; dizer isso ao
  usuário explicitamente em vez de tratar como bug novo. Bater com um
  item de `HISTORICO_BUGS.md` (defeito já visto e corrigido antes, sem
  padrão estrutural por trás) é só contexto — confirma que já aconteceu,
  não implica necessariamente a mesma causa raiz desta vez.
- **"Corrigido" exige reproduzir o sintoma que foi relatado, não só um
  cenário sintético plausível.** Um teste automatizado que você mesmo
  escreve prova que O SEU cenário passa — não que o bug relatado sumiu.
  Antes de declarar algo corrigido: (a) reproduza o cenário exato
  descrito (mesma sequência, mesma configuração), ou (b) diga
  explicitamente que não foi possível confirmar contra o caso real e
  que falta dado adicional (log, replay, resposta a uma pergunta
  direta) — nunca generalize de "meu teste sintético passou" pra
  "corrigido". Rodar a suíte inteira (900+ testes) não é prova de nada
  além de "não quebrei outra coisa que já era testada".
- **Campo novo persistido a partir de `session.last_client_payload`
  (autoridade híbrida)**: antes de aceitar esse padrão pra um campo
  novo, checar se ele é afetado por QUALQUER overlay/estado temporário
  (progressão normalizada de instância, ou qualquer mecanismo parecido
  futuro) — `server/instance_progression.py` troca `TalentTree`/
  `PlayerSkills`/`Wallet`/`Inventory`/`Equipment`/`PermanentStats`. Se
  for, o campo nasce com um `live_X` lido do ECS ao vivo desde o
  commit inicial (mesmo padrão de `get_player_equipment_data`/
  `get_player_hotbar_data`/`get_player_talent_data`) — não como reação
  a um bug relatado depois. Auditoria de 06/08/2026 achou 5 campos
  afetados; só 3 tinham esse fallback.
- **Proibido estado mutável ad-hoc fora do ECS.** Nunca anexar
  cache/estado via `hasattr(func, "_x")`/atributo de função, nem dict
  mutável no CORPO de uma classe (é atributo de classe, compartilhado
  entre todas as instâncias — armadilha clássica do Python, não estado
  por-instância). Padrão certo já existe no projeto e é pra ser
  copiado, não reinventado: dict/lista de MÓDULO com nome visível +
  `global` explícito na função que muta (`core_systems.
  register_lethal_interceptor`, `faction_system.register_pvp_context`,
  o `_svc` já documentado acima), ou — se for cache de recurso
  (sprite/superfície) com necessidade real de limite — um dict de
  módulo com teto explícito e `clear()` documentado
  (`ui/floating_text.py::_outline_cache` é o exemplo a seguir).
- **Antes de escrever qualquer lógica nova, verificar se um dos "Pontos
  únicos de verdade" acima já cobre o caso** — a lista existe
  precisamente pra evitar reimplementação local. Se a lógica que você
  está prestes a escrever se parece com algo que já devia ter um dono
  único (dano, teleporte, autorização de skill, broadcast), procurar
  o dono antes de escrever um novo caminho paralelo.
- **Análise de terceiro sobre o próprio código do projeto (outra IA,
  documento externo, benchmark trazido pelo usuário) é verificada
  contra o código ATUAL antes de aceitar qualquer recomendação dela**
  — mesma régua de "antes de recomendar a partir de memória, verificar
  estado atual", só que a fonte é externa à conversa em vez de memória
  própria. Uma análise que só viu uma AMOSTRA de saída (log, print,
  screenshot) nunca viu o código de verdade por trás — recomendações
  dela podem estar redundantes (algo já implementado) ou erradas sobre
  onde o problema realmente mora. Ver `PROBLEMAS_ARQUITETURA.md` §23.

---

## Contexto do projeto
- RPG Tibia/WoW-style, ECS puro: `world.py` (registry), `components.py` (dados),
  `systems.py` (lógica)
- Stack: Python 3.10, `pygame-ce>=2.4.0` (`requirements_server.txt`)
- Personagens: Guerreiro (Cavaleiro), Mago (Piromania), Arqueiro (Bardo)
- TILE_SIZE = 32px, tela 1280×720, cliente renderiza a 60 FPS
- Servidor: Python asyncio + WebSocket, **sem Pygame**, 30 ticks/s
- Cliente: Pygame + `client/network.py`
- Banco: PostgreSQL via `asyncpg` (migrado de SQLite em 07/09/2026, ver
  PROBLEMAS_ARQUITETURA.md §123 — dev local roda um container Docker,
  `RPG_DATABASE_URL` aponta pro alvo). `server/auth.py` é o ponto único
  de acesso — nunca abrir conexão em outro lugar.
- Protocolo: JSON via WebSocket (→ MessagePack antes do lançamento)

---

## Regras do projeto online

### Separação cliente/servidor (inviolável)
- `server/` **nunca importa Pygame** — código de servidor deve rodar headless
- `client/` **nunca calcula gameplay** — apenas renderiza estado recebido do servidor
- `shared/` **sem estado** — só constantes e funções puras de serialização

### Isolamento e modularidade (ECS) — convenção geral, não ligada a um incidente específico
- Um System nunca chama outro System diretamente (`outro_sistema.fazer_algo()`);
  comunicação entre sistemas acontece via componentes (estado compartilhado) ou
  eventos/mensagens. Se dois sistemas parecem precisar "conversar" direto, é sinal de
  que falta um componente ou evento intermediário — não de que a chamada direta é a
  solução mais simples.
- Nenhuma classe deve acumular responsabilidade de mais de um domínio (ex: uma entidade
  ou handler que mistura lógica de rede, física e IA na mesma classe). É a mesma
  disciplina de "`server/` nunca importa Pygame" e "`client/` nunca calcula gameplay",
  só que aplicada DENTRO de cada lado, não só na fronteira entre eles.
- Prefira composição a herança: se resolver um caso novo exige criar uma subclasse pra
  um comportamento específico, isso é sinal de que falta um Component novo, não uma
  classe nova.

### Pontos únicos de verdade (03/07/2026 — usar SEMPRE, nunca reimplementar)
- **Sistema de gameplay novo** → `world_systems.py` (headless, ZERO pygame no
  topo; efeitos via `from fx import FLT, SOUNDS, ...`). Sistema de UI/render
  novo → arquivo de domínio próprio em `ui/` (18/09/2026, `ui/systems.py`
  monolítico de 4913 linhas foi dividido — `ui/player_input_system.py`,
  `ui/render_system.py`, `ui/tile_render_system.py`, `ui/shop_system.py`,
  `ui/consumable_system.py`, `ui/loot_system.py`, `ui/skill_system.py`; ver
  `arquitetura/MAPA_PROJETO.md` pra tabela completa) — NUNCA `ui/systems.py`,
  que agora é só um shim de re-exportação de compatibilidade (mesmo padrão
  que ele mesmo já usa pra re-exportar `world_systems.py`). Ver
  PROBLEMAS_ARQUITETURA.md §185.
- **Efeito visual/som em código compartilhado** → façade `fx.py` (no-op no
  servidor; cliente vincula via `fx.bind_client_fx()` no GameEngine).
- **Teleporte/knockback/respawn** → `utils.snap_to_tile()` — NUNCA escrever
  `current_tile_x/y` direto (classe de bug: tween antigo sobrevive ao snap).
- **Conceder item a um player fora de loot/trade/forja** (recompensa de
  quest, e qualquer sistema futuro — conquista, recompensa diária, etc.)
  → `WorldServer.grant_items_to_player(player_eid, entries)`
  (`server/world_server.py`) — escreve direto no Inventory AO VIVO do
  servidor (mesma mecânica de empilhar/caber que loot usa,
  `LootProcessorMixin._stack_or_add_item`). NUNCA reimplementar um loop
  de `inv.items.append` à parte, nem mandar item só pro cliente mostrar
  "confiando" que um INV_SYNC futuro sincroniza depois — bug real:
  recompensa de item de quest fazia exatamente isso, o servidor nunca
  ficava sabendo do item de verdade, equipar não tinha efeito nenhum nos
  stats reais e o item sumia no próximo login (ver
  PROBLEMAS_ARQUITETURA.md §106).
- **Qualquer mutação local de `Inventory` feita no CLIENTE (deletar
  item, e qualquer ação futura parecida) precisa chamar
  `self._on_loot_action("item")` (`client/save_sync_handlers.py` →
  `INV_SYNC` → `WorldServer.sync_player_inventory`) pra virar real pro
  servidor** — `_send_save_state()` sozinho NUNCA basta: o servidor
  ignora de propósito o inventário que vem via SAVE_STATE pra
  persistência (sempre prioriza o `Inventory` AO VIVO dele mesmo, ver
  `server/session.py::_build_save_merge`/`live_inventory`) — mesma
  decisão que impede uma overlay de instância de vazar pro save real.
  Bug real: tecla Delete no inventário removia o item só do espelho
  LOCAL; o servidor nunca soube, e qualquer resync (relog, entrar/sair
  de instância) trazia o item de volta. Ver PROBLEMAS_ARQUITETURA.md
  §275.
- **Posição (`inv_index`) de um item guardada num modal/estado
  client-side que fica ABERTO esperando input do jogador (ex: modal de
  quantidade do trade) NUNCA pode ser confiada na hora de CONFIRMAR —
  reencontrar a posição ATUAL do mesmo objeto item (guardar e comparar
  por referência/identidade, `is`, não por índice) antes de agir.**
  `Inventory.items` muda por ÍNDICE a qualquer momento que uma
  mensagem assíncrona do servidor chegue nesse meio tempo (ex:
  `TRADE_STATE` removendo um item ofertado desloca todo mundo depois
  dele) — um índice capturado na ABERTURA do modal pode já apontar pra
  outro item na hora do OK. Bug real: modal de quantidade do trade
  guardava o índice no `Shift+clique`, mas só confirmava contra ele
  depois; se qualquer OUTRA oferta do mesmo jogador confirmasse
  enquanto o modal estava aberto, o item errado ia pro slot de oferta
  (servidor aceitava sem checagem de identidade nenhuma). Ver
  PROBLEMAS_ARQUITETURA.md §275.
- **Andar até um NPC amigável pra interagir** (treinador, ferreiro, quem
  dá quest, mercador — qualquer NPC futuro nessa mesma categoria) →
  `ui/npc_interaction.py::walk_to_npc()`/`suppress_chase()` — NUNCA
  reimplementar o cálculo de "tile adjacente mais perto" numa cópia
  local. `walk_to_npc` já chama `suppress_chase` sozinho; o caminho
  "já adjacente, abre direto" (sem andar) de cada sistema precisa
  chamar `suppress_chase` também, na mão. Bug real: 4 sistemas
  (Trainer/Blacksmith/QuestDialog/Shop) tinham cada um sua própria
  cópia dessa lógica, nenhuma suprimia a perseguição de um alvo de
  combate travado — clicar num NPC enquanto perseguia outro alvo (ex:
  boneco de treino) fazia o personagem oscilar entre os dois, o
  auto-chase competindo com o walk-to-NPC todo frame (ver
  PROBLEMAS_ARQUITETURA.md §112). `suppress_chase` só liga
  `CombatState.chase_suppressed` — NUNCA mexer em
  `target_entity_id`/`is_pursuing` aqui, são os únicos campos que
  controlam troca/perda de alvo de verdade (kite de arqueiro depende
  disso continuar intocado).
- **Escrita final de dano em HP** → `core_systems.apply_damage_core()` —
  guards imune/morto, overkill preservado, quebra polymorph/sleep. Regra nova
  de mitigação/resistência entra SÓ ali (deal_damage/_apply_final_damage/
  _apply_magic_damage são delegates).
- **STATS_UPDATE privado (servidor→dono)** → `WorldServer.queue_stats_update()`
  (schema na docstring; valida player_eid na origem). Mudança de
  `current_hp`/`max_hp` do PRÓPRIO player nunca precisa de chamada nova
  aqui — `WorldServer._sync_player_hp_dirty()` já detecta genericamente E
  já enfileira o STATS_UPDATE privado pro dono sozinho (01/09/2026), além
  do broadcast AOI pros outros players que já fazia antes. NUNCA mandar
  `hp`/`hp_max` só pro broadcast (achando que "o dono já recebe por
  outro canal") — bug real: nenhum canal real mandava `hp_max` pro
  dono quando a mudança vinha só de `add_modifier`/`timed_modifiers`
  (proc de item, buff de jungle boss, self-buff de skill) — `current_hp`
  chegava certo via HP5/combate mas com `max_hp` desatualizado do lado
  cliente, current/max passava de 1.0 e a barra de HP "crescia" na tela
  (ver PROBLEMAS_ARQUITETURA.md §89).
- **Qualquer ataque contra um player, de QUALQUER atacante (player,
  mob, torre, minion, boneco de treino, ou categoria futura)** →
  `CombatSystem.deal_damage` (`engine/world_systems.py`) já é o ponto
  único onde o outcome (hit/crit/block/miss/dodge/parry) é decidido —
  é TAMBÉM o ponto único de BROADCAST (07/10/2026, §246 "unificação").
  Pra qualquer atacante que não seja player, os 2 pontos internos já
  chamam `_svc.get("emit_combat_event")` sozinhos (branch de avoidance
  e branch de dano, ambos gated por `self.is_server and not
  attacker_is_player and target_is_player`) — registrado em
  `WorldServer._on_mob_combat_event`/`_pending_mob_attacks` (mesma
  lista que mob-vs-mob/NPC já usa, concatenada SEMPRE antes de
  `_combat_this_tick` em `_collect_deltas`, pra preservar a ordem
  "ataque antes de DoT" independente de QUANDO no tick o evento for de
  fato emitido — ataque ranged só resolve no impacto do projétil,
  depois do tick de DoT no mesmo tick). **NUNCA crie um canal de
  broadcast paralelo** (callback próprio tipo o antigo
  `emit_dummy_reflect`, ou qualquer mecanismo de "deduzir o evento
  depois por diff de HP") — se o atacante já cai em `deal_damage`
  (qualquer chamada direta ou via `ProjectileSystem`), o broadcast já
  está coberto de graça, sem precisar de nenhum código novo no call
  site. Mecanismo ANTIGO (removido nesta unificação, histórico em
  PROBLEMAS_ARQUITETURA.md §246 e `historico/ARQUITETURA ONLINE
  HISTORICO.md` §34.43): existiam 2 canais de DEDUÇÃO (`mob_delta`,
  diff de HP; `mob_avoidance_events`, miss/dodge/parry sem HP-diff)
  que "descobriam" o ataque de mob depois, por não ter registro
  explícito — toda fonte nova que JÁ se auto-registrava (PvP,
  retaliation, boneco) precisava se excluir manualmente dos 2,
  duplicado em 2 arquivos — 2 bugs reais de FLT duplicado no mesmo dia
  provaram que esse padrão não escala. Não existe mais nada pra
  excluir.
- **Qualquer mecânica que causa dano/efeito COLATERAL no PRÓPRIO
  CASTER de uma skill, DENTRO da execução do handler dela** (ex:
  reflexo do boneco de treino batendo de volta, ou categoria futura
  parecida) → `engine.core_systems.should_include_skill_damage_target(
  mob_eid, damage, caster_eid, explicit_target_id)` (07/10/2026, §247 —
  ponto único, unificado a partir de 3 call sites quase idênticos que
  existiam antes) — SEMPRE chamar isto antes de incluir `mob_eid` na
  branch de DANO do relatório de "quem a skill afetou" pro
  `SKILL_RESULT`, NUNCA reescrever a condição na mão num 4º call site
  futuro. Usado hoje em `server/skill_processor.py` e
  `server/spell_completion_processor.py` (2 call sites: completion sem
  projétil e impacto de projétil). Causa do bug original: os 3 call
  sites leem `LAST_DAMAGE_AMOUNTS`/`LAST_DAMAGE_OUTCOMES`/
  `LAST_EFFECTS_APPLIED` (`engine/core_systems.py`, dicts GLOBAIS
  limpos antes do handler e lidos depois — otimização de 06/09/2026
  que substituiu um snapshot O(mobs), ver histórico) — populados por
  QUALQUER `apply_damage_core` que disparar durante a janela do
  handler, sem saber se foi a skill batendo no alvo dela ou um efeito
  colateral batendo de volta no caster (ex: reflexo do boneco). A
  função só afeta a branch de DANO — nunca generalizar pra excluir
  outro eid qualquer, e nunca tocar na branch de efeito-sem-dano
  (self-buff tipo `cc_immune` do Fatiador de Corpos já é seguro por
  natureza: só entra ali quando `mob_eid == tid`/`target_id`).
  `_server_apply_magic_damage(..., report=True)` (canalização
  tick-a-tick) NÃO precisa desse guard — já é escopada por-alvo
  (hp_before/hp_after de 1 entidade só), nunca um sweep global. Ver
  PROBLEMAS_ARQUITETURA.md §247.
- **Entidade que só deve aceitar dano de um TIPO específico** (ex: alvo
  de treino de arqueiro, `TrainingDummy.accepts_only_ranged`) →
  `apply_damage_core(..., damage_kind="melee"|"ranged"|"magic"|"")`
  (07/10/2026) — cada um dos 4 call sites reais (`CombatSystem.
  deal_damage`→`"melee"`; `_apply_final_damage` via
  `_server_apply_ranged_physical`→`"ranged"`/`_server_apply_magic_
  damage`→`"magic"`; `apply_magic_damage_shared`→`"magic"`; retaliation
  do Escudo de Fogo→`"magic"`) já SABE seu próprio tipo — nunca inventar
  lógica de detecção dentro de `apply_damage_core`, só passar o literal
  certo. `""` (não classificado) é tratado como "não é ranged" — fail-
  closed. **DoT não passa por `apply_damage_core`** (`StatusEffectSystem.
  _apply_tick` escreve `current_hp` direto) — qualquer gate por
  `damage_kind` tem esse buraco conhecido, aceito pelo usuário pro alvo
  de arqueiro (ver PROBLEMAS_ARQUITETURA.md §248); se um gate futuro
  precisar cobrir DoT também, precisa de um 2º ponto dentro de
  `_apply_tick`, nunca assumir que o gate em `apply_damage_core` já
  cobre tudo.
- **Sprite de uma entidade de serviço/decoração configurável por
  instância via JSON do mapa** (ex: boneco de treino, `"sprite":
  "vp_184"` em `training_dummies[]`) → parâmetro na função `create_*`
  (`Renderable(sprite_id=sprite_id, ...)`, nunca hardcoded) + leitura
  opcional em `engine/map_loader.py` com default = aparência de sempre.
  **Sincronização de rede já é automática, sem campo de protocolo
  novo** — `WorldServer._build_mob_spawn_payload` já lê `Renderable.
  sprite_id` genericamente pra QUALQUER mob (mesmo mecanismo que Torre
  já usa), e o cliente já aplica (`client/remote_entity_handlers.py`,
  `server_sprite_id = data.get("sprite_id")`) — só funciona pra
  entidades sincronizadas como mob (`Combatant`/`_mob_eids`).
- **Atributo de combate novo** → par `base_X`/`X` em `CombatStats` + 1 entrada
  em `stat_fns._MODIFIABLE_ATTRS` (+ `_STAT_CLAMPS` se tiver limite).
- **Qualquer coisa (item, buff, talento) que pode mudar `stamina` (deriva
  `max_hp`)** → `stat_fns.recalculate_preserving_hp_pct(cs, mutate_fn)`
  (30/08/2026) — NUNCA chamar `add_modifier`/`remove_modifier`/
  `cs._recalculate_effective_stats()` direto quando o atributo pode ser
  `stamina`. `recalculate_combat_stats` sozinho preserva o HP ABSOLUTO
  (certo só quando `max_hp` diminui); sem o wrapper, `max_hp` subindo
  deixa um buraco real sem cura — um player 100% cheio vira, ex.,
  300/330 — e o HP5 fica "tickando fantasma" pra fechar essa diferença
  de verdade (bug real relatado pelo usuário, buff de chefe de jungle +
  troca de equipamento na BG, ver PROBLEMAS_ARQUITETURA.md §86).
- **Qualquer buff TEMPORÁRIO novo (proc de item, self-buff de skill,
  buff de matar mob/boss)** → `engine.core_systems.grant_visible_buff(
  world, eid, effect_type, duration, mutate_fn)` (31/08/2026) — NUNCA
  chamar `add_timed_modifier` sozinho pra um buff que devia ser visível.
  `mutate_fn` é a mutação de stat real (o `add_timed_modifier` de
  sempre); a função TAMBÉM registra um `ActiveEffect` sincronizado
  (`apply_effect`, `is_buff=True`) — sem isso o buff nunca chega ao
  cliente (bug real pré-existente: proc de item só existia em
  `CombatStats.timed_modifiers`, nunca sincronizado; o cliente
  "adivinhava" com RNG próprio, podendo divergir do servidor). `effect_type`
  precisa de entrada em `content/status_effects_data.py::EFFECT_DEFS`
  (chave ESTÁVEL e ÚNICA por fonte — nunca uma chave genérica
  compartilhada, `StatusEffects.effects` só guarda 1 instância por
  chave, e é normal 2+ buffs de fontes DIFERENTES ativos ao mesmo tempo),
  mesma disciplina de skill nova precisar de entrada em SKILL_CATALOG.
  Concessão que combina 2+ atributos numa MESMA concessão (ex: buff
  attack_power+haste_rating junto) precisa de um label DISTINTO por
  atributo dentro de `mutate_fn` — usar o mesmo label pros 2 faz
  `add_timed_modifier` (`engine/stat_fns.py`) só renovar o timer do 1º
  e nunca aplicar o 2º de verdade (bug real achado e corrigido
  31/08/2026, ver PROBLEMAS_ARQUITETURA.md §88). Talento
  permanente/equipamento continuam fora de propósito (não são
  "temporários") — nunca precisam de `grant_visible_buff`.
- **Efeito temporário do jogador com mecanismo PRÓPRIO** (componente
  dedicado tipo `FireShieldEffect`/`IceBlockEffect`, ou uma flag
  booleana sem duração tipo `fire_instant_ready`/`na_mosca_bonus_active`)
  → **NÃO** força pra StatusEffects/`grant_visible_buff` — adiciona 1
  checador pequeno em `ui/buff_bar.py::_gather_extra_buffs()` que só LÊ
  o campo/componente real (nunca escreve, nunca interfere no tick/
  sincronização/expiração de cada mecanismo) e devolve um `_FakeEffect`
  sintético (mesmo padrão do shim de stun no nameplate,
  `ui/render_system.py`). Decisão do usuário (31/08/2026): migrar mecanismos
  já funcionando e testados (Camuflagem tem 10+ consumidores — AOI de
  visibilidade, alvo de mob, respawn/ghost; Escudo de Fogo/Bloco de Gelo
  têm tick duplicado cliente+servidor) só pra ganhar 1 ícone é risco
  desproporcional ao ganho. Precisa também de 1 entrada em
  `content/status_effects_data.py::EFFECT_DEFS` (label/cor de fallback —
  esta entrada NUNCA é de fato aplicada via `apply_effect`, só existe
  pro ícone). Ver PROBLEMAS_ARQUITETURA.md §88 (fase 2).
- **Ordem de ativação de qualquer buff/efeito pra fila da buff bar**
  (qual liga antes de qual, entre fontes DIFERENTES) → `engine.
  effect_order.mark_activated(world, eid, effect_type)` (01/09/2026) —
  NUNCA comparar ordem entre fontes na mão (`ui/buff_bar.py` não sabe
  "quando" nada ligou, só o que está ativo AGORA). `apply_effect()` já
  chama isso sozinho pra todo buff/debuff de StatusEffects — só precisa
  de chamada nova no ponto de ativação de um mecanismo que NÃO passa por
  `apply_effect` (Escudo de Fogo/Bloco de Gelo/Camuflagem/procs
  binários) — 1 linha, na transição off→on, nunca todo tick/frame (senão
  vira sempre "o mais novo"). `ui/buff_bar.py::BuffBar.render()` ordena
  a lista final por `get_seq()`, um `sort()` genérico — nenhuma
  comparação hardcoded por tipo de fonte.
- **Texto de tooltip de qualquer buff/efeito** → campo `description` em
  `content/status_effects_data.py::EFFECT_DEFS` (01/09/2026) — mesmo
  catálogo central que já dá label/cor/ícone, NUNCA um bloco de texto
  calculado na mão por `effect_type`. **Texto de tooltip de QUALQUER
  skill** → campo `"desc"` do `SKILL_CATALOG`
  (`content/skill_config.py`), lido via `skill.description`
  (`client/tooltip_handlers.py::_skill_tooltip_lines`) — mesma regra,
  NUNCA um `if sid == "golpe_poderoso": ...` por skill calculando
  dano/efeito na mão (09/10/2026, pedido explícito do usuário — essa
  cadeia existia e foi removida; nunca reintroduzir um 3º lugar com
  texto hardcoded por skill_id). Se a skill precisa mostrar um NÚMERO
  calculado (dano real do jogador, por exemplo), isso é um problema
  de **UX perdida**, não motivo pra reverter — decisão consciente do
  usuário de priorizar consistência (1 fonte de texto) sobre o cálculo
  dinâmico que existia antes. Geometria de
  hit-test (qual ícone está sob o mouse) vem de `ui/render_system.py::
  _build_effects_row()`, que devolve `(surface, rects)` — o MESMO
  `ix`/`ICON` já usados pra desenhar, nunca duplicar esse cálculo num
  hit-test à parte (mesmo princípio já usado pela hotbar,
  `client/hotbar_handlers.py`). Ver PROBLEMAS_ARQUITETURA.md §90.
- **Estado "estou em cima de um arbusto real" chegando ao cliente**
  (feedback visual pessoal — opacidade, ícone, etc.) → `WorldServer.
  _sync_player_bush_dirty()` (`server/world_server.py`, 01/09/2026,
  corrigido no mesmo dia) — dirty-check por player, fonte é
  `BushZoneProcessorMixin._is_on_bush_tile(eid)` (`server/
  bush_zone_processor.py`, lê `TileType.is_bush` no tile atual do
  player), sincroniza via `queue_stats_update` privado (`in_bush:
  bool`). **NUNCA `_get_bush_zone()`/`bush_zones_by_map`** — esse é o
  retângulo de zona antigo (§47), nunca populado em NENHUM mapa real
  (`grep` em `maps/*_entities.json` não acha `bush_zones` em lugar
  nenhum) — usá-lo como fonte foi a causa raiz de a opacidade nunca ter
  aparecido em jogo na 1ª tentativa. NUNCA broadcast AOI (ninguém além
  do dono precisa saber) e NUNCA reusar/tocar `CombatState.is_visible`/
  `is_camouflaged` — bush stealth e Camuflagem são mecanismos
  separados, campos separados (`CombatState.is_bush_hidden` é o novo,
  client-only). Tile novo que deve contar como "bush" pro feedback →
  marcar `"is_bush": True` no GRUPO do catálogo em
  `engine/tileset.py::OBJECT_SHEET_FAMILIES` (hoje só `"arbustos"`,
  linha ~913) — NUNCA checar `sprite_name`/prefixo do tile em runtime
  (árvore usa o MESMO `vision_height=2` que bush pra bloquear LOS, mas
  nunca deve contar aqui; `vision_height` sozinho não distingue as
  duas). A invisibilidade DE VERDADE pros outros players (diferente da
  opacidade, que é só cosmética) já vem de outro lugar —
  `server/tile_los_processor.py::_has_tile_los()` — nunca mexer nesse
  mecanismo achando que precisa "ativar" bush stealth, ele já funciona
  pra qualquer `vision_height>=2` no tile do alvo. Ver
  PROBLEMAS_ARQUITETURA.md §91.
- **Dano base de skill FÍSICA** → `damage_calculator.ability_physical_damage()`
  (`arma×dmg_weapon_pct + AP×(damage_multiplier + 0.01×skill_level_da_arma)`) —
  multiplicador vem SÓ do SKILL_CATALOG, NUNCA hardcodear no handler (classe
  de bug: golpe_poderoso com 3.0 fixo tornava o catálogo letra morta).
  Skill sem arma → `dmg_weapon_pct: 0.0` no catálogo. Magia → `spell_damage()`.
- **Dano base de AUTO-ATTACK físico** (melee/ranged, guerreiro/arqueiro) →
  `damage_calculator.calculate_base_damage(..., ap_skill_mult=...)` — mesma
  fórmula de skill_level das skills (`1.0 + 0.01×weapon_skill_level`), só que
  aplicada ao AP dentro do branch `damage_type=="physical"`. Resolvida pelo
  CHAMADOR (`world_systems.py::_calculate_damage`/`deal_damage`,
  `server/spell_completion_processor.py::_server_apply_ranged_physical`) —
  NUNCA hardcodear `ap_skill_mult=1.0` num call site novo de auto-attack, ou
  ele fica pra sempre fora da escala de skill_level (era o estado ORIGINAL,
  corrigido em 07/07/2026).
- **Dano por tick de DoT de habilidade de MOB** (bleed/poison/burn, ver
  `content/enemy_abilities_data.py::AbilityDef`) →
  `damage_calculator.mob_dot_tick_damage(magnitude, base_attack_power,
  level)` — divide por `√level` (não multiplica direto por
  `base_attack_power`, que já escala LINEAR com level em
  `entity_factory.py`; compor 2 crescimentos lineares explodia em level
  alto — urso level 24 batendo 96/tick de Laceração, corrigido
  28/08/2026). NUNCA calcular `magnitude * base_attack_power` direto
  num call site novo de habilidade de mob — os 2 pontos existentes
  (`engine/world_systems.py`, corpo-a-corpo e projétil) chamam essa
  função, um 3º ponto que reimplemente a conta vira código paralelo.
- **Dano de qualquer habilidade da escola de FOGO do mago** (nova skill
  de fogo futura, ou qualquer call site que já cause dano `school="fogo"`)
  → `_apply_general_fire_talent_bonus()` (multiplicadores: Pirotecnia/
  Crematória/Choque Térmico) ANTES de `_server_apply_magic_damage`, e
  `_trigger_general_fire_talent_procs()` (procs pós-dano: Lapso Elemental/
  Chama Interna) DEPOIS — ambas em `server/spell_completion_processor.py`
  (10/10/2026). `_server_apply_magic_damage` em si só usa `school` pra
  aplicar `resist_<school>` do alvo — NUNCA sabe de talento nenhum, não
  adianta só passar `school="fogo"` achando que os talentos vêm de
  graça. **Critério pra saber se um talento entra nessas funções**: ler
  a PRÓPRIA descrição do talento em `content/talent_data.py` — se fala
  "skills/habilidades de fogo" em GERAL, entra; se cita uma skill
  específica por nome (ex: Queimaduras Profundas/Exaustão citam "Bola
  de Fogo" explicitamente), fica de fora, só no handler daquela skill
  mesmo. Bug real: Calamidade Flamejante usa um caminho de dano
  separado (`_process_player_channeling`, tick de `Channeling`) que
  nunca replicava os 5 talentos gerais — Choque Térmico nunca
  funcionava nela; Lapso Elemental (que também é "geral" pela própria
  descrição) nem no Calcinar funcionava, só existia dentro do handler
  da Bola de Fogo. Ver PROBLEMAS_ARQUITETURA.md §263.
- **`_process_player_channeling` (Calamidade Flamejante) é um caminho de
  dano/custo SEPARADO de `_process_spell_cast_completions` (cast único
  de Bola de Fogo/Calcinar/Nova Congelante)** — qualquer mecanismo NOVO
  ligado a "lançar uma skill de fogo"/"gastar mana numa skill" (talento,
  XP de skill, proc, etc.) precisa ser checado explicitamente contra
  ESSE caminho de canalização também, nunca assumir que os 2 handlers de
  cast único cobrem "toda skill de mago". Bug real duplo, mesmo dia
  (10/10/2026): Choque Térmico (§263) e concessão de XP de Magic (§264)
  — os dois só existiam no cast único, nunca replicados pro tick de
  canalização. Magic XP é concedida dentro do bloco de dedução de mana
  por tick (`grant_skill_xp(..., "magic", 1)`, mesma regra do cast
  único: custo efetivo de mana > 0, independente de acerto/dano).
- **Decidir "esse fix precisa de build de cliente?" exige olhar `git log
  <última tag de release_client.ps1>..HEAD` inteiro, nunca só o commit
  que acabou de ser feito** — bug real (§264a): deploy só de servidor
  pro fix de §263 (`spell_completion_processor.py`, correto, é
  server-only de verdade) esqueceu que havia um 2º commit no meio
  (checkpoint do usuário em `content/skill_config.py`) que TAMBÉM
  precisava de cliente (`SKILL_CATALOG` é lido por `ui/spell_system.py`
  pra montar a barra de cast local client-side,
  `engine/components.py::Skill._make_skill`) — tester no executável
  empacotado ficou vendo cast_time desatualizado. `content/*.py`
  (catálogos — skill/item/talent/merchant) é quase sempre client-
  relevante; `server/*.py` isolado quase sempre não é — mas a checagem
  tem que ser por COMMIT, nunca só pelo arquivo do fix mais recente.
- **Broadcast direto novo (fora do AOI_UPDATE)** →
  `SessionManager._sessions_in_aoi(tx, ty, map_file, origin_eid=...)` —
  NUNCA iterar `self._sessions` com check de distância à mão (classe de bug:
  vazamento cross-map + ignora visibilidade de camuflado + métrica errada).
  Visão compartilhada de time (instanciado — arena/battlefield/dungeon,
  30/07/2026) já é embutida em `_sessions_in_aoi` E em
  `_build_update_for_session` via `SessionManager._ally_vision_centers`
  (recomputado 1x/tick, `_compute_ally_vision_centers()`) — chamadas novas
  não precisam reimplementar isso.
- **Player pode usar a skill?** → `world_systems.is_skill_authorized()`
  (classe + talento + learned) — gate autoritativo chamado pelo
  skill_processor; toda forma nova de adquirir skill entra ALI. Fixtures de
  teste usam `tests.helpers.authorize_skill()`.
- **Resolver nome de personagem → eid entre TODOS os players online**
  (correio, convite de grupo por nome, ou categoria futura parecida que
  precise alcançar alguém independente de estar visível/na AOI) →
  `WorldServer.find_online_player_eid_by_name(name)` (13/10/2026,
  case-insensitive, filtra por `CharacterStats` — NUNCA `EntityIdentity`,
  que é componente de mob/NPC, nunca anexado a player; nome de player
  mora em `CharacterStats.name`, mesmo campo que `get_party_snapshot` já
  usa) — NUNCA resolver nome só entre entidades VISÍVEIS localmente
  (`RemoteControlled`/AOI) quando a ação deveria alcançar qualquer
  player conectado, independente de distância. Bug real: "/convidar"
  só funcionava com quem já estava no AOI do requester, porque o
  CLIENTE resolvia o nome digitado só entre players já espelhados
  localmente — o servidor (`request_party_invite`) nunca teve restrição
  de distância nenhuma. Ver PROBLEMAS_ARQUITETURA.md §278.
- **Troca de aparência temporária de entidade** (disfarce, transformação,
  futura poção de metamorfose, etc.) → `engine/entity_disguise.py::
  get_active_appearance_override()` + tabela `_APPEARANCE_SOURCES` — 1
  função checadora por fonte (lê o estado que a fonte já mantém, sem
  componente novo nem sync de rede novo), nunca um `if/elif` novo no
  render loop. `discover_sprite_variants(base_name)`/
  `get_animated_disguise_frame(base_name, ...)` são genéricas, parametrizadas
  por `base_name` — NUNCA hardcodear o nome de uma skill dentro dessas
  funções (classe de bug: 3 tentativas erradas em sequência até acertar,
  ver PROBLEMAS_ARQUITETURA.md §12/§13 — inclusive DEPOIS da regra
  "pesquisar antes de codar" já existir, o que prova que reciclar
  pesquisa de um tópico mais amplo não substitui pesquisar a decisão
  específica em jogo).
- **Descoberta de variantes numeradas de um asset** (sprite, imagem de
  fundo, qualquer arquivo onde "várias versões, escolhida em runtime"
  faz sentido) → SEMPRE probe sequencial via `os.path.exists`
  (`{base}_1`, `{base}_2`, ... parando no primeiro número ausente),
  NUNCA `glob` — ordem de glob não é garantida e pode incluir arquivo
  não relacionado por engano. Mesmo padrão em 2 lugares hoje:
  `engine/entity_disguise.py::discover_sprite_variants` (disfarce) e
  `client/habilidades_handlers.py::_discover_loading_bg_variants` (fundo
  da tela de loading, 19/10/2026 — `loading_screen.png` sem sufixo
  SEMPRE entra no sorteio também, nunca é tratado como "_1"). Numeração
  precisa ser CONSECUTIVA sem buraco — um `_2` sem `_1` nunca é
  encontrado. Se a escolha deve ficar FIXA pelo processo inteiro (não
  re-sortear a cada vez que a tela reaparece), cachear o PATH escolhido
  numa variável de módulo com sentinela dedicado (`_UNSET = object()`)
  — nunca `None` como "ainda não sorteei", porque `None` também é um
  resultado válido ("nenhum arquivo achado"). Ver
  HISTORICO_FEATURES.md §60.
- **Animação BASE de personagem** (idle/andar, qualquer direção, qualquer
  classe — não confundir com override TEMPORÁRIO de aparência acima) →
  `engine/character_animation.py::get_character_frame(class_id, state,
  direction, anim_time_ms)` — convenção de arquivo
  `assets/sprites/{class_id}_{idle|run|attack|<skill_id>}_{down|up|left|right}.png`
  (sheet cortado em frames QUADRADOS — largura=altura=altura real do
  sheet, 15/10/2026, revisão da regra de 09/09/2026, mesma convenção
  que `engine/mob_animation.py` já usa pra mob — `CHAR_TILE_SIZE`=96
  deixou de cortar sheet nenhuma, vira só o default de
  `character_anchor_offset()` pra chamador sem Surface em mãos.
  **Exceção, `state in _FIXED_WIDTH_STATES`** (`portal_in`/
  `portal_out`, sheet intencionalmente NÃO-quadrada — pilar de luz
  vertical estreito, 96×640) → continua largura FIXA `CHAR_TILE_SIZE`,
  nunca a regra quadrada — lista explícita pequena, NUNCA checar
  `state.startswith("portal")` ou prefixo parecido num call site novo,
  sempre consultar/estender essa mesma lista. `character_anchor_
  offset(frame_height, frame_width)` deriva `dx` de `frame_width/2`
  direto (tile de colisão centralizado por construção, independente da
  largura real) — qualquer call site que já tenha o Surface do frame
  em mãos passa `frame.get_width()` também, nunca só `get_height()`.
  Ver PROBLEMAS_ARQUITETURA.md §280.
  `class_id` sempre em português — mesmo valor de
  `CharacterStats.class_id`/
  `RemoteControlled.class_id`, nunca uma tradução nova). Implementação
  incremental por decisão do usuário (09/09/2026): arquivo ausente →
  `None` → `RenderSystem` cai pro retângulo colorido de sempre, nunca
  erro nem placeholder. `direction` vem de `TileMovement.facing`
  (gravado por `TileMovementSystem` — movimento real tem prioridade
  sempre; PARADO com `CombatState.target_entity_id` setado, vira pro
  alvo em vez de travar na última direção que andou, recalculado todo
  tick pra acompanhar um alvo que se move — 09/09/2026, bug real
  relatado pelo usuário: atacava um mob atrás dele "olhando" pra outro
  lado. Os dois casos via `engine.utils.facing_from_delta` — NUNCA
  recalcular esse delta em outro lugar). **Player REMOTO parado
  "olhando" pra direção errada** (07/10/2026, bug real relatado pelo
  usuário — `TileMovementSystem` já é genérico, roda em QUALQUER
  entidade com `CombatState`, mas o espelho de um player remoto nunca
  tinha esse componente, então o bloco "parado vira pro alvo" acima
  nunca disparava pra eles) → servidor sincroniza
  `CombatState.target_entity_id` de CADA player pros outros via
  `WorldServer.player_target_field(eid)` (mesmo padrão de
  `player_faction_field`/`player_light_field`, 5 pontos de payload de
  ENTITY_SPAWN — `_create_player_entity`/`get_players_in_aoi`
  (`world_server.py`), login + "entrou no meu AOI" (`session.py`),
  respawn (`respawn_system.py`)) + `_sync_player_hp_dirty` (dirty-check
  já existente, estendido pra incluir `target_entity_id` na tupla
  comparada — TROCA de alvo depois do spawn). Cliente anexa
  `CombatState` ao espelho remoto (`client/remote_entity_handlers.py::
  _spawn_remote_player_entity`) e aplica o campo em
  `_handle_msg_stats_update` — **NUNCA** `a.get(x) or b.get(x) or -1`
  pra resolver servidor→local (eid local `0`, entidade real válida, é
  falsy em Python — cairia no fallback errado); sempre `if x in dict:
  dict[x]` explícito, ou checar `x == -1` primeiro à parte de qualquer
  comparação com `_my_eid`. Nenhuma lógica de giro nova — o mecanismo
  já genérico passou a "ver" os remotos de graça. Ver
  PROBLEMAS_ARQUITETURA.md. `facing_from_delta` corta por
  ÂNGULO a partir do eixo vertical (0°=reto pra baixo/cima), não por
  magnitude relativa — down/up só dentro de
  `_VERTICAL_FACING_HALF_ANGLE_DEG` (20°) de cada lado do vertical;
  fora desse cone estreito (os 70° restantes de cada quadrante,
  inclusive a diagonal exata de 45°), left/right (10/09/2026, decisão
  do usuário — antes era um corte simétrico 45/45 via
  `abs(dy)>=abs(dx)`). "left"/"right" sem arquivo próprio herdam o espelho
  horizontal do lado oposto automaticamente — pintar só 1 dos 2 lados é
  suficiente. `RenderSystem` só tenta isso pra entidades com `class_id`
  resolvível (`CharacterStats` local ou `RemoteControlled` remoto) —
  mob/NPC continuam retângulo, sem mudança. **Estado exibido** (qual
  animação mostrar AGORA) nunca é decidido no `RenderSystem` direto —
  `engine/character_animation.py::resolve_display_state(tile_movement,
  class_id, now_ms)` é o ponto único (golpe > andando/parado; golpe
  expira sozinho após 1 ciclo do sheet, sem componente/sistema
  dedicado a "desligar"). **Animação de golpe** —
  `resolve_attack_state_name(class_id, sid, direction)` é a cascata de
  até 3 níveis pra golpe de SKILL (10/09/2026): sid específico
  (`{class_id}_{sid}_{direction}.png`) → "skill" genérico
  (`{class_id}_skill_{direction}.png`, pra qualquer skill SEM sprite
  própria — a maioria) → "attack" genérico
  (`{class_id}_attack_{direction}.png`, fallback ÚLTIMO). Auto-attack
  (`sid=""`) pula direto pro nível 3 — NUNCA passa pelo "skill"
  genérico, que é conceito exclusivo de skill. É ligada por
  `trigger_attack_anim(tile_movement, class_id, sid, now_ms, missed=
  bool)`, chamado de UM lugar só: `client/remote_entity_handlers.py::
  _apply_combat_result`, no momento em que o servidor confirma o
  RESULTADO do golpe (`source in ("auto","skill")`, nunca DoT/HoT —
  NÃO exige `damage>0`: miss/dodge/parry também animam, 10/09/2026,
  decisão do usuário) — NUNCA replicar esse gatilho em outro ponto do
  código (ex: no clique de ataque, ou no envio do AUTO_ATTACK/
  CAST_SKILL) — o servidor é quem confirma que o golpe aconteceu de
  verdade, o cliente só reage a essa confirmação. `missed=True` (outcome
  miss/dodge/parry) reusa a MESMA animação, só `MISS_ATTACK_SLOWDOWN`
  mais lenta (valor atual 50% — usuário testou 25% antes e achou pouca
  diferença perceptível) via `TileMovement.anim_attack_frame_ms` (novo
  campo, por-golpe — NUNCA o `DEFAULT_FRAME_MS` global pra golpe, que
  pode variar por trigger) — decisão explícita do usuário de NÃO criar
  uma animação dedicada de erro por enquanto; se um dia criar, o lugar
  certo é dentro de `resolve_attack_state_name`/`trigger_attack_anim`,
  nunca um branch novo espalhado. **Golpe em LOOP** (10/09/2026, pedido
  do usuário — Fatiador de Corpos dura 5s, gira o tempo todo): skills em
  `_LOOPING_ATTACK_SKILLS` (dict pequeno, nunca checar `sid ==
  "fatiador_de_corpos"` direto num call site novo) tocam o sheet em
  loop pela duração REAL da skill (lida de
  `SKILL_CATALOG[sid]["params"]["duration"]` — fonte única, NUNCA
  duplicar esse número aqui) via `TileMovement.
  anim_attack_loop_duration_ms`, em vez do padrão "1 ciclo só" — o
  gatilho de cada tick da skill (server-side, ex: `WorldServer`'s bloco
  de `fatiador_timer`) precisa incluir `sid` no payload de
  `_combat_this_tick`/COMBAT_RESULT pra o cliente saber qual skill é
  (bug real achado implementando — o tick do Fatiador nunca mandava
  `sid`, então o loop nunca ligava). **Golpe em LOOP tem prioridade
  sobre golpe concorrente de menor prioridade** (10/09/2026, bug real
  relatado: "a animação do tornado é cancelada quando o personagem se
  move") — `trigger_attack_anim` sobrescrevia `anim_attack_state`/
  `anim_attack_loop_duration_ms` sem checar NADA; um auto-attack normal
  (que o movimento provoca ao entrar em alcance de um mob — não é o
  movimento em si) chegando via `_apply_combat_result` DURANTE os 5s do
  Fatiador cancelava o loop na hora, bem antes do fim real. Fix: se já
  existe um loop ativo e ainda não expirado (`_attack_loop_still_active`),
  um trigger novo SEM duração de loop própria (`_looping_attack_duration_ms
  (sid) or 0 == 0`) é ignorado — só um loop NOVO (o mesmo sid de novo, ou
  uma skill de loop diferente) substitui um loop ainda ativo. NUNCA
  reverter pra "sempre sobrescreve" achando que simplifica — é exatamente
  essa simplicidade que causava o bug.
  **Animação de CAST (windup) separada da de LANÇAMENTO** (15/10/2026,
  pedido do usuário — "uma animação pro cast, outra pro lançamento",
  pra QUALQUER classe com skill de cast_time, não só mago) —
  `engine.character_animation.trigger_cast_anim(tile_movement,
  class_id, sid, now_ms)` liga o estado `"cast"` (cascata de 2 níveis,
  `resolve_cast_state_name`: `f"{sid}_cast"` específico → `"cast"`
  genérico → `None`, sem arte = idle/run normal) **ESTICADA** (não em
  loop, 15/10/2026, pedido do usuário — mesma técnica já usada por
  `client/portal_handlers.py::_begin_portal_in_anim` pro cast de
  portal: `frame_ms = duração_ms / nº_frames`, `anim_attack_loop_
  duration_ms = 0`, sheet toca 1 ciclo só esticado pra caber
  exatamente na duração REAL de `cast_time`, SKILL_CATALOG, fonte
  única) — `anim_attack_hold_last_frame=True` segura na última frame
  se o lançamento demorar mais que a estimativa local (rede sempre tem
  alguma folga/latência). **NUNCA voltar a usar o mecanismo de LOOP
  pro cast** — como `anim_attack_loop_duration_ms` fica sempre 0,
  `_attack_loop_still_active()` nunca o protege contra sobrescrita,
  então o lançamento sempre interrompe livremente, sem precisar de
  nenhuma flag/exceção especial (golpe em LOOP de verdade, ex: Fatiador
  de Corpos, continua 100% protegido pela regra de prioridade antiga,
  intocada). **NUNCA chamar isso de um clique/input local** — gatilho
  é SEMPRE
  `SKILL_EFFECT{event:"cast_start"}` (`client/network_handlers.py::
  _handle_msg_skill_effect`), evento que já existia só pra som e já é
  broadcast AOI pro caster local E qualquer observador, sem precisar
  de mensagem de rede nova. **Lançamento reusa a cascata de golpe JÁ
  EXISTENTE** (`attack`/`skill`/`{sid}`, `resolve_attack_state_name`)
  — nenhum nome novo, só o GATILHO mudou: `trigger_attack_anim()`
  passou a disparar (e, pra skill com `sid`, EXCLUSIVAMENTE) nos
  eventos `SKILL_EFFECT{event:"launch"|"impact"|"miss"}` — `"launch"`
  é o momento do projétil sair (skill com projétil, impacto físico
  real vem DEPOIS via COMBAT_RESULT separado); `"impact"`/`"miss"` é
  pra skill SEM projétil (cast completo = resolução imediata) E pra
  skill instantânea sem cast_time nenhum. `client/remote_entity_
  handlers.py::_apply_combat_result` (COMBAT_RESULT) **NUNCA mais
  dispara golpe pra skill com `sid`** — só auto-attack puro
  (`sid==""`) continua ali; disparar nos 2 lugares duplicaria a
  animação pra skill de projétil (lançamento + impacto). Ver
  PROBLEMAS_ARQUITETURA.md §281/§284.
  **Cast CANCELADO (movimento, rejeição do servidor) tem que chamar
  `clear_attack_anim_state(tm)` explicitamente** (19/10/2026) — como
  `anim_attack_hold_last_frame=True` nunca expira sozinho, um cast
  cancelado nunca recebe o `"launch"/"impact"/"miss"` que normalmente
  desligaria a pose, e o personagem fica travado pra sempre. Os 2
  pontos reais que removem `SpellCast`/zeram `is_casting` já chamam:
  `ui/spell_system.py::SpellCastSystem.update()` (cancelamento LOCAL
  por movimento) e `client/network_handlers.py::
  _handle_msg_skill_result` (rejeição server-authoritative tardia).
  Mecanismo genérico por `sid`, afeta mago e arqueiro igualmente — NUNCA
  um 3º ponto que remova `SpellCast` sem o clear correspondente.
  `Channeling` (Calamidade Flamejante) fica de fora — `cast_time=0.0`
  no catálogo, `cast_start` nunca dispara pra ela. Ver
  HISTORICO_BUGS.md §94.
  **Animação de MORTE do player** (15/10/2026, pedido do usuário) →
  `engine.character_animation.trigger_death_anim(tile_movement,
  class_id, now_ms)` — mesma técnica do mob (toca 1 ciclo, trava no
  último frame via `anim_attack_hold_last_frame=True`, nunca expira
  sozinha), convenção `{class_id}_death_{direction}.png`, sem cascata
  de sid. Gatilho: `PLAYER_DEATH` (local, `client/network_handlers.py::
  _handle_msg_player_death`) e `ENTITY_DEATH` (remoto, `_handle_msg_
  entity_death`) — ambos já existiam, nenhuma mensagem nova.
  **`clear_attack_anim_state(tile_movement)` (genérico) tem que ser
  chamado em TODO ponto onde o personagem deixa de estar morto** —
  estado com hold NUNCA expira sozinho: `_handle_msg_player_revive`
  (local), transição `GhostState.is_ghost` False→True (local,
  "Liberar espírito" — branch de render de fantasma nem olha pra esse
  estado, mas limpar evita sobra pro PRÓXIMO ciclo de vida), e
  transição de HP 0→positivo de player remoto no handler de
  STATS_UPDATE (não existe mensagem "reviveu" dedicada pro remoto).
  `ui/render_system.py::_is_corpse_draw` só usa o frame de morte
  quando `resolve_display_state()` devolve LITERALMENTE
  `CHAR_DEATH_STATE` — nunca só "frame não é None", ou cairia no
  idle/run "respirando" pra classe sem sprite de morte ainda, em vez
  do retângulo cinza de fallback certo. Ver PROBLEMAS_ARQUITETURA.md
  §285.
  **Golpe/cast em 2 FASES (preparação → congela num frame do meio →
  solta): tentado e REVERTIDO no mesmo dia** (15/10/2026, ver
  PROBLEMAS_ARQUITETURA.md §286/§287/§288) — congelar a animação num
  frame fixo esperando um evento de rede (`trigger_anticipation_anim`/
  `release_anticipation_anim`, removidos) tem prioridade sobre
  idle/run por construção (`resolve_display_state`), então o
  personagem ficava visualmente "preso" na pose de preparação mesmo
  depois do jogador já ter voltado a andar — bug real relatado em
  playtest. Usuário optou por resolver na ORIGEM (redesenhar as folhas
  de sprite pra que o frame de soltar o golpe/flecha fique no INÍCIO
  do ciclo, não no fim) em vez de ensinar a máquina de estados a ceder
  pra movimento no meio da espera. Se uma técnica de "congelar num
  frame esperando um evento externo" voltar a ser necessária, ler
  §286/§287/§288 inteiras antes de reimplementar — o mecanismo em si
  funcionava (testes passavam), o problema era a interação com
  movimento livre durante a espera; qualquer tentativa nova precisa
  responder "o que mostrar se o jogador andar enquanto espera?" antes
  de codar, nunca assumir "fica parado" sem confirmar com o usuário.
  **Animação BASE de MOB** (idle/run/attack × 4 direções — não
  confundir com animação de PERSONAGEM jogável acima, que é um sistema
  IRMÃO separado, nunca estendido pelo mesmo módulo) →
  `engine/mob_animation.py::get_mob_frame(mob_key, state, direction,
  anim_time_ms)` (10/10/2026). Chave de animação = `engine.utils.
  slugify(EntityIdentity.mob_key or race)` — já chega pela rede hoje no
  payload de spawn (`race`), nenhum campo de protocolo novo. Frame é
  QUADRADO, derivado da altura do próprio sheet (`largura = altura,
  contagem = largura_total // altura`, mesma convenção de
  `ui/world_icon_animator.py`) — NUNCA a largura fixa de personagem
  (`CHAR_TILE_SIZE`), mob varia demais de tamanho (rato a dragão) pra
  isso fazer sentido. Convenção de arquivo:
  `assets/sprites/{slug}_{idle|run|attack}_{direction}.png` — fallback
  por-arquivo igual ao personagem (ausente → `None` →
  `ui/render_system.py` cai pro retângulo colorido de sempre, por
  ESTADO/direção específico, nunca tudo-ou-nada). Golpe (`trigger_mob_
  attack_anim`, chamado de `client/remote_entity_handlers.py::
  _apply_combat_result` quando o atacante é mob) é SEM cascata de sid
  (mob só tem auto-attack hoje, sem skill própria com sprite dedicada)
  — se um dia mob tiver skill com sprite própria, a cascata de 3 níveis
  de `resolve_attack_state_name` (personagem) é o modelo a copiar, não
  reinventar. `slugify()` é ponto único de verdade (`engine/utils.py`)
  — qualquer 3º ponto que precise de chave de arquivo a partir de nome
  legível usa essa função, nunca uma normalização própria.
  **Qualquer feature visual nova de mob (nameplate, ícone, efeito)
  precisa ser conferida nos DOIS caminhos de desenho de mob, nunca só
  um**: `ui/render_system.py` (sprite/corpo de QUALQUER entidade com
  Position+Renderable, inclusive mob remoto) E `client/
  remote_entity_handlers.py::_draw_mob_hp_bars` (HUD/nameplate — todo
  mob em produção é remoto, já que não existe mob 100% local em
  multiplayer; `combat_stats` é removido dele de propósito, então o
  bloco de HUD de `render_system.py` nunca roda pra mob de verdade).
  Bug real: topo do nameplate só corrigido no 1º arquivo, continuou no
  meio do sprite até corrigir o 2º. Ver PROBLEMAS_ARQUITETURA.md §267.
  **Topo do nameplate de mob usa `engine.mob_animation.
  get_mob_top_opaque_offset(mob_key)`, nunca o topo CRU do frame** —
  frame quadrado de mob costuma ter padding transparente real acima da
  cabeça (conferido no Goblin Guerreiro: frame 64×64, conteúdo só
  começa na linha ~13), então "topo do frame + gap pequeno" ainda
  deixava o nameplate flutuando longe da cabeça. Mede o pixel opaco
  mais alto da animação "idle"/"down" 1x e CACHEIA por mob_key — nunca
  recalcula pela pose atual (evitaria "balançar" entre frames, mesmo
  motivo do personagem usar um inset FIXO; aqui o valor é DERIVADO da
  arte real de cada mob em vez de um número universal, porque
  proporção varia demais — rato a dragão). Ver PROBLEMAS_ARQUITETURA.md
  §268.
  **Tile de colisão de mob = CENTRO geométrico do frame, nunca a base**
  (10/10/2026) — frame quadrado pensado como N×N tiles de 32px (ex:
  goblin 64×64 = 2×2), `position.x/position.y` mapeiam pro CENTRO do
  frame inteiro (`_mblit_y = draw_y - mh/2`), não mais ancorado pela
  base como o retângulo antigo — mesma filosofia do personagem (ancora
  pelo CENTRO do tile de colisão, nunca pela base/pé), pedido explícito
  do usuário comparando os dois. Ver PROBLEMAS_ARQUITETURA.md §269.
  **`resolve_mob_anim_state` reusa `character_animation.
  resolve_anim_state` direto, nunca reimplementa a checagem de
  `is_moving` sozinha** — `anim_walk_grace_s` é um campo GENÉRICO,
  ligado por `TileMovementSystem` (`engine/world_systems.py`) ao fim de
  QUALQUER passo de QUALQUER entidade com `TileMovement`, inclusive
  espelho de mob remoto (que só recebe o próximo passo quando o pacote
  `ENTITY_MOVE` seguinte chega pela rede — gap real, não só "1 frame
  local" como no player). Sem o grace, mob perseguindo "pisca" pra
  idle a cada passo. Ver PROBLEMAS_ARQUITETURA.md §270.
  **Morte de MOB com animação "death" (11/10/2026)** — toca 1x e
  TRAVA no último frame (`engine.mob_animation.trigger_mob_death_anim`,
  `anim_attack_hold_last_frame=True`, consumido por
  `resolve_mob_display_state`), nunca volta pra idle/run sozinha.
  Convenção de arquivo igual aos outros estados:
  `{slug}_death_{direction}.png`. Dois destinos depois da animação,
  SEM mudar nada do servidor (100% cosmético no cliente):
  - **Sem loot**: `client/remote_entity_handlers.py::
    _despawn_mob_or_play_death` é o ÚNICO ponto que decide remover um
    mob local de vez ou (kill real + sprite "death" disponível) adiar
    a remoção até a animação terminar 1 ciclo
    (`get_mob_death_anim_duration_ms`) — usado pelos 2 lugares que
    antes removiam direto (`AOI_UPDATE` despawn em `client/
    network_handlers.py`, e `_flush_pending_mob_despawns`). Despawn por
    SAÍDA de AOI (não-kill) NUNCA anima — remove na hora, igual sempre.
    Mob sem sprite "death" ainda → comportamento de hoje, zero
    regressão.
  - **Com loot**: `ui/loot_system.py::render_world()` desenha o ÚLTIMO
    frame congelado (`get_mob_death_last_frame`) no lugar da elipse de
    sempre, pelo MESMO tempo que o `Corpse` já durava (nenhuma mudança
    nos timers de loot) — `Corpse.mob_key`/`Corpse.facing` (campos
    novos, default `""`/`"down"` = elipse de sempre) guardam qual mob
    morreu ali. Identidade chega via uma ponte client-only por tile
    (`GameEngine._pending_death_race_by_tile`, janela curta — mesmo
    princípio de `_pending_loot_redirect` já existente), gravada em
    `_despawn_mob_or_play_death` e consumida por `client/
    network_handlers.py::_handle_msg_loot_available` — NUNCA um campo
    novo no servidor/protocolo, a identidade já é conhecida do cliente
    antes do mob morrer. Qualquer cache novo deste tipo entra em
    `GameEngine._clear_remote_entity_caches()` no MESMO commit — já
    feito pra `_dying_mobs`/`_pending_death_race_by_tile`. Ver
    PROBLEMAS_ARQUITETURA.md §271.
  **Marcador genérico de "tem loot aqui" (sem sprite de morte
  conhecido)** (11/10/2026) → `ui/world_icon_animator.py::get_frame(
  "loot", 32, now_ms)` — reusa o MESMO módulo do marcador animado de
  quest (categoria de asset "ícone de mundo", nenhum módulo novo
  precisou nascer), chamado de `ui/loot_system.py::render_world()` no
  lugar da elipse de 3 cores antiga. MESMA animação pra "tem gold" e
  "só tem item" (decisão do usuário — sem tingimento, mais simples que
  replicar a distinção de cor antiga). `Corpse` SEM loot nenhum (`coins
  <= 0 and not loot` — já saqueado por completo, ainda dentro do timer
  de decay curto) → **não desenha nada**, nem elipse nem animação
  (antes desenhava uma elipse escura). Prioridade de desenho de
  `render_world`: sprite de morte do mob (se `Corpse.mob_key`
  resolver) > animação genérica de loot > elipse de 2 cores (fallback
  só se `loot.png` falhar ao carregar, nunca alcançado em uso normal).
  **`client/remote_entity_handlers.py::_draw_remote_corpses` é um 2º
  caminho de desenho SEPARADO pro mesmo marcador** (desenha pra quem
  NÃO tem `_available_loot[corpse_id]` — outro player sem entitlement
  de loot pessoal/condicional) — SEMPRE pular (`continue`) quando
  `_available_loot.get(corpse_id) is not None`, porque `ui/
  loot_system.py::render_world()` já desenhou essa posição com mais
  detalhe; nunca redesenhar a mesma marca aqui. Mesma classe de bug já
  documentada pro nameplate (§267/§268) — bug real: corrigir só
  `loot_system.py` não bastou, a elipse antiga continuava aparecendo
  por cima via este 2º caminho. Ver PROBLEMAS_ARQUITETURA.md §272.
  **Mob PARADO vira de frente pro alvo, mesmo mecanismo do player**
  (11/10/2026) — `TileMovementSystem` (`engine/world_systems.py`) já
  virava `facing` pro alvo quando uma entidade parada tem
  `CombatState.target_entity_id` setado (mecanismo genérico, criado pro
  player), mas mob NUNCA escrevia esse campo (só `AIControlled.
  target_eid`, usado pela IA, nunca sincronizado nele). Fix: servidor
  manda `target_eid` no payload de spawn de mob
  (`WorldServer._build_mob_spawn_payload`) e num snapshot a cada tick
  (`WorldServer._collect_mob_targets()`, mesmo padrão de
  `_collect_mob_effects` — nunca um dirty-check/buffer separado,
  já cai dentro do `deltas` existente, sem precisar de entrada nova em
  `has_pending`); cliente anexa `CombatState` ao espelho do mob
  (`_spawn_remote_mob`) e sincroniza (`_sync_mob_targets`, mesmo padrão
  de limpar-quando-não-reportado que `_sync_mob_effects` já tem).
  `_resolve_server_eid()` (novo, `client/remote_entity_handlers.py`) é
  o ponto único pra resolver eid-do-servidor → eid-local (self/
  _remote_players/_remote_mobs) — usado pelos 2 pontos novos, NUNCA
  reimplementado por um 3º/4º call site futuro (os 2 já existentes do
  player, mais antigos, continuam com a cópia inline deles — não
  mexidos por escopo). `server/session.py::_merge_deltas_into` precisa
  tratar `mob_targets` como "foto do estado atual" (mesma regra de
  `effects`/`mob_effects` — substitui, nunca concatena) sempre que um
  campo novo desse tipo nascer. Ver PROBLEMAS_ARQUITETURA.md §273.
  **Direção inicial de mob hostil é ALEATÓRIA, não fixa em "down"**
  (13/10/2026) — sorteada dentro de `create_enemy()` (NUNCA
  `_build_combat_entity`, que também serve NPC de serviço/guarda —
  esses continuam sempre "down" de propósito), sincronizada via
  `"facing"` em `_build_mob_spawn_payload` + aplicada em
  `_spawn_remote_mob` (sobrescreve o sorteio independente que a
  chamada LOCAL de `create_enemy()` no cliente também faz — sem isso,
  cada jogador veria o mesmo mob virado pra um lado diferente).
  Qualquer campo cosmético novo desse tipo (sorteado na criação,
  precisa ser IGUAL pra todo jogador) segue o mesmo padrão: sorteia
  SÓ no servidor, manda o valor pela rede, cliente nunca sorteia o
  próprio — mesmo princípio de "servidor é a única fonte de verdade"
  de qualquer outro campo de combate. Ver PROBLEMAS_ARQUITETURA.md
  §276.
  **Kit de animação PRÓPRIO pra um boss específico dentro de uma
  `SpawnZone`** (ex: boss de dungeon com sprites únicos, diferente do
  genérico da raça) → campo opcional `"spritesheet"` no JSON do mapa
  (`_entities.json::spawn_zones[].spawns[]` ou no nível da zona, mesmo
  fallback por-spawn/por-zona que `name`/`race`/`class` já têm) →
  `SpawnZone.spritesheet` → `create_enemy(sprite_key=...)` →
  `EntityIdentity.sprite_key` (13/10/2026). `race` continua definindo
  STATS/SONS/MOB_TABLE normalmente — só a chave de ANIMAÇÃO muda.
  Qualquer ponto que precise saber "qual chave de animação usar pra
  este mob" chama `engine.mob_animation.resolve_mob_sprite_key(ident)`
  (prioridade `sprite_key > mob_key > race`) — NUNCA reescrever
  `ident.mob_key or ident.race` inline de novo num call site novo
  (eram 4 cópias dessa expressão antes desta função existir: sprite em
  si, gatilho de golpe, ponte de sprite de morte do corpse,
  posicionamento de nameplate — mesma classe de bug de "2 caminhos de
  desenho, só 1 corrigido" já documentada em §267/§272). Sincronização
  de rede: `_build_mob_spawn_payload` manda `"sprite_key"` só quando
  não-vazio (mesmo padrão condicional de `"name"`); `_spawn_remote_mob`
  aplica no espelho — sem isso, o espelho remoto nunca saberia do
  override (mesmo princípio de "servidor é a única fonte de verdade"
  do bullet de direção aleatória acima). Só `create_enemy()` ganhou o
  parâmetro — NPC de serviço/guarda não precisam, sem mudança neles.
  Ver PROBLEMAS_ARQUITETURA.md §277.
  **Âncora de desenho e Y-sort** (10/09/2026, decisão do usuário): cada
  frame tem um tile de colisão/pé REAL de 32×32 (`CHAR_COLLISION_SIZE`
  == TILE_SIZE) no canto `(CHAR_COLLISION_OFFSET_X, CHAR_COLLISION_OFFSET_Y)`
  do frame — NÃO o frame inteiro (96×H). `character_anchor_offset()`
  (centro desse tile dentro do frame) é o ÚNICO ponto que sabe converter
  isso pra pixels de blit; `RenderSystem` alinha esse centro com
  `position` (a posição lógica ECS da entidade) tanto pro blit quanto
  pro Y-sort (`position.y + CHAR_COLLISION_SIZE/2`) — NUNCA usar a
  largura/altura crua do frame nem `renderable.height` (convenção
  antiga do retângulo, 24px) pra nenhum dos dois. Vale mesmo quando a
  direção específica ainda cai no retângulo de fallback (arte não
  existe pra aquele estado/direção ainda) — Y-sort não pode pular
  dependendo de qual arte já foi desenhada.
- **Preview de personagem/classe FORA do jogo** (telas de seleção/
  criação de personagem, `ui/char_creation_screen.py`, ou qualquer tela
  pré-jogo futura que precise mostrar "como é" uma classe) →
  `_get_class_preview_frame(class_id, sc)` — reusa `engine.
  character_animation.get_character_frame(class_id, "idle", "down",
  pygame.time.get_ticks())`, o MESMO mecanismo de animação do jogo,
  NUNCA um sistema de animação próprio pra telas de menu. Devolve
  `None` se não existir sprite ainda pra essa classe → cai pro
  retângulo de cor antigo (`_CLASS_COLORS`), mesmo padrão de fallback
  usado no resto do projeto. Frame real é 64x64 mas o personagem
  desenhado só ocupa os 32x32 centrais (mesma convenção de
  `CHAR_COLLISION_SIZE`) — usa o FRAME INTEIRO, nunca recorta/dá zoom
  tentando "preencher" uma caixa pequena (19/10/2026, decisão explícita
  do usuário) — `_CLASS_PREVIEW_BASE_SIZE` (128 desde §292, era 64)
  é o único lugar que define esse tamanho, nunca duplicar o número.
  **Nunca um deslocamento hardcoded pra texto/elemento vizinho ao
  preview** — bug latente evitado: o texto ao lado do ícone na tela de
  seleção usava `r.x + 64*sc` fixo, que só "funcionava" porque 64 era o
  valor exato do tamanho de preview da ÉPOCA por coincidência. 2
  padrões diferentes, por design, não por descuido (cada tela escolhe
  o que faz sentido pro próprio layout):
  - **Criação** (`_draw_creation`, atlas de UI, cards lado a lado):
    nome/descrição fluem logo ABAIXO do boneco visível
    (`_CRE_PREVIEW_TOP` + 32px RAW), e a altura do card vem do conteúdo
    medido (`_cre_layout`) — cresce sozinha se o texto crescer, nunca
    sobrepõe.
  - **Seleção** (`_draw_selection`, atlas de UI, molduras de altura
    FIXA de 3 tiles) — texto numa coluna fixa em px RAW
    (`_SEL_TEXT_X`), nunca derivada do tamanho real do preview; o
    sprite é desenhado por ÚLTIMO no conteúdo da lista (por cima das
    molduras vizinhas, recortado só pela área visível). **Sempre que
    `_CLASS_PREVIEW_BASE_SIZE` mudar, conferir que o personagem
    visível ainda cabe à esquerda de `_SEL_TEXT_X`** — os 2 números
    não são derivados um do outro (ver HISTORICO_FEATURES.md §69).
  Ver PROBLEMAS_ARQUITETURA.md §291/§292.
- **Entry point server-side novo que executa handler compartilhado em nome
  de um player** (skill/spell/projectile/channeling) →
  `WorldServer.register_map_services_for(player_eid)` ANTES do handler —
  aponta `_svc` (is_tile_walkable/find_path/get_tilemap) pro bundle do MAPA
  do player (classe de bug: `_svc` fica no último mapa carregado; Interceptar
  "bloqueado" em terreno aberto, Tiro Repulsivo stunando em parede fantasma).
  NUNCA pegar "o primeiro" `Tilemap` do world — usar o bundle via
  `get_entity_map(eid)`.
- **Colisão sub-tile pro passo manual (objeto pequeno bloqueando o tile
  de 32px inteiro sem necessidade): tentado e REVERTIDO** (30/08/2026,
  ver PROBLEMAS_ARQUITETURA.md §85) — 4 rodadas de correção sem
  convergir (matemática de fronteira de tile, passo encadeado, gate de
  rede, `current_tile_x/y` podendo apontar pra tile sólido) até o
  usuário decidir que o custo de manutenção não compensava o ganho
  visual. Se o tema voltar, ler o histórico completo em §85 ANTES de
  tentar de novo — o caminho "meio-passo de 16px como fallback perto de
  props pequenos" já foi tentado e tem uma lista concreta de armadilhas
  documentada, não é ponto de partida em branco.
- **`TileMovement.current_tile_x/y` só muda quando `TileMovementSystem`
  completa o tween** (`progress>=1.0`, `engine/world_systems.py:4273-4277`)
  — NUNCA escrever `current_tile_x/y` direto pra "adiantar" a posição
  lógica de uma skill que move o player (dash/gap-closer), mesmo que
  pareça conveniente pra destravar um range-check logo em seguida. Esse
  campo é o MESMO usado por `_resolve_auto_attack`
  (`server/combat_processor.py`) pra medir alcance de ataque automático
  — um snap antecipado faz o auto-attack (e qualquer skill futura)
  achar que o player já chegou, disparando ANTES da animação do dash
  terminar (bug real relatado pelo usuário 31/08/2026: "o alvo já toma
  um hit antes de o dash acontecer" — existia desde a implementação
  original do Interceptar, 24/05/2026; ver PROBLEMAS_ARQUITETURA.md
  §87). Pra notificar rede que a skill moveu o player, `target_tile_x/y`
  (que muda na hora, de propósito) já basta — nunca precisa do snap de
  `current_tile_x/y` junto.
- **Dash em andamento (`TileMovement.is_dash and is_moving`) tem
  autoridade exclusiva sobre a posição** — `WorldServer.move_player()`
  recusa qualquer MOVE normal enquanto essa condição for True (mesma
  simetria de `TileMovementSystem` já recusar que stun/sleep interrompa
  um dash). NUNCA remover essa checagem "pra simplificar": sem ela, um
  MOVE residual (comum — jogador andou até o alcance mínimo do
  Interceptar, cliente ainda tinha passo WASD enfileirado) sequestra o
  destino do dash — o alvo continua selecionado, mas a skill seguinte
  recusa "Nenhum alvo" porque a posição REAL não bate mais com o destino
  calculado (achado real 31/08/2026, ver PROBLEMAS_ARQUITETURA.md §87 —
  3ª causa raiz da mesma investigação do Interceptar).
- **Decidir uma partida de BG (declarar vencedor + montar placar) tem 1
  função por sistema, nunca lógica solta num novo call site** —
  `BgQueueProcessorMixin._finish_bg_match(match, match_id,
  winner_faction)` (fila real, `server/bg_queue_processor.py`) e
  `debug_battleground._finish_debug_match(ws, winner_faction)` (`/testbg`,
  01/09/2026) — os dois extraídos do código que já montava o placar
  quando o Nexus caía (`notify_bg_queue_nexus_destroyed`/
  `notify_nexus_destroyed`), reaproveitados também por
  `request_bg_forfeit`/`_forfeit` (comando `/forfeit`/`/ff`). Motivo
  novo pra decidir uma partida (item mágico, timeout, etc.) chama a
  função certa do sistema certo — NUNCA reimplementa o cálculo de
  K/D/A/Farm/Gold/Dano por delta contra `stat_snapshots`. Client-side:
  `ArenaHandlers._try_handle_arena_chat_command` só faz `return True`
  (consome "/ff") quando de fato numa partida de Arena — fora disso
  `return False` (passa a vez) — NUNCA fazer um interceptor de comando
  de chat "engolir" um comando alheio só porque a string bate; outro
  handler mais abaixo na cadeia (`client/chat_handlers.py::
  _send_chat_message`) pode ser o dono de verdade daquele comando num
  contexto diferente.
- **"Este player está dentro de alguma instância agora?" (Arena, fila
  REAL de BG, BG de teste `/testbg`, dungeon)** →
  `WorldServer.player_in_any_instance(player_eid)` (11/10/2026) — ponto
  único que verifica os 4 tipos juntos (`_player_match_id`/
  `_player_bg_match_id`/`_player_dungeon_instance`/`debug_battleground.
  _state["members"]`). Usar SEMPRE que uma ação nunca deve tirar o
  player de uma instância sem passar pelo fluxo de saída certo dela
  (ex: "Voltar ao Spawn" do menu de pausa) — NUNCA checar só
  `_player_match_id` (só Arena) achando que cobre "instância" em geral,
  classe de bug real: o guard de `_handle_unstuck` só verificava Arena,
  então usar o botão dentro de uma partida de BG puxava o player pro
  spawn do mundo aberto, fora da instância, enquanto o servidor ainda o
  considerava "dentro" dela. Não serve pra decidir "deve reviver
  automático?" (isso é `GhostState.auto_respawn`, calculado em
  `RespawnMixin._handle_player_death`, SÓ `_in_dungeon`/`_in_bg` — Arena
  usa fantasma manual de propósito, nunca auto-respawn) — são 2
  perguntas relacionadas mas DIFERENTES, não reusar uma pela outra.
  Ver PROBLEMAS_ARQUITETURA.md §274.
- **Largura de mapa (`Tilemap.map_width_tiles`) é a MAIOR linha do
  `terrain_matrix`, nunca a linha 0** — `engine/entity_factory.py::
  create_tilemap` (02/09/2026, bug real: usava só `len(terrain_matrix[0])`,
  subestimando a largura de `map_1`, que tem forma ORGÂNICA/não-retangular
  de propósito — toda linha começa/termina com "m"/montanha, borda
  natural, só a extensão real do terreno varia por altura, não é CSV
  corrompido). Linha mais curta que a máxima é preenchida com
  `FLOOR_TILE` automaticamente (mecanismo já existia, só recebia o
  número errado) — comportamento correto pra borda de mapa
  não-retangular. `engine/map_loader.py::validate_map` não compara mais
  largura de linha contra nada — não existe uma "largura esperada"
  única que sirva pra qualquer formato de mapa. Ver
  PROBLEMAS_ARQUITETURA.md §97.
- **Janela de tiles visível pra varrer `object_matrix` (objeto/prop
  maior que 1 tile)** → `ui/tile_render_system.py::TileRenderSystem.
  _object_visible_tile_range()` — NUNCA recalcular "tela em tiles + N"
  na mão num call site novo. Objeto é ancorado na BASE e o sprite
  cresce PRA CIMA/DIREITA a partir dali; a margem de corte precisa do
  tamanho do MAIOR sprite do catálogo inteiro
  (`_object_scan_margin_tiles()`, derivado de `OBJECT_MAPPING`, nunca
  um número fixo) só nas direções ESQUERDA/BAIXO — objeto ancorado
  acima/à direita da tela só cresce pra mais longe dela. `get_world_
  objects()`/`render_static_objects()` reusam o mesmo método (bug real:
  os 2 duplicavam "tela + 2" fixo, prédio/árvore alto só aparecia
  quando a câmera alcançava a base dele, ver PROBLEMAS_ARQUITETURA.md
  §102). `TileRenderSystem.render()` (terreno, sempre 1×1) não precisa
  disso.
- **Catálogo com id sequencial puro sem separador (ex: `"vb001"`, não
  `"vb_001"`)** → `"no_prefix_separator": True` no dict do grupo/família
  em `OBJECT_SHEET_FAMILIES`, NUNCA tirar o `"_"` direto em
  `_register_catalog_tiles`/`tile_id = f"{prefix}_{id_suffix}"` — esse
  separador é usado por TODAS as outras famílias do catálogo (ids já
  referenciados em mapas reais), tirar sem o opt-in quebraria id de
  cada uma delas. `"prefix"` continua igual nas outras famílias, só
  `"TX Village Building with Shadow"` usa a flag hoje. **`engine/
  tileset.py::group_tile_ids()` tem que ler a MESMA flag** (bug real —
  ela reimplementava a junção `prefix_id` em paralelo, sem saber da
  flag nova; aba Objetos do God Mode ficou vazia pra família inteira
  até corrigir as 2 funções juntas, ver PROBLEMAS_ARQUITETURA.md §104).
  Qualquer 3º ponto futuro que precise montar um id de catálogo na mão
  (em vez de ler de `OBJECT_MAPPING`/`OBJECT_SHEET_TILE_MAP` já
  registrado) tem que consultar essa mesma flag também. Ver
  PROBLEMAS_ARQUITETURA.md §103.
- **`AIControlled.is_blocked` (mob tentando andar pro tile exato do
  player) é resetado a CADA tick por TODO estado de mob que o consulta
  — nunca só pelo estado onde a flag nasce.** `engine/world_systems.py::
  EnemyAISystem` grava essa flag dentro de `is_tile_walkable()` (efeito
  colateral de baixo nível); CHASING/ATTACKING/etc. já resetam ela tanto
  no topo do loop quanto no recálculo de path, mas RETURNING não
  resetava em NENHUM dos seus 2 pontos de entrada — um mob que virasse
  `is_blocked=True` bem no tick em que perdeu o alvo ficava preso em
  RETURNING pra sempre (path válido recalculado sem nunca mover).
  Qualquer flag nova desse tipo (efeito colateral de função de baixo
  nível, consumida por vários estados de uma máquina de estados) precisa
  da mesma auditoria: listar TODOS os estados que a leem, não só o
  "óbvio". Ver PROBLEMAS_ARQUITETURA.md §130.
- **Parser de célula de mapa (`engine/map_loader.py::_parse_terrain_cell`)
  nunca devolve um token desconhecido de vários caracteres como
  `terrain_char`.** `terrain_matrix` é sempre 1 char por tile
  (`"".join(terrain_row)`) — um token de sheet não cadastrado em
  `SHEET_TILE_MAP` (família removida do catálogo, nunca cadastrada, erro
  de digitação no editor) cai pra `"G"`, nunca pro valor bruto — senão
  infla a linha inteira e desalinha todo tile seguinte (colisão E
  `map_width_tiles`, usado por minimapa/câmera/limites). Qualquer parser
  novo de formato com invariante de tamanho fixo precisa da mesma
  disciplina: fallback seguro pro "não reconheço", nunca repassar o dado
  bruto assumindo que ele cabe no formato esperado. Ver
  PROBLEMAS_ARQUITETURA.md §131.
- **Mecânica nova "no ponto de morte" (skill com `on_kill`, XP, gold,
  qualquer coisa que reage a algo morrendo) precisa ser aplicada nos 2
  caminhos de morte que o projeto tem, DESDE SEMPRE, por design —
  mob/minion/torre (`ServerDeathHandler.update()`, `server/
  server_death_handler.py`) e jogador/PvP (`RespawnMixin.
  _handle_player_death`, `server/respawn_system.py`, único lugar que
  credita kill/assist de player).** São sistemas SEPARADOS que nunca
  foram unificados — implementar só num deles e assumir que "morte é
  morte" é a causa raiz já catalogada duas vezes (§130 conceitualmente,
  §132 de fato). Skills com `on_kill=="charge"` (Vitória Iminente) →
  `grant_on_kill_charge_skills(world, world_server, killer_eid,
  mob_eid)` (`server/server_death_handler.py`, função de MÓDULO, não
  método de mixin — chamável dos dois pontos sem depender de herança) —
  NUNCA reimplementar a concessão de carga num 3º lugar. Ver
  PROBLEMAS_ARQUITETURA.md §132.
- **Perseguição genérica do mago pra usar uma skill (`ui/player_input_system.py`)
  para na distância da skill ESPECÍFICA que o jogador apertou
  (`CombatState.pursuit_skill_range`, client-only), nunca no mínimo
  genérico entre todas as skills da hotbar** (`_mage_attack_range()`) —
  esse mínimo só é fallback pra quando nenhuma skill foi apertada ainda
  (engajou só por clique direito/Espaço). Mudar o `cast_range` de UMA
  skill (ex: Calamidade Flamejante) nunca deve travar a distância de
  perseguição de QUALQUER outra skill da mesma classe — bug real: uma
  skill de mira curta na hotbar fazia o personagem parar de andar cedo
  demais mesmo tentando usar uma skill de alcance maior. Ver
  PROBLEMAS_ARQUITETURA.md §133.
- **Alcance de ATAQUE de alvo único (skill/auto-attack/torre/minion/
  mob)** → `engine.utils.in_range(ax, ay, bx, by, max_range)` —
  Euclidiana em tile (`dx²+dy² <= max_range²`, círculo de verdade),
  NUNCA `chebyshev()` (que conta diagonal igual a reto e "estica" o
  alcance ~41% na diagonal pro mesmo número — bug real relatado pelo
  usuário, 14/09/2026, ver PROBLEMAS_ARQUITETURA.md §134/§135).
  **EXCEÇÃO obrigatória: alcance melee (`attack_range<=1`, adjacência de
  8 direções) continua `chebyshev()`** — diagonal adjacente tem
  distância real ~1.41, ficaria fora de `in_range(max_range=1)` e
  quebraria golpe corpo-a-corpo em 4 das 8 direções (regressão real,
  pega antes de commitar). `chebyshev()` continua o certo pra
  movimento/pathing/leash/adjacência/aggro-detecção (`MinionSystem.
  _in_range_los` usa `aggro_range_tiles` — raio de PERCEPÇÃO, não de
  ataque — NUNCA trocar pra `in_range()` aí, quebra
  `tests/test_minions.py`, geometria desenhada em Chebyshev de
  propósito). Raio de ÁREA (AoE — Calamidade Flamejante, Canção de
  Ninar, Impacto, Fatiador, Brado Provocativo) fica de fora de
  propósito, continua Chebyshev — decisão explícita do usuário de não
  encolher essas áreas nas quinas por enquanto. **Cliente preditivo e
  servidor autoritativo (e qualquer código que decide "até onde
  andar/reposicionar" pra ficar em alcance — chase do mago,
  `_ranged_stop_tile`, kiting de mob) precisam usar a MESMA métrica**:
  `SkillHandlers._resolve_target`/`_use_skill_visual_only` (skill),
  `combat_processor._resolve_auto_attack` (auto-attack),
  `TowerSystem`/`MinionSystem`/`EnemyAISystem` (torre/minion/mob) — ver
  PROBLEMAS_ARQUITETURA.md §135 pra lista completa e a lição de mapear
  TODOS os call sites antes de trocar qualquer um.
- **Cooldown de auto-attack (`CombatProcessorMixin._attack_timers`,
  `server/combat_processor.py::_resolve_auto_attack`) é por SESSÃO, não
  por combate** — a entrada é apagada quando o alvo morre (engajar o
  próximo alvo é um "cold start" de novo, não só o login). Ranged
  (arqueiro) ganha um atraso mínimo nesse cold start
  (`CombatProcessorMixin._FIRST_RANGED_ATTACK_DELAY_S = 0.25`,
  17/10/2026, pedido do usuário) — sem isso, o 1º tiro de QUALQUER
  engajamento novo disparava no tick seguinte, sem folga nenhuma pra
  animação de disparo (gatilhada só no COMBAT_RESULT confirmado)
  mostrar nenhum frame antes do golpe de verdade. Melee continua
  instantâneo no 1º golpe (decisão explícita — a dessincronia relatada
  era só da animação do arqueiro). Essa constante é PRÓPRIA do
  servidor — NUNCA importar `DEFAULT_FRAME_MS`/qualquer coisa de
  `engine/character_animation.py` pra calibrar isso (módulo tem
  pygame, violaria "server nunca importa Pygame"); se o ritmo real da
  animação mudar no cliente, recalibrar este número A MÃO, sem
  acoplar os dois lados. Ver PROBLEMAS_ARQUITETURA.md §289.
- **`TalentTree.allocated` recebido via rede (`talent_allocated` no
  payload de STATS_UPDATE, emitido só por `server/
  instance_progression.py::enter_/exit_normalized_progression`)
  precisa chamar `self._talent_system.apply_talent_effects()`
  (`ui/talent_system.py`) logo depois de trocar o dict — NUNCA só
  `_tt_alloc.allocated = dict(payload[...])` sozinho.** `allocated` é
  dado bruto; `cs_flags` comportamentais (ex: `ice_cast_time_reduction`
  de Precisão Elemental), modifiers de atributo e skills desbloqueadas
  por talento só existem de verdade depois de `apply_talent_effects()`
  rodar — fora da instância isso sempre acontece junto (roda no mesmo
  clique que aloca um ponto), então o gap só aparece nesse ÚNICO ponto
  de entrada alternativo (sync de rede). Bug real: Nova Congelante
  continuava com 1s de cast visível dentro da BG mesmo com Precisão
  Elemental maxada pela normalização de instância — o servidor sempre
  recalculou certo (efeito real disparava na hora), só a barra de cast
  do CLIENTE ficava presa no valor de fora da instância. Ver
  PROBLEMAS_ARQUITETURA.md §136.
- **Skill com handler de previsão LOCAL (`ui/spell_system.py::
  SpellCastSystem._CAST_HANDLERS`) que TAMBÉM define
  `effects.<evento>.sound` no catálogo (`content/skill_config.py`, pra
  espectador ouvir) precisa marcar `effects.<evento>.skip_local_caster:
  True`** — sem isso, o broadcast AOI do `SKILL_EFFECT` de conclusão
  (`server/spell_completion_processor.py`, genérico pra toda skill sem
  projétil) volta pro PRÓPRIO conjurador (que está dentro do seu
  próprio raio de AOI) e `client/network_handlers.py::
  _handle_msg_skill_effect` toca o mesmo som DE NOVO, duplicando o que
  a previsão local já tocou quando o cast do cliente terminou. Bug
  real: Nova Congelante tocava o som de impacto 2x (local + eco de
  rede) — Calcinar/Polimorfia nunca tiveram esse bug só porque não
  definem `"effects"` no catálogo. `skip_local_caster` afeta só o
  branch `is_local` — outros players continuam ouvindo normal. Ver
  PROBLEMAS_ARQUITETURA.md §137.
- **Sistema de NPC amigável (Loja/Ferreiro/Treinador/Diálogo de quest)
  que precisa "abrir" outro sistema de NPC** → componente
  `NPCInteractionRequest` (`engine/components.py`, `kind`+`target_eid`)
  — quem PEDE só escreve o componente no player; quem ABRE
  (`ShopSystem`/`QuestDialogSystem`) lê e consome (remove) no INÍCIO do
  próprio `update()`, só quando `kind` bate com o seu — NUNCA chamar
  método (principalmente privado) de outra instância de System
  injetada no construtor. Consulta pura sobre quest (disponível/
  completável/em progresso pra um NPC) → `engine/quest_logic.py::
  get_available_quests`/`get_completable_quests`/`get_inprogress_quests`
  (função de módulo, `(world, player_eid, npc_id)`, sem precisar de
  instância de `QuestSystem`/`QuestDialogSystem`) — NUNCA reimplementar
  essa lógica inline nem guardar referência cruzada só pra isso. Bug
  real achado fazendo esse refactor: `BlacksmithSystem` chamava
  `self._quest_system.can_start(qid)`, método que NUNCA existiu em
  `QuestSystem` — quebraria com `AttributeError` assim que um ferreiro
  tivesse quest disponível pra iniciar. Ver PROBLEMAS_ARQUITETURA.md
  §139.
- **Qualquer NPC de serviço (loja/treinador/ferreiro/correio/futuro)
  precisando travar a interação até o player completar uma quest** →
  componente `NPCUnlock(quest_id)` (`engine/components.py`, 06/10/2026)
  — anexável a QUALQUER NPC independente de quais outras capacidades
  tem (Merchant/Trainer/Blacksmith/Mailbox/QuestGiver), via
  `engine.entity_factory._attach_npc_unlock` (chamada por TODO
  `create_*` de NPC de serviço) + `unlock_quest_id` lido do JSON do
  mapa (`engine/map_loader.py`, mesma chave em qualquer lista de
  entidade). Checagem SEMPRE via `engine.quest_logic.is_npc_unlocked
  (world, npc_eid, ql)` — NUNCA ler um campo `unlock_quest_id` dentro
  de um componente de capacidade específico de novo (era assim antes,
  só em `Trainer`, e a loja tinha um mecanismo TOTALMENTE paralelo por
  `shop_id` em `content/merchant_data.py` — os dois removidos). UI que
  abre o NPC (loja/ferreiro/correio) redireciona pra
  `NPCInteractionRequest(kind="open_quest_dialog")` em vez de abrir
  quando travado — nunca mensagem de "bloqueado". Ação que de fato
  gasta recurso real (compra/forja/reciclagem) precisa do MESMO check
  no SERVIDOR, autoritativo — `BUY_REQUEST`/`CRAFT_REQUEST`/
  `RECYCLE_REQUEST` carregam `npc_eid` pra isso (campo opcional, -1 =
  sem NPC conhecido, trata como destravado — nunca travar uma ação que
  não tinha NPC pra checar). Pedido explícito do usuário (06/10/2026):
  "para não termos retrabalho futuro" — qualquer sistema de interação
  de NPC novo usa isto desde o primeiro commit, nunca reimplementa uma
  trava própria. Dar/aceitar quest (componente `QuestGiver`, separado
  de `NPCUnlock`) segue a MESMA filosofia (07/10/2026, pedido do
  usuário: "todo tipo de entidade que eu criar, eu possa colocar uma
  quest, todos os NPCs") — TODO `create_*` de NPC de serviço
  (Merchant/Trainer/Blacksmith/Mailbox, além do `QuestGiver` dedicado)
  aceita `quest_ids`/`turn_in_ids` e anexa `QuestGiver` se QUALQUER um
  dos dois for passado — `if quest_ids or turn_in_ids:`, NUNCA só
  `if quest_ids:` (bug real: um NPC que só ACEITA entrega, sem dar
  nenhuma quest — ex: "Fabian Hardek" — ficava sem `QuestGiver`
  nenhum, sem marcador "?" e sem reação a clique. Ver
  PROBLEMAS_ARQUITETURA.md §245).
- **SAVE_STATE novo disparado por um evento sem relação com exploração
  de mapa (loot/compra/venda/forja/troca/stats/qualquer futuro)** →
  `_send_save_state(include_fog=False)` (`client/save_sync_handlers.py`)
  — NUNCA deixar no default `True` só porque "é mais simples chamar
  sem parâmetro". Montar o fog (bitmap+zlib de TODA a exploração da
  conta) é o campo mais caro de `_collect_save_state` por uma ordem de
  grandeza (~100ms+ numa conta bem explorada, quase todo esse tempo no
  laço Python de montagem do bitmap, não no zlib) — só o save de
  logout/quit (`game.py`) precisa mandar fog de verdade, é o ÚNICO
  ponto que garante fog fresco chegando ao servidor por sessão. Servidor
  trata fog ausente/vazio como "sem mudança" (`server/session.py::
  _persist_character` nem chama `merge_and_encode_fog`,
  `_build_save_merge` devolve `fog: None`, `save_character` pula a
  coluna) — nenhum risco de perda. Bug real evitado implementando isto:
  os únicos 2 pontos de saída pelo MENU de pausa ("Sair"/"Deslogar")
  nunca mandavam save nenhum antes desta correção (só o botão X da
  janela mandava) — sem o `_send_save_state()` explícito adicionado
  ali, fog nunca chegaria fresco ao servidor por esses 2 caminhos.
  Bug real achado num follow-up desta mesma correção (log de playtest
  novo): a busca original por chamadores de `_send_save_state` só
  cobriu `client/network_handlers.py` — 4 chamadas diretas em `game.py`
  (`_autosave()`, disparado por compra de skill/talento/quest/level-up/
  morte/transição de mapa, + autosave local periódico de 2min) ficaram
  de fora, todas ainda mandando fog. Ao adicionar QUALQUER novo
  `_send_save_state`/função equivalente, buscar TODOS os call sites no
  codebase inteiro (`grep -rn` sem filtro de diretório), nunca só nos
  arquivos onde o sintoma original apareceu. Ver PROBLEMAS_ARQUITETURA.md
  §141/§142.
- **Trabalho pesado de CPU pura em Python confirmado por profiling,
  dentro de uma coroutine do servidor** → `loop.run_in_executor(pool, ...)`
  com `ProcessPoolExecutor` (nunca `ThreadPoolExecutor` — GIL não
  libera CPU-bound puro), pool pequeno criado 1x (`SessionManager.
  __init__`, `self._fog_executor`) e desligado em
  `shutdown_dispatch_tasks` — 1ª aplicação real desta regra já
  documentada abaixo ("O event loop asyncio do servidor é
  single-thread"), com a função-alvo pura/pickleable (sem objeto de
  sessão/ECS, só dict/set/tupla — `merge_and_encode_fog`,
  `shared/fog_codec.py`). Sem isso, o cálculo caro trava TODOS os
  players conectados, não só quem disparou o evento — bug real medido:
  `_build_save_merge` (fog) chamado direto na coroutine de
  `_handle_save_state`, ~100-150ms de freeze do servidor inteiro por
  save de conta bem explorada. Ver PROBLEMAS_ARQUITETURA.md §142.
- **Projétil visual do JOGADOR novo** (flecha/bola de fogo/qualquer skill
  de projétil futura) → `PlayerProjectileSystem.spawn(x, y, attacker_id,
  target_id, **kwargs)` (`ui/spell_system.py`) — NUNCA `world.
  create_entity()`/`add_component(PlayerProjectile(...))` (esse
  componente nem existe mais como ECS, 15/09/2026, item 9, ver
  PROBLEMAS_ARQUITETURA.md §144). `spawn()` devolve a entrada criada —
  guarde essa referência pra mutar depois (`entry.deferred_result =
  ...`, `entry.target_server_id = ...`), nunca busque de volta por eid.
  Único caso que ainda precisa de busca (resultado do servidor pode
  chegar num frame DIFERENTE do spawn) → `PlayerProjectileSystem.
  find_pending_fireball(attacker_id, target_id)`. **NUNCA confundir com
  `Projectile`** (`engine/components.py`, flecha/bola de fogo de MOB/
  TORRE/MINION) — esse continua ECS de propósito, é server-autoritativo
  de verdade (`ProjectileSystem` em `engine/world_systems.py`, código
  headless que o SERVIDOR roda, aplica `deal_damage()` real com viagem/
  tempo) — só `PlayerProjectile` (100% client-only, confirmado por grep
  no repo inteiro) foi movido pra fora do ECS. **`spawn()` NUNCA desloca
  `(x, y)`** (tentado e REVERTIDO no mesmo dia, 15/10/2026, ver
  PROBLEMAS_ARQUITETURA.md §283 — `TileMovement.facing` só tem 4
  valores cardeais, mas o alvo pode estar em qualquer ângulo; deslocar
  a origem tirava o projétil da linha reta real até o alvo, pior
  quanto mais diagonal o alvo) — sensação de "nasce de dentro do
  personagem" é resolvida só por FADE de opacidade (`_spawn_fade_
  fraction`/`PROJECTILE_FADE_IN_PX`), NUNCA mudando a posição de
  nascimento de novo. Bola de Fogo em voo emite luz
  dinâmica amarelo-alaranjada (`PlayerProjectileSystem.
  get_fireball_light_sources(cam_x, cam_y)`, mesmo pipeline/formato de
  `"player_light"` já usado pela luz de item aceso — gradiente CACHEADO
  por raio/intensidade, custo real é só 1 blit por bola em voo) —
  qualquer skill de projétil futura que também devesse emitir luz seria
  mais uma linha de filtro nesse MESMO método, nunca um sistema de luz
  paralelo. Ver PROBLEMAS_ARQUITETURA.md §282. **`PlayerProjectileSystem.
  render()` da Bola de Fogo sempre chama `pygame.transform.rotate(frame,
  angle_deg)`, mesmo com `angle_deg=0.0` — NUNCA `rotated = frame` direto**
  (15/10/2026, §283): `frame` vem de `self._fireball_frames`, CACHEADO e
  COMPARTILHADO entre toda bola de fogo em voo — qualquer `.set_alpha()`/
  mutação chamada em cima do objeto retornado precisa ser uma Surface
  NOVA (`rotate()` sempre devolve cópia nova, até com ângulo 0), senão
  afeta o cache global e vaza pra qualquer OUTRA bola de fogo que reusar
  o mesmo frame depois.
  **Sincronizar o NASCIMENTO VISUAL de um projétil com um frame
  específico da animação de golpe/cast do personagem** (17/10/2026,
  flecha do auto-attack do arqueiro saindo antes do frame de soltura
  tocar) → `launch_delay` (campo já existente de `PlayerProjectileEntry`,
  antes só usado pela 2ª flecha de Flecha Reiterada) — `update()` já
  trava o projétil parado enquanto `launch_delay>0`, `render()` já
  desenha com `fade` dependente da DISTÂNCIA percorrida (parado,
  fade=0, efetivamente invisível) — NUNCA precisa de lógica de
  visibilidade nova. Delay = `índice_do_frame_de_soltura ×
  DEFAULT_FRAME_MS / 1000.0` (arqueiro: índice 2, `_ARROW_RELEASE_
  FRAME_INDEX` em `client/remote_entity_handlers.py`) — valor FIXO,
  conhecido de antemão, nunca depende de latência/evento de rede.
  **Isto NÃO é o congelamento de animação revertido em §286/§288** —
  a animação do personagem continua tocando normal desde o frame 0,
  sem travar em lugar nenhum, nunca bloqueia movimento; só o PROJÉTIL
  fica invisível por um tempo curto e fixo. Ver PROBLEMAS_ARQUITETURA.md
  §290.
- **Talento que não deve ter efeito dentro da BG/instância** →
  `content/talent_data.py::INSTANCE_DISABLED_TALENTS` (frozenset de
  `talent_id`) — lido nos dois lados: `server/instance_progression.py::
  enter_normalized_progression` zera o ponto alocado desse talento no
  overlay "tudo maxado" da instância (o efeito nasce desligado pela via
  NORMAL de `cs_flags`/`_apply_talent_modifiers`, nunca um `if
  talent_id=="x"` avulso no momento de uso); `ui/talent_system.py`
  mostra "0/0" em vermelho no nó + aviso no tooltip. Adicionar um
  talento futuro a essa lista é o ÚNICO passo necessário pros dois
  lados ignorarem ele dentro da BG — nunca duplicar a lista em outro
  lugar. Ver PROBLEMAS_ARQUITETURA.md §159/§160.
- **Buscar a ÚNICA entidade de um tipo (Camera/Tilemap/FogOfWar/
  PlayerControlled/etc.) no mundo LOCAL do cliente** →
  `World.get_first(*component_types)` (`engine/world.py`, 18/09/2026)
  — devolve `(entity_id, comp1, ...)` ou `None`, sem alocar a lista
  inteira que `get_entities_with()` sempre monta. **NUNCA usar no
  SERVIDOR pros mesmos tipos** — lá `Tilemap` existe 1 POR MAPA
  (`WorldServer` carrega todos ao mesmo tempo) e `PlayerControlled` é
  adicionado a TODO player conectado — `get_first()` devolveria uma
  entidade arbitrária, não "a certa". Fora de dúvida sobre se um tipo
  é singleton no mundo em questão, usar `get_entities_with()` normal.
  Ver PROBLEMAS_ARQUITETURA.md §184.
- **Objeto de mapa "alto" (árvore, construção) ficar transparente com o
  jogador atrás dele** → marcar `"occludes_player": True` no
  grupo/família certo do catálogo (`engine/tileset.py::OBJECT_SHEET_FAMILIES`,
  mesmo mecanismo de `is_bush`/`default_vision_height` — família
  cascateia pra todos os sub-grupos, grupo pode sobrescrever) — NUNCA
  reimplementar a lógica de desenho, que já existe em
  `ui/render_system.py::RenderSystem.render()` (colisão por
  `pygame.mask`, escopo só do jogador LOCAL, cobertura mínima
  `RenderSystem._OCCLUSION_COVERAGE_THRESHOLD`). Ver
  PROBLEMAS_ARQUITETURA.md §188.
- **Contorno de seleção de alvo** (destacar o alvo atualmente selecionado
  com uma borda colorida) → `RenderSystem._get_selection_ring_mask(surf,
  radius=2)` (07/10/2026) — anel EXTERNO dilatado (`Mask.draw` em
  círculo de offsets + `Mask.erase` da silhueta original), nunca
  `Mask.outline()` + `pygame.draw.polygon()` (mecanismo antigo: a linha
  ficava bem em cima da borda, metade escondida DENTRO da área opaca do
  sprite, quase invisível — ver PROBLEMAS_ARQUITETURA.md §250). O anel
  dilatado nunca sobrepõe o sprite original (by construction, testado em
  `tests/test_selection_outline.py::TestSelectionRingMask`), então a
  ORDEM de desenho (antes/depois do blit do sprite) deixa de importar —
  continua desenhado depois só por não ter motivo pra mudar. Cache por
  `id(surf)` em `self._selection_ring_mask_cache` (mesmo padrão de
  `self._occlusion_mask_cache`). Alvo sem sprite/frame resolvível
  continua caindo no fallback de retângulo (`pygame.draw.rect`),
  inalterado. **Máscara-base do anel usa `RenderSystem.
  _SELECTION_RING_ALPHA_THRESHOLD = 200` (13/10/2026), via
  `pygame.mask.from_surface(surf, 200)` — NUNCA `self._get_mask(surf)`
  aqui** (esse é o cache COMPARTILHADO com a transparência de oclusão,
  chaveado só por `id(surf)`, sem threshold — misturar os dois
  contaminaria o cache com o threshold de quem rodasse primeiro). Sombra
  "assada" em PNG de mob (ex: família Aranha, alpha=128) fica ACIMA do
  threshold padrão do pygame (127) e entrava incorretamente na
  silhueta — 200 separa com folga qualquer sombra conhecida (89 ou 128)
  do corpo opaco (255). Ver PROBLEMAS_ARQUITETURA.md §279.
- **Objeto de mapa que emite luz própria (lampião/tocha/fogueira, base
  do sistema de dia/noite)** → 12º campo posicional (`light`) na tupla
  do tile em `OBJECT_SHEET_FAMILIES` (modo catálogo,
  `engine/tileset.py::_register_catalog_tiles`) — tupla `(off_x, off_y,
  w, h, intensity_pct, radius_tiles)`, propagada pra
  `TileType.light_source` (ver docstring do campo pro formato exato).
  `off_x`/`off_y` são RELATIVOS ao canto do sprite (mesmo espaço que
  `sx`/`sy` da própria entrada) — NUNCA coordenada absoluta da folha de
  sprites inteira. Exemplo real: `vp_170`/`vp_171` (grupo
  "decoracao_vila", família "TX Village Props with Shadow"). Consumidor:
  `ui/day_night_system.py::DayNightSystem` (ver bullet de dia/noite
  abaixo) — dado nasce no catálogo, nunca inferido por `sprite_name` em
  runtime, mesmo padrão de `is_bush`/`occludes_player`. Ver
  PROBLEMAS_ARQUITETURA.md §193.
- **Ciclo dia/noite (cosmético, só mundo aberto)** →
  `ui/day_night_system.py::DayNightSystem` — 100% client-only, relógio
  determinístico (`day_night_fraction()`/`day_night_color()`, função
  pura de `time.time()`, `DAY_NIGHT_CYCLE_S` = 60min reais/dia) — SEM
  mensagem de rede nova, servidor nunca sabe a hora (não é gameplay).
  Chamado em `game.py` logo após `CHAT_BUBBLE.render()` e ANTES do
  grayscale de fantasma. NUNCA aplica dentro de BG/Arena
  (`InstanceInventoryUIState.active` — mesma flag já usada pra câmera
  livre/minimap tela-cheia) — decisão do usuário, luz não deve mudar no
  meio de uma partida competitiva.
  **Técnica (22/09/2026, §203 — MULTIPLICAÇÃO, não mais transparência,
  ver histórico obsoleto em §194/§195):** `day_night_color()` devolve um
  fator MULTIPLICADOR (não um tingimento misturado) — `(255,255,255)` =
  identidade (sem efeito); `DayNightSystem` preenche uma Surface SEM
  `pygame.SRCALPHA` (a técnica precisa dos canais RGB cheios) com essa
  cor e aplica com `special_flags=pygame.BLEND_RGBA_MULT` — cada pixel
  do mundo escurece PROPORCIONAL à própria luminosidade original (mais
  natural que uma lavada uniforme de cor). O 4º valor (alpha) que
  `day_night_color()` ainda devolve é VESTIGIAL — nenhum cálculo o
  consome mais, existe só porque o usuário pediu pra manter o campo/
  editor no F12 mesmo sem uso real; NUNCA reintroduzir lógica que
  dependa dele sem repensar o desenho primeiro. Objeto com
  `TileType.light_source` (ver bullet acima) clareia a área ao redor de
  volta em direção ao branco com um gradiente radial + `BLEND_RGBA_ADD`
  (SOMAR, não subtrair — numa camada que começa ESCURA, subtrair só
  escureceria mais). **`Surface.set_alpha()` NÃO pondera `BLEND_RGBA_
  ADD`/`SUB`/`MULT`** (confirmado empiricamente antes de codar) — a
  intensidade da luz precisa estar "assada" nos valores RGB do
  gradiente no momento em que ele é GERADO, daí o cache de
  `_get_light_add_gradient` ser por `(radius_px, intensity_pct)`, não só
  raio. **Brilho quente** (pedido separado do usuário) — círculo
  `(255,200,100)` de opacidade baixa, desenhado DEPOIS do blit de
  multiplicação, DIRETO no `world_surf` (não na camada de escurecimento)
  com blend NORMAL (esse sim é ponderado por `set_alpha()` de verdade —
  única diferença entre os 2 tipos de gradiente deste arquivo). Posição
  de tela da luz vem de `screen_x`/`screen_y` que `get_world_objects()`
  já calcula (nunca recalcular offset de câmera à parte — mesma lição
  de §191). Ver PROBLEMAS_ARQUITETURA.md §194/§203.
  **Keyframe novo de transição (amanhecer/entardecer)**: com
  multiplicação o "vale de saturação" que existia na técnica antiga de
  transparência (§195) fica bem mais fraco (uma cor multiplicadora
  "acinzentada" só escurece de forma neutra, não pinta um wash feio) —
  ainda assim, prefira âncoras próximas do branco/claras (nunca um
  multiplicador muito escuro no meio da transição, que escureceria a
  cena inteira ali). **Ver hora/fase atual e pular
  pra qualquer uma sem esperar o relógio real** → aba "Dia/Noite" do
  painel de debug F12 (`client/debug_handlers.py::
  _draw_debug_tab_dianoite`, `ui/day_night_system.py::jump_to_fraction`)
  — 100% local, NUNCA manda mensagem de rede (diferente de
  `_debug_levelup`/`_debug_add_gold`/`_debug_add_item`, que são
  server-autoritativos via GM_*) — dia/noite não existe no servidor. Ver
  PROBLEMAS_ARQUITETURA.md §195. **Cores-âncora (Noite/Amanhecer/
  Entardecer) são editáveis ao vivo na mesma aba** (`set_night`/
  `set_dawn`/`set_dusk`, botões -10/+10 por canal R/G/B/Alpha),
  persistidas em `config.json` (`GameEngine._save_config()`/boot) —
  `_build_keyframes()` deriva a transição INTEIRA dessas 3 âncoras
  (nunca hardcoded à parte, senão mudar uma âncora não mudaria a
  transição de verdade). Ver PROBLEMAS_ARQUITETURA.md §196. **Ver a
  curva de cor do ciclo inteiro e arrastar pra qualquer momento** →
  slider com trilha em gradiente na mesma aba
  (`client/debug_handlers.py::_debug_dianoite_slider_rect()`/
  `_handle_debug_dianoite_slider_events()`, substituiu os 4 botões fixos
  de §195) — mesmo padrão de drag do slider de quantidade do trade/loja
  (`_trade_qty_modal_rect()`), geometria SEMPRE recalculada (nunca
  cacheada de um frame de render anterior). `MOUSEMOTION` confirma
  `event.buttons[0]` antes de aplicar o arraste — sem isso, soltar o
  mouse fora da janela ou fechar o painel no meio do arraste deixaria o
  estado de "arrastando" travado pro resto da sessão. Ver
  PROBLEMAS_ARQUITETURA.md §198. **Config do F12 (`config.json`, local)
  NUNCA chega ao executável empacotado** — `release_tools/
  build_client.ps1` sobrescreve `dist/RPG_Online/config.json` com só
  `server_host`/`server_port`. Pra um valor ajustado no F12 virar o
  padrão de verdade pra todo jogador, atualizar as constantes
  `DEFAULT_*` em `ui/day_night_system.py` (mesmo mecanismo de "Resetar
  padrão" do F12 — atualizar lá muda pra ONDE o reset volta). Ver
  PROBLEMAS_ARQUITETURA.md §200. **Luz de objeto (buraco no
  escurecimento) é BINÁRIA** — `ui/day_night_system.py::is_night()`
  (`True` só na fase "Noite") gateia o loop de furar o escurecimento em
  `DayNightSystem.render()`; o tingimento AMBIENTE continua suave, só a
  luz é liga/desliga. **Objeto de mapa com aparência "acesa" à noite**
  (lampião etc.) → folha de sprite COMPANHEIRA cadastrada em
  `engine/tileset.py::NIGHT_SHEET_BY_FILE` (propriedade da FOLHA
  inteira, nunca por tile — mesmo `(sx,sy,w,h)` da folha base, só troca
  o arquivo) — `TileSpriteManager.get_night_overlay_sprite(name)`
  resolve a Surface, `get_world_objects()` só a popula pra objetos que
  JÁ têm `TileType.light_source` (nunca "qualquer tile com pixel
  pintado na folha companheira" — a folha pode ter arte reservada pra
  outras interações futuras, ainda não é pra acender sozinha).
  `RenderSystem` desenha por cima do sprite normal (nunca substitui) só
  quando `is_night()`. Ver PROBLEMAS_ARQUITETURA.md §201. **Corte
  liga/desliga é ANCORADO em minuto exato do ciclo de 60min**
  (`_LIGHT_OFF_MINUTE`/`_LIGHT_ON_MINUTE` em
  `ui/day_night_system.py::_PHASE_LABELS`, hoje 12/49, 23/09/2026) —
  NUNCA um valor de fração solto; mudar o minuto é a ÚNICA edição
  necessária, os dois lados (`is_night()` + rótulo "Noite" do F12) já
  seguem junto por lerem a mesma tabela. Ver PROBLEMAS_ARQUITETURA.md
  §204. **Indicador no HUD do jogador (não-debug)** →
  `ui/day_night_clock_hud.py::DayNightClockHud` (23/09/2026) — topo-
  centro da tela (único ponto livre; topo-esquerda é HP/mana, topo-
  direita é zona/minimapa/buff bar), trilha em gradiente SÓ LEITURA
  reaproveitando a mesma amostragem de `day_night_color()` do slider de
  debug (`client/debug_handlers.py::_draw_debug_tab_dianoite`) — NUNCA
  duplicar essa amostragem num 3º lugar, sempre chamar
  `day_night_color()` direto. Sem ícone de sol/lua (decisão do usuário —
  sem asset pronto, a cor escuro/claro já é intuitiva sozinha), com
  rótulo de fase (`current_phase_label()`) embaixo da trilha. Some
  inteiro em BG/Arena (mesmo gate de `InstanceInventoryUIState.active`
  que `DayNightSystem.render()` já usa). Ver PROBLEMAS_ARQUITETURA.md
  §205. **Mapa fechado (caverna/dungeon) nunca é afetado pelo ciclo** —
  chave `"day_night_affected": false` no `<mapa>_entities.json` (ausente
  = `True`, padrão mundo aberto — SÓ precisa marcar `false` nos
  fechados), parseada em `engine/map_loader.py` pro dict `spawn_points`,
  lida por `game.py::_load_ambient_zones()` (chamada em TODA troca de
  mapa) e propagada pra `DayNightSystem.map_affected`/
  `DayNightClockHud.map_affected` — os dois checam esse campo logo no
  início do `render()`, junto do gate de instância. Novo mapa fechado
  futuro (dungeon, mina, etc.) → só adicionar essa 1 chave no JSON dele,
  nunca checar nome/prefixo de arquivo em runtime. Ver
  PROBLEMAS_ARQUITETURA.md §206. **Curva de queda do brilho de uma luz
  (lampião/item)** → `DayNightSystem._get_light_add_gradient()`,
  `(1 - dist/raio) ** _FALLOFF_EXPONENT` — **`_FALLOFF_EXPONENT` fica em
  `1.0` (reta), NUNCA < 1.0** (09/10/2026, ver PROBLEMAS_ARQUITETURA.md
  §257/§258 — tentativa real, revertida no mesmo dia). Um expoente <1.0
  resolve o caso estreito de 2 luzes se tocando só pela PONTA (reta faz
  a metade externa do raio contribuir pouco sozinha, "fraco+fraco"
  parece anel escuro), MAS qualquer p<1.0 tem derivada que tende a
  infinito perto da borda — numa cena com VÁRIAS luzes próximas (cerca
  com postes em fileira, caso comum de verdade), isso cria bordas
  circulares NÍTIDAS e visíveis pra cada luz ("diagrama de Venn"), pior
  que o anel escuro original. **Testar uma curva de luz nova sempre
  contra uma cena com 5+ luzes sobrepostas, nunca só um par isolado** —
  foi exatamente essa lacuna que fez a 1ª tentativa parecer resolvida e
  na verdade ser pior. `tests/test_day_night_light_falloff.py` tem um
  teste CURVA-AGNÓSTICO (amostra fina perto da borda, detecta qualquer
  derivada íngreme ali) — passa a reprovar sozinho antes de precisar de
  outro screenshot do usuário. Mudar o expoente exige aprovação do
  usuário (afeta o visual de toda luz do jogo) + comparação visual
  antes/depois numa cena com várias luzes, nunca só pela matemática de
  um par. `_get_warm_glow_gradient` (tingimento de cor, bem mais sutil)
  continua na curva reta de propósito — nunca confundir os dois.
- **Item de bag aceso sob demanda pelo jogador (clique direito, emite
  luz ao redor de si)** → campo `Item.light_source: dict | None`
  (`{"intensity_pct", "radius_tiles"}`) no catálogo (hoje só `lampiao`,
  `content/item_table.py`) + `PlayerLightSource` (`engine/
  components.py`, só 1 ativo por jogador) + `WorldServer.
  toggle_item_light` (autoritativo, confirma posse real + catálogo,
  nunca confia no payload) + `TOGGLE_ITEM_LIGHT`/`PLAYER_LIGHT_UPDATE`
  (broadcast via `_sessions_in_aoi`, alcança dono + próximos numa
  chamada só). Item novo com luz futura → só a chave `light_source` no
  catálogo, nenhum outro código muda. Render reaproveita 100% o loop de
  luz que `ui/day_night_system.py::DayNightSystem.render()` já usa pro
  poste de mapa (entrada extra em `world_objects`, marcada
  `"player_light": True` pra ignorar o gate de `is_night()`/mapa
  fechado — sob demanda funciona sempre, diferente do poste
  automático). Ícone aceso → `ui/icon_atlas.py::
  ICON_CHANGE_SHEET_BY_FILE` + `IconManager.get_active()` — mesmo
  mecanismo do poste (`NIGHT_SHEET_BY_FILE`), aplicado ao atlas de
  ícones; a camada companheira tem alpha VARIÁVEL por pixel (efeito
  isolado, não um ícone alternativo opaco) — `get_active()` SOMA por
  cima do ícone normal, NUNCA substitui (bug real corrigido 23/09/2026:
  trocar o ícone inteiro fazia a silhueta do item sumir nas bordas
  quase transparentes da camada de efeito). Ver `ARQUITETURA ONLINE.md`
  §16.
- **Zona de spawn que não deve respawnar** (dungeon instanciada —
  mobs/bosses mortos ficam mortos pelo resto da vida DAQUELA instância)
  → `SpawnZone.respawnable: bool = False` (07/10/2026) — NUNCA um
  sistema paralelo de "morte permanente". `respawnable=True` (default)
  preserva 100% do comportamento de sempre pra qualquer zona existente.
  Campo `ever_filled` no próprio `SpawnZone` é o que impede uma zona
  esvaziada de cair de volta no branch de "preenchimento inicial
  escalonado" (mesma condição que o 1º fill usa — timers vazios +
  `alive < max_count` — ficaria True de novo a cada morte sem essa
  2ª checagem, reenchendo a zona na hora e anulando `respawnable=False`
  por completo; achado DURANTE a implementação, nunca chegou a ir pra
  produção). Uma instância NOVA (`_load_instance`) cria um `SpawnZone`
  componente do zero, então um grupo diferente entrando na MESMA
  dungeon sempre começa com a zona cheia — nenhum estado
  cross-instância é necessário. Gerado automaticamente por
  `tools/png_to_dungeon.py` a partir de cluster de pixel único na PNG
  de entidades — ver `arquitetura/GERAR_DUNGEON_PNG.md`. **`zone.
  respawnable` tem DOIS pontos que agendam respawn, não um** (bug real
  em produção, 08/10/2026, ver PROBLEMAS_ARQUITETURA.md §252) — o
  cleanup sweep de `SpawnZoneSystem.update()` (só fallback, alcançado
  quando a entidade some por outro meio que não combate) E
  `server/server_death_handler.py` (passo 6, "Notifica SpawnZone"),
  que é quem REALMENTE agenda em toda morte de combate de verdade (o
  sweep de `update()` nunca alcança esse caso — a entidade já foi
  removida antes dele rodar). Os DOIS precisam do guard
  `if zone.respawnable:` antes de `zone.respawn_timers.append(...)` —
  mudar só um é um gate pela metade que parece corrigido em teste
  sintético (`world.remove_entity()` direto) mas continua quebrado em
  combate real.
- **Nome exibido customizado de um mob criado via `SpawnZone`** (boss
  de dungeon/elite raro com nome próprio, ex: "Rainha Aracne" em vez
  da raça crua "Aranha") → campo `SpawnZone.name` (08/10/2026),
  repassado como `identity_name` pro `create_enemy()` JÁ EXISTENTE
  (`engine/entity_factory.py` — mesmo parâmetro que NPCs com nome
  próprio e mobs de jungle já usam, nenhum componente/mecanismo novo).
  `"name"` no JSON do mapa (`_entities.json::spawn_zones[].spawns[]`
  ou no nível da zona, mesmo fallback por-spawn/por-zona que
  `race`/`class` já têm) — "" (default) = comportamento de sempre
  (mostra a raça). `race` continua definindo stats/sprite/aparência —
  só o NOME exibido muda, nunca confundir os dois.
- **Loot de boss específico de uma dungeon/instância** (diferente de
  todo mob normal, cujo loot vem SEMPRE de
  `content/mob_definitions.py::MOB_TABLE`) → componente
  `CustomLootTable(items, gold_min, gold_max)` (07/10/2026), anexado
  por `SpawnZoneSystem._spawn_one` quando a `SpawnZone` tem `loot`
  preenchido (vindo do campo `"loot"` na PRÓPRIA `_entities.json` do
  mapa, nunca um catálogo Python novo). Checado em
  `server/server_death_handler.py` com prioridade TOTAL sobre
  `roll_mob_loot`/`MOB_TABLE` — mob com este componente NUNCA consulta
  o catálogo compartilhado. `content/loot_tables.py::roll_custom_loot`
  usa as chances do JSON EXATAS, sem o multiplicador de tier que
  `roll_mob_loot` aplica (decisão do usuário, 07/10/2026 — números do
  mapa já são a chance final calibrada pelo autor daquele boss
  específico).
- **Transição de tile que leva a uma INSTÂNCIA por grupo/solo** (em vez
  de um mapa compartilhado fixo — ex: entrada de dungeon) → chave
  opcional `"dungeon_key"` no dict de `transitions` de QUALQUER
  `_entities.json` (07/10/2026), resolvida por
  `server/dungeon_processor.py::DungeonProcessorMixin.
  request_dungeon_enter` — NUNCA adicionar lógica de instanciamento
  direto em `_handle_zone_change_req` fora desse branch dedicado.
  `content/dungeon_definitions.py::DUNGEON_TABLE` é o catálogo único
  de `dungeon_key → template_file` (mesmo padrão de `MOB_TABLE`).
  Reaproveita o instanciamento genérico já usado por Arena/BG
  (`WorldServer._load_instance`/`_unload_instance`) — instance_key por
  PARTY (`PartyProcessorMixin.get_party_id_of`) ou solo (o próprio
  eid), SEM fila/aceite/pareamento de oponente (entrada é imediata ao
  pisar no tile, diferente de Arena/BG). `WorldServer._load_all_maps`
  PULA qualquer transição com `dungeon_key` no preload eager depth-1 —
  o template só carrega sob demanda via `_load_instance`, nunca como
  bundle compartilhado solto. Morte dentro de uma instância de dungeon
  revive automaticamente (`DungeonProcessorMixin._tick_dungeon_respawns`,
  espelho de `BgQueueProcessorMixin._tick_bg_respawns`) — a decisão
  "este conteúdo revive sozinho" trafega via
  `GhostState.auto_respawn`/`respawn_s` (calculado em
  `RespawnMixin._handle_player_death`, ecoado no payload de
  `PLAYER_DEATH`), **NUNCA** `InstanceInventoryUIState.active` (esse é
  progressão normalizada — zera equipamento/nível real, só BG usa;
  dungeon mantém personagem real). `server/session.py::on_disconnect`
  chama `end_dungeon_of()` ANTES do save, mesmo motivo de
  `end_matches_of`/`end_bg_matches_of` — sem isso, o save persistiria o
  map_id SINTÉTICO da instância. Ver `arquitetura/GERAR_DUNGEON_PNG.md`
  pro pipeline de criação do mapa a partir de PNG.
- **Jogador pode ser teleportado/spawnado/respawnado EM CIMA de um tile
  de transição** (entrada de dungeon, saída de caverna, qualquer
  `self.transition_tiles` — `return_pos` de dungeon é o caso real, mas
  vale pra qualquer spawn/respawn/teleporte futuro) → o disparo
  automático de transição (`game.py`, `# --- Detecção de transição
  ---`) exige `key != self._last_transition_check_tile` (jogador
  CHEGOU ali andando desde o frame anterior) — NUNCA só "está parado
  num tile de transição agora", que dispara igual pra quem só apareceu
  ali (login, respawn, teleporte). `_last_transition_check_tile` é
  resetado em TODA chamada de `_build_transition_tiles` (spawn inicial
  E toda troca de mapa) — sem o reset, comparar contra um tile de um
  mapa ANTERIOR por engano. Bug real: `return_pos` de dungeon sempre
  devolve o jogador pro PRÓPRIO tile de transição (é a posição de
  quando ele entrou); sem este guard, relogar nascia o personagem
  nesse tile e o frame seguinte reentrava a dungeon sozinho — loop que
  se mantinha a cada relog. Ver PROBLEMAS_ARQUITETURA.md §262.
- **Hash de senha do cliente** (antes de sair pela rede — nunca texto
  puro, ver "Segurança" abaixo) → `shared/auth_utils.hash_password()`
  (08/10/2026) — fonte ÚNICA, usada por `client/network.py` (login E
  registro) e pelo Launcher (`launcher/login_ui.py`) — NUNCA reimplementar
  `hashlib.sha256(...)` inline num 3º lugar.
- **Nova API HTTP server-side** (fora do protocolo WebSocket de
  gameplay — ex: o Launcher) → `server/http_api.py` (08/10/2026,
  `aiohttp`, único HTTP real do projeto) — roda DENTRO do mesmo
  processo/event loop de `server/main.py` (`start_http_api`, chamado
  antes do `async with websockets.serve`), porta própria
  (`LAUNCHER_API_PORT`, `shared/constants.py`), MESMO certificado TLS
  autoassinado já usado pelo WebSocket — NUNCA gerar certificado novo
  nem subir um serviço systemd separado pra isso. Qualquer rota nova
  que precise de autenticação delega 100% pra `server/auth.py::
  authenticate()` já existente — nunca reimplementar verificação de
  senha num endpoint HTTP.
- **Publicar uma versão nova do CLIENTE** → `release_tools/
  release_client.ps1` (08/10/2026) — builda jogo+launcher, zipa
  `dist/RPG_Online` (NUNCA inclui `Launcher.exe` — ver próximo bullet),
  calcula sha256, `scp` pra `releases/` no servidor. **Independente de
  `deploy_server.ps1`** — publicar cliente novo NUNCA reinicia o
  servidor do jogo (processo/sessões de quem já está conectado
  continuam intactos).
- **Launcher nunca se autossubstitui** — o zip de auto-atualização
  (`release_tools/release_client.ps1`) NUNCA contém `Launcher.exe`, só
  os arquivos do jogo — elimina de vez o problema de Windows não deixar
  um `.exe` sobrescrever a si mesmo rodando. `launcher/update_client.py`
  (lógica pura, sem tkinter — testável sem abrir UI) é o ÚNICO lugar
  que baixa/confere sha256 (fail-closed: hash vazio NUNCA passa,
  `verify_sha256`)/extrai — nunca duplicar essa lógica dentro de
  `launcher/login_ui.py` (só UI, chama as funções puras).
  **`apply_update()` NUNCA sobrescreve um `config.json` já existente**
  (09/10/2026, ver PROBLEMAS_ARQUITETURA.md §259) — o zip sempre traz um
  `config.json` parcial (gravado por `build_client.ps1`, só server_host/
  porta/scale/volume), e `extractall` sozinho troca o arquivo INTEIRO;
  sem o guard, cada update apagava hotbar/atalhos/modo de janela do
  jogador E o login lembrado no Launcher (mesmo arquivo/mesma pasta).
  Só pula a extração desse 1 arquivo quando já existe um — 1ª instalação
  continua recebendo o do zip normalmente.
- **`RPG_Online.exe` recusa abrir fora do Launcher** (`main.py`, só em
  build `frozen` — `python main.py` em dev continua livre) — checagem
  de `os.environ.get("RPG_LAUNCHED_BY_LAUNCHER") != "1"`, variável
  setada por `launcher/update_client.py::launch_game` no
  `subprocess.Popen`. É uma checagem de conveniência (evita abrir o exe
  sem querer), NUNCA segurança de verdade — decisão explícita do
  usuário de recusar TOTAL (mensagem + `sys.exit`), não só avisar.
- **`requirements_server.txt` ganhou dependência nova** → `deploy_server.ps1`
  só envia CÓDIGO (`git archive HEAD`), nunca instala pacote Python
  novo na VPS — rodar `ssh ... "venv/bin/pip install -r
  requirements_server.txt"` no servidor ANTES/DEPOIS do próximo deploy
  que importa essa dependência, ou o processo entra em crash-loop
  (`systemd Restart=always` reinicia a cada poucos segundos,
  `systemctl is-active` continua devolvendo `active` mesmo assim —
  NUNCA confiar só nisso pra validar um deploy, checar fim-a-fim de
  FORA da VPS). Incidente real: `aiohttp` (Launcher) derrubou o
  servidor em produção por esse gap, ver PROBLEMAS_ARQUITETURA.md §253.
- **Serviço novo abrindo porta TCP própria** (além das já existentes —
  8765 jogo) → checar/abrir no `ufw` da VPS explicitamente
  (`ufw allow <porta>/tcp`) — porta escutando (`ss -tlnp`) NÃO significa
  alcançável de fora, o firewall é default-deny. Ver
  PROBLEMAS_ARQUITETURA.md §253 (mesmo incidente do Launcher,
  `LAUNCHER_API_PORT`).
- **Executável PyInstaller onedir novo que vai morar na MESMA pasta
  final de outro já existente** (ex: Launcher.exe + RPG_Online.exe) →
  `contents_directory="<nome único>"` passado pro `EXE()` (NUNCA pro
  `COLLECT()` — é lido de `arg.contents_directory` de uma instância
  `EXE`, passar em `COLLECT()` é aceito em silêncio mas não tem efeito
  nenhum) — nunca deixar os dois bundles no `_internal` padrão. Bug
  real: dois bundles com dependência em comum (`libcrypto-1_1.dll`, via
  `ssl`/`hashlib`) sobrescreviam a pasta de dependência um do outro ao
  fundir numa pasta só, travando a auto-atualização com
  `PermissionError` sempre que o OUTRO executável estivesse aberto. Ver
  PROBLEMAS_ARQUITETURA.md §255.
- **Rota HTTP nova no Launcher que precisa criar/validar conta** →
  `server/http_api.py` delega 100% pra `server/auth.py::register()`/
  `authenticate()`, as MESMAS funções que o protocolo WebSocket
  (`server/session.py::_handle_register`/`_handle_login`) já usa —
  NUNCA reimplementar validação de senha/duplicidade de username num
  endpoint HTTP novo. Validação de formato (ex: usuário 3–20 chars) é
  espelhada ali porque é a MESMA regra do WS, nunca uma nova.

- **Janela/elemento de UI com o visual do atlas do usuário** (painel,
  botão, ícone, seta, slider — qualquer tela que entre na troca de
  visual) → `ui/ui_atlas.py`. O atlas `assets/ui/ui_elements.png` é uma
  GRADE DE TILES 16x16: monta-se carimbando tiles inteiros (cantos,
  bordas e miolo REPETIDOS) — NUNCA recortar por caixa de contorno
  (pega pedaço de elemento vizinho) nem esticar/9-slice. Compor em
  espaço RAW e ampliar 1x no fim com `ui_atlas.scale_up` (SCALE fixo de
  `ui/hud_bars.py`, igual nameplate — NUNCA a escala de UI do jogador).
  A posição de cada tile numa janela vem do mockup do usuário
  (`assets/content/exemplo.png`), extraída por comparação pixel a
  pixel tile a tile — nunca de olho. Botão age no SOLTAR do mouse (age
  no clique = janela fecha antes do estado pressionado aparecer).
  Largura de botão/seletor/janela NUNCA é número fixo de tiles quando
  tem texto — a fonte acompanha a escala de UI do jogador, o atlas não:
  medir com a MESMA fonte do desenho (`atlas_window.tiles_for`).
  **Montar/interagir com a janela SEMPRE via `ui/atlas_window.py`**
  (`Frame` — botão no soltar, campo/senha, seletor, `scrollbar`,
  `child` pra conteúdo rolável, `slot`/`grid` de itens, `tabs`,
  `checkbox`, `text_block`, divisórias, `close_button`, clique direito
  `rreleased`/`right_click`, `prompt`, `quantity_window`, `item_icon`;
  `PressState`/`ScrollState` por sistema) — NUNCA reimplementar
  botão/rolagem/grade/aba/hit-test/modal de quantidade numa tela à
  parte; peça que faltar entra no kit (com teste em
  `tests/test_atlas_kit.py`) antes de ser usada. **Janela desenhada
  por BAIXO de um modal passa `press=None`** (estado descartável) —
  compartilhar o PressState do modal faz o `end()` dela zerar o botão
  afundado do modal antes dele disparar. Ações agem no soltar DENTRO do
  desenho; o handler de clique antigo do sistema só "segura" o clique
  (pra não vazar pro mundo). Medidas por TILE calculadas no próprio
  sistema — nunca de volta pra `ui/ui_sizes.py`. Limpar constante
  antiga por regex: casar só a linha que COMEÇA com o nome (nome
  citado dentro de comentário já colou 2 linhas uma vez, ver
  HISTORICO_FEATURES.md §72). **Slot de item = `aw.SLOT` (34 px RAW,
  ícone 32x32 nativo)** — posições de slot/grade em px RAW, nunca "1
  tile"; barra de rolagem na última coluna (encostada na borda direita,
  só quando precisa); botões/controles centralizados como grupo (ver
  HISTORICO_FEATURES.md §73).
  **Toda janela do atlas fica dentro da área segura padrão**
  (`atlas_window.safe_area` / `MenuHandlers._mm_safe_area`: margem de
  10% da altura no topo e acima da barra de ações, 10% da largura nas
  laterais) — `Frame` já centraliza nela (sistema fora do GameEngine
  recebe a função injetada, ex: `QuestDialogSystem.safe_area_fn`); janela com lista longa limita a altura a ela e
  rola, nunca cobre a barra de ações nem encosta nas bordas.
  **Antes de usar qualquer peça do atlas, consultar o catálogo
  `arquitetura/ATLAS_UI.md`** (o que cada tile é e pra que serve — ex:
  campo de texto (9,4)–(11,4) pra qualquer valor que o jogador define);
  peça nova/uso novo → atualizar o catálogo no mesmo commit. Ver
  HISTORICO_FEATURES.md §65–§73.
- **Ícone ANIMADO acima da cabeça de uma entidade, no MUNDO** (marcador
  de quest, ou categoria futura parecida) → `ui/world_icon_animator.py::
  get_frame(name, size, now_ms)` — spritesheet horizontal em
  `assets/sprites/{name}.png` (quadros QUADRADOS, contagem derivada de
  `largura/altura`, nunca hardcoded), loop por tempo real
  (`pygame.time.get_ticks()`, mesmo padrão de `character_animation.py`).
  NUNCA usado por mapa (M)/minimapa (`ui/map_markers.py`) — esses
  continuam no ícone ESTÁTICO de sempre (`icon_manager.py`/fallback de
  círculo+glifo), decisão explícita do usuário (09/10/2026): animação
  só faz sentido vista de perto, no mundo. Categoria de asset PRÓPRIA,
  separada de `ui/effect_animator.py` (ícone ESTÁTICO de efeito,
  `assets/effects/`) e `ui/icon_manager.py` (ícones de UI com atlas,
  `assets/icons/`) — nunca confundir os 3 diretórios/módulos.

### Abas Entrar/Criar conta no Launcher (09/10/2026)
`launcher/login_ui.py` espelha a MESMA UX de cadastro que já existe
dentro do jogo (`ui/login_screen.py`) — mesma validação local (usuário
mín. 3 chars, senha não vazia, confirmação precisa bater,
`_submit_register`), mesmo comportamento pós-sucesso (NUNCA loga
automaticamente — volta pra aba de login com usuário preenchido + aviso
verde). Campo de formulário novo no Launcher → `_field_row()` (label ao
lado do entry, largura fixa `_FIELD_ENTRY_W`, centralizado como unidade
dentro do painel) — NUNCA um entry com `width=panel_w` (layout antigo,
trocado a pedido do usuário por ficar "muito largo").

### Atualização coesa ao adicionar sistema novo (22/07/2026 — feedback recorrente do usuário)
- **Nunca** deixar uma atualização acontecer "de carona" em outra ação sem
  relação direta (ex: HUD de grupo só atualizava quando o player se movia;
  tile de portão de arena só re-renderizava quando a câmera cruzava
  fronteira de tile ao andar) — se a causa de uma atualização de tela/estado
  não é a ação que a gerou, é sintoma desta classe de bug.
- Toda vez que um sistema/evento/mensagem novo for adicionado, **enumerar
  explicitamente** todos os componentes, sistemas, entidades e elementos de
  UI afetados por ele e garantir que cada um propague na hora certa — não
  assumir que "vai atualizar em algum tick futuro" é aceitável.
- Casos já resolvidos, ambos a mesma causa raiz (dois lados diferentes do
  cliente/servidor):
  - **Servidor**: `SessionManager._on_tick`'s `has_pending`
    (`server/session.py`) só despacha pacotes se algum buffer específico
    tiver conteúdo — qualquer `_..._this_tick`/evento novo que não entrar
    nessa lista fica PRESO até atividade alheia (movimento, combate, etc.)
    destravar por acaso. Já aconteceu com arena (§34.34.1) E com grupo/
    duelo/trade/correção de posição de skill (mesmo dia, mesmo padrão —
    ver `historico/ARQUITETURA ONLINE HISTORICO.md`). Todo buffer novo
    consumido em `_dispatch_tick_deltas` tem que entrar em `has_pending`
    no MESMO commit.
  - **Cliente**: `TileRenderSystem` (`ui/tile_render_system.py`) desenha em cima de uma
    Surface em cache indexada pela posição da CÂMERA, não pelo conteúdo do
    tile — qualquer mutação direta de `Tilemap.tile_matrix` fora do fluxo
    normal de troca de mapa precisa chamar
    `GameEngine._tile_render_system.invalidate_cache()` explicitamente, ou
    o visual só se autocorrige quando o jogador anda o bastante pra cruzar
    fronteira de tile. **Mesma classe, achada de novo em 13/08/2026**: o
    overlay de FOG (`render_fog()`, cache irmão do de terreno) tinha o
    MESMO problema — só reconstruía por câmera, nunca por
    `FogOfWar.visible`/`explored` terem mudado sozinhos (visível perto de
    bush pequena, cuja sombra muda rápido demais pra câmera acompanhar).
    Corrigido com `FogOfWar.version` (contador no COMPONENTE, incrementado
    por `FogSystem` sempre que recalcula) lido por `TileRenderSystem` a
    cada frame — nunca chamar `FogSystem` → `TileRenderSystem` direto
    (violaria "System nunca chama System"); qualquer cache futuro dessa
    MESMA classe (indexado por câmera/posição em vez do dado que ele
    representa) deve seguir esse padrão de contador-no-componente, não
    reinventar. Ver `PROBLEMAS_ARQUITETURA.md` §53.
- Se não estiver óbvio QUANDO uma atualização nova deve disparar (a cada
  tick? só na ação que a causou? em resposta a outro evento?), **perguntar
  ao usuário antes de implementar** em vez de adivinhar — mesma régua de
  `feedback_ask_before_deciding` (memória).

### Performance e concorrência — convenção geral, não ligada a um incidente específico
- **Componentes são dados, não lógica.** Um Component não pode ter métodos com lógica
  de negócio, imports pesados nem side-effects — só campos. Lógica encontrada dentro de
  um Component pertence a um System.
- **Nem todo System roda todo tick.** Ao registrar um System novo, definir
  explicitamente sua frequência: tick de gameplay (movimento/colisão/combate) acompanha
  o loop principal (30Hz); IA/pathfinding deve rodar em frequência reduzida ou
  orientado a evento (recalcular rota só quando o alvo se move o suficiente ou o
  caminho é bloqueado, nunca todo tick pra toda entidade); entidades fora do AOI de
  qualquer player entram em modo "adormecido" (sem IA/física detalhada).
- **Algoritmo antes de micro-otimização.** Busca por proximidade e pathfinding usam
  estrutura espacial (grid/quadtree), nunca varredura linear sobre todas as entidades.
  Antes de propor qualquer otimização, medir com profiling (`cProfile`/`py-spy`) —
  nunca otimizar "no escuro" com base em suposição de onde está o gargalo.
- **O event loop asyncio do servidor é single-thread — CPU pesada bloqueia tudo.**
  Diferente de GIL/threading: uma função síncrona pesada (ex: A* sobre 10000+ tiles)
  dentro de uma coroutine trava o loop inteiro até terminar — nenhum outro pacote de
  rede, nenhum outro player, nenhuma outra atualização processa nesse intervalo.
  Throttling de IA e AOI reduzem a FREQUÊNCIA das chamadas pesadas, mas não impedem que
  UMA chamada pesada trave tudo enquanto roda — isso é uma causa provável (ainda não
  confirmada por profiling) dos spikes de travamento já observados mesmo com throttling
  e AOI implementados.
- **Sistemas identificados como pesados** (pathfinding A*, IA em lote) devem rodar via
  `loop.run_in_executor()` com `ProcessPoolExecutor` (contorna o GIL de verdade, roda em
  processo separado) — nunca direto dentro da coroutine do tick principal. Antes de
  implementar essa mudança, confirmar com profiling que o gargalo medido é mesmo esse
  sistema, não assumir.
- **Estrutura de dados em caminho quente (por-tick, por-entidade, por-alvo de AOE):
  preferir a ferramenta mais leve que resolve o problema** — dict/tuple/set com tipos
  primitivos, não uma classe/dataclass nova instanciada por evento/alvo. Registro
  plugável de estado (mesmo padrão de `_svc`/`_damage_tracker`/`_lethal_interceptor`
  em `engine/core_systems.py`) é dict de módulo, não uma lista de objetos. Ver
  `PROBLEMAS_ARQUITETURA.md` §14 pro contexto (débito de performance identificado,
  ainda não medido por profiling — não é motivo pra bloquear feature nova, é critério
  de escolha quando a estrutura de dados é decisão aberta).
- **Observabilidade vem antes de otimização.** Nenhuma mudança de estrutura de
  dados (numpy, dict mais leve, etc.), periodicidade de sistema, ou algoritmo é
  decidida "porque parece pesado" — precisa de dado de profiling/log apontando a
  causa raiz ESPECÍFICA primeiro (qual sistema, em qual tick, com que frequência).
  Média por bloco de ticks esconde picos isolados e pode indicar o sistema errado;
  antes de confiar num log de performance pra decidir onde otimizar, confirmar que
  ele tem granularidade suficiente (breakdown por tick, não só média; percentil,
  não só média; sistema específico, não categoria ampla). Ver
  `PROBLEMAS_ARQUITETURA.md` §23.
- **numpy (ou qualquer estrutura vetorizada) só depois que profiling confirmar
  volume/formato que justifique** — vale pra muitos valores numéricos homogêneos
  com operação vetorizada de verdade (ex.: distância de centenas de mobs de uma
  vez); pra componentes ECS pequenos/heterogêneos (a maioria deste projeto) é
  overhead sem ganho. Não é ferramenta padrão, é ferramenta de caso específico.
- **Recompute disparado por QUALQUER item de uma lista (não só "mudou
  algo") reprocessando TODOS os itens do zero é O(n) escondido —
  cachear por item, não só decidir "recomputa ou não" pro conjunto
  inteiro.** `FogOfWar.ally_centers` (visão compartilhada de time —
  player/torre/minion) é o caso real: `FogSystem` recalculava o
  shadowcasting de TODO aliado sempre que QUALQUER um deles mudava de
  posição, mesmo os que não tinham se movido — custo escondido em
  mundo aberto (zero aliados) e Arena (time pequeno), mas real em BG
  (vários minions/torres), crescendo com a partida (mais minions em
  campo). Fix: cache por item, chave = o que definiria o resultado
  daquele item específico (`(ax,ay,ar)` pro FOV de um aliado,
  `FogOfWar._ally_fov_cache`) — só o item cuja chave mudou desde o
  último tick recalcula de verdade. Mesmo princípio de qualquer lista
  de "N coisas que podem mudar independente" processada dentro de um
  gate de "algo mudou": o gate decide SE processa a rodada, o cache
  por item decide O QUE dentro da rodada precisa mesmo recalcular. Ver
  PROBLEMAS_ARQUITETURA.md §146.

### Protocolo
- Todo pacote tem `type` (MsgType), `p` (payload), `seq` (int), `ts` (ms epoch)
- Novos tipos de mensagem: adicionar em `MsgType` + documentar payload em `shared/messages.py`
- Servidor valida TUDO — nunca confiar em dados de gameplay do cliente

### Lag compensation
- Skills de cone (Pirofagia, Tiro Múltiplo): cliente envia `dir_x/dir_y` + `ts`
- Servidor usa `WorldServer.get_snapshot_at(tick)` para validar no estado correto
- Janela máxima: `LAG_COMP_WINDOW_MS = 200` em `shared/constants.py`

### Segurança
- Invisibilidade (Camuflagem): servidor **nunca** inclui jogador invisível no AOI de outros
- Dano, drops, posição final de knockback: sempre calculados no servidor
- SHA-256 do password no cliente antes de enviar — nunca texto puro na rede

### Validação de alvo no cliente — usar `utils.is_target_alive()`
- **Nunca** checar só `CombatStats.current_hp` pra decidir se um alvo está morto/válido no cliente.
  Player remoto (PvP) não tem `CombatStats` local — só `RemoteControlled.hp`. Mob remoto só tem
  `RemoteEntityMeta.hp`. Um check que só olha `CombatStats` nunca detecta a morte desses alvos
  (bug real: arqueiro continuava tocando som de "nock" e contando cooldown de ataque contra um
  guerreiro remoto já morto, pois `tgt_cs.current_hp <= 0` nunca era True com `tgt_cs is None`).
- Toda skill/sistema/classe nova que precisa saber se um alvo está vivo deve chamar
  `utils.is_target_alive(world, target_id)` (cobre os 3 casos) em vez de reimplementar o check.
  `skill_handlers.SkillHandlers._target_alive()` é um atalho que já delega pra essa função.
- Isso só importa no **cliente** — no servidor todo player (local ou remoto) tem `CombatStats`
  completo, porque o servidor é autoritativo pra todo mundo.

### Regras de skills/talentos/stats (válidas no cliente)
- Skills → `SKILL_CATALOG` em `skill_config.py` (fonte única)
- Talentos → `talent_data.py`, efeitos em `talent_system.apply_talent_effects()`
- `stat_fns.py` para mutações de stats
- Skills `offensive=False` não iniciam combate nem perseguem alvo

---

## Como rodar

Precisa de um PostgreSQL rodando (07/09/2026, §123 — migrado de SQLite).
Dev local, via Docker (1x, se o container ainda não existir):
```bash
docker run --name rpg_postgres -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=rpg_ecs_online -p 5432:5432 -d postgres:16
```
Se o container já existe mas está parado: `docker start rpg_postgres`.

```bash
# Instalar dependências do servidor
pip install -r requirements_server.txt

# Iniciar servidor (cria schema/tabelas e conta de teste automaticamente)
python server/main.py

# Em outro terminal: iniciar cliente
python main.py
```

Conta de teste criada automaticamente: `usuario=teste  senha=123456`

**Suíte completa (`pytest tests/ -q`) chegava a ~11GB de memória e podia
derrubar o Postgres/qualquer outra coisa rodando na máquina por falta
de RAM** (causa: coletor de lixo cíclico do Python não acompanhava o
ritmo de referências circulares que uma arquitetura ECS cria a cada
`World`/`WorldServer` novo — não é vazamento de verdade, ver
PROBLEMAS_ARQUITETURA.md §199) — `tests/conftest.py` já resolve isso
(`gc.collect()` a cada teste, pico medido caiu pra ~300MB). Não precisa
fazer nada extra pra rodar a suíte normal; só não REMOVER esse
`conftest.py` achando que é redundante.

Testar algo que depende de nível/talento/gold/item: conceder GM
(`python -m server.grant_gm <username>`) + `"debug_mode": true` em
`config.json` — abre F12 com nível/ouro/itens server-autoritativos de
verdade (não é mais mutação só local). Ver
`historico/ARQUITETURA ONLINE HISTORICO.md` §34.74.54.
