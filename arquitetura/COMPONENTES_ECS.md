# Componentes ECS — Referência Completa

> Todos os componentes em `components.py`. Onde são criados e onde são lidos.
> Última atualização: 2026-05-22

---

> **Problema de escala:** `CharacterStats` mistura dados permanentes com state volátil de combate. Ver `PROBLEMAS_ARQUITETURA.md` problema #1 para o plano de separação em `CharacterStats` + `CombatRuntime`.

## Componentes de Posição e Visual

| Componente | Campos principais | Criado em | Lido por |
|-----------|-------------------|-----------|----------|
| `Position(x,y,prev_x,prev_y)` | posição pixel | entity_factory | RenderSystem, CameraSystem, todos |
| `Renderable(color,width,height)` | cor e tamanho | entity_factory | RenderSystem |
| `Collider(width,height)` | hitbox | entity_factory | TileValidationSystem |
| `Camera(target_entity_id,offset_x,offset_y,zoom)` | state de câmera | entity_factory | CameraSystem, TileRenderSystem |
| `Visible()` | tag — entidade no FOV | FogSystem | RenderSystem, EnemyAISystem. **Online:** adicionada manualmente no servidor (FogSystem não roda headless) |

---

## Componentes de Movimento

| Componente | Campos principais | Notas |
|-----------|-------------------|-------|
| `TileMovement(current_tile_x/y, target_tile_x/y, progress, speed, slow_mult, elevation, is_dash)` | estado de movimento em grid | `slow_mult` setado por StatusEffectSystem. **Online:** `_server_dir_x/_server_dir_y` injetados em runtime por `_process_skill_requests` para skills direcionais |
| `PlayerAutoMove(active, path, ground_target)` | auto-move por clique | path é lista de (tx,ty) |
| `InitialPosition(x,y)` | posição de spawn do mob | usado por DeathHandlerSystem para respawn zone |

### Atributos runtime de TileMovement (online)

Injetados dinamicamente em `_process_skill_requests` antes de chamar o handler:

| Atributo | Tipo | Valor | Usado por |
|---------|------|-------|-----------|
| `_server_dir_x` | `float` | direção X normalizada do CAST_SKILL | Skills de cone (Pirofagia, Tiro Múltiplo) — **pendente C1** |
| `_server_dir_y` | `float` | direção Y normalizada do CAST_SKILL | Idem |
| `_server_move_grace` | `float` | janela (s) decrescente após cada `move_player()` aceito | `ServerCombatStateSystem._tick_player_move_grace` — infere `is_moving=True` de PLAYER no servidor (que faz snap instantâneo, sem tween real). Sem isso, `is_moving` de player nunca era `True` server-side — quebrava reset de "Calmo e Certeiro" e regen de Concentração (ver PROBLEMAS_ARQUITETURA.md) |

---

## Componentes de Combate

| Componente | Campos principais | Notas |
|-----------|-------------------|-------|
| `CombatStats` | 40+ atributos base + efetivos + **25+ flags de talento** | ver seção FLAGS abaixo |
| `CombatState(is_alive, in_combat, is_stunned, is_rooted, is_casting, is_immune, target_entity_id, is_pursuing, combat_timer, stun_timer)` | estado de combate | `can_act()` e `can_move()` são queries puras. **Online:** `respawn_immunity_ticks: int` e `is_visible: bool` adicionados em runtime para imunidade pós-morte |
| `CharacterStats(STR,INT,AGI,VIT,DEF, level, xp, rage, mana, embalo_charges, fire_instant_ready, thermal_shock_active, pnq_counter, fatiador_timer…)` | atributos e progressão | mistura permanente + temporal (ver problemas) |
| `PermanentStats(STR,INT,AGI,VIT,DEF)` | bônus roguelike acumulados na morte | somado a CharacterStats em apply_char_stats_to_combat |
| `XPReward(amount)` | XP dado ao matar | criado em create_enemy; lido por DeathHandlerSystem |
| `EnemyTier(tier)` | "normal"/"elite"/"rare"/"boss" | multiplica HP e dano; XP base: 50/150/300/1000 |
| `PendingDeath(killer_entity_id)` | marcador de morte a processar | adicionado por CombatSystem ou sweep de HP≤0 no servidor |
| `GhostState(is_dead, is_ghost, corpse_tx, corpse_ty, graveyard_timer, near_corpse)` | fluxo de morte/espírito (C30) | `is_dead`: corpo no local da morte, espírito ainda não liberado. `is_ghost`: espírito liberado (intangível, invisível, no cemitério/explorando). `corpse_tx/ty`: tile da morte. `graveyard_timer`: segundos contínuos no raio do cemitério. `near_corpse`: dentro do raio de revive do corpo (mostra prompt "Reviver agora?"). Adicionado ao player em `create_player()` (entity_factory.py) |

