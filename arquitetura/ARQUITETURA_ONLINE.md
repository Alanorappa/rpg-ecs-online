# Arquitetura Online — Decisões e Referência

> Documento vivo. Atualizar sempre que uma decisão arquitetural for tomada.
> Última atualização: 2026-06-15 (gate centralizado de dano `_apply_final_damage`; imunidade Bloco de Gelo ranged; `_target_alive` para PvP)

---

## Visão geral

RPG Tibia/WoW-style com servidor autoritativo em Python.
Clientes Pygame se conectam via WebSocket e recebem estado do mundo por ticks.

```
[Cliente Pygame] ──WebSocket──► [Servidor Python / asyncio]
                 ◄──────────────      ECS headless (SDL_VIDEODRIVER=dummy)
                                      SQLite (dev) → PostgreSQL (prod)
                                      20 ticks/s
```

**Separação inviolável:**
- `server/` nunca importa Pygame para display/input — apenas SDL dummy para sistemas herdados
- `client/` nunca calcula gameplay — apenas renderiza estado recebido
- `shared/` sem estado — constantes e funções puras de serialização

---

## Decisões arquiteturais

### 1. Modelo de mundo — Híbrido
- Open world compartilhado, MVP monolítico
- Instâncias para dungeons/raids — `zone_manager.py` (pendente)
- PvP apenas em zonas contestadas (flag por zona no mapa)

### 2. Protocolo de rede
- **Transporte:** WebSocket (TCP)
- **Formato:** JSON (dev) → MessagePack antes do lançamento
- **Pacote:** `{type: MsgType, p: dict, seq: int, ts: int_ms}`
- **Versão:** `PROTOCOL_VERSION` em `shared/constants.py`
  - Servidor rejeita versão incompatível com `LOGIN_ERROR: version_mismatch`
  - Implementado: `_handle_login` compara `payload["version"]` com `PROTOCOL_VERSION`

### 3. Tick rate — 20 ticks/s (50ms)
- Movimento, combate, skills: 20 ticks/s
- Cliente roda a 60 fps com interpolação entre posições (visual fluido)
- Client-side prediction para movimento próprio (não implementado)

### 4. Servidor autoritativo
O servidor é a única fonte de verdade para:
- Dano (`damage_calculator.py`)
- Resultado de hit/miss/crit/dodge
- Posição final após knockback
- Drops de loot
- Estado de invisibilidade de outros jogadores

**Exceção controlada:** Rage e mana são gerados localmente pelo cliente (igual ao offline) e sincronizados no `CAST_SKILL` via `rage`/`mana` fields. O servidor os aplica via `sync_player_resources()` antes de processar a skill.

**Gate de dano — `_apply_final_damage` (`server/spell_completion_processor.py`):**

Todo dano de skill no servidor passa por `_apply_final_damage(target_id, dmg) -> bool`. É o **único ponto** onde `CombatStats.current_hp` é decrementado — verifica `current_hp > 0` e `CombatState.is_immune` antes de aplicar. Para adicionar redução de dano, resistências ou novos estados de imunidade: editar **apenas aqui**, nunca nas funções de skill individuais. Auto-attack (via `deal_damage()` em `systems.py`) tem guarda própria já existente e não passa por este método.

### 5. Area of Interest (AOI)
- Raio: **15 tiles** (`AOI_RADIUS`) — igual ao `FOG_RADIUS` do cliente
- `Session.known_eids` rastreia o que cada cliente conhece
- `_build_update_for_session` calcula entradas/saídas de AOI por subscription
- AOI_UPDATE é o pacote mais frequente — contém apenas deltas
- Sweep periódico em `_build_update_for_session` detecta mobs estacionários entrando em range

### 6. Persistência
| Fase | Banco | Motivo |
|------|-------|--------|
| Desenvolvimento | SQLite (`data/game.db`) | Zero config, portátil |
| Produção | PostgreSQL | Concorrência real, backup |

Autosave a cada 5 minutos (tick_count % 6000 == 0 em `_on_tick`).

**Regra: toda ação que muda estado persistente deve disparar save imediato.**
Autosave de 5 min cobre drift geral, mas qualquer ação que altere bag/equipment/
gold/talentos/hotbar (loot, compra/venda, Recarregar, alocação de talento, etc.)
deve chamar `_send_save_state()` (e, se o servidor mantém cópia em memória do
Inventory para validação — ex: Recarregar —, também `_on_loot_action("item")`/
`INV_SYNC`) no momento da ação, não esperar o autosave. Caso contrário a ação
some se o jogador deslogar antes do próximo autosave/evento de save (ex: C9 —
Recarregar não persistia). Ver hooks existentes: `_on_loot_action`,
`_send_talent_update`, `_send_hotbar_update`, `_on_recarregar_changed`
(`client/save_sync_handlers.py`).

### 7. Autenticação
- Dev: username + SHA-256(password) → SQLite
- Session token UUID gerado no LOGIN_OK
- Contas de teste: `teste/123456` (Guerreiro), `teste2/123456` (Mago)
- `asyncio.get_running_loop()` usado no `auth.py` (fix de thread safety)

### 8. Sistema de save — `_build_save_merge`

Autoridade por campo no merge entre estado do servidor e payload do cliente:

| Campo | Autoridade | Razão |
|-------|-----------|-------|
| `tile_x`, `tile_y` | Servidor | Anti-teleporte |
| `hp` | Servidor (cap em max_hp) | Anti-cheat |
| `mp` | Servidor | Anti-cheat |
| `max_hp` | Cliente se > 0 | Inclui bônus de equipamento |
| `gold` | Cliente | Gerado localmente via lojas/loot |
| `inventory`, `equipment`, `talents` | Cliente | Gerenciados localmente |
| `skills` | Cliente (fallback servidor) | Hotbar local |
| `level`, `xp`, `atributos base` | Servidor | Cálculo autoritativo |

Save é disparado no disconnect (`on_disconnect`) e no `SAVE_STATE` do cliente. Ambos usam `_build_save_merge`.

### 9. Sistema de stats — PLAYER_STAT_SYNC

O cliente envia `PLAYER_STAT_SYNC` com stats efetivos (inclui bônus de equipamento).
O servidor aplica via `sync_player_combat_stats()` → `_apply_stat_overrides()`.

Stats sincronizados (definidos em `shared/constants.py → COMBAT_SYNC_STATS`):

| Chave do cliente | Campo no CombatStats |
|-----------------|---------------------|
| `max_hp` | `base_stamina` |
| `attack_power` | `base_attack_power` |
| `armor` | `base_armor` |
| `crit_rating` | `base_crit_rating` |
| `parry_rating` | `base_parry_rating` |
| `dodge_rating` | `base_dodge_rating` |
| `attack_interval` | `base_attack_interval` |

`_apply_stat_overrides` é chamada após qualquer operação que recalcule CombatStats (spawn, level-up, talents). Para adicionar nova stat: inserir entrada em `COMBAT_SYNC_STATS`.

### 10. Sistema de sons — spatial audio

`SoundManager` (`sound_manager.py`) suporta reprodução posicional:

- `volume_at(sx, sy, lx, ly, base)` — calcula volume por distância em pixels
  - 100% em distância 0, 5% em 10 tiles (320px), silêncio além
- `play_mob_sounds_at(comp, event, sx, sy, lx, ly)` — usa `MobSounds` componente com atenuação
- `play_skill_at`, `play_random_at` — versões posicionais de skills e SFX genéricos

No servidor: eventos de som posicionais são emitidos via `_pending_sound_events` (lista de dicts `{kind, mob_eid, mob_name, tx, ty}`). O `SessionManager._dispatch_tick_deltas` consome via `consume_sound_events()` e envia `SOUND_EVENT` para clientes no AOI. O cliente, ao receber, busca o `MobSounds` do mob local e chama `play_mob_sounds_at`.

### 11. Sistema de skills — pipeline online

**Cliente:**
1. Usuário pressiona tecla → `SkillSystem._use_skill_visual_only()`
2. Verifica GCD local (`PlayerSkills.gcd_timer`, `GCD_DURATION = 0.8s`)
3. Se skill pronta: seta `skill._server_pending = True`, `_server_pending_timeout = 0.40s`
4. Envia `CAST_SKILL {sid, tid, dir_x, dir_y, rage, mana}` ao servidor
5. Aguarda `SKILL_RESULT`: se chegar → confirma, zera `_server_pending`, aplica GCD completo
6. Se timeout sem resposta → libera com meio-GCD (0.4s) como fallback de segurança
7. `fail_flash_timer = 0.2s` — slot escurece se range inválido (verificado localmente)

**Servidor (`_process_skill_requests`):**
1. Recebe request da fila `_pending_skill_requests`
2. Busca `Skill` no `PlayerSkills` local (com estado de cargas) ou cria do `SKILL_CATALOG`
3. Lag compensation: snapa `current_tile` → `target_tile` para player e mob (se progress ≥ 0.5)
4. Injeta `_server_dir_x/_server_dir_y` no `TileMovement` para skills direcionais
5. Chama handler `_skill_{sid}()` do `SkillSystem` instanciado no servidor
6. Restaura tiles snapeados após o handler
7. Coleta HP diff dos mobs para montar `targets` do `SKILL_RESULT`
8. Se skill moveu player (Interceptar): adiciona à `_skill_position_corrections` → `ENTITY_MOVE` direto ao caster
9. Sincroniza rage/mana/HP do player em `_pending_xp_deliveries` (consumido como `STATS_UPDATE`)

**Range check — pixel-based:**
- `MELEE_RANGE_PX = 72.0px` (2.25 tiles) — cobre adjacência diagonal (45px) + kiting lag
- `_range_ok(player_pos, target_pos, max_px)` — hitbox circular em pixels
- `_melee_ok` é alias para `_range_ok(pos, pos, MELEE_RANGE_PX)`
- Skills usam `Position.x/y` (interpolada) — mais preciso que tile check

