# Problemas de Arquitetura — Análise Crítica

> Avaliação como Arquiteto de Software de Jogos Sênior.
> Última análise: 2026-05-24 | Próxima revisão sugerida: após nova build ou classe jogável.
> Revisão completa online (world_server, session, death_handler, damage_calculator, messages): ver `CODE_REVIEW.md`

---

## Status dos problemas

| # | Problema | Severidade | Status | Esforço fix |
|---|----------|------------|--------|-------------|
| 1 | CharacterStats mistura permanente + volátil | CRÍTICO | 🔴 Aberto | Alto |
| 2 | CombatStats com 80+ campos e 25+ flags de talento | CRÍTICO | 🔴 Aberto | Médio |
| 3 | Skill sem handler não avisar | CRÍTICO | ✅ Corrigido | — |
| 4 | `fatiador_timer` não reseta no respawn | ALTO | ✅ Corrigido | — |
| **C1** | `move_player` sem validação de walkability | **CRÍTICO/SEGURANÇA** | 🔴 Aberto | Baixo |
| **C2** | `client_max_hp`/`client_ap` confiados sem cap | **CRÍTICO/SEGURANÇA** | 🔴 Aberto | Baixo |
| **C3** | Gold client-autoritativo no save merge | **CRÍTICO/SEGURANÇA** | 🔴 Aberto | Baixo |
| **C4** | `last_outcome` singleton/mutable global | CRÍTICO | 🔴 Aberto | Médio |
| **C5** | `_pending_inv` como atributo dinâmico (leak) | ALTO | 🔴 Aberto | Baixo |
| **A1** | CombatStateSystem duplicado em world_server.py | ALTO | 🔴 Aberto | Médio |
| **A2** | N² AOI sweep em session.py por tick | ALTO | 🔴 Aberto | Alto |
| **A3** | Busy-wait `sleep(0)` na tick loop | ALTO | 🔴 Aberto | Baixo |
| **A4** | `_ServerSFX` inner class em `_load_map()` | MÉDIO | 🔴 Aberto | Baixo |
| **A5** | `_lookup_item_value` instancia factories por venda | MÉDIO | 🔴 Aberto | Baixo |
| **A6** | `process_shop_buy` scan linear + factory dupla | MÉDIO | 🔴 Aberto | Baixo |
| **A7** | `_tick()` 400+ linhas (God Method) | MÉDIO | 🔴 Aberto | Médio |
| **A8** | `get_session_id_for_player` O(players) em toda morte | MÉDIO | 🔴 Aberto | Baixo |
| **A9** | Mob attacker lookup O(mobs) por player por tick | MÉDIO | 🔴 Aberto | Médio |
| 5 | `fire_instant_ready` pendurado | ALTO | ✅ Corrigido | — |
| 6 | `apply_talent_effects()` recalcula tudo sem batch | ALTO | 🔴 Aberto | Médio |
| 7 | `thermal_shock_active` é state derivado armazenado | ALTO | 🔴 Aberto | Médio |
| 8 | Hardcodes `skill_id` em 3+ lugares | MÉDIO | 🔴 Aberto | Médio |
| 9 | ShopSystem / LootSystem: UI + lógica inseparável | MÉDIO | 🔴 Aberto | Alto |
| 10 | `timed_modifiers` sem type hint | MÉDIO | ✅ Corrigido | — |
| 11 | `_just_entered_combat` sem reset garantido | MÉDIO | ✅ Falso positivo | — |
| 12 | Fórmulas de skills hardcoded no handler | BAIXO | ✅ Parcialmente corrigido (Skill.params + fatiador) | — |
| 13 | MOB_ABILITIES sem validação em startup | BAIXO | ✅ Corrigido | — |
| 14 | `Skill.fail_flash_timer` é state de UI em component | BAIXO | 🔴 Aberto | Médio |

---

## 🔴 CRÍTICO — Escala impossível ou bug ativo

### Problema 1 — CharacterStats mistura dados permanentes com state volátil de combate

