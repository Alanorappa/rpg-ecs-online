# Plano de Ação — Análise e Melhorias do Projeto

> Gerado em 2026-06-01. Análise sistema a sistema + comparação com práticas da indústria.
> Atualizado com achados de inconsistências, duplicações e violações de arquitetura.

---

## 1. Status dos Itens Anteriores

| # | Item | Status |
|---|------|--------|
| 0.1 | Commits pendentes | ✅ Concluído |
| 1.1 | Cooldown server-side | ✅ Concluído (skill_processor.py) |
| 1.2 | Pirofagia online | ✅ Concluído |
| 1.3 | skill_handlers sem pygame no topo | ✅ Concluído |
| 2.1 | Volatile fields no respawn | ✅ Concluído (stats_system._respawn) |
| 2.2 | Imports circulares quebrados | ✅ Concluído (lazy imports em skill_handlers) |
| 2.3 | max_hp cap servidor | ✅ Concluído (session.py) |
| 3.1 | Testes automatizados básicos | ✅ 82 testes em tests/ |
| 3.2 | AOI spatial hashing | ✅ Concluído (utils.SpatialHash) |
| 3.3 | Lag comp com timestamps | ✅ Concluído (skill_processor) |
| 3.4 | world_server refatoração Mixin | ✅ Concluído |
| — | Tiro Múltiplo online | ⏳ Pendente (Arqueiro/Bardo) |

---

## 2. Análise Sistema a Sistema — Achados

### 2.1 Violações de Arquitetura ECS

#### A. CombatStats é um God Component (P4 — ainda aberto)
`components.py:74–321` — 200+ linhas, ~26 flags de talento inline:
- `fire_burns_on_crit`, `fire_mana_discount`, `thermal_shock_enabled`, `pyromania_bonus` etc.
- Mobs carregam todos esses campos sem nunca usá-los (desperdício de memória)
- Solução da indústria (WoW, PoE): dados de talento em componentes separados por build ou via `CombatRuntime`

**Indústria:** Unity DOTS e Flecs usam archetypes — entidades com conjuntos de componentes diferentes ficam em grupos separados na memória. Mobs não teriam as flags de Piromania.

**Proposta:** Extrair `MageStatFlags` e `WarriorStatFlags` como componentes opcionais. Custo médio, impacto na legibilidade e manutenção.

#### B. UIState em components.py — violação client/server
`components.py:1232–1252` — `UIState`, `ShopUIState`, `LootUIState` são estado de UI do *cliente* dentro do arquivo de componentes compartilhado. Isso viola o princípio de que `components.py` é usado pelo servidor também.

**Fix simples:** Mover esses 3 componentes para `game.py` ou `ui_components.py` (só cliente).

#### C. skill_config.py importa pygame para keybinds
`skill_config.py:31–39` — `import pygame` só para `DEFAULT_KEYBINDS = [pygame.K_1, ...]`. O servidor importa `skill_config` (para `SKILL_CATALOG`), carregando pygame desnecessariamente.

**Fix simples:** Substituir `pygame.K_1` pelos valores inteiros diretos (pygame.K_1 = 49, K_2 = 50... K_0 = 48) ou separar `DEFAULT_KEYBINDS` em `ui_config.py` que o servidor nunca importa.

#### D. stats_system.py importa pygame sem usar
`stats_system.py:8` — `import pygame` no topo, zero usos de `pygame.X` no módulo. O servidor importa `stats_system`.

**Fix simples:** Remover `import pygame` de `stats_system.py`.

#### E. TileMovement recebe campos via setattr (duck typing tácito)
`skill_processor.py:169–173` injeta `_server_dir_x`, `_server_dir_y`, `_server_aoe_x`, `_server_aoe_y` no dataclass `TileMovement` via atribuição direta. Esses campos não são declarados em `@dataclass TileMovement`.

**Problema:** Não existe type-safety, pode cair silenciosamente se o campo for esquecido, viola o design de dataclass imutável.

**Fix:** Declarar esses campos em `TileMovement` com valores padrão `= 0.0`. Custo baixo.