**is_ability miss bypass:**
- `damage_calculator.resolve_attack_outcome(is_ability=True)` — zera miss_chance
- Skills do jogador passam `is_ability=True`; auto-attacks usam `False`

### 12. CombatLog e feedback visual online

**CombatLog** (`combat_log.py`) — global `LOG`, 8 msgs, 8s duração, 2s fade.

Fontes de LOG no modo online:

| Evento | Mensagem | Cor | Origem |
|--------|----------|-----|--------|
| Player causou dano (auto-attack/skill) | "Você causou X de dano [crítico]." | branco/amarelo | `_apply_combat_result` |
| Player recebeu dano | "Você recebeu X de dano [crítico]." | vermelho | `_apply_combat_result` |
| Player ganhou status effect | "Você recebeu: {label}!" | laranja | `_sync_player_effects` |
| Status effect expirou | "{label} expirou." | cinza | `_sync_player_effects` |
| Mob recebeu CC/debuff | "{mob_name}: {label}!" | amarelo-laranja | `_sync_mob_effects` |
| Skill aplicou efeito em mob | "{mob_name} recebeu: {label}!" | laranja-dourado | SKILL_RESULT handler |

**Status effect icons em mobs:**
- Servidor coleta `StatusEffects` de todos os mobs em `_collect_mob_effects()` → `mob_effects: {str_eid: [{type, duration}]}`
- `session._build_update_for_session` filtra por `known_eids` (AOI)
- `game._sync_mob_effects` popula `StatusEffects` no ECS local do mob
- `RenderSystem` já renderiza ícones (spritesheet animado stun/sleep + rect colorido para outros) acima da barra de HP

**DoT sound suppression:**
- `_is_dot_hot = source not in ("auto", "skill")` — bloqueia sons em ticks de bleed/burn/poison
- `_sfx_damage_players` no servidor acumula dano de DoT por player no tick, subtraído do delta HP na detecção mob→player para evitar `COMBAT_RESULT` falso

### 13. Sistema de spawn — SpawnZoneSystem headless

No servidor, `SpawnZoneSystem` é instanciado com `ACTIVATION_RADIUS = 999999`.
Isso desativa o culling por distância de player — todos os spawns são processados.

**Fix `_pending_spawns`:** evita spawn de múltiplos mobs no mesmo tick ao zerar capacidade.
O contador `zone._pending_spawns` (atributo dinâmico adicionado em runtime) é incrementado
a cada spawn tentado e decrementado no próximo ciclo. Sem isso, uma zona poderia spawnar
`max_count` mobs num único tick após respawn.

---

## Protocolo — todas as mensagens implementadas

| Direção | Tipo | Quando | Implementado |
|---------|------|--------|-------------|
| C→S | `LOGIN` | Conectou, envia credenciais + version | ✅ |
| S→C | `LOGIN_OK` | Autenticado — inclui `eid`, `char`, `hp`, `hp_max`, `server_ts` | ✅ |
| S→C | `LOGIN_ERROR` | Credenciais inválidas / already_online / version_mismatch | ✅ |
| C→S | `LOGOUT` | Gracioso (não implementado no cliente — usa disconnect) | 🔲 |
| S→C | `WORLD_STATE` | Snapshot inicial: tick, tx/ty, entities no AOI | ✅ |
| S→C | `AOI_UPDATE` | Delta por tick: spawned, despawned, moved, combat, stats, effects, mob_effects | ✅ |
| C→S | `MOVE` | Mover 1 tile | ✅ |
| S→C | `ENTITY_MOVE` | Broadcast de movimento + correção de posição | ✅ |
| C→S | `AUTO_ATTACK` | Setar/parar alvo de auto-attack | ✅ |
| S→C | `COMBAT_RESULT` | Hit: attacker, target, outcome, damage, hp_after, source | ✅ |
| C→S | `CAST_SKILL` | sid, tid, dir_x/y, rage, mana | ✅ |
| S→C | `SKILL_RESULT` | caster_eid, sid, targets[{eid, damage, outcome, hp_after, applied_effects}] | ✅ |
| S→C | `STATS_UPDATE` | eid, hp, hp_max, xp_gained, rage, mana, heal_amount, heal_sid | ✅ |
| S→C | `ENTITY_SPAWN` | eid, kind, tx, ty, name, class_id, hp, hp_max, level, effects | ✅ |
| S→C | `ENTITY_DESPAWN` | eid (negativo para corpse) | ✅ |
| S→C | `LOOT_AVAILABLE` | corpse_id, tx, ty, items, coins — só ao dono | ✅ |
| C→S | `LOOT_REQUEST` | corpse_id | ✅ |
| S→C | `LOOT_RESULT` | corpse_id, items, coins | ✅ |
| S→C | `PLAYER_DEATH` | eid, corpse_tx, corpse_ty — corpo fica no local da morte | ✅ |
| C→S | `RELEASE_SPIRIT` | player clicou "Liberar espírito" — vira ghost no cemitério | ✅ |
| C→S | `REVIVE_REQUEST` | ghost perto do corpo clicou "Sim" — revive com 15% HP no corpo | ✅ |
| S→C | `PLAYER_REVIVE` | tx, ty, hp, hp_max, mana, max_mana — revive (cemitério ou corpo) | ✅ |
| S→C | `GHOST_STATE` | is_ghost, near_corpse, graveyard_timer — sync do estado do espírito | ✅ |
| C→S | `PLAYER_STAT_SYNC` | max_hp, attack_power, armor, crit, parry, dodge, attack_interval | ✅ |
| C→S | `SAVE_STATE` | inventory, equipment, talents, skills, stats{gold, max_hp} | ✅ |
| C→S | `PING` / S→C `PONG` | client_ts / {client_ts, server_ts} | ✅ |
| C→S | `CHAT_SEND` / S→C `CHAT_MESSAGE` | text, channel, color | ✅ |
| S→C | `SOUND_EVENT` | kind, mob_eid, mob_name, tx, ty — aggro posicional | ✅ |
| S→C | `CAST_START` / `CAST_CANCEL` / `CAST_COMPLETE` | barra de cast visível | 🔲 |
| S→C | `PROJECTILE_SPAWN` / `PROJECTILE_HIT` | projéteis | 🔲 |
| S→C | `EFFECT_APPLIED` / `EFFECT_REMOVED` | status effects | 🔲 |
| S→C | `ENTITY_DEATH` | morte de player com animação/corpo no AOI — implementado p/ players (G3 mobs ainda 🔲) | ✅ |
| S→C | `ZONE_CHANGE` / C→S `ENTER_INSTANCE` | instâncias | 🔲 |

---

## Fluxo de tick — `WorldServer._tick(dt)` — ordem exata

```
1. Decrementa respawn_immunity_ticks dos players (imunidade pós-morte)
   └── Restaura is_visible=True ao expirar

2. Snapshot pré-sistemas
   ├── pre_mob_pos: {eid: (tx, ty)} — posições dos mobs antes
   ├── player_hp_snap: {eid: current_hp} — HP dos players antes
   └── mob_states_prev: {eid: state} — estados AI antes (para detectar aggro)

3. self._systems.update(dt) — ordem:
   ├── TileValidationSystem — rebuild cache de tiles ocupados
   ├── SpawnZoneSystem — spawna mobs (ACTIVATION_RADIUS=999999)
   ├── EnemyAISystem — pathfinding, aggro, deal_damage mob→player
   ├── EnemyAbilitySystem — habilidades especiais de mobs
   └── TileMovementSystem — avança progress→current_tile (headless)

4. Detecta transições IDLE→AGGRO_DELAY
   └── Emite sound_event "mob_aggro" para broadcast AOI

5. CombatStateSystem inline (para cada player):
   ├── Decrementa combat_timer; desativa in_combat se expirou
   ├── Rage decay (−5 a cada 3s fora de combate)
   └── HP5 regen (5% max_hp a cada 5s fora de combate)
       └── Emite combat_result {outcome="regen"} para cliente mostrar "+N HP"

6. _process_skill_requests() — ANTES do auto-attack
   ├── Lag compensation: snapa tiles para range check
   ├── Chama handler _skill_{sid}() do SkillSystem
   ├── Skill com cast_time que enfileirou _pending_spell_completions →
   │   seta combat_state.is_casting = True (espelha offline systems.py:5877)
   ├── Emite skill_results_this_tick
   └── Emite skill_position_corrections (Interceptar)

6.1. _process_spell_cast_completions(dt) — resolve casts pendentes
    └── Quando o último pending de um player resolve (ou é cancelado em
        _handle_cancel_cast) → combat_state.is_casting = False, liberando
        can_act() para o auto-attack genérico

7. _process_player_attacks(dt, player_hp_snap)
   ├── Player→Mob: deal_damage() offline + cooldown de ataque
   │   └── Se morreu: PendingDeath (death_handler processa adiante)
   └── Mob→Player: detectado via HP diff vs snapshot
       └── Se morreu: _handle_player_death()

8. Sweep: mobs com HP ≤ 0 sem PendingDeath → adiciona PendingDeath(-1)

9. _death_handler.update() — processa PendingDeath de mobs:
   ├── XP proporcional por damage_log → pending_xp_deliveries
   ├── Vitória Iminente: carga para o killer
   ├── Loot roll → pending_loot_notifications
   ├── SpawnZone: remove active_entity_ids + agenda respawn_timer
   └── remove_entity() + pending_despawns

10. Aplica XP no ECS do servidor (process_levelups)
    └── Se level-up: _apply_stat_overrides + notifica cliente via pending_xp_deliveries

11. Registra corpses em _corpses{}; emite pending_loot_notifications

12. Decay de corpses (timer -= dt); emite expired_corpses_this_tick

13. Detecta novos mobs (Enemy+TileMovement não em _mob_eids)
    └── Adiciona CombatState + Visible; emite mob spawn

14. Detecta mobs movidos (pre_mob_pos diff) → _moved_this_tick

15. _collect_deltas() — consolida e limpa todos os buffers

16. _store_snapshot() — guarda (tick_count, {eid: (tx, ty)}) para lag comp

17. callbacks _on_tick → SessionManager._on_tick → _dispatch_tick_deltas
```

