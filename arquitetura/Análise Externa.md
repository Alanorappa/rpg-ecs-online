# ANÁLISE MACRO DE ARQUITETURA — RPG ECS Online

Este documento consolida a análise estrutural do projeto RPG ECS Online, um MMO top-down mesclando elementos de Tibia e World of Warcraft, implementado em Python com Pygame, usando arquitetura ECS (Entity-Component-System) e comunicação cliente-servidor. O diagnóstico cobre problemas críticos de arquitetura, débito técnico, violações de design e soluções priorizadas para garantir escalabilidade e manutenibilidade.

## 1. VISÃO GERAL DO PROJETO

O projeto consiste em um MMO RPG 2D com visão top-down, combinando o estilo visual de Tibia (gráficos em tiles, sprites pixelados) com mecânicas de World of Warcraft (sistema de talentos que desbloqueiam habilidades, árvores de talentos, efeitos sonoros espaciais). Atualmente existem duas builds funcionais: Cavaleiro (completa, migrada para online) e Piromancia (completa no modo offline, em processo de migração). O código-fonte contém aproximadamente 50 arquivos Python, organizados em módulos de jogo, sistemas, componentes e rede.

A arquitetura utiliza um ECS próprio, com um World central que gerencia entidades, componentes e sistemas. O servidor é headless (sem Pygame), executando a mesma lógica de jogo em modo terminal. A comunicação usa sockets TCP com um protocolo de mensagens serializadas. O sistema de talentos permite alocar pontos que desbloqueiam habilidades específicas, seguindo o modelo de World of Warcraft, onde remover todos os pontos de um talento também remove a skill correspondente.

O status atual de migração indica que a build Cavaleiro funciona online, mas apresenta problemas de sincronização em habilidades específicas (como Interceptar). A build Piromancia está pronta no cliente offline, mas ainda não possui implementação server-side, o que impede seu uso no modo online.

## 2. PROBLEMAS CRÍTICOS IDENTIFICADOS

Os problemas a seguir foram classificados como críticos por impactarem diretamente a funcionalidade, a manutenibilidade ou a escalabilidade do projeto. Cada problema inclui descrição técnica, impacto e solução proposta.

### PROBLEMA 1: Duplicação de Lógica de Combate em 3 Lugares

A lógica de combate — como decaimento de rage, temporizador de combate e regeneração de HP5 — está implementada em três locais distintos, com pequenas variações:


systems.py — classe CombatStateSystem, executada no cliente offline com renderização.
core_systems.py — classe ServerCombatStateSystem, executada no servidor headless, pretendendo ser um subconjunto do offline.
world_server.py — lógica inline espalhada no método _tick(), que duplica trechos como rage += 5 e combat_timer -= dt.


Impacto: Desincronização entre cliente e servidor — por exemplo, o decaimento de rage pode ocorrer em velocidades diferentes, levando a estados inconsistentes em que o cliente permite uma skill que o servidor rejeita. Além disso, qualquer correção precisa ser replicada manualmente nos três lugares, aumentando o risco de bugs.

Solução: Unificar toda a lógica de combate em uma única classe (CombatStateSystem) que seja usada tanto pelo cliente quanto pelo servidor. Ambos compartilham o mesmo código, diferenciando-se apenas na ausência de renderização no servidor. O método update() deve aceitar events e dt de forma genérica. A validação deve garantir que, com os mesmos parâmetros de entrada, as saídas (estado de rage, combat_timer) sejam idênticas.

### PROBLEMA 2: Handlers de Skills Não Extensíveis

As habilidades são implementadas como métodos privados no arquivo skill_handlers.py, com nomes como _skill_golpe_poderoso e _skill_interceptar. Cada handler contém lógica de validação, cálculo de dano e aplicação de efeitos — tudo hardcoded. Além disso, verificações do tipo if sk.skill_id == "golpe_poderoso" espalham-se por systems.py, acoplando o sistema de combate a identificadores literais de skills.

Impacto: Adicionar uma nova skill exige editar pelo menos três arquivos: skill_handlers.py (novo handler), systems.py (adicionar verificações de ID) e world_server.py (registrar no servidor). A build Piromancia, já implementada no cliente offline, não funciona online justamente porque seus handlers não foram portados para o servidor. Esse acoplamento torna o sistema frágil e impede a evolução ordenada do jogo.