---

### 2.2 Duplicação de Código

#### A. _spell_damage duplicado entre spell_system e spell_completion_processor
`spell_system.py:40–52` — função `_spell_damage(attacker_id, world, dmg_weapon_pct, sp_coeff)`.
`spell_completion_processor.py:364–375` — método `_server_spell_damage(player_eid, dmg_weapon_pct, sp_coeff)`.

São **idênticos** na lógica. O servidor poderia importar `_spell_damage` de `spell_system`.

**Fix:** Mover `_spell_damage` para `damage_calculator.py` (módulo sem dependências) ou `core_systems.py`. Servidor importa de lá.

#### B. Volatile fields reset duplicado
Campos como `pnq_counter`, `embalo_charges`, `fire_instant_ready` são resetados em lugares diferentes:
- `stats_system._respawn`: reseta a maioria, mas **não pnq_counter nem fatiador_tick**
- `systems.py:1378`: reseta `pnq_counter`
- `skill_handlers.py:566`: reseta `fire_instant_ready`

**Solução da indústria:** Reset centralizado via método `reset_volatile()` em `CharacterStats` (já proposto no plano anterior como FASE 2.1 — implementar agora).

```python
VOLATILE_FIELDS = {
    "embalo_charges": 0, "fire_instant_ready": False,
    "thermal_shock_active": False, "free_executar_charges": 0,
    "pnq_counter": 0, "fatiador_timer": 0.0, "fatiador_tick": 0.0,
    "fire_crit_counter": 0, "fire_crit_timer": 0.0,
}
def reset_volatile(self):
    for field, default in VOLATILE_FIELDS.items():
        setattr(self, field, default)
```

Chamar `char_stats.reset_volatile()` em `_respawn`, `spawn_player` (online) e `load_game`.

#### C. apply_char_stats_to_combat chamado em 9 lugares
Chamado em `game.py` (4×), `world_server.py` (2×), `stats_system.py` (2×), `quest_system.py` (1×). Cada chamada recalcula TODOS os stats do zero. Não existe dirty-flag.

**Indústria:** Dirty-flag pattern — `cs._dirty = True` ao mudar atributo base; `update_if_dirty()` no início do tick. Reduz recálculos desnecessários.

**Prioridade baixa** — o jogo tem poucos jogadores; impacto real só com >50 players.

---

### 2.3 Hardcode e Magic Numbers

#### A. Fórmulas de dano hardcoded nos handlers
Algumas skills têm fórmulas dentro dos handlers, não no SKILL_CATALOG:

| Skill | Fórmula hardcoded | Onde |
|-------|------------------|------|
| Pirofagia | `150 + SP * 1.50` | `skill_handlers.py:871` |
| Vitória Iminente | `30%` do max HP | `skill_handlers.py:167` |
| Impacto | `50%` dano base | `skill_handlers.py:180` docstring |

Skills em `skill_config.py` têm `dmg_weapon_pct`, `dmg_sp_coeff`, `base_dmg` corretamente. Pirofagia deveria ser:
```python
"pirofagia": {
    ...
    "base_dmg": 150,
    "dmg_sp_coeff": 1.50,
}
```
E o handler lê do catálogo via `skill.params.get(...)`.

#### B. Duração de efeitos hardcoded nos handlers
```python
apply_effect(world, eid, "root", 5.0)          # nova_congelante
apply_effect(world, eid, "polymorph", 6.0)     # polimorfia
apply_effect(world, eid, "disoriented", 3.0)   # pirofagia
```
Essas durações deveriam estar no `SKILL_CATALOG` sob `effect_durations: {"root": 5.0}`.

**Fix:** Adicionar `"effect_durations"` ao SKILL_CATALOG para skills que aplicam CC. Handlers leem `skill.params.get("effect_durations", {}).get(effect, default)`. Servidor passa essas durações no `applied_effects` → cliente aplica localmente com duração correta.

---

### 2.4 Side-Channels Frágeis

Comunicação entre camadas via atributos mutáveis injetados:

| Side-channel | Onde | Problema |
|--------------|------|---------|
| `_server_pending_spells` | SkillSystem | Detecta modo servidor via `getattr(self, ...)` — implícito |
| `_server_dir_x/y` | TileMovement | Campo não declarado no dataclass |
| `_last_proj_spell_is_crit` | SpellCompletionMixin | Estado volátil entre handler e coletor de resultados |
| `_last_proj_lapso_proc` | SpellCompletionMixin | Idem |
| `_last_warn` | SkillHandlers | Mensagem de erro via atributo mutable |
| `last_client_payload` | Session | Todo o estado de save do cliente em um dict genérico |

**Indústria:** Retorno de valor estruturado (dataclass/namedtuple) ou contexto de execução tipado.

**Fix proposto para `_last_proj_spell_is_crit` etc.:** Criar `SpellResult` dataclass retornado pelos handlers:
```python
@dataclass
class SpellResult:
    is_crit: bool = False
    lapso_proc: dict | None = None
    fire_instant_proc: bool = False
```
Handler retorna `SpellResult` em vez de `bool`. Coletor usa o retorno.

---

### 2.5 game.py God Object (7030 linhas)

`game.py` tem 134 métodos e 7030 linhas com responsabilidades:
- Loop principal (`run`, game loop)
- Todos os handlers de mensagens de rede (700+ linhas inline)
- Gestão de entidades remotas (spawn/despawn/move de mobs e players)
- Save/load
- Desenho de HUD (hotbar, cast bar, stats panel, etc.)
- Sistemas de UI inline

**Indústria (Godot, Unity, Unreal):** O `GameEngine` é apenas orquestrador. Cada responsabilidade tem sua classe. O handling de rede fica em um `NetworkMessageHandler` separado.

**Solução incremental sem reescrita:**
1. Extrair `_handle_net_message` para `client/message_handler.py` (classe `ClientMessageHandler`)
2. Extrair gestão de entidades remotas para `client/remote_entity_manager.py`
3. `game.py` apenas cria essas classes e delega chamadas

**Prioridade média** — não impede features, mas dificulta manutenção.

---

### 2.6 World.get_entities_with — Alocação por Frame

`world.py:48–70` — `get_entities_with` cria uma nova lista Python a cada chamada. Sistemas como `TileMovementSystem`, `EnemyAISystem`, `StatusEffectSystem` chamam isso dezenas de vezes por frame.

**Indústria:** Unity DOTS/Flecs usam iteradores sem alocação e archetypes para acesso O(1). Nossa abordagem com índice invertido é razoável, mas a alocação de lista todo frame é ineficiente.

**Fix simples:** `get_entities_with` pode retornar um generator em vez de list para sistemas que só precisam iterar:
```python
def iter_entities_with(self, *component_types):
    """Versão generator — sem alocação."""
    # ...yield em vez de append
```

**Prioridade baixa** — só importa com >200 entidades ativas.

---

### 2.7 Reconexão de Cliente Ausente

`client/network.py` — não há lógica de reconnect. Se a conexão cair (ping timeout, queda de rede), o cliente não tenta reconectar.

**Indústria:** Toda aplicação de rede tem reconnect com backoff exponencial.

**Fix:**
```python
async def _reconnect_loop(self):
    delay = 1
    while not self.connected:
        await asyncio.sleep(delay)
        try:
            await self._connect()
            delay = 1
        except:
            delay = min(delay * 2, 30)  # backoff exp até 30s
```

---

### 2.8 Handlers de Skill — Acoplamento Visual no Servidor

`skill_handlers.py` ainda usa `LOG.add(...)`, `SOUNDS.play_skill(...)`, `FLT.add(...)` em modo servidor. Apesar de serem no-ops pelo `SDL_VIDEODRIVER=dummy`, causam:
- Overhead de chamada desnecessário
- Strings de log sendo construídas mas nunca exibidas
- Dependência implícita de FloatingTextManager, SOUNDS, LOG no servidor

