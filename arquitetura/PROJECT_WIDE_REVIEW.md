# Auditoria de Projeto Inteiro — 3 Agentes Independentes (16/09/2026)

> Mesmo modelo do `CLIENT_PERFORMANCE_REVIEW.md` (15/09/2026): 3 agentes
> independentes (arquitetura, qualidade de código, práticas de mercado pra
> jogos) receberam o MESMO escopo e analisaram sem ver o trabalho um do
> outro. Desta vez o escopo expandiu pro **projeto inteiro**, incluindo
> `server/` e `shared/` pela primeira vez com essa metodologia (a rodada
> anterior cobriu só `ui/`, `client/`, `game.py`, `main.py` + módulos de
> `engine/` relevantes ao cliente).

## Metodologia

Cada agente recebeu: leitura de código real (nunca inferência por nome de
arquivo/função), lista de decisões já conscientes do projeto pra não
reabrir, e os 15 achados pendentes do apêndice da auditoria anterior pra
validar/priorizar. Citação obrigatória de arquivo+linha em todo achado.

Um achado do relatório de qualidade de código (item B1, sobre duplicação
de fechamento de painéis NPC) contradizia o relatório de arquitetura —
**verificado manualmente contra o código antes de aceitar qualquer um dos
dois**: o relatório de arquitetura estava certo (ver seção B abaixo). Essa
é exatamente a disciplina que o próprio CLAUDE.md do projeto já pede pra
análise de terceiro — inclusive quando o "terceiro" é outro agente da
mesma auditoria.

Relatórios brutos completos (não editados): `audit_arquitetura.md`,
`audit_qualidade.md`, `audit_mercado.md` — só nesta sessão, não
commitados (ficaram no scratchpad da sessão, não fazem parte do repo).

---

## 🔴 SEGURANÇA / ECONOMIA — achados do especialista em práticas de mercado, servidor tem produção real (Hetzner) rodando

Estes 4 achados descrevem exploits de duplicação de recurso alcançáveis
com **uma única mensagem de rede**, sem precisar de cliente modificado
sofisticado — só falar o protocolo. Causa raiz comum: handler aplica o
que o CLIENTE reportou no payload direto, em vez de resolver o efeito a
partir do catálogo/estado autoritativo do servidor.

### 1. Ouro infinito via `SELL_REQUEST`
**Status:** ✅ Implementado (16/09/2026) — ver `PROBLEMAS_ARQUITETURA.md`
§151. Antes de corrigir, o achado + fix propostos passaram por revisão
cruzada (os outros 2 especialistas, que não tinham visto este achado
específico, opinaram sobre ele — mesmo espírito de "segunda rodada" já
usado em auditorias anteriores do projeto). A revisão achou um problema
NOVO e mais grave no mesmo método (`current_gold` do cliente
sobrescrevendo `wallet.gold` sem validação, também presente em
`process_shop_buy`) — corrigido junto, a pedido do usuário.

`server/world_server.py:3366-3441` (`process_shop_sell`) credita ouro
mesmo quando o item vendido não existe de verdade no Inventory do
jogador (remoção é best-effort, "não achou, não remove", mas o ouro já
foi creditado antes disso) e `stack_sold` (`server/session.py:861-874`)
não tem teto superior. `sell_price = valor_do_catálogo * 0.4 * stack_sold`
com qualquer item caro do jogo e `stack_sold` arbitrário = ouro
ilimitado, repetível, sem gastar nada.
**Contraste:** `craft_item`/`recycle_item` no mesmo arquivo já conferem
posse real ANTES de aplicar — só `sell` tem essa lacuna.

### 2. Cura/mana infinitos via `CONSUMABLE_USE`
**Status:** ✅ Implementado (16/09/2026) — ver `PROBLEMAS_ARQUITETURA.md`
§152. Revisão cruzada achou que o fix rascunhado (só trocar payload por
catálogo) ainda deixava o exploit vivo por outro vetor — faltava exigir
posse real do item — e um problema de schema (HoT quebraria
silenciosamente pra itens de comida/água se implementado ao pé da
letra). Corrigido com os 2 ajustes incorporados.