Solução: Criar um catálogo data-driven de skills (SKILL_CATALOG) em um arquivo dedicado skill_config.py. Cada skill deve ser definida com metadados como damage_mult, rage_cost, mana_cost, range_px, handler_type e unlocked_by_talent. Os handlers existentes devem ser refatorados para usar esses dados, e o servidor deve registrar para cada skill um handler server-side correspondente, removendo a dependência de Pygame no servidor.

### PROBLEMA 3: CharacterStats Mistura Dados Permanentes + Voláteis

O componente CharacterStats contém simultaneamente dados que devem persistir entre sessões (como level, xp, max_mana) e dados puramente voláteis de combate que não deveriam sobreviver a um respawn ou recarga (como embalo_charges, fire_instant_ready, thermal_shock_active). A serialização para save game inclui todos os campos, o que significa que cargas de habilidades ou flags de talento ativas podem ser restauradas indevidamente.

Impacto: Um jogador pode salvar o jogo com 3 cargas de Embalo e, ao carregar, ter essas cargas imediatamente disponíveis — um bug de balanço de jogo. Além disso, a falta de separação conceitual polui o componente e torna mais difícil entender o ciclo de vida de cada atributo.

Solução: Dividir CharacterStats em duas classes:


CharacterStats — contém apenas dados permanentes: level, xp, mana total, talentos alocados, etc.
CombatRuntime — contém dados voláteis que são resetados ao entrar em combate ou ao respawnar: carga de habilidades, timers de efeitos, flags de talento temporárias.


O sistema de save deve ignorar CombatRuntime e restaurar apenas CharacterStats. O CombatRuntime deve ser inicializado com valores padrão sempre que a entidade é criada ou respawnada.

### PROBLEMA 4: CombatStats com 80+ Campos + 26 Flags de Talento

O componente CombatStats é um dos maiores do projeto, com mais de 80 campos. Destes, 26 são flags booleanas ou inteiras representando talentos das builds Cavaleiro e Piromancia, como golpe_poderoso_unlocked, fire_instant_ready e thermal_shock_active. Esses campos existem em todas as entidades que possuem CombatStats, incluindo mobs que nunca usarão talentos.

Impacto: Cada entidade consome memória desnecessariamente com dezenas de campos que nunca serão lidos. Adicionar uma nova build (terceira classe) exigiria mais 15-20 campos, elevando o total para mais de 100. A manutenibilidade sofre — é fácil cometer erros como ler o campo errado ou esquecer de atualizar uma flag ao se referir a ela.

Solução: Substituir os campos individuais por um dicionário genérico talent_flags: dict[str, int | bool]. Cada talento adiciona uma chave a esse dicionário quando é alocado. A leitura torna-se cs.talent_flags.get("golpe_poderoso_unlocked", False). Isso reduz o tamanho da classe, facilita a adição de novos talentos e permite que mobs tenham um dicionário vazio, economizando memória.

### PROBLEMA 5: AOI Sweep é O(N²)

A Área de Interesse (AOI) no servidor é implementada na classe SessionManager, mais especificamente no método _dispatch_tick_deltas. Para cada player conectado, o método itera sobre todas as entidades recém-spawnadas (_spawned_this_tick) e verifica a distância até cada uma. Em um cenário com 100 players e 1000 mobs, são realizadas 100.000 verificações de distância por tick. A 20 ticks/segundo, são 2 milhões de operações por segundo.

Impacto: O servidor consome CPU desnecessariamente, podendo causar lag perceptível à medida que a população do jogo cresce. Em ilhas com alta densidade de mobs, o gargalo torna-se evidente.

Solução: Implementar uma estrutura de spatial hashing (grade 2D) que divide o mapa em buckets. Cada entidade é atribuída a um bucket com base em sua posição. Para verificar AOI, basta iterar sobre os buckets adjacentes ao bucket do player, reduzindo a complexidade de O(N²) para O(N × k), onde k é o número médio de entidades por bucket (tipicamente pequeno e constante).

### PROBLEMA 6: Imports Circulares Críticos

