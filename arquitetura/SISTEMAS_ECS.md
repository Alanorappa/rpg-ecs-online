# Sistemas ECS — Referência Completa

> Todos os sistemas do jogo, em ordem de execução, com responsabilidades e dependências.
> Última atualização: 2026-06-15 (`_apply_final_damage` gate de dano servidor; `_target_alive` helper PvP em `SkillHandlers`)

---

## Ordem de execução — `game.py self.systems` (offline / cliente online)

| Pos | Sistema | Arquivo | Responsabilidade | Eventos |
|-----|---------|---------|------------------|---------|
| 1 | **TileValidationSystem** | systems.py | Reconstrói cache de tiles ocupados; valida walkability | Não |
| 2 | **AoeTargetingSystem** | spell_system.py | Mira de spells AOE (Calamidade); clique esq. dispara | **Sim** |
| 3 | **MouseTargetingSystem** | systems.py | Clique dir. = perseguir inimigo; clique esq. = selecionar/deselecionar | **Sim** |
| 4 | **LootSystem** | systems.py | Abre modal de loot; coleta itens de cadáveres | **Sim** |
| 5 | **PlayerInputSystem** | systems.py | Input de teclado/mouse: movimento, auto-ataque, auto-move | **Sim** |
| 6 | **SkillSystem + SkillHandlers** | systems.py + skill_handlers.py | Dispatch de skills por `skill_id`; GCD | **Sim** |
| 7 | **EnemyAISystem** | systems.py | Pathfinding, estados (IDLE/CHASING/ATTACKING), kiting, leash — N-player: cada mob seleciona alvo via `_select_target(mob_eid)`, armazena em `AIControlled.target_eid` | Não |
| 8 | **EnemyAbilitySystem** | systems.py | Cooldowns e triggers de habilidades especiais de mobs. **Ranged (range>1):** verifica LOS (Bresenham) antes de disparar; cria `Projectile` com `ability_id` em vez de `apply_effect` direto. **Melee (range=1):** `apply_effect` direto. Estados ativos: ATTACKING, CHASING, **KITING** | Não |
| 9 | **ProjectileSystem** | systems.py | Move projéteis de inimigos. Ao acertar: se `ability_id` preenchido → `apply_effect` (DoT/debuff); senão → `deal_damage` (dano direto) | Não |
| 10 | **PlayerProjectileSystem** | spell_system.py | Move projéteis do mago; resolve crit, burn, procs | Não |
| 11 | **SpellCastSystem** | spell_system.py | Processa barra de cast; completa ou cancela | Não |
| 12 | **ChannelingSystem** | spell_system.py | Ticks de canalização (Calamidade Flamejante); mana/s | **Sim** |
| 13 | **IceBlockSystem** | spell_system.py | Duração do Bloco de Gelo; regen HP por tick | Não |
| 14 | **FireShieldSystem** | spell_system.py | Duração do Escudo de Fogo; remove ao expirar | Não |
| 15 | **PirofagiaSystem** | spell_system.py | Mira de cone em tempo real; dispara no clique. **Online:** deduz mana/CD localmente e envia `CAST_SKILL {sid, dir_x, dir_y}` — servidor calcula atingidos (lag compensation pendente C1). **Offline:** calcula dano localmente. | **Sim** |
| 16 | **ManaSystem** | spell_system.py | Regen de mana; decrementa fire_crit_timer; thermal_shock_active | Não |
| 17 | **DeathHandlerSystem** | systems.py | Processa PendingDeath: loot, corpse, XP, respawn de zona | Não |
| 18 | **CorpseSystem** | systems.py | Decay de cadáveres (timer); remove entidade ao expirar | Não |
| 19 | **SpawnZoneSystem** | systems.py | Gerencia contagem de spawns ativos por zona | Não |
| 20 | **XPSystem** | stats_system.py | Consome `pending_xp`, aplica level-up | Não |
| 21 | **DeathRespawnSystem** | stats_system.py | Respawn do jogador; restaura HP | Não |
| 22 | **ConsumableSystem** | systems.py | Processa ActiveRegen (efeito de consumíveis) | Não |
| 23 | **CombatStateSystem** | systems.py | Timers de combate (stun, in_combat); procs ao entrar em combate | Não |
| 24 | **StatusEffectSystem** | systems.py | Decrementar duração; aplicar ticks (poison, burn, regen…); morte por DoT | Não |
| 25 | **TileMovementSystem** | systems.py | Interpolação de movimento; atualiza elevation; som de passos | Não |
| 26 | **FogSystem** | systems.py | Shadowcasting (8 octantes); atualiza Visible tags | Não |
| 27 | **CameraSystem** | systems.py | Suaviza câmera em direção ao player | Não |
| 28 | **TileRenderSystem** | systems.py | Cache de tiles (terrain + objects); render com ysort | Não |
| 29 | **RenderSystem** | systems.py | Ysort de entidades + objetos; HP bars; highlight de alvo | Não |