### CombatStats — PLAYER_STAT_SYNC (OBSOLETO, removido)

`PLAYER_STAT_SYNC`/`COMBAT_SYNC_STATS` foram **removidos** (handler é no-op, dict
apagado de `shared/constants.py`) — o servidor confiava direto em valores de
attack_power/crit_rating/armor/etc. que o CLIENTE calculava e enviava, sem
validar contra equipamento/talentos reais (ver `PROBLEMAS_ARQUITETURA.md`,
Tier A/F). Substituído por `WorldServer._apply_equipment_modifiers`/
`_apply_talent_modifiers`, que derivam os modificadores de `CombatStats` a
partir do `Equipment`/`TalentTree` REAIS já validados no servidor.

> ⚠️ **Problema de escala:** CombatStats tem 80+ campos + 25 flags de talento. Ver `PROBLEMAS_ARQUITETURA.md` problema #2 para o plano de migração para `talent_flags: dict`.

### CombatStats — bônus derivados de Skill Level (Tibia-like)

Campos recalculados sob demanda por `stats_system.apply_skill_bonuses_to_combat`
(level-up de skill ou spawn/login) — nunca via `Modifier`. Servidor concede xp
e recalcula; cliente nunca chama essa função, então esses campos ficam sempre
`0.0`/`{}` no cliente (leitura é sempre segura nos dois lados):

| Campo | Trilha de origem | Onde é lido |
|------|-------------------|-------------|
| `weapon_skill_bonus: dict[str, float]` | machado/espada/maca/arco/baculo | `stats_system.weapon_skill_extras` → `resolve_attack_outcome(extra_acerto, extra_crit)` |
| `shield_skill_block_bonus: float` | escudo (só conta se offhand.item_type=="shield") | `stats_system.defense_skill_extras` → `extra_block` |
| `defense_skill_avoid_bonus: float` | defesa | `stats_system.defense_skill_extras` → `extra_avoid` (soma em dodge E parry) |
| `resist_fogo/resist_gelo/resist_natureza: float` | resist_fogo/resist_gelo/resist_natureza | `damage_calculator.apply_resistance_reduction` (dano mágico + DoT poison/burn) |
| `magic_skill_dmg_bonus`/`magic_skill_crit_bonus: float` | magic | `_server_apply_magic_damage` (dano%) e `resolve_attack_outcome(extra_crit)` |

### FLAGS de talento em CombatStats

**Build Cavaleiro (guerreiro):**
```
explorador_crit_per_point    — cav_explorador: bônus de crit vs slow
foco_mortal_enabled          — cav_foco_mortal: +dano por debilitate_elapsed
embalo_on_crit               — cav_embalo: gera carga ao crit
embalo_bonus_per_charge      — cav_embalo: % bônus em Golpe Poderoso
golpe_poderoso_rage_cost     — cav_veterano: custo reduzido (default 15)
interceptar_cooldown_reduction — cav_sede_batalha: reduz CD
interceptar_stun_duration    — cav_alvo_confirmado: duração stun
interceptar_rage_bonus       — cav_vontade: raiva gerada
pnq_enabled                  — cav_punho_queixo: habilita contador
pnq_stun_duration            — cav_punho_queixo: duração stun
impacto_maquina_matar        — cav_maquina_matar: +dmg por alvo
impacto_assassino            — cav_assassino: proc Executar grátis
executar_horrorizante        — cav_horrorizante: medo ao não matar
```

