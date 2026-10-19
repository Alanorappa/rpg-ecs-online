06 de junho de 2026

Alanorappa

Análise Técnica de Arquitetura em MMORPG Python/Pygame

Identificação de problemas críticos, recomendações e diagramas de fluxo

06 de junho de 2026

---


> **Nota (19/10/2026):** este arquivo foi reorganizado — agora cobre só
> problemas ARQUITETURAIS de verdade (débito estrutural, padrões
> recorrentes, investigações de performance). Features e bugs pontuais
> viraram `HISTORICO_FEATURES.md`/`HISTORICO_BUGS.md`; o índice de TUDO
> (inclusive o que já estava aqui) é `arquitetura/INDICE.md`. Seções que
> saíram daqui deixaram um stub de 1 linha no lugar, pra nenhum ponteiro
> antigo ("ver PROBLEMAS_ARQUITETURA.md §N") quebrar.

---

## 1. DIAGRAMA DE FLUXO: Sincronização Cliente-Servidor (MULTIPLAYER)

### FLUXO CORRETO

```
Player A usa Skill
  ↓
Cliente A: validação local (feedback rápido)
  ↓
CAST_SKILL enviado ao servidor
  ↓
Servidor: validação autoritativa
  ↓
Servidor: executa handler
  ↓
Servidor: aplica efeitos no mundo
  ↓
SKILL_RESULT enviado para Player A + AOI
  ↓
Player A: aplica efeitos visuais
Player B (AOI): vê efeito de Player A
```

### FLUXO ATUAL (QUEBRADO)

```
Player A usa Skill
  ↓
Cliente A: validação local (INCOMPLETA)
  ↓
CAST_SKILL enviado ao servidor
  ↓
Servidor: validação (PODE REJEITAR POR RAZÃO DIFERENTE)
  ↓
Servidor: executa handler (MAS NÃO SABE DOS TALENTOS/PROCS DO CLIENTE)
  ↓
Servidor: aplica efeitos (INCONSISTENTES COM CLIENTE)
  ↓
SKILL_RESULT enviado (PODE CONTRADIZER O QUE CLIENTE MOSTROU)
  ↓
Player A: vê erro diferente do que servidor rejeitou
Player B (AOI): vê efeito diferente do que Player A viu
```

---

## 2. DIAGRAMA DE DEPENDÊNCIAS: Acoplamento de Sistemas

```
Skill
  ├─ depende de Handler
  │   ├─ depende de CombatStats
  │   │   ├─ depende de Talento
  │   │   │   ├─ depende de Modificador
  │   │   │   └─ depende de Flag comportamental
  │   │   ├─ depende de Proc
  │   │   │   └─ depende de CharacterStats
  │   │   └─ depende de Cooldown
  │   ├─ depende de TileMovement
  │   ├─ depende de Position
  │   └─ depende de Validação (DUPLICADA)
  ├─ depende de SpellCast (se tem cast_time)
  ├─ depende de Projectile (se é ranged)
  └─ depende de StatusEffect (se aplica efeito)

Problema: Mudança em qualquer um desses afeta Skill
Solução: Desacoplar via interfaces/eventos
```

---

## 3. DIAGRAMA DE ESCALABILIDADE: Impacto de Performance

```
PROBLEMA: AOI Sweep N²

Número de Players | Número de Mobs | Iterações/tick | CPU Impact
1                 | 100            | 100            | <1%
10                | 100            | 1.000          | 1%
100               | 100            | 10.000         | 10%
1000              | 100            | 100.000        | 100% (CRÍTICO)

PROBLEMA: Factory Instantiation em Lookup

Número de Itens | Vendas/min | Objetos criados/min | GC Pressure
100             | 10         | 1.000               | Baixa
100             | 100        | 10.000              | Média
100             | 1000       | 100.000             | Alta (CRÍTICO)

PROBLEMA: Validação Duplicada

Validações/Skill | Tempo/validação | Tempo total/tick | Escalabilidade
1 (servidor)     | 1ms             | 10ms (100 skills)| Linear
2 (C+S)          | 1ms             | 20ms (100 skills)| 2x pior
3 (C+S+Handler)  | 1ms             | 30ms (100 skills)| 3x pior (CRÍTICO)
```

---

## 4. DIAGRAMA DE FLUXO: Por que Correções não Funcionam

```
Você corrige validação de mana no cliente
  ↓
Offline: ✅ Funciona (validação local é a única)
Online: ❌ Servidor ainda rejeita (tem validação própria)
  ↓
Você corrige no servidor
  ↓
Offline: ❌ Não afeta (servidor não roda offline)
Online: ✅ Funciona (servidor é autoritativo)
  ↓
Você aloca talento
  ↓
Offline: ✅ Funciona (apply_talent_effects chamado)
Online: ❌ Servidor não sincroniza (espera próximo login)
  ↓
Você corrige um proc
  ↓
Offline: ✅ Funciona (tudo é local)
Online: ❌ Servidor não sabe do proc (não sincroniza)
```

---

## 5. DIAGRAMA DE ARQUITETURA: Estrutura Atual vs Ideal

### ESTRUTURA ATUAL (MONOLÍTICA)

```
systems.py (4500+ linhas)
├─ SkillSystem (validação + handlers)
├─ SpellSystem (duplicação de spell_damage)
├─ CombatStateSystem (copiado)
├─ PlayerInputSystem (validação local)
├─ EnemyAISystem
└─ ... 40+ outros sistemas

skill_handlers.py (1000+ linhas)
├─ _skill_golpe_poderoso
├─ _skill_bola_de_fogo
├─ _skill_impacto
└─ ... 30+ handlers

talent_system.py (500+ linhas)
├─ apply_talent_effects (reseta TUDO)
├─ _live_effects
└─ UI rendering (acoplado)

spell_system.py (800+ linhas)
├─ SpellCastSystem
├─ PlayerProjectileSystem
├─ ChannelingSystem
└─ ... duplicação de lógica

Problema: Mudança em um lugar afeta múltiplos lugares
```

### ESTRUTURA IDEAL (MODULAR)

```
core/
├─ skill_system.py
│  ├─ SkillValidator (interface)
│  ├─ SkillExecutor (interface)
│  └─ SkillRegistry (data-driven)
├─ talent_system.py
│  ├─ TalentModifier (interface)
│  ├─ TalentApplier (aplica uma vez)
│  └─ TalentRegistry (data-driven)
├─ combat_system.py
│  ├─ DamageCalculator (função pura)
│  ├─ EffectApplier (interface)
│  └─ CombatState (componente puro)
└─ sync_system.py
   ├─ StateSync (sincroniza talentos/procs)
   ├─ EventBroadcaster (notifica outros players)
   └─ ConflictResolver (resolve inconsistências)

handlers/
├─ melee_handler.py
├─ spell_handler.py
├─ projectile_handler.py
└─ effect_handler.py

Vantagem: Mudança em um lugar não afeta outros
```

---

## 6. TABELA DE SEVERIDADE: Problemas por Impacto

| Problema | Severidade | Impacto em 1 Player | Impacto em 10 Players | Impacto em 100 Players | Impacto em 1000 Players | Modularidade | Escalabilidade |
|----------|-----------|-------------------|----------------------|----------------------|------------------------|--------------|-----------------|
| AOI Sweep N² | CRÍTICO | <1% CPU | 1% CPU | 10% CPU | 100% CPU | Baixa | Impossível |
| Validação Duplicada | ALTO | 2x latência | 2x latência | 2x latência | 2x latência | Baixa | Ruim |
| Talentos não sincronizam | ALTO | Funciona offline | Bugs em PvE | Bugs em PvE | Bugs em PvE | Baixa | Ruim |
| Procs não sincronizam | ALTO | Funciona offline | Bugs em PvE | Bugs em PvE | Bugs em PvE | Baixa | Ruim |
| Handlers duplicados | MÉDIO | Inconsistências | Inconsistências | Inconsistências | Inconsistências | Muito Baixa | Ruim |
| Cooldown em 3 lugares | MÉDIO | Bugs ocasionais | Bugs frequentes | Bugs frequentes | Bugs frequentes | Muito Baixa | Ruim |
| Factory instantiation | MÉDIO | <1% GC | 1% GC | 10% GC | 100% GC | Baixa | Ruim |
| Client-trusting HP/gold | CRÍTICO | Exploits | Exploits | Exploits | Exploits | Baixa | Impossível |
| Skill range/LOS não validados | CRÍTICO | Exploits | Exploits | Exploits | Exploits | Baixa | Impossível |
| Movimento sem walkability | CRÍTICO | Exploits | Exploits | Exploits | Exploits | Baixa | Impossível |

---

## 7. MATRIZ DE RELACIONAMENTOS: Como os Problemas se Conectam

```
Duplicação de Código
  ↓ causa
Inconsistências entre Cliente/Servidor
  ↓ causa
Bugs em Multiplayer (PvE + PvP)
  ↓ causa
Impossível corrigir (correção em um lugar não afeta outro)

Acoplamento de Sistemas
  ↓ causa
Dificuldade de Mudança
  ↓ causa
Correções não funcionam (mudança em Talento quebra Skill)
  ↓ causa
Débito técnico acumula

Falta de Sincronização
  ↓ causa
Estado inconsistente entre Cliente/Servidor
  ↓ causa
Bugs em Multiplayer (outro player vê estado diferente)
  ↓ causa
Impossível escalar (cada novo player = mais inconsistências)

Performance Ruim
  ↓ causa
Impossível adicionar mais players
  ↓ causa
Impossível escalar (máximo 10-20 players simultâneos)
  ↓ causa
Projeto não é viável como MMORPG

Modularidade Baixa
  ↓ causa
Correções afetam múltiplos sistemas
  ↓ causa
Risco de regressão
  ↓ causa
Desenvolvimento lento e custoso
```

---

## 8. RECOMENDAÇÕES POR PRIORIDADE

### FASE 1: Segurança (Semana 1-2)
1. Validação de movimento (walkability)
2. Server-authoritative HP/gold
3. Skill range/LOS validation
4. Proc validation no servidor

### FASE 2: Sincronização (Semana 3-4)
1. Sincronizar talentos no LOGIN_OK
2. Sincronizar procs em STATS_UPDATE
3. Sincronizar cooldown via evento
4. Sincronizar flags de talento

### FASE 3: Performance (Semana 5-6)
1. Implementar spatial hash para AOI
2. Tick loop otimizado
3. Factory cache
4. Object pooling

### FASE 4: Modularidade (Semana 7-10)
1. Desacoplar Talento de Skill
2. Desacoplar Skill de Handler
3. Consolidar validação
4. Separar offline/online

---

## 9. ~~PROBLEMA~~ RESOLVIDO: Globais Mutáveis em game.py (SCREEN_WIDTH/SCREEN_HEIGHT) — Bloqueio para Modularização

> **Status: ✅ resolvido** (Etapa 4, mesma sessão). `SCREEN_WIDTH`/`SCREEN_HEIGHT` foram eliminados de `game.py` — todas as ~55 ocorrências (29 + 26) foram substituídas por `self.screen.get_width()`/`self.screen.get_height()`, que consultam o `pygame.Surface` ativo (única fonte de verdade, sempre atual — `_apply_scale` apenas reatribui `self.screen` a uma nova `Surface`, sem necessidade de sincronizar nenhum cache). As declarações `global SCREEN_WIDTH, SCREEN_HEIGHT` e as constantes de módulo (`game.py:56-57`) foram removidas por completo. Esse já era o padrão usado em parte do código (`game.py:2875`, `3363` — `sw, sh = self.screen.get_width(), self.screen.get_height()`), então a migração tornou o código **consistente**, não introduziu um padrão novo. Verificado: diff byte-a-byte confirma que SOMENTE as linhas que referenciavam `SCREEN_WIDTH`/`SCREEN_HEIGHT` mudaram; smoke test confirma que os novos valores acompanham trocas de resolução em runtime. Texto original do problema preservado abaixo para referência histórica.

**Descoberto durante:** Etapa 4 (extração de mixins de `game.py`, refatoração `GameEngine` → `init+loop+render`).

**O problema:**
`SCREEN_WIDTH`/`SCREEN_HEIGHT` (e também `DEBUG_MODE`/`PROFILE_FRAMES`) são variáveis de **módulo** em `game.py` (`game.py:55-56`), declaradas `global` e reatribuídas em runtime — em `__init__` (`game.py:160-162`) e em `_apply_scale` (`game.py:3220-3224`, chamado ao trocar resolução/escala da janela). Cerca de 30+ métodos de desenho (`_draw_hud`, `_draw_hotbar`, `_draw_consumable_bar`, `_draw_inventory_panel`, `_draw_tooltip`, `_mm_overlay`, `_draw_habilidades_panel`, `_handle_debug_click`, etc.) leem essas variáveis como **nomes livres de módulo** (não `self.SCREEN_WIDTH`).

**Por que isso bloqueia a modularização (Etapa 4):**
O padrão de extração mecânica usado com sucesso em `NetworkHandlers`/`RemoteEntityHandlers`/`SaveSyncHandlers` (mixin sem `__init__`, zero reescrita de `self.`, verificação byte-idêntica) **não é seguro** para esses métodos: mover um método que lê `SCREEN_WIDTH` como nome livre para outro módulo faz Python resolver o nome no namespace global do **módulo onde a função foi definida** (o novo arquivo), não em `game.py`. Um `from game import SCREEN_WIDTH` capturaria apenas um snapshot — o novo módulo nunca veria mudanças feitas por `_apply_scale` em runtime, gerando bugs sutis (painéis mal posicionados após trocar resolução).

**Escopo do impacto:** praticamente todos os grupos de métodos de HUD/desenho restantes em `game.py` (telas de menu, tooltip, inventário, hotbar, debug modal, HUD online) têm pelo menos um método com essa dependência — ou seja, isso afeta a maior parte do trabalho que falta para "Etapa 4: `game.py` volta a ser apenas init+loop+render".

**Recomendação (pré-requisito para continuar a Etapa 4 nas telas de HUD):**
Substituir os globais mutáveis por estado acessível via `self` (ex.: `self.SCREEN_WIDTH`/`self.SCREEN_HEIGHT` setados em `__init__`/`_apply_scale`, lidos como `self.SCREEN_WIDTH` nos métodos de desenho) ou por um pequeno container mutável compartilhado (ex.: `display_state.WIDTH`/`display_state.HEIGHT`, mesmo objeto importado por `game.py` e pelos mixins). Isso é uma reescrita mecânica e bem delimitada (~30 ocorrências de `SCREEN_WIDTH`/`SCREEN_HEIGHT` em `game.py`, busca-e-substituição por nome com limites de palavra), mas é um passo à parte da extração em si — deve ser feito e validado (smoke test trocando resolução em runtime) **antes** de extrair qualquer grupo de métodos de desenho que dependa dessas variáveis.

---

*Documento elaborado em 06 de junho de 2026. As informações contidas são de responsabilidade do solicitante.*

---

## Análise Arquitetural — 16 de junho de 2026

> Verificar antes de atacar cada item: alguns podem já ter sido resolvidos desde então.

---

## Resumo da sessão de refatoração arquitetural — 03 de julho de 2026

Branch `rpg-online-2026-06-22`, 6 commits (312425e..e1ac5bf). Suíte com a
mesma baseline em cada etapa (34 falhas pré-existentes); servidor sobe
limpo após cada mudança.

1. **perf(ui)**: `fonts.CachedFont` (memoização de `font.render` na própria
   fonte — cobre os ~260 call sites de UI de uma vez) + `ui_helpers.fill_surf`
   (Surfaces SRCALPHA pré-preenchidas cacheadas; 46 sites convertidos;
   overlays de cooldown usam `blit(area=)` p/ não churnar o cache).
   Consumidores que mutam a surface (fade via `set_alpha`: floating_text,
   combat_log) usam cópia própria — a surface do cache é compartilhada.
2. **`utils.snap_to_tile`**: único helper de teleporte/knockback — corrigiu
   3 bugs latentes da classe "tween sobrevive ao snap" (respawn, revive,
   empurrão de mob) e centralizou os snaps já corretos. Interceptar
   (`skill_processor:~317`) deliberadamente NÃO migrado (tween próprio).
3. **`stat_fns.recalculate_combat_stats`**: recálculo de modifiers
   data-driven (`_MODIFIABLE_ATTRS`/`_STAT_CLAMPS`) — substitui a cadeia de
   ~60 linhas de elif em `components.py`. Atributo novo = 1 entrada na
   tabela. Paridade byte-exata verificada em 6 fixtures.
4. **Problema F (parcial→substancial)**: novo `world_systems.py` (14
   sistemas de gameplay headless + `_svc`/`deal_damage`/etc., ZERO pygame
   no import — verificado em runtime) + `fx.py` (façade FLT/PROC/WARN/
   DASH_TRAIL/SOUNDS com proxies no-op; cliente vincula via
   `fx.bind_client_fx()` no `GameEngine.__init__`). `systems.py`
   (6800→~4200 linhas) re-exporta tudo — zero call site quebrado; servidor
   importa de `world_systems`. **Nó restante**: `SkillSystem` (input/UI)
   ainda vem de `systems.py` no servidor — separação completa exige um
   `ServerSkillSystem` sobre `SkillHandlers` (sessão dedicada).
5. **Problema G (resolvido)**: `WorldServer.queue_stats_update(entry)` é o
   único ponto de entrada do canal (valida `player_eid` na origem, schema
   documentado na docstring); placeholders `xp=0/mob_eid=-1` eliminados dos
   16 produtores; renomeado p/ `_pending_stats_updates`/`consume_stats_updates`.
   Wire (STATS_UPDATE) idêntico — cliente já lia com `.get()` + defaults.
6. **Problemas B/H (resolvidos)**: `core_systems.apply_damage_core` — núcleo
   único da escrita final de HP (guards morto/imune, overkill preservado,
   quebra de polymorph/sleep com cancelamento do slow encadeado,
   PendingDeath opcional, hook `on_cc_break` p/ FLT). `deal_damage`,
   `_apply_final_damage`, `_apply_magic_damage` e a retaliação do Escudo
   de Fogo viram delegates. Regra nova de mitigação entra num lugar só.

⚠️ **Incidente OneDrive (03/07)**: o OneDrive corrompeu o índice do git no
meio de um commit (commit gravado com árvore VAZIA). Recuperado via
`git reset` pro último commit íntegro + re-commit. **Recomendação forte**:
mover o repositório para fora da pasta sincronizada ou excluir `.git/` da
sincronização.

### Rodada 2 (03/07) — broadcasting e autorização de skills

Análise profunda de talentos/skills/broadcasting encontrou e corrigiu:

1. **CRÍTICO — vazamento cross-map em broadcasts diretos (resolvido)**: o
   filtro de mapa (MapLocation) existia SÓ no AOI_UPDATE; os ~9 loops de
   broadcast direto (SKILL_RESULT, SKILL_EFFECT, corpses, sons de aggro,
   ENTITY_DEATH, HP de cura, chat/ENTITY_MOVE) filtravam só por distância de
   tile — players em MAPAS diferentes com coords próximas recebiam eventos
   um do outro. Fix: `SessionManager._sessions_in_aoi(tx, ty, map_file,
   origin_eid)` é o ÚNICO filtro de destinatários (mapa + AOI + visibilidade);
   todos os loops migrados. Produtores de eventos pós-remoção (loot/corpse)
   carregam `"map"`; o resto resolve via `WorldServer.get_entity_map()`.
2. **CRÍTICO — servidor não validava SE o player pode usar a skill
   (resolvido)**: qualquer `sid` do SKILL_CATALOG era executado (único gate
   real: custo de recurso no handler). Fix:
   `world_systems.is_skill_authorized()` (classe via class_id do catálogo +
   talento via TalentTree autoritativo + aprendizado via learned_skill_ids)
   chamado no início de `_process_skill_requests`; rejeição vira SKILL_RESULT
   failed + reason (WARN no cliente). Nota: TODA skill é de treinador
   (INITIAL_SKILLS_BY_CLASS vazio por design) — personagem novo não casta
   nada até aprender. Gap remanescente (pré-existente): a COMPRA no
   treinador ainda é client-side (learned sync via SAVE_STATE).
3. **ALTO — eventos de skill vazavam posição de caster camuflado
   (resolvido)**: `_sessions_in_aoi(origin_eid=caster)` aplica `_can_see`
   — quem não vê o caster não recebe SKILL_RESULT/SKILL_EFFECT; o dono
   sempre recebe.
4. **MÉDIO — métrica de AOI inconsistente (resolvido)**: broadcasts diretos
   e `_build_update_for_session` usavam círculo Euclidiano; spawn inicial
   usava Chebyshev (`utils.in_aoi`, a canônica). Padronizado TUDO em
   Chebyshev (inclusive a histerese de saída).
5. **MÉDIO — completion de spell mandava `cooldown: None` (resolvido)**:
   cliente caía no CD base do catálogo enquanto o servidor validava com o
   CD efetivo (pós-talento) — mesma classe do bug de rejeição falsa do
   Interceptar. Completion agora envia `_skill_effective_cd`.
6. **BAIXO — `deltas["stats"]` sempre vazio (removido)**.
7. **BAIXO (aberto)** — apply de talentos duplicado cliente
   (`TalentSystem.apply_talent_effects`) vs servidor
   (`_apply_talent_modifiers`): divergência concreta = cliente reseta
   `fire_crit_counter/timer`, servidor não. Candidato a `talent_logic.py`
   (molde do quest_logic) numa rodada futura.

---

## Resumo da sessão de correções — 21/22 de junho de 2026

Passada por todos os itens A–M + K listados abaixo. Branch: `rpg-online-2026-06-22`
(2 commits, push feito). Suíte completa + diagnósticos sem regressão em cada etapa.

**Resolvidos de fato (bug real corrigido):**
- **I** — `RESPAWN_TILE` hardcoded em 3 lugares → centralizado em `shared/constants.py`.
- **K** — bug real confirmado: auto-attack do servidor não checava sleep/disoriented/
  polymorph (só skills checavam) — player adormecido continuava causando dano.
  Centralizado em `utils.is_action_locked()`.
- **D** — duração de "exhaustion" (Pirofagia) divergia entre cliente (hardcoded
  `6.0`) e servidor (lia do catálogo) — cliente agora lê do mesmo `SKILL_CATALOG`.
- **B** — overkill do dano mágico estava sendo descartado (`max(0, ...)`) —
  removido, consistente com `deal_damage()`/`_apply_final_damage()`.
- **M** — import de `apply_effect` trocado de `systems` (arrasta pygame) pra
  `core_systems` no servidor, 7 ocorrências.
- **F (parcial)** — `server/main.py` não setava `SDL_VIDEODRIVER`/`SDL_AUDIODRIVER`,
  crasharia em deploy headless real. Corrigido com `setdefault`, mesmo padrão já
  validado em `tests/helpers.py`.

**Já estavam resolvidos, só status desatualizado:**
- **A** — docs já diziam 30 ticks/s consistentemente.
- **C** — Escudo de Fogo já tinha o guard `is_immune` + overkill preservado.
- **E** — `BaseCombatStateSystem` já era herdado por cliente e servidor.

**Investigados, sem bug ativo, mas precisam de refactor maior (não corrigidos):**
- **H** — auto-attack melee não passa por `_apply_final_damage`, mas `deal_damage`
  já tem os mesmos guards de forma independente — risco é só prospectivo.
- **G** — `_pending_xp_deliveries` sem schema, mas todos os 15 produtores (não só
  os 3 citados) sempre incluem `xp`/`mob_eid` — sem risco de crash, só manutenibilidade.
- **F (completo)** — separar `EnemyAISystem`/`CombatSystem`/etc. de Pygame de
  verdade (não só evitar o crash) continua pendente.

**IU3** (modal stack de UI em `game.py`) — implementado em rodada separada,
escopo completo (ModalStack + DragState ECS). Ver seção própria abaixo.

**Regressão encontrada e corrigida depois desta rodada (mesmo dia):** Tiro
Repulsivo voltou a causar "sprint"/correção de posição visual no mob após o
knockback. Causa raiz: `_server_tiro_repulsivo` escreve `Position`/`current_tile`
do alvo diretamente e de forma instantânea (sem tween server-side), mas nunca
resetava `TileMovement.is_moving` — se o alvo estava no meio de um passo normal
de chase da IA (`is_moving=True`) no momento exato do impacto, esse passo
"sobrevivia" ao knockback: no tick seguinte, `TileMovementSystem` recalculava
`Position` usando `start_pixel`/`target_pixel` ANTIGOS (do passo de chase
interrompido), sobrescrevendo a posição correta do knockback — visualmente
parecendo um "sprint" de correção. Fix: `t_tm.is_moving = False` explícito no
início de `_server_tiro_repulsivo`, antes de aplicar o empurrão. Validado com
diagnóstico reproduzindo o cenário exato (mob mid-chase-step, 40% de um passo,
atingido por Tiro Repulsivo) — posição agora permanece correta no tick seguinte.
Mesma classe de bug pode existir em outros pontos que escrevem `current_tile_x/y`
direto sem passar por `start_tile_movement()` (ex.: Interceptar em
`server/skill_processor.py:317-318`) — não investigado a fundo nem corrigido
nesta rodada (fora do escopo pedido).

---

### 🔴 CRÍTICO A — `TICK_RATE` desalinhado entre código e docs

**Arquivos:** `shared/constants.py:10`, `arquitetura/ARQUITETURA_ONLINE.md`

Código roda a 30 TPS mas documentação diz "20 ticks/s". Cálculos de `LAG_COMP_WINDOW_MS / TICK_RATE`, autosave, snap history — tudo errado para quem implementar baseado nos docs.

**Fix:** Alinhar docs para 30 TPS em 1 PR. `TICK_INTERVAL_MS = 1000/30 ≈ 33ms`.

**Status:** ✅ resolvido — `shared/constants.py:10` (`TICK_RATE = 30`), `CLAUDE.md`, `ARQUITETURA_ONLINE.md`, `SISTEMAS_ECS.md` e `Análise Externa.md` já citam consistentemente "30 ticks/s". Nenhuma ocorrência de "20 ticks" encontrada em nenhum doc (verificado via grep em todo `arquitetura/*.md` + `CLAUDE.md`).

---

### 🔴 CRÍTICO B — `_apply_magic_damage` (cliente) duplica gate de dano do servidor

**Arquivos:** `spell_system.py:42-81`, `server/spell_completion_processor.py:508-534`

Servidor tem `_apply_final_damage` como único ponto de modificação de HP, com guards de `is_immune`, overkill preservado, quebra de polymorph/sleep. Cliente tem `_apply_magic_damage` reimplementado à mão — usa `max(0, ...)` (descarta overkill), não quebra `sleep`, usa `ai.state = "CHASING"` em vez de `"AGGRO_DELAY"`. Cada nova regra no servidor precisa ser espelhada manualmente no cliente.

**Fix:** Extrair para `core_systems.py` com hook visual/broadcast substituível por subclasse.

**Status:** ✅ resolvido — 2 das 3 divergências já tinham sido corrigidas (comentários
no próprio código dizem "alinhado com servidor"): `_apply_magic_damage` já quebra
sleep (linha 66-69) e já usa `AGGRO_DELAY` (linha 81), não mais `CHASING`. Restava só
o clamp de overkill (`max(0, ...)`) — removido agora, current_hp pode ficar negativo
igual a `deal_damage()`/`_apply_final_damage()`. A função já trata a própria morte
inline (`PendingDeath` adicionado diretamente ao detectar `current_hp <= 0`), então
não dependia de overkill preservado pra nenhum consumidor externo — não é mais
necessário o refactor grande (extrair pra `core_systems.py` com hooks) pra resolver
o que estava realmente quebrado; ainda é desejável como limpeza arquitetural futura,
mas não é mais um bug.

---

### 🔴 CRÍTICO C — Escudo de Fogo bypassa `_apply_final_damage`

**Arquivos:** `systems.py:693-704`

```python
_att_cs.current_hp = max(0, _att_cs.current_hp - _retaliation)  # sem is_immune check
```

Jogador com Bloco de Gelo (`is_immune=True`) recebe dano de retaliation. Overkill descartado → morte por Escudo de Fogo não detectada pelo diff de snapshot.

**Fix:** Chamar `_apply_final_damage(attacker_id, _retaliation)` ao invés do write direto.

**Status:** ✅ resolvido — código atual (`systems.py:695-696`) já tem
`not (_att_cst and _att_cst.is_immune)` no guard e `_att_cs.current_hp -= _retaliation`
sem `max(0, ...)` (overkill preservado). Sintoma do bug não existe mais — o write
ainda é direto (não roteado por `_apply_final_damage`, que é método server-only e
não existe no lado offline/cliente onde `deal_damage` também roda), mas isso é uma
questão de modularidade arquitetural, não mais um bug de comportamento.

---

### 🔴 ALTO D — `StatusEffects.effects[k] = ...` direto bypassa `apply_effect`

**Arquivos:** `server/spell_completion_processor.py:719,727`, `spell_system.py:1487,1499`

"Exhaustion" (Pirofagia) criado manualmente com duração hardcoded diferente entre servidor (`_exh_dur` variável) e cliente (`6.0` fixo). `apply_effect` faz refresh correto em high-latency; código manual sobrescreve direto → stacking incorreto sob lag.

**Fix:** Adicionar "exhaustion" ao `EFFECT_DEFS`, usar `apply_effect` nos dois contextos.

**Status:** ✅ resolvido (parcial, ver nota) — `"exhaustion"` já estava em `EFFECT_DEFS`
(`status_effects_data.py`). O gap real era a duração: cliente (`spell_system.py`)
hardcoded `6.0` enquanto servidor lia `SKILL_CATALOG["bola_de_fogo"]["effect_durations"]["exhaustion"]`.
Cliente agora lê da mesma fonte (`SKILL_CATALOG`) em vez do literal — elimina o
risco de divergência se o catálogo for ajustado. **Nota:** a construção manual de
`ActiveEffect` (stacking incremental: magnitude = min(atual+1, 5)) continua direta
em ambos os lados — não foi substituída por `apply_effect()` porque essa função só
suporta "criar" ou "refresh pelo maior valor", sem semântica de incremento/stack;
unificar isso exigiria estender `apply_effect()` ou extrair a lógica de stacking
pra um helper compartilhado, o que é uma refatoração maior, não um bugfix pontual.

---

### 🔴 ALTO E — `CombatStateSystem` duplicado entre offline e servidor

**Arquivos:** `core_systems.py:234-306`, `server/world_server.py:1608-1619`

`ServerCombatStateSystem` é reimplementação headless do `CombatStateSystem` offline. Rage decay, HP5, timed_modifiers — qualquer mudança em um não se reflete no outro. Os próprios arquivos de arquitetura documentam isso como "duplicação conhecida".

**Fix:** Mover lógica sem dependência Pygame para `core_systems.py` com hooks virtuais (mesmo padrão de `StatusEffectSystem`).

**Status:** ✅ resolvido — já existe `core_systems.BaseCombatStateSystem` (timers de
combate, rage decay, HP5, concentração) herdado tanto por `core_systems.ServerCombatStateSystem`
(servidor, adiciona `hp5_events`) quanto por `systems.CombatStateSystem` (cliente,
adiciona wander de disoriented/polymorph, timer de camuflagem visual, timed_modifiers,
procs). Confirmado via leitura direta: `CombatStateSystem.update()` só CHAMA os
métodos herdados (`_tick_combat_timer`, `_tick_rage_decay`, etc.), não reimplementa
nada — mudança na lógica base se reflete nos dois automaticamente.

---

### 🔴 ALTO F — `systems.py` importa `pygame`/`SOUNDS`/`FLT` no topo — servidor arrasta tudo

**Arquivos:** `systems.py:3,34-37`, `skill_handlers.py:25-27`

```python
import pygame          # linha 3 de systems.py
from sound_manager import SOUNDS   # inicia pygame.mixer
from floating_text import FLT
```

Servidor importa `EnemyAISystem` de `systems.py` → arrasta Pygame inteiro. Workaround atual é `SDL_VIDEODRIVER=dummy`. Em containers headless sem SDL isso crasha. Viola a separação inviolável cliente/servidor documentada.

**Fix:** Mover sistemas headless para arquivo separado sem imports Pygame no topo. `skill_handlers.py` já usa lazy imports — padronizar para `SOUNDS`/`FLT`/`LOG` também.

**Status:** ✅ resolvido (parcial, ver nota) — o risco OPERACIONAL real (servidor
crasha em deploy headless de verdade) estava em `server/main.py`: ele nunca setava
`SDL_VIDEODRIVER`/`SDL_AUDIODRIVER`, dependia de alguém exportar isso manualmente
antes de rodar. Adicionado `os.environ.setdefault(...)` pros dois, mesmo padrão já
usado e validado em `tests/helpers.py` a sessão inteira — confirmado via teste:
importar `server.main` num shell limpo (sem as env vars pré-setadas) agora seta
os drivers ANTES de `systems.py` ser importado, sem crash. **Nota:** isso não
elimina a causa raiz (servidor ainda importa Pygame de verdade na memória, só não
crasha mais) — a separação arquitetural completa (mover `EnemyAISystem`/
`CombatSystem`/etc. pra um módulo sem import de Pygame no topo) continua pendente,
é um refactor maior e separado tocando a estrutura toda de `systems.py`.

---

### 🔴 ALTO G — `_pending_xp_deliveries` transporta 12+ tipos de payload sem schema

**Arquivos:** `server/skill_processor.py:362-473`, `server/spell_completion_processor.py:174-241`, `server/combat_processor.py:337-347`

Canal batizado "xp_deliveries" mas carrega: XP, rage, mana, HP, heal_amount, talent_points, `proj_incoming`, `proj_caster`, `proj_target`, `applied_effects`, `effect_durations`, `concentration`. Cada consumer verifica `if entry.get("proj_incoming")` antes de assumir que é HP sync. Um novo tipo de sync tem que entrar aqui ou criar novo canal ad-hoc.

**Fix:** Separar em canais tipados (`_pending_stat_updates`, `_pending_proj_notifs`, `_pending_xp_deliveries`) ou adicionar campo `"kind"` obrigatório com dispatch explícito.

**Status:** 🔍 Investigado, sem bug ativo encontrado — auditados TODOS os 15
produtores do canal (mais que os 3 arquivos citados originalmente: também
`server/world_server.py`, 6 ocorrências). Toda entrada, sem exceção, sempre
inclui `"xp"` e `"mob_eid"` como placeholder (`0`/`-1`) mesmo quando a entrada
não é sobre XP de kill — o medo de um produtor esquecer esses campos obrigatórios
(causando `KeyError` no consumer) não se confirmou. O problema permanece real
como **manutenibilidade** (15 produtores escrevendo no mesmo bag, um campo novo
precisa entrar aqui em vez de um canal próprio), mas não é uma correção isolada
e segura — exige tocar os 15 call sites + o dispatch em `server/session.py`.
Mesmo escopo de F (separação arquitetural completa) — fica pra uma sessão
dedicada de refactor, não bugfix.

---

### 🟡 MÉDIO H — Auto-attack melee não passa por `_apply_final_damage`

**Arquivos:** `server/combat_processor.py:102-107`

Auto-attack ranged usa `_server_apply_ranged_physical` (que chama `_apply_final_damage`). Auto-attack melee chama `deal_damage(player_eid, target_eid, "physical")` diretamente — fora do gate. Resistência a dano físico adicionada a `_apply_final_damage` não protegeria contra melee.

**Fix:** Rotear melee pelo mesmo `_apply_final_damage`.

**Status:** 🔍 Investigado, sem fix pontual seguro — verificado que HOJE não há
divergência de comportamento ativa: `deal_damage` (`systems.py`, usado por melee
E por quase toda skill de guerreiro) já implementa, de forma independente,
exatamente os mesmos guards que `_apply_final_damage` tem — `is_immune` (linha
587-588) e quebra de polymorph/sleep no impacto (linha 666-678, inclusive com FLT,
mais completo que a versão silenciosa de `_apply_final_damage`). O risco descrito
no doc é **prospectivo**: uma nova guarda adicionada só em `_apply_final_damage`
não protegeria melee. Não dá pra "rotear melee pelo mesmo `_apply_final_damage`"
como um fix pontual — `_apply_final_damage` é método server-only
(`server/spell_completion_processor.py`), e `deal_damage`/`CombatSystem` é
compartilhado client+server (roda no client offline, onde aquele método nem
existe). A correção real é mover a lógica comum pra `core_systems.py` com hooks
virtuais (mesmo padrão de `StatusEffectSystem`) — isso é o mesmo escopo dos itens
B e E, não uma correção isolada. Agrupado com eles para discussão de escopo antes
de tocar.

---

### 🟡 MÉDIO I — `RESPAWN_TILE` hardcoded em 3 lugares

**Arquivos:** `server/respawn_system.py:40`, `server/auth.py:29`, `server/session.py:647`

`(115, 389)` literal em `auth.py` sem usar a constante de classe. Mover o cemitério quebra silenciosamente se só um lugar for atualizado.

**Fix:** Mover para `shared/constants.py`.

**Status:** ✅ resolvido — `RESPAWN_TILE` adicionado a `shared/constants.py`.
`server/respawn_system.py::RespawnMixin.RESPAWN_TILE` e os defaults de spawn em
`server/auth.py` (`_TEST_ACCOUNTS` e `_create_character_sync`) agora importam de
lá. `server/session.py:671` já usava `_RS.RESPAWN_TILE` (a constante de classe),
então segue correto automaticamente.

---

### 🟡 MÉDIO K — `can_act()`/`can_move()` como comportamento dentro do componente

**Arquivos:** `components.py:487-494`

```python
def can_act(self) -> bool:
    return self.is_alive and not self.is_stunned and not self.is_casting
```

Regra de gameplay no componente. `sleep` bloqueia ação mas não está em `can_act()` — está checado separadamente em `skill_processor.py:37-39`. 3 pontos de verificação de CC distintos que podem divergir.

**Fix:** Helper em `core_systems.py` ou `stat_fns.py` que recebe `CombatState + StatusEffects` e centraliza a regra de CC.

**Status:** ✅ resolvido — confirmada divergência REAL (não só risco teórico):
`server/combat_processor.py::_process_player_attacks` (gate de auto-attack) checava
só `can_act()`, sem o check de sleep/disoriented/polymorph que `skill_processor.py`
já tinha — ou seja, um player adormecido/desorientado/polimorfizado conseguia
continuar auto-atacando no servidor (só skills eram bloqueadas). Criado
`utils.is_action_locked(world, entity_id)` — único ponto de verdade para os 3 CCs
que vivem em `StatusEffects` (não em `CombatState`, por isso `can_act()` nunca os
cobre). Aplicado em: `combat_processor.py` (bug real corrigido — antes faltava),
`skill_processor.py`, e os 2 pontos client-side em `systems.py`
(`_use_skill_visual_only` online + offline). Validado com diagnóstico: player
dormindo antes do fix continuaria causando dano; depois do fix, 20 ticks dormindo
não causam nenhum dano. `can_act()` continua em `components.py` (não extraído pra
`core_systems.py`/`stat_fns.py` como o doc sugeria) — é só is_alive/is_stunned/
is_casting/is_camouflaged, todos campos do próprio `CombatState`; mover pra fora
do componente sem motivo concreto seria reescrita sem ganho real.

---

### 🟢 BAIXO M — `from systems import apply_effect` no servidor arrasta Pygame

**Arquivos:** `server/spell_completion_processor.py` (8 ocorrências)

`apply_effect` está em `core_systems.py`. Servidor importa via `systems.py` que tem `import pygame` no topo. Solução: trocar para `from core_systems import apply_effect` — mudança de 1 linha cada.

**Fix:** Trocar as 8 ocorrências de import.

**Status:** ✅ resolvido — todas as 7 ocorrências encontradas em
`server/spell_completion_processor.py` (doc citava 8, busca atual confirma 7)
trocadas para `from core_systems import apply_effect`. Confirmado via grep: zero
ocorrências restantes de `from systems import apply_effect` em todo `server/`.

---

### 🟡 MÉDIO IU3 — Sistema de UI sem modal stack / focus

**Arquivos:** `game.py`, `systems.py` (ShopSystem, CraftingSystem, TrainerSystem, etc.)

Não existe conceito de "modal em foco". Cada sistema gerencia seus próprios eventos independentemente. `game.py` faz roteamento manual ad-hoc para cada handler. `systems_events` é um patch que bloqueia apenas `MOUSEBUTTONDOWN` quando painéis estão abertos — outros tipos de evento (KEYDOWN, MOUSEMOTION) vazam para sistemas que não deveriam recebê-los.

**Consequências:**
- Skills ativam via hotbar quando modais estão abertos
- Tooltip de itens aparece por baixo de modais sobrepostos
- Cursor do mouse responde a elementos cobertos por modal
- Arrastar (drag-drop) de itens não tem estado ECS explícito — estado implícito em `game.py`
- Sem hierarquia clara de quem consome o evento primeiro

**Fix correto (refatoração futura):**
Implementar `ModalStack` em `game.py`:
- Lista de modais abertos em ordem de Z
- Loop de eventos pergunta ao modal no topo primeiro; só repassa se não consumido
- Sistemas ECS só recebem eventos quando `modal_stack` está vazio
- Interações drag-drop explicitadas como componente ECS (`DragState` no player entity): `drag_item`, `drag_source`, `drag_pos` — render lê o componente, input escreve nele

**Status:** ✅ resolvido — implementado com escopo completo (não só o patch
mínimo de filtro), em plano dedicado (ver
`C:\Users\l4nce\.claude\plans\async-dazzling-badger.md` no histórico da sessão).

- **Bug confirmado** (não só risco teórico): `systems.py:1337` (TAB cicla
  alvo via `MouseTargetingSystem`), `systems.py:1570` (SPACE engaja combate
  via `PlayerInputSystem`) e `systems.py:5892-5899` (teclas de hotbar
  disparam skill via `SkillSystem`) todos liam `KEYDOWN` de `systems_events`
  sem filtro — disparavam gameplay com qualquer modal aberto.
- **`ModalStack`**: novo `client/modal_stack_handlers.py`
  (`ModalStackHandlers`) — registry estático `_modal_registry()` (lista, não
  pilha mutável — a ordem de prioridade é fixa, igual à que já existia
  hardcoded em `_close_top_modal`, só não era reutilizável). Expõe
  `_topmost_open_modal()`/`_any_modal_open()`, usados tanto por
  `_close_top_modal` (refatorado, mesmo comportamento, agora um wrapper sobre
  o registry) quanto pelo filtro de `systems_events` em `game.py`, que agora
  bloqueia `MOUSEBUTTONDOWN`/`UP`/`KEYDOWN`/`MOUSEMOTION`/`MOUSEWHEEL` (antes
  só `MOUSEBUTTONDOWN`) quando qualquer modal está no topo — preservadas as
  exceções existentes (`LootSystem` sempre recebe eventos brutos; editor de
  hotbar/modal de quantidade já bloqueavam tudo; right-click consumido entre
  NPCs adjacentes continua só bloqueando o próprio clique).
- **`DragState` ECS** (`ui_components.py`, registrado em
  `entity_factory.py` junto de `UIState`): unificou os 4 mecanismos de
  drag-and-drop de item/skill que antes eram `self.*` solto em `game.py`
  (`_hab_drag_skill`, `_inv_drag_item`, `_hotbar_drag_idx`/`_hb_drag_*`,
  `_cbar_drag_idx`/`_cbar_drag_*` — todos removidos). Campos genéricos
  (`kind`, `payload`, `source`, `source_idx`, `start_pos`, `active`, `shift`)
  cobrem os 4 mecanismos (habilidades→hotbar, inventário→consumable bar,
  reorder/remover na hotbar, idem consumable bar). Ganho colateral: fechar
  todos os modais (`_close_all_modals`) agora cancela qualquer drag em
  andamento com 1 linha (`self._get_drag().reset()`), antes só limpava o
  drag de habilidades, os outros 3 ficavam pendurados.
- **Fora de escopo, deliberado**: `_sound_drag` (slider de volume, não é
  item drag) e `_hbe_drag_from`/`_hbe_cons_drag_from` (drag interno do
  editor de hotbar — já 100% contido enquanto `_show_hotbar_editor` bloqueia
  todo `systems_events`, nenhum outro sistema lê esses campos).
- Validado: suíte completa sem regressão (mesma baseline de 32 falhas
  pré-existentes), compile limpo em todos os arquivos tocados, smoke test de
  cliente real (conecta ao servidor e roda o loop principal sem exceção).
  **Não validado**: interação manual em janela real (abrir cada modal,
  testar drag de cada mecanismo) — sem ferramenta de automação de GUI
  disponível pra Pygame nativo; precisa de passada manual do usuário.

---

### 🟡 MÉDIO IU4 — Responsividade de UI (escala não propagada pra geometria)

**Sintoma relatado pelo usuário:** "tem muitos problemas na interface, textos
em cima de textos, botões encavalados" — ocorre ao mudar a "Escala da UI" no
menu de pausa (`self._ui_scale`, 0.5x-3.0x, controlado em
`client/menu_handlers.py`) pra qualquer valor diferente de 1.0.

**Causa raiz confirmada:** `GameEngine` já tinha um helper `self._u(px)`
(`game.py`) que converte um pixel "base" pro valor escalado
(`max(1, round(px * self._ui_scale))`), e `_reload_ui_fonts()` já recarregava
`self.font_xs/sm/md/lg` na escala certa — mas **`self._u()` tinha zero usos em
todo o codebase**. Toda geometria de painel (largura/altura/padding/row
height/ícone/offset) era pixel fixo. Fonte escalava, caixa não — daí o
encavalamento.

Pior: ~8 painéis (Forja/Reciclagem, Trainer, Loja, diálogo de quest, diário de
quest, tracker de quest no HUD, Loot, Talentos, Mapa) são implementados como
classes `System` *separadas* de `GameEngine` (`BlacksmithSystem`,
`TrainerSystem`, `ShopSystem`, `QuestSystem`/`QuestDialogSystem`/
`QuestJournalSystem`, `LootSystem`, `TalentSystem`, `MapOverlay`) — cada uma
criava sua própria fonte uma única vez no `__init__` (`self._font_sm =
_font(20)`) e nunca mais atualizava. Essas nem reagiam à escala, com ou sem
geometria escalada.

**Fix aplicado:**
- Novo `ui_scale_mixin.py` (`UIScaleMixin`): subclasses declaram
  `_FONT_BASES = {"_font_sm": 20, ...}`; o mixin expõe `self._u(px)` e
  `self.set_ui_scale(scale)` (idempotente, recria as fontes declaradas).
  `GameEngine` empurra `self._ui_scale` pra essas 8 classes na construção e de
  novo em `_set_ui_scale()` (toda vez que o usuário muda o slider).
- Todas as 8 classes `System` separadas + todos os `_draw_*`/`_handle_*`
  client-side em `game.py` e `client/*_handlers.py` (debug, habilidades,
  inventário, hotbar, hotbar editor, consumable bar, menu de pausa, HUD,
  tooltip genérico) tiveram a geometria envolvida em `self._u(...)`.
- `_HB_W`/`_HB_H`/`_HB_ICO`/`_HB_PAD` (`client/hotbar_handlers.py`) eram
  atributos de classe fixos lidos por hotbar/consumable_bar/habilidades —
  virou `@property` calculada (`self._u(68)` etc.), já que atributo de classe
  não pode chamar `self._u()`.
- `god_mode.py` (`GodModeEditor`) **excluído deliberadamente** — já tem
  mecanismo próprio (`_refresh_ui_scale()`) que reage à resolução da janela
  (`screen.get_height()/720.0`); é um conceito de escala diferente (resolução,
  não a "Escala da UI" do menu) e já funciona — não tocado, pra não quebrar o
  que já está certo. Ferramenta de admin/dev, prioridade baixa mesmo.

**Execução:** dado o volume (10 arquivos, ~400 literais), o trabalho mecânico
de empacotar literais em `self._u()` foi delegado a sub-agentes em paralelo
(um por arquivo/classe), com instruções e exemplo de transformação idênticos
em todos; toda a parte arquitetural (criação do mixin, wiring de
`GameEngine`, decisão de excluir `god_mode.py`) foi feita diretamente. Nenhum
sub-agente tinha acesso a shell no sandbox — toda a verificação de compile
(`py -3.10 -m py_compile`) foi feita depois, por fora, em todos os arquivos.

**Status:** ✅ resolvido (mecânica) — compila limpo em todos os arquivos
tocados, suíte sem regressão (mesma baseline de 32 falhas pré-existentes),
smoke test de cliente real com `ui_scale=1.25` (valor já salvo no config de
um teste anterior do usuário) conectando e rodando sem exceção — isso
confirma que a construção de todas as 8 classes `System` (incluindo o MRO
novo do `UIScaleMixin`) não quebrou nada na inicialização.
**Não validado**: abrir cada um dos ~13 painéis manualmente em 2-3 valores de
escala (0.5x/1.0x/2.0x/3.0x) e confirmar visualmente ausência de
overlap/vazamento de texto — sem ferramenta de automação de GUI pra Pygame
nativo; precisa de passada manual do usuário antes de considerar
definitivamente fechado.

**Regressão encontrada pelo usuário (mesmo dia, via screenshots) e corrigida:**
o fix acima escalava painéis pra cima sem considerar se ainda cabiam na
resolução atual da janela. Com `ui_scale=1.25` numa janela 1280×720 (1x), o
painel de Inventário (`_PANEL_W=720, _PANEL_H=660` → escalado pra 900×825)
ficava mais alto que a própria tela, e o modal de Debug (`820×620` →
1025×775) também — conteúdo cortado/fora da área visível, exatamente o
sintoma reportado ("alguns modais estão fora da tela"). **Causa raiz:**
`self._u()` só conhecia `self._ui_scale` (preferência do usuário), nunca a
resolução real da janela — escala e "cabe na tela" são dois eixos
independentes, e só o primeiro estava sendo considerado.

**Fix:** novo `self._set_panel_scale(design_w, design_h, margin=20)` em
`GameEngine` (espelhado em `UIScaleMixin` pras 8 classes `System`
separadas) — calcula `min(ui_scale, (tela_w-margin)/design_w,
(tela_h-margin)/design_h)` e guarda como override lido por `self._u()` (campo
`_u_scale_override`, `None` = usa `self._ui_scale` puro). Cada painel chama
isso UMA VEZ, no início do seu helper de geometria centralizado
(`_panel_origin()`/`_panel_rect()`/`_modal_rect()`, já existente em todos os
11 painéis modais — sorte de já estarem centralizados de uma rodada de
refactor anterior), com o tamanho-base (escala 1.0) daquele painel
específico — draw e click-handler que chamam o mesmo helper automaticamente
ficam consistentes entre si, sem duplicar a conta. Painéis sem helper
centralizado (modal de quantidade da Loja, qty_modal) tiveram a chamada
duplicada nos 3 pontos que computam geometria independentemente (mouse down/
motion/render), mesmo padrão que já existia ali pra `mw`/`mh`.

Caso especial — **hotbar + barra de consumíveis**: ambas compartilham as
mesmas propriedades `_HB_W`/`_HB_H`/`_HB_PAD` (lado a lado na mesma linha
visual), então não podiam clampar cada uma pro seu próprio tamanho
independentemente (resultaria em slots de tamanho diferente entre as duas
barras). Novo `_set_hotbar_row_scale()` em `client/hotbar_handlers.py` calcula
o clamp pela LARGURA COMBINADA das duas barras na capacidade MÁXIMA (10
slots + 5 slots, não só os ocupados — mais conservador, mas garante que as
duas barras sempre clampam pro mesmo valor), chamado nos 4 pontos que leem
essas properties (`_draw_hotbar`, `_handle_hotbar_click`,
`_handle_consumable_bar_click`, `_draw_consumable_bar`) + na detecção de
drop do painel de Habilidades sobre a hotbar.

`map_overlay.py` **não precisou de fix** — seu modal já é dimensionado como
`70% da tela` diretamente (`W_RATIO`/`H_RATIO`), nunca depende de
`ui_scale` pro tamanho externo, só pro chrome interno (botão fechar, bordas).

**Não corrigido (risco residual, menor, documentado):** `client/hud_handlers.py`
(barras de HP/mana/fúria, sempre visíveis) não tem o mesmo clamp — risco
teórico de elementos colidirem com o minimapa em `ui_scale` muito alto numa
janela pequena, mas nunca reportado e a barra de HUD é proporcionalmente
menor que os modais que já quebraram, então fica pra uma rodada futura se
algum problema concreto aparecer.

**Status:** ✅ resolvido — mesma validação (compile limpo, suíte sem
regressão, smoke test cliente real com `ui_scale=1.25`, o valor exato que
expôs o bug). Mesma ressalva de antes: sem validação visual manual em janela
real nos extremos de escala — pendente de passada do usuário.

**Segunda rodada de feedback do usuário (mesmo dia, via screenshots do
Inventário)** — três problemas distintos, todos corrigidos:

1. **Texto sobrepondo texto**: título "Equipamentos" (`font_md`) e o cabeçalho
   "Equipado (clique p/ desequipar)" (`font_sm`) abaixo dele usavam offsets Y
   fixos (`y0+4` e `y0+PAD+2`) que nunca levavam em conta a altura REAL do
   título renderizado — com `font_md` maior que o espaço reservado, o
   cabeçalho ficava por baixo do título. Fix: `header_y` agora deriva de
   `title_y + title.get_height() + margem` (altura real do título
   renderizado, não um offset fixo adivinhado) — `client/inventory_handlers.py`.
2. **Dicas redundantes removidas**: "(clique p/ desequipar)" e "clique esq.
   p/ selecionar | dir. p/ equipar" — informação já repetida no tooltip de
   cada item ao passar o mouse (`item_tooltip_lines`), sem motivo pra estar
   fixa no cabeçalho. Mesmo princípio aplicado à linha de HUD permanente
   "Mochila: X/Y [I] itens [T] talentos [ESC] menu [clic direito...]"
   (`client/hud_handlers.py`) — cortada pra só "Mochila: X/Y" (estado real,
   não tutorial); essa linha era a mais larga de todo o HUD e o motivo de
   precisar de uma reserva de segurança tão grande no item 3.
3. **Glifo quebrado**: `"── Estatísticas ──"` usava o caractere Unicode de
   desenho de caixa (BOX DRAWINGS LIGHT HORIZONTAL, não um hífen comum) — a
   fonte pixelada do jogo não tem esse glifo, renderiza como retângulo vazio
   (tofu). Confirmado via grep que era a ÚNICA ocorrência desse caractere em
   código que efetivamente é renderizado (as outras ~50 ocorrências em todo
   o projeto são separadores `# ──` dentro de comentário, nunca chegam à
   tela) — trocado por `"-- Estatísticas --"` (hífen ASCII comum).
4. **Moedas**: estavam duplicadas (HUD já mostra o ouro do jogador
   permanentemente) e no rodapé do painel, espremidas perto da hotbar
   sempre visível por baixo do modal. Removida a duplicata do rodapé,
   ícone+valor agora aparece ao lado do título "Equipamentos", no topo.
5. **Modal preciso de respeitar HUD/minimapa, não só a tela**: o
   `_set_panel_scale` da rodada anterior garantia que o painel cabia na
   TELA, mas centralizava ignorando que HUD (canto superior esquerdo) e
   minimapa (canto superior direito) já ocupam pixels reais — em qualquer
   painel alto o suficiente (a maioria — todos passam de ~400px de altura
   numa tela de 720px), o topo do painel cai bem na faixa vertical onde
   HUD/minimapa vivem, então a centralização "pura" os encavalava sempre que
   o painel era largo o bastante (Inventário 720px, Loja 1120px...).
   Confirmado pelas setas do usuário nos screenshots apontando exatamente
   pra esses dois cantos.

**Fix:** novo `self._safe_panel_origin(design_w, design_h, margin=20)` em
`GameEngine` (espelhado em `UIScaleMixin`) — mesmo contrato de
`_set_panel_scale`, mas centraliza o painel na ÁREA LIVRE da tela
(excluindo `_HUD_SAFE_W=360` à esquerda e `_MINIMAP_SAFE_W=240` à direita,
não a tela inteira) e já chama `_set_panel_scale` internamente com essas
reservas somadas ao `margin`. `_HUD_SAFE_W=360` é estimativa (cobre a linha
de atributos "FOR:.. INT:.. AGI:.. VIT:.. DEF:..", a mais larga do HUD,
folgada) — não é medido dinamicamente a partir do texto renderizado de
verdade; se algum dia o HUD ganhar uma linha ainda mais larga, terá que
ajustar essa constante de novo. `_MINIMAP_SAFE_W=240` é mais preciso, vem de
`Minimap.SIZE(220) + MARGIN_RIGHT(10)` + folga.

Trocado em TODOS os `_panel_origin()`/`_panel_rect()` centralizados
(`client/inventory_handlers.py`, `client/debug_handlers.py`,
`client/habilidades_handlers.py`, `client/hotbar_editor_handlers.py`,
`crafting_system.py`, `trainer_system.py`, `systems.py::ShopSystem`,
`quest_system.py::QuestDialogSystem` e `QuestJournalSystem`,
`talent_system.py`) — todos ganham a reserva automaticamente, sem precisar
de mudança extra em cada um (só trocar a chamada de centralização "pura"
pela nova). Efeito colateral esperado: painéis muito largos (Loja, 1120px)
agora renderizam visivelmente menores que 100% mesmo em `ui_scale=1.0` numa
resolução 1280×720 — correto, já invadiam a zona do HUD/minimapa antes
mesmo dessa rodada de fix, só não tinha sido reportado ainda.

**Fora de escopo, deliberado:**
- `client/menu_handlers.py` (menu de pausa e submenus) — painéis pequenos
  (260-400px), nunca reportados com esse problema; não vale o risco de tocar
  sem evidência concreta.
- `systems.py::LootSystem` — não é um modal centralizado, é ancorado perto
  do cadáver no mundo (já tem seu próprio clamp de posição pra não passar
  da borda da tela); estender pra também evitar HUD/minimapa é uma melhoria
  futura, não o bug reportado.
- `client/hud_handlers.py` (barras sempre visíveis) — mesma ressalva já
  registrada antes; risco residual menor.

**Status (revisado — ver regressão abaixo, encontrada na mesma rodada):**
compile limpo em todos os arquivos, suíte sem regressão (mesma baseline),
smoke test cliente real conectando e rodando sem exceção. **Não validado**:
aparência visual real dos 3 problemas relatados (texto, glifo, posição) numa
janela aberta de verdade — pendente

**Regressão CRÍTICA encontrada pelo usuário (mesmo dia, screenshot
seguinte): painel colapsava pra um amontoado de texto sem caixa visível
nenhuma.** Causa raiz: `_safe_panel_origin` passava a reserva de
HUD+minimapa (`_HUD_SAFE_W(360) + _MINIMAP_SAFE_W(240) + margin(20) = 620`,
escalada por `ui_scale` → 775 com `ui_scale=1.25`) como o parâmetro `margin`
**único** de `_set_panel_scale` — que usa o MESMO valor pra largura E
altura. Essa reserva é puramente HORIZONTAL (HUD/minimapa só ocupam as
laterais), mas `_set_panel_scale` aplicava os mesmos 775px também no cálculo
de altura: `(720 - 775) / design_h` → **negativo**, numa tela 1280×720
(altura 720 < reserva 775). Esse valor negativo vencia o `min()` e se
tornava o `_u_scale_override`. Como `_u()` faz `max(1, round(px *
override))`, COM override negativo todo `round(px * negativo)` vira
negativo e o `max(1, ...)` arredonda pra exatamente **1 em toda e qualquer
chamada de `self._u()`** — toda a geometria do painel (largura, altura,
padding, offset de cada elemento) colapsava simultaneamente pra 1px,
resultando num retângulo de fundo invisível com todo o texto desenhado
empilhado quase no mesmo pixel — exatamente o "amontoado de texto sem
modal" do screenshot.

**Fix (duplo, defesa em profundidade):**
1. `_set_panel_scale` ganhou um parâmetro `margin_h` separado de `margin`
   (largura ≠ altura) — `_safe_panel_origin` agora passa a reserva
   HUD+minimapa só pra `margin` (largura) e a margem genérica pequena (20px)
   pra `margin_h` (altura), nunca mais confundindo os dois eixos.
2. Piso de segurança `max(0.15, min(...))` em `_set_panel_scale` — mesmo que
   uma combinação futura de margens ainda gere um valor degenerado por
   algum motivo não previsto, a escala nunca mais cai abaixo de 15% do
   tamanho de design (pequeno mas visível e proporcional), nunca mais
   colapsa pra 1px cego. Esse piso deveria ter existido desde a primeira
   versão de `_set_panel_scale` — qualquer fórmula de clamp precisa de um
   piso, não só um teto.

Validado com simulação direta da matemática (sem depender de abrir a janela
real) pro cenário exato do bug (tela 1280×720, `ui_scale=1.25`, painel de
Inventário 720×660): override antes = negativo → depois = `0.701` (positivo,
sensato). Testado também Loja (1120×700, o maior painel) e os demais — todos
com escala positiva e painel inteiro dentro da tela. Suíte sem regressão,
compile limpo, smoke test cliente real sem exceção.

**Lição registrada**: qualquer função de clamp/min() que combina múltiplos
fatores (aqui: escala do usuário × 2 razões de tela) precisa validar TODOS
os ramos contra um piso sensato — um `min()` sem piso deixa qualquer ramo
que vire negativo "vencer" silenciosamente, sem erro nem warning, só
degradando visualmente. Não foi pego no smoke test anterior porque o smoke
test só confirma "conecta e não lança exceção" — nunca abriu o painel de
inventário de fato (sem automação de GUI), então o colapso visual passou
batido até o usuário testar manualmente.

---

**Terceira rodada de feedback do usuário (mesmo dia)** — dois pedidos:
(1) tooltips sem largura máxima/quebra de linha (screenshot: tooltip de
"Tiro Repulsivo" esticado quase a tela inteira numa linha só), e (2) "um
lugar onde fosse possível centralizar a configuração desses tamanhos" —
tooltips, modais, fontes — e que o controle de escala existente (não vários
sliders por categoria — usuário confirmou via pergunta de escopo) afetasse
TODOS os elementos de UI, incluindo coisas ainda não tocadas nas rodadas
anteriores: minimapa, action bar, barras de HP/mana/raiva/concentração.

**1. Tooltip sem quebra de linha — causa raiz:** `_draw_tooltip()`
(`client/tooltip_handlers.py`, compartilhado por tooltip de skill/item/
mundo) nunca teve limite de largura — cada `line` em `lines` virava UMA
superfície renderizada, sem wrap, e a caixa (`tw`) sempre crescia pra caber
a linha mais larga. O gatilho concreto: `_skill_tooltip_lines()`
(`client/tooltip_handlers.py:137-138`) usa `skill.description` cru como
fallback pra qualquer skill sem dispatch customizado (ex: Tiro Repulsivo) —
uma frase inteira numa linha só, sem ponto de quebra.
**Fix:** novo `wrap_text(text, font, max_px)` em `ui_helpers.py` (helper
compartilhado — greedy word-wrap, mesma lógica que já existia isolada em
`quest_system.py::QuestDialogSystem._wrap`, não duplicada de novo).
`_draw_tooltip` agora calcula `MAX_W = self._u(UI.TOOLTIP_MAX_W)` (420px
base) e, pra cada linha de uma coluna que excede isso, quebra em
sub-linhas antes de renderizar — linhas de duas colunas (label : valor)
não quebram, por enquanto (raras de serem longas o suficiente pra importar).

**2. Centralização — novo `ui_sizes.py`:** único módulo com TODOS os
tamanhos de design (escala 1.0) da UI — painéis modais (Inventário, Debug,
Habilidades, Hotbar Editor, Crafting, Trainer, Loja + modal de quantidade,
Loot, diálogo/diário de Quest, Talentos, menu de pausa + 4 submenus),
geometria interna de cada um (slots, padding, colunas...), HUD (largura/
altura das barras), hotbar+barra de consumíveis (tamanho de slot/ícone/
padding), minimapa (tamanho/raio/margens), tooltip (padding/largura máxima/
gap), reservas de área segura (HUD/minimapa) e bases de fonte. Cada arquivo
de painel manteve sua constante local de mesmo nome (`_PANEL_W`, `PANEL_W`
etc. — sem tocar nos ~400 usos de `self._u(_PANEL_W)` espalhados), só
redirecionada pra `ui_sizes.py` (`_PANEL_W = UI.INVENTORY_W`) — editar um
valor lá afeta o painel automaticamente, sem caçar o arquivo certo.
Efeito colateral encontrado e corrigido de passagem: `QuestSystem.
HUD_MARGIN_TOP` (tracker de quest no HUD) era um número fixo (196) com
comentário "abaixo do minimapa: ...+SIZE(120)+gap" — só que o minimapa já
tinha SIZE=220, não 120, há tempo (drift entre arquivos que a
centralização deveria justamente evitar). Corrigido pra ser derivado de
`UI.MINIMAP_MARGIN_TOP + UI.MINIMAP_SIZE + 6` — o tracker de quests deixa
de sobrepor o minimapa.

**3. Minimapa nunca reagia à "Escala da UI" — não tinha sido tocado em
nenhuma rodada anterior.** `Minimap` era uma classe simples sem
`UIScaleMixin`; `SIZE`/`MARGIN_RIGHT`/`MARGIN_TOP` eram atributos de classe
fixos, e a superfície pré-renderizada cacheada (`_rebuild_numpy`/
`_rebuild_python`) sempre construía o frame em `self.SIZE` (220) cru.
**Fix:** `Minimap(UIScaleMixin)` + override de `set_ui_scale()` que
recalcula `_tile_px`/`_content_size`/`_map_ox`/`_map_oy` a partir do
`self._u(SIZE)` escalado e invalida o cache (`_cached_surf = None`) sempre
que a escala muda — sem isso, mudar a escala não rebuildaria a superfície
cacheada e o minimapa continuaria do tamanho antigo. `render()`/
`get_rect()`/o frame dos dois métodos de rebuild atualizados pra usar a
versão escalada. `GameEngine` empurra `self._ui_scale` pro minimapa na
construção e em `_set_ui_scale()`, mesmo padrão dos outros 9 sistemas.
Validado isoladamente (sem abrir janela completa): escala 1.0→220px,
1.25→275px, sem valores negativos/degenerados, rect sempre dentro da tela.

**Status:** ✅ resolvido — compile limpo em todos os ~19 arquivos tocados
nesta rodada, suíte sem regressão (mesma baseline), smoke test cliente real
sem exceção. **Não validado**: aparência visual real da quebra de linha do
tooltip e do minimapa escalado numa janela aberta de verdade — pendente de
nova passada do usuário, como nas rodadas anteriores.

---

**Quarta rodada (mesmo dia)** — usuário comentou (sem código, manual) os
valores de `ui_sizes.py` (encolheu `INVENTORY_W/H` 720×660→660×540 e ajustou
`INVENTORY_EQ_W`/`INVENTORY_BODY_H`) e pediu (1) comentário explicando cada
constante do arquivo e (2) como mudar a POSIÇÃO de um painel (hoje só dá pra
mudar tamanho ali). Documentado inline no próprio `ui_sizes.py` — todo
painel é centralizado (tela inteira ou área livre via
`_safe_panel_origin`), sem conceito de offset; mudar posição = editar
`_panel_origin()`/`_panel_rect()`/`_modal_rect()` do painel em questão.
Ofereci `OFFSET_X/OFFSET_Y` configurável em `ui_sizes.py` como opção, não
implementado ainda (esperando decisão do usuário).

**Bug real encontrado no screenshot seguinte**: linhas do HUD principal
(`client/hud_handlers.py`) se sobrepondo entre si — não era só "painel perto
do HUD", era o HUD se sobrepondo a ele mesmo. Causa: incrementos verticais
fixos pequenos (`y += self._u(14)` / `self._u(16)`) depois de texto
`font_sm` (~22-28px de altura renderizada) nas linhas de Nível/XP,
atributos brutos (FOR/INT/AGI/VIT/DEF) e ratings (Crit/Aparo/Esquiva), ouro
e mochila — inconsistente com o padrão JÁ correto usado na linha do nome do
personagem (`y += name_surf.get_height() + self._u(2)`). **Fix**: todas as
linhas agora usam `surf.get_height() + self._u(2)`, igual à linha do nome —
nunca mais desalinha independente do tamanho de fonte/escala.

**Pedido relacionado**: esconder texto "tipo debug" do HUD permanente,
ligando só quando precisar verificar item/efeito/talento batendo nos stats.
As duas linhas mais redundantes com a aba Estatísticas do Inventário
(atributos brutos e ratings de combate) agora ficam atrás de
`self._hud_show_debug_stats` (default `False`) — novo toggle na aba "Nivel"
do painel de debug (F12), abaixo dos botões de level-up. HP/mana/raiva/
concentração/XP/ouro/mochila/talentos pendentes continuam sempre visíveis
(informação de jogo, não debug).

**Status:** ✅ resolvido — compile limpo, suíte sem regressão, smoke test
sem exceção. **Não validado**: aparência visual real do HUD sem overlap e
do toggle funcionando numa janela aberta — pendente de nova passada do
usuário.

---

**Quinta rodada (mesmo dia)**: usuário confirmou que quer o
`OFFSET_X/OFFSET_Y` configurável (pergunta anterior). Implementado pra
TODOS os 11 painéis centralizados + 5 submenus do menu de pausa (16 no
total): `ui_sizes.py` ganhou uma seção "Offsets de posição" com um par
`<PAINEL>_OFFSET_X`/`<PAINEL>_OFFSET_Y` por painel, default `(0, 0)` =
comportamento de sempre (centralizado, sem desvio). Cada
`_panel_origin()`/`_panel_rect()`/`_modal_rect()` (já o ponto único de
verdade reusado por draw+click desde a rodada do clamp de escala) soma o
offset do seu painel ao resultado de `_safe_panel_origin()`. Pro menu de
pausa, que usa `_mm_panel(pw, ph)` compartilhado entre as 5 submenus (não
tinha `_safe_panel_origin` — fora de escopo desde a 3ª rodada, painéis
pequenos), o helper ganhou um parâmetro `offset=(0,0)` e cada um dos 5
`_draw_*_submenu` passa o seu.

**Decisão de design**: offsets são pixels de TELA, não passam por
`self._u()` — um offset de 0 não pode usar `_u()` porque
`_u()` tem piso `max(1, ...)` (pensado pra tamanhos, que nunca são zero),
e isso faria `_u(0)` virar `1` — um desvio de 1px permanente em TODO painel
mesmo com offset "zerado", além de impedir offsets negativos de funcionar
(o piso também os trava em `+1`). Confirmado por simulação direta da
matemática (offset (-100, 80) → delta exato (-100, 80) na posição final,
sem o `_u()` no meio).

`LootSystem` (janela de loot) ficou de fora — não é centralizada, é
ancorada perto do corpo clicado; um offset ali teria semântica diferente
(deslocar relativo à âncora, não ao centro) e não foi pedido.

**Status:** ✅ resolvido — compile limpo em todos os arquivos, suíte sem
regressão, smoke test cliente real sem exceção, matemática do offset
validada por simulação isolada (sem depender de janela real). **Não
validado**: editar um offset e confirmar visualmente que o painel certo se
move na direção esperada — pendente de passada do usuário.

---

**Sexta rodada (mesmo dia) — bug real de gameplay, não de UI**: usuário
logado de Arqueiro viu "Mana: 165/165" na aba Estatísticas do Inventário —
Arqueiro não tem Mana, tem Concentração. Causa raiz em
`stats_system.py::apply_char_stats_to_combat` — `new_max_mana = 150 +
total_int*15` era calculado SEM checar `class_id`, então QUALQUER classe
com INT>0 ganhava `max_mana>0` (Arqueiro com INT:1 → 150+15=165, batendo
exato com o valor reportado). `client/inventory_handlers.py` escolhia qual
recurso mostrar checando `max_mana > 0` primeiro — sempre "vencia" errado
pra Arqueiro/Guerreiro com INT>0. **Fix**: `new_max_mana` agora só é
calculado pra `class_id == "mago"` (0 pras outras classes), mesmo padrão já
usado em `CLASS_CONCENTRATION.get(class_id, 0)` algumas linhas abaixo no
mesmo arquivo. `client/inventory_handlers.py` trocado pra checar
`class_id` diretamente (igual ao HUD, que já fazia certo) em vez de
`max_X > 0` — mais robusto, não depende de nenhum outro campo ficar
exatamente zerado.
Validado: `apply_char_stats_to_combat` chamado isoladamente pra Arqueiro
(INT=1) → `max_mana=0, max_concentration=100` (antes: `max_mana=165`); Mago
(INT=1) → `max_mana=165, max_concentration=0`, sem regressão. Suíte sem
regressão, smoke test sem exceção.

**Pedido relacionado, NÃO implementado ainda — precisa de decisão**:
usuário quer a aba Estatísticas mostrar "Base" e "Itens" separados pra cada
stat (HP, Atq.Físico, Atq.Mágico, Armadura, Estamina, recurso de classe,
Acerto, Esquiva, Aparo, Vel.Ataque, Crítico), não só o total combinado.
Investigado: `CombatStats` já guarda `base_X` (sem equipamento) e o valor
final em `self.X` (`base_X` + todos os `self.modifiers` aplicados em
`_recalculate_effective_stats()`) — ENTÃO "Itens" pareceria trivial (`X -
base_X`). Mas `self.modifiers` é uma lista ÚNICA sem campo de origem
(`Modifier` só tem `attribute`/`value`/`type`) — usada tanto por
equipamento (`add_modifier` no equipar) quanto por talentos
(`apply_talent_effects`) quanto por buffs temporários (`timed_modifiers`).
Não tem como hoje filtrar "só os modificadores de equipamento" — `X -
base_X` incluiria talentos e buffs ativos junto, não só itens. Implementar
de verdade exigiria adicionar um campo de origem em `Modifier` e atualizar
todo lugar que cria modificador (equip/unequip, talentos, possivelmente
efeitos de skill no servidor) — escopo bem maior que o resto desta sessão
(toca em estado de gameplay compartilhado client/server, não só render).
Perguntei ao usuário antes de iniciar esse pedaço — escolheu a opção
"implementar certo" (marcar origem do modificador), não a versão
aproximada.

**Implementado**: `Modifier.__init__` ganhou `source: str = "equipment"`
(`components.py`) — default "equipment" cobre os ~140 `Modifier(...)` em
`loot_tables.py`/`crafting_data.py`/`merchant_data.py` (todo modifier de
item) e os 3 pontos de deserialização (`save_system.py`,
`client/save_sync_handlers.py`, `server/world_server.py` ao reconstruir
`Item.modifiers` de save/rede) SEM precisar tocar em nenhum desses ~140+
locais — só os modificadores que NÃO são de item precisam declarar
`source` explícito. Catalogados e corrigidos os 9 pontos não-equipamento:
`source="talent"` em `talent_system.py` e `server/world_server.py`
(`_apply_talent_effects`); `source="buff"` em `spell_system.py`
(Lapso Elemental), `skill_handlers.py` (Canção da Inspiração, Só um Gole),
`systems.py` (proc de item — é item, mas o EFEITO é temporário, então
contabiliza como buff, não como bônus permanente de equipamento),
`client/network_handlers.py` (réplica visual do Lapso Elemental) e
`server/spell_completion_processor.py` (3 pontos: Lapso Elemental, Canção
da Inspiração, Só um Gole, versões server-side). `source` não entra em
`Modifier.__eq__`/`__hash__` — `remove_modifier()` continua funcionando
igual, sem risco de regressão na remoção de modificadores ao desequipar.

Novo `CombatStats.equipment_bonus(attribute) -> float` soma só os
modificadores `source=="equipment"` de um atributo (confirmado: todo
modifier de equipamento é "flat", nunca "percentage" — soma direta é
exata, sem precisar simular ordem de aplicação).

`client/inventory_handlers.py` — aba Estatísticas redesenhada com 3
colunas por stat (Label | Base | Itens): "Base" = `total -
equipment_bonus(attr)` (atributo cru + talentos + buffs, tudo que não é
item); "Itens" = `equipment_bonus(attr)` com sinal ("+15", "-0.30s" pra
itens que aceleram ataque, ou "—" se o equipamento não contribui pra
aquele atributo — sempre o caso de Mana/Concentração/Raiva, que
equipamento nunca modifica no sistema atual). Conversões por linha
respeitadas (HP/Estamina usam o fator ×10 de stamina→HP; Esquiva/Aparo
usam /20; Crítico/Acerto em %). Linhas de recurso de classe (HP,
Mana/Conc/Raiva) deixaram de mostrar "atual/máximo" nessa seção
especificamente — current já é visível na barra do HUD; a seção agora é
sobre comparar Base vs Itens do MÁXIMO, não rastrear consumo atual.

Validado: `equipment_bonus()` testado isoladamente com modificador de
talento misturado no mesmo `CombatStats` (talento de +100 armor não
contaminou o cálculo de "Itens", só o item de verdade contou) e também
ponta-a-ponta com um item real de `loot_tables.py` (Espada de Ferro,
+3 attack_power: base=5, itens=+3, total=8 — bate exato). Compile limpo em
todos os ~10 arquivos tocados, suíte sem regressão (mesma baseline), smoke
test cliente real sem exceção.

**Não validado**: aparência visual real das 3 colunas na aba Estatísticas
numa janela aberta — espaçamento das colunas foi calculado pra não
sobrepor com `INVENTORY_W=660` (valor atual do usuário em `ui_sizes.py`),
mas sem rodar de verdade não dá pra garantir 100%; pendente de passada do
usuário. Se algum rótulo ficar apertado, o ajuste é só nos offsets `cLb`/
`cLi`/`cRb`/`cRi` dentro de `_draw_inventory_panel`.

---

**Sétima rodada (sessão seguinte) — CRASH real reportado pelo usuário**:
Guerreiro morreu, esperou o timer de ressurreição no cemitério, jogo
crashou com `AttributeError: 'CombatStats' object has no attribute 'mana'`
em `client/network_handlers.py::_handle_msg_player_revive`.

**Causa raiz**: `CombatStats.mana` nunca foi declarado em
`CombatStats.__init__` — é um campo "espelho" de `CharacterStats.mana`
criado DINAMICAMENTE (só passa a existir na primeira vez que algum dos ~4
pontos de sync roda: cast de magia com custo de mana, regen de mana
tick, ou uma mensagem STATS_UPDATE do servidor trazendo mana). Nenhum
desses pontos roda pra Guerreiro/Arqueiro (não usam mana — e a regen de
mana já não roda pra eles desde a correção do bug de Mana/Concentração na
rodada anterior, que zerou `max_mana` pra essas classes). A linha
`cs.mana = payload.get("mana", cs.mana)` no handler de revive LÊ
`cs.mana` como valor padrão do `.get()` — Python avalia esse argumento
antes de chamar `.get()`, então se o atributo nunca foi criado, o crash
acontece sempre, incondicionalmente, pra qualquer classe que nunca lançou
magia. Não é regressão desta sessão — é um bug dormente, só nunca
exercitado antes (provável: ninguém tinha testado revive no cemitério com
uma classe sem mana).

**Fix (duas camadas, mesmo princípio do piso de `_set_panel_scale` — não
deixar uma leitura de atributo "opcional" virar crash):**
1. `CombatStats.__init__` agora declara `self.mana: int = 0` — o campo
   sempre existe desde a criação do componente, não depende de nenhum
   código externo ter rodado primeiro. Elimina a classe inteira de
   "`AttributeError` em `cs.mana`", não só este ponto específico.
2. `_handle_msg_player_revive` restaurado pra sincronizar `cs.mana`
   também (mantendo o padrão "espelho" já estabelecido em
   `world_server.py`/`network_handlers.py`/`skill_handlers.py`/
   `spell_system.py`), agora seguro porque o atributo sempre existe.

Validado: simulei o cenário exato (CombatStats novo, sem nenhum cast de
magia, processando um payload de revive) — antes do fix, `AttributeError`
imediato; depois, `cs.mana=0` e revive completo sem erro. Compile limpo,
suíte sem regressão (mesma baseline), smoke test cliente real sem
exceção.

**Não validado**: revive de verdade de um Guerreiro morto no cemitério
numa janela aberta (só a simulação isolada + smoke test genérico foram
feitos) — pendente de nova passada de teste do usuário.

---

## 10. SEGURANÇA — Auditoria de Validação Server-Side (25 de junho de 2026)

Investigação disparada por uma lista de "vulnerabilidades reivindicadas" num
doc de arquitetura antigo que nunca tinha sido verificada contra o código
real. 4 sub-agentes investigaram em paralelo: (1) range/LOS de skills, (2)
walkability/colisão, (3) autoridade de HP/gold, (4) validação de proc de
item. Duas claims do agente (1) — Pirofagia e Tiro Múltiplo sem checagem de
range no servidor — foram **desconfirmadas** por leitura direta do código
(cone de Pirofagia é geometricamente limitado a 4 tiles desde a posição
autoritativa do caster; `_server_tiro_multiplo` já valida `dist > range_t`).
A investigação revelou um problema real e mais severo, não citado no doc
original: `_resolve_target()` (Tier E abaixo). As demais claims (2, 3, 4)
eram reais. Ordem de execução escolhida pelo usuário: B → C → E → D → A.

Regra geral confirmada nesta auditoria: "servidor valida TUDO" (regra do
projeto) tinha 5 furos reais — itens de equipamento forjados, orçamento de
talento sem teto, alvo de skill sem checagem de range, proc de item só
existindo no cliente, e HP/gold do cliente aceitos sem teto. Os 5 foram
corrigidos nesta sessão.

### 🔴 CRÍTICO Tier B — `_reconstruct_item` reconstituía item do cliente sem validar contra catálogo

**Causa raiz**: `server/world_server.py::_reconstruct_item(d)` reconstituía
um `Item` a partir do dict que o CLIENTE manda (sync de inventário/
equipamento) usando os campos do próprio dict — incluindo `modifiers`,
`attack_power`, `armor`, `spell_power`, etc. Um cliente malicioso podia
mandar `{"name": "Espada de Ferro", "attack_power": 99999, ...}` e o
servidor aceitava o valor direto, sem checar se bate com o item real do
catálogo (loja, loot, forja).

**Fix**: `_reconstruct_item` agora tenta casar o item por nome contra 3
catálogos autoritativos, nesta ordem — `loot_tables._T` (itens de drop),
`merchant_data.SHOPS` (estoque de loja), `crafting_data.RECIPES[*]
["result_factory"]` (itens forjáveis). Se casar, reconstrói a partir da
FACTORY do catálogo (stats reais) e só aplica do dict do cliente os campos
de bookkeeping inofensivos (`arrow_count`, `max_arrows`, `subtype`, `stack`,
`max_stack`). Se não casar com nenhum catálogo (item não existe / nome
inventado), cai num fallback stat-stripped — só campos básicos
(`name`/`item_type`/`slot`/`rarity`/`value`/`consumable`/`max_stack`), zero
`modifiers` e zero stats de combate.

Validado: item de loja forjado (+99999 attack_power) resolve pro modificador
real do catálogo (+3); item de forja forjado (+88888) resolve pros stats
reais da receita (+6 attack_power, +0.03 crit_rating); nome totalmente
inventado cai no fallback sem `modifiers` e sem `attack_power`. Compile
limpo, suíte sem regressão (32 failed/49 passed/1 skipped — baseline
pré-existente, confirmada inalterada em todos os tiers desta auditoria).

### 🔴 CRÍTICO Tier C — Orçamento de pontos de talento sem teto no servidor

**Causa raiz**: `apply_talent_effects_to_player` aplicava
`eff["value"] * points` direto do payload do cliente (`TALENT_UPDATE` ou
`SAVE_STATE`), sem checar se `points` cabia no orçamento real do jogador
(`TalentTree.available_points`). Cliente malicioso podia mandar
`{"allocated": {"qualquer_talento": 999}}` e ganhar o efeito de 999 pontos
nesse talento.

**Fix**: `WorldServer.validate_talent_allocation(session_id, claimed)` —
recalcula o orçamento real (`available_points` atual + soma do que já está
alocado, que só cresce via `process_levelups()` no servidor) e clampa cada
talento ao seu `max_points`; se a soma reivindicada excede o orçamento real,
rejeita a alocação inteira (retorna `None`, não aplica nada). Chamado em
`_handle_talent_update` e no topo de `_handle_save_state`, ANTES de cachear/
persistir — o resultado validado substitui o payload crú em
`session.last_client_payload`. Defesa em profundidade: `apply_talent_effects_
to_player` ganhou um clamp redundante por talento (`min(points,
t.get("max_points", points))`) mesmo que o validador seja bypassado por
algum caminho futuro.

Validado: 999 pontos num talento clampam pro `max_points` desse talento E
só são aceitos se dentro do orçamento; 3 talentos de 1 ponto cada quando o
orçamento real é 2 são rejeitados por completo (retorna `None`, `TalentTree`
inalterada); alocação exatamente dentro do orçamento aplica e atualiza
`available_points` corretamente. Compile limpo, suíte sem regressão.

### 🔴 CRÍTICO Tier E — `_resolve_target` não validava range do alvo já selecionado

**Causa raiz**: `systems.py::_resolve_target(combat_state, tile_move,
_max_range)` — usado por ~12 skill handlers — só checava `_max_range` no
fallback de auto-seleção (quando não havia alvo ainda). Se já havia um
`target_entity_id` setado, a função retornava esse alvo DIRETO, sem nenhuma
checagem de distância. E `server/skill_processor.py` seta
`combat_state.target_entity_id = tid` direto do campo `tid` que o CLIENTE
manda em `CAST_SKILL`, também sem checar distância. Resultado: qualquer
skill com alvo (melee, Bola de Fogo, etc.) podia acertar QUALQUER entidade
do mapa, bastando o cliente mandar o `tid` dela — não só skills de cone,
como a investigação inicial sugeria.

**Fix**: `_resolve_target` agora valida `_max_range` também pro alvo JÁ
selecionado (`chebyshev` entre a posição do caster e do alvo), antes de
aceitar; só cai pro fallback de auto-seleção se o alvo atual estiver fora de
range ou inválido.

Validado: alvo a 40 tiles com `_max_range=30` (range de Bola de Fogo) é
rejeitado (retorna -1); alvo a 1 tile com o mesmo range resolve
normalmente; alvo a 40 tiles com `_max_range=1` (melee) também é rejeitado.
Comparação A/B confirmou que as falhas pré-existentes da suíte (testes de
combate) são as MESMAS antes/depois do fix — não é regressão.

### 🔴 CRÍTICO Tier D — Proc de item só existia no cliente, servidor não tinha lógica nenhuma

**Causa raiz**: `CombatState._just_entered_combat` (setado por
`stat_fns.enter_combat()`) só era consumido em `systems.py::
CombatStateSystem.update()` — 100% cliente. `_trigger_procs()` rolava
`random.random() < chance` e aplicava o modificador LOCALMENTE, e quando o
proc afetava `max_hp`, notificava o servidor via `PLAYER_HP_SYNC` pra ele
"ficar sabendo" do HP resultante. Ou seja: o servidor não tinha ideia de
quais itens tinham proc, nem rolava nada — dependia 100% do cliente lhe
contar o resultado. Também faltava expiração de `timed_modifiers` no
servidor (`core_systems.ServerCombatStateSystem` nunca tinha essa lógica) —
um buff aplicado authoritativamente nunca expiraria nos stats reais.

**Fix**: `core_systems.ServerCombatStateSystem` ganhou `_roll_procs()` —
versão autoritativa de `_trigger_procs`, usa o `Equipment` do PRÓPRIO
servidor (já validado pelo Tier B) e roda no consumo de
`_just_entered_combat` dentro do `update()` do servidor. Ganhou também
`_tick_timed_modifiers()` — expira buffs/procs nos stats reais do servidor
(sem isso seriam permanentes ali). Resultado fica em `self.proc_events`
(consumido em `world_server.py` só pra log de auditoria — qualquer mudança
de `current_hp`/`max_hp` já é detectada e propagada pelo mecanismo existente
`_sync_player_hp_dirty()`, sem precisar de plumbing novo). O cliente
continua rolando sua própria versão LOCAL/cosmética (LOG + feedback
visual, `systems.py::_trigger_procs`) — pode divergir do resultado real do
servidor (chance independente), mas isso é só UX, nunca afeta stats de
verdade.

Validado: item com proc 100% de chance, ao entrar em combate via
`stat_fns.enter_combat()` simulado, gera o modificador correto
(`crit_rating` 0.003→0.103) e o expira corretamente após a duração (volta
a 0.003); proc que aumenta `max_hp` preserva o percentual de HP atual
corretamente através do salto (50% antes → 50% depois, mesmo com max_hp
10x maior). Compile limpo, suíte sem regressão, smoke test servidor+cliente
real sem exceção.

### 🔴 CRÍTICO Tier A — HP e gold do cliente aceitos sem teto em 3 pontos

Com o Tier D fechado (servidor já é autoritativo pra TODA fonte de mudança
de HP — dano, regen, cura de consumível, proc), `PLAYER_HP_SYNC` perdeu seu
único uso legítimo. Gold tinha 3 pontos de confiança cega, não só 1.

**Causa raiz / Fix por ponto:**
1. `_handle_player_hp_sync` (`server/session.py`) fazia `cs.max_hp = max_hp;
   cs.current_hp = min(hp, max_hp)` direto do payload do cliente, sem
   nenhum teto — `PLAYER_HP_SYNC` com `{"hp": 999999, "max_hp": 999999}`
   setava o HP real instantaneamente. **Fix**: handler agora é um no-op —
   ignora a mensagem por completo (mantida só pra clientes antigos não
   quebrarem ao enviá-la). Removida também a chamada client-side
   (`_send_proc_hp_sync`/`_on_proc_hp_change`, `game.py`/`save_sync_
   handlers.py`) — não há mais razão pro cliente mandar essa mensagem.
2. `_handle_gold_update` fazia `wall.gold = gold` direto do payload, só
   rejeitando `gold < 0` — `{"gold": 999999999}` setava o gold real. **Fix**:
   agora calcula o DELTA contra o gold real do servidor, rejeita deltas
   negativos (perda de gold não passa por aqui) e limita o incremento a
   500/mensagem (maior recompensa de missão hoje é 25 — `quests_data.py`) +
   teto absoluto de 1.000.000.
3. `_handle_save_state` tinha um segundo ponto de confiança cega:
   `_wall_ss.gold = max(_wall_ss.gold, int(_cli_gold_ss))` — qualquer
   SAVE_STATE com `stats.gold` inflado virava gold real e persistente.
   **Fix**: bloco removido inteiramente — gold nunca é lido do payload aqui.
4. `_build_save_merge` tinha uma contradição: docstring dizia "gold:
   SERVIDOR autoritativo" mas o código fazia `merged_stats["gold"] =
   _cli_gold if _cli_gold > 0 else _srv_gold` (confiava no cliente pra
   persistência) — e o mesmo padrão pro `max_hp`. **Fix**: ambos os campos
   agora vêm sempre de `dict(srv_stats)` (lido de `Wallet.gold`/
   `CombatStats.max_hp` reais, via `get_player_save_data`), sem olhar o
   payload do cliente.

**Achado novo, NÃO corrigido nesta sessão** — `quest_system.py` (cliente,
importa Pygame) calcula e aplica recompensa de missão (gold, XP, itens)
100% localmente; não existe nenhuma mensagem `QUEST_*` no protocolo e o
servidor não tem NENHUMA noção de missões. O cap do Tier A (item 2 acima)
é mitigação — limita o dano de um `GOLD_UPDATE` forjado a 500/mensagem —
mas não fecha a lacuna de fundo: o valor "correto" de uma recompensa de
missão nunca é validado contra nada no servidor, só limitado em magnitude.
Corrigir de verdade exigiria migrar validação de objetivo + concessão de
recompensa pro servidor (objetivo já tracked? checar `quest_events.py`) —
escopo comparável aos Tiers B/C desta sessão, não tentado aqui por ser uma
frente nova, não prevista na lista original de 5 tiers. Recomendado como
próximo item de segurança a investigar.

Validado (diagnóstico isolado, `server/session.py::SessionManager`
instanciado standalone): `GOLD_UPDATE` com delta legítimo de +25 aplica
exato; delta forjado de +999999874 é limitado a +500; delta negativo
(tentativa de "voltar" o gold) é ignorado, saldo inalterado;
`PLAYER_HP_SYNC` com hp/max_hp forjados não altera `CombatStats` em nada.
Compile limpo, suíte sem regressão (mesma baseline 32/49/1), smoke test
servidor+cliente real (conecta, login, disconnect, zero exceção no log).

**Não validado**: passada manual num cliente real abrindo um proc de item
verdadeiro em combate (visual/LOG) e confirmando que o resultado do
servidor (HP/stat real) bate com o que aparece na tela — só a simulação
isolada com item de teste forjado via diagnóstico foi feita.

### 🔴 CRÍTICO Tier F (novo, 25 de junho de 2026) — PLAYER_STAT_SYNC: servidor confiava em qualquer float do cliente para attack_power/crit/armor/etc.

Achado durante investigação do sistema de quests (não fazia parte do B/C/E/D/A
original). Mais severo que o de gold: virava o jogador deus permanentemente,
não só inflava um número.

**Causa raiz**: `_handle_player_stat_sync` → `WorldServer.sync_player_combat_stats`
aceitava qualquer valor float que o cliente mandasse pra `attack_power`,
`crit_rating`, `armor`, `spell_power`, `parry_rating`, `dodge_rating`,
`max_hp` (via `base_stamina`) e `attack_interval` (`COMBAT_SYNC_STATS`), sem
comparar contra nada — guardava num dict `_player_stat_overrides[eid]` e
reaplicava esse valor cru após QUALQUER recálculo (spawn, level up, troca de
talento). Existia porque, antes do Tier B, o servidor não tinha como saber o
bônus real de um item equipado — então "confiar no que o cliente calculou"
era o único jeito de equipamento ter algum efeito nos stats de combate
server-side. Depois do Tier B (equipamento validado contra catálogo), esse
atalho nunca foi removido — ficou sendo a ÚNICA fonte dos bônus de
equipamento, e zero validada.

**Fix — eliminar a causa, não só capar**: como o servidor já valida
equipamento (Tier B) e talentos (Tier C), ele tem tudo que precisa pra
calcular esses stats ele mesmo:
- `WorldServer._apply_equipment_modifiers(eid)` (novo) — para cada item
  REAL equipado, copia seus `Modifier`s (já validados contra catálogo) para
  `CombatStats.modifiers` com `source="equipment"`, substituindo só os
  modifiers de equipamento anteriores (preserva talento/buff) e recalcula.
  Chamado em `update_player_equipment` (toda troca de equipamento) e em
  `spawn_player` (login).
- `apply_talent_effects_to_player` — trocou `cs.modifiers.clear()` (limpava
  TUDO, inclusive equipamento, daí a necessidade do override pra "restaurar")
  por um clear seletivo (`source != "talent"`). Com isso os modifiers de
  equipamento nunca são perdidos durante recálculo de talento — eliminou a
  necessidade de "restaurar" qualquer coisa depois.
- Achado de bônus, mesma função: faltava uma fase de RESET dos `cs_flags`
  pro valor padrão antes de aplicar a alocação atual — sem isso, desalocar
  um talento deixava o cs_flag dele travado no último valor (só existia,
  duplicado, no código de spawn). Unificado: agora roda nos dois lados.
- `spawn_player` também tinha dois blocos de confiança cega já neutralizados
  na prática (`client_max_hp`/`client_ap`, sempre zerados antes da chamada em
  `_spawn_and_start`, portanto código morto) e um bloco de talentos que só
  restaurava `cs_flags`, nunca os `effects` de atributo (ex: parry_rating de
  talento ficava ausente até o próximo TALENT_UPDATE/SAVE_STATE) — ambos
  substituídos por uma única chamada a `_apply_talent_modifiers` (núcleo
  compartilhado, extraído de `apply_talent_effects_to_player`).
- `_handle_player_stat_sync` agora é no-op (mesmo tratamento do
  `PLAYER_HP_SYNC` do Tier A). `sync_player_combat_stats`/
  `_apply_stat_overrides`/`_player_stat_overrides` removidos por completo —
  não só o ponto de entrada, a estrutura de dados toda. Client-side:
  `_send_combat_stat_sync`/`_get_combat_stat_snapshot` e o polling em
  `game.py` removidos (não há mais nada do outro lado pra escutar).
- Bônus, mesma limpeza: o bloco de `_handle_save_state` que adotava
  `stats.max_hp`/`stats.current_hp` do cliente (cap de 10.000) também era
  vestígio do mesmo problema raiz (equipamento sem modelagem server-side) —
  removido junto, pelo mesmo motivo: max_hp agora é sempre derivado de
  Equipment/TalentTree reais, current_hp é 100% server-autoritativo desde o
  Tier D.

Validado (diagnóstico isolado com `tests/helpers.py`): item real (+3
attack_power) aplica corretamente; item forjado via EQUIP_SYNC com claim de
+99999 cai no fallback stat-stripped do Tier B (zero bônus); PLAYER_STAT_SYNC
forjado com `attack_power=999999` não altera nada (handler no-op); equipar um
item e alocar um talento ao mesmo tempo preserva os dois conjuntos de
modifiers corretamente isolados por `source`; desalocar o talento remove só
o modifier dele, o de equipamento sobrevive intacto. Compile limpo, suíte sem
regressão (mesma baseline 32/49/1), smoke test servidor+cliente real
(conecta, autentica, desconecta, zero exceção no log).

**Não validado**: passada manual num cliente real trocando de equipamento em
combate e confirmando visualmente que dano/crit batem com o item exibido —
só a simulação isolada via diagnóstico foi feita. Também não validado:
login de um personagem real salvo com talentos de atributo alocados,
confirmando que o bônus aparece imediatamente (antes ficava ausente até a
primeira ação que disparasse TALENT_UPDATE/SAVE_STATE).

### ✅ RESOLVIDO — Sistema de quests migrado pra server-autoritativo (era 100% client-side)

Retomado o item dimensionado em 25/06/2026 (entrada antiga preservada
abaixo da validação) — agora implementado por completo. Motivado por
relato real do usuário: a quest "Iniciação Arcana" (e na prática TODA
quest) reaparecia no NPC após reconectar, e a XP que ela concedeu era
revertida (level voltava ao anterior se o player tinha subido com aquela
XP) — porque a quest nunca existiu pro servidor; só vivia no `QuestLog`
local do cliente e no save-slot offline antigo (`save_system.py`), nunca
chegando ao banco. Mesma classe de bug já corrigida nesta sessão pra mana
e pro leash de mob: nada que afeta progressão persistente pode existir só
no cliente.

**Implementação** (mesmo molde da migração de `SkillLevels`, já validada
em produção — replicado ponto a ponto):
- **Lógica pura extraída** pra `quest_logic.py` (módulo novo, paralelo a
  `quests_data.py`/`quest_events.py`): `match_objective`, `apply_event`,
  `try_start`, `complete_quest`, `can_turn_in`, `sync_collect_progress`,
  `sync_learn_skill_progress`, `roll_conditional_loot`. Usada pelo cliente
  (caminho offline, via `quest_system.py`) E pelo servidor (caminho online,
  autoritativo) — zero duplicação de regra de match/progresso.
- **`quest_events.fire()`** ganhou `player_eid: int = -1` (default
  preserva todo chamador client-only existente); `QUEST_EVENTS` virou
  deque de 3-tuplas `(event_type, player_eid, data)`.
- **`QuestLog` agora existe no servidor**: criado em
  `WorldServer.spawn_player()` a partir de `quests_json` (nova coluna,
  schema + migração `ALTER TABLE` em `server/auth.py`, mesmo padrão de
  `skill_levels_json`); `get_player_save_data()` lê do componente VIVO do
  servidor (nunca do cliente); `server/session.py::_build_save_merge`
  trata `quests` como sempre-servidor.
- **`WorldServer._process_quest_events()`**, 1x por tick (mesmo lugar de
  `_sync_player_skill_levels_dirty`): drena `QUEST_EVENTS`, aplica
  progresso via `quest_logic.apply_event`, sincroniza sem evento dedicado
  `collect_item` (contra `Inventory`) e `learn_skill` (contra
  `PlayerSkills.learned_skill_ids` — evita depender da validação de
  aprender skill em si, que é um gap pré-existente separado, ver nota
  abaixo). Dirty-check → push privado `QUEST_UPDATE` (nunca AOI).
- **6 gatilhos server-side** adicionados nos pontos já autoritativos
  identificados na investigação original: `kill` →
  `server/server_death_handler.py` (era listado como "Intencional NÃO
  fazer" no docstring — removido); `use_skill` →
  `spell_completion_processor.py::_process_spell_cast_completions` (cast)
  + `server/skill_processor.py` (instantâneas, checando `TrainingDummy`
  pro param `on_dummy`); `use_consumable` →
  `world_server.py::apply_consumable`; `equip_item` →
  `update_player_equipment` (só slots que de fato mudaram de item);
  `reach_tile` → `move_player()`; `reach_level` → já disparava via
  `process_levelups` (só passou a incluir `player_eid`).
- **Drop condicional de quest** (`roll_conditional_loot`, ex: Pelo de
  Urso) movido para `server/server_death_handler.py`, mesmo ponto que já
  rola `roll_mob_loot` — antes só existia no cliente
  (`QuestSystem.get_conditional_loot`), nunca chegava no Inventory real.
- **`QUEST_ACCEPT`/`QUEST_TURN_IN`** (C→S, novo protocolo) processados em
  `server/session.py::_handle_quest_accept`/`_handle_quest_turn_in` —
  validam nível/pré-requisitos/objetivos-completos contra o
  `QuestLog`/`CharacterStats`/`PlayerSkills` do PRÓPRIO servidor. Entrega
  concede XP pelo canal já existente (`_pending_xp_deliveries` →
  `process_levelups`, mesmo usado por mana/HP5), gold direto em
  `Wallet.gold`, remove itens de quest do `Inventory` real, e persiste
  imediatamente (mesmo padrão de `TALENT_UPDATE` — crash do servidor não
  perde a entrega). `talk_to_npc` é aplicado inline nos dois handlers
  (payload leva `npc_name`) em vez de precisar de uma mensagem extra.
- **Cliente** (`quest_system.py`): `QuestSystem`/`QuestDialogSystem`
  ganharam `self._net` (mesmo gate de `ManaSystem`/`ConsumableSystem` —
  `self._qs._net`, já que `QuestDialogSystem` guarda referência ao
  `QuestSystem`). Online: `QuestSystem.update()` só descarta
  `QUEST_EVENTS` locais (servidor já processa) e os botões Aceitar/
  Concluir mandam `QUEST_ACCEPT`/`QUEST_TURN_IN` em vez de mutar
  `QuestLog` direto. `QUEST_UPDATE` (handler novo em
  `client/network_handlers.py`) substitui `QuestLog.active`/`.completed`
  pelo snapshot do servidor (mesmo padrão de `SKILL_LEVELS_UPDATE`) e
  dispara o LOG/PROC de "missão completa" quando vem `completed_qid`.
  `quests_json` restaurado no login em
  `client/save_sync_handlers.py::_restore_save_state` (snapshot inicial).
  Caminho offline (`self._net` falsy) **inalterado** — só passou a
  delegar pra `quest_logic.py` em vez de duplicar a lógica inline.

**Limitações conhecidas, deixadas fora de escopo** (gaps pré-existentes
separados, não introduzidos por esta migração):
- `reach_tile` em OUTRO mapa (ex: `atividade_suspeita`,
  `"maps/map_cave_west.csv"`) não dispara — o servidor só carrega 1 mapa
  por vez hoje (`ZONE_CHANGE`/`ENTER_INSTANCE` ainda não implementados).
- `learn_skill` é sincronizado contra `PlayerSkills.learned_skill_ids`
  (já populado via SAVE_STATE), não contra um evento de "aprendeu skill
  validado" — `trainer_system.py::_do_learn` (gold/nível pra aprender)
  continua 100% client-side, sem validação server-side; fora de escopo
  (economia do treinador, não persistência de quest).
- `equip_item` herda o mesmo nível de confiança que `EQUIP_SYNC` já tinha
  (servidor armazena o que o cliente reporta, sem revalidar contra regras
  de classe/level) — não piorado nem corrigido aqui.
- Anti-cheat de "está perto do NPC certo" não existe pra
  `QUEST_ACCEPT`/`QUEST_TURN_IN` (só valida pré-requisitos/objetivos, não
  proximidade) — escopo desta migração foi persistência/progresso, não
  anti-cheat completo de interação com NPC.

**Validação**: 4 fases num diagnóstico isolado (`tests/helpers.py` +
`Session`/`SessionManager` fake, mesmo padrão usado a sessão inteira). (A)
DB real temporária (não toca `data/game.db`): `quests_json` salvo via
`save_character`/lido via `get_character` — round-trip idêntico. (B) ciclo
completo de "Iniciação Arcana" via mensagens reais: `QUEST_ACCEPT` →
`learn_skill` sincronizado (seta `learned_skill_ids`, 1 tick) →
`use_skill` x5 `on_dummy=True` (evento real) → `can_turn_in=True` →
`QUEST_TURN_IN` → XP +80 concedida, quest movida pra `completed`,
`QUEST_UPDATE` com `completed_qid` recebido. (C) **o repro exato do
usuário**: `get_player_save_data()` → novo `WorldServer` do zero (simula
fechar/abrir o jogo) → `spawn_player` com o `quests_json` salvo →
confirmado `QuestLog.active` vazio e `"iniciacao_arcana" in completed` —
quest NÃO reaparece, XP não regride. (D) gatilhos reais adicionais: `kill`
via `server_death_handler.py` (mob real morto, first-attacker correto) e
`reach_tile` via `move_player()` — ambos disparam com `player_eid`
correto. Suíte sem regressão (32/49/1, 2 runs estáveis).

---

<details>
<summary>Entrada original (investigação de tamanho, 25/06/2026) — preservada por contexto histórico</summary>

Investigação de tamanho pedida pelo usuário, sem implementar a migração
ainda — só a parte de extensibilidade do schema foi feita naquela rodada.

**Estado real, confirmado por leitura direta (não pelo doc antigo)**:
- `quests_data.py`/`quest_events.py` já são pygame-free — zero mudança
  necessária pra serem usados no servidor.
- `QuestLog` (componente, `components.py`) só é anexado ao player em
  `entity_factory.py` (caminho cliente/offline). `WorldServer.spawn_player()`
  NUNCA cria esse componente — o servidor não tem nenhum estado de quest,
  nem vazio.
- Persistência de progresso de quest é só o save-slot local
  (`save_system.py`, herança do single-player) — `_collect_save_state()` (o
  payload que vai pro servidor via SAVE_STATE) não inclui `quests`. Hoje,
  progresso de quest nunca chega no banco do servidor; só sobrevive no save
  local da máquina. Problema de arquitetura independente da falta de
  validação: o modelo de persistência de quest não foi pensado pra
  multiplayer (jogar de outra máquina/reinstalar perde tudo).
- Gatilhos que JÁ são autoritativos no servidor hoje, prontos pra alimentar
  progresso de quest sem trabalho adicional de validação: kill (morte de
  mob), use_skill (CAST_SKILL), use_consumable (CONSUMABLE_USE), equip_item
  (EQUIP_SYNC, Tier B), reach_level (`process_levelups` já roda no
  servidor), reach_tile (posição já é autoritativa).
- O que realmente falta por completo: aceitar/entregar quest com NPC — hoje
  é 100% local (`QuestDialogSystem` chama `turn_in()` direto no componente
  do cliente, que aplica XP/gold/remove item de inventário ali mesmo). Não
  existe mensagem de protocolo pra isso.

**Tamanho estimado**: comparável aos Tiers B+C juntos, mais desenho de
protocolo novo — (1) componente QuestLog + persistência no servidor/DB, (2)
2 mensagens novas (`QUEST_ACCEPT`/`QUEST_TURN_IN`) + broadcast S→C do estado,
(3) extrair a lógica pygame-free de `quest_system.py` pro padrão de
`core_systems.py` (mesmo split já feito pra `CombatStateSystem`) e ligar nos
6 gatilhos já autoritativos listados acima, (4) religar o cliente pra só
exibir o estado que o servidor manda, não mais calcular localmente.

</details>

### ✅ RESOLVIDO — Extensibilidade de ObjectiveDef (novos tipos de objetivo)

**Gap encontrado**: `ObjectiveDef.target` é um único campo string. Um tipo
de objetivo que precise de DOIS identificadores independentes (ex: "usar
item X num alvo Y") não tinha onde colocar o segundo sem reaproveitar um
campo existente pra dois sentidos diferentes (gambiarra).

**Fix**: `ObjectiveDef` (quests_data.py) ganhou `params: dict = {}` —
catch-all pra dados específicos de um tipo, sem precisar editar o
NamedTuple de novo a cada tipo novo que apareça (escolhido sobre a
alternativa de só adicionar um campo nomeado `item_name`, porque cobre
qualquer tipo futuro — entregar item a NPC, escoltar, defender área — não
só o caso item+alvo). Documentado na docstring do módulo, com checklist de
4 passos pra adicionar um tipo novo.

Implementado como prova concreta: tipo `use_item_on_target` (item usado
sobre um alvo — ex: óleo inflamável num urso). `target` = quem recebe,
`params["item_name"]` = o item usado. Branches adicionados em
`QuestSystem._matches()`/`_obj_label()` (quest_system.py). Evento
documentado em `quest_events.py`: `use_item_on_target {item_name,
target_name, target_race}`.

**Nota**: a mecânica de jogo "usar item sobre um alvo" (ex: arremessar um
item num inimigo) não existe ainda no jogo — só o tipo de objetivo de quest
está pronto pra reconhecer o evento quando algum sistema de gameplay vier a
disparar `quest_events.fire("use_item_on_target", ...)`. Não foi criada
nenhuma mecânica nova nesta rodada, só a capacidade do sistema de quests de
rastreá-la quando existir.

Validado: `ObjectiveDef(type="use_item_on_target", target="Urso",
params={"item_name": "Oleo Inflamavel"})` — match correto com item+alvo
certos, rejeita item errado, rejeita alvo errado, aceita `target="*"`
(qualquer alvo); label renderiza "Usar Oleo Inflamavel em Urso: 1/2"; tipos
existentes (kill, etc.) não afetados pelo novo campo `params` (default
vazio). Compile limpo, suíte sem regressão (mesma baseline 32/49/1).

### 🔴 CRÍTICO — Bug real (regressão do Tier C): personagens perdiam talentos alocados permanentemente, sem reembolso

Reportado pelo usuário: arqueiro nível 13 com talentos alocados antes desta
sessão apareceu com `allocated={}` e disponíveis incorretos após login.
Confirmado via leitura direta do `data/game.db`: `talents_json =
'{"allocated": {}, "available_points": 9}'` — nível 13 deveria ter 12 pontos
totais (1 por level-up, `process_levelups`); 3 estavam genuinamente
desaparecidos (nem alocados, nem disponíveis).

**Causa raiz (cadeia completa, confirmada por leitura de código, não
suposição)**:
1. `WorldServer.validate_talent_allocation` (Tier C, introduzido nesta sessão)
   retornava `{"allocated":, "available_points":}` — **sem** `chosen_build`.
2. `_handle_save_state` faz `payload["talents"] = _checked_tal` —
   SUBSTITUI o dict inteiro, não faz merge. Resultado: `chosen_build` nunca
   mais era persistido em `talents_json` a partir do primeiro
   TALENT_UPDATE/SAVE_STATE após a alocação de qualquer ponto.
3. No login seguinte, `client/save_sync_handlers.py::_restore_save_state` lê
   `talents_json` sem `chosen_build` → cai no default de `TalentTree.
   chosen_build` ("cavaleiro").
4. `TalentSystem.apply_talent_effects()` (client, `talent_system.py`,
   PRÉ-EXISTENTE — não foi tocado nesta sessão até agora) compara
   `tt.chosen_build` contra `CLASS_BUILD_MAP[classe]` (arqueiro→"bardo") —
   sempre dava mismatch por causa do passo 3 — e fazia só
   `tt.allocated.clear()`, **sem devolver os pontos pra
   `available_points`** (diferente de `reset_talents()`, que reembolsa
   corretamente). Pontos gastos viravam pó, silenciosamente, todo login.
5. O ciclo se autoperpetuava: o save seguinte (com `allocated={}` já
   "corrigido" pelo cliente) ia de novo por `validate_talent_allocation`,
   que de novo omitia `chosen_build` — nunca se corrigia por conta própria.

Resposta direta às perguntas do usuário: os talentos ESTAVAM salvos — o save
em si funcionava — mas o dado salvo ficou incompleto (faltando
`chosen_build`) a partir do primeiro save após o Tier C, e isso disparava um
bug pré-existente e não relacionado (item 4) que descartava os pontos
alocados sem reembolso. E sim — a sugestão do usuário de recalcular o
orçamento a partir do nível está certa e foi exatamente o que foi
implementado como rede de segurança (abaixo), independente de já ter
corrigido a causa raiz.

**Fix (3 camadas, raiz + reembolso + rede de segurança — mesmo princípio de
sempre nesta sessão: corrigir a causa E nunca deixar a perda ser
irrecuperável)**:
1. `validate_talent_allocation` agora lê `claimed.get("chosen_build")` e o
   preserva em `tt.chosen_build` e no dict retornado — nunca mais é
   descartado de um save.
2. `TalentSystem.apply_talent_effects()` (client) — antes de
   `tt.allocated.clear()` por build incompatível, agora faz
   `tt.available_points += sum(tt.allocated.values())` primeiro (mesmo
   padrão de `reset_talents()`). Mesmo que um mismatch de build aconteça de
   novo por outro motivo no futuro, os pontos voltam pro jogador, nunca são
   destruídos.
3. **Rede de segurança server-side, em `spawn_player`** — calcula
   `expected_budget = max(0, char.level - 1)` (1 ponto por level-up) e, se
   `available_points + sum(allocated.values())` carregado do banco for menor
   que isso, completa a diferença em `available_points` automaticamente, todo
   login. Também propaga a correção pro payload de `LOGIN_OK`
   (`char_data["talents_json"]` é atualizado in-place antes do envio), senão
   o cliente recebia o valor antigo e desfazia a correção na primeira sync.
   Também passou a carregar `chosen_build` do save no spawn (antes nunca era
   lido server-side, ficava sempre no default).

Isso recupera automaticamente o personagem nível 13 do usuário (e qualquer
outro afetado) no próximo login — sem precisar editar o banco manualmente.
Nota para o futuro: essa rede de segurança assume que o único jeito de
*perder* orçamento de talento é um bug; se um dia existir uma mecânica
legítima de "respec paga remove pontos permanentemente", essa rede vai
reverter essa remoção — revisar este safety net se isso for implementado.

Validado: simulação exata do estado salvo do personagem real (nível 13,
`talents_json` sem chosen_build, `allocated={}`, `available_points=9`) —
após spawn, `available_points` corrigido para 12, `talents_json` propagado
pro payload de login já corrigido; `validate_talent_allocation` testado
preservando `chosen_build="bardo"` corretamente através de uma chamada.
Compile limpo, suíte sem regressão (mesma baseline 32/49/1).

**Não validado**: login real do usuário confirmando que os 12 pontos
aparecem disponíveis e que alocar em talentos do build correto (bardo) não
dispara mais o mismatch; também não validado, por ser client-side e exigir
janela do Pygame, o reembolso de `apply_talent_effects()` num cenário real
de troca de build.

### 🔴 CRÍTICO — Tiro Múltiplo: 2 bugs (direção zero + mecânica errada — área instantânea em vez de flecha por alvo)

Reportado pelo usuário em 2 mensagens: (1) skill mostrava a mira mas não
acertava nada; (2) depois de corrigir (1), acertava todo mundo no cone
instantaneamente — mecânica errada, deveria ser 1 flecha física por alvo,
igual ao projeto offline (`rpg_ecs/spell_system.py::_apply_tiro_multiplo`).

**Bug 1 — direção sempre (0,0)**: o envio genérico de `CAST_SKILL`
(`systems.py`, usado por toda skill sem sistema dedicado) mandava
`dir_x: 0.0, dir_y: 0.0` hardcoded. Pirofagia escapa porque tem
`PirofagiaSystem` próprio, que calcula a direção real do mouse e manda seu
próprio `CAST_SKILL` — Tiro Múltiplo não tem essa exceção. No servidor,
`dlen = hypot(dir_x, dir_y); if dlen < 0.001: return` descartava
silenciosamente. **Fix**: o envio genérico agora calcula a direção real
jogador→mouse (mesmo cálculo de `PirofagiaSystem`: posição do player, câmera,
mouse em coords de mundo) antes de mandar `CAST_SKILL`. Efeito colateral
bom: ativa a compensação de lag (`_is_cone` em `skill_processor.py`) que
também estava inerte pelo mesmo motivo.

**Bug 2 — dano de área instantâneo em vez de flecha por alvo**: com o
bug 1 corrigido, `_server_tiro_multiplo` (dispatch genérico, dano aplicado
na hora) acertava TODOS os alvos no cone simultaneamente, sem flecha
individual — diferente da mecânica real (1 flecha física por alvo,
resolvida no impacto, número de alvos = talento: 1pt→2, 2pt→3, 3pt→ilimitado
dentro do alcance).

**Fix (segue o padrão já usado por Flecha Reiterada/Tiro Repulsivo — flecha
confirmada via `PROJECTILE_HIT_CS`, não dano instantâneo)**:
- `_complete_tiro_multiplo_cast` (novo) — ao concluir o canal, seleciona os
  alvos no cone (mesmo cálculo de ângulo/range de antes, sem aplicar dano),
  ordena por distância, limita por `tiro_multiplo_targets` (talento) E por
  `quiver.arrow_count` (sem isso, um cone com mais alvos que flechas geraria
  acertos "de graça" — gap que nem a versão antiga nem a offline cobriam
  explicitamente do mesmo jeito, mas é necessário aqui porque cada alvo
  selecionado garante 1 acerto). Enfileira 1 entrada por alvo em
  `_spells_in_flight_queue` (mesmo mecanismo das outras skills de flecha).
- `_server_tiro_multiplo_hit` (novo, substitui `_server_tiro_multiplo`) —
  aplica 1 flecha a 1 alvo, chamado por `PROJECTILE_HIT_CS`; usa
  `_server_apply_ranged_physical`, que já consome 1 flecha por chamada
  (mesmo padrão de `_server_flecha_reiterada/_server_tiro_repulsivo` —
  munição é gasta conforme cada flecha CONFIRMA o impacto, não no cast).
- `skill_entry["projectile_targets"]` (lista, não um único `target_id`) —
  novo campo no `SKILL_RESULT` de conclusão, pro cliente (caster) saber em
  quais alvos criar as flechas visuais. Notificação a outros players usa
  `proj_targets` (lista) no lugar de `proj_target` (singular).
- Cliente (`network_handlers.py`): bloco novo que lê `projectile_targets` e
  cria 1 `PlayerProjectile` por alvo (com leve `launch_delay` escalonado,
  igual à versão offline), reaproveitando o sistema de colisão de projétil
  já existente (`game.py` já envia `PROJECTILE_HIT_CS` genericamente pra
  qualquer `PlayerProjectile` que colide — nenhuma mudança necessária ali).
  Espectadores (outros players) recebem o mesmo tratamento cosmético já
  usado por Flecha Reiterada, mas iterando sobre a lista de alvos.

Validado com diagnóstico permanente (`tests/diag_tiro_multiplo.py`, segue o
padrão de `tests/diag_tiro_repulsivo.py` já existente): 3 mobs alinhados no
cone + 1 fora do cone, talento=3 alvos → seleciona exatamente os 3 do cone,
exclui o de fora; munição não é gasta na conclusão do cast, só conforme cada
`PROJECTILE_HIT_CS` confirma (50→49→48→47, uma flecha por confirmação); fila
de voo correta (sem duplicatas, vazia ao final). Teste complementar: 3 alvos
no cone + talento=3 mas só 2 flechas na aljava → seleciona exatamente 2 (cap
por munição funciona). Compile limpo, suíte sem regressão (32/49/1).

**Não validado**: passada manual num cliente real vendo as flechas
individuais voarem visualmente (timing do `launch_delay`, escalonamento) —
só a lógica server-side e a seleção de alvo foram validadas via diagnóstico.

### ✅ RESOLVIDO (raiz) — Problema "G" batendo de novo: broadcast pra espectador não é automático, exige plumbing manual por skill

Reportado pelo usuário: flechas de Tiro Múltiplo não apareciam pro player
remoto — e isso "acontece bastante", toda vez que algo novo é implementado.
Pedido explícito: analisar a causa raiz, não só o bug pontual, e resolver de
uma vez por todas.

**Causa do bug pontual**: `server/session.py` tinha uma allowlist manual
campo-a-campo decidindo o que do dict interno (`xp_entry`) vira payload de
`STATS_UPDATE` (canal `_pending_xp_deliveries`). Tiro Múltiplo precisava de
`proj_targets` (lista, múltiplos alvos) mas a allowlist só conhecia
`proj_target` (singular, de quando só Bola de Fogo/Flecha Reiterada/Tiro
Repulsivo existiam). Campo novo, sem espelho na allowlist → descartado sem
erro nenhum a caminho do cliente.

**Causa raiz mais profunda (por que isso "acontece bastante")**: existem DOIS
mecanismos paralelos e inconsistentes pra "avisar outros players de algo":
1. **Broadcast AOI de verdade** (`SKILL_RESULT`, `SKILL_EFFECT`, sync de HP) —
   um loop sobre `self._sessions.values()` filtrando por raio de distância,
   automático pra QUALQUER entidade nova nesses canais, sem precisar de lista
   de quem notificar.
2. **`_pending_xp_deliveries`** (canal `STATS_UPDATE`) — pensado pra entrega
   PESSOAL de XP/recursos a 1 jogador específico (via `get_session_id_for_player`),
   mas years atrás foi reaproveitado pra TAMBÉM avisar espectadores de
   projétil chegando (`proj_incoming`) — só que sem o filtro de AOI do
   mecanismo 1: o código fazia um loop manual sobre `self._player_eids.values()`
   (TODOS os players conectados no servidor, sem checar distância) E exigia
   uma allowlist própria em `session.py` (a causa do bug pontual). Toda skill
   nova de projétil duplicava esse loop + arriscava esquecer a allowlist —
   exatamente a categoria de bug que se repete.

**Fix (elimina o mecanismo 2 pra essa categoria de evento, não só remenda)**:
1. `_pending_xp_deliveries` → `STATS_UPDATE`: a allowlist foi substituída por
   `_payload = dict(xp_entry)` (encaminha QUALQUER campo que o produtor
   colocou, com rename de `player_eid`→`eid`/`xp`→`xp_gained`). Auditado os
   16 pontos de criação desse dict em 4 arquivos — todos já são dicts puros,
   só com campos destinados ao cliente, nenhum interno/sensível. Daqui pra
   frente, campo novo nesse canal chega sem precisar tocar `session.py`.
2. **Notificação de espectador de projétil foi removida do canal 2 e
   consolidada no canal 1** (`SKILL_EFFECT{event:"launch"}`), que já é
   broadcast AOI automático pra qualquer skill. Extensões mínimas no payload
   já existente: `target_eids` (lista, novo — cobre Tiro Múltiplo) e
   `arrow_count` (já existia em `projectile_target`/`SKILL_RESULT`, agora
   também no `SKILL_EFFECT`, pra Flecha Reiterada).
3. Cliente: nova `_spawn_bystander_projectile()` (única, reusada por
   `bola_de_fogo`/`flecha_reiterada`/`picada_escorpiao`/`tiro_repulsivo`/
   `tiro_multiplo`) chamada do `_handle_msg_skill_effect` no evento "launch"
   — substitui os ~100 linhas de código quase-duplicado que existiam em
   `_handle_msg_stats_update` pra cada skill. Movida para ANTES do early-return
   por ausência de som configurado (`if not snd and not snds: return`) — Tiro
   Múltiplo não tinha (nem tem) som de "launch" no `SKILL_CATALOG`, e esse
   early-return teria silenciosamente pulado a criação do projétil cosmético
   de novo se a checagem de projétil ficasse depois dele. Esse acoplamento
   acidental (visual de projétil dependendo de existir configuração de som)
   é exatamente o tipo de causa "invisível" que faz esse bug se repetir —
   documentado aqui para não cair na mesma pegadinha numa skill futura.

Validado: `tests/diag_tiro_repulsivo.py` (já existente, não relacionado a
Tiro Múltiplo) roda ponta-a-ponta sem mudança de comportamento — confirma que
a consolidação não regrediu as 3 skills de projétil pré-existentes.
`tests/diag_tiro_multiplo.py` (novo) confirma `target_eids` chegando correto
no evento `SKILL_EFFECT{event:"launch"}` com os 3 alvos certos. Compile
limpo, suíte sem regressão (32/49/1).

**Não validado**: passada manual com 2 clientes reais confirmando que o
espectador vê a flecha de Tiro Múltiplo (e que as outras 4 skills de
projétil continuam aparecendo certo pro espectador depois da consolidação).

### 🔴 REGRESSÃO (Tier E desta sessão) — Interceptar mostrava "Caminho bloqueado" sem nenhum obstáculo

Reportado pelo usuário: Guerreiro usando Interceptar no Arqueiro (depois
também testado contra um mob) mostrava "BLOQUEADO" mesmo em terreno
totalmente aberto, sem parede nem entidade no meio.

**Causa raiz**: `_skill_interceptar` (skill_handlers.py) sempre chamou
`self._resolve_target(combat_state, tile_move, 1)` — o `1` nunca representou
o alcance real da skill (2–6 tiles, `INTERCEPT_MIN/MAX_RANGE_PX`, validado
de verdade por um `_range_ok()` separado mais abaixo); era só um valor
default que nunca importava, porque `_resolve_target()` (antes do Tier E)
não validava range de um alvo JÁ selecionado — só no fallback de
auto-seleção. O Tier E (correção de segurança desta sessão, ver acima nesta
seção) passou a validar o alvo já selecionado contra `_max_range` também —
correto para as outras 5 skills melee que chamam com `_max_range=1`
(Golpe Poderoso, Vitória Iminente, Executar, Golpe Debilitante, Punho no
Queixo — nelas, `1` É o alcance real). Para Interceptar, que é um dash de
longo alcance, isso passou a rejeitar qualquer alvo a mais de 1 tile ANTES
mesmo de chegar no `_range_ok()` — `_resolve_target` retornava -1
("Nenhum alvo"), mas como o handler já tinha decidido seguir adiante com
`target_id` válido vindo do client (`combat_state.target_entity_id` setado
direto por `tid` do CAST_SKILL, sem passar por `_resolve_target` no client),
o fluxo real batia direto no branch de `_dash_path_clear`/"Caminho
bloqueado" mais abaixo. Confirmado via diagnóstico: causa raiz exata era
`_resolve_target` devolvendo -1 silenciosamente por estar fora do range de 1
tile, não um bloqueio de terreno de verdade.

**Fix**: `_skill_interceptar` agora chama `_resolve_target(..., 8)` — cobre
o alcance máximo real (`INTERCEPT_MAX_RANGE_PX` ≈ 7.25 tiles em chebyshev no
pior caso, cardinal) com margem. `_range_ok()` (pixels, mais preciso)
continua sendo o gate real de alcance; o `8` aqui só evita que
`_resolve_target` rejeite cedo demais um alvo que `_range_ok` aceitaria.
Auditado os outros 11 call sites de `_resolve_target` — só Interceptar tinha
esse descompasso entre o `_max_range` passado e o alcance real da skill.

Validado: diagnóstico contra player (Arqueiro) e contra mob, 4 tiles de
distância, terreno 100% aberto (solidez de cada tile confirmada) — antes do
fix, ambos rejeitavam com "Nenhum alvo"/sem mover; depois do fix, ambos
completam o dash normalmente. Reteste do diagnóstico já existente
(`tests/diag_interceptar.py`, caso de parede real) confirma que o bloqueio
genuíno continua funcionando — não virou um "sempre permite". Suíte sem
regressão (32/49/1).

### 🟡 RESOLVIDO — Bloco de Gelo não dispelava efeitos negativos já ativos

Reportado pelo usuário: descrição da skill promete imunidade "a todo dano e
efeito negativo", mas efeitos já ativos (slow, root, DOT, etc.) continuavam
agindo normalmente depois de usar a skill.

**Causa raiz**: `_skill_bloco_de_gelo` só setava `combat_state.is_immune =
True` (bloqueia dano/tick NOVO — ver `StatusEffectSystem._apply_tick`, "Bloco
de Gelo é a única exceção" ao tick de DOT) e `is_stunned = True`. Nunca tocava
em `StatusEffects.effects` — qualquer debuff já ativo (slow/root/fear/
polymorph/etc.) continuava lá, com `slow_mult`/`is_rooted`/
`is_crowd_controlled` sendo resincronizados normalmente todo tick a partir
dele (`StatusEffectSystem.update()`), porque `is_immune` nunca era
consultado por esse código. DOTs (poison/bleed/burn) "pausavam" o tick
enquanto imune, mas a duração seguia contando — voltavam a causar dano
quando o bloco expirasse, em vez de serem removidos.

**Fix**: mesmo padrão já usado por Camuflagem (que dispela poison/bleed/burn
ao ativar), mas data-driven em vez de lista fixa — usa
`EFFECT_DEFS[nome].is_buff` (já existente em `status_effects_data.py`) para
remover QUALQUER efeito com `is_buff=False` de `StatusEffects.effects`,
preservando buffs (`is_buff=True`, ex: Enfurecido/Acelerado/Regeneração).
Não precisa replicar reset de `slow_mult`/`is_rooted`/etc. manualmente —
`StatusEffectSystem.update()` já resincroniza tudo isso automaticamente no
próximo tick a partir do que sobrou em `sfx.effects`.

Validado: aplicado slow+root+burn+poison+haste (buff) no Mago, ativado Bloco
de Gelo — os 4 negativos foram removidos, o buff (haste) permaneceu intacto,
`IceBlockEffect`/`is_immune` ativados normalmente. Compile limpo, suíte sem
regressão (32/49/1).

**Não validado**: passada manual num cliente real confirmando visualmente
que ícones de debuff desaparecem da UI ao ativar Bloco de Gelo.

### 🔴 CRÍTICO — Mago "sem mana" logo após login, mesmo com mana cheia (e mesma classe de bug pra Raiva)

Reportado pelo usuário: skill do Mago falha por "Mana insuficiente" assim
que loga, mesmo com mana cheia; funciona normalmente depois de alguns
segundos. Diagnosticado com debug temporário pedido ao usuário (removido
após achar a causa) — log real mostrou `side=SERVER mana=0 max_mana=300`,
confirmando que o servidor (autoritativo) tinha a mana ZERADA, não o
cliente.

**Causa raiz**: `_handle_cast_skill` (server/session.py) lia `rage`/`mana` de
TODO payload de `CAST_SKILL` e chamava `sync_player_resources(...)`, que
adotava esses valores DIRETO no `CharacterStats` do servidor — comentário
original: "o cliente é fonte de verdade para recursos de combate... igual ao
offline". O cliente manda `_cs.mana` (campo-espelho em `CombatStats`,
default 0, só sincronizado por ~4 gatilhos específicos — cast de magia,
regen, etc.) — NUNCA sincronizado com `CharacterStats.mana` (o valor real,
calculado corretamente em `spawn_player`) logo após o login. Resultado: a
PRIMEIRA tentativa de skill do Mago manda `mana=0` (espelho ainda não
tocado) e o servidor SOBRESCREVE sua própria mana real (300) com esse zero
— autoinfligido, antes mesmo do handler da skill checar o custo. "Funciona
depois de alguns segundos" porque, em algum momento, esse espelho do
cliente acaba sendo tocado por um dos outros gatilhos e passa a mandar o
valor certo.

Confirmado que é a MESMA classe de bug pra Raiva (Guerreiro) — só não é
visível porque Raiva começa em 0 mesmo (cliente mandando 0 "por acidente"
coincide com a realidade). Concentração (Arqueiro) não é afetada — só
rage/mana passam por esse handler.

Vulnerabilidade de segurança equivalente à categoria já fechada nesta sessão
(PLAYER_STAT_SYNC/PLAYER_HP_SYNC, Tier A): um cliente malicioso podia mandar
`mana`/`rage` arbitrários em QUALQUER `CAST_SKILL`, não só errar por acidente
— ex: inflar rage pra spammar Golpe Poderoso sem gerar via combate real.

**Fix**: removida a leitura/confiança de `rage`/`mana` em `_handle_cast_skill`
— o servidor já rastreia os dois 100% por conta própria (ganho de rage em
`combat_processor.py`; custo de mana/rage deduzido nos próprios handlers de
skill, compartilhados client+server; mana deduzida na conclusão do cast em
`_process_spell_cast_completions`). `sync_player_resources` nunca precisou
do cliente pra nada — virou só utilitário de setup pra
`tests/diag_skills*.py`, não chamado mais pelo fluxo real.

Validado: reproduzido o bug exato com personagem real do banco
(intelligence=10 → max_mana=300) — `CAST_SKILL` forjado com `mana=0`
(replicando o que o cliente mandava antes do espelho sincronizar) não altera
mais a mana do servidor (permanece 300). Compile limpo, suíte sem regressão
(32/49/1).

**Não validado**: passada manual num cliente real confirmando que a
primeira skill funciona imediatamente após login, sem esperar os "alguns
segundos" relatados.

---

### ✅ RESOLVIDO — Consumível às vezes não regenera (feedback de testers, 06/07/2026)

Reportado por testers: em alguns momentos, usar um consumível (poção etc.)
não curava/restaurava nada — e o item sumia do inventário mesmo assim.

**Causa raiz**: modelo antigo era "cliente aplica local + avisa servidor".
`ConsumableSystem._use_consumable` (systems.py) mutava `current_hp`/`mana`
e CONSUMIA o item (`item.stack -= 1`) IMEDIATAMENTE, ANTES de qualquer
confirmação do servidor. O servidor (`apply_consumable`,
`server/world_server.py`) tinha seu próprio check de "recurso já cheio"
usando o HP/mana REAL dele (que pode divergir do que o cliente acha, por
qualquer drift natural de tick/latência) — se bloqueasse, `apply_consumable`
simplesmente `return`ava, **sem mandar nenhum pacote de volta**. Resultado:
cliente já tinha "curado" visualmente e perdido o item; servidor nunca
aplicou nada de verdade e nunca avisou; jogador fica sem efeito real e sem
o item, sem entender por quê.

**Fix — modelo invertido (servidor decide, cliente só reflete)**:
- `apply_consumable` (`server/world_server.py`) agora SEMPRE responde ao
  `CONSUMABLE_USE`, aceito ou rejeitado, via `queue_stats_update` com
  `item_name` (chave de correlação) + `consumable_ok: True` OU
  `consumable_rejected: True, reason: "hp_full"|"mana_full"|"in_combat"`.
- `ConsumableSystem._use_consumable` (systems.py), modo online: não muta
  MAIS nada localmente (nem HP/mana, nem HoT, nem o item) — só valida
  localmente pra feedback rápido (mesmos avisos de antes), manda o
  `CONSUMABLE_USE` e guarda `pending_item_name`. Offline: comportamento
  inalterado (aplica tudo local, sem servidor).
- `ConsumableSystem._finalize_consumable` (novo): chamado só quando o
  servidor confirma — AGORA remove 1 unidade do item e aplica os HoTs
  locais (a cura/mana instantânea chega via `heal_amount`/`mana_amount` no
  mesmo canal, já tratado por `network_handlers.py`).
- `client/network_handlers.py::_handle_msg_stats_update`: novo bloco no
  topo do `if eid == self._my_eid` — em `consumable_ok`, chama
  `_finalize_consumable`; em `consumable_rejected`, mostra `WARN` com o
  motivo (mapeado pra mensagem em PT). Limpa `pending_item_name` nos dois casos.

Validado: teste headless — servidor aceita e cura quando há espaço (envia
`consumable_ok` + `heal_amount`), rejeita com `reason="hp_full"` sem curar
quando já cheio (nenhum `heal_amount` enviado); cliente NÃO muta HP/stack/HoT
no momento do uso (só ao receber `_finalize_consumable`), e SÓ ENTÃO remove
o item e aplica o HoT. Suíte sem regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual em rede real confirmando que o consumível
nunca mais "some" sem efeito, e que a latência de confirmação (round-trip
servidor) não introduz atraso perceptível no feedback visual.

---

### ✅ RESOLVIDO — Loot/quest kill ia pro player errado quando alguém só usava skill (feedback de testers, 06/07/2026)

Reportado por testers: loot deveria ir pra quem ataca PRIMEIRO, não pra
quem "aggra" o mob.

**Causa raiz**: `_mob_damage_log` (dict `mob_eid → {player_eid: dano
somado}`, em ordem de inserção — `next(iter(...))` no death handler decide
quem foi o "primeiro atacante" = dono do loot/quest kill) só era escrito
manualmente em DOIS lugares: `combat_processor.py` (loop de auto-attack) e
um ponto específico de Fatiador de Corpos (`world_server.py`). **Nenhuma
outra skill logava dano** — Bola de Fogo, Golpe Poderoso, Executar,
Impacto, Vitória Iminente, Punho no Queixo, flechas de skill (Tiro
Repulsivo, Flecha Reiterada, Picada de Escorpião, Tiro Múltiplo), Nova
Congelante, Calcinar. Se um player só usa skill contra um mob (ex: mago
solando com magia) e outro player chega depois e dá um único auto-attack,
o SEGUNDO player "rouba" o loot e o crédito de kill de quest — porque é o
primeiro (e único) nome a aparecer no log. Caso extremo pior: um mob morto
SÓ por skills nunca tinha `damage_log` nem `killer_eid` válido (mortes via
skill recebem `PendingDeath(killer_entity_id=-1)` do sweep genérico, não do
handler específico) — `first_attacker_eid` caía em `-1`, e o corpse/loot
podia nem ser gerado.

**Fix — centralizado em `core_systems.apply_damage_core`** (núcleo único já
usado por TODO caminho de dano — problemas B/H desta sessão de trabalho):
- Novo callback `on_damage_dealt(attacker_eid, target_id, dmg)`, chamado
  sempre que `dmg > 0` é efetivamente aplicado (com `killer_eid` como
  identidade do atacante — mesmo campo, propósito duplo).
- `CombatSystem.__init__` ganha `on_damage_dealt=None`; `WorldServer`
  injeta `self._log_mob_damage_hit` na construção (`is_server=True`) — como
  `deal_damage()` é o núcleo COMPARTILHADO de melee auto-attack E de TODA
  skill física (`"physical_fixed"`), isso cobre as duas classes de uma vez.
- `_apply_final_damage` (`server/spell_completion_processor.py`, núcleo do
  dano mágico/ranged) ganha parâmetro `attacker_id` e passa o mesmo
  callback — cobre magia (`_server_apply_magic_damage`) e ranged
  (`_server_apply_ranged_physical`, tanto skill quanto auto-attack).
- `WorldServer._log_mob_damage_hit(attacker_eid, target_id, dmg)`: método
  único que escreve em `_mob_damage_log`.
- **Removidas as 2 escritas manuais antigas** (`combat_processor.py`,
  `world_server.py`/Fatiador) — com a centralização, elas contariam o MESMO
  golpe em dobro (inflaria XP proporcional e dano "por atacante").

Validado: 4 cenários headless — (1) skill física sozinha loga; (2) skill
mágica sozinha loga; (3) auto-attack melee continua logando o valor EXATO
do dano real (sem dobro, confirmando que a remoção das escritas manuais não
regrediu nem duplicou); (4) **cenário exato do bug relatado** — mago ataca
primeiro só com Bola de Fogo, guerreiro chega depois e auto-ataca:
`first_attacker` resolve corretamente pro mago, não pro guerreiro. Suíte
sem regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual com 2+ players reais em grupo, conferindo
que o loot/XP proporcional e o crédito de quest "kill" vão pro jogador que
realmente engajou primeiro, incluindo o caso de solo 100% skill.

---

### ✅ RESOLVIDO — Arqueiro: "Precisa de arco" falso + Recarregar não sincroniza (feedback de testers, 06/07/2026)

Reportado por testers: (a) aljava diz estar cheia mas não está depois de
usar Recarregar; (b) atacar com o arqueiro retorna "Precisa de um arco
equipado" mesmo com arco e aljava genuinamente equipados.

**Causa (a) — Recarregar nunca sincronizava**: `_skill_recarregar`
(skill_handlers.py), no modo online, só enfileirava o cast no servidor e
retornava — nunca tocava `arrow_count`/inventário locais. O servidor
recarregava de verdade (`_server_recarregar`,
`server/spell_completion_processor.py`) mas **nunca avisava o cliente**. A
cópia local do jogador (contagem de flechas na aljava, munição na bag)
ficava congelada no valor de quando foi equipada, divergindo do servidor
pra sempre após o primeiro uso online.

**Fix (a)**: `_server_recarregar` agora sempre envia confirmação via
`queue_stats_update` — `quiver_arrow_count`, `quiver_subtype`, `ammo_name`
+ `ammo_taken` (munição consumida da bag). `client/network_handlers.py::
_handle_msg_stats_update` aplica isso na `Equipment.offhand` e decrementa/
remove o item de munição correspondente no `Inventory` local.

**Causa (b) — subtype da arma nunca era salvo**: `_serialize_item`
(client/save_sync_handlers.py) só gravava o campo `subtype` dentro do
bloco condicional de aljava (`item_type == "quiver"`) — NUNCA para a arma
em si. Ao relogar, `_restore_item` tenta casar o item por NOME em
`loot_tables._T`; se o arco tiver um nome que não existe nesse catálogo
(comprado em loja com nome próprio, ou forjado), cai no fallback
`_item_from_data`, que lê `subtype = d.get("subtype", "")` — como nunca foi
salvo, vira `""`. Todo check `subtype == "Bow"` (inclusive o que decide se
o arqueiro "tem arco equipado") passa a falhar, mesmo com o item
visivelmente equipado.

**Fix (b)**: `subtype` incluído no loop geral de atributos sempre
serializados, não só no bloco específico de aljava — cobre QUALQUER item
com subtype (armas de qualquer classe), não só flechas.

Validado (headless): (5) servidor recarrega e sempre manda a confirmação
com os valores corretos; simulação do cliente aplicando a confirmação
atualiza `arrow_count`/`subtype` da aljava E decrementa a munição certa da
bag. (6) arma com nome FORA do catálogo de loot preserva `subtype="Bow"`
através de serializar→salvar→restaurar (antes virava `""`); aljava
continua funcionando (não regrediu). Suíte sem regressão (9F/83P, mesmo
baseline).

**Achado à parte (não corrigido — fora do escopo aprovado)**: o fallback
`_item_from_data` também não restaura `cast_range` (nem outros poucos
atributos fora da lista curta que ele copia) — gap pré-existente,
independente deste fix; provavelmente vale uma rodada própria revendo TODOS
os atributos de `Item` cobertos por esse fallback.

**Não validado**: passada manual em rede real — usar Recarregar online e
conferir que a aljava mostra o valor real (não "cheia" indevidamente);
relogar com um arco comprado/forjado (nome fora do catálogo de loot) e
confirmar que o arqueiro ataca normalmente sem a mensagem falsa.

---

### ✅ RESOLVIDO — Recompensa de gold da quest não aparecia (feedback de testers, 06/07/2026)

Reportado por testers: quest "De volta a terra" prometia 15 gold e não deu.

**Causa raiz**: `_handle_quest_turn_in` (`server/session.py`) credita
`wall.gold += reward.gold` corretamente — a persistência já estava certa
(`get_player_save_data` lê o `Wallet` vivo). Mas, ao contrário do bloco de
XP logo ACIMA no mesmo handler (que já chama `queue_stats_update`), o bloco
de gold **nunca notificava o cliente**. O jogador só veria o gold correto
no próximo relogin — na sessão atual, o contador na tela ficava parado.

**Fix**: `queue_stats_update({"player_eid": eid, "gold": wall.gold})` logo
após creditar — mesmo padrão já usado pro XP. `client/network_handlers.py::
_handle_msg_stats_update` ganha um novo bloco lendo `"gold"` do payload e
aplicando (valor ABSOLUTO, mesmo padrão de hp/hp_max) no `Wallet` local.

Validado: teste headless via `_handle_quest_turn_in` real (quest
"de_volta_a_terra", progresso forçado como completo, save_character
mockado) — `Wallet.gold` vai de 0→15 E a notificação
`{"player_eid": eid, "gold": 15}` é corretamente enfileirada em
`queue_stats_update` (antes: nada). Suíte sem regressão (9F/83P, mesmo
baseline).

**Não validado**: passada manual completando uma quest com recompensa de
gold em rede real, conferindo que o contador na tela atualiza na hora, sem
precisar relogar.

---

### ✅ RESOLVIDO — Progresso de quest (collect_item) atrasado (feedback de testers, 06/07/2026)

Reportado por testers: quest "Presas Afiadas" — dropou a 1ª Presa de Lobo e
o objetivo não contou; dropou a 2ª e aí contou "2" de uma vez.

**Causa raiz**: `quest_logic.sync_collect_progress` (recalcula o progresso
comparando `Inventory` real do servidor contra `obj.count`) só era chamada
dentro de `_process_quest_events` — **como efeito colateral de OUTROS
eventos de quest** (kill, use_skill, etc.) passando pela fila
`QUEST_EVENTS`. Pegar um item de quest do corpo (`LOOT_REQUEST` →
`request_loot`, que nem chega a tocar o `Inventory` do servidor — quem
atualiza é o `INV_SYNC` que o cliente manda logo depois, via
`_on_loot_action`) **não disparava nenhuma sincronização por si só**. O
contador só era recalculado na PRÓXIMA vez que um evento NÃO relacionado
passasse por `_process_quest_events` — tipicamente o próximo kill —,
momento em que o Inventory já refletia AMBOS os itens looteados
enquanto isso, daí o salto "0 → 2".

**Fix**: `_handle_inventory_update` (`server/session.py`, handler do
`INV_SYNC` — disparado pelo cliente logo após todo loot) agora chama
`quest_logic.sync_collect_progress` diretamente, logo após
`sync_player_inventory` reconstruir o `Inventory` real do servidor. Se
mudou, envia `QUEST_UPDATE` na hora pra sessão. `_process_quest_events`
continua rodando a mesma sincronização (não regride nada — cobre
progresso derivado de OUTRAS mudanças de inventário, ex: craft, compra).

Validado: teste headless — quest "Presas Afiadas" ativa, INV_SYNC com 1
Presa de Lobo chamado ISOLADAMENTE (sem nenhum kill/evento no meio) →
progresso atualiza pra `[1]` NA HORA + `QUEST_UPDATE` enviado; segundo
INV_SYNC com 2 presas → progresso vai corretamente pra `[2]` (sem pular).
Suíte sem regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual em rede real — lootar itens de quest um a
um e conferir que o diário de quests atualiza a cada pickup, sem esperar a
próxima morte de mob.

**Regressão (feedback de testers, 18/07/2026)**: voltou — progresso de
"colete N itens" parou de atualizar no HUD/diário (entrega ainda
funcionava se o player tivesse os itens, só a EXIBIÇÃO travava).

**Causa raiz**: a reescrita do loot online pra granular/free-for-all de
grupo (17/07/2026, ver ARQUITETURA_ONLINE.md §34.24) trocou COMO o
cliente credita itens de loot — antes passava por
`ui/systems.py::LootSystem`/`_on_loot_collected`, que sempre disparava
`_on_loot_action("item")` (manda `INV_SYNC`) depois de creditar; a
reescrita passou a creditar DIRETO em
`client/network_handlers.py::_handle_msg_loot_result` (novo fluxo
LOOT_REQUEST/LOOT_RESULT), mas esqueceu de mandar o `INV_SYNC`
depois — só chamava `_send_save_state()` (persiste no banco, mas não
sincroniza o `Inventory` ECS AO VIVO do servidor, nem chama
`sync_player_inventory`/`sync_collect_progress`). Sem `INV_SYNC`, o fix
de 06/07 acima nunca dispara, e o fallback por tick também nunca vê o
item novo (mesmo `Inventory` do servidor, nunca atualizado). Entrega
ainda funcionava porque `spawn_player` reconstrói o `Inventory` do zero
a partir do banco em todo login/reconnect — um relog no meio do
caminho "resolvia" acidentalmente, mascarando o sintoma.

**Fix**: `_handle_msg_loot_result` agora chama `self._on_loot_action("item")`
(mesmo padrão de `_on_recarregar_changed`) sempre que `items` não é
vazio, ANTES de `_send_save_state()`. Ouro não precisa disso —
`server/loot_processor.py::request_loot` já credita o `Wallet` do
servidor DIRETO (server-autoritativo desde sempre), só item que
depende do cliente avisar de volta.

Validado: `tests/test_client_ui.py` — creditar item dispara
`_on_loot_action("item")`; só ouro ou resposta vazia não dispara (ouro
já é server-authoritative, não precisa). Suíte completa 228/228,
rodada 3x.

Não validado: sessão manual — lootar item de quest em grupo (fluxo
LOOT_REQUEST/LOOT_RESULT) atualiza o diário na hora, sem precisar
relogar.

**Follow-up (19/07/2026) — causa raiz mais funda, o fix acima não era
suficiente**: usuário testou (print: mochila com 8x "Presa de Lobo",
quest "Presas Afiadas" ainda em "Coletar Presa de Lobo (0/5)") — o
INV_SYNC agora dispara certinho (fix anterior), mas o progresso
CONTINUA sem contar. Sintoma relacionado: NPC da quest ("Levi Hawk")
parou de abrir o diálogo de interação, mesmo relogando várias vezes.

Causa raiz: `WorldServer.sanitize_inventory_payload`/`_reconstruct_item`
(item A4, round-trip de segurança contra item forjado) só reconheciam 3
catálogos autoritativos — `loot_tables._T`, `merchant_data.SHOPS`,
`crafting_data.RECIPES` — nunca `quests_data.QUEST_ITEMS`. "Presa de
Lobo" só existe em `QUEST_ITEMS` (drop condicional a quest ativa, ver
`engine/quest_logic.py`), então `_lookup_item_value("Presa de Lobo")`
sempre retornava `None` e `sanitize_inventory_payload` DESCARTAVA o
item em silêncio — o `Inventory` AO VIVO do servidor nunca chegava a
ter o item de verdade, então `sync_collect_progress` nunca tinha nada
pra contar, não importa quantas vezes o INV_SYNC disparasse certinho.

Esse gap é ANTIGO (o item A4/Tier B nunca cobriu `QUEST_ITEMS` desde
que foi criado) — só não aparecia antes porque, até a reescrita do loot
online desta sessão (Fase E), o item de quest nem passava por um
round-trip de servidor que importasse pra progresso de quest (o fix de
06/07 tinha `INV_SYNC` funcionando corretamente contra um Inventory que
ainda não tinha esse filtro; o filtro A4 veio depois e nunca foi
re-testado contra item de quest).

**"Levi Hawk" sem diálogo explicado pelo MESMO bug**: `ui/quest_system.py::
_open_dialog` só abre o painel visual se houver quest disponível OU
completável (`total = comp + avail`); se `total` estiver vazio (ex:
todas as quests do NPC já estão ativas, nenhuma completável), o
"diálogo" vira só uma mensagem no LOG de chat ("Continue sua missão.")
— nunca um modal. Como "Presas Afiadas" nunca ficava completável (bug
acima) e as outras quests de Levi Hawk já estavam aceitas/concluídas,
`total` ficava vazio TODA vez — não era um bug de clique/targeting no
NPC, era a interação silenciosamente virando só uma linha de log que o
usuário não notou.

**Fix**: `QUEST_ITEMS` virou o 4º catálogo autoritativo — adicionado em
`WorldServer._build_item_caches()` (`_item_value_cache`) e
`WorldServer._reconstruct_item()` (mesmo padrão dos outros 3: tenta a
factory, casa pelo nome, aplica bookkeeping seguro do cliente).

**Validado**: `tests/test_server.py::TestQuestItemInventorySync` (4
testes) — `_lookup_item_value("Presa de Lobo")` não é mais `None`;
`_reconstruct_item` monta o item certo (tipo/stack) pelo catálogo;
`sanitize_inventory_payload` não descarta mais o item; fluxo completo
(INV_SYNC → Inventory ao vivo → `sync_collect_progress`) avança o
objetivo da quest `wolf_fangs` corretamente. Suíte completa 243/243,
rodada 3x.

**Não validado**: sessão manual — com o servidor reiniciado, lootar
"Presa de Lobo" (ou qualquer outro item de `QUEST_ITEMS`) atualiza o
diário na hora; "Levi Hawk" volta a abrir o diálogo normalmente quando
há quest completável (ex: "Presas Afiadas" com 5+ presas na mochila).

---

### ✅ RESOLVIDO — Conexão caía logo após autenticar: "sent 1009 (message too big)" (feedback do usuário, 19/07/2026)

Reportado: login falhava em loop (conecta → autentica → desconecta →
reconecta) com o cliente logando `erro de conexão: sent 1009 (message
too big) frame exceeds limit of 1048576 bytes`. Servidor não crashava
nem logava erro — a sessão só nunca chegava a mandar `SELECT_CHARACTER`
(desconectava antes até da tela de escolha de personagem aparecer).

**Causa raiz**: `server/auth.py::_authenticate_sync` fazia
`SELECT * FROM characters WHERE account_id=? ... LIMIT 3` e mandava a
linha INTEIRA de cada personagem em `AUTH_OK.characters` — incluindo
`fog_json` (grid de fog-of-war, cresce sem limite conforme o mapa é
explorado, nunca compactado/podado) e os demais blobs JSON grandes
(`inventory_json`, `equipment_json`, `stats_json`, etc.), nenhum dos
quais a tela de seleção de personagem usa de verdade (só
`id`/`name`/`class_id`/`level` — `ui/char_creation_screen.py`;
`_handle_select_character`, ao escolher um personagem, busca ele de
novo por inteiro via `get_character()`, então o dado pesado em
`AUTH_OK` é só peso morto). Numa conta com 3 personagens bem
explorados/testados, a soma dos `fog_json` sozinha passou de 1 MB — o
limite do frame WebSocket (`server/main.py`, `max_size=1_048_576`),
derrubando a conexão com 1009 assim que o servidor tentava mandar o
`AUTH_OK`.

**Fix**: a query agora seleciona só as 4 colunas leves
(`id, name, class_id, level`) — nem chega a ler os blobs grandes do
banco pra essa mensagem.

Validado: `tests/test_session.py::
TestLogin::test_auth_ok_nao_manda_blobs_json_pesados` — infla
`fog_json` pra 600 KB no banco pra uma conta de teste, loga, confirma
que `AUTH_OK.characters` só tem as 4 chaves esperadas e o payload
inteiro fica bem abaixo de 100 KB. Suíte completa 229/229, rodada 3x.

**Não validado**: reiniciar o servidor (o processo em produção precisa
ser reiniciado pra pegar o fix — código Python não recarrega sozinho)
e confirmar que a conta afetada consegue logar normalmente.

---

### ✅ RESOLVIDO — Causa raiz do fog_json gigante: formato sem compressão (feedback do usuário, 19/07/2026)

O fix acima (trimar `AUTH_OK`) resolve o sintoma imediato (login), mas o
usuário apontou corretamente que o PROBLEMA de fundo — `fog_json`
(tiles explorados de Fog of War) crescendo sem limite — só ia piorar
com mapas maiores/mais explorados, eventualmente estourando 1 MB de
novo em outras mensagens (ex: o personagem escolhido sozinho no
`LOGIN_OK`/`SELECT_CHARACTER`).

**Causa raiz**: `_explored_maps` (por mapa, `set[(x,y)]`) era serializado
como `{mapa: [[x,y], [x,y], ...]}` — uma coordenada JSON crua por tile,
~11,4 bytes/tile, ZERO compressão. Medido numa conta real do usuário:
46.837 tiles explorados = 532 KB.

**Fix**: `shared/fog_codec.py` (novo, módulo puro sem estado — mesmo
espírito de `shared/messages.py`/`constants.py`) — `encode_fog`/
`decode_fog`. Área explorada é sempre um "borrão" contíguo (personagem
anda, não teleporta), perfeito pra bitmap: 1 bit por tile dentro do
bounding box do que foi explorado NAQUELE mapa (não do mapa inteiro —
não depende de saber as dimensões reais do mapa, fica compacto mesmo
cedo na exploração), comprimido com zlib e codificado em base64 pra
caber no JSON. `decode_fog` aceita os DOIS formatos (lista = antigo,
dict com `"bits"` = novo) por chave de mapa — migração transparente,
sem precisar converter o banco em massa: personagem salvo antes do fix
carrega normal, e vira formato novo no PRÓXIMO save.

Pontos ligados (os 3 lugares que liam/escreviam o formato antigo):
`client/save_sync_handlers.py` (`_collect_save_state` encoda,
`_restore_save_state` decoda), `server/session.py::_build_save_merge`
(decoda DB + cliente, faz union dos SETS — antes fazia union de listas
cruas —, re-encoda pro banco).

**Validado**: `tests/test_fog_codec.py` (7 testes) — roundtrip exato;
mapa vazio não entra no resultado; decode aceita formato antigo E novo,
decodificando igual pros mesmos dados; bitmap+zlib pelo menos 50x menor
que lista de coordenadas pra área contígua; vários mapas independentes.
`tests/test_session.py::TestBuildSaveMergeFog` (3 testes) — fog do
cliente sozinho vira formato novo; fog ANTIGO no banco + fog novo do
cliente fazem union correta (migração no meio do caminho); sem payload
do cliente mantém o fog do servidor. Rodado contra o dado REAL da conta
do usuário (`account_id=41`): 532.281 bytes → 1.176 bytes (**452x**
menor), roundtrip decode→encode confirmado idêntico ao original. Suíte
completa 239/239, rodada 3x.

**Não validado**: sessão manual — explorar o mapa normalmente, deslogar,
relogar e confirmar que a névoa já descoberta continua exatamente igual
(nenhum tile "esquecido"); personagem com fog salvo no formato ANTIGO
(antes deste fix) carrega e continua acumulando exploração sem erro.

---

### ✅ RESOLVIDO — Hotbar não escurecia por mana/concentração insuficiente (feedback de testers, 06/07/2026)

Reportado por testers: a skill deveria ficar escura quando não há recurso
suficiente pra usá-la.

**Causa raiz**: já existia um overlay de "recurso insuficiente" em
`client/hotbar_handlers.py::_draw_hotbar`, mas cobria **só rage**
(guerreiro) — `rage_cost > 0 and player_rage < rage_cost`. Mago (mana) e
arqueiro (concentração) nunca tinham NENHUM overlay equivalente — a skill
ficava sempre "acesa" e só o jogador descobria que faltava recurso ao
tentar usar e levar a rejeição do servidor.

**Fix**: overlay generalizado pra também checar `mana_cost`/`mana_cost_pct`
(resolvido como `max_mana × pct` quando aplicável) e
`concentration_cost` (lido de `skill.params`, já que não é campo de
primeira classe em `Skill` — arqueiro). Mantém o desconto por talento já
existente pra rage (`f"{skill.skill_id}_rage_cost"` em `CombatStats`);
mana/concentração usam o custo BASE do catálogo — skills com desconto de
talento específico (ex: Pyromania) podem escurecer um pouco antes da hora
em casos raros, mas isso é uma melhoria grande sobre NUNCA escurecer.

Validado: lógica extraída e testada isoladamente contra objetos reais
`Skill`/`CharacterStats`/`CombatStats` — 6 cenários (guerreiro c/ rage
insuficiente e suficiente; mago c/ mana insuficiente e suficiente; arqueiro
c/ concentração insuficiente e suficiente), todos corretos. Suíte sem
regressão (9F/83P, mesmo baseline).

**Não validado**: a própria renderização visual (`_draw_hotbar` depende de
superfície pygame + estado completo do `GameEngine` — fora do escopo de um
teste headless leve; só a lógica de decisão foi testada isoladamente).
Precisa de passada manual no cliente real conferindo que o slot escurece
visualmente ao ficar sem mana/concentração, além de rage.

---

### ✅ RESOLVIDO — Shop aberto + clique em outro NPC abria um segundo modal (feedback de testers, confirmado por teste manual, 06/07/2026)

Reportado por testers e CONFIRMADO por teste manual em cliente real: com a
loja aberta, clicar num NPC diferente (que estava na posição do clique)
abria o modal DELE também, simultaneamente.

**Causa raiz**: `game.py` chama `self._crafting_system.update()`,
`self._trainer_system.update()`, `self._shop_system.update()` e
`self._quest_dialog.update()` em sequência, todo frame. Já existia
filtragem de right-click ENTRE esses 4 sistemas **dentro do mesmo frame**
(`_right_click_consumed`, resolve qual NPC ganha quando há vários
adjacentes) — mas NENHUM deles verificava se um modal **de um frame
anterior** já estava aberto antes de processar um NOVO right-click capaz
de abrir a SI PRÓPRIO. Um clique numa NPC de tipo diferente (ex: trainer)
enquanto a loja já estava na tela era processado normalmente pelo
`TrainerSystem` (que não sabe nem se importa que a loja está aberta),
abrindo um segundo modal por cima.

**Fix**: no início do bloco (`game.py`), calcula
`_any_npc_modal_open` (`OR` de `is_open` dos 4 sistemas, ANTES de
qualquer `.update()` deste frame rodar) e uma função
`_strip_open_click(evs, already_open)` — se outro modal já está aberto E o
sistema perguntando NÃO é o que já está aberto, remove o `MOUSEBUTTONDOWN`
botão 3 dos eventos passados pro `.update()` daquele sistema. O sistema
JÁ aberto continua recebendo seus próprios eventos normalmente (senão
nunca reagiria a cliques internos/fechamento).

Validado: lógica extraída e testada isoladamente — (1) nenhum modal
aberto → clique passa normal; (2) **cenário exato do bug confirmado
manualmente** — shop aberto + clique num NPC diferente e fechado → clique
removido; (3) shop aberto processando A SI MESMO → clique preservado (não
quebra a própria interação da loja). Suíte sem regressão (9F/83P, mesmo
baseline).

**Não validado (pendente de nova passada manual)**: repetir o teste manual
que reproduziu o bug (loja aberta + clique num NPC diferente) confirmando
que agora só a loja permanece aberta.

---

### ✅ IMPLEMENTADO — Arqueiro ataca melee quando sem arco (ou com arma melee) equipado (07/07/2026)

Pedido do usuário: arqueiro sem arco (ou com arma melee equipada) deve
atacar corpo-a-corpo no auto-attack, igual ao mago já faz quando adjacente
(mago nunca teve auto-attack ranged; arqueiro tem, daí precisar da
diferenciação). Ataque melee de mago E arqueiro deve somar o skill_level da
arma melee equipada (mesma fórmula do auto-attack já implementada em
07/07/2026 — ver entrada de análise acima).

**Causa raiz de por que isso não funcionava**: `CombatStats.is_ranged` é um
flag **ESTÁTICO por classe** (`CLASS_MELEE_OVERRIDES["arqueiro"]["is_ranged"]
= True`, aplicado uma vez em `apply_char_stats_to_combat`) — nunca
reavaliado contra o equipamento real. Servidor (`combat_processor.py`) e
cliente (`_process_archer_combat`, `systems.py`) tratavam "é arqueiro" como
sinônimo de "está atacando à distância": sem arco válido, o SERVIDOR
simplesmente `continue`ava (pulava o auto-attack do player inteiro, todo
tick) e o CLIENTE só exibia "Precisa de um arco equipado" e parava — sem
NENHUMA opção de ataque.

**Fix — decisão dinâmica baseada na arma real equipada, não na classe**:
- `server/combat_processor.py::_process_player_attacks`: `_is_ranged_p`
  agora é `is_ranged (classe) AND tem_arco_equipado (agora)` — sem arco,
  cai direto pro caminho de dano MELEE (`deal_damage`) já usado por
  guerreiro/mago, em vez de pular o player. Arco equipado mas SEM
  aljava/flechas continua **sem atacar** (não vira melee — tem arma em
  mãos, só falta munição; fluxo esperado é "Use Recarregar", preservado
  como estava).
- `server/combat_processor.py::_process_pvp_attack`: mesmo critério dinâmico
  pro `attack_range` (7 só com arco de verdade; senão 1, igual PvE).
- `client/systems.py` (`PlayerInputSystem`): dispatcher agora só entra em
  `_process_archer_combat` (ranged) se `is_archer AND mainhand.subtype ==
  "Bow"` — caso contrário cai no MESMO branch `else` que guerreiro usa
  (chase até adjacente + `deal_damage("physical")`). `_add_rage`/
  `_increment_pnq_counter` chamados nesse branch são sempre no-op pra quem
  não é guerreiro (`_add_rage` é no-op online; `_increment_pnq_counter`
  exige `pnq_enabled`, talento exclusivo de Cavaleiro) — seguro reusar o
  branch inteiro sem duplicar lógica nem precisar "limpar" nada.
- Bônus de skill_level da arma melee: **automático, zero código novo** — já
  vem de graça da mudança de auto-attack desta mesma sessão
  (`ap_skill_mult` em `calculate_base_damage`, resolvido via
  `weapon_skill_level` a partir do que está REALMENTE equipado, sem
  checagem de classe). Mago já usava `deal_damage("physical")` no melee, e
  arqueiro agora usa o mesmo caminho — os dois ganham o bônus igual
  guerreiro.

Validado (headless, 5 cenários): (A) arqueiro desarmado adjacente a um mob
→ ataca melee (antes ficava mudo); (B) arqueiro com espada equipada →
dano escala com skill_level de Espada (120→320, skill 0→200, igual
guerreiro); (C) arqueiro com arco válido → **continua ranged sem
regressão** (56 de dano a 3 tiles); (D) arco equipado mas aljava vazia →
**continua sem atacar** (não veio melee por engano — comportamento
restritivo intencional preservado); (E) PvP com arqueiro desarmado → usa
range melee (1), não alcança vítima a 3 tiles (só alcançaria com arco de
verdade). Suíte sem regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual no cliente real — arqueiro desequipando o
arco e trocando por uma espada/machado/maça em combate, conferindo que o
auto-attack muda de flecha pra golpe corpo-a-corpo suavemente (sem travar a
perseguição) e que o feedback visual (animação/som) faz sentido pro golpe
melee de um arqueiro.

### ✅ IMPLEMENTADO — Follow-up: flecha/som de flecha sobrevivia ao melee fallback do arqueiro + aljava "100/0" (07/07/2026)

Teste manual do fix acima achou 3 problemas na camada de FEEDBACK (visual/
som) do cliente, que não tinha sido auditada junto com a mudança
server-side: (1) arqueiro sem arco atacando melee continuava **nascendo
flecha visual e tocando som de flecha/arco**; (2) dúvida do usuário se o
arqueiro RANGED também perseguia até ficar adjacente (não — ver abaixo);
(3) aljava mostrava "100/0" na HUD após Recarregar (deveria ser "100/100"),
e a capacidade precisava ser dinâmica por item (100/125/150...).

**Causa raiz #1 (flecha fantasma)**: `_resolve_archer_attack`
(`client/remote_entity_handlers.py`) decidia "é flecha" olhando **só
`class_id == "arqueiro"`** — nunca o equipamento real. Isso é uma classe de
bug arquitetural: o cliente **não tem visibilidade do inventário/equip de
players remotos** (só sabe `class_id` via `RemoteControlled`), então
reconstituir "está atirando ou batendo agora" a partir da classe sozinha é
estruturalmente impossível de acertar — só o SERVIDOR sabe qual caminho
(`_is_ranged_p`) foi usado neste golpe específico.

**Fix #1 — servidor manda o veredito, cliente para de adivinhar**:
- `server/combat_processor.py`: os dois `_combat_this_tick.append(...)` de
  auto-attack (PvE em `_process_player_attacks`, PvP em
  `_process_pvp_attack`) agora incluem `"is_ranged": _is_ranged_p` (PvE) /
  `_pvp_class_ranged and _pvp_has_bow` (PvP) — o mesmo booleano que já
  decidiu range/dano deste golpe, agora também viaja no COMBAT_RESULT.
- `client/remote_entity_handlers.py::_resolve_archer_attack`: para
  `source == "auto"`, usa `cr.get("is_ranged", True)` em vez de assumir
  True sempre que a classe é arqueiro (default True só cobre combat
  results antigos/antes deste campo existir — nunca acontece em produção
  pós-fix). Para skills (`source != "auto"`), mantém a lista
  `_ARROW_SKILL_IDS` (essas skills já exigem arco pra serem autorizadas).
  Efeito colateral automático: como a flecha deixa de nascer, o ataque cai
  no branch `else` de `_apply_combat_result` que já toca `hit_normal_*`
  (mesmo som que guerreiro/mago) — sem precisar duplicar lógica de som.

**Questão #2 (perseguição) — não era bug**: `_process_archer_combat`
(`systems.py:758`) só limpa a perseguição (`auto_move.path.clear()`)
quando `dist <= bow_range` (8 tiles) — nunca persegue até adjacente. Esse
código não foi tocado pela mudança de melee-fallback (que só altera o
dispatcher em `_process_archer_combat` vs o branch `else`, nunca o
comportamento INTERNO de `_process_archer_combat`). Confirmado por leitura
de código: arqueiro com arco válido continua parando no alcance do arco,
sem regressão.

**Causa raiz #3 (aljava "100/0")**: `_server_recarregar`
(`server/spell_completion_processor.py`) tem um fallback de auto-reparo
`if quiver.max_arrows == 0: quiver.max_arrows = 100` (cobre aljavas
legadas/salvas antes deste campo existir) — mas o `queue_stats_update(...)`
de confirmação só mandava `quiver_arrow_count`, nunca `quiver_max_arrows`.
A cópia LOCAL da aljava no cliente ficava travada em `max_arrows=0` pra
sempre (HUD faz `arrow_count/max_arrows` → "100/0"), mesmo com o servidor
já tendo reparado o valor.

**Fix #3**: `_server_recarregar` agora inclui `"quiver_max_arrows":
quiver.max_arrows` no payload; `client/network_handlers.py`
(`_handle_msg_stats_update`) aplica `payload.get("quiver_max_arrows")` na
aljava local no mesmo bloco que já sincroniza `arrow_count`/`subtype`.

**Capacidade dinâmica (item #4 do pedido) — já era arquitetura correta,
sem mudança necessária**: `merchant_data.py::_make_quiver(...,
max_arrows=100)` e `loot_tables.py` já definem `max_arrows` por item (o
fallback `==0 → 100` só cobre o caso legado, nunca sobrescreve uma aljava
de tier maior com valor já setado — ex.: uma aljava de 150 nunca é
rebaixada). `hud_handlers.py` já lê `_quiver.max_arrows` dinamicamente (sem
hardcode). O bug real era só a sincronização (#3) — o suporte a
100/125/150 etc. já funciona fim-a-fim assim que a aljava carrega o valor
certo e o cliente fica sabendo dele.

Validado (headless, 3 cenários, script descartável): (A) arqueiro
desarmado → todo combat_result de auto-attack vem com `is_ranged=False`;
(B) arqueiro com arco de verdade → todo combat_result vem com
`is_ranged=True` (nenhuma regressão no caminho ranged); (C)
`_server_recarregar` numa aljava legada (`max_arrows=0`) → STATS_UPDATE
sai com `quiver_max_arrows=100` (reparo agora chega ao cliente). Suíte sem
regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual no cliente real confirmando que (1) o
som/visual de melee do arqueiro sem arco soa idêntico ao guerreiro; (2) a
HUD mostra "100/100" (não "100/0") após Recarregar uma aljava antiga.

### ✅ CORRIGIDO — Arco COMPRADO NA LOJA nascia sem `subtype`, quebrando ranged na hora (07/07/2026)

Usuário testou o fix acima e reportou o oposto do esperado: arqueiro COM
arco+aljava genuinamente equipados, comprados na loja, também perseguia até
melee e atacava melee — imediato, todo clique, sem precisar relogar.
Investigação por leitura de código (dispatcher, sync de equip, save/load)
não achou nada — testes headless isolados confirmavam o dispatcher correto.
Causa só apareceu com log de diagnóstico temporário no cliente real.

**Causa raiz**: `WorldServer._item_data_from_obj` (`server/world_server.py`)
monta o payload `BUY_RESULT.item` a partir de uma lista curta e explícita de
campos (`attack_power, armor, spell_power, stamina, two_handed,
attack_speed, damage_min, damage_max`) — **sem `subtype` nem
`cast_range`**. `client/network_handlers.py::_handle_msg_buy_result`
reconstrói o item local via `_item_from_data` (SEM lookup em catálogo —
comentário do próprio método diz "usado para itens de loja que podem não
estar em loot_tables._T"), que só preenche os campos presentes no dict
recebido. Resultado: **todo item comprado numa loja nasce no cliente com
`subtype=""` e `cast_range=0`, desde o instante da compra** — quebra
`_archer_has_bow` (`subtype=="Bow"`) na mesma hora, sem precisar de
relogin. `cast_range` não quebrou nesse caso específico só porque "Arco
Curto" TAMBÉM existe em `loot_tables._T` com os mesmos stats — se o
personagem relogar, `_restore_item` acha o nome no catálogo de loot e
reaplica `cast_range=7` do factory; mas **`subtype` é sobrescrito de volta
pelo valor quebrado salvo** (`_restore_item` reaplica `d["subtype"]`
mesmo após achar candidato no catálogo — nunca confia só no factory pra
esse campo, por ser bookkeeping mutável em aljavas). Afeta QUALQUER arma
comprada em loja (guerreiro/mago também), não só arco — qualquer check de
`subtype` client-side (skill_level da arma, ícones, etc.) fica errado até
o item ser vendido/recomprado ou sofrer um ciclo save→load que bata com o
catálogo de loot.

**Fix**: `_item_data_from_obj` agora inclui `subtype` e `cast_range` na
lista de campos copiados do objeto Item pro payload do BUY_RESULT.
`client/save_sync_handlers.py::_item_from_data` (usado tanto por
`_handle_msg_buy_result` quanto como fallback de `_restore_item` pra itens
fora do catálogo de loot) ganhou `cast_range` na sua própria lista de
campos copiados — antes só existia no `_serialize_item` (o que salvava
certo mas nunca restaurava, gap já flagueado como "achado à parte, fora do
escopo" numa entrada anterior deste arquivo).

Validado (headless, script descartável): monta o objeto real da factory de
loja de "Arco Curto" (`merchant_data.SHOPS`), serializa via
`_item_data_from_obj` (confirma `subtype`/`cast_range` presentes no dict),
reconstrói via `_item_from_data` real do cliente (confirma
`subtype="Bow"`/`cast_range=7` no item final, batendo com o original).
Suíte sem regressão (9F/83P, mesmo baseline).

**Não validado**: passada manual comprando "Arco Curto" na loja (sem
relogar) e confirmando que o arqueiro ataca ranged imediatamente após
equipar.

---

### ✅ CORRIGIDO — Arqueiro com arco de alcance >7 "atira mas não acerta" (07/07/2026)

Usuário reportou: clicar num alvo faz o arqueiro andar até "parecer" estar
no alcance, parar, tocar som de disparo e descontar flecha da aljava — mas
o ataque nunca sai (sem dano, sem COMBAT_RESULT). O usuário já suspeitava
corretamente que o cálculo de distância deveria usar o `cast_range` do arco.

**Causa raiz**: `server/combat_processor.py` tinha `attack_range = 7 if
_is_ranged_p else 1` — **hardcoded**, ignorando o `cast_range` real do
arco equipado (mesmo bug em `_process_player_attacks` e
`_process_pvp_attack`). O cliente (`systems.py::_process_archer_combat`)
sempre usou o `cast_range` real do item (correto) — com "Arco Curto"
(cast_range=7) os dois valores coincidiam por acaso, mascarando o bug.
Qualquer arco melhor ("Arco do Caçador"=8, "Arco Élfico"=9) expunha:
cliente parava a 7-8 tiles (distância real do bow_range), servidor só
aceitava até 7 → todo golpe fora disso caía em `continue` silencioso.
Como o auto-attack ranged é "100% server-driven" (flecha/som só nascem ao
receber COMBAT_RESULT — ver entrada de arquitetura sobre isso), mas o
cliente decrementa `arrow_count`/cooldown **otimisticamente** sem esperar
confirmação (comentário "flecha nasce 100% server-driven... aqui só
avançamos cooldown local e a aljava"), o jogador via o ciclo de disparo
completo (som de nock, desconto de flecha) rodando pra sempre sem nunca
conectar.

**Fix**: `attack_range` agora usa `getattr(_bow_cp, "cast_range", 0) or 7`
(fallback 7 só se o item não tiver o campo) em vez do valor fixo, nos dois
pontos (PvE e PvP).

Validado (headless): arqueiro com Arco Élfico (cast_range=9) a 8 tiles do
alvo → antes 0 de dano (rejeitado), agora conecta normalmente; mesmo teste
em PvP. Suíte sem regressão (9F/83P, mesmo baseline).

### ✅ CORRIGIDO — Mago para de perseguir fora do alcance da skill que vai usar (07/07/2026)

Mesmo usuário, mesma sessão: clicar num alvo (ou apertar uma skill) faz o
mago perseguir e parar antes da distância realmente necessária pra lançar
a skill — servidor rejeita com "Fora de alcance" mesmo o personagem tendo
acabado de parar de andar em direção ao alvo. Usuário corretamente
identificou que, ao contrário do arqueiro, o alcance do mago não vem da
arma (Wand/Staff não tem `cast_range`) — vem da própria skill
(`skill_config.py`), já que mago não tem auto-attack ranged.

**Causa raiz**: `systems.py::_mage_attack_range` calculava o **MAIOR**
`cast_range` entre TODAS as skills da hotbar do mago, não da skill
específica que o jogador está prestes a usar. Ex.: hotbar com Bola de Fogo
(alcance 6) + Polimorfia (alcance 7) → perseguição genérica (clique
direito) parava a 7 tiles; apertar Bola de Fogo naquela distância falhava
("Fora de alcance") porque o check específico da skill em
`_use_skill_visual_only` (que usa corretamente `skill.cast_range` da skill
pressionada) exige ≤6. A perseguição genérica e o check da skill usavam
fontes de alcance diferentes (hotbar inteira vs. skill específica) —
sempre que a skill pressionada tinha alcance MENOR que a mais longa da
hotbar, dava esse falso "fora de alcance" logo após parar de perseguir.

**Fix (opção escolhida pelo usuário)**: `_mage_attack_range` agora calcula
o **MENOR** `cast_range` entre as skills ofensivas com alvo da hotbar
(ignora self-buff/utilitárias como Bloco de Gelo — `needs_target=False` —
e skills sem alcance definido, `cast_range<=0`). Ao parar de perseguir, o
mago está automaticamente dentro do alcance de QUALQUER skill da hotbar,
não só da mais longa. Efeito colateral aceito: o mago sempre se aproxima
até a distância da sua skill de alcance mais curto, mesmo pretendendo usar
só a de alcance maior.

Validado (headless): hotbar com Bola de Fogo(6)/Nova Congelante(3)/
Polimorfia(7)/Bloco de Gelo(self) → `_mage_attack_range` retorna 3 (antes
retornava 7); hotbar só com self-buff → fallback 5 (comportamento antigo
preservado quando não há skill ofensiva com alvo). Suíte sem regressão
(9F/83P, mesmo baseline).

**Não validado**: passada manual — mago com Bola de Fogo e Polimorfia na
hotbar, clicar num alvo distante e confirmar que a perseguição agora para
mais perto (na distância de Nova Congelante, se equipada) e as 3 skills
ofensivas lançam sem "Fora de alcance" assim que a perseguição termina.

---

### ✅ RESOLVIDO — Player remoto "congela" na tela (feedback de testers, 06/07/2026)

Reportado por testers em rede real (nunca reproduzido em localhost): em
alguns momentos, um player remoto para de atualizar posição na tela de
outro cliente — fica parado enquanto continua se movendo normalmente pro
resto do mundo.

**Causa raiz**: `Session.send()` (`server/session.py`) engolia QUALQUER
exceção com `except Exception: pass` — sem log, sem retry, sem sinalizar
falha ao chamador. Combinado com isso, `_build_update_for_session` MUTA
`session.known_eids` (marca a entidade como "o cliente já conhece") ANTES
de `_dispatch_tick_deltas` sequer tentar enviar o pacote. Se ESSE envio
específico falhar — rede real tem perdas/hiccups que localhost não tem, e
há ainda um risco de concorrência real: o handler de mensagens recebidas
(task própria por conexão) e o loop de ticks (task separada) podiam chamar
`ws.send()` na MESMA conexão ao mesmo tempo, e a lib `websockets` não é
segura pra `send()` concorrente — o servidor passa a acreditar que o
cliente já recebeu aquele ENTITY_SPAWN, mas o pacote nunca chegou. Dali em
diante, todo ENTITY_MOVE seguinte pra aquele eid é descartado no cliente em
silêncio (`eid not in self._remote_players`), porque a entidade local nunca
foi criada — desync permanente até a entidade sair e voltar do raio de
visão (ou até o cliente relogar).

**Fix** (`server/session.py`):
- `Session._send_lock` (`asyncio.Lock`) protegendo todo `ws.send()` —
  elimina o hazard de concorrência entre a task de mensagens recebidas e a
  task do tick loop.
- `Session.send()` agora retorna `bool` (sucesso/falha) e LOGA a exceção em
  vez de descartá-la silenciosamente.
- `_dispatch_tick_deltas`: se o envio do AOI_UPDATE falhar, `session.
  known_eids.clear()` — em vez de tentar desfazer cirurgicamente as
  mutações (entrelaçadas com o cálculo do payload dentro de
  `_build_update_for_session`), o próximo tick com envio bem-sucedido trata
  TODO mob/player no raio como "novo" (via sweep que já roda todo tick) e
  reenvia os ENTITY_SPAWN — resync automático, sem intervenção manual.

Validado: teste headless simulando falha de rede — `send()` retorna
True/False corretamente sem propagar exceção; duas chamadas concorrentes
de `send()` na mesma sessão não colidem (lock funciona); `known_eids` vai
de populado → limpo (na falha simulada) → repopulado automaticamente (no
próximo dispatch bem-sucedido). Suíte sem regressão (9F/83P, mesmo baseline
pré-existente desta sessão).

**Não validado**: passada manual com 2+ testers em rede real confirmando
que o congelamento não volta a ocorrer (o bug original só era reproduzível
em condições de rede real, não em localhost/dev).

---

### ✅ RESOLVIDO — Vitória Iminente não ganhava carga ao matar mobs (03/07/2026)

Reportado pelo usuário: matar mobs não concedia a carga da skill.

**Causa raiz**: mesma classe do bug antigo do Punho no Queixo. O bloco
on_kill do `server_death_handler` iterava só `ps.skills` (hotbar do
servidor) — mas skill comprada no treinador DURANTE a sessão só atualiza
`learned_skill_ids` (`sync_player_skills`); o objeto `Skill` nunca entra em
`ps.skills` até o relog. Killer sem o objeto → carga jamais concedida.
Confirmado headless: canal inteiro (grant → pending_xp com `on_kill_skill`
→ STATS_UPDATE forwarding total → handler do cliente) funciona quando o
objeto existe; falha silenciosa quando é só learned.

**Fix**: bloco on_kill itera o CATÁLOGO (`on_kill=="charge"`) com
lazy-create — se o objeto não está em `ps.skills` mas
`is_skill_authorized()` aprova (gate canônico), cria via `_make_skill` e
insere (mesmo padrão do PnQ em `combat_processor`). Cargas vêm de
`PlayerSkills._CHARGE_BASED`, não do catálogo.

Validado headless: (A) kill via golpe_poderoso com VI na hotbar → carga OK
(killer propagado por deal_damage também em skill kill); (B) VI apenas
learned, fora de ps.skills → lazy-create + carga OK (antes: FALHOU). Suíte
sem regressão (34F/57P/1S).

**Não validado**: manual — comprar VI no treinador e matar mob na mesma
sessão, sem relogar; conferir carga acendendo na hotbar.

---

### ✅ RESOLVIDO — Item de quest (Presa de Lobo) dropava no servidor mas sumia na janela de loot (03/07/2026)

Reportado pelo usuário: quest "Presas Afiadas" (wolf_fangs) ativa, matando
lobos — o item condicional nunca aparecia no loot.

**Causa raiz — CLIENTE**: o servidor rolava e incluía o item corretamente
(comprovado headless: `roll_conditional_loot` + corpse com "Presa de Lobo"
no `pending_loot_notifications`, owner-only como no WoW). Mas o handler de
`LOOT_AVAILABLE` (`client/network_handlers.py`) reconstrói os itens
serializados procurando o NOME apenas em `loot_tables._T` (106 itens de
loot normal) — itens de quest vivem em `quests_data.QUEST_ITEMS`, então
"Presa de Lobo" era DESCARTADA em silêncio na reconstrução e a janela de
loot abria sem ela. Toda a cadeia seguinte (pegar item → progresso
collect_item) nunca começava.

**Fix**: fallback na reconstrução — nome não achado em `_T` → tenta
`QUEST_ITEMS.get(nome)`. O caminho de restore de save
(`save_sync_handlers._restore_item`) já tinha fallback genérico
(`_item_from_data`) e não era afetado — item de quest na bag sobrevive
relog normalmente.

**Bug irmão achado na investigação (`quests_data.py`)**: `first_blood`
tinha `requires=("prova_valor")` SEM vírgula — string, não tupla. O check
`all(r in ql.completed for r in requires)` iterava letra por letra
('p','r','o'...) e nunca passava → quest impossível de iniciar. Corrigido
pra `("prova_valor",)`.

Validado: headless — quest ativa via `try_start`, Lobo real morto por
auto-attack, corpse com "Presa de Lobo" (roll forçado); reconstrução
client-side testada isolada: "Presa de Lobo" agora reconstrói, item normal
(Colete de Couro) continua ok. Suíte sem regressão (34F/57P/1S).

**Não validado**: passada manual (aceitar Presas Afiadas, matar lobos até
dropar — chance 0.6 —, ver item na janela, pegar e conferir progresso da
quest subindo; conferir que player SEM a quest não vê a presa).

---

### ✅ RESOLVIDO — Morrer no meio de um cast deixava TODAS as skills mudas após reviver (03/07/2026)

Reportado pelo usuário: mago usou Nova Congelante várias vezes (ok), morreu
e reviveu — depois disso, cercado de inimigos, a skill "não achava alvo"
em todas as tentativas.

**Causa raiz**: `is_casting` preso em True. Cadeia exata:
1. skill_processor aceita cast com cast_time → seta `combat_state.is_casting
   = True` (espelha offline);
2. player MORRE no meio do cast → `_handle_player_death` purga a entry de
   `_pending_spell_completions` do player (fix antigo das "flechas
   fantasma") — mas os ÚNICOS pontos que liberam `is_casting` são a
   conclusão do cast (`_process_spell_cast_completions`) e o
   `_handle_cancel_cast`. Entry purgada = nenhum dos dois roda → flag preso;
3. após reviver, `can_act()` (que exige `not is_casting`) fica False pra
   sempre → o gate do skill_processor DESCARTA todo CAST_SKILL **em
   silêncio** (`continue`, sem SKILL_RESULT) e o auto-attack também fica
   bloqueado (`_process_player_attacks` usa o mesmo `can_act`).

A mensagem que o usuário viu ("nenhum alvo próximo") era o feedback
COSMÉTICO local do cliente (`spell_system._apply_nova_congelante` — "Nova
Congelante — nenhum inimigo no raio"), que roda o cast visual por conta
própria; o servidor nem processava o request. Reproduzido headless: morte
no tick 2 de um cast de 1s → `is_casting=True pendings=0` → pós-revive
`can_act=False`, cast retorna dmg=0 e results=[].

**Fix (`_handle_player_death`)**: ao purgar os casts pendentes do morto,
(a) seta `combat_state.is_casting = False` e (b) desarma
`_skill_last_used` dos sids purgados (cast que nunca completou não queima
CD — mesma regra do CANCEL_CAST).

Validado: repro headless — morrer no meio do cast, reviver, castar de novo:
dano + root aplicados normalmente, completion com CD 6.0s. Suíte sem
regressão (34F/57P/1S).

**Não validado**: passada manual (morrer castando, reviver, usar skill).
Observação de robustez futura: os gates do skill_processor (`can_act`,
`is_action_locked`, GhostState) descartam requests SEM SKILL_RESULT — o
cliente só se recupera pelo timeout de 0.4s e não recebe motivo; se novos
"skills mudas" aparecerem, vale emitir failed=True com reason nesses gates.

---

### ✅ RESOLVIDO — Skills com cast_time (arqueiro/mago) SEM cooldown nenhum (03/07/2026)

Reportado pelo usuário: skills do arqueiro spammáveis, sem cooldown na
hotbar. Afetava TODA skill com cast_time (arqueiro inteiro + magias do
mago); instantâneas (guerreiro) intactas.

**Causa raiz (server)**: handlers de skill com cast retornam True no INÍCIO
do cast sem setar `skill.current_cooldown` (o CD real viaja na entry de
`_pending_spell_completions`, campo "cooldown"). O registro de CD do
skill_processor fazia `_eff_cd_store = getattr(skill_obj,
"current_cooldown", _sk_cd)` — o atributo EXISTE (default 0.0), então o
fallback `_sk_cd` nunca era usado → `_skill_effective_cd[key] = 0.0`. Na
validação do uso seguinte, `_sk_cd = 0.0` → gate `if _sk_cd > 0` nunca
bloqueia → skill sem CD server-side PARA SEMPRE (e o registro nunca se
corrige, porque também é condicionado a `_sk_cd > 0`).

**Por que "apareceu agora" (client)**: o SKILL_RESULT de completion passou
recentemente a enviar `cooldown = _skill_effective_cd.get(key)` (fix da
rejeição falsa do Interceptar com talento) — antes enviava None e o cliente
caía no CD BASE do catálogo, mascarando o buraco visualmente. Com a mudança,
o cliente passou a receber 0.0 e a hotbar ficou sem CD — expondo o que o
servidor já não validava.

**Fix (skill_processor)**: registro de CD com fallback em cadeia —
`current_cooldown` do handler (instantâneas com redução de talento, ex:
Interceptar) → efetivo do uso anterior (`_sk_cd`) → CD base do catálogo.
Registro não é mais condicionado ao `_sk_cd` stale (auto-corrige estado
envenenado).

**Consequência tratada — cancel de cast**: com o registro no início do cast
valendo de verdade, cancelar por movimento deixaria o CD rodando SÓ no
servidor (cliente sem CD → rejeição falsa no recast). `_handle_cancel_cast`
(session.py) agora desarma `_skill_last_used` do sid cancelado — MAS só se o
cast estava genuinamente pendente (cancel que chega após a conclusão, race,
não apaga CD legítimo).

Validado: diagnóstico headless — 1º tiro_repulsivo completa com
`cooldown: 45.0` na completion (cliente aplica CD real); 2º cast imediato
REJEITADO server-side (restante 44.7s). Suíte sem regressão (34F/57P/1S).

**Não validado**: passada manual (arqueiro: CD aparece na hotbar após o
cast; cancelar cast por movimento não gera rejeição no recast).

---

### ✅ RESOLVIDO — Interceptar "bloqueado" em terreno aberto / Tiro Repulsivo stunando sem colisão real (03/07/2026)

Reportado pelo usuário: Interceptar mostrava "Bloqueado" sem nenhum
obstáculo no caminho; Tiro Repulsivo aplicava o stun de colisão sem o alvo
ter batido em parede/árvore/pedra/mob. NÃO é a regressão antiga do
`_resolve_target` (entrada anterior nesta seção) — é multi-map.

**Causa raiz (multi-map/P4)**: com `_MapBundle` por mapa, os serviços
tile_validation/pathfinding passaram a ser injetados DIRETO nos sistemas de
cada bundle (`_load_map_for`), e o `_svc` GLOBAL de `world_systems` ficou
apontando pro ÚLTIMO mapa carregado no startup (`map_cave_east`). Os
handlers compartilhados de skill (`skill_handlers.py`/`spell_system.py`)
chamam `is_tile_walkable`/`get_tilemap`/`find_path` de MÓDULO — que resolvem
via `_svc`. Resultado: player em `map_1` tinha o dash validado contra a
matriz da CAVERNA (maciça de rocha) → "Caminho bloqueado"/"Sem espaço ao
redor do alvo" em terreno 100% aberto. Reproduzido em diagnóstico:
`_svc['tile_validation']` apontava pro tilemap_entity=53 (cave_east) com o
player no map_1.

**Segunda instância da mesma classe**: `_server_tiro_repulsivo`
(spell_completion_processor) pegava "o primeiro" componente `Tilemap` do
world inteiro (`get_entities_with(Tilemap)` + break) — com 3 mapas
carregados, valida colisão de knockback contra o mapa errado → stun em
parede fantasma. Mesma classe já tinha sido corrigida PONTUALMENTE em
`move_player()` (comentário lá descrevia o bug de cave_west rejeitando
movimento em map_1) — mas não foi aplicada aos caminhos de skill.

**Fix — ponto único `WorldServer.register_map_services_for(eid)`**:
re-registra `_svc` com o bundle do mapa da entidade (via
`get_entity_map`/`MapLocation`, fonte única entity→mapa do P3). Chamado em:
- `skill_processor._process_skill_requests` — antes de cada request
- `spell_completion_processor._process_spell_cast_completions` — por entry
- `spell_completion_processor._apply_spell_on_projectile_hit` — antes do
  handler do hit (Tiro Repulsivo/Bola de Fogo/etc.)
- `_server_tiro_repulsivo` além disso usa o Tilemap do bundle do mapa do
  ALVO (fallback: primeiro Tilemap, mundo single-map/testes)

REGRA: todo entry point novo do servidor que executar handler compartilhado
em nome de um player deve chamar `register_map_services_for(player_eid)`
antes. `server/mob_system.py` também tem o padrão "primeiro Tilemap", mas é
código morto (não importado por ninguém) — não tratado.

Fixtures atualizadas: `tests/diag_interceptar.py` e
`tests/diag_tiro_repulsivo.py` não chamavam `tests.helpers.authorize_skill`
(pré-datavam o gate de autorização) e o caso de sucesso do interceptar usava
mob a 2 tiles (64px < `INTERCEPT_MIN_RANGE_PX=74`, pré-datava o min range em
píxeis) — corrigidos.

Validado: repro headless (guerreiro map_1, mob 3 tiles, terreno aberto
confirmado tile a tile) — antes: `failed=True "Sem espaço ao redor do
alvo"`; depois: dash completa (130→132, cooldown aplicado).
`diag_interceptar` (parede REAL continua bloqueando + rejected=True; caminho
livre move com is_dash) e `diag_tiro_repulsivo` (fluxo completo: cast →
voo → hit com dano + colisão contra parede real do map_1) passam. Suíte sem
regressão (34F/57P/1S idêntico ao baseline).

**Não validado**: passada manual num cliente real (Interceptar em terreno
aberto no map_1 e dentro das cavernas; Tiro Repulsivo só stunando em
colisão verdadeira).

---

### ✅ RESOLVIDO — Rage: hotbar acendia mas skill voltava "Raiva insuficiente" (03/07/2026)

Reportado pelo usuário: HUD do Guerreiro mostrava rage suficiente e o slot
da skill acendia, mas o `CAST_SKILL` voltava "Raiva insuficiente" e a barra
"corrigia" pra baixo em seguida.

**Causa raiz**: rage tinha DOIS produtores independentes, cada um com seu
relógio. Cliente: +5 por auto-attack disparado pelo timer LOCAL
(`PlayerInputSystem._add_rage`) + decay local. Servidor: +5 pelo timer
PRÓPRIO (`combat_processor._attack_timers`) + decay próprio. Os timers nunca
disparam em sincronia (offset de início, latência, visões diferentes de
"posso atacar") → cliente ficava +5/+10 à frente. O servidor só empurrava
rage pro cliente APÓS tentativa de skill (`skill_processor`) — entre skills
o drift só crescia. Hotbar/checagem de keypress usavam o valor local
inflado; a validação do handler usava o real. Reconciliação só no erro =
sintoma clássico. Complemento desde o fix da mana (entrada acima): o
servidor deixou de confiar no rage do `CAST_SKILL`, então o valor local do
cliente virou pura ilusão de HUD.

**Agravante PvP**: `_process_pvp_attack` NÃO gerava rage nenhuma no servidor
— guerreiro em PvP enchia a barra localmente mas toda skill com custo era
rejeitada.

**Fix** (mesmo modelo do regen de mana — servidor único produtor + push a
cada mudança):
- `core_systems._tick_rage_decay` retorna `(old, new)`;
  `ServerCombatStateSystem` coleta `rage_events` → `_tick()` empurra
  `STATS_UPDATE {rage}` (igual `mana_events`)
- `combat_processor.py`: ganho +5 (PvE) faz push imediato via
  `queue_stats_update`; PvP ganhou o MESMO ganho +5 com push (antes: zero)
- Cliente: `PlayerInputSystem._add_rage` no-op com `_net`;
  `CombatStateSystem` (world_systems) pula `_tick_rage_decay` com `_net`
  (senão decairia em dobro entre pushes); `game.py` injeta `_net` no
  `_combat_state_sys`
- `CharacterStats.rage` no cliente online é só display — alimentado por
  `STATS_UPDATE` (handler já existia em `network_handlers.py`)

Referência de design (jogos consolidados, modelo WoW): recurso 100%
server-side, push a cada mudança, UI acende com valor real defasado só pela
latência; predição opcional de custo com rollback — nunca "reconciliação só
quando dá erro".

Validado: diagnóstico headless (spawn guerreiro + mob real, 100 ticks) —
ganho 0→10 com 2 pushes `{rage:5}`/`{rage:10}`; decay 10→0 fora de combate
com pushes `{rage:5}`/`{rage:0}`. Suíte sem regressão (34F/57P/1S idêntico
ao baseline pré-mudança; falhas pré-existentes).

**Não validado**: passada manual com 2 clientes reais (PvE e PvP) conferindo
que o slot só acende com rage real e que a mensagem "Raiva insuficiente"
não aparece mais com a barra cheia. Concentração (Arqueiro) ainda tem regen
duplicado cliente+servidor (drift pequeno, taxa determinística) — mesma
classe de problema em escala menor, não tratado aqui.

---

### ✅ RESOLVIDO — Skill Level: painel (tecla L) nunca atualizava sem relog

Reportado pelo usuário durante teste manual: matar mobs com o arqueiro pra
subir skill level de Arco não fazia o xp exibido no painel mudar.

**Causa raiz**: o componente `SkillLevels` do CLIENTE é populado uma única
vez, no login (`client/save_sync_handlers.py::_restore_save_state`, lendo
`char_data["skill_levels_json"]`) — exatamente como o plano original previa
("só pra exibição"). O SERVIDOR concedia xp corretamente (confirmado por
diagnóstico isolado — `grant_skill_xp` é chamado e funciona), mas não havia
NENHUM canal de broadcast pra propagar essas mudanças de volta ao cliente
durante a sessão. O painel mostrava sempre o snapshot do último login,
congelado — não era um bug de cálculo, era a ausência de um mecanismo de
sync ao vivo (peça que o plano de implementação original não cobriu).

**Fix**: mesmo padrão de `WorldServer._sync_player_hp_dirty()` (dirty-check
por tick, cobre qualquer fonte de mudança sem precisar de código por
feature): novo `_sync_player_skill_levels_dirty()` compara
`SkillLevels.levels`/`xp` contra um cache por-tick e, se mudou, enfileira um
snapshot completo em `_skill_levels_broadcasts_this_tick`. Novo
`SKILL_LEVELS_UPDATE` (S→C, ver `shared/messages.py`) — enviado **só ao
dono** (nunca broadcast AOI, é dado privado), despachado em
`server/session.py::_dispatch_tick_deltas` logo após o bloco de HP
broadcasts. Cliente (`client/network_handlers.py::_handle_msg_skill_levels_update`)
substitui (replace, não soma) `levels`/`xp` no componente local — mesma
regra de sempre: nunca usado em cálculo de dano client-side, só exibição.

Validado com diagnóstico isolado simulando 3 ticks consecutivos (sem
mudança → 0 mensagens; após `grant_skill_xp` → exatamente 1 mensagem com o
snapshot correto; tick seguinte sem nova mudança → 0 mensagens de novo).
Compile limpo, suíte sem regressão (32/49/1).

---

### ⚪ DESCARTADO (usuário não reproduziu de novo) — Arqueiro: aljava desconta flechas sem projétil/dano após recarregar em combate

Reportado pelo usuário durante o mesmo teste: depois que a aljava esvazia e
o arqueiro usa Recarregar ainda em combate, ao reabastecer a aljava volta a
descontar flechas ao atirar, mas nenhum projétil aparece e nenhum dano é
causado.

**Investigação**: reproduzido em diagnóstico isolado o cenário exato
(aljava vazia em combate → cast de Recarregar com `cast_time=1.8s` → aljava
reabastecida → continua perseguindo o mesmo alvo) — em todas as execuções,
o auto-attack do SERVIDOR retomou corretamente após o cast completar
(`is_casting` liberado, `_server_apply_ranged_physical` disparou no próximo
ciclo de cooldown, consumiu flecha real e aplicou dano real). Não foi
possível reproduzir um estado "travado permanentemente" só com simulação
server-side isolada.

**Hipótese mais provável (não confirmada)**: dessincronia client/server na
transição cast→auto-attack. O contador de flechas que o jogador VÊ na HUD é
uma PREDIÇÃO COSMÉTICA local (`systems.py` linha ~1822, comentário "Online:
flecha 100% server-driven — nasce em `_apply_combat_result`...") — o cliente
decrementa seu próprio `quiver.arrow_count` e avança seu próprio
`attack_cooldown_timer` LOCALMENTE, a cada vez que SEU PRÓPRIO timer/ammo
permitem, independente de o servidor ter de fato confirmado aquele tiro via
`COMBAT_RESULT`. Se o timer/estado de cast do cliente diverge do servidor
especificamente na janela em que um cast termina (ex: cliente libera
"can fire" antes/depois do servidor), o jogador veria a munição cair sem o
projétil/dano correspondente nascer (que só nasce ao chegar um
`COMBAT_RESULT` real do servidor).

**Instrumentação temporária deixada em `server/combat_processor.py`**
(`_process_player_attacks`, prefixo `DBG_RELOAD:`) — imprime o motivo de
cada `continue` no branch ranged (can_act/is_pursuing/bow/quiver/range) e
confirma cada tiro disparado (`FIRE ranged ... arrows_before/after`).
**Próximo passo**: reproduzir o bug com um cliente real enquanto o servidor
roda com essa instrumentação, e comparar o que o servidor realmente fez
(linhas `DBG_RELOAD:`) contra o que o cliente mostrou na tela.

**Status**: usuário não conseguiu reproduzir de novo em teste subsequente e
concluiu que não era um bug real (provavelmente percepção de um delay
normal de cooldown na hora do reload, não um estado travado). Instrumentação
`DBG_RELOAD:` removida de `server/combat_processor.py`. Junto, removidos
também debugs pré-existentes não relacionados (`DBG_MOB`, em `systems.py` e
`client/remote_entity_handlers.py` — prints de cada passo de pathfinding de
mob CHASING/RETURNING, sobrando de uma sessão de debug anterior).

---

### ⚪ NÃO É BUG — Taxa de acerto do Arqueiro "sempre acertando"

Reportado pelo usuário: auto-attacks do arqueiro acertando quase sempre com
"pouco mais de 50% de acerto". Debug temporário (`DBG_ACERTO:`, removido após
diagnóstico) confirmou com dados reais: `acerto=54.0`, mas só 1 miss em 17
tiros (≈6%, esperado ≈46%).

**Causa raiz (não é bug)**: talento `bardo_calmo_certeiro` ("Calmo e
Certeiro", build Bardo/Arqueiro) — `talent_data.py`. Descrição do próprio
talento: *"Enquanto estiver parado, cada segundo concede +{v}% de taxa de
acerto (acumula **sem limite de tempo**, até 100% total)"*. `formula: lambda
pts: float(pts)` = +1%/ponto/segundo, `max_points=5` → até +5%/s. Com 5
pontos alocados, ~9 segundos parado já basta pra saturar `_acerto` em 100%
(`core_systems.py::_tick_standing_seconds` acumula `standing_seconds` sem
cap enquanto `acerto_per_standing_second > 0`; `damage_calculator.py` soma
`_standing_rate * _standing_secs` direto no `_acerto` do roll, sem cap
próprio — só o `min(100.0, ...)` final). Como o Arqueiro fica parado por
natureza enquanto atira a distância, esse talento (se alocado) domina o
combate quase instantaneamente — comportamento **documentado e intencional**
do talento, não introduzido nesta sessão. Se o balanceamento for considerado
forte demais, o ajuste é no talento (ex: cap de tempo, ou %/s menor), não no
código de resolução de combate.

---

### ✅ RESOLVIDO — Tiro Repulsivo: mob "voltava" no dash bem no instante do stun

Regressão relatada pelo usuário no fix anterior desta sessão (ver entrada
"DESCARTADO" acima sobre Recarregar — esta é uma SKILL diferente, Tiro
Repulsivo, achado real). O fix anterior (represar a IA em `AGGRO_DELAY`
durante `_duration`) cobria os dois ramos (`stunned` e livre), mas o
usuário observou o bug acontecer **justo quando o alvo era stunado** —
contradição aparente com "só acontecia sem stun" que eu tinha avaliado mal.

**Causa raiz exata**: quando o empurrão colide (`stunned=True`),
`_pending_knockback_landings` usa a MESMA `_duration` pra decidir quando
`_process_knockback_landings` aplica o efeito "stun" de fato. Esse timer e o
`aggro_delay` da IA decrementavam em **lockstep** (mesmo valor inicial, mesmo
`dt`) — cruzando zero no MESMO tick. Mas `EnemyAISystem.update()` roda ANTES
de `_process_knockback_landings` dentro de `WorldServer._tick()` (ver ordem:
`_systems.update` primeiro, `_process_knockback_landings` bem depois). Nesse
tick exato: a IA destravava (`AGGRO_DELAY`→`CHASING`, possível 1 passo de
movimento) ANTES do stun realmente aterrissar — um "pulo" de 1 tick bem no
instante em que o usuário via o stun começar.

**Fix**: margem de segurança (`_KB_AI_FREEZE_MARGIN = 0.1s`) somada à
`_duration` ao represar `aggro_delay` — sem alterar o `_duration` usado pelo
broadcast visual nem pelo timer de `_pending_knockback_landings`. Com a
margem, o stun sempre aterrissa (e `EnemyAISystem` já bloqueia movimento com
`_sfx.has("stun")`) antes do `AGGRO_DELAY` expirar.

Validado com diagnóstico isolado: mob com path antigo + aggro por dano,
forçado a colidir (resultando em `stunned=True`); confirmado congelado
durante toda a janela do dash, depois congelado pelo stun real (3s default),
e só retoma `CHASING`/`ATTACKING` após o stun expirar — sem nenhum pulo no
meio do caminho. Compile limpo, suíte sem regressão (32/49/1).

---

### 🔴 CRÍTICO (RESOLVIDO) — `TileMovement.is_moving` de PLAYER nunca era True no servidor — quebrava reset de "Calmo e Certeiro" e regen de Concentração

Veio à tona pela entrada anterior ("Calmo e Certeiro" saturando acerto em
100%) — o usuário corrigiu minha conclusão: o talento em si está certo
("acumula sem limite, até 100%"), mas o RESET ao mover (parte central do
design — "se ele se mover, reseta o bônus") nunca acontecia de verdade no
servidor, então mesmo kitando ativamente o bônus saturava.

**Causa raiz**: `WorldServer.move_player()` (chamado a cada tile que o
cliente confirma) faz um SNAP instantâneo —
`tm.current_tile_x = tm.target_tile_x = tx` — e nunca seta
`tm.is_moving = True`. Diferente de mobs (que têm tween real via
`TileMovementSystem`/`start_tile_movement` rodando no servidor), o servidor
nunca tween-a o movimento do player — só valida e aplica o tile final. Logo
`TileMovement.is_moving` de qualquer player, no servidor, fica em `False`
**permanentemente**, não importa quanto o player se mova.

Duas mecânicas server-side dependem desse campo pra diferenciar "parado" de
"andando":
- `_tick_standing_seconds` (Calmo e Certeiro) — `if tm.is_moving:
  standing_seconds = 0.0` nunca disparava → acumulava sem nunca resetar,
  mesmo kitando sem parar.
- `_tick_concentration_regen` (Arqueiro) — `moving = tm.is_moving` sempre
  `False` → sempre usava `concentration_regen_idle`, nunca
  `concentration_regen_moving`, mesmo com o player andando o tempo todo.

**Fix**: `move_player()` agora seta `tm.is_moving = True` +
`tm._server_move_grace = PLAYER_MOVE_GRACE_S` (0.4s, > intervalo real entre
moves consecutivos andando contínuo, ~0.29s a `PLAYER_SPEED`/`TILE_SIZE`) a
cada move aceito (os dois ramos: normal e ghost). Novo
`ServerCombatStateSystem._tick_player_move_grace(tm, dt)` (chamado a cada
tick, antes de `_tick_standing_seconds`/`_tick_concentration_regen`) decai
essa janela e desliga `is_moving` quando expira — infere "ainda andando" a
partir da RECORRÊNCIA de moves aceitos, já que não há tween real a observar.
Novo campo `TileMovement._server_move_grace` (mesmo padrão dos já existentes
`_server_dir_x/_server_aoe_x` — bookkeeping interno do servidor, não usado
pelo cliente). Fix é estritamente server-side: a lógica do CLIENTE pra sua
própria entidade já tinha tween real (`is_moving` sempre correto lá);
`_tick_player_move_grace` só existe em `ServerCombatStateSystem`, não na
`BaseCombatStateSystem` compartilhada — não afeta o cliente/offline.

Validado com diagnóstico isolado usando `move_player()` de verdade (não
forjando `is_moving` manualmente): parado 3s → acumula 3.0s reais; 1 move
real → `is_moving=True` + reset imediato de `standing_seconds`; kiting
simulado (move a cada 0.3s, 8.5s totais) → pico de só 0.2s acumulado (antes
do fix, teria crescido sem parar); parar de mover por 5s → volta a acumular
normalmente. Compile limpo, suíte sem regressão (32/49/1).

---

### ✅ RESOLVIDO — Quests `use_skill` nunca progrediam no modo online + novos objetivos `learn_skill`/`use_skill(on_dummy)`

Contexto: implementação da quest "Iniciação Arcana" (mago) — treinar Bola de
Fogo de graça no treinador + usá-la 5x num boneco de treino. Trainer agora
gate por nível+ouro pras 4 skills do mago (`bola_de_fogo` lvl1/grátis,
`polimorfia` lvl2/100g, `nova_congelante` lvl4/200g, `bloco_de_gelo`
lvl8/400g) — `INITIAL_SKILLS_BY_CLASS["mago"]` zerado (antes dava as 4 de
graça na criação, sem passar pelo treinador).

**Bug real encontrado de carona**: `quest_fire("use_skill", ...)` só existia
em `_use_skill()` (systems.py) — o caminho **OFFLINE** de execução de skill.
O caminho **ONLINE** (`_use_skill_visual_only()`, usado sempre que
`self._server_authoritative=True`) nunca disparava esse evento. Resultado:
`warrior_trial`/`executioner` (quests "use_skill" já existentes) **nunca
progrediam de verdade no modo online** — só funcionavam no offline. Não
percebido antes porque nenhuma quest desse tipo tinha sido testada online.
Fix: `quest_fire("use_skill", ...)` adicionado também em
`_use_skill_visual_only()`, mesmo ponto (fim da função, após enviar
`CAST_SKILL`).

**Novos objetivos** (extensão do sistema, ver docstring de
`quests_data.py`):
- `learn_skill` — completa ao aprender (treinar) uma skill no treinador;
  dispara em `trainer_system.py::_do_learn` após `learned_skill_ids.add(...)`
  (só skills compradas — skills iniciais de `INITIAL_SKILLS_BY_CLASS` nunca
  disparam esse evento, não passam por `_do_learn`).
- `use_skill` ganhou `params={"on_dummy": True}` opcional — exige que o alvo
  da skill tenha o componente `TrainingDummy` no momento do uso (calculado
  em `_use_skill`/`_use_skill_visual_only` a partir de
  `combat_state.target_entity_id`). Sem o param, comportamento idêntico ao
  anterior (qualquer alvo conta — `warrior_trial`/`executioner` inalterados).

Confirmado que `QuestDef.objectives`/`QuestSystem` já suportavam múltiplos
objetivos por quest desde sempre (`tuple[ObjectiveDef]` +
`all(prog[i] >= obj.count ...)` na conclusão) — só nunca tinha sido
exercitado com mais de 1 objetivo em nenhuma quest até "Iniciação Arcana".

Quest registrada na treinadora do mago (Selene Vail,
`maps/map_1_entities.json`, já tem bonecos de treino a poucos tiles de
distância). Validado com diagnóstico isolado: progresso por objetivo
correto (skill errada não progride; uso fora do boneco não conta pro
objetivo 2; completa só com os dois objetivos satisfeitos); treinador
aprende Bola de Fogo de graça no nível 1 mesmo com 0 de ouro; bloqueia
Polimorfia sem nível, libera com nível+ouro corretos. Compile limpo, suíte
sem regressão (32/49/1).

---

### ✅ RESOLVIDO — Boneco de treino aparecia como "Humanoide" e objetivo `use_skill(on_dummy)` nunca progredia online

Causa raiz mais profunda do que parecia: `entity_factory.create_training_dummy()`
**já** tinha `EntityIdentity` correta no servidor. O bug real estava em
`WorldServer._build_mob_spawn_payload(eid, tm)` — única função que monta o
payload de spawn de mob enviado ao cliente (WORLD_STATE/ENTITY_SPAWN/
AOI_UPDATE), com fallback hardcoded `race="Humanoide"; entity_class="Warrior"`
só sobrescrito quando a entidade tinha `SpawnZoneOwner` → `SpawnZone`. Boneco
de treino não tem zona de spawn, então caía sempre no fallback errado — **e o
protocolo nunca tinha campo nenhum pra sinalizar "isto é um TrainingDummy"**,
então mesmo corrigindo só o nome/raça, a entidade-proxy local do cliente
jamais ganharia o componente `TrainingDummy`, e o objetivo de quest
`use_skill(on_dummy=True)` nunca completaria online (client-autoritativo,
ver seção de quests acima).

Fix em duas pontas:
- `_build_mob_spawn_payload`: novo `elif ident:` (usa `EntityIdentity.race/
  entity_class/tier` quando não há `SpawnZoneOwner`); `payload["name"]`
  enviado quando `ident.name` existe; `payload["is_dummy"] = True` quando a
  entidade tem componente `TrainingDummy`. Mobs de zona (caminho normal,
  imensa maioria) continuam 100% inalterados — só entram no `elif`
  entidades sem `SpawnZoneOwner`.
- `client/remote_entity_handlers.py::_spawn_remote_mob`: lê `data.get("name")`
  pra sobrescrever `EntityIdentity.name` do proxy local; lê
  `data.get("is_dummy")` pra anexar `TrainingDummy()` ao proxy local.
- `entity_factory.create_training_dummy()`: `EntityIdentity` renomeada pra
  `name="Boneco de treino", race="Mecânico"` (antes `"Boneco de Treino"`/
  `"Construto"` — sem outras referências no código, troca segura).

Validado com diagnóstico isolado (servidor real via `create_training_dummy`
+ `_build_mob_spawn_payload`; cliente real via `_spawn_remote_mob`): payload
do boneco tem `race`/`name`/`is_dummy` corretos; mob de zona real (Elemental,
confirmado via presença de `SpawnZoneOwner`) mantém raça/classe/tier da
própria zona, sem `is_dummy` no payload — zero regressão. Compile limpo,
suíte sem regressão (32/49/1).

---

### ✅ RESOLVIDO — Texto de objetivo de quest inconsistente (sim/não, id de skill, prefixo "v"/"-")

HUD (abaixo do minimapa), diálogo de aceitar quest e diário (tecla J)
renderizavam objetivos de forma inconsistente: alguns tipos usavam
`"X: sim"/"X: não"`, outros `"X (n/total)"`; todos mostravam o **id** da
skill (`bola_de_fogo`) em vez do nome amigável (`Bola de Fogo`); um prefixo
`"v "`/`"- "` indicava conclusão.

Fix centralizado em `QuestSystem._obj_label()` (única função geradora do
texto, chamada pelos 3 pontos de renderização): todos os 9 tipos de
objetivo convertidos pro formato uniforme `"{descrição} ({progresso}/
{total})"`; novo helper `QuestSystem._skill_label(skill_id)` resolve o nome
amigável via `SKILL_CATALOG[skill_id]["name"]` (usado em `use_skill` e
`learn_skill`). Prefixo `"v "`/`"- "` removido dos 3 render sites
(`QuestSystem._build_hud_surf`, `QuestDialogSystem._render_detail`,
`QuestJournalSystem`). Cor passou de binária (verde/cinza) pra 3 estados:
verde = `progress >= count` (completo), branco (`COL_ACTIVE`/`COL_WHITE`,
novo) = `0 < progress < count` (em evolução — feedback de "está progredindo
nesse objetivo"), cinza = `progress == 0` (sem evolução).

**Bug latente encontrado de carona ao validar visualmente** (renderização
real, não só assert de string): `QuestSystem.__init__` reatribuía
`self._font_title`/`self._font_obj` pra `None` **depois** de
`super().__init__()` — que já carrega essas fontes de verdade via
`UIScaleMixin`/`_FONT_BASES`. Como `game.py` chama
`_sys.set_ui_scale(self._ui_scale)` logo após construir todo painel (ver
`ui_scale_mixin.py`), e essa chamada é idempotente (não faz nada se
`scale == self._ui_scale_applied`, e o mixin já tinha aplicado `1.0` no
próprio `__init__`), o painel só funcionava "por sorte" porque o
`config.json` deste projeto tem `ui_scale: 1.25` (≠ 1.0). Com `ui_scale`
exatamente `1.0` (default de fábrica, `_cfg_data.get("ui_scale", 1.0)`,
ou qualquer instalação nova sem o config ainda ajustado), `render_hud()`
crashava com `AttributeError: 'NoneType' object has no attribute 'render'`
na primeira quest ativa. `QuestDialogSystem`/`QuestJournalSystem` não tinham
esse padrão (não resetavam fonte pra `None` depois do `super().__init__()`)
— bug isolado a `QuestSystem`. Fix: removidas as duas linhas redundantes;
fontes ficam só a cargo do mixin.

Validado: render real (`pygame.image.save`) com `ui_scale` padrão (1.0, sem
chamada explícita de `set_ui_scale`) confirma layout/cores/texto exatos —
"Aprender Bola de Fogo (1/1)" verde, "Treinar Bola de Fogo no boneco de
treino (3/5)" branco, e ambos em cinza com progresso zerado. Compile limpo,
suíte sem regressão (32/49/1).

---

### ✅ RESOLVIDO — 3 ajustes finos pós-implementação de "Iniciação Arcana"

**1. Números de progresso na apresentação da quest (diálogo de aceitar)**:
usuário pediu pra esconder o `(x/y)` só ali — continua aparecendo no HUD
(abaixo do minimapa) e no diário (tecla J), porque antes de aceitar a quest
o progresso é sempre `0/N` e não comunica nada útil. `_obj_label()` ganhou
parâmetro `show_progress: bool = True`; internamente foi reescrita pra
montar a descrição (`desc`) separada do sufixo de progresso, só concatenando
o sufixo quando `show_progress=True`. `QuestDialogSystem._render_detail`
(diálogo) passa `show_progress=False`; `_build_hud_surf` (HUD) e
`QuestJournalSystem._render_detail` (diário) usam o default `True`.

**2. `learn_skill` não considerava skill aprendida ANTES de pegar a quest**:
se o player já tinha treinado a skill antes de aceitar "Iniciação Arcana"
(cenário legítimo — nada impede aprender a skill primeiro), o objetivo
nunca completava, porque o evento `learn_skill` só dispara na COMPRA
(`trainer_system.py::_do_learn`), nunca reavaliado depois. Mesmo padrão já
existente pra `reach_level` (`_try_start` já checava nível atual no
momento de iniciar a quest): adicionado `elif obj.type == "learn_skill"
and self._skill_already_learned(obj.target): prog.append(obj.count)` em
`QuestSystem._try_start`, com novo helper `_skill_already_learned(skill_id)`
(consulta `PlayerSkills.learned_skill_ids` direto, sem reimplementar
bloqueio de aprendizado pré-quest — como o usuário apontou, bloquear seria
mais complexo que checar o catálogo).

**3. `use_skill(on_dummy)` contava na ATIVAÇÃO da skill, não no dano efetivo**:
Bola de Fogo tem `cast_time` (1.5s) — o `quest_fire("use_skill", ...)`
disparava no instante em que o jogador apertava a tecla (`handler_fn`
retornando sucesso), antes do projétil sair, viajar e confirmar dano —
então errar o alvo ou o boneco morrer/sumir no meio do cast ainda contava.
Causa raiz: o ponto de disparo do evento estava acoplado à ativação da
skill, não ao resultado. Fix — gating por `has_cast`/`_has_cast` (skill sem
cast_time, ex: Golpe Poderoso/Executar, já causa dano de forma síncrona
dentro do próprio handler, então continua contando na ativação; skill COM
cast_time deixa de contar ali) + novo disparo no ponto real de dano
efetivo:
- Online (`client/network_handlers.py::_handle_msg_skill_result`, ramo
  `_is_pdmg` específico de `sid == "bola_de_fogo"`): dispara
  `quest_fire("use_skill", ...)` só quando `t.get("damage", 0) > 0`, com
  `on_dummy` resolvido a partir do proxy local do alvo via
  `self._remote_mobs.get(_t_srv)` — exatamente o ponto onde o servidor já
  confirmou o dano via `SKILL_RESULT(is_proj_damage=True)`.
- Offline (`spell_system.py::PlayerProjectileSystem._on_hit`, ramo de dano
  mágico): disparo movido pra depois de `_apply_magic_damage(...)`, e só é
  alcançado se `outcome != "miss"` (a função já retorna antes em caso de
  resistência/erro — dano realmente aconteceu).

Validado com diagnóstico isolado: `_obj_label(obj, 0, show_progress=False)`
omite o sufixo; `_try_start` pré-completa o objetivo 1 quando
`learned_skill_ids` já contém a skill antes de iniciar (e NÃO pré-completa
quando não contém); evento `use_skill` simulado como "dano confirmado"
incrementa o objetivo 2 corretamente. Render real do diálogo confirma texto
sem `(x/y)`. Compile limpo, suíte sem regressão (32/49/1).

---

### ✅ RESOLVIDO — Redesign de `mob_definitions.py`: atributos/habilidades/loot/xp por mob (antes só 2 templates fixos)

Antes: `entity_factory.py::create_enemy()` só tinha **2 templates fixos**
(melee/ranged), iguais pra qualquer raça — Zumbi, Lobo, Orc etc. todos com
o MESMO HP/dano/acerto/crit/velocidade, diferenciados só por
`ENEMY_TIER_CONFIGS` (tier) e level. `mob_definitions.py` só guardava
raça/classe/cor/sons. Habilidades especiais vinham de
`enemy_abilities_data.py::MOB_ABILITIES`, indexadas por raça OU
entity_class (ex: toda raça "Hunter" ganhava `poison_arrow`, sem distinção
por mob). Loot vinha de `loot_tables.py::MOB_LOOT_TABLES`, duplicando a
lista de itens que já existia conceitualmente "no mob". XP de morte
(`server/server_death_handler.py::_XP_BY_TIER`) era **flat por tier**, sem
NENHUMA relação com o level do mob.

**Novo esquema** (`mob_definitions.py::MOB_TABLE[nome]`), migrado pros 14
mobs existentes:
- `attributes` — `health`, `armor` (%, convertido em
  `create_enemy()` pra rating interna: `rating = pct*10`, já que
  `ARMOR_REDUCTION_PER_POINT=0.001` em `damage_calculator.py`),
  `attack_min/attack_max` (faixa de dano base), `attack_power` (soma à
  faixa), `move_speed_pct` (% de `ENEMY_SPEED`), `attack_speed` (intervalo
  entre ataques), `acerto` (% direto), `crit_chance` (%, convertido pra
  fração 0-1). Mob físico (Warrior/Hunter) usa `base_physical_damage`/
  `base_attack_power`; mob "mágico" (entity_class Mage/Mago/Warlock/Bruxo —
  Vampiro, Dragão) ADICIONALMENTE espelha `attack_power`→`base_spell_power`
  e a média de `attack_min/attack_max`→`base_magical_damage`, pra
  `damage_type_to_use` (`EnemyAISystem`) resolver "magical" — **mantém o
  comportamento original**: dano mágico ignora armadura do alvo (reduzido
  só por resistência elemental, nunca implementada pra auto-attack genérico
  de mob — só existe hoje pra skills nomeadas via `_server_apply_magic_damage`,
  ver Fase 7 do Skill Level). `base_attack_power` continua setado mesmo pra
  mob mágico (não zerado) — alimenta o multiplicador de dano de habilidade
  (ver abaixo). `ENEMY_TIER_CONFIGS`/level-scaling (`entity_factory.py`)
  continuam aplicados POR CIMA destes valores, sem mudança de mecanismo —
  só mudou a fonte do valor "base" (level 1). **Level do mob vem da
  `SpawnZone` no JSON do mapa** (`level_min`/`level_max`,
  `{map}_entities.json`) — não é um campo de `mob_definitions.py`.
- `abilities` — lista de ability_id (`ABILITY_DEFS`, agora com
  `default_cooldown` próprio) substituindo o lookup por raça/classe; cada
  entrada pode ser `"id"` (usa o cooldown default) ou `("id", cooldown)`
  pra sobrescrever só nesse mob (ex: Cobra usa `poison_bite` com cooldown
  diferente do Escorpião). `MOB_ABILITIES` removido (dead code).
  `AbilityDef.magnitude` deixou de ser dano flat por tick e passou a ser
  **multiplicador do `attack_power`** do mob que usa a habilidade — aplicado
  em `EnemyAbilitySystem` (melee) e `ProjectileSystem` (ranged, ao colidir)
  no momento de causar o efeito (`dano_por_tick = magnitude × base_attack_power`,
  usa `base_` e não o campo efetivo pra não depender de
  `_recalculate_effective_stats()` já ter sido chamado). Os valores atuais
  de `magnitude` em `enemy_abilities_data.py` ainda são os antigos (dano
  flat, ex: 25.0) — precisam ser reduzidos pra escala de multiplicador
  (ex: 2.0-4.0) antes de ir pra produção.
- `loot` — dict `{item_key: chance}`, fonte única (antes duplicada em
  `loot_tables.py::MOB_LOOT_TABLES`, removida). `loot_tables.py::roll_mob_loot`
  agora só lê `MOB_TABLE[nome]["loot"]` + aplica o multiplicador de tier
  (mecanismo de chance/tier inalterado).
- `xp_given_by_lvl` — usado em 2 pontos: `server/server_death_handler.py`
  (XP real concedido ao matar, server-autoritativo —
  `level_do_mob × xp_given_by_lvl × tier["xp"]`) e
  `entity_factory.py::XPReward` (componente offline, mesma fórmula).

**Level-scaling de mob cadastrado é MULTIPLICATIVO, não aditivo** (correção
pós-implementação, a pedido do usuário): `health`/`attack_power` (e o
espelho mágico `spell_power`/`magical_damage`, pra mob caster) MULTIPLICAM
por `level` (`stats.base_stamina = int(stats.base_stamina_pós_tier) × level`)
— igual à fórmula de XP. `armor`/`acerto`/`crit_chance`/`attack_min`/
`attack_max`/velocidade ficam fixos, vêm só de `attributes` + tier (level
NÃO afeta esses campos). Mob sem cadastro (fallback, "Elemental") continua
com o scaling ADITIVO antigo (`+75 stamina/level`, `+2 AP/level`, `+4
armor/level`, `+0.01 crit/level`) — `create_enemy()` ramifica em
`if attrs: ... else: ...` dentro do bloco `if level > 1:`.

**Fallback preservado**: mobs SEM entrada em `MOB_TABLE` (ex: "Elemental",
usado em zonas reais do mapa mas nunca cadastrado) continuam no template
genérico antigo (2 templates fixos por `is_ranged`, `ENEMY_SPEED` puro, XP
flat `level×15×tier`) — `create_enemy()` ramifica em
`attrs = mob_def.get("attributes") if mob_def else None`, sem alterar esse
caminho.

**Bug pré-existente encontrado e corrigido de carona**: `tests/test_server.py`
(`TestRangedMobAbilities`) faz monkeypatch de `EnemyAISystem._has_line_of_sight`
(staticmethod) pra simular LOS true/false, restaurando com
`EnemyAISystem._has_line_of_sight = original_los` — mas `original_los` foi
capturado via acesso de atributo (`EnemyAISystem._has_line_of_sight`), que já
desempacota o `staticmethod` pra função pura. Reatribuir a função pura (sem
reembrulhar em `staticmethod(...)`) faz QUALQUER chamada futura por instância
(`self._has_line_of_sight(...)`, usada em `EnemyAISystem.update()`) injetar
`self` como 1º argumento automaticamente — `TypeError: takes 5 positional
arguments but 6 were given` pro resto do processo de teste. Confirmado
pré-existente (reproduzido na baseline antes desta sessão, com taxa de falha
variável por seed de hash do processo); só não aparecia nos runs completos
da suíte porque a ordem fixa de execução "escondia" o efeito na maioria das
vezes. Fix: `EnemyAISystem._has_line_of_sight = staticmethod(original_los)`
nos 2 pontos de restore.

Validado com diagnóstico isolado: Zumbi nível 1/tier normal reproduz
exatamente os atributos do schema; Zumbi tier elite/nível 3 confirma
tier-mult + level-scaling aplicados corretamente por cima da baseline;
"Elemental" (sem cadastro) confirma fallback legado 100% intacto; loot e
habilidade (multiplicador) validados. Suíte sem regressão (32/49/1, diff
exato contra a baseline).

---

### ✅ RESOLVIDO — Clique direito em NPC (Trainer/Blacksmith/QuestGiver) não funcionava com zoom != 100%

Usuário reportou: "quando dou zoom no jogo, não estou conseguindo interagir
com os NPCs". Raiz: o zoom (`self._zoom` em `game.py`) não escala a tela
real 1:1 — o mundo é renderizado numa surface lógica intermediária
(`self._zoom_surf`, tamanho `lw×lh = tela/zoom`), depois ESCALADA pra
caber na tela real ao blitar. Convergir um clique de mouse (sempre em
coordenadas de TELA REAL) pra posição no MUNDO exige multiplicar pelo
fator de escala `world_surf.width / hud_surf.width` (≈ `1/zoom`) ANTES de
somar o offset de câmera — e o offset de câmera em si precisa ser
calculado com as dimensões da surface LÓGICA (`world_surf`), não da tela
real (`hud_surf`).

`ShopSystem._get_cam()`/click handler (`systems.py`) já fazia isso
corretamente (`_sc = world_surf.width/hud_surf.width`; `wx = mx*_sc + cam_x`)
— por isso interagir com Comerciantes nunca quebrava. Mas
`TrainerSystem` (`trainer_system.py`), `BlacksmithSystem`
(`crafting_system.py`) e `QuestDialogSystem` (`quest_system.py`) tinham a
MESMA lógica de clique copiada SEM o fator de escala (`cx = cam.x -
hud_surf.width/2`; `wx = ev.pos[0] + cx`) — funciona perfeitamente em
`zoom == 1.0` (`world_surf.width == hud_surf.width`, fator=1, por isso o
bug só aparece "quando dá zoom") mas erra a posição mundial em qualquer
outro zoom (erro proporcional à distância do clique ao centro da tela ×
`(zoom-1)` — facilmente maior que o hitbox do NPC, ~12px de raio).

Fix: replicado o padrão já correto de `ShopSystem` nos 3 sistemas —
`SW, SH = self.world_surf.get_size()` (não `hud_surf`) pro cálculo de
`cam_x/cam_y`, e `wx = ev.pos[0] * _sc + cam_x` (com
`_sc = world_surf.width / hud_surf.width`) pro clique.

Validado com diagnóstico isolado: simulado zoom=1.5, NPC posicionado de
forma que sua posição de TELA renderizada (calculada à mão, mesma fórmula
do render real) caia exatamente no ponto clicado — confirma detecção
correta do clique (`_right_click_consumed=True`, abre o menu do
treinador) onde antes o cálculo sem escala erraria a posição mundial em
~20px (maior que o hitbox). Compile limpo, suíte sem regressão (32/49/1).

---

### 🔴 CRÍTICO RESOLVIDO — `ManaSystem` regenerava mana só no cliente, sem o servidor saber (HUD mostrava mana que o servidor não tinha)

Usuário reportou: HUD mostra "Mana 150/195", mas usar uma skill
(mana_cost=25) mostra "Mana insuficiente (25)".

**Investigação** (descartando hipóteses por ordem, com diagnóstico
isolado em cada passo):
1. A mensagem com `(custo)` entre parênteses só existe em
   `skill_handlers.py::_check_mana`. Client-side online não chama essa
   função pra Bola de Fogo (só pra skills AOE) — confirmado que a
   mensagem vinha do **SERVIDOR**, propagada via
   `SKILL_RESULT(failed=True, reason=_last_warn)`
   (`server/skill_processor.py`). Logo, o servidor realmente tinha
   `CharacterStats.mana < 25` — não é check client-side incorreto.
2. Debug temporário nos dois lados (servidor: cada dedução/regen/envio de
   STATS_UPDATE; cliente: cada STATS_UPDATE recebido) confirmou: a
   sequência de deduções do servidor estava **perfeita** (195→170→145→
   ...→20, sempre -25 exato), e a checagem de mana insuficiente em 20 é
   correta. Porém o "antes" registrado no cliente, comparado ao último
   valor que o próprio cliente tinha aplicado, mostrava incrementos
   pequenos e intermitentes (+1, +1, +8, +1) **entre** as confirmações do
   servidor — sempre nos intervalos de espera entre casts, nunca nos
   pares de envio duplicado (sem gap de tempo).
3. Causa raiz: `spell_system.py::ManaSystem` — sistema cliente que
   regenera mana periodicamente (a cada 5s, 4% de max_mana fora de
   combate / 1% em combate) **sem nenhum gate de `self._net`** — rodava
   exatamente igual online e offline. O **servidor não tinha equivalente
   nenhum** desse regen passivo (só existiam HoT de poção/`ActiveManaRegen`,
   ambos corretamente sincronizados). Resultado: o cliente regenerava
   mana sozinho a cada 5s, de forma crescente e nunca corrigida (cada
   STATS_UPDATE de gasto subtrai do valor real do servidor, mas o
   "excesso" client-only nunca é zerado — só cresce com o tempo), até o
   HUD mostrar bem mais mana do que o servidor realmente tinha.

**Fix** (regen de mana agora segue o MESMO padrão já usado pra HP5/
Concentração — único produtor autoritativo é o servidor):
- `core_systems.py::BaseCombatStateSystem` — nova constante
  `MANA_REGEN_INTERVAL/OOC_PCT/IC_PCT` + `_tick_mana_regen(cs, char, dt)`
  (classmethod, espelha `_tick_hp5`): retorna `(old_mana, new_mana)` se
  regenerou, `None` caso contrário.
- `ServerCombatStateSystem.update()` — chama `_tick_mana_regen` pra cada
  player, popula novo `self.mana_events` (mesmo padrão de `hp5_events`).
- `server/world_server.py::_tick()` — consome `mana_events`, enfileira em
  `_pending_xp_deliveries` (mesmo canal já usado por
  `ActiveManaRegen`/restauração instantânea) → vira `STATS_UPDATE` real
  pro cliente.
- `spell_system.py::ManaSystem` — ganhou `self._net` (injetado por
  `game.py` após `_connect_online()`, igual `ConsumableSystem`/
  `SkillSystem`/etc.); só aplica o regen localmente quando
  `not self._net` (offline). Online, delega 100% pro servidor — chama a
  MESMA função `_tick_mana_regen` (fonte única) só pra manter o cálculo
  consistente entre os dois modos, mas o resultado só é aplicado offline.

Validado: fórmula pura testada deterministicamente (4% OOC, 1% IC, sem
regenerar com mana cheia); integração real via `tests/helpers.py`
confirma que o servidor regenera e enfileira o STATS_UPDATE
corretamente; `ManaSystem` client-side confirmado SEM regenerar
localmente quando `self._net` está setado (online) e CONTINUA
regenerando normalmente quando `None` (offline, sem servidor pra
confirmar). Suíte sem regressão (32/49/1, 3 runs estáveis).

---

### 🔴 CRÍTICO RESOLVIDO — Mob em RETURNING continuava atacando o player (dano "do nada", sem mob visível)

Usuário reportou: quando o mob persegue o player e o player sai da zona de
aggro, o mob deveria voltar pro spawn (RETURNING) — mas o player continuava
tomando dano sem nada visível na tela causando.

**Causa raiz** (`systems.py::EnemyAISystem.update()`): o bloco de ataque
(`in_attack_range` + cooldown → `deal_damage`/projétil) nunca checava
`AIControlled.state` — só checava distância/cooldown/invisibilidade. A
transição de leash (`_dist_from_spawn > leash_radius` → `state="RETURNING"`,
`target_eid=-1`) acontece DEPOIS do bloco de ataque, na mesma iteração — ou
seja, no tick em que o mob desiste e decide voltar, ele já tinha acabado de
bater. Pior: como o ataque não é bloqueado por estado, um mob que continua
dentro do `attack_range_tiles` enquanto caminha de volta (corredores
estreitos, paths que passam perto do player) continuava desferindo golpes
indefinidamente.

Isso quebrava a atribuição visual do ataque: `server/combat_processor.py`
(`_mob_attacker_of`, monta o mapa "quem atacou quem" pro cliente) só inclui
mobs com `state in ("ATTACKING", "CHASING")` — roda DEPOIS de
`EnemyAISystem.update()` no mesmo tick, então no tick exato da transição o
mob já está com `state="RETURNING"` e cai fora do mapa. O fallback
`_last_mob_attacker` (cache do último atacante válido) cobre a maioria dos
casos, mas não o primeiro hit de uma sequência nem hits subsequentes
enquanto RETURNING continuasse acertando — resultado: `attacker=-1` chega
ao cliente, que não tem mob nenhum pra apontar/animar/tocar som
(`client/remote_entity_handlers.py::_play_attacker_mob_sound` retorna sem
fazer nada quando `server_attacker == -1`) — dano aparece "do nada".

**Fix** (`systems.py`, no cálculo de `in_attack_range`): adicionado
`ai_control.state != "RETURNING"` ao gate. Único estado excluído de
propósito — **não** exclui `IDLE`/`AGGRO_DELAY`: esses são estados
pré-combate normais, e o próprio bloco que promove um mob pra `"ATTACKING"`
(`if ... in_attack_range and not needs_to_kite: state="ATTACKING"`) também
depende de `in_attack_range` — excluí-los travaria PERMANENTEMENTE em
IDLE/AGGRO_DELAY qualquer mob que já nasça adjacente ao player (regressão
real, pega por `tests/test_server.py::test_mob_attacks_player_in_range` —
corrigida revertendo a exclusão pra só `RETURNING`).

Validado com diagnóstico isolado: mob com `state="RETURNING"` forçado a
cada tick (isolando de outros mobs via HP=0), adjacente ao player por 60
ticks (3s) — zero dano, zero `COMBAT_RESULT`. Controle (mesmo mob/posição,
`state="ATTACKING"`) continua causando dano normalmente. Nota lateral
descoberta durante o diagnóstico (comportamento pré-existente, não é bug):
se o pathfinding de volta ao spawn falhar (ex: rota bloqueada/inalcançável),
o mob cai em `IDLE` na posição atual em vez de ficar travado em RETURNING
pra sempre — intencional. Suíte sem regressão (32/49/1, 3 runs estáveis).

---

### 🔴 CRÍTICO RESOLVIDO — Mob "nunca aparece" ao voltar pro spawn: flicker de spawn/despawn na borda do AOI (sem histerese)

Usuário esclareceu o bug anterior (que eu tinha corrigido a causa errada
— ver entrada acima, ainda válida, mas não era essa a queixa): "quando o
mob sai da TELA do player e eu volto pra tentar achá-lo, ele não aparece —
só aparece no spawn dele quando eu saio da tela do spawn e volto." Ou seja,
não é sobre dano invisível — é sobre o mob literalmente nunca renderizar de
forma estável quando o player se aproxima de novo.

**Evidência real**: `logs/aoi_debug.log` (debug já existente no projeto,
`aoi_debug.py`, `DBG_ENABLED=True` por padrão) já tinha a prova: o mesmo
`server_eid` gerando `SPAWN_OK`→`DESPAWN_AOI` repetidas vezes em poucos
segundos, com `local_eid` NOVO a cada vez (40s, 19s, 0.4s de intervalo) e
posições que mudavam bastante entre cada par. Isso é geometricamente
impossível pra um mob andando normalmente (poucos tiles/segundo) — só faz
sentido se o mob ficou invisível (fora do AOI) andando uma distância maior
ENQUANTO estava despawnado, e só "pisca" visível por uma fração de segundo
quando o caminho de volta cruza a borda do raio.

**Causa raiz** (`server/session.py::_build_update_for_session`): a checagem
de entrada E saída de AOI usava o MESMO raio (`AOI_RADIUS=15`, círculo de
`(tx-cx)²+(ty-cy)² <= r²`) sem nenhuma histerese. Um mob cujo caminho (ex:
RETURNING pro próprio spawn, que pode ficar bem perto da borda de 15 tiles
relativa à posição do player) cruza repetidamente esse raio gera um ciclo
completo de DESPAWN (sai) → SPAWN (volta a entrar) a CADA tick que cruza a
fronteira — o cliente literalmente nunca tem tempo de manter o mob
renderizado de forma estável antes de recebê-lo como removido de novo.

**Fix**: histerese — raio de ENTRADA continua `AOI_RADIUS` (15 tiles,
inalterado, “torna-se conhecido” não muda), mas a permanência (decisão de
SAÍDA pra quem já está em `known_eids`) agora usa `AOI_RADIUS +
AOI_EXIT_BUFFER` (nova constante em `shared/constants.py`, buffer=3 → raio
de saída efetivo 18 tiles). Um mob só é despawnado depois de se afastar
de verdade, não ao simplesmente cruzar a borda de entrada pela primeira
vez. Aplica-se só ao mecanismo de "moved" (`in_aoi_exit()`, nova função
local) — a checagem de invisibilidade (Camuflagem) e o sweep de
"newly-in-range" (que só ADICIONA, nunca remove) ficam inalterados.

Validado com diagnóstico isolado: mob simulado cruzando repetidamente
14→16→14→16→14→16→14 tiles de distância (a borda exata de 15) — SEM o
fix, gera 3 ciclos completos de despawn+spawn (reproduzido explicitamente
revertendo o fix); COM o fix, zero spawn/despawn durante todo o cruzamento
repetido. Controle: afastar de verdade (25 tiles, bem além do buffer)
continua despawnando corretamente. Suíte sem regressão (32/49/1, 3 runs
estáveis).

---

### 🔴 CRÍTICO RESOLVIDO — Mob perseguindo, player mais rápido: leash nunca disparava, mob travava em IDLE longe do spawn pra sempre

Usuário testou de novo, especificamente: aggrou um mob e correu (mais
rápido que o mob) pra longe do próprio spawn dele, esperando ver o mob
voltando quando ele olhasse pra trás. Em vez disso, o mob ficou
simplesmente **parado no meio do caminho** — sem saber se o RETURNING
chegou a iniciar. Reproduzido com diagnóstico isolado (`EnemyAISystem`
real, sem nenhuma forçação manual de estado depois do gatilho inicial de
CHASING — só ticks reais), revelando **três bugs distintos na mesma
família**, todos em `systems.py::EnemyAISystem.update`, todos com o mesmo
padrão: "desistir de perseguir" virava `state="IDLE"` direto na posição
atual, sem nunca checar se o mob estava longe do próprio spawn primeiro.

1. **Sleep Zone (linha ~2394, `SLEEP_RADIUS_TILES=40`)** rodava o
   `continue` de congelamento pra **qualquer estado**, não só `IDLE`. Se o
   player ficasse a mais de 40 tiles do mob, esse check disparava **antes**
   do leash (20 tiles do próprio spawn, `MAX_LEASH_RADIUS`) no mesmo loop —
   bastava o player ser mais rápido o bastante pra abrir 40 tiles de gap
   antes do mob se afastar 20 tiles do spawn. Mob virava `IDLE` ali mesmo,
   a só 5-9 tiles do spawn (bem longe de precisar de leash), com o path
   descartado (`path=None`). Fix: o freeze do Sleep Zone só se aplica a
   mobs **já `IDLE`** (sem chase/return pendente) — `CHASING`, `ATTACKING`
   e `RETURNING` sempre processam o tick completo, não importa a distância
   do player.
2. **Desistência por falha de pathfinding (linha ~2854, dentro do bloco de
   perseguição)**: quando o mob não achava caminho até o player E o player
   estava fora do `detect_radius` (`ENEMY_DETECTION_RADIUS=250px`≈7.8
   tiles — bem menor que o leash de 20!), o mob virava `IDLE` direto, sem
   checar a distância até o próprio spawn. Fix: nesse caso, se o mob está
   longe do spawn (`> proximity_threshold_tiles`), vai para `RETURNING`
   (replicando exatamente o padrão já usado pelo bloco "sem alvo válido")
   em vez de travar em `IDLE`.
3. **RETURNING desistia na primeira falha de path (linha ~2892)**: ao
   contrário do bloco irmão "sem alvo válido" (que em caso de falha só
   limpa o path e força recálculo no próximo tick, mantendo `RETURNING`),
   este bloco (usado quando o leash ou o fix #2 acima dispara `RETURNING`
   dentro do mesmo tick de perseguição) caía pra `IDLE` na primeira
   tentativa sem path — inclusive quando a falha era só o **orçamento de
   pathfinding do frame esgotado** (`MAX_PATHFINDS_PER_FRAME=10`, sendo
   torrado pelas várias tentativas de achar um tile de ataque perto do
   player ainda no mesmo tick, antes do leash disparar). Fix: mesmo padrão
   do bloco irmão — sem path disponível (e não bloqueado), só limpa o path
   e força recálculo imediato, sem rebaixar pra `IDLE`.

**Validação**: diagnóstico com mob real (`EnemyAISystem` de verdade, sem
forçar nada após o CHASING inicial) — player corre numa direção livre até
o mob entrar em RETURNING. ANTES dos 3 fixes: mob travava em `IDLE` a
5-21 tiles do spawn (variava por causa de qual dos 3 bugs disparava
primeiro) e **nunca** se movia de novo em 15s de ticks reais, mesmo
parado pra sempre — só "acordava" quando um player chegava bem perto da
posição travada. DEPOIS dos 3 fixes: leash/desistência aciona
corretamente, mob entra em `RETURNING` e percorre, sozinho, tile a tile,
todo o caminho de volta ao spawn (confirmado chegando a 1 tile do centro
do spawn, indo pra `IDLE` lá — não mais na posição onde travava antes).

**Renderização**: validado também em diagnóstico com `Session`/AOI real
(não só `WorldServer`) — durante a perseguição longa, o mob corretamente
*sai* do AOI da sessão (jogador longe); ao caminho de volta cruzar de
novo o raio do AOI do jogador (que se aproximou andando até ~12 tiles do
spawn do mob, sem precisar voltar exatamente até o spawn), o cliente
recebe o respawn dele imediatamente (`AOI_UPDATE`/`spawned`) — confirma
que os 3 fixes de IA, combinados com a histerese de AOI já corrigida
(entrada acima), eliminam o "mob nunca aparece de volta" relatado.

Suíte sem regressão (32/49/1, 2 runs estáveis).

---

### ✅ RESOLVIDO — Quest sem restrição de classe + menu de assunto do treinador sempre aparecia (mesmo com 1 única opção)

Pedido do usuário: "Iniciação Arcana" deveria ser estritamente do mago (e
primeira de uma cadeia da classe), mas o sistema de quest não tinha
NENHUMA noção de classe — qualquer player de qualquer classe podia
aceitar qualquer quest de qualquer NPC. Junto, quando um NPC é treinador
de uma classe mas também oferece quest pra todas: jogando de outra
classe, o clique direito sempre abria o menu "Treinamento / Quests" pra
escolher, mesmo treinamento não fazendo sentido pra essa classe (deveria
pular direto pra quest). E quando o player já tem uma quest pronta pra
entregar, o menu de assunto não devia aparecer — deveria abrir a entrega
direto.

**Fix**:
- `QuestDef` (`quests_data.py`) ganhou `class_req: str = ""` (`""` =
  qualquer classe). Aplicado `class_req="mago"` em `iniciacao_arcana`.
  Cadeia de quests por classe: usa o mecanismo `requires`/`next_quest`
  que já existia (sem mudança) — só faltava a restrição de classe em si.
- `quest_logic.try_start` valida `class_req` contra
  `CharacterStats.class_id` do `world`/`player_eid` recebido — vale tanto
  pro servidor (autoritativo) quanto pro caminho offline.
- `QuestDialogSystem._get_available_quests`/`_get_locked_quests`
  (`quest_system.py`) filtram por `_player_class_id()` — decisão do
  usuário: quest de classe errada é **totalmente invisível** (sem ícone "!"
  sobre o NPC, não aparece nem como "bloqueada" — diferente do bloqueio
  por nível, que continua visível/cinza). Não precisou mudar
  `_get_completable_quests`/`_get_inprogress_quests`: ambos já dependiam
  de `qid in ql.active`, e `ql.active` só pode conter quests que passaram
  por `try_start` (já filtra classe).
- `TrainerSystem` (`trainer_system.py`): `_compute_options(eid)` novo,
  único lugar que decide quais assuntos existem pra ESSE player com ESSE
  NPC — "Treinamento" só se `CharacterStats.class_id == Trainer.class_id`;
  "Quests" só se `_get_available_quests`/`_get_completable_quests`/
  `_get_inprogress_quests` retornarem algo (não só "o NPC tem
  `quest_ids`", como era antes — antes disso "Quests" aparecia mesmo sem
  nada pra mostrar, levando a um beco sem saída no diálogo). `_open_menu`
  agora: (1) se há quest completável pra entregar, abre o diálogo de
  quest direto, ignora o menu mesmo com treinamento disponível
  (prioridade pedida pelo usuário); (2) senão, se só resta 1 opção (ou
  nenhuma), pula direto pra ela (ou fecha com uma mensagem de log) sem
  mostrar o menu; (3) só com 2+ opções reais é que o menu de assunto
  aparece.
- Menu de assunto redesenhado: tamanho fixo igual ao diálogo de quest
  (`UI.QUEST_DIALOG_W/H`, mesmo `_safe_panel_origin`, antes era
  dinamicamente dimensionado pelo número de botões) + botão fechar `[X]`
  (não tinha) + cabeçalho trocado de "{nome do NPC}" pra "Olá {nome do
  player}, o que deseja?" (saudação pedida pelo usuário), com os botões de
  assunto centralizados no espaço abaixo da linha divisória.

**Validação**: diagnóstico isolado (mundo ECS minimal, sem rede) cobrindo
os 4 cenários — (1) `try_start` aceita mago e rejeita guerreiro pra
"iniciacao_arcana"; (2) guerreiro não vê a quest nem disponível nem
bloqueada (só vê a genérica `first_equip`), mago vê as duas; (3) guerreiro
falando com um treinador de mago que só tem quest de mago vê 0 opções →
fecha com log (não abre menu vazio); guerreiro falando com treinador da
própria classe + quest genérica vê as 2 opções → abre o menu; mago
falando com o MESMO treinador de guerreiro (só a quest genérica é dele)
pula direto pro diálogo de quest, sem menu; (4) guerreiro com uma quest
completável + treinamento disponível pula direto pro diálogo em estado
"turnin", ignorando o menu. Renderização verificada visualmente
(screenshot) — painel do tamanho certo, saudação correta, botões
centralizados. Suíte sem regressão (32/49/1).

---

## Bug — IA de mobs cruzando mapas (cross-map AI targeting) — 01/07/2026

**Sintoma relatado:** Player no mapa principal (`map_1`) tomava dano de mobs
invisíveis. Às vezes flechas, às vezes melee/dodge — mas nenhum mob visível
ou targetável causando esse dano.

**Causa raiz confirmada (via debug log):** `EnemyAISystem._select_target()`
iterava sobre **todos** os `PlayerControlled` no world, sem filtro de mapa.
Numa arquitetura multi-mapa onde todos os mapas compartilham o mesmo `World`
ECS, um mob em `map_cave_west` enxergava players em `map_1` (coordenadas
brutas coincidentes no espaço compartilhado) e os atacava normalmente.
O player atacado nunca via o mob (mob não estava no seu AOI — estava em outro
mapa) e não conseguia targetá-lo. Mobs de cave com Flecha Certeira ou melee
causavam dano que o player não podia evitar nem bloquear.

**Bugs colaterais descobertos na mesma análise:**
- `EnemyAbilitySystem.update()`: `player_tiles` construído sem filtro de mapa
  → habilidades de mob de cave disparavam contra players de `map_1`
- `SpawnZoneSystem.update()`: culling de zonas (ACTIVATION_RADIUS) usava
  posição de qualquer player, não só os do mesmo mapa → zonas de `map_1`
  não dormiam com map_1 vazio se alguém estivesse no cave
- `EnemyAISystem.update()` sleep-check (SLEEP_RADIUS, seção IDLE): mesmo
  problema — lazy import duplicado dentro do loop por mob, causando import
  redundante a cada iteração
- `in_aoi`/`in_aoi_exit` (`server/session.py`): predicado invertido —
  entidades **sem** `MapLocation` passavam pelo filtro silenciosamente
  (`if _ml and _ml.map_file != _my_map` → `False` sem MapLocation =
  entidade inclusa, deveria ser excluída)

**Fixes aplicados (branch `rpg-online-2026-06-22`):**
- `systems.py::EnemyAISystem._select_target()`: filtra `PlayerControlled`
  por `MapLocation.map_file == mob._map_filter`
- `systems.py::EnemyAISystem.update()` (any_player_exists e sleep-check):
  mesmo filtro; lazy imports de `MapLocation` dentro do loop migrados pra
  uso do import global
- `systems.py::EnemyAbilitySystem.update()`: `player_tiles` filtrado por mapa
- `systems.py::SpawnZoneSystem.update()`: culling de player filtrado por mapa
- `server/session.py::in_aoi`/`in_aoi_exit`: predicado corrigido para
  `if not _ml or _ml.map_file != _my_map` (entidades sem MapLocation excluídas)

**Cleanup arquitetural junto com o fix:**
- Todos os lazy imports `from components import MapLocation as _ML_*` dentro
  de métodos de `systems.py` (10 ocorrências com 7 aliases distintos)
  eliminados — `MapLocation` agora é import de topo-de-arquivo
- `_eid_to_map` removido de `server/world_server.py` (P3 completo):
  era segunda fonte de verdade redundante com `MapLocation`; eliminado de
  todas as 5 escritas (spawn, transfer, despawn, mob-register, snapshot)
  e da única leitura (`server/respawn_system.py`, migrado para `MapLocation`)
- `player_entity_id` removido de `EnemyAISystem.__init__` e
  `EnemyAbilitySystem.__init__` (atributo morto, nunca usado no corpo dos
  sistemas — vestigio do single-player onde havia só um player)
- Aliases mortos removidos de `WorldServer`: `_systems`, `_enemy_ai_system`,
  `_enemy_ab_system`, `_combat`

**Teste atualizado (`tests/test_map_filter.py`):**
- `test_spawned_mob_map_location_matches_eid_to_map` (comparava MapLocation
  com `_eid_to_map`, agora inexistente) → substituído por
  `test_spawned_mob_map_location_is_a_loaded_map` (verifica que todo mob
  tem MapLocation apontando para um mapa carregado no servidor). 10/10 passing.

---

## Bug — Tiro Múltiplo: direção capturada no keypress em vez da conclusão do cast — 01/07/2026

**Sintoma:** Cone de Tiro Múltiplo calculado pelo servidor com a direção do mouse no keypress,
não na conclusão do cast. A mira visual rotacionava corretamente durante o cast, mas o
dano era aplicado na direção inicial — o que o jogador via diferia do que acertava.

**Causa raiz:**
- `systems.py::SkillSystem._use_skill_visual_only` enviava `CAST_SKILL {dir_x, dir_y}` imediatamente
  ao apertar a tecla (mouse capturado naquele instante)
- `server/skill_processor.py` injetava `_server_dir_x/_server_dir_y` no `TileMovement` do `CAST_SKILL`
- `skill_handlers.py::_skill_tiro_multiplo` enfileirava `{dir_x, dir_y}` em `_pending_spell_completions`
  com a direção INICIAL
- `server/spell_completion_processor.py::_complete_tiro_multiplo_cast` usava essa direção armazenada
  na conclusão do cast — mas podiam ter passado `cast_time` segundos desde o keypress

**Fix — novo protocolo `CAST_DIR_UPDATE` (C→S):**
- `spell_system.py::SpellCastSystem._apply_tiro_multiplo`: captura `_dir_x/_dir_y` do mouse
  ATUAL (conclusão do cast) e appenda em `self.pending_dir_updates`
- `spell_system.py::SpellCastSystem.__init__`: `pending_dir_updates: list[tuple[str, float, float]] = []`
- `game.py` (~linha 1470): drena `pending_dir_updates` e envia `CAST_DIR_UPDATE {sid, dir_x, dir_y}`
  (mesmo padrão de `interrupted_visual_casts`/`CANCEL_CAST`)
- `shared/messages.py`: `CAST_DIR_UPDATE = "cast_dir_update"` adicionado
- `server/session.py::_handle_cast_dir_update`: encontra entrada pendente do player em
  `_pending_spell_completions` e atualiza `dir_x/dir_y` com a direção final
- Registrado em `_HANDLERS` ao lado de `CANCEL_CAST`

**Race condition residual (aceitável):** Se `CAST_DIR_UPDATE` chegar APÓS o timer do servidor
expirar (latência alta), o servidor usa a direção inicial do keypress. Ocorre só em latências
extremas e é muito melhor que o bug original onde SEMPRE usava a direção errada.

**Atualização — 19/09/2026: a suposição acima estava errada, a corrida acontecia sempre,
não só em latência alta.** Usuário reportou de novo: "a mira está sendo considerada a
direção do mouse no momento de apertar a skill, e não no final do cast" — ou seja, o
fallback (direção do keypress) estava vencendo na prática, não só em caso raro.

**Causa raiz do "sempre" (não "às vezes"):** o timer do SERVIDOR (`entry["timer"]`,
inicia quando o `CAST_SKILL` original chega) termina em `L + cast_time` (`L` = latência
de 1 salto de rede). A correção do CLIENTE (`CAST_DIR_UPDATE`) só é calculada quando o
cast LOCAL termina (`cast_time` após o keypress, sem rede) e precisa de mais 1 salto pra
chegar ao servidor — chega em `cast_time + L`. **Os dois caminhos chegam ao servidor
matematicamente no MESMO instante** (`L + cast_time == cast_time + L`) — não é uma
corrida rara de latência alta, é um empate estrutural, decidido por qualquer diferença
pequena de overhead (tick do servidor a 30Hz = até 33ms de folga; frame do cliente a
60fps = até 16ms) — o servidor tinha boas chances de vencer o empate mesmo em LAN.

**Fix (opção escolhida pelo usuário entre 2 propostas — a outra era o servidor esperar
a confirmação de verdade em vez de um timer cego, descartada por mexer mais na lógica
de conclusão de cast):** `TIRO_MULTIPLO_DIR_UPDATE_GRACE_MS = 150`
(`shared/constants.py`, ao lado de `LAG_COMP_WINDOW_MS`) somado ao `cast_time` só no
`entry["timer"]` de `tiro_multiplo`
(`engine/skill_handlers.py::_skill_tiro_multiplo`) — dá 150ms de folga real pro
`CAST_DIR_UPDATE` chegar antes do servidor resolver os alvos do cone. Se mesmo assim
não chegar (lag extremo/desconexão), cai no mesmo fallback de sempre (direção do
keypress) — nenhuma mudança nesse caminho. Não afeta a barra de cast visual do cliente
(`SpellCast.cast_time` no cliente continua o valor original do catálogo, só o timer
INTERNO do servidor ganhou a folga) — só atrasa ~150ms quando as flechas
entram em voo/o cooldown é aplicado no servidor, imperceptível.

Validado: `pytest tests/ -k "tiro_multiplo or spell_completion or skill_handlers or
cast_dir"` (3 passed). `tests/diag_tiro_multiplo.py` está quebrado desde antes desta
mudança por outro motivo (rejeição de autorização por talento — o script seta
`cs.tiro_multiplo_targets` direto sem alocar o talento de verdade), confirmado
revertendo a mudança e reproduzindo o mesmo erro — não é regressão desta correção,
fica registrado como débito à parte, fora de escopo.

**Validado em jogo (19/09/2026):** primeiro teste do usuário foi contra o servidor
de produção ANTES do deploy (código antigo ainda rodando lá) — pareceu não ter
resolvido. Após `release_tools/deploy_server.ps1` (commit `97bdbbd`), usuário
confirmou "agora está validado". **Decisão nova do usuário nesta mesma sessão**:
deploy pra produção passa a ser automático depois de qualquer correção — o
servidor de produção hoje só serve pra desenvolvimento (jogo ainda não lançado,
sem jogadores reais), não precisa mais de confirmação a cada vez.

---

## Feature — Variante melee/ranged por raça de mob (`alt_variant`) — 07/07/2026

**Pergunta do usuário:** zona de spawn de "Goblin" no `map_1_entities.json` tem entradas
`{"type": "melee", ...}` e `{"type": "ranged", ...}` — mas só nascem Goblins ranged na área.

**Causa raiz:** `map_loader.py` (linha ~296-311) lê cada zona e, se a entrada de spawn não tiver
`"race"`/`"class"` própria, usa o `race`/`class` da ZONA inteira pra todas as entradas — inclusive
as marcadas `"melee"`. Isso por si só é só um detalhe de herança; o problema real é em
`entity_factory.py::create_enemy` (linha ~205-219): assim que `race` bate com uma entrada em
`MOB_TABLE` (mob_definitions.py), `entity_class`/`is_ranged` são **sobrescritos** pelos valores
FIXOS daquela raça, descartando o `is_ranged` calculado a partir do `"type"` do JSON. Como só
existia UM arquétipo de "Goblin" (sempre `entity_class="Hunter", is_ranged=True`), toda entrada
`"melee"` da zona virava ranged do mesmo jeito — o campo `"type"` só criava "baldes" de contagem
separados, sem nenhum efeito real no resultado. Isso só afeta raças cadastradas em `MOB_TABLE`;
raças genéricas sem entrada (ex. `"Elemental"`) usam o `"type"` normalmente (fallback pros 2
templates antigos por `is_ranged`).

**Fix — `alt_variant` (opcional) em `MOB_TABLE`:** uma entrada pode apontar pra outra raça a usar
quando o `"type"` pedido não bate com seu próprio `is_ranged` fixo:
- `entity_factory._resolve_mob_race_variant(race, want_ranged)`: se `MOB_TABLE[race].is_ranged`
  já bate com `want_ranged`, devolve `race` sem mudança. Se não bate, olha
  `MOB_TABLE[race]["alt_variant"]` — se essa raça alternativa existir E bater com `want_ranged`,
  usa ela. Sem `alt_variant` (ou variant que também não bate) — devolve a raça pedida sem
  mudança, **fallback pro que já existe** (nunca quebra, nunca fica sem mob).
- Chamado logo no início de `create_enemy`, reatribuindo a variável local `race` — isso propaga
  automaticamente pro nome de exibição (`EntityIdentity.name`), loot (`roll_mob_loot` é keyado
  pelo nome), xp e sons, já que todos usam a MESMA variável `race` resolvida.
- `mob_definitions.py`: "Goblin" (ranged, Hunter) ganhou `"alt_variant": "Goblin Guerreiro"`; nova
  entrada "Goblin Guerreiro" (melee, Warrior, stats/loot adaptados) ganhou `"alt_variant":
  "Goblin"` simétrico. Nenhuma mudança necessária no JSON dos mapas — o `"type"` que já estava lá
  passa a ter efeito de verdade.

Validado (headless): `_resolve_mob_race_variant` nos 5 cenários (Goblin+melee→Goblin Guerreiro,
Goblin+ranged→Goblin, Goblin Guerreiro+ranged→Goblin, Lobo+ranged sem alt_variant→Lobo sem
mudança, raça genérica fora de MOB_TABLE→sem mudança) + `create_enemy` end-to-end criando
`AIControlled.entity_class`/`is_ranged` corretos conforme o `"type"` pedido. Suíte sem regressão
(9F/83P, mesmo baseline).

**Não validado:** passada manual — visitar a zona de Goblin no mapa 1 e confirmar visualmente
Goblins de espada (Warrior, melee) misturados com os de arco (Hunter, ranged).

---

## Feature — Restrição de arma/escudo/aljava por classe (`is_weapon_allowed_for_class`) — 07/07/2026

**Reportado pelo usuário:** guerreiro conseguia equipar um Arco e atacar corpo-a-corpo com ele,
inclusive subindo skill_level de arco, sem nunca disparar uma flecha de verdade — "não faz
sentido". Causa raiz: `CLASS_ARMOR_ALLOWED` (material de armadura) já existia desde antes desta
sessão, mas **nunca existiu equivalente pra arma/escudo/aljava** — qualquer classe sempre pôde
equipar qualquer arma, sem nenhuma validação (nem cliente, nem servidor).

**Fix — `stats_system.is_weapon_allowed_for_class(item, class_id)`** (mesmo padrão de
`CLASS_ARMOR_ALLOWED`, único ponto de verdade — client `_equip_item` pra feedback imediato,
servidor `update_player_equipment` autoritativo, ambos chamando a mesma função):
- **Guerreiro** — só não pode Bow (mainhand) nem quiver (offhand). Todo o resto, incl.
  Wand/Staff/Scepter, é permitido e vira arma melee (mesmo `deal_damage("physical")` +
  `weapon_skill_level` que já existia pro fallback do arqueiro).
- **Arqueiro** — só não pode escudo (offhand). Bow ativa ranged; qualquer outra arma vira melee
  (dispatcher já implementado numa sessão anterior).
- **Mago** — sem Bow/quiver, sem Axe/Mace/Hammer/Club (armas físicas pesadas). Sword só se for de
  UMA mão (`two_handed=False`); Dagger sempre permitida. Wand/Staff/Scepter sempre permitidas —
  Staff/Scepter são de duas mãos por definição do próprio item, então bloqueiam escudo sozinhas
  via `is_offhand_locked()` já existente, sem precisar de regra extra aqui.

**Achado no caminho — `item_table.py`:** "Cetro do Lich" (subtype Scepter) não tinha
`two_handed=True`, quebrando a regra "Staff e Scepter são sempre de duas mãos" confirmada pelo
usuário. Corrigido (mesma classe de inconsistência de dado que "Espada de Ferro"/"Grevas de
Ferro"/"Luvas de Couro" divergentes, já vista antes nesta sessão).

**Achado no caminho — tooltip do escudo:** `ui_helpers.py::item_tooltip_lines` tinha branches pra
`weapon` e `armor`, mas `shield` caía no `else` genérico (`"Slot: offhand"`) sem mostrar
"Escudo" — diferente de espada/arco, que sempre mostram o subtype à direita. Adicionado branch
`elif item.item_type == "shield"`, com a mesma cor verde/vermelho por classe que material de
armadura já usa (agora relevante: arqueiro não pode escudo).

Validado (headless): `is_weapon_allowed_for_class` em 22 combinações (3 classes × Bow/quiver/
Sword/Wand/Axe/Mace/Hammer/Club/Dagger/Staff/Scepter/shield, incl. Sword duas-mãos pro mago) +
`update_player_equipment` end-to-end (guerreiro+Arco rejeitado, guerreiro+Espada aceito,
arqueiro+Escudo rejeitado, mago+Machado rejeitado, mago+Cetro do Lich aceito com two_handed
correto). Suíte sem regressão (9F/83P, mesmo baseline).

**Não validado:** passada manual — tentar equipar item fora da classe em cada uma das 3 classes
e confirmar a reversão de UI (slot volta pra bag) + mensagem de aviso.

## Feature (trade) — Stack de item errado na negociação + gold "zerava" ao confirmar — 08/07/2026

**Reportado pelo usuário:** ofertar um stack de 150 flechas no trade descontava as 150 da própria
bag, mas o player remoto recebia só 1 flecha. E digitar um valor de gold e clicar em "Negociar"
zerava o campo sem nada acontecer com o gold na troca.

**Causa raiz 1 (stack):** `WorldServer._item_data_from_obj` (usado por `BUY_RESULT` e agora
também por `build_trade_state_payload`/`TRADE_RESULT`) serializava `max_stack` mas nunca `stack`
— o ECS do servidor sempre moveu o objeto `Item` real (stack correto), o bug era só na
serialização pro cliente: `_item_from_data` (client/save_sync_handlers.py) cai no default
`stack=1` quando a chave não vem no dict. Fix: adicionado `"stack": getattr(obj, "stack", 1)`
em `_item_data_from_obj`. `client/trade_handlers.py` também nunca chamava `draw_stack_count` nos
slots de trade — corrigido junto.

**Causa raiz 2 (gold):** `client/trade_handlers.py::_click_trade_window` só mandava
`TRADE_SET_GOLD` ao servidor quando o player apertava Enter no campo — clicar direto em
"Negociar" sem apertar Enter antes só desfocava o campo (`_trade_gold_focus = False`) e
descartava o texto digitado, nunca enviando o valor. A troca então sempre executava com
`gold_a/gold_b = 0` (nunca setados), e o campo "voltava a 0" porque nenhum `TRADE_STATE` novo
jamais confirmava um valor diferente. Fix: clicar em "Negociar" (ou em qualquer lugar fora do
campo) agora chama `_confirm_trade_gold()` primeiro se o campo estava focado, antes de processar
o clique restante.

Validado (headless): `_item_data_from_obj` inclui `stack` corretamente; item recebido no ECS do
servidor E reconstruído via `_item_from_data` no cliente preserva `stack=150`. Suíte sem
regressão (9F/83P).

**Não validado:** passada manual com 2 clientes reais (renderização da contagem "x150" no slot,
digitar gold e clicar em Negociar sem apertar Enter).

## Bug — Relogar após morrer trazia o personagem "vivo" em posição arbitrária + corpo órfão pra sempre — 08/07/2026

**Reportado pelo usuário:** morrer, deslogar e logar de novo trazia o personagem vivo, mas o
corpo (marcador visual no local da morte/liberação de espírito) continuava lá pra sempre, visível
pra outros players.

**Causa raiz:** 2 lacunas independentes. (1) `despawn_player` (chamado no disconnect) só remove a
entidade REAL do player do ECS — nunca limpava `_player_corpses[eid]` nem despawnava o marcador
sintético `PLAYER_CORPSE_EID_BASE+eid` criado por `_handle_release_spirit`; só `_revive_player`
fazia essa limpeza, e ela só roda em fluxo normal (auto-revive no cemitério / revive no corpo),
nunca no disconnect. (2) `GhostState` não é persistido — `spawn_player` sempre cria um
`GhostState()` fresco (vivo) no relogin; como `get_player_save_data` salva `max_hp` quando o
player está morto (current_hp=0), o relogin trazia HP cheio na posição CRUA salva — que pode ser
o local da morte (cheio de mob) ou qualquer lugar que o fantasma tenha vagado (intangível,
atravessa parede).

**Fix — `RespawnMixin._auto_revive_on_disconnect(player_eid)`** (server/respawn_system.py),
chamado em `session.py::on_disconnect` ANTES de coletar dados de save: se `GhostState.is_dead`,
força posição pro `RESPAWN_TILE` (nunca revive "in place" — é exatamente o cenário
perigoso/exploitável) + troca de mapa se necessário (mesmo bloco de `_handle_release_spirit`) e
reaproveita `_revive_player` (já reseta `GhostState`, restaura HP cheio, limpa `_player_corpses` +
enfileira despawn do marcador pro AOI). Nenhum campo novo precisou ser persistido — o char
simplesmente já está "vivo e revivido no cemitério" no ECS por ocasião do save, então
`get_player_save_data`/`_build_save_merge` gravam o estado correto sem mudança nenhuma neles.

Considerado e descartado: persistir o `GhostState` completo (is_ghost/corpse_tx/ty/timers) e
devolver o player como espírito no relogin — mais fiel ao fluxo real, mas exige um caminho novo
pro cliente já nascer com a UI de espírito ativa no login (hoje só é empurrada reativamente após
`RELEASE_SPIRIT`/mudança de tick) para uma diferença de UX pequena. Descartado por decisão do
usuário (opção mais simples escolhida).

Validado (headless, 3 cenários): morto sem liberar espírito desconecta → revive no cemitério com
HP cheio, corpo limpo; fantasma vagando longe desconecta → mesmo resultado; player vivo
desconecta → no-op (não mexe em nada). Suíte sem regressão (9F/83P).

**Não validado:** passada manual com 2 clientes reais (confirmar que o corpo desaparece
imediatamente da tela do OUTRO player ao primeiro desconectar morto/fantasma).

## Bug — Fantasma esperando no cemitério nunca revivia automaticamente — 08/07/2026

**Reportado pelo usuário:** ao morrer, o player pode correr até o corpo (revive parcial) ou
esperar no cemitério pelo revive automático (`GHOST_GRAVEYARD_REVIVE_S` = 45s) — mas esperando,
nunca revivia.

**Causa raiz — desync entre o contador exibido e o contador real:** `_tick_ghost_states`
(server/respawn_system.py) já funcionava perfeitamente sozinho (validado headless, isolado E via
`_tick()` completo, 75s simulados) — o timer real (`GhostState.graveyard_timer`) conta certinho e
reseta pra 0 sempre que o fantasma sai do raio de `GHOST_GRAVEYARD_RADIUS_TILES` (5 tiles) do
`RESPAWN_TILE`. O bug é 100% client-side: `death_ui_handlers.py::_update_death_ui` incrementa
`self._ghost_timer` (o número mostrado em "revive automático em Xs") **sem checar raio nenhum** —
só olha `gst.is_ghost`. E o servidor só mandava `GHOST_STATE` quando `near_corpse` mudava, nunca
quando o `graveyard_timer` real zerava por ter saído do raio. Resultado: o player anda um pouco
enquanto espera (fantasma não é travado, nada de anormal em se mexer), o servidor reseta o
contador em silêncio, mas o cliente continua contando pra baixo do jeito dele até chegar em "0s" —
e nunca reviva de verdade, porque o servidor nunca chegou nem perto.

**Fix:**
- `server/respawn_system.py::_tick_ghost_states` — agora também dispara `GHOST_STATE` quando o
  `graveyard_timer` **reseta** (estava > 0, foi pra 0 por sair do raio), não só quando
  `near_corpse` muda.
- `client/network_handlers.py::_handle_msg_ghost_state` — resincroniza `self._ghost_timer` (o
  contador local de exibição) com `payload["graveyard_timer"]` toda vez que um `GHOST_STATE`
  chega, em vez de deixá-lo rodar 100% independente do servidor.

Validado (headless, 3 cenários): acumula tempo dentro do raio → sai do raio (timer zera + exatos
1 `GHOST_STATE` disparado avisando) → volta pro raio e completa o tempo → revive normalmente.
Suíte sem regressão (9F/83P).

**Não validado:** passada manual (confirmar visualmente que o texto "revive automático em Xs"
volta a mostrar ~45s ao sair e reentrar no cemitério, em vez de continuar contando pra 0).

## Bug — Texto e sprites de tile com antialiasing (visual borrado, jogo é pra ser pixelizado) — 08/07/2026

**Reportado pelo usuário:** letras do balão de chat (e de outros lugares, ao olhar melhor) ficavam
levemente desfocadas — incompatível com o visual pixel-art pretendido do jogo.

**Causa raiz 1 (texto):** `fonts.py::CachedFont.render()` tinha `antialias=True` como default, e
**todos os ~330 call-sites de `.render()` do cliente (30 arquivos)** passavam `True` explicitamente
— nenhum usava `False`. Confirmado via scan AST completo do repositório (não só grep — regex de
uma linha falha silenciosamente em casos como `render(f"Tile: ({hx}, {hy})", True, cor)`, onde a
vírgula dentro do próprio argumento de texto quebra um regex ingênuo; usar `ast.parse` +
`ast.walk` procurando `Call` cujo `func.attr == "render"` é a forma correta de auditar isso sem
falso-negativo).

**Fix:** troca mecânica de `True`→`False` no 2º argumento posicional (ou `antialias=`) em TODOS os
call-sites, mais o default de `CachedFont.render()` (agora `antialias=False`).

**Achado no caminho — armadilha de performance:** `CachedFont` só cacheava o `render()` quando
`antialias=True` (`if background is not None or not antialias: return super().render(...)` —
bypass completo do cache pra `antialias=False`). Trocar todos os call-sites pra `False` sem também
corrigir essa condição teria **desativado o cache inteiro** silenciosamente — exatamente o spike de
13-22ms/frame que o cache foi criado pra resolver (ver docstring de `fonts.py`). Corrigido: cache
agora aplica sempre que não há `background` (chave inclui `antialias` pra não misturar variantes
caso algum call-site volte a usar `True` no futuro).

**Causa raiz 2 (sprites de tile):** `tile_sprite_manager.py::_get_scaled()` usava
`pygame.transform.smoothscale` (interpolado, borra) em vez de `pygame.transform.scale`
(nearest-neighbor, preserva pixel art) ao redimensionar um PNG pro tamanho alvo em tile. `god_mode.py`
(editor de nível, acessível via F10 no jogo real) tinha o mesmo problema em 3 lugares (swatches de
paleta de tile). Ambos trocados pra `scale`. `icon_manager.py` (ícones de item) e o pipeline de zoom
de câmera (`game.py`, `pygame.transform.scale` no `_zoom_surf`) já usavam `scale` — não precisaram
de mudança.

**Fora de escopo (decisão do usuário):** cantos arredondados de painel (`border_radius`, ~258
usos) — suavizados por design do próprio pygame ao desenhar a curva, mas é uma escolha visual
(quina reta vs arredondada), não um bug técnico de antialiasing. Deixado como está.

Validado: scan AST confirma zero `.render(...)` com `antialias=True` restante em todo o repo
(excluindo um worktree não-relacionado de outro agente, `.claude/worktrees/...`, fora do escopo
desta sessão); zero `smoothscale` restante. Compile-check de todos os ~32 arquivos tocados + suíte
completa sem regressão (9F/83P).

**Não validado:** passada visual com o jogo rodando (confirmar que texto e tiles realmente
aparecem nítidos/pixelizados, sem survivor de cache stale de fontes antigas).

## Bug — Nameplate/chat/trade mostravam o LOGIN da conta em vez do nome do personagem — 08/07/2026

**Reportado pelo usuário:** balão de chat não aparecia numa conta ("teste") mas aparecia em outra
("juugo"); investigando, achou também que players remotos sempre mostraram o login da conta como
nome acima da cabeça (não o nome do personagem), mesmo antes do chat existir — pediu pra corrigir
os dois juntos.

**Causa raiz (única, 6 pontos de sintoma):** `server/session.py` mandava `session.username` (LOGIN
da conta) como `"name"`/`"sender"` em 6 lugares diferentes — nameplate (`ENTITY_SPAWN` no login +
`WORLD_STATE` inicial + `AOI_UPDATE` de player entrando no raio), `CHAT_MESSAGE.sender`, e
`TRADE_INVITE.from_name`/`TRADE_OPEN.other_name` (introduzidos na sessão do trade) — nunca o nome
do PERSONAGEM (`char_data["name"]`, escolhido na criação). Só "funcionava" por coincidência quando
o jogador escolhia os dois iguais (explica por que "juugo" funcionava e "teste" não). O balão de
chat especificamente também tinha uma 2ª camada do mesmo bug no CLIENTE:
`_resolve_chat_sender_entity` comparava `sender_name` contra `_logged_char_name` (nome do
personagem) — certo em teoria, mas como o servidor mandava o LOGIN, a comparação só batia por
coincidência (mesma raiz, lado espelhado).

**Fix — fonte única no servidor:** `Session.display_name` (property nova) — `char_data.get("name")
or username` (fallback defensivo). Os 6 pontos agora usam `session.display_name`/
`s2.display_name`/`other_session.display_name`/`requester_session.display_name` em vez de
`.username` cru. Client-side, `_resolve_chat_sender_entity` mantido comparando contra
`_logged_char_name` (nome do personagem) — agora corretamente consistente, já que o servidor
sempre manda nome de personagem.

Validado (headless): `Session.display_name` retorna o nome do personagem quando `char_data` tem
`"name"`, cai pro `username` só se faltar; resolução do balão de chat confirmada consistente nos
dois lados (servidor manda nome do personagem, cliente compara com nome do personagem). Suíte
completa sem regressão (9F/83P).

**Não validado:** passada manual com 2 clientes reais em contas onde login ≠ nome do personagem —
confirmar nameplate, chat e trade mostrando o nome do personagem em todos os casos.

## Bug — Tiro Repulsivo (arqueiro) stunava o PRÓPRIO atirador quando o alvo estava adjacente — 09/07/2026

**Reportado pelo usuário:** ao usar Tiro Repulsivo num alvo adjacente, o arqueiro tomava stun da
própria skill — sem sentido, já que a flecha empurra o alvo pra LONGE do atirador (direção
correta, `dx,dy = tile_alvo - tile_atirador_no_disparo`), e o próprio atirador nunca deveria ser
vítima do próprio knockback (só faria sentido em PvP, quando OUTRO player dispara nele).

**Causa raiz:** `server/spell_completion_processor.py::_server_tiro_repulsivo`. Quando o alvo
adjacente colide (0 tiles percorridos — ex.: encostado numa parede ou noutro mob logo atrás),
o ponto de colisão é o próprio tile onde o alvo já estava, ou seja, ainda adjacente ao arqueiro.
O splash de stun em área (`_adjacent_creatures`, Chebyshev ≤ 1 do ponto de colisão — pra pegar
mobs agrupados perto do impacto) só excluía `{target_id, collided_eid}`, nunca `player_eid` — o
próprio atirador, estando a distância 1 do ponto de colisão (ele mesmo é quem estava adjacente ao
alvo), caía na busca e recebia o stun da própria flecha. Mesma classe de bug, mais estreita,
também existia na detecção de bloqueador (`_entity_at_tile`, chamada sem `exclude_eid`): se o
atirador se movesse durante o voo da flecha e acabasse na trajetória do empurrão, viraria
`collided_eid` e seria stunado igual.

**Fix:** `player_eid` adicionado ao `exclude_eids` do splash (`_adjacent_creatures`) e passado como
`exclude_eid` nas 3 chamadas de `_entity_at_tile` que detectam o bloqueador do knockback — o
atirador nunca mais pode ser `collided_eid` nem entrar no splash da própria skill.

Validado (headless, `tests/diag_tiro_repulsivo.py` + script novo reproduzindo alvo adjacente +
bloqueador logo atrás → colisão a 0 tiles): confirmado que o bug reproduzia (`player_stunned=True`)
antes do fix e sumiu depois (`player_stunned=False`, alvo e bloqueador continuam stunados
normalmente). Suíte completa sem regressão (9F/83P).

**Não validado:** cenário PvP real (outro player disparando Tiro Repulsivo em alguém adjacente a
uma 3ª entidade) — confirmar que o splash ainda pega bystanders de verdade, só o CASTER que fica
sempre de fora.

---

## 11. AUDITORIA ARQUITETURAL AMPLA — 15 de julho de 2026

Análise minuciosa (pedido do usuário): ECS core, componentes, sistemas,
gerenciamento de entidades, rede, dados e processo. Checklist de "feito de um
jeito, mas existia um jeito muito melhor". Itens já documentados nas seções
1-10 não são repetidos — isto é o que SOBRA depois de tudo já consertado.

### 🔴 A1 — Componentes-Deus: `CombatStats` (~120 campos) e `CharacterStats`

`CombatStats` mistura stats genéricos de combate com flags de TALENTO
específicas de cada build de cada classe (`fire_burns_on_crit`,
`pnq_enabled`, `interceptar_rage_bonus`, `flechas_despadronizadas_chance`,
`camouflage_timer`, `na_mosca_bonus_active`, ~40 flags assim). Todo mob do
servidor carrega os campos de talento de Piromania do Mago. Cada talento novo
= editar o componente compartilhado por TODAS as entidades. `CharacterStats`
idem: rage + mana + concentration + cargas de Embalo/PnQ/fatiador juntos.

**Melhor:** o ponto do ECS é composição — recursos de classe deviam ser
componentes próprios (`Rage`, `Mana`, `Concentration`) adicionados só a quem
tem, e flags de talento deviam viver num componente por build
(`PyromaniaTalents`, `ArcherTalents`) criado quando o talento é alocado, ou
num dict genérico `talent_flags` populado por `apply_talent_effects()`.
Custo de migrar hoje: alto (dezenas de call sites) — fazer por classe, na
próxima vez que a build daquela classe for mexida.

### 🔴 A2 — Dual-mode online/offline: 65 branches `if self._net` espalhados

`ui/systems.py`, `ui/spell_system.py`, `ui/quest_system.py`,
`engine/world_systems.py` e `game.py` decidem "roda local ou espera servidor"
com `if self._net:` caso a caso. É a maior fonte única de bugs da história
recente (predição de flecha, mana/rage regen local duplicado, aljava
dessincronizada, timer de Só um Gole duplicado — todos da mesma classe).

**Melhor:** uma interface de autoridade (`LocalAuthority`/`RemoteAuthority`)
injetada uma vez, com os pontos de decisão CENTRALIZADOS (aplicar dano?
descontar recurso? criar projétil?), em vez de cada mecânica reimplementar a
escolha. Alternativa mais barata: matar o modo offline do branch online (o
offline já vive no `rpg_ecs/` master) — metade dos branches morre.

### 🔴 A3 — `_svc` global mutável + `register_map_services_for()`

Service locator global (`world_systems._svc`) aponta pro bundle do "mapa
atual"; com multi-mapa no servidor, TODO entry-point novo tem que LEMBRAR de
chamar `register_map_services_for(player_eid)` antes do handler (classe de
bug já materializada 2x: Interceptar "bloqueado" em terreno aberto, Tiro
Repulsivo stunando em parede fantasma — ver CLAUDE.md). É contexto implícito
que depende de disciplina.

**Melhor:** contexto explícito — handlers recebem o bundle do mapa como
parâmetro (ou serviços por-World em vez de globais de módulo). Enquanto isso
não acontece, a regra do CLAUDE.md é a única defesa.

**✅ MITIGADO (15/07/2026) — resolver por-entidade.** Novo
`world_systems.register_service_resolver(fn)`: o WorldServer instala (ao fim
de `_load_all_maps`) um resolver `eid → bundle do mapa da entidade`
(via `get_entity_map`/MapLocation). `is_tile_walkable()` de módulo — que já
recebe `entity_id` como 1º argumento — resolve o bundle CERTO por chamada,
automaticamente; `register_map_services_for()` deixa de ser ponto único de
falha pra essa função. Cliente nunca instala resolver (mapa único, `_svc`
como sempre). `find_path()`/`get_tilemap()` não têm eid na assinatura —
pra essas a regra do register no entry point CONTINUA obrigatória
(CLAUDE.md); a defesa automática cobre a função mais chamada pelos handlers
compartilhados (Interceptar/knockback/walkability de dash).

Validado: teste dirigido reproduzindo a classe de bug real — `_svc`
deliberadamente apontado pro bundle da caverna (80×60) e
`is_tile_walkable(player_de_map_1, 131, 374)`: True com resolver (valida
contra map_1), False sem (fora dos limites da caverna = o bug antigo) —
+ suíte 92/92 verde.

### 🔴 A4 — Persistência com autoridade híbrida (inventário/talentos client-side)

`_build_save_merge` decide campo a campo quem manda (server: pos/hp/quests/
skill_levels/equipment; CLIENTE: inventory/talents) usando o cache
`session.last_client_payload` — que já causou o bug da aljava (Decisão 26,
ARQUITETURA_ONLINE.md) e continua sendo vetor de item-duplication via cliente
modificado, mesmo com `_reconstruct_item` validando contra catálogo.

**Melhor:** inventário 100% server-authoritative (o servidor JÁ mantém
`Inventory` em memória e já tem INV_SYNC; falta inverter a direção: cliente
pede mutação, servidor aplica e ecoa). Talents idem (budget já é validado —
falta o conteúdo da árvore). Eliminaria `last_client_payload` inteiro.

**✅ MITIGADO (15/07/2026) — sanitização na borda de persistência.** A
inversão completa do protocolo (cliente pede mutação, servidor aplica)
continua como plano; o que foi FECHADO agora é o vetor de save-forging:

- `WorldServer.sanitize_inventory_payload()` (novo): round-trip de cada
  item do payload pelo catálogo autoritativo (`_reconstruct_item` →
  `_item_data_from_obj`) — o que persiste é o item do CATÁLOGO (stats/
  modifiers/valor reais) + bookkeeping clampado; item de nome fora dos 3
  catálogos (loot/loja/forja) é DESCARTADO, nunca salvo.
- Ligado nas DUAS bordas onde inventário do cliente entra em persistência:
  `_handle_save_state` (antes de cachear em `last_client_payload`) e
  `_handle_inventory_update`/INV_SYNC (cache + ECS agora recebem a versão
  sanitizada).
- `_reconstruct_item._apply_client_bookkeeping`: cliente NÃO sobrescreve
  mais `max_arrows`/`max_stack` (deixava forjar capacidade — aljava de
  999999 flechas); `arrow_count`/`stack` agora são clampados contra a
  capacidade do CATÁLOGO. Nenhuma mecânica legítima muda capacidade em
  runtime (única mutação real: fallback legado `max_arrows==0→100`).
- Fix de quebra descoberto no caminho: `_build_item_caches` só cobria
  loot+loja — item FORJADO (crafting) ficava fora do `_item_value_cache`,
  então `process_shop_sell` vendia item craftado pelo branch "desconhecido"
  (client_value com teto 500, errado/manipulável) e o sanitizador novo
  descartaria item craftado legítimo. Cache agora cobre os 3 catálogos.

Validado: teste headless (item legítimo de CADA catálogo sobrevive com
stats do catálogo; item forjado com modifiers/value absurdos descartado;
stack estourado clampado; entrada não-dict ignorada; None/não-lista →
None) + suíte completa 92/92 verde.

Talentos já eram validados na borda (`validate_talent_allocation` em
`_handle_save_state`) — sem mudança.

**Não validado:** sessão manual online (lootar/comprar/forjar itens,
deslogar, relogar e conferir que a bag volta idêntica — em especial itens
CRAFTADOS, que dependem do fix do cache).

### 🟡 B1 — Orquestração de frame/tick manual e gigante

`game.py::run()` (~800 linhas) e `WorldServer._tick()` (~600 linhas) chamam
cada sistema hard-coded, com 40+ blocos repetidos de
`if PROFILE_FRAMES: _ts = perf_counter()` inline.

**Melhor:** lista ordenada declarativa de sistemas
(`PIPELINE = [(nome, fn), ...]`) com loop único que já cronometra cada etapa
— o profiler manual inteiro (e o rótulo mentiroso `hud:combat_log`, que
atrasou o diagnóstico do minimapa em 14/07) desaparece de graça, e adicionar
sistema vira 1 linha em vez de cirurgia num método gigante.

### 🟡 B2 — Handlers de spell duplicados cliente/servidor por skill

Cada skill de projétil tem DUAS implementações espelhadas:
`ui/spell_system.py::_apply_*` (offline/visual) e
`server/spell_completion_processor.py::_server_*` (autoritativa).
Divergências entre as duas são classe recorrente de bug (C20, consumo de
flecha da Decisão 26, knockback...).

**Melhor:** completar o padrão que `world_systems`/`core_systems`/`fx.py` já
começaram — UM handler headless por skill, efeitos visuais via façade,
chamado pelos dois lados. O doc já chama isso de "nó restante do problema F";
vale promover a prioridade: é onde os bugs de skill nascem.

### 🟡 B3 — Servidor reusa o `SkillSystem` do cliente via `getattr(f"_skill_{sid}")`

`skill_processor.py` muta `self._skill_system.player_entity_id = player_eid`
a cada request e despacha por convenção de nome numa classe de UI
(`ui/systems.py`). Funciona, mas: estado mutável compartilhado entre
requests, zero checagem estática de handlers, e o servidor importa módulo de
UI.

**Melhor:** registry explícito `SKILL_HANDLERS: dict[str, callable]` headless
(em `engine/` ou `content/`), handlers recebendo contexto como parâmetro em
vez de atributo mutado.

### 🟡 B4 — Protocolo sem schema tipado

Payloads são dicts livres; `queue_stats_update` aceita "qualquer campo
extra"; COMBAT_RESULT acumulou campos ad-hoc. Typo em chave vira bug
silencioso (`payload.get()` com default engole). MsgType é a única parte
tipada.

**Melhor:** TypedDict/dataclass por mensagem em `shared/messages.py` (que já
documenta payloads em comentário — formalizar o que já está escrito) +
validação na borda do servidor. Também prepara a migração JSON→MessagePack já
planejada.

**✅ PARCIALMENTE RESOLVIDO (15/07/2026) — validação runtime na borda.**
`shared/messages.py::C2S_REQUIRED` (26 mensagens C→S: campos NÚCLEO +
tipos) + `validate_c2s()`, chamado por `SessionManager.on_message` ANTES
do dispatch: payload malformado → `ERROR{invalid_payload:...}` + warning
no log, handler nunca roda. Schema PERMISSIVO de propósito (só campos que
o handler assume; numérico aceita int|float; mensagem sem entrada = sem
validação extra — compat por default). Mensagem nova: adicionar entrada
junto (ideal, não obrigatório).

Validado: 7 testes novos (`tests/test_protocol_validation.py` — unidade da
função + integração na borda: MOVE malformado leva ERROR e não move; MOVE
legítimo continua passando) + suíte inteira 117/117 (os testes de sessão
exercitam todos os fluxos reais C→S — nada legítimo rejeitado).

**Continua no plano:** TypedDicts formais por mensagem (documentação
tipada, sem efeito runtime) + validação dos payloads S→C no cliente —
ambos naturais de fazer na migração JSON→MessagePack.

### 🟡 B5 — Sem índice espacial (varreduras lineares por tick)

`_entity_at_tile`, `_adjacent_creatures`, `_sessions_in_aoi`, coleta de
`enemy_tiles`: tudo O(N) sobre todos os mobs+players, por tick. Escala atual
aguenta; 10x mobs/players não.

**Melhor:** dict `tile → set[eid]` mantido por `snap_to_tile`/
`TileMovementSystem` (os únicos pontos que já escrevem posição, por regra do
projeto) — consultas viram O(1), custo de manutenção 1 remove+1 add por
passo.

**✅ REAVALIADO/MITIGADO (15/07/2026):** a auditoria superestimou o caso —
o caminho MAIS quente (`is_tile_walkable`, chamado pelo pathfinding de
todos os mobs) já era O(1) via `TileValidationSystem._occupied` (dict
tile→eid reconstruído 1×/tick por bundle). O que era linear de verdade e
foi convertido: `_entity_at_tile`/`_adjacent_creatures` do knockback
(Tiro Repulsivo) varriam todos os mobs+players POR PASSO do empurrão
(~15 varreduras/uso) — agora constroem um índice `tile → [eids]` UMA vez
por knockback (snapshot seguro: nada além do próprio alvo move durante a
resolução). Validado com teste dirigido de colisão (alvo + bloqueador
atrás: alvo parado, ambos stunados, atirador nunca) + suíte 92/92.
Restante linear (`_sessions_in_aoi` ~dezenas de sessões, `_select_target`
N_mobs×N_players, coleta de enemy_tiles no cliente) é adequado na escala
atual e prevista — o índice incremental global só se justifica se
mobs/players crescerem ~10x; fica como plano, não pendência.

### 🟡 C1 — Auth: SHA-256 sem salt, hash é a senha

Cliente manda SHA-256(senha) em texto pelo WebSocket (sem TLS); o servidor
guarda esse hash direto. Replay do hash = login (pass-the-hash); rainbow
table quebra senhas fracas.

**Melhor (pré-lançamento):** wss:// + salt por conta + argon2/bcrypt no
servidor. Registrado pra não virar "regra esquecida" — hoje o CLAUDE.md
descreve o esquema atual como se fosse o desejado.

**✅ PARCIALMENTE RESOLVIDO (15/07/2026) — salt por conta + upgrade
transparente.** Banco agora guarda `sha256(salt + client_hash)` com salt
aleatório por conta (`secrets.token_hex(16)`): dump do banco não autentica
mais ninguém (pass-the-hash morto — login exige o client_hash, pré-imagem)
nem cai em rainbow table. Contas legadas (salt NULL) são verificadas pelo
esquema antigo e MIGRADAS no próprio login bem-sucedido, sem o jogador
perceber. Comparações via `hmac.compare_digest` (timing-safe). Migração de
schema é lazy (1x por processo em `_get_conn()` + coluna no CREATE TABLE) —
cobre testes/utilitários que nunca chamam `init_db()`. Cliente inalterado
(continua mandando o mesmo client_hash).

Validado: teste dirigido (conta nova salted; login certo/errado; conta
legada migra no login e continua logando; hash vazado do banco NÃO loga)
+ suíte 92/92.

**Continua pendente (produção):** TLS/wss (o client_hash na rede ainda é
"a senha") e argon2/bcrypt no lugar de sha256 — os dois juntos na migração
de infra pré-lançamento.

**✅ TLS/wss implementado (02/10/2026)** — ver `ARQUITETURA ONLINE.md`
§20 pro desenho completo. Nota: a suposição original deste item (TLS
"precisa de domínio pra certificado") era imprecisa — decisão real
tomada com o usuário foi certificado AUTOASSINADO (SAN = IP do
servidor), fixado ("pinned") no próprio cliente via `cafile=`, sem
nenhuma autoridade certificadora pública envolvida — o cliente é
nosso, não um navegador de terceiro, então não precisa de confiança
pública. argon2/bcrypt continua adiado de propósito pra quando fizer
sentido trocar o formato de hash de contas já reais (decisão
reconfirmada, não é esquecimento).

### 🟡 C2 — Identidade de item é o nome (string)

Saves/protocolo/reconstrução usam `item.name` como chave. Renomear item no
catálogo quebra saves existentes silenciosamente.

**Melhor:** `item_id` estável no catálogo (nome vira display), com migração
única nos saves.

**Status:** ✅ resolvido (parcial, deliberado) — `item_id` estável
implementado em toda a superfície de risco real de duplicação/dessincronia
(inventário, equipamento, loja, loot de quest, munição de aljava, barra de
consumíveis). Ver §20 pro desenho completo, achados durante a migração e
prova de teste. Deliberadamente NÃO migrado (baixo risco, sem chamador real
no código hoje): `server/loot_processor.py`/`ui/systems.py::
_try_send_online_loot_request` (saque de corpo por nome) e o tipo de
objetivo de quest `use_item_on_target` (nunca disparado por nenhum sistema)
— registrados aqui como remanescente conhecido, não esquecido.

### 🟢 D1 — Baseline de testes permanentemente vermelho

Suíte estabilizou em "7 failed / 85 passed" (às vezes 9F/83P) e todo mundo
trata como "verde". A 8ª falha nova passa invisível.

**Melhor:** ou consertar os 7, ou marcá-los `xfail(reason=...)` — custo de
uma tarde, devolve o sinal binário "passou/quebrou".

### 🟢 D2 — Zero testes de cliente; validações headless descartadas

Toda a lógica de predição (a MAIOR fonte de bugs) não tem teste permanente.
Os scripts headless que validaram cada fix da semana (quiver, marcador, zoom
debounce, fog do minimapa...) morreram no scratchpad.

**Melhor:** promover esses scripts a `tests/client/` — o padrão SDL dummy
driver já está provado e é barato.

### 🟢 D3 — Flags de debug como constante no código

`DBG_ENABLED = True` esquecido ligado (aconteceu 14/07 com archer_debug;
spell_debug/aoi_debug têm o mesmo padrão).

**Melhor:** ler de env var (`RPG_DEBUG_ARCHER=1`) ou do config.json — flag
ligada nunca entra em commit/build por acidente.

### 🟢 D4 — `print()` como logging do servidor

Sem níveis, sem rotação, timestamps inconsistentes.
**Melhor:** `logging` com formatter único (o projeto já usa em
spell_debug_log.py — estender o padrão).

### 🟢 D5 — `ui/systems.py` (4410 linhas) como gaveta de tudo

10 classes sem relação entre si (Shop, Loot, Fog, TileRender, Consumable,
Input, Render, Camera, MouseTargeting, Skill) no mesmo arquivo.
**Melhor:** 1 arquivo por sistema (o projeto já faz isso pra quest/trainer/
crafting — terminar o padrão). Baixo risco, alto ganho de navegação.

### ✅ O que está genuinamente BEM (pra não perder de vista)

- `World` com índice invertido: simples, correto, rápido o suficiente.
- Façade `fx.py` + `core_systems`/`world_systems` headless: a decisão
  estrutural mais importante do projeto, e funciona.
- "Pontos únicos de verdade" documentados no CLAUDE.md com as classes de bug
  que cada um previne — prática rara e valiosa.
- Conteúdo data-driven (`skill_config`, `talent_data`, `mob_definitions`,
  `quests_data`) — adicionar conteúdo não exige tocar em sistema.
- Validação server-side pós-auditoria (Tiers A-F) — os buracos grandes de
  segurança de gameplay foram fechados de verdade.
- AOI + snapshot history/lag comp + save merge documentado campo a campo.
- Disciplina de documentar CADA decisão/bug com causa raiz — este arquivo e o
  ARQUITETURA_ONLINE.md são o motivo de bugs velhos não voltarem.

---

### §11-EXECUÇÃO (15/07/2026) — status e plano dos restantes

**Resolvidos nesta rodada (1 commit cada, suíte verde após cada um):**
| Item | O quê | Commit |
|------|-------|--------|
| 12/D1 | Suíte 100% verde (7 testes desatualizados corrigidos; causa única: features pós-teste) | `5337805` |
| 14/D3 | Flags de debug → env var (`RPG_DEBUG_*`) | `16e56f4` |
| 4/A4 | Sanitização de inventário na borda de persistência (anti save-forging) + cache de forja | `3b45630` |
| 9/B5 | Índice de ocupação no knockback; reavaliação honesta (hot path já era O(1)) | `50cbd00` |
| 3/A3 | Resolver por-entidade neutraliza `_svc` no mapa errado (`is_tile_walkable`) | `16d43f2` |
| 10/C1 | Salt por conta + upgrade transparente + `hmac.compare_digest` | `9dfe105` |
| 13/D2 | 4 arquivos de testes permanentes (15 testes novos; 107 total) | `1377bd5` |
| 15a/D4 | `print()` → `logging` no servidor (server/log.py, rotativo, `RPG_LOG_LEVEL`) | `b0d2bb0` |

**Plano dos itens grandes (cada um = sessão dedicada; ordem recomendada):**

1. **(2/A2) Matar o dual-mode `if self._net`** — o de maior retorno.
   ~~Decisão prévia necessária~~ **DECIDIDO pelo usuário (15/07/2026):
   REMOVER o modo offline deste branch** (era só referência de
   implementação; offline vive no master).

   **FASE 1 FEITA (15/07/2026):** constatado que a ENTRADA já era
   online-only (main.py sempre passa por login screen → NetworkClient;
   não existe --offline). Podados os branches offline que geraram bugs
   reais: rage decay local (`world_systems.CombatStateSystem`), regen
   local de mana (`spell_system.ManaSystem`, virou só timers de proc),
   criação local de flecha do auto-attack (`systems.PlayerInputSystem::
   _process_archer_combat`), aceite/entrega/progresso local de quest
   (`quest_system`: dialog + fila de eventos + `_process_talk_to_npc`
   virou no-op de compatibilidade). Suíte 109/109 verde.

   **FASE 2 FEITA (15/07/2026) — game.py saves/boot:**
   - `_apply_save()` deletado (restore de saves/slot_N.json — já era
     código morto: nenhum caller no fluxo online; estado vem do
     WORLD_STATE).
   - `_autosave()` não grava mais save local (`save_game`) — só config
     de hotbar + `_send_save_state()` pro servidor (única persistência).
   - `auto_start_quests()` removido do boot: nenhuma quest usa
     `auto_start=True` hoje e o desbloqueio em cadeia roda no servidor
     (`quest_logic.py`) — semear QuestLog local antes do sync só criava
     divergência em potencial.
   - Teste de entry point do CLIENTE adicionado (`main.py --help` como
     subprocess, mesma classe de regressão do servidor). Suíte 110/110.
   - Ainda offline-only e intocado (código morto isolado, sem risco de
     dual-mode): `ui/char_creation_screen.run()` (seleção de slot local)
     e `engine/save_system.py` (API de slots — `request_autosave`/
     `register_autosave` continuam em uso pelo caminho online).

   **FASE 3 (pendente — junto do item 6):**
   - `ui/spell_system.py`: dano local em `PlayerProjectileSystem._on_hit`
     (caminho offline via deal_damage), caminho não-visual_only do
     `SpellCastSystem` (dedução local de recurso), handlers `_apply_*`
     de dano local — melhor junto do item 6 (unificação).
   - `ui/systems.py` PlayerInputSystem/LootSystem/DeathRespawnSystem:
     demais branches offline.
   - **CUIDADO PERMANENTE:** em `SkillSystem`/`skill_handlers.py`, o
     branch "offline" É O CÓDIGO DO SERVIDOR (skill_processor reusa com
     `_net=None`) — intocável até o item 6/7 mover os handlers pra
     registry headless.

2. **(6/B2) Unificar handlers de spell cliente/servidor** — segundo maior
   retorno em bugs evitados. Um handler headless por skill (padrão
   `world_systems` + façade fx), chamado pelos dois lados; `_server_*` e
   `_apply_*` viram wrappers finos até sumirem. Fazer DEPOIS do item 2
   (o dual-mode decide quanto do caminho visual sobra). Migrar 1 skill
   piloto (ex: Picada de Escorpião) antes de generalizar.

3. **(7/B3) Registry explícito de skill handlers** — natural de fazer JUNTO
   do item 6 (mesmos arquivos): `SKILL_HANDLERS: dict[str, callable]`
   headless substitui `getattr(f"_skill_{sid}")` + o estado mutado
   (`player_entity_id`) vira parâmetro de contexto.

4. **(8/B4) Schemas tipados do protocolo** — TypedDict por mensagem em
   `shared/messages.py` (formalizar o que os comentários já dizem) +
   validação na borda do servidor. Independente dos anteriores; bom
   "primeiro item" se quiser algo de risco baixo. Também destrava a
   migração JSON→MessagePack já planejada.

5. **(1/A1) Decompor os componentes-Deus** — fazer POR CLASSE, na próxima
   vez que a build daquela classe for retrabalhada (nunca "big bang"):
   recursos → componentes próprios (`Rage`/`Mana`/`Concentration`), flags
   de talento → componente por build. O mais invasivo de todos; só compensa
   com os itens 2-3 já feitos (menos call sites duplicados pra migrar).

6. **(5/B1) Pipeline declarativa de sistemas** — lista ordenada com
   profiling automático em `game.py::run()` e `WorldServer._tick()`.
   Ganho: manutenção + fim dos rótulos mentirosos do profiler. Fazer por
   último (mexe na espinha dos dois loops; melhor com tudo estável).

7. **(11/C2) `item_id` estável** — pequeno mas exige migração de saves;
   agrupar com qualquer sessão futura que já mexa em persistência.

---

### ✅ RESOLVIDO — Recarregar (aljava) consumia o DOBRO de flechas da mochila (feedback do usuário, 20/07/2026)

Reportado: comprou 200 flechas, recarregou uma aljava com limite de 75
flechas, a mochila perdeu 150 (exatamente o dobro do que a aljava
recebeu) em vez de 75.

**Causa raiz**: `ui/spell_system.py::_complete_cast`, ramo `visual_only`
(modo online — cast preenche barra localmente, servidor aplica o efeito
de verdade), despachava QUALQUER handler registrado em `_CAST_HANDLERS`
achando seguro por um comentário antigo ("handlers são seguros: verificam
target_cs antes de causar dano — online = None"). Essa suposição vale
pras outras spells (alvo é um mob/player remoto, sem `CombatStats` local,
handler sai cedo) mas NUNCA se aplicou a `_apply_recarregar`: é uma skill
auto-alvo (`target_id == attacker_id`), então `target_cs` é sempre válido
e o guard nunca dispara — o handler client-side rodava a mutação REAL
(bag→aljava) mesmo no ramo "visual". A confirmação do servidor
(`server/spell_completion_processor.py::_server_recarregar`) chegava
depois e aplicava sua PRÓPRIA dedução independente em cima do estado já
mutado localmente — dobrando a perda.

**Fix**:
- `ui/spell_system.py::_complete_cast` — `recarregar` entra num novo
  conjunto `_SELF_TARGET_SERVER_ONLY`, excluído do despacho de handler no
  ramo `visual_only` (mesmo tratamento que `_PROJ_SPELLS_LOCAL` já tinha
  pra projéteis). Cliente só toca som/barra de cast; o resultado real vem
  só da confirmação do servidor.
- `server/spell_completion_processor.py::_server_recarregar` — passou a
  mandar `ammo_new_stack` (valor ABSOLUTO pós-dedução) no
  `queue_stats_update`, mesmo padrão já usado por `quiver_arrow_count`.
- `client/network_handlers.py` — reconciliação trocou de `stack -=
  ammo_taken` (delta, relativo) pra `stack = ammo_new_stack` (absoluto,
  idempotente) — mesma classe de bug de outras correções desta sessão
  (confirmação do servidor deve ser SET, nunca delta acumulado em cima de
  estado que pode já ter mudado). Log "Aljava recarregada: X/Y" migrou
  pro cliente nesse mesmo ponto (perdido quando o handler parou de rodar
  local), com cuidado de aninhar SÓ dentro do `if _ammo_name_rec and
  _ammo_taken_rec > 0:` — `"quiver_arrow_count"` também é mandado por um
  path totalmente diferente (decremento por flecha disparada de Picada de
  Escorpião/Flecha Reiterada/Tiro Repulsivo/Tiro Múltiplo), que não tem
  `ammo_name`/`ammo_taken`; um log no nível errado spammaria a cada tiro.

Validado: `tests/test_server.py::TestRecarregarNaoConsomeEmDobro` (4
testes — deduz exatamente o necessário, `ammo_new_stack` absoluto correto
no payload, recarga parcial quando a mochila tem menos que o necessário,
2 chamadas seguidas não duplicam dedução) +
`tests/test_client_ui.py::test_recarregar_visual_only_nao_mexe_em_bag_nem_aljava_online`
(cliente não muda bag/aljava no ramo visual_only). Suíte completa
268/268, rodada 3x.

**Não validado**: teste manual em jogo (comprar flechas, recarregar,
confirmar consumo correto e mensagem de log).

---

### ✅ RESOLVIDO — Servidor travava TODO tick (pra sempre) depois de esgotar uma stack de item no inventário (feedback do usuário, 20/07/2026)

Reportado logo após validar o fix acima: recarregar a aljava até
esgotar as 200 flechas comprovadamente tirou a quantidade CERTA da
mochila (75, não mais 150 — o fix anterior funcionou), mas o cliente
"travou" e o terminal do servidor enchia com o mesmo traceback repetido
a 30x/s, pra sempre:

```
AttributeError: 'NoneType' object has no attribute 'name'
  File "engine/quest_logic.py", line 102, in sync_collect_progress
    owned = sum(item.stack for item in inventory.items if item.name == obj.loot_item)
```

**Causa raiz**: quando uma stack de item chega a 0, o padrão desta base
é deixar o slot como `None` na lista (preserva o índice/posição do
inventário — ver `server/spell_completion_processor.py::
_server_recarregar`, `client/inventory_handlers.py`,
`engine/save_system.py`, todos já filtram `is None`).
`engine/quest_logic.py` tinha 2 pontos que iteravam `inventory.items`
sem esse filtro: `sync_collect_progress` (linha 102) e o loop de
remoção de itens dentro de `complete_quest` (linha ~224). Assim que a
flecha esgotada virou `None` na lista, `sync_collect_progress` —
chamado 1x por tick em `_process_quest_events`, para TODO player
conectado com QUALQUER quest ativa (não precisa ser a de coletar
flechas) — lançava a exceção. Como ela sobe até `WorldServer._tick()`
sem ser contida antes, `_collect_deltas()` aborta inteiro: NENHUM
delta (STATS_UPDATE, ENTITY_MOVE, etc., de NINGUÉM) é calculado nem
enviado naquele tick — e como nada limpa o slot `None`, o próximo tick
falha exatamente igual, pra sempre, até reiniciar o servidor. Por isso
pareceu "recarregar não funciona": a confirmação da recarga ficou presa
na fila de STATS_UPDATE, nunca chegando a ser drenada — mas na
verdade o mundo INTEIRO estava congelado para todos os players
conectados, não só quem esgotou o item.

**Fix**: `engine/quest_logic.py` — `sync_collect_progress` agora pula
itens `None` na soma (`if item is not None and item.name == ...`); o
loop de `complete_quest` avança o índice sem tocar no slot quando
`item is None` em vez de acessar `.name` direto.

Validado: `tests/test_server.py::
TestQuestLogicIgnoraSlotVazioNoInventario` (3 testes — `None` no meio
da lista não quebra `sync_collect_progress`, nem `complete_quest`, e a
reprodução fim-a-fim: recarregar até esgotar a stack + rodar
`_process_quest_events` com quest ativa não lança). Confirmado que os
3 testes falham do jeito EXATO do bug reportado (mesmo
`AttributeError`) na versão sem o fix, antes de aplicá-lo. Suíte
completa 271/271, rodada 3x.

**Não validado**: teste manual em jogo (recarregar até esgotar a
stack, confirmar que o servidor não trava e a confirmação chega).

---

### ✅ RESOLVIDO — Polimorfia: alvo ficava lento pra sempre depois do efeito expirar (feedback do usuário 20/07/2026)

Reportado: "o alvo de polimorfia continua podendo andar com o
personagem, porém anda em slow (correto, é o efeito), mas quando acaba
o slow, o personagem continua no slow."

**Causa raiz**: `slow_mult`/`is_rooted`/`is_crowd_controlled` são
estados DERIVADOS de `StatusEffects.effects`, recalculados só dentro do
próprio loop de `StatusEffectSystem.update()` — que começa com
`if not sfx.effects: continue`. No modo online, `client/
remote_entity_handlers.py::_sync_player_effects`/`_sync_mob_effects`
sincronizam efeitos vindos do servidor fazendo `sfx.effects.pop()`/
`.clear()` DIRETO, por fora desse loop. Quando um efeito (aqui,
"polymorph") era removido assim e o dict ficava vazio, o guard do topo
passava a pular a entidade PRA SEMPRE — o recálculo que zeraria
`slow_mult` nunca mais rodava, porque nada além dele reabastecia
`sfx.effects`. Mesma classe de bug já documentada e corrigida em 3
pontos de leash de mob (`engine/world_systems.py`, ver histórico deste
arquivo) — nunca tinha sido replicada pro sync de rede de efeitos de
player.

**Fix**: `engine/core_systems.py::sync_status_derived_state(world, eid,
sfx)` (novo, extraído do corpo de `StatusEffectSystem.update()`) —
único ponto de verdade pro cálculo de `slow_mult`/`debilitate_elapsed`/
`is_rooted`/`is_crowd_controlled` a partir do `StatusEffects` atual,
chamável de QUALQUER lugar (não só de dentro do loop principal).
`client/remote_entity_handlers.py` passou a chamar essa função logo
depois de cada `.pop()`/`.clear()` direto (`_sync_player_effects` e nos
2 pontos de `_sync_mob_effects`).

**Não validado**: teste manual em jogo (Polimorfia expira e o alvo
volta à velocidade normal).

---

### ✅ RESOLVIDO — Pirofagia desorientava alvo amigável mesmo sem causar dano (feedback do usuário 20/07/2026)

Reportado: "pirofagia está causando o efeito de desorientado quando o
alvo (player) é amigável, só não causa dano, mas de qualquer forma é
incorreto."

**Causa raiz**: `ui/skill_handlers.py::_skill_pirofagia` (modo servidor,
disparo imediato de cone) varre `get_entities_with(TileMovement,
CombatStats)` DIRETO, sem nenhum filtro de facção — aplica dano via
`_apply_magic_damage` → `apply_damage_core`, que corretamente bloqueia
alvo amigável (`can_engage`, retorna `"blocked_friendly"`, dano não sai
do lugar) — mas a linha seguinte, `apply_effect(self.world, eid,
"disoriented", _dis_dur)`, roda INCONDICIONALMENTE pra qualquer entidade
geometricamente dentro do cone, sem checar o resultado do dano nem
`can_engage` de novo. Outras AoEs (ex: `_server_nova_congelante`, via
`WorldServer._combat_targets`) já filtram os candidatos por
`can_engage` ANTES de aplicar dano OU efeito — `_skill_pirofagia`
reimplementava a varredura à mão e não reproduziu esse filtro.

**Fix**: `_skill_pirofagia` passou a checar `can_engage(self.world,
self.player_entity_id, eid)` pra CADA candidato, pulando (`continue`)
antes de aplicar dano OU efeito se o alvo for amigável — mesmo gate,
mesmo padrão de `_combat_targets`, só que inline (esta função não é um
método de `WorldServer`, não tem acesso a `_combat_targets`).

**Não validado**: teste manual em jogo (Pirofagia não desorienta
jogador/NPC amigável dentro do cone).

---

### ✅ RESOLVIDO — Arena 2x2: recursos não eram restaurados ao entrar/sair da partida (feedback do usuário 20/07/2026)

Pedido do usuário: "os personagens que entram na arena, precisam ter
todos os recursos restaurados, HP, Mana, concentração, cooldowns de
skills e quando saem da arena é a mesma coisa."

**Fix**: `server/match_processor.py::_reset_combat_resources(eid)`
(novo) — restaura `CombatStats.current_hp = max_hp`,
`CharacterStats.mana = max_mana`, `concentration = max_concentration`,
`reset_volatile()` (contadores de combo/carga diversos), e limpa TODAS
as entradas de `_skill_last_used` daquele eid (cooldown AUTORITATIVO,
checado em `skill_processor.py` — comentário lá mesmo: "impede spam
mesmo que o cliente manipule current_cooldown local"). Chamado em
`_create_match` (pra cada um dos 4, ao entrar) e em `_arena_leave_now`
(ao sair de vez — depois do revive de quem morreu, ver §34.33
ARQUITETURA_ONLINE.md, já que `_revive_player` restaura HP/mana mas não
mexe em concentração/cooldown).

`PlayerSkills.skills[i].current_cooldown` (valor de EXIBIÇÃO, ticado
localmente pelo cliente, nunca sincronizado por rede — comentário em
`server/skill_processor.py`) é responsabilidade do CLIENTE:
`client/arena_handlers.py::_reset_local_arena_resources` espelha a
mesma restauração (HP/mana/concentração/`gcd_timer`/`current_cooldown`/
cargas) no player local, chamado nos mesmos 2 gatilhos
(`ARENA_MATCH_START`/`ARENA_MATCH_END`) — feedback instantâneo sem
esperar o próximo `STATS_UPDATE`.

**Validado**: `tests/test_arena.py::TestArenaResourceReset` (2 testes —
entrar e sair da arena restauram HP/mana/concentração/cooldown
autoritativo). Suíte completa 338/338, rodada 3x.

**Não validado**: teste manual em jogo (entrar/sair da arena com
recursos gastos e cooldowns ativos, confirmar restauração completa dos
dois lados).

---

### ✅ RESOLVIDO — Arena 2x2: resultado da partida invertido (vitória/derrota trocadas) (feedback do usuário 21/07/2026)

Pedido do usuário: "nos testes eu venci uma arena, mas em vez do
resultado ser vitória para mim, apareceu derrota, e vitória para o
perdedor."

**Causa raiz**: `client/arena_handlers.py::_draw_arena_result_modal`
identificava "minha linha" na lista `ARENA_MATCH_RESULT.results`
comparando `CharacterStats.name` (`my_row = next(r for r in results if
r.get("name") == my_name)`). `server/match_processor.py::_finish_match`
monta cada linha só com `{"name", "damage", "won"}` — sem nenhum
identificador estável por linha. Nome de personagem NÃO é único entre
contas (confirmado via SQL direto em `data/game.db`: "Aventureiro" x3,
"Juugo" x2, várias salvas no mesmo dia do teste do usuário) — com um
nome duplicado entre os 4 da partida, o cliente pegava a linha do
ADVERSÁRIO e mostrava o resultado trocado.

**Fix**: `_finish_match` agora inclui `"eid": eid` em cada linha de
`results`. `_draw_arena_result_modal` troca o match por nome para
`r.get("eid") == self._my_eid` (`self._my_eid` — eid atribuído pelo
servidor ao player local, já usado em outros pontos do cliente, ex.
`client/network_handlers.py::_my_eid = payload.get("eid", -1)`). O
outer broadcast (`server/session.py`, linha do `ARENA_MATCH_RESULT`)
já mandava `results` sem alteração — o eid por linha passa direto, sem
precisar tocar no envio.

**Validado**: `tests/test_arena.py::test_arena_match_result_manda_nome_dano_vitoria_dos_4`
estendido — cada linha carrega o `eid` correto, indexável por eid além
de por nome. Suíte completa 342/342, rodada 3x.

**Não validado**: teste manual em jogo com 2 personagens de nomes
IGUAIS nos times opostos (reproduz o cenário exato do bug).

---

### ✅ RESOLVIDO — CC (polimorfia/desorientado/medo) bloqueava só teclado, não clique do mouse (feedback do usuário 21/07/2026)

Pedido do usuário: "quando o alvo está sob efeito de polimorfia, ele
não consegue andar pelo teclado, mas continua podendo andar pelos
cliques do mouse, isso é um erro de arquitetura, pois quando há
efeitos de cc no alvo, o correto é isolar qualquer ação (a não ser que
eu dissesse 'tal ação pode ser feita'), e não somente o sistema de
input do teclado. [...] não só no polimorf mas em outros efeitos como
desorientado e fear."

**Causa raiz**: `PlayerInputSystem.update()` (`ui/systems.py`) computa
`can_move` uma vez por frame (dobrando `CombatState.can_move()` +
ghost/morte + `is_action_locked()`), mas só consultava essa variável no
branch de movimento por TECLADO (linha ~539). `_process_ground_move`
(clique de chão), `_process_follow` ("Seguir") e os 3 pontos de
perseguição via `_auto_move_step` (mago/melee dentro de `_process_target`,
arqueiro dentro de `_process_archer_combat`) nunca recebiam `can_move`
— corriam incondicionalmente contanto que `is_pursuing`/`ground_target`
estivesse setado, ignorando qualquer CC.

Segundo achado, mais amplo: `is_action_locked()` (`engine/utils.py`) —
o choke-point já usado tanto no cliente quanto no servidor
(`skill_processor.py`, `combat_processor.py`) para bloquear
skill/auto-attack — só cobria `sleep`/`disoriented`/`polymorph`. "Medo"
(fear, aplicado pelo talento "Horrorizante" do Executar via
`ui/skill_handlers.py`) nunca tinha NENHUMA restrição de ação/movimento
pro jogador (só mobs fogem de medo, em `EnemyAISystem` —
`engine/world_systems.py`); um player amedrontado agia normalmente.

**Fix**:
1. `can_move` agora é passado como parâmetro por toda a cadeia de
   chamadas: `update()` → `_process_target`/`_process_ground_move`/
   `_process_follow` → `_process_archer_combat`. Cada ponto de
   movimento (clique de chão, "Seguir", as 3 perseguições) só executa
   se `can_move` for `True` — mesmo default "bloqueia por padrão" que
   já regia o teclado.
2. `is_action_locked()` ganhou `sfx.has("fear")` na condição — como é
   o único choke-point consultado em todos os pontos de gate (cliente
   E servidor), um efeito novo entra ali e propaga sozinho pra
   movimento E ação, sem caçar call site por call site (é exatamente o
   padrão que o usuário pediu para generalizar).

**Validado**: `tests/test_client_ui.py` — 4 testes novos
(`test_clique_de_chao_move_normalmente_sem_cc`,
`test_polimorfia_bloqueia_clique_de_chao_igual_teclado`,
`test_desorientado_bloqueia_clique_de_chao`,
`test_medo_bloqueia_clique_de_chao`) instanciam `PlayerInputSystem`
direto com serviços de pathfinding fake e provam que `StatusEffects`
com polimorfia/desorientado/medo trava o clique de chão exatamente
como já travava o teclado. Suíte completa 342/342, rodada 3x.

**Não validado**: teste manual em jogo (aplicar polimorfia/desorientado/
medo em si mesmo ou receber de outro player, confirmar que clique de
chão E perseguição de alvo ficam bloqueados, não só WASD).

---

### ✅ RESOLVIDO — Balão de fala do chat aparecia sobre a cabeça do personagem ERRADO (feedback do usuário 21/07/2026)

Pedido do usuário: "quando eu estava digitando ontem no personagem
aventureiro, outro tester reportou que o balão do que eu escrevia
aparecia sobre a cabeça do personagem dele, pois tinha o mesmo nome."
Mesma classe do bug do resultado de arena invertido (§ acima) —
identidade de entidade resolvida por NOME em vez de eid.

**Causa raiz**: `CHAT_MESSAGE` (`server/session.py::_handle_chat`) nunca
mandava o eid do remetente, só `sender: session.display_name`. O cliente
(`client/network_handlers.py::_resolve_chat_sender_entity`) era forçado a
resolver o balão de fala comparando `CharacterStats`/`RemoteControlled`
`.name` contra esse nome — com dois personagens de mesmo nome na sessão
(nomes legados não são únicos, ver seção acima), o primeiro iterado
"ganhava" o balão do outro.

**Fix**: `_handle_chat` agora inclui `"eid": session.entity_id` no
payload (`_duel_chat_msg` do sistema, sender="Sistema", continua sem eid
de propósito — não representa nenhuma entidade). Cliente resolve via
`self._resolve_local_eid(sender_eid)` — mesmo choke-point já usado por
trade/duelo/party — e só cai pro fallback por nome (`_logged_char_name`,
o PRÓPRIO player) se não vier eid; o scan por `RemoteControlled.name`
foi removido inteiramente (era o código exatamente buggy).

**Validado**: `py_compile` limpo; suíte completa 352/352, rodada 3x (não
foi adicionado teste automatizado dedicado pro chat bubble — validado
via leitura de código e pelo fato de reusar `_resolve_local_eid`, já
coberto por outros testes de rede).

**Não validado**: teste manual em jogo com 2 personagens de nomes
IGUAIS na mesma sessão, confirmar que o balão aparece só sobre quem
realmente escreveu.

---

### ✅ RESOLVIDO — CC (stun) não bloqueava nada + generalização de blocks_move/blocks_act (feedback do usuário 21/07/2026)

Pedido do usuário: "eu esqueci de citar o stun, mas isso deve contar
para todos os efeitos do tipo cc, para que não precise hardcodar em
cada efeito que ele impossibilita o jogador de controlar o personagem
ou não."

**Causa raiz**: "stun" já existia como `StatusEffects` de verdade
(aplicado por Interceptar, Punho no Queixo, knockback — `ui/skill_handlers.py`,
`server/spell_completion_processor.py`), mas **nenhum** gate de ação/
movimento olhava pra ele — nem o antigo `is_action_locked` (só sleep/
disoriented/polymorph/fear), nem `CombatState.is_stunned` (que só era
setado manualmente em 3 lugares sem relação com esse efeito: freeze de
fim de partida da arena, self-stun do Bloco de Gelo, replicação de
player remoto). Um player atordoado de verdade não tinha NENHUMA
restrição — nem cliente nem servidor.

**Fix estrutural** (não só "adicionar stun à lista hardcodada" — o
usuário pediu pra eliminar o hardcode): `content/status_effects_data.py::
EffectDef` ganhou dois campos novos, `blocks_move`/`blocks_act` (default
`False`), marcados por efeito: stun/sleep/fear/polymorph/disoriented =
ambos `True`; root = só `blocks_move` (pode agir, não pode se mover);
slow e todo o resto = nenhum (só debuff/DoT, não é lock de controle).
`engine/utils.py::is_action_locked`/`is_movement_locked` (nova) leem
essas flags via `StatusEffects.effects` em vez de comparar strings
hardcodadas — um efeito de CC novo só precisa marcar as flags certas em
`EFFECT_DEFS`, nunca mais caçar call site. `ui/systems.py` (cliente) e
`server/skill_processor.py`/`combat_processor.py` (servidor) continuam
chamando as mesmas funções, sem nenhuma mudança de código neles — ganham
o fix de graça.

`server/world_server.py::move_player` (anti-cheat de movimento bruto)
tinha uma lista hardcodada separada (`sleep/stun/root`) que também não
incluía "fear" — adicionado. Disoriented/polymorph continuam DE
PROPÓSITO fora dessa lista específica (não generalizados pra
`is_movement_locked` ali): o wander aleatório desses 2 efeitos é
decidido pelo CLIENTE (`CombatStateSystem`) e mandado como MOVE normal —
bloquear ali quebraria esse wander legítimo. Comentário deixado no
código explicando a exceção.

**Validado**: `tests/test_server.py::TestCCGeneralizado` (5 testes —
stun bloqueia ação+movimento, root bloqueia só movimento, slow não
bloqueia nada, fear bloqueia MOVE bruto no servidor, disoriented
continua liberado no MOVE bruto de propósito) +
`tests/test_client_ui.py::test_stun_bloqueia_clique_de_chao`. Suíte
completa 352/352, rodada 3x.

**Não validado**: teste manual em jogo (Interceptar/Punho no
Queixo/knockback atordoando o próprio personagem, confirmar que
teclado/clique/skills ficam bloqueados durante o stun).

---

### ✅ RESOLVIDO — Arena 2x2: nameplate não ficava hostil contra o time adversário + fantasma morto continuava "spamando" nameplate (feedback do usuário 21/07/2026)

Pedido do usuário: "o ideal é a barra de HP e o nome dos
adversários(nome plate), ficarem vermelhos para os adversários, assim
como você fez no duelo [...] ao personagem morrer, o nameplate pode
sumir, e só volta quando sair da arena."

**Fix**: `client/remote_entity_handlers.py::_draw_remote_players` já
tinha a cor hostil pronta pro duelo (`_duel_opponent_local_val`) — só
faltava incluir a arena na mesma condição. Adicionado
`_is_arena_opp = server_eid in self._arena_opponents_server_val`
(`client/arena_handlers.py` já mantinha esse set desde a leva anterior,
populado só com o TIME ADVERSÁRIO — nunca o próprio, ver `ARENA_MATCH_START`
`"opponents"` em `server/match_processor.py::_create_match`) — `_is_hostile_rp
= _is_duel_opp or _is_arena_opp` decide a cor, cobrindo os dois contextos
com o mesmo código.

Nameplate-some-ao-morrer: escopado só a `self._arena_in_match` — dentro
da própria partida, se `RemoteControlled.hp <= 0` (sinal de morte já
mantido em dia por `_handle_msg_entity_death`, que zera o hp na hora do
óbito — não precisa de `GhostState`, que só existe pro player LOCAL),
o loop pula o `WORLD_LABELS.add_icon`/`add_text` daquele player — sprite
continua visível (já escurecido por `_handle_msg_entity_death`), só a
HUD flutuante some. Como a arena é uma instância isolada (só os 4 da
partida aparecem no AOI de cada um), não há risco de esconder o
nameplate de um morto de OUTRO contexto por engano; fora da arena o
comportamento antigo (nameplate sempre visível) continua idêntico.

**Validado**: `tests/test_client_ui.py` — 4 testes novos
(`test_arena_oponente_fica_hostil_igual_duelo`,
`test_arena_proprio_time_nao_fica_hostil`,
`test_arena_nameplate_some_ao_morrer_dentro_da_arena`,
`test_nameplate_de_morto_fora_da_arena_continua_aparecendo`) — sobem
`_draw_remote_players` isolado e inspecionam a fila do
`WORLD_LABELS`/cor dos pixels renderizados. Suíte completa 352/352,
rodada 3x.

**Não validado**: teste manual em jogo com 4 clientes reais na arena
(confirmar cor hostil só contra o time adversário, nameplate sumindo ao
morrer e voltando só ao sair).

---
   **(15b/D5) Fatiar `ui/systems.py`** — mecânico, zero risco de lógica;

---

## 12. AUDITORIA ARQUITETURAL AMPLA — 06/08/2026 (revalidação da §11 + achados novos)

Pedido explícito do usuário depois de um cluster de bugs recorrentes na BG
(hotbar/talentos vazando da instância, PNQ vs minion) + 1 hack de cache
achado por ele mesmo em `tileset.py`: "acha como um engenheiro ou
arquiteto desse projeto, e garanta a qualidade dele acima de tudo". Não é
mais 1 arquivo — é revalidação da §11 (15/07/2026) inteira + varredura
nova. Relatório completo (todos os achados abaixo, com trecho de código e
evidência) publicado como artifact HTML nesta sessão; aqui vai o resumo
factual pra ficar no repo.

**Achado-chave: os itens A4 e B3 da §11 (nunca resolvidos, só
"mitigados") são hoje causa raiz CONFIRMADA de bugs reais desta mesma
sessão** — a auditoria de julho já tinha previsto exatamente isso. Ver
`§34.74.47/.49/.50` (ARQUITETURA_ONLINE.md) pros 3 vazamentos de
equipment/hotbar/talents já corrigidos, e a investigação do PNQ-vs-minion
(mesma sessão) pro mecanismo do B3 (servidor despachando skill via
`getattr` numa classe de UI do cliente).

### 🔴 CRÍTICO — 2 novas ocorrências do padrão A4, ainda não corrigidas

- **`inventory`** (`server/session.py:301`) — `client_p.get("inventory")
  if client_p else None`, SEM fallback `live_X` (diferente de
  `equipment`/`talents`, já corrigidos). Mesma vulnerabilidade: SAVE_STATE
  mandado dentro da BG cacheia o inventário temporário de 6 slots, nada
  invalida o cache na saída.
- **`skills.learned`** (`server/session.py:258-260`) — subcampo ESQUECIDO
  dentro de `skills`, que só teve o subcampo `hotbar` corrigido
  (linha 265-267). Cliente ainda vence sobre `srv_data.get("skills")`
  (que já é live) quando truthy. Vetor de ataque real e concreto:
  **desconectar dentro da instância** — `on_disconnect`
  (linha 356-363) chama `exit_normalized_progression` e, na sequência
  IMEDIATA, `_persist_character`, sem round-trip pro cliente reenviar
  SAVE_STATE corrigido. Resultado: queda de conexão na BG apaga skills
  reais aprendidas, gravando o `learned_skill_ids` reduzido da instância.
- **Fix**: mesmo padrão já usado 3× nesta sessão — `WorldServer.
  get_player_inventory_data(session_id)` e uso incondicional de
  `srv_data.get("skills")`/um `live_learned` equivalente em
  `_build_save_merge`. Não implementado ainda — fica pra próxima sessão,
  prioridade alta (bug real, ainda não relatado pelo usuário).

### 🔴 REGRA DO CLAUDE.md VIOLADA — knockback do Tiro Repulsivo

`ui/spell_system.py::_apply_knockback` (linhas 1234-1235) escreve
`tgt_tm.current_tile_x/y` DIRETO, sem passar por `utils.snap_to_tile()`
— contradiz a regra textual do CLAUDE.md ("NUNCA escrever
current_tile_x/y direto"). Call site real (não é código morto): todo
acerto de Tiro Repulsivo enfileira via `_pending_knockbacks`
(linha 1477-1478). Servidor já tem versão autoritativa independente em
`server/spell_completion_processor.py::_server_tiro_repulsivo`. Não
confirmado se já causa sintoma visível (pode estar mascarado pela
correção autoritativa chegando depois, mesmo mecanismo sob investigação
pro rollback do Interceptar) — mas é violação de regra confirmada.

### 🟡 DOCUMENTAÇÃO DIZENDO O OPOSTO DO CÓDIGO REAL (4 casos)

- `COMPONENTES_ECS.md:171` — diz `gold` é "cliente autoritativo via
  `_build_save_merge`". Falso: `gold` nem aparece nesse dict; é lido
  live do `Wallet` em `get_player_save_data` (`world_server.py:1840`,
  já correto — o único campo da família A4 que escapou por acidente).
- `MAPA_PROJETO.md:188` — afirma "servidor nunca importa `ui/`". Falso:
  `server/world_server.py:478` faz `from ui.systems import SkillSystem`
  de propósito (é o próprio mecanismo do item B3 acima).
- `instance_progression.py:7` (docstring) + `COMPONENTES_ECS.md:174` —
  dizem "INERTE, nenhum processador chama ainda". Falso desde
  04-05/08/2026: `server/bg_queue_processor.py` chama
  `enter_/exit_normalized_progression` de verdade na fila real de BG.
- `MAPA_PROJETO.md:339-341` — ensina a adicionar stat via
  `shared/constants.py::COMBAT_SYNC_STATS`, mecanismo REMOVIDO do
  código (`MsgType.PLAYER_STAT_SYNC` obsoleto, handler no-op, zero
  ocorrência de `COMBAT_SYNC_STATS` em `.py`).

**Por que isso importa mais que um bug comum**: o próprio CLAUDE.md
manda confiar nos docs de arquitetura ANTES de vasculhar o código-fonte
("se a resposta está nos arquivos de arquitetura, não varrer o
codebase"). Doc errado não é neutro — ativamente desinforma qualquer
sessão futura (inclusive esta).

**Este próprio arquivo é outro caso**: 16 dias sem entrada nova
(21/07 → 06/08), apesar de pelo menos 4 bugs da família A4 terem
acontecido de verdade nesse intervalo — nenhum foi cruzado de volta com
a auditoria que já os previa.

### 🟢 Estado ad-hoc fora do ECS — achado do usuário + varredura completa

Confirmado que o cache via `hasattr(func, "_cache")` que o usuário achou
em `engine/tileset.py` (`get_camouflage_disguise_frame`, linha 1073) NÃO
é um padrão espalhado — é isolado a essa função + uma vizinha
(`discover_camouflage_variants`, linha 1037). Achado adicional da mesma
família: `ui/quest_system.py:712` — `_fallback_marker_cache: dict = {}`
como atributo de CLASSE (armadilha clássica Python, dict compartilhado
entre todas as instâncias, não por instância). Resto do "estado global"
do projeto segue um padrão de casa saudável e já documentado (`global`
explícito + função de registro — `core_systems.register_lethal_
interceptor`, `faction_system.register_pvp_context`, o próprio `_svc`)
— não é praga sistêmica, não precisa de refatoração em massa.

### 🟢 Dado disperso fora de `content/` (6 achados, baixo risco)

`RARITY_COLORS` duplicado literalmente 2× em `ui/systems.py`
(linhas 2348, 3509); `ENEMY_TIER_CONFIGS` (`entity_factory.py:44-49`) e
`RESPAWN_TIMERS` (`world_systems.py:1030-1035`) são tabelas de
balanceamento vivendo dentro de arquivos de lógica; `RACES`/`CLASSES`
(`components.py:10-27`) são conteúdo dentro do arquivo de definição de
componente ECS; `_TALENT_SKILL_REQS` derivado 2× independentemente com
shapes diferentes (`world_systems.py:134-137` vs
`hotbar_handlers.py:25-28`); nomes de campo de item hardcoded 2× no
mesmo arquivo em `save_sync_handlers.py` (serialize linha 31-34,
deserialize linha 84-86) — campo novo direto em `Item` exige lembrar as
duas listas.

### 🟡 Em aberto — cliente resolvendo combate real contra mob (não é veredito)

`ui/spell_system.py:1097-1150,1405-1447` — dano/crit de flecha contra
MOB (tem `CombatStats` espelhado no cliente) é decidido localmente sem
desvio pro servidor, diferente do caminho contra player remoto (que já
usa `pending_arrow_impacts` corretamente). Pode ser predição deliberada
ou resquício do modo offline (item A2) nunca podado pro combate contra
mob — não decidido aqui, fica como pergunta pro usuário antes de mexer.

### Revalidação da §11 (itens grandes, nenhum resolvido na raiz)

| Item | Status hoje | Nota |
|------|-------------|------|
| A2 — dual-mode `if self._net` | ✅ Fase 2 fechada (11 de 13 pontos reais + 1 achado bônus) | 2 pontos restantes (dentro de `_use_skill_visual_only`) fundidos na Fase 3 |
| A4 — autoridade híbrida | mitigado (sanitização), raiz intocada | causa dos 5 vazamentos confirmados nesta sessão |
| B2 — handlers de spell duplicados | intocado | é onde mora o achado do knockback acima |
| B3 — `getattr` dispatch reusando UI no servidor | intocado | causa raiz confirmada do bug PNQ-vs-minion |

### Prioridade recomendada

1. `inventory`/`skills.learned` (risco baixo, fix mecânico já provado 3×) — próxima sessão.
2. Correção de texto nos 4 docs errados — sem risco, qualquer momento.
3. Knockback → `snap_to_tile()` — decidir junto com o item 6/B2 (handler headless único) ou como correção pontual isolada.
4. Dado disperso (6 achados) — mecânico, agrupar numa sessão de faxina.
5. A2/A4/B2/B3 — cada um é sessão dedicada, como a §11 já recomendava. Não tentar "big bang".
6. Combate de flecha vs. mob local — aguardando decisão do usuário.

---

## 13. ROTEIRO DE SANEAMENTO ARQUITETURAL — em execução desde 07/08/2026

Usuário decidiu explicitamente: sistemas modulares/escaláveis ANTES de
qualquer trabalho de conteúdo (arte/lore/mapa/quest) — ver
`arquitetura/VISAO_PRODUTO.md`. Roteiro completo de 8 fases (Fase 0 a
7, fundindo §11 e §12) aprovado em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`.
Execução sem reaprovação por fase (decisão do usuário) — progresso
registrado aqui a cada fase fechada.

### ✅ Fase 0 — 2 bugs A4 confirmados fechados (07/08/2026)

`inventory` e `skills["learned"]` (item da "Prioridade recomendada" §12
acima) corrigidos com o mesmo padrão `live_X` já usado 3× nesta sessão
(equipment/hotbar/talents). Detalhe técnico completo:
`ARQUITETURA_ONLINE.md` §34.74.51. Suíte completa 918 passed (2x limpa
consecutiva no momento deste registro, 3ª rodada em andamento).

**Próxima**: Fase 1 (faxina mecânica de baixo risco — cache ad-hoc,
dado disperso) → Fase 2 (A2, matar dual-mode) → Fase 3 (B2+B3,
handler headless único de skill) → Fase 4 (A4, inversão completa da
autoridade de persistência) → Fase 5 (A1, componentes-deus,
oportunista) → Fase 6 (B1, pipeline declarativa) → Fase 7
(pré-lançamento: TLS/argon2/item_id).

### ⏸ PAUSADO (07/08/2026) — benchmark contra arquiteturas de referência antes de retomar

Decisão do usuário: difícil julgar sozinho "isso está certo
arquiteturalmente" sem parâmetro externo. 19 sistemas do projeto
comparados contra Veloren (ECS real, forma) e AzerothCore (emulador de
servidor WoW, conteúdo — quest/instância/talento), um por vez, com
prova/citação real. Detalhe completo:
`arquitetura/BENCHMARK_ARQUITETURA.md`. Resultado: maioria dos sistemas
VALIDADA (nenhuma mudança); alguns achados reais:

### ✅ B6 — Fila/lifecycle de partida duplicados entre Arena e Battleground (resolvido parcial, ver §17)

Achado NOVO (não estava em §11 nem §12) — `server/match_processor.py`
e `server/bg_queue_processor.py` compartilham só a camada de baixo
(`WorldServer._load_instance`/`_unload_instance`), mas cada um
reimplementa DO ZERO a fila/entrada/saída de partida — métodos
espelhados (`request_arena_queue_join`/`request_bg_queue_join`,
`_arena_leave_now`/`request_bg_leave`, etc.). AzerothCore resolve isso
com uma classe base `Battleground` compartilhada (fila/pontuação/
lifecycle 1 vez só; cada arena específica só adiciona a mecânica
própria). **Melhor**: uma base compartilhada tipo
`InstancedMatchMixin` (fila genérica, entrada/saída genérica), cada
modo definindo só o que acontece DENTRO da partida. 100% backend — não
muda a experiência de fila do jogador.

### Reforços de itens já catalogados (não são achados novos, mas mudam prioridade/desenho)

- **B2/B3** (§11): a causa raiz tem uma forma concreta de resolução —
  componente de estado único com fases (Buildup/Charge/Action/Recover,
  modelo Veloren `CharacterState`), não só "1 handler por skill num
  dict". Muda o DESENHO da Fase 3 do roteiro — discutir com o usuário
  antes de reescrever, não decidir sozinho (é redesenho, não só
  prioridade).
- **A1** (§11): flags de talento nomeados em `CombatStats` têm
  alternativa provada externamente — modificador genérico via efeito
  de "aprender spell" (modelo AzerothCore `SPELL_EFFECT_LEARN_SPELL`),
  em vez de um flag booleano bespoke por talento.
- **C2** (§11): identidade de item por nome (não ID estável) —
  confirmado como padrão errado por comparação direta
  (`item_template.entry` do AzerothCore). Argumento pra subir de
  prioridade (estava na Fase 7, baixa prioridade).

### Perguntas de produto levantadas (não são débito técnico — aguardando resposta em `VISAO_PRODUTO.md`)

Multi-spec de talento (✅ respondido — 3 builds salvas, 1 ativa),
quests diárias/repetíveis, modo de loot de grupo selecionável,
reputação de facção acumulável. Nenhuma decidida sozinha.

**Retomada do roteiro (07/08/2026)**: usuário decidiu não implementar
as 4 perguntas de produto agora (escopo controlado — ver
`VISAO_PRODUTO.md`) e autorizou continuar o roteiro. Sequência
revisada, retomando da Fase 1 (Fase 0 já fechada):

✅ Fase 1 — faxina mecânica de baixo risco (cache ad-hoc, dado
disperso) — fechada.
✅ Fase 2 — A2, matar dual-mode — fechada (11 de 13 pontos reais + 1
achado bônus; 2 restantes fundidos na Fase 3, ver seção acima).
✅ Fase 3, piloto (07/08/2026) — ver seção "✅ Fase 3 — piloto" abaixo.
✅ Sistema de GM server-autoritativo (07/08/2026, fora do roteiro em si
— destravou a validação manual do piloto acima) — `ARQUITETURA_ONLINE.md`
§34.74.54. F12 (nível/ouro/itens) só mutava ECS local do cliente,
servidor nunca sabia, autorização real sempre recusava — inviabilizava
qualquer playtest online que dependesse de nível/talento. `accounts.is_gm`
+ `GM_LEVELUP`/`GM_ADD_GOLD`/`GM_ADD_ITEM` (mesmo molde de `BUY_REQUEST`,
reaproveita os caminhos autoritativos já usados por recompensa de quest/
loja). Concessão só via `python -m server.grant_gm <username>` (CLI, sem
UI/endpoint — AzerothCore como referência).
▶ Próximo passo real do Fase 3: generalizar pra `fatiador_de_corpos`
(3 implementações de tick hoje) e depois pras skills de cone/projétil
restantes — **ainda não desenhado**, pesquisar/perguntar antes de mexer,
mesma régua desta sessão inteira.
Fase 3 (redesenho completo, referência original, mantido como norte) —
em vez de só
`SKILL_HANDLERS: dict[str, callable]`, avaliar componente de estado
único com fases (Buildup/Charge/Action/Recover, achado A.1 do
benchmark) — decisão de desenho tomada durante a execução da fase, não
antes (backend, sem afetar jogador — dentro da régua já combinada de
"pode decidir sozinho quando não muda experiência").
→ **Fase 3.5 (NOVA)** — unificar fila/lifecycle de partida entre
Arena e Battleground (achado B6) — mesma área de código da Fase 3,
por isso encaixada logo depois.
✅ Fase 4 (A4, inversão de persistência — ver §21 equipar/desequipar +
§25 saque/INV_SYNC, fechada 11/08/2026) → ✅ **Fase 4.5** — melhoria do
log de performance (§23 análise, §28 desenho+implementação, fechada
12/08/2026; ponto 4 do desenho — breakdown dentro de um único sistema —
adiado por trade-off de custo, ver §28) → ✅ **Fase 4.6 (NOVA,
12/08/2026)** — escala de servidor (pré-filtro de IA vira índice
espacial em vez de comparação linear, ver §29) — furou a fila da Fase 5
por decisão do usuário: `VISAO_PRODUTO.md` já pesava escala com mais
peso que "só grupo fechado de testers", e a própria Fase 5 já era
descrita como "oportunista" (não urgente) → ✅ **Fase 4.7 (NOVA,
12/08/2026)** — log de performance vira hierarquia real (`_perf_push`/
`_perf_pop` chaveado por caminho completo, substitui o `_perf_mark` flat
da Fase 4.5) — pedido do usuário após reportar "muitos picos" que a
lista flat não deixava diagnosticar, ver §30 → Fase 5 (A1,
componentes-deus — reforçado pelo achado A.2) → Fase 6 (B1, pipeline
declarativa) → Fase 7 (pré-lançamento — C2 com prioridade reforçada
pelo achado B.2-B.4, TLS/argon2).

### ✅ Fase 1 — Faxina mecânica de baixo risco (07/08/2026)

Todos os 7 itens do plano fechados:

- `engine/tileset.py` — as 2 funções com `hasattr(func, "_cache")`
  viram dict/variável de módulo (`global`), padrão já usado em
  `ui/floating_text.py::_outline_cache`.
- `ui/quest_system.py` — `_fallback_marker_cache` sai do corpo da
  classe (atributo compartilhado por acidente entre instâncias) pra
  `__init__`, atributo de instância de verdade.
- `RARITY_COLORS` — duplicado 2x em `ui/systems.py`, agora fonte única
  em `content/item_table.py`, as 2 classes referenciam.
- `ENEMY_TIER_CONFIGS`/`RESPAWN_TIMERS` — saíram de
  `engine/entity_factory.py`/`engine/world_systems.py` (lógica) pra
  `content/mob_definitions.py` (dado), mesmo padrão do resto do
  catálogo de mob.
- `RACES`/`CLASSES`/`TIERS` — **plano original previa mover pra
  `content/`; achado real foi diferente**: as 3 listas em
  `engine/components.py` não eram usadas em NENHUM outro lugar do
  codebase (confirmado por grep completo) — código morto, não dado
  disperso. Deletadas, não movidas (CLAUDE.md: "se tem certeza que
  está sem uso, pode deletar").
- `_TALENT_SKILL_REQS` — gerado independentemente em
  `engine/world_systems.py` (2-tupla) e `client/hotbar_handlers.py`
  (3-tupla) — unificado na versão mais rica (3-tupla, com nome do
  talento) em `world_systems.py`; `hotbar_handlers.py` importa de lá.
  2 call sites em `ui/systems.py` precisaram ajustar o unpacking pra
  3 elementos.
- `client/save_sync_handlers.py` — nomes de campo de Item duplicados
  entre serialize/deserialize viraram `_ITEM_STAT_FIELDS` (constante
  única). **Achado incidental durante a unificação: as duas listas já
  tinham divergido de verdade** — `damage_min`/`damage_max` existem em
  `Item` e estavam no loop de deserialize, mas FALTAVAM no de
  serialize — bug real, silencioso: qualquer arma reconstruída pelo
  caminho de fallback `_item_from_data` (comprada em loja/forjada, não
  bate com `loot_tables._T`) perdia o dano depois de um save/load.
  Corrigido como parte da unificação. `icon_key` (nunca foi atributo
  real de `Item`, sempre `None`) removido por ser morto.

**Testes**: `tests/test_item_serialize_roundtrip.py` (novo, 3 testes,
cobre o bug do dano de arma com prova diferencial — fix desligado,
teste falhou exatamente como o bug real falharia, religado). Demais
itens são refatoração mecânica sem lógica nova — verificados por
import/smoke-test direto + suíte completa.

### 🆕 Achado do usuário (07/08/2026) — código de skill dentro do módulo de tile de mapa

Usuário pediu explicitamente uma auditoria mais profunda de
`engine/tileset.py` (além dos 2 caches já corrigidos) e apontou:
`discover_camouflage_variants()`/`get_camouflage_disguise_frame()` (a
skill Camuflagem do Arqueiro) não tinham NADA a ver com o resto do
arquivo — `tileset.py` é sobre tile/mapa/colisão, não sobre skill de
personagem. Efeito colateral real: `server/spell_completion_processor.py`
importava `engine.tileset` INTEIRO (incluindo `OBJECT_SHEET_FAMILIES` e
centenas de linhas de catálogo de spritesheet de mapa que o servidor
não usa) só pra chamar essa função. Acoplamento sem propósito entre
sistema de tile e skill de furtividade.

**Lição sobre o próprio processo de auditoria**: minha varredura
anterior desse arquivo (mesma sessão, pedido do usuário) olhou
correção linha a linha (achou 4 IDs de sprite duplicados, corrigidos
manualmente pelo usuário) mas nunca voltou um passo pra perguntar "esse
código pertence a este arquivo?" — auditoria de correção local não é
o mesmo que auditoria de coesão de módulo; as duas são necessárias.

**Fix (1ª tentativa, ERRADA)**: extraído pra `engine/camouflage.py` —
nome da SKILL, não da categoria. Usuário apontou na hora: nem Veloren
nem AzerothCore fazem "1 arquivo por skill" — Veloren é o oposto (1
`CharacterState` único + sistemas genéricos por TIPO de efeito). Eu
tinha acabado de escrever esse exato achado no benchmark (A.1) e violei
o próprio princípio duas mensagens depois — corrigir "código no lugar
errado" criando um NOVO arquivo nomeado pelo sintoma é a MESMA classe
de erro raiz do B2/B3 (skill sem abstração compartilhada), só que em
miniatura.

**Fix (2ª tentativa, PARCIAL)**: renomeado pra `engine/entity_disguise.py`
— escopado pela CATEGORIA ("sprite de disfarce/troca de aparência
temporária de entidade"), mesmo padrão de `ui/effect_animator.py`
(ícones de efeito de status — categoria, não 1 efeito específico). Só
o NOME do arquivo mudou — o código de dentro continuava 100% hardcoded
pra "camuflagem" (`f"camuflagem_idle{{suf}}.png"` etc.), então uma skill
futura ainda não conseguiria reusar nada, só copiar/colar trocando a
string. Usuário apontou isso na hora: renomear o arquivo não resolve
se o código de dentro só serve pra uma skill.

**Fix (3ª tentativa, REVERTIDA)**: tentei parametrizar (`base_name`
como argumento em vez de hardcoded) — mas escrevi isso DIRETO no
código, sem pesquisa nova, só reciclando o benchmark geral de Veloren
já feito antes (não uma checagem específica pra ESTA decisão). Usuário
barrou na hora: "você já está mexendo no código antes de decidir como
será a arquitetura? antes de buscar na web referências????" —
revertido pra `entity_disguise.py` (2ª tentativa) enquanto pesquisa
de verdade não acontece.

**Fix (4ª tentativa, FINAL — pesquisa de verdade primeiro)**: pesquisa
real via WebSearch/WebFetch, desta vez sobre a decisão ESPECÍFICA (não
reciclando o benchmark geral): AzerothCore/WoW resolve troca de
aparência (poção, skill em si mesmo, skill em alvo, zona) por UM
mecanismo genérico (`Unit::SetDisplayId`) + prioridade entre
transformações ativas — Wowpedia confirma que a Blizzard CONSOLIDOU
isso no patch 6.0.2 depois de ter implementações fragmentadas por
spell (mesma classe de erro das tentativas 1-3 aqui). Usuário perguntou
explicitamente se o mecanismo se aplica a poções/skills-em-alvo (não só
Camuflagem self-cast) e se o sistema devia controlar só "qual aparência"
ou também o código de animação — confirmado (Polimorfia já transforma
o ALVO, não o caster) e confirmada a separação: mecanismo de
renderização genérico (camada 1) + resolução de "qual está ativo"
(camada 2), sem misturar as duas.

Mapeamento completo ANTES de codar (pedido explícito do usuário —
"analisar nosso projeto pra não deixar nenhum gap"): achado que
Camuflagem tem 2 pontos de concessão (servidor autoritativo em
`ui/skill_handlers.py::_skill_camuflagem` + réplica local do cliente em
`client/network_handlers.py`) e 2 de expiração (`engine/world_systems.py`
client-only + `server/world_server.py` server-only, deliberadamente
separados); Polimorfia usa só `StatusEffects`/"polymorph" genérico
(`server/spell_completion_processor.py::_server_polimorfia`), sem ponto
dedicado — JÁ sincroniza pelo caminho normal.

Esse mapeamento mudou o desenho pra MELHOR (mais simples que a versão
aprovada antes da pesquisa): em vez de um componente `AppearanceOverride`
NOVO e sincronizado por rede (que exigiria mudança de protocolo +
replicar os 4 pontos de concessão/expiração pra manter em sincronia —
estado duplicado do que já existe), a solução final é uma FUNÇÃO
resolvedora (`get_active_appearance_override`) que só LÊ o estado que
cada efeito já mantém (`CombatStats.camouflage_timer/camouflage_object`,
`StatusEffects.has("polymorph")`) — zero componente novo, zero mudança
de protocolo. Tabela `_APPEARANCE_SOURCES` (sprite_base → função
checadora) substitui o `if _cam_active: ... elif _polymorphed: ...` de
`ui/systems.py` — fonte nova de troca de aparência = 1 função pequena +
1 linha na tabela, nunca um novo branch.

`discover_sprite_variants(base_name)`/`get_animated_disguise_frame(base_name,
...)` finalmente parametrizadas de verdade (não hardcoded pra
"camuflagem"). Polimorfia ganha suporte a sprite animado de verdade
(hoje só o círculo placeholder — vira fallback TEMPORÁRIO só até o
asset `polimorfia_idle.png`/`polimorfia_run.png` existir, marcado
explicitamente no código pra não virar branch permanente por skill).

**2 achados colaterais durante o mapeamento, NÃO corrigidos agora
(fora do escopo aprovado, registrar e não decidir sozinho)**:
- `server/spell_completion_processor.py::_server_camuflagem` (linha
  1626) parece ser CÓDIGO MORTO — Camuflagem tem `cast_time: 0.0`
  (`content/skill_config.py`), então nunca entra em
  `_pending_spell_completions` (mecanismo exclusivo de spells COM cast
  time, ver docstring do módulo) — o dispatch em `_dispatch["camuflagem"]`
  nunca deveria disparar na prática. Não confirmado 100%, não deletado.
- Cliente (`client/network_handlers.py`) sorteia sua PRÓPRIA variante
  de Camuflagem localmente (`_rand_cam.choice(...)`) em vez de usar a
  que o SERVIDOR já escolheu e mandou — desalinhamento cosmético
  possível (jogador vê um disfarce, servidor internamente "pensa" que é
  outro) — sem impacto de gameplay (campo é só visual), mas inconsistente.

**Testes**: `tests/test_entity_disguise.py` (novo, 9 testes) — cobre
genericidade real (base_name arbitrário funciona, cache não vaza entre
bases) e o resolver (Camuflagem/Polimorfia isoladas, expiração,
entidade sem componentes não quebra, ordem determinística quando ambas
coincidem). Prova diferencial: guard de `camouflage_timer > 0`
temporariamente removido, confirmado que os 2 testes certos falham
exatamente como o bug faria, religado. Suíte completa rodada a cada
tentativa (4x total, uma por versão + revert).

**Regra final em CLAUDE.md** (reforçada depois da 3ª tentativa —
ver "Pontos únicos de verdade", nova entrada "Troca de aparência
temporária de entidade"): pesquisar/desenhar ANTES de codar pra
QUALQUER correção, mapear o projeto pra não deixar gap, nunca reciclar
pesquisa de um tópico mais amplo como se cobrisse a decisão específica.

### ✅ Fase 2 — A2, matar dual-mode `if self._net` (07/08/2026)

**Escopo real ficou bem menor que a estimativa do roteiro.** A
estimativa original ("47 ocorrências em 5 arquivos") contava qualquer
`self._net` em condicional, sem distinguir 2 padrões bem diferentes.
Mapeamento completo (agente Explore, todo o repo) achou 65 ocorrências
em 16 arquivos, classificadas em:

- **Categoria A — dual-mode de verdade** (implementação offline
  duplicada, mutando estado local como se não houvesse servidor): só
  **13 ocorrências, 3 arquivos** — `ui/systems.py` (10: `_add_rage`,
  compra/venda/desfazer de loja, regen de HP/mana por tick, uso de
  consumível, e 2 pontos dentro de `_use_skill_visual_only`),
  `ui/spell_system.py` (1: Pirofagia), `game.py` (2: transição de mapa
  via F12 debug e cave/portal normal). `engine/world_systems.py` e
  `ui/quest_system.py` — citados no roteiro original — não tinham
  NENHUM branch real (já limpos antes ou nunca tiveram).
- **Categoria B — guarda de conexão** (`if not self._net: return` antes
  de mandar mensagem, sem lógica offline alternativa — proteção contra
  `self._net` ainda `None` na janela antes de conectar, não sobra de
  modo offline): **51 ocorrências, 14 arquivos**, quase todo
  `client/*_handlers.py` (trade/duelo/grupo/arena/BG — recursos que só
  existem online, o guard nunca teve "modo offline" pra remover). Fora
  de escopo, intocado.

**Fechado (Categoria A, 12 de 13 pontos)**:
- `ui/systems.py`: `_add_rage` virou no-op puro (comentário atualizado,
  já era no-op condicional); `_buy`/`_buy_qty`/`_sell` perderam o bloco
  `# Offline: aplica tudo localmente`; botão "Desfazer" da loja
  (`_undo()` + handler de clique) removido — já era visualmente inerte
  online antes desta limpeza (histórico de transação nunca populado),
  render do botão mantido intocado de propósito (matar a lógica morta
  não é a mesma decisão que tirar um botão da UI — isso é decisão de
  produto, registrado, não decidido aqui); ticks de `ActiveRegen`/
  `ActiveManaRegen` pararam de mutar HP/mana local; `_use_consumable`
  perdeu o bloco de aplicação local.
- `ui/systems.py::_use_skill` — corpo inteiro (98 linhas: talent lock,
  GCD, dispatch por `_skill_<id>`) já era 100% morto (`_server_authoritative`
  sempre `True` neste branch) — virou wrapper de 1 linha pra
  `_use_skill_visual_only`. A flag `_server_authoritative` em si também
  foi removida (3 arquivos: `ui/systems.py`, `game.py`,
  `server/world_server.py`) por nunca mais variar. **Os métodos
  `_skill_<id>` (definidos como métodos separados da classe, não fazem
  parte do corpo deletado) continuam intactos** — são a fonte que o
  servidor reusa via `getattr` em `skill_processor.py` (débito B3
  conhecido, não é escopo desta fase).
- `ui/spell_system.py::_fire_cone` (Pirofagia) — mesmo padrão, bloco de
  dano-em-cone-local removido.
- `game.py` — os 2 branches de transição de mapa perderam o fallback
  `_do_transition(...)` direto (bypass do round-trip servidor).

**Deferido pra Fase 3, não Fase 2** — `ui/systems.py::_use_skill_visual_only`
(~400 linhas, a função que roda de verdade hoje): ao ler por completo,
não é um `if online: X else: offline: Y` limpo como os outros 12 pontos
— é UMA função com ~6 pontos onde `_is_online` liga/desliga um pedaço
(resolução de alvo, criação de `SpellCast` visual, envio de
`CAST_SKILL`, timer do Fatiador de Corpos), só 1 desses pontos
(linha ~4379, resolução de alvo) tem um `else:` com lógica offline
alternativa de verdade — os outros 5 não têm alternativa, só pulam o
passo. É exatamente o código que o roteiro já tinha marcado como
"cuidado especial — é o que o servidor reusa, não remover às cegas" —
decisão tomada com o usuário: fica pro Fase 3 (redesenho, não limpeza
mecânica), que já vai reescrever essa função do zero via máquina de
fases (achado A.1 do benchmark, Veloren `CharacterState`).

**Contexto do usuário sobre a causa raiz da bagunça** (07/08/2026):
o projeto começou offline (RPG simples single-player) e foi convertido
pra online depois — os `if self._net/else` são resíduo dessa conversão
colada em cima do código antigo, não um desenho deliberado. Direção
explícita pro Fase 3: qualquer sistema de UI (aqui e em outros lugares
citados pelo usuário — Pirofagia, canalização, projétil) deve seguir
separação estrita: **UI só mostra estado (cooldown, cargas etc.),
nunca calcula**; jogador aperta botão → UI chama sistema → sistema
processa (server-authoritative sempre) → UI só exibe o resultado.
Pesquisar essa separação (Veloren + referência geral de arquitetura de
jogo) antes de desenhar o Fase 3, não só reciclar o achado A.1 já
registrado.

**Regressão achada no playtest manual (não é desta fase — vazou da
Fase 1)**: `ui/quest_system.py` — ao mover `_fallback_marker_cache` do
corpo da classe pro `__init__` (item da Fase 1, ver acima), fui posto
na classe ERRADA (`QuestSystem.__init__`, linha 51) em vez de
`QuestDialogSystem.__init__` (linha 458), que é quem de fato usa
(`_fallback_marker_surf`/`render_world`, linha ~717) —
`AttributeError` ao clicar em qualquer NPC com marcador de quest sem
ícone customizado. Suíte automatizada (930 testes) nunca pegou porque
nenhum teste exercita esse caminho de render — só apareceu no playtest
manual do usuário, exatamente a razão de ter pedido validação manual
antes de fechar a fase (ver CLAUDE.md, "'Corrigido' exige reproduzir o
sintoma"). Corrigido (movido pra `QuestDialogSystem.__init__`),
reproduzido o crash exato fora da suíte e confirmado que sumiu, suíte
completa re-rodada limpa (930/930), depois confirmado pelo usuário em
playtest real.

**Testes**: suíte completa, 3 rodadas ao longo da fase (930/930 cada
vez, limpa) + playtest manual do usuário confirmando loja/consumíveis/
Pirofagia/transição de mapa/F12 debug funcionando.

### ✅ Fase 3 — piloto: `build_channeling_from_skill` (Calamidade Flamejante, 07/08/2026)

Pesquisa feita antes de desenhar (obrigatória, ver regra acima): Veloren
(`CharacterState`/`CharacterBehavior`, fases Buildup/Charge/Action/Recover,
servidor autoritativo, cliente só lê estado sincronizado — l33l33.com +
DeepWiki) + levantamento completo das 26 skills do catálogo (agente
Explore) confirmou que o eixo real de generalização é **quando o efeito
resolve** (na hora / após timer de cast / após ator que confirma hit
depois — projétil, já funciona certo / repetindo em ticks — canalização),
não "tem cast_time". Plano formal salvo em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`,
aprovado pelo usuário antes de codar (EnterPlanMode/ExitPlanMode).

**Achado corrigido 2x durante o próprio desenho** (registrar o processo,
não só o resultado): 1ª leitura minha concluiu que
`ChannelingSystem._apply_tick` (cliente, `ui/spell_system.py`) aplicando
dano local via `_apply_magic_damage` seria uma falha de autoridade ATIVA
(cliente decidindo dano sozinho, potencial exploit). Verificação contra
o padrão já documentado (`RemoteEntityMeta`/`RemoteControlled` — mob
online nunca tem `Enemy`+`CombatStats` local) mostrou que a query nunca
casa em jogo online real — é código morto, não brecha. Uma 2ª leitura
(via Plan agent, sem o contexto completo desta sessão) levantou a
hipótese OPOSTA — que seria lógica viva pro "modo offline" (single-player
sem servidor) e por isso devia ser preservada atrás de um gate novo.
Também verificada e descartada: `game.py::_connect_online()` (linha 543)
não tem nenhum branch condicional, roda sempre; comentário já existente
na própria função (linha 530-533) confirma que o modo offline foi
removido dali faz tempo (item A2 §11); nenhum teste referencia
`ChannelingSystem`/`_apply_tick`. Conclusão final, com as 2 hipóteses já
descartadas: código morto de verdade, sem uso vivo em NENHUM modo —
deletado (não guardado atrás de gate novo).

**Mudanças** (arquivos, ver plano pra detalhe linha-a-linha):
- `content/skill_config.py` — `calamidade_flamejante` ganha `params`
  (padrão já usado por 15+ skills) — os 6 números que viviam duplicados
  como literais em `ui/skill_handlers.py` E `ui/spell_system.py` saem do
  código.
- `engine/core_systems.py` — `build_channeling_from_skill(skill, x, y)`,
  fonte única da tradução catálogo → componente `Channeling` vivo.
- `ui/skill_handlers.py` / `ui/spell_system.py::AoeTargetingSystem` —
  os 2 pontos que construíam `Channeling(...)` na mão agora chamam a
  fábrica; `_start_channel` também parou de checar
  `if spell_id == "calamidade_flamejante"` (string hardcoded) e passou a
  checar `skill.is_channeled` (campo genérico já existente no catálogo,
  já lido, **nunca tinha nenhum consumidor** — achado morto por
  coincidência) — qualquer canalização futura já flui por aqui sem
  tocar neste arquivo de novo.
- `ui/spell_system.py::ChannelingSystem._apply_tick` — método inteiro
  removido (era só o laço de dano morto). Resto da classe (predição de
  mana pra UI, cancelamento por movimento, `render()` do círculo) —
  intacto, é responsabilidade legítima de cliente.
- `engine/components.py::Channeling` — `tick_timer`/`mana_timer` viram
  campos reais do `__init__` (antes o servidor grudava por fora via
  `getattr`/`hasattr`, mesma forma de onda, só declarada onde deveria).
- `server/spell_completion_processor.py::_process_player_channeling` —
  acesso direto aos 2 campos agora que são reais, sem mudança de
  comportamento.

**Prova de que generaliza** (não desenhado ainda, só demonstrado):
`fatiador_de_corpos` hoje não usa `Channeling` — 2 floats soltos em
`CharacterStats`, decrementados com aritmética copiada 3x (offline+
visual-online em `ui/systems.py`, autoritativo em `server/world_server.py`).
Migrar depois = dar a ela um `Channeling` construído pela MESMA fábrica
(alvo = tile do próprio caster, sem etapa de mira) e colapsar as 3
cópias na mesma trilha de tick que este piloto introduz.

**Testes**: `tests/test_calamidade_channel_config.py` (novo, 3 testes —
valores do `Channeling` batem com o catálogo, prova diferencial mudando
o catálogo, e prova de que `ChannelingSystem` não aplica mais dano local
nem em um cenário sintético onde a query antiga teria casado — prova
diferencial feita, código morto reintroduzido temporariamente, confirmado
que o teste pegava a regressão, revertido). `tests/test_flt_dedup.py::
TestCalamidadeFlamejanteNaoDuplicaFLT` (já existia, valida que a
assinatura do construtor de `Channeling` não mudou) continua passando

## Doc — SISTEMAS_ECS.md/COMPONENTES_ECS.md descrevem comportamento de modo offline já removido — 08/08/2026

Achado durante a reorganização de `ARQUITETURA_ONLINE.md` (split em
atual + histórico). `tests/test_calamidade_channel_config.py:19` já
registra explicitamente que "este branch não tem mais modo offline"
(confirma o item A2 §11 e a decisão de produto — só existe versão
online hoje). Vários trechos de `SISTEMAS_ECS.md`/`COMPONENTES_ECS.md`,
porém, ainda descrevem um branch `self._net is None` ("modo offline")
como se fosse um caminho de execução vivo e alternativo, quando na
verdade é código morto (removido, não só renomeado):

- `SISTEMAS_ECS.md:26` — tabela da PirofagiaSystem: "**Offline:** calcula
  dano localmente" como se ainda fosse um modo selecionável.
- `SISTEMAS_ECS.md:258` — ManaSystem "só prediz esse regen quando
  offline (`self._net` não setado)".
- `SISTEMAS_ECS.md:268/300` — `quest_logic` "usada pelo cliente (caminho
  offline) E pelo servidor (caminho online, autoritativo)".
- `COMPONENTES_ECS.md:217` — `Corpse(...)`: "Offline only — servidor usa
  `_corpses` dict".
- `COMPONENTES_ECS.md:239` — `SOUNDS.play_mob_sounds(...)`: "offline, sem
  atenuação".

**Não corrigido agora** (decisão do usuário, 08/08/2026, durante a
mesma sessão da reorganização de docs) — misturaria "reorganizar
arquivo" com "validar comportamento de código linha a linha", que são
tarefas de natureza diferente. Fica registrado aqui pra quando alguém
for mexer nesses trechos: antes de aceitar a frase do doc como
verdade, confirmar contra o código atual se o `self._net is None`
citado ainda corresponde a um caminho alcançável (provavelmente não —
mesmo padrão já confirmado morto em `ChannelingSystem._apply_tick`,
acima) ou se é só estado transitório de "ainda não conectou ao
servidor" (tela de login), não uma "versão offline" de fato.
sem alteração. Suíte completa: 933/933 (930 + 3 novos), limpa.

### ✅ "Imune" no floating text + imunidade de controle do Fatiador de Corpos (07/08/2026)

Usuário lembrou de uma característica esquecida do Fatiador de Corpos
(imunidade durante o canal) e generalizou em 2 pedidos explícitos, o
2º corrigindo o alcance do 1º:
1. "O correto pra todo tipo de imunidade": imunidade de DANO mostra
   "Imune" no floating text; imunidade de EFEITO também, quando algo
   tenta aplicar e é bloqueado — sistema recebe a tentativa mas segura
   o efeito, nunca falha silenciosamente.
2. Depois: "esqueci de mencionar" — não é só slow/root, é qualquer
   coisa que tire o CONTROLE do jogador (stun/poly/etc.) — "a única
   coisa que ele não é imune é a dano" — e precisa ser DINÂMICO, "não
   algo que sirva só pra essa skill".

Pesquisa (AzerothCore, pedida explicitamente): `SpellMissInfo::
SPELL_MISS_IMMUNE` é resultado de 1ª classe (mesma família de MISS/
DODGE/PARRY), bate com nosso `outcome` já existente; imunidade de
mecânica de CC é `SPELL_AURA_MECHANIC_IMMUNITY_MASK` — uma AURA
(buff), não um flag solto — confirmou o redesenho abaixo antes de
implementar (2ª versão, a 1ª usava `CombatState.is_slow_root_immune`,
um flag booleano específico de slow/root — trocado depois do 2º
pedido).

**Parte A — "Imune" no dano (fecha gap real, não é mecanismo novo)**:
`deal_damage()` (`engine/world_systems.py`) já retornava outcome
`"immune"` pra `CombatState.is_immune`, mas nunca chamava
`_emit_avoidance_feedback()` (mesma função que já mostra "Errou!"/
"Desviou!"/"Aparou!"/"Evadiu!") — só faltava ligar. Cliente
(`ui/spell_system.py::_apply_magic_damage`) e as 3 renderizações de
`COMBAT_RESULT` (`client/remote_entity_handlers.py`, mob-alvo/player-
local-alvo/player-remoto-alvo) ganharam a mesma entrada "immune"/
"Imune". Servidor foi o mais trabalhoso: `_apply_final_damage`
(`server/spell_completion_processor.py`) retornava BOOL (colapsava
"imune"/"morto"/"amigável" tudo em `False`) — virou string real
(`"applied"|"killed"|"blocked_immune"|...`); dano mágico bloqueado por
imunidade agora propaga outcome "immune" tanto no relatório do tick
(`_combat_this_tick`, canal de dano contínuo — canalização) quanto no
`SKILL_RESULT` de conclusão de cast (`consume_skill_results()`, canal
SEPARADO — achado real ao escrever o teste: os dois não são a mesma
coisa). Rastreio por eid via `_magic_blocked_immune_eids` (set, não
bool — Nova Congelante acerta vários alvos, um bool só lembraria do
último).

**Parte B — imunidade de controle (versão final, dinâmica)**: novo
status effect `"cc_immune"` (`content/status_effects_data.py`) — a
imunidade em si é um efeito de verdade, mesma infra de duração/
expiração/ícone/sync de qualquer outro (StatusEffectSystem expira
sozinho, nenhum flag bespoke). `apply_effect()` (`engine/
core_systems.py`) ganha guard genérico: bloqueia (+ mostra "Imune")
qualquer `effect_type` com `blocks_move`/`blocks_act=True` no catálogo
(stun/sleep/fear/root/polymorph/disoriented — cobre efeito de controle
NOVO automaticamente, zero lista de nomes) mais `"slow"` explícito (não
tem blocks_move/act, é só velocidade, mas pedido do usuário). DoT
(poison/bleed/burn) fica de fora — é dano. `_skill_fatiador_de_corpos`
(`ui/skill_handlers.py`) chama `apply_effect(world, player, "cc_immune",
duration=...)` + um loop dinâmico (mesmo critério do guard) que dispela
qualquer efeito de controle JÁ ativo na ativação — nenhuma skill futura
que quiser a mesma proteção precisa fazer mais que 1 chamada de
`apply_effect`.

**Achado real durante os testes**: incluir stun/polymorph no teste de
"dispel na ativação" quebrava o cast INTEIRO (não só o dispel) — porque
`is_action_locked()` (stun/polymorph têm `blocks_act=True`) já rejeita
o `CAST_SKILL` do próprio Fatiador antes de qualquer handler rodar.
Comportamento CORRETO (mesma lógica de qualquer MMO: não dá pra castar
parado enquanto atordoado) — só root/slow são "escapáveis" via auto-
cast; stun/sleep/fear/polymorph/disoriented só ficam cobertos se a
imunidade já estava ativa ANTES (ex.: usar Fatiador preventivamente).
Teste corrigido pra refletir isso, não pra "consertar" o comportamento.

**Testes**: `tests/test_immunity_feedback.py` (novo, 12 testes — guard
genérico bloqueia stun/root/slow/polymorph, não bloqueia DoT, prova
diferencial revertendo o critério dinâmico pro antigo (só slow/root) e
confirmando que stun/polymorph passam a vazar; Fatiador concede
`cc_immune`/dispela slow+root ativos/expira sozinho após a duração;
outcome "immune" chega no SKILL_RESULT de Nova Congelante contra alvo
imune). Suíte completa: 956/956 (1 teste pré-existente e não
relacionado, `test_mob_despawn_sent_to_both_players`, é flaky —
confirmado independente desta mudança: passa isolado na maioria das
rodadas, falha ocasionalmente mesmo sem nenhuma alteração em spawn/
despawn/AOI nesta sessão — registrado aqui como pendente, não
investigado a fundo ainda).

---

### 🟢 B3 fechado — `HeadlessSkillHandler` (10/08/2026)

**Sintoma:** servidor (`WorldServer.__init__`) instanciava
`ui.systems.SkillSystem` só para reusar a mixin `SkillHandlers` (todos os
`_skill_<id>`) — o conteúdo sempre foi pygame-free, mas `ui/systems.py`
faz `import pygame` no topo, então o processo do servidor carregava
pygame de verdade (mascarado pelo workaround `SDL_VIDEODRIVER=dummy`,
que virou "necessário" só por causa disso).

**Fix:** `ui/skill_handlers.py` → `engine/skill_handlers.py` (mesma
técnica de extração byte-idêntica já usada ~13x no projeto — `diff`
confirmou conteúdo idêntico antes de qualquer edição). Nova classe
`HeadlessSkillHandler(SkillHandlers)` no fim do arquivo, sem herdar de
`engine.world_systems.System` (grep confirmou zero `isinstance(x,
System)` no projeto — dependência desnecessária). `server/world_server.py`
passou a instanciar `HeadlessSkillHandler` em vez de `ui.systems.
SkillSystem`; `import pygame`/`pygame.init()` removidos de
`world_server.py` (os `SDL_VIDEODRIVER/AUDIODRIVER=dummy` ficaram como
rede de segurança barata, não mais necessidade real).

**2ª rodada — vazamentos irmãos, achados só depois de verificar de
verdade** (script isolado: `SDL_VIDEODRIVER` DESLIGADO de propósito,
processo novo, `import` + instancia `WorldServer()`, checa `'pygame' in
sys.modules`) — a mudança acima sozinha NÃO bastava, pygame ainda
carregava:

1. `engine/stats_system.py` — `from ui.systems import System` (deveria
   vir de `engine.world_systems`, onde `System` é definido de verdade;
   `ui/systems.py` só reexporta) + `from ui.floating_text import FLT` /
   `from ui.sound_manager import SOUNDS` direto (ignorando o façade
   `engine/fx.py` que o resto do projeto já usa pra isso). Alcançado via
   `server/debug_battleground.py` → `server/instance_progression.py` →
   `stats_system.py`, disparado em construção normal de `WorldServer`.
   Fix: os 3 imports trocados pro par certo (`engine.world_systems.
   System`, `engine.fx.FLT/SOUNDS` — uso idêntico, troca direta).

2. `engine/skill_handlers.py::_skill_pirofagia` (branch "modo servidor",
   cone direto sem alvo) — `from ui.spell_system import
   _apply_magic_damage`, lazy, só disparava quando um player castava
   Pirofagia de verdade contra o cone. Diferente do caso 1 (só import
   errado): a função em si é 100% pygame-free (delega tudo a
   `apply_damage_core`), só morava no módulo errado — já era o mesmo
   diagnóstico do item CRÍTICO B acima ("Extrair para core_systems.py"),
   nunca executado. Fix real: `_apply_magic_damage` virou
   `apply_magic_damage_shared()` em `engine/core_systems.py` (FLT/SOUNDS
   trocados pro façade `engine.fx`, resto idêntico); `ui/spell_system.py`
   agora importa de volta (`from engine.core_systems import
   apply_magic_damage_shared as _apply_magic_damage`) pros seus 3 call
   sites client-side, sem mudar comportamento nenhum aí.

**Regressão real pega pela suíte completa, não pela análise estática**:
depois dos 2 fixes acima, `WorldServer()` já não carregava mais pygame —
mas `TestPunhoNoQueixo::test_cast_deals_damage_and_stuns` quebrou:
`HeadlessSkillHandler` não tinha `_resolve_target`. Causa: esse método
(+ `_is_on_screen`, do qual depende) nunca esteve dentro da mixin
`SkillHandlers` (o conteúdo movido pro item B3 principal) — vivia
definido DIRETO na classe concreta `ui.systems.SkillSystem`, ao lado da
mixin, não dentro dela. `HeadlessSkillHandler` herda só de
`SkillHandlers`, não de `SkillSystem` — ficou sem acesso. Gravidade
real: `_resolve_target` é chamado em 11 dos handlers de skill (praticamente
toda skill com alvo, não só Punho no Queixo) — QUALQUER uma quebraria
com `AttributeError` no servidor, mas `server/skill_processor.py`
(linha ~388) captura a exceção genericamente e só loga + segue (pra não
derrubar o tick por 1 skill malformada) — sem teste cobrindo o dispatch
server-side de cada skill especificamente, o sintoma seria "a skill não
faz nada" em produção, não um crash visível. Só 1 dos 11 call sites
tinha teste nesse caminho exato; os outros 10 não tinham cobertura
alguma pro dispatch via `_process_skill_requests()`, o que teria deixado
o bug invisível na suíte se não fosse esse único teste. Fix: `_is_on_screen`
+ `_resolve_target` movidos de verdade pra dentro da mixin
`SkillHandlers` (`engine/skill_handlers.py`), removidos de
`ui/systems.py::SkillSystem` (agora herda da mixin, sem duplicação).
Lição: extração "byte-idêntica" de um arquivo isolado não garante que
TODAS as dependências daquele conteúdo estavam no mesmo arquivo — vale
conferir com uma varredura de `self.<método>()` não definidos localmente,
não só copiar o arquivo e assumir que basta.

**Análise mais ampla feita antes de fechar** (pedido do usuário — "algo
mais está sendo afetado por esse tipo de erro?"): grep exaustivo por
`from ui.`/`import ui.`/`import pygame` em `engine/`, `server/`,
`content/`, `shared/`. Achados sem ação necessária: `ui.combat_log`
(documentado pygame-free, import direto é o padrão certo em qualquer
lado); `ui.ui_components` (`engine/entity_factory.py`, também
pygame-free apesar do nome de pasta — só uma inconsistência de
localização, não um bug); os 2 `import pygame` locais restantes em
`engine/` (`world_systems.py::ProjectileSystem.render()`,
`entity_disguise.py::get_animated_disguise_frame()`) já são
corretamente client-only (render/sprite loading, nunca chamados do
servidor — confirmado via grep dos call sites).

**Achados colaterais, NÃO corrigidos agora (fora do escopo do vazamento
de pygame, registrados aqui pra não perder)**:
- Pirofagia modo-servidor nunca calcula crit (o próprio código já tinha
  um `TODO` nesse sentido) — `_skill_outcome` (usado pelo dispatcher
  genérico em `server/skill_processor.py` pra montar o `SKILL_RESULT`)
  só é setado por `deal_damage()` (físico); Pirofagia nunca chama
  `deal_damage`, então o outcome fica sempre no default `"hit"` setado
  no início do processamento da skill.
- ~~Alvos imunes DENTRO do cone de Pirofagia (que não são o `tid`
  explícito do cast) não aparecem no `SKILL_RESULT` nenhum~~ — **resolvido
  em 10/08/2026, ver §15** (outcome por alvo, não mais 1 valor
  compartilhado pro cast inteiro).

**Testes:** suíte completa rodada (ver resultado anexado nesta sessão).
Nenhum teste automatizado novo pra esta extração especificamente —
mudança é relocação byte-idêntica + troca de import, mesma categoria
de risco da extração de `SkillHandlers` acima (coberta pela suíte
existente, não pela política de teste-sempre do catálogo de pontos
únicos de verdade).

---

## 14. Débito de performance — estruturas de dados leves em caminho quente (10/08/2026)

**Contexto:** ao aprovar o desenho do fix de "outcome por alvo em skill AOE"
(canal `_skill_results_this_tick`/`server/skill_processor.py`, ver seção
anterior — a variável `last_outcome` é compartilhada pro cast inteiro,
referências Veloren/AzerothCore confirmaram que o certo é cada alvo carregar
o PRÓPRIO outcome, não 1 valor só pra todos), o usuário chamou atenção pra
um ponto de arquitetura mais amplo: **preferir ferramentas leves (dict/
tuple/set com tipos primitivos) a objetos Python novos (classe/dataclass
instanciada por evento) em caminho quente — por-tick, por-entidade, por-alvo
de AOE.** Regra crua registrada em `CLAUDE.md` (seção "Performance e
concorrência").

**Por que isso importa aqui especificamente:** o registro plugável que o
fix de outcome-por-alvo vai criar (mesma família de `_svc`/`_damage_tracker`/
`_lethal_interceptor` em `engine/core_systems.py`) é exatamente o tipo de
estrutura que crescer errado — se cada `apply_damage_core()` criasse um
objeto `DamageResult` novo por chamada em vez de só gravar `dict[int, str]`,
isso seria alocação de objeto Python extra em TODO golpe de TODO combate do
jogo (o caminho mais quente que existe no servidor). O fix aprovado usa dict
de módulo com tipos primitivos (int→str), não uma classe nova — consistente
com a regra, não uma exceção a ela.

**Status:** débito identificado, **não medido por profiling ainda** (nenhum
`cProfile`/`py-spy` rodado especificamente pra isso) — não é motivo pra
bloquear trabalho novo, é critério de escolha quando a estrutura de dados de
algo novo é decisão em aberto. Auditoria completa de "onde o projeto já usa
objeto pesado num caminho quente que dava pra ser dict/tuple" fica pra
quando o roadmap chegar nas fases de performance (§13, Fase 6 em diante) —
registrar aqui não substitui medir antes de otimizar (regra já existente,
"Algoritmo antes de micro-otimização", `CLAUDE.md`).

---

## 15. Outcome por alvo em skill AOE — implementado (10/08/2026)

**Sintoma original:** alvo imune atingido pelo cone da Pirofagia (não é o
`tid` explícito do cast — a skill não precisa de alvo) nunca aparecia no
`SKILL_RESULT`, nem como dano (óbvio, é 0) nem como "Imune". Achado pelo
usuário ao revisar o fix de "Imune" desta sessão (item B3 acima).

**Causa raiz confirmada por pesquisa (Veloren + AzerothCore, pedida
explicitamente):**
- Veloren (`common/src/outcome.rs`): `Outcome::HealthChange` carrega
  `target: Uid` — cada resultado de combate é um evento independente,
  autocontido, com a identidade do alvo dentro dele. Uma AOE que acerta 5
  alvos gera 5 eventos separados.
- AzerothCore: `m_UniqueTargetInfo` é uma lista com 1 `TargetInfo` por
  alvo atingido (cada um com seu próprio `missCondition`); o servidor
  itera e manda `SMSG_SPELLNONMELEEDAMAGELOG` **um pacote por alvo**.
- As duas referências, arquiteturas bem diferentes (ECS/Rust vs. pacote
  binário WoW-style), convergem no mesmo princípio: outcome é dado
  POR-ALVO, nunca 1 valor só compartilhado pro cast inteiro.
- Diagnóstico local: `server/skill_processor.py` já montava a lista de
  resultados por alvo (`results_targets`, isso estava certo), mas
  preenchia o campo `outcome` de CADA entrada lendo de `_skill_outcome`
  — 1 variável só (`CombatSystem.last_outcome`, `engine/world_systems.py`),
  setada pelo dano FÍSICO de alvo único (`resolve_attack_outcome`).
  Magia nunca tocava essa variável; e o filtro de inclusão de alvo com
  `damage==0` exigia `mob_eid == tid`, que a Pirofagia nunca tem.

**Fix:** `apply_damage_core()` (`engine/core_systems.py`, ponto único de
escrita de HP) passou a gravar o outcome de CADA chamada num registro por
alvo, `LAST_DAMAGE_OUTCOMES: dict[int, str]` — dict de módulo com tipos
primitivos (int→str), não uma classe/objeto novo por evento (ver §14,
regra de estrutura leve em caminho quente). Automático: nenhum call site
de `apply_damage_core` precisa passar nada a mais (mesmo princípio de
`_damage_tracker`/`_lethal_interceptor`, já plugáveis do mesmo jeito).
`server/skill_processor.py`: limpa o dict antes de cada resolução (mesmo
ponto/motivo que já limpava `last_outcome = "hit"`); no loop de coleta,
cada alvo agora resolve o PRÓPRIO outcome — `blocked_immune`→`"immune"`/
`blocked_evade`→`"evade"` do registro novo tem prioridade sobre o
`_skill_outcome` compartilhado (fallback, ainda correto pro caso físico
de alvo único que não passa por `apply_damage_core` isoladamente por
alvo). Alvo com "immune"/"evade" agora entra no relatório **mesmo não
sendo o `tid`** — cobre qualquer AOE futura de graça, não só a Pirofagia.

**Testes:** `tests/test_immunity_feedback.py::TestOutcomePorAlvoEmAoeSemTid`
— 2 mobs no cone da Pirofagia (1 imune, 1 normal), confirma outcome
`"immune"`/`damage=0` pro imune e `"hit"`/`damage>0` pro normal na MESMA
chamada; prova diferencial substitui `LAST_DAMAGE_OUTCOMES` por um dict
cujo `.get()` sempre retorna `None` (simula "nunca populou", comportamento
pré-fix) e confirma que o alvo imune de fato some do relatório — prova que
o teste principal falharia sem a correção. Suíte completa: 959/959 (o
teste flaky de despawn passou desta vez, nenhuma falha).

---

## 16. Limpeza `_is_online` em `_use_skill_visual_only` — "Parte 1" fechada (10/08/2026)

**Contexto:** último item do roadmap re-escopado após o fechamento do B3
(§ anteriores) — mecânico, baixo risco arquitetural, mas função grande
(427 linhas, `ui/systems.py::_use_skill_visual_only`, caminho client-side
de TODA skill do jogo).

**Achado ao mapear:** `self._skill_system._net` é setado UMA vez, em
`game.py:548`, logo após `_connect_online()` — antes do loop principal
que permite qualquer input de hotbar. `_is_online = self._net is not
None` é `True` o tempo inteiro em que a função é alcançável; as 5
branches `else`/`not _is_online` (auto-seleção "offline" de alvo,
aplicar cooldown/GCD/som "offline" sem esperar servidor, enter_combat
"offline") eram código morto — nunca executam em produção. Nenhum teste
automatizado chama `_use_skill_visual_only` diretamente (só `client/
hotbar_handlers.py` chama de verdade), então a remoção não tinha rede de
segurança de suíte — validação manual em jogo necessária (registrado
como pendente, não decidida ainda com o usuário quando esta entrada foi
escrita).

**Fix:** removidas as 5 branches mortas (auto-seleção de alvo sem
`_resolve_target`, cooldown/GCD/som aplicados localmente sem esperar
servidor, `enter_combat` fora do passo 4) — mantido só o corpo que
sempre rodou de verdade, sem reescrever lógica, só desindentar. Variável
`_is_online` removida (não faz mais sentido sem branch alternativa).
Docstrings de `_use_skill`/`_use_skill_visual_only` reescritos — o
antigo descrevia um fluxo "offline" de 7 passos como se existisse de
verdade em `_use_skill()` (que hoje é só 1 linha delegando pra
`_use_skill_visual_only`) e citava o débito B3 como "conhecido" (já
fechado, § anteriores).

**3 checks hardcoded de skill_id (escopo original da "Parte 1")** —
revisados individualmente em vez de generalizados em bloco, pesquisa
Veloren/AzerothCore confirmou que nem todo hardcode é débito:
- `_skill_has_proj = skill_id in {"bola_de_fogo"}` (gate de LOS/parede) —
  generalizado: campo novo `has_projectile: True` no catálogo
  (`content/skill_config.py::SKILL_CATALOG["bola_de_fogo"]`), lido
  dinamicamente em vez de literal hardcoded. Eixo genuinamente reusável
  (qualquer skill futura com projétil visual precisa do mesmo gate).
- Proc de Chama Interna (`skill.skill_id == "bola_de_fogo" and
  fire_instant_ready`) e timer local do Fatiador de Corpos
  (`skill.skill_id == "fatiador_de_corpos"`) — **mantidos como estão**.
  AzerothCore resolve interação única de 1 talento com 1 feitiço via
  `SpellScript` dedicado (registrado pelo ID do feitiço), não generaliza
  em dado; Veloren mantém comportamento de habilidade única como código
  específico do variant, não campo de struct compartilhado. Generalizar
  esses 2 agora seria abstração para 0 casos de reuso reais hoje —
  contra a regra do projeto de não codar para hipótese futura.

**Testes:** suíte completa 959/959 (nenhuma falha, nem a flaky).
**Pendente:** validação manual em jogo — usar pelo menos 1 skill melee
(guerreiro), 1 skill à distância com alvo (Bola de Fogo, incluindo
cenário com parede no meio pra confirmar o LOS check ainda bloqueia) e
Fatiador de Corpos, confirmando cooldown/GCD/som/feedback de erro
("Fora de alcance"/"Alvo amigável"/"Nenhum alvo") continuam idênticos.

---

## 17. B6 resolvido (parcial, deliberado) — `InstancedMatchMixin` (10/08/2026)

**Achado original (§13):** `server/match_processor.py` (Arena) e
`server/bg_queue_processor.py` (Battleground) compartilham só a camada de
baixo (`WorldServer._load_instance`/`_unload_instance`), reimplementando
DO ZERO fila/entrada/saída de partida — métodos espelhados.

**Mapeamento completo dos 2 arquivos (1238 linhas) antes de decidir**:
duplicação real de FORMA em vários pontos (fila, propose, accept,
pending/countdown, leave), mas divergência real de SEMÂNTICA na maioria
— Arena é eliminação/1-vida com congelamento dos sobreviventes ao
decidir (`is_immune`+`is_stunned`, ninguém morre de verdade até o golpe
letal); BG é corrida-por-objetivo (Nexus) com respawn contínuo até
alguém sair, sem conceito de W.O./congelamento, mais 3 sistemas inteiros
que a Arena nem tem (`_tick_bg_respawns`, `_tick_bg_kda_hud`,
`_bg_sample_gold_earned`).

**Decisão de escopo (autorizada pelo usuário: "decida baseado no nosso
ideal... lembrando sempre de checar como nossas referências tratam
esses pontos")**: unificar só os 3 pedaços genuinamente IDÊNTICOS (mesma
sequência de passos, mesmos nomes de variável nos 2 arquivos) —
`InstancedMatchMixin` (`server/instanced_match_processor.py`):
- `_im_sweep_accept_deadline` — detecta janela de aceite vencida + limpa
  convites pendentes (o que fazer com quem não apareceu continua no
  chamador — semântica diverge: Arena decide W.O., BG só devolve).
- `_im_open_gate_if_ready` — abre portão físico + ativa minion lanes no
  fim do countdown (só o conjunto de tiles e o evento de notificação
  mudam por modo, via hooks).
- `_im_results_timeout` — força saída de quem ficou na tela de resultado
  além do tempo limite (100% idêntico antes da extração).

**NÃO unificado de propósito** — accept completo (spawn calc diverge:
Arena usa `ARENA_MODES[mode]["spawns_a/b"]` com módulo simples, BG usa
offset+fallback de walkability; BG tem 4 blocos de setup extra que Arena
não tem — progressão normalizada, snapshot de stats, tracking de gold),
propose (shape do dict de partida diverge — Arena tem
`eliminated`/`damage_by_eid`/`arena_locked`/`winner_members`, BG tem
`winner_faction`/`respawn_timers`/`stat_snapshots`/`gold_earned`/etc.),
leave (Arena descongela is_immune/is_stunned SE arena_locked; BG chama
`exit_normalized_progression` + limpa 4 dicts de tracking que Arena nem
tem), fila/token (formatos diferentes: Arena usa `eid` OU `party_id` cru
dependendo do modo; BG usa tupla `("solo"|"party", val)` sempre).
Justificativa (Veloren/AzerothCore, pesquisa explícita — mesmo critério
já usado nesta sessão pra NÃO generalizar Chama Interna/timer do
Fatiador): forçar essas partes numa base com hook pra cada divergência
produziria uma função "compartilhada" cheia de callbacks — menos legível
que 2 funções separadas, não é modularidade, é ofuscação. Mesmo o
`Battleground` do AzerothCore, que compartilha mais que isso entre
battlegrounds DIFERENTES entre si, mantém a lógica de
spawn/setup (`AddPlayer`) largamente na subclasse concreta — só o
bookkeeping genérico de sessão vai pra base.

**API pública intocada** — nenhum método chamado por `server/session.py`
ou pelos handlers de cliente (`client/arena_handlers.py`, handler de fila
de BG) mudou de nome/assinatura; a extração é 100% interna aos 2 mixins.
`WorldServer`'s MRO confirmado sem conflito de diamante (`InstancedMatchMixin`
aparece 1x só, herdado pelos 2 mixins).

**Testes**: 92 testes dedicados (`tests/test_arena.py` + `tests/
test_bg_queue.py`, incluindo cenários que exercitam EXATAMENTE os 3
pedaços extraídos — deadline de aceite vencida com roster incompleto,
W.O., timeout automático de resultado) passaram inalterados antes/depois
— prova de que o comportamento não mudou. Suíte completa: 3 rodadas
consecutivas, 959/959 cada uma, zero falha (nem a flaky).

**Status**: B6 fechado com este escopo. Unificação completa de
accept/propose/leave/fila fica registrada como NÃO desejável (não só
"não feita ainda") — decisão tomada, não pendência.

---

## 18. Throttle do tick de PvP (30Hz → 1Hz) + fila MOBA vira 3 tamanhos fixos (10/08/2026)

**Achado do usuário (throttle):** `_tick_arena_queue`/`_tick_arena_pending`/
`_tick_arena_results_timeout`/`_tick_bg_queue`/`_tick_bg_pending`/
`_tick_bg_results_timeout` rodavam a CADA tick (30x/s, `server/world_server.py::
_tick`) sem necessidade — nenhuma depende de precisão de frame, só comparam
`time.time()` ou tentam parear tokens. Fix: throttle via `self.tick_count %
TICK_RATE == 0` (1x/s, reaproveita `tick_count` já existente — sem contador
novo, critério de estrutura leve em caminho quente, CLAUDE.md). `_tick_bg_respawns`/
`_tick_bg_kda_hud` (timers de respawn, HUD ao vivo) e `_tick_trade_distance_check`/
`_tick_duel_distance_check` deliberadamente NÃO throttled — usuário pediu
escopo amplo ("todas as 6 funções de PvP"), mas essas 4 não faziam parte da
lista original e têm requisito de responsividade mais alto (respawn percebido
pelo jogador, trade/duelo por proximidade). Verificado: os 92+ testes de
Arena/BG chamam essas funções DIRETO (`ws._tick_arena_queue()` etc.), nunca
via `ws._tick(dt)`/`run_ticks` — o throttle não afeta nenhum teste existente.

**Achado do usuário (fila MOBA) + pesquisa (WoW, pedida explicitamente):**
fila ÚNICA sem escolha de tamanho (`_tick_bg_queue` tentava 5x5→1x1 a cada
tick) colapsava sempre pro menor par disponível — 2 solos já fechavam 1v1 no
tick seguinte (33ms), nunca dando chance de uma partida maior se formar.
Pesquisa inicial errou ao generalizar "WoW não tem fila adaptativa" — corrigida
pelo usuário (Escaramuça de Arena É adaptativa, solo pode cair em 2v2 OU 3v3).
Achado final, mais preciso: WoW tem MÚLTIPLAS filas de tamanho FIXO (2v2/3v3
separadas), cada uma com sua própria "waiting room" (20-60s, já existiu
instantânea, mudou pra ter espera) — não é fila única adaptativa nem puramente
fixa-instantânea, é um híbrido. Decisão (usuário: 2v2/3v3/5v5, espelhando os
3 tamanhos que a Arena já usa): fila MOBA vira 3 filas fixas (`BG_MODES`,
mesmo padrão de `ARENA_MODES`) — **sem janela de espera artificial**, porque
uma vez que o tamanho já é escolhido pelo jogador, o problema original
("prioriza partida maior") desaparece por construção; a "waiting room" do WoW
resolve OUTRO problema (balanceamento de papel/MMR dentro do bracket já
fixo) que este projeto não tem ainda (sem sistema de papel/rating) — adicionar
um timer sem ele resolver nada seria complexidade sem propósito.

**Implementação:**
- `server/bg_queue_processor.py`: `BG_MODES` (2v2/3v3/5v5) substitui a fila
  única `_bg_queue`; `_bg_queues: dict[str, list]` (1 fila por modo, mesmo
  padrão de `_arena_queues`). `_bg_try_pack`/`_tick_bg_queue` mantêm o MESMO
  algoritmo guloso de antes (grupo nunca dividido, nunca assimétrico), só
  escopados a 1 tamanho fixo por fila em vez de tentar 5. `request_bg_queue_join`
  ganha `mode_id` (default "5v5"); grupo MENOR que o time do modo é aceito
  (preenchido por outros — mesma flexibilidade de sempre), só maior é recusado.
- `server/world_server.py`/`server/session.py`: `_bg_queue` → `_bg_queues`
  (dict inicializado a partir de `BG_MODES`, mesmo padrão de `_arena_queues`/
  `ARENA_MODES`); `_handle_bg_queue_join` lê `payload.get("mode", "5v5")`,
  mesmo padrão de `_handle_arena_queue_join`.
- `client/bg_queue_handlers.py`: `BG_MODE_LIST`/`BG_MODE_LABELS`/
  `BG_MODE_TEAM_SIZE` (mesmo padrão de `ARENA_MODE_LIST`); `_bg_in_queue`
  (bool) → `_bg_in_queue_mode` (mode_id ou None); `_bg_mode_eligible()` nova
  (mesma checagem client-side de feedback visual que `_arena_mode_eligible`
  já fazia, servidor sempre revalida).
- `client/arena_handlers.py`: modal unificado de fila generalizado —
  `_arena_modal_rects()` agora devolve `bg_rows` (lista, mesmo formato de
  `rows` da Arena) em vez de 1 `bg_row_rect`/`bg_btn_rect` fixo;
  `_draw_arena_queue_modal`/`_handle_arena_modal_click` iteram `bg_rows` com
  o MESMO código que já desenhava/tratava clique das linhas de Arena — 0
  lógica nova de desenho, só generalização do que já existia.
- `ui/ui_sizes.py::ARENA_QUEUE_MODAL_H`: 330→460 (3 linhas de BG em vez de 1
  precisam de mais altura no modal).

**Testes:** `tests/test_bg_queue.py` reescrito por completo (29 dos 33 testes
antigos dependiam do fallback "1v1" que não existe mais) — 40 testes agora,
incluindo cobertura nova (modo inválido, filas de modos diferentes não se
misturam, grupo maior/menor que o time do modo). `tests/test_bg_queue_client_ui.py`/
`tests/test_arena_client_ui.py` atualizados pra nova assinatura de
`_arena_modal_rects()`/payloads com "mode". Suíte completa: 3 rodadas
consecutivas, 967/967 cada, zero falha (nem a flaky).

**Pendente — validação manual em jogo:** a mudança de UI (3 linhas de fila de
BG no modal em vez de 1, mesmo desenho da Arena) não tem cobertura de teste
automatizado pra renderização pygame REAL — só a lógica por trás dela
(estado, cliques, payloads). Testar: abrir o modal (F1 ou "/bgqueue"),
conferir que as 3 linhas de Battleground aparecem sem sobrepor as de Arena,
entrar/sair de cada modo, confirmar que a partida forma corretamente com o
tamanho escolhido.

---

## 19. (movido) ESC não fechava os modais de PvP (10/08/2026) — ver HISTORICO_BUGS.md §1

## 20. Migração C2 — `item_id` estável (identidade de item) (10-11/08/2026)

**Contexto/gatilho:** trabalhando no item A4 do roteiro (inverter
autoridade de persistência de equipamento/inventário — protocolo hoje
manda o estado final calculado pelo cliente, servidor só sanitiza),
o desenho de um EQUIP_SYNC baseado em intenção (slot + item, modelo
`CMSG_AUTOEQUIP_ITEM`/`CMSG_SWAP_ITEM` do AzerothCore) esbarrou num
problema anterior: o projeto não tem identificador estável de item —
`item.name` (string de exibição) é usado como chave em toda parte
(saves, protocolo, empilhamento, catálogos). Já estava catalogado como
débito C2 (🟡, item pequeno "mas pode fazer falta pra outras correções
futuras" — exatamente o caso). Decisão do usuário, explícita: resolver
C2 primeiro, como fundação, em vez de contornar com índice de posição
no inventário.

**Desenho aprovado:** `item_id` = identidade de TEMPLATE (equivalente a
`item_template.entry` do AzerothCore — estável, nunca muda mesmo se
`name` for renomeado), não um GUID por cópia — a posição no inventário
já resolve "qual exemplar exato" (prova: o protocolo de Trade já
funciona hoje com `inv_index`/`offer_slot`, não por nome). Fonte do
`item_id`: as chaves de dicionário que os 4 catálogos de conteúdo já
usavam internamente (`content/item_table.py::ITEMS`,
`content/crafting_data.py::MATERIALS/RECIPES/RECIPE_ITEMS`,
`content/quests_data.py::QUEST_ITEMS`) — só nunca tinham sido
propagadas pro objeto `Item` construído. `name` vira campo 100%
exibição a partir daqui.

**Migração sem script separado:** toda reconstrução (save antigo,
payload de rede) tenta `item_id` PRIMEIRO (O(1)); só cai pro scan
antigo por nome quando `item_id` está AUSENTE do dado (save de antes
desta data) — nunca quando `item_id` está presente mas não bate com
nenhum catálogo (isso é forjado/inválido, cai pro item inerte de
segurança, NUNCA "resgatado" por nome — evita que um item_id forjado
seja mascarado como um item legítimo). Save antigo se autoatualiza pro
formato novo na próxima serialização, sem migração de banco.

**Superfície coberta** (ponto único de verdade OU protocolo tocado, cada
um com fallback pra dado antigo sem `item_id`):
- `engine/components.py::Item` — campo novo.
- Catálogos: `item_table.ITEMS`, `crafting_data.MATERIALS/RECIPES/
  RECIPE_ITEMS` (scroll de receita usa `f"recipe_{id}"` pra não colidir
  com o item craftado, que usa o `id` puro), `quests_data.QUEST_ITEMS`
  (renomeado de chave-por-nome pra chave-por-id).
- Servidor: `_reconstruct_item`/`_build_item_caches`/
  `_lookup_item_value`/`sanitize_inventory_payload`/`process_shop_buy`/
  `process_shop_sell`/`_handle_gm_add_item`/`apply_consumable`
  (`world_server.py`, `session.py`).
- Cliente: `_serialize_item`/`_item_from_data`/`_restore_item`
  (`save_sync_handlers.py`), `_grant_items_to_inventory`/
  `_remove_items_from_inventory`/confirmação de `CONSUMABLE_USE`
  (`network_handlers.py`), ~15 pontos de empilhamento (`ui/systems.py`,
  `ui/crafting_system.py`, `ui/spell_system.py`).
- Protocolo: `BUY_REQUEST`/`SELL_REQUEST` ganharam `item_id` (aditivo,
  `item_name` continua indo junto); `CONSUMABLE_USE` TROCOU
  `item_name`→`item_id` (schema `C2S_REQUIRED` em `shared/messages.py`
  atualizado junto — sem isso todo uso de consumível online seria
  rejeitado como malformado, achado ANTES de ir pra produção).
- Eventos de quest: `collect_item`/`use_consumable`/`equip_item` passam
  `item_id` (não mais `item_name`) — `quest_logic.py::match_objective`
  e `content/quests_data.py`'s `loot_item=`/`target=` seguem o mesmo
  formato agora. `use_item_on_target` deliberadamente NÃO migrado (zero
  chamador real no código, ver nota em C2 acima).
- `quiver.subtype` (aljava) e `ConsumableBar.slots` (barra de
  consumíveis) — os dois guardavam o NOME do item carregado/atalho,
  mesma classe de risco. Migrados pro mesmo padrão, incluindo migração
  automática de config.json antigo (nome→item_id na primeira leitura).

**Achado durante a implementação — regressão real, pega ANTES do
usuário ver** (prova de por que "pesquisar/desenhar antes de codar" +
rodar a suíte importam): renomear as chaves de `QUEST_ITEMS` quebrou em
silêncio o loot de harvestable — `maps/map_1_entities.json` e `maps/
map_cave_west_entities.json` referenciavam itens de quest pelo NOME de
exibição antigo (`"Artefato Misterioso"`, `"Vômito de Zumbi"`, `"Pá"`,
`"Picareta"`, `"Mochila de mineração"`, `"Lampião"`, `"Cantil"`) nos
arrays `"items"` dos harvestables/zonas. Sem os `item_key` desses dados
JSON também migrados, o log `[Harvestable] item_key '...' não existe em
nenhum catálogo — ignorado` apareceria em produção e esses 6
harvestables passariam a não dropar NADA — silêncioso, só detectável
rodando a suíte de verdade (não foi pego por nenhuma análise estática,
só ao rodar `test_faction`/os testes que efetivamente carregam os
mapas). Corrigido nos 2 arquivos JSON pros ids corretos
(`artefato_misterioso`, `vomito_zumbi`, `pa`, `picareta`,
`mochila_mineracao`, `lampiao`, `cantil`).

**Achado nos testes — não é bug de código, é infraestrutura de teste**:
`tests/test_quest_turn_in.py` usa `fake_login()`, que passa por
`server/auth.py` de VERDADE contra `data/game.db` (o banco real de
dev, sem isolamento por execução de suíte — usernames fixos tipo
"qtiuseri" acumulam personagem/inventário entre rodadas). Rodar a suíte
repetidamente durante esta migração deixou um "Presa de Lobo" residual
na conta de teste, quebrando `assertNotIn(item, inv.items)` (o item de
teste só era parcialmente decrementado, porque o item residual — mesmo
`item_id` — era consumido primeiro pelo loop de remoção). Confirmado
via reprodução isolada com conta nunca usada (`complete_quest` remove
certinho, `consumed` bate exato) — não é bug em `complete_quest`. Fix:
os 2 testes afetados agora limpam `inv.items` explicitamente antes de
montar o cenário, em vez de assumir bag vazia. Débito de teste
registrado aqui, não "corrigido" na raiz (exigiria banco de teste
isolado — fora do escopo desta migração).

**Segurança preservada:** `sanitize_inventory_payload` tinha uma
comparação que teria descartado TODO item como "desconhecido" assim
que o cache virou item_id-keyed (`_lookup_item_value(getattr(obj,
"name", ""))` sobrevivendo da era pré-migração) — pego e corrigido na
mesma sessão, antes de qualquer teste rodar contra isso.

**Testes:** suíte completa, 3 rodadas consecutivas, 779/779, zero
falha. Nenhum teste novo dedicado (a migração é estrutural — a
cobertura vem de exercitar os fluxos existentes de inventário/
equipamento/loja/quest/consumível/aljava, todos já testados antes;
provas pontuais adicionais rodadas manualmente durante a implementação
via script descartável — reconstrução por item_id, colisão craft vs
receita, roundtrip de save antigo sem `item_id`).

**Remanescente deliberadamente fora do escopo** (ver nota em C2 acima):
saque de corpo por nome (`loot_processor.py`) e `use_item_on_target`
(feature sem chamador real).

## 21. Migração A4 — equipar/desequipar por intenção, não estado completo (10-11/08/2026)

**Contexto:** primeira fatia vertical do item A4 do roteiro (inverter
autoridade híbrida de persistência de inventário/equipamento) — escopo
reduzido a SÓ equip/unequip, deliberadamente, depois de mapear que a
autoridade de SAVE já estava correta (Fase 0) mas o protocolo AO VIVO
(`EQUIP_SYNC`) ainda mandava o Equipment inteiro recalculado pelo
cliente a cada mudança. Bloqueado até o débito C2 (§20) existir — usar
posição no inventário como identidade já era viável antes (Trade já
prova isso com `inv_index`/`offer_slot`), mas o usuário decidiu resolver
C2 primeiro como fundação ("pode fazer falta pra outras correções
futuras" — exatamente este caso).

**Achado real, não só "usa nome"**: `update_player_equipment` (antigo
handler de `EQUIP_SYNC`) reconstruía cada item do payload via catálogo e
validava classe/level — mas NUNCA cruzava contra o Inventory real do
jogador. Um cliente modificado podia mandar qualquer item do catálogo
com `item_id` válido no payload e o servidor equipava, sem checar posse
nenhuma. Separadamente, equipar/desequipar nunca disparava `INV_SYNC` —
a mochila ao vivo do servidor nunca ficava sabendo que um item saiu dela
pro equipamento (risco de item "fantasma" em ambos os componentes se a
sessão caísse no meio).

**Nuance importante, não escondida do usuário**: o Inventory "ao vivo"
do servidor, mesmo sendo a fonte que este redesenho passa a consultar,
ainda é POPULADO a partir do `INV_SYNC` periódico que o próprio cliente
manda (`sync_player_inventory`, sanitizado — cada item precisa bater
com o catálogo — mas não com um histórico de eventos de loot/compra
reais). Este redesenho fecha o gap específico de "equipar item que
nunca existiu na mochila" e o gap de dessincronia Inventory↔Equipment;
NÃO resolve autoridade de QUANTIDADE de item na mochila em si — isso é
o resto do item A4 (INV_SYNC completo), fora do escopo desta fatia,
registrado aqui como próximo passo natural.

**Desenho (referência AzerothCore `CMSG_AUTOEQUIP_ITEM`, já pesquisado
pra esta decisão específica):** cliente manda só POSIÇÃO
(`EQUIP_ITEM{inv_index}`/`UNEQUIP_ITEM{slot}`), nunca o item. Servidor
lê o item de verdade no seu próprio `Inventory` por posição, valida
classe/level/offhand-travado, e move o item ele mesmo entre `Inventory`
e `Equipment` no mesmo passo atômico — mesma UX já provada em
`client/inventory_handlers.py::_equip_item`/`_unequip_slot` (item
trocado volta pro FIM da mochila, arma de duas mãos desequipa offhand
primeiro), só que agora espelhada no servidor como fonte de verdade.

**Efeito colateral bom**: como o servidor mexe em `Inventory` E
`Equipment` no mesmo passo, o gap de `INV_SYNC` nunca disparado nessas
duas ações fecha sozinho — `server/session.py::_cache_equipment_and_inventory`
atualiza os dois caches de save juntos.

**Simplificação habilitada**: o detector passivo por frame em `game.py`
(comparava snapshot de equipamento a cada frame e remandava o estado
inteiro) existia só pra pegar uma 3ª via de equipar (loot direto de
cadáver) que se confirmou ser código ÓRFÃO do modo offline (loot online
de verdade sempre passa pela mochila via `LOOT_RESULT`, nunca equipa
direto — `ui/systems.py::LootSystem` só faz isso quando
`_online_loot_requester` é `None`). Com as 2 fontes reais de mudança
(`_equip_item`/`_unequip_slot`) já mandando a intenção explicitamente,
o detector virou redundante — removido, junto com `_get_equip_snapshot`/
`_equip_snapshot`.

**Mensagens removidas/adicionadas**: `EQUIP_SYNC` (C→S, estado completo)
saiu — sem cliente antigo em uso, redesenho limpo sem stub de
compatibilidade (mesmo critério já aplicado ao `CONSUMABLE_USE` no C2).
Entraram `EQUIP_ITEM{inv_index}` e `UNEQUIP_ITEM{slot}` (C→S).
`EQUIP_REJECTED` (S→C) continua igual, ganhou só um 3º `reason` possível
("offhand_locked" — defesa em profundidade: cliente legítimo nem chega
a mandar `EQUIP_ITEM` nesse caso, mas um forjado precisa ser recusado
pelo servidor mesmo assim).

**Testes**: `tests/test_equip_item_protocol.py` (novo, 16 testes) —
mover item da bag pro slot; troca com item já equipado volta pro fim da
bag; arma de duas mãos desequipa offhand; recusa por classe/level não
move o item; offhand travado recusa mesmo via chamada direta (sem
depender do guard do cliente); `inv_index` forjado (fora do alcance,
negativo, posição vazia) é ignorado sem exceção — a prova de segurança
central desta migração; desequipar move pro fim da bag, no-op com slot
vazio ou mochila cheia; 4 testes fim-a-fim via protocolo real
(`encode`/`on_message`) confirmando que `EQUIP_REJECTED` só sai quando
deve. Suíte completa: 3 rodadas consecutivas, 795/795 (779 + 16 novos),
zero falha.

**Remanescente deliberadamente fora do escopo**: autoridade de
QUANTIDADE de item na mochila (`INV_SYNC` continua sendo estado
completo confiado do cliente, sanitizado só contra o catálogo) —
próxima fatia natural do item A4, não iniciada.

## 22. (movido) Crash real de playtest — slot `None` do Inventory sem guard em 2 consumidores novos (11/08/2026) — ver HISTORICO_BUGS.md §2

## 23. Discussão de performance — 2 guardrails novos (11/08/2026)

**Contexto**: usuário levantou 4 pontos sobre performance do servidor
(estrutura de dados leve/numpy, granularidade do log, periodicidade dos
sistemas, qualidade dos algoritmos) e anexou um documento de outra IA
(`melhorias-log-performance.md`) com 7 sugestões pro log de performance,
gerado numa conversa paralela onde essa IA só viu uma AMOSTRA de saída
do log (`[PERF]`/`[PERF SRV]`), nunca o código de instrumentação real.

**Achado ao cruzar o documento contra `server/world_server.py` de
verdade**: das 7 sugestões, 2 já estavam implementadas por completo
(#7 — contagem de ticks consecutivos acima do orçamento,
`_perf_overbudget_streak`/`_perf_degraded`; #2 — breakdown do próprio
tick lento por sistema, `_perf_tick_now`/`_PERF_BREAKDOWN_MS`, só que
com limiar de 100ms — ticks entre 33-100ms não ganham o detalhe) e 2
estavam parcialmente erradas sobre ONDE o problema mora: `ai_bundles`
NÃO é caixa-preta (já divide por mapa via `bnd:map_1` E por sistema via
`sys:EnemyAISystem`/`sys:SpawnZoneSystem`/etc — achado real ao ler
`_tick`, linhas ~4327-4357); o que falta de verdade é dividir DENTRO de
cada sistema individual (ex.: pathfinding vs. seleção de alvo dentro do
próprio `EnemyAISystem`). Mapa ocioso vs. ativo (#5) também já é
parcialmente rastreado (`_perf_map_active` conta ticks com jogador).
Genuinamente ausentes: percentil p95/p99 por sistema (#1), diagnóstico
nativo do asyncio pra stalls do event loop (#4,
`loop.slow_callback_duration`/`set_debug`), export Chrome
Trace/Perfetto (#6).

**Por que isso virou guardrail, não só uma correção pontual**: se a
análise externa tivesse sido aceita sem essa verificação, o trabalho
teria reimplementado 2 coisas que já existem e mirado a instrumentação
no lugar errado (`ai_bundles` inteiro em vez de dentro de
`EnemyAISystem`). Mesmo princípio já registrado pra memória própria
("antes de recomendar algo lembrado, verificar se ainda existe") — só
que a fonte aqui era um documento trazido pelo usuário, não a memória
do Claude.

**2º guardrail — observabilidade antes de otimização**: o usuário
perguntou diretamente se as mudanças recentes (C2, A4) estão
considerando substituir listas por numpy, objetos pesados por dict,
etc. Resposta dada: a regra de "estrutura leve em caminho quente" já
existe (`CLAUDE.md`, ligada ao débito §14), mas nunca foi auditada
sistema por sistema, e mudar estrutura de dados/periodicidade/algoritmo
sem primeiro ter um log que aponte a causa raiz específica é otimizar
no escuro — o próprio log de hoje (média de bloco) pode indicar o
sistema errado, como o achado acima sobre `ai_bundles` mostrou na
prática. numpy especificamente: só compensa com volume alto de valores
numéricos homogêneos em operação vetorizada real (ex.: distância de
centenas de mobs de uma vez) — pra componentes ECS pequenos/
heterogêneos (maioria deste projeto) é overhead sem ganho, não
substituto padrão de dict/objeto.

**Sequenciamento (decidido 11/08/2026)**: a Fase 4 (§21) só tem a
fatia de equipar/desequipar fechada — falta a parte de quantidade de
item na mochila (INV_SYNC). Usuário decidiu terminar essa parte
primeiro (contexto já fresco), e SÓ DEPOIS disso a melhoria do log
entra na fila, como **Fase 4.5** — antes da Fase 5 (A1). Roteiro
atualizado em `PROBLEMAS_ARQUITETURA.md` §13. Desenho do log em si
ainda não feito, fica pra quando a Fase 4.5 começar de verdade.

**Sem mudança de código nesta sessão** — só os 2 guardrails no
`CLAUDE.md` (seção "Disciplina de qualidade" e "Performance e
concorrência"). O desenho do log melhorado fica pra depois.

## 24. (movido) Forjar/reciclar/aprender receita — 4 bugs reais, um deles duplicação de ouro ativa (11/08/2026) — ver HISTORICO_BUGS.md §3

## 25. Migração A4 — saque (loot) vira autoritativo no servidor (11/08/2026)

Último pedaço client-authoritative do item A4 (ver §11/§20/§21/§24):
`request_loot()` só tirava o item do dict do corpse e devolvia o dict
cru pro CLIENTE aplicar por conta própria na sua cópia local do
Inventory — o servidor nunca sabia se o item cabia na bag, nunca
empilhava de verdade, e `take="item"` casava por NOME de exibição
(`item_name`), não por identidade estável. Mesmo padrão de risco já
fechado em equipar/craftar/reciclar/consumir — só faltava saque.

**Desenho** (aprovado pelo usuário via AskUserQuestion — "Só o saque
agora, features depois"; 2 pedidos extras do usuário nessa mesma
conversa — trade slice-on-right-click e migração de ícone pra
item_id — foram EXPLICITAMENTE adiados, não fazem parte deste item):

- `WorldServer._grant_loot_items_to_inventory(player_eid, item_dicts)`
  — novo helper (`server/loot_processor.py`), mesmo modelo de
  stack-ou-slot-novo já usado em `craft_item`. Retorna
  `(concedidos, não_concedidos)` — os NÃO concedidos nunca são
  removidos do corpse pelo chamador (decisão do usuário: "se não tem
  espaço na bag simplesmente o item continuaria no corpo até que o
  tempo de permanência do corpo durasse" — sem lógica nova de
  expiração, o timer normal já cobre). Dado inválido (item sem
  `item_id`/`name`, não deveria acontecer — corpse só é populado por
  lógica server-side) também conta como não-concedido, nunca
  descartado em silêncio.
- `request_loot()` reescrito: os 3 ramos de `take` (`gold`/`item`/
  `all`) agora chamam o helper acima em vez de só popar do dict do
  corpse. `take="item"` trocou `item_name` por `item_id` (débito C2 —
  nome de exibição não distingue itens diferentes com nomes iguais).
  `items` no retorno é exatamente o que foi CONCEDIDO — nunca mais um
  "pedido pro cliente aplicar".
- **Achado durante o desenho, não pedido pelo usuário — resposta vazia
  ambígua**: antes da mudança, `items=[]/coins=0` só podia significar
  "outro membro do grupo já pegou". Com "item que não coube fica no
  corpse", esse MESMO retorno vazio passou a também significar "não
  coube na bag" — o cliente mostraria "Já foi saqueado" quando na
  verdade era mochila cheia. Fechado com um campo novo `no_space: bool`
  no retorno interno e `"reason": "inventory_full"` no payload
  `LOOT_RESULT` só quando aplicável; cliente (`_handle_msg_loot_result`)
  checa esse campo antes de escolher a mensagem.
- `server/session.py::_handle_loot_request`: `item_name` → `item_id`
  no payload; `LOOT_UPDATE.item_names_taken` → `item_ids_taken`.
  Progresso de quest `collect_item` (`sync_collect_progress`), que
  antes só rodava como efeito colateral de `_handle_inventory_update`
  (INV_SYNC), se moveu pra DENTRO do próprio handler de loot — o
  cliente não manda mais INV_SYNC depois de sacar (o servidor já é
  quem muta o Inventory), então o gatilho precisa estar onde a mutação
  de verdade acontece, não mais pendurado num pacote separado que
  deixou de ser enviado.
- Cliente (`client/network_handlers.py`): `_handle_msg_loot_result`
  não manda mais INV_SYNC (`_on_loot_action`) depois de creditar item —
  não faz mais falta, o servidor já aplicou tudo antes de responder.
  `_sync_local_corpse_after_take`/`_handle_msg_loot_update` casam por
  `item_id` em vez de nome. `ui/systems.py::_try_send_online_loot_request`
  e `client/save_sync_handlers.py::_send_loot_request` propagam
  `item_id` (era `item_name`) ponta a ponta.
- **Trava de integridade de conteúdo** (pedido do usuário: "nomes
  iguais de itens devem ser evitados, colocar uma trava para que isso
  não possa acontecer"): `WorldServer._check_item_name_collisions()`,
  chamado a partir de `_build_item_caches()` no `__init__` — varre
  `item_table.ITEMS` + `crafting_data.MATERIALS/RECIPES/RECIPE_ITEMS` +
  `quests_data.QUEST_ITEMS` (lojas reusam as MESMAS factories de
  `item_table.ITEMS`, confirmado, não precisam de varredura própria) e
  RECUSA subir o servidor (`RuntimeError`, depois de logar
  `log.error` com o nome e os item_ids em conflito) se dois item_ids
  diferentes compartilharem o mesmo nome de exibição. Severidade
  (hard-fail vs. só log) foi decisão explícita do usuário via
  AskUserQuestion — confirmado antes de implementar que o catálogo
  ATUAL não tem nenhuma colisão (160 nomes, todos únicos), então o
  hard-fail é seguro de ligar já.
- Confirmado (sem mudança de código necessária): `collect_item`/
  `loot_item` em `engine/quest_logic.py` já casam por `item_id` desde a
  migração C2 (§20) — item 6 do desenho original era só checagem.

**Testes**: `tests/test_loot_authoritative.py` (novo, 12 testes) —
empilhamento automático em stack existente; item não-empilhável ocupa
slot novo; `take="item"` casa por item_id; dado inválido não some do
corpse; item sem espaço fica no corpse (`take="all"` e `take="item"`);
item já pego por outro NÃO conta como `no_space`; empilhar em slot já
existente funciona mesmo com bag "cheia" de outros itens; progresso de
quest `collect_item` atualiza direto no fluxo de LOOT_REQUEST sem
INV_SYNC; item alheio à quest não dispara QUEST_UPDATE à toa; catálogo
real não tem colisão de nome; colisão sintética levanta `RuntimeError`.
Testes pré-existentes ajustados pra usar item_id/dicts de catálogo
real em vez de dicts fake sem item_id (`tests/test_party.py`,
`tests/test_session.py`, `tests/test_client_ui.py`) — a maioria
continuou passando sem mudança por causa do fallback "item inerte" já
existente em `_reconstruct_item` (item_id/nome desconhecido vira Item
mecanicamente inerte em vez de None), só os pontos que exercitavam
`take="item"` por nome ou comparavam nomes específicos precisaram de
ajuste real.

**Achado à parte, na rodada de suíte completa** (não é bug do saque —
débito de teste deixado pela migração C2 anterior nesta mesma sessão,
nunca pego porque a suíte completa não tinha rodado desde então): 3
testes em `tests/test_quest_logic.py` falhavam porque `_make_try_start_
world`/as QUESTS de teste ainda usavam nome de exibição
("Relíquia de Teste"/"Pelo de Urso") onde `try_start`/
`resolve_reward_item_factory` já esperam `item_id` desde §20. Conteúdo
de produção já usa item_id de verdade (`loot_item="pelo_urso"`,
confirmado) — só os fixtures de teste ficaram presos no padrão antigo.
Corrigido junto (mesma classe de ajuste mecânico já feito nos outros
arquivos de teste desta sessão), suíte completa voltou a 1030/1030.

## 26. (movido) Fatiar quantidade no trade + ícone por item_id (11/08/2026) — ver HISTORICO_FEATURES.md §1

## 27. Suíte de testes ~3x mais rápida — cache de parse de mapa (11/08/2026)

Usuário perguntou se dava pra acelerar a suíte de testes (não é
performance de JOGO — é tempo de rodar `pytest`). Respeitando a mesma
regra já em vigor pra performance do servidor ("observabilidade antes
de otimização", §23): medi ANTES de propor qualquer coisa.

**Medição 1 — `pytest --durations=25`**: os 25 testes mais lentos
somavam só ~50s dos ~296s totais (~17%) — sinal de que o tempo estava
espalhado nos outros ~1000 testes, não concentrado em poucos vilões.
Descartou a hipótese óbvia ("otimizar o teste mais lento") e apontou
pra custo FIXO repetido por teste.

**Medição 2 — `cProfile` em `WorldServer()`**: construção levava **396ms
em média**, e **84% disso** (330ms) era só `_load_all_maps` →
`_load_map_for` → `load_map_csv` → `_load_two_layer` →
`_parse_terrain_cell`, chamada **1.9 MILHÃO de vezes** em 30 cargas de
mapa (10 construções × 3 mapas cada — map_1/cave_west/cave_east, os
mesmos sempre). Confirmado que minha própria mudança do dia
(`_check_item_name_collisions`) NÃO era o problema: 0,78ms, irrelevante.

**Causa raiz**: `engine/map_loader.py::load_map_csv` reparseava os
MESMOS 3 arquivos CSV do zero em TODA construção de `WorldServer` —
conteúdo 100% estático em runtime (mapa não muda durante a vida do
processo), mas sem nenhum cache. Como quase todo teste chama
`make_world_server()` no `setUp`, isso rodava ~700-1000 vezes por
rodada de suíte.

**Fix**: `_MAP_CSV_CACHE` (dict de módulo, chave = filepath resolvido)
em `engine/map_loader.py`. Ponto de atenção resolvido explicitamente
(risco real, não hipotético): `spawn_points`/`object_matrix` retornados
por `load_map_csv` são MUTADOS depois por quem chama (`_create_
harvestables_for_map` e outros usam essas estruturas como insumo,
harvestable reabastecendo muta estado derivado) — cachear e devolver o
MESMO objeto pra múltiplas instâncias de `WorldServer` vazaria mutação
de uma instância pra outra (2 testes diferentes, ou 2 mapas
concorrentes em produção, compartilhando estado que deveriam ser
independentes). Fix real: `load_map_csv` SEMPRE devolve uma CÓPIA
independente (`_copy_matrix` pras matrizes, `copy.deepcopy` pro dict de
spawn_points, pequeno) — cache hit pula só o PARSE caro (1,9M chamadas
de `_parse_terrain_cell`), nunca compartilha o objeto mutável.

**Resultado medido**: `WorldServer()` 396ms → 51ms (~7.7x). Suíte
completa: 296s → 105s (~2.8x) — 1058 testes (suíte já tinha crescido
com os itens anteriores desta sessão), zero mudança de comportamento
de gameplay (só evita reprocessar arquivo que não muda). Sem efeito
prático em produção (servidor real só chama `_load_all_maps` 1x por
processo) — o ganho é inteiramente de velocidade de desenvolvimento/CI.

**Testes**: `tests/test_map_loader_cache.py` (novo, 6 testes) — duas
chamadas retornam dados equivalentes mas NUNCA o mesmo objeto; mutar
`object_matrix`/`spawn_points` de uma chamada não vaza pra próxima;
cache populado após 1ª chamada; 2 instâncias de `WorldServer`
construídas em sequência não compartilham estado mutável de corpse/
harvestable. Suíte completa: 3 rodadas consecutivas, 1058/1058, zero
falha, ~100s por rodada.

## 28. Fase 4.5 — melhoria do log de performance (11-12/08/2026)

Fase encaixada no roteiro desde §23 (11/08/2026) — só a ANÁLISE tinha
sido feita antes (4 sugestões de 7 já implementadas ou parcialmente
erradas sobre onde o problema mora, ver §23), o desenho de verdade
ficou pra quando a fase começasse. Antes de desenhar, reverifiquei o
código atual em vez de confiar na análise antiga (mesma régua já usada
nesta sessão pra pesquisa de terceiro — aqui a "fonte antiga" era a
MINHA PRÓPRIA análise de um dia atrás): números de linha mudaram (~300
linhas pra baixo, arquivo cresceu com o resto da sessão), mas o
conteúdo describe continuava batendo.

Dos 4 pontos genuinamente ausentes catalogados em §23, 3 foram
desenhados e implementados nesta fase (decisões via AskUserQuestion,
todas as 3 recomendadas aceitas); o 4º (breakdown DENTRO de um único
sistema, ex. `EnemyAISystem` — pré-filtro vs seleção de alvo vs
pathfinding vs ataque) foi adiado por trade-off real de custo: os 2
pontos fáceis (`_select_target`/`_find_path_budgeted`) já são métodos
próprios, baratos de envolver; o resto (resolução de ataque, árvore de
decisão de movimento) está inline num loop por-mob com pelo menos 8
`continue`s — extrair pra função ou colocar profiling ali dentro exige
refatoração maior, e o CUSTO de profiling por-mob multiplicaria as
chamadas de `perf_counter()` por dezenas de mobs ativos/tick (hoje é
por sistema-por-bundle, não por mob) — não decidido nesta sessão,
fica registrado pra quando/se o passo 4 for pedido.

### 1. Percentil p95/p99 por rótulo

`_perf_accum[label]` só guardava SOMA — o relatório periódico (a cada
`_PERF_REPORT_TICKS`=300 ticks) só conseguia calcular MÉDIA, que
esconde exatamente o tipo de pico isolado que motivou essa
instrumentação inteira. Fix: `_perf_samples: dict[str, list[float]]`
novo, gravado por `_perf_mark` (chokepoint único, junto com
`_perf_accum`/`_perf_tick_now`) e por `TOTAL` (que não passava por
`_perf_mark`, escrita direta). Resetado junto com `_perf_accum` a cada
janela de relatório — 300 ticks × ~20 rótulos de float é memória
insignificante. Relatório agora imprime `avg=`/`p95=`/`p99=` por linha.

### 2. Breakdown "TOP:" sem corte em 100ms

Achado real durante o desenho: o dado do breakdown (`_perf_tick_now`)
já era calculado de graça pra QUALQUER tick acima do budget (33ms) —
só a IMPRESSÃO tinha um corte em `_PERF_BREAKDOWN_MS`=100ms (decisão
de 04/08/2026, pra evitar log poluído com "tick um pouco lento").
Usuário decidiu (via AskUserQuestion) reverter esse corte — mais
detalhe disponível pro pico "ainda não confirmado por profiling" que o
próprio CLAUDE.md já cita como débito aberto na seção de performance.
`_PERF_BREAKDOWN_MS` removido (atributo morto depois da mudança).

### 3. Diagnóstico nativo do asyncio

`loop.slow_callback_duration = 0.05` (50ms) ligado logo no início da
coroutine `main()` (`server/main.py`) — `asyncio.run()` não dá hook
pra configurar o loop ANTES dele rodar, por isso fica dentro da
coroutine, não em `__main__`. Sinal INDEPENDENTE do profiler próprio:
`_perf_mark` só mede o que está explicitamente instrumentado dentro de
`_tick()`; isso aqui pega qualquer callback do event loop (inclusive
processamento de mensagem de rede/I/O fora de `_tick`) que trave por
mais de 50ms. `set_debug(True)` completo ficou de FORA de propósito —
adiciona overhead a TODO callback (rastreamento de origem etc.),
arriscado com o orçamento de 33ms já apertado; só o sinalizador leve.

Esses avisos vão pro logger `asyncio` nativo do Python, que não tinha
NENHUM handler plugado (ficava mudo). `server/log.py` agora pluga o
MESMO `RotatingFileHandler` (2MB × 3 backups) já usado pelo log geral
do servidor — decisão do usuário via AskUserQuestion (vs. jogar no
arquivo de perf, que não tem rotação).

### 4. Trace sob demanda (Chrome Trace/Perfetto)

`WorldServer._dump_perf_trace()`, chamado só quando um tick passa de
`_PERF_TRACE_DUMP_MS`=300ms (bem acima do budget — só ticks
genuinamente ruins, não todo tick "um pouco lento" que já sai com
TOP:). Gera `logs/perf_trace_tickN.json` no formato Chrome Trace
(`{"traceEvents": [...]}`, abre em `chrome://tracing` ou
ui.perfetto.dev) — decisão do usuário via AskUserQuestion: sob demanda,
não contínuo (gravar todo tick geraria arquivo gigante rodando por
horas sem necessidade real). Limitação assumida conscientemente: não
existe timestamp real de INÍCIO por seção hoje (só duração acumulada
por rótulo dentro do tick) — cada evento sai com `ts=0` relativo ao
tick, em `tid` própria por rótulo, funcionando como um "breakdown lado
a lado" (cada seção sua própria linha no viewer) em vez de uma
timeline cronológica exata dentro do tick. Mantém só os 5 dumps mais
recentes (apaga os mais antigos a cada novo dump) — nunca cresce sem
limite mesmo com o servidor rodando dias.

**Testes**: `tests/test_perf_log_improvements.py` (novo, 8 testes) —
`_perf_mark` grava amostra; relatório periódico imprime p95/p99
corretos pra uma amostra conhecida (fórmula validada numericamente);
`_PERF_BREAKDOWN_MS` não existe mais; "TOP:" aparece mesmo pra tick só
um pouco acima de um budget artificialmente baixo (prova que o corte
sumiu de verdade, não só que o atributo foi removido); tick muito
acima do threshold dispara dump de trace válido (JSON com
`traceEvents`); tick normal NÃO dispara dump; mantém só 5 dumps mais
recentes; logger `asyncio` tem handler plugado. Ponto 3
(`slow_callback_duration` em si) não tem teste automatizado —
verificar exigiria subir o servidor real (bind de socket + WorldServer
completo) dentro da suíte, risco/custo desproporcional pra uma
atribuição de 1 linha; verificado por leitura de código + `py_compile`.
Suíte completa: 3 rodadas consecutivas, zero falha (ver rodada
registrada logo após esta seção ser escrita).

### 5. Achado real via log de produção — pré-filtro domina o custo de `EnemyAISystem`

Usuário rodou o servidor de verdade após a Fase 4.5 e trouxe o log
real (`logs/server_perf.log`) pra discutir — não era mais só teoria.
Achado ao ler o log: `sys:EnemyAISystem` custava consistentemente
~2ms/tick (≈31% do tick total) mesmo com só 1-5 mobs "ativos"
(perseguindo/atacando) de uma população de **191 mobs no mapa**, e o
custo NÃO caía significativamente quando `mobs_ativos` estava perto de
zero — sinal de que o custo vem de VARRER todo mundo pra decidir quem
dorme, não da IA de quem já está engajado.

**Esclarecimento pro usuário sobre a leitura do log** (confusão real
relatada): `ai_bundles`/`bnd:map_1`/`sys:EnemyAISystem` não são uma
árvore visual — é uma lista plana com 3 "zooms" do MESMO tempo:
`ai_bundles` = soma de todos os mapas; `bnd:map_1` = soma de todos os
sistemas DE UM mapa; `sys:EnemyAISystem` = soma de UM sistema em TODOS
os mapas. Com só 1 mapa populado (caso do log trazido), os 3 números
colapsam quase no mesmo valor — parece redundante, mas é porque os
outros 2 mapas estão em ~0 (não é bug, é o cenário real testado).

**Medição cirúrgica** (não a refatoração grande original — mais barata
e testa a hipótese específica direto): `EnemyAISystem` ganhou um
callback `perf_mark` opcional injetado por `WorldServer._load_map_for`
(mesmo chokepoint único de sempre) — mede `sys:EnemyAISystem:prefiltro`
(a chamada a `_active_mobs_this_tick`, 1×/tick, barato) separado de
`sys:EnemyAISystem:loop_mobs` (o resto do corpo do loop por-mob).
`perf_mark=None` (default) = zero overhead, comportamento idêntico —
usado por qualquer construção de `EnemyAISystem` fora do servidor
online (se houver).

**Confirmado em cenário de teste real** (1 player, mobs reais
spawnados via `SpawnZoneSystem`, 50 ticks): pré-filtro = 54,3% do
sistema, loop_mobs = 12,2% — hipótese bate. Os ~33% restantes (setup
no topo de `update()`: caches de combatente, tiles ocupados,
resolução de players do mapa) ainda não têm rótulo próprio — decisão
consciente de não medir agora (usuário: "documentar o achado e
parar" — otimizar/medir mais fundo é trabalho novo, não parte da Fase
4.5, que era sobre o LOG em si, não sobre mudar o algoritmo).

**Não decidido nesta sessão**: se/quando otimizar o pré-filtro de
verdade (ex: estrutura espacial em vez de comparar cada mob contra a
lista de players a cada tick) — fica registrado aqui como candidato
real, com dado de profiling já em mãos, pra quando o usuário decidir
abrir essa frente.

**Testes**: `TestEnemyAISystemPrefiltroBreakdown` em
`tests/test_perf_log_improvements.py` (3 testes) — `EnemyAISystem`
recebe `perf_mark` injetado pelo bundle real; tick real com mob gera
os 2 rótulos novos, com a soma das partes nunca passando do total
medido por fora (nenhum tempo "inventado"); `EnemyAISystem` construído
SEM `perf_mark` (`None`) não quebra nem gera rótulo novo.

## 29. Fase 4.6 — escala de servidor: pré-filtro de IA vira índice espacial (12/08/2026)

Sequência real que levou até aqui: usuário trouxe um log de produção
pra discutir o item 5 adiado do §28 (breakdown dentro de
`EnemyAISystem`); a análise do log revelou que o pré-filtro
(`_active_mobs_this_tick`) domina o custo do sistema mesmo com poucos
mobs "ativos" — confirmado com medição cirúrgica (54% do sistema, num
cenário de teste com 1 player). Ao discutir o que fazer com esse
achado, o usuário pediu explicitamente pra **pensar na escala real de
lançamento**, não na escala de teste atual (1-4 players) — decisão que
reabriu e reverteu uma escolha já registrada.

### Decisão revertida: grade espacial pro pré-filtro de IA (05/08/2026 → 12/08/2026)

`arquitetura/historico/ARQUITETURA ONLINE HISTORICO.md` §34.74.42
(06/08/2026) já tinha pesquisado e **descartado** uma grade espacial
pra este problema exato — motivo registrado: medido contra ~10 players
por mapa, comparação linear já era "barata". Essa premissa não é mais
a que importa: o usuário confirmou que a mira é escala de lançamento
público (`VISAO_PRODUTO.md`, "índice espacial" já citado lá como algo a
pesar mais que "só grupo fechado de testers"). A decisão de 06/08
estava CORRETA pro que media — não é um erro antigo, é uma decisão que
precisou ser revisitada quando a premissa mudou. Confirmado com o
usuário explicitamente antes de reverter (nunca decidir sozinho sobre
decisão já registrada).

### Pesquisa de referência (AzerothCore + Veloren, pedida explicitamente)

- **AzerothCore** (mesma linhagem de conteúdo/sistema já usada como
  referência no projeto): resolve isso com um sistema de **Grid/Cell**
  fundamentalmente diferente do nosso modelo — é PUSH-BASED, não
  "varre tudo e filtra barato". `Map::Update` nunca itera todas as
  criaturas do mapa; itera só jogadores online + uma lista pequena de
  NPCs explicitamente marcados ativos, e visita só as células de grid
  DENTRO do raio de ativação de cada um. Uma célula sem jogador por
  perto e sem nada marcado ativo **nunca é visitada** — a criatura
  dentro dela recebe ZERO processamento, nem uma checagem barata.
  Estruturalmente mais forte que o nosso pré-filtro antigo, que ainda
  tocava CADA mob todo tick pra decidir que ele devia ser ignorado.
- **Veloren**: `SpatialGrid` genérico (dupla resolução — grid fino +
  grid grosso pra entidades de raio grande), reconstruído 1x/tick como
  recurso ECS e reusado por vários sistemas leitores — mesmo padrão
  "constrói 1x, muitos leem" que o projeto já segue. `rtsim` (NPCs fora
  de área carregada) usa throttle por distância (full-rate perto,
  1/30 ticks longe) — mesmo espírito do `_THROTTLE_INTERVAL_BY_TIER`
  que o projeto já tinha.
- **`SessionManager._sessions_in_aoi`** (o AOI de rede do próprio
  projeto, citado no CLAUDE.md como ponto único de verdade): investigado
  como candidato a reaproveitar — na real é uma VARREDURA LINEAR sobre
  todas as sessões, sem índice espacial nenhum hoje. Nada pronto pra
  reaproveitar ali; mas confirma que o padrão certo (`SpatialHash` por
  tick, dict por mapa) já está estabelecido 2× no projeto —
  `server/session.py::_mob_hash` e `server/world_server.py::
  _combat_spatial_hash`, ambos com comentário "nunca reimplementar, só
  reaproveitar". Este trabalho é a 3ª aplicação do mesmo padrão.

### Desenho e implementação

`EnemyAISystem._active_mobs_this_tick` (`engine/world_systems.py`):
mobs IDLE agora entram num `SpatialHash` (célula = `SLEEP_RADIUS_TILES`)
em vez de cada um comparar contra cada player. Cada player faz 1
consulta (`nearby()`, superconjunto por célula) — candidatos são
confirmados por distância exata **dentro do loop de cada player**
(nunca contra a lista de players inteira por candidato — essa é a
estrutura que evita reintroduzir o O(mobs×players) original; um mob já
confirmado por um player pula o resto). Fallback de NPC/combatente
(`_any_candidate_in_range`) preservado sem mudança, mesmo escopo
"só o pré-filtro" já combinado.

**Bug real cometido e corrigido durante a implementação**: a primeira
versão confirmava distância exata comparando cada CANDIDATO contra
TODOS os players (não só contra os players cuja consulta o trouxe à
tona) — reintroduzia o mesmo O(candidatos×players) que a mudança
inteira existia pra eliminar. Medido, achado, corrigido antes de seguir
(ver números abaixo — a 1ª versão mal empatava com o algoritmo antigo).

### Medição de carga real (script fora da suíte, escala não coberta por nenhum teste antes)

Nenhum teste existente simulava 2+ players concorrentes num mapa aberto
com população cheia de mob — medição nova, necessária pra provar o
ganho na escala que importa (não só no cenário de 1 player já coberto).

**Players espalhados pelo mapa** (cenário do PvE aberto, a prioridade
declarada do jogo):
| Players | Antes (linear) | Depois (spatial hash) | Ganho |
|---|---|---|---|
| 1  | 0.98ms/tick | 1.02ms/tick | ~igual (esperado — overhead do índice não compensa com poucos players) |
| 4  | 1.26ms/tick | 1.23ms/tick | ~igual |
| 20 | 2.78ms/tick | 2.41ms/tick | 13% |
| 60 | 5.55ms/tick | 3.11ms/tick | **44%** |

Curva claramente mais achatada — crescimento de 1→60 players caiu de
5.66x (linear) pra 3.05x (spatial hash).

**Limitação real, medida e aceita conscientemente**: players
AGLOMERADOS num mesmo hotspot (ex: cidade lotada) não se beneficiam —
testado com 60 e 200 players concentrados em 3 pontos fixos, resultado
ficou **praticamente igual ao que o algoritmo antigo custaria** (~15ms
com 200 players aglomerados). Causa: cada player ainda faz sua própria
consulta ao índice mesmo quando várias consultas são quase idênticas
(players vizinhos) — precisaria de um passo extra (agrupar players por
célula antes de consultar, 1 consulta por grupo) pra fechar esse caso.
**Decisão do usuário (12/08/2026): fecha com o ganho atual (caso comum
de mundo aberto), hotspot fica documentado como débito conhecido pra
quando virar problema real** — mesma disciplina de fatia pequena já
usada no resto desta sessão.

### Testes

`tests/test_ai_prefilter_spatial_index.py` (novo, 4 testes) — prova
DIFERENCIAL: resultado do algoritmo novo (spatial hash) precisa ser
IDÊNTICO ao algoritmo linear antigo (reimplementado só como referência
de teste) pra uma configuração de 3 players espalhados + população real
de mob; mob perto de só 1 player entre vários ainda é elegível (união
correta); mob fora do raio de todo mundo E de todo NPC não é elegível;
regressão explícita do fallback de NPC/combatente (mob longe de todo
player mas perto de NPC continua elegível — mesmo caso que
`tests/test_faction.py::TestMultiTargetCombat` já cobria, preservado
sem mudança). 2 bugs de TESTE (não do algoritmo) encontrados e
corrigidos ao escrever esses testes: throttle por tier não resetado
entre `run_ticks` do setUp e a chamada isolada (mascarava resultado
geométrico com `tick_count` inconsistente); mover só `TileMovement` sem
sincronizar `Position` em pixels (usado por `_any_candidate_in_range`,
que ficava "vendo" a posição antiga). Nenhum dos dois era bug de
produção — ambos específicos do arranjo do teste.

**3º achado, na 1ª rodada da suíte completa** (não é bug do algoritmo
nem de teste novo — colateral num teste PRÉ-EXISTENTE):
`tests/test_idle_map_perf.py::TestCombatSpatialHashScopedToBgMaps::
test_mapa_aberto_sem_lane_nao_entra_na_hash` falhava nas 3 rodadas,
consistente (não era flaky). Causa: esse teste monkey-patcheia
`SpatialHash.insert` **na CLASSE inteira**, não numa instância — conta
QUALQUER uso de `SpatialHash` no processo, não só o
`_combat_spatial_hash` de Battleground que o teste queria isolar. O
pré-filtro de IA agora também usa `SpatialHash` (célula=20) em mapa
aberto — uso legítimo, mas o monkey-patch cego contava as duas coisas
juntas, quebrando a suposição original ("mapa aberto sem lane nunca
insere na hash"). Fix: o teste passou a filtrar por `cell_size==9`
(constante real da combat_spatial_hash, `server/world_server.py`) antes
de contar — volta a testar só o que sempre pretendeu, sem exigir que
`SpatialHash` seja de uso exclusivo do combate.

**Testes**: `tests/test_ai_prefilter_spatial_index.py` (novo, 4 testes,
detalhado acima) + `tests/test_idle_map_perf.py` corrigido (discriminador
por `cell_size`, ambos os testes da classe passando de novo). Suíte
completa: 3 rodadas consecutivas, zero falha (ver rodada registrada
logo após esta seção).

### 2ª camada, mesmo dia — throttle da varredura completa (playtest real do usuário)

Usuário testou o servidor de verdade depois da 1ª camada e trouxe um
log real pra discutir: `sys:EnemyAISystem:prefiltro` continuava em
~1,1-1,3ms mesmo com **1 player só** — a 1ª camada (índice espacial)
resolve o crescimento por NÚMERO DE PLAYERS, mas não reduz o "piso" de
tocar em ~165-190 mobs TODO tick só pra saber que a maioria continua
dormindo. Achado real ao analisar o log junto com o usuário: `ai_bundles`/
`bnd:map_1`/`sys:EnemyAISystem` aparecendo com % alto no relatório NÃO
significa problema — o tick inteiro ficava em 6-9ms de um orçamento de
33ms (bem confortável); é só o maior pedaço de um número já pequeno.
2 picos reais acima do budget foram vistos no log (66ms, 51,6ms) mas
sem repetição suficiente pra investigar sem chutar.

**Opção descartada, com risco explicado ao usuário**: índice espacial
INCREMENTAL (só atualiza a posição de um mob quando ele anda de
verdade, nunca reconstrói do zero) — pesquisado a fundo: precisaria de
**5 pontos de gancho diferentes** (conclusão de movimento normal em
`TileMovementSystem`, `utils.snap_to_tile()` — código COMPARTILHADO
cliente/servidor nos dois casos —, spawn em `SpawnZoneSystem`/
`MobRespawnSystem`, despawn em `ServerDeathHandler`), com risco real de
ficar desatualizado em SILÊNCIO se um gancho faltasse (mob "invisível"
pra IA até andar de novo — bug caro de notar/depurar). Usuário escolheu
a alternativa mais simples depois de entender o risco.

**Fix escolhido**: `_active_mobs_this_tick` cacheia o conjunto INTEIRO
de mobs elegíveis (via player OU via NPC/combatente — não só a parte
de player) e só refaz a varredura completa a cada
`_PREFILTER_REFRESH_TICKS`=3 ticks (100ms a 30 ticks/s, mesmo atraso já
aprovado como imperceptível). Achado real DURANTE a implementação: a
1ª versão só throttlava a construção do índice espacial, mas
`_any_candidate_in_range` (fallback de NPC) continuava rodando SEM
throttle, chamado pra cada mob idle não confirmado via player — esse
era o verdadeiro custo que sobrava. Fix corrigido: o conjunto
elegível INTEIRO (player + NPC juntos) entra no cache, ticks
intermediários não chamam `_any_candidate_in_range` nenhuma vez.

**2º achado real durante os testes** (não da produção — do arranjo dos
testes): o cache usa `tick_count` pra decidir se está velho, e vários
testes PRÉ-EXISTENTES chamam `run_ticks()` (avança `tick_count` de
verdade) e DEPOIS chamam `_active_mobs_this_tick` direto com um
`tick_count` menor/fixo (ex: 0) — o cache via isso como "nada mudou,
tick quase igual" e reusava um resultado calculado numa configuração de
mundo completamente diferente, quebrando 6 testes. Nunca aconteceria em
produção real (`WorldServer._tick()` só incrementa `tick_count`), mas
o cache precisa ser defensivo mesmo assim — fix: `tick_count` MENOR que
o último refresh força reconstrução sempre, nunca reusa cegamente.

**Resultado medido** (mesmo script de carga da 1ª camada):
| Players | 1ª camada (só índice) | 2ª camada (+ throttle) | Redução |
|---|---|---|---|
| 1  | 1.19ms/tick | 0.38ms/tick | 68% |
| 4  | 1.35ms/tick | 0.43ms/tick | 68% |
| 20 | 2.40ms/tick | 0.70ms/tick | 71% |

Tick inteiro (todos os sistemas): 1 player 6,54→4,73ms; 20 players
8,56→5,83ms.

**Testes**: `TestPrefilterRefreshThrottle` em
`tests/test_ai_prefilter_spatial_index.py` (novo, 4 testes) — cache
populado na 1ª chamada; mob que fica elegível só é detectado no
refresh seguinte (antes do intervalo completar, continua reusando o
cache antigo; no tick exato em que completa, já aparece); dentro do
intervalo NÃO reconstrói o índice espacial (prova via contagem real de
chamadas a `SpatialHash.insert`, não só resultado); `tick_count`
retrocedendo força reconstrução (nunca reusa cache não-confiável).

## 30. Fase 4.7 — log de performance vira hierarquia real (12/08/2026)

Usuário testou com 2 players após a Fase 4.6 (§29): latência não
multiplicou (1ª camada validada), mas "gerou muitos picos, e eu não
consigo identificar olhando no log qual é o problema". Junto, uma
crítica estrutural ao formato do próprio log: *"tem dentro do
bnd:map_1 coisas que estão nas outras linhas e isso acaba confundindo,
pra mim não importa os resumos, queria ver a raiz do consumo"* — e
`chrome://tracing` (feature da Fase 4.5, §28) "ainda não consegui
usar". Usuário pediu explicitamente discussão + pesquisa de referência
ANTES de qualquer código (regra "pesquisar antes de codar", CLAUDE.md).

**Achado 1 — pico real isolado**: tick#11503, 403.0ms total (budget
33ms), `aoi_collect=394.0ms` (98% do tick) sozinho. Código por trás
(`_collect_deltas`) não tinha nada algoritmicamente capaz de explicar
394ms com 1 player só — hipótese ambiental (GC/SO), não confirmada.
Nas 71 linhas "tick lento" do log inteiro: nenhum sistema único se
repete como pior ofensor — rotaciona entre `ai_bundles`,
`post_tick_bookkeeping`, `aoi_collect`, `combat_spatial_hash_build`,
etc. Consistente com picos isolados de causas variadas, não 1 bug.

**Achado 2 — cluster, não isolado**: ao reexaminar, tick#11503 tinha 2
vizinhos também lentos (#11507, #11508), cada um com um sistema
"culpado" DIFERENTE. Se fosse 1 algoritmo lento, o mesmo sistema
apareceria toda vez — não apareceu. Padrão consistente com uma pausa
EXTERNA ao código (GC automático fora do ciclo forçado, paginação,
scheduler do SO) afetando o que estava rodando no momento, não com um
algoritmo nosso. `gc.disable()` roda em `server/main.py` — se
`gc.get_count()` mudar mesmo assim num pico futuro, prova coleta
automática apesar do disable nominal; se não mudar, aponta pra fora do
processo. Instrumentado (ver "Fix" abaixo), ainda não confirmado.

**Achado 3 — defeito estrutural raiz, motivou o desenho**: `_dump_perf_trace`
(Fase 4.5) tecnicamente funcionava (disparou certo pro pico de 403ms,
JSON válido), mas todo evento nascia com `ts:0` e um `tid` ÚNICO por
rótulo — o viewer desenhava barras PARALELAS soltas, sem aninhamento
nenhum. É o MESMO defeito do log em texto (lista flat misturando soma
de `ai_bundles`/soma de `bnd:map_1`/soma de `sys:EnemyAISystem` como
linhas irmãs, sem indicar containment), só que no JSON também.

**Achado 4 — chrome://tracing está sendo descontinuado** pelo próprio
Google, substituído por Perfetto UI (`ui.perfetto.dev`, lê o mesmo
JSON legado) — explica por que o usuário "não conseguiu usar".

**Pesquisa de referência** (pedida explicitamente, AzerothCore/Veloren):
- **AzerothCore/TrinityCore**: sem profiler hierárquico documentado —
  tudo contador flat/série temporal (`sWorldUpdateTime`, métricas
  Grafana por sistema independente). Meta publicada no
  `worldserver.conf.dist`: diff < 300ms = bom, > 600ms = ruim — mas é
  loop de passo VARIÁVEL, não comparável direto ao nosso budget fixo
  de 33ms/tick.
- **Veloren**: usa o MESMO modelo nosso — 30 ticks/s, 33ms de budget,
  documentado assim no manual deles. Devblog real relata pico médio de
  ~280ms (8.5x o budget) com 53 players, tratado como incidente digno
  de post-mortem. O breakdown que ELES publicaram nesse post também
  foi uma lista flat ranqueada — ou seja, lista flat não é anti-padrão
  em si, até referências sérias usam pra visão geral rápida. Só que
  por baixo Veloren tem **Tracy** (spans genuinamente aninhados via
  RAII) pra quando a lista flat aponta "algo errado" mas não explica
  por quê.
- **Padrão geral da indústria** (Perfetto, flame graphs, spark do
  Minecraft, Unreal/Unity): quando a pergunta é "qual a CAUSA raiz",
  toda ferramenta séria usa aninhamento ESTRUTURAL (containment de
  timestamp determina pai/filho automaticamente), nunca convenção de
  nome/prefixo — é exatamente o que faltava aqui.
- Nenhuma das duas referências publica meta de "N players/mobs a
  Xms" — não existe número citável em nenhuma delas. Nosso budget
  (33.3ms) já é idêntico ao do Veloren — âncora legítima de
  comparação.

**Desenho aprovado com o usuário** (discussão prévia, sem código até
aprovação explícita — "Sim" final): trocar `_perf_mark(label, t0)`
(dict FLAT por nome — `_perf_accum`/`_perf_tick_now`/`_perf_samples`)
por `_perf_push(label)`/`_perf_pop()` — pilha real (`_perf_stack`)
chaveando tudo pelo CAMINHO COMPLETO (tupla de rótulos da raiz até a
folha, não o nome solto). A estrutura de aninhamento já existia
IMPLICITAMENTE no código (`_t0p` envolvendo `_t0bnd` envolvendo
`_t0sys` no loop de bundles) — só faltava capturar em vez de
descartar. Corrige de brinde outro bug: `sys:EnemyAISystem` de mapas
diferentes não fica mais somado num único número (cada mapa vira nó
próprio da árvore, chaveado por caminho).

As 3 saídas passam a vir da MESMA árvore:
- **Relatório periódico** (`_render_perf_tree`, recursivo): árvore
  indentada de verdade, `%` sempre do PAI real (não do total do tick).
- **Alerta de tick lento** (`_perf_critical_path`): em vez da lista
  top-8 flat, desce sempre pelo FILHO mais caro até achar uma folha —
  é literalmente "a raiz do consumo" pedida pelo usuário. Mostra
  também "outros ramos" (siblings de nível 0 fora do escolhido) pra
  não perder contexto.
- **Trace JSON** (`_dump_perf_trace`): usa offset REAL de início por
  seção (`_perf_tree_tick_start_offset`, gravado em `_perf_pop()`) e
  todos os eventos na MESMA `tid=0` (é single-thread mesmo) — o viewer
  aninha por CONTER o timestamp, sem lógica de árvore no JSON.

**Junto** (mesmo mecanismo, decidido durante o desenho pra não
precisar reformular tudo de novo quando o próximo pico acontecer):
`_collect_deltas` ganhou sub-marks (`_sync_player_hp_dirty`,
`_sync_player_skill_levels_dirty`, `_process_quest_events`,
`_build_deltas_dict`) — antes `aoi_collect` era uma folha sem
breakdown nenhum, exatamente o que impediu explicar o pico de 394ms —
mais um diagnóstico de `gc.get_count()`/`gc.isenabled()` disparado só
quando `_collect_deltas` sozinho passa de 50ms, testando a hipótese do
Achado 2 na próxima vez que acontecer.

**Robustez**: push/pop é LIFO — se uma exceção no meio de um tick
pular algum `_perf_pop()` pareado, a pilha ficaria "suja" e todo mark
subsequente herdaria um prefixo errado pra sempre. Mitigado com reset
defensivo de `_perf_stack`/`_perf_span_t0` no TOPO de `_tick()` (mesmo
padrão do guard de `tick_count` regressivo do §29) — limita o dano a
"perdeu a medição de 1 tick", nunca corrompe permanente.

**Migração de call sites**: ~19 pontos em `server/world_server.py`
(todos seguiam o padrão `_t0 = perf_counter(); ...; self._perf_mark(label, _t0)`
→ viram `self._perf_push(label); ...; self._perf_pop()`, sem
reindentar blocos grandes) + 2 em `engine/world_systems.py`
(`EnemyAISystem`, callback injetado trocou de `perf_mark` — função que
recebia `(label, t0)` — pra `perf_push`/`perf_pop` — 2 callables sem
argumento de tempo, default no-op quando não injetado, elimina o `if
self._perf_mark_fn:` condicional que existia antes).

**Testes**: `tests/test_perf_log_improvements.py` reescrito por
completo (19 testes) contra o novo modelo de árvore — push/pop grava
sob caminho completo, mesmo rótulo sob pais diferentes não se mistura
mais, árvore indentada de verdade (profundidade maior = mais
indentação), caminho crítico aponta sistema lento real (mesmo teste de
`MinionSystem.update` travado do log antigo), trace JSON usa mesma
`tid`/`ts` real, sub-marks de `_collect_deltas` aparecem aninhados sob
`aoi_collect`. 3 testes de `tests/test_server.py`
(`TestTickPerfProfiler`) também reescritos contra a API nova. Suíte
completa (1085 testes) rodada 1x, tudo verde — mudança é pura
observabilidade (zero comportamento de jogo alterado), então não
entra no catálogo de "pontos únicos de verdade" que exige teste
automatizado sempre; suíte 3x consecutiva fica pra quando/se o usuário
pedir depois de validar em playtest real.

## 31. Teste de carga com 100 players/8 mapas simultâneos — scan redundante de combatentes em EnemyAISystem (12/08/2026)

Usando a árvore da Fase 4.7 (§30), pedido do usuário: script fora da
suíte com 100 players — 68 espalhados em 3 mapas abertos, 12 em 3
instâncias de Arena 2v2 simultâneas, 20 em 2 instâncias de BG 5v5
simultâneas (8 bundles de mapa ativos ao mesmo tempo), 1800 ticks (1
minuto simulado a 30 ticks/s).

**Saúde geral**: média de tick 19-23ms nas 6 janelas de relatório,
dentro do orçamento de 33ms mesmo com carga cheia. CPU do processo em
86-98% médio, pico 104% — máquina de dev perto do teto pra essa carga
(anotado, não é bug).

**Achado real, só visível com a árvore nova**: tick#339, pico de
152.7ms. Caminho crítico:
```
ai_bundles(126.6ms,83%) -> bnd:arena_poco_negro::arena2v2_2(109.0ms,86%)
  -> sys:EnemyAISystem(108.0ms,99%) -> sys:EnemyAISystem:loop_mobs(0.1ms,0%)
```
`sys:EnemyAISystem` custou 108ms mas o sub-mark `loop_mobs` só explicava
0.1ms (0%) — a lista flat da Fase 4.5 nunca teria mostrado isso (não
tinha os sub-marks nem a árvore pra separar "custo do sistema" de
"custo dos 2 pedaços medidos dentro dele").

**Causa raiz**: `EnemyAISystem.update()` (linhas ~2862-2871, ANTES de
qualquer sub-mark) reconstruía `_all_combatants_cache`/
`_npc_combatants_cache` via `self.world.get_entities_with(Position,
TileMovement, Combatant, CombatStats)` — **sem filtro de mapa na
query** — varrendo TODOS os combatentes do MUNDO INTEIRO (todos os 8
mapas juntos), só filtrando por mapa DEPOIS via um `_same_map(eid)`
local. Como `update()` roda 1x por BUNDLE por tick, o mesmo scan gigante
(~247 combatentes no teste real) repetia 8x por tick — mesma classe de
bug já corrigida 2x antes pra players (05/08/2026) e mobs (05/08/2026,
§34.74.38), nunca fechada pra combatentes porque em escala pequena
(1-2 players) o custo nunca doeu o bastante pra aparecer.

**Fix**: `_combatants_by_map` — índice canônico construído 1x por tick
em `WorldServer._tick()` (mesmo padrão exato de `_players_by_map`/
`_mobs_by_map`, mesmo bloco de código, logo depois), com `is_npc`
pré-computado (1 `get_component(NPC)` por combatente, feito 1 vez no
scan canônico — antes eram 2 queries separadas, com/sem `NPC`).
`EnemyAISystem.update()` ganhou parâmetro `combatants_by_map` e usa
`_combatants_on_map()` (novo helper de módulo, mesmo padrão de
`_players_on_map`/`_mobs_on_map`, com fallback pro scan direto quando
`combatants_by_map=None` — nunca quebra teste que chama `.update()` sem
o índice). `EnemyAbilitySystem`/`SpawnZoneSystem` (mesmo grupo
`proximity_systems`) ganharam o parâmetro só por uniformidade de
dispatch (aceitam e ignoram, mesmo padrão já usado pra `mobs_by_map`/
`tick_count` nesses 2 sistemas).

**Testes**: `TestCombatantsByMapIndex` (novo, em
`tests/test_ai_prefilter_spatial_index.py`) — prova diferencial (índice
vs scan direto devolvem o MESMO conjunto de eids pro mesmo mapa) +
garantia mais crítica: combatente de um mapa NUNCA aparece no cache de
`EnemyAISystem` de outro mapa (testado com mobs reais em 2 mapas
diferentes, via tick real de `WorldServer._tick`) — bug que, se
existisse, faria NPC/mob "ver" um alvo de instância/mapa errado. Suíte
completa (1087 testes) rodada 1x, tudo verde.

## 32. Teste de carga com combate real (100 players) + "fecha o buraco" da árvore de perf (12/08/2026)

Depois do §31, usuário pediu 2 coisas juntas: (1) repetir o teste de
carga mas com os 100 players lutando de verdade (mundo aberto contra
mob, arena/BG contra outro player OU minion), pra validar a correção
sob carga de combate real, não só presença ociosa; (2) fechar o "buraco"
que a própria correção do §31 expôs — `nao_instrumentado` ainda
aparecia grande (até 40ms+) em vários ticks porque a construção dos
índices canônicos (nova, do §31) e vários trechos antigos de
`WorldServer._tick()` nunca tinham `_perf_push`/`_perf_pop` (existiam
antes da Fase 4.7 inteira, só ficaram mais visíveis proporcionalmente
depois que o maior custo — o scan de combatentes — encolheu).

**Script de carga com combate** (fora da suíte, mesmo espírito do §31):
mecanismo usado é `CombatState.target_entity_id` — o mesmo campo que
`_process_player_attacks` já lê todo tick pra decidir quem ataca quem
(confirmado como o padrão real usado nos testes existentes, ex.
`tests/test_server.py`). Setar esse campo + posicionar os 2 lados
adjacentes (via `is_tile_walkable`, tenta 8 offsets até achar um tile
livre) é suficiente — não precisa reimplementar cooldown/dano, o tick
real já cuida disso sozinho. Mundo aberto: cada um dos 68 players mira
num mob real do PRÓPRIO mapa (`_mob_eids` filtrado por `MapLocation`).
Arena: dentro de cada partida (`ws._active_matches`, roster REAL —
`team_a`/`team_b`), 1v1 par a par. BG: metade do time A mira o time B
(PvP), metade miraria minion (`ws.world.get_entities_with(Minion)` na
instância) — nesse teste específico não deu tempo de nenhum minion
spawnar antes da checagem, ficou só PvP; suficiente pra validar o
mecanismo, não invalida o resultado. Checagem prévia (30 ticks) achou
48/100 players com HP alterado — combate de verdade acontecendo, não
só alvo configurado à toa.

**Resultado da validação do §31 sob combate**: nenhum pico repetiu o
padrão antigo (`sys:EnemyAISystem` custando dezenas de ms com os
sub-marks explicando ~0%) — pico mais alto caiu de 152.7ms pra 122.1ms
(tick#1372, e esse aqui tem causa DIFERENTE — ver abaixo), com
`mobs_ativos` chegando a 65 simultâneos (bem mais IA rodando que o
teste anterior, ocioso, que batia só 14).

**"Fecha o buraco"** — pedido explícito do usuário, mesmo commit.
Blocos que rodavam soltos, sem mark, dentro de `_tick()` (catalogados
lendo o método inteiro de cima a baixo):
- `pre_tick_snapshots` — snapshot de `pre_mob_target`/`player_hp_snap`
  + snapshot de estado de mob pra detectar aggro (2-3 loops O(mobs)/
  O(players) cada tick).
- `index_build` — construção dos 3 índices canônicos
  (`_players_by_map`/`_mobs_by_map`/`_combatants_by_map`, este último
  novo do §31) — o próprio scan que a correção do §31 centralizou
  nunca tinha mark próprio.
- `regen_and_status_ticks` — bloco GRANDE entre `global_systems` e
  `skill_requests`: detecção de aggro (som), `CombatStateSystem`
  headless (hp5/mana/rage/procs), regen de boneco de treino, regen de
  mob fora de combate, `ActiveRegen` (HoT de player), `ActiveManaRegen`,
  timer de Camuflagem, `FireShieldEffect`, channeling — o maior bloco
  sem mark que existia.
- `skill_followup_ticks` — knockback/projéteis expirados/bloco de
  gelo/tick do Fatiador de Corpos, entre `spell_completions` e
  `player_attacks`.
- `death_handling` — mark subiu pra cobrir também o sweep de "mob com
  HP≤0 sem PendingDeath" que rodava logo antes, mesma fase conceitual.
- `store_snapshot` — `_store_snapshot()` (lag comp), antes solto.
- `on_tick_callbacks` — o loop que despacha `SessionManager._on_tick`
  em produção (`register_on_tick`, `server/session.py`) NUNCA tinha
  mark — é onde o encode/enfileiramento de pacote pra CADA sessão
  realmente acontece; em teste/script sem `SessionManager` fica vazio
  (custo zero), mas em produção real é trabalho genuíno que ficava
  100% invisível no relatório.

**Resultado**: `nao_instrumentado` caiu de 28-42% do tick (janelas do
teste do §31) pra **~1% consistente** (0.9%-1.1% nas 6 janelas do
relatório periódico do reteste), inclusive nos picos individuais
("tick lento") — a maioria caiu pra 0.2-0.5ms de não-instrumentado,
poucas exceções a ~1-2ms. Achados NOVOS que só apareceram por causa
disso: tick#111 e tick#414 tiveram `global_systems` (não `ai_bundles`)
como maior ramo do caminho crítico; tick#416 teve `tower_system`
(30.2ms) como maior ramo — nenhum dos dois teria aparecido claramente
antes (ficariam diluídos dentro do "não instrumentado" gigante).

**Testes**: nenhum teste novo dedicado — mudança é só bookkeeping de
medição (mesma garantia da Fase 4.7 original: `_perf_push`/`_perf_pop`
já tem cobertura própria, os novos call sites só reusam o mesmo
chokepoint). Suíte completa (1087 testes) rodada 1x depois da mudança,
tudo verde — confirma que nenhum push/pop novo ficou despareado.

## 33. Teste de carga de 3 minutos (BG com colisão real de levas) + instrumentação restante do EnemyAISystem (12/08/2026)

Repetição do §32, agora 180s simulados (não 60s) — pedido explícito do
usuário pra dar tempo das levas de minion das 2 BGs colidirem de
verdade (`wave_interval_s=45s`, mais o tempo de PREPARO de 30s antes do
`fight_started`). Confirmado: minions começaram a aparecer em t≈90s,
chegaram a 79 vivos simultâneos em t≈140s. As 3 arenas (1v1, resolvem
rápido) concluíram e descarregaram no meio do teste — `maps_ativos_peak`
caiu de 8/8 pra 5/5 depois de t≈95s, sobrando só as 2 BGs pro resto —
comportamento correto do ciclo de vida da partida, não bug.

**Saúde geral**: média de tick 16-27ms nas 18 janelas do relatório,
dentro do orçamento o teste inteiro. `minion_waves` passou a aparecer
como maior ramo do caminho crítico repetidamente a partir de t≈90s
(22-26ms por disparo de leva) — exatamente o custo que o usuário queria
conseguir medir, antes invisível/diluído.

**Achado — pico extremo de 512.1ms** (tick#476, ~t=16s, o pior desta
sessão inteira):
```
ai_bundles(325.9ms,64%) -> bnd:map_1(137.0ms,42%) -> sys:EnemyAISystem(132.8ms,97%)
  -> sys:EnemyAISystem:loop_mobs(0.4ms,0%)
outros ramos: tower_system=86.5ms | combat_spatial_hash_build=74.8ms
```
`sys:EnemyAISystem` custou 132.8ms mas nem `loop_mobs` (0.4ms) nem
`prefiltro` (menor ainda, não foi o escolhido pelo caminho crítico)
explicavam isso — sobrava ~130ms DENTRO de `EnemyAISystem.update()`
fora dos 2 sub-marks existentes. `tower_system`/`combat_spatial_hash_build`
também picaram MUITO no mesmo tick — 3 sistemas diferentes juntos, sinal
de possível pausa externa (GC/SO), não só 1 causa algorítmica isolada.

**Investigação, a pedido do usuário** ("quero que instrumente" —
identificar antes de consertar, ver contexto do processo abaixo):
lendo `EnemyAISystem.update()` de cima a baixo, achado o resto do
buraco — 2 blocos entre o início da função e o `_perf_push_fn(
"sys:EnemyAISystem:prefiltro")` que nunca tiveram mark:

1. **Preâmbulo** (construção de `_all_combatants_cache`/
   `_npc_combatants_cache` a partir do índice + `_players_this_map_cache`
   + o branch "sem player, mob dorme") — novo mark
   `sys:EnemyAISystem:preamble`. Cuidado: esse branch tem um `return` no
   meio (mob dorme quando não há player no mapa) — o pop acontece ANTES
   do return, não depois, senão a pilha ficaria com um push sem par
   nesse caminho.
2. **`_get_occupied_tiles()`** (linha ~2512) — achado real, MESMA classe
   de bug do §31: `get_entities_with(TileMovement)` **sem filtro de
   mapa**, varrendo TODAS as entidades com TileMovement do MUNDO INTEIRO
   (players+mobs+minions+NPCs), filtrando por mapa só DEPOIS em Python —
   repetido 1x por bundle por tick. Forte candidato a ser o grosso dos
   130ms perdidos (mundo tinha ~250-300 entidades com TileMovement
   durante a colisão de leva). Novo mark
   `sys:EnemyAISystem:occupied_tiles` — **só instrumentado, NÃO
   corrigido ainda** (decisão explícita do usuário, ver processo
   abaixo).

**Processo combinado com o usuário daqui pra frente** (explicado por
ele, registrado aqui pra próxima sessão não perder o fio): a medição
agora é uma fase própria, separada de corrigir — objetivo é ir
isolando casos de alto consumo (otimizáveis ou não) até ter uma lista
ranqueada (~top 10) do mais pesado pro mais leve. Só DEPOIS da lista
pronta é que cada item entra em pauta um a um, com pesquisa de
referência (mesma disciplina já em vigor no projeto — AzerothCore/
Veloren) pra decidir se é problema de arquitetura, de código, ou algo
não pensado ainda — nunca corrigir no impulso assim que um pico
aparece. `_get_occupied_tiles()` acima é o primeiro candidato claro
pra essa lista, mas ainda não foi atacado.

**Testes**: suíte completa (1087 testes) rodada 1x, tudo verde — mesma
garantia de sempre (push/pop é o chokepoint único, os 2 sites novos só
reusam ele; o `return` no meio do branch foi checado manualmente pra
garantir pop pareado nos dois caminhos de saída).

## 34. Top 10 de consumo acumulado — fase de identificação (12/08/2026)

Primeira lista ranqueada, gerada rodando o MESMO teste de carga de 3
minutos do §33 de novo (100 players, minions colidindo), agora com os
marks novos (`preamble`/`occupied_tiles`) já capturando o resto do
buraco. Critério escolhido pelo usuário: **custo ACUMULADO** (soma de
`avg×n_ticks` por rótulo-folha, agregado através de TODAS as 18 janelas
do relatório periódico, TODOS os mapas/bundles somados por rótulo) —
reflete o que mais pesa na CPU no total da sessão, não o pico isolado
mais dramático (ver nota sobre `minion_waves` abaixo).

| # | Rótulo (folha) | ms acumulados (3min) | % do total instrumentado |
|---|---|---|---|
| 1 | `sys:TileValidationSystem` | 20.304ms | 16.8% |
| 2 | `sys:EnemyAISystem:occupied_tiles` | 19.622ms | 16.2% |
| 3 | `sys:EnemyAISystem:prefiltro` | 6.828ms | 5.7% |
| 4 | `index_build` | 6.714ms | 5.6% |
| 5 | `global_systems` | 6.696ms | 5.5% |
| 6 | `sys:EnemyAbilitySystem` | 5.467ms | 4.5% |
| 7 | `sys:EnemyAISystem:loop_mobs` | 5.340ms | 4.4% |
| 8 | `minion_system` | 5.167ms | 4.3% |
| 9 | `sys:TauntSystem` | 4.805ms | 4.0% |
| 10 | `post_tick_bookkeeping` | 4.443ms | 3.7% |

(fora do top 10, ainda medidos: `tower_system`, `regen_and_status_ticks`,
`combat_spatial_hash_build`, `player_attacks`, `pre_tick_snapshots`,
`sys:SpawnZoneSystem`, `_build_deltas_dict`,
`_sync_player_skill_levels_dirty`, `death_handling`, `store_snapshot`,
`_sync_player_hp_dirty`, `sys:EnemyAISystem:preamble`, `bg_queue_ticks`,
`skill_followup_ticks`, `minion_waves` — todos ≤3.1% cada.)

**Nota importante — `minion_waves` NÃO está no top 10 por este
critério** (ficou em ~0.3%, quase no fim da lista) apesar de ter sido o
maior ramo do caminho crítico repetidas vezes no §33 — é CARO por
disparo (22-26ms) mas RARO (1x a cada 45s por lane), então o acumulado
é pequeno. Confirma na prática a diferença entre os 2 critérios
discutidos com o usuário antes de escolher: por acumulado, é irrelevante;
por pico/p99, seria top 3 fácil. Guardado aqui pra não se perder quando
chegar a hora de decidir prioridade dentro do top 10 (ex: usuário pode
querer promover `minion_waves` mesmo fora do top 10 acumulado, por ser
uma trava perceptível).

**"não instrumentado"** ficou consistente em ~1.0-1.5% em todas as 18
janelas — buraco do §32/§33 permanece fechado.

**Contexto já conhecido sobre os itens 1-2** (dos §31/§33, não
repesquisado aqui): `sys:EnemyAISystem:occupied_tiles` (#2) é
`_get_occupied_tiles()` fazendo `get_entities_with(TileMovement)` SEM
filtro de mapa — MESMA classe de bug do §31 (scan do mundo inteiro
repetido 1x por bundle por tick), ainda não corrigido, forte candidato
a "problema de código" (não de arquitetura) já mapeado. `sys:
TileValidationSystem` (#1, MAIOR item da lista) ainda não teve a causa
investigada — candidato natural a checar primeiro se tem o MESMO padrão
(scan sem filtro de mapa) antes de supor outra causa.

**Próximo passo** (combinado com o usuário, NENHUM item foi atacado
ainda): pesquisar item por item, do #1 pro #10, contra as referências
do projeto (AzerothCore/Veloren) pra classificar cada um como problema
de arquitetura, de código, ou solução não pensada — só depois disso
desenhar e propor a correção, um item de cada vez.

## 35. Correção dos itens #1, #2 e #3 do ranking — mesma causa raiz, técnicas diferentes (12/08/2026)

Investigação do #1 (`sys:TileValidationSystem`) achou a MESMA causa do
#2 sem precisar de pesquisa externa: `TileValidationSystem.update()`
também fazia `get_entities_with(TileMovement)` sem filtro de mapa na
query, filtrando por `MapLocation` depois em Python — mesmo padrão,
repetido 1x por bundle por tick. Juntos, #1+#2 já eram 33% de todo o
custo acumulado do §34. Durante a investigação, achado um **3º
ocorrência, pior que as outras duas**: `MinionSystem._get_occupied_tiles`
(usada pelo #8, `minion_system`) fazia o MESMO scan **1x POR MINION**
(não por bundle) — até ~20 mil iterações/tick com muitos minions vivos.

**Contexto histórico verificado antes de mexer** (usuário pediu cautela
específica: achava que `_get_occupied_tiles` do Minion tinha sido uma
ALTERNATIVA ao A*, porque A* "não aguentava"): conferido em
`historico/ARQUITETURA ONLINE HISTORICO.md`. Não procede exatamente
assim — A* nunca foi substituído. §34.73.2 (30/07) mostra
`_get_occupied_tiles` sendo ADICIONADO como `dynamic_obstacles` PRA
ALIMENTAR o A* (antes disso os minions não sabiam nada uns dos outros e
martelavam repath contra o mesmo tile). O episódio real de "A* caro
demais" é outro, §34.74.26 (04/08) — A* rodando 1x POR MINION por lane
travava o event loop inteiro (42 buscas síncronas empilhadas,
desconectava todo mundo); o fix real foi calcular a rota 1x POR LANE em
vez de por minion, nunca abandonar A*/occupied-tiles. A memória do
usuário estava parcialmente certa (A* FOI caro demais uma vez, de
verdade) mas a causa/fix específicos eram outros — verificação evitou
corrigir em cima de uma premissa errada.

**Risco real identificado a partir da checagem**: `_get_occupied_tiles`
do Minion é chamado DENTRO do loop que processa minion por minion, e
cada chamada precisa ver o movimento que minions ANTERIORES do MESMO
loop/tick já decidiram — um índice único CONGELADO no início do tick
(a correção usada pros itens #1/#2) reintroduziria exatamente o
"martelando repath, travado" que o mecanismo original resolveu.

**Pesquisa externa** (pedida pelo usuário pra reforçar a ideia antes de
implementar, já que não era "garantida"): técnica de **"reservation
table" / Cooperative A\* (CA\*)** — literatura de multi-agent
pathfinding (David Silver, citada como "comparada contra A* with Local
Repair, o padrão atual da indústria de jogos"). Cada agente processado
em sequência reserva os tiles do seu movimento; agentes seguintes
respeitam essas reservas na própria busca — exatamente o padrão que o
projeto precisava, confirmando que a técnica proposta não era
inventada, é estabelecida.

**Correção aplicada** (3 partes, mesmo índice canônico `_tile_movement_by_map`
alimentando técnicas DIFERENTES conforme a necessidade de frescor):
1. `WorldServer._tick()`: novo índice `_tile_movement_by_map` (dict
   mapa→lista de `(eid, TileMovement)`), construído no mesmo bloco dos
   outros 3 índices (`players_by_map`/`mobs_by_map`/`combatants_by_map`).
2. `TileValidationSystem.update()` (#1) — novo parâmetro
   `tile_movement_by_map`, deriva `_occupied` do índice em vez de
   escanear; entrou em `proximity_systems` (antes não estava) pra
   receber o índice no dispatch comum. `game.py` (cliente, sem
   `map_filter`) confirmado intacto — fallback pro scan direto quando
   `None`.
3. `EnemyAISystem._get_occupied_tiles` (#2) — mesmo tratamento, novo
   parâmetro, deriva do mesmo índice.
4. `MinionSystem._get_occupied_tiles` (#3) — NÃO usa o índice congelado
   direto. Semeia (lazy, 1x por mapa) uma cópia MUTÁVEL
   (`_working_occupied_by_map`, dict tile→eid) a partir do índice, e
   `_reserve_tile()` (novo método) atualiza essa cópia a cada
   `start_tile_movement` da própria classe (2 call sites:
   `_walk_toward`/`_advance_along_route`) — preserva o frescor
   intra-tick. Resetada no início de CADA `update()`.

**Resultado medido** (mesmo teste de carga de 3min, 100 players, 8
mapas, minions colidindo — repetido antes/depois da correção):

| Item | Antes (acumulado/3min) | Depois | Redução |
|---|---|---|---|
| `sys:TileValidationSystem` (#1) | 20.304ms (16.8%) | 1.454ms (2.1%) | **93%** |
| `sys:EnemyAISystem:occupied_tiles` (#2) | 19.622ms (16.2%) | 604.5ms (0.9%) | **97%** |
| `minion_system` (#8, inclui #3) | 5.167ms (4.3%) | 3.004ms (4.3%) | 42% |

**Tempo real de CPU pra processar os mesmos 180s simulados**: 124.6s →
72.7s, **42% mais rápido no total**, mesma carga exata. "não
instrumentado" continuou saudável (~1.6-1.9%, não voltou a crescer).
Nenhum sinal de minion travado/martelando repath no teste (contagem de
minions vivos ao longo do tempo com formato saudável, igual ao teste
anterior).

**Testes**: diferencial (índice bate com scan direto) + garantia de
não-vazamento entre mapas, pros itens #1/#2, em
`tests/test_map_filter.py` (2 testes novos por sistema). Pro #3, em
`tests/test_minions.py::TestMinionReservationTable` (4 testes) — o mais
importante: prova explícita de que um minion processado DEPOIS no loop
enxerga a reserva de um minion processado ANTES, no MESMO tick (a
garantia central que motivou a técnica diferente) + reset da reserva a
cada `update()` + a semente só acontece 1x por mapa (não resemeia
perdendo reservas já feitas). Suíte completa (1094 testes) rodada 1x,
tudo verde.

## 36. Reranking pós-correção + unificação de `index_build` (12/08/2026)

Reranking do mesmo teste de carga de 3min, agora pós-correção do §35:
#1/#2 antigos (`TileValidationSystem`/`EnemyAISystem.occupied_tiles`)
saíram do topo (2.1%/0.9%), `index_build` (o próprio mecanismo de
correção) virou o novo #1 (8.215ms, 11.8%) — esperado, absorveu o
trabalho que antes estava espalhado (e multiplicado) em 8 lugares.
Usuário pediu pra investigar mesmo assim.

**Achado, lendo `engine/world.py::World.get_entities_with` antes de
propor qualquer coisa**: a busca já é inteligente — pega o índice do
componente MENOS comum entre os pedidos e só itera sobre esse (nunca o
mundo inteiro). Por isso os bugs #1/#2/#3 do §34/§35 eram bugs de
verdade (a query pedia só 1 componente amplo sem filtro de mapa, o
"conjunto menos comum" acabava sendo o mundo inteiro) — mas
`index_build` em si não tem esse defeito. O que sobra: `_tile_movement_by_map`
pede só `TileMovement` (sem outro filtro pra restringir), então "o
conjunto menos comum" é o índice de TileMovement inteiro — TODAS as
entidades que se movem. Os outros 3 índices (players/mobs/combatentes)
são SUBCONJUNTOS desse mesmo universo — 4 passadas separadas pelo ECS,
cada uma refazendo `MapLocation` da MESMA entidade quando ela aparece
em mais de um índice.

**Pesquisa** (pedida pelo usuário pra reforçar a técnica): EnTT (ECS
sparse-set mais usado em jogos C++ reais) tem uma feature inteira —
"groups" — dedicada exatamente a este problema (várias views
sobrepostas do MESMO conjunto de entidades, caras como queries
separadas). A técnica deles reordena o armazenamento fisicamente (não
cabe no ECS baseado em dict deste projeto); o equivalente do tamanho
certo aqui: 1 passada manual pela base mais ampla (`TileMovement`) +
checagem de presença de componente (O(1), dict `in`) pra decidir se
cada entidade também entra nos 3 índices mais restritos.

**Correção**: os 4 `get_entities_with()` separados viraram 1 só (sobre
`TileMovement`), com os outros 3 índices derivados por checagem de
componente por entidade — mesmo formato de tupla de saída preservado
em todos os 4 (nenhum consumidor mudou). Guard explícito preserva a
semântica original: entidade sem `Position`/`CombatStats` nunca entrava
nos 3 índices restritos mesmo antes (só no de tile-movement), mantido
igual.

**Resultado medido** (mesmo teste de carga de 3min): `index_build`
8.215ms → 6.234ms (**24% de redução** — abaixo da estimativa inicial de
~50%, porque `get_entities_with` já era eficiente pras 3 queries mais
restritas; o ganho real veio de eliminar `MapLocation` redundante +
overhead de chamada Python, não de "4 scans completos" como se todos
fossem igualmente caros). Tempo real de CPU pra processar os mesmos
180s simulados: 72.7s → 67.1s (mais 7.7% de redução, em cima dos 42%
já conquistados no §35). Nenhum sinal de regressão (minions colidindo
normalmente, contagens saudáveis).

**Testes**: `TestIndexBuildSinglePass` (novo, em
`tests/test_ai_prefilter_spatial_index.py`, 3 testes) — usa um tick
REAL de `WorldServer` (os 4 dicts são variáveis locais de `_tick()`,
sem seam próprio isolado) e inspeciona os caches já expostos de
`EnemyAISystem` (`_players_this_map_cache`/`_all_combatants_cache`/
`_npc_combatants_cache`) pra confirmar que cada índice ainda contém
exatamente quem devia — inclusive prova de que um PLAYER (mesma base
TileMovement+Position+CombatStats que um mob) não vaza pro índice de
mobs por faltar `AIControlled`/`InitialPosition`/`DetectionRadius`.
Suíte completa (1097 testes) rodada 1x, tudo verde.

## 37. Bug de correção real achado investigando #2 (`global_systems`) — `TileMovementSystem` pegava "o primeiro Tilemap" do mundo (12/08/2026)

Ao investigar `global_systems` (novo #2 do ranking pós-§36), leitura de
`TileMovementSystem.update()` (roda GLOBALMENTE, sem `map_filter`, 1
`update()` só pra TODAS as entidades de TODOS os mapas) achou, ao
calcular elevação/transição/passthrough pro tile onde uma entidade
ACABA de chegar:

```python
for _, _tc in self.world.get_entities_with(Tilemap):
    _tm_comp = _tc
    break
```

Isso é a MESMA classe de bug já documentada como regra obrigatória no
`CLAUDE.md` do projeto ("NUNCA pegar 'o primeiro' Tilemap do world —
usar o bundle via `get_entity_map(eid)`", com histórico real: Interceptar
"bloqueado" em terreno aberto, Tiro Repulsivo stunando em parede
fantasma) — mas SEM nem passar pelo mecanismo já existente pra resolver
isso certo. Diferente de correção de perf pura: é um bug de
CORRETUDE — com 8 mapas ativos ao mesmo tempo (realidade real de
produção, não hipotética), toda entidade que não estivesse no mapa
"sortudo" da iteração calculava elevação contra a matriz de tile
ERRADA ao terminar de andar, silenciosamente, sem ninguém ter
reportado ainda. Achado reportado ao usuário como prioridade separada
do ranking de perf (correção de bug > otimização); usuário pediu fix
imediato.

**Varredura de occorrências irmãs** (mesmo padrão `get_entities_with(Tilemap)`
sem resolver, buscado no repo inteiro antes de corrigir só o achado
óbvio):
- `PathfindingSystem`/`TileValidationSystem._get_tilemap_component()`
  (`engine/world_systems.py`) — têm o MESMO fallback, mas são
  construídas com `tilemap_entity` explícito por bundle
  (`_load_map_for`) — fallback nunca dispara no servidor na prática.
  Seguro, não mexido.
- `server/spell_completion_processor.py` (linha ~1319) — JÁ resolve
  via `get_entity_map(target_id)` + `_map_bundles` corretamente, "grab
  primeiro" é só fallback de último recurso — já é o fix documentado
  do bug histórico "Tiro Repulsivo stunando em parede fantasma"
  (CLAUDE.md). Já correto, não mexido.
- `server/mob_system.py::ServerMobSystem._is_walkable` — tem o bug,
  mas `ServerMobSystem` nunca é instanciada em lugar nenhum do repo
  (`grep` confirmou 0 call sites) — código morto, sem impacto real.
  Não mexido (mesma régua já usada nesta sessão pra
  `engine/save_system.py`: não corrigir código morto sem pedido
  explícito).
- `engine/skill_handlers.py::HeadlessSkillHandler._has_los` — tem o
  bug, mas `_has_los` nunca é chamado em lugar nenhum (0 call sites) —
  também código morto. Não mexido.
- Ocorrências em `game.py`/`ui/systems.py`/`ui/god_mode.py`/scripts de
  teste — todas client-side (mapa único, resolver nunca instalado lá
  de propósito, ver comentário em `engine/world_systems.py`) — "pegar
  o único Tilemap" é CORRETO nesses contextos, não são bug.

**Correção**: `TileMovementSystem.update()` resolve via
`_svc_resolver(entity_id)` (mesmo mecanismo já usado por
`is_tile_walkable`, "ponto único de verdade" pra isso) — pega
`_bundle.tilemap_entity` e busca o `Tilemap` certo via
`get_component()`, direto. Fallback pro scan antigo só quando
`_svc_resolver is None` (cliente offline, mapa único — correto lá).

**Teste**: `TestTileMovementSystemResolvesCorrectMap` (novo, em
`tests/test_ai_prefilter_spatial_index.py`) — player transferido pro
mapa B, forçado a "acabar de chegar" num tile neste tick, espiona
`world.get_component` durante `TileMovementSystem.update()` e confirma
que o `Tilemap` do PRÓPRIO mapa do player foi consultado. Diferencial
confirmado por revert manual: sem a correção, o teste falha (a busca
antiga usa `get_entities_with`, que não passa por `get_component`
nenhuma vez — a lista de consultas fica vazia, prova que o caminho
antigo nunca resolvia por entidade). Suíte completa (1098 testes)
rodada 1x, tudo verde.

## 38. `global_systems` sub-instrumentado + `prefiltro` reinvestigado — sem bug novo — ranking congelado como referência (12/08/2026)

**`global_systems`** ganhou 3 sub-marks (`global_systems:tile_movement`/
`:status_effects`/`:projectiles`, `server/world_server.py`) — antes era
1 mark só pros 3 sub-sistemas (`TileMovementSystem`/
`_ServerStatusEffectSystem`/`ProjectileSystem`), sem saber qual pesava.
Resultado: `tile_movement` domina (~5.1% do tick), `status_effects`
bem menor (~2.7%), `projectiles` nem aparece no top 25 (carga do teste
tinha pouco tiro à distância). Investigação de cada um:
- `StatusEffectSystem` (`engine/core_systems.py`) — bem desenhado, 1
  scan só (sem repetição por bundle), sai cedo pra entidade sem efeito
  ativo (`if not sfx.effects: continue`). Nenhum bug achado.
- `TileMovementSystem` — achou e corrigiu o bug de correção real do
  §37 (Tilemap errado). O que sobra é custo intrínseco: toda entidade
  com `TileMovement` precisa ser tocada todo tick (quem anda, calcula
  posição; quem está sob "slow", decai o timer). Técnica real
  pesquisada pra reduzir isso — "dirty tracking" (só processar quem
  está realmente se movendo/afetado, via um conjunto mantido
  incrementalmente) — **NÃO recomendada**: exige gancho em TODO lugar
  que liga/desliga `is_moving` ou aplica/remove "slow", mesma classe de
  risco (gancho esquecido = estado desatualizado em silêncio) que o
  usuário já recusou explicitamente na Fase 4.6 pro pré-filtro de IA,
  por um ganho comparável ou menor (5.1% do tick aqui vs. o caso da
  Fase 4.6).

**`sys:EnemyAISystem:prefiltro`** reinvestigado (não do zero — reli a
Fase 4.6 antes de mexer, mesma disciplina de "checar histórico antes de
reinvestigar"). Código atual já reflete as 2 camadas de otimização
daquela fase (índice espacial `SpatialHash` + cache de elegibilidade
throttled a cada `_PREFILTER_REFRESH_TICKS=3` ticks). Custo restante
(~0.92ms/tick médio) é o PISO esperado da técnica escolhida, medido
antes só até ~20 players — 100 players é escala bem maior, número
absoluto maior é esperado, não um bug novo. Técnica mais forte já
identificada e já pesquisada na própria Fase 4.6: Grid/Cell do
AzerothCore (ativação PUSH-based — nunca toca mob fora do raio de
nenhum player, nem pra descartar barato) é estruturalmente superior ao
modelo atual ("toca todo mundo, filtra barato"), mas foi explicitamente
NÃO adotada por ser um redesenho maior que o throttle escolhido — nada
mudou desde então que justifique reabrir essa decisão sem uma conversa
de arquitetura nova.

### Ranking congelado como referência (pra retomar sem reinvestigar do zero)

Baseado no mesmo teste de carga de 3min (100 players, 8 mapas, minions
colidindo) usado desde o §32. Status: ✅ **otimizado** (já teve correção
aplicada e medida) | 🔍 **investigado, sem bug** (código lido, é custo
intrínseco ou já bem desenhado — não vale reinvestigar sem novo motivo)
| ⬜ **não investigado** (candidato real pra próxima rodada).

| # | Item | ms acum. (3min) | % | Status | Nota |
|---|---|---|---|---|---|
| 1 | `index_build` | 6.554ms | 9.6% | ✅ | §36 — 4 `get_entities_with()` viraram 1 (base `TileMovement` + checagem de componente), -24%. Perto do piso do ECS. |
| 2 | `sys:EnemyAISystem:prefiltro` | 4.958ms | 7.3% | 🔍 | §29 (Fase 4.6, 2 camadas) + §38. Piso da técnica atual a 100 players. Próximo passo real exigiria Grid/Cell estilo AzerothCore — redesenho maior, não decidir sozinho. |
| 3 | `sys:EnemyAbilitySystem` | 4.849ms | 7.1% | ⬜ | Habilidades especiais de mob (DoT/debuffs) — nunca lido a fundo pra achar padrão de bug. Candidato real. |
| 4 | `sys:TauntSystem` | 4.417ms | 6.5% | ⬜ | Movimento forçado de player sob "taunted" — nunca investigado. |
| 5 | `sys:EnemyAISystem:loop_mobs` | 4.350ms | 6.4% | ⬜ | Corpo principal do loop de IA (perseguição/ataque/pathfinding) — só o QUE ENTRA no loop foi otimizado (prefiltro); o que acontece DENTRO pra cada mob ativo nunca foi revisto. |
| 6 | `post_tick_bookkeeping` | 3.616ms | 5.3% | ⬜ | Detecção de aggro/projétil novo/movimento iniciado + snapshot DEBUG — vários loops `for eid in self._mob_eids` (§32), nunca checados individualmente por redundância. |
| 7 | `tower_system` | 3.605ms | 5.3% | ⬜ | IA de torre (BG) — nunca investigado. |
| 8 | `player_attacks` | 3.572ms | 5.2% | ⬜ | `_process_player_attacks` — O(players), população pequena (~100 max), suspeita baixa de bug de escala, mas nunca lido a fundo. |
| 9 | `global_systems:tile_movement` | 3.456ms | 5.1% | 🔍 | §37/§38 — bug de correção real já corrigido; custo restante é intrínseco, técnica de redução (dirty tracking) avaliada e não recomendada. |
| 10 | `combat_spatial_hash_build` | 3.455ms | 5.1% | ⬜ | Constrói `_combat_spatial_hash` (Tower/Minion) — já é "1x por tick" desde antes desta sessão (não por-bundle), suspeita baixa, mas nunca confirmado por leitura de código. |
| 11 | `regen_and_status_ticks` | ~5.0% | 5.0% | ⬜ | Bucket GRANDE do §32 (aggro-som + CombatStateSystem + regen de dummy/mob + ActiveRegen/ManaRegen + camuflagem + fireshield + channeling) — nunca sub-dividido; pode esconder 1 sub-bloco caro atrás de 7 baratos. Bom candidato a "fechar buraco" de novo se algum dia subir no ranking. |
| 12 | `sys:SpawnZoneSystem` | ~4.6% | 4.6% | ⬜ | Spawn de mob por zona — nunca investigado. |
| 13 | `pre_tick_snapshots` | ~4.5% | 4.5% | ⬜ | Snapshot de aggro/HP no início do tick (§32) — O(mobs)+O(players), nunca confirmado se tem redundância. |
| 14 | `minion_system` | ~4.2% | 4.2% | 🔍 (parcial) | §35 — `_get_occupied_tiles` (item #3 do ranking antigo) já corrigido via reservation table. Resto do loop por-minion (target/ataque/pathing) nunca revisto. |
| 15 | `_build_deltas_dict` | ~3.8% | 3.8% | ⬜ | Monta payload de delta pro broadcast — escala com players×entidades visíveis, suspeita de ser majoritariamente intrínseco, não confirmado. |
| 16 | `_sync_player_skill_levels_dirty` | ~3.0% | 3.0% | ⬜ | Cresceu de forma desproporcional com 100 players num teste anterior desta sessão (0.3ms com 100 vs ~0.003-0.01ms com 1) — vale conferir se é só O(players) mesmo ou tem algo O(players²). |
| 17 | `global_systems:status_effects` | 1.814ms | 2.7% | 🔍 | §38 — bem desenhado, sem bug. |
| 18 | `sys:TileValidationSystem` | 1.578ms | 2.3% | ✅ | §35/§36 — item #1 antigo do ranking, -93%. |
| 19-25 | `death_handling`/`store_snapshot`/`sys:EnemyAISystem:occupied_tiles`(✅ §35, -97%)/`_sync_player_hp_dirty`/`sys:EnemyAISystem:preamble`/`bg_queue_ticks`/`skill_followup_ticks` | ≤1.2% cada | ≤1.2% | — | Todos pequenos o bastante (≤1.2% do total) pra não valerem investigação isolada por ora. |

**Como reproduzir o teste de carga pra atualizar este ranking**: script
fora da suíte (não commitado) — 100 players (68 mundo aberto + 12 em 3
arenas 2v2 + 20 em 2 BGs 5v5), 180s simulados (5400 ticks a 30/s),
combate real configurado via `CombatState.target_entity_id` (mundo
aberto vs mob; arena 1v1 par a par; BG metade PvP metade vs minion
quando disponível, refresh a cada 10s). Ver §32/§33 pro desenho
completo do script.

## 39. (movido) Bug real do talento Reciclagem — flecha reciclada sem `item_id` (12/08/2026) — ver HISTORICO_BUGS.md §4

## 40. (movido) Bug real na BG — corpo do player morto recuperava HP sozinho (level-up de instância curava quem já está morto) (12/08/2026) — ver HISTORICO_BUGS.md §5

## 41. (movido) Torre recebendo knockback do Tiro Repulsivo (12/08/2026) — ver HISTORICO_BUGS.md §6

## 42. (movido) Colisão real de 2 tiles pra torre + barra de HP no topo + origem configurável de projétil (12/08/2026) — ver HISTORICO_FEATURES.md §2

## 43. Venda na loja nunca removia o item do Inventory AO VIVO do servidor — item voltava no relog E desalinhava EQUIP_ITEM (12/08/2026)

Usuário relatou 3 sintomas na mesma sessão de playtest, inicialmente
sem saber se eram relacionados: (1) vendeu itens no mercador, gold
ficou certo, mas ao relogar os itens vendidos voltaram pra bag; (2)
tentando confirmar o item #2 (Reciclagem, §39), descobriu que
desequipar espada + equipar arco/aljava simplesmente não fazia efeito
nenhum — personagem "sem arco equipado" mesmo com o arco visualmente na
tela, impedindo qualquer ataque à distância; (3) por causa do #2, nunca
conseguiu nem tentar lootear a flecha reciclada de novo pra confirmar
se aquele bug (relatado antes, ver contexto de §39) ainda existia.

**Causa raiz (única, explica #1 e #2):** `process_shop_sell`
(`server/world_server.py`) sempre mutou a `Wallet` (gold) — por isso o
gold nunca ficou errado — mas NUNCA tocava o `Inventory` AO VIVO do
servidor. Comparando com `process_shop_buy`, que tem um passo explícito
("6. Atualiza o Inventory em memória do servidor") pra adicionar o item
comprado à cópia viva, `process_shop_sell` nunca ganhou o equivalente
pra remover — mesma classe de bug já achada e corrigida nesta sessão
pra forja/reciclagem/consumível (ver `tests/test_craft_recycle_
protocol.py`, itens #5/#6 do docstring do arquivo — "sem isso, a bag
local nunca refletia o [gasto], só sumia no próximo relog"), só que
faltou aplicar o mesmo fix pro lado da venda especificamente.

**Por que isso quebrou o EQUIP também, não só a persistência:**
`_build_save_merge` (Fase 0, 07/08/2026, §12/§13) persiste
`live_inventory` (lido do Inventory ao vivo) por cima do que o cliente
reporta — decisão CORRETA e necessária pra outro bug (overlay de
instância contaminando o save real), mas dependia de todo mutador de
Inventory manter essa cópia viva sincronizada. Além da persistência,
`equip_item_from_inventory` (débito A4, 10-11/08/2026) é POSICIONAL:
"NUNCA confia em item mandado pelo cliente — só na posição; o item de
verdade é lido do Inventory real do servidor" (`item = inv.items
[inv_index]`). Depois de UMA venda sem remoção real, a lista viva do
servidor ficava um item "a mais" (fantasma) na posição errada — o
`inv_index` que o cliente manda pra "equipar o arco no slot 3" passava
a apontar pro item ERRADO (ou posição fora do range) no servidor,
rejeitando ou equipando a coisa errada silenciosamente. Isso explica
por que o bug pareceu "pior do que parece" pro usuário — não é 2 bugs
separados, é 1 causa raiz com 2 sintomas em cascata (venda quebra
persistência E, a partir da PRIMEIRA venda da sessão, quebra qualquer
equip/desequip subsequente, sem precisar relogar).

**Correção**: `process_shop_sell` agora remove/decrementa o item do
`Inventory` ao vivo por `item_id` (fallback por nome — item sem
item_id, save antigo), mesmo padrão de remoção já usado em `craft_item`
(decrementa stack, remove o slot só ao zerar). Não bloqueia a venda se
o item não for encontrado ao vivo (mesmo espírito conservador do
fallback de `client_value` já existente) — decisão deliberada de NÃO
adicionar um novo caminho de rejeição numa correção urgente, pra não
arriscar quebrar nenhum chamador que já depende de "vender sempre
sucede". Anotado como possível endurecimento futuro (rejeitar venda de
item que o servidor não confirma que o player possui), não decidido
agora.

**Item #3 (Reciclagem) continua sem causa raiz confirmada** — usuário
não conseguiu testar de novo porque o bug de equip (#2) bloqueava
qualquer ataque à distância. Fica pendente de reteste pelo usuário após
este fix + relog (o relog é necessário porque o `Inventory`/posições já
dessincronizados na sessão atual só se corrigem carregando o save real
do banco de novo — este fix impede a dessincronização ACONTECER de
novo, mas não desfaz sozinho uma sessão já em curso).

**Testes**: `tests/test_craft_recycle_protocol.py::
TestShopSellRemovesFromLiveInventory` (3 testes — remove item vendido
do Inventory vivo; vende 1 unidade de uma pilha sem remover o slot
inteiro; vender item que o servidor não tem ao vivo ainda sucede sem
remover nada, preservando comportamento anterior). Confirmados por
revert manual — 2 dos 3 falham sem o fix (o 3º não deveria mudar de
comportamento mesmo). Suíte de `test_craft_recycle_protocol.py`/
`test_session.py`/`test_server.py`/`test_instance_progression.py`/
`test_hotbar_bg_leak.py` rodada junto (241 testes), sem regressão.

**Validado pelo usuário em jogo** (12/08/2026): venda não volta mais no
relog, equipar arco/aljava depois de vender funciona normal. RESOLVIDO.

## 44. Bug antigo, nunca resolvido — HP de mob/torre "regenerava" sozinho durante o combate (12/08/2026)

Usuário relatou bug de longa data ("já reportei uma vez, mas você não
conseguiu resolver"): atacando um mob ou uma torre, a barra de HP
desce (dano aplicado) e depois volta a subir sozinha, como se o dano
tivesse sido desfeito. Pediu ajuda pra criar uma ferramenta de debug,
já que o projeto está mais organizado agora.

**Achado 1 — ferramenta de debug já existe, só nunca foi ligada**:
`debug/mob_combat_debug.py` (`RPG_DEBUG_MOB_COMBAT=1` → `debug/logs/
mob_combat.log`) já tem instrumentação PRONTA pra esse exato sintoma —
`server/world_server.py` (linha ~4679, gate `MCL.DBG_ENABLED`) já
compara o `current_hp` de cada mob contra o snapshot do tick anterior e
loga toda mutação "entre ticks" com o rótulo `"HP_DELTA"`, comentário
no código já nomeando isso de "Bug2 (regen/desaparecimento no golpe
final)" — confirma que esse sintoma já era suspeitado antes, só nunca
tinha sido caçado até o fim.

**Achado 2 — causa raiz concreta, com precedente no próprio histórico
do projeto**: `EnemyAISystem.update()` (`engine/world_systems.py`) tem
3 pontos onde o mob chega em `IDLE` vindo de perseguição. Um deles
(RETURNING→IDLE, linha ~3287) já foi corrigido em 09/07/2026
("Decisão 20.1", ver `historico/ARQUITETURA ONLINE HISTORICO.md`):
antes fazia `current_hp = max_hp` DIRETO no componente, o que nunca
passa por nenhum canal de broadcast de rede — o servidor muda o HP
real em silêncio, e o cliente só vê o salto pra cima na próxima vez que
qualquer coisa atualizar a barra (inclusive o PRÓPRIO golpe seguinte do
jogador, que aí parece "desfazer" o dano). A correção trocou isso por
regen gradual (1%/3s, mesmo canal de broadcast do HP5), com o comentário
explícito "HP não cura mais instantaneamente aqui".

O SEGUNDO ponto (mob desiste de perseguir por falha de pathfinding
estando já perto do próprio spawn, linha ~3893) é código quase idêntico
— mesmo comentário ("mesmo reset completo dos outros dois pontos de
chegada, por consistência") — mas nunca recebeu a mesma correção,
continuando com o `current_hp = max_hp` instantâneo e silencioso até
agora. É estruturalmente idêntico ao já corrigido, então explica o
mesmo sintoma: dispara quando o alcance de ataque do jogador (arco/
magia) é maior que `detect_radius` do mob (~8 tiles) e o pathfinding do
mob falha (obstáculo, aglomeração de mobs) perto do próprio spawn — o
mob "desiste" e cura tudo instantaneamente, sem avisar o cliente, no
meio do combate.

**Correção**: removida a linha `current_hp = max_hp` deste 2º ponto —
agora só entra em `IDLE`; a cura fica por conta do regen gradual
(`WorldServer._tick`, bloco "Regen de mob fora de combate"), que já
tem sync correto pro cliente. Mesma decisão de design já tomada em
09/07/2026 (Decisão 20.1), só aplicada ao ponto que ficou pra trás —
não é uma decisão nova.

**Escopo desta correção vs. o que fica em aberto**: durante a
investigação (via agente de pesquisa + verificação manual), também
achei um risco teórico separado — o despacho de rede por tick
(`server/session.py::_dispatch_tick_deltas`, chamado via
`asyncio.create_task` sem `await` e sem trava entre ticks sucessivos)
não tem NENHUMA garantia de ordem entre ticks consecutivos, e o campo
`seq` do protocolo (documentado como "detecção de ordem") não é lido
em nenhum lugar do cliente hoje — então, em teoria, um pacote de HP
mais NOVO poderia chegar antes de um mais VELHO em algum cenário raro
de concorrência. Não apliquei nenhuma mudança nisso agora: é uma
correção bem maior (mexe no pipeline central de rede), e a causa
encontrada acima já explica o sintoma sem precisar dessa teoria. Fica
registrado como possível causa residual SE o sintoma persistir depois
deste fix — nesse caso, ligar `RPG_DEBUG_MOB_COMBAT=1` e cruzar o log
`HP_DELTA` com timestamps de rede é o próximo passo natural.

**Teste**: `tests/test_server.py::TestRegressionBugs::
test_mob_sem_caminho_perto_do_spawn_nao_cura_instantaneo` (novo) —
mob CHASING sem caminho, player sai do raio de detecção mas o mob
continua perto do spawn (settle_threshold=1 tile) → confirma que o
mob vai pra IDLE mas o HP (já reduzido antes, simulando "já apanhou")
NÃO volta ao máximo. Confirmado por revert manual — falha (625≠312, a
cura instantânea) sem o fix. Suíte de `test_server.py`/
`test_map_filter.py`/`test_service_npcs.py`/`test_faction.py`/
`test_minions.py`/`test_towers.py`/`test_enemy_ai_perf.py` rodada
junta (241 testes), sem regressão.

**CORREÇÃO ao registrado acima (mesma sessão) — conclusão precipitada,
usuário pegou o erro**: o cenário reproduzido no teste (mob desiste de
perseguir → IDLE) NÃO bate com o sintoma real relatado. Usuário
esclareceu depois de eu apresentar isso como resolvido: o "rollback"
acontece DURANTE combate ativo (mob ainda perseguindo/atacando, não
desistindo), e é uma correção GRANDE, não um regen de 1%. Ou seja: o
fix acima é um bug real e válido (mantido — está correto por si só,
não foi revertido), mas muito provavelmente **não é** a causa do que
o usuário está vendo. Falhei em seguir a regra do CLAUDE.md de
"reproduzir o sintoma exato relatado antes de declarar corrigido" —
generalizei de "achei um bug real na mesma área" para "achei O bug",
sem checar se as circunstâncias batiam. Usuário perguntou diretamente
se o procedimento tinha sido seguido — resposta honesta: não, dessa
vez não.

**Nova investigação (mesma sessão, após a correção do usuário)**:
rastreei o caminho completo de dano (melee, flecha de auto-attack,
skills) e confirmei que o cliente NUNCA prediz/mostra dano antes da
confirmação do servidor — descarta a teoria literal do usuário
("cliente recebeu, servidor não"). Mas achei uma causa estrutural que
produz exatamente o mesmo efeito visual sem precisar de nenhuma
predição: `server/session.py::_on_tick` despacha as atualizações de
cada tick via `asyncio.create_task(self._dispatch_tick_deltas(deltas))`
— **fire-and-forget, sem `await`, sem trava/fila entre ticks
sucessivos**. Como a task de um tick não termina necessariamente antes
da task do PRÓXIMO tick começar (30 ticks/s, mais chance de overlap
com mais jogadores/mobs/ação na tela), duas atualizações de HP do
MESMO mob podem chegar ao cliente fora de ordem — um valor mais velho
(maior) sobrescrevendo um mais novo (menor) por puro atraso de rede,
sem NENHUM bug de cálculo do lado servidor. Bate melhor com todos os
detalhes: acontece em combate ativo, tamanho da correção varia
(depende de quanto dano rolou entre os dois pacotes fora de ordem),
afeta mob e torre igual (mecanismo genérico de despacho). O campo
`seq` do protocolo já existe pra detectar isso mas nunca é lido depois
de sair da fila de rede do cliente (`client/network.py`) — confirmado
via grep, nenhum handler consulta.

**Debug criado, aguardando reprodução do usuário** (não é mais
suposição — usuário pediu ferramenta pra confirmar antes de mexer
numa parte arriscada do código de rede): `debug/hp_rollback_debug.py`
(novo, `RPG_DEBUG_HP_ROLLBACK=1` → `debug/logs/hp_rollback.log`) —
compara `RemoteEntityMeta.hp` de cada mob/torre remoto contra o frame
anterior, 1x por frame (`client/online_mode_handlers.py::
_process_network`, chamada nova depois do loop de mensagens), loga
toda SUBIDA de HP junto com o `seq` da última mensagem processada.
Testado manualmente (World sintético, HP subindo → loga; descendo →
não loga) — sem reprodução real ainda. Zero custo quando a env var não
está setada (não roda em produção nem na suíte).

**Iteração 1 do debug — bug real na FERRAMENTA em si**: usuário setou a
env var com `set` no PowerShell, que NÃO define variável de ambiente
de verdade (é só alias de `Set-Variable`, cria uma variável do
PowerShell — pegadinha clássica, `cmd.exe` e PowerShell divergem
aqui). Corrigido: instrução certa é `$env:RPG_DEBUG_HP_ROLLBACK = "1"`.
Também achado nessa rodada: `HPR.check()` só abria o arquivo/escrevia
QUALQUER coisa no log dentro do branch de subida detectada — uma
sessão sem nenhum rollback não deixava rastro nenhum, sem jeito de
distinguir "ferramenta desligada" de "ferramenta ligada, nada
aconteceu". Corrigido: abre o arquivo e escreve linha `ALIVE` a cada
~10s desde o primeiro frame, e imprime `[HP_ROLLBACK_DEBUG] ATIVO` no
console assim que o módulo carrega com a env var setada — prova de
vida imediata e inequívoca.

**Iteração 2 — primeiro log real, achado forte**: usuário reproduziu
com a ferramenta corrigida. 3 padrões distintos no log:
- **Urso (mob comum)**: subidas de exatos +30 a cada ~3s até bater no
  teto — bate PERFEITO com o regen legítimo de mob fora de combate
  (1%/3s, Decisão 20.1). Não é bug, é sanity-check de que a ferramenta
  funciona certo.
- **Torre de Fogo (eid=79)**: subidas de +17, +45, +12, +12, +5, +5,
  +12, +32, +5, +5 — NUNCA o mesmo valor duas vezes. O regen legítimo
  de torre (`TowerSystem.update()`) é `max_hp × hp5` — pra uma
  `torre_de_fogo` real (`max_hp=2500, hp5=0.01`), isso é SEMPRE 25,
  toda vez que dispara (confirmado rodando `create_tower` de verdade).
  Magnitude variando descarta o mecanismo legítimo como explicação —
  forte candidato a ser o bug real (tamanho do salto bate com "quanto
  dano rolou entre 2 pacotes de rede fora de ordem", que varia por
  natureza).
- **Minions**: subidas também presentes, mas MinionSystem não tem
  NENHUM mecanismo de cura no código (grep confirma) — suspeito, mas
  pode ser falso positivo da PRÓPRIA ferramenta: minion nasce/morre
  o tempo todo, `world.remove_entity` recicla eid, e o rastreamento
  por eid local do debug pode comparar "HP baixo do minion que morreu"
  contra "HP inicial do minion novo que reaproveitou o mesmo eid" —
  não confiar nesse pedaço sem investigar a reciclagem de eid separado.

**Pendente**: usuário vai rodar de novo com os 2 debugs ligados ao
mesmo tempo — cliente (`RPG_DEBUG_HP_ROLLBACK=1`) E servidor
(`RPG_DEBUG_MOB_COMBAT=1`, "Bug2", já existia) — focando em bater numa
torre. Se o log do SERVIDOR nunca mostrar o `current_hp` dela subindo
nos mesmos momentos que o CLIENTE mostrou `HP_UP`, confirma de vez que
é 100% um problema de entrega/ordem de rede (servidor sempre esteve
certo) antes de mexer em qualquer código de despacho.

**Reprodução confirmada (mesmo dia)**: usuário rodou os 2 debugs juntos.
`debug/logs/mob_combat.log` (servidor, "Bug2") — **zero** entradas
`HP_DELTA` na sessão inteira (1216 linhas). `debug/logs/hp_rollback.log`
(cliente) — múltiplas subidas reais, magnitude variando pra uma MESMA
torre (`+17,+45,+12,+12,+5,+5,+12,+32,+5,+5` — nunca repete; o regen
legítimo de torre é `max_hp×hp5`, constante pra uma instância — pra
`torre_de_fogo` real, sempre 25, testado). **Confirmado: servidor nunca
errou a conta; só a entrega/exibição pro cliente está errada.**

**Consulta às referências reais do projeto** (corrigindo um erro de
processo — pesquisei antes uma fonte genérica de mercado em vez das
referências do projeto; ver seção nova `BENCHMARK_ARQUITETURA.md` §E.1
pro levantamento completo com citações): Veloren (referência de FORMA
designada pelo projeto pra esse tipo de pergunta) roda a sincronização
por tick dentro de um sistema SÍNCRONO da própria ECS
(`entity_sync::Sys`) — o tick N sempre termina de montar e mandar antes
do tick N+1 começar, por construção da arquitetura, nunca concorrente.
A entrega em si usa streams "reliable, ordered" da própria crate de
rede. Ou seja: Veloren nunca PRECISA de um mecanismo de "descartar dado
velho" porque a arquitetura nunca produz essa situação. Nosso projeto
(`server/session.py::_on_tick` → `asyncio.create_task(self.
_dispatch_tick_deltas(deltas))`, sem `await`, sem fila entre ticks)
permite exatamente o que a referência evita — confirmado como achado
arquitetural real (`BENCHMARK_ARQUITETURA.md` §E.1), não só um bug
isolado.

**Duas correções possíveis, escopo bem diferente — decisão do usuário,
não decidida aqui sozinha**:
- **(a) Raiz, alinhada com a referência**: serializar
  `_dispatch_tick_deltas` (garantir que um tick termine antes do
  próximo começar) — mexe no núcleo do despacho de rede, usado por
  quase toda mensagem do jogo, maior superfície de risco.
- **(b) Sintoma, isolada**: cliente descarta atualização de HP mais
  velha que a última aplicada, usando um número de TICK novo que o
  servidor passa a mandar (não `seq`, que mede ordem de ENVIO, não de
  tick — achado ao desenhar: uma primeira versão desta ideia reusando
  `seq` estava errada, pois uma task de tick mais velho pode ganhar a
  trava de envio DEPOIS de uma mais nova, saindo com `seq` maior).
  Aditiva, isolada, não fecha o gap arquitetural de verdade.

**Decisão do usuário**: opção (c) — coalescência de despacho, meio-termo
entre (a) e (b) que o usuário pediu pra eu desenhar depois de comparar
custo/coesão/eficiência das duas opções originais. Nunca mais de 1
despacho (`_dispatch_tick_deltas`) em andamento por vez; um tick que
chega enquanto o anterior ainda despacha tem seu `deltas` MESCLADO num
acumulador (nunca descartado nem despachado em paralelo — `_collect_deltas()`
limpa os buffers de origem todo tick incondicionalmente, então pular
sem mesclar perderia golpes/spawns pra sempre) e sai INTEIRO no próximo
despacho livre, assim que a vaga abre (não espera o próximo tick real).
Fecha o gap architectural pra TODO tipo de dado que passa por esse
pipeline (não só HP), sem exigir que o loop de tick trave esperando
envio de rede (risco de performance que a opção (a) pura teria).

**Implementação** (`server/session.py`): `SessionManager.__init__` ganha
`_dispatch_in_flight: bool` + `_pending_merged_deltas: dict | None`.
`_on_tick` não dispara mais `asyncio.create_task` incondicionalmente —
mescla sempre no acumulador (`_merge_deltas_into`, 3 regras por tipo de
campo: listas de evento concatenam preservando ordem cronológica;
`despawned_pos` é união de dict; `effects`/`mob_effects` são fotos do
estado atual, substituem sempre pela mais nova, nunca concatenam) e só
cria uma task nova se não houver nenhuma em andamento. `_run_dispatch`
(novo, wrapper de `_dispatch_tick_deltas`) usa `try/finally` — SEMPRE
destrava `_dispatch_in_flight`, mesmo se `_dispatch_tick_deltas` lançar
exceção, e encadeia automaticamente o próximo despacho se algo
acumulou durante o despacho que acabou de terminar.

**Testes** (`tests/test_session.py::TestDispatchSerializacaoDeTicks`,
5 novos): tick único ainda despacha normal (sem regressão no caso
comum); 2 ticks concorrentes mesclam em vez de descartar/competir;
3 ticks acumulados durante 1 despacho concatenam eventos em ordem
cronológica; `despawned_pos`/`effects` seguem as regras de
união/substituição corretas; exceção dentro do despacho não trava
`_dispatch_in_flight` pra sempre (chamando `_run_dispatch` direto,
simulando o estado que `_on_tick` deixaria antes de criar a task).
Confirmados por revert manual (reverti só a mecânica nova em
`server/session.py`, mantendo o resto do arquivo intacto — os 5 testes
falham/erroram contra o código antigo, restaurado depois).

**Achado extra durante o revert-to-confirm — regressão real em 4
testes pré-existentes, causa raiz numa peça de teste, não na lógica de
produção**: `tests.helpers.run_ticks()` (usado por `asyncSetUp` de
vários testes pra "adiantar" o mundo antes do teste de verdade) roda
os ticks dentro de um `asyncio.run()` PRÓPRIO e descartável, que nunca
dava nenhuma chance de execução às tasks de despacho criadas por
`_on_tick` — a função síncrona terminava e o loop era destruído antes
de qualquer task rodar. Sob o código antigo isso não importava (cada
tick criava sua PRÓPRIA task independente; descartar uma sem rodar não
afetava as seguintes). Com `_dispatch_in_flight` sendo estado ÚNICO e
persistente no `SessionManager`, uma task descartada sem nunca rodar
nem uma vez deixava a flag travada em `True` PARA SEMPRE — como é o
MESMO `self.mgr` usado depois pelo teste de verdade, todo despacho
seguinte ficava preso em "acumulando", nunca disparando de fato.
Rastreado com print temporário linha a linha (removido depois).
**Correção** (`tests/helpers.py::run_ticks`): o `_run()` interno agora
faz `await asyncio.sleep(0)` a cada tick (deixa a task do tick rodar de
verdade) e, ao final, drena qualquer despacho ainda encadeado (chain de
merges) antes do `asyncio.run()` fechar o loop — orçamento limitado
(`n + 10` voltas) pra nunca travar em loop infinito se algo realmente
quebrar. Não toca em nenhuma lógica de produção; é 100% um ajuste do
helper de teste pra não descartar mais as próprias tasks. Confirmado
pelo usuário como o caminho certo (pergunta direta antes de aplicar,
opção "corrigir o helper" vs. "ver o código antes" — usuário escolheu
corrigir).

**Suíte completa rodada** (926 testes, `py -3.10 -m unittest discover
-s tests`) — 0 regressões depois do fix de `run_ticks`.

**CORREÇÃO ao registrado acima (mesma sessão) — usuário reproduziu de
novo, sintoma PERSISTE mesmo com a opção (c) aplicada**: novo log do
usuário (`debug/logs/hp_rollback.log`, sessão `2026-08-13 20:11:13`,
DEPOIS do fix) mostra os mesmos saltos de HP com magnitude variável em
Torre de Fogo/Torre de Flechas/Minion Arqueiro. Cruzado de novo com
`debug/logs/mob_combat.log` (Bug2) da MESMA janela — zero `HP_DELTA`,
confirmando outra vez que o servidor nunca erra a conta. A opção (c)
fechou um gap real (dispatch por tick sem serialização — ver acima),
mas NÃO é a causa do sintoma que o usuário está vendo. Mesmo erro de
generalizar "achei um bug real na área" para "achei O bug" da tentativa
anterior — desta vez sem declarar "corrigido" antes de reproduzir de
verdade (usuário só perguntou "não funcionou, consegue ver o log?").

**Nova causa raiz encontrada (leitura de código, não suposição) —
CLIENTE, não rede**: `ui/spell_system.py::PlayerProjectileSystem`
mantém `pending_arrow_impacts: dict[int, list]` (linha 912) — uma FILA
POR ALVO (chave = eid LOCAL do alvo), onde QUALQUER flecha (do player
local, de outro player remoto, ou de mob/minion — 3 pontos de
`setdefault` em `client/remote_entity_handlers.py:359/505/588`)
empilha seu resultado (`outcome`/`damage`/`hp_after`) ao ser recebida
via COMBAT_RESULT. O consumo (`_on_hit`, `ui/spell_system.py:1278-1340`)
faz `_pending.pop(0)` — tira o PRIMEIRO da fila — no momento em que
QUALQUER flecha chega visualmente ao alvo, sem checar se aquela entrada
realmente pertence àquele projétil/atacante específico. `deferred_hp_
updates.append((eid, hp_after, hp_max))` (linha 1340) aplica o
`hp_after` dessa entrada errada ao `RemoteEntityMeta.hp` do alvo.

Isso é uma corrida CLIENTE-SIDE, 100% desacoplada de rede/tick: o tempo
de voo de uma flecha depende da distância real em pixels entre atacante
e alvo NO MOMENTO do disparo. Pra uma torre (parada) sendo atacada por
VÁRIOS atacantes (minions/players) a distâncias diferentes — ou até um
único atacante que varia de posição entre disparos — as flechas podem
chegar visualmente numa ordem DIFERENTE da ordem em que o servidor
confirmou os golpes. A fila `pop(0)` entrega a entrada errada pra quem
chegou primeiro visualmente, e se essa entrada for de um golpe MAIS
ANTIGO (hp_after maior), a barra "volta" — sem nenhum problema de
cálculo do servidor nem de entrega de rede, exatamente como os logs
mostram. Bate com todos os detalhes: só afeta ataques físicos com
flecha (arqueiro/torre de flechas — mobs de magia usam outro caminho,
`apply_magic_damage_shared`, sem essa fila), pior quanto mais atacantes
simultâneos (bate com torres, que levam dano de vários minions/players
ao mesmo tempo), varia em magnitude (depende de qual entrada errada foi
entregue).

**Correção ao "só arco" (mesma sessão) — usuário pediu não generalizar
de dados enviesados**: os testes que geraram os logs foram todos feitos
com personagem arqueiro, então a suspeita de "só flecha" vinha de dado
enviesado, não de análise completa. Reli magia e corpo-a-corpo: ambos
aplicam `hp_after` IMEDIATAMENTE ao receber a confirmação do servidor
(sem fila, sem espera por evento visual) — confirmado por leitura de
código, não suposição. Só o auto-attack físico de flecha usa fila
diferida. Se o usuário reproduzir o mesmo sintoma com dano só de magia/
corpo-a-corpo no futuro, esta conclusão está errada e precisa ser
revista.

**Pesquisa Veloren** (`common/systems/src/projectile.rs`, via GitHub
mirror): lá o projétil é uma entidade ECS sincronizada de verdade —
o servidor detecta a colisão no próprio momento da simulação física e o
`HealthChangeEvent` já nasce amarrado ao UID do alvo (não "quem chegou
primeiro pega o próximo da fila"). Nosso projeto não replica projétil
como entidade de rede — a flecha do cliente é 100% cosmética, o
resultado já vem pronto do servidor via COMBAT_RESULT antes da flecha
existir. Decisão do usuário: alinhar com a referência mesmo assim —
amarrar cada flecha ao SEU resultado por identidade, não pela ordem de
chegada.

**Implementado**: `PlayerProjectile.deferred_result` (`engine/
components.py`, campo que já existia, usado até então só parcialmente
por uma skill — Fatiador de Corpos) virou o mecanismo único. Cada uma
das 3 flechas de auto-attack (alvo mob/torre, alvo player local, alvo
player remoto — `client/remote_entity_handlers.py::_apply_combat_result`)
agora prende o resultado do servidor DIRETO na flecha que acabou de
nascer (`_spawn_archer_auto_arrow` passou a retornar o eid criado),
nunca mais em `pending_arrow_impacts` (fila por alvo, removida —
`ui/spell_system.py`, 2 pontos de consumo: o "peek" de outcome antes de
`_on_hit` pra decidir se a flecha desvia, e o `_on_hit` em si).

**Testes** (`tests/test_client_ui.py`, 2 novos):
`test_flecha_carrega_o_proprio_resultado_nunca_fila_por_alvo` (2 golpes
no mesmo alvo → 2 flechas, cada uma com seu próprio hp_after) e
`test_2_flechas_no_mesmo_alvo_fora_de_ordem_nao_trocam_resultado` (o
diferencial de verdade: chama `_on_hit` na ordem VISUAL invertida —
flecha mais nova chegando primeiro — e confirma que cada uma aplica o
hp_after DELA, nunca o da outra). Confirmados por revert manual: ambos
falham contra o código antigo (com `AttributeError` — a fixture nova
nem tem mais `_player_proj_system`, prova de que o mecanismo mudou de
verdade). Suíte completa (`pytest tests/`, 1122 testes) sem regressão.

**Limite honesto do que este fix cobre** (achado durante a pesquisa,
não escondido): amarrar o resultado à flecha certa elimina a troca de
valor ENTRE flechas — isso nunca mais acontece. Mas não elimina 100% a
possibilidade de uma flecha VISUALMENTE mais rápida (atacante mais
perto) chegar antes de uma flecha VISUALMENTE mais lenta (atacante mais
longe) disparada ANTES dela — nesse cenário específico (multi-atacante
a distâncias bem diferentes), a barra pode mostrar o HP mais baixo (da
flecha rápida) antes do HP mais alto (da flecha lenta, golpe mais
antigo), por uma fração de segundo — cada valor aplicado é o CORRETO
da flecha certa, mas a ORDEM de chegada visual pode não bater com a
ordem cronológica do servidor. Veloren evita isso porque a barra de HP
lá nunca depende de qual projétil chega primeiro — sincroniza via ECS
normal, sempre em ordem, e o projétil é só visual por cima. Replicar
isso aqui exigiria desacoplar a atualização de HP do momento de impacto
visual da flecha (a sugestão original do usuário) — não implementado
agora, registrado como possível 2ª camada se o sintoma persistir com
múltiplos atacantes a distâncias muito diferentes do mesmo alvo.

**4ª causa encontrada (mesma sessão) — usuário propôs a pergunta certa**:
"o HP do mob é pego quando um ataque do player SAI, mas se outro ataque
chega antes no mesmo alvo e reduz o HP, quando o dano do player chega
o HP não restaura (e some) o dano que já tinha chegado?" — SIM,
exatamente isso. Mesmo com a causa (3) corrigida (cada flecha com o
resultado certo, nunca trocado), o HP dela continuava só sendo
aplicado no MOMENTO em que a flecha chega visualmente
(`deferred_hp_updates`, consumido em `game.py`) — podendo levar vários
frames dependendo da distância. Nesse intervalo, QUALQUER outro ataque
(magia, corpo-a-corpo, outra flecha já corrigida) contra o MESMO alvo
aplica o HP dele NA HORA (branch imediato, nunca teve esse problema).
Quando a flecha atrasada finalmente chega e aplica o `hp_after` DELA
— correto pra ela, mas calculado pelo servidor num instante ANTERIOR —
ela sobrescreve o HP mais novo aplicado enquanto ela voava, apagando
esse progresso. Sintoma bate 100%: "a barra volta" durante combate
ativo, com magnitude variável (depende de quanto dano rolou entre o
disparo da flecha e ela chegar).

**Pesquisa antes de implementar** (Veloren, `common/systems/src/
projectile.rs`, já consultado antes nesta sessão para a causa (3), +
busca geral na web sobre sync de HP em netcode): confirma o mesmo
princípio — mudança de vida deveria sempre sincronizar pelo canal de
estado normal, autoritativo, assim que o servidor confirma, NUNCA
esperando um evento visual local (a barra de vida em Veloren nunca
depende de qual projétil chega primeiro). Busca geral corrobora:
"health changes are typically part of entity net state... informed by
delta snapshots" — nunca amarradas à chegada visual de um projétil
específico.

**Fix**: HP de flecha passa a ser aplicado IMEDIATAMENTE na confirmação
do servidor (`client/remote_entity_handlers.py::_apply_combat_result`,
removida a exceção `not _is_archer_arrow` do branch imediato — agora
idêntico a magia/corpo-a-corpo). Só FLT/som de impacto (e a decisão de
desviar em caso de erro) continuam esperando a flecha chegar
visualmente, via `PlayerProjectile.deferred_result` (mecanismo da
causa (3), mantido — ainda necessário pra decidir qual texto/som mostra
em qual flecha). Mecanismo antigo `deferred_hp_updates`
(`ui/spell_system.py` + drain em `game.py`) removido por inteiro —
ficou 100% morto depois da mudança.

**Testes** (`tests/test_client_ui.py`, reescritos): `test_flecha_
carrega_o_proprio_resultado_nunca_fila_por_alvo` (atualizado — confirma
que `deferred_result` não carrega mais `hp_after`, e que o HP já
reflete o 2º golpe IMEDIATAMENTE, antes de qualquer flecha chegar) e
`test_hp_de_flecha_atrasada_nao_sobrescreve_dano_mais_novo_de_outra_
fonte` (novo — o diferencial de verdade: aplica um golpe de flecha,
DEPOIS um golpe de outra fonte que reduz mais o HP, SÓ DEPOIS chama
`_on_hit` da flecha atrasada, confirma que o HP não volta). Confirmados
por revert manual — reconstruí a pipeline antiga completa (entry com
hp_after → `_on_hit` → `deferred_hp_updates` → drain manual simulando
`game.py`) num script à parte e reproduzi o bug exato (900 → 980,
rollback) fora do pytest, já que o mecanismo antigo foi removido do
código de produção; os 2 testes de pytest também falham/ficam
vácuos contra o código antigo quando a única linha determinante
(`not _is_archer_arrow`) é revertida. Suíte completa (1122 testes)
sem regressão (1 falha isolada em `test_perf_log_improvements.py` foi
confirmada como flaky — passa sozinho e numa 2ª rodada completa,
sensível a carga do sistema, sem relação com esta mudança).

**Validado pelo usuário em jogo** (13/08/2026): "Certo, agora sim
funcionou." RESOLVIDO — as 3 causas reais (dispatch por tick sem
serialização, fila de flecha por alvo, HP de flecha diferido pro
impacto visual) estão corrigidas e confirmadas em jogo.

## 45. (movido) Barra de XP dentro da BG mostrava a XP de fora da instância — mesmo gap de sync já fechado antes pra level/atributos/talento (13/08/2026) — ver HISTORICO_BUGS.md §7

## 46. (movido) Minimapa em tela cheia da BG — clique parou de mover o personagem + minion do mesmo tamanho que torre (13/08/2026) — ver HISTORICO_BUGS.md §8

## 47. (movido) Stealth de bush estilo MOBA na BG (13/08/2026, feature nova) — ver HISTORICO_FEATURES.md §3

## 48. (movido) Monstros de jungle estilo MOBA — normal + boss (13/08/2026, retomado de sessões anteriores) — ver HISTORICO_FEATURES.md §4

## 49. (movido) Bush/árvore/pedra não bloqueavam linha de visão (LOS) como parede (13/08/2026) — ver HISTORICO_BUGS.md §9

## 50. (movido) Fog assimétrico ao ficar no mesmo tile de bush (13/08/2026, achado validando §49) — ver HISTORICO_BUGS.md §10

## 51. (movido) Bushes "separadas" no mapa se fundem num blob só de qualquer forma (13/08/2026, esclarecimento — sem bug de código) — ver HISTORICO_BUGS.md §11

## 52. (movido) Pegada de visão da bush vira "só a base" — corrige a fusão de bushes separadas (13/08/2026) — ver HISTORICO_BUGS.md §12

## 53. Fog "presa" (desatualizada) na tela principal perto de bush pequena — cache de desenho, não de cálculo (13/08/2026)

Usuário validou §52 em jogo e achou mais um problema: bush GRANDE
funciona bem, mas bush de 32×32 (1 tile) faz a fog ficar "presa" — um
lado que devia estar revelado continua escuro na TELA PRINCIPAL, mas o
MINIMAPA (mesmo dado, `FogOfWar.visible`/`explored`) mostra correto.
Usuário pediu explicitamente pra discutir antes de eu tocar em código
(regra restaurada em §51).

**Hipótese descartada por dado real, não suposição**: cheguei a
suspeitar que `b17`/`b18` (que eu tinha "corrigido" de 64×64 pra 32×32
numa sessão anterior, §52) fossem a causa — usuário confirmou que
64×64 É o tamanho certo desses 2 tiles, e que minha edição anterior
NUNCA chegou a aplicar de verdade (falha silenciosa minha, relatada
como concluída sem checar de novo o arquivo — mesma classe de erro que
a regra "corrigido exige reproduzir o sintoma" do CLAUDE.md tenta
evitar). Não é a causa deste bug; descartado com uma pergunta direta ao
usuário antes de seguir por esse caminho.

**Diagnóstico real, confirmado com o usuário ANTES de propor
qualquer fix**: perguntei se a fog errada se autocorrige andando o
suficiente pra câmera rolar uma tile inteira — usuário confirmou que
SIM. Isso descarta bug na CONTA de visibilidade (`local_vision_blob`/
`compute_fov`, tudo do §50/§52 — se fosse erro de cálculo, andar mais
não corrigiria sozinho) e aponta pra bug de CACHE DE DESENHO.

**Causa raiz**: `ui/systems.py::TileRenderSystem.render_fog()` cacheia
o overlay de fog numa Surface que só reconstruía quando a JANELA DE
TILES DA CÂMERA mudava (`tile_ox = camera_offset_x // TILE_SIZE`),
nunca quando `FogOfWar.visible`/`explored` mudavam por si só (jogador
mudou de tile, câmera ainda não cruzou fronteira). É a MESMA classe de
bug já documentada no CLAUDE.md pro cache de TERRENO dessa mesma
classe (`TileRenderSystem.invalidate_cache()`) — só que ninguém tinha
conectado o `FogSystem` a esse cache específico. Bush grande "funciona"
só porque a sombra que projeta muda mais devagar — a janela de
desatualização é a MESMA, só bem menos perceptível; bush de 1 tile
muda a visão rápido em poucos passos, tornando o atraso bem visível.

**Desenho**: chamar `invalidate_cache()` direto de dentro do
`FogSystem` violaria a regra do próprio projeto ("um System nunca
chama outro System diretamente"). Fix alinhado ao ECS: contador de
versão NO COMPONENTE (`FogOfWar.version`, incrementado por
`FogSystem.update` sempre que recalcula `visible`/`explored`), lido
por `TileRenderSystem` a cada frame — comunicação via componente
(estado compartilhado), não chamada direta entre Systems.

**Implementado**:
- `engine/components.py::FogOfWar.version` (novo, int, começa em 0).
- `ui/systems.py::FogSystem.update` — `fog.version += 1` no fim do
  bloco que já recalcula `visible`/`explored`.
- `ui/systems.py::TileRenderSystem` — `self._fog_cache_version` novo;
  condição de rebuild do overlay ganhou `or fog_comp.version !=
  self._fog_cache_version`; `invalidate_cache()` também reseta esse
  campo (simetria, garante rebuild completo após troca de mapa).

**Testes** (`tests/test_fog_render_cache.py`, 3 novos): 1º render grava
a versão atual; versão muda SEM a câmera mudar de tile ainda assim
reconstrói (prova que é a versão, não a câmera, que disparou); sem
nenhuma mudança, uma 2ª chamada NÃO reconstrói de novo (espiado via
`pygame.draw.rect`, já que `Surface.fill` é atributo read-only de tipo
C e não dá pra mockar direto). Confirmado por revert manual (comentada
a condição de versão): o teste 2 falha exatamente como esperado; os
outros 2 continuam passando por não dependerem do fix. Suíte completa
(`pytest tests/ -q`) sem regressão.

**Pendente**: usuário validar em jogo — andar até uma bush de 32×32
pequena e confirmar que a fog revela certo sem precisar andar mais pra
"destravar".

## 54. (movido) Linha de visão de terreno entre times — nunca existia no servidor (13/08/2026) — ver HISTORICO_FEATURES.md §5

## 55. (movido) Bush: visão compartilhada dentro da mesma bush + "atacar revela" (LoL-style) (13/08/2026) — ver HISTORICO_FEATURES.md §6

## 56. (movido) Sair da bush não revalidava a própria visão — só o alvo "avisando" destravava (13/08/2026) — ver HISTORICO_BUGS.md §13

## 57. (movido) Visão de terreno vira universal (todo o jogo, toda entidade) + bush usa isenção da PRÓPRIA posição do viewer (13/08/2026) — ver HISTORICO_FEATURES.md §7

## 58. (movido) Torre/minion do próprio time sempre visível + varredura de AOI ganha o `_can_see` que faltava (13/08/2026) — ver HISTORICO_FEATURES.md §8

## 59. (movido) Visão de time vira UNIÃO de fontes independentes, estilo LoL (16/08/2026) — ver HISTORICO_FEATURES.md §9

## 60. (movido) Bot 100% servidor pra preencher a fila 2v2 da BG (16/08/2026, feature nova) — ver HISTORICO_FEATURES.md §10

## 61. Auto-attack PvE/PvP unificado — munição, LOS, fórmula de dano e Raiva/Punho no Queixo (16/08/2026)

Usuário reportou (e confirmou empiricamente com 2 contas reais, duelo
de verdade — testou o bot novo do §60, achou o bug testando contra
ele): arqueiro com ARCO equipado mas SEM aljava ainda atirava flecha
contra outro JOGADOR. Sem arco nenhum, cai pra melee corretamente —
isso já funcionava.

**Causa raiz**: NUNCA foi "faltou uma condição" — existiam 2 funções
paralelas pro auto-attack (`server/combat_processor.py`):
`_process_player_attacks` (alvo=mob) checava tudo (perseguindo,
alcance real do arco, aljava com flecha, linha de visão, cooldown,
fórmula própria de dano à distância via `_server_apply_ranged_
physical`); `_process_pvp_attack` (alvo=player) era uma implementação
mais pobre — só perseguindo+alcance+cooldown, SEM munição, SEM LOS,
sempre usando `deal_damage` genérico em vez da fórmula de arco. Cada
checagem nova foi adicionada só no lado PvE ao longo do tempo e nunca
replicada no PvP — os 2 caminhos foram divergindo em silêncio.
Usuário perguntou, com razão: o certo não seria ter 1 lugar só que
checa as prioridades de QUALQUER ataque, independente do alvo?

**Pesquisa de referência** (AzerothCore, antes de desenhar — pedido
explícito do usuário, incluindo sobre onde Raiva/combo deveriam
morar): `Unit::RewardRage()` (geração de Raiva do Guerreiro) vive na
classe BASE `Unit` — `Player` e `Creature` (mob) herdam do MESMO
`Unit`, chamada de dentro do caminho ÚNICO de dano corpo-a-corpo; não
existe branch PvE-only pra recurso/combo na referência. Confirmou que
a unificação era o desenho certo, e que Raiva/Punho no Queixo deveriam
entrar no MESMO caminho compartilhado — não um sistema de evento
separado nem uma condicional à parte.

**Fix**: novo método único `CombatProcessorMixin._resolve_auto_attack`
— TUDO que decide se um auto-attack acontece e o que ele causa,
independente do alvo ser mob ou player: `is_pursuing` (unificado pra
SEMPRE exigido — antes só ranged no PvE, sempre no PvP; achado
investigando, não o bug relatado, sinalizado no plano e aprovado),
detecção ranged (classe + arco de verdade equipado) + alcance real do
arco, munição (aljava com flecha), linha de visão (`EnemyAISystem.
_has_line_of_sight`), cooldown, despacho de dano (`_server_apply_
ranged_physical` se ranged, `deal_damage` se melee), `enter_combat` +
Raiva do Guerreiro + contador de Punho no Queixo do Cavaleiro — os 2
últimos MOVIDOS de dentro do braço PvE pra cá, sem condicionar a alvo
ser mob. Também trouxe pro caminho compartilhado a precisão de
posição (`server_tile_x/y` com fallback pra `current_tile`) que antes
só o PvP tinha — sem risco pro lado mob (nunca popula esse campo, cai
no mesmo fallback de sempre).

`_process_player_attacks` (braço PvE) e `_process_pvp_attack`
encolheram pra só o que É específico de alvo: PvE mantém o evento de
quest `"auto_attack_hit"` (só faz sentido contra mob, que tem
`EntityIdentity`) e delega morte ao `ServerDeathHandler` genérico; PvP
mantém sync de HP da VÍTIMA (`STATS_UPDATE`) e chama
`_handle_player_death` direto (não o handler genérico, que é pra mob).

**Achado extra durante a implementação**: `_server_apply_ranged_
physical` já tinha um guard de LOS PRÓPRIO, interno (rede de
segurança pras skills que reusam essa função, ex. Tiro Repulsivo) —
como o `_process_pvp_attack` ANTIGO nunca chamava essa função (só
`deal_damage`), esse guard nunca protegia PvP nenhum; a unificação já
ativa ele de graça. Isso também significa que HP/flecha inalterados
sozinhos não provam qual dos 2 guards (o novo, em
`_resolve_auto_attack`, ou o antigo, interno) bloqueou — o sinal que
só o guard NOVO produz é retornar ANTES de consumir o cooldown (o
guard interno só roda DEPOIS do cooldown já gasto). Teste de parede
ajustado pra checar isso explicitamente, não só HP/flecha.

**Testes**: `tests/test_pvp_auto_attack.py` (novo, 6 testes) — arqueiro
sem aljava não ataca player (bug relatado); arqueiro com aljava ataca e
consome flecha; parede real bloqueia (LOS, checando timer de cooldown
intacto — ver achado acima); sem arco cai pra melee; Guerreiro ganha
Raiva atacando player; Cavaleiro ganha carga de Punho no Queixo
atacando player 3x. Revert manual: comentar a checagem de munição
derruba exatamente 1 teste (o do bug relatado); comentar a checagem de
LOS derruba exatamente 1 teste (o da parede), mais nenhum outro.

Refatoração expôs uma dependência oculta: `is_pursuing` virar SEMPRE
exigido quebrou 8 testes pré-existentes (`tests/test_server.py`,
`tests/test_session.py`) que chamavam `set_player_target` direto sem
setar `is_pursuing` — funcionavam por acaso porque melee nunca exigia
isso antes. Corrigido com novo helper `tests/helpers.py::
engage_target` (mesma sequência que `_handle_auto_attack` já faz por
trás de um AUTO_ATTACK real), usado nos 8 call sites afetados.

Suíte completa (`pytest tests/ -q`) — 1234 passed, zero regressão.

**Pendente**: usuário repetir o duelo de 2 contas que já fez — arqueiro
sem aljava não deve mais atirar em PvP; com aljava, atira e a flecha
some da aljava; Raiva/Punho no Queixo sobem batendo em outro player
também.

## 62. (movido) Gold/XP de minion + kill credit/assist de player, estilo LoL (16/08/2026) — ver HISTORICO_FEATURES.md §11

## 63. (movido) Investigação pós-§62: janela de kill (sem bug), bot travando em torre, ImportError no shutdown (16/08/2026) — ver HISTORICO_BUGS.md §14

## 64. (movido) Player remoto nunca tinha Faction real (minimap + TAB/ESPAÇO selecionando aliado) + rota de bot desincronizando (16/08/2026) — ver HISTORICO_BUGS.md §15

## 65. (movido) Camps de jungle da BG populados a partir do concept art + level dinâmico (16/08/2026, feature nova) — ver HISTORICO_FEATURES.md §12

## 66. (movido) Jungle sem drop físico + puxão de grupo estilo WoW/LoL (16/08/2026, feature nova, planejada com o usuário via EnterPlanMode) — ver HISTORICO_FEATURES.md §13

## 67. (movido) Mob de jungle mostrava o mob_key cru como nome (16/08/2026) — ver HISTORICO_BUGS.md §16

## 68. (movido) Bot travava o tick com TypeError ao empurrar lane com curva (17/08/2026) — ver HISTORICO_BUGS.md §17

## 69. (movido) Bot AI v2 — lane sorteada, compra inicial, empurrar lane igual minion, dive gate (17/08/2026, feature nova, planejada com o usuário via EnterPlanMode) — ver HISTORICO_FEATURES.md §14

## 70. (movido) Loja de instância vende o catálogo inteiro, filtrado por classe, preço por raridade (25/08/2026, feature nova, planejada com o usuário via EnterPlanMode) — ver HISTORICO_FEATURES.md §15

## 71. (movido) Bot travando perto de torre + retentando skill em cooldown pra sempre (25/08/2026, 2 bugs reais de playtest, investigados por reprodução ao vivo) — ver HISTORICO_BUGS.md §18

## 72. (movido) Bot travando perto da PRÓPRIA torre — pé de colisão de 2 tiles invisível pro pathfinding (25/08/2026) — ver HISTORICO_BUGS.md §19

## 73. (movido) Preferência de slot de skill unificada dentro/fora da BG (25/08/2026) — ver HISTORICO_BUGS.md §20

## 74. (movido) 3 correções não relacionadas — Punho no Queixo roubando slot, TAB sem neutro, SPACE reselecionando (25/08/2026) — ver HISTORICO_BUGS.md §21

## 75. (movido) Bots de BG: guerreiro para longe demais, mago sem dano/"sem cooldown" (26/08/2026) — ver HISTORICO_BUGS.md §22

## 76. (movido) Crash ao treinar skill — `TrainerSystem` sem `_preferred_slot_for_skill` (26/08/2026) — ver HISTORICO_BUGS.md §23

## 77. (movido) Zumbi de fila BG + botão nativo de maximizar sobrescreve "Janela" (26/08/2026) — ver HISTORICO_BUGS.md §24

## 78. (movido) Catálogo de tiles: 1 cor por arquivo inteiro (grama/terra/pedra indistinguíveis no minimapa) (26/08/2026) — ver HISTORICO_BUGS.md §25

## 79. (movido) Editor de mapa (F10): pincel aleatório por grupo (26/08/2026) — ver HISTORICO_FEATURES.md §16

## 80. (movido) Migração de conteúdo: árvore/arbusto default + "TX Plant" antiga → "TX Village Plant" (26/08/2026) — ver HISTORICO_FEATURES.md §17

## 81. (movido) Pincel inteligente de terreno (grama/terra/piso, transição automática) — God Mode (26/08/2026) — ver HISTORICO_FEATURES.md §18

## 82. (movido) Árvore usava a copa inteira (sprite completo) pra bloquear visão — corrigido pra usar só a base (27/08/2026) — ver HISTORICO_BUGS.md §26

## 83. (movido) Dano de DoT de habilidade de mob escalava linear demais (urso level 24 batendo 96/tick) (28/08/2026) — ver HISTORICO_BUGS.md §27

## 84. (movido) Playtest BG: skill "Alvo inválido"/"Fora de alcance" depois de Interceptar OU seleção normal de alvo (RESOLVIDO 30/08/2026) + bot com HUD de morto às vezes (debug adicionado) (28/08/2026) — ver HISTORICO_BUGS.md §28

## 85. Colisão fina (16px) pro passo manual do jogador, sem tocar pathfinding/mapa/rede (30/08/2026)

Usuário pediu: muitos props decorativos do catálogo (ex: família "TX
Village Props with Shadow", `engine/tileset.py`) já têm `collision_rect`
autoral em sub-tile (ex: `("076", 608, 1216, 32, 32, (0, 16, 16, 16))` —
só o quadrante inferior-esquerdo de 16×16 é sólido de verdade), mas o
jogo sempre bloqueava o tile de 32×32 inteiro, descartando essa
precisão. Pedido explícito: resolver só pro passo MANUAL (WASD) do
jogador, sem mudar `TILE_SIZE` global, sem re-desenhar mapas, sem tocar
pathfinding/IA de mob/bot (que continuam 100% tile-a-tile de 32px), e
sem mudar o formato do protocolo de rede se possível.

**Planejado via EnterPlanMode** (2 rodadas de investigação — Explore
agent auditou consumidores de `current_tile_x/y`/mecanismo de correção
de MOVE; Plan agent validou o mecanismo linha por linha e achou 2 gaps
reais antes da implementação: (1) o gate de segurança pra objetos
multi-tile precisava ser explícito, não implícito; (2) a correção de
dessincronia do cliente recentralizava a `Position` incondicionalmente,
apagando um meio-passo legítimo). Pesquisa externa (Zelda ALTTP
"feelers", padrão clássico de AABB contínuo separado do tile grid,
sub-grid do Zelda NES) confirmou a técnica escolhida (meio-passo como
FALLBACK, mantendo o "encaixe" de grid) como a mais alinhada ao pedido.

**Mecanismo**: `TileValidationSystem.is_tile_walkable` (o checador de
sempre, 32px, boolean por tile) continua INTOCADO — usado por
pathfinding/mob/bot exatamente como antes. Um NOVO fallback, só pro
passo manual, entra em cascata:
1. `engine/tileset.py::get_solid_rect(tile)` — retângulo sólido REAL em
   pixels (sem arredondar), reusando `_rect_to_tile_offsets` como GATE:
   se a pegada do objeto (já calculada por essa função) tiver mais de 1
   tile, devolve bloqueio de tile inteiro sempre — objetos multi-tile
   (torre, árvore grande) nunca ganham precisão fina, porque o
   `collision_rect` de um `TileType` compartilhado por vários offsets é
   relativo ao SPRITE inteiro, não a qual offset específico está sendo
   consultado (matematicamente sem sentido tentar recortar sem esse gate).
2. `TileValidationSystem.is_tile_walkable_fine()` — AABB entre a caixa
   do jogador (`Collider`, 24×24 — componente que já existia mas tinha
   ZERO call sites de leitura em todo o jogo até agora) e o retângulo de
   (1), só chamado quando (1) o checador de sempre já rejeitou.
3. `engine/world_systems.py::resolve_manual_step()` — fonte única entre
   `PlayerInputSystem` (cliente) e `move_player()` (servidor): tenta o
   passo cheio de 32px primeiro (checador de sempre); se bloqueado,
   tenta um meio-passo de 16px na MESMA direção via (2). `target_tile_x/y`
   mandado por rede continua sempre inteiro — um sub-passo pra um tile
   novo já cruza a fronteira de tile matematicamente, então o protocolo
   `MOVE{tx,ty}` não mudou de formato. Servidor nunca confia em pixel
   vindo do cliente — recalcula do zero (função determinística sobre o
   mesmo mapa estático) e só aceita se o tile resolvido bater com o que
   o cliente pediu.
4. `engine/utils.py::start_tile_movement()` ganhou parâmetro ADITIVO
   opcional (mesmo padrão de `override_duration`) pra aceitar um alvo em
   pixel bruto em vez do centro do tile — usado também pro cálculo de
   duração da animação (senão um meio-passo de 16px animaria na mesma
   duração de um passo de 32px inteiro).
5. **Gap achado pelo Plan agent, corrigido junto**: a correção de
   dessincronia (`server/session.py::_handle_move`, MOVE rejeitado →
   `ENTITY_MOVE` de auto-correção) recentralizava a `Position` do
   cliente pro meio do tile incondicionalmente — apagaria um meio-passo
   legítimo silenciosamente. Fix: payload ganhou `px`/`py` opcionais
   (`WorldServer.get_pixel_pos()`, novo) — cliente usa quando vêm, senão
   cai no cálculo de centro de sempre (retrocompatível, sem novo tipo de
   mensagem; broadcast pra OUTROS clientes continua só `tx/ty`, posição
   exata de outro player nunca foi autoritativa pra gameplay).

**Fora de escopo, confirmado intocado**: mapas (CSV), pathfinding/A* de
mob/bot/auto-move, formato do protocolo `MOVE{tx,ty}`, orçamento
anti-cheat (tiles inteiros), ocupação entidade-por-tile (continua 1 por
tile inteiro — sub-passo só vale pra props ESTÁTICOS), lógica de
elevação/transição, timing de som de passo.

**Trade-off deliberado (avisado ao usuário, não é bug)**: depois de um
meio-passo, o eixo cruzado fica "fora de fase" (±16px do centro do
tile) até um snap real (teleporte/respawn/knockback) resetar —
`floor(pixel/32)` sempre resolve o tile certo, mas o personagem pode
renderizar levemente fora do centro da grade por um tempo. Escopo
também deliberadamente NÃO suporta um 2º meio-passo dentro do MESMO
tile (só cruza pra um tile novo) — evita quebrar a detecção de "mudei
de alvo" em `_send_player_move()`.

**Testes** (revert-to-confirm em CADA peça, isolada): `tests/
test_tileset_solid_rect.py` (gate de segurança multi-tile);
`tests/test_tile_walkable_fine.py` (AABB fino, fixture real prop
"vp_076", 4 direções de aproximação — só norte/leste livres, matemática
conferida à mão); `tests/test_resolve_manual_step.py` (passo cheio
normal, meio-passo bem-sucedido, meio-passo bloqueado, 2º meio-passo no
mesmo tile rejeitado por escopo); `tests/
test_start_tile_movement_pixel_override.py` (override de pixel/duração);
`tests/test_move_player_fine_step.py` (mesmo fallback no servidor real,
`WorldServer.move_player`); `tests/test_entity_move_px_py_correction.py`
(`get_pixel_pos` + correção preserva meio-passo). Suíte completa
(`pytest tests/ -q`, `py -3.10`) rodando ao final.

**Correção — usuário testou de verdade com o prop "vp_089" (vaso) e achou
2 bugs reais na matemática, não previstos pelos testes originais
(30/08/2026, mesmo dia).**

**Bug 1 — direção negativa (oeste/norte) sempre rejeitada.** A derivação
do tile-alvo do meio-passo usava `floor(pixel_candidato / TILE_SIZE)`.
Vindo do LESTE (dx=-1) a partir do centro do tile 6 (pixel 208), o
meio-passo aterrissa em 208-16=192 — e `floor(192/32)=6` (o PRÓPRIO tile
6), não 5 (o tile sendo alcançado), porque 192 é ao mesmo tempo "fim do
tile 5" E "início do tile 6", e a convenção de intervalo meio-aberto
`[T*32, T*32+32)` atribui essa fronteira ao tile da direita/abaixo.
Direção positiva (leste/sul) nunca sofria disso — só metade dos 4 lados
de aproximação funcionava na prática. Fix: tile-alvo agora é sempre
`current_tile + dx/dy` por aritmética direta — o pixel entra só no teste
geométrico de AABB, nunca na decisão de qual tile é.

**Bug 2 — continuar na mesma direção depois de um meio-passo não
avançava de verdade.** Mesmo com o bug 1 corrigido, o pixel do
meio-passo era calculado como `posição_atual ± 16px`. Isso funciona na
1ª vez (jogador sempre parte do CENTRO de um tile), mas se o jogador já
está descansando FORA do centro (de um meio-passo anterior), `+16px`
não necessariamente cruza pra dentro do tile seguinte — pode só andar
até o CENTRO do tile ATUAL (ex: descansando na borda norte, +16px leva
pro centro DESSE MESMO tile, não pro tile abaixo) — batendo um AABB
contra o objeto ERRADO (ou nenhum), com resultado imprevisível. Sintoma
relatado: "atravessa o vaso" ou correção diagonal do servidor — os 2
lados (cliente prediz, servidor recalcula) chegavam a resultados
diferentes porque a fórmula dependia do pixel ATUAL (que diverge entre
os 2 lados em timings diferentes), não de algo puramente determinístico.
Fix: o pixel do meio-passo passa a ser sempre a FRONTEIRA exata entre
`current_tile` e `target_tile` (`target_tile*TILE_SIZE` indo na direção
positiva, `current_tile*TILE_SIZE` na negativa) — determinística pelos
ÍNDICES de tile, nunca pelo pixel atual. Eixo perpendicular (que não
muda nesse passo) continua vindo da posição real, preservando um
descanso fora do centro já existente nesse eixo.

**Testes novos** (`tests/test_resolve_manual_step.py`, 4 casos
adicionados: aproximação pelo leste — direção negativa — entra no canto
livre; aproximação pelo oeste — direção positiva — continua bloqueada,
regressão; aproximação por baixo num objeto sintético com quadrante
NORDESTE sólido, cobrindo o eixo Y; continuar na mesma direção após um
meio-passo agora avança de verdade. `tests/test_move_player_fine_step.py`,
2 casos adicionados: aproximação pelo leste no servidor real; continuar
pro sul depois do meio-passo, reproduzindo a sequência exata relatada
pelo usuário). Revert-to-confirm em cada fix, isoladamente — confirmado
que os testes relevantes falham exatamente como o sintoma relatado antes
de restaurar. Suíte completa (`pytest tests/ -q`, `py -3.10`) rodando
de novo ao final.

**Correção 2 — usuário confirmou o bug 1 corrigido, mas achou um 3º bug
(mesmo dia).** Depois de um meio-passo, continuar pressionando a MESMA
direção ainda "atravessava o vaso" — tanto no eixo X (leste→oeste) quanto
Y (norte→sul).

**Causa raiz**: o passo seguinte, ao pular pro PRÓXIMO tile
(`current_tile+dx/dy`), sempre mirava no CENTRO daquele tile — uma
distância real (a partir de onde o jogador realmente estava, fora do
centro por causa do meio-passo anterior) de até 48px, não 32px. Esse
trecho extra de 16px NUNCA era validado — passava reto por cima da parte
sólida do tile ATUAL que o meio-passo tinha acabado de contornar. Pior:
como o pedido de rede manda só o TILE FINAL (`MOVE{tx,ty}`), e esse tile
final costuma ser um tile aberto normal (o de depois do objeto), o
`move_player()` aceitava o pedido só de olhar pro destino — nunca sabia
que o CAMINHO até lá cruzava o objeto no meio.

**Fix (2 partes)**: (1) `resolve_manual_step` ganhou um gate ANTES de
tentar pular pro próximo tile — se o jogador ainda não está no centro do
tile ATUAL (descansando de um meio-passo), o próximo passo na mesma
direção só tenta TERMINAR de entrar nesse tile (alcançar o centro dele),
usando o MESMO `is_tile_walkable_fine` de sempre, contra o tile atual —
nunca pula direto pro próximo. Corolário correto (não um limite
artificial): como a caixa do jogador (24px) é mais larga que a metade
livre de um objeto de 16px alinhada ao centro do tile, "terminar de
entrar" fica bloqueado na prática pra este objeto — o jogador para na
borda, não atravessa. (2) `server/world_server.py::move_player()`
precisou de uma reestruturação: o passo de 1 tile cardeal (WASD normal)
passa a ser resolvido INTEIRAMENTE por `resolve_manual_step` como
autoridade PRIMÁRIA (não só fallback quando o check grosso do destino
falha) — porque o check grosso sozinho, olhando só pro tile final pedido,
não tinha como saber que o caminho até lá cruzava o tile atual. Passo
diagonal (auto-move/A*) continua no check grosso de sempre, sem passar
por `resolve_manual_step` (que só entende cardeal).

**Efeito colateral achado e corrigido**: 2 testes pré-existentes
(`tests/test_client_ui.py::_make_wasd_pursuit_fixture`) usavam uma
`Position` de fixture no CANTO do tile (`5*32`) em vez do centro
(`5*32+16`) — nunca importava antes, mas agora dispara o novo gate
"terminar de entrar" contra um fake `TileValidation` sem
`is_tile_walkable_fine`. Corrigido ajustando a fixture pra centralizar a
posição (convenção real do jogo), não adicionando o método fake — a
fixture estava desalinhada com o estado real de qualquer player.

**Testes** (`tests/test_resolve_manual_step.py`: continuar na mesma
direção corretamente bloqueado nos eixos X e Y;
`tests/test_move_player_fine_step.py`: mesmo cenário no servidor real,
confirmando posição/tile inalterados após a rejeição). Revert-to-confirm
em cada peça (o gate de "terminar de entrar" isoladamente, e a
reestruturação de `move_player` isoladamente — sem ela, até o meio-passo
básico pelo norte quebrava, confirmando que virou autoridade primária de
verdade, não um fallback opcional). Suíte completa (`pytest tests/ -q`,
`py -3.10`) rodando de novo ao final.

**Correção 3 — usuário testou de novo com o log ligado e achou (1) um
crash real (`ZeroDivisionError`) e (2) confirmou que "o servidor
continua corrigindo a posição" (30/08/2026, mesmo dia).**

**Achado no log de verdade** (não suposição): `MOVE_REJECTED_WALKABLE
eid=255 requested=(99, 375) current=(99, 375)` — o cliente pediu pro
servidor mover pro MESMO tile que já é o atual. Isso só acontece por um
motivo: o meio-passo que "termina de entrar" no tile atual
(`resolve_manual_step`, Correção 2) retorna o MESMO `current_tile_x/y`
(só o pixel muda) — mas `_send_player_move()`
(`client/online_mode_handlers.py`) só detectava mudança comparando
`target_tile_x/y` contra o último valor mandado. Como o tile não muda
nesse caso, **o MOVE nunca era enviado pro servidor** — o cliente anima
localmente (pixel avança), o servidor nunca fica sabendo, e os 2 lados
ficam com uma posição diferente até algum evento não relacionado (ex:
correção de orçamento anti-cheat de um passo seguinte) expor a
divergência e "puxar" o player de volta — exatamente o "servidor
continua corrigindo a posição" relatado.

**Fix**: `_send_player_move()` passa a comparar também o PIXEL-alvo
(`target_pixel_x/y`), não só o tile — `game.py` ganhou
`_net_last_px/py` ao lado de `_net_last_tx/ty`. Os outros 3 pontos que
já escreviam `_net_last_tx/ty` pra SUPRIMIR de propósito o envio (dash/
knockback confirmado pelo servidor, sync de login) precisaram do mesmo
tratamento — sem sincronizar `_net_last_px/py` junto, o gate novo
reintroduziria o envio espúrio que esses pontos existem pra evitar.

**Sobre o crash (`ZeroDivisionError` em `TileMovementSystem.update()`,
`dt / tile_movement.move_duration`)**: a causa é `move_duration=0.0`
exato, que só pode vir de `start_tile_movement`'s `dist=0` (pixel-alvo
igual ao de partida) quando chamado com `override_target_pixel`
(exclusivo do passo manual). Fuzzing extensivo de `resolve_manual_step`
sozinho (>78 mil combinações de direção/posição) não reproduziu — a
dessincronia de rede acima (cliente e servidor discordando de onde o
player realmente está) é a candidata mais forte pra criar o estado que
dispara isso, mas o mecanismo exato não foi isolado com certeza.
**Correção defensiva aplicada de qualquer forma**: `start_tile_movement`
nunca mais deixa `move_duration` chegar a 0 (piso de 0.001s) —
independente da causa raiz exata, nenhum chamador (presente ou futuro)
consegue mais travar o jogo por essa divisão.

**Testes**: `tests/test_send_player_move_pixel_gate.py` (novo — mudança
de tile dispara envio; meio-passo no MESMO tile agora dispara envio;
nada mudou não reenvia); `tests/
test_start_tile_movement_pixel_override.py` (novo caso: pixel-alvo igual
ao de partida nunca produz duração zero). Revert-to-confirm em cada um.
Suíte completa (`pytest tests/ -q`, `py -3.10`) rodando de novo ao
final.

**Pendente**: usuário testar de novo com o log ligado — se o crash ainda
acontecer, o log/traceback exato ajuda a fechar o mecanismo com certeza
em vez de só a correção defensiva.

**Correção 4 — usuário testou de novo (com o servidor de verdade
reiniciado) e o log revelou a causa raiz de VERDADE, mais séria que as
anteriores (30/08/2026, mesmo dia).**

**Achado no log real, mapa real** — `MOVE_REJECTED_WALKABLE eid=255
requested=(99, 375) current=(99, 375)` repetindo indefinidamente.
Investigado contra o mapa de verdade (`maps/map_1_objects.csv`, linha
375): colunas 98/99/100 são **3 vasos colados** (`vp_091`/`vp_089`/
`vp_090`), todos sólidos no nível grosso. O jogador tinha ficado
registrado (`current_tile_x/y`) EXATAMENTE em cima do vaso do meio
(`vp_089`, tile 99) — resultado esperado e correto do meio-passo
(Correção 1): o retângulo FINO libera aquele pixel específico, mas o
tile continua "sólido" pra qualquer checagem GROSSA.

**O bug de verdade**: nenhuma das checagens de `is_tile_walkable`
(nem a via bundle, nem a crua) jamais teve uma exceção pra "o alvo é o
MEU PRÓPRIO tile atual" — só a checagem de OCUPAÇÃO por outra entidade
tinha esse auto-perdão (`occupant_id == moving_entity_id`). A checagem
de solidez de base (`tile_type.is_solid` → `return False`) roda ANTES
dessa exceção e não sabe nada sobre "quem está pedindo". Resultado: uma
vez que `current_tile_x/y` vira o tile de um objeto pequeno (via meio-
passo), **qualquer resync trivial de rede pedindo "continuo aqui"**
(exatamente o que a Correção 3 — gate de pixel — passou a mandar
corretamente) **era rejeitado pra sempre** — o jogador ficava
PERMANENTEMENTE preso, sem conseguir nem confirmar a própria posição,
quanto mais sair dali. Essa armadilha só existe DEPOIS da Correção 1 —
antes, `current_tile_x/y` nunca podia apontar pra um tile sólido, então
a situação nunca surgia.

**Fix**: `move_player()` ganhou um atalho explícito ANTES de qualquer
checagem de walkability — se o pedido é pro MESMO tile que o player já
ocupa (`_step_dx=_step_dy=0`), aceita sempre, sem consultar
`is_tile_walkable` nenhum, preservando o pixel REAL atual (não
recentraliza — um meio-passo legítimo continua descansando fora do
centro). Escopo mínimo: só cobre o caso `tx,ty == current`, não muda
NADA da validação pra qualquer outro destino.

**Testes** (`tests/test_move_player_self_tile_trap.py`, novo arquivo,
contra o MAPA REAL — não sintético): confirma a premissa (99,375 é
sólido de verdade no mapa); confirma que "continuo aqui" em cima do
vaso é sempre aceito e preserva o pixel do meio-passo. Revert-to-confirm
(reproduziu a rejeição exata do log real antes de restaurar). Suíte
completa (`pytest tests/ -q`, `py -3.10`) rodando de novo ao final.

**Lição pra próxima vez que `current_tile_x/y` puder apontar pra um
tile marcado sólido** (qualquer mecanismo futuro parecido com o
meio-passo): sempre verificar se TODOS os consumidores de
`is_tile_walkable` — não só os novos — têm uma saída pra "o alvo é onde
eu já estou". Essa checagem nunca precisou existir antes porque a
premissa "current_tile_x/y nunca é sólido" era garantida estruturalmente
até agora.

**REVERTIDO a pedido do usuário (30/08/2026, mesmo dia).** Depois da
Correção 4, o usuário testou de novo (servidor reiniciado com o código
novo) e a correção de posição continuava acontecendo, de forma que
descreveu como "muito grotesca". Avaliando o custo — 4 rodadas de
correção sem convergir de vez, cada uma revelando uma categoria NOVA de
bug (matemática de fronteira de tile, passo encadeado, gate de rede,
`current_tile_x/y` podendo apontar pra tile sólido) — o usuário decidiu
que o esforço de manutenção não compensava o ganho visual (deixar de
bloquear o tile de 32px inteiro perto de props pequenos) e pediu reversão
completa pra o estado de antes desta feature.

**Escopo do revert**: toda a mecânica de meio-passo de 16px foi removida
— `get_solid_rect()` (engine/tileset.py), `is_tile_walkable_fine()`
(módulo + `TileValidationSystem`, engine/world_systems.py),
`resolve_manual_step()` (engine/world_systems.py), o parâmetro
`override_target_pixel` de `start_tile_movement()` (engine/utils.py), o
bloco de WASD alternativo em `PlayerInputSystem` (ui/systems.py), o
fallback fino + atalho de auto-tile em `WorldServer.move_player()` e
`get_pixel_pos()` (server/world_server.py), os campos `px`/`py` na
correção de `_handle_move` (server/session.py), o uso de `px`/`py` em
`_handle_msg_entity_move` e a inicialização de `_net_last_px/py`
(client/network_handlers.py), o gate de pixel em `_send_player_move`
(client/online_mode_handlers.py), o sync de `_net_last_px/py` na fila de
dash (client/remote_entity_handlers.py), e as declarações de
`_net_last_px/py` (game.py). Os 8 arquivos de teste criados
especificamente pra esta feature foram deletados. `CLAUDE.md` teve o
"Ponto único de verdade" correspondente trocado por um ponteiro pra este
histórico, pra qualquer tentativa futura não começar do zero. Suíte
completa rodada após o revert pra confirmar que o comportamento pré-
feature foi restaurado sem regressão em nenhum outro trabalho da sessão.

**Esta seção (§85) fica como está** — histórico completo do que foi
tentado, por quê, e cada bug real encontrado no caminho — caso o tema
volte a ser considerado no futuro.

## 86. (movido) Auto-regen "fantasma" na BG — HP5 tickando com o personagem aparentemente cheio (30/08/2026) — ver HISTORICO_BUGS.md §29

## 87. Interceptar: alvo tomava hit de auto-attack ANTES da animação do dash acontecer (31/08/2026)

Usuário relatou: "quando eu aperto a skill, antes mesmo de o dash
acontecer, o meu alvo já toma um hit do ataque automático". Relatou
também, na mesma sessão, mobs "procurando o player em outra posição,
como se tivesse delay pra entender a posição" — investigado à parte
(ver "Sintoma (b)" abaixo, não é bug, é comportamento existente ficando
mais visível).

**Investigação — leitura de código não bastou, precisou de log ao
vivo.** `_skill_interceptar` exige alvo a >2.3 tiles pra castar
(`INTERCEPT_MIN_RANGE_PX`) e ataque automático corpo-a-corpo só acerta
a ≤1 tile (`_resolve_auto_attack`) — pela leitura estática, as duas
distâncias se excluem, o cenário relatado parecia arquiteturalmente
impossível. Log novo (`AUTO_ATK_RANGE_OK`, `server/combat_processor.py`,
mesmo padrão `AOI_DBG`/`RPG_DEBUG_AOI=1`) capturando posição/`progress`/
`is_dash` no exato instante em que um auto-attack passa pelo gate de
alcance resolveu de vez: `tick=1632 attacker_progress=0.0
attacker_is_dash=True attacker_current_tile=(129,375)` — igual ao
DESTINO do dash, com a animação ainda em 0%.

**Causa raiz (confirmada em código, não só no log)**:
`server/skill_processor.py::_process_skill_requests`, logo depois de
qualquer skill que move o player (hoje só Interceptar,
`_MOVEMENT_PREDICTED_SKILLS`), fazia:
```python
tile_move.current_tile_x = _new_tx
tile_move.current_tile_y = _new_ty
```
— snapando a posição LÓGICA pro destino do dash IMEDIATAMENTE, sem
esperar `TileMovementSystem` completar o tween (`progress>=1.0`).
`current_tile_x/y` é o MESMO campo que `_resolve_auto_attack`
(`server/combat_processor.py`) usa pra medir distância — então o auto-
attack (e qualquer skill futura) passava a achar que o player já tinha
chegado, ~200ms antes da animação (servidor E cliente) realmente
terminar. Achado com `git blame`: esse snap existe desde a implementação
ORIGINAL do Interceptar (commit `9a62279`, 24/05/2026) — não é
regressão de nenhuma correção recente das rodadas anteriores desta
mesma skill (§84); sempre esteve lá.

**Proposta inicial (rejeitada pelo usuário) e por quê**: cheguei a
propor um "guard" separado (bloquear auto-attack enquanto `is_dash`
estivesse tocando, sem mexer no snap) — corrigiria o sintoma sem tocar
na causa. O usuário rejeitou explicitamente: "isso em si é uma correção
incorreta... o dash deveria funcionar como proposto... após a animação
[...] atualiza a posição [...] e só então [...] executa o dano, seja
automático ou de skill". Está certo — um guard adicional em cima do
snap deixaria DOIS mecanismos concorrentes controlando quando o player
"chegou" (prática que este projeto já evita em outros lugares, ver
`_tick_player_move_grace` em §84, causa raiz de um bug JUSTAMENTE por
ter 2 relógios independentes decidindo a mesma coisa).

**Fix real**: remover o snap. `current_tile_x/y` volta a mudar SÓ
quando `TileMovementSystem` completa o tween — como já era o design
original desse campo pra QUALQUER outro movimento (passo normal,
knockback, etc.), Interceptar era a única skill com um atalho próprio.
`target_tile_x/y` (que já muda na hora, de propósito) continua
suficiente pra notificar rede (`_moved_this_tick`/
`_skill_position_corrections`) — nada mais dependia do snap de
`current_tile_x/y`.

**Testes** (`tests/test_interceptar_no_early_auto_attack.py`, novo
arquivo, 3 testes): `current_tile_x/y` não muda no tick do cast (só
`target_tile_x/y` muda); auto-attack NÃO dispara logo após o cast;
auto-attack dispara normalmente depois que `TileMovementSystem` termina
o tween de verdade. Revert-to-confirm (restaurado o snap manualmente):
2 dos 3 testes falharam exatamente como o bug relatado antes de
restaurar o fix. `tests/diag_interceptar.py` (script de diagnóstico da
investigação anterior, não coletado pelo pytest) tinha uma asserção que
dependia do snap instantâneo — atualizada pra verificar `target_tile_x/y`
(muda na hora) em vez de `current_tile_x/y` (só muda no fim da
animação). Suíte completa (`pytest tests/ -q`, `py -3.10`): 1375
passed, zero regressão.

**Sintoma (b) — mobs "procurando" o player em posição errada, não é
bug**: `EnemyAISystem` recalcula path do alvo perseguido no máximo a
cada 0.8s (`path_recalc_interval`/`_CHASE_RECALC_MIN_TICKS_BY_TIER`,
throttle deliberado de performance, 06/08/2026). Andando normal isso é
imperceptível; o dash desloca o player várias tiles em 0.18s, então
durante a janela de até 0.8s os mobs que já perseguiam continuam
mirando a posição PRÉ-dash — mesmo throttle existente, só fica muito
mais visível por causa do deslocamento quase instantâneo do dash. Não
alterado nesta sessão — usuário não pediu ajuste, só perguntou; se
quiser (ex: forçar recálculo de path pros mobs com o player como alvo
no tick em que o dash completa), é decisão de trade-off performance ×
responsividade a discutir separadamente (mesmo throttle calibrado em
§29/§30).

**2ª causa raiz — o fix acima não bastou, usuário testou de novo
(31/08/2026, mesmo dia).** Confirmado no log novo que a 1ª correção
funcionou: `AUTO_ATK_RANGE_OK` agora só passa DEPOIS de `DASH_COMPLETE`
no servidor (era ~200ms ANTES). Mas o usuário ainda via o hit "antes do
dash" na tela — achado com `RPG_DEBUG_INTERCEPTAR=1` (log do cliente):
```
MOVE_REQUEST tick=1061 ... accepted=True   ← passo normal ainda tocando
DASH_OK      tick=1062 ...                 ← servidor confirma o dash
DASH_START   real_tx=127 real_ty=378  queued=True   ← dash NÃO começou
```
`client/network_handlers.py::_handle_msg_entity_move`: quando a
confirmação do dash chega e o player já está com uma animação de passo
normal em andamento (`is_moving=True`), o dash entra em
`_self_move_queue` e só começa a tocar quando essa animação anterior
termina — comum, já que o jogador tipicamente andou até o alcance
mínimo do Interceptar (74px) antes de castar. Como o passo normal dura
mais que o dash (0.18s), o servidor (que nunca enfileira, sempre anima
na hora) resolve tudo — inclusive o auto-attack seguinte — bem antes da
animação do dash sequer COMEÇAR no cliente. Mesmo sintoma visual do bug
original, causa diferente.

**Fix (reaproveitando mecanismo já existente, não inventado)**: o
próprio `_handle_msg_entity_move` já tem um caminho que PREEMPTA
qualquer fila/animação em andamento e começa na hora — usado pelo
knockback (Tiro Repulsivo), que sempre manda `duration` explícito no
payload de correção. Interceptar nunca mandava esse campo, por isso caía
no caminho de fila em vez do de preempção. Adicionado `"duration":
tile_move.move_duration` (genérico — usa o valor que o próprio handler
da skill já configurou, funciona pra qualquer skill futura em
`_MOVEMENT_PREDICTED_SKILLS` sem hardcode) na correção de posição do
Interceptar (`server/skill_processor.py`). Dispatch
(`server/session.py:3245-3246`, `"duration" in pos_corr`) e tratamento
no cliente já existiam prontos, só nunca eram alimentados por Interceptar.

**Testes** (`tests/test_interceptar_no_early_auto_attack.py`, +1 teste):
correção de posição carrega `duration == tile_move.move_duration` (>0).
Revert-to-confirm (`duration: None` no lugar do valor real) reproduziu a
falha antes de restaurar. Suíte completa (`pytest tests/ -q`, `py
-3.10`): 1375 passed + 1 falha pré-existente e intermitente
(`test_mob_despawn_sent_to_both_players`, domínio de AOI de mob,
sem relação com Interceptar — passa sempre isolado, 2/2; falha só
dentro da suíte completa, mesma classe de flakiness de dispatch
assíncrono já documentada em `tests/helpers.py::run_ticks`).

**3ª causa raiz — "Nenhum alvo" voltou a acontecer, usuário testou de
novo (31/08/2026, mesmo dia).** Usuário questionou corretamente antes de
eu concluir: o alvo (`combat_state.target_entity_id`) continuava
selecionado, então por que "Nenhum alvo" em vez de simplesmente atacar?
Verificação em código (`_resolve_target`, `engine/skill_handlers.py:
157-181`) confirmou: o alvo já selecionado NUNCA é desfeito, mas a
função sempre reconfere se ele ainda está dentro do alcance a partir da
posição ATUAL do player (proteção deliberada, documentada — sem ela
qualquer skill acertaria qualquer entidade do mapa). Log
(`RPG_DEBUG_AOI=1` + `RPG_DEBUG_INTERCEPTAR=1`) mostrou a causa real:
```
DASH_OK       tick=1322  tx=133 ty=385  is_dash=True   ← Interceptar aponta pra (133,385)
MOVE_REQUEST  tick=1322  tx=131 ty=382  accepted=True  ← 6ms depois, MOVE normal residual ACEITO
DASH_COMPLETE eid=255  landed_tile=(131, 382)          ← dash "aterrissa" na tile do MOVE, não na do Interceptar!
SKILL_WARN    reason=Nenhum alvo  caster_tile=(131,382)  target_tile=(133,384)
```
`move_player()` (`server/world_server.py`) sobrescrevia
`current_tile_x/y`/`target_tile_x/y` incondicionalmente pra QUALQUER
MOVE aceito, sem checar se um dash já estava em andamento — um MOVE
normal residual (comum: jogador anda até o alcance mínimo do Interceptar
e o cliente ainda tinha um passo WASD enfileirado quase junto com o
cast) processado durante a janela do dash sequestrava o destino. O
alvo continuava selecionado; o player é que fisicamente não estava mais
perto dele. Comentário já existente no código ("corrida entre MOVE e
CAST_SKILL") reconhecia esse risco, mas só tratava o lado cliente,
nunca o servidor sobrescrevendo o próprio dash.

**Achado real, não coincidência**: a 1ª correção desta seção (remover o
snap instantâneo de `current_tile_x/y`) foi o que EXPÔS esta corrida —
antes, o snap errado deixava `current_tile_x/y` já "adiantado" pro
destino do dash, o que (por acidente) fazia o anti-cheat de
`move_player()` rejeitar um MOVE residual incoerente com essa posição
adiantada. Corrigir o snap (correto, pedido do usuário) removeu essa
proteção acidental — o fix certo não é reverter o snap, é fechar o gap
real que sempre existiu em `move_player()`.

**Fix**: `move_player()` agora recusa (`return False`) um MOVE normal
enquanto `TileMovement.is_dash and is_moving` (dash genuinamente em
andamento) — simétrico à proteção que `TileMovementSystem` já dá pro
caso inverso (dash não é interrompido por stun/sleep, `if not
tile_movement.is_dash:`). Novo evento de log
`MOVE_REJECTED_DASH_IN_PROGRESS` (`debug/aoi_debug.py`).

**Testes** (`tests/test_interceptar_no_early_auto_attack.py`, +2
testes): MOVE normal recusado durante dash em andamento, destino do
dash preservado; MOVE normal funciona normalmente depois que o dash
completa. Revert-to-confirm (`if False and tm.is_dash...`) — o teste de
recusa falhou exatamente como o bug real (MOVE aceito, teria
sequestrado o destino) antes de restaurar. Suíte completa (`pytest
tests/ -q`, `py -3.10`): 1377 passed + a mesma falha pré-existente e
intermitente de `test_mob_despawn_sent_to_both_players` (não relacionada).

**Validação**: confirmada em jogo pelo usuário (31/08/2026) — "Certo,
validado." Fechado. Log da sessão de validação (`debug/logs/
interceptar_debug.log`) mostra a corrida MOVE×CAST_SKILL acontecendo de
novo pelo menos 1 vez em jogo real (`DASH_OK tick=5702` seguido de
`MOVE_REQUEST tick=5702 ... accepted=False` no mesmo tick) — desta vez
corretamente RECUSADO pelo guard novo, e todo `DASH_FORCED` subsequente
já carrega `duration=0.18` (2ª correção também confirmada ativa).

## 88. (movido) Buff bar unificada (estilo WoW) — feature nova (31/08/2026) — ver HISTORICO_FEATURES.md §19

## 89. Barra de HP do HUD "crescia" além do normal com proc que aumenta max_hp (01/09/2026)

Usuário relatou: proc "Vontade Imortal" (item `amulet_undying`, +stamina
→ +max_hp) fazia a barra de HP do HUD (canto superior esquerdo)
aumentar de tamanho, em vez de só refletir a vida nova. Corretamente
identificado pelo usuário como estrutural: "qualquer efeito que aumente
a vida do personagem precisa afetar a quantidade de HP, mas a HUD não
deve ser afetada... algo na UI está com um furo".

**2 causas, ambas estruturais**:

1. **Sync real faltando** — `WorldServer._sync_player_hp_dirty()`
   (`server/world_server.py`) já detecta QUALQUER mudança de current_hp/
   max_hp genericamente ("cobre qualquer fonte de mudança... sem
   precisar de código por feature", docstring original), mas só usava
   isso pra montar o broadcast AOI pros OUTROS players —
   `server/session.py` EXCLUI o próprio dono desse broadcast de
   propósito (`exclude_sid=_caster_sid`), com um comentário assumindo
   "já tem via STATS_UPDATE" de outro canal. Falso: nenhum canal real
   manda `hp_max` pro dono quando a mudança vem de
   `add_modifier`/`timed_modifiers` sem `queue_stats_update` explícito —
   proc de item, buff de chefe de jungle, self-buff de skill (todo o
   território do §88) nunca chamam isso. `current_hp` eventualmente
   chegava certo via HP5/combate (que só manda `hp_after`, nunca
   `hp_max`), mas com o `max_hp` do PRÓPRIO cliente preso no valor
   ANTIGO — `current/max` passava de 1.0.
2. **UI sem teto defensivo** — `client/hud_handlers.py::_draw_hud`
   calculava a proporção de preenchimento de TODAS as 5 barras (HP/mana/
   concentração/aljava/raiva) só com `max(0, ...)`, sem `min(1, ...)`.
   Se a proporção passar de 1.0 por qualquer motivo (mesmo um desync de
   1 tick), o retângulo de PREENCHIMENTO desenha mais largo que a
   moldura de fundo — visualmente "a barra cresce". Todo outro lugar do
   jogo que desenha barra de HP (`ui/hud_bars.py::build_simple_hp_bar`/
   `build_player_hud`, `ui/systems.py:1671`) já clampava os dois lados;
   este arquivo era o único ponto sem o teto.

**Fix**: `_sync_player_hp_dirty()` agora TAMBÉM enfileira 1
`queue_stats_update({"player_eid", "hp", "hp_max"})` privado pro próprio
dono, reusando a MESMA detecção genérica — nenhuma skill/talento/proc
precisa chamar isso à mão, cobre qualquer fonte futura também. As 5
barras de `hud_handlers.py` ganharam `min(1.0, ...)` — defensivo, pra
qualquer desync remanescente nunca mais desenhar fora da moldura.

**Testes** (`tests/test_owner_hp_max_sync.py`, novo): proc que aumenta
stamina gera STATS_UPDATE privado pro dono com hp/hp_max corretos; sem
mudança de HP não gera update novo (evita spam); dano normal também
notifica (guarda de regressão — não quebrar o caminho que já funcionava).
Revert-to-confirm (removida a chamada nova) — 2 dos 3 testes falharam
exatamente como o bug relatado antes de restaurar. Suíte completa
(`pytest tests/ -q`, `py -3.10`): 1405 passed + a mesma falha pré-
existente/intermitente de sempre, sem relação.

**Validação**: pendente — usuário ainda vai testar em jogo.

## 90. (movido) Tooltip nos ícones da buff bar (01/09/2026) — ver HISTORICO_FEATURES.md §20

## 91. Feedback visual de bush stealth — opacidade 50% no próprio sprite (01/09/2026)

Usuário pediu: ao entrar numa zona de bush (stealth estilo LoL/MOBA,
`bush_zones`, já existente desde §47/13-08-2026), o PRÓPRIO jogador vê
seu personagem com ~50% de opacidade — sinal pessoal de "estou
escondido dos outros agora". Confirmado explicitamente com o usuário,
FORA de escopo: fog of war / falta de linha de visão comum ("atrás de
árvore", `server/tile_los_processor.py`) NÃO conta — é comportamento
natural de qualquer MMO, não stealth deliberado; só a zona de bush de
verdade ativa o feedback.

**Achado da investigação**: `_get_bush_zone(eid)` (`server/
bush_zone_processor.py`) já existia, 100% server-side, recalculado sob
demanda a cada checagem de visibilidade (`server/session.py::_can_see`)
— zero informação de "estou numa bush" chegava ao cliente. Confirmado
que bush e Camuflagem (`CombatState.is_visible`/`is_camouflaged`) são 2
mecanismos totalmente separados, sem overlap de código — a implementação
teve o cuidado explícito de nunca tocar nesses 2 campos existentes.

**Mecanismo**: `CombatState.is_bush_hidden: bool` (`engine/
components.py`) — campo novo, client-side, sem significado nenhum pro
servidor além de espelhar `_get_bush_zone(eid) is not None`.
`WorldServer._sync_player_bush_dirty()` (`server/world_server.py`),
MESMO princípio de `_sync_player_hp_dirty` (§89): cache por player
(`self._player_bush_cache: dict[int, bool]`), só enfileira
`queue_stats_update({"player_eid": eid, "in_bush": bool})` quando o
estado MUDA — 100% privado (nunca broadcast AOI, ninguém além do dono
precisa saber). `client/network_handlers.py::_handle_msg_stats_update`
aplica `in_bush` em `CombatState.is_bush_hidden` só no bloco do próprio
player (`eid == self._my_eid`), mesmo padrão de `hp`/`hp_max`.

`ui/systems.py::RenderSystem.render()` ganhou um novo `elif` no mesmo
bloco de desenho de entidade (`_sprite_rnd`/`_is_corpse_draw`/
`_is_ghost_draw`/`else`), logo depois de `_is_ghost_draw` (espírito tem
prioridade visual — morto E numa bush ao mesmo tempo mostra espírito,
não bush): `elif _is_local_player and combat_state.is_bush_hidden`, MESMA
técnica já usada pro espírito (`fill_surf(..., alpha=127)`). A checagem
`_is_local_player` (`PlayerControlled`) já existia nesse método, mas só
calculada mais abaixo pro badge de HUD — subida pra ANTES do bloco de
desenho, reusada nos 2 lugares (removida a duplicata).

**Testes** (`tests/test_bush_stealth.py`, nova classe
`TestSyncPlayerBushDirty`): entrar na bush gera update com
`in_bush=True`; sair gera `in_bush=False`; sem transição (fora, ou
permanecendo dentro do mesmo bush) não gera nada — evita spam todo
tick. Revert-to-confirm (`_sync_player_bush_dirty` temporariamente
desativado) — os 2 testes de transição falharam exatamente como
esperado antes de restaurar. Suíte completa (`pytest tests/ -q`,
`py -3.10`): 1415 passed + a mesma falha pré-existente/intermitente de
sempre (`test_mob_despawn_sent_to_both_players`), sem relação.

**Fora do automatizável (nesta rodada inicial)**: opacidade aparecendo/
sumindo de verdade em jogo — usuário ainda ia validar.

### Correção (01/09/2026, mesmo dia) — a fonte de detecção nunca disparava em mapa real

Usuário testou em jogo e a opacidade nunca apareceu. Investigação:
`grep` em `maps/*_entities.json` inteiro não acha a chave `bush_zones`
em NENHUM mapa — `_get_bush_zone()` sempre retorna `None` na prática,
só populado em fixtures sintéticas de teste
(`tests/test_bush_stealth.py`, comentário do próprio arquivo já dizia
isso). A Fase 1 acima estava correta pro que foi pedido, mas não tinha
onde disparar — bug de dado ausente, não de lógica.

Perguntado ao usuário como resolver: confirmou que "bush" de verdade
já existe como objeto de tile real, catalogado em `engine/tileset.py`
linha 913 (`OBJECT_SHEET_FAMILIES`, sheet "TX Village Plant", grupo
`"arbustos"`, tiles `pl_b1..pl_b26`, `vision_height=2`,
`vision_rect="base_row"`) — e confirmou também (achado adicional desta
investigação) que a invisibilidade DE VERDADE pros outros players ao
pisar num arbusto real já funciona hoje, testado, mas por OUTRO
mecanismo: `server/tile_los_processor.py::_has_tile_los()` (chamado por
`server/session.py::_can_see`), que trata qualquer tile com
`vision_height>=2` no próprio tile do alvo como bloqueio de LOS —
universal, sem relação com `_get_bush_zone`. **`_has_tile_los`/
`_can_see` não foram tocados** — já funcionam, são a fonte de verdade
da invisibilidade real.

Problema: `vision_height>=2` sozinho não distingue bush de árvore
(grupo `"arvores"`, mesmo tileset, mesma `vision_height=2`) — e o
usuário já tinha confirmado antes que "atrás da árvore" NÃO deve ativar
a opacidade. `TileType` não tinha nenhum campo consultável pra "isto é
um arbusto" (só uma convenção informal de nome de sprite,
`sprite_name="pl_b1"`) — checar isso por string em runtime seria
exatamente o padrão hardcoded que o usuário já tinha rejeitado nesta
sessão pro sistema de buffs, então virou plano formal (EnterPlanMode)
antes de codar.

**Fix**: `TileType.is_bush: bool = False` (novo campo,
`engine/tileset.py`) — só o grupo `"arbustos"` (linha 913) passa
`"is_bush": True`; `_register_catalog_tiles`/
`discover_object_sheet_tiles` propagam o parâmetro (mesmo padrão já
usado por `default_vision_height`/`underlying_terrain` por grupo).
`engine/entity_factory.py::create_tilemap` — o `dataclasses.replace()`
que já propaga `vision_height` pra toda a pegada de visão do objeto
(`vision_rect="base_row"`, pode cobrir várias colunas num arbusto
largo) ganhou `is_bush=obj_tile.is_bush` no MESMO replace — sem isso só
a célula-âncora ficaria marcada, não o resto da base de um arbusto
largo (`pl_b3`+, 64px = 2 tiles). Novo
`BushZoneProcessorMixin._is_on_bush_tile(eid)`
(`server/bush_zone_processor.py`) lê `tile_matrix[ty][tx].is_bush` na
posição atual do player (reusa `TileLosProcessorMixin._tilemap_for_map`,
mesmo lookup que `_has_tile_los` já usa).
`WorldServer._sync_player_bush_dirty()` trocou a fonte de
`self._get_bush_zone(peid) is not None` pra
`self._is_on_bush_tile(peid)` — resto do método (cache/dirty-check
privado) intocado.

**Achado fora de escopo, reportado ao usuário, NÃO corrigido agora**:
`WorldServer._tick_bush_target_clear()` (limpa `target_entity_id`
travado quando o alvo entra num bush) também lê `_get_bush_zone`/
`_bush_zone_cache` — mesma causa raiz, nunca dispara em mapa real. Um
alvo que fica de fato invisível via `_has_tile_los` (pisando num
arbusto de verdade) não limpa a mira travada de quem já mirava nele.
Gap pré-existente, independente desta tarefa (opacidade) — decisão de
corrigir ou não fica com o usuário.

**Testes**: `TestIsOnBushTile` (novo) — em cima de arbusto real →
`True`; em cima de árvore (mesmo `vision_height=2`) → `False`, guarda
de regressão pro caso que motivou a distinção; fora de qualquer objeto
→ `False`. `TestBushFootprintPropagation` (novo) — arbusto largo
(`pl_b3`) via `create_tilemap` de verdade marca `is_bush=True` em TODA
a base (célula-âncora + 2ª coluna), não só a âncora; árvore larga nunca
marca `is_bush`, mesmo bloqueando visão. `TestSyncPlayerBushDirty`
adaptado pra plantar um arbusto real (`_plant_object`, novo helper) em
vez de popular `_bush_zones_by_map`. Revert-to-confirm em 2 pontos
(`"is_bush": True`→`False` no grupo do catálogo; `is_bush=` removido do
`replace()` de propagação) — cada um derrubou exatamente os testes
esperados antes de restaurar. Confirmado por script ad-hoc
(`create_tilemap` com objeto `pl_b3` de 2 tiles de largura) que a 2ª
célula da base ganha `is_bush=True` de verdade, não só a âncora. Suíte
completa (`pytest tests/ -q`, `py -3.10`): 1420 passed + a mesma falha
pré-existente/intermitente de sempre, sem relação. Confirmado por
`grep` que `map_1`, `map_main` e a battleground têm milhares de
ocorrências de `pl_b*` já plantadas — a opacidade tem onde disparar de
verdade agora. Validação final (opacidade aparecendo/sumindo ao
entrar/sair de um arbusto real) fica com o usuário, em jogo.

## 92. (movido) Comando `/forfeit`/`/ff` na BG (01/09/2026) — ver HISTORICO_FEATURES.md §21

## 93. (movido) Skill real sumia da hotbar ao SAIR da BG — não é bug do `/ff`, pré-existente (01/09/2026) — ver HISTORICO_BUGS.md §30

## 94. (movido) "TX Village Props with Shadow" — grupos por categoria no minimapa (02/09/2026) — ver HISTORICO_FEATURES.md §22

## 95. (movido) Chão de `map_1` "vazio" — família de sprite de grama antiga deletada pelo usuário durante a limpeza do §94 (02/09/2026) — ver HISTORICO_BUGS.md §31

## 96. (movido) Recatálogo de "TX Tileset Terrain" — usuário reorganizou o PNG (02/09/2026) — ver HISTORICO_FEATURES.md §23

## 97. `map_width_tiles` usava só a linha 0 — bug real achado investigando spam de "[MAPA] Aviso" (02/09/2026)

Usuário reportou 600+ linhas de `[MAPA] Aviso: Linha N tem X tiles,
esperado 432` no console do cliente depois do recatálogo/fix de grama.
1ª hipótese (errada, corrigida ANTES de agir — nunca cheguei a tocar
no arquivo com ela): linhas mais largas que 432 seriam lixo/duplicação
acumulada de saves repetidos, cortar pra 300 (largura de
`map_1_objects.csv`, uniforme). Verificação antes de agir: toda linha,
curta OU longa (até 1944 células), começa E termina com `"m"`
(montanha) — padrão consistente de borda natural, não corrupção.
Conclusão real: `map_1` tem forma ORGÂNICA/não-retangular de
propósito, cada linha reflete a extensão real do terreno naquela
altura.

**Causa raiz de verdade**: `engine/entity_factory.py::create_tilemap`
calculava `map_width_tiles = len(terrain_matrix[0])` — só a linha 0,
que por coincidência é uma parte mais ESTREITA do mapa (432), não a
mais larga (1944). Qualquer coisa que respeita `map_width_tiles`
(movimento, câmera, minimapa) tratava terreno real pintado além da
coluna 431 como "fora do mapa" — bug pré-existente, não causado pelo
usuário nem por mim hoje, só nunca tinha sido notado.

**Fix**: `map_width_tiles = max(len(r) for r in terrain_matrix)` — só
essa linha muda; o loop que já preenche linha mais curta com
`FLOOR_TILE` (`create_tilemap`, "Pass 1") já fazia a coisa certa,
só recebia o número errado. `engine/map_loader.py::validate_map`
também mudou — a checagem antiga (linha != largura da linha 0) não
faz mais sentido pra mapa de forma orgânica válida; comparar contra a
largura MÁXIMA também não resolveria (toda linha != máxima ainda
dispararia o aviso) — removida a checagem de largura por completo
(não existe mais uma "largura esperada" única que sirva pra qualquer
formato de mapa); função mantida (mapa vazio ainda avisa) pra
checagens futuras.

**Testes**: `tests/test_map_width_ragged_rows.py` (novo) — linha 0
mais estreita não trunca a largura real do mapa; linha curta ainda é
preenchida com `FLOOR_TILE` até a largura máxima; todas as linhas do
`tile_matrix` ficam com o mesmo tamanho no final; mapa retangular
uniforme (todas as linhas iguais) continua se comportando exatamente
igual — regressão pro caso comum. `validate_map`: linhas de tamanhos
diferentes não geram mais aviso; mapa vazio ainda avisa; mapa
retangular normal continua sem aviso. Revert-to-confirm: 3 dos 4
testes de `create_tilemap` falharam exatamente como esperado com o
fix desligado (o 4º, mapa retangular, passa nos dois casos —
confirma ausência de regressão). Suíte completa (`pytest tests/ -q`,
`py -3.10`): 1431 passed + as mesmas 8 falhas pré-existentes/
não-relacionadas do §96 (família `"pr"` removida numa limpeza
paralela do usuário).

**Fora do automatizável**: usuário confirma em jogo que agora consegue
acessar/ver o terreno que antes ficava "fora do mapa" nas partes mais
largas de `map_1`.

## 98. (movido) Catalogação de "TX Village Building with Shadow" — construções fatiadas em várias peças (02/09/2026) — ver HISTORICO_FEATURES.md §24

## 99. (movido) Correção do §98 — 1 grupo por construção, não 1 grupo pra todas; id curto, nome só no label (02/09/2026, mesmo dia) — ver HISTORICO_BUGS.md §32

## 100. (movido) Colisão errada em 19 das 104 peças de "TX Village Building with Shadow" — bounding box de blob fundido, não da peça (02/09/2026, mesmo dia) — ver HISTORICO_BUGS.md §33

## 101. (movido) 7 falhas "pré-existentes" na suíte eram regressão real da remoção de "TX Props"/"TX Plant" — 5 testes com id morto + regra de bloqueio de visão de pedra grande perdida (02/09/2026) — ver HISTORICO_BUGS.md §34

## 102. Objeto do object_matrix (construção/árvore) só aparecia quando a câmera alcançava a BASE dele (02/09/2026)

Usuário relatou: objetos maiores que 1 tile (construções recém-
catalogadas no §98/§99, árvores grandes de "TX Village Plant") só
apareciam na tela quando a câmera chegava perto da base deles, mesmo
que o topo do sprite já devesse estar visível bem antes — e piorava
com zoom (menos tiles visíveis na tela). Confirmado com o usuário que
o problema era exatamente esse antes de mexer em qualquer arquivo.

**Causa raiz** (`ui/systems.py::TileRenderSystem`): `get_world_objects()`
e `render_static_objects()` (lógica de corte duplicada nos 2, mesmo
bug nos 2) decidiam quais tiles-âncora considerar varrendo só "tela em
tiles + 2" (margem fixa pequena, mesmo padrão do terreno — mas terreno
é sempre 1×1, objeto não). Objeto é ancorado na BASE (célula do
`object_matrix`) e o sprite cresce PRA CIMA e PRA DIREITA a partir
dali (`scr_y = anchor_y - camera_offset_y - total_h`,
`scr_x = rx*tile_size - camera_offset_x`). Com prédios de até 12 tiles
de altura / 11 de largura no catálogo, qualquer objeto cuja âncora
ainda não tivesse entrado nesses "+2 tiles" nunca era nem considerado
— mesmo com o topo/lado já dentro da tela de verdade.

**Pesquisa antes de codar** (WebSearch, a pedido do usuário): mesma
classe de bug documentada pra Tilemap Renderer da Unity — corte por
grade de tile sempre precisa de margem do tamanho do MAIOR sprite do
conjunto ("Chunk/Cull Bounds"), nunca uma constante pequena arbitrária.
Confirma que a correção não é workaround local, é o padrão esperado
pra esse tipo de renderer.

**Fix**: `_object_scan_margin_tiles()` (novo, módulo `ui/systems.py`)
calcula 1x, a partir do MAIOR `sprite_px_w`/`sprite_px_h` de
`OBJECT_MAPPING` inteiro (nunca hardcoded — se um asset maior for
catalogado depois, a margem se ajusta sozinha), quantas colunas extras
à ESQUERDA e linhas extras ABAIXO a varredura precisa somar à janela
ingênua. Só essas 2 direções importam: um objeto ancorado à direita ou
acima da tela só cresce pra MAIS longe dela, nunca pra dentro.
`TileRenderSystem._object_visible_tile_range()` (método novo,
substituindo o cálculo duplicado nos 2 métodos — ponto único de
verdade) aplica a margem. `TileRenderSystem.render()` (terreno, sempre
1×1) não muda — a margem só faz sentido pra objeto.

**Custo de performance**: mais tiles varridos por frame (só na faixa
de margem, não a área toda) — decisão explícita de não otimizar isso
"no escuro" agora (`CLAUDE.md`, disciplina de performance); se virar
gargalo medido depois, profiling decide o próximo passo.

**Testes**: `tests/test_object_scan_margin.py` (novo) — margem bate
com o maior objeto do catálogo; objeto alto ancorado abaixo da tela
mas com topo visível aparece; objeto largo ancorado à esquerda mas com
lado direito visível aparece; objeto genuinamente fora de alcance (nem
com margem) continua cortado nos 2 casos; `render_static_objects` usa
o mesmo método sem lançar. Revert-to-confirm: com
`_object_scan_margin_tiles()` forçado a `(0, 0)`, os 3 testes que
dependem da margem falharam exatamente como esperado (o de "fora de
alcance" continuou passando, confirmando que não virou "sempre
aparece"). Suíte completa rodada sem regressão nova.

**Validado em jogo pelo usuário (03/09/2026)**: confirmado com câmera
perto de prédio/árvore alto, inclusive com zoom.

## 103. (movido) Recatalogação de "TX Village Building with Shadow" v2 — usuário editou os 4 PNGs de apoio, ids viraram sequenciais "vb001".."vb097" (03/09/2026) — ver HISTORICO_FEATURES.md §25

## 104. Aba Objetos do God Mode não mostrava NENHUMA construção depois do §103 (03/09/2026, mesmo dia)

Usuário reportou: "nenhuma construção está aparecendo na aba de
edição (F10)", já tinha reiniciado o servidor 2x sem resultado —
descartando de cara "cache/precisa reiniciar" como causa.

**Causa raiz**: `engine/tileset.py::group_tile_ids()` — função ponto
único de verdade que `ui/god_mode.py` usa pra saber quais ids
pertencem a um grupo (painel da aba Objetos E pincel aleatório) —
montava o id na mão com `f"{prefix}_{entry[0]}"`, **sem saber da flag
nova `no_prefix_separator`** adicionada no §103 pro id sequencial
puro. Pra qualquer grupo de "TX Village Building with Shadow", isso
calculava `"vb_001"`, `"vb_002"`... — nunca `"vb001"`, que é o id
REAL cadastrado por `_register_catalog_tiles` (que já sabia da flag
desde o §103). `ui/god_mode.py:694/703-706` filtra
`OBJECT_SHEET_TILE_MAP` por esse set de ids calculado — nenhum
`"vb_00N"` bate com nenhum tile real, `family_tiles` fica vazio pra
TODOS os 30 grupos da família, `if not family_tiles: continue` pula
cada um — construção nenhuma aparece, sem erro nenhum no console
(silencioso).

**Por que passou despercebido no §103**: a suíte completa não tem
teste que exercite o painel visual do God Mode chamando
`group_tile_ids` numa família REAL com `no_prefix_separator=True` —
só existia 1 teste de `group_tile_ids` (`TestGroupTileIds`,
`tests/test_tileset_color.py`), escrito ANTES da flag existir, cobrindo
só o caminho antigo (com `"_"`). Confirma a regra do projeto:
"corrigido" exige reproduzir o sintoma relatado — aqui, a única forma
de ter pego isso ANTES do usuário reportar seria testar
`group_tile_ids` explicitamente com a flag nova, não só o registro de
tile (`_register_catalog_tiles`, que já tinha teste indireto via
`discover_object_sheet_tiles`).

**Fix**: `group_tile_ids()` ganhou o mesmo parâmetro
`group.get("no_prefix_separator", False)`, espelhando EXATAMENTE a
lógica de `_register_catalog_tiles` — as 2 funções agora concordam
sempre, por construção, não por sorte. Nenhuma outra família afetada
(default `False`, comportamento antigo intacto).

**Testes**: `tests/test_tileset_color.py::TestGroupTileIds` ganhou 2
casos — `no_prefix_separator=True` produz o id sem `"_"` (reproduz o
bug exato), e o caminho antigo (flag ausente) continua com `"_"`
normalmente (regressão). Revert-to-confirm: com o fix desligado, o
teste novo falhou exatamente como esperado (`'vb_001'` vs `'vb001'`).
Suíte completa sem regressão nova.

**Lesson**: quando um valor é derivado por 2 funções DIFERENTES a
partir do mesmo dado de origem (aqui: "qual é o id de verdade de uma
peça catalogada"), as 2 têm que ler a MESMA config, não reimplementar
a mesma fórmula em paralelo — é exatamente a classe de bug que "ponto
único de verdade" existe pra evitar, e mesmo assim aconteceu porque
`group_tile_ids` já ERA o ponto único de verdade antes da flag nova
existir, só ninguém atualizou o SEGUNDO ponto de leitura da mesma
regra quando o PRIMEIRO ganhou uma opção nova.

## 105. (movido) Rastreio de objetivo de quest no mapa/minimapa — desenho e 1ª implementação (03/09/2026) — ver HISTORICO_FEATURES.md §26

## 106. Recompensa de item de quest nunca tocava o Inventory de verdade do servidor (04/09/2026)

Usuário reportou: pegou a espada de recompensa de "bem_vindo_guerreiro",
equipar não mudou a velocidade de ataque, e ao deslogar/logar de novo a
espada tinha sumido.

**Causa raiz**: `server/session.py::_handle_quest_turn_in` — comentário
no próprio código já admitia a decisão: "*Servidor não toca no Inventory
ECS aqui — mesmo padrão de LOOT_RESULT: o cliente é quem materializa o
item na bag local, e o INV_SYNC periódico do cliente mantém o mirror do
servidor atualizado depois*". Só que essa premissa é FALSA especificamente
pra este caminho — comparado com loot (`server/loot_processor.py`, toca o
Inventory do servidor direto), trade (`server/trade_processor.py:229`,
idem), forja/reciclagem (`server/world_server.py`, idem), a recompensa de
quest era o ÚNICO caminho que só mandava o item pro cliente MOSTRAR, sem
nunca escrever no Inventory real. E o cliente nunca reagia mandando
INV_SYNC/SAVE_STATE de volta depois de receber `INVENTORY_UPDATE` (ao
contrário de compra/venda/forja/reciclagem, que chamam
`_send_save_state()` explicitamente logo depois de conceder item) — a
premissa "o INV_SYNC periódico mantém sincronizado" não correspondia a
nenhum código real.

**Por que os 2 sintomas exatos**: `equip_item_from_inventory`
(`server/world_server.py`) NUNCA confia no item mandado pelo cliente — só
na POSIÇÃO, lida do Inventory AO VIVO do servidor (decisão de segurança
deliberada, documentada). Como o servidor nunca tinha a espada, a posição
que o cliente mandava não correspondia a nada real lá (fora do índice ou
apontando pra outro item) — `equip_item_from_inventory` retorna `None`
("no-op silencioso"), NENHUM erro é mandado de volta, e o cliente já tinha
mutado o estado LOCAL de forma otimista (equipou visualmente antes mesmo
de saber se o servidor aceitou) — daí "equipei mas não mudou nada". No
login seguinte, o servidor recarrega/persiste a partir do PRÓPRIO
Inventory (nunca teve a espada) — item nunca existiu do lado que manda,
"some".

**Fix — ponto único de verdade novo**: `WorldServer.grant_items_to_player
(player_eid, entries)` (`server/world_server.py`, perto de
`equip_item_from_inventory`) — escreve direto no Inventory AO VIVO do
servidor (mesma mecânica de empilhar/caber que loot já usava, extraída
pra `LootProcessorMixin._stack_or_add_item` e reaproveitada por loot E
por esta função nova, nunca duplicada). `_handle_quest_turn_in` passou a
chamar isto em vez do loop que só serializava sem tocar Inventory.
Qualquer sistema de recompensa NOVO (achievement, recompensa diária, o
que for) — decisão explícita do usuário pra evitar que essa MESMA classe
de bug se repita num sistema futuro — é obrigado a passar por esta função
em vez de reimplementar `inv.items.append` à parte (registrado em
`CLAUDE.md`, seção "pontos únicos de verdade").

**Verificado, não presumido**: conferido com o usuário se trade já estava
certo antes de propor o fix — sim, `trade_processor.py:229` já escreve
direto no Inventory do servidor, sem bug ali.

**Testes**: `tests/test_quest_turn_in.py::
test_item_concedido_entra_de_verdade_no_inventory_do_servidor` — NOVO,
verifica o que os testes anteriores desse arquivo deixavam passar batido
(eles só checavam a MENSAGEM `INVENTORY_UPDATE`, nunca o estado real do
Inventory do servidor): item aparece de fato no `Inventory` vivo do
servidor E `equip_item_from_inventory` (por posição real) consegue
equipar de verdade, refletindo em `Equipment.slots["mainhand"]`.
Revert-to-confirm: código antigo restaurado temporariamente, teste falhou
exatamente como o sintoma relatado (`0 != 1` itens no Inventory real).
Suíte completa (1467 testes, incluindo os 15 pré-existentes de
`test_quest_turn_in.py` e 50 de loot) sem regressão.

## 107. (movido) Rastreio de quest só considerava o 1º spawn_zone de um mob com vários pelo mapa (04/09/2026, mesmo dia) — ver HISTORICO_BUGS.md §35

## 108. (movido) Interação com NPC não priorizava quest completável quando o objetivo pendente era "falar com ESTE NPC" (04/09/2026) — ver HISTORICO_BUGS.md §36

## 109. (movido) `dummy_id` removido — boneco de treino tratado igual mob no rastreio de quest (04/09/2026) — ver HISTORICO_BUGS.md §37

## 110. (movido) Opacidade do preenchimento reduzida pra 25% (04/09/2026, mesmo dia) — ver HISTORICO_FEATURES.md §27

## 111. (movido) Velocidade de ataque não atualizava depois de relogar (04/09/2026) — ver HISTORICO_BUGS.md §38

## 112. Personagem oscilava entre alvo travado e NPC clicado (04/09/2026)

Usuário reportou (comportamento, não crash): atacando um alvo travado
(ex: boneco de treino), clicar com o direito num NPC amigável fazia o
personagem ficar andando em zigue-zague — em direção ao NPC, depois de
volta ao alvo antigo, repetindo. Usuário já suspeitava corretamente
que o alvo antigo não estava sendo "solto" de algum jeito ao clicar no
NPC, e pediu cuidado explícito pra não quebrar o kite do arqueiro
(clicar no chão pra se afastar sem perder o alvo).

**Causa raiz**: clicar num NPC amigável é consumido INTEIRO pelo
sistema daquele NPC (`TrainerSystem`/`BlacksmithSystem`/
`QuestDialogSystem`/`ShopSystem`) — o evento nunca chega em
`MouseTargetingSystem` (`game.py` filtra o `MOUSEBUTTONDOWN` inteiro
do frame quando `_right_click_consumed` de qualquer um desses 4 fica
True). Clicar no CHÃO, por outro lado, passa por
`MouseTargetingSystem`, que liga `CombatState.chase_suppressed = True`
— flag client-side que só pausa o auto-walk de volta ao alvo travado,
SEM tocar em `target_entity_id`/`is_pursuing` (é exatamente o que
permite o kite do arqueiro: alvo continua selecionado, combate
continua, só o "andar sozinho de volta" para). Nenhum dos 4 sistemas
de NPC ligava essa mesma flag ao mandar o player andar até o NPC (ou
ao abrir o diálogo direto, já adjacente) — o auto-chase (`ui/systems.py
::PlayerInputSystem._process_target`) continuava chamando
`_auto_move_step` de volta pro alvo antigo TODO frame em que
`is_pursuing and not chase_suppressed`, competindo com o
`PlayerAutoMove.ground_target` que o sistema do NPC também setava no
mesmo frame — daí o zigue-zague.

**Achado extra durante a investigação**: os 4 sistemas tinham cada um
sua PRÓPRIA cópia quase idêntica do cálculo "acha o tile adjacente ao
NPC mais perto do player e manda o PlayerAutoMove pra lá"
(`TrainerSystem._walk_to`, `BlacksmithSystem._walk_to`,
`QuestDialogSystem._walk_to_npc`, `ShopSystem._walk_to_merchant`) —
nenhuma tinha o passo de suprimir a perseguição, e qualquer 5º tipo de
NPC futuro reimplementaria o mesmo cálculo (e o mesmo esquecimento) de
novo. Decisão do usuário: centralizar numa função só em vez de
corrigir as 4 cópias separadamente.

**Fix — ponto único de verdade novo**: `ui/npc_interaction.py`:
- `suppress_chase(world, player_entity)` — liga `chase_suppressed`,
  NUNCA toca `target_entity_id`/`is_pursuing`.
- `walk_to_npc(world, player_entity, player_tile, npc_tile)` — mesmo
  cálculo de tile adjacente que as 4 cópias já faziam (comportamento
  de movimento IDÊNTICO, zero mudança aí — só a resolução do tile do
  NPC continua em cada sistema, já que cada um tinha uma variação
  pequena TileMovement-vs-Position que não valia a pena arriscar
  unificar também), mais a chamada a `suppress_chase` no final.

Os 4 sistemas passam a chamar essas duas funções — nas DUAS
ramificações de cada um (já adjacente → abre direto E precisa andar →
`_pending_*_id` + caminhada), removendo as 4 cópias de `_walk_to*`
como código morto.

**Por que não afeta o kite do arqueiro** (preocupação explícita do
usuário): `chase_suppressed` já é — desde antes desta mudança — a
flag usada exatamente pra esse caso (clique no chão pra se afastar
atirando sem perder o alvo). Esta correção só passa a ligar a MESMA
flag num gatilho novo (clicar num NPC), nunca mexendo em
`target_entity_id`/`is_pursuing`, que são os únicos campos que
realmente controlam troca/perda de alvo. Comportamento de troca de
alvo (clicar noutro inimigo) e de kite (clicar no chão) ficam 100%
intocados.

**Testes**: `tests/test_npc_interaction_chase_suppress.py` (9 casos)
— função compartilhada isolada (seta `ground_target` certo, liga
`chase_suppressed`, NUNCA mexe em `target_entity_id`/`is_pursuing`,
não quebra sem `PlayerAutoMove`) + 1 teste de integração por sistema
(`TrainerSystem` com 2 casos — já adjacente e precisando andar —,
`BlacksmithSystem`, `QuestDialogSystem`, `ShopSystem`) reproduzindo o
cenário exato relatado (alvo travado + clique num NPC de cada tipo).
Revert-to-confirm: corpo de `suppress_chase` esvaziado temporariamente
— 7 dos 9 testes falharam exatamente como esperado (os 2 que não
dependem da flag continuaram passando), restaurado. Suíte completa:
1485 passed.

## 113. Teste de carga com bots reais acha 2 causas de travamento — prefiltro de IA e gc.collect() manual (05/09/2026)

Primeiro uso real de `tools/loadtest/` (300 bots, servidor e bots na
mesma máquina, jogador real logado de outra máquina na rede) travou o
cliente do jogador real por ~3-4 minutos. `server_perf.log` não mostrava
nada catastrófico no tick em si (34-64ms, acima do orçamento de 33ms mas
não um pico isolado gigante) — usuário corretamente rejeitou a hipótese
inicial de "culpa de disputa de CPU com o Windows/processo de bots" e
pediu prova de causa raiz em código antes de aceitar qualquer
explicação ambiental, mesmo com um teste reduzido (50 bots parados no
spawn, batendo só no boneco de treino — sem andar livre, sem agro de
mob de verdade) ainda travando.

**Causa 1 (travamento de minutos, achada primeiro)**: 294/300 contas
recém-provisionadas relatavam `chars=0` no primeiro LOGIN da rampa de
conexão do `swarm.py` (lote de 8 a cada 1s), mesmo já tendo personagem
criado com sucesso pelo `provision.py` minutos antes — cada bot entrava
num loop de reconexão com backoff exponencial (até 30s) tentando
recriar o personagem, por vários minutos, até estabilizar. Reproduzido
e CONFIRMADO por eliminação: com rampa lenta (5 bots a cada 10s), as
mesmas 300 contas conectaram 100% limpo, zero `chars=0`, zero
reconexão — aponta pra contenção de escrita no SQLite (single-writer)
sob rajada de registro/criação de personagem simultânea, não pra
qualquer coisa errada nas contas em si. Mitigação de uso (não de
código, por ora): sempre subir `swarm.py` com rampa gradual
(`--batch-size`/`--batch-interval-s`), nunca o default agressivo, pra
qualquer teste de carga futuro. Investigação de fundo (por que o SQLite
sob rajada de escrita concorrente causa isso, e se afeta cadastro real
de jogador em pico de divulgação/lançamento) FICA EM ABERTO — fora de
escopo desta sessão, só mitigado no uso da ferramenta de teste.

**Causa 2 (travamento residual mesmo com poucos bots parados)**: dois
achados de código, confirmados por leitura direta (nunca só inferidos
do log) + pesquisa externa antes de codar (regra do projeto):

1. **`EnemyAISystem._active_mobs_this_tick` (`engine/world_systems.py`)**
   perguntava "quais mobs estão perto de mim" (`SpatialHash.nearby()`)
   **uma vez por JOGADOR**, mesmo quando vários jogadores estão
   aglomerados no MESMO lugar (spawn, cidade, evento — cenário comum de
   MMO real, não só teste artificial). Confirmado em `engine/utils.py`:
   `SpatialHash.nearby()` depende só de `tile // cell_size`, nunca da
   posição exata — 2 jogadores na mesma célula (20×20 tiles,
   `cell_size == SLEEP_RADIUS_TILES`) recebem resultado IDÊNTICO, então
   repetir a consulta por jogador é trabalho puro desperdiçado quando
   agrupados. Pesquisa externa (broad-phase/spatial-hash em jogos)
   confirma célula-por-observador-único como o padrão certo pra esse
   caso. **Fix**: cache `_nearby_cache` por célula dentro do próprio
   loop de `_active_mobs_this_tick` — 1 chamada a `nearby()` por célula
   ÚNICA ocupada por jogador, não 1 por jogador; o crivo de distância
   EXATA (que precisa da posição real, não só da célula) continua
   rodando por jogador, sem mudança de resultado.
2. **`gc.collect()` manual periódico** (`WorldServer.run()`,
   `server/world_server.py`, a cada ~10s) — `gc.disable()` já rodava
   desde antes (`server/main.py`, comentário already dizia "evita
   pauses de 50-200ms") pra evitar coleta automática imprevisível, mas
   o `collect()` manual que substitui ela ainda escaneava TODO objeto
   rastreado vivo desde o boot (mapas/catálogos/entidades — nunca viram
   lixo, mas são revisitados em toda coleta full mesmo assim), 20-50ms
   de pausa a cada disparo, medido em log real. Suspeita relacionada já
   estava documentada e nunca fechada em §30 (12/08/2026, diagnóstico
   de `gc.get_count()`/`gc.isenabled()` instrumentado mas nunca disparou
   em nenhum log desde então — item considerado ainda aberto, não
   descartado). Pesquisa externa confirma o padrão certo (usado por
   Instagram/Rippling/Close.com): `gc.collect()` uma vez após o boot
   terminar, seguido de `gc.freeze()` — move tudo que existe até ali pra
   uma geração permanente que nenhuma coleta futura revisita. **Fix**:
   `server/main.py` chama `_gc.collect()` + `_gc.freeze()` logo depois
   do `WorldServer` construído (mundo carregado) e ANTES de
   `websockets.serve` aceitar qualquer conexão — nunca antes (mundo
   ainda não existiria pra congelar) nem depois (arriscaria prender
   estado de player real no baseline congelado).

**Testes**: `tests/test_ai_prefilter_spatial_index.py::
TestPrefilterNearbyDedupByCell` (3 casos — 5 players na mesma célula
geram exatamente 1 chamada a `nearby()`, não 5; resultado continua
idêntico ao algoritmo linear de referência mesmo aglomerado; players em
células diferentes continuam gerando 1 chamada por célula, sem fundir
indevidamente). `tests/test_gc_freeze_startup.py` (2 casos — ordem
`collect()`→`freeze()`→`websockets.serve` provada via
`server.main.main()` real com `websockets.serve`/`WorldServer.run`
mockados; `gc.disable()` do módulo continua em vigor). Revert-to-confirm
nos dois: dedup por célula desligado → teste de contagem falha 5≠1
como esperado; `collect()`/`freeze()` removidos → teste de ordem falha
"collect não encontrado" como esperado. Suíte completa (1490 testes)
rodada 1x, tudo verde.

**Verificação pendente** (fora do automatizável, decisão do usuário):
novo teste de carga com 50 bots (rampa gradual) medindo
`server_perf.log` novo contra a baseline já salva
(`server_perf_bkp.log`, renomeada pelo usuário antes desta correção)
pra confirmar ganho real, não só teste sintético isolado passando.

## 114. 4 correções de performance validadas por 2 rodadas de auditoria multi-agente (05/09/2026)

Depois de §113, usuário pediu investigação mais profunda com 3 agentes
especialistas em paralelo (arquiteto de MMORPG tile-based, engenheiro
de incongruências, QA de performance), cada um analisando os 10
sistemas mais custosos medidos em log real. Rodada 2 (mesmo dia):
metodologia mais restrita — prioridades decididas por mim, agentes só
validam/aprofundam, cada um resolvendo de forma independente 1
divergência que sobrou da Rodada 1. Comparação completa das 2 rodadas
em `arquitetura/analise_performance_rodada1.md`.

**Achado principal da comparação**: a Rodada 2 mudou a conclusão em 3
dos 5 itens candidatos — 2 correções propostas na Rodada 1 tinham
bug real se implementadas ao pé da letra (confirmado por leitura de
código antes de codar, nunca só aceito de prompt de agente):

1. **`_tick_bush_target_clear` guard morto** (`server/world_server.py`)
   — `self._bush_zones_by_map[key] = spawn_points.get("bush_zones", [])`
   grava uma entrada por MAPA mesmo com lista vazia; o guard
   `if not self._bush_zones_by_map: return` checava o dict inteiro
   (sempre truthy depois do boot), nunca o conteúdo — nunca disparava
   em produção (nenhum mapa real tem `bush_zones`). A função escaneava
   o mundo inteiro todo tick pra sempre produzir resultado vazio. Fix:
   `if not any(self._bush_zones_by_map.values()): return`.
2. **`eid not in self._player_eids.values()`** (O(n), `dict.values()`
   não é indexado) em 3 call-sites (`ActiveRegen`/`ActiveManaRegen`/
   detecção de "combatente novo" em `post_tick_bookkeeping`) — único
   bug que piorava sozinho com mais jogadores online. Fix: checagem
   por componente `world.get_component(eid, PlayerControlled) is None`
   (O(1), mesmo padrão já usado em `index_build`) — melhor que a
   alternativa original de um `set` compartilhado construído 1x
   (a Rodada 2 achou um bug real nessa alternativa: um bot de
   battleground criado NO MEIO do tick, via `_tick_bg_queue`, ficaria
   fora de um set construído cedo demais e seria registrado por engano
   como mob).
3. **3 varreduras redundantes de `get_entities_with(TileMovement)`**
   no mesmo tick (`index_build`, `pre_tick_snapshots`,
   `post_tick_bookkeeping`) — só 2 das 3 são fundíveis com segurança.
   `post_tick_bookkeeping` PRECISA rodar por último e separado: detecta
   "combatente novo" criado por `SpawnZoneSystem`, que roda DENTRO de
   `ai_bundles` — ou seja, DEPOIS de `index_build` — fundir essa
   varredura pra dentro de `index_build` faria o código nunca detectar
   mob spawnado no próprio tick em que nasceu (confirmado em código:
   `ai_bundles`/`SpawnZoneSystem` roda depois de `index_build`, `post_
   tick_bookkeeping` roda depois de `ai_bundles`). Fix: fundidas só
   `pre_tick_snapshots`+`index_build` — `pre_mob_target` passa a ser
   populado dentro do loop de `index_build`, ANTES do `continue` por
   falta de Position/CombatStats (esses 2 nunca foram exigência de
   `pre_mob_target`, só dos outros 3 índices).
4. **`_combat_targets` escaneava `_mob_eids`/`_player_eids` GLOBAIS**
   (todos os mapas/instâncias juntos), sem filtro de mapa — achado como
   divergência não resolvida em §113, CONFIRMADO por 2 agentes
   independentes na Rodada 2. Escalada de gravidade: não é só
   performance — `spell_completion_processor.py` (AoE de gelo) filtra
   alcance só por coordenada de tile CRUA (`chebyshev`), e mapas/
   instâncias diferentes reusam o MESMO espaço de coordenadas, então um
   alvo de outro mapa/instância podia coincidir numericamente "dentro
   do alcance" e tomar dano cruzado — bug de corretude real, não só
   ineficiência. Fix tentado inicialmente (persistir `_combatants_by_
   map`/`_players_by_map` como atributo, sugestão original da Rodada 2)
   foi REVERTIDO ao escrever o teste: quebraria
   `tests/test_faction.py::test_combat_targets_exclui_npc_amigavel_de_aoe`,
   que popula `_mob_eids` manualmente sem rodar tick (o índice por-mapa
   só existe depois de um `_tick()` real rodar `index_build`). Fix final,
   mais simples e sem esse risco: `_combat_targets` continua iterando
   `self._mob_eids`/`self._player_eids.values()` (mesma fonte de
   sempre), só ganhou um filtro `get_entity_map(candidato) != get_
   entity_map(caster)` — corrige a CORRETUDE (não mais vazamento entre
   mapas) mas não reduz o custo O(mobs+players) da variedade global
   (otimização de performance completa fica pra depois, precisa
   repensar a fonte de dados sem quebrar o teste existente). Candidato
   sem `MapLocation` (entidade sintética de teste, ex.
   `create_combat_npc`) nunca é excluído só por isso — só exclui quando
   o mapa do candidato é CONHECIDO e diferente do caster.

**Testes**: `tests/test_bush_stealth.py::TestBushTargetClearGuard` (3
casos), `tests/test_consumables.py::TestActiveRegenPlayerCheckIsComponentBased`/
`TestPostTickBookkeepingNeverMisclassifiesPlayerAsMob` (3 casos),
`tests/test_server.py::TestPreMobTargetSurvivesFusionWithIndexBuild` (2
casos — inclui o caso-limite de mob sem Position/CombatStats),
`tests/test_faction.py::TestCombatTargetsScopedByMap` (4 casos).
Revert-to-confirm em TODOS os 4 (cada um confirmado: teste falha sem o
fix, exatamente como esperado). Suíte completa: 1502 passed (1490 +
12 novos).

**Verificação de ganho real CONCLUÍDA** (250 bots, `--ratio 230:10:10`,
rampa gradual) — comparação justa na mesma escala de jogadores (~250-267)
entre o log antigo (sem nenhum fix) e o novo: `skill_requests` −99,6%,
`bush_target_clear` −99,7%, `pre_tick_snapshots` −79%,
`regen_and_status_ticks` −59%, `post_tick_bookkeeping` −15%,
`index_build` −12% (esperado — absorveu o trabalho que
`pre_tick_snapshots` perdeu). Tempo médio de tick a ~250-267 players:
40.27ms (acima do orçamento) → 25.1-25.7ms (dentro do orçamento). %
de ticks travados a 225-250 players: 60.33% → 4.33%. A 250-275 players
o travamento ainda sobe bastante (33.57%) — confirma o teto do
prefiltro de IA já sinalizado pelo Agente Arquiteto (Rodada 1), não é
regressão nova. Achado ao vivo durante o teste (fora do plano): usuário
relatou travamento real enquanto os 250 bots estavam ativos — investigado
na hora, causa confirmada foi o teto do prefiltro (não um bug novo); um
segundo sintoma (ping alto persistindo mesmo depois dos bots saírem) não
teve causa server-side encontrada, resolveu com reconecte limpo do
client. Ver `arquitetura/medicao_antes_depois_fixes.md` (tabela completa)
e o dashboard visual: https://claude.ai/code/artifact/24895768-ecf9-4256-98d9-c18c882f11bc

## 115. (movido) Bot de teste de carga ganha pathfinding real, loot, skill à distância e travessia de bush (05-06/09/2026) — ver HISTORICO_FEATURES.md §28

## 116. Rampa de conexão mais lenta expõe `skill_requests` a ~50% do tick — auditoria de 3 agentes acha e corrige a causa raiz (06/09/2026)

Depois do §115 (bots lutando de verdade, espalhados, com skill de longo
alcance), usuário pediu um novo teste de 250 bots com rampa MAIS LENTA
(5 bots a cada 15s, em vez de 5 a cada 1s) — objetivo: mais tempo pra
comparar desempenho em cada faixa de contagem de jogadores. Usuário
interrompeu o teste no meio (bots todos acumulados no cemitério — causa
identificada como grades no mapa, corrigida pelo próprio usuário via
edição direta do mapa) e pediu análise dos dados coletados.

**Achado real, mais grave que o teto de `EnemyAISystem:prefiltro` já
catalogado (§113/114)**: no patamar de 250 jogadores, tick médio subiu
pra ~60,5ms (orçamento é 33ms) — 73% dos ticks "lentos", bem pior que os
33,57% medidos no §114. Breakdown por sistema (top-10, consistente em 4
blocos de 300 ticks): `skill_requests` sozinho é **49,9% do tick**
(avg=30,2ms, p99=100ms) — o dobro do 2º colocado (`ai_bundles`/prefiltro,
21,9%, já catalogado, sem mudança). Nunca tinha aparecido antes porque os
bots de teste, até o §115, ficavam presos lutando só contra o boneco de
treino — volume de skill usada em paralelo era baixo. Agora que lutam de
verdade, espalhados, com skill de longo alcance, o volume de CAST_SKILL
por tick no servidor inteiro disparou, e um padrão que sempre existiu
(mas nunca doeu) virou o gargalo dominante.

**Metodologia** (pedido explícito do usuário, mesmo padrão de rigor do
§114): montar a tabela top-10 primeiro; escalar 3 agentes especialistas
em paralelo — arquitetura (comparação com Veloren/AzerothCore),
qualidade de código (achar bug de eficiência concreto, código citado) e
coesão de sistemas vs. mercado (o padrão está alinhado com o resto do
PRÓPRIO projeto?) — cada um lendo o código de verdade antes de concluir;
notar cada diagnóstico e mandar de volta pra aprofundar se não fosse nota
máxima. Os 3 agentes voltaram com diagnóstico de profundidade real
(código citado linha a linha) na primeira passada — nenhum precisou de
retrabalho.

**Causa raiz confirmada pelos 3, com camadas diferentes**: toda vez que
uma skill era usada, `_process_skill_requests`/`_process_spell_cast_
completions` chamavam `_snapshot_combat_targets()` (`server/
world_server.py`) — tirava uma "foto" de HP/StatusEffects de TODO
combatente do mapa do caster, só pra comparar com o "depois" e descobrir
quanto de dano/efeito a skill causou. Achados específicos:
- **Agente de qualidade de código**: pra TODA skill com `cast_time > 0`
  (quase toda magia de mago/arqueiro), essa foto é 100% desperdiçada — o
  handler nessa fase só ENFILEIRA a conclusão, nunca aplica dano. Pior:
  as 4 skills de projétil (Bola de Fogo, Flecha Reiterada, Picada de
  Escorpião, Tiro Repulsivo) pagavam esse custo **2 vezes** (request +
  completion) e o dano de verdade só acontecia numa 3ª chamada
  (`_apply_spell_on_projectile_hit`, disparada por `PROJECTILE_HIT_CS`),
  nunca coberta pelas duas primeiras fotos.
- **Agente de arquitetura + coesão**: o mecanismo certo já existe em
  parte no próprio projeto — `LAST_DAMAGE_OUTCOMES` (`engine/
  core_systems.py`, dict de módulo populado por `apply_damage_core` NO
  MOMENTO em que aplica, técnica de Veloren/AzerothCore já adotada em
  10/08/2026 pro OUTCOME por alvo) só faltava um irmão pro VALOR do
  dano. Comparação com `_resolve_auto_attack` (`server/
  combat_processor.py`) — mesmo diff antes/depois, mas O(1) num único
  alvo já conhecido — prova que "diffar" não é o problema, "diffar
  contra o universo inteiro do servidor" é.
- **Agente de arquitetura**: o próprio `index_build()` já monta, 1x por
  tick, ANTES de `skill_requests` rodar, um índice de combatentes por
  mapa (`_combatants_by_map`) que nunca era reaproveitado.

**5 correções implementadas** (todas aprovadas explicitamente pelo
usuário antes de mexer, 2 delas — itens 4/5 abaixo — por tocarem
mecanismo estrutural/ponto único de verdade):

1. **Pular a foto quando é 100% desperdiçada** — `_process_skill_
   requests` (skill com `cast_time>0`) e `_process_spell_cast_
   completions` (spell de projétil/`tiro_multiplo`) não tiram mais
   snapshot nenhum nesses casos (risco zero, confirmado handler a
   handler que nunca era usado).
2. **`eid in self._player_eids.values()` → checagem de componente O(1)**
   — 8 pontos (`combat_processor.py`, `skill_processor.py` ×2,
   `spell_completion_processor.py` ×4, `world_server.py`) que ainda
   faziam essa checagem O(n) sem o fix já aplicado em outro lugar do
   projeto (05/09/2026, §113) — mesmo padrão `PlayerControlled`
   replicado.
3. **`_combat_targets()` ganhou fast path por mapa** — reusa
   `_combatants_by_map`/`_players_by_map` (promovidos a atributo de
   instância, `None` até o 1º tick real rodar) em vez de varrer
   `_mob_eids`/`_player_eids` do SERVIDOR INTEIRO. A EXISTÊNCIA do
   atributo (não seu conteúdo) é o sinal de "veio de um tick real" —
   fixture de teste que popula `_mob_eids` na mão sem rodar `_tick()`
   (`test_combat_targets_exclui_npc_amigavel_de_aoe`, já frágil numa
   tentativa anterior revertida) nunca vê os índices e cai no caminho
   antigo, sem quebrar.
4. **`LAST_DAMAGE_AMOUNTS`/`LAST_EFFECTS_APPLIED`** (novos, `engine/
   core_systems.py`) — irmãos de `LAST_DAMAGE_OUTCOMES`, populados por
   `apply_damage_core`/`apply_effect` NO MOMENTO em que aplicam.
   `_snapshot_combat_targets()` foi REMOVIDA por completo (0 call sites
   restantes) — os 3 pontos que a usavam (`_process_skill_requests`,
   `_process_spell_cast_completions`, `_apply_spell_on_projectile_hit`)
   agora leem só os alvos REALMENTE tocados, nunca o mapa inteiro.
5. **`_collect_player_effects()` virou dict** (`{eid: [effects]}`, igual
   `_collect_mob_effects` já era) — `server/session.py` fazia 2
   varreduras O(P) POR SESSÃO só pra achar "qual é a minha entrada" e
   "quais são de quem eu vejo"; virou O(1) + O(known_eids) (bounded pelo
   AOI da sessão, não pelo total de jogadores do servidor).

**Gaps de cobertura achados DURANTE a implementação (não pelos 3
agentes)**: revert-to-confirm no item 4 achou 2 buracos reais na suíte —
a suíte inteira passava mesmo com `LAST_EFFECTS_APPLIED` completamente
quebrado (nenhum teste cobria `applied_effects` num alvo NORMAL, só via
um alvo imune que aparecia por outro canal) e mesmo com o dano
REPORTADO de Bola de Fogo sempre 0 no SKILL_RESULT (só existia teste do
HP real da vítima, nunca do valor que o protocolo manda pro cliente).
Novos testes adicionados: `TestNovaCongelanteReportaDanoEEfeitoAplicado`
(`tests/test_immunity_feedback.py`) e
`test_bola_de_fogo_reporta_dano_correto_no_skill_result`
(`tests/test_bg_bot.py`) — os dois confirmados via revert-to-confirm
antes de aceitar.

**Testes**: 3 testes novos de fast-path em `tests/test_faction.py`
(`TestCombatTargetsFastPathAfterRealTick` — índices começam `None`,
resultado idêntico ao caminho antigo, e um bug injetado de propósito
confirmou que os testes pegam regressão de verdade) + os 2 acima.
Suíte completa (1507 testes) verde. Revert-to-confirm em `LAST_DAMAGE_
AMOUNTS`, `LAST_EFFECTS_APPLIED` (2x) e no dano reportado de projétil —
todos os 4 bugs injetados foram pegos por algum teste antes do commit.

**Pendente** (fora do escopo desta sessão): rodar de novo o teste de 250
bots (rampa lenta) que motivou esta investigação, agora com os 5 fixes
aplicados — servidor precisa reiniciar pra carregar o código novo.
Comparação justa (mesma contagem de jogadores, mesmo comportamento de
bot) fica pro usuário decidir quando rodar.

**Resultado real, medido depois** (mesmo dia): reiniciado o servidor e
refeito o teste de 250 bots (rampa de 5 a cada 15s). Tick médio caiu de
60,5ms pra 19-23ms (dentro do orçamento de 33ms), % de ticks lentos caiu
de 73% pra 6,1%, `skill_requests` caiu de 49,9% do tick pra 1,7% em
MÉDIA — as 5 correções funcionaram como esperado. Restaram picos
severos ocasionais especificamente em `skill_requests` (até 197ms,
6x o orçamento, 78 ocorrências em ~22 minutos) — investigados no §117.

## 117. Rajada de 242 `CAST_SKILL` no mesmo tick — thundering herd na ferramenta de teste + lacuna real de rate-limit no servidor (06/09/2026, mesmo dia)

Usuário notou que os picos residuais de `skill_requests` (§116) mereciam
investigação (não só aceitar como "resíduo aceitável"). Instrumentação
temporária em `_process_skill_requests` (contagem de `len(requests)` +
tempo total, removida depois de confirmar) mediu, num pico real:

```
tick#3984 n_requests=242 dt=79.4ms avg_por_req=0.33ms
```

**Custo por pedido individual confirmado ótimo (0,2-0,4ms) — o problema
é volume: até 242 dos 250 bots mandando `CAST_SKILL` no MESMO tick de
33ms.** Auditoria de 3 agentes especialistas (arquitetura/qualidade de
código/coesão vs mercado), mesmo padrão de rigor do §114/§116, achou a
cadeia causal completa, nota máxima nos 3 relatórios sem retrabalho:

1. **Gatilho raiz — recorrência do §113, ainda aberto**: contenção de
   escrita no SQLite numa rajada de conexão faz bots falharem login.
2. **`tools/loadtest/bot.py::_reconnect_backoff`** usava
   `2 ** tentativa` **sem jitter nenhum** — todo bot que falha junto
   reconecta no MESMO segundo, sempre, em toda tentativa seguinte
   (thundering herd clássico — o motivo de "exponential backoff with
   jitter" existir em qualquer SDK de rede maduro, AWS/gRPC inclusive).
3. **`tools/loadtest/swarm.py::_staggered_run`** também não tinha jitter
   DENTRO do lote — bots do mesmo lote (`(i // batch_size) *
   batch_interval_s`, idêntico pra todos) já nascem sincronizados.
4. **A mudança "pensar imediato ao (re)conectar"** (mesma sessão, §115/
   commit `a10dfe3`) transforma reconexão sincronizada em AÇÃO
   sincronizada — inclusive `CAST_SKILL`.
5. Achado extra dos agentes (não pedido, correlacionado): `server/
   session.py::_spawn_and_start`/`WorldServer.spawn_player` fazem
   trabalho 100% síncrono (parse de JSON, loop O(sessões) de AOI) sem
   nenhum `await` no meio — numa rajada de login, isso atrasa o próprio
   loop principal, deixando a fila de skill_requests acumular ainda
   mais antes do tick processar. Não mexido agora (mudança arquitetural
   — ECS não é thread-safe, precisaria de aprovação separada).
6. `_process_skill_requests` em si CONFIRMADO limpo — O(n) puro, sem
   nada quadrático, sem snapshot residual. O código de hoje nunca foi o
   problema; a causa é 100% volume concentrado por sincronização
   externa ao servidor.

**Veredito dos 3 agentes, convergente**: a causa IMEDIATA (78 picos)
é majoritariamente artefato da ferramenta de teste (itens 2+3+4) — um
jogador humano de verdade nunca reconectaria com essa correlação de
relógio. MAS existe uma lacuna arquitetural real e independente:
`_process_skill_requests`/`queue_skill` não tinham NENHUM orçamento ou
limite, ao contrário de `MAX_PATHFINDS_PER_FRAME` (já existe pra IA de
mob/minion, mesmo princípio documentado em CLAUDE.md — "throttling
reduz frequência mas não impede uma chamada pesada travar tudo"). Um
cliente malicioso hoje poderia inflar a fila sozinho, sem depender de
reconexão nenhuma.

**2 correções implementadas, ambas aprovadas explicitamente pelo
usuário**:

1. **Jitter na ferramenta de teste** — `tools/loadtest/swarm.py::
   _staggered_run` (jitter uniforme dentro do lote, usa a RNG do
   PRÓPRIO bot) e `tools/loadtest/bot.py::_reconnect_backoff` ("full
   jitter" estilo AWS: `uniform(0, teto)` em vez do teto fixo). Deixa o
   teste mais realista, sem tocar código de jogo.
2. **Rate-limit por PLAYER (nunca por tick global) em `_process_skill_
   requests`** — `WorldServer.MAX_SKILL_REQUESTS_PER_PLAYER_PER_TICK =
   3` (folga generosa acima de qualquer sequência humana legítima num
   tick de 33ms), contador `_skill_requests_this_tick_by_player`
   (dict de instância, zerado no início de `_process_skill_requests`,
   junto do dreno de `_pending_skill_requests`). `queue_skill()`
   descarta o excesso ANTES de enfileirar — nunca paga o custo de
   processar um pedido que já sabe que vai estourar o teto. Teto POR
   CONEXÃO, nunca global — um teto global penalizaria N jogadores
   legítimos agindo juntos (raid, início de BG) tanto quanto 1 cliente
   malicioso sozinho; essa distinção foi o ponto central do veredito
   dos 3 agentes.

**Fora de escopo, decisão do usuário se quiser revisitar**: §113
(contenção SQLite) continua aberto — hoje foi só mais uma recorrência,
não uma resolução; e o achado extra do item 5 (`_spawn_and_start`
síncrono) não foi mexido (mudança arquitetural que toca ECS).

**Testes**: `tests/test_server.py::TestSkillRequestRateLimitPorPlayer`
(3 testes — excesso do mesmo player é descartado, outro player não é
afetado, contador reseta a cada tick real), revert-to-confirm confirmou
os 3 pegando a regressão. Suíte completa (1510 testes) verde.

## 118. Trava com ZERO jogadores — 2 rodadas de agentes, 1 correção errada quase implementada, causa raiz real achada na 2ª rodada (06/09/2026, mesmo dia)

**Lição de processo primeiro, porque ela é o motivo desta seção
existir**: a 1ª rodada de 3 agentes (investigando por que o servidor
travava sozinho, players=0, minutos depois do teste de bots) recebeu
nota 10/10 dos 3 relatórios só por profundidade de LEITURA de código —
nenhum tinha efetivamente implementado e rodado a suíte de teste contra
a própria correção proposta. Tentei aplicar a correção do agente de
arquitetura (early-return em `SpawnZoneSystem.update()` quando não há
player no mapa) e ela quebrou 14 testes reais na hora — usuário
questionou corretamente por que dei nota máxima sem essa validação.
**Regra nova a partir de agora**: diagnóstico de agente só fecha com
nota máxima se vier acompanhado de "implementei e rodei a suíte contra
isto", nunca só "li o código e faz sentido".

**2ª rodada**: levei as DUAS soluções da 1ª rodada (lock+WAL pro SQLite;
early-return pro SpawnZoneSystem) pros mesmos 3 agentes, com a exigência
explícita de implementar de verdade e rodar a suíte completa antes de
reportar. Resultado, convergente nos 3:

**Achado A (SQLite) — confirmado, implementado, mas com uma correção
importante ao que a 1ª rodada assumiu**: lock (`asyncio.Lock`, `server/
session.py::SessionManager._save_lock`) serializando `_persist_character`
(mesma disciplina que `_autosave_all` já tinha por acidente) +
`PRAGMA journal_mode=WAL` (`server/auth.py::_get_conn`). Um agente
mediu com um script de carga real (250 saves concorrentes, payload
realista) e achou algo que ninguém tinha visto: **WAL sozinho, SEM o
lock, piora a contenção** (2 de 5 rodadas deram `database is locked` de
verdade, latência disparou de 2.5s pra 10-16s) — WAL não elimina "1
escritor por vez", só adiciona overhead de coordenação que piora sem
serialização por cima. Lock sozinho também teve instabilidade
(stalls de 13-18s, suspeita de Windows/Defender interceptando o churn
de arquivo do modo journal DELETE). **Lock + WAL juntos foi a única
combinação estável nas 5 rodadas** — os dois têm que andar juntos,
nunca um sem o outro. Corrigido comentário no código que originalmente
atribuía a ESTE lock a explicação do "tick lento com 0 players" — não
é essa a causa (ver Achado B), o lock é uma correção de concorrência
válida por mérito próprio, mantida mesmo não sendo a causa do sintoma
investigado.

**Achado B (SpawnZoneSystem) — a correção proposta pela 1ª rodada
estava ERRADA; achada a causa raiz real, diferente**: `engine/
world_systems.py::SpawnZoneSystem.update()` usa `player_tx,ty=(0,0)`
como default quando não há player no mapa — isso é real, mas é **código
morto em produção**: `server/world_server.py:872-873` força
`ACTIVATION_RADIUS = 999999` em toda instância real do sistema, desde o
commit `9a62279` (24/05/2026) — decisão consciente e já documentada
(`ARQUITETURA ONLINE.md` §14, `SISTEMAS_ECS.md`): "o mundo deve existir
independente de conexões". `git log` achou até o commit ANTERIOR
(`182aa4e`, 18/05/2026) que tinha corrigido esse MESMO bug de forma
pontual (pausar zona sem player) — uma semana depois o time decidiu por
algo mais radical (desativar o culling inteiro) em vez de manter aquele
fix. Aplicar o early-return proposto pela 1ª rodada teria regredido
essa decisão de propósito — um servidor recém-iniciado, ou depois de
todo mundo desconectar, ficaria com ZERO mobs até alguém andar 80 tiles
a partir da origem crua do MUNDO (não do spawn de ninguém). Os 14
testes que quebraram estavam certos; a correção é que estava errada.

**Causa raiz real** (achada só na 2ª rodada, por um agente que continuou
investigando depois de descartar o (0,0)): dentro da MESMA função,
`_get_occupied()` fazia sua PRÓPRIA varredura `get_entities_with(
TileMovement)` sobre o MUNDO INTEIRO (todos os mapas), **ignorando** o
parâmetro `tile_movement_by_map` que a função já recebia (índice por
mapa, construído 1x/tick em `index_build`) — 3ª ocorrência da MESMA
classe de bug já catalogada e corrigida antes em `TileValidationSystem`/
`EnemyAISystem` (§34/§35), nunca migrada aqui. Como `ACTIVATION_RADIUS
=999999` mantém TODA zona sempre "ativa", qualquer timer de respawn
vencendo em qualquer mapa dispara esse scan global — e com timers de
90-300s setados durante um teste de 250 bots, isso continua disparando
por vários minutos depois de todo mundo desconectar, batendo exatamente
com o sintoma relatado (`SpawnZoneSystem` = 100% do custo de ticks de
33-93ms, players=0, minutos após o teste). Corrigido trocando as 2
varreduras manuais por uma chamada a `_tile_movement_on_map()` (mesmo
helper canônico, mesmo fallback pra scan direto quando o índice não é
injetado — por isso os 14 testes nunca precisaram de nenhuma mudança).

**Achado extra**: o executor padrão do asyncio nesta máquina
(`os.cpu_count()=4`) só tem 8 workers, não 250 — a suposição inicial de
"250 escritores concorrentes" superestimava a concorrência real (é no
máximo 8-way), ainda que suficiente pra causar contenção real e
comprovável.

**Testes**: suíte completa (1510 testes) rodada por 2 agentes
independentes + por mim mesmo depois de revisar o diff — 0 regressão
nos 3. `tests/test_idle_map_perf.py` (7/7), `tests/test_session.py`
(102+), `tests/test_ai_prefilter_spatial_index.py`/`test_immunity_
feedback.py`/`test_server.py` (101/101) confirmados. Validação de
carga real do lock com teste positivo E negativo (lock-fake provando
que o teste detectaria a ausência do lock de verdade). Limpo do
`data/game.db` real 12 contas de teste (`savelocktst*`) deixadas por um
script de validação de agente.

**Resultado real, medido depois** (mesmo dia): teste de 250 bots
refeito com os fixes do §118 aplicados. Trava com 0 jogadores: **875→11
ocorrências** de tick lento no período ocioso pós-teste, tick médio
ocioso ~8-11ms→~5ms, CPU ociosa ~24-38%→~11-13% — confirmado, resolvido
de verdade. `skill_requests`/rajada de reconexão (§113): não deu pra
confirmar melhora nesse mesmo teste — a rajada de reconexão foi ainda
MAIOR que em testes anteriores (1181 reconexões vs 500-680), então virou
o próximo alvo de investigação (§119).

## 119. §113 fechado de vez — vazamento de conexão no PRÓPRIO bot de teste era a causa real da amplificação, não (só) o SQLite (06/09/2026, mesmo dia)

3 agentes escalados de novo (mesmo requisito de implementar e validar
com teste real, não só diagnosticar) especificamente pro §113 (rajada
de conexão simultânea travando o SQLite, catalogado há tempo, nunca
corrigido de raiz — diferente do §118, que resolveu o problema IRMÃO na
DESCONEXÃO). Os 3 trabalharam concorrentemente NO MESMO working tree
(sem isolamento), editando `server/auth.py`/`server/session.py` ao
mesmo tempo — cada um encontrou o arquivo mudado sob os próprios pés e
reconciliou o trabalho do agente anterior em vez de duplicar. Os 3
convergiram, de forma independente, pra MESMA arquitetura de correção —
sinal forte de que é a escolha certa.

**Achado principal (o que os 3 divergiam de suposição inicial):** a
causa da amplificação (**1181 reconexões pra 250 bots, quase 5x**) não
era majoritariamente contenção de SQLite isolada — era um **vazamento
de conexão no PRÓPRIO `tools/loadtest/bot.py`**. `LoadBot.run()` nunca
chamava `self.client.close()` no caminho de EXCEÇÃO (só no fim natural
do loop, que uma falha nunca alcança) — quando `_login_cycle()` falhava
(timeout de 10s do `recv_one`, `protocol_client.py`, sob contenção real
de banco), o WebSocket antigo ficava pendurado, e o servidor só percebe
via `ping_timeout=60s` (`server/main.py`). Se o LOGIN daquela conexão
abandonada terminasse de processar nesse meio-tempo, a sessão virava
**"zumbi" autenticada** — bloqueando QUALQUER tentativa nova do MESMO
username com `LOGIN_ERROR "already_online"` até o ping-timeout derrubar
o zumbi. Cada rejeição gerava outra reconexão, que TAMBÉM não fechava a
conexão anterior — ciclo auto-alimentado. Medido: pior caso real do
SQLite isolado (250 concorrentes) nunca passou de 3.3-5.5s — bem abaixo
do timeout de 10s do cliente; a escala de tempo real do problema era a
janela de zumbi de até 80s (`ping_interval=20+ping_timeout=60`), não o
busy-timeout de 5s do SQLite.

**Correção implementada** (2 partes, uma em cada lado):
1. **`tools/loadtest/bot.py::run()`** — fecha `self.client` (best-effort,
   nunca mascara a exceção real) tanto no caminho de exceção (antes do
   backoff) quanto no fim natural do loop. Elimina a causa raiz da
   amplificação.
2. **`server/auth.py`** — `_write_lock` (não mais em `SessionManager`,
   que só protegeria quem lembrasse de usar — a mesma "proliferação por
   call site" que o projeto evita pra outros subsistemas): ponto único
   de serialização de escrita, vivendo no módulo que de fato abre a
   conexão, acionado de DENTRO de `register`/`create_character`/
   `delete_character`/`save_character` — qualquer call site futuro
   (rede, `tools/loadtest/provision.py`, script) ganha a proteção de
   graça. `authenticate`/`get_character` (leitura) ficam de propósito
   FORA — LOGIN é o caminho mais sensível a latência da rajada, e
   serializar leitura contra escrita jogaria fora o ganho do WAL bem
   onde mais dói. `SessionManager._save_lock` (do §118) foi REMOVIDO —
   substituído por este, mais amplo e correto.

**Bug sutil achado e corrigido no meio do caminho**: a 1ª versão do
lock (de um dos agentes, ainda em voo quando outro entrou) era um
`asyncio.Lock()` cru de módulo — quebra entre testes que usam
`IsolatedAsyncioTestCase` (cada método roda num event loop NOVO; um
`Lock` só "amarra" ao loop na primeira disputa real, então o 2º teste
que dispute o MESMO lock, loop diferente já fechado, quebra com
`RuntimeError`). Corrigido com `_get_write_lock()` — função que recria
o Lock sempre que o loop mudou desde a última chamada; seguro em
produção (1 processo = 1 loop sempre) e nos testes. Reproduzido
isoladamente antes de corrigir (2 `IsolatedAsyncioTestCase` disputando
o mesmo lock cru — o 2º sempre quebrava).

**Achado extra fora do pedido original**: um dos agentes achou e
corrigiu uma corrida real (TOCTOU) em `_create_character_sync` — 2
`create_character` concorrentes com o MESMO nome, mesma janela entre o
`SELECT` de unicidade e o `INSERT`, ambos passavam sem o lock (2 linhas
duplicadas no banco, violando a unicidade que o próprio docstring da
função promete). Com `_write_lock`, só 1 sucede, o outro recebe
`"name_taken"` — o lock não é só sobre latência, fecha um bug de
corretude de verdade.

**Ressalva medida, registrada por um dos 3 agentes**: o lock tem custo
de latência real e mensurável no caminho de CONEXÃO (register+login+
create, payload leve) — entre 40-90% mais lento em wall-clock puro no
hardware local (nenhum dos dois cenários chegou perto de erro real,
0% de falha nos dois). Decisão consciente de manter o lock cobrindo os
4 caminhos mesmo assim (segurança > throughput bruto nesta escala,
e os 3 agentes convergiram nisso de forma independente) — registrado
como ressalva pra reconsiderar SE o teste de carga real mostrar esse
custo sendo sentido pelos jogadores.

**Testes**: `tests/test_auth_write_lock.py` (novo — serialização real
via contador de reentrância + prova que `authenticate` não é afetado),
`tests/test_loadtest_bot_reconnect.py` (novo — prova que o cliente da
tentativa anterior é fechado antes de reconectar). Suíte completa
rodada por múltiplos agentes + por mim, de forma independente, depois
de revisar o diff final linha a linha (3 agentes editando os mesmos
arquivos concorrentemente exigiu essa revisão extra) — **1513 passed**,
0 regressão. Revert-to-confirm nos 2 mecanismos principais (lock e
close-on-reconnect) confirmou os testes novos pegando a regressão de
verdade. `data/game.db` real conferido sem lixo de teste desta vez.

**Pendente** (mesmo dia, próximo passo): rodar de novo o teste de 250
bots (mesma rampa agressiva que mediu 1181 reconexões) com os 2 fixes
aplicados, pra confirmar a redução real da rajada de reconexão.

## 120. §119 regrediu sob carga SUSTENTADA (não burst) — fila FIFO única acoplando leve+pesado, corrigido com 2 filas por peso (06-07/09/2026)

O "pendente" do §119 foi executado: rodar de novo o mesmo teste de 250
bots, mas agora com carga SUSTENTADA (chegada contínua de ~2 bots
novos/s por 500s via `--batch-size 10 --batch-interval-s 5`, diferente
do burst único medido em §119) contra o `_write_lock` único. Resultado
foi o OPOSTO do esperado: reconexões **1181→2768** (quase o dobro),
bots conectados no fim **~225/250→109/250**, CPU **99-100% sustentado
por 40s+**. Notavelmente, **zero erros reais de "database is locked"**
e o tick do jogo continuou rápido (só 6 ticks lentos em 500s, ~11ms
avg) — provando que a regressão estava inteira no caminho de conexão/
auth, não na simulação.

3 agentes escalados de novo, mesma exigência de implementar e validar
com teste real (regra do usuário, sem exceção): **2 dos 3 (arquitetura,
coesão vs. mercado) atingiram o rate limit da sessão da API a meio
caminho** — falha externa (`HTTP 429`), não um veredito sobre a
abordagem de nenhum dos dois. O 3º (qualidade de código) terminou e
**mediu de verdade, não teorizou**: script instrumentado provou que
sob rajada simultânea (thundering herd, não a chegada sustentada) a
fila FIFO do `_write_lock` único cresce **O(n) com o tamanho da
rajada** — burst de 600 gerou fila máxima de ~8.9s, ACIMA do timeout
de 10s do `recv_one()` do bot (`tools/loadtest/protocol_client.py`);
o cliente trata isso como falha, reconecta, e a nova tentativa entra
na MESMA fila já congestionada — efeito avalanche que explica
mecanicamente 1181→2768. Também mediu, como fatores secundários mas
reais: `sqlite3.connect()` repetido custa 7-17% do tempo total sob
carga concorrente (0.145ms isolado → 0.85-1.3ms sob contenção), e
logging síncrono do `RotatingFileHandler` (~0.22ms/call) roda FORA do
`_tick()` (não capturado pelo perf log baseado em tick), então é
invisível ao instrumento que catalogou o problema original.

Antes de atingir o rate limit, o agente de arquitetura já tinha
avançado o design certo (recuperado de um `git stash` após a falha,
aplicado e validado por mim — não pelo agente, já que ele não chegou
a terminar):

1. **`_write_lock` estreitado pra cobrir SÓ `save_character`**
   (`server/auth.py`) — payload pesado (mediana ~1KB, cauda real até
   ~600KB de fog-of-war), dispara em TODO disconnect (além de
   autosave/SAVE_STATE/talento/quest). Novo **`_identity_lock`** cobre
   `register`/`create_character`/`delete_character` — leves, sensíveis
   a latência de CONEXÃO, nunca esperam atrás de um save gordo de
   outro jogador. Causa raiz do §119: 1 fila FIFO só misturando
   requisição rápida (register de quem tenta voltar) com job pesado
   (save de quem acabou de cair) — o próprio disconnect que gerou a
   reconexão era, ele mesmo, quem atrasava a reconexão seguinte.
   Antipadrão clássico de mercado (nunca 1 fila só pra latência baixa
   e job pesado).
2. **Fast-path em `register()`**: `SELECT` de existência (leitura
   pura, WAL nunca disputa lock de escrita, medido ≈160000
   consultas/s) ANTES de tentar `_identity_lock` — todo bot/cliente
   manda REGISTER de novo em TODA reconexão mesmo já tendo conta (é o
   fluxo real do bot, não bug), então o caso comum nem precisa tentar
   escrever. Corrida rara (2 registros do MESMO username NOVO ao mesmo
   tempo) ainda cai certo no `IntegrityError` dentro do lock — sem
   brecha de corretude.
3. **Conexão SQLite persistente por thread** (`_conn_cache =
   threading.local()`, chaveado por `DB_PATH` pra não quebrar teste
   que troca de banco) — nunca mais 1 `sqlite3.connect()` por chamada.
   Achado por medição: o arquivo `-wal` crescia SEM checkpoint
   enquanto havia abre/fecha de conexão em volume alto e concorrente
   (SQLite só faz checkpoint quando NENHUMA conexão está com leitura
   em andamento — um fluxo de "abre, lê/escreve 1 linha, fecha" o
   tempo todo em paralelo nunca dá essa janela). 2 executores dedicados
   de 1 worker cada (`_write_executor`/`_identity_executor`, nunca o
   executor padrão do asyncio — que espalharia cada chamada numa
   thread DIFERENTE, até 8 no hardware local, impedindo a conexão
   persistente de convergir) garantem que cada fila usa sempre a MESMA
   conexão cacheada.
4. **`PRAGMA synchronous=NORMAL`** — seguro com WAL (garantia oficial
   do SQLite: nunca corrompe, só pode perder a última transação num
   crash de SO/energia, não de aplicação). Medido: fsync por commit
   caiu de ~0.8ms (FULL, default) pra ~0.04ms com conexão persistente.

Medido nesta máquina (script de benchmark ad-hoc): INSERT leve
sequencial ≈1000-1700 escritas/s (fsync-bound, teto real); UPDATE
pesado (~1KB-50KB JSON) por rowid ≈30000-80000 escritas/s — ambos MUITO
acima da demanda real (~2 conexões novas/s sustentado). **O SQLite em
si nunca foi o gargalo nesta escala** — era 100% o acoplamento leve+
pesado numa fila só.

**Validação feita por mim** (não só pelo agente, que não terminou):
recuperei o `git stash` (o mais avançado dos 2 stashes deixados —
`stash@{0}`, superset do `stash@{1}`), rodei a suíte completa
(**1515 passed**, 0 regressão), fiz revert-to-confirm no fast-path do
`register()` (desabilitei temporariamente, confirmei que
`test_register_conta_existente_usa_fast_path_sem_disputar_lock` quebra
de verdade, restaurei — suíte volta a passar), conferi `data/game.db`
real sem conta de teste vazada. Comitado (`e9cd9bd`).

**Resultado real, medido depois** (mesmo teste de 250 bots, mesma
rampa sustentada de 500s, servidor reiniciado, `data/game.db` real):
reconexões **2768→652** (baseline pré-§119 era 1181 — ficou BEM abaixo
até desse baseline), bots conectados no fim **109/250→242/250**
(melhor que o próprio baseline pré-§119, ~225/250). Tick do jogo
continuou saudável (102 ticks lentos em 500s de rampa agressiva,
nenhum acima do padrão já visto em `ai_bundles`/`skill_requests`).
Checagem de idle pós-desconexão (o sintoma original do §118): só **1**
ocorrência de tick lento com `players=0` em toda a janela pós-teste
(era 875 antes do §118) — um único pico de 107ms no exato momento da
desconexão em massa (`SpawnZoneSystem` processando respawn de várias
zonas de uma vez quando 246→0 players caem juntos), recuperando
imediatamente pro padrão saudável (~11ms/tick) na janela seguinte —
não é um lockup sustentado, é um custo pontual do evento de massa em
si.

**Causa residual não resolvida, honestamente registrada**: ainda houve
uma rajada real de reconexões no MEIO do teste (t≈110s-290s, saltando
de 0 pra 538 reconexões antes de estabilizar) — bem menor que a
regressão do §119, mas não zero. Não investigada a fundo nesta rodada
(fora do escopo do que os 3 agentes foram escalados pra resolver, que
era a regressão sustentada vs. o baseline §119, não zerar reconexão
por completo). Hipótese não confirmada: pode ser o momento em que
partidas de arena/BG começam a decidir (times formando/dissolvendo
perto de t=100-110s) somado à rampa de conexão ainda em andamento —
candidato a investigação futura SE o padrão persistir sendo sentido
como problema real em produção.

**Testes**: `tests/test_auth_write_lock.py` ganhou 2 casos novos
(fast-path chama ou não `_register_account_sync` de verdade,
conforme conta já existe ou não). Nenhum teste novo pro
`_conn_cache`/executores dedicados especificamente (cobertos
indiretamente pela suíte completa passando e pelo teste de carga real
— comportamento observável, não uma unidade isolada testável sem
concorrência real de thread).

## 121. Top-5 gargalos de tick, 250 players — 5 correções propostas por 3 agentes, discutidas com o usuário e implementadas (06-07/09/2026)

Análise do `logs/server_perf.log` do teste de carga do §120 (250 players
de pico) achou o ranking real de custo por sistema: `ai_bundles`→
`EnemyAISystem` (8,95ms médio, 40,6% do tick, pico isolado de 76,4ms),
`skill_requests` (P99 de 35-73ms mesmo com rate-limit do §117),
`aoi_collect` (2,7ms médio, 75% vindo de 2 sub-rotinas de dirty-check
que recalculavam tudo todo tick), `player_attacks` (2,4-2,6ms estável)
e `index_build` (1,5-1,7ms estável). 3 agentes escalados (arquitetura/
qualidade de código/coesão vs. mercado), com instrução explícita de SÓ
investigar e propor — nenhuma edição de arquivo — pra discutir as
sugestões com o usuário ANTES de qualquer implementação (pedido
explícito do usuário, decisão consciente de trazer o desenho pra
aprovação prévia em vez de implementar direto).

**Achados que os 3 convergiram** (aoi_collect): os 3 concordaram que
`_sync_player_bush_dirty`/`_sync_player_skill_levels_dirty` são
recompute-then-diff quando deviam ser invalidate-on-write (mesmo
princípio de `FogOfWar.version`, §53) — mas divergiram em COMO: pra
`skill_levels` (1 único escritor conhecido, `grant_skill_xp`) um
dirty-flag é seguro; pra `bush` (estado DERIVADO da posição, sem
"dono" que escreve nele) o agente de coesão/mercado apontou que um
dirty-flag genérico arriscaria ficar esquecido num código futuro que
move o player — sugeriu cache de `(tile_x, tile_y)` em vez disso.

**Achados que os 3 DIVERGIRAM** (levado ao usuário pra decidir, não
decidido sozinho): (1) `skill_requests` — orçamento agregado por tick
(tipo `MAX_PATHFINDS_PER_FRAME`)? Arquitetura achou que valia
considerar; coesão/mercado achou que NÃO valia, porque penalizaria
justo o momento de ação coordenada legítima (raid/BG) e o §117 já
tinha concluído que o pico residual era majoritariamente artefato da
ferramenta de teste, não tráfego real. (2) `EnemyAISystem` — teto tipo
pathfind-budget pro `loop_mobs`? Arquitetura sugeriu como mitigação só
pro PICO isolado (76,4ms); coesão/mercado achou que os 3 pilares de
mercado já implementados (LOD/throttle/spatial hash, §29) bastavam,
sem precisar de mais nada nesta escala. Usuário, depois de perguntar
sobre o mecanismo de cada um (incluindo se client já evita mandar
CAST_SKILL em cooldown — SIM, já existe nos 2 lados, client E bot de
teste — e se o "33 mobs ativos" do log batia com o que `loop_mobs`
processa de verdade — NÃO, são 2 contadores diferentes: "ativos"
[CHASING/ATTACKING/AGGRO_DELAY] é mais estrito que `mobs_no_filtro_ai`
[população real processada, 41-55 na janela mais cheia]), decidiu
implementar as 2 mitigações mesmo assim.

**5 correções implementadas**:
1. `World.get_component()` (`engine/world.py`) — `try/except` em vez
   de `dict.get(entity_id, {}).get(component_type)`: medido 16% mais
   rápido (o `.get(eid, {})` original aloca um dict vazio de fallback a
   cada chamada, mesmo no caminho comum onde a entidade existe). Ponto
   ÚNICO de leitura de componente do ECS — propaga sozinho pra todo
   sistema, sem tocar em nenhum call site.
2. Imports locais dentro de loop por-tick/por-request/por-player,
   subidos pro topo do arquivo (medido: `from x import y` repetido
   custa ~8,7x mais que a referência já resolvida) — `server/
   bush_zone_processor.py` (2 imports), `server/skill_processor.py::
   _process_skill_requests` (3 imports no caminho comum de TODO
   request: `is_action_locked`/`StatusEffects`/`is_skill_authorized`),
   `server/combat_processor.py::_process_player_attacks` (`can_engage`,
   2 aliases — caminho PvP e caminho player→mob, este 2º roda pra TODO
   player atacando um mob, todo tick). Mecânico, mesmo objeto, zero
   mudança de comportamento.
3. `SkillLevels.dirty`/`pending_level_ups` (`engine/components.py`,
   novos campos) — `stats_system.grant_skill_xp` (único escritor real)
   marca `dirty=True` na mutação e já registra qual skill/nível subiu,
   em vez de `_sync_player_skill_levels_dirty` recalcular
   `tuple(sorted(...))` de levels+xp de TODO player TODO tick só pra
   comparar contra cache. `dirty` começa `True` (não `False`) de
   propósito — um `SkillLevels` recém-criado (spawn/load) precisa do 1º
   snapshot completo pro dono, igual ao comportamento antigo. Campo
   `_player_skill_cache` (agora morto) removido.
4. `_player_bush_tile_cache` (`server/world_server.py`, novo, eid→
   (tile_x, tile_y)) — gate barato ANTES de `_is_on_bush_tile`
   (resolve mapa+tilemap+bounds-check): se o tile não mudou desde o
   tick anterior, bush não pode ter mudado, pula a chamada cara
   inteira. Escolhido em vez de dirty-flag genérico por decisão
   explícita (ver divergência acima) — sem "dono" único que escreve
   bush, um flag seria fácil de esquecer marcar num código futuro; 2
   inteiros comparados não têm esse risco.
5. `EnemyAISystem._apply_active_mob_budget()` (`engine/
   world_systems.py`, novo `MAX_ACTIVE_MOBS_PER_TICK=60`) — mesmo
   princípio de `MAX_PATHFINDS_PER_FRAME`/`_find_path_budgeted` (nunca
   descarta, só adia pro próximo tick), aplicado à lista inteira de
   `_active_mobs_this_tick`, não só à chamada de A*. Só entra em vigor
   quando a população ativa passa do teto (bem acima do pico já visto
   no teste, 55) — abaixo disso, comportamento idêntico ao de antes.
   Quando dispara, processa um subconjunto ROTATIVO (nunca sempre os N
   primeiros — morreria de fome os últimos da lista), garantindo que
   todo mob ativo roda pelo menos 1x a cada
   ceil(população/orçamento) ticks.

**Ressalva honesta, registrada por pedido explícito do usuário durante
a discussão**: o item 5 cobre pico por CONTAGEM maior de mob ativo, não
pico por CUSTO por-mob mais caro no mesmo tick (o pico real observado
de 76,4ms com só ~30-55 mobs ativos pode ter sido causado por várias
transições de estado caras acontecendo juntas naquele tick específico,
não por população acima do normal) — um orçamento por CONTAGEM não
necessariamente teria cortado ESSE pico específico. Só o próximo teste
de carga com este fix aplicado vai confirmar se ajudou de verdade ou
se o teto de 60 nunca chega a disparar nesta escala.

**Testes**: 9 novos — `TestEnemyAISystemActiveMobBudget` (4, ver
`tests/test_perf_log_improvements.py`, testa `_apply_active_mob_budget`
isolado, sem precisar de 60+ mobs reais no ECS), `TestSkillLevelsDirtyFlag`
(3, `tests/test_server.py`), 2 novos em `TestSyncPlayerBushDirty`
(`tests/test_bush_stealth.py`, spy provando que `_is_on_bush_tile` só é
chamada quando o tile muda). Revert-to-confirm nos 3 mecanismos com
lógica nova (dirty-flag, gate de bush, orçamento de mob) — os 3
confirmados pegando a regressão de verdade quando desligados na mão.
Suíte completa: 1524 passed (1515 + 9 novos), 0 regressão. `data/
game.db` real conferido sem lixo de teste.

**Resultado real, medido depois** (mesmo teste de 250 bots/500s,
`logs/server_perf_antes_top5.log` vs. `logs/server_perf.log` novo,
médias sobre todos os blocos de 300 ticks com players≥190 em cada log —
6 blocos antes, 10 depois):

| Sistema | avg ANTES | avg DEPOIS | p99 ANTES | p99 DEPOIS |
|---|---|---|---|---|
| `aoi_collect` | 2,358ms | **1,068ms** (−55%) | 4,528ms | **2,144ms** (−53%) |
| `skill_requests` | 2,199ms | **1,755ms** (−20%) | 33,439ms | **17,629ms** (−47%) |
| `player_attacks` | 1,940ms | **1,184ms** (−39%) | 4,354ms | **2,654ms** (−39%) |
| `index_build` | 1,525ms | 2,124ms (+39%) | 3,065ms | 5,772ms (+88%) |
| `ai_bundles` | 7,689ms | 9,608ms (+25%) | 23,758ms | 28,107ms (+18%) |

Os 3 sistemas com correção DIRETA (itens 1-4 da lista de correções —
`aoi_collect` via dirty-flag/gate de posição, `skill_requests` e
`player_attacks` via imports hoisted + `get_component` mais rápido)
melhoraram de forma consistente e grande, em TODOS os blocos, não só
numa amostra sortuda. `index_build` e `ai_bundles` pioraram — mas por
um motivo identificado, não por regressão dos fixes: `maps_ativos_peak`
médio foi **4,83 antes → 5,90 depois** (o log completo mostra o
`DEPOIS` rodando consistentemente com 6 mapas ativos simultâneos,
quase o teste inteiro, contra 5 antes) — 1 partida de arena/BG a mais
rodando o tempo todo, quase certamente puxada pela turbulência de
reconexão do meio do teste (t≈350-430s, reconnects saltando de 449 pra
943 antes de estabilizar — sintoma parecido com a "causa residual não
investigada" do §120, não deste item). `ai_bundles` e `index_build` são
os 2 sistemas cujo custo escala com número de INSTÂNCIAS/entidades
ativas no mundo TODO (cada mapa ativo paga seu próprio preâmbulo de
`EnemyAISystem`; `index_build` escaneia entidades de todos os mapas de
uma vez) — os outros 3 escalam com número de PLAYERS, que ficou
comparável entre os dois testes (190-257 nos dois). Comparando só os
blocos com a MESMA contagem de mapas ativos (players=236, 5 mapas
antes vs. depois — não achei um par exato de 5 mapas no log novo, mas
o bloco de 236 players tinha 6 mapas ativos e mesmo assim mediu
22,47ms/tick, praticamente igual aos 22,53ms/tick do bloco de 236
players ANTES com 5 mapas) — indício de que o custo por-mapa-a-mais
não é catastrófico, e a diferença bruta de tick médio ponderado
(18,55ms→21,77ms) é majoritariamente esse fator externo ao teste, não
os 5 fixes.

**Item 5 (orçamento de mob) não foi exercitado de verdade neste teste**:
`mobs_no_filtro_ai` nunca passou de 59 (teto é 60) — o mesmo patamar já
visto antes das mudanças. A ressalva já registrada acima (cobre pico
por CONTAGEM, não por CUSTO por-mob) segue sem confirmação real; só um
teste com população de mob ainda maior (ou um evento de spawn em massa)
vai exercitar o orçamento de verdade.

**Honestidade sobre o resultado**: NÃO dá pra afirmar "tick médio
melhorou X%" de forma limpa neste teste — a variância de quantas
partidas de arena/BG se formam ao mesmo tempo (decisão de bot, não
controlada por seed fixa) confunde a métrica agregada. O que dá pra
afirmar com confiança, porque foi medido de forma consistente em TODOS
os blocos e são sistemas que não dependem de quantos mapas estão
ativos: os 3 fixes diretos (`aoi_collect`, `skill_requests`,
`player_attacks`) reduziram o custo médio deles em 20-55% e o p99 em
39-53%. Pra medir `ai_bundles`/`index_build`/o orçamento de mob de
forma limpa, precisaria de um teste novo com o número de mapas ativos
controlado (ex.: só mundo aberto, sem fila de arena/BG) — não feito
nesta rodada.

**Investigação extra (07/09/2026, pedido do usuário — "ainda tem tick
lento com 0 players, isso não faz sentido")**: o log deste teste tinha
148 ocorrências de tick lento com `players=0` — bem mais que o `1`
medido no teste do §120. Investigado ANTES de assumir que era
regressão dos 5 fixes: (1) o log mostrou o servidor de pé por **4,5
minutos sem nenhum bot conectado** antes da rampa começar (boot às
14:05:26, 1º login às 14:09:57) — janela de ócio ~28x mais longa que
qualquer teste anterior, então CONTAGEM bruta não é comparável; (2) o
"culpado" no caminho crítico girou entre **18 sistemas diferentes**
(`index_build`, `global_systems`, `aoi_collect`, `combat_spatial_hash_build`,
`player_attacks`, `death_handling`, etc.), sem nenhum dominando, com
intervalos irregulares entre ocorrências (8 a 749 ticks, sem
periodicidade) — assinatura de interferência EXTERNA ao processo
(disputa de CPU no SO), não de um sistema específico com bug real (o
padrão do §118, quando era bug de verdade, tinha 1 causa dominante
achável por profiling).

**Teste de controle decisivo**: rodei o servidor de novo, sozinho,
por ~5min, SEM executar nenhum outro comando em paralelo (janela
"silenciosa", eliminando minha própria atividade de terminal como
variável). Resultado: **8 ocorrências em 7500 ticks (0,11%)** contra
**64 em 8400 ticks (0,76%)** da janela "barulhenta" — quase 7x menos,
com o MESMO código rodando dos dois lados. Confirma: a maior parte do
problema é contenção de CPU da própria máquina de desenvolvimento
compartilhada (meus comandos de terminal rodando em paralelo com o
servidor), não um bug introduzido pelos 5 fixes do §121. A taxa
residual mesmo na janela silenciosa (0,11%, ~1 a cada 31s) é esperada
num Windows real (SO, antivírus, IDE) e não é zero em NENHUM servidor
rodando numa máquina compartilhada — não é motivo pra escalar os 3
agentes de novo; fechado sem mudança de código.

## 122. Orçamento de TEMPO pra `skill_requests` — devolve excesso pro próximo tick em vez de travar o tick inteiro (07/09/2026)

Diagnóstico geral de performance (3 agentes, pedido do usuário sem
parâmetro de comparação — ver seção anterior desta mesma investigação)
achou 1 evento real preocupante no meio de um quadro geral saudável:
tick#9857 (240 players) levou **365,8ms** (11x o orçamento de 33ms),
com `skill_requests` sozinho consumindo 350,5ms — trava a simulação
INTEIRA pra TODOS os jogadores por 1/3 de segundo. Investigado se era
artefato do bot de teste antes de mexer em código: cruzando timestamp
real do tick com o log do servidor, o evento caiu bem no meio da rampa
de conexão inicial (sessão total cruzando de ~230→242), não na rajada
de reconexão já catalogada (§120). Matemática bate com "quase todo
mundo bateu o teto do rate-limit ao mesmo tempo": 350,5ms ÷ custo médio
por pedido (~0,3-0,4ms) ≈ 875-1150 pedidos processados — perto do
teto TEÓRICO de `240 players × MAX_SKILL_REQUESTS_PER_PLAYER_PER_TICK
(3) = 720`. Conclusão: o rate-limit por JOGADOR (§117) funciona certo
(ninguém abusou sozinho), mas nunca protegeu contra a SOMA de muitos
jogadores legítimos coincidindo no mesmo tick — e o bot de teste tem 2
comportamentos que tornam essa coincidência mais provável que com
humanos reais (`_SKILL_CHANCE_PER_TICK=0.35` por bot independente, e
"age imediato ao spawnar" desde o §116) — mas o GAP arquitetural em si
(nenhum teto agregado) é real e podia, em tese, ser atingido por
jogadores reais numa coordenação forte (largada de BG, pull de raid),
só que bem mais raro.

**Correção**: mesmo princípio já usado em `MAX_PATHFINDS_PER_FRAME`/
`EnemyAISystem._apply_active_mob_budget` (nunca descarta, só adia pro
próximo tick) — mas com **orçamento de TEMPO, não de contagem**, porque
o custo por pedido não é fixo o bastante (skill diferentes, projétil
vs instantânea) pra um teto de N pedidos garantir um teto de ms.
`_process_skill_requests` (`server/skill_processor.py`) agora checa, a
cada 20 pedidos processados (não a cada 1 — custo do relógio some no
ruído), se já passou de `SKILL_REQUESTS_TIME_BUDGET_MS=10.0`
(`server/world_server.py`) desde o início da fila deste tick. Se sim,
devolve o RESTO da fila — na MESMA ordem, FIFO — pro início de
`_pending_skill_requests`, que vira a fila do PRÓXIMO tick. Nenhum
pedido é descartado, só adiado; 10ms deixa ~1/3 do orçamento de 33ms
pra essa fila mesmo no pior caso, sobrando espaço pros outros sistemas
do tick.

De quebra, corrigido um import local (`import time as _time_mod`
dentro do loop por-request) que tinha escapado da limpeza do §121 —
mesma classe de custo (~8,7x mais lento que referência já resolvida),
hoisted pro topo do arquivo.

**Decisão consciente, não escalada pro escopo maior**: o usuário
perguntou sobre uma ideia mais ampla (sistemas priorizados por
importância no tick inteiro, "crítico" roda sempre vs "adiável" só se
sobrar tempo) — decidimos fazer SÓ este piloto concreto agora (baixo
risco, ataca o problema medido de verdade) e deixar a reclassificação
de TODOS os ~20 sistemas do tick pra uma conversa futura separada, já
que isso exige julgamento sistema-por-sistema que não deve ser feito
sozinho.

**Testes**: `TestSkillRequestsTimeBudget` (3, `tests/test_server.py`)
— estoura orçamento devolve resto na mesma ordem; abaixo do orçamento
processa tudo normal (comportamento antigo preservado); pedidos
adiados processam de verdade no tick seguinte. Revert-to-confirm
(corte desabilitado na mão) confirmou 2 dos 3 testes pegando a
regressão. Suíte completa: 1527 passed (1524 + 3 novos), 0 regressão.

**Resultado real, medido depois** (mesmo teste de 250 bots/500s,
`logs/server_perf_com_players_top5.log` ANTES vs. `logs/
server_perf_apos_skillbudget.log` DEPOIS, mesma janela players>100):

| | ANTES | DEPOIS |
|---|---|---|
| Pico de `skill_requests` isolado | **350,5ms** | **25,1ms** (−93%) |
| Pior tick do teste inteiro | 365,8ms | 105,4ms (e esse já não é mais `skill_requests` — é `ai_bundles`, sistema diferente, já catalogado) |
| Tick lento (players>100) | 10,85% dos ticks | **4,75%** (menos da metade) |
| Ticks severos (>100ms) | 0,24% | **0,04%** (6x menos) |

O orçamento de tempo funcionou exatamente como desenhado: nenhum tick
do teste novo teve `skill_requests` acima de ~25ms — o corte em 10ms
(mais a folga de checar a cada 20 pedidos, não a cada 1) segura o pico
numa faixa previsível em vez de deixar a fila inteira estourar. O
evento que motivou esta correção (365,8ms travando tudo pra 240
jogadores) não se repetiu; o pior caso do teste novo (105,4ms) é
`EnemyAISystem`, já catalogado no §121 como trabalho genuíno que escala
com número de mapas ativos, não uma regressão nova.

Conexão/reconexão neste teste ficou saudável (239-247/250 conectados o
tempo todo, ~500 reconexões totais, sem rajada) — não interfere na
comparação de `skill_requests`.

## 123. Migração SQLite → PostgreSQL (asyncpg) — decisão de infraestrutura do usuário: servidor dedicado em nuvem, Linux, múltiplos núcleos (07/09/2026)

Usuário decidiu ir pra servidor dedicado em nuvem (Linux, alta
disponibilidade no sentido prático — reinício automático + rede boa,
não failover completo — e múltiplos núcleos, pensando em sharding por
mapa/instância no futuro). Antes de mexer em infraestrutura, priorizado
resolver a dívida já documentada desde sempre no CLAUDE.md ("SQLite
(dev) → PostgreSQL (prod)") — decisão do usuário de fazer AGORA, junto
com a ida pra nuvem, não depois.

**Por que trocar a biblioteca também (não só o SQL)**: toda a
maquinaria de `_write_lock`/`_identity_lock`/`_write_executor`/
`_identity_executor`/`_conn_cache` construída nas seções §113-122
existia SÓ porque SQLite aceita 1 escritor real por vez. Postgres
aceita múltiplos escritores de verdade (MVCC) — manter aquela
serialização seria complexidade sem propósito, e um lock em memória de
UM processo NUNCA protegeria de verdade contra múltiplos PROCESSOS
(o próprio motivo de ir pra multi-core). Decisão consciente, aprovada
pelo usuário antes de implementar (não decidida sozinho): trocar
`sqlite3` síncrono por `asyncpg` (driver assíncrono nativo, pool de
conexões pronto, sem precisar de thread executor) E remover toda a
maquinaria de lock/executor da era SQLite.

**Unicidade de nome de personagem** (TOCTOU fechado por `_identity_lock`
no §119) agora é garantida por um **índice único no próprio banco**
(`characters_name_lower_idx`, case-insensitive) — correto mesmo com
múltiplos processos disputando ao mesmo tempo, o que um lock em memória
de 1 processo nunca garantiria. Revert-to-confirm real feito (não só
teórico): com o índice removido e um `asyncio.Event` forçando 2
coroutines a passarem pelo SELECT de checagem ANTES de qualquer uma
tentar o INSERT, o banco terminou com **2 linhas duplicadas** do mesmo
nome — com o índice restaurado, o MESMO cenário forçado terminou com
exatamente 1 linha (`UniqueViolationError` capturada e traduzida pra
`"name_taken"` na outra).

**Corrida aceita e documentada, não corrigida**: limite de 3 personagens
por conta ainda é um SELECT COUNT seguido de INSERT, sem proteção de
banco — 2 CREATE_CHARACTER simultâneos da MESMA conta podem, em teoria,
resultar em 4 personagens em vez de 3. Exige duplo-clique na MESMA
fração de segundo da MESMA conta; sem impacto de segurança/corretude de
dado (só um soft-limit cosmético passando por 1) — não justifica a
complexidade de uma transação SERIALIZABLE com retry. Unicidade de
NOME (o que realmente importa pra nameplate/chat global) tem proteção
real; contagem de personagem não.

**Achado real, sério, durante o desenvolvimento**: pool `asyncpg`
recriado a cada troca de event loop (mesmo princípio já usado pro
`_get_write_lock` da era SQLite — `unittest.IsolatedAsyncioTestCase`
cria um loop novo por método de teste) tem um problema que um
`asyncio.Lock` cru NUNCA tinha: um POOL segura conexões reais no
Postgres, e só dá pra fechar ele DIREITO enquanto o loop em que foi
criado ainda está rodando — depois que esse loop fecha (fim do método
de teste), não tem mais como agendar o fechamento limpo. Rodando a
suíte inteira, isso vazou conexão a cada teste que tocasse `server.auth`
(via `fake_login`/`_login`/chamada direta), esgotando o
`max_connections` do Postgres por volta do teste #1500 —
`asyncpg.exceptions.TooManyConnectionsError`, 17 testes falhando (todos
em arquivos que nem tocam auth diretamente, só rodavam DEPOIS que o
teto já tinha estourado). Fix: `server.auth._close_pool()` (nova
função) chamada de dentro do próprio teste, ENQUANTO o loop dele ainda
roda — via `restore_auth_schema()` (helper já usado pelos testes de
schema isolado) e um novo `tests.helpers.AuthPoolCleanupMixin`
(`asyncTearDown` que fecha o pool) aplicado a TODA classe
`IsolatedAsyncioTestCase` que toca auth sem trocar de schema (14
arquivos de teste — `test_session.py` sozinho tinha 20 classes).
Produção NUNCA tem esse problema (1 processo = 1 loop a vida inteira,
pool fechado só quando o processo morre) — é puramente um artefato de
como `IsolatedAsyncioTestCase` cria/destrói loop por método.

**Escopo tocado**: `server/auth.py` (reescrito — schema Postgres,
`asyncpg`, sem locks), `server/main.py` (`init_db()` virou `await`),
`server/grant_gm.py` (reescrito async), `server/session.py` (removida
referência a `_get_conn`/`_write_lock`, comentários desatualizados
limpos), `tools/loadtest/provision.py` (Fase B reescrita com `asyncpg`
em vez de `sqlite3` direto), `requirements_server.txt` (+`asyncpg`).
Testes: `tests/test_auth_write_lock.py` REMOVIDO (testava mecanismo que
não existe mais) e substituído por `tests/test_auth_concurrency.py`
(testa a garantia que IMPORTA agora: corretude sob concorrência real,
não "nunca sobrepõe no tempo"); `tests/test_auth_salt.py`/
`tests/test_character_names.py` reescritos pra schema Postgres isolado
(`tests.helpers.isolate_auth_schema`/`restore_auth_schema`, novo,
mesmo espírito do `DB_PATH` temporário da era SQLite); 14 arquivos de
teste ganharam `AuthPoolCleanupMixin`.

**Novo**: `tools/migrate_sqlite_to_postgres.py` — migração ÚNICA (não
faz parte do runtime) do `data/game.db` antigo pro Postgres. Nunca
destrutivo (só LEITURA no SQLite); preserva IDs originais; recusa rodar
contra um banco Postgres que já tenha dado (nunca sobrescreve). Achado
real ao testar: o banco dev real tem **~45 grupos de nomes de
personagem duplicados** (case-insensitive) — dados de teste antigos, de
antes da regra de unicidade existir (inclusive "Aventureiro", o
incidente ORIGINAL que motivou a regra, §107/§108, nunca limpo do
banco). O índice único novo rejeitaria a migração direta; o script
detecta e renomeia automaticamente as ocorrências duplicadas (sufixo
`__dupN`, nunca a primeira) — nada é apagado, tudo fica visível no
relatório. **Rodar de verdade contra `data/game.db` fica pendente,
decisão do usuário** (script testado e validado contra um schema
Postgres isolado, não contra o banco dev real ainda).

**Testes**: suíte completa — **1527 passed, 2 failed** (as 2 falhas são
`time.sleep(0.01)` com margem de 10ms num teste de timing de
performance, ex.: `TestTickPerfProfiler::test_perf_push_pop_grava_no_caminho`
— flakiness de granularidade de timer do Windows, PRÉ-EXISTENTE,
reproduzida isoladamente sem nenhuma relação com a migração; não
corrigida por estar fora do escopo desta tarefa). Revert-to-confirm
real no índice único (não só teste automatizado — script standalone
provando a corrida via `asyncio.Event`, documentado acima).

**Pendente** (próximos passos do roadmap de infraestrutura, não
travados por este item): usuário provisiona o servidor Linux na nuvem;
decidir se a migração de dados reais roda agora ou só na hora do
deploy; configurar systemd + firewall + TLS/wss (item C1, ainda em
aberto) + backup automático.

## 124. Provisionamento real do servidor Hetzner + migração de dados reais + backup + deploy (08/09/2026)

Continuação do §123 — a parte de infraestrutura que ficou pendente lá.
Usuário criou conta na Hetzner Cloud e pediu ajuda passo a passo pelo
console web (screenshots).

**Dimensionamento — recalibrado após pushback direto do usuário**:
sugeri inicialmente CPX32/CPX31 (4 vCPU/8GB, ~US$73-74/mês) baseado em
raciocínio abstrato de sharding futuro, sem pesar o suficiente a
restrição orçamentária ATUAL do usuário. Usuário corrigiu direto:
"isso vai ser muito caro pra mim que estou desenvolvendo, ainda não
tenho retorno financeiro." Recalibrei pra CPX11 (2 vCPU/2GB,
~US$20/mês) — dimensionado pro uso ATUAL de desenvolvimento/teste, não
pra escala hipotética futura (upgrade de plano é trivial depois, sem
perda de dado). **Lição registrada**: não dimensionar infraestrutura
pra escala futura hipotética quando o usuário está pré-receita — vale
pra qualquer decisão de custo de infra futura, não só esta.

**Esclarecimento importante dado ao usuário**: o processo do jogo é
`asyncio` single-thread — usa 1 núcleo só pro tick loop, não importa
quantos vCPUs o servidor tenha. Hoje, vCPUs extras só ajudam por dar
ao Postgres/SO um núcleo próprio (sem disputar com o processo do jogo),
**não** por paralelizar lógica de jogo — paralelismo de verdade entre
núcleos exigiria a arquitetura de sharding por mapa/instância (ainda
não construída, multi-processo).

**Servidor provisionado**: Ubuntu 24.04.4 LTS, região Ashburn/VA
(us-east — mais perto do Brasil que Helsinki/Europa, escolha aprovada
pelo usuário), CPX11, IP `5.161.104.174`. Par de chaves SSH dedicado
gerado localmente (`~/.ssh/rpg_hetzner`/`.pub`), nunca a chave pessoal
do usuário — arquivo `~/.ssh/config` pré-existente do usuário (hosts
não relacionados) foi só lido, nunca modificado.

**Descoberta durante o setup — falha silenciosa de `apt-get`**: comando
`apt-get install ... 2>&1 | tail -N && echo INSTALACAO_OK` imprimiu
sucesso mesmo com a instalação tendo falhado de verdade (só `git`
instalou; `postgresql` e `python3.10` não) — `&&` depois de um pipe só
checa o exit code do ÚLTIMO comando do pipe (`tail`), nunca o do
`apt-get`. Causa raiz real: `python3.10-venv` não existe nos repositórios
padrão do Ubuntu 24.04 (que já vem com Python 3.12 por padrão),
abortando a transação inteira. Só percebido por checar o estado
instalado de forma independente depois (`which psql` vazio,
`python3.10 --version` "command not found") em vez de confiar no
marcador ecoado. Fix: PPA `deadsnakes` pra Python 3.10 + checagem
explícita de exit code em todo comando remoto daí em diante
(`; echo EXIT_CODE=$?`), nunca só um `&&` depois de pipe.

**Setup final**: usuário de serviço não-root `rpgapp` (dono de
código/venv/logs, roda o processo do jogo); `ufw` liberando só
`22/tcp`+`8765/tcp` (Postgres só em `localhost`, nunca exposto —
motivo de rodar a migração de dados VIA SSH no próprio servidor, não
da máquina local pela rede); PostgreSQL local (`rpgapp`/senha aleatória
`openssl rand -hex 24`, banco `rpg_ecs_online`); `systemd`
(`rpg-server.service`, `Restart=always`, habilitado no boot) — essa é a
"alta disponibilidade" que o usuário pediu (auto-restart + rede
estável do provedor, nunca discutimos failover multi-servidor, fora de
escopo no estágio atual).

**Migração de dados reais executada**: usuário testou o servidor por 2
dias e criou uma conta de teste (`juugo`) que colidia de username com
uma conta JÁ existente no `data/game.db` legado. Usuário pediu
explicitamente pra limpar as tabelas do Postgres do servidor antes de
migrar pra evitar o conflito — `rpg-server` parado, `TRUNCATE
characters, accounts RESTART IDENTITY CASCADE`, `data/game.db` local
copiado pro servidor via `scp` (`legacy_game.db`), migração real rodada
DENTRO do servidor (venv/`.env` próprios) com
`tools/migrate_sqlite_to_postgres.py` (já validado no §123 contra
schema isolado, agora rodado contra dado real pela 1ª vez). Resultado:
**481 contas + 573 personagens** migrados, mesmos ~45 grupos de nome
duplicado renomeados automaticamente (determinístico, consistente com
o dry-run do §123). Verificado pós-migração: conta `juugo` agora reflete
os personagens do banco legado (`jungo`/`Aldrin`/`Xadread`), não mais a
conta de teste recém-criada. `legacy_game.db` removido do servidor
depois (só o `data/game.db` local continua sendo a origem histórica).
`rpg-server` reiniciado.

**Backup**: `pg_dump -Fc` (formato custom, restaurável via `pg_restore`)
via `cron` do root, diário às 4h, retenção de 14 dias
(`/opt/rpg_ecs_online/backup_db.sh` +
`/opt/rpg_ecs_online/backups/`). Escolhido em vez do produto de Backups
pago da Hetzner (~20% sobre o preço do servidor) por controle de custo
— banco pequeno hoje, sem impacto de performance medido/esperado.
Primeira execução validada manualmente antes de agendar (169KB gerado).

**Deploy — não existe automático, decisão consciente**: nenhum commit
local chega ao servidor sozinho. `release_tools/deploy_server.ps1`
criado como mecanismo único: empacota só a árvore COMMITADA
(`git archive HEAD`, nunca `config.json`/`data/game.db`/
`maps/*.csv` do usuário, que ficam de fora de propósito — mesma regra
de nunca tocar WIP do usuário), transfere e reinicia o `systemd`.
**Achado ao testar**: `git archive HEAD --format=tar | ssh ...` direto
num pipeline do PowerShell corrompe o tar ("This does not look like a
tar archive") — PowerShell trata pipeline nativo como texto, não
binário puro, mesmo redirecionando pra um comando externo. Fix: gerar
o tar num arquivo temporário (`git archive --output=`), enviar por
`scp`, extrair via SSH — evita o pipe binário pelo PowerShell
inteiramente. Script testado de ponta a ponta contra o servidor real
(deploy do HEAD já commitado, serviço confirmado `active` depois).

**TLS/wss — ainda não implementado, só esclarecido**: usuário perguntou
se afeta desempenho ou só autenticação. Resposta dada: `wss://`
criptografa a sessão WebSocket INTEIRA (toda mensagem de gameplay, não
só login) — custo de CPU por mensagem real mas provavelmente modesto;
compromisso assumido de medir via teste de carga (`tools/loadtest/`)
depois de implementar, em vez de assumir, seguindo a regra de
"observabilidade antes de otimização" já estabelecida no projeto.
Implementação em si segue pendente — precisa de domínio pra certificado,
mexe em `websockets.serve()` (`server/main.py`) e na URL de conexão do
cliente (`client/network.py`); arquitetural o suficiente pra exigir
aprovação explícita do usuário antes de codar (ainda não dada).

**Pendente**: TLS/wss (item C1, aberto desde §123); medir impacto real
de CPU depois de implementar; considerar automatizar o deploy manual se
a fricção incomodar no dia a dia (hoje é 1 comando só,
`.\release_tools\deploy_server.ps1`, considerado suficiente por ora).

## 125. (movido) IA de combate por classe — bot de teste de carga E bot de BG (08/09/2026) — ver HISTORICO_FEATURES.md §29

## 126. (movido) Interceptar: dash completa e DEPOIS é desfeito, mais comum perto de parede/torre (09/09/2026) — ver HISTORICO_BUGS.md §39

## 127. (movido) Interceptar: dash pra um lugar e depois "volta" pro tile clicado, num loop (09/09/2026) — ver HISTORICO_BUGS.md §40

## 128. (movido) Interceptar não deve ser bloqueado por entidade (player/mob/minion/NPC), só por obstáculo sólido de verdade (09/09/2026) — ver HISTORICO_BUGS.md §41

## 129. (movido) Skill temporária da BG rouba o slot de uma skill real que ainda vai desbloquear (09/09/2026) — ver HISTORICO_BUGS.md §42

## 130. Mobs aleatórios paravam de perseguir/voltar pro spawn — `is_blocked` nunca resetado em RETURNING (10/09/2026)

Usuário relatou, jogando no servidor Hetzner de produção: "alguns mobs
não estão se movendo". Investigação inicial (pergunta direta ao
usuário, regra "confirmar cenário antes de investigação profunda")
isolou o padrão: mobs afetados atacavam normalmente quando o player
chegava perto (combate funcionando), mas nunca perseguiam o player se
afastando nem voltavam pro próprio spawn — e era aleatório, qualquer
mob do mapa podia travar, não um específico. Reiniciar o servidor
"curava" todos os mobs travados até então, o que apontou desde o
início pra um estado em memória que nunca se autocorrige, em vez de
terreno/spawn específico.

**Acesso ao servidor real**: esta foi a primeira vez nesta sessão que
o servidor de produção (Hetzner, `5.161.104.174`, usuário descobriu
que já existia acesso SSH configurado — chave em
`~/.ssh/rpg_hetzner`, usada por `release_tools/deploy_server.ps1`)
precisou ser investigado ao vivo. `logs/server.log` (INFO) não tem
granularidade de decisão de IA — existe um logger dedicado,
`debug/mob_combat_debug.py` (`MCL`, grava em `logs/mob_combat.log`,
eventos AGGRO/CHASE/IN_RANGE/ATK_FIRE/ATK_BLOCK/KITING/LEASH/
LOST_TGT/SLEEP/INVISIBLE), mas vem DESLIGADO por padrão
(`RPG_DEBUG_MOB_COMBAT` via `.env`, exige restart do serviço
systemd `rpg-server` pra aplicar — autorizado explicitamente pelo
usuário, mesmo desconectando quem estivesse online). Antes desse log
real voltar a capturar um caso novo, a causa raiz foi achada e
confirmada por leitura do código + reprodução sintética local — ver
abaixo.

**Causa raiz**: `AIControlled.is_blocked` é gravado como efeito
colateral de `TileValidationSystem.is_tile_walkable()`
(`engine/world_systems.py`, ~linha 547-552) sempre que um mob
(`Enemy`) tenta andar pro tile EXATO que um player ocupa — sinal
correto, usado pra decidir `CHASING → BLOCKED_BY_PLAYER`. O problema é
que essa flag só é resetada (`is_blocked = False`) no branch de
PERSEGUIÇÃO ativa: uma vez incondicionalmente por tick pra qualquer
mob com alvo válido ("limpa flags de bloqueio independente de estar
se movendo", ~linha 3522), e de novo dentro do recálculo de path de
CHASING (~linha 3877). **O branch de RETURNING nunca resetava essa
flag em lugar nenhum** — existem 2 pontos de entrada em RETURNING no
código (mob que perde o alvo dentro do próprio loop, e o `else` do
"comportamento de retorno" quando o estado já não é CHASING/etc.), e
nenhum dos 2 tocava `is_blocked`.

Sequência exata do bug: mob em CHASING tenta um passo cujo próximo
tile do path coincide com o tile atual do player (comum — perseguição
legítima leva o mob pra perto/sobre a posição dele) →
`is_tile_walkable` marca `is_blocked=True` como efeito colateral. Se,
no MESMO tick ou pouco depois, o mob perde o alvo (player sai de
range, morre, fica invisível, etc.) e transiciona pra RETURNING — a
partir daí, `if ai_control.path and not ai_control.is_blocked` nunca
mais é `True`: o mob recalcula um path de volta pro spawn
perfeitamente válido a cada ciclo (`path_recalc_interval`), mas a
chamada de `start_tile_movement` nunca é alcançada. Preso pra sempre,
mesmo sem absolutamente nenhum player por perto bloqueando nada —
única saída era o componente `AIControlled` ser recriado do zero (mob
morre e respawna, ou o servidor reinicia).

**Reprodução sintética (confirmada ANTES de qualquer acesso a dado
real do servidor)**: script isolado — spawna mob real via
`tests.helpers.first_mob`, força manualmente
`mob_ai.is_blocked=True; mob_ai.state="CHASING"; mob_ai.target_eid=-1`
(simulando exatamente a sequência acima) com o mob distante do próprio
spawn, roda 90 ticks (4.5s) via `run_ticks`. Antes do fix: mob
transicionava pra RETURNING, `path` populado corretamente a cada
ciclo, posição NUNCA mudava — `is_blocked` permanecia `True` pra
sempre. Depois do fix: mob andava de volta normalmente, `is_blocked`
voltava a `False`. Por regra do projeto ("corrigido" exige
reproduzir o sintoma relatado, não só um cenário sintético plausível)
— este caso fica como CONFIRMADO POR MECANISMO, não por replay do
servidor real: o usuário não trouxe ainda um log do `mob_combat.log`
capturando o caso ao vivo (debug ficou ligado pra a próxima
ocorrência), então a reprodução sintética é a evidência disponível
até lá, não uma prova definitiva de que não existe uma 2ª causa
contribuindo pro mesmo sintoma.

**Fix**: `ai_control.is_blocked = False` adicionado nos 2 pontos de
entrada/continuação de RETURNING (`engine/world_systems.py`,
`EnemyAISystem.update()`), logo após `ai_control.state = "RETURNING"`
em cada um — mesma semântica já usada no branch de perseguição ("cada
tick reavalia do zero se o próximo passo está bloqueado, nunca carrega
um valor de um tick/estado anterior"). Teste de regressão novo:
`tests/test_server.py::TestRegressionBugs::
test_mob_is_blocked_nao_fica_preso_em_returning_para_sempre` — mesmo
cenário do script de reprodução, usando um mob real spawnado pelo
`WorldServer` (nunca um mock).

**Lição pra não repetir**: qualquer flag booleana gravada como efeito
colateral de uma função de BAIXO nível (`is_tile_walkable` não sabe
nem deveria saber sobre estados de IA) e consumida por uma máquina de
estados de ALTO nível precisa de uma política de reset clara pra
TODOS os estados que a consultam, não só o estado "óbvio" onde ela
nasceu. Nenhum teste existente cobria RETURNING seguido de bloqueio —
os testes de `is_blocked`/`BLOCKED_BY_PLAYER` anteriores (ver
`tests/test_server.py`, linha ~670) todos fixavam o mob em CHASING.
## 131. Mapa da Battleground "esticou" — token de sheet desconhecido inflava a largura do mapa, sem quebrar a área jogável (10/09/2026)

Usuário relatou: "vi um bug no mapa da BG, era para ser um mapa
quadrado mas está um mapa retangular". Investigação direta (regra
"confirmar cenário antes de investigação profunda") isolou onde
aparecia: minimapa (tecla M, modo tela-cheia da BG) mais largo que
alto. Perguntado se o mesmo acontecia "no mundo" (fora da BG), usuário
confirmou que sim, mas esclareceu depois que era **dentro da própria
BG** — "parece que o mapa duplicou a largura, porém o que aumentou não
foi a parte explorável" — ou seja: a ÁREA JOGÁVEL continuava normal,
só a largura DECLARADA do mapa (usada por minimapa/câmera/limites)
tinha inflado.

**Verificação prévia importante**: `maps/map_1.csv` (mundo aberto) foi
checado e descartado como parte do mesmo bug — a variação de largura
por linha que ele tem (300 a 1944 colunas reais, perfil suave e
simétrico por altura) é a forma ORGÂNICA intencional do continente,
já documentada e já tratada corretamente (ver regra "Largura de mapa
é a MAIOR linha do terrain_matrix" no `CLAUDE.md`) — confirmado
inspecionando o conteúdo real das linhas mais largas (tokens válidos
de `ter_X_Y`, nenhum token desconhecido). `map_cave_east` tem variação
pequena pelo mesmo motivo legítimo. O minimapa do mundo aberto também
usa um modo geometricamente diferente do da BG
(`ui/minimap.py::render` — janela quadrada FIXA ao redor do player,
nunca pode "esticar" por causa do formato do mapa; só
`render_fullmap`, usado "pra dentro de instância estilo MOBA", projeta
o mapa INTEIRO proporcionalmente, e é o único dos dois vulnerável a
este bug).

**Causa raiz**: `maps/moba_battleground_terrain.csv` tinha 298 células
com o valor `"txg_0_0"`/`"txg_0_4"`/etc. — uma família de sheet de
terreno que **nunca foi cadastrada** em `SHEET_TILE_MAP`
(`engine/tileset.py`; "txg" só existe como PREFIXO DE EXEMPLO num
comentário de docstring, nenhum arquivo `assets/tiles/` corresponde a
ele — provavelmente sobra de uma textura de teste nunca finalizada).
`engine/map_loader.py::_parse_terrain_cell(cell)` tinha um fallback
inseguro pra esse caso: `if cell in SHEET_TILE_MAP: ... ; return cell,
""` — quando o token NÃO está no catálogo, a função devolvia o
PRÓPRIO token (string de vários caracteres, ex: 8 chars) como se fosse
o `terrain_char` de 1 tile só. `_load_two_layer`/`_load_single_layer`
montam cada linha via `"".join(terrain_row)` — assumindo SEMPRE 1
caractere por tile — então cada ocorrência de um token de N
caracteres empurrava o resto da linha N-1 posições pra frente,
desalinhando a leitura de QUALQUER tile depois dele na mesma linha
(inclusive colisão/andabilidade real, não só o desenho). Resultado
medido: a linha terminava com até 208 "caracteres" em vez de 100
colunas reais — `map_width_tiles` (lido como a MAIOR linha,
`engine/entity_factory.py::create_tilemap`, regra já documentada pro
caso LEGÍTIMO de map_1) herdava esse valor inflado, e
`render_fullmap()` (`ui/minimap.py`) usa exatamente
`cols`/`rows` pra decidir a proporção do mapa desenhado — resultando
no "mapa esticado" relatado.

Por que a área jogável não mudava: a maioria das colunas REAIS
continuava no lugar certo antes da 1ª ocorrência de `txg_*` em cada
linha (e logo depois de cada ocorrência, se repetida); o efeito visual
predominante era a PROPORÇÃO do mapa no minimapa ficar errada, não o
terreno virar intransponível — mas colunas específicas depois de cada
token inflado na MESMA linha liam o terrain_char de uma posição real
DIFERENTE da pretendida (bug real de colisão, mesmo sem o usuário ter
localizado um caso concreto ainda).

**Fix**:
1. `maps/moba_battleground_terrain.csv` — as 298 células `txg_*`
   trocadas por `"G"` (grama comum, mesmo valor de reserva já usado
   pelo resto do sistema pra terreno sem dado específico). Mapa volta
   a carregar 100×100 uniforme.
2. `engine/map_loader.py::_parse_terrain_cell` — fallback endurecido:
   token desconhecido com `len(cell) > 1` cai pra `("G", "")` em vez de
   devolver o token cru. Protege QUALQUER mapa futuro (não só a BG)
   contra a MESMA classe de corrupção silenciosa — uma sheet removida
   do catálogo, nunca cadastrada, ou um erro de digitação no editor
   nunca mais consegue inflar uma linha inteira sem nenhum aviso.

**Testes novos**: `tests/test_map_loader_terrain_parsing.py` —
unitários pra `_parse_terrain_cell` (token desconhecido de N chars →
G; char simples desconhecido continua passando direto; token
REGISTRADO continua funcionando normal) + 1 teste de integração
carregando o mapa REAL da battleground e exigindo que toda linha do
`terrain_matrix` tenha exatamente 100 colunas (prova de regressão
contra o bug relatado, não um cenário sintético à parte).

**Lição pra não repetir**: qualquer função que PARSEIA um token
externo (arquivo de mapa, config, etc.) pra um formato com invariante
de tamanho fixo (aqui: 1 char por tile) precisa ter um fallback SEGURO
pro caso "não reconheço este valor" — nunca devolver o valor bruto
assumindo que ele "deve" caber no formato esperado. A mesma classe de
bug já apareceu antes em `_register_catalog_tiles`/`group_tile_ids()`
(§103/§104, flag de separador de prefixo) e em `create_tilemap`
(§97, largura de mapa) — três bugs diferentes, mesma lição:
"dado inesperado vindo de um catálogo/mapa real" precisa de um teste
de integração carregando o MAPA REAL, não só fixtures sintéticas, ou o
problema só aparece quando o usuário joga de verdade.
## 132. Vitória Iminente nunca ganhava carga matando JOGADOR — só matando mob (13/09/2026)

Usuário pediu pra investigar: "Vitória Iminente não está ganhando carga
quando o guerreiro mata um alvo". Primeira reprodução headless (mob
morto via `apply_damage_core` + `killer_eid`, skill só aprendida, não
na hotbar) funcionou perfeitamente — a mesma cadeia validada no fix
original de 03/07/2026 (ver seção "RESOLVIDO" mais acima) continuava
intacta. Perguntado ao usuário se a skill estava de fato na hotbar
(sim) e onde exatamente o bug aparecia — resposta: "só na BG que não
funciona".

**Causa raiz**: a concessão de carga (`on_kill=="charge"`, iterando
`SKILL_CATALOG` com lazy-create) vivia SÓ dentro do loop de morte de
mob/minion/torre (`ServerDeathHandler.update()`,
`server/server_death_handler.py`). Morte de JOGADOR (crédito de kill
estilo LoL — último a bater dentro da janela de assist) passa por
`RespawnMixin._handle_player_death` (`server/respawn_system.py`), um
caminho de código TOTALMENTE separado (unificado em 16/08/2026 só pra
crédito de kill/assist/gold, nunca ganhou a lógica de carga). Grep
case-insensitive por "on_kill"/"charge"/"vitoria" no arquivo inteiro
(512 linhas) não achou nenhuma ocorrência — confirmação direta, não
suposição.

Como a Battleground é o modo cuja atividade principal é matar
JOGADORES (não mobs), a carga nunca acendia lá — mesmo matando
mob/minion/torre DENTRO da própria BG funcionando normalmente (mesmo
código de sempre), e mesmo fora da BG contra mob (também mesmo código).
O usuário só reparou porque em PvE geralmente mata mob, e na BG mata
principalmente gente.

**Fix**: lógica extraída de dentro do loop de `ServerDeathHandler` pra
`grant_on_kill_charge_skills(world, world_server, killer_eid, mob_eid)`
(módulo `server/server_death_handler.py`, função de módulo — nunca
método de mixin, pra ser chamável dos dois pontos de morte sem
depender de qual mixin herda o quê). Notifica o cliente via
`WorldServer.queue_stats_update()` diretamente (ponto único do canal
privado) em vez do "bag" `pending_xp` que só `ServerDeathHandler` tinha
— unifica os dois call sites no mesmo mecanismo de notificação, não só
na mesma lógica de concessão. Chamada nova em
`RespawnMixin._handle_player_death`, logo após `_pk_killer` ser
decidido (mesmo jogador que já ganha o ouro/kill credit — nunca um
assistente).

**Testes novos**: `tests/test_on_kill_charge_skills.py` —
`TestOnKillChargeMobPath` (regressão do caminho original após a
extração, skill só aprendida → lazy-create funciona igual);
`TestOnKillChargePlayerPath` (o bug relatado: matar jogador concede a
mesma carga; um 3º player que só assistiu, sem ser o killer creditado,
NÃO ganha carga — usa o caminho de combate REAL,
`_process_player_attacks`, não um atalho direto em
`_handle_player_death`, mesma disciplina de `test_bg_kill_credit.py`).

**Lição pra não repetir**: quando uma mecânica nova é implementada
"no ponto de morte", checar quantos CAMINHOS DE CÓDIGO diferentes
processam morte de verdade no projeto (aqui: mob/minion/torre vs.
jogador PvP são 2 sistemas distintos desde sempre, por design — nunca
foram unificados) — implementar só num deles e assumir que "morte é
morte" é exatamente a classe de bug capturada aqui. `PROBLEMAS_
ARQUITETURA.md` §130 (mesma sessão anterior) já tinha o comentário
"ÚNICO ponto que credita kill/assist de player" documentando que
existem 2 famílias de morte separadas — a lição já estava escrita,
só não foi cruzada de volta quando Vitória Iminente foi implementada
(03/07/2026, antes até da unificação de kill credit de 16/08/2026
existir).
## 133. Bola de Fogo "com alcance curto" — não era a skill, era a perseguição parando cedo demais (14/09/2026)

Continuação do nivelamento de alcance de ranged (§ commits d5791df/
e9382d2/4dbeec7, mesma sessão). Usuário relatou, após deploy: "a bola
de fogo do mago ainda está com alcance 5" (mesmo já tendo sido
ajustada pra 7). Investigação em 2 etapas erradas antes de achar a
causa real:

**1ª hipótese (parcialmente certa, mas não era a causa do sintoma
relatado)**: `_skill_bola_de_fogo` (`engine/skill_handlers.py`) chamava
`self._resolve_target(combat_state, tile_move, 30)` — **30 fixo, nunca
`skill.cast_range`** — diferente das outras 5 skills do mesmo
nivelamento (Calcinar, Tiro Repulsivo, Picada de Escorpião, Flecha
Reiterada, Polimorfia), que já liam `skill.cast_range` corretamente.
Real, mas 30 > 7 (mais permissivo, não mais restritivo) — não explica
"alcance curto". Corrigido mesmo assim (consistência com as outras 5),
mas não era a causa raiz do sintoma.

**Causa raiz real**: `ui/systems.py::_mage_attack_range()` — usada pela
perseguição genérica por-tick do mago pra decidir "até onde andar antes
de parar e aguardar cast manual" — retorna o **MENOR `cast_range` entre
TODAS as skills ofensivas com alvo da hotbar** (decisão de design já
documentada, 26/08/2026: "garante que, quando a perseguição parar,
TODAS as skills já estão em alcance"). Como a Calamidade Flamejante
(cast_range = distância da mira) tinha acabado de cair pra 3 nesta
MESMA sessão, o mago passou a parar de perseguir a 3 tiles pra
QUALQUER skill — inclusive ao apertar Bola de Fogo (cast_range 7) —
porque o personagem nunca chegava mais perto de 3, satisfeito com o
mínimo global. Confirmado com o usuário: ele contou a distância com
precisão (grade/tiles) e o personagem literalmente parava de ANDAR
naquele ponto ao apertar a skill — não era uma rejeição "Fora de
alcance", era a aproximação parando cedo.

**Fix**: `CombatState.pursuit_skill_range` (novo campo, client-only) —
gravado em `_use_skill_visual_only` (`ui/systems.py`) com o
`_max_range_tiles` já calculado ali pro range-check normal, no
MOMENTO em que o jogador aperta uma skill ofensiva com alvo (mesmo se
o check falhar "Fora de alcance" — é EXATAMENTE esse caso que precisa
andar até lá). A perseguição por-tick do mago passa a preferir esse
valor (`combat_state.pursuit_skill_range or self._mage_attack_range(...)`)
— só cai pro mínimo genérico quando nenhuma skill específica foi
apertada ainda (engajou só por clique direito/Espaço). Reseta junto
com `target_entity_id`/`is_pursuing` (alvo morre/fica inválido).

**Sem teste automatizado dedicado**: a lógica vive dentro de
`PlayerInputSystem._process_target` (`ui/systems.py`), método grande e
fortemente acoplado (GameEngine/auto_move/combat_stats como
parâmetros) — puramente cosmético (nunca afeta autoridade de
gameplay/servidor), decisão explícita de não construir um harness de
teste isolado só pra isso (custo desproporcional ao risco). Suíte
completa confirma ausência de regressão (1634 passed, 1 failed
pré-existente/timing flake); validação manual em jogo pendente —
usuário avisa se o comportamento não bater.

**Lição pra não repetir**: um mecanismo de "usa o mínimo entre N
coisas pra garantir margem de segurança" (aqui: perseguição genérica)
é sensível a QUALQUER uma dessas N coisas mudar de valor — reduzir
drasticamente só UM item (Calamidade Flamejante) teve efeito colateral
sobre TODOS os outros (qualquer skill do mago), sem nenhuma mudança de
código relacionada a eles. Balanceamento de conteúdo que alimenta um
"mínimo/máximo compartilhado" precisa considerar esse acoplamento
antes de declarar "só mudei o número de uma skill".

## 134. Check de alcance de skill no cliente usava distância real (Euclidiana em pixel) em vez de Chebyshev — diagonal e reto davam resultado diferente (14/09/2026)

Durante o mesmo nivelamento de alcance de ranged, usuário percebeu (sem
ainda ter reportado como bug — pergunta exploratória: "medir alcance em
tiles é ruim pq a diagonal é maior, medir em pixel dá o mesmo
resultado") que a distância "sentida" em jogo variava com a direção.

**Causa raiz**: dois pontos do código mediam "distância até o alvo" com
métricas DIFERENTES pro mesmo conceito (alcance de skill):
- **Servidor, autoritativo** — `SkillHandlers._resolve_target`
  (`engine/skill_handlers.py`) usa `chebyshev()` (`engine/utils.py`) —
  `max(|dx|,|dy|)` em TILE, "distância de rei do xadrez": diagonal conta
  IGUAL a reto. Consistente com o movimento em tile do jogo, onde andar
  na diagonal custa o mesmo 1 passo que andar reto (mesmo
  `move_duration`, sem penalidade de `√2`).
- **Cliente, preditivo** — `PlayerInputSystem._use_skill_visual_only`
  (`ui/systems.py`) media Euclidiana em PIXEL contínuo
  (`Position.x/y`, `dx²+dy² > max_px²`) pra decidir se mostra "Fora de
  alcance" antes de mandar `CAST_SKILL`.

Pra um cast_range de N tiles, "N passos" na diagonal cobre ~41% mais
distância real em pixel do que "N passos" reto (N×32×√2 vs N×32). Como
o servidor aceita os dois igualmente (mesmo custo de passo) mas o
cliente só aceitava dentro de um CÍRCULO de raio N×32px, o cliente podia
rejeitar como "Fora de alcance" um alvo que o servidor aceitaria na
diagonal — ou, perto da borda reta, aceitar algo que a régua "tiles
livres" do usuário não bateria. A métrica de tile (Chebyshev) em si não
é o problema — é o padrão certo dado que o movimento também é
Chebyshev; o problema era o cliente usar uma métrica DIFERENTE da
autoritativa pro mesmo check.

**Fix**: `_use_skill_visual_only` passou a calcular a distância com
`chebyshev(tile_move.current_tile_x, ..., alvo.current_tile_x, ...)`
— mesma função, mesmos campos (`TileMovement.current_tile_x/y`) que o
servidor usa em `_resolve_target` — em vez de Euclidiana em
`Position.x/y` convertida pra pixel. `_max_range_tiles`/
`_min_range_tiles` (já em tiles, lidos do catálogo) comparam direto
contra o inteiro de tiles, sem conversão pra pixel nem tolerância
(`RANGE_TOLERANCE_PX` não se aplica mais aqui — o servidor não usa
tolerância nesse check, comparar exatamente igual evita o cliente ser
mais permissivo OU mais restritivo que a autoridade). O check de LOS
(linha de visão pra skill com projétil), que já vinha depois no mesmo
bloco, continua usando `Position.x/y` pra achar o tile atual — não
mudou, só passou a rodar dentro da guarda de `TileMovement` em vez de
`Position` (ambos sempre existem juntos em entidade com movimento,
efeito prático nulo).

**Sem teste automatizado dedicado** — mesmo motivo do §133 (lógica
client-only, dentro do mesmo método grande e acoplado,
`PlayerInputSystem._use_skill_visual_only`). Suíte completa validada
sem regressão antes de fechar (ver commit).

**Lição pra não repetir**: quando duas camadas (cliente preditivo vs.
servidor autoritativo) fazem o MESMO check de gameplay, elas precisam
usar a MESMA métrica/função — nunca reimplementar com uma métrica
"equivalente" (pixel vs. tile, Euclidiana vs. Chebyshev) achando que dá
no mesmo. A malha de tolerância (`RANGE_TOLERANCE_PX`) cobria só lag de
rede/animação — nunca foi desenhada pra cobrir uma DIFERENÇA
estrutural de métrica entre as duas pontas.

## 135. Alcance de ATAQUE virou distância real (Euclidiana) em vez de Chebyshev — diagonal não "estica" mais (14/09/2026)

Continuação direta do §134, mesmo dia. Depois do fix de §134 (cliente
passou a usar Chebyshev, igual ao servidor), o usuário voltou com uma
pergunta melhor: "medir a distância de ataques em tiles é ruim pq não é
a mesma distância em todas as direções, na diagonal a distância é muito
maior". Confirmado em jogo: a 5 tiles Chebyshev na diagonal (dist REAL
~7.07 tiles) ainda conseguia lançar Bola de Fogo com cast_range=6 — o
quadrado Chebyshev sempre foi "maior" que o círculo que o jogador
enxerga na tela.

**Decisão**: trocar a métrica AUTORITATIVA de alcance de ATAQUE de alvo
único (skill/auto-attack/torre/minion/mob) de Chebyshev pra Euclidiana
real em tile — círculo de verdade, sem "esticar" na diagonal. Escopo
fechado com o usuário via pergunta explícita: SÓ alcance de alvo único,
NUNCA raio de área (AoE) — Calamidade Flamejante, Canção de Ninar,
Impacto, Fatiador de Corpos, Brado Provocativo continuam Chebyshev
(quadrado), decisão consciente de não mexer nisso agora (encolheria a
área de efeito nas quinas, uma decisão de balanceamento separada).

**Novo ponto único de verdade**: `engine/utils.py::in_range(ax, ay, bx,
by, max_range) -> bool` — `dx²+dy² <= max_range²` (Euclidiana, sem
sqrt). `chebyshev()` continua existindo, intocado, pra tudo que é
movimento/pathing/leash/adjacência/aggro-detecção (fora de escopo).

**EXCEÇÃO deliberada, achada DEPOIS de quebrar testes na 1ª tentativa**:
alcance melee (`attack_range<=1` — adjacência de 8 direções) continua
Chebyshev, NUNCA `in_range()`. Motivo: adjacência diagonal tem
distância REAL ~1.41, que ficaria fora de um `in_range(max_range=1)` —
trocar cegamente quebraria golpe corpo-a-corpo em 4 das 8 direções
(regressão real, pega ANTES de commitar, pela suíte + por análise
manual do próprio código antes de rodar). Todo ponto que decide alcance
de ataque branch por `is_ranged`/`attack_range<=1`: usa `chebyshev()`
pra melee, `in_range()` pra ranged de verdade.

**Achado 2, também pego pela suíte antes de commitar**: `MinionSystem.
_in_range_los` (`engine/world_systems.py`) é uma função COMPARTILHADA
cujos dois chamadores reais passam `aggro_range_tiles` (raio de
PERCEPÇÃO/detecção — "vejo esse alvo, vale a pena perseguir?" — não
"alcance de ataque"), nunca `attack_range_tiles` diretamente. Trocar
essa função pra Euclidiana quebrou 2 testes de throttle de recálculo de
rota de minion (`tests/test_minions.py`, geometria do teste desenhada
em Chebyshev de propósito). Revertido pra Chebyshev — aggro/detecção
fica FORA do escopo combinado com o usuário (só "alcance de ATAQUE"), o
gate real de ataque do minion é um código separado (`_minion_atk_ok`,
mesmo arquivo), esse sim trocado pra Euclidiana com a exceção de melee
acima.

**Pontos trocados pra `in_range()`** (todos com a exceção de melee onde
aplicável — ver cada arquivo pro detalhe):
- `engine/skill_handlers.py::_resolve_target` — gate de alcance de
  skill, compartilhado cliente+servidor (6 call sites internos).
- `server/combat_processor.py::_resolve_auto_attack` — auto-attack
  melee/ranged (branch por `_is_ranged`).
- `engine/world_systems.py::TowerSystem._in_range_los` — torre sempre
  ranged, sem exceção de melee.
- `engine/world_systems.py::MinionSystem` — só o gate REAL de ataque
  (`_minion_atk_ok`, branch por `is_ranged`); `_in_range_los`
  (aggro/detecção) ficou de fora, ver achado 2 acima.
- `engine/world_systems.py::EnemyAISystem` — `in_attack_range` (branch
  por `ai_control.is_ranged`) e o gerador de tiles candidatos de
  ataque/kiting (`potential_attack_tiles`, `_do_kiting`) — **precisavam
  mudar junto com a autoridade**, senão o mob anda até um tile que ERA
  suficiente pelo Chebyshev mas não é mais pelo círculo real, ficando
  preso tentando se aproximar pra sempre (mesma classe de bug do §133,
  causada pela troca de métrica em vez do mínimo de hotbar).
- `server/world_server.py::_enemy_towers_covering` — réplica da métrica
  de torre pro dive-gate do bot de BG.
- `ui/systems.py` — gate de alcance de skill do cliente
  (`_use_skill_visual_only`, mesma exceção de melee), parada de
  perseguição do mago (`_mage_skill_in_range`), gate de disparo do
  arqueiro (`bow_range`), e `_ranged_stop_tile` (destino de onde parar
  de andar pra usar skill/atirar — reescrito pra escalar pela distância
  REAL em vez de Chebyshev, mesmo motivo do item EnemyAISystem acima).

**Teste automatizado**: `tests/test_in_range_euclidean_attack.py`
(novo) — unitários de `in_range()` (reto na borda, diagonal "esticando"
sob Chebyshev mas fora do círculo real, diagonal dentro do círculo) +
integração via `HeadlessSkillHandler._resolve_target` reproduzindo o
cenário exato relatado (Bola de Fogo, cast_range=6, alvo a 5 tiles
diagonal rejeitado; a 6 tiles reto aceito) + regressão da exceção de
melee (adjacência diagonal/ortogonal continuam válidas). Suíte completa
sem regressão (1633 passed, 2 failed pré-existentes/timing flake sem
relação, mesma família de sempre).

**Lição pra não repetir**: uma métrica de distância nova nunca é "trocar
uma função por outra" — precisa (1) mapear TODOS os call sites antes de
tocar em qualquer um (15 arquivos usavam `chebyshev()`, a maioria fora
de escopo), (2) checar se algum desses call sites tem um caso-limite
onde a métrica antiga era ESSENCIAL, não incidental (melee/adjacência
de 8 direções), e (3) atualizar TODO ponto que decide "até onde
andar/reposicionar" pra ficar consistente com a nova autoridade — uma
troca de métrica só na decisão final, sem tocar no que decide a
aproximação, reproduz a mesma classe de bug de "personagem para de
andar achando que já chegou" só que por um motivo novo.

## 136. Nova Congelante com cast dentro da BG mesmo com Precisão Elemental maxada — `talent_allocated` só trocava o dict, nunca reaplicava os efeitos (14/09/2026)

Usuário relatou: dentro da BG (onde `server/instance_progression.py::
enter_normalized_progression` aloca TODOS os talentos no máximo pra
todo mundo), Nova Congelante devia ser instantânea (Precisão Elemental
maxada = -1.0s de cast, zerando o 1.0s base) mas continuava mostrando
cast — "o som é emitido no momento que aperta a skill e depois do cast
[que não era pra acontecer]". Fora da BG, funcionava perfeitamente.

**Causa raiz**: `client/network_handlers.py::_handle_msg_stats_update`
recebe o campo `talent_allocated` (emitido por `instance_progression.
py::_push_stats_update`, ver docstring dessa função — mecanismo já
existia desde 02/08/2026 especificamente pra sincronizar a árvore de
talentos da instância pro cliente) e fazia só `_tt_alloc.allocated =
dict(payload["talent_allocated"])` — trocava o DICT, mas nunca chamava
`ui.talent_system.TalentSystem.apply_talent_effects()`, a ÚNICA função
que recalcula os `cs_flags` comportamentais (`ice_cast_time_reduction`
entre outros, e também modifiers de atributo + skills desbloqueadas por
talento) a partir de `TalentTree.allocated`. Fora da instância, isso
nunca aparecia como bug porque `apply_talent_effects()` SEMPRE é
chamada no mesmo lugar que aloca um ponto (`ui/talent_system.py`,
handle_events) — o gap só existia nesse ÚNICO ponto de entrada
alternativo (sync de rede, não ação do jogador).

O SERVIDOR nunca teve esse bug: `WorldServer._apply_talent_modifiers`
(chamado por `enter_normalized_progression` na mesma função que monta
`new_tt.allocated`) sempre recalculou `CombatStats.ice_cast_time_reduction`
certo — por isso a skill em si (o efeito real, resolvido
server-side) já completava instantaneamente, e o SOM (disparado pela
confirmação do servidor) tocava na hora certa. O que ficava "grudado"
era só a barra de cast VISUAL do cliente (`ui/systems.py::
_use_skill_visual_only`, que lê `CombatStats.ice_cast_time_reduction`
LOCAL pra decidir se cria um `SpellCast` visual com barra) — o valor
local nunca saía de 0.0 (o real, de fora da instância), então o
cliente sempre desenhava 1s de cast, mesmo a skill já tendo resolvido.

**Fix**: `_handle_msg_stats_update` chama `self._talent_system.
apply_talent_effects()` (mesmo padrão já usado em `client/
save_sync_handlers.py::apply_save_state`, `try/except` porque
`_talent_system` pode não existir em todo fixture/contexto) logo depois
de trocar `tt.allocated`. Como `chosen_build` já bate com a classe
(instância normalizada sempre usa `CLASS_BUILD_MAP` correto,
`apply_talent_effects()` só reembolsa/limpa quando NÃO bate), a
re-aplicação é segura — recalcula cs_flags, modifiers e skills
desbloqueadas por talento a partir da alocação NOVA, sem tocar em nada
que já estava certo.

**Teste automatizado**: `tests/test_instance_talent_effects_sync.py`
(novo) — reproduz o cenário exato (talent_allocated com Precisão
Elemental maxada → `ice_cast_time_reduction` deve ir a 1.0) e o
oposto (voltar a 0 ao sair da instância). Confirmado por revert-to-
confirm que os 2 testes falham sem o fix. Suíte completa sem regressão.

**Lição pra não repetir**: um campo de sync novo que espelha um
sub-componente inteiro (`TalentTree.allocated`, `Inventory`, etc.) só
resolve o problema de EXIBIÇÃO/validação direta desse dado — se esse
dado ALIMENTA um recálculo derivado em algum outro lugar (cs_flags,
modifiers, stats compostos), o sync precisa disparar esse recálculo
também, não só copiar o valor bruto. Mesma classe de gap já visto em
`recalculate_preserving_hp_pct`/`grant_visible_buff` (mutação de um
dado que alimenta outro cálculo precisa passar pelo pipeline completo,
nunca só escrever o campo final).

## 137. Som de impacto da Nova Congelante tocava 2x — previsão local + eco do próprio SKILL_EFFECT de rede (14/09/2026)

Usuário relatou, mesmo dia do §136, mas confirmado que acontece em
QUALQUER lugar (não só BG): "o som é emitido no momento que aperta a
skill, e a outra parece que acontece quando o servidor confirma a
skill, por conta do delay".

**Causa raiz**: dois caminhos INDEPENDENTES tocam o mesmo som de
impacto pro conjurador:
1. **Previsão local** — `ui/spell_system.py::SpellCastSystem.
   _apply_nova_congelante` (`_CAST_HANDLERS["nova_congelante"]`),
   chamada quando o `SpellCast` LOCAL do próprio cliente termina
   (`spell_cast.elapsed >= spell_cast.cast_time`) — toca
   `SOUNDS.play_spell("nova_congelante", "impact")` na hora que a
   barra de cast do jogador enche.
2. **Confirmação de rede** — `server/spell_completion_processor.py`
   (bloco genérico "SKILL_EFFECT: evento de apresentação da
   conclusão", usado por TODA skill sem projétil) enfileira um
   `SKILL_EFFECT{event: "impact"}` na conclusão real (servidor),
   broadcast via AOI — **pensado pra QUALQUER OUTRO player perto ouvir
   a skill**, mas o PRÓPRIO conjurador também está dentro do seu
   próprio raio de AOI e recebe a mensagem de volta.
   `client/network_handlers.py::_handle_msg_skill_effect` lia
   `SKILL_CATALOG[sid]["effects"]["impact"]["sound"]` e tocava de novo
   sempre que existia, sem checar se o caster já tinha ouvido local.

Calcinar/Polimorfia (mesma categoria — cast_time>0, handler local
próprio) NUNCA tiveram esse bug porque simplesmente não têm
`"effects"` no catálogo — `_handle_msg_skill_effect` não acha nada pra
tocar e retorna cedo. Nova Congelante é a ÚNICA skill nessa categoria
que definia `effects.impact.sound` (pensado originalmente só pros
espectadores).

**Fix**: flag nova no catálogo, `effects.<evento>.skip_local_caster:
True` (`content/skill_config.py::nova_congelante`) — checada em
`_handle_msg_skill_effect` ANTES de tocar som pro `is_local` (branch do
próprio conjurador); o branch `else` (outros players, som posicional)
nunca é afetado — continuam ouvindo a skill normalmente. Decisão
confirmada com o usuário: manter espectadores ouvindo, só suprimir a
duplicata pro próprio conjurador (não remover o SKILL_EFFECT inteiro).

**Teste automatizado**: `tests/test_skill_effect_no_local_dup_sound.py`
(novo) — confirma que o conjurador NÃO ouve de novo (`caster_eid ==
my_eid`), que outro player continua ouvindo normal (`caster_eid !=
my_eid`), e guarda de regressão pra Calcinar (sem `"effects"` no
catálogo, handler de rede nunca toca nada). Confirmado por
revert-to-confirm que o teste do conjurador falha sem o fix.

**Lição pra não repetir**: uma skill com handler de PREVISÃO LOCAL
(`ui/spell_system.py::_CAST_HANDLERS`) que TAMBÉM define
`effects.<evento>.sound` no catálogo (pra espectador ouvir) duplica o
som pro próprio conjurador por padrão — o broadcast AOI do servidor não
distingue "quem já sabe" de "quem precisa saber agora". Toda skill nova
dessa categoria (cast_time>0 + handler local + som no catálogo) precisa
decidir explicitamente se o conjurador já ouviu localmente e, se sim,
marcar `skip_local_caster` — não assumir que o broadcast genérico é
sempre seguro pra quem já previu o resultado.

## 138. Dispatch de mensagem de rede virando dict quebrou 17 handlers na 1ª tentativa — método existe só no MRO de `GameEngine`, não em `NetworkHandlers`

15/09/2026, implementando o item 7 de `CLIENT_PERFORMANCE_REVIEW.md`
(trocar a cadeia if/elif de 54 ramos de `client/network_handlers.py::
_handle_net_message` por um dict de despacho O(1)).

**1ª tentativa (errada)**: montar `NetworkHandlers._NET_DISPATCH = {
MsgType.X: NetworkHandlers._handle_msg_x, ...}` — um dict de módulo
referenciando a FUNÇÃO diretamente pela classe `NetworkHandlers`,
assinado uma vez no import. Quebrou imediatamente
(`AttributeError: type object 'NetworkHandlers' has no attribute
'_handle_msg_duel_invite'`) — pego ANTES de commitar, rodando o módulo
isolado.

**Causa raiz**: `client/network_handlers.py::NetworkHandlers` é uma
MIXIN — nunca instanciada sozinha, só herdada por `GameEngine`
(`game.py`) junto de ~20 outras mixins (`DuelHandlers`, `PartyHandlers`,
`ArenaHandlers`, `BattlegroundHandlers`, `BgQueueHandlers`, etc.). O
código ORIGINAL (`if msg_type == MsgType.DUEL_INVITE:
self._handle_msg_duel_invite(payload)`) funcionava porque `self.método()`
resolve pelo MRO INTEIRO de `GameEngine` em runtime — 17 dos 54 métodos
chamados ali (todos de duelo/grupo/arena/BG) são definidos em mixins
IRMÃOS, nunca em `NetworkHandlers` propriamente. Um dict estático
referenciando `NetworkHandlers.método` direto só encontra o que está
DEFINIDO NA PRÓPRIA CLASSE — quebra silenciosamente (na verdade
ruidosamente, `AttributeError` no import) pra qualquer método herdado de
outro lugar do MRO.

**Fix**: dict guarda NOME (string) do método, não a função —
`NetworkHandlers._NET_DISPATCH_NAMES = {MsgType.X: "_handle_msg_x", ...}`
— resolvido em runtime via `getattr(self, name)(payload)` dentro de
`_handle_net_message`. Isso preserva EXATAMENTE a mesma resolução
dinâmica via MRO que o código original já fazia, só trocando "54
comparações sequenciais" por "1 lookup de dict + 1 getattr".

**Verificação**: script à parte (`getattr(GameEngine, name)` pra cada um
dos 54 nomes) confirmou os 54 resolvendo certo contra a classe REAL
`GameEngine`, não só `NetworkHandlers` isolada — teste que teria pegado
o erro da 1ª tentativa também, se rodado antes.

**Lição pra não repetir**: qualquer dispatch table (dict de
despacho/registro/plugin) que substitui uma cadeia de `self.método()` só
pode referenciar a FUNÇÃO direto quando o método é garantidamente
definido na MESMA classe do dict. Se a classe é uma mixin (convenção já
usada em ~20 lugares neste projeto pra compor `GameEngine`), o dict
precisa guardar o NOME e resolver via `getattr(self, name)` — nunca
assumir que "está definido em algum lugar que o self.método() original
achava" significa "está definido nesta classe especificamente".

## 139. `BlacksmithSystem`/`TrainerSystem` chamavam métodos privados de outro System direto — e um deles chamava um método que nunca existiu

15/09/2026, item 4 (crítico) da revisão de performance/arquitetura
(`arquitetura/CLIENT_PERFORMANCE_REVIEW.md`). Achado pelos 3 agentes de
análise (arquitetura, código, mercado) de forma independente: `ui/
crafting_system.py::BlacksmithSystem` e `ui/trainer_system.py::
TrainerSystem` recebiam `shop_system`/`quest_dialog`/`quest_system`
injetados no construtor (`game.py`) e chamavam métodos PRIVADOS
(`self._shop_system.open_for(eid)`, `self._quest_dialog._open_dialog(eid)`,
`self._quest_dialog._get_available_quests(eid)`, etc.) — violação direta
da regra do projeto ("um System nunca chama outro System diretamente").

**Achado extra fazendo o refactor**: `BlacksmithSystem._draw_action_menu`
chamava `self._quest_system.can_start(qid)` pra decidir se uma quest
"iniciável" aparece no menu do ferreiro — `can_start` **nunca existiu**
em `QuestSystem` (`grep -rn "def can_start"` no projeto inteiro: zero
resultados). Como `game.py` sempre injeta um `QuestSystem` real
(`quest_system=self._quest_system`, nunca `None` em produção), essa
linha quebraria com `AttributeError` assim que QUALQUER ferreiro do jogo
tivesse uma quest disponível pra iniciar (`quest_ids` com algo ainda não
ativo/completo) — nunca pego porque nenhum teste automatizado
exercitava esse caminho antes de agora.

**Fix, em duas partes**:

1. **Comandos** ("abra isto pra mim" — `open_for`/`_open_dialog`): novo
   componente `NPCInteractionRequest` (`engine/components.py`, `kind` +
   `target_eid`). Quem PEDE (Blacksmith/Trainer) só escreve o
   componente no player; quem ABRE (`ShopSystem`/`QuestDialogSystem`)
   lê e consome (remove) no INÍCIO do próprio `update()` — SÓ remove
   quando `kind` bate com o seu próprio, pra não "roubar" um request de
   outro `kind` dependendo da ordem de `update()` entre Systems no
   mesmo frame.
2. **Consultas** (`can_turn_in`/`_get_available_quests`/
   `_get_completable_quests`/`_get_inprogress_quests` — leitura pura de
   `QuestLog`/`QuestGiver`/`CharacterStats`, sem side-effect): extraídas
   pra função de MÓDULO em `engine/quest_logic.py`
   (`get_available_quests`/`get_completable_quests`/
   `get_inprogress_quests`/`player_level`/`player_class_id`/
   `turn_in_ids_for`/`quests_claimed_for_turn_in`) — chamáveis por
   QUALQUER System com `(world, player_eid, npc_id)`, sem precisar de
   referência a nenhuma instância de `QuestSystem`/`QuestDialogSystem`.
   `QuestDialogSystem`'s métodos privados viraram wrappers finos
   delegando pra essas funções (call sites internos do próprio arquivo
   continuam intocados). O `can_start` quebrado virou uma chamada real
   a `get_completable_quests`/`get_available_quests` (que JÁ faziam
   exatamente essa checagem, com requires/level/class — mais completo
   que o inline original, que só checava not-active/not-completed).

`game.py` não injeta mais `shop_system=`/`quest_dialog=`/
`quest_system=` em `BlacksmithSystem`/`TrainerSystem` — os construtores
ficaram mais simples (só `world`/`player_entity`/`screen`).

**Teste automatizado**: `tests/test_npc_interaction_request.py` (novo)
— confirma que `_do_negociar()` escreve o componente e `ShopSystem.
update()` consome (abre a loja certa, remove o componente); confirma
que `ShopSystem` NUNCA consome um request de `kind` diferente do seu
(guarda contra a corrida entre Systems); confirma que
`_draw_action_menu` lista uma quest disponível sem lançar
`AttributeError` (regressão do bug do `can_start`).
`tests/test_quest_talk_completable_priority.py` adaptado (a asserção
que checava `QuestDialogSystem._dialog_state` direto após
`TrainerSystem._open_menu()` agora chama `QuestDialogSystem.update()`
explicitamente antes de checar — no jogo real isso acontece sozinho no
próximo tick do game loop, o teste só precisa simular esse passo).
Suíte completa sem regressão.

**Lição pra não repetir**: violação de "System nunca chama System" quase
sempre esconde uma consulta pública (fácil de decompor em função pura de
módulo, chamável por todo mundo) misturada com um comando (fácil de
decompor em componente request/consume) — não precisa de UM mecanismo
só pros dois casos. Refatorar pra decoupling também é uma chance real de
achar bug latente: um método privado chamado por poucos call sites, sem
teste cobrindo, é exatamente onde um nome errado (`can_start` que nunca
existiu) sobrevive sem ninguém notar.

## 140. Profiler client-side (F11/`logs/client_prof.log`) nunca mediu `RenderSystem.render()` — o ponto mais caro do frame ficava fora do relatório

15/09/2026, usuário pediu pra comparar o antes/depois da revisão de
performance (`CLIENT_PERFORMANCE_REVIEW.md`) e mandou o próprio
`logs/client_prof.log` de uma sessão real, gravado com `profile_frames:
true` em `config.json`. O relatório mostrava `rnd:TileRenderSystem`,
`hud:*`, `upd:*` de vários Systems — mas nunca `rnd:RenderSystem`.

**Causa raiz**: `game.py`, no passe de mundo do loop principal, tem um
loop genérico que envolve `system.render()` em `_prof_record()` pra
TODO system da lista — EXCETO 5, deliberadamente pulados desse loop
porque precisam de assinatura/ordem própria: `_loot_system`
(`render_world()`, não `render()`), `_render_system` (kwargs extras
`world_objects`/`nameplate_mode`, e precisa rodar DEPOIS de
`get_world_objects()`), `_projectile_system`/`_pirofagia_system`/
`_spell_cast_system` (ordem relativa a outros draws). Esses 5 são
chamados INDIVIDUALMENTE, mais abaixo no mesmo método — mas nenhuma
dessas 5 chamadas individuais nunca foi envolvida em `_prof_record`,
então nunca apareciam no relatório nem no overlay de F11.
`RenderSystem` é justamente o mais caro de todos os cinco (único com
Y-sort de toda entidade visível) — item 1/2/5/6/8 da revisão de
performance mexeram TODOS nele, e o profiler nunca teria como confirmar
esse ganho numa sessão real.

**Fix**: mesmo padrão `if PROFILE_FRAMES: ts→chama→_prof_record` do loop
genérico, replicado nos 5 pontos de chamada individual — label
`rnd:{type(system).__name__}` idêntico ao já usado pelo loop, então o
relatório/overlay não precisa de nenhuma mudança pra passar a mostrar
essas 5 linhas novas.

**Achado 2, corrigindo isso**: `tests/test_client_ui.py::
test_render_system_desenha_entidade_dentro_da_margem_de_culling`
(escrito no item 8, mesma sessão) usava `pygame.display.get_surface()`
como `screen` — dependia do tamanho de tela AMBIENTE (o último
`pygame.display.set_mode()` chamado por QUALQUER arquivo de teste antes
deste, no mesmo processo pytest), não de um tamanho fixo conhecido.
Rodando `test_client_ui.py` isolado, sempre passava (o próprio arquivo
fixa 320×240 no import); combinado com outros arquivos de teste que
chamam `set_mode()` com outro tamanho, o cálculo de margem de culling
(calibrado pra 320px de largura) ficava errado e o teste falhava —
confirmado como PRÉ-EXISTENTE (falha idêntica sem nenhuma mudança de
`game.py`, via `git stash`). Fix: `pygame.Surface((320, 240))` própria
em vez do display global, nos 2 testes de culling.

**Lição pra não repetir**: teste que depende de um tamanho de tela
EXATO pra calcular um limite/margem nunca deve usar
`pygame.display.get_surface()` (estado GLOBAL do processo, mutável por
qualquer outro arquivo de teste rodando antes no mesmo processo pytest)
— sempre uma `pygame.Surface(tamanho_fixo)` própria. `pygame.display.
get_surface()` é seguro só quando o teste não se importa com o tamanho
exato (a maioria dos testes deste arquivo).

## 141. Travamento periódico de ~105ms em `network_recv` — investigação em andamento, instrumentação adicionada pra confirmar a causa

15/09/2026, achado analisando o log real de profiling do usuário
(`logs/client_prof.log`, sessão pós-item-8/§140) — depois de corrigir o
ponto cego que escondia `rnd:RenderSystem`, apareceram vários `[SPIKE]`
com `network_recv` sozinho custando ~105-108ms (valores muito
consistentes entre si: 106.6, 106.6, 108.1, 104.7 — não parece jitter
aleatório de rede, mais parece algo periódico/estrutural), espalhados
ao longo da sessão inteira, tanto em combate (`is_pursuing=True`) quanto
fora.

**Descartado como causa**: `client/network.py::NetworkClient.poll()` é
não-bloqueante de verdade — só drena uma `queue.Queue` já populada por
uma thread de rede separada (`_recv_loop`), sem tocar socket. O
`decode()` de cada mensagem também acontece NA THREAD de rede, não em
`poll()`. Ou seja, o único custo que `_process_network()` (o que
`network_recv` mede) pode ter é: (a) o volume de mensagens dentro da
LISTA já pronta que `poll()` devolve, e (b) o trabalho de cada handler
despachado por `_handle_net_message` (já otimizado pro dispatch em si,
§138, mas não pro CONTEÚDO de cada handler).

**Hipótese líder, não confirmada ainda**: cliente conectado ao servidor
de PRODUÇÃO real (Hetzner, `5.161.104.174` — confirmado em
`config.json`, só leitura, nada alterado), não localhost — uma rajada
de mensagens (várias chegando entre um `poll()` e o outro, por qualquer
motivo de rede/scheduling) faria `_process_network()` processar N
mensagens NUM FRAME SÓ, e o custo agregado de N handlers pode
perfeitamente somar 100ms+ se N for grande (dezenas/centenas), mesmo
com dispatch O(1). Consistente com os valores serem tão parecidos entre
si SE o tamanho típico da rajada também for parecido (ex: mesma
característica de rede se repetindo).

**Instrumentação adicionada pra confirmar (ainda NÃO resolve o
problema, só mede)**: `client/online_mode_handlers.py::
_process_network()` agora guarda `self._last_net_msg_count` (quantas
mensagens vieram juntas nesse frame) e `self._last_net_msg_types`
(`Counter` dos 3 tipos mais frequentes, `.most_common(3)`) — populados
a cada frame, custo desprezível (`poll()` já devolve uma lista pronta,
isto só mede o que já existe). `game.py`'s `[SPIKE]` breakdown agora
inclui `net_msgs=N(TIPOx contagem,...)` na linha de contexto quando o
frame processou alguma mensagem — próxima sessão de profiling do
usuário vai mostrar se os spikes de `network_recv` vêm de uma rajada
grande (confirma a hipótese) ou de poucas mensagens (aponta pra um
handler específico caro, hipótese descartada por ora mas não
eliminada).

**Sem teste automatizado** — é instrumentação de diagnóstico temporária
(conta o que já existe, não muda comportamento de gameplay), não um
"ponto único de verdade" no sentido da regra do projeto. Se a causa
raiz for confirmada e a correção for de fato escrita, ESSA correção
segue a disciplina normal (teste conforme o caso).

**Status**: RESOLVIDO — a hipótese da rajada foi DESCARTADA pelo próprio
log (ver §142): todo `[SPIKE]` de 100ms+ tinha só 1-5 mensagens, nunca
dezenas/centenas. A causa raiz real (`LOOT_RESULT` → `encode_fog`) e o
fix completo estão em §142.

## 142. Causa raiz do travamento de `network_recv` (§141): `encode_fog()` disparado a cada save de evento — cliente E servidor, servidor bloqueando TODOS os players

15/09/2026, resolução da investigação aberta em §141. Com a
instrumentação de `net_msgs=` (§141) confirmada em log real: todo
`[SPIKE]` de 100ms+ tinha 1-5 mensagens no frame, nunca uma rajada
grande — a hipótese líder de §141 estava errada. Correlação real: TODO
spike de 100ms+ continha `LOOT_RESULT`. Rastreamento da cadeia:
`_handle_msg_loot_result` (client/network_handlers.py) → `_send_save_state()`
→ `_collect_save_state()` (client/save_sync_handlers.py) → `encode_fog()`
(shared/fog_codec.py) — recomprimindo o fog-of-war INTEIRO da conta a
cada loot, sem relação nenhuma com o que o loot mudou de verdade.
Confirmado com benchmark sintético (150k tiles/mapa 2000×2000,
grandeza plausível pra conta bem explorada): **95ms total, sendo 77ms
o laço Python que monta o bitmap tile-a-tile e só 17ms o zlib.compress
em si** — o vilão é o laço, não a compressão.

**Achado mais grave, achado investigando a segurança de omitir fog no
save**: o MESMO `encode_fog()` roda de novo no SERVIDOR
(`server/session.py::_build_save_merge`, dentro de `_persist_character`
→ `_handle_save_state`) — e lá é pior: essa chamada acontece dentro da
coroutine que atende a mensagem, sem `run_in_executor` nenhum. Como o
event loop asyncio do servidor é single-thread (regra já documentada no
CLAUDE.md, nunca confirmada por profiling até agora), esses ~100-150ms
travavam o servidor INTEIRO — todos os players conectados, não só quem
deu loot — a cada save de evento (loot/compra/venda/forja/troca/
stats_update) de qualquer conta bem explorada.

**Duas perguntas do usuário que moldaram o fix final** (não decidido
sozinho — as duas envolviam arquitetura/protocolo, discutidas e
aprovadas antes de codar):
1. *"Refazer essa compressão é desnecessária em game, o correto seria
   refazer só quando o personagem deslogasse"* — correto, e a resposta
   ficou melhor que "logout only" puro: o servidor JÁ tinha (sem
   ninguém ter notado) um contrato onde `fog=None` no dict retornado
   por `_build_save_merge` faz `save_character` (server/auth.py) NUNCA
   tocar a coluna `fog_json` — ou seja, "não fazer nada" é literalmente
   grátis, sem nem precisar reescrever o mesmo valor de volta.
2. *"Não é possível fragmentar o mapa pra compressão ficar mais
   leve?"* — já existe fragmentação por MAPA (`encode_fog` processa
   cada `map_key` separado); fragmentar MAIS, em chunks dentro do MESMO
   mapa, foi testado e **piora** (151ms vs 95ms num benchmark
   comparável) — o custo é por-tile no laço Python, dividir em chunks
   não reduz o trabalho total, só soma overhead de N chamadas de
   `zlib.compress()` em vez de 1.
3. Pergunta de acompanhamento do usuário, batendo direto no ponto certo
   — mesmo só no logout, dezenas de login/logout simultâneos em
   servidor com centenas de players ainda travariam todo mundo por
   ~100-150ms cada vez. Certo — frequência menor não é zero.

**Fix final, 3 partes**:
1. **Cliente** (`client/save_sync_handlers.py::_collect_save_state`/
   `_send_save_state`, novo parâmetro `include_fog: bool = True`) — os
   7 saves de evento sem relação com exploração (loot/compra/venda/
   forja/troca/stats_update, `client/network_handlers.py`; recarregar,
   mesmo arquivo) passam `include_fog=False` — nem monta o bitmap.
   Único save que continua mandando fog: logout/quit
   (`game.py`, ações "quit"/"logout" do menu de pausa — que, achado à
   parte, NUNCA mandavam save nenhum antes desta correção, só o botão X
   da janela mandava; sem esse ajuste, fog nunca chegaria fresco ao
   servidor por nenhum caminho de saída pelo menu).
2. **Servidor, correção estrutural** (`shared/fog_codec.py::
   merge_and_encode_fog`, nova função pura extraída do que era o bloco
   de fog dentro de `_build_save_merge`; `server/session.py::
   _persist_character`) — quando o cliente não mandou fog
   (`client_payload.get("fog")` vazio — o caso comum agora, pelos 7
   saves do item 1), `_persist_character` NÃO CHAMA `merge_and_encode_fog`
   NENHUMA VEZ — passa `merged_fog=None` direto pra `_build_save_merge`,
   que devolve `"fog": None`, e `save_character` pula a coluna. Zero
   decode, zero encode, zero trabalho.
3. **Servidor, escala** (o que resolve a pergunta 3 do usuário) — quando
   o cliente MANDA fog de verdade (só no logout agora), o merge+encode
   roda em `loop.run_in_executor(self._fog_executor, ...)` — um
   `ProcessPoolExecutor(max_workers=2)` criado 1x em
   `SessionManager.__init__` (primeiro uso desse padrão no projeto;
   `Thread` não serviria, é trabalho de CPU puro em Python, preso pelo
   GIL). Mesmo com dezenas de logout simultâneos, cada encode roda num
   processo separado — nunca bloqueia o loop principal, então nunca
   trava nenhum outro player. `shutdown_dispatch_tasks` (chamado no
   Ctrl+C, `server/main.py`) agora também derruba esse executor.

**Por que não foi pro caminho "fog só local, nunca sobe pro servidor"**
(opção que o usuário considerou e descartou depois de discutir):
criptografia client-side não impede compartilhar o arquivo de fog pra
revelar o mapa (a chave vai dentro do próprio executável, extraível por
qualquer um — cripto aqui só impede edição manual casual, não
compartilhamento deliberado) e local-only perde o fog ao trocar de
máquina/reinstalar (hoje é ligado à conta no banco). Decisão: manter
fog persistido no servidor (mesmo padrão do resto do projeto —
servidor autoritativo mesmo pra dado cosmético), resolver o
travamento pela FREQUÊNCIA + pelo EXECUTOR, não pela autoridade dos
dados.

**Testes**: `tests/test_session.py::TestMergeAndEncodeFog` (union +
migração de formato antigo, movido de `TestBuildSaveMergeFog`),
`TestBuildSaveMergeFogPassthrough` (contrato `merged_fog=None` → não
toca a coluna). Suíte completa: 1657 passed, mesmos 2 flakes
pré-existentes de timing (`test_perf_push_pop_*`), sem relação.

**Lição pra não repetir**: um handler de evento "genérico" (SAVE_STATE)
que sempre manda TODO o estado, mesmo quando o evento real só mudou um
pedaço pequeno dele, escala mal — o custo de montar o campo mais caro
(fog, aqui) é pago em TODA chamada, mesmo nas que não têm nada a ver
com ele. Campo caro de montar → parâmetro explícito de inclusão no
ponto de chamada, decidido pelo QUE mudou, não "sempre manda tudo por
simplicidade".

**Follow-up no mesmo dia (usuário jogou de novo depois do fix e mandou
um log novo pra confirmar)**: `LOOT_RESULT` sumiu completamente do log
— causa raiz confirmada resolvida, `network_recv` de volta a valores
normais (~0.06-0.08ms médio, sem spike de 100ms+). Mas apareceu um
spike NOVO: `[SPIKE] 119ms` com `online_sync` custando 106.8ms durante
combate (`is_pursuing=True`). Causa raiz: a varredura original desta
seção (`grep` em `client/network_handlers.py` + `_on_recarregar_changed`)
nunca cobriu `game.py` — 3 chamadas diretas de `_send_save_state()` que
não passam por `network_handlers.py` continuavam com `include_fog`
default (`True`):
1. `game.py::_autosave()` (linha ~921) — callback genérico registrado
   via `save_system.register_autosave()`/`request_autosave()`, chamado
   por `ui/trainer_system.py` (compra de skill), `ui/talent_system.py`
   (3 pontos de alocação), `ui/quest_system.py` (entrega de quest),
   `engine/stats_system.py` (level-up) — nenhum tem relação com
   exploração.
2. `game.py` (linha ~2682) — mesmo `_autosave()`, chamado após morte/
   respawn no MESMO mapa.
3. `game.py` (linha ~2755) — mesmo `_autosave()`, chamado após
   transição de mapa/zona.
4. Autosave LOCAL periódico (linha ~2111, "a cada 2 minutos", 7200
   frames @ 60fps) — chamada direta a `_send_save_state()`, mecanismo
   TOTALMENTE separado de `_autosave()`/`request_autosave()` (contador
   de frame simples, sem relação com evento nenhum).

Corrigido: os 3 pontos de `_autosave()` (item 1-3, unívocos — nenhum
tem relação com exploração, mesma regra dos 6 eventos já corrigidos)
passaram pra `include_fog=False` direto. O autosave periódico de 2min
(item 4) era uma decisão real de segurança-vs-performance (rede de
segurança contra crash vs. zero soluço periódico) — perguntado ao
usuário em vez de decidido sozinho: resposta foi remover fog daqui
também (fog garantido só no logout/quit de verdade — pygame.QUIT,
"Sair do jogo" e "Deslogar" do menu de pausa, os 3 pontos já
adicionados nesta seção).

**Lição pra não repetir, mais específica que a de cima**: antes de
declarar um "SAVE_STATE não deve incluir X" resolvido, buscar TODOS os
call sites de `_send_save_state`/função equivalente NO CODEBASE INTEIRO
(`grep -rn` sem filtro de diretório) — não só nos arquivos onde o
sintoma original foi observado (`network_handlers.py`, no caso). Um
callback genérico registrado (`register_autosave`) que várias UIs
disparam é fácil de não aparecer numa busca que já "achou o suficiente"
pra explicar o sintoma relatado.

**Achado adicional, não corrigido (custo aceito, um soluço já esperado)**:
o `[SPIKE] 123ms` no fim do mesmo log tem só 4.2ms atribuídos no
breakdown (`display_flip`) — os ~119ms restantes ficam FORA de qualquer
seção medida por `_prof_record`. Coincide com o momento de sair do
jogo: as 2 chamadas novas de `_send_save_state()` nas ações "quit"/
"logout" do menu de pausa (adicionadas nesta mesma sessão, ver acima)
rodam fora de qualquer bloco `if PROFILE_FRAMES:`/`_prof_record`, então
o custo de montar o fog completo (o único save que ainda carrega fog de
propósito) nunca aparece no breakdown — só infla o tempo total do
frame sem explicação. Isso é esperado e aceitável (1 soluço só, no
momento exato de fechar o jogo, não durante o gameplay) — não corrigido
por ser cosmético pra um instante que o jogador já está saindo, mas
registrado aqui pra não confundir uma futura análise de log.

## 143. Spike periódico de ~104-110ms sem correlação com rede/loot — hipótese do gc.collect() REFUTADA; DELETE-key era um bug real mas NÃO CONFIRMADO como causa do spike relatado — investigação reaberta com instrumentação nova

15/09/2026, achado analisando um TERCEIRO log de playtest do usuário
(pós-fix completo de §141/§142, incluindo o follow-up dos 4 call sites
perdidos em `game.py`). O bug original (fog em cada loot) e o follow-up
(fog nos outros eventos de `game.py`) continuam confirmados resolvidos
— zero `LOOT_RESULT` no log, `network_recv` normal. Mas apareceram 2
`[SPIKE]` novos, ambos com o MESMO padrão suspeito:
- `[SPIKE] 109ms ... — breakdown do frame: >> display_flip 3.8ms` (só
  3.8ms de 109ms explicados).
- `[SPIKE] 104ms ... — breakdown do frame: >> display_flip 4.0ms` (só
  4.0ms de 104ms explicados).

Nenhum dos dois tem `net_msgs=` na linha de contexto (nenhuma mensagem
de rede processada naquele frame — descarta LOOT_RESULT/qualquer save
de evento) nem correlação com `is_moving`/`is_pursuing` (um parado, um
em combate). O padrão — praticamente NADA do tempo total atribuído a
nenhum label conhecido — é o mesmo assinado já visto no `[SPIKE] 123ms`
de sessão anterior (ver achado acima, esse sim explicado: save de fog
no quit, fora de qualquer bloco medido). Esses 2 spikes NÃO são no fim
do log (o jogo continuou rodando normalmente depois, com vários `[PROF]`
subsequentes) — não é o mesmo mecanismo do quit.

**Hipótese forte**: `game.py::run()`, linha ~1567 — `_gc.disable()`
logo no início do loop principal ("GC manual — evita pauses aleatórias
no loop de jogo") + um `_gc.collect()` MANUAL forçado a cada ~10s
(`_gc_counter >= FPS * 10`, entre frames). Essa chamada roda **antes**
de qualquer `_ts = _time.perf_counter()` do frame ser marcado — nunca
apareceu em NENHUM label do profiler desde que o profiler existe.
`gc.collect()` completo (não só geração 0) num mundo ECS com centenas
de entidades/componentes/superfícies pygame vivas é plausível custar
essa faixa de tempo. Ainda **não confirmado por medição direta** — só
inferido pela ausência de qualquer outra explicação.

**Instrumentado, NÃO corrigido** (mesma disciplina do §141 antes de
propor fix — medir antes de mudar comportamento,
"Observabilidade vem antes de otimização"): `_gc.collect()` agora roda
dentro de `if PROFILE_FRAMES: _ts=...; _gc.collect();
self._prof_record("gc_collect", ...)`, sem nenhuma mudança de cadência/
comportamento do GC em si. Próxima sessão de playtest com essa
instrumentação deve mostrar uma linha `gc_collect` no `[PROF]` — peak
alto (~100ms+) confirma a hipótese; peak baixo aponta pra outro lugar
ainda não identificado (nesse caso, precisaria olhar o que roda
IMEDIATAMENTE antes do `_ts` de `online_sync`/primeiro label do frame,
que também fica fora de qualquer seção medida hoje).

**Se confirmado, fix NÃO decidido ainda** (arquitetural, precisa
conversa com o usuário antes de codar, mesma regra de sempre) — opções
a considerar quando/se confirmado: aumentar o intervalo de 10s pra
reduzir frequência (troca frequência por pausas eventualmente maiores
por acumular mais garbage); ajustar `gc.set_threshold()` pra deixar
gerações mais baratas coletarem automaticamente enquanto só a geração 2
(cara) fica no controle manual; ou aceitar como trade-off já conhecido
("Discutido com o usuário 19/07/2026" no comentário do código, sobre
CPU idle — o GC manual foi decisão consciente na época, não descoberta
agora).

**Status intermediário (agora superado)**: investigação aberta —
instrumentação adicionada, aguardando novo log pra confirmar.

**Follow-up no mesmo dia (usuário jogou de novo com a instrumentação e
mandou um QUARTO log)**: a hipótese do `gc.collect()` foi REFUTADA por
medição direta. O log novo mostra `gc_collect` com peak consistente de
~25-30ms (real, mas bem menor que os ~104-110ms do mystery spike) — e,
mais importante, uma análise da ORDEM do código mostra que
`gc.collect()` estruturalmente NUNCA PODERIA explicar esse spike: ele
roda ANTES de `_t0` ser capturado (`game.py::run()`, linha ~1650, "spike
window cobre TUDO" — mas só a partir DAQUELE ponto), então seu custo é
medido corretamente via `_prof_record` só pra fins de média/pico no
`[PROF]`, mas nunca soma no `_frame_elapsed` que decide se um frame
dispara `[SPIKE]` nem aparece no breakdown de spike de NENHUM frame —
nem do seu próprio, nem de outro. Ou seja: a instrumentação cumpriu o
papel (`Observabilidade vem antes de otimização` — a régua funcionou),
só que devolveu "não é isto" em vez de confirmar.

Script escrito pra somar automaticamente `total do [SPIKE]` contra a
soma do breakdown, em todo o log — achou o mesmo padrão (gap grande,
quase nada explicado) num `[SPIKE] 111ms` (só 4ms explicados). Causa
raiz real: **outro call site de `_send_save_state()` perdido** —
`game.py`, tecla DELETE dentro do inventário (descartar item), ainda
com `include_fog` no default (`True`). Esse é o **4º** call site desse
tipo achado nesta mesma investigação (depois dos 6 de
`network_handlers.py` + o `_on_recarregar_changed` do fix original,
mais os 4 de `_autosave()`/autosave periódico do follow-up de §142) —
e explica por que nunca apareceu na varredura por `grep`: está dentro
do laço de eventos de teclado (`for event in events:`), um trecho de
~400 linhas de `game.py` (entre o `_prof_record("events", ...)` e o
início do loop de `Systems.update()`) que não tem NENHUM `_prof_record`
cobrindo ele — é o mesmo "buraco cego" do profiler já catalogado em
§140 (RenderSystem e os 4 systems fora do loop genérico), só que dessa
vez no bloco de INPUT, não no de render.

Corrigido: `include_fog=False` nessa chamada (descartar item não tem
relação com exploração, mesma regra de sempre). Suíte completa: 1658
passed, 1 failed pré-existente/timing flake sem relação.

**2 spikes menores no mesmo log (~52ms, ~4ms explicados) não
investigados** — bem menores que o padrão de 100ms+ que motivou toda
essa investigação, sem contexto (`net_msgs` ausente num, presente
noutro) que aponte pra uma causa de código específica; hipótese mais
provável é jitter normal de driver de vídeo/compositor da janela
(vsync/DWM), não um bug — registrado aqui só pra não confundir uma
futura análise, não investigado a fundo por ser pequeno e sem padrão
claro.

**Lição pra não repetir, reforçando a de §142**: a regra "buscar TODOS
os call sites no codebase inteiro" já tinha sido escrita no CLAUDE.md
depois do follow-up de §142 — e MESMO ASSIM esta 4ª ocorrência não foi
achada por leitura/grep, só foi achada porque o USUÁRIO testou de novo
e um script comparando total-do-spike-vs-soma-do-breakdown apontou o
frame exato. Escrever a regra 1 vez não garante aplicá-la
retroativamente a TODO call site já existente — quando o padrão
"grep-based call site audit" falha silenciosamente (por estar dentro de
um bloco sem profiling, nesse caso), a ferramenta certa é comparar
dado real (spike total vs breakdown) contra o código, não confiar só
na leitura estática. Considerar, se aparecer um 5º caso: um teste/
assert genérico que falha se `_send_save_state()` sem `include_fog`
explícito for chamado de um novo call site — mas isso não foi proposto
nem decidido com o usuário ainda, só uma ideia registrada aqui.

**Correção no mesmo dia (erro meu, achado pelo usuário)**: eu declarei
"RESOLVIDO" acima achando que o `[SPIKE] 111ms` do log era causado pela
tecla DELETE — mas nunca confirmei isso, só notei que o call site
tinha o formato certo (sem `net_msgs`, quase nada explicado no
breakdown, dentro de um bloco sem profiling) e assumi. O usuário
perguntou direto: **não apertou DELETE naquele teste**. Isso quebra a
regra já escrita no próprio CLAUDE.md ("'Corrigido' exige reproduzir o
cenário que foi relatado, não só um cenário sintético plausível") — eu
mesmo violei essa regra ao declarar resolvido sem ter reproduzido o
cenário exato, só por analogia de formato.

O fix da tecla DELETE continua correto e vale a pena manter (é um bug
real da mesma família, só que sua causalidade PRA ESTE spike específico
nunca foi confirmada) — mas a causa raiz do `[SPIKE] 111ms` relatado
continua **desconhecida**. Em vez de caçar call site por call site de
novo (achando "mais um" plausível), instrumentei o bloco INTEIRO onde
qualquer call site desse tipo poderia estar escondido: todo o
processamento de eventos de teclado/mouse a nível de jogo (os 2
`for event in events:`, chat/menu/hotbar/clique — tudo entre o
`_prof_record("events", ...)` que só mede o `pygame.event.get()` e o
início do loop de `Systems.update()`) agora vira 1 label só,
`input_dispatch`, via `_prof_record`. Se o próximo spike relatado
mostrar `input_dispatch` alto no breakdown, confirma que a causa mora
nesse bloco (call site ainda não identificado, mas pelo menos
localizado); se `input_dispatch` ficar baixo, o mistério está em OUTRO
lugar — provavelmente algo fora do loop de eventos totalmente, o que
mudaria a direção da investigação.

**Follow-up 2 no mesmo dia (usuário testou de novo, sentiu engasgos,
mandou log com `input_dispatch` instrumentado)**: `input_dispatch`
ficou baixo o tempo inteiro (peak máximo ~1.5ms em 14 relatórios `[PROF]`
diferentes) — descartado como causa. Mas o `[SPIKE] 111ms` (mesma
magnitude de sempre, de novo só ~4.5ms explicados no breakdown, dessa
vez COM `net_msgs=1(AOI_UPDATEx1)` mas sem `network_recv` no
breakdown — ou seja, nem o processamento da mensagem é o gargalo)
continua acontecendo, confirmando que o mistério está em outro lugar.

Mapeei TODOS os pares `_ts=.../self._prof_record(...)` do frame em
ordem cronológica (script + leitura) procurando o próximo trecho sem
cobertura nenhuma — achado: entre `hud:map_title` e `display_flip`
existe um bloco de ~100 linhas ("por cima de tudo" — `self._loot_system.
render()`, `self._shop_system.render()`, `self._quest_dialog.render()`,
`self._quest_journal.render()`, `self._crafting_system.render()`,
`self._trainer_system.render()`, render do mapa overlay, o menu de
pausa (`_draw_pause_menu`), God Mode update+render, overlay de
performance) **completamente sem profiling** — mesma classe de bug do
§140 original (métodos de render chamados INDIVIDUALMENTE, fora de
qualquer loop genérico já medido), só que num conjunto de métodos
DIFERENTE (as renderizações de MODAL/UI desses sistemas, não as de
mundo — `_loot_system.render_world()`, por exemplo, já está medido
desde §140; `_loot_system.render()`, o modal de loot em si, nunca
esteve).

Instrumentado (mesmo padrão de `input_dispatch` — medir o bloco
INTEIRO de uma vez, sem sub-dividir call site por call site ainda):
novo label `ui_modals_overlay` cobre esse trecho inteiro. Suíte
completa: 1658 passed, 1 failed pré-existente/timing flake sem relação
(`test_perf_push_pop_grava_no_caminho`).

**Follow-up 3 no mesmo dia (usuário jogou "por um momento", mandou o
log — confirmação positiva)**: `ui_modals_overlay` apareceu como o
grosso de DOIS spikes reais:
- `[SPIKE] 90ms ... >> ui_modals_overlay 79.3ms >> display_flip 4.6ms`
- `[SPIKE] 121ms ... >> ui_modals_overlay 109.0ms >> display_flip 3.9ms`

Confirmado: o bloco "por cima de tudo" É a causa real (não mais
hipótese) — primeira vez nesta investigação inteira (§141→§143) que um
[SPIKE] de 90ms+ aparece com o culpado NOMEADO no próprio breakdown,
em vez de tempo invisível. Os 2 casos têm `is_moving=False,
is_pursuing=True` no primeiro/`is_pursuing=False` no segundo — jogador
parado nos dois, consistente com estar com algum modal ABERTO parado
olhando (mapa, inventário, etc.), não com movimento/combate.

`ui_modals_overlay` ainda era um bloco genérico só (loot/shop/quest/
crafting/trainer modal + mapa overlay + menu de pausa + God Mode + perf
overlay, todos somados). Dividido em 10 labels granulares — mesma
disciplina de sempre, achar o culpado exato em vez de mais um "bloco
inteiro": `ui:loot_modal`, `ui:shop_modal`, `ui:quest_dialog_journal`,
`ui:crafting_modal`, `ui:trainer_modal`, `ui:tooltip_flush_debug_modal`
(fechando um gap residual achado durante esta divisão — tooltip flush +
modal de debug F12 também ficavam sem cobertura entre trainer e mapa),
`ui:map_overlay`, `ui:pause_menu`, `ui:god_mode`, `ui:perf_overlay`.
Hipótese mais provável pra confirmar no próximo log, dado o padrão
"parado" dos 2 casos: `ui:map_overlay` (`_map_overlay.render()`,
gated por `is_open`, envolve `collect_markers()`/`collect_quest_circles()`
+ desenhar o fog explorado — trabalho genuinamente pesado se não
cacheado) — mas não assumir sem o dado confirmando, mesmo erro já
cometido com a tecla DELETE nesta mesma investigação.

Suíte completa: 1658 passed, 1 failed pré-existente/timing flake sem
relação.

**Follow-up 4 no mesmo dia (usuário testou de novo, "senti alguns
engasgos" — sem certeza se pequeno-repetitivo ou grande-isolado, pediu
pra investigar os dois)**: o log NÃO reproduziu o spike grande de
90-121ms desta vez (nenhum `[SPIKE]` além do de inicialização, já
esperado) — `ui:map_overlay` ficou consistentemente ~0.001ms (peak
máximo 0.025ms) em 20+ relatórios `[PROF]`, **hipótese do mapa
REFUTADA**. Sem reprodução, não dá pra avançar mais na causa do spike
grande por ora — instrumentação (10 labels `ui:*`) continua no lugar
pra próxima ocorrência.

Separado disso, achado um segundo padrão real e CONSISTENTE (presente
em todo `[PROF]` do log, ~24 ocorrências): `gc_collect` com peak de
~22-28ms a cada disparo (a cada ~10s) — pequeno comparado ao spike
grande, mas confiável/recorrente, plausível candidato pro "engasgo
pequeno e repetitivo" que o usuário descreveu. Decisão do usuário:
tratar já que está confirmado por medição (diferente do spike grande,
que ainda não tem causa certa) — opção escolhida: gen0/gen1 (baratas)
voltam a coletar automaticamente no modo padrão do Python; só a
geração 2 (a cara, a que estava sendo medida) continua manual, no
mesmo timer de ~10s.

Implementado: `_gc.disable()` (que desligava TODA coleta automática)
virou `_gc.set_threshold(700, 10, 2_000_000_000)` — thresholds 0/1 no
padrão do CPython (gen0/gen1 disparam sozinhas, sem controle nenhum
daqui), threshold2 gigantesco o suficiente pra gen2 nunca disparar
sozinha no meio de um frame qualquer (o motivo original do GC manual,
19/07/2026) — só o `_gc.collect(2)` explícito no timer periódico
continua forçando ela. Efeito esperado: menos garbage sobra acumulado
pra geração 2 processar (gen0/1 já limpam sozinhas o tempo todo),
então a pausa de ~22-28ms a cada 10s deve ficar menor — não eliminada
(a geração 2 ainda roda no mesmo timer), mas mais barata por já não
carregar objetos que gen0/1 já teriam limpado.

Suíte completa: 1658 passed, 1 failed pré-existente/timing flake sem
relação.

**Follow-up 5 no mesmo dia (usuário fez um teste completo — menus,
inventário, mapa, alt-tab — e relatou 2 travadas específicas: uma no
alt-tab, outra apertando F11; "no restante do tempo, mesmo abrindo
menus e modais não senti travar")**:

1. **F11 — CONFIRMADO e CORRIGIDO.** `[SPIKE] 197ms | ... >>
   ui:perf_overlay 185.1ms`. Causa raiz: `self._perf_font` nascia
   `None` no `__init__` com comentário explícito "lazy-loaded em
   _draw_perf_overlay" — `pygame.font.SysFont(...)` só era chamado na
   PRIMEIRA vez que o overlay desenhava de verdade (1ª vez que F11 é
   apertado na sessão). `SysFont()` varre as fontes do sistema
   (Windows) e é lenta na primeira chamada — o lazy-load "poupava" o
   custo só se o usuário nunca usasse F11, mas na prática trocava um
   custo invisível de carregamento por um engasgo visível NO MEIO do
   jogo, no exato frame do primeiro F11. Fix: fonte criada direto no
   `__init__` (mesmo momento de várias outras fontes/recursos já
   carregados ali), lazy-load removido de `_draw_perf_overlay`.

2. **Alt-tab — explicado, NÃO é bug de código, decisão pendente sobre
   mitigação.** O log mostra um cluster de ~15 frames consecutivos com
   spikes gigantes (`events`/`display_flip` até 504ms/286ms, um frame
   de 512ms total) e `net_msgs` bem acima do normal (16, 10, 8, 5 — o
   normal é 0-1) — sinal claro de que o processo ficou tempo real
   suspenso pelo Windows enquanto a janela estava sem foco (comum:
   SO/compositor throttling um processo em segundo plano), e ao voltar
   o foco a janela tem uma rede/fila de evento acumulada pra
   processar de uma vez. Nenhum código do jogo "trava" aqui — o próprio
   sistema operacional pausa o processo; o que aparece no profiler é o
   CATCH-UP acontecendo depois que o SO devolve tempo de CPU.
   Mitigação possível (NÃO implementada, decisão em aberto com o
   usuário): detectar perda/retorno de foco (`pygame.ACTIVEEVENT` ou
   similar, hoje nem sequer permitido pelo `pygame.event.set_allowed`)
   e, ao retomar, descartar a fila de eventos acumulada / resetar o
   `clock` pra não deixar um `dt` gigante contaminar física de 1 frame
   só. Fora de escopo até o usuário decidir se quer esse tratamento.

3. **Achado à parte, não perguntado, mas grave**: `[SPIKE] 1283ms |
   ... net_msgs=1(ZONE_CHANGEx1) >> network_recv 1263.1ms` — uma
   transição de mapa (ZONE_CHANGE) travou o cliente por **1.28
   segundos** inteiros. Isso é MUITO maior que qualquer coisa desta
   investigação inteira (§141-§143 juntos nunca passaram de ~150ms).
   `network_recv` engloba o handler completo de `ZONE_CHANGE`
   (`_do_transition` — descarrega entidades do mapa antigo, carrega
   CSV do mapa novo, reconstrói `Tilemap`, spawna entidades, zonas de
   ambiente/PvP, etc., ver `game.py::_do_transition`) — plausível que
   isso seja genuinamente pesado (carregar um mapa novo do disco +
   reconstruir toda a estrutura de tiles), mas 1.28s é uma trava real
   e perceptível, MUITO fora da faixa que motivou toda essa
   investigação. NÃO investigado a fundo ainda — reportado ao usuário,
   fora do escopo original (fog/engasgo geral), decisão de investigar
   ou não fica com ele.

Suíte completa: 1659 passed, 0 failed.

**Status**: 3 investigações em paralelo, estados diferentes —
1. **Spike grande de UI (90-121ms, `ui_modals_overlay`)**: aberto, não
   reproduzido nesta rodada, `ui:map_overlay` descartado. 10 labels
   granulares continuam ativos aguardando a próxima ocorrência.
2. **Pausa pequena e recorrente (`gc_collect`, ~22-28ms/10s)**: ação
   tomada (gen0/1 automáticas, só gen2 manual) — aguardando
   confirmação do usuário em jogo se a sensação de engasgo melhorou.
3. **F11 (`ui:perf_overlay`, ~185ms na 1ª vez)**: RESOLVIDO, corrigido
   e testado.
4. **Alt-tab**: explicado (SO suspendendo o processo, não é bug),
   mitigação em aberto — decisão do usuário se quer tratamento.
5. **ZONE_CHANGE de 1.28s (`network_recv`)**: achado novo, não
   investigado, fora do escopo original — decisão do usuário se
   investiga agora ou depois. Usuário confirmou que não vale investigar
   (é esperado que trocar de mapa carregue o mapa) DESDE que não afete
   o SERVIDOR — confirmado que `_handle_msg_zone_change` →
   `_do_transition` é 100% client-side (nenhum `self._net.send()` no
   caminho, nunca espera resposta do servidor) — fechado, sem ação.
   Mesma verificação pro alt-tab (item 4): confirmado que o `_ping_loop`
   (thread de rede separada, `client/network.py`) continua rodando
   independente do stall da thread principal — servidor nunca percebe
   nada, conexão nunca cai por isso.

## 144. Item 9 (object pooling de PlayerProjectile) — escopo real 3x maior que estimado, 2 achados de código morto, `Projectile` (mob/torre) preservado no ECS por ser server-autoritativo

15/09/2026, implementação do item 9 do `CLIENT_PERFORMANCE_REVIEW.md`
(usuário já tinha aprovado implementar sem confirmação de profiling
prévia, decisão de sessão anterior). Pesquisa ANTES de codar (regra do
projeto) revelou dois fatos que mudaram o escopo real da tarefa:

**1) Existem DOIS componentes de projétil, o review misturou os dois.**
`Projectile` (`engine/components.py`, flecha/bola de fogo de MOB/TORRE/
MINION) é usado por `ProjectileSystem` em `engine/world_systems.py` —
código HEADLESS COMPARTILHADO, que o SERVIDOR roda de verdade
(`ProjectileSystem.update()` chama `deal_damage()`/`apply_effect()`
reais, dano com viagem/tempo, não instantâneo). Mover esse pra fora do
ECS quebraria o jogo pro servidor — ele NUNCA foi tocado.
`PlayerProjectile` (flecha/bola de fogo do JOGADOR) só existe em
`ui/spell_system.py::PlayerProjectileSystem`, `ui/` é client-only —
confirmado por grep no repo inteiro que NENHUM código de servidor
importa/usa esse componente. Só esse foi migrado.

**2) O escopo real era ~3x maior que a estimativa do review.** O
review citou ~6 pontos de spawn; um agente de pesquisa (Explore)
mapeou exaustivamente e achou **16 pontos de import** em 4 arquivos
(`ui/spell_system.py`, `client/remote_entity_handlers.py`,
`client/network_handlers.py`, `client/online_mode_handlers.py`) — e no
processo achou 2 blocos de código MORTO:

- **4 métodos inteiros em `ui/spell_system.py`** (`_apply_tiro_multiplo`,
  `_apply_tiro_repulsivo`, `_apply_arrow_skill`, `_apply_flecha_reiterada`
  — as versões "antigas" desses handlers) nunca são chamados em
  produção: `tiro_multiplo` desvia pra `_capture_tiro_multiplo_dir`
  ANTES de chegar no dispatch de `_CAST_HANDLERS`; os outros 3 estão em
  `_PROJ_SPELLS_LOCAL` (`_complete_cast`), também excluídos do
  dispatch. As versões REAIS (que de fato disparam) foram
  reimplementadas há tempo em `client/network_handlers.py`, recebendo o
  `is_completion` do servidor — ninguém apagou as antigas. Removidos
  nesta sessão (regra já registrada no projeto: sempre remover
  duplicata morta da era offline quando encontrada — decisão do usuário
  de remover junto, confirmada por pergunta explícita antes de agir).
- **`client/online_mode_handlers.py::_process_bdf_pending`** — método
  inteiro nunca chamado por ninguém (`grep` por
  `_process_bdf_pending()` no repo inteiro: zero ocorrências) e
  `self._bdf_pending` (a lista que ele consome) nunca é populada em
  lugar nenhum (`game.py:219` só inicializa `[]`, nenhum `.append()`
  existe). Achado tarde, DEPOIS de já ter corrigido o import quebrado
  dele (a importação de `PlayerProjectile`, que eu tinha removido,
  quebraria esse método SE ele fosse chamado) — decisão: corrigir a
  query pra usar a API nova em vez de deletar (é código morto mas
  poderia ser religado no futuro; deletar seria uma decisão maior, fora
  do que foi pedido — reportado ao usuário, não decidido sozinho).

**Branch de dano local morto em `_on_hit` (achado, NÃO removido de
propósito)**: `_on_hit` tem um branch que chama `deal_damage()`/
`_apply_magic_damage()` diretamente quando `attacker_cs` E `target_cs`
(CombatStats REAIS, não espelho) existem AMBOS localmente — nunca
acontece no jogo online de hoje (mob/player remoto só tem
`RemoteEntityMeta.hp`/`RemoteControlled.hp`, nunca `CombatStats`
completo; `TrainingDummy` só existe no servidor,
`create_training_dummy` nunca é chamado do lado cliente). Confiança
alta de que é vestígio da era offline, mesma classe já documentada em
`ChannelingSystem` (comentário no próprio arquivo, achado numa sessão
anterior). Decisão: preservar a lógica exatamente como estava (só
migrando de onde ela lê os dados — não mais ECS), sem tentar "limpar"
— risco desproporcional pra algo fora do que foi pedido, e sem nenhum
teste cobrindo esse branch específico pra confirmar que uma limpeza não
quebraria nada.

**Desenho da migração** (mesmo padrão já usado por
`ui/floating_text.py::FloatingTextManager`/`FloatingTextEntry`, citado
pelo próprio review como referência):
- `PlayerProjectile` (dataclass ECS) → `PlayerProjectileEntry`
  (`ui/spell_system.py`, `__slots__`, fora do ECS). Campos de posição
  (`x/y/prev_x/prev_y`, era o componente `Position` irmão) e o rastro/
  timer de animação (`trail`/`fireball_anim_t`, eram os dicts externos
  `_arrow_trails`/`_fireball_anim` keyed por eid) viraram atributos
  diretos da entrada — não precisam mais de estrutura externa.
- `PlayerProjectileSystem.spawn(x, y, attacker_id, target_id, **kwargs)`
  — ÚNICO ponto de entrada novo pra criar um projétil visual do
  jogador, substitui `world.create_entity()`+`add_component(Position(
  ...))`+`add_component(PlayerProjectile(...))` nos ~9 call sites reais
  (os 4 do item acima já eram mortos, não precisaram migrar). Retorna a
  entrada criada.
- Correlação "achar a flecha de volta pra anexar o resultado do
  servidor" tinha 2 padrões antes (por eid retornado OU por query ECS
  filtrada por atacante+alvo+sentinela) — o padrão por eid SUMIU quase
  todo (agora é só guardar a referência que `spawn()` já devolve
  direto, sem busca nenhuma); o padrão por query sobrevive só onde o
  resultado pode chegar em FRAME DIFERENTE do spawn (Bola de Fogo via
  `_process_bdf_pending`, que compara com o `deadline` de 200ms) — novo
  método `PlayerProjectileSystem.find_pending_fireball(attacker_id,
  target_id)` substitui a query ECS ali.
- `SpellCastSystem` ganhou um parâmetro novo no construtor
  (`player_proj_system=None`, injetado por `game.py` — precisa dele
  pra `_launch_fireball` chamar `.spawn()`); `game.py` reordenou a
  construção dos 2 systems (PlayerProjectileSystem antes de
  SpellCastSystem) pra poder passar a referência direto, em vez de
  injeção tardia como `_on_inventory_changed`.

**Testes**: fixtures de teste (`tests/test_client_ui.py`) ganharam
`_player_proj_system` real (mesma classe de produção, não mock) —
`_NetHandlerFixture` e o `_Fixture` de
`_make_archer_projectile_fixture`. Helpers `_find_cosmetic_projectile`/
`_spawned_arrows` migrados pra ler `._entries` em vez de fazer query
ECS. Nenhum teste precisou de lógica nova — só troca de mecanismo de
acesso. Suíte completa: 1657 passed, 2 failed pré-existentes/timing
flake sem relação.

## 145. God Mode (F10) — cluster de spikes grandes (até 158ms) enquanto aberto, causa NÃO investigada (ferramenta de GM/debug, baixa prioridade)

15/09/2026, achado analisando o log pedido pelo usuário como veredito
final pós-§141-§144 (fog/gc/F11/item 9 todos confirmados sem
regressão, `LOOT_RESULT` continua ausente do log). Enquanto o God Mode
estava aberto, apareceu um cluster de ~15 `[SPIKE]` consecutivos
(22-158ms) com `ui:god_mode` custando 5-17ms cada (esperado — é uma
UI de edição com grade desenhada todo frame, `[PROF]` confirma que
fica em ~0.002ms quando fechado, então o custo é real só enquanto
ativo) — mas em 2 desses (158ms e 146ms) a maior parte do tempo total
NÃO está no breakdown (`ui:god_mode` 17ms/8ms de 158ms/146ms). Mesmo
padrão de "buraco" já caçado 3 vezes nesta investigação (§143), só que
numa área ainda sem instrumentação granular — `god_mode.update()`/
`.render()` já estão medidos como um bloco só (`ui:god_mode`, mesmo
padrão do que foi feito pros modais em §143), mas não sub-divididos
método a método como os modais foram.

Logo depois (mesma janela, sem `ui:god_mode` no breakdown dessa vez)
apareceram mais 2 spikes grandes (139ms, 127ms) com só
`rnd:TileRenderSystem` parcialmente explicado (~10-15ms de cada) —
pode ser cache de tile sendo invalidado/reconstruído ao fechar o God
Mode (bug já documentado em §53 pra esse mesmo cache, mas nunca com
essa magnitude), não confirmado.

**Decisão do usuário**: não investigar agora — God Mode é ferramenta
de GM/debug (`is_gm` + `"debug_mode": true` em `config.json`), jogador
comum nunca abre essa tela, então não afeta a experiência de jogo
normal. Registrado aqui pra não se perder, caso vire relevante depois
(ex: se o próprio God Mode passar a ser usado com mais frequência, ou
se o mesmo padrão aparecer em outro contexto).

**Status**: investigação aberta, baixa prioridade — sem instrumentação
adicional, sem fix.

## 146. Engasgo específico de BG — FogSystem recalculava shadowcasting de TODOS os aliados (player/torre/minion) a cada movimento de QUALQUER um deles

15/09/2026, usuário relatou travadas especificamente na Battleground e
pediu análise do log. Log real (62684 linhas, sessão longa) mostrou um
padrão sistemático e claro nos `[PROF]`: `upd:FogSystem` subindo de
avg≈0.05ms (peak 1.6ms) no início da sessão pra avg≈2-5ms (peak
10-24ms) conforme a sessão avançava — crescimento constante, não um
pico isolado. `PlayerProjectileSystem` (item 9, implementado na sessão
anterior) foi checado e descartado como causa — médias seguem
estáveis (~0.002-0.08ms) ao longo do log inteiro, sem crescer.

**Causa raiz** (`ui/systems.py::FogSystem.update()`): o recompute de
visão (shadowcasting, `compute_fov()`) dispara quando o PLAYER muda de
tile OU quando `fog.ally_centers` muda (`_ally_key != fog.
_last_ally_centers`) — `ally_centers` inclui não só teammates, mas
TAMBÉM torres e minions do próprio time (visão compartilhada de time,
30/07/2026). Dentro desse bloco, o código refazia
`compute_fov(ax, ay, ar, ...)` pra CADA entrada de `ally_centers`,
INCONDICIONALMENTE — mesmo pra um aliado que não tinha se movido desde
o tick anterior. Em mundo aberto (`ally_centers` vazio) isso nunca
aparece; numa BG com vários minions em campo + torres + teammates,
QUALQUER um desses se movendo (o que acontece quase todo tick durante
combate ativo) recalculava o shadowcasting de TODOS os outros também —
O(n_aliados) por recompute, e o recompute em si dispara com frequência
alta. Isso explica tanto o "só acontece na BG" (mundo aberto tem zero
aliados) quanto o "cresce com o tempo" (mais minions em campo conforme
a partida avança).

**Fix**: cache por aliado em `FogOfWar._ally_fov_cache` (chave
`(ax,ay,ar)`, valor o set já calculado) — um aliado que não mudou de
posição/raio desde o último tick reaproveita o FOV já calculado em vez
de rodar `compute_fov()` de novo. Só o aliado que de fato mudou de
posição (chave nova) dispara um recálculo real. Cache limitado a 300
entradas (`_ALLY_FOV_CACHE_MAX`, mesmo padrão de `_outline_cache`,
`ui/floating_text.py`) pra não crescer sem limite numa partida longa
com muitas posições diferentes; limpo também em `switch_map()` (mapa
novo invalida as coordenadas cacheadas). Aviso explícito no comentário
do código: a suposição por trás do cache (mesma posição+raio sempre
produz o mesmo FOV) só vale porque o tile_matrix é estático durante uma
partida — se algum dia o jogo ganhar mutação de tile em tempo real
(porta abrindo, etc.), esse cache precisa ser invalidado nesse
momento também.

**Testes**: 2 casos novos em `tests/test_fog_ally_vision.py`
(`TestFogSystemAllyFovCache`) — confirma que um aliado que NÃO se moveu
não gera uma 2ª chamada de `compute_fov` quando o recompute dispara por
outro motivo (o player se movendo), e que um aliado que SE moveu pra
posição nova recalcula e atualiza o cache corretamente (revert-to-
confirm: os 2 testes falham sem o fix — 4 chamadas em vez de 3, cache
vazio). Os 4 testes já existentes de visão compartilhada de time
(`test_fog_ally_vision.py`, `test_team_visibility_sweep.py`,
`test_tile_los_visibility.py`, 35 casos) continuam passando sem
alteração — comportamento observável idêntico, só o CUSTO mudou.
Suíte completa: 1659 passed, 2 failed pré-existentes/timing flake sem
relação.

**Achados secundários no mesmo log, NÃO corrigidos** (fora do escopo
imediato, mencionados pra não se perder):
- O label `hud:combat_log` (`game.py`) está com nome enganoso — na
  verdade mede o bloco de minimapa + quest HUD + buff bar, não o log
  de combate/chat de verdade. O log de combate real
  (`self._draw_chat_log()`) fica num trecho de ~25 linhas entre
  `transform_scale` e `hud:draw_hud` que NUNCA tem nenhum
  `_prof_record` — mesma classe de "buraco cego" já achada 3 vezes
  nesta investigação (§143), aqui nunca antes notada porque o nome do
  label vizinho mascarava a ausência.
- `[SPIKE] 1106ms` com `events` custando 1094.7ms, achado em algum
  ponto do log — mesmo padrão de alt-tab/backlog de evento já
  documentado em §143 (achado 2), não reinvestigado aqui.

## 147. Verificação pós-§146: log novo confirma o fix e fecha 2 achados secundários pendentes — 1 novo ponto cego achado, não corrigido (baixa prioridade)

16/09/2026, usuário testou o fix do §146 na BG e relatou "bem menos
[travamentos] que antes, mas em alguns momentos ainda travou". Log
novo (13978 linhas) comparado ao anterior (62684 linhas, pré-fix):

- **Fix confirmado por dado**: `upd:FogSystem` não mostra mais o
  padrão de crescimento constante — oscila avg≈0.03-0.8ms a sessão
  inteira, sem tendência de subida. Spikes >40ms caíram de
  232-em-10272 (pré-fix) pra 10-em-1214 (pós-fix) — redução
  proporcional grande, compatível com o relato do usuário.
- **`network_recv=73.5ms` (linha 1872, spike de 193ms, `net_msgs=7`
  incluindo `ZONE_CHANGE`)**: investigado a pedido do usuário. NÃO é
  recorrência do bug §141/§142 (fog pesado no autosave) —
  `_autosave()` já confirmado no código usando `include_fog=False`
  (`game.py:949`), e uma varredura no log inteiro por qualquer
  `network_recv` alto achou só 3 ocorrências em 13978 linhas (isolado,
  não periódico). É o custo de `_do_transition()` (troca de mapa)
  rodando de forma síncrona dentro do mesmo trecho medido como
  `network_recv` (`_handle_msg_zone_change` → `_do_transition`,
  `client/network_handlers.py`/`game.py`) — mesma categoria já aceita
  pelo usuário em §143 follow-up ("super compreensível travar numa
  troca de mapa"), só que numa escala bem menor aqui (193ms vs os
  1.28s do caso original).
- **Achado novo, não corrigido**: desse frame de 193ms, só 88.7ms
  aparecem em algum label do profiler — sobram ~104ms sem NENHUMA
  medição, dentro do próprio `_do_transition` (`load_map_csv`,
  `create_tilemap`, spawn de entidades não têm `_prof_record`
  individual, só o `network_recv` que os envolve por fora). Mesma
  classe de "ponto cego" do achado do `hud:combat_log`/`_draw_chat_log`
  acima, só que maior e em local diferente. Usuário decidiu não
  instrumentar agora — só 1 ocorrência no log inteiro, categoria já
  aceita, 193ms já bem menor que o caso que motivou a aceitação
  original. Registrado pra não se perder, caso o padrão volte a
  incomodar ou alguém queira otimizar `_do_transition` no futuro.
- Os 2 achados secundários de §146 (label `hud:combat_log`
  enganoso/`_draw_chat_log` sem profiling, padrão de spike tipo
  alt-tab) permanecem como estavam — não foram tocados nesta sessão,
  usuário optou por não investigar agora.

Nenhuma mudança de código nesta entrada — só investigação de log e
leitura de código pra confirmar/descartar hipótese, a pedido do
usuário.

## 148. (movido) Nova Congelante mudo pro próprio conjurador com "Precisão Elemental" maxada — cast instantâneo nunca criava o SpellCast local que disparava o som (§137) — ver HISTORICO_BUGS.md §43

## 149. (movido) ESC não fechava o modal de estatísticas (tecla C) — `_show_char_stats` nunca tinha entrada em `_modal_registry()` — ver HISTORICO_BUGS.md §44

## 150. Auditoria de projeto inteiro, 3 agentes independentes — 4 exploits de economia de severidade Alta achados no servidor

16/09/2026, usuário pediu pra escalar de novo os 3 agentes
independentes (mesmo modelo de `CLIENT_PERFORMANCE_REVIEW.md`,
15/09/2026), desta vez cobrindo o PROJETO INTEIRO (incluindo `server/`
e `shared/`, de fora da rodada anterior por decisão explícita). Escopo
e achados completos em `arquitetura/PROJECT_WIDE_REVIEW.md` (não
resumido aqui pra evitar duplicação — este item é só o ponteiro).

**Resumo do que mais importa**: o especialista em práticas de mercado
achou 4 exploits de duplicação de recurso de severidade Alta no
servidor (ouro infinito via `SELL_REQUEST`, cura/mana infinitos via
`CONSUMABLE_USE`, duplicação/forja de item via `INV_SYNC`, perda de
ouro sem entrega em `BUY_REQUEST`) — todos com a mesma causa raiz:
handler aplica o que o CLIENTE reportou no payload direto, em vez de
resolver o efeito a partir do catálogo/estado autoritativo do
servidor. Servidor de produção real (Hetzner) está rodando, então isso
é risco ativo, não só teórico.

Achado metodológico relevante: os relatórios de arquitetura e de
qualidade de código DISCORDARAM sobre 1 item (painéis NPC fechados por
proximidade duplicados em `game.py`) — verificado manualmente antes de
aceitar qualquer um dos dois (mesma disciplina que o CLAUDE.md já pede
pra análise de terceiro, aplicada aqui entre 2 agentes da mesma
auditoria): o relatório de arquitetura estava certo, o de qualidade
olhou só 1 dos 2 métodos duplicados e errou o veredito.

**Nenhuma mudança de código foi feita ainda** — é só o levantamento,
igual a rodada anterior. Decisão de por onde começar pendente do
usuário.

## 151. Ouro infinito via `SELL_REQUEST` (item 1 da auditoria §150) — corrigido após revisão cruzada entre os 3 agentes, que achou um 2º exploit mais grave no mesmo método

16/09/2026. Usuário pediu explicitamente, antes de corrigir qualquer
achado da auditoria §150, uma rodada de **revisão cruzada**: o achado
de 1 agente (mercado/segurança) é reapresentado aos OUTROS 2 (que não
o viram) pra opinar pela própria lente antes de qualquer código ser
escrito. Mesmo princípio já usado em rodadas anteriores deste projeto
(§114, §116-121 — "2ª rodada valida/aprofunda a 1ª"), aplicado agora
entre especialidades diferentes em vez de entre rodadas da mesma
especialidade.

**Achado original** (agente de mercado): `process_shop_sell`
(`server/world_server.py`) creditava ouro incondicionalmente
(`wallet.gold += sell_price`) ANTES de tentar remover o item do
Inventory ao vivo — a remoção era best-effort (não achou, não remove,
mas também não desfaz o crédito nem recusa a venda). Um cliente podia
vender um item que nunca possuiu (qualquer item caro do catálogo) com
`stack_sold` arbitrário (sem teto) e receber ouro ilimitado, repetível.

**Revisão cruzada — arquitetura**: confirmou que o fix proposto (checar
posse ANTES de aplicar, mesmo padrão que `craft_item`/`recycle_item` já
seguem no mesmo arquivo) é o caminho certo, sem precisar de um "ponto
único de verdade" novo — só a ORDEM das operações estava invertida.
Mas achou uma ressalva importante: o comportamento antigo ("vende
mesmo sem achar o item") era uma decisão de design DOCUMENTADA de
propósito no próprio docstring (tolerar desync de save antigo/lag) —
o fix reverte essa decisão, então não é "só corrigir um bug", é trocar
um invariante consciente. Pela própria regra do projeto ("nunca decido
sozinho"), isso precisava de confirmação explícita do usuário antes de
codar (obtida — ver abaixo).

**Revisão cruzada — qualidade de código**: confirmou que o fix não
introduz condição de corrida (mensagens da mesma sessão processam
sequencialmente, `process_shop_sell` não tem `await` interno — mas
frágil a mudanças futuras que adicionem um `await` no meio, vale
comentário). Achou 2 problemas que o fix original não cobria: (a) a
pré-checagem de posse precisa somar através de TODAS as entradas que
casam no Inventory (não só "existe 1"), senão vender 1 unidade real
ainda permite reportar `stack_sold=999` e ser pago por 999 — mesma
classe de bug, só que sobre um item realmente possuído em parte; (b)
**achado novo, mais grave que o original**: `wallet.gold =
int(current_gold)` (linha 3405-3406) sincronizava o ouro do servidor
com o valor que o CLIENTE reporta, sem teto nem validação,
incondicionalmente, ANTES de qualquer checagem de item — mesmo com o
fix de posse aplicado perfeitamente, um cliente podia vender um item
BARATO que realmente possui com `current_gold: 999999999` no payload e
receber ouro ilimitado mesmo assim. Mesmo padrão idêntico em
`process_shop_buy` (linha 3290-3291).

Confirmado por pesquisa própria (não parte do achado de nenhum agente):
`Wallet.gold` do servidor já é autoritativo de verdade — a única fonte
real de crédito é `server/loot_processor.py:207`
(`wallet.gold += coins`, aplicado no pickup de loot, server-side) mais
os próprios `sell`/`craft`/`grant_items_to_player`. Não existe nenhum
cenário legítimo em que o gold do SERVIDOR estaria "desatualizado" em
relação ao que o cliente reporta — o comentário original
("servidor pode ter gold desatualizado, ex: moedas de loot adicionadas
localmente") não bate com a arquitetura atual (loot já é
100% server-autoritativo). `current_gold` era um resquício sem
propósito real, só risco.

**Decisão do usuário**: tratar os 2 problemas juntos no mesmo fix
("pode tratar os 2").

**Fix**: `process_shop_sell` — reordenado pra checar posse real (soma
através de todas as entradas que casam, mesmo idioma de `craft_item`)
e `Inventory` presente ANTES de creditar qualquer ouro; falha
(`item_not_owned`/`no_inventory`) em vez de suceder às cegas. Parâmetro
`current_gold` removido inteiramente (assinatura da função, leitura em
`server/session.py::_handle_sell_request`, e o mesmo em
`process_shop_buy`/`_handle_buy_request` — mesmo bug, mesma causa raiz,
mesmo fix). Cliente pode continuar mandando o campo no payload sem
efeito nenhum (schema de validação em `shared/messages.py` é
"permissivo de propósito" — campo extra não declarado não quebra nada,
só passa a ser ignorado) — não precisou mexer no client.

**Nota sobre a sugestão de schema do agente de arquitetura**: ele
sugeriu adicionar `stack_sold`/`item_value` como campos obrigatórios em
`C2S_REQUIRED` (`shared/messages.py`). Descartado: o próprio arquivo já
documenta a regra "PERMISSIVO de propósito — só campos NÚCLEO entram,
campo opcional não entra, senão todo campo novo vira mensagem
rejeitada" — `stack_sold`/`item_value` são opcionais com default no
handler (`stack_sold: int = 1`), então declará-los como obrigatórios
quebraria qualquer chamador que omite o campo. O agente não tinha essa
regra no contexto que recebeu. A validação de posse dentro do handler
(soma real contra o Inventory) já fecha o exploit sem precisar mexer
no schema.

**Testes**: `tests/test_craft_recycle_protocol.py::TestShopSellRemovesFromLiveInventory`
— o teste antigo que documentava o comportamento errado como
"esperado" (`test_vender_item_que_nao_esta_no_inventory_ainda_sucede_sem_remover_nada`)
foi INVERTIDO pra `test_vender_item_que_nao_esta_no_inventory_falha_sem_creditar_ouro`;
2 casos novos (`test_vender_mais_unidades_do_que_possui_falha_sem_creditar_ouro`,
`test_vender_sem_inventory_component_falha`). Revert-to-confirm: os 3
falham contra o código antigo (`git stash` nos 2 arquivos do fix),
exatamente como esperado. Suíte relacionada (shop/sell/buy/wallet/
craft/recycle, 53 casos): 50 passed, 3 failed pré-existentes (Postgres
via Docker fora do ar neste ambiente, confirmado por traceback
idêntico em todos os 3 — não relacionado à mudança).

Achados #2/#3/#4 da mesma auditoria (cura/mana infinitos via
`CONSUMABLE_USE`, duplicação de item via `INV_SYNC`, perda de ouro em
`BUY_REQUEST`) seguem pendentes — mesmo processo de revisão cruzada
antes de corrigir, a pedido do usuário.

## 152. Cura/mana infinitos via `CONSUMABLE_USE` (item 2 da auditoria §150) — revisão cruzada achou que o fix original ainda deixava o exploit vivo por outro caminho

16/09/2026. Mesmo processo do §151: achado do agente de mercado
reapresentado aos outros 2 especialistas (com um fix já rascunhado)
antes de codar.

**Achado original** (agente de mercado): `apply_consumable`
(`server/world_server.py`) lia `heal_instant`/`mana_restore`/`hot`/
`mana_hot`/`ooc_only` direto do payload do cliente, nunca comparados
contra o catálogo. Com `item_id=""`, o bloco de dedução do item era
pulado inteiramente, mas os efeitos rodavam do mesmo jeito — cura/mana
grátis, repetível, sem cooldown.

**Rascunho inicial** (meu, antes da revisão): resolver
`heal_instant`/`mana_restore`/`hot`/`mana_hot`/`ooc_only` a partir de
`item.consumable` (catálogo, via `self._item_factory_by_id`, mesmo
mecanismo já usado ali perto pro pergaminho de receita) em vez do
payload.

**Revisão cruzada — arquitetura**: confirmou a direção (mesmo padrão
já usado no bloco do pergaminho, nada novo inventado), mas achou uma
incompatibilidade de schema que o rascunho não previu: `item.consumable`
no catálogo guarda os campos do HoT SOLTOS (`heal_per_tick`/`interval`/
`ticks` direto no dict — ex: `simple_bread`/`roasted_meat`/waterskins),
NÃO aninhados numa chave `"hot"`/`"mana_hot"` — esse aninhamento é
montado só pelo CLIENTE, na hora de montar o payload de rede
(`ui/systems.py`). Ler `item.consumable.get("hot")` direto sempre
devolveria `None` pra todo item de comida/água do jogo — HoT quebraria
silenciosamente em produção, regressão da própria Fase 6 que a
docstring do método já cita. Sugeriu também extrair a resolução de
efeito pra uma função pura reusável entre cliente/servidor no futuro
(não implementado agora, fora do escopo combinado).

**Revisão cruzada — qualidade de código**: achou 2 problemas mais
sérios que o rascunho não cobria:
1. **Ordem**: `ooc_only` é lido do payload ANTES até do resto — se a
   resolução do catálogo não subisse o suficiente na ordem (logo após
   o guard de morte), o cliente continuaria podendo mentir
   `ooc_only=False` pra usar poção restrita a fora-de-combate durante
   combate.
2. **Achado mais grave, novo**: mesmo com os valores vindo do catálogo,
   o método NUNCA rejeitava quando o item não era encontrado no
   Inventory — só pulava o decremento (`if achou: decrementa`, sem
   `else: rejeita`) e seguia aplicando o efeito normalmente. Ou seja: o
   exploit sobrevivia ao fix original, só mudando de vetor — em vez de
   `item_id=""`, bastava reportar QUALQUER `item_id` real do catálogo
   que o jogador nunca possuiu. Prova concreta encontrada no próprio
   repo: `tests/test_craft_recycle_protocol.py::TestLearnRecipeOnline::
   test_consumir_pergaminho_aprende_receita_e_avisa_cliente` tinha um
   `setUp` que NÃO colocava o pergaminho no Inventory, e mesmo assim
   esperava sucesso — o comportamento errado estava documentado como
   "esperado" por um teste real.

**Decisão do usuário**: tratar os 5 pontos juntos (resolução 1x só
reaproveitada entre os blocos de pergaminho e cura/mana, reordenação
com `ooc_only` migrado corretamente, `hot`/`mana_hot` remontados dos
campos soltos, checagem de posse nova, testes atualizados).

**Fix**: `apply_consumable` reescrito — resolve o item via
`_item_factory_by_id` uma única vez logo após o guard de morte
(`item_id` vazio ou item que não é consumível de verdade rejeita ali,
`reason="no_item"`/`"not_consumable"`); `ooc_only`/`heal_instant`/
`mana_restore` lidos do dict do catálogo; `hot`/`mana_hot` remontados
a partir de `heal_per_tick`/`mana_per_tick`/`interval`/`ticks` soltos
(mesma transformação que `ui/systems.py` já faz pro payload de rede);
posse real confirmada no Inventory ao vivo ANTES de aplicar qualquer
efeito (`reason="not_owned"` se não achar) — decremento e aplicação
de heal/mana/HoT só acontecem depois disso. Bloco de pergaminho de
receita reaproveita o mesmo `_consumable` já resolvido, sem 2º lookup.

**Testes**: `tests/test_craft_recycle_protocol.py::TestLearnRecipeOnline`
— novo helper `_give()` (insere item real no Inventory, usado pelos 4
testes que precisavam dele mas não tinham antes); novo teste
`test_consumir_item_que_nao_possui_e_rejeitado_sem_aplicar_efeito`
(prova a posse real sendo exigida). `tests/test_consumables.py::
TestConsumableRejectedWhenDead` (3 casos, guard de morte via
`current_hp<=0` roda ANTES da resolução do catálogo, item_ids fictícios
`pocao_vida`/`pocao_regen` continuam funcionando pro propósito do
teste) — sem alteração, continuam passando. Revert-to-confirm: o teste
novo falha contra o código antigo (0 rejeições em vez de 1). Suíte
relacionada (consumable/regen/recipe, 66 casos reais + 12 falhas de
`test_hotbar_bg_leak.py` — capturadas pelo filtro "hot" de "hotbar",
mesmo Postgres fora do ar, não relacionado): sem regressão nos casos
reais.

Achados #3/#4 da mesma auditoria (duplicação de item via `INV_SYNC`,
perda de ouro em `BUY_REQUEST`) seguem pendentes.

## 153. Duplicação/forja de item via `INV_SYNC` (item 3 da auditoria §150) — pesquisa achou que o canal não tem mais nenhum uso legítimo vivo, revisão cruzada confirmou e achou 2 testes que documentavam o exploit como comportamento esperado

16/09/2026. Mesmo processo dos §151/§152. Diferente dos 2 anteriores,
este item exigiu uma pesquisa mais funda ANTES de rascunhar qualquer
fix, porque a correção óbvia ("validar que o payload é um subconjunto
do que já existe") parecia entrar em conflito com um caso de uso
legítimo conhecido (Recarregar) — precisou ser resolvida antes de ir
pra revisão cruzada.

**Achado original** (agente de mercado): `sync_player_inventory`
(`server/world_server.py`) fazia `inv.items.clear()` e reconstruía o
Inventory ao vivo do zero a partir da lista que o cliente reporta em
`INV_SYNC` — sem validar quantidade nem proveniência (só stats reais,
via `sanitize_inventory_payload`). Um cliente podia reportar 50 cópias
de um item caro nunca possuído, persistindo no banco.

**Pesquisa (antes de qualquer rascunho)**: inicialmente pareceu que
"Recarregar" (reload de aljava) dependia do full-replace do INV_SYNC
pra funcionar, o que tornaria uma validação simples de posse arriscada
(quebraria a troca de tipo de munição). Investigação mais funda
(seguindo a cadeia de chamadas real, não confiando em comentários)
revelou que essa premissa estava ERRADA: `_apply_recarregar`
(`ui/spell_system.py`) está no conjunto `_SELF_TARGET_SERVER_ONLY`,
que faz o cliente ONLINE pular essa função inteira em `_complete_cast`
— exclusão feita de propósito em 19/07/2026 (bug real de duplicação de
consumo de flecha). O resultado real de Recarregar já vem 100% de
`server/spell_completion_processor.py::_server_recarregar`, que muta
Inventory/Equipment reais do servidor diretamente. Rastreamento
similar do lado de loot achou que `_on_loot_action`/INV_SYNC também
está morto ali — `_try_send_online_loot_request` sempre retorna `True`
antes de alcançar esse código quando há requester online (sempre o
caso), e o handler real de loot confirmado
(`_handle_msg_loot_result`) já não chama isso desde o débito A4
(11/08/2026) — confirmado por um teste já existente
(`test_loot_result_com_item_nunca_manda_inv_sync`). Conclusão: hoje
NENHUM caminho de gameplay online real dispara INV_SYNC pedindo pra
ADICIONAR item — os únicos 2 emissores no cliente são código morto.

**Revisão cruzada — arquitetura**: confirmou a pesquisa de forma
independente (rastreou os mesmos 2 emissores, chegou à mesma
conclusão), validou que "rejeitar tudo, nunca parcial" é o padrão já
usado no projeto (`_handle_talent_update`, achados #1/#2). Achou 1
detalhe de implementação (a validação precisa somar `.stack` por
`item_id`, não contar entradas — já batia com o rascunho) e sugeriu um
comentário de aviso perto de `_SELF_TARGET_SERVER_ONLY` pra não virar
surpresa se algum dia alguém tirar "recarregar" de lá sem revisar este
fix.

**Revisão cruzada — qualidade de código**: confirmou a premissa
lendo o código (quase contradisse, mas concluiu que o caminho do
Recarregar é morto por 2 gates empilhados — frágil, mas morto hoje).
Achou 3 coisas: (1) a contagem do que já é possuído precisa acontecer
ANTES do `clear()`, nunca depois; (2) o fix só é seguro rodando em
cima do payload JÁ sanitizado (`sanitize_inventory_payload`), nunca em
dict cru do cliente — documentado como pré-condição explícita; (3)
**2 testes existentes iam quebrar** (não só o que eu já esperava):
`tests/test_hotbar_bg_leak.py::test_inventario_normal_fora_de_instancia_continua_funcionando`
e `tests/test_server.py::test_inv_sync_com_item_de_quest_avanca_progresso_da_quest`
— os dois mandavam INV_SYNC introduzindo item NOVO que o servidor
nunca tinha, documentando esse fluxo como válido. Precisavam ser
reescritos (conceder o item antes, simulando posse real) e não só
ajustados mecanicamente, já que representavam uma premissa que o fix
declara insegura por design.

**Fix**: `sync_player_inventory` — conta quanto de cada `item_id` o
servidor JÁ TEM (soma de `.stack`, antes de qualquer mutação); rejeita
TUDO (mantém o Inventory antigo intacto) se a lista excede
`inv.max_slots` ou se qualquer `item_id` pede mais do que já possuído;
loga a rejeição (`log.warning`) pra não virar um "congelamento
silencioso" se algum bug real (não malicioso) gerar um payload errado.
Comentário de aviso adicionado perto de `_SELF_TARGET_SERVER_ONLY`
(`ui/spell_system.py`) linkando pra este fix.

**Testes**: os 2 testes que documentavam o exploit como esperado foram
reescritos (item concedido ANTES do INV_SYNC, docstring atualizada
explicando a mudança de premissa). Novo
`tests/test_server.py::TestSyncPlayerInventoryOwnershipValidation` (5
casos, sem dependência de banco — item nunca possuído rejeitado,
quantidade maior que a possuída rejeitada, quantidade igual/menor
aceita, soma correta entre múltiplos slots do mesmo item, lista maior
que `max_slots` rejeitada). Revert-to-confirm: 3 dos 5 falham contra o
código antigo. Suíte relacionada (server/craft/recycle/loot/protocolo,
137 casos): 129 passed, 8 failed pré-existentes (Postgres via Docker
fora do ar neste ambiente — confirmado por traceback idêntico em
todos, não relacionado).

Achado #4 da mesma auditoria (perda de ouro sem entrega em
`BUY_REQUEST`) segue pendente.

## 154. Aljava nova "nasce vazia" ao trocar de aljava logo após esvaziar a antiga — race condition entre confirmação atrasada de flecha/Recarregar e troca de equipamento

16/09/2026. Bug relatado pelo usuário FORA da auditoria de projeto
inteiro (§150), enquanto discutíamos o achado #3 dela — sequência
exata confirmada por pergunta direta antes de investigar: "comprei uma
aljava comum, usei todas as flechas, e comprei a aljava de batalha, e
dizia que não tinha flechas na aljava."

**Causa raiz**: quando a última flecha é disparada (auto-attack ou
skill de arco), o servidor manda uma confirmação
(`queue_stats_update({"quiver_arrow_count": 0})`) — mas essa fila só é
despachada no próximo tick, não instantaneamente. Se o jogador comprar
e equipar uma aljava NOVA rápido o bastante, o cliente já trocou
localmente (equipar é predição instantânea, `client/
inventory_handlers.py::_equip_item` muta o slot na hora, só o pedido
pro servidor viaja depois) antes dessa confirmação atrasada chegar. O
handler que aplica `quiver_arrow_count`
(`client/network_handlers.py::_handle_msg_stats_update`) nunca checava
QUAL aljava gerou aquele valor — só escrevia em cima de qualquer coisa
que estivesse no slot offhand no momento em que a mensagem chega, que
já é a aljava nova. Mesmo mecanismo afeta a confirmação de Recarregar
(`_server_recarregar`, mesmo problema).

**Fix**: as 2 mensagens que mandam `quiver_arrow_count`
(`server/spell_completion_processor.py` — consumo de flecha no
disparo, e `_server_recarregar`) passam a incluir `quiver_item_id`
(identidade da aljava que gerou o valor). O cliente
(`_handle_msg_stats_update`) só aplica `quiver_arrow_count`/
`max_arrows`/`subtype` se `quiver_item_id` bater com a aljava
equipada AGORA — confirmação "órfã" (aljava já trocada) é
silenciosamente ignorada, sem tentar reconciliar (o próximo AUTO_ATTACK/
Recarregar corrige do jeito certo, contra o estado real). O bloco de
dedução de munição da BAG (campo separado, `ammo_id`/`ammo_taken`) não
depende de qual aljava está equipada — continua aplicando sempre,
sem relação com esta checagem.

**Testes**: `tests/test_client_ui.py` — 2 casos novos
(`test_quiver_arrow_count_ignorado_se_item_id_nao_bate_com_aljava_atual`,
`test_quiver_arrow_count_aplicado_quando_item_id_bate`). Revert-to-confirm:
o primeiro falha contra o código antigo reproduzindo o bug EXATO
relatado (aljava nova de 999 flechas vira 0 ao aplicar a confirmação
órfã da aljava antiga). Suíte relacionada (quiver/recarregar/arrow, 10
casos): sem regressão.

## 155. Perda de ouro sem entrega em `BUY_REQUEST` (item 4, último da auditoria §150) — checagem de espaço usava cache do cliente em vez do Inventory ao vivo; revisão cruzada pediu extrair função compartilhada

16/09/2026. Mesmo processo dos §151/§152/§153, último achado da
auditoria de projeto inteiro (§150).

**Achado original** (agente de mercado): `process_shop_buy`
(`server/world_server.py`) checava espaço no inventário usando
`last_inventory` — uma lista vinda de `session.last_client_payload
["inventory"]`, cache do ÚLTIMO payload que o cliente reportou, NUNCA
o Inventory ao vivo real — mais `self._pending_inv[session_id]`
(contador de compras da sessão ainda não confirmadas por um
SAVE_STATE, existindo só pra compensar esse cache ficar desatualizado
entre uma compra e o próximo save). Se o cache estivesse desatualizado
(cenário comum: jogador lootou/comprou algo que já populou o Inventory
ao vivo, mas ainda não mandou um payload fresco nesta sessão), a
checagem liberava uma compra que a bag REAL não comportava — ouro
debitado incondicionalmente ANTES da entrega real (que já usava o
Inventory de verdade e só entregava até o limite real, `break`
silencioso ao bater o teto) — cliente pagava o preço total mas podia
receber só parte (ou nada), e o servidor ainda respondia
`success:true, quantity:<pedido>` (não a quantidade real entregue).

**Revisão cruzada — arquitetura**: confirmou que `craft_item`
(mesmo arquivo) já faz exatamente o certo — calcula se cabe em stack
existente, senão exige slot livre REAL, reject-all antes de debitar
gold — e que `process_shop_buy` era o ÚNICO handler ainda usando cache
do cliente pra essa checagem (grep em `max_slots`/`inv.items` no
arquivo inteiro não achou outro caso). Confirmou por leitura que
`last_inventory`/`_pending_inv` só têm uso dentro deste handler (5
pontos: init, buy, despawn, confirm_inventory_save, chamada em
`_handle_save_state`) — remoção segura, sem tocar em nenhum outro
consumidor de `session.last_client_payload["inventory"]` (que tem
outros usos legítimos fora deste parâmetro específico). Alertou pro
detalhe de implementação: quantidade comprada pode precisar de MAIS de
1 slot novo (`craft_item` só cria 1 item por vez, nunca precisa desse
cálculo) — a simulação precisa fazer a divisão-teto certa, espelhando
fielmente o loop de entrega real.

**Revisão cruzada — qualidade de código**: achado mais importante —
implementar a simulação de "cabe?" como uma cópia solta da lógica de
entrega (passo 6) é risco de DIVERGÊNCIA futura (uma muda, a outra
fica pra trás) — mesma classe de risco que motivou o projeto já
extrair `loot_processor.py::_stack_or_add_item` como ponto único de
verdade antes. Pediu pra extrair uma função só, usada tanto pelo check
(dry-run) quanto pela entrega real. Também alertou: a aritmética da
checagem tem que ser O(1) (divisão/teto), NUNCA um loop por unidade —
`quantity` vindo do cliente não tem teto superior, um loop
proporcional a ele seria vetor de trava do event loop single-thread do
servidor (regra já documentada no projeto). Confirmado por leitura dos
testes reais: nenhum teste chama `process_shop_buy`/`last_inventory`/
`_pending_inv`/`confirm_inventory_save` diretamente — remoção não
quebra nada existente.

**Fix**: novo `WorldServer._plan_stack_delivery(inv, factory, qty,
apply)` — ponto único de verdade pra "cabe entregar N unidades?",
usado tanto no passo 3 de `process_shop_buy` (dry-run, `apply=False`,
nunca muta o Inventory) quanto no passo 6 (entrega real, `apply=True`,
reaproveitando a MESMA função em vez de uma 2ª cópia da lógica de
empilhamento). Checagem de espaço passa a usar o Inventory AO VIVO
real; rejeita a compra INTEIRA (`reason: "inventory_full"`) SEM
debitar ouro nenhum se não couber tudo — nunca mais entrega parcial.
`last_inventory` (parâmetro de `process_shop_buy` + cálculo em
`_handle_buy_request`) e todo o mecanismo `_pending_inv` (dict,
incremento, reset em `despawn_player`, método
`confirm_inventory_save` inteiro + sua chamada em `_handle_save_state`)
removidos — obsoletos pela mesma razão que motivou sua criação deixar
de existir.

**Testes**: novo `tests/test_craft_recycle_protocol.py::
TestShopBuyRealInventorySpaceCheck` (5 casos, sem dependência de banco
— compra rejeitada com Inventory real cheio mesmo sem cache de
payload nenhum populado, compra múltipla de item não-empilhável
exigindo slots suficientes, empilhamento em stacks parciais existentes
sem pedir slot novo, quantidade absurda (10⁹) rejeitada rápido sem
travar, compra normal com espaço real sucede e debita o valor certo).
Revert-to-confirm: 3 dos 5 falham contra o código antigo (a chamada
sem `last_inventory` cai no default `None` do parâmetro antigo,
reproduzindo exatamente a condição de cache vazio/desatualizado — o
bug real). Suíte relacionada (shop/buy/craft/recycle/inventory/
instance_progression, 141 casos): 131 passed, 10 failed pré-existentes
(Postgres via Docker fora do ar neste ambiente, confirmado por
traceback idêntico em todos, não relacionado).

Com este item, os 4 achados de severidade Alta da auditoria §150 estão
todos corrigidos.

## 156. Bug da aljava (§154) reaberto pelo usuário em produção — fix cobriu só metade do código vulnerável (STATS_UPDATE), faltava a via paralela via COMBAT_RESULT

16/09/2026, poucos minutos depois do deploy do §155 pro Hetzner. Usuário
testou em produção (partida de BG real) e reportou, com print de tela:
aljava mostrando 986/999 no HUD, mas o jogo recusando o tiro com "Aljava
vazia! Use Recarregar." — exatamente o sintoma do §154, aparentemente
não corrigido.

**Causa raiz**: o fix do §154 corrigiu `client/network_handlers.py::
_handle_msg_stats_update` — o handler do `STATS_UPDATE` que carrega
`quiver_arrow_count`/`quiver_item_id`, mandado por
`_server_apply_ranged_physical` (auto-attack) e `_server_recarregar`.
Só que existia uma SEGUNDA via, totalmente separada, decrementando
`arrow_count` no cliente: `client/remote_entity_handlers.py::
_apply_combat_result` (linha ~395-400), acionada pelo `COMBAT_RESULT`
(mensagem DIFERENTE do `STATS_UPDATE`) toda vez que o próprio arqueiro
confirmava um tiro de auto-attack — fazia `arrow_count -= 1`
diretamente no que quer que estivesse no slot offhand NO MOMENTO,
sem NENHUMA checagem de identidade (nem antes do §154, nem depois —
o fix anterior nunca tocou este código, só o `STATS_UPDATE`). Mesma
classe de race condition do §154: trocar de aljava com tiros "em voo"
(`COMBAT_RESULT` atrasado, referente à aljava ANTIGA) fazia esse
decremento cego bater na aljava NOVA.

**Por que passou pela revisão cruzada de §154 sem ser achado**: a
revisão cruzada analisou o achado e o fix EXATAMENTE como propostos
(só o `STATS_UPDATE`) — nenhum dos dois agentes foi instruído a
procurar OUTRAS vias que também mutassem `arrow_count`, e a busca
"quiver_arrow_count" (usada como contexto de pesquisa) não aparece
nesta segunda via (ela nunca usou esse campo, só decrementa
diretamente). Lição: ao corrigir um campo específico sendo mal
sincronizado, vale grep pelo NOME DO CAMPO MUTADO (`arrow_count =`),
não só pelo nome da mensagem que uma pesquisa inicial já achou.

**Fix**: removido o decremento em `_apply_combat_result` (linhas
395-400 antigas) — o `STATS_UPDATE` (já corrigido no §154, idempotente
e com checagem de identidade) já é o único evento necessário pra
manter a cópia local sincronizada, mesmo timing/gatilho
(`_server_apply_ranged_physical` manda os dois a partir do MESMO
evento server-side). Fonte de verdade única agora, sem via paralela.

**Testes**: `tests/test_client_ui.py::
test_apply_combat_result_nao_decrementa_arrow_count_diretamente`
(novo) — confirma que `_apply_combat_result` não mexe mais em
`arrow_count`. Revert-to-confirm: falha contra o código antigo
(999→998, decremento cego confirmado). Suíte `test_client_ui.py`
completa (174 casos) e `test_character_animation.py` (65 casos): sem
regressão.

**Lição de processo**: revisão cruzada valida o design do fix
PROPOSTO, não garante que o fix cobre TODO o código relacionado ao
sintoma — vale, ao corrigir um bug de sincronização de estado, buscar
por TODOS os pontos que mutam o mesmo campo, não só o que o achado
original apontou.

**Correção do teste, a pedido do usuário (mesmo dia)**: o usuário
apontou, corretamente, que
`test_apply_combat_result_nao_decrementa_arrow_count_diretamente` só
provava que UMA função isolada não tocava mais `arrow_count` — não
reproduzia o SINTOMA real (jogo dizendo "sem flechas" com a aljava
cheia). Pediu explicitamente o cenário fiel: comprar aljava comum, usar
até sobrar pouco, comprar/trocar pra aljava de batalha, atirar de novo
e confirmar que não acusa "sem flechas" achando que ainda é a aljava
velha. Antes de escrever o teste, nova varredura por TODO ponto que
muda `.arrow_count =` no cliente inteiro (não só o que os achados
anteriores já apontavam) — confirmou só 2 pontos vivos em produção:
`_handle_msg_stats_update` (já corrigido) e `client/
save_sync_handlers.py::_restore_item` (restauração de save no login,
sem relação com o bug em combate). Novo
`tests/test_client_ui.py::test_troca_de_aljava_durante_combate_nao_causa_falso_aljava_vazia`
— usa o código de produção REAL de equipar
(`client/inventory_handlers.py::_equip_item`, não uma reconstrução
simplificada), reproduz a sequência completa (atira aljava comum até
sobrar 7 → equipa aljava de batalha → confirmação órfã da aljava
antiga chega atrasada → 10 tiros com a aljava nova) e verifica
explicitamente, a cada tiro, a condição EXATA que dispara "Aljava
vazia! Use Recarregar." (`arrow_count<=0`, `ui/systems.py:1141`) nunca
fica verdadeira enquanto a aljava realmente tem flechas. Revert-to-confirm
mais rigoroso: revertendo os 2 arquivos do fix (§154 E §156) juntos —
o teste falha reproduzindo o sintoma EXATO relatado (confirmação órfã
de `arrow_count=6` da aljava antiga aplicada por cima dos 999 da
aljava nova, virando 6). Com o fix restaurado, passa — suíte completa
`test_client_ui.py` (175 casos): sem regressão.

## 157. Bug da aljava reaberto DE NOVO após o §156 — em vez de tentar um 4º fix às cegas, conecta a ferramenta de diagnóstico já existente no projeto aos pontos reais

16/09/2026, minutos depois do deploy do §156. Usuário testou de novo
(BG real) e reportou algo NOVO e pior: "além de continuar o mesmo bug,
ainda aumentou o bug, agora não desconta da aljava nova que comprei"
— comprou aljava básica, gastou até sobrar 2, comprou aljava de
batalha, deu vários ataques sem NENHUM desconto visível, e depois a
mensagem de "sem flechas" apareceu mesmo com a aljava mostrando 999.

**Decisão explícita, em vez de tentar mais um fix**: 2 rodadas de
correção (§154, §156) já verificadas com teste + revert-to-confirm
mas ainda assim insuficientes, e a última piorou o sintoma — sinal
de que investigar mais fundo por leitura de código estática (sem dado
real de uma sessão de jogo de verdade) já não é produtivo. O projeto
já tem uma ferramenta pronta pra exatamente esse tipo de bug —
`debug/archer_debug.py` — só que ela nunca tinha sido conectada aos
pontos que mexem em munição/equipamento (`grep` por `.log("ARROW"`
não achava NENHUM call site real antes desta entrada — a infra existia
só documentada, nunca ligada).

**O que foi feito** (só instrumentação, NENHUMA lógica mudou):
- `server/spell_completion_processor.py` — evento `ARROW` em
  `_server_apply_ranged_physical` (consumo por tiro) e
  `_server_recarregar`, incluindo `quiver_item_id`/`quiver_obj_id`
  (identidade Python do objeto, não só o id do catálogo — detecta
  MUTAÇÃO indevida vs. troca de referência).
- `server/world_server.py::equip_item_from_inventory` — evento `EQUIP`
  quando o item equipado é uma aljava, com `inv_index`/`item_id`/
  `arrow_count` no momento do equip.
- `client/network_handlers.py::_handle_msg_stats_update` — evento
  `ARROW` toda vez que um `quiver_arrow_count` chega, ANTES de decidir
  aplicar (mostra o `item_id` do payload vs. o `item_id` do que está
  equipado agora), e `BLOCK` explícito quando a checagem de identidade
  do §154 rejeita.
- `client/inventory_handlers.py::_equip_item` — evento `EQUIP` (mesmo
  formato do lado servidor).
- `ui/systems.py` — a linha que já existia (`log_block("quiver_empty")`,
  desde antes desta sessão) ganhou `quiver_item_id`/`quiver_obj_id`/
  `max_arrows` extras, pra saber EXATAMENTE qual aljava (e se é o MESMO
  objeto Python de antes) no momento em que "Aljava vazia!" dispara.
- Corrigido de quebra: docstring do módulo estava desatualizada
  (dizia "setar DBG_ENABLED=True", mas o mecanismo real já é a env var
  `RPG_DEBUG_ARCHER` desde antes desta sessão) — documentado o evento
  `EQUIP` (novo) e os campos novos de `ARROW`.

**Verificação de que a instrumentação funciona**: rodada sintética
(suíte de teste com `RPG_DEBUG_ARCHER=1`) confirma as linhas sendo
geradas corretamente, formato legível, sem exceção. Suíte completa
(`test_client_ui.py`, 175 casos): sem regressão — instrumentação é
só leitura + escrita em arquivo de log, gated por env var, nunca muda
comportamento de gameplay.

**Próximo passo, pendente do usuário**: ligar `RPG_DEBUG_ARCHER=1`
nos DOIS lados (cliente E servidor — precisa nos dois, é o mesmo
pipeline) e reproduzir a sequência exata relatada (comprar aljava
básica, gastar até sobrar pouco, comprar/equipar aljava de batalha,
atirar até a mensagem de "sem flechas" aparecer com a aljava
mostrando cheia) — depois compartilhar os 2 arquivos de log
(`debug/logs/archer_debug_client.log`/`archer_debug_server.log`).
Com dado real dos dois lados (não mais suposição sobre qual `item_id`/
`inv_index` cada lado está vendo), a causa raiz real deve ficar
evidente sem mais tentativa e erro.

## 158. Logs do §157 vieram — descartada a hipótese do usuário (equip com slot já ocupado), instrumentação estendida pra BUY_REQUEST/EQUIP_ITEM

16/09/2026, com `RPG_DEBUG_ARCHER=1` ligado dos dois lados (servidor
via `.env` de produção + restart, cliente rodado localmente pelo
Claude a pedido do usuário, que não achou o arquivo de log por conta
própria). Cruzando os dois logs: o CLIENTE equipou a aljava de batalha
localmente com sucesso (`EQUIP quiver_item_id=battleground_quiver
inv_index=0`), mas o SERVIDOR nunca recebeu/aplicou esse equip —
só existe 1 evento `EQUIP` no log do servidor (a aljava básica, início
da sessão); depois disso o servidor seguiu confirmando disparo pra
`basic_quiver` até zerar, terminando em `BLOCK reason=
no_quiver_or_empty`. `quiver_obj_id` idêntico do início ao fim do lado
servidor confirma: nunca trocou de objeto.

Usuário propôs uma hipótese própria: "a BG tem itens instanciados,
talvez não aceite equipar outro item quando o slot já está ocupado".
**Testada e descartada** com reprodução direta (não leitura estática):
script isolado chamando o código REAL dos dois lados —
`WorldServer.process_shop_buy`/`equip_item_from_inventory` (servidor)
e `NetworkHandlers._handle_msg_buy_result`/`InventoryHandlers.
_equip_item` (cliente) — comprando e equipando aljava básica, depois
comprando e equipando aljava de batalha por cima (slot JÁ ocupado).
Resultado: funciona perfeito, os dois lados terminam com
`battleground_quiver` equipada. `test_equip_item_troca_item_ja_
equipado_volta_pro_fim_da_bag` (`tests/test_equip_item_protocol.py`)
já cobria exatamente esse caminho (swap com slot ocupado) e sempre
passou — a lógica de troca não tem bug, dentro ou fora da BG, é o
MESMO código pros dois casos.

**Ponto cego achado nessa investigação**: `equip_item_from_inventory`
só logava `EQUIP` no caminho de SUCESSO — as 2 saídas silenciosas
(`inv_index` fora do alcance da lista, ou posição vazia) nunca
deixavam rastro nenhum. Exatamente essas 2 saídas são as únicas que
explicariam "zero EQUIP no log do servidor" — mas não dava pra saber
QUAL das duas, nem o estado real do Inventory do servidor no
momento exato do EQUIP_ITEM.

**O que foi feito** (de novo, só instrumentação, nenhuma lógica
mudou):
- `server/world_server.py::equip_item_from_inventory` — evento
  `EQUIP_ATTEMPT` logo que `inv`/`eq_comp` resolvem (ANTES de saber se
  vai suceder), com `inv_index` pedido + `inv_len`/`inv_item_ids` do
  Inventory REAL do servidor naquele instante; `BLOCK
  "equip_index_out_of_range"`/`"equip_slot_empty"` cobrindo as 2
  saídas silenciosas identificadas acima.
- `server/world_server.py::process_shop_buy` — evento `BUY` (servidor)
  toda compra bem-sucedida: `item_id`, `qty`, tamanho/conteúdo do
  Inventory antes/depois da entrega.
- `client/network_handlers.py::_handle_msg_buy_result` — evento `BUY`
  (cliente) espelhando o mesmo formato, logo após o item entrar na bag
  local (antes do auto-equip).
- Docstring do módulo atualizada com os 2 eventos novos.

**Por que não foi só mais um fix às cegas**: a reprodução isolada
provou que compra+equip sozinhos (sem NENHUMA outra atividade
concorrente) funcionam certo nos dois lados — então o que falta
capturar é a ORDEM/interleaving real de mensagens que aconteceu
durante o jogo de verdade (voltar pra lane, atacar, tudo isso rodando
em paralelo com BUY_RESULT/EQUIP_ITEM), não a lógica de equipar em si.
Só um log de BUY ao lado do EQUIP_ATTEMPT já existente permite ver essa
ordem real na próxima reprodução, em vez de mais uma hipótese.

Verificação: `test_client_ui.py` (175 casos) e
`TestEquipItemFromInventory`/`TestUnequipItemSlot` (não dependem de
Postgres) sem regressão; reprodução isolada do §157/§158 re-rodada com
a instrumentação nova, resultado idêntico (sem bug no cenário
sintético). `TestEquipItemProtocolDispatch` e outros testes async que
dependem de `server.auth`/Postgres não rodaram nesta sessão (container
`rpg_postgres` local não estava de pé) — não relacionado a esta
mudança.

**Próximo passo, pendente do usuário**: reproduzir a sequência de novo
com a instrumentação nova (servidor já tem `RPG_DEBUG_ARCHER=1` desde
o §157; cliente precisa rodar com a mesma env var — combinar como
reproduzir) e compartilhar os 2 logs atualizados. Lembrete pendente:
depois de fechar esta investigação (com fix aplicado ou descartada por
falta de mais dado), desligar `RPG_DEBUG_ARCHER=1` do `.env` de
produção e reiniciar o serviço — compromisso já assumido com o
usuário, ainda não cumprido.

## 159. CAUSA RAIZ REAL do bug da aljava (§154/§156/§157/§158): talento Reciclagem inseria item na bag do servidor sem avisar o cliente — corrigido ignorando o talento dentro da BG

16/09/2026, com o log do §158 (`BUY`/`EQUIP_ATTEMPT`) finalmente
capturando a ordem real de mensagens. Reprodução em produção (usuário
jogando, cliente rodado localmente pelo Claude com
`RPG_DEBUG_ARCHER=1`) achou a pista decisiva no log do SERVIDOR, bem
antes da 2ª compra de aljava:

```
BUY      item_id=battleground_quiver  inv_len_before=1  inv_len_after=2  inv_item_ids_after=['arrow', 'battleground_quiver']
EQUIP_ATTEMPT  inv_index=0  inv_len=2  inv_item_ids=['arrow', 'battleground_quiver']
```

O Inventory do SERVIDOR já tinha uma "Flecha" (`item_id="arrow"`) antes
mesmo dessa compra — o log do CLIENTE nunca mostrou isso, a bag dele
aparecia vazia. Isso empurrou a aljava de batalha pro índice 1 no
servidor, mas o cliente (sem saber da Flecha) calculou índice 0 e
mandou `EQUIP_ITEM {inv_index: 0}` — o servidor tentou equipar a
Flecha (índice 0 dele), que não tem slot de equipamento
(`item.slot == ""`), e `equip_item_from_inventory` retorna `None`
silenciosamente (`target_slot not in eq_comp.slots`). Explica 100% dos
sintomas de §154 a §158: zero evento `EQUIP` pra aljava de batalha no
servidor, cliente mostrando 999 flechas (equipou local, nunca soube
que o servidor recusou), e a mensagem de "sem flechas" (servidor
seguia com a aljava básica ZERADA, nunca trocou de verdade).

**De onde vinha a "Flecha" fantasma**: usuário negou compra de
munição avulsa, uso de GM (`GM_ADD_ITEM`) e loot normal na BG — os 3
hipóteses anteriores descartadas por ele diretamente. Busca exaustiva
por todo ponto que faz `inv.items.append` no servidor (`grep`) achou o
culpado real: `server/server_death_handler.py`, bloco "5b. Reciclagem"
— talento que devolve % das flechas que acertaram um mob morto pro
matador. Decisão de 03/08/2026 fazia essa devolução ir DIRETO pra bag
do killer quando dentro da instância (`is_in_normalized_progression`),
"sem precisar lootear o corpse" — mas esse caminho NUNCA mandava
nenhuma notificação pro cliente (nem `INVENTORY_UPDATE`, nem nada).
Toda vez que o usuário matava um mob com Reciclagem ativa dentro da
BG, o servidor ganhava uma "Flecha" na bag EM SILÊNCIO — o cliente
nunca ficava sabendo, e a bag das duas cópias divergia sem nenhum
sintoma visível até uma ação POSICIONAL (equipar) expor a diferença.

**Por que a investigação anterior (loot) não achou isso**: o usuário
respondeu "não tem loot na BG" com razão — Reciclagem tecnicamente não
é "loot" (não passa por corpse/`LOOT_AVAILABLE`), é uma mecânica de
talento à parte que o próprio usuário não associou ao sintoma até eu
mostrar o log com o item_id exato.

**Decisão do usuário sobre a correção**: em vez de consertar a
notificação (mandar `INVENTORY_UPDATE` quando Reciclagem entrega na
bag), o usuário decidiu que Reciclagem **não deveria ter efeito
nenhum dentro da BG** — é mecânica de progressão real
(farm/roleplay no mundo aberto), sem propósito dentro de PvP
instanciado. Reverte a decisão de 03/08/2026 (ir direto pra bag) em
vez de estendê-la.

**Fix aplicado** (`server/server_death_handler.py`): o bloco inteiro
de Reciclagem agora checa `is_in_normalized_progression` ANTES de
fazer qualquer coisa — se o matador está dentro da instância, o
talento simplesmente não recupera flecha nenhuma (nem bag, nem
corpse). Fora da instância, comportamento 100% inalterado (continua
indo pro corpse, como sempre foi desde antes de 03/08/2026). Removido
o sub-bloco "vai direto pra bag" inteiro (ficou morto — nunca mais
alcançável).

**Testes** (`tests/test_instance_progression.py`):
- `TestInstanceReciclagemGoesToBag` — classe e teste renomeados
  (`test_dentro_da_instancia_reciclagem_nao_tem_efeito`, era
  `..._flecha_recuperada_vai_pra_bag`): agora prova que a bag NÃO
  muda e o corpse NÃO ganha a flecha reciclada dentro da instância.
  Revert-to-confirm: `git stash` só do arquivo do fix reproduziu
  exatamente `Item('Flecha', common)` aparecendo sozinho na bag —
  falha idêntica ao sintoma relatado.
- `TestInstanceShoppingClientServerAgreement` (nova) — reprodução
  ponta a ponta usando código REAL dos dois lados
  (`WorldServer.process_shop_buy`/`equip_item_from_inventory` +
  `NetworkHandlers._handle_msg_buy_result`/`InventoryHandlers.
  _equip_item`), cada lado com seu próprio Inventory/Equipment
  independente: compra arco → aljava básica → aljava de batalha por
  cima, confirma que as duas cópias (cliente/servidor) concordam em
  cada equip E que a aljava básica substituída volta pro fim das DUAS
  bags. Prova que, sem a Flecha fantasma, a troca de aljava nunca teve
  bug nenhum na lógica de compra/equipar em si (consistente com a
  reprodução isolada do §158, que já mostrava isso sem explicar de
  onde vinha a divergência).

Suíte completa (`test_instance_progression.py` + `test_client_ui.py`):
218 casos, sem regressão.

**Pendências que continuam abertas**: (1) desligar
`RPG_DEBUG_ARCHER=1` do `.env` de produção e reiniciar o serviço,
compromisso assumido no §157, ainda não cumprido; (2) o achado do
§157 sobre `equip_item_from_inventory`/protocolo `EQUIP_ITEM` ser
posicional (nunca confirma por identidade, falha silenciosa sem
`EQUIP_REJECTED` em caso de índice inválido/item sem slot) continua
válido como classe de risco arquitetural — resolvido o GATILHO deste
incidente específico (Reciclagem), mas o mecanismo de protocolo em si
continua frágil a qualquer FUTURA fonte de desync entre
`Inventory.items` do cliente e do servidor. Não mexido agora — o
usuário pediu o fix pontual (desligar Reciclagem dentro da BG), não
uma revisão do protocolo `EQUIP_ITEM` — registrado aqui caso um caso
novo da mesma família apareça, pra não reinvestigar do zero.

## 160. Revisão cruzada do fix §159 (3 agentes) + trava explícita do talento no overlay de instância + limpeza de código morto achado na investigação

16-17/09/2026. Usuário pediu revisão do commit do §159 por 3 agentes
independentes antes de seguir — achados relevantes (nenhum bloqueante):
(1) `TestInstanceReciclagemGoesToBag` continuava com esse nome mesmo
depois do teste ter sido invertido pra provar o oposto — corrigido pra
`TestInstanceReciclagemNoEffect`; (2) o talento fica mudo dentro da BG
(nenhum feedback de "não funciona aqui") — não é uma piora do §159 (o
comportamento antigo também nunca notificava nada), mas motivou a
melhoria de UX implementada aqui; (3) ordem de checagem no gate
`_in_bg_recycle` é ligeiramente diferente do padrão do resto do
arquivo — cosmético, sem custo real, não mexido.

**Pedido do usuário nesta entrada**: em vez de só bloquear o EFEITO no
momento da morte do mob (§159, ainda válido como defesa em
profundidade), travar o talento na ORIGEM — dentro da árvore de
talentos da instância — com feedback visual (nó vermelho, "0/0") na
aba de talentos. Pergunta explícita: esse desenho serve pra qualquer
talento futuro que precise ser ignorado na BG, não só Reciclagem?
**Sim, por desenho** — é exatamente pra isso que existe uma lista
central em vez de um `if talent_id == "bardo_reciclagem"` hardcoded.

**Mecanismo novo — `INSTANCE_DISABLED_TALENTS`** (`content/
talent_data.py`, `frozenset[str]`, hoje só `{"bardo_reciclagem"}`) —
fonte única lida nos dois lados:
- `server/instance_progression.py::enter_normalized_progression` —
  talentos dessa lista entram com **0 pontos** alocados no overlay
  "tudo maxado" da instância (em vez do `max_points` normal). Como
  `arrow_recovery_enabled` (e qualquer `cs_flags` futuro) é calculado
  a partir de `TalentTree.allocated` via `_apply_talent_modifiers`
  (mesmo pipeline de sempre, nenhum código novo), o efeito nasce
  desligado pela via NORMAL do sistema de talentos — não por um `if`
  avulso no momento de uso. Resto do build continua 100% maxado
  (provado por teste, ver abaixo).
- `ui/talent_system.py` — `TalentSystem._is_disabled_in_instance()`
  (novo helper: talento na lista E `InstanceInventoryUIState.active`,
  mesmo critério de "estou na BG" já usado em `ui/minimap.py`).
  `_render_node()` mostra "0/0" em vermelho (não "0/1" — sinaliza
  "sem potencial aqui", não só "sem pontos gastos ainda") com
  borda/fundo/texto vermelhos; tooltip ganha a linha "Sem efeito
  dentro da Battleground." em vermelho.
- A checagem `_in_bg_recycle` do §159 (`server/
  server_death_handler.py`) **continua no código** — tecnicamente
  redundante agora (o talento nunca liga dentro da BG pela via nova),
  mas mantida como defesa em profundidade (mesmo padrão já usado em
  `equip_item_from_inventory`, que recusa de novo no servidor mesmo o
  cliente já bloqueando) — decisão explícita de manter, não remover,
  discutida com o usuário.

**Código morto achado e removido** (`engine/world_systems.py`,
`DeathHandlerSystem.update()`): existia uma SEGUNDA cópia completa da
lógica de Reciclagem (mesma fórmula, dano/aljava/fallback de item),
"espelho" do que `server/server_death_handler.py` faz. Confirmado
código MORTO por investigação (não suposição): `DeathHandlerSystem`
só processa entidades com `PendingDeath`; a ÚNICA coisa que adicionaria
`PendingDeath` num mob no mundo LOCAL do cliente seria
`CombatSystem._handle_death` — mas `CombatSystem` é instanciado em
`game.py` só pra registrar em `register_services` (outros sistemas
consultam `_svc.combat`), NUNCA entra em `self.systems` (comentário no
próprio arquivo: "IA e spawn de mobs: gerenciados pelo servidor") — ou
seja, `CombatSystem.update()` nunca roda no cliente online, então
nunca adiciona `PendingDeath` a nada. O outro caminho possível,
`StatusEffectSystem` (tick de DoT, que ESSE sim roda no cliente) tem
uma checagem `if not is_player` antes de adicionar `PendingDeath` — e
só o PRÓPRIO player tem `CombatStats` real no cliente (mob remoto só
tem `RemoteEntityMeta.hp`, sem `CombatStats`), então esse branch nunca
alcança um mob local também. Removido só o bloco de Reciclagem (30
linhas) — resquício claro da era antes de "não existe mais versão
offline separada" (topo do CLAUDE.md).

**NÃO removido agora**: `DeathHandlerSystem` inteiro (XP/loot/corpse/
respawn/som/Vitória Iminente) parece ser TODO código morto pela mesma
razão (nunca recebe `PendingDeath` de verdade), mas confirmar isso pra
CADA responsabilidade da classe (ex.: `XPSystem` lê `pending_xp` desta
classe — precisa checar se XP do cliente vem só de rede ou também
daqui) é um escopo maior que o desta sessão. Registrado aqui como
candidato a limpeza futura — não repetir a investigação do zero.

**Testes** (`tests/test_instance_progression.py`):
- `test_enter_zera_reciclagem_mas_mantem_resto_do_build_maxado` (nova)
  — prova que `enter_normalized_progression` zera só
  `bardo_reciclagem` (0 pontos, `arrow_recovery_enabled=False`) e
  mantém TODO o resto do build "bardo" maxado normalmente. Revert-to-
  confirm: `git stash` só de `content/talent_data.py` +
  `server/instance_progression.py` reproduziu a falha exata
  (`1 != 0`) antes do fix.
- Classe renomeada pra `TestInstanceReciclagemNoEffect` (era
  `...GoesToBag`, achado da revisão cruzada).

Suíte: `test_instance_progression.py` (44), `test_client_ui.py` (177),
`test_instance_talent_effects_sync.py`, `test_towers.py`,
`test_minions.py`, `test_jungle_camps.py`, `test_char_stats.py`,
`test_bg_kill_credit.py` — 348 casos no total entre os arquivos
tocados, sem regressão.

## 161. Retomando `PROJECT_WIDE_REVIEW.md` — item #2: task de autosave sem referência (GC podia matar a task no meio)

17/09/2026. Primeiro item da lista "Por onde começar" da auditoria de
projeto inteiro (16/09/2026) ainda pendente, depois dos 4 exploits de
economia (já corrigidos, §151-§155).

`server/session.py::SessionManager._on_tick` disparava o autosave
periódico (5 em 5 min) via `asyncio.create_task(self._autosave_all())`
**sem guardar a Task em lugar nenhum**. O event loop asyncio só mantém
referência FRACA a uma task fire-and-forget — nada impede o GC de
coletá-la NO MEIO da execução, interrompendo o autosave de TODOS os
players ainda não salvos naquele lote, silenciosamente (sem exceção,
sem log, sem sintoma óbvio pro usuário além de progresso perdido num
save que "deveria" ter rodado). O próprio arquivo já tinha a mitigação
certa pra exatamente esse padrão — `_track_dispatch_task`/
`_dispatch_tasks` (16/08/2026, §44/BENCHMARK §E.1), só nunca foi
aplicada ao autosave.

**Fix**: mesmo padrão, dedicado (`_track_autosave_task`/
`_autosave_tasks`, `server/session.py`) — task de autosave ganha
referência forte assim que criada, callback de término limpa o
registro e loga qualquer exceção (antes: exceção também podia se
perder silenciosamente, não só a task). `shutdown_dispatch_tasks`
(chamado pelo `KeyboardInterrupt` de `server/main.py`) passou a
cancelar/esperar `_autosave_tasks` junto de `_dispatch_tasks` — mesmo
motivo (task fire-and-forget ainda viva no instante do Ctrl+C),
dedicado por categoria (rede vs. escrita em banco) mas tratado no
mesmo chokepoint de desligamento.

**Teste**: reproduzir o bug de verdade (GC coletando a task no meio,
num timing exato) não é praticável de forma determinística — o valor
do teste está em provar a FIAÇÃO certa, não o bug em si.
`tests/test_graceful_shutdown.py::TestAutosaveTaskTracked`:
- `test_autosave_task_fica_com_referencia_forte_e_some_ao_terminar` —
  dispara `_on_tick` no tick de autosave, confirma a task registrada
  imediatamente, aguarda terminar, confirma que o registro se limpa
  sozinho (nunca cresce sem limite).
- `test_shutdown_cancela_autosave_ainda_pendente` — autosave "lento"
  (trava num `asyncio.Event`) ainda em andamento quando
  `shutdown_dispatch_tasks` roda — confirma que é cancelada e
  removida do registro, não fica órfã.

Revert-to-confirm: `git stash` só de `server/session.py` reproduziu a
falha exata dos 2 testes novos (`AttributeError:
'SessionManager' object has no attribute '_autosave_tasks'`) antes do
fix.

Suíte: `test_graceful_shutdown.py`, `test_instance_persist_guard.py`,
`test_session.py` — 112 casos, sem regressão (Postgres local
confirmado de pé pra esta rodada).

## 162. `PROJECT_WIDE_REVIEW.md` item #3 — `PlayerInputSystem._manual_attack` removido (código morto órfão); `_space_engage` NÃO mexido (decisão já tomada antes)

17/09/2026. `ui/systems.py::PlayerInputSystem` tinha DUAS funções de
ataque manual (ESPAÇO) citadas juntas pela auditoria como "código
morto testado por si mesmo" — investigação achou que são casos
DIFERENTES, não devem receber o mesmo tratamento:

- **`_space_engage`** — já tinha decisão explícita do usuário
  registrada em 26/08/2026 (ver histórico logo acima destas entradas,
  "Correção 2 — SPACE ainda não alcançava neutro + limpeza do código
  paralelo"): a chamada real (`KEYDOWN K_SPACE` dentro de `update()`)
  foi removida, mas o MÉTODO foi mantido de propósito ("só remover a
  chamada" — os 4 testes que chamam ele direto continuam válidos como
  teste da lógica isolada). Reabrir essa decisão sem pedido explícito
  seria reverter algo já resolvido — **não mexido**.
- **`_manual_attack`** — caso diferente: **zero referências em todo o
  repositório**, nem em teste nenhum (só a própria definição), nunca
  mencionado em nenhuma limpeza anterior. Calculava dano local
  (`deal_damage` direto) — se algum dia fosse chamado, violaria
  "cliente nunca calcula gameplay". Confirmado com o usuário e
  **removido** (`ui/systems.py`, ~31 linhas). `PLAYER_ATTACK_RANGE`/
  `deal_damage` continuam em uso por outros métodos do arquivo — só o
  método em si foi removido, nenhuma outra dependência tocada.

Suíte: `test_client_ui.py` — 175 casos, mesma contagem de antes
(confirma que nenhum teste dependia do método removido) — sem
regressão.

## 163. `PROJECT_WIDE_REVIEW.md` — `SessionManager._unstuck_cooldowns` dict mutável no corpo da classe (anti-padrão já proibido pelo CLAUDE.md)

17/09/2026. `server/session.py:1665` tinha `_unstuck_cooldowns: dict =
{}` declarado no CORPO da classe `SessionManager` — atributo de
CLASSE, compartilhado entre todas as instâncias (exatamente o
anti-padrão que o próprio CLAUDE.md do projeto já proíbe
explicitamente, seção "Proibido estado mutável ad-hoc fora do ECS").
Sem bug de compartilhamento hoje (`SessionManager` é singleton por
processo), mas violava a convenção documentada e vazava memória
lentamente — nunca removia entrada por `session_id`, então todo
session_id que já usou `/unstuck` uma vez ficava no dict PRA SEMPRE,
mesmo muito depois de desconectar.

**Fix**: movido pra atributo de instância (`self._unstuck_cooldowns`
em `__init__`, mesmo bloco de `_dispatch_tasks`/`_autosave_tasks`) —
padrão já usado em todo o resto do `__init__` pra estado por-conexão.
Entrada removida em `on_disconnect` (fecha o vazamento de memória
também, não só a violação de convenção).

**Teste**: nenhum teste existente cobria o cooldown em si (só a troca
de mapa que `/unstuck` também faz) — 2 novos em
`tests/test_session.py::TestUnstuck`:
- `test_unstuck_dentro_do_cooldown_recusa_com_erro` — 2º `/unstuck`
  dentro de 60s recebe `ERROR{reason:"unstuck_cooldown:N"}`, não
  teleporta de novo.
- `test_disconnect_limpa_cooldown_de_unstuck` — prova que
  `on_disconnect` remove a entrada (fechamento do vazamento).

Revert-to-confirm: `git stash` só de `server/session.py` — o teste de
cooldown em si continuou passando (bug não era funcional, é
estrutural), mas o teste de limpeza no disconnect falhou exatamente
como esperado (`'unstuck_d' unexpectedly found in {...}`).

Suíte: `test_session.py` completo — 102 casos, sem regressão.

## 164. `PROJECT_WIDE_REVIEW.md` — rate-limit em mensagens C→S (nenhuma mensagem tinha limite antes)

17/09/2026. `SessionManager.on_message` (`server/session.py`) é o
único chokepoint de TODA mensagem que o cliente manda — não tinha
limite de taxa nenhum. Isso amplificava qualquer exploit de gameplay
em loop apertado (os 4 já corrigidos, §151-§155, mais qualquer futuro)
e deixava `LOGIN`/`REGISTER` abertos a brute-force sem custo nenhum
numa única conexão.

**Decisão de desenho, perguntada ao usuário antes de implementar**
(3 opções: limite geral + limite extra em login, só limite geral, ou
rastrear IP primeiro) — escolhida a primeira: limite geral por sessão
cobre qualquer flood/DoS de gameplay, e `LOGIN`/`REGISTER` ganham um
segundo limite bem mais apertado. Rastreio de IP ficou fora de escopo
(servidor não tem essa infra hoje; não impede 100% brute-force
multi-conexão, mas encarece muito um ataque numa conexão só).

**Mecanismo**: `_TokenBucket` (`server/session.py`, classe pequena com
`__slots__`, O(1) por chamada, 4 floats por sessão — nada perto de
justificar estrutura mais pesada, mesmo critério de "caminho quente"
do CLAUDE.md). Cada `Session` ganha 2 baldes:
- `_msg_bucket` — geral, rajada de 30, sustenta ~20/s (folgado pra
  qualquer sequência real de jogo — movimento/auto-attack/skill a 30
  ticks/s do servidor nunca chega perto disso por conexão). Estouro =
  **dropa em silêncio** (não desconecta, não avisa — um cliente
  legítimo nunca deveria bater esse teto; avisar só ajudaria um
  atacante a calibrar o ataque).
- `_auth_bucket` — só `LOGIN`/`REGISTER`, rajada de 5, sustenta 1 a
  cada 12s. Estouro = manda `LOGIN_ERROR`/`REGISTER_ERROR`
  `{reason:"rate_limited"}` (o MESMO tipo de resposta que cada handler
  já usa pros outros motivos de falha — nunca o `MsgType.ERROR`
  genérico, que nenhum handler client-side escuta hoje).

Checado em `on_message`, ANTES de decodificar/validar payload ou
chamar o handler — barato, cobre qualquer flood sem gastar trabalho
extra.

**Testes**:
- `TestTokenBucket` (`tests/test_session.py`) — 3 casos isolados da
  classe `_TokenBucket` (consome até a capacidade e bloqueia, reabastece
  com o tempo, nunca reabastece além da capacidade) — manipula
  `last_ts` direto em vez de mockar `time.monotonic`, determinístico.
- `TestRateLimit` — 3 casos de integração via `on_message` real: flood
  geral dropado em silêncio, mensagem normal (balde cheio) não afetada,
  `LOGIN` com balde de auth esgotado recebe `LOGIN_ERROR{reason:
  rate_limited}` e **nem chega a validar a senha** (prova que o corte
  acontece ANTES do handler, não só que a resposta é diferente).

Revert-to-confirm: `git stash` só de `server/session.py` — 5 dos 6
testes novos falharam (`AttributeError: 'Session' object has no
attribute '_msg_bucket'`/`'_auth_bucket'`), o 6º (mensagem normal,
balde cheio) continuou passando como esperado (não depende do
mecanismo novo pra passar).

Suíte: `test_session.py` completo (108 casos) + todo arquivo de teste
que chama `on_message` diretamente (`test_bg_leave_message_order.py`,
`test_character_names.py`, `test_craft_recycle_protocol.py`,
`test_equip_item_protocol.py`, `test_hotbar_bg_leak.py`,
`test_loot_authoritative.py`, `test_protocol_validation.py`,
`test_trade_quantity_slice.py`) — 255 casos no total, sem regressão
(confirma que a rajada de 30 nunca interfere com sequências normais de
teste, que fazem no máximo poucas chamadas por caso).

## 165. `PROJECT_WIDE_REVIEW.md` — `except Exception: pass` morto em 22 call sites de `remove_component`/`remove_entity`

17/09/2026. `engine/world.py::World.remove_component`/`remove_entity`
já toleram alvo ausente sozinhos internamente — `remove_entity`
(linha 83-90) usa `dict.pop(eid, None)` + checagem `is not None`;
`remove_component` (linha 92-99) usa `dict.get()` + `component_type
in entity_components` antes de deletar. **Nenhum dos dois levanta
exceção pra entidade/componente ausente** — é o caso normal, já
tratado.

Apesar disso, 22 call sites em 8 arquivos de `server/` envolviam essas
chamadas em `try/except Exception: pass` — um guard que NUNCA dispara
pelo motivo pretendido (a checagem "alvo já não existe" já é interna),
só mascara qualquer OUTRO erro real que aconteça ali (import errado,
`TypeError`, componente errado) sem log nenhum, em caminhos críticos
de morte/respawn/expiração de buff (`server_death_handler.py`,
`respawn_system.py`, `combat_processor.py`, `spell_completion_processor.py`,
`world_server.py`, `match_processor.py`, `bg_queue_processor.py`,
`session.py`).

**Fix**: removidos os 22 `try/except Exception: pass` — chamada direta
no lugar, com comentário de 1 linha apontando pra este item (evita que
alguém reintroduza o mesmo guard "defensivo" sem saber que é
desnecessário). Bônus em `server_death_handler.py`: 2 loops que
faziam `from engine.components import PendingDeath` DENTRO da
iteração (1 import por entidade) — import movido pra antes do loop,
já que a limpeza tirou o motivo de estar ali dentro (o `try` que
"precisava" do import local pro escopo do bloco).

**Fora de escopo, deliberado**: outros `except Exception: pass`/
similares no mesmo grep (`server/world_server.py:713`,
`server/auth.py`, `server/main.py:167`, etc.) NÃO foram tocados — são
guards de propósito diferente (ex: inicialização opcional de
profiler, observabilidade que "nunca pode derrubar o tick por causa
dela", parsing de dado externo) onde a exceção É esperada de verdade.
O achado da auditoria era especificamente sobre `remove_component`/
`remove_entity`, que é o único caso onde o guard é comprovadamente
inútil pelo motivo pretendido.

Suíte completa (`pytest tests/ -q`): 1692 de 1693 passaram. A 1 falha
(`test_server.py::TestTickPerfProfiler::
test_perf_push_pop_grava_no_caminho`) é flakiness pré-existente de um
teste baseado em `time.sleep(0.01)` + comparação `>= 0.01` (granularidade
de timer do SO, falha ~30-50% das vezes mesmo sem nenhuma mudança no
código que ele testa) — confirmado rodando isolado 4x (2 falhas, 2
sucessos); esse teste nunca toca `remove_component`/`remove_entity`
nem nenhum dos 8 arquivos tocados aqui.

## 166. `PROJECT_WIDE_REVIEW.md` — `except Exception: pass` real (não morto) em `_restore_save_state`, mascarava falha de talento no login sem log nenhum

17/09/2026. Diferente do §165 (guard morto — o método já toleravam o
"erro" internamente): `client/save_sync_handlers.py::
_restore_save_state` engolia QUALQUER exceção de
`self._talent_system.apply_talent_effects()` de verdade — se essa
chamada falhasse (dado de save corrompido, talento não catalogado,
qualquer bug futuro), o personagem entrava sem NENHUM efeito de
talento aplicado, e ninguém — nem o usuário, nem um log — ficava
sabendo. Pior classe de silêncio possível: nem sintoma óbvio (o jogo
"funciona", só com stats levemente errados) nem rastro pra investigar
depois.

**Fix**: mantida a captura (dado de save vem de fora, não é controlado
por nós — remover o guard aqui seria trocar "silencioso" por "trava o
login inteiro"), mas agora loga via `print(f"[Client]
apply_talent_effects falhou no load do save: {exc!r}")` — mesma
convenção já usada em `client/network_handlers.py` (`"[Client] login
erro: ..."`), cliente não tem módulo de log dedicado como o servidor.

**Teste** (`tests/test_restore_save_state_talent_error.py`, novo
arquivo): fixture mistura `NetworkHandlers`+`SaveSyncHandlers`+
`InventoryHandlers` (mesmo padrão de `test_restore_attack_interval.py`)
com um `_talent_system` stub que sempre levanta `ValueError` —
`test_excecao_no_apply_talent_effects_nao_propaga_e_e_logada` prova
que a chamada não propaga (não trava o resto do restore) E que a
mensagem de erro real aparece no log (`contextlib.redirect_stdout`),
não só um aviso genérico. Revert-to-confirm: `git stash` só de
`client/save_sync_handlers.py` reproduziu exatamente `'[Client]' not
found in ''` — nenhum log saía antes do fix.

Suíte: `test_client_ui.py`, `test_restore_attack_interval.py`,
`test_restore_save_state_talent_error.py`,
`test_client_instance_sync.py` — 200 casos, sem regressão.

## 167. `PROJECT_WIDE_REVIEW.md` — `QuestSystem._fire_reach_tile`/`_sync_collect_progress` código morto (caminho offline sem chamador)

17/09/2026. `ui/quest_system.py::QuestSystem.update()` documenta desde
sempre (linha 65-68 do `__init__`, "Online: servidor é o único
produtor autoritativo de progresso... None/falsy = caminho offline,
inalterado") que o progresso de quest LOCAL é resquício do jogo
offline, superado pelo servidor autoritativo. `_fire_reach_tile`
(disparava evento `reach_tile` local) e `_sync_collect_progress`
(sincronizava objetivo `collect_item` local contra o Inventory) nunca
tinham NENHUM chamador — nem em `update()`, nem em nenhum outro
método da classe, nem em teste (o único hit de busca pelo nome
`_sync_collect_progress` era um teste de `engine.quest_logic.
sync_collect_progress`, função de MÓDULO server-authoritative
completamente diferente, coincidência de nome).

Diferença importante de `_try_start`/`_complete_quest` (mesmo arquivo,
também documentados "Caminho OFFLINE"): esses DOIS continuam tendo
chamador de verdade dentro da própria classe (`update()`, gated por
`if not self._net`) — só ficam inertes em RUNTIME (online sempre seta
`_net`), não estaticamente inalcançáveis. Não foram tocados — fora de
escopo deste achado específico, que é só sobre os 2 métodos SEM
chamador nenhum.

**Fix**: removidos `_fire_reach_tile`/`_sync_collect_progress`
(`ui/quest_system.py`) + o campo `_last_reach_tile` que só existia pra
alimentar o método removido (`__init__` e o reset em
`set_current_map`, que continua chamado de verdade por `game.py` em
toda troca de mapa — só a linha do campo morto foi limpa, o método em
si permanece).

Suíte: `test_client_ui.py` (175) + todos os 8 arquivos de teste de
quest (`test_npc_interaction_request.py`, `test_quest_logic.py`,
`test_quest_map_tracking.py`, `test_quest_reward_ui.py`,
`test_quest_split_npc.py`, `test_quest_talk_completable_priority.py`,
`test_quest_tracker.py`, `test_quest_turn_in.py`) — 78 casos, sem
regressão.

## 168. `PROJECT_WIDE_REVIEW.md` — ~9 funções confirmadas mortas por grep no repo inteiro, removidas

17/09/2026. Lista fechada da auditoria, cada uma confirmada por grep
no repo inteiro (zero call site fora da própria definição) antes de
remover:

- **`Equipment.get_modifier_total()`** (`engine/components.py`) — sem
  chamador, e além de morta violava "Componentes são dados, não
  lógica" (lógica de soma de modifiers dentro de um Component).
- **`create_tree()`** (`engine/entity_factory.py`) — placeholder de
  árvore como retângulo colorido, de antes do sistema de tileset/
  objetos real existir. `TREE_COLOR`/`TREE_SIZE` (só usados por ela)
  removidos junto.
- **`WorldServer._create_spawn_zones()`** (`server/world_server.py`) —
  wrapper "Compat: cria SpawnZones sem MapLocation (usado antes do
  multi-map)" — o único chamador real (`spawn_player`) já usa
  `_create_spawn_zones_for_map` direto, nunca passou pelo wrapper.
- **5 funções factory em `shared/messages.py`** (`make_entity_spawn`,
  `make_combat_result`, `make_stats_update`, `make_aoi_update`,
  `make_projectile_spawn`) — o próprio comentário acima delas já
  admitia "reservadas para quando o cliente for implementado" — o
  cliente existe desde muito antes desta sessão, servidor sempre
  construiu os dicts inline.
- **`run_creation()`** (`ui/char_creation_screen.py`) — wrapper
  público de `_run_creation` (a função real, com `net` opcional, que
  todo chamador real usa direto) sem NENHUM chamador.
- **3 métodos de `SOUNDS`** (`ui/sound_manager.py`): `play_at`
  (a família `_at` real — `play_random_at`/`play_skill_at`/
  `play_mob_sounds_at`/`play_emote_at` — nunca passa por ele),
  `crossfade_ambient` (a máquina de fade em `update()`/
  `apply_music_settings()` continua viva e correta, só nunca é
  ACIONADA por ninguém — estado `_amb_target`/`_fade_dur`/`_fade_t`
  deixado como está, ainda lido/escrito por outros métodos vivos),
  `play_mob_event` (já vinha marcado `DEPRECATED` no próprio código,
  "Mantido apenas para compatibilidade com código legado eventual" —
  esse código legado nunca existiu de fato).
- **`SpellCast.is_targeting`** (`ui/spell_system.py`) — property sem
  leitor em lugar nenhum.

Achado incidental durante a busca (não corrigido, fora de escopo):
existe um diretório `.claude/worktrees/agent-a66679df9a2c67af2/` com
uma cópia antiga de `server/world_server.py` de uma sessão de agente
anterior isolada — não faz parte do código real, não foi tocado.

Suíte completa (`pytest tests/ -q`): 1693 de 1694 passaram. A 1 falha
(`test_perf_log_improvements.py::TestPerfPushPopAndPercentiles::
test_perf_push_pop_grava_amostra_sob_caminho_completo`) é a MESMA
classe de flakiness já registrada no §165 (`time.sleep(0.01)` +
`assertGreaterEqual(..., 0.01)`, granularidade de timer do SO) — outro
arquivo de teste do mesmo mecanismo `_perf_push`/`_perf_pop`, nenhuma
relação com as remoções desta entrada.

## 169. `PROJECT_WIDE_REVIEW.md` — `content/item_table.py::_make_quiver`/`_make_ammo` removidos (perguntado ao usuário antes, per achado da auditoria)

17/09/2026. A auditoria já sinalizou explicitamente que este achado
precisava de decisão do usuário antes de qualquer ação ("pode ser
lacuna real de conteúdo, não só lixo — vale perguntar antes de
apagar") — diferente dos outros itens de código morto desta sessão,
que eram remoções óbvias.

Investigação: `_make_quiver`/`_make_ammo` são factories genéricas de
aljava/munição, mas TODAS as entradas reais do catálogo
(`basic_quiver`, `sturdy_quiver`, `battleground_quiver`, `arrow`,
`arrow_broadhead`) constroem `Item(...)` inline, ignorando os
helpers. O comentário logo abaixo delas ("Itens — antigo
`loot_tables._T`, 106 entradas, intocadas") sugere que os helpers
foram criados numa tentativa de padronizar a criação de aljava/
munição, mas a migração das entradas reais pra usá-los nunca
aconteceu.

Perguntado ao usuário: remover, refatorar os itens existentes pra usar
os helpers, ou deixar como está. Escolhido remover — as aljavas/
munições reais já funcionam corretamente com `Item(...)` inline (e
essa MESMA sessão acabou de investigar e corrigir bugs reais nelas,
§154-§160 — refatorar agora seria tocar em código recém-estabilizado
sem ganho funcional nenhum, só estilo).

Suíte: `pytest tests/ -k "item or quiver or ammo"` — 139 casos, sem
regressão.

## 170. `PROJECT_WIDE_REVIEW.md` — `MobRespawnSystem` (violação "System nunca chama System") era código morto sem nenhum chamador — removido por completo

17/09/2026. A auditoria descreveu isto como violação arquitetural
("Systema recebendo outro System inteiro como parâmetro de construtor,
lendo/escrevendo uma lista interna dele direto") — `engine/
world_systems.py::MobRespawnSystem.__init__(self, world,
death_handler: DeathHandlerSystem)`, lê/escreve
`death_handler.pending_respawns` direto.

Investigação achou algo mais fundamental: **`MobRespawnSystem` nunca
é instanciado em lugar nenhum do repositório** — nem em `game.py`
(cliente), nem em `server/world_server.py` (servidor). O único outro
hit de busca era um re-export morto em `ui/systems.py` ("import
tudo de world_systems pra compatibilidade") e a própria definição da
classe. Zero teste também.

**Por que o respawn de mob de verdade funciona sem ela**: o mecanismo
REAL e vivo é `SpawnZoneSystem` (mesmo arquivo, `server/world_server.py:886`
instancia de verdade) — zona-based (conta mobs vivos na zona, respawna
quando cai abaixo do alvo E o cooldown expirou), NUNCA passa por
`pending_respawns`/fila de timer. `DeathHandlerSystem` (que
`MobRespawnSystem` dependia) já tinha sido confirmado amplamente
inerte em modo online no §160 desta mesma auditoria — `pending_respawns`
nunca era populado de verdade nesse caminho.

**Fix**: `MobRespawnSystem` removida por completo (classe inteira,
`engine/world_systems.py`) + o re-export morto em `ui/systems.py`.
Resolve a violação arquitetural removendo o violador, não
reformulando uma comunicação que nunca acontecia de verdade.

Suíte: `test_client_ui.py` (175) + `pytest tests/ -k "respawn or
spawn_zone or jungle"` (45) — sem regressão (confirma que o respawn
real, via `SpawnZoneSystem`, continua intocado).

## 171. `PROJECT_WIDE_REVIEW.md` — `DeathHandlerSystem` chamava `QuestSystem` via referência global — mecanismo inteiro era caminho OFFLINE morto, removido

17/09/2026. Achado da auditoria: `engine/world_systems.py:1104-1106`
(dentro de `DeathHandlerSystem.update()`) consultava
`quest_events._quest_system_ref.get_conditional_loot(...)` — uma
referência GLOBAL de módulo pro `QuestSystem` (UI), setada por
`game.py::set_quest_system()`. Mesma violação do §170 (System
acoplado a outro System/UI), só que via global de módulo em vez de
import direto — o comentário no próprio código admitia que o global
foi escolhido especificamente pra *evitar* o import direto, mas isso
só esconde o acoplamento de uma busca textual por `import ui`, não o
remove de verdade.

Confirmado pela MESMA razão do §170 — `DeathHandlerSystem.update()`
nunca roda de verdade online (nenhuma entidade chega com
`PendingDeath` no mundo do cliente) — e `get_conditional_loot()`
(`ui/quest_system.py`) tinha docstring própria confirmando: "Caminho
OFFLINE — online, o drop condicional é rolado no servidor
(`server/server_death_handler.py`) contra o QuestLog autoritativo."
Ou seja: a via online de verdade (`server/loot_processor.py::
roll_conditional_loot`) sempre existiu em paralelo, nunca passou por
este mecanismo — ele existia só pro caminho offline, que não existe
mais.

**Fix**: removida a cadeia inteira, não só o ponto de leitura —
`_quest_system_ref`/`set_quest_system()` (`engine/quest_events.py`),
a chamada em `game.py` (+ import), o read site em
`DeathHandlerSystem.update()` (+ `import engine.quest_events as
quest_events`, que ficou sem nenhum outro uso no arquivo), e
`QuestSystem.get_conditional_loot()` (`ui/quest_system.py`) — método
com 1 único chamador, o que acabou de ser removido.
`engine.quest_logic.roll_conditional_loot()` (a função de verdade,
usada pelo caminho online real) foi mantida intocada.

Suíte: `test_client_ui.py` (175) + os 8 arquivos de teste de quest
(78 casos) — sem regressão.

## 172. `PROJECT_WIDE_REVIEW.md` — `_last_mob_attacker` via `hasattr()` ad-hoc — movido pra `WorldServer.__init__`

17/09/2026. Último item de arquitetura da auditoria.
`server/combat_processor.py` (mixin de `WorldServer`, sem `__init__`
próprio) tinha `_last_mob_attacker` (cache de fallback pro attacker
mob→player dentro do mesmo tick) criado via `if not hasattr(self,
"_last_mob_attacker"): self._last_mob_attacker = {}`, repetido em 4
pontos do mesmo arquivo — exatamente o anti-padrão que o CLAUDE.md já
proíbe explicitamente ("nunca anexar cache/estado via
`hasattr(func, "_x")`/atributo de função").

**Fix**: `self._last_mob_attacker: dict[int, int] = {}` declarado uma
vez em `WorldServer.__init__` (`server/world_server.py`), ao lado de
`_mob_damage_log` (mesmo formato/uso — estado de combate por-instância
do servidor). Os 4 sites em `combat_processor.py` viraram acesso
direto ao dict, sem guard nenhum.

Suíte: `pytest tests/ -k "combat or tower or minion or mob_attack"` —
224 casos, sem regressão.

---

**Toda a seção 🟡 Arquitetura de `PROJECT_WIDE_REVIEW.md` está
concluída** (§170/§171/§172).

## 173. `PROJECT_WIDE_REVIEW.md` — `_cheby()` duplicado (convergência de 2 agentes) — vira wrapper fino, mesmo padrão já aceito em `ui/systems.py`/`ui/quest_system.py`

17/09/2026. Último item pendente da auditoria (achado por 2 agentes
independentes de forma convergente). `ui/trainer_system.py:127-129` e
`ui/crafting_system.py:163-165` reimplementavam a fórmula de Chebyshev
na mão (`max(abs(dx), abs(dy))`) em vez de delegar pra
`engine.utils.chebyshev` — risco real de divergência silenciosa se a
métrica um dia mudar (mesma classe de bug já registrada em §134/§135,
onde `chebyshev()` vs `in_range()` foi escolhido errado em vários
lugares).

`ui/systems.py`/`ui/quest_system.py` já tinham o MESMO método
`_cheby(a, b)` (assinatura de 2 tuplas em vez dos 4 escalares de
`chebyshev`), mas como wrapper FINO que já delega — CLAUDE.md's
próprio "Convergência entre agentes" já registrava esse padrão como
aceitável (não conta como duplicação de risco, só de assinatura).

**Fix**: os 2 métodos em `trainer_system.py`/`crafting_system.py`
viraram o mesmo wrapper fino (`return chebyshev(a[0], a[1], b[0],
b[1])`), import de `chebyshev` consolidado no `from engine.utils
import ...` que cada arquivo já tinha (evita import duplicado/local
dentro do método).

Suíte: `test_client_ui.py` (175) + `pytest tests/ -k "trainer or
crafting or blacksmith"` (15) — sem regressão.

---

**`PROJECT_WIDE_REVIEW.md` (auditoria de 16/09/2026) está 100%
concluída** — todos os achados de Segurança/Economia, Qualidade de
Código, Arquitetura e Convergência foram implementados ou
explicitamente revisitados com decisão do usuário (§151-§173). Só
restam os 14 itens da auditoria ANTERIOR (client-side, principalmente
performance/estrutura de `ui/` — `ui/systems.py` monolítico, cache de
ícone sem teto, fila de mob com `list.pop(0)`, etc.), listados no
próprio `PROJECT_WIDE_REVIEW.md`, seção "Veredito consolidado dos 15
itens pendentes".

## 174. Recorrência do bug de cooldown de skill "sumindo" dentro da BG (§34.74.23) — causa raiz DIFERENTE desta vez: `apply_talent_effects()` recriava skill de talento do zero em toda chamada

17/09/2026. Usuário relatou: "há um tempo já relatei o mesmo bug, foi
resolvido mas voltou" — cooldown de Tiro Múltiplo (arqueiro) dentro da
BG só aparecia na barra depois de apertar o atalho de novo (já em
cooldown), sumindo de novo pouco depois. Sintoma idêntico ao §34.74.23
(03/08/2026, histórico) — mas aquele fix (reconciliação por-slot em
`client/network_handlers.py`, bloco `skills_hotbar`) continua intacto
e correto; a recorrência vem de um mecanismo DIFERENTE que nasceu
depois (14/09/2026, §136 — `talent_allocated` passou a chamar
`apply_talent_effects()`).

**Investigação**: em vez de adivinhar, instrumentado com `print()`
temporário em 3 pontos (CAST em `ui/systems.py`, e
CREATE/REMOVE/SKILL_RESULT em `client/network_handlers.py`) — cliente
rodado localmente pelo Claude enquanto o usuário jogava na BG de
produção. Log real mostrou: cada `CAST sid=tiro_multiplo` tinha um
`id(skill)` DIFERENTE do anterior, sem NENHUM `CREATE` correspondente
no bloco `skills_hotbar` — provando que o objeto estava sendo trocado
por um caminho que a instrumentação não cobria.

**Causa raiz real**: `ui/talent_system.py::TalentSystem.
apply_talent_effects()` — a cada chamada, remove INCONDICIONALMENTE
toda skill com `talent_id` setado (`skills.skills = [None if
getattr(s, "talent_id", None) else s ...]`) e limpa
`tt._unlocked_skill_ids`, depois recria do zero via `PlayerSkills.
_make_skill()` qualquer talento com pontos suficientes — mesmo que a
alocação não tenha mudado NADA desde a última chamada. Tiro Múltiplo
é desbloqueada pelo talento `bardo_tiro_multiplo`
(`content/talent_data.py`). Essa função roda de novo toda vez que
`talent_allocated` chega via STATS_UPDATE — e dentro da BG isso é em
**todo ganho de XP** (`server/instance_progression.py::
_push_stats_update`, "empurra STATS_UPDATE em TODO ganho" desde
13/08/2026), não só quando o jogador aloca um ponto de verdade. Uma
skill de cooldown curto (Picada de Escorpião, 5s) quase nunca é pega
no meio da janela de forma perceptível; uma de cooldown longo (Tiro
Múltiplo, 90s) tem uma janela enorme — daí o padrão "cooldown alto"
relatado pelo usuário.

**Fix**: `apply_talent_effects()` agora guarda o OBJETO (não só a
posição) de cada skill de talento antes de removê-las
(`old_skills_by_id: dict[str, object]`), e ao re-conceder uma skill já
desbloqueada, REUSA o objeto existente em vez de chamar `_make_skill`
de novo — só cria um objeto genuinamente novo quando a skill nunca
tinha sido concedida antes. Preserva `current_cooldown`/`charges` ao
vivo entre chamadas repetidas com a mesma alocação — mesmo princípio
já usado (e correto) no bloco `skills_hotbar`.

**Teste** (`tests/test_talent_skill_cooldown_preserved.py`, novo):
`test_reaplicar_mesma_alocacao_preserva_objeto_e_cooldown_da_skill` —
usa `TalentSystem` REAL (não mock, mesma fixture de
`test_instance_talent_effects_sync.py`), desbloqueia Tiro Múltiplo,
simula `current_cooldown=90.0` (como um SKILL_RESULT faria), reenvia a
MESMA `talent_allocated` (simulando outro ganho de XP na BG), confirma
que o objeto Skill e o cooldown sobrevivem. Revert-to-confirm: `git
stash` só de `ui/talent_system.py` reproduziu exatamente a troca de
`id()` do objeto.

Suíte: `test_client_ui.py`, `test_instance_talent_effects_sync.py`,
`test_talent_skill_cooldown_preserved.py`,
`test_restore_save_state_talent_error.py`, `test_hotbar_bg_leak.py`,
`test_client_instance_sync.py` — 226 casos, sem regressão.

**Lição pra próxima vez que um "bug já resolvido" reaparecer**: não
assumir que é o MESMO mecanismo do fix antigo — o sintoma bateu 100%,
mas a causa raiz era um código totalmente diferente, escrito 6
semanas DEPOIS do fix original, que reintroduziu a mesma CLASSE de bug
(objeto de estado vivo descartado/recriado sem necessidade) por um
caminho novo.

## 175. Backlog dos 14 itens antigos, item #1 — painéis de NPC fechados por proximidade duplicado em `game.py`

17/09/2026. Primeiro item da fila priorizada dos 14 itens da auditoria
ANTERIOR (client-side) ainda pendentes. `game.py::
_close_modals_if_too_far` (fecha painel de NPC individualmente quando
o player se afasta) e `_close_all_modals` (fecha TUDO
incondicionalmente) tinham cada um sua PRÓPRIA lista de `if` por
painel — Loot/Shop/Crafting/Trainer/QuestDialog — mesmo painel, mesmo
`_close()`, escrito 2 vezes. Risco real (não só estilo): um painel de
NPC novo adicionado só num dos dois métodos nunca fecharia pelo outro
gatilho — bug silencioso, só perceptível em jogo.

**Fix**: `GameEngine._npc_proximity_panels()` (novo método) — fonte
única, retorna 5 tuplas `(is_open_fn, npc_id_fn, close_fn)`, uma por
painel. Loot é o único caso especial (sem atributo `is_open` — "está
aberto" é `open_corpse_id != -1`), resolvido com o mesmo formato de
tupla (lambda em vez de atributo direto) — nenhuma lógica condicional
extra no `for` de quem consome. `_close_modals_if_too_far` e
`_close_all_modals` passaram a iterar essa MESMA lista — um painel
novo só precisa entrar ali pra funcionar nos dois gatilhos.
`self._quest_dialog._pending_npc_id = -1` (estado específico do
QuestDialog, não relacionado ao "está aberto") ficou fora do loop
compartilhado, preservado como estava.

**Teste** (`tests/test_npc_proximity_panels_close.py`, novo — não
existia teste NENHUM pros 2 métodos reais antes disso, só um stub
leve num arquivo de teste não relacionado): usa os métodos REAIS de
`game.GameEngine` "emprestados" pra uma fixture leve (mesmo padrão já
usado no projeto pra testar `GameEngine` sem instanciar a classe
inteira — `test_client_ui.py`, `_GE_pvp`/`_GE_rsr`/`_GE_mm`). 6 casos:
fecha por distância, não fecha se perto, caso especial do Loot
(`open_corpse_id`), painel já fechado não quebra nada, `_close_all_modals`
fecha tudo incondicionalmente mesmo adjacente, e um teste que prova a
motivação real do fix (mesmo painel fecha pelos DOIS gatilhos, sem
precisar tocar em 2 lugares). Revert-to-confirm: `git stash` só de
`game.py` — `AttributeError: type object 'GameEngine' has no
attribute '_npc_proximity_panels'` na coleta dos testes, exatamente
como esperado.

Suíte: `test_client_ui.py` (175) + `test_npc_proximity_panels_close.py`
(6) + `test_npc_interaction_request.py` (3) + `pytest tests/ -k
"trainer or crafting or shop or loot or quest_dialog or blacksmith"`
(96) — sem regressão.

## 176. Backlog dos 14 itens antigos, itens #11/#13/#14 — imports locais redundantes em `ui/systems.py`

17/09/2026. Três itens triviais/risco zero da fila priorizada,
atacados juntos (mesma classe de achado — import que já existe no
topo do arquivo, reimportado dentro de função/método sem necessidade):

- **Item #11 — `RemoteControlled` reimportado localmente 6x**
  (linhas 400/466/566/1333/1427/4808 antes desta correção), cada uma
  com um alias descartável (`_RC`/`_RCtab`/`_RCtp`/`_RCfl`/`_RCspace`/
  `_RCcast`) — o nome já está disponível desde o import de módulo
  (linha 35). Substituído por uso direto do nome já importado nos 6
  pontos. Confirmado que os OUTROS 5 arquivos que também reimportam
  `RemoteControlled` localmente (`client/hotbar_handlers.py`,
  `network_handlers.py`, `party_handlers.py`,
  `remote_entity_handlers.py` ×2) **não têm** import de módulo — lá o
  import local é necessário, não tocado.
- **Item #13 — imports mortos de loot/spawn offline** —
  `from content.loot_tables import roll_loot, roll_mob_loot,
  roll_coins` e `from engine.entity_factory import create_corpse,
  create_enemy` (linhas 58-59) nunca eram usados em `ui/systems.py`
  (confirmado por grep — zero call site no arquivo inteiro). Resquício
  do código offline que um dia viveu aqui, nunca limpo quando migrou/
  foi removido. Ambas as linhas removidas.
- **Item #14 — `TILE_SPRITES`/`is_target_alive` reimportados
  localmente** — `TILE_SPRITES` (`ui/tile_sprite_manager.py`) tinha
  6 imports locais espalhados pelo arquivo (2 em funções de módulo,
  1 em `TileRenderSystem.__init__` — que cacheia como
  `self._tile_sprites`, mantido — e 3 dentro do método gigante
  `RenderSystem.render()`); `is_target_alive`
  (`engine/utils.py`) tinha 2. Nenhum import de módulo pra nenhum dos
  dois. Confirmado sem risco de import circular
  (`ui/tile_sprite_manager.py` não importa nada de `ui/systems.py`).
  Ambos hoisted pro topo do arquivo (mesmo padrão já usado nele mesmo
  pra outros imports, 14/09/2026, `CLIENT_PERFORMANCE_REVIEW.md` item
  2 — "estavam sendo reimportados... no loop de desenho mais quente
  do cliente"), 8 imports locais removidos no total.

Nenhuma mudança de comportamento — só remoção de import redundante e
uso do nome já disponível no escopo do módulo.

Suíte: `test_client_ui.py` (175) + `pytest tests/ -k "render or target
or tile_sprite or corpse or click or space_engage"` (158) — sem
regressão.

## 177. Backlog dos 14 itens antigos, item #6 — fila de movimento de mob/player remoto com `list.pop(0)` em vez de `deque`

17/09/2026. `game.py` declarava `self._mob_move_queues`/
`self._remote_player_move_queues` (dict de listas) e
`self._self_move_queue` (lista) — consumidas em
`client/remote_entity_handlers.py::_process_mob_move_queues` via
`queue.pop(0)`. `pop(0)` numa `list` é O(n) (desloca todo o resto pra
esquerda) — sem bug de comportamento (ordem FIFO sempre foi respeitada
certo), só custo desnecessário que cresce com o tamanho da fila (lag
spike enfileirando vários passos de uma vez pro mesmo mob/player).

**Fix**: as 3 estruturas viraram `collections.deque`
(`self._mob_move_queues: dict[int, deque] = {}`, idem
`_remote_player_move_queues`, `self._self_move_queue: deque = deque()`,
em `game.py`); os 2 pontos de enfileiramento
(`_move_remote_mob`/análogo de player remoto, em
`client/remote_entity_handlers.py`) trocaram
`.setdefault(eid, [])` por `.setdefault(eid, deque())`; os 3 pontos de
drenagem em `_process_mob_move_queues` trocaram `.pop(0)` por
`.popleft()` (O(1)). Confirmado por grep que toda outra operação feita
nessas filas em `client/network_handlers.py` (`.clear()`, `queue[-1]`,
`.append()`, `.get()`/`.pop(key, None)` no dict) já é compatível com
`deque` sem mudança nenhuma.

**Teste** (`tests/test_remote_move_queue_deque.py`, novo): usa
`RemoteEntityHandlers._move_remote_mob`/`_process_mob_move_queues`
REAIS "emprestados" pra uma fixture leve (mesmo padrão de
`_GE_pvp`/`_GE_rsr`/`_GE_mm` em `test_client_ui.py`), com `World` real
e componentes `Position`/`TileMovement` reais — nunca mock do próprio
mecanismo de fila. 3 casos: fila de mob criada via `setdefault` é
`deque` (não `list`); `_self_move_queue` é `deque`; drenar 4 passos
enfileirados numa lag spike sai na MESMA ordem FIFO que entrou, ponta a
ponta via `_process_mob_move_queues` real (1º passo inicia o tween na
hora — mob estava parado — os 3 seguintes vão pra fila porque o mob já
estava em movimento). Revert-to-confirm: `git stash` de `game.py` +
`client/remote_entity_handlers.py` reproduziu exatamente
`AssertionError: [...] is not an instance of <class
'collections.deque'>` no teste que checa o tipo — o teste de ordem FIFO
continua passando mesmo sem o fix (esperado: nunca houve bug de
comportamento, só de custo).

Suíte: `test_remote_move_queue_deque.py` (3) + `test_client_ui.py` +
`test_interceptar_no_early_auto_attack.py` (184 no total) — sem
regressão.

## 178. Backlog dos 14 itens antigos, item #7 — reticle/mira do Tiro Múltiplo recriava Surface rotacionada todo frame

17/09/2026. `SpellCastSystem.render()` (`ui/spell_system.py`, mira
direcional do Tiro Múltiplo) chamava `pygame.transform.rotate(img,
pygame_rot)` com o ângulo BRUTO (player→mouse) todo frame — mouse se
move a cada frame na prática, então uma Surface rotacionada nova era
alocada 60x/segundo enquanto o jogador mirava a skill. Sem bug de
comportamento (mira sempre apontou certo), só custo de CPU/alocação
desnecessário.

**Fix**: ângulo quantizado em buckets de 5°
(`SpellCastSystem._TIRO_AIM_ANGLE_BUCKET_DEG`), mesmo padrão já usado
pra alpha em `ui/floating_text.py::DashTrailManager.render()`
(quantização pra reduzir cardinalidade de cache, não pra mudar
resultado visual — diferença de até 2.5° é imperceptível). Cache
`self._tiro_aim_rot_cache: dict[int, Surface]`, chave = ângulo
quantizado (0-355, múltiplo de 5) — espaço de chaves NATURALMENTE
limitado a 72 entradas (360/5), diferente de `_outline_cache` (que
precisa de teto explícito porque a chave não tem limite natural). O
ângulo QUANTIZADO (não o bruto) é reusado também no cálculo de offset
de blit (matriz de rotação) — garante que a Surface cacheada e o
posicionamento calculado sempre correspondem ao mesmo ângulo exato.

**Teste** (`tests/test_tiro_multiplo_reticle_cache.py`, novo): 3 casos
— mouse na mesma posição em 2 frames reusa a MESMA Surface (`assertIs`,
não só `assertEqual`); mover o mouse pra um ângulo bem diferente cria
uma 2ª entrada; girar o mouse em volta do player em passos de 3°
(mais fino que o bucket de 5°) confirma que o cache nunca ultrapassa
72 entradas. Revert-to-confirm: `git stash` de `ui/spell_system.py` →
`AttributeError: 'SpellCastSystem' object has no attribute
'_tiro_aim_rot_cache'` nos 3 testes, exatamente como esperado.

Suíte: `test_tiro_multiplo_reticle_cache.py` (3) + `test_client_ui.py`
(175) + `pytest tests/ -k "tiro_multiplo or spell"` (5) — sem
regressão.

**Follow-up (mesma sessão, achado ao rodar `pytest tests/` completo)**:
`test_angulo_bem_diferente_cria_nova_entrada` passava isolado mas
falhava (`1 != 2`) dentro da suíte inteira — não era regressão do fix,
era o TESTE usando `pygame.mouse.set_pos()`, que depende do tamanho
REAL da janela SDL corrente. Outro módulo de teste, importado antes
deste na mesma sessão de pytest, deixa a janela menor que as
coordenadas usadas aqui — `set_pos` clampa o "mouse" pra dentro dos
limites, igualando silenciosamente 2 posições que deveriam gerar
ângulos diferentes. Corrigido trocando por monkeypatch direto de
`pygame.mouse.get_pos` (padrão já usado em
`tests/test_quest_reward_ui.py`), que não depende de geometria real de
janela nenhuma. Suíte completa re-rodada depois do fix — 1710 casos,
sem falha.

## 179. Backlog dos 14 itens antigos, item #3 — `BuffBar.render()` reconstruía Surface + fonte todo frame

17/09/2026. `BuffBar.render()` (`ui/buff_bar.py`) chamava
`_build_effects_row()` (`ui/systems.py`) TODO frame pra montar a fila
de ícones de buff — essa função aloca uma `Surface` nova e chama
`font.render()` (glifo de texto) pra CADA buff, mesmo quando nada
mudou visualmente. A contagem regressiva só muda 1x por segundo
(`max(1, ceil(duration))`), não 60x — cada buff ativo custava uma
Surface + N `font.render()` desperdiçados por frame. Sem bug de
comportamento (a fila sempre desenhou certo), só custo.

**Fix**: `BuffBar` ganhou cache de instância
(`_row_cache_key`/`_row_cache_surf`/`_row_cache_rects`) — chave =
`(icon_size, tuple((effect_type, segundo_exibido) para cada buff em
ordem))`. `_dur_bucket(duration)` (novo método estático) espelha
EXATAMENTE a condição de texto de `_build_effects_row()` (`0 < dur <=
99` → `max(1, ceil(dur))`, senão `None`) — precisa bater
exatamente, senão o cache invalida cedo/tarde demais. Mudou a chave
(buff entrou/saiu, ordem mudou, ou o segundo exibido mudou) →
reconstrói via `_build_effects_row()`; senão reusa a MESMA `Surface`/
`rects` do frame anterior. Escopo deliberadamente restrito à `BuffBar`
(só o jogador local, 1 instância) — `_build_effects_row()` é
compartilhada com o nameplate (`ui/systems.py:1924`, N entidades
remotas por frame), que NÃO foi tocado: um cache cross-entidade ali
teria trade-off de memória/invalidação diferente, fora do escopo deste
item específico da auditoria.

**Teste** (`tests/test_buff_bar_row_cache.py`, novo): `World`/
`StatusEffects`/`ActiveEffect` reais (não mock) — 3 casos: mesma
contagem em 2 frames reusa a MESMA `Surface` (`assertIs`); duração
cruzando de 17s pra 16s exibidos invalida o cache; buff novo entrando
na barra invalida o cache mesmo com o 1º buff idêntico. Revert-to-
confirm: `git stash` de `ui/buff_bar.py` → `AttributeError: 'BuffBar'
object has no attribute '_row_cache_surf'` nos 3 testes.

Suíte: `test_buff_bar_row_cache.py` (3) + `test_buff_bar_tooltip.py`
(6) + `test_client_ui.py` (175) — sem regressão.

## 180. Backlog dos 14 itens antigos, item #9 — `FloatingTextManager`/`DashTrailManager`/`ProcTextManager` reconstruíam a lista inteira todo frame

17/09/2026. Último item do lote de 4 (#6/#7/#3/#9) atacados juntos por
decisão do usuário. Os 3 managers de texto/rastro flutuante
(`ui/floating_text.py`) tinham `update(dt)` idêntico: `alive = []; for
e in self._entries: ...; if vivo: alive.append(e); self._entries =
alive` — reconstrói a lista inteira (aloca uma nova) TODO frame, mesmo
quando nada expirou. Sem bug de comportamento (a remoção sempre
funcionou certo), só custo que cresce com o nº de entradas ativas (AoE
com muitos hits simultâneos).

**Fix**: `_prune_expired_inplace(entries, dt, tick_fn)` (novo, função
de módulo) — compactação de 1 passada só, IN PLACE (mesmo objeto
lista, nunca aloca um novo): entradas vivas são copiadas pra trás no
início da própria lista, o resto é truncado no final (`del
entries[write:]`) — preserva ordem relativa dos vivos (importa pro
empilhamento visual de `FloatingTextManager`/`ProcTextManager`, que
itera a lista pra decidir `offset_y` de textos novos). `tick_fn(e, dt)
-> bool` aplica o decremento de tempo de vida específico de cada
manager (só `timer -= dt` pra `DashTrailManager`/`ProcTextManager`;
`timer -= dt` + `offset_y += speed_y * dt` pra `FloatingTextManager`)
e devolve se a entrada continua viva — os 3 `update()` viraram uma
chamada de 4 linhas pro helper compartilhado, em vez de repetir o
mesmo padrão 3x (`_entries`/`_segments`, nomes diferentes por classe,
lógica idêntica).

**Teste** (`tests/test_floating_text_prune_inplace.py`, novo): o
helper em isolamento (remove expirados preservando ordem, mesma
`id()` de lista antes/depois, lista vazia não quebra, todos expirados
esvazia certo) + os 3 managers reais (instâncias próprias, não os
singletons globais `FLT`/`DASH_TRAIL`/`PROC` — evita vazar estado
entre testes) confirmando que continuam expirando/removendo entrada
certo depois da troca pro helper. Revert-to-confirm: `git stash` de
`ui/floating_text.py` → `ImportError: cannot import name
'_prune_expired_inplace'` na coleta do teste.

Suíte: `test_floating_text_prune_inplace.py` (6) + `test_client_ui.py`
(175) + `pytest tests/ -k "floating or dash_trail or proc_text or
interceptar"` (22) — sem regressão.

**Lote de 4 itens (#6/#7/#3/#9) concluído** — todos os 4 items da fila
Tier 3 que o usuário pediu pra atacar juntos ("Pode atacar os 4") estão
implementados, testados e documentados (§177-§180).

## 181. Backlog dos 14 itens antigos, item #10 (Fase 1) — atlas de ícones (skills + itens)

18/09/2026. Item #10 do backlog antigo ("sem atlas/spritesheet pra
ícones") — a auditoria marcava como "só relevante se o catálogo
crescer significativamente". Discutido com o usuário: ele decidiu
seguir mesmo assim, com um objetivo maior que só performance — criar
espaço de crescimento pro catálogo de ícones, incluindo ícones de
TALENTO (que hoje não existem — nó de talento é só retângulo colorido
+ 2 letras iniciais, `ui/talent_system.py::_render_node`), que vão
reaproveitar ícone de skill quando fizer sentido.

**Achado antes de desenhar**: `assets/icons/` tem 161 PNGs, mas só 54
são usados de verdade pelo código (22 `item_*` + 27 `skill_*` + 5
`map_*`) — os outros 107, em subpastas (`Equipment/`, `Food/`,
`Material/`, `Ore & Gem/`, etc.), são um pacote de assets morto, zero
referência em código algum. Não removido (fora do escopo pedido, só
registrado aqui).

**Desenho (aprovado pelo usuário)**: 1 atlas PNG por CATEGORIA (não um
atlas único pra tudo — grade uniforme de células 32×32 é muito mais
simples que empacotar retângulos de tamanho variado), 512×512 = 256
slots cada, bem mais que os 27 skills/22 items de hoje — o resto fica
transparente, espaço pro usuário ir preenchendo aos poucos (skills
novas, itens novos, e futuros ícones dedicados de talento que não
reaproveitem nenhuma skill):
- `assets/icons/atlas_skills.png` (256 slots, hoje 27 ocupados)
- `assets/icons/atlas_items.png` (256 slots, hoje 22 ocupados)

Ícones `map_*` (8×8, categoria/tamanho diferente) ficam de fora,
continuam por arquivo individual — usuário confirmou.

**Mecanismo**: mesmo padrão já usado pra tiles
(`ui/tile_sprite_manager.py::_get_sheet_region`/`SHEET_TILE_MAP`) —
`ui/icon_atlas.py::_IconAtlasSheets.get_region(icon_name)` extrai e
cacheia a sub-Surface (32×32) do slot catalogado; `IconManager._load_raw()`
(`ui/icon_manager.py`, extraído de dentro de `get()`) consulta o atlas
PRIMEIRO, cai pro carregamento por arquivo individual de sempre pra
qualquer ícone ainda não catalogado — fallback incremental, zero risco
pro que ainda não migrou.

Catálogo (`ui/icon_atlas_catalog.py::ICON_ATLAS`, dict nome→(arquivo,
col, row)) é gerado/mesclado por `tools/build_icon_atlas.py` (novo,
CLI manual, roda com Pillow — mesma dependência que
`tools/png_to_map.py` já usa, sem precisar entrar em
`requirements_server.txt`, que é só pro servidor headless). O script é
ADITIVO por design — nunca reconstrói os PNGs de atlas do zero:
- Slot já ocupado no CATÁLOGO nunca é sobrescrito.
- Slot com pixel NÃO-transparente na imagem atual do atlas (mesmo sem
  entrada no catálogo — ex: célula pintada à mão pelo usuário num
  editor de imagem, sem passar pelo script) também é tratado como
  ocupado — proteção contra o script picar por cima de uma arte
  colocada manualmente. Detecção via `Image.crop(...).getbbox() is
  None` (Pillow) — bbox `None` = célula 100% transparente.
- Catálogo é escrito por merge, nunca por substituição — entrada
  adicionada à mão (pro caso de ícone pintado direto no atlas)
  sobrevive a uma execução futura do script.

Rodado 1x nesta sessão: catalogou os 27 skills + 22 items existentes
(49 no total), confirmado idempotente (2ª execução: 0 adicionados).
Confirmado por comparação pixel a pixel (Pillow `ImageChops.difference`)
que cada slot do atlas bate exatamente com o PNG individual de origem.

**Teste** (`tests/test_icon_atlas.py`, novo, 9 casos): `_IconAtlasSheets.
get_region()` isolado (ícone catalogado devolve Surface certa, não
catalogado devolve `None`, sheet cacheada não recarrega o PNG 2x);
`IconManager.get()` (ícone do atlas, ícone fora do atlas cai pro
arquivo individual, ícone inexistente devolve `None`, escala pro
tamanho pedido); e uma amostra dos PNGs de atlas REAIS já commitados
batendo pixel a pixel com os arquivos de origem (não testa o script em
si — dev tool manual, mesmo padrão dos outros scripts de `tools/`, sem
suíte própria — só o RESULTADO que o jogo consome). Revert-to-confirm:
`git stash` de `ui/icon_manager.py`/`ui/icon_atlas.py`/
`ui/icon_atlas_catalog.py`/atlas PNGs → `ModuleNotFoundError: No
module named 'ui.icon_atlas'` na coleta.

Suíte: `test_icon_atlas.py` (9) + `test_client_ui.py` (175) +
`pytest tests/ -k "icon or hotbar or inventory or tooltip or shop"`
(135, com Postgres ativo) — sem regressão.

**Fase 2 (adiada, decisão separada com o usuário)**: usar o atlas pra
desenhar arte de verdade nos nós de talento (troca do retângulo+letra
atual em `ui/talent_system.py::_render_node`) — precisa de decisão
por-talento de qual ícone usar (alguns talentos têm candidato óbvio
via `unlocks_skill`, outros são só bônus de atributo sem ícone óbvio
pra reaproveitar). Não implementado ainda.

## 182. Backlog dos 14 itens antigos, item #10 (Fase 2) — ícone de verdade nos nós de talento

18/09/2026. Fase 2 do item #10, feita na sequência da Fase 1 (§181).
Nó de talento (`ui/talent_system.py::_render_node`) desenhava só um
retângulo colorido + as 2 letras iniciais do nome, mesmo pra talentos
que desbloqueiam uma skill (que já tem ícone de verdade catalogado no
atlas desde a Fase 1) — nenhuma arte de ícone existia pra talento
antes disso.

**Dado novo**: `content/talent_data.py` — cada talento ganha
`"icon": str | None` (mesma chave que `ICON_ATLAS` já usa, ex:
`"skill_golpe_debilitante"`). Preenchido AUTOMATICAMENTE só pros 13
talentos (de 44 no total) que já têm `unlocks_skill` apontando pra uma
skill real — reaproveita o ícone da PRÓPRIA skill, zero arte nova,
zero ambiguidade (`icon = f"skill_{unlocks_skill}"`). Os outros 31
(bônus de atributo puro, sem skill óbvia pra reaproveitar) ficam
`icon: None` — nó continua com o fallback de letra de sempre até
alguém escolher um ícone (reaproveitado ou pintado numa célula livre
do atlas + catalogado via `tools/build_icon_atlas.py`).

**Fix de renderização** (`_render_node`): bloco "Inicial do nome
centralizado" virou "Ícone (ou fallback: inicial do nome)" —
`ICONS.get(t["icon"], NODE_ICON_SIZE)` (novo, 40px — cabe dentro do nó
de 64px deixando margem pro contador/estrela nos cantos) se `icon` for
truthy E resolver de verdade; senão cai pro `initials`/`font_lg` de
sempre, comportamento idêntico ao que já existia. Nó BLOQUEADO (sem
pontos alocados, requisito não cumprido) escurece o ícone com
`fill_surf(size, (0,0,0,160))` por cima — MESMO tratamento visual que
a hotbar já usa pra skill indisponível (`client/hotbar_handlers.py`),
não inventa mecanismo novo. Barra de progresso translúcida (já
existia) e estrela dourada de "desbloqueia skill" (já existia)
continuam exatamente iguais, só o miolo do nó mudou.

**Teste** (`tests/test_talent_node_icon.py`, novo, 4 casos): (1)
talento com `icon` chama `ICONS.get()` com a chave certa; (2) talento
sem `icon` NUNCA chama `ICONS.get()` (spy registra zero chamadas —
prova que o fallback de letra continua intocado pros 31 sem ícone);
(3) escurecimento isolado via `ICONS.get()` mockado devolvendo
vermelho puro opaco — desbloqueado sai vermelho puro, bloqueado sai
mais escuro (comparação direta de pixel, não indireta via cor de
fundo, que já mudava antes desta correção por outro motivo); (4) os 13
talentos com `unlocks_skill` têm `icon` batendo com a skill certa E
esse ícone genuinamente catalogado no atlas (guarda de regressão pra
skill renomeada quebrar a referência silenciosamente). Revert-to-
confirm: `git stash` de `ui/talent_system.py` + `content/talent_data.py`
→ os 4 testes falham (`AttributeError: module 'ui.talent_system' has
no attribute 'ICONS'` / `AssertionError: None != 'skill_...'`).

Suíte: `test_talent_node_icon.py` (4) + `test_icon_atlas.py` (9) +
`test_client_ui.py` (175) + `pytest tests/ -k "talent"` (23) — sem
regressão.

**Follow-up (mesma sessão, pedido do usuário)**: ícone de talento
esticava só até `NODE_ICON_SIZE=40` (deixava margem pro contador/
estrela ao redor) — usuário pediu pra preencher o slot INTEIRO (64px),
contador/estrela desenham por cima do ícone esticado em vez de ao
lado. `NODE_ICON_SIZE` removido (agora sem uso); `ICONS.get(icon_key,
r.width)` usa a largura real do nó (já escalada pela UI,
`self._node_rect()`) em vez da constante fixa. Teste reforçado —
`test_talento_com_icon_chama_icons_get_com_a_chave_certa` agora também
verifica que o tamanho pedido ao `ICONS.get()` bate exatamente com
`node_r.width` (antes só checava a CHAVE do ícone, não o tamanho).
Revert-to-confirm: `git stash` de `ui/talent_system.py` → tamanho
pedido `33` (escalado de 40) em vez do `53` esperado (escalado de 64),
exatamente a diferença proporcional certa.

## 183. Backlog dos 14 itens antigos, item #8 — `IconManager._cache` sem teto/eviction

18/09/2026. `IconManager._cache` (`ui/icon_manager.py`) crescia sem
limite — chave é `f"{name}@{size}"`, e o MESMO ícone é pedido em
vários tamanhos diferentes ao longo do jogo (hotbar, tooltip,
inventário, loja, etc.), então a cardinalidade real não é só "1 por
ícone catalogado", cresce com o produto ícone×tamanho. Inconsistente
com o padrão já usado em `ui/floating_text.py::_outline_cache` e
`ui/ui_helpers.py::_FILL_CACHE` (ambos com teto explícito).

**Fix**: `IconManager._CACHE_MAX = 512` (mesmo valor de
`_OUTLINE_CACHE_MAX`, mesma ordem de grandeza de uso) — `get()` limpa
o cache INTEIRO (`self._cache.clear()`) quando estoura, antes de
guardar a entrada nova. Mesma disciplina simples de sempre neste
projeto: `clear()` total, nunca eviction parcial/LRU — cache
reconstrói sozinho sob demanda no próximo frame, sem custo de manter
uma estrutura de LRU real por um ganho marginal.

**Teste** (`tests/test_icon_manager_cache_eviction.py`, novo, 4
casos): cache nunca ultrapassa o teto mesmo pedindo 50 tamanhos
diferentes do mesmo ícone; estourar o teto limpa TUDO (não evicta
parcial — só a entrada recém-inserida sobra); continua devolvendo o
ícone certo (tamanho correto) depois de limpar; ícone inexistente
(`None` cacheado) também conta pro teto sem quebrar. Revert-to-
confirm: `git stash` de `ui/icon_manager.py` → 3 dos 4 testes falham
(`6 not less than or equal to 5`, etc.) — o 4º passa mesmo sem o fix
porque não depende do teto (só confirma que o ícone certo continua
resolvendo).

Suíte: `test_icon_manager_cache_eviction.py` (4) + `test_icon_atlas.py`
(9) + `test_icon_key_migration.py` + `test_client_ui.py` (194 no
total) — sem regressão.

## 184. Backlog dos 14 itens antigos, item #5 — `World.get_first()` (variante singleton de `get_entities_with()`)

18/09/2026. `World.get_entities_with()` sempre aloca a lista INTEIRA de
resultados, mesmo quando o chamador só quer 1 — padrão real e
recorrente no cliente: `for ..., comp in world.get_entities_with(X):
...; break` (ou o mesmo sem `break`, rodando só 1 vez porque só existe
1 entidade candidata na prática). 37 ocorrências confirmadas,
concentradas em `ui/systems.py` (20), `ui/spell_system.py` (9),
`game.py` (5), `ui/quest_system.py` (2), `ui/god_mode.py` (1) — todas
consultando um destes tipos: `Camera`, `Tilemap`, `FogOfWar`,
`PlayerControlled`, `PlayerAutoMove`, `PirofagiaAiming`.

**Achado de segurança crítico ANTES de tocar em qualquer call site**:
esses tipos só são singleton no mundo LOCAL do CLIENTE (1 câmera, 1
mapa carregado, 1 jogador controlado por vez — confirmado checando
TODO `add_component(..., Tipo(...))` do repo pra cada um). No
SERVIDOR os MESMOS tipos NÃO são singleton — `Tilemap` existe 1 POR
MAPA (`server/world_server.py` carrega todos os mapas ao mesmo tempo)
e `PlayerControlled` é adicionado a TODO player conectado
(`server/world_server.py:2040`). Por isso o escopo desta migração
ficou travado em `ui/`, `client/` e `game.py` — nunca em `server/`,
`engine/world_systems.py` ou qualquer código compartilhado/headless.
Confirmado por varredura final que os 37 candidatos estão TODOS
dentro desse escopo seguro.

**Fix**: `World.get_first(*component_types)` (novo, `engine/world.py`)
— espelha a MESMA lógica de seleção de `get_entities_with()` (conjunto
de índice mais seletivo), devolve na 1ª entidade encontrada sem montar
a lista; `None` (não `[]`) se nada bater. Docstring documenta
explicitamente a armadilha servidor-vs-cliente acima, pra qualquer uso
futuro não reintroduzir o erro.

**Migração dos 37 call sites**: mecânica na maioria (`for` vira `if` +
unpack da tupla, mesma indentação de corpo, sem mudança de
comportamento) — MAS 3 call sites tinham `continue`/`break` dentro do
próprio corpo do loop, que não existe fora de um loop de verdade
(`continue`/`break` num `if` é `SyntaxError`). Precisaram de
reestruturação real:
- `ManaSystem.update()` — 1 `continue` (guarda de `max_mana<=0`) virou
  `if max_mana > 0: <resto do corpo>` (inversão simples).
- `SpellCastSystem.render()` (mira do Tiro Múltiplo) e
  `PirofagiaSystem.render()` — `continue`s viraram `return` (nada
  existe depois do loop no corpo do método, mesmo efeito líquido).
- `CameraSystem.update()` — 1 `continue` (guarda de `not
  SMOOTHING_ENABLED`) virou `if/else` (branch que fazia snap direto vs.
  branch que fazia lerp).
- `ChannelingSystem.update()` — o MAIS arriscado: 2 `continue`
  aninhados (intenção de movimento interrompe; mana insuficiente no
  tick interrompe), com código DEPOIS do loop original
  (`for entity_id in interrupted: ...`/`for entity_id in to_finish:
  ...`) que precisa rodar mesmo quando a entidade única cai num desses
  2 casos — reestruturado com `if/else` aninhado + flag `_mana_ok`
  (guarda o 2º `continue`, que só deveria pular o check de conclusão,
  não repetir a dedução de mana já pulada pelo próprio branch).

Confirmação de segurança ponto-a-ponto: pra CADA call site convertido,
antes de tocar, o corpo inteiro do loop foi lido em busca de
`continue`/`break` em QUALQUER nível de indentação direto do loop
(não dentro de um `for`/`while` aninhado, onde pertenceria a esse loop
interno, não ao externo sendo convertido) — só depois de confirmar
"sem `continue`/`break` direto" ou "com eles, mapeados" a conversão
foi feita.

**Erro cometido e corrigido durante a migração**: `ast.parse()` não
detecta `break`/`continue` fora de loop — isso só é validado na
COMPILAÇÃO pra bytecode, não no parse puro. Um `break` sobrando (não
removido numa conversão em `game.py::_on_god_mode_save`) passou pelo
meu próprio `ast.parse()` de verificação sem erro, e só foi pego
depois, na coleta real do pytest (`SyntaxError: 'break' outside loop`
ao importar `game.py`). Corrigido (`break` removido) e a verificação
de sintaxe desta sessão em diante passou a usar `compile(..., 'exec')`
em vez de `ast.parse()` — a lição fica registrada aqui pra não repetir.

**Teste** (`tests/test_world_get_first.py`, novo, 6 casos): sem
entidade compatível → `None`; sem argumentos → `None`; 1 entidade
compatível → tupla idêntica à de `get_entities_with()[0]`; várias
entidades compatíveis → devolve uma delas de verdade (não quebra);
múltiplos tipos exigidos → só entidade com TODOS conta; entidade
removida some do resultado. Revert-to-confirm: `git stash` de
`engine/world.py` → `AttributeError: 'World' object has no attribute
'get_first'` nos 6 testes.

**Teste dedicado pro call site de maior risco**
(`tests/test_channeling_system_get_first.py`, novo, 3 casos, já que
`ChannelingSystem.update()` teve controle de fluxo genuinamente
reescrito, não só um `for` trocado por `if`): intenção de movimento
interrompe SEM incrementar `elapsed`; mana insuficiente interrompe e
NÃO deduz mana; conclusão normal (mana suficiente, atinge a duração)
remove `Channeling` via `to_finish`, não via `interrupted`. Estes 3
testes passam tanto ANTES quanto DEPOIS da migração — esperado e
correto: é um refactor de equivalência comportamental, não uma
correção de bug, então não existe uma versão "quebrada" pra revert-to-
confirm detectar aqui. A garantia real desta migração vem de (a) o
`get_first()` em si já ter revert-to-confirm válido, e (b) esses 3
testes provarem que a saída observável é idêntica nos 2 lados do
refactor.

Suíte: `test_world_get_first.py` (6) + `test_channeling_system_get_first.py`
(3) + `test_calamidade_channel_config.py` (3) +
`test_tiro_multiplo_reticle_cache.py` (3) + `test_client_ui.py` (175)
+ `pytest tests/ -k "quest or god_mode or spell or channel or icon or
talent or minimap or camera"` (221) — sem regressão.

**Backlog dos 14 itens antigos: 13 de 14 concluídos.** Resta só #2
(`ui/systems.py` monolítico, 4954 linhas/10 Systems) — mudança
estrutural maior, tratada como iniciativa separada por recomendação já
feita ao usuário, não espremida na fila sequencial.

## 185. Backlog dos 14 itens antigos, item #2 — divisão de `ui/systems.py` (4913 linhas/10 Systems) em 7 arquivos por domínio

18/09/2026. Último item do backlog antigo de 14. Usuário pediu revisão
multiagente (5 agentes: guardrails de arquitetura, qualidade de código,
comparação com mercado/referências, performance, integração/regressão)
ANTES de aplicar — implementado num git worktree isolado
(`candidate-split-ui-systems`) como candidato, nunca direto nos
arquivos reais.

**Divisão** (nenhuma classe chamava outra diretamente ANTES da
divisão — confirmado por grep; corpo de cada System/helper movido
byte-idêntico, confirmado por diff AST):
- `ui/player_input_system.py` — MouseTargetingSystem, PlayerInputSystem
- `ui/render_system.py` — RenderSystem, CameraSystem
- `ui/tile_render_system.py` — TileRenderSystem, FogSystem
- `ui/shop_system.py`, `ui/consumable_system.py`, `ui/loot_system.py`,
  `ui/skill_system.py`
- `ui/click_rect_helpers.py` — `_corpse_click_rect`, `_entity_click_rect`,
  `_live_target_at_world_pos` (hit-test de clique compartilhado entre
  MouseTargetingSystem e LootSystem)
- `ui/systems.py` — shim de re-exportação (mesmo padrão que o arquivo
  já usava pra re-exportar `engine/world_systems.py`) — os 32 nomes
  reais consumidos por 16+ arquivos externos (`game.py`,
  `client/hotbar_handlers.py`, `ui/buff_bar.py`,
  `client/remote_entity_handlers.py`, `ui/spell_system.py`, testes)
  continuam resolvendo sem mudança nenhuma nos consumidores —
  confirmado por análise AST do repo inteiro (imports E acesso via
  `import ui.systems as X; X.nome`).

**Revisão multiagente — rodada 1 (candidato inicial)**:
- **Performance**: APROVADO, com medição real (não suposição) — bloco
  de import duplicado nos 7 arquivos custa 0,03% do tempo de startup
  (cache `sys.modules` evita reimportar de verdade); hot path
  bytecode-idêntico ao original (`LOAD_GLOBAL` continua `LOAD_GLOBAL`,
  nunca virou `LOAD_ATTR` cross-módulo); caches de módulo
  (`_GROUND_EFFECT_SPRITES`, `_OBJECT_SCAN_MARGIN_CACHE`) não foram
  duplicados.
- **Guardrails de arquitetura**: REPROVADO — (1) zero atualização de
  `CLAUDE.md`/`MAPA_PROJETO.md`/`SISTEMAS_ECS.md` no mesmo commit,
  violando a regra "Isso vale NO MESMO commit/sessão da mudança,
  nunca depois que sobrar tempo"; (2) `_live_target_at_world_pos`
  ficou em `ui/player_input_system.py` mas só é chamado de
  `ui/loot_system.py` — acoplamento novo entre domínios, tratamento
  inconsistente com `_corpse_click_rect` (que virou módulo
  compartilhado); (3) header de import completo copiado nos 7
  arquivos sem poda, com comentários que descrevem código que não
  está mais ali (ex: comentário sobre `RenderSystem.render()` em
  arquivos sem `RenderSystem`).
- **Comparação com mercado/referências**: REPROVADO, mesmos achados
  (2) e (3) acima, confirmados independentemente — com quantificação
  (723 imports não usados no total pela contagem desse agente,
  288 pela recontagem própria via AST antes da correção) e comparação
  com Veloren/Bevy/AzerothCore (agrupamento por domínio, não 1 classe
  por arquivo, É o padrão de mercado — approved nesse ponto; shim de
  re-exportação também é padrão reconhecido, "Strangler Fig"/Fowler).
- **Qualidade de código e integração/regressão**: verificadas
  diretamente (não via agente, pra não sobrecarregar a máquina rodando
  5 agentes pesados em paralelo — o processo foi interrompido 2x por
  reinício de PC durante a rodada original) — suíte completa (1740
  testes) sem regressão real, só as 2 falhas de timer já conhecidas.

**Correções aplicadas** (rodada 2, mesmo commit candidato):
1. `_entity_click_rect`/`_live_target_at_world_pos` movidos pra
   `ui/click_rect_helpers.py`, junto com `_corpse_click_rect` — os 3
   formam 1 grupo (hit-test de clique) com consumidor em Systems de
   arquivos diferentes, tratamento agora simétrico.
2. Imports podados em todos os 7 arquivos (288 nomes removidos,
   script próprio baseado em AST — nunca remove linha inteira de um
   `from X import (a, b, c)` se pelo menos 1 nome da linha ainda for
   usado). Comentários órfãos (re-export de `world_systems`,
   "hoisted pro topo de `RenderSystem.render()`") removidos/corrigidos
   nos arquivos onde não descreviam mais nada real.
3. `CLAUDE.md` (regra de roteamento "Sistema de UI/render novo" +
   5 pontos que citavam `ui/systems.py`), `MAPA_PROJETO.md` (linha da
   tabela + árvore de arquivos com os 7 novos + 3 pointers), e
   `SISTEMAS_ECS.md` (1 pointer) atualizados no mesmo commit — cada
   um redirecionado pro arquivo de domínio real.
4. Esta seção — as docstrings dos 7 arquivos já citavam "ver
   PROBLEMAS_ARQUITETURA.md item #2 do backlog de 14" antes dela
   existir; agora existe.

**Teste**: suíte completa re-rodada depois de cada correção — sem
regressão. Revert-to-confirm não se aplica da forma usual aqui (não é
correção de bug, é refactor de organização — corpo de código
byte-idêntico ao original, confirmado por diff AST na própria revisão
multiagente).

**Backlog dos 14 itens antigos: 14 de 14 concluídos.**

---

## 186. (movido) 4 bugs reportados juntos (19/09/2026) — corpo com colisão, animação de morte, transparência de bush, bot vs. própria torre — ver HISTORICO_BUGS.md §45

## 187. (movido) Investigação — "Interceptar" ficava sempre rejeitado numa partida de BG (19/09/2026, NÃO é bug de código) — ver HISTORICO_BUGS.md §46

## 188. Feature — objeto "alto" (árvore/construção) fica transparente com o jogador atrás dele (20/09/2026)

Efeito clássico "ver o personagem atrás da árvore", pedido do usuário.
Validado em jogo após 4 rodadas de calibração no mesmo dia — design
final documentado aqui pra próxima pessoa (ou próxima sessão) não
reconstruir do zero.

**Dado novo no catálogo**: `TileType.occludes_player: bool = False`
(`engine/tileset.py`) — curado por família/grupo em
`discover_object_sheet_tiles()` (mesmo mecanismo de `default_vision_height`:
família cascateia pra todos os sub-grupos, grupo pode sobrescrever).
Marcado hoje em 2 lugares: grupo `"arvores"` (1 grupo só) e família
inteira `"TX Village Building with Shadow"`/label "Construções"
(cascateia pra todos os ~97 sub-grupos de prédio de uma vez, sem marcar
cada tipo na mão). `get_world_objects()` (`ui/tile_render_system.py`)
repassa o campo no dict de cada objeto visível.

**Mecanismo de desenho** (`ui/render_system.py::RenderSystem.render()`):
- Escopo (decisão do usuário): só o **jogador LOCAL** participa (nunca
  outro player/bot/mob) — 1 checagem por objeto visível, não N.
- Aproveita a passada ÚNICA de Y-sort (ordem crescente por `sort_y`):
  guarda o retângulo de tela real do jogador local (`_local_player_screen_rect`)
  e sua máscara de pixel (`_local_player_mask`, via `pygame.mask.from_surface`
  no frame de animação — NUNCA `renderable.width/height`, que não reflete
  o sprite de verdade) assim que ele é desenhado. Objeto com
  `occludes_player=True` desenhado DEPOIS dele na mesma passada (== na
  frente dele no Y-sort) é candidato; um objeto desenhado ANTES nunca vê
  o retângulo do jogador (ainda `None`) e fica opaco automaticamente —
  nenhuma comparação de `sort_y` explícita é necessária.
- Filtro em 2 estágios: `Rect.colliderect()` primeiro (barato, corta a
  esmagadora maioria dos objetos longe do jogador) — só paga o custo de
  `Mask.overlap_area()` pros poucos candidatos que já se sobrepõem em
  bounding box.
- Critério final: **cobertura ≥ `RenderSystem._OCCLUSION_COVERAGE_THRESHOLD`**
  (constante nomeada, hoje `0.8` — ajustada de 100% depois do usuário
  testar) — `Mask.overlap_area()` conta quantos pixels OPACOS do jogador
  coincidem com pixels opacos do objeto; só ativa quando isso bate pelo
  menos essa fração da contagem total de pixels opacos do jogador
  (`Mask.count()`). Corrige o erro de 1ª tentativa (retângulo cheio
  colliderect — disparava encostando na moldura TRANSPARENTE ao redor
  do sprite, não só no tronco/copa desenhados de verdade) e o de 2ª
  tentativa ("qualquer 1 pixel em comum" — trocava fácil demais,
  bastava encostar numa borda). Fallback sem sprite (objeto OU jogador
  caiu no retângulo sólido genérico) usa a MESMA fração, mas por ÁREA de
  interseção de retângulo em vez de máscara (nenhum dos dois tem parte
  transparente nesse caso).
- Opacidade final: `set_alpha(127)` (50%, ajustado de 70% inicial) numa
  **cópia** do sprite — nunca no objeto original, que é cacheado/
  compartilhado por todo tile do mesmo tipo (`TILE_SPRITES`). Máscaras
  cacheadas por `id(surface)` em `RenderSystem._occlusion_mask_cache`,
  sem teto — seguro pelo mesmo motivo de `TILE_SPRITES`/`_frame_cache`
  (as Surfaces mascaradas já são cacheadas pra sempre em dicts de
  módulo, `id()` nunca reciclado enquanto o processo roda). Cuidado
  já resolvido: a máscara do jogador precisa vir do frame ORIGINAL
  (antes do `.copy()+set_alpha()` do bush stealth), nunca da cópia —
  `set_alpha()` não muda o canal alpha POR PIXEL (só multiplicador
  global de blit), então a forma da máscara é idêntica de qualquer
  jeito, mas usar a cópia faria o cache de máscara crescer sem teto (1
  `id()` novo por frame enquanto o bush stealth estivesse ativo).

**Extensão futura**: marcar outro grupo/família do catálogo como
"alto" é só `"occludes_player": True` no dict certo — nenhuma lógica
nova, mesmo padrão de `is_bush`.

---

## 189. (movido) Feature — instância de BG (fila real) destruída assim que não sobra jogador REAL (20/09/2026) — ver HISTORICO_FEATURES.md §30

## 190. (movido) Feature — câmera trava na borda do mapa + câmera livre estilo MOBA na BG (20/09/2026) — ver HISTORICO_FEATURES.md §31

## 191. (movido) Clamp de câmera não aparecia na tela de jogo + clique no NPC da loja parou de funcionar — 2 duplicatas esquecidas de `get_camera_offset()` (20/09/2026) — ver HISTORICO_BUGS.md §47

## 192. (movido) Câmera livre (tecla Y, BG) só reagia ao cursor na borda depois de reselecionar "tela cheia" — `hud_surf` nunca inicializado no boot (21/09/2026) — ver HISTORICO_BUGS.md §48

## 193. (movido) Feature (infraestrutura, Fase 1) — dado de catálogo pra objeto emitir luz própria (lampião/tocha), base do sistema de dia/noite (21/09/2026) — ver HISTORICO_FEATURES.md §32

## 194. (movido) Feature — sistema de dia/noite (overlay de cor + luz de objeto), consumidor real do dado de §193 (21/09/2026) — ver HISTORICO_FEATURES.md §33

## 195. (movido) Bug real — transição dia/noite passava por acinzentado + feature — aba de debug (F12) pra hora/fase (21/09/2026) — ver HISTORICO_FEATURES.md §34

## 196. (movido) Feature — cores-âncora do dia/noite editáveis ao vivo no F12, persistidas em config.json (21/09/2026) — ver HISTORICO_FEATURES.md §35

## 197. (movido) Bug real — aba nova do F12 estourou a largura do painel, 1ª e última aba cortadas (21/09/2026) — ver HISTORICO_BUGS.md §49

## 198. (movido) Feature — slider com trilha em gradiente no F12, substitui os 4 botões fixos de pular hora (22/09/2026) — ver HISTORICO_FEATURES.md §36

## 199. Bug real — suíte completa chegava a ~11,2GB de memória, derrubando o Postgres/qualquer coisa rodando na máquina (22/09/2026)

**Sintoma relatado:** usuário rodou a suíte completa (fora desta
conversa, num terminal próprio) e viu a aba "bugar" — o container do
Postgres tinha caído no meio da rodada. Não sabia dizer se era o Docker
não estar aberto que causava o problema, ou o contrário.

**Medido, não suposto** (regra do projeto: observabilidade antes de
otimização) — rodada real da suíte completa (`pytest tests/ -q`) com
amostragem externa de memória do processo Python a cada 5s
(`Get-Process` via PowerShell, fora do processo medido — não precisa de
nenhuma dependência nova no projeto):

| | Sem correção | Com `gc.collect()` por teste |
|---|---|---|
| Pico de memória do processo | **11.186 MB (~11,2GB)** | **300-309 MB** |
| RAM livre mínima da máquina (16GB total) | **260-535 MB** | **7,7+ GB** |
| Tempo total da suíte | 311-357s (variável, achado abaixo) | 357-364s |
| Resultado dos testes | Mesmos 1763 passed/1 failed (flaky pré-existente) | Idêntico |

Com só 260-535MB livres numa máquina de 16GB (Docker Desktop precisa de
memória pra manter sua própria VM — WSL2/Hyper-V — rodando o
Postgres), QUALQUER coisa extra rodando ao mesmo tempo (o próprio Docker
Desktop reajustando memória, outro programa, até uma 2ª tentativa de
rodar a suíte em paralelo) é candidata a ser derrubada por falta de
memória. Numa tentativa anterior nesta mesma sessão, o container
realmente morreu (`docker ps -a` mostrou `Exited (255)`) bem no meio de
uma rodada de ~933s (quase 3x mais lenta que o normal — sinal de que a
máquina já estava sob pressão de memória/swap antes de travar de vez).

**Causa raiz:** NÃO é vazamento de memória de verdade (nada fica
inacessível pra sempre) — é o coletor de lixo CÍCLICO do Python
(`gc`, diferente da contagem de referência normal, que É imediata) não
rodando com frequência suficiente. Arquitetura ECS cria referências
circulares o tempo todo por design (`World` referencia `Entity`,
`Entity`/`Component` frequentemente referenciam de volta o `World`/
`System` dono) — cada teste que cria um `World`/`WorldServer` novo (69
arquivos de teste chamam `make_world_server()`, cada chamada carregando
3 mapas reais + populando entidades) deixa lixo cíclico pra trás. O
coletor automático do Python dispara por CONTAGEM de alocação (não por
tempo/quantidade de memória) — sob o volume e o ritmo de ~1700 testes
independentes rodando em sequência no MESMO processo, esse gatilho não
acompanha o ritmo de acúmulo, e o "lixo" (tecnicamente coletável, só
ainda não coletado) se empilha até quase esgotar a RAM da máquina.

**Investigação que NÃO confirmou a hipótese inicial** (registrada pra
não repetir): antes de chegar no `gc.collect()`, foram checados e
descartados como causa — (1) vazamento de pool `asyncpg` (§123) — já
tinha `AuthPoolCleanupMixin`/`restore_auth_schema` cobrindo TODOS os
arquivos que de fato tocam `server.auth` de verdade (1 falso-positivo
encontrado e descartado: `test_loadtest_bot_reconnect.py` só tem uma
função local chamada `fake_login_cycle`, sem nenhuma relação com
`server.auth`); (2) caches de módulo sem teto (`ui/*.py`) — todos os
relevantes já têm `_CACHE_MAX` explícito desde §183, e os 2 sem teto
encontrados (`engine/entity_disguise.py`/`engine/character_animation.py`
`_frame_cache`) são bounded por natureza (chave = classe×direção×estado,
espaço pequeno e fixo, populado 1x e reusado) — não crescem com o
número de testes; (3) `engine/save_system.py::_autosave_listeners`
(lista que só cresce via `append`, nunca some) — só é populado por um
`GameEngine` real, que nenhum teste da suíte instancia (é pesado
demais/precisa de rede) — lista fica vazia a suíte inteira.

**Fix**: `tests/conftest.py` (arquivo NOVO — não existia nenhum
`conftest.py` antes) com 1 hook padrão do pytest
(`pytest_runtest_teardown`) chamando `gc.collect()` depois de CADA
teste. Mecanismo 100% de infraestrutura de teste — zero mudança em
`server/`, `client/`, `engine/` ou `ui/`, aplica pra suíte inteira
automaticamente sem precisar tocar em nenhum arquivo de teste
existente.

**Teste**: a própria suíte completa é o teste — rodada 2x com o fix
(1x via driver Python customizado pra medir, 1x via `pytest` de
verdade — o comando que o usuário roda no dia a dia) e 1x sem, todas
as 3 rodadas com amostragem externa de memória. Revert-to-confirm
natural: a MESMA suíte, SEM o `conftest.py`, já tinha sido medida
2x nesta mesma sessão como parte da investigação (933s com crash real
do Postgres numa rodada, 11.186MB de pico na outra) — não precisou
reverter de propósito, o "antes" já existia como dado real.

---

## 200. (movido) Feature — cores ajustadas no F12 viram o padrão de verdade do jogo (22/09/2026) — ver HISTORICO_FEATURES.md §37

## 201. (movido) Feature — luz de lampião binária (só na fase Noite) + camada de sprite "aceso" a partir de folha companheira (22/09/2026) — ver HISTORICO_FEATURES.md §38

## 202. (movido) Bug real — F11 crashava o jogo com `UnboundLocalError: _ts` (22/09/2026, pré-existente, não relacionado às mudanças desta sessão) — ver HISTORICO_BUGS.md §50

## 203. (movido) Reescrita — técnica de escurecimento trocada de transparência pra MULTIPLICAÇÃO de cor + brilho quente aditivo (22/09/2026) — ver HISTORICO_FEATURES.md §39

## 204. (movido) Feature — corte de luz de lampião ancorado em minuto exato do ciclo (23/09/2026) — ver HISTORICO_FEATURES.md §40

## 205. (movido) Feature — relógio visual do ciclo dia/noite no HUD do jogador (23/09/2026) — ver HISTORICO_FEATURES.md §41

## 206. (movido) Feature — mapa fechado (caverna/dungeon) fica de fora do ciclo dia/noite (23/09/2026) — ver HISTORICO_FEATURES.md §42

## 207. (movido) Bug real — AttributeError ao abrir o cliente, `_day_night_system` ainda não existia (23/09/2026, dentro de 1h da §206) — ver HISTORICO_BUGS.md §51

## 208. (movido) Bug real — mapa fechado (`day_night_affected: false`) ficava sempre no brilho MÁXIMO, não escuro (23/09/2026, dentro de 1h da §206/§207) — ver HISTORICO_BUGS.md §52

## 209. Revisão de performance do cliente (3 agentes) + 4 correções reais — DayNightSystem, RenderSystem, minimap da BG, rótulos de profiler (27/09/2026)

Usuário pediu investigação do log de profiling do cliente
(`logs/client_prof.log`) pra achar os 10 sistemas mais consumidores, e
depois uma revisão formal desses 10 por 3 agentes independentes
(arquitetura, qualidade de código, práticas de mercado) pra decidir um
plano de ação. Documento completo do plano (tabela dos 10, achados de
cada agente, decisão por sistema) publicado como Artifact
(`https://claude.ai/artifact/RQ1MppYS9a5AAfb1FozRBF`) — este item
registra só as correções REAIS já aplicadas a partir dele, não repete
o levantamento inteiro.

**Achado de processo, antes das correções**: 2 dos 3 agentes
recomendaram "adicionar cache por câmera ao minimapa" pro item
`hud:combat_log` — só que o minimapa normal (`Minimap.render()`) **já
tinha** exatamente esse cache desde 14/07/2026 (`_cache_key =
(player_tx, player_ty, len(explored))`), inclusive com um comentário
no código já documentando o MESMO label mentiroso que os agentes
"descobriram" de novo. Confirma a regra do CLAUDE.md de nunca aceitar
recomendação de terceiro sem verificar contra o código atual — a
recomendação literal estava errada, mas apontava pra área certa: o bug
de verdade era um MÉTODO IRMÃO (`render_fullmap`, modo tela cheia da
BG) que nunca recebeu a mesma correção.

**Correção 1 — `ui/day_night_system.py` (clusters de luz estática, já
commitada antes deste item, `02d4cbf`)**: vilarejo com 20+ lampiões
agrupados custava ~5.6-8ms/frame porque cada luz era desenhada
separada TODO frame, mesmo sendo posição ESTÁTICA (só a câmera se
move). `build_static_light_clusters(tilemap)` agora varre o mapa 1x no
load, agrupa lampiões por alcance (union-find) e pré-compõe 1 Surface
por cluster — `render()` faz 1 blit por cluster VISÍVEL, não 1 por
luz. Custo deixou de escalar com nº de lampiões no mapa; ~4.7ms medido
no pior caso real (era ~5.6-8ms). Luz de item de jogador (dinâmica)
continua individual, fora do mecanismo.

**Correção 2 — `ui/hud_bars.py` (cache de Surface de HP/nameplate,
`84750aa`)**: `build_player_hud`/`build_mob_hud` alocavam Surface
SRCALPHA + `transform.scale` novos TODO frame por entidade visível com
barra de HP — achado convergente dos 3 agentes, apesar do MESMO
arquivo já ter esse padrão de cache pro número do nível
(`_level_surf_cache`, 14/07/2026). Fix: cache por chave quantizada
(~1.6%/bucket nos ratios de HP/XP/recurso — bem abaixo de 1px de
diferença visual). Surface devolvida nunca é mutada pelos callers
(confirmado antes de cachear — só blitada por `WORLD_LABELS`).

**Correção 3 — `ui/minimap.py` (`_rebuild_fullmap`, `2268fea`)**: bug
irmão do já corrigido em `_rebuild_numpy` (14/07/2026) — nunca
propagado pra cá. `_rebuild_fullmap()` (modo tela cheia dentro de
BG/Arena) reconvertia `explored` INTEIRO pra numpy
(`np.array(list(explored))`) toda vez que o cache externo invalidava —
e isso invalida quase todo tick numa BG ativa, porque `visible` (visão
compartilhada de time) muda constantemente mesmo com `explored` já
estável. Medido antes do fix: 22.744ms/chamada com `explored` de 40k
tiles — bate quase exato com o pico real de 22.721ms do label
`hud:combat_log`. Fix: cache da conversão por `len(explored)`
(monotonicamente crescente, mesmo invariante já usado em
`_rebuild_numpy`), separado da conversão de `visible`. Medido depois:
7.333ms — redução de ~3x no cenário que mais importa (visible mudando,
explored estável). Invalidado também em `on_map_load()` (mapa novo =
`explored` novo, nunca pode reusar o array do mapa anterior mesmo que
por coincidência tenha o mesmo `len()`).

**Correção 4 — `game.py` (rótulos de profiler, mesma sessão)**: label
`hud:combat_log` nunca mediu o chat de verdade (achado já catalogado
em §146/§147, nunca corrigido até agora) — na prática media
minimapa+quest HUD+buff bar+relógio dia/noite. Além disso, um bloco
inteiro (`WORLD_LABELS.render`+`FLT.render`+`PROC.render`+
`WARN.render`+`_draw_low_hp_vignette`+`_draw_online_hud`+
`_draw_chat_log`) rodava sem NENHUM `_prof_record` — qualquer
regressão nele era invisível em qualquer relatório/spike do F11.
Fix: minimapa ganhou rótulo próprio (`rnd:minimap` — o mais provável
de gerar pico, ver Correção 3); o resto do bloco antigo virou
`hud:side_widgets` (nome que reflete o que mede de verdade); o bloco
antes invisível virou 2 rótulos novos — `rnd:world_labels`
(WORLD_LABELS+FLT, os 2 que escalam com nº de entidades) e
`hud:chat_and_notices` (o resto, custo fixo pequeno). Mudança é só de
instrumentação (dentro de `if PROFILE_FRAMES:`) — zero efeito em quem
joga sem o F12/F11 aberto.

**Testes**: `tests/test_client_ui.py` — `test_build_player_hud_cacheia_por_ratio_quantizado`,
`test_build_mob_hud_cacheia_por_ratio_quantizado`,
`test_rebuild_fullmap_reusa_array_de_explored_quando_so_visible_muda`,
`test_rebuild_fullmap_reconverte_quando_explored_cresce`,
`test_on_map_load_invalida_cache_de_explored_do_fullmap` (+ os já
existentes de `tests/test_day_night_light_clusters.py` pra Correção
1). Revert-to-confirm feito em todas as 4 correções antes de fechar —
todos os testes novos falham sem o respectivo fix. Suíte relacionada
(`test_client_ui.py` completo + `test_day_night_light_clusters.py`)
passando; correção 4 (rótulos) não tem teste automatizado — é só
renomeação/instrumentação, verificado por leitura cuidadosa do diff +
`ast.parse` (sem cobertura de teste pro mecanismo de profiling no
projeto todo).

## 210. Fecha o plano de ação de performance do §209 — itens de prioridade baixa (27/09/2026)

Continuação do §209, mesma sessão. Os 3 itens de prioridade baixa do
documento do plano:

- **`display_flip`**: label antigo somava a cópia de framebuffer (custo
  de app) com a espera de vsync do driver/SO dentro de
  `pygame.display.flip()` (custo de plataforma, não otimizável em
  código) — impossível distinguir pico de vsync vs. pico de blit real.
  Separado em `present:blit`/`present:vsync_flip` — só instrumentação.
- **`ui:perf_overlay`**: alocava Surface SRCALPHA nova todo frame com
  F11 aberto, ignorando a lição que o próprio projeto já documentou
  (`fill_surf`, `client/chat_handlers.py`). Como o conteúdo (números de
  FPS/ms) muda todo frame, não dá pra cachear por conteúdo — a Surface
  agora cresce e é reaproveitada (nunca encolhe), blit final limitado à
  área real do conteúdo atual. `hasattr(render_to)` resolvido 1x por
  instância em vez de por linha por frame. Confirmado por script de
  verificação pixel a pixel (0 diferenças) antes de escrever teste.
- **`input_dispatch`**: SEM AÇÃO isolada, por decisão explícita — custo
  medido é irrelevante (0.148ms médio) e o valor real do achado
  (quebrar em sub-labels pra diagnosticar picos raros) está atado ao
  mesmo refactor maior do monólito de input/orquestração (item "B1" já
  catalogado). Revisitar junto de B1 se esse refactor acontecer.

`transform_scale` e `hud:draw_hud` (os 2 "sem ação" do plano) também
não tiveram nenhuma mudança — os 3 agentes da revisão confirmaram que
já estão corretos.

**Testes**: `tests/test_client_ui.py` —
`test_draw_perf_overlay_reusa_surface_cacheada_entre_frames`,
`test_draw_perf_overlay_hasattr_render_to_resolvido_1_vez_por_instancia`,
`test_draw_perf_overlay_conteudo_menor_nao_deixa_pixel_fantasma`.
Revert-to-confirm feito — os 3 falham sem o fix (AttributeError,
atributo de cache nunca existiria). `display_flip` não tem teste
automatizado (só instrumentação/renomeação, mesmo critério do §209).
Suíte `test_client_ui.py` completa passando (230 testes).

Com isso, os 10 itens do documento do plano de ação
(`https://claude.ai/artifact/RQ1MppYS9a5AAfb1FozRBF`) estão todos
decididos: 2 de prioridade alta + 3 de prioridade média + 3 de baixa
corrigidos (1 sub-item da média — mesclar as 2 varreduras do
TileRenderSystem — deferido de propósito, ver §209), 2 "sem ação"
confirmados como já corretos.

## 211. Playtest pós-§209/§210 revela achado novo — cache-miss do gradiente de luz de item ainda custa ~12-18ms na 1ª vez (27/09/2026)

Depois de fechar o plano do §209/§210, o usuário jogou uma sessão real
(~7-8min, `PROFILE_FRAMES` ativo) e comparou o log novo contra o
baseline do documento publicado. 8 dos 10 sistemas confirmaram a
previsão dos 3 agentes (picos caindo 54-89% nos itens de cache
aplicados); 2 anomalias isoladas em `transform_scale`/`hud:hotbar`
foram rastreadas pelo `[SPIKE]` breakdown do próprio log até um
engasgo externo de vsync/driver no mesmo frame — não regressão dos
fixes. Comparativo completo publicado no mesmo documento do §209.

**Achado real, reportado pelo usuário**: "ao ligar a lanterna do
personagem o consumo do sistema de iluminação sobe". Medição direta
confirmou — a vetorização numpy do §209 (item 2 do plano) deixou o
CÁLCULO do gradiente rápido, mas o cache-miss da PRIMEIRA vez que um
(raio, intensidade) aparece ainda tem um piso: pro raio real do
lampião (7 tiles → array 448×448), `_get_light_add_gradient` +
`_get_warm_glow_gradient` custam ~12-18ms medidos isoladamente — bate
com o `13.5ms` de `rnd:DayNightSystem` no exato frame do
`PLAYER_LIGHT_UPDATE`, visto no log. Luz de MAPA (estática) nunca sofre
disso — já é toda pré-computada em `build_static_light_clusters()`
(§209) no carregamento; só luz de ITEM (dinâmica) paga esse custo, e
paga exatamente no momento em que o jogador aperta o botão.

**Fix**: `DayNightSystem.warm_player_light_gradients()`
(`ui/day_night_system.py`) — varre `content/item_table.py::ITEMS`,
acha todo item com `light_source` definido (hoje só `lampiao`) e
pré-chama `_get_light_add_gradient`/`_get_warm_glow_gradient` pra cada
combinação (raio, intensidade) única do catálogo. Chamado 1x em
`game.py::__init__`, no mesmo lugar/momento da chamada inicial de
`build_static_light_clusters` — **nunca** em `_load_ambient_zones()`
(troca de mapa), porque a luz de item não depende de mapa, é dado
puro do catálogo. Item de luz futuro entra na cobertura automaticamente,
sem precisar tocar este método de novo (mesmo princípio de "ponto único
de verdade" já usado pro resto do sistema de luz).

**Testes** (`tests/test_day_night_light_clusters.py::
TestWarmPlayerLightGradients`): cache populado com o (raio,
intensidade) real do lampião lido do catálogo; a chamada seguinte de
`_get_light_add_gradient`/`_get_warm_glow_gradient` (a mesma que
`render()` faz ao ligar a luz) devolve o objeto JÁ CACHEADO (`assertIs`,
não só "existe uma entrada parecida"); nenhuma combinação repetida gera
trabalho duplicado. Revert-to-confirm: 2 dos 3 testes falham sem a
chamada (o 3º, sobre não-duplicação, não é sensível a essa reversão
por natureza). Suíte `test_day_night_light_clusters.py` +
`test_client_ui.py` completas passando (243 testes).

## 212. (movido) Mob perseguindo atravessa o mapa inteiro depois que o player teleporta pra outro ponto do MESMO mapa (27/09/2026) — ver HISTORICO_BUGS.md §53

## 213. (movido) Correção do §212 não resolveu de verdade — mob "congelado" na tela era um bug de sincronização AOI, não de IA (28/09/2026) — ver HISTORICO_BUGS.md §54

## 214. (movido) Minimapa pequeno reconvertia `explored` do zero a cada tile andado — cache existente só cobria o mapa grande (28/09/2026) — ver HISTORICO_BUGS.md §55

## 215. (movido) Tela de loading pré-carrega TODO asset do jogo (boot + troca de mapa/instância) + silencia som até carregar de verdade (28/09/2026) — ver HISTORICO_FEATURES.md §43

## 216. (movido) Skill mudando de slot sozinha na BG mesmo com preferência salva — RESOLVIDO (28-29/09/2026) — ver HISTORICO_BUGS.md §56

## 217. Interceptar contra bot de BG — logging de diagnóstico adicionado, investigação EM ABERTO (29/09/2026)

Usuário relatou, testando a BG: "a skill interceptar, ainda é uma
skill problemática, como ela lida com mobilidade tanto do player
quanto com a do alvo... quando eu tentava usar a skill no bot
dificilmente funcionavam e quando funcionava o personagem não dava
dash até o alvo". Pedido explícito: log primeiro, ele reproduz, eu
investigo depois — mesma disciplina que resolveu o §216.

**Contexto que já existia**: Interceptar já teve 3 investigações
anteriores (§126/§127/§128), todas fechadas como "não validado:
reprodução manual em jogo" — nenhuma delas foi de fato confirmada
contra um sintoma relatado pelo usuário depois do fix. Isso já era
um sinal de alerta antes mesmo desta 4ª rodada começar.

**O que foi encontrado só de mapear o código (sem ainda ter log
real)**: das 6 razões de rejeição de `_skill_interceptar`
(`engine/skill_handlers.py`) — "Nenhum alvo", "Alvo inválido", "Alvo
muito próximo", "Alvo muito longe", "Sem espaço ao redor do alvo",
"Caminho bloqueado" — só as 2 primeiras tinham log via o mecanismo
genérico já existente (`_WARN_DEBUG_REASONS`/`_log_skill_warn_debug`,
`debug/aoi_debug.py`, §84/§87). As 4 específicas da skill, que são
justamente as mais prováveis de capturar rejeição contra um alvo que
se move sozinho (bot com IA própria, `server/bot_ai_system.py`),
nunca geravam log nenhum.

**Fix de diagnóstico aplicado** (`engine/skill_handlers.py`, commit
"debug(interceptar): loga target_is_bot..."):
- `_WARN_DEBUG_REASONS` ganha as 4 razões que faltavam.
- `_log_skill_warn_debug` (evento `SKILL_WARN`) e o log de sucesso
  `INTERCEPTAR_CAST` ganham o campo `target_is_bot` (via
  `engine.components.BotControlled`), pra confirmar direto no log se
  o alvo era um bot de verdade, sem precisar inferir pela ausência de
  outros campos.

**Verificação feita (smoke-test manual, script descartável, não
commitado)**: simulando um caster e um bot em times opostos (mesma
relação hostil que a BG real usa entre times, `Faction("arena_time_a"
/"arena_time_b")`, `content/faction_data.py`), as 4 razões novas e o
path de sucesso dispararam corretamente no log com `target_is_bot=
True`. Armadilha encontrada montando esse cenário: sem colocar caster
e bot em facções realmente hostis (times opostos), o gate genérico de
"Alvo amigável" rejeita a skill ANTES de chegar em
`_skill_interceptar` — nenhum log dispara, porque essa razão de
rejeição não é gerada por esta função nem está em
`_WARN_DEBUG_REASONS`. Se uma investigação futura usar esse mesmo
smoke-test como ponto de partida, lembrar de montar o cenário com
facções opostas de verdade, não só `spawn_player` genérico.

**Achado lateral relevante pro sintoma relatado (ainda não é a causa
raiz confirmada, só uma hipótese fortalecida)**: no smoke-test, o bot
(com `BotControlled` real) se moveu sozinho via `BotAISystem` entre a
seleção do alvo e o cast — a mesma sequência de tiles processada em
runs separados do script produziu resultados DIFERENTES (ora "Alvo
muito próximo", ora "Caminho bloqueado", ora nenhuma rejeição — só
porque o bot andou pra um lugar diferente antes do cast, decisão de
IA não-determinística entre execuções). Isso é consistente com a
suspeita do usuário: um bot de BG se movendo ativamente entre o
momento em que o player mira/clica e o momento em que o servidor
processa o cast pode fazer a mesma skill, no mesmo alvo, cair em
razões de rejeição diferentes a cada tentativa — o que bateria com
"dificilmente funciona". Isso NÃO está confirmado ainda contra o
sintoma real (é só um comportamento observado numa simulação
sintética) — precisa do log real do usuário pra virar causa raiz.

**Segunda metade do sintoma** ("quando funciona, o personagem não dá
dash até o alvo") já tem um mecanismo de log dedicado e pré-existente,
não tocado nesta sessão: `debug/interceptar_debug.py`
(`RPG_DEBUG_INTERCEPTAR`), eventos `DASH_OK`/`DASH_REJECTED`/
`DASH_START`/`DASH_FORCED`/`DESYNC_CORR` — cobre exatamente a classe
de bug "dash confirma no servidor mas o cliente desfaz/não anima"
(§126). Ainda não sabemos se o sintoma novo é a MESMA causa do §126
reaparecendo (que ficou "não validado", pode nunca ter sido corrigido
de fato) ou algo novo.

**Suíte relacionada** (23 testes, `tests/test_interceptar_*.py` +
`tests/test_move_grace_dash_guard.py` + `tests/test_move_player_debug.py`)
passando sem regressão. 100% client+server compartilhado
(`engine/skill_handlers.py` roda dos dois lados) — deploy pro servidor
de dev feito via `deploy_server.ps1` (regra padrão: sempre depois de
commit de fix/debug, sem precisar de confirmação, servidor de dev sem
jogador real).

**Não validado**: reprodução manual em jogo. Próximo passo é o
usuário testar Interceptar contra bot de BG com `RPG_DEBUG_AOI=1` E
`RPG_DEBUG_INTERCEPTAR=1` ativos (PowerShell:
`$env:RPG_DEBUG_AOI=1; $env:RPG_DEBUG_INTERCEPTAR=1; python server/main.py`
— ou setar as 2 antes de subir o server de dev) e trazer os 2 arquivos
de log (`debug/logs/aoi_debug.log` + `debug/logs/interceptar_debug.log`)
depois de tentar a skill contra um bot repetidas vezes.

---

### Rodada 1 de teste real (29/09/2026, mesmo dia) — não reproduziu o sintoma principal, mas achou 2 coisas relevantes

**Erro meu antes desta rodada**: eu disse ao usuário que o log já
estava ativo no servidor Hetzner só porque eu tinha setado
`$env:RPG_DEBUG_AOI`/`$env:RPG_DEBUG_INTERCEPTAR` na MINHA sessão local
do PowerShell — isso nunca chega no processo do `systemd` remoto, que lê
variáveis de `/opt/rpg_ecs_online/.env` (`EnvironmentFile=`, ver unit
`rpg-server.service`). Corrigido adicionando as 2 linhas nesse arquivo
+ `systemctl restart rpg-server`. Lição: setar env var local só afeta
processo local — pra afetar o serviço remoto, tem que editar o
`.env`/unit dele e reiniciar, nunca supor que "já deployei o código,
então a env var também foi junto" (são coisas diferentes).

**Análise do log real capturado** (`aoi_debug.log`, 1.79MB;
`interceptar_debug.log`, 69KB — ambos no Hetzner, sessão de ~13min):
- Caster real (eid 256, o próprio usuário) deu 18 casts de Interceptar
  no total nesta sessão — só 5 contra um alvo confirmado como bot
  (`target_is_bot=True`, eid 265), os outros 16 contra mobs/NPCs
  normais (`target_is_bot=False`).
- **Todos os 5 casts contra o bot de verdade tiveram sucesso** —
  passaram por TODAS as checagens (`INTERCEPTAR_CAST` logado, sem
  rejeição) e o `DASH_COMPLETE` correspondente mostra
  `landed_tile == target_tile` nos 5 casos, sem exceção. Na verdade,
  isso vale pros 18 casts inteiros: zero divergência entre onde o dash
  deveria pousar e onde pousou de verdade, do lado do SERVIDOR.
- Só 2 rejeições no total pro caster=256, ambas "Alvo muito próximo"
  contra um MOB (não-bot), comportamento esperado (a skill tem alcance
  mínimo de propósito).
- **Ou seja: a metade "dificilmente funciona" do sintoma relatado NÃO
  reproduziu nesta rodada** — pelo contrário, os 5/5 casts reais contra
  bot funcionaram sem nenhum problema do lado do servidor.

**Achado lateral, NÃO o que foi pedido, mas relevante**: 2 bots
diferentes (eid 265 e eid 263) tentaram usar Interceptar contra o
PRÓPRIO usuário dezenas de vezes cada (90 e 63 tentativas
respectivamente, em rajadas de ~350-370ms de intervalo, por vários
segundos seguidos) — TODAS rejeitadas com "Nenhum alvo". Isso indica
que a IA de bot (`server/bot_ai_system.py`) tem algum problema próprio
ao decidir usar Interceptar (provavelmente resolve mal o alvo antes de
mandar o pedido, ou fica re-tentando em loop sem re-avaliar a condição
que causou a rejeição da vez anterior) — bug separado, do lado da IA,
não da skill em si. Registrado aqui só como achado, não investigado a
fundo (fora do escopo do pedido original do usuário).

**Por que a 2ª metade do sintoma ("quando funciona não dá dash")
continua sem dado**: os eventos client-side de
`debug/interceptar_debug.py` (`DASH_KEEP`/`DASH_FORCED`/`DASH_START`/
`DESYNC_CORR`, mecanismo do §126) só existem no processo do CLIENTE —
`interceptar_debug.log` do servidor só tem os eventos SERVER-side
(`MOVE_REQUEST`/`DASH_OK`/`DASH_REJECTED`), confirmado por esta rodada
(zero ocorrência dos eventos client-side nesse arquivo, como esperado).
Como o usuário testa contra o servidor Hetzner remotamente, o log
client-side fica só na MÁQUINA DELE — nunca foi pedido pra ele ativar
`RPG_DEBUG_INTERCEPTAR` no PRÓPRIO `python main.py` local. Esse é o
dado que falta pra investigar a 2ª metade do sintoma.

**Próximo passo revisado**: como o servidor mostrou 100% de sucesso
nos casts reais contra bot desta rodada, a hipótese mais provável agora
é (a) o sintoma é intermitente/depende de condição não reproduzida
ainda (mais bots em tela? momento específico da partida?), ou (b) é
puramente visual do lado do cliente (servidor confirma o dash, cliente
não anima) — precisa do log LOCAL do cliente
(`$env:RPG_DEBUG_INTERCEPTAR=1; python main.py`, rodado pelo próprio
usuário) pra confirmar ou descartar (b).

---

### Achado lateral investigado a fundo (29/09/2026, mesmo dia) — bot falhando Interceptar repetidamente: causa raiz AINDA não confirmada, 2ª camada de diagnóstico adicionada

Usuário pediu pra investigar o achado lateral da rodada 1 (2 bots
tentando Interceptar dezenas de vezes cada, sempre "Nenhum alvo").

**Mecanismo do "sempre" confirmado**: `server/skill_processor.py` só
registra o cooldown de uma skill DEPOIS do handler retornar sucesso —
uma skill que falha nunca entra em cooldown. Combinado com
`BotAISystem.update` re-decidindo a cada `BOT_DECISION_INTERVAL_S`
(~350ms), um bot que falha Interceptar uma vez fica preso tentando a
MESMA skill, contra o MESMO alvo, pra sempre — nunca cai pro fallback
de auto-attack normal. Isso por si só é uma consequência que vale
corrigir (nunca deveria retry infinito sem limite), mas não é a causa
RAIZ da falha em si.

**Rastreei o código até a suspeita mais provável**:
`_resolve_target()` (`engine/skill_handlers.py`) tem 2 caminhos — (1)
usa `combat_state.target_entity_id` já setado (pelo `tid` do request,
via `server/skill_processor.py`), validando range+vida; (2) se (1)
falhar, cai num fallback de auto-seleção que só busca
mob/minion/`RemoteControlled` — e `RemoteControlled` só existe no
CLIENTE, nunca no servidor (confirmado por comentário já existente no
código, linha ~307-311). Hipótese: se o caminho (1) falhar por
qualquer razão pra um alvo PLAYER/BOT, o fallback (2) nunca o acha de
volta no servidor (sem `Enemy`/`AIControlled`/`RemoteControlled`) —
EXCETO que existe um 3º loop de fallback (linha ~325-344,
`CombatStats+CharacterStats+TileMovement`, adicionado especificamente
pra cobrir PvP no servidor) que DEVERIA achar um player/bot igual.

**2 repros sintéticos, ambos NÃO reproduziram**: (a) 2 bots parados, 5
tiles de distância, chamando `BotAISystem._try_priority_skill` de
verdade (a MESMA função de produção) → Interceptar funcionou
perfeitamente, `is_dash=True`, alvo resolvido certo. (b) mesmo cenário
com os 2 bots em pleno tween de movimento (`TileMovement.is_moving=
True`, simulando perseguição real) → também funcionou perfeitamente.
Ou seja, nem estar parado nem estar em movimento, isoladamente,
reproduz a falha.

**Decisão**: em vez de continuar chutando cenários sintéticos (viola a
regra do projeto de "pesquisar/desenhar antes de codar" — não faz
sentido escrever uma correção pra uma causa não confirmada), adicionei
1 log de diagnóstico NOVO, puro (zero mudança de comportamento), em
`_resolve_target` — `RESOLVE_TARGET_FALLBACK`, disparado só quando
HAVIA um alvo pré-selecionado (`current != -1`) que falhou a validação
(motivo exato: `fora_de_max_range` / `cs_presente_mas_hp<=0` /
`remote_sem_hp_ou_ausente`) e caiu pro fallback — nunca no caso comum
de skill sem alvo nenhum selecionado. Mostra posição e estado de
movimento de caster e alvo no instante exato da falha. Suíte
relacionada (109 testes skill/resolve_target/bot_ai + 23 de
Interceptar) passando, zero regressão. Deploy feito pro servidor de
dev — a próxima falha real de Interceptar de um bot na BG vai aparecer
em `aoi_debug.log` com o motivo exato, sem precisar de mais nenhuma
ação do usuário (o log já está sempre ativo, junto do resto da
investigação do §217).

**Não validado**: motivo real do fallback. Só será confirmado quando
o log `RESOLVE_TARGET_FALLBACK` aparecer numa sessão de BG real.

---

## 218. (movido) "Deslogar" reconectando durante restart do servidor mostrava tela de seleção de personagem VAZIA — sem perda de dado, causa raiz confirmada e corrigida (29/09/2026) — ver HISTORICO_BUGS.md §57

## 219. (movido) Mundo fica todo preto (HUD normal) ao sair da BG — RESOLVIDO: `Camera.free_mode` nunca desligava sozinho (29/09/2026) — ver HISTORICO_BUGS.md §58

## 220. (movido) Bot martelando a mesma skill contra alvo fora de alcance — mitigado com backoff local (29/09/2026) — ver HISTORICO_BUGS.md §59

## 221. (movido) Sistema de Portais (§18, Animação do tile): 2 bugs reais no 1º playtest — relógio errado quebrou ataque, animação nunca batia com o objeto (01/10/2026) — ver HISTORICO_BUGS.md §60

## 222. (movido) Fatiador de Corpos: cada tick de dano reiniciava o loop de animação (02/10/2026) — ver HISTORICO_BUGS.md §61

## 223. (movido) Corpo de mob "sobrevivia" entre instâncias de BG — cache client-side sem mapa (02/10/2026) — ver HISTORICO_BUGS.md §62

## 224. (movido) Bug real — `loot_request` crashava com `UnboundLocalError: s` quando o corpse esvaziava sem ninguém na AOI (02/10/2026) — ver HISTORICO_BUGS.md §63

## 225. (movido) Bug real — matar mob só contava pra quest de quem bateu primeiro, nunca pro resto do grupo (03/10/2026) — ver HISTORICO_BUGS.md §64

## 226. (movido) Bug real — Brado Provocativo nunca aumentava dano (talento prometia, código nunca aplicava) (03/10/2026) — ver HISTORICO_BUGS.md §65

## 227. (movido) Bug real — ícone do debuff de Brado Provocativo só aparecia 3s (classificação errada de is_buff) (03/10/2026) — ver HISTORICO_BUGS.md §66

## 228. (movido) Bug real — som e animação de usar o portal só existiam pro próprio caster (03/10/2026) — ver HISTORICO_BUGS.md §67

## 229. (movido) Bug real — skill ganha por recompensa de quest desaparecia da hotbar no próximo login (03-04/10/2026) — ver HISTORICO_BUGS.md §68

## 230. (movido) Bug real — reconectar NO MEIO do jogo nunca re-selecionava o personagem, cliente ficava "zumbi" (03/10/2026) — ver HISTORICO_BUGS.md §69

## 231. (movido) Bug real — luz do item (lampião) "apagava" pro observador remoto (AOI/mapa) e ficava dessincronizada ao entrar/sair da BG (04/10/2026) — ver HISTORICO_BUGS.md §70

## 232. (movido) Bug real — Fatiador de Corpos continuava causando dano/som depois do personagem morrer (04/10/2026) — ver HISTORICO_BUGS.md §71

## 233. (movido) Bug real — entregar "bem_vindo_guerreiro" com Avido Faseo reabria o diálogo de ACEITAR em vez de ENTREGAR (05/10/2026) — ver HISTORICO_BUGS.md §72

## 234. (movido) Bug real — `Trainer.unlock_quest_id` configurado no servidor nunca chegava ao cliente (05/10/2026) — ver HISTORICO_BUGS.md §73

## 235. (movido) Bug real — `TrainerSystem._do_learn` mandava o eid LOCAL do NPC, servidor sempre recusava como "treinador inválido" (05/10/2026) — ver HISTORICO_BUGS.md §74

## 236. (movido) Bug real — item condicional de quest (drop por jogador) perdido quando o corpse tinha outro item junto (06/10/2026) — ver HISTORICO_BUGS.md §75

## 237. (movido) Bug real — mob com loot comum raro quase nunca criava corpse, então o sorteio de item de quest condicional (70%) quase nunca rodava (06/10/2026) — ver HISTORICO_BUGS.md §76

## 238. (movido) Bug real — personagem "trava" e sofre rollback perto de um poste, num loop (06/10/2026) — ver HISTORICO_BUGS.md §77

## 239. (movido) `AttributeError: _show_ranking` ao abrir o client — crash reportado pelo usuário (06/10/2026) — ver HISTORICO_BUGS.md §78

## 240. (movido) Roda do mouse com modal de Ranking aberto também zoomava a câmera por trás (06/10/2026) — ver HISTORICO_BUGS.md §79

## 241. (movido) NPC/mob "travado" numa posição velha, nunca na posição real do servidor (06/10/2026) — ver HISTORICO_BUGS.md §80

## 242. Causa raiz de verdade do §241 — mob/NPC em ATTACKING nunca reavalia leash, fica preso lutando longe de casa pra sempre (06/10/2026)

**Sintoma relatado pelo usuário, reproduzido ao vivo**: usuário puxou
zumbis pra longe do respawn deles de propósito (teste); o NPC "Custodio
Benevide" (dador de quest "Coveiro", `maps/map_1_entities.json`) saiu
do posto pra perseguir, alcançou, e ficou preso trocando golpe com os
zumbis longe de casa, sem nunca voltar — screenshot do usuário mostra
os 3 (Custodio + 2 Zumbi) parados juntos, HP baixo dos zumbis, Custodio
com barra cheia. Usuário confirma que esse comportamento (NPC sair pra
lutar e depois voltar sozinho) sempre funcionou antes e só passou a
travar "de um tempo pra cá", e que NEM TODO mob/NPC trava — alguns
"quando chegam no limite do chasing voltam pro spawn normalmente".

**Investigação**: log direto (`debug/aoi_debug.log`, filtrado por nome)
confirmou que o servidor sempre soube a posição real do Custodio —
descartando de vez a hipótese de desync do §241. Log de combate de mob
(`debug/logs/mob_combat.log`, `RPG_DEBUG_MOB_COMBAT=1`, já ativo em
produção) mostrou Custodio (eid=34) em ciclo de `ATK_FIRE`/`ATK_BLOCK`
regular (cooldown normal, ~2s), sempre adjacente ao mesmo alvo, sempre
no MESMO tile — ou seja, ativamente lutando, não congelado por
nenhum bug de movimento/rede. `mob_tile` da luta ficava bem perto do
raio de leash (`EnemyAISystem.MAX_LEASH_RADIUS = 20` tiles) da casa
dele.

`create_quest_giver`/`create_blacksmith`/`create_trainer`
(`engine/entity_factory.py`) usam `_build_combat_entity` — MESMA
infraestrutura de combate/IA de um mob hostil ("combate genérico, Fase
1"), incluindo `AIControlled` com perseguição por proximidade. Isso é
intencional (NPC se defende/revida), não é o bug.

**Causa raiz real**: `EnemyAISystem.update()` (`engine/world_systems.py`)
tem uma checagem de leash (linha ~3838 antes desta correção, bloco
"Perseguição do Jogador" / "Leash") que deveria forçar QUALQUER mob em
`CHASING` OU `ATTACKING` a desistir e voltar pro spawn ao passar do
raio de leash — a própria condição já incluía os dois estados
(`ai_control.state in ("CHASING", "ATTACKING")`), prova de que o autor
original JÁ pretendia cobrir ATTACKING. Só que essa checagem fica
**depois**, no fluxo do código, do bloco de execução de ataque — que dá
`continue` (linha ~3809) todo tick em que o mob já está em alcance
(`in_attack_range`). Resultado: assim que o mob entra em `ATTACKING`,
ele nunca mais alcança a checagem de leash enquanto seguir em alcance
de ataque — está estruturalmente inalcançável, não é um bug de
condição errada, é um bloco de código morto por ordem de execução.

Um mob ainda `CHASING` (não chegou perto ainda) passa pela checagem
normalmente a cada tick e desiste certo ao estourar o leash — é
exatamente o "alguns voltam pro spawn" que o usuário descreveu. O
travamento só acontece quando o PASSO FINAL da perseguição pousa o mob
adjacente ao alvo no MESMO tick em que ele já passou do raio de leash:
ele vira `ATTACKING` ali mesmo (a checagem de leash daquele tick nunca
roda, porque o bloco de ataque — mais acima no código — já deu
`continue` primeiro) e a partir daí fica permanentemente imune ao
leash enquanto a luta continuar. Se os dois lados errarem o golpe com
frequência (muito "evadiu", como no caso relatado), a luta nunca
termina e o mob/NPC fica preso ali pra sempre — exatamente a
intermitência relatada pelo usuário ("não são todos que bugam").

**Fix**: checagem de leash antecipada, só para o estado `ATTACKING`,
inserida logo após `in_attack_range` ser calculado e ANTES de qualquer
tentativa de golpe (`engine/world_systems.py`, bloco novo comentado
"Leash antecipado (06/10/2026)") — reusa `engine.core_systems.
force_mob_give_up_target` (mesma função que o leash normal já chama).
A checagem original (cobrindo `CHASING`) continua INTOCADA — nenhuma
mudança de comportamento pra mob ainda perseguindo, só fecha a janela
onde `ATTACKING` nunca era reavaliado.

**Teste**: `tests/test_attacking_mob_respects_leash.py` (novo) — mob
real (`first_mob`), forçado pra `ATTACKING` adjacente ao player, com
`InitialPosition` realinhada pra simular "já perseguiu 25 tiles longe
de casa" (> `MAX_LEASH_RADIUS=20`): 1 tick de `EnemyAISystem.update()`
deve resultar em `state=="RETURNING"`/`target_eid==-1`. 2º teste
(guarda de regressão): mesmo mob `ATTACKING`, mas `InitialPosition`
perto (dentro do leash) — não pode ser afetado, continua `ATTACKING`.
Revert-to-confirm: com a correção desfeita (`git stash` só do arquivo
de produção), o 1º teste falha exatamente como o bug real
(`'ATTACKING' != 'RETURNING'`), confirmando que o teste pega o
problema de verdade; restaurado, ambos passam. Suíte relacionada
(`test_enemy_ai_perf.py`, `test_teleport_breaks_mob_chase.py`,
`test_faction.py`, `test_towers.py`, `test_server.py`,
`test_minions.py`) rodada inteira: só 2 falhas pré-existentes
encontradas (`test_minions.py`, 2 testes com dado de loot
order-dependente — confirmado via `git stash` que falham IDÊNTICO sem
nenhuma mudança minha, não relacionado) + 1 teste em `test_server.py`
que dependia, por acidente, do MESMO bug de leash-nunca-reavaliado pra
passar (`test_mob_attack_generates_combat_delta_with_player_target`
teleportava um mob pra `ATTACKING` sem realinhar `InitialPosition` —
corrigido no mesmo commit, mesmo realinhamento que `tests/helpers.py::
teleport_mob_to_player` já fazia).

Deploy: fix é 100% server-side (`engine/world_systems.py`, código
compartilhado mas só o SERVIDOR roda `EnemyAISystem` de verdade —
cliente não processa IA de mob). Deployado direto via
`deploy_server.ps1`, sem rebuild de `.exe`.

## 243. (movido) Log de diagnóstico (do próprio §242) derrubava o servidor inteiro a cada ciclo de RETURNING (06/10/2026) — ver HISTORICO_BUGS.md §81

## 244. (movido) Causa raiz de verdade do travamento (§241/§242) — spawn do Custódio caía em cima de parede sólida, pathfinding de volta nunca podia ter sucesso (07/10/2026) — ver HISTORICO_BUGS.md §82

## 245. (movido) Mercador "Fabian Hardek" sem marcador "?" e sem reação a clique direito, depois do fix de NPCUnlock (06/10/2026) — ver HISTORICO_BUGS.md §83

## 246. Texto flutuante de dano do reflexo do Boneco de Treino aparecia 2x (07/10/2026)

**Sintoma relatado pelo usuário**: testando o boneco com `reflect_damage`
(§ ver decisão em `ARQUITETURA ONLINE.md`), o "-1" do dano refletido
aparecia duas vezes na tela.

**Causa raiz**: `ui/player_input_system.py` (CLIENTE) já faz predição
local de combate pra alguns casos (ex: mago melee sem arco equipado) —
chama `deal_damage()` DIRETO no cliente, mesma função compartilhada que
o servidor usa, pra feedback imediato antes da confirmação de rede.
Meu bloco novo de reflexão (dentro de `CombatSystem.deal_damage`) não
tinha nenhum guard de `is_server` — rodava IGUAL nessa predição local
E no servidor autoritativo. Resultado: o cliente mostrava o FLT da
própria predição local (`_emit_hit_feedback`, chamado pela chamada
recursiva de `deal_damage` dentro da predição) E DEPOIS o FLT do
broadcast de rede autoritativo (`emit_dummy_reflect` → `COMBAT_RESULT`
→ `_apply_combat_result`) — mesmo golpe, 2 textos.

**Achado relacionado, FORA de escopo (não corrigido agora)**: essa
MESMA predição client-side (`ui/player_input_system.py`, chamada de
`deal_damage` direto no cliente) provavelmente já causava o mesmo tipo
de duplicação pro dano NORMAL contra qualquer boneco de treino (não só
o reflexo) — só não era perceptível porque os 2 FLTs (predição local +
confirmação de rede) mostram o MESMO valor, na MESMA posição,
praticamente no mesmo instante, então visualmente se sobrepõem e
parecem 1 texto só. O reflexo expôs o problema porque o FLT da
predição aparece numa posição DIFERENTE (sobre o boneco) do FLT do
broadcast (sobre o player), tornando a duplicação óbvia. Vale revisitar
esse mecanismo de predição local mais a fundo depois, se o usuário
quiser — fora do pedido desta tarefa.

**Fix**: bloco de reflexão ganhou guard `self.is_server` (mesmo padrão
já usado na MESMA função pra XP de skill level — "só pode rodar no
servidor"). Cliente nunca aplica/prediz o reflexo; só reage ao
broadcast autoritativo, igual qualquer outro dano real.

**Teste**: suíte de `tests/test_training_dummy_reflect.py` já cobria a
aplicação/broadcast do reflexo via `CombatSystem(..., is_server=True)`
— continua passando com o guard (nenhum teste quebrou). 2 testes
tiveram RNG travado (`unittest.mock.patch("random.random",
return_value=0.99)`) depois de achados flaky nesta mesma investigação
— o golpe refletido passa por rolagem de acerto real (não
`pre_outcome`, que só cobre o golpe ORIGINAL), então miss ocasional do
próprio boneco (sem acerto 100% configurado) fazia o teste falhar
~40% das vezes antes da trava de RNG.

**CORREÇÃO (07/10/2026, mesmo dia)**: o guard `is_server` acima é
legítimo (evita que a predição client-side preveja/aplique o reflexo,
problema real por si só) mas **NÃO foi a causa do sintoma relatado** —
usuário testou de novo após o deploy do guard e o "-1" continuava
duplicado. Causa raiz real, só encontrada depois de reler o histórico
do projeto (usuário apontou corretamente: "isso já aconteceu antes") —
este é o **MESMO mecanismo já catalogado em `historico/ARQUITETURA
ONLINE HISTORICO.md` §34.43** ("FLT duplicado" do Escudo de
Fogo/Calamidade Flamejante, 23/07/2026), só numa 3ª categoria que a
Fase C de então não previu:

`_process_player_attacks` (`server/combat_processor.py`) detecta
dano de MOB→player por DIFERENÇA DE HP pós-tick (`mob_delta` —
"qualquer perda de HP não explicada por DoT/PvP já rastreado é um
golpe de mob não visto"), porque EnemyAISystem nunca registra seu
próprio `combat_this_tick`. O reflexo do boneco (`_on_dummy_reflect`/
`emit_dummy_reflect`, mesma indireção do Escudo de Fogo) JÁ registra
seu golpe manualmente — mas `WorldServer._damage_tracker_composite`
(o hook único que exclui dano de `mob_delta` via
`_pvp_damage_this_tick`) só fazia essa exclusão quando o **KILLER
também é player** (a Fase C original só previu PvP: Escudo de
Fogo/Calamidade Flamejante, os 2 killers eram sempre players). O
boneco não é player — então o reflexo nunca entrava em
`_pvp_damage_this_tick`, e `mob_delta` "descobria" a MESMA perda de HP
de novo, gerando um 2º `combat_this_tick` fantasma (`attacker=-1`) pro
MESMO golpe. HP só é subtraído 1x (confirmado desde o início pelo
usuário) — só o EVENTO duplicava, exatamente como em §34.43.

**Por que o 1º revert-to-confirm desta tarefa não pegou o bug**: o
teste novo (`tests/test_flt_dedup.py::TestBonecoReflectNaoDuplicaFLT`)
inicialmente usava o helper minimalista `_make_dummy` (de
`tests/test_training_dummy_reflect.py`, que monta um boneco SEM
`Enemy`/`Combatant` — suficiente pra testar `deal_damage()` direto,
mas não pro fluxo real de tick). Sem `Combatant`, o boneco nunca
entrava em `WorldServer._mob_eids`, e `_process_player_attacks`
(linha ~289, "`target_eid not in self._mob_eids`") nunca disparava o
auto-attack contra ele — o teste "passava" por motivo nenhum (nem
reflexo nem bug aconteciam) ou "falhava" por coincidência de outro mob
de fundo (mapa real carregado por `make_world_server()`) acertar o
player por acaso. Fix do teste: usar `engine.entity_factory.
create_training_dummy` de verdade (tem `Enemy`+`Combatant`) + 1 tick
"vazio" antes de medir, pro sweep periódico que popula `_mob_eids`
(`WorldServer._tick`, checa `Combatant`) já ter rodado.

**Fix real**: `_damage_tracker_composite` ganhou uma 3ª condição —
`_killer_is_reflect_dummy` (checa `TrainingDummy` no killer) — ao lado
de `_killer_is_player`, ambos levando à mesma exclusão em
`_pvp_damage_this_tick`. **NUNCA generalizar pra "qualquer killer
não-player"** — reativaria o bug original que a Fase C evitava (mob
de verdade via EnemyAISystem não registra `combat_this_tick` próprio,
depende 100% do `mob_delta` pra ser visto; excluir isso quebraria TODO
dano de mob contra player).

**Teste** (revert-to-confirm real, não só sintético): com o fix
revertido via `git stash`, o novo teste reproduziu a assinatura exata
do bug (`[{'attacker': -1, ...}, {'attacker': <dummy_eid>, ...}]`, mesmo
`damage`/`hp_after` nos 2) — confirma que é o MESMO mecanismo de §34.43.
Com o fix restaurado, suíte relacionada (`test_flt_dedup.py`,
`test_training_dummy_reflect.py`, `test_char_stats.py`,
`test_npc_service_unlock.py`, `test_combat.py`, `test_arena.py`,
`test_bg_queue.py`) — 193 testes, 0 falhas.

**Lição a levar daqui**: um ponto único de verdade cujo guard é
baseado numa suposição implícita ("só PvP passa por aqui", nunca
escrita como tal) precisa ser revisitado toda vez que uma categoria
NOVA de atacante não-player-mas-com-registro-próprio aparecer (ver
regra nova em `CLAUDE.md`, seção "Pontos únicos de verdade" —
qualquer fonte de dano que se auto-registra em `combat_this_tick`
precisa também se excluir de `mob_delta`, não só quando o atacante é
player).

**2ª CORREÇÃO (07/10/2026, mesmo dia, depois do fix do dano já estar
em produção)**: usuário testou de novo e relatou um sintoma IRMÃO —
agora é o "Errou" (miss) do reflexo que aparece 2x, não o dano. Mesma
família de bug, canal DIFERENTE: `mob_delta` só existe pra detectar
dano via DIFF DE HP — uma esquiva não muda HP, então não passa por
`mob_delta` nenhum. O canal equivalente pra miss/dodge/parry de
MOB→player é `CombatSystem.mob_avoidance_events` (`engine/
world_systems.py`, dentro do próprio `deal_damage`, drenado por
`_process_player_attacks` pra dentro de `_pending_mob_attacks` — ver
comentário original na linha ~594: "Preenchido em deal_damage;
consumido e limpo por combat_processor cada tick"). O reflexo do
boneco já se auto-registra via `emit_dummy_reflect` pra QUALQUER
outcome (hit OU miss) desde o início — mas o
`mob_avoidance_events.append(...)` (condição antiga: `if not
attacker_is_player and target_is_player`) nunca sabia disso e
alimentava o MESMO evento de novo, só que por este canal em vez de
`mob_delta`.

**Fix**: mesmo padrão da 1ª correção — condição ganhou `and not
_attacker_is_reflect_dummy` (checa `TrainingDummy` no atacante), ao
lado da condição já existente. NUNCA generalizar pra "qualquer
attacker não-player" (mesmo aviso de sempre — mob de verdade via
`EnemyAISystem` depende 100% deste canal pra miss ser visto).

**Teste** (revert-to-confirm real):
`tests/test_flt_dedup.py::TestBonecoReflectMissNaoDuplicaFLT` — força
o golpe ORIGINAL via `pre_outcome="hit"` (determinístico) e intercepta
`_calculate_damage` só quando o atacante é o boneco, forçando o
reflexo a sempre sair "miss" sem depender de RNG. Sem o fix, reproduz
exatamente a duplicata (`mob_avoidance_events` ganha a entrada
`(dummy_eid, player_eid, "miss", hp)` que já tinha sido registrada
também em `_combat_this_tick` via `emit_dummy_reflect`). Com o fix,
suíte relacionada (`test_flt_dedup.py`, `test_training_dummy_reflect.py`,
`test_combat.py`, `test_arena.py`, `test_bg_queue.py`,
`test_char_stats.py`) — 156 passed.

**Padrão a generalizar (atualizado)**: existem HOJE 2 canais de
fallback "mob bateu/errou no player e eu não vi de outra forma"
(`mob_delta` pra dano via HP-diff, `mob_avoidance_events` pra miss/
dodge/parry sem HP-diff) — qualquer atacante NOVO que não seja
player mas já se auto-registre em `combat_this_tick` (reflexo de
boneco é o único caso hoje; um mecanismo futuro parecido teria o
MESMO problema) precisa se excluir dos DOIS, não só de um. Ver regra
atualizada em `CLAUDE.md`.

**3ª CORREÇÃO — unificação estrutural (07/10/2026, mesmo dia, a
pedido explícito do usuário)**: depois das 2 correções acima, o
usuário perguntou se os 2 bugs no mesmo dia eram sintoma de falta de
coesão/modularidade — pergunta certa. Resposta: sim. As 2 correções
anteriores só ADICIONARAM guards de exclusão (1 por mecanismo de
dedução, duplicando a MESMA checagem de `TrainingDummy` em 2 arquivos
diferentes) — não resolveram a causa estrutural, só o sintoma de cada
vez. Usuário decidiu ir pela correção de verdade: eliminar os 2
mecanismos de DEDUÇÃO por completo (`mob_delta` e
`mob_avoidance_events`), fazendo TODO atacante não-player se
auto-registrar explicitamente, igual PvP/retaliation/DoT/boneco já
faziam.

**Achado-chave da investigação** (agente Explore, antes de qualquer
código): mob (via `EnemyAISystem`), torre e minion — melee E ranged —
convergem TODOS em `CombatSystem.deal_damage` (chamada direta ou via
`ProjectileSystem.update`, que também chama `deal_damage` no impacto
do projétil), exatamente o MESMO ponto que já decide o outcome
(hit/crit/block/miss/dodge/parry) pra QUALQUER atacante, inclusive o
boneco de treino. Ou seja: não foi preciso tocar nos 3 call sites de
IA/torre/minion — bastou colocar o registro explícito DENTRO do
próprio `deal_damage`, nos 2 pontos onde o outcome já é determinado
(branch de avoidance e branch de dano), pra cobrir TODOS de uma vez,
sem duplicar nada. Habilidades especiais de mob (bleed/poison/burn)
já tinham registro explícito próprio (`_ServerStatusEffectSystem.
_emit_damage`) e não foram afetadas.

**Armadilha achada DEPOIS do plano aprovado** (correção de rota
durante a implementação, não um erro no código final): o plano
original assumia que escrever DIRETO em `_combat_this_tick` (de
dentro do `deal_damage`) preservaria a ordem "ataque de mob antes do
DoT do mesmo tick" naturalmente, por "acontecer mais cedo no tick".
Falso pra ataque RANGED de mob/torre/minion — o dano de verdade só
resolve no IMPACTO do projétil
(`global_systems:projectiles`), que roda DEPOIS de
`global_systems:status_effects` (tick de DoT) no MESMO tick
(`server/world_server.py`, ordem real do `_tick()`). Escrever direto
em `_combat_this_tick` teria INVERTIDO a ordem pra ataque ranged
especificamente. Fix: o callback novo (`emit_combat_event`) continua
apendando em `_pending_mob_attacks` (a MESMA lista que mob-vs-mob já
usava, concatenada SEMPRE antes de `_combat_this_tick` em
`_collect_deltas`, independente de QUANDO no tick cada evento for de
fato emitido) — não em `_combat_this_tick` direto. Lição: nenhuma
suposição de ordem de tick entra num plano sem confirmar a ordem real
no código (`_perf_push`/`_perf_pop` da função `_tick()` é a fonte de
verdade), mesmo quando a suposição "parece" óbvia.

**Outro achado durante a implementação** (não coberto pela
investigação inicial, achado lendo o código antes de remover
`_pending_mob_attacks` por completo, como o plano original sugeria):
existe um 4º produtor desta lista, `WorldServer._log_mob_damage_hit`
— broadcast de combate MOB-vs-MOB/NPC (Sistema de Facções, Fase 5,
`attacker_eid in self._mob_eids and target_id in self._mob_eids`),
categoria TOTALMENTE separada de "mob ataca player" e fora do escopo
desta unificação. `_pending_mob_attacks` continua existindo por causa
dele — só os produtores de dedução (`mob_delta`/
`mob_avoidance_events`) foram removidos, não a lista em si.

**Remoções** (líquido: mais código removido que adicionado):
- `server/combat_processor.py::_process_player_attacks` — seção "Mob
  → Player: detectado via variação de HP" inteira (bloco `mob_delta`,
  reverse-map `_mob_attacker_of`, `_last_mob_attacker`). A detecção de
  MORTE de player por HP-diff continua (não tinha relação com
  `mob_delta`, só compartilhava o mesmo loop/snapshot).
- `engine/world_systems.py::CombatSystem` — atributo
  `mob_avoidance_events` removido; guard `_attacker_is_reflect_dummy`
  da 2ª correção removido (obsoleto); bloco de reflexo do boneco
  simplificado (não precisa mais do próprio callback de broadcast —
  a chamada recursiva já aciona o mecanismo genérico novo sozinha).
- `server/world_server.py` — `_sfx_damage_players` (dict + acúmulo em
  `_emit_damage`), `_pvp_damage_this_tick` (dict + os guards
  `_killer_is_player`/`_killer_is_reflect_dummy` em
  `_damage_tracker_composite` — o resto da função, arena tracking e
  `incr_char_stat`, intocado), `_on_dummy_reflect`/
  `_svc["emit_dummy_reflect"]` (substituído pelo mecanismo genérico).

**Adições**: `_svc["emit_combat_event"]`/`_on_mob_combat_event`
(`server/world_server.py`, mesmo padrão de `emit_retaliation`) + 2
pontos de chamada dentro de `CombatSystem.deal_damage`
(`engine/world_systems.py`, branch de avoidance e branch de dano,
ambos gated por `self.is_server and not attacker_is_player and
target_is_player`).

**Testes migrados/novos** (revert-to-confirm real em cada um —
desabilitei os 2 pontos de emissão novos, confirmei que os 5 testes
abaixo falham exatamente como esperado, restaurei):
- `test_flt_dedup.py::TestMobAtacaPlayerViaEmitCombatEvent` — NOVO,
  substitui `TestPvpDamageTrackingNaoQuebraMobDelta` (testava o
  mecanismo de dedução que não existe mais); ataque de mob real (via
  `deal_damage`) contra player gera exatamente 1 evento, hit E miss,
  com `attacker` correto.
- `test_flt_dedup.py::TestBonecoReflectMissNaoDuplicaFLT` e
  `test_training_dummy_reflect.py::test_broadcast_de_rede_registra_
  boneco_como_atacante` — migrados pra checar `_pending_mob_attacks`
  em vez de `_combat_this_tick` (mudança de lista, não de
  comportamento observável pelo cliente).
- `test_towers.py::test_torre_aparece_como_atacante_no_combat_log_do_
  player` — reescrito pra exercitar o caminho REAL (`deal_damage`) em
  vez de mutar HP na mão simulando a dedução antiga que não existe
  mais.

**Verificação**: suíte focada (`test_flt_dedup.py`,
`test_training_dummy_reflect.py`, `test_combat.py`, `test_arena.py`,
`test_bg_queue.py`, `test_char_stats.py`, `test_towers.py`) — 187
passed. `test_minions.py` tem 2 falhas pré-existentes SEM relação com
esta mudança (loot de lampião em minion morto, roteamento de
waypoint) — confirmado rodando as 2 no código original (antes desta
refatoração, via `git stash`) e vendo as MESMAS 2 falhas lá. Suíte
completa rodada antes do deploy (ver resultado no commit).

## 247. FLT do reflexo do Boneco de Treino duplicava via SKILL (não auto-attack) — 4ª ocorrência da mesma família, mecanismo DIFERENTE (07/10/2026)

**Sintoma relatado pelo usuário**, depois do §246 (3ª correção,
"unificação") já estar em produção: "mesmo sendo resolvido parte do
problema, só o ataque automático parou de duplicar o flt, qnd uso
skill ainda duplica". Ou seja: auto-attack no boneco com
`reflect_damage` — corrigido de verdade. Skill (ex: Golpe Poderoso) no
MESMO boneco — ainda duplicava.

**Causa raiz — NÃO relacionada a `mob_delta`/`mob_avoidance_events`**
(ambos já removidos no §246, nada a ver com eles aqui). É um mecanismo
TOTALMENTE diferente, mas da MESMA família conceitual: um relatório
"genérico" que varre TUDO que aconteceu durante uma janela de execução,
sem distinguir "o que a minha skill fez de propósito" de "o que
aconteceu como efeito colateral de uma chamada recursiva dentro do
mesmo handler".

`server/skill_processor.py` (skills instantâneas) e
`server/spell_completion_processor.py` (completions de cast com tempo
+ impacto de projétil — 2 call sites) montam o relatório de "quem a
skill afetou" (pra mandar em `SKILL_RESULT`) lendo
`LAST_DAMAGE_AMOUNTS`/`LAST_DAMAGE_OUTCOMES`/`LAST_EFFECTS_APPLIED`
(`engine/core_systems.py`) — dicts GLOBAIS, limpos ANTES do handler
rodar e lidos DEPOIS, populados por QUALQUER `apply_damage_core` que
disparar nesse intervalo (otimização de performance de 06/09/2026,
substituindo um snapshot O(mobs) de todo combatente do mapa — ver
comentário original no código). O problema: a chamada recursiva do
reflexo do boneco (`CombatSystem.deal_damage` → reflexo → `deal_damage`
de novo, boneco bate no CASTER) acontece DENTRO do mesmo handler da
skill, e popula os MESMOS dicts globais — o código que monta
`results_targets`/`results` então "descobre" o PRÓPRIO CASTER como um
"alvo atingido pela skill" (branch `if damage > 0:`, sem nenhuma
distinção de fonte), mesmo o caster não sendo o alvo real da skill (é
o boneco batendo de volta). Cliente (`client/network_handlers.py::
_handle_msg_skill_result`, linha ~585) chama `_apply_combat_result`
pra CADA entrada de `targets` — gerando um 2º FLT em cima do que
`emit_combat_event` (§246) já mostrou via AOI_UPDATE.

**Por que só skill duplicava e auto-attack não**: auto-attack nunca
teve esse mecanismo de relatório-por-sweep — ele registra o resultado
do PRÓPRIO golpe direto (`_combat_this_tick.append(...)` em
`_process_pvp_attack`/`_process_player_attacks`, sem nenhum sweep de
dict global). Só skill/spell têm esse relatório "genérico" — e só
porque, até o reflexo existir, NADA durante a execução de uma skill
jamais tinha causado dano de volta no PRÓPRIO caster como efeito
colateral. O reflexo é o primeiro mecanismo a fazer isso.

**Fix** — mesmo princípio nos 3 call sites (exclui o CASTER da branch
de inclusão por dano, a menos que ele seja o alvo EXPLÍCITO do próprio
cast — preserva skill de auto-dano genuína, se um dia existir, e
preserva self-buff/self-effect sem dano, tipo `cc_immune` do Fatiador
de Corpos, que nunca passava por essa branch de qualquer forma):
- `server/skill_processor.py` (~linha 577): `if damage > 0 and
  (mob_eid != player_eid or mob_eid == tid):`
- `server/spell_completion_processor.py` (completion sem projétil,
  ~linha 266, e impacto de projétil, ~linha 490): mesma lógica,
  variável `target_id` no lugar de `tid`.

**Não precisou de fix**: `_server_apply_magic_damage(...,
report=True)` (canalização tick-a-tick, ex. Calamidade Flamejante) —
essa função já é escopada por-alvo (snapshot `hp_before`/`hp_after` do
`target_id` específico passado, nunca um sweep de dict global) — não
tem como "descobrir" o caster incidentalmente. Também não aciona o
reflexo do boneco pra início de conversa (chama `apply_damage_core`
direto, nunca `CombatSystem.deal_damage`, que é onde o bloco de
reflexo mora) — magia contra o boneco hoje não reflete; ficou fora de
escopo deste fix (é uma limitação de design separada, não a
duplicação relatada).

**Teste** (revert-to-confirm real):
`tests/test_flt_dedup.py::TestSkillNoBonecoNaoDuplicaReflexoViaSkillResult`
— guerreiro usa `golpe_poderoso` (autorizado via `authorize_skill`,
raiva setada direto) contra um boneco real (`create_training_dummy`,
`reflect_damage=20`) via `_process_skill_requests()` de verdade; sem o
fix, reproduziu exatamente a duplicata (`targets` tinha 2 entradas:
`eid=player` com `damage=20` E `eid=dummy` com o dano real da skill).
Com o fix, suíte ampla (`test_flt_dedup.py`, `test_training_dummy_
reflect.py`, `test_combat.py`, `test_fatiador_de_corpos.py` — guarda
de regressão específica pro self-buff `cc_immune`, `test_taunt.py`,
`test_immunity_feedback.py`, `test_interceptar_*`, `test_faction.py`,
`test_on_kill_charge_skills.py`, `test_skill_effect_no_local_dup_
sound.py`, `test_talent_skill_*`, `test_tiro_multiplo_reticle_cache.py`)
— 300 passed.

**Lição a levar daqui**: a família de bug "relatório genérico que
varre tudo que mudou, sem saber QUEM causou cada mudança" não é
exclusiva de `mob_delta`/`mob_avoidance_events` — `LAST_DAMAGE_AMOUNTS`
é a MESMA ideia (otimização válida, mas com o mesmo ponto cego) só que
no nível de relatório de skill, não de broadcast de combate. Qualquer
mecânica FUTURA que faça uma entidade "bater de volta" ou causar dano
colateral DENTRO da execução de uma skill/handler de outra entidade
precisa ser auditada contra os 3 pontos aqui, não só contra
`emit_combat_event`.

**ATUALIZAÇÃO (07/10/2026, mesmo dia)**: usuário perguntou se a
correção acima resolvia "de uma vez" ou só a causa raiz pontual —
resposta honesta na hora: a REGRA generaliza bem pra fontes de dano
incidental futuras (não é "excluir o boneco", é "excluir o caster a
menos que seja o alvo real"), mas os 3 call sites continuavam sendo 3
blocos quase idênticos, cada um com sua própria cópia da mesma
condição — um 4º call site futuro teria que copiar de novo, não
ganhava a proteção de graça. Usuário pediu pra unificar. Fix: `engine.
core_systems.should_include_skill_damage_target(mob_eid, damage,
caster_eid, explicit_target_id)` — função única, ao lado de
`LAST_DAMAGE_AMOUNTS`/`LAST_DAMAGE_OUTCOMES`/`LAST_EFFECTS_APPLIED`
(mesmo módulo, mesmo "pacote" de mecanismo+invariante, convenção já
usada pelo projeto). Os 3 call sites (`server/skill_processor.py` e os
2 de `server/spell_completion_processor.py`) foram trocados para
CHAMAR essa função em vez de reimplementar a condição — nenhum deles
guarda mais a lógica própria. Decisão explícita de NÃO tentar unificar
o resto da lógica de cada call site (resolução de outcome, branch de
miss/dodge/parry/block que só existe em skill_processor.py, campos
específicos como `mob_slow_mult`) — essas partes genuinamente
divergem entre skill instantânea/completion/projétil, forçar numa
função só criaria uma mega-função com parâmetros condicionais difícil
de ler, pior que 3 blocos específicos. Unificar só o que É, de fato, a
MESMA regra nos 3 lugares (a decisão de inclusão por dano) — não
unificar por unificar.

**Teste**: revert-to-confirm na função nova (retorno forçado pra só
`damage > 0`, sem o guard) — `TestSkillNoBonecoNaoDuplicaReflexoViaSkillResult`
falhou exatamente como esperado, confirmando que os 3 call sites
passaram a depender de verdade da função compartilhada (não só uma
cópia local que por acaso ficou igual). Suíte ampla de skill/magia
(158 testes) e suíte completa (ver commit) sem regressão.

## 248. DoT escapa de `apply_damage_core` por completo — débito aceito ao criar o alvo de treino de arqueiro (07/10/2026)

**Contexto**: implementando o "alvo de treino exclusivo pra arqueiro"
(`TrainingDummy.accepts_only_ranged`), a investigação (agente Explore)
achou que `StatusEffectSystem._apply_tick` (`engine/core_systems.py`,
DoT — queimadura/veneno/sangramento) escreve `cs.current_hp` DIRETO
(`cs.current_hp = max(0, cs.current_hp - dmg)`), **nunca passando por
`apply_damage_core`** — o núcleo único que todo dano "direto" usa. Ele
duplica manualmente só 2 dos invariantes de lá (`is_immune`, modo
evasão) mas **nunca checou `can_engage`/facção amigável** (o guard que
gera `"blocked_friendly"`) — ou seja, DoT já ignorava a proteção de
"alvo amigável" há tempos, antes até deste alvo de arqueiro existir.

**Efeito prático pro gate novo**: o gate de `accepts_only_ranged`
(`damage_kind != "ranged"` → bloqueia) só existe DENTRO de
`apply_damage_core` — então um DoT mágico (ex: "Queimaduras Profundas",
proc de crit da Bola de Fogo) continuaria batendo no alvo de arqueiro
por fora do bloqueio, tick a tick, até expirar, mesmo que o dano
DIRETO da Bola de Fogo já seja bloqueado corretamente.

**Achado relacionado, AINDA mais fora de escopo**: `_server_bola_de_
fogo` (`server/spell_completion_processor.py`) aplica o `apply_effect(
..., "burn", ...)` do proc de crit SEM checar o retorno de
`_server_apply_magic_damage` antes — ou seja, mesmo que o dano direto
seja bloqueado (`"blocked_immune"`/`"blocked_friendly"`), o burn é
anexado do mesmo jeito. Outros handlers (ex: `_server_picada_
escorpiao`, do arqueiro) fazem o check certo
(`if outcome not in ("miss","dodge","parry") and dmg > 0:`) antes de
anexar o próprio efeito. Isso é um bug latente PRÉ-EXISTENTE,
independente do alvo de arqueiro — não corrigido agora.

**Decisão do usuário (`AskUserQuestion`, 07/10/2026)**: aceitável por
agora — "ninguém fica queimando um alvo de treino de propósito" é um
cenário raro na prática, e corrigir isso exigiria tocar no sistema de
DoT inteiro (ou no mínimo auditar todo handler mágico que aplica
efeito após dano), fora do escopo do pedido original (sprite +
alvo de arqueiro). Documentado aqui como débito conhecido, não um bug
escondido — se algum dia doer na prática (relato real de alguém
queimando o alvo de propósito), revisitar com:
1. Um gate equivalente dentro de `StatusEffectSystem._apply_tick`
   (checar `TrainingDummy.accepts_only_ranged` contra o `effect_type`/
   `DOT_SCHOOL` do efeito — `engine/core_systems.py:608`, já mapeia
   `effect_type → school`).
2. Corrigir `_server_bola_de_fogo` (e auditar os outros handlers
   mágicos) pra só anexar efeito quando o dano direto não foi
   bloqueado — bug separado, não introduzido por esta tarefa, mas
   que precisaria ser corrigido junto se o item 1 acima for feito.

## 249. Player remoto parado "olhando" pra direção errada (07/10/2026)

**Sintoma relatado pelo usuário**: entrar no meio de uma luta de outro
player (ou só olhar pra ele parado atacando um mob) mostrava o
personagem dele virado pra direção ERRADA — ex: atacando um mob à
direita mas "olhando" pra baixo.

**Causa raiz**: `TileMovement.facing` só é recalculado em 2 lugares
(`TileMovementSystem`, `engine/world_systems.py`): durante um PASSO de
movimento real, e quando a entidade está PARADA com
`CombatState.target_entity_id` setado (gira pro alvo — mecanismo já
existia desde 09/09/2026, mas só pro PRÓPRIO jogador). O 2º mecanismo
já é genérico ("roda em QUALQUER entidade com CombatState") — nunca foi
estendido pra players remotos só porque o ESPELHO local de um player
remoto (`client/remote_entity_handlers.py`) nunca recebia esse
componente. Sem ele, `target_entity_id` fica sempre -1 pro espelho, o
bloco de correção nunca dispara, e `facing` fica travado no valor do
ÚLTIMO passo de movimento real.

**Decisão do usuário**: sincronizar o alvo de combate DE VERDADE
(servidor manda quem cada player está de olho), não só reagir a golpes
confirmados — o personagem vira pro alvo SELECIONADO, igual já
acontece pro próprio jogador.

**Fix**: `WorldServer.player_target_field(eid)` (novo, mesmo padrão de
`player_faction_field`/`player_light_field`) incluído nos 5 pontos
reais que montam payload de ENTITY_SPAWN de player (achados por
`grep` em `player_faction_field(` pra ter certeza da paridade completa
— `world_server.py::_create_player_entity`/`get_players_in_aoi`,
`session.py` login + "entrou no meu AOI", `respawn_system.py`).
`_sync_player_hp_dirty` (dirty-check já existente, já usado pra
hp/max_hp/level) ganhou `target_entity_id` na tupla comparada — TROCA
de alvo depois do spawn inicial, sem sistema novo. Cliente anexa
`CombatState` ao espelho remoto e aplica o campo em
`_handle_msg_stats_update`.

**Armadilha achada e corrigida durante a implementação (não chegou a
ir pra produção)**: 3 pontos no cliente (`_handle_msg_entity_spawn`,
`_handle_msg_world_state`, AOI_UPDATE "spawned") reconstroem o dict de
spawn MANUALMENTE com uma allowlist própria de campos — exatamente a
MESMA classe de bug já documentada pra `faction`/luz de item (campo
novo do servidor nunca chega ao cliente até alguém lembrar de estender
os 3 pontos, um por um). Os 3 foram corrigidos juntos, no mesmo commit
desta feature — achado via teste automatizado que falhava silenciosamente
(`target_eid` chegava no payload do servidor mas nunca no componente).

**2ª armadilha achada e corrigida**: a primeira tentativa de resolver
"eid do servidor → eid local" usava o padrão `a.get(x) or b.get(x) or
-1` (já replicado em outros pontos do código, ex: `_apply_combat_result`
debug-only) — eid local `0` (entidade real válida, `World.create_entity`
começa em 0) é FALSY em Python, então o `or` cairia no fallback errado
pra qualquer remoto cujo eid local calhasse de ser 0. Corrigido pra
checagem explícita (`if x in dict: dict[x]`) nos 2 pontos novos — o
padrão antigo (debug-only, baixo risco) não foi tocado, mas fica
registrado aqui como risco latente se algum dia for reusado em código
que decide algo de verdade.

**3ª armadilha**: `target_eid == -1` (sem alvo) podia coincidir com
`self._my_eid` quando este ainda não foi atribuído (ex: estado inicial
de teste/conexão) — `-1 == -1` resolvia incorretamente pro PRÓPRIO
player em vez de "sem alvo". Corrigido checando `!= -1` ANTES de
qualquer comparação com `_my_eid`.

**Teste**: `tests/test_remote_target_facing.py` (15 testes) —
`player_target_field` vazio/populado; `_sync_player_hp_dirty` dispara
SÓ com troca de alvo (sem mudança de hp/level); resolução cliente
(player conhecido, mob conhecido, eu mesmo, desconhecido, eid local 0);
STATS_UPDATE seta/desmarca alvo. Revert-to-confirm no campo novo da
tupla de dirty-check — 2 testes falharam exatamente como esperado sem
ele. Suíte completa (2225 passed, 8 failed — mesma lista já conhecida
de pré-existentes/flaky desta sessão, nenhuma nova).

## 250. (movido) Contorno de seleção de alvo quase invisível — linha em cima da borda, metade escondida (07/10/2026) — ver HISTORICO_BUGS.md §84

## 251. UI de respawn automático estava acoplada à flag errada — InstanceInventoryUIState em vez de "este conteúdo revive sozinho" (07/10/2026)

**Contexto**: implementando a 1ª dungeon PvE instanciada (Aracnópolis),
que precisa do MESMO modal de respawn automático que a BG já tem
("Você morreu" + contagem regressiva, sem botão "Liberar espírito").

**Causa raiz achada**: `client/death_ui_handlers.py::_render_death_modal`
decidia "mostra respawn automático" checando
`InstanceInventoryUIState.active` — campo que na verdade significa
"progressão normalizada da BG está ativa" (nivela o jogador pro nível
1, zera equipamento/inventário real, ver `server/instance_progression.py`).
Funcionava pra BG só porque, até agora, BG era o ÚNICO conteúdo com
respawn automático — a flag nunca precisou significar as duas coisas
ao mesmo tempo. Reusar essa mesma flag pra dungeon teria 2 efeitos
errados: (a) sinalizaria "progressão normalizada" pra um sistema que
nunca chama `enter_normalized_progression`, deixando o campo
semanticamente mentiroso; (b) na pior hipótese, se algum código futuro
confiasse nesse campo pra decidir zerar o personagem, zeraria o
personagem REAL de um jogador de dungeon por engano.

**Fix**: campo novo e dedicado, `GhostState.auto_respawn`/`respawn_s`
(`engine/components.py`) — calculado em
`RespawnMixin._handle_player_death` (`server/respawn_system.py`)
checando `player_eid in self._player_dungeon_instance` OU
`self._player_bg_match_id`, nunca lendo `InstanceInventoryUIState`.
Ecoado no payload de `PLAYER_DEATH` (`auto_respawn`/`respawn_s`),
aplicado em `client/network_handlers.py::_handle_msg_player_death`.
`death_ui_handlers.py::_render_death_modal` passou a decidir por
`gst.auto_respawn` — resultado IDÊNTICO pra BG (ela também passa a
mandar `auto_respawn=True`, confirmado por regressão focada em
`tests/test_bg_queue.py`/`tests/test_arena.py`, 452 testes passando
sem nenhuma falha nova), dungeon usa o mesmo caminho sem tocar
progressão normalizada. `_render_death_modal_battleground` renomeado
pra `_render_death_modal_auto_respawn`, lendo `gst.respawn_s` do
payload em vez de importar `DEBUG_BG_RESPAWN_S` direto — cada
instância manda a própria duração (`DUNGEON_RESPAWN_S` pra dungeon,
`DEBUG_BG_RESPAWN_S` pra BG).

**2ª armadilha, achada e corrigida ANTES de qualquer deploy**:
`SpawnZone.respawnable=False` (zona de dungeon sem respawn) tinha um
bug real na primeira versão — sem um flag adicional, a zona esvaziada
caía de volta no branch de "preenchimento inicial escalonado" do
`SpawnZoneSystem` (`engine/world_systems.py`), porque esse branch usa
a MESMA condição que um spawn morto também satisfaz (`respawn_timers`
vazio + `alive < max_count`) — a zona se reenchia na hora, anulando
`respawnable=False` por completo. Corrigido com `SpawnZone.ever_filled`
(flag runtime: só permite esse branch rodar 1 vez pra zona
`respawnable=False`). Pego por teste dedicado
(`tests/test_dungeon.py::test_zona_sem_respawn_fica_vazia_para_sempre_
depois_de_morrer`) antes de qualquer commit — revert-to-confirm
reproduziu a falha exata removendo a 2ª checagem.

**Teste**: `tests/test_dungeon.py` (14 testes) — zona respawnable=True
continua reenchendo (controle), zona respawnable=False fica vazia pra
sempre e não cai de volta no preenchimento inicial, `CustomLootTable`
tem prioridade total sobre `MOB_TABLE` (Aranha tem `gold_chance=0.0`
normal, usado como sinal inequívoco de que o caminho custom rodou),
grupo cai na mesma instância/solo em instâncias diferentes, saída
remove membership e último a sair derruba a instância, desconexão
restaura mapa de origem ANTES do save, morte na dungeon respawna no
ponto de entrada (nunca `RESPAWN_TILE` global), morte fora de
dungeon/BG continua sem `auto_respawn` (fluxo manual de sempre).
Revert-to-confirm real em 2 pontos (`ever_filled` e a branch de
`CustomLootTable` em `server_death_handler.py`) — os 2 testes
relevantes falharam exatamente como esperado sem o fix, confirmado
antes de restaurar. Suíte ampla (dungeon + bg_queue + jungle_camps +
arena + loot_authoritative + map_filter + map_loader_terrain_parsing +
party + client_ui, 452 testes) — zero regressão; as únicas 2 falhas
(`test_jungle_camps.py`, já conhecidas/pré-existentes desta sessão)
não têm relação com esta mudança.

Ver `CLAUDE.md` (pontos únicos de verdade de dungeon) e
`arquitetura/ARQUITETURA ONLINE.md` (tabela de status) pro resto da
feature.

## 252. Zona sem respawn da dungeon continuava respawnando em morte real de combate (08/10/2026)

**Sintoma relatado pelo usuário, em teste real na Aracnópolis**: "matei
o último boss mais de uma vez, e outros mobs inúmeras vezes" — apesar
de toda zona da dungeon ter `respawnable=False` (§251/CLAUDE.md).

**Investigação**: reproduzi o cenário exato (não um teste sintético)
usando o `aracnopolis_dungeon_entities.json` REAL já deployado —
entrei numa instância, deixei as 41 zonas povoarem, matei TODAS via
`world.remove_entity()` direto, esperei bem além do cooldown: zero
respawn, resultado correto. Ou seja, o teste "óbvio" (mesmo padrão dos
testes automatizados já escritos pra este bug) não reproduzia o
sintoma relatado — sinal de que a causa raiz estava em outro lugar,
não no mecanismo que eu já tinha mexido.

**Causa raiz**: existe um SEGUNDO ponto, completamente separado, que
agenda respawn de `SpawnZone` — `server/server_death_handler.py`
(passo 6, "Notifica SpawnZone"), chamado em TODA morte real de
combate. Por design, é DELIBERADAMENTE redundante com o cleanup sweep
de `SpawnZoneSystem.update()` (comentário original já explicava o
motivo: "Fazemos aqui para não perder o timer quando SpawnZoneSystem
roda depois e vê before == alive" — ou seja, numa morte REAL a
entidade já foi removida ANTES do sweep de `update()` rodar, então
esse sweep NUNCA pega uma morte de combate de verdade, só entidades
que somem por outro meio). Eu só tinha colocado o guard
`if zone.respawnable:` no sweep de `update()` (o fallback) — o ponto
que de fato dispara sempre (`server_death_handler.py`) continuava
incondicional. `world.remove_entity()` direto nos meus testes originais
exercitava EXATAMENTE o caminho que tem o guard (o fallback), nunca o
caminho real de combate — por isso a suíte passava com o bug presente.

**Fix**: mesmo guard `if zone.respawnable:` adicionado em
`server_death_handler.py` antes de `zone.respawn_timers.append(...)`.

**Lição**: "matar" um mob num teste sintetizando a remoção direta da
entidade (`world.remove_entity()`) não é o mesmo caminho de código que
uma morte de combate real (`CombatStats.current_hp=0` +
`PendingDeath` + tick) — pra qualquer mecanismo ligado a MORTE de
mob/player, mapear TODOS os pontos que reagem a morte antes de
assumir que um teste sintético cobre o caminho real (mesma classe de
lição já registrada pra `on_kill` em §130/§132, agora reafirmada pro
par `SpawnZoneSystem.update()`/`server_death_handler.py`).

**Teste**: `tests/test_dungeon.py::TestSpawnZoneNaoRespawnavelViaCombateReal`
— mata o mob pelo caminho real (hp=0 + PendingDeath + `run_ticks`),
não por `remove_entity()`. Revert-to-confirm: removendo o guard,
`zone.respawn_timers` ganhava `[1.0]` em vez de ficar vazio —
reproduzido, confirmado, restaurado. Suíte focada (dungeon + jungle_camps
+ server + map_filter, 125 passed, 3 failed — mesma lista já conhecida
de pré-existentes/flaky, nenhuma nova).

Bug 2 relatado na MESMA sessão de teste (algo bateu no personagem sem
aparecer na tela, logo após entrar na dungeon e aggrar o 1º/2º mob) —
em investigação, ainda sem causa raiz confirmada; ver próxima entrada
quando fechada.

## 253. Deploy do Launcher derrubou o servidor em produção — dependência nova não instalada + porta nova sem regra de firewall (08/10/2026)

**Incidente real**: ao fazer o primeiro `deploy_server.ps1` depois de
implementar o Launcher (`server/http_api.py`, que importa `aiohttp`,
dependência NOVA adicionada só em `requirements_server.txt`), o
servidor entrou em crash-loop (`systemd` com `Restart=always,
RestartSec=5` reiniciando a cada ~7s, `ModuleNotFoundError: No module
named 'aiohttp'`) — **zero jogador conseguia se conectar** durante essa
janela, porque `deploy_server.ps1` só envia o CÓDIGO (`git archive
HEAD`), nunca instala dependências Python novas na VPS.

**Causa raiz**: `requirements_server.txt` documentado localmente não
tem efeito nenhum em produção até alguém rodar `pip install -r
requirements_server.txt` dentro do venv da VPS
(`/opt/rpg_ecs_online/venv`) — isso nunca foi automatizado, e nenhum
deploy anterior tinha adicionado uma dependência nova desde que o
servidor foi provisionado, então o gap nunca tinha aparecido.

**2º problema, achado na sequência**: mesmo depois de instalar
`aiohttp` e o servidor subir sem crashar, a porta nova
(`LAUNCHER_API_PORT=8766`) ainda não respondia de fora — `ufw status`
revelou que o firewall só libera `22/tcp` (SSH) e `8765/tcp` (jogo)
por padrão-deny; qualquer porta TCP nova que um serviço novo abre
precisa de `ufw allow <porta>/tcp` EXPLÍCITO, não é automático só por
o processo começar a escutar nela.

**Fix aplicado** (produção já restaurada, incidente fechado):
1. `ssh ... "venv/bin/pip install -r requirements_server.txt"` —
   instala `aiohttp` (e qualquer dependência nova futura) no venv real
   da VPS.
2. `systemctl restart rpg-server` — sai do crash-loop.
3. `ufw allow 8766/tcp` — libera a porta nova.
4. Confirmado fim-a-fim: `/launcher/version` (sha256 bate com o zip
   publicado) e `/launcher/login` (credencial válida aceita, inválida
   recusada) testados contra o servidor real, de fora, depois do fix.

**Regra nova daqui pra frente** (ver CLAUDE.md): toda vez que
`requirements_server.txt` ganhar uma dependência nova, rodar o `pip
install` na VPS (passo manual, igual a migração deste incidente) ANTES
ou imediatamente DEPOIS do próximo `deploy_server.ps1` que carrega
código que a importa — nunca assumir que o arquivo de requirements por
si só já propaga pra produção. Toda vez que um serviço novo abrir uma
porta TCP própria, checar/abrir no `ufw` explicitamente — a porta
escutando (`ss -tlnp`) não significa alcançável de fora.

**Lição mais ampla**: depois de QUALQUER deploy que adiciona uma peça
de infraestrutura nova (processo, porta, dependência), verificar fim-a-
fim de FORA da VPS (não só `systemctl is-active`, que não pega
crash-loop silenciosamente "ativo" entre reinícios) antes de considerar
o deploy concluído — `systemctl is-active` sozinho devolveu `active`
mesmo durante o crash-loop, porque o scheduler já tinha reiniciado o
processo de novo bem na hora da checagem.

## 254. (movido) Launcher autenticava mas o client pedia login de novo — credencial nunca repassada (08/10/2026) — ver HISTORICO_BUGS.md §85

## 255. Auto-atualização do Launcher falhava com PermissionError numa DLL compartilhada com o jogo (09/10/2026)

**Bug real relatado pelo usuário**: ao testar a auto-atualização numa
instalação de verdade (pasta renomeada só pra preservar uma "versão
antiga" pra testar contra ela), a atualização falhava com:
`[Errno 13] Permission denied:
'...\RPG_Online_0.51.1\_internal\libcrypto-1_1.dll'`.

**Causa raiz**: `release_tools/rpg_online_launcher.spec` e
`rpg_online_client.spec` usavam os dois o nome PADRÃO do PyInstaller
onedir pra pasta de dependências (`_internal`, implícito quando
`contents_directory` não é passado pra `EXE()`). `release_tools/
release_client.ps1` funde `dist\Launcher` DENTRO de `dist\RPG_Online`
(robocopy `/E`, passo 5, pedido do usuário 08/10/2026 — "quero que os
arquivos fiquem dentro da mesma pasta") — como os DOIS bundles usam o
MESMO nome de pasta (`_internal`), o robocopy sobrescreve qualquer
arquivo de dependência com nome igual entre os dois (`libcrypto-1_1.dll`
é usada tanto pelo jogo quanto pelo Launcher, via `ssl`/`hashlib`) com
a cópia de QUALQUER UM dos dois bundles — a instalação final tem uma
pasta `_internal` "Frankenstein", misturando arquivos de dependência
dos dois executáveis.

Enquanto o Launcher está ABERTO, o Windows mantém um lock nos arquivos
que ele carregou na memória — incluindo esse `libcrypto-1_1.dll`
compartilhado. Quando a auto-atualização tenta sobrescrever a cópia do
JOGO desse mesmo arquivo (mesmo PATH, já que a pasta é compartilhada),
o Windows recusa — ainda que o Launcher nunca tenha pedido pra
atualizar A SI MESMO (o zip de auto-atualização nunca contém
`Launcher.exe`, isso já era garantido desde a implementação original),
o compartilhamento de pasta criou um acoplamento indireto que o design
original não previa.

**Fix**: `contents_directory="_internal_launcher"` no `EXE()` do
`rpg_online_launcher.spec` (NUNCA em `COLLECT()` — o parâmetro é lido
de `arg.contents_directory` de uma instância `EXE`, `COLLECT()` só
repassa; passar em `COLLECT()` é aceito silenciosamente — `**kwargs`
sem validação — mas não tem efeito nenhum, o build continua saindo com
`_internal` do mesmo jeito, erro real cometido na 1ª tentativa desta
sessão, achado lendo o código-fonte do PyInstaller 6.21 diretamente
depois do build não mudar nada). Com pastas de nome diferente
(`_internal` só do jogo, `_internal_launcher` só do Launcher), o
robocopy da fusão nunca mais sobrescreve nada entre os dois bundles —
nenhum arquivo em comum, nenhum lock cruzado possível.

**Verificação**: build real com o fix, cópia isolada do resultado
marcada artificialmente como versão antiga, `Launcher.exe` REAL aberto
a partir dessa cópia (processo rodando de verdade, DLLs carregadas),
e `launcher.update_client.apply_update()` chamado contra essa MESMA
pasta com o Launcher ainda aberto — sucesso, `version.txt` virou a
versão nova, zero `PermissionError`. Reproduz o mecanismo exato do bug
relatado (Launcher aberto + sobrescrita de arquivo compartilhado),
não só um cenário sintético genérico.

**Lição**: "tudo numa pasta só" (pedido legítimo do usuário, resolve um
problema real de distribuição) e "dois executáveis PyInstaller onedir
independentes" têm um conflito estrutural que só aparece quando os dois
bundles têm alguma dependência em comum com o MESMO nome de arquivo —
nenhum teste automatizado (os existentes só validam a LÓGICA de
update_client.py com mocks/servidor de teste, nunca o PACOTE
PyInstaller de verdade) pegaria isso; só apareceu rodando contra uma
instalação real, com o Launcher de fato aberto. Qualquer native/onedir
bundle novo que precise compartilhar pasta com outro já existente tem
que usar `contents_directory` único — nunca assumir que "funcionou no
teste automatizado" cobre o empacotamento final.

## 256. (movido) Marcador de quest/correio aparecia ABAIXO do nome do NPC em jogo online real (09/10/2026) — ver HISTORICO_BUGS.md §86

## 257. Duas luzes (lampião/item) se tocando só pelas pontas pareciam formar um "anel escuro" entre elas (09/10/2026)

**Relatado pelo usuário via screenshot**: uma mancha escura, parada
num ponto fixo do mapa, aparecia exatamente entre dois postes de luz —
sumia se as duas luzes apagassem, mas não mudava de forma ao andar ao
redor (descartando sombra de linha de visão). O próprio usuário
identificou a geometria exata: "é o anel que fica em volta da luz (onde
acaba a luz de um poste) porém está dentro da luz de outro poste,
então era pra se somarem".

**Investigação**: antes de mexer em qualquer código, reproduzi a
hipótese mais óbvia (ADD de duas luzes se SUBTRAINDO/cancelando) num
script isolado, usando a `DayNightSystem` real — resultado: a soma
aditiva nunca fica mais escura que o ambiente ao redor, confirmando que
o mecanismo ADD/MULT em si está matematicamente correto (nenhuma
subtração escondida). Só depois de reproduzir a geometria EXATA
descrita pelo usuário (duas luzes cujos raios só se alcançam pela
região MAIS EXTERNA de cada uma, nenhuma perto do centro da outra) o
efeito apareceu visualmente idêntico ao screenshot.

**Causa raiz**: `DayNightSystem._get_light_add_gradient()` usava queda
LINEAR (`255 * (1 - dist/raio)`) — a metade externa do raio de
qualquer luz já contribui pouco (ex: a 80% do raio, só 20% do pico).
Quando duas luzes se tocam exatamente nessa faixa externa de ambas (sem
alcançar o centro uma da outra), a SOMA de "fraco + fraco" continua
fraca — visualmente indistinguível de um anel escuro, mesmo a soma
tecnicamente acontecendo (nunca houve bug de subtração, só pouca
contribuição dos dois lados).

**Fix**: curva de queda trocada pra `(1 - dist/raio) ** 0.4`
(`DayNightSystem._FALLOFF_EXPONENT`) — mantém o brilho alto por boa
parte do raio e só cai forte bem perto da borda de verdade. Centro
(t=0) e borda (t=1) continuam exatamente 255/0 como antes — só a forma
da transição no meio muda. Verificado visualmente ANTES de aplicar no
código real (script de comparação lado a lado, reta vs. curva nova,
aprovado pelo usuário) — e depois confirmado rodando a `DayNightSystem`
de verdade (não uma reimplementação paralela) com a mesma geometria do
bug relatado.

**Teste**: `tests/test_day_night_light_falloff.py` — centro/borda
continuam 255/0 (nenhuma regressão de contrato), ponto a 70% do raio
fica mais forte que a reta antiga, e o ponto médio entre 2 luzes
tocando-se pelas pontas soma mais da metade do pico (antes ficava bem
abaixo). Revert-to-confirm via `git stash` na curva antiga: 2 dos 5
testes falham exatamente como esperado (um deles reproduz o valor
numérico — 102 — previsto à mão antes mesmo de rodar).

**Escopo**: só `_get_light_add_gradient` (brilho ADITIVO) mudou —
`_get_warm_glow_gradient` (tingimento de cor, bem mais sutil, opacidade
máxima 90/255) continua na curva reta de propósito, fora do relato do
usuário.

## 258. O fix do §257 (expoente<1.0) criou um problema PIOR e mais comum — revertido (09/10/2026)

**Relatado pelo usuário via screenshot, minutos depois do deploy do
§257**: "não resolveu, ficou ainda mais estranho" — print mostrando uma
cerca com vários postes de luz em fileira, cada um com uma borda
CIRCULAR nítida e claramente visível, formando um padrão tipo
"diagrama de Venn" entre os círculos vizinhos. Pior que o anel escuro
original (que pelo menos era discreto).

**Causa raiz da 2ª tentativa falhar**: o §257 testou a curva
`(1-t)**0.4` só contra um PAR de luzes isolado — caso em que ela
realmente resolve o problema relatado. Mas qualquer expoente p<1.0
nessa família de curva tem uma propriedade que não foi conferida: a
DERIVADA tende a infinito quando t→1 (perto da borda verdadeira).
Na prática isso significa que a curva segura brilho alto por quase
todo o raio e então CAI QUASE NA VERTICAL nos últimos 1-2% — criando
uma borda circular nítida pra CADA luz individual. Com só 2 luzes
isoladas (o teste do §257) isso quase não aparece; com 5-6 luzes
próximas numa cena real (cerca, vila, qualquer área bem iluminada),
cada borda nítida fica visível e os círculos se sobrepõem num padrão
óbvio e artificial — exatamente o que o usuário reportou.

**Fix**: revertido `_FALLOFF_EXPONENT` de volta pra `1.0` (reta,
comportamento original). Confirmado via script de comparação (reta vs.
p=0.4 vs. 2 variantes de "plateau + smoothstep" que também foram
cogitadas e descartadas pelo mesmo motivo — qualquer coisa que force
brilho alto por mais tempo e desce rápido no fim acaba criando uma
borda visível quando várias luzes se sobrepõem) rodando uma cena com 6
luzes espalhadas, parecida com a real — reta foi a ÚNICA das 4 sem
nenhuma borda dura visível nesse teste mais realista.

**O anel escuro original (§257) continua sem solução** — decidir com o
usuário se vale a pena perseguir uma correção diferente (ex: ajustar
posição/raio específico dos postes que causam o anel na prática, em
vez de uma mudança GLOBAL na curva de queda) ou se é um artefato menor
aceitável, já que a correção global tentada custou mais do que valeu.

**Teste**: `tests/test_day_night_light_falloff.py` reescrito —
(1) pino explícito em `_FALLOFF_EXPONENT == 1.0` com docstring
apontando pra esta seção; (2) teste CURVA-AGNÓSTICO que amostra o
gradiente em passos finos (1% do raio) do centro até quase a borda e
verifica que o ÚLTIMO passo (mais perto da borda) não é muito mais
íngreme que a média dos passos anteriores — pega QUALQUER curva futura
com essa mesma característica de "derivada explode perto da borda",
não só um expoente específico. Revert-to-confirm: com p=0.4
reintroduzido à mão, os 2 testes falham (ratio medido ~6x o passo
médio, bem acima do limite de 3x) — restaurado.

**Lição mais ampla, a real desta vez**: testar uma mudança de curva de
luz contra um PAR isolado não é suficiente — o caso que IMPORTA de
verdade pra qualidade visual é uma cena com VÁRIAS luzes próximas ao
mesmo tempo (o caso comum, não o raro). "Reproduzi o cenário antes de
aplicar" (regra já existente no CLAUDE.md) precisa ser interpretado
como "reproduzi o cenário REPRESENTATIVO", não só "reproduzi uma
versão simplificada que toca na mesma função" — a simplificação em si
pode esconder exatamente o efeito colateral que mais importa.

## 259. Auto-atualização do Launcher apagava o config.json do jogador (09/10/2026)

**Pergunta do usuário, não um bug observado em jogo**: "quando
atualizamos o jogo pelo launcher, o arquivo config também é
substituído?" — investigação confirmou que sim, e que isso era um
problema real ainda não notado.

**Causa raiz**: `release_tools/build_client.ps1` grava um
`config.json` NOVO e PARCIAL em todo build (só `server_host`/`porta`/
`use_tls`/`scale`/`ui_scale`/`music_volume`/`sfx_volume` — o resto vem
dos `DEFAULTS` de `config.py` em runtime). Esse arquivo vai dentro do
zip de auto-atualização igual qualquer outro. `launcher/
update_client.py::apply_update()` fazia só `zipfile.ZipFile.
extractall(install_dir)` — extração de arquivo SUBSTITUI o arquivo
inteiro, nunca mescla conteúdo JSON. Resultado: toda atualização
aplicada por cima de uma instalação existente apagava o `config.json`
REAL do jogador (hotbar configurada, atalhos de menu customizados,
modo de janela, ajustes do F12) e — como o Launcher usa o MESMO
arquivo/mesma pasta (`config.py::CONFIG_FILE` resolve pelo diretório
do executável, e Launcher.exe/RPG_Online.exe moram juntos desde a
fusão de pastas, ver §255) — também apagava o login lembrado no
próprio Launcher.

**Fix**: `apply_update()` passa a pular `config.json` da extração
SE já existir um na pasta de instalação (update real) — só deixa o
`config.json` do zip chegar quando NÃO existe nenhum ainda (1ª
instalação). Resto do zip continua extraído normalmente.

**Teste**: `tests/test_launcher_update_client.py::TestApplyUpdate` —
`test_preserva_config_json_existente_do_jogador` (config.json do
jogador intacto depois do update, resto do zip aplicado normalmente)
+ `test_instalacao_nova_sem_config_json_recebe_o_do_zip` (1ª instalação
continua recebendo o config.json do zip). Revert-to-confirm via `git
stash`: sem o fix, o 1º teste falha reproduzindo o bug exato
(config.json do jogador vira o do zip) — restaurado.

## 260. (movido) Fechar o jogo nunca avisava o servidor — login novo batia em "already_online" (09/10/2026) — ver HISTORICO_BUGS.md §87

## 261. (movido) Aranha da Aracnópolis batia 3-8 de dano em vez de ~256-260 — fix real estava no working tree, nunca foi deployado (09/10/2026) — ver HISTORICO_BUGS.md §88

## 262. Relogar sempre jogava o personagem de volta pra dentro da dungeon (10/10/2026)

**Relatado pelo usuário**: personagem (Xadread) aparecia DENTRO da
Aracnópolis Dungeon, no ponto de entrada dela, toda vez que relogava.
Confirmou que começou depois de ter desconectado enquanto ainda estava
dentro da dungeon.

**Investigação**: consultei o banco de produção pro personagem real —
`map_id='maps/map_1.csv'`, `tile=(137,411)`. Primeira vista parecia
contradizer o sintoma (mapa salvo é o mundo aberto, não uma instância
sintética) — mas `(137,411)` é EXATAMENTE a transição com
`"dungeon_key": "aracnopolis"` em `map_1_entities.json`. O personagem
estava salvo em cima do próprio tile que leva pra dungeon.

**Causa raiz**: duas peças que isoladamente fazem sentido, mas compõem
mal:
1. `DungeonProcessorMixin.request_dungeon_enter`
   (`server/dungeon_processor.py`) grava `return_pos[player_eid]` como
   a posição do jogador NO MOMENTO em que ele pisou no tile de
   transição — ou seja, por definição, sempre o próprio tile de
   transição.
2. `end_dungeon_of` (desconexão dentro da dungeon) teleporta o jogador
   de volta pra esse `return_pos` — colocando-o de novo exatamente em
   cima do tile de transição — e isso fica salvo no personagem.
3. `game.py`, loop principal (`# --- Detecção de transição ---`): todo
   frame, se o jogador está PARADO (não `is_moving`) num tile que bate
   com `self.transition_tiles`, manda `ZONE_CHANGE_REQ` sozinho — sem
   checar se ele CHEGOU ali andando ou só estava/apareceu ali. Login
   seguinte nasce o personagem de novo nesse tile → primeiro frame já
   dispara a transição de novo → reentra a dungeon → se desconectar de
   novo, `return_pos` grava o MESMO tile de transição de novo — loop
   que se mantém sozinho pra sempre, a cada relog.

**Fix**: `game.py` ganhou `self._last_transition_check_tile` (reset em
`_build_transition_tiles`, chamado tanto no spawn inicial quanto em
toda troca de mapa) — o disparo de `ZONE_CHANGE_REQ` agora exige
`key != self._last_transition_check_tile` (o jogador mudou de tile
desde o frame anterior, ou seja, chegou andando), nunca só "está parado
em cima". Resolve a classe inteira, não só o caso de dungeon: qualquer
spawn/respawn/teleporte futuro que caia sobre um tile de transição por
acidente também fica protegido, sem precisar de guard dedicado em
`dungeon_processor.py` nem em nenhum outro ponto que teleporte jogador.

**Teste**: `tests/test_transition_requires_movement.py` — guarda por
inspeção de fonte (mesmo padrão de
`test_main_disconnect_on_quit.py`, GameEngine real demais pra montar
num teste automatizado). Revert-to-confirm via `git stash`: os 3 testes
falham sem o fix.

**Nota**: a correção destrava sozinha o personagem já preso nesse
estado (Xadread) — como `_last_transition_check_tile` começa `None` a
cada spawn, o primeiro frame depois do login nunca dispara mais,
independente de qual tile o personagem estava salvo.

## 263. Choque Térmico não funcionava na Calamidade Flamejante (10/10/2026)

**Relatado pelo usuário**: o talento Choque Térmico (dano de fogo +100%
em alvo enraizado pela Nova Congelante) funcionava em Bola de Fogo e
Calcinar, mas não em Calamidade Flamejante — mesmo a escola dela já
estando corretamente marcada como "fogo" em `skill_config.py`.

**Investigação**: `_server_apply_magic_damage` (a função que de fato
aplica o dano, chamada pelos 3 skills) só usa `school` pra aplicar
`resist_<school>` do alvo — ela não sabe nada sobre talentos de mago.
Cada talento de fogo (Pirotecnia, Crematória, Choque Térmico, Chama
Interna, Lapso Elemental) era um bloco de código escrito à mão DENTRO
de `_server_bola_de_fogo`/`_server_calcinar`, nunca dentro da função
genérica. Calamidade Flamejante usa um caminho de dano TOTALMENTE
separado (`_process_player_channeling`, tick do componente
`Channeling`) que nunca replicava nenhum desses blocos — não só Choque
Térmico, os 5 talentos de fogo geral estavam ausentes dali.

**Achado extra durante a investigação**: a descrição de "Lapso
Elemental" fala em "3 crits de **fogo** em 6s" (fogo em geral, não
"Bola de Fogo" especificamente, diferente de Queimaduras Profundas e
Exaustão, que citam Bola de Fogo na própria descrição) — mas a
implementação só existia dentro de `_server_bola_de_fogo`. Ou seja, o
Calcinar também não contava pro contador de Lapso Elemental antes
desta correção, um 2º bug real da mesma causa raiz, achado por ler a
descrição de cada talento contra onde cada `cs_flag` era checado no
código (nunca assumir que o nome do campo sozinho basta).

**Fix**: `_apply_general_fire_talent_bonus()` (multiplicadores:
Pirotecnia/Crematória/Choque Térmico) e
`_trigger_general_fire_talent_procs()` (procs pós-dano: Lapso Elemental/
Chama Interna) — `server/spell_completion_processor.py`, ponto único
chamado pelos 3 lugares que causam dano de fogo (Bola de Fogo, Calcinar,
tick de Calamidade Flamejante). Queimaduras Profundas e Exaustão
continuam FORA de propósito — a descrição dos dois cita "Bola de Fogo"
especificamente (ver CLAUDE.md pro critério de escopo: só entra no
ponto único o talento cuja PRÓPRIA descrição fala de fogo em geral).

Durante a extração também foi removido um bloco de "Chama Interna"
duplicado que sobrava no fim de `_server_bola_de_fogo` (resquício de
antes da extração — rolava a chance de proc 2x por golpe, dobrando a
chance real sem ninguém perceber porque o guard `fire_instant_ready`
mascarava o efeito na maioria dos casos).

**Teste**: `tests/test_fire_talents_general.py` — unitário determinístico
da função de multiplicador (sem RNG) + teste de wiring que reproduz o
cast real de Calamidade Flamejante (fila de skill do servidor) contra
um alvo enraizado, confirmando que a função é chamada de dentro do tick
de canalização de verdade. Revert-to-confirm via `git stash`: os 4
testes falham sem o fix (`AttributeError`, função nem existia ainda no
caminho da Calamidade).

## 264. Dois bugs irmãos achados no mesmo dia que §263 (10/10/2026)

**a) Deploy de cliente esquecido** — ao publicar o fix de §263, respondi
"server-only" olhando só pro arquivo que eu tinha acabado de mudar
(`spell_completion_processor.py`, que é mesmo só servidor). Esqueci que
`git log v0.54.14..HEAD` tinha um 2º commit no meio — um checkpoint do
próprio usuário em `content/skill_config.py` (cast_time de Bola de Fogo
1.5→1.2, mana_cost 25→15, cast_range/dmg_sp_coeff de Calamidade
Flamejante) — que ESSE sim precisa de build de cliente
(`SKILL_CATALOG` é lido por `ui/spell_system.py` pra montar a barra de
cast local, `engine/components.py::Skill._make_skill`). Resultado: um
tester jogando pelo executável empacotado continuou vendo cast_time
antigo (1.5s, "1 seg" com Bola de Fogo Aperfeiçoada 5/5: 1.5-0.5=1.0 —
bateu exato com o valor antigo, confirmando a causa). **Lição**: decidir
"precisa de cliente?" olhando só o PRÓPRIO commit que acabei de fazer é
insuficiente — preciso checar TODOS os commits desde o último
`release_client.ps1` (`git log <última tag de cliente>..HEAD`), não só o
mais recente.

**b) Calamidade Flamejante nunca concedia XP de skill Magic** —
relatado pelo usuário junto com (a). Mesma causa raiz de §263: XP de
Magic é concedido em `_process_spell_cast_completions` (cast único de
Bola de Fogo/Calcinar/Nova Congelante — deduz mana 1x, chama
`grant_skill_xp(..., "magic", 1)` no mesmo golpe), mas Calamidade
Flamejante deduz mana por TICK dentro de `_process_player_channeling`
— o mesmo caminho separado de §263, e de novo nunca replicava um
mecanismo que só existia no caminho de cast único. Fix: mesmo
`grant_skill_xp` chamado dentro do bloco de dedução de mana por tick,
mesma regra (custo efetivo de mana > 0, independente de acerto/dano).
Teste: `tests/test_channeling_grants_magic_skill_xp.py`, revert-to-
confirm.

**Padrão a generalizar**: `_process_player_channeling` (Calamidade
Flamejante) é um caminho de dano/custo SEPARADO de
`_process_spell_cast_completions` (cast único) — qualquer mecanismo
NOVO adicionado a skills de cast único (talento, concessão de XP,
proc, etc.) precisa ser checado explicitamente contra ESSE caminho
também antes de considerar "pronto pra todo mago", não só os 2 cast
únicos de fogo. Ver CLAUDE.md.

## 265. (movido) `tools/png_to_dungeon.py` gerava um CSV que o jogo nunca lia — câmera continuava travando na borda mesmo com mapa maior (10/10/2026) — ver HISTORICO_BUGS.md §89

## 266. (movido) Jogador nascia dentro de parede ao entrar na dungeon regenerada — o `"player"` do `_entities.json` da dungeon é só informativo (10/10/2026) — ver HISTORICO_BUGS.md §90

## 267. Sistema de animação de mob implementado, nameplate ficou no meio do sprite — dois caminhos de desenho separados pra mob LOCAL vs. REMOTO (10/10/2026)

**Contexto**: implementei `engine/mob_animation.py` (animação idle/run/
attack de mob, espelhando `character_animation.py` mas com frame
QUADRADO derivado da altura do sheet, ver CLAUDE.md). Encaixei o
desenho do sprite em `ui/render_system.py` (branch `_mob_frame`,
funciona — confirmado pelo usuário, Goblin Guerreiro aparece animado) e
o topo do nameplate no MESMO arquivo, no bloco de HUD gated em
`combat_stats`.

**Relatado pelo usuário**: nameplate continuava no meio do personagem,
"da mesma forma que estava antes" — mesmo depois do 1º fix.

**Causa raiz**: todo mob de verdade em produção (multiplayer online,
sem versão offline) é um mob REMOTO do ponto de vista do cliente — e
`client/remote_entity_handlers.py::_spawn_remote_mob` REMOVE
`CombatStats` dele de propósito (`"HP é autoritativo pelo servidor,
RemoteEntityMeta.hp"`). Isso significa que o bloco de HUD de
`ui/render_system.py` que eu editei (`_draw_hp_bar = combat_stats and
...`) NUNCA RODA pra mob de verdade — é código morto nesse cenário. O
nameplate real de mob/NPC remoto é calculado por uma função TOTALMENTE
SEPARADA, `client/remote_entity_handlers.py::_draw_mob_hp_bars`, que
ainda não sabia nada sobre sprite animado e caía no fallback de
retângulo antigo (`pos.y - W/2`, `W=TILE_SIZE-4=28` fixo) — bem abaixo
do topo real do frame de 64px do Goblin Guerreiro.

**Por que o 1º fix pareceu certo mas não era**: testei só via import +
suíte de testes existente (que não cobria `_draw_mob_hp_bars` pra mob
com sprite animado) — nunca reproduzi o cenário real (mob remoto em
jogo). Mesma lição de §261/§265/§266: "corrigido" exige reproduzir o
caminho REAL, não só confirmar que o código novo roda sem erro.

**Fix**: réplica da mesma matemática (topo do frame = base
`pos.y + renderable.height/2` menos a altura real do frame, mesmo gap
pequeno do sprite estático) dentro de `_draw_mob_hp_bars` também.

**Regra geral daqui pra frente — qualquer feature visual nova ligada a
MOB precisa ser conferida nos DOIS caminhos de desenho, nunca só um**:
- `ui/render_system.py` — entidade LOCAL (combate offline-like/debug) E
  o sprite/corpo de QUALQUER entidade com Position+Renderable
  (inclusive mob remoto — o desenho do SPRITE em si passa por aqui).
- `client/remote_entity_handlers.py::_draw_mob_hp_bars` — HUD/nameplate
  de mob/NPC remoto de verdade (o caminho que roda em produção, já que
  não existe mob 100% local em multiplayer).
Essa duplicação replica o padrão já conhecido de
`_draw_remote_players` vs. o bloco de player local no mesmo arquivo
(`ui/render_system.py`, comentário já existente linha ~933-936) — não
é a primeira vez que "local" e "remoto" têm caminhos de desenho
gêmeos, mas separados, nesse arquivo. Teste novo em
`tests/test_nameplate_anchor.py::TestDrawMobHpBarsAncoraMobAnimado`
usa o sprite REAL do Goblin Guerreiro (não só fixture sintética) pra
fechar esse gap.

## 268. Nameplate de mob corrigido em §267, mas ainda flutuando longe da cabeça — frame quadrado tem padding transparente real (10/10/2026)

**Relatado pelo usuário**: depois do fix de §267 (nameplate não cai
mais no fallback de retângulo), "está muito alto, consegue colocar o
nameplate dentro do frame?" — com screenshot mostrando um vão grande
entre o nome/barra de HP e a cabeça do Goblin Guerreiro.

**Causa raiz**: o fix de §267 ancorava o nameplate no topo CRU do frame
(`position.y - anchor_dy - GAP`), assumindo (sem medir) que o sprite de
mob é desenhado "quase sem padding" — suposição errada. Medindo o
sprite real (`assets/sprites/goblin_guerreiro_idle_down.png`, frame
64×64) com `pygame.mask.from_surface().get_bounding_rects()`: o
conteúdo opaco só começa na linha ~13 — ou seja, quase 1/5 do frame é
padding transparente no topo, de propósito ou não (frame QUADRADO
"sobra" espaço que a silhueta real do goblin não precisa preencher).

**Fix**: `engine.mob_animation.get_mob_top_opaque_offset(mob_key)` —
mesma técnica de `TileSpriteManager.get_top_opaque_y` (sprite
estático), aplicada ao MÍNIMO entre todos os frames do estado "idle"/
"down", cacheada por mob_key (1x, nunca recalculada pela pose
exibida agora — evita "balançar" entre frames, mesmo motivo do
`NAMEPLATE_CHAR_FRAME_TOP_INSET_PX` do personagem ser um valor FIXO em
vez de escanear pixel real por pose). Diferença pro personagem: ali o
valor é um número universal (só 3 classes humanas, proporção uniforme
o bastante pra um número fixo servir pras 3); aqui é DERIVADO da arte
real de cada mob individualmente, porque a proporção varia demais
(rato a dragão) pra um único número fixo fazer sentido.

Os DOIS caminhos de desenho (§267) precisaram do mesmo ajuste —
`ui/render_system.py` (branch de HUD, dead code em produção mas
mantido consistente) e `client/remote_entity_handlers.py::
_draw_mob_hp_bars` (o caminho real). Testes em
`tests/test_nameplate_anchor.py` atualizados pros dois (`
TestRenderSystemAncoraMobAnimado`/`TestDrawMobHpBarsAncoraMobAnimado`),
comparando contra o resultado "ingênuo" (sem o ajuste de pixel opaco)
pra garantir que a regressão sozinha não volta a passar despercebida.

## 269. Tile de colisão do mob ancorado pela base do frame, deveria ser o centro (10/10/2026)

**Relatado pelo usuário**: "o tile de colisão do goblin está na base do
frame ou seja na posição 16, 32, porém deveria ser o tile do centro,
posição 16,16".

**Leitura do relato**: pensando no frame 64×64 do Goblin Guerreiro como
2×2 tiles de 32px, um tile de 32×32 posicionado em `(16,32)` (canto
superior esquerdo) ocupa o QUADRANTE INFERIOR do frame (colunas 16-47,
linhas 32-63) — é aí que `ui/render_system.py` ancorava o sprite antes
deste fix. O usuário queria o tile em `(16,16)`, que ocupa o QUADRANTE
CENTRAL (colunas 16-47, linhas 16-47) — o CENTRO geométrico do frame
inteiro.

**Causa**: `_mblit_y = draw_y + renderable.height/2 - mh` ancorava a
BASE do frame perto da base do retângulo antigo (convenção herdada do
fallback de retângulo colorido, nunca repensada pra sprite animado).
Isso deixava `position.y` mapeado pra perto do quadrante inferior do
frame, não pro centro.

**Fix**: `_mblit_y = draw_y - mh/2` — ancora o CENTRO do frame inteiro
em `position.y` (e `_mblit_x = draw_x - mw/2`, já correto, centro
horizontal). Generaliza pra qualquer tamanho de frame (artista desenha
o personagem centrado no canvas, sem precisar calcular quantos tiles de
altura o sprite tem) — mesma filosofia do personagem, que ancora pelo
CENTRO do tile de colisão de 32×32, nunca pela base/pé (ver
`character_anchor_offset` em CLAUDE.md).

Os 2 caminhos de nameplate (§267/§268) dependem do mesmo
`_mob_anchor_dy_for_hud`/cálculo equivalente — atualizados junto
(`mh/2` em vez de `mh - renderable.height/2`), senão o nameplate
ficaria consistente com a âncora ERRADA de novo.

## 270. Mob perseguindo "pisca" pra idle no meio do run — `anim_walk_grace_s` ignorado (10/10/2026)

**Relatado pelo usuário**: "quando o mob está me perseguindo, no meio
das animações de run, parece que dá uma quebrada talvez volta para a
animação de idle e animação parece bugada".

**Causa raiz**: `engine/mob_animation.py::resolve_mob_anim_state`
(criado junto com o sistema, mesmo dia) só checava
`tile_movement.is_moving` — a docstring original (ERRADA) dizia que
`anim_walk_grace_s` era "um detalhe de timing específico do
`PlayerInputSystem` local" e que mob não precisava dele. Falso:
`TileMovementSystem` (`engine/world_systems.py:4474`) é 100% genérico —
roda em QUALQUER entidade com `Position`+`TileMovement`, seta
`is_moving=False` e liga `anim_walk_grace_s=0.1` ao fim de TODO passo,
inclusive do espelho local de um mob remoto. A diferença real entre
player e mob não é "ter ou não o grace" — os DOIS têm — é QUEM dispara
o PRÓXIMO passo: pro player local, é `PlayerInputSystem` no frame
seguinte (gap de ~1 frame, ~16ms, bem dentro dos 100ms de graça); pro
mob remoto, é o próximo pacote `ENTITY_MOVE` da rede chegando (gap
real de rede, pode facilmente passar de 1 frame sob qualquer jitter) —
sem o grace, CADA passo de uma perseguição contínua passava por pelo
menos 1 frame "idle" entre o fim de um tween e o início do próximo,
visível como a "quebrada"/"bugada" relatada.

**Fix**: `resolve_mob_anim_state` agora delega 100% pra
`character_animation.resolve_anim_state` (já cobre `anim_walk_grace_s`
corretamente, é a MESMA função que o player usa) — eliminada a
reimplementação paralela que causou o bug. Teste novo em
`tests/test_mob_animation.py` reproduz o cenário exato (passo termina,
`is_moving=False` + grace ligado, confirma que o estado continua
"run") e também cravado contra `character_animation.resolve_anim_state`
pra NUNCA mais divergir silenciosamente.

**Lição**: a suposição "mob não passa por esse caminho" nunca foi
verificada contra o código de verdade — só assumida por analogia
("mob tem IA/rede, não input direto"). `TileMovementSystem` já era
conhecido como genérico (documentado em vários outros pontos do
CLAUDE.md), mas a suposição específica sobre ESTE campo não foi
checada antes de escrever `resolve_mob_anim_state` do zero em vez de
reusar a função já existente — mesma classe de erro que a regra
"pesquisar antes de codar" existe pra evitar.

## 271. (movido) Animação de morte de mob — feature nova, não um bug (11/10/2026) — ver HISTORICO_FEATURES.md §44

## 272. (movido) Elipse de loot trocada pela animação de loot.png, vazio não desenha mais nada (11/10/2026) — ver HISTORICO_FEATURES.md §45

## 273. Mob parado atacando continuava "olhando" pra direção errada quando o alvo mudava de lado (11/10/2026)

**Pedido do usuário**: "quando o mob está parado me atacando e eu mudo
de posição... ele continua com a animação de idle e ataque para
baixo" — exatamente o mesmo bug já corrigido pro player remoto em
07/10/2026 (`tests/test_remote_target_facing.py`), só que nunca
estendido pra mob.

**Pesquisa feita antes de codar** (agente dedicado): confirmou que
`TileMovementSystem` (`engine/world_systems.py:4642-4670`) já vira
`facing` pro alvo quando uma entidade PARADA tem `CombatState.
target_entity_id != -1` — mecanismo 100% genérico, o comentário no
código já dizia "mob/NPC ainda não têm sprite direcional, então fica
inofensivo neles por enquanto" (escrito ANTES do sistema de animação
de mob existir). A causa raiz real: mob tem `AIControlled.target_eid`
(usado pela IA pra decidir quem atacar), mas NUNCA escreve
`CombatState.target_entity_id` — e nada no servidor nunca mandava esse
dado pro cliente de jeito nenhum (confirmado: nenhum campo
`target_eid` existia em `_build_mob_spawn_payload`, nenhum canal
equivalente a `_sync_player_hp_dirty` existe pra mob).

**Decisão de desenho**: em vez de inventar um mecanismo novo de
dirty-check (cache por mob + comparação a cada tick), reusei o padrão
JÁ EXISTENTE de `_collect_mob_effects` — snapshot completo a cada tick
(`{str(mob_eid): target_eid}`, só mobs com alvo ativo), já encaixado
dentro do MESMO `deltas` dict que `mob_effects` já usa. Isso evitou
2 riscos: (1) não precisei tocar na lógica de movimento/ENTITY_MOVE
(`_moved_this_tick`), que o próprio cliente lê com `m["tx"]`/`m["ty"]`
SEM `.get()` — um entry de "só mudou o alvo, sem mover" quebraria com
`KeyError`; (2) `mob_effects`/`mob_targets`, por já estarem dentro do
`deltas` dict checado em `any(deltas.values())`, não precisam de
nenhuma entrada nova em `has_pending` (`server/session.py:2919-3000`)
— JÁ cobertos de graça, mesma lição de "Atualização coesa" do CLAUDE.md
mas sem precisar aplicá-la de novo aqui.

**Detalhe de integração quase perdido**: `server/session.py::
_merge_deltas_into` trata `"effects"`/`"mob_effects"` como "foto do
estado atual" numa regra de merge SEPARADA (substitui, nunca
concatena) — só notei isso lendo o código de verdade antes de
assumir que bastava adicionar a chave no `deltas` principal. Sem
incluir `"mob_targets"` nessa MESMA regra, um dispatch acumulado de
2+ ticks (quando o anterior ainda está em voo) perderia o campo
silenciosamente — não cairia em NENHUM dos 2 ramos de merge
existentes (nem lista de evento, nem a tupla de snapshot).

**Fix implementado**:
- `WorldServer._collect_mob_targets()` (snapshot, `server/
  world_server.py`) + `target_eid` em `_build_mob_spawn_payload`
  (cobre o momento do spawn/entrada no AOI).
- `server/session.py::_build_update_for_session` recorta o snapshot
  global por `session.known_eids` (mesmo filtro de AOI que
  `mob_effects` já usa) antes de mandar pro cliente — nunca manda
  alvo de mob fora da visão de ninguém.
- `server/session.py::_merge_deltas_into` — `mob_targets` entrou na
  mesma regra de substituição de `effects`/`mob_effects`.
- Cliente: `_spawn_remote_mob` (`client/remote_entity_handlers.py`)
  passa a anexar `CombatState` ao espelho do mob (NUNCA tinha antes),
  resolvendo `target_eid` via `_resolve_server_eid()` (helper NOVO,
  único ponto de resolução eid-servidor→eid-local, usado pelos 2
  pontos novos — os 2 já existentes do player continuam com a cópia
  inline deles, não mexidos por escopo: risco desnecessário tocar
  código já correto e testado só pra eliminar duplicação). `
  _sync_mob_targets` (novo) aplicado em TODO `AOI_UPDATE`, mesmo
  padrão de `_sync_mob_effects` — limpa o alvo (volta pra -1) de
  qualquer mob que o servidor PAROU de reportar, nunca deixa um alvo
  antigo grudado.

**Teste fim-a-fim real** (não só sintético por partes):
`tests/test_mob_target_facing.py::
TestFacingRecalcFuncionaParaMobComCombatState` reproduz o cenário
EXATO relatado — mob parado, alvo embaixo dele (facing="down" bate),
alvo anda pro lado direito, `TileMovementSystem.update()` roda de
novo (sem o mob se mexer) e `facing` vira "right" sozinho — prova que
o mecanismo genérico (zero lógica de giro nova escrita) funciona pra
mob exatamente como já funciona pro player. Demais 14 testes cobrem
cada peça isoladamente (collect/payload/merge/resolve/spawn/sync).

## 274. "Voltar ao Spawn" dentro de uma BG puxava o player pra fora da instância (11/10/2026)

**Pedido do usuário**: "quando o player clica em 'Voltar ao Spawn' no
menu principal, ele volta para o spawn do player fora da BG, precisamos
bloquear esse botão dentro das instâncias" (plural — pedido já geral,
não só BG).

**Causa raiz**: `_handle_unstuck` (`server/session.py`, handler de
"Voltar ao Spawn") só verificava `player_eid in self.world_server.
_player_match_id` (Arena) antes de decidir se trocava de mapa pro
spawn principal — igual ao comentário já existente no código dizia
("nunca faz isso dentro de uma partida de Arena"), mas o projeto tem
**4 tipos de instância**, não 1: Arena (`_player_match_id`), fila REAL
de BG (`_player_bg_match_id`), BG de teste `/testbg`
(`debug_battleground._state["members"]`) e dungeon
(`_player_dungeon_instance`) — cada um com seu próprio dict,
confirmado lendo `server/world_server.py` (linhas ~470/509/523) e
`server/debug_battleground.py`. Um player numa partida de BG real
passava o check de Arena (False) sem problema, caía no branch de
"mapa não-principal, troca pro principal" (já que o mapa da BG é
diferente de `_map_file`) e era puxado pra fora.

**1ª tentativa de design, corrigida pelo usuário**: a proposta inicial
incluía também mexer em `_handle_release_spirit` ("Liberar espírito"),
achando que tinha a MESMA lacuna (ela de fato tem — só verifica Arena
também). O usuário corrigiu: "quando o player morre dentro da BG ou da
dungeon ele não aparece fora da dungeon, ele aparece no spawn inicial
da instância... pesquise melhor antes de acabar criando algo que já
funciona." Pesquisa confirmou: BG/dungeon têm respawn AUTOMÁTICO
(`GhostState.auto_respawn`, calculado em `RespawnMixin.
_handle_player_death` como `_in_dungeon_rs or _in_bg_rs`) — o CLIENTE
nem desenha o botão "Liberar espírito" nesse caso
(`client/death_ui_handlers.py::_render_death_modal_auto_respawn`, só
mostra contagem regressiva). `_handle_release_spirit` continua com a
lacuna em TEORIA, mas é inalcançável na prática pra BG/dungeon — não
precisa de fix nenhum ali. **Lição**: "esse código tem o mesmo padrão
de outro código com bug" não é prova de que os dois têm o MESMO bug —
precisa confirmar que o caminho é de fato alcançável antes de tocar,
nunca assumir simetria só pela forma do código.

**Fix**: `WorldServer.player_in_any_instance(player_eid)` (novo, ponto
único) verifica os 4 dicts juntos. `_handle_unstuck` chama isso logo
depois de resolver `player_eid`, ANTES de qualquer teleporte — recusa
total com `MsgType.ERROR({"reason": "unstuck_in_instance"})` se
True. O bloco de "troca de mapa pro principal" logo abaixo (pro caso
legítimo de caverna) teve o check de `_in_arena_match_us` removido —
ficou redundante, já que o guard novo acima garante que esse ponto só
é alcançado fora de QUALQUER instância.

**Achado de quebra (bug secundário, corrigido junto)**: `MsgType.ERROR`
é mandado pelo servidor em vários pontos (cooldown de unstuck, erros de
payload, etc.) mas o CLIENTE nunca teve handler nenhum pra esse tipo de
mensagem — confirmado via grep no repo inteiro, zero ocorrência de
tratamento. A mensagem chegava e se perdia; bloquear "Voltar ao Spawn"
sem isso pareceria "o botão não faz nada", sem explicação nenhuma pro
jogador. `client/network_handlers.py::_handle_msg_error` novo (registrado
no dispatch table) mapeia `reason` pra texto amigável via
`ui/floating_text.py::WARN` (mesmo mecanismo já usado por
`_handle_msg_equip_rejected` pra "não foi possível equipar") — reason
desconhecida cai num fallback genérico, nunca mostra a string crua do
servidor.

**Testes**: `tests/test_session.py` ganhou `TestPlayerInAnyInstance` (5
casos, unitário) e `TestUnstuckBloqueadoEmInstancia` (5 casos,
fim-a-fim via `_handle_unstuck` real — um por tipo de instância +
regressão confirmando que fora de instância continua funcionando).
`tests/test_client_ui.py` ganhou 3 casos pro `_handle_msg_error` novo.
378 testes de `test_session.py`+`test_client_ui.py` rodados sem
regressão.

## 275. Deletar item da bolsa não funcionava + trade pegava item errado na quantidade (11/10/2026)

**Dois bugs relatados juntos pelo usuário, causas raiz totalmente
diferentes.**

### a) Delete não persiste

`game.py`'s handler de `K_DELETE` (dentro do bag aberto) já identificava
e removia o item certo do `Inventory` LOCAL, mas só mandava
`_send_save_state(include_fog=False)` — que o servidor **ignora** de
propósito pra persistência (`server/session.py::_build_save_merge`
sempre prioriza `live_inventory`, o `Inventory` AO VIVO do servidor, por
cima de qualquer coisa que o client mande via SAVE_STATE — decisão
arquitetural documentada, evita uma overlay de instância vazando pro
save real). Faltava chamar `_on_loot_action("item")` (→ `INV_SYNC` →
`WorldServer.sync_player_inventory`, o único canal que de fato muta o
`Inventory` AO VIVO do servidor), mesmo padrão que QUALQUER outra
mutação de inventário já usa (ex: `_on_recarregar_changed`). Sem isso,
o item reaparecia em qualquer resync (relog, entrar/sair de instância)
— o servidor nunca soube que ele tinha sido removido.

**Fix**: 1 linha — `self._on_loot_action("item")` antes do
`_send_save_state` já existente. Confirmado que `sync_player_inventory`
já aceita payload com MENOS do item que o servidor tem (a validação de
posse, §153, só rejeita payload reivindicando MAIS do que existe) —
nenhuma mudança server-side necessária, o canal já suportava isso.

**Sem teste automatizado** — o handler fica embutido direto no loop de
eventos gigante de `game.py` (não é um método isolável), diferente do
padrão usual de mixins testáveis do projeto; simular isso de verdade
exigiria um `GameEngine` completo com pygame display. Decisão
consciente (CLAUDE.md: nível de teste decidido por mudança, não
presumido) — validação ficou pro playtest manual do usuário.

### b) Trade: modal de quantidade oferece o item errado

`client/trade_handlers.py::_open_trade_qty_modal` guarda a POSIÇÃO
(`inv_index`) do item empilhável no momento em que o modal abre (Shift+
clique). O problema: se QUALQUER outra oferta do MESMO jogador for
confirmada pelo servidor (`TRADE_STATE`) enquanto o modal ainda está
aberto esperando a escolha de quantidade —
`client/network_handlers.py::_handle_msg_trade_state` remove o item
ofertado de `Inventory.items` POR ÍNDICE, deslocando todo mundo depois
dele. A posição guardada no modal passa a apontar pra outro item, mas
o clique em OK/Enter (`client/trade_handlers.py:121-124`/`159-162`)
confiava cegamente nela — o item errado ia pro slot de oferta, e o
servidor (`server/trade_processor.py::add_trade_item`) aceitava sem
checagem de identidade nenhuma (resolve só por índice).

**Fix**: `_confirm_trade_qty_modal()` (novo, ponto único usado pelos 2
gatilhos de confirmação) reencontra a posição ATUAL do MESMO objeto
`item` — já guardado por REFERÊNCIA no dict do modal desde a abertura
(`m["item"]`, nunca trocado), só não era usado pra nada até agora — via
`is` (identidade de objeto, não igualdade de valor) contra
`inv.items` no momento da confirmação. Se o item não existir mais na
bolsa (consumido/ofertado por outro caminho nesse meio tempo), cancela
em vez de ofertar algo errado.

**Teste**: `tests/test_trade_qty_modal_stale_index.py` (3 casos) —
reprodução EXATA do cenário relatado (item A ofertado, modal aberto pro
item B que desliza de posição quando a oferta de A confirma, confirmar
tem que pegar B na posição NOVA) + caso normal (nada muda) + caso
extremo (item sumiu da bolsa, cancela). `tests/test_trade_state_stack_diff.py`
+ `tests/test_client_ui.py` (262 testes) rodados sem regressão.

## 276. (movido) Mob de zona/jungle nascia sempre virado pra baixo (13/10/2026) — ver HISTORICO_FEATURES.md §46

## 277. (movido) Kit de animação customizado pra boss de dungeon (13/10/2026) — ver HISTORICO_FEATURES.md §47

## 278. "/convidar"/"/invite" só funcionava com player visível no AOI (13/10/2026)

**Pedido do usuário**: "não estou conseguindo convidar um player para o
meu grupo quando ele não está na minha AOI, mesmo usando o comando
/invite".

**Causa raiz**: `request_party_invite()` (`server/party_processor.py`)
**nunca teve** restrição de distância — por design, desde 17/07/2026,
documentado no próprio docstring do módulo ("replica o /invite de WoW,
funciona no mapa inteiro"). O bug estava 100% no CLIENTE: o comando de
chat (`client/party_handlers.py::_try_handle_party_chat_command`)
resolvia nome→eid procurando só entre `RemoteControlled` locais (players
JÁ visíveis no AOI) — alvo fora de alcance nunca era encontrado, mesmo
o servidor sendo capaz de aceitar o convite sem problema nenhum. O botão
"Convidar p/ Grupo" do modal de interação nunca teve esse problema (só
funciona em cima de alguém já clicado, logo já visível por natureza).

**Precedente encontrado**: `server/mail_processor.py::
_find_online_player_eid_by_name` já resolvia nome→eid entre TODOS os
players online (não só visíveis) — usado pra notificar indicador de
correspondência nova em tempo real. Mesmo mecanismo que Party precisava.

**Bug adicional achado ao mover a função** (nunca antes percebido): ela
filtrava por `EntityIdentity` — componente de MOB/NPC, **nunca** anexado
a player (`WorldServer.spawn_player` só dá `PlayerControlled`+
`CharacterStats`, nome do player mora em `CharacterStats.name`, mesmo
campo que `get_party_snapshot` já usa). Ou seja, a busca por nome do
correio NUNCA encontrava um player online de verdade — só não foi
percebido porque mail persiste no banco independente de notificação em
tempo real (destinatário offline vê no próximo login de qualquer
forma, o bug só matava o "sino" instantâneo). Corrigido junto, trocando
o filtro pra `CharacterStats`.

**Fix**:
- `WorldServer.find_online_player_eid_by_name(name)` (`server/
  world_server.py`, movido de `MailProcessorMixin` — passa a ser usado
  por 2 features, deixa de fazer sentido "pertencer" só ao correio;
  mesma vizinhança de outros helpers de consulta tipo
  `get_session_id_for_player`/`player_in_any_instance`). `mail_
  processor.py` atualizado pro novo nome/local.
- `PARTY_INVITE` (protocolo) ganhou campo OPCIONAL `target_name` —
  `target_eid=-1` sinaliza "resolver por nome" (`shared/messages.py`,
  `target_name` de propósito fora do schema de campos obrigatórios,
  regra já documentada ali: "opcionais não entram").
- `server/session.py::_handle_party_invite` — se `target_eid == -1`,
  resolve via `find_online_player_eid_by_name(target_name)`; não
  encontrou → `PARTY_INVITE_FAILED{reason: "not_found"}`.
- `client/party_handlers.py::_try_handle_party_chat_command` — continua
  tentando resolução LOCAL primeiro (rápido, sem round-trip, zero
  mudança pro caso comum de convidar quem já está visível); só cai pro
  fallback de rede (`target_eid: -1, target_name: <nome>`) quando não
  acha localmente. Motivo novo (`"not_found"`) mapeado pra mensagem
  amigável.

**Teste**: `tests/test_session.py::TestPartyInviteByName` (3 casos,
fim-a-fim via `_handle_party_invite` real) — reproduz o cenário EXATO
(requester em 115,389, alvo em 10,10, nenhuma AOI em comum, convite só
por nome funciona), caso "nome não existe" e regressão do caminho
`target_eid` direto (botão do modal). `tests/test_party.py::
TestFindOnlinePlayerEidByName` (2 casos) — unitário do helper
(case-insensitive, nome inexistente).
**Armadilha achada escrevendo o teste**: 2 dos 3 métodos de
`TestPartyInviteByName` usavam usernames com dígito (`pinv_req2`,
`pinv_req3`) — `_valid_char_name()` (tests/test_session.py) derruba
dígito/underscore, então "pinv_req"/"pinv_req2"/"pinv_req3" todos
derivavam pro MESMO nome de personagem ("pinvreq"); o índice único do
banco (`characters_name_lower_idx`) recusava a 2ª/3ª criação em
silêncio, deixando a conta sem personagem nenhum (`AUTH_OK` com
`characters=[]`) — teste falhava por razão totalmente alheia ao código
sob teste. Fix: usernames com letras TOTALMENTE distintas por método de
teste, nunca variações por sufixo numérico quando múltiplos logins
acontecem no mesmo arquivo/classe de teste.

## 279. Sombra "assada" no PNG de mob entrava no contorno de seleção (13/10/2026)

**Pedido do usuário**: "é possível fazer com que o contorno de seleção
ignore a sombra, que é algo que tem transparência?"

**Causa raiz** (confirmada pixel a pixel, não por suposição): a sombra
"assada" dentro do PNG dos mobs da família Aranha (idle/run/attack) usa
alpha=128 — e `_get_selection_ring_mask()` (`ui/render_system.py`)
monta a máscara via `pygame.mask.from_surface(surf)` com o threshold
PADRÃO do pygame (127), que considera "opaco" todo pixel com alpha
ACIMA do threshold. Como 128 > 127, a sombra entrava na silhueta por
pouquíssima margem — o contorno de seleção acompanhava a sombra junto
com o corpo real.

**Escopo real, medido antes de decidir o fix** (nunca assumir "é só
esse sprite"): sampleei o histograma de alpha de vários sprites reais
antes de mexer em qualquer coisa —
- Aranha/Acraxxidae: sombra alpha=128 (ACIMA do threshold — bug).
- Goblin Guerreiro: sombra alpha=89 (abaixo do threshold — já
  funcionava por acaso, nunca foi notado).
- Personagem jogável (Guerreiro): SEM sombra nenhuma (só 0/255).
- Tileset de objeto estático ("TX Village Props with Shadow"): sombra
  alpha=89 (mesmo valor do Goblin — já documentado em `ui/
  tile_sprite_manager.py::get_top_opaque_y`, que já confirmara isso
  pixel a pixel pra OUTRO propósito, topo do sprite pro nameplate de
  objeto).
Ou seja: já era um bug latente pra QUALQUER mob da família Aranha, só
ninguém tinha percebido até o boss (Acraxxidae) chamar atenção pro
contorno.

**Fix**: `RenderSystem._SELECTION_RING_ALPHA_THRESHOLD = 200` (novo) —
`_get_selection_ring_mask()` monta sua PRÓPRIA máscara-base via
`pygame.mask.from_surface(surf, 200)`, em vez de reusar
`self._get_mask(surf)` (que usa o threshold padrão, 127). **Decisão de
isolamento deliberada**: `_get_mask()`/`_occlusion_mask_cache` é
compartilhado com a transparência de oclusão (árvore cobrindo o
player) — a chave do cache é só `id(surf)`, SEM threshold; se eu
tivesse só mudado o threshold de `_get_mask()` globalmente, o cache
ficaria "contaminado" pelo primeiro threshold que tocasse aquela
Surface (oclusão ou seleção, o que rodasse primeiro), afetando o
OUTRO caso incorretamente. Separar a máscara-base do anel de seleção
num cálculo próprio (sem cache de `_get_mask`, só o resultado final —
o ANEL — continua cacheado em `_selection_ring_mask_cache`, inalterado)
elimina esse risco por completo. 200 separa com folga os 2 valores de
sombra conhecidos (89 e 128) do corpo opaco (255), sem afetar sprite
sem sombra nenhuma.

**Teste**: `tests/test_selection_outline.py::TestSelectionRingMask::
test_sombra_semitransparente_fica_fora_da_mascara_base` — sombra
sintética em alpha=128 (mesmo valor real da arte), confirma que o
pixel de sombra NÃO conta como opaco e o pixel do corpo (alpha=255)
continua contando. Testado na MÁSCARA BASE, não no anel dilatado — a
geometria do raio de dilatação tornava ambíguo qual pixel de teste
realmente provava a diferença (1ª tentativa de teste, descartada antes
de commitar: um pixel fora do raio de dilatação do corpo passaria de
qualquer jeito, bug presente ou não, porque `Mask.erase` remove da
sombra incluída do ANEL de qualquer forma — só a máscara base de fato
distingue os 2 casos). Suíte completa de seleção/nameplate/client_ui
(268 testes) sem regressão.

## 280. (movido) Frame de animação de personagem passa a ser QUADRADO (15/10/2026) — ver HISTORICO_FEATURES.md §48

## 281. (movido) Animação de cast separada da de lançamento, pra qualquer skill (15/10/2026) — ver HISTORICO_FEATURES.md §49

## 282. (movido) Projétil nasce 1 tile à frente do atacante + Bola de Fogo emite luz (15/10/2026) — ver HISTORICO_FEATURES.md §50

## 283. (movido) §282 revertido no mesmo dia: deslocamento de spawn piorava alvo diagonal — ver HISTORICO_FEATURES.md §51

## 284. (movido) Animação de cast esticada (não em loop) — reaproveitando a técnica do portal — ver HISTORICO_FEATURES.md §52

## 285. (movido) Animação de morte do player (15/10/2026) — ver HISTORICO_FEATURES.md §53

## 286. (movido) Golpe/cast em 2 fases (preparação → congela → solta) — arqueiro primeiro, mecanismo genérico (15/10/2026) — ver HISTORICO_FEATURES.md §54

## 287. (movido) Cast sem sprite dedicado nunca animava + cauda de golpe acelerada em colisão (15/10/2026) — ver HISTORICO_FEATURES.md §55

## 288. (movido) §286/§287 revertidos no mesmo dia: congelamento bloqueava a transição pra andar — ver HISTORICO_FEATURES.md §56

## 289. (movido) 1º tiro de um engajamento novo disparava instantâneo — zero tempo pra animação (17/10/2026) — ver HISTORICO_BUGS.md §91

## 290. (movido) Flecha do auto-attack nascia e já voava antes do frame de soltura tocar (17/10/2026) — ver HISTORICO_BUGS.md §92

## 291. (movido) Sprite idle-down real nas telas de seleção/criação de personagem (19/10/2026) — ver HISTORICO_FEATURES.md §57

## 292. (movido) Preview de classe em 200% (128x128) — painel de criação cresce, lista de seleção "espia" fora da linha (19/10/2026) — ver HISTORICO_FEATURES.md §58

## 293. (movido) §292 confirmado em playtest real: texto nascia em cima do sprite na lista de seleção (19/10/2026) — ver HISTORICO_BUGS.md §93

