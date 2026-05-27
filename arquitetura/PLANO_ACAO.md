# Plano de Ação — Validação da Análise Externa

> Gerado em 2026-05-26. Baseado em leitura direta do código-fonte.
> Referência: `arquitetura/Análise Externa.md`

---

## 1. Resultado da Validação

### Legenda
- ✅ CONFIRMADO — afirmação correta, problema real
- ❌ ERRADO — afirmação incorreta, o código já resolveu ou nunca foi assim
- ⚠️ PARCIAL — parte correta, parte errada

| # | Claim da análise | Status | Evidência no código |
|---|------------------|--------|---------------------|
| P1 | Combat duplication em 3 lugares | ⚠️ PARCIAL | `world_server.py` delega para `core_systems.ServerCombatStateSystem` (não há inline rage+= / combat_timer-= confirmados). Porém existem 3 implementações separadas (client `CombatStateSystem`, server `ServerCombatStateSystem`, inline checks em `_process_player_attacks`). |
| P2 | Não existe SKILL_CATALOG | ❌ ERRADO | `skill_config.py:45` — `SKILL_CATALOG` existe. `world_server.py:956` — servidor já usa. Handlers já leem do catálogo. |
| P3 | CharacterStats mistura permanente + volátil | ✅ CONFIRMADO | `embalo_charges`, `fire_instant_ready`, `thermal_shock_active`, `free_executar_charges`, `pnq_counter`, `fatiador_timer` em `CharacterStats`. |
| P4 | CombatStats 80+ campos / 26 flags de talento | ✅ CONFIRMADO | ~26 flags para builds Cavaleiro+Mago em `CombatStats`; mobs carregam esses campos sem usar. |
| P5 | AOI sweep O(N²) | ⚠️ PARCIAL | Não é estrito O(N²). É O(P×M) com cache de `_mob_positions` pré-calculado por tick. Outros dispatches (skill_results, corpses, sounds) ainda são O(eventos × sessions). Escala mal com jogadores. |
| P6 | Imports circulares críticos | ✅ CONFIRMADO | `skill_handlers.py:30` importa `systems.py`; `systems.py:5416` importa `skill_handlers.py`. |
| P7 | Lógica de negócio em lugar errado | ⚠️ PARCIAL | `damage_calculator.py` já existe (cálculo de dano/crítico extraído). Mas: servidor **não valida cooldown/custo de skill** — cheat vulnerability real. |
| P8 | Talent system 45+ linhas de if/elif | ❌ ERRADO | `apply_talent_effects()` já é data-driven: loop sobre `TALENTS` dict com `Modifier`. Não usa if/elif por talento. A proposta da análise **já está implementada**. |
| A1 | Skill validation desincronizada | ⚠️ PARCIAL | Não usa valores hardcoded (ambos usam `SKILL_CATALOG`). Mas: servidor **não verifica cooldown nem custo** antes de executar handler — cliente pode spam skills. |
| A2 | Lag compensation incompleto | ✅ CONFIRMADO | `get_snapshot_at()` existe (`world_server.py:2009`) mas nunca é chamado em `_process_skill_requests`. O servidor usa snapshot inline de tile (progress≥0.5), não timestamp do cliente. |
| A3 | Position corrections não sincronizadas | ❌ ERRADO | Outros jogadores recebem nova posição via `_moved_this_tick` → `AOI_UPDATE` (fluxo normal). O caster recebe `ENTITY_MOVE` separado por necessidade (prediction ignora própria entidade). Não há rubber-banding para outros. |
| A4 | Talent unlock logic espalhada | ⚠️ PARCIAL | Centralizado em `apply_talent_effects()`. Porém `world_server.py:1527` tem `apply_talent_effects_to_player()` com lógica duplicada para re-aplicação server-side. |
| D1 | God Object world_server.py | ✅ CONFIRMADO | 2022 linhas. `_tick()` tem múltiplas responsabilidades. |
| D2 | Acoplamento entre componentes | ✅ CONFIRMADO | ECS sem sistema de eventos — sistemas leem múltiplos componentes diretamente. |
| D3 | Pygame em skill_handlers (servidor) | ✅ CONFIRMADO | `skill_handlers.py:18` — `import pygame`. Workaround: `SDL_VIDEODRIVER=dummy` na linha 21. Servidor carrega pygame inteiro. |
| S3 | Piromancia não funciona online | ✅ CONFIRMADO | `_skill_pirofagia` e `_skill_tiro_multiplo` existem em `skill_handlers.py`. Servidor chama os mesmos handlers via `self._skill_system`. Problema real: handlers de cone dependem de state machine do cliente (hold-to-aim, release-to-fire) e podem usar `pygame.Rect`. Cone server-side precisa usar apenas `tile_move._server_dir_x/y` (infraestrutura existe em `world_server.py:1053`). |

