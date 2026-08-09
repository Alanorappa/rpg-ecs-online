06 de junho de 2026

Alanorappa

Análise Técnica de Arquitetura em MMORPG Python/Pygame

Identificação de problemas críticos, recomendações e diagramas de fluxo

06 de junho de 2026

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

### 🟡 C2 — Identidade de item é o nome (string)

Saves/protocolo/reconstrução usam `item.name` como chave. Renomear item no
catálogo quebra saves existentes silenciosamente.

**Melhor:** `item_id` estável no catálogo (nome vira display), com migração
única nos saves.

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

### 🆕 B6 — Fila/lifecycle de partida duplicados entre Arena e Battleground

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
→ Fase 4 (A4, inversão de persistência) → Fase 5 (A1, componentes-deus
— reforçado pelo achado A.2) → Fase 6 (B1, pipeline declarativa) →
Fase 7 (pré-lançamento — C2 com prioridade reforçada pelo achado
B.2-B.4, TLS/argon2).

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