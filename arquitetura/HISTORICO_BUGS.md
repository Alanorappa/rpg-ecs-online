# Histórico de Bugs — RPG ECS Online

> Log datado de defeitos CONCRETOS e localizados (não sintomas de um
> padrão estrutural recorrente — esses ficam em `PROBLEMAS_ARQUITETURA.md`;
> nem feature nova — essas ficam em `HISTORICO_FEATURES.md`). Números são
> deste arquivo (a entrada em `PROBLEMAS_ARQUITETURA.md`, se existia antes
> de 19/10/2026, fica como stub "(movido)" apontando pra cá). Ver
> `arquitetura/INDICE.md` pra buscar por tag/palavra-chave.

---

## 1. ESC não fechava os modais de PvP (10/08/2026)

**Sintoma relatado pelo usuário:** testando a fila de MOBA nova (§18), ESC
não fechava a janela — "que deveria ser um padrão para todos os modais do
jogo".

**Causa raiz:** `client/modal_stack_handlers.py::_modal_registry()` é o
ÚNICO ponto de verdade que `_close_top_modal()` (disparado pelo ESC,
`game.py`) consulta — qualquer modal fora dessa lista simplesmente nunca é
encontrado. Os 5 modais de PvP (fila de Arena+BG, aceite "Partida
encontrada!" de Arena+BG, resultado de fim de partida de Arena+BG — 5, não
6, porque fila de Arena e fila de BG dividem o MESMO modal físico,
`_arena_modal_open`) nunca foram adicionados a essa lista desde que
`ModalStackHandlers` foi extraído (item IU3) — nem quando os modais de Arena
já existiam, nem quando os de BG foram criados depois. Não é um bug NOVO
desta sessão, é um débito antigo só agora reportado.

**Fix:** 5 entradas novas no registro, mesma posição relativa (logo depois
de "trade", categoria "interação com outro jogador"):
- `arena_queue` — fecha só visualmente (`_arena_modal_open_val = False`),
  sem consequência de jogo (é só a tela de escolher fila).
- `arena_accept`/`bg_accept` (janela "Partida encontrada!") — fecha só
  visualmente também; não existe ação de "recusar" separada porque o
  convite pendente no SERVIDOR já expira sozinho no prazo
  (`accept_deadline`), com ou sem o modal visível no cliente — fechar cedo
  não rouba a vaga de ninguém.
- `arena_result`/`bg_result` (tela de fim de partida) — fecha mandando a
  MESMA ação do botão "Sair da Arena"/"Voltar" (`_send_arena_forfeit`/
  `_send_bg_leave`), não só esconde a UI. Mesmo padrão já usado por "trade"
  no registro (`_close_trade` manda `TRADE_DECLINE`/`TRADE_CANCEL` de
  verdade) — fechar via ESC uma tela que representa "ainda estou dentro da
  instância" precisa ser a ação real de sair, não só um esconder cosmético.

**Achado ao testar:** `tests/test_client_ui.py::_make_modal_stack_fixture`
(fixture dedicada pra `_modal_registry()`) quebrou ao adicionar as entradas
— o fixture só stubava os SISTEMAS já conhecidos, e `_modal_registry()`
constrói a lista INTEIRA de forma antecipada (referencia todo `close_fn` na
hora, mesmo os de modais fechados). `_send_arena_forfeit`/`_send_bg_leave`
usados DIRETO como `close_fn` quebravam o fixture (método sem o prefixo
`_close_` que o `__getattr__` de stub reconhece) — corrigido criando
`_close_arena_result`/`_close_bg_result` como wrappers dedicados (nome
correto por convenção, não só pra passar no teste) e adicionando os 5
novos estados ao fixture.

**Testes:** 3 novos em `tests/test_client_ui.py` — `arena_queue`/
`arena_accept`/`bg_result` reconhecidos por `_topmost_open_modal()` e
fechados por `_close_top_modal()` sem exceção. Prova diferencial: as 5
entradas novas comentadas temporariamente → os 3 testes falham (2 com
`AssertionError` esperando o nome do modal e recebendo `None`) → revertido,
testes voltam a passar. Suíte completa: 3 rodadas consecutivas, 970/970,
zero falha (nem a flaky).

## 2. Crash real de playtest — slot `None` do Inventory sem guard em 2 consumidores novos (11/08/2026)

**Sintoma relatado pelo usuário**: logou pra testar a Fase 4/A4 e o
terminal do servidor mostrou `AttributeError: 'NoneType' object has no
attribute 'name'` em `_item_data_from_obj`, disparado por
`get_player_inventory_data` dentro de `_persist_character` — ou seja,
todo DISCONNECT depois disso derrubava a tarefa de salvar o personagem
(a exceção subia até `Task exception was never retrieved`, sem crashar
o processo inteiro, mas o save daquela sessão não completava).

**Causa raiz — não é regressão da A4, é um consumidor novo do mesmo
padrão já conhecido**: `Inventory.items` guarda `None` no lugar de um
item cuja stack esgotou (formato NORMAL e deliberado nesta base — ver
`TestQuestLogicIgnoraSlotVazioNoInventario` em `tests/test_server.py`,
já resolvido uma vez em `quest_logic.py` 14/07/2026). `get_player_
inventory_data` (`server/world_server.py`) é chamada desde a Fase 0 do
saneamento (07/08/2026) mas nunca tinha sido exercitada por um
personagem com slot vazio até agora — a A4 não criou o `None`, só
tornou a função mais fácil de alcançar (`_cache_equipment_and_inventory`
chama ela a cada equipar/desequipar, além do disconnect que já
chamava). Auditoria (grep por toda iteração de `inv.items` no projeto,
cruzando contra o padrão já catalogado) achou MAIS um consumidor
igualmente exposto: `server/instance_progression.py::_push_stats_update`
(`inv_snapshot`, mandado ao ENTRAR numa instância normalizada) —
mesma falta de guard, gatilho diferente (não precisava de disconnect,
só entrar numa BG/Arena depois de esgotar a aljava).

**Fix — só nos 2 consumidores server-side reais** (`is not None` no
filtro, mesmo padrão já usado em `get_player_equipment_data`/
`sanitize_inventory_payload`, que já filtravam certo): `get_player_
inventory_data` e `_push_stats_update`. NÃO mexido: o ponto que CRIA o
`None` (`_server_recarregar`) — é o comportamento intencional, não o
bug; reverter isso quebraria os testes que já provam esse formato de
propósito.

**Auditoria adicional, client-side** (mesma classe de bug, severidade
menor — crash local de 1 jogador, não do processo do servidor): mais 6
sites descobertos sem guard, todos corrigidos com o mesmo `is not
None`/`if it is None: continue`: `ui/systems.py::_use_consumable`/
`_finalize_consumable` (usar consumível pela barra de atalhos),
`client/consumable_bar_handlers.py` (ícone/tooltip da barra, 2
ocorrências idênticas — a função-fonte `_resolve_consumable_item` já
guardava certo, só 2 usos inline diretos tinham esquecido), `client/
network_handlers.py` (reconciliação de Trade por nome), `ui/
crafting_system.py` (contar/remover material da forja, 2 sites +
`_add_to_bag`), `client/inventory_handlers.py::_handle_inventory_click`
(clicar num slot vazio da mochila — crashava tanto no clique direito
quanto no equipar).

**Testes**: 2 novos em `tests/test_server.py::
TestInventoryDataSerializationIgnoraSlotVazio` — reproduzem o cenário
EXATO (recarrega aljava até esgotar, depois chama `get_player_
inventory_data`/`_push_stats_update` de verdade). Prova diferencial:
os 2 testes revertidos temporariamente pro código sem guard reproduzem
o MESMO traceback relatado pelo usuário (`AttributeError: 'NoneType'
object has no attribute 'name'`), restaurados voltam a passar. Suíte
completa: 3 rodadas consecutivas, 797/797 (795 + 2 novos), zero falha.

## 3. Forjar/reciclar/aprender receita — 4 bugs reais, um deles duplicação de ouro ativa (11/08/2026)

**Contexto**: durante a discussão de performance (§23), usuário pediu
2 correções pontuais fora do roteiro principal — forjar/reciclar nunca
descontava ouro/material de verdade no servidor, e aprender receita
por pergaminho não funcionava online. A investigação achou MAIS 2
bugs bloqueadores no meio do caminho, ambos aprovados pelo usuário
antes de codar (mesma régua de sempre — parar, explicar, esperar
decisão).

**Bug 1 — crafting 100% local, sem nenhuma mensagem de rede**
(`ui/crafting_system.py::_do_forge`/`_do_recycle`): cliente descontava
ouro e mudava a mochila só na cópia LOCAL, sem mandar NADA pro
servidor — nem `INV_SYNC`, nem `GOLD_UPDATE`, nem save imediato. O
ouro "gasto" no servidor nunca existia de verdade: um relog (ou o
autosave de 2 minutos alcançando o cliente antes de qualquer sync)
restaurava o ouro antigo, mas o item craftado sobrevivia via o
próximo `INV_SYNC`/`SAVE_STATE` — **duplicação de ouro/item já ativa**,
não teórica.

**Fix**: `CRAFT_REQUEST {recipe_id}`/`RECYCLE_REQUEST {inv_index}` —
mesmo modelo já provado em `process_shop_buy`/`process_shop_sell` e no
`inv_index` de Trade/EQUIP_ITEM. Servidor lê a receita do PRÓPRIO
catálogo (nunca confia em custo/material/resultado vindo do cliente),
confere ouro+material no `Inventory` AO VIVO, e só então desconta e
credita — tudo atômico em `WorldServer.craft_item`/`recycle_item`.
**Simplificação de UX aprovada pelo usuário**: forjar deixou de ter 2
cliques (forjar → "clicar pra pegar" o resultado) — o item cai direto
na mochila, mesmo padrão de compra em loja. `_frg_complete`/
`_frg_result` (estado do passo removido) saíram do cliente.

**Bug 2 — aprender receita nunca funcionava online**
(`server/world_server.py::apply_consumable`): a função já validava e
confirmava o consumo do pergaminho (`consumable_ok`), mas nunca lia o
efeito `learn_recipe` do item nem tocava em `LearnedRecipes` — o
scroll era consumido de verdade, a receita nunca era aprendida.

**Bug 3 (bloqueador do #2, achado durante o desenho)** —
`_item_factory_by_id` (função nova da migração C2, §20) nunca resolvia
o item_id de um pergaminho de receita: `RECIPE_ITEMS` é chaveado pelo
recipe_id CRU (`"espada_afiada"`), mas o item_id do pergaminho tem
prefixo `"recipe_"` (`"recipe_espada_afiada"`, ver `content/
crafting_data.py::_make_recipe_item` — evita colidir com o item_id do
resultado craftado, que usa a chave crua). A busca comparava o
item_id prefixado direto contra as chaves sem prefixo — nunca batia.
Invisível até agora porque nada consumia esse resultado (era
exatamente o bug #2). Fix: 3 linhas, considerar o prefixo antes de
buscar — confirmado com reprodução isolada (`_item_factory_by_id
("recipe_espada_afiada")` retornava `None` antes, resolve certo
depois).

**Bug 4 (2º bloqueador do #2, achado ao testar)** — `LearnedRecipes`
NUNCA era criado pro personagem no `spawn_player` do SERVIDOR (método
escrito à mão, "sem efeitos colaterais de create_player()" por
design, nunca incluiu esse componente — só o `entity_factory.
create_player` do CLIENTE tinha). Pior: **não existia coluna no banco
pra persistir isso** — recipe learning só foi implementado pro
sistema de save-slot OFFLINE antigo (`engine/save_system.py`, morto
na arquitetura online desde a migração pra servidor real). Mesmo
corrigindo o bug 2 sozinho, a receita aprendida sumiria no próximo
login. Escopo maior que o esperado — apresentado ao usuário antes de
prosseguir, aprovado ("fazer completo agora").

**Fix**: mesmo padrão já usado 4x nesta sessão pra equipamento/
inventário/talentos/hotbar — coluna `learned_recipes_json` (schema +
migração `ALTER TABLE` em `server/auth.py`), `LearnedRecipes()`
anexado no `spawn_player` (carrega do `char_data` se existir),
`WorldServer.get_player_learned_recipes_data()` (mesmo formato de
`get_player_hotbar_data`), `_build_save_merge` ganha
`live_learned_recipes` (vence cache do cliente, que nem manda esse
campo de propósito) via `_persist_character`.

**Achado adicional durante a implementação**: `_grant_items_to_inventory`
(client/network_handlers.py, usado por LOOT_RESULT/INVENTORY_UPDATE e
agora também por CRAFT_RESULT/RECYCLE_RESULT) só resolvia item_id
contra `item_table.ITEMS`/`QUEST_ITEMS` — um item craftado ou material
de reciclagem (catálogos PRÓPRIOS de `content/crafting_data.py`) caía
em `obj is None: continue` e desaparecia da cópia LOCAL em silêncio,
mesmo com o servidor tendo aplicado certo. Comentário antigo dizia
"item_table cobre loot+loja+forja" — nunca cobriu forja. Corrigido:
resolução agora cruza os mesmos catálogos de `_item_factory_by_id`
(servidor), incluindo o prefixo `"recipe_"`.

**Testes**: `tests/test_craft_recycle_protocol.py` (novo, 26 testes) —
craft/recycle sucesso e cada motivo de rejeição (ouro/material
insuficiente, mochila cheia, item não reciclável, posição forjada)
SEM consumir nada nesses casos; aprender receita online + já conhecida
não repete; resolução de item_id de pergaminho; `spawn_player`
cria/carrega `LearnedRecipes`; `_build_save_merge` prioriza o
componente ao vivo; 3 testes fim-a-fim via protocolo real
(`encode`/`on_message`). Suíte completa: 3 rodadas consecutivas,
823/823 (797 + 26 novos), zero falha.

**Playtest do usuário achou mais 3 bugs reais nos fixes acima
(11/08/2026)** — mesma sessão, reportados depois de testar em jogo:

- **Bug 5 — forjar não tirava os materiais da bag LOCAL do cliente.**
  Sintoma relatado: forjou, o resultado apareceu, mas os materiais
  continuavam contados na bag — só sumiam de verdade depois de
  relogar. Causa: `CRAFT_RESULT` só mandava o item RESULTADO
  (`_grant_items_to_inventory`); nunca informava quais materiais o
  servidor consumiu, então a cópia local nunca sabia o que remover — o
  servidor já tinha descontado direito (por isso a 2ª tentativa de
  forjar era recusada, "sem material"), só o CLIENTE ficava
  desatualizado até o relog trazer o Inventory real. Fix:
  `craft_item()` passou a retornar `materials_consumed` (mesmo formato
  de `"removed"` do INVENTORY_UPDATE de entrega de quest), cliente
  chama o mesmo helper já existente (`_remove_items_from_inventory`).

- **Bug 6 — `apply_consumable` nunca decrementava o item no Inventory
  AO VIVO do SERVIDOR.** Sintoma relatado: consumiu o pergaminho de
  receita, a receita apareceu na lista de forja, mas depois de relogar
  o PERGAMINHO reapareceu na bag (como se nunca tivesse sido usado).
  Causa raiz: só a cópia do CLIENTE removia o item consumido
  (`ConsumableSystem._finalize_consumable`) — o servidor confirmava o
  uso (`consumable_ok`) e aplicava cura/efeito, mas nunca tocava no
  `Inventory` dele mesmo. Qualquer save persistia o item como se nunca
  tivesse sido consumido — mesma classe de bug do #5/#1, só que pra
  QUALQUER consumível (poção, comida, pergaminho), não só forja. Mais
  visível no pergaminho por ser raro/valioso, mas o mesmo problema
  existia (e existiria de novo, silenciosamente) pra poções comuns.
  Fix: `apply_consumable` agora decrementa o item real no `Inventory`
  ao vivo ao aceitar o uso — mesmo critério de identificação que o
  cliente já usa (primeiro item da bag com aquele item_id, stack > 0).

- **Bug 7 — cliente nunca carregava `learned_recipes_json` de volta no
  login.** Sintoma relatado: depois de relogar, a receita aprendida
  numa sessão anterior não aparecia mais na lista de forja, e nem
  consumir o pergaminho de novo resolvia (o SERVIDOR já sabia que a
  receita era conhecida — `learn_recipe()` retorna `False` pra receita
  repetida — então a confirmação nem incluía `learned_recipe`, e o
  cliente nunca tinha essa informação de outra forma). Causa: o bug 4
  (§24 acima) implementou salvar `LearnedRecipes` no banco, mas
  esqueceu de implementar CARREGAR de volta — `client/
  save_sync_handlers.py::_restore_save_state` parseia `talents_json`/
  `skill_levels_json`/`quests_json`/`skills_json` do payload de
  LOGIN_OK, mas nunca tinha o bloco equivalente pra
  `learned_recipes_json`. Fix: bloco novo, mesmo padrão simples de
  `SkillLevels`/`QuestLog` (lista direta, sem merge).

**Lição**: os bugs 5/6/7 são todos a MESMA classe de erro que motivou
esta sessão inteira (autoridade sem sincronização de volta) — cada
"metade" da mudança (servidor autoritativo) foi implementada e
testada isoladamente, mas a OUTRA metade (cliente refletindo o que o
servidor fez) só foi validada por teste sintético, nunca por um
fluxo real de "usar → relogar → conferir". Confirma a régua já
existente no `CLAUDE.md` ("corrigido" exige reproduzir o cenário
relatado, suíte sintética não é prova de comportamento real) — aqui
o gap não chegou a ser testado sinteticamente, então nem a régua
teria pego sozinha; só o playtest revelou.

**Testes**: 4 novos em `tests/test_craft_recycle_protocol.py` — craft
retorna `materials_consumed` correto; consumir pergaminho remove do
Inventory ao vivo do servidor; consumir poção empilhada decrementa só
1 unidade (não a stack inteira); cliente carrega `learned_recipes_json`
de volta em `_restore_save_state`. Suíte completa: 3 rodadas
consecutivas, 827/827 (823 + 4 novos), zero falha.

## 4. Bug real do talento Reciclagem — flecha reciclada sem `item_id` (12/08/2026)

Usuário relatou (fora do fluxo de ranking de perf, investigação de bug
pontual): talento Reciclagem (arqueiro, "após abater um alvo, chance de
recuperar as flechas gastas") às vezes não mostrava a flecha no loot;
em outro teste, a flecha dropava no corpo mas saqueá-la dava "Já foi
saqueado". Hipótese inicial do usuário: talento dessincronizado do
servidor. Investigação achou causa DIFERENTE, mais fundamental — os 2
sintomas são a MESMA causa raiz, não 2 bugs.

**Causa raiz**: `server/server_death_handler.py` (passo 5b, devolução
de flecha reciclada) montava o `Item` retornado NA MÃO —
`Item(name=_atype, item_type="ammo", ...)` — sem definir `item_id`.
Todo item do catálogo real (`content/item_table.py::ITEMS`) tem
`item_id` carimbado automaticamente pela própria chave do dict
(`_with_derived_level`); um item construído fora do catálogo nunca
passa por isso, fica com `item_id=""`.

**Por que isso produz os 2 sintomas diferentes**: `client/
network_handlers.py::_handle_msg_loot_available` (reconstrução do loot
recebido do servidor) ainda combinava só por NOME — mecanismo LEGADO,
que deveria ter sido substituído pela identidade estável por `item_id`
na migração "débito C2" (10/08/2026), mas ficou pra trás só nesse
ponto específico (outros pontos, como `WorldServer._reconstruct_item`,
já preferiam `item_id`). Resultado:
- Nome da flecha reciclada (`_atype`, tipo da munição equipada) NÃO
  bate com nenhum item do catálogo → reconstrução falha, item cai fora
  silenciosamente da lista → **"não aparece no loot"**.
- Nome COINCIDE com um item real (ex: "Flecha" comum) → cliente
  reconstrói com o `item_id` CORRETO do catálogo, mas o servidor
  guardou o item de verdade com `item_id=""` no corpse → ao clicar pra
  saquear, `request_loot` procura por um id que não existe ali →
  devolve vazio → **"Já foi saqueado"**, mesmo com o item intocado.

Não era desincronização de talento — o talento estava concedendo a
flecha corretamente (por isso ela aparecia/dropava às vezes); a
IDENTIDADE do item reciclado é que estava quebrada, e um mecanismo de
reconstrução desatualizado no cliente expunha o buraco de formas
diferentes dependendo de coincidência de nome.

**Correção (2 partes, mesma causa, ponto único de verdade)**:
1. **Raiz**: `server_death_handler.py` agora resolve a flecha reciclada
   pelo CATÁLOGO real (`content.item_table.resolve_item_by_name(_atype)`,
   com fallback pra `"Flecha"` se o subtype da aljava não bater com
   nada) em vez de montar o `Item` à mão — garante `item_id` real,
   igual a qualquer outro loot.
2. **Defesa em profundidade + ponto único de verdade**: `WorldServer.
   _item_factory_by_id`/`_item_factory_by_name` (que só existiam no
   servidor) viraram wrappers finos sobre `content.item_table.
   resolve_item_by_id`/`resolve_item_by_name` — implementação real
   MOVIDA pra `content/` (sem estado, importável por cliente E
   servidor; API antiga preservada intacta, nenhum call site/teste
   precisou mudar). Cliente ganhou `NetworkHandlersMixin.
   _resolve_loot_item()` (novo, `client/network_handlers.py`) — prefere
   `item_id`, cai pro nome só quando ausente, mesmo padrão que
   `WorldServer._reconstruct_item` já usava do lado servidor. As 2
   ocorrências de `_handle_msg_loot_available` (harvestable já
   existente + corpse novo) foram simplificadas pra usar esse método
   único — a reconstrução por nome linha-a-linha (2 cópias quase
   idênticas, ~30 linhas cada) virou 1 chamada cada.

**Bônus incidental**: a reconstrução antiga só cobria o catálogo de
loot (`_T`) + itens de quest — nunca checava materiais de forja/
pergaminhos de receita/loja. `resolve_item_by_name` (a versão movida)
já cobre os 4 catálogos, então qualquer item desses tipos que
porventura aparecesse em loot também passa a reconstruir corretamente
agora, não só a flecha.

**Testes**: `tests/test_instance_progression.py::TestInstanceReciclagemGoesToBag::
test_flecha_reciclada_tem_item_id_real_e_e_sacavel` (novo) — mata mob
de verdade com Reciclagem ativa (mesmo helper
`_kill_mob_with_arrows_received` já usado pelos 2 testes irmãos), confirma
`item_id` não-vazio E prova ponta a ponta que `request_loot` consegue
sacar a flecha por esse id de verdade (não só "não está mais vazio").
`tests/test_client_ui.py::test_loot_available_resolve_item_por_item_id_quando_nome_nao_bate_no_catalogo`
(novo) — item com `item_id` real mas nome propositalmente fora do
catálogo, confirma que ainda reconstrói (prova que o item_id é
respeitado antes do nome). Os 2 confirmados por revert manual: sem a
correção, ambos falham exatamente como o bug real relatado (item_id
vazio / lista de loot vazia). Suíte completa (1100 testes) rodada 1x,
tudo verde.

## 5. Bug real na BG — corpo do player morto recuperava HP sozinho (level-up de instância curava quem já está morto) (12/08/2026)

Usuário relatou (com print): dentro da BG, ao morrer, o corpo fica no
chão MAS o HP volta a mostrar cheio (print mostrava "Você morreu" /
"Respawn na base em 6s" e a barra em 240/240 ao mesmo tempo); às vezes
minions passam a atacar o corpo. Usuário lembrava de já ter relatado
algo parecido antes e recebido um fix — busca em todo este arquivo por
qualquer entrada "MITIGADO" com sintoma equivalente não achou nada
batendo (achou só um guard vizinho, item adjacente, ver abaixo) — tratado
como causa nova, não reincidência do mesmo item.

**Causa raiz**: `server/instance_progression.py::_process_instance_levelup()`
(chamado por `grant_instance_xp`, inclusive XP de proximidade de kill de
minion perto — `players_in_normalized_progression_near`, que não exige
hit nenhum) fazia `cs.current_hp = cs.max_hp` incondicionalmente sempre
que o level-up acontecia. Um player MORTO na BG (`GhostState.is_dead=True`,
esperando o timer de `_tick_bg_respawns`) continua recebendo XP de
proximidade normalmente — é o comportamento correto de MOBA (XP de lane
mesmo esperando respawn) — mas se esse XP fechar um level, a cura pro
máximo disparava mesmo assim, sem tirar `GhostState.is_dead`, sem
cancelar o timer de respawn, sem teleportar. O mesmo `cs.current_hp =
max_hp` incondicional existia também no caminho de XP "real" (fora de
instância), `server/world_server.py` (bloco de `death_handling`, dentro
do `if _char_xp.level > _level_before:`).

**Por que isso também fazia minion atacar o corpo**: TODO check de alvo
válido do jogo (`EnemyAISystem`, `MinionSystem`, torres — 15+ pontos em
`engine/world_systems.py`, confirmado por busca) usa só
`CombatStats.current_hp <= 0`; nenhum usa `GhostState.is_dead`. Assim
que o level-up bugado subia o `current_hp` acima de 0, o corpo virava
um alvo atacável de novo pra qualquer IA — daí o "às vezes" (só quando
um level-up cai bem na janela em que o player está morto esperando
respawn, não sempre).

**Relação com o guard vizinho já existente**: já existia um guard datado
de 06/08/2026 em `world_server.py` (bloco `regen_and_status_ticks`,
ActiveRegen/HoT de consumível) pausando cura de HoT em quem já morreu,
pelo MESMO motivo ("current_hp>0 fazia qualquer mob aceitar o corpo como
alvo válido"). Ou seja, essa classe de bug (fonte de cura que não checa
morte, dentro da janela entre `_handle_player_death` e o revive real) já
tinha se manifestado uma vez e sido corrigida NUM sistema (HoT de
consumível) — mas o level-up de instância era uma fonte INDEPENDENTE do
mesmo tipo de bug, nunca coberta por aquele guard. É provável que a
lembrança do usuário de "já reportei isso" seja sobre aquele fix
anterior, que resolveu de verdade o sintoma vindo de HoT mas não o
vindo de level-up.

**Correção** (mesmo padrão nos 2 pontos irmãos — só cura ao subir de
nível se já estiver vivo; a cura de verdade ao reviver continua sendo
responsabilidade exclusiva de `_revive_player`,
`server/respawn_system.py`):
- `server/instance_progression.py::_process_instance_levelup` —
  `if cs.current_hp > 0: cs.current_hp = cs.max_hp`.
- `server/world_server.py` (bloco `death_handling`, XP real) — mesmo
  guard.

Escopo decidido com o usuário via pergunta explícita: só o fix pontual
agora (não o redesenho completo pra "espírito na base + corpo 100%
visual" que o usuário descreveu como ideal) — redesenho maior fica pra
decisão futura, registrado aqui como próximo passo possível se o
usuário quiser revisitar.

**Testes**: `tests/test_instance_progression.py::TestInstanceProgressionLevelUp::
test_levelup_nao_cura_player_morto_esperando_respawn_na_bg` (novo) —
reproduz o cenário exato (`current_hp=0` + `GhostState.is_dead=True`,
concede XP de instância suficiente pra fechar level, confirma level sobe
mas `current_hp` continua <= 0). Confirmado por revert manual: sem o
guard, o teste falha com `current_hp` voltando a 350 (valor real do
`max_hp` pós level-up). Suíte de `test_instance_progression.py` (35/35),
`test_bg_queue.py`, `test_server.py`, `test_char_stats.py` rodadas
juntas, tudo verde — suíte completa (3x) não rodada ainda, pendente de
validação manual do usuário em jogo primeiro (mesma ordem já combinada
antes: validar manual → só depois suíte grande).

## 6. Torre recebendo knockback do Tiro Repulsivo (12/08/2026)

Usuário relatou: "temos que deletar o modulo de movimento da torre, pois
quando uso tiro repulsivo na torre, está tirando ela do lugar, e isso é
inadmissível". Proposta inicial do usuário era remover `TileMovement` da
torre inteira.

**Investigação**: `TowerSystem` (mira/ataque) não usa `TileMovement` pra
nada — resolve tile a partir de `Position` (`tower_pos.x // TILE_SIZE`)
direto. Mas `TileMovement` da torre NÃO é só sobre a torre se mover: é
também como o índice canônico de ocupação de tile (`_tile_movement_by_map`,
Fase 4.6/4.7, ver §29-§36) e os checks de colisão de `TileValidationSystem`/
`EnemyAISystem`/`MinionSystem` sabem "esse tile está ocupado pela torre,
não pode passar por cima" — remover o componente tiraria a torre desses
mecanismos também, não só do knockback. Sinalizado ao usuário como
conflito de arquitetura antes de agir (regra "nunca decide sozinho") —
usuário concordou com a alternativa proposta.

**Causa raiz real**: `server/spell_completion_processor.py::
_server_tiro_repulsivo` empurra QUALQUER alvo com `TileMovement`, sem
checar se é uma torre. Única fonte de knockback do jogo (busca por
`knockback_tiles` em `content/skill_config.py` só acha essa 1 skill) —
1 ponto só, sem duplicação.

**Correção**: guard logo após o dano ser aplicado (mesmo padrão do check
de `outcome in (miss/dodge/parry/immune/evade)` já existente ali) —
`if self.world.get_component(target_id, Tower) is not None: return`.
Dano continua normal; só o empurrão+stun de colisão são pulados. Mesma
convenção já usada pra CC (torre "não é um ser vivo", só dano — ver
[[project_towers_immune_to_cc]] em memória, e a mesma frase do usuário
02/08/2026 documentada lá), agora estendida a knockback, que era o único
efeito de skill que ainda não tinha esse guard em lugar nenhum.

**Teste**: `tests/test_towers.py::TestTowerKnockbackImmune::
test_tiro_repulsivo_nao_move_a_torre` (novo) — cast real de Tiro
Repulsivo (arqueiro com arco/aljava equipados, fluxo completo CAST_SKILL
→ cast_time → PROJECTILE_HIT_CS) contra uma torre real, confirma dano
aplicado normalmente E posição/`is_moving` inalterados. Achado ao
escrever o teste: o spawn de teste padrão (10,10) fica em terreno SÓLIDO
no mapa real (`map_1.csv` — só é usado por outros testes que já
bypassam colisão manualmente) — precisou de um corredor aberto de
verdade (linha 389, x≈115-123) E afastar mobs reais do mapa que por
coincidência bloqueavam o caminho do empurrão, senão o teste passava
"por acidente" (parede/mob barra o 1º passo independente do guard,
mascarando a prova). Confirmado por revert manual: sem o guard, o
teste falha com a torre de fato movendo 5 tiles (117→122).

## 7. Barra de XP dentro da BG mostrava a XP de fora da instância — mesmo gap de sync já fechado antes pra level/atributos/talento (13/08/2026)

Usuário relatou: dentro da BG (progressão normalizada de instância),
a barra de XP do personagem mostra a XP de FORA da instância — o
ideal é mostrar a progressão de dentro enquanto estiver lá, e voltar
a mostrar a XP normal ao sair, mesmo padrão que outros sistemas já
faseiam (level, atributos, talento, inventário, equipamento).

**Causa raiz**: `server/instance_progression.py::_push_stats_update`
já mandava vários overrides explícitos (level/atributos/talento) ao
entrar/sair/subir de nível na instância — mas NUNCA `current_xp`/
`xp_to_next_level`. O cliente só atualiza esses 2 campos via um canal
INCREMENTAL separado (`xp`/`xp_gained`, usado pelo XP real do mundo,
que soma no que já tinha localmente e dispara `process_levelups` com
a curva/cap do MUNDO REAL) — a instância nunca usava esse canal, então
a barra ficava presa no último valor real sincronizado antes de
entrar. Mesma classe de gap já documentada e corrigida 3x antes nesta
função (level, `talent_allocated`, atributos brutos) — só faltava
aplicar a mesma lição pra XP.

Achado um 2º gap ao investigar: mesmo corrigindo a entrada/saída,
`grant_instance_xp` só empurrava STATS_UPDATE quando o ganho de XP
completava um level-up (`_process_instance_levelup`'s `if leveled:`)
— um kill que não completa o próximo nível ficava totalmente invisível
pro cliente, a barra só "pulava" ao subir de nível.

**Fix**: `current_xp`/`xp_to_next_level` (valor FINAL, nunca delta,
mesmo padrão do resto de `_push_stats_update`) entram no payload
sempre. `_process_instance_levelup` passou a RETORNAR se rolou
level-up em vez de empurrar sozinha; `grant_instance_xp` decide o
único `_push_stats_update` do grant inteiro (evita 2 STATS_UPDATE
separados pro mesmo ganho) e passa a chamá-lo em TODO ganho, não só
quando sobe de nível.

**Pedido junto, mesma sessão**: feedback visual/sonoro que faltava
DENTRO da instância — texto flutuante de XP ganho, som de level-up
(igual fora da BG), e um texto flutuante NOVO de gold ganho (loot
automático da instância não tinha nenhum feedback visual até agora).
3 campos novos no payload, todos com prefixo `instance_` DE PROPÓSITO
— nunca reaproveitar os campos `xp`/`xp_gained` do XP real (dispararia
`process_levelups` local com curva/cap errados pra instância):
`instance_xp_gained` (FLT "+N XP"), `instance_leveled_up` (toca
`SOUNDS.play_ui("levelup")`, mesmo som de sempre), `instance_gold_gained`
(`grant_instance_gold`, FLT "+Ng" na cor dourada padrão já usada em
todo o resto do jogo pra ouro, `(255, 215, 0)`).

**Achado à parte, registrado por completude (regra do projeto: doc
errado é ele mesmo um problema a registrar)**: `arquitetura/
SISTEMAS_ECS.md` e o docstring de `tests/test_instance_progression.py`
ainda diziam "Sistema INERTE" pra progressão normalizada de instância
— mesmo o PRÓPRIO `server/instance_progression.py` já tendo corrigido
esse mesmo erro no próprio docstring em 06/08/2026 (ver §12). A
correção nunca se propagou pros outros 2 lugares que repetiam a
mesma frase. Ambos corrigidos nesta sessão.

**Testes**: `tests/test_instance_progression.py::
TestInstanceProgressionXpGoldClientSync` (5 testes — enter zera
current_xp no payload; exit restaura o real; ganho sem level-up ainda
empurra update com `instance_xp_gained` e SEM `instance_leveled_up`;
ganho com level-up marca `instance_leveled_up`; gold inclui
`instance_gold_gained`). `tests/test_client_ui.py` (5 testes novos —
`current_xp` sobrescreve sem somar; `instance_xp_gained` mostra FLT
sem alterar current_xp; `instance_leveled_up` toca o som; ausência não
toca som; `instance_gold_gained` mostra FLT dourado "+Ng"). Confirmados
por revert manual (comentando os campos novos e guardando os blocos
novos atrás de `if False and ...`) — falham sem o fix, restaurados
depois. Suíte completa (1133 testes) sem regressão.

**Validado pelo usuário em jogo** (13/08/2026): "Certo, validado."
RESOLVIDO.

## 8. Minimapa em tela cheia da BG — clique parou de mover o personagem + minion do mesmo tamanho que torre (13/08/2026)

Ship do dia anterior (§ acima desta, "Minimapa em tela cheia da BG"):
usuário confirmou o visual ("ficou ótimo"), mas reportou 2 problemas na
mesma mensagem — clássico caso de "mudança de render sem propagar pra
tudo que depende da geometria antiga", mesma classe de bug já
catalogada no projeto (regra "Atualização coesa ao adicionar sistema
novo", CLAUDE.md).

**Bug 1 (funcional) — clique no minimapa parou de mover o personagem**:
ao trocar o RENDER pro modo tela-cheia (`render_fullmap`, geometria
sem RADIUS/sem centralizar no player), o conversor de clique
(`game.py`, clique direito) continuou chamando `screen_to_tile` — o
método do modo RADAR antigo, que faz a conta errada nessa geometria
nova. Fix: `ui/minimap.py::screen_to_tile_fullmap` novo (mesma
matemática de escala de `render_fullmap`, sem RADIUS/player); `game.py`
decide qual dos dois chamar com o MESMO flag
(`InstanceInventoryUIState.active`) que já decide entre `render`/
`render_fullmap` — nunca deveria ter ficado destrancado disso da
primeira vez.

**Bug 2 (visual) — minion do mesmo tamanho que torre**: `_collect_bg_
minimap_dots` dava raio 2 pra QUALQUER `RemoteEntityMeta` com
`mob_key` em `MINION_TABLE` OU `TOWER_TABLE`, sem diferenciar. Fix:
raio por tipo — minion=1 (quase 1px), torre=2, player=3 (sem mudança) —
hierarquia visual clara de relance.

**Testes** (`tests/test_client_ui.py`, 5 novos):
`test_screen_to_tile_fullmap_converte_clique_pro_tile_certo`,
`test_screen_to_tile_fullmap_fora_do_frame_retorna_none`,
`test_screen_to_tile_fullmap_nao_usa_geometria_do_modo_radar`
(diferencial direto — mesmo clique, os 2 métodos TÊM que dar resultado
diferente, senão o bug não teria acontecido), `test_collect_bg_
minimap_dots_raio_por_tipo`. Confirmados por revert manual — falham
contra o código antigo. Suíte completa (1143 testes) sem regressão.

**Validado pelo usuário em jogo** (13/08/2026): "Certo, Validado."
RESOLVIDO.

## 9. Bush/árvore/pedra não bloqueavam linha de visão (LOS) como parede (13/08/2026)

Usuário, ao tentar validar a stealth de bush (§47) em jogo, percebeu que
a bush não escondia visualmente o que está do outro lado dela — e que
árvore/pedra grande tinham exatamente o mesmo problema, decisão antiga
dele mesmo que agora queria reverter. Pediu correção ANTES de continuar
os monstros de jungle (§48). Planejado formalmente (EnterPlanMode) com
investigação prévia de código — ver `C:\Users\l4nce\.claude\plans\
expressive-wondering-starlight.md` (mesmo arquivo reaproveitado dos 2
planos anteriores).

**Decisões confirmadas com o usuário antes de implementar**:
- Pegada de bloqueio de visão cobre o SPRITE VISUAL INTEIRO (não só o
  tile-âncora) pra sprites largos de bush — pegada multi-tile completa.
- Corrigir bush + árvore + pedra juntos, não só bush.

**Causa raiz real — não era falta de configuração, era um `continue`
prematuro**: `ui/systems.py::FogSystem.is_blocking` já lia
`tile_matrix[y][x].vision_height >= 2` desde sempre — o campo existia e
funcionava (parede já usa). O bloqueio nunca aconteceu porque
`engine/entity_factory.py::create_tilemap` pulava (`continue`) qualquer
objeto "puramente decorativo" (`not is_solid and elevation==0 and not
is_transition`) ANTES de checar `vision_height` — bush é
`collision_rect=None` → `is_solid=False` → sempre pulada, então
`vision_height` nunca chegava a ser escrito em `tile_matrix`,
independente do que estivesse configurado no catálogo.

**Por que não dava pra só reaproveitar `collision_rect` pra alargar a
pegada**: `_resolve_collision` (`engine/tileset.py`) SEMPRE deriva
`is_solid=True` de qualquer rect não-`None` — usar `collision_rect` pra
visão teria tornado bush sólida (quebra "continua andável"). Pro lado
oposto, árvore já tem `collision_rect` real mas DELIBERADAMENTE estreito
(só o tronco, ex. `t1`: 1 tile de colisão pra um sprite de 3×4 tiles) —
reaproveitar esse mesmo rect pra visão bloquearia só o tronco, não a
copa inteira. Conclusão: bloqueio de visão precisa de pegada PRÓPRIA,
independente da de colisão.

**Implementado**:
- `TileType.vision_rect` (`engine/tileset.py`) — campo novo, MESMO
  formato de `collision_rect` (`None`/`"full"`/`(x,y,w,h)`), mas NUNCA
  deriva `is_solid` — puramente sobre visão.
- `_rect_to_tile_offsets(rect, sprite_w, sprite_h)` — geometria
  extraída de dentro de `get_collision_offsets` (comportamento
  idêntico, zero mudança visível pra quem já usava) pra ser
  compartilhada com `get_vision_offsets(tile)` (novo), que aplica a
  MESMA matemática sobre `tile.vision_rect` em vez de
  `tile.collision_rect`.
- `create_tilemap` (`engine/entity_factory.py`) — 2 correções: (a)
  `purely_decorative` ganhou `and vision_height < 2` (objeto com
  bloqueio de visão nunca é pulado, mesmo não-sólido); (b) novo loop de
  overlay NÃO-destrutivo depois do loop de colisão já existente — pra
  cada célula da pegada de VISÃO que a pegada de COLISÃO não cobriu,
  `dataclasses.replace(tile_data[nr][nc], vision_height=...)` — só
  adiciona o bloqueio, preserva `is_solid`/`elevation`/tudo mais da
  célula original (terreno ou outro objeto que já estava lá). É isso
  que permite bush ficar andável na largura inteira do sprite mas
  bloquear visão, e árvore manter o tronco estreito sólido com a copa
  inteira bloqueando visão sem virar sólida.
- MODO CATÁLOGO (`discover_object_sheet_tiles`) — tupla de tile ganhou
  2 posições opcionais novas no final (`vision_rect_cfg`, `vision_h`),
  100% compatível pra trás (tupla de 6 a 9 elementos já cadastrada
  continua funcionando sem mudar 1 caractere).
- Conteúdo marcado com `vision_height=2, vision_rect="full"`: TODAS as
  bushes (`pl_b1`-`pl_b20`) e árvores (`pl_t1`-`pl_t15`, `collision_rect`
  do tronco intocado) da família "TX Village Plant"; pedras grandes
  (`pr_bigrock`, `pr_rock3`-`pr_rock6`, já `collision_rect="full"`) da
  família "TX Props". `pr_rock1`/`pr_rock2` (baixas, sem colisão) e
  trigo/repolho/abóbora/cenoura/grama/vômito da mesma família de
  vegetação ficaram intocados — sem bloqueio de visão, como antes.

**Correção a mim mesmo durante a investigação**: um comentário no
próprio arquivo (`OBJECT_SHEET_FAMILIES`, perto de onde o modo catálogo
é documentado) descrevia um 7º campo de tupla "vision_height" que NÃO
existia de verdade no parser — a posição real 6 é "piso"(elevação). Achado
lendo o parser direto, não confiando no comentário — corrigido antes de
qualquer código ser escrito em cima da suposição errada (`CLAUDE.md`:
"código vence, sempre").

**Testes** (`tests/test_vision_blocking.py`, 7 novos): pegada de visão
do bush bloqueia mas não solidifica; tronco da árvore sólido+bloqueia;
copa da árvore (só pegada de visão, fora do tronco) bloqueia sem
solidificar; pedra pequena (`rock1`) sem regressão — continua
`vision_height=0`; ponta a ponta via `ui.fov.compute_fov` — tile atrás
de um bush não entra no conjunto visível; `get_vision_offsets`/
`get_collision_offsets` continuam idênticos pra tile sem `vision_rect`
configurado (prova que o refactor do helper compartilhado não quebrou
nada). Confirmados por revert manual (2 rodadas — fix de
`purely_decorative`, e depois também o loop de overlay de visão): 3 dos
7 testes falham exatamente como esperado (pegada do bush, copa da
árvore, FOV ponta-a-ponta); os outros 4 continuam passando por não
dependerem do fix (tronco da árvore já era sólido antes por outro
motivo — não passava pelo `continue` de `purely_decorative` de qualquer
jeito — prova que os 3 que falharam são os testes certos, não falsos
positivos). Suíte completa (1179 testes, `pytest tests/ -q`) sem
regressão — 1 falha observada é a mesma flakiness de timing
pré-existente já registrada em §47 (`test_perf_log_improvements.py`,
não relacionada).

**Pendente**: nenhuma sprite real de bush/árvore/pedra foi repintada em
nenhum mapa por este trabalho — mecanismo pronto, efeito visível assim
que o usuário validar em jogo com o conteúdo já existente (bush/árvore/
pedra já pintadas nos mapas atuais herdam o novo bloqueio
automaticamente, já que o catálogo foi atualizado, não um mapa
específico).

## 10. Fog assimétrico ao ficar no mesmo tile de bush (13/08/2026, achado validando §49)

Usuário validou a Fase F (§49) em jogo e achou um bug novo: ao ficar NO
MESMO tile de uma bush (aglomerado real já pintado pelo usuário em
`maps/moba_battleground_objects.csv`, linhas 55-59 — 16 tiles de
`pl_b11`/`pl_b14` formando um clump orgânico), a névoa fica confusa —
só enxerga bem de UM lado. Desenhou 2 imagens (visão atual, cone
estreito assimétrico vs. esperada, área larga e simétrica, recortando
só ao redor de CADA bush separada) e pediu pra eu consultar referência
de FOG em MOBA antes de corrigir. Planejado formalmente (EnterPlanMode)
— ver `C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`
(mesmo arquivo reaproveitado dos planos anteriores).

**Pesquisa feita** (wiki oficial de League of Legends, via WebSearch):
> "Brush is opaque towards vision when viewed from the outside inwards
> and not the reverse. This means brush blocks enemy vision looking
> into it, but doesn't block vision from inside the brush looking
> outward."

Bush bloqueia de fora-pra-dentro, nunca de dentro-pra-fora — bate
exatamente com o desenho do usuário.

**Causa raiz**: `ui/fov.py::compute_fov` (shadowcasting recursivo de 8
octantes, Bergstrom) nunca checa bloqueio na própria origem — mas isso
não impede que tiles VIZINHOS dentro da MESMA bush (também
`vision_height>=2`, por fazerem parte da mesma pegada de visão
contígua da Fase F) sejam tratados como parede a distância 1. Um
aglomerado orgânico grande (como os 16 tiles do usuário) cerca o
jogador de bloqueio em várias direções ao mesmo tempo — só sobra visão
livre pro lado onde o aglomerado acaba rápido, daí o cone estreito e
assimétrico.

**Desenho**: ao computar o FOV a partir de uma origem, se o PRÓPRIO
tile de origem já é bloqueante (observador em cima de bush/copa de
árvore), isolar o blob conectado (4-direções) de tiles bloqueantes que
contém essa origem e tratá-lo como transparente SÓ pra esse cálculo —
bushes SEPARADAS continuam bloqueando normal (produz o recorte
simétrico por-bush do desenho 2, não um wedge gigante).

**Achado durante a escrita dos testes (não estava no plano original)**:
o blob, sendo puramente geométrico (só olha `vision_height`), vazaria
pra dentro de uma parede/pedra/tronco SÓLIDO se ele estivesse
fisicamente encostado numa bush no mapa — o BFS atravessaria a parede
inteira (também bloqueante) e a deixaria transparente por engano pra
quem está na bush do lado. Corrigido antes de fechar a feature:
`local_vision_blob` ganhou parâmetro `is_solid` opcional que PARA a
expansão do blob em qualquer tile sólido, mesmo que geometricamente
vizinho — a garantia "blob nunca inclui parede", que o plano já
assumia como verdadeira, só passou a ser verdadeira de fato com esse
parâmetro.

**Implementado**:
- `ui/fov.py::local_vision_blob(ox, oy, is_blocking, is_solid=None,
  radius_cap=64)` (novo) — BFS 4-direções a partir da origem; retorna
  `frozenset()` se a origem não for bloqueante (caminho comum, custo
  zero).
- `ui/systems.py::FogSystem.update` — `is_blocking` único trocado por
  `_is_blocking_from(ox, oy)`, computado POR ORIGEM (jogador e cada
  `ally_centers` — torre/minion/aliado podem estar em bushes
  diferentes, ou nenhuma). Só roda dentro do gate de custo que já
  existia (recomputa só quando o jogador muda de tile ou os centros de
  aliado mudam — sem trabalho extra em frames parados).
- `compute_fov` em si não mudou — só recebe uma closure `is_blocking`
  diferente por chamada, já era assim antes.

**Testes** (`tests/test_fog_bush_selfblock.py`, 5 novos): blob vazio
quando origem não bloqueia; blob retorna só o aglomerado conectado
(bush separada próxima não entra); ponta a ponta via `compute_fov` —
observador em cima do bloqueio enxerga dos 2 lados (cenário em "+"
reproduzindo o cone assimétrico de verdade); regressão — observador
FORA do bloqueio continua sem ver atrás (Fase F preservada); parede
encostada na bush não entra no blob (o achado do `is_solid`, acima).
Confirmados por revert manual (`local_vision_blob` forçado a sempre
devolver vazio): 3 dos 5 falham exatamente como esperado (blob
conectado, simetria ponta-a-ponta, parede-não-vaza); os outros 2
continuam passando por não dependerem do fix (prova que os 3 certos
são os testes certos, não falsos positivos). Suíte completa
(`pytest tests/ -q`) sem regressão.

**Pendente**: usuário validar em jogo no mesmo aglomerado de bush
(`maps/moba_battleground_objects.csv`, linhas 55-59) que a visão fica
simétrica ao ficar em cima, e continua escondendo o que está atrás de
OUTRAS bushes separadas.

## 11. Bushes "separadas" no mapa se fundem num blob só de qualquer forma (13/08/2026, esclarecimento — sem bug de código)

Usuário validou §50 e achou outro caso: perto de outras bushes (não
coladas na dele), ainda enxergava através delas — o esperado é só
enxergar através de bush ADJACENTE de verdade (tile livre ou outro
objeto no meio já devia cortar a conexão). Usuário também puxou uma
correção de processo importante aqui — ver nota separada abaixo, "regra
de processo restaurada".

**Investigação (código + dados reais, antes de qualquer proposta)**:
`local_vision_blob` (§50) está correto — só une tiles fisicamente
adjacentes (4-direções), confirmado programaticamente contra os dados
reais dos 2 mapas (`map_1_objects.csv`, `moba_battleground_objects.csv`).
A causa real é a PEGADA (`vision_rect="full"` da Fase F/§49): ela usa o
retângulo DECLARADO do sprite (largura×altura do PNG), não a silhueta
visível. Hipótese inicial (errada, oferecida ao usuário e descartada
depois de medir): "aparar" a pegada pro bounding-box real do canal
alpha (via Pillow) resolveria. Medido pra TODAS as 20 bushes de
`TX Village Plant.png` — preenchimento real vai de 18% a 84% do
retângulo declarado (média 52%), MAS a pegada em TILES (granularidade
32px) dá exatamente igual entre `"full"` e o retângulo justo do alpha
em 100% dos 20 tiles — a "gordura" transparente nunca é grande o
suficiente pra esvaziar uma coluna/linha de tile inteira. Aparar por
alpha não ajuda em nada aqui; foi proposto e comprovadamente descartado
com números reais antes de codar qualquer coisa (nenhuma linha de
código chegou a mudar).

**Causa real**: a maioria das bushes já é fisicamente de 2 a 6 tiles no
PRÓPRIO tamanho declarado (só `b7`/`b14`/`b17`/`b18` são de 1 tile) —
não é sobra de transparência, é tamanho real do desenho. Duas bushes
multi-tile pintadas a 1-2 tiles de distância vão sempre tocar as
pegadas, mecanismo funcionando exatamente como desenhado.

**Decisão do usuário**: manter a pegada fiel ao sprite real (rejeitada
a alternativa de encolher artificialmente pra um "núcleo" menor que o
visual, que quebraria a fidelidade visual da borda). Pra ter clumps
visualmente separados, o CONTROLE é na hora de pintar o mapa — usar
bushes de 1 tile pra tufos isolados, ou deixar pelo menos 1 tile
livre de verdade entre bushes maiores. Nenhum código mudou por este
item — é comportamento correto, só precisava de esclarecimento.

**Tabela de referência pra pintar mapas** (pegada em tiles, família
`TX Village Plant`, prefixo `pl_`):
- **1 tile** (seguro colar perto sem fundir): `b7`, `b14`, `b17`, `b18`
- **2 tiles**: `b1`, `b2`, `b8`, `b15`
- **4 tiles**: `b4`, `b5`, `b6`, `b9`, `b10`, `b11`, `b16`
- **6 tiles**: `b3`, `b12`, `b13`, `b19`, `b20`

**Regra de processo restaurada** (feedback direto do usuário, 13/08/2026):
usuário notou que eu parei de seguir o ritual "analisar código → analisar
referência → comparar as 2 → trazer pra discussão ANTES de decidir" em
pelo menos 2 pontos recentes desta sessão: (1) §49, propus
`vision_rect="full"` pra todas as bushes sem medir os pixels reais
antes — só reaproveitei o mesmo valor já usado em `collision_rect` por
ser o caminho mais simples de escrever; (2) §50, ao achar o problema de
bush encostada em parede "vazando" o bloqueio, corrigi sozinho
(`is_solid`) e só contei depois no resumo, em vez de parar antes de
codar. Neste item (§51), o ritual foi seguido de verdade: medi os
pixels reais ANTES de propor qualquer coisa, a 1ª hipótese (aparar por
alpha) foi TESTADA com números reais e descartada por não funcionar
(zero mudança em 20/20 tiles), e só depois disso a pergunta certa foi
levada ao usuário. Regra: nenhuma proposta de conteúdo/mecanismo entra
em `AskUserQuestion`/plano sem antes ter sido verificada contra dado
real (medir, não assumir) — "parece que vai funcionar" não é a mesma
coisa que "medi e funciona".

## 12. Pegada de visão da bush vira "só a base" — corrige a fusão de bushes separadas (13/08/2026)

Continuação do §51: usuário propôs a correção de verdade — em vez da
pegada de visão contar o sprite INTEIRO (`vision_rect="full"`, Fase
49), contar só a fileira de baixo (largura inteira, 32px de altura).
Verificado com números reais ANTES de propor/codar (ritual restaurado
no §51): recomputado o grupo solto de `map_1_objects.csv` (7 bushes,
x=105-113,y=464-468) — com `"full"` viravam 2 blobs grandes (6 e 24
tiles); com "base row" viram **6 componentes separados**, um por tufo
visual. O clump grande intencional da BG (16 placements,
`moba_battleground_objects.csv` linhas 55-61) continua **1 componente
só** (as bases já se tocam de propósito nesse desenho) — não quebra o
caso que devia continuar unido.

**Escopo, confirmado com o usuário**: só bush. NÃO pedra — pedra é
sólida (jogador nunca fica em cima, o problema de auto-bloqueio ao
ficar no mesmo tile nem existe pra pedra) e pedra grande bloquear o que
está atrás é intencional (é "alta", funciona como parede — confirmado
pelo usuário, não é bug). NÃO árvore — a copa (o que a Fase 49 queria
bloquear de propósito) fica na metade de CIMA do sprite; usar só a
base apagaria o bloqueio que a Fase 49 implementou pra árvore.

**Implementado**:
- `engine/tileset.py::_rect_to_tile_offsets` — novo sentinel
  `"base_row"` (homólogo a `"full"`, que já era resolvido
  internamente a partir das dimensões reais do sprite): calcula
  `(0, sprite_h - TILE_SIZE, sprite_w, TILE_SIZE)` — largura inteira,
  só os 32px de baixo. Nome novo (não reaproveita `"base"` já usado em
  `collision_rect`) porque lá `"base"` significa outra coisa (só o
  tile-âncora, ignora largura — usado pra objeto estreito tipo
  árvore); mesma string com semânticas diferentes em campos diferentes
  confundiria um leitor futuro.
- Catálogo (`OBJECT_SHEET_FAMILIES`, família "TX Village Plant") —
  `vision_rect_cfg` de `b1`-`b20` trocado de `"full"` pra `"base_row"`.
  `collision_rect` (continua `None`, andável) e `vision_height=2`
  inalterados. Árvore/pedra continuam `"full"`.

**Correção a mim mesmo, registrada em §53**: cheguei a achar (por
`git diff` contra o commit original) que `b17`/`b18` deveriam ser
`32×32` em vez do `64×64` atual, e tentei "corrigir" — a edição nunca
chegou a aplicar de verdade (falha silenciosa minha), e o usuário
confirmou depois (§53) que `64×64` É o tamanho certo desses 2 tiles.
Sorte dupla: o não-fix não quebrou nada porque nunca era pra ter sido
"fixado". Fica só como lembrete de checar o resultado de um Edit antes
de reportar como concluído — não vale a pena reabrir aqui de novo, ver
§53 pro relato completo.

**Testes** (`tests/test_vision_blocking.py`, 5 novos, classe
`TestBushBaseRowFootprint`): bush multi-tile (`b11`) só conta a
fileira de baixo; bush de 1 tile (`b14`) fica idêntica; árvore/pedra
grande continuam `"full"` (sentinel não vazou); 2 bushes em diagonal
que só se tocavam pela metade de CIMA (não pela base) deixam de se
conectar; 2 bushes com a BASE literalmente colada continuam formando 1
blob só. Confirmados por revert manual (`"base_row"` temporariamente
tratado como `"full"`): 2 dos 5 falham exatamente como esperado
(pegada do `b11`, e a conexão diagonal que devia quebrar); os outros 3
continuam passando por não dependerem do fix — inclusive achei e
corrigi um teste fraco nesse processo (`gap real`, com um espaçamento
grande demais que já ficava separado mesmo sob `"full"`, sem testar
nada de verdade — trocado por um caso diagonal que só se tocava pela
metade de cima, que realmente discrimina o fix). Suíte completa
(`pytest tests/ -q`) sem regressão.

**Pendente**: usuário validar em jogo no grupo solto de `map_1`
(x=105-113,y=464-468) e no clump da BG (linhas 55-61) que só bushes
com base colada revelam juntas.

## 13. Sair da bush não revalidava a própria visão — só o alvo "avisando" destravava (13/08/2026)

Usuário validou §55 e achou uma assimetria: ao sair da bush, quem
FICOU dentro perdia a visão de quem saiu (errado — olhar de dentro pra
fora não deveria bloquear) e quem SAIU continuava vendo quem ficou
(errado — olhar de fora pra dentro deveria bloquear). Só "destravava"
quando quem ficou andava pra outro tile. Usuário sugeriu a direção
certa antes de eu propor qualquer coisa: em vez de depender de quem
ficou se mover, quem SAI deveria avisar o servidor que não vê mais
quem ficou.

**Investigação**: recalculando `_has_tile_los` à mão pro cenário (A
sai, B fica), a conta em si já dava o resultado CERTO nos 2 sentidos —
o bug não era de cálculo, era de QUANDO recalcular e reenviar.
`server/session.py::_build_update_for_session` — o loop que reage a
`deltas["moved"]` é 100% centrado no ALVO: quando `X` se move, TODAS
as sessões reavaliam "ainda vejo X?" (cobre o outro lado continuar
vendo quem se moveu), mas nunca o inverso ("eu me movi, ainda vejo
quem eu já conhecia?"). Isso nunca foi problema antes porque toda
visibilidade anterior era simétrica por distância (AOI) ou já tinha
gatilho próprio (`visibility_changed`, pro ALVO mudar `is_visible` —
Camuflagem). A visão de bush (§54/55) é a PRIMEIRA regra cuja resposta
depende de onde o VIEWER está, não só do alvo — quebra a suposição
implícita de que só o alvo precisa avisar. Já existia um sweep
parecido (linha ~3441, "cobre mobs estacionários e players que
entraram em range sem se mover"), mas só pra REVELAR entidade
desconhecida — pula tudo que já está em `known_eids`, sem equivalente
pra esconder de novo o que o VIEWER deixou de ver.

**Implementado**: `server/session.py::_build_update_for_session` —
novo passo depois do loop de `deltas["moved"]`: se o PRÓPRIO
`session.entity_id` está entre quem se moveu neste tick, revalida cada
entidade ainda em `session.known_eids` contra `_can_see` completo (não
só bush — qualquer regra futura que dependa da posição do viewer já
fica coberta de graça); quem falhar vira despawn, mesmo tratamento já
existente de `aoi_exits`/`final_despawned`. Sem filtro/cache novo —
roda só nos ticks em que o player efetivamente andou.

**Testes** (`tests/test_tile_los_visibility.py`, classe
`TestBushViewerMovedRevalidation`, 2 novos): quem fica na bush continua
vendo quem saiu (regressão do que já funcionava); quem sai perde a
visão de quem ficou SEM precisar andar de novo (o bug relatado). Achei
2 erros no setup do próprio teste durante a escrita: (1) `Session`
leve (sem login completo) não populava `known_eids` via o fluxo normal
de descoberta (que depende de sweep de mob-hash ou WORLD_STATE,
nenhum dos dois presente aqui) — corrigido semeando `known_eids`
direto, já que isso é irrelevante pro que o teste cobre; (2) a rota de
saída inicial cruzava de volta pelo tile de bush que o alvo tinha
acabado de deixar (ainda fisicamente lá no mapa, bloqueio legítimo) —
corrigido roteando a saída por uma coluna que não recruza a bush.
Confirmado por revert manual: o teste do bug relatado falha
exatamente como esperado sem o fix; o de regressão continua passando.
Rodada a suíte completa múltiplas vezes — 1 falha isolada e não
reproduzida em 8 reruns subsequentes (`test_mob_despawn_sent_to_both_
players`), mesma classe de flakiness de `random` global já documentada
no próprio arquivo de teste (não bug de verdade, não relacionado a
este fix).

**Pendente**: usuário validar em jogo — sair de uma bush compartilhada
e confirmar que a visão muda IMEDIATAMENTE nos 2 sentidos, sem precisar
o outro lado andar de novo.

## 14. Investigação pós-§62: janela de kill (sem bug), bot travando em torre, ImportError no shutdown (16/08/2026)

Usuário testou o §62 em jogo e reportou 3 coisas: (1) desconfiança de
que a janela de 10s do kill credit só valia pro assist, não pro
matador principal; (2) bots com comportamento estranho perto de
torre inimiga — parecendo travar, incerto se a torre ataca eles; (3)
`ImportError: sys.meta_path is None, Python is likely shutting down`
no `formatTime` de `server/log.py`, disparado ao encerrar o servidor
com Ctrl+C.

**#1 — Janela de kill: investigado, SEM bug encontrado.** Releitura de
`RespawnMixin._handle_player_death` (§62) confirma que `_recent` (dict
já filtrado pela janela `PLAYER_KILL_ASSIST_WINDOW_S`) é a MESMA
estrutura usada tanto pra escolher o killer (`max(_recent, ...)`)
quanto pra iterar assists — não existe um filtro separado só pro
assist. Escrito um teste empírico reproduzindo o cenário exato
descrito (player A bate em V, hit envelhecido pra além de 10s via
manipulação direta do timestamp em `_player_damage_log`, depois um
MINION finaliza V) — `A.players_killed` ficou em 0, como esperado
(regra de execução). Também confirmado por grep que
`server/respawn_system.py:105` é o ÚNICO local que incrementa
`players_killed` — não existe um 2º caminho paralelo sem essa
proteção. Reportado ao usuário como "não reproduzido"; pedido mais
detalhe de repro (CLAUDE.md — nunca generalizar de teste sintético
pra "corrigido", mas o inverso também vale: não inventar fix pra bug
que não reproduz).

**#2 — Bot travando perto de torre: causa raiz achada e corrigida.**
`BotAISystem._advance_toward` (empurrar lane sem inimigo visível,
`server/bot_ai_system.py`) chamava `pathfinding.find_path(...,
dynamic_obstacles=None, ...)` — diferente do pathing de minion-wave
(`WorldServer._tick_minion_waves`), que já passa
`_get_tower_tiles_for_map(map_file)` como `dynamic_obstacles` desde
01/08/2026 (ver §"Torres como obstáculo da ROTA" em
`world_server.py`) exatamente pelo mesmo motivo: sem isso, o A* pode
entregar o tile EXATO de uma torre como próximo passo da rota, e como
o bot (assim como o minion) só anda 1 tile por decisão sem espaço pra
desviar, ele trava tentando pisar num tile que a torre nunca libera.
**Fix**: `_advance_toward` agora busca `world_server.
_get_tower_tiles_for_map(map_file)` e passa como `dynamic_obstacles`
— reaproveita a MESMA função já usada pelo minion-wave, sem
reimplementar. Bot ainda pode tomar dano de torre se passar perto
dela durante o combate real (`_engage_target`, fora de escopo do
pathing de lane) — isso é esperado, mesmo risco que minion já corre,
v1 continua sem "recuar" de torre por decisão explícita do usuário
(ver §60).

**#3 — ImportError no shutdown: causa raiz achada e corrigida.**
`SessionManager._on_tick`/`_run_dispatch` disparam
`asyncio.create_task(self._run_dispatch(...))` fire-and-forget (2
pontos: despacho normal e o re-agendamento dentro do próprio
`_run_dispatch` quando chega delta novo enquanto despacha). Sem
referência guardada, essas tasks corriam risco de ter sua exceção
"nunca retirada" numa corrida entre o Ctrl+C (`KeyboardInterrupt` em
`server/main.py::main()`) e a limpeza automática do `asyncio.run()` —
gerando o log "Task exception was never retrieved" já com o
interpretador em desligamento, momento em que o `formatTime`
customizado de `server/log.py` não consegue mais importar nada
(`sys.meta_path` já é `None`). Não indicava perda de dado — o
autosave (`mgr._autosave_all()`) já tinha rodado e logado sucesso
ANTES desse erro aparecer.

**Fix**: `SessionManager` ganhou `_dispatch_tasks: set` (referência
forte, registrado em `__init__`) + `_track_dispatch_task(task)` (novo
método — adiciona ao set, e no done-callback chama `task.exception()`
explicitamente pra "retirar" qualquer exceção antes da task ser
descartada, evitando o warning de coleta) + `shutdown_dispatch_tasks()`
(novo método async — cancela e espera qualquer task ainda pendente).
`server/main.py`'s `except KeyboardInterrupt` (dentro de `main()`)
passou a chamar `await mgr.shutdown_dispatch_tasks()` logo após o
autosave, ainda dentro do loop vivo — antes do `raise` que devolve o
controle pro `asyncio.run()` fazer sua própria limpeza. Isso fecha a
janela de corrida deterministicamente em vez de torcer pra
`asyncio.run()` vencer a corrida sozinho.

**Testes**: suíte completa (`pytest tests/ -q`) rodada após os 2
fixes (#2 e #3) — 1249 passed, zero regressão. O fix #2 (pathing) não
tem teste automatizado dedicado (mudança de 1 parâmetro reaproveitando
função já testada em `test_minions.py`); o fix #3 (shutdown) não é
testável via suíte automatizada (depende de sinal `KeyboardInterrupt`
real durante `asyncio.run()`) — verificação é manual, via playtest do
usuário encerrando o servidor com Ctrl+C.

**Pendente**: usuário confirmar em jogo — bot não trava mais perto de
torre inimiga (pode ainda tomar dano dela se aproximar durante
combate, isso é esperado); Ctrl+C no servidor não mostra mais o
ImportError; e, pro item #1, se o usuário AINDA observar um kill indo
pro player errado após golpe stale + torre/minion, descrever a
sequência exata (quanto tempo entre o último hit e a morte, contra
quem) pra tentar reproduzir de novo — o teste sintético não achou o
bug, mas não prova ausência em todo cenário real possível.

## 15. Player remoto nunca tinha Faction real (minimap + TAB/ESPAÇO selecionando aliado) + rota de bot desincronizando (16/08/2026)

Usuário testou o §63 e reportou que os bots continuavam com bugs: (1)
não aparecem no minimap (deveria ser igual ao círculo do player); (2)
ainda travando/pulando entre tiles perto de torre, mesmo com o fix de
`dynamic_obstacles` do §63; (3) TAB e ESPAÇO selecionando o bot
ALIADO (do próprio time) antes de qualquer inimigo.

**#1 + #3 — MESMA causa raiz**: o cliente nunca recebeu `Faction` de
NENHUM player remoto via ENTITY_SPAWN (só mob/torre/minion mandavam —
invariante documentada explicitamente no código há semanas). Isso
sempre foi "seguro" em aparência porque o PRÓPRIO player TAMBÉM nunca
tinha Faction real fora de contexto de time — mas dentro de BG/arena,
o PRÓPRIO player GANHA um `Faction("arena_time_a/b")` real via
`ARENA_COUNTDOWN` (`client/arena_handlers.py::my_faction`, existente
desde a Fase G). Com só um lado tendo Faction real,
`get_relationship_between` comparava `("arena_time_a", "jogadores")`
— par NUNCA declarado em `RELATIONSHIP` (`content/faction_data.py`),
caía no `DEFAULT_RELATIONSHIP = "neutro"` — e `can_engage()` já
retorna `True` pro tier "neutro" no PRIMEIRO check (hostil/neutro
sempre permite engajar), nunca chegando a consultar
`_client_pvp_context`/`_bg_opponents_server` (2º gate, que É o que
distingue aliado de inimigo). Resultado: TODO player remoto (aliado
OU inimigo) virava "engajável" assim que o PRÓPRIO player entrava
numa partida de time — e como o minimap da BG (`_collect_bg_minimap_dots`)
também exige `Faction` no player remoto pra colorir por time, NENHUM
player remoto (aliado, inimigo, bot ou humano) jamais apareceu nele —
provavelmente nunca notado porque ninguém tinha testado BG 2v2+ com
outro player de verdade visível antes dos bots.

Confirmado via teste diferencial (`_spawn_remote_player_entity` real,
local player com Faction "arena_time_a" simulando ARENA_COUNTDOWN,
bot remoto SEM campo "faction" no payload — reproduz o bug: `can_engage`
e TAB incluem o aliado; COM o campo — ambos corretos).

**Fix**: novo `WorldServer.player_faction_field(eid) -> dict` —
`{"faction": fac.faction_id}` só quando a entidade já tem `Faction`
real (contexto de time), `{}` fora disso (sem regressão pra mundo
aberto). Incluído nos 6 pontos que constroem payload de ENTITY_SPAWN
de player (`world_server.py::spawn_player`/`get_players_in_aoi`,
`session.py` ×3, `respawn_system.py::_handle_player_death`'s
re-anúncio). Cliente (`client/remote_entity_handlers.py::
_spawn_remote_player_entity`) passa a ler `data.get("faction")` e
anexar `Faction(faction_id=...)` real quando presente — os 4 call
sites que montam esse `data` dict (`network_handlers.py` ×3,
`session.py`'s sweep de descoberta) repassam o campo "faction" do
payload do servidor quando presente. Com os dois lados tendo Faction
real: mesmo time = `faction_a == faction_b` → sempre "amigavel"
(`get_relationship`); times opostos caem no par
`("arena_time_a","arena_time_b")` = "hostil", já declarado — nenhum
dos dois precisa mais do resolver de contexto pra decidir hostilidade
dentro de BG/arena (mesmo comportamento que o SERVIDOR já tinha desde
sempre). `_bg_opponents_server`/`_arena_opponents_server` continuam
existindo como composição pro caso de duelo/zona (não removidos).

**#2 — rota de bot desincronizando (causa DIFERENTE do §63,
adicional)**: `BotAISystem._advance_toward` fazia `bot.path.pop(0)`
ANTES de checar o retorno de `move_player` — se o passo fosse
rejeitado (tile ocupado por outro bot/minion/player no instante do
MOVE, ou qualquer outro motivo), o tile já tinha sido descartado da
rota em cache mesmo sem o bot ter se movido de verdade. A decisão
seguinte tentava o PRÓXIMO tile da rota (agora 2+ tiles de distância
da posição REAL), que `move_player` também rejeita (só aceita
distância 1) — cascata: bot fica com a posição real TRAVADA por
várias decisões enquanto a rota em cache esvazia sozinha tile a tile,
até finalmente recalcular do zero e voltar a andar — visualmente isso
é o "trava, depois pula" relatado. Fix: checar o retorno de
`move_player`; se `False`, descarta a rota (`bot.path = None`) na
hora, forçando recálculo já na PRÓXIMA decisão em vez de continuar a
cascata de tentativas inválidas.

**Testes**: `tests/test_bg_bot.py::TestBotAdvanceTowardRejectedMove`
(novo) — rota em cache com primeiro passo não-adjacente (simula
rejeição) → posição real não muda E rota é descartada (não continua
desincronizada). Revert-to-confirm: sem o fix, o teste falha
exatamente como esperado (rota permanece `[(26, 20)]` em vez de
`None`). Suíte completa (`pytest tests/ -q`) — 1250 passed (1249 +
este teste novo), zero regressão.

**Pendente**: usuário confirmar em jogo — bot aparece no minimap com
a cor do time certo; TAB/ESPAÇO não selecionam mais bot aliado (nem
humano aliado, quando testável); bot anda de forma suave perto de
torre, sem travar/pular.

## 16. Mob de jungle mostrava o mob_key cru como nome (16/08/2026)

Usuário testou o §66 ("deu certo") e achou mais 1 problema: mob de
jungle mostrando o código interno (ex: "jungle_lobo_alfa") em vez do
nome ("Lobo Alfa").

**Causa raiz**: `_build_combat_entity` (`engine/entity_factory.py`)
resolve o nome exibido como `identity_name or race` — `race` aqui é o
parâmetro passado a `create_enemy()`, que pra jungle é o PRÓPRIO
`mob_key` cru (`_create_jungle_camps` chama `create_enemy(...,
race=mob_key, ...)`, porque `race` também funciona como CHAVE de
lookup em `JUNGLE_MOB_TABLE`/`JUNGLE_BOSS_TABLE`). Mob comum de
`MOB_TABLE` não tem esse problema porque a CHAVE da tabela já É um
nome legível (`"Rato"`, `"Aranha"` — a própria chave dobra como
display name); jungle usa chave interna (`"jungle_lobo_alfa"`) +
campo `"display_name"` SEPARADO — convenção diferente que
`create_enemy()` não conhecia. `create_combat_npc()` (NPC de combate)
já tinha resolvido isso pro caso dele, passando `identity_name`
explícito pra `_build_combat_entity` — `create_enemy()` nunca expunha
esse parâmetro.

**Fix**: `create_enemy()` ganhou `identity_name: str = ""` (repassado
pra `_build_combat_entity`, mesmo mecanismo que `create_combat_npc()`
já usava — nada novo, só exposto no ponto que faltava).
`_create_jungle_camps` passa `identity_name=mdef.get("display_name",
mob_key)`. `EntityIdentity.race` (usado pra tooltip/flavor, "Fera"
etc.) não foi afetado — já vinha certo via `mob_def["race"]`, só o
`.name` estava errado.

**Teste**: `tests/test_jungle_camps.py::
TestJungleMobHostility::test_nome_exibido_e_o_display_name_nao_o_mob_key_cru`
— confirma `EntityIdentity.name` bate com `JUNGLE_MOB_TABLE[...]
["display_name"]`, não com o mob_key. Revert-to-confirm: sem o fix, o
teste falha reproduzindo o bug relatado (`'jungle_lobo_alfa' !=
'Lobo Alfa'`). Suíte completa (`pytest tests/ -q`) — 1261 passed
(1260 + este teste), zero regressão.

## 17. Bot travava o tick com TypeError ao empurrar lane com curva (17/08/2026)

Usuário reportou erro repetido no console do servidor (a cada ~10
ticks): `TypeError: unsupported operand type(s) for -: 'tuple' and
'int'`, em `find_path` chamado por `BotAISystem._advance_toward`.

**Causa raiz**: `BotAISystem._lane_goal` (`server/bot_ai_system.py`,
§60) reimplementava seu PRÓPRIO parse de `lane["target_tile"]`
assumindo a forma CRUA do JSON (`[x,y]` OU `[[x,y],[x,y],...]`) — mas
`engine/map_loader.py` já NORMALIZA isso na carga do mapa pra SEMPRE
virar `list[tuple[int,int]]` (par único vira lista de 1 tupla; lane
com curva vira lista de várias tuplas) — o próprio comentário do
loader já avisava "quem consome (`_tick_minion_waves`) nunca precisa
saber qual dos 2 formatos foi usado", mas `_lane_goal` foi escrito
sem essa premissa. Pra lane RETA (mid), o waypoint normalizado é uma
lista de 1 elemento — nenhum dos 2 `isinstance` do código velho
batia, `_lane_goal` devolvia `None` silenciosamente (bot só não
empurrava a lane, sem erro visível). Pra lane com CURVA (top/bot, 2+
waypoints) — a real causa do erro no log — `wps[0]` já é uma TUPLA
(não `list`), o 1º check falhava, caía no 2º (`len(wps)==2`, que
bate por coincidência quando a curva tem exatamente 2 waypoints) e
devolvia `tuple(wps)` — uma TUPLA DE TUPLAS como `goal` — que
`find_path` tentava subtrair como se fosse `(int,int)`, quebrando a
cada decisão do bot (a cada ~0.35s, daí o erro repetindo no log).

**Fix**: `_lane_goal` para de reimplementar o parse — usa
`wps[-1]` direto (já é o último waypoint, já normalizado), mesmo
princípio que `_tick_minion_waves` já seguia.

**Teste**: os testes de `tests/test_bg_bot.py::TestBotAISystem`
usavam dado de fixture CRU (`"target_tile": [40, 20]`, forma que
NUNCA acontece em produção — mascarava esse bug exato) — corrigidos
pra usar a forma normalizada de verdade (`[(40, 20)]`). Novo teste
`test_bot_segue_lane_com_curva_varios_waypoints` reproduz o cenário
real (lane com 2 waypoints) — revert-to-confirm: sem o fix, o teste
levanta o MESMO `TypeError` do log do usuário. Suíte completa
(`pytest tests/ -q`) — 1262 passed (1261 + este teste), zero
regressão.

**Pendente**: usuário confirmar que o erro não aparece mais no log do
servidor durante uma partida de BG.

## 18. Bot travando perto de torre + retentando skill em cooldown pra sempre (25/08/2026, 2 bugs reais de playtest, investigados por reprodução ao vivo)

Usuário reportou (mesmo playtest do §70): mago não causava dano (outras
classes não confirmadas), e alguns bots travavam perto de torre inimiga
sem desviar. Pediu pesquisa de referência + discussão antes de
implementar (não só ler código — reproduzir de verdade).

**Hipótese inicial FALSA, corrigida pelo usuário**: rodei
`_compute_lane_route_tail` pras 6 lanes reais (`maps/moba_battleground.csv`,
via `WorldServer._load_instance` + `_activate_minion_lanes`) e as 6
falharam ("sem caminho") — bati exatamente no spam
`[MinionWave] rota falhou` de `logs/server.log` de hoje (22 minutos,
todas as lanes, sem sucesso). Concluí que o canto do spawn do time B
(topo-direita, 225 tiles) estava desconectado do resto do mapa
(flood-fill confirmou: região de 225 tiles vs 6732, sem tile em comum).
**Usuário testou pessoalmente e contestou**: minion segue a lane
perfeitamente, só o bot trava perto de torre. Investigando de novo,
achei a causa do meu erro: existe uma mecânica de abertura de portão
(`InstancedMatchMixin._im_open_gate_if_ready`, troca tiles de portão de
sólido pra piso quando o countdown da partida vence,
`server/instanced_match_processor.py:56-78`) que meu teste nunca
disparava (só `_load_instance` direto, sem simular o fim do countdown).
Repeti o teste abrindo o portão manualmente (mesmos tiles de
`DEBUG_BG_GATE_TILES`, descobertos via char 'D' no terrain.csv) — as 6
lanes calculam rota perfeitamente (175/92/175/175/93/173 tiles). Sem
bug de mapa nenhum — a hipótese foi descartada, registrada aqui como
lição: **"reproduzir o sintoma real" inclui simular TODA a sequência de
eventos do jogo real (aqui, o fim do countdown), não só carregar o
mapa e chamar a função isoladamente** — meu primeiro teste tecnicamente
"reproduziu uma falha", mas não a situação que o usuário via de
verdade.

**Causa real do travamento em torre (confirmada por reprodução, desta
vez com o portão aberto)**: simulei um bot andando a rota completa da
lane "top" (175 tiles, mapa real, 20 torres reais carregadas). No tile
97/175 a rota entra no alcance de uma torre inimiga
(`attack_range_tiles=8`). `BotAISystem._dive_allowed` (regra do §69)
só libera avanço em tile coberto por torre se (a) minion aliado JÁ
DENTRO do alcance DAQUELA torre específica, ou (b) alvo <30% HP —
`_push_lane` (empurrar lane sem perseguir ninguém) sempre chama com
`target_eid=None`, então só a condição (a) pode liberar. Testei essa
condição direto contra o estado real (sem minion no alcance daquela
torre no instante do teste) — bloqueada, exatamente como reportado.
Minion NUNCA passa por essa checagem (só bot tem essa regra) — por
isso segue reto sempre, batendo com "minion ok, bot trava". A janela
de liberação era frágil: minion morre rápido pra torre, raramente está
parado exatamente dentro do alcance dela quando o bot chega.

**Causa real do bot preso tentando a mesma skill (confirmada por log
real de hoje)**: `logs/server.log` mostra um bot (arqueiro) tentando
`picada_escorpiao` a cada ~0,35s (cadência de decisão do bot,
`BOT_DECISION_INTERVAL_S`) por 2+ minutos seguidos, sempre rejeitado
pelo servidor por cooldown real (`[Skill] REJEITADO (CD)`, contagem
regressiva de ~3,9s repetindo). Causa:
`BotAISystem._try_cast_offensive_skill` decidia com base em
`PlayerSkills.skill.current_cooldown` — campo LOCAL, só atualizado
quando um CLIENTE de verdade processa `SKILL_RESULT` pela rede. Bot
não tem cliente, então esse campo nunca reflete o cooldown real
(rastreado no servidor via `self._skill_last_used`/`_skill_effective_cd`,
`server/skill_processor.py`). O bot achava pra sempre que a skill
estava pronta, tentava de novo a cada decisão, e como
`_try_cast_offensive_skill` já retornava `True` (reservando a decisão
do tick) assim que ACHAVA uma skill aparentemente pronta — sem saber
se o request ia ser aceito — nunca caía pro auto-attack
(`_engage_target`). Mesma classe de bug afetaria qualquer skill/classe
com cooldown real; provável parte do "mago sem dano" também
(`bola_de_fogo` tem `cast_time=1.5s`, mais uma chance de ficar preso
tentando recast).

Discutido com o usuário antes de codar (`AskUserQuestion`): dive-gate
vira "minion aliado PERTO DO BOT" (não da torre) — cobre o caso de
estar andando JUNTO da leva, sem depender de timing exato. Flow
field/Dijkstra map (pergunta feita à parte, pesquisa com WebSearch)
descartado por enquanto — usuário confirmou que nenhum dos 2 bugs
reais era de custo/algoritmo de pathfinding, e pediu pra só retomar
se surgir dado de profiling mostrando que pesa (ele mencionou já ter
visto avisos de "max_nodes (4000) atingido" no terminal — extremamente
provável que sejam exatamente os 22 minutos de spam do §70/hipótese
falsa acima, não um problema de performance separado).

**Fix 1 — dive-gate**: novo `WorldServer._allied_minion_near_tile(map_file,
tile, faction_id, radius_tiles)` (Chebyshev, minion vivo) substitui
`_allied_minion_covers_tower` (removido, único chamador). `BotAISystem
._dive_allowed` ganha parâmetro `my_tile` (posição ATUAL do bot, não a
torre) e nova constante `BOT_DIVE_ALLY_MINION_RADIUS_TILES=4`; os 2 call
sites (`_push_lane`, `_advance_toward`) passam `cur` (já calculado em
ambos).

**Fix 2 — skill ready**: novo `WorldServer._skill_ready(eid, skill_obj)
-> (ready, remaining)`, extraído da validação de cooldown de
`_process_skill_requests` (`server/skill_processor.py`) — mesma fonte
autoritativa, comportamento idêntico pro player real (só isolado pra
reuso; `_sk_key`/`_now_srv`/`_sk_cd` continuam computados ali também,
reusados mais abaixo no registro de CD pós-sucesso — bug de regressão
achado e corrigido na 1ª rodada da suíte completa, `NameError:
_now_srv` em 24 testes, porque a extração inicial removeu essas 3
variáveis sem notar que código MAIS ABAIXO na mesma função também
dependia delas). `BotAISystem._try_cast_offensive_skill` troca
`skill.current_cooldown > 0` por `not world_server._skill_ready(eid,
skill)[0]`.

**Testes**: `tests/test_bg_bot.py::TestBotDiveGate` — 5 testes
existentes ajustados pra nova assinatura (`my_tile`); teste renomeado
pra "minion perto do bot" + novo teste de regressão ("minion perto da
TORRE mas longe do bot" — cenário que a condição antiga liberava,
agora continua bloqueado). `TestBotSkillReady` (novo) — bot com CD
autoritativo setado (`_skill_last_used`/`_skill_effective_cd`) mas
campo local zerado não reenvia a skill e cai pro auto-attack.
Revert-to-confirm nos 2: dive-gate (checar perto da torre em vez do
bot) e skill-ready (voltar pro `current_cooldown` local) — ambos
reproduzem a falha exata do relato/log antes do fix, confirmam depois.
Suíte completa (`pytest tests/ -q`, `py -3.10`) — 1280 passed, zero
regressão (após corrigir o `NameError` da extração).

**Pendente**: usuário confirmar em jogo — bot não trava mais perto de
torre andando junto da própria leva; skills não ficam mais "mudas"
depois da 1ª tentativa em cooldown real. Se o mago AINDA não causar
dano depois disso, investigar mais (mana/LOS do handler de
`bola_de_fogo` não descartados, só não confirmados por log ainda).

## 19. Bot travando perto da PRÓPRIA torre — pé de colisão de 2 tiles invisível pro pathfinding (25/08/2026)

Correção do §71 (dive-gate) não resolveu o caso relatado — usuário
mandou print mostrando o bot parado do lado da **própria** torre
("Torre de Flechas"), não de uma inimiga. **Lição do incidente**:
antes de investigar de novo, perguntei diretamente (por instrução
explícita do usuário — "não quero que mexa no código sem ter certeza,
se tem dúvida, pergunte") se a torre era aliada ou inimiga e QUANDO o
travamento acontecia, em vez de assumir que era o mesmo mecanismo do
§71. Confirmado: torre aliada, bot trava logo depois de sair do spawn.
Como dive-gate só se aplica a torre INIMIGA (`_enemy_towers_covering`
filtra `fac.faction_id == my_faction_id: continue` — conferido,
correto), o fix do §71 nunca teria endereçado este caso — eram 2 bugs
diferentes atrás do mesmo sintoma relatado ("trava perto de torre").

**Causa raiz, confirmada por reprodução ao vivo** (mapa real, portão
aberto igual ao §70, torres reais carregadas):

1. Torre tem pé de colisão REAL de 2 tiles desde §42 (12/08/2026,
   sprite de 64px) — ponto único de verdade
   `engine/world_systems.py::entity_footprint_tiles(world, eid, tm)`,
   usado por `TileValidationSystem`/`is_tile_walkable` (bloqueio real)
   e mais 6 pontos listados naquela seção.
2. `WorldServer._get_tower_tiles_for_map` — usado como
   `dynamic_obstacles` tanto por `_compute_lane_route_tail` (rota
   ÚNICA compartilhada minion/bot, extraída em §69) quanto por
   `BotAISystem._advance_toward` (fallback A* do bot) — **não estava
   na lista de 7 pontos corrigidos no §42**. Só registrava o tile
   ÂNCORA de cada torre.
3. Medido nesta sessão: rota da lane "top"/time A (176 tiles) contém
   `(4, 88)` na posição 5 — bem cedo, perto do spawn. Esse tile é o 2º
   tile do pé real da PRÓPRIA torre do time A (`torre_de_flechas`,
   âncora `(3, 88)`) — invisível pro conjunto de obstáculos que
   `_get_tower_tiles_for_map` devolvia (só `(3, 88)` estava lá).
4. **Por que minion passa reto e bot não** (mesma rota inicial ruim
   pros dois — a diferença é o fallback quando `is_tile_walkable`
   rejeita o passo, corretamente, no tile ruim):
   - `MinionSystem._walk_toward` recalcula via
     `self._get_occupied_tiles` — **já corrigida no §42** (usa
     `entity_footprint_tiles`) — minion desvia certo.
   - `BotAISystem._advance_toward` recalcula via
     `_get_tower_tiles_for_map` — **mesma função com o bug do item
     2** — o recálculo dá o MESMO caminho ruim de novo, falha no
     mesmo tile, descarta e recalcula — loop permanente no mesmo
     lugar. Bate exatamente com o print (bot colado na própria torre,
     logo após o spawn).

**Fix**: `_get_tower_tiles_for_map` passa a usar
`entity_footprint_tiles` (trocou `Position` por `TileMovement` —
`entity_footprint_tiles` exige `tm`) em vez de só o tile âncora. 1
função corrigida, sem tocar em `_compute_lane_route_tail` nem
`_advance_toward` — os dois consomem essa função sem mudança própria.

**Testes**: `tests/test_minions.py::
test_rota_desvia_do_pe_de_2_tiles_da_torre_nao_so_do_ancora` e
`tests/test_bg_bot.py::TestBotPushLaneV2::
test_rota_do_bot_desvia_do_pe_de_2_tiles_da_torre` — os dois plantam
uma torre com o ÂNCORA FORA do caminho reto mas o 2º tile do pé (via
`entity_footprint_tiles`) EM CIMA dele (mesma técnica isolando o bug:
só o 2º tile precisa ser invisível, não o âncora). Revert-to-confirm
(voltar `_get_tower_tiles_for_map` pro tile único) — os 2 falham
exatamente como o bug relatado, confirmam depois do fix. Suíte
completa (`pytest tests/ -q`, `py -3.10`) — 1282 passed, zero
regressão.

**Pendente**: usuário confirmar em jogo — bot não trava mais do lado
da própria torre logo após spawnar, segue empurrando a lane igual ao
minion.

## 20. Preferência de slot de skill unificada dentro/fora da BG (25/08/2026)

Usuário relatou: dentro da BG, já com as 6 skills reveladas, arrastou
Interceptar do slot 3 pro slot "R" (10º slot da hotbar customizada,
1,2,3,4,5,R,T,F,Q,E), trocando de lugar com a skill que já estava lá.
No próximo level-up (BG dá XP/level o tempo todo), a troca desfez
sozinha — Interceptar voltou pro 3, a outra skill voltou pro R.

**Causa raiz 1, confirmada lendo o caminho completo (cliente e
servidor)**: arrastar uma skill dispara `game.py::_save_config()`
(único call site real de `_send_hotbar_update()` — confirmado via
`tests/test_hotbar_bg_leak.py`). `_save_config()` tinha um guard de
`InstanceInventoryUIState.active` adicionado em 04/08/2026 pra resolver
um bug DIFERENTE (não salvar o layout temporário da instância por cima
do real em `config.json`) — mas esse guard bloqueava JUNTO o envio de
`HOTBAR_UPDATE` pro servidor, não só a escrita local. Sem o
`HOTBAR_UPDATE`, o arrasto ficava só visual/local; o array
`PlayerSkills.skills` do SERVIDOR nunca soube da troca. Todo level-up
dentro da BG manda um `STATS_UPDATE` novo com `skills_hotbar`
(`server/instance_progression.py::_push_stats_update`) — snapshot 1:1
do array do SERVIDOR (que nunca mudou); a reconciliação POR SLOT do
cliente (fix do §34.74.23, `client/network_handlers.py`) via que o
`skill_id` esperado ali era outro e recriava o Skill — desfazendo a
troca nos 2 slots envolvidos. Verificado ANTES de propor que destravar
`_send_hotbar_update()` é seguro: `SessionManager._persist_character`
recusa persistir por completo enquanto `is_in_normalized_progression`
(desde o incidente §34.74.15), então nada do que acontece no overlay
temporário da instância pode vazar pro banco.

**Escopo ampliado, decisão do usuário durante a discussão do fix**:
configurar o slot de uma skill, dentro OU fora da BG, deve ser a MESMA
preferência — se a skill ainda não existe de verdade no personagem
(baixo level, real ainda não desbloqueou), a escolha feita dentro da
BG deve valer também quando ela desbloquear fora, e vice-versa.
Guardada só em `config.json` (nunca no banco do servidor — instância
não persiste mesmo). Decisão adicional: jogar fora a lógica do
SERVIDOR que tentava adivinhar a posição espelhando a hotbar real
(`_real_slot_by_skill_id`/`reserved_slots`, mecanismo de 04-06/08/2026)
— cliente decide 100% da posição sozinho.

**Redesenho implementado**:
- Novo `characters.{nome}.skill_slot_prefs` em `config.json`
  (skill_id→slot), escrito por `game.py::_skill_slot_prefs_to_dict`
  (MERGE, nunca substitui o mapa inteiro — skill configurada num
  contexto e não visível no outro não pode perder a entrada) sempre
  que `_save_config()` roda, dentro OU fora da BG (diferente de
  `hotbar`/`consumable_bar`, que continuam bloqueados dentro da
  instância — só refletem o que está NA TELA agora).
- Novo `game.py::_preferred_slot_for_skill(ps, skill_id)` — lido pelos
  3 pontos que colocam skill nova numa hotbar: `ui/trainer_system.py::
  _do_learn` (aprender de treinador, mundo aberto), `client/
  network_handlers.py::_handle_msg_skill_granted` (recompensa de
  quest, mundo aberto), e o bloco `skills_hotbar` (revelação dentro da
  instância) — os 3 antes só faziam "primeiro slot vazio", sem
  preferência nenhuma.
- Bloco `skills_hotbar` (`client/network_handlers.py`) REESCRITO —
  posição decidida 100% pelo cliente, payload do servidor só serve pra
  descobrir QUAIS skill_ids existem agora: (a) skill presente no
  `PlayerSkills` local mas AUSENTE do payload é REMOVIDA (achado
  durante os testes — necessário pra continuar limpando a hotbar do
  mundo aberto ao ENTRAR na instância, mesmo motivo do bug original de
  02/08/2026, `test_skills_hotbar_substitui_a_barra_local_por_completo`
  em `tests/test_client_instance_sync.py` provou essa lacuna);
  (b) skill já conhecida E ainda presente no payload NUNCA é
  retocada — nem objeto novo, nem realocação, não importa a posição
  que o servidor sugira (é isso que corrige o bug relatado);
  (c) skill nova usa a preferência do jogador ou cai no primeiro slot
  vazio.
- `server/instance_progression.py::_grant_instance_skill` simplificado
  de volta a "primeiro slot vazio" — `_real_slot_by_skill_id` e os
  parâmetros `real_slot_by_sid`/`reserved_slots` removidos (redundante
  com a preferência do cliente).

**Testes**: `tests/test_hotbar_bg_leak.py` — `TestInstanceSkillGrant`
substitui `TestInstanceSkillPreferredSlot` (mecanismo removido);
`test_reordenacao_ao_vivo_alimenta_slot_preferido_da_instancia`
removido (testava o mesmo mecanismo); novos testes de
`_skill_slot_prefs_to_dict` (merge), `_preferred_slot_for_skill` (não
rouba slot ocupado), e ponta a ponta via `_handle_msg_stats_update`
real (preferência vence a posição sugerida pelo servidor; skill já
conhecida nunca se mexe — regressão do bug relatado). Revert-to-confirm
na reconciliação nova: reverter pro modelo posicional antigo reproduz
os 2 sintomas exatamente (posição errada e skill retocada), confirma
depois do fix. Efeito colateral achado só rodando a suíte completa (não
antecipado no plano): `test_skills_hotbar_substitui_a_barra_local_por_completo`
quebrou porque a reconciliação nova (puramente aditiva) nunca removia
skill que sumiu do payload — corrigido adicionando o passo (a) acima
antes de reportar como pronto. Suíte completa (`pytest tests/ -q`,
`py -3.10`) — 1285 passed, zero regressão (3 falhas encontradas no meio
do caminho — 2 fixtures leves de teste sem o método novo,
`_preferred_slot_for_skill`, e 1 teste de timing flaky sem relação —
todas corrigidas/confirmadas não-relacionadas antes do resultado final).

**Pendente**: usuário confirmar em jogo — configurar uma skill num
slot dentro da BG, sair, entrar de novo (ou desbloquear ela de verdade
no mundo aberto) — o slot escolhido continua o mesmo.

## 21. 3 correções não relacionadas — Punho no Queixo roubando slot, TAB sem neutro, SPACE reselecionando (25/08/2026)

Usuário validou o §73 e reportou 3 problemas novos, sem relação entre
si — os 3 achados por leitura de código real, não suposição (depois de
2 teorias erradas hoje mais cedo, ver §71/§72).

### 1. "Punho no Queixo" ocupava o slot do Interceptar dentro da BG

Relato: guerreiro sobe pro level 2 na BG, em vez de Interceptar
aparecer, aparece "Punho no Queixo" (nem está em
`INSTANCE_SKILL_UNLOCK_ORDER`) — Interceptar só aparece no level 3.
Usuário confirmou: não arrastou nada manualmente; personagem real é
nível 3 (não tem pontos de talento pra Punho no Queixo de verdade
ainda) — pediu explicitamente pra NÃO corrigir "não funciona" (correto,
não está na lista).

Causa raiz, achada lendo código (não é nada mexido nas sessões de
hoje): `server/combat_processor.py:157-176` — mecanismo de "Punho no
Queixo" (talento Cavaleiro): toda vez que o guerreiro acerta um golpe
de verdade com `attacker_cs.pnq_enabled` ativo, o código procura a
skill em `PlayerSkills.skills`; se não achar, CRIA e insere no
primeiro slot vazio (`ps.skills.index(None)`) só pra rastrear
`charges`/`pnq_counter` entre golpes — nunca soube de
`INSTANCE_SKILL_UNLOCK_ORDER`. Como a instância pré-aloca a árvore de
talentos no MÁXIMO desde a entrada (`enter_normalized_progression`),
`pnq_enabled` fica `True` imediatamente — no PRIMEIRO golpe que o
guerreiro acerta (antes mesmo do level 2 chegar), punho_no_queixo é
inserida no slot 1 (primeiro vazio, golpe_poderoso já ocupa o 0).
Quando o level 2 chega e Interceptar (unlock_order[1]) é concedida, o
slot 1 já está ocupado — ela vai pro próximo slot livre no servidor, e
como a reconciliação do §73 só olha "esse skill_id está no payload?"
(não posição), ela só aparece quando o servidor finalmente lhe dá um
slot — daí "aparece 1 level depois".

Reproduzido via simulação level-a-level com o `config.json` REAL do
personagem "Xadread" (achei uma preferência de slot sobrando de
`skill_slot_prefs: {golpe_poderoso:0, punho_no_queixo:1, interceptar:5}`)
— não bateu exatamente com o relato (minha simulação não incluía o
mecanismo de combate, só o §73), então pedi 2 perguntas diretas ao
usuário antes de seguir (arrastou manualmente? mesmo personagem?) em
vez de chutar uma 3ª teoria.

**Fix — só no CLIENTE**, sem tocar no mecanismo de carga do servidor
(funciona certo e sem gambiarra fora da instância — Punho no Queixo é
ambient real do build Cavaleiro): bloco `skills_hotbar`
(`client/network_handlers.py`) ganha um filtro — dentro da instância
(`InstanceInventoryUIState.active`), só aceita skill_id que está em
`INSTANCE_SKILL_UNLOCK_ORDER[classe]`; qualquer skill "extra" que
apareça no array do servidor por outro motivo é ignorada, nunca ocupa
slot. Fora da instância, filtro não se aplica.

### 2. TAB não seleciona alvo "neutro" (jungle etc.)

`ui/systems.py::_visible_enemies_sorted` — loop de mob/torre/minion
usava `is_hostile` (só tier "hostil"), decisão EXPLÍCITA do próprio
usuário em 01/08/2026 ("TAB/SPACE só pra hostil de verdade, neutro/
aliado só por clique") — mas isso foi ANTES da jungle existir (tier
"neutro", 16/08/2026). Usuário reverteu esse recorte: TAB precisa
alcançar neutro também.

**Fix**: trocado `is_hostile` por `can_engage` (hostil OU neutro, só
bloqueia amigavel) — MESMA função que o loop de player remoto (linhas
abaixo, no mesmo método) já usava. Nenhuma lógica nova.

### 3. SPACE troca de alvo em vez de atacar o selecionado (jogo todo)

`ui/systems.py::_space_engage` sempre recalculava "inimigo mais
próximo visível na tela" do zero, ignorando
`combat_state.target_entity_id` por completo — se o alvo já
selecionado (TAB/clique) não fosse o geometricamente mais próximo,
SPACE trocava pra outro em vez de atacar o selecionado. Não é
regressão de nada mexido hoje — comportamento original da função.

**Fix**: checa `combat_state.target_entity_id` primeiro — se houver um
e `utils.is_target_alive()` (ponto único de verdade do projeto pra
"alvo válido no cliente", cobre mob local/player remoto/mob remoto)
confirmar vivo, usa ELE direto (sem busca); só cai pra busca de "mais
próximo visível" (código antigo, inalterado) sem alvo válido
selecionado.

**Testes**: Punho no Queixo — `tests/test_hotbar_bg_leak.py`, payload
`skills_hotbar` com skill fora da ordem curada + `InstanceInventoryUIState.
active=True` → não aparece; mesmo payload fora da instância → aparece
normal (regressão). TAB — `tests/test_client_ui.py`, mob com Faction
"vida_selvagem" (tier neutro) → `_visible_enemies_sorted` inclui.
SPACE — `tests/test_client_ui.py`, alvo já selecionado e vivo, NÃO o
mais próximo → `_space_engage` mantém; sem alvo selecionado → cai pro
mais próximo (regressão). Efeito colateral achado só rodando a suíte
completa: `test_reengajar_com_espaco_reativa_chase_suppressed_false`
(teste pré-existente) assumia a semântica ANTIGA de SPACE (sempre
re-seleciona) só pra testar `chase_suppressed` — atualizado pra
refletir a semântica nova (mantém o alvo já selecionado) sem perder a
cobertura do que realmente testava. Revert-to-confirm nos 3 fixes —
todos falham exatamente como relatado sem a correção, confirmam
depois. Suíte completa (`pytest tests/ -q`, `py -3.10`) — 1290 passed,
zero regressão.

**Pendente**: usuário confirmar em jogo — guerreiro sobe pro level 2
na BG e Interceptar aparece certinho (Punho no Queixo não aparece);
TAB cicla por mob de jungle; SPACE ataca o alvo já selecionado mesmo
não sendo o mais próximo, em qualquer parte do jogo.

### Correção — item 3 (SPACE) tinha corrigido a função ERRADA (25/08/2026)

Usuário testou em jogo e confirmou: item 1 (Punho no Queixo) resolvido,
mas SPACE continuava trocando de alvo depois de selecionar com TAB.

**Causa raiz do "não resolveu"**: `ui/systems.py::PlayerInputSystem.
_space_engage` (a função corrigida acima) **não é a usada no jogo
online** — é caminho offline/legado. A função REAL, que `game.py`
despacha de verdade no `K_SPACE`, é `client/save_sync_handlers.py::
_space_engage_online` — MESMO bug (sempre recalculava "mob remoto
hostil mais próximo" do zero, ignorando `CombatState.target_entity_id`
por completo), implementação separada, nunca peguei ela porque só
busquei `K_SPACE` numa checagem superficial antes — não achei os OUTROS
2 arquivos que também batem em `K_SPACE` na primeira olhada.

**Achado extra, não corrigido ainda (flag pro usuário, não é decisão
minha)**: `PlayerInputSystem` (dona da função offline) ESTÁ registrada
e RODANDO de verdade no jogo online (`game.py:839`, parte da lista de
`self.systems`) — ou seja, as DUAS funções processam o MESMO evento
K_SPACE, de forma independente, no mesmo frame. Corrigir só uma (como
aconteceu aqui) deixa o bug vivo, porque a outra ainda roda e pode
"ganhar" dependendo da ordem. Como as duas foram corrigidas com a
MESMA lógica agora, o resultado final é o mesmo mesmo com as duas
rodando — mas isso é sintoma de uma violação do ponto único de verdade
do projeto (2 implementações fazendo a mesma coisa) que vale a pena
limpar depois (decidir qual é a "real" e remover/desativar a outra) —
não fiz essa limpeza agora porque é uma decisão de arquitetura, não
parte do bug relatado.

**Fix**: MESMA lógica aplicada em `_space_engage_online` — checa
`CombatState.target_entity_id` primeiro via `utils.is_target_alive()`
antes de buscar "mob hostil mais próximo".

**Testes**: `tests/test_client_ui.py` — 2 novos
(`test_space_engage_online_mantem_alvo_ja_selecionado_mesmo_nao_sendo_o_mais_proximo`/
`test_space_engage_online_sem_alvo_selecionado_pega_o_mais_proximo`),
fixture mistura `SaveSyncHandlers`+`RemoteEntityHandlers` (`_meta_from_
local`). Revert-to-confirm: reproduz o sintoma exato relatado
("assert 2 == 1" — trocou pro mais próximo). Suíte completa
(`pytest tests/ -q`, `py -3.10`) — 1292 passed, zero regressão.

**Pendente**: usuário confirmar em jogo de novo — SPACE ataca o alvo
já selecionado desta vez (função certa, corrigida e testada).

### Correção 2 — SPACE ainda não alcançava neutro + limpeza do código paralelo (26/08/2026)

Usuário confirmou em jogo: manter-alvo-selecionado funcionou, mas
reportou 2 coisas na mesma mensagem — (a) SPACE ainda não selecionava
alvo "neutro" (jungle) na busca de fallback; (b) instrução PADRÃO daqui
pra frente: "sempre que acharmos [código paralelo de offline] temos
que limpar" — não é aprovação pontual, é regra permanente.

**Causa (a)**: o fix de "manter alvo selecionado" (Correção 1, acima)
só tocou a lógica de PRIORIZAR `target_entity_id` — não tocou o filtro
de facção da busca de fallback, que continuava `is_hostile` (só tier
"hostil"). Igual ao bug do TAB (item 2 do plano original), só que numa
função diferente que eu não tinha revisado com a mesma lente.

**Fix (a)**: `client/save_sync_handlers.py::_space_engage_online` —
trocado `is_hostile` por `can_engage` na busca de fallback (mesmo
critério do TAB: hostil OU neutro, só bloqueia "amigavel"). Teste novo
`test_space_engage_online_sem_alvo_alcanca_mob_neutro`. Revert-to-
confirm: com `is_hostile` de volta, reproduz exatamente "assert -1 ==
1" (mob neutro nunca é alcançado).

**Fix (b) — limpeza do código paralelo achado na Correção 1**:
`ui/systems.py::PlayerInputSystem.update()` tinha um bloco (linhas
774-778, já removido) que escutava `KEYDOWN K_SPACE` e chamava
`self._space_engage(...)` — a MESMA tecla que `game.py:1729-1730` já
despacha direto pra `_space_engage_online()`. As duas rodavam todo
frame que ESPAÇO era apertado. Perguntei ao usuário o escopo da
limpeza (3 opções: só remover a chamada / remover a chamada + apagar o
método inteiro / só documentar) — escolhido "só remover a chamada"
(método `_space_engage` continua no arquivo, só não roda mais online;
os 4 testes que chamam ele direto em `test_client_ui.py` continuam
válidos como teste da lógica isolada).

**Achado durante a limpeza, não previsto no plano original**: o bloco
removido tinha um gate `if can_act:` antes de chamar `_space_engage`
— ou seja, ESPAÇO ficava bloqueado sob CC (stun/sleep/fear/polymorph)
só por causa dessa checagem, que `_space_engage_online` NUNCA teve e
`game.py` também não tem em nenhum lugar antes de despachar `K_SPACE`.
Remover o bloco duplicado sem repor o gate teria introduzido uma
regressão real: SPACE destravado sob CC no jogo online. Corrigido
adicionando `is_action_locked(world, player_entity)` (mesmo choke-
point usado pelo gate de skills/auto-attack no servidor — `engine/
utils.py`) no topo de `_space_engage_online`. Não pedi confirmação de
novo pro usuário pra essa parte específica — é o mesmo ponto único de
verdade já documentado no projeto, aplicado corretamente, não uma
decisão de arquitetura nova.

**Teste**: `test_space_engage_online_bloqueado_por_stun` — player com
`StatusEffects.effects["stun"]`, SPACE não seleciona nem confirma
nenhum alvo. Revert-to-confirm: com o gate comentado (`if False and
...`), reproduz exatamente a regressão (`assert 2 == -1`, alvo
selecionado mesmo sob stun).

**Suíte completa** (`pytest tests/ -q`, `py -3.10`): 1293 passed
(1 falha isolada e não-relacionada, `test_perf_push_pop_grava_amostra_
sob_caminho_completo` — timing flake da mesma família já catalogada
nesta sessão, confirmada passando sozinha e no arquivo inteiro).

**Lição pra próxima vez que acharmos código paralelo offline/online**:
não basta "escolher qual é a real e apagar a outra" — a versão que vai
ficar pode ter um gate/checagem que a outra tinha e ela não, então
correto é DIFF as duas implementações antes de remover qualquer coisa
que ainda roda de verdade.

## 22. Bots de BG: guerreiro para longe demais, mago sem dano/"sem cooldown" (26/08/2026)

Usuário relatou 3 comportamentos estranhos nos bots: guerreiro para 1
tile longe demais do alvo e nunca acerta skill melee; mago lança Bola
de Fogo sem parar mas nunca causa dano; parece "sem cooldown" entre os
lançamentos. Pediu pesquisa de bot de outros MOBAs antes de desenhar a
correção (regra do projeto). Planejado via EnterPlanMode + 3
AskUserQuestion (direção geral, kiting sim/não, valor exato do alcance
melee) — todas as 3 recomendadas aceitas.

**Causa raiz 1 — 1 raio de engajamento genérico pra 3 classes**.
`BOT_ENGAGE_RANGE_TILES = 2` (`server/bot_ai_system.py`) decidia
"parei de andar, hora de brigar" pra TODAS as classes igual. Mas
melee de verdade (`_resolve_target(...,1)`/`_resolve_auto_attack`,
`attack_range=1 if not is_ranged`) exige Chebyshev ≤1 — a 2 tiles TODA
tentativa falhava "Fora de alcance", guerreiro ficava parado pra
sempre. Arqueiro (arco real equipado) e mago (`bola_de_fogo.cast_range
=6`) paravam bem mais perto do que precisavam, se expondo à toa.
Pesquisa externa (Game AI Pro 3, "Paragon Bots: A Bag of Tricks",
Epic Games) confirma: bot de MOBA usa o alcance EFETIVO de cada herói,
nunca 1 raio genérico compartilhado entre papéis.

**Causa raiz 2 — dano de skill de projétil nunca aplicado pro bot**.
`bola_de_fogo`/`flecha_reiterada`/`picada_escorpiao`/`tiro_repulsivo`
(`_PROJECTILE_SPELLS`, `server/spell_completion_processor.py`) só
aplicam dano quando o CLIENTE manda `PROJECTILE_HIT_CS` confirmando o
impacto visual (`_apply_spell_on_projectile_hit`). Bot não tem
cliente — nunca confirma — a entrada em `_spells_in_flight_queue`
expirava sozinha 3s depois sem efeito. Confirmado por simulação real
headless (sem GUI) ANTES do fix: 7 casts completos de Bola de Fogo em
20s, HP do alvo não mudou 1 ponto. "Sem cooldown" é sintoma disto, não
bug à parte — `bola_de_fogo.cooldown=0.0` é intencional (cast_time=
1.5s é o throttle real), só parecia quebrado porque nada acertava.

**Fix 1**: `BotAISystem._effective_engage_range(world_server, eid)`
novo — arqueiro com arco de verdade usa o alcance do arco (`Combat
ProcessorMixin._ranged_weapon_range`, extraído de `_resolve_auto_
attack` pra ponto único, reusado pelos dois); caster com skill
ofensiva usa o `cast_range` dela (sem hardcode de "bola_de_fogo",
lê a 1ª skill ofensiva do hotbar); fallback 1 tile (melee puro).
`BOT_ENGAGE_RANGE_TILES` (fallback) mudou de 2 pra 1.

**Fix 2**: `SpellCompletionMixin._schedule_bot_projectile_autohit`/
`_tick_bot_projectile_autohits` novos — quando uma entrada de
`_PROJECTILE_SPELLS` entra em voo E o caster tem `BotControlled`,
agenda uma auto-confirmação (tempo de voo = distância/velocidade,
valores espelhados de `ui/spell_system.py::PlayerProjectile.speed` —
bola_de_fogo=300, flecha_reiterada/picada_escorpiao=700, tiro_
repulsivo=800 px/s) que chama a MESMA `_apply_spell_on_projectile_hit`
que uma confirmação de cliente real usaria — zero duplicação de dano,
zero efeito em players reais (só dispara com `BotControlled`
presente). Multi-hit (Flecha Reiterada) reagenda automaticamente
enquanto a entrada continuar em `_spells_in_flight_queue`.

**Decisão explícita, não implementada agora**: kiting (mago/arqueiro
recuar quando inimigo melee fica adjacente) — usuário optou por manter
"v1 nunca recua" (decisão anterior já registrada) e só corrigir a
distância de parada.

**Testes** (`tests/test_bg_bot.py`): `test_guerreiro_a_2_tiles_nao_
engaja_ainda`, `test_mago_engaja_a_6_tiles_sem_precisar_chegar_mais_
perto` (raio por classe); `test_bola_de_fogo_bot_causa_dano_real_de_
ponta_a_ponta` (fluxo completo via `run_ticks`, reproduz o cenário
exato relatado), `test_schedule_bot_projectile_autohit_generico_por_
spell_id` (mecanismo isolado com `flecha_reiterada`, prova que não é
hardcoded pra Bola de Fogo). Revert-to-confirm nos 2 fixes — cada um
reproduziu o sintoma exato relatado antes do fix (guerreiro parado
sem tentar skill a 2 tiles; HP do alvo intacto após 20s de cast
completo). Suíte completa (`pytest tests/ -q`, `py -3.10`): 1298
passed, zero regressão.

**Pendente**: validação em jogo (comportamento de bot só se confirma
de verdade jogando uma partida de BG).

## 23. Crash ao treinar skill — `TrainerSystem` sem `_preferred_slot_for_skill` (26/08/2026)

Usuário reportou crash real jogando (fora da BG, testando com o
personagem Xadread): treinar uma skill no NPC treinador derrubava o
jogo com `AttributeError: 'TrainerSystem' object has no attribute
'_preferred_slot_for_skill'`.

**Causa raiz**: `_preferred_slot_for_skill`/`_skill_slot_prefs_to_dict`
nasceram como MÉTODOS de `GameEngine` no §73 (mais cedo nesta mesma
sessão). `client/network_handlers.py::NetworkHandlers` é um MIXIN
misturado direto em `GameEngine` — `self._preferred_slot_for_skill`
funcionava ali porque `self` já É um `GameEngine` em tempo de execução.
`ui/trainer_system.py::TrainerSystem`, porém, é um objeto SEPARADO
(`game.py:457`, `self._trainer_system = TrainerSystem(...)`, sem
nenhuma referência de volta pro engine) — `self._do_learn`'s `self`
nunca foi um `GameEngine`, então o método nunca existiu ali. Testes não
pegaram porque as fixtures leves (`_SkillPlacementFixture` etc.)
monkeypatchavam o método DIRETO na classe fixture
(`Fixture._preferred_slot_for_skill = GameEngine._preferred_slot_for_skill`),
mascarando a falta da ligação real — nenhum teste instanciava o
`TrainerSystem` de verdade.

Perguntei ao usuário como consertar a ligação (2 opções: extrair pra
`client/skill_slot_prefs.py`, ou dar ao `TrainerSystem` uma referência
de volta pro `GameEngine`) — usuário rejeitou as duas e pediu
investigação melhor: "Essa função não deveria estar dentro da pasta
'ui'? Já não existe uma função onde isso deveria estar?". Investigando
de novo, achei uma saída melhor: a função não precisa de NENHUMA
referência externa — o nome do personagem que ela lê
(`GameEngine._logged_char_name`) já existe no ECS, em
`CharacterStats.name` (mesma fonte, confirmado lendo o código de
login: `char_stat.name = char.get("name")` e `self._logged_char_name =
char.get("name", "")` vêm do MESMO payload). Como `TrainerSystem` já
tem `self.world`/`self.player_entity` (igual QUALQUER system), ela lê
o nome sozinha. Confirmei também que `client/` já importa de `ui/` em
19+ arquivos, mas `ui/` NUNCA importa de `client/` (grep confirmou
zero ocorrências) — `client/skill_slot_prefs.py` teria sido a fronteira
ERRADA (`ui/trainer_system.py` nunca poderia importar de lá sem
inaugurar uma dependência nova nesse sentido).

**Fix**: nova `ui/skill_slot_prefs.py` — `preferred_slot_for_skill(world,
player_entity, ps, skill_id)`/`skill_slot_prefs_to_dict(ps, existing)`,
funções puras (lêem `CharacterStats.name` do ECS, não um atributo de
instância). `game.py`, `client/network_handlers.py` (2 call sites) e
`ui/trainer_system.py::_do_learn` (o crash de verdade) passam a
importar e chamar a MESMA função — nenhum duplicado. Métodos antigos
removidos de `GameEngine`.

**Teste novo, o que realmente faltava**:
`test_trainer_system_real_aprende_skill_sem_crash`
(`tests/test_hotbar_bg_leak.py`) — instancia o `TrainerSystem` de
VERDADE (não uma fixture leve) e chama `_do_learn` de ponta a ponta.
Revert-to-confirm: com a chamada revertida pra `self._preferred_slot_
for_skill(...)`, reproduz o `AttributeError` EXATO relatado pelo
usuário. Fixtures antigas (`_SkillPlacementFixture`, `_LootResultFixture`,
`FakeClient`) tiveram os monkeypatches/stubs removidos — não precisam
mais deles, a função agora é importada, não um método `self.`. Suíte
completa (`pytest tests/ -q`, `py -3.10`): 1299 passed, zero regressão
(1 falha isolada de um teste de perf-log com conteúdo de arquivo
corrompido/null bytes — race de I/O sem relação, confirmada não-
reproduzível rodando a suíte de novo logo em seguida).

**Lição**: fixture leve que monkeypatcha um método `self.` direto numa
classe fake mascara justamente o tipo de bug que aconteceu aqui — a
ligação real entre dois objetos de produção nunca foi exercitada. Pelo
menos 1 teste por mecanismo compartilhado deveria instanciar o objeto
de PRODUÇÃO real (aqui, `TrainerSystem`), não só uma fixture equivalente.

## 24. Zumbi de fila BG + botão nativo de maximizar sobrescreve "Janela" (26/08/2026)

Duas investigações separadas na mesma conversa, nenhuma relacionada ao
trabalho de bot desta sessão — achadas porque o usuário reportou os
sintomas em jogo real.

### a) `_tick_minion_waves` rodando pra sempre contra instância já descarregada

Usuário reportou: deixou o servidor ligado (nenhum player desde
14:03), às 17:09:45 os 3 bots da BG despawnaram, e a partir de
17:10:17 o log passou a spammar `[MinionWave] rota falhou: ... sem
caminho` a cada ~45s, PRA SEMPRE (até reiniciar o processo).

**Causa raiz**: `WorldServer._unload_instance()` (chamada por `server/
bg_queue_processor.py::request_bg_leave` quando o último ocupante sai —
`if not match["team_a"] and not match["team_b"]`) libera `_map_bundles`/
`_pvp_zones_by_map`/`_bush_zones_by_map` mas ESQUECIA de tirar
`_minion_lanes[instance_key]` — `_tick_minion_waves` continuava
achando a lane e tentando calcular rota (`_compute_lane_route_tail`)
num mapa cujo bundle/pathfinding já não existe mais, pra sempre.
Mesmo caminho compartilhado por BG, Arena (`match_processor.py`) e
debug battleground (`debug_battleground.py`) — os 3 chamam
`_unload_instance`.

**Fix**: 1 linha em `_unload_instance` —
`self._minion_lanes.pop(instance_key, None)`. Não precisa limpar
`_minion_wave_timers` manualmente: `_tick_minion_waves` já se
autolimpa (`del self._minion_wave_timers[key]`) assim que a lane some
de `_minion_lanes`, no próximo tick — reusar o mecanismo existente em
vez de duplicar.

**Teste**: `tests/test_bg_queue.py::TestBgQueueLeave::
test_ultimo_a_sair_para_o_minion_wave_da_instancia` — monta partida
2v2 de verdade, abre o portão (ativa as lanes de verdade), todo mundo
sai, confirma que `_minion_lanes`/`_minion_wave_timers` da instância
somem. Revert-to-confirm: sem a linha, reproduz `AssertionError`
exatamente com o `instance_key` ainda presente. Suíte completa: 1300
passed, zero regressão.

### b) Botão nativo de maximizar do Windows sobrescrevia "Janela" sem o usuário mexer em menu nenhum

Usuário relatou: às vezes o jogo abre em modo quase-tela-cheia mesmo
com "Janela" marcada no menu Configurações — só resolve indo no menu
e trocando de modo manualmente.

**Causa raiz**: a janela do jogo é `RESIZABLE` (botões nativos do
Windows visíveis — minimizar/maximizar/fechar). `game.py`'s loop de
eventos (dentro de `run()`) escutava `pygame.WINDOWMAXIMIZED`/
`WINDOWRESTORED` — QUALQUER ação nativa do Windows que maximiza a
janela (clicar o botão quadrado, duplo-clique na barra de título,
arrastar a janela pro topo da tela = Aero Snap) disparava esse evento,
e o handler (`Fase G`, 23/07/2026) **gravava `self._window_mode_pref =
"maximized"` e chamava `_save_config()` na hora** — sobrescrevendo
silenciosamente qualquer escolha feita no menu, sem o jogador ter
aberto Configurações. Como o handler deliberadamente NUNCA chama
`set_mode()` (evita o segfault documentado do §34.48), nada muda
visualmente na hora — o jogador não percebe que acabou de sobrescrever
a própria preferência. `"maximized"` é tratado como valor LEGADO em
TODO outro lugar do código — migra pra `"windowed_fullsize"` (quase
tela cheia) no boot seguinte. Cadeia completa: escolhe "Janela" →
maximiza sem querer (gesto trivial numa janela RESIZABLE) → grava
"maximized" → próximo boot lê "maximized" → migra pra
"windowed_fullsize" → abre quase em tela cheia do nada.

**Fix**: removidos os 2 handlers (`WINDOWMAXIMIZED`/`WINDOWRESTORED`)
inteiros — o botão nativo de maximizar continua funcionando (o próprio
Windows já cuida disso), só PARA de persistir isso como escolha de
modo. Só o menu Configurações > Janela decide o que fica salvo — os 2
tipos de evento saíram também da allowlist de `pygame.event.
set_allowed()` (não sobra ninguém escutando). Sem teste automatizado —
bug é de loop de evento/janela nativa do SO, fora do que a suíte cobre
hoje; verificação é manual (maximizar de propósito, reiniciar,
confirmar que continua em "Janela").

**Pendente**: usuário confirmar em jogo — deixar "Janela" marcada,
maximizar a janela pelo botão nativo (ou Aero Snap), reiniciar o jogo,
confirmar que continua abrindo em "Janela" (não em quase-tela-cheia).

## 25. Catálogo de tiles: 1 cor por arquivo inteiro (grama/terra/pedra indistinguíveis no minimapa) (26/08/2026)

Usuário relatou (jogando, não em código): `engine/tileset.py` cataloga
tiles/objetos a partir de PNGs, mas cada arquivo tem só 1 cor RGB —
quando um PNG mistura tipos diferentes (ex: grama e terra na mesma
imagem), ficam indistinguíveis no minimapa. Pediu explicitamente pra
mapear a arquitetura e pesquisar antes de mexer em qualquer coisa —
planejado via EnterPlanMode.

**Achado 1 — colisão já tinha o padrão que faltava pra cor.**
`collision_overrides: {(col,row): valor}` (modo grade) e campo por tile
(modo catálogo) já existiam — só faltava o equivalente pra `color`.

**Achado 2 (mais sério, descoberto DURANTE a implementação, não
previsto no plano original) — o pipeline do minimapa descartava a
informação necessária ANTES de qualquer cor chegar lá:**
- **Terreno de sheet** (`SHEET_FAMILIES`, ex: "TX Tileset Terrain"):
  `engine/map_loader.py::_parse_terrain_cell` reduz toda célula de
  sheet pro CHAR DE TERRENO SUBJACENTE ("G") já na hora de ler o CSV —
  o ID específico do sprite de sheet ("ter_2_3") só sobrevive numa
  estrutura PARALELA (`terrain_visual`), que `MapOverlay.load_map()`
  nunca recebia como parâmetro. Ou seja: não importa qual cor eu
  cadastrasse por célula em `SHEET_FAMILIES`, o minimapa NUNCA
  chegaria a ver essa informação — só via "G" (grama) pra tudo.
- **Objetos de sheet** (`OBJECT_SHEET_FAMILIES`, ex: "TX Village Props
  with Shadow"): `game.py::_merge_display_matrix` truncava qualquer ID
  multi-char pro 1º caractere (`"vp_001"` → `"v"`) antes do lookup de
  cor — funcionava por acidente pras famílias antigas de letra única
  (t/b/g/s/y/l/o, que têm uma entrada canônica de 1 letra só pra isso),
  mas QUALQUER objeto de sheet mais novo truncava pra uma letra que não
  bate com NENHUM `TileType` cadastrado — caía no floor genérico, sem
  cor nenhuma, silenciosamente, desde que essas famílias foram
  adicionadas.
- Confirmado com o usuário via AskUserQuestion: corrigir os 2 problemas
  juntos nesta mesma tarefa (mecanismo + pipeline), não só o mecanismo.

**Fix — mecanismo de cor** (`engine/tileset.py`):
- Modo catálogo (`OBJECT_SHEET_FAMILIES`): novo formato `"groups":
  {nome: {prefix, label, color, underlying_terrain, tiles: [...]}}` —
  aditivo, retrocompatível com `"tiles"` plano existente. Só 2 níveis
  (arquivo→grupo, sem override por tile dentro do grupo — decisão do
  usuário: se 1 tile for muito diferente, vira outro grupo). Corpo de
  processamento por-tile extraído pra `_register_catalog_tiles()`,
  compartilhado entre os 2 modos (evita duplicar `_resolve_collision`/
  elevation/etc. em 2 lugares).
- Modo grade (`SHEET_FAMILIES` e variante grade de
  `OBJECT_SHEET_FAMILIES`): `color_overrides: {(col,row): (r,g,b)}` —
  mirror exato de `collision_overrides`, cai no `color` da família fora
  do override.

**Fix — pipeline do minimapa**:
- `game.py::_merge_display_matrix` — parou de truncar (usa o token
  completo), ganhou parâmetro `terrain_visual` (prioridade por célula:
  objeto > visual de sheet de terreno > char de terreno puro), passou a
  devolver `list[list[str]]` em vez de `list[str]` joinado (só assim dá
  pra guardar token multi-char por célula). 3 call sites atualizados
  (`game.py` × 2 diretos + 1 via `Tilemap.terrain_visual`, que já
  existia como campo do componente).
- `ui/map_overlay.py::load_map()` — 2ª passada: pra cada objeto,
  `get_collision_offsets(tile)` (já existia, mesma função usada pra
  colisão de verdade) pinta a cor no footprint INTEIRO, não só na
  célula-âncora — pedido do usuário: "deveria considerar o tamanho da
  colisão, que também é configurado no mesmo arquivo".

**Prova de conceito**: `SHEET_FAMILIES["TX Tileset Terrain"]` migrada
pra `color_overrides`, cores por FAIXA DE LINHA determinadas por
inspeção visual do PNG (grade 14×16, 32px/tile): linhas 0-3 grama,
4-11 pedra, 12-15 terra. Divisão por faixa (não célula a célula) —
algumas células de transição na borda ficam com a cor "errada" por
aproximação (aceito conscientemente, registrado no comentário do
código — célula a célula é trabalho de conteúdo maior, fora do escopo
combinado).

**Testes** (`tests/test_tileset_color.py`, novo arquivo — nenhum teste
cobria `engine/tileset.py`, `_merge_display_matrix` ou `MapOverlay`
antes): grupos com cores diferentes não vazam entre si;
`color_overrides` aplica por célula e cai no default fora dele;
`_merge_display_matrix` não trunca mais + prioriza objeto sobre visual
de terreno; `load_map()` pinta footprint inteiro (não só âncora).
Revert-to-confirm nos 4 pontos — cada um reproduziu o sintoma exato
(inclusive o `'v' != 'vp_001'` da truncagem). Suíte completa
(`pytest tests/ -q`, `py -3.10`): 1306 passed, zero regressão.

**Pendente**: usuário abrir o mapa (tecla M) e o minimapa ao vivo,
comparar visualmente a região de "TX Tileset Terrain" antes/depois.

### Correção — usuário pediu 2º exemplo real, em modo CATÁLOGO desta vez (26/08/2026)

Usuário perguntou por que a prova de conceito usou `color_overrides`
(modo grade) em vez de `"groups"` (o formato que ele tinha desenhado
originalmente). Resposta: `"groups"` é pro modo CATÁLOGO (tiles com
nome/id próprio, como os de `OBJECT_SHEET_FAMILIES`) — "TX Tileset
Terrain" é modo GRADE (células só têm posição, auto-descoberta pelo
tamanho do PNG, sem lista manual de tiles), então não tem "id" pra
agrupar por nome; `color_overrides` (por col,row) é o mecanismo certo
pra esse modo, mirror do `collision_overrides` que já existia. Usuário
concordou e pediu um 2º exemplo real, desta vez em modo catálogo:
**"TX Village Plant"**.

Esse arquivo já misturava 8 categorias bem diferentes visualmente
(árvore verde/outono, arbusto, trigo dourado, repolho, abóbora,
cenoura, grama fina, flor) — os comentários no código JÁ dividiam as
205 entradas em seções (`#TREES`, `#BUSHES`, `#WHEAT`...), só que
apenas como comentário, nunca como estrutura de dado — todas com a
MESMA cor `(96, 99, 21)`. Migrado pra `"groups"`: 8 grupos
(arvores/arbustos/trigo/repolho/abobora/cenoura/grama_fina/flores),
cores por inspeção visual do PNG (1024×1024).

**Cuidado crítico na migração**: `prefix` teve que continuar `"pl"` em
TODO grupo (nunca virar um prefixo diferente por grupo, tipo
"pltree"/"plbush") — `maps/map_1_objects.csv` e `maps/
moba_battleground_objects.csv` já referenciam ids desse arquivo
("pl_t1", "pl_b3" etc.); um prefixo diferente por grupo teria quebrado
essas referências silenciosamente (o id novo simplesmente não existiria
em `OBJECT_MAPPING`, o objeto sumiria do jogo sem erro nenhum).
Verificado programaticamente: extraí todo id "pl_*" usado nos 2 CSVs
reais e confirmei que TODOS continuam resolvendo em `OBJECT_MAPPING`
depois da migração — zero perdido.

**Teste** (`tests/test_tileset_color.py::TestTxVillagePlantGroupsMigration`):
ids pré-existentes continuam resolvendo, grupos diferentes têm cores
diferentes, dados de colisão/visão (`collision_rect`/`vision_rect`/
`vision_height`) preservados intactos na migração — só a cor mudou.
Suíte completa (`pytest tests/ -q`, `py -3.10`): 1309 passed, zero
regressão.

### Correção 2 — crash real no editor de mapa (F10 > aba Objetos) por causa do formato "groups" novo (26/08/2026)

Usuário testando o jogo (não em código): abrir o editor (F10) e clicar
na aba "Objetos" derrubava com `KeyError: 'prefix'` em `ui/god_mode.py::
_render_content`. Causa: esse painel lê `OBJECT_SHEET_FAMILIES` DIRETO
pra desenhar a paleta de tiles (`fam["prefix"]`/`fam["color"]`) — um
consumidor que eu não tinha mapeado na investigação original (só tinha
checado `engine/tileset.py` e testado o `discover_object_sheet_tiles()`
em si). "TX Village Plant" (migrada pra `"groups"` na correção acima)
não tem mais `prefix`/`color` no nível da família — só dentro de cada
grupo — então a 1ª vez que esse family apareceu na lista, o painel
quebrou.

**Fix**: `_render_content` agora monta uma lista de "entradas
renderizáveis" ANTES de desenhar — família sem `"groups"` vira 1
entrada (comportamento de sempre); família COM `"groups"` vira 1
entrada POR GRUPO (label/color do próprio grupo). Cuidado extra: o
filtro de tiles de cada entrada não pode mais ser só "prefixo" —
grupos da MESMA família (e até famílias DIFERENTES, como "TX Plant" e
"TX Village Plant", que por coincidência compartilham o prefixo "pl")
têm o mesmo prefixo; filtrar por prefixo pegaria tile de grupo/arquivo
errado. Corrigido calculando o SET exato de ids de cada grupo (`{prefix
+ "_" + id_suffix for id_suffix, ... in group["tiles"]}`) em vez de
`tid.startswith(prefix + "_")`.

**Lição**: antes de mudar o FORMATO de uma estrutura de dado
compartilhada (`OBJECT_SHEET_FAMILIES`), mapear TODO consumidor que lê
essa estrutura direto, não só o módulo "dono" dela
(`engine/tileset.py`) — `ui/god_mode.py` é um consumidor de UI/editor
que só é exercitado ao vivo (F10 > aba Objetos), nunca pela suíte de
testes automatizada até agora, então não haveria como pegar esse
crash sem o usuário testar em jogo de verdade.

**Teste novo** (`tests/test_tileset_color.py::
TestGodModeObjectPanelGroups`): instancia o `GodModeEditor` de VERDADE
(não uma fixture leve), força a aba "Objetos" e chama `_render_content`
— mesmo caminho exato que crashou em jogo. Revert-to-confirm: reproduz
o `KeyError: 'prefix'` idêntico ao relatado. Suíte completa (`pytest
tests/ -q`, `py -3.10`): 1310 passed, zero regressão.

## 26. Árvore usava a copa inteira (sprite completo) pra bloquear visão — corrigido pra usar só a base (27/08/2026)

Usuário pediu: árvore está considerando o sprite inteiro pra "esconder"
o que está atrás (bloqueio de LOS, `vision_rect`); o correto é usar só
a base — mesma regra da bush, só que pra árvore isso significa usar o
MESMO retângulo do `collision_rect` (bush não tem colisão nenhuma,
então usa o sentinel `"base_row"`; árvore já tem um `collision_rect`
de verdade representando o tronco — reusar ele é a versão certa da
mesma ideia).

**Contexto**: `vision_rect="full"` pras árvores (`OBJECT_SHEET_FAMILIES
["TX Village Plant"]["groups"]["arvores"]`, t1-t15) foi decisão
deliberada de 13/08/2026 (§49) — "copa de árvore fica na metade de
CIMA do sprite, 'base' apagaria o bloqueio de propósito". O usuário
agora decidiu que quer o oposto: só o tronco (pegada de colisão) deve
bloquear visão, não a copa inteira — decisão de conteúdo/comportamento
do próprio usuário, não conflito de arquitetura pra resolver sozinho.

**Fix**: as 15 entradas do grupo `arvores` tiveram o campo `vision_rect`
trocado de `"full"` pro MESMO valor (x,y,w,h) já usado em
`collision_rect` na mesma linha — sem mecanismo novo, `get_vision_offsets`/
`_rect_to_tile_offsets` (já compartilhado com `get_collision_offsets`)
processa normal. Rocha grande (`pr_bigrock`, família diferente,
prefixo `pr`) e bush continuam como estavam — pedido era só sobre
árvore.

**Testes** (`tests/test_vision_blocking.py`, já existia da feature
original de 13/08 — atualizado, não criado do zero):
`TestTreeTrunkVsCanopyVision` renomeada pra `TestTreeVisionMatchesCollision`
— os 2 testes que validavam a copa bloqueando SEM ser sólida foram
substituídos por um que confirma `vision_rect == collision_rect` e que
os offsets de visão/colisão são EXATAMENTE iguais (mais 1 teste extra
pra `t14`, árvore grande com colisão deslocada da âncora, confirmando
que a regra vale pra ela também). `test_arvore_e_pedra_grande_continuam_full`
renomeado e ajustado (árvore não é mais "full", pedra continua). Novo
`TestFovBehindTreeMatchesTrunkOnly` — ponta a ponta via
`engine.fov.compute_fov`: tile que só ficava escondido pela copa
antiga (fora do tronco) agora está visível; tile atrás do tronco de
verdade continua escondido. Revert-to-confirm (voltar `"full"` só na
entrada `t1`) reproduziu os 3 sintomas exatos (vision_rect errado,
offsets diferentes de colisão, tile da copa continuando escondido)
antes de restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`).

**Pendente**: usuário confirmar em jogo que a névoa/visão ao redor de
árvores agora só é bloqueada perto do tronco, não mais pela copa
inteira.

## 27. Dano de DoT de habilidade de mob escalava linear demais (urso level 24 batendo 96/tick) (28/08/2026)

Usuário reportou: urso level 24 causando 96 de dano por tick de
sangramento (Laceração) — "isso é muito". Pediu explicação breve
primeiro, depois pediu correção.

**Causa**: `_ability_dmg = defn.magnitude * ecs.base_attack_power`
(2 pontos em `engine/world_systems.py`) — `base_attack_power` do mob JÁ
escala LINEAR com level (`entity_factory.py`: `base_attack_power =
attack_power_base * level`, mob cadastrado). Multiplicar de novo por
`magnitude` (multiplicador fixo por habilidade, ex: 2.0 pra Laceração)
compunha 2 crescimentos lineares — urso (attack_power base 2) em level
24: `2 * 24 = 48` de AP, `2.0 * 48 = 96`/tick.

**Fix**: novo `damage_calculator.mob_dot_tick_damage(magnitude,
base_attack_power, level)` — divide por `√level`, cancelando a parte
linear que já veio de `base_attack_power` (sobra crescimento em raiz,
mais leve em levels altos; idêntico ao valor de hoje em level 1,
`√1=1`). Pedido explícito do usuário: número-alvo aproximado (não
exato) — nível 5→~10, 10→~15, 20→~20, 30→~25, 50→~40, formato
desacelerando; fórmula escolhida bate perto disso sem precisar de
tabela/constante nova pra ajustar (nível 24: 96→20; nível 50: 200→28).
Os 2 pontos de `world_systems.py` que aplicam efeito de habilidade de
mob (corpo-a-corpo e projétil) passam a chamar essa função — nenhum
outro lugar do código fazia essa conta (conferido antes de fechar,
`grep` não achou 3º ponto/código paralelo).

**Testes** (`tests/test_mob_dot_scaling.py`, novo arquivo — função pura
sem World, testável isolada por design de `damage_calculator.py`):
level 1 idêntico à fórmula antiga; urso level 24 não bate mais 96
(bate 20); cresce beeem mais devagar que o crescimento linear antigo
(razão 40:10 bem menor que 4x); nunca decresce com o level; mínimo 1
garantido; level 0/negativo não quebra (defensivo). Revert-to-confirm
(fórmula antiga sem `/√level` de volta) reproduziu os 2 sintomas
exatos (96 de novo, crescimento linear) antes de restaurar. Suíte
completa (`pytest tests/ -q`, `py -3.10`), incluindo
`tests/test_server.py` (mob ranged ability, caminho de projétil, não
afetado).

**Pendente**: usuário confirmar em jogo que o sangramento do urso (e
outros DoTs de mob) ficou num número que parece certo em levels
variados.

## 28. Playtest BG: skill "Alvo inválido"/"Fora de alcance" depois de Interceptar OU seleção normal de alvo (RESOLVIDO 30/08/2026) + bot com HUD de morto às vezes (debug adicionado) (28/08/2026)

Usuário testou a BG e relatou 2 problemas: (1) print mostrando um bot
sem barra de HP e com aparência de "sem possibilidade de atacar" perto
do player; (2) skill dando "sem alvo/alvo inválido" logo depois de usar
Interceptar contra um bot, suspeita de dessincronia cliente/servidor.

**Bug 1 (Interceptar) — causa raiz achada por leitura de código, sem
precisar de debug ao vivo.** `_resolve_target()` (`engine/
skill_handlers.py`, compartilhado cliente+servidor) tinha 3 buscas de
fallback quando o alvo selecionado sai de `_max_range`: 2 pra mob
(`Enemy`+`AIControlled`) e 1 pra "outro player" via componente
`RemoteControlled`. `RemoteControlled` só existe do lado CLIENTE
(representa "outro player que eu não controlo") — no servidor cada
player, INCLUSIVE bot, é uma entidade real com `CombatStats`/
`CharacterStats` próprios, nunca um proxy. Resultado: esse 3º fallback
era código MORTO no servidor (a query nunca achava nada lá). Se o alvo
já selecionado saísse de `_max_range` por 1 tick só (plausível logo
após o dash do Interceptar, cliente e servidor podendo avançar a
interpolação de posição em ritmos levemente diferentes), o servidor
rejeitava a skill ("Alvo inválido") mesmo quando o cliente conseguia
re-selecionar o MESMO alvo localmente pelo loop de `RemoteControlled`
— exatamente a "dessincronia" que o usuário suspeitou.

**Fix**: novo 4º loop de fallback, mesma estrutura dos anteriores, mas
usando os componentes que EXISTEM DE VERDADE no servidor
(`CombatStats`+`CharacterStats`, `CharacterStats` é o marcador de
"isto é personagem de player" — só `entity_factory.py` na criação de
player/bot adiciona, mob nunca tem). Do lado cliente esse loop novo
nunca acha nada (só o PRÓPRIO player tem `CharacterStats` real lá, já
excluído por "não auto-selecionar a si mesmo") — sem conflito com o
loop de `RemoteControlled` que já resolvia o lado cliente.

**Testes** (`tests/test_resolve_target_hostility.py`, arquivo já
existia — `TestResolveTargetPvpServerSide` novo): monta o mundo do
jeito que o SERVIDOR vê (nenhuma entidade com `RemoteControlled`) —
acha player inimigo real; não seleciona player do mesmo time; não
seleciona a si mesmo; mob hostil já achado antes tem prioridade (loop
novo nunca sobrescreve). Revert-to-confirm (`if False and best_id==-1`
no lugar do guard) reproduziu o sintoma exato (-1 em vez do inimigo)
antes de restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`).

**Bug 2 (bot com HUD de morto) — investigação em aberto, debug
adicionado.** Mapeado o mecanismo: `RemoteControlled.hp<=0` decide 2
coisas ao mesmo tempo — esconde a barra de HP (nameplate) E torna o
alvo inválido pra `_resolve_target`/ataque (bate com os 2 sintomas do
print serem, possivelmente, a MESMA causa: hp do bot lido como 0/
negativo no cliente mesmo vivo no servidor). Não achei uma causa
concreta só lendo código (o caminho de sync — `_apply_combat_result`
via `combat[]` do AOI_UPDATE — parece correto na leitura estática) —
"às vezes" pede reprodução real antes de arriscar outro fix às cegas
(lição da sessão: 4 rodadas de correção às cegas no pincel de terreno
não bastaram, ver §81).

**Debug adicionado** (`debug/aoi_debug.py`, `RPG_DEBUG_AOI=1`, mesmo
padrão já usado pra mob remoto — log em `debug/logs/aoi_debug.log`):
`PLAYER_SPAWN`/`PLAYER_SPAWN_SKIP` (`_spawn_remote_player_entity`,
`client/remote_entity_handlers.py`), `PLAYER_HP_COMBAT`/
`PLAYER_HP_COMBAT_SKIPPED` (`_apply_combat_result` — o `_SKIPPED` loga
quando `hp_after<0` chega e a atualização é pulada, candidato forte pra
causa raiz), `PLAYER_HP_STATS` (`_handle_msg_stats_update`, `client/
network_handlers.py` — canal normalmente privado, disparar pra outro
player é caso raro que merece atenção), `PLAYER_DESPAWN`
(`_remove_remote_player_entity`). Nada logado no loop de render (60fps
— inundaria o arquivo); todos os pontos são eventos de rede/estado,
relativamente raros.

**Pendente**: usuário ligar `RPG_DEBUG_AOI=1`, reproduzir o bot
"parecendo morto"/impossível de atacar na BG, e mandar
`debug/logs/aoi_debug.log` (ou o trecho relevante) — dá pra reconstruir
a linha do tempo de HP daquele bot e achar exatamente onde o valor
para de acompanhar o servidor.

**Correção 1 do bug 1 (Interceptar) não bastou — usuário testou de
novo, 28/08/2026.** Interceptou uma TORRE; depois de já adjacente e com
o alvo selecionado, a próxima skill deu "Fora de alcance" numa tentativa
e "Alvo inválido" em outra. Usuário deixou claro: o problema não é usar
Interceptar em si (funciona, intercepta certo) — é que NENHUMA skill
funciona depois, contra o alvo que acabou de ser interceptado.

**2ª rodada de investigação, sem achar a causa raiz só lendo código.**
Confirmado que torre É rastreada como "mob" pro servidor (`Combatant`
+ `TileMovement`, mesmo detector genérico de `world_server.py` —
minha suspeita inicial de torre cair fora da classificação
mob/player estava errada). Rastreei o mecanismo de lag-compensation em
`server/skill_processor.py::_process_skill_requests` — ele "snapa"
`tile_move.current_tile_x/y`+`Position` pro `target_tile_x/y` ANTES de
chamar o handler da skill (só enquanto `is_moving=True`), e RESTAURA
pro valor de antes DEPOIS do handler rodar — na leitura, essa ordem
parece correta (handler vê a posição já corrigida). Rastreei também
`TileMovementSystem` (`engine/world_systems.py`): `current_tile_x/y`
só assume `target_tile_x/y` quando `progress>=1.0` (dash/movimento
COMPLETO), junto com `is_moving=False`/`is_dash=False` — também parece
correto, sem gap óbvio pro caso "skill logo depois do dash completar".
Nenhuma das duas leituras achou um bug concreto — pode ser uma
interação entre os dois, ordem de processamento de múltiplos requests
no mesmo tick, ou algo fora do que foi lido.

**Decisão: parar de adivinhar, instrumentar o ponto exato da
rejeição** (mesma lição já registrada acima sobre o pincel de terreno
— 4 correções às cegas na mesma sessão não bastaram; aqui já é a 2ª
tentativa sem causa raiz confirmada, hora de parar). Novo
`SkillHandlers._log_skill_warn_debug()`, chamado de dentro de `_warn()`
(ponto único de verdade — TODA skill que rejeita already passa por
`_warn`, sem precisar instrumentar dezenas de handlers individualmente)
só pros 3 motivos relevantes ("Fora de alcance"/"Alvo inválido"/
"Nenhum alvo") — loga posição/tile do caster E do alvo, `is_moving`/
`is_dash` do caster, e HP do alvo, tudo no instante exato da rejeição.
Evento novo `SKILL_WARN` em `debug/aoi_debug.py` (mesmo arquivo/env var
`RPG_DEBUG_AOI=1` do bug 1 acima — ponto único de debug, não um 2º
sistema paralelo).

**Testes** (`tests/test_skill_warn_debug.py`, novo arquivo): loga pros
3 motivos relevantes com os campos certos (mock em
`debug.aoi_debug.AOI_DBG.log`); NÃO loga pra motivos irrelevantes
("Raiva insuficiente" etc.); sem alvo selecionado, loga `target=-1` e
tile/pos do alvo como `None` (sem quebrar tentando ler componente de
`-1`). Revert-to-confirm (`if False and ...` no lugar do filtro de
motivo) reproduziu a ausência de log antes de restaurar. Suíte
completa (`pytest tests/ -q`, `py -3.10`).

**Pendente**: usuário ligar `RPG_DEBUG_AOI=1`, reproduzir "skill falha
depois de Interceptar" de novo (contra torre ou bot, valem os 2), e
mandar `debug/logs/aoi_debug.log` — evento `SKILL_WARN` mostra a
posição/tile exatos do caster e do alvo no instante da rejeição,
suficiente pra confirmar (ou descartar) as 2 hipóteses já
investigadas (lag-comp e conclusão de dash) e achar a causa de
verdade.

**Correção 2 (28/08/2026, mesmo dia) — usuário rodou o servidor com
`RPG_DEBUG_AOI=1` e mandou o log de verdade.** Achado concreto via
`SKILL_WARN`: 1,4s depois de um Interceptar contra a Torre de Fogo
registrar `outcome=hit` (log `COMBAT_RESULT_RECV sid=interceptar`), o
servidor ainda tinha o player parado (`is_moving=False`) na tile
`(56,44)` — a 4 tiles de chebyshev da torre `(59,40)`. Nada bate com
"adjacente" — confirma de forma concreta a suspeita do usuário: o dash
parece funcionar no cliente, mas o servidor nunca aplicou (ou
reverteu) o resultado de verdade. `target_hp` decrescendo ao longo do
spam (2488→2454) confirma que a luta contra a torre continuava
acontecendo por OUTRO canal (auto-attack), só a skill em questão que
falhava sempre.

**Investigação adicional (sem causa raiz fechada ainda)**: conferido
que `TileMovementSystem` roda de verdade no servidor (1x/tick, global,
`WorldServer._tick()` → `self._global_tms.update(dt=dt)`, ANTES de
`_process_skill_requests()` no mesmo tick) e que sua lógica de
conclusão de dash (`progress>=1.0` → `current_tile_x/y =
target_tile_x/y` + atualiza `_last_valid_tile_x/y`, o baseline do
anti-cheat) está estruturalmente correta na leitura. Achado
arquiteturalmente relevante, não necessariamente o bug: `move_player()`
(`server/world_server.py`, RPC de movimento NORMAL) atualiza
`current_tile_x/y` **instantaneamente** ao aceitar um MOVE — não
depende de `TileMovementSystem` pra isso (só usa `is_moving`/pixels
pra animação visual). Interceptar, ao contrário, só seta
`target_tile_x/y` — `current_tile_x/y` só vira o destino quando
`TileMovementSystem` completa o tween, 1+ tick depois. Essa assimetria
(2 caminhos completamente diferentes pra "onde o player está de
verdade") é a suspeita mais forte até agora, mas não confirmada — pode
ser normal (o dash tem só 0.18s, tempo de sobra pra completar bem antes
do 1,4s do log) e o bug estar em outro lugar (múltiplos requests de
skill no MESMO tick competindo pelo snap/restore de lag-comp do
`skill_processor.py`, por exemplo).

**Debug adicional** (mesmo arquivo/env var): `DASH_COMPLETE`
(`engine/world_systems.py::TileMovementSystem`, só quando `is_dash`
era True) — mostra o tile onde o dash REALMENTE pousou vs o tile-alvo
que ele deveria alcançar. `MOVE_REJECTED_BUDGET`/`MOVE_REJECTED_WALKABLE`
(`server/world_server.py::move_player`) — mostra se algum MOVE normal
do cliente, mandado logo depois do dash, está sendo recusado pelo
anti-cheat (o que reverteria a posição visualmente pro cliente, mesmo
sem "desfazer" o dash em si).

**Testes** (`tests/test_move_player_debug.py`, novo arquivo, usa
`WorldServer`/`move_player` reais via `tests/helpers.py`): rejeição por
orçamento loga com os campos certos; rejeição por walkability loga
(velocidade elevada só nesse teste + baseline anti-cheat "envelhecido"
manualmente, pra isolar walkability do orçamento); move aceito não
loga; dash completo loga o tile de pouso; conclusão de movimento NORMAL
(`is_dash=False`) não loga `DASH_COMPLETE`. Revert-to-confirm (renomear
o evento de `MOVE_REJECTED_BUDGET` temporariamente) reproduziu a
falha exata antes de restaurar. Suíte completa (`pytest tests/ -q`,
`py -3.10`).

**Pendente**: usuário reproduzir MAIS UMA VEZ com `RPG_DEBUG_AOI=1` no
servidor — desta vez o log deve mostrar `DASH_COMPLETE` (confirmando
se o pouso bateu com o tile-alvo) e qualquer `MOVE_REJECTED_*` no
intervalo entre o Interceptar e a skill falhando, fechando de vez qual
das 2 hipóteses (dash não completou direito vs move normal revertido)
é a causa real.

**Correção da Correção 2 — usuário apontou conclusão precipitada
(28/08/2026, mesmo dia).** A Correção 2 concluiu "o minion se moveu"
a partir só da distância no log, sem checar se o alvo deveria estar
imóvel. O usuário corrigiu: Interceptar com o talento certo atordoa o
alvo por 1s, e na BG os talentos ficam sempre ativos — um minion
atordoado NÃO deveria conseguir andar. Além disso, ele confirmou
VISUALMENTE (em várias tentativas) que o personagem estava adjacente
ao alvo quando o warning disparava — contradizendo a leitura de
distância feita a partir do log. Pediu investigação mais precisa, sem
mais suposição, e ofereceu jogar uma partida inteira pra gerar mais
dados.

**Verificação (não suposição) de que minion respeita stun**: conferido
em código — `MinionSystem` (`engine/world_systems.py`) chama
`is_action_locked(world, minion_eid)` e pula movimento/ataque quando
True (atordoado/dormindo/medo/polimorfizado/desorientado). Minion
atordoado de verdade NÃO anda — a explicação "o minion andou" da
Correção 2 só seria válida se o atordoamento não tivesse sido aplicado
de verdade (ex: talento sem os pontos certos nesse build específico,
apesar da suposição de "todo talento sempre ativo na BG").

**Debug de 3ª rodada** (mesmo arquivo/env var) — captura a leitura
INTERNA de `_skill_interceptar` que nenhum log anterior mostrava:
`INTERCEPTAR_CAST` (`engine/skill_handlers.py`) — tile do alvo LIDO no
instante do cálculo do destino do dash, o destino escolhido, e se o
alvo já estava atordoado por um Interceptar anterior; `INTERCEPTAR_
STUN_CHECK` — a duração REAL de atordoamento usada nesse cast (0/None
= o talento não estava ativo pra esse cast, contrariando a suposição).
`SKILL_WARN` ganhou 2 campos novos (`target_stunned`/
`target_is_moving`) pra cruzar com os 2 anteriores no instante exato
da rejeição.

**Testes** (`tests/test_interceptar_debug.py`, novo arquivo — usa
`WorldServer._process_skill_requests()` REAL via fila de pending
requests, mesmo padrão de `tests/diag_interceptar.py`, não a
mock/fixture leve): `INTERCEPTAR_CAST` loga a leitura certa do tile do
alvo e um destino adjacente a ele; `INTERCEPTAR_STUN_CHECK` loga a
duração (o personagem de teste sem talento aplicado corretamente
mostrou `stun_duration=0.0` — confirma que o campo reflete a
configuração real do build, não um valor fixo). Revert-to-confirm
(renomear o evento `INTERCEPTAR_CAST` temporariamente) reproduziu a
ausência do log antes de restaurar. Suíte completa (`pytest tests/ -q`,
`py -3.10`).

**Pendente**: usuário reproduzir de novo (ou jogar uma partida inteira,
como ofereceu) com `RPG_DEBUG_AOI=1` no servidor — `INTERCEPTAR_CAST`
mostra se a leitura da posição do alvo, no instante em que o destino do
dash é calculado, já bate com o que o usuário via na tela; `INTERCEPTAR_
STUN_CHECK` confirma se o atordoamento realmente estava ativo. Essas 2
informações, cruzadas com `SKILL_WARN`/`DASH_COMPLETE` já existentes,
devem fechar a causa raiz de vez.

**Causa raiz confirmada, RESOLVIDO (30/08/2026).** Usuário jogou uma
partida inteira e mandou um log de ~24 mil linhas; relatou também que o
sintoma é MAIS AMPLO do que só pós-Interceptar — "às vezes eu seleciono
o alvo e uso a skill e também diz que o alvo é inválido". No novo log,
achado o caso definitivo: `SKILL_WARN reason=Fora de alcance` repetindo
com o MESMO `caster_tile=(59,41)`/`caster_pos=(1832.9, 1370.7)` por 6+
segundos seguidos, sempre com `caster_is_dash=True` e
`caster_is_moving=False` ao mesmo tempo — estado internamente
inconsistente (`is_moving`/`is_dash` só existem juntos ou desligam
juntos na conclusão normal do tween). `caster_pos` também não batia com
o centro de NENHUM tile (1904,1328 seria o centro de 59,41) — a posição
estava congelada NO MEIO de uma interpolação, não travada num tile
inteiro.

**Mecanismo (confirmado em código, não inferido dos números)**:
`TileMovement.is_moving` é escrito por 2 sistemas com relógios
independentes:
- `TileMovementSystem.update()` (`engine/world_systems.py:4238-4278`) —
  o tween REAL. Só desliga `is_moving`/`is_dash` quando `progress>=1.0`
  do próprio `move_duration` da entidade.
- `ServerCombatStateSystem._tick_player_move_grace`
  (`engine/core_systems.py:844-861`) — relógio FAKE e paralelo, existe
  só pra mecânicas que precisam saber "o player andou recentemente"
  (Calmo e Certeiro, regen de Concentração). Todo `move_player()`
  (movimento normal) seta `_server_move_grace = PLAYER_MOVE_GRACE_S`
  (0.4s fixo) e, quando esse tempo zera, desliga `is_moving` **sem
  checar se há um tween de verdade em andamento**.

Pra movimento normal isso nunca quebra: `move_player()` seta
`move_duration` do próprio tween IGUAL ao grace (0.4s), então os 2
relógios sempre zeram juntos. Interceptar quebra essa simetria: usa
`move_duration = INTERCEPT_DURATION = 0.18s`
(`engine/skill_handlers.py:728`) e **nunca reseta `_server_move_grace`**.
Se o player tinha dado um passo normal pouco antes de castar Interceptar
(sobrando, por exemplo, 0.15s de grace de um passo anterior), esse
grace zera ANTES do dash de 0.18s terminar — desliga `is_moving=False`
no meio do tween. No tick seguinte, `TileMovementSystem` para de
processar essa entidade inteira (só processa `if tile_movement.
is_moving:`) — `progress` congela, `Position` fica presa no meio da
interpolação, `current_tile_x/y` fica preso no tile de ANTES do dash, e
`is_dash` nunca desliga (`move_player()` também nunca reseta `is_dash`
— só um passo normal que complete o tween inteiro do início ao fim
eventualmente limpa isso, ver linha ~4377 do mesmo arquivo).

Isso explica o escopo mais amplo relatado agora: se essa corrida
acontece e o player NÃO anda de novo antes de tentar outra skill, a
posição congelada continua errada pra qualquer skill seguinte — parece
"aleatório"/sem relação com Interceptar do ponto de vista do jogador,
porque o Interceptar que causou pode ter sido minutos antes.

**Fix** (`engine/core_systems.py::ServerCombatStateSystem.
_tick_player_move_grace`): nunca desliga `is_moving` à força enquanto
`tm.is_dash` for True — mesmo padrão já usado em `TileMovementSystem`
pra proteger um dash de ser interrompido por stun/sleep (guard
`if not tile_movement.is_dash:`, já existia antes). Genérico: protege
qualquer dash futuro (não só Interceptar), sem hardcode de skill.
Opção alternativa (zerar `_server_move_grace` dentro de
`_skill_interceptar` ao iniciar o dash) foi considerada e descartada —
exigiria repetir a mesma correção em qualquer dash futuro; a escolhida
resolve na fonte genérica.

**Testes** (`tests/test_move_grace_dash_guard.py`, novo arquivo, 3
testes): grace expira com `is_dash=True` não desliga `is_moving`;
grace expira com `is_dash=False` preserva o comportamento original
(desliga); grace ainda não expirou não mexe em nada. Revert-to-confirm
feito (removido o guard, confirmado que o 1º teste falha exatamente
como o bug real — `is_moving=False` com dash em andamento — antes de
restaurar). Suíte completa (`pytest tests/ -q`, `py -3.10`): 1364
passed, zero regressão.

**Validado em jogo pelo usuário (30/08/2026)** — "Validado." Fechado.

## 29. Auto-regen "fantasma" na BG — HP5 tickando com o personagem aparentemente cheio (30/08/2026)

Usuário relatou: dentro da BG, mesmo com o personagem em full HP, o
auto-regen (HP5) ficava tickando (floating text de cura aparecendo
repetidamente). Fora da BG isso não acontecia. Pedido explícito: investigar
se é estrutural e, se for, procurar outras ocorrências da mesma classe.

**Investigação** (agente + verificação direta no código, seguindo a regra
de não confiar em achado de terceiro sem confirmar): `_tick_hp5`
(`engine/core_systems.py`) — o regen do player em si — está corretamente
travado contra HP cheio, único sistema server-side, mesmo pra BG e mundo
aberto. Regen de mob/boneco de treino/consumível HoT também corretos.
Cliente não prediz HP5 localmente (100% autoritativo do servidor). Nenhum
desses é a causa.

**Causa raiz real (não é bug de "porta"/gate, é bug de DADO)**: qualquer
coisa que aumenta `stamina` (que deriva `max_hp` direto,
`_calculate_max_hp() = int(stamina)`) precisa RE-ESCALAR `current_hp`
proporcionalmente, não preservar o valor absoluto — senão um player 100%
cheio (ex: 300/300) vira, por exemplo, 300/330 depois de um +10% de
stamina: sem cura nenhuma, sem aviso, e o HP5 (corretamente!) começa a
tickar pra fechar esse buraco de verdade. `recalculate_combat_stats`
(`engine/stat_fns.py`) documenta que preserva o ABSOLUTO de propósito —
comportamento CERTO só quando `max_hp` diminui (evita HP negativo), mas
sozinho cria esse buraco quando `max_hp` aumenta.

O padrão CERTO (rescalar pela PORCENTAGEM) já existia — 2x, duplicado
com pequenas diferenças: `_roll_procs` (proc de item, engine/
core_systems.py) e `_tick_timed_modifiers` (expiração de buff, mesmo
arquivo). Mas faltava em TODO OUTRO lugar que muda `stamina`:

- **`WorldServer._grant_jungle_boss_buff`** (server/world_server.py) — o
  chefe "Guardião de Pedra" da jungle (`content/jungle_definitions.py`,
  conteúdo EXCLUSIVO de BG, por isso o usuário só via isso lá) dá +10%
  de stamina pro time inteiro a cada abate — a cada vez, um buraco novo,
  compondo ao longo da partida. Achado confirmado exatamente pelo
  usuário testando: "entrar na BG sozinho não tickava, só depois de
  comprar um item que aumentou o HP".
- **`WorldServer._apply_equipment_modifiers`** (server/world_server.py) —
  mesma lacuna, trocar de equipamento com bônus de stamina. Não é
  exclusivo de BG (existe em qualquer lugar), mas passa despercebido no
  mundo aberto por ser evento único (equipar 1x) em vez de um ciclo
  repetido a cada ~90s como o buff do chefe — mesmo defeito, sintoma
  menos notável. Confirmado pelo próprio usuário como o gatilho real que
  ele reproduziu.
- **`WorldServer._apply_talent_modifiers`** (server/world_server.py) —
  mesma lacuna, hoje adormecida (nenhum talento atual mexe em stamina),
  mas sem correção preventiva reproduziria o mesmo bug no instante em
  que um talento futuro desse +stamina, sem nenhum aviso de code review
  pra pegar isso (a lógica de rescale nunca foi um "dono único", só
  copy-paste em 2 lugares).

**Fix**: novo `engine/stat_fns.py::recalculate_preserving_hp_pct(cs,
mutate_fn)` — ponto único de verdade, recebe a mutação (add/remove
modifier, troca de lista inteira de modifiers, etc.) como callback,
captura a porcentagem de HP ANTES, executa a mutação, e rescala
`current_hp` pela MESMA porcentagem depois (só se `max_hp` realmente
mudou — evita perda por truncamento em `int()` no caso comum de "nada
mudou"). `_roll_procs`/`_tick_timed_modifiers` foram refatorados pra usar
o helper (elimina a duplicação original) — e os 3 pontos que tinham a
lacuna (`_grant_jungle_boss_buff`, `_apply_equipment_modifiers`,
`_apply_talent_modifiers`, este último com toda a sequência de mutação
da função embrulhada de uma vez, já que ela chama `apply_char_stats_
to_combat` + N `add_modifier` em sequência) passaram a usá-lo também.

**Testes** (`tests/test_hp_pct_preserve_on_stamina_change.py`, novo
arquivo): unitário do helper (HP cheio continua cheio, HP parcial
preserva a porcentagem — não o absoluto —, redução de max_hp continua
clampando como antes, atributo que não muda max_hp não mexe em
current_hp); `WorldServer.equip_item_from_inventory` real (cenário exato
relatado pelo usuário); `WorldServer._grant_jungle_boss_buff` real (2ª
morte do camp, que dá o buff de stamina no ciclo). Revert-to-confirm no
helper central (removida a rescala final) — 5 dos 7 testes falharam
exatamente como o bug real antes de restaurar. Suíte completa (`pytest
tests/ -q`, `py -3.10`) rodando ao final.

## 30. Skill real sumia da hotbar ao SAIR da BG — não é bug do `/ff`, pré-existente (01/09/2026)

Usuário usou `/ff` (fila real, §92) level 1, só com Golpe Poderoso na
barra dentro da instância; ao sair, "Impacto" (skill real de guerreiro,
já tinha antes de entrar) sumiu da barra — as outras voltaram certas.
Hipótese inicial do usuário: `/ff` não estava "desinstanciando"
corretamente. Usuário pediu investigação mais funda antes de aceitar
minha 1ª hipótese, citando que esse tipo de bug já tinha acontecido e
sido corrigido antes — correto pedir isso, então a causa foi confirmada
por REPRODUÇÃO (teste com o payload exato que o servidor manda, não só
leitura de código) antes de eu declarar corrigido.

**Causa raiz, confirmada por reprodução com o nome exato da skill
relatada**: `client/network_handlers.py::_handle_msg_stats_update`
processa o campo `skills_hotbar` (o bloco descrito no §73) ANTES do
campo `in_instance` (bloco mais abaixo na mesma função, que atualiza
`InstanceInventoryUIState.active`). `server/instance_progression.py::
exit_normalized_progression`→`_push_stats_update` sempre manda os dois
campos JUNTOS, no MESMO STATS_UPDATE, ao sair de qualquer instância —
não só via `/ff`. No instante em que `skills_hotbar` é processado, o
flag "estou na BG" ainda reflete o estado de ANTES de sair (`True`,
stale) — o filtro que só deveria valer DENTRO da instância
(`INSTANCE_SKILL_UNLOCK_ORDER` da classe) ainda é aplicado à lista
REAL de skills que o servidor mandou pra restaurar, descartando
qualquer skill real fora dessa lista curada. "Impacto" é exatamente
esse caso: 2ª skill real de guerreiro (`SKILL_ORDER_BY_CLASS`), mas
ausente de `INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"]` (vai direto de
Golpe Poderoso pra Interceptar).

**Por que parecia ligado ao `/ff` e não é**: rastreado o caminho
completo, `/ff` (`request_bg_forfeit`→`_finish_bg_match`) e Nexus
derrubado (`notify_bg_queue_nexus_destroyed`→mesma `_finish_bg_match`,
§92) convergem pro MESMO `request_bg_leave`→`exit_normalized_
progression` na hora de sair de verdade — nenhum código novo desta
sessão fica nesse caminho. Sem entrada em `PROBLEMAS_ARQUITETURA.md`
nem commit documentando um fix anterior pra esse sintoma específico
(skill sumindo AO SAIR — §73/§74 tratam de skill sumindo/trocando de
posição DENTRO da instância, mecanismo relacionado mas diferente);
mais provável é essa combinação de payload nunca ter sido exercitada
antes com uma skill real fora da lista curada envolvida.

**Fix**: bloco `skills_hotbar` passa a ler `payload.get("in_instance")`
primeiro (valor explícito DESTE payload), só caindo pro `.active` do
componente (estado desatualizado) quando o payload não trouxer o campo
— MESMO padrão já usado por outro bloco poucas linhas acima na mesma
função (atributos brutos/`PermanentStats`, comentário original: "usa o
valor deste payload se vier explícito (enter/exit sempre mandam),
senão o estado atual"). Nenhuma mudança no servidor.

**Testes**: novo `tests/test_client_instance_sync.py::
TestSkillsHotbarExitCombinedPayload` — simula o payload EXATO que
`exit_normalized_progression` manda (`in_instance: False` +
`skills_hotbar` no mesmo dict), skill real fora da ordem curada
(testado com skill genérica E com "impacto"/guerreiro especificamente,
reproduzindo o relato). Revert-to-confirm — reverter pro `.active` puro
reproduziu o sumiço exatamente antes de restaurar. Suíte completa
(`pytest tests/ -q`, `py -3.10`): 1431 passed + 1 falha isolada
(`TestTickPerfProfiler::test_tick_lento_grava_caminho_critico_no_log`,
sensível a timing de wall-clock sob carga da suíte completa — passa
sozinho, confirmado não-relacionado, arquivo/mecanismo nunca tocado
por esta mudança).

## 31. Chão de `map_1` "vazio" — família de sprite de grama antiga deletada pelo usuário durante a limpeza do §94 (02/09/2026)

Usuário fez limpeza própria no catálogo de `engine/tileset.py` (mesma
sessão do §94) e deletou uma família de sprite de grama antiga
("TX Tileset Grass", prefixo `"txg"`, + outra `"gc"`) — sem saber que
`map_1_terrain.csv` ainda referenciava tiles dela em ~62% das células
do mapa. Sem a família registrada, `_parse_terrain_cell`
(`engine/map_loader.py`) não reconhece mais esses ids, cai no
fallback `(cell, "")` — o char de terreno vira o próprio texto da
célula, que nunca bate com nenhuma chave de `TILE_MAPPING`, e
`entity_factory.py` usa `FLOOR_TILE` (chão cinza genérico) como
último fallback. Resultado: 444.925 de 716.604 células (62%) do chão
principal viraram chão cinza liso.

**Fix, 1ª passada**: script (não à mão — arquivo grande demais pra
editar linha a linha com segurança) reusa `load_map_csv` (parser real)
+ a MESMA lógica de sorteio 80% pura/20% flor de `ui/god_mode.py::
GodModeEditor._pick_terrain_variant` (zonas `grama_pura`/`grama_flor`
da família `"TX Tileset Terrain"`, prefixo `"ter"`, ainda intacta) +
o MESMO merge/`csv.writer` de `_save()`/`_write_csv` — nunca
reimplementa o parsing/serialização, só reusa. Detecção: célula cujo
char não está em `TILE_MAPPING`. Decisão do usuário: só preenchimento,
SEM autotile de borda (`_apply_smart_terrain` completo, com ajuste de
vizinho, ficou de fora de propósito — risco desproporcional numa
mudança desse tamanho). Validado por script antes/depois de gravar:
zero célula quebrada restante, nenhuma linha mudou de tamanho, CRLF do
arquivo preservado.

**2ª passada, achada pelo usuário via screenshot**: mesmo depois da 1ª
passada, sobrou um padrão nítido de tiles cinza-escuro (`#2D2D2D`)
espalhados dentro do gramado, formando algo como um checkerboard.
Causa: o char `"_"` (chão genérico, `FLOOR_TILE`) É uma chave válida
de `TILE_MAPPING` — a 1ª passada só detectava char INVÁLIDO, então
pulou essas células, mesmo sendo parte do MESMO artefato da grama
deletada (só que, por coincidência, `"_"` resolve pra algo "válido"
em vez de cair no fallback). Achado confirmado por dado, não suposição:
histograma de "quantos dos 8 vizinhos já são grama" pra cada célula
`"_"` mostrou distribuição claramente bimodal — 97,7% (176.653 de
180.843) com 4+ vizinhos de grama (pico em 6/8), só 2,3% com 0-3
(provável chão de verdade — praça, construção). Fix: mesmo pincel da
1ª passada, aplicado só às células `"_"` com 4+ vizinhos de grama E
NENHUM vizinho sendo terreno "de verdade" diferente (pedra/parede/
água/montanha/areia/terra — 2ª trava de segurança, evita mexer em
borda de praça/construção por coincidência de contagem). 172.421
células elegíveis, calculadas a partir do estado ORIGINAL do grid
(nunca durante a própria varredura — evita viés de ordem de escrita).
8.422 células `"_"` sobraram intocadas (tocam terreno real, ou têm
poucos vizinhos de grama) — provável chão legítimo.

**Efeito colateral não relacionado ao fix, achado durante a
investigação**: o servidor já estava rodando (mapas são cacheados em
memória na inicialização, `engine/map_loader.py::_MAP_CSV_CACHE`) —
o fix só aparece em jogo depois de reiniciar o processo do servidor;
avisado ao usuário em vez de reiniciar sozinho (poderia ter gente
conectada).

**Testes**: nenhum automatizado novo — é reparo de CONTEÚDO (dado de
mapa), não lógica de jogo; validação é o próprio `load_map_csv`
reparseando o arquivo sem erro + contagem de células quebradas = 0,
rodado depois de cada uma das 2 passadas. `pytest tests/ -q` não
tocado (nenhum teste depende do conteúdo específico de `map_1`).

**Fora do automatizável**: usuário confirmou visualmente via
screenshot que a 1ª passada resolveu a maior parte, mas revelou o
padrão secundário (`"_"`) — a 2ª passada ainda não foi confirmada
visualmente em jogo no momento deste registro.

**Nota (02/09/2026, mesmo dia)**: a 1ª passada de fato não persistiu na
1ª tentativa — o usuário tinha o editor de mapa (F10) aberto num
cliente que carregou o mapa ANTES do fix (estado antigo em memória) e
salvou por cima depois, sobrescrevendo o arquivo corrigido com a
versão antiga. Reaplicado com sucesso depois de confirmar que nenhum
cliente antigo estava rodando — mesma classe de risco documentada em
`CLAUDE.md` (nunca presumir que um arquivo tocado por script continua
como foi deixado, se algo mais também escreve nele).

## 32. Correção do §98 — 1 grupo por construção, não 1 grupo pra todas; id curto, nome só no label (02/09/2026, mesmo dia)

Usuário rejeitou o resultado do §98 na hora: as 29 construções
distintas (torre, casa, celeiro, etc.) foram todas despejadas dentro
de UM grupo só chamado `"construcoes"` — exatamente o oposto do que os
retângulos verdes de `(groups).png` pediam. O motivo de o usuário ter
criado esse arquivo era precisamente delimitar 1 agrupamento por
construção no God Mode (pra elas não ficarem misturadas/espalhadas
numa lista plana de 71 peças não-relacionadas) — a leitura do §98 de
"groups = referência de proximidade pra não perder peça durante
extração" estava certa quanto à EXTRAÇÃO (peças de construções
diferentes coladas sem gap no sheet, teria juntado errado se confiasse
só no bloco verde), mas errada quanto ao USO final do agrupamento no
catálogo — a extração cuidadosa peça-a-peça devia ter virado 29 grupos
(1 por construção reconhecida), não 1 grupo genérico.

Segundo problema, mesmo feedback: id de cada peça carregava o nome
descritivo INTEIRO (`"casa_pequena_chamine_1"`,
`"torre_redonda_fortificada_1"`, `"banca_mercado_terracota"`) — nunca
o padrão do resto do catálogo (`"TX Grades2"`: ids tipo `"1"`/`"2b"`;
`"TX Village Plant"`: `"t1"`.."t15"`/`"b1"`.."b20"`; `"TX Village Props
with Shadow"`: números puros `"116"`/`"117"`). Nome descritivo tem
lugar certo — o campo `"label"` do GRUPO (mostrado no God Mode como
nome da categoria) — nunca o id da peça individual.

**Fix**: 71 peças de "construcoes" viraram 29 grupos (`"b01"`.."b29"`,
chave curta, `"label"` com o nome descritivo, prefixo `"vb"` igual em
todos — nada foi colocado em mapa real ainda com esses ids, então não
havia risco de quebrar referência existente ao reestruturar). Id de
peça única = só a chave do grupo (`"vb_b05"`); peça múltipla =
`"{grupo}_{1,2,3...}"` (`"vb_b01_1"`, `"vb_b01_2"`). `"muralhas"` e
`"mercado_e_diversos"` já eram, de fato, 1 categoria coesa cada
(confirmado por inspeção visual no §98 — kit de muro modular sem
sub-construção, barracas/tenda/poços/biombos sem sub-construção) —
mantidos como grupo único, só os ids encurtados (`"m01"`.."m17"`,
`"d01"`.."d16"`, comentário inline com o nome por peça pra não perder
contexto de leitura no código). Revalidado: 104 ids únicos (script,
não contagem manual), compila, carrega, suíte completa sem regressão
nova (mesmas falhas pré-existentes do §96, não-relacionadas). Ver
`PROBLEMAS_ARQUITETURA.md` §98 pro histórico completo da extração
(script/component-labeling, contact sheet de QA) — só o AGRUPAMENTO/
NOMENCLATURA mudou aqui, geometria/colisão/vision_height idênticos ao
§98.

**Lesson**: "peças continuam objetos independentes" (decisão de
mecanismo, §98) não implica "1 grupo só serve" — são decisões
ortogonais. O padrão já em uso no catálogo (`"groups"` = 1 categoria
reconhecível por vez, ids curtos, nome no label) devia ter sido
seguido por analogia direta, não reinventado; o próprio usuário
apontou isso ("você nem sequer olhou os exemplos que tinham no
catálogo").

**Validado em jogo pelo usuário (03/09/2026)**: catálogo completo
(§98/§99/§100) confirmado — peças posicionadas no God Mode, sem
problema encontrado.

## 33. Colisão errada em 19 das 104 peças de "TX Village Building with Shadow" — bounding box de blob fundido, não da peça (02/09/2026, mesmo dia)

Usuário testou o catálogo do §98/§99, corrigiu 1 peça na mão
(`vb_b01_2`) como exemplo e pediu revisão de todas as outras — "a
maioria que está errado, são os que estão fatiados". Causa raiz: a
extração de `collision_rect` (§98) fazia component-labeling
(`scipy.ndimage.label`) na imagem `(collisions).png` INTEIRA pra achar
os blobs de colisão, depois intersectava o BOUNDING BOX de cada blob
com os limites de cada peça (`dimensions`). Quando duas peças vizinhas
(empilhadas ou lado a lado) têm colisão que se toca/sobrepõe no eixo
perpendicular ao corte — ex: corpo da casa (`vb_b01_1`) tem colisão até
y=256 numa faixa de x larga, e o alicerce (`vb_b01_2`) começa em
y=256 numa faixa de x mais estreita mas DENTRO da faixa do corpo — o
flood-fill funde os dois blobs num só, e o bounding box do blob
fundido é maior que a colisão real de QUALQUER uma das duas peças
sozinha. Intersectar esse bbox inflado com os limites da peça não
corrige o problema porque bbox é uma aproximação retangular — não
reflete que os pixels de colisão de fato ali dentro pertencem, em
parte, à peça vizinha.

**Fix, seguindo a dica literal do usuário** ("dentro de cada dimensão,
aquela colisão está fatiada também, então é só pegar a posição da
colisão dentro da dimensão"): trocar "intersectar bbox do blob global"
por "recortar a máscara de colisão pra dentro da janela exata da peça
PRIMEIRO, e só then calcular o bounding box dos pixels de colisão
daquele recorte" — nunca olha pra fora da própria peça, então imune a
qualquer fusão de blob vizinho, não importa quão grudados os pedaços
estejam no sheet. Recalculado pra todas as 104 peças, comparado peça a
peça contra o valor antigo: 19 mudaram (todas em peças fatiadas, como
o usuário avisou), 85 continuaram idênticas (peças isoladas, sem
colisão vizinha grudada, nunca tiveram o bug). O novo método reproduz
EXATAMENTE a correção manual do usuário em `vb_b01_2`
((64,0,128,64)) — confirma que o método novo é o certo, não só uma
correção pontual. As 19 mudanças verificadas visualmente (overlay do
retângulo novo sobre o PNG de colisão real, 3 amostras — torre com
alicerce, torre-celeiro-anexo, casarão com torre) antes de aplicar; 1
peça (`vb_b18_3`, sliver fino de sombra/borda de telhado) passou a não
ter colisão nenhuma (`None`) — colisão vermelha que aparecia perto
dela pertencia inteiramente à peça vizinha (`vb_b18_2`), confirmado
visualmente.

**Lesson**: extração por bounding-box de blob assume implicitamente
que cada blob = 1 peça — falso premise nunca verificada quando o
sheet empacota peças sem gap (comum em texture packer). "Recortar
primeiro, medir depois" (escopo = a peça, nunca a imagem inteira) é o
padrão certo pra qualquer extração peça-a-peça futura neste ou em
outro sheet fatiado.

## 34. 7 falhas "pré-existentes" na suíte eram regressão real da remoção de "TX Props"/"TX Plant" — 5 testes com id morto + regra de bloqueio de visão de pedra grande perdida (02/09/2026)

Vinham sendo tratadas como "falhas pré-existentes, não-relacionadas"
desde o §96 (rotuladas assim porque nenhuma delas tinha relação com o
trabalho da sessão até então) — usuário pediu pra investigar e
resolver de verdade em vez de continuar reportando como ruído.

**Causa raiz confirmada via `git diff HEAD`**: em algum ponto (fora
desta sessão, mudança não commitada, presente no working tree desde o
início) as famílias `"TX Props"` (prefixo `"pr"`) e `"TX Plant"`
(prefixo `"pl"`, catálogo antigo — diferente da `"TX Village Plant"`
atual, que também usa `"pl"` mas é outro arquivo/outros ids) foram
inteiramente comentadas em `engine/tileset.py` — supersedidas por `"TX
Village Props with Shadow"` (catalogada nesta sessão, §94) e `"TX
Village Plant"` (catalogada em sessão anterior, §78). 5 testes
antigos ainda referenciavam ids só a família antiga tinha:
`"pr_rock1"`, `"pr_bigrock"`, `"pr_box1"`, `"pl_vomito"` —
`OBJECT_MAPPING[id]` levantava `KeyError`/retornava `None`.

**2 fixes distintos, não só 1**:
1. **Troca de id morto por equivalente real** — 4 testes só precisavam
   apontar pra um id ativo com a MESMA propriedade que o teste
   verifica: `"pr_box1"` (32×64, sólido, colisão "base") → `"vp_067"`
   (`"TX Village Props with Shadow"`, mesmas dimensões/solidez, usado
   como placeholder de "caixa" em ~20 lugares em `test_client_ui.py`/
   `test_session.py` — troca única, `replace_all`, nenhum outro teste
   dependia do id resolver de verdade, só de bater a string);
   `"pr_rock1"` (pedra pequena, sem colisão, `vision_height=0`) →
   `"vp_186"` (mesma coisa em "TX Village Props with Shadow"/grupo
   "pedras"); `"pl_vomito"` (sprite passável genérico, `is_solid=
   False`) → `"vp_186"` de novo (serve igual, é só um objeto sem
   colisão qualquer).
2. **Regra de gameplay perdida de verdade, não só nome de teste** —
   `"pr_bigrock"` tinha `vision_rect="full"`/`vision_height=2`
   explícito (pedra GRANDE bloqueia visão como árvore/bush, regra
   original de 13/08/2026, documentada em `CLAUDE.md`). NENHUMA pedra
   de `"TX Village Props with Shadow"` (grupo "pedras", catalogado no
   §94 desta sessão) carrega essa marcação — a regra foi perdida na
   migração de catálogo, silenciosamente, e só os testes antigos
   (ainda não adaptados) denunciavam isso. Fix de verdade, não só
   troca de id no teste: adicionado `vision_rect="full"`,
   `vision_height=2` em `"vp_199"` (grupo "diversos" — não "pedras",
   mas visualmente É uma pedra/pequeno amontoado de rochas, colisão já
   cobre quase o sprite inteiro (50×34 de um sprite 50×34), candidata
   natural pra herdar a regra restaurada). Escolha de QUAL pedra devia
   bloquear visão confirmada com o usuário (03/09/2026) — `vp_199`
   continua no catálogo (`OBJECT_MAPPING["vp_199"]`, id só existe
   montado em runtime, não aparece literal no arquivo — checado direto
   pra confirmar nada foi apagado numa limpeza paralela de duplicatas),
   `vision_rect="full"` funcionando. Se outra pedra "grande" precisar
   da mesma regra depois, é ajuste de conteúdo novo, não bug.

**Validação**: os 3 arquivos de teste tocados (`test_vision_blocking.py`,
`test_client_ui.py`, `test_session.py`) rodados isolados — 279 passed,
0 failed. Suíte completa (`pytest tests/ -q`) rodada em seguida pra
confirmar ausência de regressão em qualquer teste que dependia do
`"pr_box1"`/`"pl_vomito"` só como placeholder de string (sem resolver
sprite de verdade).

**Lesson**: "falha pré-existente, não-relacionada" é uma classificação
de CONVENIÊNCIA pra não bloquear o trabalho da sessão atual — não é a
mesma coisa que "não é bug real". As mesmas 7 falhas foram carregadas
por 3 sessões seguidas (§96 → §98 → aqui) sem que ninguém investigasse
se eram ruído de fato ou uma regressão de gameplay real (era — pedra
grande parou de bloquear visão). Mesma classe de disciplina já exigida
pelo `CLAUDE.md` pra itens "MITIGADO" do `PROBLEMAS_ARQUITETURA.md`:
rótulo repetido em sessões sucessivas sem revisitar é sinal de dívida
real, não de ruído confirmado.

## 35. Rastreio de quest só considerava o 1º spawn_zone de um mob com vários pelo mapa (04/09/2026, mesmo dia)

Usuário reportou: quest de matar Lobo não rastreava um spawn de lobo
específico no mapa — e perguntou diretamente se o sistema só olhava o
1º spawn do alvo no `{map}_entities.json`.

**Causa raiz confirmada**: exatamente isso. `ui/map_markers.py::
_find_spawn_zone(spawn_points, race)` (singular) fazia `for zone in
spawn_points["spawn_zones"]: if zone["race"]==race: return zone` — parava
no PRIMEIRO match. `map_1` tem "Lobo" cadastrado em 5 `spawn_zones`
diferentes (posições/raios distintos, JSON legítimo — não é erro de
dado) — só o 1º da lista virava círculo, os outros 4 ficavam invisíveis
no rastreio, mesmo sendo alvos igualmente válidos pra completar a quest.

**Fix**: `_find_spawn_zone` → `_find_spawn_zones` (plural), devolve
TODAS as zonas cadastradas pra aquela raça, não só a primeira.
`_resolve_objective_shape` → `_resolve_objective_shapes`, devolve uma
LISTA de `QuestCircle` (antes devolvia 1 ou `None`) — `collect_item`s
que resolvem como `kill` usam a mesma lógica. `collect_quest_circles`
agora itera essa lista e cria 1 círculo por zona, todos com o MESMO
`quest_title`/`objective_label` (é o mesmo objetivo, só o alvo aparece
em vários lugares do mapa).

**Testes**: `tests/test_quest_map_tracking.py::
test_kill_com_varios_spawns_da_mesma_raca_gera_um_circulo_por_zona` —
fixture com 3 `spawn_zones` de "Lobo" em posições diferentes, confirma 3
círculos (não 1). Revert-to-confirm: `_find_spawn_zones` temporariamente
revertida pra parar no 1º match — teste falhou exatamente como o sintoma
relatado (`1 != 3`). Suíte completa: 1468 passed.

**Pergunta do usuário sobre o nome do sistema**: não existe um nome
"oficial" curto ainda — respondido como "sistema de rastreio de
objetivo de quest no mapa/minimapa" (mesmo termo já usado nos títulos
de §105/§106 e nos comentários do código). Registrar aqui caso um nome
mais curto seja decidido depois, pra manter consistência em referências
futuras.

## 36. Interação com NPC não priorizava quest completável quando o objetivo pendente era "falar com ESTE NPC" (04/09/2026)

Usuário reportou (comportamento, não crash): pegou uma quest cujo único
objetivo restante era "falar com NPC X" (NPC X sendo também treinador de
classe); ao clicar com o direito em X, em vez de abrir direto a entrega
da quest, aparecia o menu "Quests/Treinamento"; entrando em "Quests", a
quest pronta pra entregar nem aparecia na lista.

**Causa raiz**: `engine/quest_logic.py::can_turn_in_after_talk()` já
existia especificamente pra esse caso (prevê "se eu falar com X agora,
a quest fica completável?" SEM mutar o QuestLog) e
`QuestDialogSystem._get_completable_quests(npc_id, pending_npc_name)`
já usa isso corretamente — mas só quando `pending_npc_name` é passado.
4 lugares que decidem o que mostrar ANTES de `_open_dialog` (que é o
único que sempre passou esse parâmetro certo) chamavam
`_get_completable_quests` sem ele:
- `ui/trainer_system.py::_open_menu` — decide se pula o menu de ação
  direto pra entrega de quest.
- `ui/trainer_system.py::_compute_options` — decide se a opção "Quests"
  aparece no menu.
- `ui/quest_system.py::QuestDialogSystem._render_list` — desenha a
  lista de quests DENTRO do diálogo (recalcula do zero pra desenhar,
  em vez de reusar o `comp` já calculado certo por `_open_dialog`).
- `ui/quest_system.py::QuestDialogSystem.marker_for` — ícone acima da
  cabeça do NPC (mostrava "?" cinza de "em progresso" em vez de "?"
  dourado de "completável").

Cada um desses reimplementava a MESMA pergunta ("essa quest está pronta
pra entregar aqui?") sem usar o parâmetro que já resolve isso — clássica
falta de ponto único de verdade: a função certa existia, só não foi
propagada pros lugares que a chamam ANTES do fluxo que já a usava certo.

**Fix**: os 4 lugares passam a fornecer o nome do NPC
(`self._tr_name` em trainer_system.py, já calculado antes; `npc_name`
já calculado em `QuestDialogSystem.render()` — repassado pra
`_render_list`; `NPC.name` lido direto dentro de `marker_for`).

**Testes**: `tests/test_quest_talk_completable_priority.py` (4 casos,
cenário exato relatado — NPC é treinador E turn_in do único objetivo
pendente): `_get_completable_quests` com/sem `pending_npc_name`,
`marker_for` mostra ícone "completável" (não "em progresso"),
`TrainerSystem._open_menu` pula direto pro diálogo de entrega
(`STATE_CLOSED` + `_dialog_state=="turnin"`, nunca `STATE_MENU`),
`_compute_options` inclui "Quests" mesmo só com a pendente de talk.
Revert-to-confirm: fix de `_open_menu` desfeito temporariamente — teste
correspondente falhou exatamente como o sintoma relatado (`ACTION_MENU`
!= `CLOSED`). Suíte completa: 1472 passed.

## 37. `dummy_id` removido — boneco de treino tratado igual mob no rastreio de quest (04/09/2026)

Usuário criou uma quest nova (editando `content/quests_data.py`
diretamente) usando `auto_attack_hit` com alvo no boneco de treino —
descobriu no processo que `auto_attack_hit` já aceita `target = nome |
raça` (documentado desde sempre, não é recurso novo) e resolveu usar
`target="Boneco de treino"` em vez de `params` (que esse tipo de
objetivo nunca leu). Ao testar, notou que o rastreio de quest no mapa
não desenhava círculo nenhum pro boneco, e perguntou por que o boneco
de treino precisava de mecanismo diferente de mob/NPC no design
original — pergunta certa, resposta é "não precisava".

**Causa raiz**: decisão original (§105) exigia `params["dummy_id"]`
pra `use_skill(on_dummy)` — assumindo (errado) que fazia sentido
apontar pra um boneco ESPECÍFICO. Na prática, boneco de treino é
100% genérico pro próprio jogo: `use_skill` com `on_dummy=True` só
checa "o alvo tem o componente TrainingDummy", nunca QUAL boneco
(`engine/quest_logic.py::match_objective`). Forçar id por instância no
rastreio era complexidade que o jogo nunca pediu — e não cobria o
caso novo (`auto_attack_hit`/`kill` no boneco), que nem tinha
mecanismo de resolução nenhum.

**Fix**: unificado com o mecanismo de mob.
- `ui/map_markers.py::_TRAINING_DUMMY_NAME`/`_TRAINING_DUMMY_RACE` —
  constantes que espelham `engine/entity_factory.py::
  create_training_dummy`'s `EntityIdentity(name="Boneco de treino",
  race="Mecânico")` (comentário cruzado nos dois lados pra não
  divergir sem querer).
- `_hostile_target_circles(spawn_points, target)` — nova função usada
  por `kill`/`auto_attack_hit`/`use_item_on_target`/fallback de
  `collect_item`: procura primeiro em `spawn_zones` (mob); se não
  achar, procura o nome/raça do boneco e circula TODOS os bonecos do
  mapa (`_dummy_positions`, sem id, mesma ideia de várias `spawn_zones`
  do mesmo mob, §107).
- `use_skill(on_dummy)` — passou a circular TODOS os bonecos do mapa
  também, sem `dummy_id` nenhum.
- Revertido por completo: `ObjectiveDef.params["dummy_id"]` (removido
  das 3 quests reais que usavam — `prova_valor`/`iniciacao_arcana`/
  `coach_quantic`, ficam só com `on_dummy: True`), campo `"id"` em
  `training_dummies` no `{map}_entities.json`, tupla de 3 elementos em
  `engine/map_loader.py`/`server/world_server.py::
  _create_training_dummies` (volta a `(x, y)`).
- `trainer_id` (learn_skill) **não muda** — já era o nome REAL do
  treinador (ex.: `"Selene Vail"`), o mesmo mecanismo de `talk_to_npc`
  por nome, só guardado em `params` porque `target` já é o skill_id.
  Não tem gordura pra cortar aí.

**Achado de bônus, não relacionado**: `tests/test_quest_turn_in.py::
test_multiplos_itens_fixos_com_stack` começou a falhar (mochila cheia)
depois de tantas rodadas da suíte completa no mesmo dia contra o MESMO
`data/game.db` real — a conta de teste "qtiuserb" acumulou `mana_potion`
até quase o `max_stack`, e conceder +3 estourava (comportamento
CORRETO de `_stack_or_add_item`, "tudo ou nada"). `_ready_session`
(fixture compartilhada da classe) não zerava o Inventory antes, ao
contrário de outra classe no mesmo arquivo que já fazia isso — corrigido
adicionando o mesmo reset.

**Testes**: `tests/test_quest_map_tracking.py` — fixture de
`training_dummies` volta a 2-tuplas; `test_use_skill_on_dummy_resolve_
por_dummy_id` virou `test_use_skill_on_dummy_circula_todos_os_bonecos_
do_mapa` (confirma os 2 bonecos do fixture, não 1 específico); 2 testes
novos pro caso que motivou tudo isso
(`test_kill_no_boneco_de_treino_resolve_igual_mob`/`_por_raca_tambem_
funciona`). Revert-to-confirm: bloco de resolução do boneco em
`_hostile_target_circles` comentado temporariamente — os 2 testes novos
falharam (`0 != 2`), restaurado. Suíte completa: 1474 passed.

## 38. Velocidade de ataque não atualizava depois de relogar (04/09/2026)

Usuário reportou (com 2 prints): personagem com espada equipada
(attack_speed=2.1) mostrava "Vel. Ataque: 2.60s" (default da classe)
no painel de Equipamentos — mesmo com a arma certa equipada e a
tooltip do item confirmando "Speed 2.1". Achado curioso relatado: ao
atacar o boneco de treino acompanhando o intervalo real, o valor
"virou" 2.1 sozinho no meio da sessão — mas ao relogar, voltou a 2.6 e
o intervalo de ataque REAL em combate também ficou errado (não só a
tela).

**Causa raiz**: `client/save_sync_handlers.py::_restore_save_state`
reimplementava a lógica de "arma da mainhand define a velocidade" na
mão (`cs_eq.base_attack_interval = _item_eq.attack_speed`) em vez de
chamar `engine.stats_system.sync_attack_interval()` — a função CERTA
pra isso, já importada no topo do método mas nunca usada aqui. A
diferença crítica: `sync_attack_interval` chama
`cs._recalculate_effective_stats()` depois de setar `base_attack_
interval`; a versão reimplementada não chamava. `CombatStats.
attack_interval` (campo EFETIVO — o que combate e a UI de fato leem,
`CombatStats.base_attack_interval` é só a entrada crua) só é atualizado
por esse recálculo. Sem ele, `attack_interval` ficava travado no valor
anterior (default da classe, herdado da criação do personagem) até
QUALQUER outro evento disparar `_recalculate_effective_stats()` por
acaso — um item com `modifiers` não-vazio dispara isso via
`add_modifier` (chamado logo depois, no mesmo método, pra cada
modifier do item); a espada de treinamento tem `modifiers=[]`
(`content/item_table.py`), então NUNCA disparava — o valor errado
sobrevivia a sessão de login inteira. Explica os 2 sintomas: o "virou
2.1 sozinho" no meio da sessão foi algum OUTRO evento (buff, troca de
outro equipamento com modifier) disparando o recálculo por acidente;
o relogin sempre reproduzia o erro porque a sequência de restore é
sempre a mesma.

**Fix**: `_restore_save_state` passa a chamar `sync_attack_interval(
cs_eq, equip)` (ponto único de verdade, já existia, só não era usado
aqui) antes do loop que reaplica os outros modifiers dos itens
equipados — nunca mais reimplementar essa lógica em paralelo.

**Testes**: `tests/test_restore_attack_interval.py` — 2 casos: arma
sem modifiers restaura `attack_interval` efetivo corretamente (o
cenário exato relatado, antes só `base_attack_interval` corrigia);
sem arma equipada usa o default da classe nos dois campos. Revert-to-
confirm: código antigo restaurado temporariamente — teste falhou
exatamente como o sintoma relatado (`1.95 != 2.1`, o valor efetivo
travado no default enquanto o base já estava certo). Suíte completa:
1476 passed.

## 39. Interceptar: dash completa e DEPOIS é desfeito, mais comum perto de parede/torre (09/09/2026)

Usuário relatou: usando Interceptar, o personagem às vezes faz o dash
inteiro e depois "volta", como se o servidor tivesse corrigido. Mais
comum perto de parede/torre; acontece tanto em mundo aberto (vs mob)
quanto em BG (vs bot/jogador). Investigado com 3 agentes em paralelo
(cliente, servidor, histórico §84/§87) antes de codar — nenhum decidido
sozinho, design revisado com o usuário antes do fix.

**1ª hipótese, DESCARTADA após verificação mais funda**: pensei que
fosse pacote fora de ordem (comparar `seq` da correção contra o `seq`
da última confirmação de dash, ignorar se mais antigo) — errado.
WebSocket roda sobre TCP, que garante ordem de entrega; o pacote
problemático não está fora de ordem, ele chega na hora certa E na
ordem certa, só que carrega DADO ERRADO. **Lição**: antes de propor um
fix pra "corrida de rede", confirmar a garantia de ordenação da camada
de transporte real — "chegou depois" não implica "foi enviado depois"
só quando a camada NÃO garante ordem; aqui garante, então a explicação
tinha que estar em outro lugar.

**Causa raiz real**: `server/session.py::_handle_move` manda uma
correção de posição pro PRÓPRIO cliente **sempre** que
`WorldServer.move_player()` rejeita um `MOVE`, usando
`get_tile_pos()` → `TileMovement.current_tile_x/y`. Esse campo só é
atualizado quando o TWEEN do dash **completa**
(`TileMovementSystem`, `progress>=1.0`) — durante o dash inteiro ele
aponta pra tile de ANTES dele (design original do campo, intocado
desde §87). O guard que já existe em `move_player`
(`if tm.is_dash and tm.is_moving: return False`, §87) protege
corretamente o dash de ser SOBRESCRITO por um MOVE comum — mas
`_handle_move` não sabia dessa distinção: mandava a mesma correção
genérica pra QUALQUER motivo de rejeição, inclusive esse. Um MOVE
residual de WASD (mais comum perto de parede/torre — o jogador tende a
ter tentado se posicionar/contornar o obstáculo bem na hora que aperta
Interceptar) rejeitado especificamente por "dash em andamento" gera
essa correção com a tile PRÉ-dash; se ela chegar ao cliente **depois**
que a animação do dash já tiver terminado localmente (é rápida, 0.18s),
o cliente aplica sem saber que é velha — código genérico de correção,
`client/network_handlers.py::_handle_msg_entity_move`, ramo
"DESYNC_CORR" — e desfaz um dash 100% legítimo. É um snap instantâneo
(`Position.x/y` sobrescrito direto), não uma 2ª animação — por isso
parece "volta" e não "anda de volta".

Investigação achada como "prima" de um bug quase idêntico já resolvido
em 22/07/2026 (`ui/systems.py:4691-4702`): removemos TODA predição
local do cast do Interceptar por causa de um "loop de dash e volta"
parecido — mas aquela causa era antecipação client-side antes da
confirmação; esta é uma fonte DIFERENTE (servidor mandando dado
stale), nunca coberta pelos fixes anteriores. §84/§87 (30-31/08/2026)
resolveram 2 causas de dessincronia de Interceptar bem específicas
(relógio de grace duplicado; MOVE sobrescrevendo campos do dash EM
ANDAMENTO) — confirmados intactos hoje, nenhum dos dois cobria "MOVE
rejeitado gerando correção com dado stale APÓS o dash já ter
completado". Não é regressão de nada — é um gap genuinamente não
coberto antes.

**Fix**: `_handle_move` passa a checar, quando `move_player` rejeita,
se `tm.is_dash and tm.is_moving` — se sim, não manda correção NENHUMA
(o dash já tem confirmação própria, via
`consume_skill_position_corrections`/`_dispatch_tick_deltas`; essa
posição seria sempre errada nesse caso específico). Qualquer OUTRO
motivo de rejeição (parede, orçamento de velocidade) continua mandando
a correção de sempre, comportamento intocado.

**Testes**: `tests/test_interceptar_move_correction_race.py` (novo, 3
casos) — rejeição por dash em andamento não manda correção; rejeição
por outro motivo continua mandando; MOVE aceito não é afetado. Suíte
completa: 1544 passed, 1 failed (mesmo timing flake de sempre, §123).
Achado colateral durante a verificação: uma comparação via `git stash`
pra confirmar que uma falha pontual de outro teste (fora do escopo
deste fix) não era regressão ficou contaminada porque o Docker/Postgres
local subiu NO MEIO da execução da suíte — lição registrada: confirmar
o estado do ambiente ANTES de rodar uma comparação, não confiar em
"estava rodando há pouco".

**Não validado**: reprodução manual em jogo pelo usuário (perto de
torre, Interceptar repetido).

## 40. Interceptar: dash pra um lugar e depois "volta" pro tile clicado, num loop (09/09/2026)

Usuário testou o fix do §126 e reportou o comportamento persistindo,
com um cenário mais preciso: andando por CLIQUE DE CHÃO até um ponto,
apertar Interceptar ANTES de chegar lá fazia o personagem dar o dash e
depois ficar oscilando entre o tile clicado e a direção do mob — um
loop, não só um snap único.

**Causa raiz — mecanismo DIFERENTE do §126** (mesma família de
sintoma, "personagem sendo puxado pra 2 lugares", mas outra origem):
existe uma flag client-only, `CombatState.chase_suppressed`, que
impede o sistema de auto-perseguição (`ui/systems.py::
PlayerInputSystem._process_target`, `_auto_move_step`) de competir com
"andar até onde cliquei" (`PlayerAutoMove.ground_target`). Clicar no
chão liga essa flag corretamente (`chase_suppressed=True`). Mas usar
QUALQUER skill ofensiva a desliga de novo (`chase_suppressed=False`,
"reengajamento reativa a perseguição") — reativando o auto-chase — sem
nunca cancelar o `ground_target` que ainda estava pendente. Os dois
sistemas passam a competir pela posição do player todo frame: chase
quer ir pro mob, o clique pendente quer ir pro tile antigo.

Achado revisando o histórico: é a MESMA classe de bug já catalogada e
corrigida em §112 (04/09/2026) — só que ali o gatilho era clicar num
NPC amigável (Treinador/Ferreiro/etc.), corrigido com
`ui/npc_interaction.py::suppress_chase()`/`walk_to_npc()`. O caso
"reativar via skill" nunca foi coberto por aquele fix — a linha
`chase_suppressed = False` estava duplicada em **6 lugares** do código
(`engine/skill_handlers.py`, 3× em `ui/systems.py`, `ui/spell_system.py`,
`client/save_sync_handlers.py`), 4 deles já cancelando `ground_target`
na mão (redundante, nunca centralizado) e 2 deles NUNCA cancelando —
exatamente os 2 caminhos de "apertar uma skill" (`ui/systems.py::
_use_skill_visual_only`, o handler genérico de tecla de skill; e
`engine/skill_handlers.py::_skill_bola_de_fogo`) — é isso que o
usuário estava batendo com Interceptar (a tecla de skill passa por
`_use_skill_visual_only` antes de qualquer coisa específica da skill).

**Fix — ponto único de verdade novo**: `engine/utils.py::
reengage_chase(world, player_entity)` — inverso simétrico de
`suppress_chase`. Liga `chase_suppressed=False` E cancela
`PlayerAutoMove.ground_target`/`path`/`active`/`follow_eid` (mesmo
"reset" que WASD já fazia há tempos ao cancelar auto-move) — nunca
toca `target_entity_id`/`is_pursuing`, mesma garantia de
`suppress_chase`. Roda em código compartilhado client/servidor
(`engine/skill_handlers.py` também executa no servidor via
`HeadlessSkillHandler`) — `PlayerAutoMove` não existe do lado
servidor, por isso o guard `if auto_move is not None`. Os 6 lugares
passam a chamar essa função em vez de mexer na flag na mão — inclusive
os 4 que já estavam certos, pra nunca mais divergir.

**Escopo do fix, decidido com o usuário**: perguntado se o fix deveria
valer só pro caminho do Interceptar ou pras 6 origens de reengajamento
— usuário escolheu as 6, já que o mesmo conflito pode acontecer com
QUALQUER skill ofensiva usada durante um clique de chão pendente (só é
menos visível nas outras porque elas não teleportam o player).

**Testes**: `tests/test_reengage_chase.py` (novo, 4 casos) — função
isolada reativa chase e cancela clique/Seguir pendente; nunca toca
target_entity_id/is_pursuing; não quebra sem PlayerAutoMove (caso
servidor); cancela "Seguir" também. Bug real pego DURANTE a
implementação: 2 dos 6 call sites usavam `self.player_entity_id`
(atributo de instância) só que 1 deles (`ui/systems.py`, função de
reengajar via SPACE/TAB) na verdade recebe `entity_id` como PARÂMETRO
local da função, não tem esse atributo — `AttributeError` pego pela
suíte de regressão antes de comitar, corrigido antes de rodar a suíte
completa. Suíte completa: 1548 passed, 1 failed (mesmo timing flake de
sempre, §123, sem relação).

**Não validado**: reprodução manual em jogo (clique de chão longo +
Interceptar no meio do caminho).

## 41. Interceptar não deve ser bloqueado por entidade (player/mob/minion/NPC), só por obstáculo sólido de verdade (09/09/2026)

Pedido do usuário: Interceptar (e qualquer skill que precise do mesmo
comportamento) nunca deveria ser bloqueado por outra entidade no
caminho — só árvore/pedra/parede/etc. Usuário citou "assim como outras
skills também não bloqueiam" como referência.

**Investigação confirmou a referência do usuário, mas por um mecanismo
diferente do que ele imaginava**: nenhuma OUTRA skill chama a função
compartilhada `is_tile_walkable` — o Tiro Repulsivo (knockback do
arqueiro) já ignora entidade no caminho, só que com uma checagem LOCAL
própria (`server/spell_completion_processor.py::_is_solid`, só olha
`tile_matrix[...].is_solid`) em vez de reusar a função compartilhada.
Interceptar é o único caso que usa `is_tile_walkable` — a mesma função
que `move_player` usa pra MOVE normal — e essa função checa DUAS coisas
juntas: solidez de terreno (`tile_type.is_solid`) E ocupação por
entidade (`TileValidationSystem._occupied`, um cache reconstruído por
frame com a posição de TODO mundo com `TileMovement`). É a checagem de
ocupação que bloqueava Interceptar contra mob/player/minion no caminho
— nunca deveria, já que dash não é passo normal de WASD.

**Fix**: `is_tile_walkable` (módulo `engine/world_systems.py`, função
E `TileValidationSystem.is_tile_walkable`) ganha `ignore_entities:
bool = False` — quando `True`, pula SÓ o bloco de ocupação por
entidade (linhas do `_occupied`), nunca a checagem de solidez real nem
as regras de piso/elevação. Usado nos 2 pontos do Interceptar
(`engine/skill_handlers.py::_dash_path_clear`, o Bresenham do caminho
do dash, e a seleção do tile de destino adjacente ao alvo) — MOVE
normal (`move_player`) continua chamando sem o parâmetro, comportamento
intocado. Parâmetro nomeado (não um novo caminho de código) pra
qualquer skill futura de dash/movimento reusar sem reinventar um
`_is_solid` local como o Tiro Repulsivo teve que fazer.

**Regressão pega pela suíte antes de comitar**: `tests/test_client_ui.py`
tinha 3 classes fake (`_FakeTileValidationBlockNearest`/
`_FakeTileValidation` ×2) simulando `TileValidationSystem` pra testes
que não precisam do mapa real — nenhuma aceitava o kwarg novo
(`TypeError: unexpected keyword argument 'ignore_entities'`), porque o
wrapper de módulo agora SEMPRE repassa esse parâmetro pra qualquer
`tile_validation` registrado, real ou fake. Corrigido adicionando o
parâmetro (default `False`, nunca usado pela lógica do fake) nas 3
assinaturas — lição: um novo parâmetro numa função de serviço
registrável (`register_services`) precisa ser adicionado em TODO fake
que a substitui em teste, não só na implementação real.

**Testes**: `tests/test_interceptar_ignores_entities.py` (novo, 4
casos) — unidade (`ignore_entities` libera tile ocupado por entidade,
nunca libera tile sólido de verdade) + integração (dash real ignora
entidade no tile de destino E no meio do caminho do Bresenham). Bug de
setup pego durante a escrita do teste (não do código de produção):
mover o mob de teste pra uma tile mais distante (125,374) quebrava o
cast por um motivo não investigado a fundo (sem warning nenhum
registrado) — descartado em favor de reusar a configuração já
validada (mob em 127,374, destino real 128,374) só adicionando o
bloqueador exatamente no meio do caminho (129,374), sem reposicionar
nada que já funcionava. Suíte completa: 1551 passed, 2 failed (mesmos
2 timing flakes de sempre, §123, sem relação).

**Não validado**: reprodução manual em jogo (Interceptar atravessando
um mob/player no meio do caminho).

## 42. Skill temporária da BG rouba o slot de uma skill real que ainda vai desbloquear (09/09/2026)

Usuário reportou (relato antigo, nunca fechado — o próprio §73 tinha
essa validação marcada como "pendente, nunca confirmada em jogo")
depois de sair de uma BG: atalhos que eram 1,2,3,R virarem 1,2,4,R —
Vitória Iminente saiu do slot 3 pro 4.

**Causa raiz — interação entre 2 regras corretas isoladamente (§73)**:
1. Skills reais (aprendidas fora da BG) têm slot preferido salvo em
   `config.json::skill_slot_prefs`.
2. Dentro da BG, skills são liberadas em ordem fixa por nível
   (`INSTANCE_SKILL_UNLOCK_ORDER`). Skill que o personagem REAL nunca
   aprendeu fora (ex: Golpe Debilitante, nível 3) não tem preferência
   salva — cai no "1º slot vazio" no momento em que aparece.
3. Se essa skill "temporária" aparecer ANTES (nível 3) de uma skill
   REAL com preferência salva pra aquele mesmo slot (ex: Vitória
   Iminente, nível 5, preferência = slot 3) e o 1º vazio nesse momento
   coincidir, ela ocupa o lugar da skill real ANTES dela nem existir.
4. Quando a skill real finalmente desbloqueia (nível 5), o slot já
   está ocupado — a regra "nunca rouba slot ocupado" (deliberada, §73)
   manda ela pro próximo vazio (slot 4).
5. Ao sair da BG, a skill temporária é removida (não é real) — o slot
   3 fica livre de novo — mas a regra "skill já conhecida nunca é
   remexida" (também deliberada, §73) deixa a skill real presa no
   fallback pra sempre, mesmo com o lugar certo livre.

**Fix — decisão do usuário, entre 2 opções apresentadas**: impedir o
roubo na ORIGEM em vez de corrigir só na saída da BG (mais abrangente
— protege dentro E fora da BG, qualquer ordem de desbloqueio).
`ui/skill_slot_prefs.py::preferred_slot_for_skill` passa a fazer a
decisão de fallback COMPLETA internamente (antes só devolvia a
preferência própria ou `None`, e os 3 chamadores faziam seu próprio
"1º slot vazio" — 3 cópias da mesma lógica): agora, sem preferência
própria disponível, escaneia por um slot vazio que NÃO seja a
preferência salva de NENHUMA outra skill (mesmo uma que ainda não
existe em `ps.skills`) — só cai no 1º vazio puro (ignorando reservas)
se todos os vazios estiverem reservados por outra coisa (melhor
conceder em algum lugar do que não conceder). Os 3 chamadores
(`ui/trainer_system.py::_do_learn`, `client/network_handlers.py::
_handle_msg_skill_granted`, bloco `skills_hotbar`) simplificados —
confiam 100% no retorno da função agora, removido o `ps.skills.
index(None)` duplicado de cada um.

**Testes**: `tests/test_hotbar_bg_leak.py` — 2 casos novos
(`test_preferred_slot_fallback_pula_reserva_de_outra_skill`: unidade,
confirma que pula reserva de outra skill e só usa como último recurso
quando não há slot seguro nenhum; `test_skill_temporaria_da_bg_nao_
rouba_slot_de_skill_real_que_ainda_vai_desbloquear`: ponta a ponta,
reproduz a sequência exata level 3→5→saída da BG) + 1 teste antigo
(`test_preferred_slot_ignora_slot_ja_ocupado`) atualizado pro novo
contrato da função (antes retornava `None` quando a preferência própria
estava ocupada; agora retorna a decisão final de fallback). Bug de
edição pego durante a escrita do teste (não do código de produção):
uma asserção legítima do teste ORIGINAL (`assert ps.skills[3] is None`,
de `test_skills_hotbar_usa_preferencia_do_jogador`) foi arrastada por
engano pra dentro do MEU teste novo ao inserir conteúdo no meio do
arquivo — `git diff` confirmou a origem antes de corrigir, devolvida
pro lugar certo. Suíte completa: 1553 passed, 2 failed (mesmos 2 timing
flakes de sempre, §123, sem relação).

**Achado importante sobre distribuição**: este fix (e os §126/§127/§128
anteriores) tocam código do CLIENTE (`ui/`, `client/`) — usuário
confirmou que testa via pacote buildado (`dist/RPG_Online`), não
`python main.py` direto do repo. `deploy_server.ps1` só atualiza o
SERVIDOR — o pacote do cliente precisa ser RECONSTRUÍDO
(`release_tools/build_client.ps1`) e o usuário precisa trocar pelo
novo a cada fix client-side, ou vai continuar testando código antigo
sem perceber. Vale reforçar isso a cada fix client-side daqui pra
frente, não só no servidor.

**Não validado**: reprodução manual em jogo (subir de nível na BG até
desbloquear Vitória Iminente depois de uma skill sem preferência,
sair, confirmar slot).

---

### 🟡 §130 — `_handle_player_death` chamado 2x no mesmo tick dobrava `deaths` (não `kills`) — bug relacionado, mas diferente do relatado (09/09/2026)

Usuário relatou: "quando eu mato pela primeira vez um player ele conta 2
vezes, aparece no KDA 'Kills: 2' quando era pra ser 1", na fila real de
BG, no HUD ao vivo (não no placar final), "só de vez em quando" — sem
certeza se o golpe final foi auto-attack ou skill.

**Investigação**: checado primeiro se batia com algum item já
catalogado (regra do CLAUDE.md) — não achou nenhum. Mapeados os 3
únicos call sites de `_handle_player_death` (`server/respawn_system.py`
— "ÚNICO ponto que credita kill/assist de player", ver docstring da
função e §a unificação de 16/08/2026): (1) `_process_pvp_attack`
(combat_processor.py, kill por auto-attack PvP), (2) o loop genérico
dentro de `_process_player_attacks` (mesmo arquivo, "qualquer fonte que
zerou o HP neste tick" — pensado pra DoT/skill/mob), (3)
`ServerDeathHandler.update()` (server_death_handler.py, fallback pra
quando nenhum dos 2 anteriores capturou a morte no mesmo tick).

**Causa raiz confirmada por teste, não só leitura de código** (regra do
CLAUDE.md — "corrigido" exige reprodução, não suposição): quando o
golpe final é um auto-attack PvP, (1) e (2) rodam SEQUENCIALMENTE
dentro da MESMA chamada de `_process_player_attacks` — (1) primeiro
(mata a vítima, credita o kill certo, remove `PendingDeath`), (2) depois
(vê a MESMA transição de HP via `player_hp_snapshot`, sem saber que (1)
já tratou essa morte) e chama `_handle_player_death` de novo. A 2ª
chamada NÃO dobra `players_killed` (o dict de contribuições de dano já
foi consumido — `.pop()` — pela 1ª chamada, então o bloco de crédito de
kill nem executa), mas dobra `CharStatsTracker.deaths` da vítima, porque
esse incremento é incondicional (primeira linha de lógica da função,
roda sempre, antes de qualquer checagem de quem bateu). Reproduzido
isolado: `players_killed(matador) == 1` (certo), `deaths(vítima) == 2`
(errado).

**Isso não é o bug relatado** — usuário viu "Kills" dobrar, não
"Deaths". Rastreando os 3 call sites e a lógica de crédito inteira, não
foi encontrado nenhum caminho que dobre especificamente `players_killed`
do matador. Reportado ao usuário nesses termos; decisão explícita dele:
corrigir o bug confirmado (deaths) mesmo sem ele ser 100% o relatado, e
avisar na próxima ocorrência do "Kills: 2" com horário + se havia mais
de um atacante do time no mesmo alvo (dado que falta pra achar o
mecanismo real).

**Fix**: `_handle_player_death` (server/respawn_system.py) ganhou um
guard de idempotência no topo — se `GhostState.is_dead` já é `True`
(campo só fica `True` entre a morte e o revive, resetado por
`_revive_player`), retorna sem fazer nada. Corrigir NA função (ponto
único de verdade já existente) em vez de tentar impedir a 2ª CHAMADA em
cada call site — mais simples e cobre qualquer chamador futuro
automaticamente.

**Testes**: `tests/test_bg_kill_credit.py::TestPlayerDeathNotDoubleProcessed`
— 2 casos novos: (1) via caminho de combate real
(`_process_player_attacks` com snapshot pré-tick, mesmo formato que
`WorldServer._tick` monta), confirma `players_killed==1` e `deaths==1`;
(2) chamando `_handle_player_death` 2x direto pro mesmo eid, confirma
2ª chamada é no-op total (nem kill nem death somam de novo). Suíte
ampla de combate/morte/BG (13 arquivos, 426+ testes) sem regressão —
única falha é o flake de timer do Windows já catalogado (§123), sem
relação. Suíte completa (`pytest tests/ -q`) roda em paralelo, resultado
pendente de confirmação.

**Pendente**: mecanismo real do "Kills: 2" ainda não encontrado —
usuário avisará na próxima ocorrência com horário e contexto (sozinho ou
mais gente do time no mesmo alvo) pra reduzir o espaço de busca.
## 43. Nova Congelante mudo pro próprio conjurador com "Precisão Elemental" maxada — cast instantâneo nunca criava o SpellCast local que disparava o som (§137)

16/09/2026, usuário relatou: o som de impacto de Nova Congelante
(corrigido em §137 pra não duplicar) parou de tocar POR COMPLETO pro
conjurador — só quem recebe a skill (outro player pego pela área)
continuava ouvindo.

**Causa raiz**: "Precisão Elemental" (talento, `content/talent_data.py`)
reduz `ice_cast_time_reduction` em 0.2s/ponto, até 5 pontos = -1.0s —
exatamente igual ao `cast_time` de Nova Congelante (1.0s). Com o
talento maxado, o cast fica instantâneo (`_sc_cast == 0.0`,
`ui/systems.py`). Nesse caso o cliente **nem cria o componente
`SpellCast` local** (`if _sc_cast > 0.0:`, de propósito — evita um bug
diferente, cast com duração 0 + `interruptible=True` cancelando sozinho
ao mover). Sem `SpellCast`, `SpellCastSystem._complete_cast()` nunca
roda pra essa skill — e é ali, via `_CAST_HANDLERS["nova_congelante"]
→ _apply_nova_congelante()`, que morava o som local desde sempre.
Resultado: `skip_local_caster` (§137) suprimia o eco de rede achando
que o conjurador "já tinha ouvido local" — mas com o talento maxado
essa premissa nunca foi verdade, sobrando silêncio total.

Confirmado por instrumentação isolada (não achado por leitura só): um
script que chama `_apply_nova_congelante`/`_complete_cast` direto, com
`SOUNDS` mockado, mostra a chamada de som acontecendo normalmente
nesse caminho — ou seja, o mecanismo em si nunca teve bug de exceção
silenciosa; o problema é ele simplesmente nunca ser executado quando o
cast é instantâneo.

**Fix, a pedido explícito do usuário** ("deveria ser igual como
funciona pra bola de fogo, independente do cast ou não, executa o som
no momento que a skill sai"): em vez de consertar só o caso
instantâneo (cogitei extrair um método compartilhado chamado nos dois
casos — descartado), o som deixou de ter QUALQUER previsão local.
Removida a chamada `SOUNDS.play_spell(...)` de dentro de
`_apply_nova_congelante` (`ui/spell_system.py`) e removido
`skip_local_caster` do catálogo (`content/skill_config.py`) — o som
passa a vir SÓ do `SKILL_EFFECT` que o servidor já manda por broadcast
AOI (o mesmo que qualquer OUTRO player por perto já recebe), igual ao
modelo que Bola de Fogo já usa (efeito sempre confirmado pelo
servidor, nunca previsto localmente). Funciona idêntico com cast
normal ou instantâneo, sem precisar saber qual dos dois é — elimina
também de vez a classe de bug "previsão local duplicando eco de rede"
pra essa skill, já que não existe mais previsão local nenhuma pra
duplicar. A previsão local de dano/raiz em `_apply_nova_congelante`
(client-side, cosmética — servidor é autoritativo) continua intacta,
só o som saiu de lá.

Nenhuma outra skill afetada: `bola_de_fogo` também pode virar
instantânea (proc `fire_instant_ready`), mas seu som/projétil já vem
100% do servidor via `SKILL_RESULT` — nunca dependeu dessa barra de
cast local. `calcinar`/`polimorfia` não têm `cast_time_reduction_attr`,
cast_time nunca zera.

**Testes**: `tests/test_skill_effect_no_local_dup_sound.py` reescrito
— `test_nova_congelante_toca_pro_proprio_conjurador_via_servidor`
(antes testava o oposto, que o eco de rede NÃO tocava pro conjurador —
invertido, já que agora é a ÚNICA fonte do som), guarda de regressão
`test_nova_congelante_nao_tem_mais_skip_local_caster_no_catalogo`
(catálogo não pode reintroduzir a flag sem uma previsão local de
verdade pra cobrir o caso instantâneo) e
`test_apply_nova_congelante_local_nao_toca_som_diretamente` (se a
chamada de som voltar pra cá, duplica de novo no cast NORMAL, já que
não tem mais skip pra suprimir o eco). Revert-to-confirm: `git stash`
nos 2 arquivos do fix — 3 dos 5 testes do arquivo falham sem o fix
(os 2 que sobram são casos que já eram independentes da mudança).
Suíte completa rodando ao final da sessão.

## 44. ESC não fechava o modal de estatísticas (tecla C) — `_show_char_stats` nunca tinha entrada em `_modal_registry()`

16/09/2026, usuário relatou: apertar ESC com o modal de estatísticas
(tecla C, `CharStatsUI`) aberto não fechava — mesma classe de bug já
catalogada em 10/08/2026 pros 5 modais de PvP (ver comentário já
existente em `client/modal_stack_handlers.py::_modal_registry()`),
achada de novo num modal diferente.

**Causa raiz**: `_modal_registry()` (`client/modal_stack_handlers.py`)
é o ponto único de verdade que `_close_top_modal()` (chamado pelo ESC,
`game.py`) e `_any_modal_open()` consultam pra saber "o que está
aberto agora" — mas `_show_char_stats` nunca teve uma entrada na
lista, apesar de já ser resetado corretamente por `_close_all_modals()`
(usado ao ABRIR outro modal) desde que a skill foi implementada. Com
só o modal de estatísticas aberto, `_close_top_modal()` percorria a
lista inteira, não achava nada `is_open()==True`, retornava `False` —
e o handler de ESC em `game.py` (`elif not self._close_top_modal():
self._show_pause = True`) abria o menu de pausa por cima, sem fechar
as estatísticas.

**Fix**: nova entrada `("char_stats", lambda: self._show_char_stats,
self._close_char_stats)` no registry, na mesma posição relativa dos
outros painéis de personagem simples (`skill_level`/`talents`), e novo
`_close_char_stats()` (só `self._show_char_stats = False`, mesmo
padrão de `_close_debug`/`_close_skill_level` — `CharStatsUI` não tem
estado próprio de servidor pra desfazer ao fechar, diferente de
`_close_arena_result`/`_close_trade`).

**Teste**: novo `test_char_stats_modal_e_reconhecido_e_fechado_pelo_esc`
em `tests/test_client_ui.py`, mesmo padrão diferencial dos testes de
PvP já existentes (`_topmost_open_modal()` reconhece, `_close_top_modal()`
fecha de verdade). Revert-to-confirm: falha sem o fix
(`_topmost_open_modal()` retorna `None` em vez de `"char_stats"`).
Suíte completa de `test_client_ui.py` (171 casos, sem dependência de
banco): sem regressão.

## 45. 4 bugs reportados juntos (19/09/2026) — corpo com colisão, animação de morte, transparência de bush, bot vs. própria torre

Usuário reportou 4 sintomas na mesma mensagem. 3 tiveram causa raiz
confirmada por leitura direta do código (nunca por suposição) e foram
corrigidos; o 4º ficou **em aberto** — reproduzido duas vezes com o
pipeline REAL (não montagem sintética) e o comportamento saiu correto
nos dois casos, então a causa real ainda não foi encontrada.

**A — Corpo de player/bot morto continua com colisão.** `TileValidationSystem.
update()` (`engine/world_systems.py`, varredura que popula `self._occupied`)
e `EnemyAISystem._get_occupied_tiles` (mesmo arquivo, usado como
`dynamic_obstacles` do pathfinding de minion) marcavam qualquer entidade
com `TileMovement` como ocupando seu tile, sem checar `GhostState.is_dead`.
Morte de MOB remove a entidade (`world.remove_entity`), então nunca teve
esse problema; morte de player/bot só marca `GhostState` — a entidade
(e o `TileMovement`) continuam vivos pra sempre no tile de morte até o
revive. Fix: pular entidade com `GhostState.is_dead=True` nos dois
pontos de varredura, mesmo padrão que `Harvestable.solid` já usa ali do
lado (não bloqueia se não é sólido).

**Follow-up (19/09/2026, mesmo dia — usuário testou e corpo de BOT
continuava sólido):** o fix acima só cobria `GhostState`, que — mesma
causa raiz do item B abaixo — **só existe no player LOCAL do cliente**
(`client/network_handlers.py` só anexa em `self.player_entity`).
`TileValidationSystem.update()` é código COMPARTILHADO: no cliente, ele
também valida a própria predição de movimento local contra o corpo de
um player/bot REMOTO — que nunca tem `GhostState`, só
`RemoteControlled.hp<=0` (mesmo sinal já usado em B). Sem esse segundo
sinal, o corpo de qualquer um que não seja o PRÓPRIO jogador continuava
sólido pro cliente, mesmo já corrigido no servidor. Fix: `update()`
agora pula a entidade também quando `RemoteControlled.hp<=0` (nunca
existe no servidor — autoritativo, sem "entidade remota" — então não
muda nada lá, só corrige o lado cliente).

**Follow-up 2 (mesmo dia — usuário testou de novo: corpo de bot
desenhava NA FRENTE de quem andava por cima, "como se eu passasse por
trás dele").** Y-sort de corpo de player/bot (`ui/render_system.py`,
variável `foot_y` no loop de coleta de drawables) usava a mesma âncora
de personagem EM PÉ (`CHAR_COLLISION_SIZE/2=16`) que um personagem vivo
no mesmo tile — empatava o `sort_y` com quem estava andando por cima,
decidido por ordem de iteração em vez de "quem está de fato na
frente". Corpo de MOB nunca teve esse problema (nunca resolve
`class_id`, sempre cai no branch `renderable.height/2=12`, mais "atrás"
que o personagem vivo por construção). Pedido explícito do usuário:
corpo de player/bot deve ordenar IGUAL a corpo de mob. Fix nº1: usar
`renderable.height/2` pra corpo (via `_is_corpse_rnd`) em vez da âncora
de personagem — só que essa exceção dependia de `_is_corpse_rnd`, que
**também** só reconhecia `GhostState` (3ª ocorrência do MESMO gap
GhostState-só-local nesta sessão — itens A e B acima tiveram o mesmo
bug antes). Fix nº2 (correção do fix nº1): `_is_corpse_rnd`, calculado
na 1ª passagem do loop (coleta/culling/Y-sort), agora também reconhece
`RemoteControlled.hp<=0` — mesmo sinal que `_is_corpse_draw` (2ª
passagem, desenho) já tinha ganhado no item B. **Lição registrada**:
qualquer sinal novo de "está morto" adicionado a este arquivo precisa
ser conferido nas DUAS passagens do loop (coleta E desenho computam
`_is_corpse_*`/`_is_ghost_*` de forma independente, não compartilham a
mesma variável) — não é o mesmo bug reaparecendo, é o mesmo
BUG-CLASSE (checagem de morte que esquece o caso remoto) tendo 3 pontos
de aplicação diferentes que precisavam da mesma correção cada um.

**Follow-up 3 (mesmo dia — usuário testou de novo: ordem já saía
certa parado em cima do corpo, mas piscava invertida por um instante ao
ANDAR por cima).** O fix nº1 do follow-up 2 (`renderable.height/2=12`
pro corpo) só tinha 4px de folga contra o personagem vivo
(`CHAR_COLLISION_SIZE/2=16`) — muito menor que os 32px de um tile: a
travessia a pé por cima do corpo faz o `position.y` real do personagem
cruzar essa margem de 4px, invertendo a ordem por um instante. Usuário
esclareceu a intenção: corpo é CHÃO, não personagem — não devia
competir por Y-sort contra quem anda por cima, devia estar SEMPRE
atrás, não só na maior parte do tempo. Fix: em vez de somar uma âncora
ao `position.y` do corpo (competindo com o `position.y` de quem está
por cima), agora SUBTRAI meio tile (`position.y - TILE_SIZE/2`) —
garante `foot_y` do corpo menor que o de QUALQUER personagem vivo em
QUALQUER ponto do mesmo tile (pior caso: personagem na borda norte do
tile, `foot_y` = `position.y_do_corpo`, ainda maior que
`position.y_do_corpo - TILE_SIZE/2`). Escopo de 1 tile — não muda
ordenação contra árvore/objeto 2+ tiles de distância. Corpo de MOB não
passa por este branch (nunca resolve `class_id`), comportamento
intocado — só corpo de player/bot ganhou o tratamento "chão, sempre
atrás".

**B — Guerreiro morto (remoto/bot) continua na animação "idle".**
`engine/character_animation.py::resolve_display_state` nunca teve
noção de morte — resolve pra "idle"/"run" pela `TileMovement` sozinha.
O `RenderSystem` (`ui/render_system.py`) já tinha um `elif _is_corpse_draw`
que cai pro retângulo cinza — mas `_is_corpse_draw` vinha só de
`GhostState`, que **só existe no player LOCAL** (`client/network_handlers.py`
só anexa em `self.player_entity`). Player/bot REMOTO nunca tem
`GhostState` — "morto" pra eles é `RemoteControlled.hp<=0` (mesmo sinal
que `client/remote_entity_handlers.py` já usa pra esconder a barra de
HP do nameplate remoto), sinal que o branch de corpo/animação nunca
consultava. Fix: `_is_corpse_draw` (`ui/render_system.py`) agora também
é `True` quando `RemoteControlled.hp<=0` — sem introduzir detecção
nova, só estendendo o ponto único já existente pro caso remoto que
faltava.

**C — Transparência de bush só funciona por acidente, nunca com a
animação.** O branch de `is_bush_hidden` (`ui/render_system.py`,
01/09/2026) era um `elif` irmão do branch de animação de personagem
(09/09/2026) — desenhava sempre um retângulo semi-transparente,
NUNCA chamava `get_character_frame`, pra qualquer estado (parado,
andando, atacando). Os dois recursos foram implementados 8 dias
separados e nunca integrados; o "funciona ao atacar" que o usuário
percebeu é efeito colateral: atacar quebra o próprio stealth
(`is_bush_hidden` vira `False`), então nesse instante o código cai no
branch normal (COM animação, mas SEM transparência) — parecia
"funcionar" só porque parou de ser bush hidden. Fix: bush deixou de
ser `elif` separado — dentro do branch de animação, se
`is_bush_hidden` e existe `_char_frame`, aplica `set_alpha(127)` numa
**cópia** do frame (nunca no objeto original — `get_character_frame`
devolve Surface CACHEADA/compartilhada entre qualquer entidade/frame
igual; mutar sem copy() vazaria a opacidade pra próximo uso do mesmo
frame) antes de desenhar; sem frame disponível, cai no mesmo retângulo
translúcido de sempre (fallback preservado).

**D — Bot tratando a própria torre como inimiga (EM ABERTO, não
reproduzido).** Sintoma relatado: jogador ataca a torre do TIME de um
bot (torre aliada ao bot); bot fica parado numa distância fixa fora do
alcance da torre, nunca se aproxima — usuário descreve como idêntico
ao comportamento correto de recusar entrar no alcance da torre INIMIGA
ao perseguir o jogador. Mecanismo suspeito óbvio: `BotAISystem.
_dive_allowed`/`_enemy_towers_covering` (`server/bot_ai_system.py`/
`server/world_server.py`) — já filtra explicitamente `fac.faction_id
== my_faction_id` (torre da MESMA facção nunca conta como "inimiga"),
e isso é coberto por `TestBotDiveGate` (`tests/test_bg_bot.py`).
Reproduzido 2x sem sucesso:
1. Montagem sintética (bot+jogador+torre da mesma facção do bot criados
   na mão) — bot se aproximou normalmente, `dist` caiu de 6 pra 1 em 5
   ticks.
2. Pipeline REAL completo (`_tick_bg_queue` → `request_bg_accept` real +
   bots, instância `moba_battleground.csv` carregada de verdade,
   jogador teleportado pra dentro do alcance do NEXUS do time
   inimigo-só-bot) — os 2 bots do time inimigo se aproximaram e
   chegaram a distância 1 (adjacente) em 10 ticks de decisão.

Confirmado com o usuário: o teste real foi contra a fila de BG de
verdade (não o enxame de bots de carga `tools/loadtest/`, que tem
regra deliberada de nunca mirar no jogador real — se fosse esse caso,
não seria bug). Sem reprodução, nenhum fix foi tentado — próximo passo
depende de dado adicional (log do servidor no momento exato do
incidente — `server/spell_debug_log.py`/log padrão já mostra facção e
posição —, ou uma descrição mais precisa de qual torre/lane e se havia
outro combate acontecendo ao mesmo tempo).

**Teste**: A/B/C — `pytest tests/ -k "occupied or tile_walkable or ghost
or corpse or minion_pathfind or character_animation or render_system"`
(100 passed; as 4 falhas na mesma rodada são `test_session.py`,
dependentes de Postgres via Docker, que estava parado — não regressão
desta mudança). Sem teste automatizado novo dedicado (nenhum dos 3
pontos está no catálogo de "pontos únicos de verdade" que exige teste
sempre) — validação em jogo fica com o usuário.

---

## 46. Investigação — "Interceptar" ficava sempre rejeitado numa partida de BG (19/09/2026, NÃO é bug de código)

**Sintoma relatado:** usuário jogando guerreiro numa partida de BG, ao
alcançar nível 3 de instância (subindo via jungle), toda tentativa de
usar Interceptar retornava "Skill não aprendida" — e continuou assim
pelo resto da partida, mesmo com mais levels depois.

**Investigação:** `is_skill_authorized()` (`engine/world_systems.py`)
rejeita com essa mensagem quando `sid not in PlayerSkills.
learned_skill_ids`. Reproduzido 2x com o mecanismo REAL de concessão
(`server/instance_progression.py::_grant_instance_skill`/
`_process_instance_levelup`, chamado por `grant_instance_xp`) — uma vez
chamando a função direto, outra simulando uma morte de mob de JUNGLE de
verdade (o caminho específico que o usuário apontou como suspeito) — e
nos dois casos Interceptar desbloqueou certinho no nível 2 (índice 1 de
`INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"]`) e continuou autorizado daí
em diante. Nenhum código encontrado em lugar nenhum que remova algo de
`learned_skill_ids` depois de concedido (só `.add()`, nunca
`.discard()`/reatribuição do set) — `HOTBAR_UPDATE`
(`server/session.py::_handle_hotbar_update`) mexe só em `ps.skills`
(posição visual), nunca em `learned_skill_ids`.

**Causa raiz real, achada no log de produção (`logs/server.log` +
`journalctl` do Hetzner, timestamps completos):** o incidente
(rejeições de `interceptar player=255` entre 19/09 00:07:44 e
00:14:08) caiu bem no meio de uma janela em que o SERVIDOR foi
reiniciado 4 vezes (19/09 22:56:38, 23:15:15, 23:33:48, 23:59:36) por
causa dos deploys de `release_tools/deploy_server.ps1` rodados a cada
correção de bug DIFERENTE nessa mesma sessão (item #186, iterações do
Y-sort de corpo). Cada restart derruba a conexão do jogador; o cliente
reconecta sozinho rápido o bastante pra passar despercebido, mas o
servidor carrega uma instância de BG NOVA a cada reconexão
(`carregando mapa: moba_battleground.csv (instância ...::bg_1)` se
repete no log a cada vez) — resetando a progressão normalizada
(nível/skills da instância) do zero. Resultado: o jogador via "cheguei
no nível 3" em uma tentativa, o restart apagava esse progresso sem
aviso, e a tentativa seguinte de usar Interceptar caía numa instância
ainda no nível 1 de verdade — sem NENHUM bug no mecanismo de
desbloqueio em si.

**Não é código — é processo.** Ver `[[feedback-auto-deploy-after-fix]]`
(memória): daqui pra frente, deploy durante uma sessão de teste ATIVA
do usuário é anunciado/combinado antes, não automático a cada commit —
só volta a ser automático quando ele confirma que não está testando ao
vivo naquele momento.

---

## 47. Clamp de câmera não aparecia na tela de jogo + clique no NPC da loja parou de funcionar — 2 duplicatas esquecidas de `get_camera_offset()` (20/09/2026)

Sequência de 2 bugs na mesma sessão do item 190, mesma causa raiz de
fundo: cópias locais da fórmula de offset de câmera que nunca foram
migradas pro ponto único (`engine/utils.py::get_camera_offset()`,
consolidado 15/09/2026) continuavam vivas em outros arquivos, cada uma
descoberta só depois que ALGUMA outra parte do código já usava a versão
nova (com clamp).

**A — `game.py`, loop de render principal.** O clamp do item 190 foi
implementado e testado corretamente em `get_camera_offset()`, mas o
usuário reportou via print que a câmera continuava mostrando vazio além
da borda do mapa. Causa: o loop de render de verdade em `game.py` nunca
chamava essa função — tinha sua PRÓPRIA cópia inline
(`cam_x = (camera_pos.x - lw/2) if camera_pos else 0`, calculada direto
de `Position` do `camera_entity`) que nunca foi atualizada quando a
função compartilhada ganhou o clamp. Fix: substituída pela chamada real
a `get_camera_offset(self.world, lw, lh)`, removendo o fetch de
`camera_pos` que só servia pra essa conta.

**B — `ui/shop_system.py`/`ui/quest_system.py`, clique direito em NPC.**
Assim que o fix do item A entrou em produção, o usuário reportou que o
clique direito no NPC Mercador da BG parou de funcionar por completo —
nem o "andar até o NPC" acontecia, em qualquer modo de câmera
(travada/livre). Causa: `ShopSystem._get_cam()`
(`ui/shop_system.py`) e `QuestDialogSystem._get_cam()`
(`ui/quest_system.py`) tinham cada uma sua PRÓPRIA cópia idêntica da
fórmula antiga (uma comentava explicitamente "ver ShopSystem._get_cam()",
confirmando que foi copiada de propósito em vez de reusar a função).
Convertiam clique de tela pra posição de mundo usando o offset SEM
clamp, enquanto o NPC já era desenhado usando o offset COM clamp (item
A) — perto da borda do mapa os dois offsets divergem, o clique erra o
retângulo do NPC por completo (`abs(wx - pos.x) <= hw` nunca bate). Fix:
as duas passam a delegar pra `get_camera_offset()`, igual
`LootSystem`/`PlayerInputSystem`/`SpellCastSystem`/`SkillHandlers`/
`CraftingSystem`/`TrainerSystem` já faziam.

**Lição:** um "ponto único de verdade" só protege contra esta classe de
bug enquanto TODOS os consumidores de fato chamam ele — uma cópia local
esquecida (mesmo pequena, mesmo já catalogada como "13+ pontos
consolidados" antes) fica invisível até o dia em que o comportamento da
função central muda e a cópia não acompanha. Antes de mudar o
comportamento de uma função "ponto único" já consolidada (não só ao
criá-la), vale grep por fórmulas equivalentes reimplementadas
(`cam_pos.x -`, `- SW / 2`, `- SH / 2`, `_get_cam`) em vez de assumir
que a consolidação anterior pegou 100% dos casos.

**Teste**: novo `test_shop_click_direito_acerta_npc_perto_da_borda_do_mapa`
(`tests/test_client_ui.py`) — reproduz o cenário exato relatado (câmera
perto do canto inferior-direito de um mapa 1600×1600px, clique na
posição de tela onde o offset CLAMPADO desenha o NPC) e confirma que o
clique é reconhecido. Revert-to-confirm: com `ShopSystem._get_cam()`
revertida pra fórmula antiga, o teste falha exatamente no ponto
esperado (`_right_click_consumed is False`) — confirma que o teste
prova o bug relatado, não só um cenário sintético qualquer. Suíte
completa (`pytest tests/ -q`, 1750 passed, 1 falha pré-existente e não
relacionada em `test_perf_log_improvements.py` — timing flaky de
`time.sleep(0.01)` medindo 0.009956s, tolerância de relógio do SO, não
regressão desta mudança).

---

## 48. Câmera livre (tecla Y, BG) só reagia ao cursor na borda depois de reselecionar "tela cheia" — `hud_surf` nunca inicializado no boot (21/09/2026)

**Sintoma relatado pelo usuário:** dentro de uma partida de BG, empurrar a
câmera livre (item 190/B) com o cursor na borda da janela só funcionava
depois de abrir Configurações > Janela e escolher "Tela cheia" — mesmo já
estando em tela cheia (reselecionar a MESMA opção já destravava).

**Causa raiz:** `engine/world_systems.py::System.hud_surf` é atributo de
CLASSE, default `None` — cada `System` (inclusive `CameraSystem`) só recebe
um `hud_surf` de verdade via `GameEngine._rebuild_screen_refs()`
(`game.py`), a única função que atribui esse campo (confirmado pela
própria docstring dela, escrita em 24/07/2026: "`world_surf` escapa por
acaso [reatribuído todo frame]; `hud_surf` não tem esse refresh por frame
em lugar nenhum — só aqui"). Essa função só é chamada a partir de
`_apply_window_mode()`/`_apply_scale()` — ou seja, só quando o jogador
troca de modo de janela ou escala PELO MENU, em tempo real. No boot,
`_compute_and_set_window_mode()` também é chamado (pra aplicar
`window_mode` salvo), mas nesse ponto do `__init__` `self.world` ainda não
existe — o guard `if hasattr(self, "world")` pula `_rebuild_screen_refs()`
de propósito (comentário already existente explica por quê: os sistemas
que ela atualiza ainda não foram criados). Resultado: `hud_surf` de TODOS
os `System` (não só `CameraSystem`) ficava `None` a sessão inteira, até o
usuário mexer no menu Janela pela 1ª vez.

Isso nunca importou pros outros consumidores de `hud_surf`
(`ShopSystem`/`QuestDialogSystem`/etc.) porque todos eles só o leem DENTRO
de um handler de clique, e ou têm fallback pra `world_surf` (que É
reatribuído todo frame por `_assign_world_surf`) ou só importam no exato
frame de um evento de mouse (praticamente nunca o 1º frame). Mas
`CameraSystem._pan_free_camera()` (item 190) lê `hud_surf` DIRETO, sem
fallback — precisa da resolução REAL da janela, não a escalada por zoom de
`world_surf` — e faz no-op silencioso quando `None` (`if self.hud_surf is
None or dt <= 0: return`). Câmera livre nasce funcionalmente "morta" até
o gatilho acidental do menu Janela ligar tudo pela 1ª vez.

**Fix:** `GameEngine.__init__` passa a chamar `self._rebuild_screen_refs(
self.screen)` uma vez, logo após montar `self.systems` (mesmo ponto onde
um comentário PRÉ-EXISTENTE já afirmava, incorretamente, que "hud_surf é
inicializado em todos os sistemas" — a inicialização real nunca tinha sido
escrita). Nesse ponto do `__init__`, `self.world`/`self.systems`/
`self._map_overlay`/`self._minimap`/`self._god_mode` (tudo que
`_rebuild_screen_refs` toca) já existe — chamada seguível sem duplicar
lógica, só disparando o ponto único já existente 1x a mais cedo do que
antes.

**Teste**: sem teste automatizado novo — instanciar `GameEngine` completo
em teste headless exige rede/servidor (nenhum precedente no projeto faz
isso; os testes de câmera livre já existentes em `test_client_ui.py`
constroem `CameraSystem` isolado e injetam `hud_surf` manualmente, então
não cobrem — e não cobriam — esta lacuna de wiring do boot). Validação
fica com o usuário, em jogo, entrando numa BG SEM tocar no menu Janela e
testando a câmera livre direto.

---

## 49. Bug real — aba nova do F12 estourou a largura do painel, 1ª e última aba cortadas (21/09/2026)

**Sintoma relatado (com print):** depois da aba "Dia/Noite" (§196), a
1ª aba ("Nivel") e a última ("Dia/Noite") apareciam cortadas pra fora
do modal, dos dois lados.

**Causa raiz:** `_draw_debug_modal()` (`client/debug_handlers.py`)
calculava a largura de cada aba como uma constante FIXA
(`tab_w = self._u(160)`), nunca dividida pelo número de abas. Com as 4
abas originais (`4*160 + 3*10 = 670px`) cabia dentro do painel
(`DEBUG_W = 820px`) por acaso — sobrava até margem. A 5ª aba
("Dia/Noite") empurrou o total pra `5*160 + 4*10 = 840px`, MAIOR que o
painel inteiro; como a fileira de abas é centralizada
(`tx0 = px + PW//2 - tabs_total_w//2`), o excesso "vazou" pros 2 lados
igualmente — 1ª aba cortada à esquerda, última cortada à direita,
exatamente o que o print mostrou.

**Fix:** `tab_w` passa a ser CALCULADA a partir de `PW`/número de abas
(`(PW - 2*margem - (n-1)*gap) // n`), nunca mais um número fixo — se uma
6ª aba for adicionada no futuro, a largura de cada uma encolhe
automaticamente pra continuar cabendo, sem precisar lembrar de ajustar
esta conta na mão de novo. Verificado por script (`PW=820`, 5 abas):
`tab_w=148px`, fileira total `780px`, margem de 20px sobrando em cada
ponta — cabe com folga. Rótulo mais longo ("Dia/Noite", 9 caracteres)
mede 72px de largura renderizado (fonte `FONT_MD=28`) — bem menor que
os 148px disponíveis por aba, sem risco de o texto também estourar o
botão.

**Teste**: sem teste automatizado novo (mesmo caso de §195/§196 — UI de
debug do F12 não tem precedente de teste dedicado no projeto, validada
manualmente pelo usuário). Verificação por script headless confirmando
a matemática do layout (`tx0 >= 0` e `tx0 + tabs_total_w <= PW`) antes
de aplicar.

---

## 50. Bug real — F11 crashava o jogo com `UnboundLocalError: _ts` (22/09/2026, pré-existente, não relacionado às mudanças desta sessão)

**Sintoma relatado (com traceback):** apertar F11 em jogo crashava com
`UnboundLocalError: local variable '_ts' referenced before assignment`
em `game.py:2167` (`self._prof_record("input_dispatch", ...)`).

**Causa raiz:** o bloco que mede o tempo do dispatch de input inteiro
(`game.py`, instrumentado em 15/09/2026, item #143) tinha essa forma:

```python
if PROFILE_FRAMES:              # só captura SE já estava ligado
    _ts = _time.perf_counter()

for event in events:            # ... e AQUI DENTRO, o handler de F11:
    ...
    elif event.key == pygame.K_F11:
        globals()["PROFILE_FRAMES"] = self._show_perf_overlay

...
if PROFILE_FRAMES:              # se ligou DURANTE o loop acima, lê um
    self._prof_record(..., _time.perf_counter() - _ts)   # _ts que nunca existiu
```

F11 (a MESMA tecla que liga/desliga `PROFILE_FRAMES`) é processada
DENTRO do próprio bloco de eventos que este trecho mede. Apertar F11
pra LIGAR o overlay com ele desligado no início do frame faz a
checagem de fechamento ver `PROFILE_FRAMES=True` e tentar ler um `_ts`
que nunca foi capturado nesta iteração — 100% determinístico, não é
uma corrida rara (acontece toda vez que se liga o overlay, nunca
quando já está ligado ou ao desligar).

**Não é bug desta sessão** — o bloco existe desde 15/09/2026 (§143),
nenhuma mudança recente desta sessão tocou nele; só apareceu agora
porque o usuário reativou o overlay de performance pela 1ª vez desde
então.

**Fix:** `_ts = _time.perf_counter()` passa a ser capturado SEMPRE
(fora do `if PROFILE_FRAMES:`) — custo de `perf_counter()` sozinho é
irrelevante (nanosegundos, nada a ver com o custo real de
`_prof_record()`, que continua condicional). Mesma classe de bug
(variável só existe dentro de 1 branch de um `if`, lida fora dele
achando que as 2 checagens do MESMO flag sempre concordam) — se outro
par capture/leitura condicional aparecer no futuro em código que
processa o PRÓPRIO evento que muda a condição no meio, aplicar o mesmo
princípio: captura incondicional, só a ação cara fica condicional.

**Teste**: sem teste automatizado — reproduzir de verdade exige
`GameEngine.run()` completo com loop de eventos real (nenhum teste do
projeto instancia isso, mesmo caso já registrado em §192: pesado
demais, precisa de tela/rede). Validação fica com o usuário, apertando
F11 em jogo (o cenário exato relatado).

---

## 51. Bug real — AttributeError ao abrir o cliente, `_day_night_system` ainda não existia (23/09/2026, dentro de 1h da §206)

Usuário reportou traceback real ao abrir o jogo:
```
File "game.py", line 802, in _load_ambient_zones
    self._day_night_system.map_affected     = self._day_night_affected
AttributeError: 'GameEngine' object has no attribute '_day_night_system'
```

Causa raiz: `GameEngine.__init__()` chama `_load_map_and_entities()`
(que chama `_load_ambient_zones()`) ANTES de construir
`self._day_night_system`/`self._day_night_clock_hud` — os dois
precisam de `self.player_entity`, que só existe depois do mapa carregar
(ordem do `__init__` não é invertível por esse motivo). Erro de
verificação da §206: eu tinha CONFIRMADO a ordem lendo o código
(`self._day_night_system = DayNightSystem(...)` na linha que LI como
"~481", contra a chamada de `_load_ambient_zones` na linha "~697" que
eu tinha visto — 481 < 697, parecia seguro) mas a leitura foi feita
ANTES de várias edições da mesma sessão terem deslocado os números de
linha reais; a ordem de EXECUÇÃO nunca foi essa — `_load_map_and_entities()`
é chamada na linha 479 do `__init__`, e a construção dos 2 sistemas só
vem depois, na 486-489. Verificar "linha X < linha Y" num arquivo que
está sendo editado na mesma sessão não é o mesmo que verificar ordem de
CHAMADA real — lição pra qualquer wiring futuro em `__init__`: seguir o
fluxo de execução (quem chama quem, nessa ordem), nunca comparar
posição de linha isolada.

**Fix**: `_load_ambient_zones()` passa a usar `getattr(self,
"_day_night_system", None)`/mesma coisa pro clock hud antes de setar
`map_affected` — 1ª chamada (mapa inicial, sistemas ainda não existem)
não crasha mais (nada a propagar ainda de qualquer forma — os 2 objetos
nascem com `map_affected=True`, igual ao default). Logo depois de
`self._day_night_system`/`self._day_night_clock_hud` serem construídos
no `__init__`, o valor já calculado por `self._day_night_affected` é
reaplicado nos dois — cobre o caso (hoje inexistente, spawn inicial
sempre é `map_1`, mundo aberto) de o mapa inicial vir a ser fechado no
futuro.

**Reprodução do cenário real, não sintética**: teste novo chama
`game.GameEngine._load_ambient_zones` (unbound, método real da classe)
numa Stub sem os 2 atributos, na MESMA ordem de chamada do `__init__`
real — revert-to-confirm confirmou que, sem o fix, o teste falha com o
MESMO `AttributeError` e a MESMA linha do traceback relatado pelo
usuário (não um cenário inventado). Lançar o cliente de ponta a ponta
pra confirmar visualmente não foi possível nesta sessão (exige servidor
rodando + login/seleção de personagem antes de `GameEngine.__init__`
sequer começar) — a reprodução via unbound method já bate 1:1 com o
traceback relatado, mas o usuário ainda precisa confirmar que o jogo
abre normal na próxima tentativa.

**Teste**: `tests/test_client_ui.py::
test_load_ambient_zones_nao_crasha_antes_dos_sistemas_de_dia_noite_existirem`.
Suíte completa: 207 passed em `tests/test_client_ui.py`.

## 52. Bug real — mapa fechado (`day_night_affected: false`) ficava sempre no brilho MÁXIMO, não escuro (23/09/2026, dentro de 1h da §206/§207)

Usuário testou a §206 (mapa fechado fora do ciclo) e reportou: "quando
'day_night_affected': false, o modo default do mapa deve ser noite e
não dia, pois ambientes fechados como cavernas e dungeons são escuras".

Causa raiz: `DayNightSystem.render()` (§206) tratava `map_affected ==
False` como "desliga o efeito inteiro" (`return` antecipado). Como a
técnica de tingimento é MULTIPLICAÇÃO (§203) — onde "sem efeito" =
multiplicar por branco = brilho ORIGINAL, o mais claro possível — a 1ª
versão produzia exatamente o OPOSTO do pedido: a caverna sempre no
brilho máximo (como se fosse sempre meio-dia), nunca escurecida.

**Fix**: `map_affected == False` deixou de ser um `return` antecipado —
agora usa a âncora `_night_color` (mesma cor editável no F12, "Noite")
como tingimento FIXO, ignorando por completo `day_night_color()`
(relógio real) — a caverna nunca "amanhece". A luz de lampião
(`is_night()`, gate binário) também passou a considerar mapa fechado
como "sempre noite" (`is_night() or not self.map_affected`) — sem essa
parte, um lampião numa dungeon nunca acenderia durante o "dia" de fora,
deixando a caverna escura pra sempre sem nenhuma luz funcionando
(nenhum objeto desse tipo existe nas 2 cavernas atuais, mas o corte já
nasce certo pra quando existir — confirmado por grep no `object_matrix`
das duas, nenhum tile de objeto presente hoje).

`DayNightClockHud` NÃO mudou — continua escondendo o relógio inteiro em
mapa fechado (não fazia parte do reporte do usuário, e não faz sentido
mostrar um indicador de "hora do dia" num lugar onde não existe
variação nenhuma).

**Achado no processo de escrever o teste**: `pygame.BLEND_RGBA_MULT`
NÃO é `round(a*b/255)` (1ª tentativa de calcular o pixel esperado na
mão bateu 29 vs 28 num canal) — mesma classe de "verificar contra
comportamento real do pygame, nunca assumir a fórmula" já documentada
em §203. Corrigido calculando o valor esperado com um blit de
referência de verdade (1×1 Surface, mesmo `BLEND_RGBA_MULT`) em vez de
reimplementar a fórmula em Python.

**Teste**: `tests/test_client_ui.py::
test_day_night_render_mapa_fechado_fica_sempre_escuro` (mocka
`day_night_color()` pra "dia pleno" e confirma que o resultado AINDA é
escuro — prova que o mapa fechado nem consulta o relógio real) +
`test_day_night_render_mapa_fechado_lampiao_acende_mesmo_fora_da_noite_real`
(mocka `is_night()` pra `False` e confirma que o lampião acende assim
mesmo). Revert-to-confirm feito nos dois antes de fechar — ambos
falham sem o fix, reproduzindo exatamente o comportamento errado
relatado. Rodados só os testes relacionados (day/night + map_loader +
wiring), 22 passed — suíte completa E validação manual em jogo ainda
pendentes por decisão do usuário (novo fluxo combinado 23/09/2026: só
teste direcionado + validação manual antes de rodar a suíte inteira).

## 53. Mob perseguindo atravessa o mapa inteiro depois que o player teleporta pra outro ponto do MESMO mapa (27/09/2026)

Bug real relatado pelo usuário: agroou os zumbis do cemitério
(`map_1`, spawn_zone em `(130,374)`) e, ainda sendo perseguido, entrou
num portal que teleporta pra outro canto do MESMO mapa (`(138,370)` →
`(210,480)`, `maps/map_1_entities.json::transitions`). Ao voltar pro
cemitério, os mobs estavam "numa área diferente do mapa" — não no
lugar de onde saíram, nem no spawn — e só quando o usuário se
aproximou do respawn deles é que "corrigiram" a posição, como se o
servidor os tivesse puxado de volta.

**Causa raiz**: `WorldServer.transfer_player()` só reescreve a posição
do player (`snap_to_tile`) — nenhum mob que o perseguia é avisado. O
mob continua com `AIControlled.target_eid` válido e, no próximo tick,
tenta pathfindar até a posição NOVA do alvo, por mais longe que
esteja — porque o "leash" (`EnemyAISystem.update()`, linha ~3853) só
mede a distância do MOB até o PRÓPRIO spawn, nunca até o alvo
perseguido. Resultado real: o mob percorre boa parte do mapa
perseguindo antes que essa distância-do-spawn-dele finalmente passe
de `MAX_LEASH_RADIUS` (20 tiles) e dispare o retorno — o "puxão de
volta" que o usuário viu é só o leash disparando tarde, depois de um
trajeto real e visível.

**Achado relacionado, fora de escopo desta correção**: se o portal
levasse pra um mapa DIFERENTE (não o caso confirmado aqui), existe um
2º problema: quando nenhum player está presente num mapa,
`EnemyAISystem.update()` (linha ~3174) força TODO mob daquele mapa
pro estado `IDLE` na posição atual, pulando o corpo do loop onde o
leash é calculado — o mob fica congelado longe do spawn, sem chance
de se auto-corrigir, até um player aparecer de novo naquele mapa. A
correção abaixo (hook em `transfer_player`) já cobre esse caso também,
de graça, porque limpa o alvo do mob ANTES dele ter chance de ficar
congelado em CHASING/ATTACKING.

**Fix**: `engine.core_systems.force_mob_give_up_target(world, mob_eid,
target_eid)` — extrai a transição CHASING/ATTACKING→RETURNING que o
leash normal já fazia inline (5 campos: `state`, `aggroed_by_damage`,
`path_recalc_timer`, `target_eid`, `tile_movement.path`) pra 1 dono
só; no-op se o mob não estiver perseguindo ESTE alvo específico.
`EnemyAISystem.update()` (leash por distância acumulada) e
`WorldServer.transfer_player()` (perda ABRUPTA de alvo, na hora do
teleporte) chamam a MESMA função agora. `transfer_player()` captura o
mapa de ORIGEM do player (antes de sobrescrever `MapLocation.map_file`)
e varre mobs desse mapa com `target_eid` igual ao player que teleportou
— evento raro (1 por teleporte), sem necessidade de índice espacial.
Cobre teleporte pro MESMO mapa (portal, caso confirmado) e pra outro
mapa (ZONE_CHANGE) igual, já que os dois passam pelo mesmo ponto único.

**Testes** (`tests/test_teleport_breaks_mob_chase.py`): unitário direto
de `force_mob_give_up_target` (transição real + 3 casos de no-op:
alvo errado, mob já IDLE, e — cobertura de simetria — funciona em
ATTACKING também) + integração real com `WorldServer.transfer_player`
(objeto de produção real, não fixture com o método colado): mob
perseguindo desiste tanto no teleporte pro mesmo mapa quanto pra outro
mapa; mob perseguindo OUTRO alvo não é afetado; mob em mapa diferente
do player não é afetado; mob IDLE não é afetado. Revert-to-confirm: os
2 testes de integração que exercitam o caminho novo falham sem a
chamada (mob continua `CHASING` em vez de `RETURNING`).

**Achado de processo, registrado por transparência**: a 1ª rodada de
verificação (suíte completa de `tests/test_session.py`, banco Postgres
real) mostrou 79 testes falhando — muito além do esperado, incluindo
classes sem relação nenhuma com o fix (`TestRateLimit`,
`TestAllyVisionSharing`, `TestItemGrantsQuestM4`). Antes de aceitar
isso como regressão real do fix, isolei a variável: rodei a MESMA
suíte, sozinha, sem nenhum outro processo `pytest` concorrente — 108/108
passando, no mesmo tempo do baseline sem o fix. A causa real foi eu
mesmo ter disparado várias suítes pesadas de Postgres EM PARALELO
(várias chamadas em background quase simultâneas durante a
investigação), esgotando `max_connections` do banco — nada a ver com o
código do fix. Repetido 2x limpo pra confirmar antes de comitar. Lição:
nunca correr para declarar "regressão" a partir de uma suíte que
compartilha um recurso finito (conexão de banco) com outros processos
rodando ao mesmo tempo — isolar antes de concluir.

## 54. Correção do §212 não resolveu de verdade — mob "congelado" na tela era um bug de sincronização AOI, não de IA (28/09/2026)

Usuário testou o fix do §212 (mob desiste de perseguir quando o alvo
teleporta) e relatou: "não resolveu, ainda acontece exatamente a mesma
coisa" — com uma teoria própria e correta na direção: o cliente
guardava a última posição conhecida dos mobs, e parecia que o
servidor "corrigia" a posição só quando o player se aproximava do
respawn de novo.

**Por que o §212 não bastava**: aquele fix arruma o COMPORTAMENTO do
mob no servidor (ele realmente volta pro spawn mais rápido), mas
nunca tocava em COMO o cliente fica sabendo disso. Investigação (com
teste de integração real, que FALHOU antes de eu entender o problema —
nunca confiar só na teoria) achou 2 causas reais, que se combinavam:

1. **Cliente**: `game.py::_do_transition()` tem um atalho pra "morte no
   mesmo mapa: apenas reposiciona o player, mobs continuam vivos" —
   ativado sempre que `target_file == self._current_map_file`, o que
   cobre TANTO respawn de morte (curto, mobs ainda relevantes, atalho
   correto) QUANTO um portal de mundo aberto que teleporta pra outro
   canto do MESMO arquivo de mapa (nosso caso — nunca limpa/atualiza
   `_remote_mobs` nem nenhuma entidade remota).
2. **Servidor**: `_handle_zone_change_req` (`server/session.py`) fazia
   `session.known_eids.clear()` depois de mandar `ZONE_CHANGE` —
   apagava o registro do SERVIDOR sobre "o que este player via", mas
   NUNCA mandava `ENTITY_DESPAWN`/despawn explícito pro cliente. Só
   "funciona" numa troca de mapa DE VERDADE porque aí o item 1 acima
   reconstrói tudo do cliente sozinho — pra teleporte no MESMO mapa,
   os dois lados ficavam mudos um pro outro: cliente nunca limpa
   (atalho), servidor nunca avisa (clear silencioso).
   Além disso, achei que o ÚNICO mecanismo que reavalia "o que eu
   ainda vejo" quando EU me movo (bloco de revalidação em
   `_build_update_for_session`, criado em §56 pra bush/stealth) só
   chamava `_can_see` (visibilidade — parede/bush/invisibilidade),
   NUNCA distância — um mob PARADO nunca é revalidado pelo loop
   principal (que só reage a quem SE MOVEU), então mesmo removendo o
   `clear()` acima, nada despawnava um mob simplesmente deixado muito
   longe. Esse buraco é mais geral que só teleporte (existe pra
   qualquer "eu me afasto de um mob parado"), só que invisível no dia
   a dia porque a maioria dos mobs anda/persegue/volta de tempos em
   tempos, gerando o próprio delta de "moved" que dispara a OUTRA
   checagem (a do loop principal, essa sim já olhava distância).

**Fix**: (a) `WorldServer.transfer_player()` ganhou
`emit_moved_delta: bool = False` (default preserva TODO outro
chamador — respawn/ghost documentou de propósito que não queria isso,
match start, debug) — só `_handle_zone_change_req` passa `True`,
registrando o teleporte em `_moved_this_tick` como um movimento normal
do próprio player. (b) Removido o `session.known_eids.clear()` cego —
a revalidação real (item seguinte) já cobre cross-map E same-map
igual, sem precisar de atalho manual. (c) O bloco de revalidação em
`_build_update_for_session` agora TAMBÉM checa `in_aoi_exit` (mesma
métrica de distância que o loop principal já usa), não só `_can_see` —
mob que ficou fora do raio manda despawn de verdade, mesmo parado.

**Testes** (`tests/test_session.py`):
`TestTransferPlayerEmitMovedDelta` (unitário, confirma o novo
parâmetro default False preserva comportamento antigo, True registra
o movimento certo) + `TestTeleportDespawnaMobJaConhecido` (integração
real — login, mob conhecido, teleporte pro MESMO mapa bem longe,
confirma despawn de verdade chega + `known_eids` perde o mob).
Revert-to-confirm feito em 2 PARTES separadas (cada metade da correção
tem seu próprio teste que falha isolado sem ela) — a 1ª tentativa de
revert-to-confirm já tinha pego que a checagem de distância sozinha
não bastava (o `clear()` antigo mascarava completamente, known_eids
já vinha vazio antes da revalidação nova rodar), o que levou a achar a
2ª causa. Suíte completa `test_session.py` (111 testes, rodada sozinha
— ver §209 sobre não rodar Postgres em paralelo) + suíte de IA de mob
(§212, 111 testes) passando.

**Lição de processo**: a 1ª correção (§212) resolvia uma causa real e
válida, mas não era a causa DOMINANTE do sintoma relatado — só fechar
o "1º achado plausível" sem testar contra o cenário exato relatado
(mesmo mapa, mob parado, distância grande) deixou passar 2 bugs
adicionais que juntos explicavam o comportamento por completo. O
usuário insistir em "ainda acontece exatamente a mesma coisa" em vez
de aceitar a explicação da 1ª rodada foi o que abriu a investigação
certa.

## 55. Minimapa pequeno reconvertia `explored` do zero a cada tile andado — cache existente só cobria o mapa grande (28/09/2026)

Pergunta do usuário sobre o sistema de dia/noite (§209-§211) levou a
instrumentar `DayNightSystem.render()` em 5 sub-labels — e os dados
reais mostraram que o maior custo não era o escurecimento de tela
(hipótese inicial), e sim o blit dos clusters de luz estática. Ao
pedir a lista de picos do log, o usuário notou que `rnd:minimap`
ainda era a maior causa recorrente (36 de 200 picos), mesmo já tendo
recebido um fix de cache nesta sessão (§209, item "hud:combat_log").

**Causa raiz**: aquele fix anterior só tocou `_rebuild_fullmap`
(mapa cheio, tela de instância). O minimapa PEQUENO (HUD, sempre
visível, `render()`/`_rebuild_numpy`) é um caminho de código
SEPARADO — ele reconvertia `explored` (set→numpy) do zero toda vez
que o player mudava de tile, via um set Python de ~2601 tuplas
(`window_tiles = {(x0+wx, y0+wy) for wx in range(win) for wy in
range(win)}`) + interseção com `explored`. Já era bounded (fix de
14/07/2026, O(win²) em vez de O(len(explored))), mas montar/hashear
milhares de tuplas por tile andado (quase todo frame, andando) ainda
pesava de verdade — confirmado por benchmark isolado: ~0.55ms/chamada
numa sessão bem explorada (20k tiles).

**Fix**: `_get_explored_arr(explored, cols, rows)` — extrai a
conversão explored→numpy (cacheada por `len(explored)`, que só
CRESCE) que já existia dentro de `_rebuild_fullmap` pra 1 método
compartilhado. `_rebuild_numpy` passa a chamar esse MESMO método em
vez de reimplementar via set, filtrando pra janela de RADIUS tiles
com máscara numpy (`x0<=x<x0+win` etc) em vez de set intersection.
`visible` (pequeno, muda quase todo tick) também parou de depender do
`window_tiles` removido — mesmo sem cache dedicado, ganhou de graça
por não precisar mais daquele set gigante. Benchmark: ~0.55ms →
~0.13ms por chamada (4x). Nenhuma mudança visual — mesmo fog, mesma
geometria.

**Testes** (`tests/test_client_ui.py`): spy real confirmando que
`_rebuild_numpy` CHAMA `_get_explored_arr` (não só compara o
resultado final, que bateria mesmo sem a chamada — mesma lição já
registrada em §209 sobre teste que passa mesmo com o fix revertido);
reconversão quando `explored` cresce; cross-check pixel a pixel
contra uma reimplementação isolada do comportamento antigo (set-
based), só pra provar equivalência visual. Revert-to-confirm: 2 dos 3
falham sem a chamada. Suíte completa `test_client_ui.py` (233 testes)
passando. 100% client-side, sem deploy no servidor.

## 56. Skill mudando de slot sozinha na BG mesmo com preferência salva — RESOLVIDO (28-29/09/2026)

Usuário relatou: dentro da BG, "Fatiador de Corpos" desbloqueou no
slot "5", depois mudou pra "F", só terminando no slot certo "T" —
mesma classe de sintoma já catalogada e supostamente corrigida em
§73/§74/§129 (skill nunca deveria se mexer depois de conhecida, e
deveria ir direto pro slot salvo em `skill_slot_prefs`). Confirmado
com o usuário: existe uma preferência REAL salva (`config.json` do
personagem "Xadread": `fatiador_de_corpos: 6`, que corresponde à tecla
"T" — confirmado lendo `characters.Xadread.hotbar.keybinds` no mesmo
arquivo, `116` = tecla "t"). Slot "5" = tecla `53`, que também é onde
`punho_no_queixo` tem preferência salva (coincidência ainda sem
explicação confirmada); slot "F" (`102`) não tem preferência de
ninguém.

**Investigação (sem reproduzir ainda)**: simulei a progressão nível a
nível usando os dados REAIS do personagem (as 9 preferências salvas
de verdade) contra o mecanismo atual (`ui/skill_slot_prefs.py::
preferred_slot_for_skill` + bloco `skills_hotbar` de
`client/network_handlers.py::_handle_msg_stats_update`), em 2 rodadas:
(1) progressão limpa, 1 skill por nível, ordem de
`INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"]`; (2) mesma coisa MAIS o
interloper "Punho no Queixo" entrando ad-hoc no array esparso do
servidor (mecanismo já catalogado em §74, `server/combat_processor.py`).
Nas duas simulações, `fatiador_de_corpos` foi direto pro slot 6 (T) na
primeira vez que apareceu, sem nunca passar por outro slot — o
mecanismo se comportou como projetado. 2 perguntas diretas ao usuário
descartaram as 2 hipóteses mais prováveis: não houve arrasto manual
na hotbar, e o level-up foi gradual (1 por vez), não em salto múltiplo.

**Sem conseguir reproduzir via leitura/simulação de código**, e
seguindo a regra do projeto de nunca declarar "corrigido" (ou, nesse
caso, "achei a causa") sem reproduzir o sintoma relatado de verdade —
adicionado `debug/hotbar_slot_debug.py` (mesmo padrão de
`debug/aoi_debug.py` — env var `RPG_DEBUG_HOTBAR_SLOT`, log em
`logs/hotbar_slot_debug.log`, no-op por default) instrumentando o
bloco `skills_hotbar`: loga o payload bruto do servidor (array
esparso), a origem do flag `in_instance` (payload vs componente), as
preferências salvas do personagem, e cada skill REMOVIDA/PLACED/
SKIPPED com o slot escolhido vs a preferência salva.

**Rodada 1 de log real** (usuário reproduziu com `RPG_DEBUG_HOTBAR_SLOT=1`):
mostrou que a mudança de slot NÃO vinha do bloco `skills_hotbar`
instrumentado — entre 2 `STATS_UPDATE` consecutivos (27s de intervalo),
`fatiador_de_corpos` (e outras skills reais: `golpe_debilitante`,
`brado_provocativo`, `punho_no_queixo`) apareceram em `ps.skills` em
posições COMPACTADAS (1,2,3,4), sem nenhum `PLACED`/`STATS_UPDATE_RAW`
do bloco já instrumentado disparar entre essas 2 chamadas — prova de
que outro mecanismo, fora de `_handle_msg_stats_update`, escrevia ali.
Instrumentação expandida (`_apply_hotbar_config` + `LOGIN_OK_RECEIVED`
+ `LEARNED_SKILL_IDS_SET`) pra cobrir as 2 suspeitas mapeadas por
leitura de código (nenhuma das 2 se confirmou na rodada 2).

**Rodada 2 de log real — causa raiz confirmada**: `learned_skill_ids`
do cliente cresceu de `{golpe_poderoso}` pra 5 skills ENTRE duas
chamadas de `_handle_msg_stats_update`, sem NENHUM
`LEARNED_SKILL_IDS_SET` logado no meio — ou seja, algo usa
`.add()` incremental, não a substituição completa que eu já
instrumentava. Busca por `learned_skill_ids.add(` achou
`ui/talent_system.py:299`, dentro de
`TalentSystem.apply_talent_effects()` — que também concede a skill
desbloqueada num "1º slot vazio" (`skills.skills.index(None)`,
`talent_system.py:293`), batendo exatamente com o padrão "compactado"
observado.

**Causa raiz completa**: `server/instance_progression.py::
_push_stats_update()` inclui `"talent_allocated": dict(tt.allocated)`
em TODO `STATS_UPDATE` que manda (todo level-up dentro da BG, não só
entrada/saída — um comentário no código cliente dizia o contrário,
desatualizado). Isso dispara `apply_talent_effects()` de novo em TODO
level-up — como a árvore de talentos da instância já vem pré-alocada
no MÁXIMO desde `enter_normalized_progression` (decisão de design já
documentada), essa função tenta conceder TODA skill de talento do
Cavaleiro de uma vez, direto no primeiro slot vazio, sem saber nada de
`INSTANCE_SKILL_UNLOCK_ORDER` nem da preferência salva do jogador. O
bloco `skills_hotbar` (que SIM respeita os dois) roda voltado na
MESMA chamada e remove de novo qualquer skill que a ordem curada ainda
não liberou — criando um ciclo concede→remove a cada level-up até a
ordem curada alcançar o nível real daquela skill, quando o mecanismo
correto (com a preferência) finalmente assume pra sempre.

**Fix**: dentro da instância (`InstanceInventoryUIState.active`), o
trecho de `apply_talent_effects()` que concede skill nova na hotbar
fica desativado — revelar skill na hotbar dentro da BG passa a ser
responsabilidade EXCLUSIVA do bloco `skills_hotbar`. Modifiers/
cs_flags (efeitos de stat/comportamento de talento, ex: Precisão
Elemental/Nova Congelante, §136) continuam aplicando normalmente a
cada chamada — só a parte de concessão de skill foi gateada.

**Testes** (`tests/test_talent_skill_grant_skipped_in_instance.py`):
não concede skill dentro da instância; continua concedendo normal
fora dela (regressão); continua aplicando modifiers dentro da
instância (guarda contra regredir §136); ponta a ponta com 5
"level-ups" simulados seguidos confirmando que a skill nunca aparece
prematuramente em nenhuma chamada. Revert-to-confirm: 2 dos 4 falham
sem o gate. Suíte completa de talentos/hotbar/BG + `test_client_ui.py`
(270 testes) passando. 100% client-side (`ui/talent_system.py`), sem
deploy no servidor.

**Lição de processo**: 2 rodadas de simulação "limpa" (código atual +
dados reais do personagem) NÃO reproduziram o bug — só o log real,
capturado em 2 rodadas sucessivas (a 1ª já eliminou 2 suspeitas
plausíveis e apontou pra uma 3ª, ainda não encontrada), revelou a
causa de verdade. Confirma a regra do projeto: preferir reprodução ao
vivo a leitura estática quando a simulação não bate com o sintoma
relatado — insistir em mais teorias sem dado real teria sido só
adivinhação.

---

## 57. "Deslogar" reconectando durante restart do servidor mostrava tela de seleção de personagem VAZIA — sem perda de dado, causa raiz confirmada e corrigida (29/09/2026)

Usuário relatou, no meio do teste do §217 (mesma sessão): "eu desloguei
o char para logar de novo, e na tela de seleção de char eu não tinha
nenhum char". Preocupação óbvia: perda de personagem.

**Descartado por log real do servidor**: toda linha `auth ok: 'juugo'`
no log do Hetzner, antes E depois do incidente (inclusive a leva
seguinte à queda), mostra `chars=3` — os 3 personagens nunca saíram do
banco. Coincidência de horário forte: eu tinha reiniciado o servidor
(`deploy_server.ps1`) duas vezes bem naquela janela de tempo, pra
aplicar os logs de diagnóstico do §217, enquanto o usuário ainda
testava.

**Causa raiz**: botão "Deslogar" (menu de pausa) reconecta com a MESMA
conta sem passar pela tela de login normal
(`main.py::_connect_and_login`, usado também pelo fast-path dev
`--user`/`--password`) — espera até 10s por `AUTH_OK`. Antes desta
correção, se essa resposta não chegasse a tempo (servidor reiniciando,
lento, qualquer falha de rede), a função devolvia `(net, [])` — os
MESMOS valores de uma conta genuinamente sem personagem nenhum — e o
código seguia direto pra tela de seleção, sem nenhum aviso de erro.
Comparação: a tela de login NORMAL (`ui/login_screen.py`) já tratava
esse timeout corretamente (mensagem "Servidor não respondeu" + permite
tentar de novo) — só o caminho de reconexão rápida nunca ganhou esse
mesmo cuidado.

**Fix**: `_connect_and_login` (`main.py`) agora devolve `(None, [])`
em qualquer falha real (conexão nunca estabelece, timeout sem
resposta, `LOGIN_ERROR`) — nunca mais confundível com "conta vazia de
verdade". Novo `_reconnect_or_login_screen` tenta a reconexão rápida
e, se falhar, cai na MESMA tela de login normal
(`ui.login_screen.run`, agora com 2 parâmetros novos —
`prefill_user`/`initial_status` — pra pré-preencher o usuário e
mostrar o motivo da queda) em vez de reimplementar tratamento de erro
duplicado em `main.py`. Os 2 pontos que reconectavam sem tratamento
("Deslogar" e o fast-path dev) passam a usar esse wrapper.

**Testes** (`tests/test_main_reconnect_login_fallback.py`, 5 casos,
`NetworkClient` falso via `unittest.mock.patch`, sem socket real):
sucesso real devolve net+lista; conta legitimamente vazia (`AUTH_OK`
com `characters: []`) NÃO é tratada como falha (distinção central
desta correção); conexão nunca estabelece → `None`; timeout sem
resposta → `None`; `LOGIN_ERROR` → `None`. Revert-to-confirm: 3 dos 5
falham no código anterior. Suíte relacionada (login/main/network, 30
testes) passando. 100% client-side (`main.py`/`ui/login_screen.py`) —
sem deploy no servidor.

---

## 58. Mundo fica todo preto (HUD normal) ao sair da BG — RESOLVIDO: `Camera.free_mode` nunca desligava sozinho (29/09/2026)

Usuário relatou: ao sair da BG (clicou "Voltar" na tela de resultado),
o mundo aparece todo preto — HUD continua normal — e só volta ao
normal relogando (não se resolve andando/esperando). Pista real dada
pelo usuário: começou a acontecer depois do trabalho recente na tela
de loading/transição (§215).

**Investigação estática, sem causa confirmada**: percorri os 3
candidatos mais plausíveis do próprio código de §215/correlatos —
nenhum bateu com o sintoma exato:
- O gate de "reconectando" (`game.py::run()`, `_in_loading` — na
  verdade bem mais antigo que §215, de maio/2026) mostraria a TELA de
  loading inteira (título + texto), não "HUD normal + mundo preto".
- `_draw_map_transition_screen`/`SOUNDS.set_game_ready` (§215, a
  mudança mais recente que toca `_handle_msg_zone_change`) é um
  desenho de 1 frame + toggle de som — não deveria deixar nenhum
  estado de render corrompido depois que `_do_transition` roda.
- O "Zoom surf" (`game.py::run()`, dimensiona `_zoom_surf` todo frame)
  depende só da RESOLUÇÃO DA TELA/zoom, nunca do mapa — BG ser menor
  que o mundo aberto não deveria afetar isso.
- Não há `try/except` envolvendo o loop de render de sistemas de mundo
  (`game.py::run()`, ~linha 2577) — uma exceção de verdade ali
  derrubaria o jogo inteiro (crash.log), o que não bate com "HUD
  continua normal".

Sem dado real, insistir em mais teorias seria adivinhação — mesma
disciplina já aplicada com sucesso no §217/§218 nesta sessão.

**Log de diagnóstico adicionado** (100% client-side,
`RPG_DEBUG_AOI=1`, nunca chega no servidor):
- `CLIENT_ZONE_CHANGE_RAW` (`client/network_handlers.py::
  _handle_msg_zone_change`) — payload bruto do `ZONE_CHANGE` +
  `is_real_change`, antes de qualquer processamento.
- `CLIENT_TRANSITION_DONE` (`game.py::_do_transition`, logo no fim) —
  dimensões do tilemap novo, tamanho de `_zoom_surf`, posição da
  câmera, `InstanceInventoryUIState.active`,
  `DayNightSystem.map_affected`.

**Próximo passo**: usuário reproduz localmente com
`$env:RPG_DEBUG_AOI=1; python main.py` (log fica em
`debug/logs/aoi_debug.log`, NO PRÓPRIO COMPUTADOR do usuário — não no
servidor, diferente da investigação do §217) — entra numa BG, sai
pelo botão "Voltar", e traz o log. Comparar os valores da transição
"saída de BG" (com bug) contra uma transição normal qualquer (sem
bug, ex: atravessar um portal de caverna) no MESMO log deve isolar o
que é diferente.

**Não validado**: causa raiz. Suíte relacionada (zone_change/
transition/day_night, 43 testes + map_transition_screen/loading_screen,
7 testes) passando sem regressão — o log em si não muda nenhum
comportamento.

---

### Causa raiz confirmada com dado real (mesmo dia) — `Camera.free_mode` travado

Usuário reproduziu de novo com o log de pixel real ativo (2 checagens:
1 transição funcionou sem bug, a seguinte reproduziu — screenshot real
anexado pelo usuário mostrando o mundo 100% preto). Comparação direta
no log:

- Transição que funcionou: `cam_xy=(3444.0, 12022.0)` — bate com a
  posição esperada do player no mundo aberto (perto do tile de
  destino, `target_x=122 target_y=383` → ~3900,12256px). Pixels
  amostrados do `_zoom_surf`: cores reais de terreno escurecidas pela
  noite (ex: `(11, 3, 22)`), não preto puro.
- Transição que reproduziu o bug: `cam_xy=(1912.8, 438.0)` — **nada a
  ver** com a posição esperada do player (`target_x=128 target_y=383`
  → ~4096,12256px). `(1912.8, 438.0)` é uma coordenada plausível
  DENTRO da BG (mapa de só 100×100 tiles = 3200×3200px), não do mundo
  aberto (1944×603 tiles). Os 3 pixels amostrados vieram `(0, 0, 0)`
  puro — o terreno realmente não desenhou nada ali (bate com a câmera
  apontando pra uma região sem sentido no mapa novo).

**Causa raiz**: `Camera.free_mode` — a câmera livre estilo MOBA
(tecla Y, `ui/render_system.py::CameraSystem`, pedido do usuário
20/09/2026) só liga DENTRO da BG/Arena (gate por
`InstanceInventoryUIState.active`) e a "re-trava" é manual, por
design (aperta Y de novo). O problema: **nada desligava `free_mode`
ao SAIR da instância** — se o jogador não apertasse Y de novo antes
de sair (ex: esqueceu, ou saiu logo depois de usar a câmera livre pra
olhar o mapa da BG), `free_mode` continuava `True` no mundo aberto.
E o próprio toggle da tecla Y só funciona com `InstanceInventoryUIState
.active` — que é `False` no mundo aberto — ou seja, **o jogador não
tinha NENHUMA forma de corrigir isso sozinho** uma vez fora da
instância; a câmera ficava congelada pra sempre na última posição do
pan livre (coordenadas de dentro da BG, sem significado no mapa muito
maior do mundo aberto) até relogar (que recria a entidade Camera do
zero, `free_mode=False` por default). Explica também "1ª vez
funcionou, 2ª não": o usuário só usou a câmera livre (tecla Y) durante
a 2ª sessão de BG.

**Fix** (`client/network_handlers.py::_handle_msg_zone_change`):
`Camera.free_mode = False` incondicional a cada `ZONE_CHANGE`, no
mesmo bloco que já limpa outro estado escopado à instância (remoção
de `Faction`, reset de `_bg_result_val`/`_bg_in_match_val`) — mesmo
padrão já estabelecido, só faltava este campo na lista. Seguro mesmo
fora de instância/nos casos que nem envolvem BG (free_mode já deveria
ser `False` ali de qualquer forma).

**Testes** (`tests/test_map_transition_screen.py`, 2 casos novos):
`ZONE_CHANGE` pra mapa diferente desliga `free_mode` que estava ligado;
o mesmo vale pro atalho de teleporte pro MESMO mapa (nunca deveria
sobreviver a nenhum `ZONE_CHANGE`, não só o caminho "de verdade").
Revert-to-confirm: os 2 falham sem o fix. Suíte relacionada (camera/
zone_change/transition/bg_queue/arena, 176 testes) passando sem
regressão.

**Logs de diagnóstico removidos** (`CLIENT_ZONE_CHANGE_RAW`/
`CLIENT_TRANSITION_DONE`/`CLIENT_TRANSITION_PIXEL_CHECK`) assim que a
causa raiz foi confirmada e corrigida, a pedido do usuário — nunca
deixar log temporário esquecido no código depois de resolver o que
motivou ele existir. `RPG_DEBUG_AOI`/`RPG_DEBUG_INTERCEPTAR`
continuam ativos (infra + flags em `config.json`, ver
`main.py`) — ainda servem à investigação em aberto do Interceptar
(§217).

**Lição**: o log de TEXTO (estado no instante da transição) não bastou
— o sintoma só existia nos frames RENDERIZADOS depois. Só a 2ª rodada
de diagnóstico, amostrando PIXELS DE VERDADE do `zoom_surf` +
comparando `cam_xy` contra a posição REAL esperada do player, revelou
a causa — mesma disciplina "medir com dado real em vez de continuar
lendo código estático" já usada com sucesso no §217/§218 nesta sessão.

---

## 59. Bot martelando a mesma skill contra alvo fora de alcance — mitigado com backoff local (29/09/2026)

Continuação do achado do §217: o log `RESOLVE_TARGET_FALLBACK`
(adicionado naquela investigação) revelou 781 ocorrências numa única
sessão de produção — quase sempre `reason=fora_de_max_range`: o bot
decidia CORRETO (skill aprendida, alvo dentro do alcance real,
cooldown pronto — tudo checado com dado FRESCO no instante da
decisão), mas a tentativa falhava porque o alvo (também em movimento,
bot ou player) saía de alcance antes do servidor processar o pedido —
a fila de skill requests pode atrasar vários ticks sob carga (ver
`SKILL_REQUESTS_TIME_BUDGET_MS`). Como o cooldown REAL só é registrado
em SUCESSO (nunca em falha, de propósito — não desperdiçar 22s numa
falha legítima de alcance), nada detinha o bot: ele reavaliava e
tentava a MESMA skill contra o MESMO alvo a cada ciclo de decisão
(~350ms), pra sempre.

Perguntado se queria a correção arquitetural completa (revalidar
alcance no último instante antes de executar — mudança maior, toca a
fila de processamento de skill) ou só atacar o sintoma visível
primeiro, o usuário escolheu focar no sintoma agora: "quero que o bot
tente usar interceptar sempre que ele tiver a skill desbloqueada, está
dentro da distância exigida pela skill, e a skill não está no
cooldown" — verificado que essas 3 regras já eram exatamente as que
`_try_priority_skill` usa (`server/bot_ai_system.py`); o problema nunca
foi a REGRA de decisão, e sim a corrida entre decisão e execução. Como
a fila atrasada em si (causa raiz de fundo) segue sem correção, focamos
em impedir a RAJADA de tentativas idênticas fadadas a falhar.

**Fix**: `BotControlled.skill_retry_backoff` (novo campo,
`engine/components.py` — dict `skill_id -> próximo horário permitido`)
+ `BOT_SKILL_RETRY_BACKOFF_S = 1.5` (`server/bot_ai_system.py`).
Aplicado nos 2 ÚNICOS pontos que enfileiram skill pro bot
(`_try_cast_specific`, usado pela prioridade tática por classe;
`_try_cast_offensive_skill`, o fallback genérico do hotbar) via 2
métodos novos (`_skill_retry_allowed`/`_mark_skill_attempt`) — nunca
hardcoded pra "interceptar" especificamente, qualquer skill futura com
o mesmo padrão (gap-closer contra alvo móvel) se beneficia de graça.
Bot continua podendo cair pra OUTRA skill pronta no hotbar enquanto
uma está em backoff (não fica parado à toa — confirmado no teste: com
Interceptar em backoff, o bot cai pra Golpe Poderoso normalmente).

**Testes** (`tests/test_bg_bot.py::TestBotInterceptarRetryBackoff`, 3
casos): 1ª tentativa enfileira normal; 2ª tentativa no ciclo seguinte
(sem deixar o cooldown real registrar — simula uma falha real de
alcance) NÃO reenvia a MESMA skill; depois do backoff expirar, tenta
de novo. Revert-to-confirm: 2 dos 3 falham sem o fix (o 3º dá erro de
atributo ausente). Suíte completa de bot (55 testes,
`tests/test_bg_bot.py`) passando sem regressão. Deploy feito pro
servidor (código compartilhado client/server, mas a mudança em si é
100% server-side — `server/bot_ai_system.py` só roda lá).

**Fica registrado, não esquecido**: a causa raiz de fundo (fila de
skill requests atrasando vários ticks sob carga, permitindo o alvo se
afastar demais entre decisão e execução) continua sem correção — só o
SINTOMA (retry infinito) foi mitigado. Se o volume de
`RESOLVE_TARGET_FALLBACK` continuar alto mesmo com o backoff, a
correção arquitetural mencionada acima (revalidar distância no último
instante antes de executar, ou reduzir a latência da fila sob carga)
ainda está de pé como próximo passo.

**Interceptar do personagem do usuário (§217, metade original)**:
pausado por pedido explícito do usuário — juntar dados novos exige um
teste dedicado (não incidental, como os testes de BG que geraram os
dados usados até agora) e o usuário preferiu não gastar esse tempo
agora. Não fechar como resolvido — retomar quando o usuário topar
testar de novo.

## 60. Sistema de Portais (§18, Animação do tile): 2 bugs reais no 1º playtest — relógio errado quebrou ataque, animação nunca batia com o objeto (01/10/2026)

Usuário testou em jogo a animação de evento do portal (ver
`ARQUITETURA ONLINE.md` §18) e relatou 2 problemas na mesma mensagem:
"não funcionou" (a animação do portal) e "além disso estragou as
animações de ataque do personagem, não funcionam mais".

**Bug 1 — relógio de parede vs ticks do pygame (quebrava TODO ataque
depois do 1º cast de portal)**: `client/portal_handlers.py::
_handle_msg_portal_cast_start` setava `TileMovement.
anim_attack_started_ms` com `time.time()*1000` (relógio de PAREDE,
epoch — número ~1.7 trilhão). TODO o resto do sistema de animação de
personagem usa `pygame.time.get_ticks()` (dezenas de milhares,
resetando a cada sessão) — ver `ui/render_system.py::
resolve_display_state()` e `client/remote_entity_handlers.py::
_apply_combat_result` → `trigger_attack_anim(..., pygame.time.
get_ticks())`. `engine/character_animation.py::_attack_loop_still_active`
faz `now_ms - anim_attack_started_ms < duration` — com a escala
errada, essa subtração dá um número NEGATIVO enorme, sempre menor que
qualquer duração, então `trigger_attack_anim`'s guard de "golpe em
loop ativo" (pensado originalmente só pra Fatiador de Corpos, ver §18
histórico) bloqueava PERMANENTEMENTE qualquer golpe novo depois do 1º
cast de portal — não só durante os 5s do cast. Fix: `pygame.time.
get_ticks()`, igual ao resto do jogo. Também zera
`anim_attack_loop_duration_ms` em `_cancel_portal_cast_local` (cast
cancelado antes do tempo deixava esse campo "vivo" até os 5s originais
passarem sozinhos — bug menor, mesma classe).

**Lição**: qualquer campo que vai ser lido por `trigger_attack_anim`/
`_attack_loop_still_active` (ou qualquer código que mistura timestamps
com código já existente) precisa confirmar, por grep nos call sites
REAIS (não só na assinatura da função), qual relógio aquele código já
usa — `now_ms: int` no type hint não diz nada sobre a ESCALA esperada.

**Bug 2 — animação nunca desenhada: `vp_144` é `no_ysort=True`, nunca
passa por `get_world_objects()`**: a implementação original só
adicionava a troca de sprite estático→animado dentro de
`get_world_objects()`. Mas o catálogo do portal
(`engine/tileset.py`, grupo "cemiterio", entrada `"144"`) tem o 9º
campo posicional (`sort_val`) igual a `0` → `no_ysort=True` —
`get_world_objects()` pula esses objetos de propósito (`if no_ysort:
continue`, são desenhados por `render_static_objects()`, uma função
TOTALMENTE separada). A animação nunca tinha chance de aparecer
porque o código novo vivia na função errada. Fix: mesma lógica
(`engine.portal_anim_state.get_active` + `TILE_SPRITES.
get_portal_event_frame`) duplicada em `render_static_objects()`.

**Bug 3 — posição do GATE ≠ posição de RENDER do objeto (achado
DEPOIS do usuário confirmar que o bug 2 sozinho não bastou — pergunta
dele: "você garantiu que a animação está ordenada acima do objeto do
portal?")**: mesmo corrigindo o bug 2, a animação ainda não aparecia.
Investigação: `maps/map_1_entities.json::portal_gates` marca a posição
onde o JOGADOR fica em pé pra interagir (`portal_teste_1` em
(138,370), `portal_teste_2` em (210,480)); `grep` em
`maps/map_1_objects.csv` mostrou que o objeto `vp_144` de CADA um dos
2 portais está plantado em `(gate_x-1, gate_y+1)` — UM TILE DE
DISTÂNCIA, consistente nos dois casos (sprite ancora no canto onde a
arte foi desenhada, não necessariamente onde o jogador interage). O
servidor só manda a posição do GATE (nunca sabe de sprite/objeto, por
desenho — ver §18); escrever o estado de animação direto nessa posição
significava que `render_static_objects()`/`get_world_objects()`
(que consultam pela posição REAL do objeto no `object_matrix`) nunca
encontravam nada — chaves diferentes, nunca batiam. Fix:
`client/portal_handlers.py::_find_portal_anim_anchor()` (novo) escaneia
um raio pequeno (`_PORTAL_ANIM_ANCHOR_SEARCH_RADIUS = 2`) ao redor do
gate, no `object_matrix` real, procurando o tile cujo `sprite_name`
está cadastrado em `PORTAL_ACTIVATE_SHEET_BY_TILE_ID`/
`PORTAL_DEACTIVATE_SHEET_BY_TILE_ID` — resultado cacheado em
`self._portal_anim_anchor` (`_build_portal_tiles`, 1x por carga de
mapa, precisou de parâmetro novo `object_matrix`, 2 call sites em
`game.py`). `_handle_msg_portal_anim_start/stop/arrive` traduzem
`(payload["x"], payload["y"])` por esse dict ANTES de escrever em
`portal_anim_state` — nunca mais usar o x/y cru do payload como chave
de animação.

**Revert-to-confirm**: os 2 bugs tiveram teste novo em
`tests/test_portal.py` (`TestPortalClientAnimationClock`,
`TestPortalAnimAnchorResolution`) — reintroduzidos de propósito, 1 por
vez, confirmando que os testes pegam cada um; restaurados os fixes
depois. Suíte relacionada (portal + character_animation + duelo, 106
testes) passando. Como são bugs 100% client-side, não precisam de
deploy no servidor — só reabrir o jogo local já pega a correção.

**Lição de processo**: a pergunta direta do usuário ("ordenada acima
do objeto?") não era literalmente a causa raiz (não era Z-order), mas
apontou corretamente pra região certa do problema (algo entre o
desenho do objeto e a animação não estava se conectando) — vale como
pista de investigação mesmo quando o termo técnico usado não é exato.

## 61. Fatiador de Corpos: cada tick de dano reiniciava o loop de animação (02/10/2026)

Usuário relatou: "a skill dura 5 segundos mas a animação fica um bom
tempo depois". Investigado por subagente dedicado (não misturado com o
trabalho de portal da mesma sessão).

**Causa raiz**: Fatiador de Corpos manda 1 resultado de combate POR
TICK de dano (`server/world_server.py:6299-6348`, `tick_interval=1.0`
no `SKILL_CATALOG`, ~5 ticks ao longo dos 5s reais), cada um com
`"source": "skill", "sid": "fatiador_de_corpos"`. O cliente
(`client/remote_entity_handlers.py::_apply_combat_result`) chama
`trigger_attack_anim(...)` pra CADA um desses resultados, sem
filtro — e `trigger_attack_anim` (`engine/character_animation.py`)
tinha um guard que só bloqueava golpe CONCORRENTE de outro tipo
durante um loop ativo (`if _new_loop_ms == 0 and
_attack_loop_still_active(...): return`); como `_new_loop_ms` é
SEMPRE != 0 pro próprio Fatiador (é uma skill de loop), esse guard
nunca pegava o caso "é o MESMO loop de novo" — cada tick reiniciava
`anim_attack_started_ms` pra "agora" e rearmava os 5000ms do zero. O
último tick (perto de t≈4s) empurrava o fim VISUAL pra ~t≈9s — ~4s
depois do fim de verdade da skill. Efeito colateral: a cada tick o
frame também voltava pro 0, fazendo a animação "piscar"/reiniciar
visivelmente uma vez por segundo.

**Achado de processo**: existia um teste
(`tests/test_character_animation.py`, agora reescrito) que CODIFICAVA
esse reset como comportamento esperado — comentário original "retrigger
do mesmo loop reinicia o contador (novo tick de dano)" — ou seja, quem
escreveu aquele teste (uma sessão anterior) já sabia que ticks
reiniciavam o contador e tratou isso como intencional, sem perceber que
isso esticava a duração VISUAL além da duração real da skill. Confirmado
com o usuário (`AskUserQuestion`) antes de mexer: o cooldown real do
Fatiador é 45s contra 5s de duração, então "recast genuíno no meio do
próprio loop" é IMPOSSÍVEL hoje — o cenário que o teste antigo tentava
proteger nunca acontece de verdade; só os ticks do MESMO cast acontecem,
e esses nunca deveriam reiniciar nada.

**Fix**: `TileMovement` ganha `anim_attack_sid` (novo campo — sid CRU
que ligou o loop atual, distinto de `anim_attack_state`, que é o nome
RESOLVIDO/exibido e pode ser genérico tipo "skill"). `trigger_attack_anim`
passa a comparar o `sid` do trigger NOVO contra `anim_attack_sid` do
loop já ativo: mesmo sid + loop ainda dentro da janela → IGNORA
(não reinicia nada, nem o relógio nem o frame); sid DIFERENTE → substitui
normalmente (comportamento já existente, intocado — ex: skill de loop
futura interrompendo outra). Testes: `tests/test_character_animation.py`
— `test_tick_do_mesmo_loop_e_ignorado_nunca_reinicia` (5 ticks seguidos,
nenhum mexe em `anim_attack_started_ms`, expira no tempo certo a partir
do INÍCIO real) + `test_loop_diferente_ainda_substitui_normalmente`
(controle, loop hipotético diferente registrado temporariamente). Revert-
to-confirm feito — reproduziu o bug exato (contador reiniciando a cada
tick) antes de restaurar o fix.

## 62. Corpo de mob "sobrevivia" entre instâncias de BG — cache client-side sem mapa (02/10/2026)

Usuário relatou, testando a BG repetidamente: o corpo de um mob morto
numa sessão de teste continuava aparecendo ao entrar numa instância
NOVA depois — ficou em dúvida se a BG era realmente instanciada.

**Investigação (2 subagentes, nunca misturada com o trabalho de
Fatiador/portal da mesma sessão)**: a isolação SERVER-SIDE está
correta — `_unload_instance()` (`server/world_server.py:995-1026`)
remove toda entidade com `MapLocation(instance_key)`; `/testbg` (chave
fixa compartilhada) e a fila real (`match_id` incrementando,
`bg_queue_processor.py`) ambos chamam isso corretamente quando os
jogadores saem (manual, timeout, OU disconnect — `on_disconnect`
chama `_leave` incondicionalmente). "Corpse" nem é uma entidade ECS de
verdade — é um dict puro em `server/loot_processor.py::_corpses`
(`corpse_id` de contador global, nunca colide entre partidas), com
timer de expiração próprio (120s mob / 4s minion) que despacha
`ENTITY_DESPAWN` só pra quem ainda está na AOI daquele mapa
(`server/session.py`).

**Causa raiz de verdade — 100% client-side**: `client/
network_handlers.py` guarda cada corpse em `self._remote_corpses[
corpse_id] = (tx, ty)` — SEM NENHUMA noção de mapa. Se o jogador sair
da BG ANTES do timer de 120s expirar (`/testbg leave` antes da hora,
bem comum em teste) — o que é o caminho normal, não uma exceção — o
`ENTITY_DESPAWN` que eventualmente dispara nunca alcança esse cliente
(ele já não está mais na AOI daquele mapa). A entrada fica presa em
`_remote_corpses` pelo resto da SESSÃO inteira do cliente, e
`_draw_remote_corpses` (`client/remote_entity_handlers.py`) desenha
QUALQUER entrada ali, sem filtrar por mapa — reaparece em QUALQUER
mapa futuro que reuse as mesmas coordenadas de tile (todo BG usa o
mesmo layout, por isso o sintoma era tão consistente). `game.py::
_do_transition()` já limpava `_remote_mobs`/`_remote_players`/
`_pending_mob_despawn`/`_mob_ghost_pos` em toda troca de mapa de
verdade, mas `_remote_corpses`/`_available_loot` (mesmo problema,
dict por corpse_id sem mapa) nunca entraram nessa lista — mesma classe
de bug do reset de `Camera.free_mode` (§219): estado que deveria
resetar em TODA troca de mapa, mas não resetava.

**Fix**: as 4 limpezas existentes + as 2 novas foram extraídas pra
`GameEngine._clear_remote_entity_caches()` (novo método, chamado só no
branch de troca de mapa DE VERDADE de `_do_transition`) — extração
deliberada pra ficar testável isoladamente (sem precisar instanciar
`GameEngine` inteiro) e pra qualquer cache NOVO indexado por eid/
corpse_id remoto ter um lugar ÓBVIO pra entrar, em vez de ser lembrado
caso a caso. Teste novo (`tests/test_map_transition_screen.py::
TestClearRemoteEntityCaches`) bate numa lista EXPLÍCITA dos 6
atributos — revert-to-confirm feito, reproduziu o sintoma exato
(`_remote_corpses`/`_available_loot` sobrevivendo) antes de restaurar.

**Achado secundário, não corrigido (fora do sintoma relatado)**: o
dict `_corpses` do SERVIDOR (`server/loot_processor.py`) também nunca
é varrido por `_unload_instance()` — um corpse criado dentro de uma
instância sobrevive ali até o próprio timer (120s/4s) expirar, mesmo
depois da instância já ter sido descarregada. Bounded/autolimpa
sozinho, nunca visível pro jogador (nenhum cliente está na AOI de uma
instância já descarregada) — mencionado ao usuário, não é o bug
relatado, correção fica a critério dele.

## 63. Bug real — `loot_request` crashava com `UnboundLocalError: s` quando o corpse esvaziava sem ninguém na AOI (02/10/2026)

**Sintoma relatado** (achado pelo usuário no log de produção, sem
pedir investigação — "ERRO em handler loot_request: local variable 's'
referenced before assignment"): `SessionManager.on_message`
(`server/session.py:652-658`) captura qualquer exceção de handler
genericamente e devolve `MsgType.ERROR` ao cliente em vez de deixar a
conexão cair — por isso o sintoma nunca apareceu como crash de
verdade, só como um `ERROR` espúrio chegando depois do saque.

**Causa raiz**: `_handle_loot_request`, no trecho que declara um
corpse REALMENTE vazio e manda `ENTITY_DESPAWN` pra quem está na AOI:

```python
for s in self._sessions_in_aoi(corpse_data.get("tx", 0),
                               corpse_data.get("ty", 0),
                               corpse_data.get("map")):
    await s.send(MsgType.ENTITY_DESPAWN, despawn_payload)
s.known_eids.discard(-corpse_id)   # MESMA indentação do for -- fora do loop
```

`s.known_eids.discard(...)` estava na MESMA indentação do `for`, não
do `await s.send(...)` dentro dele — ou seja, rodava só UMA vez, DEPOIS
do loop, usando o que quer que `s` tivesse valido na ÚLTIMA iteração.
Dois problemas, um deles o crash relatado:
1. Se `_sessions_in_aoi(...)` devolve vazio (ninguém perto do tile do
   corpse no instante exato em que ele esvazia — cenário comum: quem
   sacou já tinha se afastado, ou o resto do grupo saiu da área), o
   loop nunca roda nenhuma vez, `s` nunca é atribuída, e a linha
   seguinte explode com `UnboundLocalError`.
2. Mesmo com o loop rodando (≥1 sessão), só a ÚLTIMA sessão da
   iteração tinha `known_eids` corrigido — as outras ficavam com o eid
   negativo "fantasma" preso em `known_eids` pra sempre (apesar de já
   terem recebido o `ENTITY_DESPAWN` de verdade via `send()` — a
   inconsistência era só na contabilidade server-side de "o que esta
   sessão já sabe", não no que o cliente via na tela).

Confirmado por comparação direta com o par simétrico já correto no
mesmo arquivo (`server/session.py` ~linha 3231-3233, ENTITY_SPAWN):
```python
for s in self._sessions_in_aoi(...):
    await s.send(MsgType.ENTITY_SPAWN, spawn_payload)
    s.known_eids.add(-corpse_id)   # DENTRO do loop, 1x por sessão
```

**Fix**: mover `s.known_eids.discard(-corpse_id)` pra dentro do loop,
mesma indentação de `await s.send(...)` — 1 linha.

**Teste**: `tests/test_session.py::TestLootRequestDespawnBroadcast` —
sem ninguém na AOI não gera `MsgType.ERROR` (reproduz o sintoma exato
relatado); com 2 sessões na AOI, as DUAS têm o eid fantasma removido
de `known_eids`, não só a última. **Não executável neste ambiente**
(sem Postgres local disponível — mesma limitação já documentada pro
resto da suíte nesta sessão; os testes chegam a tentar a conexão real
com o banco via `fake_login`, confirmado que o único erro é
`ConnectionRefusedError`, não um bug na escrita do teste). Mecanismo
confirmado isoladamente, fora da suíte, com um script mínimo
reproduzindo só a forma do bug (sem SessionManager/DB): código antigo
levanta a MESMA mensagem de erro relatada com lista vazia, e só
corrige a última sessão com lista de 2; código novo corrige as duas e
nunca levanta.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) no mesmo
commit da notificação por e-mail em andamento (ver `ARQUITETURA
ONLINE.md` §21) — fix de servidor, sem jogador real afetado até agora
além do próprio usuário testando.

## 64. Bug real — matar mob só contava pra quest de quem bateu primeiro, nunca pro resto do grupo (03/10/2026)

**Sintoma relatado pelo usuário**: "quando qualquer um dos membros
matar um mob de uma quest que um dos membros tem, conta para a quest,
mesmo que o portador da quest não tenha participado da luta... eu
achava que isso já estava correto e quando testei recentemente não
estava funcionando." Usuário pediu pra investigar bem antes de
assumir causa raiz.

**Investigação**: `engine/quest_events.py::fire()` enfileira 1 evento
com 1 `player_eid` só; `WorldServer._process_quest_events()`
(`server/world_server.py`) aplica progresso SÓ a esse eid. Único
chamador real de `fire("kill", ...)` é `server/server_death_handler.py`
(`engine/world_systems.py:1111` existe mas nunca passa `player_eid`,
cai no default -1 e é ignorado por `_process_quest_events` — código
morto/caminho client-only, confirmado por grep, não é o que roda
server-side) — disparava com `player_eid=first_attacker_eid` **só**,
comentário no próprio código dizia "mesmo critério de dono do loot"
(premissa errada: o loot JÁ compartilha com o grupo inteiro via
`get_party_members`, ver `_resolve_conditional_loot_for`/`server/
session.py` ~linha 3310, a quest "kill" nunca teve o mesmo fan-out).

**Contraste que confirmou a causa raiz**: objetivo `collect_item`
(chance de item de quest cair) JÁ funciona certo em grupo — manda
`LOOT_AVAILABLE` personalizado pra TODOS os membros do grupo do
first-attacker, não só ele, nenhum gate de distância. Só `kill`
estava quebrado — exatamente o sintoma relatado ("matar mob" não
funciona, mas o usuário não reclamou de "coletar item").

**Fix**: `server/server_death_handler.py`, bloco do evento "kill" —
fan-out pra `get_party_members(first_attacker_eid)` (mesmo padrão do
collect_item), 1 evento por membro do grupo. Sem gate de distância/
AOI de propósito — mesmo precedente do collect_item (diferente do XP,
que usa `PARTY_XP_SHARE_RADIUS_TILES`); pedido do usuário foi
explícito ("mesmo que não tenha participado da luta"), sem mencionar
restrição de posição.

**Teste**: `tests/test_quest_kill_party_credit.py` (6) — quest holder
no grupo progride sem lutar; killer de verdade continua progredindo
normal (regressão); os dois progridem juntos se ambos tiverem a
mesma quest; objetivo já no count máximo não passa; estranho FORA do
grupo não progride (controle negativo); grupo de 3 — o 3º membro
(nem killer nem quem deu o kill) também progride. Revert-to-confirm
feito (fan-out revertido pra só `first_attacker_eid`, 3 dos 6 testes
falharam exatamente como esperado, restaurado).

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`).

## 65. Bug real — Brado Provocativo nunca aumentava dano (talento prometia, código nunca aplicava) (03/10/2026)

**Sintoma relatado pelo usuário**: usou Brado Provocativo num duelo
PvP e pareceu não afetar o oponente; testando contra mobs depois, o
ícone "Provocado" aparecia certo ao lado da barra de HP, mas o dano
causado ao mob não aumentava os 10% esperados, e o dano que o mob
causava de volta também não subia os 5% esperados.

**Investigação**: `content/talent_data.py::cav_provocacao` promete no
texto "+5% dano causado, +10% dano recebido" por quem é provocado.
`engine/world_systems.py::CombatSystem._resolve_damage_modifiers`
(linha ~928) JÁ tinha o código pra isso: `if _sfx_target.has("enraged"):
dmg *= 1.10` / `if _sfx_att.has("enraged"): dmg *= 1.05`. Só que
`engine/skill_handlers.py::_skill_brado_provocativo` nunca aplicava o
status `"enraged"` — só `"taunted"` (o CC que força o alvo a andar/
atacar o taunter). Ou seja: o código de dano sempre existiu, mas
nunca disparava, porque nada aplicava o status que ele lia. Clássico
gap de wiring entre 2 peças escritas em sessões diferentes (CC em
23/07/2026, bônus de dano referenciado no texto do talento desde
então) que nunca foram conectadas — e sem teste algum cobrindo o
bônus (`tests/test_taunt.py` só testava o CC).

A parte do duelo (debuff "não apareceu") não foi confirmada como bug
separado — mais provável é o oponente estar fora do raio de 3 tiles
da skill (o jogo avisaria "Nenhum inimigo no raio" nesse caso) ou o
usuário ter notado a falta do bônus de dano e generalizado como "não
afetou". Sincronização de StatusEffects pra players remotos em PvP já
foi confirmada funcionando (`client/remote_entity_handlers.py::
_sync_mob_effects` busca mob OU player remoto pelo mesmo eid).

**Decisão do usuário**: "taunted" (CC — forçado a atacar) e "enraged"
(dano) têm durações INDEPENDENTES — CC continua 3s, dano continua os
10s cheios do talento (o alvo pode parar de ser perseguido mas continua
"enraivecido"/fácil de matar pelo resto da duração).

**Fix**: `content/skill_config.py` ganhou `"enraged_duration": 10.0`
(mantendo `"duration": 3.0` só pro CC); `_skill_brado_provocativo`
agora chama `apply_effect(..., "enraged", _enraged_duration_bp)` junto
com o `apply_effect(..., "taunted", ...)` já existente.

**Teste**: `tests/test_taunt.py::TestBradoProvocativoDanoEnraged` (4) —
enraged aplicado junto com taunted; enraged sobrevive ao fim do taunted
(>3s) mas expira em ~10s; unidade direta em `_resolve_damage_modifiers`
confirmando +10% dano recebido e +5% dano causado pelo provocado.
Revert-to-confirm feito (removida a chamada de `apply_effect("enraged")`,
os 2 testes que dependem dela falharam exatamente como esperado,
restaurado).

**Achado fora de escopo, reportado e NÃO corrigido agora**: ao ler
`CombatSystem.deal_damage` (`engine/world_systems.py` ~linha 732-777)
pra entender onde o bônus entra, percebi que o branch `pre_outcome`
(usado por flechas que já resolveram miss visualmente) chama
`_resolve_damage_modifiers` DUAS VEZES — uma dentro do próprio branch
(linha ~748, reatribuindo `calculated_damage`) e outra incondicional
mais abaixo (linha ~773, sempre executada pros 2 branches). Isso
dobraria armadura/Foco Mortal/o bônus de "enraged" novo especificamente
pra ataques físicos à distância com outcome pré-rolado — nunca
confirmei o impacto real em jogo (não testei), só achei lendo o código
por causa deste fix. Fica registrado aqui pro usuário decidir se quer
que eu investigue/corrija numa rodada própria.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`).

## 66. Bug real — ícone do debuff de Brado Provocativo só aparecia 3s (classificação errada de is_buff) (03/10/2026)

**Sintoma relatado pelo usuário**, no mesmo dia e logo depois do fix
do §226: "o ícone do debuff está ficando somente 3 segundos, ele deve
ficar durante os 10 segundos de debuff" — ou seja, mesmo depois do
§226 ligar o bônus de dano por 10s, nenhum ícone correspondente
aparecia no alvo além do "Provocado" (taunted), que some em 3s.

**Causa raiz**: `content/status_effects_data.py::EFFECT_DEFS["enraged"]`
tinha `is_buff=True`. Dois filtros de UI já existentes (criados
31/08/2026, antes de "enraged" ter qualquer produtor real — ver §11
("enraged"/"haste"/"regen" eram entradas órfãs) tratam `is_buff` como
roteamento exclusivo:
- `ui/render_system.py::_filter_nameplate_effects` — nameplate (acima
  da cabeça de mob/jogador, local OU remoto) só mostra `is_buff=False`.
- `ui/buff_bar.py` — a barra de buff só mostra `is_buff=True`, e só
  pro jogador LOCAL (nunca desenha buff de outra entidade).

Com `is_buff=True`, "enraged" nunca aparecia em lugar nenhum quando
aplicado a um MOB ou a um OPONENTE de duelo — não é "jogador local",
então a buff bar nunca o desenha; e por ser `is_buff=True`, o
nameplate também o exclui de propósito. Resultado: o único ícone
visível em quem foi provocado era "taunted" (debuff puro, 3s) — o
bônus de dano de 10s do §226 ficava sem nenhuma representação visual
em QUEM o jogador está de fato olhando (o alvo).

**Fix**: `EFFECT_DEFS["enraged"].is_buff` → `False`. O efeito é
mecanicamente misto (+5% causado / +10% recebido), mas o sinal visual
relevante é sempre no ALVO provocado — mesma categoria de "taunted".
Efeito colateral identificado e aceito (não é regressão, é
consequência correta da reclassificação): `engine/skill_handlers.py`
(~linha 1288, Bloco de Gelo) remove todo efeito `not is_buff` ao
ativar — um mago que leva Brado Provocativo e usa Bloco de Gelo agora
também limpa "enraged" (antes só limpava "taunted") — coerente com o
Bloco de Gelo ser uma bolha de imunidade.

**Teste**: `tests/test_nameplate_buff_filter.py::
test_enraged_e_taunted_aparecem_juntos_no_nameplate` (novo) — confirma
que os 2 efeitos aparecem juntos no nameplate. Revert-to-confirm
(`is_buff` de volta pra `True`, o teste novo falhou exatamente com
"enraged" faltando no conjunto, restaurado). Suíte de buff/nameplate/
taunt/combate completa (68 testes) passando.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`).

## 67. Bug real — som e animação de usar o portal só existiam pro próprio caster (03/10/2026)

**Sintoma relatado pelo usuário**: "quando o player usa o portal, o
efeito do sonoro não está sendo emitido, e nem o efeito do sprite do
player está aparecendo para o player remoto". Confirmado por pergunta
direta: o próprio caster ouve/vê normal — só quem está observando de
fora não recebe nada.

**Causa raiz**: `PORTAL_CAST_START` (`server/session.py`) é enviado só
pro `session` que pediu (`await session.send(...)`, nunca broadcast).
`client/portal_handlers.py::_update_portal_sfx`/`_update_portal_char_anim`
só leem `self._portal_cast_active` — estado 100% local do PRÓPRIO
jogador. Sem nenhuma mensagem avisando quem está por perto, o cliente
de um observador nunca tem motivo pra tocar som nem animar a entidade
remota. Diferente da animação do TILE do portal em si
(`PORTAL_ANIM_START`/`STOP`/`ARRIVE`), que já é broadcast por AOI desde
01/10/2026 — só o personagem ficou de fora.

**Fix** (ver `arquitetura/ARQUITETURA ONLINE.md`, seção de portais,
pra desenho completo): 2 mensagens novas, `PORTAL_CAST_BEGIN`/
`PORTAL_CAST_ABORT` (S→C, broadcast AOI, pares de `PORTAL_CAST_START`/
`CANCEL`), + `PortalHandlers._update_remote_portal_casts` no cliente,
espelhando som posicional e animação "portal_in" na entidade remota.
2 funções de módulo novas (`_begin_portal_in_anim`/`_clear_portal_in_anim`)
extraídas do código que já existia só pro caster local, reusadas
pelos 2 caminhos — nenhuma lógica duplicada.

**Bug real pego pelo próprio teste, antes de ir pra produção**: a
propriedade `_remote_portal_casts` (copiando o padrão `getattr(...)
or {}` de `portal_tiles`) nunca persistia nenhuma mutação em lugar —
dict vazio é "falsy" em Python, então `or {}` sempre devolvia um dict
NOVO descartável assim que o guardado ficava vazio. `portal_tiles`
nunca teve esse problema porque é sempre reatribuído por inteiro
(nunca `self.portal_tiles[x] = y`); qualquer propriedade nova que
mute dict/lista em lugar precisa inicializar e CACHEAR no primeiro
acesso (`if ... is None: self._x_val = {}`), nunca `or {}`. Lição
pra qualquer propriedade lazy futura do mesmo padrão.

**Teste**: `tests/test_portal_remote_mirror.py` (10, fixture mínima de
`PortalHandlers` mesmo padrão de `tests/test_client_ui.py`) — cobre
handler de rede dos 2 eventos novos + `_update_remote_portal_casts`
(SFX no instante certo, animação só no último segundo, remoção ao
completar/ao a entidade sair do AOI, abort limpa tracking+animação).
Revert-to-confirm feito (reintroduzido o `or {}` quebrado, 4 de 10
testes falharam exatamente como esperado, restaurado). Regressão:
`tests/test_portal.py`/`tests/test_character_animation.py`/
`tests/test_client_ui.py` completos continuam passando.

**Fora de escopo, não implementado**: animação de CHEGADA
("portal_out") pra quem já está no mapa de destino vendo outro
jogador materializar — pedido do usuário foi só sobre o lado do
cast/origem.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`).

## 68. Bug real — skill ganha por recompensa de quest desaparecia da hotbar no próximo login (03-04/10/2026)

**Sintoma relatado pelo usuário**: cria personagem novo, ganha "Golpe
Poderoso" ao completar uma quest, a skill vai pra barra de ações —
relogou o personagem e a skill some da barra (continua "aprendida",
só não aparece em nenhum slot).

**Causa raiz dupla** (as 2 juntas produzem o sintoma; a 1ª é o
gatilho real, a 2ª é um buraco de segurança que já existia por trás):

1. **`game.py` — "Sair do jogo"/"Deslogar" pelo menu de pausa nunca
   chamavam `_save_config()`.** Só `pygame.QUIT` (botão X da janela)
   fazia as 2 chamadas (`_send_save_state()` + `_save_config()`); os
   2 botões do menu (as ações `"quit"`/`"logout"`) só chamavam
   `_send_save_state()` — o comentário já dizia "mesmo padrão já
   usado pelo fechamento via botão X", mas só copiou a metade que
   manda pro servidor, nunca a que grava `config.json` localmente
   (onde o LAYOUT da hotbar vive — "learned" vai pro servidor, mas o
   layout é tratado como UI local de propósito, ver comentário em
   `client/save_sync_handlers.py`). Resultado: usar o menu pra
   sair/relogar (o caminho normal, não fechar a janela) nunca
   persistia a hotbar em disco.

2. **`game.py::_apply_hotbar_config` — o fallback que reinsere skill
   aprendida ausente só rodava dentro de `if hb:`.** Mesmo COM o
   fix #1, um personagem RECÉM-CRIADO nunca teve nenhum hotbar salvo
   ainda (`hb is None`) — e o bloco inteiro que reinsere qualquer
   skill de `learned_skill_ids` sem slot (comentário da própria
   função: "Garante que skills aprendidas... não desapareçam da
   hotbar") ficava pulado justamente nesse caso, porque estava todo
   dentro do `if hb:`. A rede de segurança só cobria "config salvo
   mas faltando uma skill nova", nunca "sem config nenhum salvo".

**Fix**: (1) `_save_config()` adicionado nas 2 ações do menu, mesmo
padrão do `pygame.QUIT`. (2) `_apply_hotbar_config` reestruturado —
`hb_data = hb or {}` em vez de gatear o bloco inteiro em `if hb:`;
sem nenhum slot salvo (`saved_slots=[]`), o restante da função já
preenche a hotbar do zero com qualquer skill de talento/aprendida
ainda sem lugar — mesmo mecanismo que já existia, agora também
cobre "sem config nenhum ainda".

**Teste**: `tests/test_hotbar_bg_leak.py::TestApplyHotbarConfigSemConfigSalvo`/
`TestApplyHotbarConfigComConfigSalvo` (4, fixture mínima com o método
real de `GameEngine`, `config.load` mockado — nunca toca o
config.json de verdade) — skill aprendida sem config nenhum entra na
hotbar; sem skill aprendida a hotbar continua vazia (controle
negativo); layout salvo continua restaurado no slot certo
(regressão); skill nova ausente de um config JÁ salvo continua
reinserida (regressão do comportamento antigo). Revert-to-confirm
feito (gate `if not hb: return` reintroduzido, o teste principal
falhou exatamente como esperado, restaurado). Suíte completa de
`test_hotbar_bg_leak.py`/`test_client_ui.py` sem regressão (12
falhas em `test_hotbar_bg_leak.py` são o `ConnectionRefusedError` do
Postgres local indisponível, confirmado — nenhuma toca
`_apply_hotbar_config`).

**Não testado automaticamente**: a chamada nova de `_save_config()`
nas ações `"quit"`/`"logout"` do menu — está dentro do loop de evento
gigante de `GameEngine.run()`, sem método isolável sem refatoração
maior (fora de escopo deste fix). Verificado por leitura comparando
com o padrão idêntico já usado por `pygame.QUIT`; validação real é
manual (usuário aprende skill, sai pelo menu "Deslogar", relogar e
confirmar que a skill continua na barra).

Fix 100% client-side (`game.py`) — nenhum arquivo de `server/` mudou,
então **sem deploy no Hetzner** (nada lá seria afetado). Só entra em
vigor pro jogador num `.exe` buildado DEPOIS deste commit — o pacote
`RPG_Online_v0.48.0.zip` gerado antes deste fix (mesma sessão, pedido
anterior) NÃO o contém; precisa de rebuild antes do próximo upload
no itch.io.

## 69. Bug real — reconectar NO MEIO do jogo nunca re-selecionava o personagem, cliente ficava "zumbi" (03/10/2026)

**Sintoma relatado pelo usuário**: quando o servidor cai, aparece
"Reconectando" na tela; depois de "reconectar", o jogo comporta-se
como se estivesse offline — personagem chega perto de um mob e ele
não agroa.

**Causa raiz**: `client/network.py::NetworkClient._connect_and_run`
já reconecta o socket com backoff exponencial E reenvia `LOGIN`
sozinho (credenciais salvas) — mas isso só refaz a PRIMEIRA metade do
fluxo de autenticação. O servidor responde com `AUTH_OK` (conta
autenticada, ainda sem personagem escolhido) — e NADA no cliente
reenviava `SELECT_CHARACTER` depois disso. Esse passo só existia no
fluxo de login/seleção INICIAL (`ui/login_screen.py`, loop próprio,
roda ANTES do `GameEngine` existir) — nunca foi replicado pra uma
reconexão NO MEIO de uma sessão já em andamento. `MsgType.AUTH_OK`
nem tinha entrada em `NetworkHandlers._NET_DISPATCH_NAMES` — durante
a gameplay, a mensagem chegava e era silenciosamente descartada por
`_handle_net_message` (`name = self._NET_DISPATCH_NAMES.get(msg_type)`
→ `None` → no-op).

Sem `SELECT_CHARACTER`, a sessão nova no servidor nunca virava uma
entidade no mundo de novo — zero `AOI_UPDATE`/`COMBAT_RESULT` chegava
pro cliente dali em diante, mesmo com o socket mostrando "conectado".
O jogo não travava de verdade porque `game.py::run()` já tinha um
timeout de 30s (`_loading_timeout`) que, sem nada acontecer, desiste
e libera a tela mesmo assim — comentário literal no código: "entra no
jogo com personagem padrão". Esse fallback sempre existiu como rede
de segurança pra UM caso bem mais raro (timeout de rede genérico),
mas na prática era o ÚNICO caminho que desbloqueava QUALQUER
reconexão no meio do jogo — nunca havia um caminho de sucesso real,
só esse fallback degradado disfarçado de "reconectou".

**Fix**: `_handle_msg_login_ok` passa a guardar
`self._selected_char_id` (o id do personagem, já presente no payload
`char`). Novo handler `_handle_msg_auth_ok`, registrado em
`_NET_DISPATCH_NAMES`, reenvia `SELECT_CHARACTER{char_id}`
automaticamente sempre que esse id já existir — o próprio sinal de
"isto é uma reconexão no meio do jogo", já que o login inicial nunca
passa por este dispatch (`ui/login_screen.py` tem seu próprio loop,
`GameEngine` nem existe ainda nesse momento). A resposta
(`LOGIN_OK` de verdade) cai no `_handle_msg_login_ok` já existente,
que já faz toda a resincronização correta (posição, HP, hotbar,
remote players limpos) — o timeout de 30s deixa de ser o único
caminho de desbloqueio, só continua como rede de segurança última.

**Teste**: `tests/test_mid_session_reconnect.py` (6) — reenvia
`SELECT_CHARACTER` com o id guardado; não reenvia nada sem id
guardado (guard defensivo); não reenvia com id negativo; não crasha
sem `self._net`; `AUTH_OK` de fato mapeado no dispatch; o dispatch
genérico (`_handle_net_message`) chama o handler de verdade, não só
a função isolada. Revert-to-confirm feito (`if False and ...`
desativando o reenvio, os 2 testes que dependem dele falharam
exatamente como esperado, restaurado). Regressão:
`tests/test_client_ui.py` (239) e
`tests/test_main_reconnect_login_fallback.py` (5, fluxo de reconexão
relacionado mas diferente — "Deslogar"/dev fast-path, ANTES do jogo
começar) sem quebra.

**Não testado automaticamente**: o fluxo fim-a-fim de verdade (matar
o processo do servidor com o cliente em jogo, esperar o backoff
reconectar, confirmar que mob volta a agroar) — precisa de um
servidor real subindo/caindo; `_handle_msg_login_ok` em si (onde
`_selected_char_id` é guardado) também não ganhou teste dedicado,
função já grande e sem cobertura prévia nenhuma, fora de escopo
refatorar isso agora só para testar 1 linha nova. Validação real é
manual: usuário mata o servidor (ou desliga a rede) com o jogo
aberto, espera reconectar, confirma que mob agroa normalmente de
novo.

100% client-side (`client/network_handlers.py`) — sem deploy no
Hetzner. Precisa de rebuild do `.exe` antes do próximo upload no
itch.io (mesmo caveat do §229 — ainda não rebuildado nesta sessão).

## 70. Bug real — luz do item (lampião) "apagava" pro observador remoto (AOI/mapa) e ficava dessincronizada ao entrar/sair da BG (04/10/2026)

**Sintoma relatado pelo usuário**, 2 partes:
1. Jogador remoto com lampião ligado sai do AOI de outro player e
   volta (ou muda de mapa) — quem observa deixa de ver a iluminação.
2. Jogador entra na BG com o lampião ligado e sai — a luz continua
   acesa no mundo, mas o ÍCONE do item mostra "apagado" e clicar não
   faz nada; só relogar corrige.

**Causa raiz (parte 1)**: `WorldServer.player_light_field(eid)` já
existe desde 23/09/2026 especificamente pra resolver "jogador que
ENTRA no campo de visão de outro já aceso" — e já era usado nos 2
pontos server-side corretos (spawn do próprio player ao logar,
`server/session.py:2182`; sweep de descoberta de outros players no
AOI por tick, `server/session.py:4169`). O problema era 100%
CLIENTE: `client/network_handlers.py::_handle_msg_aoi_update` tem seu
PRÓPRIO dict de spawn pro caminho "spawned" do `AOI_UPDATE` — o
caminho MAIS COMUM de descoberta (roda todo tick, é como o cliente
aprende sobre qualquer jogador que entra no seu AOI de novo, inclusive
depois de uma troca de mapa) — e esse dict nunca repassava os 4 campos
de luz que o servidor já mandava, mesmo o `faction` bem ao lado já
sendo repassado com o mesmo padrão condicional. `_handle_msg_entity_spawn`
(ENTITY_SPAWN, usado só por entradas pontuais) já tratava isso certo
há mais de 1 semana — os 2 caminhos deveriam ter paridade e não tinham.

**Causa raiz (parte 2)**: `inv_snapshot`/`equip_snapshot` (enviados
pelo servidor ao entrar/sair de progressão normalizada de instância,
`server/instance_progression.py`) reconstruíam o Item no cliente via
`self._item_from_data` — o mesmo formato "raw" de `BUY_RESULT`/
`TRADE_STATE`, que só copia uma lista FIXA de campos conhecidos.
`WorldServer._item_data_from_obj` (servidor, quem serializa) nunca
inclui `light_source` nessa lista — campo adicionado em 23/09/2026,
bem depois desse serializador existir, igual aos outros "campo novo
esquecido no round-trip" já catalogados várias vezes neste arquivo
(equipamento/hotbar/talentos/inventário). Resultado: o lampião
reconstruído perdia `light_source`, o ícone (que exige
`item.light_source` truthy pra mostrar aceso,
`client/inventory_handlers.py`) caía pra "apagado", e o clique (que
TAMBÉM exige `item.light_source` truthy pra rotear pro toggle) caía no
fallback de equipar em vez de alternar a luz — daí "clico e não
acontece nada". A luz em si ficava acesa porque `PlayerLightSource`
(componente separado, nunca tocado por `instance_progression.py`)
nunca mudava — só a REPRESENTAÇÃO do item no inventário local
divergia do estado real. Relogar corrige porque o login normal usa
`_restore_item` (tenta o catálogo primeiro por `item_id`), nunca
`_item_from_data`.

**Fix**:
- Parte 1: `_handle_msg_aoi_update`'s "spawned" ganha o mesmo merge
  condicional de `light_active`/`light_item_id`/`light_intensity_pct`/
  `light_radius_tiles` que `_handle_msg_entity_spawn` já tinha.
- Parte 2: `inv_snapshot`/`equip_snapshot` passam a reconstruir via
  `self._restore_item` (tenta o catálogo local por `item_id` primeiro
  — pega QUALQUER campo novo/futuro de propósito, sem precisar listar
  campo por campo nesse serializador manual; cai pro `_item_from_data`
  de sempre só quando o item não existe no catálogo local, ex: item de
  loja/instância sem equivalente — comportamento preservado).
- `BUY_RESULT`/`TRADE_STATE` (achado relacionado, NÃO corrigido agora
  — fora de escopo do relatado): usam o mesmo `_item_from_data` cru.
  "lampiao" hoje só é concedido por recompensa de quest (não é
  vendido em loja), então esse caminho específico não reproduz o bug
  na prática — mas SE algum item com `light_source` um dia for
  vendável ou negociável em trade, o mesmo problema reapareceria ali.
  Registrado aqui pro caso de alguém pedir isso depois.

**Teste**: `tests/test_client_ui.py::test_aoi_update_spawned_propaga_luz_de_item_do_player_remoto`/
`test_aoi_update_spawned_sem_luz_nao_cria_player_light_source` (2,
parte 1) + `tests/test_client_instance_sync.py::
TestInstanceInventorySyncPreservaLightSource` (3, parte 2 — lampião
preserva `light_source` via catálogo; equipamento usa o mesmo caminho;
item SEM equivalente no catálogo local continua caindo no fallback
raw, regressão). Revert-to-confirm feito nos 2 pontos (cada um
quebrado isoladamente, os testes correspondentes falharam exatamente
como esperado, restaurados). Regressão: suíte combinada
`test_client_ui.py`+`test_client_instance_sync.py`+
`test_fatiador_de_corpos.py`+`test_item_light.py` (281 testes) sem
quebra.

100% client-side (`client/network_handlers.py`) — sem deploy no
Hetzner. Precisa de rebuild do `.exe` antes do próximo upload no
itch.io.

## 71. Bug real — Fatiador de Corpos continuava causando dano/som depois do personagem morrer (04/10/2026)

**Sintoma relatado pelo usuário**: a skill Fatiador de Corpos (canal
de 5s, dano em área ao redor do guerreiro) continuava ativa mesmo
depois do personagem morrer — som e dano em quem estivesse na área.

**Causa raiz**: `WorldServer._tick`'s loop de "ticks subsequentes ao
cast" (`server/world_server.py`, bloco "Fatiador de Corpos", chamado
todo tick pra qualquer player com `CharacterStats.fatiador_timer > 0`)
nunca checava se o CASTER ainda estava vivo antes de chamar
`_fatiador_aoe_tick` — só o HP do ALVO era checado
(`ecs.current_hp > 0`, dentro de `engine/skill_handlers.py::
_fatiador_aoe_tick`). `fatiador_timer` é um campo bespoke em
`CharacterStats` (não usa o componente genérico `Channeling`) — e a
limpeza de morte já existente (`server/respawn_system.py::
_handle_player_death`, comentário "Cancela Channeling ativo — evita
dano AoE pós-morte") só olhava `Channeling`, nunca esse campo
específico. Mesma classe de bug (canal que devia morrer com o
personagem), causa raiz diferente (campo fora do componente genérico
que a limpeza já cobria).

**Fix**: `_handle_player_death` zera `CharacterStats.fatiador_timer`
no mesmo bloco onde já cancela `Channeling` — corta o canal na
próxima iteração do loop de tick, igual qualquer outro cast
interrompido por morte.

**Teste**: `tests/test_fatiador_de_corpos.py::
TestFatiadorParaDeVerdadeAoMorrer` (3) — morte zera `fatiador_timer`;
mob próximo não recebe mais dano depois da morte do caster (prova
real, não só o campo); controle de regressão SEM morte continua
causando dano normal. Revert-to-confirm feito (zeramento desativado,
os 2 testes relevantes falharam — inclusive a prova real, mob levou
dano de um guerreiro morto — restaurado). Suíte de combate/arena/BG
(94 testes) sem regressão.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — fix é
server-side (`server/respawn_system.py`).

## 72. Bug real — entregar "bem_vindo_guerreiro" com Avido Faseo reabria o diálogo de ACEITAR em vez de ENTREGAR (05/10/2026)

**Sintoma relatado pelo usuário**: pegou "bem_vindo_guerreiro" com
Caterina Alisarf (objetivo único: falar com Avido Faseo) e foi
entregar com Avido Faseo — clicar na quest abria o diálogo de aceitar
de novo, não o de entrega. Detalhe que confirmou a causa raiz: Avido
Faseo também oferece outra quest própria ("merchant_greeting") — se o
player já tivesse essa outra quest ativa ANTES, o bug desaparecia.

**Investigação**: `QuestDialogSystem._open_dialog` monta `total = comp
+ avail` e decide o estado do diálogo pelo TAMANHO dessa lista — com
só 1 item (`len(total)==1`), abre direto no estado certo (`"turnin"`
se completável, `"detail"` se disponível), usando
`_get_completable_quests(npc_id, pending_npc_name)` — que já lida
corretamente com quest completável só por `talk_to_npc` (via
`can_turn_in_after_talk`, que simula o efeito do talk SEM mutar o
`QuestLog`, porque online o cliente nunca pode confirmar esse
objetivo por conta própria — só o servidor). Com 2+ itens (Avido
Faseo tendo "merchant_greeting" disponível AO LADO de
"bem_vindo_guerreiro" completável), o diálogo abre em estado `"list"`
em vez de ir direto — e o handler de CLIQUE dentro dessa lista
(`handle_events`, bloco `_dialog_state == "list"`) decidia "turnin"
vs "detail" chamando `self._qs.can_turn_in(qid)` — a versão CRUA, que
só olha o progresso bruto salvo em `QuestLog.active`, sem o
tratamento especial de `talk_to_npc` que `_get_completable_quests`
(usado só pra MONTAR a lista, nunca reusado no clique) já tinha.
Resultado: `can_turn_in` cru sempre devolvia `False` pra essa quest
específica (o objetivo nunca é mutado de verdade no cliente online),
o clique caía no fallback `"detail"` (aceitar) — mesmo a quest já
estando na lista como completável.

Com "merchant_greeting" já ativo antes (removido de `avail`, já que
`get_available_quests` filtra quest já em `ql.active`), `total` tinha
só 1 item, pulava a lista inteira, e caía no branch que já usava a
checagem certa desde sempre — por isso o bug só aparecia com a quest
extra disponível, exatamente como o usuário descreveu.

**Fix**: o clique na lista agora recalcula com
`self._get_completable_quests(self._dialog_npc_id, npc_name)` — MESMO
critério usado pra montar a lista — em vez de `self._qs.can_turn_in(qid)`
cru.

**Teste**: `tests/test_quest_dialog_list_turnin_click.py` (4, novo) —
reproduz o cenário exato (Avido Faseo com quest completável-por-talk
+ outra disponível, diálogo abre em "list", clicar na completável
abre "turnin"; controle: clicar na disponível continua abrindo
"detail"; controle: sem a quest extra, pula a lista e já vai direto
pro "turnin" certo). Revert-to-confirm feito (`can_turn_in` cru
restaurado temporariamente, o teste principal falhou exatamente como
antes do fix — `'detail' != 'turnin'` —, restaurado). Regressão:
`tests/test_quest_split_npc.py`/`test_quest_talk_completable_priority.py`/
`test_quest_reward_ui.py`/`test_quest_logic.py`/`test_client_ui.py`
(35+247 testes) sem quebra.

100% client-side (`ui/quest_system.py`) — sem deploy no Hetzner.
Precisa de rebuild do `.exe` antes do próximo upload no itch.io.

## 73. Bug real — `Trainer.unlock_quest_id` configurado no servidor nunca chegava ao cliente (05/10/2026)

**Sintoma relatado pelo usuário**: configurou a trava de quest nova
(ver feature do dia anterior, `ARQUITETURA ONLINE.md`) no Avido Faseo
— `unlock_quest_id="merchant_greeting"` no `map_1_entities.json` — e
testou em jogo sem ter completado a quest. O menu continuou mostrando
"Treinamento" como opção liberada, igual antes da trava existir.

**Causa raiz**: NPCs de serviço (treinador/mercador/etc.) não são
entidades locais do cliente — chegam pela rede, mesmo caminho de
spawn de qualquer mob (`WorldServer._build_mob_spawn_payload`, única
função que monta esse payload, usada pelos 3 pontos de descoberta:
WORLD_STATE/ENTITY_SPAWN/AOI_UPDATE). Essa função já incluía
`payload["class_id"] = _train.class_id` quando a entidade tinha
`Trainer`, mas NUNCA incluía `unlock_quest_id` — campo adicionado no
dia anterior, só nesta MESMA função é que esqueci de propagar. O
cliente (`client/remote_entity_handlers.py::_spawn_remote_mob`)
reconstrói um `Trainer` local a partir só do que o payload mandou —
sem o campo, sempre recebia o default `""` (sem trava nenhuma), não
importa o que o servidor (autoritativo de verdade) tivesse
configurado. `ui/trainer_system.py::_compute_options` (quem decide
mostrar "Treinamento" no menu) só consulta essa cópia LOCAL, nunca o
servidor direto — daí o menu mentir pro jogador mesmo com a trava
real funcionando por trás (uma tentativa de treinar de verdade já
seria recusada por `process_train_skill`, só que o jogador nunca
chegava a tentar, porque o botão nem avisava que estava bloqueado).

Mesma classe de bug já catalogada várias vezes neste arquivo ("campo
novo esquecido num dos vários pontos que reconstroem/sincronizam
dado") — aqui achado e corrigido no MESMO dia em que o campo nasceu,
antes de qualquer jogador real notar (a loja não tem esse problema:
`SHOPS[...]["unlock_quest_id"]` é conteúdo ESTÁTICO que o cliente já
importa direto de `content/merchant_data.py`, sem precisar de sync —
só o Trainer, que é dado POR INSTÂNCIA, precisava do campo novo no
payload de rede).

**Fix**: `_build_mob_spawn_payload` passa a incluir
`payload["trainer_unlock_quest_id"]` quando o Trainer tem a trava
configurada (mesmo padrão condicional de `shop_id`/`quest_ids`
logo ali do lado); `_spawn_remote_mob` lê esse campo e repassa pro
`Trainer(...)` reconstruído localmente.

**Teste**: `tests/test_service_npcs.py` (2, servidor — payload inclui/
omite o campo conforme a trava existir) + `tests/test_client_ui.py`
(1, cliente — `_spawn_remote_mob` aplica o campo no `Trainer` local).
Revert-to-confirm feito nos 2 lados (servidor e cliente,
separadamente — cada um reproduziu o sintoma exato isolado).
Regressão: `test_service_npcs.py`/`test_client_ui.py`/
`test_npc_service_unlock.py`/`test_faction.py` (331 testes) sem
quebra.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — fix
também precisa de rebuild do `.exe` (metade client-side) antes do
próximo upload no itch.io.

## 74. Bug real — `TrainerSystem._do_learn` mandava o eid LOCAL do NPC, servidor sempre recusava como "treinador inválido" (05/10/2026)

**Sintoma relatado por tester (ERUDOM)**: tentando aprender a skill
Interceptar com Avido Faseo — a skill aparece na lista, ele clica em
"Aprender" e nada acontece. Hipótese inicial do usuário (quest
completada numa versão antiga do client) foi investigada e descartada:
conferido direto no banco de produção, `merchant_greeting` já estava
em `QuestLog.completed`, nível/ouro também batiam com os requisitos.

**Investigação**: sem nenhum log de falha no servidor pra comparar
(nenhuma das 3 ações autoritativas — compra, treino, craft — logava o
motivo de recusa), adicionei log temporário em
`WorldServer.process_train_skill` pra capturar o `reason` de cada
rejeição. Resultado real em produção: `reason=invalid_trainer`,
repetido dezenas de vezes, com o MESMO padrão mesmo depois do tester
relogar 2 vezes (eid/npc_id mudando a cada relog, sempre inválido).
Relog eliminava a hipótese de "id antigo de antes de um restart do
servidor" (cache stale) — se fosse isso, o PRIMEIRO pedido pós-relog
já teria funcionado.

**Causa raiz**: NPCs (e mobs) chegam ao cliente pela rede e são
reconstruídos localmente por
`client/remote_entity_handlers.py::_spawn_remote_mob`, que cria uma
entidade NOVA no ECS do cliente via `create_enemy()` — o eid resultante
é um contador LOCAL do cliente, **nunca** o mesmo eid que o servidor
usa pra essa entidade. O eid real do servidor fica guardado à parte, em
`RemoteEntityMeta.server_eid`, anexado à mesma entidade local. Esse
padrão já era seguido corretamente em outros pontos que precisam
mandar um alvo pro servidor (`ui/skill_system.py` ao conjurar skill
num alvo, `client/trade_handlers.py` ao abrir trade) — mas
`TrainerSystem._do_learn` (escrito no dia anterior, função nova) mandava
`self._tr_eid` (o eid LOCAL, capturado em `_open_menu` via
`world.get_entities_with(..., Trainer)`, iteração 100% local) direto
como `"npc_id"` no payload de `TRAIN_SKILL_REQUEST`. O servidor
(`process_train_skill`) faz `self.world.get_component(npc_id, Trainer)`
contra o PRÓPRIO World dele — um eid de outro espaço de numeração nunca
bate com nada ali, resultado sempre `reason="invalid_trainer"`, pra
QUALQUER player, sempre (não era específico do ERUDOM nem da trava de
quest nova — a trava nunca chegou a ser avaliada, a função recusava
antes disso). Como a `ShopSystem` nunca precisou resolver um eid de NPC
(usa `shop_id`, uma string de catálogo), esse bug ficou invisível até a
trainer ser a primeira feature de NPC a precisar do eid de verdade.

**Fix**: `_do_learn` resolve `RemoteEntityMeta.server_eid` da entidade
local ANTES de mandar o request (mesmo padrão de `skill_system.py`/
`trade_handlers.py`); sem `RemoteEntityMeta` (NPC sem origem de rede,
não deveria acontecer em modo online), não manda nada em vez de
arriscar mandar um id chutado.

**Observabilidade adicionada no mesmo commit**: `process_train_skill`
agora loga o motivo de toda rejeição (`log.info`, nível INFO,
`logs/server.log` + journal) — lacuna que atrasou bastante esta
investigação (sem isso, a única forma de saber o `reason` real era
reproduzir ao vivo com o tester enquanto eu olhava o log). Vale
replicar o mesmo padrão em `process_shop_buy`/craft se um caso parecido
aparecer de novo.

**Teste**: `tests/test_npc_service_unlock.py::
TestDoLearnMandaServerEidNaoLocal` (2, novo) — NPC local com eid
deliberadamente diferente do `server_eid` simulado, confirma que o
payload manda `server_eid`; guarda de regressão pra NPC sem
`RemoteEntityMeta` (não manda nada). Revert-to-confirm feito (fix
desfeito temporariamente, os 2 testes falharam exatamente como o bug
real — `npc_id` batendo com o eid local em vez do `server_eid` —,
restaurado). Regressão: `test_npc_service_unlock.py`/
`test_client_ui.py`/`test_service_npcs.py` (280 testes) sem quebra.

Deploy: log de diagnóstico enviado pro Hetzner
(`deploy_server.ps1`) durante a investigação. O fix de verdade é
100% client-side (`ui/trainer_system.py`) — precisa de rebuild do
`.exe` antes do tester conseguir treinar.

## 75. Bug real — item condicional de quest (drop por jogador) perdido quando o corpse tinha outro item junto (06/10/2026)

**Sintoma relatado pelo usuário**: "já tínhamos corrigido isso antes"
— quest "spider_venom" (70% de chance de dropar "Veneno de Aranha" ao
matar aranha). O item só aparecia no loot quando a aranha também
dropava outro item comum junto — e looteando o item comum PRIMEIRO, o
modal de loot fechava sozinho e o item de quest sumia, sem chance de
pegar.

**Causa raiz**: loot condicional de quest (ex: Veneno de Aranha, Pelo
de Urso — Fase L1, 25/07/2026) vive num pote SEPARADO do loot comum —
`corpse["quest_rolls"][player_eid]`, resolvido por jogador, não mais
fixo no corpse (decisão da Fase L1, pra grupo não "roubar" item de
quest de quem não tem ela). O timer pós-saque (`request_loot`,
`server/loot_processor.py`, regra "02/10/2026: saqueou tudo, expira na
hora; sobrou algo, mais 30s") decidia "saqueou tudo" olhando só o pote
COMUM (`items`/`coins`) — nunca `quest_rolls`. Com um corpse tendo 1
item comum + 1 item condicional de quest, saquear o comum esvaziava
items/coins, a condição batia "vazio", e o corpse inteiro era apagado
NA HORA (`INSTANT_EXPIRE_S`, timer=0, próximo tick remove o dict
completo de `self._corpses`) — levando junto `quest_rolls[player_eid]`,
mesmo esse ainda tendo o item de quest não reclamado. O comentário
original já documentava essa omissão como escolha deliberada ("checa
só o pote COMUM... quest_rolls não entra nessa conta") — intencional
na hora, mas sem considerar o caso de os dois potes terem conteúdo ao
mesmo tempo.

**Por que "já tínhamos corrigido antes" fazia sentido pro usuário**: a
Fase L1 (25/07/2026) resolveu um bug DIFERENTE da mesma família (loot
condicional vazando pra quem não tinha a quest); essa correção criou o
pote `quest_rolls` separado, que nunca foi integrado na lógica de
expiração de timer escrita DEPOIS (02/10/2026) — regressão nova sobre
terreno já mexido antes, não o mesmo bug voltando.

**Fix**: `request_loot` calcula `_personal_pending = any(corpse.get(
"quest_rolls", {}).values())` (qualquer jogador do grupo com item
condicional ainda não reclamado, não só quem acabou de saquear agora)
e inclui isso na condição de `INSTANT_EXPIRE_S` — só expira na hora se
os TRÊS potes (comum + coins + qualquer quest_rolls de qualquer
jogador) estiverem vazios.

**Teste**: `tests/test_loot_authoritative.py::
TestCorpseTimerRespeitaLootCondicionalDeQuest` (2, novo) — corpse com 1
item comum + 1 item condicional de quest ("veneno_aranha",
`content.quests_data.QUEST_ITEMS`), saquear só o comum não expira
(timer=30s, sobrevive ao tick seguinte, item de quest continua
lootável de verdade); controle — saquear os dois aí sim expira na hora
(timer=0). Revert-to-confirm feito (checagem de `quest_rolls`
removida, o teste principal falhou exatamente como o bug real — timer
0.0 em vez de 30.0 —, restaurado). Regressão:
`test_loot_authoritative.py` (exceto 2 testes de
`TestLootQuestProgressViaSessionFlow`, falha pré-existente de ambiente
— `ConnectionRefusedError`, confirmada via `git stash` que já falhava
ANTES desta mudança, não relacionada) + `test_quest_logic.py` (29
testes) sem quebra.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — fix é
100% server-side (`server/loot_processor.py`), sem rebuild de `.exe`
necessário.

## 76. Bug real — mob com loot comum raro quase nunca criava corpse, então o sorteio de item de quest condicional (70%) quase nunca rodava (06/10/2026)

**Sintoma relatado pelo usuário**, ao questionar o fix do §236: "a
chance de drop estava bem baixa, o que é estranho pois a chance de
drop é de 70%... só dropava o item da quest quando outro item dropava
junto". Confirmado como bug REAL, distinto (mas na mesma investigação)
do §236.

**Causa raiz**: loot condicional de quest (`quest_rolls`, Fase L1
25/07/2026 — ex: "Veneno de Aranha" da quest `spider_venom`, 70% de
chance) só é sorteado DEPOIS que um corpse já existe
(`_resolve_conditional_loot_for`, chamado só quando há corpse pra
anexar o sorteio). `_process_loot_drops` só cria corpse quando o mob
dropa algo da tabela de loot COMUM dele (`not items and coins <= 0:
continue`, decisão de 02/10/2026: "sem loot nenhum, sem corpo"). A
Aranha (`content/mob_definitions.py`) tem `gold_chance=0.0` e uma
tabela de item comum com chances baixas (2-5% cada,
`padded_gloves`/`cloth_wrists`/`worn_hood`) — na prática só ~10% das
mortes geravam corpse NENHUM. Como o sorteio dos 70% só acontece
DENTRO de um corpse já existente, a chance EFETIVA observada do item
de quest era ~10% × 70% ≈ 7%, não os 70% documentados — o sorteio
simplesmente nunca chegava a rodar na maioria das mortes.

**Fix** (confirmado com o usuário via pergunta direta — mudava uma
decisão de produto anterior, 02/10/2026, "corpo vazio não aparece"):
`_process_loot_drops` agora cria o corpse mesmo com loot comum vazio
quando o dono do kill (ou algum membro do MESMO grupo — loot
condicional é free-for-all dentro do grupo, igual o resto) tem uma
quest ativa com objetivo `collect_item` elegível pra este mob
(`WorldServer._any_party_member_awaits_conditional_loot`, novo). O
sorteio de verdade continua acontecendo depois, lazy, igual sempre —
o corpse pode abrir vazio se os 70% falharem (decisão explícita do
usuário: é o preço de 70% valerem 70% de verdade).

**Ponto único de verdade novo**: `engine.quest_logic.
_eligible_conditional_objectives(ql, enemy_name, enemy_race)` —
extraído de `roll_conditional_loot` (que agora só itera e sorteia),
reusado por `has_pending_conditional_loot(ql, enemy_name, enemy_race)`
(checa elegibilidade SEM consumir o RNG). Mob sem NENHUMA quest no
jogo inteiro que o aceite continua sem gerar corpse vazio — o custo do
fix é só avaliar `QuestLog.active` do dono/grupo, nunca varre o
catálogo de quests inteiro por mob.

**Teste**: `tests/test_loot_authoritative.py::
TestCorpseNasceParaLootCondicionalSemLootComum` (4, novo) — sem quest
ativa continua sem criar corpse (controle, preserva a regra de
02/10/2026); com `spider_venom` ativa cria corpse mesmo com
items=[]/coins=0; quest ativa num MEMBRO DO GRUPO (não o dono do kill)
também força o corpse; objetivo já completo (`prog >= count`) NÃO
força corpse. Revert-to-confirm feito (checagem nova removida, 2 dos 4
testes falharam exatamente como o bug real — corpse não nascia com
quest ativa —, restaurado). Regressão:
`test_loot_authoritative.py`/`test_quest_logic.py`/`test_party.py` (55
testes, exceto os 2 de `TestLootQuestProgressViaSessionFlow` já
catalogados como falha de ambiente pré-existente no §236) sem quebra.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — fix é
100% server-side (`server/loot_processor.py` + `engine/quest_logic.py`),
sem rebuild de `.exe` necessário.

## 77. Bug real — personagem "trava" e sofre rollback perto de um poste, num loop (06/10/2026)

**Sintoma relatado pelo usuário**: andando (clique no chão) perto de um
poste de iluminação do cemitério (mapa 1), o personagem fica num loop —
anda um pouco, volta pra mesma posição, tenta chegar de novo, volta de
novo — até "travar" visualmente ali, com o servidor repetidamente
corrigindo (rollback) a posição.

**Investigação**: diagnosticado com as ferramentas de debug já
existentes de uma investigação anterior (§126, `debug/
interceptar_debug.py`, apesar do nome — cobre QUALQUER correção de
posição do próprio player, não só dash), já ativas em produção
(`RPG_DEBUG_INTERCEPTAR=1`/`RPG_DEBUG_AOI=1`). Log real capturado:
primeiro passo da rota (diagonal, cortando perto do poste) recusado
como `MOVE_REJECTED_WALKABLE`; **hipótese inicial errada, corrigida
pelo usuário** — cheguei a supor "ocupação passageira de outra
entidade" sem confirmar; usuário testou repetidas vezes com o tile
visivelmente livre no cliente e pediu investigação real em vez de
suposição (ver nota de processo abaixo). Confirmado por carregamento
direto do mapa pelo código real do jogo (não inferência de CSV cru):
mapa idêntico entre cliente e servidor (checksum), tile de destino não
é sólido, mesma elevação, sem transição — a causa exata da 1ª recusa
não foi finalizada (user priorizou resolver o loop em vez de
continuar essa ponta), mas **o mecanismo do loop em si foi confirmado
com certeza via log**: a cada recusa, o `PlayerAutoMove` do cliente
(`ui/player_input_system.py::_process_ground_move`) nunca ficava
sabendo — só olha se a animação LOCAL terminou (`tile_movement.
is_moving`), nunca se o servidor realmente aceitou o passo anterior.
Resultado: a cada `path_recalc_timer` (recalcula a rota do zero), o
mesmo primeiro passo (ainda bloqueado) era tentado de novo, recusado
de novo, num loop — até a correção de posição (`DESYNC_CORR`,
`client/network_handlers.py::_handle_msg_entity_move`) sincronizar a
posição de volta (o "rollback" visível), sem nunca parar o
`PlayerAutoMove` pendente que ia gerar o PRÓXIMO recalculo idêntico.

**Nota de processo**: durante a investigação, apresentei "ocupação
passageira" como explicação antes de verificar — usuário corrigiu
("não quero que você tire conclusões sem me perguntar"). Lição: só
checar dado real (log do cliente, não só do servidor) ou ADMITIR que é
hipótese não confirmada, nunca apresentar suposição como diagnóstico
fechado. Ver [[feedback-dont-generalize-from-biased-repro-data]] /
[[feedback-verify-data-assumptions-before-concluding]] (memória).

**Fix, escopo decidido explicitamente pelo usuário** ("prefiro que o
servidor recuse o caminho e o client pare de andar do que ficar com
esse loop" — não pediu a causa raiz da 1ª recusa, só o fim do loop):
`_handle_msg_entity_move`, no ramo que já aplica a correção de posição
pro MOVE normal (não-dash, gap genuíno — mesmo ramo do fix do §126),
agora também limpa `PlayerAutoMove.path`/`ground_target`/`active` do
player local — mesmo reset que `_process_ground_move` já faz sozinho
ao "chegar" no destino. Efeito: 1 recusa = o personagem para (precisa
clicar de novo pra tentar ir até lá), nunca mais um loop de
recusa→recalcula→recusa de novo. Ramo "correção ignorada" (gap<=1,
ainda em movimento — considerado possivelmente desatualizado, mesmo
critério de antes) continua sem tocar em nada, dash continua sem
mudança de comportamento (só ganha o mesmo reset de auto-move de
brinde, que não tinha efeito prático nesse caminho).

**Causa raiz da 1ª recusa (walkable), NÃO resolvida nesta tarefa**:
fora de escopo por decisão do usuário — fica para investigação futura,
se o padrão se repetir perto de outros objetos. Hipótese a checar
primeiro, se retomado: `PathfindingSystem.find_path` (A*,
`engine/world_systems.py`) NÃO tem nenhuma regra de "não cortar quina
diagonal perto de obstáculo sólido" — só olha se o tile DESTINO da
diagonal é sólido, nunca os 2 tiles ortogonais do "canto". Se
`TileValidationSystem.is_tile_walkable` (usado pela validação real do
`move_player`) tiver essa regra em algum ponto não encontrado nesta
investigação, path cliente e validação servidor podem divergir
exatamente nesse tipo de diagonal — mas isso NÃO foi confirmado
empiricamente, só fica registrado como pista.

**Teste**: `tests/test_client_ui.py::
test_correcao_de_move_normal_com_gap_grande_cancela_auto_move_pendente`
(novo) — correção aplicada (gap>1) zera `PlayerAutoMove` pendente;
`test_correcao_de_move_normal_ignorada_nao_mexe_no_auto_move` (novo,
controle) — correção IGNORADA (gap<=1, ainda em movimento) não mexe em
nada, nem posição nem auto-move. Revert-to-confirm feito (reset do
auto-move desativado, o teste principal falhou exatamente como o loop
real — `active` continuava `True` depois da correção —, restaurado).
Regressão: `test_client_ui.py` completo (238 testes) sem quebra.

Deploy: fix é 100% client-side (`client/network_handlers.py`) — precisa
de rebuild do `.exe` antes de chegar a quem já tem o jogo instalado.

## 78. `AttributeError: _show_ranking` ao abrir o client — crash reportado pelo usuário (06/10/2026)

**Sintoma**: usuário tentou abrir o client logo após o build da feature
de Ranking Geral (§26, ARQUITETURA ONLINE.md) e o jogo nunca chegou a
abrir — traceback no terminal, `'GameEngine' object has no attribute
'_show_ranking'`, na primeira leitura da flag no loop de render.

**Causa raiz**: implementei `self._show_ranking` seguindo o padrão de
`_show_skills`/`_show_char_stats`, mas só copiei o ponto onde essas
flags são RESETADAS (`_close_all_modals()`, que assume que a flag já
existe) — nunca o ponto onde são DECLARADAS de verdade pela primeira
vez (`__init__`, perto de `_show_habilidades`/`_show_skills`, linha
~457). Como `_close_all_modals()` só roda em resposta a uma tecla
pressionada, e o loop de render lê `self._show_ranking` todo frame
desde o início, o crash acontecia ANTES de qualquer tecla ser
apertada — na primeira iteração do loop.

**Por que não foi pego antes de chegar no usuário**: toda a verificação
desta feature (testes automatizados + smoke test manual) ficou restrita
a `ui/ranking_system.py` isolado e ao fixture mínimo de
`_make_modal_stack_fixture` — nunca cheguei a instanciar o `GameEngine`
real nem rodar `python main.py` de verdade antes de entregar. Nenhum
teste automatizado cobre (nem deveria tentar cobrir sozinho, dado o
custo) a inicialização completa do `GameEngine` — a única forma real de
pegar este tipo de gap é efetivamente abrir o jogo, que é exatamente o
passo que ficou pulado.

**Fix**: `self._show_ranking: bool = False` adicionado no `__init__`,
mesma linha de vizinhança de `_show_skills`/`_show_char_stats`.

**Lição a aplicar daqui pra frente**: ao adicionar um novo `_show_X`
(ou qualquer flag lida incondicionalmente no loop de render/eventos),
sempre verificar que existem OS DOIS pontos — declaração real no
`__init__` E reset em `_close_all_modals()` — nunca só copiar o
segundo achando que já cobre o primeiro. Idealmente, rodar
`python main.py` pelo menos até a tela de jogo (não só os testes
automatizados) antes de reportar uma feature de UI como pronta — gap
já registrado nas instruções do projeto, mas pulado nesta entrega.

Deploy: fix é 100% client-side (`game.py`) — precisa de rebuild do
`.exe` antes de chegar a quem já tem o `.exe` v0.51.0 instalado
(quem roda `python main.py` direto do código-fonte já recebe o fix
só com o commit, sem rebuild).

## 79. Roda do mouse com modal de Ranking aberto também zoomava a câmera por trás (06/10/2026)

**Sintoma relatado pelo usuário**: rolar o mouse pra descer a lista do
Ranking Geral também mudava o zoom da câmera do mundo, por trás do
modal.

**Causa raiz**: `game.py::run()` tem 1 loop de eventos com um `elif`
pra `MOUSEWHEEL` — só 2 casos tinham tratamento especial antes do zoom
(`_show_debug` e `_handle_chat_scroll`); QUALQUER outro modal com
scroll próprio (Ranking, e em tese também o Treinador — mesmo
mecanismo de `MOUSEWHEEL` só que nunca comparado lado a lado antes)
nunca entrava nesse `elif`, então o MESMO evento de roda chegava ao
zoom da câmera E, mais abaixo no mesmo `run()`, ao `handle_events()` do
modal aberto — os dois reagiam ao mesmo scroll ao mesmo tempo.

**Fix**: `elif event.type == pygame.MOUSEWHEEL and not self.
_any_modal_open():` — reusa o mesmo ponto único de verdade já usado
por outro bloqueio de input de fundo no mesmo arquivo (clique no
minimap atravessando um modal por cima, achado antes). Zoom da câmera
nunca deveria reagir com QUALQUER modal cobrindo a tela, não só os 2
que já tinham tratamento nomeado — corrige Ranking de propósito e,
de quebra, a mesma classe de gap pra qualquer modal futuro com scroll
próprio, sem precisar listar cada um à mão.

**Teste**: `tests/test_client_ui.py::
test_any_modal_open_true_com_ranking_aberto` (novo) — cobre a PARTE
testável fora do loop de eventos real (`_any_modal_open()` reconhece
`_show_ranking`); o comportamento completo (zoom realmente suprimido
durante o scroll) depende do loop de eventos do `GameEngine` rodando
de verdade, fora do que dá pra automatizar sem instanciar o jogo
inteiro — validado por leitura cuidadosa do código + boot smoke-test
(`python main.py` sobe sem erro), mas a confirmação final é manual do
usuário em jogo.

Deploy: fix é 100% client-side (`game.py`) — precisa de rebuild do
`.exe` antes de chegar a quem já tem o `.exe` instalado (quem roda
`python main.py` direto do código-fonte já recebe o fix só com o
commit).

## 80. NPC/mob "travado" numa posição velha, nunca na posição real do servidor (06/10/2026)

**Sintoma relatado pelo usuário, ao vivo**: NPC "Custodio Benevide"
visualmente parado no tile (133,358); mapa declara o spawn dele em
(127,361) — confirmado via `map_1_entities.json`, não é "NPC parado no
lugar certo" (comportamento normal, NPC nunca anda), é posição ERRADA
de verdade. Zumbi "travado" em (128,349) relatado junto — log do
servidor (`AUTO_ATK_RANGE_OK`) confirma que o COMBATE contra ele
funciona normalmente naquele tile, então o servidor concorda com
aquela posição — a dúvida do usuário era exatamente "cliente trava a
posição ou servidor para de atualizar", e a resposta é nenhum dos dois:
o servidor está certo, o CLIENTE que nunca criou a entidade nova.

**Causa raiz**: eid de entidade NÃO é estável entre reinícios do
servidor (mundo reconstrói do zero a cada boot — NPCs/mobs ganham eids
NOVOS, já documentado em §238/§240 pra OUTRO sintoma). `client/
remote_entity_handlers.py::_spawn_remote_mob` pula silenciosamente
(`SPAWN_SKIP`) qualquer `server_eid` que já exista em
`self._remote_mobs` — mas a reconexão mid-sessão (`_handle_msg_auth_ok`,
§230, 03/10/2026) NUNCA limpava esse dict antes de reenviar
`SELECT_CHARACTER` (só `_do_transition`, troca de mapa de verdade,
fazia essa limpeza). Depois de QUALQUER reconexão (servidor reiniciado,
ex: um deploy — esta sessão fez vários hoje — ou só uma queda de rede),
o `WORLD_STATE` novo chega com eids NOVOS; se um eid novo colide por
acaso com uma chave VELHA do dict stale, `_spawn_remote_mob` acha que
"já tem essa entidade" e NUNCA cria a de verdade — o que fica na tela é
a entidade ANTIGA, na posição de ANTES do restart, para sempre (NPC
nunca mais se move por natureza; mob só "autocorrige" se por acaso
andar de novo, que pode nunca acontecer).

**Fix**: `_handle_msg_auth_ok` passa a chamar
`_remove_all_remote_entities(keep_tilemap=True)` +
`_clear_remote_entity_caches()` ANTES de reenviar `SELECT_CHARACTER` —
mesmos 2 métodos que `_do_transition()` já usava pra troca de mapa de
verdade, só que preservando o Tilemap local (reconexão é pro MESMO
mapa, terreno continua válido). `_remove_all_remote_entities` (novo,
extraído de dentro de `_do_transition`) e `_clear_remote_entity_caches`
(já existia) ficam SEPARADOS de propósito — um teste já existente
(`TestClearRemoteEntityCaches`) testa só a parte de dicts com um objeto
fake sem `world`/`player_entity`; fundir os dois quebraria esse teste.

**Lição repetida nesta sessão** (mesma classe do §239 — atributo
usado sem checar quem mais chama o método): ao adicionar a chamada
nova em `_handle_msg_auth_ok`, o teste já existente
(`tests/test_mid_session_reconnect.py`) usa um `_Fixture` que herda SÓ
de `NetworkHandlers`, nunca de `GameEngine` — os métodos novos nem
EXISTIAM nesse fixture (não é só atributo faltando, é o método inteiro,
já que `_remove_all_remote_entities`/`_clear_remote_entity_caches`
vivem em `game.py`, não em `client/network_handlers.py`). Pego ANTES de
chegar no usuário desta vez — rodei a suíte afetada antes de reportar
pronto, diferente do §239.

**Limitação conhecida**: o fix previne o problema em QUALQUER
reconexão FUTURA — não corrige, sozinho, uma entidade que JÁ ficou
stale numa sessão aberta antes do deploy (precisa de 1 relog/reconexão
pra aplicar a limpeza nova).

**Teste**: `tests/test_map_transition_screen.py::
TestRemoveAllRemoteEntities` (2, novo — `keep_tilemap=True` preserva
Tilemap e remove o resto; `keep_tilemap=False`, default, remove tudo
inclusive Tilemap) + `tests/test_mid_session_reconnect.py::
test_limpa_entidades_remotas_velhas_antes_de_reselecionar` (novo —
confirma que `_handle_msg_auth_ok` chama os 2 métodos, nesta ordem,
antes de reenviar `SELECT_CHARACTER`). Revert-to-confirm feito nos 2
testes de `TestRemoveAllRemoteEntities` (loop de remoção desativado, os
2 falharam exatamente como o bug real — entidade velha continuava no
World —, restaurado). Regressão: `test_map_transition_screen.py`/
`test_mid_session_reconnect.py`/`test_client_ui.py` (264 testes) sem
quebra. Boot smoke-test (`python main.py`) sem erro.

**CORREÇÃO (06/10/2026, mesmo dia — a causa raiz acima estava
ERRADA)**: usuário testou com um RESTART COMPLETO do processo
(`python main.py` fechado e reaberto do zero, confirmado
explicitamente) — garante `_remote_mobs` 100% vazio, zero
possibilidade de staleness de cliente. O bug persistiu mesmo assim,
derrubando a hipótese acima. O fix de `_handle_msg_auth_ok` continua
válido e testado (resolve reconexão NO MEIO da sessão, cenário
diferente), só não era a causa DESTE sintoma. Causa raiz de verdade:
**§242**. Diagnóstico real feito com log direto no servidor
(`_build_mob_spawn_payload`, filtrado por nome "custodio") confirmando
que a posição broadcastada pelo servidor SEMPRE bateu com a realidade
— nunca houve desync de posição/broadcast; o NPC realmente estava
onde o servidor dizia, só que longe de casa, travado numa luta que
nunca terminava. Lição: a hipótese "cliente com cache velho" parecia
plausível e tinha um mecanismo real por trás (SPAWN_SKIP), mas nunca
foi confirmada com prova direta antes de ser reportada como corrigida
— só depois do usuário contestar com um teste mais forte (restart
completo) que a investigação voltou a fontes primárias (log do
servidor) em vez de insistir na mesma teoria.

Deploy: fix é 100% client-side (`game.py`, `client/
network_handlers.py`) — precisa de rebuild do `.exe` antes de chegar a
quem já tem o `.exe` instalado.

## 81. Log de diagnóstico (do próprio §242) derrubava o servidor inteiro a cada ciclo de RETURNING (06/10/2026)

**Contexto**: depois do fix do §242, usuário reportou um sintoma NOVO
— "o Custódio já está travado, ele andou só um tile pro lado e isso já
fez ele travar, quando um mob passa perto ele não reage". Para
investigar, adicionei um log temporário (`RETURNING_STEP`) dentro do
bloco de pathfinding de RETURNING (`engine/world_systems.py`),
chamando `debug.aoi_debug.AOI_DBG.log(...)` com a MESMA sintaxe
posicional que `debug.mob_combat_debug`'s `_MCL.log(event, eid, name,
race, cls, **kwargs)` aceita — só que `AOI_DBG.log()` tem assinatura
DIFERENTE (`log(self, event: str, **kwargs)`, só kwargs depois do
evento, ver `debug/aoi_debug.py`). Resultado: `TypeError: log() takes
2 positional arguments but 6 were given`, levantado TODA vez que
QUALQUER entidade com "custodio" no nome entrava em `RETURNING`.

**Efeito real, bem mais grave que um log quebrado**: essa exceção não
era pega pelo try/except de `WorldServer._run_tick_or_sleep` (que
RESETA deltas e loga "ERRO no tick X" — zero ocorrências encontradas
em `logs/server.log`/`server_stderr.log`/`server_stdout.log`,
confirmando que não foi esse o caminho) — o processo Python inteiro
morria, e o `systemd` (`Restart=always`, `RestartSec=5`,
`rpg-server.service`) reiniciava sozinho em ~5s. Confirmado nos logs:
`[Session] entrou no jogo...` normal até `22:40:56`, nada de erro
visível, e às `22:40:59.825` aparece `[Auth] banco inicializado` — a
sequência INTEIRA de boot do zero — exatamente ~2.3s depois do
`LOST_TGT ret=RETURNING` do Custódio às `22:40:57.531`. Cada restart
reconstrói o `World` do zero (mundo ECS inteiro, não só o NPC) —
explica tanto "Custódio travado" (congelado pelo processo matando no
meio do movimento, mundo reconstruído) quanto "mob perto não reage"
(toda a IA do servidor, não só a dele, para e recomeça do zero a cada
restart). O `RestartSec=5` disfarça isso de uma reconexão rápida
comum, nunca parecendo uma queda de verdade pro jogador.

**Lição**: diagnóstico temporário em produção, mesmo "só um log a
mais", tem o mesmo risco de qualquer código novo — precisa ser testado
antes de confiar nele (aqui, bastava rodar localmente 1x com
`RPG_DEBUG_AOI=1` e forçar o branch de RETURNING pra pegar o erro
antes do deploy; não foi feito, por ser "só diagnóstico"). Daqui pra
frente: qualquer log novo que usa `AOI_DBG`/`MCL`/qualquer logger do
projeto precisa confirmar a ASSINATURA EXATA daquele logger específico
antes de copiar o padrão de uma chamada vizinha que usa um logger
DIFERENTE — os dois loggers deste projeto têm assinaturas diferentes
de propósito (`MCL.log(event, eid, name, race, cls, **kwargs)` vs
`AOI_DBG.log(event, **kwargs)`), e não há nada no nome/uso que avise
isso visualmente.

**Fix**: removidos os 2 logs de diagnóstico temporários inteiros
(`RETURNING_STEP` em `engine/world_systems.py` e o
`MOB_SPAWN_PAYLOAD_NAMED` irmão em `server/world_server.py`'s
`_build_mob_spawn_payload`, já tinham cumprido o papel de confirmar
que a posição do servidor sempre esteve correta) — nenhum dos dois é
mais necessário, a causa real (§242) já está corrigida e testada.

**Consequência pra investigação do §242**: o sintoma "andou 1 tile e já
travou" relatado pelo usuário DEPOIS do fix provavelmente era só este
crash-loop (mundo resetando no meio do retorno pro spawn), não uma
falha nova do fix de leash em si — mas isso só pode ser confirmado de
verdade com um teste limpo, sem o log quebrado, depois deste deploy.

## 82. Causa raiz de verdade do travamento (§241/§242) — spawn do Custódio caía em cima de parede sólida, pathfinding de volta nunca podia ter sucesso (07/10/2026)

**Sintoma, depois do fix do §242 (que estava certo, mas não era
suficiente)**: Custódio continuava travando — inclusive bem na frente
do usuário, logo depois de atacar um mob (não mais um problema de sair
da tela/AOI, confirmado pelo próprio usuário testando isso
explicitamente). Log de diagnóstico corrigido (desta vez testado
localmente antes do deploy, sem crashar) confirmou: ele entrava em
`RETURNING` normalmente, `is_blocked=False`, `is_moving=False`, mas
`ai_control.path` ficava **permanentemente vazio** a cada ciclo de
recálculo (confirmado no momento exato em que `path_recalc_timer`
resetava pra `0.80`, ou seja, uma busca de caminho NOVA de verdade
tinha acabado de rodar — não era caso de orçamento de pathfinding
esgotado).

**Causa raiz real**: `maps/map_1_entities.json` declarava Custódio em
`x=127, y=361`. Conferido direto no `maps/map_1.csv`: essa linha é
`"########....###"` a partir da coluna 120 — coluna 127 cai
EXATAMENTE em cima de um tile `"#"` (`WALL_TILE`, "Parede da cidade",
`engine/tileset.py:329`), o poste de muro bem ao lado do vão de porta
real (colunas 128-131, terreno `"."` = `STONE_FLOOR`, chão andável).
Confirmado também no `object_matrix` (`vp_216` nessa coluna — poste de
iluminação, decoração do portão). Entidade nasce ali porque a criação
inicial (`_build_combat_entity`) grava `Position`/`TileMovement`
DIRETO, sem checar `is_tile_walkable` — spawn estático nunca passa por
pathfinding. Mas assim que ele sai (persegue um mob) e depois precisa
VOLTAR via A* (`_find_path_budgeted`→`find_path`), não existe — e
nunca vai existir — nenhuma rota válida terminando num tile sólido.
`find_path` sempre retorna vazio, pra sempre, em QUALQUER distância,
de QUALQUER posição — não é timing, não é occupancy, não é budget, é
estrutural: o destino do caminho é impossível por definição.

Isso explica de forma completa e consistente TUDO que foi observado
nesta investigação (§241→§242→§243→§244): por que alguns mobs/NPCs
voltam pro spawn normalmente e o Custódio especificamente nunca
voltava (casa dele é a única em cima de parede); por que ele trava em
posições/distâncias completamente diferentes a cada repetição (ele só
para onde quer que esteja no momento em que perde o alvo — nunca
consegue dar nem 1 passo de volta); e por que esse travamento nunca
tinha relação com reconexão, AOI, ou crash (as 3 hipóteses anteriores,
cada uma investigada e descartada com prova — client cache em §241,
leash em §242, crash-loop do próprio log em §243).

**Fix**: usuário andou até o local pessoalmente pra confirmar visual
(achado dele: "o tile onde Custódio está tem um poste de iluminação")
e corrigiu o spawn direto em `maps/map_1_entities.json` para
`x=128, y=362` (entrada do cemitério, terreno `"."`/`STONE_FLOOR`
confirmado andável, sem objeto bloqueando). Dado de conteúdo puro —
zero mudança de código pra esse fix específico (os logs de diagnóstico
do §243/§244, já tendo cumprido o papel, foram removidos no mesmo
commit).

**Lição pra qualquer entidade estática futura (NPC/mob fixo em
mapa)**: posição declarada em `*_entities.json` precisa ser verificada
contra o terreno real (`is_tile_walkable`/terreno não-sólido) no
momento da autoria — nada no pipeline de carga (`map_loader.py`,
`entity_factory.py`) valida isso automaticamente hoje, então um spawn
em cima de parede nasce e PARECE normal (a entidade aparece parada no
lugar certo) até o dia em que ela precisa se mover e voltar — o bug
fica invisível por tempo arbitrário até alguém puxar a entidade pra
longe de casa pela primeira vez.

## 83. Mercador "Fabian Hardek" sem marcador "?" e sem reação a clique direito, depois do fix de NPCUnlock (06/10/2026)

**Sintoma relatado pelo usuário**: depois do deploy da trava genérica
de quest (`NPCUnlock`, ver decisão em `ARQUITETURA ONLINE.md`), o
Fabian Hardek continuava sem o marcador "?" de quest acima dele, e
clicar com o direito nele não abria nada (nem loja, nem diálogo de
quest) — nenhum feedback visível.

**Causa raiz**: `create_merchant`/`create_trainer`
(`engine/entity_factory.py`) só anexavam `QuestGiver` quando
`quest_ids` (quests que o NPC DÁ) era não-vazio —
`if quest_ids: world.add_component(eid, QuestGiver(...))`. O Fabian é
exatamente o caso "só aceita ENTREGA de uma quest dada por outro NPC"
— `turn_in_ids=["merchant_greeting"]` no JSON, `quest_ids` ausente
(vazio). Com o guard antigo, `QuestGiver` nunca nascia nele, então
`quest_logic` (genérico, já corrigido para ler `QuestGiver` de
QUALQUER NPC) não tinha nada pra ler — zero quest disponível/
completável, nenhum marcador, e `QuestDialogSystem._open_dialog`
(chamado quando `ShopSystem` redireciona um mercador travado) só
loga uma mensagem de chat ("Sem missões para você no momento") e
retorna sem abrir nada quando a lista de quests está vazia — por
isso "nada acontece" visualmente ao clicar.

O teste escrito na tarefa anterior (`test_create_merchant_com_
turn_in_ids_anexa_questgiver`) não pegou esse caso porque passava
`quest_ids` E `turn_in_ids` juntos — nunca exercitou o cenário real do
Fabian (só `turn_in_ids`).

**Fix**: guard trocado para `if quest_ids or turn_in_ids:` em
`create_merchant` E `create_trainer` (mesmo bug copiado nos dois) —
`QuestGiver` agora nasce sempre que o NPC tem QUALQUER papel de quest,
dar OU aceitar entrega.

**Teste**: `tests/test_npc_service_unlock.py::TestMerchantComQuestGiver::
test_create_merchant_so_com_turn_in_ids_sem_quest_ids_anexa_questgiver`
(novo) — só `turn_in_ids`, sem `quest_ids`, replicando a config real do
Fabian. Revert-to-confirm: com o guard antigo restaurado
(`git stash` só de `entity_factory.py`), o teste falha exatamente como
o bug relatado (`QuestGiver` ausente); com o fix, passa. Suíte
relacionada (307 testes) sem regressão.

## 84. Contorno de seleção de alvo quase invisível — linha em cima da borda, metade escondida (07/10/2026)

**Sintoma relatado pelo usuário**: ao selecionar um alvo, o contorno
colorido aparecia NA FRENTE do sprite e como uma linha fina em cima da
própria borda — "quero que fique atrás, e que seja externo não
interno, se não, não vai aparecer".

**Causa raiz**: o mecanismo (`RenderSystem.render()`,
`ui/render_system.py`) traçava a silhueta exata do sprite via
`pygame.mask.Mask.outline(3)` e desenhava essa linha com
`pygame.draw.polygon(..., width=2)` DEPOIS do blit do sprite. Uma linha
de 2px centrada bem na fronteira da máscara fica ~metade DENTRO da área
que já é opaca (desenhada por cima do próprio sprite, cortando a arte
na borda) e a outra metade fora, fina e fácil de perder contra o fundo
do mapa — resultado: contorno quase invisível e visualmente "colado"
na frente do personagem, exatamente o sintoma relatado.

**Fix**: `RenderSystem._get_selection_ring_mask(surf, radius=2)` —
dilata a máscara original por um raio circular de offsets
(`Mask.draw(base_mask, (dx,dy))` pra todo `dx²+dy²<=radius²`) e depois
REMOVE a silhueta original (`Mask.erase(base_mask, (0,0))`), sobrando
só o "halo" externo, sem nenhum pixel em comum com o sprite — provado
antes de implementar com um script de verificação de pixel isolado, e
depois formalizado em teste (`overlap_area(base, ring) == 0`). Como o
anel nunca sobrepõe o sprite, a ORDEM de desenho (antes/depois do blit)
deixou de ter qualquer efeito sobre a correção — ficou depois só porque
já estava naquela posição no código. Desenhado via
`ring_mask.to_surface(setcolor=..., unsetcolor=(0,0,0,0))` + `blit()`,
não mais `draw.polygon`. Cache por `id(surf)`
(`self._selection_ring_mask_cache`, mesmo padrão de
`self._occlusion_mask_cache`). Fallback de retângulo (mob sem
sprite/frame resolvível) inalterado.

**Teste**: `tests/test_selection_outline.py` reescrito — nova classe
`TestSelectionRingMask` (4 testes: anel não-vazio pra sprite real, anel
nunca sobrepõe a silhueta original, pixel fora do raio de dilatação
fica de fora, cache devolve a mesma instância) + 2 testes de integração
reescritos pra confirmar que nem o mecanismo antigo
(`draw.polygon`) nem o fallback de retângulo disparam mais pra
sprite/frame reais. `pygame.Surface.blit` não é monkeypatchável em
pygame-ce (confirmado: "cannot set 'blit' attribute of immutable type
'pygame.surface.Surface'") — por isso a prova do caminho novo é
indireta (ausência dos 2 caminhos antigos) combinada com o teste de
unidade direto sobre `_get_selection_ring_mask`. Revert-to-confirm no
`Mask.erase()`: substituído temporariamente por `pass`, o teste de
não-sobreposição falhou com `overlap == 100` (área cheia do quadrado
original), restaurado, 7/7 passando. Regressão focada (422 testes:
render/client-ui/character-animation/nameplate/fog-cache/object-scan/
portal) — zero falhas novas. Mudança 100% client-side (nenhum arquivo
de `server/` tocado).

## 85. Launcher autenticava mas o client pedia login de novo — credencial nunca repassada (08/10/2026)

**Bug real relatado pelo usuário**: "quando eu logo no launcher, está
pedindo para logar novamente no client" — login no Launcher tinha
sucesso (`/launcher/login` confirmava), mas ao abrir `RPG_Online.exe` a
tela de login normal aparecia de novo, pedindo usuário/senha.

**Causa raiz**: `launcher/update_client.py::launch_game()` só fazia
`subprocess.Popen([exe_path], ...)` com a env var de autorização
(`RPG_LAUNCHED_BY_LAUNCHER=1`) — nunca repassava a credencial validada
pro processo do jogo. O Launcher nunca guarda a senha crua (só o hash
SHA-256, mesmo usado por "Lembrar login"), então não dava pra
simplesmente reenviar a senha original.

**Fix**: `password_hash` (nunca a senha crua) passa a viajar via env
vars novas (`RPG_PRELOGIN_USER`/`RPG_PRELOGIN_HASH`) —
`launch_game(install_dir, username, password_hash)` →
`main.py::__main__` lê as env vars → `GameEngine(net_password_hash=...)`
→ `client/online_mode_handlers.py::_do_login()` →
`NetworkClient.login(..., password_hash=...)`, que usa o hash DIRETO
sem hashear de novo (`ph = password_hash or hash_password(password)`
— hashear de novo daria hash-do-hash, senha errada). Auditados os 3
call sites que reusam credencial guardada (login inicial via Launcher,
reconexão de "Deslogar", reconexão automática dentro do jogo) pra
garantir que o hash certo é threaded em todos, nunca só no caminho
feliz.

**Teste**: `tests/test_network_login_hash.py` (4 casos — hash pronto é
usado direto, nunca re-hasheado; hash pronto nunca é confundido com
hash de senha vazia; hash é guardado pra re-login automático) +
`tests/test_main_reconnect_login_fallback.py` atualizado (fixture de
rede precisa aceitar o parâmetro novo). Revert-to-confirm: removendo
`password_hash or` da linha, 3 dos 4 testes novos quebravam —
restaurado.

**Aproveitado no mesmo pedido do usuário**: Launcher ganhou arte de
fundo própria (`assets/hud/Launcher.png`) — ver `ARQUITETURA ONLINE.md`
§27 pra detalhes visuais (fora do escopo deste bug, mas mesma sessão).

## 86. Marcador de quest/correio aparecia ABAIXO do nome do NPC em jogo online real (09/10/2026)

**Relatado pelo usuário via screenshot**: a animação nova de quest
(ver §254/CLAUDE.md) aparecia ABAIXO do nome do NPC ("Andre Von
Han'Duch"), não acima — mesmo depois de eu confirmar com o usuário que
a posição ficaria "igual a de hoje" (acima do nome) antes de
implementar a animação.

**Causa raiz**: `game.py` chamava `quest_dialog.render_world()`/
`mail_system.render_world()` ANTES de `_draw_mob_hp_bars()` no loop de
frame. `WORLD_LABELS` (`ui/world_labels.py`) empilha por ORDEM DE
CHAMADA — quem adiciona por ÚLTIMO fica no topo (gap acumulado maior).
Isso SÓ funcionava certo pro caminho de NPC LOCAL/offline
(`ui/render_system.py::RenderSystem`, que monta badge+nome e roda
ANTES de `quest_dialog.render_world()`) — mas todo NPC SINCRONIZADO
VIA REDE (a imensa maioria em jogo online de verdade, incluindo
QUALQUER quest giver — ver "Fase 1 de combate genérico pra NPC de
serviço", 21/07/2026) tem badge+nome montados em
`client/remote_entity_handlers.py::_draw_mob_hp_bars()`, chamado DEPOIS
de `quest_dialog.render_world()` — então pro caso real (NPC remoto), o
ícone de quest empilhava com gap MENOR que o nome, ficando abaixo
dele.

**Por que passou despercebido ao pedir confirmação ao usuário antes de
implementar**: eu descrevi "a posição de hoje é acima do nome" com base
num comentário desatualizado no código (`ui/render_system.py`, que só
descrevia o caminho LOCAL/offline, nunca testado em jogo online de
verdade) — nunca tinha visto o resultado real em tela antes do
usuário mandar o screenshot. O usuário respondeu "sim, mantém a posição
de hoje" confiando na minha descrição, que estava errada pro caso que
realmente acontece em jogo online.

**Fix**: `game.py` passa a chamar `quest_dialog.render_world()`/
`mail_system.render_world()` DEPOIS de `_draw_mob_hp_bars()` — garante
que o ícone de quest/correio sempre empilha por ÚLTIMO (maior gap,
topo), tanto pro caminho local quanto pro remoto.

**Teste**: `tests/test_quest_marker_above_remote_npc_name.py` — 3
casos: (1) chamando na ordem CORRETA, quest fica acima; (2) prova
negativa — chamando na ordem ERRADA (a que causava o bug), quest fica
abaixo, documentando o mecanismo; (3) guarda direta no texto-fonte de
`game.py` (índice de string), garantindo que `_draw_mob_hp_bars`
sempre precede as duas chamadas — qualquer reordenação futura acidental
quebra este teste imediatamente. Revert-to-confirm feito via `git
stash`: com a ordem antiga, o teste (3) falhava exatamente como
esperado.

**Lição mais ampla**: um comentário no código afirmando "X acontece
nesta ordem" não é verificação — só *um* dos 2+ caminhos de código que
levam ao mesmo resultado visual foi realmente conferido. Pedir
confirmação ao usuário sobre "manter o comportamento atual" só é
confiável se esse comportamento atual foi observado de verdade (captura
de tela/jogo rodando), nunca inferido de um comentário — mesma classe
de lição de `PROBLEMAS_ARQUITETURA.md §11/§23` (não confiar em análise
de terceiro — aqui a "análise de terceiro" foi meu próprio comentário
antigo no código).

## 87. Fechar o jogo nunca avisava o servidor — login novo batia em "already_online" (09/10/2026)

**Relatado pelo usuário**: fechou o jogo (cliente que estava logado) e,
ao tentar logar de novo na hora pelo comando de dev, o servidor disse
que a conta já estava online.

**Causa raiz**: `main.py`, bloco principal — depois de
`game_engine.run()` retornar, `if action != "logout": raise
SystemExit(0)` saía do processo SEM chamar `net.disconnect()`. Só o
branch `"logout"` (menu "Deslogar") chamava. Como fechar a janela
(botão X, `pygame.QUIT`) E "Sair do jogo" no menu de pausa os DOIS
terminam em `action=None` (não `"logout"`, ver `game.py` linha ~3260,
`pygame.quit(); return None`), nenhum dos dois jeitos "normais" de
fechar o jogo nunca enviava o frame de close do WebSocket.
`server/session.py::_handle_login` recusa login novo pra um username
já com `authenticated=True` em alguma sessão viva
(`_sessions.values()`) — e `on_disconnect()` só remove a sessão
morta quando o servidor detecta o fechamento de verdade, que sem um
close explícito só acontece via `ping_timeout` (60s,
`server/main.py`). Mesmo mecanismo já catalogado em §119 (lá era um
vazamento de conexão num BOT de loadtest; aqui é o cliente de
verdade nunca fechando a conexão no quit normal — raiz igual,
gatilho diferente).

**Fix**: `net.disconnect()` adicionado no branch `action != "logout"`,
antes do `raise SystemExit(0)` — mesma chamada que o branch
`"logout"` já fazia, agora nos 2 caminhos de saída normal.

**Teste**: `tests/test_main_disconnect_on_quit.py` — guarda por
inspeção de fonte (não dá pra montar um `GameEngine` real num teste
automatizado) garantindo que `net.disconnect()` aparece dentro do
branch antes do `SystemExit`. Revert-to-confirm via `git stash`: sem
o fix, o teste falha. **Verificação adicional contra o servidor de
produção de verdade** (não só o teste sintético — `client/network.py::
NetworkClient` real, conta de teste real): login → `disconnect()`
limpo → login imediato de novo, mesmos 500ms de respiro que o usuário
teria entre fechar e reabrir — `AUTH_OK` confirmado na hora, sem
esperar nenhum timeout.

## 88. Aranha da Aracnópolis batia 3-8 de dano em vez de ~256-260 — fix real estava no working tree, nunca foi deployado (09/10/2026)

**Relatado pelo usuário**: Aranha elite nível 15-17 (dungeon
Aracnópolis) batendo "3 a 8 de dano" num personagem nível 23/122
armadura — valor muito abaixo do esperado pra um mob elite daquele
nível.

**Investigação (ordem real, nenhum atalho)**: 1) tracei a fórmula
inteira (tier×level em `attack_power`, só tier em `attack_min/max`,
`damage_calculator.calculate_base_damage`, `apply_armor_reduction`) —
matematicamente consistente, previa ~256-260 de dano líquido com 122
de armadura; 2) criei a entidade de verdade (`create_enemy(tier=
'elite', race='Aranha', level=17, ...)`) num teste isolado e confirmei
`attack_power=289` — bateu com a conta; 3) rodei a fórmula de dano
nesses stats reais contra um alvo com 122 de armadura — confirmou
~256-260, não reproduzindo o sintoma; 4) como a investigação estática
não convergia com o relato, adicionei um log temporário no PONTO REAL
de ataque (`EnemyAISystem`, `engine/world_systems.py`, onde `deal_
damage` é chamado de verdade pro golpe corpo-a-corpo do mob), deployei,
usuário lutou uma vez, log mostrou `attack_power=0.0` pro ataque real
(`phys_min/max` corretos em 3.0/7.0, confirmando tier aplicado —
só `attack_power` zerado).

**Causa raiz**: `content/mob_definitions.py` tinha `"attack_power": 0`
pra Aranha no COMMIT que estava deployado — o usuário já tinha
corrigido isso localmente pra `5` (o valor certo, o mesmo que todo meu
cálculo teórico assumiu), mas essa correção estava só no working tree,
nunca commitada nem publicada. Ou seja: a fórmula do jogo sempre
esteve certa — o bug era puramente "correção pronta, nunca chegou no
servidor".

**Fix**: commit real da correção (`attack_power: 0 → 5`) +
`deploy_server.ps1`. Log de debug temporário revertido (`git revert`)
assim que o diagnóstico foi confirmado.

**Lição**: antes de assumir "a fórmula está errada" num caso onde o
cálculo teórico diverge muito do observado, checar `git status`/`git
diff` nos arquivos de conteúdo envolvidos — um valor "certo" visto ao
ler o arquivo (via Read tool, que mostra o WORKING TREE) pode nunca
ter sido commitado/deployado, fazendo a leitura do código enganar sobre
o que está rodando de verdade em produção. `git diff` nesses arquivos
deveria ter sido o 1º passo, não o último — economizaria toda a
investigação de fórmula (que não estava errada) e o ciclo de log
temporário (que confirmou, mas um `git diff` mais cedo já apontava
direto pra causa).

## 89. `tools/png_to_dungeon.py` gerava um CSV que o jogo nunca lia — câmera continuava travando na borda mesmo com mapa maior (10/10/2026)

**Relatado pelo usuário**: adicionou borda ao redor do mapa da dungeon
Aracnópolis pra câmera parar de travar perto da entrada (personagem
aparecendo grudado na parte de baixo da tela). Depois do regen +
deploy (ver §entrada anterior, sem número próprio ainda na época),
testou e "parece que não mudou nada".

**Investigação (ordem real)**: 1) calculei a teoria de clamp de câmera
(`engine/utils.py::get_camera_offset`) com os números reais de
resolução/zoom do jogo — a matemática dizia que o buffer novo (12
tiles ao sul da entrada) deveria ser mais que suficiente, contradizendo
o relato; 2) adicionei um log temporário em `get_camera_offset`
(`DEBUG_CAM_CLAMP=1`, revertido depois) — usuário rodou localmente
(`python main.py`, lê código-fonte direto) e o log mostrou
`map_tiles=(100x150)` — as dimensões ANTIGAS, mesmo com o arquivo
`maps/aracnopolis_dungeon.csv` (gerado por `png_to_dungeon.py`)
corretamente em 110x160 tanto local quanto no servidor de produção
(conferido via SSH); 3) o usuário mencionou de passagem a existência
de 2 arquivos extras na pasta `maps/`:
`aracnopolis_dungeon_terrain.csv` e `aracnopolis_dungeon_objects.csv`
— nenhum dos dois tocado pela regeneração.

**Causa raiz**: `engine/map_loader.py::load_map_csv()` **sempre**
prioriza o par `<nome>_terrain.csv` + `<nome>_objects.csv` sobre o
arquivo único `<nome>.csv`, se os dois primeiros existirem:
```python
base   = os.path.splitext(filepath)[0]
t_path = base + "_terrain.csv"
o_path = base + "_objects.csv"
if os.path.exists(t_path) and os.path.exists(o_path):
    ... usa o par, NUNCA olha pro filepath original
elif os.path.exists(filepath):
    ... só cai aqui se o par NÃO existir
```
A dungeon Aracnópolis já tinha esse par (datado de 08/10/2026, de
ANTES do pipeline de PNG existir/ser adotado — provavelmente criado
manualmente ou por outro processo). `tools/png_to_dungeon.py` sempre
escreveu só `maps/<nome>.csv` (arquivo único) — formato que o loader
só usa como FALLBACK, nunca checado primeiro. Resultado: toda vez que
alguém rodou o script pra ESSA dungeon, o CSV gerado era
**silenciosamente ignorado** — nem erro, nem aviso, o jogo sempre
carregava o par antigo por baixo dos panos. Já tinha acontecido pelo
menos 1 vez antes desta investigação (a 1ª regeneração de borda, ver
registro anterior — o merge de entidades funcionou normalmente porque
usa `_entities.json`, um caminho totalmente separado, só o TERRENO
ficou invisível).

**Fix**: `tools/png_to_dungeon.py` reescrito pra gerar DIRETO no par
`_terrain.csv`+`_objects.csv` (objects sempre vazio — o pipeline de PNG
não tem conceito de camada de objeto ainda), nunca mais o arquivo único
— elimina a ambiguidade pra qualquer dungeon, nova ou já existente.
Removido o `maps/aracnopolis_dungeon.csv` órfão (nunca lido por
ninguém). Confirmado via teste real: `load_map_csv()` E
`request_dungeon_enter()` (fluxo completo do servidor) agora reportam
`110x160` — bate com o PNG.

**Lição**: "o servidor tem o arquivo certo" (confirmado via SSH) e "o
script rodou sem erro" NÃO provam que o jogo está LENDO aquele arquivo
— existe lógica de prioridade entre formatos que pode fazer um arquivo
gerado corretamente nunca ser alcançado. Antes de confiar que uma
regeneração de conteúdo "pegou", testar o carregamento de ponta a
ponta (`load_map_csv()`/`request_dungeon_enter()` reais, não só
conferir o arquivo em disco) — é exatamente o padrão já registrado em
§261 ("git diff deveria ter sido o 1º passo"), só que da forma
inversa: aqui o arquivo em disco estava certo, mas não era o arquivo
que o código realmente carregava.

## 90. Jogador nascia dentro de parede ao entrar na dungeon regenerada — o `"player"` do `_entities.json` da dungeon é só informativo (10/10/2026)

**Relatado pelo usuário**: depois do fix de §265 (terreno carregando
110x160 corretamente), o personagem continuava nascendo dentro de
parede ao entrar na Aracnópolis.

**Investigação**: conferi pixel a pixel que `aracnopolis_dungeon_
entities.json` batia exatamente com o PNG (`player: (94,148)`,
`transition: (54,0)`, 41 zonas = 62 pixels vermelhos) — tudo
consistente, e o tile `(94,148)` no terreno novo É chão (`_`), não
parede. A causa não estava em nenhum arquivo que eu tinha mexido.

**Causa raiz**: o campo `"player"` do `_entities.json` DA DUNGEON
nunca é lido pelo mecanismo real de spawn — é só um valor informativo
que `tools/png_to_dungeon.py` imprime no terminal pra o autor copiar à
mão (ver `GERAR_DUNGEON_PNG.md`, edição manual obrigatória, item 3).
Quem decide de verdade onde o jogador nasce é `target_x`/`target_y` da
transição com `"dungeon_key"` no `_entities.json` do MAPA ABERTO
(`map_1_entities.json`) — arquivo totalmente diferente, fora do
alcance do merge de entidades da PRÓPRIA dungeon (§anterior). Esse
campo ainda apontava pro valor ANTIGO, de antes da borda
(`target_x: 87`, deveria ser `94` depois do deslocamento) — nunca
atualizado porque a regeneração de §265 nem sabia que esse 2º arquivo
existia.

**Fix**: `map_1_entities.json`, transição `dungeon_key="aracnopolis"`
→ `target_x` 87→94 (`target_y` 148 não mudou, só o eixo X foi afetado
pelo deslocamento da borda, mesmo padrão já visto nas outras
entidades).

**Teste real**: diferente da validação de §265 (que passava
`entrance_x`/`entrance_y` direto pro `request_dungeon_enter`, provando
só que o MECANISMO funciona), desta vez o teste leu
`target_x`/`target_y` do PRÓPRIO `map_1_entities.json` — o mesmo
caminho que o jogo realmente percorre — e confirmou o tile de destino
como chão. **Lição**: testar um mecanismo com valores hardcoded no
teste prova que o código funciona pros valores que EU escolhi, não
que os dados de configuração reais estão certos — mesma classe de
lição de §261/§265, água de moinhos diferentes (formula vs. arquivo
certo vs., agora, campo de configuração espalhado em 2 arquivos
diferentes que "parecem" redundantes mas um é decorativo e o outro é
o de verdade).

## 91. 1º tiro de um engajamento novo disparava instantâneo — zero tempo pra animação (17/10/2026)

**Pedido do usuário**: "o ataque automático do arqueiro inicia logo que
o arqueiro cumpre as condições para o ataque, porém teria que contar o
intervalo entre o ataque antes do primeiro ataque, isso corrigiria a
animação desincronizada do ataque do arqueiro, precisamos fazer pelo
menos 3 frames iniciar antes de sair o ataque do arqueiro."

**Causa raiz, achada por investigação (não suposição)**:
`server/combat_processor.py::_resolve_auto_attack` — `_attack_timers`
é um dict por SESSÃO (`self._attack_timers[session_id]`), NUNCA por
combate — `timer = self._attack_timers.get(session_id, 0.0) - dt`
inicializa em **0.0** sempre que não existe entrada ainda. Isso
acontece em 2 momentos reais, não só no login: (1) a sessão nunca
atacou antes; (2) **o alvo anterior morreu** — `cs.target_entity_id =
-1; self._attack_timers.pop(session_id, None)` (linha ~379) apaga a
entrada a cada kill. Resultado: praticamente todo "engajar um alvo
novo" (caça comum de qualquer MMO — matar um mob, engajar o próximo)
disparava o tiro no tick seguinte, sem folga nenhuma — e como a
animação de disparo só liga no COMBAT_RESULT CONFIRMADO (desde a
reversão de §288, sem nenhum mecanismo de antecipação), ela nunca
tinha tempo de mostrar nenhum frame antes do golpe de verdade já ter
acontecido.

**Decisão de alcance, perguntada ao usuário (`AskUserQuestion`)**: só
RANGED (arqueiro) ganha o atraso — melee (guerreiro) continua
disparando instantâneo no 1º golpe, decisão explícita (a dessincronia
relatada foi só da animação do arqueiro, `_resolve_auto_attack` é
COMPARTILHADA entre todas as classes e PvP/PvE, então o alcance
precisava ser confirmado, não assumido). Duração: 0.25s (~3 frames no
ritmo atual de `DEFAULT_FRAME_MS`=78ms, 3×78≈234ms, arredondado) —
constante NOVA e PRÓPRIA do servidor (`CombatProcessorMixin.
_FIRST_RANGED_ATTACK_DELAY_S`), nunca importando `engine/
character_animation.py` pra pegar o valor de lá (módulo tem pygame,
violaria "server nunca importa Pygame").

**Fix**: troca o default do `.get()` — `_cold_start_default =
_FIRST_RANGED_ATTACK_DELAY_S if _is_ranged else 0.0`, só usado quando
NÃO há entrada no dict ainda. A cadência NORMAL entre tiros (quando já
existe uma entrada real) nunca é afetada — só o "nascimento" de uma
entrada nova ganha o atraso, exatamente o momento que o usuário
descreveu. Nenhuma mudança em melee, PvP continua pela mesma função
(mesmo fix se aplica a ranged vs. player, automaticamente).

**Teste**: `tests/test_pvp_auto_attack.py`,
`tests/test_faction.py`, `tests/test_towers.py`,
`tests/test_pack_aggro.py`, `tests/test_bg_kill_credit.py`,
`tests/test_flt_dedup.py`, `tests/test_on_kill_charge_skills.py` (115
testes) sem regressão — todos os testes existentes que exercitam
`_attack_timers` pré-populam a entrada manualmente antes de chamar
`_resolve_auto_attack` (`self.ws._attack_timers[sid] = 0.0`), então
nenhum deles dependia do comportamento de COLD START que foi mudado;
nenhum teste existente cobria esse caminho especificamente (cold start
de verdade, sem pré-popular o dict) — oportunidade pra um teste novo
dedicado, não escrito nesta sessão por decisão de foco (fix pontual,
pequeno, de 1 linha de lógica real). `tests/test_server.py`/`tests/
test_session.py` (326 testes) rodados depois, em segundo plano: 94
falhas, todas em `TestAllyVisionSharing`/`TestPartyPositionsNoAoiUpdate`
(visão compartilhada de time/posição de grupo no AOI — nada a ver com
`_resolve_auto_attack`) — confirmado isolando 1 teste com traceback
completo: `ConnectionRefusedError` tentando abrir o Postgres (Docker
local estava desligado nesta sessão, ver "Como rodar" no topo deste
arquivo) — ambiental, não causado por esta mudança. Os 116 testes
restantes desses 2 arquivos passaram.

## 92. Flecha do auto-attack nascia e já voava antes do frame de soltura tocar (17/10/2026)

**Pedido do usuário**: "a animação está acontecendo depois de o
projétil ser lançado ainda" (depois do fix de §289, que só resolvia o
atraso ANTES do 1º tiro de um engajamento, não a sincronia golpe-a-
golpe).

**Investigação, não suposição** — confirmei 3 hipóteses ANTES de
propor qualquer fix:
1. Código de disparo: `client/remote_entity_handlers.py::
   _apply_combat_result` dispara a animação (`trigger_attack_anim`,
   linha ~286) e cria a flecha (`_spawn_archer_auto_arrow`, linha
   ~409) na MESMA chamada síncrona — sem atraso de código nenhum entre
   os dois. Descartado como causa.
2. Cache de sprite: `engine/character_animation.py::_frame_cache` é
   um dict de MÓDULO, nunca invalidado — se o cliente não reiniciar
   depois de editar o PNG, a edição nunca aparece. Perguntei ao
   usuário: ele confirmou que reiniciou o processo. Descartado.
3. Conteúdo do sprite — a causa real: contei os 8 frames de
   `arqueiro_attack_down.png` e o frame de SOLTURA (flash branco) está
   no ÍNDICE 2 (3ª posição), não no 0. Confirmado pelo usuário (os 4
   sprites — down/left/right/up — têm a soltura no MESMO índice 2).
   Como a flecha nasce (e começa a voar IMEDIATAMENTE) no exato
   instante em que a animação começa a tocar do frame 0, ela já
   percorreu ~156ms de voo (2 frames × `DEFAULT_FRAME_MS`=78ms) antes
   do personagem sequer chegar na pose de soltar — exatamente o
   sintoma relatado.

**Fix, reaproveitando mecanismo JÁ EXISTENTE, NUNCA o congelamento
revertido em §286/§288**: `launch_delay` (campo de
`PlayerProjectileEntry`, `ui/spell_system.py`, usado antes só pela 2ª
flecha de Flecha Reiterada) — `PlayerProjectileSystem.update()` já
trava o projétil parado enquanto `launch_delay > 0` (`continue`, sem
mover), e `render()` já desenha ele com `fade = _spawn_fade_fraction`
dependente da DISTÂNCIA percorrida — parado, `fade=0`, efetivamente
invisível, sem precisar de nenhuma lógica nova de visibilidade.
`client/remote_entity_handlers.py::_spawn_archer_auto_arrow` agora
passa `launch_delay=_ARROW_RELEASE_FRAME_INDEX * DEFAULT_FRAME_MS /
1000.0` (índice 2 × 78ms ≈ 0.156s) pro `spawn()`. **Por que isto NÃO é
o mesmo erro de §286/§288**: a animação do personagem continua tocando
NORMAL desde o frame 0, sem travar em lugar nenhum, sem esperar
evento de rede nenhum (nunca bloqueia andar) — só a FLECHA fica
invisível por um tempo FIXO e curto, conhecido de antemão (não depende
de latência/confirmação de servidor). Som de disparo
(`arrow_release_*`) continua tocando no instante de sempre, sem
atraso — fora de escopo (usuário só relatou a flecha, não o som).

**Teste**: `tests/test_client_ui.py`, `tests/test_character_
animation.py`, `tests/test_flt_dedup.py`, `tests/test_pvp_auto_
attack.py` (341 testes) sem regressão — nenhum teste existente checa
a posição do projétil nos primeiros frames após o spawn, então nenhum
dependia do comportamento antigo (`launch_delay=0.0` implícito).
`_ARROW_RELEASE_FRAME_INDEX = 2` é uma constante ÚNICA pros 4
direções — se um dia os sprites tiverem a soltura em índices
DIFERENTES por direção, essa constante precisa virar um dict por
direção (não feito agora, usuário confirmou que os 4 estão no mesmo
índice). Validação visual de verdade (a flecha "aparecer" exatamente
no flash) ainda depende de playtest manual do usuário.

## 93. §292 confirmado em playtest real: texto nascia em cima do sprite na lista de seleção (19/10/2026)

**Sintoma, com print anotado do usuário**: nome/classe/level da linha
de seleção apareciam SOBRE o sprite (print mostrava uma seta de
"150px" da borda esquerda do retrato até onde o texto deveria
começar). Causa raiz: `_LIST_ICON_NOMINAL` (introduzido em §292) tinha
valor `64+14=78px` de `icon_x` — menor que a largura REAL do sprite de
128px, que começa em `icon_x` e vai até `icon_x+128`. O texto, calculado
a partir desse nominal errado, nascia 50px DENTRO da área do sprite.

**Fix**: `_LIST_ICON_NOMINAL` virou `_LIST_TEXT_OFFSET = int(150 *
sc)` — número único, direto do valor que o usuário mediu/pediu no
print, substituindo o cálculo antigo (`64+14`) que nunca foi
recalibrado depois do preview crescer pra 128px em §292. `text_x =
icon_x + _LIST_TEXT_OFFSET` (era `icon_x + nominal + gap` separados,
virou 1 número só — mais simples, já que não há mais 2 conceitos
distintos aqui).

**Teste**: smoke script (`_draw_selection` com 2 personagens
sintéticos) roda sem exceção; sem suíte automatizada de layout pra
este arquivo (ver §291/§292) — validação visual de verdade (texto
não encosta mais no sprite) ainda depende de playtest manual do
usuário.

## 94. Personagem travava na pose de cast depois de cancelar (mago e arqueiro) (19/10/2026)

Pedido do usuário: cancelar um cast (Bola de Fogo, Tiro Múltiplo, etc.)
devia desligar a animação de windup, mas o personagem ficava preso na
última pose de cast indefinidamente.

**Causa raiz**: `trigger_cast_anim` (`engine/character_animation.py`,
ligado por `cast_start` via `SKILL_EFFECT`, ver CLAUDE.md) seta
`anim_attack_hold_last_frame=True` — esse estado NUNCA expira sozinho
por design (segura o último frame esperando o evento `"launch"/
"impact"/"miss"` real). Quando o cast completa normalmente esse evento
chega e `trigger_attack_anim` sobrescreve a pose sem problema; mas
quando o cast é CANCELADO, esse evento nunca chega — nenhum dos 2
pontos que removem o `SpellCast`/zeram `is_casting` chamava
`clear_attack_anim_state()` pra desligar a animação. A própria
docstring de `clear_attack_anim_state` já citava "cast" entre os
estados que precisam de limpeza manual (15/10/2026, pro revive) — só
nunca tinha sido de fato conectada ao cancelamento de cast.
Mecanismo genérico (chave = `sid` do SKILL_CATALOG, não por classe) —
afeta igualmente mago e arqueiro, qualquer skill com `cast_time>0`.

**Fix**: `clear_attack_anim_state(tm)` chamado nos 2 pontos reais de
cancelamento de `SpellCast`:
- `ui/spell_system.py::SpellCastSystem.update()` — cancelamento LOCAL
  por movimento (o caminho mais comum, cobre qualquer classe/skill).
- `client/network_handlers.py::_handle_msg_skill_result` — rejeição
  SERVER-AUTHORITATIVE de um `SKILL_RESULT{failed:true}` pro mesmo
  `sid` que já estava em cast (cenário real, embora raro: 2ª tentativa
  da mesma skill rejeitada por CD/carga enquanto a 1ª ainda está no
  meio do `cast_time`). No-op seguro quando não havia animação de cast
  ativa.
`Channeling` (Calamidade Flamejante) ficou de fora de propósito —
`cast_time=0.0` no catálogo (é canalização, não cast), `cast_start`
nunca dispara pra ela, não existe animação de windup pra travar.

**Teste**: `tests/test_client_ui.py::
test_cancelar_cast_por_movimento_limpa_anim_attack_state` e
`::test_skill_result_falho_limpa_anim_attack_state_do_cast` — ambos
simulam `anim_attack_state` já ligado (como se `cast_start` já tivesse
disparado) e confirmam que o cancelamento limpa `anim_attack_state`/
`anim_attack_hold_last_frame`. Suíte relacionada (336 testes,
`test_client_ui`/`test_skill_effect_no_local_dup_sound`/
`test_tiro_multiplo_reticle_cache`/`test_channeling_system_get_first`/
`test_portal`) sem regressão. Reprodução em jogo (iniciar cast e mover
o personagem) ainda depende de playtest manual do usuário.

## 95. Limite de personagens por conta era 3, deveria ser 8 (20/10/2026)

Relatado pelo usuário ao revisar a tela de seleção: "o limite de
personagens deveria ser de 8 personagens e não 3". O 3 estava fixo em 3
lugares independentes: `server/auth.py::_create_character` (`count >=
3`, o que barra de verdade), a lista de personagens do login
(`server/auth.py::authenticate`, `LIMIT 3` — mesmo com 8 criados, só 3
chegariam ao cliente) e o cliente (`MAX_ONLINE_CHARS = 3` em
`ui/char_creation_screen.py`); a versão offline antiga usava 8
(`engine/save_system.py::MAX_SLOTS`). Fix: constante única
`shared/constants.py::MAX_CHARACTERS_PER_ACCOUNT = 8`, lida pelos 3
pontos. O peso que motivou o comentário do `LIMIT` (fog de cada
personagem, §19/07/2026) já não vai nessa lista (só id/nome/classe/
nível), então 8 não volta a estourar o frame WebSocket. Teste:
`tests/test_character_names.py::test_limite_de_personagens_por_conta`
(precisa do Postgres — não rodado nesta sessão). Precisa de deploy do
servidor pra valer em produção.

## 96. "Deletar" do ferreiro apaga item só no cliente (RESOLVIDO, 21/10/2026)

Achado ao converter o ferreiro pro atlas de UI (HISTORICO_FEATURES.md
§72): clique direito num item que não é equipamento, dentro de
Reciclar/Forjar, abre o menu "Deletar" — `BlacksmithSystem.
_do_delete_bag_item` faz `inv.items.pop(idx)` no espelho LOCAL e nunca
manda INV_SYNC (`self._on_loot_action("item")`). Mesma classe já
catalogada (CLAUDE.md, "Qualquer mutação local de Inventory feita no
CLIENTE"; PROBLEMAS_ARQUITETURA.md §275): o servidor nunca fica sabendo
e o item volta no próximo resync. Comportamento mantido na troca de
visual (não decidido sozinho); aguardando decisão do usuário entre
corrigir (mesmo caminho da tecla Delete do inventário) ou remover o menu.

**Correção (21/10/2026, decisão do usuário: "pode fazer com a tecla
delete")**: ponto único `SaveSyncHandlers._on_inventory_deleted_item()`
(INV_SYNC + save sem fog) usado pela tecla Delete do inventário (game.py,
antes inline) e pelo menu Deletar do ferreiro (injetado em
`BlacksmithSystem.on_inventory_changed` por game.py — System nunca chama
o GameEngine direto).

## 97. Texto do botão "Desfazer" da loja encostado nas bordas (21/10/2026)

Largura do botão medida com a fonte PEQUENA (`_font_sm`) mas o texto
desenhado com a fonte da janela (`_font_md`, a do Frame) — sobrava quase
nada de folga. Pedido do usuário: regra pra que o texto de botão nunca
fique perto das bordas. Fix no kit (`ui/atlas_window.py`): `BTN_PAD`
(10 px RAW) + `button_tiles(font, labels)`; `Frame.button` mede o
rótulo com a PRÓPRIA fonte e cresce se o `w` pedido for pequeno; todas
as janelas do atlas passaram a calcular largura de botão com
`button_tiles` (loja, ferreiro, correio, troca, quest, treinador, menu
de pausa, atalhos, seleção/criação de personagem, modal de quantidade,
avisos). Teste: `tests/test_atlas_kit.py::test_botao_nunca_deixa_texto_perto_das_bordas`.