**Arquivo:** `components.py` linhas 449–499

**Descrição:**
`CharacterStats` contém duas categorias completamente distintas de dados:
- **Permanentes:** `strength`, `intelligence`, `level`, `xp`, `mana`, `max_mana`, `spawn_tile_x`
- **Voláteis de combate:** `embalo_charges`, `free_executar_charges`, `fire_instant_ready`, `thermal_shock_active`, `pnq_counter`, `fatiador_timer`, `fatiador_tick`

**Por que é crítico:**
1. Cada nova build adiciona ~10 flags voláteis. Com 5 builds → 50 campos em CharacterStats.
2. State temporal não deve persistir: `embalo_charges` ao salvar/carregar cria inconsistências.
3. Qualquer sistema que precisa de `mana` carrega também `thermal_shock_active`, `pnq_counter`, etc.
4. Impossível saber, sem ler o código, quais campos são permanentes e quais são de combate.

**Evidência:**
```python
# components.py CharacterStats.__init__
self.strength: int = 1          # permanente — faz sentido salvar
self.level: int = 1             # permanente
self.embalo_charges: int = 0    # volátil — NÃO deveria salvar
self.fatiador_timer: float = 0.0 # volátil de combat runtime
```

**Fix recomendado:**
```python
class CharacterStats:   # dados permanentes e de progressão
    strength, intelligence, agility, vitality, defense
    level, xp, xp_to_next_level
    mana, max_mana, mana_regen_timer
    rage, max_rage
    name, class_id, spawn_tile_x, spawn_tile_y, spawn_map

class CombatRuntime:    # state volátil de combate (não salvar)
    embalo_charges, free_executar_charges
    fire_instant_ready, thermal_shock_active
    pnq_counter, fatiador_timer, fatiador_tick
```

**Impacto se não corrigido:** Impossível adicionar 3ª classe sem CharacterStats virar um monolito de 100+ campos.

---

### Problema 2 — CombatStats com 80+ campos e flags de talento sem isolamento

**Arquivo:** `components.py` linhas 73–165

**Descrição:**
`CombatStats` tem ~45 atributos de combate base + 26 flags de talento (12 Cavaleiro + 14 Piromania). Todos os campos existem em TODAS as entidades, mesmo que nunca sejam usados.

**Por que é crítico:**
1. Com 5 builds × 12 flags = 60 flags novas → CombatStats chega a 120+ campos.
2. Sistemas do guerreiro lêem flags do mago e vice-versa (sem isolamento).
3. Impossível debugar qual flag pertence a qual build.
4. Type checkers não detectam uso incorreto de flag de build errada.

**Evidência:**
```python
# CombatStats.__init__ — guerreiro lendo flag do mago (bugável)
cs.fire_mana_discount    # flag do mago — existe em entidades guerreiro
cs.golpe_poderoso_rage_cost  # flag do guerreiro — existe em entidades mago
```

**Fix recomendado (data-driven):**
```python
class CombatStats:
    # Atributos base (manter)
    base_stamina, armor, spell_power, crit_rating...
    
    # Flags de talento — dict em vez de campos fixos
    talent_flags: dict = field(default_factory=dict)
    # Ex: {"fire_mana_discount": 5, "embalo_on_crit": True}

# Em skill_handlers.py:
discount = cs.talent_flags.get("fire_mana_discount", 0)
```

**Impacto se não corrigido:** Impossível escalar para arqueiro, necromante, etc. sem quebrar os sistemas existentes.

---

## 🟡 ALTO — Limita funcionalidade ou escala mal

### Problema 3 ✅ — Skill sem handler não avisava em startup

**Corrigido em:** `game.py` → `_validate_skill_handlers()`

---

### Problema 4 ✅ — `fatiador_timer`/`fatiador_tick` não resetavam no respawn

**Corrigido em:** `stats_system.py` → `DeathRespawnSystem._respawn()`

---

### Problema 5 ✅ — `fire_instant_ready` podia ficar pendurado