**Fix:** Adicionar guard `if getattr(self, "_server_authoritative", False): return` nos paths visuais, ou mover logs para hook virtual (já existe `_emit_damage`/`_emit_heal` em `StatusEffectSystem`).

---

### 2.9 LOGOUT não tratado no servidor

`shared/messages.py:34` define `LOGOUT = "logout"` mas não aparece no `_handlers` dict de `session.py`. O cliente pode enviar LOGOUT para gracefully disconnect mas o servidor ignora.

**Fix simples:** Adicionar handler:
```python
async def _handle_logout(self, session, payload, ts):
    await self.on_disconnect(session.session_id)
```

---

### 2.10 Skill sem Critério de Crit no Servidor para non-BdF

`spell_completion_processor.py:_server_nova_congelante`, `_server_calcinar`, `_server_polimorfia`, `_process_player_channeling` — chamam `_server_apply_magic_damage` sem verificar crit.

Apenas `_server_bola_de_fogo` chama `resolve_attack_outcome` e aplica crit corretamente.

**Fix:** `_server_apply_magic_damage` deveria aceitar `attacker_cs` e calcular crit internamente, ou cada handler deve chamar `resolve_attack_outcome` antes.

```python
def _server_apply_magic_damage(self, attacker_id, target_id, dmg,
                                is_crit: bool = False,
                                roll_crit: bool = False) -> bool:
    if roll_crit:
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        target_cs   = self.world.get_component(target_id, CombatStats)
        if attacker_cs and target_cs:
            outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "magical")
            is_crit = (outcome == "crit")
            if is_crit:
                dmg = int(dmg * CRITICAL_DAMAGE_MULTIPLIER)
    ...
```

Handlers passariam `roll_crit=True` ao invés de replicar a lógica.

---

## 3. Comparação com Indústria — Diagnóstico Rápido

| Aspecto | Nosso projeto | Indústria (WoW, PoE, Tibia) | Gap |
|---------|--------------|------------------------------|-----|
| Tick rate | 30/s | 20–30/s ✅ | Nenhum |
| Protocolo | WebSocket+JSON | UDP+binário ou QUIC | Eficiência (para >100 players) |
| AOI | SpatialHash 15 tiles | Grid/quadtree | OK para scale atual |
| Lag comp | Snapshot por timestamp | Snapshot + replay | Funcional |
| Skill data-driven | Parcial (SKILL_CATALOG) | Total (DBC, JSON) | Fórmulas ainda no código |
| Autoridade servidor | Total em dano/posição | Total | ✅ |
| Persistência | SQLite → PostgreSQL | PostgreSQL/Redis | OK para MVP |
| Reconexão | Sem reconexão | Reconexão automática | Gap real |
| ECS | Inverted index | Archetype (Unity/Flecs) | Aceitável para scale |
| Separação cliente/servidor | Boa (Mixin pattern) | Excelente | Pequenos leaks (visual em handler) |
| Testes | 82 testes | CI/CD com cobertura | OK, pode expandir |

---

## 4. Plano de Ação Priorizado — NOVO

### FASE A — Rápidos (< 1h cada, alto impacto)

**A.1 — Remover `import pygame` de `stats_system.py`** `[5min]`
- `stats_system.py:8` — remover linha. Nenhum uso real.
- Servidor carrega stats_system → não precisa de pygame.

**A.2 — Separar keybinds de skill_config** `[30min]`
- `skill_config.py:31–39` — mover `DEFAULT_KEYBINDS` para `client/ui_config.py` (novo arquivo, só cliente).
- Remover `import pygame` de `skill_config.py`.
- Servidor não importa mais pygame via skill_config.

**A.3 — Mover UIState/ShopUIState/LootUIState para fora de components.py** `[20min]`
- `components.py:1232–1252` — mover para `game.py` ou `ui_components.py`.
- `components.py` é shared (servidor usa). Essas classes são só do cliente.

