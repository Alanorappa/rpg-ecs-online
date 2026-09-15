# Revisão de Performance e Arquitetura — Cliente (15/09/2026)

> Análise sob demanda do usuário: 3 agentes independentes (especialista em
> arquitetura, especialista em qualidade de código, especialista em práticas
> de mercado pra jogos) receberam o MESMO escopo — `ui/`, `client/`,
> `game.py`, `main.py`, e os módulos client-relevantes de `engine/`
> (`character_animation.py`, `entity_disguise.py`, `tileset.py`,
> `floating_text.py`) — e analisaram independentemente, sem ver o
> trabalho um do outro. `server/` ficou fora de escopo.

## Metodologia

Cada agente recebeu instrução explícita pra:
- Ler código de verdade (não inferir a partir de nome de arquivo)
- Respeitar decisões já conscientes do projeto (colisão sub-tile revertida,
  numpy só após profiling, `GameEngine` com ~20 mixins é convenção
  deliberada, cache de `TileRenderSystem` já corrigido)
- Citar arquivo + linha pra cada achado

Os 3 relatórios brutos somaram 30 achados. Os 10 abaixo são os que
convergiram entre múltiplos agentes (sinal mais forte — o mesmo problema
achado de forma independente por ângulos diferentes) ou que, mesmo
achados por só 1 agente, têm evidência de código muito concreta e/ou
gravidade alta. Os 20 achados restantes estão resumidos no apêndice, pra
não perder o trabalho, sem diluir os 10 principais.