### Sistemas fora da lista principal (chamados explicitamente em `game.py`)

| Sistema | Quando chamado | Arquivo |
|---------|----------------|---------|
| **QuestSystem** | inserido em `self.systems` via `insert()` após XPSystem | quest_system.py |
| **ShopSystem** | `self._shop_system.update()` + `render_world()` separados | systems.py |
| **BlacksmithSystem** | `self._crafting_system.update()` + `handle_events()` | crafting_system.py |
| **TrainerSystem** | `self._trainer_system.update()` + `handle_events()` | trainer_system.py |
| **QuestDialogSystem** | `render_world()` separado | quest_system.py |
| **TalentSystem** | `render()` via `_show_talents` flag | talent_system.py |
| `_aoe_targeting_system.render()` | **após** fog + entities | spell_system.py |
| `_pirofagia_system.render()` | **após** aoe_targeting | spell_system.py |
| `_projectile_system.render()` | **após** fog | systems.py |
| `_player_proj_system.render()` | **após** fog | spell_system.py |
| `_channeling_system.render()` | **após** fog | spell_system.py |

> **Regra:** sistemas que rendem por cima de tiles/entidades devem ser chamados explicitamente APÓS `_tile_render_system.render_fog()`.

---

## Sistemas do Servidor (branch online) — `server/`

> Estes sistemas rodam **sem Pygame real** (SDL dummy), em asyncio, no processo do servidor.
> Nunca importam Pygame para display, input ou render.

### Loop de ticks — `server/world_server.py`

O servidor roda a **30 ticks/s** (33ms por tick). Ver `ARQUITETURA_ONLINE.md → Fluxo de tick` para a ordem completa.

### Sistemas na lista `_systems` do servidor

Instanciados em `_load_map()`, executados por `_systems.update(dt)` a cada tick:

| Ordem | Sistema | Responsabilidade | Notas |
|-------|---------|------------------|-------|
| 1 | **TileValidationSystem** | Rebuild cache de tiles ocupados; valida walkability | Idêntico ao offline |
| 2 | **SpawnZoneSystem** (headless) | Spawna mobs; verifica contagem ativa | `ACTIVATION_RADIUS = 999999` — sem culling por distância |
| 3 | **EnemyAISystem** (headless) | Pathfinding, aggro (SLEEP_RADIUS_TILES=40), ataque mob→player via `deal_damage()` | `MAX_PATHFINDS = 4`; multi-player: `_select_target` por mob |
| 4 | **EnemyAbilitySystem** (headless) | Habilidades especiais de mobs. **Ranged (range>1):** LOS check + cria `Projectile(ability_id=...)` em vez de `apply_effect` direto. **Melee (range=1):** `apply_effect` direto. Ativo em ATTACKING/CHASING/KITING | Alvo via `AIControlled.target_eid` |
| 5 | **StatusEffectSystem** (headless) | Ticks de DoT (poison, bleed, burn); expiração; slow_mult; PendingDeath por DoT | Subclasse de `core_systems.StatusEffectSystem`; `_emit_damage` → `_combat_this_tick` |
| 6 | **TileMovementSystem** (headless) | Avança `progress → current_tile` | Sem render, sem som de passos |