A análise dos módulos revela um ciclo de importação entre skill_handlers.py e systems.py. skill_handlers.py importa funções de systems.py (como apply_effect e deal_damage), enquanto systems.py importa a classe SkillHandlers de skill_handlers.py. Esse ciclo só não quebra em tempo de execução porque Python permite referências circulares, mas torna o código frágil — qualquer refatoração que altere a ordem dos imports pode gerar um ImportError difícil de depurar.

Impacto: O ciclo impede que módulos sejam importados de forma independente, dificultando testes unitários e refatorações. Além disso, indica uma responsabilidade mal distribuída: funções de baixo nível como deal_damage deveriam residir em um módulo separado, não em systems.py.

Solução: Extrair funções compartilhadas (deal_damage, apply_effect, is_tile_walkable) para um novo módulo combat_utils.py. Este módulo não deve importar nem systems.py nem skill_handlers.py. Em seguida, remover a importação de systems.py de dentro de skill_handlers.py, substituindo-a por imports de combat_utils.py. O ciclo é quebrado e ambos os módulos tornam-se testáveis individualmente.

### PROBLEMA 7: Lógica de Negócio em Locais Errados

Várias regras de negócio fundamentais estão implementadas em classes de sistema que deveriam ser apenas orquestradoras, não contenedoras de lógica de domínio. Por exemplo:

O cálculo de dano crítico (chance de crítico, multiplicador) está dentro de CombatSystem, misturando a regra com o ciclo de atualização.
A validação de skills ocorre em dois lugares diferentes — SkillSystem._use_skill() (cliente) e WorldServer._process_skill_requests() (servidor) — sem um repositório compartilhado de regras.
O sistema de efeitos de status (StatusEffectSystem) lê diretamente os componentes CombatStats, TileMovement e CombatState, acoplando-se a múltiplos componentes e dificultando a reutilização.


Impacto: Qualquer alteração em uma regra de jogo (por exemplo, modificar a fórmula de crítico) exige localizar o fragmento de código dentro de um sistema de centenas de linhas, em vez de um módulo de lógica pura. A validação duplicada frequentemente resulta em race conditions — o cliente aceita a skill e o servidor a rejeita, causando o conhecido problema da skill Interceptar travar no meio do caminho.

Solução: Criar módulos dedicados para a lógica de domínio:

damage_formulas.py — funções puras de cálculo de dano, crítico, resistência.
skill_validation.py — validações de range, custo, cooldown, compartilhadas entre cliente e servidor.
effect_resolver.py — aplicação de efeitos a partir de dados, desacoplada de sistemas.

Os sistemas devem chamar essas funções em vez de implementar as regras internamente. Isso permite testar cada regra isoladamente e garante que cliente e servidor usem a mesma lógica.

### PROBLEMA 8: Talent System com 45+ Linhas de Reset + Aplicação

O talent_system.py contém um método apply_talent_effects() que ultrapassa 100 linhas. Para cada talento alocado (ou removido), o código manualmente atualiza flags em CombatStats, desbloqueia ou bloqueia habilidades e ajusta atributos. Isso cria um padrão de código repetitivo e propenso a erros: a cada novo talento, o desenvolvedor precisa adicionar um novo bloco if/elif no método. A mesma lógica se repete para resetar talentos.

Impacto: A manutenção do sistema de talentos é onerosa. É fácil esquecer de adicionar o efeito correto ao resetar um talento, deixando flags ativas indevidamente. Além disso, a implementação não escala para 20+ talentos por build.

Solução: Adotar uma abordagem data-driven para efeitos de talentos. Definir uma estrutura como:

```python
TALENT_EFFECTS = {
    "golpe_poderoso": {
        "unlock_skill": "golpe_poderoso",
        "stat_modifiers": {"rage_cost_reduction": 5}
    },
    "embalo": {
        "unlock_skill": "embalo",
        "stat_modifiers": {"move_speed_bonus": 10}
    }
}
```

O método apply_talent_effects() deve iterar sobre os talentos alocados e aplicar os efeitos definidos no dicionário, usando funções genéricas de modificação. Remover um talento deve reverter esses mesmos efeitos. Dessa forma, adicionar um novo talento requer apenas adicionar uma entrada no dicionário, sem tocar no código do sistema.