`server/world_server.py:4149-4241` (`apply_consumable`) lê
`heal_instant`/`mana_restore`/`hot`/`mana_hot` **direto do payload do
cliente**, nunca comparados contra o que o item concede de verdade no
catálogo. Com `item_id=""` (passa a validação de borda, que só exige
`str`), a dedução do item é pulada inteiramente — cura total grátis e
repetível sem cooldown/rate-limit.

### 3. Duplicação/forja de itens via `INV_SYNC`
**Status:** ✅ Implementado (16/09/2026) — ver `PROBLEMAS_ARQUITETURA.md`
§153. Pesquisa prévia (antes do rascunho) achou que a validação óbvia
pareceria quebrar Recarregar — investigação mais funda mostrou que essa
premissa estava errada (Recarregar já é 100% autoritativo por outro
canal, o caminho que "precisaria" do INV_SYNC é código morto há tempos).
Revisão cruzada confirmou a pesquisa de forma independente e achou 2
testes que documentavam o exploit como comportamento esperado.

`server/world_server.py:3973-4031` (`sync_player_inventory`) aceita o
inventário INTEIRO que o cliente reporta como verdade — sanitiza stats
contra o catálogo (não dá pra inventar dano/armadura), mas não valida
QUANTIDADE nem PROVENIÊNCIA, nem limita contra `inv.max_slots`. Persiste
no Postgres via `live_inventory` na hora do save — não é bug cosmético,
sobrevive a logout/login.
**Contraste:** `TALENT_UPDATE` já valida orçamento real via
`validate_talent_allocation` — `INV_SYNC` nunca recebeu o mesmo tratamento.

### 4. Perda de ouro sem entrega em `BUY_REQUEST`
**Status:** ✅ Implementado (16/09/2026) — ver `PROBLEMAS_ARQUITETURA.md`
§155. Último dos 4 achados Alta da auditoria. Revisão cruzada pediu
extrair uma função compartilhada (`_plan_stack_delivery`) entre o
check e a entrega real, em vez de duplicar a lógica de empilhamento —
mesmo espírito de `loot_processor.py::_stack_or_add_item`, já um
"ponto único de verdade" no projeto.

`server/world_server.py:3296-3312` (`process_shop_buy`) checa espaço
usando `session.last_client_payload` (cache que pode estar desatualizado)
em vez do Inventory REAL — ouro debitado incondicionalmente antes de
tentar entregar; se a bag real estiver cheia, a entrega para no meio
silenciosamente, mas o servidor responde `success:true, quantity:<pedido>`
mesmo assim. Perda de progresso real, sem aviso.

### 5. [Média] Nenhum rate-limit em nenhuma mensagem C→S
Amplifica os 4 achados acima (loop apertado = exploração em escala) e
abre brute-force de login/DoS num event loop single-thread (já
documentado no projeto como sensível a isso).

### 6. [Baixa] Hash de senha (servidor) usa SHA-256 salteado, não uma KDF lenta
Só relevante pós-vazamento de banco; troca isolada em `server/auth.py`.

---

## 🟠 Achados novos de qualidade de código

### Task de autosave periódico sem referência — GC pode matá-la no meio [Alta]
**Status:** ✅ Implementado (17/09/2026) — ver `PROBLEMAS_ARQUITETURA.md`
§161. Mesmo padrão já usado por `_track_dispatch_task`/`_dispatch_tasks`
(citado abaixo), agora dedicado ao autosave (`_track_autosave_task`/
`_autosave_tasks`) — `shutdown_dispatch_tasks` passou a cancelar/esperar
os dois registros.

`server/session.py:2511` — `asyncio.create_task(self._autosave_all())`
sem guardar a Task em lugar nenhum. O event loop só guarda referência
FRACA; o GC pode coletar a task no meio da execução, interrompendo o
autosave de TODOS os players ainda não salvos naquele lote (5 em 5 min),
silenciosamente. O próprio arquivo já tem a mitigação certa
(`_track_dispatch_task`/`_dispatch_tasks`, 80 linhas abaixo) — só não foi
aplicada aqui.