### Arquitetura de StatusEffectSystem

```
core_systems.py
  └─ StatusEffectSystem (base, sem Pygame)
        ├─ _emit_damage(eid, amount, etype, pos, color) → hook virtual
        └─ _emit_heal(eid, amount, etype, pos, color)   → hook virtual

systems.py
  └─ StatusEffectSystem(_CoreStatusEffectSystem, System)  ← cliente
        ├─ _emit_damage → FLT.add("-N", ...)
        └─ _emit_heal   → FLT.add("+N", ...)

server/world_server.py (inline _ServerSFX)
  └─ _ServerSFX(_CoreStatusEffectSystem)  ← servidor
        ├─ _emit_damage → _combat_this_tick.append({outcome="hit", source="poison"...})
        └─ _emit_heal   → _combat_this_tick.append({outcome="regen", ...})
```

`apply_effect()` vive em `core_systems.py`; `systems.py` re-exporta para compatibilidade retroativa.

### SpawnZoneSystem headless

`ACTIVATION_RADIUS = 999999` — desativa culling por distância de player.
O servidor processa todas as zonas independente de conexões ativas (mundo persiste).

**Fix `_pending_spawns`:** atributo dinâmico adicionado em runtime ao componente `SpawnZone`.
Incrementado a cada tentativa de spawn no tick; decrementado no ciclo seguinte.
Evita que uma zona spawne `max_count` mobs de uma vez ao voltar de cooldown.

```python
_spawn_sys = SpawnZoneSystem(self.world)
_spawn_sys.ACTIVATION_RADIUS = 999999
```

### EnemyAISystem headless

Parâmetros herdados do offline, funcionam sem mudança:
- `SLEEP_RADIUS_TILES = 40` — distância máxima de aggro
- `MAX_PATHFINDS = 4` — A* limitado por tick para performance
- `_select_target(mob_eid)` — seleciona o player mais próximo no raio de aggro
- Armazena alvo em `AIControlled.target_eid`
- Chama `deal_damage(mob_eid, player_eid)` → HP diff detectado em `_process_player_attacks`

### CombatStateSystem inline (no `_tick`)

Lógica core (rage decay, HP5, regen de mana, stun timer, etc.) NÃO é mais
duplicada — vive em `core_systems.py::BaseCombatStateSystem` (puro, sem
Pygame), herdada por `systems.CombatStateSystem` (cliente) e
`core_systems.ServerCombatStateSystem` (servidor, adiciona `hp5_events`/
`mana_events`/`proc_events`). `server/world_server.py::_tick()` chama
`self._combat_state_sys.update(self._player_eids, dt)` pra todos os
players conectados a cada tick.
- `RAGE_DECAY_AMOUNT = 5`, `RAGE_DECAY_INTERVAL = 3.0s`
- HP5: `max(1, int(max_hp * hp5)) a cada 5s` fora de combate — emite
  `{outcome="regen", damage=negative}` em `_combat_this_tick` (cliente
  exibe `+N HP`)
- Regen de mana (Mago): `max(1, int(max_mana * pct))` a cada
  `MANA_REGEN_INTERVAL=5s` — 4% fora de combate, 1% em combate
  (`_tick_mana_regen`). **Único produtor autoritativo** — enfileira em
  `_pending_xp_deliveries` (vira `STATS_UPDATE`). O cliente
  (`spell_system.ManaSystem`) só prediz esse regen quando offline
  (`self._net` não setado); online, espera o `STATS_UPDATE` do servidor —
  ver `PROBLEMAS_ARQUITETURA.md` (bug real: cliente regenerava mana
  sozinho sem o servidor saber, causando "Mana insuficiente" com o HUD
  mostrando mana de sobra).