## 3. PROBLEMAS ARQUITETURAIS (DÉBITO TÉCNICO)

### A1: Skill Validation Desincronizada

Conforme mencionado no Problema 7, a validação de habilidades é feita de forma independente no cliente e no servidor. O cliente, em SkillSystem._use_skill(), verifica se o jogador tem rage/mana suficiente, se a skill está em cooldown e se o alvo está dentro do alcance. O servidor, em WorldServer._process_skill_requests(), refaz essas mesmas verificações. Como os dois conjuntos de regras nem sempre usam os mesmos valores (por exemplo, o custo de mana pode estar hardcoded em um lugar e em constante no outro), o servidor pode rejeitar uma habilidade que o cliente considera válida.

Impacto: O jogador aperta o botão da skill, vê a animação começar (prediz localmente), mas o servidor rejeita e envia uma correção. O resultado é uma experiência inconsistente, onde a skill parece "falhar" sem motivo aparente. Esse é o caso da habilidade Interceptar, que frequentemente trava o personagem em uma posição intermediária.

Solução: Extrair TODAS as validações de skill para um módulo compartilhado skill_validation.py. Tanto o cliente quanto o servidor devem chamar a mesma função validate_skill(character_entity, skill_id, target_entity, world_state). O servidor continua sendo a autoridade final, mas o cliente usa a mesma lógica para predizer, reduzindo drasticamente as rejeições.

### A2: Lag Compensation Incompleto

O servidor possui um sistema de snapshots de posição (get_snapshot_at()), capaz de retornar a posição de uma entidade em um timestamp passado. No entanto, esse recurso não é utilizado nas validações de alcance das skills. Em _process_skill_requests(), a posição atual do alvo (target.current_pos) é usada em vez da posição que o alvo ocupava no momento em que o cliente disparou a skill (timestamp do cliente).

Impacto: Skills de alcance, especialmente projéteis ou habilidades de curto alcance como Golpe Poderoso, podem ser rejeitadas se o alvo se moveu entre o clique do jogador e o processamento do servidor (30-50ms de diferença). Isso cria a sensação de que "a skill não acertou mesmo estando na mira".

Solução: Modificar _process_skill_requests() para usar get_snapshot_at() tanto para a posição do atacante quanto para a do alvo, utilizando o timestamp enviado pelo cliente na mensagem da skill. A verificação de alcance deve ser feita sobre essas posições históricas, garantindo que a skill seja julgada pelo estado do mundo no momento do input.

### A3: Skill Position Corrections Não Sincronizadas

Certas habilidades, como Interceptar, alteram a posição do jogador no servidor (por exemplo, mover o personagem para perto do alvo). O servidor envia essa correção de posição via mensagem ENTITY_MOVE direcionada ao caster. Contudo, outros jogadores próximos recebem a posição do caster através do sistema de AOI (AOI_UPDATE), que pode conter a posição antiga se o AOI tiver sido calculado antes da correção ser aplicada.

Impacto: Os outros jogadores veem o caster "teletransportar" de volta para a posição antiga por um frame, causando um efeito visual de rubber-banding. Isso prejudica a imersão e, em combate PvP, pode dar vantagens injustas (o oponente vê o caster em um lugar onde ele não está mais).

Solução: Garantir que a correção de posição seja aplicada ANTES do próximo cálculo do AOI. Uma abordagem simples é, logo após aplicar a correção, forçar uma atualização imediata do AOI para a entidade afetada, removendo-a da posição antiga e adicionando-a na nova em todos os buckets. Outra alternativa é unificar o fluxo de correção: sempre que a posição de uma entidade for alterada pelo servidor (não por movimento normal), o servidor deve enviar uma mensagem de POSITION_CORRECTION para todos os jogadores que têm essa entidade em seu AOI, não apenas para o caster.

### A4: Talent Unlock Logic Espalhada

A lógica para desbloquear uma skill ao alocar pontos em um talento está dispersa. No talent_system.py, o método apply_talent_effects() contém chamadas do tipo add_skill(player_eid, "golpe_poderoso"). O método para remover talentos, por outro lado, está em outro local (reset_talents() em talent_system.py ou skills.py), e pode não remover a skill corretamente. Não há uma função central que mapeie talento → skill desbloqueada.