### `PlayerInputSystem._manual_attack`/`_space_engage` — código morto testado por si mesmo [Alta]
**Status:** ✅ Investigado e tratado (17/09/2026) — ver
`PROBLEMAS_ARQUITETURA.md` §162. Os dois casos são diferentes:
`_space_engage` já tinha decisão explícita do usuário (26/08/2026) de
manter o método mesmo sem a chamada real — não mexido de novo.
`_manual_attack` era órfão de verdade (zero uso, zero teste) —
removido.

`ui/systems.py:1362`/`:1394`. ESPAÇO no jogo online vai pra
`client/save_sync_handlers.py::_space_engage_online`, não pra cá — o
próprio comentário em `tests/test_client_ui.py:1618-1622` confirma que
isso **já causou um bug real relatado pelo usuário**, corrigido
PRIMEIRO na função errada (morta) porque a suíte de teste exercita só o
caminho morto. Mesma classe "duplicata offline nunca removida" já
catalogada como padrão a limpar neste projeto.

### `SessionManager._unstuck_cooldowns` — dict mutável no CORPO da classe [Média]
`server/session.py:1659` — exatamente o anti-padrão que o CLAUDE.md do
projeto já proíbe explicitamente. Sem bug de compartilhamento hoje
(`SessionManager` é singleton por processo), mas viola a convenção
documentada e vaza memória lentamente (nunca remove entrada por
`session_id`).

### `except Exception: pass` morto em ~20 call sites de `remove_component`/`remove_entity` [Baixa/Média]
Espalhado em `server/*.py` (lista completa no relatório bruto). Os dois
métodos já toleram alvo ausente internamente — o guard nunca dispara
pelo motivo pretendido, só mascara qualquer OUTRO erro real que
aconteça ali (import errado, `TypeError`) sem log, dentro de caminho
crítico de morte/expiração.

### Mais achados (severidade Baixa, resumidos — detalhe completo no relatório bruto)
- `QuestSystem._fire_reach_tile`/`_sync_collect_progress` (`ui/quest_system.py`) — código morto, o próprio comentário do arquivo documenta a remoção da chamada.
- ~9 funções confirmadas mortas por grep no repo inteiro: `Equipment.get_modifier_total()`, `create_tree()`, `_create_spawn_zones()`, 5 funções em `shared/messages.py` (comentário "reservadas pro cliente" — hoje falso, cliente existe há muito), `run_creation()`, 3 métodos de `SOUNDS`, `SpellCast.is_targeting`.
- `content/item_table.py::_make_quiver`/`_make_ammo` — nunca usadas; aljava/munição são construídas inline em vez de via catálogo. **Pode ser lacuna real de conteúdo, não só lixo — vale perguntar antes de apagar.**
- `except Exception: pass` em `client/save_sync_handlers.py:429-433` ao redor de `apply_talent_effects()` — falha silenciosa deixa personagem sem efeito de talento no login, sem nenhum log.

---

## 🟡 Achados novos de arquitetura

### `MobRespawnSystem` guarda referência direta a `DeathHandlerSystem` [Média]
`engine/world_systems.py:4998` — único caso no projeto de um System
recebendo *outro System inteiro* como parâmetro de construtor e
lendo/escrevendo uma lista interna dele direto. Viola a regra "System
nunca chama System direto" (comunicação deveria ser via
componente/evento).

### `DeathHandlerSystem` chama método de `QuestSystem` (UI) via referência global [Média-Alta]
`engine/world_systems.py:1104-1106` + `engine/quest_events.py:30,45-48`.
Mesma violação do achado acima, só que a referência chega por uma
global de módulo em vez de import direto — por isso um grep só por
`import ui` (o método usado na auditoria de 06/08/2026 que concluiu
"servidor nunca importa ui/") não pega esse caso. O comentário no
próprio código admite que o global foi escolhido especificamente pra
*evitar* o import — mas só esconde o acoplamento de uma busca textual,
não o remove.

### Estado ad-hoc via `hasattr` repetido em `WorldServer` [Média]
`server/combat_processor.py:393-440` — `_last_mob_attacker` nunca é
declarado em `__init__`; o guard `if not hasattr(self, ...)` aparece 4x
no mesmo método. Mesma classe de anti-padrão que o CLAUDE.md já proíbe
citando outro exemplo — mostra que a regra não tem enforcement
automatizado.