### Progresso de quest — `quest_logic.py` + `WorldServer._process_quest_events`

Lógica pura (sem Pygame) extraída de `quest_system.py` pra `quest_logic.py`
(módulo top-level, paralelo a `quests_data.py`/`quest_events.py`) — usada
pelo cliente (caminho offline) E pelo servidor (caminho online,
autoritativo). Funções: `match_objective`, `apply_event`, `try_start`,
`complete_quest`, `can_turn_in`, `sync_collect_progress`,
`sync_learn_skill_progress`, `roll_conditional_loot`.

`WorldServer._process_quest_events()` roda 1x por tick (mesmo lugar de
`_sync_player_skill_levels_dirty`): drena `quest_events.QUEST_EVENTS`
(eventos com `player_eid` explícito — `fire(event_type, player_eid=eid,
**data)`), aplica progresso via `quest_logic.apply_event`, e sincroniza
sem evento dedicado `collect_item` (contra `Inventory`) e `learn_skill`
(contra `PlayerSkills.learned_skill_ids`). Dirty-check por player → push
privado `QUEST_UPDATE` (nunca AOI).

Gatilhos server-side (`quest_fire`) por tipo de objetivo:
- `kill` → `server/server_death_handler.py` (first-attacker)
- `use_skill` → `server/spell_completion_processor.py::_process_spell_cast_completions`
  (cast-time) + `server/skill_processor.py` (instantâneas)
- `use_consumable` → `server/world_server.py::apply_consumable`
- `equip_item` → `server/world_server.py::update_player_equipment`
- `reach_tile` → `server/world_server.py::move_player`
- `reach_level` → `stats_system.py::process_levelups` (já roda nos dois lados)
- `talk_to_npc` → aplicado inline em `_handle_quest_accept`/`_handle_quest_turn_in`
  (payload leva `npc_name`)

`QUEST_ACCEPT`/`QUEST_TURN_IN` (C→S) são processados em
`server/session.py` — validam contra o `QuestLog`/`CharacterStats`/
`PlayerSkills` do PRÓPRIO servidor, nunca confiam no cliente. Persistência:
`quests_json` (mesmo padrão de `skill_levels_json` — sempre servidor, ver
`PROBLEMAS_ARQUITETURA.md`).

`QuestDef.class_req` (`quests_data.py`, default `""` = qualquer classe) —
restringe a quest a uma classe (ex: `"mago"`). Validado em
`quest_logic.try_start` (servidor/offline). Quest com classe errada é
TOTALMENTE invisível pro player (não aparece nem como bloqueada) —
`QuestDialogSystem._get_available_quests`/`_get_locked_quests` filtram por
`self._qs._player_class_id()`. Cadeia de quests por classe: encadear via
`requires=(quest_anterior,)`/`next_quest` (já existia, reaproveitado).

### `_apply_final_damage(target_id, dmg) -> bool` — gate centralizado de dano

Definido em `server/spell_completion_processor.py` (mixin `SpellCompletionProcessorMixin`).

**Único lugar no servidor onde `CombatStats.current_hp` é decrementado por dano.**

```python
def _apply_final_damage(self, target_id: int, dmg: int) -> bool:
    cs = self.world.get_component(target_id, CombatStats)
    if not cs or cs.current_hp <= 0:
        return False          # alvo já morto
    cst = self.world.get_component(target_id, CombatState)
    if cst and cst.is_immune:
        return False          # Bloco de Gelo, etc.
    cs.current_hp -= dmg
    return True
```

**Regra:** nenhum código de skill/combate do servidor deve modificar `current_hp` diretamente. Para adicionar redução de dano, resistências ou novos estados de imunidade: editar **apenas aqui**.

Retorna `False` quando bloqueado (sem HP, imune); `True` quando aplicado. HP pode ficar negativo (overkill preservado para cálculo de dano real em chamadores).

