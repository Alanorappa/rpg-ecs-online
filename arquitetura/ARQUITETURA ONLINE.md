# Arquitetura Online — Decisões e Referência

> Documento vivo. Atualizar sempre que uma decisão arquitetural for tomada.
> Versão enxuta (08/08/2026) — separado do histórico de decisões datadas e do
> log "Problemas conhecidos e TODOs", que agora vivem em
> `arquitetura/historico/ARQUITETURA ONLINE HISTORICO.md`. Consultar o
> histórico só quando precisar do contexto completo de uma decisão específica
> (ex: por que um comportamento existe hoje) — não é leitura obrigatória de
> toda sessão.
>
> Nota: referências a "offline"/`systems.py:NNNN` neste arquivo são nomes de
> arquivo/função herdados (o arquivo `systems.py` ainda existe e é usado pelo
> cliente hoje) — não indicam que existe um modo offline separado. Não existe
> mais versão offline no projeto.

---

## Visão geral

RPG Tibia/WoW-style com servidor autoritativo em Python.
Clientes Pygame se conectam via WebSocket e recebem estado do mundo por ticks.

```
[Cliente Pygame] ──WebSocket──► [Servidor Python / asyncio]
                 ◄──────────────      ECS headless (SDL_VIDEODRIVER=dummy)
                                      SQLite (dev) → PostgreSQL (prod)
                                      30 ticks/s
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

### 3. Tick rate — 30 ticks/s (33ms)
- Movimento, combate, skills: 30 ticks/s
- Cliente roda a 60 fps com interpolação entre posições (visual fluido)
- Client-side prediction para movimento próprio (não implementado)

### 4. Servidor autoritativo
O servidor é a única fonte de verdade para:
- Dano (`damage_calculator.py`)
- Resultado de hit/miss/crit/dodge
- Posição final após knockback
- Drops de loot
- Estado de invisibilidade de outros jogadores

**Recursos (rage/mana/concentração) — servidor é o ÚNICO produtor (03/07/2026):**
O modelo antigo ("cliente gera localmente e sincroniza no `CAST_SKILL`") foi removido em duas etapas — era vulnerabilidade (cliente forjava valores) e causava dessincronia (dois relógios independentes; hotbar acendia com rage local que o servidor não tinha → "Raiva insuficiente" + snap da barra). Modelo atual:
- **Ganho de rage** (+5 por auto-attack, PvE e PvP): `combat_processor.py` → push imediato via `queue_stats_update({rage})`
- **Decay de rage** (−5/3s fora de combate): `ServerCombatStateSystem.rage_events` → STATS_UPDATE
- **Regen/custo de mana**: `mana_events` + dedução nos handlers/completion → STATS_UPDATE
- **Custos pós-skill**: `skill_processor` envia rage/mana/concentração após todo attempt
- Cliente online **não produz** rage: `PlayerInputSystem._add_rage` e o rage decay de `CombatStateSystem` são no-op com `_net` setado — `CharacterStats.rage` local é só display, alimentado por STATS_UPDATE.

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
| `map_id` | Servidor | Anti-teleporte entre zonas |
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

### 13. Multi-map — transições de zona (ZONE_CHANGE)

**Decisão:** `_MapBundle` por mapa dentro de um único `World()` global.

- `WorldServer._map_bundles: dict[str, _MapBundle]` — bundle de sistemas por mapa
- `WorldServer._player_maps: dict[str, str]` — mapa atual de cada sessão de player
- `MapLocation(map_file)` component em **todas** as entidades (mobs, NPCs, spawn_zones) — fonte única de verdade para mapa da entidade (P3 completo; `_eid_to_map` eliminado em 2026-07-01)
- `EnemyAISystem(map_filter=map_file)` e `SpawnZoneSystem(map_filter=map_file)` por bundle
- `PathfindingSystem(tilemap_entity=...)` e `TileValidationSystem(tilemap_entity=...)` por bundle
- P4: serviços (tile_validation/pathfinding) injetados DIRETO nos sistemas de cada bundle em `_load_map_for` — o `_svc` global fica apontando pro ÚLTIMO mapa carregado
- **`register_map_services_for(eid)` (ponto único)**: TODO entry point do servidor que executa handler compartilhado em nome de um player (skill request, spell completion, projectile hit) chama isto ANTES do handler — re-registra `_svc` com o bundle do mapa da entidade (via `MapLocation`). Sem isso, `is_tile_walkable`/`find_path`/`get_tilemap` de módulo validavam contra o mapa errado (bug real: Interceptar "Caminho bloqueado" em terreno aberto; Tiro Repulsivo stunando em parede fantasma). `move_player` usa o bundle direto (mesma classe de bug, corrigida antes pontualmente)

**Carregamento (depth 1):**
1. Carrega mapa principal (`MAP_FILE`), lê transições do JSON
2. Para cada destino único de transição, carrega bundle secundário
3. Imprime `[WorldServer] mapas carregados: [...]` na startup

**Fluxo de troca de mapa:**
1. Cliente detecta tile de transição → envia `ZONE_CHANGE_REQ {to_map, target_x, target_y}`
2. Servidor valida: mapa existe em `_map_bundles`; tile atual do player tem transição para esse mapa
3. `transfer_player()` atualiza `_player_maps`, `MapLocation` do player, `TileMovement`, `Position`
4. Servidor envia `ZONE_CHANGE {map_file, target_x, target_y}`; cliente limpa `known_eids`
5. Cliente executa `_do_transition()` (carrega mapa, reposiciona player)

**AOI filtering por mapa:**
- `_build_update_for_session` lê `_my_map = get_player_map(session_id)`
- `in_aoi(tx, ty, eid)` e `in_aoi_exit(tx, ty, eid)` leem `MapLocation` da entidade — se ausente **ou** mapa diferente, exclui
- Players remotos têm `MapLocation` setado por `transfer_player` — cobertos pelo mesmo filtro

**Persistência:** `map_id` salvo via `_save_character_sync` → coluna `map_id TEXT` na DB.

### 14. Sistema de spawn — SpawnZoneSystem headless

No servidor, `SpawnZoneSystem` é instanciado com `ACTIVATION_RADIUS = 999999`.
Isso desativa o culling por distância de player — todos os spawns são processados.

**Fix `_pending_spawns`:** evita spawn de múltiplos mobs no mesmo tick ao zerar capacidade.
O contador `zone._pending_spawns` (atributo dinâmico adicionado em runtime) é incrementado
a cada spawn tentado e decrementado no próximo ciclo. Sem isso, uma zona poderia spawnar
`max_count` mobs num único tick após respawn.

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
   ├── Rage decay (−5 a cada 3s fora de combate) → rage_events → STATS_UPDATE {rage}
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
| Constantes de rede/mundo | ✅ completo | `shared/constants.py` |
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
| Stats de equipamento/talentos server-autoritativos (PLAYER_STAT_SYNC obsoleto) | ✅ completo | `server/world_server._apply_equipment_modifiers`/`_apply_talent_modifiers` |
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
| Skill Level (Tibia-like) — armas/escudo/defesa/resistências/magic, 0-200, server-autoritativo | ✅ completo | `components.SkillLevels`, `stats_system.py` (xp/bônus), hooks em `server/spell_completion_processor.py`/`systems.py::CombatSystem`/`core_systems.py::StatusEffectSystem`, persistência `skill_levels_json`, UI `skill_level_ui.py` (tecla L) |
| Migração do sistema de quests para server-autoritativo (QuestLog/progresso/entrega) | ✅ completo | `quest_logic.py` (lógica pura), `server/world_server.py::_process_quest_events`, hooks em `server_death_handler.py`/`spell_completion_processor.py`/`skill_processor.py`/`world_server.move_player`/`apply_consumable`/`update_player_equipment`, `server/session.py::_handle_quest_accept`/`_handle_quest_turn_in`, persistência `quests_json`, ver `PROBLEMAS_ARQUITETURA.md` |
| Duelo (contexto PvP por convite, estilo WoW) | ✅ completo, validado em jogo | `server/duel_processor.py`, `client/duel_handlers.py` |
| Party/Grupo + XP compartilhado (Fase E) | ✅ completo, validado em jogo | `server/party_processor.py`, `client/party_handlers.py` |
| Zona PvP (Fase F — "solo=hostil, grupo=exceção") | ✅ completo, validado em jogo (solo, mesmo grupo, e grupo vs grupo) | `server/pvp_zone_processor.py`, `client/pvp_zone_handlers.py` |
| Arena 2x2 + instanciamento (Fase G leva 1) | ✅ completo, não validado em jogo | `server/match_processor.py`, `client/arena_handlers.py` |
| Base compartilhada Arena/BG pro que é genuinamente idêntico no ciclo de vida de partida (janela de aceite, abertura de portão, timeout de resultado) — accept/propose/leave/fila continuam separados de propósito (semântica diverge de verdade) | ✅ completo (escopo parcial deliberado) | `server/instanced_match_processor.py::InstancedMatchMixin`, ver `PROBLEMAS_ARQUITETURA.md` §17 |
| Fila REAL de matchmaking da BG estilo MOBA — 3 tamanhos fixos (2v2/3v3/5v5, jogador escolhe, mesmo padrão de `ARENA_MODES`), não mais fila única adaptativa sem janela de espera | ✅ completo, não validado em jogo (mudança de UI, ver `PROBLEMAS_ARQUITETURA.md` §18) | `server/bg_queue_processor.py::BG_MODES`, `client/bg_queue_handlers.py::BG_MODE_LIST`, modal unificado em `client/arena_handlers.py` |
| Ticks de PvP (fila/pending/results-timeout de Arena e BG) rodam 1x/s em vez de 30x/s — nenhum depende de precisão de frame | ✅ completo | `server/world_server.py::_tick` (`self.tick_count % TICK_RATE == 0`), ver `PROBLEMAS_ARQUITETURA.md` §18 |
| Arena 3x3, campos de batalha 5x5 (torres/bandeira/base), matchmaking solo real, ranking | 🔲 pendente | próximas levas da Fase G |
| Instâncias de dungeon/raid PvE | 🔲 pendente | reaproveita o instanciamento genérico da Fase G (`_load_instance`/`_unload_instance`) |
| Client-side prediction de movimento | 🔲 pendente | `client/` |
| Validação de nome de personagem (formato + unicidade global) + gerador de nome sugerido | ✅ completo, não validado em jogo | `shared/character_names.py`, `server/auth.py`, `client/ui/char_creation_screen.py` |
| Modo offline (`if self._net`/`_is_online` alterando lógica de gameplay) removido de `ui/systems.py`/`ui/spell_system.py`/`game.py` — servidor é sempre o único caminho | ✅ completo | `ui/systems.py::_use_skill_visual_only` era a última função com branches `_is_online` vivas (fechado 10/08/2026); ver `PROBLEMAS_ARQUITETURA.md` §13/§16 |
| Skill com projétil visual (gate de LOS/parede) — campo de catálogo, não hardcode de skill_id | ✅ completo | `content/skill_config.py::SKILL_CATALOG[...]["has_projectile"]`, lido em `ui/systems.py::_use_skill_visual_only` |
| Construção de `Channeling` (canalização) centralizada — skill nova só preenche `params` no catálogo, nunca constrói `Channeling(...)` na mão | ✅ completo | `engine/core_systems.py::build_channeling_from_skill(skill, x, y)` |
| Sistema de GM server-autoritativo (F12: nível/ouro/item viram pedido real ao servidor, não mutação local) | ✅ completo | `accounts.is_gm` (`server/auth.py`) + `GM_LEVELUP`/`GM_ADD_GOLD`/`GM_ADD_ITEM` (`shared/messages.py`) + `server/session.py`; concedido via `python -m server.grant_gm <username>` |
| Imunidade bloqueada (dano ou efeito) sempre mostra "Imune" no floating text — nunca falha silenciosamente | ✅ completo | dano: `engine/world_systems.py::_emit_avoidance_feedback`; efeito de controle: `engine/core_systems.py::apply_effect` |
| Imunidade a efeito de controle (stun/sleep/fear/root/polymorph/disoriented/slow, NUNCA dano) é o status effect `"cc_immune"` — qualquer skill concede via `apply_effect(world, eid, "cc_immune", duration=X)`, nenhum flag bespoke | ✅ completo | `content/status_effects_data.py` (catálogo) + `engine/core_systems.py::apply_effect` (guard genérico via `blocks_move`/`blocks_act`); usado por `engine/skill_handlers.py::_skill_fatiador_de_corpos` |
| Mixin `SkillHandlers` (todos os `_skill_*`/`_talent_*`) mora em `engine/` — servidor não importa mais `ui.systems`/pygame só pra reusar os handlers de skill | ✅ completo | `engine/skill_handlers.py` (`SkillHandlers` + `HeadlessSkillHandler`, usado por `server/world_server.py`); `ui/systems.py::SkillSystem(System, SkillHandlers)` herda a mesma mixin do lado cliente |
| Dano mágico fora do fluxo de `spell_completion_processor` (cliente offline + handlers de skill com resolução inline, ex: cone de Pirofagia) | ✅ completo | `engine/core_systems.py::apply_magic_damage_shared` |
| Outcome (immune/evade/etc.) reportado por ALVO em skill AOE sem `tid` único (ex: Pirofagia) — não mais 1 valor compartilhado pro cast inteiro | ✅ completo | `engine/core_systems.py::LAST_DAMAGE_OUTCOMES` (populado por `apply_damage_core`), lido em `server/skill_processor.py` |
| ESC fecha os 5 modais de PvP (fila de Arena+BG, aceite de Arena+BG, resultado de Arena+BG) — antes nenhum estava no registro central de modais | ✅ completo | `client/modal_stack_handlers.py::_modal_registry()`, ver `PROBLEMAS_ARQUITETURA.md` §19 |
| `item_id` estável (identidade de item, débito C2) — inventário/equipamento/loja/loot de quest/munição de aljava/barra de consumíveis migrados; `name` vira só exibição | ✅ completo (parcial deliberado) | `engine/components.py::Item`, `content/item_table.py`/`crafting_data.py`/`quests_data.py`, `server/world_server.py`, `client/save_sync_handlers.py`/`network_handlers.py`, ver `PROBLEMAS_ARQUITETURA.md` §20 |
| Equipar/desequipar por intenção (débito A4) — `EQUIP_SYNC` de estado completo substituído por `EQUIP_ITEM {inv_index}`/`UNEQUIP_ITEM {slot}`; servidor lê o item de verdade no Inventory ao vivo por posição, nunca confia em item mandado pelo cliente | ✅ completo (parcial deliberado) | `server/world_server.py::equip_item_from_inventory`/`unequip_item_slot`, `server/session.py`, `client/inventory_handlers.py`, `client/save_sync_handlers.py`, ver `PROBLEMAS_ARQUITETURA.md` §21 |
| Forjar/reciclar autoritativo no servidor (débito A4) — `CRAFT_REQUEST {recipe_id}`/`RECYCLE_REQUEST {inv_index}`; servidor confere ouro/material reais e produz/credita ele mesmo (antes 100% local, ouro "gasto" voltava sozinho no relog) | ✅ completo | `server/world_server.py::craft_item`/`recycle_item`, `server/session.py`, `ui/crafting_system.py`, ver `PROBLEMAS_ARQUITETURA.md` §24 |
| Aprender receita por pergaminho — `apply_consumable` aprende de verdade na `LearnedRecipes` ao vivo + persiste no banco (`learned_recipes_json`, coluna nova); antes não existia esse componente nem persistência no personagem do servidor | ✅ completo | `server/world_server.py::apply_consumable`/`spawn_player`/`get_player_learned_recipes_data`, `server/auth.py`, `server/session.py::_build_save_merge`, ver `PROBLEMAS_ARQUITETURA.md` §24 |
| Saque (loot) autoritativo no servidor (débito A4, última peça) — `LOOT_REQUEST {item_id}` (era `item_name`); servidor credita direto no Inventory ao vivo antes de responder (empilha automático, item sem espaço fica no corpse); progresso de quest `collect_item` roda direto no fluxo de saque, sem INV_SYNC | ✅ completo | `server/loot_processor.py::request_loot`/`_grant_loot_items_to_inventory`, `server/session.py::_handle_loot_request`, `client/network_handlers.py`, ver `PROBLEMAS_ARQUITETURA.md` §25 |
| Trava de integridade — dois item_ids diferentes com o mesmo nome de exibição recusam o boot do servidor | ✅ completo | `server/world_server.py::_check_item_name_collisions`, ver `PROBLEMAS_ARQUITETURA.md` §25 |
| Fatiar quantidade ao ofertar item no trade — `TRADE_OFFER_ITEM {inv_index, quantity}` (quantity opcional); clique direito simples oferece 1, Shift+clique abre modal de quantidade (mesmo padrão visual da compra em loja) | ✅ completo | `server/trade_processor.py::add_trade_item`, `client/trade_handlers.py`, `server/session.py::_handle_trade_offer_item`, ver `PROBLEMAS_ARQUITETURA.md` §26 |
| Ícone de item por `item_id` (não mais nome de exibição) — `IconManager.item_key` prefere item_id, fallback por nome só quando vazio; 21 arquivos de ícone renomeados | ✅ completo | `ui/icon_manager.py::item_key`/`item_key_by_name`, `assets/icons/item_<item_id>.png`, ver `PROBLEMAS_ARQUITETURA.md` §26 |
| Cache de parse de mapa — `load_map_csv` cacheia por filepath (sempre devolve cópia isolada), evita reprocessar CSV estático a cada `WorldServer()` — suíte de testes ~3x mais rápida (296s→105s), sem efeito em produção | ✅ completo | `engine/map_loader.py::load_map_csv`/`_MAP_CSV_CACHE`, ver `PROBLEMAS_ARQUITETURA.md` §27 |
| Fase 4.5 — log de performance: p95/p99 por rótulo, breakdown "TOP:" sem corte em 100ms, `loop.slow_callback_duration` (avisos via logger `asyncio`→`server/log.py`), trace sob demanda formato Chrome Trace/Perfetto | ✅ completo (item "breakdown dentro de 1 sistema" adiado, ver §28; superado pela Fase 4.7) | `server/main.py`, `server/log.py`, ver `PROBLEMAS_ARQUITETURA.md` §28 |
| Fase 4.6 — escala de servidor: pré-filtro de IA (`_active_mobs_this_tick`) vira índice espacial (`SpatialHash`) em vez de comparar cada mob contra cada player — O(mobs×players)→O(mobs+players); ganho real medido em players espalhados (44% com 60), limitação conhecida em hotspot aglomerado (débito documentado). 2ª camada (mesmo dia, playtest real): throttle da varredura completa (elegibilidade inteira, não só o índice) a cada 3 ticks — corta o "piso" de tocar todo mob todo tick em ~70%, mesmo com 1 player só | ✅ completo | `engine/world_systems.py::EnemyAISystem._active_mobs_this_tick`, ver `PROBLEMAS_ARQUITETURA.md` §29 |
| Fase 4.7 — log de performance vira hierarquia real: `_perf_mark(label, t0)` (dict flat por nome, misturava soma-de-pai com soma-de-filho na mesma linha) substituído por `_perf_push(label)`/`_perf_pop()` (pilha real, chaveia por CAMINHO completo/tupla). Relatório periódico e alerta de "tick lento" (agora "caminho crítico" — desce sempre pelo filho mais caro até a folha) vêm da mesma árvore; trace JSON ganhou timestamp real de início por seção + `tid` único (single-thread), corrigindo as "barras paralelas soltas" da Fase 4.5. `_collect_deltas`/`aoi_collect` ganhou sub-marks + diagnóstico de `gc.get_count()` pra investigar picos ambientais isolados | ✅ completo | `server/world_server.py::_perf_push`/`_perf_pop`/`_perf_critical_path`/`_render_perf_tree`/`_dump_perf_trace`, `engine/world_systems.py::EnemyAISystem` (`perf_push_fn`/`perf_pop_fn`), ver `PROBLEMAS_ARQUITETURA.md` §30 |
| Índice canônico de combatentes por mapa (`_combatants_by_map`) — achado real de teste de carga (100 players/8 mapas simultâneos) usando a árvore da Fase 4.7: `EnemyAISystem.update()` reconstruía `_all_combatants_cache`/`_npc_combatants_cache` varrendo TODOS os combatentes do MUNDO INTEIRO sem filtro de mapa na query, repetido 1x por bundle por tick (causa de um pico real de 152.7ms) — mesma classe de bug já corrigida antes pra `_players_by_map`/`_mobs_by_map`, agora fechada também pra combatentes | ✅ completo | `server/world_server.py::_tick` (`_combatants_by_map`), `engine/world_systems.py::_combatants_on_map`/`EnemyAISystem.update`, ver `PROBLEMAS_ARQUITETURA.md` §31 |
| "Fecha o buraco" — 7 marks novos em `WorldServer._tick()` (`pre_tick_snapshots`, `index_build`, `regen_and_status_ticks`, `skill_followup_ticks`, `death_handling` ampliado, `store_snapshot`, `on_tick_callbacks`) cobrindo blocos que rodavam soltos desde antes da Fase 4.7 — `nao_instrumentado` caiu de 28-42% do tick pra ~1% consistente (validado em teste de carga com 100 players em combate real, mob/PvP simultâneo) | ✅ completo | `server/world_server.py::_tick`, ver `PROBLEMAS_ARQUITETURA.md` §32 |
| Ranking de consumo acumulado (top 10) + correção dos #1/#2/#3 — `sys:TileValidationSystem`/`EnemyAISystem._get_occupied_tiles`/`MinionSystem._get_occupied_tiles` faziam scan do mundo inteiro sem filtro de mapa (mesma causa dos #1/#2; #3 pior, 1x por MINION). Índice canônico `_tile_movement_by_map` fecha #1/#2 direto; #3 usa técnica diferente (reservation table / cooperative pathfinding, pesquisada) por precisar de frescor intra-tick — reduziu #1 em 93%, #2 em 97%, tempo real de CPU do teste de carga em 42% | ✅ completo (#1/#2/#3 fechados; #4-#10 do ranking ainda não atacados) | `server/world_server.py::_tick` (`_tile_movement_by_map`), `engine/world_systems.py::_tile_movement_on_map`/`TileValidationSystem.update`/`EnemyAISystem._get_occupied_tiles`/`MinionSystem._get_occupied_tiles`/`_reserve_tile`, ver `PROBLEMAS_ARQUITETURA.md` §34/§35 |
| Unificação de `index_build` — 4 `get_entities_with()` separados (players/mobs/combatentes/tile-movement) viraram 1 só (base `TileMovement`) + checagem de presença de componente por entidade (técnica equivalente, escala reduzida, ao "groups" do EnTT) — 24% de redução no item, mais 7.7% de CPU real no teste de carga (em cima do §35) | ✅ completo | `server/world_server.py::_tick`, ver `PROBLEMAS_ARQUITETURA.md` §36 |
| Bug de correção real — `TileMovementSystem.update()` (roda globalmente, sem `map_filter`) pegava "o primeiro Tilemap" do mundo pra calcular elevação/transição ao terminar um movimento — mesma classe de bug já documentada no CLAUDE.md ("nunca pegar o primeiro Tilemap"), sem passar pelo `_svc_resolver` já usado por `is_tile_walkable`. Com 8 mapas ativos, entidades fora do mapa "sortudo" calculavam elevação contra a matriz errada, silenciosamente. Varredura confirmou 2 outras ocorrências do padrão, ambas código morto (nunca instanciado/chamado) — não mexidas | ✅ completo | `engine/world_systems.py::TileMovementSystem.update`, ver `PROBLEMAS_ARQUITETURA.md` §37 |
| Ranking de consumo acumulado congelado como referência — top 10 + honoráveis com status por item (✅ otimizado / 🔍 investigado sem bug / ⬜ não investigado) e nota do que já se sabe de cada um, pra retomar sem reinvestigar do zero | 📋 referência (não é feature — 3 itens fechados #1/#2/#8-parcial do índice antigo, #2/tile_movement no piso da técnica atual, 6+ itens ainda não investigados) | ver `PROBLEMAS_ARQUITETURA.md` §38 (tabela completa) |
| Bug real do talento Reciclagem — flecha recuperada sem `item_id` (montada à mão em `server_death_handler.py`, fora do catálogo) explicava os 2 sintomas relatados: "flecha não aparece no loot" (nome não bate no catálogo) e "Já foi saqueado" (nome bate, mas item_id reconstruído no cliente diverge do que o servidor guardou). `_item_factory_by_id`/`_item_factory_by_name` migraram de `WorldServer` (só servidor) pra `content/item_table.py` (compartilhado) — cliente ganhou `_resolve_loot_item()` preferindo item_id, mesmo padrão que o servidor já usava | ✅ completo | `server/server_death_handler.py`, `content/item_table.py::resolve_item_by_id`/`resolve_item_by_name`, `client/network_handlers.py::_resolve_loot_item`, ver `PROBLEMAS_ARQUITETURA.md` §39 |
| Bug real na BG — corpo do player morto recuperava HP sozinho (às vezes minion voltava a atacá-lo). Causa: level-up de progressão normalizada de instância (`_process_instance_levelup`) curava `current_hp` pro máximo incondicionalmente, mesmo pra quem já morreu e está esperando o timer de respawn (XP de proximidade de kill continua chegando normal enquanto morto) — como todo check de alvo válido do jogo olha só `current_hp<=0` (nunca `GhostState.is_dead`), o corpo virava alvo atacável de novo. Mesmo defeito espelhado no caminho de XP real (fora de instância). Fix pontual only, aprovado pelo usuário — redesenho "espírito na base + corpo 100% visual" (pedido ideal do usuário) fica pra decisão futura | ✅ completo (fix pontual); redesenho maior não iniciado | `server/instance_progression.py::_process_instance_levelup`, `server/world_server.py` (bloco `death_handling`), ver `PROBLEMAS_ARQUITETURA.md` §40 |
| Bug real — Tiro Repulsivo empurrava torres do lugar. Usuário propôs remover `TileMovement` da torre inteira; sinalizado que isso também tiraria a torre dos índices de ocupação de tile (colisão de minion/mob) — corrigido no ponto certo em vez disso: `_server_tiro_repulsivo` (única fonte de knockback do jogo) agora pula empurrão+stun quando o alvo tem `Tower`, mesma convenção já usada pra CC ("torre não é um ser vivo, só dano") | ✅ completo | `server/spell_completion_processor.py::_server_tiro_repulsivo`, ver `PROBLEMAS_ARQUITETURA.md` §41 |
| Sprite real de torre (em vez do retângulo colorido) — `TOWER_TABLE` ganhou campo opcional `sprite_id` (reaproveita o catálogo de sprite de objeto de mapa já usado por harvestable, `ui/systems.py::RenderSystem`/`ui/tile_sprite_manager.py`, zero mecanismo novo); propagado servidor→cliente pelo mesmo payload de spawn de mob (`_build_mob_spawn_payload`/`_spawn_remote_mob`), mesmo padrão já usado por `color` | ✅ completo | `content/tower_definitions.py` (`sprite_id`), `engine/entity_factory.py::create_tower`, `server/world_server.py::_build_mob_spawn_payload`, `client/remote_entity_handlers.py::_spawn_remote_mob` |
| Colisão real de 2 tiles pra torre (sprite de 64×128px, colisão batia só com 1 tile antes) — `entity_footprint_tiles()` novo (ponto único de verdade) deriva a pegada do PRÓPRIO catálogo do sprite (`get_collision_offsets`), sem campo redundante; detecta torre via `EntityIdentity.mob_key` (não `Tower`, que o espelho do cliente nunca tem) — mudança de arquitetura real (nenhuma entidade do jogo ocupava mais de 1 tile antes), decidida com o usuário via plano formal aprovado. + barra de HP ancorada no topo do sprite real, tanto pro mob local (`RenderSystem`) quanto pro REMOTO (`_draw_mob_hp_bars` — passe de desenho separado, só corrigido depois de playtest apontar que o 1º fix não bastava) + traçado de seleção (amarelo) e hit-test de clique (`MouseTargetingSystem._enemy_at_world_pos`) passam a usar a bounding box real do sprite, não o retângulo antigo + `Tower.projectile_origin_offset` (novo, por tipo em `TOWER_TABLE`, default zero) pra escolher de onde o projétil nasce | ✅ completo; offset de projétil sem valor customizado ainda (fica pro usuário decidir depois) | `engine/world_systems.py::entity_footprint_tiles`/`_spawn_attack_projectile`, `ui/systems.py::RenderSystem.render`/`_get_enemy_tiles`/`MouseTargetingSystem._enemy_at_world_pos`, `client/remote_entity_handlers.py::_draw_mob_hp_bars`, `engine/components.py::Tower.projectile_origin_offset`, ver `PROBLEMAS_ARQUITETURA.md` §42 |
| Bug real — vender item na loja nunca removia do Inventory AO VIVO do servidor (só a Wallet era mutada) — item vendido voltava no relog, e pior: `EQUIP_ITEM` (posicional contra esse mesmo Inventory vivo) passava a equipar/rejeitar o item errado depois de QUALQUER venda na mesma sessão, sem precisar relogar. Mesma classe de bug já corrigida pra forja/reciclagem/consumível, faltou aplicar pro lado da venda. Fix: `process_shop_sell` remove/decrementa o item vendido do Inventory vivo (mesmo padrão de `craft_item`), sem bloquear a venda se o item não for encontrado (conservador, evita regressão em quem já depende de venda sempre suceder) | ✅ completo, validado pelo usuário em jogo | `server/world_server.py::process_shop_sell`, ver `PROBLEMAS_ARQUITETURA.md` §43 |
| Bug antigo (relatado antes, nunca resolvido) — HP de mob/torre "regenerava" sozinho durante combate ATIVO. 4 causas encontradas em sequência, as 3 últimas reais e implementadas: (1) cura instantânea ao desistir de perseguir — bug real mas não o sintoma relatado; (2) dispatch por tick sem serialização (`server/session.py::_on_tick`) — gap arquitetural real (bate com Veloren), implementado (opção (c), coalescência), mas sintoma persistiu; (3) `pending_arrow_impacts` era fila FIFO por ALVO (não por flecha) — flecha errada podia consumir o resultado de outra; fix: cada flecha prende o PRÓPRIO resultado em `PlayerProjectile.deferred_result` no momento em que nasce; (4) mesmo com (3) corrigido, o HP da flecha só era aplicado no INSTANTE em que ela chegava visualmente (podia levar vários frames) — se outro ataque (magia/corpo-a-corpo/outra flecha) acertasse o MESMO alvo e aplicasse HP na hora ENQUANTO ela ainda voava, a flecha atrasada sobrescrevia com o `hp_after` antigo dela ao chegar, apagando o progresso mais novo (usuário identificou esse mecanismo sozinho e propôs a correção). Fix (4): HP de flecha aplicado IMEDIATAMENTE na confirmação, igual magia/corpo-a-corpo — só FLT/som continuam esperando o impacto visual; mecanismo `deferred_hp_updates` removido por inteiro | ✅ completo, validado em jogo pelo usuário (13/08/2026) | `client/remote_entity_handlers.py::_spawn_archer_auto_arrow`/`_apply_combat_result`, `ui/spell_system.py::_on_hit`, `engine/components.py::PlayerProjectile.deferred_result`, `server/session.py::_on_tick` (causa 2, também implementada), `debug/hp_rollback_debug.py`, ver `PROBLEMAS_ARQUITETURA.md` §44 / `BENCHMARK_ARQUITETURA.md` §E.1 |
| Atraso configurável da 1ª wave de minion por lane (`first_wave_delay_s`, campo opcional em `minion_lanes`) — independente de `wave_interval_s`; MOBA battleground configurado top=15s/bot=16.5s/mid=18s, mantendo 1.5s de diferença entre lanes em TODAS as waves seguintes (não só a 1ª). Mapa sem o campo cai no fallback antigo (`_LANE_GROUP_STAGGER_S`) | ✅ completo | `engine/map_loader.py` (parse), `server/world_server.py::_activate_minion_lanes`, `maps/moba_battleground_entities.json`, ver `arquitetura/SISTEMAS_ECS.md` |
| Barra de XP dentro da BG mostrava a XP de fora da instância (gap de sync nunca fechado pra `current_xp`/`xp_to_next_level`, mesma classe já corrigida pra level/atributos/talento) — corrigido junto com feedback visual/sonoro novo exclusivo da instância: FLT de XP ganho, som de level-up, FLT de gold ganho (loot automático não tinha nenhum feedback antes) | ✅ completo | `server/instance_progression.py::_push_stats_update`/`grant_instance_xp`/`grant_instance_gold`, `client/network_handlers.py::_handle_msg_stats_update`, ver `PROBLEMAS_ARQUITETURA.md` §45 |
| Minimapa em tela cheia só dentro da BG — mapa inteiro encolhido no frame (sem seguir/centralizar no player), minion/torre/player coloridos por time (`arena_time_a`=azul, `arena_time_b`=vermelho), raio por tipo (minion=1px/torre=2/player=3). Névoa de guerra continua valendo. Clique direito pra mover usa conversor de geometria próprio (`screen_to_tile_fullmap`, corrigido logo após o ship — usava o conversor do modo radar por engano, quebrando o clique) | ✅ completo, validado em jogo (13/08/2026) | `ui/minimap.py::render_fullmap`/`screen_to_tile_fullmap`, `game.py::_collect_bg_minimap_dots`, ver `arquitetura/SISTEMAS_ECS.md` / `PROBLEMAS_ARQUITETURA.md` §46 |
| Stealth de bush estilo MOBA na BG (player/minion dentro de um bush só é visível pra quem tem presença física — própria ou de TIME — dentro do MESMO bush, mesmo dentro do raio normal de visão) — reaproveita o padrão de `_in_pvp_zone` (retângulo por mapa, `bush_zones` em `<mapa>_entities.json`) e o gate central `server/session.py::_can_see` (já usado por Camuflagem/ghost); alvo travado antes da entrada no bush é limpo explicitamente (mesmo motivo da Camuflagem: is_visible sozinho só bloqueia mira nova). Torres excluídas (sem `TileMovement` que muda de tile) | ✅ mecanismo completo e testado; nenhuma bush real pintada em mapa ainda (posicionamento é manual, pelo usuário, no editor) | `server/bush_zone_processor.py`, `server/session.py::_can_see`, `server/world_server.py::_tick_bush_target_clear`, `engine/map_loader.py` (parse de `bush_zones`), ver `PROBLEMAS_ARQUITETURA.md` §47 |
| Monstros de jungle estilo MOBA — normal (hostil aos 2 times, XP/gold no mesmo padrão de Minion) e boss (XP/gold por proximidade restritos ao TIME do golpe final; time inteiro — jogadores + minions vivos — ganha buff temporário; ciclo de buff muda por abate, trava no último ao esgotar a lista). Reaproveita a cadeia de fallback de `_build_combat_entity` (`MOB_TABLE`/`TOWER_TABLE`/`MINION_TABLE` → agora também `JUNGLE_MOB_TABLE`/`JUNGLE_BOSS_TABLE`) — `create_enemy()` sem função de criação nova; Faction `monstros_hostis` já era hostil aos 2 times; buff reaproveita `Modifier`/`add_timed_modifier` (mesmo mecanismo de Canção de Inspiração). Sem drop de item por enquanto | ✅ mecanismo completo e testado; nenhum camp/boss real posicionado no mapa ainda (fica pro usuário decidir onde) | `engine/components.py::JungleMob`, `content/jungle_definitions.py`, `server/world_server.py::_create_jungle_camps`/`_tick_jungle_camp_respawns`/`_grant_jungle_boss_buff`, `server/server_death_handler.py`, ver `PROBLEMAS_ARQUITETURA.md` §48 |
| Bush/árvore/pedra grande passam a bloquear linha de visão (LOS) como parede já bloqueava — `vision_height` do catálogo nunca era escrito em `tile_matrix` pra objeto não-sólido (`create_tilemap` pulava por ser "puramente decorativo" antes de checar bloqueio de visão). `TileType.vision_rect` novo dá pegada de visão INDEPENDENTE de `collision_rect` (bush anda em toda a pegada, bloqueia visão em toda a pegada; árvore mantém tronco estreito sólido, copa inteira bloqueia visão sem virar sólida) | ✅ completo e testado; nenhuma sprite repintada em mapa (bush/árvore/pedra já existentes herdam o bloqueio automaticamente do catálogo) | `engine/tileset.py::TileType.vision_rect`/`get_vision_offsets`, `engine/entity_factory.py::create_tilemap`, ver `PROBLEMAS_ARQUITETURA.md` §49 |
| Fog assimétrico ao ficar no mesmo tile de bush — tiles vizinhos da MESMA bush (também `vision_height>=2`) cortavam a visão do próprio jogador a curta distância, sobrando só o lado onde o aglomerado acaba rápido. Bush bloqueia de fora-pra-dentro, nunca de dentro-pra-fora (confirmado na wiki oficial de LoL) — `local_vision_blob` isola o blob conectado que contém a origem do observador e o torna transparente SÓ pro FOV calculado a partir dali; `is_solid` impede o blob de vazar pra dentro de parede/pedra/tronco encostados na bush | ✅ completo e testado | `ui/fov.py::local_vision_blob`, `ui/systems.py::FogSystem.update`/`_is_blocking_from`, ver `PROBLEMAS_ARQUITETURA.md` §50 |
| Pegada de visão da bush vira "só a base" — `vision_rect="full"` (sprite inteiro) fazia bushes com espaço de grama visível entre si se fundirem num blob só de visão (a maioria já é multi-tile no desenho real, não sobra de transparência). Novo sentinel `"base_row"` conta só a fileira de baixo (largura inteira) — só bush; pedra/árvore ficam com `"full"` (pedra é sólida = sem o problema de auto-bloqueio; copa de árvore fica na metade de CIMA do sprite, "base" apagaria o bloqueio de propósito) | ✅ completo e testado | `engine/tileset.py::_rect_to_tile_offsets` (sentinel `"base_row"`), catálogo `b1`-`b20` em "TX Village Plant", ver `PROBLEMAS_ARQUITETURA.md` §52 |
| Fog "presa" (desatualizada) na tela principal perto de bush pequena — `TileRenderSystem.render_fog()` cacheava o overlay indexado pela janela de tiles da CÂMERA, nunca reconstruindo só porque `FogOfWar.visible`/`explored` mudaram (mesma classe de bug já documentada no CLAUDE.md pro cache de terreno). Minimapa usava outro cache (correto); só a tela principal ficava desatualizada, autocorrigindo quando a câmera cruzava fronteira de tile por acaso. Fix: `FogOfWar.version` (contador no componente, incrementado por `FogSystem`) lido por `TileRenderSystem` — sem System chamar System direto | ✅ completo e testado | `engine/components.py::FogOfWar.version`, `ui/systems.py::FogSystem.update`/`TileRenderSystem.render_fog`, ver `PROBLEMAS_ARQUITETURA.md` §53 / CLAUDE.md |
| Linha de visão de TERRENO entre times, autoritativa no servidor — `_can_see` nunca teve checagem contra `vision_height` (só proximidade + zona de bush, que nunca teve dado real em nenhum mapa, ver §51). Reaproveita a mesma malha (`tile_matrix`, código compartilhado com o cliente) — parede/árvore/pedra/bush escondem o time adversário de verdade agora (não só visualmente), e o próprio tile do alvo conta (parado em cima de bush = escondido, mesmo sem obstáculo intermediário). Vale pra players E minions/mobs, só entre FACTIONS diferentes | ✅ completo e testado | `engine/utils.py::bresenham_line_tiles`, `server/tile_los_processor.py::TileLosProcessorMixin._has_tile_los`, `server/session.py::_can_see`, ver `PROBLEMAS_ARQUITETURA.md` §54 |
| Bush: visão compartilhada dentro da mesma bush (adversários que dividem a mesma bush se veem, estilo LoL — bloqueio de bush não vale entre quem está DENTRO) + "atacar revela" (usar ataque/skill de dentro da bush revela o atacante por 2s pro time adversário, mesmo que continue fisicamente lá — só player, minion não tem `CombatState`). `ui/fov.py` movido pra `engine/fov.py` (ponto único de verdade compartilhado cliente+servidor). Nenhuma das 2 isenções cancela bloqueio de sólido (parede/pedra/tronco) | ✅ completo e testado | `engine/fov.py::local_vision_blob`, `engine/components.py::CombatState.bush_reveal_timer`, `engine/core_systems.py::apply_damage_core`/`BaseCombatStateSystem._tick_bush_reveal_timer`, `server/tile_los_processor.py::_shares_bush_blob`, `shared/constants.py::BUSH_REVEAL_DURATION_S`, ver `PROBLEMAS_ARQUITETURA.md` §55 |
| AOI revalida a PRÓPRIA visão quando o viewer se move, não só quando o alvo se move — `_build_update_for_session` só reagia a "o ALVO se moveu"; sair de uma bush compartilhada nunca invalidava quem tinha ficado "conhecido" de dentro dela (só destravava quando o outro lado andava de novo, por acaso). Novo passo revalida `known_eids` contra `_can_see` completo sempre que o PRÓPRIO player se move nesse tick — cobre bush e qualquer regra futura cuja resposta dependa da posição do viewer | ✅ completo e testado | `server/session.py::_build_update_for_session`, ver `PROBLEMAS_ARQUITETURA.md` §56 |
| Visão de terreno vira universal — vale pra QUALQUER par de entidades (player/mob/npc/minion), mundo aberto E BG, sem filtro de Faction (antes só rodava "entre facções diferentes", e player comum não tem Faction nenhuma fora de contexto de time, então nunca disparava no mundo aberto). Isenção de vegetação bloqueante (bush/copa) sempre a partir da posição do PRÓPRIO viewer (nunca do alvo — mesmo princípio do cliente, `local_vision_blob`) — corrige bug real (player no meio de uma bush grande não via quem tinha saído). Visão compartilhada de time (aliado dentro da bush revela pro time) continua exclusiva de Faction explícita — nunca mundo aberto. Sólido nunca é isento por nenhuma regra | ✅ completo e testado | `server/tile_los_processor.py::_has_tile_los`/`_ally_bush_blobs`, `server/session.py::_can_see`, ver `PROBLEMAS_ARQUITETURA.md` §57 |
| Torre/minion do PRÓPRIO time sempre visíveis (efeito colateral da LOS virar universal em §57: torre/minion passaram a "sumir" atrás de bush/parede pro time deles também, o que nenhum MOBA faz) — bypass direto em `_can_see` quando alvo é `Tower`/`Minion` E viewer/alvo têm a MESMA Faction explícita; adversário continua sujeito à LOS normal. + a varredura genérica de "entidade em AOI ainda não conhecida" (mob/torre/minion/harvestable) nunca chamava `_can_see` (só distância) — torre/minion/mob adversário sem LOS podiam ser "descobertos" só por estar perto; corrigido. Investigação incluiu uma hipótese (aliado revela inimigo pro time inteiro) que se mostrou FALSO POSITIVO — mecanismo já existia (varredura de players separada, sempre ativa) — nenhum código novo shipado pra esse caso | ✅ completo e testado | `server/session.py::_can_see`/`_build_update_for_session`, `tests/test_team_visibility_sweep.py`, ver `PROBLEMAS_ARQUITETURA.md` §58 |
| Visão de time vira UNIÃO de fontes independentes, estilo LoL (usuário reportou de novo: minion/torre/player aliado não compartilhavam visão de um inimigo pro RESTO do time — a exceção antiga, `_ally_bush_blobs`, só isentava a célula de bush que um aliado ocupava fisicamente, sem raio nenhum, e nunca cobria parede) — `_has_tile_los` ganha `ally_centers` (mesma lista, já calculada 1x por tick por `_compute_ally_vision_centers`); tenta o raycast do PRÓPRIO viewer primeiro, senão tenta a partir de CADA aliado (alvo dentro do raio dele + raycast livre a partir dele). Sólido continua bloqueando de forma absoluta em qualquer raycast. `_ally_bush_blobs` removida (subsumida) | ✅ completo e testado | `server/tile_los_processor.py::_has_tile_los`, `server/session.py::_can_see`, `tests/test_tile_los_visibility.py::TestTeamVisionUnion`, ver `PROBLEMAS_ARQUITETURA.md` §59 |

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

**Conceder GM** (menu de debug F12 — nível/ouro/itens de verdade, ver
§34.74.54): `python -m server.grant_gm <username>` (com o servidor
rodando ou parado, mexe direto no banco). Precisa também
`"debug_mode": true` em `config.json` (F12 exige as duas: conta GM E
config local). Revogar: `python -m server.grant_gm <username> --revoke`.