**Corrigido em:** `stats_system.py` → `DeathRespawnSystem._respawn()`

---

### Problema 6 — `apply_talent_effects()` recalcula todos os stats a cada clique

**Arquivo:** `talent_system.py` linhas 152–245

**Descrição:**
A cada clique em nó de talento (alocar/desalocar), `apply_talent_effects()`:
1. Remove TODOS os modificadores existentes
2. Itera todos os talentos alocados
3. Para cada efeito, chama `add_modifier()` que por sua vez chama `_recalculate_effective_stats()`
4. `_recalculate_effective_stats()` itera sobre toda a lista de `modifiers`

Com 15 talentos × 5 pontos × `_recalculate` completo por modifier = N² operações.

**Evidência:**
```python
# talent_system.py
for talent_id, points in tt.allocated.items():
    for eff in (t["effects"] or []):
        mod = Modifier(eff["attribute"], total_value, eff["type"])
        add_modifier(cs, mod)  # ← chama _recalculate_effective_stats() CADA VEZ
```

**Fix recomendado:**
```python
# stat_fns.py — adicionar batch apply
def apply_modifiers_batch(cs: CombatStats, modifiers: list[Modifier]) -> None:
    cs.modifiers.extend(modifiers)
    cs._recalculate_effective_stats()  # uma vez só
```

**Impacto:** Lag perceptível (~50ms) ao usar árvore de talentos. Piora com mais talentos.

---

### Problema 7 — `thermal_shock_active` é state derivado armazenado em CharacterStats

**Arquivo:** `components.py` linha 491; `spell_system.py` ManaSystem linhas 131–141

**Descrição:**
`thermal_shock_active` é calculado todo frame por `ManaSystem` e armazenado em `CharacterStats`. É puramente derivado de: "alvo selecionado tem efeito `root`?". Não precisa ser armazenado — pode ser calculado on-demand.

**Problema:** Se ManaSystem não rodar por alguma razão (bug de ordem), o valor fica stale.

**Evidência:**
```python
# ManaSystem.update() — recalcula e armazena a cada frame
char_stats.thermal_shock_active = (
    _t_sfx is not None and _t_sfx.has("root"))

# game.py hotbar — lê o valor armazenado
is_procced = _char.thermal_shock_active
```

**Fix recomendado:** Calcular inline na hotbar:
```python
# game.py hotbar — sem armazenar
def _is_thermal_shock_active(world, player_id, cs):
    if not getattr(combat_stats, "thermal_shock_enabled", False):
        return False
    target_id = combat_state.target_entity_id
    if target_id == -1:
        return False
    sfx = world.get_component(target_id, StatusEffects)
    return sfx is not None and sfx.has("root")
```

---

## 🔵 MÉDIO — Débito técnico que limita desenvolvimento

### Problema 8 — `skill_id` hardcoded em sistemas para lógica de negócio

**Arquivo:** `systems.py` linhas ~812, ~1165, ~1177

**Descrição:**
Alguns sistemas verificam `skill.skill_id` diretamente para tomar decisões:
```python
if sk.skill_id == "vitoria_iminente" and sk.max_charges > 0: ...
if sk.skill_id == "punho_no_queixo": ...
```

Em vez de usar metadados declarativos na própria skill.

**Impacto:** Adicionar nova skill com carga → buscar e editar todos os `if sk.skill_id ==` manualmente.

**Fix recomendado:** Os campos `max_charges` e `is_charge_based` já existem no `Skill` component. Usar esses campos em vez de verificar pelo ID:
```python
# Em vez de:
if sk.skill_id == "punho_no_queixo":
    # lógica de carga

# Usar:
if sk.max_charges > 0 and sk.charges > 0:
    # lógica genérica de carga
```

---

### Problema 9 — ShopSystem, LootSystem, CraftingSystem: UI + lógica inseparável

**Arquivo:** `systems.py` ShopSystem, LootSystem; `crafting_system.py`