Caminhos que delegam para `_apply_final_damage`:
- `_server_apply_magic_damage` — Bola de Fogo, Calcinar, Nova Congelante, Calamidade Flamejante. Param `school` (Skill Level — ver seção abaixo) aplica resistência do alvo + bônus de Magic do atacante
- `_server_apply_ranged_physical` — Flecha Reiterada, Picada de Escorpião, Tiro Repulsivo, Tiro Múltiplo (por flecha). Aplica bônus de Arco/Escudo/Defesa (Skill Level)

Caminho fora do mixin (tem guarda própria já há mais tempo):
- `deal_damage()` em `systems.py` — auto-attack player→mob e player→player; já checava `is_immune`

### `_process_skill_requests` — pipeline de skills no servidor

Executado **antes** do auto-attack a cada tick:

1. Busca objeto `Skill` no `PlayerSkills` local (estado de cargas preservado)
2. Fallback: instância temporária do `SKILL_CATALOG`
3. **Lag compensation pixel-based:**
   - Mob: snapa `current_tile → target_tile` se `progress ≥ 0.5` (espelha predição do cliente)
   - Player: sempre snapa para `target_tile` (client-side prediction)
   - Snapa `Position.x/y` também (não só tile) — range check usa pixels
   - Restaura posições após o handler
4. Injeta `tile_move._server_dir_x/_server_dir_y` para skills direcionais
5. Chama `_skill_{sid}()` do `SkillSystem` instanciado (`self._skill_system`)
6. Detecta movimento do player (Interceptar) → `_skill_position_corrections`
7. Coleta HP diff dos mobs → monta `targets` do `SKILL_RESULT`
8. Sincroniza rage/mana/HP em `_pending_xp_deliveries`

### `_process_player_attacks` — auto-attack

**Player→Mob:**
- Verifica `CombatState.target_entity_id` do player
- Range check: Chebyshev tiles (1 melee, 7 ranged se `is_ranged`)
- Cooldown via `_attack_timers[session_id]`
- Usa `deal_damage(player_eid, mob_eid, "physical")` do offline
- Rage: +5 por ataque disparado (mesmo em miss)
- Registra em `_mob_damage_log` para XP proporcional

**Mob→Player:**
- Detectado via HP diff vs `player_hp_snap` capturado antes de `_systems.update`
- Identifica atacante via `AIControlled.target_eid` dos mobs
- Se HP ≤ 0: remove `PendingDeath` (colocado por deal_damage), chama `_handle_player_death`

### ServerDeathHandler

Processa entidades com `PendingDeath` a cada tick:

1. Calcula XP base: `level_do_mob × MOB_TABLE[nome]["xp_given_by_lvl"] ×
   ENEMY_TIER_CONFIGS[tier]["xp"]` (mob_definitions.py/entity_factory.py).
   Mob sem cadastro (ex: "Elemental") cai no fallback antigo flat por tier
   (`_XP_BY_TIER`: normal=50, elite=150, rare=300, boss=1000)
2. XP proporcional: divide por dano total do `_mob_damage_log`
3. Vitória Iminente: killer ganha carga se tiver skill na hotbar
4. Determina first-attacker (primeiro a atacar = dono do loot)
5. Rola loot via `roll_mob_loot(mob_name, tier)` + `roll_coins(tier)`
6. Notifica `SpawnZone`: remove de `active_entity_ids`, adiciona `respawn_timer`
7. `remove_entity()` + `pending_despawns`

### RespawnMixin — fluxo de morte/espírito (ghost) + cemitério (C30)

`server/respawn_system.py` — mixin de `WorldServer`. Ver `ARQUITETURA_ONLINE.md →
Fluxo de morte/espírito (ghost) + cemitério` para o fluxo completo.

- `_tick_respawn_immunity()` — chamado em `_tick`; decrementa
  `CombatState.respawn_immunity_ticks`, restaura `is_visible=True` ao expirar.