**Positivo confirmado pelo especialista em arquitetura:** fronteiras
client/server/shared continuam genuinamente limpas — zero `import
pygame` em `server/`/`engine/`/`shared/`, zero import real de `ui`/
`client` a partir de `server/`, `shared/` sem estado mutável de módulo,
componentes livres de lógica de negócio real. O ponto fraco não é a
fronteira client/server (bem guardada), é o acoplamento *lateral* entre
Systems do mesmo lado.

---

## Convergência entre agentes (sinal mais forte)

**`_cheby()` duplicado** — achado de forma independente pelos
especialistas de arquitetura E qualidade de código, ambos confirmando o
MESMO escopo exato após descartar falsos positivos: só
`ui/trainer_system.py:127-129` e `ui/crafting_system.py:163-165`
reimplementam a fórmula `max(abs(dx),abs(dy))` na mão — `ui/systems.py`
e `ui/quest_system.py` também têm um `_cheby` próprio, mas são wrappers
finos que já chamam `engine.utils.chebyshev` de verdade (não contam
como duplicação de risco, só de assinatura).

---

## Veredito consolidado dos 15 itens pendentes (auditoria anterior, client-side)

| # | Achado | Veredito consolidado |
|---|--------|----------------------|
| 1 | Painéis NPC fechados por proximidade duplicado em `game.py` | **PROCEDE** — os 2 relatórios discordaram; verificado manualmente: `_close_modals_if_too_far` (1274) e `_close_all_modals` (1377) são métodos DIFERENTES, cada um com sua própria lista de `if` por painel (Loot/Shop/Crafting/Trainer/QuestDialog) — duplicação real, entre 2 métodos com gatilhos diferentes (auto-close por distância vs. fechar tudo explícito). O relatório de qualidade olhou só o 1º método isoladamente e errou o veredito. |
| 2 | `ui/systems.py` monolítico (4954 linhas, 10 Systems) | PROCEDE, confirmado por 2 agentes |
| 3 | `BuffBar.render()` sem cache de Surface/fonte | PROCEDE |
| 4 | `_cheby()` duplicado (2 arquivos reais) | PROCEDE, convergência de 2 agentes |
| 5 | `get_entities_with()` sem variante singleton (43 ocorrências) | PROCEDE, cresceu desde a última auditoria |
| 6 | Fila de mob remoto com `list.pop(0)` em vez de `deque` | PROCEDE |
| 7 | Reticle/mira do Tiro Múltiplo sem cache de rotação | PROCEDE |
| 8 | `IconManager._cache` sem teto/eviction | PROCEDE |
| 9 | `FloatingTextManager`/`DashTrailManager`/`ProcTextManager` sem remoção in-place | PROCEDE |
| 10 | Sem atlas de ícones | PROCEDE, cresceu (54→67 PNGs) |
| 11 | 6 imports locais redundantes de `RemoteControlled` | PROCEDE |
| 12 | `except Exception: pass` em `save_sync_handlers.py` | PROCEDE (ver achado novo A8 acima — o mais sério dos vários no arquivo) |
| 13 | Imports mortos de loot/spawn offline em `ui/systems.py` | PROCEDE, linha mudou (58-59) |
| 14 | `TILE_SPRITES`/`is_target_alive` reimportados localmente | PROCEDE |

Nenhum dos 14 originais foi corrigido nesta sessão (o trabalho recente
focou nos 10 itens PRINCIPAIS da auditoria anterior, não no apêndice).

---

## Por onde começar (sugestão, não decisão)

1. ~~Os 4 exploits de economia (Alta)~~ — **✅ concluído 16/09/2026**, todos
   os 4 corrigidos com revisão cruzada + revert-to-confirm (ver §151-§155
   em `PROBLEMAS_ARQUITETURA.md`).
2. ~~Task de autosave sem referência (Alta)~~ — **✅ concluído
   17/09/2026** (ver §161 em `PROBLEMAS_ARQUITETURA.md`).
3. ~~`PlayerInputSystem` código morto + teste com falso positivo (Alta)~~
   — **✅ concluído 17/09/2026** (ver §162 em `PROBLEMAS_ARQUITETURA.md`
   — `_manual_attack` removido, `_space_engage` mantido por decisão
   anterior do usuário).
4. Resto — Média/Baixa, sem urgência de produção.
