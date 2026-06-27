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

### 🟡 PENDENTE — Sistema de quests: zero validação server-side (tamanho investigado, não corrigido)

Investigação de tamanho pedida pelo usuário (25 de junho de 2026), sem
implementar a migração ainda — só a parte de extensibilidade do schema
(abaixo) foi feita nesta rodada.

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
exibir o estado que o servidor manda, não mais calcular localmente. Multi-
sessão, não é patch — fora de escopo desta rodada, recomendado como próxima
frente de segurança/arquitetura.

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