- `_handle_player_death(player_eid)` — chamado quando HP do player chega a 0
  (PvE ou PvP). Limpa efeitos/channeling/spells em voo/aggro de mobs (igual
  versão antiga); marca `GhostState.is_dead=True`, `corpse_tx/ty`=posição da
  morte; corpo fica visível (`is_visible=True`, `current_hp==0` já bloqueia
  dano/alvo em todo `combat_processor`/`spell_completion_processor`); registra
  em `_player_corpses`; emite `_player_deaths_this_tick` e `_entity_deaths_this_tick`.
- `_handle_release_spirit(player_eid)` — chamado pelo handler de `RELEASE_SPIRIT`.
  `GhostState.is_ghost=True`; teleporta (`_moved_this_tick`, `teleport=True`)
  pro `RESPAWN_TILE`; `CombatState.is_visible=False` (ghost intangível/invisível,
  reaproveita regra de Camuflagem); spawna marcador `kind="player_corpse"`
  (eid sintético `PLAYER_CORPSE_EID_BASE + player_eid`) no local da morte.
- `_tick_ghost_states(dt)` — chamado em `_tick` para cada ghost ativo: timer de
  `GHOST_GRAVEYARD_REVIVE_S` no raio do cemitério (`GHOST_GRAVEYARD_RADIUS_TILES`)
  → revive automático full HP; calcula `near_corpse` (raio
  `GHOST_CORPSE_RADIUS_TILES`) e emite `GHOST_STATE` ao mudar.
- `_revive_player(player_eid, hp_frac, at_corpse)` — restaura HP/mana, reseta
  `GhostState`, `respawn_immunity_ticks=80`; se `at_corpse`, teleporta pro
  local do corpo e remove o marcador `player_corpse`; emite
  `_player_revives_this_tick` (→ `PLAYER_REVIVE`).
- `move_player()` (world_server.py) — bypass de CC/walkable para
  `GhostState.is_ghost` (intangível, só valida 1 tile de distância).

### PLAYER_STAT_SYNC — OBSOLETO (handler é no-op)

`_handle_player_stat_sync` em `session.py` **não faz mais nada** — o mecanismo
antigo (cliente envia stats efetivos calculados localmente; servidor confiava
e sobrescrevia via `sync_player_combat_stats`/`_apply_stat_overrides`/
`_player_stat_overrides[eid]`) foi removido por confiar em valores que o
cliente podia forjar (ver `PROBLEMAS_ARQUITETURA.md`, Tier A/F). Substituído
por `WorldServer._apply_equipment_modifiers`/`_apply_talent_modifiers`, que
derivam os modificadores de `CombatStats` a partir do `Equipment`/`TalentTree`
REAIS já validados no servidor — chamados em `spawn_player`,
`update_player_equipment` e sempre que talentos são realocados. Mantido só
por compat de protocolo (mensagem ainda existe em `shared/messages.py`).

### Skill Level (Tibia-like) — hooks de xp e bônus no servidor

Ver `COMPONENTES_ECS.md` → "Skill Level" pro componente `SkillLevels` e os
campos derivados em `CombatStats`. Funções puras de xp/bônus em
`stats_system.py` (`grant_skill_xp`, `apply_skill_bonuses_to_combat`,
`weapon_skill_extras`, `defense_skill_extras`, `grant_weapon_skill_xp`,
`grant_defense_skill_xp`, `grant_resist_skill_xp`) — só o servidor as chama.

Pontos de integração (todos já existentes, estendidos — não criou funil novo):
- **Magic** (cast): `_process_spell_cast_completions` concede 1 xp quando
  `mana_cost` efetivo deduzido > 0 (exclui Bloco de Gelo, custo real 0).
- **Arco/Escudo/Defesa** (Arqueiro): `_server_apply_ranged_physical` lê
  `weapon_skill_extras`/`defense_skill_extras` do atacante/alvo, passa como
  `extra_acerto/extra_crit/extra_block/extra_avoid` pro
  `resolve_attack_outcome` existente, e concede xp via `grant_weapon_skill_xp`
  (atacante)/`grant_defense_skill_xp` (alvo) — independente de hit/miss.