**Build Piromania (mago):**
```
fire_mana_discount           — pir_frieza: desconto flat na mana de BdF
fire_cast_time_reduction     — pir_bdf_aperfeicoada: reduz cast time BdF
fire_burns_on_crit           — pir_queimaduras: ativa burn em crit BdF
fire_burn_duration           — pir_queimaduras: duração do burn (pts×3s)
ice_cast_time_reduction      — pir_precisao_elemental: reduz cast time NC
fire_shield_enabled          — pir_escudo_fogo: retaliação de fogo ativa
fire_instant_proc_chance     — pir_chama_interna: % chance proc BdF grátis
thermal_shock_enabled        — pir_choque_termico: ×2 dano fogo em frozen
pyromania_bonus              — pir_piromaníaco: % dano e desconto mana
elemental_lapse_crit_bonus   — pir_lapso_elemental: crit% durante proc
fire_crit_counter            — contador de crits para Lapso (0→3)
fire_crit_timer              — janela de 6s para acumular 3 crits
fire_exhaustion_enabled      — pir_exaustao: slow progressivo por BdF
crematoria_enabled           — pir_crematoria: +25% dano em <20% HP
```

---

## Componentes de Habilidades

| Componente | Campos principais | Notas |
|-----------|-------------------|-------|
| `Skill(name,desc,cooldown,rage_cost,mana_cost,mana_cost_pct,cast_time,cast_range,school,proc_attr,charges,offensive,interruptible)` | skill ativa | `offensive=False` → não inicia combate/perseguição; criada por `_make_skill(id, SKILL_CATALOG)` |
| `PlayerSkills(skills[10], keybinds[], learned_skill_ids, gcd_timer)` | hotbar e skills aprendidas | `_CHARGE_BASED` define skills de carga; `GCD_DURATION = 0.8s` |

### Skill — atributos runtime online

Adicionados dinamicamente ao objeto `Skill` pelo sistema online (não são campos `__init__`):

| Atributo | Tipo | Valor padrão | Descrição |
|---------|------|-------------|-----------|
| `_server_pending` | `bool` | `False` | Skill enviada ao servidor, aguardando `SKILL_RESULT` para confirmar |
| `_server_pending_timeout` | `float` | `0.0` | Segundos até liberar sem confirmação (fallback = 0.40s) |
| `fail_flash_timer` | `float` | `0.0` | Slot fica escuro por 0.2s ao falhar range check localmente |

Esses atributos existem na classe `Skill.__init__` (`fail_flash_timer` está lá; `_server_pending` é injetado em runtime pelo `SkillSystem._use_skill_visual_only`).

### PlayerSkills — notas online

| Campo | Valor | Mudança vs. master |
|-------|-------|-------------------|
| `GCD_DURATION` | `0.8s` | Era `0.5s` no master |
| `_GCD_SKILLS` | `{"golpe_poderoso", "executar", "polimorfia"}` | Apenas algumas skills disparam GCD |

---

## Componentes de Magia

| Componente | Campos | Notas |
|-----------|--------|-------|
| `SpellCast(spell_id, cast_time, elapsed, target_id, mana_cost, interruptible)` | cast em andamento | mana deduzida só ao completar |
| `Channeling(spell_id, duration, elapsed, tick_interval, mana_per_tick, target_x/y, radius_tiles, slow_pct, dmg_weapon_pct, dmg_sp_coeff)` | canalização ativa | apenas Calamidade Flamejante |
| `PlayerProjectile(spell_id, attacker_id, target_id, speed, dmg_weapon_pct, dmg_sp_coeff, color)` | projétil do mago em voo | Bola de Fogo |
| `AoeTargeting(spell_id, radius_tiles, cast_range_tiles, pending_x/y, waiting_for_range, cancel_pending)` | mira AOE de alvo | Calamidade Flamejante |
| `PirofagiaAiming(elapsed)` | mira de cone ativa | presente enquanto player aponta; removido ao clicar |
| `IceBlockEffect(duration, elapsed, heal_interval, last_heal)` | Bloco de Gelo ativo | imunidade + regen |
| `FireShieldEffect(duration, elapsed)` | Escudo de Fogo ativo | retaliação em atacantes |

---

## Componentes de Inventário e Economia

