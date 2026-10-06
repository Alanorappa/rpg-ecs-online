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
PostgreSQL via `asyncpg` (migrado de SQLite em 07/09/2026, ver
PROBLEMAS_ARQUITETURA.md §123 — decisão do usuário de ir pra servidor
dedicado em nuvem). `RPG_DATABASE_URL` aponta pro banco alvo (default de
dev: container Docker local). Sem locks/executor de aplicação — Postgres
serializa escritor-contra-escritor de verdade (MVCC), diferente do
SQLite que só aceita 1 escritor por vez (motivo original de toda a
maquinaria de lock das §113-122, removida na migração). Unicidade de
nome de personagem é um índice único NO BANCO
(`characters_name_lower_idx`), não lock de aplicação — correto mesmo
com múltiplos processos (sharding futuro por mapa/instância).
`data/game.db` (SQLite) preservado só como origem histórica pra
`tools/migrate_sqlite_to_postgres.py`, nunca mais lido pelo servidor.

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

**Infraestrutura de produção (08/09/2026):** servidor dedicado Hetzner
Cloud, Ubuntu 24.04 LTS, plano CPX11 (2 vCPU/2GB, ~US$20/mês —
dimensionado pro estágio atual pré-receita, upgrade de plano é trivial
depois sem perda de dado). IP público `5.161.104.174`. Usuário de
serviço não-root `rpgapp` roda tanto o processo do jogo quanto é dono
do código/venv/logs. `ufw` libera só `22/tcp` (SSH) e `8765/tcp`
(WebSocket do jogo) — Postgres roda só em `localhost`, nunca exposto
à internet. `systemd` (`/etc/systemd/system/rpg-server.service`,
`Restart=always`) reinicia o processo sozinho se cair e sobe no boot —
essa é a "alta disponibilidade" pedida pelo usuário (auto-restart +
rede estável do provedor, não failover multi-servidor, que não faz
sentido no estágio atual).

Deploy é manual, nunca automático: `release_tools/deploy_server.ps1`
empacota `git archive HEAD` (só o que está COMMITADO, nunca
`config.json`/`data/game.db`/`maps/*.csv` do usuário, que ficam sempre
de fora de propósito), envia por `scp` (pipe direto por SSH corrompe o
tar binário no PowerShell — motivo do arquivo temporário no script) e
reinicia o `rpg-server` via SSH. Rodar sempre que um commit precisa
valer no servidor de verdade — nenhum código novo ou mapa editado tem
efeito na nuvem até esse script rodar (não existe hot-reload; mapas
carregam 1x no boot do processo).

Backup: `pg_dump` diário via `cron` (root, 4h da manhã,
`/opt/rpg_ecs_online/backup_db.sh`), retenção de 14 dias, formato
`-Fc` (custom, restaurável com `pg_restore`). Escolhido em vez do
Backups pago da Hetzner (~20% sobre o preço do servidor) por custo —
banco ainda pequeno, sem impacto de performance medido.

### 7. Autenticação
- username + SHA-256(password, client) → sha256(salt+client_hash) no banco (PostgreSQL)
- Session token UUID gerado no LOGIN_OK
- Contas de teste: `teste/123456` (Guerreiro), `teste2/123456` (Mago)
- `server/auth.py` — pool `asyncpg` recriado por `(DATABASE_URL, schema, event loop)`, mesmo princípio que já existia pro lock da era SQLite (loop novo, ex. em teste, precisa de pool novo)

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

### 15. Ferramenta de teste de carga com bots reais — `tools/loadtest/` (04-05/09/2026)

Enxame de bots que conectam via WebSocket de verdade e falam o protocolo
real (`shared/messages.py`) — substitui o teste anterior (nunca
commitado, `PROBLEMAS_ARQUITETURA.md` §31-38) que injetava estado
direto no ECS sem exercitar a camada de rede. Zero mensagem de
protocolo nova, zero mudança de código de jogo — só consome o que já
existe. Ver `tools/loadtest/README.md` pra uso.

- `provision.py` — cria/confirma contas+personagens via protocolo real
  (REGISTER/LOGIN/CREATE_CHARACTER), depois grava `skills_json` direto
  no banco (todas as skills treináveis da classe, `SKILL_ORDER_BY_CLASS`).
  Idempotente.
- `bot.py::LoadBot` — 1 conexão por bot, máquina de estado
  (LOGGING_IN→WANDER/QUEUEING→ENGAGE↔WANDER→arena/BG→RECONNECTING),
  RNG própria por bot. Nunca mira no jogador real — só em mobs (mundo
  aberto) e, DENTRO de uma partida de arena/BG, em outros bots (mundo
  aberto não é PvP livre, precisa de duelo).
- Movimento é **otimista** (mesmo contrato do client real — servidor
  nunca ecoa `MOVE` aceito de volta pro remetente, só corrige quem
  errou via `ENTITY_MOVE`, `server/session.py::_handle_move`): o bot
  aplica `tx/ty` na hora de mandar, não espera confirmação. Sem isso
  a posição local nunca avança e todo passo seguinte recalcula a partir
  da tile velha (bug real achado e corrigido nesta sessão — bot ficava
  "andando no lugar" o teste inteiro).
- `swarm.py` — CLI, rampa de conexão escalonada, proporção
  mundo:arena:bg configurável (`--ratio`, default `68:12:20`),
  `bot_eids: set[int]` compartilhado, encerramento gracioso
  (LOGOUT em cada bot) por Ctrl+C ou `--duration-s`.

### 16. Item de bag aceso sob demanda — luz de item de jogador (23/09/2026)

Pedido do usuário: item de bag (hoje só `lampiao`) que o jogador acende/
apaga clicando com o direito, emitindo luz ao redor de si — diferente do
poste de mapa (`TileType.light_source`, automático, só à noite), este é
sob demanda e funciona a qualquer hora/mapa, inclusive numa caverna
sempre escura. Design discutido e aprovado com o usuário antes de
codar (3 decisões via pergunta direta): visível a jogadores próximos
(não só o dono); só 1 luz ativa por jogador de cada vez (acender outro
item troca, nunca acumula); ícone aceso via atlas companheiro do mesmo
mecanismo já usado pro poste de mapa (`NIGHT_SHEET_BY_FILE`), aplicado
ao atlas de ícones em vez do tileset.

**Catálogo**: `Item.light_source: dict | None` (`engine/components.py`)
— `{"intensity_pct": int, "radius_tiles": int}`, mesma unidade de
`TileType.light_source`. Só itens com essa chave podem ser acesos —
hoje só `content/item_table.py::ITEMS["lampiao"]`. Item novo com luz
futuro só precisa dessa 1 chave, nenhum outro código muda.

**Estado — por JOGADOR, não por item** — `PlayerLightSource`
(`engine/components.py`, MESMO schema usado nos 2 lados): `active`,
`item_id` (qual item fornece a luz), `intensity_pct`, `radius_tiles`
(copiados do catálogo no momento do toggle, nunca recalculados depois).
Servidor autoritativo: `WorldServer.toggle_item_light(session_id,
{item_id})` confirma posse real no `Inventory` ao vivo + que o item tem
`light_source` no catálogo ANTES de ligar — nunca confia em valores do
payload (mesma disciplina de `apply_consumable`, ver
`PROBLEMAS_ARQUITETURA.md` §152). Clicar no MESMO item já aceso apaga
(toggle de verdade); clicar num item DIFERENTE troca.

**Protocolo**: `TOGGLE_ITEM_LIGHT` (C→S, `{item_id}`) →
`PLAYER_LIGHT_UPDATE` (S→C, `{eid, active, item_id, intensity_pct,
radius_tiles}`) — broadcast via `SessionManager._sessions_in_aoi`
(`origin_eid=eid`, que já inclui o PRÓPRIO dono sempre) numa chamada só,
sem precisar de `queue_stats_update` separado pro dono.
`WorldServer.player_light_field(eid)` (mesmo padrão de
`player_faction_field`) inclui o estado atual no payload de
ENTITY_SPAWN — jogador que entra no campo de visão de outro já aceso
não precisa esperar o próximo toggle alheio pra descobrir.

**Render** (client-only): a cada frame, `game.py` injeta 1 entrada por
entidade com `PlayerLightSource.active` (local OU remota, via
`world.get_entities_with`) numa lista SEPARADA de `_world_objs` (nunca a
mesma que alimenta `RenderSystem` — ela não sabe desenhar um objeto sem
sprite), marcada `"player_light": True`. Consumida pelo MESMO loop de
luz que `ui/day_night_system.py::DayNightSystem.render()` já usava pro
poste de mapa — a marca faz esse tipo de luz ignorar o gate de
`is_night()`/mapa fechado que o poste automático respeita (funciona
sempre, inclusive dia pleno mundo aberto).

**Ícone aceso**: `ui/icon_atlas.py::ICON_CHANGE_SHEET_BY_FILE` (mesmo
mecanismo de `NIGHT_SHEET_BY_FILE`, aplicado ao atlas de ícones —
`atlas_items.png` → `atlas_items_change.png`, mesma grade 512×512,
célula por célula) + `IconManager.get_active(name, size)`. A camada
companheira tem alpha VARIÁVEL por pixel (quase transparente nas
bordas, opaco só no núcleo do brilho) — `get_active()` **soma** essa
camada por cima do ícone normal (nunca substitui — bug real corrigido
no mesmo dia: a 1ª versão trocava o ícone inteiro, fazendo a silhueta
do item sumir nas bordas quase transparentes, sobrando só o brilho
isolado "flutuando" sobre o fundo do slot).

Arquivos: `engine/components.py` (`Item.light_source`,
`PlayerLightSource`), `content/item_table.py`/`content/quests_data.py`
(lampião migrado de QUEST_ITEMS pro catálogo geral — deixou de ser
material puro de quest ao ganhar comportamento real), `shared/
messages.py`, `server/session.py`, `server/world_server.py`,
`client/inventory_handlers.py`, `client/network_handlers.py`,
`client/remote_entity_handlers.py`, `ui/day_night_system.py`, `ui/
icon_atlas.py`, `ui/icon_manager.py`, `game.py`. Testes:
`tests/test_item_light.py` (servidor) + `tests/test_client_ui.py`
(render/ícone).

### 17. Sistema de correio — NPC "Caixa de Correio" (25/09/2026)

Pedido do usuário, com wireframes de referência pros 3 modais (inbox,
compor, abrir correspondência). Design discutido e aprovado antes de
codar (`AskUserQuestion`, 25/09/2026): NPC perto da loja/banco
existente; sem expiração de correspondência (fica até o player deletar
manualmente); COM indicador visual de correspondência nova sobre a
caixa de correio.

**Primeira feature do projeto com entrega ASSÍNCRONA de verdade**
(destinatário pode estar offline) — confirmado por investigação prévia
que não existia nenhum padrão parecido no código. Por isso é a única
feature de gameplay com uma tabela de banco NOVA (`mail`,
`server/auth.py::init_db()`) além de `accounts`/`characters` — anexo
(item/gold) fica em CUSTÓDIA na própria linha da tabela (não em
memória, diferente de `TradeSession`) até reclamado, sem coluna de
"lido" separada (derivado de `claimed_items_json`/`gold_claimed`).

**Entrega de anexo reaproveita canais JÁ existentes, decisão
deliberada pra não duplicar canal** — item concedido vai por
`WorldServer.grant_items_to_player` + `INVENTORY_UPDATE` (mesmo
caminho de recompensa de quest); gold por mutação direta de
`Wallet.gold` + `STATS_UPDATE` (mesmo canal de sempre). O indicador de
correspondência nova (`unread_count`) TAMBÉM reaproveita `STATS_UPDATE`
(campo extra `mail_unread`, servidor já encaminha qualquer campo extra
do dict) — nenhum buffer `queue_X`/`consume_X`/`_flush_X` novo, nenhuma
entrada nova em `has_pending` (`SessionManager._on_tick`) precisou ser
adicionada, porque `_pending_stats_updates` já estava lá.

**NPC**: `create_mailbox()` (`engine/entity_factory.py`, mesma receita
de `create_merchant` — combate genérico via `_build_combat_entity`,
facção `civis`) + componente marcador `Mailbox` (vazio — diferente de
`Merchant`/`shop_id`, não existe catálogo por instância; toda caixa dá
acesso ao MESMO inbox do personagem). Cadastro em `_entities.json` →
chave `"mailboxes"` (`engine/map_loader.py`) → `WorldServer.
_create_service_npcs`.

**Protocolo** (`shared/messages.py`): `MAIL_OPEN_INBOX`/`MAIL_INBOX_LIST`,
`MAIL_OPEN_ITEM`/`MAIL_ITEM_DETAIL`, `MAIL_SEND`/`MAIL_SEND_RESULT`,
`MAIL_CLAIM_ITEM`/`MAIL_CLAIM_GOLD`/`MAIL_CLAIM_RESULT`, `MAIL_DELETE`
(resposta é um `MAIL_INBOX_LIST` atualizado, sem mensagem própria).
`MAIL_CLAIM_RESULT` só confirma QUAL slot marcar reclamado na UI — o
valor real do item/gold chega pelos canais reaproveitados acima, no
MESMO handler.

**Servidor autoritativo** (`server/mail_processor.py::
MailProcessorMixin`, mixed em `WorldServer`): `process_mail_send`
valida posse REAL de item(ns)/gold no `Inventory`/`Wallet` AO VIVO do
remetente (tudo-ou-nada — um índice inválido no meio da lista rejeita
a correspondência INTEIRA, nada é removido), resolve destinatário por
nome (`server/auth.py::get_character_id_by_name`), aplica o
auto-preenchimento de assunto vazio (nome do 1º item, ou o valor de
gold se só gold) **no servidor também** — não só client-side, pra não
depender de um cliente modificado preencher certo.
`process_mail_claim_item`/`process_mail_claim_gold` são idempotentes
(2ª tentativa do mesmo slot é recusada — `claimed_items_json`/
`gold_claimed` no banco, com `FOR UPDATE` fechando a corrida de 2
claims simultâneos). Ownership sempre por `char_id` (nunca só
`mail_id`) — outro player não pode abrir/reclamar/deletar
correspondência alheia adivinhando o id.

**Cliente** (`ui/mail_system.py::MailSystem`, mesmo padrão de
`ShopSystem` — clique direito no NPC + `walk_to_npc`/`suppress_chase`
se não-adjacente): 3 telas em `MailUIState.screen`
(`ui/ui_components.py`) — inbox/compose/detail. Composição de nova
correspondência é staging 100% LOCAL (diferente do trade, que manda 1
mensagem por clique porque é negociação em tempo real com outro player
online) — nada sai do Inventory real até "Enviar" (1 `MAIL_SEND` só).
Indicador visual sobre a caixa de correio reusa o pipeline de
`ui/world_labels.py`/`icon_manager` (mesmo de `quest_system.py`), mas a
condição vem de `MailUIState.unread_count` SINCRONIZADO por rede — ao
contrário do marcador de quest (calculado 100% local a partir de
`QuestGiver`+`QuestLog`), "tem correspondência" só existe no banco.

**Nenhuma restrição de item por tipo** (soulbound/quest item
intocável) — confirmado por investigação que isso não existe em nenhum
lugar do projeto (nem no trade); correio segue o mesmo precedente.

Arquivos: `server/auth.py` (tabela `mail` + 8 funções + `unclaim_mail_item`
de rollback), `server/mail_processor.py` (novo), `server/session.py`
(handlers + trigger de login), `server/world_server.py` (mixin +
`_create_service_npcs`), `engine/entity_factory.py`
(`create_mailbox`), `engine/components.py` (`Mailbox`),
`engine/map_loader.py` (`"mailboxes"`), `shared/messages.py`,
`ui/mail_system.py` (novo), `ui/ui_components.py` (`MailUIState`),
`ui/ui_sizes.py`, `client/network_handlers.py`, `client/
modal_stack_handlers.py`, `game.py`, `maps/map_1_entities.json`.
Testes: `tests/test_mail.py` (envio/claim/ownership/idempotência,
servidor). Validação visual dos 3 modais em jogo pendente (usuário).

### 18. Sistema de portais — seleção de destino + cast de 5s (30/09/2026)

Pedido do usuário: reaproveitar o sistema de transição de mapa já
existente (cavernas, `ZONE_CHANGE`), mas com portais onde o jogador
ESCOLHE o destino (em vez de 1 destino fixo por tile) via modal,
seguido de um cast de 5s (tempo pra carregar o mapa destino), com
animação tanto do personagem quanto do próprio tile do portal —
fallback gracioso pra idle/sprite estático enquanto a arte não existe.
Design discutido e aprovado antes de codar (`AskUserQuestion`,
30/09/2026): lista de destinos FIXA e pequena por portal (não uma rede
única com todos os portais do jogo); mover-se durante o cast CANCELA o
teleporte (mesmo padrão de `SpellCast.interruptible`).

**Achado de segurança que motivou o desenho (bloqueante, não
opcional)**: `_handle_zone_change_req` (`ZONE_CHANGE_REQ` existente)
confia cegamente no `to_map`/`target_x`/`target_y` que o cliente manda
— seguro hoje só porque toda transição existente tem exatamente 1
destino FIXO por tile (cliente desonesto só chegaria no MESMO destino
mais cedo). Essa premissa quebra assim que destino vira ESCOLHA do
jogador. Por isso o fluxo de portal **nunca** reusa `ZONE_CHANGE_REQ`
vindo do cliente — o destino é validado contra a lista de destinos
DAQUELE portal específico no momento em que o cast COMEÇA
(server-side), e a teleportação final é disparada pelo PRÓPRIO
SERVIDOR (`transfer_player`/`ZONE_CHANGE` internos, mesmo par que
`ZONE_CHANGE_REQ` já usa) quando o timer dele mesmo completa — nunca
por um pedido novo vindo do cliente nesse fluxo. As transições de
caverna existentes continuam exatamente como estão, fora de escopo.

**Dado de mapa novo**: `"portal_gates"` em `maps/{mapa}_entities.json`
(mesma família de `"transitions"`, `engine/map_loader.py`) — lista de
`{id, x, y, destinations: [{label, target_map, target_x, target_y}]}`.
Nome escolhido pra evitar colisão com `spawn_points["portals"]` (dado
morto pré-existente — tiles `PORTAL_TILE`/char "O" do CSV de terreno,
coletados mas nunca lidos por ninguém).

**Servidor** (`server/portal_processor.py::PortalProcessorMixin`,
mixed em `WorldServer`, mesmo padrão de `DuelProcessorMixin`): estado
em `self._portal_casts: dict[eid, {portal_id, dest_index, elapsed,
duration, map_file}]` + `self._portal_gates_by_map` (carregado em
`_load_map_for`, igual a `"transitions"`). `request_portal_enter(eid,
portal_id, dest_index)` valida em 3 passos — portal existe no mapa
ATUAL do player, player está DE VERDADE no tile do portal
(`TileMovement`), `dest_index` no range da lista DAQUELE portal — só
então cria a entrada. `_tick_portal_casts(dt)` (chamado de
`WorldServer._tick`) incrementa `elapsed`; ao completar, chama
`transfer_player(..., emit_moved_delta=True)` SÍNCRONO e enfileira o
evento de notificação pro dispatch do tick. `move_player()` ganhou um
guard novo (side-effect, não rejeita o MOVE em si — diferente do guard
de dash): `eid in self._portal_casts` → `cancel_portal_cast(eid,
"moved")`.

**Protocolo** (`shared/messages.py`): `PORTAL_ENTER_REQ` (C→S,
`{portal_id, dest_index}`), `PORTAL_CAST_START` (S→C, `{portal_id,
dest_index, duration}` — confirma o cast, cliente já mostrou
localmente ao clicar mas só conta pra valer com a confirmação),
`PORTAL_CAST_CANCEL` (S→C, `{reason}`). Handler
`_handle_portal_enter_req` em `server/session.py::_handlers`; drenos
de `consume_portal_teleport_events()`/`consume_portal_cast_cancel_events()`
em `_dispatch_tick_deltas`, ambos entrando em `has_pending` no mesmo
commit. A teleportação final não é mensagem nova — reusa o
`ZONE_CHANGE` que já existe.

**Cliente** (`client/portal_handlers.py::PortalHandlers`, novo mixin):
`self.portal_tiles` (dict paralelo a `self.transition_tiles`, 100%
local, nunca auto-dispara) detecta chegada no tile e abre o modal de
seleção. Clique num destino manda `PORTAL_ENTER_REQ` e seta estado de
cast OTIMISTA (`self._portal_cast_active`), confirmado por
`PORTAL_CAST_START` (mesmo padrão preditivo de `SpellCast`).
`client/hud_handlers.py::_draw_cast_bar` ganhou um `elif` pro cast de
portal ("Teleportando... Xs"). Registrado em `client/
modal_stack_handlers.py` (ESC fecha o modal de seleção).

**Animação do personagem — 2 estados, "in"/"out" (02/10/2026, arte real
entregue pelo usuário — revisão do design original acima, que previa 1
estado genérico)**: `guerreiro_portal_in.png` (35 frames, SEM direção —
personagem dissolvendo num pilar de luz) toca em quem está CASTANDO;
`guerreiro_portal_out.png` (12 frames, SEM direção — pilar
materializando o personagem) toca em quem acabou de CHEGAR. Arquivo
sem sufixo `_{direction}` é um padrão NOVO — `engine/
character_animation.py::_load_sheet_frames` ganhou fallback: tenta
`{class_id}_{state}_{direction}.png` primeiro (preserva idle/run/
attack/skill, que têm variantes reais), cai pra `{class_id}_{state}.png`
só se o direcional não existir.