- **Arma/Escudo/Defesa, todas as classes** (auto-attack):
  `CombatSystem.deal_damage`/`_calculate_damage` (systems.py) — mesmo padrão
  acima. **`CombatSystem` é compartilhado client+server**; novo flag
  `is_server: bool` (só `True` na instância de `world_server.py`) gateia as
  chamadas de `grant_*` — leitura dos bônus já calculados é segura nos dois
  lados (cliente nunca popula esses campos, ficam 0).
- **Resistência mágica** (Mago): `_server_apply_magic_damage` recebeu novo
  param `school: "fogo"|"gelo"|"natureza"`; aplica `magic_skill_dmg_bonus`/
  `magic_skill_crit_bonus` do atacante e `apply_resistance_reduction` +
  `grant_resist_skill_xp` no alvo quando `school` setado. Callers (Bola de
  Fogo, Calcinar, Nova Congelante, Calamidade Flamejante) passam a escola.
- **Resistência em DoT**: `core_systems.StatusEffectSystem._apply_tick` —
  mapa `DOT_SCHOOL = {"poison": "natureza", "burn": "fogo"}`; aplica
  `apply_resistance_reduction` e o hook virtual `_on_resisted_dot(eid, school)`
  (base no-op; servidor sobrescreve em `_ServerStatusEffectSystem` pra
  conceder xp). **Sangramento (bleed) não muda** — físico, sem resistência,
  como antes.

Persistência: coluna `skill_levels_json` em `server/auth.py` (mesmo padrão
`ALTER TABLE` de `fog_json`); `get_player_save_data`/`_build_save_merge`
sempre do componente vivo do servidor, nunca do payload do cliente.

---

## Sistemas do Cliente (Online) — modificações no branch online

### `_use_skill_visual_only` (em `SkillSystem`)

Substitui `_use_skill` em modo online (`self._server_authoritative = True`).

Fluxo:
1. Verifica GCD local (`PlayerSkills.gcd_timer > 0`)
2. Verifica cooldown local da skill
3. Verifica range local (usa mesmos handlers de range do servidor para fail_flash)
4. Se válido: seta `skill._server_pending = True`, `_server_pending_timeout = 0.40s`
5. Envia `CAST_SKILL` ao servidor
6. Aguarda `SKILL_RESULT`: confirma + aplica `GCD_DURATION = 0.8s`
7. Timeout sem resposta → libera com `GCD_DURATION * 0.5` (fallback)

`fail_flash_timer = 0.2s` — slot escurece visualmente quando range inválido.

### `SkillHandlers` — constantes de range e helpers de alvo

```python
MELEE_RANGE_PX: float = 72.0           # 2.25 tiles — cobre diagonal + kiting lag
INTERCEPT_MIN_RANGE_PX: float = 44.0   # 2 tiles - tolerance
INTERCEPT_MAX_RANGE_PX: float = 232.0  # 6 tiles + tolerance
RANGE_TOLERANCE_PX: float = 40.0       # tolerância genérica
```

`_range_ok(player_pos, target_pos, max_px, min_px)` — hitbox circular em `Position.x/y` (interpolada).
`_melee_ok` é alias para `_range_ok(pos, pos, MELEE_RANGE_PX)`.

`is_ability=True` em `damage_calculator.resolve_attack_outcome` → zera `miss_chance` para skills.

#### `_target_alive(target_id) -> bool`

Centraliza a checagem "alvo está vivo", cobrindo **mobs/players locais (`CombatStats`) e players remotos (`RemoteControlled`, sem `CombatStats`):**

```python
def _target_alive(self, target_id: int) -> bool:
    cs = self.world.get_component(target_id, CombatStats)
    if cs is not None:
        return cs.current_hp > 0
    rc = self.world.get_component(target_id, RemoteControlled)
    return rc is not None and rc.hp > 0
```

**Regra:** todos os handlers de skill que verificam validade do alvo devem usar `_target_alive` — nunca acessar `CombatStats.current_hp` diretamente. Garante que skills funcionem em PvP (players remotos não têm `CombatStats`).

