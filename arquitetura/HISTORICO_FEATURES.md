# Histórico de Features/Melhorias — RPG ECS Online

> Log datado de toda funcionalidade/melhoria implementada a pedido do
> usuário — não é lugar de bug nem de problema estrutural (ver
> `PROBLEMAS_ARQUITETURA.md`/`HISTORICO_BUGS.md`). Números são PRÓPRIOS
> deste arquivo, não herdados de `PROBLEMAS_ARQUITETURA.md` (onde a entrada
> morava antes de 19/10/2026 fica como stub, "(movido)", apontando pra
> cá). Ver `arquitetura/INDICE.md` pra buscar por tag/palavra-chave.

---

## 1. Fatiar quantidade no trade + ícone por item_id (11/08/2026)

Os 2 pontos explicitamente adiados no §25 ("Só o saque agora, features
depois") — usuário pediu pra prosseguir logo em seguida, depois de
confirmar que a Fase 4 (roteiro §13) já tinha fechado com o §25 (o
INV_SYNC pendente era exatamente forja/consumíveis/saque).

### A — Fatiar quantidade ao ofertar item no trade

Antes, clique direito na bag durante um trade sempre movia a STACK
INTEIRA pra oferta — `add_trade_item()` só sabia popar por índice, sem
noção de quantidade. Decisão do usuário via AskUserQuestion: igualar ao
padrão já usado na compra em loja (`BUY_REQUEST`) — clique direito
simples oferece 1 unidade; Shift+clique direito num item empilhável
abre o MESMO modal visual de quantidade da loja (usuário pediu
explicitamente "o mesmo que já temos na compra de itens stackáveis").

- `server/trade_processor.py::add_trade_item(player_eid, inv_index,
  quantity=None)` — `quantity` ausente ou >= stack atual = oferece o
  item INTEIRO (comportamento original, ainda usado por
  `_cancel_trade_session`/devolução). `quantity` parcial: decrementa
  `quantity` do item de ORIGEM (que continua na Inventory) e oferta uma
  CÓPIA nova reconstruída via `_item_factory_by_id(item.item_id)` — NUNCA
  copia o objeto do cliente direto (mesmo princípio de
  `_reconstruct_item`: só o catálogo real dá os stats, nunca o payload).
  Item sem `item_id` (save legado/corrompido) ou não-empilhável
  (`max_stack<=1`) não pode ser fatiado com segurança — cai no
  comportamento de oferecer tudo, nunca quebra nem inventa stats.
- `server/session.py::_handle_trade_offer_item` — lê `quantity`
  opcional do payload, repassa pra `add_trade_item`.
- `shared/messages.py` — `TRADE_OFFER_ITEM` ganhou o campo opcional
  `quantity`, doc comment atualizado.
- Cliente (`client/trade_handlers.py`) — réplica fiel do padrão de
  `ui/systems.py::ShopSystem._qty_modal`/`_handle_qty_modal_event`/
  `_render_qty_modal` (slider arrastável, campo numérico, ESC/ENTER,
  botões Cancelar/Ofertar), adaptado pro layout do trade (sem linha de
  preço — trade não tem custo). Diferença de integração real: `ShopSystem`
  é um `System` próprio com `update(events, dt)`/`handle_events(events)`
  chamados em 2 passes pelo game loop; `TradeHandlers` é um mixin plano
  de `GameEngine`, sem esse padrão de 2 passes — precisou de uma chamada
  NOVA em `game.py` (`self._handle_trade_qty_modal_events(events)`,
  full-pass sobre TODOS os eventos do frame, não só o elif single-event
  dispatch de `_handle_trade_click`) pra suportar MOUSEMOTION de
  arrastar o slider, que o dispatch elif original nunca roteava pra
  trade. `_handle_trade_click` ganhou um early-return quando o modal
  está aberto, pra não vazar clique pra janela de trade por baixo.
  Registro em `client/modal_stack_handlers.py::_modal_registry` (entrada
  `trade_qty` antes de `trade`, mesmo padrão de `shop_qty`/`shop`) — ESC
  fecha o modal antes de cancelar o trade inteiro, e `_any_modal_open()`
  bloqueia input de outros sistemas enquanto ele está aberto.

**Testes**: `tests/test_trade_quantity_slice.py` (novo, 9 testes) —
quantity None/>=stack oferece tudo (regressão); quantity parcial fatia
mantendo o resto na mochila E cria cópia nova (nunca compartilha objeto
com a mochila); item não-empilhável ignora quantity; item sem item_id
não é fatiado (fallback seguro); cancelar/retirar devolve a fatia
corretamente; round-trip via protocolo real (`TRADE_OFFER_ITEM` com
`quantity`). Nenhum teste de trade existia antes desta sessão — área
descoberta sem cobertura nenhuma durante a pesquisa.

### B — Ícone de item por item_id (não mais nome de exibição)

`ui/icon_manager.py::IconManager.item_key(item)` gerava a chave do
ícone a partir de `item.name` (`"item_" + nome.lower().replace(" ",
"_")`) — mesma classe de fragilidade já corrigida em outros lugares
pela migração C2 (item_id como identidade estável, §20): dois itens
com nome igual colidiriam no mesmo ícone.

- `item_key(item)` agora prefere `item.item_id`; só cai pro nome
  (`item_key_by_name`, novo — extraído pra ponto único do formato de
  fallback) quando item_id está vazio.
- 21 arquivos em `assets/icons/` renomeados de `item_<nome_snake>.png`
  pra `item_<item_id>.png` (script gerou o mapeamento comparando a
  chave ANTIGA de cada item de TODOS os catálogos — item_table,
  quest_items, materials, recipes, recipe_items — contra os arquivos em
  disco). 3 arquivos já estavam ÓRFÃOS antes da migração (nome de
  exibição do item mudou depois do ícone ser feito, ou tinha um typo no
  arquivo) — `item_espada_treino.png`→`training_sword` (nome real:
  "Espada de treinamento"), `item_artefato_extremamente_
  misterioso.png`→`artefato_misterioso` (nome real: "Artefato
  Misterioso"), `item_lamião.png`→`lampiao` (typo, faltava o "p").
  Inferências por similaridade confirmadas com o usuário via
  AskUserQuestion antes do rename (decisão de conteúdo, nunca assumida
  sozinha) — os 3 hoje resolvem ícone corretamente pela 1ª vez desde
  que ficaram órfãos.
- 2 pontos em `client/consumable_bar_handlers.py` reimplementavam a
  MESMA fórmula de fallback por nome à mão (`"item_" + nome.lower()...`)
  pra quando não havia objeto Item resolvido, só uma string de nome —
  trocados por `ICONS.item_key_by_name(nome)` (ponto único).
- **Achado durante a pesquisa, corrigido por decisão do usuário
  (AskUserQuestion) e depois REVERTIDO por verificação própria**:
  pesquisa inicial (Agent subagent) reportou `engine/save_system.py`
  (save local antigo) como "ainda ativamente ligado" e sem round-trip
  de `item_id` — usuário decidiu corrigir também. Antes de implementar,
  verifiquei a reachability real: `_item_to_dict`/`_dict_to_item` só são
  chamadas por `save_game`/`load_game`, e `save_game`/`load_game` NUNCA
  são chamadas em NENHUM lugar fora do próprio `save_system.py` —
  `game.py::_autosave` (único ponto real de autosave) tem no próprio
  comentário "O save LOCAL... foi removido junto do modo offline",
  chamando só `_send_save_state()` (servidor). Os outros arquivos que a
  pesquisa citou (`ui/talent_system.py`, `ui/trainer_system.py`,
  `ui/quest_system.py`) só usam `request_autosave()`, que roteia pro
  MESMO `_autosave` — nunca toca serialização de item. Reportei o erro
  da minha própria pesquisa ao usuário antes de agir (mesma régua de
  "análise de terceiro verificada contra o código atual", aplicada
  aqui à MINHA PRÓPRIA pesquisa) — usuário confirmou pular, já que
  `_item_to_dict`/`_dict_to_item` são código morto de verdade hoje.
  Ver `arquitetura/MAPA_PROJETO.md`/`ARQUITETURA_ONLINE.md` — candidato
  a remoção futura (fora do escopo desta sessão, decisão nova
  necessária).

**Testes**: `tests/test_icon_key_migration.py` (novo, 6 testes) —
`item_key` prefere item_id; fallback por nome preservado; nomes iguais
com item_ids diferentes nunca colidem; todo item_id migrado resolve um
arquivo real em disco; item reconstruído via catálogo REAL (não string
à mão) resolve ícone corretamente. `tests/test_client_ui.py` — fixture
de teste do registro de modais (`_make_modal_stack_fixture`) precisou
de `_trade_qty_modal = None` novo (5 testes que já existiam pra outros
modais quebraram até esse ajuste, por causa do `__getattr__` estrito
do fixture rejeitando o atributo novo referenciado pelo registry).

### C — Bug real de playtest (11/08/2026): ofertar 1 unidade fazia a STACK INTEIRA sumir da bag

Usuário testou a Parte A logo após implementada: "quando clico com o
direito em uma stack de itens realmente só 1 vai para o slot de trade
porém a stack inteira sai da bag, e deveria descontar só a quantidade
que foi para o slot". Reproduzido exatamente como descrito.

**Causa raiz**: `client/network_handlers.py::_handle_msg_trade_state` —
único ponto onde um item ofertado sai da Inventory local de verdade
(nunca otimista) — diffava a oferta antiga vs nova por CONTAGEM de
ENTRADAS (`Counter` por nome, `newly_offered = new_counter -
old_counter`), e para cada entrada nova contada, POPAVA O SLOT INTEIRO
da bag que batesse o nome (`inv.items.pop(idx)`, sem olhar `.stack`).
Esse diff-por-contagem estava certo enquanto `add_trade_item()` só
sabia mover o item INTEIRO (1 entrada nova sempre = 1 slot inteiro que
saiu de verdade) — mas ficou errado no instante em que a Parte A deste
mesmo §26 ensinou `add_trade_item` a fatiar (1 entrada nova agora pode
representar só PARTE do stack de um slot que continua na bag). Bug
introduzido pela própria Parte A, achado no playtest imediatamente
depois — nenhum teste da Parte A cobria o lado CLIENTE do diff (os
testes de `test_trade_quantity_slice.py` só validavam
`add_trade_item()` no servidor; a reconstituição da bag local nunca
tinha teste).

**Fix**: diff trocado de CONTAGEM de entradas pra QUANTIDADE (soma de
`stack` por item_id/nome) — `delta = nova_soma - soma_antiga`; `delta >
0` decrementa só essa quantidade da bag (removendo o slot inteiro só
se `stack` chegar a 0); `delta < 0` (retirada da oferta) devolve
mesclando num stack já existente na bag quando possível, senão cria
slot novo. Corrige de brinde uma fragmentação que já existia antes
(retirar item da oferta sempre criava um slot NOVO em vez de mesclar
de volta no que sobrou na bag).

**Testes**: `tests/test_trade_state_stack_diff.py` (novo, 7 testes,
todos no HANDLER CLIENTE — lado que faltava cobertura) — reproduz o
cenário EXATO relatado (ofertar 1 de uma stack de 5 remove só 1);
mesmo cenário via modal de quantidade (ofertar 3 de 10); stack inteira
ainda remove o slot por completo (regressão, comportamento original);
2 sincronizações seguidas acumulam o decremento corretamente (diff
sempre contra o estado anterior, não cumulativo em duplicidade); item
não-empilhável continua funcionando; retirar oferta fatiada mescla de
volta no stack que sobrou; retirar sem stack existente cria slot novo.
Prova diferencial confirmada: com o código ANTIGO (`git stash` só do
arquivo tocado), 4 dos 7 testes falham reproduzindo o bug relatado —
com o fix, os 7 passam. Usuário confirmou em playtest real que o fix
funcionou antes da suíte completa rodar (mesma ordem: teste manual do
usuário primeiro, suíte automatizada depois, a pedido dele).

## 2. Colisão real de 2 tiles pra torre + barra de HP no topo + origem configurável de projétil (12/08/2026)

Sequência de 3 pedidos do usuário depois de testar o sprite novo da
torre (§41 mexeu no knockback; este item é sobre o sprite em si):
(1) "a torre não está usando a colisão definida pelo catálogo e sim o
retângulo que era renderizado antes", (2) "a barra de HP precisa ficar
no topo da sprite, não na base", (3) "quero saber se é possível colocar
um parâmetro... de qual parte do sprite surge os projéteis".

**Item 1 — colisão real de 2 tiles.** Investigação achou um conflito de
arquitetura real: neste motor TODA entidade de combate ocupa exatamente
1 tile (`TileMovement.current_tile_x/y`, único ponto lido por
`TileValidationSystem._occupied`/`is_tile_walkable`); o `collision_rect`
do catálogo de tileset só é consumido pelo subsistema de objetos
ESTÁTICOS de mapa (`OBJECT_MAPPING`, `maps/*_objects.csv`), nunca por
uma entidade de combate dinâmica — o sprite da torre (64×128px = 2 tiles
de largura na base) ficou visualmente maior que o hitbox real de 1
tile. Perguntado ao usuário: manter 1 tile (aceitar o "vazamento"
visual, como outros objetos grandes do jogo já fazem) ou implementar
ocupação real de 2 tiles — escolheu implementar de verdade, ciente do
escopo maior. Entrei em modo de planejamento (ver plano aprovado,
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`) por
ser mudança grande o bastante (8 pontos de código independentes,
cliente+servidor) pra não propor informalmente.

Design final (agente de design + verificação linha a linha própria
antes de implementar):
- `engine/world_systems.py::entity_footprint_tiles(world, entity_id, tm)`
  (novo, ponto único de verdade) — `[(tx,ty)]` pra qualquer entidade
  normal; pra torre, deriva a pegada real do PRÓPRIO catálogo do sprite
  (`engine/tileset.py::get_collision_offsets(OBJECT_MAPPING[sprite_id])`
  — a mesma função que os objetos estáticos já usam, `(0,96,64,32)` →
  `[(0,0),(1,0)]`, 1 tile a leste do tile âncora, batendo com a
  matemática de blit do `RenderSystem`). **Zero campo novo redundante**
  — a pegada nunca pode dessincronizar do visual porque é calculada a
  partir dele.
- Detecção "isso é uma torre?" usa `EntityIdentity.mob_key in
  TOWER_TABLE`, NUNCA `world.get_component(eid, Tower)` — achado crítico
  do agente de design: o espelho de torre reconstruído no CLIENTE
  (`client/remote_entity_handlers.py::_spawn_remote_mob` → `create_enemy`
  → `_build_combat_entity`) NUNCA ganha o componente `Tower` (só existe
  no lado que chama `create_tower()`, ou seja, só o servidor) — checar
  `Tower` faria a pegada nunca aparecer no cliente, reintroduzindo
  "bloqueia no servidor mas não visualmente" (rubber-band). `mob_key`
  sobrevive nos 2 lados via `_build_mob_spawn_payload`/
  `_build_combat_entity` (mecanismo já existente, usado antes pra
  som/entity_class da torre).
- 7 pontos de chamada trocados pra usar o helper: `TileValidationSystem.
  update()` (o mais importante — bloqueio de movimento real),
  `MinionSystem._get_occupied_tiles()`, `EnemyAISystem.
  _get_occupied_tiles()`, `SpawnZoneSystem._get_occupied()`,
  `_server_tiro_repulsivo`'s `_occ_kb` (colisão de knockback),
  `WorldServer._tick_harvestable_zones`'s `_occupied_for`, e
  `ui/systems.py::_get_enemy_tiles()` (client-side, evita o player local
  predizer andar por cima do 2º tile e levar snap de correção). 2 sites
  confirmados FORA de escopo (não tocados):
  `server/mob_system.py::npc_tiles` (filtra por `NPC`, que torre nunca
  tem) e a adjacência de taunt em `EnemyAISystem` (mesma família de
  range/targeting, deliberadamente fora do pedido).
- Escopo deliberado: só bloqueio/colisão usa a pegada de 2 tiles — mira,
  alcance, LOS e raio de visão da torre continuam no tile âncora
  (`Position`), sem mudança — usuário não pediu isso.

**Item 2 — barra de HP no topo.** Regressão introduzida pelo sprite
novo: `_hud_top_world_y` (`ui/systems.py::RenderSystem.render()`)
calculava a partir de `renderable.height` (altura antiga do
retângulo/tier, ~40px) — pra um sprite de 128px isso deixava a barra no
meio/base, não no topo. Fix: quando `renderable.sprite_id` está setado,
ancora pela altura REAL do sprite (`TILE_SPRITES.get_raw_sprite(...)
.get_height()`), mesma fórmula de base que o `RenderSystem` já usa pra
desenhar o próprio sprite (`position.y + TILE_SIZE/2 - sprite_height`),
só que subindo até o TOPO em vez do rodapé. Sem teste automatizado
(mudança puramente visual, mesma convenção do projeto pra esse tipo de
caso) — validar em playtest.

**Item 3 — origem configurável do projétil.** Novo campo `Tower.
projectile_origin_offset` (tupla de pixels, por TIPO em
`content/tower_definitions.py::TOWER_TABLE`, default `(0,0)` = nasce do
centro, comportamento de sempre — nenhuma torre existente tem valor
setado ainda, decisão de conteúdo fica pro usuário depois). Aplicado em
`engine/world_systems.py::_spawn_attack_projectile` (função
COMPARTILHADA com `MinionSystem`, que nunca passa o parâmetro — fica
`(0,0)` pra minion sempre): desloca só o PONTO DE NASCIMENTO visual da
Position da torre, nunca a mira (`dir_x`/`dir_y` continuam calculados a
partir da Position real, não do ponto deslocado, senão o tiro desviaria
da direção correta).

**Testes**: `tests/test_towers.py::TestTowerFootprint` (4 testes — 2º
tile bloqueia pra player/`is_tile_walkable`, `EnemyAISystem`/
`MinionSystem` tratam como ocupado, entidade comum continua 1 tile,
posicionamento real de torre nos 3 mapas com `"towers"` não colide com
terreno sólido — trava permanente, não checagem manual pontual);
`tests/test_client_ui.py::test_espelho_remoto_de_torre_bloqueia_o_
segundo_tile_pro_player_local` (prova a paridade cliente/servidor —
único teste que reproduz o espelho REMOTO como o cliente de verdade
constrói, sem o componente `Tower`); `tests/test_towers.py::
TestTowerProjectileOriginOffset` (2 testes — default zero, offset
desloca nascimento sem desviar mira). Todos confirmados por revert
manual (marcadores `# TEMP-REVERT` temporários, removidos depois).
Suíte de `test_towers.py`/`test_client_ui.py`/`test_minions.py`/
`test_server.py`/`test_faction.py`/`test_service_npcs.py` rodada junta
2x ao longo da sessão (349 testes), sem regressão.

**Pendente**: validação visual/manual do usuário em jogo (posição da
barra de HP, e a colisão de 2 tiles não quebrar nenhuma lane de minion
em `moba_battleground` — risco já sinalizado no plano aprovado, nenhuma
lane testada em playtest real ainda).

**Playtest real (mesmo dia) achou 2 problemas que a validação acima não
cobriu:**

1. **Item 2 (barra de HP) continuava errado em jogo** — o fix aplicado
   em `ui/systems.py::RenderSystem.render()` só cobre "mob local/offline"
   (comentário do próprio código, já existia antes desta sessão). A
   torre dentro da BG é sempre uma entidade REMOTA do ponto de vista do
   player local — o HP dela é desenhado por uma função TOTALMENTE
   diferente, `client/remote_entity_handlers.py::_draw_mob_hp_bars`,
   nunca tocada no fix original. Corrigido lá também: mesma lógica
   (altura real do sprite via `TILE_SPRITES.get_raw_sprite(...)
   .get_height()` em vez do `W = TILE_SIZE-4` fixo). Sem teste
   automatizado (mudança visual). **Lição**: ao corrigir algo que
   envolve entidade remota, sempre checar se existe um caminho de
   renderização/HUD SEPARADO pra remoto antes de declarar "corrigido" —
   já documentado como padrão recorrente neste projeto (mob local vs.
   remoto têm passes de desenho DIFERENTES em vários pontos), mas essa
   sessão ainda caiu nele por não checar `client/
   remote_entity_handlers.py` antes de considerar o item 2 fechado.

2. **Traçado de seleção (amarelo) e clique não acompanhavam o sprite
   novo** — achado que não tinha sido pedido antes (item novo do
   usuário, não regressão de nada já implementado): o contorno de
   seleção (`RenderSystem.render()`, `pygame.draw.rect(...,
   (255,220,0), rect, 2)`) e o hit-test de clique
   (`MouseTargetingSystem._enemy_at_world_pos`) usavam os dois o mesmo
   `rect`/caixa antigos baseados em `Renderable.width/height` (~40px),
   nunca o sprite real. Corrigido nos 2 lugares: quando
   `Renderable.sprite_id` está setado, tanto o `rect` usado pro
   contorno quanto a caixa de clique passam a usar a bounding box REAL
   do sprite (mesma âncora de base do desenho — "tracejado em volta da
   sprite", exatamente a opção que o usuário marcou como "perfeita").
   Mecanismo genérico (chaveado por `sprite_id`, não por "é torre") —
   qualquer entidade futura com sprite ganha os dois de graça, sem
   código novo.

**Testes**: `tests/test_client_ui.py::
test_clique_seleciona_torre_pelo_2o_tile_do_sprite_nao_so_pelo_
retangulo_antigo` (novo, confirmado por revert manual — clique no 2º
tile do sprite falhava antes do fix, `-1` em vez do eid da torre).
Contorno de seleção não tem teste automatizado (mesma razão do item 1 —
puramente visual); `_draw_mob_hp_bars` idem.

## 3. Stealth de bush estilo MOBA na BG (13/08/2026, feature nova)

Pedido do usuário: regra clássica de bush de MOBA — player/minion dentro
de um bush fica invisível pra quem não tem presença física (própria ou
de time) dentro do MESMO bush, mesmo que o bush esteja dentro do raio
normal de visão (AOI); quem tinha esse alvo selecionado perde a mira ao
ele entrar na bush. Planejado formalmente (EnterPlanMode) antes de
qualquer código, com pesquisa no próprio codebase antes de desenhar
qualquer coisa nova — ver `C:\Users\l4nce\.claude\plans\
expressive-wondering-starlight.md` pro plano completo.

**Decisões confirmadas com o usuário antes de implementar**:
- Visão de bush é **compartilhada por time** (padrão League of Legends
  de verdade — 1 unidade do time dentro do bush revela pro time
  inteiro), não só "quem está literalmente no bush enxerga".
- "Entrar na fog" pra perder o alvo já funciona hoje via AOI normal
  (saiu do raio de visão = alvo some) — nenhum código novo precisou
  disso. A regra nova é bush ser uma EXCEÇÃO ao AOI: mesmo dentro do
  raio normal de visão, o bush não revela quem está nele nem o que está
  atrás.
- Posicionamento das sprites de bush no mapa é manual, pelo usuário, no
  editor — este trabalho só constrói o MECANISMO de jogo (zona), não
  pinta nenhuma bush real em nenhum mapa.
- Vale pra players E minions (torres excluídas — não têm `TileMovement`
  que muda de tile, mesma exceção já usada pra imunidade a CC).

**Achado-chave — reaproveitar Zona PvP, não inventar do zero**:
`server/pvp_zone_processor.py::_in_pvp_zone` já resolvia exatamente o
mesmo problema de forma (retângulo por mapa, carregado de
`<mapa>_entities.json`, checado sob demanda contra
`TileMovement.current_tile_x/y`, sem cache, sem tick dedicado) — copiado
linha a linha pra bush (`bush_zones` no entities.json, `server/
bush_zone_processor.py::BushZoneProcessorMixin::_get_bush_zone`).

**Achado sobre minions**: `create_minion` (`engine/entity_factory.py`)
não dá `CombatState` a minions — só `Faction`/`TileMovement`/
`CombatStats`/`Minion`. Guardar `bush_zone` em `CombatState` (cache por
componente) foi descartado por causa disso; a visibilidade em si é
recomputada sob demanda (nunca cacheada) então isso não importa pra
`_can_see`. O alvo de minion mora em `Minion.current_target_eid`
(campo próprio, não `CombatState.target_entity_id`), então o sweep de
limpar alvo travado trata os dois separadamente.

**Implementado**:
- `engine/map_loader.py` — parse de `bush_zones` do `<mapa>_entities.json`
  (mesmo formato de `pvp_zones`).
- `server/bush_zone_processor.py` (novo) — `_get_bush_zone(eid)` (índice
  da zona ou `None`, sob demanda) e `_team_sees_bush_zone(viewer_eid,
  zone_idx)` (algum aliado, mesmo mapa, fisicamente na zona agora).
- `server/session.py::_can_see` — assinatura trocou de `(world, viewer,
  target)` pra `(world_server, viewer, target)` (precisa de acesso ao
  mixin acima) — 5 call sites atualizados. Bush é um gate INDEPENDENTE
  do `is_visible` (Camuflagem é global; bush é por PAR viewer/alvo).
  Nenhuma ponte tipo `_visibility_changed_this_tick` foi necessária —
  entrar/sair de bush sempre exige mover, então o sweep de `moved` já
  existente em `_build_update_for_session` reavalia sozinho; o sweep de
  "outros players ainda não conhecidos" (roda todo tick, sem gate de
  movimento) já cobre "meu aliado entrou no bush do inimigo escondido,
  mesmo eu parado".
- `server/world_server.py::_tick_bush_target_clear()` (novo, chamado
  todo tick sem throttle, mesmo padrão de `_tick_harvestable_zones`) —
  o único pedaço que PRECISA de detecção de transição (cache leve,
  `self._bush_zone_cache: dict[int,int]`, dict de instância nome
  visível, mesmo padrão já aprovado de `_damage_tracker`/
  `_lethal_interceptor`): `CombatState.target_entity_id`/`Minion.
  current_target_eid` já travados ANTES da entrada no bush não se
  limpam sozinhos (mesmo motivo já documentado na Camuflagem,
  `engine/skill_handlers.py::_skill_camuflagem`: is_visible/bush
  sozinho só bloqueia mira NOVA) — sweep explícito só na transição, não
  every tick pra todo mundo.
- Cliente: nenhuma mudança. Mesma lógica de Camuflagem — uma entidade
  que `_can_see` recusa nunca chega no `AOI_UPDATE`/`ENTITY_SPAWN`
  daquele viewer.

**Fora de escopo, não incluído**: feedback visual local ("estou numa
bush" — sprite semi-transparente tipo o efeito de Camuflagem). Usuário
não pediu; adicional pequeno e independente se quiser depois.

**Testes** (`tests/test_bush_stealth.py`, 16 novos): `_get_bush_zone`
(dentro/fora/múltiplas zonas/mapa sem zona); `_can_see` (alvo em bush
sem time no bush → invisível; mesmo bush → visível; aliado no bush
revela pro time; time adversário no bush NÃO revela; minion também
oculto e também revela pro time; fora de bush não regride
comportamento normal); alvo travado (some ao entrar sozinho; sobrevive
se os 2 entram juntos; nada acontece sem transição); minion perde
`current_target_eid`/volta pra `ADVANCING`; torre nunca participa.
Confirmados por revert manual (comentado o bloco novo de `_can_see`) —
5 testes falham sem o fix (exatamente os que dependem de oclusão
acontecer), os outros 11 continuam passando por não dependerem dela
(prova que os 5 são os testes certos, não falsos positivos). Suíte
completa (1159 testes, `pytest tests/ -q`) sem regressão (1 falha
observada é flakiness de timing pré-existente e não relacionada,
`test_perf_log_improvements.py`, passa isolada).

**Pendente**: usuário pintar sprites de bush (`pl_b1`..`pl_b20`,
`engine/tileset.py:772-791`) no editor em `maps/moba_battleground_
objects.csv`; depois, adicionar os retângulos correspondentes em
`bush_zones` no `moba_battleground_entities.json` pra ativar o
mecanismo de verdade no mapa real. Nenhum dado de mapa real foi mudado
por este trabalho.

## 4. Monstros de jungle estilo MOBA — normal + boss (13/08/2026, retomado de sessões anteriores)

Pedido retomado — usuário lembrava de já ter pedido isso antes, mas não
tinha certeza se tinha sido implementado. Investigação confirmou que
NÃO tinha: a única menção a "jungle" no projeto inteiro era o roadmap
original em `next_implementations/battlefield_design.md` (item #5 de 8,
nunca virou código). Planejado formalmente (EnterPlanMode) com pesquisa
prévia no codebase — ver `C:\Users\l4nce\.claude\plans\
expressive-wondering-starlight.md` (mesmo arquivo reaproveitado do
plano de bush stealth, já implementado antes).

**Requisitos confirmados com o usuário**: 2 tipos — normal (hostil aos
2 times, XP/gold no MESMO padrão que Minion já usa — proximidade sem
filtro de time pra XP, gold só pro golpe final) e boss (XP e ouro por
proximidade, mas restritos ao MESMO time de quem deu o golpe final —
diferente de minion, que não filtra por time; time inteiro — jogadores
E minions vivos no momento — ganha buff temporário; ciclo de buff muda
a cada abate numa lista ordenada, repete o ÚLTIMO buff pra sempre
depois de esgotar a lista; sem drop de item por enquanto).

**Achado-chave — quase tudo já existia, foi questão de conectar**:
- `_build_combat_entity` (`engine/entity_factory.py`) já resolvia mob
  por uma CADEIA de fallback (`MOB_TABLE.get(race) or TOWER_TABLE.get(race)
  or MINION_TABLE.get(race)`) — mesmo mecanismo que já dava vida ao
  espelho de Torre/Minion no cliente. Só precisou ACRESCENTAR
  `JUNGLE_MOB_TABLE`/`JUNGLE_BOSS_TABLE` nessa cadeia — `create_enemy()`
  (que já dá `AIControlled`/aggro/chase/leash de graça) passou a
  funcionar pra jungle sem nenhuma função de criação nova, e o cliente
  reconstrói o espelho remoto automaticamente pelo mesmo caminho.
- Faction `"monstros_hostis"` já tinha `("arena_time_a",
  "monstros_hostis"): "hostil"` e `("arena_time_b", "monstros_hostis"):
  "hostil"` cadastrados em `content/faction_data.py` — reaproveitar essa
  faction fechou "hostil aos 2 times" sem tocar em `faction_data.py`
  nem em `engine/faction_system.py`.
- Buff de time: `Modifier(attribute, value, type, source="buff")` +
  `add_timed_modifier` (`engine/stat_fns.py`) já é o mecanismo real
  usado pela skill Canção de Inspiração — reaproveitado 1:1, nenhum
  sistema de buff novo.

**Sem precedente** (confirmado por pesquisa, desenhado do zero): nenhum
respawn do projeto (torre/harvestable/wave de minion) muda uma
propriedade a cada ciclo — todos voltam pro MESMO template sempre.
Resolvido com `WorldServer._jungle_boss_cycle_index` (dict de módulo/
instância, nome visível — mesmo padrão já aprovado de
`_damage_tracker`/`_lethal_interceptor`), chave = posição de ORIGEM do
camp (via `InitialPosition`, nunca a posição de morte — o boss pode ter
perseguido alguém antes de morrer), incrementado só no momento do
ABATE, nunca no respawn (precisa sobreviver ao ciclo de timer, que é
descartado a cada respawn).

**`JungleMob` novo** (`engine/components.py`) — marca qualquer entidade
criada via `JUNGLE_MOB_TABLE`/`JUNGLE_BOSS_TABLE` (`is_boss`,
`xp_reward`, `gold_min/max`, `respawn_s` — este último é parâmetro de
INSTÂNCIA do mapa, guardado aqui pra sobreviver até a morte). `XPReward`
(auto-anexado por `create_enemy`) é removido logo em seguida — jungle
usa reward PRÓPRIA, nunca a genérica.

**`server/server_death_handler.py`** — gate `_jungle_dh` ao lado de
`_minion_dh`/`_tower_dh` já existentes. Normal entra nos MESMOS 3
pontos que minion já usa (base_xp, proximidade sem filtro de time,
gold-só-pro-killer) via `or`-clause nas condições existentes — sem
duplicar lógica. Boss tem ramo PRÓPRIO (novo bloco, roda ANTES da
lógica genérica — a genérica cai em `else: 0`/`_gold_amt=0` pra boss
por construção, sem precisar de guard explícito extra em quase nada):
acha o time do killer (mesmo fallback já usado por `_gold_recipient_eid`
— primeiro player do damage_log se o golpe final não foi de um player),
filtra jogadores próximos pelo MESMO `Faction.faction_id`, divide XP e
ouro entre eles, e chama `WorldServer._grant_jungle_boss_buff` (time
inteiro, não só quem está perto).

**Achado real durante os testes** (revert-to-confirm pegou um teste
fraco): `test_gold_dividido_entre_membros_do_time_perto` checava só
`wallet.gold > 0` (absoluto) em vez de comparar antes/depois — passava
mesmo com o ramo de boss inteiro desligado, porque personagem novo já
nasce com gold > 0 de qualquer forma. Corrigido pra comparar delta
(`gold_antes`), como os testes irmãos já faziam — só aí o revert
realmente derrubou o teste. Fica registrado como lembrete de que
"assert absoluto sem baseline" é uma classe de teste fraco fácil de
escrever sem perceber.

**Testes** (`tests/test_jungle_camps.py`, 13 novos): hostilidade aos 2
times; XP/gold normal seguem minion (proximidade sem filtro de time,
gold só pro killer); boss filtra por time (XP e gold, incluindo teste
de que adversário perto NÃO ganha nada); gold dividido entre membros do
MESMO time perto; buff pro time inteiro incluindo quem está LONGE e
minions vivos; ciclo de buff muda por abate e trava no último; respawn
na mesma posição; `_build_combat_entity` reconstrói jungle mob/boss
pela cadeia de fallback nova. Confirmados por revert manual (2 rodadas
— ramo de boss inteiro, e a extensão do proximity-gate pro mob normal)
— ambos os grupos de teste certos falham sem o fix correspondente.
Suíte completa (1172 testes, `pytest tests/ -q`) sem regressão.

**Pendente**: nenhuma posição real de camp/boss foi adicionada em
`maps/moba_battleground_entities.json` — mecanismo pronto, aguardando o
usuário decidir onde posicionar (mesma decisão já tomada pro bush:
mecanismo primeiro, conteúdo/posicionamento depois). Catálogo de buff
inicial (`content/jungle_definitions.py::JUNGLE_BOSS_TABLE["jungle_boss_
ancestral"]["buff_cycle"]`) e valores de XP/gold/atributos são ponto de
partida sugerido, não balanceamento final.

## 5. Linha de visão de terreno entre times — nunca existia no servidor (13/08/2026)

Usuário validou §53 e levantou 2 problemas relacionados, pedindo
discussão formal antes de qualquer código (regra restaurada em §51,
reforçada aqui de novo): (1) player de um time via o player do time
adversário mesmo com bush/parede/árvore no meio; (2) player adversário
parado exatamente na base de uma bush não ficava invisível pro outro
time (stealth clássica de MOBA).

**Investigação (antes de propor)**:
- `grep bush_zones maps/*_entities.json` → zero ocorrências nos 5
  mapas. A stealth de bush (§47, testada com 16 testes sintéticos)
  nunca foi ligada a nenhum mapa real — explica o item 2 sozinho.
- `server/session.py::_can_see` (gate central de visibilidade AOI) —
  só checava `is_visible`(Camuflagem/ghost) + zona de bush (sempre
  `None` na prática). **Nunca existiu checagem de linha-de-visão
  contra o terreno** — item 1 é feature nova, não regressão.
- `server/world_server.py:753` já chama `create_tilemap` (código
  compartilhado com o cliente) — `tile_matrix`/`vision_height` já
  existe no servidor, só não era consultado pra visibilidade entre
  entidades.
- Já existiam 3 implementações Bresenham quase-duplicadas no projeto
  (`engine/skill_handlers.py::_has_los`/`_dash_path_clear`,
  `engine/world_systems.py::EnemyAISystem._has_line_of_sight`) — todas
  excluem os 2 extremos (semântica de "tiro limpo": nem origem nem
  alvo bloqueiam o próprio tiro). Errada pro que precisava aqui: um
  alvo parado EM CIMA de um tile bloqueante precisa contar como
  escondido, então o tile FINAL tem que ser checado. Escrita uma
  função nova (`bresenham_line_tiles`) em vez de reaproveitar essas —
  semântica genuinamente diferente, não duplicação evitável. As 3
  antigas não foram mexidas (fora de escopo, sem necessidade real de
  tocar código que já funciona).

**Decisões confirmadas com o usuário** (via `AskUserQuestion`, antes
de codar):
- Vale pra QUALQUER coisa que já bloqueia visão (`vision_height>=2` —
  parede, árvore, pedra grande, bush), não só bush.
- Vale pra players E minions/mobs do time adversário.

**Implementado**:
- `engine/utils.py::bresenham_line_tiles(x0,y0,x1,y1)` (novo) — início
  excluído, FIM INCLUÍDO (diferente das 3 Bresenham existentes,
  propositalmente).
- `server/tile_los_processor.py` (novo, mesmo padrão de
  `bush_zone_processor.py`) — `TileLosProcessorMixin._has_tile_los`:
  anda o segmento entre viewer e alvo, retorna `False` se qualquer
  tile do caminho (incluindo o do alvo) tiver `vision_height>=2`.
  Mixado em `WorldServer`.
- `server/session.py::_can_see` — novo gate, só quando os 2 têm
  `Faction` E são de times DIFERENTES (aliado sempre vê aliado, mesmo
  espírito de `_team_sees_bush_zone`); entidade sem `Faction` (mundo
  PvE) não participa.
- Gate de zona de bush antigo (`_get_bush_zone`) mantido como está —
  dormant (nenhum mapa usa), não conflita com o novo, não vale a pena
  remover código testado sem necessidade real.
- Sem cache/throttle novo — `_can_see` já roda por par viewer×alvo em
  AOI todo tick (5 call sites); custo do raycast é O(distância em
  tiles), tipicamente poucas dezenas. Regra do projeto: medir com
  profiling antes de otimizar "no escuro" — revisar só se um teste de
  carga real apontar isso como gargalo.

**Testes** (`tests/test_tile_los_visibility.py`, 6 novos): parede entre
adversários esconde; parede entre ALIADOS não esconde (gate só entre
times diferentes); sem obstrução continua visível (regressão); alvo em
cima de bush fica escondido mesmo SEM obstrução no meio (prova que o
tile final conta — achei e corrigi um erro no próprio teste aqui: usei
`BUSH_TILE` legado por engano, que tem `vision_height=1` e nunca
bloqueia — trocado pro tile real do catálogo, `pl_b1`); minion
adversário atrás de parede também esconde; zona de bush antiga (§47)
continua funcionando isolada, sem depender do gate novo. Também achei
e corrigi 2 testes que dependiam do CONTEÚDO REAL de `map_1.csv` nas
coordenadas escolhidas (deram falso positivo/negativo por causa de
terreno de verdade ali) — corrigido limpando um retângulo pra
`FLOOR_TILE` antes de cada teste, pra não depender de mapa real.
Confirmados por revert manual: os 3 testes que dependem do gate novo
falham exatamente como esperado; os outros 3 continuam passando (prova
que são os certos, não falsos positivos). Suíte completa
(`pytest tests/ -q`) sem regressão.

**Pendente**: usuário validar em jogo (2 clients, times opostos) que
bush/parede/árvore escondem o time adversário de verdade agora, e que
ficar na base de uma bush esconde o player.

## 6. Bush: visão compartilhada dentro da mesma bush + "atacar revela" (LoL-style) (13/08/2026)

Usuário validou §54 e pediu 2 refinamentos, mesmo procedimento (código
+ referência MOBA/LoL + proposta + confirmação antes de codar): (1)
adversário que entra na MESMA bush deveria se ver com quem já está lá,
independente de time; (2) atacar (player ou minion, tanto faz) tira o
atacante de stealth.

**Investigação**: `_has_tile_los` (§54) não tinha noção de "estamos na
mesma bush" — só olhava se o tile do ALVO bloqueia, sem considerar
onde o VIEWER está; 2 players na mesma bush não se viam (lacuna real).
Nenhum precedente de "atacar quebra stealth" no projeto (Camuflagem só
expira por tempo).

**Referência** (LoL, via WebSearch): "While inside of a brush, using
targeted attacks and abilities will reveal a 300 radius centered
around the champion for 2 seconds" — e confirmado em fórum: revela
você E todo mundo que está na mesma bush. Números usados como ponto de
partida (2s); alcance = decisão do usuário (AOI normal, não o raio de
300 unidades do LoL — versão simples confirmada via `AskUserQuestion`).

**Desenho — 1 conceito unificado ("isenção de bush")**: tanto "estar
na mesma bush" quanto "atacou há pouco" cancelam SÓ bloqueio de tile
ANDÁVEL-bloqueante (bush/copa, `vision_height>=2 and not is_solid`),
NUNCA bloqueio de sólido (parede/pedra/tronco) — atacar não deveria
deixar ninguém ver através de uma parede de verdade.

**Implementado**:
- `ui/fov.py` → `engine/fov.py` (MOVIDO, conteúdo intocado) —
  `local_vision_blob`/`compute_fov` (§50) já faziam exatamente o
  cálculo de "blob conectado de bush" que a regra 1 precisa; moveu pra
  `engine/` pra virar ponto único de verdade compartilhado de verdade
  entre cliente (fog) e servidor (visibilidade), em vez de "cliente que
  o servidor por acaso também consegue importar" (`ui/__init__.py` é
  vazio, então tecnicamente já era seguro, mas architecturalmente
  errado). 3 importadores atualizados (`ui/systems.py` e 2 arquivos de
  teste).
- `engine/components.py::CombatState.bush_reveal_timer` (novo,
  `float`) — setado em `apply_damage_core` quando `killer_eid` tem
  `CombatState` (só player — minion nunca tem, `create_minion` não
  anexa, então isso já escopa "só player revela a si mesmo ao atacar"
  sem checagem extra de tipo). Decrementado em
  `BaseCombatStateSystem._tick_bush_reveal_timer` (mesmo padrão de
  `_tick_combat_timer`/`_tick_stun_timer`, herdado automaticamente por
  cliente E servidor).
- `shared/constants.py::BUSH_REVEAL_DURATION_S = 2.0`.
- `server/tile_los_processor.py::_shares_bush_blob` (novo) —
  `local_vision_blob` a partir do tile do ALVO, checa se o tile do
  VIEWER está no mesmo blob; custo zero se o alvo não estiver numa
  bush. `_has_tile_los` reestruturado: computa `bush_exempt` (mesma
  bush OU `bush_reveal_timer>0`) uma vez, e um tile só bloqueia se
  `not (bush_exempt and not tile.is_solid)`.

**Testes** (`tests/test_tile_los_visibility.py`, 5 novos, classe
`TestBushExemption`): adversários na MESMA bush se veem; adversários em
bushes SEPARADAS (blobs diferentes) continuam escondidos; atacar
revela e, depois do timer decair, volta a esconder; isenção de bush
NUNCA cancela parede sólida real (mesma bush + reveal ativo, parede de
verdade no meio ainda bloqueia); minion não ganha `bush_reveal_timer`
ao atacar (sem `CombatState`, regressão do escopo "só player"). Achado
e corrigido um erro de setup nos próprios testes durante a escrita: um
teste falhava por causa de conteúdo REAL de `map_1.csv` numa
coordenada (x=5) fora da área que eu limpava pra `FLOOR_TILE`
(x=8..40) — corrigido movendo as coordenadas de teste pra dentro da
área limpa, não mudando o mecanismo. Também trocado IDs de facção
sintéticos (`"time_a"/"time_b"`) pelos reais e já registrados como
hostis em `content/faction_data.py` (`arena_time_a`/`arena_time_b`) —
o dano de teste (`apply_damage_core`) estava sendo bloqueado como
"amigável" com IDs inventados. Confirmado por revert manual (`bush_
exempt` forçado a `False`): os 2 testes que dependem da isenção falham
exatamente como esperado; os outros 9 (incluindo o de parede-nunca-
vazando e o de minion) continuam passando. Suíte completa
(`pytest tests/ -q`) sem regressão.

**Pendente**: usuário validar em jogo — 2 clients, times opostos,
ambos entrando na mesma bush (devem se ver); um ataca de dentro da
bush (deve revelar por ~2s pro adversário em alcance).

## 7. Visão de terreno vira universal (todo o jogo, toda entidade) + bush usa isenção da PRÓPRIA posição do viewer (13/08/2026)

Usuário corrigiu 2 pontos depois de validar §54-56, mesmo procedimento
(explicar entendimento antes de codar, usuário confirmou/corrigiu 2x
antes do desenho final ser aprovado):

1. **Bush**: a isenção "mesma bush" (§55) calculava o blob a partir da
   posição do ALVO — por isso um player no MEIO de uma bush grande
   ainda não enxergava quem tinha acabado de sair (o caminho reto
   cruzava OUTRAS células da MESMA bush que o VIEWER ocupa, e nada
   isentava essas células). Usuário simplificou o modelo: "o player só
   tem a flag de stealth enquanto está na bush; ao sair, fica visível
   normalmente, a menos que esteja em OUTRA bush distante ou fora do
   alcance de visão".
2. **Escopo**: o gate (§54) só rodava "entre facções diferentes" — no
   mundo aberto, player comum não tem NENHUMA Faction (confirmado:
   `SessionManager._compute_ally_vision_centers` já documentava isso —
   "Players comuns não têm Faction nenhuma; só ganham ao entrar num
   contexto de time"), então o gate nunca disparava lá. Usuário quer
   isso universal: fog/LOS vale pra QUALQUER par de entidades (player/
   mob/npc/minion), mundo aberto E BG — mesma régua que mob já usa. A
   ÚNICA parte exclusiva de contexto de time/instância é a VISÃO
   COMPARTILHADA (aliado, não necessariamente o viewer, dentro da bush
   revela ela pro time inteiro).

**Desenho**:
- Isenção de vegetação bloqueante (bush/copa, `vision_height>=2 and
  not is_solid`) sempre a partir da posição do PRÓPRIO viewer — mesmo
  princípio já usado no cliente (`engine/fov.py::local_vision_blob`,
  §50), nunca mais "blob do alvo". "2 players na mesma bush se veem"
  cai de graça disso (mesma componente conectada = mesmo blob, sem
  checagem extra).
- `server/session.py::_can_see` para de filtrar por facção antes de
  chamar `_has_tile_los` — roda sempre, pra qualquer par.
- Visão compartilhada de time reaproveita o MESMO gate que
  `_compute_ally_vision_centers` já usa (`Faction` explícita via
  `get_component` direto, nunca resolver com fallback — só existe
  Faction real dentro de contexto de time) — se o viewer tem Faction,
  e algum ALIADO (mesma Faction, mesmo mapa) está dentro do blob de
  uma célula que bloquearia o caminho, essa célula também isenta.
- Sólido (parede/pedra grande/tronco) nunca é isento por nenhuma das
  regras acima.

**Implementado**:
- `server/tile_los_processor.py::_has_tile_los` reescrito — 1 blob do
  viewer computado por chamada; blob de aliados sob demanda (só se
  viewer tem Faction E nenhuma outra isenção já resolveu — custo zero
  fora de contexto de time). `_shares_bush_blob` (§55, baseada na
  posição do alvo) removida por inteiro, não ficou como código morto.
- `server/session.py::_can_see` — condição de facção removida.

**Achado ao rodar a suíte depois do fix**: 2 testes de
`tests/test_bush_stealth.py` (§47, mecanismo de zona-retângulo
antigo) começaram a falhar — usam posições bem distantes
(`_OUTSIDE=(5,5)` a `_IN_A=(102,102)`) sem nunca ter se preocupado com
terreno real no caminho, porque antes o gate de LOS nunca rodava pra
esse par (mesma Faction sintética `"time_x"` nos 2, ou nenhuma). Com o
gate virando universal, terreno real de `map_1.csv` nesse trecho
passou a interferir num teste que não tinha nada a ver com terreno —
corrigido limpando o retângulo pra `FLOOR_TILE` no `setUp`, mesmo
padrão já usado em `tests/test_tile_los_visibility.py`.

**Testes** (`tests/test_tile_los_visibility.py`, reescrito — 17 no
total): universal sem Faction nenhuma (parede esconde no mundo aberto,
antes não escondia); parede esconde entre ALIADOS também (mudança de
comportamento intencional — sólido não tem mais exceção de time);
viewer no meio de uma bush grande vê quem saiu cruzando outras células
da MESMA bush (prova a correção do bug 1); mutual bush (cai de graça
do blob do viewer); bushes diferentes continuam escondendo; visão
compartilhada de time (aliado, não o viewer, dentro da bush revela);
regressão — sem Faction não há compartilhamento nenhum; sólido nunca
é isento (nem por blob nem por reveal nem por aliado); `bush_reveal_
timer` continua funcionando; minion não ganha o timer; revalidação ao
sair (§56) continua funcionando com o novo mecanismo por baixo.
Confirmado por revert manual em 3 rodadas separadas (universal, blob
do viewer, aliados) — cada uma derruba exatamente o subconjunto de
testes esperado, o resto continua passando (prova que cada isenção
está testada pelo teste certo, não um falso positivo). Suíte completa
(`pytest tests/ -q`) sem regressão depois do ajuste em
`test_bush_stealth.py`.

**Pendente**: usuário validar em jogo — mundo aberto (sem contexto de
time), 2 players com parede/bush no meio deveriam parar de se ver;
dentro da BG, um aliado dentro da bush deveria revelar ela pro time
inteiro mesmo o viewer estando longe.

## 8. Torre/minion do próprio time sempre visível + varredura de AOI ganha o `_can_see` que faltava (13/08/2026)

Usuário reportou (2 prints de uma partida de teste em
`moba_battleground`): torres e minions somem pros dois times assim
que um player entra numa bush, e um inimigo dentro da visão de um
minion aliado não apareceu pra outro player do mesmo time do outro
lado da bush. Pediu investigação profunda ANTES de qualquer código —
regra restaurada em §51/§57.

**Achado A — não é bug, é conteúdo real**: varredura de 45×45 tiles ao
redor da jungle de `moba_battleground.csv` — 839 de 2025 tiles (41%)
bloqueiam visão. Antes de §57 (LOS universal) essa regra quase nunca
rodava de verdade (só entre facções diferentes); virando universal,
esse tanto de bloqueio real do mapa passou a valer pra todo mundo pela
primeira vez — inclusive pro PRÓPRIO time, que MOBA nenhum esconde.

**Achado B (fix real)**: torre/minion não tinham nenhuma exceção pro
próprio time — ficavam sujeitos à mesma regra de LOS que um inimigo.
Corrigido: `server/session.py::_can_see` ganhou um bloco ANTES do
gate de LOS — se o alvo tem `Tower` ou `Minion` E viewer/alvo têm a
MESMA `Faction` explícita (mesmo padrão de `_compute_ally_vision_
centers`, nunca fallback), retorna `True` direto. Torre/minion
ADVERSÁRIO continua 100% sujeito à regra normal (LOS de verdade,
sem exceção).

**Achado C (investigado, FALSO POSITIVO — registrado pra não
reinvestigar)**: hipótese inicial — "aliado (minion) entra na bush
onde um inimigo está, mas outro player do time nunca descobre esse
inimigo, porque o único gatilho de descoberta de player é a lista de
`moved` do PRÓPRIO alvo/viewer". Cheguei a IMPLEMENTAR um fix (incluir
players na varredura de mob) antes de perceber, escrevendo o teste
diferencial, que já existe um bloco SEPARADO e não documentado no
raciocínio inicial dentro de `_build_update_for_session`
(`for other_session in self._sessions.values(): ... if not _can_see
(...): continue ...`) que já re-varre TODOS os players conectados,
todo tick, incondicionalmente, já com `_can_see` aplicado — sem
depender de nenhum "moved". O teste que "provava" o bug tinha um erro
de setup (`Session.authenticated` nunca setado como `True` — atributo
default `False`, mecanismo já existente pulava a sessão por isso). Com
o teste corrigido, o mecanismo JÁ existente passou de primeira, sem
precisar do fix novo. O fix redundante foi revertido antes de
qualquer commit — código shipado não inclui nada do item 3 do plano
original. Ver `tests/test_team_visibility_sweep.py`
(`TestAllyTriggeredPlayerDiscovery`), reescrito como teste de
REGRESSÃO do mecanismo pré-existente, não como prova de fix novo.

**Achado D (fix real, mais sério — achado investigando, não
relatado)**: a varredura genérica de "entidade em AOI ainda não
conhecida" (`server/session.py::_build_update_for_session`, cobre só
`_mob_eids` — mob/torre/minion — + harvestable, nunca players)
**nunca chamava `_can_see`** — só checava distância via
`SpatialHash`. Testado direto: uma torre adversária sem LOS nenhum
ficava marcada como "conhecida" só por estar dentro do raio de AOI.
Corrigido com o mesmo padrão que o check de `_harvestable_visible_to`
já usa ali do lado: `if not _can_see(self.world_server, session.
entity_id, mob_eid): continue` antes de marcar como descoberto — junto
com o item B, torre/minion ALIADO continua sempre visível (bypassa
dentro de `_can_see`), só ADVERSÁRIO passa a respeitar bush/parede/
Camuflagem de verdade nessa varredura específica.

**Testes** (`tests/test_team_visibility_sweep.py`, novo, 7 testes):
`TestOwnTeamStructureAlwaysVisible` (4 — torre/minion próprio time
atrás de parede real continua visível; torre/minion adversário atrás
de parede continua escondido, prova que a exceção não vazou pro lado
errado); `TestSweepRespectsLos` (2 — torre adversária sem LOS não é
"descoberta" só por estar perto; torre adversária COM LOS continua
sendo descoberta normalmente); `TestAllyTriggeredPlayerDiscovery` (1,
regressão do Achado C). Confirmado por revert manual, blocos
separados: comentar a exceção de torre/minion (item B) derruba
exatamente os 2 testes de "próprio time atrás de parede"; comentar o
`_can_see` da varredura (item D, `if False and ...`) derruba
exatamente `test_torre_adversaria_sem_los_nao_e_descoberta_pela_
varredura` — os outros 6 continuam passando em cada rodada, provando
que cada teste cobre exatamente o bloco que diz cobrir. Suíte completa
(`pytest tests/ -q`) — 1216 passed, zero regressão.

**Pendente**: usuário validar em jogo — torre/minion do próprio time
nunca mais somem ao entrar na bush; torre/minion adversário continua
escondendo normalmente atrás de bush/parede real; um aliado (minion ou
player) vendo um inimigo na bush continua revelando ele pro time
inteiro, mesmo pra quem não se moveu (mecanismo pré-existente,
confirmado ainda funcionando).

## 9. Visão de time vira UNIÃO de fontes independentes, estilo LoL (16/08/2026)

Usuário reportou de novo (mensagem que não tinha chegado no histórico
na 1ª vez): minion/torre ainda não compartilhavam a visão de um
inimigo pro RESTO do time, e não se sabia se player aliado tinha o
mesmo problema. Pedido explícito: comportamento idêntico ao LoL — o
que QUALQUER unidade do time vê (player, minion ou torre), o time
inteiro vê. Investigado ANTES de codar, com plano formal aprovado
(`EnterPlanMode`/`ExitPlanMode`).

**Causa raiz**: `_can_see`/`_has_tile_los` (`server/session.py`,
`server/tile_los_processor.py`) sempre calculavam LOS a partir da
posição do PRÓPRIO viewer perguntando — nunca da posição de um
aliado. A única exceção existente (`_ally_bush_blobs`, §57) era mais
estreita do que parecia: só isentava uma célula de bush que um aliado
ocupava FISICAMENTE, sem checar raio de visão nenhum (um aliado no
outro canto do mapa, dentro de qualquer bush, já isentava aquela
célula pra qualquer viewer do time) — e nunca cobria parede/pedra
(bloqueio sólido nunca teve exceção) nem o caso de um aliado ver o
alvo por um ÂNGULO diferente do viewer (torre vendo por trás de uma
parede que bloqueia exatamente o raio do player). A parte de
PROXIMIDADE já estava certa — `SessionManager.
_compute_ally_vision_centers()` já calculava, 1x por tick, posição+
raio de cada aliado (player/torre com raio próprio/minion) e já
estendia o raio de "candidato a descoberta" — só a checagem de LINHA
DE VISÃO nunca usava essa lista.

**Pesquisa de referência** (wiki oficial de LoL + 1v9.gg, antes de
desenhar): confirmado — "you and all your allied champions and
structures provide vision for your whole team — if you can see it, so
can your teammates"; minion e turret são fontes de visão
independentes, com raio próprio. Modelo é UNIÃO de fontes
independentes (posição+raio+LOS cada uma), não "raio estendido mas LOS
ainda centralizado em quem pergunta" (o que o código fazia).

**Fix**: `_has_tile_los` ganhou parâmetro opcional `ally_centers`
(mesmo formato — lista de `(tx, ty, raio)` — que `_compute_ally_vision_
centers()` já produzia). Fluxo: tenta primeiro o raycast a partir da
posição do PRÓPRIO viewer (mais barato, cobre a maioria dos casos); se
falhar e `ally_centers` foi passado, tenta de novo a partir de CADA
aliado — alvo dentro do raio PRÓPRIO daquele aliado e raycast livre a
partir dele já basta (sólido continua bloqueando de forma absoluta em
QUALQUER raycast, próprio ou de aliado — regra nunca mudou). A
exceção estreita antiga (`_ally_bush_blobs`) foi REMOVIDA — fica
totalmente subsumida pelo passo novo, com o ganho extra de agora
respeitar raio de visão (a versão antiga nunca respeitava). `_can_see`
repassa o parâmetro; os 7 pontos de chamada dentro de `SessionManager`
passam a enviar `ally_centers=self._ally_vision_centers.get(<viewer_
eid>, [])` — reaproveitando a MESMA lista já calculada 1x por tick,
zero estrutura nova, zero recálculo extra por chamada.

**Testes**: novo `TestTeamVisionUnion` em `test_tile_los_visibility.py`
(4 testes — torre/minion/player aliado com LOS própria revela o alvo
pro time mesmo com parede bloqueando o raio do viewer; aliado fora do
próprio raio de visão NÃO revela); `TestBushAllyVisionSharing`
adaptado pra passar `ally_centers` explicitamente + teste novo de raio
insuficiente. `TestAllyTriggeredPlayerDiscovery`
(`test_team_visibility_sweep.py`) expôs 2 gaps de SETUP que a mecânica
antiga mascarava (não dependia de `_mob_eids` nem de `ally_centers`
pré-calculado): minion de teste nunca estava em `ws._mob_eids`
(`_compute_ally_vision_centers` só considera mob/torre/minion que
estão lá) e o teste chama `_build_update_for_session` direto, sem
passar por `_dispatch_tick_deltas` (que recalcula `_ally_vision_
centers` 1x por tick) — corrigido simulando esse passo manualmente.
Confirmado por revert manual: comentar o loop de aliados em
`_has_tile_los` derruba exatamente os 5 testes que dependem dele
(4 de `TestTeamVisionUnion` + 1 de `TestBushAllyVisionSharing`), mais
nenhum outro. Suíte completa (`pytest tests/ -q`) — 1220 passed, 1
falha isolada (`test_perf_log_improvements.py`, flakiness de seed já
documentada, não relacionada — confirmado rodando o arquivo sozinho,
passou).

**Fora de escopo, deliberado**: fog visual do cliente
(`ui/systems.py::FogSystem`) não mudou — já revela TERRENO
geometricamente via `_ally_vision_centers`, sem granularidade de LOS
por célula; só a visibilidade de ENTIDADE (`_can_see`) foi corrigida.

**Pendente**: usuário validar em jogo — torre/minion/player aliado
vendo um inimigo atrás de parede ou bush revela ele pro time inteiro,
mesmo pra quem está longe/sem LOS própria.

## 10. Bot 100% servidor pra preencher a fila 2v2 da BG (16/08/2026, feature nova)

Usuário quer testar a BG sem precisar de 4 humanos: solo na fila 2v2
cai com 1 parceiro bot contra 2 bots; grupo de 2 (já time completo)
cai contra 2 bots; 2 solos que entram na fila SEPARADAMENTE (sem grupo
entre eles) viram ADVERSÁRIOS um do outro, cada um com seu próprio
parceiro bot — nunca 2 humanos estranhos no MESMO time.

**Pesquisa de referência** (antes de codar, ver `PROBLEMAS_ARQUITETURA.md`
histórico da conversa): wiki oficial de LoL + API de bot scripting da
Valve pra Dota 2 (bem documentada) — modelo de IA de utilidade,
"modes"/comportamentos com pontuação de desejo por decisão (perseguir,
empurrar lane...), maior prioridade vence. Indústria de QA de jogos
(EPAM e outros) confirma o padrão geral pra bot de TESTE: processo
headless falando o protocolo real do servidor — descartado em favor de
bot 100% server-side por decisão explícita do usuário (evita gerenciar
processo/conexão externa; mais robusto rodando dentro do próprio loop
do servidor).

**Decisão de arquitetura**: bot é uma entidade normal, criada por
`WorldServer.spawn_player` (MESMO caminho de qualquer login real, sem
fábrica paralela) — nunca um processo cliente separado. `session_id`
sintético, registrado nas mesmas tabelas que um login real
(`_player_eids`/`_player_eid_to_sid`) + uma `Session` FALSA em
`SessionManager` (`ws` nulo, `send()` descarta em silêncio) — sem essa
Session falsa, o bot ficaria invisível pros outros players: a
varredura que descobre OUTROS PLAYERS
(`server/session.py::_build_update_for_session`) só itera sessões
REAIS (`self._sessions.values()`), nunca `_player_eids` direto (achado
investigando, não óbvio de antemão). Ação do bot (mover, atacar, usar
skill) chama as MESMAS funções internas que os handlers de rede já
chamam por trás (`move_player`/`set_player_target`/`queue_skill`,
`server/world_server.py`) — zero lógica de combate/cast duplicada.

**Política de pareamento** (só modo 2v2 — 3v3/5v5 continuam com o
bin-packing antigo, `_bg_try_pack`, inalterado): `_bg_try_pack_with_bots`
nova — cada TIME vem de NO MÁXIMO 1 token real (nunca 2 solos
estranhos no mesmo time). ≥2 tokens reais disponíveis → fecha na hora,
1 por lado, cada lado incompleto ganha bot depois. Só 1 token
disponível → espera `BG_BOT_BACKFILL_WAIT_S` (20s) por um adversário
real antes de fechar sozinho com o time inteiro adversário virando bot
— MESMA regra pra solo ou grupo já completo (uniforme, decisão
confirmada com o usuário depois de uma pergunta de esclarecimento).
Token de fila ganhou timestamp de entrada (`("solo"|"party", val,
join_ts)`) só pra esse timeout — `_bg_members_for_token`/
`request_bg_queue_leave` ajustados pra ignorar o campo extra.

**IA do bot** (`server/bot_ai_system.py::BotAISystem`, throttle ~3x/s,
`BotControlled.decision_timer`): por prioridade — skill ofensiva
off-cooldown em alcance (1ª do hotbar, catálogo único `SKILL_CATALOG`,
sem hardcode) > auto-attack em alcance > perseguir alvo visível fora de
alcance (pathfinding reaproveitado de `WorldServer.
_get_pathfinding_for_map_file`, MESMO que `_tick_minion_waves` já usa,
nenhum A* duplicado) > empurrar a própria lane (waypoints de
`WorldServer._minion_lanes`, mesmo dado que os minions já seguem). Sem
"recuar" na v1 (decisão do usuário) — bot morre, respawna pelo mesmo
timer automático de qualquer player da BG. v1 só mira PLAYERS inimigos
(ignora torre/minion), 1 skill fixa (`golpe_poderoso`), classe
Guerreiro/Cavaleiro (decisão do usuário — melee, sem mira de
direção/projétil, IA mais simples), nível 2 (mesmo nível de
minion_lanes/torre no mapa).

**Cleanup**: centralizado dentro de `request_bg_leave` (não em cada
chamador) — checa `BotControlled` no fim e despawna por completo
(`WorldServer.despawn_player` + remove a Session falsa de
`SessionManager`) se for bot. Cobre os 3 caminhos de saída (timeout de
resultado, time incompleto descartado, saída manual) de uma vez, sem
duplicar a checagem.

**Testes**: `tests/test_bg_bot.py` (novo, 13 testes) — política de
pareamento (`_bg_try_pack_with_bots`: solo espera timeout, 2 solos
fecham imediato como adversários, grupo também espera o mesmo
timeout); `_spawn_bot_player`/`_despawn_bot_player` (componentes de
combate esperados, Session falsa registrada/removida); `_tick_bg_queue`
ponta-a-ponta (bot se autoaceita, humano precisa mandar
BG_MATCH_ACCEPT); `BotAISystem` (engaja com skill em alcance, persegue
fora de alcance, segue lane sem inimigo, não age morto). Suíte
existente (`tests/test_bg_queue.py`) tinha 18 testes presos ao
comportamento ANTIGO do 2v2 (4 solos combinando pra fechar 1 time real,
premissa que deixou de valer só pro 2v2) — corrigidos: testes de
formato de token (3-tupla com timestamp) ajustados; testes de ciclo de
vida genérico (aceite/countdown/portão/Nexus/saída) migrados pra usar 2
GRUPOS de 2 em vez de 4 solos independentes (resultado idêntico ao
antigo, sem bot nenhum envolvido); testes do algoritmo de pareamento
migrados pra 3v3/5v5 (modos que não mudaram). Revert manual confirma:
reverter "nunca 2 solos no mesmo time" derruba exatamente os 2 testes
que provam essa regra, mais nenhum outro. Suíte completa
(`pytest tests/ -q`) — 1234 passed, zero regressão.

**Pendente**: usuário validar em jogo (servidor real, não só testes
automatizados) — bot ataca/usa skill/persegue/empurra lane de forma
crível; os 3 cenários de pareamento (solo+bot vs 2 bots; grupo vs 2
bots; 2 solos viram adversários) confirmados corretos pelo usuário na
conversa, mas ainda não jogados de verdade.

## 11. Gold/XP de minion + kill credit/assist de player, estilo LoL (16/08/2026)

Usuário jogou uma partida real da BG e achou 3 problemas: (1) gold de
matar minion dado a player mesmo sem golpe final; (2) minion do
PRÓPRIO time do player dando XP pra ele; (3) desconfiança de que o
KDA de kill de player estava certo — pediu estilo LoL (kill vai pro
último PLAYER inimigo que bateu, mesmo que o golpe final tenha sido
de minion/torre; quem também bateu recentemente ganha assistência).

**Achado investigando, mais sério que o relatado**: `players_killed`
era código morto em partida real. Existem 3 lugares que detectam
morte de player, mas só 1 (`ServerDeathHandler`, genérico) creditava
kill — e os outros 2 (`_process_player_attacks`'s varredura de HP a
cada tick, `_process_pvp_attack`) SEMPRE processavam a morte e
apagavam o `PendingDeath` (única fonte de "quem matou") ANTES do
`ServerDeathHandler` rodar, no MESMO tick. Resultado: em partida real,
NENHUMA morte de player chegava no código de crédito — kills sempre
zerado no placar, e `INSTANCE_PLAYER_KILL_GOLD` nunca pago (mesma
causa raiz). Nenhum conceito de "assistência" existia.

**Pesquisa (LoL, pedido do usuário)**: kill vai pro último CAMPEÃO
inimigo que causou dano (nunca minion/torre); assist = outro campeão
inimigo que bateu nos últimos 10s (janela renova a cada hit novo); sem
NENHUM campeão inimigo tendo batido nos últimos 15s, ninguém ganha
nada ("regra de execução"). Gold de minion é last-hit ESTRITO (sem
fallback); gold de torre é compartilhado com o time presente
(objetivo, não farm — usuário confirmou manter só torre com o
fallback).

**Fix #1 — gold de minion**: `server/server_death_handler.py`, bloco
de gold — fallback "primeiro player que bateu" (desenhado originalmente
pra torre) restrito a só rodar quando `_tower_dh is not None`. Minion/
jungle-normal deixam de herdar esse fallback — sem golpe final de
player, ninguém ganha gold (a intenção já estava documentada num
comentário do jungle-normal, só a implementação preenchia errado pro
minion).

**Fix #2 — XP de minion**: mesmo arquivo, bloco de proximidade — filtra
`_nearby` pela Faction do PRÓPRIO minion morto (nunca do killer, que
pode não existir/ser resolvível) — só time INIMIGO ganha XP. Mesmo
padrão que o branch de jungle BOSS já usava, só não tinha sido copiado
pro branch de minion/jungle-normal.

**Fix #3 — kill credit + assist, unificado**: novo `WorldServer.
_player_damage_log: dict[vítima_eid, dict[atacante_eid, (timestamp,
seq)]]` — só player→player, populado dentro de `_log_mob_damage_hit`
(hook universal já usado por QUALQUER dano, `apply_damage_core`).
`seq` é um contador monotônico próprio, desempate pra 2 hits no MESMO
tick — achado TESTANDO: `time.time()` sozinho não tem resolução
garantida pra separar 2 chamadas a microssegundos de distância, e
`max()` desempatava pela ordem de inserção do dict (sempre o
PRIMEIRO a bater), não por quem bateu de verdade por último — bug
real na primeira versão da própria correção, pego pela suíte rodada
várias vezes seguidas antes de fechar.

`RespawnMixin._handle_player_death` ganhou `killer_eid` (default -1) e
virou o ÚNICO lugar que credita kill/assist — os 3 pontos que detectam
morte de player (`server/combat_processor.py` ×2,
`server/server_death_handler.py`) passaram a REPASSAR o killer que já
tinham disponível, em vez de descartar ou creditar cada um por conta
própria. Kill vai pro contribuinte mais recente dentro da janela de
10s (`PLAYER_KILL_ASSIST_WINDOW_S`, `shared/constants.py`); qualquer
outro contribuinte na mesma janela vira assist. Sem ninguém na janela
→ ninguém ganha nada (regra de execução cai de graça do desenho, sem
caso especial). Novo campo `CharStatsTracker.assists`.

**Rebalance de economia** (usuário aproveitou a correção pra pedir,
decisão explícita): minion melee 25→20, minion ranged 30→15, minion
ranged raro 80→35 (`content/minion_definitions.py`); as 2 torres
viram 80 fixo cada, eram uma faixa 18-40 (`content/
tower_definitions.py`); kill de player 50→300
(`INSTANCE_PLAYER_KILL_GOLD`); assist = metade do kill, 150
(`INSTANCE_PLAYER_ASSIST_GOLD`, novo).

**KDA "assists"** propagado nos 2 sistemas que existem (fila real E
`/testbg` de debug — deixar um de fora reproduziria a MESMA classe de
bug do §61, 2 caminhos paralelos divergindo): `stat_snapshots`/
`_tick_bg_kda_hud`/`notify_bg_queue_nexus_destroyed`
(`server/bg_queue_processor.py`) e o equivalente em
`server/debug_battleground.py`. Cliente ganhou coluna "A" no placar
final (`client/battleground_handlers.py`, modal alargado de 560 pra
620px) e "Assists: N" no HUD ao vivo (`client/hud_handlers.py`) —
sem isso o número existiria só no servidor, invisível pro usuário.

**Testes**: `tests/test_bg_kill_credit.py` (novo, 9 testes) — minion
morto por outro minion/torre não dá gold a quem bateu antes; minion
do próprio time não dá XP, minion inimigo dá normal; player golpeado
por outro player e finalizado por minion → o PLAYER ganha a kill,
não ninguém; segundo player que bateu ganha assist, não kill;
ninguém batendo (só minion/torre) → ninguém ganha nada; gold de
kill/assist concedido certo (300/150); hit fora da janela de 10s não
conta. Passa pelo caminho de combate REAL (`_process_pvp_attack`,
`_process_player_attacks`) — o teste antigo de `players_killed`
(`test_char_stats.py`) tinha esse defeito exato (chamava
`apply_damage_core`/`_death_handler.update()` direto, nunca exercitava
a corrida de verdade), dando falso positivo. Revert manual confirma:
cada bloco novo (fallback restrito, filtro de Faction, crédito de
kill/assist) derruba exatamente os testes que provam aquele ponto,
mais nenhum outro.

Suíte completa (`pytest tests/ -q`) — 1249 passed, zero regressão.

**Pendente**: usuário validar em jogo — minion sem golpe final de
player não dá mais gold; minion do próprio time não dá mais XP; kill
de player aparece certo no placar (mesmo com assistência de minion/
torre no golpe final), incluindo quem ajudou; números de gold batem
com os novos valores.

## 12. Camps de jungle da BG populados a partir do concept art + level dinâmico (16/08/2026, feature nova)

Usuário forneceu `maps/concepts/moba_bg_entities.png` (mapa 100×100,
1 pixel = 1 tile — mesma dimensão do terreno real) marcando posição de
monstro de jungle (marrom) e boss (rosa); pediu pra AGRUPAR os pontos
próximos (camps de verdade) e, depois de alinhado o desenho com o
usuário, popular tudo com dados aleatórios/placeholder (usuário ajusta
depois) — mais um pedido extra: level do mob de jungle precisa subir
junto com o nível médio dos players, igual já acontece com minion.

**Agrupamento** (clustering por proximidade, raio 4px/tiles, sobre os
pixels marrons/rosa do PNG): 14 camps de mob normal (4 solitários —
1 mob mais forte cada; 8 trios; 2 quartetos — pacote de mobs mais
fracos) + 4 bosses. Todas as 40 posições conferidas contra
`moba_battleground_terrain.csv`/`_objects.csv` — grama, sem obstáculo,
nenhum ponto precisou de ajuste.

**Dados** (`content/jungle_definitions.py`, tudo marcado como
PLACEHOLDER no comentário — não é balanceamento final): 4 tipos novos
de mob normal (`jungle_javali_alfa` — solitário, mais forte;
`jungle_aranha_venenosa`/`jungle_lobo_alfa` (já existia)/`jungle_harpia`
— trios; `jungle_raptor` — quarteto, mais fraco individualmente) + 3
bosses novos (`jungle_boss_guardiao_de_pedra`, `jungle_boss_dragao_jovem`,
`jungle_boss_horror_das_profundezas`, cada um com `buff_cycle` próprio
usando atributos diferentes do `jungle_boss_ancestral` já existente —
armor/stamina, spell_power/crit_rating, attack_power/crit_rating).
Posicionamento em `maps/moba_battleground_entities.json::"jungle_mobs"/
"jungle_bosses"` (chaves que faltavam — schema já existia em
`engine/map_loader.py`, só nunca populado pra este mapa), `faction:
"monstros_hostis"` em todos (confirmado com o usuário — hostil aos 2
times por igual, padrão MOBA).

**Level dinâmico** (mudança de sistema, não só dado): novo
`WorldServer._compute_match_avg_level(map_file, default_level)` —
irmão de `_compute_team_avg_level` (já usado por minion), mas SEM
filtro de Faction (jungle é neutra, não pertence a nenhum time, então
escala pela média de TODOS os players da instância, não só um time).
`_create_jungle_camps` (chamada tanto na carga inicial do mapa quanto
em CADA respawn individual via `_tick_jungle_camp_respawns`) passa a
computar o level por essa função a cada chamada, usando o valor do
JSON só como fallback (sem player ainda na instância) — mesmo
princípio de "recalcula a cada oportunidade de spawn" que minion já
usa, só que pra jungle a oportunidade é o respawn de CADA mob morto
(não existe wave fixa).

**Testes**: `tests/test_jungle_camps.py::TestJungleCampLevelScaling`
(novo, 2 casos) — level do respawn bate com a média de TODOS os
players (2 times diferentes, sem filtro) e cai no fallback estático
sem ninguém na instância. Revert-to-confirm: sem o fix, o teste de
média falha exatamente como esperado (nível fica travado no fallback
antigo). Suíte completa (`pytest tests/ -q`) — 1252 passed (1250 + os
2 testes novos), zero regressão.

**Pendente**: usuário rebalancear os números placeholder (todos
marcados como tal no código) e testar em jogo — camps aparecem nas
posições certas, level sobe conforme os players sobem de nível.

## 13. Jungle sem drop físico + puxão de grupo estilo WoW/LoL (16/08/2026, feature nova, planejada com o usuário via EnterPlanMode)

Usuário testou o §65 e achou 2 problemas: (1) mob de jungle dropava
item E gold físico no corpse, quando deveria ir tudo automático pra
carteira com FLT (mesmo padrão de minion/kill de player); (2) jungle é
hostil e aggra só de o player chegar perto — pediu que só aggre se for
atacado, e que (pedido GERAL, confirmado via `AskUserQuestion`, não só
pra jungle) quando um mob entra em combate, outro mob da MESMA facção
perto TAMBÉM entra, mesmo que normalmente nunca aggraria sozinho por
proximidade — estilo WoW/LoL. Decisões fechadas com o usuário antes de
codar: puxão de **1 salto só** (sem corrente pelo camp inteiro), raio
de **3 tiles** (Chebyshev), mecanismo genérico desde já (não só pra
BG), mas a MUDANÇA de comportamento (deixar de aggrar por proximidade)
por enquanto só pra jungle — mob hostil clássico do mundo aberto
continua aggrando por proximidade normalmente.

**Fix #1 — drop físico**: `server/server_death_handler.py`, bloco
`if/elif/else` que decide `loot_items`/`coins` (~linha 545) não tinha
`elif _jungle_dh is not None:` — jungle caía no `else` genérico
(pensado pra `MOB_TABLE` comum), rolando item/coin FÍSICO no corpse
ALÉM do gold de instância já concedido automaticamente por
`grant_instance_gold` (mesma função que já dá o FLT "+Ng" dourado que
minion/kill de player usam). Novo branch, espelhando EXATAMENTE o de
`_minion_dh`: `loot_items = []; coins = 0`.

**Fix #2a — jungle para de aggrar por proximidade**: o mecanismo já
existia pronto — `EnemyAISystem` (bloco "Detecção inicial",
`engine/world_systems.py`) só aggra por proximidade em tier "hostil"
(`is_hostile()`); tier "neutro" só aggra por dano
(`aggroed_by_damage`), sem nenhum código novo pra isso. Jungle usava a
facção COMPARTILHADA `monstros_hostis` (mesma de todo mob hostil
clássico do mundo aberto) — não dava pra mudar o tier desse par sem
quebrar TODO mob hostil do jogo. Nova facção própria
`content/faction_data.py::"monstros_jungle"`, tier "neutro" vs
`arena_time_a`/`arena_time_b` (mesmo padrão de `"civis"` no mesmo
arquivo) — `can_engage()` continua liberando dano ("neutro" só bloqueia
"amigavel"). `_create_jungle_camps`/`map_loader.py` (defaults) e as 40
entradas de `moba_battleground_entities.json` migradas pra essa
facção. Efeito colateral avisado ao usuário: jungle deixa de ser
`is_hostile()`, então `TowerSystem`/`MinionSystem` (que só miram
`is_hostile()`) param de considerar jungle alvo válido — comportamento
correto de MOBA (torre/minion não brigam com monstro neutro).

**Fix #2b — puxão de grupo (novo mecanismo genérico)**: achado
investigando — a transição IDLE→AGGRO_DELAY por DANO estava
duplicada quase idêntica em 4 lugares (`world_systems.py::CombatSystem.
deal_damage`, `core_systems.py::apply_magic_damage_shared`,
`spell_completion_processor.py` ×2 — 1 delas pulava direto pra
"CHASING" sem NENHUM delay, inconsistência não-intencional corrigida
de graça ao centralizar). Novo ponto único,
`engine/core_systems.py::trigger_mob_aggro()` — transiciona o mob pra
AGGRO_DELAY E (via `_pull_nearby_packmates`, `_propagate=True` por
padrão) varre mobs da MESMA Faction, MESMO mapa, ainda IDLE, dentro de
`PACK_AGGRO_RADIUS_TILES=3`, puxando cada um pra AGGRO_DELAY contra o
MESMO alvo — com `_propagate=False` no puxado, garantindo só 1 salto
(sem corrente). Sem checagem de linha de visão de propósito (módulo
compartilhado/headless, sem acesso confiável ao tilemap certo em
contexto multi-mapa sem reimplementar o padrão de
`register_map_services_for` — over-engineering pro caso comum de
grupo já nascer próximo). Os 4 call sites duplicados passaram a
chamar essa função em vez de setar os campos direto; cada um mantém
seu próprio som/log (não dava pra unificar — 2 deles enfileiram
`_pending_sound_events` por serem assíncronos, os outros tocam som
direto).

**3º call site achado DURANTE a implementação (fora do plano
original)**: testando, um mob que nasce/fica ADJACENTE ao alvo ataca
DIRETO de IDLE (`in_attack_range` com `state != IDLE or is_hostile()`,
comportamento pré-existente) — esse caminho tem um `continue` ANTES do
bloco "Detecção inicial", então NUNCA passava por `trigger_mob_aggro`,
e o puxão de grupo nunca disparava pra um mob que já nasce adjacente
(exatamente o cenário de um trio de jungle bem agrupado). Corrigido
com `_was_idle_this_tick` (capturado no topo do loop de
`EnemyAISystem.update()`, antes de qualquer transição de estado do
tick) + chamada direta a `_pull_nearby_packmates` no ponto onde esse
atalho vira "ATTACKING".

**Testes**: `tests/test_jungle_camps.py` — novo caso em
`TestJungleMobNormalReward`/`TestJungleBossReward` confirmando corpse
sem item/coin mas carteira recebendo gold (revert-to-confirm: sem o
fix, `coins` do corpse fica > 0). `TestJungleMobHostility` reescrita —
jungle não é mais `is_hostile()` mas continua `can_engage()`-ável, e
teste fim-a-fim via `EnemyAISystem.update()` confirmando que fica IDLE
mesmo com player adjacente. Novo `tests/test_pack_aggro.py` (5 casos)
— puxão por dano dentro do raio, vizinho fora do raio não é puxado,
facção diferente não é puxada, só 1 salto (sem corrente), e o MESMO
mecanismo funciona pra facção hostil clássica disparado por
proximidade (não hardcoded pra jungle/BG) — este último caso foi o que
pegou o 3º call site (mob nascia adjacente ao player no teste).
Revert-to-confirm em cada caso novo. Suíte completa
(`pytest tests/ -q`) — 1260 passed (1252 + 8 testes novos), zero
regressão.

**Pendente**: usuário confirmar em jogo — mob de jungle não dropa mais
nada físico (gold aparece automático com FLT); mob de jungle não aggra
mais só de chegar perto; atacar 1 mob de um trio/quarteto puxa os
outros do grupo junto pra combate.

## 14. Bot AI v2 — lane sorteada, compra inicial, empurrar lane igual minion, dive gate (17/08/2026, feature nova, planejada com o usuário via EnterPlanMode)

Usuário pediu 4 melhorias pro bot da BG (v1: só mirava player, sempre
Guerreiro, sempre "a 1ª lane que bater", pathing próprio até o destino
final, ignorava torre por completo): (1) sortear lane (top/mid/bot) —
2 bots do mesmo time nunca na mesma; (2) comprar kit inicial por
classe antes de ir pra lane; (3) esperar 10s depois da PRÓPRIA wave
nascer e então seguir o MESMO caminho dos minions, brigando com
minion OU player que entrar no alcance; (4) só arriscar entrar no
alcance de torre inimiga se já tiver minion aliado lá dentro OU o
alvo estiver <30% HP (dive).

Pesquisado antes de desenhar (Dota 2/LoL — bot de dificuldade maior
"não se intimida com torre quando o alvo está baixo", compra roda
antes da lane) — confirma que os 4 pedidos batem com convenção real
de MOBA, não invenção.

Decisões fechadas com o usuário antes de codar (`AskUserQuestion`,
dentro do plano): kit de guerreiro/mago sem o Anel do Poder (185g→
115g cada, cabe nos 150g iniciais — arqueiro mantém os 3 itens,
150g exato); classe do bot passa a **variar** (sorteio guerreiro/
arqueiro/mago, senão o kit de arqueiro/mago nunca rodaria de
verdade); item comprado é **equipado** automaticamente (senão a
compra não muda nada em combate); dive gate = alcance de torre +
minion aliado já lá dentro, OU alvo <30% HP.

**1. Classe variável**: `_spawn_bot_player` (`server/
bg_queue_processor.py`) sorteia `class_id` (ganhou parâmetro
`class_id: str = ""` pra teste forçar determinístico). Skill inicial
não é mais hardcoded — lê `INSTANCE_SKILL_UNLOCK_ORDER[class_id][0]`
(`content/skill_config.py`), MESMA tabela que `enter_normalized_
progression` já usa como fonte única — pra arqueiro isso resolve pra
`"recarregar"` (não-ofensiva, tudo bem: luta de auto-attack à
distância mesmo). Nenhum ajuste em `_try_cast_offensive_skill`/
`_engage_target` (já genéricos por catálogo) nem no auto-attack (já
unificado desde §61, resolve melee/ranged pelo EQUIPADO).

**2. Lane sorteada**: novo `BotControlled.lane_id`. `_tick_bg_queue`
sorteia (`random.sample`, sem reposição) 1 lane por bot, POR TIME —
garante unicidade sem lógica extra. `_lane_goal` (v1) foi substituída
por `_push_lane` (item 3), que filtra por `lane_id` em vez de "a
primeira que bater".

**3. Compra + espera + seguir rota real**: novo
`BotAISystem.do_initial_shopping` (chamado 1x por
`request_bg_accept`, bot-only, logo após `enter_normalized_
progression` conceder gold) reaproveita os MESMOS métodos síncronos
que um player real usa via rede — `WorldServer.process_shop_buy`/
`equip_item_from_inventory` (`server/session.py::_handle_buy_request`/
`_handle_equip_item`), só chamados direto (bot não tem cliente pra
receber `BUY_RESULT`/`EQUIP_REJECTED`). Novo `BotControlled.
push_start_at` (timestamp absoluto, calculado 1x junto do sorteio de
lane, ancorado em `match["countdown_deadline"]` + `first_wave_delay_s`
da lane + 10s de folga) — `_push_lane` fica parado até esse horário.

Achado pesquisando: não existe cache de rota por lane em
`WorldServer` — `_tick_minion_waves` computava a rota LOCAL (variável
descartada ao terminar), só sobrevivendo dentro de `Minion.route` de
cada minion. Extraído pra `WorldServer._compute_lane_route_tail`
(MESMO cálculo, ponto único de verdade) — `_tick_minion_waves` passou
a CHAMAR esse método em vez do bloco inline, e `BotAISystem` usa o
MESMO método, garantindo que bot e minion NUNCA divirjam de caminho.
`BotControlled` ganhou `route`/`route_idx` (mesmo par que `Minion` já
usa) — consumidos tile a tile no ritmo de decisão do bot (mesmo
princípio de `MinionSystem._advance_along_route`, adaptado pra
`BOT_DECISION_INTERVAL_S` em vez de tick).

`_find_nearest_enemy` ganhou uma 2ª passada sobre `Minion` (antes só
via player) — mesmo filtro de facção que o loop de player já usa.
Correção necessária fora do pedido literal: o scan (players E
minions) ganhou limite de raio (`BOT_LANE_AWARENESS_RADIUS_TILES=8`)
— sem isso, com ~24 minions no mapa ao mesmo tempo (várias lanes), o
bot seria puxado pra longe da PRÓPRIA lane atrás do inimigo mais
próximo em QUALQUER lugar do mapa.

**4. Dive gate**: novos `WorldServer._enemy_towers_covering`
(Chebyshev ≤ `attack_range_tiles`, mesma métrica de
`TowerSystem._in_range_los`, só lê componentes) e `_allied_minion_
covers_tower` (minion da mesma Faction dentro do alcance da MESMA
torre = "ocupada"). `BotAISystem._dive_allowed` combina os dois +
checagem de HP% do alvo atual — usado tanto em `_push_lane` (rota da
lane) quanto em `_advance_toward` (perseguição de alvo), peek do
próximo tile ANTES de consumir do path/rota (não descarta cache num
dive negado, só espera a condição virar verdadeira).

**Testes**: `tests/test_bg_bot.py` — 14 casos novos (compra por
classe com item equipado, não só na mochila; sorteio de lane sem
repetir no mesmo time; bot não anda antes de `push_start_at`; rota do
bot bate exatamente com `_compute_lane_route_tail` da mesma lane;
ataca minion em alcance e persegue fora dele; dive gate bloqueado sem
minion aliado/alvo baixo, liberado com qualquer um dos dois). Testes
antigos ajustados: skill esperada agora depende da classe sorteada
(fixado `class_id="guerreiro"` nos testes que precisam de
determinismo); raio de scan agora limitado (ajustada distância de 1
teste de perseguição). Revert-to-confirm em cada teste novo (dive
gate, timing de push, ataque a minion) — todos falham exatamente como
esperado sem o fix correspondente. Suíte completa (`pytest tests/
-q`) — 1276 passed (1262 + 14 testes novos), zero regressão.

**Pendente**: usuário confirmar em jogo — bot sorteia lane/classe
diferentes a cada partida; compra e equipa o kit certo (sem Anel do
Poder pra guerreiro/mago); espera os minions saírem antes de
empurrar, seguindo o mesmo caminho; briga com minion/player que
aparece; não morre de graça mergulhando em torre sozinho, mas arrisca
quando o alvo está baixo.

## 15. Loja de instância vende o catálogo inteiro, filtrado por classe, preço por raridade (25/08/2026, feature nova, planejada com o usuário via EnterPlanMode)

Estado anterior: `content/merchant_data.py::SHOPS["instance_shop"]
["stock"]` era 13 itens escolhidos à mão, preço também à mão, sem
relação matemática com `value`/`rarity` do catálogo (`content/
item_table.py`, 129 itens). Usuário pediu: (1) vender o catálogo
inteiro; (2) filtrar por classe — guerreiro só placa/espada/maça/
machado/escudo, arqueiro só couro/arco/aljava, mago só tecido/cajado/
varinha, anel/amuleto/consumível (poção/comida/água) geral pra
qualquer classe; (3) preço gradual por raridade.

Investigação (direta, sem Explore agent — escopo já conhecido de
sessão anterior): `engine/components.py::Item` não tem campo de
"classe permitida" — só `item_type`/`subtype`/`armor_class`. A regra
de EQUIPAR já existente (`is_weapon_allowed_for_class`/
`CLASS_ARMOR_ALLOWED`, `engine/stats_system.py:47-95`) é mais
permissiva que o tema descrito pelo usuário (ex: mago hoje PODE
equipar adaga e espada de 1 mão; guerreiro tecnicamente pode equipar
qualquer coisa como arma melee). `process_shop_buy` (`server/
world_server.py`) nunca validou classe na compra — só gold/estoque/
espaço no inventário; um guerreiro podia comprar um robe hoje, sem
nada impedindo.

4 perguntas fechadas com o usuário (`AskUserQuestion`, dentro do
plano): filtro é uma **lista curada por tema**, função NOVA, separada
da regra de equipar (que continua intacta, só pra decidir o que pode
ser EQUIPADO depois de comprado); subtypes não mencionados (Adaga,
Machado-de-guerra/Martelo, Cetro) seguem padrão sugerido — Adaga→
guerreiro+arqueiro, Machado/Martelo→guerreiro, Cetro→mago; preço
comum=25g; preço é FIXO por raridade (confirmado por pergunta direta
— "se um incomum custa 50, o raro custa 2x disso e o épico 3x disso"
= mesma coisa que preço fixo por raridade, só explicado como
multiplicador).

**1. Tabela de preço + filtro de classe** — `content/merchant_data.py`:
`INSTANCE_SHOP_RARITY_PRICE` (common=25, uncommon=50, rare=100,
epic=150, legendary=200, mythic=250 — as 2 últimas sem item no
catálogo hoje, só completando a tabela) e `instance_shop_class_allows
(item, class_id)`, ponto único de verdade chamado tanto pelo servidor
quanto pelo cliente. Consumível foge da fórmula de raridade — TODO
consumível do catálogo tem `rarity="common"`, então a fórmula
igualaria poção de vida (hoje 25g) e odre de água (hoje 5g) no mesmo
preço; mantém `value` individual do item (decisão tomada e explicada
no plano, sem objeção do usuário).

**2. Estoque gerado do catálogo inteiro** — `_build_instance_shop_
stock()` itera `content.item_table.ITEMS` (129 itens), monta a MESMA
forma de `stock` que `server/world_server.py::_build_item_caches` já
consumia genericamente (nenhuma mudança necessária lá — compra por
`item_id` continua funcionando pra qualquer item sem tocar essa
função). `INSTANCE_SHOP_STOCK_BY_CLASS` pré-filtra + ordena (por
categoria → raridade → nome) 1x na carga do módulo, não por frame —
só usado pelo cliente (servidor chama `instance_shop_class_allows`
direto no momento da compra, não precisa da lista pronta).

**3. Enforcement no servidor** — `process_shop_buy` (`server/
world_server.py`) ganhou checagem de classe só pra `shop_id ==
"instance_shop"` (lojas "general"/"blacksmith" fora do escopo do
pedido, inalteradas): resolve o item via `entry["factory"]()`, chama
`instance_shop_class_allows(item, char.class_id)`, rejeita com
`{"success": False, "reason": "class"}` (mesmo formato da rejeição de
equipar por classe já existente). Sem isso o filtro seria só
cosmético — um cliente modificado ainda compraria qualquer coisa.

**4. Cliente** — `ui/systems.py`: painel da loja (`render`) E handler
de clique (`handle_events`) precisaram da MESMA troca — achado
importante durante a implementação: os dois liam `stock` de fontes
INDEPENDENTES (`SHOPS[...]["stock"]` cru em cada um, sem
compartilhar variável), então trocar só o `render` pra usar a lista
filtrada/ordenada e esquecer o `handle_events` teria feito clique
comprar o item ERRADO (índice N da lista filtrada mapeando pro índice
N da lista completa, listas com conteúdo/ordem diferentes) — mesma
classe de bug que a regra "atualização coesa" do CLAUDE.md já cobre,
só que dentro do mesmo arquivo/função em vez de entre sistemas.
Corrigido nos 2 pontos.

**5. Efeito colateral no bot** — `BOT_STARTER_KIT_BY_CLASS` (`server/
bot_ai_system.py`, de §69) quebrava com o preço/filtro novos:
arqueiro (arco+aljava+anel, antes 150g exato com preço à mão) virava
250g com `battleground_quiver` agora "rare"=100g — kit reduzido pra
`["hunter_bow", "battleground_quiver"]` (150g exato, sem o anel);
mago (`arcane_wand`+`iron_shield`) — escudo agora é EXCLUSIVO de
guerreiro no filtro temático, `iron_shield` seria rejeitado pelo
servidor — trocado por `silk_robe` (peito, tecido, uncommon, 50g),
kit vira 100g. Guerreiro sem mudança de itens (só ficou mais barato:
100g em vez de 105g).

**6. Limpeza** — `content/instance_shop.py` (lista curada antiga,
`INSTANCE_SHOP_ITEM_IDS`) removido — ficou obsoleto (docstring
afirmava "sem expor o catálogo completo", o que deixou de ser
verdade) e nada mais importava dele além de 1 teste, que foi
reescrito pra validar a estrutura nova (`content/merchant_data.py`)
em vez da lista antiga. `arquitetura/MAPA_PROJETO.md` atualizado
(referência ao arquivo removido).

**Decisões explicitadas pro usuário no plano, sem objeção**: preço
não trava por nível na COMPRA (só no EQUIPAR, que já existia — mesmo
comportamento da loja "general"); "todo o catálogo" tomado ao pé da
letra (129 itens, `Item` não tem flag de "exclusivo de loot/quest"
pra excluir algo automaticamente).

**Testes**: `tests/test_instance_progression.py` — estoque tem
exatamente `len(ITEMS)` entradas; preço bate com a fórmula de
raridade (exceto consumível = `value`); toda entrada de
`INSTANCE_SHOP_STOCK_BY_CLASS[cls]` passa em `instance_shop_class_
allows`; joia/consumível aparecem nas 3 classes. `tests/
test_bg_bot.py::TestBotInitialShopping` — 2 testes ajustados pro kit
novo de arqueiro/mago (nomes dos itens equipados mudaram). Suíte
completa (`pytest tests/ -q`, `py -3.10`) — 1277 passed, 1 failed
(teste de timing de performance, `test_perf_log_improvements.py`,
`sleep(0.15)` + assert de tick — falhou só na corrida cheia sob carga
do sistema, passou isolado; não relacionado a esta mudança).

**Pendente**: usuário abrir o comerciante da BG com cada classe em
jogo real e confirmar que a lista bate com o tema esperado, preços
corretos, e que comprar algo fora do tema (exploit/bug) é recusado
pelo servidor.

## 16. Editor de mapa (F10): pincel aleatório por grupo (26/08/2026)

Usuário pediu, no editor (F10, aba Objetos): um pincel por GRUPO (ex:
"árvores" de "TX Village Plant") — clicar no mapa com esse pincel
ativo planta um elemento aleatório do grupo, nunca repetindo o mesmo
da pincelada anterior. Pediu explicitamente seguir o procedimento
(investigar arquitetura, consultar referências, não criar função em
lugar errado) — planejado via EnterPlanMode + pesquisa externa.

**Investigação**: `self._sel_char` (`ui/god_mode.py`) é o pincel atual
— clicar numa célula da paleta seta essa string; pintar (clique OU
arrasto contínuo) sempre passa por `_apply(tx, ty)`, único
choke-point que lê o pincel e escreve no `Tilemap`.

**Pesquisa externa** (Tiled, Unity 2D Tilemap Extras): os dois têm
"pincel aleatório" via toggle que ativa modo aleatório pra um CONJUNTO
de tiles — confirma que ativar aleatoriedade por SELEÇÃO de um
conjunto (não célula a célula) é o padrão de mercado. Nenhum dos dois
evita repetir o último elemento — isso é uma melhoria específica
pedida pelo usuário (implementação simples: 1 variável "último id
colocado", sorteia excluindo ela).

**Fix**:
- `engine/tileset.py::group_tile_ids(group)` novo — ponto único de
  verdade pra "ids completos de um grupo" (`prefix + "_" + id_suffix`),
  reusado por `ui/god_mode.py::_render_content` (que antes recalculava
  o mesmo comprehension inline, ver §78) e pelo pincel novo.
- `GodModeEditor` ganhou `_brush_group_ids`/`_brush_last_id` (estado) e
  `_resolve_brush_char()` (sorteia excluindo o último, com fallback pro
  grupo inteiro quando só tem 1 tile — evita `random.choice([])`).
  `_apply()` passa a chamar `_resolve_brush_char()` em vez de ler
  `self._sel_char` direto — cobre clique único E cada passo de um
  arrasto contínuo (mesmo método, 1 só).
- Clicar no NOME do grupo (não um ícone separado, decisão do usuário)
  ativa o pincel — `_obj_group_label_rects` novo, testado ANTES dos
  cliques de célula única em `_handle_panel_click`. Clicar numa célula
  específica, ou trocar de aba, cancela o pincel aleatório (zera
  `_brush_group_ids`) — senão "vazaria" pra depois de escolher 1 tile
  fixo.
- Só famílias COM `"groups"` ganham a opção (hoje só "TX Village
  Plant") — mecanismo genérico, sem hardcode de nome; qualquer catálogo
  futuro migrado pra `"groups"` ganha de graça.

**Testes** (`tests/test_god_mode_random_brush.py`, novo arquivo, +
`tests/test_tileset_color.py::TestGroupTileIds`): pincel normal
devolve `_sel_char` direto; pincel aleatório nunca repete 2x seguidas
(rodado 50x); grupo de 1 tile só não trava; clicar no nome do grupo
ativa o pincel; clicar numa célula específica ou trocar de aba
cancela. Revert-to-confirm nos 3 pontos (filtro "nunca repetir",
cancelamento por clique de célula, ativação por clique no nome) —
cada um reproduziu o sintoma exato sem a correção. Suíte completa
(`pytest tests/ -q`, `py -3.10`): 1317 passed, zero regressão.

**Pendente**: usuário confirmar em jogo — F10, aba Objetos, clicar em
"🎲 Árvores", pintar várias no mapa, confirmar que nunca repete a
árvore anterior em sequência.

## 17. Migração de conteúdo: árvore/arbusto default + "TX Plant" antiga → "TX Village Plant" (26/08/2026)

Usuário pediu pra varrer todos os mapas e substituir (a) objetos da
família antiga "TX Plant" (prefixo `pl`, sufixos `tree*`/`bush*`/
`grassblade*`/`vomito` — diferente de "TX Village Plant", que usa
sufixos `t*`/`b*`/etc.) e (b) todo tile usando a árvore/arbusto
DEFAULT (chars `"t"`/`"b"`, `TREE_TILE`/`BUSH_TILE` — sem sprite real,
renderiza como retângulo de cor sólida) pelos elementos reais
catalogados em "TX Village Plant" (grupos `arvores`/`arbustos`/
`grama_fina`), sorteando aleatoriamente. Pediu pra seguir o
procedimento e perguntar se houvesse dúvida — teve 2 achados sérios
que exigiram parar e perguntar antes de tocar em qualquer arquivo.

**Achado 1 — metade dos arquivos de mapa está MORTA.** `map_1.csv`,
`map_main.csv`, `map_cave_east.csv`, `map_cave_west.csv`, `map_worm_
cave.csv` e `moba_battleground.csv` (sem sufixo) têm uma versão em 2
camadas ao lado (`..._terrain.csv`+`..._objects.csv`) — `engine/
map_loader.py::load_map_csv` SEMPRE prefere a versão em 2 camadas
quando ela existe (`if os.path.exists(t_path) and os.path.exists(
o_path): ... elif os.path.exists(filepath): ...`), então a versão sem
sufixo nunca é lida pelo jogo. Editar essas seria trabalho void — só
os `_objects.csv` (+ `arena_2v2.csv`/`arena_poco_negro.csv`, que não
têm par) importam de verdade.

**Achado 2 — a troca NÃO é só visual, muda colisão de verdade.**
Comparando `get_collision_offsets()` do char default contra os tiles
de "TX Village Plant":
- Arbustos da TX Village Plant são `is_solid=False` (andáveis) — o
  arbusto default `"b"` sempre bloqueava 1 tile. Já existiam ~41
  arbustos da TX Village Plant plantados manualmente no `map_1` antes
  desta tarefa, então o comportamento andável já era aceito no jogo.
- Árvores GRANDES (`pl_t14`/`pl_t15`, copa larga) têm colisão
  DESLOCADA 3-5 tiles da âncora (`get_collision_offsets` não inclui
  `(0,0)` pra elas) — plantar no lugar de uma árvore `"t"` default
  (bloqueia exatamente o próprio tile) mudaria QUAL tile fica sólido.
  Árvores pequenas (`t1`-`t13`) só deslocam 1 tile.

Perguntado ao usuário via AskUserQuestion: (a) aceitar arbusto andável
— SIM; (b) excluir `t14`/`t15` do sorteio pra evitar o deslocamento
grande — SIM (usa só `t1`-`t13`, 13 variantes).

**Escala real** (só nos arquivos que o jogo carrega, confirmado com
script de varredura antes de qualquer edição): `map_main_objects.csv`
— 8149 árvores + 245 arbustos (mapa de 3000×1500 tiles, confirmado que
são posições reais e espalhadas, não sujeira de arquivo);
`moba_battleground_objects.csv` — 2887 árvores; `map_1_objects.csv` —
já usava a TX Plant antiga em ~120 lugares (árvores/arbustos/grama-
fina). `map_cave_east/west_objects.csv`, `map_worm_cave_objects.csv`,
`arena_2v2.csv`, `arena_poco_negro.csv` — nada a trocar.

**Execução**: script 1x (não faz parte do código do jogo — migração de
dado, não mecanismo reusável) reusando `engine.tileset.group_tile_ids`
(mesma função do pincel aleatório do God Mode, §79) pra montar os 3
pools (arvores sem t14/t15, arbustos, grama_fina) e o MESMO algoritmo
"nunca repetir o último da mesma categoria" (`_resolve_brush_char`,
adaptado pra rodar em lote sobre o arquivo inteiro, 1 "último" por
pool, resetado por arquivo). 14066 substituições no total (2785 +
8394 + 2887).

**Verificação** (antes de considerar terminado): `load_map_csv` recarrega
os 3 arquivos migrados sem erro, dimensões (linhas×colunas) idênticas
antes/depois; TODO id resultante resolve em `OBJECT_MAPPING` (nenhuma
referência quebrada); `WorldServer()` sobe normalmente com os mapas
migrados; suíte completa (`pytest tests/ -q`, `py -3.10`) — 1317
passed, zero regressão. Conferência extra: nenhuma posição recebeu a
MESMA variante 2x seguidas dentro da mesma categoria (confirmado
programaticamente) — algumas sequências repetidas encontradas no
arquivo são conteúdo PRÉ-EXISTENTE não tocado por este script (ids
"TX Village Plant" já plantados manualmente antes desta tarefa, fora
do escopo do pedido).

**Pendente**: usuário abrir os mapas em jogo e conferir visualmente as
árvores/arbustos novos (principalmente `map_main`, a maior mudança).

## 18. Pincel inteligente de terreno (grama/terra/piso, transição automática) — God Mode (26/08/2026)

Usuário pediu, no editor (F10, aba Terreno, "TX Tileset Terrain"): pintar
o pincel de "Grama" num tile que hoje é "Terra" deve (a) virar aquele
tile em grama e (b) ajustar a BORDA de contato — só os vizinhos que
ainda são terra ganham um tile de transição (mancha/cruz), sem tocar
vizinho que já é outra coisa (ex: piso). Vice-versa pra "Terra" sobre
"Grama". Pintar sobre a MESMA categoria só sorteia entre variante pura
e com flor (80/20, confirmado com o usuário). "Piso sobre grama"/"Piso
sobre terra" têm tiles "completos" (4 pisos desenhados no mesmo tile —
descrição do que já está pronto na imagem, NÃO uma instrução de
montagem por 4 cantos, correção que o usuário fez em cima da minha 1ª
hipótese) que entram no sorteio automático, e tiles parciais/
customizáveis que ficam só pra seleção manual (nunca no sorteio).

**Pesquisa externa**: padrão "Terrain Brush"/autotile (Tiled, Godot, RPG
Maker) — cada célula examina até 8 vizinhos; o caso aqui é mais simples
que "Blob-47" completo (47 peças, 1 por combinação de vizinho): o PNG só
tem 2 peças de transição genéricas por par de terreno (mancha
arredondada + cruz), não um jogo completo de bordas direcionais — a
implementação reflete exatamente isso, não inventa peça que não existe.

**Catálogo da grade** (`SHEET_FAMILIES["TX Tileset Terrain"]["zones"]`,
`engine/tileset.py`) — lido da imagem de referência que o próprio
usuário anotou (`assets/tiles/TX Tileset Terrain_Explanation.png`,
caixas vermelhas rotuladas em português) depois que minhas 2 primeiras
tentativas de descrever a grade em texto não ficaram claras pro
usuário. Grid 14×16 de 32px — leitura VISUAL, não pixel-perfeita
(mesmo processo do `color_overrides` em §78: 1ª passada aproximada,
ajustada depois de testar pintura real no editor):
- `transicao_terra_grama` (cols 0-5, rows 0-2) / `transicao_grama_terra`
  (cols 0-5, rows 3-5)
- `grama_flor` (cols 6-9, rows 0-2) / `grama_pura` (cols 10-13, rows 0-2)
- `piso_sobre_terra` (cols 6-7, rows 3-5) / `piso_sobre_grama`
  (cols 0-3, rows 6-11)
- `terra_pura` (cols 0-3, rows 12-14) / `terra_flor` (cols 4-7, rows 13-15)
- Fora dessas 8 zonas = piso parcial/customizável, sem categoria — nunca
  entra em sorteio automático (nem do pincel inteligente, nem do pincel
  de piso), só seleção manual na grade célula-a-célula que já existia.

**Fix**:
- `engine/tileset.py::terrain_zone_of(family, tile_id)` novo — ponto
  único de verdade pra "em qual zona esse tile cai" (mesmo padrão de
  `group_tile_ids`, §79). `color_overrides` da família passou a ser
  DERIVADO das zonas onde elas têm cor própria (nunca diverge do
  catálogo novo), mantendo a divisão por faixa de linha (§78) como
  fallback pras células ainda sem zona.
- `ui/god_mode.py` ganhou `_terrain_smart_terrain`/`_terrain_last_id`
  (estado do pincel inteligente) + `_terrain_family()`/
  `_terrain_category_at()`/`_pick_terrain_variant()`/
  `_apply_smart_terrain()`. `_apply()` desvia pro modo especial quando
  `_terrain_smart_terrain` está setado (testado ANTES do fluxo normal
  de `_resolve_brush_char()`).
  - `_terrain_category_at` lê `terrain_visual[ty][tx]` (se houver
    override de sprite, resolve a zona dele) ou cai pro char plano de
    `terrain_matrix` ("G"/"f") — cobre tile nunca pintado com sprite
    de "TX Tileset Terrain" ainda.
  - Vizinhança: 8 direções (confirmado com o usuário, inclui
    diagonais). Só ajusta um vizinho se a categoria dele é EXATAMENTE
    a oposta da nova (nunca toca piso/água/parede/já-transicionado).
- "Piso sobre grama"/"Piso sobre terra" reusam o MESMO mecanismo de
  pincel de grupo aleatório já existente (`_brush_group_ids`, §79) —
  só o pool vem da zona `floor_full` em vez de um `"groups"` de
  `OBJECT_SHEET_FAMILIES`; zero código novo pra "sorteia sem repetir",
  só um jeito novo de montar o pool.

**Testes** (`tests/test_god_mode_terrain_brush.py`, novo arquivo):
`terrain_zone_of` resolve tile→categoria (pura, transição, fora de
zona, família sem zonas); pintar grama sobre terra escreve grama no
alvo + transição nos 8 vizinhos que eram terra; pintar grama sobre
grama só sorteia variante, não mexe em vizinho nenhum; vizinho que é
piso nunca vira transição; variante nunca repete a última (50
iterações); pool de piso nunca inclui tile de transição; ativação/
cancelamento via clique (label de terreno, célula específica, label de
piso, troca de aba). Revert-to-confirm no filtro de vizinho oposto
(`if True: continue` no lugar do check real) — reproduziu o sintoma
exato (vizinho nunca ganha transição) antes de restaurar. Suíte
completa (`pytest tests/ -q`, `py -3.10`).

**Correção (27/08/2026) — usuário testou em jogo e reportou "a função da
grama com os tiles vizinhos e a tela com os tiles vizinhos não deu
certo".** Investigando com pixels reais do PNG (`assets/tiles/TX
Tileset Terrain.png`, não a imagem anotada — script 1x lendo os
segmentos de linha vermelha da anotação e convertendo pra múltiplo de
32px), achei a causa raiz: **não era bug de código, era erro de modelo
mental sobre o asset.**

A região de "transição" não são 18 tiles de 32×32 independentes que dá
pra sortear um a um (era assim que a 1ª versão tratava, herdando o
mecanismo do pincel aleatório por grupo, §79). São **2 quebra-cabeças
de 3×3 peças interligadas** (uma "mancha" arredondada, uma "cruz") —
cada peça (canto/borda/centro) só faz sentido visual ao lado das peças
vizinhas certas do MESMO desenho (canto = grama cobrindo ~3/4 do tile
com corte diagonal; borda-meio = metade grama/metade terra; centro =
cor sólida). Sortear uma peça qualquer pra cada vizinho produzia um
mosaico sem nexo — exatamente o sintoma relatado.

Perguntei ao usuário como resolver (mostrando as peças recortadas):
escolheu **autotile posicional de verdade** (calcular a peça certa por
vizinhança real, nunca sortear) + **sorteia 1 estilo (mancha/cruz) ao
ativar o pincel, mantém fixo até desativar** (misturar os 2 dentro da
mesma área não conecta — cada um tem curvatura própria).

**Pesquisa**: [Autotiling – Interactive Guide (Red Blob Games)]
(https://www.redblobgames.com/articles/autotile/claude/) — confirma o
modelo (máscara de bits N/E/S/W por vizinho, técnica "Blob47"). Este
asset só tem 9 peças por quebra-cabeça (sem canto INTERNO/côncavo) —
variante reduzida "3×3 mínima": resolve só 9 das ~16 combinações de
vizinhança ortogonal (centro, 4 bordas de 1 lado só, 4 cantos de 2
lados adjacentes); qualquer outra combinação (2 lados opostos, 3+,
isolado) não tem peça no asset — escolha certa é não sobrescrever o
vizinho nesse caso, não forçar uma peça errada.

Também corrigi, pelo mesmo processo de pixel real, as fronteiras das
outras zonas que a 1ª leitura visual (aproximada, da imagem anotada)
errou por 1 linha/coluna: `grama_flor`/`grama_pura` ganham a linha 3
(rows 0-3, não 0-2); `piso_sobre_terra` desce pra rows 4-6 (a linha 3
daquela faixa já é grama, não piso); `piso_sobre_grama` encolhe de 4
pra 2 colunas (cols 0-1 — confirmado visualmente que cols 2-3 daquela
faixa já são peças parciais de OUTRO quebra-cabeça de piso, não tiles
completos); `terra_pura` ganha a linha 15; `terra_flor` desloca pra
cols 5-7 (col 4 é gap real sem categoria na imagem).

**Fix**:
- `engine/tileset.py`: zonas `transicao_terra_grama`/
  `transicao_grama_terra` trocam `"cells"` (lista solta) por `"shapes":
  {"mancha": (col,row), "cruz": (col,row)}` — só a ORIGEM (canto
  superior-esquerdo) de cada bloco 3×3. Novo dict `_BLOB3X3_PIECE_OFFSET`
  + função `resolve_blob3x3_piece(missing_sides: frozenset) -> tuple|None`
  (ponto único de verdade, mesmo padrão de `group_tile_ids`/
  `terrain_zone_of`) — mapeia "quais lados N/S/E/W do vizinho ainda não
  são a categoria nova" pro offset da peça certa. `terrain_zone_of`
  ganhou `_zone_cells()` auxiliar pra reconhecer célula dentro de um
  `"shapes"` (expande os 9 tiles de cada bloco), sem quebrar zonas com
  `"cells"` simples.
- `ui/god_mode.py`: `_apply_smart_terrain` trocou o sorteio aleatório
  do vizinho por `_missing_sides()` (lê os 4 vizinhos ortogonais do
  PRÓPRIO vizinho via `_terrain_category_at`, já existia) +
  `resolve_blob3x3_piece` — sem peça, não sobrescreve. Novo
  `_heal_if_fully_surrounded()`: vizinho que já tinha peça de borda de
  uma pincelada anterior, mas agora está 100% cercado pela categoria
  nova, vira tile normal (pura/flor) em vez de ficar com uma "cicatriz"
  de borda órfã. Novo estado `_terrain_smart_style` ("mancha"/"cruz"),
  sorteado 1x na ativação do pincel (clique no label), resetado nos
  MESMOS pontos que já resetavam `_terrain_smart_terrain` (troca de
  aba, clique em célula específica).

**Testes** (`tests/test_god_mode_terrain_brush.py`, reescrito): tile
isolado (sem vizinho já convertido) não transiciona nenhum vizinho
(bitmask sempre cai fora das 9 combinações válidas); vizinho pré-
montado com padrão válido (`{"E"}`) recebe a peça EXATA esperada
(cálculo, não só "alguma peça de transição"); vizinho totalmente
cercado cura pra pura/flor; piso nunca tocado (mantido); variante nunca
repete a última (mantido); `resolve_blob3x3_piece` testado direto nos 9
casos válidos + 4 combinações inválidas; ativação sorteia estilo,
cancelamento/troca de aba zera. Revert-to-confirm (`offset = (2,0)`
fixo no lugar do lookup real) reproduziu os 2 sintomas exatos — peça
errada forçada E vizinho isolado sendo tocado indevidamente — antes de
restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`).

**Correção 2 (27/08/2026) — usuário testou de novo e mandou 2 prints
com setas mostrando exatamente o comportamento esperado**: pincel de
"Terra" clicado sobre grama → o tile clicado vira terra sólida E os 8
vizinhos JÁ viram tiles de transição na hora (não só depois de várias
pinceladas acumuladas, como a Correção 1 exigia); arrastando o pincel
pra baixo, o tile anterior (já sólido) fica como está, só os vizinhos
NOVOS que ainda são grama ganham borda. Pediu pesquisa adicional em vez
de eu "chutar" de novo.

**Pesquisa**: confirmei a causa exata — a Correção 1 calculava a peça
de cada vizinho a partir dos PRÓPRIOS vizinhos-do-vizinho dele (bitmask
"real" de verdade), que é o algoritmo padrão de Blob/Wang tile citado
em [Autotiling – Interactive Guide (Red Blob Games)]
(https://www.redblobgames.com/articles/autotile/claude/) — mas isso
SÓ funciona quando o terreno já tem vizinhos acumulados; num clique
isolado, todo vizinho tem 3 lados "de fora" (sem peça no catálogo de 9
peças), então nada era desenhado — sintoma batendo com o 1º print do
usuário (nenhuma borda visível). Conferido também como o editor Tiled
documenta seu "Terrain Brush" ([Using Terrains — Tiled docs]
(https://doc.mapeditor.org/en/stable/manual/terrain/)): o comportamento
"padrão de mercado" também exige contexto acumulado (ou segurar Ctrl
pra pintar uma área maior de 1 vez) — ou seja, a Correção 1 estava
alinhada com como Tiled funciona, mas NÃO com o que o usuário
especificamente pediu/desenhou nos prints. Decisão: seguir a
especificação exata do usuário (mais simples que Tiled), não a
convenção genérica do editor de referência.

**Fix**: `_apply_smart_terrain` para de calcular a peça de um vizinho a
partir do ambiente real dele — agora trata o tile CLICADO como o centro
FIXO de um carimbo 3×3 novo a cada pincelada: a peça de cada 1 dos 8
vizinhos vem direto da DIREÇÃO (dx,dy) dele em relação ao clique
(`ui/god_mode.py::_direction_missing_sides`, decompõe diagonal nos 2
lados cardeais — ex: vizinho a NE tem lados {"N","E"} "de fora"), sem
inspecionar os vizinhos-do-vizinho. Isso garante o anel completo já na
1ª pincelada (resolve o padrão pra qualquer uma das 8 direções sempre,
diferente do cálculo "real" que podia cair fora das 9 combinações
válidas). `_missing_sides` (a versão "real", inspecionando o ambiente
de verdade) não foi removida — passou a servir SÓ pra
`_heal_if_fully_surrounded` (decidir se um vizinho já transicionado
ficou de fato 100% cercado, onde faz sentido olhar o ambiente real, não
a direção do clique).

**Testes**: reescrito o teste de "clique isolado" — antes esperava
NENHUM vizinho tocado, agora espera os 8 tocados com a peça EXATA da
direção certa (todas as 8 conferidas, não só 1). Novo teste de arrasto
(2 chamadas de `_apply_smart_terrain` em sequência, 1 tile abaixo da
outra) confirma que o tile do 1º clique não é alterado pelo 2º, e que
só o vizinho novo do 2º clique ganha borda — reproduz exatamente o 2º
print do usuário. Revert-to-confirm: voltar pro cálculo "real"
(`_missing_sides`) no lugar de `_direction_missing_sides` reproduziu os
2 sintomas exatos relatados (vizinho sem borda no clique isolado) antes
de restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`).

**Correção 3 (27/08/2026) — usuário testou arrasto em várias direções e
apontou, explicitamente: "você está fazendo o script pela metade...
alguns tiles que desenhei no tile anterior vão ser alterados, você não
está alterando... não está considerando todas as possibilidades" —
pediu pesquisa mais profunda ANTES de qualquer novo código (não
"concluir e já mexer").**

**Achado real, não arquitetural**: a Correção 2 travava a peça de cada
vizinho pra sempre na direção do clique que o tocou PRIMEIRO — nunca
recalculava depois. Investigando com pesquisa nova ([Autotiling —
Interactive Guide, Red Blob Games](https://www.redblobgames.com/articles/autotile/claude/),
[versão Gemini](https://www.redblobgames.com/articles/autotile/gemini/autotiling.html),
["The Power to Bring Light: Autotiles"](https://thepowertobringlight.blogspot.com/2016/11/autotiles-formats-and-algorithms.html))
achei **2 bugs de comparação reais** na Correção 1 (não um problema de
arquitetura) que, corrigidos, permitem usar o CÁLCULO REAL (ambiente de
verdade) como método principal — resolvendo o arrasto em qualquer
direção — com a direção-do-clique (Correção 2) virando só um reforço
(fallback) pros casos sem contexto real ainda:

1. **`_terrain_category_at` perdia a categoria real de um tile já
   decorado** — qualquer tile com peça de transição virava `None`,
   igual piso/água. Uma peça de transição é só cosmética; o
   `terrain_matrix` por baixo nunca muda sozinho. Sem esse fix, o
   sistema não tem como saber se um vizinho já decorado "ainda conta"
   como categoria antiga pra ser recalculado depois.
2. **Polaridade invertida em `_missing_sides`** — comparava `!= terrain`
   quando devia comparar `== terrain`. Confirmado recortando pixel a
   pixel as peças reais do PNG (`ter_1_0` = terra no NORTE/grama no
   SUL; `ter_1_2` = grama no norte/terra no sul — o oposto do que o
   código antigo produzia pra um vizinho ao norte de um clique). O bug
   fazia o cálculo real "resolver" (sem erro, sem cair no fallback) mas
   pra peça ERRADA (invertida) — por isso corrigir só esse detalhe já
   habilita o cálculo real como caminho principal.

**3º achado, só apareceu testando arrasto em RETA (não só L)**: com os
2 fixes acima, um vizinho que já tinha ganhado a peça CERTA por
fallback (ex: relação ORTOGONAL direta com o clique 1) podia ser
sobrescrito por uma peça PIOR quando um clique 3 mais adiante passava a
enxergar esse MESMO tile só como vizinho DIAGONAL dele (sem ganhar
contexto real novo pra valer) — o fallback não distinguia "relação
direta e confiável" de "relação diagonal fraca só de passagem". Fix:
fallback só sobrescreve um vizinho DIAGONAL se ele ainda não tem
NENHUMA decoração (bootstrap inicial); vizinho ORTOGONAL sempre aceita
(é sempre a relação mais direta possível).

**Desenho final** (`ui/god_mode.py::_apply_smart_terrain`): pra cada 1
dos 8 vizinhos, 1) cálculo REAL (`_missing_sides`, POLARIDADE
corrigida) — autoritativo sempre que resolve; vazio = cura pra tile
normal (com `terrain_matrix` atualizado de verdade, não só o sprite);
2) se real não resolve (sem peça no catálogo de 9 — tipicamente
diagonal sem contexto, ou padrão complexo tipo 2 lados opostos), cai no
fallback por direção do clique (`_direction_missing_sides`, já existia
da Correção 2) — MAS só se o vizinho for ortogonal ao clique atual OU
ainda não tiver decoração nenhuma. Isso garante os 8 vizinhos decorados
já no 1º clique (pedido original do usuário) E a auto-correção durante
arrasto em qualquer direção (pedido desta correção) — porque um
vizinho decorado continua sendo categoria "antiga" de verdade
(`terrain_matrix` nunca muda sozinho), então toda pincelada próxima
futura recalcula ele de novo com mais contexto.

**Testes**: reescrito `test_9_combinacoes_validas`→
`test_8_combinacoes_validas` (`frozenset()` saiu da tabela — vazio
agora significa "sem contexto real", tratado à parte, nunca uma "peça
de centro"); novo teste de auto-correção (clique isolado decora um
vizinho diagonal por fallback; clique seguinte que o torna vizinho
ORTOGONAL recalcula pra peça real diferente); novo teste de arrasto
reto em 3 cliques confirmando que uma peça boa (ortogonal) NÃO é
substituída por uma peça pior (diagonal fraca) — reproduz exatamente o
bug achado. Teste de cura passou a conferir `terrain_matrix` também,
não só o sprite. Revert-to-confirm em 2 pontos separados (polaridade de
`_missing_sides`; restrição diagonal-já-decorado do fallback) — cada um
reproduziu o sintoma exato antes de restaurar. Suíte completa
(`pytest tests/ -q`, `py -3.10`).

**Correção 4 (27/08/2026) — usuário mandou print de um caminho de terra
sobre grama com curvas: "as quinas não estão sendo alteradas".**
Simulei a mesma forma, renderizei com o PNG real e inspecionei pixel a
pixel — a princípio pareceu que os cantos estavam corretos (peça e
direção certas), mas o usuário insistiu que havia problema real e
pediu pesquisa de verdade em vez de mais suposição.

**Achado real, medido (não visual/estimado)**: escrevi um script que
mede, pra cada 1 das 9 células de cada quebra-cabeça (mancha E cruz,
nas 2 zonas), a fração de pixels "cor nova" em cada uma das 4 bordas da
célula. Resultado: nas 4 peças de CANTO (2 lados adjacentes) da zona
`transicao_grama_terra` (pintar TERRA sobre grama — exatamente o caso
do usuário), o lado que deveria mostrar a cor nova (terra) só chega a
~30-50% de presença — nunca perto de 100%. Na zona irmã
(`transicao_terra_grama`, pintar grama sobre terra), as mesmas 4 peças
de canto chegam a ~100%. Confirmado que **isso vale pros 2 estilos**
(mancha E cruz) — minha 1ª hipótese ("troca pra cruz resolve") também
teria falhado, a medição pegou isso antes de eu sugerir errado de novo.
Causa: não é bug de cálculo — é o próprio desenho do PNG (o "buraco de
terra" nessa direção é menor/mais arredondado que a "ilha de grama" da
outra). As peças de BORDA (1 lado só) são sempre fortes (~100%) nos 2
estilos, nas 2 zonas — só as de CANTO da zona `transicao_grama_terra`
são fracas.

**Fix**: `SHEET_FAMILIES["TX Tileset Terrain"]["zones"]
["transicao_grama_terra"]` ganhou `"weak_corners": True`. Novo
`GodModeEditor._resolve_piece_offset(missing, trans_zone)` — mesmo
lookup de `resolve_blob3x3_piece`, mas quando a zona é `weak_corners` E
o padrão é um canto (2 lados adjacentes), sorteia 1 dos 2 lados e usa a
peça de BORDA dele em vez do canto dedicado (fraco). Os 2 pontos que já
chamavam `resolve_blob3x3_piece` em `_apply_smart_terrain` (cálculo
real e fallback por direção) passam a chamar essa função no lugar —
zero duplicação, mesmo mecanismo cobre os 2 caminhos.

**Testes**: novo `TestWeakCornerFallback` — zona `weak_corners`
resolve pra 1 dos 2 offsets de borda válidos, nunca o canto dedicado;
zona sem a flag mantém o canto normal. Teste de auto-correção existente
trocado pra usar a zona SEM `weak_corners` (o sorteio novo tornaria a
asserção de desigualdade não-determinística naquela zona). Revert-to-
confirm (`if False and ...` no lugar do check da flag) reproduziu o
canto fraco original antes de restaurar. Suíte completa
(`pytest tests/ -q`, `py -3.10`).

**Pendente**: usuário confirmar em jogo, especialmente arrastando em
retas longas e em curvas/L em várias direções, que a borda se ajusta
corretamente conforme a pintura avança, e que os cantos de caminhos de
terra sobre grama agora aparecem visíveis de verdade (não mais "sem
transição nenhuma").

**Nota (27/08/2026) — pausado a pedido do usuário.** Depois desta
Correção 4, usuário testou em jogo de novo e desistiu ("não deu certo
e eu desisto. Vamos deixar isso em aberto pra editar a mão, quando eu
ver necessidade de melhorar esse sistema a gente volta nesse tópico").
Existe um rascunho de uma possível Correção 5 (recalcular
`_missing_sides` contra `opposite` em vez de `new_terrain`, mais um
fix em `_terrain_category_at` pra não perder a categoria de um tile já
decorado) anotado no arquivo de plano da sessão, mas **nunca foi
implementado** — não assumir que é o próximo passo óbvio se o tópico
for retomado; perguntar ao usuário o que ele quer primeiro.

## 19. Buff bar unificada (estilo WoW) — feature nova (31/08/2026)

Usuário pediu: além de debuff (que já aparece ao lado do nameplate),
uma barra de BUFF nova ao lado do minimapa, mostrando todo buff
temporário ativo no próprio jogador — talento, skill, proc de item,
buff de matar chefe de jungle — igual WoW. Seguido o fluxo completo do
projeto (EnterPlanMode, Explore + Plan agents, `AskUserQuestion` pras
decisões de design em aberto, plano escrito e aprovado ANTES de tocar
em código).

**Achado central da investigação (não suposição, código real)**:
`StatusEffects`/`apply_effect()` (`engine/core_systems.py`) já tinha
ícone + sincronização de rede (`AOI_UPDATE`/`deltas["effects"]`) + timer
— mas só era usado na prática por CC/DoT. Todo buff de VERDADE do jogo
(proc de item, self-buff de skill, buff de chefe de jungle) usava um
mecanismo TOTALMENTE separado — `CombatStats.timed_modifiers`/
`add_timed_modifier` — sem ícone, sem identidade estável, e **nunca
sincronizado pro cliente**. Proc de item especificamente tinha um bug de
dessincronia pré-existente, nunca reportado: o cliente re-rolava
localmente com RNG próprio (`engine/world_systems.py::_trigger_procs`),
podendo divergir do resultado real do servidor.

**Decisões confirmadas com o usuário** (via `AskUserQuestion`, todas as
3 recomendadas foram aceitas): (1) unificar via `StatusEffects` —
reaproveitar o pipeline de ícone+sync existente, não criar canal de rede
novo e paralelo; (2) buff bar só pra buffs TEMPORÁRIOS (talento
permanente/equipamento continuam invisíveis, igual hoje); (3) fallback
de quadrado colorido pra ícones sem PNG ainda, sem bloquear a
implementação por arte.

**Mecanismo**: `engine.core_systems.grant_visible_buff(world, eid,
effect_type, duration, mutate_fn)` — novo ponto único, ao lado de
`apply_effect`. Chama `apply_effect()` (registra `ActiveEffect`
sincronizado) E roda `mutate_fn` (a mutação de stat real, tipicamente
`add_timed_modifier`) — mesmo padrão de composição de
`recalculate_preserving_hp_pct` (cada wrapper cuida de 1 preocupação).
3 call sites reais passaram a rotear por aqui: `_roll_procs`
(`engine/core_systems.py`, proc de item), `_grant_jungle_boss_buff`
(`server/world_server.py`), self-buffs de skill (`_server_
cancao_inspiracao`/`_server_so_um_gole` em `server/
spell_completion_processor.py`, espelhados em `engine/skill_handlers.py`
pro caminho client/offline). 14 entradas novas em `content/
status_effects_data.py::EFFECT_DEFS` (8 procs de item, 2 self-buffs de
skill, 4 variantes de buff de chefe de jungle), cada uma com chave
ESTÁVEL e ÚNICA por fonte (nunca chave genérica — `StatusEffects.effects`
só guarda 1 instância por chave, e é normal 2+ procs de itens DIFERENTES
ativos ao mesmo tempo).

`engine/world_systems.py::_trigger_procs` (re-roll cosmético do cliente,
única fonte de dessincronia real) foi apagado — o cliente agora reage
ao buff de verdade sincronizado, mesmo `LOG.add(...)` que debuff já
dispara pra efeito novo (`client/remote_entity_handlers.py::
_sync_player_effects`). **Trade-off aceito e avisado ao usuário**: a
mensagem de log perde detalhe (não mostra mais nome do item/valor exato,
só o nome do buff) — decorrência direta de "sem canal de rede novo".

`ui/buff_bar.py` (novo módulo, mesma forma de `QuestSystem` —
`UIScaleMixin`, sem precisar ser `System`) lê `StatusEffects` do PRÓPRIO
jogador, filtra por `is_buff=True`, reaproveita `_build_effects_row()`
(`ui/systems.py`, sem modificar) pra montar a fileira de ícones, blitada
à ESQUERDA do minimapa (posição derivada de `UI.MINIMAP_*`, nunca
hardcode — `UI.BUFF_BAR_MARGIN_TOP`/`BUFF_BAR_GAP_TO_MINIMAP` novos em
`ui/ui_sizes.py`). Nameplate (`ui/systems.py::RenderSystem.render` +
os 2 pontos equivalentes em `client/remote_entity_handlers.py`, mob
remoto e player remoto) passa a EXCLUIR buff da própria lista de ícones
(`_filter_nameplate_effects()`, extraído pra ser testável isolado) — só
debuff continua lá, buff só na barra nova (só jogador local tem).

**Bug real achado e corrigido junto (não pedido pelo usuário, mas
autorizado explicitamente antes de tocar)**: `_grant_jungle_boss_buff`
usava o MESMO `label` pra TODOS os modifiers de uma concessão — se o
buff combina 2+ atributos numa concessão só (ex: buff_cycle índice 2 =
armor+stamina, ou attack_power+haste_rating), `add_timed_modifier()`
(`engine/stat_fns.py`) via o 2º label como "já existe" e só renovava o
timer do 1º, NUNCA aplicando o 2º atributo de verdade — silencioso, sem
erro, sem log. Corrigido: cada atributo dentro da MESMA concessão ganha
seu próprio label (`f"{label_base}_{attribute}"`), todos compartilham a
`duration` (expiram juntos na prática). `tests/test_jungle_camps.py`
tinha um teste que validava (sem querer) o comportamento ANTIGO —
reescrito pra checar o CONJUNTO de atributos aplicados, não só o
primeiro.

**Limitação aceita, avisada ao usuário**: `StatusEffects` só guarda 1
`ActiveEffect` por chave — matar o MESMO chefe de jungle 2x antes do
buff anterior expirar mostra 1 ícone só (duração renovada), não 2
empilhados. O efeito de STATS continua empilhando normalmente (não
mudou); só a representação visual simplifica pro que o ícone já suporta.

**Testes**: `tests/test_buff_bar_status_effects_sync.py` (novo, `WorldServer`
real — proc de item real com RNG forçado, buff de chefe de jungle
incluindo o caso combinado e o caso de stacking, self-buff de skill,
`grant_visible_buff` isolado); `tests/test_nameplate_buff_filter.py`
(novo — filtro de nameplate isolado, sem pygame/screen real). Revert-to-
confirm em `grant_visible_buff` (sem `apply_effect`) e no filtro de
nameplate — falharam exatamente como o comportamento antigo antes de
restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`): 1387 passed
+ 1 falha pré-existente/intermitente já catalogada (`test_mob_despawn_
sent_to_both_players`, domínio de AOI de mob, sem relação — passa
sempre isolado).

**Validação**: pendente — usuário ainda vai testar em jogo.

**Fase 2 (mesmo dia) — usuário apontou mais efeitos que deveriam
aparecer**: Escudo de Fogo, Bloco de Gelo, Camuflagem, Fatiador de
Corpos, Chama Interna, Na Mosca, Lapso Elemental. Investigação (Explore
agent, tudo conferido linha a linha) achou que NENHUM desses usa
`timed_modifiers` — cada um tem mecanismo PRÓPRIO:

- **Fatiador de Corpos**: já aplica `cc_immune` via `apply_effect`
  direto (`engine/skill_handlers.py:857`) — já era StatusEffects,
  `is_buff=True`, já aparecia na buff bar de graça, zero trabalho.
- **Lapso Elemental (parte de crit_rating)**: achado um buff REAL que
  ficou de fora da varredura da fase 1 — usava `add_timed_modifier`
  direto (`server/spell_completion_processor.py:961`, espelho
  `ui/spell_system.py:1527`), nunca visível em lugar nenhum. Corrigido
  igual qualquer buff da fase 1: roteado por `grant_visible_buff`, chave
  nova `lapso_elemental_crit` (distinta de `elemental_lapse`, o debuff
  de queima que continua no MESMO caster — StatusEffects só guarda 1
  por chave, não dava pra reusar).
- **Escudo de Fogo / Bloco de Gelo**: componente dedicado
  (`FireShieldEffect`/`IceBlockEffect`, `engine/components.py:1669,1676`)
  com tick DUPLICADO cliente+servidor já funcionando
  (`server/world_server.py:5631`/`5670`, `ui/spell_system.py:1780`/`1740`).
  Bloco de Gelo já tem barra de progresso própria no HUD
  (`client/hud_handlers.py:262`).
- **Camuflagem**: `CombatStats.camouflage_timer` (contagem regressiva
  direta) conectado em 10+ arquivos — visibilidade AOI
  (`server/session.py:59`, decide o que é mandado pra OUTROS clientes),
  alvo de mob (`server/mob_system.py`), respawn/ghost
  (`server/respawn_system.py`, mesmo flag `is_visible` reusado),
  targeting em combate (`engine/world_systems.py`, 7+ pontos).
- **Chama Interna / Na Mosca**: `CharacterStats.fire_instant_ready` /
  `CombatStats.na_mosca_bonus_active` — flags booleanas SEM duração
  nenhuma, ficam True até serem consumidas pelo próximo cast (não é
  "buff por N segundos", é "proc pronto pra usar", categoria diferente).

**Decisão do usuário**: NÃO migrar nenhum desses pra StatusEffects —
Camuflagem sozinha tem consumidores demais (mexer no mecanismo dela é
risco alto pra um ganho só visual); Escudo de Fogo/Bloco de Gelo têm
tick duplicado já funcionando, testado, em produção. Em vez disso, a
buff bar ganhou a capacidade de LER esses componentes/campos como estão
— `ui/buff_bar.py::_gather_extra_buffs(world, player_entity)`, 1
checador pequeno por fonte, cada um só lê (nunca escreve) e devolve um
"fake effect" sintético (`_FakeEffect`, mesmo padrão do shim de stun no
nameplate, `ui/systems.py`) com `effect_type`/`duration` — os 2 únicos
atributos que `_build_effects_row()` precisa. Proc sem duração
(Chama Interna/Na Mosca) aparece com `duration=0` — `_build_effects_row`
já pula o overlay de tempo quando `duration<=0`, comportamento existente
reaproveitado sem mudança. Bloco de Gelo/Fatiador de Corpos MANTÊM a UI
própria que já tinham (barra de progresso / canalização na hotbar) — o
ícone da buff bar é adicional, não substitui (decisão do usuário, menor
risco).

6 entradas novas em `EFFECT_DEFS` (`escudo_fogo`, `bloco_de_gelo`,
`camuflagem`, `chama_interna`, `na_mosca`, `lapso_elemental_crit`) — as
5 primeiras NUNCA são de fato aplicadas via `apply_effect` (só existem
pra dar label/cor de fallback aos "fake effects" sintéticos); só
`lapso_elemental_crit` é uma StatusEffects de verdade.

Confirmado (grep exaustivo): `enraged`/`haste`/`regen` em `EFFECT_DEFS`
não têm NENHUM produtor no código atual — entradas órfãs pré-existentes,
fora de escopo (não é "deveria aparecer e não aparece", é infra sem uso).

**Testes**: `tests/test_buff_bar_extra_sources.py` (novo — `_gather_extra_buffs`
isolado: cada fonte aparece/some conforme o campo/componente real muda,
duração calculada certa pros com `duration-elapsed`); +1 teste em
`tests/test_buff_bar_status_effects_sync.py` (Lapso Elemental crit real,
`WorldServer`, RNG de crit forçado via mock, guarda de regressão
confirmando que o debuff de queima continua intacto e separado).
Revert-to-confirm em `_gather_extra_buffs` (retorno vazio forçado — 5 de
7 testes falharam como esperado) e no wiring do Lapso Elemental (voltou
pro `add_timed_modifier` direto — falhou como esperado) antes de
restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`): 1395 passed
+ a mesma falha pré-existente/intermitente de sempre, sem relação.

**Validação**: pendente — usuário ainda vai testar em jogo.

**Ajuste (01/09/2026)**: ícone da buff bar pedido maior — 32×32 em vez
dos 16×16 padrão de nameplate. `_build_effects_row()` (`ui/systems.py`)
ganhou parâmetro opcional `icon_size` (default `None` = mantém 16px pra
todo call site existente — nameplate local/mob remoto/player remoto
intocados); PNG de 16×16 é reescalado com `pygame.transform.scale` (não
`smoothscale`, de propósito — mantém nitidez pixel-art no upscale).
Novo `UI.BUFF_BAR_ICON = 32` (`ui/ui_sizes.py`, escala com `_u()` igual
todo resto da UI). Fila (mais novo à direita, mais antigo empurrado pra
esquerda, 3px de gap) já funcionava assim de propósito — confirmado ao
usuário, não precisou de mudança — MAS achada uma limitação real ao
explicar: a ordem só é cronologicamente correta DENTRO de cada fonte
(StatusEffects entre si). Os "fake effects" de `_gather_extra_buffs`
(Escudo de Fogo/Bloco de Gelo/Camuflagem/procs) são sempre concatenados
DEPOIS da lista de StatusEffects, então aparecem sempre como o "grupo
mais novo" (mais à direita) mesmo que tenham sido ativados antes de um
buff de StatusEffects — não existe um relógio compartilhado entre os 2
grupos hoje. Avisado ao usuário; não corrigido ainda (pendente decisão
se vale a complexidade de um timestamp real cross-fonte).

**Correção (01/09/2026) — usuário pediu explicitamente, com restrição
de arquitetura clara**: "não faça correções que hardcodam nada... se
precisar criar um sistema que centraliza esses efeitos, crie, mas não
ponha um pedaço de código em cada efeito". Criado `engine/effect_order.py`
— ponto único novo: um contador monotônico (`_next_seq()`) guardado no
componente `EffectOrder` (`engine/components.py`, `dict[effect_type,
seq]` por entidade), com 2 funções (`mark_activated`/`get_seq`).

**Cobertura sem call site novo em quase tudo**: `engine.core_systems.
apply_effect()` (o funil único de TODO buff/debuff de StatusEffects —
proc de item, buff de skill, buff de jungle boss, `cc_immune`,
`lapso_elemental_crit`, e qualquer buff futuro que passar por
`grant_visible_buff`) chama `mark_activated()` sozinho, na criação de um
`ActiveEffect` NOVO (nunca no refresh de um já ativo — buff que renova
não pula pra frente da fila). Zero código adicional em qualquer skill/
talento que já usa `apply_effect`/`grant_visible_buff`.

**Onde precisou de 1 chamada nova** (só nos mecanismos que NÃO passam
por `apply_effect`, confirmando o inventário do §88 fase 2): `client/
remote_entity_handlers.py::_sync_player_effects` (o ponto de ativação
REAL pro jogador local em modo online — servidor roda `apply_effect`
por trás, cliente só espelha, nunca rechama); `client/network_handlers.py`
(3 pontos — confirmação de Escudo de Fogo/Bloco de Gelo/Camuflagem pro
jogador local online, mesmo raciocínio: skill roda só no servidor,
cliente espelha ao confirmar); `engine/skill_handlers.py` (mesmos 3,
cobertura server-side/offline); Chama Interna e Na Mosca (2 pontos cada
— proc client `ui/spell_system.py` + proc server `server/
spell_completion_processor.py`, ambos com guard "só na transição
False→True", igual todo o resto).

`ui/buff_bar.py::BuffBar.render()` ordena a lista COMBINADA (StatusEffects
+ `_gather_extra_buffs`) por `get_seq()` antes de montar a fileira —
nenhuma comparação hardcoded por fonte, só 1 `sort()` genérico.

**Testes** (`tests/test_effect_order.py`, novo): contador cresce a cada
marcação nova; refresh de efeito já ativo NÃO reordena (`apply_effect`
integrado corretamente); e o teste que prova o bug relatado —
Escudo de Fogo ativado ANTES de um proc de item aparece à ESQUERDA dele
na fila combinada e ordenada (o inverso também testado). Revert-to-confirm
(`get_seq` retornando sempre 0) — 4 de 7 testes falharam exatamente como
o bug relatado (incluindo o teste de ordem cross-fonte) antes de
restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`): 1402 passed
+ a mesma falha pré-existente/intermitente de sempre, sem relação.

**Ajuste do mesmo pedido**: ícone da buff bar aumentado pra 32×32 (era
16×16, igual nameplate) — `_build_effects_row()` ganhou `icon_size`
opcional (default preserva 16px em todo call site de nameplate/mob/
player remoto), novo `UI.BUFF_BAR_ICON=32` em `ui/ui_sizes.py`.

**Validação**: pendente — usuário ainda vai testar em jogo.

## 20. Tooltip nos ícones da buff bar (01/09/2026)

Usuário pediu tooltip ao passar o mouse num ícone da buff bar, igual já
existe nas skills da hotbar — explicitamente pedindo um sistema
centralizado, não um bloco de código por efeito (mesma régua já usada
no §88).

**Achado da investigação**: não existe um registro genérico de "área do
mouse → tooltip" no jogo — cada UI (hotbar, inventário) faz o próprio
hit-test e escreve numa caixa de correio compartilhada
(`self._pending_tooltip`/`self._pending_skill_tooltip`, `game.py`), que
`client/tooltip_handlers.py::_draw_tooltip()` desenha no fim do frame —
essa PARTE (a caixa em si) já é ponto único reaproveitável. O tooltip de
skill (`_skill_tooltip_lines`) tem uma cadeia grande de `if sid ==
"golpe_poderoso": ...` — texto calculado na mão por skill, exatamente o
padrão que o usuário pediu pra NÃO repetir aqui. `_build_effects_row()`
(a função que desenha os ícones, usada pela buff bar e pelo nameplate)
devolvia só uma `Surface` achatada, sem preservar qual pixel pertence a
qual ícone — faltava a base pra saber "o mouse está sobre qual buff".
`EFFECT_DEFS` só tinha `label` (nome curto), nenhuma frase descritiva em
lugar nenhum do jogo, pra nenhum buff.

**Decisões do usuário**: descrição = texto FIXO por buff (não calculado
ao vivo — evita replicar o padrão hardcoded do tooltip de skill); escopo
só a buff bar por agora (nameplate/debuff de qualquer entidade fica pra
depois, mecanismo já genérico o bastante pra estender sem reescrever).

**Mecanismo**: `EffectDef` (`content/status_effects_data.py`) ganhou
campo `description: str = ""` — mesmo catálogo central que já dá
label/cor/ícone, 1 frase por buff (as ~21 já cadastradas nos §88/89:
procs de item, self-buffs de skill, buffs de chefe de jungle,
`cc_immune`, `lapso_elemental_crit`, Escudo de Fogo, Bloco de Gelo,
Camuflagem, Chama Interna, Na Mosca). `_build_effects_row()`
(`ui/systems.py`) passa a devolver `(surface, rects)` —
`rects: list[(effect_type, Rect_local)]`, o MESMO `ix`/`ICON` já usados
pra desenhar, só que também guardados — mesmo princípio da hotbar
(geometria calculada 1x, nunca duplicada num hit-test separado). Os 3
call sites existentes (nameplate, mob remoto, player remoto) só
desempacotam a tupla nova, sem mudança de comportamento.

`ui/buff_bar.py::BuffBar` guarda `_last_icon_rects` (rects traduzidos
pra coordenadas de TELA + duração de cada um) a cada `render()`; novo
`check_hover(mx, my)` devolve `(label, lines)` no MESMO formato que
`_draw_tooltip()` já espera — `lines` = descrição (se houver) + "Ns
restantes" (só quando `duration>0`, mesma regra que o ícone já segue —
proc sem timer tipo Chama Interna não mostra número). `game.py` chama
isso depois do `render()` da buff bar e alimenta `self._pending_tooltip`
— zero código de desenho novo, reaproveita 100% o que já existe.

**Testes** (`tests/test_buff_bar_tooltip.py`, novo): toda entrada de
`EFFECT_DEFS` alcançável pela buff bar tem `description` não-vazia
(guarda de regressão pra buff futuro esquecer o campo); `_build_effects_row`
devolve rects na posição/ordem certa; `check_hover` acha o ícone certo
sob o mouse, devolve `None` fora de qualquer ícone, e não mostra linha
de tempo pra proc sem duração. Revert-to-confirm (description apagada
de 1 entrada) — o teste de guarda falhou exatamente como esperado antes
de restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`): 1411
passed + a mesma falha pré-existente/intermitente de sempre, sem
relação.

## 21. Comando `/forfeit`/`/ff` na BG (01/09/2026)

Usuário pediu: mesma UX do `/ff` que a Arena já tem, aplicada ao
battleground — sem votação de time (versão de testes), qualquer player
que digitar decide a partida na hora, placar igual ao de Nexus
derrubado, derrota pro time de quem desistiu. Perguntei escopo (só
fila real de matchmaking, ou também `/testbg` debug) — usuário
confirmou os dois.

**Achado da investigação que mudou o esforço estimado de "pequeno" pra
"médio"**: `/forfeit`/`/ff` já é interceptado no CLIENTE pela Arena
(`client/arena_handlers.py::_try_handle_arena_chat_command`) — e o
interceptor SEMPRE engolia o comando (`return True`), mesmo fora de
partida de Arena, só mostrando "você não está em uma partida de
Arena". Se a BG ganhasse um interceptor simétrico chamado DEPOIS do da
Arena na cadeia (`client/chat_handlers.py::_send_chat_message`),
qualquer player numa partida de BG (mas não de Arena) digitando `/ff`
nunca chegaria a ver o handler de BG — a Arena sempre roubava o
comando primeiro. Existem também DOIS sistemas de BG paralelos, cada
um com sua própria função "Nexus caiu, monta placar" quase-gêmea
(`server/bg_queue_processor.py::notify_bg_queue_nexus_destroyed` pra
fila real matchmade; `server/debug_battleground.py::
notify_nexus_destroyed` pro `/testbg` de debug, estado global módulo
em vez de dict por partida) — `/ff` precisa de uma versão "declara o
time ADVERSÁRIO vencedor" em cada um, sem duplicar o cálculo de
placar.

**Mecanismo**:
- Extraído `BgQueueProcessorMixin._finish_bg_match(match, match_id,
  winner_faction)` do bloco de placar de `notify_bg_queue_nexus_destroyed`
  (mesmo cálculo de K/D/A/Farm/Gold/Dano por delta) — reaproveitado por
  novo `request_bg_forfeit(eid)`: acha o time de `eid`, declara o
  ADVERSÁRIO vencedor, chama `_finish_bg_match`. Guardas: `not_in_match`/
  `already_decided`/`fight_not_started` (antes do portão abrir não faz
  sentido desistir de uma luta que nem começou).
- Mesmo padrão espelhado em `server/debug_battleground.py`: extraído
  `_finish_debug_match(ws, winner_faction)` de `notify_nexus_destroyed`,
  novo `_forfeit(ws, eid)` e subcomando `/testbg ff` no `handle_command`
  já existente (zero mensagem de protocolo nova — mesmo texto puro que
  `/testbg leave` já usa).
- `MsgType.BG_FORFEIT` novo (C→S `{}`, espelha `ARENA_FORFEIT`) só pra
  fila real. `server/session.py::_handle_bg_forfeit` despacha pro
  `request_bg_forfeit`.
- **Fix necessário na Arena** (`client/arena_handlers.py::
  _try_handle_arena_chat_command`): "não está em partida de Arena" virou
  `return False` (passa a vez) em vez de `return True` (engolia com
  aviso) — a mensagem final de "não está em partida nenhuma" passou a
  ser responsabilidade de quem está no fim da cadeia (o handler de BG,
  que sempre reenvia pro servidor e deixa o servidor responder com texto
  claro quando não há partida nenhuma — mesmo padrão que `/testbg` já
  usa pra qualquer uso indevido).
- Novo `client/battleground_handlers.py::_send_bg_forfeit`/
  `_try_handle_bg_forfeit_chat_command`: fila real → `BG_FORFEIT`; senão
  → texto puro `/testbg ff` (mesmo espírito de `_send_bg_leave`, que já
  faz essa mesma distinção pro botão "Voltar"). O handler de BG SEMPRE
  consome "/forfeit"/"/ff" quando chega até ele (depois da Arena na
  cadeia) — mesmo fora de qualquer partida, porque o servidor responde
  com uma mensagem de chat clara nesse caso.

**Testes**: `tests/test_bg_queue.py::TestBgQueueForfeit` (fila real) —
desistir decide a partida com derrota pro PRÓPRIO time (não só "eu
saio"), vencedor certo pros 2 lados, guardas de not_in_match/
fight_not_started/already_decided, 2ª chamada não sobrescreve.
`tests/test_debug_battleground_match.py::TestForfeit` (debug) — mesmo
conjunto adaptado ao estado global. Revert-to-confirm em 2 pontos
(winner-side hardcoded pra `arena_time_a` em cada `request_bg_forfeit`/
`_forfeit`) — cada um derrubou exatamente os testes sensíveis à direção
certa antes de restaurar. Suíte completa (`pytest tests/ -q`,
`py -3.10`): 1430 passed + a mesma falha pré-existente/intermitente de
sempre, sem relação.

**Fora do automatizável**: a cadeia de interceptação de chat no
cliente (Arena passa a vez → BG assume) não tem cobertura de teste
automatizado — nenhum teste client-side prévio pra esses mixins de
comando de chat existia no projeto, e criar um harness de `GameEngine`
completo só pra isso ficou desproporcional ao tamanho da mudança
(lógica é 1 `if`/`return` por handler). Usuário valida em jogo 3
cenários: `/ff` dentro de Arena, `/ff` dentro de BG (fila real ou
`/testbg`) sem estar em Arena, `/ff` fora de qualquer partida.

## 22. "TX Village Props with Shadow" — grupos por categoria no minimapa (02/09/2026)

Usuário pediu: catalogar os 213 objetos de `"TX Village Props with
Shadow"` em grupos temáticos (cemitério, pedras, móveis, etc.), mesmo
estilo já usado em `"TX Village Plant"` (§78 — `"groups"`, cor própria
por categoria no minimapa/mapa-múndi). Família inteira usava 1 cor só
(`(41,39,55)`) pra tudo — cemitério, pedra, móvel, comida, todos
indistinguíveis no minimapa, mesmo sintoma do §78.

**Investigação visual**: 213 sprites, catálogo flat sem nome/tema
nenhum (só ids "001".."214"). Gerado contact sheet anotado (recorta
cada sprite pelo próprio retângulo já catalogado, rotula com o id,
grade de 30 por página, 8 páginas) pra inspecionar todos com precisão
— não deu pra adivinhar por número, precisava ver o sprite de cada um.

**Categorização** (9 grupos, todos com `"prefix":"vp"` — ver decisão de
arquitetura abaixo):
- `cemiterio` (24: lápides, cruzes, túmulos, caixão, estátuas, cripta)
- `pedras` (27: rochas soltas decorativas)
- `plantas_jardim` (10: sebes/topiaria, vasos com flor, jardineiras)
- `moveis` (30: baús, camas, armários, mesas, bancos, tapete, bigorna)
- `potes_e_recipientes` (31: jarros, potes, barris, baldes, sacos)
- `comida` (27: frutas, legumes, queijo, carne, tábua de corte)
- `madeira_e_cercas` (29: toras, tábuas, postes, cercas, janelas soltas)
- `decoracao_vila` (30: espantalho, placas, poço, luminária, fogueira,
  alvo de tiro, boneco de treino, carroça)
- `diversos` (3: `vp001`/`vp199` sem tema óbvio + `vp214`, já marcado
  "COLISÃO INVISÍVEL" no comentário original — mantido como está)

Coberto por script (não à mão): parseia os 213 ids catalogados,
verifica cobertura 1:1 contra a lista proposta (zero id esquecido,
zero duplicata) ANTES de tocar no arquivo — usuário só aprovou a
proposta depois de ver a lista completa.

**Decisão de arquitetura**: `"prefix"` continua **"vp" em TODOS os 9
grupos** — nunca um prefixo por categoria. Mapas reais já têm objetos
plantados como `"vp_XXX"` (`tile_id = f"{prefix}_{id_suffix}"`); um
prefixo novo por grupo mudaria o id de todo objeto já colocado nos
CSVs, quebrando placement existente. Mesmo padrão que `"TX Village
Plant"` já usa (`"arvores"` e `"arbustos"`, dois grupos, MESMO prefixo
`"pl"` — a distinção semântica mora no `id_suffix`, nunca no prefixo do
grupo). Só a cor por grupo muda; nenhum id, geometria ou colisão foi
tocado — aplicado via substituição mecânica de texto (script Python,
não edição manual linha a linha), com verificação antes/depois: 213
ids registrados em `OBJECT_MAPPING` antes E depois, mesmas 213 tuplas
(sx,sy,w,h,col,...) byte-idênticas, CRLF do arquivo preservado (o
arquivo é 100% CRLF; um write ingênuo trocaria pra LF e geraria diff
gigante em linhas não tocadas — conferido explicitamente antes de
declarar pronto).

**Testes**: `tests/test_tileset_color.py`/`test_god_mode_random_brush.py`/
`test_god_mode_terrain_brush.py` (mexem em `SHEET_FAMILIES`/grupos) — 38
passed, sem regressão. Suíte completa (`pytest tests/ -q`, `py -3.10`):
1432 passed (a falha isolada de timing do §93 não reapareceu nesta
rodada — confirma que era mesmo timing, não algo desta mudança).

**Fora do automatizável**: usuário confere visualmente no editor de
mapa (F10) se as cores novas no minimapa fazem sentido pra cada
categoria — só validação visual, não tem asserção automatizada pra
"esta cor parece cemitério".

## 23. Recatálogo de "TX Tileset Terrain" — usuário reorganizou o PNG (02/09/2026)

Usuário substituiu/expandiu o PNG `TX Tileset Terrain` (mais variações
de grama) e pediu recatálogo. Investigação visual (contact sheet
ampliado, grid rotulado por célula — não adivinhado por número) +
confirmação direta do usuário resolveu 3 rodadas de ambiguidade:
"coluna 14" não existe (imagem só tem 14 colunas, índices 0-13 — erro
de contagem do usuário, corrigido pra coluna 13); split `grama_flor`/
`grama_pura` dentro da área nova (flor = só onde tem flor visível,
pura = resto, incluindo pedras espalhadas nas linhas 4-5 no mesmo
pool, sem zona separada); `piso_sobre_terra` também mudou de posição,
zonas de terra/transição NÃO mudaram (usuário confirmou explicitamente
— evitou eu recatalogar coisa que já estava certa).

**Mudança**: `grama_flor` = c6-9/r0-3 (16 células, mesma posição de
antes por coincidência); `grama_pura` = c10-13/r0-3 + c6-13/r4-7 (48
células); `piso_sobre_terra` = c8-13/r8-12 (30 células, antes
c6-7/r4-6). `terra_pura`/`terra_flor`/`piso_sobre_grama`/zonas de
transição intocadas.

**Achado de brinde durante o recatálogo**: `color_overrides` da
família (cor no minimapa) tinha o comentário "DERIVADO das zonas —
ponto único de verdade — nunca diverge", mas era hand-written de
verdade — ficaria com cor errada (pedra em vez de grama) pros tiles
novos. Fix: `_derive_terrain_zone_colors(fam)` novo, roda 1x após
`SHEET_FAMILIES` ser definido, deriva cor de verdade a partir de
`zone["terrain"]`/`purpose=="floor_full"` pra qualquer família com
`zones` — agora o comentário é verdade, um recatálogo futuro não
precisa mais chasear `color_overrides` à mão.

**Testes**: `tests/test_god_mode_terrain_brush.py`/
`test_tileset_color.py`/`test_god_mode_random_brush.py` — 38 passed,
sem regressão (zonas de transição/terra, únicas coisas que os testes
cobrem em detalhe, ficaram intocadas). Suíte completa: 1424 passed +
8 falhas pré-existentes rastreadas até uma família `"pr"` removida
pelo usuário numa limpeza PARALELA e não relacionada (não mexida,
avisado ao usuário).

**Fora do automatizável**: usuário valida visualmente em jogo/editor
se as texturas novas de grama aparecem certas.

## 24. Catalogação de "TX Village Building with Shadow" — construções fatiadas em várias peças (02/09/2026)

Usuário trouxe um novo sheet de construções (`assets/tiles/"TX Village
Building with Shadow".png`) e criou 3 arquivos de apoio no mesmo
diretório — `(dimensions).png` (retângulo de corte de cada peça,
outline vermelho), `(collisions).png` (área de colisão real, fill
vermelho semi-transparente), `(groups).png` (blocos verdes de
proximidade no sheet) — porque várias construções vêm fatiadas em
múltiplas peças separadas dentro do PNG (colisão em mais de 1
retângulo, ou outros motivos de arte).

**Decisão de mecanismo (perguntada ao usuário antes de tocar em
qualquer arquivo, `AskUserQuestion`)**: pedaços continuam sendo
objetos INDEPENDENTES no catálogo — sem mecanismo novo de "objeto
composto" no motor (que exigiria formato de dado novo, placement
novo, colisão combinada, serialização no mapa — trabalho de engine
real, não só cataloging). O arquivo `(groups)` é usado só como
referência visual pra não perder pedaço nenhum durante a extração —
**não** significa "1 construção por caixa verde": o mesmo bloco pode
conter várias construções distintas lado a lado (achado durante a
investigação — ex. o bloco que cobria uma "torre" também continha,
grudados sem gap no sheet, um alpendre pequeno e um coreto alto
inteiramente não-relacionados; outro bloco continha as 17 peças do
kit de muro modular; outro continha as 9 barracas de mercado). Decidir
quais peças formam 1 construção só exigiu inspeção visual peça a peça
(adjacência no sheet sozinha é enganosa — texture packers colam
sprites não-relacionados sem gap pra economizar espaço).

**Extração de retângulo por script, não a olho** (`numpy`/`scipy.
ndimage.label` sobre os 3 PNGs de apoio — que usam cores sólidas
exatas: outline vermelho opaco em `dimensions`, fill vermelho
alpha=128 em `collisions`, outline verde opaco em `groups`) —
component-labeling sobre as regiões de fundo delimitadas pelas linhas
deu 104 peças (`dimensions`), colisão de cada uma cruzada por
interseção geométrica contra os blobs de `collisions` (94 peças com 1
retângulo de colisão, 10 sem colisão — telhados de coreto/mesa de
barraca/degrau de entrada, passáveis). 1 peça teve colisão espúria de
1px (artefato de arredondamento na borda entre peças vizinhas),
zerada manualmente. Nomeação (descritiva, decisão do usuário via
`AskUserQuestion`) e agrupamento em "1 construção = várias peças"
vieram só de inspeção visual (crops compostos por faixa do sheet,
104 peças em 5 imagens ao invés de 1 por peça, pra não gastar contexto
demais). Contact sheet final (104 crops rotulados, gerado por script)
usado como QA visual antes de declarar pronto — pegou nomes
inconsistentes com a arte antes de commitar.

**`vision_height` (bloqueio de FOV, só `>=2` conta pro `FogSystem`)**:
regra sistemática, não julgamento peça a peça — 2 pra qualquer peça
com colisão nos grupos "construcoes"/"muralhas" (mesmo padrão de
`WALL_TILE`: construção/muro é alto o suficiente pra bloquear visão);
0 pra peça sem colisão. Exceção no grupo "mercado_e_diversos": bancas
de mercado e poços cobertos têm colisão (não atravessa) mas NÃO
bloqueiam visão (altura de balcão/cintura, não de parede) — só tenda
indígena e casinha do poço (altura de estrutura pequena de verdade)
bloqueiam ali. Regra não confirmada item a item com o usuário — se
alguma peça bloquear visão errado em jogo, é aqui que ajustar.

**Resultado (1ª versão, ERRADA — ver §99 pra correção no mesmo dia)**:
1 entrada nova em `OBJECT_SHEET_FAMILIES` (`engine/tileset.py`, prefixo
`"vb"`, 104 tiles, 3 grupos — `"construcoes"` 71 peças, `"muralhas"` 17
peças, `"mercado_e_diversos"` 16 peças — mesma cor pra todos os 3,
`(115, 108, 98)`, decisão do usuário já que a paleta visual é parecida
entre todas as construções). Suíte completa rodada após a mudança.
**Fora do automatizável**: usuário ainda não validou em jogo.

## 25. Recatalogação de "TX Village Building with Shadow" v2 — usuário editou os 4 PNGs de apoio, ids viraram sequenciais "vb001".."vb097" (03/09/2026)

Usuário reeditou os 4 arquivos de apoio (arte principal, colisões,
dimensões, agrupamento) do §98 — 36 peças removidas, 29 adicionadas
(104 → 97 no total), a maioria por RESLICE (mesma construção, corte
interno diferente) em ~10 das 29 construções, não wholesale redo. Uma
construção inteira ("Casebre pequeno", antiga `b05`) saiu da arte nova
— sem substituto, tratada como removida de propósito, não bug.

**Fluxo de reprocessamento** (mesmo script de component-labeling do
§98, rodado do zero nos 4 arquivos atuais): os 30 retângulos verdes
bateram 1-a-1 na MESMA posição do catálogo anterior (nenhuma
construção mudou de lugar no sheet, só o conteúdo interno) — permitiu
reatribuir cada peça nova ao grupo/label antigo por OVERLAP geométrico
contra o catálogo salvo em `engine/tileset.py` (nunca a olho), robusto
a reslice/merge desde que a posição geral não mude. `_object_scan_
margin_tiles()` do §102 é data-driven (deriva do catálogo, não
hardcoded) — não precisou de ajuste nenhum com a mudança de tamanho
dos objetos.

**Achado à parte, não usado**: `(collisions).png` ganhou um blob de
~700px em vermelho OPACO (não o preenchimento semi-transparente usual)
perto da área de "Salão comprido com torre" (x≈1856-1951, y≈1088-1343)
— formato de contorno fino, não preenchimento. Ignorado na extração
(só o padrão semi-transparente estabelecido conta como colisão real) —
se era pra ser uma colisão nova de verdade, não teve efeito; avisar o
usuário.

**IDs sequenciais puros (pedido do usuário, 2 rodadas de ajuste)**: 1ª
tentativa (`"prefix": ""` por grupo, id_suffix já contendo "vb001") deu
certo tecnicamente mas o usuário rejeitou — queria `"prefix": "vb"`
visível no código, igual toda outra família, só sem o separador `"_"`.
Como `_register_catalog_tiles` é FUNÇÃO COMPARTILHADA por todo o
catálogo (tirar o `"_"` de vez quebraria id de TODAS as outras famílias
— `vp_001`→`vp001`, etc., referenciadas em mapas reais), a mudança
virou opt-in por família/grupo: `"no_prefix_separator": True` no dict
do grupo faz `tile_id = f"{prefix}{id_suffix}"` em vez de
`f"{prefix}_{id_suffix}"` — default `False`, nenhuma outra família
afetada. Grupo do catálogo (`"b01"`.."b29"`, `"muralhas"`,
`"mercado_e_diversos"`) continua só organização interna do arquivo, o
nome descritivo mora só no `"label"` do grupo — ids não carregam mais
código de construção nem "_N" de sub-peça.

**`map_1_objects.csv` já tinha 50 peças plantadas com os ids antigos**
(usuário validou o catálogo em God Mode antes desta correção, ver §98
fechamento) — recatalogar SEM corrigir o mapa deixaria 50 referências
mortas. Fix em 2 categorias, decididas por geometria (nunca adivinhado):
27 peças cuja posição/tamanho NÃO mudou entre catálogo antigo e novo
(`(sx,sy,w,h)` idêntico) foram renomeadas 1-a-1 (bookkeeping puro, zero
risco visual); 25 peças cuja construção foi resliced/merged (posição
não bate mais exatamente com nenhuma peça nova) foram REMOVIDAS do
mapa (célula limpa pra `"."`) em vez de adivinhada — colocar a peça
errada seria pior que deixar vazio. Usuário precisa replantar essas 25
manualmente no God Mode (lista completa no resumo desta sessão, não
repetida aqui). Construções afetadas: casa pequena com chaminé, coreto
de poço coberto (4 de 5 peças), casebre pequeno (removida da arte),
torre de vigia de madeira (3 peças), torre com celeiro anexo (2 de 4
peças), chalé com chaminé, torre com alicerce, salão comprido com
torre (6 peças), tenda indígena, e o muro-lateral `m11`.

**Validação**: 97 ids únicos (`vb001`..`vb097`), sem duplicata; suíte
completa 1445 passed, 0 failed; contact sheet de 97 crops rotulados
gerado e conferido visualmente (mesmo processo de QA do §98) antes de
declarar pronto; nenhuma referência `vb_` (formato antigo) sobrando em
`map_1_objects.csv`, nenhum id no mapa fora do catálogo atual
(checagem programática, não só visual).

**Fora do automatizável**: usuário replantar as 25 peças limpas do
mapa (lista acima) e validar visualmente as construções resliced.

## 26. Rastreio de objetivo de quest no mapa/minimapa — desenho e 1ª implementação (03/09/2026)

Pedido do usuário: quando o player tem uma quest ativa, mostrar no
minimapa E no mapa cheio (tecla M) um círculo indicando onde encontrar
o alvo daquele objetivo — tracejado amarelo, preenchimento 50%
translúcido, raio igual ao raio de respawn do mob (quando aplicável),
com tooltip no hover mostrando quest+objetivo. Discussão levou várias
rodadas (ver histórico da conversa) até fechar o desenho abaixo.

### Decisões de desenho

- **Campo novo `ObjectiveDef.map`** (`content/quests_data.py`) — mapa
  onde o ALVO do objetivo se encontra. `""` = mapa atual (sem
  cross-map). **Exceção deliberada**: `reach_tile` NÃO usa esse campo
  — esse tipo já usava `target` como filtro de mapa desde antes (ver
  docstring do módulo); unificar exigiria reescrever quests existentes
  sem ganho real, decisão do usuário foi manter como está.
- **Conflito resolvido**: `use_skill`/`learn_skill` já usam `target`
  pro skill_id (autoritativo pro MATCH da quest, `engine/quest_logic.py
  ::match_objective`). O pedido original do usuário era colocar o id
  do boneco/treinador EM `target` — INVIÁVEL, quebraria a conclusão da
  quest (nada mais identifica qual skill é a certa). Resolvido
  colocando a referência em `params` (`dummy_id`/`trainer_id`), nunca
  em `target` — `target` continua == skill_id sempre. Exemplo
  documentado em `content/quests_data.py` (linhas ~29-49, no docstring
  do módulo) e aplicado em 3 quests reais (`prova_valor`,
  `iniciacao_arcana`, `coach_quantic`).
- **`training_dummies` no `{map}_entities.json` ganhou `"id"`**
  (`engine/map_loader.py::_merge_entities_json` — tupla passou de
  `(x, y)` pra `(x, y, id)`; `server/world_server.py::
  _create_training_dummies` ajustado pro unpack de 3, ignora o id —
  não precisa no lado gameplay, só existe pro rastreio). `trainers` já
  tinha `name`, não precisou de mudança de schema.
- **Círculo de harvestable fixo (item único, não zona) nunca
  centralizado no item real** — pedido do usuário: centralizado
  entregaria a posição exata de cara, tirando a graça da busca.
  Deslocamento é DETERMINÍSTICO (hash espacial simples,
  `(x*73856093) ^ (y*19349663)`, não `random`/`hash()` builtin — este
  último varia por processo, quebraria a consistência entre minimapa e
  mapa cheio desenhando em posições diferentes). Raio mínimo 5 tiles
  (mesmo motivo — círculo pequeno demais entregaria a posição). Zona
  de harvestable (`harvestable_zones`, já tem raio cadastrado) NÃO
  precisa de deslocamento — já é imprecisa por natureza.
- **`collect_item` de harvestable exige `target="harvestables"` ou
  `target="harvestable_zones"` explícito** (não mais só `"*"`) — é
  como o rastreador sabe em qual lista do `{map}_entities.json`
  procurar. Nenhuma quest real usa esse caso hoje (as 3 existentes já
  usam `target`=raça de mob), então nenhuma migração de dado foi
  necessária — só documentado pro futuro.
- **Cross-map**: se o objetivo está em outro mapa (via `obj.map`, ou
  via `obj.target` pra `reach_tile`), busca em
  `spawn_points["transitions"]` do MAPA ATUAL uma entrada com
  `target_map` igual — desenha um círculo de 1 tile (mesma unidade dos
  outros raios, NUNCA px de tela — decisão do usuário depois de eu
  apontar que 20px seria menor que 1 tile) na posição da transição, cor
  igual ao alvo real (decisão do usuário: "amarelo, simplicidade").
  Sem transição direta → sem círculo (multi-hop A→B→C explicitamente
  fora de escopo por pedido do usuário, "melhor não se preocupar com
  isso agora").
- **Corte de visibilidade = mesmo do HUD lateral**
  (`QuestSystem.MAX_HUD_QUESTS`/`_sorted_active_items`) — nunca mais
  círculos no mapa do que quests visíveis no HUD. Objetivo já completo
  (`prog[i] >= obj.count`) não aparece.
- **Só quest ATIVA** (`qid in ql.active`) — sai da lista sozinho quando
  completa/abandonada, sem mecanismo extra (`ql.active` já É "aceita e
  não completada").

### Arquitetura (pontos únicos de verdade novos)

- **Resolução de posição/estilo** — `ui/map_markers.py::
  collect_quest_circles()` (mesmo arquivo/filosofia de
  `collect_markers()`: devolve dados prontos, `MapOverlay`/`Minimap`
  só desenham, não conhecem regra de nenhum tipo de objetivo). Roda
  **client-side**, lendo `spawn_points` (já carregado localmente pelo
  cliente via `load_map_csv` — mesmo dado que já monta harvestables/
  spawn_zones/etc pro cliente renderizar hoje, não é gameplay, não
  precisa de mensagem de rede nova) e o componente `QuestLog` (já
  sincronizado do servidor pro cliente, mesmo padrão de sempre).
- **Desenho** — `ui/map_markers.py::draw_quest_circle_marker`/
  `draw_quest_rect_marker` (chamadas por `Minimap.render`/
  `MapOverlay.render`, que só convertem tile→tela — geometria própria
  de cada um). Primitivas de baixo nível (círculo/retângulo tracejado,
  preenchimento translúcido — pygame não tem nativo) em
  `ui/ui_helpers.py::draw_dashed_circle`/`draw_translucent_filled_
  circle`/`draw_dashed_rect`.
- **Hit-test do tooltip** — `ui/map_markers.py::quest_circle_hit_test`/
  `quest_rect_hit_test`, usando a MESMA geometria (sx/sy/radius_px) já
  calculada pro desenho — nunca recalculada à parte (mesmo padrão de
  `ui/systems.py::_build_effects_row`). Tooltip desenhado por
  `ui/ui_helpers.py::draw_box_tooltip` (mesmo estilo visual do tooltip
  de skill/efeito, `client/tooltip_handlers.py`), texto = título da
  quest + `QuestSystem._obj_label()` (reaproveitado, não duplicado).
- **`self._spawn_points`** (novo atributo em `game.py`) — guarda o
  `spawn_points` do mapa atual pros dois render loops (antes só
  existia como variável local dentro da função de carga de mapa,
  descartada depois). Atualizado nos 2 pontos de carga (mapa inicial e
  troca por transição), sempre em sincronia com `self._current_map_file`.

**Testes**: `tests/test_quest_map_tracking.py` (19 casos) — cobre
resolução por tipo de objetivo (spawn_zone, harvestable fixo com
deslocamento+raio mínimo, harvestable_zone, dummy por id, trainer por
id, NPC por nome, reach_tile ponto único e área), objetivo completo
não aparece, corte igual ao HUD, cross-map com e sem transição direta.
Revert-to-confirm no cálculo de deslocamento/raio mínimo do
harvestable (zerado → teste correspondente falhou como esperado,
restaurado). Smoke test manual de renderização end-to-end (Minimap +
MapOverlay com círculos reais, SDL dummy) sem exceção. Suíte completa:
1466 passed.

**Bug real introduzido e corrigido no mesmo dia**: ao anexar as 4
funções de desenho novas (`draw_dashed_circle`/`draw_translucent_
filled_circle`/`draw_dashed_rect`/`draw_box_tooltip`) no FIM de
`ui/ui_helpers.py` via edit por trecho, o `return lines` que fechava
`item_tooltip_lines()` (função pré-existente, usada em QUALQUER
tooltip de item do jogo — inventário, loja, recompensa de quest) ficou
fisicamente ENTRE o corpo dela e as novas funções — o trecho
substituído (`old_string` do edit) terminava 2 linhas ANTES do
`return`, então o `return lines` original sobrou órfão, reindentado
por coincidência pra dentro de `draw_box_tooltip` (que por acaso
também tem um parâmetro chamado `lines`, então nem deu erro de
sintaxe — só devolvia um valor sem uso). Resultado: `item_tooltip_
lines()` passou a devolver `None` sempre, sem erro NENHUM na hora —
só quebrou quando algo tentou iterar o resultado
(`client/tooltip_handlers.py::_draw_tooltip`, `for line in lines`).
**Sintoma real reportado pelo usuário**: crash ao completar uma quest
com recompensa de skill (hover no ícone da skill de recompensa →
`TypeError: 'NoneType' object is not iterable`). A suíte completa
(1464 testes) passou LIMPA com o bug presente — **`item_tooltip_lines`
não tinha teste nenhum** (`grep` em `tests/` não achava a função),
então nada exercitava esse caminho. Fix: `return lines` devolvido pro
lugar certo (fim de `item_tooltip_lines`), removido o `return lines`
órfão de `draw_box_tooltip` (que não devia devolver nada — é uma
função de desenho). 2 testes novos em
`tests/test_quest_map_tracking.py::
TestUiHelpersDrawingPrimitivesNaoQuebramFuncoesVizinhas` (um com item
REAL do catálogo, não mock, pra reproduzir o cenário de verdade — ver
`feedback_test_real_objects_not_just_monkeypatched_fixtures` na
memória). Revert-to-confirm: com o bug reintroduzido de propósito, o
teste falhou exatamente como o sintoma relatado (`None is not an
instance of <class 'list'>`).

**Lesson**: editar por TRECHO (`old_string`/`new_string`) no fim de um
arquivo, logo depois do corpo de uma função existente, é uma classe de
risco específica — se o trecho substituído não inclui o `return`/fim
real da função, qualquer coisa que sobrar DEPOIS do trecho (inclusive
o `return` de verdade) passa a ficar dentro do bloco novo inserido, às
vezes sem nem gerar erro de sintaxe (como aqui, por coincidência de
nome de parâmetro). Depois de qualquer edit que anexa código no fim de
um arquivo/função, ler de volta o trecho inteiro (função antiga +
função nova) antes de seguir em frente — não só confiar que "o Edit
funcionou" pela ausência de erro da ferramenta.

**Revisão de tamanho — tile-space era errado pro mapa/minimapa (04/09/2026,
mesmo dia, feedback do usuário com print)**: 1ª versão desenhava raio em
TILES (`radius_tiles`, multiplicado por `tp`/`scale` de cada renderer) —
pareceu razoável em teoria ("mesmo raio do respawn real"), mas na prática
1 tile no jogo é sempre 32px, e a escala de EXIBIÇÃO do mapa (zoom livre
2-100px/tile no M) e do minimapa (janela fixa) varia — o mesmo raio de 30
tiles (spawn_zone de Elemental) virava um círculo cobrindo boa parte da
tela no zoom alto do M, e seria quase invisível no zoom baixo. Print do
usuário mostrou o círculo ocupando 1/4 do mapa cheio. Fix: `QuestCircle`
trocou `radius_tiles` por `radius_px` (SEMPRE px de tela fixo, igual
`map_markers.py::MAP_ICON_SIZE` já fazia pros ícones de ponto desde
sempre) + `offset_px` (deslocamento do harvestable, também fixo, não mais
embutido em `tile_x/tile_y`). Só a POSIÇÃO continua em tile-space
(convertida por cada renderer como qualquer marcador). `reach_tile`
continua em tile-space pro retângulo em si (representa uma ÁREA real do
mundo, não uma posição aproximada — não fazia parte da reclamação).
Tamanhos fixados por pedido explícito do usuário: `TARGET_MARKER_RADIUS_PX
= 15` (raio; "30px de dimensão" = diâmetro) pra alvo real (mob, item,
boneco de treino, marcador de transição) e `FRIENDLY_MARKER_RADIUS_PX =
MAP_ICON_SIZE // 2` (mesmo tamanho do ícone de quest "!"/"?") pra alvo de
conversa (talk_to_npc, treinador/learn_skill). **Reclassificação**: boneco
de treino (`use_skill` on_dummy) saiu do grupo "friendly" (conversa) e
entrou no grupo "hostile" (alvo real) — decisão do usuário: usar skill
nele é interação física, não conversa, mesma categoria visual de mob.

**Corte no limite da janela (mesmo feedback, mesmo dia)**: círculo perto
da borda do mapa (M) ou do minimapa vazava pro fundo escuro/verde fora da
moldura — pygame não corta desenho automaticamente por padrão. Fix:
`screen.set_clip(modal)` (M) / `screen.set_clip(frame_rect)` (minimapa)
antes de desenhar os círculos, restaurando o clip anterior
(`screen.get_clip()`) depois — mesmo padrão já usado em
`ui/quest_system.py::_blit_scrollable`/`ui/god_mode.py`.

**Testes**: `tests/test_quest_map_tracking.py` atualizado pro modelo
`radius_px`/`offset_px` (reclassificação do dummy incluída). Smoke test
manual: círculo desenhado bem perto da borda da janela (minimapa) sem
exception, `screen.get_clip()` confirmado restaurado depois do render
(não vaza pra outros desenhos do frame). Suíte completa: 1465 passed +
1 falha em `test_perf_log_improvements.py` confirmada flaky (passa
isolada, sem relação com este trabalho — teste sensível a timing sob
carga da suíte completa).

**Correção parcial no mesmo dia — spawn de mob volta a usar raio REAL,
sem limite**: usuário testou em jogo e confirmou que tamanho fixo pra
spawn de mob tirava informação real (a área de respawn de verdade ajuda
a entender o alcance do bicho) — pediu de volta especificamente pra esse
caso: "não é pra ter limite, o raio deve ser pego do raio do respawn".
Escopo confirmado com o usuário (`AskUserQuestion`): vale pra QUALQUER
círculo com raio REAL cadastrado no JSON — `spawn_zones` (mob) E
`harvestable_zones` (zona de item, mesma categoria de dado) — não só
mob. `QuestCircle` ganhou os 2 raios de volta lado a lado:
`radius_tiles` (raio real, escala com zoom, sem limite — usado quando
existe zona cadastrada) e `radius_px` (tamanho fixo — usado quando NÃO
há área real: NPC, boneco de treino, item único, marcador de transição).
Renderer escolhe um dos dois por círculo (`radius_tiles * scale if
radius_tiles > 0 else radius_px`) — nunca os dois juntos. O `set_clip`
do item anterior já cobre a consequência óbvia (círculo de raio real
grande passando da borda no zoom alto): ele CORTA visualmente na
moldura, sem limitar o dado em si — exatamente o que o usuário queria
(raio de verdade, mas nunca vazando pra fora da janela).

**Bug separado achado pelo usuário no mesmo dia — não é do sistema de
rastreio, é pré-existente**: marcador de quest giver (ícone "!"/"?" no
mapa/minimapa, `map_markers.py::collect_markers`, existia desde
11/07/2026) aparecia menor e borrado no minimapa comparado ao mapa
cheio — usuário mandou 2 prints lado a lado provando que é o MESMO
marcador com aparência diferente. Causa raiz: os dois desenham o
fallback (círculo + glifo) com o MESMO `icon_size=MAP_ICON_SIZE=16px`
fixo, mas cada view carrega sua PRÓPRIA fonte via `_FONT_BASES`
(`UIScaleMixin.set_ui_scale`, `ui/ui_scale_mixin.py:94`) — `MapOverlay`
usava base 20px, `Minimap` usava base 12px — nunca foi notado porque os
dois círculos nunca foram comparados lado a lado antes. Como o
`_font` de `Minimap` também passou a ser usado pelo "..." do círculo de
rastreio novo e pelo tooltip de hover (`draw_box_tooltip`), o mesmo
bug ia afetar os 3. Fix: `Minimap._FONT_BASES` alinhado pra 20, igual
`MapOverlay` — confirmado `MapOverlay._font.get_height() ==
Minimap._font.get_height()` (14px renderizado, escala 1.0) depois do
fix. Nenhum teste dependia do tamanho antigo (grep vazio).

**Pendente pro usuário validar em jogo** (não automatizável): visual
real dos círculos (mob/harvestable_zone com raio de verdade, resto com
tamanho fixo — cor/opacidade/tracejado/proporção como esperado),
tooltip aparecendo no hover, círculo de transição de mapa guiando
corretamente entre map_1 e uma caverna, corte correto na borda do
mapa/minimapa em zoom alto com raio real grande, marcador de quest
giver com o mesmo tamanho/nitidez nos 2 lugares.

## 27. Opacidade do preenchimento reduzida pra 25% (04/09/2026, mesmo dia)

Ajuste simples pedido pelo usuário: preenchimento do círculo/retângulo
de rastreio de quest (`ui/map_markers.py`) estava com alpha 110/90
(≈43%/35% — os 2 números já divergiam entre si, resquício de escrever
cada um à mão em vez de uma constante só). Unificado em
`QUEST_CIRCLE_FILL_ALPHA = 64` (25% de 255), usado pelos dois desenhos
(`draw_quest_circle_marker`/`draw_quest_rect_marker`) — nunca mais 2
valores diferentes pro mesmo preenchimento. Suíte completa: 1474 passed
(nenhum teste dependia do valor antigo).

## 28. Bot de teste de carga ganha pathfinding real, loot, skill à distância e travessia de bush (05-06/09/2026)

Usuário pediu, depois do §114, que os bots parassem de andar só tile-a-tile
aleatório e passassem a: (1) perseguir alvo distante com pathfinding de
verdade, (2) saquear loot após matar mob, (3) usar skill com alcance
real (não só melee), (4) atravessar bush de propósito. Preocupação
explícita: mandar bot pra ponto muito distante/cercado de parede
(spawn é no cemitério) poderia travar o pathfinding. Pesquisa com 3
agentes em paralelo (comportamento de bot / auditoria da implementação
real / plano de teste incremental) antes de codar.

**Achado que muda toda a análise de risco**: o servidor NUNCA calcula
rota pro player — `server/world_server.py::move_player()` só valida
passo a passo (orçamento tempo×velocidade + tile andável + linha reta
sem parede), nunca chama A*. Quem calcula o caminho inteiro é o
CLIENT (`ui/systems.py::_process_ground_move`/`_auto_move_step`, via
`engine.world_systems.find_path` — módulo sem pygame, chamável
headless), mandando 1 `MOVE` por tile conforme anda. `MAX_PATHFINDS_PER_FRAME`
(orçamento por tick) só existe pra IA de mob/minion
(`EnemyAISystem`/`MinionSystem`) — pathfinding de bot no PRÓPRIO
processo do swarm nunca compete com esse orçamento nem trava o tick do
servidor. Risco real (menor que o imaginado): CPU do processo de bots
competindo com o servidor na MESMA máquina, não trava de tick.

Achado incidental confirmado em código: saque de loot NÃO tem checagem
de distância nenhuma no servidor (`server/loot_processor.py::request_loot`
só valida ownership/grupo) — comportamento real do jogo, não bug da
ferramenta de teste.

Achado de implementação: ao testar o payload real de `CAST_SKILL`
descobri (e corrigi na hora) que meu próprio código do bot chamava
`find_path` com `manhattan_limit=100` arbitrário — rejeitava qualquer
travessia legítima de mapa grande. Corrigido pra usar a MESMA fórmula
que o client de verdade já usa em produção
(`ui/systems.py:1237`, `min(30000, max(8000, dist*80))` nós de A*
escalado pela distância) — sem isso, pontos de interesse reais a mais
de ~4 tiles de distância eram erroneamente marcados "inalcançável".

**Implementado** (`tools/loadtest/map_data.py`, novo — carrega
`map_1` OFFLINE, mesmo código do servidor, sem rede: lista de tiles de
arbusto REAIS via `TileType.is_bush`, pontos de interesse REAIS via
`spawn_points["spawn_zones"]` do mapa, e wrapper de `find_path` com o
orçamento de nós escalado por distância; `tools/loadtest/bot.py`):
- Loot: fila `_pending_loot` populada por `LOOT_AVAILABLE`, drenada no
  think loop com `LOOT_REQUEST {corpse_id, take: "all"}`.
- Skill à distância: `_engage()` agora consulta `SKILL_CATALOG[sid]
  ["cast_range"]` — skill com alcance real (ex.: Bola de Fogo=6) pode
  disparar sem precisar chegar adjacente; auto-attack continua exigindo
  adjacência (comportamento antigo preservado pra isso).
- Excursão de longa distância (mesmo mecanismo pra bush/POI/mob
  distante): `_start_goto()` computa o caminho numa THREAD
  (`run_in_executor`, nunca bloqueia o loop do PRÓPRIO swarm mesmo em
  cálculo de ~150ms), `_continue_goto()` segue o caminho tile a tile
  com watchdog (`_GOTO_STUCK_TIMEOUT_S=8s` sem avançar = desiste, nunca
  fica preso insistindo no mesmo destino). Prioridade: alvo de combate
  real > excursão em andamento > mob visto de longe (raio de 30 tiles,
  fora do raio de aggro curto) > chance pequena (5%+5%) de bush/POI
  aleatório > wander curto de sempre (caso comum, de propósito — nem
  todo bot devia estar sempre em excursão).

**Testado**: importação limpa, módulo `map_data` isolado (6175 tiles
de arbusto reais achados em `map_1`, 11 pontos de interesse únicos),
teste determinístico de goto (caminho real de 15 passos até um POI,
recuperação automática de 1 passo rejeitado), teste determinístico de
skill à distância (Bola de Fogo disparada a 4 tiles sem aproximar),
teste ao vivo com 3 bots (2,5min, 0 erros, loot confirmado via
`gold=10` real) e com 10 bots (4min, 0 erros, 1 bot claramente saiu do
aglomerado de spawn até (126,382), loot confirmado de novo).

**Limitação observada, corrigida no mesmo dia (06/09/2026)**: bots que
spawnam perto do boneco de treino (alvo sempre disponível, nunca
morre) ficavam "grudados" nele a maior parte do tempo — só ficavam
"ociosos" o suficiente pra sortear uma excursão ocasionalmente. Fix:
`_is_attackable()` agora ignora qualquer entidade com `is_dummy: True`
no payload de spawn (`WorldServer._build_mob_spawn_payload`, única
fonte usada por WORLD_STATE/AOI_UPDATE/ENTITY_SPAWN — nunca checar
nome/raça na mão). Teste ao vivo com 5 bots (150s, 0 erros) confirmou:
todos se espalharam de verdade pra faixa (120-136, 367-376), bem longe
do aglomerado antigo perto do spawn — evidência de combate real (1 bot
com hp=97) e loot (2 bots com gold=10).

**2º achado real, mesma sessão (06/09/2026) — skill de projétil (Bola
de Fogo/Flecha Reiterada/Picada de Escorpião) completava o cast
(consumia mana, aplicava cooldown, `SKILL_RESULT` chegava) mas NUNCA
causava dano**: usuário observou isso ao vivo assistindo um bot mago.
Causa raiz em código (`server/spell_completion_processor.py`): essas
skills só aplicam dano ao receber `PROJECTILE_HIT_CS` do cliente (a
confirmação de "o projétil visual colidiu") — sem isso a entrada em
`_spells_in_flight_queue` só expira sozinha 3s depois, sem efeito.
`_schedule_bot_projectile_autohit` já resolve isso pros bots de BG
reais (`BotControlled`, bug idêntico relatado 26/08/2026, ver auto-hit
já existente), mas o bot de load-test é um PLAYER DE REDE de verdade
(sem esse componente) — nunca era coberto por aquele mecanismo. Fix:
`tools/loadtest/bot.py` agora reage a `SKILL_RESULT` com
`projectile_target` presente mandando `PROJECTILE_HIT_CS` de volta
depois do tempo de voo calculado (distância × velocidade — reusa a
MESMA tabela `_BOT_PROJECTILE_SPEED_PX_S`/`_BOT_PROJECTILE_DEFAULT_
SPEED_PX_S` do servidor, importada direto, nunca reinventada), igual
`game.py` faz pro cliente real. Testado ao vivo (3 bots, 1 mago): Bola
de Fogo aplicando 7 de dano por acerto, HP do mob caindo em sequência
(43→36, 43→35→25) — confirmado via print de debug temporário no
payload `is_proj_damage`, removido depois de confirmar.

**Pendente** (fora do escopo desta sessão, decisão do usuário se
quiser): teste em escala maior (50-250 bots) combinando tudo, seguindo
o resto do plano de fases desenhado pelos 3 agentes (fases 1-2 e 5
já cobertas pelos testes de 3, 5 e 10 bots acima; fases de escala maior
e "todos os comportamentos ao mesmo tempo" ainda não rodadas).

## 29. IA de combate por classe — bot de teste de carga E bot de BG (08/09/2026)

Motivado por dois testes de carga reais nesta sessão (§124): com 10 e
depois 20 bots por 90s/10min, nenhum bot chegou a lutar de verdade
(`WANDER` nunca mudava, `mobs_ativos` do servidor ficava perto de 0) —
usuário pediu IA de combate de verdade pros bots, primeiro pro de teste
(`tools/loadtest/bot.py`), depois pediu explicitamente pra replicar o
mesmo tipo de inteligência pro bot real de BG (`server/bot_ai_system.py`).
Dois sistemas TOTALMENTE separados (um é cliente de rede externo, outro
é entidade ECS 100% servidor) — nenhum código compartilhado entre eles,
tratados como dois trabalhos distintos nesta seção.

### Causa raiz do "bot não anda direito" (loadtest) — teleporte/lentidão

Investigado ANTES de desenhar a IA de combate (usuário perguntou
separadamente "por que os bots teleportam/andam devagar"). Achado: o
bot só mandava 1 `MOVE` (1 tile) por ciclo de "think" inteiro, e o
think só rodava a cada `random.uniform(0.6, 1.8)` segundos — um jogador
real manda 1 `MOVE` por tile completo, a cada ~0,29s (`TILE_SIZE/110`,
mesma constante de fallback que `server/world_server.py::
PLAYER_SPEED_TILES_FALLBACK` usa). Resultado: bot andava 3-9x mais
devagar, E o hiato longo e irregular entre passos (nenhum MOVE real
pulava mais de 1 tile — confirmado lendo `_step_toward`/`_continue_goto`)
criava a ILUSÃO de teleporte pra quem olhava de relance (personagem
parado, depois "pulava" 1 tile de repente). Fix: `_move_loop` novo,
cadência própria (`_MOVE_STEP_INTERVAL_S`), separado do `_think_loop`
de decisão (que continua lento de propósito — só "pensar" precisa ser
raro, "andar" não).

### Bot de teste de carga (`tools/loadtest/bot.py`) — desenho aprovado

Levantamento prévio (Explore agent) confirmou mecânicas reais antes de
codar: Bola de Fogo (cast_range=6, não exige adjacência), Bloco de Gelo
(self-cast, imunidade+regen 5s), Polimorfia (cast_range=7, CD=0, só
limitada por mana/interrupção), Interceptar (dash 2-6 tiles, min E max
range — "muito perto" também rejeita), Vitória Iminente (CD=0 mas é uma
CARGA ganha ao MATAR um alvo — não cura garantida ao usar, só "não
guarda a carga com HP cheio"), arqueiro precisa de arco+aljava
equipados de verdade (senão `server/combat_processor.py` bloqueia o
auto-attack inteiro, "no_quiver_or_empty" — nunca deixa passar com
munição infinita), Picada de Escorpião/Tiro Repulsivo (kite real).

Implementado: `_hp_ratio()` (antes `STATS_UPDATE` era só `pass`'d — bot
nunca sabia o próprio HP); `_priority_skill()` por classe (guerreiro:
Interceptar ao entrar em alcance 1<dist≤6, Vitória Iminente se
HP<90%; mago: Bola de Fogo padrão, Bloco de Gelo/Polimorfia com
HP≤30%; arqueiro: Picada de Escorpião/Tiro Repulsivo pra abrir espaço);
kite real (`_combat_step`/`_step_away`) com HISTERESE (`_kiting` liga a
≤30% HP, só desliga a ≥50% — sem isso o bot oscilava fugir/lutar a cada
think bem na borda de 30%), estendida a TODAS as 3 classes (guerreiro
também foge agora — sem skill de escape, só corre mesmo; decisão
revertida do desenho original desta sessão, que tinha excluído
guerreiro por "sem ferramenta"). Bug pego e corrigido ANTES de declarar
pronto: `_combat_step` só invertia a direção (fugir) quando `dist <=
desired_range` — um alvo mais rápido que alcançasse o bot kitando de
FORA do alcance desejado fazia ele andar EM DIREÇÃO ao perseguidor (o
oposto do propósito); corrigido pra kite SEMPRE recuar, independente da
distância atual.

Busca ativa de mob: `_POI_EXCURSION_CHANCE` 0.05→0.15 (POI = zona de
spawn real de mob, `map_data.poi_points()`, não um ponto aleatório) +
`self.anchor` passa a "assentar" onde a excursão termina (não mais no
spawn original do player) — sem isso, um bot cujo spawn ficava longe de
qualquer zona de mob raramente decidia ir até uma, e o
`_WANDER_ANCHOR_DRIFT_MAX` de 10 tiles o puxava de volta pro spawn
inteiro toda vez que uma excursão terminava, mesmo bem-sucedida.
Confirmado por teste real (ver seção de validação abaixo):
`mobs_ativos` no servidor foi de ~0 (testes anteriores) pra 2-7 (pico 8)
sustentado nos 3 minutos de teste local pós-fix.

Arqueiro: `tools/loadtest/provision.py` ganhou `_grant_archer_equipment`
(Fase B nova, mesmo padrão de `_grant_starter_skills`) — grava
`hunter_bow`+`battleground_quiver` (999 flechas, mesma aljava vendida
na loja de BG) direto no `equipment_json` via `item_id` (schema mínimo
que `WorldServer._reconstruct_item` já resolve pro item real do
catálogo — nunca confia em stat vindo de fora).

### Bot de BG (`server/bot_ai_system.py`) — escopo bem maior que o esperado

Usuário pediu réplica do comportamento pro bot real de BG — investigação
prévia (Explore agent) achou a IA atual (v2, §69-75) MUITO mais simples
do que a do loadtest: sempre "1ª skill ofensiva pronta do hotbar",
zero lógica de HP próprio, zero recuo (docstring do módulo dizia
literalmente "sem recuar na v1, decisão explícita do usuário" —
perguntado, usuário esclareceu que foi ELE quem pediu isso antes, pra
desenvolver aos poucos, não uma decisão definitiva).

**Engano corrigido durante a investigação**: a 1ª pesquisa concluiu
"XP de instância nunca é concedido em jogo real, só em teste" (grep por
`grant_instance_xp(` não achou nada fora de `tests/`). Usuário
discordou, afirmando que bot E player sobem de nível de verdade dentro
da BG. Reconferido manualmente: existe sim um call site real,
`server/world_server.py:6029-6034` — `_death_handler.consume_xp()`
(abate de MINION) chama `grant_instance_xp` via um alias de import
(`grant_instance_xp as _grant_ixp`) que o grep original não pegou por
procurar o literal `grant_instance_xp(` com parêntese colado. Ou seja:
skills 2-6 de `INSTANCE_SKILL_UNLOCK_ORDER` já ficam disponíveis de
verdade ao longo de uma partida (bot e player), só faltava o bot saber
usar a que fizesse sentido pra situação — nenhuma progressão nova
precisou ser criada, ao contrário do que a 1ª pesquisa levou a crer.
Lição: **verificar contra o código atual antes de aceitar uma conclusão
de "não existe"/"nunca é chamado" baseada em busca textual — um alias
de import é o tipo de coisa que grep ingênuo perde.**

**Levantamento adicional** (2ª rodada de pesquisa) confirmou mecânicas
específicas antes de codar: Nova Congelante é AOE de verdade (raio =
`cast_range=3`, reaproveitado como raio de área em
`_server_nova_congelante`) — dano E `root` (`blocks_move=True` mas
SEM `blocks_act`, confirmado em `content/status_effects_data.py` — o
alvo enraizado não pode andar mas continua podendo atacar, então o
propósito real da skill pro mago é enraizar QUEM PERSEGUE, abrindo
espaço de fuga de verdade); `_dive_allowed` (`bot_ai_system.py:425-455`
antes desta sessão) só olhava o HP do ALVO, nunca do próprio bot;
`_find_nearest_enemy` só usava distância, sem noção de "espera o minion
ficar fraco"; `WorldServer.apply_consumable(session_id, payload)`
existe e é reaproveitável (mesmo padrão de `queue_skill`/`move_player`)
pra poção/comida, mas exige montar o payload a partir do catálogo (não
existe atalho `use_item_by_id` pronto) — ficou de fora do escopo desta
sessão por decisão do usuário (mais trabalho que o resto, confirmar
depois).

**Implementado** (`server/bot_ai_system.py`, `engine/components.py::
BotControlled.is_retreating` novo campo):
- **Histerese de fuga** (`BOT_LOW_HP_RETREAT_PCT=0.30`/
  `BOT_LOW_HP_REENGAGE_PCT=0.50`), mesmo princípio do loadtest bot,
  agora pras 3 classes de BG também — guerreiro corre em direção à
  PRÓPRIA BASE (reaproveita `_advance_toward` mirando `bot.route[0]`,
  a ponta da rota de lane mais perto do spawn — zero pathfinding novo,
  só um destino diferente do de sempre; fallback simples de "1 passo
  na direção oposta" só se `bot.route` ainda não existir, caso raro de
  HP cair antes do 1º `_push_lane`).
- **Discernimento tático por classe** (`_try_priority_skill`, chamado
  só quando NÃO retreating): guerreiro — Executar (alvo≤30% HP, dano
  3x) > Fatiador de Corpos (2+ inimigos num raio de 2, `_count_
  nearby_enemies` novo, reusa o mesmo padrão de scan de `_find_
  nearest_enemy`) > Vitória Iminente (HP próprio<90%) > Interceptar
  (1<dist≤6); mago — Nova Congelante (2+ inimigos num raio de 3,
  dano+enraíza — prioridade sobre Bola de Fogo porque é "ofensiva E
  defensiva ao mesmo tempo", pedido explícito do usuário) > Bola de
  Fogo padrão. Cada tentativa cai pro fallback genérico já existente
  (`_try_cast_offensive_skill`, "1ª pronta do hotbar") se a skill
  específica não estiver aprendida/pronta — nunca quebra o
  comportamento de skills sem regra tática própria (golpe_poderoso,
  flecha_reiterada, tiro_multiplo, etc.).
- **Skill defensiva ao fugir** (`_try_defensive_skill`, tentada 1x por
  decisão ANTES de `_retreat` — mesma ordem "skill > movimento" que o
  resto do arquivo já usa): mago tenta Nova Congelante (enraíza quem
  persegue) > Polimorfia > Bloco de Gelo; arqueiro tenta Picada de
  Escorpião (slow no perseguidor); guerreiro não tem ferramenta de
  escape real (Interceptar aproxima, Vitória Iminente exige
  adjacência) — sempre cai direto pra correr.
- **Minion só vira alvo perto de morrer** (`BOT_MINION_LAST_HIT_PCT
  =0.20`, `_find_nearest_enemy` reescrito): player inimigo SEMPRE
  vence minion agora (antes era só "o mais perto entre os dois");
  minion só entra na consideração com HP≤20% — garante o abate (XP)
  sem gastar hit/skill em minion saudável que a própria lane (minion
  aliado + torre) já está resolvendo sozinha.
- **Dive-gate com HP próprio** (`_dive_allowed` ganha `my_eid`):
  bloqueia SEMPRE que o HP do PRÓPRIO bot estiver ≤30%, antes de
  checar minion aliado perto ou alvo baixo — as duas condições
  antigas existem pra fechar KILL, nunca valem a pena se o bot morre
  pro dano da própria torre no caminho.

**Decisão de design não pedida explicitamente, mas necessária**:
alcance de engajamento (`_effective_engage_range`) MANTIDO dinâmico
por classe/arma (melee=1, mago=6, arqueiro=7-9 pelo arco real
equipado) em vez de fixar em 5 tiles como o usuário sugeriu como
simplificação possível — um valor fixo faria melee tentar atacar de
longe (nunca acerta) ou arqueiro parar antes da hora (desperdiça
alcance real do arco). Comunicado ao usuário, sem objeção.

### Testes

`tests/test_bg_bot.py`: 2 testes antigos de `TestBotAttacksMinion`
QUEBRARAM de propósito (testavam o comportamento ANTIGO — atacar
qualquer minion em alcance) — reescritos pra refletir o novo
last-hit (`_spawn_minion(hp_pct=...)` helper novo), mais 1 teste NOVO
confirmando que minion saudável é ignorado. 4 testes novos em
`TestBotDiveGate` (HP próprio baixo bloqueia mesmo com minion
aliado/alvo baixo presentes; regressão confirmando que HP próprio
saudável + alvo baixo continua permitido). 2 classes novas inteiras:
`TestBotRetreat` (liga/desliga histerese, foge de verdade do
atacante) e `TestBotPrioritySkills` (Executar/Fatiador/Nova
Congelante disparam nas condições certas, Fatiador NÃO dispara com só
1 alvo, mago fugindo tenta Polimorfia antes de só correr). Suíte
completa do arquivo: 50 passed (era 38 antes desta sessão). Suíte
COMPLETA do projeto: 1540 passed, 2 failed (mesmo timing flake
pré-existente de granularidade de timer do Windows já documentado no
§123, sem relação — confirmado rodando o teste isolado). Validação em
partida de BG real ainda pendente (usuário vai testar).

**Pendente**: poção/comida pro bot (decisão consciente de adiar,
precisa de kit inicial novo + tradução catálogo→payload que hoje só o
cliente real faz); TLS/wss (aberto desde §123).

## 30. Feature — instância de BG (fila real) destruída assim que não sobra jogador REAL (20/09/2026)

Investigação prévia (mesma sessão) mapeou o ciclo de vida completo:
`request_bg_leave()` (`server/bg_queue_processor.py`) é o único ponto
que tira alguém de uma partida de verdade — `/forfeit` (`_finish_bg_match`)
só decide o vencedor e manda a tela de resultado, NUNCA teleporta nem
destrói nada; a saída de fato só acontecia quando o critério antigo
batia: os 2 times (`team_a`+`team_b`) ficarem 100% vazios. Problema
real: se o único jogador humano desconecta/desloga/fecha o jogo no meio
da luta, `request_bg_leave` só remove ELE — os bots continuavam nos 2
times, então a instância ficava viva até um Nexus cair organicamente
(bot vs. bot, sem limite de tempo) + o timeout de resultado de 15s.

**Fix (pedido explícito do usuário):** critério novo — uma partida da
fila REAL só existe enquanto tiver pelo menos 1 jogador NÃO-bot em
qualquer um dos 2 times. `request_bg_leave` agora computa, depois de
remover `eid`, se sobrou algum ocupante sem `BotControlled` — se não
sobrou (e a flag abaixo estiver ligada), destrói a instância na hora,
mesmo com bots ainda presentes, em vez de esperar cada um sair
individualmente.

**Decisão de timing** (perguntado ao usuário antes de codar): `/ff`
continua em 2 fases — decide o vencedor e mostra o placar, sem destruir
nada na hora. O critério novo só entra em ação no momento em que o
jogador REALMENTE sai (clica "Voltar", o timeout de 15s força a saída
de todos, ou desconexão/logout dispara direto). Não mexe no fluxo de
2 fases que a Arena usa como referência.

**Boolean pedido pelo usuário** (`WorldServer.bg_destroy_when_no_real_players`,
default `True`) — válvula de escape pra teste que precisa inspecionar
uma partida só-de-bot ainda viva (comportamento antigo). `False`
desliga o critério novo, volta a exigir os 2 times 100% vazios.

**Bug real achado ESCREVENDO o teste desta mudança** (não suposição —
`test_sair_destroi_instancia_na_hora_mesmo_com_bots_presentes`
falhou na 1ª tentativa): a ordem inicial chamava `_unload_instance`
(que já remove a entidade ECS de todo mundo via varredura por
`MapLocation`) ANTES de percorrer os bots restantes pra despawná-los
individualmente (`_despawn_bot_player`, que limpa a sessão falsa +
`_player_eid_to_sid`/`_eid_to_sid` do `SessionManager` — bookkeeping
que `_unload_instance` não toca). Com a entidade já removida,
`world.get_component(bot_eid, BotControlled)` voltava `None` pra TODOS
os bots (não só 1 — `World.remove_entity` é síncrono, `_components.pop`
imediato), então a checagem "é bot?" falhava sempre e nenhum bot
recebia a limpeza de sessão — ficavam órfãos em `_player_eid_to_sid`
mesmo com a entidade ECS já apagada. Fix: despawnar os bots restantes
ANTES de chamar `_unload_instance`, não depois — mesma disciplina de
"varredura tolerante a alvo ausente" que já existe em outros pontos do
projeto, mas a ORDEM importa quando o dado necessário pra decidir QUEM
limpar (o componente `BotControlled`) só existe enquanto a entidade
ainda não foi removida.

**Teste**: `tests/test_bg_bot.py::TestBgLeaveDestroysWhenNoRealPlayerLeft`
(2 casos novos — comportamento novo default + flag desligada preserva o
antigo). Suíte completa relacionada (`bg`, `minion`, `instance_progression`,
`respawn`, `disconnect`): 314 passed, sem regressão.

---

## 31. Feature — câmera trava na borda do mapa + câmera livre estilo MOBA na BG (20/09/2026)

Duas mudanças relacionadas na mesma sessão, ambas 100% client-only (câmera
é cosmética, servidor nunca sabe pra onde o jogador está olhando).

**A — Clamp de borda.** Antes, perto do canto do mapa, a câmera
continuava centralizando o personagem, mostrando vazio além da borda em
vez de mais mapa. Fix no ponto único já existente pra "canto superior-
esquerdo do mundo visível em tela" — `engine/utils.py::get_camera_offset()`
(consolidado em 15/09/2026 de 13+ cópias duplicadas) — limita o offset
retornado entre `0` e `dimensão_do_mapa_em_px - dimensão_da_tela`, nos 2
eixos, lendo `Tilemap` via `World.get_first()` (seguro — cliente only,
1 Tilemap por vez). Vale tanto pra câmera travada no personagem quanto
pra câmera livre (item B abaixo), já que as duas passam pela mesma
função. Mapa menor que a tela num eixo (não deve acontecer — mapas são
desenhados maiores que a tela de propósito, confirmado com o usuário)
cairia num intervalo invertido; `max(0.0, ...)` no limite superior evita
o clamp explodir nesse caso, só não centraliza (aceitável, cenário que
não deveria existir de verdade).

**B — Câmera livre (tecla Y, só dentro da BG).** Mesmo mecanismo do
League of Legends, citado como referência explícita pelo usuário: tecla
destrava a câmera de seguir o personagem, clique ESQUERDO no minimapa
(tela-cheia da BG) pula a câmera pro ponto clicado, cursor perto da
borda da JANELA empurra a câmera naquela direção. Re-trava é 100%
manual (aperta Y de novo) — NUNCA volta sozinha ao mover o personagem,
decisão explícita do usuário (mesmo comportamento do LoL).

- `Camera.free_mode: bool` (novo campo, `engine/components.py`) —
  quando `True`, `CameraSystem.update()` (`ui/render_system.py`) para
  de seguir `target_entity_id` e chama `_pan_free_camera()` (cursor na
  borda) em vez disso. Margem/velocidade nomeadas
  (`FREE_CAM_EDGE_MARGIN_PX`/`FREE_CAM_PAN_SPEED`), fácil de recalibrar
  depois se o usuário testar e achar rápido/lento demais (mesmo padrão
  do `_OCCLUSION_COVERAGE_THRESHOLD` da transparência de objeto, §188).
- Margem de borda é medida em pixels de TELA (`hud_surf`, resolução real
  da janela) — não em `world_surf`/zoom, então a sensibilidade do
  "empurrão" não muda com o nível de zoom. Velocidade de pan é em
  pixels de MUNDO/segundo — também não afetada por zoom (câmera se move
  na mesma velocidade "real" independente do nível de zoom).
- `game.py`: tecla Y (novo `elif event.key == pygame.K_y`) alterna
  `free_mode`, só quando `InstanceInventoryUIState.active` (mesma flag
  já usada pro modo tela-cheia do minimapa — eco client-side de "estou
  numa instância"). Clique esquerdo no minimapa (mesmo bloco de evento
  que já trata o clique DIREITO = "andar até aqui", `screen_to_tile_fullmap`
  reaproveitado) só ativa quando `Camera.free_mode` já é `True` — nunca
  precisa checar `InstanceInventoryUIState` de novo ali, porque
  `free_mode` só liga através da tecla Y, que já exige estar na BG antes
  — checar só a flag da câmera é suficiente e evita duplicar a checagem.
- Botão direito (andar) e botão esquerdo (pular câmera) nunca colidem —
  filtram por `_ev.button` diferente no MESMO loop de eventos.

**Teste**: `tests/test_client_ui.py` — 3 casos novos de `CameraSystem`
(`free_mode` para de seguir alvo; empurra com cursor na borda; cursor no
meio da tela não move) + 3 casos novos de `get_camera_offset` (trava no
canto superior-esquerdo, trava no canto inferior-direito, sem clamp no
meio do mapa). **Cuidado replicado de uma lição já documentada nesta
sessão** (cache de mira do Tiro Múltiplo): os testes de cursor usam
monkeypatch de `pygame.mouse.get_pos`, NUNCA `pygame.mouse.set_pos()` —
`set_pos` depende da janela REAL do SDL (passa isolado, mas falha
dentro da suíte completa quando outro teste já redimensionou a janela
antes). Suíte relacionada (`render_system`, `minimap`, `camera`,
`tile_render`): 19 passed, mais os 181 de `test_client_ui.py` inteiro —
sem regressão.

---

## 32. Feature (infraestrutura, Fase 1) — dado de catálogo pra objeto emitir luz própria (lampião/tocha), base do sistema de dia/noite (21/09/2026)

**Pedido original do usuário:** sistema de dia/noite no jogo. Pygame não
tem iluminação nativa — proposta discutida e aprovada: overlay de cor
semi-transparente (`BLEND_MULT`) por cima do mundo renderizado, sem
tocar em gameplay/servidor (relógio calculado como função determinística
do tempo real, igual em todo cliente, sem mensagem de rede nova). Só
mundo aberto tem dia/noite (BG/Arena ficam num horário fixo — decisão do
usuário, evita a luz mudar no meio de uma partida competitiva). Ciclo:
60+ minutos reais por dia completo.

**Achado que mudou o escopo antes de desenhar o resto:** o usuário pediu
que a 1ª versão já incluísse luz de tocha/fogueira brilhando à noite.
Pesquisa no catálogo (`engine/tileset.py::OBJECT_SHEET_FAMILIES`) e nos
assets achou que **não existe nenhuma tocha/fogueira/lampião plantado em
mapa nenhum hoje** — só um item de quest chamado "Lampião"
(`content/quests_data.py`, sem lógica de luz) e 2 ÍCONES de inventário
(`item_lampiao.png`, `Torch.png`). A família "TX Props" que teria um
`lampiao`/`lighting` catalogado está inteira COMENTADA (nunca ativada em
produção, apesar do PNG existir). Reportado ao usuário antes de
prosseguir — decisão dele: em vez de fase-1-overlay/fase-2-luz-separadas,
construir agora a INFRAESTRUTURA de dado (onde fica a luz, intensidade,
raio) direto no catálogo, aplicada num objeto que JÁ existe e já parece
visualmente um lampião de vila (`vp_170`/`vp_171`, grupo
"decoracao_vila", família "TX Village Props with Shadow" — essa família
está ATIVA, diferente de "TX Props").

**Iteração de formato (usuário propôs, 3 rodadas de confirmação —
"se ficar dúvida me pergunte, não tire conclusões", seguido à risca):**
usuário deu um exemplo com posição em coordenada ABSOLUTA da folha de
sprites inteira (412, 1705) — perguntei se era coincidência bater com
sx+44/sy+41 do objeto (368, 1664); ele confirmou que o certo é relativo
(44, 41) e que descreveu errado na 1ª tentativa. Confirmado também: w/h
do bloco de luz (5, 6) = tamanho do pixel exato da chama (não colisão,
não vision_rect — só precisão visual de onde fica o glow); intensidade
0-100% (aplicada plena, só à noite — de dia a luz fica apagada, não faz
sentido ver brilho de tocha sob sol); raio em TILES (mesmo padrão de
`AOI_RADIUS`/etc. já usado no resto do projeto).

**Implementação (só o dado, sem consumidor ainda):**
- `TileType.light_source: tuple | None = None` (`engine/tileset.py`) —
  `(off_x, off_y, w, h, intensity_pct, radius_tiles)`, `off_x`/`off_y`
  RELATIVOS ao canto do sprite (mesmo espaço que `sx`/`sy` da entrada,
  nunca absoluto da folha inteira — ponto que gerou a 1ª rodada de
  dúvida acima).
- `_register_catalog_tiles()` ganha o 12º campo posicional da tupla
  (`entry[11]`), mesmo padrão já usado pelos campos opcionais anteriores
  (`piso`/`transpassavel`/`sort`/`vision_rect`/`vision_h`) — sem
  parâmetro nomeado novo, só mais uma posição opcional no fim.
- `vp_170` (linha ~1438) ganhou `(44, 41, 5, 6, 75, 3)` — confirmado por
  script headless que `OBJECT_MAPPING["vp_170"].light_source == (44, 41,
  5, 6, 75, 3)`. `vp_171` (sprite vizinho na mesma folha, possivelmente a
  mesma peça noutra variante) ficou SEM luz nesta rodada — usuário não
  deu a posição da chama pra ele, e não presumi que o mesmo offset
  relativo se aplica (sprites vizinhos na folha não necessariamente têm
  o elemento de luz no mesmo lugar interno) — fica pendente até o
  usuário confirmar/medir.

**Fora de escopo desta rodada, decisão consciente:** renderização da luz
(glow/radial gradient), o overlay de cor dia/noite em si, e o relógio
determinístico — nenhum desses tem consumidor implementado ainda. Este
item é só a fonte de dado (mesmo padrão de `is_bush`/`occludes_player`:
o dado nasce no catálogo primeiro, o sistema que o lê vem depois).

**Teste**: `py -3.10 -m pytest tests/ -k "tileset or object_sheet or
catalog or map_loader or entity_factory"` (53 passed) + suíte completa
de `test_client_ui.py` (182 passed) — sem regressão, campo é aditivo e
default `None` pra todo objeto que não o define.

---

## 33. Feature — sistema de dia/noite (overlay de cor + luz de objeto), consumidor real do dado de §193 (21/09/2026)

Continuação de §193, mesmo pedido do usuário. Design discutido e
aprovado em conversa (não em arquivo) antes de codar, seguindo
"pesquisar antes de codar": mapeado o pipeline de render inteiro em
`game.py` (tudo do mundo desenha em `self._zoom_surf`, HUD desenha
depois em tela nativa — mesmo ponto onde o grayscale de fantasma já se
aplica hoje) e o overlay de Fog of War (`TileRenderSystem.render_fog()`)
como precedente direto da técnica a reusar (Surface `SRCALPHA` cacheada,
blit simples por cima do mundo).

**Relógio (`ui/day_night_system.py`):** `day_night_fraction(now=None)`
— `(time.time() % DAY_NIGHT_CYCLE_S) / DAY_NIGHT_CYCLE_S`, função pura,
SEM estado/servidor/mensagem de rede — todo cliente calcula a MESMA
hora sozinho porque usa a mesma fórmula sobre o mesmo relógio de parede.
`DAY_NIGHT_CYCLE_S = 3600.0` (60 minutos reais por dia completo,
decisão do usuário). `day_night_color(now=None)` interpola entre 8
keyframes (fração→RGB+alpha) e é o ÚNICO ponto de verdade da cor/
intensidade do tingimento atual — tanto o overlay quanto a luz de
lampião leem dele (luz nunca tem sua própria tabela de "quando acender"
— ver abaixo).

**Overlay (`DayNightSystem.render()`):** Surface `SRCALPHA` do tamanho
de `world_surf`, preenchida com `day_night_color()` atual, blitada por
cima de tudo — chamada em `game.py` logo após `CHAT_BUBBLE.render()` e
ANTES do grayscale de fantasma (ordem testada e escolhida de propósito:
se fosse depois, o tingimento apareceria como uma cor estranha por cima
de um mundo já cinza; antes, o grayscale só reduz a saturação do
tingimento já aplicado, preservando a diferença de brilho dia/noite
mesmo como fantasma). `if a <= 0: return` logo no início — dia pleno
não aloca nem desenha nada, custo zero na maior parte do ciclo (2/3 do
tempo, keyframes 0.33-0.67).

**Escopo (`InstanceInventoryUIState.active`):** BG/Arena ficam sempre
num horário fixo — mesma flag client-side já usada pra câmera
livre/minimap tela-cheia (§190), reaproveitada aqui sem checagem nova.
Decisão do usuário: luz não deve mudar no meio de uma partida
competitiva.

**Luz de lampião "fura" o overlay, não desenha por cima:** em vez da
técnica aditiva óbvia (desenhar um brilho colorido por cima do
tingimento), a luz remove alpha do PRÓPRIO overlay de escuridão com
`BLEND_RGBA_SUB` — revela a cor normal do mundo por baixo, em vez de
somar luz artificial. Consequência elegante: de dia o overlay já tem
alpha=0 em todo canto, então subtrair não muda nada visualmente — a luz
"liga" sozinha conforme escurece, sem nenhum "if é noite" explícito.
**Achado de implementação (pego ANTES de rodar, por raciocínio, não por
bug ao vivo):** o gradiente radial usado no `BLEND_RGBA_SUB` precisa ter
RGB sempre `(0, 0, 0)` — só o canal alpha varia. `BLEND_RGBA_SUB`
subtrai TODOS os canais diretamente, sem ponderar pelo alpha do próprio
pixel (diferente de um blit normal) — um gradiente colorido (branco,
por exemplo) estouraria a subtração de R/G/B pra 0 em qualquer pixel com
alpha>0, virando um buraco preto sólido de bordas duras em vez de um
degradê suave. Com RGB=0, subtrair 0 de R/G/B é sempre no-op — só o
alpha do overlay é reduzido, gradualmente, exatamente como o gradiente
descreve.

**Posição da luz em tela** vem de `screen_x`/`screen_y` que
`TileRenderSystem.get_world_objects()` JÁ calcula (mesma lista usada pra
desenhar os objetos) — somados ao offset relativo `off_x`/`off_y` do
catálogo. Propositalmente NÃO recalcula offset de câmera à parte —
exatamente a lição do item #191 desta mesma sessão (duas cópias
esquecidas de `get_camera_offset()` quebraram clique de NPC perto da
borda do mapa). `get_world_objects()` ganhou 1 campo novo no dict
(`"light_source"`), mesmo padrão já usado por `"occludes_player"`.

**Cache:** gradiente radial calculado 1x por RAIO em pixels distinto
(poucos valores possíveis — `radius_tiles` do catálogo é inteiro
pequeno), `dict` de instância `_light_gradient_cache` — mesmo padrão de
`ui/floating_text.py::_outline_cache`. Overlay em si só é realocado
quando o tamanho de `world_surf` muda (zoom/resize), preenchido (`fill`)
de novo todo frame (barato, é só um `fill` de superfície).

**Wiring em `game.py`:** `DayNightSystem(world, player_entity)` — igual
`ShopSystem`/`TrainerSystem`, recebe `player_entity` porque precisa
consultar `InstanceInventoryUIState` DO player. Adicionado a
`_WORLD_SYSTEM_ATTRS` (refresh de `world_surf` todo frame,
`_assign_world_surf`) e à lista de `_rebuild_screen_refs` (mesmo padrão
de todo `System` novo — ver §192, cuidado de wiring de boot já
documentado ali).

**Teste**: 6 casos novos em `tests/test_client_ui.py` — cor em meio-dia
(alpha=0), meia-noite (alpha=150, RGB do keyframe), interpolação
intermediária, gate de instância (não desenha nada dentro de BG/Arena),
early-return de dia pleno (nem aloca a surface), e o caso principal —
luz de lampião revela a cor original EXATA no centro, aplica só o
tingimento cru longe da luz (valor de blend alpha calculado à mão e
comparado), com decaimento suave (não binário) num ponto intermediário.
Suíte completa (`pytest tests/ -q`, 1755 passed, 2 falhas pré-existentes
e não relacionadas em `test_perf_log_improvements.py`/`test_server.py`
— mesma classe de timing flaky de `time.sleep(0.01)` já documentada em
§191, não regressão desta mudança).

**Fora de escopo desta rodada:** luz aditiva por cima (deixar a área MAIS
clara que o dia normal, não só cancelar o escuro) — decisão consciente
do usuário de manter simples por enquanto, pode virar um "brilho quente"
por cima depois. `vp_171` está com posição de luz própria (16,41),
diferente do `vp_170` (44,41) — usuário confirmou que NÃO é o mesmo
offset por padrão (perguntei em vez de presumir).

---

## 34. Bug real — transição dia/noite passava por acinzentado + feature — aba de debug (F12) pra hora/fase (21/09/2026)

**Sintoma relatado:** usuário logou pra ver o efeito de §194 e viu a
tela "um pouco acizentada" em vez de escura — ficou sem saber se era
noite fechada, amanhecer ou entardecer.

**Causa raiz (bug real, não achado por acaso — diagnosticado amostrando
a função `day_night_color()` em vários pontos do ciclo):** os keyframes
originais tinham só 2 pontos por transição — noite `(20,25,60)` direto
pro dia/amanhecer `(255,140,70)`. Interpolar RGB em LINHA RETA entre
azul e laranja (cores quase opostas no círculo de cores) passa por um
meio-termo dessaturado no caminho: `t=0.5` dava `(137,82,65)` — um
"cinza sujo" acastanhado, nem azul nem laranja, exatamente o que o
usuário descreveu. Não é uma percepção errada do usuário; é uma
propriedade matemática de interpolar linearmente 2 cores distantes no
espaço RGB (o ponto médio de qualquer par de cores complementares/quase-
opostas tende a ficar próximo do cinza, já que R≈G≈B no meio do
caminho).

**Fix:** cadeia de keyframes intermediários em vez de 2 pontas —
noite→violeta→rosa/aurora→dourado→dia (e o espelho no entardecer). Cada
segmento consecutivo agora é um tom VIZINHO no círculo de cores (violeta
puxa pra rosa, rosa pra dourado), nunca 2 extremos opostos — elimina o
vale de saturação. Resultado amostrado (meio do amanhecer): antes
`(137,82,65)` acastanhado/cinza, depois `(198,109,128)` rosa/aurora de
verdade.

**Feature companion, mesmo pedido:** aba "Dia/Noite" nova no painel de
debug F12 (`client/debug_handlers.py::_draw_debug_tab_dianoite`) —
mostra fase atual (`current_phase_label()`) + % do ciclo, e 4 botões
("Meia-noite"/"Amanhecer"/"Meio-dia"/"Entardecer") que pulam o relógio
na hora. **100% local, NUNCA manda mensagem de rede** — diferente de
`_debug_levelup`/`_debug_add_gold`/`_debug_add_item` (que são
server-autoritativos via GM_*), dia/noite não tem conceito de "hora" no
servidor (é puramente cosmético, `ui/day_night_system.py`), então o
pulo só precisa mexer em estado do PRÓPRIO cliente.

**Mecanismo do pulo (`jump_to_fraction()`) — desloca, não congela:**
`_debug_time_offset_s` (global de módulo, só mutado via
`set_debug_time_offset()` — mesmo padrão de `_svc`/
`_lethal_interceptor` já documentado em CLAUDE.md "Proibido estado
mutável ad-hoc") é somado a `time.time()` antes do módulo de
`DAY_NIGHT_CYCLE_S` — pular pra "Amanhecer" calcula o deslocamento
necessário pra `day_night_fraction()` bater com o alvo NO INSTANTE do
clique, mas o relógio CONTINUA correndo normalmente dali em diante
(nunca fica travado no valor pulado). `day_night_fraction(now=<valor
explícito>)` (usado pelos testes) ignora esse offset de propósito — só
o relógio "de verdade" (`now=None`, chamado pelo jogo) passa por ele.

**Teste**: `test_day_night_transicao_nao_passa_por_cinza` — varre a
transição inteira de amanhecer em passos de 1%, falha se `max(r,g,b) -
min(r,g,b) < 25` em qualquer ponto com alpha visível (>=20). Revert-to-
confirm: rodado contra os keyframes ANTIGOS, falhou exatamente em
`fração 0.21` (RGB=54,41,61, diferença 20) — confirma que o teste prova
o bug relatado, não só um cenário sintético. Mais 2 testes novos:
`jump_to_fraction` avança o relógio sem congelar, e
`current_phase_label` bate com as faixas esperadas. Suíte completa
(`pytest tests/ -q`, 1759 passed, 1 falha pré-existente e não
relacionada — mesma classe de timing flaky já documentada em §191/§194).

---

## 35. Feature — cores-âncora do dia/noite editáveis ao vivo no F12, persistidas em config.json (21/09/2026)

Continuação de §194/§195, mesmo pedido do usuário: poder "escurecer
mais a noite"/"diminuir o efeito no amanhecer" sem editar código,
testando o resultado na hora. 3 decisões fechadas com o usuário antes de
codar (AskUserQuestion): (1) só as 3 cores-âncora (Noite/Amanhecer/
Entardecer) são editáveis, não os 12 pontos de transição da v1; (2)
edições persistem em `config.json` (sobrevivem reiniciar); (3) duração
do ciclo (60min) NÃO fica editável nesta rodada.

**Refatoração necessária pra "só 3 âncoras" funcionar de verdade:** a
tabela de 12 keyframes fixos de §195 tinha pontos INTERMEDIÁRIOS
hardcoded (violeta pré-amanhecer, dourado pós-amanhecer, etc.) que NÃO
eram deriváveis das 3 âncoras (eram tons escolhidos à mão,
independentes). Se a UI só deixasse editar 3 pontos e os outros 9
continuassem fixos, mudar a âncora do amanhecer não mudaria a
transição toda — a "violeta" fixa ficaria destoante de um amanhecer
escolhido pra outra cor. Fix: `_build_keyframes()` (função, não mais
lista estática) gera os 8 pontos necessários TODOS a partir das 3
âncoras (`night`/`dawn`/`dusk`, cada uma com cor RGB + alpha) — a
transição inteira (não só o pico) reage a uma mudança de âncora.
Reduzido de 12 pra 8 pontos (removidos os hard-coded violeta/dourado
intermediários) — verificado que a versão simplificada AINDA evita o
acinzentado de §195 com os valores-padrão (amostrado por script:
nenhum ponto da transição cai abaixo do limiar de saturação do teste de
regressão).

**Achado ao escrever o teste de regressão pra versão simplificada:**
o ponto "sem tingimento" (fim da transição de amanhecer/início da de
entardecer) usava `(0,0,0)` como RGB com alpha caindo pra 0 — como
RGB E alpha caem JUNTOS até esse ponto, a cor visitava um tom
"escurecendo pro preto" logo antes de desaparecer (não é o mesmo bug
de §195 — não é um acinzentado de transição opondo 2 matizes, é um
apagão pro preto no finalzinho do fade). Corrigido usando a cor da
PRÓPRIA âncora vizinha (amanhecer/entardecer) no ponto "limpo", só com
alpha=0 — sumir de vista não deveria mudar a matiz do que está
sumindo. Sem efeito visual real (alpha já baixíssimo nesse trecho), mas
o teste de regressão pegou porque a diferença R/G/B ainda contava.

**API nova (`ui/day_night_system.py`):** `set_night`/`set_dawn`/
`set_dusk(color=None, alpha=None)` (clampam 0-255, mutam só o que for
passado), `get_anchor_config()`/`set_anchor_config(dict)` (snapshot pra
salvar/carregar), `reset_anchors_to_default()`. Estado em globais de
módulo (`_night_color`/`_dawn_color`/`_dusk_color` + alphas) — mesmo
padrão de estado plugável já usado (`_debug_time_offset_s` de §195,
`_svc`/`_lethal_interceptor` em `engine/core_systems.py`).

**UI (`client/debug_handlers.py::_draw_debug_tab_dianoite`):** 3 linhas
(Noite/Amanhecer/Entardecer), cada uma com 4 canais (R/G/B/Transparência)
e botões `-10`/`+10` por canal (mesmo padrão de "denominação grosseira"
já usado pelos botões de ouro — não precisa de precisão de 1 em 1 pra
testar atmosfera) + swatch de cor ao lado. Botão "Resetar padrão"
restaura os 3 defaults do módulo. Clique em qualquer botão chama
`GameEngine._debug_adjust_daynight_channel()` (novo, `client/
debug_handlers.py`), que aplica o delta E already persiste
(`self._save_config()`) — sem esperar um autosave periódico.

**Persistência:** `GameEngine._save_config()` ganhou a chave
`"daynight"` (`get_anchor_config()`); no boot, `set_anchor_config(
_cfg_data.get("daynight", {}))` — chave ausente (save antigo, 1ª vez)
mantém os defaults do módulo, sem quebrar nada. **100% local em todo o
fluxo** — nenhuma mensagem de rede nova, dia/noite não existe no
servidor (mesma razão de `jump_to_fraction` em §195), diferente de
`_debug_levelup`/`_debug_add_gold`/`_debug_add_item` (que SÃO
server-autoritativos via GM_*).

**Teste**: 4 casos novos — âncora editada reflete em `day_night_color()`
na hora; round-trip `get_anchor_config()`/`set_anchor_config()`; chave
ausente cai no default sem quebrar; clamp 0-255 nos 2 extremos. Suíte
completa (`pytest tests/ -q`, 1762 passed, 2 falhas pré-existentes e não
relacionadas — mesma classe de timing flaky de §191/§195).

---

## 36. Feature — slider com trilha em gradiente no F12, substitui os 4 botões fixos de pular hora (22/09/2026)

Continuação de §194-197, mesmo pedido do usuário: poder ver a curva de
cor do ciclo inteiro (não só 4 pontos fixos) e arrastar pra qualquer
momento exato. Decisão fechada com o usuário antes de codar
(AskUserQuestion): os 4 botões fixos (Meia-noite/Amanhecer/Meio-dia/
Entardecer) saem, substituídos pelo slider — arrastar já cobre esses 4
pontos e qualquer outro, sem redundância nem apertar mais o painel
(que acabou de ter um bug de estouro de largura corrigido, §197).

**Trilha em gradiente, não barra genérica:** em vez de uma barra cinza
com uma bolinha, a TRILHA do slider é pintada amostrando
`day_night_color()` em 60 pontos ao longo do ciclo — o usuário vê a
curva inteira (noite→amanhecer→dia→entardecer→noite) de relance, sem
precisar arrastar nada. Cada amostra é composta contra um cinza neutro
fixo `(45,42,36)` só pra o canal alpha (que controla "quão visível" é
o tingimento) ficar perceptível na prévia — é uma aproximação visual,
não uma simulação exata do resultado final em jogo (que compõe contra
o MUNDO renderizado, não contra cinza).

**Drag reaproveitando um padrão já existente:** mesma técnica do slider
de quantidade do trade/loja (`client/trade_handlers.py::
_handle_trade_qty_modal_events`, já em produção) — clique/arraste
calcula `ratio = (mouse_x - slider_x) / slider_width` e aplica. Só que
aqui o "aplicar" é `jump_to_fraction(ratio)` (§195) em vez de mutar uma
quantidade local — mesmo 100%-local-sem-rede de sempre (dia/noite não
existe no servidor).

**Robustez adicionada que o precedente (qty modal) não precisava:** o
qty modal é um dict recriado do zero toda vez que abre
(`dragging: False` sempre fresco); o slider do F12 usa um atributo
PERSISTENTE de `GameEngine` (`_debug_dianoite_dragging`), que sobrevive
fechar/reabrir o painel. Sem cuidado extra, soltar o botão do mouse
FORA da janela (sem gerar `MOUSEBUTTONUP`) ou fechar o painel no meio
de um arraste deixaria essa flag travada em `True` pro resto da sessão
— o próximo `MOUSEMOTION` qualquer (mesmo sem clique nenhum) recomeçaria
a arrastar sozinho. Fix: o branch de `MOUSEMOTION` confirma
`event.buttons[0]` (pygame informa quais botões estão fisicamente
presos em CADA evento de movimento) antes de aplicar — se o botão não
está mais preso de verdade, a flag se autocorrige na hora, mesmo sem
nenhum `MOUSEBUTTONUP` ter chegado.

**Geometria compartilhada, nunca cacheada:** `_debug_dianoite_slider_rect()`
é chamada tanto pelo desenho quanto pelo handler de clique/arraste,
recalculada do zero em cada chamada (mesmo padrão de
`_trade_qty_modal_rect()`) — nunca lida de um `pygame.Rect` guardado de
um frame de render anterior, que arriscaria ficar 1 frame desatualizado
dependendo da ordem exata evento→render do loop principal.

**Teste**: sem teste automatizado novo pro slider em si (mesmo caso de
§195-197 — UI de debug do F12 não tem precedente de teste dedicado,
validado manualmente pelo usuário; a lógica de fundo que o slider aciona,
`jump_to_fraction()`, já tinha teste próprio desde §195, intocado aqui).
Suíte completa (`pytest tests/ -q`) rodada sem regressão.

---

## 37. Feature — cores ajustadas no F12 viram o padrão de verdade do jogo (22/09/2026)

Continuação de §196-198. Usuário validou em jogo os valores ajustados
ao vivo no editor de âncoras do F12 e perguntou como fazer isso "valer
pra build do executável" — achado importante: a config do F12
(`config.json`, local, do lado de onde o jogo roda a partir do
código-fonte) NUNCA chega ao executável empacotado, porque
`release_tools/build_client.ps1` SOBRESCREVE `dist/RPG_Online/
config.json` com um arquivo novo contendo só `server_host`/
`server_port` — qualquer coisa ajustada via F12 fica presa no ambiente
de dev, nunca chega em quem baixa o instalador.

**Decisão do usuário (2 opções apresentadas, ver AskUserQuestion):**
os valores viram o PADRÃO DE VERDADE do jogo — atualizados direto nas
constantes `DEFAULT_NIGHT_COLOR`/`DEFAULT_NIGHT_ALPHA`/
`DEFAULT_DAWN_COLOR`/`DEFAULT_DAWN_ALPHA`/`DEFAULT_DUSK_COLOR`/
`DEFAULT_DUSK_ALPHA` (`ui/day_night_system.py`) — em vez da alternativa
descartada (fazer o script de build copiar o `config.json` local, que
ficaria sendo uma config pessoal do usuário, não o padrão do jogo, e
precisaria repetir o passo a cada build futura). Valores novos (lidos
do `config.json` local do usuário, já validados em jogo):

| | Antes (§196) | Agora (padrão) |
|---|---|---|
| Noite (RGB, alpha) | (20,25,60), 150 | (10,5,30), 230 |
| Amanhecer (RGB, alpha) | (190,100,130), 95 | (190,100,130), 15 |
| Entardecer (RGB, alpha) | (220,90,110), 95 | (220,90,110), 15 |

Noite ficou mais escura e mais azulada; amanhecer/entardecer mantiveram
a MESMA cor (rosa/aurora), só bem mais sutis (alpha 95→15) — efeito bem
mais discreto na transição, a noite carregando o peso visual principal.

**Efeito colateral esperado, não um bug**: botão "Resetar padrão" do
F12 (§196) agora volta pra ESTES valores, não mais os da v1 — é
literalmente o que "padrão" deveria significar a partir de agora.
`config.json` local do usuário continua com a chave `"daynight"`
explícita (agora redundante, já que bate com o novo default) — não foi
limpa de propósito, sem efeito prático (`set_anchor_config()` aplicaria
os mesmos valores de qualquer forma).

**Teste**: `test_day_night_color_meia_noite_tingimento_maximo`
(§196/197) tinha os valores ANTIGOS hardcoded como literais — corrigido
pra importar `DEFAULT_NIGHT_COLOR`/`DEFAULT_NIGHT_ALPHA` em vez de
repetir os números (nunca mais fica obsoleto se o padrão mudar de novo).
Os outros 12 testes de dia/noite (incluindo o de regressão do
acinzentado, §195/§197) passaram sem alteração — confirma que a lógica
de transição continua correta mesmo com os novos valores de alpha bem
mais baixos no amanhecer/entardecer. Suíte completa de
`test_client_ui.py`: 195 passed.

---

## 38. Feature — luz de lampião binária (só na fase Noite) + camada de sprite "aceso" a partir de folha companheira (22/09/2026)

Continuação de §193-200, mesmo pedido do usuário: (1) desligar a luz do
lampião fora da noite de propósito, não por coincidência de alpha; (2)
objeto de mapa (lampião) mudar de aparência à noite — uma 2ª imagem
sobrepondo a 1ª.

**Pesquisa antes de codar (achado real que mudou o desenho):** o
usuário descreveu a ideia de "criar uma camada" — antes de propor
qualquer campo novo no catálogo, verifiquei o asset que ele já tinha
exportado (`assets/tiles/TX Village Props Iight.png`, criado no mesmo
dia): MESMO canvas 2048×2048 da folha base
(`TX Village Props with Shadow.png`), com arte SÓ nas mesmas posições
`(sx,sy,w,h)` de alguns objetos — inclusive, confirmado por crop
programático (`PIL`) da região exata do `vp_170`/`vp_171`, a lanterna
ACESA exatamente sobreposta à posição da lanterna apagada da folha
base. Esse achado eliminou a necessidade de QUALQUER campo novo por
tile no catálogo — a "camada" já existe pronta como arquivo, só falta
o mecanismo de runtime saber que ela existe e buscar a MESMA posição.

**Achado de escopo (evitou ativar coisa errada):** a folha nova tem
MAIS conteúdo pintado além do lampião — o usuário confirmou que é arte
reservada pra "outras interações com objetos do mapa" ainda não
implementadas. Varredura por pixel não-transparente (`numpy`) achou
pelo menos 3 outros clusters de conteúdo na folha, fora da região do
lampião. Decisão: o mecanismo só ativa a camada noturna pra objetos
que JÁ têm `TileType.light_source` marcado (hoje só `vp_170`/`vp_171`)
— nunca pra "qualquer tile que a folha companheira tenha algo
pintado". Quando as próximas interações forem implementadas, cada uma
ativa sua própria parte da folha do jeito que fizer sentido pra ela —
não é escopo deste item.

**Nome do arquivo corrigido antes de referenciar no código**: o export
saiu como "TX Village Props **I**ight.png" (I maiúsculo em vez de l
minúsculo) — usuário confirmou que foi sem querer, renomeado pra
"TX Village Props Light.png" (`git mv`) antes de qualquer código
apontar pra ele.

**Mecanismo (3 peças, cada uma um ponto único de verdade):**

1. `ui/day_night_system.py::is_night(now=None) -> bool` — `True` só
   quando `current_phase_label(now) == "Noite"`. Corte BINÁRIO,
   diferente do tingimento ambiente (`day_night_color()`), que continua
   suave/gradual — decisão do usuário: só os efeitos de LUZ (buraco no
   escurecimento + camada de sprite) são liga/desliga, não o tingimento
   em si. `DayNightSystem.render()`'s loop de furar o escurecimento
   agora é `if world_objects and is_night():` em vez de rodar sempre
   que `alpha>0` — antes, com os novos valores de alpha baixo no
   amanhecer/entardecer (§200), a luz já ficava quase invisível por
   coincidência; agora é uma regra de verdade.
2. `engine/tileset.py::NIGHT_SHEET_BY_FILE` — dict de módulo
   (`{sheet_file_base: sheet_file_noturno}`), 1 entrada hoje
   (`"TX Village Props with Shadow" → "TX Village Props Light"`).
   Propriedade da FOLHA inteira, nunca por tile — evita repetir a
   associação em cada objeto que ganhar uma folha companheira no
   futuro.
3. `ui/tile_sprite_manager.py::TileSpriteManager.get_night_overlay_sprite(name)`
   — reusa `_get_sheet_region()` (mesmo cache já usado por
   `get_raw_sprite()`) com o MESMO `(sx,sy,w,h)` do tile, só trocando o
   `sheet_file` pela folha companheira. `None` se a folha base não tem
   companheira cadastrada. Este método NÃO gateia por `light_source` —
   só resolve "existe alguma coisa pintada aqui na folha noturna",
   devolve mesmo pra tile sem light_source (`vp_186`/pedras, testado
   explicitamente) — o gate real é 1 nível acima.

**Gate real + wiring**: `ui/tile_render_system.py::get_world_objects()`
só chama `get_night_overlay_sprite()` quando `tile_type.light_source`
existe — resultado (`Surface | None`) entra no dict do objeto como
`"night_overlay_sprite"`, mesma posição/tamanho do sprite base (nunca
recalcula âncora à parte). `ui/render_system.py::RenderSystem.render()`
desenha essa Surface por cima do sprite normal (nunca substitui) só
quando `is_night()` — 1 `import` de módulo (`from ui.day_night_system
import is_night`), não é chamada de System pra System (é função pura,
mesmo padrão já usado por `day_night_color`/`day_night_fraction`
alhures).

**Teste**: 6 casos novos — `is_night()` bate com os limites esperados
das 5 fases; luz de lampião NÃO fura o escurecimento fora da noite
(revert-to-confirm feito: comentando o gate de propósito, o teste falha
exatamente como esperado); `get_night_overlay_sprite("vp_170")` com
catálogo/assets REAIS (não mock) devolve uma Surface 80×96 com pixels
de verdade; folha sem companheira (`gcn_1`) e nome inexistente devolvem
`None`; `get_world_objects()` com um mapa sintético plantando `vp_170`
(light_source) e `vp_186` (mesma folha, SEM light_source) lado a lado
confirma que só o primeiro ganha `night_overlay_sprite` — o gate real
funciona ponta a ponta, não só na função de baixo nível. Suíte completa
(`pytest tests/ -q`, 1768 passed, 1 falha pré-existente e não
relacionada — mesma classe de timing flaky já documentada).

---

## 39. Reescrita — técnica de escurecimento trocada de transparência pra MULTIPLICAÇÃO de cor + brilho quente aditivo (22/09/2026)

Pedido do usuário, chegou como uma especificação já bem detalhada
("Instruções para Implementação"). Seguindo a regra do projeto de
verificar recomendação de terceiro contra o código/comportamento real
antes de aceitar — a ideia central (trocar `alpha blending` por
`BLEND_RGBA_MULT`) é sólida e foi aceita, mas achei e corrigi 1
inconsistência técnica real na especificação ANTES de escrever
qualquer código, e 1 achado adicional só depois de verificar
empiricamente.

**Achado #1 — `BLEND_RGBA_SUB` não funciona mais, precisa ser
`BLEND_RGBA_ADD`:** a especificação pedia pra MANTER `BLEND_RGBA_SUB`
pra "limpar" a escuridão onde tem luz. Isso fazia sentido na técnica
ANTIGA (a camada começava em alpha=0/"nada", subtrair não tinha efeito
visível). Na técnica NOVA, a camada começa ESCURA de propósito (ex.:
`(30,40,80)` de noite) — **subtrair** de um valor já escuro só deixa
mais escuro ainda (tende a preto), o oposto do que se quer (clarear de
volta perto do branco). A operação certa é **somar**
(`BLEND_RGBA_ADD`). Usuário confirmou a correção antes de eu tocar em
qualquer arquivo.

**Achado #2 — `Surface.set_alpha()` não pondera `BLEND_RGBA_ADD` (nem
`SUB`), verificado empiricamente antes de escrever o código real**
(`scratchpad/verify_blend.py`, não fica no repo — script de
investigação): testei `set_alpha(128)` numa Surface e usei ela com
`BLEND_RGBA_ADD` — resultado IDÊNTICO ao caso sem `set_alpha()`
nenhum. Confirma e generaliza a MESMA lição já documentada em §194 pro
`BLEND_RGBA_SUB` (blends "crus" da família RGBA_ADD/SUB/MULT/MIN/MAX
não são ponderados por alpha de superfície) — pra `intensity_pct`
continuar controlando a força da luz, precisa estar "assado" nos
PRÓPRIOS valores RGB do gradiente no momento em que ele é gerado, não
aplicado depois via `set_alpha()`. Por causa disso, o cache do
gradiente aditivo mudou de chave — de só `radius_px` (v1, §194) pra
`(radius_px, intensity_pct)` — poucas combinações reais existem no
catálogo hoje, sem risco de crescer sem controle.

**Mecanismo novo (`ui/day_night_system.py`):**
- `day_night_color()` continua devolvendo um RGBA de 4 valores (mesma
  assinatura, evita quebrar quem já chama), mas a SEMÂNTICA mudou:
  agora é um **fator multiplicador** — `(255,255,255)` = identidade
  (sem efeito nenhum), quanto mais escuro, mais a cena escurece. O 4º
  valor (alpha) virou **VESTIGIAL** — por decisão EXPLÍCITA do usuário
  ("manter mesmo sem uso real"), continua existindo, editável no F12,
  persistido em `config.json`, só que NENHUM cálculo em
  `day_night_color()`/`DayNightSystem.render()` o consome mais.
- `DEFAULT_NIGHT_COLOR = (30,40,80)` e o platô de dia
  `_MULT_IDENTITY = (255,255,255)` vieram de valor EXATO dado pelo
  usuário. `DEFAULT_DAWN_COLOR`/`DEFAULT_DUSK_COLOR` (`(255,205,190)`/
  `(255,190,175)`) são palpite inicial meu — usuário decidiu ajustar ao
  vivo no F12 depois, mesmo fluxo já usado em §196/§200.
- `DayNightSystem._overlay_surf` deixou de usar `pygame.SRCALPHA`
  (técnica de multiplicação precisa dos canais RGB cheios, sem
  propósito nenhum pro canal alpha aqui). Early-return de "dia pleno"
  trocou de `if a <= 0` pra `if (r,g,b) == _MULT_IDENTITY`.
- `_get_light_add_gradient(radius_px, intensity_pct)` substituiu
  `_get_light_gradient(radius_px)` — branco no centro (ESCALADO pela
  intensidade, achado #2 acima) decaindo até preto na borda, usado com
  `BLEND_RGBA_ADD` sobre a `camada_noite`.
- Blit final: `world_surf.blit(overlay, (0,0), special_flags=
  pygame.BLEND_RGBA_MULT)`.
- **Brilho quente (adição nova, pedido explícito do usuário)** —
  `_get_warm_glow_gradient(radius_px)`: círculo cor `(255,200,100)`,
  opacidade BAIXA (pico 90/255), desenhado DEPOIS do blit de
  multiplicação, DIRETO no `world_surf` (não na `camada_noite`) — blend
  NORMAL (sem `special_flags`), que É ponderado por `set_alpha()` de
  verdade (diferente do gradiente aditivo acima) — por isso intensidade
  é aplicada no BLIT (`.set_alpha()`), sem precisar de chave composta
  no cache.

**Teste**: 3 dos testes já existentes (§195-198) quebraram — corrigidos
refletindo o comportamento NOVO, verificado com script isolado antes de
mexer no teste (não assumido):
1. `test_day_night_render_dia_pleno_nao_desenha` — mock mudou de
   `(0,0,0,0)` pra `(255,255,255,0)` (identidade nova).
2. `test_day_night_transicao_nao_estoura_faixa_das_ancoras` (renomeado,
   era `..._nao_passa_por_cinza`) — o teste ORIGINAL checava uma
   saturação mínima fixa; com multiplicação, essa classe de bug fica
   estruturalmente muito mais fraca (uma cor multiplicadora
   "acinzentada" só escurece de forma neutra, não pinta um wash feio
   como a transparência fazia) E as âncoras agora são editáveis ao vivo
   pelo usuário — uma trava numérica fixa quebraria pra qualquer
   combinação de âncoras válida escolhida por ELE, sem ser bug de
   verdade. Reescrito pro invariante que TEM que valer sempre,
   estrutural: cada canal interpolado nunca sai da faixa das 2 pontas
   do segmento (prova que a interpolação está correta, não uma opinião
   sobre "quão colorida" a transição fica).
3. `test_day_night_luz_de_lampiao_fura_o_escurecimento` — reescrito com
   números conferidos por script (`center_px` não é mais EXATAMENTE
   `base_color`, porque intensidade 75%<100% não clareia 100% de volta
   E o brilho quente soma um viés laranja por cima de propósito) — novo
   teste verifica decaimento monotônico centro→borda em cada canal E
   que o canal R ganha mais que o B perto da luz (prova o viés laranja
   do brilho quente).

Suíte completa (`pytest tests/ -q`, 1768 passed, 1 falha pré-existente
e não relacionada — mesma classe de timing flaky já documentada).

**Também corrigido, achado na revisão final antes de fechar**: a
trilha em gradiente do slider do F12 (§198) ainda simulava a técnica
ANTIGA (mistura por transparência contra um cinza ESCURO de
referência) — com a semântica nova, isso desenharia a prévia inteira
quase preta (multiplicar um cinza escuro por qualquer coisa continua
escuro) e ainda dependeria do alpha vestigial. Corrigido pra simular
MULTIPLICAÇÃO de verdade contra um cinza CLARO de referência (deixa a
curva de cor visível), ignorando o alpha.

## 40. Feature — corte de luz de lampião ancorado em minuto exato do ciclo (23/09/2026)

Pedido do usuário: luz dos postes (o corte binário de is_night(), ver
§201/§203) devia apagar no minuto 12 e acender no minuto 49 do ciclo de
60 minutos reais — valores diferentes dos ~10.2/~49.8 que
_PHASE_LABELS (ui/day_night_system.py) produzia antes (arredondado de
0.17/0.83 de fração).

is_night() já delegava 100% pra current_phase_label(now) == "Noite"
(nenhuma lógica própria) — a mudança certa era só onde a fase "Noite"
começa/termina em _PHASE_LABELS, não criar um 2º mecanismo paralelo.
Trocado o par fixo (0.17, 0.83) por _light_off_frac/_light_on_frac,
derivados de _LIGHT_OFF_MINUTE=12.0/_LIGHT_ON_MINUTE=49.0 sobre
DAY_NIGHT_CYCLE_S (nunca um número de fração mágico solto no código — a
intenção do usuário era em MINUTOS, não fração). Só o início de
"Amanhecer" e o fim de "Entardecer" (os 2 vizinhos de "Noite") se movem
pra abrir espaço — "Dia" e o resto de Amanhecer/Entardecer ficam
intocados, sem necessidade.

**Achado, não corrigido antes, ajustado de graça no mesmo commit por
estar bem ao lado da linha editada**: o comentário acima de
_PHASE_LABELS dizia "nunca consultado pelo overlay/luz (que só leem
day_night_color())" — falso desde que is_night() passou a existir em
§201 (ele CONSULTA current_phase_label, que lê _PHASE_LABELS). Não
afeta comportamento (só doc), mas é exatamente o tipo de
doc-mentindo-sobre-código que a auditoria de 06/08/2026 pede pra
registrar — corrigido no comentário do módulo.

O tingimento AMBIENTE (day_night_color(), gradiente suave de cor) usa
keyframes PRÓPRIOS (_build_keyframes()), totalmente independentes de
_PHASE_LABELS — não mudou nada aqui, só a luz binária do lampião.

**Teste**: test_is_night_so_true_na_fase_noite/
test_day_night_current_phase_label continuam passando sem alteração
(os fracs de amostra usados, 0.23/0.77/0.90, continuam caindo nas
mesmas fases após o reposicionamento) — confirmado por script isolado
varrendo minuto a minuto (0, 11, 11.9, 12, 12.1, 48.9, 49, 49.1) antes
de considerar fechado: corte exato em 12 e 49, nenhum "quase". Suíte de
tests/test_client_ui.py completa: 200 passed.

## 41. Feature — relógio visual do ciclo dia/noite no HUD do jogador (23/09/2026)

Pedido do usuário: uma noção visual de "quando a noite está chegando"
sempre visível na tela, sem ser um relógio numérico comum — reaproveitar
a MESMA forma do slider de debug do F12 (trilha em gradiente, ver §198),
só que simplificada pro jogador comum entender de relance.

Discutido ANTES de codar (2 perguntas via AskUserQuestion, respostas do
usuário 23/09/2026): (1) incluir um texto pequeno da fase atual do lado
da trilha (ex: "Noite") — SIM; (2) desenhar ícone de sol/lua nas
pontas/centro pra marcar meio-dia/meia-noite — NÃO, sem asset pronto no
jogo, a cor sozinha (escuro=noite, claro=dia) já é intuitiva.

**Levantamento de layout ANTES de decidir posição**: topo-esquerda já é
HP/mana/fúria (`client/hud_handlers.py`), topo-direita já é zona/
coordenadas + minimapa + buff bar + tracker de quest (`ui/ui_sizes.py`).
Topo-centro estava livre — único lugar sem conflito.

**Implementação** — `ui/day_night_clock_hud.py::DayNightClockHud`
(classe nova, `UIScaleMixin`, mesmo padrão de `BuffBar`/`Minimap`):
- Trilha fina (160×8px base) topo-centro, amostra `day_night_color()`
  em 24 pontos fixos do ciclo inteiro (0.0 a 1.0, SEMPRE a curva
  completa, independente da hora atual) e simula MULTIPLICAÇÃO contra
  um cinza CLARO de referência — EXATAMENTE a mesma técnica de
  `_draw_debug_tab_dianoite` (§198/§203), só que 24 amostras em vez de
  60 (trilha bem menor, mais amostras seria custo sem ganho) e SEM
  interação de arraste (`rect()`/`render()` — sem
  `_handle_..._events()` companheiro, o HUD normal nunca deixa o
  jogador "viajar no tempo").
- Marcador (linha branca vertical) na posição X correspondente à
  FRAÇÃO ATUAL (`day_night_fraction()`, hora real) — esse sim reage ao
  relógio de verdade, diferente da trilha (sempre a curva completa).
  Rótulo de fase (`current_phase_label()`) centralizado embaixo.
- Some inteiro (return antecipado) quando
  `InstanceInventoryUIState.active` — MESMO campo/mesma regra que
  `DayNightSystem.render()` (§201/§203) já usa pro tingimento ambiente:
  dia/noite é cosmético só de mundo aberto, nunca aparece em BG/Arena.
- `ui/ui_sizes.py`: `DAYNIGHT_CLOCK_W/H/MARGIN_TOP` (160/8/10) — mesmo
  ponto único de tamanhos "de design" que todo outro painel/HUD usa.
- `game.py`: instanciado junto de `_day_night_system`; registrado nas
  DUAS listas de propagação de `set_ui_scale` (push inicial + troca ao
  vivo no menu de pausa — mesmo padrão de todo outro painel fora de
  GameEngine); chamado dentro do MESMO bloco `if not
  self._map_overlay.is_open:` que já protege minimap/buff bar/tracker
  de quest (mapa-múndi aberto — tecla M — cobre a tela inteira, não faz
  sentido desenhar por baixo).

**Verificação visual** (23/09/2026, antes de considerar fechado):
script isolado renderizando a trilha em 5 frações (0.02/0.25/0.5/0.75/
0.98) pra uma Surface, screenshot comparado lado a lado — confirmado:
extremos escuros/azulados (Noite), transição quente (Amanhecer/
Entardecer), centro claro (Dia), marcador seguindo a posição certa em
cada frame, rótulo batendo com a fase.

**Teste**: `tests/test_client_ui.py` — geometria (`rect()` centralizado,
tamanho batendo com `UI.DAYNIGHT_CLOCK_*`), oculto durante instância
(`iius.active=True` não desenha nada na trilha), marcador na posição X
certa pra uma fração mockada, e um invariante estrutural (soma dos
canais do centro > soma da ponta + margem, prova que a trilha É mais
clara no meio) — nunca comparar pixel exato (âncoras de cor são
editáveis pelo usuário no F12, um valor fixo quebraria à toa). Suíte
completa de `tests/test_client_ui.py`: 204 passed.

## 42. Feature — mapa fechado (caverna/dungeon) fica de fora do ciclo dia/noite (23/09/2026)

Pedido do usuário: caverna/dungeon nunca recebe luz do sol — aplicar o
tingimento ambiente e o relógio do HUD (§203/§205) lá não faz sentido
nenhum (ficaria escurecendo/clareando um lugar que já é sempre igual).
Pergunta exploratória antes de codar ("qual a complexidade, o que
acha?") — respondida com complexidade BAIXA + recomendação de desenho
(campo booleano por mapa, default True, só marcar False nas cavernas),
usuário confirmou ("Exatamente") antes de qualquer código escrito.

**Levantamento ANTES de decidir onde o campo mora**: cada mapa já tem um
`<mapa>_entities.json` opcional (`ambient_zones`, `spawn_zones`, etc.,
`engine/map_loader.py::_merge_entities_json`) mesclado num dict
`spawn_points` só de escalares/listas — já existia precedente EXATO pra
um campo escalar simples de mapa inteiro: `"default_ambient"` (string,
lido por `game.py::_load_ambient_zones()`, guardado como atributo de
instância do `GameEngine`, não um componente ECS). Copiado o MESMO
padrão pra `day_night_affected` em vez de inventar um componente
`Tilemap` novo — mais simples e já é o jeito que o projeto resolve
"metadado escalar de mapa inteiro" hoje.

**Implementação**:
- `engine/map_loader.py`: `spawn_points["day_night_affected"] = True`
  no dict de defaults; `_merge_entities_json` sobrescreve com
  `bool(data["day_night_affected"])` se a chave existir no JSON.
- `maps/map_cave_west_entities.json`/`maps/map_cave_east_entities.json`:
  `"day_night_affected": false` adicionado (únicas cavernas com
  `_entities.json` de verdade e alcançáveis via transição — existe um
  `maps/map_worm_cave.csv` órfão sem `_entities.json` e sem nenhuma
  referência em código Python, fora de escopo, não mexido).
- `game.py`: `self._day_night_affected: bool = True` (atributo de
  instância, inicializado no `__init__`, mesma vizinhança de
  `self._default_ambient`); `_load_ambient_zones(spawn_points)` (ÚNICO
  ponto de carga por troca de mapa, 2 call sites — carga inicial e
  transição) lê `spawn_points.get("day_night_affected", True)` e
  propaga pros DOIS sistemas de UI: `self._day_night_system.
  map_affected` e `self._day_night_clock_hud.map_affected` — setado 1x
  por troca de mapa (nunca todo frame, o valor só muda quando o mapa
  muda).
- `ui/day_night_system.py::DayNightSystem`/`ui/day_night_clock_hud.py::
  DayNightClockHud`: campo novo `self.map_affected: bool = True` (init)
  + 1 `return` a mais no topo de `render()`, logo depois do gate de
  instância já existente (`InstanceInventoryUIState.active`) — MESMA
  forma, novo motivo.

**Verificado antes de escrever o campo novo** que nenhum código
existente itera `spawn_points` de forma exaustiva (`.items()`/`.keys()`/
`for x in spawn_points`) esperando um conjunto FIXO de chaves — só
`server/debug_battleground.py::_discover_gate_tiles` faz `for side in
spawn_points`, mas é um PARÂMETRO homônimo diferente (dict pequeno
`{"time_a": (x,y), "time_b": (x,y)}`, não o dict de
`load_map_csv`) — sem risco de a chave nova quebrar esse código.

**Teste**: `tests/test_map_loader_terrain_parsing.py` —
`TestDayNightAffectedFlag` (mapa sem a chave no JSON default pra True
via `load_map_csv` real; as 2 cavernas carregam False de verdade, sem
mock). `tests/test_client_ui.py` — `DayNightSystem.render()`/
`DayNightClockHud.render()` com `map_affected=False` não desenham nada
(mesmo formato dos testes de gate de instância já existentes). Suíte
completa de `tests/test_client_ui.py`: 206 passed.

## 43. Tela de loading pré-carrega TODO asset do jogo (boot + troca de mapa/instância) + silencia som até carregar de verdade (28/09/2026)

Continuação da investigação de performance desta sessão. Ao analisar
um log de playtest numa BG (mais personagens em tela), achei um
freeze real de **~2060ms num frame só**, exatamente na entrada da BG,
com 4 custos empilhados no mesmo instante: `network_recv` (1492ms,
processando a mensagem `ZONE_CHANGE`), `rnd:get_world_objects` (323ms,
sprite nunca visto), `rnd:tile_static_objects` (112ms) e `rnd:minimap`
(102ms, rebuild do array numpy pro mapa novo). Usuário confirmou: sentiu
como um engasgo bem na transição, e apontou 2 problemas relacionados —
(1) o mesmo tipo de engasgo acontece ao explorar uma parte NOVA do
mapa aberto (sprite nunca carregado), e (2) durante a tela de loading
inicial, já dá pra ouvir som de combate antes do jogo "abrir" de
verdade.

**Pesquisa de mercado feita antes de desenhar a solução** (pedido
explícito do usuário): como jogos (WoW e afins) tratam carregamento de
tela — confirmou 3 pontos que guiaram o desenho: (a) transição
INSTANTÂNEA (teleporte/instância, nosso caso) sempre mostra tela
dedicada, mesmo em MMO AAA — nunca fica "seamless" como transição
andando; (b) prática universal de UX: nunca deixar a tela vazia/
congelada sem feedback enquanto carrega; (c) padrão comum (Phaser e
afins) é 1 "boot/preloader scene" carregando tudo de uma vez — usado
aqui porque o catálogo TOTAL do jogo é pequeno (medido: 217 imagens +
258 sons = ~45MB, ~600ms + ~2.5s = ~3.1s pra carregar TUDO), não
justifica a complexidade de streaming por-mapa que jogos grandes
precisam.

**Causa raiz do engasgo de sprite** (item 1 do usuário, já catalogado
antes nesta sessão via `rnd:get_world_objects`/§209): cada gerenciador
de asset do cliente (`TileSpriteManager`, `IconManager`, `IconAtlas`,
`SoundManager`, `hud_bars`, `render_system`, `spell_system`,
`character_animation`, `entity_disguise`, `effect_animator`) tinha seu
PRÓPRIO dict de cache, nunca compartilhado — mesmo pré-carregando um
arquivo antecipadamente, o resultado seria jogado fora, porque a
chamada real em jogo decodificaria tudo de novo do zero.

**Causa raiz do som durante o loading** (item 2 do usuário): o
cliente já processa mensagens de rede (`_process_network()`) DURANTE
a tela de loading inicial — só a parte VISUAL fica escondida atrás da
tela, o processamento de jogo (e o som que ele dispara, ex: combate de
quem está por perto) continua rolando por baixo.

**Fix, em 5 partes** (commits separados, mesma sessão):

1. `ui/asset_cache.py` (novo) — `get_image(path)`/`get_sound(path)`,
   cache ÚNICO por caminho de arquivo, compartilhado entre TODOS os
   gerenciadores acima (~14 pontos que chamavam `pygame.image.load`/
   `pygame.mixer.Sound` direto passaram a chamar isto). Guarda a
   Surface/Sound RAW — cada gerenciador continua responsável pela
   própria transformação (escala/corte/composição) e cache de
   derivadas, só o carregamento CRU do arquivo passou a ser
   compartilhado.
2. `ui/asset_preloader.py::preload_all(progress_cb=None)` — varre
   `assets/` inteiro (recursivo) e carrega cada imagem/som pelo cache
   único. Decisão deliberadamente bruta (carrega TUDO, não só o que o
   mapa atual usa) — justificada pela pesquisa acima, dado o tamanho
   pequeno do catálogo total.
3. `game.py::__init__` chama `preload_all()` logo depois do 1º desenho
   estático da tela de loading, ANTES de `_load_map_and_entities()` —
   redesenha a tela a cada leva de arquivos via `progress_cb` (evita
   os ~3.1s de tela travada sem feedback). `_draw_loading_screen`
   (`client/habilidades_handlers.py`) ganhou 2 fases: "assets" (barra
   mostra done/total real do preload) e "server" (comportamento de
   sempre, barra baseada em `_loading_min_t`).
4. `SoundManager.set_game_ready(bool)` — gate no único ponto de
   entrada comum de SFX de gameplay (`play()`, do qual
   `play_mob`/`play_ui`/`play_footstep`/`play_random`/`play_skill`/
   `play_mob_sounds` todos derivam) — NUNCA gateia ambiente/música
   (tocar música de fundo durante loading é normal). `game.py` desliga
   no início do `__init__` (antes do preload) e liga de volta no
   exato frame em que a tela de loading termina — mesmo tratamento na
   reconexão (perda de conexão pós-login volta a silenciar até
   reconectar).
5. `_handle_msg_zone_change` (`client/network_handlers.py`) — antes de
   chamar `_do_transition()`, desenha `_draw_map_transition_screen()`
   (novo, mesmo estilo visual da tela de loading, sem barra — a troca
   de mapa não tem contagem própria de progresso) + silencia som,
   SÓ quando é troca de mapa DE VERDADE (arquivo diferente do atual) —
   nunca no atalho de "portar pro mesmo mapa"/morte no mesmo mapa
   (`_do_transition` já trata esse caso como reposição rápida, sem
   recarregar nada, nunca teve esse engasgo). Com o pré-carregamento
   do boot (item 3), a maior parte do freeze original (sprite/objeto
   nunca visto, ~430ms dos ~2060ms medidos) já deveria desaparecer
   sozinha antes mesmo desta tela entrar em cena — o que resta
   (parse de mapa/tilemap novo, rebuild do minimapa) passa a ter
   feedback visual em vez de silêncio.

**Testes**: `tests/test_asset_cache.py` (memoização por identidade +
arquivo inexistente não lança), `tests/test_asset_preloader.py`
(contra pasta fake isolada — carrega tudo, arquivo corrompido entra
em "failed" sem lançar, `progress_cb` correto, pasta ausente não
lança), `tests/test_loading_screen.py` (barra reflete progresso real
na fase assets, não usa `_loading_min_t` nessa fase, não lança com
total=0), `tests/test_sound_muted_during_loading.py` (`play()` normal
por default, `set_game_ready(False)` suprime de verdade via canal de
mixer real — não só o retorno da função —, `set_game_ready(True)`
restaura), `tests/test_map_transition_screen.py` (troca real mostra
tela + silencia/libera som na ordem certa; teleporte pro MESMO mapa
não mostra tela nem silencia; efeitos colaterais já existentes de
BG/Faction continuam intactos). Revert-to-confirm feito em cada peça,
separadamente. Suíte completa dos arquivos tocados passando a cada
commit. 100% client-side, sem deploy no servidor.

## 44. Animação de morte de mob — feature nova, não um bug (11/10/2026)

**Pedido do usuário**: tocar a animação "death" do mob ao morrer e
travar no último frame; se não tem loot, anima e some; se tem loot,
fica congelado no lugar da elipse atual até ser saqueado/expirar —
"mesma regra do corpo (elipse) hoje, só que sem precisar mais dele". O
próprio usuário avisou de antemão "acho que o projeto não está
preparado pra isso" — pedido explícito de pesquisa + desenho antes de
codar, sem exceção.

**Pesquisa feita (agente dedicado) antes de propor qualquer coisa**:
confirmou que hoje NÃO existe nenhum "corpo de mob" visual — o mob é
removido pelo servidor no MESMO tick da morte
(`ServerDeathHandler.update()`), e o cliente remove o espelho local
quase imediatamente (só um grace curto de até 2s pra terminar um
TWEEN de movimento em andamento, `_pending_mob_despawn`/
`_PENDING_DESPAWN_TIMEOUT` — nada relacionado a animação). O que
parece "corpo" hoje é uma elipse TOTALMENTE separada
(`ui/loot_system.py::render_world`), ligada a um registro de loot do
servidor (`WorldServer._corpses`, timers 45s/30s/0s) que só existe se
o mob soltou algo — sem relação nenhuma com a entidade do mob (já
removida).

**Decisão de arquitetura tomada, registrada pra transparência**: dado
que a animação em si é 100% cosmética, e a arquitetura já separa
"gameplay" (servidor) de "desenho" (cliente), decidi implementar a
feature INTEIRA no cliente, sem nenhuma mudança de protocolo/servidor
— a identidade do mob que morreu (raça/slug) já é conhecida do cliente
ANTES da morte (`EntityIdentity`), então não há necessidade de o
servidor informar nada de novo. Isso evitou a alternativa mais
arriscada (seguraria a entidade REAL do mob viva no servidor até o
corpse expirar, que tocaria IA/respawn-zone/pathing/aggro — exatamente
o tipo de mudança que CLAUDE.md pede pra nunca decidir sozinho).
Perguntei ao usuário (AskUserQuestion) só 2 pontos realmente em aberto:
(1) mob sem sprite "death" ainda — cai no comportamento de hoje (some
na hora), confirmado; (2) entregar as duas partes (sem loot / com
loot) juntas, confirmado.

**Desenho implementado** (ver CLAUDE.md "Morte de MOB com animação
'death'" pra descrição atemporal):
1. `engine/mob_animation.py`: `trigger_mob_death_anim` liga
   `anim_attack_hold_last_frame=True` (campo que já existia em
   `TileMovement`, usado pelo cast "portal in" do personagem — nunca
   fora consumido por `resolve_mob_display_state`, que simplesmente
   não tinha o branch; portado de `character_animation.
   resolve_display_state`).
2. **Sem loot**: `_despawn_mob_or_play_death` (novo, `client/
   remote_entity_handlers.py`) — ponto único que substitui as 2 chamadas
   diretas de `world.remove_entity(local_eid)` que existiam pra mob
   (`AOI_UPDATE` despawn em `client/network_handlers.py` e
   `_flush_pending_mob_despawns`). Decide: kill de verdade + sprite
   "death" disponível → liga a animação, agenda remoção de verdade
   pro `_dying_mobs` dict (consumido por `_flush_dying_mobs`, chamado
   todo frame em `game.py` ao lado de `_flush_pending_mob_despawns`);
   qualquer outro caso (saída de AOI, sem sprite) → remove na hora,
   comportamento idêntico a antes.
3. **Com loot**: `ui/loot_system.py::render_world()` ganhou um branch
   ANTES da elipse — se `Corpse.mob_key` resolve um frame congelado
   (`get_mob_death_last_frame`), desenha ele no lugar, ancorado pelo
   CENTRO do frame (mesma convenção do mob vivo, §269) em vez da
   elipse. `Corpse.mob_key`/`facing` são campos novos opcionais
   (default `""`/`"down"`) — forma de "a identidade chegou" é uma ponte
   client-only por tile, `GameEngine._pending_death_race_by_tile`,
   gravada em QUALQUER kill real (mesmo sem sprite — é dado barato,
   só nunca é consumida com sucesso nesse caso) e consumida por
   `_handle_msg_loot_available` na MESMA coordenada que
   `_pending_loot_redirect` já usa (padrão já existente, só uma 2ª
   ponte do mesmo tipo). Janela de vida curta (3s,
   `_DEATH_RACE_BRIDGE_TTL_MS`) — puramente cosmética, pior caso
   possível (2 mobs morrendo no MESMO tile dentro de 3s) é mostrar o
   sprite do mob ERRADO congelado, nunca afeta loot de verdade.

**Testes**: `tests/test_mob_death_animation.py` (14 casos) — estado
puro de animação (hold-last-frame, nunca expira sozinho) com frames
SINTÉTICOS (seed direto em `_mob_frame_cache`, sem depender de arte
real), `Corpse.mob_key` default/passagem por `create_corpse`,
`ui/loot_system.py` desenhando sprite vs. elipse, e o fluxo completo
de `_despawn_mob_or_play_death`/`_flush_dying_mobs` (kill com/sem
sprite, saída de AOI nunca anima). Confirmado também contra os sprites
REAIS que o usuário já tinha desenhado nesse meio tempo
(`goblin_guerreiro_death_{down,up,left,right}.png`, 64×64, 8 frames,
960ms de duração por ciclo) — `get_mob_death_last_frame`/
`get_mob_death_anim_duration_ms` resolvem certo nas 4 direções.

**Regressão pega e corrigida ANTES de declarar pronto**: 2 fixtures de
teste (`tests/test_map_transition_screen.py::
TestClearRemoteEntityCaches`, `tests/test_client_ui.py::
_NetHandlerFixture`) quebraram porque construíam o objeto fake sem os
2 atributos novos (`_dying_mobs`/`_pending_death_race_by_tile`) — rodar
a suíte ampla ANTES de considerar terminado (não só os testes novos)
pegou isso; `TestClearRemoteEntityCaches` em particular já existe
precisamente pra pegar "cache novo esquecido" (§217-ish), e funcionou
como desenhado. As 28 falhas restantes da suíte ampla (test_session.py/
test_server.py/test_jungle_camps.py/etc.) são PRÉ-EXISTENTES —
confirmado rodando a mesma suíte via `git stash` no commit anterior
(falha idêntica sem nenhuma mudança minha) — causa raiz é Postgres não
estar rodando localmente nesta sessão, fora de escopo desta feature.

## 45. Elipse de loot trocada pela animação de loot.png, vazio não desenha mais nada (11/10/2026)

**Pedido do usuário**: substituir a elipse de 3 cores (dourado/marrom
claro/escuro) pela animação em `assets/sprites/loot.png` — e quando o
estado fosse "escuro" (sem loot nenhum), não desenhar nada.

**Dúvida levantada antes de implementar**: `loot.png` (288×32 = 9
frames de 32×32) tem um esquema de cor ÚNICO (dourado) — perguntei se
a distinção visual gold-vs-item-só devia ser preservada via
tingimento programático, ou se a mesma animação servia pros 2 casos.
Usuário confirmou: mesma animação pros 2, sem tingimento (mais simples,
sem precedente no projeto pra esse tipo de filtro de cor).

**Implementação**: `ui/loot_system.py::render_world()` ganhou 2
mudanças — (1) `Corpse` sem loot nenhum (`coins<=0 and not loot`) sai
do loop com `continue` ANTES de desenhar qualquer coisa (nem elipse,
nem animação); (2) quando `Corpse.mob_key` não resolve um sprite de
morte (branch já existente de §271), em vez de cair direto na elipse,
tenta `ui/world_icon_animator.py::get_frame("loot", 32, now_ms)`
primeiro — MESMO módulo já usado pro marcador animado de quest
(nenhum módulo novo, mesma categoria de asset "ícone de mundo"). A
elipse de 2 cores (dourado/marrom — o caso "escuro" nem existe mais)
vira fallback de ÚLTIMO recurso, só alcançado se `loot.png` falhar ao
carregar (nunca em uso normal, já que o arquivo está commitado).

**Escopo deliberadamente limitado**: a pergunta do usuário mencionava
só "o cilindro" — NÃO toquei no comportamento do sprite de morte
congelado do mob (§271), que continua mostrando pelo tempo de vida
INTEIRO do `Corpse` (incluindo depois de totalmente saqueado), mesmo
que isso pareça inconsistente com "vazio não mostra nada". Essa
inconsistência é intencional por ora — mudar o lifecycle do sprite de
morte não foi pedido, e alterar os dois ao mesmo tempo seria escopo
maior do que o relatado. Se o usuário quiser o MESMO "some quando
vazio" pro sprite de morte também, é um pedido novo, separado.

**Testes**: `tests/test_mob_death_animation.py` ganhou
`test_corpse_vazio_nao_desenha_nada` (reprodução direta — superfície
não muda em nada) e o teste antigo que validava "elipse ainda
desenha" foi renomeado/atualizado pra refletir que agora é a animação
de `loot.png`, não mais a elipse, que desenha nesse caso (a asserção
em si — "algo foi desenhado" — continua válida, só a documentação do
teste estava ficando desatualizada).

**Follow-up no mesmo dia — "ainda aparece a elipse do loot"**:
relatado pelo usuário logo depois do fix de §272 acima parecer
completo. Causa raiz: `ui/loot_system.py::render_world()` NÃO é o
único lugar que desenha o marcador de loot — `client/
remote_entity_handlers.py::_draw_remote_corpses()` é um SEGUNDO
caminho, chamado TODO frame em `game.py` (linha ~2730, logo depois de
`self._loot_system.render_world(cam_x, cam_y)` na mesma sequência de
render) — eu não tinha achado essa função na primeira pesquisa porque
procurei só por "elipse"/"cilindro" mentalmente associado a
`loot_system.py`, sem fazer um `grep` amplo por `pygame.draw.ellipse`
no repo inteiro ANTES de declarar o fix pronto. `_draw_remote_corpses`
desenhava a MESMA posição com a lógica de 3 cores antiga, pra TODO
`corpse_id` em `_remote_corpses` — inclusive os que o PRÓPRIO jogador
já tinha `Corpse` local (`_available_loot[corpse_id]` presente) e que
`loot_system.py` JÁ tinha desenhado corretamente (sprite de morte,
animação, ou nada) — resultado: a elipse antiga desenhava POR CIMA do
que eu tinha acabado de corrigir, invisível no código mas bem visível
em jogo.

**Fix**: `_draw_remote_corpses` agora `continue` IMEDIATO quando
`_available_loot.get(corpse_id) is not None` (esse corpse já foi
desenhado por `loot_system.py`, nunca de novo aqui) — só desenha algo
pra quem NÃO tem entitlement local (outro player vendo um corpse que
não é dele, ou ainda não processou o `LOOT_AVAILABLE` correspondente),
e troca a elipse de 3 cores pela MESMA animação de `loot.png` usada em
`loot_system.py` (sem distinção de cor, mesma decisão do usuário já
confirmada).

**Lição / regra geral**: antes de declarar um fix de "aparência
visual" completo, rodar `grep -rn "pygame\.draw\.<forma>"` (ou
equivalente pro tipo de desenho em questão) no repo INTEIRO — nunca
assumir que a função que "parece a dona" do visual é a única fonte.
Mesma classe de erro já catalogada pro nameplate (§267: `ui/
render_system.py` vs. `_draw_mob_hp_bars`) — este é o 2º incidente
reconhecido da mesma família ("local" desenha uma vez, "remoto"/
"outro player" desenha de novo, sem os dois saberem um do outro).
Candidato a virar regra permanente em CLAUDE.md se um 3º incidente
parecido aparecer.

## 46. Mob de zona/jungle nascia sempre virado pra baixo (13/10/2026)

**Pedido do usuário**: "a posição das aranhas no spawn delas é igual
pra todas, todas ficam viradas pra baixo, é possível fazer com que
isso seja aleatório?"

**Causa raiz**: `TileMovement.facing` tem default `"down"` e nada
nunca sobrescrevia isso na criação de um mob — nem `create_enemy()`
nem `SpawnZoneSystem._spawn_one()` (que só sorteia `level`, nunca
direção). Rede nunca carregava esse campo pro cliente (`_build_mob_
spawn_payload` não tinha `"facing"`), então mesmo randomizando só no
servidor o cliente nunca saberia.

**Decisão de escopo**: sortear dentro de `create_enemy()` (o wrapper,
NÃO `_build_combat_entity` genérica) — assim só mob hostil de verdade
(spawn zone + acampamento de jungle, os 2 únicos chamadores reais de
`create_enemy()` fora de testes) ganha direção aleatória; NPC de
serviço/guarda (`create_combat_npc`/`create_merchant`/`create_
trainer`/etc., que chamam `_build_combat_entity` direto) continua
sempre "down" de propósito — comportamento deles nunca foi o motivo
do pedido. Cobre respawn de graça, já que `_spawn_one` chama
`create_enemy()` de novo a cada vez que a zona reenche.

**Sincronização**: como `create_enemy()` também é chamada do LADO
CLIENTE pra reconstruir o espelho de qualquer mob remoto
(`_spawn_remote_mob`), sortear só ali faria cada jogador ver o MESMO
mob virado pra um lado diferente (servidor sorteia um valor pra
entidade real, cliente sorteia outro pro espelho, de forma
independente). Fix: `_build_mob_spawn_payload` (`server/world_
server.py`) passou a mandar `"facing": tm.facing` (servidor é a
única fonte de verdade); `_spawn_remote_mob` (`client/remote_
entity_handlers.py`) sobrescreve a direção que a chamada local de
`create_enemy()` acabou de sortear, aplicando a direção REAL que
veio no payload.

**Teste**: nenhum automatizado (decisão do usuário — mudança
cosmética, nível de teste decidido por mudança) — validado rodando a
suíte de mob/spawn/NPC relacionada (`test_mob_target_facing.py`,
`test_mob_animation.py`, `test_mob_death_animation.py`,
`test_dungeon.py`, `test_jungle_camps.py`, `test_towers.py`,
`test_minions.py`, `test_npc_service_unlock.py`,
`test_service_npcs.py`) sem regressão nova (os poucos testes que já
falhavam antes da mudança continuam falhando pelos mesmos motivos,
confirmado via `git stash`/baseline). Validação visual fica pro
playtest do usuário.

## 47. Kit de animação customizado pra boss de dungeon (13/10/2026)

**Pedido do usuário**: boss específico dentro de uma `SpawnZone` (ex:
"Acraxxidae, o Obstinado") usar um conjunto de sprites PRÓPRIO
(`acraxxidae_o_obstinado_idle_down.png`, etc.) em vez do sprite
genérico da raça ("Aranha") — mas continuar com os mesmos stats/sons/
loot da raça, só a aparência animada muda.

**Pesquisa feita antes de codar**: o sistema de animação de mob
(`engine/mob_animation.py`, §267-272) já usa convenção de arquivo por
"chave" (`{chave}_idle_{direção}.png`), e essa chave hoje SEMPRE vem de
`EntityIdentity.mob_key or race` — nenhum ponto de override existia.
Mapeei os 4 call sites que leem essa expressão direto (duplicada, sem
dono único): `ui/render_system.py::_resolve_mob_animation_key`
(sprite em si), `client/remote_entity_handlers.py` em 3 pontos
(gatilho de golpe, ponte de sprite de morte do corpse, posicionamento
de nameplate).

**Desenho**: `EntityIdentity` ganha `sprite_key: str = ""` (override
opcional, nunca mexe em `mob_key`/`race` — aqueles continuam
alimentando stats/sons/MOB_TABLE normalmente). `SpawnZone` ganha
`spritesheet: str = ""`, lido do JSON do mapa (`_entities.json`,
dentro de `spawn_zones`) com o MESMO fallback por-spawn/por-zona que
`name`/`race`/`class` já usam — `sp.get("spritesheet", zone.get(
"spritesheet", ""))`. Cadeia: `map_loader.py` → `WorldServer.
_create_spawn_zones_for_map` → `SpawnZone.spritesheet` →
`SpawnZoneSystem._spawn_one` passa `sprite_key=zone.spritesheet` pro
`create_enemy()` já existente → `_build_combat_entity` grava em
`EntityIdentity.sprite_key`.

**Consolidação dos 4 call sites**: criei `engine.mob_animation.
resolve_mob_sprite_key(ident)` (prioridade `sprite_key > mob_key >
race`) e troquei os 4 pontos pra chamar essa função em vez de repetir
`ident.mob_key or ident.race` cada um na própria cópia — um override
novo como este precisaria ser adicionado em 4 lugares separados sem
essa consolidação, com risco real de esquecer um (mesma classe de bug
já documentada pro nameplate/corpse de mob, §267/§272 — "2 caminhos de
desenho, 1 corrigido, o outro não").

**Sincronização de rede**: como `create_enemy()` também roda do LADO
CLIENTE pra reconstruir o espelho de qualquer mob remoto
(`_spawn_remote_mob`), `sprite_key=""` (default) nessa chamada local
nunca bate com o valor real do servidor sozinho — `_build_mob_spawn_
payload` passou a mandar `"sprite_key"` (só quando não-vazio, mesmo
padrão condicional de `"name"`), e `_spawn_remote_mob` aplica no
`EntityIdentity` do espelho logo após criá-lo (mesmo padrão já usado
pro nome customizado, `_server_name`).

**Escopo**: só `create_enemy()` ganhou o parâmetro novo (não
`_build_combat_entity` genérica sozinha sem repasse explícito) — NPC
de serviço/guarda (`create_combat_npc`/`create_merchant`/etc.) não
precisam disso, continuam sem override, comportamento inalterado.

**Teste**: nenhum automatizado (decisão do usuário) — validado rodando
a suíte de mob/spawn/NPC relacionada (mesma lista de §276 + nameplate)
sem regressão nova. Validação visual fica pro usuário, criando o kit
de sprites do boss (`{spritesheet}_idle_down.png` etc.) e configurando
`"spritesheet": "acraxxidae_o_obstinado"` no spawn/zona do mapa.

## 48. Frame de animação de personagem passa a ser QUADRADO (15/10/2026)

**Pedido do usuário**: "como teria que ser o tamanho do frame para que
a animação do mage funcionasse corretamente? só coloquei as animações
com o frame 64x64... a animação ficou bem estranha" — seguido de "é
possível fazer com que o sistema identifique automaticamente baseado
no tamanho total da arte e a quantidade de imagens?".

**Causa raiz do sintoma original**: `engine/character_animation.py`
cortava toda sheet em largura FIXA `CHAR_TILE_SIZE=96`
(`sheet.get_width() // CHAR_TILE_SIZE` frames, `Rect(i*96, 0, 96,
frame_h)`), decisão explícita de 09/09/2026 ("largura fixa, NÃO
derivável da altura do sheet"). Arte do Mago exportada em frames
64×64 — o corte continuava fatiando a cada 96px, misturando pedaço de
um frame real com o próximo e errando a contagem total.

**1ª proposta (descartada pelo usuário no meio da implementação)**:
declarar a contagem de frames no nome do arquivo (ex:
`mago_idle_down_8f.png`), preservando a largura fixa como fallback.
O usuário interrompeu e pediu algo mais simples: "vamos fazer com que
o sistema identifique a quantidade de frames fazendo a divisão pela
ALTURA" — ou seja, adotar a MESMA convenção que `engine/mob_animation.py`
já usa pra mob (frame QUADRADO, largura=altura=altura real do sheet,
contagem=largura_total//altura) — zero metadata nova, reaproveita um
padrão já provado no próprio projeto em vez de inventar um novo.

**Efeito colateral achado ANTES de codar** (mapeamento obrigatório —
CLAUDE.md): `guerreiro_portal_in.png`/`portal_out.png` são 768×640 —
hoje 8 frames de 96×640 (pilar de luz vertical ESTREITO e ALTO, de
propósito, redesenhado em 02/10/2026 especificamente pra essa
proporção). Aplicar "largura=altura" cegamente faria essa sheet virar
1 frame só de 640×640, destruindo a animação de portal. Levado ao
usuário ANTES de implementar — decisão: `portal_in`/`portal_out`
ficam em lista de exceção (`_FIXED_WIDTH_STATES`), continuam com
largura fixa `CHAR_TILE_SIZE`; todo o resto (idle/run/attack/skill/
qualquer sid de skill) vira quadrado.

**Fix**:
- `_load_sheet_frames()` (`engine/character_animation.py`): `frame_w =
  CHAR_TILE_SIZE if state in _FIXED_WIDTH_STATES else frame_h` (mesmo
  guard de `frame_h<=0` que `mob_animation.py` já usa). `CHAR_TILE_SIZE`
  deixa de cortar sheet nenhuma — vira só o default de
  `character_anchor_offset()`.
- `character_anchor_offset()` ganhou parâmetro `frame_width` — `dx`
  passou de hardcoded `CHAR_COLLISION_OFFSET_X + CHAR_COLLISION_SIZE/2`
  (=48, só por coincidência de frame sempre ter 96 de largura) pra
  `frame_width / 2` direto (tile de colisão é centralizado por
  construção, independente da largura real do frame). Default de
  AMBOS os parâmetros preserva (48, 48) pra quem chama sem Surface em
  mãos (nameplate de player remoto, que quer esse valor FIXO de
  propósito, nunca "balançando" por animação). Os 2 call sites que já
  tinham o Surface em mãos (`client/portal_handlers.py`, `ui/
  render_system.py`) passaram a mandar `frame.get_width()` também, não
  só `get_height()`.

**Estado de transição conhecido, aceito pelo usuário**: `guerreiro_
idle_down.png` (768×80, ainda NÃO quadrado) corta hoje em 80×80 (9
frames, 768/80 não é divisão exata — último frame incompleto) até o
usuário re-exportar a arte do Guerreiro em frames quadrados de
verdade ("eu vou corrigir" — dito explicitamente). Não é regressão do
fix, é o próximo passo de conteúdo que o usuário já sinalizou que vai
fazer.

**Teste**: `tests/test_character_animation.py` — 2 testes que
comparavam largura do blit contra o literal `96` (`test_combinacao_
existente_retorna_frame`, `test_blit_alinha_centro_do_tile_de_colisao_
com_position`) e 1 que usava `96` só como heurística pra achar o blit
do personagem no spy de render (`test_ysort_de_personagem_...`)
precisaram ser reescritos pra não depender de um número fixo que a
arte real pode mudar — descobrem a largura REAL do frame carregado
(`get_character_frame(...).get_width()`) antes de montar a asserção/
filtro, em vez de hardcoded `96`. Suíte de animação de personagem +
nameplate + portal (155 testes) + client_ui/selection_outline (260
testes) sem regressão.

## 49. Animação de cast separada da de lançamento, pra qualquer skill (15/10/2026)

**Pedido do usuário**: "precisamos consertar a animação de ataque do
mago, o correto seria ter duas animações, uma para o cast, e outra
para o lançamento da magia, que é no momento que o projétil [sai]...
já pode extender isso para todos os casts do jogo, como arqueiro".

**Pesquisa feita antes de codar**: mapeei o fluxo de skill ponta a
ponta antes de decidir qualquer coisa. Achados principais:
- `trigger_attack_anim()` (golpe/lançamento) só disparava em UM lugar
  — `client/remote_entity_handlers.py::_apply_combat_result`, no
  COMBAT_RESULT (confirmação de IMPACTO/resultado). Não existia
  animação de CAST (windup) nenhuma — o personagem ficava em idle/run
  o cast_time inteiro, só a barra de cast (UI) indicava progresso.
- O servidor **já emite** 2 eventos `SKILL_EFFECT` nos momentos exatos
  necessários, já broadcast AOI pra caster local E observadores
  (`server/skill_processor.py`/`spell_completion_processor.py`):
  `"cast_start"` (cast aceito, início do windup) e `"launch"`/
  `"impact"`/`"miss"` (conclusão — `"launch"` só pra skill de
  PROJÉTIL, cujo impacto físico vem depois via COMBAT_RESULT separado;
  `"impact"`/`"miss"` pra skill SEM projétil, cast completo = resolução
  imediata; TAMBÉM usado por skill INSTANTÂNEA sem cast_time nenhum).
  Hoje só tocavam som — zero mudança de protocolo necessária pra essa
  feature inteira.

**Desenho**: 2 fases, nomenclatura em inglês (consistente com idle/
run/attack/skill já existentes):
- **"cast"** (NOVO) — `engine.character_animation.
  resolve_cast_state_name()`: cascata de 2 níveis, `f"{sid}_cast"`
  (windup específico) → `"cast"` genérico → `None` (sem arte, fica em
  idle/run, zero regressão). `trigger_cast_anim()` liga em LOOP pela
  duração REAL de `cast_time` (SKILL_CATALOG, fonte única) — chamado
  por `_handle_msg_skill_effect` no evento `"cast_start"`.
- **"lançamento"** reusa a cascata JÁ EXISTENTE (`attack`/`skill`/
  `{sid}`, `resolve_attack_state_name`) — NENHUM nome novo, só o
  GATILHO mudou: `trigger_attack_anim()` passou a ser chamado também
  (e, pra skill com sid, EXCLUSIVAMENTE) nos eventos `"launch"`/
  `"impact"`/`"miss"` de SKILL_EFFECT, nunca mais em COMBAT_RESULT.

**Por que remover o gatilho de COMBAT_RESULT pra skill (não só
adicionar o novo)**: sem isso, uma skill de PROJÉTIL acionaria o golpe
2x — uma vez no lançamento (`"launch"`, correto) e de novo no impacto
físico real (`COMBAT_RESULT`, tarde demais e duplicado). `_apply_
combat_result` (`client/remote_entity_handlers.py`) agora só dispara
pra auto-attack PURO (`sid==""`) — skill com sid nunca mais passa por
ali, 100% movido pro handler de SKILL_EFFECT.

**`anim_attack_is_cast` (campo novo em `TileMovement`)**: um loop de
CAST em andamento SEMPRE cede pro lançamento, mesmo ainda dentro da
própria duração estimada — porque a conclusão REAL do cast é o
evento de rede, não o relógio local (`cast_time` é só ESTIMATIVA, rede
tem folga/latência real, mesmo raciocínio já usado por `anim_attack_
hold_last_frame` no portal). Sem este campo, a regra de prioridade JÁ
EXISTENTE de loop (`_attack_loop_still_active`, criada pro Fatiador de
Corpos) bloquearia a interrupção do cast por engano — golpe em loop
que NÃO é cast (Fatiador) continua 100% protegido pela regra antiga,
intocada.

**Escopo**: automático pra QUALQUER classe/skill com `cast_time>0` —
nenhuma lista de exceção, nenhum catálogo novo. Sem arte de `"cast"`
pra uma skill/classe = comportamento de hoje (idle/run durante o cast
inteiro), zero regressão até o usuário criar os sprites.

**Armadilha achada ajustando os testes** (2 testes pré-existentes
quebraram, puramente por causa da mudança de comportamento intencional
— `_apply_combat_result` não dispara mais golpe pra skill com sid):
reescritos pra confirmar o NOVO contrato (COMBAT_RESULT não liga mais
animação/loop pra skill com sid) em vez do antigo. **Tentativa de
reutilizar um helper entre mixins, revertida**: tentei consolidar a
resolução de `class_id` (local via `CharacterStats`/remoto via
`RemoteControlled`, hoje duplicada 2x) num helper novo
(`_resolve_player_class_id`) chamado de `client/remote_entity_
handlers.py` — quebrou um teste que usa um fixture `MagicMock` com
SÓ `RemoteEntityHandlers` misturado (não o `GameEngine` completo),
porque o helper vive em `client/network_handlers.py` (mixin
DIFERENTE). Revertido pra cópia inline original ali — mesmo
princípio já documentado em §273 ("não mexer em call site já correto/
testado só pra eliminar duplicação, risco de quebrar isolamento de
mixin"); o helper novo continua existindo e sendo usado só dentro do
PRÓPRIO arquivo onde nasceu (`_handle_msg_skill_effect`).
**Também achado durante os ajustes**: `guerreiro_idle_down.png` foi
re-exportado pelo usuário (768×80 → 512×64, agora quadrado de
verdade) EM PARALELO a esta sessão — 2 testes que mediam a arte real
tinham expectativas hardcoded presas à altura antiga (80px); corrigidos
pra derivar a expectativa da altura REAL carregada, e uma 2ª asserção
de regressão (que comparava contra a fórmula antiga) virou coincidência
numérica frágil com a nova altura (64px) e foi removida — a asserção
principal já prova o mecanismo certo sozinha.

**Teste**: manual em playtest (decisão explícita do usuário) — a
cobertura automatizada ficou limitada a corrigir os 2 testes
pré-existentes que o novo comportamento quebrou (confirma o contrato
NOVO de `_apply_combat_result`), sem suíte nova dedicada ao cast/
lançamento em si. Validação visual fica pro usuário, criando os
sprites `{class_id}_cast_{direction}.png`/`{class_id}_{sid}_cast_
{direction}.png`.

## 50. Projétil nasce 1 tile à frente do atacante + Bola de Fogo emite luz (15/10/2026)

**Pedidos do usuário**: "1. Fazer o projétil iniciar no tile que está a
frente do personagem... atualmente está saindo do tile onde o
personagem está, fica estranho; 2. É possível fazer com que o projétil
de bola de fogo emita luz? e uma luz amarelada?"

**Pesquisa feita antes de codar**: `PlayerProjectileSystem.spawn()`
(`ui/spell_system.py`) já é o ÚNICO ponto de entrada pra criar
QUALQUER projétil visual do jogador (flecha, Bola de Fogo, Tiro
Múltiplo, Flecha Reiterada, Tiro Repulsivo, projétil cosmético de
mob/torre pro espectador) — todos os call sites já passavam `pos.x,
pos.y` (posição crua do atacante). Corrigir dentro de `spawn()` em vez
de em cada call site resolve os 2 pedidos de uma vez, sem duplicar
lógica.

**1. Deslocamento de spawn**: `spawn()` passou a olhar `TileMovement.
facing` de `attacker_id` e deslocar `(x, y)` 1 tile INTEIRO nessa
direção antes de criar a entrada — `engine.utils.facing_to_tile_delta()`
(novo, inverso de `facing_from_delta` já existente). `attacker_id` sem
`TileMovement` resolvível (ex: projétil cosmético de mob/torre remoto,
que passa `attacker_id=-1` de propósito) não desloca nada — mesmo
comportamento de sempre, fail-safe via `World.get_component` (try/
except, nunca explode com eid inválido).

**2. Luz da Bola de Fogo**: reaproveita 100% o pipeline de luz
dinâmica já usado pelo lampião aceso sob demanda (`ui/day_night_
system.py::DayNightSystem.render()`, entrada `"player_light": True` +
`"light_source"` + `"glow_color"` opcional) — `PlayerProjectileSystem.
get_fireball_light_sources(cam_x, cam_y)` (novo) devolve 1 entrada por
Bola de Fogo em voo, lendo a posição AO VIVO (`entry.x/entry.y`, já
atualizada todo tick pelo próprio update do projétil) — zero
componente ECS novo (projétil de jogador é client-only, fora do ECS
de propósito desde 15/09/2026, §144). Calibração (pedido do usuário):
70% intensidade / 3 tiles de raio — mais forte e compacto que o
lampião (50%/7 tiles), já que é um brilho vívido temporário, não uma
ferramenta de iluminação de área. Cor `(255, 200, 100)` — mesmo
amarelo-alaranjado quente que já é o DEFAULT do sistema pra esse tipo
de luz.

**Custo de performance (pergunta explícita do usuário, respondida
ANTES de implementar)**: o gradiente de luz é CACHEADO por `(raio,
intensidade)` desde a implementação original do lampião — o custo
real por Bola de Fogo em voo é só **1 blit** de uma Surface já pronta
(`BLEND_RGBA_ADD` pro furo na escuridão + `BLEND_RGBA_ADD` com
`set_alpha` pro brilho quente por cima), nenhum cálculo de pixel novo
por frame. Com um punhado de bolas de fogo simultâneas em tela (caso
realista — vários magos próximos), o custo adicional é alguns blits
baratos em cima de um sistema já medido em ~0.4-0.46ms por frame (ver
PROBLEMAS_ARQUITETURA.md, otimização de clusters estáticos). Não
identificado como risco.

**Integração**: `game.py` (montagem de `_lit_world_objs`, mesmo padrão
já usado pelas luzes de item/portal) chama `self._player_proj_system.
get_fireball_light_sources(cam_x, cam_y)` e concatena o resultado —
vazio na esmagadora maioria dos frames (sem Bola de Fogo em voo agora).

**Teste**: suíte de client_ui/day_night/portal (338 testes) +
animação de personagem/nameplate (84 testes) rodada sem regressão —
nenhum teste pré-existente assumia a posição EXATA de spawn de
projétil (só comportamento de hit/dano), então nenhum precisou de
ajuste. Sem teste automatizado NOVO dedicado (mudança
puramente visual/posicional) — validação fica pro usuário em
playtest.

## 51. §282 revertido no mesmo dia: deslocamento de spawn piorava alvo diagonal

**Pedido do usuário**: "ficou esquisito quando o ataque sai em
diagonal, pois o personagem vira apenas para quatro lados, e a magia
quando eu lanço em um inimigo que está na diagonal sai na frente do
personagem e desce para a direção do alvo... ficou pior do que antes".

**Causa raiz do novo problema**: `TileMovement.facing` só tem 4
valores cardeais, mas o alvo pode estar em QUALQUER ângulo —
deslocar a origem do projétil pro cardeal mais próximo (§282) tirava
o projétil da linha reta real até o alvo. A trajetória sempre aponta
pro alvo de verdade (`target_last_x/y`), então um spawn FORA dessa
reta faz o projétil visualmente "corrigir o rumo" logo nos primeiros
pixels — mais perceptível (e pior) quanto mais diagonal o alvo, que é
exatamente o caso que o deslocamento deveria ter ajudado.

**Decisão, com o usuário escolhendo entre 2 alternativas**: opacidade
crescente nos primeiros pixels de voo (em vez de Y-sort/ordem de
desenho, que só mudaria quem desenha por cima de quem — não resolve a
trajetória torta em nada, já que não muda posição nenhuma).

**Fix**: `spawn()` voltou a usar `(x, y)` exatamente como passado
(sem nenhum deslocamento) — trajetória sempre a linha reta real até o
alvo, como era antes do §282. `PlayerProjectileEntry` ganhou
`spawn_x`/`spawn_y` (posição de nascimento, nunca muda depois).
`PlayerProjectileSystem._spawn_fade_fraction(proj)` (novo) devolve
0.0→1.0 conforme a distância viajada desde o spawn cresce até
`PROJECTILE_FADE_IN_PX` (= 1 tile). Aplicado em `render()`:
- **Bola de Fogo** (único caminho que já desenha via Surface/blit) —
  alpha de VERDADE (`rotated.set_alpha(255*fade)`). Achado corrigindo
  isso: o branch `else: rotated = frame` (sem rotação, projétil
  parado) apontava pro frame CACHEADO/compartilhado entre TODAS as
  bolas de fogo (`self._fireball_frames`) — chamar `.set_alpha()`
  nesse objeto mutaria o cache global por engano, afetando qualquer
  OUTRA bola de fogo que reusasse o mesmo frame depois. Fix: sempre
  chamar `pygame.transform.rotate(frame, angle_deg)` (mesmo com
  `angle_deg=0.0`) — `rotate()` sempre devolve uma Surface NOVA, nunca
  o objeto compartilhado.
- **Flecha e círculo mágico genérico** (desenhados direto via
  `pygame.draw.*` no `world_surf` opaco, sem alpha de verdade
  possível sem uma Surface própria) — reaproveita a MESMA técnica que
  o rastro da flecha já usa pra "envelhecer" (escurecer a cor por
  fração), combinando o fator de idade existente com o novo fator de
  fade-de-nascimento.

**Teste**: suíte de client_ui/day_night/portal (338 testes) sem
regressão. Sem teste automatizado novo dedicado (mesma decisão do
usuário do §282 — mudança visual, validação em playtest).

## 52. Animação de cast esticada (não em loop) — reaproveitando a técnica do portal

**Pedido do usuário**: "é possível fazer com que a animação do
casting se estenda ao tempo do cast dinamicamente em vez de ser em
loop?"

**Pesquisa feita antes de codar**: o projeto JÁ TEM exatamente esse
mecanismo, implementado pro cast de portal (`client/portal_handlers.py::
_begin_portal_in_anim`, 02/10/2026) — `frame_ms = duração_ms /
nº_de_frames`, `anim_attack_loop_duration_ms = 0` — o sheet toca 1
ciclo só, mas cada frame fica esticado no tempo pra caber exatamente
na duração real. `trigger_cast_anim()` (§281, implementado mais cedo
no mesmo dia com LOOP) foi reescrito pra usar essa MESMA técnica em
vez de reinventar uma nova.

**Simplificação em cascata**: a troca de LOOP pra ESTICADO tornou
`anim_attack_is_cast` (campo em `TileMovement`, criado em §281 só pra
garantir que um loop de cast cedesse pro lançamento mesmo dentro da
própria duração) **morto** — `_attack_loop_still_active()` já devolve
`False` sozinho quando `anim_attack_loop_duration_ms == 0` (sempre o
caso agora pro cast), então a guarda de prioridade de
`trigger_attack_anim()` nunca mais bloqueia a interrupção do cast, sem
precisar de nenhuma exceção. Campo e a linha de bypass removidos —
Fatiador de Corpos (golpe em LOOP de verdade) continua 100% protegido
pela regra antiga, intocada.

**Comportamento no detalhe**: `anim_attack_hold_last_frame=True` pro
cast (mesmo raciocínio do portal — `cast_time` local é só ESTIMATIVA,
rede tem folga/latência real) — se o evento de LANÇAMENTO demorar um
pouco mais que a estimativa local, o personagem fica parado na ÚLTIMA
frame do windup em vez de "piscar" de volta pro idle por 1+ frame.

**Teste**: suíte de animação de personagem/nameplate/portal/client_ui/
Fatiador de Corpos (417 testes) sem regressão — nenhum teste
pré-existente testava o mecanismo de loop do cast especificamente
(§281 foi implementado e documentado, mas sem suíte dedicada, decisão
do usuário na ocasião), então não houve teste pra atualizar/remover.

## 53. Animação de morte do player (15/10/2026)

**Pedido do usuário**: "o jogo não está fazendo a animação de morte do
player, a animação de morte precisa ser feito assim que o player
recebe o hit que o mata, e o último frame da animação deve ficar
congelado até o personagem libertar espírito ou spawnar novamente."

**Pesquisa feita antes de codar**: confirmei que `ui/render_system.py`
já tem um conceito de "corpo" (`GhostState.is_dead and not is_ghost`
pro player local; `RemoteControlled.hp<=0` pro remoto, mesmo sinal já
usado pra suprimir a barra de HP do nameplate) — mas o branch de
desenho (`_is_corpse_draw`) sempre caía num retângulo cinza
dessaturado, nunca tentava a animação de personagem. O sistema de
morte de MOB (`engine/mob_animation.py::trigger_mob_death_anim`,
§271) já resolvia exatamente o mesmo problema pra mob — só faltava o
equivalente pro player.

**Fix**: `engine.character_animation.trigger_death_anim(tile_movement,
class_id, now_ms)` (novo) — mesma técnica do mob: toca 1 ciclo e trava
no último frame (`anim_attack_hold_last_frame=True`), convenção de
arquivo `{class_id}_death_{direction}.png` (sem cascata de sid — morte
não é específica de skill). `clear_attack_anim_state(tile_movement)`
(novo, genérico) limpa `anim_attack_state`/`anim_attack_hold_last_
frame` na mão — necessário porque estados com hold NUNCA expiram
sozinhos (mesmo padrão já documentado pro portal/cast).

**Gatilhos — reaproveitando mensagens de rede JÁ EXISTENTES, nenhuma
nova**:
- Local: `client/network_handlers.py::_handle_msg_player_death`
  (`PLAYER_DEATH`) — já é o sinal autoritativo "você morreu agora".
- Remoto: `_handle_msg_entity_death` (`ENTITY_DEATH`) — já existia e já
  tingia o sprite de cinza; só adicionei o trigger de animação ali.

**Limpeza — 3 pontos, cobrindo os 2 casos que o usuário pediu
("libertar espírito OU spawnar") + 1 por higiene**:
- `_handle_msg_player_revive` (`PLAYER_REVIVE`, local, respawn
  completo) — ponto óbvio.
- `_handle_msg_ghost_state`, na transição `is_ghost` False→True
  (local, "Liberar espírito") — tecnicamente o branch de render de
  fantasma nem olha pra esse estado (é um desenho totalmente
  diferente, semi-transparente), mas limpar aqui evita estado obsoleto
  sobrevivendo pro PRÓXIMO ciclo de vida do personagem.
- Transição de HP 0→positivo no handler de STATS_UPDATE de player
  remoto (revive remoto — não existe uma mensagem "reviveu" dedicada
  pro remoto, só o HP voltando a subir).

**Renderização**: `_is_corpse_draw` tenta resolver o frame de morte
PRIMEIRO (mesma âncora/Y-sort já usados pro personagem vivo,
`character_anchor_offset`/`get_character_frame`) — só cai no retângulo
cinza se `resolve_display_state` não devolver literalmente o estado
`CHAR_DEATH_STATE` (ou seja, sem arte de morte pra essa classe/direção
ainda, ou o trigger nunca chegou a ligar) — zero regressão pra classe
sem sprite. Confundir isso com "cair pro idle/run" seria o bug errado
de se cometer aqui — por isso a checagem é `_state_corpse ==
CHAR_DEATH_STATE` explícito, não só "frame não é None".

**Teste**: suíte de animação de personagem/nameplate/portal/client_ui
(407 testes) + seleção/mob (23 testes) sem regressão. Nenhum teste
pré-existente exercitava os handlers de PLAYER_DEATH/PLAYER_REVIVE/
ENTITY_DEATH/GHOST_STATE diretamente — decisão do usuário (manual em
playtest) confirmada compatível com a ausência de cobertura prévia
nesse ponto específico.

## 54. Golpe/cast em 2 fases (preparação → congela → solta) — arqueiro primeiro, mecanismo genérico (15/10/2026)

**Pedido do usuário**: "precisamos de alguma forma de sincronizar
melhor a animação de auto attack e cast do arqueiro, a animação
precisa acontecer da seguinte forma: a animação deve acontecer antes
de sair o projétil, junto com a emissão do som, e congelar no 5º frame
da animação, por alguns milésimos antes de sair o projétil então
finaliza a animação, e nas animações de cast, pode iniciar a animação
no início do cast, congelar no 5º frame até sair o projétil então
finaliza a animação."

**Pesquisa feita antes de codar — correção de rumo no meio do processo**:
a 1ª suposição (perguntar ao usuário "quando começa a fase 1?"
assumindo que não havia nenhum sinal local de antecipação pro
auto-attack) estava ERRADA — o usuário corrigiu diretamente: "na
verdade existe assim, emite um som de tensionar a corda do arco (puxar
a flecha) antes de soltar, investigue mais." Achei o mecanismo real em
`ui/player_input_system.py::_process_archer_combat` — pré-tensionamento
local já existente (`_PRE_DRAW_THRESHOLD = 1.0`s,
`combat_stats.arrow_pre_draw_ready`, toca `arrow_nock_1`/`arrow_nock_2`
~1s antes do próximo disparo, 30% de chance) — é EXATAMENTE o sinal de
timing que faltava pra fase 1, sem precisar inventar nenhum delay
artificial novo. Evita a classe de erro já documentada no projeto:
reciclar uma suposição anterior em vez de pesquisar a decisão
ESPECÍFICA em jogo (mesma lição de §12/§13, diferente mecanismo).

**Desenho — primitiva genérica, reaproveitável por qualquer classe**:
`engine/character_animation.py::trigger_anticipation_anim(tile_movement,
class_id, state_name, now_ms, hold_frame_index=5, frame_ms=DEFAULT_
FRAME_MS, sid="")` — toca o sheet normalmente até `hold_frame_index`
(inclusive) e CONGELA ali via `TileMovement.anim_attack_frame_cap_ms`
(campo novo, `int`, trava o `elapsed` calculado em
`resolve_display_state()` nesse teto, antes de qualquer outro cálculo
de duração/loop). `release_anticipation_anim(tile_movement, now_ms)` —
rebate `anim_attack_started_ms` pra trás pelo valor do teto (nunca
reinicia do frame 0), remove o teto, desliga `hold_last_frame` — a
animação passa a avançar sozinha de onde congelou até o fim do sheet,
depois cai pra idle/run pelo mecanismo normal já existente.
`hold_frame_index` é PARÂMETRO por chamador (pedido explícito do
usuário, "configurável por animação") — nunca uma constante fixa pro
jogo inteiro, já que sheets de classes diferentes podem ter contagens
de frame diferentes.

**`trigger_cast_anim()` (§284) reescrito pela 3ª vez na mesma sessão**
para delegar a esta primitiva em vez da técnica de "esticar pra caber
a duração real" — cast agora É um caso de antecipação (fase 1 = desde
o início do cast até o frame 5; fase 2 = liberada no evento
`SKILL_EFFECT` de `"launch"`/`"impact"`/`"miss"`, já existente desde
§281, sem mensagem de rede nova). Cada reescrita anterior deixou código
morto que foi removido no mesmo commit (`anim_attack_is_cast`, criado
em §281 e já removido em §284, não reaparece aqui).

**Gatilhos finais, 2 call sites**:
- Fase 1 (puxar o arco) — `ui/player_input_system.py::
  _process_archer_combat`, no MESMO bloco do som de pré-tensionamento
  (fora do `if random.random() < 0.30:` do som — a animação liga a
  CADA ciclo de pré-tensionamento, independente do som ter tocado ou
  não, já que o som é só 30% de chance mas a "preparação visual" deve
  ser sempre visível).
- Fase 2 (soltar) — 2 pontos, cobrindo local E remoto, ambos com
  fallback pro `trigger_attack_anim()` normal quando não havia
  antecipação ativa (ex: player remoto, que nunca passa pela fase 1
  local de outro jogador; ou primeiro golpe antes do pré-tensionamento
  disparar):
  - `client/network_handlers.py::_handle_msg_skill_effect` — cast de
    QUALQUER classe (`"launch"`/`"impact"`/`"miss"`).
  - `client/remote_entity_handlers.py::_apply_combat_result` — auto-
    attack (`sid==""`), via COMBAT_RESULT (confirmação real do servidor
    de que o golpe aconteceu).

**Teste**: suíte de animação de personagem/mob/nameplate/portal (189
testes relevantes) sem regressão rodando isolada por arquivo; rodando
os 7 arquivos juntos, 2 testes de `test_mob_death_animation.py`
(`TestLootSystemDrawsFrozenFrame`) falham por POLUIÇÃO DE ORDEM DE
TESTE (superfície pygame compartilhada entre arquivos — confirmado
rodando `test_mob_death_animation.py` isolado: 17/17 passam; e rodando
a suíte INTEIRA sem nenhuma mudança deste commit, via `git stash`: os
mesmos 2 testes também não aparecem isolados como quebra — a causa é
pré-existente e não relacionada a este fix, não investigada a fundo
por estar fora do escopo desta tarefa). Decisão do usuário sobre nível
de teste automatizado para esta feature específica não foi pedida
antes de implementar (diferente do padrão desta mesma sessão) —
validação combinada com manual em playtest, a confirmar.

## 55. Cast sem sprite dedicado nunca animava + cauda de golpe acelerada em colisão (15/10/2026)

**Pedido do usuário**, 2 partes no mesmo turno: (1) "a animação do auto
attack está certinho mas o cast não, é para a animação acontecer no
início do cast e travar no 5º frame e continuar a animação após o
ataque sair"; (2) "eu também percebi que gera uma confusão na animação
quando sai um auto attack logo depois de uma skill ser lançada, então
quando isso acontecer, você pode acelerar a animação para casar com o
disparo do projétil."

**Causa raiz (1) — confirmada por investigação, não suposição**:
`ls assets/sprites | grep arqueiro` mostrou que o arqueiro não tem
NENHUM sprite de cast (`arqueiro_cast_*.png`) nem sprite próprio por
skill (`arqueiro_tiro_repulsivo_*.png` e similares, nenhum existe).
`trigger_cast_anim()` (§286) só olhava `resolve_cast_state_name`
(cascata `{sid}_cast` → `"cast"` → `None`) — sem achar nenhum dos
dois, virava no-op completo, personagem ficava em idle/run o cast
inteiro. Fix: quando `resolve_cast_state_name` devolve `None`, cai pro
MESMO `resolve_attack_state_name` (cascata sid→"skill"→"attack") já
usado pelo lançamento — reusa o sheet de ataque pra fase 1 também, sem
nenhuma arte nova. Mago (que TEM sprite de cast dedicado) não muda —
o fallback só entra quando a cascata de cast genuinamente não acha
nada.

**Causa raiz (2)**: como o arqueiro usa o MESMO sheet `"attack"` pra
TUDO (windup de skill, soltura de skill, pré-tensionamento de
auto-attack, soltura de auto-attack — nenhuma skill tem sprite
própria), o pré-tensionamento LOCAL do próximo auto-attack (`ui/
player_input_system.py`, timer independente, ~1s antes do próximo
tiro) podia disparar e congelar o sheet de volta no frame 5 ENQUANTO a
cauda de uma skill recém-lançada ainda estava tocando (ela não trava,
só termina o ciclo e cai pro idle) — virava um "pulo pra trás" visível
no meio do movimento.

**Desenho aprovado pelo usuário** (`AskUserQuestion`, 3 opções —
acelerar/cortar-pro-último-frame/esperar — escolheu "acelera a cauda
antiga até o fim"): `engine.character_animation._accelerate_tail_and_
queue(tile_movement, class_id, now_ms, pending)` — quando um gatilho
novo (`trigger_attack_anim`/`trigger_anticipation_anim`) precisa
começar enquanto `_tail_still_playing()` detecta uma cauda ainda
tocando (não congelada por anticipação, não em loop, não held, ainda
não no último frame), REBASEIA o timeline da cauda pra manter o frame
ATUAL intacto mas avançar daí pra frente a `_TAIL_ACCELERATE_FRAME_MS`
(25ms/frame, rápido mas visível, nunca instantâneo) — e guarda o
gatilho novo em `TileMovement.anim_attack_pending` (tupla, campo novo)
em vez de aplicar na hora. `resolve_display_state()` consome e limpa
esse pending sozinho no EXATO frame em que a cauda acelerada chega ao
fim natural (recursão de profundidade 1, sempre termina — pending é
limpo ANTES de reaplicar). Protege os 2 pontos que já tinham prioridade
própria e nunca devem ser tocados por isto: golpe em LOOP de verdade
(`_attack_loop_still_active`, guard separado, continua intocado) e
anticipação JÁ congelada (`anim_attack_frame_cap_ms>=0` — por
construção, nunca pode coexistir com um `anim_attack_pending` não-nulo,
ver docstring de `_tail_still_playing`).

**Bug lateral achado implementando, corrigido no mesmo commit**:
`trigger_death_anim()` nunca resetava `anim_attack_frame_cap_ms` — se
o hit fatal acontecesse bem no meio de uma anticipação congelada
(janela rara, nunca reportada), a morte herdava o teto antigo e a
animação de morte travava no frame ERRADO (o da anticipação), não no
último frame de verdade. `clear_attack_anim_state()` (revive) ganhou o
mesmo reset, mais a limpeza de `anim_attack_pending` — nenhum dos 2
campos novos expira sozinho, mesma disciplina já documentada pra
`anim_attack_hold_last_frame`.

**Teste**: suíte de animação/mob/nameplate/portal/seleção (199 testes,
7 arquivos juntos) passa 100% depois do fix. 1 teste pré-existente
(`test_character_animation.py::TestApplyCombatResultTriggersAttackAnim::
test_miss_liga_animacao_generica_mais_lenta`) quebrou como efeito
colateral ESPERADO da mudança — ele disparava 3 resultados de combate
em sequência síncrona sem avançar o tempo real entre eles (suposição
antiga: "toda chamada sobrescreve na hora", agora só verdade quando a
cauda anterior já tinha tempo de terminar) — corrigido fazendo o teste
avançar `pygame.time.get_ticks()` de verdade (patch) entre os 3
outcomes, igual aconteceria em gameplay real (resultados nunca chegam
no mesmo milissegundo). Os 2 testes de `test_mob_death_animation.py`
que falhavam só em combinação com outros arquivos (§286, poluição de
ordem pré-existente) não reapareceram nesta rodada combinada — não
investigado a fundo, segue fora do escopo. Validação do comportamento
visual real (cast do arqueiro aparecendo, cauda acelerada sem pulo)
ainda depende de playtest manual do usuário.

## 56. §286/§287 revertidos no mesmo dia: congelamento bloqueava a transição pra andar

**Sintoma relatado pelo usuário, em playtest real** (a validação que
§286/§287 deixaram pendente): "não ficou bom, o congelamento do frame
está atrapalhando as outras animações, por exemplo quando o personagem
está congelado no 5º frame e ele começa andar, continua congelado
nesse frame." Causa raiz: `resolve_display_state()` sempre dá
prioridade a golpe/cast sobre idle/run enquanto o estado está ativo —
regra que já existia ANTES de §286 (golpe normal de 1 ciclo já segue
essa prioridade, decisão de 09/09/2026) e é aceitável quando o estado
tem duração curta e previsível. O problema específico de §286 é que a
ANTICIPAÇÃO (congelar no frame 5 esperando o evento de rede de
lançamento) não tem prazo nenhum por construção — fica presa até
`release_anticipation_anim()` chegar, o que podia ser quase 1s depois
(pré-tensionamento do arqueiro) — tempo de sobra pro jogador já ter
soltado WASD e começado a andar de novo, com o personagem visualmente
preso na pose de "puxando o arco" o tempo todo.

**Decisão do usuário**: em vez de ensinar `resolve_display_state()` a
ceder pra andar no meio de uma anticipação congelada (o que exigiria
decidir o que fazer quando o jogador parar de andar de novo — retomar
o congelamento? perder a sincronia com o som de pré-tensionamento?),
o usuário optou por uma solução mais simples na ORIGEM do problema:
redesenhar as próprias folhas de sprite pra que o frame de soltar a
flecha fique no INÍCIO do ciclo, não no fim — eliminando a necessidade
de qualquer congelamento/anticipação. "vou mexer nos frames para fazer
com que o lançamento da flecha seja no início da animação e não no
final, então vc pode reverter o que fez e deixar a animação normal."

**Fix**: `git revert --no-commit` dos 2 commits de §286/§287
(`fbdfdc4`, `c6fc409`) nos arquivos de CÓDIGO (`engine/
character_animation.py`, `engine/components.py`, `client/
network_handlers.py`, `client/remote_entity_handlers.py`, `ui/
player_input_system.py`, `tests/test_character_animation.py`) —
`git checkout HEAD -- CLAUDE.md arquitetura/PROBLEMAS_ARQUITETURA.md`
logo depois pra manter o HISTÓRICO destas 2 seções intacto (nunca
apagar o registro do que foi tentado, mesma régua de §85/§283) e
escrever esta entrada nova à mão em vez de deixar o revert mudar a
documentação sozinho. Suíte de animação/mob/nameplate/portal/seleção
(199 testes) voltou a passar 100% no estado revertido, confirmando que
o código voltou exatamente ao baseline de antes de §286
(`trigger_cast_anim` de volta à técnica ESTICADA de §284;
`trigger_attack_anim`/COMBAT_RESULT sem fase de anticipação nenhuma).

**Removido de verdade (não existe mais no código)**:
`trigger_anticipation_anim`/`release_anticipation_anim`,
`TileMovement.anim_attack_frame_cap_ms`/`anim_attack_pending`,
`_accelerate_tail_and_queue`/`_tail_still_playing`/`_apply_pending_
attack_trigger`, o fallback de `trigger_cast_anim` pra `resolve_
attack_state_name` quando não há sprite de cast dedicado, e o gatilho
de fase 1 em `ui/player_input_system.py::_process_archer_combat`.
**Se uma técnica de "congelar num frame do meio esperando um evento de
rede" voltar a ser necessária no futuro** (pra qualquer classe/skill),
ler esta seção E §286/§287 inteiras antes de reimplementar — o desenho
técnico em si funcionava (testes confirmavam o mecanismo), o problema
real era a INTERAÇÃO com movimento livre durante a espera, não um bug
de implementação. Qualquer tentativa futura precisa responder essa
pergunta ANTES de codar: "o que a animação deve mostrar se o jogador
andar enquanto está esperando o evento de liberação?" — nunca assumir
que "fica parado esperando" é aceitável sem confirmar com o usuário.

## 57. Sprite idle-down real nas telas de seleção/criação de personagem (19/10/2026)

**Pedido do usuário**: "na tela de seleção de personagem e na tela de
criação de personagem, quero que coloque o sprite idle down de cada
classe no lugar do retângulo."

**Investigação**: as 2 telas vivem no MESMO arquivo
(`ui/char_creation_screen.py`) — `run_online()` (chamada de
`main.py::_char_run`) despacha entre `_draw_selection()` (lista de
personagens existentes, ícone de classe de 36x36 ao lado do nome) e
`_run_creation()` (criar personagem novo, ícone de classe de 36x36
dentro de cada card das 3 classes). Os 2 ícones eram
`pygame.draw.rect()` preenchido com `_CLASS_COLORS[class_id]` — nenhum
sprite de verdade.

**Desenho, 2 perguntas feitas ao usuário antes de codar**:
1. Animado (loop idle, mesmo mecanismo do jogo) vs frame estático —
   escolhido ANIMADO.
2. Tamanho: manter 36x36 (encolhido) vs aumentar a área — usuário
   esclareceu que o frame real é 64x64 mas o personagem desenhado só
   ocupa os 32x32 centrais (mesma convenção de `CHAR_COLLISION_SIZE`
   documentada pro resto do jogo) — pediu pra usar o FRAME INTEIRO
   (64x64) centralizado, nunca recortar/dar zoom tentando "preencher"
   uma caixa pequena.

**Fix**: `_get_class_preview_frame(class_id, sc)` (novo, módulo) —
chama `engine.character_animation.get_character_frame(class_id,
"idle", "down", pygame.time.get_ticks())` (MESMO mecanismo de
animação do jogo, zero código novo de animação) e escala o frame
(64px base) por `sc` — igual todo resto desta tela já escala por
resolução — devolve `None` se não existir sprite ainda (fallback pro
retângulo de cor ANTIGO, mesmo padrão de "sem arte ainda" usado no
resto do projeto, zero regressão pra uma classe futura sem sprite).

- **`_draw_selection`**: ícone passou de 36x36 fixo pra `frame.
  get_width()` (64×sc) — texto (nome/classe/data) ao lado do ícone
  usava um deslocamento HARDCODED (`r.x + 64*sc`, coincidência do
  valor antigo já ser 64) — virou `text_x = icon_x + icon_sz +
  14*sc`, calculado a partir do tamanho REAL do ícone (sprite ou
  fallback de cor) — nunca mais quebra se o tamanho do preview mudar
  de novo no futuro.
- **`_run_creation`** (cards de classe): label/descrição/"[em breve]"
  usavam offsets Y hardcoded (ex: `r.y + 64*sc` pro nome) assumindo um
  ícone de 36px — virou cálculo SEQUENCIAL a partir do fundo real do
  ícone (`icon_bottom = icon_top + preview_sz`, depois cada elemento
  seguinte usa o fundo do anterior + gap) — robusto a qualquer tamanho
  de preview futuro, não só o atual. `CARD_H` (altura do card) subiu
  de 160 pra 185 pra caber o sprite maior sem sobrepor o texto —
  conferido por script (baseline sc=1.0): conteúdo real termina em
  y=448, card termina em y=505 (57px de folga), botões Voltar/
  Confirmar começam em y=530 (25px de folga do card) — zero overlap.

**Teste**: `tests/test_client_ui.py` (288 testes, nenhum toca
`_draw_selection`/`_run_creation` diretamente — só `_drain_char_
select_batch`, lógica de rede, intocada) sem regressão. Sem suíte
automatizada pro RENDER em si (telas pré-jogo, sem precedente de teste
de layout no projeto) — validado por 2 smoke scripts manuais nesta
sessão: (1) `_get_class_preview_frame` devolve Surface 64x64 pras 3
classes reais + `_draw_selection` roda sem exceção com dados
sintéticos; (2) replica o corpo de desenho dos cards de `_run_creation`
fora do loop bloqueante, confirma que o conteúdo cabe dentro do card
novo (sem a tela real abrir, só matemática de posição). Validação
visual de verdade (como fica na tela, de perto) ainda depende de
playtest manual do usuário.

## 58. Preview de classe em 200% (128x128) — painel de criação cresce, lista de seleção "espia" fora da linha (19/10/2026)

**Pedido do usuário**: "o tamanho da sprite não está legal, quero que
tenha o tamanho aumentado em 200%, para testar, que no caso o boneco
que tem 32x32, vai ter 64x64." (o frame REAL do sprite é 64x64, com o
personagem ocupando o tile central de 32x32 — "boneco 32→64" = frame
64→128).

**Investigação ANTES de mudar o número**: `_CLASS_PREVIEW_BASE_SIZE`
é usado pelas 2 telas; mudar só a constante funcionava de graça na
tela de CRIAÇÃO (3 cards lado a lado, o layout já calculava posições
dinamicamente a partir do tamanho real do ícone desde §291), mas
quebrava a tela de SELEÇÃO: um ícone de 128px numa linha de lista de
84px de altura (`_BASE_SLOT_H`), dentro de um painel de altura FIXA
(520px) pensado pra caber exatamente 3 linhas + rodapé de botões —
crescer a linha pra caber o ícone sem cortar nada exigiria um painel
de ~780px, maior que uma janela 720p inteira (nasceria cortado no
topo). Calculado e apresentado ao usuário ANTES de codar, com 3
opções — escolhida: o sprite "espia" por cima da linha (anel de
fundo da linha continua do tamanho de sempre, só a ARTE desenha por
cima, sem cortar).

**Fix — 2 telas, tratamento DIFERENTE por design, não por limitação**:
- **Criação** (`_run_creation`): só recalcula `CARD_H` (185→212,
  conferido por script: conteúdo real termina em y=192 na base 720p,
  212 deixa ~20px de folga) e `PH` (460→500, mesma folga de ~18px até
  os botões Voltar/Confirmar que o design original já tinha, só
  realocada pro painel maior). Nenhuma mudança de MECANISMO — mesmo
  cálculo sequencial (`icon_bottom` → label → descrição) de §291,
  só com números maiores.
- **Seleção** (`_draw_selection`): preview vira um **2º passe de
  desenho**, DEPOIS de todas as linhas/botões (`_pending_previews`,
  lista coletada durante o loop principal, desenhada por ÚLTIMO) —
  sem isso, a linha SEGUINTE (desenhada depois, na ordem natural do
  loop) cobriria a parte de baixo do sprite que "espiou" da linha
  ANTERIOR. Posição X/Y do texto (nome/classe/data) passou a usar
  `_LIST_ICON_NOMINAL` (64px, fixo) em vez do tamanho REAL do sprite —
  o texto nunca se move só porque o preview mudou de tamanho; só a
  arte cresce/encolhe livremente por cima. Testado que o overflow
  (22px acima da linha 0, base 720p: `(128-84)/2`) não alcança o
  título da tela (`title` termina ~py+50, linha 0 começa em
  `py+slot_h=py+84`, folga de 12px mesmo com o overflow).

**Teste**: `tests/test_client_ui.py` (252 testes) sem regressão — não
toca render de char_creation_screen diretamente (ver §291). 2 smoke
scripts refeitos nesta sessão confirmam: (1) `_get_class_preview_frame`
devolve 128x128 pras 3 classes + `_draw_selection` roda sem exceção
com o 2º passe novo; (2) layout da criação (`CARD_H`/`PH` novos) cabe
sem sobrepor botões, folga de 38px conferida por script. Validação
visual de verdade (como o "espiar por cima" fica na prática, se o
usuário gosta do tamanho 200% ou quer calibrar diferente) ainda
depende de playtest manual — pedido do usuário foi explicitamente
"para testar", não necessariamente o valor final.

## 59. Imagem de fundo nas telas de login e loading do client (19/10/2026)

**Pedido do usuário**: "quero que coloque a imagem
`assets/content/login_screen.png` na tela de login do client, e não do
launcher, e na tela de loading a `assets/content/loading_screen.png`
como fundo de tela."

**Investigação**: tela de login é `ui/login_screen.py::run()` (loop
bloqueante, roda ANTES de qualquer `GameEngine` existir — nunca
confundir com `launcher/login_ui.py`, telas DIFERENTES, uma é o jogo e
a outra o launcher de atualização, só o jogo foi pedido). Tela de
loading é `client/habilidades_handlers.py::_draw_loading_screen`
(mixin de `GameEngine`, nome do arquivo é legado — o método mora ali
desde antes de existir arquivo próprio pra loading screen).

**Fix**: as 2 imagens (800×446 e 800×441, ambas ~16:9) são carregadas
1x via `ui/asset_cache.py::get_image()` (ponto único de decode de
imagem do cliente — nunca `pygame.image.load` direto) e escaladas pra
cobrir a janela inteira (`pygame.transform.scale`), com um cache
PRÓPRIO por tela indexado por `(largura, altura)` da janela — só
recalcula o scale se a janela mudar de tamanho, nunca todo frame.
Substituem o `screen.fill(cor sólida)` de cada tela; sem o arquivo no
disco (`get_image` devolve `None`), cai pro fundo sólido de sempre —
mesmo padrão de fallback usado em todo o projeto pra "sem arte ainda".
Resto do desenho de cada tela (painel de login, gradiente/barra de
progresso do loading) continua por cima, sem mudança.

**Teste**: `tests/test_loading_screen.py`, `tests/test_sound_muted_
during_loading.py`, `tests/test_main_reconnect_login_fallback.py`,
`tests/test_mid_session_reconnect.py`, `tests/test_client_ui.py` (264
testes) sem regressão. Smoke scripts confirmam que as 2 funções de
background carregam e escalam certo pras 2 imagens reais, e que o
método `_draw_loading_screen` roda ponta a ponta sem exceção com o
background novo. Validação visual de verdade (como as imagens ficam
atrás do painel/barra de progresso) ainda depende de playtest manual
do usuário.

## 60. Fundo da tela de loading sorteado entre variantes "_1/_2/_3" (19/10/2026)

**Pedido do usuário**: "é possível fazer com que a tela de loading
varie? Se eu colocar diferentes imagens com o mesmo nome e '_1/_2/_3'
no nome, você consegue escolher elas aleatoriamente?"

**Desenho — reusa um padrão JÁ EXISTENTE no projeto**, em vez de
inventar um novo: `engine/entity_disguise.py::discover_sprite_variants`
já faz descoberta SEQUENCIAL de variantes numeradas (`os.path.exists`
um a um, para no primeiro buraco) — mesma técnica aplicada aqui pra
`loading_screen_1.png`, `_2`, `_3`... NUNCA `glob` (ordem de glob não é
garantida, poderia incluir arquivo não relacionado por engano).
`loading_screen.png` (sem sufixo, já em uso desde §59) sempre entra no
sorteio também, nunca é tratado como "_1" — conceito separado, pra quem
só tinha o arquivo único de antes continuar funcionando sem precisar
renomear nada.

**Fix**: `_choose_loading_bg_path()` sorteia 1 caminho entre os
candidatos encontrados — só 1 VEZ por processo (cacheado em variável de
módulo com sentinela `_UNSET`, pra distinguir "ainda não sorteou" de
"sorteou None" — nenhum arquivo achado). Trocar de imagem no meio da
mesma sessão de jogo seria mais estranho que repetitivo, então a
escolha vale pro processo inteiro, não por "toda vez que a tela de
loading aparece" (reconexão, por exemplo, mostra a MESMA imagem
sorteada no boot).

**Limitação conhecida, herdada do padrão que foi reaproveitado**: a
numeração precisa ser CONSECUTIVA sem buraco (`_1`, `_2`, `_3`, nunca
pular pra `_5` direto) — a descoberta para no primeiro número ausente,
mesmo padrão de `discover_sprite_variants`. Testado explicitamente
(script manual): `_2` sozinho, sem `_1`, é ignorado por completo.

**Teste**: `tests/test_loading_screen.py`, `tests/test_sound_muted_
during_loading.py`, `tests/test_client_ui.py` (259 testes) sem
regressão. 3 scripts manuais confirmam: (1) com só o arquivo único de
hoje, o sorteio sempre escolhe ele; (2) com 4 arquivos fake
(base+_1+_2+_3), os 4 aparecem numa distribuição razoavelmente uniforme
em 300 sorteios; (3) um buraco na numeração (`_2` sem `_1`) corretamente
não encontra nada. Validação visual de verdade (ver as imagens
alternando entre reinícios do jogo) depende de playtest manual do
usuário, com mais de uma imagem de verdade no disco.

## 61. Barra de progresso da tela de loading vira sprite, escala igual ao nameplate (19/10/2026)

**Pedido do usuário**: usar `assets/content/loading_screen_bar.png`
centralizada no eixo horizontal e encostada ~20px acima do limite
inferior da tela; o marcador de cor `#291000` dentro da imagem marca o
início/tamanho do preenchimento, que deve terminar simetricamente do
outro lado; remover o título "RPG Online", manter só o status
("Carregando recursos"/"Carregando personagem") 5px ACIMA da barra; e
usar "a mesma escala que os nameplates dos personagens têm no jogo".

**Investigação**: escaneei a imagem pixel a pixel — a cor `#291000`
aparece só nas colunas 3-4, linhas 3-12 (imagem nativa 380×16). Pedi
confirmação ao usuário se essa cor era só um marcador de POSIÇÃO ou
também a cor de preenchimento de verdade — confirmou que é as 2 coisas
(ele já tinha dito isso na primeira mensagem, eu que não tinha certeza).
"Escala dos nameplates" → `ui/hud_bars.py::SCALE` (hoje 2x,
nearest-neighbor via `pygame.transform.scale`, usado por
`build_player_hud`/`build_mob_hud`/badge de NPC) — reusada direto, nunca
um número novo duplicado.

**Fix**: `_detect_bar_fill_rect(raw)` acha o retângulo de preenchimento
automaticamente a partir do marcador — margem horizontal SIMÉTRICA
(distância do marcador até a borda esquerda = até a direita, calculado
a partir da posição real do marcador, nunca um número fixo tipo "3px")
e altura/topo EXATOS do marcador (não deriva de simetria nenhuma,
intencional pelo tamanho do próprio marcador). Sem marcador encontrado
(PNG editado sem ele) → fallback de 3px de margem em todo lado, nunca
quebra. `_get_loading_bar()` carrega 1x, detecta o retângulo, e escala
TUDO (imagem + retângulo) por `SCALE` — cacheado em variável de módulo,
sem recalcular depois. Removidos: título "RPG Online" e a linha
decorativa que ficava logo abaixo dele (não fazia mais sentido sem o
título — layout inteiro passou a ser ancorado na barra, não mais
centralizado na tela). Dica de timeout reposicionada logo ABAIXO da
barra (mesmo espaçamento de 5-6px, espelhado). Sem o PNG da barra →
cai pro desenho antigo completo (retângulos de cor, posição central
antiga) — fallback preservado por inteiro, zero regressão se o arquivo
sumir.

**Teste**: `tests/test_loading_screen.py` tinha 3 testes que mediam a
barra por COORDENADA/COR fixas (geometria antiga, centro da tela,
verde) — todos quebraram com a mudança de posição/cor, como esperado
(teste validando o desenho ANTIGO, não um bug). Reescritos pra ler a
geometria/cor de `_get_loading_bar()` dinamicamente (nunca mais hardcoda
posição/cor que podem mudar de novo se o PNG ou o `SCALE` do nameplate
mudarem). Fixture de teste também precisou crescer de 640×480 pra
1280×720 — a barra escalada (760px) não cabia mais na tela de teste
antiga, causando `IndexError` ao ler pixel fora da Surface. `tests/
test_sound_muted_during_loading.py`/`tests/test_client_ui.py` (259
testes) sem regressão. Validação visual de verdade (posição/tamanho
reais na tela, "ficou do jeito que eu descrevi") ainda depende de
playtest manual do usuário.

## 62. Contorno no texto da tela de loading + progresso único entre as 2 fases (19/10/2026)

**Pedido do usuário**: "preciso que você coloque contorno nas letras
do texto de carregamento pois não dá pra ler do jeito que está, a
letra é clara assim como o fundo"; e "não dá para fazer o carregamento
dos recursos e do personagem na mesma barra? ou seja, a evolução dos 2
ser uma só em vez de 2 carregamentos da barra."

**Contorno**: reusa `ui/floating_text.py::_render_outlined` (ponto
único de verdade de texto com contorno já usado no FLT de dano, nunca
uma 2ª implementação) — texto do status ("Carregando recursos.../
Carregando personagem...") e da dica de timeout, `thickness=2`. Cor do
texto em si NÃO mudou (continua a mesma — o pedido foi contorno, não
recor; mudar as duas coisas ao mesmo tempo teria sido decidir uma cor
nova sozinho sem necessidade).

**Progresso único**: antes, cada fase ("assets" = preload rodando,
"server" = aguardando LOGIN_OK) reiniciava seu próprio ciclo 0%→100% —
a barra "resetava" visualmente na troca de fase. Agora
`_ASSETS_PHASE_WEIGHT` (0.5, metade pra cada fase — ponto de partida
razoável sem dado real de duração relativa de cada uma, fácil de
recalibrar depois) reserva a fração `[0, 0.5]` pra "assets" e `[0.5,
1.0]` pra "server" — 1 progressão contínua, nunca mais 2 ciclos
separados. Verificado por cálculo direto: assets 0%→50%→100% mapeia
pra combined 0.0→0.25→0.5; server início→fim mapeia pra 0.5→1.0 — sem
salto/reset na transição.

**Teste**: `tests/test_loading_screen.py` tinha 1 teste que esperava
"preload 100% = barra quase cheia" (comportamento ANTIGO, 2 ciclos
separados) — reescrito pra esperar 50% (peso da fase assets), já que
"quase cheia" deixou de ser verdade de propósito com o progresso
único. Docstring do módulo de teste atualizado pra não descrever mais
"2 fases = 2 barras". `tests/test_sound_muted_during_loading.py`/
`tests/test_client_ui.py` (259 testes) sem regressão. Validação visual
de verdade (texto legível na prática, sensação de "uma barra só"
durante o boot real) ainda depende de playtest manual do usuário.

## 63. Build do cliente e git passam a excluir material de origem de assets/content (19/10/2026)

`assets/content/` guardava, misturados na mesma pasta: (a) packs
craftpix/PSD/PNG/Tiled_files/frames/mago/aranha_anim_v21 — material de
ORIGEM pra gerar sprites finais em `assets/sprites/`, nunca carregado
em runtime — e (b) arquivos soltos na RAIZ (`login_screen.png`,
`loading_screen_1.png`...`_5.png`, `loading_screen_bar.png`)
carregados de verdade em runtime pelas features de §59-§62 acima.
Pedido do usuário foi "ignorar a pasta content nos commits e pra gerar
o executável" — uma 1ª tentativa literal (`.gitignore`/robocopy `/XD
"content"` excluindo a pasta INTEIRA) teria quebrado login/loading no
executável, descoberta ANTES de comitar (grep em `*.py` confirmou os 2
consumidores). Corrigido pra excluir só as 7 SUBPASTAS de origem pelo
nome (`.gitignore`: `assets/content/PNG/`, `.../PSD/`, `.../Tiled_
files/`, `.../frames/`, `.../mago/`, `.../aranha_anim_v21/`, `.../
craftpix-net-*/`; `release_tools/build_client.ps1`: mesmo conjunto via
`/XD` no robocopy de `assets/`) — os arquivos soltos na raiz continuam
versionados e no executável. `git rm -r --cached` removeu 3493
arquivos do índice (nada apagado do disco). Build validado: executável
gerado (104,6 MB) com os PNGs de runtime presentes e as 7 subpastas
ausentes. Ver CLAUDE.md pra nunca reintroduzir uma exclusão "pasta
inteira" sem checar arquivos soltos na raiz primeiro.

## 64. Imagens de UI (login/loading/launcher) movidas pra assets/ui/ (19/10/2026)

Usuário reorganizou manualmente: `login_screen.png`, `loading_screen_1.
png`...`_5.png`, `loading_screen_bar.png` saíram de `assets/content/`
(onde foram colocadas na pressa de §59-§63, misturadas com material de
origem) e `Launcher.png` saiu de `assets/hud/` (onde nunca pertenceu de
verdade — não é uma barra de HP/nameplate como os outros arquivos
daquela pasta) — todas as 7 imagens foram pra uma pasta nova
`assets/ui/`, dedicada a arte de telas de UI fora do mundo de jogo.
Atualizados os 3 pontos que liam o caminho antigo:
`client/habilidades_handlers.py::_LOADING_BG_BASE`/
`_LOADING_BAR_IMG_PATH` (tela de loading), `ui/login_screen.py::
_BG_IMG_PATH` (tela de login do jogo) e `launcher/login_ui.py::
_ASSET_PATH` (janela do Launcher) — nenhuma lógica mudou, só o
caminho. `arquitetura/ARQUITETURA ONLINE.md` (seção do Launcher)
também atualizada pra refletir o novo caminho. Como consequência
colateral boa: `assets/content/` agora só tem as 7 subpastas de origem
(§63) + 4 arquivos soltos minúsculos (license/readme/desktop.ini/
mask.ase) — nenhum arquivo de runtime real sobrou lá, mas a exclusão
por subpasta de §63 continua certa do mesmo jeito (nunca excluir a
pasta `content` inteira sem reconferir se algo novo foi colocado solto
na raiz dela). Validado: `tests/test_loading_screen.py` +
`tests/test_client_ui.py` (256 testes) sem regressão; paths
confirmados existentes em disco antes do commit.

## 65. Atlas de UI por tiles 16x16 — 1ª tela: configuração de som (20/10/2026)

**Pedido do usuário**: trocar o visual de 6 janelas (diálogo de quest,
menu principal, configuração de som, seleção de personagem, inventário,
criação de personagem) pelo atlas `assets/ui/ui_elements.png`, seguindo
o mockup `assets/content/exemplo.png`, na MESMA escala dos nameplates.
Rollout 1 tela por vez (decisão do usuário) — 1ª: configuração de som.

**Como o atlas funciona (o que eu entendi errado na 1ª tentativa)**: é
uma GRADE DE TILES 16x16 (256x256 = 16x16 tiles). Janelas são montadas
como um mapa — cantos, bordas e miolo REPETIDOS, nunca esticados — e
cada elemento (ícone, seta, botão, trilho) ocupa tiles inteiros
alinhados à grade. A 1ª tentativa recortou peças por "caixa de
contorno" (pegou pedaço de elemento vizinho — o trilho veio contaminado
pelos quadrados de slot logo abaixo) e esticou o painel via 9-slice;
resultado desalinhado, rejeitado pelo usuário em playtest. Correção:
mapeamento extraído do mockup comparando cada tile 16x16 dele com os
tiles do atlas pixel a pixel (igualdade exata + busca de padrão nos
tiles com sombra semi-transparente) — nunca escolhido de olho.

**Implementação**: `ui/ui_atlas.py` (novo, ponto único) — `blit_tile`/
`blit_tile_px` (carimba tile), `wood_panel_raw(tw, th)` (painel de
madeira; bordas com variante decorativa determinística por posição;
miolo sempre o tile liso (1,10); cacheado), `blit_button` (3 colunas:
pontas fixas + meio repetido, 2 linhas; idle nas colunas 6-8,
pressionado nas 9-11, linhas 10-11; arte só na faixa y 10..24 do
bloco), `scale_up` (SCALE fixo de `ui/hud_bars.py`, nearest-neighbor).
Tudo composto em espaço RAW e ampliado 1x no fim — nunca a escala de UI
do jogador.

`client/menu_handlers.py::_draw_sound_submenu` reescrita: painel 9x11
tiles; Música (linha 3) e Efeitos (linha 6), cada um com ícone (clicar
= mudo/desmudo; 0% de volume também mostra o ícone desligado, mesmo com
o som ligado — clicar no ícone em 0% não muda nada visível, decisão do
usuário), setas (clique = ±1%; segurar = ±1 barrinha a cada 250ms, com
o tile "pressionado" enquanto segura), trilho (13,4) com barrinhas
(14,4) coladas a cada 3px, proporcionais ao volume (21 cabem no
trilho; arrastar sobre o trilho ajusta direto — as setas não iniciam
arraste, era o que fazia um clique pular pra 0%/100%) e percentual
acima. Sem rótulos "Music"/"Effects" (o ícone identifica a linha, igual
ao mockup). Botão "Voltar" age no SOLTAR (afunda enquanto segura;
soltar fora cancela) — agindo no clique, a janela fechava no mesmo
frame e o estado pressionado nunca aparecia. Estado novo em `game.py`:
`_sound_arrow_hold`, `_sound_back_pressed` (resetados junto de
`_sound_drag`). `_mm_panel`/`_mm_button` (outros submenus) ficam
intocados até a próxima tela.

**Teste**: `tests/test_ui_atlas.py` — painel copia tiles exatos sem
esticar, botão repete o tile do meio, ícone muta, 0% = ícone desligado,
arrastar proporcional, seta ±1% sem virar arraste, segurar = 1
barrinha/250ms com relógio simulado, Voltar age no soltar e soltar fora
cancela. Achado de infraestrutura de teste: `pygame.display.set_mode()`
no nível do módulo vaza pro resto da sessão do pytest (quebrou
`test_clique_na_sidebar_troca_categoria`) — o tamanho maior de janela é
setado e desfeito por teste. Validado em jogo pelo usuário.

## 66. Menu de pausa inteiro no atlas de UI + larguras a partir do texto (20/10/2026)

Continuação de §65. `client/menu_handlers.py` reescrito com helpers
comuns: `_mm_begin` (painel de madeira em tiles + mouse em espaço RAW),
`_mm_button` (age no SOLTAR, id em `self._mm_pressed` — substitui o
`_sound_back_pressed` só da tela de som), `_mm_selector` (◀ texto ▶ com
as setas do atlas), `_mm_text`, `_mm_end` (amplia, desenha textos).
Telas: **Menu** (botões "baixos" — cópia de 1 linha do botão, linha 9
do atlas — empilhados como no mockup), **Sair** (Sim/Não), **Resolução**
(2 seletores: escala e Tela cheia/Janela — decisão do usuário: "tudo em
setas"), **Interface** (◀ 1.25× ▶ — decisão do usuário: só setas +
valor), **Som** (§65). Textos traduzidos pro português (decisão do
usuário). `_mm_panel`/`_mm_button` antigos e `UI.MENU_*_W/H`
(`ui/ui_sizes.py`) removidos — tamanho agora é em tiles.

**Bug achado em playtest**: larguras fixas em tiles faziam o texto
vazar do botão ("Atalhos do teclado") e passar por cima das setas
("1.5x — 1920 × 1080") — a fonte acompanha a escala de UI do jogador
(1.25+ no jogo), o atlas não. Fix: `_mm_tiles_for(textos, mínimo)` mede
com a MESMA fonte do desenho (`font_md`) + folga de 6px RAW por lado;
botão, seletor (maior valor POSSÍVEL, pra janela não mudar de tamanho
ao trocar de opção) e janela crescem com o texto. Teste de regressão
simula escala de UI 2.0 (`test_textos_cabem_nos_botoes_e_seletores_com_fonte_grande`).

## 67. Modal "Atalhos do teclado" no atlas de UI + campo de texto + catálogo do atlas (20/10/2026)

Continuação de §65/§66. `client/hotbar_editor_handlers.py` reescrito no
estilo da janela de seleção de personagem do mockup (mapeada tile a
tile): painel ESCURO (decisão do usuário), 1 moldura vazada por seção —
Menus, Barra de Habilidades, Barra de Consumíveis (decisão do usuário;
1 moldura de 3 tiles por linha, como no mockup, deixaria 21 atalhos
longos demais), tecla de cada linha num **campo de texto** (tiles
(9,4)–(11,4) — o usuário definiu esse elemento como "campo de digitar
texto"), barra de rolagem na coluna da borda direita (setas clicáveis
com repetição a cada 250ms segurando, puxador arrastável, clique no
trilho pula o puxador, roda do mouse). Conteúdo montado inteiro num
Surface e recortado pela janela de rolagem. Cabeçalho "Ação/Tecla"
removido (as seções já dizem o que é cada coluna).

**Altura / área segura**: a 1ª versão cobria a barra de ações; a 2ª
encostava nela e nas bordas. Usuário pediu um PADRÃO de distanciamento
pra todas as janelas (marcação vermelha no print: ~11% da altura no
topo, ~65px acima da barra em 1080p). Decisão: `_mm_safe_area()` em
`client/menu_handlers.py` — margem de 10% da altura da tela no topo e
acima da barra de ações, 10% da largura nas laterais; `_mm_begin`
centraliza TODA janela do atlas nela (menu de pausa incluso), e o modal
de atalhos limita a altura a ela (lista rola). Largura continua pelo
conteúdo (decisão do usuário). Topo da barra via `_hotbar_top_y()` novo
em `client/hotbar_handlers.py` (mesma conta que `_draw_hotbar` usa pro
topo da linha de slots, que passou a chamar a função em vez de repetir
a conta). `_mm_begin` ganhou `style` ("wood"/"dark"); `_mm_text_field`
novo.

`ui/ui_atlas.py`: `panel_raw(estilo, tw, th)` (madeira + escuro),
`blit_frame`, `blit_text_field`, constantes da barra de rolagem.
Catálogo de TODAS as peças do atlas criado em `arquitetura/ATLAS_UI.md`
(pedido do usuário) — o que cada tile é e pra que serve; peças ainda
não usadas (barra de título/pergaminho, slots, paginação) marcadas como
pendentes de mapeamento. `UI.HOTBAR_EDITOR_W/MAX_H/SLOT_SZ/GAP` removidos.

**Teste**: `tests/test_ui_atlas.py` — clicar na tecla + KEYDOWN rebinda,
seta rola 1 linha e segurar repete, arrastar o puxador até o fim rola
tudo, roda do mouse, Fechar salva e fecha, modal e todas as janelas do
menu de pausa dentro da área segura (fonte normal e grande), textos
cabem com fonte grande.

## 68. Diálogo de quest no atlas de UI + `ui/atlas_window.py` (20/10/2026)

Continuação de §65–§67. Pedido do usuário: "podemos criar a janela de
diálogo das quests?". Decisões do usuário: (1) extrair os ajudantes de
janela do atlas pra um módulo comum; (2) ícones de recompensa no
pergaminho, no fim do texto (rolam junto), cada um no slot claro (9,5);
(3) "Concluir" quando falta escolher a recompensa = sprite normal com
texto cinza, sem ação.

**`ui/atlas_window.py` (novo)** — ponto único de montagem/interação de
janela do atlas, antes espalhado como `_mm_*` em
`client/menu_handlers.py`: `Frame` (painel centralizado na área segura,
mouse em espaço RAW, `button`/`text_field`/`selector`/`text`/`press`,
`child` pra conteúdo rolável, `scrollbar`, `end`), `PressState` (botão
afundado — age no soltar), `ScrollState` (rolagem + seta segurada +
puxador arrastado), `safe_area`, `tiles_for`, `draw_texts`. Menu de
pausa e modal de atalhos migrados; a barra de rolagem que era código
inline do modal de atalhos virou `Frame.scrollbar` (o diálogo de quest
usa a mesma). Estado no GameEngine: `_mm_press` (PressState),
`_hbe_scroll` (ScrollState; substitui `_mkb_scroll`/`_hbe_arrow_hold`/
`_hbe_grip_drag`).

**Diálogo de quest** (`ui/quest_system.py::QuestDialogSystem`) —
janela 1 do mockup mapeada tile a tile (`ui_atlas.quest_window_raw`):
barra de título com o nome do NPC (ou do item, quando aberto pela bag)
e X funcionando; pergaminho com texto escuro rolável; barra de rolagem
sempre visível na coluna da direita (como no mockup); rodapé escuro com
botões "altos" — Recusar/Aceitar nas pontas, Concluir ou Fechar
(lista) centralizado. Lista de quests vira linhas clicáveis no
pergaminho. Largura sai da largura de texto antiga (QUEST_DIALOG_W) em
tiles inteiros, mín. 11×13 tiles (tamanho do mockup); altura cresce com
o texto até o limite da área segura e depois rola. `handle_events` só
trata ESC e guarda os eventos; botões/linhas/slots agem no SOLTAR
dentro de `render()` (Frame). Área segura injetada por game.py
(`safe_area_fn = self._mm_safe_area`). `_hit_rects` guarda o rect de
tela de cada elemento clicável (pros testes). Textos com acento
corrigidos ("Missão Completa!", "Escolha uma missão:"). Removidos:
`_render_list/_detail/_turnin`, `_blit_scrollable`,
`_draw_reward_icon_row/_skill_icon`, rects `_accept_rect` & cia,
`_panel_origin`, `UI.QUEST_REWARD_ICON/_GAP`. Lógica de aceitar/
entregar/escolher (mensagens pro servidor, regra do talk_to_npc na
lista) inalterada — só mudou de lugar (`_accept`, `_complete`,
`_select_from_list`).

**Teste**: `tests/test_quest_reward_ui.py` (escolha de recompensa,
Concluir desabilitado, tooltip da skill, X fecha, soltar fora não
dispara, texto longo rola e fica na tela),
`tests/test_quest_dialog_list_turnin_click.py`, `tests/test_client_ui.py`
(aceitar/recusar pelo item) — clique completo via
`tests.helpers.click_quest_dialog`; `tests/test_ui_atlas.py` (menu e
atalhos no módulo novo).

**Ajuste pós-teste (20/10/2026, pedido do usuário)**: barra de rolagem
do diálogo só aparece quando o texto não cabe (antes ficava sempre
visível, como no mockup). **Teste de fonte REVERTIDO**: usuário achou
o texto pesado ("negrito") — a Determination é grossa por natureza, sem
versão regular. Comparadas fontes do sistema (Georgia/Segoe UI/Verdana)
e fontes pixel livres (Pixelify Sans/VT323/DotGothic16/Jersey 10);
usuário escolheu testar Pixelify Sans (grade nativa 13px) só no
diálogo de quest, viu no jogo e mandou desfazer ("ficou péssimo").
Diálogo voltou pra Determination; fonte e licença removidas de
`assets/fonts/`. Se o tema voltar: não repetir a Pixelify Sans.

## 69. Seleção de personagem no atlas de UI (20/10/2026)

Continuação de §65–§68. `ui/char_creation_screen.py::run_online`
redesenhada com a janela escura do mockup (canto superior direito de
`assets/content/exemplo.png`, mapeada tile a tile): título, 1 moldura
vazada de 3 linhas por personagem (preview animado + nome + classe/nível,
Jogar/Excluir à direita em botões "baixos"), coluna da direita cromada
(5,1) pra barra de rolagem (`ui_atlas.dark_list_window_raw`), Criar
Personagem/Sair empilhados embaixo. Decisões do usuário: sempre 3
molduras como no mockup, as sobrando mostram "Vazio"; com mais de 3
personagens a lista rola (barra só aparece quando precisa); Criar
Personagem no limite = texto cinza, sem ação; confirmação "Excluir
personagem?" também no atlas (janela escura pequena, Excluir/Cancelar).
Botões agem no soltar (`ui/atlas_window.py`). Janela centralizada na
área segura (tela sem barra de ações: margem de 10%).

Preview: mesmo `_get_class_preview_frame` (128px, frame 64×64 a 2×),
desenhado por ÚLTIMO no conteúdo (por cima das molduras vizinhas, mesma
decisão de §292) e recortado só pela área visível da lista — substitui
`_LIST_TEXT_OFFSET`/`_pending_previews` (texto agora fica numa coluna
fixa em px RAW, `_SEL_TEXT_X`).

Bug achado pelo teste novo, corrigido antes de chegar ao jogo: com a
confirmação aberta, a lista de fundo (desenhada antes) via o mouse
solto e zerava o botão afundado COMPARTILHADO antes da confirmação
disparar — Excluir/Cancelar nunca funcionariam. Fix: a lista de fundo
usa um `PressState` descartável enquanto a confirmação está aberta.

Removidos: `run()` (versão OFFLINE da seleção, lia saves locais — sem
nenhum chamador, só `run_online` é usado por `main.py`), retângulos de
geometria antigos (`_slot_rect`, `_play_btn_rect`, ...),
`_draw_confirm_overlay`, `_BASE_CHAR_W/H/SLOT_H/SLOT_PAD`. Tela de
CRIAÇÃO (`_run_creation`) continua no visual antigo — próxima tela.

**Teste**: `tests/test_char_select_ui.py` — Jogar/Excluir disparam no
soltar com o índice certo, apertar sem soltar não dispara, Criar/Sair,
Criar desabilitado no limite, 3 molduras mínimas e rolagem com 8,
janela na área segura com fonte grande, confirmação Excluir/Cancelar,
confirmação aberta bloqueia a lista.

## 70. Criação de personagem no atlas de UI (21/10/2026)

Continuação de §69. `ui/char_creation_screen.py::_run_creation` com a
janela escura do mockup (canto inferior de `assets/content/exemplo.png`,
mapeada tile a tile): título, campo de nome (campo de texto do atlas)
+ Sortear (botão baixo) no topo, mensagem de erro logo abaixo, 1 card
por classe (preview animado + nome + descrição), Voltar/Confirmar
"altos" encostados na borda de baixo (como no mockup). Decisões do
usuário: a classe ESCOLHIDA ganha o contorno do atlas (moldura vazada),
as outras ficam sem; cards alargam repetindo as bordas da moldura pra
caber a descrição, que quebra em linhas (`_CRE_DESC_W`). Sem rótulos
("Nome do personagem:"/"Escolha sua classe:"), como no mockup.

Desenho separado do loop: `_draw_creation(screen, events, st, ...)`
devolve a ação disparada no SOLTAR (`field`/`reroll`/`class`/`confirm`/
`back`); `_run_creation` continua dono da rede (SUGGEST_NAME/
CREATE_CHARACTER/espera do CHARACTER_CREATED) e do teclado, lógica
inalterada. Boneco desenhado 14px RAW abaixo do topo do card (o
contorno da moldura fica ~8px pra dentro do tile — com 4px a cabeça
passava por cima dele). Paleta antiga e `_BASE_BTN_W/H` removidas.

**Teste**: `tests/test_char_select_ui.py` — botões da criação disparam
só no soltar, clicar fora do campo desativa, Confirmar desabilitado
enquanto cria, Sortear desabilitado enquanto gera nome, tudo dentro da
área segura com fonte grande.

## 71. Kit de peças das janelas do atlas (21/10/2026)

Pedido do usuário: "a ideia é nosso sistema ser fácil de ser
construído, tendo um padrão". Diagnóstico: `ui/atlas_window.py` já
cobria botão/campo/seletor/rolagem, mas ~25 janelas antigas
(inventário, loja, correio, artesão, treinador, troca, loot, login,
talentos, diário, ranking, avisos/convites...) ainda desenham tudo
sozinhas, e faltavam peças que quase todas precisam. Plano aprovado:
completar o kit primeiro, depois converter em lotes por família.

Peças novas (`ui/ui_atlas.py` + `Frame`, `ui/atlas_window.py`):
janela escura com barra de título e X (`titled_dark_window_raw`,
`close_button` — inventário do mockup); slot claro/escuro com ícone em
resolução de tela + quantidade (`slot`, sobreposições desenhadas depois
da ampliação: `Frame.overlays`/`draw_overlays`, `slot_icon_px`); grade
de slots (`grid`); abas (`tabs`, peças desenhadas pelo usuário em
(0..5,12), dentro do painel na borda de cima — decisão do usuário);
checkbox (`checkbox`, (12,9)/(13,9)); texto com quebra (`text_block`,
`wrap_text`); linha divisória (`divider_h/v`, mesma cor do contorno da
moldura — as linhas do mockup do inventário); campo de senha
(`text_field(mask=True)`).

**Teste**: `tests/test_atlas_kit.py`.

## 72. Lote de janelas de item no atlas de UI (21/10/2026)

Continuação de §71 (kit). Pedido do usuário: converter de uma vez as
janelas que faltam, no mesmo padrão, e depois ver o que sobra. Decisões
do usuário: inventário com o boneco animado no meio dos 10 slots de
equipamento (esq. Cabeça/Ombros/Torso/Punhos, dir. Pescoço/Anel/Luvas/
Botas, embaixo Mão Princ./Mão Secund.), nome do slot só no tooltip;
janelas sem mockup no mesmo estilo (janela escura com título e X, grade
de slots marrons, botões vermelhos, abas onde houver telas) e imagens
de todas pra aprovar antes do teste no jogo.

Convertidas (todas com cliques no SOLTAR, esquerdo e direito, dentro do
desenho; handlers de clique antigos só "seguram" o clique pra ele não
vazar pro mundo):
- **Inventário** (`client/inventory_handlers.py`) — janela 2 do
  mockup: equipamento em volta do boneco, divisórias, mochila 5x4,
  estatísticas (Base/Itens) embaixo; ouro no cabeçalho da mochila.
- **Loja** (`ui/shop_system.py`) — lista à venda rolável (slot + nome +
  preço), mochila em grade; direito compra/vende, Shift+direito abre o
  modal de quantidade. "Desfazer" mantido desabilitado (nunca funcionou —
  histórico de transação nunca é populado; remoção é decisão do usuário).
- **Troca** (`client/trade_handlers.py`) — mochila 4x5, "sua oferta"
  (campo de ouro + 5 slots), oferta do outro, Negociar/Cancelar; menu de
  player (Negociar/Duelar/Seguir/Convidar) e convite.
- **Convites de duelo e de grupo** (`client/duel_handlers.py`,
  `client/party_handlers.py`) — `atlas_window.prompt`, mesmo do convite
  de troca.
- **Loot** (`ui/loot_system.py`) — lista rolável perto do cursor;
  esquerdo pega, direito equipa; `_try_take_item`/`_try_equip_item`
  recebem o índice da linha (lógica e pedido ao servidor inalterados).
- **Correio** (`ui/mail_system.py`) — 1 janela com abas [Entrada]
  [Escrever] (ler carta abre dentro de Entrada, Voltar retorna).
- **Ferreiro/artesão** (`ui/crafting_system.py`) — menu de ação +
  janela com abas [Reciclar] [Forjar]; ícones reais de item (antes:
  quadrado com a inicial — agora só reserva, `atlas_window.item_icon`).
- **Treinador** (`ui/trainer_system.py`) — menu de assunto
  (`prompt`) + lista de treinamento rolável com Aprender.

Kit ganhou: clique direito no soltar (`Frame.rreleased`/
`right_click`), `prompt` (aviso com botões), `quantity_window`/
`quantity_state`/`quantity_key` (modal de quantidade — loja, troca,
correio; troca o slider antigo por ◀ ▶, digitação e roda do mouse),
`item_icon` (ícone com reserva), `text_field(placeholder, align)`,
`field_hover`, `Frame(press=None)` (janela de FUNDO de um modal: estado
de botão descartável — compartilhar o do modal fazia o `end()` do fundo
zerar o botão afundado do modal antes dele disparar; bug real pego por
teste na seleção de personagem E na loja). Sistemas fora do GameEngine
recebem a área segura por `safe_area_fn` (game.py injeta
`_mm_safe_area`). Medidas antigas (`UI.INVENTORY_*`, `SHOP_*`,
`TRADE_*`, `LOOT_*`, `MAIL_*`, `CRAFTING_*`, `TRAINER_*`) removidas de
`ui/ui_sizes.py`.

Achados no caminho: (1) a limpeza das medidas do loot (por regex)
cortou "LOOT_SCROLL_W" de dentro de um comentário e grudou a linha de
`CHAT_SCROLL_W` com a de `CRAFTING_BAG_COLS` — o ferreiro ANTIGO
quebraria ao abrir entre os commits do loot e do ferreiro (nunca chegou
a jogo: o ferreiro foi convertido em seguida e não usa mais a medida);
`CHAT_SCROLL_W` conferido, intacto. (2) "Deletar" do ferreiro só apaga
no espelho local — ver HISTORICO_BUGS.md §96. (3) cache de ícone de
moeda do loot nasceu como dict no corpo da classe (proibido pelo
CLAUDE.md) — corrigido pra dict de módulo com teto antes do commit.

**Teste**: `tests/test_inventory_atlas_ui.py`, `test_shop_atlas_ui.py`,
`test_trade_atlas_ui.py`, `test_loot_atlas_ui.py`,
`test_mail_system_compose.py` (reescrito pros cliques no soltar, mesmas
garantias), `test_crafting_atlas_ui.py`, `test_trainer_atlas_ui.py`,
`test_atlas_kit.py`.

## 73. Slots 32x32, ajustes de layout e Desfazer da loja (21/10/2026)

Retorno do usuário sobre o lote de §72 (prints anotados):
- **Slots/ícones em 32x32** em todo modal com slot de item (inventário,
  loja, loot, troca, correio, ferreiro, treinador, recompensa de quest):
  `ui_atlas.SLOT_SIZE = 34` (ícone 32 RAW + 1px de borda), montado por
  `ui_atlas.slot_raw()` repetindo a linha/coluna do meio do tile de 16
  (cantos 8x8 intactos — nunca esticado). Ícone mostrado no tamanho
  nativo (32px → x2 de SCALE). `Frame.grid` passou a receber posição em
  px RAW; `atlas_window.grid_px(n, gap)` dá o tamanho. Janelas
  redimensionadas; boneco do inventário ampliado 2x junto.
- **Inventário**: cabeçalho "Nome, Classe Nível N" acima do boneco.
  Com slots de 32 a janela não cabia na altura da área segura (720p) com
  as estatísticas embaixo — elas foram pra uma 3ª coluna à direita da
  mochila; se a largura não couber (fonte grande), voltam pra baixo.
- **Loja**: "Seu ouro:" → "Saldo:" (troca e treinador também). Mochila
  à esquerda, lista à venda à direita (barra de rolagem encostada na
  borda direita).
- **Modal de quantidade**: elementos centralizados; a quantidade fica no
  campo de texto do atlas (mostra que dá pra digitar). `prompt` e os
  pares de botões (correio, troca) também centralizados como grupo.
- **Barras de rolagem**: sempre na última coluna da janela, encostadas
  na borda direita; loot só mostra a barra (e só reserva a coluna)
  quando os itens não cabem; slots do loot encostados à esquerda.
- **Desfazer da loja** (pedido do usuário: "o quanto antes", compra e
  venda, dura enquanto a janela estiver aberta): servidor autoritativo.
  `ShopUndoLog` (componente server-only) guarda as transações da janela
  aberta (máx. 20); `WorldServer.process_shop_undo` desfaz a ÚLTIMA com
  o valor EXATO dela (compra: devolve o preço pago e tira o item — se o
  item já foi usado/sumiu, a entrada sai do histórico e o próximo
  Desfazer pega a anterior; venda: devolve uma cópia do item vendido e
  cobra o que foi recebido — sem ouro/espaço, falha e mantém o
  histórico). `SHOP_CLOSE` (ao abrir/fechar a janela) e transação com
  outro mercador zeram o histórico. Mensagens novas `SHOP_UNDO`/
  `SHOP_UNDO_RESULT`/`SHOP_CLOSE`; `SELL_REQUEST` ganhou `npc_eid`.
- **"Deletar" do ferreiro** corrigido (HISTORICO_BUGS.md §96).
- **Item sem ícone** (140 do catálogo ainda): toda janela passou a usar
  `atlas_window.item_icon` (antes várias chamavam `ICONS.get` direto e o
  slot ficava vazio); reserva = retângulo na cor da qualidade, sem letra
  (decisão do usuário).

**Teste**: `tests/test_shop_undo.py` (servidor: compra, venda, LIFO com
item consumido, sem ouro, fechar/outro mercador),
`test_shop_atlas_ui.py` (botão Desfazer, SHOP_CLOSE, Saldo),
`test_atlas_kit.py` (slot 34, grade em px, modal centralizado),
`test_inventory_atlas_ui.py` (cabeçalho), demais suítes de janela
ajustadas. Validação em jogo pendente com o usuário.

## 74. Abas de serviço do NPC — sem menu intermediário (21/10/2026)

Pedido do usuário: o mestre artesão não abre mais o menu de 3 botões
(Negociar/Reciclar/Forjar) — abre direto em Negociar, com abas pras
outras funções; o mesmo pra todo NPC com mais de uma função (quest +
treinador/mercador...), e quando há quest a aba de Missões abre SEMPRE
primeiro. Decisões do usuário: abas compartilhadas (cada janela continua
no seu System — opção de menor risco; a janela pode mudar de tamanho ao
trocar de aba), abas no topo lado a lado, quest como pergaminho dentro
da janela, e a lista da loja de volta à ESQUERDA com a mochila à direita
(barra de rolagem colada à direita da lista — exceção consciente à regra
"rolagem na borda direita da janela" de §73).

Ponto único: `ui/npc_interaction.py` — `npc_services()` (funções que o
NPC oferece a este player, ordem fixa Missões > Negociar > Reciclar >
Forjar > Treinar; travado por `NPCUnlock` = só Missões),
`default_npc_service()` (aba que abre no clique), `service_tabs()`
(fileira de abas, só com 2+ funções) e `open_npc_service()` (pede a
janela via `NPCInteractionRequest` — kinds novos `open_recycle`,
`open_forge`, `open_train`, além de `open_shop`/`open_quest_dialog`).
Cada System só consome o kind dele (System nunca chama System). Menu de
ação do ferreiro (`_draw_action_menu`, `STATE_MENU`, `_do_negociar`) e
menu de assunto do treinador (`_compute_options`, prompt "o que
deseja?") removidos. Ferreiro também voltou a ter a mochila à direita
(mesmo lado da aba Negociar, a mochila não "pula" ao trocar de aba).
`ui_atlas.quest_window_raw(tw, th, tab_rows)` ganhou uma linha escura
pras abas entre o título e o pergaminho; botões do rodapé da quest
centralizados como grupo. Título da janela com abas = nome do NPC.

Fora do escopo: correio (Mailbox) ainda não entra nas abas.

**Teste**: `tests/test_crafting_atlas_ui.py` (abre direto em Negociar,
abas Reciclar/Forjar/Negociar, pedido de aba vindo de outra janela),
`tests/test_npc_interaction_request.py` (NPC com quest abre Missões
primeiro com as abas das outras funções; aba Negociar abre a loja via
componente), `tests/test_npc_service_unlock.py` (trava por quest via
`npc_services`). Validação em jogo pendente.

## 75. HUD no atlas: moldura do minimapa, retrato e molduras da hotbar (21/10/2026)

Peças novas desenhadas pelo usuário no atlas (`ui/ui_atlas.py`, seção
"Peças do HUD"), esboço em `assets/content/exemplo_minimapa.png`.
Decisões do usuário: retrato com o ROSTO do personagem; ranhuras = Vida /
recurso da classe / Experiência; retrato em 3x (ranhuras de 2px ficariam
finas em 2x); slots da hotbar com ícone 32x32 (moldura montada repetindo
o meio, igual aos slots de item); ícones do minimapa = inventário / PvP
(F1) / menu / amigos (aba ainda não existe — por enquanto só avisa "em
breve") / ranking; aceso = botão apertado OU janela aberta.

- **Minimapa** (`ui/minimap.py`): moldura de madeira em volta do mapa
  (`stretch_raw` — bordas intactas, coluna/linha do meio repetidas), barra
  do topo com o nome da zona (ou do mapa), 5 ícones em círculos
  encaixados na borda de baixo (célula de 16px do atlas: círculo e ícone
  na mesma célula). Clique age no SOLTAR, o apertar é consumido (não vaza
  pro mundo) — `client/hud_handlers.py::_handle_minimap_icon_event`;
  tooltip com o nome/atalho. Nome do mapa/zona saiu do canto superior
  direito (vai na barra); coordenadas só com debug (F12). Tecla I/K e os
  ícones usam o mesmo `_toggle_inventory`/`_toggle_ranking`.
- **Retrato** (`client/hud_handlers.py::_draw_portrait`): rosto recortado
  da animação idle de frente (mesmo mecanismo do jogo) com máscara
  redonda atrás do anel; ranhuras medidas do próprio atlas
  (`ui_atlas.portrait_grooves`, nunca número fixo); nome + nível acima da
  faixa; valores exatos no tooltip da ranhura (taxa de regen da
  concentração também). Substitui nome/HP/recurso em texto; aljava do
  arqueiro continua como barra separada logo abaixo.
- **Hotbar e consumíveis** (`client/hotbar_handlers.py`,
  `client/consumable_bar_handlers.py`): moldura cinza (habilidades) /
  oliva (consumíveis) de `ui_atlas.hud_slot_frame`; slot de tamanho FIXO
  do atlas (`HUD_SLOT_PX` = 72), nunca a escala de UI. Realce de estado
  (proc pulsante, requer talento, arrasto) desenhado por dentro da moldura.
- Ajustes de posição: margem direita do minimapa 18 (8px são a moldura),
  tracker de quests abaixo da fileira de ícones (`UI.MINIMAP_FOOTER`),
  buff bar 16px à esquerda do mapa.

**Teste**: `tests/test_hud_atlas.py`. Validação em jogo pendente.

## 76. Retrato: busto sem animação, fundo do anel e munição do arqueiro em pinos (22/10/2026)

Retorno do usuário sobre §75: cabeça mal centralizada no retrato; sem
animação (só o 1º frame do idle); mostrar o busto (o que couber no anel);
fundo novo do círculo no atlas; elemento novo de munição (pino em
(27,249), exemplo em `assets/content/exemplo_munição.png`).

- Busto: `client/hud_handlers.py::_portrait_face` — 1º frame do idle de
  frente, cabeça centralizada pela própria silhueta (colunas das 12
  primeiras linhas), topo da cabeça a `PORTRAIT_BUST_TOP` do topo da
  abertura; recortado pela máscara da abertura do anel
  (`ui_atlas.portrait_opening_mask`, preenchimento a partir de
  `PORTRAIT_FACE` sobre a cor de fundo do círculo — nunca cobre o anel).
  Cacheado por classe (sem animação = 1 montagem só).
- Munição (só arqueiro): pinos pendurados embaixo da faixa (posição/passo
  medidos no exemplo: 1º em (27,25) da peça, de 2 em 2px, 18 = aljava
  cheia). Quantidade PROPORCIONAL à aljava (`round(flechas/máx × 18)`,
  mínimo 1 enquanto houver flecha); tooltip com o número exato ou "Sem
  aljava". Substitui a barra de aljava antiga abaixo do retrato.

**Teste**: `tests/test_hud_atlas.py` (abertura do anel, pinos
proporcionais).

Complemento (22/10/2026, pedido do usuário): saíram do HUD as linhas
abaixo do retrato — "Ataque: Pronto/CD", ouro, "Mochila N/M" e o aviso
piscando de talentos a alocar (ouro/mochila continuam no inventário e na
loja). Ficam abaixo do retrato só os ícones de efeitos ativos e, com
debug (F12), os atributos brutos.

Complemento (22/10/2026, pedido do usuário): tecla de atalho dos slots da
hotbar e dos consumíveis desenhada DENTRO da moldura do atlas (nem o texto
nem o contorno encostam na borda) e número de cooldown maior (`font_lg`),
os dois com contorno quase preto — `HotbarHandlers._hb_key_label`/
`_hb_center_number`, reaproveitando `ui/floating_text.py::_render_outlined`.

## 77. Tileset de caverna novo catalogado como objetos (22/10/2026)

Usuário desenhou um tileset novo de caverna (ainda incompleto) por cima
de `assets/tiles/TX Tileset Cave.png` e criou 2 PNGs de apoio no mesmo
padrão das construções (§24): `(dimensions)` (contorno vermelho de cada
peça) e `(collisions)` (área de colisão). Pediu pra catalogar já, pra
ir testando em jogo.

**Extração por script** (component-labeling do contorno, alinhado à
grade de 32px — a 1ª passada sem alinhar pegava 1px a mais em peças sem
contorno interno): 139 regiões, 2 vazias (sem arte e sem colisão)
descartadas = **137 peças**, ids `cv001`..`cv137` em
`OBJECT_SHEET_FAMILIES` (`engine/tileset.py`, família "Caverna"), 13
grupos por região do sheet (rocha/passagem em chão, lava e água;
estalactites; buraco/lago/poço de lava e plataformas dentro deles). Cor
de cada grupo = média dos pixels opacos da região. Peça transparente
COM colisão (miolo do buraco) é mantida — é o que impede andar no
buraco.

**Decisões do usuário**: (1) bloqueio de visão (`vision_height=2`) só
nas peças de rocha com colisão; bordas de buraco/lava/água só impedem
de andar. (2) O terreno antigo `txc` (o mesmo arquivo era uma imagem
64x64 com 4 pisos) saiu de `SHEET_FAMILIES` — `map_cave_east`/
`map_cave_west` perderam esse chão (tokens `txc_*` caem no fallback `G`
do parser, ver PROBLEMAS_ARQUITETURA.md §131; mapa carrega sem erro) e
serão repintados com as peças novas. Peça sem colisão fora da rocha
(chão de plataforma) usa sort 0 (sem Y-sort, desenha por baixo do
personagem).

**Verificação**: registro (137 ids, colisão/visão conferidas), testes
de tileset/mapa/God Mode (48 passed), carga dos 2 mapas de caverna,
folha de conferência visual com todas as peças rotuladas. **Falta**:
usuário validar no God Mode/jogo (Y-sort das bordas de rocha sem
colisão é o ponto mais provável de ajuste).

## 78. Caverna separada em paredes (objetos) e chão (terreno) (22/10/2026)

Usuário corrigiu o tileset de §77 e percebeu que plataformas/buracos/
lava/água são CHÃO (onde o player pisa) e as paredes ficam POR CIMA
deles — como tudo era objeto, não dava pra pôr parede sobre chão.
Separou em 2 arquivos, cada um com seus PNGs de apoio `(dimensions)`/
`(collisions)`:

- `TX Tileset Cave` (só paredes) → recatalogado em
  `OBJECT_SHEET_FAMILIES`, 57 peças `cv001`..`cv057`, 6 grupos (rocha/
  passagem × chão/lava/água), `vision_height=2` em toda peça com
  colisão.
- `TX Tileset Cave Ground` (chão) → família NOVA de terreno em
  `SHEET_FAMILIES`, prefixo `cvg`, 83 células.

**Mudança de motor (pequena, mesmo padrão de `color_overrides`)**:
terreno só tinha colisão por ARQUIVO inteiro (`underlying_terrain` por
prefixo). `discover_sheet_tiles` ganhou 2 campos opcionais —
`cells` (só registra as células listadas, God Mode não mostra vazio) e
`cell_underlying` (char de gameplay por célula). Célula com colisão no
PNG de apoio vira `"W"` (sólido, `vision_height=0` — mesma decisão de
§77: buraco/lava/água impedem andar, não bloqueiam visão).
`map_loader._parse_terrain_cell` passou a ler o char POR TILE de
`OBJECT_UNDERLYING` (preenchido por `discover_sheet_tiles`) — o cache
por prefixo `_get_sheet_underlying` saiu (resultado idêntico pras
famílias antigas, conferido). God Mode pula célula não registrada na
grade de terreno. Colisão de terreno pintado no God Mode continua
valendo só depois de salvar/recarregar o mapa (comportamento de sempre
pra terreno de sheet).

**Mapa já pintado** (`aracnopolis_dungeon_objects.csv`, 14 células com
peças de §77): remapeado por comparação de pixel peça antiga × nova —
9 bateram idênticas (só `cv023`→`cv017` mudou de número); 5 peças da
passagem antiga não existem mais no desenho novo e foram limpas (linhas
143–145, colunas 94–96), pra repintar.

**Verificação**: registro (57 + 83), char sólido/andável por célula,
testes de mapa/tileset/God Mode (50 passed, 2 testes novos em
`tests/test_map_loader_terrain_parsing.py::TestColisaoPorCelulaNoTerreno`).
**Falta**: validação do usuário no God Mode/jogo.

## 79. God Mode com zoom (abre em 3x) + pincel aleatório "Chão Caverna" (22/10/2026)

**Zoom**: o God Mode (F10) forçava zoom 1x ao abrir porque grade,
destaque do cursor e clique→tile (`ui/god_mode.py`) supunham 1 px de
mundo = 1 px de tela — no 1x tudo ficava pequeno demais pra editar
(pedido do usuário, já feito antes e adiado). Agora o `GameEngine`
grava todo frame `GodMode.view_scale = (largura da área / largura
lógica do zoom_surf, altura da tela / altura lógica)` — a escala REAL
usada no `transform.scale` do mundo, não o zoom nominal (o arredondamento
de `int(dest_w / z)` faz as 2 diferirem um pouco). `_screen_to_tile`
divide por ela; `_tile_screen_rect` (novo) é o único ponto que converte
tile→retângulo de tela, usado pelo destaque/seleção; grade desenhada em
espaço de tela. Faixa própria do God Mode em `game.py`
(`_GOD_ZOOM_DEFAULT=3.0`, 1x–4x, passo 0.5): abre em 3x, roda do mouse
SOBRE O MAPA e `+`/`-` ajustam (sobre o painel a roda continua rolando a
lista); ao fechar por qualquer caminho (F10/Esc/Sair) volta ao zoom de
jogo de antes (`_god_zoom_on`, checado no bloco "Zoom surf").

**Pincel "Chão Caverna"**: campo opcional `random_brushes: {nome:
[células]}` em `SHEET_FAMILIES` (hoje só na família `cvg`, células
c6-10/r0-5 = 30 texturas de chão) — o God Mode desenha um botão "🎲
nome" na aba Terreno e reusa o pincel de grupo aleatório que já existia
pro "Piso sobre grama/terra" (sorteia sem repetir o último). O nome
exibido no painel ("Selecionado") vem junto na tupla de
`_terrain_floor_group_rects`, antes era fixo "Piso (aleatório)".

**Verificação**: testes de God Mode/mapa/tileset (52 passed, 2 novos:
clique com zoom 3x e pincel da caverna), desenho do God Mode com escala
3x sem erro. **Falta**: usuário validar no jogo (rodar `python main.py`).

## 80. Paredes da caverna recatalogadas + painel do F10 2x com grupos montados (22/10/2026)

**Paredes**: usuário redesenhou `TX Tileset Cave` (rocha virou grade de
32x32, passagem com colunas 32x96) e atualizou `(dimensions)`/
`(collisions)`. Mesmo script de §77/§78 → 75 peças `cv001`..`cv075`, 6
grupos. `aracnopolis_dungeon_objects.csv` remapeado: peça idêntica em
pixel → id novo; senão peça na MESMA posição/tamanho do sheet (arte
editada no lugar); senão célula limpa — 4 células limpas (cv008/cv011/
cv012 antigas, que viraram peças de outro tamanho): (linha 137, col 98),
(138, 99), (144, 95), (144, 96).

**Painel do F10 (`ui/god_mode.py`)**, pedido do usuário (miniaturas
pequenas demais): `_BASE_PANEL_W` 220→440 (decisão do usuário: painel
mais largo, mapa fica mais estreito), miniatura de tile `_THUMB_BASE =
64` (2x o real). Aba Objetos: cada GRUPO aparece montado como no PNG
(peças na posição relativa, contorno leve em cada uma; escala reduz só
se o grupo não couber; grupo esparso demais cai na fileira antiga). Aba
Terreno: sheet mais largo que o painel é quebrado em blocos de colunas
(cada bloco montado, sem encolher o tile).

**Verificação**: 52 testes de God Mode/mapa/tileset, capturas do painel
renderizado (objetos e terreno). **Falta**: usuário validar no jogo.