- **"portal_in"**: `_handle_msg_portal_cast_start` seta
  `anim_attack_state = "portal_in"` com `anim_attack_frame_ms =
  round(duration_ms / n_frames)` — ESTICA via duração de FRAME, NUNCA
  via `anim_attack_loop_duration_ms` (esse campo faz `get_character_frame`
  repetir o ciclo em LOOP via módulo — certo pra Fatiador de Corpos,
  que gira sem parar; ERRADO aqui, repetiria o dissolver e o
  personagem "piscaria" de volta visível no meio do cast). Com
  `frame_ms` calculado, o ciclo natural (`n_frames × frame_ms`) já bate
  com a duração real do cast — a última frame (quase invisível) é a
  que fica na tela até o `ZONE_CHANGE` trocar de cena.
  **`anim_attack_hold_last_frame=True` (02/10/2026, bug real: "ele
  volta pra idle antes de aparecer no outro portal... é 1 frame, mas é
  perceptível")** — o `frame_ms` calculado é só uma ESTIMATIVA da
  duração real do cast; latência de rede + granularidade de tick do
  servidor sempre deixam uma folga pequena e IMPREVISÍVEL entre "o
  ciclo esticado acabou" e "o `ZONE_CHANGE` realmente chegou". Campo
  novo em `TileMovement` (`engine/components.py`) consultado por
  `resolve_display_state()` (`engine/character_animation.py`): quando
  True e o ciclo expira, SEGURA na última frame (nunca reseta
  `anim_attack_state` sozinho) em vez de cair pro idle/run — quem liga
  isso é responsável por desligar quando o evento real chegar
  (`_trigger_portal_out_if_pending`/`_cancel_portal_cast_local`, e
  `trigger_attack_anim()` também reseta pra False em TODO golpe novo —
  sem isso um ataque normal disparado logo depois herdaria o hold de um
  golpe anterior e ficaria preso na última frame).
- **"portal_out"**: `_trigger_portal_out_if_pending()` (novo, chamado
  por `game.py::_do_transition()` logo após reposicionar o jogador, nos
  2 branches) — duração NATURAL (`anim_attack_frame_ms=0`,
  `anim_attack_loop_duration_ms=0`), toca 1 ciclo e volta pro idle
  sozinho. Só liga se o `ZONE_CHANGE` que chegou veio de um cast de
  portal completo — `client/network_handlers.py::_handle_msg_zone_change`
  captura `_portal_arrival_pending = self._portal_cast_active is not
  None` ANTES de cancelar o cast (único sinal client-side de "motivo" do
  teleporte, o payload não carrega isso) — nunca liga em morte/caverna.
- **Luz azulada do personagem** (mesma técnica da luz do tile, ver
  abaixo): `_get_portal_character_light_source()` deriva a máscara do
  FRAME real sendo desenhado agora no personagem (`resolve_display_state`
  + `get_character_frame`, mesmas chamadas que `RenderSystem` já faz —
  duplicado, barato, mesmo padrão de outras geometrias já duplicadas no
  projeto), só pro jogador LOCAL (sem broadcast nesta rodada — decisão
  de escopo, diferente da luz do tile).

**Animação do tile do portal — EVENTO, não relógio, visível por AOI
(01/10/2026, revisão do design original acima — pedido do usuário
depois de desenhar a arte)**: diferente da 1ª versão (loop ambiente
sempre ligado, nunca chegou a ser usada de verdade), o usuário desenhou
2 folhas de evento — `assets/sprites/portal_activate.png` (17 frames)
e `assets/sprites/portal_deactivate.png` (9 frames), cada frame com o
MESMO w×h do tile estático catalogado (98×96 — ajustou `vp_144` no
catálogo de 112→98 de propósito pra isso fechar com frame width exato:
1666/98=17, 882/98=9). "activate" toca no portal de ORIGEM, ESTICADO
pra caber a duração real do cast (5s); "deactivate" toca 1x no portal
de DESTINO, na duração NATURAL do sheet, quando alguém chega — só se o
destino também for um tile de portal cadastrado (senão não tem o que
animar). Decisão do usuário (`AskUserQuestion`, 01/10/2026): visível
pra QUALQUER jogador perto, não só pra quem está usando o portal —
precisa de broadcast novo, não só estado client-local.

- `engine/tileset.py::PORTAL_ACTIVATE_SHEET_BY_TILE_ID`/
  `PORTAL_DEACTIVATE_SHEET_BY_TILE_ID` (dict `tile_id → nome de
  arquivo`, hoje só `"vp_144"`) — arquivos ficam em `assets/sprites/`
  (convenção de animação de PERSONAGEM, não `assets/tiles/`).
- `engine/portal_anim_state.py` (novo módulo, client-only): dict de
  módulo `(tile_x,tile_y) → {phase, started_ms, duration_ms}` — mesmo
  padrão de `core_systems.register_lethal_interceptor`. Escrito por
  `client/portal_handlers.py` ao receber os 3 eventos de rede abaixo;
  lido tanto por `ui/tile_render_system.py::get_world_objects()`
  (objetos Y-sorted) quanto por `render_static_objects()` (objetos
  `no_ysort=True` — **vp_144, o portal, é um destes**: tem
  `sort_val=0` no catálogo, logo `get_world_objects()` pula ele de
  propósito na linha `if no_ysort: continue` — bug real corrigido
  01/10/2026, ver `PROBLEMAS_ARQUITETURA.md` §221, a 1ª versão só
  cobria o método errado). `ui/tile_sprite_manager.py::get_portal_event_frame(name,
  phase, elapsed_ms, duration_ms)` resolve o frame. Limpo em `game.py::
  _do_transition` (posição de tile não tem relação entre mapas
  diferentes).
- **A animação é uma LUZ/overlay, nunca substitui o sprite (correção
  02/10/2026, pedido do usuário — mesma técnica de `night_overlay_sprite`
  do poste)**: campo novo `"portal_anim_overlay"` no dict de objeto de
  `get_world_objects()`/variável local em `render_static_objects()` —
  `RenderSystem`/`render_static_objects()` desenham o sprite ESTÁTICO
  sempre, com o frame animado BLITADO POR CIMA (mesma posição/tamanho)
  só quando há animação ativa. 1ª versão fazia `sprite = frame`
  (substituição), escondendo o portal atrás da arte da animação.
- **A luz do portal fura a camada de escuridão da noite, com a FORMA
  real do brilho azul desenhado na animação (02/10/2026, 2 rodadas —
  pedido do usuário: "a ideia é de iluminação", depois "não quero que
  o portal como um todo emita luz... queria que essa luz [a do
  desenho] furasse a camada de noite")**: a 1ª versão usava um círculo
  sintético (`_get_light_add_gradient`, raio fixo) centrado no objeto —
  TROCADA por uma máscara derivada do canal ALPHA do próprio FRAME
  ativo da animação (`DayNightSystem._get_portal_frame_light_mask(frame,
  intensity_pct)`, cacheada por `(id(frame), intensity_pct)`): branco ×
  (alpha do pixel / 255) × intensidade — onde a arte é mais opaca, mais
  luz; onde é transparente, nenhuma. `BLEND_RGBA_ADD` NUNCA pondera pela
  alpha de ORIGEM (mesmo achado empírico já documentado pro gradiente de
  lampião) — por isso a intensidade precisa estar ASSADA no RGB aqui,
  nunca um blit direto do frame (que criaria um furo de borda dura, não
  o brilho suave da arte).
  `engine.portal_anim_state::active_positions(now_ms)` lista os tiles
  animando agora; `ui/tile_render_system.py::get_portal_light_sources()`
  resolve o FRAME de verdade (`TILE_SPRITES.get_portal_event_frame`,
  mesma chamada que já desenha a animação visível) + a posição de tela
  EXATA onde ele é desenhado (idêntica à de `portal_anim_overlay` —
  tem que bater pixel a pixel, senão o furo apareceria num lugar
  diferente do brilho visível) — devolve `{"screen_x"/"screen_y"/
  "portal_light_frame"/"intensity_pct"}`, um formato PRÓPRIO (não mais
  reaproveita `"light_source"`/`"player_light"` da luz de item — são
  caminhos de desenho DIFERENTES em `DayNightSystem.render()` agora,
  cada um com seu próprio loop). `_PORTAL_LIGHT_INTENSITY_PCT=90` é a
  única constante do mecanismo que resta (força do efeito; forma/cor
  vêm do desenho). `game.py` injeta essa lista na mesma variável
  SEPARADA de sempre (`_lit_world_objs`, nunca a lista passada pro
  `RenderSystem`).
- **Posição do GATE ≠ posição de RENDER do objeto (bug real corrigido
  01/10/2026, ver `PROBLEMAS_ARQUITETURA.md` §221)** — `portal_gates`
  no JSON marca onde o jogador INTERAGE
  (tile de pé), mas o objeto `vp_144` pode estar plantado num tile
  DIFERENTE do `object_matrix` (nos 2 portais de teste, consistentemente
  a `(-1,+1)` do gate — o sprite ancora no canto onde a arte foi
  desenhada, não necessariamente onde o jogador fica em pé). O servidor
  só conhece a posição do GATE (nunca sabe de sprite/objeto); escrever
  o estado de animação direto nessa posição significava que o renderer
  (que consulta pela posição real do OBJETO) nunca encontrava nada.
  Fix: `client/portal_handlers.py::_find_portal_anim_anchor()` escaneia
  um raio pequeno (`_PORTAL_ANIM_ANCHOR_SEARCH_RADIUS = 2`) ao redor de
  cada gate, no `object_matrix` real, procurando um tile cujo
  `sprite_name` esteja cadastrado em `PORTAL_ACTIVATE_SHEET_BY_TILE_ID`/
  `PORTAL_DEACTIVATE_SHEET_BY_TILE_ID` — resultado cacheado em
  `self._portal_anim_anchor` (`_build_portal_tiles`, roda 1x por carga
  de mapa). Os 3 handlers de rede (`_handle_msg_portal_anim_*`) traduzem
  `(payload["x"], payload["y"])` por esse dict (`_portal_anim_render_pos`)
  ANTES de escrever em `portal_anim_state` — nunca usar o x/y cru do
  payload como chave. Sem objeto nenhum encontrado no raio, cai pro
  próprio gate (fallback gracioso, mesma animação simplesmente nunca
  aparece, sem quebrar nada).
- **Protocolo novo, broadcast por AOI** (`SessionManager._sessions_in_aoi`,
  nunca privado ao dono — diferente de `PORTAL_CAST_START`/`CANCEL`):
  `PORTAL_ANIM_START` (S→C, `{portal_id, x, y, duration}` — 1º caster
  de um portal específico dispara; 2º+ caster simultâneo no MESMO
  portal não reinicia), `PORTAL_ANIM_STOP` (S→C, `{portal_id, x, y}` —
  só quando o ÚLTIMO caster daquele portal desiste ANTES do tempo;
  conclusão NATURAL nunca manda STOP, o cliente já sabia a duração real
  desde o START e encerra sozinho), `PORTAL_ANIM_ARRIVE` (S→C,
  `{portal_id, x, y}` — ao concluir um teleporte cujo destino bate
  exatamente com outro portal cadastrado, `_portal_gate_at_position`).
  Servidor nunca manda sprite/tile_id, só posição — quem resolve qual
  sheet usar é o cliente, a partir do objeto que já desenha ali.
- Bookkeeping server-side (`server/portal_processor.py` +
  `server/world_server.py`): `self._portal_anim_casters: dict[(map_file,
  portal_id), set[eid]]` — controla quando de fato mandar START/STOP
  (só na transição vazio↔não-vazio do set). `cancel_portal_cast` agora
  também é chamado no disconnect (`server/session.py`, ao lado de
  `end_duels_of`) — sem isso, bystanders só veriam a animação parar
  quando a duração original (que não ia mais acontecer) terminasse
  sozinha.

- **Barra de cast não sumia sozinha ao completar (bug real relatado
  02/10/2026)**: `_update_portal_state()` só limpava
  `_portal_cast_active` em 2 casos (movimento local, `PORTAL_CAST_CANCEL`
  do servidor) — a conclusão NATURAL (chegou ao destino) nunca era
  nenhum dos dois, então o `ZONE_CHANGE` teleportava o jogador mas a
  barra ficava órfã na tela até o 1º passo no mapa novo. Fix:
  `client/network_handlers.py::_handle_msg_zone_change` chama
  `_cancel_portal_cast_local()` incondicionalmente pra QUALQUER troca
  de mapa, mesmo padrão dos outros resets que já rodavam ali (resultado
  de BG, contexto de PvP, câmera livre) — idempotente.
- **Modal de seleção abria sozinho ao CHEGAR via teleporte (bug real
  relatado 02/10/2026, consequência direta do fix anterior)**: com a
  barra agora sumindo na hora, sobrou um efeito colateral — os 2
  portais de teste se miram (destino de um é exatamente o tile do
  outro), então chegar NUM portal vindo de OUTRO já abria o modal de
  seleção dele, como se o jogador tivesse ANDADO até lá. Fix:
  `client/portal_handlers.py::_suppress_portal_modal_here()` (novo) —
  chamado por `game.py::_do_transition()` logo após reposicionar o
  jogador em QUALQUER teleporte (morte no mesmo mapa, ou troca de mapa
  de verdade — os 2 branches), marca o tile de chegada como "modal já
  dispensado". `_update_portal_state()` já reseta esse dismissed_key
  sozinho assim que o jogador SAI do tile — só suprime a CHEGADA; sair
  e voltar ANDANDO depois abre normal. **Achado fora de escopo, não
  corrigido**: o branch "mesmo mapa" de `_do_transition()` sempre loga
  "Você morreu e voltou ao ponto de spawn." — mensagem falsa quando o
  motivo é um portal pro mesmo arquivo (ex: Vila Sul→Centro), não morte
  de verdade; avisado ao usuário, decisão de corrigir ou não é dele.

Arquivos: `maps/map_1_entities.json` (`"portal_gates"`, 2 portais de
teste se mirando — `portal_teste_1` em (138,370)→Vila Sul(210,480)/
Caverna Oeste, `portal_teste_2` em (210,480)→Centro(138,370)/Caverna
Oeste), `engine/map_loader.py`, `server/portal_processor.py` (novo),
`server/world_server.py`, `server/session.py`, `shared/messages.py`,
`client/portal_handlers.py` (novo), `engine/portal_anim_state.py`
(novo), `game.py`, `client/hud_handlers.py`, `client/network_handlers.py`,
`client/modal_stack_handlers.py`, `engine/tileset.py`, `ui/
tile_sprite_manager.py`, `ui/tile_render_system.py`, `engine/
character_animation.py` (fallback sem direção), `assets/sprites/
guerreiro_portal_in.png`/`guerreiro_portal_out.png`.
Testes: `tests/test_portal.py` (48 — validação de `request_portal_enter`,
teleporte só ao completar o cast, cancelamento por movimento,
confirmação de que `ZONE_CHANGE_REQ` direto nunca alcança destino de
portal sem passar pela validação nova, broadcast de animação
start/stop/arrive incluindo os casos de 1º/último caster e disconnect,
luz derivada do frame real — tile E personagem —, barra de cast limpa
no ZONE_CHANGE, modal suprimido só na chegada por teleporte, animação
"portal_in"/"portal_out" esticada/natural, chegada pendente só com
cast realmente ativo); `tests/test_character_animation.py` (fallback
sem direção + assets reais de portal_in/out). Validação visual em jogo
pendente (usuário) — animação do personagem, do tile e a luz azulada
(tile + personagem) já têm arte e mecanismo prontos; falta só o
usuário confirmar em jogo esta última rodada (personagem entrando/
saindo + luz do personagem).

**Modal só abre com o jogador PARADO no tile, não de passagem
(02/10/2026)**: `_update_portal_state` (`client/portal_handlers.py`)
abria o modal no 1º frame em que `TileMovement.is_moving` ficava
`False` — isso acontece por 1 frame só entre dois tweens de tile,
mesmo andando sem parar (tecla segurada), então só "passar por cima"
já abria. Fix: `_PORTAL_MODAL_STAND_DELAY_S = 0.5` — o jogador precisa
ficar parado no MESMO tile de portal por 0.5s acumulados antes do
modal abrir (`_portal_stand_key`/`_portal_stand_elapsed`, zerados
sempre que o jogador se move ou troca de tile). 100% client-side, sem
mudança de protocolo. Testes: `tests/test_portal.py::
TestPortalModalStandDelay` (passar de passagem não abre, ficar parado
o suficiente abre, mover no meio da contagem zera, trocar de tile de
portal zera) — revert-to-confirm feito (delay zerado, os 4 testes
falharam como esperado, restaurado).

**Tremor de câmera nos últimos 3s do cast, decaindo na chegada
(02/10/2026, pedido do usuário)**: durante os últimos
`_PORTAL_SHAKE_RAMP_S = 3.0` segundos do cast de 5s, `_portal_shake_
intensity` (0.0-1.0) sobe linear até 1.0; ao chegar no destino (mesmo
instante que liga "portal_out" via `_trigger_portal_out_if_pending`),
decai de volta a 0 em `_PORTAL_SHAKE_FADE_S` (= `_PORTAL_DEACTIVATE_
DURATION_MS/1000`, reusa a MESMA duração da animação natural de
"desativar", nenhum número novo). Cancelamento do cast por movimento
zera o tremor NA HORA, sem fade (decisão confirmada com o usuário —
só chegada de verdade decai suave).

Aplicado SÓ no offset de DESENHO (`game.py`, `self._cam_x`/`_cam_y`,
`_portal_shake_render_offset()`, amplitude `_PORTAL_SHAKE_MAX_
AMPLITUDE_PX = 6.0` px de mundo) — **NUNCA** dentro de `engine.utils.
get_camera_offset()`, que clique/mira/loot/tooltip também usam
(`ui/player_input_system.py`, `ui/loot_system.py`, `ui/skill_system.py`,
etc.) — tremer ali deixaria esses sistemas imprecisos durante o cast/
chegada. `client/tooltip_handlers.py` lê `self._cam_x`/`_cam_y`
diretamente (único outro consumidor fora do próprio render) e por
isso o hover de tooltip também treme de leve junto — efeito colateral
aceito (cosmético, sem mutação de estado).

Testes: `tests/test_portal.py::TestPortalShake` (sem cast fica em
zero, antes da janela de 3s não sobe, sobe linear dentro da janela,
chega a 1.0 no fim do cast, cancelamento zera na hora sem fade,
chegada liga o fade e decai gradualmente até zero, offset de render
fica em zero sem tremor e nunca excede a amplitude máxima) —
revert-to-confirm feito (ramp zerada, os testes de subida/cancelamento
falharam como esperado, restaurado).

**Bug real na 1ª versão (02/10/2026, usuário testou e não viu nada)**:
o tremor somava `_shake_dx/_shake_dy` só em `self._cam_x`/`self._cam_y`
(`game.py`) — mas TODO o passe de mundo (`system.render(cam_x, cam_y)`
do loop genérico, `TileRenderSystem`, `loot_system`, `DASH_TRAIL`, etc.)
lê as variáveis LOCAIS `cam_x`/`cam_y`, nunca os atributos — só
`client/tooltip_handlers.py` (hover) lia `self._cam_x`/`_cam_y`. Os
testes automatizados (`TestPortalShake`) não pegaram porque testam
`_portal_shake_render_offset()`/estado isolados em `client/
portal_handlers.py`, nunca a fiação de `game.py` que aplica o offset
no desenho — esse wiring não tem teste (mesma categoria de "fora do
automatizável" já documentada em várias outras features de render).
Fix: muta `cam_x += _shake_dx; cam_y += _shake_dy` ANTES de qualquer
uso, com `self._cam_x/_cam_y` só espelhando o resultado já somado.

**2º bug real (02/10/2026, usuário testou de novo): tremor não
continuava decaindo na chegada, desaparecia na hora**. Causa:
`client/network_handlers.py::_handle_msg_zone_change` chama
`_cancel_portal_cast_local()` incondicionalmente pra limpar
`_portal_cast_active` em QUALQUER `ZONE_CHANGE` (inclusive a conclusão
NATURAL do cast) — e essa função também zerava
`_portal_shake_intensity` sem condição, ANTES de
`_trigger_portal_out_if_pending()` (chamado só depois, dentro de
`_do_transition()`) conseguir capturar o valor pro fade. Fix:
`_cancel_portal_cast_local(stop_shake: bool = True)` — o cancelamento
por movimento (`_update_portal_state`) continua com o padrão (zera na
hora, decisão já confirmada); `_handle_msg_zone_change` passa
`stop_shake=not self._portal_arrival_pending` — preserva a intensidade
quando a chegada vem de um cast real, deixando
`_trigger_portal_out_if_pending()` capturá-la pro fade; `ZONE_CHANGE`
sem cast ativo (caverna, morte) continua zerando normalmente (no-op
seguro, intensidade já era 0). Teste:
`test_zone_change_de_cast_completo_preserva_tremor_pro_fade` (+
controle `test_zone_change_sem_cast_zera_tremor_normalmente`) —
revert-to-confirm feito (stop_shake forçado True, o teste falhou como
esperado, restaurado).

**SFX de cast (02/10/2026, pedido do usuário, timing calculado por ele
a partir da forma de onda de `assets/sounds/sfx/portal.ogg`)**: toca
aos `_PORTAL_SFX_START_S = 1.317` segundos de cast decorridos
(`client/portal_handlers.py::_update_portal_sfx`, chamado junto da
rampa do tremor). Arquivo tem 6.807s de duração total (confirmado via
`pygame.mixer.Sound.get_length()`); o usuário identificou que o ápice/
estouro do ARQUIVO cai aos 3.683s de reprodução — 1.317+3.683=5.000s =
exatamente a duração do cast, fazendo o estouro coincidir com o
instante da troca de tela. O restante do arquivo (~3.12s) toca
naturalmente já no mapa de destino, sem corte. `SOUNDS.play("portal")`
— carregamento automático, qualquer `.ogg` em `assets/sounds/sfx/`
sem entrada em `_REGISTRY` já é pré-carregado pelo nome do arquivo
(`ui/sound_manager.py::SoundManager.init()`).

Mesmo padrão de "abrupto vs. natural" do tremor: `_cancel_portal_cast_
local`'s `stop_shake` agora TAMBÉM controla o SFX — `True` (cancelamento
por movimento) chama `.stop()` no canal na hora; `False` (conclusão
natural, `_handle_msg_zone_change`) só solta a referência
(`_portal_sfx_channel = None`) sem parar, deixando o canal tocar até o
fim por conta própria. Exigiu 1 mudança pequena e compatível em
`ui/sound_manager.py::SoundManager.play()`: agora devolve o `Channel`
usado (antes `None` sempre) — nenhum chamador existente usava o
retorno. Testes: `tests/test_portal.py::TestPortalSfx` (não toca antes
do tempo, toca ao atingir 1.317s, não toca 2x no mesmo cast, cancelar
por movimento para o canal, conclusão natural NÃO para) —
revert-to-confirm feito (disparo desativado, 3 testes falharam como
esperado, restaurado).

**Animação de personagem passa a ligar só no último 1 segundo, não
mais esticada pelos 5s inteiros (02/10/2026, pedido do usuário — arte
de `guerreiro_portal_in.png`/`guerreiro_portal_out.png` foi refeita
mais curta, 35→8 e 12→8 frames respectivamente)**: `_handle_msg_
portal_cast_start` não liga mais "portal_in" na hora — só registra o
cast. `_update_portal_char_anim` (novo, chamado todo frame junto da
rampa do tremor/SFX) dispara quando `remaining <= _PORTAL_CHAR_ANIM_
START_S = 1.0`, esticando o ciclo pro RESTANTE real do cast nesse
instante (`frame_ms = restante_ms / n_frames`), não mais pra duração
inteira — mesma técnica de sempre, janela menor. Nos segundos
anteriores o personagem fica no idle normal. A luz azulada do
personagem (`_get_portal_character_light_source`, condicionada ao
mesmo `anim_attack_state`) automaticamente também passa a acender só
nesse último segundo — confirmado com o usuário via `AskUserQuestion`
que é o comportamento desejado (antes acendia quase o cast inteiro).
Testes: `tests/test_portal.py::TestPortalCharacterInOutAnimation`/
`TestPortalClientAnimationClock` atualizados pra avançar
`_update_portal_state` até a janela final antes de checar o estado de
animação (6 testes ajustados); `tests/test_character_animation.py::
test_assets_reais_de_portal_in_out_existem_e_tem_frames` atualizado
pro frame count real novo (8/8, antes 35/12) — revert-to-confirm feito
(disparo desativado, 6 testes falharam como esperado, restaurado).

**Som + animação do CASTER broadcast pra observadores (03/10/2026, bug
real relatado pelo usuário: o próprio caster ouvia/via tudo normal,
mas quem estava só OBSERVANDO não ouvia o som nem via a animação
"entrando no portal")** — causa raiz: `PORTAL_CAST_START` (S→C) é
privado de propósito (só o dono do cast), e tanto `_update_portal_sfx`
quanto `_update_portal_char_anim` só liam `self._portal_cast_active`
(estado client-only do PRÓPRIO jogador) — nada no protocolo avisava
quem estava perto.

- **Protocolo novo** (`shared/messages.py`): `PORTAL_CAST_BEGIN` (S→C,
  AOI, `{eid, duration}`) — par de `PORTAL_CAST_START`, broadcast via
  `SessionManager._sessions_in_aoi(tx, ty, map_file, origin_eid=caster)`
  (inclui o próprio caster também, por como `_sessions_in_aoi` sempre
  funciona — o cliente ignora quando `eid == self._my_eid`, mesmo
  princípio de `skip_local_caster` já usado por `SKILL_EFFECT`).
  `PORTAL_CAST_ABORT` (S→C, AOI, `{eid}`) — par de `PORTAL_CAST_CANCEL`,
  mesmo broadcast, avisa quem estava observando que o cast terminou
  antes do tempo. Handler `_handle_portal_enter_req` (`server/
  session.py`) manda os dois junto dos privados já existentes; o dreno
  de `consume_portal_cast_cancel_events()` manda `PORTAL_CAST_ABORT`
  junto do `PORTAL_CAST_CANCEL` privado.
- **Cliente** (`client/portal_handlers.py::PortalHandlers`): novo dict
  `self._remote_portal_casts: dict[eid, {elapsed, duration, sfx_played,
  char_anim_triggered}]`, populado por `_handle_msg_portal_cast_begin`
  e espelhado a cada frame por `_update_remote_portal_casts` (chamado
  de `_update_portal_state`, incondicional — roda mesmo quando o
  jogador LOCAL não está castando nada). SFX positional via
  `SOUNDS.play_random_at(["portal"], ...)` no mesmo instante
  (`_PORTAL_SFX_START_S`); animação via 2 funções de MÓDULO extraídas
  do código que já existia pro caster local — `_begin_portal_in_anim(tm,
  class_id, remaining_s, hold_last_frame)`/`_clear_portal_in_anim(tm)`
  — PONTO ÚNICO reusado por `_update_portal_char_anim`/
  `_cancel_portal_cast_local` (caster) E pelo espelho remoto, nunca
  duplicar essa conta de novo. Diferença intencional: o espelho remoto
  usa `hold_last_frame=False` (diferente do caster local, que usa
  `True`) — sem nenhum `ZONE_CHANGE` chegando nesta ponta pra desligar
  o hold explicitamente, deixar o ciclo esticado terminar sozinho é
  mais seguro que arriscar prender a pose pra sempre (ex: teleporte
  dentro do mesmo mapa, entidade continua visível depois). Entrada do
  dict remove sozinha quando a entidade sai do AOI local (`local_eid is
  None`, sem handler de despawn dedicado) ou ao cruzar a duração cheia.
- **Bug real pego pelo próprio teste antes de ir pra produção**: a
  propriedade `_remote_portal_casts` (padrão `getattr(...) or {}`,
  copiado de `portal_tiles`) quebrava porque dict vazio é "falsy" em
  Python — `or {}` devolvia um dict NOVO descartável a cada acesso
  assim que o dict guardado ficasse vazio, fazendo qualquer mutação em
  lugar (`self._remote_portal_casts[eid] = ...`) desaparecer na hora.
  `portal_tiles` nunca teve esse problema porque é sempre REATRIBUÍDO
  por inteiro, nunca mutado em lugar — propriedade nova que muta
  dict/lista em lugar precisa inicializar e CACHEAR no primeiro acesso
  (`if ... is None: self._x_val = {}`), nunca `or {}`.
- **Fora de escopo, não implementado**: animação de CHEGADA
  ("portal_out") pra quem já está no mapa de destino vendo outro
  jogador materializar — o pedido do usuário foi só sobre o cast
  (lado da origem); quem está no destino hoje só vê o jogador aparecer
  normal, igual qualquer outro `ENTITY_SPAWN`.
- Testes: `tests/test_portal_remote_mirror.py` (10, handlers de rede +
  `_update_remote_portal_casts` com fixture mínima de `PortalHandlers`,
  mesmo padrão de `tests/test_client_ui.py`) — revert-to-confirm feito
  (reintroduzindo o `or {}` quebrado, 4 dos 10 testes falharam
  exatamente como esperado, restaurado). Regressão: `tests/test_portal.py`
  + `tests/test_character_animation.py` + `tests/test_client_ui.py`
  completos continuam passando (os 2 helpers extraídos preservam o
  comportamento do caster local byte a byte).

**Âncora de desenho de personagem passa a ser pela BASE do frame, não
mais um offset fixo a partir do topo (02/10/2026, bug real relatado
pelo usuário)**: `engine/character_animation.py::character_anchor_
offset()` ganhou parâmetro opcional `frame_height` (default
`CHAR_BASELINE_FRAME_H = 80`, as sheets "normais"). Causa: sprites de
portal_in/portal_out foram redesenhadas bem mais altas (80px→640px,
pensadas pra crescer PRA CIMA mantendo os pés no mesmo lugar) — com o
offset antigo (`CHAR_COLLISION_OFFSET_Y + CHAR_COLLISION_SIZE/2 = 48`,
FIXO, sempre medido do topo do frame), a sheet mais alta "crescia pra
baixo" a partir desse topo fixo, enterrando os pés. `dy` agora é
`frame_height - CHAR_FRAME_BOTTOM_MARGIN_PX - CHAR_COLLISION_SIZE/2`
(`CHAR_FRAME_BOTTOM_MARGIN_PX = 16`, derivada: distância constante
entre a base do tile de colisão e a base do frame, a mesma não importa
a altura da sheet) — numa sheet baseline (80px) dá exatamente 48,
idêntico ao valor fixo de sempre (zero mudança pra quem chama sem
argumento). 2 call sites que já têm o Surface do frame em mãos passam
a passar `frame.get_height()`: `ui/render_system.py` (RenderSystem,
blit do personagem local) e `client/portal_handlers.py::
_get_portal_character_light_source` (luz azulada do personagem).
`client/remote_entity_handlers.py` (nameplate de player remoto)
continua usando o default de propósito — fixo, não deve "balançar"
seguindo a sheet de cada animação. Y-sort não foi afetado (já usa
`CHAR_COLLISION_SIZE` fixo, independente de altura de frame, desde
sempre). Testes: `tests/test_character_animation.py::
TestCharacterAnchorOffsetFrameAltoAncoraPelaBase` (frame baseline
mantém comportamento de sempre, frame 8x mais alto ancora pela base,
margem da base é constante entre alturas diferentes) —
revert-to-confirm feito (offset fixo antigo restaurado temporariamente,
2 testes falharam como esperado, restaurado).

### 19. Corpse de loot — sem loot não aparece corpo (02/10/2026)

Pedido do usuário (redesenho, substitui o par antigo "120s mob / 4s
minion / min 15s após qualquer interação"): **se a morte não droppa
item nem moeda nenhuma, nenhum corpse é criado** — nem pra mob normal,
nem pra minion (confirmado explicitamente: minion também some sem
corpo nenhum, apesar de minion nunca ter loot real por design). Quando
há loot (item e/ou moeda), o corpse aparece por **45s**
(`LootProcessorMixin.CORPSE_TIMER_S`); se o player saqueia TUDO de uma
vez (`take="all"`, ou o último item restante), o corpse some **na
hora** (`INSTANT_EXPIRE_S = 0.0`); se saqueia só parte, o timer cai pra
**30s** (`PARTIAL_LOOT_TIMER_S`) a partir daquele momento, nunca os 45s
originais.

**Implementação** (`server/loot_processor.py`):
- `_process_loot_drops(dt)` — logo após extrair `items`/`coins` de cada
  `loot_entry`, `if not items and coins <= 0: continue` — pula tanto a
  criação do dict em `self._corpses[corpse_id]` quanto a notificação
  que dispara `ENTITY_SPAWN(kind="corpse")` pro cliente. Sem essa
  notificação, o cliente nunca sabe que aquele mob tinha um "corpo" —
  não precisou de nenhuma mudança client-side.
- `request_loot(...)` — ao final de qualquer saque, decide o timer
  novo só pelo pool COMPARTILHADO (`items`/`coins` do corpse, não o
  `quest_rolls[player_eid]` pessoal): `INSTANT_EXPIRE_S` se não sobrou
  nada, senão `PARTIAL_LOOT_TIMER_S`.
- Removida a antiga flag `is_minion` do dict de `pending_loot`
  (`server/server_death_handler.py`) — ficou morta: minion nunca tem
  loot de verdade (ouro de instância vai direto pra wallet por outro
  caminho), então sob a regra nova ele cai sempre no caminho "sem loot
  = sem corpse", nunca precisou de um timer dedicado de 4s.

Harvestables (`no_decay=True`, `owner_eid=-1`) são um mecanismo
SEPARADO, sem relação com esta mudança — continuam com seu próprio
ciclo de `empty_timer`/`respawn_s`.

Testes: `tests/test_loot_authoritative.py::TestCorpseTimerSemLootNaoAparece`
(5 — sem loot não cria corpse, item cria corpse de 45s, gold sozinho
também cria corpse, saque total expira na hora, saque parcial dá +30s);
`tests/test_towers.py::test_corpse_de_minion_nao_aparece_mais_sem_loot_nenhum`
(substitui o teste antigo do timer de 4s); `tests/test_instance_progression.py`
ajustado (mob "Lobo" sem loot natural precisou de um coin mockado pra
o teste de reciclagem de flecha continuar tendo um corpse pra inspecionar).

### 20. TLS (wss://) via certificado autoassinado fixado no cliente (02/10/2026)

Usuário quer publicar o jogo jogável (itch.io, download direto do
`.exe` já empacotado por `release_tools/build_client.ps1`) e pediu
revisão de segurança antes. Achado confirmado nesta sessão (já
catalogado em `PROBLEMAS_ARQUITETURA.md` item C1): servidor falava
`ws://` sem criptografia nenhuma — senha (com hash) e todo o resto do
tráfego expostos em texto plano na rede. **Sem domínio** (decisão do
usuário — não quer comprar um): caminho escolhido é certificado
autoassinado com SAN = IP do servidor, fixado ("pinned") no cliente —
funciona porque o cliente é nosso, não um navegador de terceiro, não
precisa de autoridade certificadora pública confiável.

**Certificado**: gerado 1x no servidor via SSH (`openssl req -x509
-newkey rsa:2048 -days 3650 -nodes -subj "/CN=5.161.104.174"
-addext "subjectAltName=IP:5.161.104.174"`), chave privada fica SÓ no
servidor (`/opt/rpg_ecs_online/certs/server.key`, `chmod 600`,
`chown rpgapp`, fora do git pra sempre); o certificado PÚBLICO é
commitado no repo em `certs/server_cert.pem` — é ele que o cliente
embute e usa pra fixar confiança (não é segredo, só prova identidade).
10 anos de validade — sem CA pública envolvida, sem rotação
automática. Limitação conhecida: se o IP do servidor mudar, o
certificado precisa ser regerado e o cliente republicado (aceitável
no estágio atual, sem rotação dinâmica).

**Servidor** (`server/main.py::_build_server_ssl_context(cert_path,
key_path)`): `None` (sem TLS, `ws://` normal) se as 2 env vars
(`RPG_TLS_CERT`/`RPG_TLS_KEY`) vierem vazias — default de dev local,
zero configuração extra. Lidas via `os.environ.get(...)`, mesmo padrão
já usado por `server/auth.py::DATABASE_URL` (`RPG_DATABASE_URL`); em
produção setadas em `/opt/rpg_ecs_online/.env` (`EnvironmentFile=` do
unit `rpg-server.service`, mesmo mecanismo já usado antes pra ligar
flags de debug remotamente, ver `PROBLEMAS_ARQUITETURA.md` ~linha
23520) + `systemctl restart rpg-server`. Se a env var vier preenchida
mas o arquivo não existir, `load_cert_chain` levanta — nunca cai pra
`ws://` em silêncio quando TLS foi pedido explicitamente. `main()`
NÃO ganhou parâmetro novo (as env vars são lidas dentro da função) —
`tests/test_graceful_shutdown.py`/`test_gc_freeze_startup.py` chamam
`main(host, port)` posicionalmente, mudar a assinatura quebraria os
dois.

**Cliente** (`client/network.py`): `NetworkClient.__init__` ganha
`use_tls: bool = False`. `_build_uri(host, port, use_tls)` escolhe
`wss://`/`ws://` (passar `ssl=` numa URI `ws://` é erro na lib
`websockets`). `_build_client_ssl_context(use_tls)` — `None` se
`use_tls=False`; senão `ssl.create_default_context(cafile=...)`
apontando pro certificado bundlado (`certs/server_cert.pem`, resolvido
via `paths.resource_path()` — mesmo ponto único já usado por
assets/maps pra funcionar tanto em dev quanto no `.exe` empacotado).
Certificado ausente com `use_tls=True` levanta — mesmo princípio do
servidor, nunca conecta sem TLS em silêncio.

`use_tls` threaded pelos 3 pontos reais que criam `NetworkClient`:
`main.py::_connect_and_login` (lido de `config.json`), `ui/
login_screen.py::run()` (parâmetro novo), `client/
online_mode_handlers.py::_connect_online` (lido de `config.json`
direto). `config.py::DEFAULTS["use_tls"] = False`.
`release_tools/build_client.ps1` copia `certs/` pro pacote (mesmo
padrão de assets/maps) e escreve `use_tls = ($ServerHost -ne
"localhost")` no `config.json` parcial — build local continua sem
TLS (não existe certificado pra "localhost"), build pro IP de
produção liga automaticamente.

**Fora de escopo**: `tools/loadtest/protocol_client.py` tem seu
próprio `ws://` independente (comentário no arquivo já documenta que
nunca reusa `client/network.py`) — ferramenta local/dev, nunca aponta
pro servidor de produção com TLS fixado pra um IP específico, não
precisa de TLS. Hash de senha com KDF lenta (argon2/bcrypt) continua
adiado de propósito (decisão já confirmada com o usuário antes, ver
`PROBLEMAS_ARQUITETURA.md` item C1) — trocar isso é uma rodada
separada, depois que TLS estiver rodando de verdade.

**Achado novo, catalogado mas fora de escopo**: `MAX_PLAYERS_PER_WORLD`
(`shared/constants.py`) nunca é checado em `SessionManager.on_connect`
— servidor aceita qualquer número de conexões sem teto. Baixo risco na
escala de itch.io, registrado pra uma rodada própria futura.

Testes: `tests/test_tls.py` (`_build_server_ssl_context` sem
cert/key → None, com fixture real → SSLContext, caminho inexistente
com env preenchida → levanta; `_build_client_ssl_context` mesma
tríade do lado cliente; `_build_uri` nas 4 combinações host/use_tls) —
revert-to-confirm feito nas 2 funções "nunca cai em silêncio" (guard
removido, os 2 testes falharam como esperado, restaurado). Prova de
mecanismo ponta-a-ponta feita fora da suíte (sem Postgres local pra
rodar o servidor completo): script isolado em scratchpad usando
`_build_server_ssl_context`/`_build_uri` reais contra um
`websockets.serve()`/`websockets.connect()` mínimo — handshake TLS
real confirmado funcionando nos dois lados antes de tocar em produção.

**Pacote do itch.io (02/10/2026, pedido do usuário, testado no `.exe`
empacotado de verdade)**: `release_tools/build_client.ps1` passou a
escrever também `ui_scale = 1.25` (bug real na 1ª versão — só setava
`scale`, que controla o tamanho da JANELA; `ui_scale`, que controla o
tamanho dos elementos de INTERFACE, é lido direto em `game.py`, FORA
de `config.py::DEFAULTS`, e ficou de fora — usuário testou o `.exe` e
confirmou que a interface continuava em 1x mesmo com `scale: 1.25`
salvo) e `music_volume`/`sfx_volume = 0.1`. **F10 (God Mode) e F11
(overlay de performance) agora exigem `self.is_gm`** (`game.py`,
mesmo padrão já usado por F12) — antes abriam pra qualquer jogador
(as ações de GM em si já eram rejeitadas pelo servidor sem
`session.is_gm`, mas o painel ficava visível pra qualquer um).
`-ServerHost` do build script precisa ser passado EXPLICITAMENTE
quando o build não é feito na própria máquina do servidor — sem ele,
o script descobre "o IP público de quem está rodando o build" via
`api.ipify.org`, não o IP do servidor (achado real: 1ª tentativa sem
o parâmetro gravou o IP da máquina de dev, não o do Hetzner).

### 21. Notificação por e-mail de eventos de conta (02/10/2026)

Pedido do usuário, pensando no lançamento no itch.io: avisar por
e-mail quando (a) uma conta nova é criada, (b) um personagem novo é
criado, (c) alguém loga. `server/notify.py` (novo) — ponto único
`notify_async(subject, body)`, chamado nos 3 pontos de sucesso já
existentes em `server/session.py` (`_handle_register`/`_handle_login`/
`_handle_create_character`, logo depois de confirmar que a ação deu
certo, nunca antes).

Dispara via `smtplib` (stdlib, SMTP+TLS — sem dependência nova) dentro
de `loop.run_in_executor()` — `smtplib` é bloqueante, nunca pode rodar
direto numa coroutine (travaria o event loop single-thread pra TODOS
os players conectados enquanto o SMTP responde, mesma classe de risco
já documentada pra qualquer I/O bloqueante no projeto). Task
registrada em `_pending_tasks` (set de módulo, mesmo motivo já
catalogado em `PROBLEMAS_ARQUITETURA.md` §161 — sem referência forte o
GC pode coletar a task no meio do envio) e qualquer falha de envio
(SMTP fora do ar, credencial errada) é só logada, NUNCA propaga pro
chamador — e-mail é sempre side-effect, nunca pode derrubar/atrasar um
registro, login ou criação de personagem real.

Configurado via env vars (`RPG_SMTP_HOST`/`RPG_SMTP_PORT`/
`RPG_SMTP_USER`/`RPG_SMTP_PASS`/`RPG_NOTIFY_EMAIL_TO`), lidas DENTRO
das funções (nunca módulo-level, mesmo motivo do TLS — testável sem
reimportar o módulo) — mesmo padrão já usado por `RPG_DATABASE_URL`/
`RPG_TLS_CERT`, setadas em `/opt/rpg_ecs_online/.env` + `systemctl
restart rpg-server`. Sem elas (dev local), `notify_async` é no-op
silencioso. Remetente: Gmail do usuário com senha de app (sem TLS
próprio novo — o Gmail já fala TLS via `starttls()`).

Testes: `tests/test_notify.py` (`_smtp_config`/`_configured` com
env vazia/completa/faltando 1 chave; `_send_email_sync` monta e manda
via `smtplib.SMTP` mockado; `notify_async` não cria task sem
configuração, cria e executa quando configurado, falha de envio nunca
propaga) — revert-to-confirm feito (try/except removido do `_run`
interno, o teste de falha-nunca-propaga falhou como esperado,
restaurado).

### 22. Sussurro por nome ("/w Nome mensagem") + clicar no nome responde (02/10/2026)

Pedido do usuário: mandar mensagem pra um player pelo nome, sem exigir
mesmo mapa/lugar — mesmo comando do WoW (`/w`). Seguro buscar por nome
porque nome de personagem é ÚNICO no banco inteiro desde
`characters_name_lower_idx` (`server/auth.py`) — diferente do
comentário já existente em `_handle_chat` sobre chat normal, que é
sobre contas LEGADAS de antes dessa constraint.

**Servidor** (`server/session.py::_handle_chat`, novo ramo
`channel == "whisper"`): busca uma sessão AUTENTICADA cujo
`display_name` bate (case-insensitive) com `target_name` do payload,
entre TODAS as sessões conectadas (`self._sessions.values()`) — nunca
passa por AOI/mapa, funciona com o alvo em qualquer lugar/instância.
Achou → manda `CHAT_MESSAGE` pro destino (`channel: "whisper"`, cor
azul clara `[150,210,255]`, pedido do usuário) + um ECO de volta pro
remetente com campo extra `"to": <nome_do_destino>` (sinaliza "esta é
a cópia de SAÍDA" pro cliente montar o rótulo certo). Não achou →
aviso de "Sistema" só pro remetente ("X não está online"). **Sem fila
offline de propósito** (decisão do usuário) — isso é papel do sistema
de correio, mecanismo separado, não misturado aqui.

**Cliente** — comando (`client/chat_handlers.py::
_try_handle_whisper_chat_command`): `/w Nome mensagem` (nome é sempre
1 palavra, `shared/character_names.py` não permite espaço) vira
`CHAT_SEND {text, channel:"whisper", target_name}`; sem mensagem
mostra aviso de uso 100% local, nunca manda payload incompleto.

**Exibição** (decisão do usuário: cai na aba LOCAL mesmo, não ganha
aba própria): `client/network_handlers.py::_handle_msg_chat_message`
monta um rótulo diferente dependendo do lado — "`<sender>` sussurra"
pra quem recebe, "para `<to>`" pro eco de volta (03/10/2026, pedido do
usuário — "Você sussurra para X" ficava grande demais no chat) — e
grava junto um `reply_target` (quem um clique no nome deve
sussurrar), tudo numa tupla `(label, text, color, reply_target)`
(antes era só `(sender, text, color)`) guardada em `_chat_local`/
`_chat_world`. Mensagem normal (não-whisper): `reply_target` = o
próprio sender (ou `None` pra "Sistema", nunca clicável).

**Clicar no nome responde** (pedido do usuário, estilo WoW — funciona
pra QUALQUER mensagem com remetente claro, não só sussurro recebido,
já que o mecanismo é o mesmo sem custo extra): `client/
chat_handlers.py::_chat_tab_lines` quebra cada entrada em linhas
(wrap) e anota, só na 1ª linha quebrada de cada entrada, a LARGURA em
pixels do trecho antes do primeiro `": "` (o "nome" exibido) — nunca
nas linhas de continuação de mensagem longa, que não repetem o nome.
`_try_handle_chat_name_click` (novo, chamado por `_handle_chat_click`
quando o clique cai dentro de `msg_area`) refaz a MESMA matemática de
posicionamento de `_draw_chat_log` (linha visível a partir de scroll
atual) pra achar a entrada clicada, confere se o X do clique cai
dentro da largura anotada, e — se não for o PRÓPRIO nome do jogador
(`CharacterStats.name`, guard contra sussurrar pra si mesmo) — abre o
campo de chat com `/w <reply_target> ` pré-preenchido.

**Modo FIXO (03/10/2026, pedido do usuário logo depois, mesma ideia do
WoW — generalizado em seguida pro chat de grupo também, ver §23)**:
`self._chat_sticky_mode` (`game.py`, novo campo, default `None`) —
`None` = chat normal (local/mundial pela aba); `("whisper", Nome)` ou
`("party", None)` faz qualquer texto PLANO (sem `/comando` nenhum)
digitado depois continuar indo pro MESMO alvo/grupo, sem precisar
repetir o comando em toda mensagem de uma conversa. `/w Nome` grava
`("whisper", Nome)`; outro `/w OutroNome` troca; `/s` (sozinho) limpa
o modo e confirma localmente "Voltou ao chat normal"; `/s mensagem`
limpa E já manda essa mensagem no canal normal (local/mundial pela
aba atual) na mesma tecla Enter — mesmo padrão do WoW. Único ponto de
ESCRITA: dentro de `_try_handle_whisper_chat_command`/`_try_handle_
group_chat_command` (nunca ativa whisper com comando incompleto, ex:
`/w Nome` sem mensagem — mas "/p" sozinho JÁ é válido, não precisa de
mensagem pra ligar o modo) e `_try_handle_say_chat_command` (sempre
limpa, não importa qual modo estava ativo). Único ponto de LEITURA: o
fallback de `_send_chat_message`, verificado ANTES de cair no canal
normal — clicar no nome (mecanismo acima) e digitar `/w`/`/p`
manualmente alimentam o MESMO estado, então os dois ativam o modo
fixo igual.

### 23. Chat de grupo ("/p" ou "/grupo") (03/10/2026)

Mesma ideia do sussurro (§22), mas entrega pra TODOS os membros do
grupo em vez de 1 pessoa só. `server/session.py::_handle_chat`, novo
ramo `channel == "party"`: resolve membros via `WorldServer.
get_party_members(session.entity_id)` (já existia, mesmo usado pelo
broadcast de `LOOT_UPDATE`) + `get_session_id_for_player(m_eid)` pra
cada um — nunca passa por AOI/mapa, igual o whisper. Mensagem é a
MESMA pra todos (inclui o próprio remetente, sem eco assimétrico como
o whisper precisa — não existe "Você disse pro grupo", todo mundo vê
exatamente a mesma linha). Sem grupo (ou grupo "de 1", já que
`get_party_members` sempre inclui o próprio eid): aviso de sistema só
pro remetente ("Você não está em um grupo"). Cor: verde claro
`[140,230,150]` (escolha minha, sem pedido explícito do usuário sobre
cor — diferente do azul do whisper).

**Cliente**: `/p` ou `/grupo`, com ou sem mensagem
(`_try_handle_group_chat_command`) — "/p" SOZINHO já é uma ação
válida (liga o modo fixo de grupo, diferente de "/w" sem nome, que é
erro por faltar o alvo). Exibição: `client/network_handlers.py::
_handle_msg_chat_message`, `channel=="party"` → rótulo
`"grupo/<sender>"` (formato EXATO pedido pelo usuário, barra entre a
palavra e o nome — não é o `"[Mundial] "` com colchete que a aba
Mundial usa) + `reply_target = ("party", None)`. Cai na aba LOCAL,
mesma decisão já tomada pro whisper (§22) — sem aba própria.

**Clicar numa mensagem de grupo** (`_try_handle_chat_name_click`) —
`mode=="party"` pré-enche `/p ` (resposta vai pro GRUPO, nunca sussurra
só pra quem mandou) — diferente do whisper, NUNCA bloqueia clicar na
própria mensagem (reativar o modo de grupo é sempre uma ação válida,
não tem o problema de "sussurrar pra si mesmo" que o whisper tem).

Testes: `tests/test_whisper_chat.py` (40) — roteamento server-side do
whisper (entrega certa por nome, case-insensitive, alvo offline avisa
só o remetente, nunca usa AOI) + do grupo (entrega pra todos os
membros inclusive o remetente, sem grupo avisa só o remetente, grupo
"de 1" também avisa, nunca usa AOI); parsing dos comandos cliente
("/w"/"/s"/"/p"+"/grupo", válidos/maiúsculo/incompletos, gravam/não-
gravam/trocam o modo fixo); fluxo completo via `_send_chat_message` de
verdade (`/w`→texto plano→novo `/w`→`/s`; `/p`→texto plano→`/w` troca
de grupo pra whisper); composição do rótulo/`reply_target` dos 2
lados do whisper + grupo + mensagem normal + "Sistema"; clique no
nome (whisper e grupo) abre resposta certa, clique no próprio nome só
bloqueia pro whisper, clique fora da região/aba Combate nunca abrem
nada. Revert-to-confirm feito em 4 pontos (match case-insensitive do
whisper, bounds-check do X do clique, leitura do modo fixo no
fallback, entrega pra TODOS os membros do grupo não só o 1º) — os 4
testes relevantes falharam como esperado antes de restaurar.

### 24. Ícone de NPC de serviço no mapa (M)/minimapa — independente de AOI (06/10/2026)

Pedido do usuário: ícone de Treinador/Mercador/Dador de Quest no mapa
(M)/minimapa já tinha a intenção documentada de não exigir visão direta
("guia o jogador"), mas na prática só aparecia enquanto o NPC existia
localmente — e NPC de serviço só existe no cliente enquanto dentro da
AOI (mesmo spawn/despawn de mob). `ui/map_markers.py::collect_markers`
varria o World AO VIVO (`world.get_entities_with(TileMovement,
QuestGiver/Trainer/Merchant)`) em vez dos dados ESTÁTICOS do mapa
(`spawn_points`, a mesma fonte que já alimenta `collect_quest_circles`,
sempre disponível independente de AOI) — ganhou `spawn_points: dict`
como parâmetro novo e passou a ler `spawn_points["quest_givers"]`/
`["trainers"]`/`["merchants"]` direto (tuplas, ver
`engine/map_loader.py::_merge_entities_json` pro shape exato de cada
lista).

**Dador de quest é o caso delicado**: a cor do ícone (!/disponível,
?/completável, etc.) depende do progresso REAL de quest do player, que
as 3 consultas (`engine.quest_logic.get_available_quests`/
`get_completable_quests`/`get_inprogress_quests`) e uma 4ª que vivia
SÓ em `ui/quest_system.py` (`_get_locked_quests`, nunca extraída —
agora `get_locked_quests` em `quest_logic.py`, ponto único de verdade
corrigido de quebra) resolviam só a partir de um `QuestGiver` de
entidade VIVA. As 4 ganharam parâmetro opcional `giver=None`
(retrocompatível — só usado se passado, senão cai no lookup por
`npc_id` de sempre) — `ui/quest_system.py::QuestDialogSystem.
marker_for_static(name, quest_ids, turn_in_ids)` constrói um
`QuestGiver` avulso (nunca anexado a nenhuma entidade — o componente é
só um dataclass simples) a partir dos dados estáticos e chama as
mesmas 4 consultas com `giver=` — mesma decisão (`_marker_from_lists`,
núcleo extraído de `marker_for`) que o caminho de NPC vivo, garantido
por teste que os dois concordam byte a byte pro mesmo quest_ids/
turn_in_ids.

Testes: `tests/test_client_ui.py` (2, novos — world SEM nenhuma
entidade NPC ainda mostra os marcadores, vindos só de spawn_points) +
`tests/test_quest_split_npc.py` (1, novo — `marker_for_static` bate
exatamente com `marker_for` do NPC vivo equivalente). Revert-to-confirm
feito (loop de trainers temporariamente desativado, os 2 testes
relevantes falharam exatamente como o bug real). Regressão: 307 testes
(`test_client_ui.py`/`test_quest_split_npc.py`/
`test_quest_talk_completable_priority.py`/`test_quest_logic.py`/
`test_npc_service_unlock.py`/`test_service_npcs.py`) sem quebra.

100% client-side (`ui/map_markers.py`, `engine/quest_logic.py`,
`ui/quest_system.py`, `game.py`) — sem deploy no Hetzner, precisa de
rebuild do `.exe` antes do próximo upload no itch.io.

### 25. Posição de companheiros de grupo no mapa (M)/minimapa (06/10/2026)

Pedido do usuário, na mesma sessão do item acima: ver no mapa/minimapa
onde estão os companheiros de grupo, independente de FoW/AOI — vale em
mundo aberto E dentro de instâncias (Arena/BG/dungeon, decisão
explícita do usuário via `AskUserQuestion`). Cor do marcador: mesma do
canal de chat de grupo, `[140,230,150]` verde (decisão do usuário —
"a cor de todos pode ser igual", não distingue indivíduo por cor,
símbolo de fallback é a 1ª letra do nome pra diferenciar quem é quem).

**Novo campo em `AOI_UPDATE`** (não é mensagem nova — enriquece o
pacote mais frequente, mesmo padrão já usado por
`ally_vision_centers`, §visão compartilhada de time): `"party_positions":
[{eid, tx, ty, name}, ...]` — ver `shared/messages.py` pro shape
completo. `server/session.py::_build_update_for_session` computa do
ZERO todo tick (nunca delta) via `WorldServer.get_party_members`,
filtrado pro MESMO mapa do destinatário (`MapLocation`) — quem está em
outro mapa/instância simplesmente não entra na lista, cliente não
precisa filtrar nada. Só presente quando o player está em grupo de 2+
E tem companheiro no mesmo mapa (ausência = sem grupo OU grupo sem
ninguém no mesmo mapa agora).

**Por que não precisou de entrada em `has_pending`** (checagem
explícita, mesma regra de sempre pra buffer novo): diferente dos
outros campos documentados lá, `party_positions` não é um buffer
separado — é recomputado inline a partir de posição (`TileMovement`),
e QUALQUER movimento de QUALQUER player já populam `deltas["moved"]`
globalmente (1º termo do `has_pending`, antes de qualquer buffer
específico) — companheiro de grupo se movendo já dispara o dispatch
pra toda a sessão, o resto (meu próprio AOI_UPDATE recomputar
`party_positions` fresco) vem de graça no mesmo ciclo.

**Cliente**: `client/network_handlers.py::_handle_msg_aoi_update` guarda
em `self._party_member_positions: dict[eid, {...}]` (GameEngine,
inicializado em `game.py`) — SEMPRE substitui o dict inteiro (nunca
merge, campo ausente limpa) — `ui/map_markers.py::
collect_party_markers(party_member_positions)` converte pra
`MapMarker` (ícone `map_party_member`, fallback círculo verde + 1ª
letra do nome), somado à lista de `collect_markers()` nas 2 chamadas
de render (`Minimap.render`/`MapOverlay.render`, `game.py`) — zero
mudança nos dois renderers, que já tratam `MapMarker` de forma
genérica.

Testes: `tests/test_session.py::TestPartyPositionsNoAoiUpdate` (3,
novos, servidor) — companheiro longe (fora de qualquer AOI) continua
na lista; nunca inclui o próprio eid; sem grupo não inclui o campo.
**Não executados nesta sessão** (ambiente local sem Postgres/Docker
disponível — mesma limitação já registrada antes nesta mesma sessão
pra `TestLootQuestProgressViaSessionFlow`) — seguem o MESMO padrão
exato dos testes vizinhos já passando neste arquivo
(`fake_login`/`_build_update_for_session`), revisados com cuidado
redobrado por não poder confirmar via execução; rodar localmente antes
de considerar 100% validado. `tests/test_client_ui.py` (3, novos,
cliente) — cor/símbolo do marcador, lista vazia sem grupo,
`_handle_msg_aoi_update` substitui o dict inteiro e limpa quando o
campo some — estes SIM rodados e passando, com revert-to-confirm.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — metade
client-side (`ui/map_markers.py`, `client/network_handlers.py`,
`game.py`) precisa de rebuild do `.exe` antes do próximo upload no
itch.io.

### 26. Ranking Geral — level e skill level, todos os personagens (06/10/2026)

Pedido do usuário, com esboço fornecido: modal com categorias numa
coluna à esquerda (Level + as 11 trilhas de Skill Level — machado/
espada/maça/arco/báculo/escudo/defesa/3 resistências/magic,
`engine.components.SKILL_IDS`) e uma tabela rolável à direita (nome,
posição no rank, valor). Decisões fechadas com o usuário
(`AskUserQuestion`): inclui TODOS os personagens já criados — online ou
não ("ranking de verdade", não só quem está jogando agora); tecla **K**
abre o modal (livre — conferido não haver nenhum `pygame.K_k` já em uso
em lugar nenhum do código, apesar de um comentário antigo e
desatualizado em `ui/ui_sizes.py` sugerir o contrário pro editor de
hotbar, que na verdade abre por um caminho diferente).

**Fonte de dado é o BANCO, não o World ao vivo** — primeira vez que uma
feature deste projeto precisa necessariamente de personagem OFFLINE
(diferente de todo o resto do jogo, sempre sobre o ECS ao vivo).
`server/auth.py::get_ranking(category)` — "level" usa `ORDER BY` direto
na coluna; skill usa `skill_levels_json` (TEXT, não JSONB) lido e
ordenado em Python (mais simples/seguro que confiar em cast `::jsonb`
dentro do SQL, e o volume de personagens ainda é pequeno o bastante pra
isso não pesar) — até 100 entradas, nome como desempate. Mixin novo
`server/ranking_processor.py::RankingProcessorMixin.
process_ranking_request` (1 método, bridge fino pro `auth.get_ranking`
— mesmo padrão de `mail_processor.py`).

**Protocolo**: `RANKING_REQUEST` (C→S, `{category}`) / `RANKING_RESULT`
(S→C, `{category, entries: [{name, class_id, value}]}`) — request/
response simples (não é AOI_UPDATE nem push periódico; ranking não
muda a cada frame).

**Cliente**: `ui/ranking_system.py::RankingSystem` — modal read-only,
SEM estado próprio de "aberto" (mesmo padrão de `SkillLevelUI`/
`CharStatsUI`: flag `self._show_ranking` em `game.py`, ligada/desligada
pela tecla K + `_close_all_modals()`/ESC via `client/
modal_stack_handlers.py::_modal_registry()`). Resultado de cada
categoria fica cacheado em `self._data` pela duração da sessão —
clicar numa categoria já vista não manda outro `RANKING_REQUEST`.
Scrollbar: mesmo padrão visual (track + thumb proporcional) já usado
pelo chat (`client/chat_handlers.py`), só mouse wheel, sem drag.

Testes: `tests/test_client_ui.py` (7, novos, cliente — pedido da
categoria padrão ao abrir, cache por categoria, clique na sidebar troca
categoria, clique no X fecha, roteamento do RANKING_RESULT pro sistema)
— rodados e passando, com revert-to-confirm na lógica de cache.
`tests/test_ranking.py` (5, novos, servidor — ordenação por level,
leitura de `skill_levels_json`, skill sem dado vira 0 (nunca erro),
categoria inválida devolve lista vazia, fluxo completo via protocolo).
**Não executados nesta sessão** (sem Postgres/Docker local disponível —
mesma limitação já registrada nos itens anteriores desta sessão) —
seguem o mesmo padrão de `tests/test_mail.py` (`fake_login` + banco
real de dev); rodar localmente antes de considerar 100% validado.

Achado colateral durante a implementação: um teste JÁ EXISTENTE
(`tests/test_client_ui.py::_make_modal_stack_fixture`) quebrou ao
adicionar a entrada "ranking" em `_modal_registry()` — o fixture
precisa declarar EXPLICITAMENTE todo `_show_X` novo lido pelo registro
(`__getattr__` só auto-stuba método `_close_*`, nunca flag de estado) —
mesma classe de gap já documentada ali pelo próprio comentário do
arquivo (achada antes com o modal de portal). Corrigido no mesmo
commit.

Deploy: enviado pro servidor Hetzner (`deploy_server.ps1`) — metade
client-side (`ui/ranking_system.py`, `game.py`, `client/
network_handlers.py`, `client/modal_stack_handlers.py`, `config.py`)
precisa de rebuild do `.exe` antes do próximo upload no itch.io.

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
| Correio (NPC "Caixa de Correio", entrega assíncrona) | ✅ completo, testado; validação visual em jogo pendente | `server/mail_processor.py`, `server/auth.py` (tabela `mail`), `ui/mail_system.py`, ver §17 |
| Portais (seleção de destino + cast de 5s + animação) | ✅ plumbing + animação do tile completos, testado; animação de personagem e validação visual em jogo pendentes | `server/portal_processor.py`, `client/portal_handlers.py`, `engine/portal_anim_state.py`, ver §18 |
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
| Bush/árvore/pedra grande passam a bloquear linha de visão (LOS) como parede já bloqueava — `vision_height` do catálogo nunca era escrito em `tile_matrix` pra objeto não-sólido (`create_tilemap` pulava por ser "puramente decorativo" antes de checar bloqueio de visão). `TileType.vision_rect` novo dá pegada de visão INDEPENDENTE de `collision_rect` (bush anda em toda a pegada, bloqueia visão em toda a pegada). Árvore originalmente bloqueava visão com a copa INTEIRA (`vision_rect="full"`, tronco continua o único sólido) — corrigido em 27/08/2026 (pedido do usuário, §82): árvore passa a usar o MESMO retângulo do `collision_rect` também pra visão (só o tronco/base bloqueia, igual pedra grande já fazia com `"full"`=próprio corpo sólido) | ✅ completo e testado; nenhuma sprite repintada em mapa (bush/árvore/pedra já existentes herdam o bloqueio automaticamente do catálogo) | `engine/tileset.py::TileType.vision_rect`/`get_vision_offsets`, `engine/entity_factory.py::create_tilemap`, ver `PROBLEMAS_ARQUITETURA.md` §49/§82 |
| Fog assimétrico ao ficar no mesmo tile de bush — tiles vizinhos da MESMA bush (também `vision_height>=2`) cortavam a visão do próprio jogador a curta distância, sobrando só o lado onde o aglomerado acaba rápido. Bush bloqueia de fora-pra-dentro, nunca de dentro-pra-fora (confirmado na wiki oficial de LoL) — `local_vision_blob` isola o blob conectado que contém a origem do observador e o torna transparente SÓ pro FOV calculado a partir dali; `is_solid` impede o blob de vazar pra dentro de parede/pedra/tronco encostados na bush | ✅ completo e testado | `ui/fov.py::local_vision_blob`, `ui/systems.py::FogSystem.update`/`_is_blocking_from`, ver `PROBLEMAS_ARQUITETURA.md` §50 |
| Pegada de visão da bush vira "só a base" — `vision_rect="full"` (sprite inteiro) fazia bushes com espaço de grama visível entre si se fundirem num blob só de visão (a maioria já é multi-tile no desenho real, não sobra de transparência). Novo sentinel `"base_row"` conta só a fileira de baixo (largura inteira) — só bush; pedra/árvore ficam com `"full"` (pedra é sólida = sem o problema de auto-bloqueio; copa de árvore fica na metade de CIMA do sprite, "base" apagaria o bloqueio de propósito) | ✅ completo e testado | `engine/tileset.py::_rect_to_tile_offsets` (sentinel `"base_row"`), catálogo `b1`-`b20` em "TX Village Plant", ver `PROBLEMAS_ARQUITETURA.md` §52 |
| Fog "presa" (desatualizada) na tela principal perto de bush pequena — `TileRenderSystem.render_fog()` cacheava o overlay indexado pela janela de tiles da CÂMERA, nunca reconstruindo só porque `FogOfWar.visible`/`explored` mudaram (mesma classe de bug já documentada no CLAUDE.md pro cache de terreno). Minimapa usava outro cache (correto); só a tela principal ficava desatualizada, autocorrigindo quando a câmera cruzava fronteira de tile por acaso. Fix: `FogOfWar.version` (contador no componente, incrementado por `FogSystem`) lido por `TileRenderSystem` — sem System chamar System direto | ✅ completo e testado | `engine/components.py::FogOfWar.version`, `ui/systems.py::FogSystem.update`/`TileRenderSystem.render_fog`, ver `PROBLEMAS_ARQUITETURA.md` §53 / CLAUDE.md |
| Linha de visão de TERRENO entre times, autoritativa no servidor — `_can_see` nunca teve checagem contra `vision_height` (só proximidade + zona de bush, que nunca teve dado real em nenhum mapa, ver §51). Reaproveita a mesma malha (`tile_matrix`, código compartilhado com o cliente) — parede/árvore/pedra/bush escondem o time adversário de verdade agora (não só visualmente), e o próprio tile do alvo conta (parado em cima de bush = escondido, mesmo sem obstáculo intermediário). Vale pra players E minions/mobs, só entre FACTIONS diferentes | ✅ completo e testado | `engine/utils.py::bresenham_line_tiles`, `server/tile_los_processor.py::TileLosProcessorMixin._has_tile_los`, `server/session.py::_can_see`, ver `PROBLEMAS_ARQUITETURA.md` §54 |
| Bush: visão compartilhada dentro da mesma bush (adversários que dividem a mesma bush se veem, estilo LoL — bloqueio de bush não vale entre quem está DENTRO) + "atacar revela" (usar ataque/skill de dentro da bush revela o atacante por 2s pro time adversário, mesmo que continue fisicamente lá — só player, minion não tem `CombatState`). `ui/fov.py` movido pra `engine/fov.py` (ponto único de verdade compartilhado cliente+servidor). Nenhuma das 2 isenções cancela bloqueio de sólido (parede/pedra/tronco) | ✅ completo e testado | `engine/fov.py::local_vision_blob`, `engine/components.py::CombatState.bush_reveal_timer`, `engine/core_systems.py::apply_damage_core`/`BaseCombatStateSystem._tick_bush_reveal_timer`, `server/tile_los_processor.py::_shares_bush_blob`, `shared/constants.py::BUSH_REVEAL_DURATION_S`, ver `PROBLEMAS_ARQUITETURA.md` §55 |
| AOI revalida a PRÓPRIA visão quando o viewer se move, não só quando o alvo se move — `_build_update_for_session` só reagia a "o ALVO se moveu"; sair de uma bush compartilhada nunca invalidava quem tinha ficado "conhecido" de dentro dela (só destravava quando o outro lado andava de novo, por acaso). Novo passo revalida `known_eids` contra `_can_see` completo sempre que o PRÓPRIO player se move nesse tick — cobre bush e qualquer regra futura cuja resposta dependa da posição do viewer | ✅ completo e testado | `server/session.py::_build_update_for_session`, ver `PROBLEMAS_ARQUITETURA.md` §56 |
| Visão de terreno vira universal — vale pra QUALQUER par de entidades (player/mob/npc/minion), mundo aberto E BG, sem filtro de Faction (antes só rodava "entre facções diferentes", e player comum não tem Faction nenhuma fora de contexto de time, então nunca disparava no mundo aberto). Isenção de vegetação bloqueante (bush/copa) sempre a partir da posição do PRÓPRIO viewer (nunca do alvo — mesmo princípio do cliente, `local_vision_blob`) — corrige bug real (player no meio de uma bush grande não via quem tinha saído). Visão compartilhada de time (aliado dentro da bush revela pro time) continua exclusiva de Faction explícita — nunca mundo aberto. Sólido nunca é isento por nenhuma regra | ✅ completo e testado | `server/tile_los_processor.py::_has_tile_los`/`_ally_bush_blobs`, `server/session.py::_can_see`, ver `PROBLEMAS_ARQUITETURA.md` §57 |
| Torre/minion do PRÓPRIO time sempre visíveis (efeito colateral da LOS virar universal em §57: torre/minion passaram a "sumir" atrás de bush/parede pro time deles também, o que nenhum MOBA faz) — bypass direto em `_can_see` quando alvo é `Tower`/`Minion` E viewer/alvo têm a MESMA Faction explícita; adversário continua sujeito à LOS normal. + a varredura genérica de "entidade em AOI ainda não conhecida" (mob/torre/minion/harvestable) nunca chamava `_can_see` (só distância) — torre/minion/mob adversário sem LOS podiam ser "descobertos" só por estar perto; corrigido. Investigação incluiu uma hipótese (aliado revela inimigo pro time inteiro) que se mostrou FALSO POSITIVO — mecanismo já existia (varredura de players separada, sempre ativa) — nenhum código novo shipado pra esse caso | ✅ completo e testado | `server/session.py::_can_see`/`_build_update_for_session`, `tests/test_team_visibility_sweep.py`, ver `PROBLEMAS_ARQUITETURA.md` §58 |
| Visão de time vira UNIÃO de fontes independentes, estilo LoL (usuário reportou de novo: minion/torre/player aliado não compartilhavam visão de um inimigo pro RESTO do time — a exceção antiga, `_ally_bush_blobs`, só isentava a célula de bush que um aliado ocupava fisicamente, sem raio nenhum, e nunca cobria parede) — `_has_tile_los` ganha `ally_centers` (mesma lista, já calculada 1x por tick por `_compute_ally_vision_centers`); tenta o raycast do PRÓPRIO viewer primeiro, senão tenta a partir de CADA aliado (alvo dentro do raio dele + raycast livre a partir dele). Sólido continua bloqueando de forma absoluta em qualquer raycast. `_ally_bush_blobs` removida (subsumida) | ✅ completo e testado | `server/tile_los_processor.py::_has_tile_los`, `server/session.py::_can_see`, `tests/test_tile_los_visibility.py::TestTeamVisionUnion`, ver `PROBLEMAS_ARQUITETURA.md` §59 |
| Bot 100% servidor pra preencher a fila 2v2 da BG (usuário quer testar sem precisar de 4 humanos: solo ganha parceiro bot contra 2 bots; grupo de 2 cai contra 2 bots; 2 solos que entram separados viram adversários um do outro, cada um com bot parceiro — nunca 2 humanos estranhos no mesmo time) — bot é entidade normal via `WorldServer.spawn_player` (mesmo caminho de login real) + Session FALSA em `SessionManager` (sem ela, o bot fica invisível pros outros players — a varredura de descoberta de player só itera sessões reais). IA (`BotAISystem`) chama as MESMAS funções internas que os handlers de rede já usam (`move_player`/`set_player_target`/`queue_skill`) — zero lógica de combate duplicada. Prioridade por decisão: skill ofensiva > auto-attack > perseguir > empurrar lane (waypoints reaproveitados de `_minion_lanes`, pathfinding reaproveitado de `_get_pathfinding_for_map_file` — nenhum A* novo). Só modo 2v2 (3v3/5v5 inalterados); v1 só mira player inimigo, 1 skill fixa, classe Guerreiro | ✅ completo e testado; validação em jogo real pendente | `server/bg_queue_processor.py::_bg_try_pack_with_bots`/`_spawn_bot_player`/`request_bg_leave`, `server/bot_ai_system.py::BotAISystem`, `engine/components.py::BotControlled`, `tests/test_bg_bot.py`, ver `PROBLEMAS_ARQUITETURA.md` §60 |
| Auto-attack PvE/PvP unificado (usuário testou o bot novo e achou: arqueiro com arco mas SEM aljava ainda atirava contra outro JOGADOR — sem munição, sem LOS, com fórmula de dano genérica; causa raiz era `_process_pvp_attack` sendo uma implementação PARALELA e mais pobre que `_process_player_attacks`, nunca ganhando as checagens que só eram adicionadas do lado PvE) — novo `_resolve_auto_attack` único: perseguindo (agora SEMPRE exigido), arco real/munição/LOS/cooldown, despacho de dano (ranged vs melee), Raiva do Guerreiro + Punho no Queixo do Cavaleiro (antes só disparavam contra mob — pesquisa em AzerothCore, `Unit::RewardRage` mora no caminho único de dano da classe base, confirmou que não deveriam ser PvE-only). `_process_player_attacks`/`_process_pvp_attack` encolheram pro que é específico de alvo (evento de quest vs sync de HP da vítima/morte PvP) | ✅ completo e testado; usuário repetir o duelo real pendente | `server/combat_processor.py::_resolve_auto_attack`, `tests/test_pvp_auto_attack.py`, ver `PROBLEMAS_ARQUITETURA.md` §61 |
| Gold/XP de minion corrigidos + kill credit/assist de player unificado, estilo LoL (usuário jogou a BG e achou: gold de minion dado sem golpe final; minion do PRÓPRIO time dando XP; desconfiança do KDA — pediu pesquisa de referência) — achado investigando, mais sério que o relatado: `players_killed` era código morto em partida real (2 dos 3 lugares que detectam morte de player sempre apagavam o `PendingDeath`, única fonte do killer, ANTES do único código que credita kill rodar). Fix: gold de minion vira last-hit estrito (fallback fica só pra torre); XP de minion filtra por Faction (só time inimigo); novo `WorldServer._player_damage_log` (timestamp+seq, só player→player) + `_handle_player_death(killer_eid=...)` vira o ÚNICO ponto que credita kill (contribuinte mais recente na janela de 10s) e assist (outros na mesma janela) — os 3 pontos que detectam morte de player passam a repassar o killer em vez de descartar. Novo `CharStatsTracker.assists`, propagado no HUD/placar dos 2 sistemas (fila real + `/testbg`) e no cliente (coluna "A"). Rebalance de gold junto (pedido do usuário): minion 20/15/35, torre 80 fixo, kill de player 300, assist 150 | ✅ completo e testado; validação em jogo pendente | `server/server_death_handler.py`, `server/respawn_system.py::_handle_player_death`, `server/world_server.py::_player_damage_log`, `tests/test_bg_kill_credit.py`, ver `PROBLEMAS_ARQUITETURA.md` §62 |
| Investigação pós-§62: janela de kill (usuário desconfiou que só valia pro assist) testada empiricamente e CONFIRMADA correta (mesmo filtro `_recent` decide killer E assist) — nenhum bug encontrado, nenhuma mudança de código; bot travando perto de torre inimiga (`BotAISystem._advance_toward` não passava torres como `dynamic_obstacles` no pathing, diferente do minion-wave que já fazia isso desde 01/08 pelo mesmo motivo) — corrigido reaproveitando `WorldServer._get_tower_tiles_for_map`; `ImportError` cosmético no encerramento do servidor (Ctrl+C) — `SessionManager` não guardava referência das tasks fire-and-forget de `_run_dispatch`, corrida com a limpeza do `asyncio.run()` podia deixar uma exceção "não retirada" já com o interpretador fechando — corrigido com `_dispatch_tasks`/`shutdown_dispatch_tasks()`, chamado por `server/main.py` antes do `raise` do `KeyboardInterrupt` | ✅ completo e testado; validação em jogo pendente (bot perto de torre + Ctrl+C limpo); item da janela de kill sem reprodução, aguardando mais detalhe do usuário se acontecer de novo | `server/bot_ai_system.py::_advance_toward`, `server/session.py::SessionManager._track_dispatch_task`/`shutdown_dispatch_tasks`, `server/main.py`, ver `PROBLEMAS_ARQUITETURA.md` §63 |
| Player remoto (bot ou humano) nunca recebia `Faction` real do servidor — causa raiz PARTILHADA de 2 bugs relatados (bot sumido do minimap + TAB/ESPAÇO selecionando bot do PRÓPRIO time): o PRÓPRIO player já ganha `Faction("arena_time_a/b")` real via ARENA_COUNTDOWN dentro de BG/arena, mas o player remoto continuava só com o default `RemoteControlled`→"jogadores" — par `("arena_time_a","jogadores")` nunca declarado em `RELATIONSHIP` caía em "neutro" (não "amigavel"), e `can_engage()` já libera engajar em "neutro" no 1º check, pulando o resolver de contexto que distingue aliado de inimigo (confirmado via teste diferencial: bug reproduz sem Faction, some com ela). Fix: novo `WorldServer.player_faction_field(eid)` incluído nos 6 pontos que montam ENTITY_SPAWN de player; cliente lê `data.get("faction")` e anexa `Faction` real quando presente — mesmo time vira "amigavel" automático (`faction_a==faction_b`), times opostos caem no par já declarado "hostil", igual o servidor sempre resolveu. 3º bug (bot ainda pulando/travando perto de torre mesmo com fix do §63): `_advance_toward` descartava o passo da rota em cache ANTES de checar se `move_player` aceitou — passo rejeitado desincronizava a rota da posição real, causando "trava depois pula"; fix: descarta a rota inteira (força recálculo) quando o passo é rejeitado | ✅ completo e testado; validação em jogo pendente | `server/world_server.py::player_faction_field`, `client/remote_entity_handlers.py::_spawn_remote_player_entity`, `server/bot_ai_system.py::_advance_toward`, `tests/test_bg_bot.py::TestBotAdvanceTowardRejectedMove`, ver `PROBLEMAS_ARQUITETURA.md` §64 |
| Camps de jungle da BG populados a partir do concept art do usuário (`maps/concepts/moba_bg_entities.png`, agrupamento por proximidade dos pixels marcados) — 14 camps de mob normal (4 solitários mais fortes, 8 trios, 2 quartetos mais fracos) + 4 bosses, todos conferidos contra o terreno real (grama, sem obstáculo). Dados 100% PLACEHOLDER (usuário pediu explicitamente "crie com dados aleatórios, eu altero depois") — 4 tipos novos de mob normal + 3 bosses novos em `content/jungle_definitions.py`, posicionamento nas chaves `"jungle_mobs"/"jungle_bosses"` de `moba_battleground_entities.json` (schema já existia, nunca populado pra este mapa). Feature nova junto: level do camp agora é DINÂMICO — novo `WorldServer._compute_match_avg_level` (irmão de `_compute_team_avg_level` do minion, mas sem filtro de Faction, já que jungle é neutra) recalcula a cada respawn individual do mob, mesmo princípio de "recalcula a cada spawn" que minion já usa | ✅ completo e testado; usuário vai rebalancear os números placeholder e testar em jogo | `content/jungle_definitions.py`, `maps/moba_battleground_entities.json`, `server/world_server.py::_compute_match_avg_level`/`_create_jungle_camps`, `tests/test_jungle_camps.py::TestJungleCampLevelScaling`, ver `PROBLEMAS_ARQUITETURA.md` §65 |
| Jungle sem drop físico de item/gold (gold vai automático pra carteira com FLT, mesmo padrão de minion/kill de player) + jungle deixa de aggrar por proximidade, só por ataque (nova Faction `monstros_jungle`, tier "neutro" — mob hostil clássico do mundo aberto inalterado) + puxão de grupo estilo WoW/LoL: mob da MESMA Faction dentro de 3 tiles (Chebyshev, 1 salto só) também entra em combate quando um vizinho aggra, mecanismo GENÉRICO (não hardcoded pra jungle/BG, testado também com facção hostil clássica). Novo ponto único `engine/core_systems.py::trigger_mob_aggro`/`_pull_nearby_packmates`, substituindo 4 blocos quase-duplicados de transição de aggro por dano espalhados em 4 arquivos (1 deles tinha uma inconsistência real — pulava a AGGRO_DELAY — corrigida de graça ao centralizar); um 3º call site (mob que nasce/fica adjacente ataca direto de IDLE, nunca passava pelo bloco normal de aggro) foi achado só durante os testes e corrigido junto | ✅ completo e testado; validação em jogo pendente | `server/server_death_handler.py`, `content/faction_data.py` (`monstros_jungle`), `engine/core_systems.py::trigger_mob_aggro`, `engine/world_systems.py` (3 call sites), `server/spell_completion_processor.py` (2 call sites), `tests/test_pack_aggro.py`, ver `PROBLEMAS_ARQUITETURA.md` §66 |
| Bot de jungle mostrando mob_key cru como nome (`create_enemy()` ganhou `identity_name` opcional, mesmo mecanismo que `create_combat_npc()` já usava) — corrigido junto de "TypeError no tick ao empurrar lane com curva" (`_lane_goal` v1 reimplementava parse próprio que não batia com a forma normalizada de `target_tile`, devolvia tupla-de-tuplas como destino) | ✅ completo e testado | `engine/entity_factory.py::create_enemy`, `server/world_server.py::_create_jungle_camps`, ver `PROBLEMAS_ARQUITETURA.md` §67/§68 |
| Bot AI v2 (planejado via EnterPlanMode + 4 AskUserQuestion): bot sorteia lane (top/mid/bot, sem repetir no mesmo time) e CLASSE (guerreiro/arqueiro/mago, com skill inicial correta via `INSTANCE_SKILL_UNLOCK_ORDER`); compra e EQUIPA kit inicial por classe (`WorldServer.process_shop_buy`/`equip_item_from_inventory`, mesmos métodos de um player real — guerreiro/mago sem Anel do Poder, orçamento de 150g); espera 10s após a PRÓPRIA wave nascer e segue o MESMO caminho que os minions usam (`WorldServer._compute_lane_route_tail`, extraído de `_tick_minion_waves` — ponto único de verdade, bot e minion nunca mais podem divergir de rota), brigando com minion OU player que entrar num raio de percepção (8 tiles, novo — evita ser puxado pra outra lane); só arrisca alcance de torre inimiga (dive) se já tiver minion aliado "tankando" ela ou o alvo estiver <30% HP | ✅ completo e testado; validação em jogo pendente | `server/bot_ai_system.py` (reescrito), `server/bg_queue_processor.py` (classe/lane/compra), `engine/components.py::BotControlled`, `server/world_server.py::_compute_lane_route_tail`/`_enemy_towers_covering`/`_allied_minion_near_tile` (renomeado em §71), `tests/test_bg_bot.py` (14 casos novos), ver `PROBLEMAS_ARQUITETURA.md` §69 |
| Loja de instância (BG) vende o CATÁLOGO INTEIRO (129 itens, antes 13 escolhidos à mão) — planejado via EnterPlanMode + 4 AskUserQuestion (filtro por tema vs regra de equipar, subtypes não mencionados, preço do comum, fórmula de preço). Filtro por classe é temático e SEPARADO da regra de equipar (`is_weapon_allowed_for_class`/`CLASS_ARMOR_ALLOWED`, mais permissiva) — guerreiro só vê placa/espada/maça/machado/martelo/adaga/escudo; arqueiro só couro/arco/adaga/aljava/munição; mago só tecido/varinha/cajado/cetro; joia/consumível são gerais. Preço fixo por raridade (comum 25g, incomum 50g, raro 100g, épico 150g — múltiplo linear confirmado com o usuário); consumível foge da fórmula (todos são "common" no catálogo, usaria o mesmo preço pra poção e água) e mantém o `value` individual do item. Novo ponto único de verdade `content/merchant_data.py::instance_shop_class_allows`, chamado tanto pelo servidor (`process_shop_buy`, autoritativo — compra fora do tema é rejeitada com `reason: "class"`) quanto pelo cliente (painel da loja, cosmético). `content/instance_shop.py` (lista curada antiga, virou obsoleta) removido; `BOT_STARTER_KIT_BY_CLASS` reajustado pro preço novo (arqueiro perdeu o Anel do Poder, mago trocou Escudo de Ferro por Manto de Seda — escudo virou exclusivo de guerreiro) | ✅ completo e testado; validação em jogo pendente | `content/merchant_data.py` (`instance_shop_class_allows`, `INSTANCE_SHOP_RARITY_PRICE`, `INSTANCE_SHOP_STOCK_BY_CLASS`), `server/world_server.py::process_shop_buy`, `ui/systems.py` (painel + eventos da loja), `server/bot_ai_system.py::BOT_STARTER_KIT_BY_CLASS`, `tests/test_instance_progression.py`, ver `PROBLEMAS_ARQUITETURA.md` §70 |
| Bot travando perto de torre (dive-gate frouxo) + bot retentando skill em cooldown pra sempre (2 bugs reais de playtest, investigados por reprodução ao vivo — hipótese inicial de "mapa com região desconectada" foi FALSA, usuário corrigiu, causa real era a mecânica de abertura de portão que meu teste não simulava). Dive-gate: condição "minion aliado JÁ DENTRO do alcance da MESMA torre" (§69) trocada por "minion aliado PERTO DO PRÓPRIO BOT" (`WorldServer._allied_minion_near_tile`, raio `BOT_DIVE_ALLY_MINION_RADIUS_TILES=4`) — a condição antiga exigia timing exato (minion morre rápido pra torre, raramente está lá quando o bot chega), causava o bot ficar parado pra sempre esperando; confirmado por reprodução real (rota completa da lane "top", tile 97/175 bloqueado sem minion por perto). Skill retry: novo `WorldServer._skill_ready(eid, skill_obj)` extraído da validação de cooldown de `_process_skill_requests` (fonte AUTORITATIVA, `_skill_last_used`/`_skill_effective_cd`) — `BotAISystem._try_cast_offensive_skill` passa a consultar essa função em vez de `PlayerSkills.skill.current_cooldown` (campo local, só atualizado por um CLIENTE real processando SKILL_RESULT pela rede; bot não tem cliente, então ficava martelando a MESMA skill em cooldown real pra sempre sem cair pro auto-attack — confirmado em `logs/server.log` real, arqueiro tentando `picada_escorpiao` a cada ~0,35s por 2+ minutos, sempre rejeitado) | ✅ completo e testado; validação em jogo pendente | `server/world_server.py::_allied_minion_near_tile`/`_skill_ready`, `server/skill_processor.py::_process_skill_requests`, `server/bot_ai_system.py::_dive_allowed`/`_try_cast_offensive_skill`, `tests/test_bg_bot.py::TestBotDiveGate`/`TestBotSkillReady`, ver `PROBLEMAS_ARQUITETURA.md` §71 |
| Bot travando perto da PRÓPRIA torre logo após spawnar (usuário corrigiu minha 1ª hipótese do §71 com print — torre era aliada, não inimiga; pedi confirmação antes de investigar de novo, por instrução explícita). Causa raiz real: torre tem pé de colisão de 2 tiles desde §42, mas `WorldServer._get_tower_tiles_for_map` (obstáculo usado tanto por `_compute_lane_route_tail`, rota compartilhada minion/bot, quanto pelo fallback A* do bot) nunca foi atualizada pra isso — só registrava o tile ÂNCORA. Rota calculada podia cruzar o 2º tile (invisível pro A*); minion nunca mostrava o sintoma porque seu PRÓPRIO fallback (`MinionSystem._get_occupied_tiles`) já usava o pé real desde §42, mas o fallback do bot (`_advance_toward`) reusava o mesmo obstáculo incompleto — recalculava o MESMO caminho ruim e travava pra sempre no mesmo tile. Confirmado por reprodução ao vivo (mapa real, lane "top", tile `(4,88)` do pé da própria torre do time A na posição 5 de 176 da rota). Fix: `_get_tower_tiles_for_map` passa a usar `entity_footprint_tiles` (mesma função que os outros 6 pontos do §42 já usam) — 1 função, corrige minion (já não tinha o bug) e bot (tinha) de uma vez | ✅ completo e testado; validação em jogo pendente | `server/world_server.py::_get_tower_tiles_for_map`, `tests/test_minions.py::test_rota_desvia_do_pe_de_2_tiles_da_torre_nao_so_do_ancora`, `tests/test_bg_bot.py::test_rota_do_bot_desvia_do_pe_de_2_tiles_da_torre`, ver `PROBLEMAS_ARQUITETURA.md` §72 |
| Preferência de slot de skill unificada dentro/fora da BG (usuário relatou: arrastar Interceptar pra outro slot dentro da BG, o próximo level-up desfazia a troca sozinha). Causa raiz 1: `game.py::_save_config()` bloqueava `_send_hotbar_update()` inteiro dentro da instância (só a escrita em config.json deveria ficar bloqueada) — fix aprovado e implementado. Discutindo a correção, usuário pediu escopo maior: configurar uma skill dentro OU fora da BG deve ser a MESMA preferência, memorizada em `config.json` (não no banco — instância nunca persiste mesmo). Redesenho: novo `characters.{nome}.skill_slot_prefs` (skill_id→slot, client-only, MERGE nunca replace) consultado pelos 3 pontos que colocam skill numa hotbar (`ui/trainer_system.py::_do_learn`, `client/network_handlers.py::_handle_msg_skill_granted`, bloco `skills_hotbar`); servidor jogou fora a lógica de "adivinhar" posição espelhando a hotbar real (`_real_slot_by_skill_id`/`reserved_slots` removidos de `server/instance_progression.py` — posição virou 100% decisão do cliente, servidor só informa QUAIS skills estão desbloqueadas). Reconciliação do `skills_hotbar` reescrita: remove skill que sumiu do payload (troca de contexto — mundo aberto↔instância), nunca reposiciona skill já conhecida e ainda presente, skill nova usa preferência/fallback | ✅ completo e testado; validação em jogo pendente | `game.py` (`_skill_slot_prefs_to_dict`/`_preferred_slot_for_skill`/`_save_config`), `ui/trainer_system.py::_do_learn`, `client/network_handlers.py` (`_handle_msg_skill_granted`, bloco `skills_hotbar`), `server/instance_progression.py` (`_grant_instance_skill` simplificado), `tests/test_hotbar_bg_leak.py`, ver `PROBLEMAS_ARQUITETURA.md` §73 |
| 3 correções não relacionadas, achadas investigando (não suposição) depois do usuário validar o fix da preferência de slot: (1) "Punho no Queixo" ocupava o slot do Interceptar na BG — `server/combat_processor.py` tem um mecanismo à parte (rastreio de carga do talento Cavaleiro) que insere essa skill no array do servidor no primeiro golpe do guerreiro, sem saber de `INSTANCE_SKILL_UNLOCK_ORDER` (a instância pré-aloca a árvore de talento no máximo desde a entrada, então o flag liga antes do 1º level-up de verdade); fix só no cliente — bloco `skills_hotbar` filtra pra só revelar skill_id que está na ordem curada da classe QUANDO dentro da instância, sem tocar no mecanismo de carga do servidor (que é ambient real do build Cavaleiro fora dela). (2) TAB não alcançava alvo "neutro" (ex: mob de jungle) — `_visible_enemies_sorted` usava `is_hostile` por decisão explícita do usuário de 01/08/2026, ANTES da jungle (tier neutro) existir; trocado por `can_engage` (mesma função que o loop de player remoto já usava). (3) SPACE trocava de alvo em vez de atacar o já selecionado, no jogo todo — `_space_engage` sempre recalculava "mais próximo" do zero, ignorando `combat_state.target_entity_id`; agora usa `utils.is_target_alive()` pra manter o alvo já selecionado quando válido, só busca "mais próximo" sem alvo nenhum. **Correção**: usuário testou e o item 3 não tinha resolvido — a função corrigida (`ui/systems.py::PlayerInputSystem._space_engage`) é caminho offline/legado; a REAL usada no jogo online é `client/save_sync_handlers.py::_space_engage_online` (`game.py` despacha K_SPACE pra ela), mesmo bug, corrigida com a mesma lógica. Achado não resolvido, flag pro usuário: `PlayerInputSystem` está registrada e RODA de verdade no jogo online (`game.py:839`) — as 2 funções processam o MESMO K_SPACE de forma independente (violação de ponto único de verdade); inofensivo agora porque as 2 têm a mesma lógica, mas vale limpar depois | ✅ completo e testado; validação em jogo pendente | `client/network_handlers.py` (filtro do bloco `skills_hotbar`), `ui/systems.py` (`_visible_enemies_sorted`, `_space_engage`), `client/save_sync_handlers.py::_space_engage_online`, `tests/test_hotbar_bg_leak.py`, `tests/test_client_ui.py`, ver `PROBLEMAS_ARQUITETURA.md` §74 |
| Correção 2 do item 3 (SPACE): (a) `_space_engage_online` ainda não alcançava alvo "neutro" na busca de fallback — o fix anterior só priorizou `target_entity_id`, não trocou `is_hostile` por `can_engage` como o TAB já tinha. (b) Limpeza do código paralelo achado na correção anterior, por instrução PADRÃO do usuário ("sempre que acharmos código paralelo de offline temos que limpar"): removida a escuta duplicada de K_SPACE dentro de `PlayerInputSystem.update()` (chamava `_space_engage`, redundante com o despacho direto de `game.py`); usuário escolheu manter o método `_space_engage` no arquivo (só a chamada saiu), pelas 4 opções apresentadas via AskUserQuestion. Achado durante a limpeza: o bloco removido tinha gate `can_act` (CC) que `_space_engage_online` nunca teve, e nenhum outro ponto de `game.py` checava isso antes do despacho — sem repor, a limpeza teria destravado SPACE sob stun/sleep/fear/polymorph; corrigido com `is_action_locked()` (mesmo choke-point do servidor) no topo de `_space_engage_online` | ✅ completo e testado; validação em jogo pendente | `client/save_sync_handlers.py::_space_engage_online` (`can_engage` + `is_action_locked`), `ui/systems.py::PlayerInputSystem.update` (bloco K_SPACE removido), `tests/test_client_ui.py` (`test_space_engage_online_sem_alvo_alcanca_mob_neutro`, `test_space_engage_online_bloqueado_por_stun`), ver `PROBLEMAS_ARQUITETURA.md` §74 (Correção 2) |
| Bots de BG: guerreiro parava 1 tile longe demais do alvo e nunca acertava skill/auto-attack melee; mago lançava Bola de Fogo sem parar mas nunca causava dano ("sem cooldown" era sintoma disso). Planejado via EnterPlanMode + pesquisa externa (Game AI Pro 3, "Paragon Bots", Epic Games) + 3 AskUserQuestion. Causa 1: `BOT_ENGAGE_RANGE_TILES=2` genérico pra 3 classes — melee de verdade exige Chebyshev ≤1 (`_resolve_target`/`_resolve_auto_attack`), arqueiro/mago paravam mais perto do que o alcance real da arma/skill permitia. Causa 2: skills de projétil (`bola_de_fogo`/`flecha_reiterada`/`picada_escorpiao`/`tiro_repulsivo`) só aplicam dano quando o CLIENTE manda `PROJECTILE_HIT_CS` — bot não tem cliente, nunca confirma, a entrada em `_spells_in_flight_queue` expirava sozinha sem efeito (confirmado por simulação real: 7 casts completos, 0 dano em 20s). Fix 1: `BotAISystem._effective_engage_range` novo — arqueiro usa alcance do arco equipado (`CombatProcessorMixin._ranged_weapon_range`, extraído de `_resolve_auto_attack` pra ponto único), caster usa `cast_range` da 1ª skill ofensiva (sem hardcode de "bola_de_fogo"), fallback 1 tile. Fix 2: `SpellCompletionMixin._schedule_bot_projectile_autohit`/`_tick_bot_projectile_autohits` novos — agenda auto-confirmação por tempo de voo (distância/velocidade, valores espelhados de `ui/spell_system.py`) que chama a MESMA `_apply_spell_on_projectile_hit` de uma confirmação real, zero efeito em players. Kiting explicitamente NÃO implementado agora (usuário manteve a decisão v1 "nunca recua") | ✅ completo e testado; validação em jogo pendente | `server/bot_ai_system.py::_effective_engage_range`, `server/combat_processor.py::_ranged_weapon_range`, `server/spell_completion_processor.py` (`_schedule_bot_projectile_autohit`/`_tick_bot_projectile_autohits`), `server/world_server.py` (`_bot_projectile_autohits`, chamada no `_tick`), `tests/test_bg_bot.py`, ver `PROBLEMAS_ARQUITETURA.md` §75 |
| Crash real ao treinar skill (fora da BG): `_preferred_slot_for_skill`/`_skill_slot_prefs_to_dict` nasceram método de `GameEngine` no fix anterior (linha acima, §73) — funcionavam via mixin pra `NetworkHandlers`, mas `TrainerSystem` (`ui/trainer_system.py`) é objeto SEPARADO sem referência de volta pro engine, então `self._preferred_slot_for_skill` não existia ali (`AttributeError`). Usuário rejeitou as 2 correções propostas de primeira e pediu investigação melhor ("já não existe uma função onde isso deveria estar?") — achado: o nome do personagem que a função lê já existe no ECS (`CharacterStats.name`, mesma fonte de `_logged_char_name`), e `client/` já importa de `ui/` livremente (nunca o contrário) — não precisava de referência nova nenhuma. Extraído pra `ui/skill_slot_prefs.py` (funções puras, recebem `world`/`player_entity`), os 3 chamadores (`game.py`, `client/network_handlers.py`, `ui/trainer_system.py`) importam a mesma função. Teste novo instancia o `TrainerSystem` de VERDADE (não uma fixture leve) — as fixtures antigas mascaravam esse bug monkeypatchando o método direto | ✅ completo e testado; validação em jogo pendente | `ui/skill_slot_prefs.py` (novo), `game.py`, `client/network_handlers.py`, `ui/trainer_system.py::_do_learn`, `tests/test_hotbar_bg_leak.py::test_trainer_system_real_aprende_skill_sem_crash`, ver `PROBLEMAS_ARQUITETURA.md` §76 |
| `[MinionWave] rota falhou ... sem caminho` logando pra sempre depois que a última partida de BG/Arena esvaziava (usuário relatou: servidor ligado sem players, bots da BG saíram, log passou a spammar a cada 45s até reiniciar). Causa: `WorldServer._unload_instance()` (chamada por BG/Arena/debug_battleground quando o último ocupante sai) liberava `_map_bundles`/`_pvp_zones_by_map`/`_bush_zones_by_map` mas esquecia `_minion_lanes[instance_key]` — `_tick_minion_waves` continuava achando a lane e tentando rotear num mapa sem bundle, pra sempre. Fix: 1 linha, `self._minion_lanes.pop(instance_key, None)` — `_minion_wave_timers` já se autolimpa sozinho no próximo tick (mecanismo existente, não duplicado) | ✅ completo e testado; validação em jogo pendente | `server/world_server.py::_unload_instance`, `tests/test_bg_queue.py::TestBgQueueLeave::test_ultimo_a_sair_para_o_minion_wave_da_instancia`, ver `PROBLEMAS_ARQUITETURA.md` §77a |
| Botão nativo de maximizar do Windows sobrescrevia a preferência "Janela" sem o usuário mexer em menu (usuário relatou: jogo às vezes abre quase em tela cheia mesmo com "Janela" marcada, só resolve trocando de modo manualmente). Causa: janela `RESIZABLE` — qualquer maximizar nativo (botão, duplo-clique na barra de título, Aero Snap) disparava `WINDOWMAXIMIZED`, e o handler (Fase G) gravava `_window_mode_pref="maximized"` + `_save_config()` na hora, sem nenhum redesenho visual (por isso passava despercebido). `"maximized"` é valor legado que migra pra `"windowed_fullsize"` no boot seguinte — daí o "abre quase em tela cheia do nada". Fix: removidos os 2 handlers (`WINDOWMAXIMIZED`/`WINDOWRESTORED`) por completo — só o menu Configurações > Janela decide o que fica salvo; maximizar nativo continua funcionando (o Windows já cuida disso), só para de persistir. Sem teste automatizado (loop de evento de janela nativa, fora do que a suíte cobre) — verificação manual pendente | ✅ completo, validação manual em jogo pendente | `game.py` (loop de eventos em `run()`, `pygame.event.set_allowed()`), ver `PROBLEMAS_ARQUITETURA.md` §77b |
| Catálogo de tiles: 1 cor por arquivo inteiro fazia grama/terra/pedra/objetos bem diferentes visualmente ficarem indistinguíveis no minimapa/mapa-múndi quando misturados no MESMO PNG. Planejado via EnterPlanMode + pesquisa externa (técnicas padrão de cor de minimapa em engines 2D). Achado sério durante a implementação (não previsto no plano original): mesmo com cor certa cadastrada, o pipeline do minimapa NUNCA chegaria a vê-la — `engine/map_loader.py::_parse_terrain_cell` reduz terreno de sheet pro char subjacente ("G") antes do CSV virar `terrain_matrix` (o ID do sprite só sobrevive em `terrain_visual`, que `MapOverlay.load_map()` não recebia); `game.py::_merge_display_matrix` truncava ID de objeto multi-char pro 1º caractere ("vp_001"→"v", que não bate com nenhum TileType). Fix mecanismo: `engine/tileset.py` ganhou `"groups"` (modo catálogo, cor por grupo dentro do arquivo, aditivo/retrocompatível) e `color_overrides` (modo grade, mirror de `collision_overrides`). Fix pipeline: `_merge_display_matrix` parou de truncar + ganhou parâmetro `terrain_visual` (prioridade objeto > visual de terreno > terreno puro), `ui/map_overlay.py::load_map` passou a pintar o footprint de colisão inteiro do objeto (`get_collision_offsets`, já existia), não só a âncora. Prova de conceito: "TX Tileset Terrain" migrado pra `color_overrides` por faixa de linha (grama/pedra/terra), cores determinadas por inspeção visual do PNG | ✅ completo e testado; validação em jogo pendente | `engine/tileset.py` (`_register_catalog_tiles`, `discover_object_sheet_tiles`, `discover_sheet_tiles`, `SHEET_FAMILIES["TX Tileset Terrain"]`), `game.py::_merge_display_matrix` (+3 call sites), `ui/map_overlay.py::load_map`, `tests/test_tileset_color.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §78 |
| Pincel aleatório por grupo no editor (F10, aba Objetos) — clicar no NOME de um grupo (só famílias com `"groups"`, ver §78) ativa um pincel que sorteia 1 elemento do grupo a cada tile pintado, nunca repetindo o último colocado. `engine/tileset.py::group_tile_ids(group)` novo (ponto único de verdade pra "ids completos de um grupo"); `GodModeEditor._resolve_brush_char()` chamado no lugar de ler `_sel_char` direto, cobrindo clique único e arrasto contínuo | ✅ completo e testado; validação em jogo pendente | `engine/tileset.py::group_tile_ids`, `ui/god_mode.py` (`_brush_group_ids`/`_resolve_brush_char`), `tests/test_god_mode_random_brush.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §79 |
| Migração de conteúdo: árvore/arbusto default (`"t"`/`"b"`) + "TX Plant" antiga espalhados pelos mapas → elementos reais de "TX Village Plant" (grupos `arvores`/`arbustos`/`grama_fina`), sorteio sem repetir (mesmo algoritmo do §79). Achados que exigiram parar e perguntar antes de editar: metade dos arquivos de mapa é código MORTO (`load_map_csv` sempre prefere o par `_terrain/_objects` quando existe); a troca muda colisão de verdade (arbusto novo é andável; árvore grande tem colisão deslocada 3-5 tiles — `t14`/`t15` excluídos do sorteio). 14066 substituições em 3 arquivos realmente carregados pelo jogo (script 1x, não faz parte do código permanente) | ✅ completo e testado; validação visual em jogo pendente | `engine/tileset.py::group_tile_ids` (reuso), script 1x em scratchpad (não versionado), `maps/map_1_objects.csv`/`map_main_objects.csv`/`moba_battleground_objects.csv`, ver `PROBLEMAS_ARQUITETURA.md` §80 |
| Pincel inteligente de terreno (F10, aba Terreno, "TX Tileset Terrain") — pintar "Grama" sobre "Terra" (ou vice-versa) escreve a categoria nova no tile clicado (sorteio 80/20 pura/com flor) e preenche/RECALCULA cada 1 dos 8 vizinhos a partir do AMBIENTE REAL dele (`_missing_sides`, ground truth em `terrain_matrix` — nunca esquece a categoria de um tile já decorado, só cosmético) — método principal, autoritativo, autocorrige durante arrasto em QUALQUER direção (um vizinho decorado antes continua "categoria antiga" de verdade, então toda pincelada próxima recalcula ele com mais contexto). Quando o real não resolve (sem contexto ainda — tipicamente diagonal de clique isolado), cai no fallback por direção do clique (`_direction_missing_sides`) só se o vizinho for ORTOGONAL ao clique atual ou ainda não tiver decoração nenhuma (evita um toque diagonal fraco sobrescrever uma peça boa já resolvida). 4 correções em cima da 1ª versão (18/08→27/08): Correção 1 trocou sorteio cego por autotile "real" (mas com polaridade invertida — resolvia peça ERRADA); Correção 2 trocou pra direção-do-clique fixa (resolvia visual mas nunca atualizava durante arrasto); Correção 3 corrigiu os 2 bugs reais de comparação da Correção 1 (polaridade + categoria esquecida) e usa o cálculo real como principal, com a direção-do-clique só como reforço restrito; Correção 4 (medição de pixel real, não visual) achou que as 4 peças de CANTO da zona "pintar terra sobre grama" têm a cor nova só ~30-50% presente (nos 2 estilos mancha/cruz) contra ~100% na zona irmã — não é bug de cálculo, é o próprio desenho do PNG — `_resolve_piece_offset` troca canto fraco por 1 peça de borda (sempre forte) quando a zona é `"weak_corners"`. "Piso sobre grama/terra" reusa o pincel de grupo aleatório do §79, pool = zona `floor_full`. `"zones"` em `SHEET_FAMILIES["TX Tileset Terrain"]` + `terrain_zone_of()`/`resolve_blob3x3_piece()` (pontos único de verdade) | ✅ completo e testado; usuário aceitou o estado atual como satisfatório (03/09/2026), fechado | `engine/tileset.py` (`SHEET_FAMILIES["TX Tileset Terrain"]["zones"]["transicao_grama_terra"]["weak_corners"]`, `terrain_zone_of`, `resolve_blob3x3_piece`), `ui/god_mode.py` (`_apply_smart_terrain`/`_resolve_piece_offset`/`_missing_sides`/`_direction_missing_sides`/`_terrain_category_at`), `tests/test_god_mode_terrain_brush.py`, ver `PROBLEMAS_ARQUITETURA.md` §81 |
| Dano por tick de DoT de habilidade de mob (bleed/poison/burn) escalava linear demais — `magnitude * base_attack_power` compunha 2 crescimentos lineares, já que `base_attack_power` do mob já multiplica linear por level (`entity_factory.py`). Urso level 24, Laceração batendo 96/tick. Novo `damage_calculator.mob_dot_tick_damage(magnitude, base_attack_power, level)` divide por `√level` — cresce mais devagar em levels altos, idêntico ao valor antigo em level 1. Vale pra TODOS os DoTs de mob (ponto único de verdade, 2 pontos de chamada em `world_systems.py`, corpo-a-corpo e projétil) | ✅ completo e testado; validação em jogo pendente | `engine/damage_calculator.py::mob_dot_tick_damage`, `engine/world_systems.py` (2 call sites), `tests/test_mob_dot_scaling.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §83 |
| Skill dando "Alvo inválido"/"Fora de alcance" depois de Interceptar OU depois de simplesmente selecionar alvo e castar, na BG — 1º bug real (`_resolve_target()` com fallback de PvP via `RemoteControlled`, código morto no servidor) corrigido cedo mas não bastou; usuário jogou partida inteira e confirmou escopo mais amplo. Causa raiz de verdade, achada via debug instrumentado (não suposição): `TileMovement.is_moving` escrito por 2 relógios independentes — o tween real (`TileMovementSystem`) e um relógio FAKE (`ServerCombatStateSystem._tick_player_move_grace`, existe só pra mecânicas tipo Calmo e Certeiro saberem "andou recentemente"). Movimento normal usa a MESMA duração nos 2 (0.4s, sempre em sincronia); Interceptar usa `move_duration=0.18s` só no tween real, sem resetar o grace fake — se sobrava pouco grace de um passo normal anterior, ele zerava NO MEIO do dash, desligando `is_moving` à força enquanto `progress<1.0`. Resultado: `TileMovementSystem` parava de processar a entidade (só processa `if is_moving`), `Position`/`progress` congelavam no meio da interpolação, `is_dash` ficava preso `True` por segundos — corrompendo QUALQUER range-check de skill seguinte, mesmo sem Interceptar por perto (a corrupção podia ter acontecido minutos antes). Fix: `_tick_player_move_grace` nunca desliga `is_moving` à força enquanto `is_dash=True` — mesmo guard já usado pra proteger dash de interrupção por stun/sleep | ✅ completo, testado e validado em jogo pelo usuário (30/08/2026). Bot com HUD de morto/impossível de atacar às vezes continua aberto (bug separado), debug instrumentado, aguardando log de repro | `engine/skill_handlers.py::_resolve_target` (loop PvP) / `_warn`/`_log_skill_warn_debug`, `engine/core_systems.py::ServerCombatStateSystem._tick_player_move_grace` (fix real), `debug/aoi_debug.py` (eventos `PLAYER_*`/`SKILL_WARN`/`DASH_COMPLETE`/`INTERCEPTAR_CAST`/`INTERCEPTAR_STUN_CHECK`), `tests/test_resolve_target_hostility.py::TestResolveTargetPvpServerSide`, `tests/test_move_grace_dash_guard.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §84 |
| Minimapa (janela pequena, `Minimap.render()`) mostrava toda entidade `Enemy` como ponto vermelho fixo, inclusive mob neutro de jungle (`monstros_jungle`) — usuário pediu distinção visual. Reuso do ponto único de verdade já existente (`engine/faction_system.py::get_relationship_between`, mesma função de `can_engage`/`is_hostile`) — `game.py` resolve a cor por entidade (amigável/neutro/hostil) antes de montar a lista de pontos; `Minimap.render()` só ganhou 1 cor por ponto (mesmo formato `(tx,ty,cor)` que `render_fullmap` já usa pra BG em tela cheia, sem mecanismo novo). Cores novas: `ALLY_COL=(100,255,140)` verde-menta, `NEUTRAL_COL=(255,225,90)` amarelo-claro; `ENEMY_COL` (vermelho) sem mudança | ✅ completo e testado; validação visual em jogo pendente | `ui/minimap.py::Minimap.render` (`ALLY_COL`/`NEUTRAL_COL`), `game.py` (bloco de montagem de `_enemy_tiles`), `tests/test_client_ui.py::test_render_desenha_ponto_com_a_cor_passada_por_entidade` |
| Colisão fina (16px) pro passo manual (WASD) do jogador — tentativa de deixar objetos pequenos (props com `collision_rect` sub-tile no catálogo, ex: "TX Village Props with Shadow") não bloquearem o tile de 32px inteiro. 4 rodadas de correção real (matemática de fronteira de tile, passo encadeado "atravessando" o objeto, gate de rede nunca avisando o servidor de um meio-passo no mesmo tile, `current_tile_x/y` podendo apontar pra um tile sólido e travar o player pra sempre) sem convergir — usuário considerou o resultado final "muito grotesco" e pediu reversão completa (30/08/2026) | 🔴 revertido a pedido do usuário — histórico completo (mecanismo tentado + cada bug achado) preservado em `PROBLEMAS_ARQUITETURA.md` §85 pra qualquer tentativa futura não começar do zero | ver `PROBLEMAS_ARQUITETURA.md` §85 |
| Comando de chat `/forfeit`/`/ff` na BG (01/09/2026, pedido do usuário — mesma UX que a Arena já tinha, sem votação por enquanto, versão de testes) — declara o time ADVERSÁRIO de quem digitou vencedor NA HORA (não é "eu saio", é "a partida acaba"), mostra o mesmo placar de fim de partida que o Nexus caindo gera. Cobre os 2 sistemas de BG (fila real matchmade + `/testbg` debug), cada um com sua própria função `_finish_*_match` extraída do código que já montava o placar por Nexus (ponto único de verdade, não duplicado). Efeito colateral necessário: `ArenaHandlers._try_handle_arena_chat_command` (client) parou de engolir "/ff" com aviso quando fora de partida de Arena — agora passa a vez, senão o BG nunca recebia o comando quando o player estava numa partida de BG mas não de Arena | ✅ completo e testado | `MsgType.BG_FORFEIT` (`shared/messages.py`), `server/bg_queue_processor.py::request_bg_forfeit`/`_finish_bg_match`, `server/debug_battleground.py::_forfeit`/`_finish_debug_match` (subcomando `/testbg ff`), `server/session.py::_handle_bg_forfeit`, `client/battleground_handlers.py::_send_bg_forfeit`/`_try_handle_bg_forfeit_chat_command`, `client/arena_handlers.py::_try_handle_arena_chat_command` (passa a vez), `tests/test_bg_queue.py::TestBgQueueForfeit`, `tests/test_debug_battleground_match.py::TestForfeit` |
| Skill real sumia da hotbar ao SAIR de qualquer instância de BG (não só `/ff`) — `client/network_handlers.py::_handle_msg_stats_update` processava `skills_hotbar` ANTES de `in_instance` no mesmo payload, lendo o flag "estou na BG" ainda desatualizado (`True`) e filtrando a lista REAL de skills pela lista curada da instância por engano. Fix: bloco `skills_hotbar` passa a ler `payload.get("in_instance")` primeiro, mesmo padrão já usado por outro bloco no mesmo método (01/09/2026) | ✅ completo e testado | `client/network_handlers.py::_handle_msg_stats_update`, `tests/test_client_instance_sync.py::TestSkillsHotbarExitCombinedPayload`, ver `PROBLEMAS_ARQUITETURA.md` §93 |
| "TX Village Props with Shadow" (213 objetos) ganhou grupos por categoria (cemitério/pedras/plantas/móveis/potes/comida/madeira/decoração/diversos) — cor própria por grupo no minimapa, mesmo padrão de "TX Village Plant" (§78). `"prefix"` continua "vp" em TODOS os grupos (mapas reais já têm objetos plantados como "vp_XXX" — trocar o prefixo por grupo quebraria esses ids); só a cor muda, ids/geometria/colisão intocados (02/09/2026) | ✅ completo e testado | `engine/tileset.py::OBJECT_SHEET_FAMILIES["TX Village Props with Shadow"]["groups"]`, ver `PROBLEMAS_ARQUITETURA.md` §94 |
| Recatálogo de "TX Tileset Terrain" — usuário reorganizou o PNG (mais variações de grama), zonas `grama_pura`/`grama_flor`/`piso_sobre_terra` atualizadas pra posição nova (terra/transição não mudaram, confirmado). `color_overrides` (cor no minimapa) parou de ser hand-written — agora deriva de verdade das próprias `zones`, pra um recatálogo futuro não precisar mexer nisso de novo (02/09/2026) | ✅ completo e testado | `engine/tileset.py` (zonas + `_derive_terrain_zone_colors`), ver `PROBLEMAS_ARQUITETURA.md` §96 |
| `map_width_tiles` usava só a largura da linha 0 do terreno, nunca a MAIOR linha — mapa de forma orgânica/não-retangular (`map_1`) tinha terreno real pintado além da coluna da linha 0 tratado como "fora do mapa" por movimento/câmera/minimapa. Bug pré-existente, achado investigando spam de "[MAPA] Aviso" no console (02/09/2026) | ✅ completo e testado | `engine/entity_factory.py::create_tilemap`, `engine/map_loader.py::validate_map` (checagem de largura removida — não existe uma "largura esperada" única pra qualquer formato de mapa), `tests/test_map_width_ragged_rows.py`, ver `PROBLEMAS_ARQUITETURA.md` §97 |
| Catalogação de "TX Village Building with Shadow" (97 peças, prefixo "vb") — construções vêm fatiadas em várias peças no PNG (colisão em mais de 1 retângulo); usuário criou 3 PNGs de apoio (dimensões/colisões/agrupamento por proximidade) pra guiar a extração. Retângulos extraídos por script, nomes/agrupamento em "1 construção = N peças" por inspeção visual peça a peça. 29 grupos (1 por construção, chave curta tipo "b01", nome descritivo só no "label") + "muralhas" + "mercado_e_diversos", mesma cor pra todos. Ids sequenciais puros "vb001".."vb097" (`"no_prefix_separator"`, opt-in por família — não afeta outras famílias do catálogo). 03/09/2026: usuário reeditou os 4 PNGs de apoio (104→97 peças, 1 construção removida da arte, ~10 resliced) — recatalogado do zero, `map_1_objects.csv` (50 peças já plantadas) corrigido: 27 renomeadas 1-a-1 (posição idêntica), 25 removidas do mapa (construção mudou de forma, precisam ser replantadas) | ✅ completo e testado; 25 peças do mapa precisam ser replantadas manualmente (ver PROBLEMAS_ARQUITETURA.md §103 pra lista) | `engine/tileset.py::OBJECT_SHEET_FAMILIES["TX Village Building with Shadow"]`, `engine/tileset.py::_register_catalog_tiles` (`no_prefix_separator`), ver `PROBLEMAS_ARQUITETURA.md` §98/§99/§100/§103 |
| Objeto do `object_matrix` maior que 1 tile (construção, árvore grande) só aparecia quando a câmera alcançava a BASE dele — `get_world_objects()`/`render_static_objects()` (`ui/systems.py`) varriam só "tela em tiles + 2" (margem fixa) pra decidir qual tile-âncora considerar, mas o sprite cresce PRA CIMA/DIREITA a partir da âncora; prédio de até 12 tiles de altura ficava invisível até a base entrar nessa margem pequena. Pesquisado antes de codar (mesma classe de bug documentada pro Tilemap Renderer da Unity, "Chunk/Cull Bounds"). Fix: margem calculada do MAIOR sprite do catálogo (`OBJECT_MAPPING`, nunca hardcoded), estendendo a varredura só ESQUERDA/BAIXO (únicas direções em que o sprite pode "entrar" na tela vindo de fora); lógica de corte, antes duplicada nos 2 métodos, virou 1 função só (02/09/2026) | ✅ completo, testado e validado em jogo pelo usuário (03/09/2026) | `ui/systems.py::TileRenderSystem._object_visible_tile_range`/`_object_scan_margin_tiles`, `tests/test_object_scan_margin.py`, ver `PROBLEMAS_ARQUITETURA.md` §102 |
| Rastreio de objetivo de quest ativa no mapa (M)/minimapa — círculo tracejado amarelo + preenchimento translúcido no alvo (`QUEST_CIRCLE_FILL_ALPHA`, 25% — era 50%/43%, reduzido a pedido do usuário 04/09/2026), tamanho SEMPRE fixo em px de tela (não escala com zoom/tp — 1ª versão usava raio real em tiles, virava gigante ou sumia dependendo do zoom, corrigido no mesmo dia com feedback+print do usuário): `TARGET_MARKER_RADIUS_PX=15` pra alvo real (mob, item, boneco de treino, marcador de transição) e `FRIENDLY_MARKER_RADIUS_PX` (= metade do ícone de quest "!"/"?") pra alvo de conversa (talk_to_npc, treinador), com "..." dentro. Boneco de treino (on_dummy) classificado junto do alvo real, não da conversa (é interação física). Tooltip no hover com quest+objetivo; círculos cortados na borda da janela via `set_clip` (M e minimapa). `ObjectiveDef` ganhou campo `map` (mapa do alvo, `reach_tile` continua usando `target` como filtro de mapa, convenção antiga preservada); `use_skill`(on_dummy)/`learn_skill` ganharam `params["dummy_id"]`/`params["trainer_id"]` (target continua == skill_id, autoritativo pro match da quest). Cross-map: procura transição no mapa atual que leve ao mapa do objetivo. Resolução 100% client-side (`spawn_points` já carregado localmente, sem mensagem de rede nova) (03-04/09/2026) | ✅ completo e testado; validação visual em jogo pendente | `content/quests_data.py::ObjectiveDef.map`, `ui/map_markers.py::collect_quest_circles`/`draw_quest_circle_marker`/`draw_quest_rect_marker`/`TARGET_MARKER_RADIUS_PX`/`FRIENDLY_MARKER_RADIUS_PX`, `ui/ui_helpers.py::draw_dashed_circle`/`draw_translucent_filled_circle`/`draw_dashed_rect`/`draw_box_tooltip`, `ui/minimap.py::Minimap.render` (`circles`, `set_clip`), `ui/map_overlay.py::MapOverlay.render` (`circles`, `set_clip`), `game.py::self._spawn_points`, `engine/map_loader.py` (`training_dummies` ganhou `id`), `tests/test_quest_map_tracking.py`, ver `PROBLEMAS_ARQUITETURA.md` §105 |
| Recompensa de item de quest agora concede de verdade no Inventory AO VIVO do servidor — antes só mandava o item pro cliente MOSTRAR (mesmo padrão declarado de LOOT_RESULT, só que loot já tocava o Inventory do servidor e quest não), sem nenhum jeito real do servidor ficar sabendo depois; bug real relatado pelo usuário: equipar a recompensa não mudava stats (equip valida por posição no Inventory do servidor, que nunca tinha o item) e o item sumia no relogin (servidor persiste a partir do próprio Inventory). Novo ponto único de verdade `WorldServer.grant_items_to_player(player_eid, entries)` — mesma mecânica de empilhar/caber que loot já usa (`LootProcessorMixin._stack_or_add_item`, extraída pra ser reaproveitada, nunca duplicada); qualquer sistema de recompensa futuro é obrigado a passar por ela (registrado em CLAUDE.md). Trade conferido: já estava correto (`trade_processor.py`, toca Inventory do servidor direto) (04/09/2026) | ✅ completo e testado | `server/world_server.py::grant_items_to_player`/`equip_item_from_inventory`, `server/loot_processor.py::_stack_or_add_item`/`_grant_loot_items_to_inventory`, `server/session.py::_handle_quest_turn_in`, `tests/test_quest_turn_in.py::test_item_concedido_entra_de_verdade_no_inventory_do_servidor`, ver `PROBLEMAS_ARQUITETURA.md` §106 |
| Rastreio de quest no mapa/minimapa considerava só o 1º `spawn_zone` de um mob com vários espalhados pelo mapa (ex: "Lobo" em 5 posições em map_1) — `_find_spawn_zone` (singular) parava no 1º match. Interação com NPC não priorizava entrega de quest quando o ÚNICO objetivo pendente era "falar com ESTE NPC" — `can_turn_in_after_talk` (previsão sem mutar QuestLog) já existia e já era usado certo por `QuestDialogSystem._open_dialog`, mas 4 lugares que decidem o que mostrar ANTES desse fluxo (`TrainerSystem._open_menu`/`_compute_options`, `QuestDialogSystem._render_list`/`marker_for`) chamavam `_get_completable_quests` sem passar o nome do NPC — menu de ação aparecia em vez de ir direto pra entrega, e a quest nem aparecia na lista de "Quests" (04/09/2026) | ✅ completo e testado | `ui/map_markers.py::_find_spawn_zones`/`_resolve_objective_shapes`, `ui/trainer_system.py::_open_menu`/`_compute_options`, `ui/quest_system.py::QuestDialogSystem._render_list`/`marker_for`, `tests/test_quest_map_tracking.py`, `tests/test_quest_talk_completable_priority.py`, ver `PROBLEMAS_ARQUITETURA.md` §107/§108 |
| `use_skill(on_dummy)` no rastreio de quest exigia `params["dummy_id"]` (apontar um boneco de treino ESPECÍFICO) — decisão desnecessária, revertida: boneco de treino é 100% genérico pro próprio jogo (`on_dummy` só checa se o alvo tem o componente, nunca qual boneco). Unificado com o mecanismo de mob: `kill`/`auto_attack_hit`/`use_item_on_target`/`collect_item` com `target` batendo no nome ("Boneco de treino") ou raça ("Mecânico") do boneco — mesma identidade real de `entity_factory.py::create_training_dummy` — circulam TODOS os bonecos do mapa, sem id nenhum; `use_skill(on_dummy)` idem. Achado pelo usuário ao criar uma quest nova usando `auto_attack_hit` no boneco, que não tinha resolução nenhuma antes (04/09/2026) | ✅ completo e testado | `ui/map_markers.py::_hostile_target_circles`/`_dummy_positions`/`_TRAINING_DUMMY_NAME`/`_TRAINING_DUMMY_RACE`, `content/quests_data.py` (3 quests reais sem `dummy_id`), `engine/map_loader.py`/`server/world_server.py` (`training_dummies` volta a `(x,y)`), `tests/test_quest_map_tracking.py`, ver `PROBLEMAS_ARQUITETURA.md` §109 |
| Velocidade de ataque não atualizava depois de relogar, mesmo com arma equipada de verdade (painel mostrava default da classe, ex. 2.60s, em vez do real da arma, ex. 2.1s — E o intervalo de ataque real em combate também ficava errado) — `client/save_sync_handlers.py::_restore_save_state` escrevia `CombatStats.base_attack_interval` na mão em vez de chamar `engine.stats_system.sync_attack_interval()` (ponto único de verdade, já importado mas não usado ali), então nunca disparava `_recalculate_effective_stats()` — o campo EFETIVO (`attack_interval`, o que combate/UI realmente leem) ficava travado até algum OUTRO modifier de item disparar recálculo por acidente; arma sem modifiers (ex. espada de treinamento) nunca disparava, erro sobrevivia a sessão inteira (04/09/2026) | ✅ completo e testado | `client/save_sync_handlers.py::_restore_save_state`, `tests/test_restore_attack_interval.py`, ver `PROBLEMAS_ARQUITETURA.md` §111 |
| Personagem oscilava (zigue-zague) entre um alvo de combate travado (ex: boneco de treino) e um NPC amigável clicado — clicar no NPC é consumido inteiro pelo sistema dele (nunca chega em `MouseTargetingSystem`), então `CombatState.chase_suppressed` (flag que já suprime só o auto-walk de volta ao alvo, sem tocar `target_entity_id`/`is_pursuing` — é o que já permite o kite do arqueiro) nunca era ligada ao interagir com NPC, e o auto-chase competia com o walk-to-NPC todo frame. 4 sistemas (Trainer/Blacksmith/QuestDialog/Shop) tinham cada um sua própria cópia quase idêntica do cálculo de "andar até o NPC" — nenhuma suprimia a perseguição; centralizado por pedido do usuário numa função só (04/09/2026) | ✅ completo e testado | `ui/npc_interaction.py::walk_to_npc`/`suppress_chase` (novo), `ui/trainer_system.py`/`ui/crafting_system.py`/`ui/quest_system.py`/`ui/systems.py::ShopSystem` (usam a função nova, cópias antigas removidas), `tests/test_npc_interaction_chase_suppress.py`, ver `PROBLEMAS_ARQUITETURA.md` §112 |
| Primeiro teste de carga real (`tools/loadtest/`) achou 2 causas de travamento sob carga, ambas confirmadas em código (usuário rejeitou explicitamente a hipótese de "culpa do Windows"/contenção de CPU sem prova): (1) `EnemyAISystem._active_mobs_this_tick` consultava `SpatialHash.nearby()` 1x por JOGADOR, mesmo com vários jogadores aglomerados no MESMO lugar (spawn/cidade — `nearby()` depende só de `tile // cell_size`, não da posição exata, então players na mesma célula recebem resultado idêntico); (2) `gc.collect()` manual periódico (a cada ~10s, já rodava fora do tick desde antes por causa de `gc.disable()`) ainda escaneava todo objeto vivo desde o boot (mapas/catálogos/entidades, nunca viram lixo), 20-50ms de pausa por disparo (05/09/2026) | ✅ completo e testado; verificação de ganho real com novo teste de carga pendente | `engine/world_systems.py::_active_mobs_this_tick` (`_nearby_cache` por célula), `server/main.py` (`_gc.collect()`+`_gc.freeze()` após o mundo carregar, antes de aceitar conexão), `tests/test_ai_prefilter_spatial_index.py::TestPrefilterNearbyDedupByCell`, `tests/test_gc_freeze_startup.py`, ver `PROBLEMAS_ARQUITETURA.md` §113 |
| 2 rodadas de auditoria multi-agente (3 especialistas cada, arquiteto MMORPG/engenheiro de incongruências/QA de performance) nos 10 sistemas mais custosos acharam 4 correções reais: guard morto em `_tick_bush_target_clear` (nunca disparava, função escaneava o mundo à toa); 3 call-sites com checagem O(n) de jogador (`dict.values()`) trocados por checagem de componente O(1); 2 das 3 varreduras redundantes de `TileMovement` por tick fundidas (a 3ª precisa continuar separada — roda depois do `SpawnZoneSystem` criar mob novo); `_combat_targets` vazava alvo entre mapas/instâncias simultâneas (bug de corretude, não só performance — AoE podia acertar alvo de outro mapa por coincidência de coordenada). Rodada 2 corrigiu 2 propostas da Rodada 1 que tinham bug real se implementadas ao pé da letra (05/09/2026) | ✅ completo, testado (revert-to-confirm nos 4) E verificado com teste de carga real de 250 bots: ganho apples-to-apples na mesma escala (~250-267 players) de −12% a −99,7% dependendo do sistema, tick médio 40.27ms→25ms (volta a caber no orçamento de 33ms), % de tick travado a 225-250 players 60%→4% | `server/world_server.py` (`_tick_bush_target_clear`, `ActiveRegen`/`ActiveManaRegen`/`post_tick_bookkeeping`, `index_build`, `_combat_targets`), `tests/test_bush_stealth.py::TestBushTargetClearGuard`, `tests/test_consumables.py::TestActiveRegenPlayerCheckIsComponentBased`/`TestPostTickBookkeepingNeverMisclassifiesPlayerAsMob`, `tests/test_server.py::TestPreMobTargetSurvivesFusionWithIndexBuild`, `tests/test_faction.py::TestCombatTargetsScopedByMap`, ver `arquitetura/analise_performance_rodada1.md`, `arquitetura/medicao_antes_depois_fixes.md` (dashboard visual: https://claude.ai/code/artifact/24895768-ecf9-4256-98d9-c18c882f11bc), `PROBLEMAS_ARQUITETURA.md` §114 |
| Rampa de conexão mais lenta (5 bots/15s, teste de carga real) expôs `skill_requests` como ~50% do custo do tick (avg=30ms, p99=100ms) a 250 jogadores — nunca visto antes porque os bots, até então, ficavam presos lutando só contra o boneco de treino (§115), volume de skill em paralelo era baixo. Causa raiz: `_snapshot_combat_targets` (tirava foto de HP/efeitos de TODO combatente do mapa a cada skill usada, só pra diffar depois) rodava até 3× por cast (request + completion + hit de projétil), 2 dessas 3 vezes 100% desperdiçadas (dano ainda nem tinha acontecido). Auditoria de 3 agentes (arquitetura/qualidade de código/coesão vs. mercado) nota máxima na 1ª passada. 5 correções: (1) pula a foto quando comprovadamente nunca é usada; (2) 8 checagens O(n) `eid in dict.values()` restantes → O(1) por componente; (3) `_combat_targets` ganha fast path reaproveitando o índice por mapa que `index_build` já constrói (`_combatants_by_map`/`_players_by_map`, `None` até o 1º tick real — fixture de teste sem tick cai no caminho antigo, sem quebrar); (4) `LAST_DAMAGE_AMOUNTS`/`LAST_EFFECTS_APPLIED` (novos, irmãos de `LAST_DAMAGE_OUTCOMES`) populados por `apply_damage_core`/`apply_effect` no momento em que aplicam — `_snapshot_combat_targets` REMOVIDA por completo, 0 call sites restantes; (5) `_collect_player_effects` vira dict (era lista, 2 varreduras O(P) por SESSÃO em `session.py`) (06/09/2026) | ✅ completo, testado E verificado com novo teste de carga real de 250 bots: tick médio 60,5ms→19-23ms, % de ticks lentos 73%→6,1%, `skill_requests` 49,9%→1,7% do tick em média — suíte com 5 testes novos, revert-to-confirm em 5 pontos distintos (incluindo 2 gaps de cobertura achados NA HORA: `LAST_EFFECTS_APPLIED` e dano reportado de projétil não tinham teste nenhum, corrigido) | `engine/core_systems.py` (`LAST_DAMAGE_AMOUNTS`/`LAST_EFFECTS_APPLIED`), `server/world_server.py` (`_combat_targets`, `_combatants_by_map`/`_players_by_map`, `_collect_player_effects`), `server/skill_processor.py`, `server/spell_completion_processor.py`, `server/session.py`, `server/combat_processor.py`, `tests/test_faction.py::TestCombatTargetsFastPathAfterRealTick`, `tests/test_immunity_feedback.py::TestNovaCongelanteReportaDanoEEfeitoAplicado`, `tests/test_bg_bot.py::test_bola_de_fogo_reporta_dano_correto_no_skill_result`, ver `PROBLEMAS_ARQUITETURA.md` §116 |
| Picos residuais de `skill_requests` (até 197ms, 78x em 22min) mesmo após o fix acima — instrumentação temporária mediu 242 dos 250 bots mandando `CAST_SKILL` no MESMO tick, custo por pedido confirmado ótimo (0,2-0,4ms), problema é volume. Auditoria de 3 agentes achou cadeia causal: reconexão em massa (recorrência do §113, SQLite, ainda aberto) + backoff de reconexão do bot de teste SEM jitter (`2**tentativa` determinístico — thundering herd clássico) + rampa de conexão sem jitter dentro do lote + "pensar imediato ao reconectar" (§116) transformam reconexão sincronizada em `CAST_SKILL` sincronizado. Causa imediata é majoritariamente artefato da ferramenta de teste, mas os 3 agentes acharam lacuna real independente: `_process_skill_requests`/`queue_skill` não tinham NENHUM orçamento, ao contrário de `MAX_PATHFINDS_PER_FRAME` (já existe pra IA de mob). 2 correções: jitter no bot/rampa de teste (`tools/loadtest/`); rate-limit por PLAYER (nunca por tick global — penalizaria N jogadores legítimos agindo juntos) em `_process_skill_requests`, teto `MAX_SKILL_REQUESTS_PER_PLAYER_PER_TICK=3` (06/09/2026) | ✅ completo, testado — 3 testes novos, revert-to-confirm confirmou os 3 pegando a regressão, suíte completa (1510 testes) verde | `server/world_server.py` (`MAX_SKILL_REQUESTS_PER_PLAYER_PER_TICK`, `queue_skill`, `_skill_requests_this_tick_by_player`), `server/skill_processor.py` (reset do contador), `tools/loadtest/swarm.py::_staggered_run`, `tools/loadtest/bot.py::_reconnect_backoff`, `tests/test_server.py::TestSkillRequestRateLimitPorPlayer`, ver `PROBLEMAS_ARQUITETURA.md` §117 |
| Trava com ZERO jogadores (ticks >33ms, "culpado" mudando entre 17 sistemas, minutos depois de um teste de bots). 2 rodadas de agentes — 1ª rodada teve nota 10/10 dada sem validar contra a suíte (regra de processo corrigida: diagnóstico só fecha com "implementei e rodei os testes", nunca só leitura); correção proposta pra `SpawnZoneSystem` (early-return sem player) quebrou 14 testes reais e foi REVERTIDA sem commitar. 2ª rodada (3 agentes, exigência de validação real): confirmou lock (`asyncio.Lock`) + `PRAGMA journal_mode=WAL` pro SQLite (os dois têm que andar JUNTOS — WAL sozinho mede PIOR que nada, achado real via script de carga); e achou a causa raiz REAL do travamento (não era o `(0,0)` proposto antes, que é código morto — `ACTIVATION_RADIUS=999999` já desativa esse culling de propósito desde 24/05/2026): `SpawnZoneSystem._get_occupied()` ignorava o índice `tile_movement_by_map` já disponível e fazia scan do MUNDO INTEIRO — 3ª ocorrência da mesma classe de bug já corrigida em `TileValidationSystem`/`EnemyAISystem` (§34/§35) (06/09/2026) | ✅ completo, testado por 2 agentes independentes + suíte completa rodada por mim (1510 testes, 0 regressão); `data/game.db` real limpo de 12 contas de teste deixadas por script de validação; verificado com novo teste de carga: 875→11 ocorrências de tick lento no período ocioso, tick médio ocioso ~5ms, CPU ociosa ~11-13% | `server/auth.py::_get_conn` (WAL; `SessionManager._save_lock` original substituído pelo `_write_lock` do §119), `engine/world_systems.py::SpawnZoneSystem._get_occupied` (usa `_tile_movement_on_map`), ver `PROBLEMAS_ARQUITETURA.md` §118 |
| §113 fechado de vez — vazamento de conexão no PRÓPRIO bot de teste (`tools/loadtest/bot.py::run()` nunca fechava o WebSocket da tentativa anterior numa reconexão) era a causa real da amplificação (1181 reconexões pra 250 bots, quase 5x), não (só) contenção de SQLite isolada — sessão "zumbi" (LOGIN completando numa conexão já abandonada pelo cliente) bloqueava toda reconexão seguinte com `LOGIN_ERROR "already_online"` até o `ping_timeout` (60s) derrubar. 3 agentes convergiram, de forma independente, na mesma correção: fechar a conexão no bot + `_write_lock` único em `server/auth.py` (vive no módulo que abre a conexão, não em `SessionManager` — cobre `register`/`create_character`/`delete_character`/`save_character`, nunca leitura). Achado extra: corrigida de quebra uma corrida real (TOCTOU) de nome de personagem duplicado em `_create_character_sync` (06/09/2026) | ✅ completo, testado — 2 testes novos (`test_auth_write_lock.py`, `test_loadtest_bot_reconnect.py`), revert-to-confirm nos 2 mecanismos, suíte completa (1513 testes) rodada por múltiplos agentes + por mim após revisar o diff (3 agentes editaram os mesmos arquivos concorrentemente) | `tools/loadtest/bot.py::run`, `server/auth.py` (`_get_write_lock`/`_write_lock`), `server/session.py` (remoção do `_save_lock`), ver `PROBLEMAS_ARQUITETURA.md` §119 |
| `_write_lock` único do §119 regrediu sob carga SUSTENTADA (diferente do burst medido em §119): reconexões 1181→2768, conectados no fim 225/250→109/250, mesmo com ZERO erros reais de SQLite. Causa: fila FIFO única misturava `register` (leve, sensível a latência de reconexão) com `save_character` (pesado, até ~600KB de fog, dispara em todo disconnect) — o disconnect que causava a reconexão atrasava, ele mesmo, a reconexão seguinte; medido que a fila cresce O(n) com o tamanho da rajada, ultrapassando o timeout de 10s do bot e realimentando o próprio congestionamento. 2 dos 3 agentes escalados atingiram rate limit da API a meio caminho (falha externa, não veredito sobre a abordagem). Correção: `_write_lock` estreitado pra só `save_character`, novo `_identity_lock` pra `register`/`create_character`/`delete_character`; fast-path de `SELECT` de existência em `register()` antes do lock (bot manda REGISTER em toda reconexão mesmo já tendo conta); conexão SQLite persistente por thread (`_conn_cache`) + 2 executores dedicados de 1 worker, em vez de 1 `sqlite3.connect()` por chamada espalhado pelo pool padrão; `PRAGMA synchronous=NORMAL` (seguro com WAL) (06-07/09/2026) | ✅ completo, testado e verificado com o mesmo teste de 250 bots/500s sustentado: reconexões 2768→652 (abaixo até do baseline pré-§119, 1181), conectados no fim 109/250→242/250, tick lento ocioso pós-desconexão 1 ocorrência isolada (era 875 antes do §118) — rajada residual menor no meio do teste não investigada a fundo, registrada como possível trabalho futuro | `server/auth.py` (`_write_lock`/`_identity_lock`, `_conn_cache`, `_write_executor`/`_identity_executor`, `register` fast-path), `tests/test_auth_write_lock.py`, ver `PROBLEMAS_ARQUITETURA.md` §120 |
| Top-5 gargalos de tick a 250 players, medidos no log do §120: `EnemyAISystem` (40,6% do tick, pico de 76,4ms), `skill_requests` (P99 35-73ms mesmo com rate-limit do §117), `aoi_collect` (75% vindo de 2 dirty-checks que recalculavam tudo todo tick), `player_attacks`/`index_build` (estáveis). 3 agentes (arquitetura/qualidade/coesão-mercado) escalados só pra PROPOR (proibido editar arquivo) — sugestões trazidas pro usuário discutir antes de qualquer implementação (2 pontos de divergência real entre agentes, decididos pelo usuário: orçamento agregado de skill_requests rejeitado por poder penalizar ação coordenada legítima; teto de mob/tick aceito mesmo assim). 5 correções implementadas: `World.get_component()` com `try/except` (16% mais rápido, propaga por todo o ECS); imports locais dentro de loop hot-path subidos pro topo (3 arquivos); `SkillLevels.dirty`/`pending_level_ups` — dirty-flag setado por `grant_skill_xp` (único escritor) em vez de recompute-then-diff; `_player_bush_tile_cache` — gate de posição (não dirty-flag genérico, por decisão consciente: bush não tem "dono" que escreve nele) antes de `_is_on_bush_tile`; `EnemyAISystem._apply_active_mob_budget()` — orçamento com carryover rotativo pro `loop_mobs`, mesmo princípio de `MAX_PATHFINDS_PER_FRAME` (06-07/09/2026) | ✅ completo, testado (9 testes novos, revert-to-confirm nos 3 mecanismos com lógica nova, suíte completa 1524 passed) e verificado com novo teste de carga: os 3 fixes diretos (`aoi_collect`/`skill_requests`/`player_attacks`) reduziram custo médio 20-55% e p99 39-53%, consistente em TODOS os blocos medidos; `ai_bundles`/`index_build` pioraram no teste (7,7→9,6ms / 1,5→2,1ms), mas por confundidor identificado — o teste rodou com 1 mapa ativo a mais em média (6 vs 5, provável efeito de uma turbulência de reconexão à parte, não dos fixes) — não uma regressão real; orçamento de mob (item 5) não chegou a disparar neste teste (população nunca passou de 59, teto é 60), segue sem confirmação real | `engine/world.py::get_component`, `server/bush_zone_processor.py`, `server/skill_processor.py`, `server/combat_processor.py`, `engine/components.py::SkillLevels`, `engine/stats_system.py::grant_skill_xp`, `server/world_server.py` (`_sync_player_skill_levels_dirty`, `_sync_player_bush_dirty`), `engine/world_systems.py::EnemyAISystem._apply_active_mob_budget`, ver `PROBLEMAS_ARQUITETURA.md` §121 |
| Diagnóstico geral (3 agentes, usuário sem parâmetro de comparação) achou 1 evento real preocupante: tick de 365,8ms (240 players) com `skill_requests` sozinho em 350,5ms — trava a simulação pra TODOS por 1/3 de segundo. Investigado se era artefato do bot de teste antes de mexer em código: caiu na rampa de conexão inicial, matemática bate com quase todo mundo batendo o teto do rate-limit por jogador (§117) ao mesmo tempo — gap real (rate-limit não protege contra a SOMA), mas mais raro com jogadores reais (bot tem 2 comportamentos que favorecem a coincidência: chance de skill por tick independente + age imediato ao spawnar). Correção: orçamento de TEMPO (não contagem) pra `_process_skill_requests` inteira — mesmo princípio de `MAX_PATHFINDS_PER_FRAME`/`_apply_active_mob_budget` (nunca descarta, só adia), mas com relógio real porque custo por pedido não é fixo o bastante pra um teto de N garantir um teto de ms (07/09/2026) | ✅ completo, testado (3 testes novos, revert-to-confirm confirmado, suíte completa 1527 passed) e verificado com novo teste de carga: pico de `skill_requests` 350,5ms→25,1ms (−93%), pior tick do teste inteiro 365,8ms→105,4ms (e o novo pior nem é mais `skill_requests`, é `ai_bundles`, já catalogado), tick lento (players>100) 10,85%→4,75%, severos (>100ms) 0,24%→0,04% | `server/skill_processor.py::_process_skill_requests`, `server/world_server.py::SKILL_REQUESTS_TIME_BUDGET_MS`, `tests/test_server.py::TestSkillRequestsTimeBudget`, ver `PROBLEMAS_ARQUITETURA.md` §122 |
| Migração SQLite → PostgreSQL/`asyncpg` — decisão do usuário de ir pra servidor dedicado em nuvem (Linux, múltiplos núcleos, sharding futuro). Toda a maquinaria de lock/executor das §113-122 (existia só pra contornar SQLite aceitar 1 escritor por vez) removida — Postgres aceita escritores concorrentes de verdade, e um lock de 1 processo nunca protegeria múltiplos processos futuros mesmo assim. Unicidade de nome de personagem (TOCTOU do §119) agora é um índice único NO BANCO (`characters_name_lower_idx`, case-insensitive), não lock de app — correto com múltiplos processos. Achado real durante o desenvolvimento: pool `asyncpg` vaza conexão se não fechado ENQUANTO o loop que o criou ainda roda (`unittest.IsolatedAsyncioTestCase` cria loop novo por teste) — sem isso, suíte inteira esgotava `max_connections` do Postgres por volta do teste #1500 (07/09/2026) | ✅ completo, testado (suíte completa 1527 passed, 2 failed pré-existentes/timing flake sem relação — `tests/test_auth_write_lock.py` removido e substituído por `tests/test_auth_concurrency.py`, 14 arquivos de teste ganharam `AuthPoolCleanupMixin`), revert-to-confirm real no índice único (script com `asyncio.Event` forçando a corrida: sem índice, 2 linhas duplicadas; com índice, 1) | `server/auth.py` (reescrito), `server/main.py`, `server/grant_gm.py`, `server/session.py`, `tools/loadtest/provision.py`, `tools/migrate_sqlite_to_postgres.py` (novo, migração 1x do banco antigo — achou ~45 grupos de nome duplicado no banco dev real, tratamento não-destrutivo por sufixo, rodar contra dado real ainda pendente de decisão do usuário), `tests/helpers.py::isolate_auth_schema`/`restore_auth_schema`/`AuthPoolCleanupMixin`, ver `PROBLEMAS_ARQUITETURA.md` §123 |
| IA de combate por classe pros dois "bots" do projeto (08/09/2026, pedido do usuário) — **bot de teste de carga** (`tools/loadtest/bot.py`, cliente de rede real): cadência de MOVIMENTO separada da de DECISÃO (~0,29s/passo, igual ao fallback de velocidade real do servidor — antes as duas eram a mesma, 0,6-1,8s, bot andava 3-9x mais devagar que jogador real e parecia "teleportar" pelo hiato longo entre passos); mago/guerreiro/arqueiro ganham prioridade tática (Interceptar/Vitória Iminente/Bola de Fogo/Bloco de Gelo/Polimorfia/Picada de Escorpião, conforme HP e distância) em vez de skill aleatória; kite real com histerese (foge a ≤30% HP, só reengaja a ≥50%) pras 3 classes agora, inclusive guerreiro (sem skill de escape, só corre mesmo); arqueiro ganha arco+aljava de batalha (999 flechas) via `provision.py`. **Bot de BG** (`server/bot_ai_system.py`, entidade ECS 100% servidor): mesmo espírito, só que com acesso DIRETO a `CombatStats`/posição (sem rede/AOI no meio) — guerreiro usa Executar (alvo≤30%)/Fatiador de Corpos (2+ inimigos num raio)/Vitória Iminente (HP<90%)/Interceptar, por prioridade; mago usa Nova Congelante (dano+enraíza em área, ofensiva E defensiva) preferencialmente sobre Bola de Fogo com 2+ alvos; fuga com histerese reaproveita `_advance_toward` mirando `bot.route[0]` (a própria base); minion só vira alvo com HP≤20% (last-hit pra XP, nunca gasta hit em minion saudável que a lane resolve sozinha) — player inimigo sempre tem prioridade sobre minion; dive-gate corrigido pra também exigir HP PRÓPRIO >30% (antes só olhava o alvo — bot podia morrer de graça pra torre mesmo saudável nunca sendo o problema). Investigação corrigiu um engano da 1ª pesquisa: XP de instância (BG) já funciona de verdade via abate de minion (`WorldServer._death_handler.consume_xp()`→`grant_instance_xp`, achado perdido antes por causa de um alias de import) — bot ganha skill 2-6 ao longo da partida igual jogador real, nenhuma progressão precisou ser criada. Poção/comida ficou de fora por decisão consciente (adiado, ver PROBLEMAS_ARQUITETURA.md) | ✅ completo e testado (suíte completa 1540 passed, 2 failed pré-existentes/timing flake sem relação); validação em jogo real da BG pendente | `tools/loadtest/bot.py`, `tools/loadtest/provision.py`, `server/bot_ai_system.py` (reescrito), `engine/components.py::BotControlled.is_retreating`, `tests/test_bg_bot.py` (12 casos novos/atualizados), ver `PROBLEMAS_ARQUITETURA.md` §125 |
| Interceptar: dash completava e DEPOIS era desfeito (snap de volta), mais comum perto de parede/torre (09/09/2026, relatado pelo usuário) — investigado com 3 agentes em paralelo antes de codar. Causa raiz: `server/session.py::_handle_move` mandava correção de posição pro próprio cliente sempre que `move_player()` rejeitava um `MOVE`, usando `TileMovement.current_tile_x/y` — campo que só atualiza quando o tween do dash COMPLETA, então durante o dash aponta pra tile de ANTES dele. Um MOVE residual de WASD (mais comum perto de obstáculo) rejeitado especificamente por "dash em andamento" (guard do §87) gerava essa correção com posição stale; chegando ao cliente depois da animação do dash já ter terminado localmente, era aplicada por engano — não é pacote fora de ordem (TCP garante ordem), é conteúdo stale num pacote corretamente ordenado. Não é regressão de §84/§87 (confirmados intactos, cobrem causas diferentes) | ✅ completo e testado (suíte completa 1544 passed, 1 failed pré-existente/timing flake sem relação); validação manual em jogo pendente | `server/session.py::_handle_move`, `tests/test_interceptar_move_correction_race.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §126 |
| Interceptar (e qualquer skill ofensiva): dash/reengajamento e depois volta pro tile do clique de chão, em loop (09/09/2026, relatado pelo usuário após validar §126) — mecanismo DIFERENTE do §126. Causa raiz: `CombatState.chase_suppressed` (liga no clique de chão, impede o auto-chase de competir com `PlayerAutoMove.ground_target`) era desligada por QUALQUER skill ofensiva sem nunca cancelar o `ground_target` pendente — mesma classe de bug já corrigida pra clique em NPC (§112), nunca coberta pro caso "usar skill". A flag estava duplicada em 6 lugares; 2 (o handler genérico de tecla de skill e Bola de Fogo) nunca cancelavam o clique pendente — é onde Interceptar batia. Fix: `engine/utils.py::reengage_chase()`, ponto único de verdade (inverso de `suppress_chase`), chamado nos 6 lugares — nunca toca `target_entity_id`/`is_pursuing` | ✅ completo e testado (suíte completa 1548 passed, 1 failed pré-existente/timing flake sem relação); validação manual em jogo pendente | `engine/utils.py::reengage_chase`, `engine/skill_handlers.py`, `ui/systems.py` (3 sites), `ui/spell_system.py`, `client/save_sync_handlers.py`, `tests/test_reengage_chase.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §127 |
| Interceptar não deve ser bloqueado por entidade (player/mob/minion/NPC) no caminho, só por obstáculo sólido de verdade — pedido do usuário (09/09/2026). `is_tile_walkable` (usada por Interceptar, e por `move_player` pra MOVE normal) checava solidez de terreno E ocupação por entidade juntas; Tiro Repulsivo já ignorava entidade, mas com um `_is_solid` local próprio em vez desta função compartilhada. Fix: `ignore_entities=True` novo em `is_tile_walkable`/`TileValidationSystem.is_tile_walkable` — pula só o bloco de ocupação, nunca solidez real nem piso/elevação; usado nos 2 pontos do Interceptar (`_dash_path_clear` + seleção de tile de destino), `move_player` intocado | ✅ completo e testado (suíte completa 1551 passed, 2 failed pré-existentes/timing flake sem relação — inclui regressão pega e corrigida em 3 stubs fake de `tests/test_client_ui.py` que não aceitavam o parâmetro novo); validação manual em jogo pendente | `engine/world_systems.py::is_tile_walkable`/`TileValidationSystem.is_tile_walkable`, `engine/skill_handlers.py` (`_dash_path_clear`, `_skill_interceptar`), `tests/test_interceptar_ignores_entities.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §128 |
| Skill temporária da BG (sem preferência real salva, ex: Golpe Debilitante) roubava o slot de hotbar de uma skill real que ainda ia desbloquear (ex: Vitória Iminente) — relato antigo, nunca fechado (09/09/2026). Causa: skill sem preferência própria caía no "1º slot vazio" no momento em que aparecia, podendo coincidir com o slot preferido de outra skill que só desbloqueia depois; quando essa chegava, o slot já estava tomado e a regra "nunca rouba slot ocupado" a jogava pra outro lugar — permanentemente, mesmo depois da interlopeira sair ao fim da BG (regra "skill já conhecida nunca é remexida"). Fix, decisão do usuário: impedir o roubo na origem — `ui/skill_slot_prefs.py::preferred_slot_for_skill` agora faz o fallback completo internamente, pulando qualquer slot vazio que seja preferência salva de OUTRA skill (só usa como último recurso se não houver nenhum seguro); 3 chamadores simplificados, confiam 100% no retorno | ✅ completo e testado (suíte completa 1553 passed, 2 failed pré-existentes/timing flake sem relação); validação manual em jogo pendente | `ui/skill_slot_prefs.py::preferred_slot_for_skill`, `ui/trainer_system.py`, `client/network_handlers.py` (2 sites), `tests/test_hotbar_bg_leak.py` (2 casos novos), ver `PROBLEMAS_ARQUITETURA.md` §129 |
| KDA da BG (fila real) relatado "Kills: 2" no HUD ao vivo pra 1 kill de verdade, só de vez em quando (09/09/2026). Investigado sem achar o mecanismo exato de `players_killed` dobrar — mas confirmado por teste que `_handle_player_death` (`server/respawn_system.py`, único ponto que credita kill/assist/death de player) podia ser chamado 2x no MESMO tick quando o golpe final é auto-attack PvP: o braço de PvP credita certo, e o loop genérico de "qualquer fonte que zerou HP" (pensado pra DoT/skill/mob) roda depois sem saber que aquela morte já foi tratada. Não dobrava `players_killed` (log de contribuição já consumido), mas dobrava `CharStatsTracker.deaths` da vítima incondicionalmente — bug real, mas diferente do relatado. Decisão do usuário: corrigir esse mesmo assim; avisar na próxima ocorrência do "Kills: 2" com horário + contexto (sozinho ou mais gente do time no alvo) | 🟡 bug de deaths confirmado e corrigido (guard de idempotência em `_handle_player_death`, via `GhostState.is_dead`); mecanismo real do "Kills: 2" ainda NÃO encontrado — pendente de nova ocorrência com mais contexto do usuário | `server/respawn_system.py::_handle_player_death`, `tests/test_bg_kill_credit.py::TestPlayerDeathNotDoubleProcessed` (2 casos novos), ver `PROBLEMAS_ARQUITETURA.md` §130 |
| Animação BASE de personagem (idle/andar × 4 direções × classe), implementação incremental (09/09/2026, pedido do usuário — começou com 1 sprite só, `guerreiro_idle_down.png`). Convenção nova: `assets/sprites/{class_id}_{idle\|run}_{down\|up\|left\|right}.png` (class_id em português, mesmo valor já usado em `CharacterStats`/`RemoteControlled` — sem tabela de tradução nova), sheet cortado em frames de 32px de largura (mesmo corte que Camuflagem já usa pra "run", mas aqui "idle" TAMBÉM é sheet animada, não frame único parado). Direção calculada 1x por passo real em `TileMovementSystem` (delta target-current via `facing_from_delta`, novo em `engine/utils.py`), gravada em `TileMovement.facing` (campo novo, client-only). `RenderSystem` tenta o sprite antes do retângulo de sempre — arquivo ausente cai pro retângulo (regressão zero enquanto a arte não existir); "left"/"right" sem arquivo próprio herdam espelho horizontal do lado oposto. Bug pego testando (não em produção): espelhamento recursivo — "left" sem arquivo tentava espelhar "right" sem arquivo, que tentava espelhar "left" de volta, recursão infinita — corrigido lendo o lado espelhado sempre DIRETO do disco, nunca via a própria função de fallback. **Follow-up no mesmo dia** (usuário validando visualmente): sprite real (32x48) é quase o dobro do retângulo antigo (24px) — nameplate/HP bar/ícones de efeito (todos empilhados a partir do mesmo `_hud_top_world_y`) cobriam a metade de cima do sprite. Fix: mesmo padrão já usado pro sprite de objeto de mapa (torre, `renderable.sprite_id`) — topo do HUD passa a acompanhar a altura REAL do sprite desenhado (`_char_sprite_h_for_hud`, setado no branch de desenho e resetado a cada entidade do loop pra nunca herdar valor de uma entidade anterior), não mais `renderable.height/2` fixo. **Follow-up 2 no mesmo dia** (usuário reportou: andando em QUALQUER direção, "em alguns momentos" a animação idle aparecia quando devia ser só o retângulo de fallback do "run"). Causa raiz: `PlayerInputSystem` (ordem 5, `game.py`) roda ANTES de `TileMovementSystem` (ordem 23) todo frame — no frame em que um passo termina (`progress>=1.0`, `is_moving` vira False), o passo SEGUINTE só é iniciado pelo input no PRÓXIMO frame; numa caminhada contínua (tecla segurada), isso deixa exatamente 1 frame por passo com `is_moving=False` no MEIO da caminhada, escolhendo "idle" pra animação naquele frame. Fix: `TileMovement.anim_walk_grace_s` (campo novo, client-only, cosmético — NUNCA lido por gameplay/combate) — `TileMovementSystem` seta pra `ANIM_WALK_GRACE_S=0.1s` no instante em que o passo termina e decrementa todo frame; `engine/character_animation.py::resolve_anim_state()` (novo, ponto único de verdade pra "o que conta como andando" na animação) trata `is_moving OU grace>0` como "run". **Follow-up 3 no mesmo dia** (usuário trocou TODOS os sprites de 32×48 pra 96×80, pra caber animação de ataque futura sem precisar recortar tudo de novo depois) — `CHAR_TILE_SIZE` (largura de corte) virou 96; frames NÃO são quadrados (96 largura × 80 altura), então a detecção automática por altura do sheet (considerada e descartada nesta mesma conversa) não serve — decisão do usuário: constante fixa, não derivada. `DEFAULT_FRAME_MS` também subiu de 65→78 (20% mais lento, pedido separado do usuário). Nenhuma mudança de código fora do corte foi necessária (âncora de pé e topo do HUD já liam a altura do sprite dinamicamente, ver Follow-up 1). 4 arquivos novos `guerreiro_attack_{down,up,left,right}.png` já na pasta — **ainda NÃO integrados** (sem state "attack" no código, sem gatilho decidido — fora de escopo até o usuário pedir). **Follow-up 4 no mesmo dia** (usuário definiu o gatilho da animação de golpe): dispara em auto-attack OU skill sem sprite própria (fallback genérico "attack" — skill COM sprite própria, ex: futuro `guerreiro_golpe_poderoso_down.png`, usa a própria), no momento em que o servidor confirma dano de verdade (`COMBAT_RESULT`, `damage>0`, nunca DoT/HoT) — nunca no clique/input local, sempre na confirmação. Toca 1 ciclo completo do sheet resolvido (contagem de frames automática, sem duração hardcoded) e volta sozinho pro idle/run. Novo: `TileMovement.anim_attack_state`/`anim_attack_started_ms` (client-only); `engine/character_animation.py::trigger_attack_anim()` (liga), `resolve_attack_state_name()` (decide sid-próprio vs "attack" genérico), `resolve_display_state()` (substituiu `resolve_anim_state()` como ponto único de verdade do RenderSystem — golpe tem prioridade sobre andando/parado enquanto ativo, expira sozinho por tempo decorrido); gatilho chamado de UM lugar só, `client/remote_entity_handlers.py::_apply_combat_result`, cobrindo atacante local E remoto, contra mob OU player. **Follow-up 5 no mesmo dia** (usuário reportou: parado atacando um mob atrás dele, personagem continuava "olhando" pra última direção que tinha andado — `facing` só era gravado durante um passo real). Fix: `TileMovementSystem`, quando PARADO (`not is_moving`) e com `CombatState.target_entity_id` setado, recalcula `facing` a partir do delta posição-do-alvo menos posição-própria (mesma `facing_from_delta`, reusada — nunca duplicada), todo tick — acompanha um alvo que se move ao redor de quem está parado. Movimento real continua tendo prioridade (regressão coberta por teste: andando numa direção com alvo em OUTRA, `facing` segue o movimento, não o alvo). Roda pra qualquer entidade com `CombatState` (não só player) — mob/mob remoto ainda sem sprite direcional, inofensivo por enquanto; remoto (outro jogador) não tem `CombatState` replicado no cliente, então esse fix só afeta o PRÓPRIO jogador local por ora. **Follow-up 6 no mesmo dia** (usuário reportou: andando pra baixo com alvo selecionado ao NORTE, direção oposta, piscava de frente pro alvo no meio da caminhada) — mesma causa raiz do flicker idle/run já corrigido (frame único de `is_moving=False` entre 2 passos de uma caminhada contínua): o novo check de facing-pro-alvo não respeitava `anim_walk_grace_s`. Fix: condição virou `not is_moving AND anim_walk_grace_s<=0` (só vira pro alvo quando REALMENTE parado, não no gap entre passos). **Follow-up 7 no mesmo dia** (usuário: cada frame é 96×80, mas a colisão/pé real é um tile de 32×32 no canto (32,32) do frame — centrado horizontalmente, não verticalmente; esse tile de 32×32 é que tem que valer pro Y-sort, não o frame inteiro nem o retângulo antigo de 24px). Fix: `CHAR_COLLISION_OFFSET_X/Y=32`, `CHAR_COLLISION_SIZE=32` (novo, `engine/character_animation.py`) + `character_anchor_offset()` (retorna (48,48) — centro do tile de colisão dentro do frame) usado tanto pra ancorar o blit (`RenderSystem`, substitui o antigo "+12") quanto pro Y-sort (`position.y + CHAR_COLLISION_SIZE/2`, substitui `renderable.height/2` e a altura crua do frame). Novo helper único `RenderSystem._resolve_character_class_id()` (evita duplicar a resolução CharacterStats/RemoteControlled entre o loop de Y-sort e o de desenho). Y-sort novo vale mesmo quando a direção específica cai no retângulo de fallback (arte ainda não existe), pra não pular dependendo do que já foi desenhado. Achado de quebra: typo num arquivo do usuário (`guerreiro_fatiador_de_corpos_rightt.png`, dois "t") — renomeado pra `_right.png`. **Follow-up 8 no mesmo dia** (usuário: golpe que erra — miss/dodge/parry — deixava o personagem parado sem nenhuma reação visual; perguntado, usuário escolheu reusar a MESMA animação de golpe em vez de criar uma dedicada, só 25% mais lenta, valendo pros 3 outcomes; usuário testou 25% e achou pouca diferença perceptível, ajustado pra 50% — validado.). Fix: gatilho em `_apply_combat_result` não exige mais `damage>0` (só `source in ("auto","skill")`, já garantido por `_is_dot_hot`) — passa `missed=outcome in ("miss","dodge","parry")` pra `trigger_attack_anim`, que agora também grava `TileMovement.anim_attack_frame_ms` (novo campo — `DEFAULT_FRAME_MS*MISS_ATTACK_SLOWDOWN` quando `missed`, senão `DEFAULT_FRAME_MS` normal; `int()` — frame_ms float quebra o indexing de `frames[]`, achado testando). `resolve_display_state()` retorna 3-tupla agora (`state, anim_time_ms, frame_ms`) em vez de 2 — `frame_ms` por-golpe, não mais o `DEFAULT_FRAME_MS` fixo. "block"/"immune" continuam velocidade normal (só miss/dodge/parry ficam lentos) — golpe dedicado de erro fica como upgrade futuro (`resolve_attack_state_name`/arquivo `{class_id}_miss_{direction}.png`), decisão explícita de não fazer agora. **Follow-up 9 no mesmo dia** (3 pedidos do usuário juntos): (1) cascata de fallback de golpe de SKILL ganhou um 3º nível — `resolve_attack_state_name` agora tenta sid específico → `{class_id}_skill_{direction}.png` genérico (novo) → `attack` genérico; auto-attack (sid vazio) pula direto pro nível 3, nunca passa pelo "skill" genérico. Usuário removeu os sprites de `golpe_poderoso` e já criou `guerreiro_skill_*.png` — hoje toda skill sem sprite própria (a esmagadora maioria) usa esse genérico. (2) Fatiador de Corpos (dura 5s, `SKILL_CATALOG` — fonte única, nunca duplicada) agora fica em LOOP durante a duração REAL da skill em vez de expirar após 1 ciclo do sheet — novo `TileMovement.anim_attack_loop_duration_ms` (0 = padrão de sempre; >0 = toca em loop até esse tempo passar; `get_character_frame` já cicla os frames via módulo sozinho, nenhuma lógica de loop extra precisou ser escrita). Achado corrigindo: o tick de dano do Fatiador no servidor (`WorldServer`, bloco de `fatiador_timer`) NUNCA mandava `sid` no payload de `_combat_this_tick` — sem isso o cliente nunca saberia que era essa skill especificamente, e o loop nunca ligaria; corrigido no mesmo commit. (3) usuário dobrou o tamanho dos sprites pra teste (96×80 → 192×160, tile de colisão real continua 32×32 mas o offset dele dentro do frame muda de (32,32) pra (80,96) — corrigido de um typo inicial do usuário, (80,95)) — combinado esperar ele redimensionar TODOS os arquivos existentes antes de eu tocar em `CHAR_TILE_SIZE`/offset (mesma lição do follow-up 3/7: trocar o código achando que só metade dos arquivos existe quebra visualmente os que ainda não foram redimensionados) — ainda NÃO aplicado, pendente do usuário terminar de redimensionar. **Follow-up 10 no mesmo dia** — usuário terminou de redimensionar TODOS os arquivos pra 192×160 e avisou ("grave, sprites cortadas ao meio"); aplicado `CHAR_TILE_SIZE=192`/offset `(80,96)` (medido direto dos arquivos reais antes de tocar em código, nunca assumir), 6 testes com número mágico 96/48 ajustados pra ler as constantes dinamicamente. Usuário testou e NÃO gostou do resultado visual — pediu reversão completa, código E arquivos. Revertido via `git revert` (commit isolado, seguro) + `git restore` nos sprites já commitados (idle/run/attack/fatiador_de_corpos/golpe_poderoso voltam pra 96×80) + exclusão manual dos 2 grupos de arquivo NUNCA commitados (`guerreiro_skill_*.png`, `guerreiro_idle_down_old.png` — sem histórico pra recuperar, apagados só com autorização explícita do usuário pro grupo específico). 1 teste ajustado pra não depender do disco (assumia erroneamente que "skill" genérico não existia). **Follow-up 11 no mesmo dia** (2 pedidos): (1) usuário re-adicionou `guerreiro_skill_*.png` (96×80, bate com o tamanho atual) — cascata de fallback de skill (código do follow-up 9, nunca revertido — só o ARQUIVO tinha sido apagado) volta a funcionar de verdade sem nenhuma mudança de código. (2) `facing_from_delta` mudou de corte por MAGNITUDE relativa (`abs(dy)>=abs(dx)`, simétrico 45/45) pra corte por ÂNGULO a partir do eixo vertical (`math.degrees(math.atan2(abs(dx),abs(dy)))`) contra `_VERTICAL_FACING_HALF_ANGLE_DEG=20°` — down/up só dentro desse cone estreito de 20° de cada lado do vertical; fora dele (os 70° restantes de cada quadrante, INCLUSIVE a diagonal exata de 45°), left/right. Mudança de comportamento CONFIRMADA de propósito: antes uma diagonal 45/45 exata caía em down/up (por tie-break arbitrário), agora cai em left/right. Testes antigos que dependiam do corte simétrico (diagonal 1,1 esperando "down") reescritos pra testar o cone de 20° explicitamente, com deltas construídos via `math.tan(radians(ângulo))` — bordas exatas (19.9°/20.1°) e alguns ângulos genéricos, não só a diagonal. **Follow-up 12 no mesmo dia** (2 achados do usuário testando a Fatiador de Corpos): (1) usuário reportou "a animação do tornado é cancelada quando o personagem se move" — causa raiz de verdade confirmada por reprodução direta em código: não era o movimento em si, era um auto-attack concorrente (que o movimento provoca ao entrar em alcance de um mob) chegando via `_apply_combat_result` DURANTE os 5s do loop — `trigger_attack_anim` sobrescrevia `anim_attack_state`/`anim_attack_loop_duration_ms` sem checar nada, cancelando o loop em ~800ms num teste isolado. Fix: novo `_attack_loop_still_active(tile_movement, now_ms)` — um trigger SEM duração de loop própria (`_looping_attack_duration_ms(sid) or 0 == 0`) é ignorado enquanto já existir um loop ativo e ainda não expirado; só um loop NOVO (mesmo sid de novo, ou skill de loop diferente) substitui um loop ainda ativo. (2) usuário notou que, apesar do overlay de cooldown cobrir toda a hotbar durante o canal, nenhuma trava real impedia executar OUTRA skill nesse meio tempo — `_process_skill_requests` (servidor, autoritativo) nunca checava `CharacterStats.fatiador_timer`, só dead/ghost/can_act/is_action_locked/taunted; golpe_poderoso aplicava dano e custava raiva normalmente mesmo com o spin ativo. Fix: novo gate silent-continue (mesmo padrão de taunted) logo antes de construir o objeto Skill, bloqueando QUALQUER sid (inclusive recast da própria Fatiador) enquanto `fatiador_timer>0`; replicado também em `ui/systems.py::_use_skill_visual_only` (feedback local, não autoritativo) pra não depender só da rejeição do servidor. | ✅ completo e testado (65 casos em test_character_animation.py — 61 anteriores + 4 novos de prioridade de loop — + 7 em test_fatiador_de_corpos.py — 4 anteriores + 3 novos do gate de skill bloqueada; suíte completa: 1625 passed, 1 failed pré-existente/timing flake sem relação) | `engine/utils.py::facing_from_delta` (`_VERTICAL_FACING_HALF_ANGLE_DEG=20.0`, corte por ângulo), `engine/character_animation.py` (`resolve_attack_state_name`, `_LOOPING_ATTACK_SKILLS`, `_looping_attack_duration_ms`, `_attack_loop_still_active` (novo, guarda de prioridade), `CHAR_TILE_SIZE=96`/offset `(32,32)`), `engine/components.py::TileMovement.anim_attack_loop_duration_ms`, `server/world_server.py` (tick do Fatiador com `sid`), `server/skill_processor.py` (gate `fatiador_timer` novo), `engine/world_systems.py::TileMovementSystem`, `ui/systems.py::RenderSystem`/`_use_skill_visual_only` (gate espelhado), `client/remote_entity_handlers.py::_apply_combat_result`, `tests/test_character_animation.py`, `tests/test_fatiador_de_corpos.py` |
| Mobs aleatórios paravam de perseguir/voltar pro spawn (10/09/2026, relatado pelo usuário no servidor Hetzner de produção — "alguns mobs não estão se movendo"). Padrão confirmado com o usuário antes de investigar: atacavam normalmente quando o player chegava perto, mas nunca perseguiam nem voltavam pro spawn; aleatório, qualquer mob podia travar; reiniciar o servidor curava todos até então (apontou pra estado em memória, não terreno). Causa raiz: `AIControlled.is_blocked` (efeito colateral de `is_tile_walkable()` quando um mob tenta andar pro tile exato do player) só era resetado no branch de PERSEGUIÇÃO — RETURNING nunca resetava, em nenhum dos seus 2 pontos de entrada. Um mob que ficasse `is_blocked=True` no tick em que perdeu o alvo (virando RETURNING) ficava preso pra sempre: path de volta recalculado normalmente, `start_tile_movement` nunca chamado. Primeira vez nesta sessão acessando o servidor real via SSH (chave já existia, `release_tools/deploy_server.ps1`); `RPG_DEBUG_MOB_COMBAT` ligado lá (exige restart, autorizado pelo usuário) pra capturar uma próxima ocorrência ao vivo — causa raiz já confirmada por leitura de código + reprodução sintética local antes disso | ✅ corrigido e testado (reprodução sintética confirma antes/depois; suíte completa 1625 passed, 2 failed pré-existentes/timing flake sem relação) — confirmação contra ocorrência real em produção ainda pendente (debug ligado, aguardando próximo caso) | `engine/world_systems.py::EnemyAISystem.update()` (2 pontos de entrada em RETURNING), `tests/test_server.py::TestRegressionBugs::test_mob_is_blocked_nao_fica_preso_em_returning_para_sempre` (novo), ver `PROBLEMAS_ARQUITETURA.md` §130 |
| Mapa da Battleground "esticado" no minimapa/mundo (10/09/2026, relatado pelo usuário — "era pra ser quadrado, está retangular"). Confirmado com o usuário: dentro da própria BG, largura DECLARADA do mapa dobrou, mas a área explorável não mudou. `map_1` (mundo aberto) checado e descartado do mesmo bug — variação de largura por linha lá é forma orgânica intencional do continente, já documentada. Causa raiz: 298 células `"txg_0_0"/"txg_0_4"` em `maps/moba_battleground_terrain.csv` — sheet nunca cadastrada em `SHEET_TILE_MAP` ("txg" só existe como prefixo de EXEMPLO num comentário, nenhum asset real corresponde). `engine/map_loader.py::_parse_terrain_cell` devolvia o token cru (8 chars) como `terrain_char` quando não reconhecia — `terrain_matrix` é sempre 1 char/tile (`"".join(terrain_row)`), então cada ocorrência inflava a linha inteira, desalinhando todo tile seguinte (colisão real, não só visual) e inflando `map_width_tiles` de 100 pra até 208 — exatamente a proporção "dobrou" relatada | ✅ corrigido e testado (298 células trocadas por "G" no mapa real + fallback do parser endurecido pra QUALQUER token desconhecido de N chars, não só "txg"; 5 casos novos incluindo integração contra o mapa real da BG; suíte completa 1632 passed, 0 failed) | `maps/moba_battleground_terrain.csv` (dado corrigido), `engine/map_loader.py::_parse_terrain_cell`, `tests/test_map_loader_terrain_parsing.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §131 |
| Vitória Iminente nunca ganhava carga matando JOGADOR, só matando mob (13/09/2026, pedido do usuário pra investigar). Primeira reprodução headless (mob morto via `apply_damage_core`, skill só aprendida) funcionou normalmente — mesma cadeia validada em 03/07/2026 continuava intacta. Perguntado onde exatamente falhava: "só na BG que não funciona" (skill confirmada na hotbar). Causa raiz: a concessão de carga (`on_kill=="charge"`) vivia só dentro do loop de morte de mob/minion/torre (`ServerDeathHandler.update()`) — morte de JOGADOR passa por `RespawnMixin._handle_player_death` (crédito de kill estilo LoL, unificado em 16/08/2026), um caminho de código TOTALMENTE separado que nunca ganhou essa lógica (grep case-insensitive no arquivo inteiro, 512 linhas, zero ocorrências de "on_kill"/"charge"/"vitoria"). Como a BG é o modo cuja atividade principal é matar jogadores, a carga nunca acendia lá, mesmo funcionando normalmente contra mob/minion/torre dentro da própria BG | ✅ corrigido e testado (lógica extraída pra `grant_on_kill_charge_skills()`, função de módulo chamada dos 2 pontos de morte; 3 casos novos — regressão do caminho de mob após a extração, kill de jogador concede carga [o bug], assistente não-creditado não ganha carga; suíte completa 1634 passed, 1 failed pré-existente/timing flake sem relação) | `server/server_death_handler.py` (`grant_on_kill_charge_skills`, nova função de módulo), `server/respawn_system.py::_handle_player_death` (chamada nova), `tests/test_on_kill_charge_skills.py` (novo), ver `PROBLEMAS_ARQUITETURA.md` §132 |
| Bola de Fogo "ainda com alcance curto" mesmo após ajustada pra 7 (14/09/2026, continuação do nivelamento de ranged). Causa raiz real: `ui/systems.py::_mage_attack_range()` retorna o MENOR cast_range entre TODAS as skills ofensivas da hotbar do mago (garante que a perseguição só pare quando toda skill já está em alcance) — reduzir a Calamidade Flamejante pra 3 nesta MESMA sessão fez o personagem parar de perseguir a 3 tiles pra QUALQUER skill, inclusive Bola de Fogo (7). Achado secundário real, mas não era a causa do sintoma: `_skill_bola_de_fogo` usava 30 fixo em vez de `skill.cast_range` (diferente das outras 5 skills do nivelamento) — corrigido por consistência mesmo assim | ✅ corrigido, sem teste automatizado dedicado (lógica client-only cosmética, dentro de método grande e acoplado — decisão explícita de não construir harness isolado); suíte completa 1634 passed, 1 failed pré-existente/timing flake sem relação; validação manual em jogo pendente | `engine/components.py::CombatState.pursuit_skill_range` (novo campo), `ui/systems.py` (`_use_skill_visual_only` grava o campo, perseguição do mago passa a preferi-lo sobre `_mage_attack_range`), `engine/skill_handlers.py::_skill_bola_de_fogo` (30 fixo -> skill.cast_range), ver `PROBLEMAS_ARQUITETURA.md` §133 |
| Alcance de skill "sentido" diferente na diagonal vs. reto (14/09/2026, pergunta exploratória do usuário durante o mesmo nivelamento de ranged: "medir em tiles é ruim pq a diagonal é maior, medir em pixel dá o mesmo resultado"). Causa raiz: o check AUTORITATIVO (`SkillHandlers._resolve_target`, servidor) usa Chebyshev em tile (`max(\|dx\|,\|dy\|)`, diagonal conta igual a reto — consistente com o movimento em tile do jogo, sem penalidade de √2), mas o check PREDITIVO do cliente (`_use_skill_visual_only`) media Euclidiana em pixel contínuo (`Position.x/y`) — pra um cast_range de N tiles, N passos na diagonal cobrem ~41% mais distância real em pixel do que N passos reto, então o círculo Euclidiano do cliente podia rejeitar ("Fora de alcance") um alvo que o quadrado Chebyshev do servidor aceitaria, ou o inverso perto da borda reta | ✅ corrigido, sem teste automatizado dedicado (mesmo motivo do §133 — lógica client-only, método grande e acoplado); suíte completa validada sem regressão | `ui/systems.py::_use_skill_visual_only` (Euclidiana em `Position.x/y` -> `chebyshev()` em `TileMovement.current_tile_x/y`, mesma função/campos que o servidor usa em `SkillHandlers._resolve_target`), ver `PROBLEMAS_ARQUITETURA.md` §134 |
| Alcance de ATAQUE (skill/auto-attack/torre/minion/mob) virou distância REAL (Euclidiana) em vez de Chebyshev (14/09/2026, continuação direta do item acima, mesmo dia). Usuário testou em jogo: 5 tiles Chebyshev na diagonal (dist real ~7.07) ainda conseguia lançar Bola de Fogo com cast_range=6 — Chebyshev conta diagonal igual a reto, então o alcance sempre "esticava" na diagonal. Escopo fechado com o usuário via pergunta explícita: só alcance de ALVO ÚNICO, nunca raio de área (Calamidade Flamejante, Canção de Ninar, Impacto, Fatiador, Brado Provocativo continuam Chebyshev/quadrado, decisão consciente). Exceção obrigatória achada corrigindo: alcance melee (<=1 tile) precisa continuar Chebyshev — adjacência diagonal (dist real ~1.41) ficaria fora de um alcance real de 1, quebrando golpe corpo-a-corpo em 4 das 8 direções. 2º achado: MinionSystem._in_range_los usa aggro_range_tiles (raio de PERCEPÇÃO, não ataque) — trocar quebrou 2 testes de throttle de recálculo (tests/test_minions.py), revertido pra Chebyshev, fora do escopo combinado | ✅ corrigido e testado (novo tests/test_in_range_euclidean_attack.py: unitários de in_range() + integração via _resolve_target reproduzindo o cenário exato relatado + regressão da exceção de melee; suíte completa 1633 passed, 2 failed pré-existentes/timing flake sem relação) | `engine/utils.py::in_range` (novo, ponto único de verdade), `engine/skill_handlers.py::_resolve_target`, `server/combat_processor.py::_resolve_auto_attack`, `engine/world_systems.py` (TowerSystem/MinionSystem/EnemyAISystem/_do_kiting), `server/world_server.py::_enemy_towers_covering`, `ui/systems.py` (_use_skill_visual_only/_mage_skill_in_range/bow_range/_ranged_stop_tile), ver `PROBLEMAS_ARQUITETURA.md` §135 |
| Nova Congelante com cast dentro da BG mesmo com Precisão Elemental maxada pela normalização de instância (14/09/2026). Causa raiz: `client/network_handlers.py::_handle_msg_stats_update` recebia `talent_allocated` e só trocava o dict `TalentTree.allocated` — nunca chamava `TalentSystem.apply_talent_effects()`, a única função que recalcula cs_flags comportamentais (`ice_cast_time_reduction`) a partir da alocação. Servidor sempre recalculou certo (`WorldServer._apply_talent_modifiers`), então o efeito real da skill disparava na hora — só a barra de cast VISUAL do cliente ficava presa no valor de fora da instância | ✅ corrigido e testado (novo tests/test_instance_talent_effects_sync.py, confirmado por revert-to-confirm; suíte completa sem regressão) | `client/network_handlers.py::_handle_msg_stats_update` (chama `self._talent_system.apply_talent_effects()` após trocar `allocated`), ver `PROBLEMAS_ARQUITETURA.md` §136 |
| Som de impacto da Nova Congelante tocava 2x (14/09/2026, confirmado que acontece em qualquer lugar, não só BG). Causa raiz: previsão local (`ui/spell_system.py::_apply_nova_congelante`, toca quando o cast LOCAL do cliente termina) + eco do próprio `SKILL_EFFECT` de conclusão que o servidor manda via broadcast AOI pra QUALQUER player perto ouvir a skill — o próprio conjurador está dentro do seu raio de AOI e recebia (e tocava) essa mesma notificação de novo. Calcinar/Polimorfia nunca tiveram esse bug só porque não definem `effects` no catálogo | ✅ corrigido e testado (novo tests/test_skill_effect_no_local_dup_sound.py, confirmado por revert-to-confirm; suíte completa sem regressão) | `content/skill_config.py::nova_congelante` (nova flag `effects.impact.skip_local_caster`), `client/network_handlers.py::_handle_msg_skill_effect` (checa a flag antes de tocar som pro próprio conjurador — outros players continuam ouvindo normal), ver `PROBLEMAS_ARQUITETURA.md` §137 |
| Dispatch de mensagem de rede virando dict quebrou 17 handlers na 1a tentativa (15/09/2026, implementando item 7 de CLIENT_PERFORMANCE_REVIEW.md). Causa raiz: `NetworkHandlers` (`client/network_handlers.py`) e uma MIXIN, nunca instanciada sozinha - 17 dos 54 handlers (duelo/grupo/arena/BG) sao definidos em mixins IRMAOS combinados em `GameEngine`, so existem no MRO de `self` em runtime. Um dict referenciando a funcao direto pela classe (`NetworkHandlers.metodo`) quebrou com AttributeError no import - pego antes de commitar | ✅ corrigido e testado (dict guarda NOME do metodo, resolvido via `getattr(self, name)` - preserva a mesma resolucao dinamica via MRO do codigo original; script de verificacao confirma os 54 nomes contra a classe REAL `GameEngine`; suite completa 1649 passed, 1 failed pre-existente/timing flake sem relacao) | `client/network_handlers.py::_handle_net_message` (`_NET_DISPATCH_NAMES`), ver `PROBLEMAS_ARQUITETURA.md` §138 |
| BlacksmithSystem/TrainerSystem chamavam metodos PRIVADOS de ShopSystem/QuestDialogSystem direto (15/09/2026, item 4 critico da revisao de performance/arquitetura). Achado extra: BlacksmithSystem._draw_action_menu chamava self._quest_system.can_start(qid) - metodo que NUNCA existiu em QuestSystem (grep no projeto inteiro: zero resultados) - quebraria com AttributeError assim que um ferreiro tivesse quest disponivel pra iniciar, nunca pego por falta de teste nesse caminho | ✅ corrigido e testado (componente NPCInteractionRequest pra comandos 'abra isto', engine.quest_logic.get_available_quests/get_completable_quests/get_inprogress_quests como funcoes de modulo pra consultas puras - can_start quebrado virou chamada real a essas funcoes; novo tests/test_npc_interaction_request.py; suite completa sem regressao) | `engine/components.py::NPCInteractionRequest`, `engine/quest_logic.py` (funcoes novas), `ui/crafting_system.py`, `ui/trainer_system.py`, `ui/quest_system.py`, `ui/systems.py::ShopSystem.update`, `game.py` (wiring simplificado), ver `PROBLEMAS_ARQUITETURA.md` §139 |
| Profiler client-side (F11/logs/client_prof.log) nunca mediu RenderSystem.render() (15/09/2026, usuario pediu pra comparar antes/depois da revisao de performance e mandou o proprio log real de uma sessao). Causa raiz: RenderSystem e outros 4 systems (loot/projectile/pirofagia/spell_cast) ficam fora do loop generico que envolve render() em _prof_record, por precisarem de assinatura/ordem propria - nunca apareciam no relatorio nem no overlay F11, justo o mais caro dos cinco (unico com Y-sort de toda entidade visivel, tocado pelos itens 1/2/5/6/8) | ✅ corrigido e testado (mesmo padrao if PROFILE_FRAMES replicado nos 5 pontos de chamada individual; achado 2 corrigindo isso - teste de culling do item 8 dependia do tamanho de tela AMBIENTE via pygame.display.get_surface(), corrigido pra Surface propria de tamanho fixo; suite completa 1656 passed, 1 failed pre-existente/timing flake sem relacao) | `game.py` (5 pontos de chamada individual instrumentados), `tests/test_client_ui.py` (2 testes de culling do item 8 corrigidos), ver `PROBLEMAS_ARQUITETURA.md` §140 |
| Travamento periodico de ~105ms em network_recv, achado analisando o log real de profiling do usuario apos o fix do §140 (15/09/2026). Valores muito consistentes entre si (106.6/106.6/108.1/104.7ms) - nao parece jitter aleatorio de rede. poll() confirmado nao-bloqueante (so drena fila ja populada por thread separada, decode() roda na thread de rede, nao aqui) - hipotese da rajada foi DESCARTADA (ver §142: todo spike tinha so 1-5 mensagens, nunca dezenas) | ✅ RESOLVIDO - causa raiz e fix completo em §142 (nao era rajada, era encode_fog() rodando a cada LOOT_RESULT, cliente E servidor) | `client/online_mode_handlers.py::_process_network` (_last_net_msg_count/_last_net_msg_types, instrumentacao que permitiu descartar a rajada), ver `PROBLEMAS_ARQUITETURA.md` §141/§142 |
| encode_fog() (fog-of-war) recomprimindo o mapa INTEIRO a cada save de evento (loot/compra/venda/forja/troca/stats_update) - causa raiz do §141 (15/09/2026). Cliente: ~100ms+ so no laco Python do bitmap (77ms de 95ms num benchmark de 150k tiles, zlib e so 17ms). Servidor (`_build_save_merge`): pior ainda - roda dentro da coroutine sem executor, e o asyncio do servidor e single-thread, entao travava TODOS os players conectados, nao so quem deu loot. 2 perguntas do usuario moldaram o fix (nunca decidido sozinho): "so recomprimir no logout" e "fragmentar o mapa ajuda?" (testado e PIORA - custo e por-tile no laco, chunking so soma overhead de N compress() calls). Follow-up mesmo dia (usuario jogou de novo e mandou log novo): LOOT_RESULT sumiu (causa raiz confirmada), mas achado spike novo de 106.8ms em `online_sync` - varredura original nao cobriu 4 call sites diretos em `game.py` (`_autosave()` chamado por compra/talento/quest/level-up/morte/transicao-de-mapa, + autosave local periodico de 2min), todos ainda com `include_fog` default. 3 dos 4 corrigidos direto (sem ambiguidade); o autosave periodico de 2min era decisao real (rede de seguranca contra crash vs. zero soluco periodico) - perguntado ao usuario, que escolheu remover fog dai tambem | ✅ corrigido e testado (cliente so manda fog no save de logout/quit, novo `include_fog=False` nos 7 saves de evento de rede + 3 pontos de `_autosave()` + autosave periodico de 2min; servidor: fog=None no merge faz save_character pular a coluna sem nenhum trabalho quando cliente nao mandou nada; quando manda de verdade, roda em ProcessPoolExecutor - 1o uso desse padrao no projeto - nunca bloqueia o loop principal mesmo com login/logout simultaneos; suite completa 1658 passed, 1 failed pre-existente/timing flake sem relacao) | `shared/fog_codec.py::merge_and_encode_fog` (novo, pura/pickleable), `server/session.py` (`_fog_executor`, `_persist_character`, `_build_save_merge` simplificado), `client/save_sync_handlers.py` (`include_fog`), `client/network_handlers.py` (7 sites), `game.py` (save explicito nas acoes quit/logout do menu + `_autosave()` + autosave periodico, todos `include_fog=False` exceto quit/logout), `tests/test_session.py::TestMergeAndEncodeFog`/`TestBuildSaveMergeFogPassthrough`, ver `PROBLEMAS_ARQUITETURA.md` §142 |
| Spike periodico de ~104-110ms sem correlacao com rede/loot, achado num 3o log de playtest pos-fix completo de §141/§142 (15/09/2026). Descartados por medicao direta: gc.collect() (peak real so ~25-30ms), tecla DELETE (usuario confirmou nao ter apertado), input_dispatch (peak maximo ~1.5ms). CONFIRMADO num 4o log: `ui_modals_overlay` (bloco "por cima de tudo" - render de loot/shop/quest/crafting/trainer MODAL, mapa overlay, menu de pausa, God Mode, perf overlay) aparece NOMEADO no breakdown de 2 spikes reais (79.3ms de 90ms, 109.0ms de 121ms) - primeira vez na investigacao inteira que o culpado aparece no proprio breakdown em vez de tempo invisivel. Ambos com jogador PARADO (is_moving=False), consistente com modal aberto parado olhando | 🔍 investigacao REABERTA, 3a rodada - `ui_modals_overlay` CONFIRMADO como causa real, dividido em 10 labels granulares (loot/shop/quest_dialog_journal/crafting/trainer/tooltip_flush_debug_modal/map_overlay/pause_menu/god_mode/perf_overlay) pra achar QUAL sub-bloco e o responsavel; hipotese mais provavel (nao confirmada) e `ui:map_overlay` dado o padrao "parado" dos 2 casos; suite completa 1658 passed, 1 failed pre-existente/timing flake sem relacao | `game.py` (10 labels `ui:*` novos, substituem `ui_modals_overlay`), ver `PROBLEMAS_ARQUITETURA.md` §143 |
| Pausa recorrente de ~22-28ms a cada ~10s (gc_collect, mesma investigacao do §143) - usuario testou de novo, sentiu engasgos mas nao teve certeza se pequenos-repetitivos ou grande-isolado; spike grande (90-121ms) NAO reproduziu neste log, ui:map_overlay descartado (ficou ~0.001ms sempre). gc_collect continua consistente/confirmado em todo [PROF] (~22-28ms). Usuario escolheu tratar o que ja esta confirmado: gen0/gen1 (baratas) automaticas no modo padrao do Python, so gen2 (a cara) continua manual no mesmo timer de 10s | ✅ implementado - `_gc.disable()` trocado por `_gc.set_threshold(700, 10, 2_000_000_000)` (thresholds 0/1 padrao do CPython, threshold2 gigante pra gen2 nunca disparar sozinha no meio de um frame); `_gc.collect()` -> `_gc.collect(2)` explicito no timer; efeito esperado (nao confirmado em jogo ainda): pausa de gen2 menor, ja que gen0/1 limpam sozinhas o tempo todo agora; suite completa 1658 passed, 1 failed pre-existente/timing flake sem relacao | `game.py::run()` (`_gc.set_threshold`, `_gc.collect(2)`), ver `PROBLEMAS_ARQUITETURA.md` §143 |
| Trava ao apertar F11 pela 1a vez na sessao (185ms), achada num teste completo do usuario (menus/inventario/mapa/alt-tab) - [SPIKE] 197ms com ui:perf_overlay=185.1ms. Causa: self._perf_font nascia None (lazy-load), pygame.font.SysFont() so era chamado na 1a vez que o overlay desenhava de verdade - SysFont() varre fontes do sistema no Windows, lento na 1a chamada. Mesmo teste tambem explicou o alt-tab (SO suspende o processo, nao e bug de codigo - cluster de spikes gigantes com net_msgs acumulado, 16/10/8/5 vs normal 0-1, e o catch-up ao retomar foco) e achou de quebra um ZONE_CHANGE de 1.28s (fora de escopo, nao investigado ainda) | ✅ F11 corrigido e testado - fonte criada direto no __init__ (mesmo momento de outros recursos), lazy-load removido; alt-tab so explicado, mitigacao (descartar fila de evento/resetar clock ao retomar foco) fica pendente de decisao do usuario; ZONE_CHANGE de 1.28s reportado, nao investigado; suite completa 1659 passed, 0 failed | `game.py::__init__` (`_perf_font` criado ali), `game.py::_draw_perf_overlay` (lazy-load removido), ver `PROBLEMAS_ARQUITETURA.md` §143 |
| Item 9 do CLIENT_PERFORMANCE_REVIEW.md (object pooling de projetil do jogador) - escopo real 3x maior que estimado (16 imports em 4 arquivos, nao ~6); pesquisa antes de codar achou que existem DOIS componentes de projetil - `Projectile` (mob/torre/minion, server-autoritativo de verdade via ProjectileSystem compartilhado, NUNCA tocado) e `PlayerProjectile` (so do jogador, 100% client-only, confirmado por grep no repo inteiro). 2 achados de codigo morto no caminho: 4 metodos inteiros em spell_system.py nunca chamados (superados ha tempo por network_handlers.py, removidos com aprovacao do usuario) e `_process_bdf_pending` nunca invocado por ninguem (corrigido, nao deletado - decisao maior, fora do pedido) | ✅ implementado e testado - `PlayerProjectile` (dataclass ECS) -> `PlayerProjectileEntry` (`__slots__`, fora do ECS, mesmo padrao de `FloatingTextEntry`); `PlayerProjectileSystem.spawn()` unico ponto de criacao novo, `find_pending_fireball()` substitui a unica query ECS que ainda precisava sobreviver a frames diferentes (Bola de Fogo com resultado atrasado); suite completa 1657 passed, 2 failed pre-existentes/timing flake sem relacao | `engine/components.py` (PlayerProjectile removido), `ui/spell_system.py` (`PlayerProjectileEntry`/`spawn`/`find_pending_fireball`, 4 metodos mortos removidos), `client/network_handlers.py`/`client/remote_entity_handlers.py`/`client/online_mode_handlers.py` (call sites migrados), `game.py` (ordem de construcao dos systems), `tests/test_client_ui.py`, ver `PROBLEMAS_ARQUITETURA.md` §144 |
| Engasgo especifico de BG (15/09/2026, usuario relatou travadas so na Battleground) - log real mostrou upd:FogSystem subindo de avg~0.05ms pra avg~2-5ms (peak 10-24ms) conforme a partida avancava. Causa: FogSystem recalculava o shadowcasting (compute_fov) de TODO aliado (ally_centers inclui player/torre/minion do time, nao so teammates) a cada movimento de QUALQUER um deles, mesmo pra aliados que nao se moveram - O(n_aliados) por recompute, disparando quase todo tick em combate ativo. Mundo aberto nunca mostra isso (zero aliados); BG com varios minions+torres+teammates, sim. PlayerProjectileSystem (item 9) checado e descartado como causa - medias estaveis o log inteiro | ✅ corrigido e testado - cache por aliado em `FogOfWar._ally_fov_cache` (chave (ax,ay,ar) -> FOV ja calculado, capado em 300 entradas, limpo em switch_map); so o aliado que de fato mudou de posicao recalcula. 2 testes novos com revert-to-confirm (falham sem o fix - 4 chamadas de compute_fov em vez de 3, cache vazio); 35 testes existentes de visao compartilhada de time continuam passando sem alteracao (comportamento observavel identico, so o custo mudou); suite completa 1659 passed, 2 failed pre-existentes/timing flake sem relacao | `engine/components.py::FogOfWar` (`_ally_fov_cache` novo), `ui/systems.py::FogSystem.update()`, `tests/test_fog_ally_vision.py::TestFogSystemAllyFovCache`, ver `PROBLEMAS_ARQUITETURA.md` §146 |
| Nova Congelante muda pro proprio conjurador com "Precisao Elemental" maxada (16/09/2026, usuario relatou apos o fix do §137). Causa raiz: talento maxado reduz o cast_time da skill a exatamente 0 - nesse caso o cliente nem cria o SpellCast local (de proposito, evita bug de cast dur=0 + interruptible cancelando sozinho ao mover), entao _complete_cast() (onde morava o som local) nunca roda; skip_local_caster suprimia o eco de rede achando que o som local ja tinha tocado, mas nunca tinha - silencio total. Confirmado por instrumentacao isolada (script com SOUNDS mockado): o mecanismo local nunca teve excecao, so nunca era executado nesse caminho | ✅ corrigido e testado - a pedido do usuario ("igual bola de fogo, independente do cast, no momento que a skill sai"), removida QUALQUER previsao local de som (a chamada em _apply_nova_congelante) e removido skip_local_caster do catalogo; som passa a vir so do SKILL_EFFECT do servidor pro conjurador tambem, igual qualquer outro player - funciona identico com cast normal ou instantaneo, sem precisar saber qual dos dois e. 3 dos 5 testes do arquivo falham sem o fix (revert-to-confirm) | `ui/spell_system.py::_apply_nova_congelante` (som removido), `content/skill_config.py::nova_congelante` (skip_local_caster removido), `tests/test_skill_effect_no_local_dup_sound.py` (reescrito), ver `PROBLEMAS_ARQUITETURA.md` §148 |
| ESC nao fechava o modal de estatisticas - tecla C (16/09/2026, usuario relatou). Mesma classe do bug de 10/08/2026 que afetou os 5 modais de PvP: `_show_char_stats` nunca teve entrada em `_modal_registry()` (`client/modal_stack_handlers.py`), entao `_close_top_modal()` nunca o via como aberto e `game.py` caia pro `else: self._show_pause = True`, abrindo o menu de pausa por cima em vez de fechar as estatisticas | ✅ corrigido e testado - nova entrada `char_stats` no registry + `_close_char_stats()` (mesmo padrao de `_close_debug`/`_close_skill_level`); novo teste diferencial confirma reconhecimento e fechamento via ESC, revert-to-confirm falha sem o fix; suite completa de test_client_ui.py (171 casos) sem regressao | `client/modal_stack_handlers.py` (`_modal_registry`, `_close_char_stats`), `tests/test_client_ui.py`, ver `PROBLEMAS_ARQUITETURA.md` §149 |
| Ouro infinito via SELL_REQUEST (16/09/2026, item 1 da auditoria de projeto inteiro #150) - process_shop_sell creditava ouro antes de confirmar posse real do item no Inventory vivo (remocao best-effort, nunca recusava nem desfazia o credito). Revisao cruzada entre os 3 agentes (pedido explicito do usuario, antes de corrigir) achou um 2o exploit mais grave no mesmo metodo: current_gold do cliente sobrescrevia wallet.gold sem validacao, tambem em process_shop_buy - gold do servidor ja e autoritativo via loot_processor, nao precisava de sync do cliente | ✅ corrigido e testado - process_shop_sell agora confere posse real (soma atraves de todos os slots que casam) e Inventory presente ANTES de creditar; current_gold removido de process_shop_sell/process_shop_buy/session.py. Teste antigo que documentava o comportamento errado como esperado foi invertido, 2 casos novos; revert-to-confirm confirma os 3 falhando sem o fix | `server/world_server.py` (process_shop_sell, process_shop_buy), `server/session.py` (_handle_sell_request, _handle_buy_request), `tests/test_craft_recycle_protocol.py`, ver `PROBLEMAS_ARQUITETURA.md` §151 |
| Cura/mana infinitos via CONSUMABLE_USE (16/09/2026, item 2 da auditoria #150) - apply_consumable lia heal_instant/mana_restore/hot/mana_hot/ooc_only direto do payload do cliente, nunca contra o catalogo; item_id vazio pulava a deducao do item mas os efeitos rodavam igual. Revisao cruzada achou que o fix rascunhado (so trocar payload por catalogo) ainda deixava o exploit vivo - faltava exigir posse real (metodo nunca rejeitava quando o item nao era achado, so pulava o decremento) - e um problema de schema (campos do HoT sao soltos no catalogo, nao aninhados em "hot"/"mana_hot" como o payload de rede monta - ler direto quebraria HoT de comida/agua silenciosamente) | ✅ corrigido e testado - item resolvido 1x via _item_factory_by_id logo apos o guard de morte, heal/mana/ooc_only do catalogo, hot/mana_hot remontados dos campos soltos, posse real exigida (reason=not_owned) antes de aplicar qualquer efeito. Teste que documentava consumir sem possuir como "esperado" foi corrigido (helper _give novo), teste novo prova a posse sendo exigida; revert-to-confirm confirma falhando sem o fix | `server/world_server.py` (apply_consumable), `tests/test_craft_recycle_protocol.py`, ver `PROBLEMAS_ARQUITETURA.md` §152 |
| Duplicacao/forja de item via INV_SYNC (16/09/2026, item 3 da auditoria #150) - sync_player_inventory fazia inv.items.clear() e reconstruia do zero confiando na lista do cliente, sem validar quantidade/proveniencia. Pesquisa previa (antes do fix) achou que Recarregar pareceria depender do full-replace - investigacao mais funda mostrou que essa premissa era errada: Recarregar ja e 100% autoritativo via _server_recarregar, o caminho que "precisaria" do INV_SYNC (_apply_recarregar) e codigo morto (esta em _SELF_TARGET_SERVER_ONLY, pulado no cliente online). Loot tambem ja nao dispara mais INV_SYNC (confirmado por teste existente). Revisao cruzada confirmou a pesquisa de forma independente e achou 2 testes que documentavam o exploit como esperado | ✅ corrigido e testado - conta quanto de cada item_id o servidor ja tem (soma de stack, antes de qualquer mutacao) e rejeita TUDO se a lista excede max_slots ou pede mais do que ja possuido; loga a rejeicao. 2 testes que documentavam o exploit reescritos, novo teste com 5 casos sem dependencia de banco; revert-to-confirm confirma 3 falhando sem o fix | `server/world_server.py` (sync_player_inventory), `ui/spell_system.py` (comentario de aviso perto de _SELF_TARGET_SERVER_ONLY), `tests/test_server.py`, `tests/test_hotbar_bg_leak.py`, ver `PROBLEMAS_ARQUITETURA.md` §153 |
| Aljava nova nasce vazia ao trocar de aljava logo apos esvaziar a antiga (16/09/2026, bug relatado pelo usuario fora da auditoria). Causa: confirmacao de quiver_arrow_count (consumo de flecha ou Recarregar) fica numa fila despachada so no proximo tick do servidor; se o jogador equipar aljava nova antes dela chegar (equipar e predicao local instantanea), o handler aplicava o valor cegamente em cima de qualquer coisa no slot offhand, sem checar se era a mesma aljava que gerou o valor | ✅ corrigido e testado - as 2 mensagens que mandam quiver_arrow_count agora incluem quiver_item_id; cliente so aplica se bater com a aljava equipada agora, confirmacao orfa e ignorada silenciosamente. Revert-to-confirm reproduz o bug exato relatado (aljava de 999 vira 0) | `server/spell_completion_processor.py` (2 sites), `client/network_handlers.py` (_handle_msg_stats_update), `tests/test_client_ui.py`, ver `PROBLEMAS_ARQUITETURA.md` §154 |
| Perda de ouro sem entrega em BUY_REQUEST (16/09/2026, item 4/ultimo da auditoria #150) - process_shop_buy checava espaco usando cache do cliente (last_client_payload, via _pending_inv) em vez do Inventory ao vivo real - se o cache estivesse desatualizado, liberava compra que a bag real nao comportava, ouro debitado antes da entrega parar no meio silenciosamente. Revisao cruzada confirmou o design e pediu extrair funcao compartilhada entre o check e a entrega (mesmo espirito de _stack_or_add_item), evitando duplicar a logica de empilhamento | ✅ corrigido e testado - novo _plan_stack_delivery (dry-run/apply) usado tanto pro check quanto pra entrega real; rejeita a compra INTEIRA sem debitar ouro se nao couber tudo. last_inventory e todo o mecanismo _pending_inv/confirm_inventory_save removidos (obsoletos). 5 testes novos sem dependencia de banco; revert-to-confirm confirma 3 falhando sem o fix. Com este item, os 4 achados Alta da auditoria estao todos corrigidos | `server/world_server.py` (_plan_stack_delivery, process_shop_buy), `server/session.py` (_handle_buy_request, _handle_save_state), `tests/test_craft_recycle_protocol.py`, ver `PROBLEMAS_ARQUITETURA.md` §155 |
| Bug da aljava (#154) reaberto pelo usuario em producao poucos minutos apos o deploy - fix anterior corrigiu so o STATS_UPDATE (_handle_msg_stats_update), mas existia uma 2a via paralela: client/remote_entity_handlers.py::_apply_combat_result decrementava arrow_count direto a cada COMBAT_RESULT de auto-attack do proprio arqueiro, sem nenhuma checagem de identidade - nem antes nem depois do fix anterior, que nunca tocou esse codigo. Mesma race: tiro "em voo" com aljava trocada decrementava a aljava NOVA | ✅ corrigido e testado - decremento removido de _apply_combat_result; STATS_UPDATE (ja corrigido, idempotente, com checagem de identidade) e a unica fonte de verdade agora, mesmo evento/timing do servidor. Teste novo confirma que _apply_combat_result nao mexe mais em arrow_count; revert-to-confirm reproduz o decremento cego (999->998) contra o codigo antigo | `client/remote_entity_handlers.py` (_apply_combat_result), `tests/test_client_ui.py`, ver `PROBLEMAS_ARQUITETURA.md` §156 |
| Trava de quest por serviço de NPC — loja e treinador de skill (04/10/2026, pedido do usuário: "a loja só destravar depois que o player fizer uma quest... a mesma coisa serve pra qualquer outro NPC, como treinamento"). Decisões fechadas com o usuário: só 1 `unlock_quest_id` por serviço (não lista — "já consigo fazer chain quest, só coloco a última no desbloqueio"); sem mensagem de "bloqueado" nenhuma — clicar no NPC travado abre o diálogo de quest direto (`ui/trainer_system.py::_open_menu` já priorizava quest completável sobre o menu de assunto; replicado pra loja via `NPCInteractionRequest(kind="open_quest_dialog")`, que não tinha menu intermediário). Aproveitou a tarefa pra fechar um débito pré-existente, aprovado pelo usuário: treinar skill NUNCA teve validação no servidor (só loja/forja já tinham, desde o débito A4) — `_do_learn` mutava `learned_skill_ids`/`Wallet.gold` locais direto, sem nenhuma mensagem de rede. Agora `TRAIN_SKILL_REQUEST`/`TRAIN_SKILL_RESULT` espelham exatamente o padrão já usado por `BUY_REQUEST`/`CRAFT_REQUEST` (classe/nível/ouro/trava de quest revalidados no servidor, nunca confiados do cliente) | ✅ completo e testado (18 testes novos, revert-to-confirm em cada peça); nenhuma loja/treinador usa a trava ainda — mecanismo pronto, qual quest trava qual NPC é decisão de conteúdo do usuário, não código | `engine/quest_logic.py::has_unlocked`, `engine/components.py::Trainer.unlock_quest_id`, `content/merchant_data.py::SHOPS[...]["unlock_quest_id"]`, `server/world_server.py::process_shop_buy`/`process_train_skill`, `server/session.py::_handle_train_skill_request`, `ui/trainer_system.py::_compute_options`/`_do_learn`, `ui/shop_system.py::_try_open_merchant`, `client/network_handlers.py::_handle_msg_train_skill_result`, `tests/test_npc_service_unlock.py` |
| Ícone de NPC de serviço no mapa (M)/minimapa independente de AOI — ver decisão §24 (06/10/2026, pedido já feito antes, não corrigido até agora) | ✅ completo e testado (3 testes novos, revert-to-confirm) | `ui/map_markers.py::collect_markers`, `engine/quest_logic.py` (`giver=` opcional + `get_locked_quests` novo), `ui/quest_system.py::marker_for_static`, `game.py` |
| Posição de companheiros de grupo no mapa (M)/minimapa, independente de FoW — ver decisão §25 (06/10/2026) | ✅ implementado; testes CLIENTE passando (3), testes SERVIDOR escritos mas **não executados** nesta sessão (sem Postgres local disponível) — rodar antes de considerar validado | `shared/messages.py` (AOI_UPDATE.party_positions), `server/session.py::_build_update_for_session`, `client/network_handlers.py::_handle_msg_aoi_update`, `ui/map_markers.py::collect_party_markers`, `game.py`, `tests/test_session.py::TestPartyPositionsNoAoiUpdate`, `tests/test_client_ui.py` |
| Ranking Geral (level + skill level, TODOS os personagens online ou não) — ver decisão §26 (06/10/2026) | ✅ implementado; testes CLIENTE passando (7), testes SERVIDOR escritos mas **não executados** nesta sessão (sem Postgres local disponível) — rodar antes de considerar validado | `server/auth.py::get_ranking`, `server/ranking_processor.py` (novo), `shared/messages.py` (RANKING_REQUEST/RESULT), `server/session.py::_handle_ranking_request`, `ui/ranking_system.py` (novo), `game.py`, `client/modal_stack_handlers.py`, `config.py` (tecla K), `tests/test_ranking.py`, `tests/test_client_ui.py` |

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