### SpawnZoneSystem no cliente online

`_spawn_entities_from` em modo online pula criação de entities do tipo `enemy` e `spawn_zone`.
O mundo de mobs é gerenciado exclusivamente pelo servidor — cliente só renderiza o que recebe via AOI.

### SoundManager — áudio espacial (online)

`play_mob_sounds_at(mob_sounds_comp, event, sx, sy, lx, ly)`:
- `sx/sy` = posição em pixels do som (mob)
- `lx/ly` = posição em pixels do listener (player)
- Volume calculado por `volume_at()`: linear de 100% a 0 em 10 tiles (320px)

Fluxo online:
1. Servidor detecta `IDLE → AGGRO_DELAY` → emite `sound_event {kind="mob_aggro", mob_eid, tx, ty}`
2. `SessionManager` envia `SOUND_EVENT` para players no AOI
3. Cliente recebe, busca `MobSounds` do mob local, chama `play_mob_sounds_at`

---

## Regra de separação cliente/servidor

| Responsabilidade | Onde roda | Justificativa |
|-----------------|-----------|---------------|
| Cálculo de dano | Servidor | Anti-cheat |
| Posição de entidades | Servidor (canônico) | Anti-teleporte |
| IA de mobs | Servidor | Consistência entre clientes |
| Animações, partículas | Cliente | Cosmético |
| Previsão de movimento | Cliente (parcial) | Fluidez |
| Range check de skill | Ambos | Servidor autoritativo; cliente para fail_flash |
| Aiming de cone (Pirofagia, Tiro Múltiplo) | Cliente envia dir, servidor valida | Lag compensation |
| Invisibilidade (Camuflagem) | Servidor não envia posição a outros | Segurança |

---

## Protocolo de adição de sistema no servidor

1. Criar classe em `server/` herdando de `System` do `world.py` (ou classe simples com `update(dt)`)
2. Instanciar em `WorldServer._load_map()` e adicionar a `self._systems`
3. Se gerar deltas → adicionar campo em `WorldServer._collect_deltas()`
4. Se precisar lag compensation → usar `WorldServer.get_snapshot_at(tick)`
5. Documentar na tabela acima com status ✅

---

## Serviços de sistema (módulo-nível)

Registrados via `register_services()` em `world_server._load_map`:

```python
from systems import deal_damage, find_path, is_tile_walkable, get_tilemap, get_mainhand_weapon
```

| Função | Implementação real | Para quê |
|--------|-------------------|----------|
| `deal_damage(attacker, target, type, ...)` | `CombatSystem.deal_damage` | Aplica dano + feedback |
| `find_path(start, end, obstacles, ...)` | `PathfindingSystem.find_path` | A* com bounded search |
| `is_tile_walkable(entity, tx, ty, ...)` | `TileValidationSystem.is_tile_walkable` | Valida caminhabilidade |
| `get_tilemap()` | `PathfindingSystem._get_tilemap_component` | Acessa Tilemap component |
| `get_mainhand_weapon(world, entity)` | helper | Retorna item da mainhand |
| `apply_effect(world, eid, type, dur, mag)` | module-level em systems.py | Aplica/refresha status effect |
| `sync_attack_interval(cs, equip)` | stats_system.py | Sincroniza velocidade de ataque |

---

## Classe base `System`

```python
class System:
    world_surf: pygame.Surface   # surface de mundo (zoom_surf) — atribuída por _assign_world_surf()
    hud_surf:   pygame.Surface   # surface da tela nativa

    def update(self, events, dt): ...
    def render(self, cam_x=0, cam_y=0): ...
```

> No servidor, `world_surf` e `hud_surf` não são usadas — sistemas headless ignoram render.

---

> **Problema de qualidade:** ShopSystem, LootSystem e CraftingSystem misturam UI e lógica de negócio. Ver `PROBLEMAS_ARQUITETURA.md` problema #9.
