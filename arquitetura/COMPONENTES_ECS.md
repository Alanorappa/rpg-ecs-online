# Componentes ECS — Referência Completa

> Todos os componentes em `components.py`. Onde são criados e onde são lidos.

---

> ⚠️ **Problema de escala:** `CharacterStats` mistura dados permanentes com state volátil de combate. Ver `PROBLEMAS_ARQUITETURA.md` problema #1 para o plano de separação em `CharacterStats` + `CombatRuntime`.

## Componentes de Posição e Visual

| Componente | Campos principais | Criado em | Lido por |
|-----------|-------------------|-----------|----------|
| `Position(x,y,prev_x,prev_y)` | posição pixel | entity_factory | RenderSystem, CameraSystem, todos |
| `Renderable(color,width,height)` | cor e tamanho | entity_factory | RenderSystem |
| `Collider(width,height)` | hitbox | entity_factory | TileValidationSystem |
| `Camera(target_entity_id,offset_x,offset_y,zoom)` | state de câmera | entity_factory | CameraSystem, TileRenderSystem |
| `Visible()` | tag — entidade no FOV | FogSystem | RenderSystem, EnemyAISystem |

---

## Componentes de Movimento

| Componente | Campos principais | Notas |
|-----------|-------------------|-------|
| `TileMovement(current_tile_x/y, target_tile_x/y, progress, speed, slow_mult, elevation, is_dash)` | estado de movimento em grid | `slow_mult` setado por StatusEffectSystem |
| `PlayerAutoMove(active, path, ground_target)` | auto-move por clique | path é lista de (tx,ty) |
| `InitialPosition(x,y)` | posição de spawn do mob | usado por DeathHandlerSystem para respawn zone |

---

## Componentes de Combate

| Componente | Campos principais | Notas |
|-----------|-------------------|-------|
| `CombatStats` | 40+ atributos base + efetivos + **25+ flags de talento** | ver seção FLAGS abaixo |
| `CombatState(is_alive, in_combat, is_stunned, is_rooted, is_casting, is_immune, target_entity_id, is_pursuing, combat_timer, stun_timer)` | estado de combate | `can_act()` e `can_move()` são queries puras |
| `CharacterStats(STR,INT,AGI,VIT,DEF, level, xp, rage, mana, embalo_charges, fire_instant_ready, thermal_shock_active, pnq_counter, fatiador_timer…)` | atributos e progressão | mistura permanente + temporal (ver problemas) |
| `PermanentStats(STR,INT,AGI,VIT,DEF)` | bônus roguelike acumulados na morte | somado a CharacterStats em apply_char_stats_to_combat |
| `XPReward(amount)` | XP dado ao matar | criado em create_enemy; lido por DeathHandlerSystem |
| `EnemyTier(tier)` | "normal"/"elite"/"rare"/"boss" | multiplica HP e dano |
| `PendingDeath(killer_entity_id)` | marcador de morte a processar | adicionado por CombatSystem ou StatusEffectSystem (DoT) |

> ⚠️ **Problema de escala:** CombatStats tem 80+ campos + 25 flags de talento. Ver `PROBLEMAS_ARQUITETURA.md` problema #2 para o plano de migração para `talent_flags: dict`.

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
| `Skill(name,desc,cooldown,rage_cost,mana_cost,mana_cost_pct,cast_time,cast_range,school,proc_attr,charges,offensive,interruptible)` | skill ativa | `offensive=False` → não inicia combate/perseguição; `mana_cost_pct` para custo percentual (Polimorfia); criada por `_make_skill(id, SKILL_CATALOG)` |
| `PlayerSkills(skills[10], keybinds[], learned_skill_ids, gcd_timer)` | hotbar e skills aprendidas | `_CHARGE_BASED` define skills de carga |

---

## Componentes de Magia