---

## Fluxo de morte/espírito (ghost) + cemitério (C30)

Substitui o respawn instantâneo (C28/C29). Vale para QUALQUER morte (PvE e PvP).

```
1. Player morre (current_hp==0) → _handle_player_death (respawn_system.py):
   - Limpa efeitos/channeling/spells em voo/aggro de mobs (igual antes)
   - GhostState.is_dead=True, corpse_tx/ty = posição da morte
   - CombatState.is_visible=True (corpo FICA visível, jaz no local)
   - PLAYER_DEATH {eid, corpse_tx, corpse_ty} → dono
   - ENTITY_DEATH {eid, tx, ty} → broadcast AOI

2. Cliente (dono): timer local de 2s → modal "Você morreu" com botão
   "Liberar espírito" (client/death_ui_handlers.py)

3. RELEASE_SPIRIT (C→S) → _handle_release_spirit:
   - GhostState.is_ghost=True
   - Teleporta player-entity pro RESPAWN_TILE=(115,389) (cemitério),
     "_moved_this_tick" com teleport=True
   - CombatState.is_visible=False (ghost invisível p/ mobs e outros players,
     reaproveita regra de Camuflagem)
   - Marcador de corpo sintético (eid 3_000_000+player_eid, kind="player_corpse")
     spawna no local da morte p/ quem está no AOI

4. Ghost é INTANGÍVEL: move_player bypassa CC/walkable (só valida 1 tile de
   distância); cliente também bypassa is_tile_walkable quando is_ghost
   (PlayerInputSystem)

5. _tick_ghost_states(dt) — a cada tick:
   - Dentro do raio do cemitério (GHOST_GRAVEYARD_RADIUS_TILES=5 tiles) por
     GHOST_GRAVEYARD_REVIVE_S=45s contínuos → revive automático, full HP
     (_revive_player(hp_frac=1.0, at_corpse=False)). Sair do raio reseta o timer.
   - Dentro do raio do corpo (GHOST_CORPSE_RADIUS_TILES=3 tiles) →
     near_corpse=True → GHOST_STATE → cliente mostra prompt "Reviver agora?"

6. REVIVE_REQUEST (C→S, só se near_corpse) → _handle_revive_request revalida
   distância e chama _revive_player(hp_frac=GHOST_CORPSE_REVIVE_HP_FRAC=0.15,
   at_corpse=True) — revive no local do corpo com 15% HP

7. _revive_player: restaura HP/mana, reset GhostState, respawn_immunity_ticks=80
   (4s), despawna marcador de corpo, PLAYER_REVIVE {tx,ty,hp,hp_max,mana,max_mana}
   → dono
```

Render: corpo (`is_dead and not is_ghost`) desenhado dessaturado (cinza, sem
barra de HP); ghost (`is_ghost`) desenhado semi-transparente (alpha ~120/255).

---

## Estado de implementação