Impacto: É fácil introduzir bugs onde um talento desbloqueia uma skill, mas ao remover os pontos, a skill permanece disponível. Ou vice-versa: a skill some, mas o efeito associado ao talento (como redução de custo) ainda persiste.

Solução: Criar um dicionário global TALENT_SKILL_MAP em skill_config.py, que mapeie cada talento à skill que ele desbloqueia (se aplicável). Em seguida, implementar funções genéricas apply_talent_effects(eid, talent_id, allocated_points) e remove_talent_effects(eid, talent_id) que leem esse dicionário e executam as ações de desbloqueio/bloqueio e modificação de atributos. Isso centraliza a lógica e facilita a adição de novos talentos.

## 4. PROBLEMAS DE DESIGN (VIOLAÇÕES DE ECS)

### D1: God Object em world_server.py

O arquivo world_server.py contém a classe WorldServer, que ultrapassa 1000 linhas de código. Esta classe acumula responsabilidades de:

Gerenciamento de sessões de jogadores (conexão, desconexão).
Spawn de entidades (mobs, NPCs).
li>Processamento de requisições de skills.
Processamento de ataques e combate.
Gerenciamento de loot e drops.
Controle de morte e respawn.
Sincronização de estado (AOI, mensagens).
Lógica de tick principal (_tick() com 400+ linhas).


Impacto: A classe é difícil de entender, testar e manter. Qualquer mudança em uma parte do código pode ter efeitos colaterais imprevistos em outras. O método _tick() sozinho já é um God Method que mistura atualização de entidades, processamento de mensagens e renderização (mesmo que headless).

Solução: Dividir WorldServer em múltiplas classes especializadas, cada uma com uma única responsabilidade:

SkillProcessor — responsável por receber e processar requisições de skills.
CombatProcessor — gerencia ataques automáticos, dano contínuo e efeitos.
LootProcessor — gerencia drops, coleta e inventário.
RespawnSystem — controla spawn e respawn de entidades.

O WorldServer deve se tornar um orquestrador que instancia esses processadores e os chama na ordem correta dentro do tick, delegando as tarefas específicas.

### D2: Acoplamento entre Componentes

Dentro do ECS, os sistemas frequentemente acessam múltiplos componentes diretamente. Por exemplo, StatusEffectSystem lê CombatStats (para dano), TileMovement (para posição) e CombatState (para verificar se está em combate). Isso cria dependências implícitas: se um componente for alterado (ex.: renomear um campo), todos os sistemas que o acessam precisam ser atualizados.

Impacto: O sistema se torna frágil a refatorações. Não há uma interface clara entre sistemas e componentes, e a lógica de aplicação de efeitos está espalhada em vez de centralizada.

Solução: Implementar um sistema de eventos. Quando um efeito precisa ser aplicado (ex.: dano ao longo do tempo), o StatusEffectSystem deve enviar um evento (ex.: DamageEvent) que é processado por um sistema especializado em dano. Alternativamente, usar callbacks registrados nos componentes — por exemplo, CombatStats.on_damage_taken que dispara um evento. Isso desacopla os sistemas entre si e facilita a extensão (novos sistemas podem ouvir os mesmos eventos sem modificar os existentes).

### D3: Falta de Separação Client/Server

O arquivo skill_handlers.py importa pygame e faz uso de constantes gráficas (como pygame.Rect) e funções de colisão específicas do cliente. Para executar esse módulo no servidor headless, um workaround é utilizado: os.environ['SDL_VIDEODRIVER'] = 'dummy'. Isso contorna o problema, mas não o resolve — o servidor ainda carrega todo o Pygame na memória, desperdiçando recursos.

Impacto: O servidor tem uma dependência desnecessária de uma biblioteca gráfica, o que aumenta o consumo de memória e impede execução em ambientes sem Pygame instalado (como containers mínimos). Além disso, a mistura de lógica de render com lógica de jogo fere o princípio de separação de responsabilidades.

Solução: Criar skill_handlers_server.py, uma versão puramente lógica dos handlers de skill, que não importa Pygame. Ela deve conter as regras de validação e aplicação de efeitos, mas sem qualquer referência a gráficos. O skill_handlers.py original deve herdar ou delegar a este módulo, adicionando apenas a parte gráfica (efeitos visuais, sons). O servidor carrega apenas skill_handlers_server.py.