| Componente | Campos | Notas |
|-----------|--------|-------|
| `SpellCast(spell_id, cast_time, elapsed, target_id, mana_cost, interruptible)` | cast em andamento | `interruptible=False` → Calcinar pode ser castado em movimento; mana deduzida só ao completar |
| `Channeling(spell_id, duration, elapsed, tick_interval, mana_per_tick, target_x/y, radius_tiles, slow_pct, dmg_weapon_pct, dmg_sp_coeff)` | canalização ativa | apenas Calamidade Flamejante por enquanto |
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
| `Item(name, item_type, slot, modifiers[], rarity, value, damage_min/max, attack_speed, proc, consumable, armor_class)` | item de jogo | `armor_class`: "placa"/"couro"/"tecido"/"" — restrição por classe via `CLASS_ARMOR_ALLOWED` em `stats_system.py` |
| `Modifier(attribute, value, type)` | modificador de stat | type: "flat" ou "percentage" |
| `Wallet(gold)` | ouro do jogador | |
| `ConsumableBar(slots[], keybinds[], global_cooldown)` | barra de consumíveis | |
| `LearnedRecipes(known[])` | receitas aprendidas | modificado via `stat_fns.learn_recipe()` |

---

## Componentes de Status e IA

| Componente | Campos | Notas |
|-----------|--------|-------|
| `StatusEffects{effects{type→ActiveEffect}}` | efeitos ativos | `has()`, `get()`, `remove()` |
| `ActiveEffect(effect_type, duration, magnitude, tick_interval, _tick_elapsed)` | efeito singular | magnitude = dano/cura por tick |
| `ActiveRegen(heal_per_tick, interval, ticks_total, ticks_remaining, tick_timer)` | regen ativa (consumíveis) | ConsumableSystem |
| `AIControlled(state, path, attack_range_tiles, is_ranged, entity_class, disengage_cd, kite_*, ranged_cast_timer, aggroed_by_damage)` | IA do mob | state: IDLE/CHASING/RETURNING/ATTACKING |
| `EnemyAbilities(slots[EnemyAbilitySlot])` | habilidades especiais | EnemyAbilitySystem |
| `Projectile(attacker_id, target_id, damage_type, speed)` | projétil inimigo | |

---

## Componentes de Mundo

| Componente | Campos | Notas |
|-----------|--------|-------|
| `Tilemap(tile_matrix, terrain_matrix, object_matrix, terrain_visual, map_width_tiles, map_height_tiles, tile_size)` | dados do mapa carregado | |
| `FogOfWar(radius, explore_radius, visible, explored, _explored_maps, _last_tile)` | estado de neblina | `visible` = set de tiles visíveis no frame |
| `Visible()` | tag adicionada/removida por FogSystem | entidades sem Visible são ignoradas |

---

## Componentes de NPC e Quests

| Componente | Campos | Notas |
|-----------|--------|-------|
| `NPC(name, level, profession)` | dados de NPC genérico | base para Merchant, QuestGiver, Trainer, Blacksmith |
| `Merchant(shop_id)` | referência ao shop em `merchant_data.SHOPS` | |
| `QuestGiver(quest_ids[], turn_in_ids[])` | quests disponíveis | |
| `Trainer(class_id)` | classe de habilidades ensinadas | |
| `Blacksmith(shop_id)` | ferreiro com crafting | |
| `QuestLog(active{qid→progresso[]}, completed{qid})` | estado de quests do jogador | |
| `SpawnZone(...)` | zona de respawn | gerenciada por SpawnZoneSystem |
| `SpawnZoneOwner(zone_entity_id)` | liga mob à sua zona | |
| `Corpse(loot[], coins, timer, looted, is_open)` | cadáver com drop | |
| `MobSounds(aggro, death, attack_*, crit, emote_*)` | chaves de som do mob | |
| `EntityIdentity(name, race, entity_class, level, tier)` | identidade completa do mob | lido por DeathHandlerSystem e QuestSystem |

---

## Componentes de UI (state em component)

| Componente | Campos | Notas |
|-----------|--------|-------|
| `UIState(show_inventory, show_talents)` | visibilidade de painéis | player entity; propriedades em GameEngine roteiam aqui |
| `ShopUIState(open_merchant_id)` | qual loja está aberta | `is_open` property |
| `LootUIState(open_corpse_id)` | qual cadáver está aberto | |

---

## Talentos

| Componente | Campos | Notas |
|-----------|--------|-------|
| `TalentTree(chosen_build, allocated{}, available_points, _applied_modifiers[], _unlocked_skill_ids[])` | árvore de talentos | `chosen_build` derivado de `class_id` via `CLASS_BUILD_MAP` |