| Componente | Status | Arquivo |
|------------|--------|---------|
| Protocolo de mensagens | ✅ completo | `shared/messages.py` |
| Constantes + COMBAT_SYNC_STATS | ✅ completo | `shared/constants.py` |
| Loop de ticks ECS headless | ✅ funcional | `server/world_server.py` |
| EnemyAISystem real no servidor | ✅ completo | `server/world_server._load_map` |
| SpawnZoneSystem headless (ACTIVATION_RADIUS=999999) | ✅ completo | `server/world_server._load_map` |
| CombatStateSystem inline (rage decay, HP5) | ✅ completo | `server/world_server._tick` |
| Auto-attack player→mob + mob→player | ✅ completo | `server/world_server._process_player_attacks` |
| Skills no servidor (CAST_SKILL → SKILL_RESULT) | ✅ completo | `server/world_server._process_skill_requests` |
| Lag compensation pixel-based para skills | ✅ completo | `server/world_server._process_skill_requests` |
| Morte de mobs (XP proporcional, loot, SpawnZone) | ✅ completo | `server/server_death_handler.py` |
| Corpse + loot (first-attacker, timer 120s/15s) | ✅ completo | `server/world_server` + `session.py` |
| Vitória Iminente: carga ao matar mob | ✅ completo | `server/server_death_handler.py` |
| Morte/respawn de player (fluxo ghost/cemitério, C30) | ✅ completo | `server/respawn_system.py` |
| HP max correto no login/spawn | ✅ completo | `server/world_server.spawn_player` |
| PLAYER_STAT_SYNC + _apply_stat_overrides | ✅ completo | `server/world_server.sync_player_combat_stats` |
| AOI subscription (known_eids) | ✅ completo | `server/session.py` |
| Save merge com autoridade por campo | ✅ completo | `server/session._build_save_merge` |
| Autosave a cada 5 min | ✅ completo | `server/session._autosave_all` |
| Talent effects no servidor (cs_flags + modifiers) | ✅ completo | `server/world_server.apply_talent_effects_to_player` |
| Sound events posicionais (mob aggro) | ✅ completo | `server/world_server._tick` + `session._dispatch_tick_deltas` |
| StatusEffectSystem no servidor (DoT, burn, stun, slow, root) | ✅ completo | `server/world_server._load_map` + `core_systems.StatusEffectSystem` |
| Effects broadcast via AOI_UPDATE (effects key) | ✅ completo | `server/world_server._collect_player_effects` + `session._build_update_for_session` |
| HUD de status effects no cliente | ✅ completo | `game.py._draw_hud` |
| Mob effects broadcast (mob_effects em AOI_UPDATE) | ✅ completo | `server/world_server._collect_mob_effects` + `session._build_update_for_session` |
| Status effect icons acima da barra de HP de mobs online | ✅ completo | `game.py._sync_mob_effects` → `StatusEffects` no ECS local |
| applied_effects em SKILL_RESULT (procs, CC) | ✅ completo | `server/world_server._process_skill_requests` |
| CombatLog online (dano causado/recebido, procs, CC) | ✅ completo | `game.py._apply_combat_result`, `_sync_player_effects`, `_sync_mob_effects`, SKILL_RESULT handler |
| DoT sound suppression (_is_dot_hot flag) | ✅ completo | `game.py._apply_combat_result` |
| _sfx_damage_players: tracking DoT→player para evitar COMBAT_RESULT falso | ✅ completo | `server/world_server._process_player_attacks` |
| Despawn AOI-aware com posição de morte | ✅ completo | `server/world_server._despawned_this_tick` + `session.despawned_pos` |
| ProjectileSystem no servidor | ✅ completo | `server/world_server._load_map` (`_proj_sys`) |
| Drag de item consumível p/ barra de consumíveis | ✅ completo | `game.py._draw_consumable_bar` + `_draw_inventory_panel` |
| Overlay out-of-stock na barra de consumíveis | ✅ completo | `game.py._draw_consumable_bar` |
| Talent-lock overlay na hotbar (roxo + ✕) | ✅ completo | `game.py._draw_hotbar` + `_is_talent_locked` |
| "Requer talento: [nome]" no tooltip da hotbar | ✅ completo | `game.py._draw_hotbar` (injeta linha no tooltip) |
| Shift+drag fora da barra → remove skill do slot | ✅ completo | `game.py._draw_hotbar` (drag shift) |
| Drag de slot para slot → reordena hotbar | ✅ completo | `game.py._draw_hotbar` (swap) |
| Bloqueio de skill talent-locked via teclado | ✅ completo | `systems.py._use_skill` + `_use_skill_visual_only` |
| _TALENT_SKILL_REQS lookup reverso (skill→talent) | ✅ completo | `game.py` + `systems.py` (module-level) |
| Refatoração `_handle_net_message` → mixin `NetworkHandlers` (game.py God Object, item 2.5 do PLANO_ACAO) | ✅ completo | `client/network_handlers.py` (`GameEngine(NetworkHandlers)`); extração mecânica verificada byte-idêntica, mesmo padrão de `SkillHandlers`/`SkillSystem` |
| Refatoração gestão de entidades remotas → mixin `RemoteEntityHandlers` (item 2 do PLANO_ACAO) | ✅ completo | `client/remote_entity_handlers.py` (`GameEngine(NetworkHandlers, RemoteEntityHandlers)`); 17 métodos (~896 linhas: spawn/despawn/move/sync de mobs e players remotos + `_apply_combat_result`), extração mecânica verificada byte-idêntica |
| Refatoração save/load + sync → mixin `SaveSyncHandlers` (item 2 do PLANO_ACAO, etapa 4.2) | ✅ completo | `client/save_sync_handlers.py` (`GameEngine(NetworkHandlers, RemoteEntityHandlers, SaveSyncHandlers)`); 15 métodos (~430 linhas: serialização de itens, save/load de personagem, envio de sync de talentos/hotbar/stats/loot/engage), extração mecânica verificada byte-idêntica |
| Refatoração painel de inventário → mixin `InventoryHandlers` (item 2 do PLANO_ACAO, etapa 4.3) | ✅ completo | `client/inventory_handlers.py` (`GameEngine(NetworkHandlers, RemoteEntityHandlers, SaveSyncHandlers, InventoryHandlers)`); 5 métodos (~447 linhas: `_equip_item`, `_unequip_slot`, `_handle_inventory_click`, `_use_consumable`, `_draw_inventory_panel`), extração mecânica verificada byte-idêntica; `_panel_origin` ficou em `GameEngine` (depende do global mutável `SCREEN_WIDTH`/`SCREEN_HEIGHT` — ver `PROBLEMAS_ARQUITETURA.md` §9, **resolvido logo em seguida nesta mesma etapa**) |
| Eliminação dos globais mutáveis `SCREEN_WIDTH`/`SCREEN_HEIGHT` → `self.screen.get_width()/get_height()` (pré-requisito da etapa 4, ver `PROBLEMAS_ARQUITETURA.md` §9) | ✅ completo | 55 ocorrências substituídas em `game.py` (29 `SCREEN_WIDTH`, 26 `SCREEN_HEIGHT`); módulo-globais e respectivas declarações `global` removidos por completo. `self.screen` (uma `pygame.Surface` recriada em `_apply_scale`) passa a ser a única fonte de verdade — mesmo padrão já usado em `game.py:2875/3363`. Desbloqueia extração de qualquer mixin de desenho/HUD restante |
| Refatoração tooltips de skills/itens/mundo → mixin `TooltipHandlers` (item 2 do PLANO_ACAO, etapa 4.4) | ✅ completo | `client/tooltip_handlers.py` (`GameEngine(NetworkHandlers, RemoteEntityHandlers, SaveSyncHandlers, InventoryHandlers, TooltipHandlers)`); 7 métodos (~398 linhas: `_player_spell_dmg`, `_player_dmg_range`, `_skill_tooltip_lines`, `_draw_tooltip`, `_flush_tooltip`, `_flush_skill_tooltip`, `_draw_world_tooltip`), extração mecânica verificada byte-idêntica. Cores do HUD (`C_WHITE`/`C_YELLOW`/`C_GREEN`/`C_RED`/`C_GRAY`/`C_CYAN`/`C_ORANGE`) movidas para `client/colors.py` — fonte única compartilhada por `game.py` e mixins, evitando duplicação/rewrite e desbloqueando extrações futuras que também referenciam essas cores (HUD, hotbar, debug modal) |
| Refatoração modal de debug (F12) → mixin `DebugHandlers` (item 2 do PLANO_ACAO, etapa 4.5) | ✅ completo | `client/debug_handlers.py` (`GameEngine(..., TooltipHandlers, DebugHandlers)`); 11 métodos (~345 linhas: level up, ouro, troca de mapa, catálogo de itens, clique e desenho do modal + abas Nivel/Itens/Ouro/Mapa — `_debug_levelup`, `_handle_debug_click`, `_debug_add_gold`, `_debug_add_item`, `_debug_open_map`, `_get_debug_item_catalog`, `_draw_debug_modal`, `_draw_debug_tab_nivel`, `_draw_debug_tab_ouro`, `_draw_debug_tab_mapa`, `_draw_debug_tab_itens`), extração mecânica verificada byte-idêntica. `_DEBUG_MAP_NAMES` (dict de nomes amigáveis de mapas, usado só pela aba Mapa) foi relocado para dentro do mixin — não duplicado nem deixado órfão, já que essa era sua única referência fora da própria definição em `game.py` |
| Refatoração menu de pausa (ESC) + submenus → mixin `MenuHandlers` (item 2 do PLANO_ACAO, etapa 4.6) | ✅ completo | `client/menu_handlers.py` (`GameEngine(..., DebugHandlers, MenuHandlers)`); 9 métodos (~307 linhas: helpers de estilo + telas — `_mm_overlay`, `_mm_panel`, `_mm_button`, `_draw_pause_menu`, `_draw_quit_confirm`, `_draw_main_menu`, `_draw_resolution_submenu`, `_draw_interface_submenu`, `_draw_sound_submenu`) + 7 constantes de cor `_MM_*`, extraídos como bloco contíguo único (divisor + constantes + métodos, via slice literal de linhas — não método-a-método, pois há atributos de classe intercalados), verificado byte-idêntico |
| Refatoração editor de hotbar (tecla K) → mixin `HotbarEditorHandlers` (item 2 do PLANO_ACAO, etapa 4.7) | ✅ completo | `client/hotbar_editor_handlers.py` (`GameEngine(..., MenuHandlers, HotbarEditorHandlers)`); 2 métodos (~181 linhas: tabela de rebind de teclas — menus, slots de habilidade e consumíveis — com captura de tecla e botões Salvar/Fechar — `_close_hotbar_editor`, `_draw_hotbar_editor`) + 2 constantes de geometria `_HBE_*`, extraídos como bloco contíguo único (mesmo padrão de `MenuHandlers`: divisor + constantes + métodos via slice literal de linhas), verificado byte-idêntico |
| Refatoração painel de Habilidades (tecla H) + drag ghost + tela de loading → mixin `HabilidadesHandlers` (item 2 do PLANO_ACAO, etapa 4.8) | ✅ completo | `client/habilidades_handlers.py` (`GameEngine(..., HotbarEditorHandlers, HabilidadesHandlers)`); 3 métodos (~249 linhas: lista scrollável de skills aprendidas com drag-and-drop para a hotbar, ghost de drag exibido sobre os slots, e tela de loading exibida enquanto aguarda LOGIN_OK do servidor — `_draw_habilidades_panel`, `_draw_loading_screen`, `_draw_hab_drag_ghost`), extração método-a-método via AST (sem constantes de classe intercaladas — mesmo padrão de `TooltipHandlers`/`DebugHandlers`), verificada byte-idêntica. `_draw_loading_screen` está fisicamente entre os outros dois métodos no divisor "Painel de Habilidades (H)" apesar de tematicamente pertencer ao fluxo de conexão — manteve-se a ordem original (extração mecânica, sem reordenação) |
| Refatoração ciclo de vida da conexão online → mixin `OnlineModeHandlers` (item 2 do PLANO_ACAO, etapa 4.9) | ✅ completo | `client/online_mode_handlers.py` (`GameEngine(..., HabilidadesHandlers, OnlineModeHandlers)`); 6 métodos (~136 linhas: abre/mantém conexão com o servidor, processa login, drena mensagens recebidas (`_process_network`), resolve dano diferido de Bola de Fogo (`_process_bdf_pending`), envia movimento do jogador e desenha o HUD minimalista de status de conexão — `_connect_online`, `_do_login`, `_process_network`, `_process_bdf_pending`, `_send_player_move`, `_draw_online_hud`), extração método-a-método via AST (sem constantes de classe intercaladas — mesmo padrão de `HabilidadesHandlers`), verificada byte-idêntica |
| Refatoração hotbar de habilidades (slots 1-4) → mixin `HotbarHandlers` (item 2 do PLANO_ACAO, etapa 4.10) | ✅ completo | `client/hotbar_handlers.py` (`GameEngine(..., OnlineModeHandlers, HotbarHandlers)`); 5 métodos (~496 linhas: bloqueio de skill por talento não alocado, clique em slot da hotbar/barra de consumíveis, vinheta de HP baixo e desenho completo da hotbar — ícones, cooldown, GCD, custo de fúria, indicador de proc, drag-and-drop com remoção — `_is_talent_locked`, `_handle_hotbar_click`, `_handle_consumable_bar_click`, `_draw_low_hp_vignette`, `_draw_hotbar`) + 5 constantes de classe (`_HB_W`, `_HB_H`, `_HB_ICO`, `_HB_PAD`, `_SKILL_FALLBACK_COLORS`), extraído como bloco contíguo único (mesmo padrão de `MenuHandlers`/`HotbarEditorHandlers`: divisor + constantes + métodos via slice literal de linhas), verificado byte-idêntico. Também relocou `_TALENT_SKILL_REQS` (constante de módulo — lookup reverso skill→talento gerado de `talent_data.TALENTS`, usada exclusivamente por `_is_talent_locked`/`_draw_hotbar`) para `client/hotbar_handlers.py` como constante de módulo — mesmo princípio de relocação de `_DEBUG_MAP_NAMES` (etapa 4.5), porém em escopo de módulo (não de classe) |
| Refatoração barra de consumíveis → mixin `ConsumableBarHandlers` (item 2 do PLANO_ACAO, etapa 4.11) | ✅ completo | `client/consumable_bar_handlers.py` (`GameEngine(..., HotbarHandlers, ConsumableBarHandlers)`); 1 método (~232 linhas: slots, ícones, cooldown/GCD compartilhado com a hotbar de skills, contagem de stack, atalhos de teclado e drag-and-drop com remoção — `_draw_consumable_bar`), extraído como bloco contíguo único — divisor "Barra de consumíveis" viajou junto com o método para o mixin (mesmo princípio de `OnlineModeHandlers`/`HabilidadesHandlers`: o título da seção pertence à seção), verificado byte-idêntico |
| Refatoração HUD principal + barra de cast → mixin `HudHandlers` (item 2 do PLANO_ACAO, etapa 4.12) | ✅ completo | `client/hud_handlers.py` (`GameEngine(..., ConsumableBarHandlers, HudHandlers)`); 2 métodos (~249 linhas: barras de vida/mana/fúria, ícones de buff/debuff, minimapa, ouro, equipamentos rápidos e talentos alocados (`_draw_hud`); barra de cast/canalização no centro inferior da tela (`_draw_cast_bar`)), extraído como bloco contíguo único — divisor de uma linha (sem título) viajou junto com a seção (mesmo princípio de `OnlineModeHandlers`/`ConsumableBarHandlers`), extração via slice literal de linhas (métodos separados só por uma linha em branco, sem constantes de classe intercaladas), verificado byte-idêntico |
| Relocação/limpeza do resíduo final do painel de inventário (item 2 do PLANO_ACAO, etapa 4.17) — **conclusão da Etapa 4** | ✅ completo | Investigação pós-4.12 confirmou que o resíduo "Painel de equipamentos + inventário (tecla I)" no fim de `game.py` (16 constantes de classe + `_panel_origin`) era scaffolding de quando o painel era desenhado em `GameEngine` — os métodos de desenho/clique já viviam em `InventoryHandlers`. Confirmado via `grep -rn "self\._NOME"`: 13 itens (`_RARITY_COLORS`, `_PANEL_W`, `_PANEL_H`, `_PAD`, `_HEADER_H`, `_EQ_W`, `_EQ_SLOT_H`, `_EQ_ICON`, `_BODY_H`, `_INV_SLOT`, `_INV_COLS`, `_INV_GAP`, `_panel_origin`) usados exclusivamente por `InventoryHandlers`; 4 itens (`_STATS_H`, `_SLOT_W`, `_SLOT_H`, `_SLOT_PAD`) com zero referências — código morto. Relocados os 13 (primeira relocação para um mixin PRÉ-EXISTENTE — diferente de `_DEBUG_MAP_NAMES`/`_TALENT_SKILL_REQS`, que foram para mixins recém-criados): inseridos logo após `class InventoryHandlers:`, byte-idênticos, com docstring atualizado (deixou de documentar essas constantes como "fornecidas" por `GameEngine`); os 4 mortos foram removidos. Verificado byte-idêntico + ausência total dos nomes mortos (`(?<!\w)NOME\b`, pois `_SLOT_H` é substring de `_EQ_SLOT_H` viva) + corpo pré-existente de `InventoryHandlers` intocado. Smoke test: `GameEngine` resolve as 13 entidades via MRO e os 4 nomes mortos levantam `AttributeError`. `game.py`: 2059 → **2025 linhas** (de 7030 originais, ~71% de redução) — **Etapa 4 ("game.py volta a ser apenas init+loop+render") concluída** |
| Gate centralizado de dano servidor (`_apply_final_damage`) | ✅ completo | `server/spell_completion_processor.py::_apply_final_damage` |
| Imunidade de Bloco de Gelo a dano ranged (arqueiro) | ✅ completo | `server/spell_completion_processor.py::_server_apply_ranged_physical` → `_apply_final_damage` |
| PvP: skills de alvo único suportam `RemoteControlled` (`_target_alive`) | ✅ completo | `skill_handlers.py::_target_alive` |
| Instâncias (dungeons/raids) | 🔲 pendente | `server/zone_manager.py` |
| Client-side prediction de movimento | 🔲 pendente | `client/` |