## 5. PROBLEMAS DE ESCALABILIDADE

### S1: Teste de Regressão Manual

Atualmente, não existem testes automatizados (como pytest ou unittest) para regressão. Toda validação é feita manualmente: abrir o jogo, alocar talentos, testar skills, verificar visualmente. Esse processo é demorado e propenso a falhas, especialmente quando há múltiplos talentos que interagem entre si (ex.: talento que reduz custo de rage de outra skill).

Impacto: Cada refatoração, mesmo pequena, carrega um alto risco de quebrar funcionalidades existentes. O desenvolvedor hesita em fazer mudanças arquiteturais por medo de introduzir bugs não detectados. A produtividade cai e o débito técnico se acumula.

Solução: Criar uma suíte de testes com pytest focada nos módulos de lógica pura (cálculo de dano, validação de skills, efeitos de talento). Os testes devem cobrir:

Alocar talento X → skill Y desbloqueada.
Remover talento → skill Y bloqueada (não pode ser usada).
Usar skill com recursos insuficientes → rejeitada.
Usar skill com alvo fora do alcance → rejeitada.
Efeitos de talento (redução de custo, aumento de dano) aplicados corretamente.

Idealmente, esses testes rodam em CI (GitHub Actions) a cada push na branch online, garantindo que regressões sejam detectadas automaticamente.

### S2: Documentação de Fluxo Desatualizada

O arquivo PROBLEMAS_ARQUITETURA.md existe e documenta diversos problemas identificados anteriormente, mas não cobre o fluxo completo de desbloqueio de skills por talentos. Também não há diagramas ou descrições de como as mensagens de rede fluem entre cliente e servidor durante uma skill.

Impacto: Novos desenvolvedores (ou o próprio autor, após semanas sem tocar no código) precisam ler dezenas de arquivos para entender o fluxo. Isso aumenta o tempo de onboarding e o risco de introduzir inconsistências.

Solução: Atualizar PROBLEMAS_ARQUITETURA.md com seções detalhadas sobre:

Fluxo de desbloqueio de skill por talento (diagrama de sequência textual).
Fluxo de validação de skill (cliente → servidor → resposta).
Fluxo de correção de posição (para skills como Interceptar).

Manter a documentação como parte do repositório e revisá-la sempre que o fluxo for alterado.

### S3: Piromancia Não Testada Online

A build Piromancia está completamente implementada no cliente offline (sistema linear de habilidades, talentos, efeitos especiais como Thermal Shock e Combustão), mas o servidor não possui:

Handlers para as skills de Piromancia (Pirofagia, Rajada Arcana, etc.).
Lógica para efeitos especiais como dano em cone.
Sincronização de stacks de Thermal Shock entre cliente e servidor.


Impacto: Se a build Piromancia for ativada no servidor antes de ter os handlers implementados, as skills serão rejeitadas silenciosamente ou causarão erros no lado do servidor (quebrando o jogo para todos). Isso inviabiliza o lançamento da segunda build no modo online.

Solução: Antes de portar Piromancia, é necessário garantir que a arquitetura de skills suporte os novos tipos (skills de cone, área). Isso inclui:

Adicionar ao SKILL_CATALOG as skills de Piromancia com metadados de área (raio/ângulo).
Implementar os handlers server-side correspondentes (ex.: _skill_pirofagia_server()) no servidor.
Testar cada skill manualmente com um cliente de Piromancia conectado ao servidor local.


## 6. SOLUÇÕES PROPOSTAS (ORDEM DE PRIORIDADE)

As soluções estão organizadas em três fases, priorizando correções que desbloqueiam o progresso imediato (Piromancia online) e reduzem o risco de regressão. Cada fase contém estimativas de esforço e critérios de sucesso.

### FASE 1 — CRÍTICO (Fazer antes de Piromancia)

Objetivo: Eliminar duplicações críticas e preparar a base para a Piromancia, garantindo que o servidor consiga processar novas skills sem quebrar as existentes.

