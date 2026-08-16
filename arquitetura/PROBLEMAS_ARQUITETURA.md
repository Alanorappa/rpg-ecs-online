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

## 19. ESC não fechava os modais de PvP (10/08/2026)

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

## 22. Crash real de playtest — slot `None` do Inventory sem guard em 2 consumidores novos (11/08/2026)

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

## 24. Forjar/reciclar/aprender receita — 4 bugs reais, um deles duplicação de ouro ativa (11/08/2026)

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

## 26. Fatiar quantidade no trade + ícone por item_id (11/08/2026)

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

## 39. Bug real do talento Reciclagem — flecha reciclada sem `item_id` (12/08/2026)

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

## 40. Bug real na BG — corpo do player morto recuperava HP sozinho (level-up de instância curava quem já está morto) (12/08/2026)

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

## 41. Torre recebendo knockback do Tiro Repulsivo (12/08/2026)

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

## 42. Colisão real de 2 tiles pra torre + barra de HP no topo + origem configurável de projétil (12/08/2026)

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

## 45. Barra de XP dentro da BG mostrava a XP de fora da instância — mesmo gap de sync já fechado antes pra level/atributos/talento (13/08/2026)

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

## 46. Minimapa em tela cheia da BG — clique parou de mover o personagem + minion do mesmo tamanho que torre (13/08/2026)

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

## 47. Stealth de bush estilo MOBA na BG (13/08/2026, feature nova)

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

## 48. Monstros de jungle estilo MOBA — normal + boss (13/08/2026, retomado de sessões anteriores)

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

## 49. Bush/árvore/pedra não bloqueavam linha de visão (LOS) como parede (13/08/2026)

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

## 50. Fog assimétrico ao ficar no mesmo tile de bush (13/08/2026, achado validando §49)

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

## 51. Bushes "separadas" no mapa se fundem num blob só de qualquer forma (13/08/2026, esclarecimento — sem bug de código)

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

## 52. Pegada de visão da bush vira "só a base" — corrige a fusão de bushes separadas (13/08/2026)

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

## 54. Linha de visão de terreno entre times — nunca existia no servidor (13/08/2026)

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

## 55. Bush: visão compartilhada dentro da mesma bush + "atacar revela" (LoL-style) (13/08/2026)

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

## 56. Sair da bush não revalidava a própria visão — só o alvo "avisando" destravava (13/08/2026)

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

## 57. Visão de terreno vira universal (todo o jogo, toda entidade) + bush usa isenção da PRÓPRIA posição do viewer (13/08/2026)

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

## 58. Torre/minion do próprio time sempre visível + varredura de AOI ganha o `_can_see` que faltava (13/08/2026)

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

## 59. Visão de time vira UNIÃO de fontes independentes, estilo LoL (16/08/2026)

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