---

## Problemas conhecidos e TODOs

### Críticos (C) — afetam gameplay

| ID | Problema | Impacto | Localização |
|----|---------|---------|------------|
| C1 | Pirofagia/Tiro Múltiplo: cliente deduz mana/CD localmente e envia direção, mas servidor não implementa cálculo de cone (lag compensation com `get_snapshot_at` não usada) | Skills de cone não causam dano no modo online | `server/world_server._process_skill_requests`, `systems.PirofagiaSystem` |
| C3 | Lag compensation real: `get_snapshot_at()` existe mas não é chamada em `_process_skill_requests` — range check usa posição atual, não snapshot do timestamp do cliente | Pode rejeitar skills válidas em alta latência | `server/world_server.get_snapshot_at` |
| C4 | ~~StatusEffectSystem ausente no servidor~~ → **RESOLVIDO**: `_ServerSFX` herda `core_systems.StatusEffectSystem`; `_emit_damage/_emit_heal` adicionam a `_combat_this_tick`; efeitos sincronizados no cliente via `AOI_UPDATE.effects` | — | `server/world_server._load_map` |
| C5 | ~~Aggro prematuro: mob agrava ao apertar a tecla de skill (ex: Flecha Reiterada), antes do dano da skill ser confirmado~~ → **RESOLVIDO**: handlers server-mode de skills com `cast_time` nunca setavam `combat_state.is_casting = True` (só o offline/predição cliente faziam isso). Com `is_casting=False`, `can_act()` ficava sempre `True` durante o cast, e o auto-attack genérico (cooldown 0-inicializado) disparava dano+aggro REAL antes/durante o cast da própria skill. Corrigido: `is_casting=True` ao enfileirar `_pending_spell_completions` (`server/skill_processor.py`); `is_casting=False` quando o último pending resolve (`server/spell_completion_processor.py::_process_spell_cast_completions`) ou quando o cast é cancelado e não há outro pendente (`server/session.py::_handle_cancel_cast`) | — | `server/skill_processor.py`, `server/spell_completion_processor.py`, `server/session.py` |
| C6 | ~~Mob recebe golpe final: desaparece sem mostrar o dano~~ → **RESOLVIDO** (2 causas): **(1)** em `_build_update_for_session`, o loop de despawns roda ANTES do filtro de `combat`, e chama `session.known_eids.discard(eid)` para o mob que está morrendo. O filtro de combat checa `cr["target"] in session.known_eids` — como o eid do mob já foi removido por esse discard, o COMBAT_RESULT do golpe fatal (mesmo tick do despawn) era descartado antes de ser enviado ao cliente. Corrigido unindo `final_despawned` ao conjunto de eids aceitos pelo filtro. **(2)** `_server_apply_magic_damage` (dano de Bola de Fogo, Calcinar, Nova Congelante, Calamidade Flamejante) aplicava `current_hp -= dmg` e `PendingDeath` mas NUNCA gerava entrada em `_combat_this_tick` — apenas o handler de canalização (Calamidade Flamejante) montava o evento manualmente. Para as demais skills mágicas, um kill nessas condições despawnava o mob no mesmo tick sem nenhum `COMBAT_RESULT`, então nem a correção (1) tinha o que enviar. Corrigido centralizando a emissão de `_combat_this_tick` (+ PvP HP-sync) dentro do próprio `_server_apply_magic_damage`, removendo a montagem duplicada do handler de canalização. ("Regen" da mesma queixa original já estava resolvido pela instrumentação `HP_DELTA` confirmando ausência de mutações anômalas — `_mob_hp_prev` permanece como instrumentação ativa em `logs/mob_combat.log`) | `server/session.py::_build_update_for_session`, `server/spell_completion_processor.py::_server_apply_magic_damage` |
| C7 | ~~Auto-attack do arqueiro: mob morre antes de mostrar dano/flecha~~ → **RESOLVIDO**: causa era a predição local de flecha em `_process_archer_combat` (`systems.py`), guiada por um timer client-side (`combat_stats.attack_cooldown_timer`) INDEPENDENTE do timer server-side (`server/combat_processor.py::_attack_timers`). Quando o servidor resolvia e reportava o golpe fatal ANTES do timer local do cliente disparar, a flecha (`PlayerProjectile`) nunca era criada — o golpe matava o mob "sem flecha, sem FLT, sem HP update". Corrigido seguindo o padrão da Bola de Fogo (projétil só nasce em resposta a confirmação do servidor, nunca por timer local): `PlayerInputSystem._net` (injetado por `game.py` só no modo online) faz `_process_archer_combat` pular toda a criação local de `PlayerProjectile`/sons de flecha (mantém só cooldown/aljava/UI), e `_apply_combat_result` (`client/remote_entity_handlers.py`) cria a flecha (`spell_id="arrow"`, `damage_type="physical"`, `color=(101,67,33)`) ao receber `COMBAT_RESULT` com `source="auto"` do arqueiro — flecha nasce na posição do player e mira `target_id`/`target_last_x/y`, igual BdF nasce no `is_completion`. Render já suportado por `PlayerProjectileSystem.render` (branch `damage_type=="physical"`, idêntico ao usado por flecha reiterada/auto-attack offline) | `systems.py::PlayerInputSystem/_process_archer_combat`, `client/remote_entity_handlers.py::_apply_combat_result`, `game.py` |
| C8 | ~~Recarregar recusa flechas recém-compradas até relogar~~ → **RESOLVIDO**: `_server_recarregar` valida munição contra o `Inventory` em memória do servidor, mas `process_shop_buy` (compra em loja) só devolvia `item_data` ao cliente (que atualiza seu próprio `Inventory` local e manda `SAVE_STATE`) — nunca atualizava o `Inventory` em memória do servidor. `SAVE_STATE` persiste no banco mas não re-popula esse componente (só `load_player_inventory`/spawn ou `INV_SYNC` o fazem, e compra não dispara `INV_SYNC`). Resultado: comprar flechas e tentar Recarregar na mesma sessão falhava com "Não há flechas disponíveis para recarregar"; só funcionava após relogar (spawn recarrega `Inventory` do banco já atualizado). Corrigido: `process_shop_buy` agora também adiciona o item comprado ao `Inventory` em memória do servidor (empilha em stack existente ou cria novo slot via `entry["factory"]`), igual à lógica client-side `_buy_qty` | `server/world_server.py::process_shop_buy`, `server/spell_completion_processor.py::_server_recarregar` |
| C9 | ~~Recarregar não persiste ao deslogar~~ → **RESOLVIDO**: `_apply_recarregar` (`spell_system.py`) é client-authoritative — completa o cast localmente e muta `Equipment.slots["offhand"]` (quiver.arrow_count/subtype) e `Inventory.items` (bag) diretamente, sem nenhum aviso ao código de persistência. Sem hook, esse estado só era salvo no próximo `_send_save_state()` (loot, talento, hotbar, autosave 5min) — se o jogador deslogasse antes, a recarga sumia (bag e aljava voltavam ao estado anterior no próximo login). Corrigido: novo callback `SpellCastSystem._on_inventory_changed` (injetado por `game.py` → `_on_recarregar_changed`), chamado ao final de `_apply_recarregar` em todo caminho que muta bag/aljava (recarga normal + troca de tipo de flecha) — dispara `_on_loot_action("item")` (sincroniza `Inventory` em memória do servidor) + `_send_save_state()` (persiste no banco imediatamente). Ver regra geral na seção 6 (Persistência) | `spell_system.py::_apply_recarregar`, `client/save_sync_handlers.py::_on_recarregar_changed`, `game.py` |
| C10 | ~~Canção de Ninar: som só toca 2s depois do botão (no fim do canal), offline toca na hora~~ → **RESOLVIDO**: offline, `_skill_cancao_ninar` (`skill_handlers.py`) toca `SOUNDS.play_skill("skill_cancao_ninar")` ANTES de criar o `SpellCast` (canal de 2s) — o som de "começar a cantar" toca no aperto do botão. Online, esse handler nem roda no cliente (server-authoritative → `_use_skill_visual_only`); o som genérico de skills com `cast_time` só é tocado no `_is_completion` do `SKILL_RESULT`, 2s depois (quando o sono já foi aplicado), dessincronizando o áudio do efeito. Corrigido: `_SOUND_ON_CAST_START = {"cancao_ninar"}` em `client/network_handlers.py` — para skills nesse set, o som toca no `cast_started` (início do canal) em vez do `is_completion` | `client/network_handlers.py::_handle_msg_skill_result` |
| C11 | ~~Canção de Ninar: cancelar o canal não acorda os mobs, e ataques (flecha) não quebram o sono~~ → **RESOLVIDO** (2 partes): **(1)** Server-side `_skill_cancao_ninar` (`skill_handlers.py`, roda no início do cast via `_skill_{sid}` dispatch de `skill_processor.py`) já aplica sono IMEDIATAMENTE a todos os mobs no raio e popula `CharacterStats.lullaby_targets` — igual ao offline. Porém `_server_cancao_ninar` (completion, +2s) reaplicava sono de novo (redundante) e `_handle_cancel_cast` (`server/session.py`) só removia o pending da fila — nunca acordava os `lullaby_targets`, então cancelar o canal deixava os mobs dormindo a duração cheia. Corrigido: `_server_cancao_ninar` agora só limpa `lullaby_targets` (igual `_apply_cancao_ninar_complete` offline, sono já foi aplicado no início e continua normalmente); `_handle_cancel_cast` ganhou o equivalente a `_cancel_cancao_ninar` — se `sid=="cancao_ninar"`, remove "sleep" (e cancela o `on_expire_effect="slow"` encadeado) de cada `lullaby_targets` e limpa a lista. **(2)** "Sono quebra ao tomar dano" (systems.py:665-677, `deal_damage`) só roda no caminho melee/spells genéricas — auto-attack ranged do arqueiro usa `_server_apply_ranged_physical` (`spell_completion_processor.py`), que removia "polymorph" mas não tinha a lógica de quebrar "sleep". Corrigido adicionando o mesmo bloco (remove "sleep" + cancela `on_expire_effect`) em `_server_apply_ranged_physical` | `server/spell_completion_processor.py::_server_cancao_ninar`, `server/spell_completion_processor.py::_server_apply_ranged_physical`, `server/session.py::_handle_cancel_cast` |
| C12 | ~~Ícone de efeito (sono/lento) em mob fica "fantasma" contando sozinho após o servidor remover o efeito~~ → **RESOLVIDO**: `_handle_msg_skill_result`/AOI_UPDATE só chamava `_sync_mob_effects(_mob_efx)` quando `mob_effects` vinha não-vazio (`if _mob_efx:`). Quando o servidor remove "sleep" do último/único mob com efeito (ex: C11 — cancelar Canção de Ninar acorda o mob), `_collect_mob_effects` passa a retornar `{}` (mob some das chaves por não ter mais `sfx.effects`) — e com o dict vazio o handler nunca roda, então o early-clear de `_sync_mob_effects` ("Limpa efeitos de mobs que o servidor não enviou") nunca executa. O `ActiveEffect("sleep", ...)` local do mob fica orfão e o `StatusEffectSystem` do cliente (game.py, roda em todo frame) continua decrementando `duration` e desenhando o ícone com contagem, mesmo já acordado no servidor. Corrigido: `_sync_mob_effects` agora é chamado SEMPRE (mesmo com `{}`), garantindo que o early-clear rode every tick | `client/network_handlers.py::_handle_msg_skill_result` |
| C13 | ~~Auto-attack de arqueiro REMOTO (outro player) contra mob soa como golpe corpo a corpo, sem flecha~~ → **RESOLVIDO**: `_apply_combat_result` só tratava `_is_archer_arrow` (nasce flecha visual + som de disparo, HP diferido até colisão) quando `server_attacker == self._my_eid` — para outro player (PvE com mais jogadores, ou flechada vista por terceiros), o ataque caía no branch genérico e tocava `hit_normal_*`/`hit_crit_*` (som de impacto corpo a corpo), sem flecha nem som de disparo. Corrigido: detecção de "atacante é arqueiro" agora também checa `RemoteControlled.class_id == "arqueiro"` do `server_attacker` via `self._remote_players`; a flecha visual nasce na posição do player remoto (`attacker_id` = seu eid local) e os sons `arrow_draw_*`/`arrow_release_*` tocam posicionalmente (`play_random_at` com falloff por distância) em vez de `play_random` (full volume, usado só para o próprio player) | `client/remote_entity_handlers.py::_apply_combat_result` |
| C14 | ~~Som de impacto da flecha (`arrow_impact_1/2`) toca sempre no volume cheio, sem falloff por distância~~ → **RESOLVIDO**: os 6 pontos de `_on_hit` (`PlayerProjectileSystem`, `spell_system.py`) que tocavam `SOUNDS.play_random(["arrow_impact_1","arrow_impact_2"], channel_group=(12,13))` usavam volume fixo, diferente dos demais sons do arqueiro (draw/release, já corrigidos em C13 com `play_random_at`). Corrigido: `PlayerProjectileSystem` agora recebe `player_entity` no construtor (`game.py`) e ganhou `_player_world_pos()` (mesmo padrão de `AoeTargetingSystem`); `_on_hit` recebe `impact_x/impact_y` (posição do projétil no momento da colisão, passada pelos 2 call-sites em `update()`) e centraliza o som de impacto em `_play_arrow_impact_sound()` — toca `play_random_at(impact_x, impact_y, player_x, player_y, base=1.0, channel_group=(12,13))` com falloff, e cai para `play_random` (sem falloff) só se a posição do jogador local não estiver disponível | `spell_system.py::PlayerProjectileSystem` (`__init__`, `_player_world_pos`, `update`, `_on_hit`), `game.py` |
| C17 | ~~Mob (ex.: Zumbi) ataca player REMOTO → toca som genérico (`hit_normal_*`/`hit_crit_*`, "melee de guerreiro") em vez do som de ataque do mob (ex.: "bite" via `MobSounds.attack_melee`)~~ → **RESOLVIDO**: a resolução de "som do atacante" (mob remoto rastreado → `MobSounds.attack_melee/ranged/magic` via `AIControlled.entity_class/is_ranged`, posicional com falloff) só existia no branch "Player LOCAL foi atacado" de `_apply_combat_result`. O branch "Player REMOTO foi atacado" nunca resolvia o atacante — sempre tocava `hit_normal_*`/`hit_crit_*` genérico, independente de o atacante ser mob ou player. Esse era um caso concreto do problema geral apontado pelo usuário: a escolha do som dependia de QUAL branch (quem é o alvo), não de QUEM é o atacante. Corrigido extraindo `_play_attacker_mob_sound(server_attacker, lx, ly)` — resolve 100% via componentes (`MobSounds` + `AIControlled`, nada de nome de mob hardcoded), retorna `False` se o atacante não é um mob remoto rastreado (PvP → fallback genérico). Chamado por AMBOS os branches ("player local atacado" e "player remoto atacado"), garantindo que o som do golpe dependa apenas da identidade do atacante, simetricamente, em qualquer ponto de vista | `client/remote_entity_handlers.py::_play_attacker_mob_sound`, `_apply_combat_result` |
| C18 | ~~Som de mob/player remoto continuava audível (piso de 5%) mesmo bem fora do AOI; sem pan estéreo (mono)~~ → **RESOLVIDO** (2 partes): **(1)** `volume_at` usava `MAX_WORLD_SOUND_DIST=320px` (10 tiles) com piso `MIN_WORLD_SOUND_VOL=0.05` aplicado também ALÉM do raio (`dist >= MAX → 0.05`, nunca zero) — enquanto `AOI_RADIUS=15 tiles=480px`. Resultado: fontes a até 480px (dentro do AOI, ainda "conhecidas" pelo cliente) tocavam pra sempre em 5%, mesmo o jogador se afastando bastante. Corrigido: `MAX_WORLD_SOUND_DIST = AOI_RADIUS * TILE_SIZE` (480px, importado de `shared/constants` — fonte única), e `volume_at` retorna `0.0` (não apenas o piso) quando `dist >= MAX`; os 6 wrappers `play_*_at` abortam sem chamar `play()` quando `vol <= 0`. Curva final: 100% em dist=0 → 5% em 480px → silêncio além do AOI. **(2)** Adicionado pan estéreo para todos os sons posicionais: `SoundManager.pan_at(sx, lx)` calcula `-1..+1` pelo eixo X do mundo (câmera não rotaciona — direita no mundo = canal direito), `_pan_gains(pan)` converte para ganhos L/R lineares (centro = 1.0/1.0, idêntico ao comportamento mono anterior — sem perda de volume para sons centrados), aplicados via `Channel.set_volume(left, right)` após `ch.play()`. `pan` é parâmetro opcional (default 0.0/centro) propagado por toda a cadeia `play → play_random/play_skill/play_mob → play_mob_sounds/play_emote_*`; os wrappers `_at` calculam e passam automaticamente | `sound_manager.py` (`pan_at`, `_pan_gains`, `volume_at`, `play*`) |
| C19 | ~~Auto-attack de arqueiro em PvP (vs player) sem flecha visual/som correto; FLT de multi-hit (Flecha Reiterada) só aparece no 1º golpe para espectadores; Picada de Escorpião sem cor verde; Flecha Reiterada sem visual sequencial em espectadores~~ → **RESOLVIDO** (4 partes): **(1)** A detecção "atacante é arqueiro → nasce flecha visual + som de disparo, dano/som de impacto diferidos" (C7/C13) só existia no branch "Mob foi atacado" de `_apply_combat_result`. Nos branches "Player local foi atacado" e "Player remoto foi atacado" (PvP), o ataque do arqueiro caía no caminho genérico — sem flecha, som `hit_normal_*`/`hit_crit_*` (melee) em vez de `arrow_release_*`. Extraída a detecção comum para `_resolve_archer_attack(cr, server_attacker, source)` (retorna `is_archer_arrow, attacker_eid, is_self_attacker`, cobre arqueiro local OU remoto via `RemoteControlled.class_id`) e a criação da flecha+sons para `_spawn_archer_auto_arrow(...)`; ambos os branches PvP agora criam a flecha e enfileiram o resultado em `pending_arrow_impacts[local_eid]`, com dano/FLT/som de impacto diferidos para `_on_hit` (igual ao mob). **(2)** `_on_hit` (`spell_system.py`) ganhou branch `_isplr_oh` (`entry["is_player_target"]`) — FLT de impacto de flecha contra player usa vermelho `(220,80,80)` (padrão de dano-em-player), em vez do esquema crit/block/normal usado para mobs. **(3)** FLT só do 1º hit em multi-hit (Flecha Reiterada) para espectadores: `_handle_msg_skill_result` (loop de `targets` para espectadores) não propagava `is_proj_damage` no `_cr_t` — sem essa flag, hits subsequentes (que não têm flecha própria nascendo na tela do espectador) caíam no caminho "diferir FLT para impacto de flecha" e o impacto nunca chegava (sem entidade de flecha) → FLT perdido. Corrigido propagando `payload.get("is_proj_damage", False)` em `_cr_t`. **(4)** Visual sequencial de Flecha Reiterada/Picada de Escorpião/Tiro Repulsivo para espectadores: novo handler de `proj_incoming` em `_handle_msg_stats_update` (`sid in ("flecha_reiterada","picada_escorpiao","tiro_repulsivo")`, atacante != local) cria projéteis cosméticos (`target_server_id=-2`, sem round-trip ao servidor) — Flecha Reiterada cria N flechas sequenciais (`proj_arrow_count`, enviado pelo servidor em `spell_completion_processor.py`/`session.py`, considera talento "Sequência Final") com `launch_delay` escalonado via `arrow_delay` do `SKILL_CATALOG`; Picada de Escorpião/Tiro Repulsivo criam 1 flecha cosmética. `_on_hit` para `target_server_id == -2` físico agora toca som de impacto de flecha (antes não tocava nenhum som). Brinde cosmético: cor da flecha de Picada de Escorpião alterada para verde `(60,200,80)` (era marrom) tanto no `is_completion` local quanto no cosmético de espectador | `client/remote_entity_handlers.py` (`_resolve_archer_attack`, `_spawn_archer_auto_arrow`, `_apply_combat_result`), `client/network_handlers.py` (`_handle_msg_skill_result`, `_handle_msg_stats_update`), `spell_system.py::_on_hit`, `server/spell_completion_processor.py`, `server/session.py` |
| C20 | Picada de Escorpião não lança projétil nem desconta flecha da aljava ao usar; Flecha Reiterada "não funciona" (dano/completion) — **NÃO CONFIRMADO** (parte "sem barra de cast ao andar" foi isolada e resolvida em C23). Precisa repro com `logs/spell_debug.log` mostrando se `_server_picada_escorpiao`/`_server_flecha_reiterada` chegam a executar e se `quiver.arrow_count` é decrementado | `skill_handlers.py`, `server/spell_completion_processor.py` |
| C21 | ~~Atacar player remoto com arqueiro (auto-attack PvP) crasha o cliente do alvo: `AttributeError: 'NoneType' object has no attribute 'crit_rating'` em `damage_calculator.resolve_attack_outcome`~~ → **RESOLVIDO**: `PlayerProjectileSystem.update` (`spell_system.py` ~1018-1036), ao decidir o outcome do impacto de uma flecha física não-`guaranteed_hit`, só tratava `target_cs is None` (mob/player remoto sem `CombatStats`) caindo no outcome pré-computado via `pending_arrow_impacts`; quando o ALVO é o player LOCAL (`target_cs` existe) mas o ATACANTE é um player remoto (sem `CombatStats` local — `attacker_cs is None`), caía no `else` e chamava `resolve_attack_outcome(None, target_cs, ...)` → crash. Corrigido: condição agora é `if target_cs is None or attacker_cs is None:` (idem no branch de consumo de `pending_arrow_impacts` em caso de miss/dodge/parry) | `spell_system.py::PlayerProjectileSystem.update` |
| C22 | ~~Lançamento de Picada de Escorpião/Flecha Reiterada/Tiro Repulsivo por player remoto (PvP) é silencioso para o espectador~~ → **RESOLVIDO**: o handler `proj_incoming` (C19-4, `_handle_msg_stats_update`) cria os projéteis cosméticos mas não tocava nenhum som no momento do lançamento — apenas o impacto (via `_on_hit`/`target_server_id==-2`) tinha som. Corrigido: adicionado som posicional de saque/disparo (`arrow_draw_*` 35% chance + `arrow_release_*`, `play_random_at` com falloff, igual ao padrão de auto-attack remoto de C13) antes da criação dos projéteis cosméticos | `client/network_handlers.py::_handle_msg_stats_update` |
| C23 | ~~Picada de Escorpião (ou qualquer skill com `cast_time`) "sai" sem mostrar a barra de cast quando o player já está em movimento ao apertar a tecla — efeito (slow) e cooldown aplicam normalmente no servidor, como se não tivesse sido cancelado~~ → **RESOLVIDO**: causa é uma corrida entre duas filas no servidor. `CAST_SKILL` (`_handle_cast_skill` → `queue_skill`) só ENFILEIRA o request em `_pending_skill_requests` — a conversão para `_pending_spell_completions` (com `timer = cast_time`) só ocorre no PRÓXIMO tick, em `_process_skill_requests`. Quando o player já está andando, `SpellCastSystem.update` detecta `tm.is_moving and interruptible` no mesmo/frame seguinte à criação do `SpellCast` e cancela IMEDIATAMENTE (`CANCEL_CAST` enviado quase no mesmo instante do `CAST_SKILL`). `_handle_cancel_cast` filtrava apenas `_pending_spell_completions`/`_spells_in_flight_queue` — como o request ainda estava em `_pending_skill_requests` (não processado), o cancel não encontrava nada para remover; no tick seguinte o request era processado normalmente, criava a entrada em `_pending_spell_completions` e o cast completava (slow + cooldown) sem que o cliente tivesse barra de cast visível (criada e removida no mesmo frame por `SpellCastSystem`). No caso "inicia cast e move depois" o `CANCEL_CAST` chega bem depois (após o `cast_time`), quando o request já foi processado — por isso esse caso já funcionava. Corrigido: `_handle_cancel_cast` também filtra `_pending_skill_requests` por `player_eid`+`sid` | `server/session.py::_handle_cancel_cast`, `server/world_server.py::queue_skill/_pending_skill_requests` |
| C24 | ~~FLT de dano recebido em auto-attack de arqueiro (PvP) não aparece na tela da VÍTIMA~~ → **RESOLVIDO**: no branch "Player local foi atacado" de `_apply_combat_result`, auto-attack de arqueiro (`source=="auto"`) cria a flecha visual via `_spawn_archer_auto_arrow(..., target_local_eid=self.player_entity, ...)` e diferere o FLT/dano para `PlayerProjectileSystem._on_hit` (via `pending_arrow_impacts[self.player_entity]`, `is_player_target=True`). Em `_on_hit`, o branch que consome `pending_arrow_impacts` e mostra o FLT só era executado quando `target_cs is None` — mas `proj.target_id == self.player_entity` é o PRÓPRIO player local, que TEM `CombatStats`. Resultado: caía direto no pipeline de dano físico local (`deal_damage`), recalculando dano client-side (potencialmente double-dano) e nunca exibindo o FLT vermelho de "dano recebido". Corrigido com o mesmo padrão de C21: condição ampliada para `if target_cs is None or attacker_cs is None:` — como o atacante é um player remoto (`attacker_cs is None`), agora entra no branch correto e consome `pending_arrow_impacts` (FLT vermelho + som de impacto) | `spell_system.py::PlayerProjectileSystem._on_hit` |
| C25 | ~~PvP arqueiro: ao errar a flecha contra player, sem efeito visual de flecha (nem no atacante nem na vítima) e toca som de erro melee; skills de flecha (Picada de Escorpião/Flecha Reiterada/Tiro Repulsivo) acertando player tocam som de impacto melee em vez de flecha~~ → **RESOLVIDO**: contra mobs, a flecha SEMPRE nasce no auto-attack (`source=="auto"`), independente do outcome — miss/dodge/parry/block são resolvidos em `_on_hit` via `pending_arrow_impacts` (flecha voa, erra, sem som de impacto). Nos branches PvP "Player local foi atacado" e "Player remoto foi atacado" de `_apply_combat_result`, a condição de nascimento da flecha exigia `damage > 0` — miss/dodge/parry/block (damage=0) caía no caminho genérico (sem flecha, som `combat_miss`/melee). Corrigido: condição ampliada para `damage > 0 or outcome in ("miss","dodge","parry","block")` em ambos os branches — flecha nasce e o outcome é diferido para `_on_hit` (mesmo padrão de mob), sem som de impacto em caso de erro/esquiva/aparo/bloqueio. Adicionalmente, `_ARROW_SKILL_IDS = {"picada_escorpiao","flecha_reiterada","tiro_repulsivo"}` (constante de classe, antes local a `_resolve_archer_attack`) agora também é checada no bloco de som de impacto (`elif damage > 0`) de ambos os branches PvP: se `cr["sid"]` é uma skill de flecha, toca `arrow_impact_1/2` (com falloff posicional no branch remoto) em vez do genérico `hit_normal/crit` | `client/remote_entity_handlers.py` (`_apply_combat_result`, `_ARROW_SKILL_IDS`) |
| C26 | ~~Canção de Ninar perto de player remoto (PvP) acusa "Nenhum alvo no raio" e não executa — o player remoto deveria ser um alvo válido, e a skill é AoE (não deveria nem exigir alvo)~~ → **RESOLVIDO**: `_skill_cancao_ninar` (`skill_handlers.py`) selecionava alvos iterando `Enemy + AIControlled + TileMovement` — só mobs controlados por IA contam, players remotos (sem `Enemy`/`AIControlled`) nunca entravam na lista; com `targets` vazio, `if not targets: return False` abortava o cast (sem canal, sem som, sem aplicar sono). Corrigido seguindo o padrão de `_skill_impacto` (PvP): iteração agora é `TileMovement + CombatStats` (exclui o próprio caster, `current_hp > 0`), cobrindo mobs E players remotos no raio. Além disso, removido o early-return "Nenhum alvo no raio" — a skill é AoE e deve sempre executar (canal de 2s, custo de Concentração, som), aplicando sono a quem estiver no raio (zero ou mais alvos) | `skill_handlers.py::_skill_cancao_ninar` |
| C27 | ~~Efeito sleep (Canção de Ninar etc.) não impede o player-alvo de se mover/agir — apenas mobs (`EnemyAISystem`) respeitavam `sfx.has("sleep")`~~ → **RESOLVIDO**: `StatusEffectSystem` sincroniza `slow_mult`/`is_rooted`/`is_crowd_controlled` a partir de `StatusEffects`, mas `CombatState.can_move()`/`can_act()` (usados por `PlayerInputSystem`) não checam `is_crowd_controlled` nem `StatusEffects` diretamente — sleep não tinha efeito sobre o player local. Corrigido em 2 camadas: **(1)** Cliente — `PlayerInputSystem.update` ganhou checagem análoga ao bloco `_is_disoriented` já existente: se `StatusEffects.has("sleep")`, força `can_move=False, can_act=False` (igual ao comportamento de mobs dormentes em `EnemyAISystem`). **(2)** Servidor (autoritativo) — `move_player` (`world_server.py`) agora rejeita o move se `StatusEffects` do player tem `"sleep"`, `"stun"` ou `"root"` (CC totalmente imobilizante; `disoriented`/`polymorph` ficam de fora propositalmente — o wander aleatório nesses casos é decidido pelo cliente via `CombatStateSystem` e enviado como MOVE normal) | `systems.py::PlayerInputSystem.update`, `server/world_server.py::move_player` |
| C28 | ~~Em PvP, quando um player mata outro: a vítima não "respawna visivelmente" para os espectadores (em particular o assassino) — fica invisível, sem restaurar vida na tela de quem a matou~~ → **RESOLVIDO**: `_handle_player_death` (`respawn_system.py`) teleporta a vítima para `RESPAWN_TILE` ANTES de registrar o evento de movimento e o broadcast de HP restaurado. **(1)** `_moved_this_tick` registrava `from_tx/from_ty` = posição JÁ teleportada (igual a `tx/ty`) — `_build_update_for_session` calcula `in_aoi(from)` a partir dessa posição errada, então quem estava perto do local da morte (o assassino) nunca recebia o `ENTITY_DESPAWN`/`ENTITY_MOVE` correto da vítima. Corrigido: captura `old_tx/old_ty` (posição ANTES do teleporte) e usa como `from_tx/from_ty`. **(2)** O broadcast de HP restaurado (`_player_hp_broadcasts_this_tick`, mecanismo genérico de self-heal) é filtrado por AOI em `session.py` usando `get_tile_pos(_caster_sid)` — após o teleporte, essa é a posição de RESPAWN (longe de todos), então o `STATS_UPDATE` de HP restaurado nunca chegava a quem estava perto da morte. Corrigido: `_handle_player_death` agora inclui `bcast_tx/bcast_ty` (= `old_tx/old_ty`, posição de morte) na entrada do broadcast; `session.py` (`_hp_bcast` loop) usa `_hp_upd.get("bcast_tx"/"bcast_ty")` quando presentes (death/respawn) em vez de `get_tile_pos(_caster_sid)`, mantendo o fallback original para broadcasts de self-heal (sem teleporte) | `server/respawn_system.py::_handle_player_death`, `server/session.py` (loop `_hp_bcast`) |
| C29 | ~~Em PvP, após C28: para o espectador (assassino), o corpo da vítima "caminha" visivelmente do local da morte até o tile de respawn (em vez de sumir e reaparecer); e a vítima continua recebendo dano de Flecha Reiterada (multi-hit) já em voo mesmo após respawnar com HP restaurado~~ → **RESOLVIDO** (2 partes): **(1)** A entrada de `_moved_this_tick` criada por `_handle_player_death` (C28, `from_tx/from_ty`=morte → `tx/ty`=respawn, tiles distantes) era processada por `_apply_remote_move` como um movimento normal — `start_tile_movement` anima o `TileMovement` do remoto entre os dois tiles ao longo de `move_duration`, produzindo a "caminhada" até o respawn. Corrigido: a entrada ganhou `"teleport": True`; `_handle_msg_aoi_update` propaga `m.get("teleport", False)` para `_apply_remote_move`, que agora (quando `teleport=True`) faz snap instantâneo de `Position`/`TileMovement` (sem animação, cancela fila de moves pendente). **(2)** `_handle_player_death` (`respawn_system.py`) só removia de `_pending_spell_completions`/`_spells_in_flight_queue` as entradas onde `player_eid` (CASTER) == vítima — flechas de Flecha Reiterada já em voo, lançadas pelo atacante CONTRA a vítima (`target_id == player_eid` da vítima), permaneciam na fila e `_apply_spell_on_projectile_hit` continuava aplicando os hits restantes ao alvo já respawnado. Corrigido: ambos os filtros agora também excluem entradas onde `target_id == player_eid` | `server/respawn_system.py::_handle_player_death`, `client/remote_entity_handlers.py::_apply_remote_move`, `client/network_handlers.py::_handle_msg_aoi_update` |
| C30 | O respawn instantâneo (C28/C29) teleportava o player direto pro `RESPAWN_TILE` com HP restaurado — sem "seriedade": sem animação/corpo persistente, sem escolha do jogador. **RESOLVIDO**: substituído por um fluxo de espírito (ghost)/cemitério — ver seção "Fluxo de morte/espírito (ghost) + cemitério" abaixo. `_handle_player_death` agora só marca `GhostState.is_dead=True` e deixa o corpo (`current_hp==0`, `is_visible=True`) no local da morte; revive (full HP no cemitério ou 15% no corpo) é feito por `_revive_player` via `RELEASE_SPIRIT`/`REVIVE_REQUEST`/`_tick_ghost_states` | `server/respawn_system.py`, `components.py::GhostState`, `client/death_ui_handlers.py` |