1.1 Unificar CombatStateSystem (2-3 horas)
Remover a classe ServerCombatStateSystem de core_systems.py e a lógica inline de world_server.py. A classe existente em systems.py deve ser movida para um módulo compartilhado (ex.: combat_state_system.py) e usada tanto pelo cliente quanto pelo servidor. A diferença é que o servidor não executa a parte visual (se houver). Teste de validação: executar 100 ticks no servidor e no cliente com os mesmos parâmetros de entrada e verificar que os valores de rage e combat_timer são idênticos.

1.2 Criar SKILL_CATALOG Completo (2-3 horas)
Criar o arquivo skill_config.py e definir um dicionário SKILL_CATALOG contendo todas as skills existentes (Cavaleiro e Piromancia) com metadados: name, damage_mult, rage_cost/mana_cost, range_px, handler_type, unlocked_by_talent. Em seguida, refatorar skill_handlers.py para ler os valores desse catálogo em vez de usar constantes hardcoded. Teste: comparar o dano causado por uma skill antes e depois da mudança — deve ser exatamente o mesmo.

1.3 Registrar Handlers Server-Side (1-2 horas)
Criar um dicionário SKILL_HANDLERS_SERVER em world_server.py (ou em um novo módulo skill_handlers_server.py) que mapeie cada skill_id para sua função handler. O método _process_skill_requests() deve consultar esse dicionário para executar o handler correto. Teste: conectar um cliente com build Cavaleiro, usar todas as skills e verificar que o servidor processa corretamente. Em seguida, conectar um cliente com build Piromancia (forçar usando código local) e testar se os handlers são chamados (mesmo que a skill ainda não aplique dano, ao menos não deve lançar exceção).

### FASE 2 — ESCALABILIDADE (Fazer depois da Piromancia funcional)

Objetivo: Melhorar a performance e a manutenibilidade do sistema, preparando o terreno para uma terceira build e para um possível aumento de capacidade de jogadores.

2.1 Separar CharacterStats (2-3 horas)
Criar a classe CombatRuntime e mover para ela todos os campos voláteis de CharacterStats (embalo_charges, fire_instant_ready, thermal_shock_active, etc.). Modificar o sistema de save/load para ignorar CombatRuntime. Teste: salvar um jogo no meio de um combate, carregá-lo, e verificar que as cargas de habilidades foram resetadas para zero, enquanto level e talentos permanecem.

2.2 Refatorar CombatStats com Dict (1-2 horas)
Substituir os 26 campos individuais de flags de talento em CombatStats por um dicionário talent_flags: dict[str, int | bool]. Atualizar todos os locais que leem ou escrevem essas flags para usar o dicionário. Teste: alocar um talento, verificar que a flag aparece no dicionário; remover o talento, verificar que a flag desaparece ou é atualizada para 0/False. Executar todos os testes de regressão da Fase 1 para garantir que o combate continua funcionando.

2.3 Implementar Spatial Hashing (3-4 horas)
Implementar a classe SpatialHash que divide o mapa em células de tamanho fixo (ex.: TILE_SIZE × 10). Cada entidade é inserida em uma célula com base em sua posição. O método get_nearby_entities(x, y, radius) retorna as células adjacentes como candidatas. Substituir o loop O(N²) em _dispatch_tick_deltas pela consulta ao SpatialHash. Teste: com 200 entidades, medir o tempo de execução de _dispatch_tick_deltas antes e depois da mudança — a redução deve ser de pelo menos 80%.

### FASE 3 — REFATORAÇÃO ESTRUTURAL (Longo prazo)

Objetivo: Amadurecer a arquitetura do projeto, eliminando God Objects, imports circulares e desacoplando sistemas. Essas mudanças têm maior risco de quebrar funcionalidades, por isso são deixadas para depois da fase de testes automatizados.

3.1 Dividir world_server.py (4-5 horas)
Extrair as responsabilidades de WorldServer para classes menores:

SkillProcessor: lida com requisições de skills (_process_skill_requests).
CombatProcessor: lida com ataques automáticos e aplicação de dano ao longo do tempo.
LootProcessor: gerencia drops e coleta.
SpawnSystem: controla o spawn de mobs e respawn de entidades.