| Componente | Campos | Notas |
|-----------|--------|-------|
| `Inventory(items[], max_slots=20)` | lista de Items | |
| `Equipment(slots{slot→Item})` | 9 slots de equipamento | mainhand, offhand, head, chest, shoulders, gloves, boots, wrists, ring, neck |
| `Item(name, item_type, slot, modifiers[], rarity, value, damage_min/max, attack_speed, proc, consumable, armor_class)` | item de jogo | `armor_class`: "placa"/"couro"/"tecido"/"" — restrição via `CLASS_ARMOR_ALLOWED` |
| `Modifier(attribute, value, type)` | modificador de stat | type: "flat" ou "percentage" |
| `Wallet(gold)` | ouro do jogador | **Online:** sincronizado no save via `_build_save_merge` (cliente autoritativo) |
| `ConsumableBar(slots[], keybinds[], global_cooldown)` | barra de consumíveis | `GCD_DURATION = 1.5s` |
| `LearnedRecipes(known[])` | receitas aprendidas | modificado via `stat_fns.learn_recipe()` |

---

## Componentes de Status e IA

| Componente | Campos | Notas |
|-----------|--------|-------|
| `StatusEffects{effects{type→ActiveEffect}}` | efeitos ativos | `has()`, `get()`, `remove()` |
| `ActiveEffect(effect_type, duration, magnitude, tick_interval, _tick_elapsed)` | efeito singular | magnitude = dano/cura por tick |
| `ActiveRegen(heal_per_tick, interval, ticks_total, ticks_remaining, tick_timer)` | regen ativa (consumíveis) | ConsumableSystem |
| `AIControlled(state, path, attack_range_tiles, is_ranged, entity_class, disengage_cd, kite_*, ranged_cast_timer, aggroed_by_damage, target_eid=-1)` | IA do mob | state: IDLE/AGGRO_DELAY/CHASING/RETURNING/ATTACKING; `target_eid` = eid do alvo atual (-1 = sem alvo) |
| `EnemyAbilities(slots[EnemyAbilitySlot])` | habilidades especiais | EnemyAbilitySystem |
| `Projectile(attacker_id, target_id, damage_type, speed)` | projétil inimigo | |

---

## Componentes de Mundo

| Componente | Campos | Notas |
|-----------|--------|-------|
| `Tilemap(tile_matrix, terrain_matrix, object_matrix, terrain_visual, map_width_tiles, map_height_tiles, tile_size)` | dados do mapa carregado | |
| `FogOfWar(radius, explore_radius, visible, explored, _explored_maps, _last_tile)` | estado de neblina | `visible` = set de tiles visíveis no frame |
| `Visible()` | tag adicionada/removida por FogSystem | no servidor: adicionada manualmente no spawn de mobs (FogSystem não roda) |
| `MapLocation(map_file)` | mapa ao qual esta entidade pertence | adicionado a mobs/NPCs/spawn_zones pelo `_load_map_for()`; players usam `WorldServer._player_maps` |

---

## Componentes de NPC, Mundo e Sons

| Componente | Campos | Notas |
|-----------|--------|-------|
| `NPC(name, level, profession)` | dados de NPC genérico | base para Merchant, QuestGiver, Trainer, Blacksmith |
| `Merchant(shop_id)` | referência ao shop em `merchant_data.SHOPS` | |
| `QuestGiver(quest_ids[], turn_in_ids[])` | quests disponíveis | |
| `Trainer(class_id)` | classe de habilidades ensinadas | |
| `Blacksmith(shop_id)` | ferreiro com crafting | |
| `QuestLog(active{qid→progresso[]}, completed{qid})` | estado de quests do jogador | |
| `SpawnZone(center_x/y, radius, enemy_type, enemy_tier, max_count, respawn_cooldown, level_min/max, race, entity_class, active_entity_ids, respawn_timers)` | zona de respawn | Gerenciada por SpawnZoneSystem |
| `SpawnZoneOwner(zone_entity_id)` | liga mob à sua zona | |
| `Corpse(loot[], coins, timer, looted, is_open)` | cadáver com drop | Offline only — servidor usa `_corpses` dict |
| `EntityIdentity(name, race, entity_class, level, tier)` | identidade completa do mob | lido por DeathHandlerSystem e QuestSystem |

### SpawnZone — atributo runtime online

| Atributo | Tipo | Adicionado por | Descrição |
|---------|------|---------------|-----------|
| `_pending_spawns` | `int` | `SpawnZoneSystem` (runtime) | Contador de spawns em andamento no tick. Evita spawnar `max_count` mobs de uma vez. Decrementado no ciclo seguinte. |

### MobSounds