**A.4 — Declarar campos server-inject em TileMovement** `[15min]`
- `components.py:TileMovement` — adicionar campos com `= 0.0`:
  `_server_dir_x: float = 0.0`, `_server_dir_y: float = 0.0`,
  `_server_aoe_x: float = 0.0`, `_server_aoe_y: float = 0.0`
- Eliminar `setattr` dinâmico no skill_processor.

**A.5 — LOGOUT handler no servidor** `[10min]`
- `server/session.py` — adicionar `_handle_logout` no `_handlers`.

**A.6 — reset_volatile() em CharacterStats** `[30min]`
- `components.py:CharacterStats` — adicionar método `reset_volatile()` com todos os campos voláteis.
- `stats_system._respawn`: substituir os campos manuais por `char_stats.reset_volatile()`.
- `server/world_server.spawn_player`: chamar `char.reset_volatile()` após load.

---

### FASE B — Médio prazo (1–3h cada)

**B.1 — Extrair `_spell_damage` para módulo shared** `[1h]`
- Mover `_spell_damage` de `spell_system.py` para `damage_calculator.py`.
- Atualizar `spell_completion_processor.py` para importar de lá (remover `_server_spell_damage` duplicado).
- Atualizar todas as referências.

**B.2 — Fórmulas de dano de Pirofagia no SKILL_CATALOG** `[30min]`
- `skill_config.py:pirofagia` — adicionar `"base_dmg": 150, "dmg_sp_coeff": 1.50`.
- `skill_handlers.py:_skill_pirofagia` — ler via `skill.params.get("base_dmg", 150)`.
- `server/spell_completion_processor.py:_server_calcinar` — já lê do catálogo ✅.

**B.3 — Duração de efeitos no SKILL_CATALOG** `[1h]`
- Adicionar `"effect_durations"` em cada skill que aplica CC:
  ```python
  "nova_congelante":  {"effect_durations": {"root": 5.0}},
  "pirofagia":        {"effect_durations": {"disoriented": 3.0}},
  "polimorfia":       {"effect_durations": {"polymorph": 6.0}},
  ```
- Handlers leem: `dur = skill.params.get("effect_durations", {}).get(eff, 5.0)`.
- Servidor inclui duração em `applied_effects`: `{"type": "root", "dur": 5.0}`.
- Cliente usa a duração real ao aplicar localmente.

**B.4 — Crit em todas as spells mágicas do servidor** `[1h]`
- `spell_completion_processor._server_apply_magic_damage`: aceitar parâmetro `roll_crit=False`.
- Handlers de `nova_congelante`, `calcinar`, `calamidade_flamejante`, `pirofagia` passam `roll_crit=True`.
- Resultado de crit propagado no SKILL_RESULT (`"outcome": "crit"`).

**B.5 — Reconexão automática no NetworkClient** `[1h]`
- `client/network.py` — implementar reconnect com backoff exponencial.
- Exibir tela de "Reconectando..." no `game.py` durante tentativa.
- Ao reconectar: relogar automaticamente com as credenciais em memória.

---

### FASE C — Longo prazo (3h+, qualidade de vida)

**C.1 — SpellResult dataclass para handlers** `[2h]`
- Substituir side-channels `_last_proj_spell_is_crit`, `_last_proj_lapso_proc` por retorno estruturado.
- Handlers de skill retornam `SpellResult` (dataclass tipado).
- Coletores de resultado usam o retorno em vez de `getattr(self, "_last_*")`.

**C.2 — Extrair ClientMessageHandler de game.py** `[3h]`
- `game.py:_handle_net_message` (700+ linhas) → `client/message_handler.py`.
- `game.py` cria uma instância e delega: `self._msg_handler.handle(msg_type, payload)`.
- Facilita testes unitários de cada handler de mensagem.

**C.3 — RemoteEntityManager** `[2h]`
- Extrair de `game.py`:
  - `_spawn_remote_mob`, `_move_remote_mob`, `_spawn_remote_player_entity`, `_remove_remote_player_entity`
  - `_remote_mobs`, `_remote_players`, `_mob_hp`, `_mob_move_queues`
- Para `client/remote_entity_manager.py`.