O método _tick() deve ser simplificado para chamar cada processador em ordem. Teste: executar o servidor com a nova estrutura e verificar que o jogo roda sem diferenças perceptíveis.

3.2 Criar skill_handlers_server.py (2-3 horas)
Criar um novo módulo skill_handlers_server.py que contém apenas a lógica de validação e aplicação de skills, sem qualquer referência ao Pygame. O módulo original skill_handlers.py deve importar esses handlers e adicionar apenas a parte visual (efeitos, sons). O servidor carrega apenas skill_handlers_server.py. Teste: executar o servidor sem o workaround SDL_VIDEODRIVER=dummy — se o servidor iniciar sem erros, a separação foi bem-sucedida.

3.3 Implementar Event System (3-4 horas)
Implementar um sistema de eventos pub-sub centralizado no World. Os sistemas não acessam mais componentes uns dos outros diretamente; em vez disso, disparam eventos (ex.: EntityDamagedEvent, EntityKilledEvent) que são processados por sistemas ouvintes. Isso desacopla os sistemas e facilita a adição de novos comportamentos (ex.: um sistema de conquistas que ouve EntityKilledEvent). Teste: aplicar um efeito de dano ao longo do tempo e verificar que ele ainda causa dano a cada tick, mas agora através do sistema de eventos.

## 7. CHECKLIST DE VALIDAÇÃO

Antes de realizar qualquer refatoração, este checklist deve ser executado para garantir que o estado atual do jogo está funcional. Após a refatoração, ele deve ser reexecutado para confirmar que nada foi quebrado.


[ ] Alocar ponto em talento desbloqueia skill na hotbar.
[ ] Remover ponto de talento bloqueia a skill (não aparece na hotbar, não pode ser usada).
[ ] Usar skill com talento desbloqueado causa dano/efeito corretamente.
[ ] Tentar usar skill sem o talento desbloqueado é bloqueado (exibe mensagem de erro).
[ ] Ao respawnar, cargas de habilidades (ex.: Embalo) são resetadas.
[ ] Ao respawnar, flags temporárias de talento (ex.: fire_instant_ready) são resetadas.
[ ] Salvar o jogo e carregar: talentos e nível persistem; cargas e flags temporárias não persistem.
[ ] No modo online, o servidor sincroniza corretamente o desbloqueio/bloqueio de skills de todos os jogadores.
[ ] Skills da build Piromancia (quando implementadas no servidor) funcionam em modo online.
[ ] Skills de cone (Pirofagia, Tiro Múltiplo) acertam múltiplos alvos corretamente.
[ ] A habilidade Interceptar não trava o personagem em posição errada.
[ ] Dano causado em modo online é consistente com o dano calculado localmente (diferença máxima aceitável: 2%).


## 8. RECOMENDAÇÃO FINAL

Com base na análise, a recomendação principal é: não iniciar a portabilidade da Piromancia antes de resolver os problemas críticos de arquitetura. Tentar portar a Piromancia sobre a base atual resultará em:


Skills de cone completamente quebradas no servidor (pois o lag compensation e a validação de AOI não estão preparados para área).
Handlers duplicados (a Piromancia offline teria handlers no servidor que são cópias do offline, perpetuando o problema de duplicação).
Dificuldade de testar regressão (sem testes automatizados, bugs de interação entre talentos de Cavaleiro e Piromancia passariam despercebidos).


Portanto, o cronograma sugerido é:


FASE 1 (1 semana): Unificar CombatStateSystem, criar SKILL_CATALOG, registrar handlers server-side para ambas as builds.
FASE 2 (1 semana): Implementar testes automatizados básicos (pytest) para validação de skills e efeitos de talento.
FASE 3 (2 semanas): Portar Piromancia para online, aproveitando a arquitetura já limpa.
FASE 4 (contínua): Refatorações estruturais (dividir world_server, separar CharacterStats, implementar spatial hashing) conforme necessidade.


Essa abordagem garante que o tempo investido em portar a Piromancia não seja desperdiçado corrigindo problemas que já poderiam ter sido resolvidos antes, e que a experiência online seja fluida para os jogadores.

---

Análise concluída em 26 de maio de 2026. Este documento deve ser revisado sempre que novos módulos forem adicionados ou a arquitetura for alterada.