### Arquiteturais (A) — débito técnico

| ID | Problema | Impacto | Localização |
|----|---------|---------|------------|
| A1 | `skill_handlers.py` importa `pygame` diretamente (linha 18) — workaround: `SDL_VIDEODRIVER=dummy` no servidor | Servidor depende de Pygame instalado | `skill_handlers.py:18`, `server/world_server.py:21` |
| A2 | `spawn_player()` é God Method (~180 linhas): CharacterStats, CombatStats, Equipment, PlayerSkills, Wallet, talents — difícil testar partes isoladas | Manutenção difícil | `server/world_server.spawn_player` |

### Game features (G) — faltam implementações

| ID | Problema | Impacto |
|----|---------|---------|
| G3 | `ENTITY_DEATH` implementado para morte de PLAYER (C30); mobs ainda despawnam direto, sem animação de morte | UX ruim (mobs) |
| G4 | `_server_dir_x/_server_dir_y` injetados em `TileMovement` mas handlers direcionais (Pirofagia, Tiro Múltiplo) não os consomem ainda | Skills de cone sem efeito online |
| G5 | `move_player()` não valida walkability — TODO comentado no código | Players podem atravessar paredes |

---

## Como rodar

```bash
# Dependências do servidor
pip install -r requirements_server.txt

# Servidor (cria banco e contas de teste automaticamente)
py -3.10 server/main.py

# Cliente A (outro terminal)
py -3.10 main.py

# Cliente B (terceiro terminal)
py -3.10 main.py
```

**Contas de teste criadas automaticamente:**
| Usuário | Senha | Classe |
|---------|-------|--------|
| `teste` | `123456` | Guerreiro |
| `teste2` | `123456` | Mago |

**Deletar banco:** apagar `data/game.db` — recriado na próxima inicialização.