---

## 2. Problemas REAIS não mencionados na análise

Encontrados durante a validação:

| # | Problema | Impacto |
|---|----------|---------|
| NM1 | **Servidor não valida cooldown/custo de skill** | Cheat: cliente malicioso pode remover cooldown e spam skills/heals ilimitados |
| NM2 | **max_hp autoritativo no cliente** (`session.py:81`) | Cliente envia `max_hp` e servidor aceita sem cap — possível exploit de HP infinito |
| NM3 | **`is_tile_walkable` em `move_player` sem `ignore_eid`** | Antes do fix: player snapa ao perseguir mob. Fix já aplicado mas não commitado. |

---

## 3. Plano de Ação Priorizado

### FASE 0 — Imediato (antes de qualquer feature nova)

**0.1 — Commitar fixes pendentes** `[5min]`
```
git add systems.py
git commit -m "fix(movement): allow player→enemy tile in is_tile_walkable"
git add systems.py  # ATK_FIRE CombatStats fix
git commit -m "fix(debug): restore ATK_FIRE log using CombatStats.current_hp"
```

**0.2 — Validar fix de movimento** `[10min — teste manual]`
- Abrir jogo, perseguir mob, confirmar que não há snap-back
- Registrar no PROBLEMAS_ARQUITETURA.md como resolvido

---

### FASE 1 — Crítico (falhas funcionais / segurança)

**1.1 — Cooldown server-side** `[2-3h] — SECURITY`

Servidor não valida cooldown nem custo antes de executar handler.

```python
# world_server.py — _process_skill_requests, antes de chamar handler_fn
_now = time.time()
_last = self._skill_last_used.get((player_eid, sid), 0.0)
_cd   = getattr(skill_obj, "cooldown", 0.0)
if _now - _last < _cd:
    continue  # rejeita silenciosamente
self._skill_last_used[(player_eid, sid)] = _now
# idem para custo de mana/rage
```

Critério de sucesso: usar skill, checar no log que second uso no CD é ignorado.

**1.2 — Piromancia handlers server-side** `[3-4h]`

Problema real: `_skill_pirofagia` espera state machine do cliente (hold/release).
O servidor já injeta `tile_move._server_dir_x/y` (linha 1053-1054 de `world_server.py`).

Solução: reescrever `_skill_pirofagia` para funcionar em dois modos:
- **Modo cliente** — state machine existente (hold to aim), `_server_dir_x` == 0 → inicia mira
- **Modo servidor** — `_server_dir_x/y` != 0 → executa cone diretamente

> Nota: `_skill_tiro_multiplo` é do **Arqueiro (Bardo)**, não Piromancia — mesmo problema técnico (cone),
> mas pertence à futura fase de portabilidade do Bardo, não a esta.

```python
def _skill_pirofagia(self, skill, combat_stats, combat_state, tile_move):
    # Servidor injeta _server_dir_x/y via world_server._process_skill_requests
    dir_x = getattr(tile_move, "_server_dir_x", 0.0)
    dir_y = getattr(tile_move, "_server_dir_y", 0.0)
    if dir_x == 0.0 and dir_y == 0.0:
        # Modo cliente: iniciar state machine de mira
        ...
    else:
        # Modo servidor (ou cliente após soltar): executar cone
        self._fire_pirofagia_cone(combat_stats, combat_state, tile_move, dir_x, dir_y)
```

Remover quaisquer `pygame.Rect` do handler; substituir por cálculo geométrico puro (dot product + angle).

Critério: conectar cliente Piromancia, usar Pirofagia, ver dano em mob no servidor.

**1.3 — Separar pygame de skill_handlers** `[1-2h]`

Não criar `skill_handlers_server.py` separado (esforço alto). Solução simples:
- Mover todo import de `pygame` para dentro das funções que realmente precisam dele
- Deixar o `SDL_VIDEODRIVER=dummy` como backup

```python
# skill_handlers.py — remover import pygame do topo
# Dentro de métodos que precisam de pygame (efeitos visuais cliente):
def _algum_metodo_visual(self):
    import pygame  # lazy import — servidor nunca chama isso
```

Critério: iniciar servidor sem `SDL_VIDEODRIVER=dummy` — deve funcionar.