**Descrição:**
Cada sistema renderiza sua própria UI (blit de surfaces, fontes, botões com 200–400 linhas de render) junto com a lógica de negócio (transações, coleta de itens, crafting).

**Impacto:**
- Impossível testar lógica sem inicializar pygame
- Mudança visual (reposicionar um botão) força modificar código de transação
- Reutilização de lógica em outro contexto é impossível

**Fix recomendado (quando houver tempo):**
```python
# systems/shop_logic.py
class ShopLogic:
    def buy(self, player_id, item_id) -> bool: ...
    def sell(self, player_id, item_id) -> bool: ...
    # sem pygame — testável

# systems/shop_ui.py
class ShopUIRenderer:
    def render(self, shop_state, screen): ...
    # usa ShopLogic para estado, só UI
```

---

### Problema 10 ✅ — `timed_modifiers` sem type hint

**Corrigido em:** `components.py` — tipado como `list[dict]` com comentário de schema.

---

### Problema 11 ✅ — `_just_entered_combat` sem reset garantido

**Falso positivo:** Já é resetado em `CombatStateSystem.update()` linha 887-888 do `systems.py`.

---

## 🟢 BAIXO — Qualidade e padronização

### Problema 12 — Fórmulas de skills hardcoded nos handlers

**Arquivo:** `skill_handlers.py`

**Descrição:**
Multiplicadores e percentuais de skills estão hardcoded nos handlers:
```python
deal_damage(..., multiplier=3.0)        # Golpe Poderoso: 3x dano
deal_damage(..., multiplier=2.0)        # Vitória Iminente: 2x dano
heal = int(combat_stats.max_hp * 0.30) # Vitória Iminente: 30% cura
```

Em vez de virem do `SKILL_CATALOG`.

**Impacto:** Balancear uma skill requer editar código, não dados.

**Fix recomendado:**
```python
# skill_config.py
"golpe_poderoso": {
    "damage_multiplier": 3.0,   # documentado aqui
    ...
}

# skill_handlers.py
mult = SKILL_CATALOG[sid].get("damage_multiplier", 1.0)
deal_damage(..., multiplier=mult)
```

---

### Problema 13 ✅ — MOB_ABILITIES sem validação

**Corrigido em:** `game.py` → `_validate_skill_handlers()` agora também valida abilities.

---

### Problema 14 — `Skill.fail_flash_timer` é state de render em component de dados

**Arquivo:** `components.py` linha ~725

**Descrição:**
```python
self.fail_flash_timer: float = 0.0  # escurece o slot por 0.2s ao falhar
```
Timer de animação visual num component de dados de jogo. Viola separação de responsabilidades.

**Fix recomendado:** Mover para `UIState` component ou gerenciar diretamente no renderer da hotbar com dict local.

---

---

## Novos problemas identificados — Análise de Escalabilidade (2026-05-04)

### P15 ✅ — `if char.class_id == "mago"` em `game.py` para atributos base

**Corrigido:** `CLASS_BASE_STATS` em `stats_system.py`. Arqueiro já incluso.

---

### P16 ✅ — `if entity_class == "Warlock"/"Hunter"/"Mage"` em `EnemyAISystem`

**Corrigido:** `PROJECTILE_BY_CLASS` em `mob_definitions.py`. EnemyAISystem lê do dict.

---

### P16-original — `if entity_class == "Warlock"/"Hunter"/"Mage"` em `EnemyAISystem`

**Arquivo:** `systems.py` EnemyAISystem, ~linha 2064

**Descrição:** Lógica de projéteis de mobs inimigos verifica `entity_class` diretamente:
```python
if ai_control.entity_class in ("Warlock", "Mage"):
    # cor de projétil
if ai_control.entity_class == "Hunter":
    # comportamento de kiting específico
```

**Impacto:** Criar mob da classe "Arqueiro" sem adicionar código aqui → comportamentos incorretos.

**Fix:** Mover parâmetros de projétil (cor, velocidade, damage_type) para `mob_definitions.py` como dados.

**Esforço:** Médio

---

