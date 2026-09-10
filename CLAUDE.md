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
- Problema arquitetural → `PROBLEMAS_ARQUITETURA.md`
- **Regra nova a partir daqui: separar regra de histórico.** Ao registrar
  uma regra nova neste arquivo, escrever a regra em forma crua e atemporal
  (sem data, sem narrativa do incidente) e colocar o contexto completo
  (data, sintoma, tentativas erradas) em `PROBLEMAS_ARQUITETURA.md`,
  referenciado por ponteiro (`ver §N`). O objetivo é este arquivo continuar
  enxuto e sempre atual pra releitura a cada sessão — a história detalhada
  já tem endereço certo. Entradas datadas já existentes neste arquivo
  continuam válidas como estão; consolidá-las é decisão a ser tomada com o
  usuário, não automática.
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

- **Antes de investigar qualquer bug, checar `PROBLEMAS_ARQUITETURA.md`
  (seções numeradas + §11) pelo sintoma.** Se bater com um item marcado
  "MITIGADO" (não "RESOLVIDO"), o fix de verdade é terminar aquele item
  — não outro patch pontual em cima do sintoma novo. Um item "mitigado"
  que gera um 4º/5º incidente da mesma família é sinal de que a
  mitigação nunca foi suficiente; dizer isso ao usuário explicitamente
  em vez de tratar como bug novo.
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
  topo; efeitos via `from fx import FLT, SOUNDS, ...`). Sistema de UI/render →
  `systems.py` (que re-exporta world_systems pra compatibilidade).
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
  `ui/systems.py`). Decisão do usuário (31/08/2026): migrar mecanismos
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
  calculado na mão por `effect_type` (é exatamente o padrão que o
  tooltip de skill tem, `client/tooltip_handlers.py::
  _skill_tooltip_lines`'s cadeia de `if sid == "golpe_poderoso": ...` —
  pedido explícito do usuário pra não repetir isso aqui). Geometria de
  hit-test (qual ícone está sob o mouse) vem de `ui/systems.py::
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
- **Animação BASE de personagem** (idle/andar, qualquer direção, qualquer
  classe — não confundir com override TEMPORÁRIO de aparência acima) →
  `engine/character_animation.py::get_character_frame(class_id, state,
  direction, anim_time_ms)` — convenção de arquivo
  `assets/sprites/{class_id}_{idle|run|attack|<skill_id>}_{down|up|left|right}.png`
  (sheet cortado em frames de `CHAR_TILE_SIZE` px de largura — 96,
  09/09/2026, frame NÃO é quadrado, largura fixa por decisão do
  usuário, nunca derivada da altura do sheet; `class_id` sempre em
  português — mesmo valor de `CharacterStats.class_id`/
  `RemoteControlled.class_id`, nunca uma tradução nova). Implementação
  incremental por decisão do usuário (09/09/2026): arquivo ausente →
  `None` → `RenderSystem` cai pro retângulo colorido de sempre, nunca
  erro nem placeholder. `direction` vem de `TileMovement.facing`
  (gravado por `TileMovementSystem` a cada passo real, via
  `engine.utils.facing_from_delta` — NUNCA recalcular esse delta em
  outro lugar). "left"/"right" sem arquivo próprio herdam o espelho
  horizontal do lado oposto automaticamente — pintar só 1 dos 2 lados é
  suficiente. `RenderSystem` só tenta isso pra entidades com `class_id`
  resolvível (`CharacterStats` local ou `RemoteControlled` remoto) —
  mob/NPC continuam retângulo, sem mudança. **Estado exibido** (qual
  animação mostrar AGORA) nunca é decidido no `RenderSystem` direto —
  `engine/character_animation.py::resolve_display_state(tile_movement,
  class_id, now_ms)` é o ponto único (golpe > andando/parado; golpe
  expira sozinho após 1 ciclo do sheet, sem componente/sistema
  dedicado a "desligar"). **Animação de golpe** (auto-attack, ou skill
  sem sprite própria cai no genérico `attack` — ver
  `resolve_attack_state_name`) é ligada por
  `trigger_attack_anim(tile_movement, class_id, sid, now_ms)`, chamado
  de UM lugar só: `client/remote_entity_handlers.py::
  _apply_combat_result`, no momento em que o dano é confirmado
  (`damage>0`, nunca DoT/HoT) — NUNCA replicar esse gatilho em outro
  ponto do código (ex: no clique de ataque, ou no envio do
  AUTO_ATTACK/CAST_SKILL) — o servidor é quem confirma que o golpe
  aconteceu de verdade, o cliente só reage a essa confirmação.
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
  maior que 1 tile)** → `ui/systems.py::TileRenderSystem.
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
  - **Cliente**: `TileRenderSystem` (`ui/systems.py`) desenha em cima de uma
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

Testar algo que depende de nível/talento/gold/item: conceder GM
(`python -m server.grant_gm <username>`) + `"debug_mode": true` em
`config.json` — abre F12 com nível/ouro/itens server-autoritativos de
verdade (não é mais mutação só local). Ver
`historico/ARQUITETURA ONLINE HISTORICO.md` §34.74.54.