```python
MobSounds(aggro="", death="", attack_melee="", attack_ranged="",
          attack_magic="", crit="", emote_attack="", emote_get_crit="")
```

Cada campo é a chave base do arquivo OGG (sem extensão).
Variações `_2`, `_3`, `_4` são tentadas automaticamente pelo `SoundManager`.

Como é usado:
- `SOUNDS.play_mob_sounds(comp, "aggro")` — offline, sem atenuação
- `SOUNDS.play_mob_sounds_at(comp, "aggro", sx, sy, lx, ly)` — online, com atenuação espacial
- Definido em `mob_definitions.py["sounds"]` por mob type
- Para adicionar som: criar `.ogg` em `assets/sounds/sfx/` + adicionar chave no `mob_definitions`

---

## Componentes de UI (state em component)

| Componente | Campos | Notas |
|-----------|--------|-------|
| `UIState(show_inventory, show_talents)` | visibilidade de painéis | player entity |
| `ShopUIState(open_merchant_id)` | qual loja está aberta | `is_open` property |
| `LootUIState(open_corpse_id)` | qual cadáver está aberto | |

---

## Talentos

| Componente | Campos | Notas |
|-----------|--------|-------|
| `TalentTree(chosen_build, allocated{}, available_points, _applied_modifiers[], _unlocked_skill_ids[])` | árvore de talentos | `chosen_build` derivado de `class_id` via `CLASS_BUILD_MAP`. **Online:** `allocated` enviado no `SAVE_STATE` e re-aplicado via `apply_talent_effects_to_player` no servidor |

## Skill Level (Tibia-like)

| Componente | Campos | Notas |
|-----------|--------|-------|
| `SkillLevels(levels{}, xp{})` | progressão por uso, 0-200 por trilha | `components.SKILL_IDS` = 11 trilhas: `machado, espada, maca, arco, baculo` (armas, agrupando os 10 `item.subtype` via `stats_system.WEAPON_SUBTYPE_TO_SKILL`), `escudo, defesa, resist_fogo, resist_gelo, resist_natureza, magic`. **Server-autoritativo**: só `stats_system.grant_skill_xp` (chamada apenas do servidor) escreve `levels`/`xp`. Anexado em `entity_factory.create_player` (cliente, vazio — só exibição) e `WorldServer.spawn_player` (servidor, carregado de `skill_levels_json`). Persistido via `server/auth.py` coluna `skill_levels_json` + `get_player_save_data`/`_build_save_merge` (sempre do componente vivo do servidor, nunca do payload do cliente). Fórmula de xp: `stats_system.skill_xp_for_level(level) = 20 × (level+1)^1.2` (`SKILL_XP_BASE`, ajustado de 100→20 após feedback de que o grind original estava difícil demais). Bônus: `stats_system.skill_bonus_pct(level)` linear 0%→15% (level 0→200), aplicado em `CombatStats` via `apply_skill_bonuses_to_combat` — ver seção "CombatStats — bônus derivados de Skill Level" acima. UI read-only em `skill_level_ui.py` (tecla L, `_show_skills` em game.py). Hooks de xp: cast de magia com `mana_cost` efetivo > 0 (Magic), `_server_apply_ranged_physical` (arco/escudo/defesa, Arqueiro), `CombatSystem.deal_damage` com `is_server=True` (arma/escudo/defesa, auto-attack todas as classes), `_server_apply_magic_damage` e `StatusEffectSystem._apply_tick` DoT poison/burn (resistências). **Live sync**: `WorldServer._sync_player_skill_levels_dirty()` (dirty-check por tick, mesmo padrão de `_sync_player_hp_dirty`) detecta qualquer mudança e envia `SKILL_LEVELS_UPDATE` (snapshot completo) só ao dono — sem isso o painel só atualizava no próximo login (bug real, ver PROBLEMAS_ARQUITETURA.md). Payload inclui `leveled_up: [{skill_id, level}]` quando algo subiu — cliente mostra "Parabéns, você subiu..." no `LOG` (chat) + centro da tela (`PROC`, igual Tibia) + som `levelup` (`client/network_handlers.py::_handle_msg_skill_levels_update`). Painel: 1 linha por trilha (nome + "Lv X (+Y%)" + barra com xp sobreposto centrado) — layout original de 2 linhas por trilha ficava alto demais |