---

### FASE 2 — Estabilidade (após Piromancia online)

**2.1 — Reset volatile fields no spawn/respawn** `[1-2h]`

Problema P3: CharacterStats persiste `embalo_charges`, etc.

Solução simples (sem criar nova classe `CombatRuntime`):
```python
# components.py — CharacterStats.__post_init__ ou método reset_volatile()
VOLATILE_FIELDS = ["embalo_charges", "fire_instant_ready", "thermal_shock_active",
                   "free_executar_charges", "pnq_counter", "fatiador_timer"]

def reset_volatile(self):
    for f in VOLATILE_FIELDS:
        setattr(self, f, type(getattr(self, f))())  # reseta para zero/False
```

Chamar `char_stats.reset_volatile()` no respawn e no load.

**2.2 — Quebrar imports circulares** `[2-3h]`

Extrair para `combat_utils.py`:
- `deal_damage` (de `systems.py`)
- `apply_effect` (de `systems.py`)
- `is_tile_walkable` (de `systems.py` — TileValidationSystem)

`skill_handlers.py` passa a importar de `combat_utils.py` em vez de `systems.py`.
`systems.py` remove import de `skill_handlers.py` — importa `SkillHandlers` dentro das funções que precisam (lazy).

Critério: `python -c "import skill_handlers"` sem circular ImportError.

**2.3 — Validação de max_hp no servidor** `[30min]`

```python
# session.py — _build_save_merge
MAX_HP_CAP = 10000  # valor máximo razoável
_cli_mhp = min(cli_stats.get("max_hp", 0), MAX_HP_CAP)
```

---

### FASE 3 — Débito técnico (qualidade de vida)

**3.1 — Testes automatizados básicos** `[3-4h]`

Criar `tests/test_combat.py` com pytest:
```python
def test_deal_damage_reduz_hp():
    ...
def test_skill_cooldown_server_rejeita():
    ...
def test_pirofagia_cone_hits_mob_in_range():
    ...
```

Foco: funções puras de `damage_calculator.py` + `combat_utils.py` (após 2.2).

**3.2 — AOI spatial hashing** `[3-4h — só se player count > 20]`

O O(P×M) atual é aceitável para < 20 jogadores simultâneos.
Implementar `SpatialHash` em `utils.py` com células de `TILE_SIZE * 10`.
Substituir o sweep em `_build_update_for_session` pelo hash.

**3.3 — Lag comp com timestamps** `[2-3h]`

Modificar CAST_SKILL para incluir `client_ts` (já tem `ts` no protocolo).
Em `_process_skill_requests`, usar `get_snapshot_at(tick_from_ts)` para validar range.

**3.4 — world_server.py God Object** `[4-5h — longo prazo]`

Extrair: `SkillProcessor`, `CombatProcessor`, `LootProcessor`, `RespawnSystem`.
Só fazer depois de testes cobrindo o comportamento atual.

---

## 4. O que NÃO fazer (análise errou)

- **Não criar SKILL_CATALOG** — já existe em `skill_config.py`.
- **Não refatorar talent_system** com dict de efeitos — já está data-driven.
- **Não criar `skill_validation.py` compartilhado** — o problema real é falta de validação server-side de CD/custo, não hardcoded values.
- **Não tratar A3 (position sync)** — já funciona corretamente.

---

## 5. Checklist de entrega

- [x] Fix de movimento commitado (5fb5a13)
- [x] Fix ATK_FIRE commitado (5fb5a13)
- [x] HP sync guard — SAVE_STATE não reseta HP em combate (5fb5a13)
- [x] Windows timer 1ms + TICK_RATE 30 TPS (5fb5a13)
- [x] Punho no Queixo — contador server-side (11f81f2)
- [x] Cooldown server-side implementado (1.1) — 2909ad1
- [x] Pirofagia funciona online (1.2) — b3f9962
- [x] skill_handlers sem pygame no topo (1.3) — 2909ad1
- [x] Volatile fields resetam no respawn (2.1) — ab3a348
- [x] max_hp cap no servidor (2.3) — ab3a348
- [x] Imports circulares quebrados (2.2) — 123f1df
- [ ] Tiro Múltiplo online (futura fase — Arqueiro/Bardo)
- [x] Testes automatizados (3.1) — 9313adb
- [x] AOI spatial hashing (3.2) — 709b93c
- [x] Lag comp com timestamps (3.3) — 6d3c60b
- [ ] world_server.py refatoração (3.4)