### P17 — `talent_system.py` tem 25+ flags hardcoded (reset + aplicação)

**Arquivo:** `talent_system.py` linhas 172-200 (reset) e 248-273 (aplicação)

**Descrição:** Para cada nova build de talentos, é necessário:
1. Adicionar ~15 campos a `CombatStats`
2. Adicionar reset de cada flag no bloco de reset
3. Adicionar aplicação de cada flag no bloco de aplicação

Com 3ª classe (arqueiro + build nova) → ~45 flags em CombatStats, ~45 linhas de reset, ~45 linhas de aplicação.

**Fix ideal:**
```python
# talent_data.py — cada nó declara seu reset e fórmula
"cav_veterano": {
    "flag": "golpe_poderoso_rage_cost",
    "reset_value": 15,
    "formula": lambda pts, tt: max(10, 15 - pts),
}
# talent_system.py — loop genérico
for talent_id, t in TALENTS.items():
    if "flag" in t:
        setattr(cs, t["flag"], t["reset_value"])  # reset
        setattr(cs, t["flag"], t["formula"](pts, tt))  # apply
```

**Esforço:** Alto

---

### P18 ✅ — Skills em `spell_system.py` com `_complete_cast` crescendo por skill

**Corrigido:** `_CAST_HANDLERS` dict em `SpellCastSystem.__init__`. Adicionar nova spell = 1 linha no dict.

---

### P18-original — Skills em `spell_system.py` com `_complete_cast` crescendo por skill

**Arquivo:** `spell_system.py` SpellCastSystem._complete_cast

**Descrição:**
```python
if sid == "bola_de_fogo":
    self._launch_fireball(...)
elif sid == "nova_congelante":
    self._apply_nova_congelante(...)
elif sid == "polimorfia":
    ...
elif sid == "calcinar":
    ...
```

Cada nova skill com cast_time adiciona um `elif`. Sem dispatch automático.

**Fix:** Registrar handler de completion no `SKILL_CATALOG`:
```python
"bola_de_fogo": {
    ...
    "on_cast_complete": "launch_fireball",  # nome de método em SpellCastSystem
}
```

**Esforço:** Médio

---

## Guia de priorização para próximas sessões

### 🚨 Crítico — Segurança (corrigir antes de qualquer teste com usuários reais)
- **C1**: `move_player` walkability check — ~30min
- **C2**: Remover trust em `client_max_hp`/`client_ap` — ~1h
- **C3**: Gold exclusivamente server-side — ~1h
- **C5**: `_pending_inv` dict próprio + cleanup em despawn — ~30min

### Corrigir imediatamente (baixo esforço, alto impacto)
- **A3**: Fix busy-wait tick loop — 15min
- **A8**: Reverse map `_player_session_by_eid` — 30min
- **A4**: Extrair `_ServerSFX` para arquivo próprio — 30min
- **#8**: Remover hardcodes de `skill_id` — 1-2h
- **#12**: Mover multiplicadores para `SKILL_CATALOG` — 2-3h

### Planejar antes da próxima classe jogável
- **A1**: CombatStateSystem em `core_systems.py` — ~2h
- **C4**: `deal_damage` retornar outcome — ~2h (afeta muitos callers)
- **#2**: `talent_flags: dict` em CombatStats — refator de ~30 callsites, ~4h
- **#6**: Batch apply em `apply_talent_effects()` — ~2h

### Planejar para fase de polimento / performance
- **A2**: Spatial hash para AOI sweep — ~4h
- **A5/A6**: Item lookup caches — ~2h
- **A7**: Quebrar `_tick()` em submétodos — ~2h
- **A9**: Mob aggro tracking direto — ~2h
- **#1**: Separar `CharacterStats` + `CombatRuntime` — refator maior, ~8h
- **#7**: `thermal_shock_active` como função inline — ~1h
- **#9**: Separar UI de lógica em Shop/Loot/Crafting — ~16h
- **#14**: `fail_flash_timer` fora do Skill component — ~2h