**Nenhuma mudança de código foi feita** na publicação original deste
documento — era só o levantamento. Implementação começou em 15/09/2026,
com aprovação do usuário ("vamos começar, pode me questionar o que
precisar"); status de cada item abaixo é atualizado conforme
implementado.

---

## 1. Nome de player/mob (`font.render()`) recriado do zero todo frame, sem cache — mesmo bug já documentado e corrigido só pro número de nível

**Status:** ✅ Implementado (15/09/2026) — `ui/world_labels.py::_cached_text_surf()`.

**Gravidade:** Alta

**Impacto no desempenho:** `font.render()` é, pelas palavras do próprio
código do projeto, a operação mais cara já medida no cliente —
`ui/hud_bars.py:169-178` documenta que o combo `font.render()` +
`pygame.mask.from_surface()` + `get_bounding_rects()` foi a causa de uma
queda de FPS relatada pelo usuário em 13/07/2026 quando rodava todo
frame pra todo mob visível. Essa lição virou cache (`_level_surf_cache`,
teto de 256 entradas) só pro **número de nível** — o **nome** do
player/mob continua sendo re-renderizado do zero a cada chamada, todo
frame, pra toda entidade visível.

**Sistemas relacionados:** `ui/world_labels.py:49-68`
(`_WorldOverlayQueue.add_text()`, sem cache); chamadores por-entidade-
por-frame: `ui/systems.py:1852-1957` (nameplate do `RenderSystem`),
`client/remote_entity_handlers.py:1614` e `:1811` (HP bar/nome de mob e
player remoto). Padrão correto já existe ao lado, em
`ui/hud_bars.py:179-190` e `ui/floating_text.py:11-34`.

**Solução proposta:** Aplicar o MESMO padrão já usado em
`_level_surf_cache`/`_outline_cache`: dict de módulo cacheando por
`(font_id, texto, cor)`, com teto + `clear()` ao estourar (mesmo
mecanismo, não um novo). Nome de personagem muda só em login/troca de
personagem — cache hit deveria ficar perto de 100% do tempo.

**Ganho esperado:** Remove o gargalo mais quente já medido pelo próprio
projeto, pro caso mais frequente possível (toda entidade visível, todo
frame) — deve reduzir de forma mensurável a queda de FPS em cena com
muitas entidades (BG, dungeon, várias torres/minions/players à vista).

---

## 2. Import de módulo dentro do loop de desenho mais executado do jogo (`RenderSystem.render()`)

**Status:** ✅ Implementado (15/09/2026) — imports movidos pro topo de `ui/systems.py`.

**Gravidade:** Alta

**Impacto no desempenho:** Achado de forma independente por 2 dos 3
agentes (código e mercado), com as mesmas linhas exatas. Dentro do loop
`for item in drawables:` de `RenderSystem.render()` — que roda a 60 FPS
pra TODA entidade visível — há `from engine.entity_disguise import (...)`
(`ui/systems.py:1663-1664`, executado pra toda entidade mesmo sem
disguise ativo) e `from engine.character_animation import (...)`
(`ui/systems.py:1748-1749`, `:1763`). Mesmo com o módulo já em
`sys.modules`, cada `import` dentro de função paga um lookup + bind de
nome — aqui multiplicado por N entidades × 60 fps, no loop mais quente
de todo o cliente.

**Sistemas relacionados:** `ui/systems.py::RenderSystem.render()`,
linhas 1618-1664 e 1748-1763.

**Solução proposta:** Mover os dois imports pro topo do arquivo, junto
dos demais (`ui/systems.py:27-45`) — `entity_disguise` e
`character_animation` são módulos puros (sem pygame, sem ciclo de
import aparente com `ui/systems.py`).

**Ganho esperado:** Custo de import cai de O(entidades × frames) pra
O(1); ganho pequeno por chamada individual, mas remove um custo fixo
repetido dezenas de milhares de vezes por segundo em cena cheia —
"morte por mil cortes" clássica de profiling de produção.

---

## 3. `__import__("engine.components", fromlist=[...])` no caminho de uso de skill — reimportando nomes que já são globals do módulo

**Status:** ✅ Implementado (15/09/2026) — 15 ocorrências removidas de `ui/systems.py`.

**Gravidade:** Alta

**Impacto no desempenho:** `ui/systems.py` já importa `Position`,
`CombatStats`, `TileMovement`, `CombatState`, `CharacterStats`,
`RemoteControlled`, `Visible` no topo do arquivo (linhas 27-35). Mesmo
assim, dentro de `SkillHandlers` (usada por `SkillSystem.update`, roda
todo tick de input de skill) existem **15 chamadas** a
`__import__("engine.components", fromlist=["X"]).X` pros MESMOS nomes
já disponíveis — `ui/systems.py:417, 457, 4467, 4518-4547, 4616-4629`.
`__import__()` é uma chamada de função de verdade (processa `fromlist`,
faz `getattr`) — mais caro que usar o nome já ligado, e roda toda vez
que o jogador tenta usar uma skill, potencialmente todo frame com a
tecla segurada.

**Sistemas relacionados:** `ui/systems.py`, região 4460-4630 (resolução
de alvo/range dentro de `SkillHandlers`) e
`MouseTargetingSystem._visible_enemies_sorted` (linha 417).

**Solução proposta:** Remover os 15 `__import__(...)` e usar os nomes
diretamente — já são globals do módulo.

**Ganho esperado:** Elimina 15 chamadas de função desnecessárias por
resolução de skill; o padrão atual sugere código colado sem revisão —
corrigir também melhora legibilidade.

---

## 4. `BlacksmithSystem`/`TrainerSystem` chamam outros `System` diretamente, inclusive métodos privados — viola a regra própria do projeto

**Gravidade:** Crítica

**Impacto no desempenho:** Nenhum mensurável em FPS — é violação de
acoplamento, mas é a mais grave estruturalmente: o projeto tem uma regra
explícita ("um System nunca chama outro System diretamente... se dois
sistemas parecem precisar 'conversar' direto, é sinal de que falta um
componente ou evento intermediário") e esse achado é uma violação direta
e concreta dela.

**Sistemas relacionados:** `ui/crafting_system.py:68-93`
(`BlacksmithSystem` recebe `shop_system`/`quest_system` como referência
direta no construtor), `:369` (`self._shop_system.open_for(...)`),
`:534`/`:540` (`self._quest_system.can_turn_in(qid)`); `ui/
trainer_system.py:246, 282-284, 306-307, 323` (chama
`self._quest_dialog._open_dialog(...)`, `._get_available_quests(...)`,
`._get_completable_quests(...)` — todos com `_`, ou seja, métodos
PRIVADOS de `QuestDialogSystem` chamados de fora); wiring em
`game.py:464-484`.

**Solução proposta:** Trocar por um componente de estado (ex:
`NPCInteractionRequest` no player entity, com `kind`/`target_eid`, lido
por `ShopSystem`/`QuestSystem` no próprio `update()`) ou evento
despachado via fila de eventos já reaproveitada pelo projeto. `can_turn_in`/
`can_start` deveriam virar leitura de um componente `QuestLog` público,
não chamada de método privado de outro System.

**Ganho esperado:** Sem ganho de runtime — o ganho é eliminar a causa
estrutural mais provável do próximo bug "consertei X, quebrei Y" nessa
área: hoje qualquer mudança em `QuestDialogSystem._get_completable_quests`
pode quebrar `TrainerSystem` silenciosamente, sem nenhum contrato formal
entre os dois.

---

## 5. Hit-test de clique/alvo reimplementado de forma DIVERGENTE em 3-4 lugares — já é uma classe de bug conhecida do projeto

**Status:** ✅ Implementado (15/09/2026) — novo `ui/systems.py::
_entity_click_rect()` (ponto único, sprite real vs. retângulo), usado por
`_enemy_at_world_pos`/`_live_target_at_world_pos`/
`_remote_player_at_world_pos`. `_live_target_at_world_pos` também ganhou
o check de `FogOfWar.visible` que faltava. Decisão do usuário: ampliar a
área clicável onde fizer sentido, não só preservar o comportamento atual.
`_corpse_click_rect` ficou de fora de propósito (semântica diferente,
tolerância pequena deliberada). 2 testes novos em
`tests/test_client_ui.py`. Suíte completa: 1653 passed, 1 failed
pré-existente/timing flake sem relação.

**Gravidade:** Alta

**Impacto no desempenho:** Nenhum — é corretude. Achado de forma
independente por arquitetura e código. `_corpse_click_rect`
(`ui/systems.py:237-263`) usa o sprite REAL via `TILE_SPRITES` quando
existe (fix documentado de 25/07/2026 — clique não acertava sprites
grandes). `MouseTargetingSystem._enemy_at_world_pos`
(`ui/systems.py:323-357`) reimplementa a MESMA lógica inline, sem
chamar a função compartilhada. Já `_live_target_at_world_pos`
(`ui/systems.py:266-301`, usado por `LootSystem`) e
`_remote_player_at_world_pos` (`:359-377`) usam SÓ
`renderable.width/height` — nunca o sprite real, e o primeiro nem checa
`FogOfWar.visible`. Ou seja: um mob/torre com sprite grande é clicável
num caminho e não no outro; um inimigo fora do fog-of-war (não deveria
ser alvo) ainda bloqueia loot no segundo caminho.

**Sistemas relacionados:** `ui/systems.py` — `_corpse_click_rect`
(L237), `_live_target_at_world_pos` (L266),
`MouseTargetingSystem._enemy_at_world_pos` (L323),
`_remote_player_at_world_pos` (L359).

**Solução proposta:** Extrair função única (ex: `entity_click_rect(world,
entity_id, pos, renderable)`) que decide sprite-real-vs-retângulo (e
fog-of-war) uma vez, chamada pelos 4 call sites — mesmo padrão já usado
por `is_target_alive()`. Como pode mudar área clicável em alguns casos,
precisa de validação com o usuário antes de aplicar (não é refactor
puramente interno).

**Ganho esperado:** Elimina inconsistência de "onde dá pra clicar" entre
mob local, player remoto e corpse — evita reabrir a mesma classe de bug
já corrigida uma vez (25/07/2026) e nunca generalizada.

---

## 6. Cálculo de offset de câmera duplicado — inclusive DUAS definições idênticas byte-a-byte no mesmo arquivo, mais 11+ cópias inline

**Status:** ✅ Implementado (15/09/2026) — `engine.utils.get_camera_offset()`
novo, 7 dos 13 call sites unificados (os 6 restantes tinham semântica
genuinamente diferente — `_is_on_screen`/escrita de `cam.offset_x/y`/
`CameraSystem.update` — e ficaram de fora de propósito, ver commit).

**Gravidade:** Alta

**Impacto no desempenho:** Pequeno por chamada (Camera é entidade única,
loop para no 1º item) — o problema real é manutenibilidade/correção.
Achado de forma independente por arquitetura e código.
`_get_camera_offset()` existe **literalmente duplicada** em
`ui/systems.py:316-321` (`MouseTargetingSystem`) e `ui/systems.py:
3821-3826` (`LootSystem`) — mesma classe de arquivo, copy-paste em vez
de função de módulo. O mesmo padrão `for _, _, cam_pos in
world.get_entities_with(Camera, Position): ... break` se repete inline
em pelo menos mais 11 pontos: `ui/systems.py:592, 1994, 2749, 4789`,
`ui/spell_system.py:1823, 2092`, `ui/crafting_system.py:243`, `ui/
trainer_system.py:185`, `game.py:2975, 3010`, `engine/skill_handlers.py:133`.

**Sistemas relacionados:** ver lista acima (13 pontos ao todo).

**Solução proposta:** Função única, ex.
`engine.utils.get_camera_offset(world, screen_w, screen_h) -> tuple[float,
float]` — mesmo padrão de `is_target_alive`/`in_range`/`chebyshev` já
estabelecidos como ponto único de verdade no projeto.

**Ganho esperado:** Zero divergência futura de comportamento de câmera
(ex: shake, zoom); remove 13 blocos de código duplicado de uma vez —
exatamente a classe de bug ("lógica duplicada em vez de ponto único")
que o projeto já pagou antes com `walk_to_npc` (4 cópias antes de
virar função única).

---

## 7. Dispatch de mensagens de rede via cadeia if/elif de 53-54 ramos

**Status:** ✅ Implementado (15/09/2026) — `NetworkHandlers._NET_DISPATCH_NAMES`
(dict `MsgType -> nome do método`, resolvido via `getattr(self, name)`
em vez de referência direta de função — 17 dos 54 handlers são definidos
em mixins IRMÃOS de `NetworkHandlers`, não nela mesma, ver
`arquitetura/PROBLEMAS_ARQUITETURA.md` pra essa pegadinha).

**Gravidade:** Média

**Impacto no desempenho:** Real e mensurável em cenário de stress.
Achado de forma independente por arquitetura e código.
`_handle_net_message` roda pra CADA mensagem recebida do servidor
(AOI_UPDATE, ENTITY_MOVE, COMBAT_RESULT chegam em rajada durante
combate com várias entidades, servidor tickando a 30Hz). Mensagens no
fim da cadeia (`TRADE_*`, `DUEL_*`, `ARENA_*`, `CHAT_MESSAGE`) custam
até 53 comparações sequenciais — O(n) por mensagem onde deveria ser
O(1).

**Sistemas relacionados:** `client/network_handlers.py:17-126`
(`NetworkHandlers._handle_net_message`).

**Solução proposta:** Dict de despacho montado uma vez (`{MsgType.X:
self._handle_msg_x, ...}`), chamado com `handler =
self._DISPATCH.get(msg_type); if handler: handler(payload)`. Refactor
mecânico, baixo risco.

**Ganho esperado:** O(1) em vez de O(n) por mensagem — a diferença por
mensagem é pequena (microssegundos), mas o volume em combate PvP/AoE
multi-entidade é alto o bastante pra somar; o ganho mais claro é
manutenibilidade (mensagem nova não exige mais escolher onde encaixar
no meio de 54 `elif`).

---

## 8. `RenderSystem` sem culling de viewport pra entidades — só objetos estáticos de mapa (tiles/props) têm janela de visibilidade calculada

**Status:** ✅ Implementado e testado (15/09/2026) — margem fixa de 200px
em `RenderSystem.render()`, `ui/systems.py`. 2 testes novos em
`tests/test_client_ui.py`. Suíte completa: 1653 passed, 1 failed
pré-existente/timing flake sem relação.

**Gravidade:** Média-alta

**Impacto no desempenho:** `ui/systems.py:1549` itera TODA entidade com
`Position`+`Renderable` no ECS local (delimitado pelo raio de AOI do
servidor, que existe pra replicação de rede — não pra render) e entra
no Y-sort/desenho todo frame, mesmo estando fora da tela mas dentro do
AOI. O projeto já tem a disciplina certa pra objetos de mapa
(`TileRenderSystem._object_visible_tile_range()`, citada no próprio
CLAUDE.md) mas nunca replicou o mesmo filtro pra entidades
(players/mobs/NPCs).

**Sistemas relacionados:** `ui/systems.py::RenderSystem.render()`,
coleta de "drawables" nas linhas 1546-1615.

**Solução proposta:** Aplicar o mesmo padrão de
`_object_visible_tile_range()` ao loop de entidades: filtrar por
retângulo de câmera + margem antes de entrar no Y-sort, em vez de
confiar só no AOI do servidor.

**Ganho esperado:** Em cenário de escala real (torres, minions e vários
players no mesmo AOI mas fora da tela — BG/dungeon cheia), corta o
trabalho de Y-sort/lookup de componente proporcionalmente ao que está
realmente em tela, não ao que está em AOI.

---

## 9. Sem object pooling pra entidades efêmeras de projétil — cada flecha/bola de fogo é `create_entity()`/`remove_entity()` no ECS

**Gravidade:** Média

**Impacto no desempenho:** Toda flecha de auto-attack, toda Bola de
Fogo, todo projétil de mob aloca uma entidade ECS nova (dict novo +
entrada nova em cada índice de componente) e a destrói ao impactar. Em
PvP com múltiplos arqueiros/magos disparando (BG cheia), isso é
alocação/desalocação contínua — pressão de alocador e trabalho
redundante de índice que um pool evitaria. É o padrão OPOSTO ao já
usado corretamente pelo próprio projeto pra floating text (`FLT`), que
deliberadamente NÃO usa entidade ECS e sim uma lista Python simples com
`__slots__`.

**Sistemas relacionados:** `ui/spell_system.py:259, 440, 496, 600, 677`
(spawn de projéteis de skill), `client/remote_entity_handlers.py:183`
(flecha de auto-attack de arqueiro), `engine/world_systems.py:1427,
4173, 4725` (projéteis server-side/replicados). Contraste correto:
`ui/floating_text.py:37-63` (`FloatingTextEntry`, fora do ECS).

**Solução proposta:** Seguir o MESMO padrão já usado por
`FloatingTextManager` — projétil puramente visual não precisa ser
entidade ECS registrada em `World._index` (que existe pra permitir
*queries* entre Systems, algo que nenhum outro System faz sobre
projétil); uma lista dedicada com `__slots__`, gerenciada fora do ECS,
resolveria sem o overhead de indexação.

**Ganho esperado:** Reduz alocação/GC de curta duração no caminho mais
frequente de combate (auto-attack, o mais disparado do jogo); elimina
overhead de manutenção de índice ECS pra algo que ninguém faz *query*
via `get_entities_with`.

---

## 10. `LootSystem` re-escaneia o ECS inteiro pra achar a MESMA entidade que já guarda em `self.player_entity`

**Status:** ✅ Implementado (15/09/2026) — 8 call sites trocados por
`get_component(self.player_entity, ...)` em `ui/systems.py`.

**Gravidade:** Média

**Impacto no desempenho:** `LootSystem.__init__` (`ui/systems.py:3671`)
já guarda `self.player_entity`. Mesmo assim, 8 métodos da mesma classe
ignoram esse campo e fazem `self.world.get_entities_with(Wallet,
PlayerControlled)` / `Inventory, PlayerControlled` / `Equipment,
PlayerControlled` / `CombatStats, PlayerControlled` pra achar de novo a
entidade já conhecida — monta índice, filtra set, aloca lista e tupla,
só pra pegar o 1º (e único) resultado e sair com `break`.
`world.get_component(self.player_entity, Comp)` é O(1) direto (dict
lookup). Custo pequeno por chamada, mas loot é evento frequente em
farm.

**Sistemas relacionados:** `ui/systems.py` — `LootSystem.
_try_open_corpse` (L3763), `_try_take_item` (L3970, 3988),
`_try_equip_item` (L4055, 4058, 4061), tooltip de loot (L4313).

**Solução proposta:** Trocar todos os `for _, X, _ in
self.world.get_entities_with(Comp, PlayerControlled): ... break` dentro
de `LootSystem` por `X = self.world.get_component(self.player_entity,
Comp)`.

**Ganho esperado:** Elimina 8 varreduras de índice + alocações por ação
de loot; ganho de CPU pequeno mas gratuito, código mais legível.

---

## Resumo rápido (por onde começar)

Os itens **1, 2 e 3** são os de maior ganho por menor risco — nenhum
muda comportamento visível, só removem trabalho redundante em caminhos
quentes (render por-entidade e uso de skill). O item **4** é o mais
grave estruturalmente (viola regra própria do projeto), mas exige
desenho de solução (componente/evento novo) antes de mexer — não é
trivial. Os itens **6** e **7** são refactors mecânicos de baixo risco
com alto valor de manutenibilidade. Os itens **5, 8, 9, 10** têm ganho
real mas dependem mais de contexto de uso (quantas entidades em tela,
volume de projéteis) pra saber o quanto importam na prática.

---

## Apêndice — achados secundários (não entraram nos 10 principais)

Achados por só 1 agente, com gravidade baixa/média, ou já cobertos
parcialmente por um item acima — registrados pra não perder o
levantamento, sem overlap direto com os 10 principais:

- **Lista de painéis NPC (Loot/Shop/Crafting/Trainer/QuestDialog)
  fechados por proximidade duplicada em `game.py:1277-1306` E
  `game.py:1358-1393`** — mesmo padrão de tupla nomeada já usado em
  `_WORLD_SYSTEM_ATTRS` (L1339-1345) resolveria.
- **`IceBlockSystem`/`FireShieldSystem` calculam HP real no cliente**
  (`ui/spell_system.py:1748-1804`) — débito JÁ conhecido e documentado
  no CLAUDE.md do projeto ("tick duplicado cliente+servidor"), migração
  já avaliada e descartada por risco desproporcional. Registrado aqui só
  por completude, não é recomendação de reabrir.
- **`ui/systems.py` monolítico (~4900 linhas, 10 `System`s
  independentes sem relação de domínio)** — `ShopSystem`/
  `ConsumableSystem`/`LootSystem` são candidatos a virar arquivo próprio,
  seguindo o padrão já usado por `quest_system.py`/`trainer_system.py`/
  `crafting_system.py`.
- **`BuffBar.render()` reconstrói Surface + fonte todo frame** mesmo
  quando só o número de segundos muda 1x/s — mesmo padrão de
  `_outline_cache` resolveria.
- **`_cheby()` duplicado inteiro em `ui/crafting_system.py:164-166` e
  `ui/trainer_system.py:127-129`**, ignorando `engine.utils.chebyshev`
  já central no projeto.
- **`get_entities_with()` aloca lista nova mesmo quando o chamador só
  quer 1 resultado** (padrão singleton — Camera, Tilemap, FogOfWar) —
  40+ ocorrências só em `ui/systems.py`. Um `World.get_first(*tipos)`
  resolveria sem tocar em `get_entities_with`. Mudança estrutural no
  ECS compartilhado — vale medir com profiling antes (mesma regra do
  projeto de não otimizar estrutura de dado sem medição).
- **Fila de movimento de mob remoto usa `list.pop(0)` (O(n)) em vez de
  `collections.deque`** (`client/remote_entity_handlers.py:1300`) —
  troca mecânica de baixo risco.
- **Reticle de mira do Tiro Múltiplo recria Surface rotacionada todo
  frame sem cache** (`ui/spell_system.py:869`) — quantizar ângulo em
  buckets de 5-10° (mesmo padrão já usado pra alpha em
  `floating_text.py:235`) resolveria.
- **`IconManager._cache` sem teto/eviction**, diferente do padrão já
  usado em `_outline_cache`/`_level_surf_cache` — baixo risco hoje
  (catálogo pequeno), mas inconsistente com a convenção do projeto.
- **`FloatingTextManager`/`DashTrailManager`/`ProcTextManager`
  reconstroem a lista "viva" inteira via filtro Python todo frame** em
  vez de remoção in-place — irrelevante em volume atual, relevante em
  cena de AoE com muitos hits simultâneos.
- **Sem atlas/spritesheet pra ícones** (54 PNGs soltos) — só relevante
  se o catálogo crescer significativamente; `_get_sheet_region()` já
  existe em `ui/tile_sprite_manager.py` e resolveria sem mecanismo novo.
- **6 imports locais redundantes de `RemoteControlled`** com aliases
  diferentes (`ui/systems.py`, 6 pontos) — mesmo nome já é global do
  módulo.
- **`except Exception: pass` silencioso** em `client/
  save_sync_handlers.py:408-411` (restore de talentos no login) — sem
  log nenhum, dificulta diagnosticar falha real se acontecer.
- **Imports mortos de loot/spawn "offline"** (`ui/systems.py:48-49`,
  `roll_loot`/`roll_mob_loot`/`roll_coins`/`create_corpse`/
  `create_enemy` — nenhum usado) — sobra da remoção do modo offline
  (15/07/2026).
- **`TILE_SPRITES`/`is_target_alive` reimportados localmente várias
  vezes** no mesmo arquivo sem motivo de ciclo — só legibilidade.