**C.4 — CombatStats: extrair flags de talento** `[4h — alto impacto futuro]`
- Criar `MageTalentFlags` e `WarriorTalentFlags` como componentes opcionais.
- Mobs não recebem esses componentes → reduz tamanho de CombatStats.
- Handlers checam `self.world.get_component(eid, MageTalentFlags)`.

**C.5 — World.iter_entities_with (generator)** `[1h]`
- Adicionar versão generator de `get_entities_with` para hot paths.
- Medir impacto de alocação com profiling antes de implementar.

**C.6 — Vitória Iminente e heal % no SKILL_CATALOG** `[30min]`
- `skill_config.py:vitoria_iminente` — adicionar `"heal_pct": 0.30`.
- Handler lê `skill.params.get("heal_pct", 0.30)`.
- Permite balancing sem tocar em código.

---

## 5. Inconsistências Menores a Monitorar

| # | Inconsistência | Arquivo | Prioridade |
|---|----------------|---------|-----------|
| M1 | `apply_effect` usa 5.0 fallback no cliente | `game.py:3282` | B.3 resolve |
| M2 | `_last_warn` em SkillHandlers para retornar erro | `skill_handlers.py:_warn` | C.1 resolve parcialmente |
| M3 | `StatsUpdate` não é enviado ao fazer level-up pelo servidor em todas as circunstâncias | `world_server.py` | Verificar |
| M4 | Duração de `exhaustion` (Exaustão) hardcoded como 6.0 em dois lugares | `spell_completion_processor.py:499,507` | B.3 |
| M5 | Sem `LOGOUT` handler no servidor | `session.py` | A.5 |
| M6 | `fatiador_tick` não resetado em `_respawn` | `stats_system.py:313` | A.6 |
| M7 | `fire_crit_counter` e `fire_crit_timer` resetados em `apply_talent_effects` mas não em `_respawn` | `stats_system.py` | A.6 |
| M8 | Todas as spells mágicas exceto BdF não aplicam crit no servidor | `spell_completion_processor.py` | B.4 |
| M9 | `skill_config.py` importa `pygame` — server carrega pygame via skill_config | `skill_config.py:31` | A.2 |
| M10 | `DEFAULT_KEYBINDS` em skill_config (dados de gameplay misturados com UI) | `skill_config.py:33` | A.2 |

---

## 6. O que NÃO fazer

- **Não reescrever game.py do zero** — refatoração incremental é mais segura.
- **Não migrar para UDP/binary agora** — WebSocket+JSON funciona bem para o scale atual. Migrar para MessagePack primeiro quando tiver >20 players simultâneos.
- **Não implementar archetype ECS** — nossa World com índice invertido é correta para o scale. Só seria necessário com >500 entidades ativas.
- **Não criar combat_utils.py separado** — os imports circulares já foram resolvidos via lazy imports.

---

## 7. Checklist de Entrega (Novo)

- [ ] A.1 — Remover `import pygame` de `stats_system.py`
- [ ] A.2 — Separar `DEFAULT_KEYBINDS` de `skill_config.py`
- [ ] A.3 — Mover UIState para fora de `components.py`
- [ ] A.4 — Declarar campos `_server_dir_x/y/_aoe_x/y` em TileMovement
- [ ] A.5 — Handler LOGOUT no servidor
- [ ] A.6 — `reset_volatile()` em CharacterStats
- [ ] B.1 — `_spell_damage` extraído para `damage_calculator.py`
- [ ] B.2 — Fórmula de Pirofagia no SKILL_CATALOG
- [ ] B.3 — Durações de efeitos CC no SKILL_CATALOG
- [ ] B.4 — Crit em todas as spells mágicas servidor
- [ ] B.5 — Reconexão automática no NetworkClient
- [ ] C.1 — SpellResult dataclass (substitui side-channels)
- [ ] C.2 — ClientMessageHandler extraído de game.py
- [ ] C.3 — RemoteEntityManager extraído de game.py
- [ ] C.4 — CombatStats: extrair flags de talento
